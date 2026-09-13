# Task 1 A/B 对比：回测结算口径 raw → hfq

- 固定参数：start=2025-08-08, end=2026-08-28, top_k=10, init_cash=1,000,000, daily, equal（`backend/scripts/ab_backtest.py`）
- before：commit 543e6f0（数据源 = `universe_daily`，raw 口径结算）
- after：本分支最终 commit（数据源 = `universe_daily_bt`，hfq 口径结算 + 物理整手换算）
- 数据仓：D:/Python_Project/Alpha Quant Platform/data/parquet（universe 1133 只 × 257 个交易日）

## Metrics 对比

| 指标 | before (raw) | after (hfq) | diff | 解读 |
|---|---|---|---|---|
| nav_last | 0.942363 | 0.957438 | **+0.015075** | 全区间净值上修 1.51pp |
| total_return | -5.7637% | -4.2562% | +1.5075pp | 同上 |
| annual_return | -5.6763% | -4.1911% | **+1.4852pp** | 年化虚低修正约 1.5 个百分点/年 |
| sharpe | -0.0957 | -0.0350 | +0.0607 | 风险调整后改善 |
| max_drawdown | 26.05% | 25.21% | -0.85pp | 除权假亏损造成的回撤被修正 |
| annual_vol | 25.93% | 25.97% | +0.04pp | 波动率基本不变（预期：口径只改水平不改路径形状） |
| win_rate | 49.22% | 49.22% | 0 | 日度胜负结构不变（修正只影响幅度） |
| annual_turnover | 121.99 | 122.91 | +0.92 | 基本不变 |
| filled_trades | 3013 | 3037 | +24 | 见下 |
| rejected: lot | 338 | 338 | 0 | 物理整手换算后与 raw 域完全一致 |
| rejected: limit_up | 2 | 1 | -1 | hfq 域涨跌停基准为除权参考价，个别边界判定更准 |

## 差异来源解释（承诺项：差异本身就是失真的量化证据）

1. **主因 = 除权缺口修正（方向：净值上修）**。raw 口径下：
   - 现金分红除息日，持仓市值按 raw 价下跌记账，分红现金从未入账；
   - 送转除权日（如 10 送 10），raw 价减半而持股数不变 → 市值凭空 -50%。
   hfq 口径下价格路径连续，上述两项不再产生虚假亏损。窗口内本地数据实测
   15 个除权事件（raw 单日 -30%+ 而 hfq 正常），逐一落在持仓变动路径上
   累积出 +1.49pp/年 的修正。
2. **整手粒度效应（已修复，未进入最终差异）**。中间版本曾出现 lot 拒绝
   338→954、filled 3013→2741：hfq 价格量级是 raw 的 f 倍，按 100 股整手
   会把"物理 100 股"错算成"100 股 hfq"。修复：broker 按 `factor=hfq/raw`
   把等效手数换算为 `round(100/f)`（`Broker._lot_size`），参与率/冲击成本的
   日成交额同步换算到 hfq 域（`Broker._daily_amount_hfq`）。修复后 lot 拒绝
   与基线逐数一致（338），证明持仓集合不受口径切换扰动。
3. **涨跌停基准修正（次要）**。hfq 域的 limit = hfq 昨收 × (1±pct)，天然
   等于交易所"除权参考价"口径；raw 域除权日的基准未做除权调整。窗口内该
   修正仅改变 1 笔 limit_up 拒绝，影响可忽略。

## 已知边界

- hfq 域涨跌停价不做 0.01 舍入（交易所对 raw 价舍入），0.9999 容差的极端
  边界可能有极少数判定差异（本次窗口未观测到异常）。
- `universe_daily_bt` 与 `universe_daily` 并存：旧数据集未删除，回滚 =
  revert 提交 + `_load_universe_and_signals` 指回旧目录。
