# 测试报告 — Sprint 2 实时体系（第 1 轮）

> QA：严过关（兼）｜ 日期：2026-09-05 ｜ 被测：/market/quotes + SSE quotes/alerts + 预警中心 MVP
> 工程师环节：python-fullstack-engineer SOP（主理人代行，详见 sprint2-code-summary.md）

## ROUTE

```
ROUTE: NoOne
```

第 1 轮共发现 3 个失败用例，经甄别均为**测试环境假设失效**（本机 aqp-redis 容器当日上线），修复属于测试基建加固而非源码缺陷回退；伴随修复交付了 2 项生产加固（见「环境修复」）。修复后全量回归通过，无需返工。

## 用例统计（最终全量回归）

| 项 | 结果 |
|---|---|
| 命令 | `backend/.venv/Scripts/python.exe -m pytest -q` |
| 总数 | 374 |
| 通过 | **369** |
| 失败 | **0** |
| 跳过 | 5（`tests/test_e2e.py`，E2E_AVAILABLE 门控，惯例跳过） |
| 耗时 | **296s（4:56）**——修复预热泄漏后较此前 ~896s 提速近 3 倍 |

新增测试：`test_market_quotes.py`（8 用例）+ `test_alerts.py`（7 用例）= 15 个，全部通过。

### 新增用例覆盖清单

| 文件 | 覆盖点 |
|---|---|
| test_market_quotes | 真实腾讯样本批量解析（字段索引/单位口径）；契约 as_of/source/quotes；缓存命中（同 symbols 集合外部抓取仅 1 次）；上限 200 超限 4001；空 symbols 4001；降级返回空数组不造数；SSE `event: quotes` + `data:` JSON 帧格式；未知频道 `event: error`；alerts 频道扇出/退订 |
| test_alerts | 规则 CRUD 往返（POST/PUT/DELETE+404）；六类规则参数白名单与值域拒绝；price_pct 触发→冷却期内去重→历史可查→批量已读→未读过滤；阈值未达不触发；volume_spike（合成 30 日 daily_bar，5 倍均量触发/1.2 倍不触发）；score_topk 进入/跌出（合成两期 predictions + watchlist 作用域）；data_health 磁盘水位（96.5% 触发 / 50% 不触发） |

### 真实链路抽测（腾讯源线上实测）

| 场景 | 验收标准 | 实测 | 结论 |
|---|---|---|---|
| 批量行情 50 只 | <300ms | **264ms** | ✅ |
| 批量行情 200 只 | <500ms | **382ms** | ✅ |
| 降级不造数 | 空数组 + source=degraded | 单测断言 | ✅ |

## 首轮失败明细与环境修复（3 例）

| # | 用例 | 根因 | 处置 |
|---|---|---|---|
| 1 | `test_api.py::test_screener_filters_and_order`（from_cache 断言 True≠False） | **本机 aqp-redis 当日上线**（Up 2h），被 kill 的上一轮回归把 SWR 影子键（TTL 2100s）写入真实 Redis，下一轮在窗口内命中 stale 缓存——套件隐含假设「测试无 Redis」失效 | conftest 强制测试会话 `REDIS_ENABLED=0`（与 SQLITE/DATA_ROOT 隔离同哲学），并加入会话结束清理清单 |
| 2 | `test_swr_cache.py::test_cached_or_build_stale_hit_background_rebuild` | redis-py 异步连接跨事件循环复用 → `try_lock` 的 future 永久 pending（测试进程多 loop） | 同上（禁用真实 Redis 后走进程内锁兜底路径） |
| 3 | `test_swr_cache.py::test_cached_or_build_stale_bg_rebuild_after_build_failure` | 同 #2 | 同上 |

### 伴随的环境级加固（生产受益）

1. **main.py lifespan shutdown 有界等待**：`asyncio.wait(bg, timeout=10)`——原实现 cancel 后直接退出，遇到不可取消的 to_thread（如预热中的外部聚合）会把 TestClient/uvicorn 关闭流程拖死（faulthandler 转储实证：portal join 永久阻塞）。
2. **`WARM_OVERVIEW_ON_STARTUP` 开关**（config.py，测试默认关闭）：原每 TestClient 启动都真实发起 48s+ overview 聚合预热，既拖慢全套 3 倍又在退出时不可取消；conftest 置 0，生产默认不变。

## 遗留问题

1. E2E（Playwright）5 例按惯例跳过；Alerts 页面未纳入 E2E（R8 存量范围）。
2. SSE quotes 频道的浏览器端 EventSource 消费（Watchlist/Screener 实时列接入）为下一轮工作，后端协议本轮已验收。
3. 预警调度在收盘时段为一小时一评（含日级规则），如后续接入分钟级规则可细化调度粒度。
4. 本机 aqp-redis 现已常驻——生产部署验收点（重启后首请求 <300ms 且 from_cache=true）具备真实验证条件，建议用户按 run.md 步骤自测。
