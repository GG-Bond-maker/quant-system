# 市场概览与 ETF 性能修复报告

**日期：** 2026-09-14  
**范围：** `/api/v1/market/overview*`、`/api/v1/etf/{overview,performance,scale,detail}`

## 根因

1. **市场概览冷路径混合了实时外部抓取和日频本地计算。** 兼容端点 `/market/overview` 直接同步聚合，实时子项之间存在串行等待；Redis 缓存缺失或不可用时，请求会跟随外部源的重试而长时间卡住。
2. **ETF 目录冷读采用 100 条/页的串行分页。** 约 1,383 只 ETF 需要约 14 次东财 HTTP 请求；每次又沿用默认 `12s × 3` 重试策略。`build_catalog()` 被 overview、performance、scale、detail 多次调用，因此目录冷路径是主要放大器。
3. **ETF K 线读取也按标的串行执行。** performance 最多 8 个 symbol、scale 最多 30 个 top-N 标的，原实现逐个调用 `fetch_kline`；detail 虽对块并发，但每块没有统一响应预算。

## 修复

### 市场概览

- 前端已使用 `/overview/rt` 与 `/overview/daily` 并发加载；后端保持该切块协议。
- 实时块改为最多 3 个 worker 的受限并发（指数、资金、异动），不再在请求路径串行等待独立 IO。
- 日频块继续只读本地 Parquet/预测结果，并在响应中提供 `data_freshness`。
- 旧 `/overview` 仍保持原字段兼容，但实时、日频两块并发构建，并设置 6 秒冷路径预算；超时返回各块 `status: unavailable` 与 `data_freshness.status: degraded`，不填造行情。
- `/overview/rt`、`/overview/daily` 均设置 5 秒预算；缓存命中、SWR 影子键和 Redis/LRU 降级路径保持原有行为。

### ETF

- 东财全量目录由 100 条串行分页改为最多 **两次** 2,000 条请求；ETF 交互 HTTP 单源改为 4 秒、1 次尝试。
- performance 与 scale 的互不依赖 K 线读取使用最多 4 个 worker 并行，避免 N 个符号的串行网络等待。
- 新增按数据日与完整请求参数隔离的 SWR 缓存键：overview、performance、scale、detail；TTL 300 秒、stale 窗口 1,800 秒，Redis 不可用时复用已有 LRU 兜底。
- overview/performance/scale 的冷路径预算为 4.5 秒；detail 的单块预算为 4 秒。源不可达时返回既有 `status: unavailable/degraded` 空态和 `data_freshness`，不返回伪造价格、规模或曲线。

## 实测基准

### 修复前（审计输入）

| 端点 | 首次耗时 |
|---|---:|
| `/market/overview` | 35–40 分钟（Redis 关闭时约 45 秒无响应） |
| `/etf/overview` | 82.8 秒 |
| `/etf/performance` | 80 秒 |
| `/etf/scale` | 59.7 秒 |
| `/etf/detail/510300` | 40 秒 |

### 修复后（127.0.0.1:8123 临时 Uvicorn，`REDIS_ENABLED=false`，服务已停止）

| 端点 | 第一次（冷应用/LRU） | 第二次（LRU/SWR） | 结果 |
|---|---:|---:|---|
| `/market/overview?refresh=1` | 6.034s | `/market/overview`: 0.009s | 外部实时源不可达时 6 秒结构化降级；不再 45 秒悬挂 |
| `/etf/overview` | 4.520s | 0.014s | 冷路径快速降级，热缓存 <1 秒 |
| `/etf/performance?symbols=510300` | 4.521s | 0.013s | 冷路径快速降级，热缓存 <1 秒 |
| `/etf/scale?period=1m&top_n=10` | 4.492s | 0.011s | 冷路径快速降级，热缓存 <1 秒 |
| `/etf/detail/510300` | 4.528s | 0.007s | 冷路径快速降级，热缓存 <1 秒 |

测试网络下外部 ETF 行情源没有在预算内返回，因此冷调用如实返回 unavailable/degraded，而非伪造数据；所有 ETF 冷调用均控制在约 4.53 秒内，符合“数据源缺失快速返回业务空态”的要求。市场兼容端点仍保留较宽的 6 秒预算，以保证实时/日频组合的兼容响应；该项较“本地纯计算”目标更慢，但较原 45 秒 Redis 降级路径显著改善。

## 自动化验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_market_etf_performance.py tests/test_etf.py tests/test_swr_cache.py -q
# 34 passed
.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q
# 24 passed
.\scripts\run_offline_tests.ps1
# 773 passed, 8 skipped, 2 deselected, 2 failed（30:23）
```

完整离线基线的两项失败均为 `tests/test_pipeline.py::{test_pipe_fail_fast,test_pipe_idempotent}`：
断言期望 `current_step=build_features`，实际为 `validate`，日志显示数据质量门禁先因
`600519: |pct| > 45%` 失败；与市场/ETF 模块没有调用关系。本次受影响测试均通过。

新增 `backend/tests/test_market_etf_performance.py` 覆盖：

- 实时块三路受限并发（3×80ms 小于 200ms，而非串行约 240ms）；
- ETF performance 相同参数二次调用命中缓存、不重复读取 K 线；
- detail 慢源按块级预算快速返回 unavailable。

## 结论

**IS_PASS: YES（本任务范围）**。跨文件检查已确认：新 ETF 缓存键与路由参数匹配；SWR 使用已有 Redis/LRU 机制；新增 `data_freshness` 仅扩展响应，不破坏既有块字段与 HTTP 200 + 业务 code 约定；临时 8123 服务已停止。完整离线基线仍有两项既存 pipeline 质量门禁失败，详见上文，未归因于本次改动。
