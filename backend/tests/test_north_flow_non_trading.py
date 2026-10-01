"""缺陷 A / B 的针对性回归：**不可得状态绝不用 0 冒充**（项目红线）。

实测根因证据（2026-10-01 国庆休市，本机 live 调用，网络可用）：
    akshare 1.16.72 ``stock_hsgt_fund_flow_summary_em()`` 返回

    交易日      类型    板块        资金方向  交易状态  成交净买额  资金净流入 ...
    2026-10-01  沪港通  沪股通      北向      4        0.0        0.0
    2026-10-01  沪港通  港股通(沪)  南向      4        50.94      420.0
    2026-10-01  深港通  深股通      北向      4        0.0        0.0
    2026-10-01  深港通  港股通(深)  南向      4        17.69      420.0

即：休市日北向 ``资金净流入`` 为**占位 0.0**（同表南向仍为真实更新值），
若不读 ``交易状态``，北向会以 ``north=0.0`` 上抛并被前端渲染成红色「+0亿」。

本文件用 fixture 注入上述 DataFrame（**不依赖网络**），断言：
1. 交易状态=4（休市）⇒ ``north_net_today is None`` 且 block 为 degraded 并披露原因；
2. 未知交易状态 ⇒ **保守放行**（按真实值处理），不得误伤正常交易日；
3. 本地降级 path 的 ``total_amount_yi`` 在 amount 整列缺失时为 ``None``（非 0）。
"""
from __future__ import annotations

import pandas as pd
import polars as pl
import pytest


def _ak_stub(north_frame: pd.DataFrame):
    """构造只替换北向子源的 akshare 客户端替身（其余两源保持成功）。"""

    class _AK:
        def stock_hsgt_fund_flow_summary_em(self):
            return north_frame

        def stock_market_fund_flow(self):
            return pd.DataFrame({"主力净流入-净额": [2.5e8]})

        def stock_sector_fund_flow_rank(self, **kw):
            return pd.DataFrame({
                "名称": ["银行"],
                "超大单净流入-净额": [1e8], "大单净流入-净额": [2e7],
                "中单净流入-净额": [-5e7], "小单净流入-净额": [-7e7],
            })

    return _AK()


def _run_money_flow(monkeypatch, north_frame: pd.DataFrame) -> dict:
    import app.services.market_service as ms

    client = _ak_stub(north_frame)
    monkeypatch.setattr(ms, "get_akshare", lambda: client)
    monkeypatch.setattr(ms, "_safe_call", lambda fn, *a, **k: fn(*a, **k))
    return ms.build_money_flow()


def _closed_day_frame(status: int = 4) -> pd.DataFrame:
    """2026-10-01 休市实测表结构的等价 fixture（北向占位 0.0）。"""
    return pd.DataFrame({
        "交易日": ["2026-10-01"] * 4,
        "类型": ["沪港通", "沪港通", "深港通", "深港通"],
        "板块": ["沪股通", "港股通(沪)", "深股通", "港股通(深)"],
        "资金方向": ["北向", "南向", "北向", "南向"],
        "交易状态": [status] * 4,
        "资金净流入": [0.0, 420.0, 0.0, 420.0],
    })


# ==================== 缺陷 A：休市 0 不得当真实值 ====================

def test_closed_market_north_is_none_not_zero(monkeypatch) -> None:
    """**缺陷 A 本体**：交易状态=4（休市）⇒ 北向不可得 ⇒ ``None`` + degraded。"""
    r = _run_money_flow(monkeypatch, _closed_day_frame(status=4))

    # 修前：north == 0.0 且 status == "ok"（0 冒充真实值，前端染红）
    assert r["north_net_today"] is None, "休市 0.0 被当成了真实北向净流入"
    # 北向不可得 ⇒ n_ok 由 3 降为 2 ⇒ 必须 degraded 并披露原因
    assert r["status"] == "degraded", r
    assert r["n_ok"] == 2, r
    # `errs` 必须带**可自证口径的上下文**（不只异常类名，见
    # `market_service.py` 中 `errs.append(f"north:{e}")` 的注释）：本块的 raise 点
    # 都在消息里带了"为什么不可得"，这条 reason 会原样透传到前端，把可解释的降级
    # 压成 `north:ValueError` 等于让降级重新变得不可解释。
    assert "north" in r["reason"], r
    assert "非交易态" in r["reason"], r
    assert "交易状态=4" in r["reason"], r


def test_closed_market_reason_is_visible_in_log(monkeypatch, caplog) -> None:
    """判定依据（交易状态取值 + 语义）必须可追溯：细节进日志，不静默。"""
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(m), level="DEBUG")
    try:
        _run_money_flow(monkeypatch, _closed_day_frame(status=4))
    finally:
        logger.remove(sink_id)

    joined = "".join(records)
    assert "交易状态=4" in joined, joined
    assert "休市" in joined, joined


def test_unknown_status_is_not_treated_as_closed(monkeypatch) -> None:
    """保守策略：**未知**交易状态必须放行（按真实值处理），不得误伤正常交易日。

    ``3`` 是构造出的未登记取值 —— 代码只对已确认的不可计量态（当前仅 4）拦截，
    遇到未知值保持原行为（宁可漏判，不可把真实交易日判成不可得）。
    """
    frame = _closed_day_frame(status=3)
    frame["资金净流入"] = [12.5, 420.0, 3.5, 420.0]  # 带真实北向值的交易日
    r = _run_money_flow(monkeypatch, frame)

    assert r["status"] == "ok", r
    assert r["north_net_today"] == 16.0, r  # 12.5 + 3.5，按真实值求和


def test_status_column_absent_keeps_previous_behaviour(monkeypatch) -> None:
    """源未提供 ``交易状态`` 列时不得改变行为（保持既有 P1-33 口径）。"""
    frame = pd.DataFrame({
        "资金方向": ["北向", "北向", "南向"],
        "净流入": [1.5, 2.5, 400.0],
    })
    r = _run_money_flow(monkeypatch, frame)
    assert r["status"] == "ok", r
    assert r["north_net_today"] == 4.0, r


# ==================== 缺陷 B：本地降级成交额不得用 0 冒充 ====================

@pytest.fixture()
def _heat_env(monkeypatch, tmp_path):
    """搭一个最小本地 parquet 环境供 ``_heat_from_local`` 读取。"""
    import app.api.v1.market as mk

    class _S:
        DATA_ROOT = tmp_path

    monkeypatch.setattr(mk, "get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.data.parquet_store.today_trade_date_or_last", lambda: None,
        raising=False)
    return tmp_path


def _write_daily_bar(root, amount_all_null: bool) -> None:
    """写入最小 daily_bar 分区。

    ⚠️ **不得**预置 `prev` 列：``_heat_from_local`` 的 ``shift(1).over("symbol")``
    会直接覆盖同名列，预置列会让"最后一日的额外一行"悄悄进入求和。
    `amount` 整列写 null 时该列 dtype 为 polars ``Null``（这正是缺陷 B 的触发态）。
    """
    d = root / "daily_bar" / "symbol=600000"
    d.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "symbol": ["600000", "600000"],
        "date": [__import__("datetime").date(2026, 9, 29),
                 __import__("datetime").date(2026, 9, 30)],
        "close": [10.0, 11.0],
        "amount": [None, None] if amount_all_null else [1.0e8, 2.0e8],
    })
    df.write_parquet(d / "year=2026.snappy.parquet")


def test_local_heat_total_amount_is_none_when_amount_missing(
        monkeypatch, _heat_env) -> None:
    """**缺陷 B 本体**：amount 整列为 null ⇒ ``total_amount_yi is None``（非 0.0）。"""
    import app.api.v1.market as mk

    _write_daily_bar(_heat_env, amount_all_null=True)
    out = mk._heat_from_local()

    # 修前：sum() 返回 None，`or 0` ⇒ 0.0（前端显示「两市成交额 0 亿」）
    assert out["total_amount_yi"] is None, "不可得成交额被 0 冒充"
    assert out["status"] == "ok"  # 涨跌分布本身有效，不得整体降级


def test_local_heat_total_amount_computed_when_present(
        monkeypatch, _heat_env) -> None:
    """amount 有效时照常换算（仅最后一交易日 2e8 元 ⇒ 2.0 亿元），不误伤正常路径。"""
    import app.api.v1.market as mk

    _write_daily_bar(_heat_env, amount_all_null=False)
    out = mk._heat_from_local()
    assert out["total_amount_yi"] == 2.0, out


def test_local_heat_amount_all_null_float64_is_none_not_zero(
        monkeypatch, _heat_env) -> None:
    """**残余分支**：dtype 为 *Float64*（非 Null）但**整列全 null** 时也不得给 0。

    与上方 `amount_all_null=True` 是不同的代码路径：polars 把"整列全 null 的
    显式 Float64 列"的 `sum()` 求值为 **0.0**（而不是抛错、也不是 None）——
    仅靠 `schema != pl.Null` 守卫**挡不住**这一支，会退回"0 冒充不可得"。
    判据必须落在**有效观测数**（`len() - null_count()`）上。
    """
    import app.api.v1.market as mk

    d = _heat_env / "daily_bar" / "symbol=600001"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["600001", "600001"],
        "date": [__import__("datetime").date(2026, 9, 29),
                 __import__("datetime").date(2026, 9, 30)],
        "close": [20.0, 21.0],
        # 显式 Float64 且全 null —— 与 Null dtype 分支区分开
        "amount": pl.Series([None, None], dtype=pl.Float64),
    }).write_parquet(d / "year=2026.snappy.parquet")

    out = mk._heat_from_local()
    assert out["total_amount_yi"] is None, (
        "Float64 全 null 列的 sum() == 0.0 被当成了真实成交额（0 冒充不可得）")
