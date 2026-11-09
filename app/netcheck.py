"""联网可用性快速探测（为长时间离线场景准备）。

问题：配置了 DEEPSEEK_API_KEY 时，断网后每次问答都要等 LLM_TIMEOUT（60s）
才降级；点「同步」也会在多次重试上耗很久。
做法：用一次带短超时的 TCP 连接探测目标主机（带缓存），判定离线后立刻走本地能力。

可用环境变量 `APP_OFFLINE=1` 显式声明离线，跳过所有探测（最快、最确定）。
"""
from __future__ import annotations

import os
import socket
import threading
import time
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 1.2
CACHE_TTL = 45.0

_cache: dict[tuple[str, int], tuple[float, bool]] = {}
_lock = threading.Lock()


def forced_offline() -> bool:
    return os.getenv("APP_OFFLINE", "0").strip().lower() in ("1", "true", "yes", "on")


def reachable(host: str, port: int = 443, timeout: float = DEFAULT_TIMEOUT,
              ttl: float = CACHE_TTL, force: bool = False) -> bool:
    if forced_offline():
        return False
    key = (host, port)
    now = time.monotonic()
    with _lock:
        ent = _cache.get(key)
        if not force and ent and now - ent[0] < ttl:
            return ent[1]
    ok = False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok = True
    except OSError:
        ok = False
    with _lock:
        _cache[key] = (now, ok)
    return ok


def url_reachable(url: str, force: bool = False) -> bool:
    u = urlparse(url)
    host = u.hostname or ""
    if not host:
        return False
    port = u.port or (443 if u.scheme == "https" else 80)
    return reachable(host, port, force=force)


def llm_reachable(base_url: str, force: bool = False) -> bool:
    return url_reachable(base_url, force=force)


def mall_reachable(force: bool = False) -> bool:
    return reachable("mall.dahuatech.com", 443, force=force)
