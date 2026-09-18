# AQP 增量修改 · 架构师复核报告（第二轮）

- 复核人：高见远（架构师）
- 复核时间：2026-09-15 20:30+（**晚于前序报告的 19:15–19:55**，见 §7）
- 复核方式：**全程只读业务源码**；仅用 `git status/diff`、`Grep`、`Read`、只读 SQLite、`npx tsc --noEmit`
- 对照前序：`REVIEW-SUMMARY.md`(19:50) / `architecture-review.md`(19:40) / `qa-verification.md`(19:48)
- 关键变量：`frontend/src/types/p1.ts` 在 **20:12** 被修改，**晚于全部前序报告**

---

## 1. 审核结论（一句话）

> **"没改完全"这句话低估了问题：20:12 那次 `p1.ts` 改动不是"改了一半"，而是"改错了层、且方向带破坏性"——
> 它改了唯一没人消费的类型层、把三处字段口径撕成两半、并让前端 `tsc --noEmit` 从 0 error 变成 RC=2（构建门禁已断）；
> 同时前一轮遗留的 3 个 P0（export 500、两条既有测试失败、公告/北向/ETF 搜索静默下线）一个都没消。全批 92 个改动仍未提交。**

前序报告的"tsc 0 error / 前端健康"结论**已因 20:12 的改动失效**。

---

## 2. 半成品清单（核心）

类别：A 孤儿代码 / B 单侧改动（前后端不对称）/ C 配置漂移 / D 重复实现口径分裂 / E 注释与实现矛盾 / F 静默功能下线

| 编号 | 类别 | 文件:行 | 现象（现有代码事实） | 为什么是缺陷 | 严重度 | 补齐动作 |
|---|---|---|---|---|---|---|
| **H-01** | **B** | `frontend/src/types/p1.ts:19,39,44` ↔ `Screener/index.tsx:223,358` | p1.ts 把 `ScreenerItem.risk` 改名 `signal_strength`、`stats.today.high_risk` 改名 `strong_signal`；后端与 `StatsCards.tsx` 全用 `risk`/`high_risk` | **实测 `npx tsc --noEmit` = RC 2，2 个 TS2322 error**（见 §3/§7）。前端构建门禁已断 | **P0** | 见 T01：优先回滚 p1.ts 恢复可构建；如要改名则全链路原子改 |
| **H-02** | **A** | `frontend/src/types/p1.ts:19,39,44` | 全项目 grep：`signal_strength`/`strong_signal` **仅出现在 p1.ts 自身**，0 消费方 | 改名后无人读，是"悬空类型"——UI 仍渲染 `risk`（`index.tsx:620`、`StatsCards.tsx:162,199`） | P0 | 随 H-01 处理 |
| **H-03** | **B** | `backend/app/api/v1/export.py:44` | `filename = f"screener_{data['date'].replace('-', '')}.xlsx"`，而 `_screen` 在无预测时返回 `date: None`（`screener.py:263-265,279-281`） | `None.replace` → AttributeError → 裸 500。**注意 `screener_workbook` 不崩（`excel.py:44` 用 `str(v)`），崩点确实在 :44** | **P0** | `(data.get('date') or 'nodata').replace('-','')`；或 `date is None` 时返 `ERR_DATA_EMPTY` |
| **H-04** | **E** | `backend/tests/test_write_endpoints_smoke.py:538-552` | 断言 `_ok(body)` + `raw.startswith("aqpx_")`；后端 `app_settings.py:263` 已恒返 `fail(ERR_PARAMS,…)` | 断言与实现矛盾，**必然失败**；该测试文件**不在本轮 60 个改动清单**（`git status` 未列） | **P0** | 改断言为 `code==40000 且 data is None`，或整条替换为"接口已下线"契约测试 |
| **F-01** | **F** | `backend/app/data/panels.py:187-188` + `realtime.py:658` | `build_events` 查本地 `news_announcement`（实测 **0 行**）→ 空则调 `fetch_events`，而它**恒 raise** | 个股"近期事件"块**永久不可用**，且被 `_cached_block` 缓存 600s 伪装成"暂时" | **P0** | 见 §4（补写入方 or 显式下线） |
| **F-02** | **F** | `backend/app/data/realtime.py:505` + `panels.py:105` | `fetch_north_holding` **恒 raise** `RuntimeError("北向持股暂无有界数据源")` | 个股"北向持股"块**永久不可用** | **P0** | 见 §4 |
| **F-03** | **F** | `backend/app/api/v1/portfolio.py:68-71` | `_search_assets` 只查 `instrument WHERE instrument_type IN ('stock','etf')`；DB 实测 `instrument` **仅 ('stock',5552)，0 条 etf** | `/portfolio/search` **永久搜不到任何 ETF**，请求路径不再抓远端目录 | **P0** | 补 etf 落库（根因见 §4）或保留远端目录兜底 |
| **G-01** | **D** | `backend/app/cache/swr.py:131-132`（`market.py:859` ttl=300 / `etf.py:96` ttl=300 / `screener.py:410` ttl=300） | 同步重建路径**无条件** `write_cache(key, data, ttl, stale_window)`，无 `valid` 谓词 | 各 `build()` 已把 TimeoutError 吞成 degraded → **一次外部抖动 = 全站该键连续 ttl（300s）空态**且不自动重试（新写的主键是"新鲜空态"） | P0 | 给 `cached_or_build` 加 `valid=` 谓词，或降级结果用短 TTL（≤15s） |
| **N-01** | **B** | `backend/tests/test_write_endpoints_smoke.py:57`（VIEWER/RESEARCHER 注册表） | 缺 `("POST","/api/v1/notify/stream-ticket")`；grep 该文件无 `notify`/`stream-ticket` | `test_registry_matches_runtime_rbac` 报"存在未纳管的写端点"→ 治理网破损 | P1 | 把新端点补进注册表 |
| **N-02** | **B** | `backend/app/core/compute_guard.py:15` | `_slots.acquire(blocking=False)` 失败即 `raise ERR_RATE_LIMITED` | 前端已做 ComputeQueue(2)（`Research/index.tsx:75-113`），**后端一行没动** → 跨标签页/跨用户仍直接 40103 | P1 | 改有界排队（`Semaphore`+`retry_after`）或响应带队列长度 |
| **N-03** | **B** | `backend/app/ml/monitor.py:113` | `files = sorted(root.rglob("*.parquet"))` 仍混读 features 全部 `version=*` | P0#1 半修：research/studio/alerts 已切 `data/features.py`，仅监控路径仍污染 → 可能触发错误自动重训 | P1 | 切 `read_feature_frame()` 或按 `FEATURE_VERSION` 限定 |
| **N-04** | **B** | `backend/app/api/v1/etf.py`（4 端点新增 `data_freshness/coverage/message/status`） ↔ `frontend/src/pages/Etf*` | 前端 ETF 页本轮 **0 改动**（`git status` 未列），新字段全不消费 | 单侧改动：4.5s 后用户看到"空"而非"为什么空" | P1 | 前端消费状态/文案 |
| **D-01** | **D** | `frontend/src/types/datacenter.ts:22` + `pages/DataCenter/index.tsx:552` | 前端声明并读取 `refreshing`；后端 grep `"refreshing"` **零匹配**（已不产出） | 类型与实现双口径，读恒 undefined | P1 | 删前端类型与消费点（`stale` 已有替代） |
| **D-02** | **D** | `backend/app/api/v1/etf.py:495-505` vs `frontend/src/types/etf.ts` vs `types/stock.ts:393` | ETF 降级块只有 `status/reason`；`stock.BlockBase` 有 `message/as_of/coverage/freshness` | 同一产品两套"降级块"语义 | P2 | 统一为 `BlockBase` |
| **D-03** | **B** | `backend/app/api/v1/market.py:370-405` ↔ `frontend/src/types/stock.ts:393` | `BlockBase` 4 个新字段只 `RecommendBlock` 能拿到；其余 6 块只返回 `status/reason` | 契约半成品：TS 因可选不报错，未来按 `message` 渲染会静默空白 | P2 | 后端补齐 6 块 or 前端收窄到 Recommend |
| **B-01** | **B** | `backend/app/api/v1/watchlist.py:320,343` ↔ `frontend/src/types/p1.ts` | 后端新增 `summary.flow_status`、`items[].status`；前端类型未扩展 | 12 只上限导致 `flow_status='unavailable'` 时用户只看到"资金流 -" | P2 | 补类型 + 展示阈值原因 |
| **C-01** | **C** | `backend/app/core/config.py:79` `FEATURE_VERSION` | `.env.example` **未收录**；留空=按 mtime 隐式选版本（`data/features.py:51` 仅 `logger.info`） | 部署方无从得知、口径可静默切换 | P1 | 补 `.env.example` + 空值时升 warning |
| **C-02** | **C** | `backend/app/core/config.py:52` `METRICS_REQUIRE_AUTH` | `.env.example` **未收录**（`main.py:190` 读取） | 同上 | P1 | 补 `.env.example` |
| **C-03** | **C** | `backend/app/core/config.py:100` `WARM_OVERVIEW_ON_STARTUP` | `.env.example` **未收录**；`conftest.py:171` 强依赖它=0 | 对外无文档 | P1 | 补 `.env.example` |
| **C-04** | **C** | `backend/app/api/v1/stock.py:68` `AQP_PANEL_BLOCK_TIMEOUT` 默认 20→**4.5** | `.env.example` 无此键，README 未提 | 默认值腰斩属行为变更，无文档 | P1 | 补文档 + 记录变更 |
| **E-01** | **E** | `backend/app/data/panels.py:194-195` | `return {"status":"unavailable",...}` 在 `fetch_events` raise 之后 → **不可达** | 作者想要的"显式 unavailable"被上一行 raise 吞掉 | P2 | 调整顺序，让本地空表走显式 unavailable |
| **A-01** | **A** | `backend/app/api/v1/watchlist.py:152` `_etf_names()` | 全项目无调用方 | 孤儿 | P2 | 删除 or 复用于 ETF 名称兜底 |
| **A-02** | **A** | `backend/app/api/v1/datacenter.py:286 _dir_size` / `:307-318 _refresh_overview+_OVERVIEW_REFRESHING` / `:335 global` | 全套死状态机，grep 无调用方 | 孤儿 | P2 | 删除 |
| **A-03** | **A** | `frontend/src/components/RequireAuth.tsx:24` `export default function RequireAuth` | `App.tsx:6` 只 import `RequireRole`；无 default import 方 | 孤儿 | P2 | 删除（保留 `hasMinimumRole`/`RequireRole`） |
| **E-02** | **E** | `frontend/src/stores/useWatchlistStore.ts:3` | 注释"后端 watchlist 接口尚未实现" | 后端早已存在且本轮大改 | P2 | 更新注释 |
| **E-03** | **E** | `frontend/src/api/market.ts:8` | 注释"冷算约 49.5s" | 与新 6s 预算矛盾 | P2 | 更新注释 |
| **E-04** | **E** | `frontend/src/pages/Screener/index.tsx:56-57` 注释"后端为兼容保留 risk 枚举" | 注释暗示还有别的口径；实际前后端**只有** `risk` 一套，新类型悬空 | 注释误导后人以为已迁移 | P2 | 随 T01 清理措辞 |

> 图例：`H-*` = 本轮新发现（含 20:12 改动）；`F-*` = 静默下线；`N-*` = 只改一侧；`G-*` = 降级被缓存。

---

## 3. 前后端契约不一致专表

| # | 字段 | 后端（权威）| 前端类型 | 前端消费点 | 现状 | 统一方案 |
|---|---|---|---|---|---|---|
| **K-1** | 榜单项信号强度 | `risk: 'low'\|'mid'\|'high'`（`data/screening.py:136`；快照 `:270,292`） | `p1.ts:19` 写成 `signal_strength: 'weak'\|'neutral'\|'strong'`；`Screener/index.tsx:57` 另有**本地** `risk: string` | `index.tsx:81-85,236,620` 全用 `risk`；`FilterPanel.tsx:16-21` 用 `low/mid/high` | **三处口径、两套字段名**；p1.ts 无人消费且 **tsc 报错** | 词表选 `signal_strength: weak/neutral/strong`（产品语义正确），但**必须原子改**：`screening.py:136`+`load_screener_snapshot:270,292`+`screener.py:127,131`+snapshot DB 列 + `index.tsx`+`StatsCards`+`FilterPanel`+`excel.py:35,41` 同一提交内改；迁移期后端**同时**回显 `risk` 别名一个版本 |
| **K-2** | 榜单 stats 强信号数 | `high_risk`（`data/screening.py:159`） | `p1.ts:39,44` 写成 `strong_signal`；`StatsCards.tsx:18` 声明 `high_risk` | `StatsCards.tsx:162,199` 读 `high_risk` | **不一致**；导致 `tsc` 在 `index.tsx:358` 报错 | 同 K-1 一起改；stats 键改名 `strong_signal`，后端 + `StatsCards.tsx:18,162,199` 同步 |
| **K-3** | 自选股信号强度 | `risk`（`screener.py:127,131`） | `p1.ts:139 WatchlistQuote.risk: 'low'\|'mid'\|'high'\|null` | `index.tsx:528` `SignalStrengthBadge level={it.risk}` | **一致**（本轮未动） | 若采纳 K-1 改名，同步把此列也改 `signal_strength` 并改 `WatchlistQuote` 类型；否则保持 `risk` 不动（但会与 K-1 命名分裂） |
| **K-4** | 选股结果 date 可空性 | `date: string \| None`（`screener.py:263-265`） | `p1.ts:23` `date: string \| null` ✅ | 前端已适配；**后端 `export.py:44` 未适配** | 后端内部单侧，见 H-03 | 修 `export.py` |
| **K-5** | ETF 降级块 | `status/reason`（`etf.py:495-505`） | `types/etf.ts` 只有 `status/reason`；`types/stock.ts:393 BlockBase` 多 4 字段 | ETF 页 0 消费 | 两套降级契约 | 统一 `BlockBase` |
| **K-6** | datacenter refreshing | 已不返回（grep 0 匹配） | `types/datacenter.ts:22` 仍声明 | `DataCenter/index.tsx:552` 读取 | 双口径 | 删前端 |

**统一方案总原则**：以**产品语义**为准（P0#10①：信号强度 ≠ 风险），字段名统一 `signal_strength`、取值 `weak/neutral/strong`、stats 键 `strong_signal`；**后端先改并保留 `risk`/`high_risk` 兼容别名一个版本**，前端改名后再删别名。迁移顺序：① 后端双写 → ② 前端换读 → ③ 删别名 + 删 `index.tsx` 本地类型。**现状风险**：20:12 只做了第②步的一部分（且无消费方），并跳过了①③，导致构建断裂。

---

## 4. 静默功能下线判定

| 功能 | 本地数据是否存在 | 是否有写入方 | 兜底 | 结论 | 建议 |
|---|---|---|---|---|---|
| **公告（近期事件）** | `news_announcement` **0 行**（只读实测 `data/sqlite/aqp.db`） | **无写入方**：全项目 grep 仅 `db/models.py:210`(建表)、`panels.py:176`(读)、`realtime.py:656`(注释)，**无任何 INSERT/upsert** | `fetch_events` **恒 raise**（`realtime.py:658`） | **已永久下线**，用户看到被 `_cached_block` 缓存的"数据源暂时不可用"（`stock.py:238-243`，600s） | 表已存在（`models.py:210`），**推荐补写入方**（同步管线落库）；若决定下线，则改 `panels.py:187` 顺序让 `:194-195` 的显式 `unavailable` 可达，且前端事件卡固定文案"公告功能未开放" |
| **北向持股** | 无本地表/无落库 | **无写入方**（`panels.py:105` 直接调 `fetch_north_holding`） | 恒 raise（`realtime.py:505`） | **已永久下线** | 同上：补有界数据源落库，或前端卡显式标注"已下线" |
| **ETF 搜索** | `instrument` 表 **0 条 etf**（实测仅 `('stock',5552)`） | **无写入方**：`akshare_adapter.py:289` 把目录**硬编码** `instrument_type="stock"`；`ingest/__main__.py:51` 只 upsert `fetch_stock_list()` | 无兜底（已移除远端目录） | **已永久下线** | 在同步流程把 `E.build_catalog()`（`data/etf.py:381`）的 ETF 也 upsert 进 `instrument`；否则 `/portfolio/search`、`datacenter.py:812-821/959-964` 的 etf 分支全空 |

> 三条共同点：**功能被下线，但对外文案统一是"数据源暂时不可用"**（`stock.py:240`），用户与运维都无法区分"暂时"与"永久"。

---

## 5. 补齐任务列表（按实现顺序）

| 任务 | 优先级 | 依赖 | 影响文件 | 验收 |
|---|---|---|---|---|
| **T01 恢复前端可构建（契约对齐）** | **P0** | 无 | `frontend/src/types/p1.ts`（回滚 `signal_strength`→`risk`、`strong_signal`→`high_risk`）或连同 `pages/Screener/*` + 后端全链路原子改名 | `cd frontend && npx tsc --noEmit` **RC=0** |
| **T02 修后端回归 + 测试治理** | **P0** | 无 | `backend/app/api/v1/export.py:44`；`backend/app/api/v1/screener.py:402`（`date=None` 致 `_log_run` 静默丢审计）；`tests/test_write_endpoints_smoke.py:538-552`（改断言）、`:57`（补 `POST /api/v1/notify/stream-ticket`） | `pytest tests/test_write_endpoints_smoke.py tests/test_read_endpoints_rbac.py -q` 相关用例转绿 |
| **T03 静默下线落地（需产品决策）** | **P0** | 需 §6 拍板 | 公告：`panels.py:187` / `realtime.py:658` / 同步管线写入 `news_announcement`；北向：`realtime.py:505`；ETF：`akshare_adapter.py:289`+`ingest/tasks.py:45`+`portfolio.py:68` | 三条各有明确归属：要么有数据，要么前端显示"已下线"而非"暂时不可用" |
| **T04 后端"部分修复"收口** | **P1** | 无 | `ml/monitor.py:113`（单版本化）；`core/compute_guard.py:15`（有界排队）；`cache/swr.py:131-132`（降级结果短 TTL / `valid=` 谓词） | 新增/回归测试覆盖三条路径 |
| **T05 契约 / 配置 / 卫生收尾** | **P1** | **T01** | `.env.example`（补 `FEATURE_VERSION`/`METRICS_REQUIRE_AUTH`/`WARM_OVERVIEW_ON_STARTUP`/`AQP_PANEL_BLOCK_TIMEOUT`）；`types/datacenter.ts:22`+`DataCenter/index.tsx:552`；`types/p1.ts`（`flow_status/status`）；`watchlist.py:152`、`datacenter.py:286/307-318`、`RequireAuth.tsx:24`（孤儿）；`useWatchlistStore.ts:3`、`api/market.ts:8`（注释）；`pages/Etf*`（消费新契约）；`types/stock.ts:393` | `tsc` 0 error；grep 孤儿=0；`.env.example` 覆盖全部新开关 |

---

## 6. 待明确事项（需用户拍板）

1. **信号强度字段名**：是"回滚 p1.ts 保持 `risk`"（零风险、但与产品"非风险"意图不符），还是"全链路改 `signal_strength`"（正确但需前后端+DB 快照列同步）？**当前 p1.ts 的现状两者都不是**，必须选一个。
2. **公告数据源**：补同步管线写入 `news_announcement`，还是永久下线（前端显式标注）？若补，公告源用哪个（无界 AKShare 已被否）？
3. **北向持股**：同上——补有界源落库，还是前端显式下线？
4. **ETF 搜索**：是否同意把 `build_catalog()` 的 ETF 落 `instrument` 表（会新增约千行）？还是恢复远端目录兜底（但违背"请求路径不抓远端"的本轮原则）？
5. **降级缓存策略**：允许 degraded 结果短 TTL（≤15s）缓存以止血，还是要求"降级一律不缓存"（会放大外部源压力）？
6. **提交拆分**：92 个改动仍全部未提交；是否先按主题（`fix(sse)`/`perf(market)`/`refactor(features)`/`feat(types)`）拆提交再收尾？否则单个半成品回滚会连带整批。

---

## 7. 与前序报告的差异（复核后修正）

1. **【最重要】前序"前端 `tsc --noEmit` 0 error、vite build 通过"已失效。**
   前序三份报告均测于 19:15–19:55；`p1.ts` 于 **20:12** 被改。**我实测 `npx tsc --noEmit` = RC 2，2 个 error**：
   - `src/pages/Screener/index.tsx(223,9)`: `ScreenerItem[]`(from p1) 与本地 `ScreenerItem[]` 不兼容——`risk` missing；
   - `src/pages/Screener/index.tsx(358,19)`: stats 类型不兼容——`high_risk` missing（p1 写的是 `strong_signal`）。
   → 结论：**当前工作区前端无法通过 tsc 门禁**。请在 `REVIEW-SUMMARY.md` 的"tsc 0 error"处加注时间戳。

2. **前序 B8/P0#10① 的描述需修正。**
   前序称"前端说明文字写的是 weak/neutral/strong，实际返回 low/mid/high"。复核：**UI 层（`index.tsx:81-85`、`FilterPanel.tsx:16-21`、`StatsCards.tsx:198-200`）用的全是 `risk`/`low/mid/high`**，只是**label 文案**改成了弱/中/强；真正把 `weak/neutral/strong` 落到**类型层**的只有 20:12 的 `p1.ts`，而它的 `ScreenerItem` **无消费方**（`index.tsx` 有自己的本地 `ScreenerItem:45-58`）。所以前序"前端已按新口径消费"**不成立** —— 现状是"类型悬空 + 构建断裂"。

3. **前序把"18 条半成品"的 B8 列为"建议后端改名"。** 复核后发现 20:12 已有人**只改了类型文件**、未改后端、未改消费点 → 这不是"未改完"，而是**制造了新的 P0（构建断裂）**。故本报告新增 `H-01/H-02`，并把它列为**最高优先级**。

4. **数值/引用复核一致的项（确认前序正确）**：`export.py:44` 500（H-03）、`test_write_endpoints_smoke.py:538` 必然失败（H-04）、`news_announcement` 0 行且无写入方、`instrument` 0 etf、`swr.py:129-132` 降级被缓存、`compute_guard.py:15` 未排队、`monitor.py:113` 全版本混读、`realtime.py:505/658` 恒 raise、`etf.py` 新字段前端 0 消费（`git status` 确认 `pages/Etf*` 未列）。

5. **补充证据（前序未点明）**：
   - `screener_workbook`（`core/excel.py:44`）对 `date=None` **不崩**（`str(v)`），故 export 崩点确为 `export.py:44`，非 workbook。
   - ETF 无落库的**根因**是 `akshare_adapter.py:289` 硬编码 `instrument_type="stock"` + `ingest/__main__.py:51` 只 upsert 股票目录（前序仅指出"0 条 etf"，未给根因）。
   - `panels.py:194-195` 的显式 `unavailable` **不可达**（被 `:188` 的 raise 吞掉）——作者意图与实现矛盾。
   - `_cached_block`（`stock.py:243`）会把 `unavailable` 也缓存 600s，与 `swr` 的降级缓存是**同一类问题**，前序只提了 `swr`。
   - 后端 grep `"refreshing"` **零匹配**，确认 `types/datacenter.ts:22` 为纯残留（前序 B7/D1 判断正确，此处补证据）。

---

## 8. 复核方法与局限

**已执行**：`git status --short`（60 M + 32 ??）；`Read` 15 个关键文件；`Grep` 全项目（`signal_strength|strong_signal|high_risk`、`ScreenerItem`、`news_announcement`、`fetch_events|north`、`instrument_type`、`refreshing`、`_etf_names|_dir_size|_refresh_overview`）；只读 SQLite（`news_announcement` 行数、`instrument` 类型分布）；`npx tsc --noEmit`（RC=2）。

**未执行（避免越界/分工）**：未运行全量 pytest；未启动后端；**未修改任何业务源码**（本文件写在 `docs/audit-2026-09-15/`）。

**"未证实"标注**：`news_announcement`、北向持股两条，我以"**全项目 grep 无 INSERT/upsert**"判定"无写入方"，而非仅凭"当前 0 行"——若存在仓库外脚本写库则结论需修正，标注为**未证实（仓库范围内确认为无写入方）**。
