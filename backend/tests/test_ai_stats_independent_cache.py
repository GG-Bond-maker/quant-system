"""P3（2026-10-01）：`_build_ai_stats` 的**独立缓存**必须命中、必须自动失效、且不得缓存降级态。

## 为什么需要它

ai_stats 是 `_build_daily` 里最贵的子块（实测 **2.41s**，占 `_build_daily` 4.66s 的 **52%**），
但它的输入**只随 predictions 分区变化**（日频一次），而日频块的其它子块会随行情更新
更频繁地重建 ⇒ 挂在日频块 300s TTL 上会让 ai_stats 跟着白算一遍。

## 本文件断言什么

1. **第二次调用命中缓存** —— 不再读 parquet（读次数为 0），且载荷逐字段相同；
2. **输入变化自动失效** —— 新增一个预测分区后，缓存必须 miss（重新读）；
3. 🔴 **降级态绝不入缓存** —— 否则"先降级、后数据就绪"的路径会被缓存钉死 1 小时，
   与本仓「降级必须可自愈」的纪律冲突；
4. **TTL 到期后失效**；
5. **无预测分区时不缓存**（签名为 None）。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import market as market_api  # noqa: E402
from app.core.config import get_settings  # noqa: E402

SYMBOLS = [f"60000{i}.SH" for i in range(1, 9)]  # 8 只 ≥ min_symbols_per_day(5)
DATES = [date(2025, 6, d) for d in range(1, 11)]


def _write_hfq(root: Path, symbols: list[str], dates: list[date]) -> None:
    """每只标的写一份 2025 年 hfq。

    ⚠️ 收盘价必须**同时**随日期与标的编号变化：
    - 随日期变化 ⇒ `future_close` 非空、`fwd_ret` 可算；
    - 随标的编号变化 ⇒ **截面内有方差**，否则 `rank().corr()` 返回 NaN，
      会被 `pd.isna(ic)` 过滤掉，`daily_ic` 为空 ⇒ 早退"有效截面不足"。
    这里让 `fwd_ret` 与 `pred_score` **同向**递增，截面 RankIC = 1（非 NaN）。
    """
    for si, sym in enumerate(symbols):
        d = root / "daily_bar_hfq" / f"symbol={sym}"
        d.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({
            "symbol": [sym] * len(dates),
            "date": dates,
            "close": [1.0 + 0.01 * j + 0.05 * si for j in range(len(dates))],
        }).write_parquet(d / "year=2025.snappy.parquet")


def _write_pred(root: Path, day: str, symbols: list[str]) -> None:
    d = root / "predictions"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [date(int(day[:4]), int(day[4:6]), int(day[6:8]))] * len(symbols),
        "symbol": symbols,
        "pred_score": [0.1 + 0.05 * i for i in range(len(symbols))],
        "model_version": ["v1"] * len(symbols),
        "feature_version": ["f1"] * len(symbols),
        "label_horizon": [5] * len(symbols),
    }).write_parquet(d / f"date={day}.parquet")


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    # 每个测试用全新的缓存，避免跨测试串味
    monkeypatch.setattr(market_api, "_ai_stats_cache", {})
    return tmp_path


@pytest.fixture()
def reads(monkeypatch):
    """统计 `read_parquet_columns` 被调用的**次数**（透传真实现）。"""
    counter = {"n": 0}
    import app.data.parquet_store as ps
    real = ps.read_parquet_columns

    def _counting(files, columns=None, **kw):  # noqa: ANN001, ANN002, ANN003
        counter["n"] += 1
        return real(files, columns, **kw)

    monkeypatch.setattr(ps, "read_parquet_columns", _counting)
    return counter


def _seed_ok(root: Path, days: int = 5) -> None:
    """构造足以走到 ok 路径的数据：8 只 × N 天。"""
    _write_hfq(root, SYMBOLS, DATES)
    for d in DATES[:days]:
        _write_pred(root, d.strftime("%Y%m%d"), SYMBOLS)


def test_second_call_hits_cache(data_root, reads):
    """首次读盘 → 第二次必须命中缓存（读次数不增加），且载荷逐字段相同。"""
    _seed_ok(data_root)

    first = market_api._build_ai_stats()
    assert first["status"] == "ok", f"前置条件不满足，未走到 ok 路径：{first}"
    n_after_first = reads["n"]
    assert n_after_first > 0, "首次调用应读 parquet"

    second = market_api._build_ai_stats()
    assert reads["n"] == n_after_first, (
        f"第二次调用又读了 {reads['n'] - n_after_first} 次 parquet ⇒ 缓存未命中"
    )
    assert second == first, "缓存载荷与首次结果不一致"


def test_new_prediction_partition_invalidates_cache(data_root, reads):
    """新增一个预测分区 ⇒ 签名变化 ⇒ 必须重新计算。"""
    _seed_ok(data_root)
    market_api._build_ai_stats()
    n_before = reads["n"]

    # 新增一个预测日（内容变化 ⇒ 文件 mtime/size 变化）
    _write_pred(data_root, "20250611", SYMBOLS)
    market_api._build_ai_stats()

    assert reads["n"] > n_before, (
        "新增预测分区后仍命中缓存 ⇒ 签名未覆盖分区变化，会返回过期统计"
    )


def test_degraded_result_is_not_cached(data_root, reads):
    """🔴 降级态不得入缓存：补上行情后必须立刻恢复 ok，而不是被缓存钉死。"""
    # 只有预测、没有行情 ⇒ unavailable
    for d in DATES[:5]:
        _write_pred(data_root, d.strftime("%Y%m%d"), SYMBOLS)
    degraded = market_api._build_ai_stats()
    assert degraded["status"] == "unavailable", f"预期降级，实际：{degraded}"

    # 补上行情 ⇒ 同一签名下必须能自愈
    _write_hfq(data_root, SYMBOLS, DATES)
    healed = market_api._build_ai_stats()
    assert healed["status"] == "ok", (
        "补上行情后仍返回降级 ⇒ 降级态被缓存了（会把一次瞬时失败钉死整个 TTL）"
    )


def test_cache_expires_after_ttl(data_root, reads, monkeypatch):
    """TTL 到期后必须失效。"""
    _seed_ok(data_root)

    class _Clock:
        t = 1000.0

        def monotonic(self) -> float:
            return self.t

    clock = _Clock()
    # ⚠️ 必须先装时钟再首次调用：否则写入用的是真实 monotonic，
    # 而读取用的是假时钟，两个时基不同会让比较结果毫无意义。
    monkeypatch.setattr(market_api, "time", clock)

    market_api._build_ai_stats()
    n_before = reads["n"]
    assert n_before > 0, "首次调用应读 parquet"

    # TTL 内 ⇒ 命中
    clock.t = 1000.0 + market_api._AI_STATS_TTL_SECONDS - 1
    market_api._build_ai_stats()
    assert reads["n"] == n_before, "TTL 内不应重算"

    # 超过 TTL ⇒ 重算
    clock.t = 1000.0 + market_api._AI_STATS_TTL_SECONDS + 1
    market_api._build_ai_stats()
    assert reads["n"] > n_before, "TTL 到期后仍命中缓存 ⇒ 缓存永不过期"


def test_signature_none_without_predictions(data_root):
    """无预测分区 ⇒ 签名为 None（不缓存），不得抛异常。"""
    assert market_api._ai_stats_signature(get_settings()) is None
