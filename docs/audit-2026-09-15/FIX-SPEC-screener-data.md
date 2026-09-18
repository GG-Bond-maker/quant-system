# 修复规格：选股数据链路 3 个缺陷（BugFix）

- 关联诊断：`docs/audit-2026-09-15/DIAGNOSIS-empty-screener.md`
- 用户决策：**先修 3 个代码缺陷**；C 项先量化影响（已完成，见下方 §0）；**本轮不跑联网回补、不改动任何数据文件**
- 范围：仅 `backend/app/`、`backend/tests/`、`scripts/`

---

## §0 影响面量化（已完成，供工程师参考）

根因 C 涉及的 771 只未登记标的画像：

| 维度 | 结论 |
|---|---|
| instrument 表覆盖 | **771/771 全在表内**，`instrument_type='stock'` |
| 板块分布 | main 479 / chinext 197 / star 95（**无北交所**） |
| 历史长度 | 769 只 501~1500 行（2022→2026），2 只仅 2026 年（新股） |
| 年分区数 | 5 个 = 730 只，4 个 = 39 只，1 个 = 2 只 |
| ST | 32 只（4.1%，下游 `filter_universe` 会剔除，不进榜） |

**判断：这 771 只是真实标的，非垃圾数据。** 特征预热窗只需 400 个交易日（≈1.6 年），其 2022→2026 的历史（≈950+ 交易日）**完全满足**，因此排除它们是 manifest 缓存事故而非数据不足。

预期影响：标的池 1729 → 2500（+44.6%）；生产模型 `lgbm_v1_20260905_003646_bulk1133_demean` 做横截面 demean，**成分变化会移动 demean 基准 → Alpha 排名分布会变**。故：本轮**只落代码**，实际放开留待用户批准数据回补后观察。

---

## §1 修复 D — 同步任务"假成功"（P1，最小改动）

文件：`backend/app/services/sync_service.py` :: `_sync_worker`（≈L334-407）

现状：只要「未 cancel 且未抛异常」就 `_record_sync_job(mode, "SUCCESS", ...)`，不校验实际工作量。09-14 的 `sync_incremental` 即以此为 1109ms 空跑记了 SUCCESS。

要求：
1. 在 `finally` 里取快照时**额外**捕获 `total = _sync.total`；`done` 已有。
2. 新增"真实工作量"判定：`noop = (total > 0 and done == 0)`。
   - 依据：`_run_incremental` 的跳过分支（`last >= target`）与处理分支**都会**推进 `_sync.done`（L234 / L261），因此 `done == 0 且 total > 0` 唯一对应"循环一次都没跑"。
3. `noop` 时：`_record_sync_job(mode, "FAILED", duration_ms, "NOOP: 未处理任何标的（done=0/total=N）")`，并 `_persist_sync_state(..., finished=False)`；SSE 通知文案标注"空跑"。
4. 无论成功失败，完成日志与 SSE 文案里**必须**带上 `completed/failed/rows` 计数（新增行数计数器：在 `_run_incremental` 累加 `n` 到 `_sync.rows_written`，`_start_sync_bg`/`trigger_sync` 重置）。
5. 不得改变"全部标的都已最新（done==total, rows=0）"仍记 SUCCESS 的既有语义。

回归：`backend/tests/` 下既有 sync 用例必须全绿。

## §2 修复 B — 增量特征构建不回填"最新日"（P0）

文件：`backend/app/orchestrator.py` :: `step_build_features`（≈L79-182）

现状：
```python
d_last   = existing["date"].max()
new_rows = feats_new.filter(pl.col("date") > d_last)   # 严格大于 → 最新日永久残缺
```
一旦某交易日被部分写入，`d_last` 锁死该日，之后**再补数据也永不回填**（09-14 停在 1 行即此机制）。

要求：
1. 改为"最新日可补齐"的合并：
   - `tail_new = feats_new.filter(pl.col("date") >= d_last)`
   - `head_old = existing.filter(pl.col("date") < d_last)`（历史段**原样保留**，维持 asof 稳定性）
   - `merged = concat([head_old, tail_new]).unique(subset=["date","symbol"], keep="last").sort(...)`
2. 一致性守卫（L149-167）同步放宽：高度/逐值比对只在 `guard_start <= date < d_last` 区间进行；对 `date == d_last` 这一"前沿日"**允许增行**，但必须断言**不减行**（`tail_new.height >= old_tail.height`），否则视为数据损坏 → 抛错走 `_full()` 全量兜底。
3. 保留"增量校验失败自动回退全量"的既有行为与 `mode=...` 返回文案（文案需反映新逻辑，例如 `incremental(warm_start=..., frontier=YYYY-MM-DD, filled=N)`）。

回归：`backend/tests/test_feature_incremental.py`（两段式投喂 == 一次性全量）必须继续通过，并**新增**用例：
- 先落"前沿日仅 1 行"的特征，再对同一前沿日补入更多 symbol 的行情，重跑 `step_build_features` → 前沿日行数增至完整，且 `date < d_last` 段逐值不变。

## §3 修复 C — manifest 不自愈导致 771 只被静默排除（P0）

文件：`backend/app/data/parquet_store.py` :: `list_symbols_with_data`（≈L144-158）

现状：`daily_bar_hfq` 磁盘 2500 只（无空目录，全部 rows>0），`.manifest.json` 只登记 **1729** 只 → `read_all_symbols("daily_bar_hfq")` 恒返 1729。铁证：`features` 09-11 行数 = **1729**。manifest 只在写路径增量登记，且仅在 dataset 键**缺失**时才全量重扫（`_manifest_scan_dataset`），故一旦键存在就永不修复。已知遗留：`docs/software-company/test-report-sprint3.md` L41 记录 `repair_data.py` 旁路直写未接 `manifest_invalidate`。

要求：
1. `list_symbols_with_data` 增加**一致性自愈**：
   - 取 manifest 结果后，廉价统计磁盘 `symbol=` 目录数 `N_dir`（一次 `iterdir()`）；记 `N_man = len(ds)`。
   - 若 `N_man < N_dir` → 触发一次 `_manifest_scan_dataset(dataset)` 重扫并 `_manifest_flush_locked(force=True)`，同时 `logger.warning` 明确打印 `manifest 条目=N_man 磁盘目录=N_dir，已强制重扫`。
   - **每个 dataset 每进程最多重扫一次**（模块级 `_audited: set[str]`）：重扫后若仍 `N_man < N_dir`，说明差异来自合法空目录，记入 `_audited` 不再重扫、也不再刷 warning。
   - 空目录语义必须保持：`skip_empty=True` 仍过滤 `rows<=0`。
2. 旁路写入点接线 `manifest_invalidate()`：审计 `scripts/*.py`、`app/data/repair.py` 及其他直接写 `DATA_ROOT/<dataset>/` 的路径；已接线的（如 `expand_universe_2500.py`）确认**最后一次写之后**仍有失效调用（注意 L815 `atomic_write_parquet` 的位置）。仅补必要的失效调用，不重构脚本。
3. 不得引入新的网络/重 IO 启动开销：重扫只在检测到不一致时发生，最多一次。

回归：新增用例（临时 DATA_ROOT）：
- 手工写一个只含 1 个 symbol 的 `daily_bar` manifest，再在磁盘上多造 1 个含数据的 symbol 目录 → `list_symbols_with_data("daily_bar")` 返回 2 只、manifest 文件被更新、warning 被记录；同进程再次调用**不再**重扫（用计数器/monkeypatch 断言）。
- 空目录（rows=0）不被 `skip_empty=True` 返回。

---

## §4 项目红线（必须遵守）

- pytest **必须 `cd backend` 再跑**；venv = `backend/.venv/Scripts/python.exe`。
- 跑测试前必须解除沙箱守卫变量，否则出现假失败：
  ```bash
  cd backend && env -u CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR -u CODEBUDDY_SAFE_DELETE_BULK_GUARD \
    -u CODEBUDDY_TOOL_CALL_ID .venv/Scripts/python.exe -m pytest -m "not network" -q
  ```
- 测试用临时 `DATA_ROOT`，**严禁**污染真实 `data/`；写共享 DATA_ROOT 的用例须自清分区、dtype 对齐。
- 编辑后回读；禁止对同一文件并行 Edit。
- **不改动 `data/` 下任何真实数据文件**；不跑联网抓取。
- 新增/修改代码须过 `ruff` + `mypy`（CI 只查 `app/`）。
- 不得引入新的静默降级：任何重扫、断言、兜底都要有 WARNING 或落库痕迹。

## §5 交付物

1. 三处代码修改（含必要的单元测试）
2. 代码摘要：改动文件清单 + 每处 before/after 关键片段 + 影响面说明
3. 自测结果：新增用例 + 相关既有用例的通过情况
4. 全局一致性审查结论（`IS_PASS: YES/NO`）
