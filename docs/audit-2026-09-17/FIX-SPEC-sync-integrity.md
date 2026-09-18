# FIX-SPEC：同步完整性缺口（2026-09-17）

## §0 背景与证据

在回补生产 `/screener` 空榜的根因 A（数据未物化）过程中，顺带审计发现 **2 处真实代码缺陷**。
另 3 处产物过期（qfq / universe_daily / cs 镜像）是缺陷 2 的**后果**，不单独改代码。

实测证据（全部来自本仓库当前真实数据）：

| 证据 | 内容 |
|---|---|
| 缺陷 1 正向 | `300001.SZ`：`daily_bar` 最后 2026-09-15，`daily_bar_hfq` 仅到 **2026-09-04**（差 11 天） |
| 缺陷 1 反向 | `000631.SZ` / `000632.SZ`：`daily_bar_hfq` 到 09-17，`daily_bar` 仅到 09-11 ⇒ 两数据集可**双向漂移** |
| 缺陷 2 后果 A | `daily_bar_qfq`：1729 只停 09-11、720 只停 09-04，**无一只到 09-14+**；仅 2458 只有 2026 数据（raw/hfq 为 2499/2500） |
| 缺陷 2 后果 B | `universe_daily` 最后 2026-09-11（`/screener` 股票池来源） |
| 缺陷 2 后果 C | `cs/daily_bar`、`cs/daily_bar_qfq` 最后 2026-09-11（`/screener/stocks` 全市场截面源，且带"有效截面日回退"⇒ 会静默用旧截面冒充当前） |

---

## §1 缺陷 1（P1）：增量同步的跳过判据**只读 raw**，hfq 缺口被永久跳过

**位置**：`backend/app/services/sync_service.py`

- `_symbol_last_date(sym, ref_year)`（L186）只读 `read_symbol_year("daily_bar", sym, year)`
- `_run_incremental`（L240-245）：

```python
last = _symbol_last_date(sym, target.year)
if last is not None and last >= target:
    with _sync.lock:
        _sync.completed.add(sym)
        _sync.done = i + skipped
    continue        # ← 整个标的被跳过，不再抓取任何口径
```

**危害**：raw 一旦 `>= target`，该标的被永久判为"已完成"。若其 hfq 落后（如 09-04），
**hfq 永远不会被回补**；而 hfq 是 `step_build_features` 的**唯一输入** ⇒ 静默固化坏样本，
且同步全程显示"成功"。反向情形（hfq 领先 raw）同样存在 ⇒ **"只看一侧"这个判据本身不成立**。

这是"同步假成功"的**第二种形态**：之前的缺陷 D 修的是"空跑记成功"，本项修"**漏跑记成功**"。

**要求**：

1. 跳过判据必须**同时**满足两个数据集都 `>= target`（`daily_bar` **且** `daily_bar_hfq`）；
   任一落后即必须进入抓取流程。
2. 实现方式建议：新增 `_symbol_last_date_in(dataset, sym, ref_year)`（或给现有函数加 `dataset` 形参），
   但**必须保持 `_symbol_last_date(sym, ref_year)` 的 2 参调用可用**，因为：
   - `app/api/v1/datacenter.py:70` 重导出了它；
   - `tests/test_resume_failed_semantics.py:38,100` 以 `lambda sym, year: None` patch 它。
3. **可观测性**：若因 raw 已最新但对侧落后而仍需抓取，必须留下 WARNING（含 symbol 与两侧日期），
   不得静默。反之，若因两侧都已最新而跳过，保持静默即可。
4. 不得改动 `_run_repair` / `_run_rebuild` 的既有语义（`_run_repair` 本来就不做该跳过判断）。

---

## §2 缺陷 2（P1）：手动重跑入口与晚间例行**步骤集不一致**

**证据链**：

| 入口 | 位置 | 传入的步骤 |
|---|---|---|
| 晚间例行 | `app/jobs/evening_routine.py:71` | `["rebuild_qfq","build_universe","build_features","infer","screener_dump","build_cs_mirror"]` |
| 手动重跑 | `app/api/v1/ops.py:437-447`（`POST /ops/dag/rerun`） | **不传 `steps`** → `steps = steps or STEPS`（`orchestrator.py:396`）→ 默认 `STEPS` |
| 默认集 | `app/orchestrator.py:330` | `["update_daily","validate","build_features","infer","screener_dump"]` |

⇒ 默认集**缺 `rebuild_qfq` / `build_universe` / `build_cs_mirror`**。

`POST /ops/dag/rerun` 是前端"重跑"按钮，**文案写着"真实重跑每日流水线"**，实际却跑子集。
运维在事故后点重跑，qfq / universe / 截面镜像**不会被重建，且没有任何提示** ⇒ §0 中三处过期无法通过 UI 自救。

**要求**：

1. 消除两个入口的步骤集漂移：定义**单一事实源**常量（如 `FULL_STEPS` / `EVENING_STEPS`），
   `orchestrator.STEPS`、`evening_routine`、手动入口全部从它派生，**禁止再各自硬编码字面量列表**。
2. 手动入口必须覆盖 `rebuild_qfq`、`build_universe`、`build_cs_mirror`。
3. 顺序必须符合 `orchestrator.py` 头部 docstring 的约定：
   `update_daily → validate → rebuild_qfq → build_universe → build_features → infer → screener_dump → build_cs_mirror`。
4. 若确实要保留"精简集"语义，必须由调用方**显式**选择，且 `/ops/dag/rerun` 的返回值/前端文案
   必须与实际执行的步骤一致 —— 不得再宣称"重跑每日流水线"却只跑子集。
5. `STEPS` 还被 `python -m app.orchestrator` CLI 使用；变更后 CLI 行为随之改变，需在 docstring 说明。

---

## §3 测试要求（含变异反证）

1. **缺陷 1**：
   - 新增用例覆盖 **"raw 已最新但 hfq 落后 ⇒ 该标的仍必须被处理"**；
   - 与 `_run_incremental` 现有 patch 风格对齐（参考 `tests/test_resume_failed_semantics.py` 的 `isolated_sync` fixture）；
   - **变异反证**：把判据改回"只看 raw"，该用例必须变红。
2. **缺陷 2**：
   - 新增用例断言 `run_pipeline` 的默认步骤集 **⊇** `{rebuild_qfq, build_universe, build_cs_mirror}`；
   - 且断言 `dag_rerun` 与 `evening_routine` 使用的步骤集**一致**（同一常量或显式相等）；
   - **变异反证**：把常量改回子集，用例必须变红。
3. `tests/test_resume_failed_semantics.py` 中 patch `_symbol_last_date` 的两处必须保持可用（或同步更新）。
4. 回归：`tests/test_resume_failed_semantics.py`、`tests/test_pipeline.py`、`tests/test_p1_data.py`、
   `tests/test_write_endpoints_smoke.py` 必须全绿。

---

## §4 新增缺陷（实施中实测发现）：step_validate 对全市场必然失败

**位置**：`backend/app/data/pipeline.py` `step_validate`（原 L54-71）。

### 实测证据（只读探针，按 step_validate 同一读取路径 `read_symbol_dataset("daily_bar", sym, start=d, end=d)` 扫全市场）

```
[2026-09-11] total=2499 ok=2147 empty=352
[2026-09-12] total=2499 ok=0    empty=2499
```

### 根因

旧实现只要任一 code 当日无数据即 `raise ValueError("... 当日无数据 ...")`。而 A 股全市场**每天必有停牌/退市标的**（09-11 实测 352/2499），故 `validate` 只要以全市场 codes 直跑（晚间例行 / `ops.dag_rerun` / CLI 默认）**必然 FAILED**，其后所有步骤（rebuild_qfq / build_features / infer / screener_dump …）一律不执行 ⇒ 榜单静默停更——正是本次审计要根除的「静默失败」形态。此前 `step_validate` 在 `tests/` 下**零覆盖**，该行为从未被约束。

### 新语义（保持 fail-fast 契约）

1. `prev = prev_trade_day(trade_date, get_calendar())`；日历不可用 → `prev=None`、**降级为「当日覆盖率」判据（不是直接放行）**：`coverage = n_ok / max(1, len(codes))`，若 `coverage < VALIDATE_MIN_COVERAGE_FALLBACK`（=0.5）⇒ raise「疑似整日/大面积丢失」；未触发则照常返回、记 WARNING，返回串标记 `degraded=1`。
2. 当日有数据 → 仍走 `validate_daily_bar`，坏 bar 记 `quality_errs`（**始终致命**，严格性不放松）。
3. 当日无数据 → **仅此时**读 `prev` 日那一行：
   - `prev` 有 → `missing_errs`（可疑「昨有今无」）；
   - `prev` 也无 → 停牌 / 未上市 / 退市 → 跳过，只计 `n_suspended`。
4. **容差阈值（纯比例，不用绝对下限）**：仅当 `len(missing_errs) > VALIDATE_MISSING_TOLERANCE_RATIO * n_expected` 时才致命；阈值内只 WARNING。
   - `VALIDATE_MISSING_TOLERANCE_RATIO = 0.05`
   - `n_expected = 当日有数据只数 + len(missing_errs)`
   - ⚠️ **不得使用绝对下限**（原 `VALIDATE_MISSING_TOLERANCE_ABS = 50` 已删除）：小代码集（手动 `dag_rerun` / CLI 默认仅 3 只）的 `n_expected` 只有个位数，绝对下限会恒 ≥ `n_expected` ⇒ 连 **100% 丢失**也被静默放行（相对旧「任一缺失即 raise」是退化）。纯比例下 `missing == n_expected` 时 ratio=1.0 > 阈值 ⇒ **任何规模的 100% 丢失恒致命**；市场级 `n_expected≈2167` 时阈值≈108，可容纳日常 ~20 只临时停牌。
   - 必要性：A 股每天都有少量「昨日正常、今日临时停牌」的标的（完全正常）；若无容差，晚间例行几乎每晚都会被误判失败。
5. 返回串：`validated={n_ok}/{len(codes)} suspended={n_suspended} missing_vs_prev={len(missing_errs)} degraded={0|1}`；raise 消息带总计数 + 沿用 `[:5]` 截断样例。

### 连带收敛：晚间例行改为只剔除 update_daily

`validate` 修好后即可全市场直跑，且它是**唯一能抓住「根因 A 再次发生」的门禁**（09-14 正是「昨有今无」：标的 09-11 有数据、09-14 无；若晚间例行带此步即 FAILED 告警，而非静默用陈旧数据产出榜单）。故：

- `orchestrator._EVENING_EXCLUDED` 由 `("update_daily", "validate")` 收敛为 `("update_daily",)`；`EVENING_STEPS` = `FULL_STEPS` **有序剔除 1 步**（7 步，仍从 `FULL_STEPS` 派生，不漂移）。
- `ops.dag_rerun` 继续用 `FULL_STEPS` 全量。

### 测试

`backend/tests/test_validate_step.py`（10 例，monkeypatch `read_symbol_dataset`，不写 parquet）：停牌容忍 / 整日丢失 raise / 市场级少量新停牌容差内放行 + WARNING / 坏 bar 仍致命 / 日历不可用降级（`degraded=1`）/ 小代码集 100% 与单只丢失均致命 / 小代码集全停牌放行 / 日历不可用 + 整日丢失致命 / 日历不可用 + 覆盖率≥50% 降级。含多项变异反证。

### 连带订正：脚本入口静默空跑（同批清理）

`__main__` 入口在 `app/orchestrator.py`，`app/data/pipeline.py` **没有** ⇒ 旧脚本 `python -m app.data.pipeline` 以退出码 0 静默空跑（什么也不做）：

- `scripts/daily_pipeline.sh` / `scripts/daily_pipeline.ps1`：`-m app.data.pipeline` → `-m app.orchestrator`。
- `scripts/rebuild_derivatives.py`：`from app.data.pipeline import STEP_FUNCTIONS` → `from app.orchestrator import STEP_FUNCTIONS`（后者未定义于 pipeline 模块，原为 ImportError）。
- `run.md`：`python -m app.data.pipeline` → `python -m app.orchestrator`。

---

## §5 红线（违反即返工）

- **只改代码**：不跑联网回补、**不动 `data/` 下任何数据文件**。（生产数据回补由主理人另行处理，正在后台执行。）
- pytest **必须 `cd backend`**；必须 `env -u CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR -u CODEBUDDY_SAFE_DELETE_BULK_GUARD -u CODEBUDDY_TOOL_CALL_ID`，
  否则会多出 4–8 个 `SystemExit:1` **伪失败**；只允许用 `backend/.venv/Scripts/python.exe`。
- **禁止** `git stash` / `git checkout -- <file>` / `git reset --hard` / `git clean` / commit / push
  —— 本 git 仓库被外部进程破坏过 **4 次**，当前工作区是**唯一副本**。
- 每条 bash 命令开头：`export PATH="/usr/bin:/bin:/c/Windows/System32:$PATH"`。
- 若新增写端点，须同步 `tests/test_write_endpoints_smoke.py` 的计数断言（当前 **52**）。
- 编辑后回读；**禁止同文件并行 Edit**。
