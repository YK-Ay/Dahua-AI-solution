"""W4 竞品事实采集：双方官网公开参数页 → competitor_facts。

只取官网公开页面，不改写参数；每条事实保留 source_url / evidence / retrieved_at，
便于对比表逐条回溯。对标为"同档位代表款"（类目级），不是渠道 SKU。

用法：
    python -m scraper.collect_competitor
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from config.settings import BASE_DIR, DB_PATH, RAW_DIR, REQUEST_DELAY, TIMEOUT
from scraper.density import annotate

DAHUA_PAGE = "https://www.dahuatech.com/product/info/{pid}.html"
DAHUA_API = "https://www.dahuatech.com/ajax/product_new/{pid}/2"
HIK_PAGE = "https://www.hikvision.com/cn/products/pdplist/{pid}/"

# 同档位代表款：两侧都为该类目下的主力规格，便于逐字段对齐
TARGETS = [
    {
        "category": "网络摄像机",
        "dahua": {"pid": 14147, "model": "DH-IPC-HFW5443M1-YL-PV-AS"},
        "hik": {"pid": 71161, "model": "DS-2CD1245(D)-LA"},
    },
    {
        "category": "NVR",
        "dahua": {"pid": 13128, "model": "DH-NVR5532-I"},
        "hik": {"pid": 148362, "model": "DS-7700N-K4-V3"},
    },
    {
        "category": "PoE交换机",
        "dahua": {"pid": 9757, "model": "DH-AS3900C-24GT4GF-370"},
        "hik": {"pid": 110077, "model": "DS-3E0526P-E/M"},
    },
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")  # 本机默认 journal 写入被拦截
    return conn


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def fetch_hik(pid: int) -> tuple[str, str, list[tuple[str, str, str]]]:
    """海康：pdplist 页服务端渲染，规格区为 ul[data-target=分区] + span.item-title/desc。"""
    url = HIK_PAGE.format(pid=pid)
    html = curl_requests.get(url, impersonate="chrome", timeout=TIMEOUT).text
    (RAW_DIR / f"comp_hik_{pid}.html").write_text(html, encoding="utf-8")

    soup = BeautifulSoup(html, "html.parser")
    model_node = soup.select_one("div.model span")
    model = _clean(model_node.get_text()) if model_node else ""
    title = _clean(soup.title.string.split(" - ")[0]) if soup.title and soup.title.string else ""

    rows, seen = [], set()
    for ul in soup.select("ul.tech-specs-accordion-desc-list"):
        section = _clean(ul.get("data-target") or "")
        for li in ul.select("li.tech-specs-accordion-desc-item"):
            t, d = li.select_one("span.item-title"), li.select_one("span.item-desc")
            if not (t and d):
                continue
            label, value = _clean(t.get_text()), _clean(d.get_text())
            if not label or not value or label in seen:
                continue
            seen.add(label)  # 同页多配置段（NVR 32/16/8 路）取第一段
            rows.append((section, label, value))
    return model, title, rows


def fetch_dahua(pid: int) -> tuple[str, list[dict]]:
    """大华：详情页参数异步接口返回 parameterList JSON。"""
    page = DAHUA_PAGE.format(pid=pid)
    resp = requests.get(DAHUA_API.format(pid=pid), headers={"User-Agent": UA}, timeout=TIMEOUT)
    (RAW_DIR / f"comp_dh_{pid}.json").write_text(resp.text, encoding="utf-8")

    page_html = requests.get(page, headers={"User-Agent": UA}, timeout=TIMEOUT).text
    title = ""
    m_title = re.search(r"<title>(.*?)</title>", page_html, re.S)
    if m_title:
        title = _clean(m_title.group(1)).split("-")[0]

    m = re.search(r"const parameterList = (\[[\s\S]*?\]);", resp.text)
    if not m:
        raise ValueError(f"大华参数接口未返回 parameterList（pid={pid}）")

    rows, seen = [], set()
    for tab in json.loads(m.group(1)):
        section = _clean(str(tab.get("tabTitle", "")))
        for item in tab.get("list", []):
            label, value = _clean(str(item.get("label", ""))), _clean(str(item.get("value", "")))
            if not label or not value or label in seen:
                continue  # 同页重复字段（如背光补偿）取第一条
            seen.add(label)
            rows.append({"section": section, "label": label, "value": value})
    return title, rows


def upsert(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """INSERT INTO competitor_facts
           (brand, category, model, display_name, section, field_label, raw_value,
            source_url, page_kind, evidence, retrieved_at)
           VALUES (:brand,:category,:model,:display_name,:section,:field_label,:raw_value,
                   :source_url,:page_kind,:evidence,:retrieved_at)
           ON CONFLICT(brand, model, field_label) DO UPDATE SET
             category=excluded.category, display_name=excluded.display_name,
             section=excluded.section, raw_value=excluded.raw_value,
             source_url=excluded.source_url, page_kind=excluded.page_kind,
             evidence=excluded.evidence, retrieved_at=excluded.retrieved_at""",
        row,
    )


def save_product(conn, brand, category, model, display_name, rows, source_url):
    conn.execute(
        "DELETE FROM competitor_facts WHERE brand=? AND category=? AND model=?",
        (brand, category, model),
    )
    now = datetime.now().isoformat(timespec="seconds")
    page_kind = annotate(" ".join(f"{r['label']} {r['value']}" for r in rows))["page_kind"]
    for r in rows:
        upsert(conn, {
            "brand": brand, "category": category, "model": model,
            "display_name": display_name, "section": r.get("section", ""),
            "field_label": r["label"], "raw_value": r["value"],
            "source_url": source_url, "page_kind": page_kind,
            "evidence": f"{r.get('section', '')}｜{r['label']}｜{r['value']}"[:300],
            "retrieved_at": now,
        })
    conn.commit()
    print(f"  + {brand} {category} {model}：{len(rows)} 个字段（{page_kind}）")


def normalise(row: tuple[str, str, str] | dict) -> dict:
    if isinstance(row, dict):
        return row
    section, label, value = row
    return {"section": section, "label": label, "value": value}


def main() -> None:
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript((BASE_DIR / "db" / "schema.sql").read_text(encoding="utf-8"))

    total = 0
    for target in TARGETS:
        category = target["category"]
        print(f"\n[{category}]")

        dh = target["dahua"]
        try:
            title, rows = fetch_dahua(dh["pid"])
            save_product(conn, "大华", category, dh["model"], title, rows,
                         DAHUA_PAGE.format(pid=dh["pid"]))
            total += len(rows)
        except Exception as exc:  # 单侧失败不影响另一侧
            print(f"  x 大华 {dh['model']} 采集失败：{exc}")

        hk = target["hik"]
        try:
            model, title, rows = fetch_hik(hk["pid"])
            save_product(conn, "海康", category, model or hk["model"], title,
                         [normalise(r) for r in rows], HIK_PAGE.format(pid=hk["pid"]))
            total += len(rows)
        except Exception as exc:
            print(f"  x 海康 {hk['model']} 采集失败：{exc}")
        time.sleep(REQUEST_DELAY)

    conn.close()
    print(f"\n完成：竞品事实 {total} 条 → {DB_PATH}")


if __name__ == "__main__":
    main()
