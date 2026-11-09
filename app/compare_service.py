"""竞品对比数据层：类目 → 双方产品池 → 任意配对逐字段对比。

与旧的 app/compare.py 的关系：
- 旧模块只支持"每类固定 1 款大华 + 1 款海康"的硬编码对比。
- 本模块把产品池开放出来：大华侧接 products（3811 款，含官网参数 spec_value），
  海康侧接 competitor_facts（官网公开参数机型）。用户各选一款后，
  复用 app/compare.py 的字段映射 / 归一化 / 判定 / 话术逻辑生成对照表。

原则（沿用项目反幻觉口径）：
- 只用库里真实存在的公开字段；缺项标"官网未公开"，绝不推断、不补数。
- 每个字段绑定来源 URL；非官网参数机型的覆盖范围会显式回传 coverage。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from config.settings import DB_PATH
from scraper.density import annotate

from .compare import (
    FIELDS,
    REPRESENTATIVES as OFFICIAL_PAIRS,
    Parsed,
    STATUS_LABEL,
    _pick,
    _talking,
    _verdict,
)

DAHUA, HIK = "大华", "海康"

# 三大类目在 products.class_name 上的近似映射（仅用于缩小候选池，不做精确分档）
CATEGORY_FILTERS: dict[str, str] = {
    "网络摄像机": (
        "(class_name LIKE '%摄像机%' OR class_name LIKE '%球机%' "
        "OR class_name LIKE '%监控%' OR class_name LIKE '%热成像%' "
        "OR class_name LIKE '%夜视王%' OR class_name LIKE '%云台%')"
    ),
    "NVR": (
        "(class_name LIKE '%录像机%' OR class_name LIKE '%存储%' "
        "OR class_name LIKE '%服务器%' OR class_name LIKE '%综合平台%')"
    ),
    # 注意：不能含 '%网络%'，否则会把「网络摄像机」整类混进交换机池
    "PoE交换机": (
        "(class_name LIKE '%交换机%' OR class_name LIKE '%数通%' "
        "OR class_name LIKE '%布线%' OR class_name LIKE '%网关%' "
        "OR class_name LIKE '%路由%' OR class_name LIKE '%光口%')"
    ),
}

# OFFICIAL_PAIRS 从 app/compare.py 导入（REPRESENTATIVES），只用于标记
# 「官网同档代表款」这一组，不再据此调用 compare.compare()——补采后同类目
# 几十款机型会按 field_label 互相覆盖，必须统一按具体型号取参数。

CATEGORY_HINT: dict[str, str] = {
    "网络摄像机": "前端采集：枪机 / 球机 / 行业相机，重点看像素、传感器、宽动态、补光。",
    "NVR": "后端存储：录像机 / 存储设备，重点看接入路数、带宽、盘位、解码。",
    "PoE交换机": "网络传输：交换机 / 数通，重点看 PoE 端口、速率、管理型、功率。",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_spec_value(text: str | None) -> dict[str, str]:
    """把官网技术参数文本（【分组】+ `字段 | 值`）解析成 字段 → 原文值。

    续行（无 `|` 的行）并入上一个字段，避免多行取值被截断。
    """
    out: dict[str, str] = {}
    last_key = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("【") and line.endswith("】"):
            continue
        if "|" in line:
            key, value = line.split("|", 1)
            key, value = key.strip(), value.strip()
            if not key:
                continue
            if key in out:
                last_key = ""
                continue
            out[key] = value
            last_key = key
        elif last_key and out.get(last_key):
            out[last_key] += "；" + line
    return out


def _competitor_facts(conn: sqlite3.Connection, brand: str, model: str) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT field_label, raw_value, source_url, evidence, section, retrieved_at "
        "FROM competitor_facts WHERE brand=? AND model=? ORDER BY id", (brand, model)
    ).fetchall()
    return {r["field_label"]: dict(r) for r in rows}


def _product_facts(conn: sqlite3.Connection, model: str) -> dict[str, dict]:
    """大华侧的公开字段：优先官网参数事实，其次产品库 spec_value。"""
    facts = _competitor_facts(conn, DAHUA, model)
    if facts:
        return facts

    row = conn.execute(
        "SELECT model, display_name, spec_value, prod_desc, source_url, price_stand "
        "FROM products WHERE model=?", (model,)
    ).fetchone()
    if not row:
        return {}
    parsed = parse_spec_value(row["spec_value"])
    common = {
        "source_url": row["source_url"] or "",
        "evidence": "产品库技术参数",
        "section": "官网产品库",
        "retrieved_at": "",
    }
    facts = {k: dict(raw_value=v, **common) for k, v in parsed.items()}
    if row["prod_desc"]:
        facts.setdefault("产品概述", dict(
            raw_value=row["prod_desc"], **common
        ))
    if row["price_stand"]:
        facts.setdefault("标准价格", dict(
            raw_value=f"¥{row['price_stand']}", **common
        ))
    return facts


def _dh_alignment_score(spec_value: str, category: str) -> int:
    """产品库技术参数里，有多少字段名能对该类目对比模板的 dh 标签。

    用来给大华候选排序：有 spec_value 不等于能对比（字段名可能完全不同），
    分数为 0 的产品排后面，避免用户默认点到后生成一张空表。
    """
    if not spec_value:
        return 0
    keys = set(parse_spec_value(spec_value))
    labels = {a for field in FIELDS[category] for a in field["dh"]}
    return len(keys & labels)


def categories() -> dict:
    """三个类目 + 双方可选产品数 / 参数事实数，供前端引导页使用。"""
    conn = _connect()
    try:
        items = []
        for cat in FIELDS:
            dahua_n = conn.execute(
                f"SELECT COUNT(*) FROM products WHERE {CATEGORY_FILTERS[cat]}"
            ).fetchone()[0]
            dahua_spec = conn.execute(
                f"SELECT COUNT(*) FROM products WHERE {CATEGORY_FILTERS[cat]} "
                "AND spec_value IS NOT NULL AND spec_value<>''"
            ).fetchone()[0]
            hik = conn.execute(
                "SELECT COUNT(DISTINCT model) AS n, COUNT(*) AS facts "
                "FROM competitor_facts WHERE brand=? AND category=?", (HIK, cat)
            ).fetchone()
            reps = [{"brand": r["brand"], "model": r["model"]}
                    for r in conn.execute(
                        "SELECT brand, model FROM competitor_facts WHERE category=? "
                        "GROUP BY brand, model", (cat,))]
            # 大华代表款来自 OFFICIAL_PAIRS（与 app/compare.REPRESENTATIVES 同源），
            # 竞品表只放海康，故在此显式补回大华侧代表款。
            dh_rep = OFFICIAL_PAIRS.get(cat, (None, None))[0]
            if dh_rep:
                reps.append({"brand": DAHUA, "model": dh_rep})
            items.append({
                "key": cat,
                "name": cat,
                "hint": CATEGORY_HINT[cat],
                "dahua_products": dahua_n,
                "dahua_with_spec": dahua_spec,
                "hik_models": hik["n"],
                "hik_facts": hik["facts"],
                "representatives": reps,
            })
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


def products(category: str, brand: str, q: str = "", offset: int = 0, limit: int = 20) -> dict:
    """按类目 + 品牌列出可选产品。大华有官网参数的排前面。"""
    if category not in FIELDS or brand not in (DAHUA, HIK):
        return {"items": [], "total": 0, "offset": offset, "limit": limit}
    limit = max(1, min(limit, 60))
    offset = max(0, offset)
    conn = _connect()
    try:
        if brand == DAHUA:
            where = CATEGORY_FILTERS[category]
            params: list[Any] = []
            if q:
                where += " AND (model LIKE ? OR display_name LIKE ? OR class_name LIKE ?)"
                params += [f"%{q}%"] * 3
            # 取全量后按「能与对比模板对齐的字段数」排序再切片：
            # 有 spec_value 不代表字段名能和 FIELDS 对上，直接按 has_spec 排
            # 会把摄像机之类的产品排到最前，用户一点就是 0/N 的空对比表。
            rows = conn.execute(
                "SELECT model, display_name, class_name, "
                "COALESCE(img_local, img_url) AS img_url, price_stand, "
                "prod_desc, source_url, spec_value, "
                "CASE WHEN spec_value IS NOT NULL AND spec_value<>'' THEN 1 ELSE 0 END AS has_spec "
                f"FROM products WHERE {where}", params,
            ).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                spec = d.pop("spec_value", "") or ""
                d["_score"] = _dh_alignment_score(spec, category)
                items.append(d)

            # 官网同档代表款（大华）的参数在 products 表中、字段与模板完全对齐，
            # 置顶为默认推荐。竞品表只放海康，大华代表款一律以 products 为准。
            rep_model = OFFICIAL_PAIRS.get(category, (None, None))[0]
            if rep_model and not q:
                rr = conn.execute(
                    "SELECT model, display_name, COALESCE(img_local, img_url) AS img_url, "
                    "price_stand, prod_desc, source_url, class_name "
                    "FROM products WHERE model=?", (rep_model,)
                ).fetchone()
                if rr:
                    items = [d for d in items if d["model"] != rep_model]
                    items.insert(0, {
                        "model": rr["model"], "display_name": rr["display_name"],
                        "class_name": rr["class_name"] or "官网同档代表款",
                        "img_url": rr["img_url"] or "",
                        "price_stand": rr["price_stand"],
                        "prod_desc": rr["prod_desc"]
                        or "官网公开参数机型，字段与对比模板完全对齐",
                        "source_url": rr["source_url"] or "", "has_spec": 1,
                        "_score": 99, "_rep": True,
                    })
            items.sort(key=lambda d: (
                bool(d.get("_rep")), d["_score"], d["has_spec"],
                d["class_name"] or "", d["model"],
            ), reverse=True)
            total = len(items)
            items = items[offset:offset + limit]
            n_fields = len(FIELDS[category])
            for d in items:
                d["brand"] = DAHUA
                d["match_score"] = min(d["_score"], n_fields)
                d["fact_note"] = (
                    "推荐 · 官网同档代表款，参数最完整"
                    if d.get("_rep") else
                    (f"官网参数已收录 · 可对齐 {d['match_score']}/{n_fields} 项"
                     if d["has_spec"] else "仅产品目录信息，参数未公开")
                )
                d.pop("_score", None)
                d.pop("_rep", None)
        else:
            # 优先用补采后的「海康产品目录」；表不存在时回退到旧的参数事实聚合
            try:
                conn.execute("SELECT 1 FROM competitor_products LIMIT 1").fetchone()
                has_catalog = True
            except sqlite3.OperationalError:
                has_catalog = False

            if has_catalog:
                where = "p.category=?"
                params = [category]
                if q:
                    where += " AND (p.model LIKE ? OR p.display_name LIKE ?)"
                    params += [f"%{q}%"] * 2
                total = conn.execute(
                    f"SELECT COUNT(*) FROM competitor_products p WHERE {where}", params
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT p.model, p.display_name, p.category AS class_name, p.source_url, "
                    "p.pid, p.has_spec, p.collected_at AS retrieved_at, "
                    "(SELECT COUNT(*) FROM competitor_facts f "
                    " WHERE f.brand='海康' AND f.model=p.model) AS fact_count "
                    f"FROM competitor_products p WHERE {where} "
                    "ORDER BY p.has_spec DESC, fact_count DESC, p.model LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
                items = []
                for r in rows:
                    d = dict(r)
                    n = d.get("fact_count") or 0
                    d.update({
                        "brand": HIK, "img_url": "", "price_stand": None,
                        "prod_desc": (f"海康官网公开参数机型，已收录 {n} 条参数事实"
                                      if d["has_spec"] else "海康官网目录型号，参数尚未采集"),
                        "fact_note": (f"{n} 条官网参数 · 可逐字段对比"
                                      if d["has_spec"] else "仅目录型号 · 无法逐字段对比"),
                    })
                    items.append(d)
            else:
                where = "brand=? AND category=?"
                params = [HIK, category]
                if q:
                    where += " AND (model LIKE ? OR display_name LIKE ?)"
                    params += [f"%{q}%"] * 2
                total = conn.execute(
                    f"SELECT COUNT(DISTINCT model) FROM competitor_facts WHERE {where}", params
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT model, MAX(display_name) AS display_name, COUNT(*) AS fact_count, "
                    "MIN(source_url) AS source_url, MAX(retrieved_at) AS retrieved_at "
                    f"FROM competitor_facts WHERE {where} "
                    "GROUP BY model ORDER BY model LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
                items = []
                for r in rows:
                    d = dict(r)
                    d.update({
                        "brand": HIK, "class_name": category, "img_url": "",
                        "price_stand": None, "has_spec": 1,
                        "prod_desc": f"官网公开参数机型，已收录 {d['fact_count']} 条参数事实",
                        "fact_note": f"{d['fact_count']} 条官网参数 · 可逐字段对比",
                    })
                    items.append(d)
        return {"items": items, "total": total, "offset": offset, "limit": limit}
    finally:
        conn.close()


def pair(category: str, dahua_model: str, hik_model: str) -> dict:
    """任意配对 → 逐字段对照表。

    若正好是双方官网代表款，直接复用 app/compare.compare() 的精对比结果；
    否则用产品库 spec_value / competitor_facts 重新组装字段，再走同一套判定逻辑。
    """
    if category not in FIELDS:
        raise ValueError(f"不支持的对比类目：{category}")
    if not dahua_model or not hik_model:
        raise ValueError("请分别在两侧各选一款产品")

    conn = _connect()
    try:
        official = OFFICIAL_PAIRS.get(category)
        is_official = bool(official and dahua_model == official[0] and hik_model == official[1])
        # 不再调用 compare.compare()：它按 field_label 覆盖，补采后同类目
        # 有几十款机型会串型号。统一按「具体型号」取参数再走同一套判定逻辑。
        d_row = conn.execute(
            "SELECT model, display_name, prod_desc, source_url, price_stand "
            "FROM products WHERE model=?", (dahua_model,)
        ).fetchone()
        if not d_row:
            # 官网同档代表款可能只存在于竞品事实库、没进大华产品库，
            # 用 competitor_facts 里的元信息兜底（参数仍按具体型号精确取）。
            d_row = conn.execute(
                "SELECT model, MAX(display_name) AS display_name, '' AS prod_desc, "
                "MIN(source_url) AS source_url, NULL AS price_stand "
                "FROM competitor_facts WHERE brand=? AND model=? GROUP BY model",
                (DAHUA, dahua_model),
            ).fetchone()
        if not d_row:
            raise ValueError(f"产品库和竞品库中都找不到大华型号：{dahua_model}")
        h_row = conn.execute(
            "SELECT model, display_name, source_url, MAX(retrieved_at) AS retrieved_at "
            "FROM competitor_facts WHERE brand=? AND model=? GROUP BY model",
            (HIK, hik_model),
        ).fetchone()
        if not h_row:
            try:
                catalog_only = conn.execute(
                    "SELECT 1 FROM competitor_products WHERE model=? AND has_spec=0", (hik_model,)
                ).fetchone()
            except sqlite3.OperationalError:
                catalog_only = None
            if catalog_only:
                raise ValueError(
                    f"海康型号 {hik_model} 只有官网目录信息、尚未采集逐字段参数，无法生成对比表；"
                    "请选择标注「官网参数已收录」的机型，或先补采参数。"
                )
            raise ValueError(f"竞品库中没有海康型号：{hik_model}（当前只收录官网公开参数机型）")

        dh_facts = _product_facts(conn, dahua_model)
        hk_facts = _competitor_facts(conn, HIK, hik_model)
    finally:
        conn.close()

    rows = []
    summary = {k: 0 for k in STATUS_LABEL}
    aligned = 0
    for spec in FIELDS[category]:
        d = _pick(dh_facts, spec["dh"])
        h = _pick(hk_facts, spec["hk"])
        if d and h:
            aligned += 1
        a = spec["parse"](d["raw_value"]) if d else None
        b = spec["parse"](h["raw_value"]) if h else None
        status, delta, note = _verdict(spec, a, b)
        summary[status] += 1
        dh_disp = (a.display or d["raw_value"]) if (a and d) else "官网未公开"
        hk_disp = (b.display or h["raw_value"]) if (b and h) else "官网未公开"
        talk = _talking(spec["field"], status, a or Parsed(None, dh_disp),
                        b or Parsed(None, hk_disp), delta, note)
        density = annotate(talk)
        rows.append({
            "field": spec["field"], "status": status, "status_label": STATUS_LABEL[status],
            "dahua_value": dh_disp, "hik_value": hk_disp,
            "dahua_raw": d["raw_value"] if d else "", "hik_raw": h["raw_value"] if h else "",
            "dahua_section": d["section"] if d else "", "hik_section": h["section"] if h else "",
            "dahua_source": d["source_url"] if d else "", "hik_source": h["source_url"] if h else "",
            "dahua_evidence": d["evidence"] if d else "", "hik_evidence": h["evidence"] if h else "",
            "delta": delta, "note": note, "talking_point": talk,
            "density_score": density["density_score"], "density_kind": density["page_kind"],
        })

    dh_src = (d_row["source_url"] or (rows[0]["dahua_source"] if rows else "")) or "本地产品库"
    return {
        "category": category,
        "mode": "official_pair" if is_official else "custom_pair",
        "dahua_model": dahua_model, "hik_model": hik_model,
        "dahua_name": d_row["display_name"] or dahua_model,
        "hik_name": h_row["display_name"] or hik_model,
        "dahua_source": dh_src,
        "hik_source": h_row["source_url"] or "海康官网公开参数页",
        "retrieved_at": h_row["retrieved_at"] or "本地知识库",
        "rows": rows, "summary": summary,
        "coverage": {"aligned": aligned, "total": len(rows)},
        "disclaimer": (
            "本次为双方官网同档位代表款对比（非渠道订货 SKU）；参数均取自官网公开参数页，"
            "缺失项不作推断；海康官网无公开报价，价格不纳入对比。"
            if is_official else
            "本次为自定义配对：大华参数取自产品库官网技术参数/产品目录，海康参数取自官网公开参数页；"
            "任一侧未公开的字段一律标『官网未公开』，不推断、不补数。两侧并非严格同档位代表款，"
            "规格差异仅供选型参考，交付前须按实际项目配置复核。"
        ),
    }
