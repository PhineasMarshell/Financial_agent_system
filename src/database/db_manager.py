import sqlite3
import json
import threading
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from contextlib import contextmanager

class DatabaseManager:
    """
    金融 Agent 系统的 SQLite 数据库管理器
    三大数据库 signals(信号) / trades(交易) / price_snapshots(价格快照)
    线程安全：通过 _write_lock 保护所有写操作，支持多线程并发
    """

    def __init__(self, db_path: str = "financial_agent_db.db"):
        self.db_path = db_path
        # 用可重入锁，防止将来嵌套写调用时死锁
        self._write_lock = threading.RLock()
        self._init_tables()

    # ========== 连接管理 ==========
    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    
    # ========== 上下文管理器（读操作无锁，写操作加锁）==========
    @contextmanager
    def _transaction(self, write: bool = False):
        """
        数据库事务上下文管理器
        write=True 时获取写锁，防止多线程并发写入导致 database is locked
        """
        if write:
            self._write_lock.acquire()
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            if write:
                self._write_lock.release()
    
    # ========== 建表 ==========
    def _init_tables(self):
        with self._transaction(write=True) as conn:
            cursor = conn.cursor()

            # 1. 信号表：记录每个 Agent 对新闻的分析结果
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,    -- 自增主键，唯一标识这条信号
                    event_id TEXT NOT NULL,                   -- 关联上游新闻ID（如 jin10:123），知道这条信号从哪条新闻来的
                    agent_name TEXT NOT NULL,                 -- 哪个Agent产生的（gold/stocks/crypto/futures），方便分类统计
                    source TEXT,                              -- 新闻来源（jin10/新浪/WSJ），追溯原始出处
                    content_snippet TEXT,                     -- 新闻内容前200字摘要，复盘时不用查原始消息
                    signal TEXT NOT NULL,                     -- 交易方向：STRONG_BULLISH/BULLISH/NEUTRAL/BEARISH/STRONG_BEARISH
                    confidence INTEGER CHECK(confidence BETWEEN 0 AND 10),  -- 置信度0-10，0表示无关联，CHECK约束防止乱填
                    timeframe TEXT CHECK(timeframe IN ('SCALP','INTRADAY','SWING','TREND')),  -- 时间维度：超短线/日内/波段/趋势
                    entry_price REAL,                         -- Agent建议的入场价格（如2350.50）
                    stop_loss REAL,                           -- 建议止损价
                    take_profit REAL,                         -- 建议目标价
                    position_size TEXT,                       -- 仓位建议：轻仓试探/标准仓位/重仓出击/观望
                    key_drivers TEXT,                         -- 核心驱动因素（JSON数组），如["美联储降息","中东局势"]
                    risk_factors TEXT,                        -- 风险因素（JSON数组），如["美元反弹"]
                    reasoning TEXT,                           -- Router的推理过程文字，复盘时看为什么判断这个方向
                    tickers TEXT,                             -- 关联的标的代码（JSON数组），如["PAXG","XAU"]
                    tools_used TEXT,                          -- 调用了哪些工具（JSON数组），如["hyperliquid_query","search_news"]
                    raw_report TEXT,                          -- Agent完整原始输出（Markdown格式），保留现场
                    latency_ms INTEGER,                       -- Agent分析耗时（毫秒），监控性能
                    execution_status TEXT DEFAULT 'PENDING',  -- 执行状态
                    execution_reason TEXT,                     -- 执行状态原因
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- 自动生成时间戳，记录信号产生时间
                )
            """)

            # 2. 交易表：记录执行Agent实际下单和后续跟踪
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,     -- 自增主键，唯一标识这笔交易
                    signal_id INTEGER,                        -- 外键关联signals.id，知道这笔交易对应哪条信号
                    event_id TEXT,                            -- 冗余存储新闻ID，查交易时不用JOIN也能知道来源
                    agent_name TEXT,                          -- 哪个Agent产生的信号（gold/stocks等）
                    ticker TEXT,                              -- 实际交易的标的代码（如PAXG）
                    side TEXT CHECK(side IN ('BUY','SELL')),  -- 方向：买入或做空（CHECK约束只允许这两个值）
                    signal_price REAL,                        -- Agent建议的入场价（和signals.entry_price一致，冗余防丢）
                    executed_price REAL,                      -- 实际成交价格（可能和signal_price有滑点差异）
                    executed_qty REAL,                        -- 实际成交数量/仓位大小
                    stop_loss REAL,                           -- 实际设置的止损价（执行时可能微调）
                    take_profit REAL,                         -- 实际设置的目标价
                    status TEXT CHECK(status IN ('PENDING','OPEN','CLOSED','CANCELLED','EXPIRED')) DEFAULT 'PENDING',
                                                            -- 交易状态：待执行/持仓中/已平仓/已取消/已过期
                    is_paper_trade BOOLEAN DEFAULT 1,         -- 是否为模拟盘（1=模拟，0=实盘），默认先模拟
                    opened_at TIMESTAMP,                      -- 实际开仓时间（执行Agent下单成功时写入）
                    closed_at TIMESTAMP,                      -- 平仓时间（触发止损/止盈/手动平仓时写入）
                    close_price REAL,                         -- 平仓成交价格
                    close_reason TEXT CHECK(close_reason IN ('STOP_LOSS','TAKE_PROFIT','MANUAL','EXPIRED','SIGNAL_REVERSE')),
                                                            -- 平仓原因：止损/止盈/手动/过期/信号反转
                    realized_pnl REAL,                        -- 已实现盈亏（绝对金额，如+150.50或-80.00）
                    realized_pnl_pct REAL,                    -- 已实现盈亏率（百分比，如+2.15%或-1.30%）
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 记录创建时间
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,    -- 最后更新时间（状态变化时更新）
                    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE SET NULL
                                                            -- 外键约束：信号被删了，trade.signal_id设为NULL（保留交易记录）
                )
            """)

            # 3. 价格快照表：记录持仓期间的实时价格，用于跟踪浮动盈亏
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,     -- 自增主键
                    trade_id INTEGER NOT NULL,                -- 外键关联trades.id，知道这个价格属于哪笔持仓
                    ticker TEXT NOT NULL,                     -- 标的代码（如PAXG）
                    price REAL NOT NULL,                      -- 当时的实时价格（从Hyperliquid查的）
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 记录这条价格的时间点
                    unrealized_pnl REAL,                    -- 当时这笔持仓的浮动盈亏（未平仓时的账面盈亏）
                    unrealized_pnl_pct REAL,                  -- 当时浮动盈亏率（%）
                    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
                                                            -- 外键约束：交易被删了，对应的价格快照也自动删（省空间）
                )
            """)

            # 索引：加速查询
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_event ON signals(event_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_agent ON signals(agent_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_signal ON trades(signal_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_trade ON price_snapshots(trade_id)")

    # ========== Signals 表操作 ==========
    def insert_signal(self, data: Dict[str, Any]) -> int:
        """
        插入分析信号
        返回: 新插入记录的 ID
        """
        with self._transaction(write=True) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO signals (
                    event_id, agent_name, source, content_snippet,
                    signal, confidence, timeframe, entry_price, stop_loss, take_profit,
                    position_size, key_drivers, risk_factors, reasoning,
                    tickers, tools_used, raw_report, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("event_id"),
                data.get("agent_name"),
                data.get("source"),
                data.get("content_snippet"),
                data.get("signal"),
                data.get("confidence"),
                data.get("timeframe"),
                data.get("entry_price"),
                data.get("stop_loss"),
                data.get("take_profit"),
                data.get("position_size"),
                json.dumps(data.get("key_drivers", []), ensure_ascii=False),
                json.dumps(data.get("risk_factors", []), ensure_ascii=False),
                data.get("reasoning"),
                json.dumps(data.get("tickers", []), ensure_ascii=False),
                json.dumps(data.get("tools_used", []), ensure_ascii=False),
                data.get("raw_report"),
                data.get("latency_ms")
            ))
            return cursor.lastrowid
        
    def update_signal_status(self, signal_id: int, status: str, reason: str = ""):
        """
        更新信号的执行状态
        status: PENDING / EXECUTED / SKIPPED / FAILED
        reason: 跳过或失败的原因
        """
        with self._transaction(write=True) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE signals
                SET execution_status = ?, execution_reason = ? 
                WHERE id = ?
            """, (status, reason, signal_id))
    
    def get_signal_by_id(self, signal_id: int) -> Optional[Dict]:
        """根据 ID 查询单条信号"""
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
            row = cursor.fetchone()
            return self._parse_signal_row(row) if row else None

    def get_signals_by_agent(self, agent_name: str, limit: int = 100) -> List[Dict]:
        """查询某个 Agent 的最近 N 条信号"""
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM signals 
                WHERE agent_name = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (agent_name, limit))
            return [self._parse_signal_row(row) for row in cursor.fetchall()]

    def get_signals_by_event(self, event_id: str) -> List[Dict]:
        """查询同一事件的所有信号（多个 Agent 可能都分析了这条新闻）"""
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM signals WHERE event_id = ?", (event_id,))
            return [self._parse_signal_row(row) for row in cursor.fetchall()]

    def get_recent_signals(self, hours: int = 24) -> List[Dict]:
        """查询最近 N 小时的所有信号"""
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM signals 
                WHERE created_at > datetime('now', ?)
                ORDER BY created_at DESC
            """, (f'-{hours} hours',))
            return [self._parse_signal_row(row) for row in cursor.fetchall()]
            
    def _parse_signal_row(self, row: sqlite3.Row) -> Dict:
        """把 signals 表的 Row 解析为字典，自动反序列化 JSON"""
        d = dict(row)
        for field in ["key_drivers", "risk_factors", "tickers", "tools_used"]:
            d[field] = json.loads(d.get(field) or "[]")
        return d
    
    # ========== Trades 表操作 ==========
    def insert_trade(self, data: Dict[str, Any]) -> int:
        """插入交易记录"""
        with self._transaction(write=True) as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO trades (
                    signal_id, event_id, agent_name, ticker, side,
                    signal_price, executed_price, executed_qty,
                    stop_loss, take_profit, status, is_paper_trade, opened_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("signal_id"),
                data.get("event_id"),
                data.get("agent_name"),
                data.get("ticker"),
                data.get("side", "BUY"),
                data.get("signal_price"),
                data.get("executed_price"),
                data.get("executed_qty"),
                data.get("stop_loss"),
                data.get("take_profit"),
                data.get("status", "PENDING"),
                data.get("is_paper_trade", True),
                data.get("opened_at") or now
            ))
            return cursor.lastrowid

    def update_trade_status(self, trade_id: int, status: str, **kwargs):
        """更新交易状态（开仓、平仓、取消等）"""
        allowed_fields = ["status", "executed_price", "executed_qty",
                         "closed_at", "close_price", "close_reason",
                         "realized_pnl", "realized_pnl_pct", "updated_at"]

        fields = ["status = ?"]
        values = [status]

        for key, val in kwargs.items():
            if key in allowed_fields:
                fields.append(f"{key} = ?")
                values.append(val)

        # 自动更新 updated_at 为 UTC 时间
        if "updated_at" not in kwargs:
            fields.append("updated_at = ?")
            values.append(datetime.now(timezone.utc).isoformat())

        values.append(trade_id)
        
        with self._transaction(write=True) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE trades SET {', '.join(fields)}
                WHERE id = ?
            """, values)

    def get_open_trades(self, agent_name: Optional[str] = None) -> List[Dict]:
        """查询所有未平仓的交易"""
        with self._transaction() as conn:
            cursor = conn.cursor()
            if agent_name:
                cursor.execute("""
                    SELECT * FROM trades 
                    WHERE status = 'OPEN' AND agent_name = ?
                """, (agent_name,))
            else:
                cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
            return [dict(row) for row in cursor.fetchall()]

    def get_trade_by_signal(self, signal_id: int) -> Optional[Dict]:
        """根据信号 ID 查询对应的交易"""
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE signal_id = ?", (signal_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # ========== Price Snapshots 表操作 ==========
    def insert_snapshot(self, data: Dict[str, Any]) -> int:
        """插入价格快照"""
        with self._transaction(write=True) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO price_snapshots (
                    trade_id, ticker, price, timestamp, unrealized_pnl, unrealized_pnl_pct
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                data["trade_id"],
                data["ticker"],
                data["price"],
                data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                data.get("unrealized_pnl"),
                data.get("unrealized_pnl_pct")
            ))
            return cursor.lastrowid

    def get_snapshots_by_trade(self, trade_id: int, limit: int = 100) -> List[Dict]:
        """查询某笔交易的所有价格快照"""
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM price_snapshots 
                WHERE trade_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (trade_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    # ========== 统计查询（方便可视化） ==========
    def get_agent_performance(self, agent_name: str) -> Dict:
        """
        统计某个 Agent 的历史表现
        返回: 总信号数、平均置信度、已平仓交易数、总盈亏、胜率
        """
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            # 信号统计
            cursor.execute("""
                SELECT COUNT(*), AVG(confidence) 
                FROM signals WHERE agent_name = ?
            """, (agent_name,))
            sig_count, avg_conf = cursor.fetchone()

            # 交易统计
            cursor.execute("""
                SELECT COUNT(*), SUM(realized_pnl), AVG(realized_pnl)
                FROM trades 
                WHERE agent_name = ? AND status = 'CLOSED' AND realized_pnl IS NOT NULL
            """, (agent_name,))
            trade_count, total_pnl, avg_pnl = cursor.fetchone()

            # 胜率（盈利交易 / 总平仓交易）
            cursor.execute("""
                SELECT COUNT(*) FROM trades 
                WHERE agent_name = ? AND status = 'CLOSED' AND realized_pnl > 0
            """, (agent_name,))
            win_count = cursor.fetchone()[0]

            win_rate = (win_count / trade_count * 100) if trade_count else 0

            return {
                "agent_name": agent_name,
                "total_signals": sig_count or 0,
                "avg_confidence": round(avg_conf or 0, 2),
                "closed_trades": trade_count or 0,
                "total_pnl": round(total_pnl or 0, 2),
                "avg_pnl_per_trade": round(avg_pnl or 0, 2),
                "win_rate_pct": round(win_rate, 2)
            }

    def get_unprocessed_signals(self, agent_name: str, limit: int = 10) -> List[Dict]:
        """
        查询某个 Agent 还没有对应交易的、且状态为 PENDING 的信号（供执行 Agent 消费）
        只捞 PENDING 状态，避免 SKIPPED/FAILED 的信号被反复重试
        """
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.* FROM signals s
                LEFT JOIN trades t ON s.id = t.signal_id
                WHERE s.agent_name = ? AND t.id IS NULL AND s.execution_status = 'PENDING'
                ORDER BY s.created_at DESC
                LIMIT ?
            """, (agent_name, limit))
            return [self._parse_signal_row(row) for row in cursor.fetchall()]