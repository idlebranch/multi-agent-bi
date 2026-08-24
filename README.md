# Multi-Agent BI

这是一个面向业务数据问答的只读 Multi-Agent 系统。项目使用 LangGraph 编排 Schema Linking、SQL Writer、SQL Reviewer、Safety Validator、只读 Executor 与 Answer Formatter；FastAPI 提供接口，PostgreSQL 17 保存 Olist Brazilian E-Commerce 公开数据。

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

- Production Agent 只使用数据库角色 `agent_readonly`，数据库默认事务只读；
- 单条 `SELECT`/`WITH` 查询白名单，拒绝写操作、管理命令和多语句 SQL；
- Reviewer 业务口径检查、PostgreSQL `EXPLAIN`、statement timeout、最大返回行数与并发背压；
- Agent 状态字段、工具、路由和迭代次数由 `policies/agent_policy.json` 限制；
- 数据库文本按不可信输入处理，接口和网页不返回密钥、堆栈或原始供应商异常。

## 数据库

数据源为 [Olist Brazilian E-Commerce Public Dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，许可为 CC BY-NC-SA 4.0。PostgreSQL warehouse 由 `scripts/load_olist_postgres.py` 构建，包含 9 张基础表、7 张物化语义表和生产索引。`data/olist_semantic_model.json` 定义 GMV、平均客单价、按时送达率与复购等受控指标。

网页右上角的“数据库状态”会读取 `/health` 的实际诊断结果，展示 PostgreSQL 版本、数据库大小、只读状态、时间范围及主要表行数。这些统计由后端轻量只读查询并缓存，不在前端写死。

下载原始数据：

```powershell
New-Item -ItemType Directory -Force data/raw | Out-Null
Invoke-WebRequest `
  -Uri "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce" `
  -OutFile data/raw/olist.zip
```

Compose 的一次性 `loader` 会在空 warehouse 上加载数据；已初始化的 PostgreSQL volume 会直接复用，不会在 App 重启时重复 COPY。手动运行 loader 前需配置迁移账号的 `BI_MIGRATION_DATABASE_URL`。原始压缩包、数据库 volume 和 `.env` 不提交到 Git。Production Agent 的 `BI_DATABASE_URL` 必须使用 `agent_readonly`，不能使用迁移账号。

## Docker Compose 启动（推荐）

需要 Docker Desktop、Linux containers 和 Compose。先把 Olist ZIP 放在 `data/raw/olist.zip`，然后：

```powershell
cd C:\Users\10475\AI_PROJECT\multi_agent_bi
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

在 `.env` 中设置随机的 owner/readonly 密码，并配置 `DEEPSEEK_API_KEY`。Compose 内部通过服务名 `postgres:5432` 连接，不使用容器内的 `127.0.0.1`。不要把 `.env` 或密钥提交到仓库。

- 项目主页：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

查看 App 的非 root 身份：

```powershell
docker compose exec app id
```

## 本地 Python 启动（可选）

本地开发需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和一个已初始化的 PostgreSQL 17。

```powershell
Copy-Item .env.example .env
uv sync --locked
```

在 `.env` 中配置 `DEEPSEEK_API_KEY`、`BI_MIGRATION_DATABASE_URL` 和使用 `agent_readonly` 的 `BI_DATABASE_URL`。

```powershell
cd C:\Users\10475\AI_PROJECT\multi_agent_bi
uv sync --locked
uv run python scripts/load_olist_postgres.py --replace
uv run python api.py
```

- 项目主页：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

## Windows 图形启动器

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
uv run python benchmarks/run_benchmark.py --suite business
```

只测试模型连通性：

```powershell
uv run python scripts/smoke_test_deepseek.py
```

真实模型 benchmark 会产生 API 调用：

```powershell
uv run python benchmarks/run_benchmark.py --live-agent --suite business --case-id B001
```

Docker 验证：

```powershell
docker compose build
docker compose up -d
docker compose ps
docker compose exec app id
docker compose restart
```

## 数据库架构

Production runtime 只支持 PostgreSQL。`BI_DATABASE_URL` 是正式数据库连接，`BI_DATA_AS_OF_DATE` 可覆盖业务快照日期，`BI_SEMANTIC_MODEL` 可覆盖语义模型文件。

项目最初使用 SQLite 原型，完成 85/85 确定性跨数据库 parity 后迁移到 PostgreSQL。迁移前的 SQLite benchmark baseline 仍保留在 `benchmarks/results/`，但 SQLite 不再属于当前 Production runtime。
