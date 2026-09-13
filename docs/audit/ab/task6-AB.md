# Task 6 A/B 对比：退市语义 + 持仓强平减记

## 代码改动

- `Broker.liquidate()`：按最后有效收盘 × haircut 折价清仓，reason=
  `delisted_liquidation`，立即重估 total_equity；
- `run_backtest()` 新增 `delist_haircut=0.5` / `delist_grace_days=60`：
  持仓连续退出宇宙超过宽限期 → 强平（API 透传并入缓存键）；
- `build_universe_backtest()`：instrument.delist_date 之后的日期移出宇宙
  （退市股不再表现为"永续停牌"）；
- `fetch_delist_list()`（东财沪深退市名单；SH 源仅提供暂停上市日期，
  作为退市时点保守近似——适配器内注明）+ `scripts/enrich_delist.py`
  （--apply 门禁）+ `upsert_delist_dates()`；
- `/run` 响应新增 `universe_scope` 披露股票池口径（幸存者偏差透明化）。

## 机制验证（单测，非估算）

| 场景 | 断言 |
|---|---|
| broker 强平 | px = 10×0.5 = 5.0；持仓清零；equity = 前 − qty×10×0.5 ✓ |
| 引擎强平 | A 退出宇宙 6 日后触发 1 笔强平（qty=买入整手数）；近 5 日持仓无 A ✓ |
| 无退市场景 | 不触发强平，行为与旧版一致 ✓ |
| 宇宙过滤 | delist_date=2024-01-04 → 01-04 之后无网格行（01-04 当日仍在）✓ |

## 真实数据 A/B（诚实结论：本窗口无行为差异）

```
T2-after nav=0.947596  ==  T6-after nav=0.947596（filled/sharpe/mdd 全同）
```

原因：`enrich_delist.py` dry-run 实测，退市名单 361 条与本地 instrument
**交集 0 条**——本地 1133 只宇宙抽样自当前在市股票，历史退市股本就不在
其中（这正是幸存者偏差的来源，本任务无法凭空补出其历史行情）。因此：

- 当前数据集上不存在"退市持仓冻结"的实际案例，修复无行为变化（回归安全）；
- **修复的真实价值在结构预防**：今后任何已下载股票退市（如 2026-07-14
  终止上市的 000004 类情形若在宇宙内），只要运行
  `enrich_delist --apply` → `build_universe_backtest`，其持仓就会按
  haircut 强平而非永久冻结估值；
- 若宇宙中存在"数据中断但未退市"的股票（instrument 无 delist_date），
  其网格行仍延伸到末日（is_halted），强平不触发——该情况属于数据缺口，
  应补数据而非误判退市，语义上是有意为之。

## 已知边界（ledger 已记录）

- `run_group_backtest` 未接强平逻辑（分组回测为诊断工具，延后）；
- ST 状态历史（is_st asof）需要名称变更历史数据源，本任务未接入，
  宇宙构建的 ST 判定仍用当前快照（审查报告 Q-P1-9 的残留项）。
