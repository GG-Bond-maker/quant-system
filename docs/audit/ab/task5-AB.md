# Task 5 A/B 对比：流动性口径接通与如实披露

- 代码：universe_daily_bt 自 T1 起携带 amount（raw 口径）+ factor；本任务
  补端到端测试（4 项）+ `/run` 响应 `liquidity` 披露块 + runner 摩擦参数。
- A/B：同参数（top10 / 100 万 / daily）开/关摩擦各跑一次。

## 结果

| 项 | 无摩擦 | 开摩擦（滑点5bp+sqrt冲击10bp+衰减10bp+参与率5%） |
|---|---|---|
| nav_last | 0.9476 | 0.7359 |
| annual_return | -5.16% | -26.06% |
| friction_costs.slippage | 0 | 108,310 元 |
| friction_costs.**impact** | 0 | **2,057.66 元** |
| friction_costs.decay | 0 | 108,676 元 |

## 解释

1. **冲击成本首次真实非零**（2,057.66 元）：旧主路径因 universe_daily 无
   amount 列，冲击成本恒 0、参与率上限无操作——修复后 sqrt 冲击按当日
   真实成交额参与率计费，且 factor 缩放保证 hfq 域口径不虚增参与率。
2. 摩擦全套合计把年化从 -5.2% 压到 -26.1%（滑点+衰减各约 10.8%，
   主要是 daily 全量调仓 × 122 倍年化换手的代价）——这正是审核指出的
   "主回测偏乐观"的量级披露；真实可执行性评估必须开摩擦。
3. 无摩擦路径数字与 T2 after 完全一致（nav 0.947596），证明本任务
   未改动结算语义，只补披露与验证。

## 测试

- `tests/test_impact_cost_e2e.py` 4 项：冲击非零 / 缺 amount 时按 0 不虚构 /
  参与率截断 / factor 缩放下参与率不虚增。
- `tests/test_api.py` 24 项全绿（liquidity 字段为纯新增，前端兼容）。
