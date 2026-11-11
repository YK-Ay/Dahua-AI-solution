"""拓扑图渲染器：Spec → PNG（大华红白风 · 产品图标 + 框架框）。

两种模式：
  - mode="simple"  简略分层版（前端 → 传输 → 存储 → 平台 → 显示），
                    摄像机节点标注「使用地点 + 建设目标」，按场景分组。
  - mode="detailed" 超详细逻辑连线版：场景摄像机 → PoE 交换机 → NVR → 硬盘
                    → 综合平台 → 监视器 的真实互连，展示整个项目所有配单如何逐个相连。

与前端交互式 SVG（web/static/topology.js）保持视觉一致（均为红白风）。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import Spec

MARGIN_X = 56
LABEL_W = 150          # 左侧层标题区宽度
BAND_GAP = 54          # 层间留白（放连接线）
CARD_H = 130
CARD_GAP = 18
CARD_W = 252           # 单卡固定宽度
MAX_PER_ROW = 4        # 每层最多每行的卡片数
TITLE_FONT = 38
SUB_FONT = 19
HEAD_FONT = 22
NODE_HEAD = 18
NODE_MODEL = 17
NODE_SUB = 14
NODE_LOC = 13          # 使用地点小字
QTY_FONT = 22

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
]

# ===== 大华红白配色 =====
BG = (255, 255, 255)
PANEL = (246, 247, 249)        # 浅灰层背景
PANEL_LINE = (226, 229, 234)
NODE_FILL = (255, 255, 255)
NODE_LINE = (222, 226, 232)
LINK = (230, 0, 18)            # 大华红
LINK_DARK = (163, 10, 20)
INK = (26, 29, 36)             # 主文字
MUTED = (120, 128, 140)        # 次要文字
HEADER_FILL = (230, 0, 18)     # 红色层标题底
HEADER_INK = (255, 255, 255)

# 类目强调色（在白底上仍清晰）
C_CAMERA = (0, 145, 190)
C_SWITCH = (22, 150, 110)
C_RECORDER = (200, 120, 0)
C_DISK = (120, 80, 190)
C_MONITOR = (214, 51, 108)
C_PLATFORM = (230, 0, 18)
C_BOX = (96, 110, 130)

LAYERS = ["前端", "传输", "存储", "平台", "显示"]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path.exists():
            return ImageFont.truetype(str(path), size, index=0)
    return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: float, tail: str = "…") -> str:
    """按像素宽度截断，超长加省略号。"""
    text = str(text or "")
    if _text_w(draw, text, font) <= max_w:
        return text
    cut = text
    while cut and _text_w(draw, cut + tail, font) > max_w:
        cut = cut[:-1]
    return cut + tail


def _centered_at(draw, text, font, cx, cy, fill):
    w = _text_w(draw, text, font)
    draw.text((cx - w / 2, cy - font.size / 2), text, font=font, fill=fill)


def _cat_color(category: str) -> tuple[int, int, int]:
    if category == "网络摄像机":
        return C_CAMERA
    if "交换机" in category or category == "PoE交换机":
        return C_SWITCH
    if category == "NVR" or "录像机" in category:
        return C_RECORDER
    if "硬盘" in category or "存储" in category:
        return C_DISK
    if category == "监视器" or "拼接屏" in category:
        return C_MONITOR
    if "平台" in category or "解码" in category:
        return C_PLATFORM
    return C_BOX


def _icon_type(category: str) -> str:
    if category == "网络摄像机":
        return "camera"
    if "交换机" in category or category == "PoE交换机":
        return "switch"
    if category == "NVR" or "录像机" in category:
        return "recorder"
    if "硬盘" in category or "存储" in category:
        return "disk"
    if category == "监视器" or "拼接屏" in category:
        return "monitor"
    if "平台" in category or "解码" in category:
        return "platform"
    return "box"


def _glyph(draw: ImageDraw.ImageDraw, category: str, box: tuple[float, float, float, float], color) -> None:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w, h = x1 - x0, y1 - y0
    t = _icon_type(category)
    if t == "camera":
        r = min(w, h) * 0.26
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=4)
        draw.ellipse([cx - r * 0.45, cy - r * 0.45, cx + r * 0.45, cy + r * 0.45], fill=color)
        draw.rectangle([cx + r * 0.5, cy - r * 1.1, cx + r * 1.3, cy - r * 0.5], fill=color)
    elif t == "switch":
        for i in range(4):
            px = x0 + w * 0.16 + i * (w * 0.2)
            draw.rectangle([px, cy - h * 0.14, px + w * 0.1, cy + h * 0.14], fill=color)
        draw.ellipse([cx - w * 0.06, cy - h * 0.34, cx + w * 0.06, cy - h * 0.22], fill=color)
    elif t == "recorder":
        draw.rectangle([x0 + w * 0.15, cy - h * 0.28, x1 - w * 0.15, cy - h * 0.06], outline=color, width=3)
        draw.rectangle([x0 + w * 0.15, cy + h * 0.06, x1 - w * 0.15, cy + h * 0.28], outline=color, width=3)
        draw.ellipse([x1 - w * 0.22, cy - h * 0.26, x1 - w * 0.1, cy - h * 0.14], fill=color)
    elif t == "disk":
        draw.rectangle([cx - w * 0.42, cy - h * 0.34, cx + w * 0.42, cy + h * 0.34], outline=color, width=3)
        draw.line([x0 + w * 0.1, cy - h * 0.1, x1 - w * 0.1, cy - h * 0.1], fill=color, width=2)
        draw.line([x0 + w * 0.1, cy + h * 0.1, x1 - w * 0.1, cy + h * 0.1], fill=color, width=2)
        draw.ellipse([cx - w * 0.12, cy - w * 0.12, cx + w * 0.12, cy + w * 0.12], fill=color)
    elif t == "monitor":
        draw.rectangle([cx - w * 0.4, cy - h * 0.34, cx + w * 0.4, cy + h * 0.16], outline=color, width=3)
        draw.line([cx, cy + h * 0.16, cx, cy + h * 0.3], fill=color, width=4)
        draw.line([cx - w * 0.18, cy + h * 0.3, cx + w * 0.18, cy + h * 0.3], fill=color, width=4)
    elif t == "platform":
        for k in range(3):
            yy = cy - h * 0.26 + k * h * 0.26
            draw.rectangle([x0 + w * 0.16, yy, x1 - w * 0.16, yy + h * 0.14], outline=color, width=3)
            draw.ellipse([x1 - w * 0.2, yy + h * 0.03, x1 - w * 0.08, yy + h * 0.15], fill=color)
    else:
        draw.rectangle([cx - w * 0.2, cy - h * 0.2, cx + w * 0.2, cy + h * 0.2], outline=color, width=3)
        draw.line([cx - w * 0.2, cy, cx + w * 0.2, cy], fill=color, width=3)


def _scene_intent_map(spec: Spec) -> dict[str, str]:
    m = {}
    for r in spec.requirements:
        if r.target:
            m[r.target] = r.intent or ""
    return m


def _device_node(draw: ImageDraw.ImageDraw, box, item: dict, dashed: bool) -> None:
    x0, y0, x1, y1 = box
    color = _cat_color(item["category"]) if not dashed else C_BOX
    # 卡片
    draw.rounded_rectangle(box, radius=14, fill=NODE_FILL, outline=NODE_LINE, width=2)
    draw.rounded_rectangle([x0, y0, x0 + 6, y1], radius=3, fill=color)  # 左侧色条
    # 图标区
    _glyph(draw, item["category"], [x0 + 18, y0 + 24, x0 + 96, y1 - 24], color)
    tx = x0 + 112
    max_text = (x1 - 52) - tx  # 右侧给数量徽标留空间
    draw.text((tx, y0 + 12), _fit(draw, item["category"], _font(NODE_HEAD), max_text),
              font=_font(NODE_HEAD), fill=color)
    if not dashed:
        draw.text((tx, y0 + 38), _fit(draw, item.get("model", ""), _font(NODE_MODEL), max_text),
                  font=_font(NODE_MODEL), fill=INK)
        sub = _fit(draw, item.get("display_name", ""), _font(NODE_SUB), max_text)
        if sub:
            draw.text((tx, y0 + 62), sub, font=_font(NODE_SUB), fill=MUTED)
        # 使用地点（需求3核心）：所有设备底部全宽标注使用地点，保证完整显示不截断
        scene = item.get("scene") or ""
        intent = item.get("intent") or ""
        if scene:
            fy = y1 - 50
            draw.rounded_rectangle([x0 + 12, fy, x1 - 12, fy + 22], radius=8,
                                   fill=(255, 244, 245), outline=LINK, width=1)
            draw.text((x0 + 22, fy + 4),
                      _fit(draw, f"地点：{scene}", _font(NODE_SUB), (x1 - 16) - (x0 + 22)),
                      font=_font(NODE_SUB), fill=LINK_DARK)
        if intent:
            # 摄像机在地点下方再补一行建设目标
            draw.text((x0 + 22, y1 - 24),
                      _fit(draw, f"目标：{intent}", _font(NODE_SUB), (x1 - 16) - (x0 + 22)),
                      font=_font(NODE_SUB), fill=C_PLATFORM)
        # 数量徽标（右上角，独占，避免与底部地点条重叠）
        bx, by = x1 - 28, y0 + 26
        draw.ellipse([bx - 20, by - 20, bx + 20, by + 20], fill=LINK)
        _centered_at(draw, f"×{item['qty']}", _font(QTY_FONT), bx, by, (255, 255, 255))
    else:
        draw.text((tx, y0 + 50), "待选型", font=_font(NODE_SUB), fill=MUTED)


def _arrow_down(draw: ImageDraw.ImageDraw, x, y0, y1) -> None:
    draw.line((x, y0, x, y1), fill=LINK, width=4)
    head = 12
    draw.polygon([(x, y1), (x - head * 0.6, y1 - head), (x + head * 0.6, y1 - head)], fill=LINK)


def _layer_nodes(spec: Spec) -> dict[str, list[tuple[dict, bool]]]:
    intent_map = _scene_intent_map(spec)

    def mk(d, scene_override=None):
        return ({
            "category": d.category,
            "model": d.model,
            "display_name": d.display_name or "",
            "qty": d.qty,
            "scene": scene_override or d.scene or "",
            "intent": intent_map.get(d.scene, "") if d.category == "网络摄像机" else "",
        }, False)

    front = [mk(d) for d in spec.devices if d.category == "网络摄像机"]
    trans = [mk(d) for d in spec.devices if d.category == "PoE交换机"]
    storage = [mk(d) for d in spec.devices if d.category in ("NVR", "通用存储录像机", "机械硬盘")]
    display = [mk(d) for d in spec.devices
               if d.category in ("监视器", "拼接屏", "拼接支架", "解码器")]
    plat = [mk(d) for d in spec.devices if "平台" in d.category]
    if not plat:
        plat = [({"category": "综合管理平台", "model": "P1 规划", "display_name": "待选型",
                   "qty": 0, "scene": "监控中心", "intent": ""}, True)]

    return {"前端": front, "传输": trans, "存储": storage, "平台": plat, "显示": display}


def render_topology(spec: Spec, out_path: Path | str, mode: str = "simple") -> Path:
    if mode == "detailed":
        return _render_detailed(spec, out_path)
    return _render_simple(spec, out_path)


# ===========================================================================
# 简略分层版（带场景 / 使用地点标注）
# ===========================================================================
def _render_simple(spec: Spec, out_path: Path | str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    nodes = _layer_nodes(spec)
    area_x = MARGIN_X + LABEL_W

    band_layout = []
    max_content_w = 0
    for layer in LAYERS:
        items = nodes[layer] or [({"category": f"{layer}设备", "model": "待选型", "display_name": "",
                                    "qty": 0, "scene": "", "intent": ""}, True)]
        n = len(items)
        per_row = min(n, MAX_PER_ROW) or 1
        content_w = per_row * CARD_W + (per_row - 1) * CARD_GAP
        max_content_w = max(max_content_w, content_w)
        rows = math.ceil(n / per_row)
        bh = max(CARD_H + 24, rows * CARD_H + (rows - 1) * CARD_GAP + 24)
        band_layout.append((items, per_row, rows, bh))

    area_w = max_content_w
    W = MARGIN_X * 2 + LABEL_W + area_w
    top = 150
    band_rects = []
    for items, per_row, rows, bh in band_layout:
        band_rects.append((top, bh, items, per_row, rows))
        top += bh + BAND_GAP
    H = top + 46

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    title_font = _font(TITLE_FONT)
    sub_font = _font(SUB_FONT)
    title = "系统拓扑图（简略）"
    tw = _text_w(draw, title, title_font)
    draw.text(((W - tw) / 2, 30), title, font=title_font, fill=INK)
    sub = f"{spec.project.name} · {spec.project.industry}"
    sw = _text_w(draw, sub, sub_font)
    draw.text(((W - sw) / 2, 82), sub, font=sub_font, fill=MUTED)
    draw.line((MARGIN_X, 122, W - MARGIN_X, 122), fill=LINK, width=3)

    for i, (btop, bh, items, per_row, rows) in enumerate(band_rects):
        layer = LAYERS[i]
        draw.rounded_rectangle((MARGIN_X, btop, W - MARGIN_X, btop + bh), radius=14,
                               fill=PANEL, outline=PANEL_LINE, width=1)
        draw.rounded_rectangle((MARGIN_X, btop, MARGIN_X + LABEL_W, btop + bh), radius=14,
                               fill=HEADER_FILL)
        draw.rectangle((MARGIN_X, btop, MARGIN_X + 5, btop + bh), fill=LINK_DARK)
        lw = _text_w(draw, f"{layer} 层", _font(HEAD_FONT))
        draw.text((MARGIN_X + (LABEL_W - lw) / 2, btop + bh / 2 - 26), f"{layer} 层",
                  font=_font(HEAD_FONT), fill=HEADER_INK)
        cw = _text_w(draw, f"{len(items)} 类设备", _font(SUB_FONT))
        draw.text((MARGIN_X + (LABEL_W - cw) / 2, btop + bh / 2 + 6), f"{len(items)} 类设备",
                  font=_font(SUB_FONT), fill=(255, 214, 216))

        card_w = CARD_W
        total_h = rows * CARD_H + (rows - 1) * CARD_GAP
        start_y = btop + (bh - total_h) / 2

        idx = 0
        for r in range(rows):
            row_n = min(per_row, len(items) - idx)
            row_w = row_n * card_w + (row_n - 1) * CARD_GAP
            start_x = area_x + (area_w - row_w) / 2
            ry = start_y + r * (CARD_H + CARD_GAP) + CARD_H / 2
            for c in range(row_n):
                x0 = start_x + c * (card_w + CARD_GAP)
                _device_node(draw, (x0, ry - CARD_H / 2, x0 + card_w, ry + CARD_H / 2),
                             items[idx][0], items[idx][1])
                idx += 1

        if i < len(band_rects) - 1:
            next_top = band_rects[i + 1][0]
            _arrow_down(draw, area_x + area_w / 2, btop + bh + 4, next_top - 4)

    img.save(out, "PNG")
    return out


# ===========================================================================
# 超详细逻辑连线版：场景摄像机 → 交换机 → NVR → 硬盘 → 平台 → 监视器
# ===========================================================================
def _detailed_node_box(draw, box, title, sub1, sub2, qty, color):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=14, fill=NODE_FILL, outline=color, width=2)
    draw.rounded_rectangle([x0, y0, x0 + 6, y1], radius=3, fill=color)
    draw.text((x0 + 22, y0 + 14), title, font=_font(NODE_HEAD + 2), fill=color)
    if sub1:
        draw.text((x0 + 22, y0 + 44), _fit(draw, sub1, _font(NODE_MODEL), (x1 - x0) - 44),
                  font=_font(NODE_MODEL), fill=INK)
    if sub2:
        draw.text((x0 + 22, y0 + 70), _fit(draw, sub2, _font(NODE_SUB), (x1 - x0) - 44),
                  font=_font(NODE_SUB), fill=MUTED)
    if qty:
        bx, by = x1 - 30, y0 + 28
        draw.ellipse([bx - 22, by - 22, bx + 22, by + 22], fill=color)
        _centered_at(draw, f"×{qty}", _font(QTY_FONT), bx, by, (255, 255, 255))


def _darrow(draw, x0, y0, x1, y1, color=LINK, label=None, label_font=None):
    draw.line((x0, y0, x1, y1), fill=color, width=4)
    import math as _m
    ang = _m.atan2(y1 - y0, x1 - x0)
    head = 13
    ah = [(x1, y1),
          (x1 - head * _m.cos(ang - 0.4), y1 - head * _m.sin(ang - 0.4)),
          (x1 - head * _m.cos(ang + 0.4), y1 - head * _m.sin(ang + 0.4))]
    draw.polygon(ah, fill=color)
    if label and label_font:
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        lw = _text_w(draw, label, label_font)
        draw.rectangle((mx - lw / 2 - 5, my - 11, mx + lw / 2 + 5, my + 13), fill=BG)
        draw.text((mx - lw / 2, my - 9), label, font=label_font, fill=LINK_DARK)


def _render_detailed(spec: Spec, out_path: Path | str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    intent_map = _scene_intent_map(spec)
    cameras = [d for d in spec.devices if d.category == "网络摄像机"]
    # 摄像机按场景分组
    scenes: dict[str, list] = {}
    for d in cameras:
        scenes.setdefault(d.scene or "未分类", []).append(d)
    scene_items = []
    for sc, ds in scenes.items():
        qty = sum(x.qty for x in ds)
        model = ds[0].model
        dn = ds[0].display_name or ""
        scene_items.append((sc, intent_map.get(sc, ""), model, dn, qty))

    switches = [d for d in spec.devices if d.category == "PoE交换机"]
    nvrs = [d for d in spec.devices if d.category in ("NVR", "通用存储录像机")]
    disks = [d for d in spec.devices if d.category == "机械硬盘"]
    platforms = [d for d in spec.devices if "平台" in d.category]
    displays = [d for d in spec.devices if d.category in ("监视器", "拼接屏")]

    # 画布尺寸
    SCENE_W, SCENE_H, SCENE_GAP = 320, 96, 16
    n_sc = max(1, len(scene_items))
    scenes_band_h = n_sc * SCENE_H + (n_sc - 1) * SCENE_GAP

    SPINE_W, SPINE_H, SPINE_GAP = 250, 96, 22
    # spine 节点：交换机 / NVR / 平台 / 监视器（存在的才画）
    spine_defs = []
    if switches:
        spine_defs.append(("接入交换机", switches[0].model, switches[0].display_name or "PoE 接入",
                           sum(x.qty for x in switches), C_SWITCH))
    if nvrs:
        spine_defs.append(("NVR 录像机", nvrs[0].model, "存储与录像",
                           sum(x.qty for x in nvrs), C_RECORDER))
    if platforms:
        spine_defs.append(("综合管理平台", platforms[0].model, "统一管理与上墙",
                           sum(x.qty for x in platforms), C_PLATFORM))
    if displays:
        head = "监视器" if displays[0].category == "监视器" else "拼接电视墙"
        spine_defs.append((head, displays[0].model, "监控中心上墙",
                           sum(x.qty for x in displays), C_MONITOR))
    n_sp = len(spine_defs)
    spine_band_h = n_sp * SPINE_H + (n_sp - 1) * SPINE_GAP

    M = 56
    SCENE_X = M
    SPINE_X = SCENE_X + SCENE_W + 130
    HDD_X = SPINE_X + SPINE_W + 60  # 硬盘分支在 NVR 右侧
    HDD_W = 200

    band_h = max(scenes_band_h, spine_band_h)
    TOP = 150
    W = HDD_X + HDD_W + M
    H = TOP + band_h + 200  # 底部留图例

    # 场景区垂直居中
    scene_top = TOP + (band_h - scenes_band_h) / 2
    spine_top = TOP + (band_h - spine_band_h) / 2

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    title_font = _font(TITLE_FONT)
    sub_font = _font(SUB_FONT)
    title = "系统拓扑图（详细 · 逻辑连线）"
    tw = _text_w(draw, title, title_font)
    draw.text(((W - tw) / 2, 30), title, font=title_font, fill=INK)
    sub = f"{spec.project.name} · {spec.project.industry} · 全配单互连示意"
    sw = _text_w(draw, sub, sub_font)
    draw.text(((W - sw) / 2, 82), sub, font=sub_font, fill=MUTED)
    draw.line((M, 122, W - M, 122), fill=LINK, width=3)

    # ---- 场景摄像机节点 ----
    sy = scene_top
    scene_centers = []
    for sc, intent, model, dn, qty in scene_items:
        box = (SCENE_X, sy, SCENE_X + SCENE_W, sy + SCENE_H)
        sub1 = model
        sub2 = (intent + " · " if intent else "") + (dn or "")
        _detailed_node_box(draw, box, sc, sub1, sub2, qty, C_CAMERA)
        scene_centers.append((box[0] + SCENE_W, (box[1] + box[3]) / 2))
        sy += SCENE_H + SCENE_GAP

    # ---- spine 节点 ----
    spy = spine_top
    spine_boxes = []
    for t, model, s, q, col in spine_defs:
        box = (SPINE_X, spy, SPINE_X + SPINE_W, spy + SPINE_H)
        _detailed_node_box(draw, box, t, model, s, q, col)
        spine_boxes.append(box)
        spy += SPINE_H + SPINE_GAP

    # spine 节点中心点
    spine_centers = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in spine_boxes]

    # ---- 连线 ----
    # 1) 每个场景 → 交换机（扇形分散接入交换机左边缘不同高度）
    if spine_boxes:
        sw_box = spine_boxes[0]
        sw_left = sw_box[0]
        n_s = len(scene_centers)
        for i, (ex, ey) in enumerate(scene_centers):
            ty = sw_box[1] + (i + 1) / (n_s + 1) * SPINE_H
            _darrow(draw, ex, ey, sw_left, ty, label=None)
        # 交换机入口标签（置于交换机左上方）
        draw.text((sw_left - 122, sw_box[1] - 8), "网线/PoE 接入", font=_font(NODE_SUB),
                  fill=LINK_DARK)

    # 2) spine 内部顺序连线 + 标签
    spine_labels = ["视频流汇聚", "统一管理", "上墙显示"]
    for i in range(len(spine_boxes) - 1):
        b0 = spine_boxes[i]
        b1 = spine_boxes[i + 1]
        x0 = (b0[0] + b0[2]) / 2
        y0 = b0[3]
        x1 = (b1[0] + b1[2]) / 2
        y1 = b1[1]
        lab = spine_labels[i] if i < len(spine_labels) else ""
        _darrow(draw, x0, y0 + 6, x1, y1 - 6, label=lab, label_font=_font(NODE_SUB))

    # 3) NVR → 硬盘（存储分支）
    if nvrs and disks and len(spine_boxes) >= 2:
        nvr_box = spine_boxes[1] if len(spine_boxes) > 1 else spine_boxes[0]
        hdd_qty = sum(x.qty for x in disks)
        hdd_box = (HDD_X, nvr_box[1], HDD_X + HDD_W, nvr_box[1] + SPINE_H)
        _detailed_node_box(draw, hdd_box, "监控硬盘", disks[0].model if disks else "",
                           "录像存储", hdd_qty, C_DISK)
        _darrow(draw, nvr_box[2], (nvr_box[1] + nvr_box[3]) / 2,
                hdd_box[0], (hdd_box[1] + hdd_box[3]) / 2, label="录像存储",
                label_font=_font(NODE_SUB))

    # ---- 图例 ----
    ly = TOP + band_h + 28
    draw.text((M, ly), "连线含义：", font=_font(HEAD_FONT), fill=INK)
    legend = [
        "摄像机（按使用地点分组）→ 接入交换机（网线 / PoE）",
        "交换机 → NVR（视频流汇聚与录像）→ 综合平台（统一管理）→ 监视器 / 拼接电视墙（上墙显示）",
        "NVR → 监控硬盘（录像存储，按冗余系数折算块数）",
        "注：本图为逻辑连接示意；实际工程按点位就近接入交换机，并按区域 / 机柜位置部署。型号与数量来自选型结果。",
    ]
    yy = ly + 36
    for line in legend:
        draw.text((M + 8, yy), "• " + line, font=_font(NODE_SUB), fill=MUTED)
        yy += 28

    img.save(out, "PNG")
    return out
