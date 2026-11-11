"""LangGraph 多 Agent 编排：主 Agent 调度五个子 Agent，人工在两处介入。

架构（对应华为云解决方案助手的「主循环 + 子能力」而不是五个互相调用的 LLM）：
    主 Agent（supervisor）
      ├─ 意图识别 Agent：需求原文 → 行业/场景/路数分配
      ├─ 选型 Agent：场景 → RAG 真实配单/中标案例 → 摄像机型号
      ├─ 配单 Agent：型号 → NVR/硬盘/交换/显示/平台 + 存储带宽
      ├─ 方案 Agent：意图 + 配单 → Spec + 硬闸门校验
      └─ 产物结果 Agent：Spec → 拓扑 PNG / Word / PPT
    人工介入：缺口回问（ask_gap）、Spec 审阅（review）

主 Agent 只做两件事：按 stage 路由、调用工具（research 里的 search_kb /
calc_storage / calc_bandwidth / compare_pair）。所有数字仍由 app.builder /
app.calc 确定性计算，子 Agent 不编造型号或价格。

用法：
    from agent.graph import build_graph
    graph = build_graph()
    cfg = {"configurable": {"thread_id": "t-001"}}
    graph.invoke({"text": "……"}, cfg)          # 跑到第一个 interrupt 就返回
    graph.invoke(Command(resume="补充：100 路"), cfg)  # 从断点继续
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from app.builder import SpecGateError, build_spec, validate_spec
from config.settings import BASE_DIR

from . import subagents as S
from .parser import parse_requirement, phrase_gaps
from .state import AgentState
from .tools import research as run_research

CHECKPOINT_DB = BASE_DIR / "data" / "checkpoints" / "langgraph.sqlite"
EXPORT_DIR = BASE_DIR / "data" / "exports"
MAX_GAP_ROUNDS = 3
APPROVALS = {"approve", "approved", "ok", "yes", "y", "通过", "同意", "确认", "可以"}

AGENTS = ("intent", "research", "selection", "bom", "solution", "deliver")
TARGETS = AGENTS + ("review", "ask_gap", "abort")


# ---------------------------------------------------------------- 子 Agent 节点
def intent_node(state: AgentState) -> dict:
    return S.run_intent(state, parse_requirement=parse_requirement)


def research_node(state: AgentState) -> dict:
    """主 Agent 调工具核实原文：LLM 自主决定是否 search_kb / calc_* / compare_pair。"""
    try:
        info = run_research(state.get("text", ""))
    except Exception as exc:  # noqa: BLE001 —— 研究层任何异常都不能中断流程
        info = {"engine": "rule", "calls": [],
                "warnings": [f"工具核实异常已跳过：{type(exc).__name__}"]}
    return {"research": info, "stage": "selection"}


def selection_node(state: AgentState) -> dict:
    return S.run_selection(state)


def bom_node(state: AgentState) -> dict:
    return S.run_bom(state)


def solution_node(state: AgentState) -> dict:
    return S.run_solution(state, build_spec=build_spec, validate_spec=validate_spec)


def deliver_node(state: AgentState) -> dict:
    return S.run_deliver(state, export_dir=EXPORT_DIR)


# ---------------------------------------------------------------- 主 Agent
def supervisor_node(state: AgentState) -> dict:
    """主 Agent 不写业务数据，只决定下一步由哪个子 Agent 执行。"""
    return {}


def route_from_supervisor(state: AgentState) -> str:
    stage = state.get("stage") or "intent"
    if stage == "gate":
        return "abort" if state.get("rounds", 0) >= MAX_GAP_ROUNDS else "ask_gap"
    return stage if stage in TARGETS else "intent"


# ---------------------------------------------------------------- 人工介入
def ask_gap_node(state: AgentState) -> dict:
    """缺口回问：把硬闸门缺口改写成具体追问，中断等人工补充。"""
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
    reset = {"spec": None, "gate_gaps": [], "intent": None, "selection": None,
             "bom": None, "stage": "intent", "rounds": rounds}
    if not supplement:
        return reset
    return {**reset, "text": f"{state.get('text', '')}\n补充：{supplement}"}


def review_node(state: AgentState) -> dict:
    """人工审阅：approve 生成产物；改参数或提意见则回到意图识别重算。"""
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

    if decision == "approve":
        update["stage"] = "deliver"
    elif feedback or update.get("patched"):
        update.update({"stage": "intent", "spec": None, "gate_gaps": [],
                       "intent": None, "selection": None, "bom": None})
    else:
        update["stage"] = "review"
    return update


def abort_node(state: AgentState) -> dict:
    """缺口回问超过上限：不输出半成品，保留缺口清单人工处理。"""
    return {
        "outputs": {},
        "gaps": state.get("gate_gaps") or state.get("gaps") or [],
    }


def build_node(state: AgentState) -> dict:
    """一次跑完五个子 Agent（脚本与单测用；图内由主 Agent 逐个调度）。"""
    merged = dict(state)
    for step in (intent_node, research_node, selection_node, bom_node, solution_node):
        merged.update(step(merged))
        if merged.get("stage") == "gate":
            break
    keys = ("spec", "gate_gaps", "gaps", "parser", "research")
    return {k: merged[k] for k in keys if k in merged}


# ---------------------------------------------------------------- 工具函数
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


def build_graph(checkpoint_path: Path | str = CHECKPOINT_DB):
    """编译带 SqliteSaver 的图；同一个 checkpoint 文件可跨进程续跑。"""
    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("intent", intent_node)
    graph.add_node("research", research_node)
    graph.add_node("selection", selection_node)
    graph.add_node("bom", bom_node)
    graph.add_node("solution", solution_node)
    graph.add_node("review", review_node)
    graph.add_node("ask_gap", ask_gap_node)
    graph.add_node("deliver", deliver_node)
    graph.add_node("abort", abort_node)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor", route_from_supervisor, {name: name for name in TARGETS})
    for name in ("intent", "research", "selection", "bom", "solution",
                 "review", "ask_gap"):
        graph.add_edge(name, "supervisor")
    graph.add_edge("deliver", END)
    graph.add_edge("abort", END)
    # 多 Agent 协作把步数放大，放宽递归上限避免正常修订被误判为死循环
    return graph.compile(checkpointer=saver).with_config({"recursion_limit": 100})


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
