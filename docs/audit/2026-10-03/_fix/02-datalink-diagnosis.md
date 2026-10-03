# 02 · daily_pipeline 连续失败根因诊断（只读）

> 分析人：严过关（software-qa-engineer，QA）｜日期：2026-10-03｜项目：Alpha Quant Platform
> 范围：**只读诊断**，未修改任何源码/数据。临时脚本见同目录 `probe_datalink.py` / `verify_validate_current.py` / `probe_out.log`。
> 结论分级：【确证】=有代码/数据/日志直接证据；【推测】=推断，附所需补充信息。

---

## ① 现象与影响

`data_jobs` 中 `daily_pipeline` 在 2026-09-04 ~ 2026-09-30 连续失败（11 行里 6 行 FAILED），失败步骤集中在 `validate`：

| id | trade_date | status | 失败步骤 | error_message（原文） |
|---|---|---|---|---|
| 4 | 2026-09-04 | FAILED | infer | `ValueError: 特征中不存在交易日 2026-09-04` |
| 9 | 2026-09-18 | FAILED | validate | `ValueError: code 必须 6 位数字: 000001.SZ` |
| 13 | 2026-09-23 | FAILED | validate | `validate failed: 昨有今无 25/25 只（阈值 5%）: 000001, …` |
| 17 | 2026-09-28 | FAILED | validate | `validate failed: 昨有今无 28/29 只（阈值 5%）: …` |
| 19 | 2026-09-29 | FAILED | validate | `validate failed: 昨有今无 24/25 只（阈值 5%）: …` |
| 20 | 2026-09-30 | FAILED | validate | `validate failed: 昨有今无 1/1 只（阈值 5%）: 000007` |

**连带影响**：`predictions/` 缺失。以「交易日历」为准，2026-09-01~10-09 区间缺 16 个交易日：
`20260901,02,03,08,09,10,11,18,21,22,23,24,28,29,1008,1009`。
> ⚠️ 订正：任务书所述"09-18~09-29 共 9 个交易日"应精确为：**09-18~09-29 区间内缺 7 个交易日**（18/21/22/23/24/28/29）。详见 Q6。

**观测面**：失败在 `data_jobs` 里**完整可查**（status+error_message+traceback 都在），但**没有**任何主动通知触达运维（无告警行、无 webhook）。原因见 Q5。

---

## ② 逐条作答

### Q1「昨有今无 X/Y」的分母为什么那么小？待校验集合怎么构造的？

**答：分母小 ≠ 只校验了 25 只。全部 codes 都被遍历了，但"分母" `n_expected` 只统计『当日或上一交易日有数据』的标的；数据停滞时 99% 的标的被归入 `n_suspended` 静默排除，不进分母、也不报警。**

证据（当前代码）：

`backend/app/data/pipeline.py`
```python
122  for code in codes:                       # ← 遍历的是【全部】codes，不是 25 只
123      sym = code_to_symbol(code)
124      df = read_symbol_dataset("daily_bar", sym, start=trade_date, end=trade_date)
125      if not df.is_empty():
126          ok, errs = validate_daily_bar(df)
127          if ok:  n_ok += 1                  # 当日有数据
...
133      if prev is None:
134          n_suspended += 1                   # 日历不可用降级
135          continue
136      prev_df = read_symbol_dataset("daily_bar", sym, start=prev, end=prev)
137      if prev_df.is_empty():
138          n_suspended += 1                   # ← 今昨都无数据 ⇒ 当"停牌"静默跳过
139      else:
140          missing_errs.append(code)          # 昨有今无
...
159  n_universe = len(codes)
160  n_expected = n_ok + len(missing_errs)      # ← 分母【只含】今/昨有数据的标的
...
174  tolerance = VALIDATE_MISSING_TOLERANCE_RATIO * n_expected
175  if len(missing_errs) > tolerance:
176      raise ValueError(
177          f"validate failed: 昨有今无 {len(missing_errs)}/{n_expected} 只"
178          f"（阈值 {VALIDATE_MISSING_TOLERANCE_RATIO:.0%}；"
179          f"当日已落库 {n_ok}/{n_universe} = {n_ok / max(1, n_universe):.1%}）: "
180          + ", ".join(missing_errs[:5]))
```

- 「待校验集合」= 传入的 `codes`（晚间例行传的是 `read_all_symbols("daily_bar")`，见 `jobs/evening_routine.py:123`）。
- 「报错分母」= `n_expected = n_ok + len(missing_errs)`，**排除了 `n_suspended`**。停滞时绝大多数标的（今昨都无数据）落进 `n_suspended`，被静默丢弃。
- 由报错串 `X/Y` 反推各失败日的计数（`Y - X = n_ok`）：

| trade_date | 报错串 | missing | n_expected | n_ok（反推） | 说明 |
|---|---|---|---|---|---|
| 2026-09-23 | 25/25 | 25 | 25 | 0 | 当日【零】标的落库 |
| 2026-09-28 | 28/29 | 28 | 29 | 1 | |
| 2026-09-29 | 24/25 | 24 | 25 | 1 | |
| 2026-09-30 | 1/1 | 1 | 1 | 0 | 上一交易日(09-29)仅 000007 有数据 |

**缩小趋势 25→29→25→1 的成因**：
- 【确证】分母 == "当日 或 上一交易日有数据"的标的数。随同步持续失败，落到这两个具体日期的标的越来越少，分母随之塌缩（极端时 09-30 只剩 1）。
- 【推测】这 ~25 只是「最近某次**部分成功**的 sync」留下的更新足迹（例：09-23 sync 摘要 `done=25/2499`）——即只有约 25 只标的被那几次同步真正写入了新数据。**需**结合 `app_state.sync_state` 的 `completed` 列表与逐日 sync 日志核对才能定论。

**当前代码已部分缓解**（本次实测，见 `verify_validate_current.py` 输出）：新增了「全市场硬门禁」
`pipeline.py:74-76,161-168`：
```python
161  if n_universe >= VALIDATE_MIN_MARKET_UNIVERSE:          # 500
162      coverage = n_ok / max(1, n_universe)
163      if n_expected == 0 or coverage < VALIDATE_MIN_MARKET_COVERAGE:  # 0.5
164          raise ValueError(f"validate failed: 全市场覆盖率异常 …")
```
实测：
```
[B 塌缩-全市场] step_validate(2026-10-09, 5220 codes)
    RAISE -> 全市场覆盖率异常 （当日已落库 0/5220 = 0.0%，停牌/无数据 5220 只）
[C 塌缩-小池(100)] step_validate(2026-10-09, 100 codes)
    PASS  -> validated=0/100 suspended=100 missing_vs_prev=0 degraded=0   ← 残留漏洞
```
即：**市场级（≥500）已能正确报"全市场覆盖率异常"；但 <500 的小代码集在 100% 丢失时仍被静默放行**（`n_expected=0 ⇒ tolerance=0 ⇒ 0>0 为 False`）。

---

### Q2「昨有今无」的判据是什么？阈值 5% 怎么用？

**答：对每个 code，先看当日是否有数据；当日没有时，才读上一交易日那一行——昨天有、今天没有 ⇒ 记 `missing`。容差 `= 5% × n_expected`，`missing > tolerance` 才致命（纯比例，无绝对下限）。**

- 比较的两个数据源/日期：**同一个 `daily_bar` 数据集**，`trade_date` 与 `prev = prev_trade_day(trade_date, get_calendar())`（`pipeline.py:110-115,124,136`）。
- 判据只是"当日该标的是否有空 DataFrame"，不做价格比对。
- 阈值常量 `pipeline.py:61` `VALIDATE_MISSING_TOLERANCE_RATIO = 0.05`；判定 `pipeline.py:174-175`。
- 为什么这些标的被判"今天没有"：**因为失败当日 parquet 里确实没有当日行**（见 Q3 的 mtime 证据）。

日期口径**正确**（排除"拿错日期"）：实测 `prev_trade_day(09-29)=09-28`、`prev_trade_day(09-28)=09-24`（09-25 是中秋假期，日历中无该行，已核 `trade_calendar`）；`trade_calendar` 覆盖 2000-01-04~2026-12-31 共 6543 个交易日。

---

### Q3 校验逻辑缺陷（误报）还是数据源真没抓到？

**答：在"数据是否真缺"这一层——是【真实缺失】，不是误报（不是拿错日期/拿错集合/用了未就绪数据）。但在"报错规模"这一层——校验确有缺陷：把一个"全市场 2474/2499 停更"误报成"25 只小池丢了 24 只"。**

关键证据 —— **文件 mtime 证明失败当时数据确实没有**：
```
data/parquet/daily_bar/symbol=000001.SZ/year=2026.snappy.parquet  2026-10-01 21:54
data/parquet/daily_bar/symbol=000007.SZ/year=2026.snappy.parquet  2026-10-01 21:54
```
→ 这些 2026 分区**最后一次写入是 2026-10-01**，晚于所有失败日（09-18~09-30）。**故失败当时文件里不含 09-18~09-30 的行**；现在读 parquet 能看到"数据到 09-30"，是 **10-01/10-02 事后回补**的结果，不是失败当时的状态。

实测（现在读，均已回补）：000001/000002/000006/000007/000008/000009 的 2026-09 日期集合**完整**（09-01…09-24,09-28…09-30，缺 09-25 假期），max 均 2026-09-30。
`data/parquet/.manifest.json` 亦显示 5209/5222 只 `last=2026-09-30`。

**反向对照（同一份代码 + 回补后的数据，逐失败日复跑计数）**——说明"现在能过"完全是因为数据被补回来了：
```
2026-09-04 (prev=09-03): n_ok=5203 missing=2  suspended=15  coverage=99.67%  -> 若当时如此则 '昨有今无 2/5205'（远低于阈值，通过）
2026-09-18 (prev=09-17): n_ok=5205 missing=0  suspended=15  coverage=99.71%  -> 通过
2026-09-23 (prev=09-22): n_ok=5206 missing=0  suspended=14  coverage=99.73%  -> 通过
2026-09-28 (prev=09-24): n_ok=5206 missing=1  suspended=13  coverage=99.73%  -> 通过
2026-09-29 (prev=09-28): n_ok=5207 missing=3  suspended=10  coverage=99.75%  -> 通过
2026-09-30 (prev=09-29): n_ok=5209 missing=3  suspended=8   coverage=99.79%  -> 通过
```
失败当时这些日期的 `coverage` 却是 `1/2499 ≈ 0.04%`（见 Q1 表）。同一代码、同一日期，**只差数据** ⇒ 直接证明失败是"数据缺失"而非"校验误报"。

旁证（数据源真的抓不到）：
- `data_jobs` sync 任务：09-18 `FAILED: PipelineBusy`；09-19 `FAILED: 用户主动停止`；09-27 `FAILED: NOOP done=0/2499`。
- `app.log` 09-19 起大量 `eastmoney daily fetch fail … ConnectionError(RemoteDisconnected)` / `ProxyError('Unable to connect to proxy')`。
- 结构上：`validate` 只做日期过滤（`read_symbol_dataset(start=trade_date,end=trade_date)`），无价格/列口径逻辑，不存在"读到了却说没有"的分支。

**结论**：**不是误报**。日期口径、标的集合、读取路径都正确；`昨有今无` 如实反映"这些标的当日确实没落库"。**校验侧唯一的错误是"规模"**：分母塌缩让全市场停滞看起来像 25 只小池（Q1）。

---

### Q4 09-18 的 `code 必须 6 位数字: 000001.SZ` 是同一根因还是独立缺陷？

**答：独立缺陷（符号格式契约不一致），与分母塌缩无因果，但同样致命（fail-fast 首步中断全链）。已修复。**

调用链：
- `jobs/evening_routine.py:123` `codes = read_all_symbols("daily_bar")` → 返回**带后缀** symbol（实测 2499 只全是 `000001.SZ` 形）。
- `data/pipeline.py:123` `sym = code_to_symbol(code)` → `domain/a_share_rules.py:59-60` 要求纯 6 位：
  ```python
  if not re.fullmatch(r"\d{6}", code):
      raise ValueError(f"code 必须 6 位数字: {code}")
  ```
- `orchestrator.py:811-821` fail-fast，`validate` 又是 `EVENING_STEPS` 的第一项（`orchestrator.py:668`，`FULL_STEPS` 首项 `update_daily` 之后即 `validate`）⇒ **校验一挂，后续 features/infer 全部不执行**。

**修复状态【确证】**：commit `4c4c3d4`（2026-09-19 01:30）在流水线入口统一规范化：
`orchestrator.py:748-761`
```python
for raw in raw_codes:
    try: normalized.append(normalize_code(raw))   # 000001.SZ -> 000001
    except ValueError as e: raise ValueError(f"流水线 codes 参数含非法标的: {e}")
codes = list(dict.fromkeys(normalized)) or ["600519","000001","300750"]
```
且当前 HEAD 包含该提交（`git merge-base --is-ancestor 4c4c3d4 HEAD` 成立）。
⚠️ 时间线注意：该提交在 09-19 凌晨，而失败发生在 09-18 19:32 —— **失败先于修复**，属历史缺陷。

---

### Q5 为什么失败后没有自动重试/告警？失败被吞了吗？

**答：① 重试【有】（最多 3 次、退避 15 分钟），但本案重试无效（根因未消失）、且重试会覆盖同一行；② 告警【缺失】——webhook 默认关、告警规则表为空 ⇒ 失败只在 `data_jobs`/日志里，进不了运维视野；③ 失败【没有被吞】**：`data_jobs.status=FAILED` + 完整 `error_message`/`traceback` 都在。

**① 重试机制【确证】**：`jobs/evening_routine.py:33-38`（`_MAX_ATTEMPTS=3`、`_RETRY_BACKOFF_SECONDS=900`）、`_already_done_today:53-79`、`_mark:82-90`。日志实证同日多次触发：
```
2026-09-23 17:30 / 17:48 / 18:04  → 3 次 validate FAILED
2026-09-28 17:30 / 17:47 / 18:03  → 3 次
2026-09-29 17:30 / 17:47 / 18:05  → 3 次
2026-09-30 17:35 / 18:34          → 2 次
```
即"没有自动重试"的说法不准确——**重试跑了，但当天数据源始终抓不到，重试也白搭**。且 `data_jobs` 对 `(job_type, trade_date)` 唯一（`UNIQUE(uq_datajob_type_date)`），重试是 upsert **覆盖**同一行 ⇒ 表里只留最后一次失败，看不到尝试次数。

**② 告警缺失【确证】**：
- `orchestrator.py:686-699` `notify_failure` 由 `NOTIFY_ENABLED` 把关；`core/config.py:242` 默认 `False`，且 `.env` 未设置该键（已核 `.env` 键列表，只有 `ADMIN_TOKEN/JWT_SECRET/REDIS_*/…`）⇒ webhook 为 **no-op**。
- `api/v1/alerts.py:398-493` 的 `data_health/metric=pipeline` 分支**能**读 `data_jobs` 的 FAILED/陈旧 RUNNING 并告警，但**需要一条已配置的 AlertRule**；实测 `alert_rules`、`alert_events`、`alert` 三表均 **0 行** ⇒ 从未评估、从未产生告警。
- 唯一"主动"信号是内存 SSE：`evening_routine.py:129-130,201-207` `events.publish_threadsafe("monitor", …)`——只在**有客户端在线**时可见，无持久化。
- 附加盲点【确证】：sync 任务即使"几乎全失败"也记 SUCCESS —— `services/sync_service.py:538-539` 仅当 `cancelled / worker_error / noop` 才记 FAILED；per-symbol 抓取失败不计入。故 09-26 `completed=0 failed=2488` 那次仍落 `status=SUCCESS`（`data_jobs` id=14）。

---

### Q6 predictions 缺失，是 validate 中断导致 infer 没跑，还是有独立原因？

**答：主因是 fail-fast（validate 在最前，挂了后面全不跑），但【不止】这一个原因——还叠加了"后端停机未触发"与"事后离线补跑"两类情况。**

**失败链【确证】**：`orchestrator.py:668` `EVENING_STEPS = [s for s in FULL_STEPS if s not in ("update_daily",)]`，顺序为
`validate → enrich_delist → sync_announcements → rebuild_qfq → build_universe → build_universe_bt → build_features → infer → screener_dump → build_cs_mirror`；
`_run_pipeline_impl` 失败即 `return`（`orchestrator.py:811-821`）⇒ **validate 一挂，`build_features`/`infer` 不会执行 ⇒ 无 predictions**。
对应实证：09-18/09-23/09-28/09-29 `daily_pipeline FAILED@validate`，这些日期的 predictions 均缺。

**但不是唯一原因**：
- 09-30：`daily_pipeline` 同样 `FAILED@validate`，可 `predictions/date=20260930.parquet` **存在**，mtime = **2026-10-02 23:30** ⇒ 是**事后离线补跑**产出，非当日流水线。
- 09-14/15/16/17：四个 predictions mtime 均为 **2026-09-18 00:15** ⇒ 也是一次批量补跑。
- 09-16 / 09-21 / 09-22 / 09-24：`app.log` 当天**几乎无日志行**（后端未运行）⇒ 晚间例行**根本没被触发**，与 validate 无关。
  （`app.log` 有日志的日期：09-11,13,14,15,17,18,19,20,22,23,24,26,27,28,29,30 —— 缺 09-16,21,25；09-22 仅 00 时、09-24 仅 00/01/13/14 时。09-25 为节假日。）

**因此**：predictions 的精确缺口（相对交易日历）为 16 天，成因分三类：
- **fail-fast@validate**：09-18, 09-23, 09-28, 09-29（连同 09-30 当日也挂了，但被离线补跑救回）
- **后端未运行/未触发**：09-16, 09-21, 09-22, 09-24（及 10-08/10-09 —— 10-01 之后未再跑流水线）
- **更早的其它问题**：09-01,02,03,08,09,10,11（如 09-04 `infer` 报"特征中不存在交易日 2026-09-04"，属 features 滞后；09-04 的预测由 09-05 00:37 的一次运行补出）

---

## ③ 根因结论

### 确证（有直接证据）

1. **数据面主因**：2026-09-18 ~ 09-30 全市场 `daily_bar` 未能成功更新。证据：`…/year=2026.snappy.parquet` mtime=**2026-10-01 21:54**（晚于全部失败日）、sync 任务 FAILED/PipelineBusy/NOOP、日志大量 `ProxyError/RemoteDisconnected`。**validate 报的"昨有今无"是真实缺失，不是误报。**
2. **校验分母塌缩缺陷**：`pipeline.py:160` 的 `n_expected = n_ok + len(missing_errs)` 排除 `n_suspended`，使"全市场停滞"被报成 `25/25`、`1/1` 这类误导性小分母（`pipeline.py:176-181`）。
3. **符号格式契约缺陷（独立）**：`evening_routine.py:123` 传带后缀 symbol → `code_to_symbol`（`a_share_rules.py:59-60`）抛错 → fail-fast 首步中断。已在 `4c4c3d4`（09-19）修复。
4. **fail-fast 传播**：`validate` 为流水线首步，失败即无 features/infer → predictions 缺口。
5. **告警缺失**：`NOTIFY_ENABLED` 默认 `False`（`config.py:242`）+ 无 AlertRule（`alert_rules` 0 行）⇒ 失败不被主动通知；sync 的 per-symbol 失败还被记 SUCCESS（`sync_service.py:538`）。

### 推测（需补数据）

1. **为何恰好 25~29 只**：推断是"最近若干次部分成功 sync"的更新足迹（`done=25/2499` 量级吻合），需逐年逐日核对 `app_state.sync_state.completed` 与 sync 日志闭环。
2. **后端 09-16/09-21/09-22/09-24 停机原因**：未查（疑似进程被杀/重启脚本），需查系统事件或启动日志。
3. **代理 `127.0.0.1:63978` 是否常驻、有无 NO_PROXY**：未核，需查环境变量与代理进程。

---

## ④ 修复建议（文件:行号 + 改法方向 + 风险）

> 前置说明：**数据面（P0）已由 10-01/10-02 的回补脚本修复**（`daily_bar` 已到 09-30，`predictions` 补出 09-14~17/09-30）。以下针对"根因回归"与"可观测性"。

| 优先级 | 位置 | 改法方向 | 风险 |
|---|---|---|---|
| **P0** | 运行环境（`HTTP(S)_PROXY=127.0.0.1:63978`） | 让代理稳定可用，或对行情域名（`push2his.eastmoney.com`/`finance.sina.com.cn` 等）设 `NO_PROXY` 直连；再跑 `repair` 回补 | 低。不改代理则**停更会复发**（数据面已两度停更） |
| **P0** | `backend/app/data/pipeline.py:176-181` 报错文案 | 把判据主句改为**覆盖率口径**优先（`missing/total`），把 `X/n_expected` 降为附注；或把 `missing_errs[:5]` 改为"共 N 只 + 前 5 只" | 低，纯文案/可观测性 |
| **P1** | `backend/app/data/pipeline.py:161-168` 市场硬门禁 | 补一条**与规模无关**的致命/告警：当 `n_ok == 0 and n_expected == 0` 且显式传入 `codes` 非空 ⇒ 至少 `raise`（或强制 WARNING + 在返回串标 `all_missing=1`）。当前 `<500` 小池在 100% 丢失时静默放行（已实测 C 分支） | **中**：手动重跑常传 3 只；需保留显式 `force` 开关，且勿误杀真停牌日 |
| **P1** | `backend/app/orchestrator.py:686-699` + `jobs/evening_routine.py:199-207` | 失败终态时落一条**持久化**告警（写 `alert_events`）或默认启用 notify；用尽 3 次重试后升级告警 | 低-中：注意勿刷屏（沿用现有冷却） |
| **P1** | `backend/app/services/sync_service.py:538-539` | `failed` 占比过高（如 `failed/max(1,total) > 阈值`，或 `done << total` 早停）也应记 `FAILED`/WARNING，避免 2488/2499 失败被记 SUCCESS | **中**：阈值需校准，勿把"全市场合法停牌"误判 |
| **P2** | `backend/app/db/models.py` `data_jobs`（`UNIQUE(job_type,trade_date)`）+ 重试覆盖 | 增 `attempts` 字段或保留历史，避免重试覆盖后丢失"尝试过几次" | 低（改 schema 需迁移） |
| **P2** | `jobs/evening_routine.py:123` | 直接传 `normalize_code` 后的 codes（入口已兜底，双保险） | 低 |
| **P2** | `backend/app/api/v1/datacenter.py:723` | 已是 `_err[:160]`（旧 `[:60]` 截断已改善），可再改为"关键信息不截断" | 低 |

> 说明：旧报告提到的"前端幽灵文案『部分高频接口建议开启本地缓存同步』"在当前 `frontend/src` 已检索不到，视为已修。

---

## ⑤ 仍需补充的信息

1. `app_state.sync_state.completed`（当前只截到 28 项）+ 逐日 sync 日志 → 确认 `25/29/25/1` 的精确构成（把 Q1 的"推测"升级为"确证"）。
2. 后端 09-16/09-21/09-22/09-24 的停机原因（系统事件/启动脚本）。
3. 代理 `127.0.0.1:63978` 的常驻性与 `NO_PROXY` 现状。
4. **端到端复跑验证**：本次后端未启动（按任务书要求）。建议服务恢复后跑一次 `run_pipeline(2026-09-30, codes=全市场)`，确认 `validate` 现在返回 `validated=5209/5220 suspended=8 missing_vs_prev=3 degraded=0`（本次已用 `step_validate` 单独复现该结果）并全链 SUCCESS。

---

### 附：本次实测命令与产物

- `docs/audit/2026-10-03/_fix/probe_datalink.py`（全市场口径/命名标的数据/逐日计数）
- `docs/audit/2026-10-03/_fix/verify_validate_current.py`（当前代码 validate 行为：A 通过 / B 门禁 / C 小池静默放行）
- `docs/audit/2026-10-03/_fix/probe_out.log`
