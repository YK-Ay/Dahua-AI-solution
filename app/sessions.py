"""会话索引与产出版本：跨进程持久化 Agent 会话，供历史列表 / 续聊 / 版本回溯。

LangGraph checkpoint 只存图状态，无法按用户列出会话，也存不下标题、产物归属。
这里用独立 SQLite（data/sessions.sqlite）补一层索引，不改图、不改 checkpoint 结构：

  sessions  会话主表（归属 client_id / user_id、标题、状态、Spec 快照）
  versions  每次生成交付物的版本快照（V1 / V2 …，含 Spec 与文件清单）
  exports   导出文件归属（/api/export 鉴权用）

产物文件仍落在 data/exports，这里只记录归属，不复制文件。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from config.settings import BASE_DIR

DB_PATH = BASE_DIR / "data" / "sessions.sqlite"

_lock = threading.RLock()
_ready: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id    TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    user_id      TEXT,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL,
    stage        TEXT NOT NULL DEFAULT '需求整理',
    text         TEXT NOT NULL DEFAULT '',
    lanes        INTEGER,
    retention_days INTEGER,
    spec_json    TEXT,
    files_json   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(client_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL,
    version_no   INTEGER NOT NULL,
    title        TEXT NOT NULL,
    spec_json    TEXT,
    files_json   TEXT,
    summary_json TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE(thread_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_versions_thread ON versions(thread_id, version_no DESC);

CREATE TABLE IF NOT EXISTS exports (
    filename   TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    user_id    TEXT,
    thread_id  TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    key = str(DB_PATH)
    if key not in _ready:
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)
        _ready.add(key)
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """老库迁移：exports.user_id 是 P1 新增列，缺了就补上。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(exports)")}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE exports ADD COLUMN user_id TEXT")
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "stage" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN stage TEXT NOT NULL DEFAULT '需求整理'")


def stage_of(data: dict) -> str:
    status = (data or {}).get("status") or ""
    if status == "done":
        return "方案已生成"
    if status == "awaiting_input":
        return "等待人工审阅"
    if status == "aborted":
        return "已中止"
    return "方案设计中"


def reset_for_tests() -> None:
    """测试切换 DB 路径后清掉建表缓存。"""
    with _lock:
        _ready.clear()


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _title_of(spec: dict, text: str) -> str:
    name = ((spec or {}).get("project") or {}).get("name")
    if name:
        return str(name).strip()[:60]
    flat = " ".join((text or "").split())
    if not flat:
        return "未命名方案"
    return flat[:24] + ("…" if len(flat) > 24 else "")


def _lanes_of(spec: dict) -> int:
    return sum(int(r.get("lanes") or 0) for r in (spec or {}).get("requirements") or [])


def _summary_of(spec: dict, files: dict) -> dict:
    devices = (spec or {}).get("devices") or []
    total_price = sum(
        float(d.get("unit_price") or 0) * int(d.get("qty") or 0) for d in devices
    )
    return {
        "lanes": _lanes_of(spec),
        "retention_days": ((spec or {}).get("storage") or {}).get("retention_days"),
        "devices": len(devices),
        "qty": sum(int(d.get("qty") or 0) for d in devices),
        "price": round(total_price, 2),
        "files": sorted(files.keys()),
    }


def _register_exports(conn: sqlite3.Connection, client_id: str, thread_id: str | None,
                      files: dict, user_id: str | None = None) -> None:
    now = _now()
    for item in (files or {}).values():
        name = (item or {}).get("name")
        if not name:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO exports(filename, client_id, user_id, thread_id, created_at) "
            "VALUES(?,?,?,?,?)",
            (name, client_id, user_id, thread_id, now),
        )


def _owner_clause(user_id: str | None) -> tuple[str, list]:
    """归属条件：匿名按 client_id，登录后 client_id 或 user_id 任一匹配即可。"""
    if user_id:
        return "(client_id=? OR user_id=?)", [user_id]
    return "client_id=?", []


def _record_version(conn: sqlite3.Connection, thread_id: str, title: str,
                    spec: dict, files: dict) -> dict | None:
    """同一份产物（文件清单完全相同）不重复记版本；审阅后重新生成才 +1。"""
    files_json = _dumps(files)
    last = conn.execute(
        "SELECT version_no, files_json FROM versions WHERE thread_id=? "
        "ORDER BY version_no DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    if last and last["files_json"] == files_json:
        return None
    no = int(last["version_no"]) + 1 if last else 1
    summary = _summary_of(spec, files)
    created = _now()
    conn.execute(
        "INSERT INTO versions(thread_id, version_no, title, spec_json, files_json, "
        "summary_json, created_at) VALUES(?,?,?,?,?,?,?)",
        (thread_id, no, title, _dumps(spec or {}), files_json, _dumps(summary), created),
    )
    return {"version_no": no, "title": title, "files": files, "summary": summary,
            "created_at": created}


def record(client_id: str, data: dict, touch: bool = True,
           user_id: int | str | None = None, version: bool = True) -> dict | None:
    """把一次 start / resume / state 的结果写进索引（别人的会话不覆盖）。

    touch=False 用于只读刷新（如 history 里点开某个会话），不改变排序时间。
    user_id 登录后传入：同一个账号换浏览器也能续上自己的会话。
    version=False 用于 PDF 这类「不改变方案内容」的派生导出，避免凭空多一个版本。
    """
    thread_id = (data or {}).get("thread_id")
    if not client_id or not thread_id:
        return None
    uid = str(user_id) if user_id not in (None, "") else None
    spec = data.get("spec") or {}
    files = data.get("files") or {}
    title = _title_of(spec, data.get("text") or "")
    lanes = _lanes_of(spec) or None
    days = (spec.get("storage") or {}).get("retention_days")
    now = _now()
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT client_id, user_id, title FROM sessions WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO sessions(thread_id, client_id, user_id, title, status, stage, text, "
                "lanes, retention_days, spec_json, files_json, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id, client_id, uid, title, data.get("status") or "running", stage_of(data),
                 data.get("text") or "", lanes, days, _dumps(spec), _dumps(files), now, now),
            )
            title_now = title
        elif row["client_id"] == client_id or (uid and str(row["user_id"] or "") == uid):
            sets = ("title=?, status=?, stage=?, text=?, lanes=?, retention_days=?, "
                    "spec_json=?, files_json=?, user_id=COALESCE(user_id, ?)")
            params: list = [row["title"], data.get("status") or "running", stage_of(data),
                            data.get("text") or "", lanes, days,
                            _dumps(spec), _dumps(files), uid]
            if touch:
                sets += ", updated_at=?"
                params.append(now)
            params.append(thread_id)
            conn.execute(f"UPDATE sessions SET {sets} WHERE thread_id=?", params)
            title_now = row["title"]
        else:
            return None
        _register_exports(conn, client_id, thread_id, files, uid)
        made = None
        if version and data.get("status") == "done" and files:
            made = _record_version(conn, thread_id, title_now, spec, files)
    return made


def owner_of(thread_id: str) -> dict | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT client_id, user_id FROM sessions WHERE thread_id=?", (thread_id,)
        ).fetchone()
    return {"client_id": row["client_id"], "user_id": row["user_id"]} if row else None


def _uid_str(user_id: int | str | None) -> str | None:
    return str(user_id) if user_id not in (None, "") else None


def get_session(client_id: str, thread_id: str,
                user_id: int | str | None = None) -> dict | None:
    uid = _uid_str(user_id)
    where = "(client_id=? OR user_id=?)" if uid else "client_id=?"
    params: list = [thread_id, client_id] + ([uid] if uid else [])
    with _lock, _conn() as conn:
        row = conn.execute(
            f"SELECT * FROM sessions WHERE thread_id=? AND {where}", params
        ).fetchone()
        if row is None:
            return None
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM versions WHERE thread_id=?", (thread_id,)
        ).fetchone()["c"]
    item = _session_item(row)
    item["version_count"] = count
    item["spec"] = _loads(row["spec_json"], {})
    item["files"] = _loads(row["files_json"], {})
    return item


def _session_item(row: sqlite3.Row) -> dict:
    return {
        "thread_id": row["thread_id"],
        "title": row["title"],
        "status": row["status"],
        "stage": row["stage"] if "stage" in row.keys() else "需求整理",
        "lanes": row["lanes"],
        "retention_days": row["retention_days"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_sessions(client_id: str, limit: int = 50, offset: int = 0,
                  user_id: int | str | None = None) -> dict:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    uid = _uid_str(user_id)
    where = "(s.client_id=? OR s.user_id=?)" if uid else "s.client_id=?"
    params: list = [client_id] + ([uid] if uid else [])
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM versions v WHERE v.thread_id=s.thread_id) "
            f"AS version_count FROM sessions s WHERE {where} "
            "ORDER BY s.updated_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM sessions s WHERE {where}", params
        ).fetchone()["c"]
    items = []
    for r in rows:
        item = _session_item(r)
        item["version_count"] = r["version_count"]
        items.append(item)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def rename_session(client_id: str, thread_id: str, title: str,
                   user_id: int | str | None = None) -> bool:
    title = " ".join((title or "").split())[:60]
    if not title:
        raise ValueError("标题不能为空")
    uid = _uid_str(user_id)
    where = "(client_id=? OR user_id=?)" if uid else "client_id=?"
    params: list = [title, _now(), thread_id, client_id] + ([uid] if uid else [])
    with _lock, _conn() as conn:
        cur = conn.execute(
            f"UPDATE sessions SET title=?, updated_at=? WHERE thread_id=? AND {where}",
            params,
        )
        if cur.rowcount:
            conn.execute("UPDATE versions SET title=? WHERE thread_id=?", (title, thread_id))
        return bool(cur.rowcount)


def set_stage(client_id: str, thread_id: str, stage: str,
              user_id: int | str | None = None) -> bool:
    """更新项目阶段，不改动方案内容和版本。"""
    stage = " ".join((stage or "").split())[:30]
    if not stage:
        raise ValueError("阶段不能为空")
    uid = _uid_str(user_id)
    where = "(client_id=? OR user_id=?)" if uid else "client_id=?"
    params: list = [stage, _now(), thread_id, client_id] + ([uid] if uid else [])
    with _lock, _conn() as conn:
        cur = conn.execute(
            f"UPDATE sessions SET stage=?, updated_at=? WHERE thread_id=? AND {where}",
            params,
        )
        return bool(cur.rowcount)


def delete_session(client_id: str, thread_id: str,
                   user_id: int | str | None = None) -> dict | None:
    """删索引 + 归属校验；返回待删除的物理文件名，由 API 层负责 unlink。"""
    uid = _uid_str(user_id)
    where = "(client_id=? OR user_id=?)" if uid else "client_id=?"
    params: list = [thread_id, client_id] + ([uid] if uid else [])
    with _lock, _conn() as conn:
        row = conn.execute(
            f"SELECT files_json FROM sessions WHERE thread_id=? AND {where}", params
        ).fetchone()
        if row is None:
            return None
        files = _loads(row["files_json"], {})
        names = [(f or {}).get("name") for f in files.values() if (f or {}).get("name")]
        # 版本产出的文件也要算进来（历史版本文件同样归该会话所有）
        for vr in conn.execute(
            "SELECT files_json FROM versions WHERE thread_id=?", (thread_id,)
        ).fetchall():
            for item in (_loads(vr["files_json"], {}) or {}).values():
                name = (item or {}).get("name")
                if name and name not in names:
                    names.append(name)
        conn.execute("DELETE FROM exports WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM versions WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM sessions WHERE thread_id=?", (thread_id,))
    return {"thread_id": thread_id, "files": names}


def list_versions(client_id: str, thread_id: str,
                  user_id: int | str | None = None) -> list[dict] | None:
    uid = _uid_str(user_id)
    where = "(client_id=? OR user_id=?)" if uid else "client_id=?"
    params: list = [thread_id, client_id] + ([uid] if uid else [])
    with _lock, _conn() as conn:
        owner = conn.execute(
            f"SELECT client_id FROM sessions WHERE thread_id=? AND {where}", params
        ).fetchone()
        if owner is None:
            return None
        rows = conn.execute(
            "SELECT * FROM versions WHERE thread_id=? ORDER BY version_no DESC",
            (thread_id,),
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "version_no": r["version_no"],
            "title": r["title"],
            "files": _loads(r["files_json"], {}),
            "summary": _loads(r["summary_json"], {}),
            "created_at": r["created_at"],
        })
    return items


# ---------------------------------------------------------------------------
# 导出鉴权：文件名 → 归属
# ---------------------------------------------------------------------------

def register_exports(client_id: str, thread_id: str | None, names: list[str],
                     user_id: int | str | None = None) -> None:
    if not client_id or not names:
        return
    uid = _uid_str(user_id)
    with _lock, _conn() as conn:
        now = _now()
        for name in names:
            if not name:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO exports(filename, client_id, user_id, thread_id, "
                "created_at) VALUES(?,?,?,?,?)",
                (name, client_id, uid, thread_id, now),
            )


def owns_file(client_id: str, filename: str, user_id: int | str | None = None) -> bool:
    uid = _uid_str(user_id)
    if not client_id and not uid:
        return False
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT client_id, user_id FROM exports WHERE filename=?", (filename,)
        ).fetchone()
    if row is None:
        return False
    return row["client_id"] == client_id or bool(uid) and row["user_id"] == uid


def claim(client_id: str, user_id: int | str | None) -> dict:
    """登录时把本浏览器尚未归属的匿名会话 / 导出记录认领到账号。"""
    uid = _uid_str(user_id)
    if not client_id or not uid:
        return {"sessions": 0, "exports": 0}
    with _lock, _conn() as conn:
        s = conn.execute(
            "UPDATE sessions SET user_id=? WHERE client_id=? AND user_id IS NULL",
            (uid, client_id),
        )
        e = conn.execute(
            "UPDATE exports SET user_id=? WHERE client_id=? AND user_id IS NULL",
            (uid, client_id),
        )
    return {"sessions": int(s.rowcount or 0), "exports": int(e.rowcount or 0)}
