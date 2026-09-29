# B9b · 前端 · 回测 / 组合 / 研究页 —— 详细审核报告（2026-09-18）

审核员：B9b 子审核员（前端 · 量化研究页）
纪律来源：`docs/audit-2026-09-18/AUDIT-BRIEF.md`、`docs/chatgpt-full-review-prompt.md` §1 / §3-B9b
基线（父审核员已跑，未重复）：`npx tsc --noEmit --noUnusedLocals --noUnusedParameters` = 0 错误
本报告所有「确定」条目均已用命令或代码双向对读验证；推理类标注「疑似」并给出确认所需信息。
未修改任何源码；未启动 dev server；未跑全量 pytest。

---

## 0. 审核范围与逐文件结论行

| 文件 | 行数 | 结论 |
|---|---|---|
| `frontend/src/pages/Backtest/index.tsx` | 234 | **P1×2**：walk-forward 的 KPI 卡片是样本内成绩却无标注；顶栏硬编码「QFQ」而不看后端实际复权口径。另有长请求无取消 + 切 Tab 重挂载自动重跑（P2） |
| `frontend/src/pages/Backtest/parts.tsx` | 320 | **P2**：寻优表单只校验「是数字」，无候选值条数上限 → 可构造超大网格打到后端「先物化后守卫」的 OOM 路径 |
| `frontend/src/pages/Backtest/resultParts.tsx` | 287 | 未发现 P0–P2。`-mdd` 前缀与后端正值口径一致（已核对）；交易表 `price.toFixed` 依赖后端必返数值（后端确实恒返） |
| `frontend/src/pages/Backtest/TopKPanel.tsx` | 108 | **P1**：年化/回撤/胜率用 `fmtPct` 直接吃后端**小数**，全部小 100 倍且回撤符号为正；成本/口径披露字段（`liquidity`/`friction_costs`）未展示 |
| `frontend/src/pages/Backtest/SignalAnalysisPanel.tsx` | 108 | **P1**：`quantile_spread.long_short_nav` / `spread_annualized` 后端**不存在** → 分层多空图恒空、年化价差永不显示；后端已算的 `monotonic`/`ls_t_stat` 也丢弃 |
| `frontend/src/pages/Portfolio/index.tsx` | 422 | **P2×3**：搜索竞态 + 失败静默；`metrics.x*100` 使 `null` 变 `0` 显示假「+0.00%」；基准 NaN 时 beta/alpha 静默退化为 1.00/0.00 且基准线消失无提示；权重输入框标 `%` 但值是小数 |
| `frontend/src/pages/Report/index.tsx` | 129 | **P2**：历史期切换无请求序号 → 旧日报覆盖新日报；**P3**：`<li>` 里保留了 markdown 的 `- ` 前缀（已用落库日报验证每行都以 `- ` 开头） |
| `frontend/src/pages/Research/index.tsx` | 629 | **P2**：因子勾选 / 优化器 / CV 参数三处并发请求无序号守卫（ComputeQueue 只管并发数，不排序）。`y.rank_ic.toFixed` 等 `?.` 缺失点经后端对读**均为安全** |
| `frontend/src/pages/Research/parts.tsx` | 316 | 未发现 P0–P2。MDD 前缀与后端正值口径一致（已核对 `stress_windows`/`portfolio_stress_replay`） |
| `frontend/src/pages/FactorStudio/index.tsx` | 445 | **P2 疑似**：`top_expressions` 条目在后端可能缺 `icir/t_stat/mean_ic` 键（`st={}`），前端直接 `.toFixed` 会崩；轮询无在途守卫、启动窗口内「取消」静默无效（P3） |
| `frontend/src/pages/FactorStudio/FactorLab.tsx` | 177 | **P3**：因子库加载失败静默成「空库」；本地重复实现 `SectionCard` 影子组件 |
| `frontend/src/pages/FactorStudio/NlFactorCard.tsx` | 89 | 未发现 P0–P2（`evaluation` 全字段 `?.`/`??` 到位） |
| `frontend/src/pages/CapacityAttribution/index.tsx` | 301 | **P2**：容量滑块 400ms 防抖不足以避免旧响应覆盖；**P3**：交互/残差 Alpha 文案重复两遍、风格数据为空时图表空白无占位 |
| `frontend/src/api/backtest.ts` / `strategyBacktest.ts` / `portfolio.ts` / `research.ts` / `production.ts` / `types/p1.ts` / `types/portfolio.ts` | — | **死代码**：`StrategyLiquidity`/`liquidity`、`mean_is_sharpe`、`BacktestResultData` 7 个字段、`NlFactorResult` 之外的 `ERR.EXPR_INVALID` 全无消费方；`signal-analysis` 的 `quantile_spread` 类型与后端字段名不符（见 I-1） |

**明确未发现问题的项**（按纪律不沉默跳过）：
- 表格分页语义：本批 6 个页面**均无服务端分页**（`TradesPanel` 是 `slice(-8)` 客户端截断且如实标注「共 N 笔」；`/research/experiments` 后端 `LIMIT 30` 无前端翻页）→ **本批无「客户端/服务端分页混用」缺陷**。
- `useChart`（`frontend/src/utils/useChart.ts`）用回调 ref，条件渲染后挂载也能 init → `SignalAnalysisPanel` / `CapacityAttribution` / `FactorStudio` / `FactorLab` 的「attr && <div ref>」写法**不会**导致图表永不初始化（曾怀疑，已排除）。
- Report 的 `report.generated_at.replace('T',' ')`：查生产 kv（`app_state.daily_reports`，5 期）**每期都有** `date/generated_at/sections/markdown/tips` → 当前不会崩（仅当历史 schema 变更才崩）。
- Portfolio `max_drawdown` 展示 `+x%`（无负号）与后端**正值**口径一致（`domain/portfolio.py:90-91`）；`Research/parts.tsx` StressTable 的 `-mdd` 前缀同样正确。
- 后端 `monthly_monotonic_ratio` 方向反了（`backtest/engine.py:526`，B5 已报）→ **前端全仓无任何消费点**（`rg -n "monotonic" frontend/src` 仅命中两处注释/无关文案），故本批页面**不呈现**该指标，无 UI 层影响。

---

## 1. 目标 A — Bug（会导致错误结果 / 误导 / 崩溃）

### I-1【P1 · Bug/契约不一致】`/backtest/signal-analysis` 的分层多空图恒空，「年化价差」永不显示

- 位置：`frontend/src/pages/Backtest/SignalAnalysisPanel.tsx:39-53, 97-108`；类型 `frontend/src/api/backtest.ts:31-36`
- 后端真实响应（`backend/app/ml/signal_analysis.py:134-143` 由 `backend/app/api/v1/backtest.py:686` 原样透出）：

```python
return {"horizon":…, "n_quantiles":…, "quantile_mean_ret": {Q1..Qg},
        "ls_mean_daily":…, "ls_t_stat":…, "ls_annualized":…,
        "monotonic":…, "n_days":…}
```

- 前端读取的是 `quantile_spread.long_short_nav`（数组）与 `quantile_spread.spread_annualized` —— **两个键后端都不产生**（`long_short_nav` 全仓只存在于 `ml/gp_miner.py:286`，属 `/studio/alpha-eval` 路径）。
- 触发条件：打开「策略回测 → 信号分析 → 运行信号分析」。必然发生，与数据无关。
- 后果：① `navOption` 恒为 `null`，`<div ref={navRef} className="h-56">` 渲染成**一个没有内容的空框**，标题还写着「分层多空净值（5 分位 · H=20）」；② `年化价差` 分支恒 false；③ 后端辛苦算出的 `ls_annualized`/`ls_t_stat`/`monotonic`（分层单调性判据）**全部丢弃**，页面上只剩 IC 表。
- 验证命令：
  - `rg -n "ls_annualized|long_short_nav|spread_annualized" backend/app` → 只命中 `ml/gp_miner.py:218,286`（studio 路径），`signal_analysis.py` 无 `long_short_nav`
  - `sed -n '31,36p' frontend/src/api/backtest.ts` → `spread_annualized` / `long_short_nav`
- 修法：前端改读 `ls_annualized`（乘 100 展示）、新增展示 `ls_t_stat`/`monotonic`；若要真曲线，需后端把 `ls_daily` 累积成净值序列（当前只返标量）。

### I-2【P1 · Bug/单位错误】Top-K 页把「小数」当「百分数」格式化 —— 年化/胜率小 100 倍，最大回撤符号为正

- 位置：`frontend/src/pages/Backtest/TopKPanel.tsx:95-99`
- 代码：`{ label:'年化收益', value: fmtPct(m.annual_return) }`、`{ label:'最大回撤', value: fmtPct(m.max_drawdown) }`、`{ label:'胜率', value: fmtPct(m.win_rate) }`
- `frontend/src/utils/format.ts:10-14`：`fmtPct` 只做 `p.toFixed(2)+'%'`，**不乘 100**。
- 后端 `metrics` 来自 `domain/metrics.py:210-238 → all_metrics()`：`annual_return`（`:44-50`）是 `(last/first)**(252/n)-1` 的**小数**；`win_rate`（`:92-97`）是 `mean(r>0)` 的**小数**；`max_drawdown`（`:79-89`）是 `1-a/peak` 的**正**小数。
- 触发条件：Top-K 页跑任意一次回测。例：真实年化 18.4% → 页面显示「**+0.18%**」；最大回撤 27% → 显示「**+0.27%**」（既小 100 倍又变正号）。
- 对照（同批同项目正确写法）：`Backtest/resultParts.tsx:19-20` 的 `pct()` 与 `Portfolio/index.tsx:145-147` 都是 `*100` 后再格式化 → 说明这是 TopK 单点的单位错误，而非全局约定。
- 验证命令：`rg -n "fmtPct\(m\.|annual_return|win_rate" frontend/src/pages/Backtest/TopKPanel.tsx frontend/src/utils/format.ts`；`rg -n "def annual_return|def max_drawdown|\"win_rate\"" backend/app/domain/metrics.py`
- 修法：`fmtPct(m.annual_return * 100)` / `fmtPct(m.win_rate * 100)` / 回撤改为 `-${(m.max_drawdown*100).toFixed(2)}%`（或统一换成 `pct(v)` 包装）。

### I-3【P1 · Bug/误导性呈现】walk-forward 模式下 KPI 卡片是「样本内」成绩，却与折外表并列且无任何标注

- 位置：`frontend/src/pages/Backtest/index.tsx:105-119`（KPI 区 + walk-forward 折表）
- 后端事实（B 批次已确认，本批复用）：`backend/app/api/v1/backtest.py:547-550` 在 walk-forward 分支取 **最后一折的 best_params** 作为 `base_overrides`，`:571` 再用它跑**全区间**回测；`kpi` 与 `risk` 全部来自这次全区间（样本内）运行 → `:619-624`。
- 前端呈现：KPI 卡片标题是「策略表现概览 / 策略年化收益 / 夏普比率 / 最大回撤」，**没有任何**「样本内 / 用最后一折参数跑全区间」字样；只有下方折表内的小字 `note` 提到「最优参数不可直接采信」。
- 触发条件：勾选「参数寻优 + walk-forward 折外验证」后运行。用户读到的头条数字（如「策略年化收益 42%」）是样本内结果，而真正的折外成绩 `mean_oos_sharpe` 只出现在小字里（`index.tsx:115`）。
- 附带：`mean_is_sharpe`（`api/strategyBacktest.ts:98`）**从未渲染** → 用户无法对照 IS/OOS 均值，`overfit_ratio` 的判读失去参照。
- 验证命令：`rg -n "base_overrides|walk_forward|overfit_ratio" backend/app/api/v1/backtest.py`（看 `:550,:571`）；`rg -n "mean_is_sharpe|mean_oos_sharpe|策略表现概览" frontend/src`
- 修法：walk-forward 模式下把 KPI 卡换成 OOS 口径（`mean_oos_sharpe` + 折外年化），或至少在卡片上加「样本内（最后一折参数跑全区间）」角标与 `mean_is_sharpe` 对照。

### I-4【P2 · Bug/资源】前端对寻优候选值无条数上限 → 可触发后端「先物化后守卫」的 OOM 路径

- 位置：`frontend/src/pages/Backtest/parts.tsx:83-91`（`validateForm` 的寻优分支）、`:96-106`（`buildOptimizeParams`）
- 现状：只校验「至少一个参数填了候选」「候选都是数字」；`parseNumList`（`:30-35`）对长度**不设限**，三个输入框都是自由文本。
- 后端：`backend/app/backtest/param_search.py:80-85`

```python
combos = list(itertools.product(*(param_grid[k] for k in keys)))   # 先物化
if len(combos) > max_trials:                                       # 后守卫（500）
    raise ValueError(...)
```

- 触发条件：把 `短均线候选` 粘贴成 2000 个数字、`长均线候选` 2000 个 → 前端放行 → 请求体即达后端 → `itertools.product` 物化 **4,000,000** 个元组（≈数百 MB）后才被 500 上限拒绝；`optimize_params` 的 `max_length=6` 只限**键**数（`api/v1/backtest.py:291-293`），不限**值**长度。
- 影响：单进程部署（`--workers 1`）下一次请求即可吃满内存，且前端把超时放宽到 600s（`api/strategyBacktest.ts:128-130`）→ 用户最长等 10 分钟。
- 验证命令：`rg -n "itertools.product|max_trials" backend/app/backtest/param_search.py`；`rg -n "parseNumList|lists.every|raw.some" frontend/src/pages/Backtest/parts.tsx`
- 修法：前端在 `validateForm` 增加「单参数候选 ≤ 50 且笛卡尔积 ≤ 500」的即时校验（后端 `max_trials=500` 是公开口径），超限直接拦在 UI。

### I-5【P2 · Bug/一致性】Portfolio：`null × 100 === 0` 把缺失指标显示成假「+0.00%」；基准异常时 beta/alpha 静默退化

- 位置：`frontend/src/pages/Portfolio/index.tsx:145-147`（`fmtPct(metrics.total_return * 100)` 等 3 处）、`:164`（volatility）、`:418-419`（KPI 卡）
- 机制（已验证）：
  - pydantic v2 以 JSON 模式序列化响应时把 `NaN/Inf` 写成 `null`（实测见下方命令输出）→ 前端拿到的是 `null` 而不是 `NaN`；
  - 但调用方**先乘 100 再交给 `fmtPct`**，而 JS 里 `null * 100 === 0`（实测）→ `fmtPct` 的 `p == null` 守卫被绕过，输出「**+0.00%**」。
- 后端可产生 NaN/Inf 的位置（B 批次已确认）：`backend/app/domain/portfolio.py:365-366` `bm_nav = initial_cash / benchmark.iloc[0] * benchmark`，基准首值为 0 → 整条 `inf`；`_compute_metrics`（`:117-129`）只把 `psr/dsr/calmar` 做了 `None` 化，`total_return/cagr/max_drawdown/volatility` 是 `round(nan/inf, 6)` 直出。
- 同源第二个问题：`_compute_metrics:98-104` 在 `bm_a.std()==0`（基准整条 NaN/Inf 时必然走到）时**静默回退** `alpha=0.0, beta=1.0`；前端 `:166-167` 原样显示「贝塔系数 1.00 / 阿尔法 0.00」，同时 `:87` 的基准系列全为 `null` → ECharts 画出一条不存在的基准线（静默留白），页面**没有任何**降级提示。用户会得到「策略 alpha=0、beta=1、跑赢一条看不见的基准」这种看似正常的错误结论。
- 前端是否会崩？**不会**（我实测 NaN 不会以 `NaN` 字面量到达 JS：FastAPI 响应模型序列化阶段已转 `null`；若走 Starlette `JSONResponse` 原生渲染则会 `ValueError` 并被兜底成 50000，但本路由有 `response_model`，走的是前者）。所以此处**不需要** NaN 防御，需要的是 `null` 语义修正 + 降级提示。
- 验证命令（本批实跑输出）：

```
$ node -e "console.log(null*100, null*100===0, (null*100).toFixed(2))"
0 true 0.00
$ backend/.venv/Scripts/python.exe  (TestClient 实测，response_model=APIResponse[dict])
status 200
body {"code":0,"message":"ok","data":{"nav":[{"date":"2024-01-01","benchmark":null}]}}
$ python -c "from fastapi.responses import JSONResponse; JSONResponse({'nav': float('nan')})"
RAISED: ValueError Out of range float values are not JSON compliant
```

- 修法：`fmtPct(v == null ? null : v * 100)` 或让 `fmtPct` 接受小数口径；对「基准不可用」增加显式降级横幅，并让 alpha/beta 在后端回退时返回 `null`（`alpha: null`）而不是 1.0/0.0。

### I-6【P2 · Bug/竞态】Portfolio 资产搜索：旧响应可覆盖新响应，且失败被静默成「无匹配」

- 位置：`frontend/src/pages/Portfolio/index.tsx:213-223`
- 代码：`portfolioApi.search(query.trim()).then(setSearchResults).catch(() => setSearchResults([]))` —— 只有 300ms 防抖，**没有** AbortController/序号守卫：防抖窗口一过，请求即发；用户继续输入会让多个请求同时在飞，先发的若后返回就会用旧关键词的结果覆盖新结果（下拉列表内容与输入框不一致）。
- 另有卡死态：`setSearching(true)` 在**建定时器之前**执行，若 300ms 内清空输入，cleanup 只 `clearTimeout`，新一次 effect 走 `if (!query.trim()) { setSearchResults([]); return; }` → `searching` 永远停在 `true`（输入框右侧一直显示「…」）。`addAsset`（`:229-230`）正好会把 query 清空。
- `.catch(() => setSearchResults([]))`：后端 50000/超时时用户看到的是「搜不到」而不是「搜索失败」——与「空结果」不可区分。
- 验证命令：`rg -n "searching|setSearchResults|setTimeout" frontend/src/pages/Portfolio/index.tsx`（`:213-223,291`）
- 修法：`search` 传 `AbortSignal`（`api/client.ts:132-149` 已支持 `RequestOptions`，只是 `api/portfolio.ts:11-12` 没接），并在 effect cleanup 里 abort + 复位 `searching`。

### I-7【P2 · Bug/竞态】Report 切换历史期：旧日报可覆盖新日报

- 位置：`frontend/src/pages/Report/index.tsx:42-53`
- `load(date)` 无请求序号/abort，`useEffect` 依赖 `selected`；快速切换下拉（或点「重新生成」后 `setSelected(undefined)` 触发第二次 load，`:59-61`）会让两个请求并发，后返回的旧日期日报会覆盖新选择的日期，而「期数：」标签显示的是旧日报自己的 `date` → 用户看到的期数与所选不符（且 `loading` 已被先返回的请求置 false）。
- 验证命令：`rg -n "load|selected|setReport" frontend/src/pages/Report/index.tsx`
- 修法：`load` 内加 `const seq = useRef(0)` 守卫，或 `AbortController` + cleanup。

### I-8【P2 · Bug/竞态】Research 三处交互重算无序号守卫（ComputeQueue 只限并发、不保证顺序）

- 位置：`frontend/src/pages/Research/index.tsx:269-285`（`rerunFactorPanels`，由因子勾选/口径下拉/中性化开关触发）、`:298-305`（`rerunCv`）、`:307-320`（`runOptimizer`）
- `ComputeQueue`（`:75-116`）把并发压到 2，但队列本身不保证「后发的结果最后 set」：并发槽位里两个请求的耗时不同，先发的先返回即写入 `setIcirRows/setCorr/setQuantile`，随后被更早的旧请求覆盖。表现：因子 chips 已是新选中集合，热力图/IC 表却是旧集合（两者不同源）。
- 触发条件：连续点击两个因子 chips，或快速改 `n_splits/purge/embargo` 三个下拉。
- 另：`setErr` 是页面级单值（`:119`），一个区块的错误会覆盖另一区块的错误，且与区块级 `sectionStatus.*.error` 并存（两套错误模型）。
- 验证命令：`rg -n "enqueueCompute|rerunFactorPanels|rerunCv|runOptimizer" frontend/src/pages/Research/index.tsx`
- 修法：每个重算入口持有自增序号，`setState` 前比对；或在 `rerunFactorPanels` 内先 `abort` 上一次的 controller。

### I-9【P2 · Bug/竞态（疑似）】CapacityAttribution 容量滑块：400ms 防抖挡不住乱序返回

- 位置：`frontend/src/pages/CapacityAttribution/index.tsx:40-49`
- `loadCapacity` 无序号守卫；防抖只在「连续变更」时合并请求。用户「拖动→停 400ms（请求发出）→再拖→停（第二个请求发出）」时两个请求并发，旧响应后到即覆盖新值，而 `formula` 串里含本次参数 → 「aum_yi 与下方滑块数值/公式串不一致」。
- 置信度：疑似（需要第二个请求先返回才能观察到；代码层缺失守卫是确定的）。
- 验证命令：`rg -n "loadCapacity|setTimeout" frontend/src/pages/CapacityAttribution/index.tsx`
- 修法：与 I-6 相同（序号或 abort）。

### I-10【P2 · Bug/披露】回测类长请求既无取消通道，又会因切 Tab 重挂载而自动重跑

- 位置：`frontend/src/pages/Backtest/index.tsx:36-63`（`run` + 挂载自动回测）、`:237-239`（Tab 条件渲染）
- ① 无取消：`api/backtest.ts:40-46`、`api/strategyBacktest.ts:126-130`、`api/portfolio.ts:10-16` 都**不接受** `RequestOptions`（对比 `api/research.ts` 全部接受 `signal`）→ 卸载/切 Tab 后 120s（寻优 600s）的请求仍在后端跑，回调再 `setResult` 到已卸载组件（React 18 不警告，但请求白烧）。
- ② 自动重跑：`{tab === 'ma' && <MaCrossTab />}` 每次切回「趋势跟踪」都重新挂载 → `useEffect` 自动跑一次 `/backtest/strategy-run`（`:49-63`）。纯 Tab 往返 = 重复回测（含寻优时可能 10 分钟）。
- 验证命令：`rg -n "RequestOptions|signal" frontend/src/api/backtest.ts frontend/src/api/strategyBacktest.ts frontend/src/api/portfolio.ts`（无命中）；`rg -n "tab === 'ma'" frontend/src/pages/Backtest/index.tsx`
- 修法：给三个 api 加 `options?: RequestOptions`，`MaCrossTab` 用 `AbortController`；把首屏自动回测结果提升到 `Backtest` 层缓存，或在 Tab 上改为 keep-alive/记忆化，避免纯导航触发重算。

### I-11【P2 · 一致性/披露】Top-K 与策略回测的成本/口径披露被前端丢弃；Top-K 页文案与后端数据源不符

- 位置：`frontend/src/pages/Backtest/TopKPanel.tsx:56-118`（整页无任何成本/口径提示）
- 后端明明给了披露字段，前端**没有消费**：
  - `/backtest/run`：`api/v1/backtest.py:217-223` `liquidity.{impact_cost_included, participation_cap, note}`（未启用摩擦时 `note="未启用摩擦成本（enable_friction=false）"`）、`:214` `friction_costs`、`:204` `rejected_trades`。前端 `types/p1.ts:161-180` 里 `friction_costs`/`rejected_trades` 有类型但**无渲染**，`liquidity` 连类型都没有；默认 `enable_friction=false`（`:36`）且复选框默认不勾（`TopKPanel.tsx:19`）→ 页面头条的「年化收益/夏普」是**只含佣金+印花税、不含滑点/衰减/冲击**的口径，却没有一句说明。
  - `/backtest/strategy-run`：`api/strategyBacktest.ts:74-81,120` 定义了 `StrategyLiquidity`（`impact_cost_included`/`note`）却全仓无消费方（`rg` 只命中定义处）→ ma_cross 卡片「策略规则」只写了佣金与印花税（`parts.tsx:315`），**未提** 后端默认的 `slippage_bps=5.0`（`api/v1/backtest.py:285`）。
  - Top-K 页文案 `TopKPanel.tsx:61` 写「基于本地 predictions 模型信号的 Top-K 等权调仓回测（**universe_daily** + pred_daily）」，而后端已切到 **`universe_daily_bt`（hfq 口径）**（`api/v1/backtest.py:76-94,196-201`）→ 口径说明错误。**【已知条目的延伸】**：`universe_daily / daily_bar_qfq` 口径不一致已在台账中登记，此处是它在前端文案上的新证据。
- 同一契约的另一处硬编码：`Backtest/index.tsx:88` 无条件打印「`缓存/实时 · QFQ · T+1 开盘撮合`」，不看 `_load_strategy_bars` 里 `source` 的 qfq→raw 回退（`api/v1/backtest.py:312-316`，B 批次已确认为 F841 死变量）→ **前端替后端担保了它没担保的复权口径**。**【已知条目的延伸】**（后端从不披露 → 前端反而硬编码为 QFQ）。**✅ 已修（2026-09-21，第 10 轮，= B7b F6 / P0-4）**：后端新增恒存在的 `price_basis`/`benchmark_basis` 披露，`index.tsx` 改由 `basisLabel()` 读披露（缺字段显示"复权口径未知"），`parts.tsx:316` 规则说明改为"优先 QFQ…实际口径以结果页右上角标注为准"，`resultParts.tsx` 的基准 KPI 在构造基准时改标题为"基准年化收益（构造基准）"；静态断言防止回退（`backend/tests/test_backtest_price_basis_disclosure.py`）。
- 验证命令：`rg -n "liquidity|friction_costs|rejected_trades|mean_is_sharpe" frontend/src`（除类型定义外无消费）；`rg -n "universe_daily_bt|enable_friction" backend/app/api/v1/backtest.py`
- 修法：Top-K 页加一行口径条（`liquidity.note` + `friction_costs` 合计 + `rejected_trades` 归因）；`RejectedTrades` 是涨跌停/停牌等真实约束的可见证据，建议直接列出。

### I-12【P2 · 一致性/披露】Research 执行冲击模拟：后端已修正 VWAP 坏点并生成 `data_warnings`，前端一字未显示

- 位置：`frontend/src/pages/Research/index.tsx:635-643`（只展示均冲击/总成本/未成交）；类型 `frontend/src/api/research.ts:100-101`
- 后端 `backend/app/api/v1/research.py:493-504`：偏离 close ±50% 的 VWAP 视为坏点 → 「已按 close 修正」并写入 `data_warnings`（注释举例：2026-08-28 `000001.SZ`），`:509` 透出。
- 前端 `data_warnings` **无任何消费点**（`rg -n data_warnings frontend/src` 只命中类型声明）→ 用户看到的 `avg_impact_bps` 是「修过数据」后的结果，却看不到修了哪天。默认标的正是 `000001.SZ`（`:154`），即命中该已知坏点样本。
- 验证命令：`rg -n "data_warnings" frontend/src backend/app/api/v1/research.py`
- 修法：在冲击结论下方渲染 `impact.data_warnings?.map(...)`（后端文案已可直接展示）。

### I-13【P2 疑似 · 崩溃】FactorStudio「新生成的 Alpha 表达式」表：条目缺指标键时 `.toFixed` 抛错白屏

- 位置：`frontend/src/pages/FactorStudio/index.tsx:349-357`（`e.mean_ic.toFixed(4)` / `e.icir.toFixed(3)` / `e.t_stat.toFixed(2)`），类型声明 `api/production.ts:17-18` 把这些字段写成必填 `number`。
- 后端可产生缺键条目：`backend/app/ml/gp_miner.py:405-413` 的 `evaluate()` 在求值失败时返回 `(-1.0, {})`，`:434-435` `task.history = [{"expr": e, "fitness": …, **st} for s,e,st in scored[:8]]` —— `st={}` 时该条目**没有** `mean_ic/icir/t_stat/n_days` 键。同文件 `:432-433` 用 `best_stats.get("mean_ic", 0.0)` 兜底，说明作者已知 `st` 可为空。
- 触发条件：`scored[:8]` 里出现至少一个 `-1.0` 条目，即**有效表达式不足 8 个**（UI 允许 `种群规模=10` 最小值，`index.tsx:269`；字段选得窄、表达式无法求值时概率上升）。此时 React 渲染 `undefined.toFixed` → TypeError → 整页白屏。
- 置信度：**疑似**（代码路径确定；现有生产数据未复现）。我用落库快照核查过两个真实任务的 8 条 history，全部含完整指标键：

```
$ python(读 app_state.gp_tasks) →
task c597a3c24006 DONE gen 6 history 8   → 8 条 MISSING=[] 
task 08e1cbc9d910 CANCELLED gen 2 history 8 → 8 条 MISSING=[]
```

- 确认所需：一次「有效表达式 < 8」的挖掘任务（或直接单测 `_run_task` 注入返回 `None` 的 evaluator）。
- 验证/复现命令：`rg -n "return -1.0, \{\}|task.history = " backend/app/ml/gp_miner.py`；前端 `rg -n "toFixed" frontend/src/pages/FactorStudio/index.tsx`（`:353-356` 无 `?.`）
- 修法：`e.mean_ic?.toFixed(4) ?? '—'`（其余同），并把类型改成 `number | null`。

### I-14【P2 · 策略/一致性】Research 优化目标「Mean-Variance（均值-方差）」名不副实

- 位置：`frontend/src/pages/Research/index.tsx:559-569`（下拉提供 `mvo` 选项，文案「Mean-Variance（均值-方差）」）
- 后端（B 批次已确认）：`backend/app/domain/research.py:185-195` 调 `compute_weights_from_returns(...)` **不传 `expected_returns`** → `domain/optimizer.py:302-303` `mu = np.zeros(n)` → `mean_variance_weights` 的梯度里收益项恒 0，实际只做「协方差 + 换手上限（λ=8）」的方差步进。所谓 MVO 的收益预期从未参与。
- 后果：用户选择「均值-方差」以为在按预期收益优化，实际拿到的是与预期收益无关的一组权重（且结果依赖「给定的初始权重」`prev_w`，`research.py:402-404`）。同页没有 λ 滑块，但下拉标签本身即是误导。
- 验证命令：`rg -n "def optimize_portfolio|expected_returns" backend/app/domain/research.py backend/app/domain/optimizer.py`；`rg -n "Mean-Variance|value=\"mvo\"" frontend/src/pages/Research/index.tsx`
- 修法（择一）：后端传真实 `mu`（如近 N 日截面预期收益）；或前端把选项文案改为「最小方差（协方差口径，不含收益预期）」并在悬停说明中写清。

---

## 2. 目标 B — 死代码 / 未接线 / 静默降级

| 编号 | 严重度 | 位置 | 内容 |
|---|---|---|---|
| D-1 | P2 | `frontend/src/api/strategyBacktest.ts:74-81,120,98` | `StrategyLiquidity`（含 `impact_cost_included`/`note`）与 `mean_is_sharpe` 定义了但**无任何消费方**（`rg -n "liquidity\|mean_is_sharpe" frontend/src` 只命中定义）。前者是成本披露被丢弃（见 I-11），后者让 IS/OOS 无法对照（见 I-3） |
| D-2 | P3 | `frontend/src/types/p1.ts:158,167,170-177` | `BacktestResultData` 的 `rejected_trades` / `equity_curve` / `drawdown_curve` / `annual_returns` / `holdings` / `friction_costs` / `enable_friction` 与 `metrics.annual_vol/profit_loss_ratio/annual_turnover/total_return` **全部无渲染点**（TopK 页只用了 8 个字段）。后端每项都在算，前端只用零头 |
| D-3 | P3 | `frontend/src/types/api.ts:38` + `backend/app/api/v1/studio.py:344` | `ERR.EXPR_INVALID=53001` 在前端**从未被引用**，且后端非法表达式返回的是 `ERR_PARAMS(40000)`（`return fail(ERR_PARAMS, f"表达式校验失败: {e}")`）→ 常量不可达 + 前端无法区分「表达式非法」与普通参数错。对照 `FactorStudio/index.tsx:89` 用的是**裸数字** `51001/40400`（`ERR.DATA_EMPTY/NOT_FOUND` 已存在），同一文件内两套风格 |
| D-4 | P3 | `frontend/src/pages/FactorStudio/FactorLab.tsx:185-195` | 文件内自定义了一个 `SectionCard` 影子组件，与 `@/components/ui` 的 `SectionCard` 同名不同实现（后者在本批其他页面广泛使用）→ 两套实现并存 |
| D-5 | P3 | `frontend/src/pages/FactorStudio/FactorLab.tsx:32-35` | `load()` 的 `catch { /* 因子库加载失败不阻塞页面 */ }` 把失败吞成**空库**：与「库里确实没有因子」视觉完全一致（`factors && factors.length>0` 才渲染表格），用户无从判断后端是否不可用 |
| D-6 | P3 | `frontend/src/pages/Portfolio/index.tsx:217-220` | 资产搜索 `.catch(() => setSearchResults([]))` 同上：把 50000/超时吞成「无匹配」 |
| D-7 | P3 | `frontend/src/types/portfolio.ts:71` | `PortfolioBacktestResult.status` 无消费方，页面在 `index.tsx:449` 硬编码「状态：回测完成」 |

---

## 3. 目标 C — 策略 / 呈现合理性（本批只列有代码定位的）

1. **回测「年化」口径没有一处披露**：Top-K / 策略回测 / 组合回测三页都展示「年化收益」，但 ① `metrics.annual_return` 是几何年化（`domain/metrics.py:44-50`，按 252 日折算）② `ma_cross` 的 `annual_strategy` 走另一份实现（`backtest/ma_cross.py:284`）③ 三者成本口径不同（策略回测含佣金+印花税+5bps 滑点；Top-K 默认不含滑点）。建议在 KPI 卡下方统一加「口径」小字（几何年化 / 252 折算 / 已计成本明细）。预期收益：消除跨页数字不可比造成的误判；风险：无；验证：三页各跑一次，人工核对卡片文案。
2. **回撤与夏普均无定义**：`最大回撤` 直接展示（`Backtest/resultParts.tsx:28`、`Portfolio:419`），但 `sharpe` 的 rf 口径不一致 —— 组合回测用 `_RISK_FREE_ANNUAL=0.02`（`domain/portfolio.py:94`），`domain/metrics.py:63-76` 默认 `rf=0.0`（回测页即 0 无风险利率），`ma_cross.py` 又是另一处。同一仪表盘上两个「夏普」不可比。建议统一 rf 并在 tooltip 标注。验证：`rg -n "rf|risk_free" backend/app/domain/metrics.py backend/app/domain/portfolio.py backend/app/backtest/ma_cross.py`。
3. **折外成绩未被当作一等公民**（I-3）：walk-forward 的卖点是「防过拟合」，但 UI 把样本内 KPI 放在最显眼处、折外均值塞进小字，等于把结论倒过来讲。建议把 OOS 均值提为 KPI 主位、IS 为副位。
4. **未成交/被拒订单不可见**（I-11）：`rejected_trades` 是涨跌停/停牌闸门生效的唯一证据。缺失时用户会把「回测收益高」误读为「策略可执行」。建议在交易明细旁展示拒绝原因分布（一次改动即可，量化含义明确）。

---

## 4. 交叉核对清单（按 B9b 重点逐项回答）

| 重点项 | 结论 |
|---|---|
| 回测结果误导性呈现 | 命中 3 条：I-2（单位×100 错）、I-3（样本内当成绩）、I-11（成本/复权口径不披露） |
| 表格分页语义 | 本批**无**服务端分页，也无客户端/服务端混用（见 §0） |
| 长任务轮询 | 仅 FactorStudio 有轮询：终态停止 ✓（`index.tsx:82-85`）、卸载清理 ✓（`:148`）、失败退避 ✗（固定 2s，无退避）、在途重叠守卫 ✗（P3，见下） |
| 请求竞态 | 逐页核对：Portfolio 搜索 I-6 ✗、Report I-7 ✗、Research I-8 ✗、CapacityAttribution I-9 ✗、FactorStudio 轮询 P3 ✗、Backtest 主请求 ✓（按钮 disabled + 挂载用 cancelled 标记） |
| `data.x.y` 缺 `?.` | 唯一可崩溃点是 I-13（`top_expressions[].icir` 等）；`Portfolio.metrics.*` 因 I-5 的机制表现出错而非崩溃；`Research` 的 `y.rank_ic`/`e.metrics.valid_rank_ic`（后端 `research.py:627-629`、`:253-256` 恒有键值）与 `e.hyperparams.learning_rate`（后端可为缺键 `{}`，但前端 `?? '—'` 已兜底）、`f.metrics.icir`（`gp_miner.py:251-253` 有 0.0 兜底）均**不会崩**，非缺陷 |
| `NaN`/极值/`null` 格式化 | I-5（`null*100→0`）；`fmtPct/fmtNum` 自身对非有限值返回「—」✓ |
| 图表空数据渲染 | I-1（恒空图，最严重）、`CapacityAttribution:85-101`（`style.betas/contributions` 为空字典时 `h-44` 空白且无占位，触发：风格因子有效样本 < 30 日，`domain/attribution.py:99-100`）、`Research/parts.tsx:296`（`StressTable` 空数组显示「计算中…」而非「无数据」） |
| 契约不一致（参数名/字段名） | I-1（`long_short_nav`/`spread_annualized` 不存在）、I-11（`universe_daily` vs `universe_daily_bt`、硬编码 QFQ）、D-3（`ERR_EXPR_INVALID` 不可达） |
| 所有调用点检查 `code != 0` | ✅ 统一由响应拦截器抛 `ApiError`（`api/client.ts:95-128`）；但有 2 处**吞掉 ApiError**：D-5、D-6 |
| 免责声明覆盖 | 使用 `ResearchDisclaimer` 的：Backtest ✓（`index.tsx:240`）、Portfolio ✓（`index.tsx:273`）、AiPicks/Screener（B9a）。**未覆盖**：FactorStudio（全页无任何声明）、CapacityAttribution（无）、Research（仅页脚「仅用于研究参考」，未用统一组件）→ P3 一致性问题：这两个页面同样输出可直接用于投资决策的数字（因子 Alpha 表达式、超额收益/残差 Alpha） |
| 死代码 | D-1..D-7（另：`api/research.ts:101` 的 `data_warnings` 无消费，见 I-12） |

补充两条低价值但确定的展示缺陷（P3）：
- `CapacityAttribution/index.tsx:298-303`：一行里「交互 … · 残差 Alpha …」**重复输出两遍**（复制粘贴残留）。
- `Report/index.tsx:14-25,125-130`：`sections[].lines` 的字符串本身以 `- `（或 `  - `）开头（落库日报实测 5 期、全部 section 每行都以 `- ` 开头），前端又用 `<li list-disc>` 渲染 → 页面出现「• - xxx」双重项目符号，二级缩进丢失。修法：渲染前 `line.replace(/^\s*-\s*/, '')`。
- `FactorStudio/index.tsx:316-317`：`耗时 {status.elapsed_sec}s`，而重启后恢复的终态任务 `started_at=0` → 后端返回 `elapsed_sec: null`（`api/v1/studio.py:110-112`）→ 页面显示「耗时 s」。
- `FactorStudio/index.tsx:151-156`：`start()` 的 60s 窗口内 `status` 为 `null`，此时 UI 已切到「取消任务」按钮，但 `cancelRunning` 因 `status?.task_id` 为空**静默 return** → 表现为按钮点了没反应。

---

## 5. 附：本批执行过的验证命令（节选）

```powershell
# 契约核对：后端 signal-analysis 的分层字段
rg -n "ls_annualized|long_short_nav|spread_annualized" backend/app
# → 仅 ml/gp_miner.py:218,286（studio 路径）

# 单位核对：后端 metrics 是小数
rg -n "def annual_return|def max_drawdown|\"win_rate\"|def all_metrics" backend/app/domain/metrics.py

# JS null 语义 + FastAPI NaN→null
node -e "console.log(null*100, null*100===0, (null*100).toFixed(2))"     # 0 true 0.00
python -c "from fastapi.responses import JSONResponse; JSONResponse({'nav': float('nan')})"  # ValueError

# walk-forward 的 KPI 来源
rg -n "base_overrides|walk_forward|overfit_ratio" backend/app/api/v1/backtest.py   # :550,:571

# 寻优网格：先物化后守卫
rg -n "itertools.product|max_trials" backend/app/backtest/param_search.py          # :81,:84

# GP history 是否含缺键条目（真实落库数据，只读）
python(读 app_state.gp_tasks) → 两个任务各 8 条 history，MISSING=[] （未复现 I-13）

# 落库日报的 lines 前缀（只读）
python(读 app_state.daily_reports) → 每行以 "- " / "  - " 开头（Report 双重项目符号）
```

（未运行 dev server、未运行 pytest、未修改任何源码；上述 python 片段均为只读查询或合成输入。）