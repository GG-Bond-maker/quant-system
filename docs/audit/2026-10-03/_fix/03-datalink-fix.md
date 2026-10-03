# AQP 数据链路两项修复报告（工程师 · 2026-10-03 · 第二批）

项目根：`D:\Python_Project\Alpha Quant Platform`　作者：software-engineer（寇豆码）

> ⚠️ **必须先读的冲突提示**：修复 A 的目标缺陷在本工作区**已被他人未提交的改动部分修复**
> （`_mark_mass_failure()` + `SYNC_FAILURE_ALERT_RATIO=0.05`，**不在 HEAD** 里）。
> 详见下方"冲突说明"。本次仍按你的规格实现了 worker 级兜底判据，并如实标注两者关系。

---

## 修复 A：sync_service.py —— 终态判定掩盖大面积失败　✅ 完成（附冲突说明）

### A1) 新增阈值常量（`backend/app/services/sync_service.py:142-149`）
```python
# 数据链路缺陷 A（2026-10-03）：**失败占比过高即不得记 SUCCESS**（worker 级兜底）。
# 推导：阈值取 **0.5**（宽松）——只有"半数及以上标的抓取失败"才升级为作业失败，
# 正常的零星网络抖动（个别标的临时失败 → 计入 failed 可 resume 重试）绝不被误判。
# 与上方 SYNC_FAILURE_ALERT_RATIO(0.05) 的关系：后者在 _run_incremental/_run_repair
# 收尾置 _sync.error（**更严格**，对本兜底构成上游覆盖）；本常量服务于 _sync_worker
# 的终态判据，实际价值是覆盖**未调用 _mark_mass_failure 的 _run_rebuild** 等路径
# （rebuild 失败只进 failed 集、不置 error），作防御性兜底。
SYNC_MAX_FAILED_RATIO = 0.5
```
**阈值选 0.5 的理由**：按你的要求"宽松、不误伤零星抖动"。常规运营轮 `failed≈0`
（停牌/退市记 `completed` 不记 `failed`）；只有"半数及以上标的抓取失败"这种显然的
数据源级事故才升级为作业失败。**字段名取自 `:508` 处 `counts` 的真实结构**
（`completed=len(completed_left) failed=len(failed_left)`）：`failed_n = len(failed_left)`、
`total = _sync.total`（`:492` 快照）。

### A2) worker 判据（`:516-536`，在 `noop` 判定之后、`else` 之前）
```python
noop = total > 0 and done == 0
failed_n = len(failed_left)
failed_ratio = (failed_n / total) if total > 0 else 0.0
partial_fail = (not noop) and failed_n > 0 and failed_ratio >= SYNC_MAX_FAILED_RATIO
...
_sync.log("WARNING" if (cancelled or worker_error or noop or partial_fail) else "INFO", ...)
```
终态落库新增分支（`:557-567`，在 `elif noop` 之后、`else(SUCCESS)` 之前）：
```python
elif partial_fail:
    _record_sync_job(
        mode, "FAILED", duration_ms,
        f"PARTIAL: {failed_n}/{total} 只抓取失败"
        f"（{failed_ratio:.0%}），数据可能大面积未更新")
    if task_id:
        update_task(task_id, "failed", progress={"done": done, "total": total},
                    error=f"部分失败（{failed_n}/{total} 只，{failed_ratio:.0%}），可 resume 重试")
```
- SSE 通知同步补 `elif partial_fail`（`:586-589`）。
- `_persist_sync_state` 末参追加 `and not partial_fail`（`:528-530`）：部分失败**保留**
  续传记录供跨重启 resume（与中断/空跑同口径），与"正常完成才清空"一致。
- **`:553` 附近的另一处 `noop` 分支**已检查：那是 SSE 通知里的 `elif noop:`
  （`:583-585`），属同一模式的分支链，已同步补 `elif partial_fail`。无遗漏。

### ⚠️ 冲突说明（重要）
- **HEAD 无此机制，工作区已有另一套且更严格的实现**：`git show HEAD:...sync_service.py`
  中 `_mark_mass_failure` / `SYNC_FAILURE_ALERT_RATIO` **均不存在**；它们是**工作区未提交
  改动**（他人/并行会话所加，`git diff` 显示 `+134,24` 起）。其逻辑：
  `_run_incremental`/`_run_repair` 收尾调用 `_mark_mass_failure()`，当
  `failed/total >= 0.05` 时置 `_sync.error` ⇒ worker 走 `worker_error` 分支记 FAILED。
- **因此**：对 **incremental / repair**，我这条 50% 判据在**生产路径上被 5% 机制上游覆盖**
  （≥50% 必然 ≥5%）——即这两条路径**早已**不会把大面积失败记成 SUCCESS。本判据的真正
  价值是覆盖 **`_run_rebuild`**（它**未**调用 `_mark_mass_failure`，失败只进 `failed` 集、
  不置 `error`）——这是我测试里用 `rebuild` 走**真实可达路径**断言 FAILED 的原因。
- **与规格的不一致点**：你要求的阈值是 0.5（宽松），而工作区既有机制是 0.05（严格）。
  两者对 incremental/repair 的**实际生效口径 = 0.05**（更严格）；0.5 仅在 rebuild 等
  未接 5% 机制的路径上生效。**建议后续统一**（二选一：把 `_mark_mass_failure` 也接到
  `_run_rebuild`，或把两处阈值对齐），本批**不改** `_mark_mass_failure`（属他人改动，
  避免冲突）。我按规格落地了 0.5 的 worker 兜底，未擅自改动既有 0.05 机制。

---

## 修复 B：pipeline.py —— 小代码集全灭时静默放行　✅ 完成

### B1) 规模无关告警标志 + 告警（`backend/app/data/pipeline.py:146-149、166-174`）
```python
# :146-149（在 degraded/else 分叉之前声明，供返回串标注）
all_empty = False

# :166-174（在 "else:" 之内、硬门禁 if 之外，大小代码集都能触发）
if n_universe > 0 and n_ok == 0 and n_expected == 0:
    all_empty = True
    logger.warning(
        f"[pipeline] validate: 当日 {len(codes)} 只标的全部无数据，"
        f"且无法用上一交易日校准（可能为整日断更或全部停牌）")
```
- **行为**：`logger.warning`（**非 fatal**，保留 `:158` 注释所述"有意避开小重跑误杀"的
  既有取舍）；位置在硬门禁分支**之外**，故 `len(codes)<500`（此前静默放行）与
  `≥500`（随后仍被硬门禁拦下）**两种规模都会触发**告警。

### B2) 返回串追加 `all_empty=<0|1>`（`:203-213`，追加在**串尾**）
```python
if degraded:
    return (f"validated={n_ok}/{len(codes)} suspended={n_suspended} "
            f"missing_vs_prev=n/a coverage={coverage:.2f} degraded=1 "
            f"all_empty={int(all_empty)}")
return (f"validated={n_ok}/{len(codes)} suspended={n_suspended} "
        f"missing_vs_prev={len(missing_errs)} degraded=0 "
        f"all_empty={int(all_empty)}")
```
- **向后兼容**：既有消费方按**子串**匹配（如 `"missing_vs_prev=0" in res`），新字段追加
  在串尾不影响任何既有解析；已同步更新 `step_validate` docstring 说明新字段。
- **未动** `:176-181` 的报错文案（已同时打印覆盖率口径），符合规格。

---

## 新增/改动的测试
- 新增 `backend/tests/test_sync_partial_failure_terminal.py`（4 例，修复 A）：
  - `test_rebuild_mass_failure_records_failed` —— **真实可达路径**（`rebuild` 10 只里 8 只
    失败 ⇒ 断言 FAILED + `PARTIAL` + `8/10`）。
  - `test_worker_partial_fail_over_threshold_records_failed` —— worker 判据直测 80% ⇒ FAILED。
  - `test_worker_partial_fail_below_threshold_still_success` —— 反向护栏：10% < 50% ⇒ SUCCESS。
  - `test_threshold_constant_is_loose` —— 钉死阈值 ≥0.5。
- `backend/tests/test_validate_step.py` 追加（5 例，修复 B）：
  - `test_small_codelist_all_empty_warns_not_fatal`（<500 全灭 ⇒ 不 raise + WARNING + `all_empty=1`）
  - `test_normal_day_all_empty_flag_is_zero`（有数据 ⇒ `all_empty=0`，不误报）
  - `test_partial_missing_is_not_all_empty`（400ok+20昨有今无 ⇒ `all_empty=0`）
  - `test_large_codelist_all_empty_warns_then_fatal`（500 全灭 ⇒ 先告警、再被硬门禁 fatal）

---

## 验证结果（真实输出）
### 相关子集
`pytest tests/test_sync_partial_failure_terminal.py tests/test_validate_step.py
tests/test_resume_failed_semantics.py tests/test_failed_count_per_round.py
tests/test_pipeline_code_normalization.py tests/test_data_jobs_timestamp.py -q -p no:randomly`
⇒ **82 passed**（日志 `_fix/pytest_datalink_subset.log`）

### 全量
`pytest -m "not network" -q -p no:randomly`（日志 `_fix/pytest_full2.log`）
```
= 1 failed, 2065 passed, 8 skipped, 4 deselected, 217 warnings in 358.38s (0:05:58) =
FAILED tests/test_monitor_trigger_caliber.py::test_real_panel_injection_sensitivity_table
```
- 相比上一批全量（2057 passed）**+8 passed = 本次新增的 8 个测试**，**无新增失败**。
- 唯一失败 `test_real_panel_injection_sensitivity_table` 是**既有失败**（PSI 基线
  `assert 0.5149 <= 0.25`，与数据链路无关），**未修**（按你的要求）。

### 静态门禁
- `ruff check <改动文件> --select F,E9` ⇒ **All checks passed!**（`_fix/ruff_datalink.log`）
- `mypy app/services/sync_service.py app/data/pipeline.py --ignore-missing-imports` ⇒
  **Success: no issues found in 2 source files**（`_fix/mypy_datalink.log`）

---

## 变更文件清单（本批）
1. `backend/app/services/sync_service.py`（修复 A：常量 142-149、判据 516-536、分支 557-573、SSE 586-589、persist 528-530）
2. `backend/app/data/pipeline.py`（修复 B：标志 146-149、告警 166-174、返回串 203-213、docstring）
3. `backend/tests/test_sync_partial_failure_terminal.py`（新增，修复 A 测试）
4. `backend/tests/test_validate_step.py`（追加修复 B 测试）
- 报告与日志：`docs/audit/2026-10-03/_fix/`（03-datalink-fix.md + 各 pytest/ruff/mypy 日志）

## 与规格的冲突点（汇总）
1. **修复 A 的缺陷在工作区已被他人未提交改动修复**（`_mark_mass_failure`，阈值 0.05，
   不在 HEAD）。对 incremental/repair，实际生效口径是 0.05（更严格），我按规格加的
   0.5 worker 兜底主要覆盖 `_run_rebuild`。**两套阈值口径不一致，建议后续统一**。
2. 除此之外，其余规格（判据位置、PARTIAL 文案、失败占比字段来源、B 的告警文案/位置、
   `all_empty` 追加、不 fatal、不动 :176-181）**均按原样实现，无偏离**。
