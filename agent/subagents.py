"""五个子 Agent：意图识别 → 选型 → 配单 → 方案 → 产物。

主 Agent（supervisor）只负责按 stage 调度，不直接写业务数据。这里每个
Agent 都只做一件事，并且只把结构化结果写回 state：

- 意图识别：需求原文 → 行业/场景/路数分配/需求条目（Plan）
- 选型：每个场景 → RAG 案例 + 产品库白名单里的摄像机型号与理由
- 配单：选型结果 → NVR/硬盘/交换机/显示/平台的数量与型号 + 存储带宽
- 方案：意图 + 配单 → 可审阅的 Spec（含边界、假设、缺口）
- 产物：Spec → 拓扑 PNG / Word / PPT

所有数字仍由 app.builder / app.calc 确定性计算，Agent 不编造型号或价格。
"""
from __future__ import annotations

import re

from app.builder import (
    Bom,
    Plan,
    SpecGateError,
    analyze,
    plan_bom,
    select_cameras,
)
from app.kb import ProductRepo
from app.models import Device, Spec
from app.package import build_deliverables

from .state import AgentState

LANES_NUM_RE = re.compile(r"\d+\s*(?:路|个点位|台摄像机|摄像机|点位|个监控)")


def _apply_lanes_override(text: str, lanes: int | None) -> str:
    """审阅页改了总路数时，抹掉场景级数字并追加总点位，让新总数真正生效。"""
    if lanes is None:
        return text
    cleaned = re.sub(r"[ \t]{2,}", " ", LANES_NUM_RE.sub("", text))
    return f"{cleaned}\n总点位 {lanes} 路"


def _gate(exc: Exception) -> dict:
    gaps = exc.gaps if isinstance(exc, SpecGateError) else [str(exc)]
    return {"spec": None, "gate_gaps": gaps, "gaps": gaps, "stage": "gate"}


# ---------------------------------------------------------------- 意图识别
def run_intent(state: AgentState, parse_requirement) -> dict:
    """解析需求并做行业/场景/路数分配；解析失败不抛异常，落到 gate_gaps。"""
    raw_text = state.get("text", "")
    try:
        parsed = parse_requirement(raw_text)
    except Exception as exc:  # noqa: BLE001 —— 解析层任何异常都不能中断流程
        parsed = {"text": raw_text, "engine": "rule", "scenes": [],
                  "project_name": None, "warnings": [f"解析降级：{type(exc).__name__}"]}
    parser_info = {
        "engine": parsed.get("engine"),
        "scenes": parsed.get("scenes") or [],
        "normalized_scenes": parsed.get("normalized_scenes") or [],
        "number_normalized": parsed.get("number_normalized") or [],
        "dropped_scenes": parsed.get("dropped_scenes") or [],
        "industry_suggestion": parsed.get("industry_suggestion"),
        "industry_override": parsed.get("industry_override"),
        "warnings": parsed.get("warnings") or [],
    }
    name = state.get("name") or parsed.get("project_name")
    text = _apply_lanes_override(parsed.get("text") or raw_text, state.get("lanes"))
    try:
        plan = analyze(text, state.get("lanes"), state.get("retention_days"),
                       industry_override=parsed.get("industry_override"))
    except (SpecGateError, ValueError) as exc:
        return {**_gate(exc), "parser": parser_info, "name": name,
                "intent": None, "stage": "gate"}
    return {"intent": plan.to_dict(), "parser": parser_info, "name": name,
            "gate_gaps": [], "stage": "research"}


# ---------------------------------------------------------------- 选型
def run_selection(state: AgentState) -> dict:
    """每个场景按 RAG 真实案例选摄像机，产出型号 + 理由 + 证据。"""
    try:
        plan = Plan.from_dict(state["intent"])
        with ProductRepo() as repo:
            cameras = select_cameras(repo, plan)
    except (KeyError, SpecGateError, ValueError) as exc:
        return {**_gate(exc), "selection": None, "stage": "gate"}
    scenes = [
        {"target": d.scene, "lanes": d.qty, "model": d.model, "reason": d.reason}
        for d in cameras
    ]
    return {
        "selection": {
            "devices": [d.model_dump() for d in cameras],
            "scenes": scenes,
        },
        "stage": "bom",
    }


# ---------------------------------------------------------------- 配单
def run_bom(state: AgentState) -> dict:
    """按选型结果配齐 NVR/硬盘/交换/显示/平台，并测算存储与带宽。"""
    try:
        plan = Plan.from_dict(state["intent"])
        cameras = [Device(**d) for d in state["selection"]["devices"]]
        with ProductRepo() as repo:
            bom = plan_bom(repo, plan, cameras)
    except (KeyError, SpecGateError, ValueError) as exc:
        return {**_gate(exc), "bom": None, "stage": "gate"}
    return {"bom": bom.to_dict(), "stage": "solution"}


# ---------------------------------------------------------------- 方案
def run_solution(state: AgentState, build_spec, validate_spec) -> dict:
    """把意图与配单组装成 Spec，并过硬闸门（型号白名单 / 数量 / 存储）。"""
    try:
        plan = Plan.from_dict(state["intent"]) if state.get("intent") else None
        cameras = ([Device(**d) for d in state["selection"]["devices"]]
                   if state.get("selection") else None)
        bom = Bom.from_dict(state["bom"]) if state.get("bom") else None
        text = plan.text if plan else state.get("text", "")
        spec = build_spec(
            text,
            name=state.get("name"),
            lanes=state.get("lanes"),
            retention_days=state.get("retention_days"),
            plan=plan,
            cameras=cameras,
            bom=bom,
        )
        validate_spec(spec)
    except (SpecGateError, ValueError) as exc:
        return _gate(exc)
    return {"spec": spec.model_dump(), "gate_gaps": [], "gaps": spec.gaps,
            "stage": "review"}


# ---------------------------------------------------------------- 产物
def run_deliver(state: AgentState, export_dir) -> dict:
    """审阅通过后渲染 Word / Excel / 拓扑 / PPT，并打包成方案包 ZIP。"""
    spec = Spec.model_validate(state["spec"])
    output_dir = export_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = build_deliverables(spec, output_dir)
    return {"outputs": outputs, "stage": "done"}
