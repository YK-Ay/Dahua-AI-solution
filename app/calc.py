"""确定性计算：存储容量与上行带宽。

口径写在代码里而不是 prompt 里，参数全部显式可调：
  GB/天/路 = 码流(Mbps) × 86400 / 8 / 1024 ≈ 码流 × 10.5469
  raw_tb   = Σ(路数 × GB/天/路) × 存储天数 / 1024
  冗余容量 = raw_tb × 冗余系数（默认 1.1）
  上行带宽 = Σ(路数 × 码流) × 并发系数（默认 1.2）
"""
from __future__ import annotations

from .models import Requirement

GB_PER_DAY_PER_MBPS = 86400 / 8 / 1024
DEFAULT_REDUNDANCY = 1.1
DEFAULT_BANDWIDTH_OVERHEAD = 1.2


def gb_per_day_per_lane(bitrate_mbps: float) -> float:
    """单路单天产生的原始数据量（GB）。"""
    return bitrate_mbps * GB_PER_DAY_PER_MBPS


def calc_storage(
    requirements: list[Requirement],
    retention_days: int,
    redundancy: float = DEFAULT_REDUNDANCY,
) -> dict:
    """按需求清单计算裸容量与冗余容量（TB）。"""
    if retention_days <= 0:
        raise ValueError("存储天数必须大于 0")
    raw_gb = sum(
        r.lanes * gb_per_day_per_lane(r.bitrate_mbps) for r in requirements
    ) * retention_days
    raw_tb = raw_gb / 1024
    return {
        "raw_tb": round(raw_tb, 1),
        "with_redundancy_tb": round(raw_tb * redundancy, 1),
        "redundancy": redundancy,
    }


def calc_bandwidth(
    requirements: list[Requirement],
    overhead: float = DEFAULT_BANDWIDTH_OVERHEAD,
) -> float:
    """上行带宽（Mbps），按路数 × 码流求和后乘并发系数。"""
    total_mbps = sum(r.lanes * r.bitrate_mbps for r in requirements)
    return round(total_mbps * overhead, 1)


def total_lanes(requirements: list[Requirement]) -> int:
    return sum(r.lanes for r in requirements)
