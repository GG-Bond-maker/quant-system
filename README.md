# AQP · A股智能量化预测与分析平台

> Alpha Quant Platform —— 以机器学习预测为核心，融合传统量化选股、技术分析、资金面与基本面研究的 A 股智能研究平台。
> 本项目为教学研究工具，**所有预测与解读不构成任何投资建议**；市场有风险，投资需谨慎。

## 项目简介

AQP 在普通笔记本硬件（CPU-only，32GB 内存）上打通完整研究闭环：

| 层 | 能力 | 技术栈 |
| --- | --- | --- |
| 数据层 | AKShare 采集（限速/重试/双源降级）、按【年份+标的】分区的 Parquet 列式仓库 | Polars · PyArrow · AKShare |
| 算法层 | 复权 / 涨跌停 / 绩效指标 / K线形态 等纯函数域层；42 列基础因子；LightGBM 未来 N 日收益率预测 + TreeSHAP 因子解释 | LightGBM · NumPy · pandas |
| 服务层 | 统一响应 `{code,message,data,trace_id,ts}` 的 REST API，Redis 熔断降级缓存，SQLite WAL | FastAPI · SQLAlchemy 2 async · Redis |
| 回测层 | Top-K 等权撮合引擎：T+1 / 涨跌停 / 停牌 / 整手 / 最低佣金 / 印花税，全部闸门可观测 | 自研（CPU 友好） |
| 前端 | 市场概览 / 个股详情（专业 K 线 + ML 预测面板） | React 18 · Vite · TS · Tailwind · Lightweight Charts · ECharts |

## 目录结构

```
Alpha Quant Platform/
├── backend/
│   ├── app/
│   │   ├── core/        # 配置 / 日志 / 统一响应 / 异常
│   │   ├── db/          # SQLAlchemy async + 8 张表 + WAL 初始化
│   │   ├── cache/       # Redis 客户端（熔断降级）+ 进程内 LRU
│   │   ├── domain/      # 纯函数：日历/复权/涨跌停/绩效/技术指标
│   │   ├── data/        # Parquet 分区仓库 + AKShare 适配器 + 日历数据层 + 初始化 CLI
│   │   ├── backtest/    # 撮合引擎（T+1/涨跌停/停牌/手数/费用）+ Top-K 回测
│   │   ├── ml/          # 因子提取(hfq asof 基准) / 三段切分训练 / 推理 / TreeSHAP
│   │   ├── api/v1/      # market / stock 路由
│   │   └── main.py
│   ├── scripts/         # bootstrap / update_daily / build_features / train / infer
│   ├── tests/           # pytest 68 条（域纯度守卫 + 回测 TC + 防泄漏 + API 端到端）
│   └── requirements.txt
├── frontend/            # React + Vite + TS + Tailwind
├── scripts/             # 数据维护脚本（bootstrap / 流水线等）
├── data/                # parquet / sqlite / models（运行时生成）
└── docs/                # 项目设计与开发文档
```

## 环境准备

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11.x | 必需 |
| Node.js | 20 LTS+ | 前端构建 |
| Redis | 7.x（可选） | 不可用时自动降级进程内 LRU 缓存，无需强制安装 |

**后端依赖安装**（Windows PowerShell 示例）：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**前端依赖安装**：

```bash
cd frontend
npm install
```

## 数据初始化

```bash
cd backend
python scripts/bootstrap.py                 # 环境检查 + SQLite(WAL) + 目录初始化
python -m app.data.ingest                   # 交易日历 + 证券列表 + 示例标的日线（双复权口径）
```

常用变体（daily 阶段同时写入 daily_bar 与 daily_bar_hfq，后者是特征计算基准）：

常用变体：

```bash
python -m app.data.ingest --stage calendar                  # 仅交易日历
python -m app.data.ingest --stage daily --codes 600519,000001 --days 400
python -m app.data.ingest --stage daily --codes 600519 --start 2023-01-01 --end 2024-12-31
```

- 行情主源为东方财富，失败自动降级新浪；按年分区原子写入 `data/parquet/`；
- 如需运行完整三段训练（--holdout 252 --test-days 252），建议回看 2022 年至今；
- 环境变量模板见 `.env.example`；`AQP_CPU_THREADS` 可统一控制 Polars/LightGBM 线程
  （默认 min(逻辑核-2, 12)，避免嵌套并行争用）。

## 本地运行

**手动启动**（两个终端；详细小白版教程见 [run.md](run.md)）：

```bash
# 终端 1：后端
cd backend && .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2：前端
cd frontend && npm run dev
```

- 首次打开前端会要求登录，Token 取项目根目录 `.env` 中的 `ADMIN_TOKEN`（未创建 `.env` 见 run.md 第五步）；
- 停止：到对应终端按 `Ctrl+C`。

访问地址：

- 前端：<http://127.0.0.1:5173>
- Swagger API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

## 核心接口速览

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/market/overview` | 指数 / 涨跌分布 / 资金 / 异动 / ML 推荐榜（Redis 缓存 300s） |
| `GET /api/v1/stock/search?q=茅台` | 代码/名称模糊搜索 |
| `GET /api/v1/stock/{symbol}/profile` | 个股档案 + 最新行情 |
| `GET /api/v1/stock/{symbol}/kline?adjust=none&start=...&end=...` | K 线 + MA/MACD/RSI/BOLL |
| `GET /api/v1/stock/{symbol}/predict` | ML 预测收益率 / 置信度 / Top5 SHAP 因子贡献 |

## 测试

```bash
cd backend
.venv/Scripts/python -m pytest          # 68 条：域纯度守卫 / 回测 7 TC / 防泄漏 / ML / API 端到端
```

前端类型检查与构建：

```bash
cd frontend
npm run build        # tsc 严格模式 + vite 产物构建
```

## 机器学习流水线（已脚本化）

```bash
cd backend
python scripts/update_daily.py --codes 600519,000001,300750 --start 2022-01-01
python scripts/build_features.py                                    # hfq 基准因子（asof 稳定）
python scripts/train.py --horizon 5 --holdout 252 --test-days 252   # train/gap/valid/gap/test 三段
python scripts/infer.py                                             # 生成 predictions/date=*.parquet
```

防泄漏设计：特征筛选只用 train 段；gap ≥ horizon 隔离 Label 窗口；test 段仅做最终评估、
代码路径上无法参与早停；特征基于 hfq 价格具备 asof 稳定性（`tests/test_feature_asof.py` 守卫）。
训练完成后 GET /api/v1/stock/{symbol}/predict 即可用真实模型预测。

## 常见问题

- **Redis 未启动？** 后端自动降级进程内 LRU 缓存，`/health` 返回 `redis: degraded`，功能不受影响；
- **东方财富接口断连 / 限流？** 行情拉取自动降级新浪源；AKShare 限速 1.2s + 随机抖动内置；
- **K 线接口返回 `code=51001`？** 对应复权口径（`daily_bar_qfq` 等）尚未拉取，先运行数据初始化；
- **PowerShell 无法激活 venv？** `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

## Docker 部署（P0 最小）

```bash
docker compose config          # 校验编排
docker compose up -d --build   # 三服务：redis / aqp-api / aqp-web(Nginx 反代 API)
# 访问 http://127.0.0.1:8080（SPA + /api 反代后端）
```

## 认证与权限（P3）

```bash
python scripts/create_admin.py admin your_password    # 创建管理员
```

登录获取 JWT：
```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login   -H "Content-Type: application/json"   -d '{"username":"admin","password":"your_password"}'
```

角色权限：
| 角色 | 权限 |
|---|---|
| viewer | 只读（行情/个股/预测/选股） |
| researcher | + 回测/训练/推理/数据更新 |
| admin | 全部权限 |

## 监控与告警（P3）

- `GET /metrics` — Prometheus exposition format（API 请求数/耗时/错误 + Redis + Pipeline + ML 指标）
- `GET /health/ready` / `GET /health/live` — K8s readiness/liveness 探针
- `python scripts/alerter.py` — 检查 pipeline FAILED / Redis circuit / 通知（Webhook，需 `NOTIFY_ENABLED=true` + `NOTIFY_WEBHOOK_URL`）
- 去重：同一 alert key 300s 内不重复通知

## 备份与恢复（P3）

```bash
python scripts/backup.py          # 备份 data/sqlite + data/models → backup/aqp-*.tar.gz (+ .sha256)
python scripts/restore.py --file backup/aqp-*.tar.gz   # 恢复（先备份当前数据再还原）
python scripts/backup_drill.py    # 恢复演练（创建→删除→恢复→验证 SQLite 数据完整性）
```

定时备份（Linux cron）：`10 2 * * * cd /path/to/AQP/backend && python scripts/backup.py`

## CI / Nightly E2E

- `.github/workflows/ci.yml` — push/PR 触发：pytest + mypy + ruff + frontend build
- `.github/workflows/nightly.yml` — 每日 02:00 UTC：E2E（Redis + FastAPI + Frontend + Playwright 截图）
- CI 环境使用 SQLite + 合成数据 + ephemeral Redis，不依赖真实 AKShare

## 未实现（未来展望）

以下为项目文档定义但 P3 明确暂缓项：
- PWA / IndexedDB Offline（P3-2）
- Redis Sentinel + 多节点（P3-4）
- PostgreSQL 迁移（P3-6）
- Celery / MongoDB / SSE 异步任务队列（P3-8）

## 合规声明

本平台展示的所有行情、预测、解读与回测结果仅用于学习与研究，不构成任何投资建议，不承诺任何收益。模型基于历史数据训练，样本外可能失效；AI 解读可能存在幻觉。使用者应对自身决策独立承担全部风险。
