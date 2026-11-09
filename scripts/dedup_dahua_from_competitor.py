"""把 `competitor_facts` 里 brand='大华' 的冗余行清出竞品表。

背景：
- `competitor_facts` 语义上应只放「竞争对手（海康）」的官网参数。
- 之前误把 3 款大华代表款（共 273 条参数）也写进了这张表，导致知识库
  「竞品」Tab 把自家产品显示成竞品。
- 竞品*对比*页的大华侧本就优先走 `products` 表（含 spec_value），不依赖本表。
- 因此：先把不在 products 里的大华型号迁进 products（避免丢数据），
  再删除 competitor_facts 里全部 brand='大华' 行。

幂等：已迁移过的型号不会重复插入；已删除的行不会报错。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "db" / "dahua_kb.sqlite"


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1) 找出 competitor_facts 中大华、但 products 里没有的型号
    stray = cur.execute(
        "SELECT DISTINCT model FROM competitor_facts "
        "WHERE brand='大华' AND model NOT IN (SELECT model FROM products)"
    ).fetchall()
    print(f"[1] competitor_facts 中大华、products 缺失的型号数: {len(stray)}")

    migrated = 0
    for (model,) in stray:
        facts = cur.execute(
            "SELECT display_name, category, source_url, field_label, raw_value "
            "FROM competitor_facts WHERE brand='大华' AND model=? ORDER BY id",
            (model,),
        ).fetchall()
        if not facts:
            continue
        display_name = facts[0]["display_name"]
        category = facts[0]["category"]
        source_url = facts[0]["source_url"]
        # 把 字段|值 拼成 products.spec_value 格式（parse_spec_value 可解析）
        spec_lines = [f"{f['field_label']} | {f['raw_value']}" for f in facts]
        spec_value = "\n".join(spec_lines)
        prod_id = f"MIG{datetime.now():%Y%m%d%H%M%S}"
        cur.execute(
            "INSERT INTO products (prod_id, model, display_name, class_name, "
            "source, source_url, spec_value, collected_at, is_virtual) "
            "VALUES (?,?,?,?,?,?,?,?,0)",
            (prod_id, model, display_name, category, "competitor_migrated",
             source_url, spec_value, datetime.now().isoformat(timespec="seconds")),
        )
        migrated += 1
        print(f"    迁移 -> products: {model} ({len(facts)} 条参数, {category})")
    print(f"[2] 已迁移 {migrated} 个大华型号进 products")

    # 2) 删除 competitor_facts 里全部 brand='大华' 行
    before = cur.execute(
        "SELECT COUNT(*) FROM competitor_facts WHERE brand='大华'"
    ).fetchone()[0]
    cur.execute("DELETE FROM competitor_facts WHERE brand='大华'")
    after = cur.execute(
        "SELECT COUNT(*) FROM competitor_facts WHERE brand='大华'"
    ).fetchone()[0]
    conn.commit()
    print(f"[3] 删除 competitor_facts.brand='大华': {before} -> {after} 行")

    # 3) 校验
    total = cur.execute("SELECT COUNT(*) FROM competitor_facts").fetchone()[0]
    hik = cur.execute(
        "SELECT COUNT(*) FROM competitor_facts WHERE brand='海康'"
    ).fetchone()[0]
    assert after == 0, "删除未生效"
    assert total == hik, f"竞品表仍有非海康数据: total={total} hik={hik}"
    print(f"[4] 校验通过：竞品表现在仅含海康 {hik} 条参数")
    conn.close()


if __name__ == "__main__":
    main()
