"""账号体系：注册 / 登录 / 登出。

口令用 PBKDF2-HMAC-SHA256 + 随机盐散列；登录后签发随机 token 放 HttpOnly
cookie。未登录仍可匿名使用，登录时把本浏览器「未归属」的匿名会话认领到账号，
换设备登录也能看到自己的历史。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

from config.settings import BASE_DIR

DB_PATH = BASE_DIR / "data" / "auth.sqlite"
TOKEN_DAYS = 30
_ITERATIONS = 120_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{3,32}$")

_lock = threading.RLock()
_ready: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
"""


def _now() -> datetime:
    return datetime.now()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    key = str(DB_PATH)
    if key not in _ready:
        conn.executescript(_SCHEMA)
        _ready.add(key)
    return conn


def reset_for_tests() -> None:
    with _lock:
        _ready.clear()


def _hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def _verify(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   bytes.fromhex(salt), int(iterations)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc, digest)


def _check(username: str, password: str) -> None:
    if not _USERNAME_RE.match(username or ""):
        raise ValueError("用户名需为 3-32 位字母、数字、下划线或中文")
    if not (6 <= len(password or "") <= 64):
        raise ValueError("密码长度需为 6-64 位")


def _public(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}


def register(username: str, password: str) -> dict:
    username = (username or "").strip()
    _check(username, password)
    with _lock, _conn() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            raise ValueError("用户名已存在")
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
            (username, _hash(password), _now().isoformat(timespec="seconds")),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return _public(row)


def authenticate(username: str, password: str) -> dict:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?",
                           ((username or "").strip(),)).fetchone()
    if row is None or not _verify(password or "", row["password_hash"]):
        raise ValueError("用户名或密码错误")
    return _public(row)


def create_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO tokens(token, user_id, created_at, expires_at) VALUES(?,?,?,?)",
            (token, user_id, now.isoformat(timespec="seconds"),
             (now + timedelta(days=TOKEN_DAYS)).isoformat(timespec="seconds")),
        )
    return token


def user_by_token(token: str) -> dict | None:
    if not token:
        return None
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id "
            "WHERE t.token=? AND t.expires_at > ?",
            (token, _now().isoformat(timespec="seconds")),
        ).fetchone()
    return _public(row) if row else None


def delete_token(token: str) -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM tokens WHERE token=?", (token or "",))


def purge_expired() -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM tokens WHERE expires_at <= ?",
                     (_now().isoformat(timespec="seconds"),))
