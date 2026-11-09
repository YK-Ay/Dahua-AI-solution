"""MCP Server：把已有纯函数薄薄包一层 JSON Schema，暴露给 Claude / Cursor。

不新写业务逻辑，全部复用：
  calc_storage / calc_bandwidth  ← app.calc（确定性口径，带单测）
  search_kb                      ← app.kb.ProductRepo.search
  render_topology / gen_docx     ← app.topology / app.word
  compare_pair                   ← agent.tools.compare_pair_facts（与 Agent 工具层同源）

运行（stdio 传输）：
    python mcp_server.py
在 Cursor / Claude Desktop 的 mcpServers 里注册：
    {
      "mcpServers": {
        "dahua-assistant": {
          "command": "python",
          "args": ["/path/to/dahua-assistant/mcp_server.py"]  # 换成你本机的实际路径
        }
      }
    }
调试：npx @modelcontextprotocol/inspector python mcp_server.py
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from app.calc import calc_bandwidth as _calc_bandwidth
from app.calc import calc_storage as _calc_storage
from app.kb import ProductRepo
from app.models import Requirement, Spec
from app.topology import render_topology as _render_topology
from app.word import render_docx as _render_docx
from agent.tools import compare_pair_facts as _compare_pair_facts
from config.settings import BASE_DIR

EXPORT_DIR: Path = BASE_DIR / "data" / "exports"

server = MCPServer(
    name="dahua-assistant",
    version="1.1",
    instructions=(
        "大华安防解决方案助手工具集。存储容量与上行带宽一律由本工具确定性计算，"
        "不要让模型自己算数；型号信息只来自本地产品库，不得编造。"
    ),
)


class RequirementIn(BaseModel):
    """一路需求的输入参数。"""
    lanes: int = Field(default=0, ge=0, description="点位数 / 路数")
    bitrate_mbps: float = Field(gt=0, description="单路码流（Mbps），如 4.0 / 6.0")


class StorageOut(BaseModel):
    raw_tb: float
    with_redundancy_tb: float
    redundancy: float


def _to_requirements(items: list[RequirementIn]) -> list[Requirement]:
    return [
        Requirement(target=f"点位{idx + 1}", intent="视频监控", lanes=item.lanes, bitrate_mbps=item.bitrate_mbps)
        for idx, item in enumerate(items)
    ]


@server.tool(description="按 路数×码流×保存天数 确定性计算存储容量（TB），含冗余")
def calc_storage(requirements: list[RequirementIn], retention_days: int, redundancy: float = 1.1) -> StorageOut:
    if retention_days <= 0:
        raise ValueError("存储天数必须大于 0")
    return StorageOut(**_calc_storage(_to_requirements(requirements), retention_days, redundancy))


@server.tool(description="按 Σ(路数×码流) × 并发系数 计算上行带宽（Mbps）")
def calc_bandwidth(requirements: list[RequirementIn], overhead: float = 1.2) -> float:
    return _calc_bandwidth(_to_requirements(requirements), overhead)


@server.tool(description="在本地大华产品库检索型号（按型号或名称模糊匹配，只返回库内真实存在的型号）")
def search_kb(keyword: str = "", class_name: str | None = None, limit: int = 10) -> list[dict]:
    with ProductRepo() as repo:
        return repo.search(keyword=keyword, class_name=class_name, limit=limit)


@server.tool(description="大华 vs 海康任意配对逐字段对比（本地库官网公开参数，未公开字段如实标注，不推断）")
def compare_pair(category: str, dahua_model: str, hik_model: str) -> dict:
    """与 agent/tools.py 的 compare_pair 同源：复用 app.compare_service.pair。"""
    result = _compare_pair_facts(category, dahua_model, hik_model)
    if result.get("error"):
        raise ValueError(result["error"])
    return result


@server.tool(description="按 Spec 渲染系统拓扑图 PNG，返回文件路径")
def render_topology(spec: dict, filename: str | None = None) -> dict:
    parsed = Spec.model_validate(spec)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = _safe_name(filename) or f"{_safe_name(parsed.project.name)}-{_stamp()}-拓扑"
    out = EXPORT_DIR / f"{name}.png"
    _render_topology(parsed, out)
    return {"path": str(out)}


@server.tool(description="按 Spec 生成 Word 方案（可附拓扑图），返回文件路径")
def gen_docx(spec: dict, filename: str | None = None, topology_png: str | None = None) -> dict:
    parsed = Spec.model_validate(spec)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = _safe_name(filename) or f"{_safe_name(parsed.project.name)}-{_stamp()}"
    out = EXPORT_DIR / f"{name}.docx"
    png = Path(topology_png) if topology_png else None
    _render_docx(parsed, out, topology_png=png if png and png.is_file() else None)
    return {"path": str(out)}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_name(text: str | None) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", (text or "").strip()).strip("_")


if __name__ == "__main__":
    server.run()
