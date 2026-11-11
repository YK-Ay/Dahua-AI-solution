"""多 Agent 共享的图状态。"""
from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    # ---- 输入 ----
    text: str                 # 需求原文（含人工补充内容）
    name: str | None
    lanes: int | None
    retention_days: int | None

    # ---- 子 Agent 产物 ----
    intent: dict | None       # 意图识别：行业/场景/路数分配/需求条目
    selection: dict | None    # 选型：每场景摄像机型号与理由
    bom: dict | None          # 配单：NVR/硬盘/交换/显示/平台 + 存储带宽
    spec: dict | None         # 方案：Spec.model_dump()

    # ---- 流程控制 ----
    stage: str                # 主 Agent 路由到哪个子 Agent
    gaps: list[str]           # 软缺口（写进方案"待确认"章节）
    gate_gaps: list[str]      # 硬闸门缺口（触发缺口回问）
    rounds: int               # 缺口回问轮次
    decision: str | None      # approve / revise
    feedback: str | None
    outputs: dict             # {"png": ..., "docx": ...}
    parser: dict              # 需求解析引擎信息（llm / rule / offline）
    research: dict            # 工具核实记录（LLM 自主调工具，结果仅供展示审计）
    patched: bool             # 审阅页是否提交过结构化参数修改
