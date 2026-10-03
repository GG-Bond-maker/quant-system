# AQP 现状核实报告（独立取证，不采信任何既有报告）

- 核实时间：2026-10-03 01:30–01:45（周六，休市日）
- 核实方式：只读取证 —— SQLite 只读查询、parquet 文件读取、文件系统 mtime、源码 grep、**直接导入并调用生产函数**、运行日志
- 未修改任何源码与数据
- 前置事实：**后端(8000) 与前端(5173) 当前均未运行**，仅 Redis(6379) 在监听 ⇒ 所有"HTTP 端点实际返回什么"一类的行为**未做运行时验证**，下文已逐条标注

---

## 0. 环境与取证基底

| 项 | 实测值 |
|---|---|
| 监听端口 | 仅 `127.0.0.1:6379`（Redis）；8000 / 5173 无监听 |
| 主数据库 | `data/sqlite/aqp.db`，27 张表 |
| `data_jobs` | 20 行 |
| `trade_calendar` | 6543 行，范围 **2000-01-04 ~ 2026-12-31**，**2027 年 0 行** |
| `watchlist` | **0 行** |
| `app_state` | 8 个键，含 `daily_reports`（10 份日报） |

---

## 1. 已证实的缺陷（附证据）

### F-1【高】daily_pipeline 连续 4 个交易日失败，预测链路已中断
- **证据**（`data_jobs` 表原始行）：

| trade_date | 周 | job_type | status | step | error_message |
|---|---|---|---|---|---|
| 2026-09-23 | 三 | daily_pipeline | FAILED | validate | `validate failed: 昨有今无 28/29 只（阈值 5%）` |
| 2026-09-28 | 一 | daily_pipeline | FAILED | validate | `昨有今无 28/29 只（阈值 5%）` |
| 2026-09-29 | 二 | daily_pipeline | FAILED | validate | `昨有今无 24/25 只（阈值 5%）` |
| 2026-09-30 | 三 | daily_pipeline | FAILED | validate | `昨有今无 1/1 只（阈值 5%）: 000007` |

- **连带证据**：`data/parquet/predictions/` 分区为 `20260914 / 15 / 16 / 17 / **20260930**`，中间 **09-18~09-29 共 9 个交易日无预测数据**；`date=20260930.parquet` 的 mtime 是 **2026-10-02 23:30**（事后补写）。
- **影响**：选股/预测类功能的数据实际停在 09-17，09-30 那份是 10-02 补的。这是当前**最实质的数据缺口**。
- **不确定性**：`昨有今无` 的具体成因（数据源缺量 vs 校验阈值过严）未深入，需看 `validate` 实现与当日原始抓取量。

### F-2【高】数据落盘严重滞后于交易日
- **证据**：`daily_bar/symbol=000001.SZ/year=2026.snappy.parquet` mtime = **2026-10-01 21:54**（国庆假期首日），而该交易日是 09-30；`predictions/date=20260930.parquet` mtime = **2026-10-02 23:30**。
- **影响**：09-30 当天及其后数日，系统展示的都是过期数据；若此时生成日报/健康判断，结论基于旧数据。

### F-3【高】AI 日报在周日生成并自称"交易日"（原报告属实）
- **证据**：`app_state.daily_reports` 共 10 份，日期为 09-03/04/07/14/18/23/**27(周日)**/28/29/30。09-27 那份 `generated_at=2026-09-27T16:18:53`，markdown 原文：
  ```
  ## 一、数据面
  - 交易日：2026-09-27
  - 行情最新日期：2026-09-24（参考标的 000001.SZ）
  - 预测最新日期：20260917（共 262 期 predictions）
  - 近期流水线：2026-09-27 FAILED@-；2026-09-26 SUCCESS@-；2026-09-23 FAILED@validate
  ```
  tips：`predictions 落后于当日（最新 20260917）…`
- **影响**：休市日产出"交易日"日报，并把周末的作业记录当成流水线状态来源。
- **更正**：09-30 那份日报说"行情最新日期 2026-09-28"——**这句在当时是对的**（09-30 数据是 10-01 才落盘），不是日报的 bug。

### F-4【高】autoSync 代码确无交易日门禁
- **证据**：`services/sync_service.py:648-652`
  ```python
  hhmm = datetime.now().strftime("%H:%M")
  if hhmm >= cfg["time"] and not _auto_sync_today_done():
  ```
  判据只有"到点 + 当日未跑过"，**无任何 `is_trade_day` 调用**。
- `backend/data/.auto_sync.json` = `{"enabled": true, "time": "15:45", "last_run_date": "2026-09-03"}`。
- `_record_sync_job` 的 `trade_date` 写 `date('now','localtime')`（自然日），`sync_service.py:649`。
- **⚠️ 但"已在休市日自动触发"缺乏证据**：日志中 `auto sync triggered at 15:45` **仅有 2026-09-28、2026-09-29 两条**（均为工作日）。09-26(六) SUCCESS 的 `finished_at=15:53:25`、09-27(日) FAILED 的 `finished_at=15:57:59` 与 15:45 触发点相差 8–12 分钟，且 09-19(六) 的错误是 `用户主动停止（非异常，可断点续传）`（耗时 7284 秒）—— 更像**手动触发**。
- **结论修正**：缺陷**存在**（无门禁），但"休市日已被自动同步污染"这一因果链**未获证据支持**。

### F-5【高】ETF 跟踪误差时序：后端产出与前端消费语义错配
- 后端 `api/v1/etf.py:1106-1107`：`e_ret = (e_close / base_etf - 1) * 100` ⇒ `points[].etf` 是**累计收益率%**。
- 前端 `pages/EtfDetail/index.tsx:194-195`：
  ```js
  const e = (points[i].etf / (points[i - 1].etf || 1) - 1) * 100;
  ```
  把累计收益率当**价格**做除法。算例：累计 0.5% → 1.0%（真实日收益约 +0.5%）被算成 **+100%**。
- 后端自己在 `:1113-1119` 用 `e_close / prev_e` 算出了正确的年化跟踪误差 `te` ⇒ 同一语义出现两套数字。

### F-6【高】watchlist 表零写入
- `watchlist` 表 **0 行**；全仓 grep 无任何 `INSERT INTO watchlist` 或等价写入语句。
- 唯一读取点 `api/v1/alerts.py:304`，空集合静默返回。

### F-7【中高】设置页"量化引擎"参数是死控件
- `commission_pct` / `slippage_pct` 全仓仅 6 处命中：`app_settings.py:64,65`（默认值）、`:209,210`（入参定义）、`frontend/src/api/settings.ts:19,20`（类型）、`pages/Settings/index.tsx:156,535-543`（UI）。
- **后端计算侧零引用** ⇒ 改了不生效。

### F-8【中高】交易日历 2027 年无数据
- `trade_calendar` max = `2026-12-31`，`WHERE trade_date >= '2027-01-01'` = **0 行**。
- 注：该表**只存交易日**（6543 行全部 `is_sh=1`），因此"某日期不在表中"= 休市，设计正确。实测 09-26(六)、09-27(日)、10-01、10-02、10-03 均不在表；10-08(四) 在表 ⇒ **国庆休市识别正确**。

### F-9【中】交易时间判断实现分散（"8 套"基本属实）
实测函数定义 **9 个**（8 个后端文件 + 1 个前端）：

| 位置 | 函数 | 是否有收盘截断 |
|---|---|---|
| `domain/calendar.py:39` | `is_trade_day` | 无（纯集合判断） |
| `domain/calendar.py:70` | `last_completed_trade_day` | **有** `MARKET_CLOSE_CUTOFF=(15,30)` |
| `data/parquet_store.py:560` | `today_trade_date_or_last` | **无** |
| `data/parquet_store.py:551` | `_weekend_rewind` | 降级兜底（不认节假日） |
| `data/kpi_series.py:668` | `_is_trading_day` | 另写一份 `(15,30)`（:709） |
| `data/quality.py:562` | `load_trade_days` | 直连 SQLite |
| `orchestrator.py:703` | `is_trade_day_checked` | — |
| `orchestrator.py:833` | `_last_completed_trade_date` | 名不副实（无截断） |
| `api/v1/alerts.py:729` | `_is_market_open` | 注释明写"工作日 09:15-15:05" |
| `frontend/src/hooks/useMarketSession.ts` | `isMarketOpen` | 注释明写"周一~周五 09:15–15:05" |

**实测调用分布**：
- `last_completed_trade_day`（有截断）仅 3 处：`screener.py:83`、`sync_service.py:273`、`:360`
- `today_trade_date_or_last`（无截断）**13 处**：`etf.py:122`、`market.py:196/294/1006/1090/1182/1309`、`stock.py:207/270`、`portfolio.py:175`、`watchlist.py:278`

⇒ 交易日的**盘前/盘中**，market/stock/etf/portfolio/watchlist 会把"今天"当最新交易日，而 sync/screener 认为是上一交易日。

### F-10【中】休市日行为实测：正确
直接导入生产函数实测（2026-10-03 周六 01:42）：
```
is_trade_day(2026-10-03) = False
is_trade_day(2026-10-08) = True
today_trade_date_or_last()                    = 2026-09-30
last_completed_trade_day(cal)                 = 2026-09-30
last_completed_trade_day @ 2026-10-08 09:00   = 2026-09-30   ← 盘前不返回今天 ✅
last_completed_trade_day @ 2026-10-08 16:00   = 2026-10-08   ← 收盘后返回今天 ✅
```
⇒ **休市日与盘前的回退逻辑是对的**，交易日历本身没有问题；问题在"哪些模块用了哪套"。

---

## 2. 与既有报告/先前结论**不一致**之处（以现状为准）

| # | 先前结论 | 现状核实 | 依据 |
|---|---|---|---|
| D-1 | 「周六 SUCCESS ⇒ 健康灯绿灯，掩盖 09-25 数据缺失」 | **不成立（当前）**。健康灯取**全局最新一条**（`datacenter.py:698-700`，不按 job_type 过滤），实测最新为 **09-30(三) daily_pipeline FAILED** ⇒ 显示 yellow +「最近同步异常：ValueError: validate failed…」 | `datacenter.py:698-700`；`data_jobs` 按 `finished_at` 排序 |
| D-2 | 「autoSync 休市日照跑」已实证 | **代码缺陷属实，但"已发生"证据不足**：日志仅 09-28/09-29（工作日）两条触发；周末三条作业完成时间与 15:45 不符、且含"用户主动停止" | `backend-run-0928e.log`、`backend-run-0929b.log`；`.auto_sync.json` |
| D-3 | 「`stale_days` 用自然日差，10-08 必误报『行情已 8 天未更新』」 | **不成立**。代码为 `if stale_days > 5 and health == "green"`（`datacenter.py:727-731`）——**必须健康灯为绿才降级**；当前 health 非 green ⇒ 不会触发 | `datacenter.py:727-731` |
| D-4 | 「节假日表覆盖到 2026-12-31」 | 数值属实，但**性质描述不准**：该表是**交易日历（只存交易日）**而非节假日表；且它**能正确识别国庆休市**（10-01~10-03 不在表） | `select min/max/count` + 逐日查询 |
| D-5 | 「8 套交易时间实现」 | 实测 **9 个函数定义**（8 后端 + 1 前端），说法基本属实；但**休市日两套口径输出一致**（都返回 09-30），"必然给出不同今天"仅在**交易日的盘前/盘中**成立 | 实际调用结果（F-10） |
| D-6 | 「口径分裂导致每交易日 00:00–15:30 必然给出不同的今天」 | **部分成立**：休市日一致；仅交易日的 00:00–15:30 分裂 | 同上 |
| D-7 | 先前报告**完全未提** daily_pipeline 连续失败 | **新增**：09-23/28/29/30 连续 FAILED，predictions 缺 9 个交易日 —— 这是当前最严重的问题 | F-1 |
| D-8 | 「AI 日报 09-30 说行情最新 09-28」若被当作 bug | **不是 bug**：09-30 数据是 10-01 21:54 才落盘，日报生成于 09-30 18:37，当时确实只有 09-28 | 文件 mtime |

---

## 3. 无法从现状验证的事项（如实说明）

| # | 事项 | 为什么无法验证 | 需要补充什么 |
|---|---|---|---|
| U-1 | 所有 HTTP 端点的实际响应（健康灯渲染值、`/market/overview` 的 `data_date`、降级态字段） | **后端未运行**（8000 无监听） | 启动后端 + 有效 JWT（`ALLOW_ADMIN_TOKEN_LOGIN` 已关，需 `POST /auth/login`）后实测 |
| U-2 | 前端在休市日的实际展示（是否显示"已休市"、涨跌分布是否 unavailable） | **前端未运行**（5173 无监听），且无当日浏览器抓包 | 启动前端 + 浏览器验证；或至少 `tsc --noEmit` + 静态走查 |
| U-3 | autoSync 在国庆 10-01~10-07 是否真会自动触发 | 当前无该时段运行日志；触发需后端持续运行到 15:45 | 观察 10-01~10-07 的 `backend-run-*.log` 中 `auto sync triggered` |
| U-4 | `昨有今无` 校验失败的根因 | 只看到校验结果，未看到当日原始抓取量与数据源返回 | `validate` 实现 + 09-23/28/29/30 的抓取日志 |
| U-5 | Redis 内缓存键的实际内容与 TTL | Redis 在跑但未连接取证（避免干扰生产缓存） | 只读 `SCAN` + `TTL` 抽样 |
| U-6 | 前端 `useMarketSession` 轮询在假期的实际请求量 | 需运行时抓包 | 浏览器 Network 面板或后端访问日志 |

---

## 4. 结论

1. **交易日历本身是正确的**，休市识别、盘前回退实测无误；真实风险在于**13 处调用点用了无收盘截断的 `today_trade_date_or_last`**。
2. **当前最严重的问题是数据链路**：daily_pipeline 连续 4 个交易日 `validate` 失败，predictions 缺 9 个交易日，数据靠事后补写（09-30 数据 10-01 才落盘）。这比"休市日时间边界"更影响正确性。
3. **autoSync 无交易日门禁是代码事实**，但"已在休市日造成污染"缺乏证据，不应作为已发生的事故陈述。
4. **先前报告中 3 条结论与现状不符**（D-1 / D-2 / D-3），应避免按旧结论决策。
