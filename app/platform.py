"""平台层选型：按项目规模在「SMB 一体机 / ICC 平台」之间分层。

依据来自两类真实证据，而不是拍脑袋固定 qty=1：
- 53 份云商配单：SMB-H2（小区/园区轻量，4 个方案）、SMB-X1（多 NVR 一体机，
  4 个方案），且配单明确写「多台 NVR 选配，单台 NVR 无需平台」；
- 中标明细：ICC 纯软平台 DH-ICC-B8900-U-PRO 的中标价从 ¥2,700 到 ¥255,900，
  同型号差异来自「版本 + 通道授权 + 服务器」，因此不能给一个标准价了事，
  必须拆成 平台软件 / 通道授权 / 服务器 三项，分别标注计价口径。
"""
from __future__ import annotations

from .kb import ProductRepo
from .models import Device

# 规模分档：路数 → 平台形态
SMB_H2_MAX_LANES = 32     # 1 台 NVR，轻量一体机
SMB_X1_MAX_LANES = 128    # 2~4 台 NVR，多 NVR 一体机
SERVER_U64_MAX_LANES = 256  # ICC 服务器：≤256 路企业版，以上旗舰版

ICC_PLATFORM = "DH-ICC-B8900-U-PRO"          # 纯软平台
ICC_LICENSE = "DH-ICC-Common-VD-COCHN"       # 视频通道授权
ICC_SERVER = "DH-ICC-B8900S3H-HW-ARM-U64"    # 企业版服务器
ICC_SERVER_U128 = "DH-ICC-B8900S4H-HW-ARM-U128"  # 旗舰版服务器

# 招投标证据：同型号中标价区间（用于选型理由，不参与计算）
ICC_PRICE_EVIDENCE = "¥2,700–¥255,900"
SERVER_PRICE_EVIDENCE = "¥34,800–¥200,000"


def _row(repo: ProductRepo, model: str, require_price: bool) -> dict | None:
    """按订货型号精确取产品；找不到或缺少标准价时返回 None。"""
    sql = ("SELECT model, display_name, price_stand, class_name, year_new "
           "FROM products WHERE model = ? AND model != '/'")
    if require_price:
        sql += " AND price_stand IS NOT NULL"
    sql += " ORDER BY price_stand IS NULL, price_stand ASC LIMIT 1"
    row = repo.conn.execute(sql, (model,)).fetchone()
    return dict(row) if row else None


def build_platform(
    repo: ProductRepo,
    lanes: int,
    nvr_qty: int,
    industry: str = "",
) -> tuple[list[Device], list[str], list[str]]:
    """返回 (平台层设备, 缺口, 假设说明)。"""
    devices: list[Device] = []
    gaps: list[str] = []
    notes: list[str] = []

    # 交管项目即使点位少，也需要 ICC 承载信号/卡口/诱导等业务
    big = lanes > SMB_X1_MAX_LANES or industry == "智慧交管"
    mid = not big and lanes > SMB_H2_MAX_LANES

    if not big and not mid:
        p = _row(repo, "DH-SMB-H2", require_price=True)
        if not p:
            gaps.append("产品库缺少轻量管理平台 DH-SMB-H2，平台层待补")
            return devices, gaps, notes
        devices.append(Device(
            category="综合平台", model=p["model"], qty=1, scene="监控中心",
            display_name=p["display_name"], unit_price=p["price_stand"],
            reason=(f"{lanes} 路（{nvr_qty} 台 NVR）· 轻量场景；"
                    f"方案库 4 个 SMB 方案选用园区管理一体化平台"),
        ))
        return devices, gaps, notes

    if mid:
        p = _row(repo, "DH-SMB-X1", require_price=True)
        if not p:
            gaps.append("产品库缺少多 NVR 管理平台 DH-SMB-X1，平台层待补")
            return devices, gaps, notes
        devices.append(Device(
            category="综合平台", model=p["model"], qty=1, scene="监控中心",
            display_name=p["display_name"], unit_price=p["price_stand"],
            reason=(f"{lanes} 路（{nvr_qty} 台 NVR）需平台统一管理；"
                    f"方案库 4 个方案选用多 NVR 一体机"),
        ))
        return devices, gaps, notes

    # ---- ICC 分层：纯软平台 + 通道授权 + 服务器 ----
    if industry == "智慧交管" and not lanes > SMB_X1_MAX_LANES:
        why = f"{lanes} 路交管业务需 ICC 统一承载卡口/视频/诱导"
    else:
        why = f"{lanes} 路（{nvr_qty} 台 NVR）超出单台一体机管理规模"
    soft = _row(repo, ICC_PLATFORM, require_price=False)
    if soft:
        devices.append(Device(
            category="综合平台", model=soft["model"], qty=1, scene="监控中心",
            display_name=soft["display_name"], unit_price=soft["price_stand"],
            reason=(f"{why}；ICC 按版本 + 通道授权 + 服务器部署，"
                    f"招投标同型号中标价 {ICC_PRICE_EVIDENCE}"),
        ))
        notes.append("ICC 平台软件无标准价，按版本与授权商务询价")
    else:
        gaps.append(f"产品库缺少 ICC 纯软平台 {ICC_PLATFORM}，平台层待补")

    lic = _row(repo, ICC_LICENSE, require_price=True)
    if lic:
        devices.append(Device(
            category="平台授权", model=lic["model"], qty=max(1, lanes), scene="监控中心",
            display_name=lic["display_name"], unit_price=lic["price_stand"],
            reason=f"{lanes} 路视频通道授权 × ¥{lic['price_stand']:g}/路（产品库标准价）",
        ))
    else:
        gaps.append(f"产品库缺少 ICC 视频通道授权 {ICC_LICENSE}，授权数量待核")

    server_model = ICC_SERVER if lanes <= SERVER_U64_MAX_LANES else ICC_SERVER_U128
    server = _row(repo, server_model, require_price=False)
    if server:
        tier = "企业版" if server_model == ICC_SERVER else "旗舰版"
        devices.append(Device(
            category="平台服务器", model=server["model"], qty=1, scene="监控中心",
            display_name=server["display_name"], unit_price=server["price_stand"],
            reason=(f"承载 {lanes} 路 ICC（{tier}服务器）；"
                    f"招投标同档服务器中标价 {SERVER_PRICE_EVIDENCE}，需按配置询价"),
        ))
        notes.append(f"ICC {tier}服务器无标准价，需按 CPU/内存/盘位配置询价")
    else:
        gaps.append(f"产品库缺少 ICC 服务器 {server_model}，服务器待补")

    return devices, gaps, notes
