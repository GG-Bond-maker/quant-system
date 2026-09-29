# B7a · API 路由第一部分（读端点为主）审核报告

- 批次：B7a（`docs/chatgpt-full-review-prompt.md` §3 B7a）
- 审核范围：`backend/app/api/v1/{auth,market,stock,screener,etf,report,research}.py`
  + 共用件 `backend/app/services/{market_service,stats_cache}.py`
- 运行环境：`backend/.venv/Scripts/python.exe`（Python 3.11.15）；git HEAD `f7c9410`
- 审核方式：逐文件静态通读 + FastAPI `TestClient` 内存真打端点（隔离 `DATA_ROOT/SQLITE`，
  全部探针脚本置于 `backend/.tmp_testrun/`，**未修改任何源码、未写仓库 `data/`**）
- 纪律：每条标「确定」的问题都附真实命令与输出片段；无法复现的标「疑似」并写明触发条件。

---

## 0. 结论摘要

| 严重度 | 条数 | 主题 |
|---|---|---|
| P1 | 5 | ETF 三端点外部源异常逃逸（50000）；北向资金口径错（把南向算进北向）；选股榜 universe 缺失时静默不过滤 ST；空榜返回 `ok`+空数组；日历非法日期参数 → 裸 50000 |
| P2 | 6 | 降级 reason 外泄内部异常串；全空涨跌分布被标 `ok`（连带情绪"中性 50"）；`pl.concat([])` 崩溃；`ERR_DATA_SOURCE`/`ERR_EXPR_INVALID` 两个契约码全仓不可达；`/research/experiments` 静默截断；ETF 详情丢 `size_basis`/`quote_status` 披露 |
| P3 | 9 | 死分支 `raw_fallback`；F841 `last`；换手率口径注释误导；overlap 分母硬编码；`stats_cache` 并发 docstring 不符；`/report/daily` 无日期校验且无三态；`/research/lab/yearly` 空数组无披露；`capacity_formula` 命名偏离契约 |
| 有意设计/不报 | 4 | 4 个 market 读端点匿名（文档声明 + `App.tsx:86`）；`ERR_DATA_EMPTY` 承担"无本地数据"；`_finalize_screener_payload` 的 `available==0` 优先级；`quotes_hub` 200 只静默截断（调用方已分片） |

父审核员两条线索均已查证到底（见 §3），并各发现一个**更严重的延伸**：
线索 1 的根因不只在 `flow`，`list`/`hot` 同样裸奔且**还有第二条未包裹调用**（`etf.py:299`）；
线索 2 中 53001 确实全仓不可达，且 `/studio/alpha-eval` 把非法表达式错报成 `51001`。

---

## 1. 逐文件结论

| 文件 | 结论 |
|---|---|
| `api/v1/auth.py` | 未发现 P0–P2 问题。`/login`、`/register`、`/register/status` 无鉴权属登录前设计；`/me` 用 `require_auth`（身份端点，不需要角色）。注册角色白名单、`IntegrityError` TOCTOU 兜底、进程级登录限速均正确。 |
| `api/v1/market.py` | **问题最集中**：4 个读端点无 `require_role`（文档声明匿名，见 B7a-13）；`/overview/daily` 日历非法日期 → 50000（B7a-05）；降级 reason 外泄异常串（B7a-06）；全空涨跌分布报 `ok`（B7a-07）；`raw_fallback` 死分支（B7a-09）。SWR/预算/背景重建设计本身自洽（含"降级载荷无顶层 status 会落地缓存"的注释与无预算重建 builder 兜底）。 |
| `api/v1/stock.py` | `require_role` 齐全（search/profile/kline/panels=viewer，predict=researcher）；`_cached_block` 分块降级与"unavailable 不落地"处理正确。唯一问题：`/{symbol}/kline` 的 `start/end` 只校验形状，日历非法值 → 50000（B7a-05）。 |
| `api/v1/screener.py` | `score` 字段名、`data.status` 三态、`pool_size` 截断前口径、分页上限（page≥1/page_size≤100）、`_fetch_quotes_sharded` 分片规避静默截断——均正确。问题：universe 缺失时静默不过滤 ST/停牌（B7a-03）；空榜 `ok`+空数组（B7a-04）；`date` 日历非法 → 50000（B7a-05）。 |
| `api/v1/etf.py` | `overview/performance/scale/detail` 走 `_cached_etf_payload`/`_run_detail_block` 包裹，实测降级为 `code=0 + status`（正确）；`list/hot/flow` 三个端点**完全没有包裹**（B7a-01）。详情 header 丢失 `size_basis`/`quote_status`（B7a-12）。F841 `last`（B7a-10）。 |
| `api/v1/report.py` | 除 `/daily` 的 `date` 无校验、响应无三态（B7a-16）外未发现 P0–P2；Brinson 节显式披露 `basis`（同池等权基准 + hfq 窗口）值得肯定。 |
| `api/v1/research.py` | 全部端点 `require_role("researcher")`，与 `test_route_permission_contract.py` 契约一致；`compute_slot` 并发闸门齐备。问题：`/stress-test` 的 `pl.concat([])` 崩溃路径（B7a-08）；`/experiments` LIMIT 30 无披露（B7a-11）；`/lab/yearly` 空数组（B7a-17）；overlap 分母硬编码（B7a-14）。 |
| `services/market_service.py` | **发现本批最严重的业务数据错误**：`north_net_today` 把南向（港股通）净流入算进"北向资金"（B7a-02）；且 3 块中 2 块失败仍返回 `status:"ok"`（同一 B7a-02 的延伸）。 |
| `services/stats_cache.py` | `invalidate_stats_cache` 跳过 `logs` 键属有意（数据同步不应清日志统计）。`cached` 的 docstring 称"并发下只算一次"，锁未覆盖计算段（B7a-15，仅性能）。 |

---

## 2. 「路径 × 方法 × 要求角色」清单（B7a 全量，运行时内省产出）

内省命令见 §4 探针 5；`ROLE` 列 = 路由依赖树里 `require_role` 闭包捕获的最低角色。

| 方法 | 路径 | 要求角色 | 处理函数 |
|---|---|---|---|
| POST | `/api/v1/auth/login` | —（登录前） | `auth_login` |
| POST | `/api/v1/auth/register` | —（登录前，开关受配置） | `auth_register` |
| GET | `/api/v1/auth/register/status` | —（登录前，决定是否展示注册入口） | `auth_register_status` |
| GET | `/api/v1/auth/me` | `require_auth`（无角色要求，身份端点） | `auth_me` |
| GET | `/api/v1/market/overview` | **无** | `market_overview` |
| GET | `/api/v1/market/overview/rt` | **无** | `market_overview_rt` |
| GET | `/api/v1/market/overview/daily` | **无** | `market_overview_daily` |
| GET | `/api/v1/market/index/kline` | **无** | `index_kline` |
| GET | `/api/v1/market/quotes` | viewer | `market_quotes` |
| GET | `/api/v1/stock/search` | viewer | `search_stock` |
| GET | `/api/v1/stock/{symbol}/profile` | viewer | `stock_profile` |
| GET | `/api/v1/stock/{symbol}/kline` | viewer | `stock_kline` |
| GET | `/api/v1/stock/{symbol}/panels` | viewer | `stock_panels` |
| GET | `/api/v1/stock/{symbol}/predict` | researcher | `stock_predict` |
| GET | `/api/v1/screener` | viewer | `screener` |
| GET | `/api/v1/screener/stocks` | viewer | `screener_stocks` |
| GET | `/api/v1/screener/watchlist` | viewer | `screener_watchlist` |
| GET | `/api/v1/etf/overview` | viewer | `etf_overview` |
| GET | `/api/v1/etf/list` | viewer | `etf_list` |
| GET | `/api/v1/etf/hot` | viewer | `etf_hot` |
| GET | `/api/v1/etf/performance` | viewer | `etf_performance` |
| GET | `/api/v1/etf/scale` | viewer | `etf_scale` |
| GET | `/api/v1/etf/flow` | viewer | `etf_flow` |
| GET | `/api/v1/etf/detail/{code}` | viewer | `etf_detail` |
| GET | `/api/v1/report/daily` | viewer | `daily_report` |
| POST | `/api/v1/report/daily/generate` | researcher | `regenerate_daily_report` |
| GET | `/api/v1/research/overview` | researcher | `research_overview` |
| GET | `/api/v1/research/experiments` | researcher | `ml_experiments` |
| GET | `/api/v1/research/feature-importance` | researcher | `feature_importance` |
| GET | `/api/v1/research/lab/yearly` | researcher | `ml_lab_yearly` |
| POST | `/api/v1/research/factor-icir` | researcher | `factor_icir` |
| POST | `/api/v1/research/factor-corr` | researcher | `factor_corr` |
| POST | `/api/v1/research/factor-quantile` | researcher | `factor_quantile` |
| POST | `/api/v1/research/cv-folds` | researcher | `cv_folds` |
| POST | `/api/v1/research/optimize` | researcher | `optimize`（`compute_slot`） |
| POST | `/api/v1/research/impact-sim` | researcher | `impact_sim`（`compute_slot`） |
| POST | `/api/v1/research/stress-test` | researcher | `stress_test`（`compute_slot`） |

**未注册端点（死代码排查）**：正则提取 7 个文件的 `@router.*` 装饰器与 `app.routes` 求差集 → **空**，
本批没有"写了但没接线"的端点（详见 §4 探针 5 输出）。

**关于 4 个无角色 market 端点（不报 P0 的理由）**：
`docs/项目文档.md:1424` 把 `GET /api/v1/market/overview` 明确登记为「匿名」；
`frontend/src/App.tsx:86` 注释「市场概览是唯一公开业务页」；`docs/audit-2026-09-18/B7b-api2.md:73`
已把同一组端点判为「有意公开」。故**不作为权限绕过 P0 报**。
但需指出残留风险（B7a-13）：`/market/overview` 与 `/market/overview/daily` 的 `recommend` 块
（含 `pred_score`/`rank_pct`/候选标的与公告）**匿名可取**，即平台核心 ML 信号随公开页一起公开。

---

## 3. 父审核员两条线索的查证

### 线索 1：ETF 外部源 RuntimeError 逃逸 —— 根因定位、影响面、契约缺口

**① 到底是哪一层没把 RuntimeError 收成降级块**

三层链条，缺口在**第 2 层**（API 层少一次包裹）；第 1 层数据层的 try 只覆盖了美股分支：

| 层 | 位置 | 事实 |
|---|---|---|
| 源头 | `backend/app/data/realtime.py:139` | `raise RuntimeError(f"外部数据源请求失败: {type(last).__name__}")` |
| 数据层 | `backend/app/data/etf.py:110`（`fetch_cn_etfs`) → `:86-89`（`_build` 内 `_request`） | 中国 ETF 全量目录走东财 `push2delay`，异常直接上抛 |
| 数据层 | `backend/app/data/etf.py:384`（`build_catalog` 内 `for e in fetch_cn_etfs()`） | **在 try 之外**——同函数 `:400-406` 专门为 `_tencent_us_batch` 写了 `except Exception → us_quotes = {}`（注释：「绝不能让整个 ETF 目录构建失败（曾因 qt.gtimg.cn 超时把 /etf/overview 打成 500）」），但**同款的保护没有给中国目录** |
| 数据层 | `backend/app/data/etf.py:247`（`fetch_flow` → `_cached` → `_build`） | 资金流同样无保护 |
| API 层 | `backend/app/api/v1/etf.py:288-292`（`etf_list` → `_filter_catalog`） | **无 try/except、无 `asyncio.wait_for`** |
| API 层 | `backend/app/api/v1/etf.py:299`（`etf_list` → `E.build_catalog()` 取 options） | **第二处未包裹调用**：即使修好 288 行，这一行仍会 50000 |
| API 层 | `backend/app/api/v1/etf.py:319`（`etf_hot` → `_filter_catalog`） | 无包裹 |
| API 层 | `backend/app/api/v1/etf.py:477`（`etf_flow` → `E.fetch_flow`） | 无包裹 |
| 兜底 | `backend/app/core/errors.py:160-173` | `@app.exception_handler(Exception)` → HTTP 200 + `code=50000`「系统暂不可用，请稍后重试」 |

对照：同文件 `_cached_etf_payload`（`etf.py:87-94`，`wait_for` + `except Exception → fallback`）与
`_run_detail_block`（`etf.py:106-113`）**就是正确的包裹范式**，说明这是遗漏而非设计。

**② 影响面**

- `etf.py` 共用同一外部路径的端点：`/etf/list`（两处）、`/etf/hot`、`/etf/flow` —— **3 个端点全中**。
- `etf.py` 其余端点实测**正常降级**（`code=0`）：`/etf/overview`（`status=None` 但 `data_freshness=degraded`）、
  `/etf/performance`、`/etf/scale`（`status=unavailable`）、`/etf/detail/510300`（`status=degraded`，
  header 块 `unavailable:"未找到该 ETF"`，kline 块 `ok`）。即**影响面是"3 个端点 vs 4 个正确包裹"**。
- `market.py` 同类未包裹路径：`GET /market/index/kline`（`market.py:606-628`）→ `E.fetch_index_kline`
  → `fetch_kline`（`data/etf.py:165-203`）→ `_request`。该路径**全链无 try/except**，腾讯源失败时同样 50000。
  （本次实测腾讯源可达，返回 `code=0` + 真实 bars，见 §5 输出 A；路径存在性为代码确定。）
- `stock.py`：`/kline`、`/profile`、`/panels` 均只读本地 parquet，且 `_cached_block` 逐块捕获一切；
  `/predict` 的内部异常由全局处理器转统一信封。**未发现同类未包裹外部路径**。

**③ `ERR_DATA_SOURCE=51000` 契约缺口**

`rg ERR_DATA_SOURCE backend/app` → 仅 `core/errors.py:96` 一处（定义处）；`rg 51000 backend/app` 同样只有定义。
**全仓（含 tests）无任何抛出/引用点**。前端 `frontend/src/types/api.ts:33` 定义 `DATA_SOURCE: 51000`，
全前端无引用（`rg "ERR.DATA_SOURCE" frontend/src` → 0 命中）→ **前后端双死码**。
契约要求"外部源失败应独立降级"，而现网实现一律降级成 `code=0 + status:unavailable`（ETF/市场块）
或 `code=50000`（本批 3 个端点）。应有产生点：
`etf.py:288/299/319/477`（收口后若要区分"外部源不可用"与"参数错误"）、
`market.py:70/151/491`、`services/market_service.py:60`（`status:unavailable` 的同时给出 51000）。

### 线索 2：`ERR_EXPR_INVALID=53001` 是否全仓不可达

**确认不可达**：`rg ERR_EXPR_INVALID backend/app` → 仅 `core/errors.py:101`（定义处）；
`rg 53001 backend/app` → 0 命中（唯一 53001 引用在 `tests/test_write_endpoints_smoke.py:655` 的
"可接受码"集合）。前端 `types/api.ts:38` 定义 `EXPR_INVALID: 53001`，无使用点。

唯一校验表达式的 API 调用点共 2 处，**都改成别的码**：

1. `backend/app/api/v1/studio.py:341-344` —— `parse_expr` 抛 `ValueError` → 直接
   `return fail(ERR_PARAMS, f"表达式校验失败: {e}")`（40000）。**这是 53001 最应有的产生点。**
2. `backend/app/api/v1/studio.py:243-276`（`/studio/alpha-eval`）—— `gp_miner.evaluate_expr_detail`
   在 `gp_miner.py:229-232` 用 `except Exception: return None` 吞掉 `eval_expr` 的 `ValueError`，
   端点随即抛 `AQPException(ERR_DATA_EMPTY, "表达式无法求值或有效 IC 样本不足（<30 日）")`
   → **非法表达式被错报成 `51001 数据为空`**（实测见 §5 输出 A）。语法错误的建议产生点。

（说明：`/studio/nl-to-factor` 的 `studio.py:217-223` 对 LLM 产物用 `result["valid"]=False` 表达，
不产生错误码，属另一语义，不必改。）

---

## 4. 问题详表

### B7a-01 · P1 · Bug · `etf.py:288/299/319/477`（源头 `data/etf.py:110,247` + `realtime.py:139`）

- **现象**：无外网/东财不可达时 `GET /etf/flow|list|hot` 返回 `code=50000`「系统暂不可用」，
  而不是契约要求的"外部源独立降级"（`code=0` + `status:unavailable` 块或 `51000`）。
- **触发条件**：`push2delay.eastmoney.com` 不可达（本次沙箱实测 `RemoteProtocolError`）——
  离线部署、被墙、节假日源故障均可复现；**每次请求都会真实外呼**（`_cached` 只在成功时回写，
  失败不缓存 → 每个用户每次都付一次 4s 超时 + 50000）。
- **验证**：§5 探针 1 输出 `[viewer] GET /api/v1/etf/flow → code=50000`（list/hot 同）；
  对照 `[viewer] /etf/detail/510300 → code=0 status=degraded`。
- **最小修复方向**（不在本次改动范围，供父审核员裁决）：把 `etf.py:288-299` 与 `:319`、`:477`
  统一收进 `_cached_etf_payload`/`await asyncio.wait_for(...)` 并返回带 `status:unavailable` 的 fallback。

### B7a-02 · P1 · Bug · `services/market_service.py:22-27`（延伸：`:59-62`）

- **现象**：`north_net_today` 把**南向（港股通）**净流入算进"北向资金"。实测 840.0 亿元，
  而当日真实北向（沪股通 0 + 深股通 0）为 **0.0**；840.0 全部来自港股通(沪)+港股通(深)。
- **根因**：`stock_hsgt_fund_flow_summary_em` 返回**双向**行（列 `资金方向` = 北向/南向，
  列 `类型` = 沪港通/深港通），代码只按列名 `"净流入" in c` 取列，**对全表 `sum()`，未过滤 `资金方向=="北向"`**。
- **用户可见影响**：`frontend/src/pages/MarketOverview/MoneyFlowPanel.tsx:113` 直接展示
  「北向净流入」= 该字段；`KpiCards.tsx:48,73` 在主力净流入缺失时把它当卡片主值。
- **延伸（同一函数，`market_service.py:59-62`）**：3 个数据块中"大盘主力"与"行业板块"均失败时
  （实测日志两条 `degraded`），只要 north 有值就返回 `status:"ok"`，**错误率被静默吞掉**、
  `errs` 只在三块全失败时才作为 `reason` 出现 → 前端拿到"ok"的残缺资金块。
- **验证**：§5 探针 6（输出含 `by_direction` 分组与 `build_money_flow()` 返回值）。
- **改法**：`df = df[df["资金方向"] == "北向"]` 后再取列求和；并在 `errs` 非空时返回
  `{"status":"ok","degraded":True,"failed_blocks":errs, ...}`。

### B7a-03 · P1 · Bug · `screener.py:262` + `data/screening.py:71-72,95-104`

- **现象**：当预测日**没有对应的 `universe_daily` 分区**（或该日分区为空）时，
  `filter_universe(..., require_universe=False)` 直接跳过 universe join，
  ST/停牌/板块过滤**整段静默失效**，榜单仍以 `status:"ok"` 返回，`coverage.ratio=1.0`，无任何降级披露。
- **实测对比**（同一份 `predictions`，仅 universe 分区有无之差）：
  - 有 universe（`000001.SZ` 标 `is_st=True`）：`count=1`，ST 股被正确剔除；
  - 无 universe 分区：`count=2`，**ST 股出现在推荐榜**，`status=ok`、`reason=null`。
- **为什么是真缺陷**：`data/screening.py:82-88` 的注释明确把"universe join 静默失效"定义为
  「未校验的榜单被当成已校验结果」（2026-09-14 已修路径拼装 bug）；而调用侧 `require_universe=False`
  让"分区缺失"这一分支仍然静默通过。触发场景现实：晚间流水线的 predictions 先于 `universe_daily` 落盘、
  或某日 universe 同步失败（已知台账中 manifest 与磁盘不一致的正是 `universe_daily`）。
- **验证**：§5 探针 8。
- **改法**：`_build_items` 改 `require_universe=True` 并把 `ERR_DATA_EMPTY` 走 `_unavailable_body`
  （现有 `screener.py:401-411` 分支即可复用），或在 `coverage`/`freshness` 里显式披露 `universe_missing`。

### B7a-04 · P1 · 一致性 · `screener.py:305-310`

- **现象**：`total == 0`（候选池筛完为 0）时返回 `status:"ok"` + `items: []` + `reason:"no_matching_signals"`，
  与 P0 契约块第 3 条「空榜的终态必须是 `unavailable`（不能是 `ok` 空数组）」直接冲突，
  也与本文件自己的规则注释（`screener.py:404-407`「空榜终态必须是 unavailable（前端按该状态渲染空态）」）冲突。
- **必然触发路径**：`GET /api/v1/screener?board=bse` —— `bse` 板块池为 0 是**需求**（P0 块 §二.5），
  `/market/quotes` 之外它是最稳定的复现；实测 `code=0 status=ok count=0 items=0`。
- **对照**：`available == 0 && total > 0` 分支（`:311-316`）正确返回 `unavailable/market_data_missing`。
- **验证**：§5 探针 2 第 1 节。
- **备注**：若产品口径认为"当日无匹配信号"算正常终态，则应修订契约文档并统一 `reason`；
  当前代码与契约二者必有一处要改。

### B7a-05 · P1 · Bug · `stock.py:167-168`、`market.py:769-770`、`screener.py:472`

> **✅ 已修（2026-09-21，第 7–8 轮）**：四类调用点统一改用 `core/params.py` 的
> `parse_yyyymmdd`/`parse_iso_date`（**形状与日历双重严格**，非法一律 `ERR_PARAMS=40000`）：
> `stock.py` kline（并前移到缓存查询之前，非法参数不消耗缓存/IO）、`market.py` `/overview`
> 与 `/overview/daily`、`screener.py`。
> **错标路径**也一并修掉：`_build_overview_hist` 改收 `date` 对象，回退最新榜时**显式标注**
> `fallback:"latest"` + `fallback_reason`，且 `trade_date` 恒为**实时块真实所属交易日**、
> 请求日另放 `requested_date`（原为把最新榜错标成请求日期且 `code=0`）。
> 防回归 21 条（`backend/tests/test_param_date_calendar_validation.py`）。
> **自查发现并修正**：初版 `parse_yyyymmdd` 对 7 位输入过宽（`"2026021"` 被切片当成 2026-02-01），已补长度/数字校验。

- **现象**：日期参数**形状合法但日历非法**时，纯参数错误被报成系统故障：
  `code=50000`「系统暂不可用」（HTTP 200，无栈外泄，但语义错、掩盖真实原因）。
- **实测三处**：
  - `GET /api/v1/stock/600519.SH/kline?start=20269999&end=20260101` → 50000
  - `GET /api/v1/market/overview/daily?date=20269999`、`?date=20260230` → 50000
  - `GET /api/v1/screener?date=2026-02-30` → 50000
- **根因**：三处只用正则校验形状（`^\d{8}$` / `^\d{4}-\d{2}-\d{2}$`），随后直接
  `date(int(...), int(...), int(...))` / `date.fromisoformat(...)`，`ValueError` 未被捕获。
- **附带（错标而非报错）**：`GET /api/v1/market/overview?date=20269999` → `code=0`，
  实测返回**最新一期推荐榜**却把 `trade_date` 标成 `20269999`（`market.py:820-825` 走
  `_build_overview_hist`，其 `date.fromisoformat` 在 `market.py:961-966` 被 except 吞掉，
  只 debug 记日志）→ 客户端无法察觉拿到的是别的日期的数据。
- **验证**：§5 探针 9。
- **改法**：三处加 `try: ... except ValueError: raise AQPException(ERR_PARAMS, ...)`；
  compat 路径应显式报 `ERR_PARAMS` 而不是静默错标。

### B7a-06 · P2 · Bug · `market.py:151`（同型 `:491`）

> **✅ 已修（2026-09-21，第 7–8 轮）**：`_build_heat` 与 `_build_ai_stats` 均改为
> **细节只进日志、对外只回固定文案**（heat 另带 `degraded_from:"em_snapshot"` 便于定位来源）；
> 防回归用例桩掉 `_safe_call` 抛含 `RemoteDisconnected` 的异常，断言响应串里不含异常名与细节
> （`backend/tests/test_heat_sentiment_and_probe_truth.py`，2 条）。
> **未做**：`etf.py:539/555/583/669` 与 `market.py:70` 的 `type(e).__name__` 风格统一（原判"同型但不报"，维持）。

- **现象**：降级块的 `reason` 直接拼接内部异常串（含远端主机/协议细节），随 JSON 返回前端。
  实测：`{"status":"unavailable","reason":"_Boom: ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed'))"}`。
- **触发条件**：东财全市场快照失败且本地日线也不可用（`_heat_from_local` 返回非 ok）→ `market.py:147-152`。
- **一致性证据**：同文件 `market.py:217-223` 有一段专门的修复注释——「不要把内部异常串
  （ConnectionError / RemoteDisconnected 等）直接放进 API 响应 —— 实测曾把 … 原样返回给前端」，
  `_build_sectors` 已改成只回固定文案；`_build_heat`(151) 与 `_build_ai_stats`(491) **漏改**。
- **验证**：§5 探针 2 第 3 节（直接调用 `_build_heat()`，桩掉 `_safe_call` 抛异常）。
- **同型但不报**：`etf.py:539/555/583/669` 与 `market.py:70` 只回 `type(e).__name__`
  （无 message/URL），属文件内自洽的既有风格，风险等级低于上者，建议一并统一为固定文案。

### B7a-07 · P2 · Bug · `market.py:86-110,131`（`_heat_payload`）+ `:502-515`

> **✅ 已修（2026-09-21，第 7–8 轮）**：按本条"改法"落地，并在消费端补了第二道闸——
> `_heat_payload` 无有效样本 ⇒ `status:"unavailable"` + 固定 reason（`extra` 的口径字段仍保留）；
> `_build_sentiment` 对 **零样本**（`up+down+flat==0`）也拒绝出分，**绝不推 50/中性**。
> 防回归覆盖 pandas/polars × 空集/全 NaN/全 None、有样本时行为不变、正常广度仍出分与口径披露
> （`backend/tests/test_heat_sentiment_and_probe_truth.py`，13 条）。

- **现象**：涨跌分布输入为**空集/全 NaN** 时，`_heat_payload` 仍返回 `status:"ok"`、
  `up=down=flat=0`、九个桶全 0、`limit_up/limit_down=0` —— 即"看似正常实则为空的数据"
  （P0 块 §二.3 明确把这一类算作缺陷）。
- **必然触发路径（年频）**：`_heat_from_local` 用 `shift(1).over("symbol")` 求前收，
  而 `market.py:119` 只 glob **当年**分区 → 每年**首个交易日**（当年分区仅一行）时 `pct` 全为 null。
  实测：当年分区只有 1 个交易日时返回
  `{"status":"ok","up":0,"down":0,"flat":0,"buckets":{全 0},"total_amount_yi":2.0}`，
  且**连带** `_build_sentiment` 用 up=down=0 推出 `{"status":"ok","score":50,"label":"中性"}` ——
  在完全没有涨跌数据的日子对外宣称"市场情绪中性"。
  同类输入还有东财快照 `涨跌幅` 列整列 `'-'`/NaN（`market.py:142` `dropna()` 后为空）。
- **验证**：§5 探针 7（`[1] 单日分区 heat` / `[2] 由该 heat 推导的情绪` / `[3][4] _heat_payload` 空集与全 NaN）。
- **改法**：`_heat_payload` 里 `if not vals: return {"status":"unavailable","reason":"无有效涨跌样本"}`。

### B7a-08 · P2 · Bug · `research.py:548-551`

> **✅ 已修（2026-09-21，第 7–8 轮）**：按本条"改法"落地（`if not uni_files: raise
> AQPException(ERR_DATA_EMPTY, "本地 universe_daily 为空，无法构建市场基准")`）。
> 端到端用例先**造出 hfq 数据**再断言错误消息含 `universe_daily`——否则两处早退
> 都抛 `ERR_DATA_EMPTY` 会让"只断言 code"的用例假通过。
> **自查记录（测试工程的坑）**：该用例初版把 `path_for_year` 写在 `monkeypatch` **之前**，
> 于是 fixture 落到了**当时的 DATA_ROOT**；本轮因 conftest 已把 DATA_ROOT 重定向到临时目录
> 而未污染生产 `data/parquet/`（已核实：该树近 1 小时 0 文件改动、`600519.SH` hfq 分区
> mtime 仍为 9/17），但顺序已修正并在用例内加断言锁定"fixture 必须落在临时根内"。

- **现象**：`/research/stress-test` 在 `universe_daily` 无任何 parquet 时，
  `pl.concat([])` 抛 `ValueError: cannot concat empty list`（实测确认），未被捕获 → `code=50000`。
- **触发条件**：`daily_bar_hfq` 有数据（通过 `:544-545` 的空校验）但 `universe_daily` 尚未 bootstrap/被清空——
  正是已知台账「manifest 口径与磁盘实际不一致（universe_daily…）」描述的部分数据态。
- **当前生产盘状态**（只读核查）：`universe_daily` 9 个分区、schema 含 `close`，故**当前不会崩**；
  这是一条"部分数据态即 50000"的健壮性缺口，非现网故障。
- **验证**：§5 探针 2 第 4 节（`pl.concat([])` → ValueError）+ 代码路径。
- **改法**：`if not uni_files: raise AQPException(ERR_DATA_EMPTY, "市场基准数据缺失")`。

### B7a-09 · P2 · 死代码 · `market.py:480`

> **✅ 已修（2026-09-21，第 7–8 轮）**：删掉 `else "raw_fallback"`，OK 路径恒为 `"hfq"`
> 并就地写明理由；防回归用例做**源码扫描**（`raw_fallback` 只允许出现在注释里）
> 与 unavailable 分支不得携带 `label_price_basis`（`backend/tests/test_dead_branch_and_empty_data_codes.py`）。
> **同批**：B7a-08 也按本条"改法"落地（`universe_daily` 为空 ⇒ `ERR_DATA_EMPTY`，不再裸 50000），
> 并额外加了**顺序锚点**用例，避免"两处早退都抛 51001"造成的假通过。

- **现象**：`"label_price_basis": "hfq" if hfq_files else "raw_fallback"` 的 `else` 分支**不可达**——
  `market.py:435-437` 已在 `not hfq_files` 时提前 `return {"status":"unavailable","reason":"无后复权行情，无法评估推荐"}`。
- **验证**：`read market.py` 435-437 与 480；`rg raw_fallback backend/app` → 仅此一处（无消费方）。
- **风险**：`label_price_basis` 是「派生指标披露口径」契约字段，留一个永不出现的假口径值会误导后续维护者
  （以为存在"无 hfq 时用 raw 兜底"的降级路径，实际是直接 unavailable）。删掉 `else` 分支即可。

### B7a-10 · P3 · 死代码 · `etf.py:436`（ruff F841，基线 5 条之一）

- **现象**：`last = bars[-1]` 赋值后从未使用（`ruff check --select F` 唯一命中）。
- **判断**：**疑似漏用**而非纯残留。紧邻注释（`:437`）写「份额(股) = 规模(元) / 最新收盘价」，
  但 `:438-439` 用的是目录里的 `e["price"]`（实时快照价）→ 估算份额 = 最新规模 / **盘中价**，
  而 `:445` 用 `b["close"] * shares` 反推历史各日规模。若按注释意图用 `last["close"]`（K 线最新收盘），
  口径才与"规模序列"同日频自洽；且 `e["price"]` 为 None 时（目录行情缺失但 K 线正常）
  该 ETF 会被 `continue` 整只跳过。
- **验证**：`ruff check app/api/v1/etf.py --select F`；`read etf.py` 430-450。
- **风险/建议**：属估算口径（响应已用 `note` 披露"估算口径：最新份额 × 历史收盘价"），
  建议明确两者取哪个并删除死变量，避免下次改动时无人知道该用哪个价。

### B7a-11 · P2 · 一致性 · `research.py:244`

- **现象**：`GET /api/v1/research/experiments` 的 SQL 硬编码 `ORDER BY id DESC LIMIT 30`，
  响应是**裸 list**（`response_model=APIResponse[list]`），**没有 `total`/`truncated` 字段**，
  而 docstring（`:232`）写的是「实验追踪：model_registry **全部**实验」。
- **触发条件**：`model_registry` 行数 > 30（本仓即有 72 条：
  `backend/scripts/purge_legacy_models.py:5`「model_registry 有 72 条记录」、
  `backend/tests/conftest.py:8`「model_registry 72 行，全是测试垃圾」），
  前端"候选四维对比"会静默丢掉更早的实验且用户无从察觉。
- **验证**：`rg "LIMIT 30" backend/app/api/v1/research.py`；响应结构见 §5 探针 5 清单（list 模型无状态位）。
- **改法**：加 `limit` 页码参数并在信封 `data` 内改包 `{total, items}`，或至少回 `X-Truncated` 语义字段。

### B7a-12 · P2 · 一致性 · `etf.py:507-530`（对照 `data/etf.py:422-423`）

- **现象**：`data/etf.py:422` 给美股 ETF 行显式加了 `size_basis = f"亿美元 × USD_CNY_RATE={fx}"`
  与 `quote_status`，但 ETF 详情 header 块（`etf.py:517-530`）**只透传 `size_yi`，丢弃这两个字段**；
  详情页 `frontend/src/pages/EtfDetail/index.tsx:425,591` 直接用 `header.size_yi` 展示"XX 亿"。
- **契约影响**：美股 ETF 的 `size_yi` 是**平台按配置汇率折算**的自算值（非行情源披露规模），
  列表响应披露了 `size_basis`、详情响应不披露；且 `frontend/src/types/etf.ts` 未声明 `size_basis`，
  全前端 `rg size_basis` → **0 命中**（该披露字段目前无任何消费方）。
- **验证**：`rg size_basis|quote_status frontend/src`（仅 `quote_status` 有消费）；`read etf.py` 507-530。
- **改法**：header 块透传 `size_basis`/`quote_status`，前端在美股 ETF 规模旁标注"按汇率折算"。

### B7a-13 · P2 · 一致性（线索/趋势，非"权限绕过 P0"） · `market.py:606/707/749/805`

- 4 个 market 读端点无 `require_role`，**这是本批唯一一组缺失鉴权的端点**（清单见 §2）。
- **不报 P0 的依据**：`docs/项目文档.md:1424` 登记为「匿名」；`App.tsx:86`「市场概览是唯一公开业务页」；
  `docs/audit-2026-09-18/B7b-api2.md:73` 已判「有意公开」。
- **残留风险（建议裁决）**：`recommend`（ML 推荐榜：`pred_score`/`rank_pct`/候选标的/公告摘要）
  随 `/market/overview` 与 `/market/overview/daily` 匿名可取；若"公开市场概览"的意图不含 ML 信号，
  应把 `recommend` 块改为需登录（或对该块单列 `status:"login_required"`）。

### B7a-14 · P3 · Bug · `report.py:243`

- **现象**：`overlap = len(set(tops[1]) & set(tops[0])) / k` 的分母是**硬编码 `k=50`**，
  而 `tops[i]` 来自 `head(k)`，当某期 predictions 行数 < 50 时实际集合更小 → 重合率被系统性低估
  （分子最多 = 实际行数）。同一段 `mean_*` 用的是 `len(tops[i])`（正确），口径不一致。
- **触发条件**：predictions 分区行数 < 50（小样本调试环境/局部补跑），日报"模型分数迁移"文案失真。
- **验证**：`read report.py` 226-245。

### B7a-15 · P3 · 一致性 · `services/stats_cache.py:17-27`

- **现象**：docstring 称「带 TTL 的进程级缓存；**并发下只算一次**」，但 `fn()` 在**锁外**执行，
  两个并发未命中会各自完整执行一遍（`datacenter` 的调用方 fn 是全表统计扫描）。
- **性质**：仅重复计算，无数据损坏（同 key 后写覆盖前写，值等价）。docstring 与实现不符 → 建议加单飞
  （per-key in-flight future）或修正措辞。

### B7a-16 · P3 · 一致性 · `report.py:453-464`

- **现象**：`GET /api/v1/report/daily`：`date` 参数**完全没有校验**（对照同批 `market.py:807` 用 `^\d{8}$`、
  `screener.py:456` 用 `^\d{4}-\d{2}-\d{2}$`）；实测 `?date=abc`、`?date=2026-99-99` → `code=0` 且
  `data.report = null`，客户端无法区分"该日期没有日报"与"日报尚未生成"；响应也无 `status` 三态。
- **验证**：§5 探针 9（`/report/daily?date=2026-99-99 → code=0`）。
- **改法**：加 pattern + `report is None` 时在 `data` 里给 `status/reason`（如 `unavailable/no_report_for_date`）。

### B7a-17 · P3 · 一致性 · `research.py:585-594`（并 `:571` 声明）

- **现象**：`GET /api/v1/research/lab/yearly` 在"无 predictions 分区""无 daily_bar 分区"
  以及"每年样本不足被 `continue` 跳过"三种情况下都返回 `data: []`（`code=0`），
  **没有 `status`/`reason` 字段**（`response_model=APIResponse[list]`），
  前端热力图无法区分"没有数据"与"数据被样本阈值过滤"。
- **验证**：`read research.py` 580-630；响应模型见 §2 清单。
- **改法**：改包 `{status, items, n_years_filtered}`（同类：`/research/experiments`、`/studio/factors` 也是裸 list，
  但后两者为空时尚可通过 `model_registry`/因子库为空自行判断，优先级更低）。

### B7a-18 · P3 · 一致性 · `data/screening.py:236` vs `:245`（跨层口径注释）

- **现象**：`screening.py:236` `turnover = round(bar["turnover"] * 100, 2)`（小数→百分比），
  但 `:245` 注释写「换手率为小数口径（0.012 = 1.2%），**前端换算百分比展示**」；
  前端 `frontend/src/pages/Screener/index.tsx:606` 实际是 `${it.turnover.toFixed(2)}%` ——**不再乘 100**。
  同文件 `:655` 的注释又写「pct / turnover = 百分比（本地截面的 turnover 是小数，这里 ×100）」（正确）。
  → **当前数值是对的，但 `:245` 的注释是错的**，且与 `:655` 自相矛盾。
- **真实数据核查**（只读）：`data/parquet/daily_bar/symbol=000001.SZ/year=*.parquet` 的 `turnover`
  均值 0.00668、最大 0.0198 → 确为小数口径，故今天 `×100` 正确。
- **风险**：`domain/chip.py:38-43` 明确承认该列存在"百分数/小数"两种口径并用 `mean > 1.0` 启发式兼容，
  而 screener 路径**无条件 ×100** → 一旦上游改为百分数口径，选股榜换手率会整体放大 100 倍。
- **建议**：删掉 `:245` 的错误注释，或抽一个 `normalize_turnover()`（与 chip.py 同判据）供两处共用。

### B7a-19 · P3 · 一致性 · `research.py:147-148`

- **现象**：`capacity_estimate_yi` 的口径披露字段名是 `capacity_formula`，
  而 P0 契约块第 5 条建议 `basis` 或 `kind:"platform"`（`market.py:512-514` 的情绪块就是
  `kind:"platform"` + `basis`）。披露**存在**，仅字段名偏离契约，前端需为每个端点各写一套读取逻辑。
- **验证**：`read research.py` 143-151。

---

## 5. 验证命令与真实输出（摘）

环境准备（三个探针脚本均在 `backend/.tmp_testrun/`，不改源码）：

```
$base='D:\Python_Project\Alpha Quant Platform\backend'
$env:PYTHONPATH="$base;$base\.tmp_testrun"; $env:TEMP="$base\.tmp_testrun"; $env:TMP=$env:TEMP
& "$base\.venv\Scripts\python.exe" "$base\.tmp_testrun\b7a_probeN.py"
```

### 输出 A（探针 1/4：真打端点，`TestClient(app, raise_server_exceptions=False)`）

```
[anon] GET /api/v1/market/index/kline?code=sh000001
    HTTP=200 code=0 status=None        ← 匿名可读（真实 bars: 2025-02-05 …）
[anon] GET /api/v1/market/overview/rt
    HTTP=200 code=0 status=None        ← 匿名可读
[anon] GET /api/v1/market/overview/daily
    HTTP=200 code=0 status=recommend:unavailable   ← 匿名可读
[anon] GET /api/v1/etf/flow
    HTTP=200 code=40100                ← 鉴权层正常（对照）
[viewer] GET /api/v1/etf/flow
    HTTP=200 code=50000  ← B7a-01
[viewer] GET /api/v1/etf/list
    HTTP=200 code=50000  ← B7a-01
[viewer] GET /api/v1/etf/hot
    HTTP=200 code=50000  ← B7a-01
[viewer] GET /api/v1/etf/detail/510300
    HTTP=200 code=0 status=degraded    ← 同文件已包裹的对照
[viewer] GET /api/v1/stock/600519.SH/kline?start=20269999&end=20260101
    HTTP=200 code=50000  ← B7a-05
[anon] GET /api/v1/report/daily  → code=40100
[anon] GET /api/v1/research/overview → code=40100
[anon] GET /api/v1/auth/register/status → code=0（登录前端点，正常）
```

traceback 关键帧（`b7a_probe.log`，证明逃逸链）：

```
File "app/api/v1/etf.py", line 477, in etf_flow
    data = await asyncio.to_thread(E.fetch_flow, _FLOW_FIELD.get(period, "1d"), limit)
File "app/data/etf.py", line 226, in _build   → data = _request(...)
File "app/data/realtime.py", line 139, in _request
    raise RuntimeError(f"外部数据源请求失败: {type(last).__name__}")
RuntimeError: 外部数据源请求失败: RemoteProtocolError
FLOW_RAW: {"code": 50000, "message": "系统暂不可用，请稍后重试", ...}
```

### 输出 B（探针 2：空榜三态 / 降级串 / 53001）

```
/screener?board=bse -> code=0 status='ok' reason='no_matching_signals' count=0 items=0   ← B7a-04
/screener?board=all -> code=0 status='unavailable' reason='market_data_missing'          ← 对照（正确）
POST /studio/factors(非法表达式)  -> {"code": 40000, "message": "表达式校验失败: 表达式含不允许的节点 Attribute: …"}
POST /studio/alpha-eval(未知字段) -> {"code": 51001, "message": "表达式无法求值或有效 IC 样本不足（<30 日）"}
POST /studio/nl-to-factor(LLM 未配) -> {"code": 53000, …}
_build_heat() -> {"status":"unavailable","reason":"_Boom: ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed'))"}   ← B7a-06
pl.concat([]) -> ValueError: cannot concat empty list                                    ← B7a-08
ERR_EXPR_INVALID in app/: 仅 core/errors.py:101（定义处）
ERR_DATA_SOURCE   in app/: 仅 core/errors.py:96（定义处）
```

### 输出 C（探针 6：北向资金口径，`b7a_north_flow.json`）

```json
"by_direction": {"北向": {"sum": 0.0,   "boards": ["沪股通", "深股通"]},
                 "南向": {"sum": 840.0, "boards": ["港股通(沪)", "港股通(深)"]}},
"sum_all_rows": 840.0,
"build_money_flow": {"status": "ok", "north_net_today": 840.0,
                     "main_net_today": null, "sector_flows": []}
```

（即"北向净流入"显示 840.0 亿元，真实北向为 0.0；且 2/3 块失败仍 `status:"ok"` → B7a-02）

### 输出 D（探针 7：全空涨跌分布 → 假 ok，`b7a_heat.json`）

```json
"[1] 单日分区 heat": {"status": "ok", "source": "local", "up": 0, "down": 0, "flat": 0,
                      "limit_up": 0, "limit_down": 0,
                      "buckets": {"b1":0,…,"b9":0}, "total_amount_yi": 2.0,
                      "note": "实时快照不可用，按本地日线 2 只标的计算"},
"[2] 由该 heat 推导的情绪": {"status": "ok", "score": 50, "label": "中性",
                             "kind": "platform", "basis": "平台自研口径：…"},
"[3] _heat_payload(全 NaN pct)": {"status": "ok", "up": 0, "down": 0, …}
```

### 输出 E（探针 8：universe 缺失 → ST 股进榜，`b7a_universe.json`）

```
D1(有 universe，000001.SZ 为 ST): code=0 status=ok count=1 items=[600519.SH score=1.5]  coverage 1/1
D2(无 universe 分区)            : code=0 status=ok count=2 items=[600519.SH, 000001.SZ] coverage 2/2 ratio=1.0
```

### 输出 F（探针 9：日历非法日期参数）

```
/api/v1/market/overview/daily?date=20269999 -> HTTP=200 code=50000
/api/v1/market/overview/daily?date=20260230 -> HTTP=200 code=50000
/api/v1/market/overview?date=20269999       -> HTTP=200 code=0（静默错标为请求日期）
/api/v1/screener?date=2026-02-30            -> HTTP=200 code=50000
/api/v1/report/daily?date=2026-99-99        -> HTTP=200 code=0（report=null）
```

### 输出 G（探针 5：路由内省，节选）

```
--- 源码 decorator 与已注册路由的差集（识别未接线端点）---   （空）
--- B7a 未挂 require_role 的端点 ---
  POST /api/v1/auth/login / register；GET /auth/register/status      （登录前，正常）
  GET /api/v1/market/index/kline                                     ← B7a-13
  GET /api/v1/market/overview/rt / overview/daily / overview         ← B7a-13
```

### 静态检查

```
ruff check app/api/v1/{auth,market,stock,screener,etf,report,research}.py \
           app/services/{market_service,stats_cache}.py --select F
→ backend/app/api/v1/etf.py:436:13: F841 Local variable `last` is assigned to but never used
```

---

## 6. 复核过的"看似问题其实不是"

1. **`/market/overview` 降级载荷无顶层 `status`** —— `market.py:844` 注释已说明（分块各有 status），
   且 `cached_or_build` 的落盘谓词依赖此形状，属有意设计。
2. **`/etf/overview` 降级后外层 `status` 为 `None` 但 `data_freshness=degraded`** —— `_fallback`
   把 status 放在 `today` 块内（`etf.py:248-256`），`today.status=unavailable` 实测存在。
3. **`quotes_hub.quotes_snapshot` 对 >200 只静默截断**（`data/quotes_hub.py:68-69`）——
   两个调用方都已处理：`market.py:655-656` 显式返回 40000；`screener.py:527-563` 分片抓取
   并在 docstring 说明原因。`alerts.py:520` 的调用方属 B7b 范围，建议由 B7b 复核其入参是否可能 >200。
4. **分页上限**：`etf_list`（`page_size le=100`）、`screener/stocks`（1~100 + page≥1）、
   `stock/search`（le=200）、`market/index/kline`（le=800）、`etf/flow`（le=50）、`market/quotes`（>200→40000）
   实测 `limit=1000000000` 均返回 `code=40000`「请求参数错误」，无越界/无静默截断。
5. **`/screener/watchlist` 与 `/etf/*` 的 `require_role("viewer")`** 与 `test_read_endpoints_rbac.py`
   的 `EXPECTED_ROLES` 一致，未发现降级。
6. **`auth.py` 三处无鉴权** —— `/login`、`/register`、`/register/status` 必须登录前可访问；
   注册默认角色被强制收敛在 `viewer/researcher`，`admin` 无法自助注册
   （`auth.py:44-45` 白名单 + `auth.py:154-160` `_default_register_role()` 越权降级 + `:203` 落库）。
7. **`etf_scale` 的"估算规模"** —— 响应 `note` 已披露「估算口径：最新份额 × 历史收盘价，非基金公司披露规模」。
8. **`stock_panels`/`_cached_block` 不缓存 unavailable** —— `stock.py:244-247` 注释与本批实测一致（自愈设计）。

---

## 7. 策略/口径建议（目标 C，仅 2 条，各含改法与验证）

1. **Brinson 基准换成同池外基准（`report.py:170-176`）**
   当前问题：基准是**组合自身持仓的等权组合**（`bench_w` 只覆盖 `close_pd.columns` = 持仓标的），
   行业配置效应因此偏离"相对市场"的通常含义。
   具体改法：基准改用 `universe_daily` 最新截面**全体标的**等权（同 `stress_test` 的市场基准构造方式），
   配置效应才有解释力。
   预期收益：归因结果的配置项从"恒接近 0"变为可解读（量级：配置效应通常 ±0.5%~2%）。
   引入风险：universe 截面缺失时基准不可得 → 需按 `_brinson_section` 既有风格返回 `ok:False` 而非造数。
   验证方式：同一窗口下对比改前/改后的 `summary` 配置项是否随行业权重变化而变。

2. **`north_net_today` 增加"资金方向 + 板块"明细（`market_service.py:22-27`）**
   当前问题：单值字段无法自证口径（正是本次误把南向算进北向的成因）。
   具体改法：修 `资金方向=="北向"` 过滤的同时，响应里附 `by_board`（沪股通/深股通）明细。
   预期收益：口径可自查，前端"北向"卡片可直接展示分项（量级：消除 800+ 亿元级错报）。
   引入风险：字段增加需前端同步（当前 `frontend/src/types/stock.ts:353` 只声明单值，可先加可选字段）。
   验证方式：`build_money_flow()` 返回里断言 `north_net_today == by_board` 之和，且与逐行 北向 求和一致。

---

## 8. 覆盖与限制

- 逐文件通读覆盖：7 个 API 文件 + 2 个 services 文件（`market.py` 967 行、`screener.py` 657 行、
  `etf.py` 777 行、`research.py` 632 行、`report.py` 473 行全部读完）。
- 真打端点：15 + 7 + 7 = 29 次，均使用 `TestClient(app, raise_server_exceptions=False)`
  （判定"裸 500"以响应信封 `code` 为准，见 P0 提示）。
- **未做**：全量 pytest、uvicorn、任何源码修改、任何仓库 `data/` 写入。
- 限制：`etf/list|hot|flow` 与 `market/index/kline` 的"外部源失败"分支利用**沙箱无外网到东财**
  真实触发（`RemoteProtocolError`）；腾讯源在本环境可达，故 `index_kline` 的 50000 分支为
  **代码确定 + 同型路径实测**（未直接观察到该端点自身的 50000）。
- `B7a-03` 的复现基于合成 `predictions/universe_daily`（隔离目录），生产盘当前
  `universe_daily` 有 9 个分区、7 类必需列齐全，故该问题为"分区缺失/错位时触发"。