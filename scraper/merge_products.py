"""把 collect_public_products.py 采集到的候选**安全地合并**进 products 表。

原则（重要）
----------
1. 只新增，不覆盖：已有 prod_id / 已有型号的产品，一个字段都不改。
2. 只补空：对已有产品，仅当 prod_desc / source / source_url 为空时才补详情。
3. 先备份数据库，随时可回滚。
4. 每条新增都带 source 来源标记，方便以后追溯与清理。

用法：
  python scraper/merge_products.py --dry-run          # 只看会新增多少，不写库
  python scraper/merge_products.py                   # 正式合并（自动备份）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "db" / "dahua_kb.sqlite"
DEFAULT_JSON = ROOT / "data" / "public_products.json"

TRAFFIC_KEYWORDS = ["车行出入口", "抓拍", "道闸", "智能交通", "智慧停车", "电子警察", "车位"]
PARKING_KEYWORDS = [
    "网络摄像机", "球机", "录像机", "NVR", "硬盘", "PoE", "交换机",
    "人行出入口", "门禁", "商显", "显示器", "综合平台", "报警", "配件",
    "布线", "机柜", "电源", "支架", "拾音", "AP", "路由器", "光纤", "无线",
]

NEW_COLUMNS = [("source", "TEXT"), ("prod_desc", "TEXT"),
               ("source_url", "TEXT"), ("spec_value", "TEXT")]


def classify(class_name):
    cn = class_name or ""
    if any(k in cn for k in TRAFFIC_KEYWORDS):
        return "交管"
    if any(k in cn for k in PARKING_KEYWORDS):
        return "园区楼宇"
    return "其他"


def ensure_columns(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(products)")}
    added = []
    for name, typ in NEW_COLUMNS:
        if name not in cols:
            con.execute(f"ALTER TABLE products ADD COLUMN {name} {typ}")
            added.append(name)
    return added


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--json", default=str(DEFAULT_JSON))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    js = Path(args.json)
    if not js.exists():
        raise SystemExit(f"找不到采集结果：{js}\n请先跑 collect_public_products.py")
    data = json.loads(js.read_text(encoding="utf-8"))
    cands = data.get("products") or []
    print(f"候选产品：{len(cands)} 条（采集于 {data.get('collected_at')}）")

    db = Path(args.db)
    con = sqlite3.connect(db)
    before = con.execute("select count(*) from products").fetchone()[0]
    existing_ids = {r[0] for r in con.execute("select prod_id from products")}
    existing_models = {r[0] for r in con.execute(
        "select model from products where model is not null and model!=''")}
    print(f"当前 products：{before} 条 | 不同型号 {len(existing_models)} 个")

    to_insert, to_enrich, skipped = [], [], 0
    seen_ids, seen_models = set(), set()
    for c in cands:
        pid, model = c.get("prod_id"), c.get("model")
        if not pid or not model:
            skipped += 1
            continue
        if pid in existing_ids:
            # 已有记录：只补空字段，绝不覆盖
            if c.get("prod_desc"):
                to_enrich.append((c["prod_desc"], c.get("source_url"), c.get("spec_value"), pid))
            continue
        if model in existing_models or model in seen_models or pid in seen_ids:
            skipped += 1
            continue
        seen_ids.add(pid)
        seen_models.add(model)
        year = None
        if c.get("pi_sysj"):
            try:
                year = int(str(c["pi_sysj"])[:4])
            except ValueError:
                pass
        to_insert.append((
            pid, model, c.get("display_name"), num(c.get("price_stand")),
            c.get("pi_sysj"), year, c.get("pi_status"), c.get("class_name"),
            json.dumps(c.get("catalog_ids") or [], ensure_ascii=False),
            c.get("img_url"), c.get("part_num"), classify(c.get("class_name")),
            c.get("collected_at") or datetime.now().isoformat(timespec="seconds"),
            c.get("source"), c.get("prod_desc"), c.get("source_url"), c.get("spec_value"),
        ))

    print(f"\n计划：新增 {len(to_insert)} 条 | 给已有记录补详情 {len(to_enrich)} 条 | 跳过(重复/无型号) {skipped} 条")
    if to_insert:
        print("新增样例：")
        for r in to_insert[:5]:
            print(f"  {r[1][:30]:30} | {str(r[2])[:34]:34} | ¥{r[3]} | {r[7]}")
    if args.dry_run:
        print("\n[空跑] 未写入数据库。")
        con.close()
        return

    if not args.no_backup:
        bak = db.with_name(db.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        con.close()  # 关闭后复制，保证文件完整
        shutil.copy2(db, bak)
        print(f"\n已备份 → {bak}")
        con = sqlite3.connect(db)

    added_cols = ensure_columns(con)
    if added_cols:
        print(f"新增列（不影响旧数据）：{', '.join(added_cols)}")

    con.executemany(
        """INSERT OR IGNORE INTO products
           (prod_id, model, display_name, price_stand, pi_sysj, year_new, pi_status,
            class_name, catalog_ids, img_url, part_num, direction, collected_at,
            source, prod_desc, source_url, spec_value)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", to_insert)
    # 只补空字段：COALESCE 保证已有值不被覆盖
    con.executemany(
        """UPDATE products SET
             prod_desc  = COALESCE(prod_desc, ?),
             source_url = COALESCE(source_url, ?),
             spec_value = COALESCE(spec_value, ?)
           WHERE prod_id = ? AND (prod_desc IS NULL OR prod_desc = '')""", to_enrich)
    con.commit()

    after = con.execute("select count(*) from products").fetchone()[0]
    print(f"\n合并完成：{before} → {after} 条（净增 {after - before}）")
    print("按来源统计：")
    for r in con.execute("select coalesce(source,'legacy') s, count(*) from products group by s order by 2 desc"):
        print(f"  {r[0]:22} {r[1]} 条")
    print("\n新增数据字段完整度：")
    for col in ("img_url", "price_stand", "class_name", "part_num", "prod_desc"):
        n = con.execute(
            f"select count(*) from products where source in ('mall_public_search','mall_cloud_choice','official_catalog') and {col} is not null and {col}!=''").fetchone()[0]
        print(f"  {col:14} {n}/{len(to_insert)}")
    con.close()


if __name__ == "__main__":
    main()
