"""极简埋点：本地 SQLite 记录关键动作，用于工作台展示使用情况。

只记事件名、会话号、客户端号与少量元数据；不记需求原文，避免把客户信息
重复落盘。写入失败一律吞掉，埋点不能影响主流程。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime

from config.settings import BASE_DIR

DB_PATH = BASE_DIR / "data" / "metrics.sqlite"

_lock = threading.RLock()
_ready: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,
    client_id  TEXT,
    thread_id  TEXT,
    meta_json  TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(event, created_at);
"""

# 参与"完成率"统计的事件
START = "start"
APPROVE = "approve"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    key = str(DB_PATH)
    if key not in _ready:
        conn.executescript(_SCHEMA)
        _ready.add(key)
    return conn


def reset_for_tests() -> None:
    with _lock:
        _ready.clear()


def record(event: str, client_id: str = "", thread_id: str | None = None, **meta) -> None:
    try:
        with _lock, _conn() as conn:
            conn.execute(
                "INSERT INTO events(event, client_id, thread_id, meta_json, created_at) "
                "VALUES(?,?,?,?,?)",
                (event, client_id or "", thread_id,
                 json.dumps(meta, ensure_ascii=False) if meta else None,
                 datetime.now().isoformat(timespec="seconds")),
            )
    except sqlite3.Error:
        pass


def overview(client_id: str | None = None) -> dict:
    today = date.today().isoformat()
    where = "WHERE client_id=? AND " if client_id else "WHERE "
    params: list = [client_id] if client_id else []
    try:
        conn = _conn()
    except sqlite3.Error:
        return {"today_start": 0, "today_done": 0, "today_export": 0,
                "total_start": 0, "total_done": 0, "done_rate": 0}
    try:
        with _lock:
            def count(event: str, day_only: bool) -> int:
                sql = f"SELECT COUNT(*) FROM events {where}event=?"
                args = params + [event]
                if day_only:
                    sql += " AND created_at LIKE ?"
                    args.append(today + "%")
                return int(conn.execute(sql, args).fetchone()[0])

            def distinct_threads(event: str, day_only: bool = False) -> int:
                sql = (f"SELECT COUNT(DISTINCT thread_id) FROM events {where}event=? "
                       "AND thread_id IS NOT NULL")
                args = params + [event]
                if day_only:
                    sql += " AND created_at LIKE ?"
                    args.append(today + "%")
                return int(conn.execute(sql, args).fetchone()[0])

            total_start = distinct_threads(START)
            total_done = distinct_threads(APPROVE)
            return {
                "today_start": distinct_threads(START, True),
                "today_done": distinct_threads(APPROVE, True),
                "today_export": count("export", True),
                "total_start": total_start,
                "total_done": total_done,
                "done_rate": round(total_done / total_start, 3) if total_start else 0,
            }
    finally:
        conn.close()
