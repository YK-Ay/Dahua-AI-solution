"""需求 → Spec → 拓扑图 / Word 命令行入口（W2-W3）。

用法：
  python scripts/make_spec.py --text "某园区：周界80路，出入口20路，存储30天"
  python scripts/make_spec.py --text "..." --render          # 同时出 Spec/拓扑图/Word
  python scripts/make_spec.py --text "..." --ask             # 缺口回问后再生成
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.builder import (  # noqa: E402
    DEFAULT_LANES,
    DEFAULT_RETENTION_DAYS,
    SpecGateError,
    build_spec,
    validate_spec,
)
from app.topology import render_topology  # noqa: E402
from app.word import render_docx  # noqa: E402
from config.settings import BASE_DIR  # noqa: E402

DEMO_TEXT = "某智慧园区综合安防项目：园区周界80路，出入口20路，主干道30路，室内大厅40路，存储30天。"


def print_summary(spec) -> None:
    print(f"\n项目：{spec.project.name}（{spec.project.industry}）")
    print(f"辅标签：{'、'.join(spec.project.industry_tags) or '-'}")
    print("\n需求分解：")
    for r in spec.requirements:
        print(f"  - {r.target}：{r.intent}，{r.lanes} 路，{r.bitrate_mbps} Mbps，级别 {r.level}")
    print("\n设备清单：")
    cost = 0.0
    for d in spec.devices:
        line = f"  - {d.category:8s} {d.model:28s} ×{d.qty:<4d} {d.display_name}"
        if d.unit_price:
            cost += d.unit_price * d.qty
            line += f"（单价 {d.unit_price:.2f}）"
        print(line)
    print(f"\n设备小计（标准价，未税/未折扣）：{cost:,.2f} 元")
    print(f"存储：{spec.storage.raw_tb} TB（{spec.storage.retention_days} 天，{spec.storage.codec}），"
          f"含 {spec.storage.redundancy} 冗余后 {spec.storage.with_redundancy_tb} TB")
    print(f"上行带宽：{spec.network.uplink_mbps} Mbps")
    print(f"\n交付边界：含 {len(spec.boundaries.included)} 项，不含 {'、'.join(spec.boundaries.excluded)}")
    print("\n假设：")
    for a in spec.boundaries.assumptions:
        print(f"  - {a}")
    if spec.gaps:
        print("\n缺口（需售前补充确认）：")
        for g in spec.gaps:
            print(f"  ! {g}")
    else:
        print("\n缺口：无")


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return cleaned or "未命名方案"


def ask_missing(spec, lanes: int | None, days: int | None) -> tuple[int | None, int | None]:
    """对「未写点位数 / 未写存储天数」两类缺口逐个回问，回车则沿用当前估算。"""
    for gap in spec.gaps:
        if "未写点位数" in gap and lanes is None:
            raw = input(f"  ? 请确认总点位数（当前按 {DEFAULT_LANES} 路估算，回车沿用）：").strip()
            if raw.isdigit() and int(raw) > 0:
                lanes = int(raw)
        elif "未写存储天数" in gap and days is None:
            raw = input(f"  ? 请确认存储天数（当前按 {DEFAULT_RETENTION_DAYS} 天测算，回车沿用）：").strip()
            if raw.isdigit() and int(raw) > 0:
                days = int(raw)
    return lanes, days


def main() -> int:
    ap = argparse.ArgumentParser(description="需求 → Spec → 拓扑图 / Word")
    ap.add_argument("--text", default=DEMO_TEXT, help="客户需求描述")
    ap.add_argument("--name", help="项目名（默认按行业生成）")
    ap.add_argument("--lanes", type=int, help="总点位数（覆盖文本解析）")
    ap.add_argument("--days", type=int, help="存储天数（覆盖文本解析）")
    ap.add_argument("--ask", action="store_true", help="对缺失信息进行终端回问")
    ap.add_argument("--render", action="store_true", help="同时输出 Spec JSON、拓扑图 PNG、Word DOCX")
    ap.add_argument("--out", help="仅输出 Spec JSON 到指定路径")
    args = ap.parse_args()

    lanes, days = args.lanes, args.days
    spec = build_spec(args.text, name=args.name, lanes=lanes, retention_days=days)
    if args.ask and spec.gaps:
        new_lanes, new_days = ask_missing(spec, lanes, days)
        if (new_lanes, new_days) != (lanes, days):
            lanes, days = new_lanes, new_days
            spec = build_spec(args.text, name=args.name, lanes=lanes, retention_days=days)

    try:
        validate_spec(spec)
    except SpecGateError as e:
        print("硬闸门拦截：")
        for g in e.gaps:
            print(f"  ! {g}")
        return 1

    print_summary(spec)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        print(f"\nSpec 已写入：{out.resolve()}")

    if args.render:
        stem = _safe_name(spec.project.name)
        spec_dir = BASE_DIR / "data" / "specs"
        export_dir = BASE_DIR / "data" / "exports"
        spec_dir.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)

        spec_path = spec_dir / f"{stem}.json"
        png_path = export_dir / f"{stem}-拓扑.png"
        docx_path = export_dir / f"{stem}.docx"

        spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        render_topology(spec, png_path)
        render_docx(spec, docx_path, topology_png=png_path)

        for p in (spec_path, png_path, docx_path):
            size = p.stat().st_size
            print(f"  → {p.relative_to(BASE_DIR)}  ({size / 1024:.1f} KB)")
    elif not args.out:
        print("\n提示：加 --render 可同时生成拓扑图与 Word。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
