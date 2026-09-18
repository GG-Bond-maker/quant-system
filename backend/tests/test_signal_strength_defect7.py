"""缺陷 7 回归：signal_strength 由「绝对阈值」改为「相对分位」（2026-09-18）。

背景：旧口径 = 绝对分数阈值 ``score>=0.3→strong / >=0.1→neutral / else weak``，
与模型分数尺度强耦合。生产模型换成 demean/repaired 版后分数尺度极小
（实测 board=all ``max pred_score=0.039818``、``avg=0.003317``），全部 < 0.1
⇒ 全市场恒为 weak、零信息量。用户裁定 Option A「相对分位」：
以板块池前 ``SIGNAL_REFERENCE_DEPTH``（=200）为参考总体，按名次分位切三档
（前 20% strong / 20%~50% neutral / 其余 weak），与绝对分数无关。

自包含：不写共享 DATA_ROOT（需要落盘时 monkeypatch 到 tmp_path）、不联网。

变异反证（实测）：把 ``enrich_items`` 的 signal_strength 表达式改回旧绝对阈值
``"strong" if score >= 0.3 else "neutral" if score >= 0.1 else "weak"``
⇒ ``test_enrich_items_relative_not_all_weak`` / ``test_reference_map_matches_enrich_items``
/ ``test_watchlist_quotes_relative_quantile`` 变红（全 weak）；逐字节还原后转绿。
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.data.screening import (  # noqa: E402
    SIGNAL_REFERENCE_DEPTH,
    compute_stats,
    enrich_items,
    filter_universe,
    signal_strength_by_rank,
    signal_strength_reference_map,
)

DAY = date(2026, 9, 17)
_ORDER = {"strong": 0, "neutral": 1, "weak": 2}


def _score_desc(n: int, top: float = 0.0398, step: float = 0.0001) -> list[float]:
    """降序分数，全部 < 0.1（复刻生产 demean 后尺度）。"""
    return [round(top - i * step, 6) for i in range(n)]


def _board_df(n: int = 200, base: int = 600000, top: float = 0.0398) -> pl.DataFrame:
    """伪造板块池 df（已按 pred_score 降序）：symbol/date/pred_score/close/limit_pct。"""
    syms = [f"{base + i:06d}.SH" for i in range(n)]
    return pl.DataFrame({
        "symbol": syms,
        "date": [DAY] * n,
        "pred_score": _score_desc(n, top),
        "close": [10.0] * n,
        "limit_pct": [10.0] * n,
    })


def _write_universe(root: Path, symbols: list[str], *, board: str = "main",
                    st_symbols: frozenset[str] = frozenset()) -> None:
    """把 universe_daily 落盘（路径必须与 parquet_store.path_for_year 同名）。"""
    n = len(symbols)
    uni = pl.DataFrame({
        "date": [DAY] * n,
        "symbol": symbols,
        "name": [f"标的{i}" for i in range(n)],
        "industry": ["行业A"] * n,
        "board": [board] * n,
        "is_st": [s in st_symbols for s in symbols],
        "is_halted": [False] * n,
        "close": [10.0] * n,
        "limit_pct": [10.0] * n,
    })
    uni_dir = root / "universe_daily" / "symbol=__all__"
    uni_dir.mkdir(parents=True, exist_ok=True)
    uni.write_parquet(uni_dir / f"year={DAY.year}.snappy.parquet")


@pytest.fixture()
def no_disk(monkeypatch):
    """隔离 enrich_items 的兜底读盘：instrument 表与 daily_bar 前收都打空。"""
    monkeypatch.setattr("app.data.screening.instrument_info", lambda: {})
    monkeypatch.setattr("app.data.universe.read_prev_and_today",
                        lambda sym, trade_date: {})


# 1) 纯函数边界与计数
def test_signal_strength_by_rank_counts_and_monotonic():
    assert signal_strength_by_rank(0) == []
    assert signal_strength_by_rank(-3) == []
    assert signal_strength_by_rank(1) == ["strong"]
    assert Counter(signal_strength_by_rank(10)) == {"strong": 2, "neutral": 3, "weak": 5}
    assert Counter(signal_strength_by_rank(200)) == {"strong": 40, "neutral": 60, "weak": 100}
    ranks = [_ORDER[x] for x in signal_strength_by_rank(200)]
    assert ranks == sorted(ranks), "标签序列必须单调（不允许 weak 早于 strong）"


# 2) 回归主测：全部分数 < 0.1 也不得全 weak
def test_enrich_items_relative_not_all_weak(no_disk):
    df = _board_df(200)
    assert float(df["pred_score"].max()) < 0.1, "测试数据须复刻生产尺度（全部 < 0.1）"
    items = enrich_items(df, 200)
    labels = [it["signal_strength"] for it in items]
    assert Counter(labels) == {"strong": 40, "neutral": 60, "weak": 100}
    assert set(labels) != {"weak"}, "缺陷 7：全市场不得再全 weak"
    # 字段/顺序不变：symbol 与输入一致、score 仍为 pred_score
    assert [it["symbol"] for it in items] == df["symbol"].to_list()
    assert items[0]["score"] == pytest.approx(0.0398)


# 3) top_k 无关性（实时榜 top50 与盘后快照 top200 必须一致）
def test_enrich_items_top_k_independent_labels(no_disk):
    df = _board_df(200)
    labels50 = [it["signal_strength"] for it in enrich_items(df, 50)]
    labels200 = [it["signal_strength"] for it in enrich_items(df, 200)]
    assert labels50 == labels200[:50], "参考深度不得随 top_k 变化（两路径一致契约）"


# 4) compute_stats 据此统计 strong_signal
def test_compute_stats_counts_strong_signal(no_disk):
    items = enrich_items(_board_df(200), 200)
    assert compute_stats(items, 200)["strong_signal"] == 40


# 5) 自选股 reference map 与 enrich_items 同口径；板块内最高分即使 <0.1 也是 strong
def test_reference_map_matches_enrich_items(tmp_path, monkeypatch, no_disk):
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    n = 10
    df = _board_df(n)
    _write_universe(tmp_path, df["symbol"].to_list(), board="main")

    main_df, _ = filter_universe(df, DAY.isoformat(), "main")
    items = enrich_items(main_df, SIGNAL_REFERENCE_DEPTH)
    labels = {it["symbol"]: it["signal_strength"] for it in items}
    ref = signal_strength_reference_map(df, DAY.isoformat())

    for sym, lab in labels.items():
        assert ref[sym] == lab, f"{sym} 两路径标签不一致: {lab} vs {ref.get(sym)}"
    top_sym = main_df["symbol"][0]
    assert labels[top_sym] == "strong"
    assert float(df["pred_score"].max()) < 0.1, "最高分仍 < 0.1，却应为 strong（相对分位）"


# 6) _watchlist_quotes 端到端：最高分（<0.1）→ strong；有分但不在参考总体 → weak；无分 → None
def test_watchlist_quotes_relative_quantile(tmp_path, monkeypatch, no_disk):
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    monkeypatch.setattr("app.api.v1.screener._load_instrument_info", lambda: {})
    monkeypatch.setattr("app.data.parquet_store.read_symbol_dataset",
                        lambda *a, **k: pl.DataFrame())

    n = 10
    df = _board_df(n)                                     # 600000..600009
    st_sym = df["symbol"].to_list()[-1]                   # 600009 标记 ST → 被剔除
    _write_universe(tmp_path, df["symbol"].to_list(), board="main",
                    st_symbols=frozenset({st_sym}))
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(pred_dir / f"date={DAY.strftime('%Y%m%d')}.parquet")

    from app.api.v1.screener import _watchlist_quotes

    out = _watchlist_quotes(["600000.SH", st_sym, "600099.SH"])
    by = {it["symbol"]: it for it in out["items"]}
    assert by["600000.SH"]["signal_strength"] == "strong"
    assert by["600000.SH"]["score"] is not None and by["600000.SH"]["score"] < 0.1
    assert by[st_sym]["signal_strength"] == "weak", "有分但不在参考总体 → weak"
    assert by["600099.SH"]["signal_strength"] is None, "无预测 → None（不瞎猜）"


# 7) B-1 回归：预测分区**缺 date 列** ⇒ 降级不抛异常（此前 pred_all["date"] 冒泡裸 500）
def test_watchlist_quotes_missing_date_column_degrades(tmp_path, monkeypatch, no_disk):
    """B-1（2026-09-18）：schema 漂移分区缺 `date` 列时，自选股路径必须**降级**而非裸 500。

    修复前 `trade_date = str(pred_all["date"].max())[:10]` 直接抛
    ``polars.exceptions.ColumnNotFoundError``，冒泡成 FastAPI 裸 500、自选股整页不可用
    （回归由缺陷 7 引入 —— 旧实现只读 ``pred_score``）。断言：不抛异常、``count``
    正确（行情字段仍在）、``score``/``signal_strength`` 均为 ``None``（降级不瞎猜）。
    """
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    monkeypatch.setattr("app.api.v1.screener._load_instrument_info", lambda: {})
    monkeypatch.setattr("app.data.parquet_store.read_symbol_dataset",
                        lambda *a, **k: pl.DataFrame())

    n = 10
    df = _board_df(n)
    _write_universe(tmp_path, df["symbol"].to_list(), board="main")
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    # 关键：落盘分区**缺 date 列**（模拟换代分区 schema 漂移）
    drift = df.drop("date")
    assert "date" not in drift.columns
    drift.write_parquet(pred_dir / f"date={DAY.strftime('%Y%m%d')}.parquet")

    from app.api.v1.screener import _watchlist_quotes

    syms = df["symbol"].to_list()[:3]
    out = _watchlist_quotes(syms)                      # 修复前：此处抛 ColumnNotFoundError
    assert out["count"] == len(syms), "行情字段仍在，count 必须正确"
    for it in out["items"]:
        assert it["signal_strength"] is None, "缺列降级 → 不瞎猜强度（非 'weak'）"
        assert it["score"] is None, "缺列降级 → score 为 None"
