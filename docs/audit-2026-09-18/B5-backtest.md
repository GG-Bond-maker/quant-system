# B5 · 回测与交易 —— 深度审核报告

> 审核员视角：**量化研究员｜苏砚**（A 股交易规则 / 因子 / 回测）
> 日期：2026-09-18 · 环境：`backend/.venv`（Python 3.11.15）· 纪律来源 `docs/audit-2026-09-18/AUDIT-BRIEF.md`
>
> **证据纪律**：本报告所有「确定」结论均已用命令实际执行。控制台为 GBK，命令输出中的中文
> 乱码已按原文转录为可读文本，数值未做任何改动。所有合成实验只写内存/临时对象，未写仓库 `data/`。

---

## 0. 逐文件结论行

| 文件 | 结论 |
|---|---|
| `backend/app/backtest/engine.py` | **有 P1**：衰减成本在非交易日重复扣费（B5-01）；月度单调比例方向反了（B5-05）；换手口径双边（B5-08）。撮合时序与 T+1 **正确**，风险权重方案无前视 **正确**。 |
| `backend/app/backtest/broker.py` | **有 P1 级影响**：`last_day_turnover` 陈旧（B5-01 根因）；ETF 卖出误收印花税（B5-10）；T+1 部分成交语义错（B5-15）；缺 universe 的订单静默丢失（B5-16）；无过户费（B5-18）。闸门本身（涨停/跌停/停牌/整手/最低佣金/卖出印花税）**实现正确且有测试**。 |
| `backend/app/backtest/strategy_base.py` | **有 P2**：基准降级被呈现为 0.0% 收益（B5-12）；`decay_bps` 死配置（B5-17）。事件驱动主循环时序 **正确**（T 日 on_bar → T+1 开盘撮合）。 |
| `backend/app/backtest/ma_cross.py` | **P2/D 类**：旧引擎无任何 A 股闸门且停牌按最后价成交（B5-13），但仅 `use_legacy_engine=true` 可达；`day_idx` 是**无害残留**（B5-D1）。`_is_etf_symbol` 的 `("5","15","16","56","58")` 前缀元组有冗余但无害。 |
| `backend/app/backtest/param_search.py` | **有 P1**：`grid_search` 在 `max_trials` 守卫之前物化整个笛卡尔积 → 单请求可 OOM 打死单 worker 进程（B5-06）。DSR/n_trials 防过拟合设计**真实存在且正确方向**；walk-forward **无 embargo**（C4）。 |
| `backend/app/trading/paper.py` | **有 P1**：无持仓校验的裸卖凭空造现金（B5-03）；冲击成本二次计费（B5-09）。停牌处理正确（停牌日无 bar → 不顺延成交，实测抽样 9047 行 volume≤0 为 0）。 |
| `backend/app/jobs/evening_routine.py` | **该文件未发现 P0–P2 问题**。幂等（`_already_done_today`）、非交易日跳过、各环节独立 try/except、`BaseException` 兜底与 `is_fatal_base_exception` 放行均正确。 |
| `backend/app/api/v1/backtest.py` | **有 P1**：策略回测缓存键漏 `use_legacy_engine`（B5-02）；walk-forward 最终 KPI 是全样本内（B5-07）；策略路径涨跌停是幅度近似（B5-11）；披露文本与磁盘事实不符（B5-04）。`/run` 的全参数入键**已核实完整**。 |

---

## 1. 本批重点逐条裁定（先给结论，后给证据）

### 1.1 撮合时序：t 日信号 → t+1 开盘成交（**正确，无前视**）

**证据（代码）**：`engine.py:350-363`

```python
sig_date = dates[i - signal_lag] if i >= signal_lag else None      # 用 T-1 的信号日
sig_d = sig_by_date.get(sig_date, pd.DataFrame()) if sig_date else pd.DataFrame()
...
trades = rebalance_to_weights(broker, d, uni_d, tw)                # d = T 日
```
`rebalance_to_weights`（`engine.py:143`）调 `_open_prices(uni_d)` → `broker.match(d, ...)` →
`broker.buy/sell` 一律以 `open_` 计价（`broker.py:288,325`）。即 **T-1 日收盘信号 → T 日开盘成交**，
不存在同日收盘价成交。`signal_lag < 1` 直接抛错硬约束（`engine.py:319-320`）。

**证据（运行）**：`tests/test_backtest.py::TestEngine::test_tc_engine_no_lookahead`（已存在且通过）
构造「第 5 日才出现信号、断言前 5 日持仓为空、第 6 日才建仓」。
`strategy_base.run_strategy` 与 `ma_cross.run_ma_cross` 同口径（`pending` 由 T 日收盘产生，T+1 执行，
`strategy_base.py:452-454`、`ma_cross.py:214-227`）。

### 1.2 T+1：**真实现**（当日买入不可卖），但有一处语义错误

**实现方式**：`broker.buy` 把成交股数写入 `_locked_today`（`broker.py:347`），
`broker.sell` 只从 `self.holdings` 取可卖量（`broker.py:272`），
`mark_to_market` 才把 `_locked_today` 解冻进 `holdings`（`broker.py:184-186`）。
因此当日买入的股票**物理上不在可卖集合里**，T+1 成立。已被
`test_backtest.py::TestBrokerRules::test_tc_t1_buy_locked_then_sellable` 与
`test_locked_not_sellable_via_match` 覆盖。

**语义错误（B5-15，P3）**：`broker.py:274-276` 的拒绝条件是
`if available < int(qty) and locked > 0: return Trade(..., reason="t1")` ——
只要「可卖量 < 委托量」且当日有锁仓，**整单拒绝**，而不是「先卖可卖的 available 股」。
运行验证：

```
$ .venv/Scripts/python.exe -c "... Broker(init_cash=100000); b.holdings['600519.SH']=1000; b._locked_today['600519.SH']=500; b.sell(...,qty=1200,row)"
available=1000 locked=500 qty=1200 -> t1 0      # 期望：成交 1000（部分成交）
available=1000 locked=500 qty=900  -> filled 900
```
**可达性**：引擎生成的卖单量恒 ≤ `broker.holdings[sym]`（`engine.py:153,164`），
所以该分支**在回测路径上不可达**；只有直接调用 `Broker` 公共 API 才触发。故定级 P3。

### 1.3 涨跌停：**Top-K 路径用真实列；策略路径是幅度近似；旧引擎完全无闸门**

| 路径 | 涨跌停来源 | 判定方式 |
|---|---|---|
| `POST /backtest/run` | `universe_daily_bt` 的 `limit_up/limit_down` 列（`universe.py:436-448`，由 `domain/limit.py` 板块规则作用于 hfq 昨收、ROUND_HALF_UP 到 0.01） | `open >= limit_up*0.9999` 拒买 / `open <= limit_down*1.0001` 拒卖（`broker.py:312,270`）——**真实价，非幅度近似** |
| `POST /backtest/strategy-run`（默认框架版） | `backtest.py:335-338` **现算**：`round(close.shift(1)*(1+pct), 2)`，`pct` 只按板块取（`{"bse":0.30,"chinext_star":0.20}.get(board,0.10)`） | 同上，但价格是**近似值**：ST 的 5% 未处理、新股不设限未处理、交易所 raw 域取整未复现（见 B5-11） |
| `strategy-run` + `use_legacy_engine=true` | 无 | **完全无涨跌停闸门**（`ma_cross.py:155-212` 的 `execute_target` 里没有任何 limit 判断） |

涨停能否买入 / 跌停能否卖出：Top-K 与框架路径**都不能**（有一字板测试
`test_backtest.py::test_tc_engine_respects_limit_up`、`test_ma_cross_gates.py::test_framework_macross_respects_limit_up`）。
框架路径的 limit 列缺失时 `broker._row` 退化为 `close*1.2 / close*0.8`（`broker.py:220-221`），
即主板 ±10% 的股票 +13% 开盘会被放行买入——该退化行为已被
`test_ma_cross_gates.py::test_limit_columns_absent_keeps_legacy_default` 固化为「旧行为兼容」，
但它同时用**当日 close**（成交时点的未来信息）推导涨跌停价，属于兜底路径内的隐性前视。

### 1.4 停牌：Top-K/框架路径**不按最后价成交**；旧引擎**会**

- Top-K：`is_halted`（`volume<=0`）或 `volume<=0` 或 `open<=0` → 拒绝（`broker.py:268,310`）；
  `mark_to_market` 在 close 为 NaN 时保持 `_prev_close`（`broker.py:190-195`）→ 估值冻结、不成交。**正确**。
- 框架路径：停牌日在 qfq 数据集中没有 bar → `px_volume[s].get(d)` 缺键 → `NaN` → `_num` 默认 0 → `halted`（`strategy_base.py:429`）。**正确**。
- **旧引擎（B5-13）**：`ma_cross.py:116` 把缺失的 open 填成 `last_c`（最后收盘），
  `execute_target` 无任何 halted 判断 → **停牌期间按最后价成交**。这正是本批重点要抓的形态，
  但因为 `use_legacy_engine` 默认 false（`backtest.py:300`）且前端从不下发该字段，实际不可达。

### 1.5 整手：买入**向下取整到 100 股**（三条路径都做了，但域不同）

- `broker.buy`：`lot = self._lot_size(factor)`；`qty = floor(use_cash/price/lot)*lot`，不足一手拒（`broker.py:327-332`）。
  `factor` 存在时 lot = `round(100/factor)`（`broker.py:231-239`），把整手纪律换算到物理股域——**设计正确**。
- `ma_cross.execute_target`：`lots = floor(budget/(px*LOT_SIZE))`（`ma_cross.py:200`）。**正确**。
- `paper.run_fills`：`qty = floor(amt/exec_px/LOT_SIZE)*LOT_SIZE`（`paper.py:223`）。**正确**。
- **一致性缺口（B5-19）**：`factor` 列只存在于 `universe_daily_bt`；`_load_strategy_bars` 的 `keep`
  白名单（`backtest.py:339-341`）不含 `factor` → 所有 `/strategy-run` 请求的 broker 都按 `factor=1.0`
  运行，Task 1 的「hfq 域等效整手」机制**在该端点上恒为 inert**。同时策略路径喂的是 **qfq** 价、
  Top-K 路径喂的是 **hfq** 价，两套价格域共用同一个 Broker 而只有一套做了 factor 换算 → 整手/金额
  的口径在两个端点间不可比。

### 1.6 费用：佣金（含最低 5 元）✔ 印花税（仅卖出、0.05%）✔ 过户费 ✘

- 佣金：`max(5.0, amount*commission_rate)`，双边（`broker.py:171-172`，默认 0.0003）。**与现行规则一致**。
- 印花税：`_sell_cost = commission + amount*stamp_duty`，仅卖出，默认 0.0005（2023-08 减半后现行 0.05%）。**正确**。
- **过户费缺失（B5-18，P3）**：`rg "过户|transfer_fee|TRANSFER" app/` → 0 命中。现行 0.001% 双边
  （沪深均收），往返少计约 0.2bp。量级可忽略，但属于规则不全。
- **ETF 印花税不一致（B5-10，P2）**：`broker._sell_cost` 对**所有**卖出收印花税；
  而 `ma_cross._is_etf_symbol`（`ma_cross.py:180-181`）、`paper`（`paper.py:229-230`）、
  `domain/portfolio.py:303` 都豁免 ETF；`domain/a_share_rules.stamp_duty_rate` 也豁免。
  即同一平台有 **4 套费用实现 + 1 套无人调用的「规则模块」**（见 B5-D2）。

### 1.7 滑点 / 冲击成本：模型存在，但**默认关闭**，且衰减项有重复计费 bug

- `/backtest/run` 的 `enable_friction` **默认 False**（`backtest.py:36`）→ `friction=None` →
  `_friction_price` 原价（`broker.py:120-121`）、`_impact_cost` 恒 0（`broker.py:137-138`）→
  **默认展示的回测只有佣金+印花税，零滑点零冲击**。响应里 `liquidity.impact_cost_included=false` 有披露，属于合规，
  但数量级影响极大（见 §4-C1 实测 29.7pp/年）。
- `/strategy-run` 恒开摩擦（`strategy_base.py:367` `BrokerConfig(slippage_bps=..., enabled=True)`），
  但 **`decay_bps` 在该路径永不生效**（B5-17，`apply_decay_cost` 未被调用）。
- 冲击模型本身合理（sqrt 参与率模型 `broker.py:139-147`），默认 `linear` 口径偏"薄"：
  线性冲击只在 `amount > daily_amount*2%` 后才计费，小单免费（`broker.py:149-153`）。

### 1.8 退市 / ST / 新股：**幸存者偏差完全存在**，且披露文本与磁盘事实相反（B5-04）

这一条是本批**最严重的策略问题**，实测证据如下。

```
$ .venv/Scripts/python.exe -c "sqlite3 只读连接 data/sqlite/aqp.db"
instrument total: 5552
is_st=1        : 205
delist notnull : 0          <-- 全部为 NULL
list_date null : 5430       <-- 97.8% 为 NULL
```

```
universe_daily_bt: years=2022..2026, rows=1,283,107, dates=1133, 2022-01-04~2026-09-04
symbols 1133
is_st True rows: 52118 (symbols 46)
is_halted rows : 44280 (3.45%)
每月末宇宙规模：2026-04..2026-09 全部恒为 1133
最后真实 bar 早于 2026-01-01 的标的数: 0
```

**推论（逐条都有代码/数据支撑）**：

1. `universe.py:406-407` 的「退市证券按 delist_date 移出宇宙」过滤器，在 `delist_date` 全 NULL 的
   生产库上**恒真、永不生效**；`upsert_delist_dates`（`ingest/tasks.py:60`）**零调用方**（见 B5-D3）。
2. 落盘宇宙里 **1133 只标的全部存活到 2026-09-04**，没有任何一只有"最后一根真实 bar 且之后被剔除"
   的形态 → **2022–2026 期间真实退市的股票完全不在宇宙里**（下载集合 = 当前在市证券）。
   这就是彻底的幸存者偏差。
3. 因此 `engine.py:377-388` 的 `absent_streak` + `broker.liquidate` 退市强平路径**在生产数据上不可达**
   （symbol 不会从 index 消失，只会变成 `is_halted=True` 的永久占位行）。
4. `backtest.py:199-200` / `:680-681` 的披露
   `"股票池=本地已下载数据集（含退市证券的历史 bar，直至其 delist_date）；delist_date 之后的日期已从宇宙剔除，超期持仓按最后收盘×haircut 强平减记"`
   **与磁盘事实不符** —— 违反 P0 契约第 5 条「派生指标必须披露口径，不允许把合成数据冒充真实行情」。
5. `list_date` 97.8% 为 NULL → `universe.py:402-403` 的「仅保留已上市」过滤器也是 no-op：
   实测 `001306.SZ`（首根真实 bar 2023-11-16）在宇宙里有 **452 行 2022-01-04 起的上市前占位行**。
   连带 `is_new_issue`（`universe.py:425-430`）恒 False → **新股前 5 个交易日「不设涨跌幅」规则失效**，
   被套上 ±10%/±20% 的假限价：真实大涨日会误拒买入、真实暴跌日会**误拒卖出**（跌停闸门把可卖判成不可卖）。
6. `is_st` 用的是 **instrument 表当前快照**（单标量，非时点字段）→ 46 只当前 ST 股的全部历史都被套上 ±5%
   （`universe.py:112`），历史上曾 ST 而现在摘帽的股票则被套上 ±10%。这是时点错误（look-ahead），
   代码注释已自认（`backtest.py:329-330`「ST 历史口径缺失沿用当前快照限制」），但方向并非总是保守。

### 1.9 `param_search.py` / `grid_search.py`：同一段历史挑最优？有 DSR；walk-forward 缺 embargo

- `grid_search`/`optuna_search`/`genetic_search`（`param_search.py`）都在**同一段历史上**选最优
  （`evaluate` 就是全区间回测），**但**：`SearchResult.deflated_sharpe` 用 `n_trials=len(trials)` 对最优净值
  做 Deflated Sharpe（`param_search.py:113,261,208`），这是**真实的防过拟合闭环**，不是装饰。
- `scripts/grid_search.py` 是 **LightGBM 超参**搜索（不是回测参数）：`pick_top1` 的排序键只含
  valid/train 字段、显式不含 test（`grid_search.py:60-65`），test 仅记录。**纪律正确**。
- `walk_forward_search`（`param_search.py:287-333`）本身是真 walk-forward（IS 寻优 → OOS 评估）。
  **但**：`backtest.py:435-439` 的窗口切分 `is_e = all_dates[(i+1)*seg-1]`、`oos_s = all_dates[(i+1)*seg]`
  —— **IS 与 OOS 完全相邻，无 purge、无 embargo**。对本批的 MA 类策略影响被"冷启动"偶然掩盖
  （OOS 段 deque 需要 `long_ma` 根 bar 才有信号，等效天然 gap），但对任何带状态/标签重叠的策略就是泄漏。
- **B5-07（P2）**：walk-forward 跑完后 `base_overrides = 最后一折的 best_params`（`backtest.py:550`），
  再用它跑**全区间**回测（`backtest.py:571`）→ 响应的 `kpi.sharpe / annual_strategy / max_drawdown`
  是**样本内数字**（最后一折的 IS = 前 3/4 全历史）。前端把 KPI 放在 WF 表格**上方**且无"样本内"标注
  （`frontend/src/pages/Backtest/index.tsx:98-110`）。`mean_oos_sharpe` 虽在表里，但用户第一眼看到的是前者。

### 1.10 「两套实现」：实际是**四套结算路径**，哪套活、哪套死（台账条目的延伸）

| 端点 / 入口 | 结算实现 | T+1 | 涨跌停 | 停牌 | 整手 | 印花税 | 滑点/冲击 | 状态 |
|---|---|---|---|---|---|---|---|---|
| `POST /backtest/run`；`scripts/run_backtest_real.py`、`ab_backtest.py`、`p2_experiment.py` | `engine.run_backtest` + `broker.Broker` | ✔ | ✔ 真实列 | ✔ | ✔ factor 域 | 收（ETF 也收） | **默认关** | **活（主）** |
| `POST /backtest/strategy-run`（默认） | `strategy_base.run_strategy` + `broker.rebalance_equal_weight` | ✔ | ~ 幅度近似 | ✔ | ✔（factor=1） | 收（ETF 也收） | 恒开（decay 失效） | **活（策略页）** |
| 同上 + `use_legacy_engine=true` | `ma_cross.run_ma_cross` 自带 `execute_target` | ✘ | ✘ | ✘（按最后价成交） | ✔ | ETF 免 | 仅滑点 | **半死**（仅 API 开关可达；前端从不下发 → 见 F-07；无任何测试、无脚本调用） |
| `POST /portfolio/backtest`（**跨批**，属 B7b/P2-D） | `domain/portfolio.run_portfolio_backtest`（自带） | ✘ | ✘ | ✔(NaN) | ✔ | ETF 免 | 有 | 活（组合页） |

**结论（台账要求的具体裁定）**：
- **哪套活**：`engine+broker`（Top-K）与 `strategy_base+broker`（策略页默认）是活路径，二者**共享 broker**，
  因此闸门逻辑同源、结果口径不会分裂；分裂点在**喂进去的价格域**（hfq vs qfq）与**limit 列来源**（真实 vs 现算）。
- **哪套死**：`ma_cross.run_ma_cross` 在当前代码库里除 `use_legacy_engine=true` 外**无任何调用方**
  （`rg run_ma_cross` 仅命中 `backtest.py:377,385` 的 legacy 分支与 `:500` 的预热 import），
  且 `rg "run_ma_cross|use_legacy_engine" backend/tests` 只命中注释一句 → **零测试覆盖**。
- **能否安全删**：**不能直接删**，它是 `docs/audit/ab/task7-AB.md:42` 记录在案的 A/B 对照基线
  （"旧引擎结果偏乐观"这一结论的唯一可复现证据来源）。
  删除前必须确认四件事：
  (1) 移除 `StrategyBacktestRequest.use_legacy_engine`（`backtest.py:300`）与 `_run_single` 的 legacy 分支（`:375-390`）；
  (2) 移除 `_run_strategy` 的 `liquidity` 分支（`:594-600`）与 `_run_strategy` 内的预热 import（`:500`）；
  (3) 确认 `docs/audit/ab/*.md`、CI 配置、外部脚本没有任何命令引用 `use_legacy_engine=true`；
  (4) 把 `MaCrossResult/monthly/risk` 字段契约的"参考实现"责任显式移交 `strategy_base.StrategyRunResult`
  （两者的 `summarize_risk` 至今是**逐行复制的两份**：`ma_cross.py:245-287` vs `strategy_base.py:260-299`）。

### 1.11 性能：**无可向量化的 O(n²) 热点**（实测线性）

```
rows=500000 symbols=2000 days=250
  weighting=equal        1.02s
  weighting=risk_parity  1.39s
group_backtest(200x250,5组) 6.07s
run_strategy(10x250)      0.34s
run_ma_cross(10x250)      0.02s
```
主循环已是 `groupby("date")` 预分组（`engine.py:328-332`，注释 CRIT-6），协方差只回看 `window+1` 天
（`engine.py:222`）。逐日 `uni_by_date[d].set_index("symbol")`（`engine.py:347`）是 O(D·U log U) 的
可优化点，但 2000×250 实测总耗时 1.02s，**不构成 P2**。`run_group_backtest` 因每组每日各调一次
`rebalance_equal_weight`（5 组 × 250 日 = 1250 次）而比单引擎慢 ~6×，仍为线性，可接受。

### 1.12 `app/backtest/ma_cross.py:104 day_idx`：**无害残留**（F841）

```
$ rg -n "day_idx" backend/app backend/tests backend/scripts
backend/app/backtest/ma_cross.py:104:    day_idx = {d: i for i, d in enumerate(all_days)}
backend/tests/test_qlib_vnpy_upgrade.py:235-236:  （测试内同名局部变量，与本文件无关）
```
定义后从未被读取，`all_days` 的索引在 `ma_cross.py:214` 直接用 `enumerate` 取。
删除它不改变任何行为（`ma_cross.py:98-104` 无副作用依赖）→ **无害残留，安全删**。

---

## 2. 目标 A —— Bug 明细

### B5-01【P1·Bug】衰减成本在**非交易日重复扣费**（陈旧 `last_day_turnover`）

**位置**：`backend/app/backtest/engine.py:369-371`（扣费）+ `backend/app/backtest/broker.py:397`（更新点）；
**同一缺陷在分组回测里重复一份：`engine.py:490-491`**（`b.apply_decay_cost(b.last_day_turnover, b.total_equity)`，
`rg apply_decay_cost` 全仓只有这 2 个调用点，均在 engine 内）。
**现象**：`broker.last_day_turnover` 只在 `match()` 内被赋值（`broker.py:397`）。当某日既无卖单也无买单时
（`rebalance_to_weights` 早退，`engine.py:145-146/169/187` 都不调 `match`），该字段保留**上一交易日的值**。
而 `run_backtest` 在每个交易日无条件调用 `apply_decay_cost(broker.last_day_turnover, prev_equity)`
（`engine.py:369-371`）→ **同一个换手率被反复收费，直到下一次真正成交**。
次要根因：`rebalance_to_weights` 分两次调 `match`（`engine.py:169` 卖、`:188` 买），第二次调用会把
`last_day_turnover` **覆盖成只含买单腿**的换手率，卖出腿的换手丢失。

**触发条件**：`enable_friction=true` 且 `decay_bps>0` 且组合存在"持有不动"的交易日（等权+容忍带下极常见）。
**量级**：杠杆 = `last_day_turnover × decay_bps/1e4`。默认换手 0.95 × 10bp = 9.5bp/日 → 每 100 个不动日
≈ 9.5% 权益。实测 21 个交易日、**只有 1 笔真实成交**：

```
$ .venv/Scripts/python.exe -c "... 21 天恒定价格、单标的、top_k=1、decay_bps=10 ..."
raw      final equity=99971.50  nav=0.9997   sharpe=-3.550
friction final equity=98089.06  nav=0.9809   sharpe=-240.193
turnover series: [0.0, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, ...]   # 全程 21 天都是 0.95
friction_costs : {'slippage': 0.0, 'impact': 0.0, 'decay': 1882.44}
n_days=21, trades=1
```
即：**1 笔成交扣出了 21 天的衰减成本**，权益 −1.91%，Sharpe 从 −3.55 崩到 −240。
**最小验证**：上面的 `python -c` 片段（合成行情，2 秒可复现）。也可写成单测：
`run_backtest(... friction=BrokerConfig(enabled=True, slippage_bps=0, decay_bps=10))` 后断言
`len([t for t in res.trades if t['reason']=='filled'])==1` 且 `res.friction_costs['decay']`
≈ 单日值（而非 ×n_days）。
**建议改法**：`match()` 每次进入先 `self.last_day_turnover = 0.0`；且 `rebalance_to_weights`
改为累计两次 `match` 的 turnover（或统一为单次 match：把卖单与买单合并成一个列表交给 `match`，
它本身就是「先卖后买」，`broker.py:380-394` 已保证顺序）。

### B5-02【P1·Bug】策略回测缓存键漏 `use_legacy_engine` → 返回**另一套引擎**的结果

**位置**：`backend/app/api/v1/backtest.py:243-254`（`_strategy_cache_key`）
**现象**：函数注释自称「Task 4：策略回测全参数入键（此前漏 walk_forward/wf_folds）」，但键串里
`f"strategy_{...}_wf{walk_forward}_{wf_folds}{opt_key}_{symbols}"` **不含 `use_legacy_engine`**，
而 `use_legacy_engine` 恰恰决定「走 broker 全套闸门」还是「无闸门旧引擎」（`backtest.py:375`）。
TTL=600s 内两个请求互取缓存，`from_cache=true` 时用户拿到的是**另一套引擎口径**的净值与 KPI。
**验证命令与输出**：
```
$ .venv/Scripts/python.exe -c "...StrategyBacktestRequest / _strategy_cache_key..."
legacy=False key: aqp:backtest:strategy_ma_cross_2025-08-08_2025-09-30_1000000_0.0003_5.0_5_20_3.0_wfFalse_3_600519
legacy=True  key: aqp:backtest:strategy_ma_cross_2025-08-08_2025-09-30_1000000_0.0003_5.0_5_20_3.0_wfFalse_3_600519
COLLISION: True
```
**可达性（诚实标注）**：前端 `frontend/src/api/strategyBacktest.ts` 的请求类型**从不下发**该字段
（与既有报告 `docs/audit/AQP_前后端逻辑审查_20260912.md` 的 F-07 一致），所以当前只能由直接调 API 触发。
但该参数在 OpenAPI schema 里是公开字段（`use_legacy_engine`），任何脚本/第三方客户端都能命中。
**最小验证**：对同一 `StrategyBacktestRequest` 分别用 `use_legacy_engine=False/True` 调 `_strategy_cache_key`
断言不等；或加一条 `test_backtest_cache_key.py` 的对称用例（该文件已有 4 条用例，恰好漏了这一条）。
**改法**：键串追加 `_legacy{req.use_legacy_engine}`。

### B5-03【P1·Bug】模拟盘**无持仓校验**：裸卖凭空生成现金，账户/净值/夏普全部失真

**位置**：`backend/app/trading/paper.py:118-147`（`place_order` 只校验 kill_switch / 禁买池 / 金额>0，
不校验持仓）、`paper.py:158-248`（`run_fills` 无条件撮合卖出）、`paper.py:317-397`（`account_summary` 对
超卖只做 `positions.pop`，不报错也不对冲）。上游 `app/api/v1/desk.py:175-190` 的 `OrderRequest.side`
允许 `"sell"`，无任何持仓校验。
**现象**：现金 = 初始资金 − Σ买入 + Σ卖出。只要发出「未持有标的的卖单」，`run_fills` 就会造出成交，
`account_summary` 把 `cash += amount - fee` 记进去 → 权益虚增，`max_drawdown/sharpe/annualized_return`
（`paper.py:359-381`）全部基于虚增权益。
**验证命令与输出**（内存 SQLite，不碰生产库）：
```
$ .venv/Scripts/python.exe -c "... 插入一条 side='sell'、symbol='000001.SZ'、qty=10000、price=10 的 PaperFill，调 account_summary ..."
无持仓裸卖 10000 股 -> positions= {}  cash=1099965.00 (初始 1e6)  equity=1099965.00
  => 卖空凭空生成现金 99965.00 元，且风控/持仓校验缺失
```
**最小验证**：上面的内存库片段（或 `POST /api/v1/desk/order {side:"sell"}` + `POST /desk/fills/run` 两次调用）。
**改法**：`place_order` 增加 `held = account_summary(session)["positions"].get(symbol, {}).get("qty", 0)`，
`sell` 且 `order_amount > held*decision_price` → 拒绝；`run_fills` 内对 sell 逐笔夹到可用持仓；
`account_summary` 对负持仓抛错/记 `oversold` 标志而不是静默 `pop`。

### B5-04【P1·Bug + 策略】退市机制在生产数据上**完全失效**，且披露文本与事实相反（幸存者偏差）

**位置**：`backend/app/data/universe.py:406-407`（delist 过滤）、`backend/app/data/ingest/tasks.py:60`
（`upsert_delist_dates` 零调用方）、`backend/app/api/v1/backtest.py:199-200` 与 `:680-681`（披露文本）。
**证据**：见 §1.8（`delist notnull=0/5552`；`list_date null=5430/5552`；宇宙 1133 只全部存活到 2026-09-04；
2026-04~09 每月宇宙规模恒为 1133；「最后真实 bar 早于 2026-01-01」的标的数 = 0）。
**最小验证**：
```
$ .venv/Scripts/python.exe -c "sqlite3 只读: select count(*) from instrument where delist_date is not null"
delist notnull : (0,)
$ rg -n "upsert_delist_dates" backend/app backend/tests backend/scripts
backend/app/data/ingest/tasks.py:60:async def upsert_delist_dates(...)      # 仅定义，无调用方
```
**改法**：把 `upsert_delist_dates` 接进 `orchestrator` 的月度步骤 + `datacenter` 手动入口；
`universe.py` 去掉 `list_date is null` 即放行的兜底（改为 NULL 时用 daily_bar 首根 bar 回填）；
披露文本改为如实陈述「本股票池仅含本地已下载的在市证券，不含期间退市标的，存在幸存者偏差」。

### B5-05【P1·Bug】`run_group_backtest` 的 `monthly_monotonic_ratio` **方向反了**

**位置**：`backend/app/backtest/engine.py:511-517`
**现象**：`group_of()` 把高分放 **Q5**（`engine.py:470-471`，docstring `:445` 亦写明「Q1 最低 .. Qg 最高」），
但单调性判据是 `all(rets[i] > rets[i+1])`，即要求 **Q1 > Q2 > … > Qg** —— 恰是"因子反向"。
结果：因子越单调，该指标越接近 0。附带 `engine.py:512-513` 的第一个列表推导算完即被 `:514` 覆盖，是死代码。
**验证命令与输出**（10 只标的、日收益严格随 pred_score 递增）：
```
      date       Q1       Q2      Q3       Q4       Q5  long_short
2024-01-22 0.999715 1.037245 1.08966 1.143358 1.216544    0.216829
Q1 final=0.9997 Q5 final=1.2165 (Q5 应 > Q1)
monthly_monotonic_ratio = 0.0        # 完美单调因子被判定为 0% 单调
```
**为何没被测出**：`tests/test_p2_backtest.py:125` 只有 `assert res["monthly_monotonic_ratio"] >= 0.0`
—— 值域 [0,1] 的恒真断言。**这是未覆盖的真缺陷**。
**消费方**：`backend/scripts/p2_experiment.py:86` 打印 `月度单调比例`，该数字被写进 P2 实验报告。
**改法**：`all(rets[i] < rets[i+1] for i in ...)`；并删除 `:512-513` 的死推导；
把测试改为「构造单调因子 → 断言 == 1.0，构造反向因子 → 断言 == 0.0」。

### B5-06【P1·Bug（影响面 P0）】`grid_search` 在守卫之前物化笛卡尔积 → 单请求 OOM 打死单 worker 进程

**位置**：`backend/app/backtest/param_search.py:80-85`
```python
combos = list(itertools.product(*(param_grid[k] for k in keys)))   # 先全量物化
...
if len(combos) > max_trials:                                       # 之后才守卫
    raise ValueError(...)
```
**触发条件**：`POST /backtest/strategy-run`（`require_role("researcher")`）带
`optimize_params`。模型约束只有 `max_length=6`（**键数**≤6，`backtest.py:291-293`），
**每个键的候选列表长度完全无上限**。6 个键 × 100 个候选 = 10^12 个 tuple → `list()` 直接吃光内存；
部署是 `Dockerfile --workers 1` 单进程 → 整站不可用。
**验证命令与输出**（用小规模证明"物化先于守卫"）：
```
$ .venv/Scripts/python.exe -c "... grid_search(ev, {'a':range(150),'b':range(150),'c':range(150)}) ..."
ValueError: 网格组合数 3375000 超过上限 500，请缩小参数空间
  组合数 150^3 = 3375000  构建+报错耗时 1.47s  峰值内存 243.4 MB
结论：max_trials 守卫在 list(itertools.product(...)) 之后才生效
```
**最小验证**：上面的片段（3 个键各 150 个候选就已 243MB / 1.47s，且**报错前**内存已经吃满）。
**改法**：用 `math.prod(len(param_grid[k]) for k in keys)` 先算组合数并守卫，
或用生成器/`itertools.islice` 边算边停；同时在 Pydantic 层给每个候选列表加 `max_length`（如 ≤50）。

### B5-07【P2·一致性/策略】walk-forward 模式下最终 KPI 是**样本内数字**

**位置**：`backend/app/api/v1/backtest.py:550`（取最后一折 best_params）+ `:571`（用它在**全区间**回测）
**现象**：`optimization` 块如实报告了 `mean_oos_sharpe` 与 `overfit_ratio`，但响应顶层的
`kpi.{sharpe,annual_strategy,max_drawdown}` 与 `nav_curve` 是「用最后一折（IS=前 3/4 全历史）选出的参数」
在**整个区间**上跑出来的。前端 `pages/Backtest/index.tsx:98-110` 把 KPI 卡片放在 WF 表格上方且无标注。
**最小验证**：读 `backtest.py:540-571`，或发一个 `walk_forward=true, wf_folds=3` 的请求，
比较 `kpi.sharpe` 与 `optimization.mean_oos_sharpe`（前者系统性高于后者）。
**改法**：`walk_forward=true` 时把 `kpi` 改为折外汇总（`mean_oos_sharpe` + 折外净值拼接），
或至少在 `kpi` 旁输出 `basis:"in_sample_last_fold"` 字段供前端标注。

### B5-08【P2·一致性】换手率口径是**双边**，`annual_turnover` 与衰减成本被放大 2×

**位置**：`backend/app/backtest/broker.py:380-397`（`turnover += t.amount` 买卖各累加一次）
→ `engine.py:403`（写进 nav_df）→ `domain/metrics.py:109-116`（`annual_turnover = mean × 252`）
**现象**：一次「全仓换标的」的完整换手，真实单边换手 ≈ 0.47，上报值 0.94。实测：
```
      date    cash     equity  turnover          trades
2024-01-03  5867.3  99867.3  0.940268   A.SH sell 95000 + B.SH buy 94000
...                                     annual_turnover = 203.8   （真实单边 ≈ 102）
```
**影响**：`annual_turnover` 指标 2× 误导；`apply_decay_cost(..., decay_bps)` 也按双边计费（×2）；
前端与报告里的"年化换手"数字全部翻倍。
**最小验证**：上面的交替信号合成回测；或断言「全仓换标的当日 `nav_df.turnover <= 1.05`」（当前为 ~0.94×2）。
**改法**：`match` 内 `turnover = (buy_amount + sell_amount) / 2`，或只累加单边；文档与字段名同步改为 `one_side_turnover`。

### B5-09【P2·Bug】模拟盘冲击成本**二次计费**

**位置**：`backend/app/trading/paper.py:220-237`（`exec_px = open*(1±i)` 已把冲击打进成交价）
vs `paper.py:298` 与 `:325`（`fee = f.fee + f.amount * f.impact_bps/1e4` 再收一次）
**现象**：买入 100 股 @10.01（含 10bp 冲击，`amount=1001`）时，现金被扣
`1001 + (5 + 1001×0.001) = 1007.001`，其中 `amount` 已包含 1.00 元冲击，又额外扣 1.001 元。
**验证命令与输出**（内存库）：
```
买入 100 股 @10.01(含10bp冲击) 佣金5元
  应为现金 998994.00
  paper 给出 998993.00   -> 多扣 1.00 元（= amount*impact_bps 二次计费）
  equity=1125691.00  total_fees=5.00 total_impact_cost=1.00
```
**最小验证**：上面片段；或断言 `account_summary(...)["total_impact_cost"]` 与
`Σ amount×bps/1e4` 不应同时出现在现金扣减里。
**改法**：`exec_px` 不含冲击、冲击只走 `fee`（或反之），二者取一；`impact_bps` 仅作为披露字段。

### B5-10【P2·一致性】ETF 卖出误收印花税；平台存在**4 套费用实现 + 1 个无人调用的规则模块**

**位置**：`backend/app/backtest/broker.py:171-175`（无 instrument_type 概念，卖出恒收 0.05%）
vs `ma_cross.py:180-181`、`paper.py:229-230`、`domain/portfolio.py:303`（均豁免 ETF）
vs `domain/a_share_rules.py:100-104`（`stamp_duty_rate` 亦豁免 ETF，但**零调用方**）。
**验证命令**：`rg -n "stamp_duty_rate|commission_rate_default|is_t_plus_one|settlement_days" backend/app`
→ 仅命中 `a_share_rules.py` 自身 4 行定义。即该模块是纯死代码，而费率被硬编码在 4 处。
**影响**：任何经 `engine.run_backtest` 回测 ETF 的路径会多扣 5bp/次卖出；`domain/limit.py` 的
"唯一规则来源"纪律（`universe.py:6` 注释明示）在费用这一块没有贯彻。
**最小验证**：`Broker(init_cash=1e5).sell(d,'510300.SH',100,row)` 断言 `cost` 不含印花税（当前会含）。
**改法**：`broker._sell_cost(amount, symbol)` 内按 `_is_etf(symbol)` 豁免，并让三处实现统一 import
`a_share_rules.stamp_duty_rate` / `commission_rate_default`。

### B5-11【P2·策略】策略回测路径的涨跌停是**幅度近似**，且丢掉 ST / 新股窗口 / raw 域取整

**位置**：`backend/app/api/v1/backtest.py:335-338`
```python
pct = {"bse": 0.30, "chinext_star": 0.20}.get(board, 0.10)
df.with_columns([(pl.col("close").shift(1) * (1+pct)).round(2).alias("limit_up"), ...])
```
**问题**：① ST 的 ±5% 未处理（`universe.py:112` 有，这里没有）；② `is_new_issue` 的"不设涨跌幅"未处理；
③ `.round(2)` 发生在 **qfq 域**，而交易所是在 **raw 域**四舍五入到 0.01，factor≠1 时两侧错位
（误差可达 0.005×factor/price ≈ 数 bp，而闸门容差只有 1bp，`broker.py:312,270`）；
④ 该路径喂 **qfq** 价而 Top-K 喂 **hfq** 价，同一 Broker 两套价格域。
**最小验证**：对一个 ST 股（`is_st=true`）取一次真实 +5% 涨停日，构造 `_load_strategy_bars` 的 DataFrame，
断言 `limit_up` 等于 `prev_close×1.05`（当前为 ×1.10）。
**改法**：策略路径改为直接读 `universe_daily_bt` 的 `limit_up/limit_down`（与 Top-K 同源），
或调用 `domain/limit.py`（`universe.py:6` 声明的唯一规则来源）而不是自己拼。

### B5-12【P2·Bug/披露】基准降级被报告成「基准收益 0.0%」

**位置**：`backend/app/api/v1/backtest.py:525-528`（失败时造一行 `close=1.0` 的假基准）
+ `strategy_base.py:480-481`（无基准时把 `bench_n` 替换成全 1）
+ `strategy_base.py:296-298`（`annual_benchmark = annual_return(bench_nav[~isnan])` → 1×n 的序列年化 = 0.0）
**验证命令与输出**：
```
$ .venv/Scripts/python.exe -c "... run_strategy(bars, MaCrossStrategy(short_ma=3,long_ma=5), benchmark=None) ..."
benchmark=None -> annual_benchmark = 0.0
naive 解读：与真实 0% 收益的基准不可区分；alpha/beta = nan nan
```
**影响**：用户在 KPI 卡看到「策略年化 X% / 基准年化 0.0%」，会把降级当成真实数据 ——
正是 P0 契约第 5 条禁止的"把合成数据冒充真实行情"，也正是 P0 背景块「降级路径是否产生看似正常实则
为空的数据」明确要求重点检查的那一类。
**最小验证**：上面片段；或让 `fetch_index_daily` 抛错后读响应里的 `kpi.annual_benchmark`。
**改法**：基准不可得时 `annual_benchmark = None` 并在响应里加
`benchmark_status:"unavailable"`（前端已有 `benchmark: null` 的渲染分支，`backtest.py:586`）。

### B5-13【P2·策略】旧引擎（`use_legacy_engine=true`）无任何 A 股闸门，且停牌按最后价成交

**位置**：`backend/app/backtest/ma_cross.py:112-117`（`opens[d] = ... else (last_c or nan)`）、
`ma_cross.py:155-212`（`execute_target` 内无 halted / limit / T+1 判断）。
**影响**：可在涨停价买入、跌停价卖出、**停牌日按最后收盘价成交**；结果系统性偏乐观。
`docs/audit/ab/task7-AB.md` 已量化过该偏乐观（本批不重复该结论），但**代码路径仍在**。
**可达性**：默认关（`backtest.py:300`）且前端从不下发 → 当前只能由直接调 API 触发。
**最小验证**：`run_ma_cross` 传入「执行日 open == limit_up」的 bars，断言行情的买入仍会成交
（对照 `test_ma_cross_gates.py::test_framework_macross_respects_limit_up` 会拒绝）。
**改法**：直接删除该分支（见 §1.10 的 4 步确认清单），或至少把它标注为
`reason="legacy_engine_no_gates"` 并在响应 `liquidity.note` 里强制显示（`backtest.py:594-600` 已有该 note，
但只在 `use_legacy_engine=true` 时出现——默认路径不会看到）。

### B5-14【P2·Bug】`list_date` 缺失导致「上市前占位行」与「新股不设涨跌幅」失效

**位置**：`backend/app/data/universe.py:402-403`（NULL 即放行）、`:425-430`（`days_since_list` 为 NULL → `is_new_issue=False`）
**证据**：`001306.SZ` 首根真实 bar 2023-11-16，但在宇宙里有 **452 行 2022-01-04 起的占位行**（close/volume 全 null）；
宇宙内 1133 只标的中 **1012 只 `list_date` 为 NULL**。
**影响**：① 新股前 5 日的无涨跌幅窗口失效 → 套上假限价，真实大涨日误拒买入、真实暴跌日**误拒卖出**；
② `universe_scope.n_symbols`（`backtest.py:198`）在任何窗口都报 1133，夸大截面宽度（2022 年实际在市远少于此）。
**最小验证**：`pl.read_parquet(universe_daily_bt).filter(symbol=='001306.SZ')` 看首行日期与 `close` 是否 null。
**改法**：`list_date` 缺失时用该 symbol 在 `daily_bar`/`daily_bar_hfq` 的首根 bar 日期回填（一行 Polars 表达式），
再走原有 `days_since_list` 逻辑。

### B5-15 ~ B5-20【P3】边界与一致性问题（逐条一行）

| 编号 | 位置 | 现象 | 验证 |
|---|---|---|---|
| B5-15 | `broker.py:270-276` | T+1 部分卖出语义错：有可卖持仓 + 当日锁仓时，超额委托被**整单拒绝**而非部分成交（详见 §1.2，引擎不可达） | 见 §1.2 的 `python -c` |
| B5-16 | `broker.py:381,389` | 不在 `uni_d` 的订单被 `continue` 跳过，**不产生任何 Trade 记录**，与 `broker.py:17` "被拒绝的订单以 qty=0 的 Trade 记录返回…保证可观测性"的承诺矛盾；API 的 `rejected_trades` 因此少报 | `b.match(d,[sell GONE.SH],uni_without_it)` → `len(trades)==0` |
| B5-17 | `strategy_base.py:365-367` | 该路径构造了 `BrokerConfig(decay_bps=10, enabled=True)` 但**从不调用 `apply_decay_cost`** → `decay_bps` 在策略回测里是死配置 | `rg "apply_decay_cost" app/` 仅命中 `broker.py` 定义与 `engine.py:371` |
| B5-18 | `broker.py:171-175`、`ma_cross.py:178-181`、`paper.py:228-230` | **过户费完全缺失**（现行 0.001% 双边，沪深均收）；`rg "过户\|transfer_fee" app/` = 0 命中 | 代码检索 |
| B5-19 | `backtest.py:505` vs `:540-542` | `short_ma >= long_ma` 的参数校验（`:505`）只作用于请求级参数；寻优覆盖值（`overrides`）**绕过校验**，非法组合（如 short=30/long=10）会被当作有效试验参与选优 | 发 `optimize_params={"short_ma":[30],"long_ma":[10]}`，观察搜索照常产出 |
| B5-20 | `strategy_base.py:406-412`、`ma_cross.py:183,209-210` | 逐笔 `pnl` 只减**卖出**费用：成本价用 `amount` 累计（不含买入佣金），因此 buy 侧佣金/冲击未进 pnl → 前端 `win_rate / avg_pnl_ratio` 略偏乐观 | `strategy_base.py:410` 的 `avg_cost` 分子无 `t.cost` |
| B5-21 | `engine.py:73-74` | `sig_d.empty` 时返回 `set(current_holdings)`，随后仍被 `_compute_target_weights` **等权再平衡**（`:361-363`）→ 无信号日的调仓不再受信号约束；当持仓权重因价格漂移超过 `WEIGHT_TOLERANCE=10%`（`:90`）时会在**无信号日产生交易**（docstring 只说"保留现有持仓，避免被动清仓"，未声明会再平衡） | 造 prediction 空洞区间（如只在偶数日给信号）并让价格单边漂移，观察空洞日仍出现 trades |

---

## 3. 目标 B —— 死代码 / 未接线 / 永不成立分支

| 位置 | 分类 | 依据 | 建议动作 | 置信度 |
|---|---|---|---|---|
| `ma_cross.py:104 day_idx` | 真死（无害残留） | 定义后从未读取（`rg day_idx` 仅此一行） | 删除 | 确定 |
| `domain/a_share_rules.py:90-111` 的 `is_t_plus_one`/`settlement_days`/`stamp_duty_rate`/`commission_rate_default` | 真死（4 个函数零调用方） | `rg` 全仓仅命中定义处 | 保留但**接线**（broker/paper/portfolio 改为调用它），否则删除 | 确定 |
| `data/ingest/tasks.py:60 upsert_delist_dates` | **未接线**（有价值但无人调） | 零调用方；DB 中 `delist_date` 全 NULL | 接进 orchestrator 月度步骤 + datacenter 手动入口 | 确定 |
| `universe.py:406-407` 的 delist 过滤 | **永不成立的分支**（当前数据下） | `delist_date` 全 NULL → 条件恒真 | 随上一条一起修 | 确定 |
| `engine.py:512-513` 第一个 `rets` 列表推导 | 真死（算完即被 `:514` 覆盖） | 直接读代码 | 删除 | 确定 |
| `engine.py:377-388` + `broker.liquidate` 退市强平路径 | **未接线**（数据侧前置条件缺失导致不可达） | 宇宙中无标的会离场（§1.8） | 修数据后自动生效；否则在文档中标注"当前空转" | 确定 |
| `backtest.py:500` `from ...backtest.ma_cross import run_ma_cross  # noqa: F401` | 假阳性（预热 import） | 注释已说明用途 | 保留 | 确定 |
| `ma_cross.run_ma_cross` 整条旧引擎 | **半死**：仅 `use_legacy_engine=true` 可达，零测试、零脚本调用 | `rg run_ma_cross` 仅 API legacy 分支；`rg use_legacy_engine backend/tests` 仅注释 | 见 §1.10 的 4 步确认清单后删除 | 确定 |
| `donchian` / `rsi_reversion` 策略类 + `STRATEGIES` 注册表 | **未接线（UI 不可达）** | `frontend/src/api/strategyBacktest.ts` 请求类型无 `strategy_type`（与既有报告 F-07 一致） | 前端补字段接线，或明确标注实验性 | 确定（非本批新发现，交叉引用 F-07） |
| `broker.py` 的 `factor`/`_lot_size` 机制在 `/strategy-run` 上 | **局部永不生效** | `_load_strategy_bars` 的 `keep` 白名单不含 `factor`（`backtest.py:339-341`） | 策略路径改用 hfq 或显式声明"不适用" | 确定 |

> 未发现「永远不会成立」的 except 子句或恒真/恒假类型分支（除上述 delist 过滤外）。
> `evening_routine.py` 的 `except BaseException` + `is_fatal_base_exception` 放行语义正确，不是死分支。

---

## 4. 目标 C —— 策略合理性：这套回测假设在真实 A 股能不能成立、收益被高估多少

### C1【默认无摩擦 + 换手口径 2×】高换手 Top-K 策略年化被高估约 **30 个百分点**

- **当前问题**（1 句）：`/backtest/run` 的 `enable_friction` 默认 false（`backtest.py:36`），
  默认口径只有佣金+印花税，**零滑点零冲击**；同时换手口径是双边（B5-08），使"看起来该扣的摩擦"减半。
- **具体改法**：把 `enable_friction` 默认改为 `True`（或至少在前端默认勾选），
  默认 `slippage_bps=5`、`impact_model="sqrt"`、`max_participation=0.05`；
  `match` 内换手改单边（B5-08）；修掉 B5-01 后再给 `decay_bps` 定值。
- **预期收益（量级）**：实测（合成 2000 只 × 250 日、Top-10 日频、日换手 ~0.95）：
  ```
  raw      nav=0.8055 年化=-19.03% 年化换手=236
  friction nav=0.5047 年化=-48.69% 年化换手=236
  差异     29.66 个百分点/年
  friction_costs: {'slippage': 173670, 'impact': 0, 'decay': 173972}
  ```
  即**默认口径把一个 −49%/年的策略展示成 −19%/年**（29.7pp 的乐观偏差）。注意这 29.7pp 里
  含 B5-01 的虚增部分，所以正确修法后真实偏差仍在 10–20pp 量级（换手 100 倍以上时）。
- **引入风险**：默认变保守后，历史对比数字全部变化（缓存键已含 `enable_friction`，
  不会有旧结果污染）；用户可能误以为"策略变差了"——需要在前端显式标注口径。
- **验证方式**：同一时段的 `run_backtest_comparison(raw, friction)` 差值；
  以及用真实 `universe_daily_bt` + 真实 predictions 跑 `scripts/ab_backtest.py --enable-friction`
  与不带该 flag 的两次结果对比（该脚本已支持）。

### C2【幸存者偏差】收益高估量级 **1–4 个百分点/年**（小市值更高），且当前披露为不实

- **当前问题**：宇宙 = 本地已下载的 1133 只**当前在市**证券，2022–2026 期间退市的股票一只都不在里面
  （`delist_date` 全 NULL、无标的离场，§1.8）；而 API 却声称"含退市证券的历史 bar"。
- **具体改法**：① 接线 `upsert_delist_dates`；② 下载退市标的的历史 bar（akshare 退市名单 → 逐只拉取）
  并纳入 `universe_daily_bt`，`delist_date` 之后剔除；③ 退市前最后 20 日的强平折价
  `delist_haircut` 从 0.5 校准（老三板首日普遍跌幅更深，A 股近年退市整理期平均跌幅约 −60%~−80%）。
- **预期收益（量级）**：小市值/高换手策略年化高估 **1–4pp**（退市率约 0.5–1%/年 × 退市平均损失 50–80%
  × "持有退市股的概率"），大市值策略 <1pp。**该量级来自文献/规则推算，必须用 ③ 修完后重跑实测确认**，
  我不能声称知道本项目的真实数值。
- **引入风险**：退市股票的历史 bar 质量差（缺量额、缺复权因子），需要与 `_read_bt_bars` 的
  raw/hfq 合并逻辑做兼容；可能引入新的 NaN 路径。
- **验证方式**：修完后对比「含退市中/不含退市」两套宇宙的同一策略年化差；
  并统计 `universe_daily_bt` 里 `delist_date not null` 的标的数与每月的"离场"事件数（当前恒为 0）。

### C3【衰减成本模型】应按**当日真实订单金额**计，而不是按"上一交易日换手 × 权益"

- **当前问题**：`apply_decay_cost(broker.last_day_turnover, prev_equity)`（`engine.py:369-371`）
  用**昨日**换手率乘**今日**权益；配合 B5-01 的陈旧值导致"不动也扣钱"。
- **具体改法**：把 decay 挂到 `match()` 内，按当日实际成交金额计：
  `cost = Σ(成交金额) × decay_bps/1e4`（与滑点同基），删掉 `apply_decay_cost` 的启发式调用。
- **预期收益（量级）**：消除最坏情况下每 100 个不动日 ~9.5% 权益的虚扣（实测 21 日虚扣 1.9%、
  Sharpe 从 −3.55 崩到 −240）；使摩擦成本与实际成交严格成比例。
- **引入风险**：与历史 `p2_experiment` 的报告数字不可比（需重跑）；若 `decay_bps` 是"调出来的观感参数"，
  改按成交金额后需要重新标定。
- **验证方式**：单测——「只有 1 天成交、其余日子不动」的合成回测，断言 `friction_costs['decay']`
  等于该日成交额 × bps（当前为 ×21 天）；以及「零成交全程」断言 decay == 0。

### C4【样本外纪律】walk-forward 需要 embargo，且**最终 KPI 必须用折外数**

- **当前问题**：IS/OOS 窗口相邻无 gap（`backtest.py:435-439`）；最终 KPI 用最后一折参数跑全区间（B5-07）。
- **具体改法**：① `oos_s = all_dates[(i+1)*seg + embargo]`，`embargo = max(long_ma, horizon)`
  （ma_cross 用 `long_ma`；若将来接 ML 信号用标签 horizon）；② `walk_forward=true` 时
  `kpi` 改由各折 OOS 段的净值拼接计算，并输出 `kpi_basis="walk_forward_oos"`。
- **预期收益（量级）**：把"看起来有效的参数"从 `mean_is_sharpe` 拉到 `mean_oos_sharpe`。
  本项目已有 `overfit_ratio>1.5` 的告警阈值（`backtest.py:469-472`），说明 IS/OOS 差距通常是显著的
  （经验上 OOS Sharpe 常为 IS 的 30–60%）——但**具体数值需要真实数据跑一次才能给**。
- **引入风险**：embargo 会缩短可用于寻优的样本；折数 3 + embargo 20 日时每折损失约 20 个 OOS 观测。
- **验证方式**：对同一策略跑 `walk_forward=true` 与 `false`，比较 `kpi.sharpe` 与 `mean_oos_sharpe`；
  并用 `tests/test_optuna_walkforward.py` 的既有 fixture 断言 OOS 窗口起点 ≥ IS 终点 + embargo。

### C5【时点正确性】ST / 新股 / 涨跌停必须 point-in-time，不能用当前快照

- **当前问题**：`is_st` 用当前快照套全历史（`universe.py:112`）；`list_date` 97.8% 为 NULL 使新股窗口失效（B5-14）。
- **具体改法**：① `list_date` 用首根 bar 回填；② ST 状态改为从 `instrument` 的 ST 变更历史或
  从"证券简称含 ST/＊ST"的日线字段重建（akshare 的历史简称列表）；无法获得时，
  在 `universe_daily_bt` 加一列 `limit_pct_basis ∈ {"board","board_st_snapshot"}` 并在响应披露。
- **预期收益（量级）**：ST 的涨跌幅是 5% vs 10%——影响面是 46 只当前 ST 股的**全部历史**（5.2 万行，占 4%）
  和历史上曾 ST 的标的。对"专挑低位反转/困境反转"的策略，闸门错配会同时产生纸面成交与误拒成交，
  单边偏差可达数个百分点/年；对大盘蓝筹策略 <0.5pp。
- **引入风险**：ST 历史数据源本身有成本；若只做部分覆盖，会出现"半时点"新口径。
- **验证方式**：抽 3 只 2023 年被 ST、2025 年摘帽的标的，对比 `limit_pct` 与当时真实涨跌停价；
  统计"Gate 触发率"在修前/修后的变化。

### C6【新股与流动性闸门】上市前占位行与 `max_participation` 的默认值

- **当前问题**：默认 `max_participation=0`（`backtest.py:44`）→ 参与率闸门关闭；
  但已有 sqrt 冲击模型下，大单在小市值股上会系统性低估冲击（`impact_sqrt_coef_bps=10` 偏小）。
- **具体改法**：默认 `max_participation=0.05`（机构常用 1%–5%），
  `impact_sqrt_coef_bps` 提到 20–30；对「检测到一字板/连续涨停」的标的额外加惩罚。
- **预期收益（量级）**：单边冲击成本从 ~3bp 升到 ~10bp 量级（参与率 5% 时 sqrt(0.05)=0.22 × 20bp ≈ 4.5bp），
  高换手策略年化再降 3–8pp；换来的是"大资金不可无限吃单"这一现实约束。
- **引入风险**：`max_participation` 打开后，买单被截断成 `liquidity_cap` 的比例上升，
  Top-K 实际建仓数可能少于 K（需要在响应里显示 `rejected_trades.liquidity_cap`，已有该统计）。
- **验证方式**：固定信号跑 `max_participation ∈ {0, 0.05, 0.01}` 三档，看年化与
  `rejected_trades.liquidity_cap` 的关系，确认排序不变（说明成本模型有效而非随机扰动）。

---

## 5. 已读测试的覆盖结论：**未覆盖的真缺陷**

已读：`tests/test_backtest.py`（闸门 8 例 + 引擎 5 例）、`test_backtest_settlement.py`（hfq 口径 4 例）、
`test_ma_cross_gates.py`（3 例）、`test_p2_backtest.py`（摩擦 5 例 + 分组 5 例）、
`test_backtest_cache_key.py`（4 例）、`test_qlib_vnpy_upgrade.py`（param_search 部分）。
另核实：`backend/tests/test_paper*.py` **不存在**（模拟盘零测试）。

**已覆盖且质量良好**：T+1 锁仓、涨停拒买、跌停拒卖、停牌拒双边、整手 100、最低佣金 5 元、
印花税仅卖出、`limit_up` 列生效、防未来信号、hfq 除权不产生虚假亏损、DSR 随试验数下降。

**明确未覆盖的真缺陷（本批新增，可直接拿去写红用例）**：
1. `test_backtest_cache_key.py` 缺 `use_legacy_engine` 不对称用例 → B5-02（键碰撞）。
2. `test_p2_backtest.py:125` 的 `>= 0.0` 恒真断言 → B5-05（单调比例反向）。
3. 无任何测试覆盖「非交易日不应扣衰减成本」→ B5-01。
4. 无任何测试覆盖「换手率是单边还是双边」→ B5-08。
5. `run_ma_cross` / `use_legacy_engine` **零测试** → B5-13 可静默回归（连"旧引擎无闸门"这一
   已写进文档的结论都没有用例锁住）。
6. 无测试覆盖「策略回测的 `annual_benchmark` 在基准不可得时不得为 0.0」→ B5-12。
7. 无测试覆盖 `paper.py` 的持仓校验与冲击计费口径 → B5-03 / B5-09（`paper.py` 无 `test_paper*.py`）。
8. 无测试覆盖「`optimize_params` 组合数上限在物化前生效」→ B5-06。
9. 无测试覆盖 `universe_daily_bt` 的 `list_date` / `delist_date` 语义（`test_backtest_settlement.py`
   只断言价格与 factor，未断言"上市前不得出现"与"退市后必须消失"）→ B5-04 / B5-14。

---

## 6. 我无法验证 / 需要补充的信息

1. **退市股历史 bar 是否可得**：我只确认了当前 `databar` 里没有退市标的，无法判断 akshare 退市名单 +
   历史行情能否补齐（需要跑一次 `fetch_delisted` + 逐只下载）。这是 C2 的前置条件。
2. **真实预测信号下的摩擦缺口**：C1 的 29.7pp 是合成高换手场景；真实 `predictions` 的日换手是多少、
   摩擦后年化降多少，需要跑 `scripts/ab_backtest.py` 两次（我没跑，避免与父审核员的全量测试抢 CPU）。
3. **`universe_daily_legacy` 目录**：`data/parquet/` 下存在 `universe_daily_legacy`，本批未展开；
   若它含有早期 raw 口径宇宙，可能对「manifest 与磁盘不一致」那条台账有帮助（属别的批次）。
4. **`is_st` 历史可获取性**：决定 C5 的可行改法。
5. **`POST /portfolio/backtest` 的具体口径**（`domain/portfolio.py`）我只做了交叉引用式核对
   （无 T+1/涨跌停闸门、有 ETF 印花税豁免），未逐行审 —— 该文件属 B7b/P2-D 批次，
   请该批次确认我表格里的那一行。

---

## 附：本报告全部验证命令清单

```powershell
# 均在 D:\Python_Project\Alpha Quant Platform\backend 下执行
.venv\Scripts\python.exe -c "from app.api.v1.backtest import StrategyBacktestRequest,_strategy_cache_key; ..."   # B5-02
.venv\Scripts\python.exe -c "...Broker; b.holdings=1000; b._locked_today=500; b.sell(qty=1200)..."               # B5-15 / §1.2
.venv\Scripts\python.exe -c "...run_backtest(21 天恒定价格, decay_bps=10)..."                                     # B5-01
.venv\Scripts\python.exe -c "...交替信号 -> 打印 nav_df.turnover 与 annual_turnover..."                           # B5-08
.venv\Scripts\python.exe -c "...run_group_backtest(收益严格随分数递增) -> monthly_monotonic_ratio..."             # B5-05
.venv\Scripts\python.exe -c "...grid_search({'a':range(150),'b':range(150),'c':range(150)}) + tracemalloc..."      # B5-06
.venv\Scripts\python.exe -c "...create_engine('sqlite://') + PaperFill + account_summary..."                        # B5-03 / B5-09
.venv\Scripts\python.exe -c "...run_strategy(..., benchmark=None) -> risk['annual_benchmark']..."                   # B5-12
.venv\Scripts\python.exe -c "...run_backtest_comparison(2000x250, top10) -> raw vs friction 年化差..."              # C1
.venv\Scripts\python.exe -c "...universe_daily_bt: delist/list_date/is_halted/每月规模/占位行..."                    # B5-04 / B5-14
.venv\Scripts\python.exe -c "...sqlite3 mode=ro: delist_date / is_st / list_date 计数..."                            # B5-04
rg -n "upsert_delist_dates" backend/app backend/tests backend/scripts                                             # B5-D
rg -n "stamp_duty_rate|commission_rate_default|is_t_plus_one|settlement_days" backend/app                          # B5-10 / B5-D
rg -n "过户|transfer_fee|TRANSFER" backend/app                                                                     # B5-18
rg -n "use_legacy_engine" frontend/src backend/app                                                                 # §1.10
rg -n "day_idx" backend/app backend/tests backend/scripts                                                          # B5-D1
```