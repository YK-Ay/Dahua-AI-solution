"""大华云商方案配单采集器（API 直连版，W3 补抓包产物）。

流程：匿名登录 → /smb/api/scheme/list 拉全量方案 → 按 catname 过滤 4 类
→ /smb/api/detail/getSchemeNew 拿元信息 → /product/api/lectotype/products/cloudProds 拿配单
→ 落 solutions / solution_items 表。

替代原来的浏览器 DOM 提取（换电脑采集手册），不依赖 F12 控制台，可无人值守批量跑。
"""
import json
import sqlite3
import sys
import time
from datetime import datetime

import requests

from config.settings import DB_PATH, UA

BASE = "https://mall.dahuatech.com"

# 目标四类方案配单（按方案 catname 过滤）
TARGET_CATS = ["小区社区", "工厂园区", "企业办公", "智能交通"]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def login(sess):
    r = sess.post(BASE + "/user/api/cloudLogin/anonymousLogin", json={}, timeout=20)
    tok = r.json()["data"]["accessToken"]
    sess.headers.update({"accessToken": tok})
    return tok


def fetch_all_schemes(sess):
    """拉取全部方案（scheme/list 不分页过滤，按 page 翻页）。"""
    out = []
    page = 1
    while True:
        r = sess.post(BASE + "/smb/api/scheme/list",
                      json={"page": page, "pageSize": 50}, timeout=30)
        d = r.json().get("data") or {}
        lst = d.get("list") or []
        out.extend(lst)
        print(f"  scheme/list page {page}: +{len(lst)} (累计 {len(out)}/{d.get('total')})")
        if len(lst) < 50:
            break
        page += 1
        time.sleep(0.3)
    return out


def fetch_scheme_detail(sess, sol_id):
    """方案元信息（名称/场景/详情图/浏览量）。"""
    r = sess.post(BASE + "/smb/api/detail/getSchemeNew",
                  json={"id": sol_id, "showChannel": "0"},
                  headers={"Channel": "PC", "Version": "1.0"}, timeout=20)
    return (r.json().get("data") or {})


def fetch_bom(sess, sol_id):
    """方案配单（分组 + 产品）。"""
    r = sess.post(BASE + "/product/api/lectotype/products/cloudProds",
                  json={"lectotypeId": sol_id}, timeout=30)
    return r.json().get("data") or []


def upsert_solution(conn, sol_id, meta, raw_list_item):
    conn.execute(
        """INSERT INTO solutions (sol_id, name, scene_id, scene_name, detail_img,
           release_time, view_count, raw_json, collected_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(sol_id) DO UPDATE SET
             name=excluded.name, scene_id=excluded.scene_id,
             scene_name=excluded.scene_name, detail_img=excluded.detail_img,
             release_time=excluded.release_time, view_count=excluded.view_count,
             raw_json=excluded.raw_json, collected_at=excluded.collected_at""",
        (
            sol_id,
            meta.get("name") or raw_list_item.get("name"),
            meta.get("sceneId") or raw_list_item.get("sceneId"),
            meta.get("sceneName") or raw_list_item.get("catname"),
            meta.get("image") or raw_list_item.get("path"),
            meta.get("releaseTime") or raw_list_item.get("createTime"),
            meta.get("view") or raw_list_item.get("viewCount"),
            json.dumps({"list_item": raw_list_item, "detail": meta}, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def upsert_items(conn, sol_id, groups):
    conn.execute("DELETE FROM solution_items WHERE sol_id=?", (sol_id,))
    rows = []
    for g in groups:
        grp_no = int(g.get("serialNum") or 0) if str(g.get("serialNum", "")).isdigit() else 0
        grp_name = g.get("name") or ""
        for p in g.get("lectotypeProductList") or []:
            cols = p.get("columns") or {}
            rows.append((
                sol_id,
                grp_no,
                grp_name,
                cols.get("modelInside") or cols.get("model"),
                cols.get("displayName"),
                cols.get("specValue") or "",
                cols.get("priceStand") or cols.get("cloudPrice") or 0,
            ))
    if rows:
        conn.executemany(
            """INSERT INTO solution_items (sol_id, grp_no, grp_name, model, display_name, spec, price)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Content-Type": "application/json"})
    login(sess)
    conn = get_conn()

    print("步骤 1：拉取全量方案列表...")
    schemes = fetch_all_schemes(sess)
    target = [s for s in schemes if s.get("catname") in TARGET_CATS]
    print(f"  全量 {len(schemes)} 个，命中 4 类 {len(target)} 个：")
    for cat in TARGET_CATS:
        n = sum(1 for s in target if s.get("catname") == cat)
        print(f"    - {cat}: {n}")

    print("\n步骤 2：逐个拉取元信息 + 配单...")
    total_items = 0
    for idx, s in enumerate(target, 1):
        sid = s["id"]
        name = s.get("name")
        cat = s.get("catname")
        try:
            meta = fetch_scheme_detail(sess, sid)
            groups = fetch_bom(sess, sid)
            upsert_solution(conn, sid, meta, s)
            n_items = upsert_items(conn, sid, groups)
            conn.commit()
            total_items += n_items
            print(f"  [{idx}/{len(target)}] {name} [{cat}] 配单 {n_items} 条")
        except Exception as e:
            print(f"  [{idx}/{len(target)}] {name} 失败: {e}")
        time.sleep(0.3)

    conn.close()
    print(f"\n完成：入库方案 {len(target)} 个，配单项 {total_items} 条")


if __name__ == "__main__":
    main()
