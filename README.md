# Multi-Agent BI

这是一个面向业务数据问答的只读 Multi-Agent 系统。项目使用 LangGraph 编排 Schema Linking、SQL Writer、SQL Reviewer、Safety Validator、只读 Executor 与 Answer Formatter；FastAPI 提供接口，SQLite 保存 Olist Brazilian E-Commerce 公开数据。

网页只提供一个 `Production` Agent。原实验恢复图保留为 legacy 代码，不再由网页、API、命令行或启动器公开调用。Production 工作流包含有限 SQL 修复：Reviewer、Safety 或 Executor 返回可修复问题时会重新进入 SQL Writer，最多修复 2 次，之后安全终止。

## 架构与安全边界

```text
用户问题
  → 输入防护
  → Schema Linking
  → SQL Writer
  → SQL Reviewer
  → 必要时有限修复（最多 2 次）
  → SQL Safety Validator
  → Read-only Executor
  → Analyst / Answer Formatter
```

系统保留以下安全措施：

- SQLite URI 只读连接和 `PRAGMA query_only=ON`；
- 单条 `SELECT`/`WITH` 查询白名单，拒绝写操作、危险 PRAGMA、ATTACH 和多语句 SQL；
- Reviewer 业务口径检查、SQLite `EXPLAIN`、执行超时、最大返回行数与并发背压；
- Agent 状态字段、工具、路由和迭代次数由 `policies/agent_policy.json` 限制；
- 数据库文本按不可信输入处理，接口和网页不返回密钥、堆栈或原始供应商异常。

## 数据库

数据源为 [Olist Brazilian E-Commerce Public Dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，许可为 CC BY-NC-SA 4.0。本机数据库由 `scripts/load_olist.py` 构建，包含原始业务表、索引和 7 个 BI 语义表。`data/olist_semantic_model.json` 定义 GMV、平均客单价、按时送达率与复购等受控指标。

网页右上角的“数据库状态”会读取 `/health` 的实际诊断结果，展示文件大小、只读状态、完整性、外键异常、时间范围及主要表行数。这些统计由后端轻量查询并缓存，不在前端写死。

如需重新下载并构建：

```powershell
New-Item -ItemType Directory -Force data/raw | Out-Null
Invoke-WebRequest `
  -Uri "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce" `
  -OutFile data/raw/olist.zip
uv run python scripts/load_olist.py --replace
```

数据库文件、原始压缩包和 `.env` 不提交到 Git。其他开发者可通过 ETL 脚本重建同一数据结构。

## 首次安装

需要 Windows、Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```powershell
cd C:\Users\10475\AI_PROJECT\multi_agent_bi
uv sync --locked
Copy-Item .env.example .env
```

在 `.env` 中配置 `DEEPSEEK_API_KEY`。不要把 `.env` 或密钥提交到仓库。

## 启动方式一：PowerShell

```powershell
cd C:\Users\10475\AI_PROJECT\multi_agent_bi
uv sync --locked
uv run python api.py
```

- 项目主页：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

## 启动方式二：Windows 图形启动器

先构建一次轻量 EXE：

```powershell
cd C:\Users\10475\AI_PROJECT\multi_agent_bi
.\build_launcher.cmd
```

然后双击 `dist\MultiAgentBI-Launcher.exe`，点击“启动项目”。启动器会在后台执行等价于 `uv run python api.py` 的命令，不显示终端；健康检查通过后自动打开网页。启动器在创建 Tk 窗口前启用 Windows Per-Monitor DPI Awareness V2，并使用原生 Microsoft YaHei UI/Segoe UI 字体，适配高 DPI 显示缩放。

启动器支持启动、停止、打开网页、打开 API 文档、健康检查和打开日志目录。关闭 GUI 不会停止服务，只有点击“停止项目”才会在核对 PID 与项目路径后终止服务进程树。

创建当前用户桌面快捷方式：

```powershell
powershell -ExecutionPolicy Bypass -File .\create_desktop_shortcut.ps1
```

- EXE：`C:\Users\10475\AI_PROJECT\multi_agent_bi\dist\MultiAgentBI-Launcher.exe`
- 快捷方式：当前 Windows 用户桌面的 `Multi-Agent BI.lnk`
- 日志：`logs\launcher.log` 与 `logs\launcher_server.log`

EXE 必须保留在项目的 `dist` 目录中。数据库和虚拟环境不会被重复打包进 EXE。

## 网页演示

输入问题后，左侧依次展示最终回答、运行摘要、SQL、查询结果和运行详情；右侧始终展示结构化 Agent 时间线。

网页的“调试模式”默认关闭，开关状态保存在浏览器 `localStorage`，无需重启后端。开启后可展开原始 Trace、路由历史、handoff、策略决策与完整运行状态，并可复制 Trace 或运行 JSON。普通网页测试不需要输入 `/trace on`。

“测试案例”菜单内置常规查询、复杂分析、边界测试和安全测试。完整人工验收步骤见 [docs/manual_test_checklist.md](docs/manual_test_checklist.md)。

## 命令行人工测试

```powershell
# 交互模式
uv run python scripts/manual_test.py

# 单问题模式
uv run python scripts/manual_test.py "已签收订单的平均客单价是多少？"

# 输出完整节点 Trace
uv run python scripts/manual_test.py --trace "按月统计2017年已签收商品GMV"
```

交互命令包括 `/status`、`/examples`、`/trace on`、`/trace off` 和 `/quit`。命令行与网页都只调用 Production Agent。

## 测试

```powershell
uv sync --locked
uv run pytest -q tests
uv run ruff check api.py launcher.pyw src tests scripts
uv run python scripts/audit_olist.py
uv run python scripts/run_olist_golden.py
uv run python scripts/run_olist_golden.py --cases data/olist_advanced_queries.json
```

只测试模型连通性：

```powershell
uv run python scripts/smoke_test_deepseek.py
```

真实模型批量测试会产生 API 调用：

```powershell
uv run python scripts/batch_test_live.py
```

数据库与 Agent 性能测试：

```powershell
uv run python scripts/performance_test.py --mode db
uv run python scripts/performance_test.py --mode live --live-levels 1 --live-base-requests 12
```

## 数据库切换与扩展

数据库优先级为：`BI_DB_PATH` 环境变量、`data/active_dataset.json`、`data/mock_db.sqlite`。可用 `BI_DATA_AS_OF_DATE` 指定业务快照日期，用 `BI_SEMANTIC_MODEL` 指定语义模型。

当前 SQLite 适合本地面试演示。进入千万到亿级明细后，可保持 Agent 与语义层接口不变，将执行适配器替换为 DuckDB、PostgreSQL 或云数仓，并继续使用物化汇总表、指标治理与 golden SQL 回归。
