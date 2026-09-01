"""
宏观快照工具 (macro_snapshot)
直接调 Yahoo Finance v8 chart API（网页端用的，限制宽松）
系统层自动注入，非 LLM 直接调用
"""

import os
import time
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from src.tools.hy_finance_tools import info

# ========== 缓存 ==========
""" 宏观经济趋势5分钟内有效 """
_cache: Optional[Dict[str, Any]] = None # 内存中的宏观数据快照（最后一次成功拉取的结果）
_cache_timestamp: float = 0 # 上次写入缓存的时间戳
_CACHE_TTL_SECONDS = 300  # 5 分钟

# Yahoo Finance v8 chart API
_YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Ticker 映射
_YF_TICKERS = {
    "dxy": "DX-Y.NYB", # ICE 美元指数期货
    "us10y": "^TNX", # CBOE 10 年期美债收益率指数
    "spx_futures": "ES=F", # CME 标普 500 E-mini 期货
    "vix": "^VIX", # CBOE 波动率指数（恐慌指数）
    "gold": "GC=F", # COMEX 黄金期货
}
 
# 伪装请求头
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def _fetch_yf_chart(name: str, ticker: str) -> Dict[str, Any]:
    """直接调 Yahoo v8 chart API"""
    url = f"{_YF_BASE}/{ticker}?interval=1d&range=5d"

def _fetch_yf_chart(name: str, ticker: str) -> Dict[str, Any]:
    """直接调 Yahoo v8 chart API"""
    url = f"{_YF_BASE}/{ticker}?interval=1d&range=5d"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        # 过滤 None（周末/节假日无数据）
        valid = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
        if len(valid) < 2:
            return {"error": "有效数据不足"}

        latest_close = valid[-1][1]
        prev_close = valid[-2][1]
        change_pct = round((latest_close - prev_close) / prev_close * 100, 2)

        # US10Y 特殊处理：返回收益率和基点变化
        if name == "us10y":
            change_bps = round((latest_close - prev_close) * 100, 1)
            trend = "up" if change_bps > 5 else "down" if change_bps < -5 else "sideways"
            return {
                "yield": round(latest_close, 2),
                "change_24h_bps": change_bps,
                "trend": trend
            }

        trend = "up" if change_pct > 0.3 else "down" if change_pct < -0.3 else "sideways"
        return {
            "price": round(latest_close, 2),
            "change_24h_pct": change_pct,
            "trend": trend
        }

    except requests.exceptions.ProxyError as e:
        return {"error": f"代理错误: {e}"}
    except requests.exceptions.Timeout:
        return {"error": "请求超时"}
    except Exception as e:
        return {"error": str(e)}

def _fetch_btc_funding() -> Dict[str, Any]:
    """从 Hyperliquid 查 BTC 资金费率"""
    try:
        data = info.meta_and_asset_ctxs()
        if not data or len(data) < 2:
            return {"error": "无法获取 Hyperliquid 数据"}

        universe = data[0]["universe"]
        ctxs = data[1]

        for asset, ctx in zip(universe, ctxs):
            if asset["name"] == "BTC":
                price = float(ctx.get("markPx", 0))
                funding = float(ctx.get("funding", 0))
                prev_day_px = float(ctx.get("prevDayPx", price))
                change_pct = round(((price - prev_day_px) / prev_day_px * 100), 2) if prev_day_px else 0

                return {
                    "price": round(price, 2),
                    "change_24h_pct": change_pct,
                    "funding_1h": round(funding * 100, 4),
                    "trend": "up" if change_pct > 1 else "down" if change_pct < -1 else "sideways"
                }

        return {"error": "BTC 数据未找到"}

    except Exception as e:
        return {"error": str(e)}

def _calculate_regime(data: Dict[str, Any]) -> Dict[str, Any]:
    """计算市场状态 (risk_on / risk_off / neutral)"""
    score = 0
    reasons = []

    dxy = data.get("dxy", {})
    us10y = data.get("us10y", {})
    spx = data.get("spx_futures", {})
    vix = data.get("vix", {})

    # DXY
    if "error" not in dxy:
        if dxy.get("change_24h_pct", 0) > 0.5:
            score += 2
            reasons.append("DXY 强势")
        elif dxy.get("change_24h_pct", 0) < -0.5:
            score -= 1

    # US10Y
    if "error" not in us10y:
        if us10y.get("change_24h_bps", 0) > 5:
            score += 3
            reasons.append("美债收益率飙升")
        elif us10y.get("change_24h_bps", 0) < -5:
            score -= 1

    # SPX 期货
    if "error" not in spx:
        if spx.get("change_24h_pct", 0) < -1:
            score += 1
            reasons.append("美股期货下跌")
        elif spx.get("change_24h_pct", 0) > 1:
            score -= 1

    # VIX
    if "error" not in vix:
        if vix.get("price", 0) > 25:
            score += 2
            reasons.append("VIX 恐慌")
        elif vix.get("price", 0) < 15:
            score -= 1

    if score >= 7:
        regime = "risk_off"
    elif score <= 3:
        regime = "risk_on"
    else:
        regime = "neutral"

    return {
        "market_regime": regime,
        "risk_score": score,
        "reasoning": reasons
    }


def get_macro_snapshot() -> Dict[str, Any]:
    """
    获取宏观市场快照。
    5 分钟 TTL 缓存，全部数据源失败时返回空 dict。
    """
    global _cache, _cache_timestamp

    now = time.time()
    if _cache is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        return _cache.copy()

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 顺序拉取 yfinance 数据（单线程 + 0.3s 间隔，避免限流）
    for key, ticker in _YF_TICKERS.items():
        snapshot[key] = _fetch_yf_chart(key, ticker)
        time.sleep(0.3)

    # 拉取 BTC 资金费率
    snapshot["btc"] = _fetch_btc_funding()

    # 计算市场状态
    regime_info = _calculate_regime(snapshot)
    snapshot.update(regime_info)

    # 检查是否有任何有效数据
    has_valid = any(
        "error" not in snapshot.get(k, {})
        for k in _YF_TICKERS.keys()
    ) or ("error" not in snapshot.get("btc", {}))

    if not has_valid:
        return {}

    _cache = snapshot.copy()
    _cache_timestamp = now
    return snapshot