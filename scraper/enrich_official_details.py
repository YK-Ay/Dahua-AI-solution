"""给官网目录补进来的产品抓「产品概述 + 技术参数」。

官网详情页的参数是公开 Ajax 片段：
  https://www.dahuatech.com/ajax/product_new/<pid>/1  → 产品概述
  https://www.dahuatech.com/ajax/product_new/<pid>/2  → 技术参数（HTML 表格）
  https://www.dahuatech.com/ajax/product_new/<pid>/4  → 订货型号

只处理 source='official_catalog' 的产品，只填空字段、不覆盖任何已有数据。
用法：python scraper/enrich_official_details.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "db" / "dahua_kb.sqlite"
AJAX = "https://www.dahuatech.com/ajax/product_new/{pid}/{tab}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
# 新版详情页把参数直接内嵌成 JS 常量（中文为 \uXXXX 转义），不是 HTML 表格
PARAM_LIST_RE = re.compile(
    r"(?:const|var|let)\s+parameterList\s*=\s*(\[.*?\])\s*;\s*//parameterList replace end", re.S)


def parse_params(fragment):
    """把内嵌的 parameterList JSON 转成「分组 / 字段 | 值」文本。"""
    m = PARAM_LIST_RE.search(fragment or "")
    if not m:
        return ""
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return ""
    lines = []
    for grp in arr:
        if not isinstance(grp, dict):
            continue
        title = str(grp.get("tabTitle") or "").strip()
        if title:
            lines.append("【" + title + "】")
        for kv in grp.get("list") or []:
            if isinstance(kv, dict):
                label = str(kv.get("label") or "").strip()
                value = str(kv.get("value") or "").strip()
                if label or value:
                    lines.append(f"{label} | {value}")
    return "\n".join(lines)[:6000]


def to_text(fragment, tech=False):
    """把 Ajax 返回的 HTML 片段转成可读文本；技术参数表按「字段 | 值」逐行。"""
    t = SCRIPT_RE.sub(" ", fragment or "")
    if tech:
        t = re.sub(r"</tr\s*>", "\n", t, flags=re.I)
        t = re.sub(r"</(th|td)\s*>", " | ", t, flags=re.I)
    else:
        t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t, flags=re.I)
    t = TAG_RE.sub("", t)
    t = html_lib.unescape(t)
    lines = []
    for ln in t.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip().strip("|").strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines)[:6000]


def fetch(pid, tab):
    try:
        r = requests.get(AJAX.format(pid=pid, tab=tab),
                         headers={"User-Agent": UA}, timeout=20)
        if r.status_code == 200:
            if tab == 2:
                params = parse_params(r.text)
                if params:
                    return params
            return to_text(r.text, tech=(tab == 2))
    except Exception:
        pass
    return ""


def work(row):
    prod_id, url = row
    m = re.search(r"/product/info/(\d+)\.html", url or "")
    if not m:
        return prod_id, "", ""
    pid = m.group(1)
    return prod_id, fetch(pid, 1), fetch(pid, 2)


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(products)")}
    missing = {"prod_desc", "spec_value"} - cols
    if missing:
        for c in missing:
            con.execute(f"ALTER TABLE products ADD COLUMN {c} TEXT")
        con.commit()
    rows = con.execute(
        "SELECT prod_id, source_url FROM products WHERE source='official_catalog' "
        "ORDER BY prod_id").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"待补详情的官网产品：{len(rows)} 条")
    if args.dry_run:
        print("[空跑] 未发请求、未写库。")
        con.close()
        return

    done = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (pid, overview, tech) in enumerate(ex.map(work, rows), 1):
            done.append((overview, tech, pid))
            if i % 80 == 0:
                print(f"  已处理 {i}/{len(rows)}")
    ok_desc = sum(1 for o, _, _ in done if o)
    ok_tech = sum(1 for _, t, _ in done if t)
    print(f"抓到产品概述 {ok_desc} 条 | 技术参数 {ok_tech} 条")

    con.executemany(
        """UPDATE products SET
             prod_desc  = CASE WHEN (prod_desc  IS NULL OR prod_desc  = '') THEN ? ELSE prod_desc  END,
             spec_value = CASE WHEN (spec_value IS NULL OR spec_value = '') THEN ? ELSE spec_value END
           WHERE prod_id = ?""", done)
    con.commit()
    print("\n样例（第 1 条）：")
    o, t, pid = done[0]
    print(f"  {pid}\n  概述: {o[:150]}\n  参数: {t[:220]}")
    print("\n官网产品详情完整度：")
    for col in ("prod_desc", "spec_value"):
        n = con.execute(
            f"select count(*) from products where source='official_catalog' and {col} is not null and {col}!=''").fetchone()[0]
        print(f"  {col}: {n}/{len(rows)}")
    con.close()


if __name__ == "__main__":
    main()
