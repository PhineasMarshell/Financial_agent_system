import os
import asyncio
import base64
import json
from urllib.parse import quote
import websockets
from dotenv import load_dotenv
import ssl
from datetime import datetime

# 加载env文件
load_dotenv()

BASE = os.getenv("FEED_API_BASE_URL")
API_V1 = f"{BASE.rstrip('/')}/api/v1"
API_KEY = os.getenv("FEED_API_KEY")

# ========== 第一层过滤配置 ==========
FILTER_CONFIG = {
    "min_filter_score": 0.6,           # 最低过滤评分
    "min_temporal_realis": 0.8,        # 时态确定性最低分
    "require_event_id": True,          # 是否要求有 event_id
}

def build_ws_uri(base_url: str, token: str) -> str:
    # 把 http:// 或 https:// 替换为 ws:// 或 wss://
    base = base_url.rstrip("/")
    ws_url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return f"{ws_url}/feed/news/ws?token={quote(token, safe='')}"

def should_filter_news(data_dict: dict) -> tuple[bool, str]:
    """
    第一层过滤：判断新闻是否值得进入分析流程
    返回: (是否过滤掉, 原因)
    """
    # 检查是否有实质内容
    app_msg = data_dict.get("app_msg", "").strip()
    if not app_msg:
        return True, "空内容"
    
    # 检查是否是纯包装内容
    if data_dict.get("filter_is_packaging_only") is True:
        return True, "纯包装内容"
    
    # 检查过滤评分
    filter_score = data_dict.get("filter_score")
    if filter_score is not None and filter_score < FILTER_CONFIG["min_filter_score"]:
        return True, f"过滤评分过低({filter_score:.2f} < {FILTER_CONFIG['min_filter_score']})"
    
    # 检查时态确定性
    temporal_realis = data_dict.get("filter_temporal_realis")
    if temporal_realis is not None and temporal_realis < FILTER_CONFIG["min_temporal_realis"]:
        return True, f"时态确定性不足({temporal_realis:.2f} < {FILTER_CONFIG['min_temporal_realis']})"
    
    # 检查 event_id（用于追踪）
    if FILTER_CONFIG["require_event_id"] and not data_dict.get("event_id"):
        # 有些源可能没有 event_id，降级为警告但不过滤，用 source+published_time 生成伪id
        pass  # 不过滤，但后续处理要注意
    
    return False, "通过"

def format_news(data_dict: dict) -> dict:
    """将原始 WS 消息转换为标准格式，供后续 Agent 使用"""
    source = data_dict.get("source", "未知来源")
    raw_msg = data_dict.get("app_msg", "")
    ts = data_dict.get("captured_time") or data_dict.get("published_time")
    
    readable_time = "未知时间"
    if ts:
        readable_time = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    
    # 生成唯一追踪 ID
    event_id = data_dict.get("event_id")
    if not event_id:
        # 降级：用 source + published_time 生成伪 ID
        pub_time = data_dict.get("published_time", "unknown")
        event_id = f"{source}:{pub_time}"
    
    return {
        "event_id": event_id,
        "time": readable_time,
        "source": source,
        "content": raw_msg,
        "origin_link": data_dict.get("origin_link"),
        "filter_score": data_dict.get("filter_score"),
        "filter_observation": data_dict.get("filter_observation"),
        "crawler_node": data_dict.get("crawler_node"),
        "published_time": data_dict.get("published_time"),
        "captured_time": data_dict.get("captured_time"),
    }

async def listen_to_market(callback=None):
    key = API_KEY.strip()
    if not key:
        print("请填写 API_KEY")
        return

    # 创建一个忽略证书校验的 SSL 上下文 (应对代理软件干扰)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    token = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
    uri = build_ws_uri(API_V1, token)
    print("connect:", uri[:120] + ("..." if len(uri) > 120 else ""))

    reconnect_attempts = 0
    stats = {"total": 0, "filtered": 0, "passed": 0}

    while True:
        try:
            async with websockets.connect(
                uri, 
                ssl=ssl_context, 
                user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64) curl/7.81.0"
            ) as websocket:

                if reconnect_attempts > 0:
                    print(f"[WS] 连接恢复，重置重连计数 (之前累计 {reconnect_attempts} 次)")
                    reconnect_attempts = 0

                await websocket.send("ping")
                response = await websocket.recv()
                print("首包响应:", response)

                while True:
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=5)
                        try:
                            data_dict = json.loads(response)
                        except json.JSONDecodeError:
                            print("非 JSON:", response[:200])
                            continue

                        # 心跳过滤
                        if data_dict.get("msg") == "pong":
                            continue
                        
                        stats["total"] += 1

                        # ========== 第一层过滤 ==========
                        should_filter, reason = should_filter_news(data_dict)
                        
                        if should_filter:
                            stats["filtered"] += 1
                            if stats["total"] % 50 == 0:
                                print(f"[过滤] {reason} | 来源: {data_dict.get('source')} | {data_dict.get('app_msg', '')[:50]}...")
                            continue
                        
                        stats["passed"] += 1
                        
                        # 格式化
                        formatted_news = format_news(data_dict)
                        
                        # 打印统计（每50条打印一次）
                        # if stats["total"] % 50 == 0:
                        #     print(f"[统计] 总接收: {stats['total']}, 过滤: {stats['filtered']}, 通过: {stats['passed']}")

                        # 发给 Agent
                        if callback:
                            asyncio.create_task(callback(formatted_news))

                    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                        try:
                            await websocket.send("ping")
                            await websocket.recv()
                            continue
                        except Exception as e:
                            print("连接异常，将重连:", e)
                            break

        except websockets.exceptions.ConnectionClosed as e:
            reconnect_attempts += 1
            wait_time = min(2**reconnect_attempts, 60)
            print(f"Connection closed: {e}\n Reconnecting in {wait_time} seconds...")
            await asyncio.sleep(wait_time)
            continue

        except asyncio.TimeoutError as e:
            reconnect_attempts += 1
            wait_time = min(2**reconnect_attempts, 60)
            print(f"Timeout: {e}\n Reconnecting in {wait_time} seconds...")
            await asyncio.sleep(wait_time)
            continue

        except Exception as e:
            print("其他错误，稍后重试:", e)
            await asyncio.sleep(10)
            continue


if __name__ == "__main__":
    asyncio.run(listen_to_market())