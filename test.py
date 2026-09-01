"""
绕过 yfinance，直接调 Yahoo Finance v8 chart API
测试稳定性 + 代理支持
"""

import os
import time
import json
import requests
from datetime import datetime

# ========== 代理配置（自动读取系统环境变量）==========
PROXIES = {}
http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
if http_proxy:
    PROXIES["http"] = http_proxy
if https_proxy:
    PROXIES["https"] = https_proxy

print(f"代理配置: {PROXIES if PROXIES else '无'}")

# Yahoo Finance v8 chart API（网页端用的，限制宽松）
BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

TICKERS = {
    "dxy": "DX-Y.NYB",        # 美元指数
    "us10y": "^TNX",          # 10年期美债
    "spx_futures": "ES=F",    # 标普期货
    "vix": "^VIX",            # 恐慌指数
    "gold": "GC=F",           # 黄金期货
}


def fetch_chart(ticker: str, name: str) -> dict:
    """直接调 Yahoo v8 chart API"""
    url = f"{BASE_URL}/{ticker}?interval=1d&range=5d"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    start = time.time()
    try:
        resp = requests.get(url, headers=headers, proxies=PROXIES, timeout=10)
        latency = int((time.time() - start) * 1000)

        if resp.status_code == 429:
            return {"status": "RATE_LIMIT", "error": "429 Too Many Requests", "latency_ms": latency}

        resp.raise_for_status()
        data = resp.json()

        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        # 过滤 None（周末/节假日无数据）
        valid = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
        if len(valid) < 2:
            return {"status": "FAIL", "error": "有效数据不足", "latency_ms": latency}

        latest_close = valid[-1][1]
        prev_close = valid[-2][1]
        change_pct = round((latest_close - prev_close) / prev_close * 100, 2)

        return {
            "status": "OK",
            "price": round(latest_close, 2),
            "change_24h_pct": change_pct,
            "data_points": len(valid),
            "latency_ms": latency,
        }

    except requests.exceptions.ProxyError as e:
        return {"status": "FAIL", "error": f"代理错误: {e}", "latency_ms": int((time.time()-start)*1000)}
    except Exception as e:
        return {"status": "FAIL", "error": str(e), "latency_ms": int((time.time()-start)*1000)}


def test_sequential():
    """顺序拉取（模拟 macro_snapshot 实际模式）"""
    print("\n" + "="*60)
    print("测试1: 顺序拉取（带 0.3s 延迟）")
    print("="*60)

    results = {}
    for name, ticker in TICKERS.items():
        results[name] = fetch_chart(ticker, name)
        status = "✅" if results[name]["status"] == "OK" else "❌"
        print(f"{status} {name} ({ticker}): {results[name]}")
        time.sleep(0.3)

    _summary(results)
    return results


def test_rapid():
    """快速连续拉取（压力测试）"""
    print("\n" + "="*60)
    print("测试2: 快速连续拉取（无延迟）")
    print("="*60)

    results = {}
    for name, ticker in TICKERS.items():
        results[name] = fetch_chart(ticker, name)
        status = "✅" if results[name]["status"] == "OK" else "❌"
        print(f"{status} {name} ({ticker}): {results[name]}")

    _summary(results)
    return results


def test_retry():
    """容错：错误 ticker + 正确 ticker"""
    print("\n" + "="*60)
    print("测试3: 容错测试")
    print("="*60)

    r1 = fetch_chart("INVALID_TICKER_123", "错误代码")
    print(f"{'❌' if r1['status']!='OK' else '✅'} 错误代码: {r1}")

    time.sleep(0.5)
    r2 = fetch_chart("GC=F", "恢复测试")
    print(f"{'✅' if r2['status']=='OK' else '❌'} 恢复测试: {r2}")

    if r2["status"] == "OK":
        print("\n✅ 容错 OK：错误请求未影响后续")
    else:
        print("\n⚠️  警告：可能被全局限流")


def _summary(results: dict):
    ok = sum(1 for r in results.values() if r["status"] == "OK")
    fail = len(results) - ok
    print(f"\n汇总: 成功 {ok} | 失败 {fail}")


def main():
    print(f"Yahoo Finance v8 API 稳定性测试 | {datetime.now()}")
    test_sequential()
    test_rapid()
    test_retry()
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)


if __name__ == "__main__":
    main()