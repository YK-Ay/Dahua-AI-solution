"""Agent 工具层：与 mcp_server.py 同源的确定性工具 + DeepSeek function-calling 循环。

定位（延续项目反幻觉口径，与 MCP Server 是同一套确定性核心）：
- 四个工具全是薄包装，复用 app.kb / app.calc / app.compare_service 的
  确定性函数：search_kb / calc_storage / calc_bandwidth / compare_pair。
- LLM 只决定「调不调、调哪个、传什么参数」；数字全部由确定性函数算出，
  工具入参原样展示在研究记录里，可人工审计。
- 工具结果只作为「研究记录」展示，不回写需求原文、不参与 build_spec /
  calc 的正式计算——方案的数字永远只走确定性链路。
- 离线 / 无 Key / 任何异常：research 返回空记录，主流程照常不受影响。
- 结果按需求原文缓存，LangGraph 回边重跑 build 节点时不会重复调用大模型。
"""
from __future__ import annotations

import json
import os
import threading

import requests

from app import netcheck
from app.calc import calc_bandwidth as _calc_bandwidth
from app.calc import calc_storage as _calc_storage
from app.compare_service import pair as _compare_pair
from app.kb import ProductRepo
from app.models import Requirement
from app.rag import config as C

MAX_TOOL_CALLS = 4      # 单次研究最多调用多少次工具
MAX_ROUNDS = 3          # 最多几轮「LLM 提出调用 → 执行 → 回传」
LLM_TIMEOUT = float(os.getenv("AGENT_RESEARCH_TIMEOUT", "20"))
RESULT_CHARS = 1600     # 单条工具结果写进 state 的最大字符数（防 checkpoint 膨胀）
CATEGORIES = ("网络摄像机", "NVR", "PoE交换机")

RESEARCH_SYSTEM_PROMPT = (
    "你是安防售前研究助理。阅读客户需求原文，只在确有必要时调用工具核实信息：\n"
    "- 原文点名了大华型号（DH- 开头）或要查产品价格 → search_kb（不确定类别时不要传 class_name，避免过滤掉型号）\n"
    "- 原文点名了海康型号（DS- 开头）且需要大华对标 → 先 search_kb 找大华同档型号，再 compare_pair\n"
    "- 原文给出了明确的路数和码流，需要预估存储或带宽 → calc_storage / calc_bandwidth\n"
    "硬性要求：\n"
    "- 工具参数里的数字必须逐字来自需求原文，禁止编造、换算或估计\n"
    "- 类目只允许：网络摄像机 / NVR / PoE交换机\n"
    "- 拿不准就不要调用；最多调用 4 次\n"
    "- 核实完成或无需核实时，直接回复「完成」两个字，不要输出总结"
)


# ---------------------------------------------------------------- 工具实现

def _positive(value, field: str) -> float:
    """入参数字化 + 正数校验；不合法直接抛 ValueError 交给 run_tool。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是数字") from None
    if num <= 0:
        raise ValueError(f"{field} 必须大于 0")
    return num


def t_search_kb(keyword: str = "", class_name: str | None = None, limit: int = 8) -> dict:
    """在本地大华产品库检索型号（同 mcp_server.search_kb，多了官网机型兜底）。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return {"error": "请提供检索关键词"}
    try:
        cap = max(1, min(int(limit or 8), 12))
    except (TypeError, ValueError):
        cap = 8
    with ProductRepo() as repo:
        rows = repo.search(keyword=keyword, class_name=class_name, limit=cap)
    if rows:
        return {"found": len(rows), "items": rows}
    note = f"产品库中没有匹配「{keyword}」的型号"
    if keyword.upper().startswith("DS-"):
        note += "（DS- 是海康型号，不在大华产品库；可用 compare_pair 直接对比）"
    return {"found": 0, "items": [], "note": note}


def t_calc_storage(lanes: int, bitrate_mbps: float, retention_days: int,
                   redundancy: float = 1.1) -> dict:
    """按 路数×码流×保存天数 确定性计算存储容量（TB，含冗余）。"""
    n_lanes = _positive(lanes, "路数")
    bitrate = _positive(bitrate_mbps, "码流")
    days = _positive(retention_days, "存储天数")
    red = _positive(redundancy, "冗余系数")
    req = [Requirement(target="点位1", intent="视频监控",
                       lanes=int(n_lanes), bitrate_mbps=bitrate)]
    return _calc_storage(req, int(days), red)


def t_calc_bandwidth(lanes: int, bitrate_mbps: float, overhead: float = 1.2) -> dict:
    """按 Σ(路数×码流) × 并发系数 计算上行带宽（Mbps）。"""
    n_lanes = _positive(lanes, "路数")
    bitrate = _positive(bitrate_mbps, "码流")
    ovh = _positive(overhead, "并发系数")
    req = [Requirement(target="点位1", intent="视频监控",
                       lanes=int(n_lanes), bitrate_mbps=bitrate)]
    return {"uplink_mbps": _calc_bandwidth(req, ovh)}


def compare_pair_facts(category: str, dahua_model: str, hik_model: str) -> dict:
    """大华 vs 海康任意配对逐字段对比，压缩成研究记录友好的结构。"""
    if category not in CATEGORIES:
        return {"error": "类目只允许：" + "、".join(CATEGORIES)}
    if not (dahua_model or "").strip() or not (hik_model or "").strip():
        return {"error": "请同时提供大华型号与海康型号"}
    res = _compare_pair(category.strip(), dahua_model.strip(), hik_model.strip())
    rows = [{
        "field": r["field"], "status": r["status_label"],
        "dahua": r["dahua_value"], "hik": r["hik_value"],
    } for r in res["rows"]]
    return {
        "category": res["category"],
        "dahua_model": res["dahua_model"], "hik_model": res["hik_model"],
        "coverage": res["coverage"], "summary": res["summary"], "rows": rows,
    }


TOOLS: dict[str, dict] = {
    "search_kb": {
        "schema": {
            "type": "function",
            "function": {
                "name": "search_kb",
                "description": "在本地大华产品库按型号或名称模糊检索，返回真实存在的型号与标准价（不编造型号）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "型号或名称关键词，如 DH-IPC"},
                        "class_name": {"type": "string", "description": "可选，产品类别过滤词，如 摄像机 / 交换机"},
                        "limit": {"type": "integer", "description": "返回条数，默认 8，最多 12"},
                    },
                    "required": ["keyword"],
                },
            },
        },
        "handler": t_search_kb,
    },
    "calc_storage": {
        "schema": {
            "type": "function",
            "function": {
                "name": "calc_storage",
                "description": "按 路数 × 单路码流(Mbps) × 保存天数 确定性计算存储容量（TB，含冗余系数）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lanes": {"type": "integer", "description": "点位数 / 路数（取需求原文数字）"},
                        "bitrate_mbps": {"type": "number", "description": "单路码流 Mbps，如 4.0"},
                        "retention_days": {"type": "integer", "description": "录像保存天数（取需求原文数字）"},
                        "redundancy": {"type": "number", "description": "冗余系数，默认 1.1，无特别说明不要改"},
                    },
                    "required": ["lanes", "bitrate_mbps", "retention_days"],
                },
            },
        },
        "handler": t_calc_storage,
    },
    "calc_bandwidth": {
        "schema": {
            "type": "function",
            "function": {
                "name": "calc_bandwidth",
                "description": "按 Σ(路数 × 单路码流) × 并发系数 计算上行带宽（Mbps）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lanes": {"type": "integer", "description": "点位数 / 路数（取需求原文数字）"},
                        "bitrate_mbps": {"type": "number", "description": "单路码流 Mbps，如 4.0"},
                        "overhead": {"type": "number", "description": "并发系数，默认 1.2，无特别说明不要改"},
                    },
                    "required": ["lanes", "bitrate_mbps"],
                },
            },
        },
        "handler": t_calc_bandwidth,
    },
    "compare_pair": {
        "schema": {
            "type": "function",
            "function": {
                "name": "compare_pair",
                "description": "大华与海康各选一款具体型号，按官网公开参数逐字段对比；未公开的字段会标注，不推断",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(CATEGORIES),
                                     "description": "对比类目"},
                        "dahua_model": {"type": "string", "description": "大华具体型号，须先用 search_kb 确认存在"},
                        "hik_model": {"type": "string", "description": "海康具体型号（DS- 开头，须为需求原文提到的）"},
                    },
                    "required": ["category", "dahua_model", "hik_model"],
                },
            },
        },
        "handler": compare_pair_facts,
    },
}


def run_tool(name: str, args: dict | None) -> dict:
    """执行一个工具调用；任何失败都返回 {"ok": False, "error": ...}，绝不抛异常。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": f"未知工具：{name}"}
    if args is None or not isinstance(args, dict):
        return {"ok": False, "error": "参数不是合法的 JSON 对象"}
    try:
        result = tool["handler"](**args)
    except TypeError as exc:
        return {"ok": False, "error": f"参数不合法：{exc}"}
    except Exception as exc:  # noqa: BLE001 —— 工具失败只记录，不中断研究
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    ok = not (isinstance(result, dict) and result.get("error"))
    return {"ok": ok, "result": result if ok else result.get("error")}


def _truncate(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= RESULT_CHARS:
        return text
    return text[:RESULT_CHARS] + "…（已截断）"


# ------------------------------------------------------- LLM 工具调用循环

_cache: dict[str, dict] = {}
_lock = threading.Lock()


def clear_research_cache() -> None:
    """供测试与配置变更后清空缓存。"""
    with _lock:
        _cache.clear()


def _chat_with_tools(messages: list[dict]) -> dict:
    """一轮对话；返回的 message 可能带 tool_calls。异常抛给上层降级。"""
    payload = {
        "model": C.DEEPSEEK_MODEL,
        "messages": messages,
        "tools": [t["schema"] for t in TOOLS.values()],
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 500,
        "stream": False,
    }
    r = requests.post(
        C.DEEPSEEK_BASE_URL + "/chat/completions",
        headers={"Authorization": "Bearer " + C.DEEPSEEK_API_KEY,
                 "Content-Type": "application/json"},
        json=payload,
        timeout=LLM_TIMEOUT,
    )
    r.raise_for_status()
    msg = (r.json().get("choices") or [{}])[0].get("message", {})
    # 规范成可 json 序列化的结构（原样回填给下一轮 messages）
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = [
            {"id": tc.get("id", ""),
             "type": "function",
             "function": {"name": tc["function"]["name"],
                          "arguments": tc["function"].get("arguments") or "{}"}}
            for tc in msg["tool_calls"] if tc.get("function")
        ]
    return out


def research(text: str, use_llm: bool = True) -> dict:
    """让 LLM 自主决定是否调用工具核实需求，返回研究记录。

    返回 {"engine": llm/rule/offline, "calls": [{tool,args,ok,result}], "warnings": []}。
    研究记录只用于展示与人工审计，不进入确定性计算链路。
    """
    clean = (text or "").strip()
    if not clean:
        return {"engine": "rule", "calls": [], "warnings": []}

    online = netcheck.llm_reachable(C.DEEPSEEK_BASE_URL)
    if not online:
        return {"engine": "offline", "calls": [],
                "warnings": ["当前无网络，跳过工具核实"]}
    if not (use_llm and C.DEEPSEEK_API_KEY):
        return {"engine": "rule", "calls": [],
                "warnings": ["未配置大模型 Key，跳过工具核实"]}

    with _lock:
        cached = _cache.get(clean)
    if cached:
        return dict(cached)

    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": "需求原文：\n" + clean[:1500]},
    ]
    calls: list[dict] = []
    warnings: list[str] = []
    try:
        for _ in range(MAX_ROUNDS):
            msg = _chat_with_tools(messages)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls or len(calls) >= MAX_TOOL_CALLS:
                break
            messages.append(msg)
            for tc in tool_calls:
                if len(calls) >= MAX_TOOL_CALLS:
                    break
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                out = run_tool(name, args)
                calls.append({
                    "tool": name,
                    "args": args if isinstance(args, dict) else {},
                    "ok": out.get("ok", False),
                    "result": _truncate(out.get("result")),
                })
                messages.append({
                    "role": "tool", "tool_call_id": tc.get("id", ""),
                    "content": calls[-1]["result"],
                })
        if len(calls) >= MAX_TOOL_CALLS:
            warnings.append(f"工具调用达到上限 {MAX_TOOL_CALLS} 次，已截断")
    except Exception as exc:  # noqa: BLE001 —— 研究失败不影响主流程
        warnings.append(f"工具核实中断，已保留已完成记录：{type(exc).__name__}")

    result = {"engine": "llm", "calls": calls, "warnings": warnings}
    with _lock:
        _cache[clean] = dict(result)
    return result


def status() -> dict:
    """给前端展示工具层状态。"""
    online = netcheck.llm_reachable(C.DEEPSEEK_BASE_URL)
    return {
        "tools": sorted(TOOLS),
        "online": online and bool(C.DEEPSEEK_API_KEY),
        "label": ("工具核实可用（" + "、".join(sorted(TOOLS)) + "）"
                  if online and C.DEEPSEEK_API_KEY else
                  ("工具核实离线（流程不受影响）" if not online else "工具核实未启用（未配置 Key）")),
    }
