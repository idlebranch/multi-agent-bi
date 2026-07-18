# Multi-Agent BI Query System

一个基于 LangGraph 的只读 BI 查询系统。当前已接入 Olist Brazilian E-Commerce 真实公开数据，并用独立 Agent 分工完成表选择、SQL 生成、SQL 业务审核和结果解读；路由、安全校验及数据库执行保持为确定性代码。

## Agent 工作流

```text
用户问题
  -> Catalog Agent：从全库目录选择相关表、字段和 JOIN 键
  -> SQL Writer Agent：生成一条只读 SQLite 查询
  -> SQL Reviewer Agent：独立审核业务口径、JOIN、聚合和日期范围
  -> SQL Safety Validator：只读白名单 + SQLite EXPLAIN
  -> Read-only Executor：只读连接、查询超时、结果行数上限
  -> Analyst Agent：根据真实查询结果生成中文答案
```

- `v1`：默认稳定版，所有状态转移由代码决定。
- `v2`：实验恢复版，仅在可恢复错误时让 LLM 建议重试或结束。

## Agent 协作治理

Agent 不直接自由对话，而是通过受控共享状态交接。`policies/agent_policy.json` 是默认拒绝的策略源，规定每个角色可读取的状态字段、可写入的字段、可调用工具、合法状态转换、资源上限和需要人工审批的高风险动作。

- `src/contracts.py`：Catalog、Reviewer、SQL 尝试、handoff 和策略决定的 Pydantic 严格协议，拒绝额外字段；
- `src/policy.py`：最小上下文投影、工具白名单、状态字段权限、转换白名单和策略版本；
- `src/guardrails.py`：直接提示注入检测、数据库文本隔离、控制字符清理、长度限制和密钥脱敏；
- `src/semantic_rules.py`：指标识别、指标—视图适用矩阵、Reviewer 冲突消解和确定性 SQL 语义检查；
- `src/workflow.py`：所有节点的统一策略包装器，越权更新会被拒绝并安全终止；
- API 返回 `run_id`、`handoff_history` 和 `policy_decisions`，页面可查看每次 Agent 交接及策略判定。

稳定流程只允许以下逻辑转换：

```text
start -> Catalog -> Writer -> Reviewer -> Validator -> Executor -> Analyst
                      ^          |            |          |
                      +----------+------------+----------+
                         审核、校验或执行失败时有界重写
```

所有用户问题、外部 Schema 和数据库文本都按不可信数据处理。Olist 评价中的疑似间接提示注入会在传给 Analyst 前隔离；前端对查询结果、trace 和路由内容统一进行 HTML 转义。当前系统没有写数据库或外部副作用工具，策略文件仍预先声明写入、批量导出、敏感字段和外部副作用必须人工审批，未实现审批处理器前默认拒绝开放这些能力。

## 当前真实数据库

数据源是 [Olist Brazilian E-Commerce Public Dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，许可为 CC BY-NC-SA 4.0。当前构建结果：

- 99,441 个订单、112,650 个订单商品行、103,886 条支付、99,224 条评价；
- 1,000,163 条原始地理记录被聚合为 19,015 个邮编前缀，避免 JOIN 放大；
- 订单日期为 2016-09-04 至 2018-10-17；
- SQLite 文件约 243 MB；增加的空间用于保存预计算结果，换取稳定的并发查询性能；
- 七个物化 BI 语义表：三个明细/订单粒度表，以及类别 GMV、配送 KPI、支付方式、复购客户四个汇总表；
- 主键、外键以及订单日期、状态、商品、卖家、支付方式等常用字段均已建立索引。

`data/olist_semantic_model.json` 定义了 GMV、客单价、按时送达率和复购客户等受控口径。Agent 会同时看到表结构和这些业务定义。
对已登记指标，Catalog 会优先选择粒度正确的语义表；Reviewer 的模型意见还会经过确定性规则复核，不能擅自增加用户未要求的状态或日期条件。默认工作流预算为 12 次节点迭代。

## 首次安装

```powershell
uv sync --locked
Copy-Item .env.example .env
# 在 .env 中填写 DEEPSEEK_API_KEY
```

下载并构建 Olist：

```powershell
New-Item -ItemType Directory -Force data/raw | Out-Null
Invoke-WebRequest `
  -Uri "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce" `
  -OutFile data/raw/olist.zip
uv run python scripts/load_olist.py --replace
```

导入器直接流式读取压缩包，不落地九个 CSV。它先构建临时数据库，完成外键检查、索引、物化语义表和 `ANALYZE` 后才原子切换，并生成 `data/active_dataset.json`。

## 测试

数据质量审计：

```powershell
uv run python scripts/audit_olist.py
```

八个固定 BI 问题及标准 SQL 回归：

```powershell
uv run python scripts/run_olist_golden.py
```

完整离线测试（不会调用 DeepSeek），覆盖正常查询、角色越权、非法跳转、直接/间接提示注入、密钥脱敏与前端转义：

```powershell
uv run python -m unittest discover -s tests -v
uv run ruff check src api.py tests scripts
```

只测试模型连通性：

```powershell
uv run python scripts/smoke_test_deepseek.py
```

使用真实 DeepSeek 对全部黄金问题做端到端批量测试，并生成本地 JSON 报告：

```powershell
uv run python scripts/batch_test_live.py
```

### 高难度与性能测试

12 条高难度标准 SQL（窗口函数、多维 JOIN、环比、占比、P99）回归：

```powershell
uv run python scripts/run_olist_golden.py --cases data/olist_advanced_queries.json
```

数据库阶梯压测（并发 1 到 64，保持生产 5 秒超时）：

```powershell
uv run python scripts/performance_test.py --mode db
```

真实 DeepSeek 高难度 Agent 基线：

```powershell
uv run python scripts/performance_test.py --mode live --live-levels 1 --live-base-requests 12
```

真实 DeepSeek 并发压测会产生 API 调用费用，建议逐级运行并保存独立报告：

```powershell
uv run python scripts/performance_test.py --mode live `
  --cases data/olist_golden_queries.json `
  --live-levels 2,4,8,16
```

当前推荐将 Agent 常规并发控制在 16，突发并发最多 32。数据库执行器默认只允许 4 条 SQL 同时运行，并最多排队 10 秒；超过容量会返回结构化 `queue_timeout`，避免大量重查询互相争抢并全部拖到查询超时。可通过 `.env` 中的 `BI_DB_MAX_CONCURRENCY` 和 `BI_DB_QUEUE_TIMEOUT_SECONDS` 调整，但应先重新压测。完整优化结果见 `reports/performance_optimization_results.md`。

## 运行

### 一键人工测试

Windows 下可以直接双击项目根目录中的：

- `start_web_test.cmd`：启动 API 并自动打开网页；
- `start_cli_test.cmd`：进入终端人工测试台，可以查看答案、SQL、结果、Agent 交接、Reviewer 意见和策略拒绝。

也可以直接使用命令行：

```powershell
# 交互模式
uv run python scripts/manual_test.py

# 单问题模式
uv run python scripts/manual_test.py "已签收订单的平均客单价是多少？"

# 显示完整节点 trace
uv run python scripts/manual_test.py --trace "按月统计已签收商品 GMV"
```

终端测试台支持 `/examples`、`/v1`、`/v2`、`/trace on`、`/status` 和 `/quit`。

### 常规启动

```powershell
uv run python api.py
```

- 页面：`http://127.0.0.1:8000`
- 接口文档：`http://127.0.0.1:8000/docs`
- 健康状态：`http://127.0.0.1:8000/health`，会显示当前数据集、数据库文件大小和业务快照日期。

## 切换数据库

默认优先级为：

1. `BI_DB_PATH` 环境变量；
2. `data/active_dataset.json`；
3. `data/mock_db.sqlite`。

临时切换到其他 SQLite：

```text
BI_DB_PATH=D:/data/another.sqlite
BI_DATA_AS_OF_DATE=2026-07-17
BI_SEMANTIC_MODEL=D:/data/another_semantic_model.json
```

项目不会提交原始压缩包、生成的 SQLite 或活动清单。代码、语义模型、黄金 SQL 和 ETL 脚本可提交，其他开发者可以重复构建相同数据库。

## 大数据边界

当前执行层已经具备只读连接、单语句白名单、查询超时、并发背压、排队超时、结构化错误、最大返回行数、紧凑 Catalog 和按需 Schema 展开。Olist 规模适合本地 SQLite 演示；如果后续进入千万至亿级明细，建议保持 Agent/语义层接口不变，将执行适配器替换为 DuckDB、PostgreSQL 或云数仓，并继续保留受控指标、物化汇总表与黄金问题回归集。
