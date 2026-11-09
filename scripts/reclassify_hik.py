"""按修正后的型号前缀规则，重打海康机型的类目标签。

背景：旧 classify() 把裸 "POE" 关键词放在交换机规则第一位，
任何支持 PoE 供电的摄像机 / NVR 都被误分到「PoE交换机」，
污染了竞品对比的类目池。本脚本幂等、可重复运行，改前自动备份。

用法：python scripts/reclassify_hik.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import DB_PATH  # noqa: E402
from scraper.collect_hik_catalog import classify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = Path(DB_PATH)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT model, display_name, category FROM competitor_products WHERE brand='海康'"
    ).fetchall()
    before = {}
    for r in con.execute(
        "SELECT category, COUNT(*) c FROM competitor_products WHERE brand='海康' GROUP BY category"
    ):
        before[r[0]] = r[1]
    print("重分类前：", before)

    changes = []
    for r in rows:
        new = classify(r["model"], r["display_name"])
        if new != r["category"]:
            changes.append((r["model"], r["category"], new))
    print(f"待修正 {len(changes)} 款，样例：")
    for m, old, new in changes[:12]:
        print(f"  {m:36} {old} → {new}")

    if args.dry_run:
        print("[空跑] 未写库")
        con.close()
        return 0

    bak = db.with_name(db.name + ".bak-reclass-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    con.close()
    shutil.copy2(db, bak)
    print("已备份 →", bak)

    con = sqlite3.connect(str(db))
    for model, _old, new in changes:
        con.execute("UPDATE competitor_products SET category=? WHERE model=?", (new, model))
        con.execute(
            "UPDATE competitor_facts SET category=? WHERE brand='海康' AND model=?",
            (new, model),
        )
    con.commit()
    after = {}
    for r in con.execute(
        "SELECT category, COUNT(*) c FROM competitor_products WHERE brand='海康' GROUP BY category"
    ):
        after[r[0]] = r[1]
    print("重分类后：", after)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
