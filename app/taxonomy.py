"""行业 → 场景 → 能力 → 设备族 的分类表（纯数据 + 纯函数）。

为什么要单独一张表：
- 原来 `builder.detect_industry` 只有三选一、`SCENE_RULES` 只有 7 个通用场景，
  电力/化工/医疗/金融等需求全部塌陷成「智慧园区」，特种设备永远选不出来。
- 这里把「行业关键词」「行业允许哪些场景」「场景该用哪些设备类目」
  「专属设备缺失时是报缺还是降级」全部数据化，改口径只改这张表，不改计算逻辑。

口径约束（与项目反幻觉原则一致）：
- 本模块不产出任何数字，只做词表匹配与类目映射。
- 数字仍由 `app.builder` 的正则和 `app.calc` 的确定性函数处理。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 设备类目

# 通用安防摄像机类目：这些类目走 RAG 选型（复用 53 份配单 + 中标明细）
GENERIC_CAMERA_CLASSES = frozenset({
    "网络摄像机", "分销球机", "球机", "项目型网络摄像机",
    "全景系列网络摄像机", "行业球机产品", "行业夜视王",
})

# ---------------------------------------------------------------- 行业定义

# order 只影响同分时的优先级；越靠前越优先
INDUSTRIES: dict[str, dict] = {
    "智慧电力": {
        "keys": {"变电站": 5, "变电所": 5, "升压站": 5, "开关站": 5, "换流站": 5,
                 "输电": 5, "杆塔": 5, "导线": 3, "绝缘子": 4, "电网": 5,
                 "配电房": 4, "配电柜": 4, "配电室": 4, "开关柜": 4, "环网柜": 4,
                 "电缆沟": 3, "光伏": 4, "储能": 4, "风电": 4, "电力": 3, "巡检": 2},
        "bom_industries": ["电力"],
    },
    "智慧交管": {
        "keys": {"交管": 5, "交警": 5, "卡口": 4, "电子警察": 5, "电警": 5,
                 "测速": 4, "违停": 4, "诱导屏": 5, "信号机": 5, "交通事件": 4,
                 "高速公路": 4, "城市道路": 4, "车路协同": 5, "隧道": 3,
                 "交通": 3, "通行": 2},
        "bom_industries": ["智能交通", "出入口"],
    },
    "智慧化工能源": {
        "keys": {"化工": 5, "危化": 5, "油站": 5, "加油站": 5, "油库": 5,
                 "罐区": 5, "装卸区": 4, "燃气": 4, "矿山": 4, "冶金": 4,
                 "管廊": 3, "粉尘": 3, "防爆": 3},
        "bom_industries": ["中小化工厂"],
    },
    "智慧水利": {
        "keys": {"水利": 5, "水库": 5, "河道": 5, "河湖": 5, "泵站": 5,
                 "闸站": 5, "水闸": 4, "防汛": 5, "水位": 4, "大坝": 5,
                 "堤防": 4, "灌区": 4, "防溺水": 4},
        "bom_industries": ["水利", "数字乡村"],
    },
    "智慧金融": {
        "keys": {"银行": 5, "ATM": 5, "自助银行": 5, "金库": 5, "现金区": 5,
                 "信用社": 5, "证券": 4, "保险": 3, "网点": 2, "柜台": 2},
        "bom_industries": ["金融"],
    },
    "智慧医疗": {
        "keys": {"医院": 5, "门诊": 5, "卫生院": 5, "手术室": 5, "病房": 4,
                 "ICU": 4, "疾控": 4, "药房": 2, "医疗": 4},
        "bom_industries": ["医疗"],
    },
    "文博景区": {
        "keys": {"博物馆": 5, "文物": 5, "展馆": 5, "纪念馆": 5, "景区": 5,
                 "游客中心": 4, "遗址": 4, "文旅": 5, "公园": 2},
        "bom_industries": ["景区场馆"],
    },
    "零售连锁": {
        "keys": {"连锁": 4, "门店": 4, "便利店": 5, "超市": 5, "商超": 5,
                 "卖场": 4, "快递驿站": 5, "餐饮": 3, "零售": 4},
        "bom_industries": ["连锁商超", "快递驿站"],
    },
    "智慧校园": {
        "keys": {"校园": 5, "学校": 5, "中学": 5, "小学": 5, "大学": 5,
                 "校区": 5, "幼儿园": 5, "教学楼": 4, "教室": 3, "实验室": 4,
                 "操场": 3, "宿管": 4},
        "bom_industries": ["智慧校园"],
    },
    "智慧社区": {
        "keys": {"小区": 5, "社区": 4, "物业": 4, "安置房": 5, "公寓": 4,
                 "单元楼": 3, "楼栋": 2},
        "bom_industries": ["小区社区"],
    },
    "智慧楼宇": {
        "keys": {"写字楼": 5, "办公楼": 4, "大厦": 4, "酒店": 4, "宾馆": 4,
                 "商业综合体": 5, "商场": 5, "楼宇": 4},
        "bom_industries": ["企业办公", "商业综合体", "酒店民宿"],
    },
    "智慧园区": {
        "keys": {"园区": 5, "产业园": 5, "开发区": 4, "物流园": 5, "保税区": 5,
                 "工厂": 4, "厂区": 4, "车间": 4, "仓储": 3, "制造": 3,
                 "企业": 1, "办公": 1},
        "bom_industries": ["工厂园区", "小区社区", "景区场馆", "商业综合体"],
    },
}

DEFAULT_INDUSTRY = "智慧园区"

# 行业关键词总分低于该值时视为「线索较弱」：仍按命中的行业测算，
# 但在缺口里提示人工确认行业，避免把偶然命中的弱词当成明确行业。
INDUSTRY_LOW_SCORE = 3

# ---------------------------------------------------------------- 场景定义

# 字段说明：
#   keys                 触发词
#   industries           限定行业；空 = 通用场景，所有行业可用
#   camera_classes       候选设备类目，按优先级排列（越靠前越优先）
#   require_price        False 时允许选择无标准价的行业机型（价格留待询价）
#   degrade              "fallback" = 专属缺失时降级通用机型并标注；
#                        "report"   = 专属缺失时直接报缺，不用通用机冒充
#   rag_family           接入 RAG 选型时映射到的既有场景族；None 表示不走 RAG
#   capability_tags      该场景真正需要的能力标签（app/capability.py），
#                        用于跨类目选型与「选型依据」可解释
#   suppresses           本场景命中时压掉其它场景（更具体的场景优先）
#   fallback_only_with_lanes  只有原文明确给了该场景点位数才保留（避免抢路数）
SCENES: list[dict] = [
    # ---------------- 通用：周界 / 出入口 / 室内 / 道路 / 停车 / 电梯 ----------------
    {
        "name": "周界围墙",
        "keys": ["周界", "围墙", "边界"],
        "industries": [],
        "intent": "入侵检测",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["枪型", "400万"],
        "device_category": "网络摄像机",
        "rag_family": "周界围墙",
        "capability_tags": ["周界人车"],
    },
    {
        "name": "出入口",
        "keys": ["出入口", "大门", "门禁", "闸机"],
        "industries": [],
        "intent": "人车通行管理",
        "level": "高",
        "bitrate": 6.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["枪型", "400万"],
        "device_category": "网络摄像机",
        "rag_family": "出入口",
    },
    {
        "name": "主干道",
        "keys": ["主干道", "道路", "路面", "车道"],
        "industries": [],
        "intent": "车辆识别",
        "level": "中",
        "bitrate": 6.0,
        "camera_classes": ["分销球机", "网络摄像机"],
        "camera_keywords": ["球机", "400万"],
        "device_category": "网络摄像机",
        "rag_family": "主干道",
    },
    {
        "name": "电梯轿厢",
        "keys": ["电梯", "轿厢", "入梯"],
        "industries": [],
        "intent": "电瓶车入梯检测",
        "level": "中",
        "bitrate": 2.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["半球", "400万"],
        "device_category": "网络摄像机",
        "rag_family": "电梯轿厢",
        "capability_tags": ["电瓶车入梯"],
    },
    {
        "name": "室内区域",
        "keys": ["室内", "大厅", "走廊", "办公", "教室", "会议室",
                 "教学楼", "宿舍", "食堂", "谈话室", "机房", "办案区"],
        "industries": [],
        "intent": "人员活动监控",
        "level": "中",
        "bitrate": 2.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["海螺", "400万"],
        "device_category": "网络摄像机",
        "rag_family": "室内区域",
    },
    {
        "name": "停车场",
        "keys": ["停车场", "车棚", "车位", "车库", "地库"],
        "industries": [],
        "intent": "车辆停放管理",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["分销球机", "网络摄像机"],
        "camera_keywords": ["球机", "400万"],
        "device_category": "网络摄像机",
        "rag_family": "停车场",
    },
    {
        "name": "公共区域",
        "keys": ["公共区域", "广场", "园区", "小区", "操场"],
        "industries": [],
        "intent": "公共区域视频监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["枪型", "400万"],
        "device_category": "网络摄像机",
        "rag_family": "公共区域",
        "fallback_only_with_lanes": True,
    },
    {
        "name": "消防通道",
        "keys": ["消防通道", "消防车道", "通道占用"],
        "industries": [],
        "intent": "消防通道占用检测",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["枪型", "400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },
    {
        "name": "充电区",
        "keys": ["充电桩", "充电区", "电瓶车充电"],
        "industries": [],
        "intent": "充电区安全监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["枪型", "400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },

    # ---------------- 智慧电力 ----------------
    {
        "name": "输电线路监测",
        "capability_tags": ["输电通道"],
        "keys": ["输电", "杆塔", "导线", "防外破", "电力线路", "线路通道"],
        "industries": ["智慧电力"],
        "intent": "输电通道防外破与本体监测",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["输电相机"],
        "camera_keywords": [],
        "require_price": False,
        "device_category": "输电监测相机",
        "degrade": "report",
        "suppresses": ["周界围墙"],
    },
    {
        "name": "变电设备测温",
        "capability_tags": ["热成像测温"],
        "keys": ["测温", "红外测温", "发热", "温升", "设备测温"],
        "industries": ["智慧电力"],
        "intent": "变电设备热缺陷在线监测",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["测温型枪型摄像机", "测温型双光谱球型摄像机",
                           "观测型双光谱重型云台"],
        "camera_keywords": [],
        "require_price": False,
        "device_category": "热成像测温相机",
        "degrade": "report",
    },
    {
        "name": "变电站站区",
        "capability_tags": ["热成像测温", "周界人车"],
        "keys": ["变电站", "变电所", "升压站", "开关站", "换流站", "站区"],
        "industries": ["智慧电力"],
        "intent": "站区周界与设备区监控",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["测温型枪型摄像机", "测温型双光谱球型摄像机", "网络摄像机"],
        "camera_keywords": [],
        "require_price": False,
        "device_category": "网络摄像机",
        "degrade": "fallback",
        "suppresses": ["周界围墙"],
    },
    {
        "name": "配电房",
        "capability_tags": ["热成像测温"],
        "keys": ["配电房", "配电柜", "配电室", "开关柜", "环网柜", "电缆沟"],
        "industries": ["智慧电力", "智慧化工能源", "智慧园区", "智慧楼宇"],
        "intent": "配电设施状态与温度监测",
        "level": "中",
        "bitrate": 2.0,
        "camera_classes": ["测温型枪型摄像机", "网络摄像机"],
        "camera_keywords": [],
        "require_price": False,
        "device_category": "网络摄像机",
        "degrade": "fallback",
        "suppresses": ["室内区域", "变电设备测温"],
    },
    {
        "name": "光伏储能站",
        "capability_tags": ["热成像测温"],
        "keys": ["光伏", "储能", "风电", "风机"],
        "industries": ["智慧电力"],
        "intent": "新能源场站设备与周界监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["测温型枪型摄像机", "网络摄像机"],
        "camera_keywords": ["400万"],
        "require_price": False,
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },

    # ---------------- 智慧交管 ----------------
    {
        "name": "道路卡口",
        "capability_tags": ["车牌抓拍"],
        "keys": ["卡口", "过车", "车辆抓拍", "抓拍单元"],
        "industries": ["智慧交管"],
        "intent": "车辆过车抓拍与特征识别",
        "level": "高",
        "bitrate": 8.0,
        "camera_classes": ["萤光系列", "车行出入口"],
        "camera_keywords": [],
        "require_price": False,
        "device_category": "卡口抓拍单元",
        "degrade": "report",
        "suppresses": ["主干道"],
    },
    {
        "name": "电子警察",
        "capability_tags": ["车牌抓拍"],
        "keys": ["电子警察", "电警", "违法抓拍", "违章"],
        "industries": ["智慧交管"],
        "intent": "交通违法行为自动取证",
        "level": "高",
        "bitrate": 8.0,
        "camera_classes": ["萤光系列"],
        "camera_keywords": [],
        "require_price": False,
        "device_category": "电子警察抓拍单元",
        "degrade": "report",
        "suppresses": ["道路卡口", "主干道"],
    },
    {
        "name": "测速卡口",
        "capability_tags": ["车牌抓拍"],
        "keys": ["测速", "区间测速", "超速"],
        "industries": ["智慧交管"],
        "intent": "机动车测速取证",
        "level": "高",
        "bitrate": 8.0,
        "camera_classes": ["萤光系列"],
        "camera_keywords": [],
        "require_price": False,
        "device_category": "测速抓拍单元",
        "degrade": "report",
        "suppresses": ["道路卡口", "主干道"],
    },
    {
        "name": "违停抓拍",
        "capability_tags": ["车牌抓拍"],
        "keys": ["违停", "违法停车", "乱停"],
        "industries": ["智慧交管"],
        "intent": "违法停车自动抓拍",
        "level": "中",
        "bitrate": 6.0,
        "camera_classes": ["行业球机产品", "分销球机", "网络摄像机"],
        # 行业球机/网络摄像机公开型号命名不统一，优先按类目筛选。
        "camera_keywords": [],
        "require_price": False,
        "device_category": "违停抓拍单元",
        "degrade": "fallback",
    },
    {
        "name": "交通事件检测",
        "keys": ["交通事件", "事件检测", "抛洒物", "拥堵", "逆行"],
        "industries": ["智慧交管"],
        "intent": "道路异常事件自动检测",
        "level": "中",
        "bitrate": 6.0,
        "camera_classes": ["行业球机产品", "网络摄像机"],
        # 算法能力不能由外观关键词替代，具体授权与能力在工程审查中确认。
        "camera_keywords": [],
        "require_price": False,
        "device_category": "事件检测相机",
        "degrade": "report",
    },
    {
        "name": "停车出入口",
        "capability_tags": ["车牌抓拍"],
        "keys": ["停车出入口", "道闸", "车牌识别", "车位引导"],
        "industries": [],
        "intent": "车辆出入控制与车牌识别",
        "level": "高",
        "bitrate": 6.0,
        "camera_classes": ["车行出入口"],
        "camera_keywords": ["出入口", "抓拍"],
        "device_category": "车行出入口设备",
        "degrade": "fallback",
        "suppresses": ["停车场"],
    },
    {
        "name": "人行闸机",
        "keys": ["人行道闸", "摆闸", "翼闸", "访客", "人行通道", "三辊闸"],
        "industries": [],
        "intent": "人员通行控制与身份核验",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["人行出入口"],
        "camera_keywords": [],
        "device_category": "人行通道设备",
        "degrade": "report",
        "suppresses": ["出入口"],
    },

    # ---------------- 智慧化工能源 ----------------
    {
        "name": "防爆区域",
        "capability_tags": ["防爆"],
        "keys": ["防爆", "爆炸性环境", "危险区", "危化", "化工", "油站",
                 "加油站", "油库", "罐区", "装卸区", "燃气"],
        "industries": ["智慧化工能源"],
        "intent": "爆炸性环境视频监控",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["防爆摄像机", "防爆多功能摄像机", "防爆防腐蚀产品"],
        "camera_keywords": [],
        "device_category": "防爆摄像机",
        "degrade": "report",
    },
    {
        "name": "管廊巡检",
        "capability_tags": ["热成像测温"],
        "keys": ["管廊", "管线", "管道", "输送带", "传送带"],
        "industries": ["智慧化工能源", "智慧电力"],
        "intent": "管廊与输送线状态巡检",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["测温型枪型摄像机", "防爆摄像机", "网络摄像机"],
        "camera_keywords": ["400万"],
        "require_price": False,
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },
    {
        "name": "气体泄漏监测",
        "capability_tags": ["气体探测"],
        "keys": ["可燃气体", "有毒气体", "气体泄漏", "气体探测"],
        "industries": ["智慧化工能源", "智慧电力", "智慧园区"],
        "intent": "可燃有毒气体泄漏报警",
        "level": "高",
        "bitrate": 0.0,
        "camera_classes": ["可燃气体探测报警系统"],
        "camera_keywords": [],
        "device_category": "气体探测设备",
        "degrade": "report",
    },

    # ---------------- 智慧水利 ----------------
    {
        "name": "水库河道",
        "keys": ["水库", "河道", "河湖", "水位", "大坝", "堤防", "灌区", "防溺水"],
        "industries": ["智慧水利"],
        "intent": "水域水位与岸线监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["水利相机", "行业球机产品", "网络摄像机"],
        "camera_keywords": ["球机", "400万"],
        "require_price": False,
        "device_category": "水利监测相机",
        "degrade": "fallback",
    },
    {
        "name": "泵站闸站",
        "keys": ["泵站", "闸站", "水闸", "排涝"],
        "industries": ["智慧水利"],
        "intent": "泵闸设施运行监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["水利相机", "网络摄像机"],
        "camera_keywords": ["400万"],
        "require_price": False,
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },

    # ---------------- 智慧金融 ----------------
    {
        "name": "现金区柜台",
        "keys": ["现金区", "柜台", "高柜", "营业厅"],
        "industries": ["智慧金融"],
        "intent": "现金业务区人员与操作监控",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },
    {
        "name": "ATM自助区",
        "keys": ["ATM", "自助银行", "自助设备"],
        "industries": ["智慧金融"],
        "intent": "自助区人员与尾随监控",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },
    {
        "name": "金库",
        "keys": ["金库", "保管箱"],
        "industries": ["智慧金融"],
        "intent": "金库双人双锁与出入监控",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["400万"],
        "device_category": "网络摄像机",
        "degrade": "report",
        "exclusive": True,
        "suppresses": ["现金区柜台"],
    },

    # ---------------- 智慧医疗 ----------------
    {
        "name": "门诊大厅",
        "keys": ["门诊", "挂号", "导医", "候诊"],
        "industries": ["智慧医疗"],
        "intent": "门诊人流与秩序监控",
        "level": "中",
        "bitrate": 2.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["半球", "400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
        "rag_family": "室内区域",
        "suppresses": ["室内区域"],
    },
    {
        "name": "药房库房",
        "keys": ["药房", "药库", "药品库"],
        "industries": ["智慧医疗", "零售连锁"],
        "intent": "药品存放与出入库监控",
        "level": "中",
        "bitrate": 2.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },
    {
        "name": "手术室ICU",
        "keys": ["手术室", "ICU", "重症", "洁净区"],
        "industries": ["智慧医疗"],
        "intent": "洁净区医疗过程留痕",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["400万"],
        "device_category": "网络摄像机",
        "degrade": "report",
        "exclusive": True,
    },

    # ---------------- 文博景区 ----------------
    {
        "name": "文物库房",
        "keys": ["文物", "藏品", "展柜", "库房"],
        "industries": ["文博景区"],
        "intent": "文物存放与展陈监控",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["400万"],
        "device_category": "网络摄像机",
        "degrade": "report",
        "exclusive": True,
    },
    {
        "name": "景区广场",
        "keys": ["景区", "游客中心", "景点"],
        "industries": ["文博景区"],
        "intent": "游客集散区秩序监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机", "分销球机"],
        "camera_keywords": ["枪型", "400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
        "rag_family": "公共区域",
    },
    {
        "name": "山林防火",
        "capability_tags": ["热成像测温", "烟火检测"],
        "keys": ["山林", "森林", "防火", "林区", "山火"],
        "industries": ["文博景区", "智慧水利", "智慧电力"],
        "intent": "林区火情早期探测",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["观测型双光谱球型摄像机", "观测型双光谱重型云台",
                           "网络摄像机"],
        "camera_keywords": ["400万"],
        "require_price": False,
        "device_category": "双光谱热成像",
        "degrade": "fallback",
    },

    # ---------------- 零售连锁 ----------------
    {
        "name": "门店收银",
        "keys": ["收银", "门店", "便利店", "连锁", "超市", "商超"],
        "industries": ["零售连锁"],
        "intent": "收银过程与顾客纠纷留痕",
        "level": "中",
        "bitrate": 2.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["半球", "400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
        "rag_family": "室内区域",
    },
    {
        "name": "卖场货架",
        "keys": ["卖场", "货架", "陈列"],
        "industries": ["零售连锁"],
        "intent": "卖场经营与损耗监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_classes": ["网络摄像机"],
        "camera_keywords": ["枪型", "400万"],
        "device_category": "网络摄像机",
        "degrade": "fallback",
    },

    # ---------------- 智慧校园 ----------------
    {
        "name": "实验室危化",
        "capability_tags": ["防爆"],
        "keys": ["实验室", "危化品", "化学品"],
        "industries": ["智慧校园"],
        "intent": "危化品存放与实验过程监控",
        "level": "高",
        "bitrate": 4.0,
        "camera_classes": ["防爆摄像机", "网络摄像机"],
        "camera_keywords": ["400万"],
        "device_category": "防爆摄像机",
        "degrade": "fallback",
    },
]

# 兜底场景：什么都没命中时用它，保证流程可继续并如实标注
FALLBACK_SCENE = "公共区域"

# 未填写的默认值，避免每个场景都重复写一遍
SCENE_DEFAULTS = {
    "industries": [],
    "level": "中",
    "bitrate": 4.0,
    "camera_keywords": [],
    "device_category": "网络摄像机",
    "require_price": True,
    "degrade": "fallback",
    "rag_family": None,
    "capability_tags": [],
    "suppresses": [],
    "fallback_only_with_lanes": False,
    "exclusive": False,
}


def scene(name: str) -> dict:
    """按名称取场景定义，并补齐默认字段。"""
    for raw in SCENES:
        if raw["name"] == name:
            return {**SCENE_DEFAULTS, **raw}
    raise KeyError(name)


def all_scenes() -> list[dict]:
    """返回补齐默认值后的完整场景表。"""
    return [{**SCENE_DEFAULTS, **raw} for raw in SCENES]


def industry_of(text: str) -> tuple[str, dict]:
    """按加权关键词判定行业，返回 (行业名, 明细)。

    没有任何命中时返回默认行业，并在明细里标 `fallback=True`，
    由上层决定是否把「行业未识别」写进缺口，而不是静默当成园区。
    """
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for name, spec in INDUSTRIES.items():
        total = 0
        found = []
        for key, weight in spec["keys"].items():
            if key in text:
                total += weight
                found.append(key)
        if total:
            scores[name] = total
            hits[name] = found
    if not scores:
        return DEFAULT_INDUSTRY, {"score": 0, "hits": [], "ranked": [], "fallback": True}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best = ranked[0][0]
    return best, {
        "score": scores[best],
        "hits": hits.get(best, []),
        "ranked": [{"industry": k, "score": v} for k, v in ranked],
        "fallback": False,
    }
