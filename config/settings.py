"""全局配置：路径、目标行业、爬取礼貌参数。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "dahua_kb.sqlite"
RAW_DIR = BASE_DIR / "data" / "raw"

# 三个目标行业（URL 已实测可达）
INDUSTRIES = {
    "智慧园区": "https://www.dahuatech.com/cases/lists/1810.html",
    "智慧交管": "https://www.dahuatech.com/cases/lists/1764.html",
    "智慧校园": "https://www.dahuatech.com/cases/lists/1826.html?area=1827",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DahuaAssistantResearch/0.1"
REQUEST_DELAY = 1.5   # 每请求间隔秒数，礼貌爬取
TIMEOUT = 20
MAX_DETAIL_PER_INDUSTRY = 40   # 每个行业最多抓多少个详情页，W1 先控量
