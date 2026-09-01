from hyperliquid.info import Info
from hyperliquid.utils import constants
import time
import datetime
import json
import os

# ========== 网络统一配置 ==========
# 与 trade_executor.py 保持严格一致，避免价格偏差
NETWORK = os.getenv("HYPERLIQUID_NETWORK")
print(f"下单网为{NETWORK}")
API_URL = constants.TESTNET_API_URL if NETWORK == "testnet" else constants.MAINNET_API_URL
info = Info(API_URL, skip_ws=True)

ACTION_REGISTRY = {}

def register_action(name: str):
    """动作注册装饰器"""
    def decorator(func):
        ACTION_REGISTRY[name] = func
        return func
    return decorator

@ register_action("get_latest_price")
def _get_latest_price(coin: str, **kwargs) -> dict:
    """原子功能：查最新价"""
    mids = info.all_mids()
    price = mids.get(coin)
    if not price:
        return {"error": f"未找到 {coin} 的报价"}
    return {"latest_price": float(price)}

@ register_action("get_candles")
def _get_candles(coin: str, interval: str = "15m", limit: int = 20, **kwargs) -> dict:
    """原子功能：查K线"""
    interval_ms_map = {
        "1m": 60 * 1000, "5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000,
        "1h": 3600 * 1000, "4h": 4 * 3600 * 1000, "1d": 24 * 3600 * 1000
    }
    ms_per_candle = interval_ms_map.get(interval, 15 * 60 * 1000)
    end_time = int(time.time() * 1000)
    start_time = end_time - (limit * ms_per_candle)
    
    candles = info.candles_snapshot(coin, interval, start_time, end_time)
    if not candles:
        return {"error": f"未能获取 {coin} 的 K 线数据"}
         
    result_data = [{
        "time": datetime.datetime.fromtimestamp(c['t'] / 1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        "open": float(c['o']), "close": float(c['c']),
        "high": float(c['h']), "low": float(c['l']),
        "volume": float(c['v'])
    } for c in candles[-limit:]]
    
    return {"interval": interval, "status": f"成功获取 {len(result_data)} 根 K 线", "data": result_data}

def hyperliquid_query(action: str, coin: str, interval: str = None, limit: int = None) -> str:
    """Hyperliquid的统一入口"""
    coin = "PAXG" if coin.upper() in ["XAU", "GOLD", "XAU/USD", "XAUUSD", "黄金"] else coin.upper()
    target_action = ACTION_REGISTRY.get(action)
    
    if not target_action:
        return json.dumps({"error": f"未知的 action 类型: {action}"})
        
    try:
        result_dict = target_action(coin=coin, interval=interval, limit=limit)
        result_dict.update({"action": action, "coin": coin})
        return json.dumps(result_dict)
    except Exception as e:
        return json.dumps({"error": f"Hyperliquid 执行报错: {str(e)}"})