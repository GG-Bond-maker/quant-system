# AQP 硬编码 / 伪造数据 / 不合理固定值 专项排查（原始发现）

- 审查人：hardcode-hunter（GStack Investigator）
- 日期：2026-09-28
- 范围：`backend/app/**`（生产）、`frontend/src/**`（生产）
- 排除：`backend/tests/**`、`backend/.tmp_*`、`backend/reports/`、`frontend/dist*`、`frontend/node_modules/`、`docs/audit/**`、`**/__pycache__/**`
- 性质：**只读审查，未修改任何生产代码，未启动服务**
- 严重度口径：P0 伪造数据 / P1 不合理固定值 / P2 过度兜底吞异常 / P3 死代码·文档说谎

---

## 0. 前置事实核对（用于判断"真数据 vs 兜底"）

| 项 | 实测结论 | 证据 |
|---|---|---|
| 本地日线 | 2499 只标的 × 2022~2026 分区，**真实落盘** | `data/parquet/daily_bar/symbol=*/year=*.snappy.parquet` |
| 日线最新日 | **2026-09-28（当日）** | 000001.SZ 2026 分区末行 date=2026-09-28 |
| 日线字段 | `date/open/high/low/close/volume/amount/turnover/code/symbol/source` | 实测 schema |
| `volume` 单位 | **股**（`amount/volume ≈ close`，如 9.54e8/8.18e7 = 11.67 ≈ close 11.73） | 实测 |
| `turnover` | 存在且非空（2026 分区 null 率 **0%**），为小数口径（0.0042 = 0.42%） | 实测 |
| 预测分区 | 262 个，**最新 `date=20260917`** | `data/parquet/predictions/` |
| 结论 | 行情是当日真实数据；**AI 预测滞后 11 天**（但后端 `_freshness` 有 `is_stale` 披露，见 `api/v1/screener.py:54-95`） | — |

> ⚠️ 全局背景：行情(9-28) 与 AI 预测(9-17) 混搭展示。这不是"假数据"，但选股榜上的「AI 评分 + 当日涨跌幅」并不同源，已由 `freshness.is_stale` 披露，属**已知且已披露**状态。

---

## 🔴 P0 · 伪造数据 / 假指标（1 条链，3 处代码）

结论：**全仓生产路径无 `random` 造假**。`random.random/np.random/Math.random` 在生产代码里只有 4 处命中，且全部合法：
- `backtest/param_search.py:139` 参数随机搜索（种子化）
- `data/realtime.py:99`、`data/ingest/akshare_adapter.py:74` 限速 jitter
- `frontend/src/api/client.ts:211` 重试退避 jitter

`fake/mock/dummy/stub/placeholder/demo/synthetic` 在生产代码里只命中注释与"防造假"说明文字，**无 mock 数据泄漏生产路径**。

### P0-1　回测「基准年化收益」在无基准时是**构造出来的 0.0**，却以数字形式进 KPI

| 文件:行号 | 代码片段 |
|---|---|
| `backend/app/api/v1/backtest.py:745-748` | `except Exception as e:  # 基准失败时回测仍可运行（基准置常数）`<br>`bench_reason = "fetch_failed"`<br>`bench = pd.DataFrame({"date": [date.fromisoformat(req.start)], "close": **[1.0]**})` |
| `backend/app/api/v1/backtest.py:749-751` | `if bench_reason == "no_overlap":`<br>`bench = pd.DataFrame({"date": [...], "close": **[1.0]**})` |
| `backend/app/backtest/strategy_base.py:497-499` | `bench_n = nav_df["benchmark_nav"].to_numpy()`<br>`if not np.isfinite(bench_n).any():`<br>`    bench_n = np.ones_like(strat)  # 无基准时用常数（alpha/beta 记 NaN）` |

**为什么可疑**：三条退化路径（抓取失败 / 区间无重叠 / 引擎对齐后全 NaN）最终都把"基准"变成**常数 1.0**。`annual_return(全 1 序列) = (1/1)^(252/n) - 1 = 0.0`，于是响应里 `annual_benchmark` 是一个**被构造出来的 0.0**，前端 KPI 卡渲染为「基准年化收益 **0.0%**」。用户读到的是"基准这段时间没涨没跌"，而真实情况是"根本没有基准数据"。这符合团队定义中「回测结果里写死的收益率」。

**已缓解的部分**（诚实说明，不做过度指控）：
- `backtest.py:825-834` 返回 `benchmark_basis = {synthetic: True, basis: "synthetic_flat", reason, note}`，note 明写"`annual_benchmark` 的 0.0 是构造值，不可解读为真实基准收益"。
- `frontend/src/pages/Backtest/index.tsx:42` 会显示标签「基准不可得（构造基准）」。

**未缓解的部分**：
1. 数字本身仍是 0.0（不是 `null`），会参与展示与颜色判定（见 P1-1、P1-2）；
2. 基准净值曲线被策略净值顶替（见 P1-1）。

**建议**：
- `synthetic == True` 时，响应里把 `annual_benchmark` 显式置 `null`（并在 `kpi_reason` 里说明），前端自然显示 `—`；
- 或保留 0.0 但强制 `benchmark_basis.blocking = True`，UI 用灰底 + "—" 而非数字。

---

## 🟠 P1 · 不合理固定值 / 魔法数（10 条）

### P1-1　基准走势不可得时，迷你图直接画**策略净值**冒充基准

| 文件:行号 | 代码 |
|---|---|
| `frontend/src/pages/Backtest/resultParts.tsx:19-20` | `const spark = nav.slice(-60).map((p) => p.strategy);`<br>`const benchSpark = nav.slice(-60).map((p) => **p.benchmark ?? p.strategy**);` |

**为什么可疑**：当 `benchmark_nav` 为 NaN（P0-1 触发）时，后端返回的 curve 里 `benchmark` 是 `null`，前端立刻回落到 `p.strategy`。结果：「基准年化收益（构造基准）」这张卡下面挂着一条**策略自己的净值曲线**。用户看到"基准走势和策略一模一样"，是纯视觉伪造。

**建议**：`benchmarkSynthetic || p.benchmark == null` 时不渲染 `SparkArea`（显示"无基准曲线"占位）。

### P1-2　构造基准 0.0 被染成"正收益蓝"，且颜色分支因字符串比对失效

| 文件:行号 | 代码 |
|---|---|
| `frontend/src/pages/Backtest/resultParts.tsx:54` | `color={c.hl ? '#3B82F6' : **c.label === '基准年化收益' ? '#F59E0B'** : c.up == null ? '#94A3B8' : c.up ? '#3B82F6' : '#EF4444'}` |
| 同文件 :29-31 | `label: benchmarkSynthetic ? '基准年化收益（构造基准）' : '基准年化收益'`<br>`up: kpi.annual_benchmark == null ? undefined : kpi.annual_benchmark >= 0` |

**为什么可疑**：synthetic 时 label 变成 `'基准年化收益（构造基准）'`，第 54 行的字符串等值比对**恒为 false**，颜色落到 `c.up` 分支：`annual_benchmark = 0.0` ⇒ `0.0 >= 0` ⇒ `true` ⇒ **蓝色（涨色）**。一个"无基准"的构造值被染成正收益色。用 label 字符串做业务分支本身就是脆弱写法。

**建议**：改成显式 `benchmarkSynthetic` 布尔分支；synthetic 时 `up = undefined`（中性灰），与同文件 :24-25 自己写的"不得用 (x ?? 0) >= 0 兜方向"规范保持一致。

### P1-3　「AI 市场情绪指数」无任何 AI，权重 45 / 1.5 是产品拍的

| 文件:行号 | 代码 |
|---|---|
| `backend/app/api/v1/market.py:505-530` | `score = round(50 + breadth * **45** + (heat["limit_up"] - heat["limit_down"]) * **1.5**)`<br>`"basis": "平台自研口径：score = 50 + (涨-跌)/(涨+跌)×45 + (涨停-跌停)×1.5，权重为产品设定，非第三方情绪指数"` |
| `frontend/src/pages/MarketOverview/AiPicksPanel.tsx:88` | `AI 市场情绪指标<span …>ⓘ</span>` |

**为什么可疑**：公式是纯广度算术，没有模型、没有拟合、没有 AI。前端标题写「**AI** 市场情绪指标」，仪表盘做成"恐慌/中性/贪婪"。后端 `basis` 和前端 `title`（tooltip）都有披露，但**标题本身在说谎**——披露放在 ⓘ 悬浮里，主标签仍是 AI。同上一条：这是"名字造假"而非"数据造假"。

**建议**：主标题改为「市场情绪指标（广度口径）」或把"平台自研·非 AI"做成常驻副标题。

### P1-4　「置信度」= `clip(0.5 + 2×RankIC, 0, 1)`，同一模型下所有个股同值

| 文件:行号 | 代码 |
|---|---|
| `backend/app/ml/predict.py:36-51` | `"""模型级置信度：clip(0.5 + 2 * valid_rank_ic, 0, 1)。"""`<br>`return float(min(1.0, max(0.0, **0.5 + 2.0 * ric**)))` |
| 同文件 :10-11 | `confidence 当前为【模型级】置信度占位（由验证集 RankIC 映射）` |
| 同文件 :156-160 | `"confidence_basis": "…同一模型下所有个股同值，非个股上涨概率…"` |

**为什么可疑**：0.5 与系数 2 是无依据的线性映射，被命名为"置信度"。已通过 `confidence_basis` 完整披露，且 metrics 缺失时返回 `None`（不回退 0.5）——这点做得对。但字段名 `confidence` 本身仍会被下游/用户当概率读。

**建议**：字段改名 `model_ic_score`，或在 UI 上永不单独展示 `confidence` 数字，只展示 `confidence_basis` 文案。

### P1-5　筹码分布在换手率缺失时用「经验常数 1.2%」构造，但 `note` 不区分

| 文件:行号 | 代码 |
|---|---|
| `backend/app/domain/chip.py:28-31` | `#: 换手率缺失时的估算基准（A 股日均换手率经验值 ~1.2%）`<br>`FALLBACK_TURNOVER = **0.012**` |
| `backend/app/domain/chip.py:56` | `t = np.where(base > 0, v / base, 0.0) * **FALLBACK_TURNOVER**` |
| `backend/app/domain/chip.py:203-216` | 返回 `avg_cost / profit_ratio / trapped_ratio / p5 / p95 / concentration / curve`，`"note": "日频换手衰减近似模型，非 Level-2 真实盘口筹码，仅作研究参考"` |

**为什么可疑**：当 `turnover` 列缺失或整列 0 时，整条筹码分布链（平均成本、获利盘比例、套牢盘比例、集中度）建立在 `量能相对倍数 × 1.2%` 这一经验常数上。而返回的 `note` **对"真实换手"和"经验常数估算"两种情况完全一样**，用户（乃至前端）无法分辨。
**当前风险等级**：实测 `daily_bar` 的 `turnover` 真实存在且 2026 分区 null 率 0% ⇒ **当前未触发**，属"潜伏的构造路径"。

**建议**：`_turnover_series` 返回 `(t, is_estimated)`；`chip_distribution` 在 `is_estimated=True` 时把 `note` 换成"换手率不可得，已按 A 股日均经验值 1.2% 估算，指标仅供定性参考"，并加 `turnover_source: "estimated" | "real"`。

### P1-6　夏普口径分叉：回测链路 `rf=0`，组合/风控链路 `rf=2%`

| 文件:行号 | 代码 |
|---|---|
| `backend/app/domain/metrics.py:23-26` | `# 无风险利率的**唯一来源**（审计 B2-16…）`<br>`RISK_FREE_ANNUAL = **0.02**` |
| `backend/app/backtest/ma_cross.py:288` | `"sharpe": sharpe_ratio(strat)` ← 默认 `rf=0.0` |
| `backend/app/backtest/engine.py:496` | `all_metrics(nav_df["nav"].to_numpy(), turnovers_per_day=…, n_trials=…)` ← **未传 rf** ⇒ 0.0 |
| `backend/app/backtest/strategy_base.py:286` | `m = all_metrics(strat_nav)` ← 未传 rf ⇒ 0.0 |
| `backend/app/trading/paper.py:461` | `# 夏普（日收益年化，**rf=0**，252 交易日）` |
| `backend/app/domain/risk.py:100` / `portfolio.py:468` | `rf: float = RISK_FREE_ANNUAL` / `RISK_FREE_ANNUAL` |

**为什么可疑**：`metrics.py:23` 的注释宣称这是"无风险利率的**唯一来源**"，但回测/模拟盘三条主路径全部绕开它用 `rf=0`。结果是**回测页的夏普与组合页/风控页的夏普口径不同、不可比**——这正是 B2-16 想消除的问题，但那次只对齐了 portfolio vs risk，**漏了 backtest 与 paper**。注释（"唯一来源"）与代码实际行为不符。

**建议**：让 `sharpe_ratio` / `all_metrics` 的 `rf` 默认取 `RISK_FREE_ANNUAL`；确需 rf=0 的旧对比口径显式传参并在响应里披露。

### P1-7　ETF 持仓报告期解析失败时**凭空造 `{年}-12-31`**

| 文件:行号 | 代码 |
|---|---|
| `backend/app/data/etf.py:787-789` | `dates = re.findall(r"(\d{4}-\d{2}-\d{2})", html)`<br>`report_date = dates[0] if dates else **f"{year}-12-31"**` |

**为什么可疑**：HTML 里找不到日期时，直接合成一个"该年 12 月 31 日"当作重仓股报告期返回。用户会以为这是真实的季报/年报截止日。这是**写死的日期常量**的典型。

**建议**：解析不到时返回 `report_date: None`，前端显示"报告期未知"。

### P1-8　美元 ETF 规模用静态汇率 7.2 折算，无生效日期

| 文件:行号 | 代码 |
|---|---|
| `backend/app/core/config.py:141-144` | `USD_CNY_RATE: float = Field(default=**7.2**, description="…人工配置值，非实时汇率")` |
| `backend/app/data/etf.py:641,653-655` | `fx = get_settings().USD_CNY_RATE`<br>`"size_yi": (round(mv * fx, 2) if mv else None)`<br>`"size_basis": f"亿美元 × USD_CNY_RATE={fx}"` |

**为什么可疑**：规模（亿元）是"美元市值 × 人工常量"，汇率漂移会直接反映成规模错误；`size_basis` 有披露但没有"汇率日期"。
**建议**：`size_basis` 补上汇率生效日，或在 Settings 页暴露并可改。

### P1-9　研究工作台首屏用**硬编码示例组合/示例因子**跑真实计算，未标注"示例"

| 文件:行号 | 代码 |
|---|---|
| `frontend/src/pages/Research/index.tsx:40-45` | `const DEFAULT_ASSETS = [{000001.SZ,0.4},{300750.SZ,0.3},{000002.SZ,0.3}];`<br>`const DEFAULT_FACTORS = ['ret_5','vol_20','rsi_14','skew_ret_20','ma_gap_20','v_rank_20'];` |
| 同文件 :128 | `const [availableFactors, setAvailableFactors] = useState<string[]>(**DEFAULT_FACTORS**);` ← 接口失败时用户看到的就是这 6 个硬编码因子 |
| 同文件 :224-227 | `researchApi.optimize({assets: DEFAULT_ASSETS, method:'risk_parity', weight_cap:0.4, **turnover_penalty: 3.0**, **cov_window: 120**})` |
| 同文件 :331 | `turnover_penalty: **pen ? 3.0 : 0**, cov_window: 120` |
| 同文件 :232-233 | `researchApi.impactSim({symbol: **'000001.SZ'**, algo:'vwap', order_amount: **20_000_000**, participation_cap: **0.05**, …}, …)`；:343 覆盖 `split_days:5, lookback_days:20` |
| 同文件 :357 | `{/* 顶栏：真实统计 */}` |

**为什么可疑**：页面顶部写着「顶栏：真实统计」，但统计对象是**一组写死的示例资产与示例因子**。计算本身是真的，可"这组权重是谁的？"没有答案——它不是用户的任何真实持仓。同样的模式见：
- `frontend/src/pages/Portfolio/index.tsx:48-52` `DEFAULT_ASSETS = [茅台 0.3 / 招行 0.4 / 宁德 0.3]`
- `frontend/src/pages/CapacityAttribution/index.tsx:13-16,33-39`（示例组合 + 参与率 1% / 持仓 20 只 / 年调仓 12 次）

**建议**：页面上加"当前为示例组合，仅用于口径演示"徽标；`availableFactors` 初值改为 `[]`，加载失败显示"因子清单不可用"。

### P1-10　AI 标签阈值 0.02 / 0.008 / -0.008 / -0.02 无来源披露

| 文件:行号 | 代码 |
|---|---|
| `frontend/src/pages/MarketOverview/AiPicksPanel.tsx:45-51` | `if (score >= **0.02**) return '强烈看多'; if (score >= **0.008**) return '看多';`<br>`if (score > **-0.008**) return '震荡'; if (score > **-0.02**) return '看空'; return '强烈看空';` |

**为什么可疑**：把 5 日预测收益切成 5 个语义档（含"强烈"），阈值是纯前端硬编码，后端无对应口径、UI 无来源说明。`0.008`（5 日 0.8%）即算"看多"这个档位边界会显著放大标签的情绪强度。

**建议**：阈值下沉到后端单一来源并随响应返回（`tag_thresholds`），或在标签上挂 title 说明分档口径。

---

## 🟡 P2 · 过度兜底吞异常（6 条）

> 总体评价：全仓 `except Exception` 约 190 处，其中绝大多数带 `# noqa: BLE001` + 中文理由（"不阻断启动/不影响主流程/降级披露"），**整体是克制的**。真正裸 `except …: pass` 约 20 处，逐个核对后多为可选元数据（model_registry 计数、last_sync、localStorage），风险低。以下只列有实际误导风险的。

| # | 文件:行号 | 代码片段 | 为什么可疑 | 建议 |
|---|---|---|---|---|
| P2-1 | `backend/app/api/v1/etf.py:942-946` | `try: fee = E.fetch_etf_fee(code)`<br>`except Exception:  # noqa: BLE001`<br>`    **pass**`<br>`return _block_ok({… "management_fee": fee.get("management") …})` | 费率抓取失败被静默吞掉，返回 `management_fee: None`，但整块 status 仍是 **`"ok"`**——用户看到"ETF 详情正常，管理费率为空"，无法区分"该 ETF 无费率"与"抓取失败" | 失败时把块降级为 `degraded` 并带 reason，或至少置 `fee_status: "unavailable"` |
| P2-2 | `backend/app/data/etf.py:1181` | `ratio = float(it.get("ratio") **or 0**)`（ETF 持仓行业分布聚合） | 某重仓股占比缺失/为 0 时按 0 累加，**静默少算**行业权重，行业分布饼图看起来完整实则缺斤两 | 缺失计入 `unknown` 桶并在响应里返回 `n_ratio_missing` |
| P2-3 | `frontend/src/pages/Research/index.tsx:128` | `useState<string[]>(DEFAULT_FACTORS)` | `loadSection('factor')` 失败时 `availableFactors` 保持硬编码 6 因子，UI 把它们当"平台支持的因子清单"渲染 | 初值改 `[]`；失败显示"因子清单不可用" |
| P2-4 | `frontend/src/pages/MarketOverview/AiPicksPanel.tsx:55` | `const score = sentiment?.score ?? **0**;` | 情绪不可得时仪表盘指针落到 **0（"恐慌"端最左）**，label 才是 `—`。视觉上"0 分 + 指针在极度恐慌区"比"无数据"更像一个真实读数 | `sentiment == null` 时不渲染 gauge，显示"情绪暂不可用" |
| P2-5 | `backend/app/ml/monitor.py:581` | `except Exception:  # noqa: BLE001 列缺失/索引不可对齐 ⇒ 不折算` | 静默跳过折算（**待确认**：需确认被跳过的折算结果是否以"未折算原值"形式继续上报，若是则会与同页其他已折算指标口径混用） | 跳过时给结果打 `converted: false` 标记 |
| P2-6 | `frontend/src/stores/usePreferencesStore.ts:7` | `const DEFAULT_REFRESH_SEC = **5**;` | 前端默认 5s 轮询，后端 `/market/quotes` 进程缓存 `QUOTES_TTL=15`（`core/config.py:177-180`）——**轮询频率是后端缓存 TTL 的 3 倍**，大部分请求打在缓存上，看起来"实时"实际 15s 粒度 | 把默认对齐到 15s，或让后端在响应里回 `next_refresh_after` |

---

## 🟢 P3 · 死代码 / 文档与实现不符（5 条）

| # | 文件:行号 | 代码 / 文档 | 问题 | 建议 |
|---|---|---|---|---|
| P3-1 | `backend/app/data/etf.py:525` | `return f"{cat[0]}{cat[1:]}ETF" if False else cat` | **死分支**：`if False` 使前半句永不执行，恒返回 `cat`。残留的重写痕迹 | 删除三元，直接 `return cat` |
| P3-2 | `backend/app/data/etf.py:647` | `"type": "跨境型" if False else "股票型"` | **死分支**：美股 ETF 恒为"股票型"，"跨境型"分类永不出现。与 `classify_board` 里"纳指/标普/日经→跨境ETF"（:520-521）口径矛盾 | 删掉 `if False`，或统一为按 `classify_board` 结果决定 type |
| P3-3 | `backend/app/api/v1/report.py:1-9` + `frontend/src/pages/Report/index.tsx:90` | 后端 docstring：「AI 日报（…**模板版，无 LLM 硬依赖**）…确定性模板拼装」；<br>前端：`<h1 …>**AI 日报**</h1>` | **文档与 UI 名称不符**：实现是纯模板拼装、无 LLM（`LLM_PROVIDER` 默认 `none`，`core/config.py:257-258`），但产品名带 AI。页面底部只写"日报全部由真实落库数据聚合生成｜仅供研究参考"，未说明"非 AI 生成" | 改名「每日投研日报（模板生成）」，或接 LLM 后再称 AI |
| P3-4 | `backend/app/domain/limit.py:58-60` | `# ETF / 指数默认按 main 处理`<br>`if attrs.instrument_type != "stock": return "main"` ⇒ ±10% | **过期/不完整规则**（**待确认**）：创业板 ETF(159xxx)/科创板 ETF(588xxx) 实际 ±20%，北证 ETF ±30%，此处一律 ±10%。实测 `instrument` 表 5552 条中 ETF 类代码（1*/5* 开头）为 **0 条**，故当前 `build_universe_daily` 未触发；但若 ETF 进入回测/闸门链路即算错涨跌停 | 按 ETF 代码段判定板块；至少加 `FIXME` 标注已知缺口 |
| P3-5 | `backend/app/api/v1/market.py:485-488` | `# B7a-09：原为 "hfq" if hfq_files else "raw_fallback"，但 else **不可达**…那是一句"永不兑现的降级承诺"` | **已修复并留档**的死承诺，注释本身是正面证据（说明审计有效），列出以供交叉验证 | 无需改动；保留注释 |

---

## 附：排查过但**判定为误报 / 已正确处理**的项（避免重复劳动）

| 项 | 判定 |
|---|---|
| `frontend/src/pages/StockDetail/index.tsx:385` `const inflow = (mainNet ?? 0) >= 0;` | **误报**：`inflow` 只在 `mainOk`（`main_net_yi != null`）分支内使用，安全 |
| `frontend/src/pages/MarketOverview/BreadthPanel.tsx:83-85` `upPct/downPct` 用 `?? 0` | **误报**：只在 `ok = status==='ok'` 分支渲染 |
| `backend/app/api/v1/screener.py:169` 注释「伪造『最弱』结论」 | **误报**：这是描述**已修复**缺陷的注释（strength 为空时降级 `None` 而非回落 `weak`） |
| `backend/app/api/v1/desk.py:436` `bench_w … .fillna(0.0)` | **正确**：自定义基准组合外的标的权重本就为 0 |
| `backend/app/backtest/param_search.py:139` `np.random.default_rng(seed)` | **正确**：参数随机搜索，种子化、合法 |
| `data/realtime.py:99` / `akshare_adapter.py:74` `random.uniform` 限速 jitter | **正确** |
| `frontend/src/api/client.ts:211` `Math.random()` 退避抖动 | **正确** |
| `backend/app/api/v1/etf.py:837-849` 资金流降级 | **正确**：明确"绝不用成交额等指标冒充净流入"，返回 `items: []` + `status: unavailable` |
| `backend/app/data/quotes_hub.py` 实时行情降级链 | **正确**：腾讯→新浪→`degraded` + `quotes: []`，明写"不造数" |
| `backend/app/domain/trading_rules.py` 费率常量 | **正确**：已从 8 处硬编码收敛为单一事实来源 |
| `backend/app/api/v1/market.py:516-519` 情绪零样本 | **正确**：显式拒绝"零样本 ⇒ 中性 50"（P1-36） |
| `frontend/src/pages/*` 各处 `catch { setXxx(null) }` | **正确**：置 null ⇒ 显示"暂无数据"占位，是有意为之的诚实降级 |
| `frontend/src/types/kpi.ts:17`、`components/charts/Sparkline.tsx:7` | **正确**：类型文档明写"任一为 false 时必须显示"暂无历史序列"占位，**不得**用任何方式补齐曲线" |

---

## 总判断：这个项目的数据/指标，有多少可信？

**分层结论（按"你在屏幕上看到的数字"计）**

| 层 | 可信度 | 说明 |
|---|---|---|
| **行情 / K 线 / 成交量额 / 换手率** | **高（可信）** | 2499 只标的真实落盘、更新至当日（2026-09-28），`amount/volume≈close`、`turnover` 非空且与已知 A 股量级吻合。无合成数据。 |
| **因子 / IC / 回测净值 / 交易成本** | **高（可信，但口径需对齐）** | 计算走真实数据；问题不在"造假"而在**口径分叉**（P1-6 回测 rf=0 vs 组合 rf=2%）与个别构造回退（P1-5 筹码经验换手）。 |
| **AI 预测 / 选股榜 / 信号强度** | **中** | 预测本身是真实模型输出，但**最新分区停在 2026-09-17，滞后 11 天**，与当日行情混排展示。`is_stale` 有披露。 |
| **「AI / 智能」冠名的派生指标** | **低（名字不可信，数据有披露）** | P1-3 情绪指数（纯广度算术）、P1-4 置信度（`0.5+2×IC` 线性拍脑袋）、P3-3 AI 日报（纯模板）——**三者都无模型参与，但都冠以 AI/置信度之名**。这是本项目最大的"看起来有值其实不可信"来源。 |
| **基准相关（回测）** | **低（存在构造值）** | P0-1 的 `annual_benchmark = 0.0` 是唯一确认进入 KPI 的构造数字，且 P1-1/P1-2 让它在视觉上更可信（画策略净值 + 染涨色）。**已部分披露，但数字本身未被中和。** |
| **示例组合类（研究台/组合页/容量归因）** | **数值真实、语义不可信** | 计算是真的，但对象是硬编码的示例组合/示例因子，页面未标注"示例"。 |

**一句话总结**

> 这个项目**不存在系统性造假**：生产路径 0 处随机造数、0 处 mock 数据泄漏，行情/因子/回测的计算链条扎实，且审计痕迹（大量 `# noqa: BLE001` + 中文理由、`basis`/`note`/`freshness` 披露字段、类型注释里"不得补齐曲线"的硬约束）显示团队在**主动防造假**上投入很多。
>
> 真正的问题集中在三类：
> 1. **一个构造数字没被中和** —— 回测基准退化时的 `annual_benchmark = 0.0`（P0-1），且前端还给它画了假曲线、染了涨色（P1-1/P1-2）；
> 2. **"AI" 这个前缀在说谎** —— 情绪指数、置信度、AI 日报三处都是确定性算术/模板，披露藏在 tooltip 里而主标签仍写 AI（P1-3/P1-4/P3-3）；
> 3. **口径分叉未被发现** —— 无风险利率号称"唯一来源"实际回测链路全走 rf=0（P1-6）。
>
> **量化估计**：屏幕上"看起来是模型/指标输出"的数字里，约 **85% 可信**（真实数据 + 真实计算），约 **10% 是"真计算但语义被包装"**（AI 冠名类、示例组合类），约 **5% 是"构造/经验值且未充分中和"**（合成基准 0.0、筹码经验换手、静态汇率、凭空造的持仓报告期）。**没有发现任何一处"纯随机数冒充指标"。**
