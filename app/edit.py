"""在线编辑：交付页改路数 / 存储天数 / 项目名 / 换型号 / 改数量后确定性重算。

规则与审阅页一致：
  - 改总路数 → 抹掉原文场景数字、追加总点位，交给 build_spec 重新分配并重新选型
  - 换型号 → 只允许产品库里真实存在的型号，单价与名称同步取自产品库
  - 任何一步失败都抛错，不产出半成品
"""
from __future__ import annotations

import re

from app.builder import build_spec, validate_spec
from app.kb import ProductRepo
from app.models import Spec

LANES_NUM_RE = re.compile(r"\d+\s*(?:路|个点位|台摄像机|摄像机|点位|个监控)")


def _apply_lanes_override(text: str, lanes: int | None) -> str:
    if lanes is None:
        return text
    cleaned = re.sub(r"[ \t]{2,}", " ", LANES_NUM_RE.sub("", text))
    return f"{cleaned}\n总点位 {lanes} 路"


def _positive(value, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        num = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是正整数") from exc
    if num < 1:
        raise ValueError(f"{label}必须是正整数")
    return num


def apply_edit(current: Spec, text: str, patch: dict) -> Spec:
    lanes = _positive(patch.get("lanes"), "总路数")
    days = _positive(patch.get("retention_days"), "存储天数")
    name = (patch.get("name") or "").strip() or current.project.name

    if lanes is not None or days is not None or name != current.project.name:
        base = build_spec(
            _apply_lanes_override(text, lanes),
            name=name,
            lanes=lanes,
            retention_days=days,
        )
    else:
        base = current

    data = base.model_dump()
    edits = patch.get("devices") or []
    if edits:
        repo = ProductRepo()
        try:
            for edit in edits:
                idx = int(edit.get("index")) - 1
                if idx < 0 or idx >= len(data["devices"]):
                    raise ValueError(f"设备序号越界：{edit.get('index')}")
                target = data["devices"][idx]
                model = (edit.get("model") or "").strip()
                if model and model != target["model"]:
                    info = repo.info(model)
                    if not info:
                        raise ValueError(f"产品库不存在型号：{model}")
                    target["model"] = info["model"]
                    target["display_name"] = info["display_name"]
                    target["unit_price"] = info["price_stand"]
                qty = _positive(edit.get("qty"), "设备数量")
                if qty is not None:
                    target["qty"] = qty
        finally:
            repo.close()

    spec = Spec.model_validate(data)
    validate_spec(spec)
    return spec
