"""LangGraph 编排：需求 → Spec → 校验 →（缺口回问 / 人工审阅）→ 导出。

图只立三个点，不再扩：
  1. checkpoint 断点续跑：SqliteSaver 落盘到 data/checkpoints/langgraph.sqlite，
     进程退出后凭 thread_id 继续
  2. human-in-loop：缺口回问与 Spec 审阅两处 interrupt() 暂停，等人工输入
  3. 条件回边：validate_spec 失败 → 缺口回问 → 补充需求后回到 build

计算与选型仍全部由 app.builder / app.calc 确定性执行，图只负责流程与暂停/恢复。

用法：
    from agent.graph import build_graph
    graph = build_graph()
    cfg = {"configurable": {"thread_id": "t-001"}}
    graph.invoke({"text": "……"}, cfg)          # 跑到第一个 interrupt 就返回
    graph.invoke(Command(resume="补充：100 路"), cfg)  # 从断点继续
"""
from __future__ import annotations

import sqlite3
import re
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from app.builder import SpecGateError, build_spec, validate_spec
from app.models import Spec
from app.topology import render_topology
from app.word import render_docx
from config.settings import BASE_DIR

from .parser import parse_requirement, phrase_gaps
from .tools import research as run_research

CHECKPOINT_DB = BASE_DIR / "data" / "checkpoints" / "langgraph.sqlite"
EXPORT_DIR = BASE_DIR / "data" / "exports"
MAX_GAP_ROUNDS = 3
APPROVALS = {"approve", "approved", "ok", "yes", "y", "通过", "同意", "确认", "可以"}


class AgentState(TypedDict, total=False):
    text: str                 # 需求原文（含人工补充内容）
    name: str | None
    lanes: int | None
    retention_days: int | None
    spec: dict | None         # Spec.model_dump()
    gaps: list[str]           # 软缺口（写进方案"待确认"章节）
    gate_gaps: list[str]      # 硬闸门缺口（触发条件回边）
    rounds: int               # 缺口回问轮次
    decision: str | None      # approve / revise
    feedback: str | None
    outputs: dict             # {"png": ..., "docx": ...}
    parser: dict              # 需求解析引擎信息（llm / rule / offline）
    research: dict            # 工具核实记录（LLM 自主调工具，结果仅供展示审计）
    patched: bool             # 审阅页是否提交过结构化参数修改


LANES_NUM_RE = re.compile(r"\d+\s*(?:路|个点位|台摄像机|摄像机|点位|个监控)")


def _apply_lanes_override(text: str, lanes: int | None) -> str:
    """审阅页改了「总路数」时，必须让新总数真正生效。

    build_spec 的 allocate_lanes 优先采用原文里各场景的显式数字
    （如「周界80路、出入口20路」），会把总参 lanes 挤掉。这里把场景级
    数字抹去、只留场景词，并追加一条总点位，让确定性算法按新总数重新分配。
    """
    if lanes is None:
        return text
    cleaned = re.sub(r"[ \t]{2,}", " ", LANES_NUM_RE.sub("", text))
    return f"{cleaned}\n总点位 {lanes} 路"


def build_node(state: AgentState) -> dict:
    """解析需求并校验；校验失败不抛异常，落到 gate_gaps 交给条件边。

    LLM 只做场景词归一化，不产数字；断网 / 超时自动回退原文 + 规则引擎。
    补写的场景词来自固定白名单且不含数字，计算与选型仍全部交给
    build_spec / validate_spec 确定性执行。

    另外跑一轮「工具核实」（research）：LLM 自主决定是否调用 search_kb /
    calc_storage / calc_bandwidth / compare_pair 核实原文信息。工具结果
    只作为研究记录展示，不改变确定性计算链路；离线 / 失败自动跳过。
    """
    raw_text = state.get("text", "")
    try:
        research_info = run_research(raw_text)
    except Exception as exc:  # noqa: BLE001 —— 研究层任何异常都不能中断流程
        research_info = {"engine": "rule", "calls": [],
                         "warnings": [f"工具核实异常已跳过：{type(exc).__name__}"]}
    try:
        parsed = parse_requirement(raw_text)
    except Exception as exc:  # noqa: BLE001 —— 解析层任何异常都不能中断流程
        parsed = {"text": raw_text, "engine": "rule", "scenes": [],
                  "project_name": None, "warnings": [f"解析降级：{type(exc).__name__}"]}
    text = parsed.get("text") or raw_text
    parser_info = {
        "engine": parsed.get("engine"),
        "scenes": parsed.get("scenes") or [],
        "normalized_scenes": parsed.get("normalized_scenes") or [],
        "number_normalized": parsed.get("number_normalized") or [],
        "dropped_scenes": parsed.get("dropped_scenes") or [],
        "industry_suggestion": parsed.get("industry_suggestion"),
        "warnings": parsed.get("warnings") or [],
    }
    name = state.get("name") or parsed.get("project_name")
    lanes = state.get("lanes")
    text = _apply_lanes_override(text, lanes)
    try:
        spec = build_spec(
            text,
            name=name,
            lanes=state.get("lanes"),
            retention_days=state.get("retention_days"),
        )
        validate_spec(spec)
    except SpecGateError as exc:
        return {"spec": None, "gate_gaps": exc.gaps, "gaps": exc.gaps,
                "parser": parser_info, "research": research_info}
    except ValueError as exc:
        return {"spec": None, "gate_gaps": [str(exc)], "gaps": [str(exc)],
                "parser": parser_info, "research": research_info}
    return {"spec": spec.model_dump(), "gate_gaps": [], "gaps": spec.gaps,
            "parser": parser_info, "research": research_info}


def route_after_build(state: AgentState) -> str:
    if state.get("spec"):
        return "review"
    if state.get("rounds", 0) >= MAX_GAP_ROUNDS:
        return "abort"
    return "ask_gap"


def ask_gap_node(state: AgentState) -> dict:
    """缺口回问：把硬闸门缺口改写成具体追问，中断等人工补充。

    LLM 只负责追问措辞，不产数字；离线 / 失败自动用本地模板。结果有缓存，
    LangGraph 重放本节点（resume 时会重跑到 interrupt）不会重复调用大模型。
    """
    gaps = state.get("gate_gaps") or []
    try:
        phrased = phrase_gaps(gaps)
    except Exception as exc:  # noqa: BLE001 —— 追问失败不能卡住流程
        phrased = {"items": [{"gap": str(g), "question": "请补充这项缺口对应的具体信息"}
                             for g in gaps],
                   "engine": "rule", "warnings": [f"追问降级：{type(exc).__name__}"]}
    answer = interrupt({
        "type": "gap",
        "question": "方案存在缺口，请补充需求信息后继续",
        "gaps": gaps,
        "questions": phrased["items"],
        "question_engine": phrased["engine"],
        "question_warnings": phrased.get("warnings") or [],
        "round": state.get("rounds", 0) + 1,
        "max_rounds": MAX_GAP_ROUNDS,
    })
    supplement = _as_text(answer)
    rounds = state.get("rounds", 0) + 1
    if not supplement:
        return {"rounds": rounds}
    return {"text": f"{state.get('text', '')}\n补充：{supplement}", "rounds": rounds}


def review_node(state: AgentState) -> dict:
    """人工审阅：approve 通过；修改意见或结构化 patch 都回边重算。

    审阅页可以改「总路数 / 存储天数 / 项目名」，这些值必须写进 state
    让 build_node 重新调用 build_spec，不能塞进自然语言——否则正则会先
    命中原文里的第一个「20路」，改不动总数。
    """
    answer = interrupt({
        "type": "review",
        "question": "请审阅 Spec：通过请点「通过并生成」，修改参数或输入意见则重新计算",
        "spec": state.get("spec"),
        "gaps": state.get("gaps") or [],
        "research": state.get("research") or {},
    })
    decision, feedback = _as_decision(answer)
    update: dict = {"decision": decision, "feedback": feedback}
    patch = answer.get("patch") if isinstance(answer, dict) else None
    if isinstance(patch, dict):
        for key in ("lanes", "retention_days", "name"):
            if patch.get(key) not in (None, ""):
                update[key] = patch[key]
        update["decision"] = "revise"
        decision = "revise"
        update["patched"] = True
    elif decision == "revise" and feedback:
        update["text"] = f"{state.get('text', '')}\n修改：{feedback}"
    return update


def route_after_review(state: AgentState) -> str:
    if state.get("decision") == "approve":
        return "finish"
    if state.get("feedback") or state.get("patched"):
        return "build"
    return "review"


def finish_node(state: AgentState) -> dict:
    """审阅通过后按 Spec 渲染拓扑 PNG 与 Word，产物仍由确定性函数生成。"""
    spec = Spec.model_validate(state["spec"])
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = _safe_name(spec.project.name)
    png = EXPORT_DIR / f"{stem}-{stamp}-拓扑-简略.png"
    png_detail = EXPORT_DIR / f"{stem}-{stamp}-拓扑-详细.png"
    docx = EXPORT_DIR / f"{stem}-{stamp}.docx"
    render_topology(spec, png, mode="simple")
    render_topology(spec, png_detail, mode="detailed")
    render_docx(spec, docx, topology_png=png)
    return {"outputs": {"png": str(png), "png_detail": str(png_detail), "docx": str(docx)}}


def abort_node(state: AgentState) -> dict:
    """缺口回问超过上限：不输出半成品，保留缺口清单人工处理。"""
    return {
        "outputs": {},
        "gaps": state.get("gate_gaps") or state.get("gaps") or [],
    }


def _as_text(answer: object) -> str:
    if isinstance(answer, dict):
        return str(answer.get("text") or answer.get("answer") or "").strip()
    return str(answer or "").strip()


def _as_decision(answer: object) -> tuple[str, str | None]:
    if isinstance(answer, dict):
        decision = str(answer.get("decision") or "").strip().lower()
        feedback = str(answer.get("feedback") or answer.get("text") or "").strip()
    else:
        decision = str(answer or "").strip().lower()
        feedback = decision if decision not in APPROVALS else ""
    return ("approve" if decision in APPROVALS else "revise"), (feedback or None)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return cleaned or "未命名方案"


def build_graph(checkpoint_path: Path | str = CHECKPOINT_DB):
    """编译带 SqliteSaver 的图；同一个 checkpoint 文件可跨进程续跑。"""
    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)

    graph = StateGraph(AgentState)
    graph.add_node("build", build_node)
    graph.add_node("ask_gap", ask_gap_node)
    graph.add_node("review", review_node)
    graph.add_node("finish", finish_node)
    graph.add_node("abort", abort_node)

    graph.set_entry_point("build")
    graph.add_conditional_edges("build", route_after_build, {
        "review": "review",
        "ask_gap": "ask_gap",
        "abort": "abort",
    })
    graph.add_edge("ask_gap", "build")
    graph.add_conditional_edges("review", route_after_review, {
        "finish": "finish",
        "build": "build",
        "review": "review",
    })
    graph.add_edge("finish", END)
    graph.add_edge("abort", END)
    return graph.compile(checkpointer=saver)


def resume(graph, thread_id: str, answer: str | dict):
    """便捷函数：从上次中断处继续，等价于 invoke(Command(resume=...))。"""
    return graph.invoke(Command(resume=answer), {"configurable": {"thread_id": thread_id}})


if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:]).strip() or "某智慧园区 100 路摄像机，周界、出入口、主干道，存储 30 天"
    thread = {"configurable": {"thread_id": f"cli-{datetime.now():%Y%m%d-%H%M%S}"}}
    graph = build_graph()
    state = graph.invoke({"text": text}, thread)
    while state.get("__interrupt__"):
        payload = state["__interrupt__"][0].value
        print(f"\n[{payload['type']}] {payload['question']}")
        for gap in payload.get("gaps") or []:
            print(f"  - {gap}")
        if payload["type"] == "review":
            print(f"  Spec 设备数：{len(state['spec']['devices'])}，缺口：{payload.get('gaps') or '无'}")
        state = graph.invoke(Command(resume=input("> ").strip()), thread)
    print("\n完成：", state.get("outputs") or "未产出（缺口未补齐）")
