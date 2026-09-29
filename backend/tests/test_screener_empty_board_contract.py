"""P0-6 裁决：空榜终态必须区分「板块结构性无成分股」与「当日无匹配信号」。

**裁决（2026-09-22，人工指定按最优解落地）**：不采纳"`total == 0` 一律
`unavailable`"这一简单读法。理由：`total == 0` 有两种成因，混为一谈会把任意一种
误报成另一种——

* **①板块结构性无成分股**：请求板块在 `universe_daily` 快照里**一只都没有**
  （`board=bse` 未纳入数据源；实测必然命中）。本次**从未真正筛选过**，此时报
  `ok/no_matching_signals` 等于宣称"今天全市场没有符合条件的股票"，是**信息失实**
  ——这正是派单契约要求"空榜终态不能是 ok 空数组"的真实所指。
* **②池子存在、当日策略无匹配**：`09-14` 修复报告把 `no_matching_signals` 作为
  **有意设计**落地，`tests/test_data_freshness_degradation.py:111-127` 正断言它，
  前端两种状态也都区分渲染 —— 它是**真话**，一刀切改成 `unavailable` 会以
  "修复"之名制造新的失实。

故最优解 = **按可判定的事实分开报**：①`unavailable/empty_board`（新增机器码），
②保持 `ok/no_matching_signals`。判别字段是 `universe_filter.board_rows`（请求板块
在 universe 快照里的成分股数，与"当日有无 pred 信号"无关）；取不到时**不臆断**，
按②处理。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.api.v1 import screener as screener_api
from app.core.config import get_settings
from app.data.screening import BOARDS, filter_universe

TRADE_DATE = "2026-09-11"
D0 = date(2026, 9, 11)


def _universe_frame(symbols: list[str], boards: list[str] | None = None,
                    with_board_col: bool = True) -> pl.DataFrame:
    cols = {
        "symbol": symbols,
        "is_st": [False] * len(symbols),
        "is_halted": [False] * len(symbols),
        "date": [D0] * len(symbols),
    }
    if with_board_col:
        cols["board"] = boards or ["main"] * len(symbols)
    return pl.DataFrame(cols)


def _seed_universe(data: Path, uni: pl.DataFrame) -> None:
    from app.data.parquet_store import write_year_batch

    write_year_batch("universe_daily", "__all__", 2026, uni)


def _pred_frame(symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": symbols,
        "date": [D0] * len(symbols),
        "pred_score": [0.03 + i / 1000 for i in range(len(symbols))],
        "close": [10.0 + i for i in range(len(symbols))],
    })


def _base_payload(**over: object) -> dict:
    payload: dict = {
        "date": TRADE_DATE,
        "board": "bse",
        "items": [],
        "count": 0,
        "stats": {"today": {"pool_size": 0}, "prev": None, "prev_date": None},
        "universe_filter": {"applied": True, "rows": 2, "date": TRADE_DATE,
                            "reason": None, "board_rows": 0},
    }
    payload.update(over)
    return payload


# ---------------------------------------------------------------- 纯判据真值表
def test_empty_board_is_unavailable_not_no_matching_signals(monkeypatch):
    """①板块无成分股 ⇒ unavailable/empty_board（不得谎报"无匹配信号"）。"""
    monkeypatch.setattr(screener_api, "_freshness", lambda as_of: {
        "as_of": as_of, "is_stale": False, "note": "数据正常"})
    out = screener_api._finalize_screener_payload(_base_payload())
    assert out["status"] == "unavailable"
    assert out["reason"] == "empty_board"
    assert "并非" in out["message"] and "bse" in out["message"]
    # 池规模仍如实披露，客户端可自行复核"空"的成因
    assert out["stats"]["today"]["pool_size"] == 0
    assert out["universe_filter"]["board_rows"] == 0


def test_board_with_constituents_keeps_no_matching_signals(monkeypatch):
    """②池子存在但当日无匹配 ⇒ 保持 ok/no_matching_signals（有意设计，不改）。"""
    monkeypatch.setattr(screener_api, "_freshness", lambda as_of: {
        "as_of": as_of, "is_stale": False, "note": "数据正常"})
    out = screener_api._finalize_screener_payload(_base_payload(
        board="main",
        universe_filter={"applied": True, "rows": 5200, "date": TRADE_DATE,
                         "reason": None, "board_rows": 3100}))
    assert out["status"] == "ok"
    assert out["reason"] == "no_matching_signals"


def test_board_all_is_not_subject_to_empty_board_rule(monkeypatch):
    """board="all" 无"板块成分股"概念 ⇒ 不适用本裁决（board_rows 恒 None）。"""
    monkeypatch.setattr(screener_api, "_freshness", lambda as_of: {
        "as_of": as_of, "is_stale": False, "note": "数据正常"})
    out = screener_api._finalize_screener_payload(_base_payload(
        board="all",
        universe_filter={"applied": True, "rows": 5200, "date": TRADE_DATE,
                         "reason": None, "board_rows": None}))
    assert out["status"] == "ok"
    assert out["reason"] == "no_matching_signals"


def test_unknown_board_rows_does_not_fabricate(monkeypatch):
    """board_rows 未知（旧 schema / 无快照）⇒ 不臆断为"板块无成分股"。

    注意两条既有分支的优先级也必须保持不变：
      * `applied=False`（没做过过滤）⇒ P1-34 如实降级为 degraded/universe_unfiltered；
      * `applied=True` 但无 board_rows（旧快照）⇒ 保持 ok/no_matching_signals。
    两种情况都**不得**出现 empty_board。
    """
    monkeypatch.setattr(screener_api, "_freshness", lambda as_of: {
        "as_of": as_of, "is_stale": False, "note": "数据正常"})
    unfiltered = screener_api._finalize_screener_payload(_base_payload(
        universe_filter={"applied": False, "rows": 0, "date": TRADE_DATE,
                         "reason": "universe_partition_missing", "board_rows": None}))
    assert unfiltered["reason"] == "universe_unfiltered"
    assert unfiltered["status"] == "degraded"

    old_snapshot = screener_api._finalize_screener_payload(_base_payload(
        universe_filter={"applied": True, "rows": 5200, "date": TRADE_DATE,
                         "reason": None, "board_rows": None}))
    assert old_snapshot["status"] == "ok"
    assert old_snapshot["reason"] == "no_matching_signals"
    assert unfiltered.get("reason") != "empty_board"


def test_legacy_minimal_payload_unchanged(monkeypatch):
    """09-14 既有契约不被本次裁决破坏：最小载荷（无 board / 无 universe_filter）

    仍必须是 ok/no_matching_signals —— 这是"未放宽既有断言"的反向锁。
    """
    monkeypatch.setattr(screener_api, "_freshness", lambda as_of: {
        "as_of": as_of, "is_stale": False, "note": "数据正常"})
    out = screener_api._finalize_screener_payload({"date": TRADE_DATE, "items": [], "count": 0})
    assert out["status"] == "ok"
    assert out["reason"] == "no_matching_signals"


def test_missing_market_data_still_wins(monkeypatch):
    """available==0（有候选但全缺行情）仍是 unavailable/market_data_missing，优先级不变。"""
    monkeypatch.setattr(screener_api, "_freshness", lambda as_of: {
        "as_of": as_of, "is_stale": False, "note": "数据正常"})
    out = screener_api._finalize_screener_payload({
        "date": TRADE_DATE, "board": "main",
        "items": [{"symbol": "600001.SH", "close": None, "pct": None, "amount": None}],
        "universe_filter": {"applied": True, "rows": 10, "date": TRADE_DATE,
                            "reason": None, "board_rows": 8},
    })
    assert out["status"] == "unavailable"
    assert out["reason"] == "market_data_missing"
    assert out["items"] == []


# ------------------------------------------------------- filter_universe 计数本体
def test_filter_universe_counts_board_universe_rows(tmp_path, monkeypatch):
    """真实快照下：bse=0、main=2、all=None（计数与当日 pred 信号无关）。"""
    # 仓库惯例：直接改**已缓存的 Settings 对象**（32 处既有用例同款）。
    # 不要用 setenv+"cache_clear"：teardown 只还原环境变量，lru_cache 里仍留着
    # 指向 tmp_path 的 Settings ⇒ 污染同批次的后续用例（本轮实测把
    # test_api/test_alerts 的两条打成红灯）。
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path / "parquet")
    _seed_universe(tmp_path / "parquet",
                   _universe_frame(["600000.SH", "000001.SZ"], ["main", "main"]))
    pred = _pred_frame(["600000.SH"])

    bse = filter_universe(pred, TRADE_DATE, "bse")
    assert bse.pool_size == 0
    assert bse.board_universe_rows == 0, "北交所在快照里 0 只 ⇒ 从未真正筛选"

    main = filter_universe(pred, TRADE_DATE, "main")
    assert main.pool_size == 1
    assert main.board_universe_rows == 2

    allb = filter_universe(pred, TRADE_DATE, "all")
    assert allb.board_universe_rows is None, "all 不适用板块判定"
    assert "bse" in BOARDS, "bse 是合法 board 值（池恒 0 属需求，不是参数错误）"


def test_filter_universe_board_rows_without_snapshot_is_none(tmp_path, monkeypatch):
    """无快照 ⇒ None（不臆断为 0，否则会把"数据缺失"谎报成"板块无成分股"）。"""
    # 仓库惯例：直接改**已缓存的 Settings 对象**（32 处既有用例同款）。
    # 不要用 setenv+"cache_clear"：teardown 只还原环境变量，lru_cache 里仍留着
    # 指向 tmp_path 的 Settings ⇒ 污染同批次的后续用例（本轮实测把
    # test_api/test_alerts 的两条打成红灯）。
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path / "parquet")
    res = filter_universe(_pred_frame(["600000.SH"]), TRADE_DATE, "bse")
    assert res.board_universe_rows is None
    assert res.universe_ok is False


def test_filter_universe_board_rows_prefix_fallback(tmp_path, monkeypatch):
    """快照缺 board 列（旧 schema）⇒ 用 symbol 前缀兜底计数，且披露未完全生效。

    期望值由 `board_of` 现算（沪深主板同属一个板块枚举，不能硬编码 1）。
    """
    from app.data.universe import board_of

    # 仓库惯例：直接改**已缓存的 Settings 对象**（32 处既有用例同款）。
    # 不要用 setenv+"cache_clear"：teardown 只还原环境变量，lru_cache 里仍留着
    # 指向 tmp_path 的 Settings ⇒ 污染同批次的后续用例（本轮实测把
    # test_api/test_alerts 的两条打成红灯）。
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path / "parquet")
    _seed_universe(tmp_path / "parquet",
                   _universe_frame(["600000.SH", "000001.SZ"], with_board_col=False))
    board = board_of("600000")
    expected = sum(1 for s in ("600000.SH", "000001.SZ")
                   if board_of(s.split(".")[0]) == board)
    res = filter_universe(_pred_frame(["600000.SH"]), TRADE_DATE, board)
    assert res.board_universe_rows == expected
    assert res.pool_size == 1, "前缀兜底过滤必须真的生效（600000.SH 属该板块）"
    assert res.universe_ok is False, "缺 board 列 ⇒ 过滤只做了一部分，必须如实披露"


# ------------------------------------------------------------------- 端到端（真实 _screen）
def _seed_predictions(data: Path, pred: pl.DataFrame) -> None:
    d = data / "predictions"
    d.mkdir(parents=True, exist_ok=True)
    pred.write_parquet(d / f"date={D0.strftime('%Y%m%d')}.parquet")


def test_screen_bse_end_to_end_is_unavailable(tmp_path, monkeypatch):
    """端口级：同一份 pred + universe，bse 报 unavailable/empty_board 而 main 正常出榜。

    这条同时证明"空白是板块维度的事实，不是数据缺失"——若两种请求都空，则说明是
    测试环境问题而非本裁决生效。
    """
    data = tmp_path / "parquet"
    monkeypatch.setattr(get_settings(), "DATA_ROOT", data)
    _seed_universe(data, _universe_frame(["600000.SH", "000001.SZ"], ["main", "main"]))
    _seed_predictions(data, _pred_frame(["600000.SH", "000001.SZ"]))
    monkeypatch.setattr(screener_api, "_freshness", lambda as_of: {
        "as_of": as_of, "is_stale": False, "note": "数据正常"})
    monkeypatch.setattr("app.data.screening.instrument_info", lambda: {})
    monkeypatch.setattr("app.data.universe.read_prev_and_today", lambda *a, **k: {})

    body, _fv = screener_api._screen(D0, "alpha_basic_v1", 50, "bse")
    out = screener_api._finalize_screener_payload(body)
    assert out["status"] == "unavailable"
    assert out["reason"] == "empty_board"
    assert out["count"] == 0 and out["items"] == []
    assert out["universe_filter"]["board_rows"] == 0
    assert out["universe_filter"]["rows"] == 2, "universe 本身非空 ⇒ 不是数据缺失"

    body2, _fv2 = screener_api._screen(D0, "alpha_basic_v1", 50, "main")
    out2 = screener_api._finalize_screener_payload(body2)
    assert out2["count"] == 2
    assert out2["status"] == "ok", out2
    assert out2["universe_filter"]["board_rows"] == 2

    # 缺陷本体反证：把新增的 board_rows 去掉（= **修复前**的载荷形态），同一份真实
    # 载荷随即被判成 ok/no_matching_signals —— 这就是 bse 请求被谎报成"当日无匹配
    # 信号"的来源；也证明本条用例的差异**只**来自本裁决新增的判别字段。
    # ⚠️ 必须**重新取一份**载荷：`_finalize_screener_payload` 是就地改写（long-standing
    # 设计），浅拷贝 `dict(body)` 会连 `status="unavailable"` 一起带过去而在函数开头的
    # 早退分支返回（本轮实测踩到）。
    legacy, _ = screener_api._screen(D0, "alpha_basic_v1", 50, "bse")
    legacy["universe_filter"] = {k: v for k, v in legacy["universe_filter"].items()
                                 if k != "board_rows"}
    old = screener_api._finalize_screener_payload(legacy)
    assert old["status"] == "ok"
    assert old["reason"] == "no_matching_signals"


# ---------------------------------------------------------------------- 接线锁
def test_wiring_source_locks():
    """接线锁：计数必须由 universe 快照算、并端到端传到终态判定处。"""
    root = Path(__file__).resolve().parents[1]
    screening = (root / "app" / "data" / "screening.py").read_text(encoding="utf-8")
    api = (root / "app" / "api" / "v1" / "screener.py").read_text(encoding="utf-8")
    assert "board_universe_rows: int | None = None" in screening
    assert 'universe.filter(pl.col("board") == board).height' in screening
    assert "board_universe_rows=board_rows" in screening
    assert '"board_rows": res.board_universe_rows' in api
    assert '"reason": "empty_board"' in api
    assert '_board_rows == 0' in api
    # 前端：终态文案优先取后端 message（新增 empty_board 文案必须能显示到用户面前）
    fe = (root.parent / "frontend" / "src" / "pages" / "Screener" / "index.tsx").read_text(
        encoding="utf-8")
    assert "result?.message" in fe