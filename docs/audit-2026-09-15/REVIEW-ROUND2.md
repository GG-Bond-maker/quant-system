# AQP 增量改动审核 · 第二轮（主理人汇总）

> **审核时间**：2026-09-15 20:39 起
> **审核对象**：工作区**未提交**的 60 个已修改 + 32 个未跟踪文件（92 项）
> **分工**：架构评审（高见远，只读）→ 专项 `gao-incremental-audit.md`；实测验证（严过关，只读，已跑全量 pytest）
> **基线对照**：`docs/audit-2026-09-15/REVIEW-SUMMARY.md`（第一轮，测于 19:15–19:55）

---

## 〇、一句话结论

> **「没改完全」说轻了。** 20:12 有一次改动落在 `frontend/src/types/p1.ts`，它**没有**改后端、**没有**改消费点，
> 却让前端 `tsc -b && vite build` **直接编译失败**（`tsc` 退出码 2，2 个 TS2322）——
> 第一轮报告里"前端 tsc 0 error"的结论**已失效**，因为那份报告测于 20:12 之前。
> 同时第一轮点名的 3 个 P0（export 空数据崩溃 / 一条既有测试必失败 / 公告·北向·ETF 搜索静默下线）**一个都没消**。

---

## 一、交付概览

| 项 | 结果 |
|---|---|
| 后端全量离线测试 | **808 passed / 8 failed / 8 skipped / 3 deselected，252.11s（4:12）**（实测跑完） |
| 前端 `tsc --noEmit` | **退出码 2 —— 构建门禁已断**（`npm run build` = `tsc -b && vite build`，必然失败） |
| 10 条断言裁决 | **9 条证实 / 1 条计数吻合但基线含回归** |
| 8 个失败用例归因 | 本轮回归 **3** / HEAD 旧缺陷 **1** / 顺序污染 flake **4** |
| 半成品 | P0 **6 项** + P1 **6 项** + P2 **9 项** |
| 结论 | **建议本轮暂不合并，先补齐下述 P0** |

---

## 二、🔴 P0 清单（全部经实测证实）

### P0-1 前端构建已断（本轮 20:12 新引入）

`Screener/index.tsx:25` 是 `import type { ScreenerResult } from '@/types/p1'`，即 `result` 的类型来自 `p1.ts`。
而 `p1.ts` 把 `ScreenerItem.risk`→`signal_strength`、`stats.today.high_risk`→`strong_signal` 之后：

```
src/pages/Screener/index.tsx(223,9): error TS2322
  Property 'risk' is missing in type 'p1.ScreenerItem' but required in type 'ScreenerItem'
src/pages/Screener/index.tsx(358,19): error TS2322
  Property 'high_risk' is missing ... required in type 'StatsDay'
```

- 报错点 1：`const raw: ScreenerItem[] = result?.items ?? []`（本地接口要 `risk`，p1 只给 `signal_strength`）
- 报错点 2：`<StatsCards stats={result?.stats ?? null} />`（`StatsCards` 要 `high_risk`，p1 只给 `strong_signal`）
- **全前端 grep `signal_strength` 消费点 = 0** → 这是"改了唯一没人消费的类型层"
- 运行期**不会**白屏（渲染走的是 `index.tsx` 自己的本地接口 + 后端仍返 `risk`），所以**不跑 tsc 根本发现不了**

### P0-2 一条既有测试必然失败（本轮回归）

`tests/test_write_endpoints_smoke.py::test_settings_apikeys_rotate_writes_isolated`
实测 `1 failed, 3 passed`。**真实失败点在前置的 `_ok(body)`（line 416）**：`assert 40000 == 0`，
而非第一轮报告推测的 line 538 的 `aqpx_` 断言。

- 后端 `app_settings.py:254-263` 本轮已把 `/settings/apikeys/rotate` 改成**明确拒绝**（`fail(ERR_PARAMS)`）
- 该测试文件**不在本轮 60 个改动里** → 后端改了语义，测试没跟

### P0-3 通知流端点的权限契约未纳管（本轮回归，2 条测试）

| 用例 | 报错 |
|---|---|
| `test_read_endpoints_rbac.py::test_read_endpoints_have_expected_role` | `/api/v1/notify/stream` 内省得到 `(None, 'viewer')` —— 本轮改成自定义依赖 `require_stream_viewer`，运行时扫描扫不到角色 |
| `test_write_endpoints_smoke.py::test_registry_matches_runtime_rbac` | 未纳管写端点 `('POST', '/api/v1/notify/stream-ticket')` |

### P0-4 静默功能下线（3 项，用户端全空且不报错）

| 功能 | 实测证据 | 用户实际看到 |
|---|---|---|
| **公告（近期事件）** | `news_announcement` 表 **0 行**；全仓 grep **无任何写入方**；`fetch_events('600519')` → `RuntimeError` | 空占位（`PanelEmpty`） |
| **北向持股** | `fetch_north_holding('600519')` → `RuntimeError`；`build_north` 同 | 占位「—」，且 `unavailable` 被缓存 **6h** |
| **ETF 搜索** | `instrument` 表实测 **仅 `('stock', 5552)`，etf = 0**；`_search_assets('510300',10)` → **0 行** | 组合页搜不到任何 ETF |

**ETF 的根因不在查询，在入库**：`akshare_adapter.py:289` 把 `instrument_type` 硬编码为 `"stock"`，
`ingest/__main__.py:51` 只 upsert 股票目录 → ETF 从未进过 `instrument` 表。

> ⚠️ **重要新发现：公告其实有数据，只是面板读错了源。**
> `data/parquet/announcements/symbol=__all__/year=2024.snappy.parquet` 实测**有 3 行**，
> 由 `data/ingest/announcements.py:save_announcements` 写入、被 `market.py:_latest_announcements` 读取。
> 而个股面板 `panels.py:build_events` 读的是**空的 SQLite 表** —— 同一"公告"语义两套存储，面板选错了。
> → **这项可能是最低成本的修复**（改读取源，不必新建管线）。

### P0-5 `export.py:44` 空数据崩溃

- 代码：`filename = f"screener_{data['date'].replace('-','')}.xlsx"`
- 实测 traceback 精确指向 `export.py:44 AttributeError: 'NoneType' object has no attribute 'replace'`
- 表现：**HTTP 200 + 业务码 50000**（信封契约下不是裸 500）
- `export.py` 本身**不在本轮 diff**（HEAD 旧代码），但本轮 `_screen` 新增的 `date=None` 不可用契约让它**变成可达**
- 已打红 `test_read_endpoint_allows_authorized[/api/v1/export/screener-researcher]`

### P0-6 降级空态被固化进缓存

`cache/swr.py:129-132` 同步重建路径**无条件** `write_cache(...)`，没有 `valid=` 谓词。
实测：第 1 次 build 返回 `unavailable` → 第 2 次（build 换成必抛异常的函数）仍返回同样的 `unavailable`，
且 `from_cache=True`、`build invocations: [1]`。

实测 ttl：`screener=300`（+stale 1800）、`market/overview=300`、`stocks=60`、`watchlist=60`、`datacenter=120`。
个股面板 `stock.py:243 _cached_block` 同款：`north=21600(6h)`、`events=3600`。

→ **外部源一次抖动 = 全站连续数分钟显示"不可用"且不自愈。**

---

## 三、半成品清单

| 编号 | 类别 | 位置 | 现象 | 级别 |
|---|---|---|---|---|
| N-01 | B 单侧 | `tests/.../:57` | 写端点注册表缺 `POST /notify/stream-ticket` | P1 |
| N-02 | B | `core/compute_guard.py:15` | 仍 `acquire(blocking=False)` 抛错**不排队**；前端已做 2 路队列，后端一行没动 | P1 |
| N-03 | B | `ml/monitor.py:113` | `sorted(root.rglob("*.parquet"))` 实测混读 `v1`(5)+`v2g`(9)=14 个文件；同仓库已有 `data/features.read_feature_frame()` 且 alerts/research/studio 都已接入，**只有 monitor 没接** | P1 |
| N-04 | B | `api/v1/etf.py` ↔ `pages/Etf*` | ETF 后端新增字段，前端 ETF 页本轮 **0 改动** → 全不消费 | P1 |
| D-01 | D 口径分裂 | `types/datacenter.ts:22` ↔ 后端 | 前端声明并读 `refreshing`；后端全文 grep **零匹配** | P1 |
| D-02/D-03 | D/B | `etf.py:495-505`、`market.py:370-405` ↔ `types/stock.ts:393` | 降级块契约两套：`BlockBase` 新字段只有 Recommend 能拿到，其余 6 块只返 `status/reason` | P2 |
| B-01 | B | `watchlist.py:320,343` ↔ `types/p1.ts` | 后端新增 `flow_status`，前端类型未扩展 | P2 |
| C-01~04 | C 配置漂移 | `config.py:79/52/100`、`stock.py:68` | `FEATURE_VERSION` / `METRICS_REQUIRE_AUTH` / `WARM_OVERVIEW_ON_STARTUP` 均未收录进 `.env.example`；`AQP_PANEL_BLOCK_TIMEOUT` 默认 **20→4.5 腰斩**且无文档 | P1 |
| E-01 | E 逻辑矛盾 | `panels.py:194-195` | `return {"status":"unavailable"}` 写在 `:188` 的 `raise` **之后 → 永远不可达**，作者的本意被吞掉 | P2 |
| A-01~03 | A 孤儿 | `watchlist.py:152 _etf_names()`、`datacenter.py:286/307-318`、`RequireAuth.tsx:24` | 全项目无调用方 | P2 |
| E-02~04 | E 注释失真 | `useWatchlistStore.ts:3`、`api/market.ts:8`、`Screener/index.tsx:56` | 注释与现实矛盾（称后端未实现／旧"冷算 49.5s"／暗示已迁移 risk） | P2 |

---

## 四、前后端契约统一方案

| # | 字段 | 后端（权威） | 前端 | 结论 |
|---|---|---|---|---|
| K-1 | 榜单项强度 | `risk: low/mid/high`（`screening.py:136`） | `p1.ts:19` 写 `signal_strength`；`index.tsx:57` 另有本地 `risk:string` | **矛盾 → 构建失败** |
| K-2 | stats 强信号数 | `high_risk`（`screening.py:159`） | `p1.ts:39,44` 写 `strong_signal`；`StatsCards.tsx:18` 用 `high_risk` | **矛盾 → 构建失败** |
| K-3 | 自选股强度 | `risk`（`screener.py:127,131`） | `p1.ts:139 WatchlistQuote.risk` | 一致（本轮未动） |
| K-4 | `screener.date` 可空 | `string \| None` | 前端已适配 ✅ | 后端内部 `export.py:44` 未适配 |
| K-5 | ETF 降级块 | `status/reason` | `types/etf.ts` 同 | 与 `stock` 两套语义 |
| K-6 | `datacenter.refreshing` | 已不返回 | `types/datacenter.ts:22` 仍声明 | 双口径 |

**建议统一方案**（若确定采纳 `signal_strength` 语义）：
① 后端**双写**（保留 `risk`/`high_risk` 兼容别名一版）→ ② 前端换读（`index.tsx` 删本地重复类型、`StatsCards`、`FilterPanel`、`WatchlistQuote`）→ ③ 删别名 + 改快照 DB 列 + `core/excel.py`。

> 20:12 那次改动**只做了 ② 的一小部分且跳过了 ①③**，结果既没达成新语义、又把构建弄断。

---

## 五、测试基线实测（第 10 条）

`cd backend && python -m pytest -m "not network" -q`
→ **808 passed / 8 failed / 8 skipped / 3 deselected，252.11s；退出码 1**

8 个失败归因：

| # | 用例 | 归因 |
|---|---|---|
| 1 | `test_read_endpoints_rbac::test_read_endpoints_have_expected_role` | **本轮回归**（notify/stream 自定义依赖） |
| 2 | `test_read_endpoint_allows_authorized[/api/v1/export/screener-researcher]` | **HEAD 旧缺陷**（`export.py:44`） |
| 3-6 | `test_train_service.py::test_start_rejected_*` ×3 + `test_param_whitelist_filters_unknown_kwargs` | **顺序污染 flake** —— 单跑 `pytest tests/test_train_service.py -q` → **15 passed 全绿**；全量时因残留 pipeline 锁（`管道任务 [sync] 执行中`，`assert 40900 == 52000`）失败 |
| 7 | `test_write_endpoints_smoke::test_registry_matches_runtime_rbac` | **本轮回归**（notify/stream-ticket 未纳管） |
| 8 | `test_write_endpoints_smoke::test_settings_apikeys_rotate_writes_isolated` | **本轮回归**（rotate 语义改了） |

**结论：808 这个数字本身成立，但它已经把 3 个本轮回归算进去了** —— 不能当作"健康基线"。

---

## 六、补齐任务列表（按依赖顺序）

| 任务 | 级别 | 依赖 | 涉及文件 | 验收 |
|---|---|---|---|---|
| **T01 恢复前端可构建** | **P0** | 无 | `types/p1.ts`（回滚两个字段名）**或** 后端全链路原子改名 | `tsc --noEmit` 退出码 0 |
| **T02 修后端回归 + 测试治理** | **P0** | 无 | `export.py:44`；`tests/test_write_endpoints_smoke.py`（rotate 断言、注册表补 2 条）；`notify.py` 权限内省 | 上述 3 个回归用例转绿 |
| **T03 静默下线落地** | **P0** | 需你决策 | 公告 → 改读 `parquet/announcements`（**成本最低**）或补管线；北向 → 补有界源 or 显式下线；ETF → `akshare_adapter.py:289` + `ingest` 落 `instrument` | 三项各有明确归属，前端不再"假空" |
| **T04 后端"半修"收口** | P1 | 无 | `ml/monitor.py:113` 接 `read_feature_frame()`；`compute_guard.py:15` 改有界排队；`swr.py:129` 加 `valid=` 谓词 | 新增/回归测试覆盖 |
| **T05 契约/配置/卫生收尾** | P1 | **T01** | `.env.example` 补 4 开关；`types/datacenter.ts` 删 `refreshing`；删 3 处孤儿；`panels.py:194` 不可达分支；`pages/Etf*` 消费新字段；`backend/` 根目录 13 个 `pytest_*.txt` 调试产物（未进 `.gitignore`） | tsc 0 error；grep 孤儿 = 0 |

---

## 七、需要你拍板的决策

1. **强度字段方向**：回滚 `risk`（零风险但语义不变）还是全链路改 `signal_strength`（语义正确，需前后端+DB 快照列同步）？
2. **公告**：改读已有的 `parquet/announcements`（推荐，成本最低），还是补同步管线，还是显式下线？
3. **北向持股**：补有界源落库，还是前端显式标注"已下线"？
4. **ETF 搜索**：把 ETF 目录落 `instrument` 表（需改 `akshare_adapter.py`），还是恢复远端目录兜底？
5. **降级缓存**：degraded 给短 TTL（≤15s）止血，还是"降级一律不缓存"？
6. **提交拆分**：92 项全部未提交 —— 是否先按主题拆提交（`fix(sse)` / `perf(market)` / …）再收尾？

---

## 八、本轮的优点（建议保留）

1. **SSE ticket 方案**（双存储域 + GETDEL 原子消费 + 60s 一次性）质量高，威胁模型逐条对应实现。
2. 全量测试**首次跑完**（4:12），这是后续一切验证的前提。
3. 合规侧 `ResearchDisclaimer` 组件已在 4 个页面落地。
4. 18 个错误码前后端全对齐。
5. `data/features.py` 的版本化读取 helper 本身是对的，**只是 monitor 没接**。
