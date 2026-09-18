"""step_validate 新语义回归（缺陷 3，docs/audit-2026-09-17/FIX-SPEC-sync-integrity.md §4）。

背景：旧 ``step_validate`` 只要任一 code 当日无数据即 raise ⇒ 全市场直跑必然失败
（实测 2026-09-11：352/2499 无当日数据）。新语义改为「相对上一交易日」判据 +
容差阈值，既抓「昨有今无」式整日数据丢失，又不误杀临时停牌股。

不写 parquet（禁止动共享 DATA_ROOT）：monkeypatch
``app.data.parquet_store.read_symbol_dataset``——``step_validate`` 是**函数内 import**，
patch 模块属性即可命中。

变异反证（每个用例 docstring 末尾注明）：
  - 容差改回 ``max(ABS, RATIO*n)``（恢复绝对下限）→ ``test_small_codelist_mass_loss_is_fatal``
    变红；
  - 去掉 ``prev is None`` 的覆盖率守卫 → ``test_calendar_unavailable_mass_loss_is_fatal``
    变红；
  - 把「当日无数据」改回无条件记错（不看 prev）→ ``test_validate_tolerates_suspended_symbols``
    的 ``suspended=`` 计数断言变红；
  - 把降级返回串改回 ``missing_vs_prev=<n>``（旧写法）→ ``test_validate_calendar_unavailable_degrades``
    与 ``test_calendar_unavailable_partial_coverage_degrades`` 的 ``missing_vs_prev=n/a``
    断言变红。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.pipeline import (  # noqa: E402
    VALIDATE_MIN_COVERAGE_FALLBACK,
    VALIDATE_MISSING_TOLERANCE_RATIO,
    step_validate,
)

TRADE_DAY = date(2026, 9, 14)
PREV_DAY = date(2026, 9, 11)


def _row(sym: str, d: date, *, close: float = 10.0) -> pl.DataFrame:
    """单行合法日线（close/open/high/low 相等，volume>=0，pct 在界内）。"""
    return pl.DataFrame({
        "symbol": [sym], "date": [d],
        "open": [close], "high": [close], "low": [close], "close": [close],
        "volume": [1000.0], "pct": [0.01],
    })


def _codes(n: int, base: int = 600000) -> list[str]:
    return [f"{base + i:06d}" for i in range(n)]


def _sym(code: str) -> str:
    return f"{code}.SH"  # 6 开头 -> 上交所


@pytest.fixture()
def fake_store(monkeypatch):
    """安装假 parquet：present[date][symbol] = DataFrame（缺省=空）。并固定 prev 日。"""
    present: dict[date, dict[str, pl.DataFrame]] = {TRADE_DAY: {}, PREV_DAY: {}}

    def fake_read(dataset, symbol, start=None, end=None, columns=None):
        assert dataset == "daily_bar"
        return present.get(start, {}).get(symbol, pl.DataFrame())

    monkeypatch.setattr("app.data.parquet_store.read_symbol_dataset", fake_read)
    # 日历可用的默认桩：prev = PREV_DAY
    monkeypatch.setattr("app.data.calendar_store.get_calendar", lambda: object())
    monkeypatch.setattr("app.domain.calendar.prev_trade_day", lambda d, cal: PREV_DAY)
    return present


class _RecLogger:
    """最小 logger 替身：捕获 WARNING（loguru 不进 pytest caplog）。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str) -> None:  # noqa: D401
        self.warnings.append(str(msg))

    def info(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def test_validate_tolerates_suspended_symbols(fake_store):
    """全市场当日无、prev 日也无 ⇒ 停牌/退市，**不 raise**，返回串含 suspended=N。

    变异反证：把「当日无数据」改回无条件记错（不看 prev 日）→ 这些 code 会全被
    计入 missing_vs_prev、suspended=0 ⇒ ``assert "suspended=3" in res`` 变红。
    """
    present = fake_store
    codes = _codes(3)
    res = step_validate(TRADE_DAY, codes)  # 不应抛异常
    assert "suspended=3" in res
    assert "missing_vs_prev=0" in res
    assert res.startswith("validated=0/3")


def test_validate_flags_mass_loss(fake_store):
    """整日数据丢失（模拟 09-14）：200 只 prev 有、当日全无 ⇒ **raise**，消息含计数。"""
    present = fake_store
    codes = _codes(200)
    for c in codes:
        present[PREV_DAY][_sym(c)] = _row(_sym(c), PREV_DAY)
    with pytest.raises(ValueError) as ei:
        step_validate(TRADE_DAY, codes)
    msg = str(ei.value)
    assert "昨有今无" in msg and "200" in msg


def test_validate_tolerates_small_newly_suspended(fake_store, monkeypatch):
    """防误杀（市场级）：1000 只有当日数据 + 20 只昨有今无（阈值内）⇒ 不 raise，但有 WARNING。

    纯比例阈值 = 0.05 * n_expected = 0.05 * 1020 = 51.0 ⇒ missing=20 ≤ 51 落于容差内。
    （规模取市场级：纯比例下小代码集更严格，见 test_small_codelist_* 系列。）

    变异反证：去掉容差判定（``if missing_errs:`` 即致命）⇒ 本用例在 ``step_validate``
    处抛 ValueError 变红。
    """
    present = fake_store
    ok_codes = _codes(1000, base=600000)
    miss_codes = _codes(20, base=601000)
    for c in ok_codes:
        present[TRADE_DAY][_sym(c)] = _row(_sym(c), TRADE_DAY)
    for c in miss_codes:
        present[PREV_DAY][_sym(c)] = _row(_sym(c), PREV_DAY)

    fake_log = _RecLogger()
    monkeypatch.setattr("app.data.pipeline.logger", fake_log)

    res = step_validate(TRADE_DAY, ok_codes + miss_codes)  # 不应抛异常
    assert "missing_vs_prev=20" in res
    assert res.startswith("validated=1000/1020")
    assert "degraded=0" in res
    assert any("昨有今无" in w for w in fake_log.warnings), fake_log.warnings
    assert VALIDATE_MISSING_TOLERANCE_RATIO == 0.05  # 纯比例常量口径钉死


def test_validate_quality_error_still_fatal(fake_store):
    """当日行存在但 validate_daily_bar 不通过（close<=0）⇒ **始终 raise**（严格性不放松）。"""
    present = fake_store
    code = _codes(1)[0]
    present[TRADE_DAY][_sym(code)] = _row(_sym(code), TRADE_DAY, close=-1.0)
    with pytest.raises(ValueError) as ei:
        step_validate(TRADE_DAY, [code])
    assert "validate failed" in str(ei.value)


def test_validate_calendar_unavailable_degrades(fake_store, monkeypatch):
    """日历不可用（get_calendar 抛错）⇒ 降级为覆盖率判据：不崩步，返回串标记 degraded=1。"""
    present = fake_store
    ok_codes = _codes(2)
    no_today = _codes(1, base=602000)

    def _boom() -> object:
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("app.data.calendar_store.get_calendar", _boom)
    fake_log = _RecLogger()
    monkeypatch.setattr("app.data.pipeline.logger", fake_log)

    for c in ok_codes:
        present[TRADE_DAY][_sym(c)] = _row(_sym(c), TRADE_DAY)
    # no_today 当日无数据：日历不可用 ⇒ 记为 suspended（跳过 prev 检查），coverage=2/3≥0.5
    res = step_validate(TRADE_DAY, ok_codes + no_today)
    assert "suspended=1" in res
    # 降级口径：missing_vs_prev 无意义 → n/a，改用 coverage 明示
    assert "missing_vs_prev=n/a" in res
    assert "coverage=0.67" in res
    assert "degraded=1" in res
    assert any("日历不可用" in w for w in fake_log.warnings), fake_log.warnings


def test_small_codelist_mass_loss_is_fatal(fake_store):
    """洞 1：3 只小代码集**全部**昨有今无 ⇒ 必须 raise（纯比例 n_expected=3、阈值=0.15）。

    变异反证：把容差改回 ``max(VALIDATE_MISSING_TOLERANCE_ABS, RATIO * n)``（恢复绝对
    下限 50）⇒ ``3 > 50`` 为假 ⇒ 不 raise ⇒ 本用例在 ``pytest.raises`` 处变红。
    """
    present = fake_store
    codes = _codes(3)
    for c in codes:
        present[PREV_DAY][_sym(c)] = _row(_sym(c), PREV_DAY)
    with pytest.raises(ValueError) as ei:
        step_validate(TRADE_DAY, codes)
    assert "昨有今无 3/3" in str(ei.value)


def test_small_codelist_single_missing_is_fatal(fake_store):
    """洞 1：3 只里 1 只昨有今无 ⇒ 必须 raise（恢复旧「任一缺失即 raise」的严格性）。

    n_expected = 2 ok + 1 missing = 3，阈值 = 0.15 ⇒ ``1 > 0.15`` ⇒ raise。
    变异反证：容差改回含绝对下限 50 ⇒ ``1 ≤ 50`` ⇒ 不 raise ⇒ 本用例变红。
    """
    present = fake_store
    codes = _codes(3)
    for c in codes[:2]:                                                  # 2 只有当日数据
        present[TRADE_DAY][_sym(c)] = _row(_sym(c), TRADE_DAY)
    present[PREV_DAY][_sym(codes[2])] = _row(_sym(codes[2]), PREV_DAY)   # 1 只昨有今无
    with pytest.raises(ValueError) as ei:
        step_validate(TRADE_DAY, codes)
    assert "昨有今无 1/3" in str(ei.value)


def test_small_codelist_all_suspended_passes(fake_store):
    """3 只当日与 prev 均无 ⇒ 纯停牌，不 raise（suspended=3、missing_vs_prev=0）。"""
    present = fake_store
    codes = _codes(3)
    res = step_validate(TRADE_DAY, codes)  # 不应抛异常
    assert "suspended=3" in res
    assert "missing_vs_prev=0" in res
    assert "degraded=0" in res


def test_calendar_unavailable_mass_loss_is_fatal(fake_store, monkeypatch):
    """洞 2：日历不可用（prev=None）且全部无当日数据 ⇒ 覆盖率 0% < 50% ⇒ **raise**。

    变异反证：去掉 ``prev is None`` 的覆盖率守卫 ⇒ 静默放行 ⇒ 本用例在
    ``pytest.raises`` 处变红。
    """
    present = fake_store
    codes = _codes(5)

    def _boom() -> object:
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("app.data.calendar_store.get_calendar", _boom)
    with pytest.raises(ValueError) as ei:
        step_validate(TRADE_DAY, codes)
    assert "覆盖率" in str(ei.value)


def test_calendar_unavailable_partial_coverage_degrades(fake_store, monkeypatch):
    """洞 2：prev=None 且覆盖率 ≥50% ⇒ 不 raise，且返回串含 degraded=1（降级可观测）。"""
    present = fake_store
    ok_codes = _codes(2)
    no_today = _codes(1, base=602000)

    def _boom() -> object:
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("app.data.calendar_store.get_calendar", _boom)
    for c in ok_codes:
        present[TRADE_DAY][_sym(c)] = _row(_sym(c), TRADE_DAY)
    res = step_validate(TRADE_DAY, ok_codes + no_today)  # coverage=2/3≈0.67 ≥ 0.5
    assert "degraded=1" in res
    assert res.startswith("validated=2/3")
    assert "missing_vs_prev=n/a" in res
    assert "coverage=0.67" in res
    assert VALIDATE_MIN_COVERAGE_FALLBACK == 0.5


# --------------------------------------------------------- 5% 容差边界（round 5）
# 判据 = ``len(missing) > VALIDATE_MISSING_TOLERANCE_RATIO * (n_ok + len(missing))``，
# 即 missing > n_ok/19 才致命；恰好 5% 视为**通过**（严格大于）。


def _seed(present, ok_codes: list[str], miss_codes: list[str]) -> None:
    """往假 store 灌入：ok 只写当日、miss 只写 prev 日。"""
    for c in ok_codes:
        present[TRADE_DAY][_sym(c)] = _row(_sym(c), TRADE_DAY)
    for c in miss_codes:
        present[PREV_DAY][_sym(c)] = _row(_sym(c), PREV_DAY)


def test_ratio_boundary_inside_passes(fake_store, monkeypatch):
    """5% 边界**内侧**：400 ok + 20 昨有今无 ⇒ n_expected=420、阈值=21.0，20 ≤ 21 ⇒ 不 raise。

    宽集放大：避免小代码集更严格带来的干扰；返回串含 missing_vs_prev=20 + WARNING。
    """
    present = fake_store
    ok_codes = _codes(400, base=600000)
    miss_codes = _codes(20, base=610000)
    _seed(present, ok_codes, miss_codes)
    fake_log = _RecLogger()
    monkeypatch.setattr("app.data.pipeline.logger", fake_log)

    res = step_validate(TRADE_DAY, ok_codes + miss_codes)  # 不应抛异常
    assert res.startswith("validated=400/420")
    assert "missing_vs_prev=20" in res
    assert "degraded=0" in res
    assert any("昨有今无" in w for w in fake_log.warnings), fake_log.warnings


def test_ratio_boundary_exactly_five_percent_passes(fake_store):
    """5% 边界**含等号**：380 ok + 20 昨有今无 ⇒ n_expected=400、阈值=20.0，20 ≤ 20 ⇒ 不 raise。

    钉死「严格大于」口径——恰好 5% 判为通过（与上一例只差比例分母不同）。
    """
    present = fake_store
    ok_codes = _codes(380, base=600000)
    miss_codes = _codes(20, base=610000)
    _seed(present, ok_codes, miss_codes)
    res = step_validate(TRADE_DAY, ok_codes + miss_codes)  # 不应抛异常
    assert "missing_vs_prev=20" in res
    assert "degraded=0" in res


def test_ratio_boundary_just_over_raises(fake_store):
    """5% 边界**外侧**：380 ok + 21 昨有今无 ⇒ n_expected=401、阈值=20.05，21 > 20.05 ⇒ raise。

    与「恰好 5%」例仅差 1 只，证明阈值确实在 5% 处精确切换。
    """
    present = fake_store
    ok_codes = _codes(380, base=600000)
    miss_codes = _codes(21, base=610000)
    _seed(present, ok_codes, miss_codes)
    with pytest.raises(ValueError) as ei:
        step_validate(TRADE_DAY, ok_codes + miss_codes)
    assert "昨有今无 21/401" in str(ei.value)


def test_small_codelist_ratio_boundary_raises(fake_store):
    """小代码集边界：105 ok + 6 昨有今无 ⇒ n_expected=111、阈值=5.55，6 > 5.55 ⇒ raise。

    证明纯比例口径下小集合的 5% 边界同样精确生效（不因绝对下限而失效）。
    """
    present = fake_store
    ok_codes = _codes(105, base=600000)
    miss_codes = _codes(6, base=610000)
    _seed(present, ok_codes, miss_codes)
    with pytest.raises(ValueError) as ei:
        step_validate(TRADE_DAY, ok_codes + miss_codes)
    assert "昨有今无 6/111" in str(ei.value)
