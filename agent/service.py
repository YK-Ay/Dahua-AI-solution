"""Web 层薄封装：把 LangGraph 的 checkpoint / interrupt 变成三个 JSON 动作。

图本身不改（仍只有 build / ask_gap / review / finish / abort 五个节点），
这一层只负责：
  - 全局单例 graph + 同一把锁（SqliteSaver 跨线程复用）
  - start / state / resume 三个动作的状态序列化
  - 把 `__interrupt__` / StateSnapshot.tasks[].interrupts 统一成前端好读的结构
  - 把产物绝对路径转成 /api/export/ 下载 URL
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from urllib.parse import quote

from langgraph.types import Command

from agent import graph as G

_lock = threading.RLock()
_graph = None


def get_graph():
    global _graph
    with _lock:
        if _graph is None:
            _graph = G.build_graph()
        return _graph


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _values_of(state) -> dict:
    """invoke() 返回 dict，get_state() 返回 StateSnapshot；dict 自带 .values 方法，
    不能直接用 getattr(state, "values", state)，否则会拿到 builtin method。"""
    if isinstance(state, dict):
        return dict(state)
    return dict(getattr(state, "values", None) or {})


def _interrupt_payloads(state) -> list[dict]:
    out: list[dict] = []
    values = _values_of(state)
    for it in values.get("__interrupt__") or ():
        v = getattr(it, "value", it)
        out.append(v if isinstance(v, dict) else {"payload": str(v)})
    if not out:
        for task in getattr(state, "tasks", ()) or ():
            for it in getattr(task, "interrupts", ()) or ():
                v = getattr(it, "value", it)
                out.append(v if isinstance(v, dict) else {"payload": str(v)})
    return out


def _files(outputs: dict) -> dict:
    files = {}
    for kind, path in (outputs or {}).items():
        name = Path(str(path)).name
        url = "/api/export/" + quote(name)
        if kind == "png" or kind == "png_detail":
            url += "?inline=1"
        files[kind] = {"name": name, "url": url}
    return files


def serialize(state, thread_id: str) -> dict:
    values = _values_of(state)
    interrupts = _interrupt_payloads(state)
    outputs = values.get("outputs") or {}
    if interrupts:
        status = "awaiting_input"
    elif outputs:
        status = "done"
    elif "outputs" in values and not outputs:
        status = "aborted"
    else:
        status = "running"
    return {
        "thread_id": thread_id,
        "status": status,
        "interrupt": interrupts[0] if interrupts else None,
        "interrupt_type": (interrupts[0].get("type") if interrupts else None),
        "spec": values.get("spec"),
        "gaps": values.get("gaps") or [],
        "gate_gaps": values.get("gate_gaps") or [],
        "rounds": values.get("rounds") or 0,
        "decision": values.get("decision"),
        "parser": values.get("parser") or {},
        "research": values.get("research") or {},
        "text": values.get("text") or "",
        "files": _files(outputs),
    }


def start(text: str, name: str | None = None, lanes: int | None = None,
          retention_days: int | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("请先填写需求描述")
    thread_id = "a-" + uuid.uuid4().hex[:12]
    init = {"text": text, "rounds": 0}
    if name:
        init["name"] = name
    if lanes is not None:
        init["lanes"] = lanes
    if retention_days is not None:
        init["retention_days"] = retention_days
    with _lock:
        state = get_graph().invoke(init, _cfg(thread_id))
    return serialize(state, thread_id)


def state_of(thread_id: str) -> dict:
    with _lock:
        snap = get_graph().get_state(_cfg(thread_id))
    values = getattr(snap, "values", None) or {}
    if not values:
        raise KeyError("会话不存在或已过期")
    return serialize(snap, thread_id)


def resume(thread_id: str, answer) -> dict:
    if answer is None:
        raise ValueError("恢复内容不能为空")
    try:
        with _lock:
            state = get_graph().invoke(Command(resume=answer), _cfg(thread_id))
    except KeyError as exc:
        raise KeyError("会话不存在或已结束") from exc
    return serialize(state, thread_id)


def reset_graph() -> None:
    """仅供测试或换 checkpoint 文件时重建单例。"""
    global _graph
    with _lock:
        if _graph is not None:
            try:
                _graph.checkpointer.conn.close()
            except Exception:  # noqa: BLE001
                pass
        _graph = None
