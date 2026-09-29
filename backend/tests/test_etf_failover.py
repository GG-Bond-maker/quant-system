"""ETF 日线取数修复回归（2026-09-26）：adjust 门控 + 「空 / 故障」分离 + 多源降级。

真 bug（``app/api/v1/datacenter.py::_fetch_etf_bar``）：原实现只有东财一个源，
且 ``except Exception: ... return pl.DataFrame()`` **把异常静默吞成空表**。后果：

1. 东财断连/限流时，ETF 全天无数据却**既不报错、也不降级**，任务显示"完成"，
   ``_sync.failed`` 里没有任何记录 ⇒ 用户以为抓取成功，实际落库为空。
2. 即便只看东财，接口异常与"停牌/无数据"在返回值上**不可区分**（都是空表），
   下游无法据实区分"该标的今日无行情"与"数据源故障"。

修法（对齐 ``akshare_adapter.fetch_daily_bar`` 的多源链语义）：

    源1 东方财富 ``fund_etf_hist_em``（``adjust=""`` / ``"qfq"`` / ``"hfq"`` 均支持）；
    源2 新浪 ``fund_etf_hist_sina``（**仅当** ``adjust==""``，且需按 [start,end] 闭区间裁剪）。

    * **空**：某适用源成功但无数据 ⇒ 记 ``saw_empty``，最终返回空表（不算故障）；
    * **故障**：全部**适用**源都以异常失败 ⇒ 抛 ``DataSourceUnavailable``（**绝不吞**）
      ⇒ ``fetch_and_write_daily_bars`` 记入 ``failed`` ⇒ ``_run_fetch`` 计入
      ``_sync.failed``（而非 ``completed``）。

adjust × 场景真值表（``_run_fetch`` 只传 ``adjusts=("", "hfq")``）：

    ┌────────┬──────────────────────────┬───────────────────────────────────────┐
    │ adjust │ 适用源                   │ 结果                                  │
    ├────────┼──────────────────────────┼───────────────────────────────────────┤
    │ ""     │ 东财 → 新浪              │ 东财非空即返；否则试新浪；两者皆空返空; │
    │        │                          │ 两源皆异常 ⇒ 抛 DataSourceUnavailable  │
    │ "hfq"  │ 东财（新浪不适用）       │ 东财非空即返；东财空 ⇒ 返空;           │
    │        │                          │ 东财异常 ⇒ 抛 DataSourceUnavailable    │
    └────────┴──────────────────────────┴───────────────────────────────────────┘

测试纪律：**全部 monkeypatch**（替换 akshare 的 ``fund_etf_hist_em`` /
``fund_etf_hist_sina`` 两个外呼接口为可编程替身），**绝不发真实网络**；
被测函数 ``_fetch_etf_bar`` 本身不做任何打桩。
"""
from __future__ import annotations

import sys
from pathlib import Path

import akshare as ak
import loguru
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import datacenter as dc  # noqa: E402
from app.core.errors import DataSourceUnavailable  # noqa: E402
from app.data.ingest.tasks import fetch_and_write_daily_bars  # noqa: E402

START = "2024-01-01"
END = "2024-01-31"


# ---------------------------------------------------------------------------
# 测试替身：东财（中文列名）/ 新浪（英文列名）各返回一份**真实结构**的 DataFrame
# ---------------------------------------------------------------------------
def _em_df(dates: list[str]) -> pd.DataFrame:
    """东财 ``fund_etf_hist_em`` 形态（中文列名，与股票东财接口同构）。"""
    n = len(dates)
    return pd.DataFrame({
        "日期": dates,
        "开盘": [1.00] * n, "收盘": [1.10] * n, "最高": [1.20] * n, "最低": [0.90] * n,
        "成交量": [100.0] * n, "成交额": [1000.0] * n, "振幅": [1.00] * n,
        "涨跌幅": [0.50] * n, "涨跌额": [0.10] * n, "换手率": [0.30] * n,
    })


def _sina_df(dates: list[str]) -> pd.DataFrame:
    """新浪 ``fund_etf_hist_sina`` 形态（英文列名、**不复权**全量历史）。"""
    n = len(dates)
    return pd.DataFrame({
        "date": dates, "open": [1.00] * n, "high": [1.20] * n,
        "low": [0.90] * n, "close": [1.10] * n, "volume": [100.0] * n,
    })


@pytest.fixture()
def net(monkeypatch):
    """把 akshare 的两个 ETF 外呼接口换成可编程替身。

    返回 ``(calls, state)``：
      * ``calls["em"] / calls["sina"]`` 记录每次调用的 kwargs（用于断言"是否被调"）；
      * ``state["em"] / state["sina"]`` 由用例赋值，可为 DataFrame（正常/空响应）
        或 Exception 实例（该源抛异常）；``None`` 表示"本不该被调用"。
    """
    calls: dict[str, list[dict]] = {"em": [], "sina": []}
    state: dict[str, object] = {"em": None, "sina": None}

    def _make(key: str):
        def _fn(**kwargs):
            calls[key].append(kwargs)
            val = state[key]
            if isinstance(val, BaseException):
                raise val
            return val
        return _fn

    monkeypatch.setattr(ak, "fund_etf_hist_em", _make("em"))
    monkeypatch.setattr(ak, "fund_etf_hist_sina", _make("sina"))
    return calls, state


# ---------------------------------------------------------------------------
# T1-T8：_fetch_etf_bar 的 adjust × 场景真值表
# ---------------------------------------------------------------------------
def test_t1_source1_eastmoney_success(net):
    """T1 主源成功（adjust=""）：返回东财行，不触发备源。"""
    calls, state = net
    state["em"] = _em_df(["2024-01-02", "2024-01-03"])
    out = dc._fetch_etf_bar("159915", START, END, "")
    assert out.height == 2
    assert set(out["symbol"].to_list()) == {"159915.SZ"}
    assert set(out["source"].to_list()) == {"akshare"}
    assert len(calls["em"]) == 1
    assert len(calls["sina"]) == 0


def test_t2_source2_sina_fallback_closed_trim(net):
    """T2 备源降级（adjust=""）：东财空 -> 新浪，且按 [start,end] 闭区间裁剪。"""
    calls, state = net
    state["em"] = pd.DataFrame()  # 主源成功但空
    state["sina"] = _sina_df(["2023-12-29", "2024-01-02", "2024-01-15", "2024-02-01"])
    out = dc._fetch_etf_bar("159915", START, END, "")
    assert sorted(d.isoformat() for d in out["date"].to_list()) == \
        ["2024-01-02", "2024-01-15"]  # 区间外两条被裁掉
    assert set(out["symbol"].to_list()) == {"159915.SZ"}
    assert set(out["source"].to_list()) == {"akshare"}
    assert len(calls["em"]) == 1 and len(calls["sina"]) == 1
    assert calls["sina"][0]["symbol"] == "sz159915"  # market_prefix 委托（深市 ETF）


def test_t3_hfq_gate_sina_not_used_and_raise(net):
    """T3 复权门控（adjust="hfq"）：新浪**不适用**；东财异常 -> 抛 DataSourceUnavailable。

    即便新浪此时"有数据"，也绝不能把【不复权】冒充【后复权】降级落库。
    """
    calls, state = net
    state["em"] = RuntimeError("em boom")
    state["sina"] = _sina_df(["2024-01-02"])  # 有数据也不得被调用
    with pytest.raises(DataSourceUnavailable):
        dc._fetch_etf_bar("159915", START, END, "hfq")
    assert len(calls["em"]) == 1
    assert len(calls["sina"]) == 0


def test_t4_both_sources_empty_returns_empty_no_raise(net):
    """T4 空响应分离（adjust=""）：两适用源皆成功但空 -> 返回空表，**不抛**。"""
    calls, state = net
    state["em"] = pd.DataFrame()
    state["sina"] = pd.DataFrame()
    out = dc._fetch_etf_bar("159915", START, END, "")
    assert out.is_empty()
    assert len(calls["em"]) == 1 and len(calls["sina"]) == 1


def test_t5_all_applicable_sources_raise(net):
    """T5 故障分离（adjust=""）：两适用源皆异常 -> 抛 DataSourceUnavailable。"""
    calls, state = net
    state["em"] = RuntimeError("em boom")
    state["sina"] = RuntimeError("sina boom")
    with pytest.raises(DataSourceUnavailable):
        dc._fetch_etf_bar("159915", START, END, "")
    assert len(calls["em"]) == 1 and len(calls["sina"]) == 1


def test_t6_hfq_eastmoney_success_sina_unused(net):
    """T6 复权主源成功（adjust="hfq"）：返回东财行，不触发新浪。"""
    calls, state = net
    state["em"] = _em_df(["2024-01-02", "2024-01-03"])
    out = dc._fetch_etf_bar("159915", START, END, "hfq")
    assert out.height == 2
    assert set(out["symbol"].to_list()) == {"159915.SZ"}
    assert len(calls["sina"]) == 0


def test_t7_hfq_empty_returns_empty_no_raise(net):
    """T7 复权空响应（adjust="hfq"）：东财空 -> 返回空表，不抛，新浪不适用。"""
    calls, state = net
    state["em"] = pd.DataFrame()
    out = dc._fetch_etf_bar("159915", START, END, "hfq")
    assert out.is_empty()
    assert len(calls["sina"]) == 0


def test_t8_em_fault_falls_back_and_warns(net):
    """T8 不吞异常（adjust=""）：东财异常但新浪有数据 -> 降级返回新浪行 + 结构化 WARNING。"""
    calls, state = net
    state["em"] = RuntimeError("em boom")
    state["sina"] = _sina_df(["2024-01-02", "2024-01-03"])
    msgs: list[str] = []
    sink = loguru.logger.add(
        lambda m: msgs.append(str(m)), level="WARNING", format="{message}")
    try:
        out = dc._fetch_etf_bar("159915", START, END, "")
    finally:
        loguru.logger.remove(sink)
    assert out.height == 2  # 未被吞成空表 -> 降级成功
    assert set(out["symbol"].to_list()) == {"159915.SZ"}
    joined = "\n".join(msgs)
    assert "source=eastmoney" in joined and "159915" in joined and "adjust=''" in joined


# ---------------------------------------------------------------------------
# T9：抛点在 fetcher 层 -> 上游按「抓取故障」计（而非「完成」）
# ---------------------------------------------------------------------------
def test_t9_fault_flows_into_failed_not_completed(net):
    """T9 集成：fetcher 抛出 -> fetch_and_write_daily_bars 记入 failed（供 _run_fetch
    计入 _sync.failed）。``_run_fetch`` 只传 adjusts=("", "hfq")，故两口径皆故障。"""
    calls, state = net
    state["em"] = RuntimeError("boom")
    state["sina"] = RuntimeError("boom")
    total, failed = fetch_and_write_daily_bars(
        "159915", START, END, adjusts=("", "hfq"), fetcher=dc._fetch_etf_bar)
    assert total == 0
    assert failed == ["", "hfq"]  # 两口径都因全部源失败被记为故障
    assert len(calls["em"]) == 2  # "" 与 "hfq" 各调一次东财
    assert len(calls["sina"]) == 1  # 仅 "" 口径适用新浪


# ---------------------------------------------------------------------------
# 回归：ETF 落库 symbol 由 _standardize_daily 决定，深市 1xxxxx 必须 .SZ
# ---------------------------------------------------------------------------
def test_regression_etf_symbol_is_sz():
    """回归：``159915`` -> ``159915.SZ``（历史 else 分支曾静默拼成不存在的 .SH）。

    symbol 由 ``akshare_adapter._standardize_daily`` 无条件按 ``code_to_symbol(code)``
    决定，与新浪/东财外呼用的 ``sz``/``sh`` 前缀无关；本用例锚定 **ETF** 落地口径。
    """
    from app.data.ingest.akshare_adapter import _standardize_daily
    out = _standardize_daily(_em_df(["2024-01-02"]), "159915")
    assert out["symbol"].to_list() == ["159915.SZ"]
    assert out["code"].to_list() == ["159915"]
