import os
import time
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from decimal import Decimal, ROUND_DOWN

from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from src.database.db_manager import DatabaseManager

load_dotenv()


# ========== Agent → 交易标的 映射 ==========
# 每个分析 Agent 只对应一个 Hyperliquid 永续合约标的；后续新增 Agent 时在这里加一行即可。
AGENT_TO_COIN: Dict[str, str] = {
    "gold": "PAXG",
    "btc": "BTC",
}


def resolve_coin(agent_name: Optional[str]) -> str:
    """根据 agent_name 解析对应的交易标的；未识别返回空字符串。"""
    return AGENT_TO_COIN.get(agent_name, "")


# ========== 配置项==========
class TradeConfig:
    """交易配置，集中管理所有可调参数"""

    LEVERAGE: int = 1

    POSITION_MAP: Dict[str, float] = {
        "观望": 0.0,
        "轻仓试探": 0.05,
        "标准仓位": 0.15,
        "重仓出击": 0.30,
    }
    PRICE_DEVIATION_PCT: float = 0.02
    MIN_CONFIDENCE: int = 5
    SIGNAL_VALIDITY_MINUTES: int = 10
    # 加仓上限：累计仓位名义值不超过账户该比例，防止良性信号无限加仓
    MAX_POSITION_PCT: float = 0.30
    NETWORK: str = os.getenv("HYPERLIQUID_NETWORK", "testnet")

    @classmethod
    def get_api_url(cls) -> str:
        return constants.TESTNET_API_URL if cls.NETWORK == "testnet" else constants.MAINNET_API_URL


# ========== Hyperliquid 客户端封装 ==========
class HyperliquidClient:
    """Hyperliquid API 客户端封装"""

    def __init__(self):
        private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
        if not private_key:
            raise ValueError("请设置 HYPERLIQUID_PRIVATE_KEY 环境变量")
        
        self.agent_private_key = Account.from_key(private_key)
        self.agent_address = os.getenv("HYPERLIQUID_AGENT_WALLET_ADDRESS")
        self.main_address = os.getenv("HYPERLIQUID_MAIN_ADDRESS")

        api_url = TradeConfig.get_api_url()
        self.info = Info(api_url, skip_ws=True)
        self.exchange = Exchange(self.agent_private_key, api_url)

        # 禁用 requests 读取系统/环境代理，避免代理软件异常时误走坏代理导致 ProxyError
        self.info.session.trust_env = False
        self.exchange.session.trust_env = False

        print(f"[Hyperliquid_trade] 初始化完成 | Agent: {self.agent_address} | "
              f"主钱包: {self.main_address} | 网络: {TradeConfig.NETWORK}")

    def get_account_value(self) -> float:
        user_state = self.info.user_state(self.main_address)
        return float(user_state["marginSummary"]["accountValue"])
    
    def get_open_positions(self) -> List[Dict]:
        user_state = self.info.user_state(self.main_address)
        positions = []
        for pos_data in user_state.get("assetPositions", []):
            pos = pos_data["position"]
            positions.append({
                "coin": pos["coin"],
                "size": float(pos["szi"]),
                "entry_price": float(pos["entryPx"]),
                "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
            })
        return positions
    
    def has_position(self, coin: str) -> bool:
        return any(p["coin"] == coin for p in self.get_open_positions())
    
    def get_sz_decimals(self, coin: str) -> int:
        meta = self.info.meta()
        for asset in meta["universe"]:
            if asset["name"] == coin:
                return asset["szDecimals"]
        return 4

    def round_price(self, price: float, coin: str) -> float:
        """
        价格取整，与 Hyperliquid SDK _slippage_price 口径一致：
        先取 5 位有效数字，再按 perp 精度 (6 - szDecimals) 四舍五入。
        旧启发式 5 - szDecimals 会把 BTC 抹成整数价、把低价币抹成 0，已废弃。
        """
        sz_decimals = self.get_sz_decimals(coin)
        decimals = max(6 - sz_decimals, 0)
        return round(float(f"{price:.5g}"), decimals)
    
    def place_market_order(self, coin: str, is_buy: bool, sz: float) -> Dict[str, Any]:
        print(f"[Order] 市价{'买入' if is_buy else '卖出'} {coin} | 数量: {sz}")
        result = self.exchange.market_open(
            name=coin,
            is_buy=is_buy,
            sz=sz,
            slippage=0.01
        )
        return result
    
    def place_trigger_order(self, coin: str, is_buy: bool, sz: float, 
                           trigger_px: float, tpsl: str, is_market: bool = True) -> Dict[str, Any]:
        """
        下触发单（止损/止盈）
        FIX #3: 用 round_price 替代 int(round())，保留价格精度
        """
        trigger_px = self.round_price(trigger_px, coin)
        
        order_type = {
            "trigger": {
                "triggerPx": trigger_px,
                "isMarket": is_market,
                "tpsl": tpsl
            }
        }
        
        print(f"[Order] {'止损' if tpsl == 'sl' else '止盈'}单 {coin} | "
              f"触发价: {trigger_px} | 数量: {sz} | "
              f"触发后{'市价' if is_market else '限价'} | "
              f"方向: {'买入' if is_buy else '卖出'}")
        
        result = self.exchange.order(
            name=coin,
            is_buy=is_buy,
            sz=sz,
            limit_px=trigger_px,
            order_type=order_type,
            reduce_only=True
        )
        return result

    def place_market_close(self, coin: str, sz: float, is_buy: bool) -> Dict[str, Any]:
        """市价减仓/平仓（reduce-only，is_buy 为平仓方向：False=卖出平多，True=买入平空）

        不能用 SDK 的 market_close：它内部按私钥钱包地址查持仓，在 agent 委托模式下
        会查到 agent 钱包（无持仓）而非 main 账户。这里直接 order + reduce_only 下单。
        """
        print(f"[Order] 市价减仓 {coin} | 数量: {sz} | 方向: {'买入平空' if is_buy else '卖出平多'}")
        px = self.exchange._slippage_price(coin, is_buy, 0.01)
        return self.exchange.order(
            name=coin,
            is_buy=is_buy,
            sz=sz,
            limit_px=px,
            order_type={"limit": {"tif": "Ioc"}},
            reduce_only=True,
        )

    def get_position_size(self, coin: str) -> float:
        """返回该标的当前持仓的有符号数量：>0 多头，<0 空头，0 无持仓"""
        for p in self.get_open_positions():
            if p["coin"] == coin:
                return p["size"]
        return 0.0

    def cancel_open_orders(self, coin: str) -> int:
        """撤销该标的所有挂单（本系统挂单只有止盈/止损触发单），返回撤销数量"""
        try:
            open_orders = self.info.open_orders(self.main_address)
        except Exception as e:
            print(f"[Cancel] 查询挂单失败: {e}")
            return 0

        cancelled = 0
        for o in open_orders:
            if o.get("coin") != coin:
                continue
            oid = o.get("oid")
            try:
                self.exchange.cancel(coin, oid)
                cancelled += 1
                print(f"[Cancel] 已撤销 {coin} 挂单 oid={oid}")
            except Exception as e:
                print(f"[Cancel] 撤销 {coin} oid={oid} 失败: {e}")
        return cancelled


# ========== 风控引擎 ==========
class RiskEngine:
    """交易前风控检查"""
    
    def __init__(self, hl_client: HyperliquidClient, db: DatabaseManager):
        self.hl = hl_client
        self.db = db
    
    def check_signal(self, signal: Dict[str, Any]) -> tuple[bool, str]:
        confidence = signal.get("confidence", 0)
        if confidence < TradeConfig.MIN_CONFIDENCE:
            return False, f"置信度过低: {confidence} < {TradeConfig.MIN_CONFIDENCE}"
        
        position_size = signal.get("position_size", "观望")
        if position_size == "观望" or TradeConfig.POSITION_MAP.get(position_size, 0) == 0:
            return False, f"仓位建议为'{position_size}'，不执行"
        
        coin = resolve_coin(signal.get("agent_name"))
        if not coin:
            return False, "无对应的交易标的"
        # 注意：已有持仓不再直接跳过，交由 execute_signal 根据信号方向做加仓/减仓

        entry_price = signal.get("entry_price")
        if entry_price:
            try:
                mids = self.hl.info.all_mids()
                current_price = float(mids.get(coin, 0))
                if current_price > 0:
                    deviation = abs(current_price - entry_price) / entry_price
                    if deviation > TradeConfig.PRICE_DEVIATION_PCT:
                        return False, f"价格偏离过大: {deviation:.2%} > {TradeConfig.PRICE_DEVIATION_PCT:.0%}"
            except Exception as e:
                print(f"[Risk] 价格检查异常: {e}")
        
        return True, "通过"


# ========== 执行引擎 ==========
class TradeExecutor:
    """交易执行主引擎"""

    def __init__(self, db: Optional[DatabaseManager] = None):
        # 支持传入共享的 DatabaseManager 实例，避免多实例并发写入
        self.db = db if db is not None else DatabaseManager()
        self.hl = HyperliquidClient()
        self.risk = RiskEngine(self.hl, self.db)

        print(f"[Executor] 初始化完成 | 杠杆: {TradeConfig.LEVERAGE}x | "
              f"网络: {TradeConfig.NETWORK}")
    
    @staticmethod
    def _get_trade_side(signal_text: Optional[str]) -> Optional[str]:
        """
        根据 Agent 信号判断交易方向
        BULLISH/STRONG_BULLISH → BUY
        BEARISH/STRONG_BEARISH → SELL
        任何含 NEUTRAL → None（不执行）
        """
        if not signal_text:
            return None
        s = signal_text.upper()
        if "NEUTRAL" in s:
            return None
        if "BULLISH" in s:
            return "BUY"
        if "BEARISH" in s:
            return "SELL"
        return None
    
    def calculate_position_size(self, signal: Dict[str, Any]) -> Dict[str, float]:
        account_value = self.hl.get_account_value()
        position_size_label = signal.get("position_size", "轻仓试探")
        position_pct = TradeConfig.POSITION_MAP.get(position_size_label, 0.05)
        
        nominal_position = account_value * position_pct
        margin_required = nominal_position / TradeConfig.LEVERAGE
        
        coin = resolve_coin(signal.get("agent_name"))
        if not coin:
            return {"error": "无交易标的"}
        
        try:
            mids = self.hl.info.all_mids()
            current_price = float(mids.get(coin, 0))
            if current_price <= 0:
                return {"error": f"无法获取 {coin} 当前价格"}
            
            raw_sz = nominal_position / current_price
            sz_decimals = self.hl.get_sz_decimals(coin)
            sz = float(Decimal(str(raw_sz)).quantize(
                Decimal("0." + "0" * sz_decimals), rounding=ROUND_DOWN
            ))
            
            if sz <= 0:
                return {"error": f"计算数量过小: {sz}"}
            
            return {
                "coin": coin,
                "nominal_position": nominal_position,
                "margin_required": margin_required,
                "current_price": current_price,
                "sz": sz,
                "leverage": TradeConfig.LEVERAGE,
                "account_value": account_value,
            }
            
        except Exception as e:
            return {"error": f"计算仓位失败: {e}"}
    
    @staticmethod
    def _parse_price(val) -> Optional[float]:
        """把信号里的价格字段安全转成 float，失败返回 None"""
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _round_sz(self, sz: float, coin: str) -> float:
        """按标的 szDecimals 向下取整数量"""
        sz_decimals = self.hl.get_sz_decimals(coin)
        return float(Decimal(str(sz)).quantize(
            Decimal("0." + "0" * sz_decimals), rounding=ROUND_DOWN
        ))

    def _execute_market_order(self, coin: str, is_buy: bool, sz: float,
                              entry_price: Optional[float], reduce_only: bool = False):
        """下市价单并解析成交结果，返回 (executed_price, executed_sz)，失败返回 None"""
        result = self.hl.place_market_close(coin, sz, is_buy) if reduce_only \
            else self.hl.place_market_order(coin, is_buy=is_buy, sz=sz)
        print(f"[Order] 下单结果: {json.dumps(result, indent=2)}")

        if result.get("status") != "ok":
            print(f"[Execute] 下单失败")
            return None

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses:
            print(f"[Execute] 无法获取成交状态")
            return None

        status = statuses[0]
        if "filled" not in status:
            print(f"[Execute] 订单未成交: {status}")
            return None

        fill_info = status["filled"]
        executed_price = float(fill_info.get("avgPx", entry_price or 0))
        executed_sz = float(fill_info.get("totalSz", sz))
        print(f"[Fill] 成交价格: {executed_price} | 成交数量: {executed_sz}")
        return executed_price, executed_sz

    def _place_sl_tp(self, coin: str, side: str, sz: float, executed_price: float,
                     stop_loss: Optional[float], take_profit: Optional[float]):
        """为 side 方向、数量 sz 的持仓挂止盈/止损触发单（平仓方向自动取反）"""
        is_buy_close = (side != "BUY")   # 平多=卖出(False)，平空=买入(True)
        MIN_TRIGGER_DISTANCE = 0.02

        if stop_loss:
            sl_distance = abs(executed_price - stop_loss) / executed_price
            if sl_distance < MIN_TRIGGER_DISTANCE:
                if side == "BUY":
                    # 多单：止损往下调
                    adjusted_sl = executed_price * (1 - MIN_TRIGGER_DISTANCE)
                else:
                    # 空单：止损往上调
                    adjusted_sl = executed_price * (1 + MIN_TRIGGER_DISTANCE)
                stop_loss = self.hl.round_price(adjusted_sl, coin)
                print(f"[Risk] 止损距离 {sl_distance:.2%} 过小，自动调整为止损价: {stop_loss}")

            sl_price = self.hl.round_price(stop_loss, coin)
            sl_result = self.hl.place_trigger_order(
                coin=coin, is_buy=is_buy_close, sz=sz,
                trigger_px=sl_price, tpsl="sl", is_market=True
            )
            print(f"[Order] 止损单结果: {json.dumps(sl_result, indent=2)}")

        if take_profit:
            tp_distance = abs(take_profit - executed_price) / executed_price
            if tp_distance < MIN_TRIGGER_DISTANCE:
                if side == "BUY":
                    # 多单：止盈往上调
                    adjusted_tp = executed_price * (1 + MIN_TRIGGER_DISTANCE)
                else:
                    # 空单：止盈往下调
                    adjusted_tp = executed_price * (1 - MIN_TRIGGER_DISTANCE)
                take_profit = self.hl.round_price(adjusted_tp, coin)
                print(f"[Risk] 止盈距离 {tp_distance:.2%} 过小，自动调整为止盈价: {take_profit}")

            tp_price = self.hl.round_price(take_profit, coin)
            tp_result = self.hl.place_trigger_order(
                coin=coin, is_buy=is_buy_close, sz=sz,
                trigger_px=tp_price, tpsl="tp", is_market=False
            )
            print(f"[Order] 止盈单结果: {json.dumps(tp_result, indent=2)}")

    def _record_trade(self, signal: Dict[str, Any], coin: str, side: str, executed_sz: float,
                      executed_price: float, entry_price: Optional[float],
                      stop_loss: Optional[float], take_profit: Optional[float], status: str) -> int:
        """写入 trades 表；CLOSED 交易额外补充平仓信息"""
        trade_data = {
            "signal_id": signal.get("id"),
            "event_id": signal.get("event_id"),
            "agent_name": signal.get("agent_name"),
            "ticker": coin,
            "side": side,
            "signal_price": entry_price,
            "executed_price": executed_price,
            "executed_qty": executed_sz,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "status": status,
            "is_paper_trade": TradeConfig.NETWORK == "testnet",
            "opened_at": datetime.now().isoformat(),
        }
        trade_id = self.db.insert_trade(trade_data)
        print(f"[DB] 交易已入库 | trade_id={trade_id} | side={side} | status={status}")

        if status == "CLOSED":
            try:
                self.db.update_trade_status(
                    trade_id, "CLOSED",
                    closed_at=datetime.now().isoformat(),
                    close_price=executed_price,
                    close_reason="SIGNAL_REVERSE"
                )
            except Exception as e:
                print(f"[DB] 平仓信息补充失败: {e}")
        return trade_id

    def _get_open_trade_sl_tp(self, agent_name: str, coin: str):
        """减仓后剩余仓位沿用最早一笔开仓交易的止盈止损价；找不到返回 (None, None)"""
        for t in self.db.get_open_trades(agent_name):
            if t.get("ticker") == coin:
                return self._parse_price(t.get("stop_loss")), self._parse_price(t.get("take_profit"))
        return None, None

    def _close_open_trades(self, agent_name: str, coin: str, close_price: float, reason: str = "SIGNAL_REVERSE"):
        """全部平仓时，把该 Agent 在该标的上仍为 OPEN 的历史交易标记为已平仓"""
        for t in self.db.get_open_trades(agent_name):
            if t.get("ticker") == coin:
                try:
                    self.db.update_trade_status(
                        t["id"], "CLOSED",
                        closed_at=datetime.now().isoformat(),
                        close_price=close_price,
                        close_reason=reason
                    )
                except Exception as e:
                    print(f"[DB] 平仓记录更新失败 trade_id={t['id']}: {e}")

    def execute_signal(self, signal_id: int) -> bool:
        """
        执行单个信号（支持做多/做空双向）。
        无持仓 → 开仓；与持仓同向 → 加仓；与持仓反向 → 减仓。
        """
        signal = self.db.get_signal_by_id(signal_id)
        if not signal:
            print(f"[Execute] 信号 {signal_id} 不存在")
            return False

        side = self._get_trade_side(signal.get("signal"))
        if side is None:
            print(f"[Execute] 信号方向不明确或中性，跳过执行")
            self._update_signal_status(signal_id, "SKIPPED", "信号方向不明确或中性")
            return False

        is_buy = (side == "BUY")

        passed, reason = self.risk.check_signal(signal)
        print(f"[Risk] {reason}")
        if not passed:
            self._update_signal_status(signal_id, "SKIPPED", reason)
            return False

        pos_calc = self.calculate_position_size(signal)
        if "error" in pos_calc:
            print(f"[Execute] 仓位计算失败: {pos_calc['error']}")
            return False

        coin = pos_calc["coin"]
        account_value = pos_calc["account_value"]
        current_price = pos_calc["current_price"]
        base_sz = pos_calc["sz"]   # 本次信号按 position_size 比例算出的数量

        entry_price = self._parse_price(signal.get("entry_price"))
        stop_loss = self._parse_price(signal.get("stop_loss"))
        take_profit = self._parse_price(signal.get("take_profit"))

        # 当前持仓（有符号）：>0 多头，<0 空头，0 无持仓
        cur_sz = self.hl.get_position_size(coin)
        cur_is_long = cur_sz > 0
        cur_is_short = cur_sz < 0

        # 判定动作：无持仓开仓；同向加仓；反向减仓
        if cur_sz == 0:
            action = "OPEN"
        elif (cur_is_long and is_buy) or (cur_is_short and not is_buy):
            action = "ADD"
        else:
            action = "REDUCE"

        print(f"\n{'='*60}")
        print(f"[Execute] signal_id={signal_id} | event_id={signal.get('event_id')} | "
              f"方向={side} | 当前持仓={cur_sz} | 动作={action}")

        try:
            if action == "OPEN":
                # 开仓：直接按信号仓位下单
                fill = self._execute_market_order(coin, is_buy, base_sz, entry_price, reduce_only=False)
                if not fill:
                    return False
                executed_price, executed_sz = fill
                self._place_sl_tp(coin, side, executed_sz, executed_price, stop_loss, take_profit)
                self._record_trade(signal, coin, side, executed_sz, executed_price,
                                   entry_price, stop_loss, take_profit, "OPEN")
                self._update_signal_status(signal_id, "EXECUTED", f"{side} 开仓成功")

            elif action == "ADD":
                # 加仓：受 MAX_POSITION_PCT 上限约束
                add_sz = base_sz
                current_nominal = abs(cur_sz) * current_price
                add_nominal = add_sz * current_price
                max_nominal = account_value * TradeConfig.MAX_POSITION_PCT

                if current_nominal + add_nominal > max_nominal:
                    allowed_nominal = max_nominal - current_nominal
                    if allowed_nominal <= 0:
                        print(f"[Execute] 仓位已达上限 {TradeConfig.MAX_POSITION_PCT:.0%}，跳过加仓")
                        self._update_signal_status(signal_id, "SKIPPED",
                                                   f"仓位已达上限 {TradeConfig.MAX_POSITION_PCT:.0%}")
                        return False
                    add_sz = self._round_sz(allowed_nominal / current_price, coin)
                    print(f"[Execute] 加仓触及上限，由 {base_sz} 缩减至 {add_sz}")
                    if add_sz <= 0:
                        self._update_signal_status(signal_id, "SKIPPED", "加仓数量过小")
                        return False

                fill = self._execute_market_order(coin, is_buy, add_sz, entry_price, reduce_only=False)
                if not fill:
                    return False
                executed_price, executed_sz = fill
                # 只给新增部分挂止盈止损，旧仓的止盈止损保持不变
                self._place_sl_tp(coin, side, executed_sz, executed_price, stop_loss, take_profit)
                self._record_trade(signal, coin, side, executed_sz, executed_price,
                                   entry_price, stop_loss, take_profit, "OPEN")
                self._update_signal_status(signal_id, "EXECUTED", f"{side} 加仓成功")

            else:  # REDUCE
                # 减仓：按信号比例减，但不超过当前持仓
                reduce_sz = min(base_sz, abs(cur_sz))
                full_close = reduce_sz >= abs(cur_sz) - 1e-9

                # 先撤销旧止盈止损，避免减仓后残留的 reduce-only 单超出剩余仓位
                self.hl.cancel_open_orders(coin)

                fill = self._execute_market_order(coin, is_buy, reduce_sz, entry_price, reduce_only=True)
                if not fill:
                    return False
                executed_price, executed_sz = fill

                closing_side = "SELL" if cur_is_long else "BUY"
                remaining_sz = self._round_sz(abs(cur_sz) - executed_sz, coin)

                if remaining_sz > 0:
                    print(f"[Execute] 减仓后剩余持仓 {remaining_sz}")
                    # 剩余仓位沿用原开仓交易的止盈止损价（反向信号的止盈止损不适用于剩余仓位）
                    orig_sl, orig_tp = self._get_open_trade_sl_tp(signal.get("agent_name"), coin)
                    if orig_sl or orig_tp:
                        remaining_side = "BUY" if cur_is_long else "SELL"
                        self._place_sl_tp(coin, remaining_side, remaining_sz, executed_price, orig_sl, orig_tp)
                    else:
                        print(f"[Risk] 剩余持仓 {remaining_sz} 无可用止盈止损价，未重新挂单，请人工关注")
                else:
                    print(f"[Execute] 已全部平仓")

                self._record_trade(signal, coin, closing_side, executed_sz, executed_price,
                                   entry_price, stop_loss, take_profit, "CLOSED")
                if full_close:
                    self._close_open_trades(signal.get("agent_name"), coin, executed_price)
                self._update_signal_status(signal_id, "EXECUTED", f"{side} 触发减仓成功")

            return True

        except Exception as e:
            print(f"[Execute] 执行异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _update_signal_status(self, signal_id: int, status: str, reason: str):
        try:
            self.db.update_signal_status(signal_id, status, reason)
            print(f"[Signal] signal_id={signal_id} 标记为 {status}: {reason}")
        except Exception as e:
            print(f"[Signal] 更新状态失败: {e}")
    
    def run(self, limit: int = 10):
        # print(f"\n{'='*60}")
        # print("[Executor] 扫描未执行信号...")
        
        total = 0
        for agent_name in AGENT_TO_COIN:
            signals = self.db.get_unprocessed_signals(agent_name, limit=limit)
            total += len(signals)
            if len(signals) != 0:
                print(f"[Executor] {agent_name}: 找到 {len(signals)} 条未执行信号")

            for signal in signals:
                self.execute_signal(signal["id"])
                time.sleep(1)
        
        # print(f"[Executor] 本轮共扫描 {total} 条未执行信号，执行完成")


# ========== 入口 ==========
if __name__ == "__main__":
    executor = TradeExecutor()
    executor.run()