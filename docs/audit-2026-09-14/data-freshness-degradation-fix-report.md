# 数据新鲜度与空壳推荐降级修复报告

日期：2026-09-15  
任务：Task #15

## 修复范围

本次仅处理市场概览 AI 推荐、Alpha 选股、新鲜度状态与用户错误文案。未修改 SSE、权限、ETF 性能、Research 并发和市场概览既有缓存/响应预算设计。

## 后端实现

### 1. 推荐榜空壳过滤

`backend/app/api/v1/market.py` 的推荐构建器现在会为候选读取真实日线行情，并仅在 `close`、`pct`、`amount` 至少一个有效时返回该项。关键行情全空的候选会被过滤，不再仅凭预测分构成推荐。

返回项新增兼容字段：

- `close`
- `pct`
- `amount`

原有 `pred_score`、`rank_pct`、`bars`、模型版本等字段保持不变。

### 2. 推荐/选股最小统一契约

推荐块和选股响应补齐以下字段：

- `status`: `ok` / `degraded` / `unavailable`
- `as_of`: 数据所属交易日
- `reason`: 稳定机器码
- `message`: 用户友好说明
- `coverage`: `{available, total, ratio}`
- `items`: 所有状态均保持数组，空结果为 `[]`

稳定原因码：

| reason | 含义 |
|---|---|
| `no_matching_signals` | 模型已产出，但当前筛选没有满足条件的有效信号 |
| `model_not_ready` | 指定交易日无预测分区或预测分区为空 |
| `market_data_missing` | 候选关键行情全空，过滤后无可展示项 |
| `market_data_partial` | 部分候选关键行情缺失，已过滤空壳并保留有效项 |
| `data_stale` | 数据落后最近已收盘交易日 |
| `recommendation_build_failed` | 推荐构建内部失败（对外不暴露异常类型） |
| `recommendation_timeout` / `recommendation_unavailable` | 聚合接口预算耗尽或整体降级 |

### 3. 空结果不再抛裸异常

选股指定日期没有预测结果时，不再返回业务错误信封让页面进入错误态，而是返回 HTTP 200 统一成功信封，业务数据为：

- `status=unavailable`
- `reason=model_not_ready`
- `items=[]`
- 完整 `stats`、`coverage`、`freshness` 兼容结构

无满足信号时返回 `status=ok`、`reason=no_matching_signals`，与模型未产出明确区分。

### 4. 新鲜度与旧缓存兜底

选股路由在每次响应时执行最终化处理，不把随时间变化的 freshness 固化进缓存；同时过滤旧缓存/旧 SQLite 快照中可能存在的行情全空项。

推荐榜复用选股的交易日历新鲜度计算：行情滞后时保留有效推荐项，但返回 `status=degraded`、`reason=data_stale` 和落后交易日说明。

### 5. 异常消息脱敏

- 推荐构建异常只进入服务端日志；接口固定返回友好说明，不返回 `ValueError`、`AQPException` 等内部类名。
- 全局未处理异常响应由 `系统异常: <Python类名>` 改为 `系统暂不可用，请稍后重试`。
- 前端 API 客户端增加二次防线，检测 Python/框架异常特征并替换为稳定友好文案。

## 前端实现

### MarketOverview / AiPicks

- `degraded` 且仍有有效项时继续展示数据，并在面板顶部显示降级原因与数据时间。
- `unavailable` 或空数组时显示后端 `message`；无信号明确显示“当前没有满足条件的有效信号”。
- 面板不再直接展示机器码 `reason`。

### Screener

- 使用明确的 `ScreenerResult` 类型替代 `any`。
- 降级/不可用时展示友好 message 和 `as_of`，不丢失已有有效条目。
- 空数组按原因显示无信号、模型未产出或行情缺失，而不是统一提示运行流水线。

## 测试

新增 `backend/tests/test_data_freshness_degradation.py`，覆盖：

1. 全空推荐项过滤；
2. 模型无预测；
3. 数据滞后但保留有效项；
4. 正常推荐路径；
5. 无信号与行情缺失原因码区分；
6. 未处理异常消息脱敏。

相关测试命令：

```text
backend/.venv/Scripts/python.exe -m pytest \
  backend/tests/test_data_freshness_degradation.py \
  backend/tests/test_screener_snapshot.py::test_freshness_reports_trading_day_lag \
  backend/tests/test_api.py::test_screener_empty_predictions -q
```

结果：`8 passed`。

复跑新增最小测试：`6 passed`。

前端类型检查：

```text
npm --prefix frontend run type-check
```

结果：通过（`tsc --noEmit`，0 errors）。首次检查曾被 Research 并行任务的缺失导入短暂阻塞；该并行改动完成后复跑已全绿，本任务未修改 Research 文件。

## 兼容性与边界

- 保留市场概览既有 SWR 缓存键、TTL、stale window 和 5/6 秒构建预算。
- 保留原响应字段，仅新增状态/行情字段并把“无模型结果”从异常信封改为可展示的空数据状态。
- 未运行全量回归，符合“先完成全部修复优化，最后统一验收”的要求。

## 结论

IS_PASS: YES（Task #15 实现、最小相关测试与全局前端 type-check 均通过）。
