"""把 SQLite 里 5 类数据统一成「资料文档」（Doc）。

为什么要有这一层：数据库里方案是 chunks、配单是树、竞品是逐字段的事实表，
格式全不一样。检索层不该关心这些差异 —— 统一成 Doc 后，谁都能被编号成
[资料N]，引用时才有稳定的来源可回溯。

5 类资料：
  dahua_solution     大华方案文档 244 块
  competitor_solution 海康方案文档（竞品子集，禁止给大华能力背书）
  bom                云商官方配单，按「方案 × 分类」拆成可引用的小文档
  product            产品库（型号/简介/技术参数/标准价）
  case               官网行业案例
  competitor_fact    竞品参数事实（逐字段原始值）
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .config import SOURCE_LABELS

TAG_RE = re.compile(r"<[^>]+>")
CANDIDATE_NOTE = "【重要】同一分类下多个型号是云商的「可替换候选」，实际选型应择一，禁止把全部候选累加成采购清单或总价。"


@dataclass
class Doc:
    doc_id: str
    title: str
    text: str
    subset: str
    vendor: str = "大华股份"
    industry: str = ""
    source: str = ""
    source_url: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.subset, self.subset)


def clean(v, limit: int = 0) -> str:
    s = TAG_RE.sub("", str(v or "")).strip()
    s = re.sub(r"[ \t]+", " ", s)
    return s[:limit] if limit else s


def money(v) -> str:
    if v in (None, "", 0, "0"):
        return "原文未提供"
    try:
        return "¥" + format(float(v), ",.2f")
    except (TypeError, ValueError):
        return "原文未提供"


def _sha(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:10]


def _chunks_of(text: str, size: int = 900):
    text = clean(text)
    for i in range(0, len(text), size):
        yield text[i:i + size]


def load_docs(db_path: Path) -> list[Doc]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    docs: list[Doc] = []
    try:
        # ---------- 1. 方案文档分块（大华 / 海康） ----------
        for r in con.execute(
            """SELECT c.chunk_id, c.seq, c.text, d.vendor, d.subset, d.title,
                      d.industry, d.source, d.raw_path
               FROM kb_chunks c JOIN kb_docs d ON d.doc_id = c.doc_id"""
        ):
            docs.append(Doc(
                doc_id=f"ch:{r['chunk_id']}",
                title=clean(r["title"]) or "方案文档",
                text=clean(r["text"]),
                subset=r["subset"],
                vendor=r["vendor"] or "",
                industry=clean(r["industry"]),
                source=clean(r["source"]) or r["vendor"] or "",
                source_url=clean(r["raw_path"]),
                meta={"chunk_seq": r["seq"]},
            ))

        # ---------- 2. 官方配单：按「方案 × 分类」拆成小文档 ----------
        for b in con.execute(
            "SELECT scheme_id, scheme_name, industry, source_url FROM boms"
        ):
            sid = b["scheme_id"]
            groups: dict[str, list[sqlite3.Row]] = {}
            for it in con.execute(
                """SELECT seq, category, product_name, model, spec, unit, qty,
                          unit_price, price_basis, role
                   FROM bom_items WHERE scheme_id=? ORDER BY seq""", (sid,)
            ):
                groups.setdefault(clean(it["category"]) or "未分类", []).append(it)
            for cat, items in groups.items():
                lines = [
                    f"方案：{clean(b['scheme_name'])}",
                    f"行业：{clean(b['industry'])}",
                    f"配单分类：{cat}",
                    f"本组候选型号 {len(items)} 个。{CANDIDATE_NOTE}",
                ]
                for it in items:
                    bits = [
                        f"- {clean(it['product_name'])}",
                        f"型号 {clean(it['model']) or '原文未提供'}",
                        f"规格 {clean(it['spec']) or '原文未提供'}",
                        f"数量 {it['qty'] if it['qty'] is not None else '原文未提供'} {clean(it['unit'])}",
                        f"单价 {money(it['unit_price'])}（口径：{clean(it['price_basis']) or '原文未提供'}）",
                    ]
                    if clean(it["role"]):
                        bits.append(f"职责 {clean(it['role'])}")
                    lines.append(" | ".join(bits))
                docs.append(Doc(
                    doc_id=f"bom:{sid}:{_sha(cat)}",
                    title=f"{clean(b['scheme_name'])} · {cat}",
                    text="\n".join(lines),
                    subset="bom",
                    industry=clean(b["industry"]),
                    source="大华云商方案配单（分销商账号导出，official）",
                    source_url=clean(b["source_url"]),
                    meta={"scheme_id": sid, "category": cat, "item_count": len(items)},
                ))

        # ---------- 3. 产品库 ----------
        for p in con.execute(
            """SELECT prod_id, model, display_name, class_name, price_stand, part_num,
                      prod_desc, spec_value, source, source_url, pi_status, is_virtual
               FROM products"""
        ):
            pid = p["prod_id"]
            model = clean(p["model"])
            if not model and not clean(p["display_name"]):
                continue
            parts = [
                f"产品型号：{model or '原文未提供'}",
                f"名称：{clean(p['display_name']) or '原文未提供'}",
                f"类目：{clean(p['class_name']) or '原文未提供'}",
                f"物料号：{clean(p['part_num']) or '原文未提供'}",
                f"产品库标准价：{money(p['price_stand'])}（非分销成交价，报价以云商下单为准）",
                f"销售状态：{clean(p['pi_status']) or '原文未提供'}",
            ]
            if p["is_virtual"]:
                parts.append("【注意】这是虚拟项/服务项/套件编号，不是可单独选型采购的设备。")
            if clean(p["prod_desc"]):
                parts.append("产品简介：" + clean(p["prod_desc"], 320))
            if clean(p["spec_value"]):
                parts.append("技术参数：" + clean(p["spec_value"], 320))
            docs.append(Doc(
                doc_id=f"pr:{pid}",
                title=clean(p["display_name"]) or model,
                text="\n".join(parts),
                subset="product",
                industry=clean(p["class_name"]),
                source=clean(p["source"]) or "legacy",
                source_url=clean(p["source_url"]),
                meta={"prod_id": pid, "model": model, "is_virtual": int(p["is_virtual"] or 0)},
            ))

        # ---------- 4. 官网案例 ----------
        for r in con.execute("SELECT id, industry, title, url, content_text FROM pages"):
            for i, seg in enumerate(_chunks_of(r["content_text"] or "")):
                docs.append(Doc(
                    doc_id=f"pg:{r['id']}:{i}",
                    title=clean(r["title"]) or "官网案例",
                    text=seg,
                    subset="case",
                    industry=clean(r["industry"]),
                    source="大华官网公开案例",
                    source_url=clean(r["url"]),
                    meta={"page_id": r["id"]},
                ))

        # ---------- 5. 竞品参数事实（按 品牌+型号+参数分区 细拆） ----------
        # 补采后一款机型可能有上百条参数，整篇塞进一个 Doc 会被 prompt 的
        # CHARS_BY_SUBSET(1200) 截断，具体字段检索不到；按分区切成 <900 字的
        # 小文档后既能完整进上下文，检索粒度也更细。
        facts: dict[tuple[str, str], dict[str, list[str]]] = {}
        urls: dict[tuple[str, str], str] = {}
        for r in con.execute(
            "SELECT brand, model, display_name, field_label, raw_value, source_url, section "
            "FROM competitor_facts"
        ):
            key = (clean(r["brand"]), clean(r["model"]))
            section = clean(r["section"]) or "其他参数"
            facts.setdefault(key, {}).setdefault(section, []).append(
                f"{clean(r['field_label'])}：{clean(r['raw_value'])}"
            )
            urls.setdefault(key, clean(r["source_url"]))

        CHUNK_CHARS = 850
        for (brand, model), by_section in facts.items():
            chunks: list[tuple[str, list[str]]] = []
            cur_section, cur_lines, cur_len = "", [], 0
            for section, lines in by_section.items():
                for ln in lines:
                    if cur_lines and cur_len + len(ln) + 1 > CHUNK_CHARS:
                        chunks.append((cur_section, cur_lines))
                        cur_lines, cur_len = [], 0
                    if not cur_lines:
                        cur_section = section
                    cur_lines.append(ln)
                    cur_len += len(ln) + 1
            if cur_lines:
                chunks.append((cur_section, cur_lines))

            for i, (section, lines) in enumerate(chunks, 1):
                suffix = f" 第 {i}/{len(chunks)} 段" if len(chunks) > 1 else ""
                docs.append(Doc(
                    doc_id=f"cf:{_sha(brand + model + section + str(i))}",
                    title=f"{brand} {model} 官网参数 · {section}{suffix}",
                    text=(f"品牌：{brand}\n型号：{model}\n参数分区：{section}\n"
                          + "\n".join(lines)),
                    subset="competitor_fact",
                    vendor=brand,
                    source=f"{brand}官网公开参数页",
                    source_url=urls.get((brand, model), ""),
                    meta={"brand": brand, "model": model, "section": section,
                          "field_count": len(lines)},
                ))
    finally:
        con.close()
    return docs


def docs_stats(docs: list[Doc]) -> dict:
    by: dict[str, int] = {}
    for d in docs:
        by[d.subset] = by.get(d.subset, 0) + 1
    return {"total": len(docs), "by_subset": by}
