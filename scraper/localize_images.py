"""把知识库里的远程图片下载成本地缩略图（离线可用）。

用户将长时间处于无网环境：products.img_url / solutions.detail_img 全是
大华 CDN 上的远程地址，断网后知识库产品卡片会变成一排破图。
本脚本下载后统一转成 320px 以内的 WebP 缩略图，落到
`web/static/img/products/` 与 `web/static/img/solutions/`，
并把相对 URL 写进新列 `img_local`（原列保留，随时可回退在线模式）。

特性：幂等（已存在的跳过）、并发、单张失败不影响整体、只新增不覆盖原字段。
用法：
    python -m scraper.localize_images --limit 30      # 小批试跑
    python -m scraper.localize_images                 # 全量
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

from config.settings import BASE_DIR, DB_PATH, UA

IMG_DIR = BASE_DIR / "web" / "static" / "img"
PRODUCT_DIR = IMG_DIR / "products"
SOLUTION_DIR = IMG_DIR / "solutions"
MAX_SIDE = 320
QUALITY = 72
TIMEOUT = (6, 18)


def log(*a):
    print(*a, flush=True)


def ensure_columns(con: sqlite3.Connection) -> None:
    for table in ("products", "solutions"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if "img_local" not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN img_local TEXT")
    con.commit()


def name_for(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:20] + ".webp"


def fetch_one(url: str, folder: Path) -> tuple[str, str]:
    """返回 (相对 URL 或空, 错误说明)。"""
    if not url or not url.startswith("http"):
        return "", "非 http 地址"
    rel_dir = "products" if folder == PRODUCT_DIR else "solutions"
    fname = name_for(url)
    target = folder / fname
    rel = f"/static/img/{rel_dir}/{fname}"
    if target.exists() and target.stat().st_size > 0:
        return rel, ""
    last = ""
    for attempt in range(2):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content))
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="WEBP", quality=QUALITY, method=4)
            data = buf.getvalue()
            folder.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return rel, ""
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"[:120]
            time.sleep(0.4)
    return "", last


def collect_jobs(con: sqlite3.Connection, limit: int) -> list[dict]:
    jobs = []
    rows = con.execute(
        "SELECT prod_id AS key, img_url AS url FROM products "
        "WHERE img_url IS NOT NULL AND img_url<>'' "
        "AND (img_local IS NULL OR img_local='') ORDER BY prod_id"
    ).fetchall()
    jobs += [{"table": "products", "key": r[0], "url": r[1], "folder": PRODUCT_DIR} for r in rows]
    rows = con.execute(
        "SELECT sol_id AS key, detail_img AS url FROM solutions "
        "WHERE detail_img IS NOT NULL AND detail_img<>'' "
        "AND (img_local IS NULL OR img_local='') ORDER BY sol_id"
    ).fetchall()
    jobs += [{"table": "solutions", "key": r[0], "url": r[1], "folder": SOLUTION_DIR} for r in rows]
    if limit:
        jobs = jobs[:limit]
    return jobs


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="知识库图片本地化（离线可用）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张，0 表示全量")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(str(DB_PATH))
    ensure_columns(con)
    jobs = collect_jobs(con, args.limit)
    con.close()
    log(f"待本地化图片 {len(jobs)} 张（并发 {args.workers}）")
    if args.dry_run:
        log("[空跑] 前 5 个：" + ", ".join(j["url"][:80] for j in jobs[:5]))
        return 0

    ok, fail = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, j["url"], j["folder"]): j for j in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            j = futures[fut]
            try:
                rel, err = fut.result()
            except Exception as exc:  # noqa: BLE001
                rel, err = "", f"{type(exc).__name__}: {exc}"
            if rel:
                ok.append((j["table"], j["key"], rel))
            else:
                fail.append({"key": j["key"], "url": j["url"], "error": err})
            if i % 200 == 0 or i == len(jobs):
                log(f"  进度 {i}/{len(jobs)} · 成功 {len(ok)} · 失败 {len(fail)}")

    con = sqlite3.connect(str(DB_PATH))
    for table, key, rel in ok:
        con.execute(f"UPDATE {table} SET img_local=? WHERE {'prod_id' if table == 'products' else 'sol_id'}=?",
                    (rel, key))
    con.commit()
    have = {
        t: con.execute(f"SELECT COUNT(*) FROM {t} WHERE img_local IS NOT NULL AND img_local<>''").fetchone()[0]
        for t in ("products", "solutions")
    }
    con.close()
    size_mb = sum(p.stat().st_size for p in IMG_DIR.rglob("*.webp")) / 1024 / 1024 if IMG_DIR.exists() else 0
    log(f"完成：本次成功 {len(ok)} 张，失败 {len(fail)} 张")
    log(f"库内已有本地图：产品 {have['products']} / 方案 {have['solutions']}；磁盘占用 {size_mb:.1f} MB")
    for f in fail[:5]:
        log(f"  ! {f['key']} {f['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
