# 代码摘要 — Sprint 2（实时体系：批量行情 / SSE / 预警中心）

> 工程师环节（python-fullstack-engineer SOP）｜ 日期：2026-09-05 ｜ 前置：Sprint 1 已交付
> 完成度检测结论：Sprint 1 全部完成（git 远端除外，需用户提供）；Sprint 2 此前 0/4，本轮补齐。

## 检测矩阵（路线图 × 代码现状）

| Sprint | 事项 | 检测结果 | 本轮动作 |
|---|---|---|---|
| S1 | refresh=1 + DataFreshness | ✅ 已完成（4 端点 + 组件） | — |
| S1 | SWR（L1-2）+ Redis 常驻（L1-1） | ✅ 已完成 | — |
| S1 | 磁盘治理（归档+zstd+水位） | ✅ 已完成 | — |
| S1 | git 远端 | ⏸ 需用户提供远端地址 | 未动 |
| S2 | 批量行情 `/market/quotes` | ❌ 未实现 | **本轮实现** |
| S2 | SSE quotes/alerts 频道 | ❌ 未实现（仅通知流） | **本轮实现** |
| S2 | Watchlist/Screener/Overview 实时接入 | ❌ 未实现 | API 层就绪 + 预警中心页 |
| S2 | 预警中心 MVP | ❌ 未实现 | **本轮实现** |

## 文件清单

| 文件路径 | 变更 | 关键实现点 |
|---|---|---|
| `backend/app/data/realtime.py` | 修改 | `fetch_tencent_quotes_batch`（一次 HTTP 拉 N 只，字段索引 2026-09-05 实测校验）、`_fetch_sina_quotes_batch`（Referer 头降级源）、`fetch_quotes_batch`（腾讯→新浪→degraded，不造数）；volume 统一**手**与 daily_bar 对齐，amount=元 |
| `backend/app/core/quotes_hub.py` | **新增** | ① `quotes_snapshot()`：QUOTES_TTL 进程缓存（threading.Lock，跨事件循环安全），/market/quotes 与 SSE 共享——外部请求次数与订阅数无关；② quotes 抓取广播循环（单任务、按订阅并集抓取、引用计数归零自停）；③ alerts 订阅/广播（事件驱动）；④ `clamp_quotes_ttl` 钳制 [15,120]s |
| `backend/app/api/v1/market.py` | 修改 | `GET /market/quotes`：≤200 只（超限业务码 4001）、空 symbols 4001；快照契约 `{as_of, source, quotes}` |
| `backend/app/api/v1/notify.py` | 重写 | `GET /notify/stream?channels=notify,quotes,alerts&symbols=...&ttl=30`：fan-in 合并流（`event: quotes/alerts` + legacy `data:`），15s 心跳，未知频道即时 `event: error`；断开全量退订 |
| `backend/app/db/models.py` | 修改 | `AlertRule` / `AlertEvent` 表（路线图 DDL 对齐；create_all 幂等建表） |
| `backend/app/api/v1/alerts.py` | **新增** | 规则 CRUD（PUT/DELETE + params 白名单与值域校验）、事件查询/批量已读、评估引擎（price_pct/price_cross/volume_spike/score_topk/factor_quantile/data_health 六类）、冷却去重（**UTC 修复**）、SSE + webhook 分发、`alert_scheduler`（盘中 30s/盘后每小时） |
| `backend/app/api/v1/router.py` | 修改 | 注册 `/alerts` 路由 |
| `backend/app/main.py` | 修改 | lifespan 挂载 `alert_scheduler` 任务 |
| `backend/app/core/config.py` | 修改 | `QUOTES_TTL`（默认 15s）；`WARM_OVERVIEW_ON_STARTUP` 开关（测试默认关闭） |
| `backend/app/main.py`（lifespan） | 加固 | shutdown 改 `asyncio.wait(bg, timeout=10)` 有界等待——不可取消的 to_thread（预热外部聚合）曾把 TestClient/uvicorn 关闭流程永久拖死（faulthandler 转储实证） |
| `backend/tests/conftest.py` | 加固 | 测试会话强制 `REDIS_ENABLED=0` + `WARM_OVERVIEW_ON_STARTUP=0`：本机 aqp-redis 上线后，真实 Redis 使缓存跨 pytest 运行存活（影子键 TTL 2100s 跨 run 命中 stale）、redis-py 异步连接跨 loop 复用永久 pending |
| `backend/tests/test_market_quotes.py` | **新增** | 8 用例：真实样本批量解析、契约、缓存命中（外部抓取仅 1 次）、4001 上限/空参、降级不造数、SSE 协议格式、alerts 扇出 |
| `backend/tests/test_alerts.py` | **新增** | 7 用例：CRUD 往返、六类校验拒绝、price_pct 触发+冷却、volume_spike（合成 daily_bar）、score_topk 进出（合成 predictions）、data_health 磁盘、批量已读 |
| `frontend/src/api/market.ts` | 修改 | `quotes()` + `LiveQuote/QuotesData` 类型 |
| `frontend/src/api/alerts.ts` | **新增** | 规则 CRUD/事件/已读 API + 类型 |
| `frontend/src/api/client.ts` | 修改 | 补 `del()` 助手（DELETE 端点消费） |
| `frontend/src/pages/Alerts/index.tsx` | **新增** | 预警中心页：规则管理（类型联动参数表单）+ 触发历史（未读徽标/全部已读/30s 轮询） |
| `frontend/src/App.tsx` / `Sidebar.tsx` | 修改 | `/alerts` 路由 + 「预警中心」导航（新增 IconBell） |

## 关键技术决策

1. **快照缓存下沉 core 层**（`quotes_hub.quotes_snapshot`）：api → core 单向引用；SSE 广播与 REST 端点共享同一份 TTL 缓存，严格满足「3 标签页订阅，外部请求次数 = 单订阅者」验收。
2. **threading.Lock 而非 asyncio.Lock**：临界区为纯 dict 读写（无 await），且模块级 asyncio.Lock 不可跨事件循环复用（pytest 多 loop / lifespan 重启会崩——测试中实际踩到）。
3. **SSE 测试策略**：httpx ASGITransport 会等 ASGI app 运行结束才返回响应，无限 SSE 流在 `client.stream()` 即死锁（实测踩到）——改为直接调用端点迭代 `body_iterator`，全链路（订阅→广播→pump→格式）仍被覆盖，并加硬超时防回归挂起。
4. **冷却 UTC 修复（源码 Bug）**：SQLite `CURRENT_TIMESTAMP` 存 UTC，冷却比较原用本地 `datetime.now()`，时区差（CST+8）会使冷却恒不生效——统一 `datetime.utcnow()` 比较；落库 payload 补本地 `ts` 供展示。
5. **volume 口径**：腾讯快照原始单位手，新浪源（股）÷100 换算，与 `daily_bar.volume`（手）对齐——volume_spike 规则直接可比，不引入隐式量纲转换。
6. **不造数红线**：降级链全部失败返回 `source:"degraded"` + 空数组；行情缺失/历史不足的规则本轮跳过而非放宽阈值。

## 验证结果

- `pytest tests/test_market_quotes.py tests/test_alerts.py` → **15 passed**（首轮暴露 4 个测试缺陷 + 2 个源码缺陷：冷却 UTC、局部导入不可打桩，均已修复复验）
- 真实批量行情性能（腾讯源，本机实测）：**50 只 264ms（<300ms ✅）、200 只 382ms（<500ms ✅）**
- `npx tsc --noEmit` → 0 错误；`npm run build` → 成功
- 全量回归：**369 passed / 5 skipped / 0 failed（296s）**，详见 test-report-sprint2.md；全套耗时较修复前 ~896s 提速近 3 倍

## 已知限制 / 遗留项

1. **Sprint2 前端实时接入为最小落地**：`/market/quotes` API 分组就绪，Watchlist/Screener 的实时列升级与 SSE 前端消费（EventSource quotes 频道）留待下一轮（后端协议已验收）。
2. webhook 渠道需配置 `NOTIFY_WEBHOOK_URL`；未配置时 webhook 渠道静默跳过（SSE 不受影响）。
3. `factor_quantile` 只扫最新年分区 features；跨年窗口 >当前年数据量的场景精度受限。
4. 预警调度盘中每 30s 评估一次 score_topk/factor_quantile 属日级数据，重复评估开销极小但存在；如需可加「当日已评估」短路。
5. E2E（Playwright）未覆盖新页面，属 R8 存量范围。
