"""配单型号 ↔ 产品库对齐（B 阶段前置）。

背景
----
配单 1093 条明细里有 88 个型号在产品库中找不到。核对原始响应后发现，
它们几乎全是 `salesOneType=项目产品 / source=PRM` —— 也就是集成商认证后
才在 partner 站展示的那批产品。但**用户自己登录后导出的配单响应里，每个
产品都自带完整产品卡片**（prodId / 型号 / 名称 / 图片 / 简介 / 规格 / 标准价
/ 物料号）。因此无需任何鉴权绕过：用配单原文就能把它们补齐。

本脚本做三件事（全部幂等，可反复跑）：
  1. 给 bom_items 补 price_basis / matched_prod_id / match_type 列
  2. 多路匹配：型号 → prodId → 物料号 partNum → modelInside
  3. 仍未命中的，用配单 columns 原文生成产品记录写入 products（只新增、
     不覆盖），并回填 matched_prod_id

来源标注：
  - source='bom_prm'       真实项目产品
  - source='bom_virtual'   虚拟项/服务/软件/套件编号，is_virtual=1
  - description 里保留 salesOneType 与出处说明，供人工复核
  - 绝不编造任何字段：配单里没有的字段留空

用法：
  python scraper/align_bom_products.py [--dry-run] [--db ...]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from config.settings import DB_PATH  # noqa: E402

TAG_RE = re.compile(r"<[^>]+>")
VIRTUAL_S_RE = re.compile(r"^S\d{15,}$")
NUMERIC_RE = re.compile(r"^[\d.\-/]+$")


def clean(v) -> str:
    if v is None:
        return ""
    return TAG_RE.sub("", str(v)).strip()


def norm(v) -> str:
    return re.sub(r"\s+", "", clean(v)).upper()


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
        return float(m.group()) if m else None


def is_virtual(model: str, c: dict) -> bool:
    """判断是不是「不该被当成可选设备」的虚拟项/服务项。"""
    m = clean(model)
    if not m or m in ("无", "-", "/", "待定", "大华云联"):
        return True
    if m.isdigit() or VIRTUAL_S_RE.match(m) or NUMERIC_RE.match(m):
        return True
    # 中文名称直接被当成型号（如「扫码枪」「视频追溯软件」）
    if re.search(r"[\u4e00-\u9fff]", m) and not re.match(r"^(DH|DHI|PFM)-", m, re.I):
        return True
    if str(c.get("realFlag", "")) in ("0", "False", "false"):
        return True
    return False


def ensure_columns(con: sqlite3.Connection) -> None:
    bi = {r[1] for r in con.execute("PRAGMA table_info(bom_items)")}
    for col, typ in (("price_basis", "TEXT"), ("matched_prod_id", "TEXT"), ("match_type", "TEXT")):
        if col not in bi:
            con.execute(f"ALTER TABLE bom_items ADD COLUMN {col} {typ}")
    pr = {r[1] for r in con.execute("PRAGMA table_info(products)")}
    if "is_virtual" not in pr:
        con.execute("ALTER TABLE products ADD COLUMN is_virtual INTEGER DEFAULT 0")
    con.commit()


def build_lookups(con):
    """型号 / prodId / 物料号三张精确匹配索引 + 产品来源表。"""
    by_model, by_pid, by_part, pid_source = {}, {}, {}, {}
    for pid, model, part, source in con.execute(
        "SELECT prod_id, model, part_num, source FROM products"
    ):
        if pid:
            by_pid[str(pid)] = pid
            pid_source[str(pid)] = source or "legacy"
        if model:
            by_model.setdefault(norm(model), pid)
        if part:
            by_part.setdefault(norm(part), pid)
    return by_model, by_pid, by_part, pid_source


def product_from_bom(c: dict, scheme_url: str, today: str, virtual: bool):
    """用配单原始 columns 生成一条 products 记录（只填原文里真实存在的字段）。"""
    model = clean(c.get("model") or c.get("modelInside") or c.get("partNum"))
    pid = clean(c.get("prodId") or c.get("productId"))
    if not pid:
        pid = "bom:" + (model or clean(c.get("partNum")) or str(abs(hash(json.dumps(c, sort_keys=True, ensure_ascii=False)))))
    name = clean(c.get("displayName") or c.get("name") or c.get("xName")) or model
    price = to_float(c.get("priceStand"))
    if not price or price <= 0:
        price = to_float(c.get("cloudPrice")) or None
    desc = clean(c.get("prodDesc"))
    feature = clean(c.get("html"))
    prod_desc = "\n".join(x for x in (feature, desc) if x) or None
    meta = {
        "salesOneType": clean(c.get("salesOneType")),
        "source": clean(c.get("source")),
        "from": "大华云商方案配单原始响应 columns（非产品目录爬取）",
        "note": "项目产品/虚拟项，投标前请人工复核参数与报价" if not virtual else "虚拟项/服务项，不作为可选设备",
    }
    return {
        "prod_id": pid,
        "model": model,
        "display_name": name,
        "price_stand": price,
        "pi_sysj": None,
        "year_new": None,
        "pi_status": clean(c.get("piStatus")) or None,
        "class_name": clean(c.get("className") or c.get("piCppm")) or None,
        "catalog_ids": "[]",
        "img_url": clean(c.get("image")) or None,
        "part_num": clean(c.get("partNum")) or None,
        "direction": None,
        "collected_at": today,
        "description": json.dumps(meta, ensure_ascii=False),
        "source": "bom_virtual" if virtual else "bom_prm",
        "prod_desc": prod_desc,
        "source_url": scheme_url,
        "spec_value": clean(c.get("specValue")) or None,
        "is_virtual": 1 if virtual else 0,
    }


INSERT_SQL = """INSERT OR IGNORE INTO products
 (prod_id,model,display_name,price_stand,pi_sysj,year_new,pi_status,class_name,
  catalog_ids,img_url,part_num,direction,collected_at,description,source,
  prod_desc,source_url,spec_value,is_virtual)
 VALUES (:prod_id,:model,:display_name,:price_stand,:pi_sysj,:year_new,:pi_status,
  :class_name,:catalog_ids,:img_url,:part_num,:direction,:collected_at,:description,
  :source,:prod_desc,:source_url,:spec_value,:is_virtual)"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    a = ap.parse_args()
    db = Path(a.db)

    print(f"数据库：{db}")
    if not a.dry_run and not a.no_backup:
        bak = db.with_name(db.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(db, bak)
        print(f"已备份：{bak.name}")

    con = sqlite3.connect(str(db))
    if not a.dry_run:
        ensure_columns(con)

    scheme_urls = dict(con.execute("SELECT scheme_id, source_url FROM boms").fetchall())
    by_model, by_pid, by_part, pid_source = build_lookups(con)
    today = date.today().isoformat()

    rows = con.execute(
        "SELECT id, scheme_id, model, raw_json FROM bom_items ORDER BY id"
    ).fetchall()
    stats = Counter()
    new_products, updates = {}, []
    still_missing = []

    for item_id, sid, model, raw in rows:
        try:
            c = (json.loads(raw).get("columns") or {}) if raw else {}
        except Exception:
            c = {}
        m = norm(model)
        pid = clean(c.get("prodId") or c.get("productId"))
        part = norm(c.get("partNum"))
        inside = norm(c.get("modelInside"))

        matched, how = None, None
        if m and m in by_model:
            matched, how = by_model[m], "model"
        elif pid and pid in by_pid:
            matched, how = by_pid[pid], "prod_id"
        elif part and part in by_part:
            matched, how = by_part[part], "part_num"
        elif inside and inside in by_model:
            matched, how = by_model[inside], "model_inside"

        if not matched:
            virtual = is_virtual(model, c)
            rec = product_from_bom(c, scheme_urls.get(sid, ""), today, virtual)
            # 冲突保护：prodId 已被库里别的产品占用时改用合成键
            if rec["prod_id"] in by_pid:
                rec["prod_id"] = "bom:" + (m or part or str(item_id))
            # 同一 prodId 只生成一次（多个方案共用同一产品）
            new_products.setdefault(rec["prod_id"], rec)
            matched = rec["prod_id"]
            how = "bom_virtual" if virtual else "bom_prm"
            still_missing.append((model, how, clean(c.get("displayName"))[:26]))
            # 让后续同型号明细直接命中，避免重复生成
            if m:
                by_model[m] = matched
            if pid:
                by_pid[pid] = matched

        # 命中到「之前从配单补进来的产品」时，保留该来源语义，便于统计
        src = pid_source.get(matched)
        if src in ("bom_prm", "bom_virtual"):
            how = src
        stats[how] += 1
        updates.append((matched, how, item_id))

    print("\n=== 明细对齐结果 ===")
    print(f"  总明细：{len(rows)} 条")
    for k, v in stats.most_common():
        print(f"    {k:14} {v:5} 条")
    print(f"  生成新产品：{len(new_products)} 款"
          f"（真实项目产品 {sum(1 for r in new_products.values() if not r['is_virtual'])} / "
          f"虚拟项 {sum(1 for r in new_products.values() if r['is_virtual'])}）")
    print("\n  补齐样例（前 8）：")
    for m, how, name in still_missing[:8]:
        print(f"    {str(m)[:30]:30} {how:12} {name}")

    if a.dry_run:
        print("\n（dry-run，未写入数据库）")
        con.close()
        return

    con.executemany(INSERT_SQL, list(new_products.values()))
    con.executemany(
        "UPDATE bom_items SET matched_prod_id=?, match_type=? WHERE id=?", updates
    )
    con.commit()

    # ---------- 验证 ----------
    total = con.execute("SELECT count(*) FROM bom_items").fetchone()[0]
    linked = con.execute(
        "SELECT count(*) FROM bom_items WHERE matched_prod_id IS NOT NULL AND matched_prod_id!=''"
    ).fetchone()[0]
    orphan = con.execute(
        """SELECT count(*) FROM bom_items i LEFT JOIN products p
           ON p.prod_id=i.matched_prod_id WHERE p.prod_id IS NULL"""
    ).fetchone()[0]
    zero = con.execute("SELECT count(*) FROM bom_items WHERE unit_price=0").fetchone()[0]
    print("\n=== 落库校验 ===")
    print(f"  products 总数：{con.execute('SELECT count(*) FROM products').fetchone()[0]}")
    print(f"  明细已关联产品：{linked}/{total}（{linked / total * 100:.1f}%）")
    print(f"  悬空关联：{orphan}")
    print(f"  单价仍为 0：{zero} 条（需先重跑 import_bom.py 才会修正）")
    print("\n  来源分布：")
    for r in con.execute("SELECT coalesce(source,'legacy'),count(*) FROM products GROUP BY 1 ORDER BY 2 DESC"):
        print("   ", r)
    con.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
