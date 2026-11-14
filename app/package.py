"""可交付方案包：一次生成 Word / 报价单 / 点位表 / 拓扑 / PPT，并打包 ZIP。

投标与汇报常用的交付物在这里统一命名，ZIP 内使用稳定名称，
外部导出目录仍保留带时间戳的文件名，避免覆盖历史版本。
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

from .excel import render_engineering_check_xlsx, render_point_table_xlsx, render_quote_xlsx
from .models import Spec
from .ppt import render_pptx
from .topology import render_topology
from .word import render_docx

ZIP_NAMES = {
    "docx": "方案书.docx",
    "quote": "报价单.xlsx",
    "points": "点位表.xlsx",
    "engineering": "工程审查清单.xlsx",
    "png": "系统拓扑.png",
    "png_detail": "系统拓扑-详细.png",
    "pptx": "方案汇报.pptx",
}


def safe_stem(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name or "").strip("_")
    return cleaned or "未命名方案"


def build_deliverables(spec: Spec, export_dir: Path | str) -> dict[str, str]:
    """生成全部交付物（含 ZIP），返回 kind → 绝对路径。"""
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")[:-3]
    stem = safe_stem(spec.project.name)

    paths = {
        "png": export_dir / f"{stem}-{stamp}-拓扑-简略.png",
        "png_detail": export_dir / f"{stem}-{stamp}-拓扑-详细.png",
        "docx": export_dir / f"{stem}-{stamp}.docx",
        "pptx": export_dir / f"{stem}-{stamp}.pptx",
        "quote": export_dir / f"{stem}-{stamp}-报价单.xlsx",
        "points": export_dir / f"{stem}-{stamp}-点位表.xlsx",
        "engineering": export_dir / f"{stem}-{stamp}-工程审查清单.xlsx",
    }
    render_topology(spec, paths["png"], mode="simple")
    render_topology(spec, paths["png_detail"], mode="detailed")
    render_docx(spec, paths["docx"], topology_png=paths["png"])
    render_pptx(spec, paths["pptx"])
    render_quote_xlsx(spec, paths["quote"])
    render_point_table_xlsx(spec, paths["points"])
    render_engineering_check_xlsx(spec, paths["engineering"])

    package = export_dir / f"{stem}-{stamp}-方案包.zip"
    build_zip(paths, package, spec)
    outputs = {k: str(v) for k, v in paths.items()}
    outputs["package"] = str(package)
    return outputs


def build_zip(paths: dict[str, Path], out_path: Path, spec: Spec) -> Path:
    """按稳定名称打包；附一页交付清单，说明口径与待人工确认项。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for kind, arcname in ZIP_NAMES.items():
            path = paths.get(kind)
            if path and Path(path).is_file():
                zf.write(path, arcname)
        zf.writestr("交付说明.txt", _readme(spec))
    return out_path


def _readme(spec: Spec) -> str:
    lanes = sum(r.lanes for r in spec.requirements)
    devices = sum(d.qty for d in spec.devices)
    lines = [
        f"{spec.project.name} · 交付清单",
        f"生成日期：{datetime.now():%Y-%m-%d %H:%M}",
        f"行业：{spec.project.industry}　点位数：{lanes}　设备数量：{devices}",
        "",
        "包内文件：",
        "  方案书.docx      技术方案（封面、目录、设计依据、点位、设备、施工、售后）",
        "  报价单.xlsx      设备明细与标准价合计（未税、未折扣）",
        "  点位表.xlsx      按场景统计点位数、码流与存储容量",
        "  工程审查清单.xlsx  NVR、存储、网络、证据与待确认项",
        "  系统拓扑.png     建议拓扑（分层）",
        "  系统拓扑-详细.png 工程拓扑（设备连线）",
        "  方案汇报.pptx    汇报用演示稿",
        "",
        "使用口径：",
        "  1. 设备型号与标准价来自本地知识库公开资料，生成过程不联网。",
        "  2. 存储、带宽、容量由确定性公式计算，未使用估算或模型生成数字。",
        "  3. 同一分类下的多个型号为可替换候选，应择一选型，不可累加为采购总价。",
        "",
        "交付前须人工确认：",
        "  · 招标文件条款逐条响应（技术偏离表另行导出）",
        "  · 现场勘察后的点位坐标、楼栋与安装方式",
        "  · 渠道价、税率、施工与维保费用",
        "  · 平台授权、license 与服务项是否完整",
    ]
    if spec.gaps:
        lines += ["", "当前缺口："] + [f"  · {g}" for g in spec.gaps]
    return "\n".join(lines) + "\n"
