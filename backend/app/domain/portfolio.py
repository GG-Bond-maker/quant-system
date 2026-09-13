"""组合回测引擎（纯函数 + pandas）。

支持股票/ETF 混合资产，按月度/季度/年度/不调仓再平衡，
输出净值曲线、回撤、年度收益、持仓漂移及风险指标。

执行纪律（CRIT-2/3 修复）：
- 信号时序：调仓信号在 T-1 日收盘后触发，T 日（次一交易日）撮合执行，
  杜绝"当日收盘信号当日收盘价成交"的前视偏误；首个建仓日除外。
- 交易摩擦：双边佣金（最低 5 元）、卖出印花税（股票 0.05%，ETF 豁免）、
  滑点（默认 5bps，买加卖减）、A 股 100 股/份整手约束。
- 数据对齐：晚上市资产在首个有效价之前以现金形式持有权重（不再静默截断
  回测起点）；停牌/数据缺口以前收盘价估值并记入 data_warnings。
- 外呼纪律（CRIT-4 修复 → Task 13 整改 A-P1-1）：价格获取由调用方注入
  （price_loader / benchmark_loader，API 层传 data 层 portfolio_source 的
  限速+重试实现）；domain 只做纯计算——见 tests/test_domain_purity.py
  的业务层反向依赖守卫（app.data/app.ml/app.api 一律禁止）。

机构级升级（weighting 参数）：
- user（默认）：使用调用方给定的目标权重（完全向后兼容）；
- risk_parity：风险平摊（Ledoit-Wolf 收缩 + RMT 去噪协方差，各资产风险
  贡献相等，大幅降低尾部回撤）；
- max_div：最大分散度组合（最大化 Diversification Ratio）；
- inverse_vol：波动率倒数加权。

⚠️ 风险类方案的无前视纪律：每次调仓的协方差只用**执行日之前**的
收盘价（returns 截止 T-1），历史不足或资产缺数据时回退等权并记
rebalance_log / data_warnings。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .metrics import deflated_sharpe_ratio, probabilistic_sharpe_ratio
from .optimizer import compute_weights_from_returns

_RISK_FREE_ANNUAL = 0.02

# 支持的权重方案
WEIGHTING_CHOICES = ("user", "risk_parity", "max_div", "inverse_vol")

# ---- A 股交易摩擦常量（口径与 backtest/broker.py 保持一致） ----
LOT_SIZE = 100            # A 股整手（股票与场内 ETF 均为 100 股/份）
COMMISSION_RATE = 0.0003  # 佣金率（双边）
COMMISSION_MIN = 5.0      # 单笔最低佣金（元）
STAMP_DUTY = 0.0005       # 印花税率（仅卖出，ETF 豁免）
SLIPPAGE_BPS = 5.0        # 滑点（bps）：成交价 = 收盘价 × (1 ± bps/1e4)
WEIGHT_TOLERANCE = 0.10   # 再平衡容忍带：偏离目标权重超过 10% 才调整


def _rebalance_dates(dates: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    """根据调仓频率返回每个周期的第一个交易日。"""
    if freq == "none" or not len(dates):
        return pd.DatetimeIndex([dates[0]]) if len(dates) else pd.DatetimeIndex([])

    s = pd.Series(np.arange(len(dates)), index=dates)
    if freq == "M":
        idx = s.groupby([s.index.year, s.index.month]).head(1).index
    elif freq == "Q":
        idx = s.groupby([s.index.year, s.index.quarter]).head(1).index
    elif freq == "Y":
        idx = s.groupby(s.index.year).head(1).index
    else:
        raise ValueError(f"不支持的调仓频率: {freq}")
    return idx


def _compute_metrics(nav: pd.Series, bm: pd.Series, daily_rf: float,
                     n_trials: int = 1) -> dict[str, float | None]:
    """计算风险指标；输入为日净值序列。

    机构级补充：probabilistic_sharpe / deflated_sharpe（López de Prado
    口径，n_trials 为回测尝试的策略/参数组合数，用于多重试验惩罚）。
    """
    ret = nav.pct_change().dropna()
    bm_ret = bm.pct_change().dropna()
    aligned = pd.concat([ret, bm_ret], axis=1).dropna()
    ret_a = aligned.iloc[:, 0]
    bm_a = aligned.iloc[:, 1]

    n = len(nav)
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1)
    cagr = float((nav.iloc[-1] / nav.iloc[0]) ** (252 / max(1, n)) - 1)

    running_peak = nav.cummax()
    drawdown = 1.0 - nav / running_peak
    max_dd = float(drawdown.max())

    vol = float(ret.std() * np.sqrt(252))
    sharpe = float((ret.mean() * 252 - _RISK_FREE_ANNUAL) / max(vol, 1e-12))
    calmar = float(cagr / max_dd) if max_dd > 1e-9 else np.inf

    # Beta / Alpha（日收益率一元线性回归）
    if len(ret) >= 2 and ret.std() > 0 and bm_a.std() > 0:
        beta, alpha_daily = np.polyfit(bm_a.values, ret_a.values, 1)
        alpha = float(alpha_daily * 252)
        beta = float(beta)
    else:
        alpha = 0.0
        beta = 1.0

    # PSR / DSR（防过拟合指标；输入需 >= 3 个观测）
    # rf 与上方 sharpe 保持同一无风险利率口径（此前漏传，导致口径分裂）
    nav_arr = nav.to_numpy(dtype=np.float64)
    try:
        psr = float(probabilistic_sharpe_ratio(nav_arr, rf=_RISK_FREE_ANNUAL))
        dsr = float(deflated_sharpe_ratio(nav_arr, n_trials=n_trials,
                                          rf=_RISK_FREE_ANNUAL))
    except ValueError:
        psr = float("nan")
        dsr = float("nan")

    return {
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "max_drawdown": round(max_dd, 6),
        "volatility": round(vol, 6),
        "sharpe": round(sharpe, 6),
        "probabilistic_sharpe": round(psr, 6) if psr == psr else None,  # noqa: PLR0124
        "deflated_sharpe": round(dsr, 6) if dsr == dsr else None,       # noqa: PLR0124
        "calmar": round(calmar, 6) if np.isfinite(calmar) else None,
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "risk_free": _RISK_FREE_ANNUAL,
    }


def run_portfolio_backtest(
    assets: list[dict[str, Any]],
    start_date: str,
    end_date: str,
    initial_cash: float = 1_000_000,
    rebalance: str = "M",
    benchmark_code: str = "000300",
    weighting: str = "user",
    cov_window: int = 60,
    n_trials: int = 1,
    price_loader: Any = None,
    benchmark_loader: Any = None,
) -> dict[str, Any]:
    """执行组合回测（纯计算：价格由调用方注入，domain 不发起任何 IO）。

    assets: [{code, type: 'stock'|'etf', weight}], weights 为小数且和≈1。
    weighting: "user" 用给定权重；"risk_parity"/"max_div"/"inverse_vol"
               在每个调仓日按执行日之前的收益率重新求解权重（无前视）。
    price_loader: (code, typ, start, end) -> pd.Series——API 层注入 data 层
                  portfolio_source.fetch_asset_close（限速+重试+TTL 缓存）；
    benchmark_loader: (code, start, end) -> pd.Series。
    返回字典包含 metrics / nav_curve / drawdown_curve / annual_returns /
    holdings_drift / rebalance_log。
    """
    if price_loader is None or benchmark_loader is None:
        raise ValueError(
            "price_loader/benchmark_loader 必须由调用方注入"
            "（domain 层禁止发起 IO，见 tests/test_domain_purity.py）")
    if weighting not in WEIGHTING_CHOICES:
        raise ValueError(f"weighting 仅支持 {WEIGHTING_CHOICES}，收到 {weighting!r}")
    if not assets:
        raise ValueError("资产列表不能为空")

    codes = [a["code"] for a in assets]
    if len(set(codes)) != len(codes):
        raise ValueError("资产代码重复")

    total_weight = sum(float(a.get("weight", 0)) for a in assets)
    if not (0.99 <= total_weight <= 1.01):
        raise ValueError(f"资产权重之和应为 1，当前 {total_weight}")

    # 1. 取数据
    price_frames: list[pd.Series] = []
    failed: list[str] = []
    for a in assets:
        code = a["code"]
        typ = a.get("type", "stock")
        try:
            series = price_loader(code, typ, start_date, end_date)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{code}: {type(e).__name__}")
            continue
        if series.empty:
            failed.append(f"{code}: 无数据")
            continue
        price_frames.append(series)

    if failed:
        raise ValueError(f"部分资产数据获取失败: {'; '.join(failed)}")

    prices = pd.concat(price_frames, axis=1)
    benchmark = benchmark_loader(benchmark_code, start_date, end_date)

    # 2. 对齐（CRIT-3 修复：不再 dropna 截断起点）
    # 交易日轴 = 资产与基准指数的并集；每列自首个有效价起 ffill（停牌/缺口用
    # 前收盘估值），首个有效价之前保持 NaN（该资产权重以现金形式持有）。
    union_idx = prices.index.union(benchmark.index).sort_values()
    raw_prices = prices.reindex(union_idx)
    prices = raw_prices.ffill()
    benchmark = benchmark.reindex(union_idx).ffill().dropna()
    prices = prices.loc[benchmark.index]
    prices.index = pd.to_datetime(prices.index)
    benchmark.index = pd.to_datetime(benchmark.index)

    if len(prices) < 2:
        raise ValueError("对齐后有效交易日不足")

    # 数据质量提示（替代原先的静默截断）：晚上市 / 停牌缺口 / 疑似退市
    data_warnings: list[str] = []
    for code in codes:
        col = raw_prices.get(code)
        if col is None:
            continue
        fv = col.first_valid_index()
        if fv is None:
            data_warnings.append(f"{code} 区间内无任何行情数据")
            continue
        if fv > union_idx[0]:
            data_warnings.append(
                f"{code} 行情自 {str(fv)[:10]} 起可用，此前其权重以现金形式持有")
        n_gap = int(col.loc[fv:].isna().sum())
        if n_gap > 0:
            data_warnings.append(f"{code} 有 {n_gap} 个交易日无行情（停牌/缺口），以前收盘价估值")
        lv = col.loc[fv:].last_valid_index()
        if lv is not None and lv < union_idx[-1]:
            data_warnings.append(
                f"{code} 行情止于 {str(lv)[:10]}（疑似退市/长期停牌），其后以最后价估值")

    # 3. 调仓日历与目标权重
    reb_dates = _rebalance_dates(prices.index, rebalance)
    user_weights = np.array([float(a.get("weight", 0)) for a in assets])
    is_etf_arr = np.array([a.get("type", "stock") == "etf" for a in assets])
    n_assets = len(assets)

    # 4. 模拟持仓（CRIT-2 修复：T-1 收盘信号 -> T 日撮合，含摩擦）
    cash = float(initial_cash)
    holdings = np.zeros(n_assets)      # 当前股数/份额
    values = np.zeros(len(prices))     # 每日组合净值（现金 + 持仓市值）
    drift_records: list[dict] = []
    rebalance_log: list[dict] = []     # 每次调仓的权重决策审计（含回退标记）
    friction = {"commission": 0.0, "stamp_duty": 0.0, "slippage": 0.0}
    slip = SLIPPAGE_BPS / 10_000.0

    def _solve_weights(exec_idx: int, dt_label: str) -> np.ndarray:
        """计算执行日 exec_idx 的目标权重（无前视：只用 exec_idx 之前的价格）。

        weighting == "user" 直接返回给定权重；
        风险类方案按 trailing 收益率求解，历史/数据不足回退等权并记日志。
        """
        if weighting == "user":
            return user_weights
        equal_w = np.full(n_assets, 1.0 / n_assets)
        hist = prices.iloc[max(0, exec_idx - cov_window):exec_idx]   # 严格 < 执行日
        if len(hist) >= 5:
            rets = hist.pct_change().dropna(how="all")
            valid = [c for c in prices.columns
                     if rets[c].notna().all() and np.isfinite(rets[c]).all()]
            if len(valid) >= 2 and len(rets) >= 5:
                R = rets[valid].to_numpy(dtype=np.float64)
                w_valid = compute_weights_from_returns(R, method=weighting)
                w = np.zeros(n_assets)
                for j, c in enumerate(prices.columns):
                    if c in valid:
                        w[j] = w_valid[valid.index(c)]
                rebalance_log.append({
                    "date": dt_label, "weighting": weighting, "fallback": False,
                    "weights": {code: round(float(w[j]), 6)
                                for j, code in enumerate(codes)},
                })
                return w
        rebalance_log.append({
            "date": dt_label, "weighting": weighting, "fallback": True,
            "weights": {code: round(float(equal_w[j]), 6)
                        for j, code in enumerate(codes)},
        })
        return equal_w

    def _execute_rebalance(prices_t: np.ndarray, target_weights: np.ndarray) -> None:
        """以当日收盘价（含滑点）执行调仓：先卖后买，双边佣金/卖出印花税/整手。"""
        nonlocal cash, holdings
        tradable = np.array([v is not None and v == v and v > 0 for v in prices_t])  # noqa: PLR0124
        safe_px = np.where(tradable, prices_t, 0.0)   # NaN 参与估值时按 0（持仓必为 0）
        equity = cash + float(np.dot(holdings, safe_px))
        target_value = equity * target_weights

        # ---- 先卖：清零权重 / 超配削减（容忍带 WEIGHT_TOLERANCE）----
        for j in range(n_assets):
            if holdings[j] <= 0 or not tradable[j]:
                continue
            cur_value = holdings[j] * float(prices_t[j])
            if target_weights[j] <= 0:
                sell_shares = holdings[j]
            elif cur_value > target_value[j] * (1.0 + WEIGHT_TOLERANCE):
                sell_shares = (cur_value - target_value[j]) / float(prices_t[j])
            else:
                continue
            sell_shares = math.floor(sell_shares / LOT_SIZE) * LOT_SIZE
            if sell_shares <= 0:
                continue
            amount = sell_shares * float(prices_t[j]) * (1 - slip)   # 卖出滑点价
            commission = max(COMMISSION_MIN, amount * COMMISSION_RATE)
            stamp = amount * STAMP_DUTY if not is_etf_arr[j] else 0.0  # 印花税仅股票
            fee = commission + stamp
            friction["commission"] += commission
            friction["stamp_duty"] += stamp
            friction["slippage"] += sell_shares * float(prices_t[j]) * slip
            holdings[j] -= sell_shares
            cash += amount - fee

        # ---- 后买：欠配补足（用卖出回笼后的现金），整手 + 含费现金约束 ----
        for j in range(n_assets):
            if not tradable[j] or target_weights[j] <= 0:
                continue
            deficit = target_value[j] - holdings[j] * float(prices_t[j])
            budget = min(deficit, cash)
            if budget <= 0:
                continue
            buy_px = float(prices_t[j]) * (1 + slip)                  # 买入滑点价
            shares = math.floor(budget / (buy_px * LOT_SIZE)) * LOT_SIZE
            while shares > 0:
                amount = shares * buy_px
                fee = max(COMMISSION_MIN, amount * COMMISSION_RATE)
                if amount + fee <= cash:
                    break
                shares -= LOT_SIZE
            if shares <= 0:
                continue
            amount = shares * buy_px
            fee = max(COMMISSION_MIN, amount * COMMISSION_RATE)
            friction["commission"] += fee
            friction["slippage"] += shares * float(prices_t[j]) * slip
            cash -= amount + fee
            holdings[j] += shares

    pending_rebalance = False   # T-1 收盘触发的调仓，T 日执行
    for i, (dt, row) in enumerate(prices.iterrows()):
        prices_t = row.values
        if pending_rebalance:
            # 昨日收盘触发的调仓 -> 今日收盘撮合（信号先于成交一日，无前视）
            _execute_rebalance(prices_t, _solve_weights(
                i, dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)))
            pending_rebalance = False
        if dt in reb_dates:
            if i == 0:
                _execute_rebalance(prices_t, _solve_weights(
                    0, dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)))   # 首个交易日当日建仓
            else:
                pending_rebalance = True       # 之后一律次日执行
        safe_px = np.array([v if (v is not None and v == v and v > 0) else 0.0  # noqa: PLR0124
                            for v in prices_t])
        values[i] = cash + float(np.dot(holdings, safe_px))

        # 记录权重漂移（无价资产持仓为 0，权重记现金占比之外的 0）
        total_v = values[i]
        if total_v > 0:
            w = (holdings * safe_px) / total_v   # 逐元素：各资产市值 / 总净值
        else:
            w = np.zeros(n_assets)
        drift_records.append({
            "date": dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt),
            "weights": {code: round(float(w[j]), 6) for j, code in enumerate(codes)},
        })

    nav = pd.Series(values, index=prices.index)
    bm_nav = initial_cash / benchmark.iloc[0] * benchmark

    # 5. 指标
    metrics = _compute_metrics(nav, bm_nav, _RISK_FREE_ANNUAL / 252, n_trials=n_trials)

    # 6. 回撤曲线
    running_peak = nav.cummax()
    drawdown = 1.0 - nav / running_peak

    nav_curve = [
        {"date": str(d)[:10], "nav": round(float(nav.loc[d]), 4),
         "benchmark": round(float(bm_nav.loc[d]), 4)}
        for d in nav.index
    ]
    dd_curve = [
        {"date": str(d)[:10], "drawdown": round(float(drawdown.loc[d]), 6)}
        for d in nav.index
    ]

    # 7. 年度收益
    nav_df = pd.DataFrame({"nav": nav.values, "bm": bm_nav.values}, index=pd.to_datetime(nav.index))
    annual = nav_df.resample("YE").apply(lambda x: x.iloc[-1] / x.iloc[0] - 1 if len(x) else np.nan)
    annual_returns = [
        {"year": str(y)[:4], "portfolio": round(float(row["nav"]), 6),
         "benchmark": round(float(row["bm"]), 6)}
        for y, row in annual.iterrows() if pd.notna(row["nav"])
    ]

    return {
        "status": "ok",
        "start_date": start_date,
        "end_date": end_date,
        "initial_cash": float(initial_cash),
        "rebalance": rebalance,
        "benchmark": benchmark_code,
        "weighting": weighting,
        "assets": [{"code": a["code"], "type": a.get("type", "stock"),
                    "weight": float(a.get("weight", 0))} for a in assets],
        "trading_days": len(nav),
        "metrics": metrics,
        "nav_curve": nav_curve,
        "drawdown_curve": dd_curve,
        "annual_returns": annual_returns,
        "holdings_drift": drift_records,
        # 调仓权重决策审计（weighting != user 时每次调仓的实际目标权重与回退标记）
        "rebalance_log": rebalance_log,
        # 交易摩擦合计（元）：佣金 / 印花税 / 滑点成本
        "friction_costs": {k: round(v, 2) for k, v in friction.items()},
        # 数据对齐提示：晚上市 / 停牌缺口 / 疑似退市（此前为静默处理）
        "data_warnings": data_warnings,
    }
