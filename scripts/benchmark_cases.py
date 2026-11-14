"""运行无密合成案例，检查规则引擎是否能稳定产出或明确报缺口。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.builder import SpecGateError, build_spec


def main() -> int:
    path = Path(__file__).resolve().parents[1] / "data" / "benchmark_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    failed = 0
    for case in cases:
        try:
            spec = build_spec(case["input"])
            lanes = sum(r.lanes for r in spec.requirements)
            print(f"[OK] {case['id']} · {lanes} 路 · {len(spec.devices)} 类设备 · 缺口 {len(spec.gaps)}")
        except SpecGateError as exc:
            failed += 1
            print(f"[GAP] {case['id']} · {exc}")
        except Exception as exc:  # noqa: BLE001 —— 基准报告必须继续跑完
            failed += 1
            print(f"[ERROR] {case['id']} · {type(exc).__name__}: {exc}")
    print(f"\n合成案例：{len(cases)} 个，明确缺口/失败：{failed} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
