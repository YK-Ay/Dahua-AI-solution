"""海康官网公开产品目录补采（#27）。

数据源（全部为公开页面，不涉及任何账号 / 鉴权）：
  1. 官网搜索接口 new_filter_search.json —— 按型号前缀搜索，结果里的
     `/cn/products/pdplist/<pid>/` 就是产品详情页地址。
  2. 官网产品详情页 `https://www.hikvision.com/cn/products/pdplist/<pid>/`
     —— 服务端渲染，`div.model span` 是精确型号，
     `li.tech-specs-accordion-desc-item` 是逐条参数。

原则：
- 型号只取详情页里的原文，不把 sitemap 的 URL slug 当订货型号；
- 参数只取官网原文，不推断、不补数；
- 只新增不覆盖：已有 358 条事实保持原样，新事实按「品牌+型号+字段+原文值」去重；
- 已排除的弯路：sitemap 里 2885 个旧产品 URL 现已全部 404，
  `/cn/products/product/<slug>/` 新路径在 www 域也 404，publisher 子域不可达。

用法：
    python -m scraper.collect_hik_catalog --dry-run          # 只探测，不写库
    python -m scraper.collect_hik_catalog --limit 120        # 真实采集
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup
from curl_cffi import requests as cr

from config.settings import DB_PATH

SEARCH_API = ("https://www.hikvision.com/content/hikvision/cn/search/"
              "jcr:content/root/responsivegrid/new_filter_search.json")
DETAIL = "https://www.hikvision.com/cn/products/pdplist/{pid}/"

# 三类设备各用一组型号前缀，尽量覆盖大华产品库里对应的品类
KEYWORDS = [
    "DS-2CD1", "DS-2CD3", "DS-2CD5", "DS-2CD7", "DS-IPC",
    "DS-2CD", "DS-7800N", "DS-7600", "DS-7700N", "DS-8600N",
    "DS-3E05", "DS-3E", "DS-3W",
]

# 型号前缀是最强信号，必须优先于标题关键词。
# 踩过的坑：旧规则把裸 "POE" 放在交换机第一位，导致支持 PoE 供电的
# 摄像机（DS-IPC-T12HV3-LA）和 PoE NVR（DS-7800N-Z1）被误分到交换机类。
MODEL_PREFIX_RULES = [
    ("网络摄像机", r"^(?:DS|IDS)-(?:2CD|IPC|2X|2CE|2MC)"),
    ("NVR", r"^(?:DS|IDS)-(?:[789]\d{3}N|7[68]\d{2}N|76\d\d|78\d\d)"),
    ("PoE交换机", r"^(?:DS|IDS)-(?:3E|3W|3D|3T)"),
]
TITLE_RULES = [
    ("网络摄像机", r"网络摄像机|筒机|半球|球机|枪机|摄像机", re.I),
    ("NVR", r"录像机|NVR|XVR|NVS", re.I),
    ("PoE交换机", r"交换机|SWITCH", re.I),
]
CAT_RULES = [
    (name, pattern, re.I) for name, pattern in MODEL_PREFIX_RULES
] + TITLE_RULES
TAG_RE = re.compile(r"<[^>]+>")
PID_RE = re.compile(r"/pdplist/(\d+)/")


def log(*a):
    print(*a, flush=True)


def clean(v, flat=True) -> str:
    if v is None:
        return ""
    s = html_lib.unescape(TAG_RE.sub("", str(v)))
    return re.sub(r"\s+", " ", s).strip() if flat else s.strip()


def classify(model: str, title: str) -> str:
    text = (model or "") + " " + (title or "")
    for cat, pattern, flags in CAT_RULES:
        if re.search(pattern, text, flags):
            return cat
    return "其他"


def search(session, kw: str, start: int = 0, num: int = 50) -> tuple[list[dict], int]:
    url = (SEARCH_API + "?keyword=" + quote(kw) +
           "&subcategory=" + quote("硬件产品") +
           f"&start={start}&num={num}")
    try:
        j = session.get(url, timeout=30).json()
        d = (j.get("data") or {})
        return d.get("result") or [], int(d.get("recordCount") or 0)
    except Exception as exc:  # noqa: BLE001
        log(f"  ! 搜索失败 {kw} @ {start}: {type(exc).__name__}: {exc}")
        return [], 0


def collect_pids() -> dict[str, str]:
    """返回 {pid: 官网搜索结果标题}。"""
    session = cr.Session(impersonate="chrome")
    pids: dict[str, str] = {}
    for kw in KEYWORDS:
        rows, total = search(session, kw, 0, 50)
        got = 0
        for r in rows:
            m = PID_RE.search(r.get("url") or "")
            if m:
                pids.setdefault(m.group(1), clean(r.get("title")))
                got += 1
        log(f"  {kw:12} 结果 {len(rows):3}/{total:4} · 详情页 pid +{got}")
        # 宽泛词再翻两页，补齐靠后的产品
        if kw == "DS-2CD":
            for start in (50, 100, 150, 200):
                more, _ = search(session, kw, start, 50)
                n = 0
                for r in more:
                    m = PID_RE.search(r.get("url") or "")
                    if m:
                        pids.setdefault(m.group(1), clean(r.get("title")))
                        n += 1
                log(f"    DS-2CD 第 {start} 页起 · pid +{n}")
        time.sleep(0.25)
    return pids


def fetch_detail(pid: str) -> dict | None:
    url = DETAIL.format(pid=pid)
    try:
        r = cr.get(url, impersonate="chrome", timeout=28)
        if r.status_code != 200:
            return {"pid": pid, "error": f"HTTP {r.status_code}"}
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("div.model span")
        model = clean(node.get_text()) if node else ""
        if not model:
            return {"pid": pid, "error": "页面无型号"}
        title = clean(soup.title.get_text().split(" - ")[0]) if soup.title else ""
        facts = []
        seen = set()
        for li in soup.select("li.tech-specs-accordion-desc-item"):
            t, d = li.select_one("span.item-title"), li.select_one("span.item-desc")
            if not t or not d:
                continue
            label = clean(t.get_text())
            value = clean(d.get_text())
            if not label or not value:
                continue
            key = (label, value)
            if key in seen:
                continue
            seen.add(key)
            ul = li.find_parent("ul")
            section = clean(ul.get("data-target") or "") if ul else ""
            facts.append({"section": section, "field_label": label, "raw_value": value})
        return {
            "pid": pid, "model": model, "display_name": title,
            "category": classify(model, title), "source_url": url,
            "facts": facts,
        }
    except Exception as exc:  # noqa: BLE001
        return {"pid": pid, "error": f"{type(exc).__name__}: {exc}"}


def pick_balanced(pids: dict[str, str], limit: int) -> list[str]:
    """按搜索结果标题粗分类，三类均衡取，避免全是 NVR。"""
    buckets: dict[str, list[str]] = {c: [] for c, _, _ in CAT_RULES}
    buckets["其他"] = []
    for pid, title in pids.items():
        buckets[classify("", title)].append(pid)
    for k in buckets:
        log(f"  候选 {k}: {len(buckets[k])} 个")
    per = max(10, limit // 3)
    chosen: list[str] = []
    for cat in ("网络摄像机", "NVR", "PoE交换机"):
        chosen.extend(buckets[cat][:per])
    # 不足限额时用「其他」补齐（详情页会重新精确分类）
    if len(chosen) < limit:
        chosen.extend(buckets["其他"][:limit - len(chosen)])
    return chosen[:limit]


def backup(db: Path) -> str:
    dst = db.with_name(db.name + ".bak-hik-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(db, dst)
    return str(dst)


def write_db(db: Path, products: list[dict], all_facts: list[dict], today: str) -> dict:
    con = sqlite3.connect(str(db))
    con.execute(
        """CREATE TABLE IF NOT EXISTS competitor_products (
            model TEXT PRIMARY KEY, brand TEXT, display_name TEXT, category TEXT,
            pid TEXT, source_url TEXT, fact_count INTEGER, has_spec INTEGER,
            collected_at TEXT)"""
    )
    existing = {
        (r[0], r[1], r[2]) for r in con.execute(
            "SELECT brand, model, field_label FROM competitor_facts WHERE brand='海康'"
        )
    }
    before_facts = con.execute("SELECT COUNT(*) FROM competitor_facts").fetchone()[0]
    before_products = con.execute("SELECT COUNT(*) FROM competitor_products").fetchone()[0]

    for p in products:
        con.execute(
            """INSERT INTO competitor_products
               (model, brand, display_name, category, pid, source_url, fact_count, has_spec, collected_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(model) DO UPDATE SET
                 display_name=excluded.display_name, category=excluded.category,
                 pid=excluded.pid, source_url=excluded.source_url,
                 fact_count=excluded.fact_count, has_spec=excluded.has_spec,
                 collected_at=excluded.collected_at""",
            (p["model"], "海康", p["display_name"], p["category"], p["pid"],
             p["source_url"], len(p["facts"]), 1 if p["facts"] else 0, today),
        )

    added = 0
    for f in all_facts:
        key = ("海康", f["model"], f["field_label"])
        if key in existing:
            continue
        existing.add(key)
        con.execute(
            """INSERT INTO competitor_facts
               (brand, category, model, display_name, section, field_label, raw_value,
                source_url, page_kind, evidence, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("海康", f["category"], f["model"], f["display_name"], f["section"],
             f["field_label"], f["raw_value"], f["source_url"], "tech_page",
             f["raw_value"], today),
        )
        added += 1

    # 历史机型（最初 3 款里未被本次抽样抓到的）同样登记进目录，
    # 否则「有参数却选不到」，竞品对比会漏掉可对比机型。
    con.execute(
        """INSERT OR IGNORE INTO competitor_products
           (model, brand, display_name, category, pid, source_url,
            fact_count, has_spec, collected_at)
           SELECT f.model, '海康', MAX(f.display_name), f.category, '',
                  MIN(f.source_url), COUNT(*), 1, MAX(f.retrieved_at)
           FROM competitor_facts f
           WHERE f.brand='海康'
             AND f.model NOT IN (SELECT model FROM competitor_products)
           GROUP BY f.model, f.category"""
    )
    con.commit()
    after_facts = con.execute("SELECT COUNT(*) FROM competitor_facts").fetchone()[0]
    after_products = con.execute("SELECT COUNT(*) FROM competitor_products").fetchone()[0]
    by_cat = list(con.execute(
        "SELECT category, COUNT(*) FROM competitor_products GROUP BY category ORDER BY 2 DESC"
    ))
    hik_facts = con.execute(
        "SELECT COUNT(*) FROM competitor_facts WHERE brand='海康'"
    ).fetchone()[0]
    con.close()
    return {
        "products_before": before_products, "products_after": after_products,
        "facts_before": before_facts, "facts_after": after_facts,
        "new_facts": added, "hik_facts": hik_facts,
        "by_category": [{"category": c, "count": n} for c, n in by_cat],
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="海康官网公开产品目录补采")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=120, help="最多抓取多少个详情页")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--exclude-existing", action="store_true",
                    help="跳过已入库的 pid，只补采新增产品（联网窗口有限时用）")
    args = ap.parse_args()

    today = date.today().isoformat()
    log(f"=== 海康公开产品补采 {datetime.now():%Y-%m-%d %H:%M:%S}（公开页面，无账号）===")
    log("▶ [1/3] 从官网搜索接口收集产品详情页 pid…")
    pids = collect_pids()
    if not pids:
        log("✗ 没拿到任何 pid，接口可能变更")
        return 1
    log(f"  共 {len(pids)} 个唯一 pid")
    if args.exclude_existing:
        con = sqlite3.connect(str(DB_PATH))
        try:
            done = {r[0] for r in con.execute(
                "SELECT pid FROM competitor_products WHERE pid IS NOT NULL AND pid<>''"
            )}
        except sqlite3.OperationalError:
            done = set()
        finally:
            con.close()
        pids = {k: v for k, v in pids.items() if k not in done}
        log(f"  跳过已入库 {len(done)} 个，剩余待采 {len(pids)} 个")
        if not pids:
            log("✔ 没有新增产品，无需补采")
            return 0
    targets = pick_balanced(pids, args.limit)
    log(f"▶ [2/3] 均衡抽取 {len(targets)} 个详情页（并发 {args.workers}）…")
    if args.dry_run:
        log("  [空跑] 不抓详情页、不写库；候选 pid：" + ", ".join(targets[:8]))
        return 0

    products: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_detail, pid): pid for pid in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            d = fut.result()
            if not d or d.get("error"):
                errors.append(d or {"pid": futures[fut], "error": "空结果"})
            else:
                products.append(d)
            if i % 20 == 0 or i == len(targets):
                log(f"  进度 {i}/{len(targets)} · 成功 {len(products)} · 失败 {len(errors)}")
            time.sleep(0.08)

    # 同一型号只保留一条（参数多的优先）
    dedup: dict[str, dict] = {}
    for p in products:
        if p["model"] not in dedup or len(p["facts"]) > len(dedup[p["model"]]["facts"]):
            dedup[p["model"]] = p
    products = sorted(dedup.values(), key=lambda x: (x["category"], x["model"]))
    log(f"  解析成功 {len(products)} 款（去重后），失败 {len(errors)} 个")
    for p in products[:5]:
        log(f"    + {p['model']:36} [{p['category']}] 参数 {len(p['facts'])} 条")
    if not products:
        log("✗ 没有可用产品，未写库")
        return 1

    all_facts = []
    for p in products:
        for f in p["facts"]:
            all_facts.append({
                "model": p["model"], "display_name": p["display_name"],
                "category": p["category"], "section": f["section"],
                "field_label": f["field_label"], "raw_value": f["raw_value"],
                "source_url": p["source_url"],
            })

    db = Path(DB_PATH)
    bak = backup(db)
    log(f"  已备份 → {bak}")
    log("▶ [3/3] 写入 competitor_products / competitor_facts（只新增不覆盖）…")
    result = write_db(db, products, all_facts, today)
    result.update({
        "ok": True, "fetched_pages": len(targets), "parsed_products": len(products),
        "failed_pages": len(errors), "new_fact_rows": len(all_facts),
        "errors_sample": errors[:3], "backup": bak,
    })
    log(f"  ✔ 机型 {result['products_before']} → {result['products_after']}；"
        f"参数事实 {result['facts_before']} → {result['facts_after']}（海康 {result['hik_facts']} 条）")
    for x in result["by_category"]:
        log(f"    {x['category']}: {x['count']} 款")
    log("HIK_RESULT " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
