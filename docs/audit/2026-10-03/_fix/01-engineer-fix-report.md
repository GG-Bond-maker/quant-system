# AQP 四项确定性缺陷修复报告（工程师 · 2026-10-03）

项目根：`D:\Python_Project\Alpha Quant Platform`　作者：software-engineer（寇豆码）

> 说明：本次仅做四项修复的最小改动。工作区本身含有**他人未提交的改动**（`git status` 显示 54 个已改文件），
> 本报告只列**本次修复涉及**的文件与行。新建文件一律 LF。

---

## 修复 1：autoSync 补交易日门禁　✅ 完成

**文件**：`backend/app/services/sync_service.py`，`auto_sync_scheduler()`（668-714）

**改动前**（触发分支直接开跑，无交易日判断）：
```python
if hhmm >= cfg["time"] and not _auto_sync_today_done():
    if not _sync.running:
        logger.info(f"[datacenter] auto sync triggered at {hhmm}")
        _record_auto_sync()
        await asyncio.to_thread(lambda: _start_sync_bg("incremental", resume=False))
```

**改动后**（683-705，照抄 `evening_routine.py:94-101` 门禁写法；用 `if/elif` 跳过本次触发，**不 return**，循环末尾原 `await asyncio.sleep(60)` 继续节流；`non_trade_day_logged` 去重，避免 60s 刷屏）：
```python
if not _sync.running:
    from ..data.calendar_store import get_calendar
    from ..domain.calendar import is_trade_day
    today = date.today()
    if is_trade_day(today, get_calendar()):
        non_trade_day_logged = None
        logger.info(f"[datacenter] auto sync triggered at {hhmm}")
        _record_auto_sync()
        await asyncio.to_thread(lambda: _start_sync_bg("incremental", resume=False))
    elif non_trade_day_logged != today.isoformat():
        logger.info(f"[datacenter] auto sync skipped: {today} 非交易日")
        non_trade_day_logged = today.isoformat()
```

- **理由**：`data_jobs` 出现 09-19(六)/09-26(六)/09-27(日) 伪交易日记录，根因就是触发判据无交易日门禁；非交易日直接跳过即根治。
- 顶部已 import `date`/`get_calendar`（19/26 行），故门禁内为**函数内局部 import**（仅触发时执行）；未重复 import、未改动 `_record_sync_job` 的 `date('now','localtime')`（超范围）。
- 未新增未使用 import。

---

## 修复 2：AI 日报补交易日判断　✅ 完成

### (a) `backend/app/api/v1/report.py`，`build_daily_report()`（259-275）

**改动前**：`day = day or date.today()`
**改动后**（261-275）：
```python
if day is None:
    from ...data.calendar_store import get_calendar
    from ...domain.calendar import last_completed_trade_day
    try:
        day = last_completed_trade_day(get_calendar())
    except Exception as e:  # noqa: BLE001 日历缺失/区间不足 ⇒ 退回自然日
        logger.warning(f"[report] 交易日历不可用，日报日期退回自然日: {e!r}")
        day = date.today()
```
- **理由**：自然日会把周日当交易日（实证 2026-09-27 周日日报写「交易日：2026-09-27」）；`last_completed_trade_day` 有 15:30 截断。日历为空时该函数会 `raise ValueError`，故 try/except 兜底退回 `date.today()`（同 `parquet_store.today_trade_date_or_last` 的降级处理），保证日报绝不 500。

### (b) `backend/app/jobs/evening_routine.py`，`startup_catchup()`（250-261）

**改动前**：`if get_latest_report() is None: await asyncio.to_thread(generate_and_store_report)`
**改动后**：
```python
if get_latest_report() is None:
    from ..data.calendar_store import get_calendar
    from ..domain.calendar import is_trade_day
    today = date.today()
    if is_trade_day(today, get_calendar()):
        await asyncio.to_thread(generate_and_store_report)
    else:
        logger.info(f"[routine] {today} 非交易日，跳过启动补跑日报")
```
- **理由**：与 `_run_routine()` 一致，非交易日不补跑日报（阻断"周末启动即生成周日日报"入口）。`monitor.get_health_snapshot()` 分支按要求未动。

### 实测（`docs/audit/2026-10-03/_fix/verify_report_date.py`）
```
[direct] last_completed_trade_day(2026-09-27 20:00) = 2026-09-25
[e2e]    build_daily_report()['date'] = 2026-09-25
[fallback] 空日历 ⇒ build_daily_report()['date'] = 2026-10-03   （并打 WARNING 留痕）
ALL CHECKS PASSED: 日报默认日 = 最近已收盘交易日（2026-09-27 周日 ⇒ 2026-09-25）
```
→ **2026-09-27（周日）调用 `build_daily_report()`，`rep["date"] == "2026-09-25"`，非 2026-09-27**，满足验收口径。

---

## 修复 3：ETF 跟踪误差 —— 后端补日收益差字段，前端直接消费　✅ 完成

### 后端 `backend/app/api/v1/etf.py`，`_build_tracking_block()`（1112-1129）
在**同一循环**里把日收益差写入 `points`（新增字段 `diff`，单位 %，4 位小数；首点 `None`）。既有字段 `date/etf/index` 不变，`daily_diffs`（TE 年化用）语义不变。
```python
    if prev_e is not None and prev_i is not None and prev_e and prev_i:
        raw_diff = (e_close / prev_e - 1) - (i_close / prev_i - 1)
        daily_diffs.append(raw_diff)
        diff_pct = round(raw_diff * 100, 4)
    else:
        diff_pct = None
    points.append({"date": d, "etf": round(e_ret, 2),
                   "index": round(i_ret, 2), "diff": diff_pct})
```
- **理由**：原 `points[].etf/index` 是**累计收益率%**，前端却当价格做除法，数值完全错误；方案 A 由后端给出正确日收益差，避免口径漂移。
- `:1101` 的 `base_etf = ... or 1.0` 兜底未动（超范围）。响应模型为 `APIResponse[dict]`，新字段可正常透传。

### 前端 `frontend/src/pages/EtfDetail/index.tsx`，`TrackingErrorChart`（190-204）
删除"累计收益率当价格做除法 + `|| 1` 兜底"，直接读 `diff` 并显式挡 null（禁 `?? 0`）：
```js
for (let i = 0; i < points.length; i++) {
  const d = points[i].diff;
  if (d == null) continue;
  diffs.push({ date: points[i].date, value: d });
}
```
### 前端类型 `frontend/src/types/etf.ts`，`EtfTrackingPoint`（213-226）
新增 `diff?: number | null;`（含注释）。`TrackingErrorChart` 的 props 内联类型同步加 `diff?: number | null`。类型与后端实际返回一致（后端首点返回 `None`，故必须允许 `null`）。

---

## 修复 4：收敛「缓存键日期口径」　✅ 完成（含 1 处与 team-lead 判断的冲突，已按"以代码为准"处理）

### 新增函数 `backend/app/data/parquet_store.py`（590-617），并登记 `__all__`（54）
`last_completed_trade_date_or_last()`：语义=「最近**已收盘**交易日」，内部复用 `get_calendar()` + `domain.calendar.last_completed_trade_day`，**完整保留**降级路径（日历为空 / 回溯失败 ⇒ `_weekend_rewind(date.today())` + warning）。`today_trade_date_or_last()` 行为未动。

### 逐处判断表

| 位置 | 用途 | 结论 | 一行理由 |
|---|---|---|---|
| `api/v1/etf.py:127`（原:122）`_etf_data_date()` | 概览缓存维度 + `as_of` | **改** | 纯缓存键日期维度，盘中键=今天 ⇒ 收盘后旧缓存仍命中；改后含 15:30 截断。 |
| `api/v1/stock.py:209`（原:207）`stock_predict` | 缓存键 | **改** | 同为缓存键日期维度，收盘后不应再命中盘中快照。 |
| `api/v1/stock.py:274`（原:270）`stock_panels` `td` | 各块缓存键 | **改** | 同上（8 个面板块共用该 `td` 作缓存维度）。 |
| `api/v1/portfolio.py:177`（原:175）`portfolio_search` | 缓存键 | **改** | 同上。 |
| `api/v1/watchlist.py:280`（原:278）`dashboard` | 缓存键 | **改** | 同上。 |
| `api/v1/market.py:1006` `/overview/rt` | 实时块缓存键 | **不改** | 实时块语义就是"今天"，须用无截断的 `today_trade_date_or_last`（team-lead 亦判不改）。 |
| `api/v1/market.py:196` `_heat_from_local` | 仅取 `.year` 定位分区 | **不改** | 只用年份定位 `year=` 分区，日期口径对结果无影响（team-lead 亦判不改）。 |
| `api/v1/market.py:294` `_sectors_from_local` | 仅取 `.year` 定位分区 | **不改** | 同 :196（`test_sectors_local_stat_day` 依赖其只需 `.year`）。 |
| `api/v1/market.py:1090` `/overview/daily` | `td` = `date_str` 默认值 + 响应字段 `trade_date` + 缓存键 | **不改** | 见下方"冲突说明"。 |
| `api/v1/market.py:1182` `/overview` | 同上 | **不改** | 见下方"冲突说明"。 |
| `api/v1/market.py:1309` `warm_overview_cache` | 预热 `k_market_overview(td)` 等三键 | **不改** | 见下方"冲突说明"。 |

### ⚠️ 与 team-lead 判断的冲突（market.py:1090/1182/1309）

team-lead 建议 `:1309` **应改**，`:1090/1182` **需判断**。**读码后判定：三者均不应改**，理由如下（按"若与代码实际语义冲突，以代码为准"处理）：

1. `_build_daily(td, ...)` / `_build_rt(td)` **内部根本不使用 `td`**（heat/sectors 走 `_pick_stat_day(df)` 从数据取统计日，rt 走外部源）。`td` 的作用只有两个：**缓存键日期维度** 与 **响应字段 `trade_date`**。
2. `:1182` 的 docstring 明确定义 `trade_date` 恒为"**实时块真实所属交易日**"，且 `:1090` 同款。既有测试**固化**了这一语义：
   - `tests/test_api.py:307` 打桩 `market_api.today_trade_date_or_last` 后断言 `trade_date == frozen.strftime("%Y%m%d")`；
   - `tests/test_param_date_calendar_validation.py:111` 断言 `data["trade_date"] == today_trade_date_or_last()`；
   - `tests/test_overview_heal_chain.py:112/412` 也用 `k_market_overview(today_trade_date_or_last()...)` 构造缓存键。
   若改 `:1090/1182`，这些用例将在**盘中（<15:30）/非交易日**变红——它们并非"过时测试"，而是**编码了既定契约**。
3. `:1309`（预热器）用**同一个** `td` 同时写 `k_market_overview(td)`、`k_market_overview_rt(td)`、`k_market_overview_daily(td)` 三键；而 `:1006`（`/overview/rt`，已判定不改）读的正是 `k_market_overview_rt(today_trade_date_or_last())`。**若只改 `:1309`，预热键将与端点读取键错位**（盘中写上一交易日的键、端点读今天的键）⇒ 预热对 rt 失效甚至把主键冷落，**严格劣化**。要改必须 `:1309` 与 `:1090/:1182/:1006` 同改，而 `:1006` 按语义（实时块=今天）不应改 ⇒ 结论是这一族**整组保持不动**。
4. 该族 `/overview` 主键 TTL 仅 300s、日频块 TTL `_daily_ttl()` 至次日 15:30，短线陈旧窗口有限；而 `:1006` 必须代表"今天"，是更硬的约束。

→ 故本次**未改** `market.py:1090/1182/1309`。若 team-lead 仍要求改，需要先明确 `/overview*` 全族的 `trade_date` 语义（是否允许盘中回上一交易日）并同步更新上述 3 个测试，属**独立设计项**，不在"最小改动"范围。

### 附带测试更新（修复 4 的必然结果）
`tests/test_hotpath_watchlist_panels.py:77/154`、`tests/test_hotpath_portfolio_datacenter.py:83` 原先打桩的是被替换掉的符号 `today_trade_date_or_last`（源码改用新函数后该属性不再存在 ⇒ `monkeypatch.setattr` 报 `AttributeError`）。已把打桩目标同步为新函数名 `last_completed_trade_date_or_last`——**测试意图（冻结日期）不变**，非放宽断言。

---

## 验证结果（真实输出）

### 后端（`backend/`，开头 `export PATH="/usr/bin:/bin:$PATH"`）
相关子集（sync/report/etf/market/watchlist/portfolio/调度）：
- 批次 A：`test_hotpath_watchlist_panels / test_hotpath_portfolio_datacenter / test_etf / test_param_date_calendar_validation / test_api / test_monitor / test_lab_factor / test_overview_heal_chain / test_kpi_series / test_sectors_local_stat_day / test_north_flow_non_trading` ⇒ **182 passed**
- 批次 B：`test_datacenter_auto_sync / test_sync_resume_not_wiped / test_sync_integrity / test_failed_count_per_round / test_datacenter_* / test_resilient_loop / test_scheduler_isolation / test_scheduler_lifetime_isolation / test_single_instance_guard / test_task_store_reap / test_monitor_retrain_resilience / test_pipeline_state_truth` ⇒ **199 passed**

全量：`pytest -m "not network" -q -p no:randomly`（日志 `docs/audit/2026-10-03/_fix/pytest_full.log`）
```
= 1 failed, 2057 passed, 8 skipped, 4 deselected, 217 warnings in 387.39s (0:06:27) =
FAILED tests/test_monitor_trigger_caliber.py::test_real_panel_injection_sensitivity_table
```

**唯一失败与本次四项修复无关**（已单独复现，日志 `_fix/pytest_monitor_iso.log`）：
```
tests\test_monitor_trigger_caliber.py:348: in test_real_panel_injection_sensitivity_table
    assert base["max"] <= M.PSI_DEGRADED, "基线不应已降级"
E   assert 0.5149 <= 0.25
```
- 该用例只跑 `app.ml.monitor.compute_psi()`（真实特征面板 PSI 标定），**不经过本次改动的任何文件/函数**：`monitor.py` 仅 `from ..data.parquet_store import read_all_symbols, read_symbol_dataset`（均未改），与 4 项修复零交集；我仅**新增**了 `parquet_store` 的函数与 `__all__` 条目，未改既有行为。
- **单独运行同样失败** ⇒ 非本次改动引入，是当前工作区（含他人未提交的 `app/ml/monitor.py +316` / `tests/test_monitor_trigger_caliber.py +37`）既有问题：该测试 docstring 记录的基线是 **0.2024**，实测已漂到 **0.5149**（真实面板/标定口径变化）。
- 相对团队给的基线口径（1885 passed）：当前树为 **2057 passed**（工作区多出 9 个新增测试文件及他人对既有测试的修改），故 passed 数差异来自他人改动，非本次修复。

### 前端（`frontend/`）
- `npx tsc --noEmit` ⇒ **退出码 0，无类型错误**（日志 `_fix/tsc.log`）
- `npx vitest run` ⇒ **4 test files passed / 29 tests passed，退出码 0**（日志 `_fix/vitest.log`）

### 静态门禁（与 CI 一致）
- `ruff check <改动文件> --select F,E9` ⇒ **All checks passed!**（日志 `_fix/ruff.log`）——无未使用 import（F401）等。
- `mypy <改动文件> --ignore-missing-imports` ⇒ **Success: no issues found in 8 source files**（日志 `_fix/mypy.log`）

---

## 变更文件清单（本次修复）
1. `backend/app/services/sync_service.py`
2. `backend/app/api/v1/report.py`
3. `backend/app/jobs/evening_routine.py`
4. `backend/app/api/v1/etf.py`
5. `backend/app/data/parquet_store.py`
6. `backend/app/api/v1/stock.py`
7. `backend/app/api/v1/portfolio.py`
8. `backend/app/api/v1/watchlist.py`
9. `frontend/src/pages/EtfDetail/index.tsx`
10. `frontend/src/types/etf.ts`
11. `backend/tests/test_hotpath_watchlist_panels.py`（打桩目标同步，非断言放宽）
12. `backend/tests/test_hotpath_portfolio_datacenter.py`（同上）
- 新增（未跟踪）：`docs/audit/2026-10-03/_fix/`（报告 + 验证脚本 + 日志）

**未改（有意）**：`backend/app/api/v1/market.py`（:196/:294/:1006/:1090/:1182/:1309 —— 见修复 4 冲突说明）。

---

# 追加：验收更正与护栏补充（2026-10-03 · 第二批次）

触发：team-lead 复核 + QA `04-qa-verification.md`。两项遗留：
① 给错的验收值（09-27 的上一交易日应为 **09-24** 而非 09-25）；
② 3 处"行为正确但无测试守护"的真实覆盖缺口（#4 / #2 / #1+#2b）。

## 一、验收值更正（修复 2 的验收脚本）

**结论：修复代码本身无需改动**（`build_daily_report` 如实委托 `last_completed_trade_day`，真日历下得 09-24）；
**错的是验收期望值与验收脚本**（脚本自建了"把 09-25 当交易日"的**假日历** ⇒ 自证式验收）。

- 更正 `docs/audit/2026-10-03/_fix/verify_report_date.py`：
  - **删除**自建假日历，改为直接读取**真实** `get_calendar()`；
  - 断言值 `2026-09-25` → **`2026-09-24`**；
  - 打印 `2026-09-25 in cal = False` 作为佐证，并断言 09-24/09-28/09-30/10-08 均在真实日历中。
- 实测（日志 `_verify_report_date.log`）：
```
[cal]    真实日历交易日数 = 6543
[cal]    2026-09-25 in cal = False（应为 False）
[cal]    预期交易日 09-24 / 09-28 / 09-30 / 10-08 均在日历中
[direct] last_completed_trade_day(real_cal, 2026-09-27 20:00) = 2026-09-24
[e2e]    build_daily_report()['date'] = 2026-09-24
[fallback] 空日历 ⇒ build_daily_report()['date'] = 2026-10-03（date.today()）
ALL CHECKS PASSED
```
（09-25 是 2026 中秋假期 ⇒ 09-27 周日的上一交易日只能是 09-24。空日历兜底注入的是**空日历**=documented 降级路径，非假日历。）

## 二、源码改动（为可测性补一个**可选**注入点）

`backend/app/data/parquet_store.py`，`last_completed_trade_date_or_last()`（590-622）：
新增**可选**参数 `now: datetime | None = None`（默认 `None` ⇒ `datetime.now()`；生产调用方**零改动**），
把内部 `date.today()` 改为 `now.date()`、并把 `now` 透传给 `domain.calendar.last_completed_trade_day(cal, now)`。
风格对齐 `domain/calendar.py:70` 的同名 `now` 参数。**语义不变**（`now=None` 时与改前完全等价）。
- 唯一目的：让测试可在**函数体层面**冻结时钟，断言"盘前/盘中 vs 收盘后"两种口径
  （原缺口：既有 hotpath 测试 `monkeypatch` 掉整个符号，看不到函数体变异）。

## 三、新增护栏用例（`backend/tests/test_trade_caliber_guardrails.py`，8 例）

针对 QA §6 的 3 处缺口，每条都能**区分新/旧口径**；含正向对照防"门禁恒跳过"式过度防御。
日历来源：测试隔离使 `get_calendar()` 恒空 ⇒ 注入与**真实 `trade_calendar` 逐日一致**的交易日集合
（不含 09-25；来源见 QA §2.1），**非**自证式假日历。

| # | 用例 | 守护点 |
|---|---|---|
| #4 | `..._applies_close_cutoff` / `..._default_uses_now` | 15:30 截断（盘前⇒上一交易日、收盘后⇒当天）；默认路径透传 now |
| #2 | `..._uses_last_completed_trade_day` / `..._falls_back_when_calendar_empty` | 日报默认日=上一交易日（≠自然日）；空日历兜底不抛+WARNING |
| #1 | `..._skips_on_non_trade_day` / `..._triggers_on_trade_day`（正向对照） | autoSync 非交易日不触发；交易日触发一次 |
| #2b | `..._skips_report_on_non_trade_day` / `..._generates_report_on_trade_day`（正向对照） | startup_catchup 非交易日不补跑日报；交易日补跑一次 |

## 四、变异验证（把修复改回缺陷形态 ⇒ 护栏必须变红；byte 级还原 + md5 复核对齐）

harness：`docs/audit/2026-10-03/_fix/run_mutations.py`（读字节→替换→跑测试→还原→md5 核对）。结果（日志 `_mutations_summary.log` 及 `_mut4.log`/`_mut2.log`/`_mut1.log`/`_mut2b.log`）：

| # | 变异（缺陷形态） | 结果 | 真红断言（摘录） |
|---|---|---|---|
| #4 | `last_completed_trade_day(cal, now)` → `(cal)`（丢 now） | **RED**（2 失败） | `AssertionError: datetime.date(2026, 9, 30)` / `默认路径必须把'当前时刻'透传…` |
| #2 | `day = last_completed_trade_day(get_calendar())` → `date.today()` | **RED**（2 失败） | `AssertionError: 2026-10-03` / `空日历降级必须留 WARNING，实际日志: []` |
| #1 | `if is_trade_day(today, get_calendar()):` → `if True:`（=删门禁） | **RED**（1 失败） | `AssertionError: 非交易日不得触发同步，实际触发: ['incremental']` |
| #2b | 同上（`evening_routine.startup_catchup`） | **RED**（1 失败） | `AssertionError: 非交易日不得补跑日报，实际: ['gen']` |

- 4 个源文件变异前后 **md5 逐一相等**（还原无残留）；`git grep MUTATION -- backend/app` ⇒ **空**。
- 变异后 md5（=还原后）与本批基线一致：`report.py f227a29a…`、`sync_service.py d1d3fa5b…`、`evening_routine.py 13429790…`；`parquet_store.py` 因本次新增 `now` 参数，md5 更新为 `52e877d1…`。

## 五、静态门禁与回归（真实输出）

- `ruff check app/data/parquet_store.py tests/test_trade_caliber_guardrails.py --select F,E9` ⇒ **All checks passed!**
- `mypy app/data/parquet_store.py --ignore-missing-imports` ⇒ **Success: no issues found in 1 source file**
- 新增用例单跑：`pytest tests/test_trade_caliber_guardrails.py -q -p no:randomly` ⇒ **8 passed**（日志 `_guard_new.log`）
- 全量：`pytest -m "not network" -q -p no:randomly`（日志 `_full_engineer2.log`）⇒ **1 failed, 2073 passed, 8 skipped, 4 deselected**

## 六、变更文件（本批新增）

1. `backend/app/data/parquet_store.py`（+`now` 可选参数，语义不变）
2. `backend/tests/test_trade_caliber_guardrails.py`（新增，8 例护栏）
3. `docs/audit/2026-10-03/_fix/verify_report_date.py`（更正为真日历 + 09-24）
4. `docs/audit/2026-10-03/_fix/run_mutations.py`（新增，变异 harness）
- ⚠️ `#5/#6` 的既有护栏（`test_sync_partial_failure_terminal.py` / `test_validate_step.py`）**未动**。

### 全量回归（本批）
```
pytest -m "not network" -q -p no:randomly   （日志 _full_engineer2.log，522s）
= 1 failed, 2073 passed, 8 skipped, 4 deselected, 217 warnings =
FAILED tests/test_monitor_trigger_caliber.py::test_real_panel_injection_sensitivity_table
```
- 相比本批基线（QA 复核 2065 passed）：**+8 passed = 本次新增的 8 个护栏用例**，**无新增失败**。
- 唯一失败仍是**既有**的 PSI 标定用例（`assert 0.5149 <= 0.25`，真实面板漂移；与交易日口径零交集），按 team-lead 要求**未修**。

---

# 追加：sync 失败阈值双口径对齐（2026-10-03 · 第三批次）

触发：`backend/app/services/sync_service.py` 中并存两套失败阈值——`SYNC_FAILURE_ALERT_RATIO = 0.05`（`_mark_mass_failure`，在 `_run_incremental`/`_run_repair` 收尾置 `_sync.error`）与 `SYNC_MAX_FAILED_RATIO = 0.5`（worker 层 `partial_fail` 兜底）。二者**判据口径一致**（均为 `失败数/总数`），**仅数值不同**。用户裁决：**对齐到更严格的 0.05**（不得放松至 0.5，否则削弱既有保护）。

## 一、源码改动

### 1. `backend/app/services/sync_service.py`

**（a）`:151`（原 `:149`）—— 单一数据源引用（核心改动）**

改动前：
```python
SYNC_MAX_FAILED_RATIO = 0.5
```
改动后：
```python
SYNC_MAX_FAILED_RATIO = SYNC_FAILURE_ALERT_RATIO          # 现解析为 0.05
```
- **理由**：两个常量曾各写各的（0.05 / 0.5）而分叉，令同一"失败占比"在两条路径上得出**相反**结论。改为引用上游常量 ⇒ 单一数据源，杜绝再次漂移（并由下方护栏用例钉死）。
- 行号由 `:149` 移到 `:151`，因紧随其上的注释块由 8 行增为 9 行（见下）。

**（b）`:142-150`（原 `:142-148`）—— 注释如实描述现状**

改动前（要点）：阈值取 **0.5**（宽松）……"与上方 SYNC_FAILURE_ALERT_RATIO(0.05) 的关系：后者**更严格**，对本兜底构成上游覆盖"。
改动后（要点）：
- 明示两常量**同源同值**，统一为 **0.05**；"对齐到 0.05，不得放松——放松会削弱 `_run_incremental`/`_run_repair` 已有的保护"；
- 定位说明改为：worker 层判据是**防御性兜底**，覆盖**未调用 `_mark_mass_failure` 的 `_run_rebuild`** 等路径（rebuild 失败只进 `failed` 集、不置 `_sync.error`）；
- **未重复** `SYNC_FAILURE_ALERT_RATIO` 上方（`:134-139`）已有的"口径与 `data/pipeline.py` 昨有今无容差一致（纯比例、不设绝对下限）"信息——该层信息保持原位、不复制。

**（c）`:520`（worker 层判据注释）**

`… >= SYNC_MAX_FAILED_RATIO(0.5) ⇒ 记 FAILED。` → `… >= SYNC_MAX_FAILED_RATIO（= SYNC_FAILURE_ALERT_RATIO = 5%）⇒ 记 FAILED。`（去掉写死的 0.5，避免注释再次漂移）。

**（d）未改（有意，符合规格 #3）**：**未**给 `_run_rebuild` 添加 `_mark_mass_failure()` 调用。阅读后确认现有设计自洽：`_run_rebuild` 逐标的失败只进 `failed` 集，由 worker 层（现 0.05）兜底判 FAILED，无需上游置错。**无异常、与规格无冲突**。

### 2. `backend/tests/test_sync_partial_failure_terminal.py`

- 模块 docstring：`SYNC_MAX_FAILED_RATIO(0.5)` → 说明两阈值已统一为 0.05、互为单一数据源。
- 新增 `from ... import SYNC_FAILURE_ALERT_RATIO`（护栏用例需要）。
- 新增 helper `_counts_around_threshold(total)`：**基于常量**返回 `(须判 FAILED 的失败数, 须判 SUCCESS 的失败数)`——`over=int(阈值*total)+1`（严格高于）、`under=int(阈值*total)-1`（严格低于且 ≥1）。**不使用写死数值**，故阈值再漂移仍成立；用整数 `±1` 而非浮点 `±0.1`，避免阈值下探到 <0.1 时构造出越界（负/超 1）比例。
- 3 个 worker 场景用例（`test_rebuild_mass_failure_records_failed` / `..._over_threshold_...` / `..._below_threshold_...`）改为 `_TOTAL=100` + 上述 helper 构造，不再写死 8/10、1/10。
- **`test_threshold_constant_is_loose`（原断言 `0.5 <= SYNC_MAX_FAILED_RATIO <= 1.0`）→ 拆为两条**：
  - `test_max_failed_ratio_shares_alert_threshold`：**防漂移护栏**——`assert SYNC_MAX_FAILED_RATIO == SYNC_FAILURE_ALERT_RATIO`（规格 #4）；
  - `test_threshold_scope_is_sane`：合理性 `assert 0.0 < SYNC_MAX_FAILED_RATIO <= 1.0`（纯比例口径，不设绝对下限）。

### 3. 其余两文件核查（规格 #5）

- `backend/tests/test_validate_step.py`：命中的 `0.5` 是**无关常量** `VALIDATE_MIN_COVERAGE_FALLBACK`（`:255` `assert VALIDATE_MIN_COVERAGE_FALLBACK == 0.5`），与 sync 失败阈值零关系 ⇒ **不改**（未动该断言，未放宽）。
- `backend/tests/test_trade_caliber_guardrails.py`：无 `0.5` / `SYNC_*` 命中 ⇒ **不改**。

## 二、验证（真实输出）

### 相关子集
`pytest tests/test_sync_partial_failure_terminal.py tests/test_validate_step.py tests/test_trade_caliber_guardrails.py -q -p no:randomly`
```
tests\test_sync_partial_failure_terminal.py .....                        [ 16%]
tests\test_validate_step.py ..................                           [ 74%]
tests\test_trade_caliber_guardrails.py ........                          [100%]
============================= 31 passed in 3.98s ==============================
```

### 全量
`pytest -m "not network" -q -p no:randomly`
```
= 1 failed, 2074 passed, 8 skipped, 4 deselected, 217 warnings in 353.00s (0:05:53) =
FAILED tests/test_monitor_trigger_caliber.py::test_real_panel_injection_sensitivity_table
```
- 唯一失败为**既有**已知项（PSI 基线 `0.5149<=0.25`，见本报告 §二/§五），**与本任务无关、按令未修**。
- 相比上一批（2073 passed）：**+1 passed**（5 例护栏 vs 原 4 例），**无新增失败**。

### 变异验证（把 `:151` 改回字面量 `0.5` ⇒ 防漂移用例必须变红）
```
151:SYNC_MAX_FAILED_RATIO = 0.5
tests\test_sync_partial_failure_terminal.py ...F.                        [100%]
=========================== short test summary info ===========================
FAILED tests/test_sync_partial_failure_terminal.py::test_max_failed_ratio_shares_alert_threshold
tests\test_sync_partial_failure_terminal.py:178: in test_max_failed_ratio_shares_alert_threshold
    assert SYNC_MAX_FAILED_RATIO == SYNC_FAILURE_ALERT_RATIO, (
E   AssertionError: (0.5, 0.05)
E   assert 0.5 == 0.05
========================= 1 failed, 4 passed in 1.44s =========================
```
- 仅**新增的防漂移用例变红**；其余 4 例（基于常量相对构造）在 0.5 下仍绿 ⇒ 说明变红**确实**来自"两常量不一致"，而非场景构造。
- 还原后：`md5sum app/services/sync_service.py` = `83091bdac0cdac3f41790ff10fadeb3f`，与变异前基线**逐一相等**（byte 级还原无残留）；`grep` 确认为 `SYNC_MAX_FAILED_RATIO = SYNC_FAILURE_ALERT_RATIO`；单跑 `5 passed`。

### 静态门禁
- `ruff check app/services/sync_service.py --select F,E9` ⇒ **All checks passed!**
- `ruff check tests/test_sync_partial_failure_terminal.py --select F,E9` ⇒ **All checks passed!**

## 三、变更文件（本批）
1. `backend/app/services/sync_service.py`（`:151` 引用化；`:142-150` 注释；`:520` 注释）
2. `backend/tests/test_sync_partial_failure_terminal.py`（+1 防漂移用例；3 场景改常量相对构造；docstring/注释）
- 前端未动；`test_validate_step.py` / `test_trade_caliber_guardrails.py` 核查后**无需改动**。

### 与规格的冲突/偏差
- 无。规格称 `SYNC_MAX_FAILED_RATIO` 在 `:149`，实际因注释增行后落于 **`:151`**（`SYNC_FAILURE_ALERT_RATIO` 仍在 `:140`）——仅行号位移，改动语义与规格完全一致。
