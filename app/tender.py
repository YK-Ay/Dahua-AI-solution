"""招标文件导入：PDF / Word / 文本 → 结构化读标结果。

只做「能可靠抽出来的」：
  - 项目硬信息（编号 / 预算 / 截止与开标时间 / 工期 / 地点）
  - ★ 参数清单（含是否要求证明材料）
  - 资格要求、无效投标条款、投标文件格式清单（按章节切段）
  - 商务硬指标（质保 / 响应时限 / 工期 / 付款比例）
  - 技术条款列表（喂给 deviations.evaluate 做偏离表）

抽不准的一律留原文并给 warnings，不猜、不补。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

# ---------------------------------------------------------------- 读取文件

_TEXT_SUFFIX = {".txt", ".md"}


class TenderError(ValueError):
    """文件读不出来 / 不是招标文件。"""


def read_text(filename: str, data: bytes) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(data)
    if suffix == ".docx":
        return _read_docx(data)
    if suffix in _TEXT_SUFFIX:
        return data.decode("utf-8", errors="ignore")
    raise TenderError(f"暂不支持 {suffix or '该'} 格式，请上传 PDF / DOCX / TXT")


def _read_pdf(data: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - 依赖缺失环境
        raise TenderError("缺少 PyMuPDF，请先安装 pymupdf") from exc
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 —— 用户文件，统一转成可读错误
        raise TenderError(f"PDF 打不开：{type(exc).__name__}") from exc
    try:
        if doc.needs_pass:
            raise TenderError("PDF 有打开密码，请先解除加密后再上传")
        parts = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    return "\n".join(parts)


def _read_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise TenderError("缺少 python-docx，请先安装") from exc
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise TenderError(f"Word 打不开：{type(exc).__name__}") from exc
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" ".join(cells))
    return "\n".join(parts)


# ---------------------------------------------------------------- 文本清洗

_BOILER = (
    re.compile(r"^=+\s*第\s*\d+\s*页\s*=+$"),
    re.compile(r"^[-—–]\s*\d+\s*[-—–]$"),
    re.compile(r"^郑州市公共资源交易中心$"),
    re.compile(r"^政府采购招标文件$"),
    re.compile(r"^第[一二三四五六七八九十]+部分$"),
)
_TOC = re.compile(r"(\.{6,}|·{6,}|…{3,}|．{6,})")


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"\r?\n", text or ""):
        line = raw.replace("\u3000", " ").strip()
        if not line or _TOC.search(line):
            continue
        if any(p.match(line) for p in _BOILER):
            continue
        out.append(line)
    return out


def _flat(text: str) -> str:
    """去掉所有空白：PDF 会把一句话折成多行，硬信息只在整串里才连得上。"""
    return re.sub(r"[\s\u3000]+", "", text or "")


def _clip(value: str, limit: int = 200) -> str:
    value = (value or "").strip(" ：:，,。；;、")
    return value[:limit]


# ---------------------------------------------------------------- 章节切片

_CHAPTER_HEAD = re.compile(r"^第[一二三四五六七八九十]+章")


def _slice(lines: list[str], start: str, stops: tuple[str, ...],
           occurrence: int = 1) -> list[str]:
    """按关键词起止切段；occurrence 用于跳过目录里的同名条目。"""
    hit = 0
    begin = -1
    for i, line in enumerate(lines):
        if start in line:
            hit += 1
            if hit == occurrence:
                begin = i
                break
    if begin < 0:
        return []
    end = len(lines)
    for j in range(begin + 1, len(lines)):
        if any(s in lines[j] for s in stops):
            end = j
            break
    return lines[begin + 1:end]


def _items(lines: list[str]) -> list[str]:
    """把一段文本按「一、二、」「（一）」「1.」拆成条目，短句直接丢弃。"""
    joined = "".join(lines)
    parts = re.split(r"(?=(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\d{1,2}[、．.](?!\d)))",
                     joined)
    out: list[str] = []
    for part in parts:
        body = part.strip(" 　;；,，。")
        body = re.sub(r"^[一二三四五六七八九十]+、|^（[一二三四五六七八九十]+）|^\d{1,2}[、．.]", "", body)
        body = body.strip()
        if len(body) >= 3:
            out.append(body)
    return out


# ---------------------------------------------------------------- 硬信息

def _project(text: str) -> dict:
    flat = _flat(text)
    info: dict = {}

    m = re.search(r"就(.{4,80}?)（?项目编号", flat)
    if m:
        info["name"] = _clip(m.group(1), 80)
    if not info.get("name"):
        m = re.search(r"项目名称[：:]?([^\s，。；]{4,80})", flat)
        if m:
            info["name"] = _clip(m.group(1), 80)

    m = re.search(r"项目编号[：:]?([A-Za-z0-9\-—_/]{3,40})", flat)
    if not m:
        m = re.search(r"项目编号[：:]?([\u4e00-\u9fff0-9\-]{4,30}?)[）（),，。；]", flat)
    if m:
        info["code"] = _clip(m.group(1), 40)

    m = re.search(r"受([\u4e00-\u9fff（）()]{4,30}?)委托", flat)
    if m:
        info["buyer"] = _clip(m.group(1), 30)
    m = re.search(r"采购代理机构(?:信息)?[：:]?(?:名\s*称[：:]?)?([\u4e00-\u9fff（）()]{4,30}?)(?:地\s*址|联系|电\s*话|邮\s*编)", flat)
    if not m:
        m = re.search(r"(?:采购代理机构|集中采购机构|代理机构)[：:]?(?:名\s*称[：:]?)?"
                      r"([\u4e00-\u9fff]{4,30}?(?:中心|公司|研究院|事务所))", flat)
    if m:
        info["agency"] = _clip(m.group(1), 30)

    m = re.search(r"(?:采购预算|预算金额|最高限价|采购预算或最高限价)[^0-9]{0,16}([\d,]+(?:\.\d+)?)\s*万元", flat)
    if not m:
        # PDF 表格转文本会把「最高限价（万元）」和数值拆到不同位置，退一步找最近的带小数金额
        head = re.search(r"(?:采购预算|预算金额|最高限价)", flat)
        if head:
            m = re.search(r"(\d{2,9}\.\d{1,4})", flat[head.start(): head.start() + 160])
    if m:
        info["budget"] = _clip(m.group(1), 20).replace(",", "") + " 万元"

    m = re.search(r"开标时间[：:]?(20\d{2})年(\d{1,2})月(\d{1,2})日(\d{1,2})时(\d{1,2})分", flat)
    if m:
        y, mo, d, h, mi = m.groups()
        info["open_time"] = f"{y}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{int(mi):02d}"

    m = re.search(r"投标截止时间[：:]?(20\d{2})年(\d{1,2})月(\d{1,2})日(\d{1,2})时(\d{1,2})分", flat)
    if m:
        y, mo, d, h, mi = m.groups()
        info["deadline"] = f"{y}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{int(mi):02d}"
    elif info.get("open_time"):
        info["deadline"] = f"{info['open_time']}（开标时间即截止时间）"

    m = re.search(r"签订合同后\s*(\d{1,3})\s*(?:个)?(?:日历)?天", flat)
    if m:
        info["duration"] = f"合同签订后 {m.group(1)} 日历天"

    m = re.search(r"交货地点[：:]?([\u4e00-\u9fff]{2,12}(?:号|路|街|市|区|县|院|校|楼|室))", flat)
    if m:
        info["site"] = _clip(m.group(1), 30)
    return {k: v for k, v in info.items() if v}


_SCORING_LABEL = re.compile(r"^(.{2,20}?)\s*[（(]\s*(\d{1,2})\s*%\s*[）)]$")
_WEIGHT_ONLY = re.compile(r"^[（(]\s*(\d{1,2})\s*%\s*[）)]$")
_PURE_NUM = re.compile(r"^\d{1,3}(?:\.\d{1,2})?$")
_HEADING = re.compile(r"^(?:第[一二三四五六七八九十]+[章节]|[一二三四五六七八九十]+、)")
_SKIP_LABEL = {"说明", "备注", "序号", "评分因素及权重", "评分因素", "分值", "评分标准", "权重"}


def _scoring(lines: list[str]) -> list[dict]:
    """粗抽评分表：因素（权重）→ 分值 → 评分标准原文。

    PDF 会把「投标报价」和「（35%）」拆成两行，分值也常单独成行，这里三种形态都接。
    """
    start = -1
    for i, line in enumerate(lines):
        if "评分标准" in line or "评标标准" in line:
            start = i
            break
    if start < 0:
        return []

    out: list[dict] = []
    current: dict | None = None
    pending: list[str] = []
    for line in lines[start + 1: start + 200]:
        if "无效投标条款" in line:
            break
        inline = _SCORING_LABEL.match(line)
        only = _WEIGHT_ONLY.match(line)
        if inline:
            current = {"label": inline.group(1).strip(), "weight": inline.group(2) + "%",
                       "score": "", "criterion": ""}
            out.append(current)
            pending.clear()
            continue
        if only:
            # 「商务」「部分」被表格拆成两行的，拼回去
            label = "".join(pending[-2:]).lstrip("说明备注") if pending else ""
            if label:
                current = {"label": label, "weight": only.group(1) + "%",
                           "score": "", "criterion": ""}
                out.append(current)
            pending.clear()
            continue
        if 2 <= len(line) <= 12 and not re.search(r"\d", line) and not _HEADING.match(line):
            if line not in _SKIP_LABEL:
                pending.append(line)
                if len(pending) > 3:
                    pending.pop(0)
            continue
        if current is None:
            continue
        if not current["score"] and _PURE_NUM.match(line):
            current["score"] = line
            continue
        if len(line) >= 4 and not _PURE_NUM.match(line):
            current["criterion"] = (current["criterion"] + line)[:300]
    return [x for x in out if x["label"]]


_COMMERCIAL = (
    ("质保", re.compile(r"质保期(?:为|限|：|:)?\s*(\d+)\s*年")),
    ("质保", re.compile(r"免费质保\s*(\d+)\s*年")),
    ("响应", re.compile(r"(\d+)\s*小时内(?:到达|响应|赶到|到现场)")),
    ("工期", re.compile(r"签订合同后\s*(\d+)\s*(?:个)?(?:日历)?天")),
    ("付款", re.compile(r"验收合格[^。；]{0,20}?付(?:款)?\s*(\d{1,3})\s*%")),
    ("投标有效期", re.compile(r"投标有效期(?:为)?\s*(\d{1,3})\s*(?:个)?(?:日历)?天")),
)


def _commercial(flat: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for label, pattern in _COMMERCIAL:
        for m in pattern.finditer(flat):
            key = f"{label}:{m.group(1)}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"label": label, "value": m.group(1),
                        "text": flat[max(0, m.start() - 12): m.end() + 12]})
    return out


# ---------------------------------------------------------------- 参数与 ★

_PARAM_START = re.compile(r"具体参数要求|技术参数要求|技术要求")
_PARAM_STOP = re.compile(r"^(?:第[一二三四五六七八九十]+章|商务要求|落实政府采购政策|投标文件格式)")
_ITEM_SPLIT = re.compile(r"\d{1,3}\s*[、．]|\d{1,3}\.(?!\d)")
_PROOF_WORDS = ("证明", "检测报告", "彩页", "截图", "软著", "著作权", "授权", "证书", "检验")


def _param_block(lines: list[str]) -> str:
    begin = -1
    for i, line in enumerate(lines):
        if _PARAM_START.search(line) and len(line) <= 24:
            begin = i
            break
    if begin < 0:
        return ""
    end = len(lines)
    for j in range(begin + 1, len(lines)):
        if _PARAM_STOP.search(lines[j]):
            end = j
            break
    return "".join(lines[begin + 1: end])


def _split_items(block: str) -> list[str]:
    """按「1、」「2.」切条。★ 常紧贴序号，切完把落到上一条末尾的 ★ 挪回本条。"""
    if not block:
        return []
    starts = [m.start() for m in _ITEM_SPLIT.finditer(block)]
    if not starts:
        return []
    # finditer 已消费完整数字，这里再滤掉紧挨着的重复起点（如「10、」被拆成 1 / 0）
    bounds = [starts[0]] + [s for prev, s in zip(starts, starts[1:]) if s - prev > 1]
    chunks = [block[a:b] for a, b in zip(bounds, bounds[1:] + [len(block)])]

    out: list[str] = []
    carry = ""
    for chunk in chunks:
        text = carry + chunk
        carry = ""
        head = text.rstrip()
        if head.endswith("★"):
            carry = "★"
            text = head[:-1]
        body = text.strip()
        star = body.startswith("★")
        if star:
            body = body[1:].lstrip()
        body = re.sub(r"^\d{1,3}\s*[、．]\s*|^\d{1,3}\.\s*", "", body).strip(" 　;；,，。")
        if len(body) < 8:
            continue
        if re.search(r"[\u4e00-\u9fff]", body) and not body.startswith("序号"):
            out.append(("★" if star else "") + body[:500])
    return out


def _stars(items: list[str]) -> list[dict]:
    stars: list[dict] = []
    for body in items:
        if "★" not in body:
            continue
        text = body.replace("★", "").strip()
        stars.append({
            "no": len(stars) + 1,
            "text": text,
            "need_proof": any(w in text for w in _PROOF_WORDS),
        })
    return stars


def _format_list(lines: list[str]) -> list[str]:
    """投标文件格式清单：取最后一处「投标文件格式」短标题，往后到附件为止。"""
    begin = -1
    for i, line in enumerate(lines):
        if "投标文件格式" in line and len(line) <= 24:
            begin = i
    if begin < 0:
        return []
    body: list[str] = []
    for line in lines[begin + 1: begin + 120]:
        if line.startswith("附件") or re.match(r"^第[一二三四五六七八九十]+[章部分]", line):
            break
        body.append(line)
    return _items(body)[:40]


# ---------------------------------------------------------------- 主入口

def analyze(text: str) -> dict:
    """招标文件正文 → 读标结果。所有字段都可能为空，前端按存在与否渲染。"""
    lines = _lines(text)
    warnings: list[str] = []
    if len(lines) < 40:
        warnings.append("正文太短，可能不是完整的招标文件（或 PDF 为扫描件、无文字层）")

    project = _project(text)
    for key in ("name", "code", "budget"):
        if key not in project:
            warnings.append(f"未识别到{'项目名称' if key == 'name' else '项目编号' if key == 'code' else '预算/最高限价'}")

    qualification = [x for x in _items(_slice(lines, "投标人资格要求", ("第四章", "四、", "五、", "获取招标文件")))
                     if "以下资格条件" not in x and "资格要求" not in x]
    invalid = [x for x in _items(_slice(lines, "无效投标条款", ("第六章", "第七", "投标文件格式")))
               if not x.startswith("无效投标条款")]
    formats = _format_list(lines)

    scoring = _scoring(lines)
    if scoring:
        warnings.append("评分表由表格转文本，因素与分值的对应关系需人工核对一遍")
    else:
        warnings.append("未定位到评分标准表，请人工翻阅「评标办法」章节")
    if not formats:
        warnings.append("未定位到投标文件格式清单，请人工确认投标文件目录要求")

    block = _param_block(lines)
    items = _split_items(block)
    stars = _stars(items)
    if not block:
        warnings.append("未定位到「具体参数要求」章节，技术条款可能不完整")
    elif not stars:
        warnings.append("参数章节里没有找到 ★ 标记，请确认该文件是否使用其他符号（▲ / ※）")

    clauses = [{
        "index": i,
        "text": body,
        "weight": "",
        "star": "★" in body,
    } for i, body in enumerate(items, 1)]

    return {
        "project": project,
        "qualification": qualification,
        "invalid": invalid,
        "formats": formats,
        "scoring": scoring,
        "stars": stars,
        "commercial": _commercial(_flat(text)),
        "clauses": clauses,
        "warnings": warnings,
        "stats": {
            "chars": len(text),
            "lines": len(lines),
            "clauses": len(clauses),
            "stars": len(stars),
        },
    }
