# AQP 数据处理流程与交易时间边界审查

- **审查日期**：2026-10-03（周六，A 股休市）
- **审查人**：严过关（QA / software-qa-engineer）
- **仓库**：`D:\Python_Project\Alpha Quant Platform`
- **性质**：纯静态代码审查 + 只读取证，**未修改任何源码**
- **证据口径**：每条结论附 `文件路径:行号` + 代码片段；生产数据证据来自只读查询
  `data/sqlite/aqp.db`（`sqlite3.connect('file:...?mode=ro', uri=True)`）与
  `data/.auto_sync.json`

---

## 0. 结论速览

| 严重度 | 条数 | 一句话 |
|---|---|---|
| **P0** | 2 | 休市日的自动同步与 AI 日报把**非交易日**当成交易日展示，生产已有实证 |
| **P1** | 7 | 交易时间判断 8 处口径分裂；盘中/盘前把"今天"当最新交易日；日历过期与空缓存不可自愈；假日误判盘中；自然日差判陈旧；freshness 进长缓存 |
| **P2** | 8 | 口径重复实现、静默截断、僵尸状态残留、缓存白名单靠人工登记 |

**最重要的一条判断**：交易时间（交易日 / 已收盘 / 盘中）在本仓**至少有 8 套独立实现**，
其中只有 `domain/calendar.py::last_completed_trade_day` 一处具备"已收盘"语义（15:30 截断），
而 API 展示链路（`today_trade_date_or_last`）**没有**该语义 —— 这是"口径分裂"的主轴。

---

## ① 交易时间判断实现全景（几套实现？分别在哪？口径是否一致）

### 1.1 实现清单（全仓 grep 结果）

| # | 实现 | 位置 | 数据源 | 是否有"已收盘"截断 | 是否认节假日 | 主要调用方 |
|---|---|---|---|---|---|---|
| 1 | `is_trade_day` / `prev_trade_day` / `next_trade_day` / `last_completed_trade_day` / `range_trade_days` | `backend/app/domain/calendar.py:39-99` | `CalendarData` 快照 | ✅ `MARKET_CLOSE_CUTOFF=(15,30)`（L67） | ✅ | sync_service、orchestrator、evening_routine、screener._freshness |
| 2 | `today_trade_date_or_last` + `_weekend_rewind` | `backend/app/data/parquet_store.py:551-586` | 同上 | ❌ **盘中/盘前也返回今天** | ⚠️ 日历为空时**不认** | market / stock / portfolio / watchlist / etf（5 个 API 模块） |
| 3 | `_is_trading_day` + `should_archive_etf_snapshot` | `backend/app/data/kpi_series.py:668-711` | **直连 SQLite**（绕过 calendar_store），带 `is_sh/is_sz` 过滤 | ✅ 但**另写一份** `(15,30)`（L709），不复用 #1 的常量 | ✅ | ETF 快照归档（读/写双路径） |
| 4 | `_is_market_open` | `backend/app/api/v1/alerts.py:729-735` | 无（仅 `weekday()`） | 盘中 09:15–15:05 | ❌ **只认周末** | 预警调度轮询间隔（30s vs 3600s） |
| 5 | `isMarketOpen` / `useMarketSession` | `frontend/src/hooks/useMarketSession.ts:25-31` | 无（仅 `getDay()`） | 盘中 09:15–15:05 | ❌ **只认周末** | MarketOverview 轮询、StockDetail 休市徽标 |
| 6 | `_last_completed_trade_date` | `backend/app/orchestrator.py:833-842` | 同 #1 | ❌ **名不副实**：今天是交易日就返回今天 | ✅ | 仅 CLI `python -m app.orchestrator` |
| 7 | `load_trade_days` | `backend/app/data/quality.py:561-573` | **直连 SQLite**（第三份） | — | ✅ | 数据质量日历校验 |
| 8 | `refresh_calendar_cache` / `get_calendar` / `set_calendar` | `backend/app/data/calendar_store.py:29-82` | SQLite（唯一合法访问层） | — | — | 以上 #1/#2/#6 的供给方 |

**结论**：口径**不一致**。
- "是否交易日" 有 4 套（#1、#3、#7 三份直连/快照 + #2 的降级路径）；
- "是否已收盘" 有 3 套常量（#1 的 `MARKET_CLOSE_CUTOFF=(15,30)`、#3 的裸写 `(15,30)`、#4/#5 的 `15:05`）；
- "是否盘中" 有 2 套（#4 后端、#5 前端），且**都不含节假日、不含午休**；
- #2（API 展示链路主力）与 #1（同步链路）对"今天"的判定**在 00:00–15:30 之间必然不同**。

### 1.2 节假日表覆盖情况（只读取证）

```bash
sqlite> select count(*),min(trade_date),max(trade_date) from trade_calendar;
(6543, '2000-01-04', '2026-12-31')
sqlite> select is_sh,is_sz,count(*) from trade_calendar group by is_sh,is_sz;
[(1, 1, 6543)]          # 全部 is_sh=is_sz=1，无交易所差异
sqlite> select trade_date,is_sh,is_sz from trade_calendar
        where trade_date>='2026-09-25' and trade_date<='2026-10-15';
2026-09-28/29/30, 2026-10-08/09/12/13/14/15   # 国庆 10-01~10-07 正确排除
```

表**当前有效**（覆盖到 2026-12-31），但**只剩约 3 个月**；刷新只在同步任务前触发
（`services/sync_service.py:197-218` `_refresh_trade_calendar`，由 `_run_incremental`/`_run_repair` 调用），
`evening_routine`、API、前端**都不刷新**。

---

### [P0] 1-A　autoSync 无交易日门禁：休市日照跑全市场同步，并把自然日写进 `data_jobs.trade_date`

**模块**：`backend/app/services/sync_service.py` → `api/v1/datacenter.py`、`api/v1/report.py`

**现象（代码证据）** —— 调度器的触发判据里**完全没有交易日判断**：

```python
# backend/app/services/sync_service.py:668-693
async def auto_sync_scheduler() -> None:
    while True:
        try:
            cfg = _load_auto_sync()
            if cfg.get("enabled"):
                hhmm = datetime.now().strftime("%H:%M")
                if hhmm >= cfg["time"] and not _auto_sync_today_done():   # ← 只有"到点 + 当日未跑过"
                    if not _sync.running:
                        _record_auto_sync()
                        await asyncio.to_thread(
                            lambda: _start_sync_bg("incremental", resume=False))
```

对比同仓的晚间例行**有**门禁（`jobs/evening_routine.py:98-103`）：

```python
today = date.today(); cal = get_calendar()
if not is_trade_day(today, cal):
    _mark("skipped", {"reason": f"{today} 非交易日"}); return
```

而作业终态落库用的是**自然日**：

```python
# backend/app/services/sync_service.py:649-660
"INSERT INTO data_jobs (job_type, trade_date, status, ... ) "
"VALUES (?, date('now','localtime'), ?, ?, datetime('now','localtime'), ...)"
#                       ^^^^^^^^^^^^^^^^^^^^^^  自然日，不是交易日
```

**生产实证**（`data_jobs` 表，附星期）：

```
sync_incremental  2026-09-18 Fri  FAILED
sync_incremental  2026-09-19 Sat  FAILED     ← 周六
sync_incremental  2026-09-26 Sat  SUCCESS    ← 周六
sync_incremental  2026-09-27 Sun  FAILED     ← 周日
daily_pipeline    2026-09-28 Mon  FAILED     ← 流水线有门禁，从不落在周末
```

**触发场景**：每个周六/周日/法定节假日的 15:45 之后（`.auto_sync.json` 实测
`{"enabled": true, "time": "15:45", "last_run_date": "2026-10-01"}`）。

**影响范围**：
1. **状态被污染**：`api/v1/datacenter.py:701-704` 的健康灯取的是
   `SELECT status, error_message FROM data_jobs ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1`
   —— 休市日的 `sync_incremental` 行会直接决定灯色。09-26（周六）`SUCCESS` ⇒
   绿 + "AKShare 连接正常，最近同步成功"；而当天真实的 `daily_bar` 末端停在 **09-24**（09-25 交易日缺失，
   见 09-27 日报"行情最新日期：2026-09-24"），`stale_days=2` 未触发 5 天兜底 ⇒
   **断更被绿健康灯掩盖**。反向地，09-27（周日）`FAILED` ⇒ 黄 + "最近同步异常"，而当日根本不需要同步。
2. **日报污染**：见 1-B。
3. **资源浪费**：每个休市日对全市场 ~2499 只标的跑一遍增量同步（含
   `_refresh_trade_calendar()` 的网络日历拉取），白耗外部源限速配额。
   （好消息：`_run_incremental` 的 target 用 `last_completed_trade_day`，
   `sync_service.py:273`，因此**不会**把未来日期写进 parquet，不造成数据覆盖。）

**建议**：`auto_sync_scheduler` 在触发前补 `is_trade_day(date.today(), get_calendar())` 门禁，
与 `evening_routine` 同口径（非交易日 `_record_auto_sync()` 后直接 `continue`）；
`_record_sync_job` 的 `trade_date` 改为 `last_completed_trade_day(cal)` 而非 `date('now','localtime')`；
`datacenter` 健康灯查询应把 `sync_*` 与 `daily_pipeline` 分开取，且按
`MAX(trade_date)` 而非 `MAX(COALESCE(finished_at,...))` 取（避免休市日作业盖住交易日作业）。

---

### [P0] 1-B　AI 日报在非交易日生成：把周日标成"交易日"，并误报 predictions 落后

**模块**：`backend/app/api/v1/report.py`

**现象（代码证据）** —— `build_daily_report` 用 `date.today()` 且**无任何交易日判断**：

```python
# backend/app/api/v1/report.py:259-283
def build_daily_report(day: date | None = None) -> dict:
    day = day or date.today()                       # ← 自然日
    ...
    sections.append({"title": "一、数据面", "lines": [
        f"- 交易日：{day.isoformat()}",             # ← 休市日也会被标成"交易日"
        ...
    ]})
    ...
    if fresh.get("prediction_last") and fresh["prediction_last"] < day.strftime("%Y%m%d"):
        tips.append(f"predictions 落后于当日（最新 {fresh['prediction_last']}），"
                    f"请检查晚间例行/autoSync 是否正常")   # ← 休市日必然误报
```

调用方都没有交易日门禁：`jobs/evening_routine.py:239-253` 的 `startup_catchup()`
在**任意日期**启动即补日报；`main.py:125` 无条件 `create_task(startup_catchup())`。

**生产实证**（`app_state['daily_reports']` 中 2026-09-27 那份，2026-09-27 是**周日**）：

```json
{
 "date": "2026-09-27", "generated_at": "2026-09-27T16:18:53",
 "sections": [{"title": "一、数据面", "lines": [
    "- 交易日：2026-09-27",
    "- 行情最新日期：2026-09-24（参考标的 000001.SZ）",
    "- 预测最新日期：20260917（共 262 期 predictions）",
    "- 近期流水线：2026-09-27 FAILED@-；2026-09-26 SUCCESS@-；2026-09-23 FAILED@validate"
 ]}],
 "tips": ["predictions 落后于当日（最新 20260917），请检查晚间例行/autoSync 是否正常", ...]
}
```

三处事实错误同时出现：① "交易日：2026-09-27"（周日）；② "近期流水线"两条都是
**周末 autoSync 行**（09-26 Sat SUCCESS / 09-27 Sun FAILED，见 1-A）；
③ "predictions 落后于当日"（周日不跑推理，属必然，非故障）。

**触发场景**：后端在周末/节假日启动（`startup_catchup`）、或运维在休市日手动触发日报。
即将到来的国庆 10-01~10-07 长假，只要后端重启一次即复现。

**影响范围**：AI 日报是值班判断"数据面是否健康"的主入口；休市日的误报会让值班把
正常状态当故障排查，反之休市日 `SUCCESS` 也会掩盖真实断更（见 1-A）。

**建议**：`build_daily_report` 增加交易日门禁（非交易日拒绝生成或显式标
`is_trading_day=false` + "休市日无当日产物"）；"近期流水线"过滤掉 `job_type LIKE 'sync_%'`；
"落后"判据改用 `last_completed_trade_day(cal)` 而非 `date.today()`。

---

### [P1] 1-C　`today_trade_date_or_last()` 无"已收盘"截断：盘前/盘中把今天当最新交易日

**模块**：`backend/app/data/parquet_store.py:560-586` → 5 个 API 模块

**现象（代码证据）**：

```python
# backend/app/data/parquet_store.py:560-586
def today_trade_date_or_last() -> date:
    today = date.today(); cal = get_calendar()
    if len(cal) == 0: ...return _weekend_rewind(today)
    try:
        # prev_trade_day 从 d-1 起向前找，故传 tomorrow 以覆盖"今天就是交易日"
        return prev_trade_day(today + timedelta(days=1), cal)   # ← 只要今天是交易日就返回今天
```

对比唯一的"已收盘"语义实现（`domain/calendar.py:70-88`），二者**在 00:00–15:30 必然不同**：

```python
# backend/app/domain/calendar.py:83-88
now = now or datetime.now(); today = now.date()
hh, mm = MARKET_CLOSE_CUTOFF            # (15, 30)
if is_trade_day(today, cal) and (now.hour, now.minute) >= (hh, mm):
    return today
return prev_trade_day(today, cal)
```

消费方把 #2 的结果直接当 `trade_date` 返回：

```python
# backend/app/api/v1/market.py:1090,1106
td = today_trade_date_or_last()
...
data["trade_date"] = td.strftime("%Y%m%d")
```
（`market.py:1006/1182/1309`、`stock.py:207/270`、`portfolio.py:175`、`watchlist.py:278`、`etf.py:122` 同）

**触发场景**：**每个交易日的 00:00–15:00**（盘前 + 早盘 + 午休）。
此时 `trade_date = 今天`，但日频块的实际统计日是上一交易日：

```python
# backend/app/api/v1/market.py:209, 251-255  （_heat_from_local）
last_day, coverage, latest_day, skipped_days = _pick_stat_day(df)
...
"data_date": last_day.isoformat(),
"note": f"实时快照不可用，按本地日线 {last_day.isoformat()} 的 {coverage} 只标的计算"
```

前端把 `trade_date` 当"交易日"展示在标题栏下拉：

```tsx
// frontend/src/pages/MarketOverview/index.tsx:86,110
const today = data?.trade_date ?? '—';
<option value="">交易日 {fmtDate(today)}（最新）</option>
```

**影响范围**：盘中打开市场概览 ⇒ 顶部显示"交易日 <今天>（最新）"，而涨跌分布/板块/推荐榜
实际是上一交易日产物（`data_date` 只在 `note` 里披露）。属于"主标签错、附注对"。
`screener._freshness`（`screener.py:83`，用 `last_completed_trade_day`）与这里**不同源**，
同一屏上两处"今天"含义不同。

**建议**：展示链路的 `trade_date` 应统一走 `last_completed_trade_day`；
若确实需要"盘中也显示今天"，则必须同时返回 `data_date` 并让前端以 `data_date` 为标题
（现有 `heat.data_date` / `sectors.data_date` 已具备）。

---

### [P1] 1-D　交易日历缓存为空后永不可自愈；空日历使全仓降级为"不认节假日"

**模块**：`backend/app/data/calendar_store.py:29-82`

**现象（代码证据）**：

```python
# backend/app/data/calendar_store.py:63-68
def get_calendar() -> CalendarData:
    global _CACHE
    if _CACHE is None:             # ← 空 CalendarData 不是 None，命中后永不重载
        _CACHE = load_from_db_sync()
    return _CACHE
```
```python
# backend/app/data/calendar_store.py:29-45
def load_from_db_sync() -> CalendarData:
    if not db_path.exists():
        logger.warning(...); return build_calendar([])      # 空快照
    try: ...
    except Exception as e:
        logger.warning(f"交易日历同步加载失败: {e}"); return build_calendar([])   # 空快照
```

一旦空快照被缓存（DB 临时不可达 / 文件锁 / 启动早于 `init_database` 完成），
`_CACHE` 非 `None` ⇒ 本进程生命周期内**再也不会重试**。后果链：

- `is_trade_day()`（`domain/calendar.py:39-43`）恒 `False` ⇒
  `evening_routine._run_routine`（`jobs/evening_routine.py:100`）**每晚静默 skipped**，
  `run_pipeline`（`orchestrator.py:771,782-788`）**恒记 `non_trade_day` FAILED**；
- `prev_trade_day()`（`domain/calendar.py:46-53`）20 天窗口内找不到 ⇒ 抛 `ValueError`；
- `today_trade_date_or_last()` 落到 `_weekend_rewind`（`parquet_store.py:551-557`）
  —— 只回退周末、**完全不认节假日**；
- `kpi_series._is_trading_day()`（`kpi_series.py:668`）保守返回 `False` ⇒
  `should_archive_etf_snapshot()` 恒 False ⇒ **ETF 快照归档永久停摆且只有 debug 日志**。

**触发场景**：SQLite 被独占/损坏/磁盘满；容器冷启动与 `init_database()` 竞态；
`maint.py` 备份期间短暂不可用。发生时只有一条 `logger.warning`，无告警升级。

**建议**：`get_calendar()` 增加"空快照视为未加载"（`if _CACHE is None or len(_CACHE) == 0`）
并加最小重试间隔；或让 lifespan 的 `refresh_calendar_cache()` 失败时**不要**写入空快照。
另建议加一条启动自检：`len(calendar) == 0` ⇒ 健康检查告警。

---

### [P1] 1-E　节假日表 2026-12-31 到期；过期后静默降级为"只认周末"，新年首个交易日整块 unavailable

**模块**：`backend/app/data/parquet_store.py:551-586`、`backend/app/api/v1/market.py:193-264,288-347`

**现象（代码证据）**：

```python
# backend/app/data/parquet_store.py:551-557
def _weekend_rewind(today: date) -> date:
    """无日历可用时的兜底：周末回退到周五（不识别节假日，仅降级路径）。"""
    if today.weekday() == 5: return today - timedelta(days=1)
    if today.weekday() == 6: return today - timedelta(days=2)
    return today
```
```python
# backend/app/data/parquet_store.py:580-586
try:
    return prev_trade_day(today + timedelta(days=1), cal)
except ValueError as e:
    # 超长假期或日历区间不足，20 天窗口内未命中
    logger.warning(f"[parquet_store] 日历回溯失败，降级为周末回退: {e}")
    return _weekend_rewind(today)
```

`prev_trade_day` 只回溯 20 天（`domain/calendar.py:46-53`）。日历末端为 `2026-12-31`，
则 2027 年起任何日期都在窗口内找不到交易日 ⇒ 全部走 `_weekend_rewind`。

**触发场景（可复现的具体日期）**：2027-01-01 是**周五**（元旦假期首日）。
`_weekend_rewind(2027-01-01)` 返回 `2027-01-01` 本身 ⇒ `today_trade_date_or_last()` 返回
**2027-01-01（非交易日）** ⇒

```python
# backend/app/api/v1/market.py:196, 294
year = (target or today_trade_date_or_last()).year        # = 2027
files = sorted((s.DATA_ROOT / "daily_bar").glob(f"symbol=*/year={year}.snappy.parquet"))
if not files:
    return {"status": "unavailable", "reason": "本地无日线数据"}   # ← 2027 分区尚不存在
```

⇒ 涨跌分布块与热门板块块**同时 unavailable**，且 `reason` 是"本地无日线数据"（误导：数据其实都有，只是年份算错）。
同样会在 2027 年每个工作日持续，直到有人手动跑 `python -m app.data.ingest --stage calendar`。

**影响范围**：日频块两块同时空态；`_build_daily` 的 `degraded=True` ⇒
`data_freshness.status="degraded"`；`stock/portfolio/watchlist/etf` 的 `data_date` 缓存键错日期。

**建议**：① 增加日历"剩余覆盖天数"自检（<60 天告警，<7 天视为故障）；
② 日历刷新从"只在同步前"扩展到 `evening_routine` 与 lifespan（带每日最多一次节流）；
③ `_weekend_rewind` 的降级必须让调用方**显式披露** `degraded`（现在只有一条 warning，
API 层把它当正常交易日使用）。

---

### [P1] 1-F　前端 / 预警调度的"盘中"判定不含节假日：长假被当盘中

**模块**：`frontend/src/hooks/useMarketSession.ts`、`backend/app/api/v1/alerts.py`

**现象（代码证据）**：

```ts
// frontend/src/hooks/useMarketSession.ts:8-31
 * 口径：周一~周五 09:15–15:05（含集合竞价缓冲与收盘后 5 分钟尾差）。
 * 注意：**不含法定节假日**，仅按星期过滤；节假日会退化为「盘中」，
 *       表现为多轮询几次空数据，不会产生错误展示。
export function isMarketOpen(now: Date = new Date()): boolean {
  const day = now.getDay();
  if (day === 0 || day === 6) return false;
  const mins = now.getHours() * 60 + now.getMinutes();
  return mins >= 555 && mins <= 905;          // 09:15 ~ 15:05
}
```
```python
# backend/app/api/v1/alerts.py:729-735
def _is_market_open(now: datetime | None = None) -> bool:
    """A股盘中（工作日 09:15-15:05，与前端 isMarketOpen 同口径）。"""
    if now.weekday() >= 5: return False
    mins = now.hour * 60 + now.minute
    return 555 <= mins <= 905
```

**触发场景**：国庆 10-01~10-07（其中 5 个工作日）、春节、五一等**工作日节假日**。
`frontend/src/pages/MarketOverview/index.tsx:42-46`：

```tsx
const open = isMarketOpen();
const rt = useApi<OverviewRt>('/api/v1/market/overview/rt', open ? { _t: 'live' } : { _t: 'off' },
  { refreshInterval: open ? REFRESH.realtime * 1000 : 0 }, OVERVIEW_TIMEOUT);
```
⇒ 节假日 5 天 × 9 小时 × 每 30s = 每个前端标签页 **~5400 次** `/market/overview/rt` 请求/假期；
后端 `alerts.py:742` 同步把预警评估从每小时一次提到每 30s 一次。

**影响范围**：① 休市期间前端显示"盘中"而非"已休市"；
`frontend/src/pages/StockDetail/index.tsx:93-94` 的 "· 当前休市，为最近快照" 徽标**不会出现**；
② 外部行情源（东财/新浪/腾讯）在休市日被高频轮询，白白消耗限速配额，且这些调用会
写入 `k_market_overview_rt` 缓存（`market.py:1065-1068`），无实质收益；
③ 区间 09:15–15:05 不排除午休 11:30–13:00，午休也被当盘中。

**建议**：后端在 `/market/overview/rt`（或新增 `/market/session`）返回
`{is_trading_day, session: pre_open|continuous|lunch|closed|holiday, next_open_at}`
（由 `domain/calendar` 计算），前端与 `alerts._is_market_open` 统一消费，删掉两份本地副本。

---

### [P2] 1-G　`_last_completed_trade_date` 名不副实 + 三份重复的"已收盘"常量

```python
# backend/app/orchestrator.py:833-842
def _last_completed_trade_date(today: date | None = None) -> date:
    """默认目标日：今天；非交易日/未来则回退最近交易日（日历驱动）。"""
    today = today or date.today(); cal = get_calendar()
    if is_trade_day(today, cal) and today <= date.today():
        return today                       # ← 无 15:30 截断，与 domain 的 last_completed_trade_day 相反
    return prev_trade_day(min(today, date.today()), cal)
```
函数名叫 `last_completed_*` 却返回未收盘的今天（`domain/calendar.py:77-78` 的 docstring 明确
把"盘中返回今天"列为反模式）。当前仅 CLI `orchestrator.main()`（L854）使用，
但极易被后来者当成"标准答案"复用。

同时"15:30"这个常量存在三份：
`domain/calendar.py:67` `MARKET_CLOSE_CUTOFF = (15, 30)`、
`kpi_series.py:709` `if (now.hour, now.minute) < (15, 30):`、
`domain/calendar.py:74` docstring 里的文字。
另 `kpi_series._is_trading_day`（L668-692）与 `quality.load_trade_days`（L561-573）
**绕过 `calendar_store` 直连 SQLite**，与"唯一访问层"的架构约定
（`calendar_store.py:3-4`：*"本模块是【唯一】允许为日历触碰 SQLite 的地方"*）相抵触。

**建议**：删除 `_last_completed_trade_date`（改为调用 `domain.calendar.last_completed_trade_day`）；
`kpi_series._is_trading_day` 改为 `is_trade_day(d, get_calendar())`；
`quality.load_trade_days` 复用 `calendar_store`；`15:30` 统一引用 `MARKET_CLOSE_CUTOFF`。

---

## ② 休市期间的数据更新与展示

### [P1] 2-A　"行情已 N 天未更新"用**自然日差**判定：长假必误报

**模块**：`backend/app/api/v1/datacenter.py:727-731`

**现象（代码证据）**：

```python
# backend/app/api/v1/datacenter.py:727-731
if datasets.get("daily_bar", {}).get("end"):
    end = date.fromisoformat(datasets["daily_bar"]["end"])
    stale_days = (date.today() - end).days          # ← 自然日差，不是交易日差
    if stale_days > 5 and health == "green":
        health, health_msg = "yellow", f"行情已 {stale_days} 天未更新，建议执行增量同步"
```

**触发场景**：国庆 10-01~10-07（8 天休市）。10-08 开盘前，`daily_bar.end = 2026-09-30`，
`stale_days = 8 > 5` ⇒ 数据中心顶部告警条变黄："行情已 8 天未更新，建议执行增量同步"，
而实际**落后 0 个交易日**。春节（通常 9~11 天休市）必然命中。

**影响范围**：`frontend/src/pages/DataCenter/index.tsx:731-750` 直接渲染
`akshare_message` 为告警条文案；误报会诱导运维在休市/开盘前重复跑全量同步。

**建议**：改用交易日差 —— `len(range_trade_days(end + 1 day, last_completed_trade_day(cal), cal))`，
阈值按交易日计（如 >2）。同仓 `screener._freshness`（`screener.py:85`）已是此口径，应复用。

---

### [P2] 2-B　ETF 概览在休市日恒报 `data_freshness.status="fresh"`

```python
# backend/app/api/v1/etf.py:125-130
def _freshness(status: str, reason: str | None = None) -> dict[str, str]:
    return {"status": status, "as_of": _etf_data_date()}
```
```python
# backend/app/api/v1/etf.py:188-190 / 238
data.setdefault("data_freshness", _freshness("fresh"))     # 恒 "fresh"
```
`_etf_data_date()`（`etf.py:111-122`）返回的是 `today_trade_date_or_last()`，**休市日会正确回溯**
（2026-10-03 ⇒ 2026-09-30），所以 `as_of` 是诚实的；但 `status` 恒为 `"fresh"`，
不区分"今日收盘值"与"上一交易日收盘值"。
对比 `screener._freshness`（`screener.py:54-97`）会给出 `lag_trading_days` / `is_stale`。

**影响**：休市日 ETF 概览页（`frontend/src/pages/Etf/index.tsx`）显示"数据截至 2026-09-30"但
新鲜度为 fresh，用户无法区分"刚收盘"与"隔了 8 天假期"。

**建议**：`_freshness` 改为复用 `screener._freshness` 的口径（把该函数下沉到
`domain` 或 `data` 层共用），至少在 `as_of != date.today()` 时给出 `is_stale=true`。

---

### [P2] 2-C　前端缺少统一的"休市 / 未开盘 / 数据截至 X 日"提示

全仓只在个股详情的**盘中快照徽标**里出现休市文案：

```tsx
// frontend/src/pages/StockDetail/index.tsx:93-94
title={`盘中快照（${q.source}）${q.as_of ? ` · ${q.as_of}` : ''}${open ? '' : ' · 当前休市，为最近快照'}`}
```

其余页面（市场概览、选股中心、ETF、数据中心、组合、自选）都没有"已休市/未开盘"状态位；
`DataFreshness` 组件（`frontend/src/components/DataFreshness.tsx:62`）只渲染
"数据截至 {asOf}"，且 `asOf` 在 rt 块为 `"%Y-%m-%d %H:%M:%S"`、在 daily 块为 `YYYYMMDD`，
**两处格式不一致**却用同一个组件渲染（`MarketOverview/index.tsx:117`）。

**建议**：后端统一暴露 `market_session`；`DataFreshness` 增加 `session` 与 `is_stale` 入参。

---

## ③ 时间边界与跨日一致性

### [P1] 3-A　日频块把 `freshness` / `data_freshness` 固化进长 TTL 缓存（最长 3 天），且降级态因缺**顶层** `status` 不被短 TTL 收敛

**模块**：`backend/app/api/v1/market.py`、`backend/app/cache/swr.py`

**现象（代码证据）** —— `_build_daily` 只写**嵌套**的 `data_freshness`，**不写顶层 `status`**：

```python
# backend/app/api/v1/market.py:828-833
degraded = any(_is_degraded(block) for block in blocks.values())
blocks["data_freshness"] = {
    "status": "degraded" if degraded else "fresh",
    "source": "local_daily",
}
return blocks                      # ← 无 blocks["status"]
```

对比 rt 块**有**顶层 status（`market.py:795` `blocks["status"] = "degraded" if degraded else "ok"`）。
而 `_build_daily` 返回的 `recommend` 块内还带着**构建时刻算出的** `freshness`
（`market.py:508-521`，含 `expected` / `lag_trading_days` / `is_stale`）。

`/overview/daily` 的构建函数同样只在超时分支补顶层 status：

```python
# backend/app/api/v1/market.py:1142-1147
data["trade_date"] = date_str
return data                        # ← 正常路径无 data["status"]
...
data = await _overview_block_response(key, _build, ttl=_daily_ttl(), stale_window=OVERVIEW_STALE_WINDOW, ...)
```

SWR 的降级收敛**只看顶层 `status`**：

```python
# backend/app/cache/swr.py:56-60
def _effective_ttl(data: Any, ttl: int, stale_window: int) -> tuple[int, int]:
    if isinstance(data, dict) and data.get("status") == DEGRADED_STATUS:
        return min(ttl, DEGRADED_TTL_SECONDS), DEGRADED_STALE_WINDOW     # ≤15s，不留影子键
    return ttl, stale_window
```
```python
# backend/app/cache/swr.py:45-53
def default_cacheable(data: Any) -> bool:
    if not isinstance(data, dict): return True
    return data.get("status") != UNAVAILABLE_STATUS
```

TTL 长达"次日 15:30"（上限 3 天）：

```python
# backend/app/api/v1/market.py:956-962
def _daily_ttl(now: datetime | None = None) -> int:
    """日频块 TTL：至次日 15:30（盘后流水线之后自然轮换；周五覆盖到周一）。"""
    now = now or datetime.now()
    target = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now >= target: target += timedelta(days=1)
    return min(max(int((target - now).total_seconds()), 60), 86400 * 3)
```

预热器也会把同一份（含 freshness 的）载荷按 `_daily_ttl()` 回写：

```python
# backend/app/api/v1/market.py:1344-1347
daily = {k: v for k, v in data.items() if k not in ("indices", "money_flow", "anomalies", "from_cache")}
await swr.write_cache(k_market_overview_daily(td.strftime("%Y%m%d"), recommend_k),
                      daily, _daily_ttl(), OVERVIEW_STALE_WINDOW)
```

**触发场景**：`_daily_ttl()` 的到期点是**次日 15:30**，而当日产物（predictions / 榜单）
要 17:30 晚间例行才产出 —— 即"轮换点"早于"产出点"。当预热器
（`main.py:127-141`，每 240s 一轮）因异常持续失败或 `WARM_OVERVIEW_ON_STARTUP=false` 时，
日频块会带着**构建时刻的** `freshness.expected` / `lag_trading_days` / `is_stale`
与 `data_freshness.status` 锁死最长 3 天（另加 1800s 影子键窗口）。
典型表现：17:30 新榜单已生成，页面仍显示"数据落后最近已收盘交易日 1 个交易日"。

**缓解因素（不是修复）**：`_overview_warmer` 每 240s 无条件重建并回写，
稳态下掩盖了该缺陷；一旦 warmer 停摆（`main.py:135-136` 只 `logger.warning`）即暴露。

**影响范围**：`/market/overview/daily`（前端市场概览的涨跌分布/板块/推荐/AI 准确率/情绪全部来自该块）。

**建议**：① `_build_daily` 补顶层 `status`（与 `_build_rt` 对齐），让 `swr._effective_ttl` 生效；
② **freshness 不得进缓存** —— 把 `recommend.freshness` 与 `blocks["data_freshness"]`
从缓存载荷中剥离，改为每次请求实时计算（选股端点已这么做：
`screener.py:594-597` *"状态与 freshness 每次请求实时计算，不参与缓存"*）；
③ `_daily_ttl` 的轮换点改到"当日 18:00 之后"（覆盖 17:30 流水线）而非"次日 15:30"。

---

### [P2] 3-B　`k_screener(..., "latest", ...)` 无日期维度，"最新榜"最长滞后 35 分钟

```python
# backend/app/api/v1/screener.py:547, 560, 594
key = k_screener(target.isoformat() if target else "latest", strategy, top_k, board)
...
await swr.write_cache(key, snap, ttl=300, stale_window=1800)
...
data = await cached_or_build(key, _build, ttl=300, refresh=refresh, after_build=_log_run)
```

不传 `date` 时键恒为 `"latest"`（`cache/keys.py:105-107`）。跨日后键不变，
只能靠 TTL 300s + stale 窗口 1800s 自然过期 ⇒ 新交易日的榜单最长 **35 分钟**后才可见
（TTL 内命中 + 软过期 30 分钟内回旧值）。

**建议**：`"latest"` 换成 `last_completed_trade_day(cal).isoformat()`，
让键随交易日自动轮换（与 `k_market_overview_daily` 同口径）。

---

### [P2] 3-C　时区：本地时间为主，两处 `utcnow` 与 `CURRENT_TIMESTAMP` 配对正确，但缺统一约定

- 交易日历/TTL/缓存键全部用 `datetime.now()` / `date.today()`（**本地时间，GMT+8**）：
  `domain/calendar.py:83`、`market.py:958`、`parquet_store.py:574`、
  `sync_service.py:613/622/677`、`evening_routine.py:42/85/220`。
- `kpi_series.py:704` 用 `datetime.now(timezone.utc).astimezone()`（也落到本地），语义一致。
- `alerts.py:548-550` 用 `datetime.utcnow()` 与 SQLite `CURRENT_TIMESTAMP`（UTC）配对，一致。
- `sync_service.py:646-659` 显式用 `date('now','localtime')` / `datetime('now','localtime')`，
  并注明 `updated_at` 默认值是 UTC 需显式补（注释 L646-648），处理正确。
- `services/task_store.py:38-39` `_now()` 用 `datetime.now(timezone.utc)`，
  而 `data_jobs` 用 localtime ⇒ **两张任务表时区口径不同**（`background_tasks`=UTC，`data_jobs`=本地）。

**建议**：统一为"存储 UTC / 展示本地"或"全部本地"，并在 `core/` 提供单一 `now_local()`；
当前虽未造成错乱，但跨表 JOIN/对比时会踩坑。

### [P2] 3-D　时间戳单位：未发现秒/毫秒混用

全仓仅 `core/errors.py:94` 用毫秒（`int(time.time() * 1000)`，`APIResponse.ts`），
其余均为秒（`quotes_hub.py:107/137/140` `_time.time()`、`sync_service.py:501` `duration_ms`、
`orchestrator.py:813/822` `duration_ms`、`task_store.py` ISO 字符串）。**无混用风险**。

### [P2] 3-E　parquet / SQLite / Redis 三处日期一致性

- **写入同源性**：同步目标日统一为 `last_completed_trade_day`
  （`sync_service.py:273/360` + `backfill_incremental.py:375`），`universe_daily`/
  `predictions`/`screener_snapshot` 均由流水线按同一 `trade_date` 写
  （`screening.py:397-399` 甚至显式校验 `pred['date'].max() == trade_date`，不一致即 `ValueError`）。
- **日期校验**：`write_partition`（`parquet_store.py:844-845`）与 `write_year_batch`（L867-868）
  均对空 DataFrame 抛 `ValueError`，**空写不会覆盖已有数据** —— 这条历史风险已闭合。
- **残留风险**：`data_jobs.trade_date` 用自然日（见 1-A）；
  `background_tasks` 用 UTC（见 3-C）；`Redis` 侧 `k_etf_snap_history` 是持久业务存档
  且 TTL 仅 60 天（`cache/keys.py:34-51`），磁盘与数据库**均无第二副本**。

### [P2] 3-F　`data_jobs` 的 `RUNNING` 行无回收机制（只覆盖 `background_tasks`）

```python
# backend/app/services/task_store.py:109-149
def reap_stale_running_tasks(reason=...) -> list[str]:
    """启动时把**所有** status='running' 任务回收为 failed（消除永不自愈的僵尸任务）。"""
    ...
    conn.execute("UPDATE background_tasks SET status='failed', ... WHERE status = 'running'")
```
仅在 `main.py:106-110` 启动时调用一次，`WHERE` 针对 `background_tasks` 表。
`data_jobs` 表的 `RUNNING`（由 `orchestrator.py:65-66` 写入）**没有任何回收**：
`alerts.py:462-478` 只把它按 6 小时阈值**在告警视图里**当失败展示，
`datacenter.py:707-712` 明确"RUNNING 不得当绿灯，是否卡死交给启动时回收" ——
但启动回收并不覆盖 `data_jobs`。

**影响**：流水线进程被强杀 ⇒ `data_jobs` 留一行 `RUNNING`，健康灯永久显示
"同步任务执行中（结果未确认）"（`datacenter.py:712`）。当前生产库无 `RUNNING` 残留
（最新为 `daily_pipeline 2026-09-30 FAILED`），属潜在风险。

**建议**：`reap_stale_running_tasks` 扩展到 `data_jobs`（同样只在启动时、无条件），
或让 `alerts` 的陈旧判定复用同一阈值并回写 `FAILED`。

### [P2] 3-G　晚间例行卡在 `running` 时不受重试上限约束，会每 60s 重跑

```python
# backend/app/jobs/evening_routine.py:66-79
def _already_done_today() -> bool:
    rec = kv_get(_ROUTINE_KEY) or {}
    if rec.get("day") != _today_key(): return False
    st = rec.get("status")
    if st in ("done", "skipped"): return True
    if st != "failed": return False        # ← "running" 落到这里，无视 attempts 上限
    if int(rec.get("attempts") or 0) >= _MAX_ATTEMPTS: return True
    ...
```
`_mark("running", attempts=_attempts_today() + 1)`（`evening_routine.py:223`）在跑之前写入；
若进程在 `_run_routine` 中途崩溃，kv 停留在 `running` ⇒ 下一轮 60s 后重跑，
`attempts` 无限递增（`_MAX_ATTEMPTS=3` 与 15 分钟退避只对 `failed` 生效）。

**建议**：`running` 状态增加"租约/心跳"或按 `at` 时间戳做陈旧判定，
超过 N 分钟仍为 `running` 时按 `failed` 计入退避与上限。

---

## ④ 其他数据一致性风险

### [P2] 4-A　`/watchlist/dashboard` 静默截断到 100 只（全仓唯一未披露的截断）

```python
# backend/app/api/v1/watchlist.py:275
raw = list(dict.fromkeys(s.strip() for s in symbols.split(",") if s.strip()))[:100]
```
响应体（`watchlist.py:341-354`）只有 `summary` + `items`，**没有** `truncated` / `dropped_count` / `limit`。
对比全仓其它批量端点均已实现 §4.13.3 截断契约：

| 端点 | 位置 | 披露字段 |
|---|---|---|
| `/desk/...` | `desk.py:251` | `total, limit, truncated` |
| `/etf/...` | `etf.py:702, 933` | `total, limit, truncated` |
| `/datacenter/...` | `datacenter.py:971, 1537` | `total, limit, truncated` |
| `/ops/...` | `ops.py:113` | `truncated, symbols_available` |
| `/market/quotes` | `quotes_hub.py:109-114` | `requested, returned, truncated, dropped_count, limit` |
| `/alerts/...` | `alerts.py:487-491` | `recent_truncated, recent_limit, truncated, supplement_limit` |
| **`/watchlist/dashboard`** | `watchlist.py:275` | **无** |

**建议**：补 `requested/returned/truncated/limit` 四件套，与 `quotes_hub._disclose` 同形。

### [P2] 4-B　美股 ETF 批量快照静默截断到 50 只

```python
# backend/app/data/etf.py:364
q = ",".join(f"us{s}" for s in symbols[:50])
```
腾讯批量 URL 有长度上限，截断合理，但**未向上传递丢弃计数**，
调用方无法知道哪些标的没取到。建议返回 `(quotes, dropped)` 并由上层披露。

### [P2] 4-C　持久化键白名单靠人工登记，缺少新增守卫

```python
# backend/app/cache/keys.py:158-183
PERSISTENT_KEYS: frozenset[str] = frozenset({k_etf_snap_history()})
def is_persistent(key: str | bytes) -> bool: ...
```
```python
# backend/app/api/v1/app_settings.py:307-312
keys = [k async for k in r.scan_iter(match="aqp:*", count=500)]
to_delete = [k for k in keys if not is_persistent(k)]
protected = sorted({_decode_key(k) for k in keys if is_persistent(k)})
if to_delete:
    deleted = int(await r.delete(*to_delete))
```

历史事故（2026-09-28 无差别 `DEL` 丢掉 7 天 ETF 归档）**已修复且保护到位**：
全仓唯一的批量删除点就是 `app_settings.clear_cache`，且 `is_persistent` 过滤在前。
- 已核查：无 `flushdb` / `flushall` 调用（全仓 grep 仅命中注释）；
  `scan_iter` 仅此一处；`RedisClient.delete` 仅单键 TTL/影子键清理（`redis_client.py:270,285`）。

**残留风险**：`PERSISTENT_KEYS` 是**手工维护的单例集合**，新增"看起来像缓存、实为持久状态"
的键若忘记登记，下一次清缓存就会重演事故。目前集合中只有 1 个键，而
`k_etf_snap_history` 是"磁盘与数据库都没有第二份"的唯一副本（`keys.py:35-41`）。

**建议**：① 在 `keys.py` 增加"新增持久键必须同时加单测"的守卫（如断言
`PERSISTENT_KEYS` 非空 + 每个键的 docstring 含"持久状态"标记）；
② 给 `k_etf_snap_history` 增加 SQLite/parquet 双写备份（当前 60 天 TTL 是唯一保护）。

### [P2] 4-D　静默降级检查结论：整体健康，仅 2-B 一处 `status` 恒 fresh

已核查的降级路径均**如实标注**且**不造数**：
- `market._build_rt` / `_build_daily` 的 `data_freshness`（`market.py:788-795, 829-832`）；
- `_build_recommend` 的 `status/reason/message/coverage/freshness`（`market.py:495-524`）；
- `_heat_payload` 空集时返回 `unavailable` 而非九个全 0 桶（`market.py:159-168`）；
- ETF 各端点的 `_freshness("degraded", reason)`（`etf.py:554/815/885/922/1279`）；
- `stock._cached_block` 的 `unavailable`（`stock.py:247`）；
- `quotes_hub` 降级链 `source:"degraded"` + 空数组（`quotes_hub.py:82-84`）。
- `_pick_stat_day`（`market.py:99-135`）已修掉"用 `df["date"].max()` 被单只标的离群行劫持"的缺陷 A/D。

唯一问题项见 2-B（ETF `status` 恒 `"fresh"`）。

---

## 附录 A　严重度汇总

| 编号 | 严重度 | 标题 | 位置 |
|---|---|---|---|
| 1-A | **P0** | autoSync 无交易日门禁，休市日作业污染健康灯/日报 | `services/sync_service.py:668-693`, `:649-660` |
| 1-B | **P0** | AI 日报把休市日标成"交易日"并误报落后 | `api/v1/report.py:259-283` |
| 1-C | P1 | `today_trade_date_or_last` 无收盘截断，盘中把今天当最新交易日 | `data/parquet_store.py:560-586` |
| 1-D | P1 | 日历空缓存不可自愈，空日历使全仓降级为不认节假日 | `data/calendar_store.py:63-68` |
| 1-E | P1 | 节假日表 2026-12-31 到期，过期后新年整块 unavailable | `data/parquet_store.py:551-586`, `api/v1/market.py:196,294` |
| 1-F | P1 | 前端/预警"盘中"判定不含节假日（长假高频轮询 + 无休市提示） | `hooks/useMarketSession.ts:25-31`, `api/v1/alerts.py:729-735` |
| 2-A | P1 | "行情已 N 天未更新"用自然日差，长假必误报 | `api/v1/datacenter.py:727-731` |
| 3-A | P1 | 日频块 freshness 固化进长 TTL 缓存（≤3 天）+ 降级态缺顶层 status | `api/v1/market.py:828-833,1142-1147`, `cache/swr.py:56-60` |
| 1-G | P2 | `_last_completed_trade_date` 名不副实 + 三份 15:30 常量 | `orchestrator.py:833-842` |
| 2-B | P2 | ETF 概览 `data_freshness.status` 恒 fresh | `api/v1/etf.py:125-130` |
| 2-C | P2 | 前端缺统一"休市/未开盘"提示 | `components/DataFreshness.tsx` |
| 3-B | P2 | `k_screener("latest")` 无日期维度，最新榜最长滞后 35 分钟 | `api/v1/screener.py:547` |
| 3-C | P2 | `background_tasks`(UTC) 与 `data_jobs`(localtime) 时区口径不同 | `services/task_store.py:38-39` |
| 3-F | P2 | `data_jobs` RUNNING 无回收 | `services/task_store.py:109-149` |
| 3-G | P2 | 晚间例行卡 `running` 时不受重试上限约束 | `jobs/evening_routine.py:66-79` |
| 4-A | P2 | `/watchlist/dashboard` 静默截断 100 只 | `api/v1/watchlist.py:275` |
| 4-B | P2 | 美股 ETF 批量快照静默截断 50 只 | `data/etf.py:364` |
| 4-C | P2 | 持久化键白名单靠人工登记（当前保护正确） | `cache/keys.py:158-183` |

## 附录 B　本次未发现问题（已核查并确认闭环）

| 项 | 结论与证据 |
|---|---|
| 缓存键日期口径与业务截断不同源（历史缺陷） | **已修**：`market/overview/daily|rt`、`watchlist.dashboard`、`stock.predict|panels`、`portfolio`、`etf.*` 的键日期全部来自 `today_trade_date_or_last()`，与业务截断同源（`cache/keys.py:11-121`）。残留项见 3-B。 |
| 清缓存误删持久业务存档（历史事故） | **已修且保护到位**：`app_settings.py:307-312` 先过 `is_persistent`；全仓无 `flushdb/flushall`，`scan_iter` 仅此一处。残留风险见 4-C。 |
| freshness 进缓存（历史缺陷） | **部分残留**：选股端点已剥离（`screener.py:594-597`），但**日频块仍带**（3-A）。 |
| 空 DataFrame 写入覆盖有效数据 | **已闭合**：`write_partition:844-845` / `write_year_batch:867-868` 对空 df 抛 `ValueError`。 |
| 空序列导致异常（历史缺陷） | **已闭合**：`_heat_payload:159-168`（空集 → unavailable）、`screener.py:137-145`（列缺失/全 Null dtype 降级）、`screener.py:192-202`（缺列降级）、`pipeline.step_validate:113-118`（日历不可用降级）。 |
| 时间戳单位混用 | **未发现**：仅 `core/errors.py:94` 用毫秒，其余为秒/ISO。 |
| 静默降级返回 200 + 看似正常的空数据 | **基本闭环**：各块 `status/reason` 齐全；仅 ETF 的 `status` 恒 fresh（2-B）。 |
| 静默截断 | **基本闭环**：6/7 个批量端点有截断三件套；仅 `/watchlist/dashboard` 与美股快照缺失（4-A/4-B）。 |

## 附录 C　取证命令（可复核，全部只读）

```bash
# 1) 交易日历覆盖范围与节假日正确性
python -c "import sqlite3;c=sqlite3.connect('file:data/sqlite/aqp.db?mode=ro',uri=True);\
print(c.execute('select count(*),min(trade_date),max(trade_date) from trade_calendar').fetchone());\
print(c.execute('select is_sh,is_sz,count(*) from trade_calendar group by 1,2').fetchall());\
print(c.execute(\"select trade_date from trade_calendar where trade_date between '2026-09-25' and '2026-10-15'\").fetchall())"

# 2) 休市日同步实证（Sat/Sun 出现 sync_incremental 行）
python -c "import sqlite3,datetime;c=sqlite3.connect('file:data/sqlite/aqp.db?mode=ro',uri=True);\
[print(*r, datetime.date.fromisoformat(r[1]).strftime('%a')) for r in c.execute('select job_type,trade_date,status from data_jobs order by trade_date')]"

# 3) 休市日 AI 日报实证（2026-09-27 为周日）
python -c "import sqlite3,json;c=sqlite3.connect('file:data/sqlite/aqp.db?mode=ro',uri=True);\
a=json.loads(c.execute(\"select value from app_state where key='daily_reports'\").fetchone()[0]);\
print([r['date'] for r in a]); print(json.dumps([r for r in a if r['date']=='2026-09-27'],ensure_ascii=False)[:800])"

# 4) autoSync 配置
cat data/.auto_sync.json
```

---

*本报告为纯静态审查结论，未修改任何源码；所有生产数据均通过只读 SQLite 连接取得。*
