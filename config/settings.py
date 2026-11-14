"""全局配置：路径、目标行业、爬取礼貌参数。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "dahua_kb.sqlite"
RAW_DIR = BASE_DIR / "data" / "raw"

# 官方方案资料采集范围。先覆盖售前最常遇到的城市、园区、交通、能源、
# 物流、金融和医疗等主行业；列表页公开可访问，不触碰需要登录的云商配单接口。
INDUSTRIES = {
    "智慧城市": "https://www.dahuatech.com/cases/lists/1866.html?belong=0",
    "智慧交管": "https://www.dahuatech.com/cases/lists/1764.html?belong=0",
    "智慧交通": "https://www.dahuatech.com/cases/lists/1770.html?belong=0",
    "智慧应急": "https://www.dahuatech.com/cases/lists/1787.html?belong=0",
    "城市停车": "https://www.dahuatech.com/cases/lists/1786.html?belong=0",
    "智慧园区": "https://www.dahuatech.com/cases/lists/1810.html",
    "智慧校园": "https://www.dahuatech.com/cases/lists/1826.html?area=1827",
    "智慧制造": "https://www.dahuatech.com/cases/lists/1801.html?belong=1",
    "智慧物流": "https://www.dahuatech.com/cases/lists/1818.html?belong=1",
    "智慧能源": "https://www.dahuatech.com/cases/lists/1831.html?belong=1",
    "智慧金融": "https://www.dahuatech.com/cases/lists/1829.html?belong=1",
    "智慧文旅": "https://www.dahuatech.com/cases/lists/1836.html?belong=1",
    "智慧医疗": "https://www.dahuatech.com/cases/lists/1841.html?belong=1",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DahuaAssistantResearch/0.1"
REQUEST_DELAY = 1.5   # 每请求间隔秒数，礼貌爬取
TIMEOUT = 20
MAX_DETAIL_PER_INDUSTRY = 20   # 每个行业最多抓 20 个详情页，避免一次采集过重
