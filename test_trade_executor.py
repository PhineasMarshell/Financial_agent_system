"""
TradeExecutor 修复测试 - 用 ETH 代替 PAXG
"""

import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from src.database.db_manager import DatabaseManager
from src.execution.trade_executor import TradeExecutor, TradeConfig, AGENT_TO_COIN

TEST_COIN = "ETH"  # testnet 有流动性的币种
AGENT_TO_COIN["gold"] = TEST_COIN  # 测试用：把 gold 临时映射到测试币

def make_signal(signal_type: str, confidence: int, position: str = "轻仓试探"):
    return {
        "event_id": f"test_{signal_type.lower()}_{datetime.now().strftime('%H%M%S%f')[:-3]}",
        "agent_name": "gold",
        "source": "test_script",
        "content_snippet": f"测试 {signal_type}",
        "signal": signal_type,
        "confidence": confidence,
        "timeframe": "INTRADAY",
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "position_size": position,
        "key_drivers": [f"test_{signal_type}"],
        "risk_factors": [],
        "reasoning": "test",
        "tools_used": [],
        "raw_report": "test",
        "latency_ms": 100,
    }

def test_side_parser():
    """先单独测试 _get_trade_side，不碰网络"""
    print("=" * 60)
    print("测试 _get_trade_side 方向解析")
    print("=" * 60)
    
    cases = [
        ("BULLISH", "BUY"),
        ("STRONG_BULLISH", "BUY"),
        ("BEARISH", "SELL"),
        ("STRONG_BEARISH", "SELL"),
        ("NEUTRAL", None),
        ("NEUTRAL_BULLISH", None),
        ("NEUTRAL_BEARISH", None),
        ("", None),
        (None, None),
    ]
    
    for sig, expected in cases:
        result = TradeExecutor._get_trade_side(sig)
        status = "✅" if result == expected else "❌"
        print(f"{status} _get_trade_side('{sig}') = {result} (预期: {expected})")
    
    print()

def run_once(name: str, signal_data: dict, expect_trade: bool):
    """执行单个测试"""
    print(f"\n{'='*60}")
    print(f"【{name}】signal={signal_data['signal']} | confidence={signal_data['confidence']}")
    print(f"{'='*60}")
    
    db = DatabaseManager()
    executor = TradeExecutor()
    
    # 清掉已有 ETH 持仓，避免重复持仓风控
    positions = executor.hl.get_open_positions()
    for p in positions:
        if p["coin"] == TEST_COIN:
            is_buy = p["size"] < 0
            sz = abs(p["size"])
            print(f"[Cleanup] 平掉已有 {TEST_COIN} 持仓 {sz}")
            executor.hl.place_market_order(TEST_COIN, is_buy=is_buy, sz=sz)
    
    # 插入并执行
    sid = db.insert_signal(signal_data)
    result = executor.execute_signal(sid)
    
    # 验证
    if expect_trade and result:
        print(f"\n✅ PASS: {name} - 成功开仓")
    elif not expect_trade and not result:
        print(f"\n✅ PASS: {name} - 正确跳过")
    else:
        print(f"\n❌ FAIL: {name} - 预期执行={expect_trade}, 实际={result}")
    
    return result

def main():
    print(f"网络: {TradeConfig.NETWORK}")
    print(f"测试币种: {TEST_COIN}")
    
    # 0. 先测方向解析
    test_side_parser()
    
    # 1. 做多 ETH
    run_once("做多 ETH", make_signal("BULLISH", 8), expect_trade=True)
    
    # 2. 做空 ETH
    run_once("做空 ETH", make_signal("STRONG_BEARISH", 9), expect_trade=True)
    
    # 3. 中性跳过
    run_once("中性跳过", make_signal("NEUTRAL", 5), expect_trade=False)
    
    # 4. 低置信度拦截
    run_once("低置信度拦截", make_signal("BULLISH", 2), expect_trade=False)
    
    # 5. 观望仓位拦截
    run_once("观望仓位拦截", make_signal("BULLISH", 8, position="观望"), expect_trade=False)
    
    print(f"\n{'='*60}")
    print("测试完成，去 testnet 查看：")
    print("https://app.hyperliquid-testnet.xyz/portfolio")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()