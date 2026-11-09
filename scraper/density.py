"""W1 技术密度标注器（启发式）。

判断一页官网素材是"真技术"还是"营销话术"：
- 技术词：型号 / 码流 / 分辨率 / 存储容量 / 路数 / 编解码 / 工程参数
- 营销词：赋能 / 助力 / 一网统管 / 数字化转型 / 生态圈 等空泛话术
density_score = tech / (tech + market)，越接近 1 越像技术文档。
"""
import re

TECH_WORDS = [
    "分辨率", "码流", "Mbps", "H.265", "H.264", "H265", "像素", "帧", "fps",
    "TB", "GB", "存储容量", "NVR", "DVR", "IPC", "DH-", "DH", "宽动态",
    "补光", "红外", "焦距", "镜头", "照度", "PoE", "POE", "交换机", "解码",
    "上墙", "拼接", "UPS", "冗余", "点位", "立杆", "光纤", "路数", "回放",
    "抓拍", "卡口", "电警", "线圈", "雷达", "视频结构化",
]

MARKET_WORDS = [
    "赋能", "助力", "一网统管", "数字化转型", "智慧化升级", "生态圈",
    "数据底座", "闭环", "抓手", "打造", "领先", "卓越", "全方位", "一站式",
    "深度融合", "数据融合", "智慧化", "智能化升级", "携手", "共创", "新篇章",
    "新格局", "新生态", "标杆", "典范", "领跑", "保驾护航",
]


def _hits(text, words):
    return sum(len(re.findall(re.escape(w), text, re.I)) for w in words)


def annotate(text):
    tech = _hits(text, TECH_WORDS)
    market = _hits(text, MARKET_WORDS)
    denom = tech + market
    score = round(tech / denom, 3) if denom else 0.0
    if tech == 0 and market == 0:
        kind = "unknown"
    elif score >= 0.6:
        kind = "tech_page"
    elif score <= 0.4:
        kind = "marketing_page"
    else:
        kind = "mixed"
    return {"density_score": score, "tech_hits": tech,
            "market_hits": market, "page_kind": kind}
