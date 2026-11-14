# 大华 AI 解决方案助手

面向安防售前方案工程师的本地 Web 工具。输入自然语言项目需求后，系统完成需求解析、场景拆解、设备选型、存储与网络测算、工程审查，并导出方案文档和设备清单。

项目强调“确定性计算 + 可追溯选型”：大模型只负责辅助理解和问答，点位、码流、容量、带宽、设备数量等关键数字由规则和公式计算。

## 功能

- 自然语言需求解析与场景归一化；
- 卡口、电警、违停抓拍、事件检测、防爆、测温等行业场景识别；
- 产品库、方案库、案例库和竞品事实检索；
- 摄像机、NVR、硬盘、交换机、平台和显示设备选型；
- 存储容量、录像天数、NVR 通道、交换机端口和网络带宽测算；
- 工程审查：缺口、风险、证据、平台接口和人工确认项；
- Word、Excel、PPT、拓扑图、技术偏离表和方案 ZIP 导出；
- Agent 工作流断点续跑、人工审阅和历史会话；
- FastAPI 接口与 MCP Server。

## 系统架构

```text
浏览器 Web 工作台
        │
        ▼
FastAPI API 层（app/api.py）
        │
        ├── Agent 编排层（agent/）
        │     ├── 需求解析 / 缺口追问
        │     ├── 人工审阅 / 断点续跑
        │     └── 工具调用与问答
        │
        ├── 方案构建层（app/builder.py）
        │     ├── 行业与场景分类（app/taxonomy.py）
        │     ├── 产品选型（app/selection.py）
        │     ├── 计算与校验（app/calc.py、engineering_checks.py）
        │     └── 方案、报价、偏离表导出
        │
        ├── 知识检索层（app/rag/、app/kb.py）
        │     ├── 产品 / 方案 / 案例 / 竞品事实
        │     └── SQLite + 本地 RAG 索引
        │
        └── 数据采集层（scraper/）
              公开产品、案例和方案资料采集与清洗
```

## 技术栈

- Python 3.12+；
- FastAPI + Uvicorn：Web API 与静态页面服务；
- SQLite：产品、方案、案例、竞品事实和会话存储；
- Pydantic：结构化数据模型与校验；
- LangGraph + SQLite Checkpoint：Agent 编排、人工介入和断点续跑；
- Pillow：拓扑图绘制；
- python-docx：Word 方案导出；
- openpyxl：Excel 清单、容量表和工程审查表；
- python-pptx：PPT 导出；
- FastMCP：MCP 工具服务；
- 原生 HTML / CSS / JavaScript：前端界面，无 CDN 依赖。

完整依赖版本见 [requirements.txt](requirements.txt)。

## 快速启动

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python config/init_db.py
python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

打开 <http://127.0.0.1:8080/agent>。

也可以直接运行 `web.bat`。

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python config/init_db.py
python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

没有大模型密钥时，确定性方案流程仍可运行。离线开发可在 `.env` 中设置：

```dotenv
APP_OFFLINE=1
```

## 配置项

`.env.example` 提供完整配置模板：

| 配置 | 作用 |
|---|---|
| `APP_OFFLINE` | 是否跳过联网探测和在线模型调用 |
| `LLM_PROVIDER` | 模型提供方：`deepseek`、`bailian`、`doubao` |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | OpenAI 兼容接口地址 |
| `DEEPSEEK_MODEL` | 模型名称 |
| `BAILIAN_API_KEY` | 百炼备用模型密钥 |

## 常用接口

启动服务后可访问：

- `/agent`：Agent 方案工作台；
- `/kb`：产品、方案、案例知识库；
- `/rag`：本地检索与问答；
- `/compare`：大华与海康产品对比；
- `/api/health`：健康检查；
- `/docs`：FastAPI 自动生成的接口文档。

## 测试

```bash
python -m pytest tests/ -q
python scripts/benchmark_cases.py
```

测试包含 API、Agent、计算、选型、RAG、导出、会话和前端静态资源检查。合成基准案例覆盖校园、工业园区和城市交管三个方向。

## 目录结构

```text
app/          方案构建、计算、校验、导出、RAG 与 API
agent/        LangGraph Agent、解析器和工具调用层
config/       配置与数据库初始化
scraper/      公开资料采集、清洗与导入脚本
scripts/      离线检查、基准案例和维护脚本
tests/        单元测试与接口测试
web/static/   前端页面、样式和交互脚本
db/schema.sql SQLite 数据库结构
docs/         工程规则和发布说明
```

## 运行说明

`db/schema.sql` 和 `config/init_db.py` 用于初始化空数据库。空数据库可以启动应用和运行结构测试；要使用完整产品、方案、案例和竞品检索，需要另外准备本地知识库数据。

本项目用于安防售前方案方法验证和个人学习。自动生成的结果属于方案草稿，正式项目仍需结合厂家彩页、现场勘察、平台联调、商务询价和内部审批进行复核。
