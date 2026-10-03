# 契约一致性审查 · 第二轮补充摘要（回传给 team-lead 的备份）

> 说明：SendMessage 工具在本次会话中持续返回 `"content" is required`，无法回传，
> 故将摘要落盘于此，供主理人取用。完整报告见同目录 `02-contract-consistency.md`。

## 计数

| 严重度 | 数量 |
|---|---|
| P0 | 1 |
| P1 | 7 |
| P2 | 16 |
| **合计** | **24** |

（第一轮 1 / 5 / 13；第二轮补充新增 2 P1 + 3 P2）

## 新增 P1

### P1 · 参数寻优的 NaN 目标值进入 `top5` ⇒ HTTP 500，破坏「恒 200 + 信封」契约（间歇性）

- `backend/app/backtest/param_search.py:102-107`：`trials.append(t)` 未过滤 NaN（仅 `:108` 选 best 时用 `np.isfinite` 过滤）
- `backend/app/backtest/param_search.py:53-58`：`top()` 对 NaN 直接 `round(t.objective, 6)`
- `backend/app/api/v1/backtest.py:657`（及 `:638` / `:670`）：`evaluate` 返回 `sharpe`，恒定净值/无交易 ⇒ NaN
- `backend/app/api/v1/backtest.py:820-824`：`optimization.top5` 进入响应体
- 序列化（已实测）：`backend/.venv/Lib/site-packages/starlette/responses.py:196-203` → `json.dumps(..., allow_nan=False)`；`fastapi/responses.py:8` re-export 该类
- 间歇性根因：`api/v1/backtest.py:942` 先用 **orjson**（NaN→null）写 Redis，`:943` 才渲染响应 ⇒ **首次 500（纯文本）、10 分钟内重试命中缓存反而 200**
- 附带：`sorted(reverse=True)` 在含 NaN 时比较全假 ⇒ 展示的「最优 5 组」不保证是最优（`frontend/src/pages/Backtest/index.tsx:243`）
- 建议：`trials` 收集阶段过滤 `np.isfinite(obj)` 并披露 `n_invalid_trials`；`top()` 二次兜底；
  更根本的是加**响应体净化中间件**（递归 NaN/Inf → null），替代各路由手写的 `v == v`

### P1 · 「最大回撤」同站三处两种符号：组合回测页显示 **+25.00%**

- 后端量纲：`backend/app/domain/metrics.py:91`（正）、`backend/app/domain/portfolio.py:152-153`（正）、`backend/app/trading/paper.py:455-460`（**负**）
- 前端：`frontend/src/pages/Backtest/resultParts.tsx:42`（手加 `-` + 绿，正确）、
  `frontend/src/pages/OrderDesk/index.tsx:310-312`（原样 + 绿，正确）、
  **`frontend/src/pages/Portfolio/index.tsx:531-532`**（`fmtPct(x * 100)`，`utils/format.ts:12` 的 `p > 0 ⇒ '+'` ⇒ 渲染 `+25.00%` 且无色，错）
- 影响：用户在组合回测页读到「最大回撤 +25.00%」，与另两页 `-25.00%` / `-12.00%` 反号

## 新增 P2

- **P2-14** 组合回测 8 个披露字段前端零消费（`backend/app/domain/portfolio.py:493-520` 的
  `weighting` / `weights_normalization` / `rebalance_log` / `friction_costs` / `data_warnings` /
  `metrics.probabilistic_sharpe` / `metrics.deflated_sharpe` / `metrics.rf_basis`；
  `frontend/src/types/portfolio.ts:85-104` 全未声明）。其中 `data_warnings`（晚上市/停牌缺口/疑似退市）
  在 `frontend/src/pages/Research/index.tsx:686-688` 有一等公民展示 ⇒ 同类数据质量披露两页不一致。
- **P2-15** `backend/app/api/v1/backtest.py:845` `signals[].nav` 用 `nav_map.get(date, float("nan"))` 且未净化，
  与同函数 `:848`（curve）/`:851`（risk_out）的 `v == v` 口径不一致 ⇒ 键类型不一致即 NaN ⇒ 同样触发 500。
- **P2-16** 组合回测前端从不传 `n_trials`（`frontend/src/types/portfolio.ts:20-31` 无该字段）
  ⇒ `backend/app/domain/portfolio.py:173-186` 的 Deflated Sharpe 恒按 1 次试验（等于不惩罚），
  且 `probabilistic_sharpe` / `deflated_sharpe` 前端零消费；对照
  `frontend/src/pages/Backtest/index.tsx:228` 是展示 `deflated_sharpe` 的。

## 第二轮确认为「无问题」的项（供 QA 排除）

- 模拟盘母单 P2-8 截断契约完整消费：`backend/app/api/v1/desk.py:250-251` +
  `frontend/src/pages/OrderDesk/index.tsx:74-75, 453-457`
- 模拟盘账户派生指标判空完整：`OrderDesk/index.tsx:289-323`（缺数显「样本不足」，无 `?? 0`）
- 组合回测指标判空完整：`Portfolio/index.tsx:186-220, 527-534`（唯一例外是回撤符号，见 P1）
- 对 `pages/{Portfolio,Backtest,OrderDesk,Report,DataCenter,Pipeline,CapacityAttribution,FactorStudio,DataQuality,Settings}`
  全量 grep `?? 0` / `|| 0` / `?? 1`：**零命中**

## 通用建议

加一条响应体净化中间件（递归 NaN/Inf → `null`），一次性堵住「NaN ⇒ 500」这类契约破坏；
当前仓库已在 `backtest.py:415-417` / `:848` / `:851`、`portfolio.py:185-187` 各手写一遍，仍漏 2 处。

本次审查全程**未修改任何源码**（仅新增 `docs/audit/2026-10-03/` 下的报告与本文档）。
