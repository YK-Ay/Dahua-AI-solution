"""把大华代表款的官网公开参数从备份库回灌进 products.spec_value。

背景：
- `scripts/dedup_dahua_from_competitor.py` 把 competitor_facts 里 brand='大华'
  的行清出了竞品表（竞品表只该放海康）。
- 大华代表款的官网参数本应落在 products.spec_value，但 PoE 交换机代表款
  DH-AS3900C-24GT4GF-370 在 products 里 spec_value 为空，参数只存在于竞品表
  （已被删）。竞品*对比*页的大华侧依赖 products.spec_value，缺了会生成空对比表。
- 本脚本从删除前的备份库读取大华参数，回灌进 products.spec_value（只补空值，
  不覆盖已有 spec_value），避免真实功能回归。

幂等：已存在 spec_value 的型号不会被覆盖。
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

BASE = Path(__file__).resolve().parent.parent
MAIN = BASE / "db" / "dahua_kb.sqlite"
BACKUP = BASE / "db" / "dahua_kb.sqlite.BEFORE-DEL-DAHUA-COMP-20261106.bak"


def main() -> None:
    assert BACKUP.exists(), f"找不到备份库: {BACKUP}"
    bk = sqlite3.connect(BACKUP)
    bk.row_factory = sqlite3.Row
    main_conn = sqlite3.connect(MAIN)
    main_conn.row_factory = sqlite3.Row
    cur = main_conn.cursor()

    rows = bk.execute(
        "SELECT model, category, display_name, source_url, field_label, raw_value "
        "FROM competitor_facts WHERE brand='大华' ORDER BY model, id"
    ).fetchall()

    # 按型号聚合
    by_model: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)

    updated, skipped, missing = 0, 0, 0
    for model, facts in by_model.items():
        existing = cur.execute(
            "SELECT spec_value, class_name FROM products WHERE model=?",
            (model,),
        ).fetchone()
        if not existing:
            missing += 1
            continue
        if existing["spec_value"]:
            skipped += 1
            continue
        spec_value = "\n".join(f"{f['field_label']} | {f['raw_value']}" for f in facts)
        cur.execute(
            "UPDATE products SET spec_value=?, class_name=COALESCE(NULLIF(class_name,''), ?), "
            "source_url=COALESCE(NULLIF(source_url,''), ?) WHERE model=?",
            (spec_value, facts[0]["category"], facts[0]["source_url"], model),
        )
        updated += 1
        print(f"    回灌 {model}: {len(facts)} 条参数 -> products.spec_value")

    main_conn.commit()
    print(f"完成：更新 {updated} 款，跳过(已有spec) {skipped} 款，products缺失 {missing} 款")

    # 校验：三款代表款都应有 spec_value
    for model in ("DH-AS3900C-24GT4GF-370", "DH-IPC-HFW5443M1-YL-PV-AS", "DH-NVR5532-I"):
        ok = cur.execute(
            "SELECT COUNT(*) FROM products WHERE model=? AND spec_value IS NOT NULL AND spec_value<>''",
            (model,),
        ).fetchone()[0]
        print(f"    校验 {model}: spec_value {'OK' if ok else '缺失!'}")
    main_conn.close()
    bk.close()


if __name__ == "__main__":
    main()
