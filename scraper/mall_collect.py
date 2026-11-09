"""大华云商产品采集器（W2）。

流程：匿名登录拿 token → 翻页拉全站产品 → 落 SQLite products 表
→ 按类目标注归属方向（园区楼宇 / 交管 / 其他）。
"""
import json
import sqlite3
import sys
import time
from datetime import datetime

import requests

from config.settings import DB_PATH, UA

BASE = "https://mall.dahuatech.com"

# 类目 → 方向映射（按 className 匹配）
TRAFFIC_KEYWORDS = ["车行出入口", "抓拍", "道闸", "智能交通", "智慧停车", "电子警察", "车位"]
PARKING_KEYWORDS = [
    "网络摄像机", "球机", "录像机", "NVR", "硬盘", "PoE", "交换机",
    "人行出入口", "门禁", "商显", "显示器", "综合平台", "报警", "配件",
    "布线", "机柜", "电源", "支架", "拾音", "AP", "路由器", "光纤", "无线",
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    return conn


def login(sess):
    r = sess.post(BASE + "/user/api/cloudLogin/anonymousLogin", json={}, timeout=20)
    tok = r.json()["data"]["accessToken"]
    sess.headers.update({"accessToken": tok})
    return tok


def classify(class_name):
    cn = class_name or ""
    if any(k in cn for k in TRAFFIC_KEYWORDS):
        return "交管"
    if any(k in cn for k in PARKING_KEYWORDS):
        return "园区楼宇"
    return "其他"


def upsert(conn, p, direction):
    year = None
    if p.get("piSysj"):
        try:
            year = int(p["piSysj"][:4])
        except ValueError:
            year = None
    conn.execute(
        """INSERT INTO products (prod_id, model, display_name, price_stand, pi_sysj,
           year_new, pi_status, class_name, catalog_ids, img_url, part_num, direction, collected_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(prod_id) DO UPDATE SET
             model=excluded.model, price_stand=excluded.price_stand,
             pi_status=excluded.pi_status, direction=excluded.direction""",
        (
            p.get("prodId"), p.get("model"), p.get("displayName"), p.get("priceStand"),
            p.get("piSysj"), year, p.get("piStatus"), p.get("className"),
            json.dumps(p.get("catalogId") or [], ensure_ascii=False),
            p.get("piPath"), p.get("partNum"), direction,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Content-Type": "application/json"})
    login(sess)
    conn = get_conn()

    page = 1
    total_in = 0
    while True:
        r = sess.post(BASE + "/product/api/productCenter/cloudChoiceProd",
                      json={"pageNum": page, "pageSize": 100}, timeout=30)
        d = r.json().get("data") or {}
        lst = d.get("list") or []
        if not lst:
            break
        for p in lst:
            upsert(conn, p, classify(p.get("className")))
        conn.commit()
        total_in += len(lst)
        print(f"page {page}: +{len(lst)} (累计 {total_in}/{d.get('total')})")
        if len(lst) < 100:
            break
        page += 1
        time.sleep(0.4)

    conn.close()
    print(f"\n完成：入库 {total_in} 条产品")


if __name__ == "__main__":
    main()
