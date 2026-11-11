"""中国政府采购网（ccgp.gov.cn）中标公告采集器。

目的
----
云商公开方案库只有 53 份配单，且平台只出现过 DH-SMB-X1/H2；而实际项目
（尤其 ICC 平台）大多体现在政府采购中标公告里。本脚本从**公开页面**抓取
"主要标的信息"表格，得到「项目 → 品牌 → 型号 → 数量 → 单价」的真实记录，
用于补平台选型证据与价格区间。

合规说明
--------
· 只抓匿名可见的公开页面，不登录、不绕过任何鉴权、不解析验证码；
· 站内搜索接口（search.ccgp.gov.cn）对脚本限流，因此改走
  "中标公告列表页 → 详情页" 的公开浏览路径；
· 每次请求间隔默认 2 秒，命中限流会退避重试，不并发冲击站点。

用法
----
    python -m scraper.collect_tenders                 # 默认 8 页列表、最多抓 120 个详情
    python -m scraper.collect_tenders --pages 20 --limit 300
    python -m scraper.collect_tenders --dry-run       # 只统计不落库
    python -m scraper.collect_tenders --keyword ICC   # 只看含 ICC 的明细（仍全量入库）
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH  # noqa: E402

BASE = "http://www.ccgp.gov.cn"
# (列表路径, 来源标签)
LISTINGS = [("/cggg/dfgg/zbgg/", "ccgp_地方"), ("/cggg/zygg/zbgg/", "ccgp_中央")]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "http://www.ccgp.gov.cn/",
}

# 标题粗筛：只对可能含安防/信息化设备的项目抓详情，控制请求量
TITLE_KW = ("安防", "监控", "视频", "平台", "智能", "弱电", "信息化", "智慧", "园区",
            "校园", "学校", "医院", "政务", "服务中心", "看守所", "监狱", "公安",
            "交警", "交通", "雪亮", "楼宇", "机房", "数据中心", "应急", "综治")

MODEL_RE = re.compile(r"\b(?:DH|DHI|ICC|NVR|IPC|EVS)[- ]?[A-Z0-9][A-Z0-9.\-/]{2,34}\b", re.I)
DAHUA_RE = re.compile(r"大华|DAHUA", re.I)
NUM_RE = re.compile(r"^[¥￥]?\s*\d+(?:\.\d+)?$")
# "货物类"逐行/连写块格式的字段边界（公告里常把一行所有字段连在一起）
KV_RE = re.compile(r"(名称|品牌（如有）|品牌|规格型号|数量|单价（元）|单价)\s*[:：]")

PROVINCES = ("北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏",
             "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西",
             "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆")

INDUSTRY_KW = [
    ("智慧校园", ("校园", "学校", "学院", "大学", "中学", "小学", "教育", "实训")),
    ("医疗", ("医院", "卫生院", "医疗", "疾控", "妇幼")),
    ("公安司法", ("看守所", "监狱", "公安", "派出所", "戒毒", "法院", "检察院", "司法")),
    ("智慧交管", ("交警", "交通", "公路", "高速", "轨道")),
    ("政务", ("政务", "服务中心", "行政审批", "市民")),
    ("智慧园区", ("园区", "厂区", "开发区", "工业")),
    ("商业综合体", ("商场", "商业", "综合体", "写字楼", "办公")),
    ("金融", ("银行", "金融", "农信", "信用社")),
    ("文旅", ("景区", "场馆", "博物馆", "文旅", "公园")),
]


def log(*a) -> None:
    print(*a, flush=True)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def get(sess: requests.Session, url: str, tries: int = 3, timeout: int = 30) -> str | None:
    """带退避的 GET；命中"频繁访问"或超时会等待后重试。"""
    for attempt in range(1, tries + 1):
        try:
            r = sess.get(url, timeout=timeout)
            r.encoding = "utf-8"
            text = r.text
            if "频繁访问" in text:
                wait = 15 * attempt
                log(f"    [限流] 等待 {wait}s 后重试（{attempt}/{tries}）")
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return text
            log(f"    [HTTP {r.status_code}] {url}")
        except requests.RequestException as e:
            log(f"    [重试 {attempt}/{tries}] {type(e).__name__}: {e}")
            time.sleep(5 * attempt)
    return None


def clean_cell(v: str) -> str:
    v = v.replace("&nbsp;", " ")
    v = re.sub(r"<[^>]+>", "", v)
    return re.sub(r"\s+", " ", v).strip(" |\u3000")


def html_to_lines(html: str) -> list[str]:
    text = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</t[dh]>", "|", text, flags=re.I)
    text = re.sub(r"</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t\r\u3000]+", " ", raw).strip(" | ")
        if line:
            lines.append(line)
    return lines


def listing_items(sess: requests.Session, path: str, pages: int, delay: float):
    """翻列表页，产出 (url, title, publish_date)。"""
    for page in range(1, pages + 1):
        suffix = "index.htm" if page == 1 else f"index_{page - 1}.htm"
        url = BASE + path + suffix
        html = get(sess, url)
        if not html:
            return
        found = 0
        for href, title in re.findall(
                r'<a[^>]+href="([^"]*t\d{8}_\d+\.htm)"[^>]*>([^<]{4,200})</a>', html):
            full = href if href.startswith("http") else BASE + path + href.lstrip("./")
            date_m = re.search(r"t(\d{4})(\d{2})(\d{2})_", full)
            pub = f"{date_m.group(1)}-{date_m.group(2)}-{date_m.group(3)}" if date_m else ""
            yield full, clean_cell(title), pub
            found += 1
        log(f"  列表 {path} 第 {page} 页：{found} 条")
        if not found:
            return
        time.sleep(delay)


def pick_region(text: str) -> str:
    for p in PROVINCES:
        if p in text:
            return p
    return ""


def pick_industry(text: str) -> str:
    for name, kws in INDUSTRY_KW:
        if any(k in text for k in kws):
            return name
    return ""


def item_category(name: str, model: str) -> str:
    s = f"{name}{model}"
    if "平台" in s or "软件" in s:
        return "平台"
    if "拼接" in s or "监视器" in s or "显示" in s:
        return "显示"
    if "解码" in s:
        return "解码器"
    if "硬盘" in s:
        return "硬盘"
    if "NVR" in s or "EVS" in s or "存储" in s:
        return "存储"
    if "交换机" in s:
        return "交换机"
    if "摄像" in s or "球机" in s or "枪机" in s:
        return "摄像机"
    return "其他"


def parse_block_items(lines: list[str]) -> list[dict]:
    """解析"货物类 名称/品牌/规格型号/数量/单价"块格式。

    公告排版差异很大：有的每字段一行，有的整段连写
    （如「名称：智能综合管理平台品牌（如有）：大华规格型号：DH-ICC-B8900-U-PRO数量：1个单价：2700.00」），
    因此按字段名做边界扫描，再按「名称」分组。
    """
    blob = "\n".join(lines)
    marks = list(KV_RE.finditer(blob))
    items: list[dict] = []
    cur: dict | None = None
    for i, m in enumerate(marks):
        key = m.group(1)
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(blob), m.end() + 60)
        val = clean_cell(blob[m.end():end])[:60]
        if key == "名称":
            # 跳过"供应商名称/项目名称/采购人名称"等非标的字段
            if re.search(r"(供应商|项目|采购|单位|包组)$", blob[max(0, m.start() - 6):m.start()]):
                continue
            if cur and (cur["model"] or DAHUA_RE.search(cur["brand"])):
                items.append(cur)
            cur = {"item_name": val.replace("※", "").strip(), "brand": "",
                   "model": "", "qty": None, "unit_price": None}
            continue
        if cur is None:
            continue
        if key.startswith("品牌"):
            cur["brand"] = val
        elif key == "规格型号":
            mm = MODEL_RE.search(val)
            cur["model"] = mm.group(0).upper().replace(" ", "-") if mm else val[:40]
        elif key == "数量":
            nm = re.search(r"\d+(?:\.\d+)?", val)
            cur["qty"] = float(nm.group(0)) if nm else None
        elif key.startswith("单价") and cur["unit_price"] is None:
            nm = re.search(r"\d[\d,]*(?:\.\d+)?", val)
            cur["unit_price"] = float(nm.group(0).replace(",", "")) if nm else None
    if cur and (cur["model"] or DAHUA_RE.search(cur["brand"])):
        items.append(cur)
    return [
        {**it, "category": item_category(it["item_name"], it["model"]), "raw_line": it["item_name"]}
        for it in items
        if DAHUA_RE.search(it["brand"]) or (it["model"] or "").startswith(("DH-", "ICC"))
    ]


def parse_detail(url: str, title: str, pub: str, html: str) -> dict:
    lines = html_to_lines(html)
    blob = "\n".join(lines)

    def field(pattern: str) -> str:
        m = re.search(pattern, blob)
        return clean_cell(m.group(1)) if m else ""

    tid_m = re.search(r"(t\d{8}_\d+)", url)

    def any_field(*patterns: str) -> str:
        for p in patterns:
            v = field(p)
            if v and v not in ("名称", "代表）"):
                return v
        return ""

    # 中标供应商：先定位"供应商名称"表头，再取紧随其后以序号开头的行
    winner = ""
    for i, line in enumerate(lines):
        if "供应商名称" in line:
            for nxt in lines[i + 1:i + 3]:
                cells = [clean_cell(c) for c in nxt.split("|")]
                if len(cells) >= 3 and cells[0].isdigit():
                    winner = cells[1]
                    break
            if winner:
                break

    tender = {
        "tender_id": tid_m.group(1) if tid_m else url,
        "title": title or "",
        "project_no": field(r"项目编号[:：]\s*([A-Za-z0-9\-\[\]（）() ]{4,40})"),
        "purchaser": any_field(
            r"采购人信息\s*名\s*称[:：]\s*([^\s|地]{2,40})",
            r"采购人名称[:：]\s*([^\s|]{2,40})",
            r"(?:采购人|采购单位)\|\s*([^|]{2,40})",
        ),
        "agency": any_field(
            r"采购代理机构信息\s*名\s*称[:：]\s*([^\s|地]{2,40})",
            r"代理机构名称\|\s*([^|]{2,60})",
            r"(?:代理机构|采购代理机构)[:：]\s*([^\s|]{2,60})",
        ),
        "winner": winner,
        "region": pick_region(blob[:4000]),
        "industry": pick_industry(title or blob[:800]),
        "publish_date": pub,
        "source_url": url,
        "items": [],
    }
    # 金额：优先"总中标金额|￥364.000000 万元"，其次"中标（成交）金额"
    amount, unit = None, ""
    amt_m = re.search(r"总(?:中标|成交)金额\|\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)\s*(万元|元)", blob)
    if not amt_m:
        amt_m = re.search(r"(?:中标|成交)(?:金额|总价)[^0-9]{0,12}[¥￥]?\s*([\d,]+(?:\.\d+)?)\s*(万元|元)?", blob)
    if amt_m:
        amount = float(amt_m.group(1).replace(",", ""))
        unit = amt_m.group(2) or ""
        if unit == "万元":
            amount *= 10000
    tender["amount"] = amount

    for line in lines:
        if line.count("|") < 4:
            continue
        cells = [clean_cell(c) for c in line.split("|")]
        brand_idx = next((i for i, c in enumerate(cells) if DAHUA_RE.search(c)), None)
        model_idx = next((i for i, c in enumerate(cells) if MODEL_RE.search(c) and not DAHUA_RE.search(c)), None)
        if brand_idx is None and model_idx is None:
            continue
        if brand_idx is None:
            continue  # 只保留明确写了大华的品牌行
        model = ""
        if model_idx is not None:
            m = MODEL_RE.search(cells[model_idx])
            model = m.group(0).upper().replace(" ", "-") if m else ""
        nums = [c for c in cells[max(brand_idx, model_idx or brand_idx) + 1:] if NUM_RE.match(c)]
        qty = float(nums[0].lstrip("¥￥")) if len(nums) >= 2 else None
        price = float(nums[1].lstrip("¥￥")) if len(nums) >= 2 else (
            float(nums[0].lstrip("¥￥")) if nums else None)
        name_idx = brand_idx - 1 if brand_idx > 0 else 0
        name = cells[name_idx].replace("※", "").strip()
        if name.isdigit() and name_idx > 0:
            name = cells[name_idx - 1]
        tender["items"].append({
            "item_name": name,
            "brand": cells[brand_idx],
            "model": model,
            "qty": qty,
            "unit_price": price,
            "category": item_category(name, model),
            "raw_line": line[:400],
        })

    # 追加"货物类"逐行块格式，按（型号, 单价）去重
    seen_keys = {(it["model"], it["unit_price"]) for it in tender["items"]}
    for it in parse_block_items(lines):
        key = (it["model"], it["unit_price"])
        if key not in seen_keys:
            seen_keys.add(key)
            tender["items"].append(it)
    return tender


def write_db(tenders: list[dict]) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS tenders (
        tender_id TEXT PRIMARY KEY, source TEXT, region TEXT, industry TEXT, title TEXT,
        project_no TEXT, purchaser TEXT, agency TEXT, winner TEXT, publish_date TEXT,
        amount REAL, source_url TEXT, has_dahua INTEGER, items_count INTEGER,
        collected_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS tender_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tender_id TEXT, seq INTEGER, item_name TEXT,
        brand TEXT, model TEXT, qty REAL, unit_price REAL, category TEXT, raw_line TEXT,
        collected_at TEXT)""")
    today = datetime.now().isoformat(timespec="seconds")
    for t in tenders:
        con.execute(
            "INSERT OR REPLACE INTO tenders (tender_id,source,region,industry,title,project_no,"
            "purchaser,agency,winner,publish_date,amount,source_url,has_dahua,items_count,collected_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t["tender_id"], t.get("source", ""), t["region"], t["industry"], t["title"],
             t["project_no"], t["purchaser"], t["agency"], t["winner"], t["publish_date"],
             t["amount"], t["source_url"], 1, len(t["items"]), today),
        )
        con.execute("DELETE FROM tender_items WHERE tender_id=?", (t["tender_id"],))
        con.executemany(
            "INSERT INTO tender_items (tender_id,seq,item_name,brand,model,qty,unit_price,category,"
            "raw_line,collected_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(t["tender_id"], i, it["item_name"], it["brand"], it["model"], it["qty"],
              it["unit_price"], it["category"], it["raw_line"], today)
             for i, it in enumerate(t["items"], 1)],
        )
    con.commit()
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="采集 ccgp 中标公告中的大华设备清单")
    ap.add_argument("--pages", type=int, default=8, help="每个来源翻多少页列表")
    ap.add_argument("--limit", type=int, default=120, help="最多抓多少个公告详情")
    ap.add_argument("--delay", type=float, default=2.0, help="请求间隔秒")
    ap.add_argument("--sources", default="dfgg,zygg", help="dfgg(地方) / zygg(中央)")
    ap.add_argument("--no-prefilter", action="store_true", help="不按标题粗筛，全部抓详情")
    ap.add_argument("--keyword", default="", help="额外只看含该关键词的明细（不影响入库）")
    ap.add_argument("--urls", default="", help="直接指定公告 URL 文件（每行一个，# 为注释），跳过列表翻页")
    ap.add_argument("--dry-run", action="store_true", help="不写库")
    ap.add_argument("--out", default=str(ROOT / "data" / "tender_awards.json"))
    args = ap.parse_args()

    sess = session()
    candidates: list[tuple[str, str, str, str]] = []

    if args.urls:
        for line in Path(args.urls).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                candidates.append((line, "", "", "seed"))
        log(f"从 {args.urls} 读入 {len(candidates)} 个公告 URL")
    else:
        wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
        listings = [(p, tag) for p, tag in LISTINGS
                    if any(k in p for k in wanted) or any(tag.endswith(k) for k in wanted)]
        seen: set[str] = set()
        for path, tag in listings:
            log(f"== 列表来源 {tag} ==")
            for url, title, pub in listing_items(sess, path, args.pages, args.delay):
                if url in seen:
                    continue
                seen.add(url)
                if not args.no_prefilter and not any(k in title for k in TITLE_KW):
                    continue
                candidates.append((url, title, pub, tag))
                if len(candidates) >= args.limit:
                    break
            if len(candidates) >= args.limit:
                break

    log(f"\n候选公告 {len(candidates)} 个，开始抓详情（间隔 {args.delay}s）")
    hits: list[dict] = []
    for i, (url, title, pub, tag) in enumerate(candidates, 1):
        html = get(sess, url)
        if not html:
            continue
        if not title:
            m = re.search(r"<title>(.*?)</title>", html, re.S)
            title = clean_cell(m.group(1)) if m else ""
        if not pub:
            d = re.search(r"t(\d{4})(\d{2})(\d{2})_", url)
            pub = f"{d.group(1)}-{d.group(2)}-{d.group(3)}" if d else ""
        t = parse_detail(url, title, pub, html)
        t["source"] = tag
        if t["items"]:
            hits.append(t)
            log(f"  [{i}/{len(candidates)}] ✓ {t['title'][:40]} 大华明细 {len(t['items'])} 条")
        else:
            log(f"  [{i}/{len(candidates)}] · 无大华明细")
        time.sleep(args.delay)

    # 汇总
    item_count = sum(len(t["items"]) for t in hits)
    icc = [it for t in hits for it in t["items"] if "ICC" in (it["model"] or "")]
    log(f"\n命中公告 {len(hits)} 个，明细 {item_count} 条，其中 ICC 明细 {len(icc)} 条")
    prices = [(it["model"], it["unit_price"]) for it in icc if it["unit_price"]]
    for model, price in sorted(set(prices))[:15]:
        log(f"   ICC 价格样本：{model}  ¥{price:,.0f}")

    if args.dry_run:
        log("（--dry-run，未写库）")
    else:
        write_db(hits)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(hits, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"已写库：tenders {len(hits)} 条 / tender_items {item_count} 条；快照 {out}")


if __name__ == "__main__":
    main()
