# 独立验证报告（verify-backend）— GStack 上线前修复

- 验证人：verify-backend（fresh eyes，独立于实现者）
- 日期：2026-10-04
- 工作区：`D:/Python_Project/Alpha Quant Platform`，HEAD=`393627d`（全部改动仍在工作区、未 commit）
- 基线：后端 `pytest -m "not network"` = 1 failed / 2211 passed / 8 skipped / 4 deselected（唯一红例 `tests/test_monitor_trigger_caliber.py:348`）
- 纪律：**未修改任何生产代码/测试**；探针只写系统 `$TEMP`，跑完删除并复核残留=0；未 git add/commit/stash。

---

## V1 全量门禁 ✅

命令：
```
cd backend && .venv/Scripts/python.exe -m pytest -m "not network" -q -p no:cacheprovider
```
结果：**2212 passed, 8 skipped, 4 deselected, 0 failed**（708.81s / 11m48s）

与基线对比：唯一红例已消除；passed **2211 → 2212（+1）**，skipped/deselected 不变，**无新增失败**。
→ 无失败，无需 `git worktree` 在 HEAD 复现。

## V2 线程池隔离 ✅

- `grep -rn "asyncio.to_thread"` 于 desk/screener/monitor/studio/report.py → **0 命中**（exit=1）。
- 残留 `import asyncio` → **0 命中**；`ruff check` 5 文件 **F401=0**（83 errors 均为既有 B008/Depends 等）。唯一 `asyncio` 文本是 `screener.py:672` docstring，非 import。
- 位置参数逐处核对（≥3 处，全部一致）：
  | 调用点 | 调用实参 | 函数签名 |
  |---|---|---|
  | screener.py:758 | `apply_quote_snapshot, payload, quotes, source, as_of` | `(payload, quotes, source, as_of)` ✅ |
  | screener.py:766 | `filter_stock_rows, rows, board, industry, q, exclude_st` | `(rows, board, industry, q, exclude_st)` ✅ |
  | screener.py:768 | `sort_stock_rows, filtered, sort, sort_dir` | `(rows, sort, dir_)` ✅ |
  | screener.py:570 | `_screen, target, strategy, top_k, board` | `(target, strategy, top_k, board)` ✅ |
  | screener.py:553 | `load_screener_snapshot, …, strategy, board, top_k` | `(trade_date, strategy, board, top_k)` ✅ |
  | studio.py:85 | `gp_miner.start_task, params, pdf.to_pandas()` | `start_task(params, pdf)` ✅ |
  | monitor.py:40 | `monitor.run_monitor, "manual"` | `run_monitor(trigger="manual")` ✅ |
- **kwargs 路径**（`compute_pool.py` 新增 `functools.partial(fn, **kwargs)`，screener `_series(days=,strategy=,top_k=,board=)` 依赖它）：微探针实测「纯位置 / 纯 kwargs / 混合 / 全 kw」四种形态返回值全对 → 与 `asyncio.to_thread` 参数契约一致 ✅。

## V3 desk N+1（实测 SQL 条数）✅

隔离 sqlite（200 母单 × 2 子单），SQLAlchemy `before_cursor_execute` 逐条计数：

| 实现 | 总 SQL | paper_fills 相关 |
|---|---|---|
| 改后（真实端点 `desk.list_orders(limit=200)`） | **3** | **1** |
| 改前逻辑复刻（逐单查 fills） | **202** | **200** |

- fills 相关 SQL：**200 → 1**。
- 输出对比：母单顺序一致 ✅、每单 fills 顺序一致 ✅、整体 JSON 逐字段**完全一致** ✅。
- 口径说明：任务称"改后 2 条"，实测总 **3**（多一条 `COUNT(*)`，与 N+1 无关）；N+1 消除结论成立。

## V4 超时预算 ✅

- `cd frontend && npm run timeout:check` → **✅ 通过**（C1 误杀 / C2 孤儿 / C3 默认覆盖 / C4 集中登记 / C5 保留档位 / C6 收紧）。
- **独立解析**（自写脚本，不复用闸门）：`frontend/src/api/timeouts.ts::CAPPED_PATHS`(30) vs `backend/app/core/timeout_guard.py::_PATH_MAX_BUDGETS`(30)：
  - ① 键集合：**30==30 完全一致** ✅（无"仅前端/仅后端"）
  - ② 每条后端值 == 前端值 × 0.8（±0.01）：**30/30 满足** ✅
    （例：settings 60→48、market/quotes 20→16、screener 30→24、backtest/run 120→96、export/* 180→144、datacenter/datasets 90→72 …）
  - ③ `CAPPED ∩ LONG = ∅` ✅；`_PATH_MAX_BUDGETS ∩ _PATH_BUDGETS = ∅` ✅
  - ④ 延长方向 `_PATH_BUDGETS`(2)==`LONG_PATHS`(2)：strategy-run 660>600、dag/rerun 330>300 ✅

## V5 `/overview/rt` 超时降级保留已完成子块 ✅

直接 `await` 真实端点 `market_overview_rt(refresh=1)`，stub：indices/money_flow 立即 ok、heat `sleep(5)`，`RT_BUILD_TIMEOUT_SECONDS=1.0`：

- 墙钟 1.17s；顶层 `status='degraded'`；
- **`indices` / `money_flow` 原样保留为 ok**（值 == stub 真实值，未被替换）；
- `anomalies` = `unavailable` + 真实 reason（"异动子块：实时块1秒预算已用尽"）；
- `data_freshness.reason` 如实列出"未完成子块：异动"；
- **无占位 0 / 假数据** ✅。
- 日志佐证：`[overview] … 保留已完成子块 ['indices','money_flow']`。
- → 行为变更确实生效，**未整块连坐丢弃**（与旧行为相反）。

## V6 请求体 413 ✅

最小 FastAPI app + 真实 `BodySizeLimitMiddleware`（limit=1024）：

| 形态 | 结果 |
|---|---|
| 小请求 100B（有 content-length） | **HTTP 200** |
| 超限 1524B（有 content-length） | **HTTP 413 + code=41300** |
| 超限 2400B（chunked，无 content-length，TE=chunked） | **HTTP 413 + code=41300** |

- 真实 `app.user_middleware` 含 `BodySizeLimitMiddleware`（顺序：CORS → BodySizeLimit → TimeoutGuard → PanicGuard）。
- 错误码双向：`backend/app/core/errors.py:123 ERR_PAYLOAD_TOO_LARGE=41300` ↔ `frontend/src/types/api.ts:51 PAYLOAD_TOO_LARGE: 41300`。
- `pytest tests -k "error_code" -m "not network"` → **15 passed**（含 `test_backend_and_frontend_code_tables_match` 双向逐值对账）。

## V7 SSE 连接上限（含断连归还）✅

直接 `await` 真实 `notify.stream()`，自造场景（不跑实现者用例）：

- 同用户建 5 → active=5；**第 6 → HTTP 429 + code=40103** ✅
- 断 3（`aclose`）→ active=2；**再建 3 → 全 OK，active=5**（无泄漏；泄漏则第 6 次仍被拒）✅
- **异常断开路径**：`athrow(ConnectionError)` → active 5→4 ✅
- 全关 → active=0、user_map={} ✅
- 全局：压到 3，用 4 个不同用户打 → 第 4 个 429（全局上限生效）✅

## V8 红例修复的"证伪" ✅（用例**未被改废**）

- 新断言契约：基线健康性挂 **body 统计量**（`mean ≤ DRIFT_MEAN_WATCH`、`ratio_degraded ≤ DRIFT_RATIO_DEGRADED`、`_drift_state ∈ healthy/watch`）；注入灵敏度挂 **被注入因子自身 per-factor PSI**（`> PSI_DEGRADED` 且 `> 5×基线`）。
- 与生产契约一致：`monitor._drift_state` 的 dict 分支（`monitor.py:819-821`）确实以 `mean` + `over.ratio_*` 判定；单因子 `max` 仅披露（`monitor.py:620-624`）。旧断言 `max ≤ 0.25`（HEAD:348）与现契约矛盾（真实面板 `max=0.5149`，被 regime 因子 `rank_close_60` 主导）⇒ **旧红例是"断言过时"，非产品 bug**。
- **证伪实测**：写 pytest 插件把 `app.ml.monitor.compute_psi` 换成"恒返回 0"（注入不可检出），跑该用例 → **FAILED**：
  ```
  tests\test_monitor_trigger_caliber.py:386: in test_real_panel_injection_sensitivity_table
      assert pf["ma_gap_250"] > M.PSI_DEGRADED
  AssertionError: 注入 30% 未被检出（被注入因子自身 PSI=0.0）
  assert 0.0 > 0.25
  ```
- → 用例仍能抓住"注入灵敏度退化"回归，**未被改废**（非"为让门禁变绿而删断言"）。

---

## 反证 / 存疑（最有价值部分）

1. **【未覆盖项·重要】新增的请求体上限（41300）与 SSE 连接上限在 `tests/` 中零测试覆盖**（`grep BodySizeLimit|body_limit|41300|_acquire_sse_slot|_GLOBAL_CONN_LIMIT` 于 tests/ 无命中）。两条新基础设施防线没有回归守卫，仅靠本次探针证明生效；后续重构可能静默破坏。**建议补测试**。
2. **【存疑·低风险】SSE 名额 release 只在 `gen()` 的 finally**：若响应已构造、但生成器在首帧迭代前就断开（Starlette 未开始迭代），finally 不执行 → 计数泄漏窄窗。实测正常/异常断开路径均无泄漏，风险低。
3. **【观察·非本次引入】真实 `app.user_middleware` 最外层含 `BaseHTTPMiddleware`**（应为 `@app.middleware("http")` 的 timing），而项目注释声称"严禁 BaseHTTPMiddleware"。属既有状态，非本次改动，供知悉。
4. **【口径微差】** V3 任务称"改后 2 条 SQL"，实测总 **3**（含 `COUNT(*)`）；N+1 消除结论不变。
5. **【既有】ruff 对 5 个文件报 83 个 B008 等**（`Depends`/`require_role` 作默认值），F401=0，非本次引入。

## 未覆盖项

- 前端 vitest 全量（本次仅按要求跑 `timeout:check`）。
- `/overview/rt` 的**真实外部源**路径（V5 用 stub 控时，未触网）。
- 413 / SSE 的**真实 ASGI server**（用 TestClient / 直调端点，未起 uvicorn）。

## 残留复核

- `$TEMP` 探针文件与 `v3_desk_*` 临时目录：删除后 **残留=0**。
- `git status`：无新增于 `backend/**`、`frontend/**` 的未跟踪文件（3 个 ?? 均为改动前既有）；HEAD 仍 `393627d`，未 commit/add/stash。
