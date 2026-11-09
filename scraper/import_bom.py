"""配单导入：把浏览器脚本导出的 dahua_boms.json 按《配单数据规范》转结构化表。

用法：
  python scraper/import_bom.py --json dahua_boms.json [--index 大华云商方案索引_53个.json] [--db ...] [--dry-run]

- 数据来源：大华云商 mall.dahuatech.com（需你本人登录后跑导出脚本得到 dahua_boms.json）
- 入库：boms(方案级) + bom_items(配单明细)，用 scheme_id 关联
- 价格/型号只取自原始数据，不编造；字段名未知时做启发式映射并保留 raw_json
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from config.settings import DB_PATH  # noqa: E402


def dig(obj, path):
    if not path:
        return None
    cur = obj
    for part in path.replace("[", ".").replace("]", "").split("."):
        part = part.strip()
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def iter_tree_products(node, trail=None):
    """遍历云商配单的分类树：data -> 分类 -> childrenList -> lectotypeProductList[].columns

    云商返回的不是扁平数组，而是「场景分类 -> 子分类 -> 候选产品」的层级结构，
    真正的产品信息（型号/价格/规格/图片/简介）藏在产品条目的 columns 里。
    """
    trail = trail or []
    if isinstance(node, list):
        for x in node:
            yield from iter_tree_products(x, trail)
        return
    if not isinstance(node, dict):
        return
    name = str(node.get("name") or "").strip()
    cur = trail + [name] if name else trail
    for p in (node.get("lectotypeProductList") or []):
        if isinstance(p, dict):
            yield cur, p
    for ch in (node.get("childrenList") or []):
        yield from iter_tree_products(ch, cur)


def find_bom_arrays(obj, hits=None, path=""):
    if hits is None:
        hits = []
    if isinstance(obj, list):
        if obj:
            sample = next((x for x in obj if isinstance(x, dict)), None)
            if sample:
                keys = [k.lower() for k in sample.keys()]
                has_model = any(k in ("model", "modelname", "productmodel", "prodmodel", "spec", "specification") for k in keys)
                has_qty = any(k in ("qty", "quantity", "num", "count", "amount", "number") for k in keys)
                has_price = any("price" in k or k in ("cost", "priceind") for k in keys)
                has_name = any(k in ("name", "productname", "prodname", "title") for k in keys)
                if (has_model and has_qty) or (has_model and has_price) or (has_name and has_qty and has_price):
                    hits.append({"path": path, "count": len(obj), "sample": sample})
        for i, v in enumerate(obj):
            find_bom_arrays(v, hits, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            find_bom_arrays(v, hits, f"{path}.{k}" if path else k)
    return hits


def to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(\.\d+)?", str(v).replace(",", "").strip())
    return float(m.group()) if m else None


def g(item, *keys):
    if not isinstance(item, dict):
        return ""
    for k in keys:
        v = next((item[kk] for kk in item if kk.lower() == k), None)
        if v not in (None, ""):
            return v
    return ""


def pick_price(c):
    """取配单里第一个真实有效（>0）的价格，并记住它的口径。

    云商「项目产品」的 cloudPrice 常为 0（表示不公开分销价），但同一条里有
    priceStand 标准价。早期版本直接取 cloudPrice 导致 70 条单价被记成 ¥0，
    这里按优先级取正数，并把字段名写进 price_basis 供生成层如实标注。
    """
    # 注意：g() 内部按 key.lower() 比较，这里必须传全小写字段名
    for key, label in (
        ("cloudprice", "云商分销价"),
        ("pricestand", "标准价(项目产品)"),
        ("priceguidelow", "指导价下限"),
        ("priceguide", "指导价"),
    ):
        n = to_num(g(c, key))
        if n and n > 0:
            return n, label
    return None, ""


def build_plan(rows, index_map):
    plan = []
    for r in rows:
        scheme_id = r.get("scheme_id")
        detail = r.get("detail") or {}
        idx = index_map.get(scheme_id, {})
        scheme_name = idx.get("name") or r.get("scheme_name") or ""
        industry = idx.get("cat") or r.get("industry") or ""
        view = idx.get("view") or r.get("view_count") or 0

        norm = []
        bom_path = None
        tree = list(iter_tree_products(detail.get("data")))

        if tree:
            # 真实结构：分类树 + columns
            bom_path = "data[*].childrenList[*].lectotypeProductList[].columns"
            for i, (trail, p) in enumerate(tree):
                c = p.get("columns") or {}
                qty = to_num(g(p, "count", "qty", "quantity", "num") or g(c, "count"))
                price, price_basis = pick_price(c)
                norm.append({
                    "seq": i + 1,
                    "category": " > ".join(trail),
                    "product_name": g(c, "name", "displayname") or g(p, "displayname"),
                    "model": g(c, "model", "modelinside", "partnum"),
                    "spec": g(c, "specvalue", "spec"),
                    "unit": g(c, "unit"),
                    "qty": qty,
                    "unit_price": price,
                    "price_basis": price_basis,
                    "amount": round(qty * price, 2) if (qty is not None and price is not None) else None,
                    "role": trail[-1] if trail else "",
                    # 关键语义：同分类下多型号 = 可替换候选，不是确定采购清单
                    "remark": "云商选型池：同一分类下多个型号为可替换候选，非确定采购清单",
                    "raw": {"columns": c,
                            "node": {k: p.get(k) for k in ("count", "partNum", "isVirtual", "choiceCode")}},
                })
        else:
            # 兼容旧结构 / 未来字段变化：启发式找扁平配单数组
            bom = None
            cands = r.get("bom_candidates") or []
            if cands:
                bom = dig(detail, cands[0].get("path"))
                bom_path = cands[0].get("path")
            if not isinstance(bom, list) or not bom:
                hits = find_bom_arrays(detail)
                if hits:
                    bom_path = hits[0]["path"]
                    bom = dig(detail, bom_path)
            items = bom if isinstance(bom, list) else []
            for i, it in enumerate(items):
                norm.append({
                    "seq": i + 1,
                    "category": g(it, "category", "cat", "type"),
                    "product_name": g(it, "name", "productname", "prodname", "title"),
                    "model": g(it, "model", "modelname", "productmodel", "prodmodel"),
                    "spec": g(it, "spec", "specification", "specname"),
                    "unit": g(it, "unit", "unitname"),
                    "qty": to_num(g(it, "qty", "quantity", "num", "count", "number")),
                    "unit_price": to_num(g(it, "price", "unitprice", "saleprice", "priceind")),
                    "price_basis": "配单表原始单价",
                    "amount": to_num(g(it, "amount", "totalprice", "sum")),
                    "role": "",
                    "remark": "",
                    "raw": it,
                })

        plan.append({
            "scheme_id": scheme_id, "scheme_name": scheme_name, "industry": industry,
            "view": view, "item_count": len(norm),
            "bom_path": bom_path,
            "items": norm,
        })
    return plan


def report(plan):
    print(f"读取方案 {len(plan)} 个")
    ok = [p for p in plan if p["item_count"] > 0]
    print(f"识别到配单的：{len(ok)} 个；空配单：{len(plan) - len(ok)} 个")
    for p in plan[:5]:
        print(f"  {str(p['scheme_id'])[:8]}… {p['scheme_name']} | 行业={p['industry']} | 配单={p['item_count']} 条 | 路径={p['bom_path']}")
    if ok:
        s = ok[0]
        print("示例配单明细(首方案前 3 条)：")
        for it in s["items"][:3]:
            print(f"    {it['seq']}. {it['product_name']} | 型号={it['model']} | 数量={it['qty']} | 单价={it['unit_price']}")


def write_db(plan, rows, db_path):
    import sqlite3

    con = sqlite3.connect(db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS boms (
            scheme_id TEXT PRIMARY KEY, scheme_name TEXT, vendor TEXT, industry TEXT,
            channel TEXT, source TEXT, source_url TEXT, collected_at TEXT, status TEXT,
            view_count INTEGER, raw_json TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS bom_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scheme_id TEXT, seq INTEGER, category TEXT,
            product_name TEXT, model TEXT, spec TEXT, unit TEXT, qty REAL, unit_price REAL,
            amount REAL, role TEXT, remark TEXT, raw_json TEXT,
            price_basis TEXT, matched_prod_id TEXT, match_type TEXT,
            FOREIGN KEY (scheme_id) REFERENCES boms(scheme_id))"""
    )
    # 幂等迁移：老库没有这几列时补上（对齐脚本写入 matched_prod_id 之前必备）
    existing = {r[1] for r in con.execute("PRAGMA table_info(bom_items)")}
    for col, typ in (("price_basis", "TEXT"), ("matched_prod_id", "TEXT"), ("match_type", "TEXT")):
        if col not in existing:
            con.execute(f"ALTER TABLE bom_items ADD COLUMN {col} {typ}")
    today = date.today().isoformat()
    raw_by_id = {r.get("scheme_id"): r for r in rows}
    for p in plan:
        # 幂等：同一方案重新导入时先清掉旧明细，避免重复累加
        con.execute("DELETE FROM bom_items WHERE scheme_id=?", (p["scheme_id"],))
        con.execute(
            "INSERT OR REPLACE INTO boms (scheme_id,scheme_name,vendor,industry,channel,source,source_url,collected_at,status,view_count,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (p["scheme_id"], p["scheme_name"], "大华股份", p["industry"], "SMB", "mall_official",
             f"https://mall.dahuatech.com/#/solutionsCenter/solutionDetail/{p['scheme_id']}",
             today, "official", p["view"], json.dumps(raw_by_id.get(p["scheme_id"], {}), ensure_ascii=False)),
        )
        con.executemany(
            "INSERT INTO bom_items (scheme_id,seq,category,product_name,model,spec,unit,qty,unit_price,amount,role,remark,raw_json,price_basis) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(p["scheme_id"], it["seq"], it["category"], it["product_name"], it["model"], it["spec"],
              it["unit"], it["qty"], it["unit_price"], it["amount"], it["role"], it["remark"],
              json.dumps(it["raw"], ensure_ascii=False), it.get("price_basis", "")) for it in p["items"]],
        )
    con.commit()
    con.close()
    print(f"已写入：boms {len(plan)} 条，bom_items 明细已落库。")
    print("提示：重新导入会清空产品关联，请接着跑 scraper/align_bom_products.py 重建对齐。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--index", default=None)
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    index_map = {}
    if a.index:
        try:
            for it in json.loads(Path(a.index).read_text(encoding="utf-8")):
                index_map[it["id"]] = it
        except Exception as e:
            print("索引加载失败：", e)

    rows = json.loads(Path(a.json).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = [rows]
    plan = build_plan(rows, index_map)
    report(plan)

    if a.dry_run:
        print("（dry-run，未写入数据库）")
        return
    write_db(plan, rows, a.db)


if __name__ == "__main__":
    main()
