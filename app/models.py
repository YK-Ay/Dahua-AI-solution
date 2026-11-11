"""方案中间表示 Spec。

约定：LLM 只负责把需求翻译成这些字段，存储/带宽等数字一律由 calc.py 确定性计算，
Word / 拓扑图 / 对比表后续都从 Spec 渲染，不反向重写正文。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["高", "中", "低"]


class Scale(BaseModel):
    buildings: int = 0
    area_ha: float = 0.0
    entrances: int = 0


class Project(BaseModel):
    name: str
    industry: str
    industry_tags: list[str] = Field(default_factory=list)
    scale: Scale = Field(default_factory=Scale)


class Requirement(BaseModel):
    target: str
    intent: str
    level: Level = "中"
    lanes: int = 0
    bitrate_mbps: float = 0.0


class Device(BaseModel):
    category: str
    model: str
    qty: int
    scene: str = ""
    display_name: str = ""
    unit_price: float | None = None
    reason: str = ""          # 选型依据（场景/点位/配单来源），写入审阅页与 Word


class Storage(BaseModel):
    retention_days: int
    codec: str = "H.265"
    bitrate_mbps: dict[str, float] = Field(default_factory=dict)
    raw_tb: float
    with_redundancy_tb: float
    redundancy: float = 1.1


class Network(BaseModel):
    uplink_mbps: float


class Topology(BaseModel):
    layers: list[str] = Field(default_factory=lambda: ["前端", "传输", "存储", "平台", "显示"])


class Boundaries(BaseModel):
    included: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Spec(BaseModel):
    project: Project
    requirements: list[Requirement]
    devices: list[Device]
    storage: Storage
    network: Network
    topology: Topology = Field(default_factory=Topology)
    boundaries: Boundaries = Field(default_factory=Boundaries)
    gaps: list[str] = Field(default_factory=list)
