# 代码摘要 — 遗留项收尾（回测寻优 / L3 列裁剪 / L4 SWR）

> 工程师环节（python-fullstack-engineer SOP）｜ 日期：2026-09-05 ｜ 前置：Sprint 1-4 已交付
> 用户决策：全市场扩容本轮不做（热月分区与之强耦合，一并顺延为扩容前置项）

## 存量盘点 → 增量收敛

| 遗留项 | 存量（远超路线图描述） | 本轮增量 |
|---|---|---|
| 回测寻优（4.4） | inverse_vol/risk_parity/max_div 权重、TopkDropout、非线性冲击成本、DSR 防过拟合、grid/ga 寻优**均已存在** | **Optuna TPE 寻优器 + walk-forward 折外验证 + 报告落盘 + 前端三法切换** |
| L3 列裁剪 | read_symbol_dataset 全列读 | **columns 投影参数**（逐文件 footer 校验）+ 个股 K 线热路径接线 |
| L3 热月分区 | — | **顺延**（与全市场扩容耦合：收益在 5552 只规模才显现，单标的 1133 日无月分区收益） |
| L4 SWR | 手写轮询 | **swr 2.5.1 引入 + 数据层基建 + MarketOverview 迁移** |

## 文件清单

| 文件路径 | 变更 | 关键实现点 |
|---|---|---|
| `backend/app/backtest/param_search.py` | 修改 | `optuna_search()`（TPESampler，网格值=离散候选 categorical，失败试验告警续跑，DSR 口径不变）；`walk_forward_search()`（evaluate_window 闭包模式：业务层按窗口切片数据，IS 寻优 → OOS 折外评估，汇总 mean_IS/mean_OOS/**overfit_ratio**）；`run_search` 增加 optuna 分支与 `optuna_trials` 传参 |
| `backend/app/api/v1/backtest.py` | 修改 | `StrategyBacktestRequest`：`optimize_method` 扩 `optuna`、新增 `walk_forward/wf_folds`；`_run_walk_forward`（时间轴均切 folds+1 段，每段 ≥20 交易日守卫）；`_persist_opt_report`（寻优报告落 `MODEL_ROOT/exp/backtest_opt/*.json`，失败仅告警） |
| `backend/requirements.txt` | 修改 | `optuna==4.9.0` |
| `backend/tests/test_optuna_walkforward.py` | **新增** | 5 用例：TPE 单峰收敛（真值 7.3 → 命中 7）、统一入口分发与 trials 透传、walk-forward 折结构/调用次数/过拟合比口径、整折失败明确抛错、报告落盘 JSON 可读 |
| `frontend/src/api/strategyBacktest.ts` | 修改 | 请求 `optimize_method` 扩 optuna + `walk_forward/wf_folds`；响应 `StrategyOptimization` 扩折表字段 |
| `frontend/src/pages/Backtest/parts.tsx` | 修改 | 寻优 UI：方法三选（网格/遗传/TPE）+ walk-forward 开关与折数 |
| `frontend/src/pages/Backtest/index.tsx` | 修改 | 结果区 walk-forward 折表分支（IS/OOS 窗口、最优参数、过拟合比告警色） |
| `frontend/src/api/swr.ts` | **新增** | SWR 基建：全局 fetcher（复用统一信封/认证 client）、`REFRESH` 分级表（realtime 30s/snapshot 300s/static 0）、`useApi` hook（key=[url,params]）、全局默认（revalidateOnFocus/dedup 5s/重试 2 次） |
| `frontend/src/pages/MarketOverview/index.tsx` | 修改 | rt/daily 迁移 `useApi`：页面二次切换缓存命中，盘中轮询由 refreshInterval 接管（非盘中 key 切换停轮询），⟳ 轻刷新改 mutate 定点重取（refresh=1 透传后端防抖锁） |
| `frontend/src/pages/Backtest/*`、`package.json` | — | `swr` 依赖（2.5.1） |

## 验证结果

- 新增测试 5/5；全量回归 **386 passed / 5 skipped / 0 failed**（7.1min）
- 离线验证：TPE 25 试验收敛单峰（真值 7.3 → x=7）；walk-forward 3 折 IS/OOS 调用计数精确
- `tsc --noEmit` 0 错误；`npm run build` 成功；后端已重启加载全部代码

## 已知限制 / 遗留项

1. **热月分区 + 全市场扩容**：强耦合顺延（收益在 5552 只规模显现）。扩容执行手册见 `docs/全市场扩容手册.md`。
2. walk-forward 的 OOS 段为「紧随 IS 的下一段」expanding-IS 设计；rolling-IS 变体未做（可按需扩展）。
3. TPE 试验数固定 30/折（前端可后续开放配置）；行业中性权重约束仍为遗留（需 universe 带行业映射接入回测）。
4. SWR 迁移仅 MarketOverview（首个消费者）；其余页面手写轮询与 SWR 并存，按需渐进迁移（基建已就绪）。
