"""公开渠道产品补全采集器（不需要任何登录 / 认证凭证）。

背景
----
原有 `mall_collect.py` 只抓 `cloudChoiceProd`（云商"可选"产品，约 2985 款），
漏掉了站内更大的资料库。本脚本走三条**公开合法**的路：

  1. mall `prodInfoSearch`，关键字用两个空格（长度校验通过、后端当空条件）
     → 返回整个匿名可见产品池（约 3680 款），且带产品简介 / 物料号 / 高清图。
  2. mall `cloudChoiceProd` 全量重抓（老来源，可能含已下架/线下产品）。
  3. 大华官网 `www.dahuatech.com/product/lists/*.html` 公开产品目录页
     → 提取型号与详情页 URL（部分项目产品只在官网公开）。

不绕过任何鉴权；partner.dahuatech.com 的接口一律不碰。

输出：data/public_products.json（规范化后的候选产品，供 merge_products.py 入库）
用法：python scraper/collect_public_products.py [--skip-official] [--out PATH]
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "public_products.json"

MALL = "https://mall.dahuatech.com"
WEB = "https://www.dahuatech.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SEARCH_API = "/product/api/productCenter/prodInfoSearch"
CHOICE_API = "/product/api/productCenter/cloudChoiceProd"
ANON_API = "/user/api/cloudLogin/anonymousLogin"

TAG_RE = re.compile(r"<[^>]+>")
MODEL_RE = re.compile(r"\b(?:DH|DHI|PFM|OPT|EVS|NVR|IPC)-?[A-Z0-9][A-Z0-9.\-/]{2,34}\b", re.I)
# 官网分类页的产品卡片：<a href=".../info/ID.html "> <h3 data-title="型号">型号</h3>
CARD_RE = re.compile(
    r'<a[^>]+/product/info/(\d+)\.html\s*["\'][^>]*>\s*'
    r'<h3[^>]*data-title=["\']([^"\']+)["\'][^>]*>(.*?)</h3>', re.S)
# 同一卡片的图片：<a href=".../info/ID.html " class="link"> <img ... src="..." alt="中文名">
IMG_RE = re.compile(
    r'/product/info/(\d+)\.html\s*["\']\s+class="link"[^>]*>\s*'
    r'<img[^>]*src=["\']([^"\']+)["\'][^>]*alt=["\']([^"\']*)["\']', re.S)


def clean(v, flat=True):
    """去搜索高亮标签 + HTML 反转义。flat=True 时把空白压成单空格。"""
    if v is None or isinstance(v, (int, float, list, dict)):
        return v
    s = TAG_RE.sub("", str(v))
    s = html_lib.unescape(s)
    if flat:
        return re.sub(r"\s+", " ", s).strip()
    return s.strip()


def log(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------
# mall：匿名登录（站点自带的游客登录，非用户账号）
# --------------------------------------------------------------------------
def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Content-Type": "application/json"})
    r = s.post(MALL + ANON_API, json={}, timeout=20)
    tok = (r.json().get("data") or {}).get("accessToken")
    if not tok:
        raise RuntimeError("匿名登录失败：" + r.text[:200])
    s.headers.update({"accessToken": tok})
    return s


def post_json(s, path, payload, tries=3):
    for i in range(tries):
        try:
            r = s.post(MALL + path, json=payload, timeout=40)
            return r.json()
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(1.2 * (i + 1))
    return {}


def pull_search(s, key, label, page_size=1000):
    """按关键字翻页拉全。空格关键字 = 全池。"""
    rows, page = [], 1
    while page <= 30:
        j = post_json(s, SEARCH_API, {
            "key": key, "pageNum": page, "pageSize": page_size,
            "dir": "", "piStatus": "", "priceStart": "", "priceEnd": "", "sortfield": "",
        })
        d = j.get("data") or {}
        lst = d.get("list") or []
        total = d.get("total") or 0
        if j.get("code") != 200 and not lst:
            log(f"  [{label}] 第 {page} 页返回 code={j.get('code')} msg={j.get('msg')}，停止")
            break
        rows.extend(lst)
        log(f"  [{label}] page {page}: +{len(lst)} (累计 {len(rows)}/{total})")
        if not lst or page * page_size >= total:
            break
        page += 1
        time.sleep(0.35)
    return rows


def pull_choice(s):
    rows, page = [], 1
    while page <= 60:
        j = post_json(s, CHOICE_API, {"pageNum": page, "pageSize": 100})
        d = j.get("data") or {}
        lst = d.get("list") or []
        total = d.get("total") or 0
        rows.extend(lst)
        if page % 10 == 1 or not lst:
            log(f"  [cloudChoice] page {page}: +{len(lst)} (累计 {len(rows)}/{total})")
        if not lst or len(lst) < 100:
            break
        page += 1
        time.sleep(0.3)
    return rows


def norm_mall(it, source, now):
    """把两种 mall 接口统一成同一套字段。"""
    cat = it.get("catalogId") or []
    desc = clean(it.get("prodDesc"), flat=False) if it.get("prodDesc") else None
    return {
        "prod_id": clean(it.get("prodId")),
        "model": clean(it.get("model")),
        "display_name": clean(it.get("displayName") or it.get("name")),
        "price_stand": it.get("priceStand"),
        "pi_sysj": clean(it.get("piSysj")),
        "pi_status": clean(it.get("piStatus")),
        "class_name": clean(it.get("className")),
        "catalog_ids": cat if isinstance(cat, list) else [],
        "img_url": clean(it.get("image") or it.get("piPath")),
        "part_num": clean(it.get("partNum")),
        "prod_desc": desc,
        "spec_value": clean(it.get("specValue")),
        "source": source,
        "source_url": MALL + (SEARCH_API if source == "mall_public_search" else CHOICE_API),
        "collected_at": now,
    }


# --------------------------------------------------------------------------
# 大华官网：公开产品目录页（服务端渲染，无需登录）
# --------------------------------------------------------------------------
def official_catalog(s):
    from concurrent.futures import ThreadPoolExecutor
    try:
        t = s.get(WEB + "/product.html", timeout=30).text
    except Exception as e:
        log("  官网总入口读取失败：", e)
        return []
    # 菜单文字 → 类目名（给官网产品补一个可读分类）
    cat_names = {}
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']*/product/lists/[^"\']+)["\'][^>]*>(.*?)</a>', t, re.S):
        nm = clean(TAG_RE.sub("", m.group(2)))
        if nm and 1 < len(nm) < 30:
            cat_names[urljoin(WEB, m.group(1))] = nm
    # 收集所有产品分类页完整 URL（含 ?area= 子区域，产品不同）
    urls = re.findall(r'(?:https://www\.dahuatech\.com)?/product/lists/\d+\.html(?:\?[^"\'<>\s]*)?', t)
    full = []
    for u in urls:
        u = urljoin(WEB, u)
        if u not in full:
            full.append(u)
    log(f"  官网产品分类页：{len(full)} 个（含子区域），已识别类目名 {len(cat_names)} 个")

    def fetch(u):
        try:
            return u, requests.get(u, headers={"User-Agent": UA}, timeout=25).text
        except Exception:
            return u, ""

    found = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        for i, (u, page) in enumerate(ex.map(fetch, full), 1):
            if not page:
                continue
            imgs = {}
            for m in IMG_RE.finditer(page):
                imgs.setdefault(m.group(1), (clean(m.group(2)), clean(m.group(3))))
            for m in CARD_RE.finditer(page):
                info_id, model = m.group(1), clean(m.group(2))
                img, alt = imgs.get(info_id, (None, None))
                found[info_id] = {
                    "prod_id": "official:" + info_id,
                    "model": model,
                    "display_name": alt or clean(m.group(3)) or model,
                    "price_stand": None,
                    "pi_sysj": None, "pi_status": None,
                    "class_name": cat_names.get(u),
                    "catalog_ids": [],
                    "img_url": img, "part_num": None,
                    "prod_desc": None, "spec_value": None,
                    "source": "official_catalog",
                    "source_url": f"{WEB}/product/info/{info_id}.html",
                    "list_url": u,
                    "collected_at": datetime.now().isoformat(timespec="seconds"),
                }
            if i % 80 == 0:
                log(f"  官网分类页 {i}/{len(full)}：已累计 {len(found)} 个产品")
    return list(found.values())


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-official", action="store_true", help="跳过官网目录采集")
    ap.add_argument("--official-only", action="store_true", help="只跑官网目录并追加进已有 JSON")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    now = datetime.now().isoformat(timespec="seconds")
    out = Path(args.out)
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    if args.official_only:
        if not out.exists():
            raise SystemExit(f"找不到 {out}，请先跑一次完整采集")
        data = json.loads(out.read_text(encoding="utf-8"))
        log("只跑官网公开产品目录（不动已有的 mall 数据）…")
        official = official_catalog(s)
        known = {p.get("model") for p in data["products"]}
        add = [p for p in official if p.get("model") and p["model"] not in known]
        data["products"] = [p for p in data["products"]
                            if p.get("source") != "official_catalog"] + add
        data["official_count"] = len(add)
        data["official_collected_at"] = now
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"\n完成：官网共 {len(official)} 个产品，其中 {len(add)} 个型号是 mall 池里没有的 → {out}")
        return

    mall = make_session()
    log("匿名会话已建立（游客态，无任何账号凭证）\n")

    log("① 拉取完整匿名产品池（prodInfoSearch 空条件）…")
    pool = pull_search(mall, "  ", "全池")
    log("② 补拉 DH 型号子集（补齐搜索索引差异）…")
    pool += pull_search(mall, "DH", "DH补充")
    log("③ 重抓 cloudChoiceProd（老来源，含线下/停产）…")
    choice = pull_choice(mall)

    merged = {}
    for src, rows in (("mall_public_search", pool), ("mall_cloud_choice", choice)):
        for it in rows:
            r = norm_mall(it, src, now)
            pid = r["prod_id"]
            if not pid:
                continue
            if pid not in merged:
                merged[pid] = r
            elif not merged[pid]["prod_desc"] and r["prod_desc"]:
                merged[pid]["prod_desc"] = r["prod_desc"]  # 只补空的详情
    log(f"\nmall 侧去重后：{len(merged)} 款")

    official = []
    if not args.skip_official:
        log("\n④ 采集大华官网公开产品目录…")
        official = official_catalog(s)
        log(f"官网目录：{len(official)} 个产品")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "collected_at": now,
        "sources": ["mall_public_search", "mall_cloud_choice", "official_catalog"],
        "mall_count": len(merged),
        "official_count": len(official),
        "products": list(merged.values()) + official,
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"\n完成 → {out}（共 {len(data['products'])} 条候选，{out.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()
