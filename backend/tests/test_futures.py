"""期货扩展与 CTA 多空回测/基差对冲单元测试。"""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import futures as futures_api
from app.core.auth import create_jwt_token
from app.data import futures as futures_data
from app.data.futures import (
    get_futures_instrument_profile,
    get_futures_term_structure,
    get_inter_commodity_spread,
    get_parameter_sensitivity,
    run_futures_cta_backtest,
)
from app.domain.futures_rules import (
    calc_annualized_basis_rate,
    calc_basis,
    calc_basis_rate,
    calc_continuous_contract,
    calc_contract_value,
    calc_futures_cost,
    calc_hedge_contracts,
    calc_margin,
    calc_underwater_drawdowns,
    get_futures_spec,
)
from app.main import app


def _fixture_bars(count: int = 180) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    current = date(2025, 1, 2)
    while len(bars) < count:
        if current.weekday() < 5:
            index = len(bars)
            close = 4000.0 + index * 0.8 + math.sin(index / 4.0) * 45.0
            bars.append(
                {
                    "date": current.isoformat(),
                    "open": round(close - 3.0, 2),
                    "high": round(close + 8.0, 2),
                    "low": round(close - 8.0, 2),
                    "close": round(close, 2),
                    "volume": 100_000 + index * 100,
                    "open_interest": 200_000 + index * 50,
                }
            )
        current += timedelta(days=1)
    return bars


def test_futures_specs_and_rules():
    """验证核心期货合约规格与单源数学公式。"""
    spec_if = get_futures_spec("IF2412")
    assert spec_if is not None
    assert spec_if.symbol_prefix == "IF"
    assert spec_if.multiplier == 300.0
    assert spec_if.margin_rate == 0.12

    spec_au = get_futures_spec("AU")
    assert spec_au is not None
    assert spec_au.multiplier == 1000.0
    assert spec_au.category == "precious"

    assert get_futures_spec("600519.SH") is None

    # 名义本金市值
    val = calc_contract_value(price=4000.0, qty=2, multiplier=300.0)
    assert val == 2_400_000.0

    # 保证金测算
    margin = calc_margin(price=4000.0, qty=1, multiplier=300.0, margin_rate=0.12)
    assert margin == 144_000.0

    # 基差与年化升贴水
    basis = calc_basis(spot_price=4020.0, futures_price=4000.0)
    assert basis == 20.0
    basis_pct = calc_basis_rate(spot_price=4000.0, futures_price=3960.0)
    assert basis_pct == 1.0
    ann_rate = calc_annualized_basis_rate(spot_price=4000.0, futures_price=3960.0, days_to_expiry=36.5)
    assert round(ann_rate, 1) == 10.0

    # 股指对冲手数测算
    hedge_lots = calc_hedge_contracts(portfolio_value=12_000_000.0, futures_price=4000.0, multiplier=300.0, beta=1.0)
    assert hedge_lots == 10


def test_underwater_drawdown_breakdown():
    """验证从区间最高点计算的回测表（Drawdown from Peak Breakdown Table）。"""
    equity = [100.0, 110.0, 120.0, 105.0, 90.0, 115.0, 125.0, 118.0]
    dates = [
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
        "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"
    ]

    events, curr_dd, max_dd = calc_underwater_drawdowns(equity, dates, top_n=5)
    assert len(events) >= 1

    # 第一个主要回撤：从 120.0 (2026-01-03) 跌到 90.0 (2026-01-05)，在 2026-01-07 修复到 125.0
    first_ev = events[0]
    assert first_ev["peak_date"] == "2026-01-03"
    assert first_ev["peak_value"] == 120.0
    assert first_ev["trough_date"] == "2026-01-05"
    assert first_ev["trough_value"] == 90.0
    assert first_ev["max_drawdown_pct"] == -25.0
    assert first_ev["recovery_date"] == "2026-01-07"
    assert first_ev["is_recovered"] is True

    # 当前回撤：125.0 跌到 118.0 => -5.6%
    assert curr_dd < 0


def test_futures_cta_backtest_dual_ma(monkeypatch):
    """验证期货 CTA 双向交易与做空盈利回测引擎。"""
    monkeypatch.setattr(
        futures_data,
        "get_futures_kline",
        lambda *args, **kwargs: _fixture_bars(),
    )
    monkeypatch.setattr(futures_data, "futures_trade_status", lambda *args: (True, "ok"))
    res = run_futures_cta_backtest(
        symbol_prefix="IF",
        strategy_type="dual_ma",
        init_cash=1_000_000.0,
        leverage=1.5,
        fast_period=5,
        slow_period=20,
        direction="both",
    )
    assert res["status"] == "ok"
    assert "equity_curve" in res
    assert "benchmark_curve" in res
    assert "drawdown_curve" in res
    assert "top_drawdowns" in res
    assert "long_stats" in res
    assert "short_stats" in res
    assert "sortino_ratio" in res
    assert "trades" in res
    assert len(res["trades"]) > 0
    assert "margin_ratio_curve" in res
    assert "peak_margin_ratio" in res
    assert len(res["equity_curve"]) == len(res["dates"])
    assert res["available"] is True
    assert res["modeled"] is True
    assert res["data_status"] == "ok"
    assert res["input_start_date"] == res["dates"][0]
    assert res["input_end_date"] == res["dates"][-1]



def test_futures_hedge_backtest(monkeypatch):
    """验证股指期货 Beta 对冲模式。"""
    monkeypatch.setattr(
        futures_data,
        "get_futures_kline",
        lambda *args, **kwargs: _fixture_bars(),
    )
    monkeypatch.setattr(futures_data, "futures_trade_status", lambda *args: (True, "ok"))
    res = run_futures_cta_backtest(
        symbol_prefix="IF",
        strategy_type="hedge",
        init_cash=1_000_000.0,
        leverage=1.0,
        beta=1.0,
    )
    assert res["status"] == "ok"
    assert res["strategy_type"] == "hedge"


def test_futures_cta_rejects_unknown_or_disabled_symbol_before_reading_bars(monkeypatch):
    called = False

    def unexpected_read(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled futures must not read another symbol's bars")

    monkeypatch.setattr(futures_data, "get_futures_kline", unexpected_read)

    res = run_futures_cta_backtest(symbol_prefix="PB")

    assert res == {"status": "error", "message": "品种未启用或不存在"}
    assert called is False


def test_futures_cta_rejects_non_tradable_cache_with_reason(monkeypatch):
    monkeypatch.setattr(
        futures_data,
        "futures_trade_status",
        lambda *args: (False, "品种数据质量状态为 failed"),
    )
    monkeypatch.setattr(
        futures_data,
        "get_futures_kline",
        lambda *args, **kwargs: pytest.fail("non-tradable futures must not be backtested"),
    )

    res = run_futures_cta_backtest(symbol_prefix="IF")

    assert res == {"status": "error", "message": "品种数据质量状态为 failed"}


def test_futures_cta_forwards_dates_and_full_history_request(monkeypatch):
    bars = _fixture_bars()
    captured: dict[str, object] = {}

    def read_history(*args, **kwargs):
        captured.update(kwargs)
        return bars

    monkeypatch.setattr(futures_data, "get_futures_kline", read_history)
    monkeypatch.setattr(futures_data, "futures_trade_status", lambda *args: (True, "ok"))

    res = run_futures_cta_backtest(
        symbol_prefix="IF",
        days=None,
        start_date=bars[20]["date"],
        end_date=bars[-1]["date"],
    )

    assert res["status"] == "ok"
    assert captured["days"] is None
    assert captured["start_date"] == bars[20]["date"]
    assert captured["end_date"] == bars[-1]["date"]


def test_futures_cta_uses_contract_specific_open_close_fee_rules(monkeypatch):
    captured: list[dict[str, object]] = []
    spec = get_futures_spec("IF")
    assert spec is not None

    def record_fee(**kwargs):
        captured.append(kwargs)
        return 0.0

    monkeypatch.setattr(
        futures_data,
        "get_futures_kline",
        lambda *args, **kwargs: _fixture_bars(),
    )
    monkeypatch.setattr(futures_data, "futures_trade_status", lambda *args: (True, "ok"))
    monkeypatch.setattr(futures_data, "calc_futures_cost", record_fee)

    res = run_futures_cta_backtest(symbol_prefix="IF")

    assert res["status"] == "ok"
    assert captured
    assert all(call["fee_open"] is spec.fee_open for call in captured)
    assert all(call["fee_close"] is spec.fee_close for call in captured)
    assert all(call["fee_close_today"] is spec.fee_close_today for call in captured)


def test_continuous_contract_marks_heuristic_in_both_branches():
    no_roll_bars = [
        {
            "date": "2020-01-02",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        },
        {
            "date": "2020-01-03",
            "open": 100.2,
            "high": 101.2,
            "low": 99.2,
            "close": 100.5,
        },
    ]
    roll_bars = [
        {
            "date": "2020-01-02",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        },
        {
            "date": "2020-01-03",
            "open": 110.0,
            "high": 111.0,
            "low": 109.0,
            "close": 110.0,
        },
    ]

    no_roll = calc_continuous_contract(no_roll_bars)
    rolled = calc_continuous_contract(
        roll_bars,
        roll_dates=[("2020-01-03", 100.0, 110.0)],
    )

    assert all(bar["roll_detection"] == "heuristic" for bar in no_roll)
    assert all(bar["roll_detection"] == "heuristic" for bar in rolled)


def test_futures_api_endpoints(monkeypatch):
    """测试期货中心与分析全部 API 接口契约。"""
    bars = _fixture_bars(700)

    def read_history(symbol, *, days=500, continuous=False, start_date=None, end_date=None):
        selected = bars
        if start_date is not None:
            selected = [bar for bar in selected if bar["date"] >= start_date]
        if end_date is not None:
            selected = [bar for bar in selected if bar["date"] <= end_date]
        if days is not None:
            selected = selected[-days:]
        if continuous:
            return calc_continuous_contract(selected)
        return selected

    monkeypatch.setattr(futures_api, "get_futures_kline", read_history)
    monkeypatch.setattr(futures_api, "futures_trade_status", lambda *args: (True, "ok"))
    monkeypatch.setattr(futures_data, "get_futures_kline", read_history)
    monkeypatch.setattr(futures_data, "futures_trade_status", lambda *args: (True, "ok"))
    monkeypatch.setattr(
        futures_data,
        "fetch_sina_futures_quotes",
        lambda symbols=None: {
            "IF": {
                "symbol": "IF主力",
                "close": 4000.0,
                "change_pct": 0.5,
                "volume": 120_000,
                "open_interest": 220_000,
                "source": "sina",
            }
        },
    )

    client = TestClient(app)
    token, _ = create_jwt_token("test-researcher", "researcher")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. 主力合约全景看板
    r = client.get("/api/v1/futures/contracts", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 0
    assert data["data"]["count"] > 0

    # 2. 基差看板
    r = client.get("/api/v1/futures/basis?category=index", headers=headers)
    assert r.status_code == 200
    assert r.json()["code"] == 0

    # 3. 期限结构
    r = client.get("/api/v1/futures/term-structure?symbol=IF", headers=headers)
    assert r.status_code == 200
    ts_data = r.json()["data"]
    assert ts_data["available"] is False
    assert ts_data["modeled"] is False

    # 4. 日 K 线
    r = client.get("/api/v1/futures/kline?symbol=IF&days=60", headers=headers)
    assert r.status_code == 200
    k_data = r.json()["data"]
    assert len(k_data) == 60

    # 5. CTA 回测
    r = client.post(
        "/api/v1/futures/backtest",
        headers=headers,
        json={
            "symbol": "IF",
            "strategy_type": "dual_ma",
            "init_cash": 1000000.0,
            "leverage": 1.0,
            "fast_period": 5,
            "slow_period": 20,
            "direction": "both",
        },
    )
    assert r.status_code == 200
    bt_data = r.json()
    assert bt_data["code"] == 0
    assert "top_drawdowns" in bt_data["data"]

    # 6. 对冲与保证金计算器
    r = client.get(
        "/api/v1/futures/hedge-calc?portfolio_value=10000000&futures_price=3950&symbol=IF&beta=1.0",
        headers=headers,
    )
    assert r.status_code == 200
    calc_data = r.json()["data"]
    assert calc_data["required_contracts"] > 0
    assert calc_data["total_margin_required"] > 0

    # 7. 跨期套利价差与 Z-Score
    r = client.get("/api/v1/futures/calendar-spread?symbol=IF&window=20", headers=headers)
    assert r.status_code == 200
    spread_data = r.json()["data"]
    assert spread_data["available"] is False
    assert spread_data["modeled"] is False

    # 8. 复权连续合约
    r = client.get("/api/v1/futures/kline?symbol=IF&days=60&continuous=true", headers=headers)
    assert r.status_code == 200
    cont_bars = r.json()["data"]
    assert len(cont_bars) == 60
    assert "is_continuous" in cont_bars[0]

    # 9. 主力席位持仓龙虎榜 (COT)
    r = client.get("/api/v1/futures/cot?symbol=IF", headers=headers)
    assert r.status_code == 200
    cot_data = r.json()["data"]
    assert cot_data["available"] is False
    assert cot_data["modeled"] is False

    # 10. 跨品种比价与产业链套利
    r = client.get("/api/v1/futures/inter-commodity?pair=AU_AG", headers=headers)
    assert r.status_code == 200
    inter_data = r.json()["data"]
    assert "current_z_score" in inter_data
    assert "half_life_stats" in inter_data
    assert len(inter_data["series"]) > 0

    # 11. 全市场展期收益率矩阵天梯榜
    r = client.get("/api/v1/futures/roll-yield-matrix", headers=headers)
    assert r.status_code == 200
    ry_data = r.json()["data"]
    assert ry_data["available"] is False
    assert ry_data["modeled"] is False

    # 12. 历史基差时序与分位数雷达
    r = client.get("/api/v1/futures/basis-history?symbol=IF&days=60", headers=headers)
    assert r.status_code == 200
    bh_data = r.json()["data"]
    assert bh_data["available"] is False
    assert bh_data["modeled"] is False

    # 13. 单品种微观画像与量价+OI
    r = client.get("/api/v1/futures/instrument-profile?symbol=IF&days=60", headers=headers)
    assert r.status_code == 200
    prof_data = r.json()["data"]
    assert prof_data["symbol_prefix"] == "IF"
    assert "contract_value" in prof_data
    assert "margin_per_lot" in prof_data
    assert "volatility_stats" in prof_data
    assert "open_interest_stats" in prof_data
    assert len(prof_data["kline"]) == 60

    # 13.1 单品种全历史画像（days=2500）
    r_full = client.get("/api/v1/futures/instrument-profile?symbol=IF&days=2500", headers=headers)
    assert r_full.status_code == 200
    prof_full = r_full.json()["data"]
    assert len(prof_full["kline"]) > 500

    # 14. 参数敏感性二维热力图
    r = client.get("/api/v1/futures/param-sensitivity?symbol=IF&strategy_type=dual_ma", headers=headers)
    assert r.status_code == 200
    sens_data = r.json()["data"]
    assert "grid" in sens_data
    assert len(sens_data["grid"]) > 0
    assert "optimal_params" in sens_data


def test_dynamic_delivery_date_and_roll_yield():
    """验证动态交割日计算与展期收益率纯函数。"""
    from datetime import date

    from app.domain.futures_rules import (
        calc_days_to_expiry,
        calc_roll_yield,
        get_contract_delivery_date,
    )

    # 2026年10月，第1天是周四(3)，首个周五是10月2日，第三个周五是 10月16日
    d_if = get_contract_delivery_date("IF", 2026, 10)
    assert d_if == date(2026, 10, 16)

    # 商品期货：10月15日是周四，所以交割日就是 10月15日
    d_rb = get_contract_delivery_date("RB", 2026, 10)
    assert d_rb == date(2026, 10, 15)

    # DTE 计算
    dte = calc_days_to_expiry(d_if, as_of=date(2026, 10, 6))
    assert dte == 10

    # 展期收益率 (近月 4000，远月 3960，贴水 40 点)
    ry = calc_roll_yield(near_price=4000.0, far_price=3960.0, near_dte=10, far_dte=100)
    assert ry > 0


def test_instrument_profile_and_param_sensitivity_direct(monkeypatch):
    """验证标的画像与参数敏感性数据函数。"""
    def read_history(symbol, *, days=500, **kwargs):
        bars = _fixture_bars()
        return bars[-days:] if days is not None else bars

    monkeypatch.setattr(futures_data, "get_futures_kline", read_history)

    prof = get_futures_instrument_profile("IF", days=60)
    assert prof["symbol_prefix"] == "IF"
    assert prof["tick_value"] > 0
    assert prof["margin_per_lot"] > 0
    assert prof["volatility_stats"]["hv20_pct"] > 0
    assert prof["open_interest_stats"]["latest_oi"] > 0
    assert len(prof["kline"]) == 60
    assert "ma5" in prof["kline"][0]
    assert "ma20" in prof["kline"][0]

    sens = get_parameter_sensitivity("IF", "dual_ma")
    assert sens["symbol_prefix"] == "IF"
    assert len(sens["grid"]) > 0
    assert sens["optimal_params"] is not None
    assert "sharpe" in sens["optimal_params"]


def test_instrument_profile_rejects_disabled_symbol_without_if_fallback(monkeypatch):
    monkeypatch.setattr(
        futures_data,
        "get_futures_kline",
        lambda *args, **kwargs: pytest.fail("disabled futures must not fall back to IF"),
    )

    result = get_futures_instrument_profile("PB", days=60)

    assert result["status"] == "error"
    assert "不存在或未启用" in result["message"]


def test_instrument_profile_returns_error_for_empty_real_history(monkeypatch):
    monkeypatch.setattr(futures_data, "get_futures_kline", lambda *args, **kwargs: [])

    result = get_futures_instrument_profile("IF", days=60)

    assert result == {"status": "error", "message": "无可用真实历史行情"}


def test_parameter_sensitivity_rejects_disabled_symbol_and_empty_history(monkeypatch):
    monkeypatch.setattr(
        futures_data,
        "get_futures_kline",
        lambda *args, **kwargs: pytest.fail("disabled futures must not fall back to IF"),
    )

    disabled = get_parameter_sensitivity("PB")
    assert disabled["status"] == "error"
    assert "不存在或未启用" in disabled["message"]

    monkeypatch.setattr(futures_data, "get_futures_kline", lambda *args, **kwargs: [])
    empty = get_parameter_sensitivity("IF")
    assert empty == {"status": "error", "message": "无可用真实历史行情"}


def test_inter_commodity_rejects_unknown_pair_without_defaulting_to_au_ag(monkeypatch):
    monkeypatch.setattr(
        futures_data,
        "get_futures_kline",
        lambda symbol, **kwargs: _fixture_bars(),
    )

    result = get_inter_commodity_spread("NOT_A_PAIR")

    assert result["status"] == "error"
    assert "未知跨品种配对" in result["message"]


def test_term_structure_is_unavailable_without_real_contract_quotes():
    """缺少真实分合约行情时，不得返回模板化期限结构。"""
    ts = get_futures_term_structure("IF")
    assert ts["available"] is False
    assert ts["surface"] == "term_structure"
    assert ts["modeled"] is False


def test_slippage_and_intraday_penalty_fee():
    """验证真实微观摩擦：最小跳价滑点撮合与中金所平今仓15倍惩罚费率。"""
    from app.domain.futures_rules import calc_slippage_price

    # 1. 滑点测算 (IF 最小跳价 0.2 点，1 tick = 0.2)
    buy_exec = calc_slippage_price(price=4000.0, direction=1, tick_size=0.2, slippage_ticks=1.0)
    assert buy_exec == 4000.2  # 买入价格滑高 1 tick

    sell_exec = calc_slippage_price(price=4000.0, direction=-1, tick_size=0.2, slippage_ticks=1.0)
    assert sell_exec == 3999.8  # 卖出价格滑低 1 tick

    # 2. 中金所股指手续费 (IF 1手，乘数300，市值 1,200,000 元)
    # 开仓万 0.23 => 1,200,000 * 0.000023 = 27.6 元
    fee_open = calc_futures_cost(price=4000.0, qty=1, multiplier=300.0, is_close=False, symbol_prefix="IF")
    assert round(fee_open, 2) == 27.6

    # 平昨仓万 0.23 => 27.6 元
    fee_close_yd = calc_futures_cost(price=4000.0, qty=1, multiplier=300.0, is_close=True, is_today=False, symbol_prefix="IF")
    assert round(fee_close_yd, 2) == 27.6

    # 平今仓万 3.45 (15倍惩罚) => 1,200,000 * 0.000345 = 414.0 元
    fee_close_today = calc_futures_cost(price=4000.0, qty=1, multiplier=300.0, is_close=True, is_today=True, symbol_prefix="IF")
    assert round(fee_close_today, 2) == 414.0
    assert round(fee_close_today / fee_open, 1) == 15.0

    # 显式 FeeRule 选择顺序：开仓 / 平昨 / 平今
    spec_if = get_futures_spec("IF")
    assert spec_if is not None
    fee_open_rule = calc_futures_cost(
        price=4000.0,
        qty=1,
        multiplier=300.0,
        fee_open=spec_if.fee_open,
        fee_close=spec_if.fee_close,
        fee_close_today=spec_if.fee_close_today,
    )
    fee_close_today_rule = calc_futures_cost(
        price=4000.0,
        qty=1,
        multiplier=300.0,
        is_close=True,
        is_today=True,
        fee_open=spec_if.fee_open,
        fee_close=spec_if.fee_close,
        fee_close_today=spec_if.fee_close_today,
    )
    assert fee_open_rule == 27.6
    assert fee_close_today_rule == 414.0


def test_comprehensive_metrics_and_attribution():
    """验证包含夏普、索提诺、Alpha、Beta、IR、VaR、CVaR、PSR与DSR的全量量化指标。"""
    from app.domain.futures_rules import calc_comprehensive_futures_metrics

    # 构造一条持续上行但有波动的净值序列
    eq_curve = [100.0, 102.0, 101.5, 104.0, 103.0, 106.0, 105.0, 108.0, 110.0, 109.5]
    bm_curve = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0, 102.5, 104.0, 105.0, 104.5]
    trades = [
        {"id": "T-1", "direction": "LONG", "pnl": 5000.0, "pnl_pct": 2.5},
        {"id": "T-2", "direction": "SHORT", "pnl": -2000.0, "pnl_pct": -1.0},
        {"id": "T-3", "direction": "LONG", "pnl": 7000.0, "pnl_pct": 3.5},
    ]
    margin_ratios = [15.0, 20.0, 18.0, 22.0, 75.0, 30.0, 25.0, 20.0, 15.0, 10.0]

    res = calc_comprehensive_futures_metrics(eq_curve, bm_curve, trades, margin_ratios)
    assert res["total_return_pct"] == 9.5
    assert res["sharpe_ratio"] > 0
    assert res["sortino_ratio"] >= res["sharpe_ratio"]
    assert res["beta"] > 0
    assert "alpha_annual_pct" in res
    assert "information_ratio" in res
    assert "var_95_pct" in res
    assert "cvar_95_pct" in res
    assert res["profit_factor"] == 6.0  # 12000 / 2000 = 6.0
    assert res["margin_call_count"] == 1  # 75.0% >= 70%
    assert res["liquidation_count"] == 0  # < 90%
    assert "psr" in res
    assert "dsr" in res


def test_cost_sensitivity_matrix():
    """验证二维成本压力测试矩阵 (滑点跳数 × 手续费倍数)。"""
    from app.domain.futures_rules import calc_cost_sensitivity_matrix

    closes = [3000.0 + i * 5.0 for i in range(50)]
    matrix_res = calc_cost_sensitivity_matrix(closes, tick_size=0.2, symbol_prefix="IF")
    grid = matrix_res["grid"]
    assert len(grid) == 16  # 4 x 4
    for cell in grid:
        assert "slippage_ticks" in cell
        assert "fee_multiplier" in cell
        assert "sharpe" in cell
        assert "total_return_pct" in cell



