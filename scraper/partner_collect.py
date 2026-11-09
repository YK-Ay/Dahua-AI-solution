"""从 partner.dahuatech.com（大华在线·产品中心）爬取更详细的产品（含平台类）。

重要说明
--------
partner.dahuatech.com 的产品接口（/product/api/prod/list 等）需要**已登录的
合作伙伴会话凭证**（Cookie / Token），匿名访问会返回 {"code":10000,"msg":"未授权请求"}。
本脚本支持两种方式提供凭证：
  1) 环境变量 PARTNER_AUTH：完整的 Cookie 字符串（从浏览器登录后复制）
  2) 环境变量 PARTNER_TOKEN：Bearer Token
提供后运行：python -m scraper.partner_collect

未提供凭证时，脚本会明确提示如何获取，不会报错中断。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime

from config.settings import BASE_DIR, DB_PATH

BASE = "https://partner.dahuatech.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _auth_headers() -> dict:
    h = dict(HEADERS)
    cookie = os.environ.get("PARTNER_AUTH")
    token = os.environ.get("PARTNER_TOKEN")
    if cookie:
        h["Cookie"] = cookie
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(path: str, params: str = "") -> dict:
    url = BASE + path + (("?" + params) if params else "")
    req = urllib.request.Request(url, headers=_auth_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _pick(d: dict, *keys: str):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def collect(limit_pages: int = 50, page_size: int = 50) -> int:
    auth = os.environ.get("PARTNER_AUTH") or os.environ.get("PARTNER_TOKEN")
    if not auth:
        print("[跳过] 未检测到 PARTNER_AUTH / PARTNER_TOKEN。")
        print("      请在浏览器登录 partner.dahuatech.com 后，从 DevTools → Network → 任意请求复制 Cookie，")
        print("      设置环境变量后重试：")
        print('        set PARTNER_AUTH=你的Cookie字符串   (Windows)')
        print('        export PARTNER_AUTH=你的Cookie字符串 (Linux/Mac)')
        print("      然后再运行：python -m scraper.partner_collect")
        return 0

    # 1) 类目树：catalogId -> 类目名
    try:
        tree = _get("/product/api/prod/catalog/tree")
    except Exception as e:
        print("[错误] 获取类目树失败：", e)
        return 0
    if tree.get("code") == 10000:
        print("[未授权] 凭证无效或已过期：", tree.get("msg"))
        return 0

    cat_name = {}
    def walk(nodes):
        for n in nodes or []:
            cid = _pick(n, "id", "catalogId", "catId")
            name = _pick(n, "name", "catalogName", "catName")
            if cid and name:
                cat_name[str(cid)] = name
            walk(n.get("children"))
    walk(tree.get("data") or tree.get("result"))

    # 2) 分页拉取产品
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    inserted = 0
    for page in range(1, limit_pages + 1):
        try:
            resp = _get("/product/api/prod/list", f"pageNum={page}&pageSize={page_size}")
        except Exception as e:
            print(f"[错误] 第 {page} 页失败：", e)
            break
        if resp.get("code") == 10000:
            print("[未授权] 凭证无效：", resp.get("msg"))
            break
        rows = resp.get("data", {}).get("list") or resp.get("result", {}).get("list") or []
        if not rows:
            break
        for it in rows:
            model = _pick(it, "prodNum", "model", "code")
            name = _pick(it, "prodName", "name", "title")
            if not model:
                continue
            cid = str(_pick(it, "catalogId", "catId") or "")
            cur.execute(
                """INSERT INTO products (model, display_name, price_stand, class_name, img_url, part_num, direction, pi_status, collected_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(model) DO UPDATE SET
                     display_name=excluded.display_name, class_name=excluded.class_name,
                     img_url=excluded.img_url, price_stand=excluded.price_stand,
                     pi_status=excluded.pi_status, collected_at=excluded.collected_at""",
                (
                    model, name or model,
                    _pick(it, "price", "priceStand", "standPrice"),
                    cat_name.get(cid, _pick(it, "catalogName", "className") or "未分类"),
                    _pick(it, "imgUrl", "img", "imageUrl", "pic"),
                    _pick(it, "partNum", "partNo"),
                    _pick(it, "direction", "dirName"),
                    _pick(it, "status", "piStatus", "saleStatus"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            inserted += 1
        print(f"  第 {page} 页：已处理 {len(rows)} 条（累计 {inserted}）")
        if len(rows) < page_size:
            break
    con.commit()
    con.close()
    print(f"[完成] 共写入/更新 {inserted} 条产品（含平台类目，若接口返回）。")
    return inserted


if __name__ == "__main__":
    n = collect()
    sys.exit(0 if n >= 0 else 1)
