# GitHub 发布清单

## 可公开

- 应用代码、测试、数据库表结构和 CI 配置；
- 合成案例 `data/benchmark_cases.json`；
- 公开的工程规则、数据分层原则和开发文档；
- 采集脚本本身，但不包含采集结果、登录态或浏览器导出文件。

## 不公开

- `.env`、密钥、Cookie、Token、账号密码；
- `db/` 下 SQLite 数据库及所有备份；
- `data/` 下的原始页面、方案导出、截图、检查点、日志和运行数据库；
- 客户招标文件、点位表、网络拓扑、报价、成本、毛利和审批记录；
- 任何内部系统导出的 BOM、渠道价或登录后资料。

## 推送前检查

```bash
git status --short
git diff --cached --stat
git check-ignore -v .env db/dahua_kb.sqlite data/exports/example.docx
```

确认暂存区只包含代码、测试、文档和合成样例后，再提交和推送。不要使用 `git add .` 代替检查。

## 当前建议的首次提交范围

建议先提交：

1. `app/`、`agent/`、`config/`、`scraper/`、`scripts/`；
2. `tests/`、`web/`、`db/schema.sql`、`.env.example`；
3. `README.md`、`docs/`、`.github/`；
4. `data/benchmark_cases.json`。

暂不提交任何数据库、导出文件、原始抓取资料和本地交接文件。
