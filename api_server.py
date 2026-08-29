"""
Financial Agent Dashboard API
独立 FastAPI 服务，为前端提供 REST + WebSocket 实时推送
"""

import os
import sys
import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

DB_PATH = "financial_agent_db.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ["key_drivers", "risk_factors", "tickers", "tools_used"]:
        if field in d and d[field]:
            try:
                d[field] = json.loads(d[field])
            except Exception:
                d[field] = []
        else:
            d[field] = []
    return d


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        disconnected = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                disconnected.append(conn)
        for d in disconnected:
            self.disconnect(d)


manager = ConnectionManager()


async def poll_new_signals():
    last_id = 0
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT MAX(id) FROM signals")
        row = cur.fetchone()
        last_id = row[0] or 0
        conn.close()
    except Exception as e:
        print(f"[Poll] 初始化失败: {e}")

    print(f"[Poll] 开始轮询新信号，起始 id={last_id}")

    while True:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM signals WHERE id > ? ORDER BY id ASC LIMIT 20",
                (last_id,),
            )
            rows = cur.fetchall()
            conn.close()

            for row in rows:
                data = parse_row(row)
                await manager.broadcast({"type": "new_signal", "data": data})
                last_id = max(last_id, data["id"])
                print(
                    f"[Poll] 广播新信号 id={data['id']} event_id={data.get('event_id')}"
                )
        except Exception as e:
            print(f"[Poll] 异常: {e}")

        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_new_signals())
    yield
    task.cancel()


app = FastAPI(title="Financial Agent Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/signals")
def get_signals(limit: int = 50, agent: str = None):
    conn = get_db()
    cur = conn.cursor()
    if agent:
        cur.execute(
            "SELECT * FROM signals WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?",
            (agent, limit),
        )
    else:
        cur.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    rows = cur.fetchall()
    conn.close()
    return [parse_row(r) for r in rows]


@app.get("/api/trades")
def get_trades(status: str = None, limit: int = 50):
    conn = get_db()
    cur = conn.cursor()
    if status:
        cur.execute(
            "SELECT * FROM trades WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        cur.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/account/summary")
def get_account_summary():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM signals")
    total_signals = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM signals WHERE created_at > datetime('now', '-1 day')"
    )
    today_signals = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM signals WHERE execution_status = 'PENDING'")
    pending_signals = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'OPEN'")
    open_trades = cur.fetchone()[0]

    cur.execute(
        "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades WHERE status = 'CLOSED'"
    )
    total_pnl = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM trades WHERE status = 'CLOSED' AND realized_pnl > 0"
    )
    wins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'CLOSED'")
    total_closed = cur.fetchone()[0]
    win_rate = round((wins / total_closed * 100), 2) if total_closed > 0 else 0

    cur.execute(
        "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades WHERE status = 'CLOSED' AND closed_at > datetime('now', '-1 day')"
    )
    today_pnl = cur.fetchone()[0]

    conn.close()

    return {
        "total_signals": total_signals,
        "today_signals": today_signals,
        "pending_signals": pending_signals,
        "open_trades": open_trades,
        "total_pnl": round(total_pnl, 2),
        "today_pnl": round(today_pnl, 2),
        "win_rate": win_rate,
        "total_closed_trades": total_closed,
    }


@app.get("/api/performance/{agent_name}")
def get_performance(agent_name: str):
    try:
        from src.database.db_manager import DatabaseManager

        db = DatabaseManager()
        return db.get_agent_performance(agent_name)
    except Exception as e:
        return {"error": str(e)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/")
async def root():
    return FileResponse("dashboard.html")


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("Financial Agent Dashboard API")
    print("访问 http://localhost:8000 查看面板")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)