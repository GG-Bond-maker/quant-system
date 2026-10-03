"""市场概览「外盘」块回归（2026-10-04）。

被守的契约（对应用户可见风险）：
1. **每项携带自己的 ``data_date``** —— 不同市场交易日/时区不同（实测美股/日韩 10-02、
   A 股 09-30），若共用 A 股 trade_date，会把"上一交易日的外盘收盘"错标成"A 股当日"。
   这条是本块最关键、也最容易被"顺手复用 td"改坏的不变量。
2. ``pct`` 由**前收**计算（``close[-1]/close[-2]-1``），不是后端别处传入。
3. ``sparkline`` 截断到近 60 根（否则迷你走势线会画上千个点）。
4. 全失败 ⇒ ``status="unavailable"``（前端渲染「—」，不得用 0 冒充）。
5. 部分失败 ⇒ ``status="ok"`` 且 ``failed`` 如实列出失败项（单项失败不拖垮整块）。
6. **美股 3 项以腾讯报价为主、新浪 EOD 托底**（2026-10-04 用户确认的口径）：
   - 腾讯报价可信 ⇒ ``source="tencent"``、``as_of`` 为源时间戳、``close/pct`` 取报价；
     ``sparkline`` **仍**取新浪日线（腾讯不提供历史）；
   - 腾讯不可达 / 报价被守卫拒绝 ⇒ ``source="sina"``、``as_of=None``，值取日线收盘；
   - 日经225 / KOSPI **恒**为 ``source="sina"``（腾讯不覆盖）。
7. **反"僵尸源"守卫**（判据 7「错标冒充不可得」的输入侧形态）：报价时间戳早于
   EOD 的 K 线日、或数值偏离 EOD 收盘 > 20% ⇒ **拒绝覆盖**，保留 EOD。
   —— 一个"值合法、量纲正常、无报错"的陈旧报价是最危险的输入。

测试纪律：
- **不打真网络**：``_build_global_indices`` 的依赖 ``fetch_global_index_daily`` 与
  ``fetch_global_index_quotes_tencent`` 都用桩替换（二者由 market 模块 globals 查表，
  故 monkeypatch ``market.<name>`` 生效）。⚠️ 后者有 **autouse 默认桩**，否则每个
  用例都会真的去请求 qt.gtimg.cn；
- ``fetch_global_index_daily`` 自身另用假的 akshare 模块桩，验证列归一 + 走 ``_safe_call``
  （不触发真实限速：把 ``_last_call`` 归零）；
- ``fetch_global_index_quotes_tencent`` 自身用假的 ``realtime._request`` 桩验证解析。
"""
from __future__ import annotations

import importlib
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

adapter = importlib.import_module("app.data.ingest.akshare_adapter")
market = importlib.import_module("app.api.v1.market")
realtime = importlib.import_module("app.data.realtime")

ALL_SYMBOLS = [sym for _kind, sym, _name in adapter.GLOBAL_INDICES]
US_SYMBOLS = list(realtime.GLOBAL_INDEX_TENCENT_CODES)


@pytest.fixture(autouse=True)
def _no_tencent_network(monkeypatch):
    """默认把腾讯报价桩成"不可用" ⇒ 既有用例不会打真网络。

    需要腾讯报价的用例自行再次 monkeypatch（后者覆盖本 fixture）。
    """
    monkeypatch.setattr(market, "fetch_global_index_quotes_tencent", lambda symbols: {})
    yield


def _stub_tencent(monkeypatch, quotes: dict[str, dict],
                  boom: bool = False) -> None:
    """替换 ``market.fetch_global_index_quotes_tencent``。"""
    def _fetch(symbols: list[str]) -> dict[str, dict]:
        if boom:
            raise ConnectionError("stub tencent boom")
        return {s: q for s, q in quotes.items() if s in symbols}

    monkeypatch.setattr(market, "fetch_global_index_quotes_tencent", _fetch)


def _quote(price: float, as_of: str, pct: float = 0.5) -> dict:
    return {"price": price, "prev_close": price, "pct": pct, "as_of": as_of,
            "source": "tencent"}


def _frame(last_date: date, closes: list[float]) -> pl.DataFrame:
    """构造 [date, open, high, low, close, volume] 的日线帧（末日 = last_date）。"""
    n = len(closes)
    dates = [last_date - timedelta(days=n - 1 - i) for i in range(n)]
    return pl.DataFrame({
        "date": dates,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [1.0] * n,
    })


def _stub_fetch(monkeypatch, frames: dict[str, pl.DataFrame],
                boom: set[str] | None = None) -> None:
    """替换 ``market.fetch_global_index_daily``：命中 frames 返回帧，boom 抛异常。"""
    boom = boom or set()

    def _fetch(kind: str, symbol: str) -> pl.DataFrame:
        if symbol in boom:
            raise ConnectionError(f"stub boom: {symbol}")
        return frames.get(symbol, pl.DataFrame())

    monkeypatch.setattr(market, "fetch_global_index_daily", _fetch)


# ---------------------------------------------------------------------------
# 1. 每项自己的 data_date（最关键）
# ---------------------------------------------------------------------------
def test_each_item_carries_its_own_data_date(monkeypatch) -> None:
    """不同市场末日不同 ⇒ 各项 data_date 必须**各自**如实，不得共用同一日。"""
    frames = {sym: _frame(date(2026, 10, 2), [100.0, 101.0, 102.0])
              for sym in ALL_SYMBOLS}
    # 纳指故意停在 09-30（模拟美股/A 股交易日错位）
    frames[".IXIC"] = _frame(date(2026, 9, 30), [400.0, 401.0, 402.0])
    _stub_fetch(monkeypatch, frames)

    out = market._build_global_indices()

    assert out["status"] == "ok"
    by_code = {i["code"]: i for i in out["items"]}
    assert by_code[".IXIC"]["data_date"] == "2026-09-30"
    assert by_code[".DJI"]["data_date"] == "2026-10-02"
    # 反证核心：不得把 5 项压成同一个 data_date
    assert len({i["data_date"] for i in out["items"]}) == 2


# ---------------------------------------------------------------------------
# 2. pct 由前收计算
# ---------------------------------------------------------------------------
def test_pct_computed_from_prev_close(monkeypatch) -> None:
    frames = {sym: _frame(date(2026, 10, 2), [100.0, 110.0]) for sym in ALL_SYMBOLS}
    _stub_fetch(monkeypatch, frames)

    out = market._build_global_indices()

    assert out["status"] == "ok"
    for it in out["items"]:
        assert it["close"] == 110.0
        assert it["pct"] == pytest.approx(10.0)  # 110/100 - 1 = +10%


# ---------------------------------------------------------------------------
# 3. sparkline 截断到 ≤ 60
# ---------------------------------------------------------------------------
def test_sparkline_capped_at_60(monkeypatch) -> None:
    closes = [float(i) for i in range(1, 201)]  # 200 根
    frames = {sym: _frame(date(2026, 10, 2), closes) for sym in ALL_SYMBOLS}
    _stub_fetch(monkeypatch, frames)

    out = market._build_global_indices()

    assert out["status"] == "ok"
    for it in out["items"]:
        assert len(it["sparkline"]) == 60
        assert len(it["sparkline"]) <= 60


# ---------------------------------------------------------------------------
# 4. 全失败 ⇒ unavailable
# ---------------------------------------------------------------------------
def test_all_failed_returns_unavailable(monkeypatch) -> None:
    _stub_fetch(monkeypatch, {}, boom=set(ALL_SYMBOLS))

    out = market._build_global_indices()

    assert out["status"] == "unavailable"
    assert out["reason"] == "外盘指数数据源暂不可用"
    assert "items" not in out


def test_all_empty_returns_unavailable(monkeypatch) -> None:
    """空帧（数据源无数据）与异常同口径：不得报 ok + 空 items。"""
    _stub_fetch(monkeypatch, {})  # 全部落到空 DataFrame

    out = market._build_global_indices()

    assert out["status"] == "unavailable"


# ---------------------------------------------------------------------------
# 5. 部分失败 ⇒ ok + failed 列表
# ---------------------------------------------------------------------------
def test_partial_failure_ok_with_failed_list(monkeypatch) -> None:
    frames = {
        "日经225指数": _frame(date(2026, 10, 2), [100.0, 101.0]),
        # 首尔缺失 ⇒ 空帧 ⇒ failed
        ".DJI": _frame(date(2026, 10, 2), [300.0, 301.0]),
        # .IXIC 走 boom 分支
        ".INX": _frame(date(2026, 10, 2), [500.0, 501.0]),
    }
    _stub_fetch(monkeypatch, frames, boom={".IXIC"})

    out = market._build_global_indices()

    assert out["status"] == "ok"
    assert len(out["items"]) == 3
    assert len(out["failed"]) == 2
    assert any("韩国KOSPI" in f for f in out["failed"])
    assert any("纳斯达克" in f for f in out["failed"])


def test_single_bar_is_failed(monkeypatch) -> None:
    """只有 1 根 K 线无法算前收 ⇒ 该项进 failed（不得拿它当 ok）。"""
    frames = {sym: _frame(date(2026, 10, 2), [100.0, 101.0]) for sym in ALL_SYMBOLS}
    frames[".DJI"] = _frame(date(2026, 10, 2), [300.0])  # 仅 1 根
    _stub_fetch(monkeypatch, frames)

    out = market._build_global_indices()

    assert out["status"] == "ok"
    assert ".DJI" not in {i["code"] for i in out["items"]}
    assert any("道琼斯" in f for f in out["failed"])


# ---------------------------------------------------------------------------
# 6. fetch_global_index_daily 自身：列归一 + 走 _safe_call（假 akshare 桩）
# ---------------------------------------------------------------------------
class _FakeAk:
    """最小 akshare 替身：只暴露外盘用到的两个接口，记录调用。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.calls: list[tuple[str, str]] = []

    def index_us_stock_sina(self, symbol: str) -> pd.DataFrame:
        self.calls.append(("us", symbol))
        return self._df

    def index_global_hist_sina(self, symbol: str) -> pd.DataFrame:
        self.calls.append(("global", symbol))
        return self._df


def _raw_df() -> pd.DataFrame:
    # 故意用字符串数值 + 多余列，验证归一与裁剪
    return pd.DataFrame({
        "date": ["2026-09-30", "2026-10-01", "2026-10-02"],
        "open": ["1", "2", "3"],
        "high": [1.0, 2.0, 3.0],
        "low": [1.0, 2.0, 3.0],
        "close": ["10.5", "11.0", "12.25"],
        "volume": [100, 200, 300],
        "amount": [1, 2, 3],  # 多余列应被裁掉
    })


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """把限速状态归零，避免测试进程内其它用例推进的 _last_call 造成真实 sleep。"""
    monkeypatch.setattr(adapter, "_last_call", 0.0)
    adapter.reset_source_breaker()
    yield
    adapter.reset_source_breaker()


def test_fetch_global_index_daily_us_normalizes(monkeypatch) -> None:
    fake = _FakeAk(_raw_df())
    monkeypatch.setattr(adapter, "_ak", lambda: fake)

    out = adapter.fetch_global_index_daily("sina_us", ".DJI")

    assert out.columns == ["date", "open", "high", "low", "close", "volume"]
    assert out["date"].to_list() == [date(2026, 9, 30), date(2026, 10, 1),
                                     date(2026, 10, 2)]
    assert out["close"].to_list() == [10.5, 11.0, 12.25]  # 字符串 -> float
    assert fake.calls == [("us", ".DJI")]


def test_fetch_global_index_daily_global_uses_chinese_symbol(monkeypatch) -> None:
    fake = _FakeAk(_raw_df())
    monkeypatch.setattr(adapter, "_ak", lambda: fake)

    out = adapter.fetch_global_index_daily("sina_global", "日经225指数")

    assert out.height == 3
    # 走的是 global 接口、且传的是**中文全名**（传英文代码会 KeyError）
    assert fake.calls == [("global", "日经225指数")]


def test_fetch_global_index_daily_empty_returns_empty(monkeypatch) -> None:
    fake = _FakeAk(pd.DataFrame())
    monkeypatch.setattr(adapter, "_ak", lambda: fake)

    out = adapter.fetch_global_index_daily("sina_us", ".INX")

    assert out.is_empty()


def test_fetch_global_index_daily_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        adapter.fetch_global_index_daily("nope", "x")


# ---------------------------------------------------------------------------
# 7. 常量契约（防止被"顺手"改回并入 rt 的口径）
# ---------------------------------------------------------------------------
def test_global_budget_and_ttl_are_sane() -> None:
    # 预算必须覆盖 5 次串行外呼（~12s），否则每次未命中都必然超时降级
    assert market.GLOBAL_BUILD_TIMEOUT_SECONDS >= 12.0
    assert market.GLOBAL_TTL > market.RT_TTL  # 日频 >> 分钟级
    assert market.GLOBAL_STALE_WINDOW >= market.GLOBAL_TTL


def test_endpoint_is_anonymous() -> None:
    """外盘端点与 rt/daily 一致：匿名（不得引入 require_role 依赖）。"""
    path = "/overview/global"
    route: Any = next(
        (r for r in market.router.routes if getattr(r, "path", None) == path), None)
    assert route is not None, "未注册 /overview/global 路由"
    dep_names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
    assert "require_role" not in dep_names, "外盘端点不应要求鉴权"


# ---------------------------------------------------------------------------
# 8. 腾讯为主 + 新浪 EOD 托底（2026-10-04 用户确认口径）
# ---------------------------------------------------------------------------
def _us_frames(close: float = 100.0) -> dict[str, pl.DataFrame]:
    return {sym: _frame(date(2026, 10, 2), [close, close]) for sym in ALL_SYMBOLS}


def test_tencent_quote_overrides_us_headline(monkeypatch) -> None:
    """腾讯报价可信 ⇒ 覆盖 close/pct/source/as_of；sparkline 仍取新浪日线。"""
    _stub_fetch(monkeypatch, _us_frames(close=100.0))
    _stub_tencent(monkeypatch, {
        ".DJI": _quote(101.0, "2026-10-02 17:00:17", pct=1.0),
        ".IXIC": _quote(102.0, "2026-10-02 17:15:59", pct=2.0),
        ".INX": _quote(103.0, "2026-10-02 16:59:13", pct=3.0),
    })

    out = market._build_global_indices()

    assert out["status"] == "ok"
    by_code = {i["code"]: i for i in out["items"]}
    dji = by_code[".DJI"]
    assert dji["source"] == "tencent"
    assert dji["as_of"] == "2026-10-02 17:00:17"
    assert dji["close"] == 101.0          # 覆盖了日线的 100.0
    assert dji["pct"] == 1.0
    assert dji["data_date"] == "2026-10-02"
    assert len(dji["sparkline"]) == 2     # 走势线仍来自新浪日线
    # 日经/KOSPI 不在腾讯码表内 ⇒ 恒为 sina，且不得被"顺手"覆盖
    for sym in ("日经225指数", "首尔综合指数"):
        assert by_code[sym]["source"] == "sina"
        assert by_code[sym]["as_of"] is None
        assert by_code[sym]["close"] == 100.0


def test_tencent_failure_falls_back_to_sina_eod(monkeypatch) -> None:
    """腾讯请求失败**不是**本块失败：整体退回新浪 EOD，且不进 failed。"""
    _stub_fetch(monkeypatch, _us_frames(close=100.0))
    _stub_tencent(monkeypatch, {}, boom=True)

    out = market._build_global_indices()

    assert out["status"] == "ok"
    assert out["failed"] == []
    for it in out["items"]:
        assert it["source"] == "sina"
        assert it["as_of"] is None
        assert it["close"] == 100.0


def test_stale_tencent_quote_rejected(monkeypatch) -> None:
    """🔴 反僵尸源：报价时间戳**早于** EOD 的 K 线日 ⇒ 拒绝覆盖，保留 EOD。

    这正是新浪 ``int_*`` 停更一年的形态：值合法、量纲正常、无报错。
    """
    _stub_fetch(monkeypatch, _us_frames(close=100.0))
    _stub_tencent(monkeypatch, {
        ".DJI": _quote(101.0, "2025-09-26 17:00:00"),   # 早于 2026-10-02
    })

    out = market._build_global_indices()
    dji = {i["code"]: i for i in out["items"]}[".DJI"]

    assert dji["source"] == "sina", "陈旧报价不得覆盖日线收盘"
    assert dji["as_of"] is None
    assert dji["close"] == 100.0
    assert dji["data_date"] == "2026-10-02"


def test_outlier_tencent_quote_rejected(monkeypatch) -> None:
    """🔴 报价偏离 EOD 收盘 > 20% ⇒ 判为串号/异常，拒绝覆盖。"""
    _stub_fetch(monkeypatch, _us_frames(close=100.0))
    _stub_tencent(monkeypatch, {
        ".DJI": _quote(150.0, "2026-10-02 17:00:17"),   # +50%
    })

    out = market._build_global_indices()
    dji = {i["code"]: i for i in out["items"]}[".DJI"]

    assert dji["source"] == "sina"
    assert dji["close"] == 100.0


def test_tencent_newer_date_moves_data_date(monkeypatch) -> None:
    """报价日比日线新（美股盘中/日线未落）⇒ data_date 随报价前移，仍逐项如实。"""
    _stub_fetch(monkeypatch, {sym: _frame(date(2026, 10, 1), [100.0, 100.0])
                              for sym in ALL_SYMBOLS})
    _stub_tencent(monkeypatch, {".DJI": _quote(100.5, "2026-10-02 17:00:17")})

    out = market._build_global_indices()
    dji = {i["code"]: i for i in out["items"]}[".DJI"]

    assert dji["data_date"] == "2026-10-02"
    assert dji["as_of"] == "2026-10-02 17:00:17"


def test_tencent_quote_without_as_of_rejected(monkeypatch) -> None:
    """缺时间戳的报价无法证明"不陈旧" ⇒ 拒绝（宁可要可证的 EOD）。"""
    _stub_fetch(monkeypatch, _us_frames(close=100.0))
    _stub_tencent(monkeypatch, {".DJI": {"price": 101.0, "prev_close": 100.0,
                                         "pct": 1.0, "as_of": None}})

    out = market._build_global_indices()
    dji = {i["code"]: i for i in out["items"]}[".DJI"]

    assert dji["source"] == "sina"
    assert dji["close"] == 100.0


def test_tencent_quote_missing_pct_keeps_eod_pct(monkeypatch) -> None:
    """报价有价无 pct ⇒ close 用报价、pct 保留日线口径（不得写 null/0 冒充）。"""
    _stub_fetch(monkeypatch, _us_frames(close=100.0))
    _stub_tencent(monkeypatch, {".DJI": {"price": 101.0, "prev_close": 100.0,
                                         "pct": None, "as_of": "2026-10-02 17:00:17"}})

    out = market._build_global_indices()
    dji = {i["code"]: i for i in out["items"]}[".DJI"]

    assert dji["close"] == 101.0
    assert dji["pct"] == 0.0        # 日线 100→100
    assert dji["source"] == "tencent"


def test_tencent_code_map_covers_only_us() -> None:
    """腾讯码表只应收录美股 3 项（日经/KOSPI 腾讯不覆盖 ⇒ 收录即造数）。"""
    assert set(realtime.GLOBAL_INDEX_TENCENT_CODES) == {".DJI", ".IXIC", ".INX"}


# ---------------------------------------------------------------------------
# 9. fetch_global_index_quotes_tencent 解析（假 _request，不打真网络）
# ---------------------------------------------------------------------------
def _tencent_row(tcode: str, name: str, price: str, prev: str, pct: str,
                 as_of: str, *, fields: int = 36) -> str:
    """按实测字段布局拼一行（1=名称 2=symbol 3=现价 4=昨收 30=时间 32=涨跌%）。"""
    f = [""] * fields
    f[0], f[1], f[2], f[3], f[4], f[30], f[32] = "200", name, tcode, price, prev, as_of, pct
    return f'v_{tcode}="' + "~".join(f) + '";\n'


def test_parse_tencent_global_quotes(monkeypatch) -> None:
    text = (
        _tencent_row("usDJI", "道琼斯", "51176.96", "50926.56", "0.49",
                     "2026-10-02 17:00:17")
        + _tencent_row("usIXIC", "纳斯达克", "27190.86", "26871.60", "1.19",
                       "2026-10-02 17:15:59")
    )
    monkeypatch.setattr(realtime, "_request", lambda *a, **k: text)

    out = realtime.fetch_global_index_quotes_tencent([".DJI", ".IXIC", ".INX"])

    assert set(out) == {".DJI", ".IXIC"}      # .INX 未返回 ⇒ 如实缺报
    assert out[".DJI"]["price"] == 51176.96
    assert out[".DJI"]["prev_close"] == 50926.56
    assert out[".DJI"]["pct"] == 0.49
    assert out[".DJI"]["as_of"] == "2026-10-02 17:00:17"
    assert out[".IXIC"]["as_of"] == "2026-10-02 17:15:59"


def test_parse_tencent_global_skips_unmapped_and_empty(monkeypatch) -> None:
    """未收录 symbol（日经/KOSPI）与"占位空行"都如实跳过，不得造数。"""
    text = (
        _tencent_row("usDJI", "道琼斯", "", "50926.56", "0.49", "2026-10-02 17:00:17")
        + 'v_usINX="";\n'                       # 空 body
        + 'v_jpNI225="";\n'                     # 腾讯不覆盖日经
    )
    monkeypatch.setattr(realtime, "_request", lambda *a, **k: text)

    out = realtime.fetch_global_index_quotes_tencent(
        [".DJI", ".INX", "日经225指数"])

    assert out == {}


def test_parse_tencent_global_unmapped_only_returns_empty_without_request(
        monkeypatch) -> None:
    """只传未收录 symbol ⇒ 不发起任何 HTTP 请求（空 dict 短路）。"""
    def _boom(*a, **k):
        raise AssertionError("不应发起请求")

    monkeypatch.setattr(realtime, "_request", _boom)

    assert realtime.fetch_global_index_quotes_tencent(["日经225指数", "首尔综合指数"]) == {}


def test_parse_tencent_global_bad_format_raises(monkeypatch) -> None:
    monkeypatch.setattr(realtime, "_request", lambda *a, **k: "<html>nope</html>")

    with pytest.raises(RuntimeError):
        realtime.fetch_global_index_quotes_tencent([".DJI"])
