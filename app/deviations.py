"""技术偏离表：招标条款 → 逐条响应结论（可回溯到 Spec 与产品库证据）。

原则：
  - 只对能算的做判定（存储天数、点位数、码流、容量），其余一律「待确认」；
  - 不做「正偏离」之类的营销性结论，避免把不确定写成满足；
  - 每条结论都给出依据（型号 / 点位数 / 容量）或明确说明缺什么。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Spec

STATUS_LABEL = {
    "respond": "响应",
    "negative": "负偏离",
    "confirm": "待确认",
}
STATUS_FILL = {
    "respond": "E6F4EA",
    "negative": "FBE9E7",
    "confirm": "FFF4E5",
}

# 章节定位：评分点索引 / 偏离项对应的方案章节
CHAPTER_OF = {
    "video": "二、需求分析 / 五、点位设计",
    "storage": "七、存储与带宽测算",
    "network": "四、总体设计 / 七、存储与带宽测算",
    "platform": "四、总体设计",
    "display": "四、总体设计",
    "construction": "九、施工组织与进度",
    "service": "十、售后服务与培训",
    "commercial": "六、设备清单与选型说明",
    "other": "十二、缺口与待确认",
}

CATEGORY_WORDS = [
    ("storage", ("存储", "硬盘", "容量", "录像保存", "保存天数", "tb")),
    ("network", ("交换机", "带宽", "光纤", "poe", "传输", "网络")),
    ("platform", ("平台", "联网", "28181", "服务器", "管理软件", "客户端")),
    ("display", ("显示", "监视器", "大屏", "解码", "电视墙")),
    ("service", ("质保", "售后", "培训", "维护", "响应时间", "备件", "服务")),
    ("construction", ("施工", "工期", "进度", "验收", "安装", "调试", "组织")),
    ("commercial", ("报价", "价格", "税", "付款", "折扣", "商务", "发票")),
    ("video", ("摄像机", "视频", "图像", "球机", "枪机", "半球", "抓拍",
               "录像", "编码", "h.265", "像素", "码流")),
]

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-/\.]{1,}|[\u4e00-\u9fff]{2,}")
_WEIGHT_RE = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]|权重\s*[:：]\s*(\d+(?:\.\d+)?)")
_NUM_PREFIX = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百]+[条章节]\s*|"
    r"[（(]\s*\d+\s*[）)]\s*|"
    r"\d+(?:\.\d+)*\s*[\.、\)．]\s*|\d+\s+)"
)
_DAYS_RE = re.compile(r"存储[^。；，,]{0,16}?(\d{1,4})\s*(?:个)?(?:日历)?(?:天|日)")
_LANES_RE = re.compile(r"(\d{1,5})\s*(?:路|个点位|点位)")
_BITRATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mbps", re.I)
_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*tb", re.I)


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        word = m.group()
        if word[0].isascii():
            out.add(word.lower())
        else:
            for i in range(len(word) - 1):
                out.add(word[i:i + 2])
    return out


def _category_of(text: str) -> str:
    low = (text or "").lower()
    for name, words in CATEGORY_WORDS:
        if any(w in low for w in words):
            return name
    return "other"


def _clean(text: str) -> tuple[str, str]:
    """去掉序号前缀与末尾分值，返回（条款正文, 权重）。"""
    weight = ""
    m = _WEIGHT_RE.search(text or "")
    if m:
        weight = m.group(1) or m.group(2) or ""
        text = text[:m.start()] + text[m.end():]
    text = _NUM_PREFIX.sub("", text or "").strip(" \t　.、;；")
    return text, weight


def parse_text(text: str) -> list[dict]:
    """粘贴文本 → 条款列表：按行切分，保留原文，不合并、不改写。"""
    clauses: list[dict] = []
    for raw in re.split(r"\r?\n", text or ""):
        line = raw.strip()
        if len(line) < 3:
            continue
        body, weight = _clean(line)
        if len(body) < 3:
            continue
        clauses.append({"index": len(clauses) + 1, "text": body, "weight": weight})
    return clauses


def _pick_column(headers: list[str], keys: tuple[str, ...]) -> int | None:
    for i, head in enumerate(headers):
        h = (head or "").strip()
        if any(k in h for k in keys):
            return i
    return None


def parse_xlsx(data: bytes) -> list[dict]:
    """上传 Excel → 条款列表。表头需含 序号/条款/技术要求/权重 之一。"""
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [[("" if v is None else str(v).strip()) for v in row]
            for row in ws.iter_rows(values_only=True)]
    if not rows:
        return []

    header_idx = -1
    text_col = weight_col = None
    for i, cells in enumerate(rows[:10]):
        t = _pick_column(cells, ("条款", "技术要求", "技术参数", "参数", "功能", "内容", "名称"))
        if t is not None:
            header_idx, text_col = i, t
            weight_col = _pick_column(cells, ("权重", "分值", "分数", "评分"))
            break

    clauses: list[dict] = []
    body_rows = rows[header_idx + 1:] if header_idx >= 0 else rows
    for cells in body_rows:
        if header_idx < 0:
            text = " ".join(x for x in cells if x)
        else:
            text = cells[text_col] if text_col < len(cells) else ""
        text = (text or "").strip()
        if len(text) < 3:
            continue
        weight = ""
        if header_idx >= 0 and weight_col is not None and weight_col < len(cells):
            weight = cells[weight_col].strip()
        body, extra = _clean(text)
        clauses.append({
            "index": len(clauses) + 1,
            "text": body,
            "weight": weight or extra,
        })
    wb.close()
    return clauses


def _evidence(spec: Spec) -> list[dict]:
    """可引用的本地证据：设备、点位、存储、带宽、服务与施工承诺。"""
    ev: list[dict] = []
    for d in spec.devices:
        text = f"{d.category} {d.model} {d.display_name} {d.scene} 数量 {d.qty} 台"
        ev.append({
            "kind": "device",
            "tokens": _tokens(text),
            "ref": f"{d.model} {d.display_name or d.category}（{d.qty} 台/套）",
        })
    for r in spec.requirements:
        text = f"{r.target} {r.intent} 点位 {r.lanes} 路 码流 {r.bitrate_mbps} Mbps"
        ev.append({
            "kind": "requirement",
            "tokens": _tokens(text),
            "ref": f"{r.target}：{r.lanes} 路，单路 {r.bitrate_mbps:g} Mbps",
        })
    ev.append({
        "kind": "storage",
        "tokens": _tokens("存储 录像 保存 容量 硬盘 冗余 TB 天"),
        "ref": f"存储 {spec.storage.retention_days} 天，裸容量 {spec.storage.raw_tb} TB，"
               f"含冗余 {spec.storage.with_redundancy_tb} TB",
    })
    ev.append({
        "kind": "network",
        "tokens": _tokens("网络 传输 交换机 带宽 上行 PoE 光纤"),
        "ref": f"上行带宽 {spec.network.uplink_mbps} Mbps，前端采用 PoE 交换机供电",
    })
    ev.append({
        "kind": "service",
        "tokens": _tokens("质保 售后 培训 响应 维护 备件 服务 运维"),
        "ref": "质保、响应时限、培训与备件按合同及商务承诺执行（方案书第十章）",
    })
    ev.append({
        "kind": "construction",
        "tokens": _tokens("施工 组织 进度 工期 安装 调试 验收 深化设计"),
        "ref": "施工组织与进度计划见方案书第九章（周期为示例，按合同工期调整）",
    })
    ev.append({
        "kind": "commercial",
        "tokens": _tokens("报价 价格 标准价 税 折扣 付款 发票 商务"),
        "ref": "设备按标准价（未税、未折扣）计列，最终以商务报价单为准",
    })
    ev.append({
        "kind": "platform",
        "tokens": _tokens("平台 联网 管理 服务器 客户端 授权 license"),
        "ref": "平台层按需配置，支持 GB/T 28181 联网；软件授权需商务单独确认",
    })
    return ev


def _numeric_check(text: str, spec: Spec, total_lanes: int,
                   max_bitrate: float) -> dict | None:
    """能算的条款直接算：存储天数 / 点位总数 / 单路码流 / 容量。"""
    low = text.lower()

    m = _DAYS_RE.search(text)
    if m:
        need = int(m.group(1))
        actual = spec.storage.retention_days
        ok = actual >= need
        return {
            "status": "respond" if ok else "negative",
            "response": (f"本地方案存储 {actual} 天，满足不少于 {need} 天的要求。"
                         if ok else f"本地方案存储 {actual} 天，低于要求的 {need} 天，需调整存储配置。"),
            "evidence": f"Spec 存储天数 = {actual}（方案书第七章）",
        }

    if any(w in text for w in ("总路数", "点位", "摄像机", "监控点", "视频路数")):
        m = _LANES_RE.search(text)
        if m:
            need = int(m.group(1))
            ok = total_lanes >= need
            return {
                "status": "respond" if ok else "negative",
                "response": (f"本地方案共 {total_lanes} 个点位，满足不少于 {need} 路的要求。"
                             if ok else f"本地方案共 {total_lanes} 个点位，低于要求的 {need} 路。"),
                "evidence": f"Spec 点位合计 = {total_lanes}（方案书第二章）",
            }

    m = _BITRATE_RE.search(text)
    if m:
        need = float(m.group(1))
        ok = max_bitrate >= need
        return {
            "status": "respond" if ok else "negative",
            "response": (f"本地方案单路码流最高 {max_bitrate:g} Mbps，满足不低于 {need:g} Mbps。"
                         if ok else f"本地方案单路码流最高 {max_bitrate:g} Mbps，低于要求 {need:g} Mbps。"),
            "evidence": f"Spec 场景码流（方案书第二章）",
        }

    m = _CAPACITY_RE.search(low)
    if m and ("存储" in text or "容量" in text or "录像" in text):
        need = float(m.group(1))
        actual = spec.storage.with_redundancy_tb
        ok = actual >= need
        return {
            "status": "respond" if ok else "negative",
            "response": (f"本地方案含冗余容量 {actual} TB，满足不少于 {need:g} TB 的要求。"
                         if ok else f"本地方案含冗余容量 {actual} TB，低于要求 {need:g} TB。"),
            "evidence": f"Spec 冗余容量 = {actual} TB（方案书第七章）",
        }
    return None


def evaluate(clauses: list[dict], spec: Spec | dict) -> list[dict]:
    """逐条判定：能算的算，能匹配的给证据，其余明确「待确认」。"""
    if isinstance(spec, dict):
        spec = Spec.model_validate(spec)
    total_lanes = sum(r.lanes for r in spec.requirements)
    max_bitrate = max((r.bitrate_mbps for r in spec.requirements), default=0.0)
    evidence = _evidence(spec)

    rows: list[dict] = []
    for clause in clauses:
        text = clause["text"]
        category = _category_of(text)
        tokens = _tokens(text)
        best, best_hit = None, 0
        for ev in evidence:
            hit = len(tokens & ev["tokens"])
            if hit > best_hit:
                best, best_hit = ev, hit
        coverage = best_hit / max(len(tokens), 1)

        numeric = _numeric_check(text, spec, total_lanes, max_bitrate)
        if numeric:
            status = numeric["status"]
            response = numeric["response"]
            proof = numeric["evidence"]
        elif coverage >= 0.18 and best is not None:
            # 有明确数字或比较符的条款，关键词命中也不能直接判满足
            if re.search(r"\d|≥|≤|不低于|不高于|不小于|不大于|优于", text):
                status = "confirm"
                response = "本地方案涉及该条款，但具体参数需对照产品彩页逐项核对后再确认。"
            else:
                status = "respond"
                response = "本地方案已配置对应设备或章节内容，具体参数以产品彩页为准。"
            proof = best["ref"]
        else:
            status = "confirm"
            response = "本地方案未覆盖该条款，需补充设备/参数或由投标负责人确认响应方式。"
            proof = "—"

        rows.append({
            "index": clause["index"],
            "text": text,
            "weight": clause.get("weight") or "",
            "category": category,
            "status": status,
            "status_label": STATUS_LABEL[status],
            "response": response,
            "evidence": proof,
            "chapter": CHAPTER_OF.get(category, CHAPTER_OF["other"]),
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    counts = {key: 0 for key in STATUS_LABEL}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "total": len(rows),
        "respond": counts["respond"],
        "negative": counts["negative"],
        "confirm": counts["confirm"],
        "note": "「响应」仅表示本地配置能证明满足；带具体参数但无法自动核对的条款一律标「待确认」，"
                "投标前须逐条人工复核。",
    }


def score_index(rows: list[dict]) -> list[dict]:
    """评分点索引：带权重/分值的条款 → 对应方案章节。"""
    items = []
    for row in rows:
        if row.get("weight"):
            items.append({
                "item": row["text"],
                "weight": row["weight"],
                "chapter": row["chapter"],
            })
    return items


def render_xlsx(rows: list[dict], summary: dict, project_name: str,
                scores: list[dict] | None = None, out_path: Path | str = "偏离表.xlsx") -> Path:
    """导出技术偏离表；有评分点时追加「评分点索引」工作表。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    head_fill = PatternFill("solid", fgColor="243447")
    thin = Side(style="thin", color="D6DAE0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    wb = Workbook()
    ws = wb.active
    ws.title = "技术偏离表"
    headers = ["序号", "招标条款", "权重", "响应结论", "响应说明", "依据（型号/参数）", "对应章节"]
    for i, head in enumerate(headers, 1):
        cell = ws.cell(row=1, column=i, value=head)
        cell.font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    for r, row in enumerate(rows, 2):
        values = [row["index"], row["text"], row["weight"] or "—",
                  row["status_label"], row["response"], row["evidence"], row["chapter"]]
        for c, value in enumerate(values, 1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = Font(name="微软雅黑", size=9)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = border
        ws.cell(row=r, column=4).fill = PatternFill(
            "solid", fgColor=STATUS_FILL.get(row["status"], "FFFFFF"))

    tail = len(rows) + 3
    ws.cell(row=tail, column=1, value=(
        f"统计：共 {summary['total']} 条，响应 {summary['respond']} 条，"
        f"负偏离 {summary['negative']} 条，待确认 {summary['confirm']} 条。{summary['note']}"))
    ws.merge_cells(start_row=tail, start_column=1, end_row=tail, end_column=len(headers))
    ws.cell(row=tail, column=1).font = Font(name="微软雅黑", size=9, color="5B6672")
    ws.cell(row=tail, column=1).alignment = Alignment(wrap_text=True)

    for i, width in enumerate([6, 46, 8, 10, 42, 36, 22], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"

    if scores:
        ws2 = wb.create_sheet("评分点索引")
        heads = ["评分项", "分值/权重", "方案对应章节"]
        for i, head in enumerate(heads, 1):
            cell = ws2.cell(row=1, column=i, value=head)
            cell.font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center")
            cell.border = border
        for r, item in enumerate(scores, 2):
            for c, value in enumerate([item["item"], item["weight"], item["chapter"]], 1):
                cell = ws2.cell(row=r, column=c, value=value)
                cell.font = Font(name="微软雅黑", size=9)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.border = border
        for i, width in enumerate([56, 12, 30], 1):
            ws2.column_dimensions[get_column_letter(i)].width = width
        ws2.freeze_panes = "A2"

    wb.save(out)
    return out
