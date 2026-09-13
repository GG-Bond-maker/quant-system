"""``scripts/expand_universe_2500.py :: _classify_fetch_coverage`` 离线单测。

回归背景
--------
扩容脚本 run1 被中断后残留 4 只标的（``000729.SZ`` / ``300277.SZ`` /
``000962.SZ`` / ``600714.SH``），其 raw 分区（``daily_bar``）存在、但 hfq 分区
（``daily_bar_hfq``）残缺。旧覆盖判定**只检查 raw**，便把这些标的标记为
``covered``，于是它们永不进入重抓队列 —— hfq 空洞被 ``step_rebuild`` 静默传播
到由 raw+hfq 派生的 ``daily_bar_qfq``，污染下游 qfq 数据。

修复把覆盖判定抽成纯函数 ``_classify_fetch_coverage``，要求 **raw + hfq 双满足**
才算 ``covered``。本用例对 ``_symbol_min_date`` 打桩（键为 ``(rel, sym)``），
离线覆盖全部判定分支，无需网络、不落盘。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

_SCRIPT = PROJECT_ROOT / "scripts" / "expand_universe_2500.py"


def _load_script_module():
    """以文件路径加载扩容脚本为独立模块（避免污染 scripts/ 的模块命名空间）。

    必须在 ``exec_module`` 前把模块登记进 ``sys.modules``：脚本内的
    ``@dataclass`` 会通过 ``sys.modules[cls.__module__]`` 反查注解命名空间，
    未登记时 ``sys.modules.get(...)`` 返回 ``None`` 导致导入失败。
    """
    name = "expand_universe_2500_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None, "无法为脚本创建模块 spec"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


mod = _load_script_module()
COVER_MIN: date = mod.FETCH_COVER_MIN


@pytest.fixture
def min_date_table(monkeypatch):
    """以 ``(rel, sym)`` 为键的最小日期表打桩 ``_symbol_min_date``。

    未登记的键返回 ``None``（等价于「该数据集分区不存在」）。
    Returns:
        dict[(rel, sym) -> date | None]，测试内可直接写入期望值。
    """
    table: dict[tuple[str, str], date | None] = {}

    def _fake(root: Path, rel: str, sym: str) -> date | None:
        return table.get((rel, sym))

    monkeypatch.setattr(mod, "_symbol_min_date", _fake)
    return table


def _before(cover_min: date = COVER_MIN) -> date:
    """早于覆盖阈值（覆盖）的日期。"""
    return cover_min - timedelta(days=3)


def _after(cover_min: date = COVER_MIN) -> date:
    """晚于覆盖阈值（不覆盖）的日期。"""
    return cover_min + timedelta(days=30)


# ------------------------- 五类核心判定分支 -------------------------
def test_case1_raw_ok_hfq_ok_covered(min_date_table):
    """raw 与 hfq 均覆盖新起始日 -> covered。"""
    sym = "600000.SH"
    min_date_table[("daily_bar", sym)] = _before()
    min_date_table[("daily_bar_hfq", sym)] = _before()

    covered, raw, hfq = mod._classify_fetch_coverage(Path("R"), [sym], {sym})

    assert covered == {sym}
    assert raw == []
    assert hfq == []


def test_case2_raw_ok_hfq_absent_uncovered_hfq(min_date_table):
    """回归核心：raw 存在但 hfq 分区缺失（None）-> uncovered_hfq（必须重抓）。"""
    sym = "000729.SZ"
    min_date_table[("daily_bar", sym)] = _before()
    # 故意不登记 ("daily_bar_hfq", sym) -> None，模拟 hfq 分区缺失

    covered, raw, hfq = mod._classify_fetch_coverage(Path("R"), [sym], {sym})

    assert covered == set()
    assert raw == []
    assert hfq == [sym]


def test_case3_raw_ok_hfq_too_late_uncovered_hfq(min_date_table):
    """raw 覆盖但 hfq 最早日期晚于阈值 -> uncovered_hfq。"""
    sym = "300277.SZ"
    min_date_table[("daily_bar", sym)] = _before()
    min_date_table[("daily_bar_hfq", sym)] = _after()

    covered, raw, hfq = mod._classify_fetch_coverage(Path("R"), [sym], {sym})

    assert covered == set()
    assert raw == []
    assert hfq == [sym]


def test_case4_raw_too_late_hfq_ok_uncovered_raw(min_date_table):
    """raw 最早日期晚于阈值（hfq 正常）-> uncovered_raw。"""
    sym = "000962.SZ"
    min_date_table[("daily_bar", sym)] = _after()
    min_date_table[("daily_bar_hfq", sym)] = _before()

    covered, raw, hfq = mod._classify_fetch_coverage(Path("R"), [sym], {sym})

    assert covered == set()
    assert raw == [sym]
    assert hfq == []


def test_case5_not_in_have_in_none(min_date_table):
    """不在 ``have`` 中的标的：即使两口径都覆盖，也不出现在任何返回列表。"""
    sym = "600714.SH"
    min_date_table[("daily_bar", sym)] = _before()
    min_date_table[("daily_bar_hfq", sym)] = _before()

    covered, raw, hfq = mod._classify_fetch_coverage(Path("R"), [sym], set())

    assert covered == set()
    assert raw == []
    assert hfq == []


# ------------------------- 边界与补充 -------------------------
def test_raw_absent_is_uncovered_raw(min_date_table):
    """raw 分区缺失（None）也应归入 uncovered_raw（优先级高于 hfq）。"""
    sym = "600100.SH"
    min_date_table[("daily_bar_hfq", sym)] = _before()  # hfq 正常也无用

    covered, raw, hfq = mod._classify_fetch_coverage(Path("R"), [sym], {sym})

    assert covered == set()
    assert raw == [sym]
    assert hfq == []


def test_boundary_min_equals_cover_min_is_covered(min_date_table):
    """min 恰等于 cover_min 视为覆盖（阈值含当日，保持既有 <= 语义）。"""
    sym = "600001.SH"
    min_date_table[("daily_bar", sym)] = COVER_MIN
    min_date_table[("daily_bar_hfq", sym)] = COVER_MIN

    covered, raw, hfq = mod._classify_fetch_coverage(Path("R"), [sym], {sym})

    assert covered == {sym}
    assert raw == []
    assert hfq == []


def test_custom_cover_min_override(min_date_table):
    """``cover_min`` 可显式覆盖：分区早于自定义阈值即算覆盖。"""
    sym = "600002.SH"
    early = date(1999, 12, 30)  # <= 自定义阈值 2000-01-01
    min_date_table[("daily_bar", sym)] = early
    min_date_table[("daily_bar_hfq", sym)] = early

    covered, raw, hfq = mod._classify_fetch_coverage(
        Path("R"), [sym], {sym}, cover_min=date(2000, 1, 1))

    assert covered == {sym}
    assert raw == []
    assert hfq == []


def test_multi_symbol_ordering_and_partition(min_date_table):
    """多标的混合：分类互不干扰，列表保持 ``selected`` 顺序。"""
    ok, hole_hfq, hole_raw, absent_from_have = (
        "600000.SH", "000729.SZ", "000962.SZ", "600714.SH")
    min_date_table[("daily_bar", ok)] = _before()
    min_date_table[("daily_bar_hfq", ok)] = _before()
    min_date_table[("daily_bar", hole_hfq)] = _before()          # hfq 缺
    min_date_table[("daily_bar", hole_raw)] = _after()           # raw 缺
    min_date_table[("daily_bar_hfq", hole_raw)] = _before()
    min_date_table[("daily_bar", absent_from_have)] = _before()
    min_date_table[("daily_bar_hfq", absent_from_have)] = _before()

    selected = [hole_hfq, ok, hole_raw, absent_from_have]
    covered, raw, hfq = mod._classify_fetch_coverage(
        Path("R"), selected, {ok, hole_hfq, hole_raw})

    assert covered == {ok}
    assert raw == [hole_raw]
    assert hfq == [hole_hfq]
    # not-in-have 标的既不在 covered 也不在任一缺失列表（由 todo 兜底）
    assert absent_from_have not in covered
    assert absent_from_have not in raw and absent_from_have not in hfq
