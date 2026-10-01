# AQP 上线前代码审查报告（未提交 diff）

**日期**：2026-10-01
**审查人**：product-reviewer（GStack）
**审查对象**：工作区未提交改动（`git status` 20+ M + 若干新增未跟踪）
**审查范围**：逻辑正确性 · 量化口径 · 静默退化 · 前端逻辑 · 架构约定 · 产品一致性
**方法**：`git diff` 逐文件 + 全仓 grep 反模式 + 定向单测复跑 + 与 `docs/项目文档.md §7.7.1/§7.7.2` 对照
**说明**：仅审查、未改动任何生产代码。

---

## ① 结论 TL;DR

**整体：🟡 有条件 Go（3 项 🟠 须上线前处理，无 🔴 阻断）**

- 本轮 diff 的主体质量**很高**：§7.7.2 的 A1/A2/A3/A4/A5/A6/A7 七条纪律在改动中**大部分被主动落实并加了证伪测试**（46 项新增/相关测试全绿，54 项回测/PIT 回归全绿）。统计日劫持（A1）、`sum() or 0`（A2）、墙钟预算（A3）、分区按日裁剪（A4）、缓存仅 ok（A6）、独立指纹缓存（A7）都能在代码里找到对应实现与注释依据。
- 但存在 **3 个 🟠 级「口径不一致 / 残留反模式」**，均属「注释声称已治、实际仍有缺口」型：
  1. **`max_participation` 默认值三处漂移且两处实际未接线**（量化，核心）；
  2. **ETF 总量 `sum(x.get(...) or 0)` 残留 A2**（静默退化）；
  3. **`liquidate()`/`sell()` 的 T+1 闸门为「整单拒绝」而非「部分成交」**（逻辑，保守方向）。
- 未发现 🔴 级前视泄漏：价格基准 `_LABEL_PRICE_BASIS="hfq"` 唯一、`daily_bar.volume` 单位契约（股）已在 baostock 适配器修正且注释留证、`financial_factors` 的 asof join 逐日正确。

**严重度分布**：🔴 0 · 🟠 3 · 🟡 6 · 🟢 5

---

## ② 发现清单

| # | 严重度 | 类别 | 文件:行 | 问题 | 证据 | 建议 |
|---|--------|------|---------|------|------|------|
| 1 | 🟠 | 量化 | `backtest/broker.py:65`、`backtest/strategy_base.py:311`、`api/v1/backtest.py:65` | **`max_participation` 默认值三处不一致，且两条 API 路径实际未接线**：①`BrokerConfig`/`BacktestRequest`=0.05，`run_strategy`=0.0；②`BacktestRequest.max_participation` **仅在 `enable_friction=True` 时**经 `_friction_config` 生效（`backtest.py:83-91`），默认 `enable_friction=False` ⇒ 新 0.05 默认**是死参数**；③`StrategyBacktestRequest` **根本没有 `max_participation` 字段**，`_run_single`(621-634) 调 `run_strategy` 也**不传**该参数 ⇒ 框架策略路径 broker `enabled=True` 但 `max_participation=0.0` ⇒ **完全无流动性闸门**。 | `git diff` 明示 0.0→0.05；`grep -n "max_participation" backtest.py` 仅 65/91/375/411/428；`sed -n '621,634p'` 无该参数 | ①统一三处默认并让 `run_strategy` 显式接收；②把 `max_participation` 从「仅 friction 生效」解耦（参与率闸门与摩擦成本是两件事）；③给 `StrategyBacktestRequest` 补齐该字段并透传。**否则「机构级流动性闸门」在最常用的策略回测路径上不存在** |
| 2 | 🟠 | 静默退化 | `api/v1/etf.py:371,383,389` | **A2 残留**：`total_size_yi = round(sum(x.get("size_yi") or 0 for x in cn), 2)`、`amount_yi = round(sum(x.get("amount") or 0 ...)/1e8, 2)`、`us_size_yi` 同型。而 `size_yi` **确实可为 None**（`data/etf.py:736,765,776` 显式产出 None），`amount` 同为可空。缺失规模被当 0 计入「总量」绝对值，**无任何 degraded 标记**——正是 A2 要消灭的「0 冒充不可得」。对照：同文件 `net_inflow` 已正确用 `None + flow.status=unavailable`。 | `data/etf.py:736 "size_yi": (round(e["float_mv"]/1e8,2) if e["float_mv"] else None)` | 与 `net_inflow` 同口径：无法覆盖全部标的时返回 `None` + `status`/`reason`（或至少披露 `n_null`）。判据用有效观测数而非 `or 0` |
| 3 | 🟠 | 逻辑（保守向） | `backtest/broker.py:328-335` | **T+1 闸门为整单拒绝**：`if available < int(qty) and locked > 0: return reason="t1"`。当 `holdings=200` 且 `locked_today=500`、卖单 `qty=600` 时，可卖的 200 股本应部分成交，却被**整单拒绝**。方向是**保守（少成交→收益偏低）**，不会造成乐观偏差，但会让「T+1 部分可卖」场景的回测换手/成本偏低。**无泄漏风险**（`qty = min(int(qty), available)` 已确保锁定仓不可卖）。 | `sed -n '328,340p' broker.py` | 若追求真实度，改为「截断到 available 部分成交 + 剩余 reason=t1」；若刻意保守，请在 docstring 注明该取舍（现注释只说"触发 T+1 拒绝"，未说明整单 vs 部分） |
| 4 | 🟡 | 量化 | `backtest/strategy_base.py:439-451` | **`uni_d` 缺 `factor` 列** ⇒ broker `_row()` 的 `factor` 恒为 1.0 ⇒ `_lot_size()` 恒为 100、`_daily_amount_hfq()` 不做 raw→hfq 换算。当前 qfq 域下 `amount`/`price` 同域，参与率比值**自洽**，故非硬错误；但**与 `engine.py` 路径（有 factor 列）口径不同**，两路径的整手/参与率在除权标的上会产生系统性差异。 | `sed -n '439,451p' strategy_base.py` 无 factor；`_row` default=1.0 | 若两路径应同口径，在 `run_strategy` 的 `uni_d` 补 `factor` 列；若刻意不同，注释需说明（当前 docstring 只提"bars 为 QFQ 口径"） |
| 5 | 🟡 | 静默退化 | `api/v1/watchlist.py:82-84` | `highs = [b.get("high") or b.get("close") or 0 ...]`、`vols = [b.get("volume") or 0 ...]`：缺失 high 回落 close 是**有意**兜底（可接受），但缺失 volume 落 0 会污染 `_kline_state` 的量能判据（"缩量/放量"可能被误判）。属 A2 边缘。 | `sed -n '78,90p' watchlist.py` | volume 缺失时该 bar 应跳过或整体判 unavailable，勿以 0 参与量能比较 |
| 6 | 🟡 | 架构 | `api/v1/backtest.py:906` | 框架策略重计算走 `await asyncio.to_thread(_run_strategy, req)`，**未走 `core/compute_pool.get_compute_pool()`**。该项目约定重计算下沉专用池以免挤占 `/health/ready` 槽位（diff 中 `market.py`/`etf.py` 均已改）。`_run_strategy` 内含 walk-forward 参数寻优，属重计算。 | `grep "compute_pool" backtest.py` 无命中；同文件已 import `compute_slot` 但那是并发信号量非线程池 | 与 `market.py:1098` 同法，改为 `run_in_executor(get_compute_pool(), ...)` |
| 7 | 🟡 | 逻辑 | `data/etf.py:439-478` `us_real_symbol` | `qt` 回传解析取**第一个**含点号的 `v[2]`：`for v in (node.get("qt") or {}).values(): if ... "." in v[2]: real = v[2]`。若一次探测响应含多个 `qt` 条目，可能取到**非本次探测候选**的交易所。docstring 声称已实证（"请求 usSPY.OQ 答 SPY.AM"），风险低但仅靠 24h TTL 兜底。 | `sed -n '455,478p' etf.py` | 以 `node.get("qt", {})` 的 **key==cand** 精确取值，而非取任意 `values()` 首个 |
| 8 | 🟡 | 产品/一致性 | `api/v1/backtest.py:59-65` vs `strategy_base.py:318-323` | **注释声称与实际不符**：`run_strategy` docstring 写「API 回测路径走 `enable_friction` + `BacktestRequest.max_participation`（默认 5%）」，但如上 #1，策略回测路径根本无该字段。docstring 的"担保"超出代码"做的"。 | 见 #1 证据 | 修正 docstring 或补齐接线（二选一，勿留错误担保） |
| 9 | 🟡 | 逻辑 | `data/etf.py:534-543` `fetch_kline` | 超限自检 `if market == "us" and limit > 1 and len(out) == 1: logger.warning(...)` 只说「疑似后缀错误」，**未返回 degraded 标记**。前端拿到 1 根会当作正常短序列 → 恰是本次要修的「静默退化」同类。 | `sed -n '534,543p' etf.py` | 至少让 API 层可感知（返回 degraded 或单独字段），否则告警只进日志 |
| 10 | 🟢 | 静默退化 | `api/v1/etf.py:360` | `sum(f["net_inflow"] or 0 for f in flow if f.get("net_inflow"))`：`or 0` 是**死代码**（被 `if f.get(...)` 守卫），但形态仍是 A2 靶点；且 `if f.get("net_inflow")` 会把真实 `0.0` 净流入**从 sum 中剔除**（对求和无害，但语义上是"忽略 0"）。 | 同上 | 去掉 `or 0`；如需排除 None，用 `if f.get("net_inflow") is not None` |
| 11 | 🟢 | 架构 | `db/init_db.py:69-73` | `ensure_columns` 为 `financial_report` 补 `announce_basis`/`gross_margin`/`bps`（幂等），与 `models.py` 同步，符合既有迁移模式。**核对通过**（仅记录）。 | diff | 无 |
| 12 | 🟢 | 架构 | `data/ingest/multi_source.py:48` | `from ..domain.a_share_rules` 修正为 `from ...domain.a_share_rules`（层级 bug 修复）。**核对通过**。 | diff | 无 |
| 13 | 🟢 | 逻辑 | `backtest/broker.py:219-231` | `_sell_cost` 统一走 `effective_stamp_duty`/`effective_transfer_fee`（ETF 豁免、分段印花税、过户费双边）。**核对通过，未见单位错误**。 | diff + 全文 | 无 |
| 14 | 🟢 | 前端 | 全仓 | **涨跌染色 `null⇒中性` 合规**：`KpiCards`/`resultParts`/`BreadthPanel`/`Etf` 均用 `x == null ? 中性 : (x>=0 ? t-up : t-down)`；`MarketHeatmap` 的 `?? 0` 有 `ok` 守卫。`total_yi`/`pred_score` 类型为非空 number，无 null 染色洞。 | grep 全仓 | 无 |
| 15 | 🟢 | 前端 | 全仓 | **`setOption(option,true)` 合规**：未加 `true` 的调用（`resultParts:162,225`、`DataCenter:87`、`AiPicksPanel:100`、`MoneyFlowPanel:68` 等）均**每次 `echarts.init` 新建+`dispose`**，故 notMerge 无害；真正的持久实例（`KLineChart`/`Etf`/`Portfolio`/`Screener/DistributionCharts`）**均已带 `true`**。`theme` 也进了依赖数组（30+ 处 `}, [..., theme]`）。 | grep + 逐文件读 | 无 |

---

## ③ 已回归的既有清单项核查（§7.7.1 / §7.7.2）

**§7.7.2（A1~A8）——本轮 diff 的主动落实（未见回归）：**

| 条目 | 状态 | 证据 |
|------|------|------|
| A1 统计日不得取 `max(date)` | ✅ 已修 | `market.py:_pick_stat_day`（相对判据 `n_max//2`、不硬编码池规模）；`_heat_from_local`/`_sectors_from_local` 共用；披露 `data_date`/`coverage_symbols`/`latest_date` + 跳过天数 |
| A2 禁止 0 冒充不可得 | ⚠️ 主体已修，**etf.py:371/383/389 残留** | `market.py` 用 `len()-null_count()>0` 判据；但见发现 #2 |
| A3 墙钟预算 ≥ 最坏耗时 | ✅ 已修 | `DAILY_BUILD_TIMEOUT_SECONDS=20.0`，注释列实测（冷 7.6s / 稳态 8.6s / 留 2.3× 余量）；degraded 走 SWR 短 TTL 自愈 |
| A4 分区按业务日裁剪 | ✅ 已修（保守正确） | `_build_ai_stats` 按预测日跨度裁 hfq 分区，注释明确"只裁起点安全、终点留足前瞻窗口" |
| A5 新增字段不得与保留键同名不同型 | ✅ 已修 | 用 `coverage_symbols` 而非 `coverage`（避开 `BlockBase.coverage` 对象），注释留证 |
| A6 缓存不得缓存降级载荷 | ✅ 已修 | `_ai_stats_cache` 仅写 `status=="ok"`；日频块顶层 `status=degraded` ⇒ 短 TTL |
| A7 贵子块独立缓存 + 内容指纹 | ✅ 已修 | 签名 = 全部 predictions 分区 `(name,mtime_ns,size)` + 配置；指纹取不到 ⇒ 退化为不缓存 |
| A8 每个修复配证伪 | ✅ 已配（46 新测试全绿） | `test_heat_local_stat_day_robustness`/`test_sectors_local_stat_day`/`test_ai_stats_*`/`test_build_daily_parallel_and_budget` |

**§7.7.1（L1~L5 前视）——本轮相关项核查：未见回归。**
- 标签/推理价格基准：`_LABEL_PRICE_BASIS="hfq"` 唯一事实源，`_build_ai_stats` 全路径携带 ✅
- 财务 PIT（L2）：`financials.py` 重写为「实际披露优先 / 首次预约回落」，**明确拒绝 `stock_yjbb_em` 的「最新公告日期」未来函数**（实测该列+1 年）；`load_financials_asof` 双分支 SQL 正确；`financial_factors.py` 逐日 `merge_asof(direction=backward, allow_exact_matches=True)` ✅
- 公告 PIT（`announcements.py`）：修掉「`pub_date` 覆盖为入参 start」，改用接口真实公告日 ✅

---

## ④ 未覆盖面 / 局限

1. **`git diff` 不能全量**：仓库有**损坏的 git 对象**（`git fsck` 报 `invalid reflog entry`，diff 报 `unable to read adf4e2b7...`）。本次以**逐文件 `git diff HEAD -- <file>`** 绕过；`api/v1/etf.py` 与 `frontend/src/pages/Etf/index.tsx`（staged `MM`）的**旧版 blob 缺失**，其改动以**当前文件内容 + 上下文**判定，未逐行比对旧版。
2. **未做端到端 live 验证**：`_pick_stat_day`、`us_real_symbol`、`_ai_stats` 独立缓存的实际命中率/失效行为**未跑真实数据**（依赖其自带的证伪测试）。
3. **未覆盖**：`frontend/` 全部 40+ 改动仅抽查了染色/取色/setOption/降级组件四类高危模式，未逐组件审语义；`scripts/`、`.github/workflows/ci.yml`、`tailwind.config.js` 未审。
4. **未验证**：#1 的 `max_participation` 默认漂移对**既有回测缓存 key** 的影响（`_strategy_cache_key` 含该参数值，`backtest.py:426-428`）——改默认会另开缓存路，需确认线上无需清缓存（属运维项）。
5. **测试环境**：`python -m pytest` 需项目 venv（`.venv/Scripts/python.exe`，py3.11）；系统 py3.13 无 pytest。相关测试共 **100 项全绿**（46 新增 + 54 回归）。

---

*本报告仅为代码审查产出，未修改任何生产代码。*
