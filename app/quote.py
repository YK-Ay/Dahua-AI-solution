"""报价单：Spec → 明细行 + 标准价合计。

只汇总产品库里已有的标准价，不臆造折扣、税费、施工与维保费用；
缺失单价的型号单独计数，避免"看起来有总价其实漏项"。
"""
from __future__ import annotations

from app.models import Spec


def build_quote(spec: Spec | dict) -> dict:
    if isinstance(spec, dict):
        spec = Spec.model_validate(spec)
    lines: list[dict] = []
    total = 0.0
    unpriced = 0
    for idx, d in enumerate(spec.devices, 1):
        amount = None
        if d.unit_price is not None:
            amount = round(float(d.unit_price) * int(d.qty), 2)
            total += amount
        else:
            unpriced += 1
        lines.append({
            "index": idx,
            "category": d.category,
            "model": d.model,
            "display_name": d.display_name,
            "scene": d.scene,
            "qty": d.qty,
            "unit_price": d.unit_price,
            "amount": amount,
        })
    return {
        "currency": "CNY",
        "lines": lines,
        "total": round(total, 2),
        "priced": len(lines) - unpriced,
        "unpriced": unpriced,
        "note": "合计为标准价（未税、未折扣、未含施工与维保）；最终价格以商务报价单为准。",
    }
