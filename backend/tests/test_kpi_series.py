"""KPI 历史序列（data/kpi_series.py）的行为契约测试。

覆盖重点不是「函数能跑」，而是**它必须守住的诚实性契约**：
- 没有真实数据时必须 ``enough=False`` / ``comparable=False``，**绝不合成曲线**；
- 计数类指标在覆盖度剧烈变化时必须被标为不可比（否则会把「数据补全进度」
  画成「市场趋势」）；
- 归档必须过「交易日 + 盘后」双判据（否则周末/节假日/盘前会灌重复值）。

这些用例全部用 monkeypatch 隔离真实磁盘/Redis，不依赖当日数据状态。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import orjson
import polars as pl

from app.data import kpi_series as K


# ---------------- 工具：构造假的预测/截面数据 ----------------


def _pred(day: date, n: int) -> pl.DataFrame:
    return pl.DataFrame({
        "date": [day] * n,
        "symbol": [f"{i:06d}.SZ" for i in range(n)],
        "pred_score": [round(0.01 - i * 1e-5, 8) for i in range(n)],
    })


def _sect(day: date, n: int, base: float = 10.0) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [f"{i:06d}.SZ" for i in range(n)],
        "close": [base + i * 0.01 for i in range(n)],
    })


def _patch_trading_days(monkeypatch, days: set) -> None:
    """把交易日判定替换为固定集合。

    为什么必须替换：``etf_overview_series`` 内部会真实查询 SQLite
    ``trade_calendar``。隔离测试库里该表为空/不存在 ⇒ ``_is_trading_day``
    对**任何**日期都返回 False ⇒ 所有快照都被判为非交易日、``count`` 恒为 0，
    用例失去区分度（"过滤生效"与"过滤过火"都变成 0 点，测不出来）。
    替换掉这个外部依赖后，用例只验证过滤逻辑本身。
    """
    monkeypatch.setattr(K, "_is_trading_day", lambda d: d in days)


# ---------------- 契约 1：MIN_POINTS 门槛 ----------------


class TestEnoughGate:
    def test_below_min_points_not_enough(self):
        """点数 < MIN_POINTS 时 enough=False（前端据此不绘制）。"""
        raw = [K.SeriesPoint(date=f"2026-01-{i:02d}", value=float(i))
               for i in range(1, K.MIN_POINTS)]
        m = K._build_metric("x", "basis", "%", raw, [], countable=False)
        assert m.enough is False
        assert m.count == len(raw)

    def test_at_min_points_is_enough(self):
        """恰好 MIN_POINTS 点时 enough=True（边界含）。"""
        raw = [K.SeriesPoint(date=f"2026-01-{i:02d}", value=float(i))
               for i in range(1, K.MIN_POINTS + 1)]
        m = K._build_metric("x", "basis", "%", raw, [], countable=False)
        assert m.enough is True

    def test_none_values_excluded_from_count(self):
        """value 为 None 的点不计入 count（不得当作 0）。"""
        raw = [K.SeriesPoint(date=f"2026-01-{i:02d}", value=1.0) for i in range(1, 5)]
        raw += [K.SeriesPoint(date="2026-01-05", value=None),
                K.SeriesPoint(date="2026-01-06", value=None)]
        m = K._build_metric("x", "basis", "%", raw, [], countable=False)
        assert m.count == 4
        assert all(p.value is not None for p in m.points)


# ---------------- 契约 2：计数类可比性 ----------------


class TestComparableGate:
    def test_countable_spread_marks_incomparable(self):
        """计数类指标覆盖度极差 > 阈值 ⇒ comparable=False 且 note 说明原因。

        这是防止「股票数量 119 → 2431」被当成市场趋势画出来的**唯一**闸门。
        """
        raw = [K.SeriesPoint(date=f"2026-01-{i:02d}", value=float(i) * 10,
                             coverage=cov)
               for i, cov in enumerate([0.05, 0.05, 0.06, 0.9, 0.95, 0.97], start=1)]
        m = K._build_metric("pool_size", "basis", "只", raw, [], countable=True)
        assert m.comparable is False
        assert m.note is not None
        assert "覆盖度" in m.note

    def test_countable_stable_coverage_stays_comparable(self):
        """覆盖度稳定（极差 ≤ 阈值）时计数类仍可比 —— 不能一律拉黑。"""
        raw = [K.SeriesPoint(date=f"2026-01-{i:02d}", value=float(i),
                             coverage=0.90 + i * 0.001)
               for i in range(1, 8)]
        m = K._build_metric("pool_size", "basis", "只", raw, [], countable=True)
        assert m.comparable is True

    def test_ratio_metric_ignores_coverage(self):
        """比率/均值类（countable=False）**不受**覆盖度影响 —— 池子大小不改变比率。"""
        raw = [K.SeriesPoint(date=f"2026-01-{i:02d}", value=2.0, coverage=cov)
               for i, cov in enumerate([0.05, 0.9, 0.05, 0.95, 0.06, 0.97], start=1)]
        m = K._build_metric("win_rate", "basis", "%", raw, [], countable=False)
        assert m.comparable is True


# ---------------- 契约 3：dropped 如实记录 ----------------


class TestDropped:
    def test_pool_smaller_than_topk_is_dropped_with_reason(self, monkeypatch):
        """pool_size < top_k 的日期必须进 dropped 并写明原因，不得塞进曲线。

        模拟 2026-09-07 的真实残缺分区（当日只有 19 只预测）。
        """
        d = date(2026, 9, 7)
        monkeypatch.setattr(K, "_pred_dates_asc", lambda: [d])
        monkeypatch.setattr(K, "_cs_section", lambda x: _sect(x, 20))
        monkeypatch.setattr(K, "_section_close_map", lambda x: {})
        monkeypatch.setattr(K, "_universe_rows_map", lambda ds: {x: 2500 for x in ds})
        _patch_pred_reads(monkeypatch, {d: _pred(d, 17)})   # 17 < top_k=50

        out = K.screener_stats_series(days=30, top_k=50)
        assert out["avg_pct"].count == 0
        assert any("pool_size" in str(x.get("reason", ""))
                   for x in out["avg_pct"].dropped)

    def test_empty_pred_dates_returns_all_not_enough(self, monkeypatch):
        """完全无预测分区 ⇒ 六个指标全 enough=False，且不抛异常。"""
        monkeypatch.setattr(K, "_pred_dates_asc", lambda: [])
        out = K.screener_stats_series(days=30)
        assert set(out) == {"pool_size", "win_rate", "avg_pct", "avg_score",
                            "strong_signal", "industry_count"}
        assert all(m.enough is False for m in out.values())
        assert all(m.count == 0 for m in out.values())
        assert all(m.basis for m in out.values())   # basis 不得为空


def _patch_pred_reads(monkeypatch, preds: dict[date, pl.DataFrame]) -> None:
    """让 ``pl.read_parquet(<某日的 predictions 路径>)`` 返回预设 DataFrame。

    为什么不用假 DATA_ROOT 对象：``DATA_ROOT`` 是 ``Path``，而 ``screener_stats_series``
    里会 ``pl.read_parquet(p)`` 真实读文件；伪造一个鸭子类型的路径对象**无法**让
    polars 认出来（它要求 os.PathLike）。直接 patch ``pl.read_parquet`` 更可靠，
    且只对 ``date=YYYYMMDD.parquet`` 形状的路径生效，不干扰其他读取。
    """
    real_read = pl.read_parquet

    def _fake_read(path, *a, **kw):
        name = Path(str(path)).name
        if name.startswith("date=") and name.endswith(".parquet"):
            key = datetime.strptime(name[len("date="):-len(".parquet")], "%Y%m%d").date()
            if key in preds:
                return preds[key]
        return real_read(path, *a, **kw)

    monkeypatch.setattr(pl, "read_parquet", _fake_read)


# ---------------- 契约 4：归档双判据 ----------------


class TestArchiveGate:
    def test_weekend_not_archived(self, monkeypatch):
        """非交易日（周末）不归档 —— 否则会写入上一交易日的重复值。"""
        # 2026-09-26 是周六
        monkeypatch.setattr(K, "_is_trading_day", lambda d: False)
        ok, why = K.should_archive_etf_snapshot(
            datetime(2026, 9, 26, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
        assert ok is False
        assert "非交易日" in why

    def test_intraday_not_archived(self, monkeypatch):
        """交易日但未到盘后 ⇒ 不归档（盘中值与其余日期的收盘值口径不同）。"""
        monkeypatch.setattr(K, "_is_trading_day", lambda d: True)
        ok, why = K.should_archive_etf_snapshot(
            datetime(2026, 9, 25, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
        assert ok is False
        assert "盘后" in why

    def test_trading_day_after_close_archived(self, monkeypatch):
        """交易日 + 盘后 ⇒ 归档。"""
        monkeypatch.setattr(K, "_is_trading_day", lambda d: True)
        ok, why = K.should_archive_etf_snapshot(
            datetime(2026, 9, 25, 18, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
        assert ok is True

    def test_is_trading_day_false_when_calendar_missing(self, monkeypatch):
        """trade_calendar 表不存在 ⇒ 保守判非交易日（宁可漏归档也不灌重复值）。

        ``SQLITE_PATH`` 是只读 property（由 SQLITE_URL 派生），故直接替换
        ``get_settings`` 返回一个指向**空库**的替身，而不是改 property。
        """
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            empty_db = Path(td) / "empty.db"
            sqlite3.connect(empty_db).close()   # 建库但不建 trade_calendar 表

            class _S:
                SQLITE_PATH = empty_db

            monkeypatch.setattr(K, "get_settings", lambda: _S())
            assert K._is_trading_day(date(2026, 9, 25)) is False

    def test_is_trading_day_uses_sh_or_sz_flags(self, monkeypatch):
        """有 trade_calendar 表时按 is_sh/is_sz 判定（非交易日返回 False）。"""
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "cal.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE trade_calendar "
                        "(trade_date DATE, is_sh BOOLEAN, is_sz BOOLEAN)")
            con.execute("INSERT INTO trade_calendar VALUES ('2026-09-25', 1, 1)")
            con.execute("INSERT INTO trade_calendar VALUES ('2026-09-26', 0, 0)")
            con.commit()
            con.close()

            class _S:
                SQLITE_PATH = db

            monkeypatch.setattr(K, "get_settings", lambda: _S())
            assert K._is_trading_day(date(2026, 9, 25)) is True
            assert K._is_trading_day(date(2026, 9, 26)) is False


# ---------------- 契约 5：ETF 序列不得编造 ----------------


class TestEtfSeriesHonesty:
    def test_empty_archive_all_not_enough(self, monkeypatch):
        """无存档 ⇒ 五个指标全 enough=False 且有说明，绝不返回补出的曲线。"""
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: [])
        out = K.etf_overview_series(days=30)
        assert set(out) == {"etf_count", "total_size_yi", "avg_pct",
                            "net_inflow_yi", "amount_yi"}
        for m in out.values():
            assert m.enough is False
            assert m.points == []
            assert m.note

    def test_source_change_not_merged_into_one_curve(self, monkeypatch):
        """口径断裂：不同 source 的存档不得混进同一条曲线，且要进 dropped 说明。"""
        snaps = [
            {"date": "2026-09-11", "source": None, "etf_count": 1348},
            {"date": "2026-09-13", "source": None, "etf_count": 1348},
            {"date": "2026-09-24", "source": "sina+tencent", "etf_count": 1679},
            {"date": "2026-09-25", "source": "sina+tencent", "etf_count": 1685},
        ]
        # 两条同口径的都是交易日 ⇒ 本用例只验证"口径过滤"，不掺杂交易日过滤
        _patch_trading_days(monkeypatch, {date(2026, 9, 24), date(2026, 9, 25)})
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: snaps)
        out = K.etf_overview_series(days=30)
        m = out["etf_count"]
        # 只保留与最新一条同口径的 2 条（< MIN_POINTS ⇒ 不可画）
        assert m.count == 2
        assert m.enough is False
        # 口径不一致的两条必须出现在 dropped 里并注明不可比
        assert len(m.dropped) == 2
        assert all("不可比" in d["reason"] for d in m.dropped)

    def test_non_trading_day_snapshots_are_dropped(self, monkeypatch):
        """非交易日 / 相邻同值快照必须剔除 —— 它们是上一交易日收盘的重复，不是独立观测。

        复刻线上真实归档的形态：09-26（六）/09-27（日）/09-28（盘前）外部源返回的
        都是 09-25 周五收盘 ⇒ 三者逐位相同，是**同一观测被重复标注**。09-27/09-28
        被「相邻同值去重」剔除（保留 09-26 作代表），09-26 又被「非交易日」剔除 ⇒
        最终只剩 09-24 一个独立观测点。

        归档端双判据（``should_archive_etf_snapshot``）只能挡**未来**写入，
        对判据上线前已落盘的脏数据无效 ⇒ 读取侧必须独立守住。
        """
        snaps = [
            {"date": "2026-09-24", "source": "sina+tencent", "etf_count": 1679,
             "amount_yi": 4749.45},
            {"date": "2026-09-26", "source": "sina+tencent", "etf_count": 1685,
             "amount_yi": 4786.46},
            {"date": "2026-09-27", "source": "sina+tencent", "etf_count": 1685,
             "amount_yi": 4786.46},
            {"date": "2026-09-28", "source": "sina+tencent", "etf_count": 1685,
             "amount_yi": 4786.46},
        ]
        _patch_trading_days(monkeypatch, {date(2026, 9, 24), date(2026, 9, 28)})
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: snaps)
        m = K.etf_overview_series(days=30)["etf_count"]

        # 只有 09-24 是独立观测点
        assert m.count == 1, [p.date for p in m.points]
        assert [p.date for p in m.points] == ["2026-09-24"]
        dropped_dates = {d["date"] for d in m.dropped}
        assert dropped_dates == {"2026-09-26", "2026-09-27", "2026-09-28"}, m.dropped
        # 剔除原因必须写清（非交易日 / 同一观测重复标注），不能静默丢弃
        by_date = {d["date"]: d["reason"] for d in m.dropped}
        assert "非交易日" in by_date["2026-09-26"]
        assert "重复标注" in by_date["2026-09-27"]
        assert "重复标注" in by_date["2026-09-28"]

    def test_nontrading_filter_does_not_touch_trend_when_absent(self, monkeypatch):
        """反向守卫：全是交易日时，交易日过滤不得误伤任何一个点。"""
        snaps = [{"date": f"2026-09-{d:02d}", "source": "sina+tencent",
                  "etf_count": 1600 + d}
                 for d in (21, 22, 23, 24, 25, 28)]
        _patch_trading_days(monkeypatch, {date(2026, 9, d)
                                          for d in (21, 22, 23, 24, 25, 28)})
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: snaps)
        m = K.etf_overview_series(days=30)["etf_count"]
        assert m.count == 6, [p.date for p in m.points]
        assert m.enough is True
        assert m.dropped == []


# ---------------- 契约 6：无合成数据 ----------------


class TestNoSynthesis:
    def test_points_only_from_real_rows(self, monkeypatch):
        """序列点数必须等于**可复算的**真实日期数 —— 不得补齐、插值或重复当前值。

        注意窗口首日不可复算：涨跌幅需要 prev_close，而首日没有更早的截面
        ⇒ 该日必须被 dropped（如实记录），而不是拿当日收盘当 prev_close 造出 0%。
        """
        # 7 个真实日期 ⇒ 首日无 prev_close 被 dropped，剩 6 个点 == MIN_POINTS（边界含）
        days = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4),
                date(2026, 6, 5), date(2026, 6, 8), date(2026, 6, 9)]
        preds = {d: _pred(d, 60) for d in days}
        monkeypatch.setattr(K, "_pred_dates_asc", lambda: days)
        monkeypatch.setattr(K, "_cs_section", lambda x: _sect(x, 60))
        monkeypatch.setattr(K, "_section_close_map",
                            lambda x: {f"{i:06d}.SZ": 10.0 + i * 0.01 for i in range(60)})
        monkeypatch.setattr(K, "_universe_rows_map", lambda ds: {x: 2500 for x in ds})
        _patch_pred_reads(monkeypatch, preds)
        # 存档里塞 30 条哨兵记录：曲线一旦被"补齐"就会污染，用来证明只取真实可复算点
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: [
            {"date": f"2026-07-{i:02d}", "source": "sina+tencent",
             "etf_count": 9999, "total_size_yi": 9999.0, "avg_pct": 9.99}
            for i in range(1, 31)
        ])

        out = K.screener_stats_series(days=30, top_k=50)
        m = out["avg_pct"]
        # 7 个真实日期，首日无 prev_close ⇒ 6 个点（绝不是补到 30 个）
        assert m.count == len(days) - 1 == K.MIN_POINTS
        assert m.count != 30
        assert m.enough is True
        # 点日期必须是被保留的真实日期（首日不在其中）
        assert {p.date for p in m.points} == {d.isoformat() for d in days[1:]}
        assert days[0].isoformat() not in {p.date for p in m.points}

    def test_basis_never_empty(self, monkeypatch):
        """所有指标的 basis 必须非空（口径必须披露）。"""
        monkeypatch.setattr(K, "_pred_dates_asc", lambda: [])
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: [])
        for m in list(K.screener_stats_series(days=30).values()) + \
                list(K.etf_overview_series(days=30).values()):
            assert m.basis and m.basis.strip()
            assert m.kind == "platform"


# ---------------- 契约 7：相邻同值去重（历史脏数据读取兜底） ----------------


# 线上 09-25 周五收盘被误标注到 09-26/27/28 的形态：三者全部指标逐位相同。
_DUP_BODY = {
    "etf_count": 1685, "total_size_yi": 49541.83, "avg_pct": -1.5158,
    "net_inflow_yi": None, "amount_yi": 4786.46,
}


class TestAdjacentDuplicateDedup:
    """相邻存档全部指标同值 ⇒ 判定为「同一观测被重复标注」，只保留最早一条。

    这是读取侧对「盘前/周末被动归档把同一观测重复标注到多个自然日」的历史兜底：
    归档双判据（``should_archive_etf_snapshot``）只挡未来写入，挡不住已落盘的脏数据。
    """

    def test_adjacent_identical_snapshots_collapsed_to_one(self, monkeypatch):
        snaps = [
            {"date": "2026-09-24", "source": "sina+tencent",
             "etf_count": 1679, "total_size_yi": 50120.45, "avg_pct": -0.4776,
             "net_inflow_yi": None, "amount_yi": 4749.45},
            {"date": "2026-09-26", "source": "sina+tencent", **_DUP_BODY},
            {"date": "2026-09-27", "source": "sina+tencent", **_DUP_BODY},
            {"date": "2026-09-28", "source": "sina+tencent", **_DUP_BODY},
        ]
        # 09-24 与 09-28 都是交易日 —— 证明 09-28 被剔不是因为非交易日，
        # 而是因为它是 09-26/27 这条"重复标注链"的一员。
        _patch_trading_days(monkeypatch, {date(2026, 9, 24), date(2026, 9, 28)})
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: snaps)
        m = K.etf_overview_series(days=30)["etf_count"]

        # 只保留 09-24（09-26 作代表保留后又被非交易日剔除）
        assert m.count == 1, [p.date for p in m.points]
        assert [p.date for p in m.points] == ["2026-09-24"]
        dup_dates = {d["date"] for d in m.dropped if "重复标注" in d["reason"]}
        assert dup_dates == {"2026-09-27", "2026-09-28"}, m.dropped

    def test_distinct_adjacent_trading_days_not_deduped(self, monkeypatch):
        """反向守卫：相邻但**全字段不同**的真实交易日绝不能被去重误删。

        两个交易日仅在 ``avg_pct`` 上不同（其余字段相同）也必须都保留 —— 若判据
        只看某单一字段就会误删，本用例专门守住这点。
        """
        snaps = [
            {"date": "2026-09-24", "source": "sina+tencent",
             "etf_count": 1679, "total_size_yi": 50120.45, "avg_pct": -0.4776,
             "net_inflow_yi": None, "amount_yi": 4749.45},
            {"date": "2026-09-25", "source": "sina+tencent",
             "etf_count": 1679, "total_size_yi": 50120.45, "avg_pct": -1.5158,
             "net_inflow_yi": None, "amount_yi": 4749.45},
        ]
        _patch_trading_days(monkeypatch, {date(2026, 9, 24), date(2026, 9, 25)})
        monkeypatch.setattr(K, "_read_etf_snapshots", lambda: snaps)
        m = K.etf_overview_series(days=30)["etf_count"]

        assert m.count == 2, [p.date for p in m.points]
        assert [p.date for p in m.points] == ["2026-09-24", "2026-09-25"]
        assert m.dropped == []


# ---------------- 契约 8：归档写入幂等 / 覆盖 ----------------


class TestAppendEtfSnapshotIdempotency:
    """``api.v1.etf.append_etf_snapshot`` 的幂等（同日已有盘后行才跳过）与覆盖语义。

    覆盖场景正是本次缺陷修复点：盘前/周末写下的占位行（旧格式无
    ``archived_post_close`` 字段，或该字段为 False）必须能被盘后的真收盘**覆盖**，
    否则当日真收盘快照会永久缺失。所有 Redis 访问都被换成内存桩（零外网、零真实
    Redis 写入）。
    """

    @staticmethod
    def _install_redis(monkeypatch, history: list[dict] | None):
        """把 etf 模块的 RedisClient.get/set 换成内存桩，返回 (store, writes)。"""
        import app.api.v1.etf as etf_api

        store: dict = {
            "v": orjson.dumps(history) if history is not None else None
        }
        writes: list[list[dict]] = []

        async def _fake_get(cls, key):
            return store["v"]

        async def _fake_set(cls, key, value, ex=3600, stale_ex=None):
            store["v"] = value
            writes.append(orjson.loads(value))

        monkeypatch.setattr(etf_api.RedisClient, "get", classmethod(_fake_get))
        monkeypatch.setattr(etf_api.RedisClient, "set", classmethod(_fake_set))
        return etf_api, store, writes

    def test_same_day_post_close_row_skips(self, monkeypatch):
        """同日已有 ``archived_post_close=True`` 的行 ⇒ 跳过、返回 False、内容不变。"""
        monkeypatch.setattr(K, "should_archive_etf_snapshot",
                            lambda *a, **k: (True, "交易日盘后"))
        existing = {"date": "2026-09-24", "archived_post_close": True,
                    "etf_count": 1679, "amount_yi": 4749.45}
        etf_api, store, writes = self._install_redis(monkeypatch, [existing])

        ok = asyncio.run(etf_api.append_etf_snapshot(
            {"etf_count": 9999}, trade_date="2026-09-24"))

        assert ok is False
        assert writes == [], "同日已有盘后行时不得写入"
        assert orjson.loads(store["v"]) == [existing], "内容必须保持不变"

    def test_same_day_legacy_row_is_overwritten(self, monkeypatch):
        """同日只有**无 archived_post_close 字段**的旧行 ⇒ 覆盖、返回 True、无重复行。"""
        monkeypatch.setattr(K, "should_archive_etf_snapshot",
                            lambda *a, **k: (True, "交易日盘后"))
        legacy = {"date": "2026-09-28", "etf_count": 1685, "amount_yi": 4786.46}
        etf_api, store, writes = self._install_redis(monkeypatch, [legacy])

        ok = asyncio.run(etf_api.append_etf_snapshot(
            {"etf_count": 1700, "amount_yi": 5000.0}, trade_date="2026-09-28"))

        assert ok is True
        assert len(writes) == 1
        rows = orjson.loads(store["v"])
        assert len(rows) == 1, "覆盖后同日只能有一条记录"
        assert rows[0]["date"] == "2026-09-28"
        assert rows[0]["etf_count"] == 1700, "内容必须更新为本次观测"
        assert rows[0]["archived_post_close"] is True

    def test_preclose_placeholder_overwritten_by_postclose(self, monkeypatch):
        """盘前占位行（archived_post_close=False）在盘后被真收盘覆盖 ⇒ 修复数据丢失。"""
        gate: dict = {"v": (False, "未到盘后时点（需 15:30 之后）")}
        monkeypatch.setattr(K, "should_archive_etf_snapshot",
                            lambda *a, **k: gate["v"])
        etf_api, store, _ = self._install_redis(monkeypatch, None)

        # 盘前写占位（archived_post_close=False）
        ok1 = asyncio.run(etf_api.append_etf_snapshot(
            {"etf_count": 1685}, trade_date="2026-09-28"))
        assert ok1 is True
        row_1 = orjson.loads(store["v"])[0]
        assert row_1["etf_count"] == 1685
        assert row_1["archived_post_close"] is False

        # 盘后判据通过，同一日期再写 ⇒ 必须覆盖，而非"同日已有则跳过"
        gate["v"] = (True, "交易日盘后")
        ok2 = asyncio.run(etf_api.append_etf_snapshot(
            {"etf_count": 1700}, trade_date="2026-09-28"))
        assert ok2 is True, "盘后真收盘必须能覆盖盘前占位行"
        rows = orjson.loads(store["v"])
        assert len(rows) == 1
        assert rows[0]["etf_count"] == 1700
        assert rows[0]["archived_post_close"] is True


# ---------------- 契约 9：读路径被动归档必须过同一套判据 ----------------


class TestOverviewReadPathGate:
    """``_overview_with_prev`` 的被动归档必须复用 ``should_archive_etf_snapshot``。

    不加判据的代价：盘前/周末访问会把上一交易日值写成 today 行，当晚盘后定时任务
    因"该日期已存在"而跳过 ⇒ 当日真收盘永久丢失。Redis 与外部源全部 monkeypatch。
    """

    _HISTORY = [{"date": "2026-09-24", "source": "sina+tencent",
                 "etf_count": 1679}]

    def _install(self, monkeypatch):
        import app.api.v1.etf as etf_api

        writes: list[bytes] = []

        async def _fake_get(cls, key):
            return orjson.dumps(self._HISTORY)

        async def _fake_set(cls, key, value, ex=3600, stale_ex=None):
            writes.append(value)

        monkeypatch.setattr(etf_api.RedisClient, "get", classmethod(_fake_get))
        monkeypatch.setattr(etf_api.RedisClient, "set", classmethod(_fake_set))
        monkeypatch.setattr(etf_api, "_overview_snapshot",
                            lambda: {"etf_count": 9999,
                                     "source": "sina+tencent"})
        return etf_api, writes

    def test_read_path_skips_archive_when_gate_fails(self, monkeypatch):
        """判据不通过（盘前/非交易日）⇒ 读路径**不得**写档。"""
        etf_api, writes = self._install(monkeypatch)
        monkeypatch.setattr(K, "should_archive_etf_snapshot",
                            lambda *a, **k: (False, "未到盘后时点（需 15:30 之后）"))

        asyncio.run(etf_api._overview_with_prev())

        assert writes == [], "判据不通过时读路径不得写档"

    def test_read_path_writes_when_gate_passes(self, monkeypatch):
        """判据通过且同日无档 ⇒ 读路径写档（保留既有被动归档行为，不多不少写一次）。"""
        etf_api, writes = self._install(monkeypatch)
        monkeypatch.setattr(K, "should_archive_etf_snapshot",
                            lambda *a, **k: (True, "交易日盘后"))

        asyncio.run(etf_api._overview_with_prev())

        assert len(writes) == 1, "判据通过时应写入一份快照"
        rows = orjson.loads(writes[0])
        assert any(r.get("date") == date.today().isoformat() for r in rows)
