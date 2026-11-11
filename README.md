# Dahua-AI-solution

大华安防解决方案助手：输入一段自然语言项目需求，自动完成场景解析、设备选型、容量测算，并输出拓扑图与 Word 方案文档。

本地优先设计，全流程可离线降级运行；联网时可选接入大模型做需求理解与问答生成。

## 功能

- **Agent 工作台**：自然语言需求 → 场景归一化 → 缺口追问 → Spec 审阅 → 一键生成。基于 LangGraph 支持断点续跑与人工审阅。
- **确定性选型与测算**：设备型号、路数、带宽、存储容量由固定规则与公式计算，不由大模型产数字；需求原文可逐字回溯。
- **双模式拓扑图**：简略分层版（标注每个设备的使用地点与摄像机建设目标）+ 超详细逻辑连线版（场景摄像机 → 交换机 → NVR → 硬盘 → 平台 → 监视器）。
- **方案导出**：Word 方案（含设备表、测算口径与资料来源附录）、PNG 拓扑图。
- **知识库 / 智能问答 / 竞品对比**：本地检索 + 来源标注，支持混合检索与离线摘要。
- **MCP Server**：对外暴露知识库查询、存储测算、竞品对比等工具。

## 技术栈

Python 3.14 · FastAPI + uvicorn · SQLite · LangGraph（SqliteSaver 断点）· Pillow（拓扑渲染）· python-docx · 原生 JS/CSS 前端（无 CDN 依赖）。

## 快速启动

```bash
# Windows 下可直接双击 web.bat（自动选择 Python 3.14 并检查依赖）
pip install -r requirements.txt

cp .env.example .env      # 填入自己的 LLM Key；不填也能跑，会自动离线降级
python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

打开 `http://127.0.0.1:8080/agent` 进入工作台。

无网环境可在 `.env` 中设置 `APP_OFFLINE=1`，跳过所有联网探测，问答与同步立即走本地逻辑。

## 环境变量

| 变量 | 说明 |
|---|---|
| `APP_OFFLINE` | `1` = 声明离线，跳过联网探测 |
| `LLM_PROVIDER` | `deepseek` / `bailian` / `doubao` |
| `DEEPSEEK_API_KEY` | 大模型密钥，**仅放在 `.env`，切勿提交** |
| `DEEPSEEK_BASE_URL` | 兼容 OpenAI 的接口地址 |
| `DEEPSEEK_MODEL` | 模型名 |

`.env` 已被 `.gitignore` 排除；仓库中只有不含真实值的 `.env.example`。

## 关于数据（重要）

本仓库**只包含代码与表结构**，不含以下内容：

- `db/*.sqlite` 及其备份：产品库、竞品参数、方案库等采集数据（体积与数据合规原因）；
- `data/`：运行产物、导出的方案文档、抓取的原始页面、QA 截图、断点文件；
- `web/static/img/`：从厂商站点抓取的产品缩略图。代码已使用 `COALESCE(img_local, img_url)` 做在线回退，缺少本地图片不会导致启动失败。

首次运行可用 `config/init_db.py` 与 `db/schema.sql` 建立空表结构，但**空表并不足以让系统正常工作**：

实测干净 clone 后启动服务，五个页面（`/agent`、`/kb`、`/rag`、`/compare` 等）能正常打开，但 `/api/kb/stats` 直接返回 500、知识库/竞品/Agent 状态等接口会请求挂起。原因只是缺少 `db/dahua_kb.sqlite`，不是代码缺陷——把该文件（约 23 MB）放回 `db/` 目录后，全部接口立即恢复 200。

因此，在新环境部署时请从本地备份恢复 `db/dahua_kb.sqlite`，或按 `scraper/` 下的采集脚本重新采集。该文件因体积与数据合规原因不随仓库分发。

## 测试

```bash
python -m pytest tests/ -q
```

## 目录结构

```
app/         Spec 构建、测算、拓扑渲染、Word 导出、RAG、API
agent/       LangGraph 图、工具调用层、服务层
config/      配置与建库脚本
scraper/     公开数据采集脚本
scripts/     数据整理与离线自检脚本
tests/       单元测试
web/static/  前端页面与交互脚本
db/schema.sql 空表结构定义
```

## 说明

本项目为个人学习与技术验证用途。产品型号、参数与图片版权归各厂商所有；采集脚本仅访问公开页面并复用浏览器已登录会话，不绕过任何鉴权。请勿将内部资料、客户配置清单或真实凭证提交到仓库。
