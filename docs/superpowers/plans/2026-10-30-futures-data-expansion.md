# 期货品种分层扩容与全历史数据治理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将期货默认研究池从 14 个扩展到 40 个核心品种，登记 43 个扩展品种，按数据源实际首日保存全历史，并确保行情与分析接口不再生成或隐藏虚构数据。

**Architecture:** 使用一个纯领域品种目录统一 83 个品种元数据和 40 个交易规格；使用带 manifest 的本地 parquet 缓存管理真实首末日与质量状态；使用可测试的同步服务原子更新缓存；API 和前端从目录接口读取品种与可用状态，核心池默认启用，扩展池默认只读禁用。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、Polars、Pandas、AkShare、pytest、React 18、TypeScript、Vitest、SWR-compatible fetch hook。

**Spec:** `docs/superpowers/specs/2026-10-30-futures-data-expansion-design.md`

## Global Constraints

- 历史范围从每个品种的 `source_first_date` 开始，直到最新有效交易日。
- 不把 2008-01-01 或 `listing_date` 作为统一数据截断点。
- `effective_start` 默认等于 `source_first_date`；使用 `--start-date` 时必须等于实际缓存首日。
- 目录固定为 40 个核心品种、43 个扩展品种、总计 83 个。
- 只有核心 40 个品种有完整交易规格并默认启用。
- 43 个扩展品种默认 `trade_enabled=false`，不得进入回测、套保或手续费计算。
- 历史行情、行情快照、COT、基差、期限结构、展期收益不得用随机值、固定模板或 IF 数据兜底。
- 未知代码必须报错，禁止回退到 `IF`。
- 所有缓存与 manifest 写入必须原子替换，清理前备份。
- 保留现有用户修改，不回退 `backend/app/data/text_ingest.py` 和 `backend/app/orchestrator.py`。
- 现有质量工具 `backend/app/data/futures_quality.py` 与测试 `backend/tests/test_futures_quality.py` 必须保留并扩展。

---

## Review Focus

1. 数据源无历史且缓存为空时，所有历史入口必须明确失败，绝不返回合成行情。
2. 未知代码、已登记但未启用代码、已同步但质量失败的代码必须有不同状态，不能都回退到 IF。
3. `source_first_date`、`cache_first_date`、`effective_start` 在默认同步和人工截断两种情况下必须一致。
4. 合约乘数、最小跳价、保证金与开仓/平昨/平今手续费必须来自已启用规格，不能让扩展品种套用核心品种费用。
5. 前端必须在目录、实时快照、K 线、COT、基差、期限结构和展期收益不可用时显示明确状态。

---

### Task 1: 建立统一期货目录与交易规格

**Files:**
- Create: `backend/app/domain/futures_catalog.py`
- Modify: `backend/app/domain/futures_rules.py`
- Create: `backend/tests/test_futures_catalog.py`
- Modify: `backend/tests/test_futures.py`

**Interfaces:**
- Produces:
  - `FeeRule(fee_type: Literal["rate", "fixed"], fee_unit: float, source_note: str)`
  - `FuturesInstrument(code: str, name: str, exchange: str, category: str, sina_symbol: str, listing_date: date | None, tier: Literal["core", "extended"], status: Literal["active", "retired", "pending"], default_enabled: bool)`
  - `FuturesContractSpec(symbol_prefix, name, exchange, category, multiplier, tick_size, margin_rate, fee_open, fee_close, fee_close_today, spec_as_of, underlying_code=None)`
  - `FUTURES_CATALOG: dict[str, FuturesInstrument]`
  - `FUTURES_CORE: tuple[str, ...]`
  - `FUTURES_EXTENDED: tuple[str, ...]`
  - `FUTURES_SPECS: dict[str, FuturesContractSpec]`
  - `get_futures_instrument(symbol: str) -> FuturesInstrument | None`
- Compatibility:
  - `FuturesContractSpec.fee_type` returns `fee_open.fee_type`.
  - `FuturesContractSpec.fee_unit` returns `fee_open.fee_unit`.
  - `app.domain.futures_rules` continues to export the catalog classes and `FUTURES_SPECS`.

- [ ] **Step 1: Write the failing catalog tests**

```python
from datetime import date

from app.domain.futures_catalog import (
    FUTURES_CATALOG,
    FUTURES_CORE,
    FUTURES_EXTENDED,
    FUTURES_SPECS,
    get_futures_instrument,
)


def test_catalog_counts_and_disjoint_tiers() -> None:
    assert len(FUTURES_CATALOG) == 83
    assert len(FUTURES_CORE) == 40
    assert len(FUTURES_EXTENDED) == 43
    assert set(FUTURES_CORE).isdisjoint(FUTURES_EXTENDED)
    assert set(FUTURES_CATALOG) == set(FUTURES_CORE) | set(FUTURES_EXTENDED)


def test_core_specs_exist_and_extended_specs_do_not() -> None:
    assert set(FUTURES_SPECS) == set(FUTURES_CORE)
    assert FUTURES_SPECS["CF"].multiplier == 5.0
    assert FUTURES_SPECS["CF"].category == "agri"
    assert FUTURES_SPECS["TS"].category == "bond"
    assert "PB" not in FUTURES_SPECS
    assert FUTURES_CATALOG["PB"].tier == "extended"


def test_t0_is_registered_even_though_sina_display_omits_it() -> None:
    instrument = get_futures_instrument("T0")
    assert instrument is not None
    assert instrument.code == "T"
    assert instrument.sina_symbol == "T0"


def test_core_listing_dates_are_explicit() -> None:
    assert all(FUTURES_CATALOG[code].listing_date is not None for code in FUTURES_CORE)
    assert FUTURES_CATALOG["CF"].listing_date == date(2004, 6, 1)
```

- [ ] **Step 2: Run the catalog tests and verify they fail**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_catalog.py -q
```

Expected: FAIL because `app.domain.futures_catalog` does not exist.

- [ ] **Step 3: Implement the catalog**

Use Python constants, not runtime JSON, so the catalog is type checked and versioned with code.

Core codes:

```text
IF IC IM IH T TF TS AU AG CU AL ZN NI SN RB I J JM HC
SC TA MA FG SA PP L V RU BU CF SR M Y P A C OI RM AP JD
```

Extended codes:

```text
PB SS AO BC AD SF SM UR EG EB PG PF PX LU NR SP BR EC PR PL SH
CY CJ LH CS PK B FU FB RR LG BZ RS WH JR RI LR LC SI PS PT PD OP
```

Populate all core `FuturesContractSpec` values from exchange contract terms as of `2026-10-30`. Add `source_note` to every `FeeRule`. Core records must have:

```python
default_enabled=True
status="active"
```

Extended records must have:

```python
default_enabled=False
tier="extended"
```

Use:

```python
def get_futures_instrument(symbol: str) -> FuturesInstrument | None:
    prefix = "".join(ch for ch in symbol.strip().upper() if ch.isalpha())
    return FUTURES_CATALOG.get(prefix)
```

- [ ] **Step 4: Migrate `futures_rules.py` without breaking imports**

Replace the local dataclass and 14-entry dict with:

```python
from .futures_catalog import (
    FeeRule,
    FuturesContractSpec,
    FuturesInstrument,
    FUTURES_CATALOG,
    FUTURES_CORE,
    FUTURES_EXTENDED,
    FUTURES_SPECS,
    get_futures_instrument,
)
```

Update `get_futures_spec` to return only enabled core specs:

```python
def get_futures_spec(symbol: str) -> FuturesContractSpec | None:
    prefix = "".join(ch for ch in symbol.strip().upper() if ch.isalpha())
    return FUTURES_SPECS.get(prefix)
```

- [ ] **Step 5: Add open/close/close-today fee selection**

Extend `calc_futures_cost` with optional keyword arguments:

```python
fee_open: FeeRule | None = None,
fee_close: FeeRule | None = None,
fee_close_today: FeeRule | None = None,
```

Selection order:

```text
is_close and is_today -> fee_close_today
is_close -> fee_close
otherwise -> fee_open
```

When all three are `None`, preserve the current `fee_type`/`fee_unit` behavior for compatibility.

Add tests asserting IF open and close-today rates remain `0.000023` and `0.000345`.

- [ ] **Step 6: Run catalog and rules tests**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_catalog.py tests/test_futures.py::test_futures_specs_and_rules tests/test_futures.py::test_slippage_and_intraday_penalty_fee -q
```

Expected: PASS.

- [ ] **Step 7: Commit checkpoint**

```powershell
git add backend/app/domain/futures_catalog.py backend/app/domain/futures_rules.py backend/tests/test_futures_catalog.py backend/tests/test_futures.py
git commit -m "feat: add tiered futures instrument catalog"
```

---

### Task 2: 实现 manifest、同步服务与同步脚本

**Files:**
- Create: `backend/app/data/futures_store.py`
- Create: `backend/app/data/futures_sync.py`
- Create: `backend/scripts/sync_futures_history.py`
- Create: `backend/tests/test_futures_store.py`
- Create: `backend/tests/test_futures_sync.py`
- Modify: `backend/app/data/futures_quality.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class FuturesCacheMeta:
    symbol: str
    source: str
    source_symbol: str
    fetched_at: str
    listing_date: str | None
    raw_source_first_date: str | None
    source_first_date: str | None
    source_last_date: str | None
    cache_first_date: str | None
    effective_start: str | None
    requested_start_date: str | None
    rows: int
    tier: str
    quality_status: str
    trade_enabled: bool
    issue_counts: dict[str, int]
    warning_counts: dict[str, int]
    checksum: str


@dataclass(frozen=True)
class FuturesSyncResult:
    symbol: str
    success: bool
    wrote_cache: bool
    quality_status: str
    rows_before: int
    rows_after: int
    message: str


def normalize_futures_frame(frame: pd.DataFrame) -> pl.DataFrame: ...
def read_futures_manifest(cache_dir: Path) -> dict[str, FuturesCacheMeta]: ...
def write_futures_manifest(cache_dir: Path, entries: Mapping[str, FuturesCacheMeta]) -> Path: ...
def futures_trade_status(symbol: str, cache_dir: Path) -> tuple[bool, str]: ...
def sync_futures_symbol(
    symbol: str,
    *,
    cache_dir: Path,
    requested_start_date: date | None = None,
    force: bool = False,
    fetch_fn: Callable[[str], pd.DataFrame] | None = None,
) -> FuturesSyncResult: ...
def sync_futures_universe(
    symbols: Iterable[str],
    *,
    cache_dir: Path,
    requested_start_date: date | None = None,
    force: bool = False,
    max_workers: int = 4,
    fetch_fn: Callable[[str], pd.DataFrame] | None = None,
) -> list[FuturesSyncResult]: ...
```

- [ ] **Step 1: Write failing store tests**

Cover:

```python
def test_normalize_futures_frame_maps_sina_columns() -> None:
    ...


def test_manifest_round_trip_is_atomic(tmp_path: Path) -> None:
    ...


def test_manifest_tolerates_corrupt_json(tmp_path: Path) -> None:
    ...
```

Expected normalization columns:

```text
date, open, high, low, close, volume, open_interest
```

- [ ] **Step 2: Run store tests and verify they fail**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_store.py -q
```

Expected: FAIL with import errors.

- [ ] **Step 3: Implement `futures_store.py`**

Requirements:

- Rename AkShare Chinese columns exactly as the current `get_futures_kline` does.
- Cast `date` to ISO string and numeric columns to `Float64`.
- Sort ascending and remove duplicate dates by retaining the last row only during normalization; the quality audit remains authoritative for quarantine.
- Serialize manifest deterministically with `ensure_ascii=False`, sorted symbols and sorted keys.
- Write `manifest.json` to a same-directory temporary file and `Path.replace()` the destination.
- `read_futures_manifest` returns `{}` for a missing or corrupt file and never raises solely because manifest is absent.
- Compute `checksum` as SHA-256 of the parquet file bytes.
- `futures_trade_status` returns `(True, "ok")` only when the code is a core instrument, has a spec, and its manifest entry has `quality_status="ok"` and `trade_enabled=true`; every other case returns `(False, reason)`.

- [ ] **Step 4: Write failing sync tests**

Use a fake `fetch_fn` that returns one of two frames:

```python
raw = pd.DataFrame(
    {
        "日期": ["2005-01-04", "2005-01-05"],
        "开盘价": [100.0, 101.0],
        "最高价": [102.0, 103.0],
        "最低价": [99.0, 100.0],
        "收盘价": [101.0, 102.0],
        "成交量": [1000, 1100],
        "持仓量": [2000, 2100],
    }
)
```

Assertions:

```python
result = sync_futures_symbol("CF", cache_dir=cache, fetch_fn=lambda _: raw)
meta = read_futures_manifest(cache)["CF"]
assert meta.source_first_date == "2005-01-04"
assert meta.cache_first_date == "2005-01-04"
assert meta.effective_start == "2005-01-04"
assert meta.quality_status == "failed"
assert meta.trade_enabled is False


result = sync_futures_symbol(
    "CF",
    cache_dir=cache,
    requested_start_date=date(2005, 1, 5),
    force=True,
    fetch_fn=lambda _: raw,
)
meta = read_futures_manifest(cache)["CF"]
assert meta.source_first_date == "2005-01-04"
assert meta.cache_first_date == "2005-01-05"
assert meta.requested_start_date == "2005-01-05"
assert meta.effective_start == "2005-01-05"
```

Add a second fixture with 260 recent valid rows and assert:

```python
assert recent_meta.quality_status == "ok"
assert recent_meta.trade_enabled is True
assert futures_trade_status("CF", cache) == (True, "ok")
assert futures_trade_status("PB", cache) == (False, "品种属于扩展池，默认未启用")
```

Also assert fetch failure preserves identical existing parquet bytes and existing manifest.

- [ ] **Step 5: Implement `futures_sync.py`**

Rules:

- Fetch raw data first and record `raw_source_first_date` and `source_first_date` before cleaning.
- Apply optional `requested_start_date` after normalization.
- Call `audit_futures_frame(clean_frame, symbol)`.
- Do not replace the cache when the cleaned frame is empty or required columns cannot be normalized.
- Replace the cache when data is structurally clean; quality-gate failures still write `quality_status="failed"` and `trade_enabled=false`.
- Core quality status is `ok` only when the cleaned frame has at least 250 rows, no structural issue counts, latest date within 10 natural days, trailing-three-year zero-volume ratio <= 0.05 and median volume >= 10_000.
- Extended quality may be `warning`, but `trade_enabled` remains false in this release.
- Retry fetching three times with 1.2-second minimum spacing.

- [ ] **Step 6: Implement the CLI**

`backend/scripts/sync_futures_history.py` arguments:

```text
--tier core|extended|all
--symbols CF,SR,M
--start-date YYYY-MM-DD
--force
--apply
--max-workers 4
```

Behavior:

- Without `--apply`, fetch and report but do not write cache or manifest.
- Default symbols are `FUTURES_CORE` for `--tier core`.
- `--symbols` overrides `--tier` but rejects unknown codes.
- At least one of `--tier` or `--symbols` is required.
- Exit code is `1` when any requested core symbol has `quality_status != "ok"`.

- [ ] **Step 7: Run store and sync tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_store.py tests/test_futures_sync.py tests/test_futures_quality.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit checkpoint**

```powershell
git add backend/app/data/futures_store.py backend/app/data/futures_sync.py backend/app/data/futures_quality.py backend/scripts/sync_futures_history.py backend/tests/test_futures_store.py backend/tests/test_futures_sync.py
git commit -m "feat: add manifest-backed futures history sync"
```

---

### Task 3: 重写历史读取路径并移除合成行情

**Files:**
- Modify: `backend/app/data/futures.py`
- Modify: `backend/app/domain/futures_rules.py`
- Modify: `backend/tests/test_futures_atomic_cache.py`
- Modify: `backend/tests/test_futures.py`
- Create: `backend/tests/test_futures_history_contract.py`

**Interfaces:**

```python
def get_futures_kline(
    symbol_prefix: str = "IF",
    days: int | None = 500,
    continuous: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]: ...
```

Behavior:

- `days=None` returns the full cached history.
- Explicit `start_date` and `end_date` are inclusive.
- Unknown code raises `AQPException(ERR_NOT_FOUND, ...)`.
- Known code with no cache and failed refresh raises `AQPException(ERR_DATA_EMPTY, ...)`.
- Stale cache may be returned if refresh fails.
- `continuous=True` adds `roll_detection="heuristic"` to every bar.

- [ ] **Step 1: Write failing history contract tests**

```python
def test_unknown_futures_symbol_does_not_fall_back_to_if(monkeypatch, tmp_path):
    ...
    with pytest.raises(AQPException) as exc:
        get_futures_kline("ZZZZ")
    assert exc.value.code == ERR_NOT_FOUND


def test_empty_cache_and_failed_fetch_never_returns_synthetic(monkeypatch, tmp_path):
    ...
    monkeypatch.setattr(futures_sync, "sync_futures_symbol", failing_fetch)
    with pytest.raises(AQPException) as exc:
        get_futures_kline("CF")
    assert exc.value.code == ERR_DATA_EMPTY


def test_days_none_returns_full_source_history(monkeypatch, tmp_path):
    ...
    bars = get_futures_kline("CF", days=None)
    assert bars[0]["date"] == "2005-01-04"
    assert bars[-1]["date"] == "2026-10-30"


def test_explicit_dates_are_inclusive(monkeypatch, tmp_path):
    ...
    bars = get_futures_kline("CF", days=None, start_date="2005-01-05", end_date="2005-01-06")
    assert [bar["date"] for bar in bars] == ["2005-01-05", "2005-01-06"]
```

- [ ] **Step 2: Run new tests and verify they fail**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_history_contract.py -q
```

Expected: FAIL because synthetic fallback still exists and date parameters are absent.

- [ ] **Step 3: Replace the cache/read implementation**

Delete the GBM/Merton fallback block entirely.

Read order:

```text
manifest + parquet cache
-> single-symbol sync when stale or missing
-> stale real cache if refresh fails
-> ERR_DATA_EMPTY if neither exists
```

Call `sync_futures_symbol` with `requested_start_date=None`; do not silently truncate on reads.

- [ ] **Step 4: Update backtest and dependent functions**

In `run_futures_cta_backtest`:

- Resolve spec with `get_futures_spec(prefix)`.
- If `None`, return `{"status": "error", "message": "品种未启用或不存在"}`.
- Call `futures_trade_status(prefix, get_settings().DATA_ROOT / "futures_cache")`; if false, return `{"status": "error", "message": reason}`.
- Call `get_futures_kline(..., days=days, start_date=start_date, end_date=end_date, continuous=continuous)`.
- When both `start_date` and a future `history_days=None` are used, pass full history.

In `get_futures_instrument_profile`, `get_parameter_sensitivity`, and inter-commodity spread:

- Remove every `or FUTURES_SPECS["IF"]` fallback.
- Remove the empty-bars fallback to IF.
- Return an error/unavailable result when required data is absent.

- [ ] **Step 5: Mark heuristic continuous bars**

In `calc_continuous_contract`, add:

```python
"roll_detection": "heuristic"
```

to both the no-roll and adjusted output branches.

- [ ] **Step 6: Update atomic cache tests**

Keep these assertions:

```python
assert target.exists()
assert bars[0]["close"] == 1.5
assert target.read_bytes() == old_bytes
```

Replace old monkeypatch source shape with the new sync service interface or monkeypatch `sync_futures_symbol` to write a normalized cache through the production helper.

For `test_futures_cta_backtest_dual_ma` and `test_futures_hedge_backtest`, replace reliance on the developer machine cache with a fixture that:

```python
monkeypatch.setattr(futures, "get_futures_kline", lambda *args, **kwargs: fixture_bars)
monkeypatch.setattr(futures, "futures_trade_status", lambda *args: (True, "ok"))
```

Do not let unit tests pass merely because a populated `data/parquet/futures_cache` exists on the development machine.

- [ ] **Step 7: Run history and backtest tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_history_contract.py tests/test_futures_atomic_cache.py tests/test_futures.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit checkpoint**

```powershell
git add backend/app/data/futures.py backend/app/domain/futures_rules.py backend/tests/test_futures_history_contract.py backend/tests/test_futures_atomic_cache.py backend/tests/test_futures.py
git commit -m "fix: require real futures history and full date ranges"
```

---

### Task 4: 移除行情与分析接口中的虚构数据

**Files:**
- Modify: `backend/app/data/futures.py`
- Modify: `backend/app/api/v1/watchlist.py`
- Modify: `backend/tests/test_futures.py`
- Modify: `backend/tests/test_watchlist.py`
- Create: `backend/tests/test_futures_real_data_only.py`

**Interfaces:**

Add a shared payload helper in `futures.py`:

```python
def unavailable_futures_payload(
    surface: str,
    *,
    symbol: str | None = None,
    reason: str,
) -> dict[str, Any]:
    return {
        "available": False,
        "surface": surface,
        "symbol": symbol,
        "reason": reason,
        "data_status": "unavailable",
        "modeled": False,
    }
```

- [ ] **Step 1: Write failing real-data-only tests**

```python
def test_snapshot_uses_cache_instead_of_hardcoded_quote(monkeypatch, tmp_path):
    ...
    snapshot = get_futures_market_snapshot()
    assert snapshot["items"][0]["source"] == "cache"
    assert snapshot["items"][0]["degraded"] is True


def test_term_structure_is_unavailable_without_contract_data(monkeypatch):
    ...
    result = get_futures_term_structure("CF")
    assert result["available"] is False
    assert result["modeled"] is False


def test_cot_is_unavailable_without_exchange_member_data(monkeypatch):
    ...
    result = get_futures_cot("CF")
    assert result["available"] is False


def test_basis_history_is_unavailable_without_real_spot_series(monkeypatch):
    ...
    result = get_basis_history("CF")
    assert result["available"] is False
```

Add equivalent tests for calendar spread, roll-yield matrix and watchlist futures valuation.

- [ ] **Step 2: Run real-data-only tests and verify they fail**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_real_data_only.py -q
```

Expected: FAIL because current implementations generate modeled or constant values.

- [ ] **Step 3: Replace synthetic market fallbacks**

`get_futures_market_snapshot`:

- First use `fetch_sina_futures_quotes`.
- For missing core quotes, read the last real cached bar with `get_futures_kline(days=1)`.
- Mark cache-derived quotes with `source="cache"`, `degraded=True`.
- Omit instruments with no real quote and no real cache from `items`.
- Set `basis`, `basis_rate` and `annualized_basis_rate` to `None` when no real spot is available.
- Never use `_FALLBACK_BASE_QUOTES`.

`_batch_valuation` in `watchlist.py`:

- Use `fetch_sina_futures_quotes` or the latest real cache bar.
- Do not import `_FALLBACK_BASE_QUOTES`.
- Omit futures with no real price.

- [ ] **Step 4: Disable unsupported modeled surfaces explicitly**

For each function below, when no real contract/spot/member input is available, return `unavailable_futures_payload(...)`:

```text
get_futures_term_structure
get_futures_calendar_spread
get_futures_cot
get_roll_yield_matrix
get_basis_history
```

Do not return old fixed-member, fixed-offset, fixed-spot or fallback-price payloads.

- [ ] **Step 5: Preserve genuine derived analytics**

Keep these surfaces when their inputs are real cached or live bars:

```text
inter-commodity spread
parameter sensitivity
instrument profile
CTA backtest
```

Add `"modeled": true` only for derived calculations and include the real input date range.

- [ ] **Step 6: Update existing API expectations**

Modify `backend/tests/test_futures.py` so unavailable surfaces assert:

```python
assert r.status_code == 200
assert r.json()["code"] == 0
assert r.json()["data"]["available"] is False
```

Do not assert fabricated COT counts, fixed term-structure length or fallback values.

- [ ] **Step 7: Run real-data-only and watchlist tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_real_data_only.py tests/test_futures.py tests/test_watchlist.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit checkpoint**

```powershell
git add backend/app/data/futures.py backend/app/api/v1/watchlist.py backend/tests/test_futures_real_data_only.py backend/tests/test_futures.py backend/tests/test_watchlist.py
git commit -m "fix: remove fabricated futures analytics fallbacks"
```

---

### Task 5: 增加品种目录 API、日期参数与启用状态校验

**Files:**
- Modify: `backend/app/api/v1/futures.py`
- Create: `backend/tests/test_futures_catalog_api.py`
- Modify: `backend/app/api/v1/watchlist.py`

**Interfaces:**

`GET /api/v1/futures/instruments` response:

```json
{
  "count": 40,
  "items": [
    {
      "code": "CF",
      "name": "棉花",
      "exchange": "CZCE",
      "category": "agri",
      "sina_symbol": "CF0",
      "listing_date": "2004-06-01",
      "tier": "core",
      "status": "active",
      "has_spec": true,
      "trade_enabled": true,
      "data_status": "ok",
      "quality_status": "ok",
      "source_first_date": "2005-01-04",
      "source_last_date": "2026-10-30",
      "effective_start": "2005-01-04",
      "issue_counts": {},
      "warning_counts": {}
    }
  ]
}
```

Query parameters:

```text
tier=core|extended|all
category=index|bond|precious|metal|ferrous|energy|agri
enabled=true|false
```

- [ ] **Step 1: Write failing API tests**

```python
def test_instruments_defaults_to_core_catalog(client, auth_headers):
    response = client.get("/api/v1/futures/instruments", headers=auth_headers)
    data = response.json()["data"]
    assert data["count"] == 40
    assert {item["tier"] for item in data["items"]} == {"core"}


def test_instruments_can_list_extended_but_not_enable_them(client, auth_headers):
    response = client.get("/api/v1/futures/instruments?tier=extended", headers=auth_headers)
    items = response.json()["data"]["items"]
    assert len(items) == 43
    assert all(item["trade_enabled"] is False for item in items)


def test_unknown_symbol_is_rejected(client, auth_headers):
    response = client.get("/api/v1/futures/kline?symbol=ZZZZ&days=20", headers=auth_headers)
    assert response.json()["code"] == ERR_NOT_FOUND


def test_extended_symbol_cannot_backtest(client, auth_headers):
    response = client.post(
        "/api/v1/futures/backtest",
        headers=auth_headers,
        json={"symbol": "PB", "strategy_type": "dual_ma"},
    )
    assert response.json()["code"] == ERR_PARAMS
```

- [ ] **Step 2: Run catalog API tests and verify they fail**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_catalog_api.py -q
```

Expected: FAIL because `/instruments` is absent.

- [ ] **Step 3: Implement `/instruments`**

Add:

```python
@router.get("/instruments")
async def get_instruments(
    tier: Literal["core", "extended", "all"] = "core",
    category: str | None = None,
    enabled: bool | None = None,
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    ...
```

Trade enablement:

```python
trade_enabled = (
    instrument.tier == "core"
    and instrument.default_enabled
    and get_futures_spec(code) is not None
    and manifest_entry.quality_status == "ok"
)
```

If manifest is absent, `quality_status="unknown"` and `data_status="not_synced"`.

- [ ] **Step 4: Add date parameters and symbol validation**

`GET /kline`:

```python
days: int | None = Query(180, ge=20, le=20_000)
start_date: str | None = Query(None)
end_date: str | None = Query(None)
```

Pass all four values to `get_futures_kline`.

`FuturesBacktestRequest.history_days` becomes:

```python
history_days: int | None = Field(default=500, ge=30, le=20_000)
```

Explicit `start_date`/`end_date` take precedence over `history_days`.

- [ ] **Step 5: Reject disabled instruments on trade endpoints**

Add one helper in `api/v1/futures.py`:

```python
def _require_enabled_spec(symbol: str) -> FuturesContractSpec:
    spec = get_futures_spec(symbol)
    if spec is None:
        raise AQPException(ERR_PARAMS, "品种不存在或尚未启用")
    enabled, reason = futures_trade_status(
        spec.symbol_prefix,
        get_settings().DATA_ROOT / "futures_cache",
    )
    if not enabled:
        raise AQPException(ERR_PARAMS, reason)
    return spec
```

Use it in:

```text
/hedge-calc
/backtest
/param-sensitivity
```

Remove all `or FUTURES_SPECS["IF"]` expressions from API code.

- [ ] **Step 6: Update watchlist search to use the full catalog**

Replace:

```python
FUTURES_PREFIXES = set(FUTURES_SPECS.keys())
```

with:

```python
FUTURES_PREFIXES = set(FUTURES_CATALOG.keys())
```

`get_futures_prefix` must return `""` for unknown input, not `"IF"`.

- [ ] **Step 7: Run catalog API and watchlist tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_catalog_api.py tests/test_watchlist.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit checkpoint**

```powershell
git add backend/app/api/v1/futures.py backend/app/api/v1/watchlist.py backend/tests/test_futures_catalog_api.py backend/tests/test_watchlist.py
git commit -m "feat: expose tiered futures catalog API"
```

---

### Task 6: 前端改用统一目录并显示不可用状态

**Files:**
- Modify: `frontend/src/types/futures.ts`
- Modify: `frontend/src/api/futures.ts`
- Create: `frontend/src/hooks/useFuturesCatalog.ts`
- Create: `frontend/src/components/FuturesDataUnavailable.tsx`
- Modify: `frontend/src/pages/FuturesAnalysis/index.tsx`
- Modify: `frontend/src/pages/FuturesCenter/TermStructureCard.tsx`
- Modify: `frontend/src/pages/FuturesCenter/CotRankCard.tsx`
- Modify: `frontend/src/pages/FuturesCenter/CalendarSpreadCard.tsx`
- Modify: `frontend/src/pages/FuturesCenter/BasisHistoryCard.tsx`
- Modify: `frontend/src/pages/FuturesCenter/RollYieldMatrixCard.tsx`
- Modify: `frontend/src/components/Topbar.tsx`
- Modify: frontend futures tests listed below

**Interfaces:**

```typescript
export type FuturesTier = 'core' | 'extended';

export interface FuturesInstrumentInfo {
  code: string;
  name: string;
  exchange: string;
  category: string;
  sina_symbol: string;
  listing_date: string | null;
  tier: FuturesTier;
  status: string;
  has_spec: boolean;
  trade_enabled: boolean;
  data_status: string;
  quality_status: string;
  source_first_date: string | null;
  source_last_date: string | null;
  effective_start: string | null;
}

export interface FuturesInstrumentCatalog {
  count: number;
  items: FuturesInstrumentInfo[];
}
```

API:

```typescript
instruments(params?: {
  tier?: 'core' | 'extended' | 'all';
  category?: string;
  enabled?: boolean;
}): Promise<FuturesInstrumentCatalog>;
```

Hook:

```typescript
export function useFuturesCatalog(): {
  items: FuturesInstrumentInfo[];
  coreItems: FuturesInstrumentInfo[];
  loading: boolean;
  error: string | null;
  reload: () => void;
};
```

- [ ] **Step 1: Add failing frontend catalog tests**

Create `frontend/src/hooks/useFuturesCatalog.test.tsx`:

```typescript
it('loads core instruments from the API', async () => {
  vi.mocked(futuresApi.instruments).mockResolvedValue({
    count: 2,
    items: [
      { code: 'IF', name: '沪深300股指期货', tier: 'core', trade_enabled: true } as never,
      { code: 'CF', name: '棉花', tier: 'core', trade_enabled: true } as never,
    ],
  });
  ...
  expect(result.current.coreItems.map((item) => item.code)).toEqual(['IF', 'CF']);
});
```

Modify page tests to assert the select contains `CF` after catalog mock resolution.

Add a test that an unavailable COT result renders `数据暂不可用` and does not render 20 member rows.

- [ ] **Step 2: Run frontend tests and verify they fail**

```powershell
cd frontend
npx vitest run src/hooks/useFuturesCatalog.test.tsx src/pages/FuturesAnalysis/FuturesAnalysis.test.tsx src/pages/FuturesCenter/FuturesCenter.test.tsx
```

Expected: FAIL because `instruments()` and the hook do not exist.

- [ ] **Step 3: Implement types, API and hook**

`useFuturesCatalog` loads once on mount, ignores stale responses after unmount, filters `coreItems`, and exposes `reload`.

At the same time, update `FuturesContractItem`:

```typescript
basis: number | null;
basis_rate: number | null;
annualized_basis_rate: number | null;
spot_price: number | null;
source?: 'sina' | 'cache' | string;
degraded?: boolean;
```

Format `null` basis fields as `-` rather than zero.

- [ ] **Step 4: Replace hardcoded symbol lists**

Delete each local `SYMBOL_OPTIONS` and `FUTURES_INDEX_LIST`.

Use `coreItems` to render options in:

```text
FuturesAnalysis
TermStructureCard
CotRankCard
CalendarSpreadCard
```

Option label:

```typescript
`${item.name} (${item.code})`
```

Sort by category order:

```text
index, bond, precious, metal, ferrous, energy, agri
```

Topbar:

- Populate futures search results from `coreItems`.
- Replace the fixed regex with an input classification that validates the extracted alphabetic prefix against the loaded catalog.
- If the catalog is still loading, direct alphabetic codes may be submitted but the page must handle unknown codes explicitly.

- [ ] **Step 5: Handle unavailable analytics**

`FuturesDataUnavailable` props:

```typescript
interface Props {
  title?: string;
  reason: string;
}
```

Render it when:

```typescript
data?.available === false
```

Update these cards to accept the new response shape:

```text
TermStructureCard
CotRankCard
CalendarSpreadCard
BasisHistoryCard
RollYieldMatrixCard
```

Do not leave stale previous data visible when a new response says `available=false`.

- [ ] **Step 6: Disable trade actions for extended instruments**

If `trade_enabled=false`:

- Show the instrument in catalog/filter views.
- Disable backtest, hedge and any mutation button.
- Display `data_status` or `quality_status` as the reason.

- [ ] **Step 7: Update tests and mocks**

Add `instruments: vi.fn()` to every `vi.mock('@/api/futures')` object touching these pages.

Update API result mocks for unavailable analytics:

```typescript
{ available: false, reason: '缺少真实交易所数据', data_status: 'unavailable' }
```

Run:

```powershell
cd frontend
npx vitest run src/hooks/useFuturesCatalog.test.tsx src/pages/FuturesAnalysis/FuturesAnalysis.test.tsx src/pages/FuturesCenter/FuturesCenter.test.tsx src/pages/FuturesCenter/RollOverRadarCard.test.tsx
```

Expected: PASS.

- [ ] **Step 8: Run type check**

```powershell
cd frontend
npm run type-check
```

Expected: PASS with no TypeScript errors.

- [ ] **Step 9: Commit checkpoint**

```powershell
git add frontend/src/types/futures.ts frontend/src/api/futures.ts frontend/src/hooks/useFuturesCatalog.ts frontend/src/components/FuturesDataUnavailable.tsx frontend/src/pages/FuturesAnalysis frontend/src/pages/FuturesCenter frontend/src/components/Topbar.tsx
git commit -m "feat: drive futures UI from catalog API"
```

---

### Task 7: 同步 40 个核心品种、执行质量清理并完成全量验证

**Files:**
- Modify: `backend/scripts/check_futures_data_quality.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/项目文档.md` only if it contains an inaccurate futures universe count
- Create at runtime: `data/parquet/futures_cache/manifest.json`
- Create at runtime: `data/quality_reports/futures_quality_<timestamp>.json`
- Create at runtime: `data/quality_reports/futures_quarantine_<timestamp>.parquet`

**Interfaces:**
- Quality script consumes `FUTURES_CORE` rather than the current `FUTURES_SPECS`, so core records remain expected even when quality temporarily disables one.

- [ ] **Step 1: Change quality expectation source**

In `backend/scripts/check_futures_data_quality.py`, replace:

```python
expected_symbols=FUTURES_SPECS,
```

with:

```python
expected_symbols=FUTURES_CORE,
```

Add a CLI regression assertion that missing core files are listed in `missing_symbols`.

- [ ] **Step 2: Run targeted verification before network work**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_futures_catalog.py tests/test_futures_store.py tests/test_futures_sync.py tests/test_futures_history_contract.py tests/test_futures_real_data_only.py tests/test_futures_catalog_api.py tests/test_futures_atomic_cache.py tests/test_futures_quality.py tests/test_futures.py tests/test_watchlist.py -q
.\.venv\Scripts\python.exe -m ruff check app/domain/futures_catalog.py app/domain/futures_rules.py app/data/futures.py app/data/futures_store.py app/data/futures_sync.py app/data/futures_quality.py app/api/v1/futures.py app/api/v1/watchlist.py tests/test_futures_catalog.py tests/test_futures_store.py tests/test_futures_sync.py tests/test_futures_history_contract.py tests/test_futures_real_data_only.py tests/test_futures_catalog_api.py
.\.venv\Scripts\python.exe -m mypy --strict app/domain/futures_catalog.py app/data/futures_store.py app/data/futures_sync.py
```

Expected: all tests and static checks pass.

- [ ] **Step 3: Seed the 40 core histories**

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\sync_futures_history.py --tier core --apply --force --max-workers 4
```

Expected:

```text
40 core symbols requested
40 parquet caches present
40 manifest entries present
exit code 0
```

If a transient upstream error occurs, retry the failed symbols with:

```powershell
.\.venv\Scripts\python.exe scripts\sync_futures_history.py --symbols <failed-csv> --apply --force --max-workers 2
```

- [ ] **Step 4: Run quality cleanup**

```powershell
.\.venv\Scripts\python.exe scripts\check_futures_data_quality.py --apply
```

Expected:

- A backup directory is created.
- A JSON report and quarantine parquet are created.
- The printed issue counts are zero after applying the cleaner.
- Remaining warnings are reported, not silently deleted.

- [ ] **Step 5: Audit manifest coverage**

Run:

```powershell
.\.venv\Scripts\python.exe -c "import json,pathlib; p=pathlib.Path('../data/parquet/futures_cache/manifest.json'); m=json.loads(p.read_text(encoding='utf-8')); print(len(m)); print(sorted(k for k,v in m.items() if not v.get('effective_start'))); print(sorted(k for k,v in m.items() if not v.get('trade_enabled')))"
```

Expected:

```text
40
[]
[]
```

- [ ] **Step 6: Update public documentation**

`README.md` and `CHANGELOG.md` must state:

```text
40 个核心期货品种，默认启用
43 个扩展期货品种，目录可见但默认不可交易
历史覆盖以数据源实际首日为准，不设统一 2008 年截断
```

Replace the old `60+` wording and ensure the documented `/api/v1/futures/instruments` endpoint exists.

- [ ] **Step 7: Run complete backend offline suite**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -m "not network" -q
```

Expected: no failures.

- [ ] **Step 8: Run frontend full verification**

```powershell
cd frontend
npm run test:raw
npm run type-check
npm run build
npm run style:check
npm run timeout:check
npm run nav:check
```

Expected: all commands exit 0.

- [ ] **Step 9: Final repository check**

```powershell
cd ..
git status --short
git diff --check
```

Expected:

- No whitespace errors.
- User-owned modifications in `backend/app/data/text_ingest.py` and `backend/app/orchestrator.py` remain untouched.
- Runtime cache/report files stay ignored by `.gitignore`.

- [ ] **Step 10: Commit final integration**

```powershell
git add README.md CHANGELOG.md backend/scripts/check_futures_data_quality.py docs/superpowers/specs/2026-10-30-futures-data-expansion-design.md docs/superpowers/plans/2026-10-30-futures-data-expansion.md
git commit -m "docs: document tiered futures universe and data governance"
```

---

## Plan Self-Review

- Spec coverage:
  - 83 品种目录、40 核心、43 扩展：Task 1、Task 5、Task 7。
  - 数据源实际首日与 manifest：Task 2。
  - 移除 synthetic 与 IF 回退：Task 3、Task 4、Task 5。
  - 真实数据边界：Task 4、Task 6。
  - 前后端目录统一：Task 5、Task 6。
  - 数据落地、质量报告、文档和全量验证：Task 7。
- Type consistency:
  - `FUTURES_CATALOG`、`FUTURES_CORE`、`FUTURES_EXTENDED`、`FUTURES_SPECS` 在所有任务中使用同一命名。
  - `source_first_date` 是原始数据首日，`cache_first_date` 是清理/截断后的缓存首日，`effective_start=cache_first_date`。
  - API 和前端统一使用 `trade_enabled`、`data_status`、`quality_status`、`available`、`modeled`。
- Review Focus coverage:
  - 无数据不合成：Task 3、Task 4。
  - 未知/未启用/质量失败区分：Task 1、Task 4、Task 5。
  - 首日与截断语义：Task 2、Task 7。
  - 规格与费用：Task 1、Task 5。
  - 前端不可用状态：Task 6。
