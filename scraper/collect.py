"""W1 采集器：抓三个行业列表页 → 详情页 → 清洗正文 → 落 SQLite。

用法：
    python -m scraper.collect            # 全量抓三个行业
    python -m scraper.collect --reset    # 清空 pages 表重抓
"""
import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config.settings import (DB_PATH, INDUSTRIES, MAX_DETAIL_PER_INDUSTRY,
                             RAW_DIR, REQUEST_DELAY, TIMEOUT, UA)
from scraper.density import annotate

LIST_HREF_RE = re.compile(r"/cases/info/\d+\.html")


def get_conn():
    """统一连接。本机环境 quirk：默认 journal 文件写入被拦截，事务静默回滚；
    journal 放内存后正常（单用户采集工具，崩溃可重抓，无事务保护损失）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    return conn


def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def fetch(sess, url):
    r = sess.get(url, timeout=TIMEOUT)
    # 优先信响应头声明的字符集；仅在未声明时用 chardet 猜测，
    # 避免把站点正确的 UTF-8 声明覆盖成误判编码，导致正文乱码入库
    if not r.encoding:
        r.encoding = r.apparent_encoding or "utf-8"
    time.sleep(REQUEST_DELAY)
    return r


def collect_detail_urls(sess, list_url):
    """从行业列表页提取去重后的详情页 URL。"""
    soup = BeautifulSoup(fetch(sess, list_url).text, "lxml")
    urls, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = LIST_HREF_RE.search(a["href"])
        if not m:
            continue
        full = urljoin(list_url, a["href"])
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def extract_detail(soup):
    """从详情页提取 (标题, 正文纯文本)。"""
    for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        t.decompose()
    box = soup.select_one(".detail-cnt") or soup.select_one(".solution-detail-wrap")
    if not box:
        return None, ""
    title = (soup.title.string or "").split("-")[0].strip() if soup.title else ""
    text = re.sub(r"\n{3,}", "\n\n", box.get_text("\n", strip=True))
    return title, text


def save_raw(url, html):
    pid = re.search(r"/info/(\d+)\.html", url).group(1)
    path = RAW_DIR / f"{pid}.html"
    path.write_text(html, encoding="utf-8")
    return str(path)


def upsert_page(conn, row):
    conn.execute(
        """INSERT INTO pages (url, industry, title, fetched_at, raw_html_path,
           content_text, content_len, density_score, tech_hits, market_hits, page_kind)
           VALUES (:url,:industry,:title,:fetched_at,:raw_html_path,
                   :content_text,:content_len,:density_score,:tech_hits,:market_hits,:page_kind)
           ON CONFLICT(url) DO UPDATE SET
             title=excluded.title, content_text=excluded.content_text,
             content_len=excluded.content_len, density_score=excluded.density_score,
             tech_hits=excluded.tech_hits, market_hits=excluded.market_hits,
             page_kind=excluded.page_kind""",
        row,
    )


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    if args.reset:
        conn.execute("DELETE FROM pages")
        print("[reset] pages 表已清空")

    sess = get_session()
    total = 0
    for industry, list_url in INDUSTRIES.items():
        detail_urls = collect_detail_urls(sess, list_url)[:MAX_DETAIL_PER_INDUSTRY]
        print(f"\n[{industry}] 列表页发现 {len(detail_urls)} 个详情页")
        for u in detail_urls:
            try:
                soup = BeautifulSoup(fetch(sess, u).text, "lxml")
                title, text = extract_detail(soup)
                if not text:
                    print(f"  ! 无正文容器: {u}")
                    continue
                raw_path = save_raw(u, str(soup))
                d = annotate(text)
                upsert_page(conn, {
                    "url": u, "industry": industry, "title": title,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "raw_html_path": raw_path, "content_text": text,
                    "content_len": len(text), **d,
                })
                conn.commit()
                total += 1
                print(f"  + [{d['page_kind']:>15}] tech={d['tech_hits']:>2} mkt={d['market_hits']:>2}  {title[:30]}")
            except Exception as e:
                print(f"  x 抓取失败 {u}: {e}")

    conn.close()
    print(f"\n完成：共入库 {total} 页 → {DB_PATH}")


if __name__ == "__main__":
    main()
