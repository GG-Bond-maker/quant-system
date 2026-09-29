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
from loguru import logger

from ..core.errors import DataSourceUnavailable
from .metrics import (
    RISK_FREE_ANNUAL,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
from .optimizer import compute_weights_from_returns
from .trading_rules import (
    COMMISSION_MIN,
    COMMISSION_RATE_DEFAULT,
    STAMP_DUTY_STOCK_RATE,
)

# 无风险利率口径 = 年化，来自 domain.metrics 单一定义（审计 B2-16：此前本地再定义
# 一份 2% 且 _compute_metrics 的形参叫 daily_rf 但从没被用过 ⇒ 形参与实现分裂）。

# 支持的权重方案
WEIGHTING_CHOICES = ("user", "risk_parity", "max_div", "inverse_vol")

# ---- A 股交易摩擦常量（费率数字来自 domain.trading_rules 单一事实来源） ----
LOT_SIZE = 100            # A 股整手（股票与场内 ETF 均为 100 股/份）
SLIPPAGE_BPS = 5.0        # 滑点（bps）：成交价 = 收盘价 × (1 ± bps/1e4）
WEIGHT_TOLERANCE = 0.10   # 再平衡容忍带：偏离目标权重超过 10% 才调整

# 「取不到该标的数据」类异常的**显式**集合（与 API 层 P1-7 同源治理）。
# 逐类收窄而非 `except Exception`，避免把真实程序缺陷粉饰成「数据为空」：
#   - DataSourceUnavailable：本仓限速/熔断层在源连续失败后抛出的「源不可用」信号；
#   - IndexError / KeyError：第三方 akshare 在源站对该标的返回空/畸形响应体时
#     抛出的裸索引/键错误（实测：腾讯 get_tx_start_year 对未知代码返回的空
#     data 列表取 [0] ⇒ IndexError: list index out of range）；
#   - OSError：网络类异常（requests 的 RequestException 继承 IOError=OSError）。
# 命中 ⇒ 按「本地暂无该标的区间数据」处理（对外 ERR_DATA_EMPTY 51001）；
# 未命中 ⇒ 真实缺陷，继续上抛，由 API 兜底归 ERR_SYSTEM(50000)。
_PRICE_UNAVAILABLE_ERRORS = (DataSourceUnavailable, IndexError, KeyError, OSError)


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


def _aggregate_price_basis(
    codes: list[str], metas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """把「各资产口径 meta」聚合成组合级 ``price_basis``。

    字段形状**与 api/v1/backtest.py 的 price_basis 逐字对齐**
    （``{kind, basis, raw_fallback_symbols, note}``），不自创字段名。

    ``basis`` ∈ ``{"qfq", "raw", "mixed", "unknown"}``：
        - 全部资产口径已知且均为前复权   -> ``"qfq"``；
        - 全部已知且均为不复权           -> ``"raw"``；
        - 全部已知但 QFQ 与不复权混用     -> ``"mixed"``；
        - 任一资产口径未知（未经平台数据源加载，如测试注入的裸 ``price_loader``，
          或升级期间命中的旧缓存 payload）-> ``"unknown"``（保守：不替后端担保口径）。

    ``raw_fallback_symbols`` 为口径为不复权的资产代码列表；``note`` 为可读说明
    （前端 banner 直接展示，缺失时前端按 Backtest 先例显示"口径未知"）。
    """
    known = {c: m for c, m in metas.items() if m and m.get("basis")}
    raw_symbols = [c for c in codes if known.get(c, {}).get("basis") == "raw"]
    if len(known) < len(codes):
        note = ("部分标的未经平台数据源加载，复权口径无法确认"
                "（不显示为 QFQ 以避免误导）")
        return {"kind": "platform", "basis": "unknown",
                "raw_fallback_symbols": raw_symbols, "note": note}
    if not raw_symbols:
        return {"kind": "platform", "basis": "qfq", "raw_fallback_symbols": [],
                "note": "全部标的为平台前复权（QFQ）口径"}
    if len(raw_symbols) == len(codes):
        note = ("全部标的为**不复权**口径（备用源降级）；"
                "除权跳空会影响组合净值，结果仅供参考")
        return {"kind": "platform", "basis": "raw",
                "raw_fallback_symbols": raw_symbols, "note": note}
    note = (f"以下标的备用源为**不复权**口径、已如实降级：{raw_symbols}"
            "（除权跳空会影响组合净值，结果仅供参考）")
    return {"kind": "platform", "basis": "mixed",
            "raw_fallback_symbols": raw_symbols, "note": note}


def _compute_metrics(nav: pd.Series, bm: pd.Series, rf_annual: float,
                     n_trials: int = 1) -> dict[str, float | str | None]:
    """计算风险指标；输入为日净值序列。

    机构级补充：probabilistic_sharpe / deflated_sharpe（López de Prado
    口径，n_trials 为回测尝试的策略/参数组合数，用于多重试验惩罚）。

    :param rf_annual: **年化**无风险利率。审计 B2-16 前该形参名为 ``daily_rf``
        且**从未被使用**（函数体直接读模块常量）⇒ 形参与实现分裂；现在形参生效。
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
    sharpe = float((ret.mean() * 252 - rf_annual) / max(vol, 1e-12))
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
        psr = float(probabilistic_sharpe_ratio(nav_arr, rf=rf_annual))
        dsr = float(deflated_sharpe_ratio(nav_arr, n_trials=n_trials,
                                          rf=rf_annual))
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
        # 口径披露（审计 B2-16）：无风险利率为年化，与个股风险卡同源
        "risk_free": rf_annual,
        "rf_basis": "annual",
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
    # 审计 B2-12：±1% 是**输入容差**（前端百分比取整），不能当成"可以少投"。
    # 修复前接受 Σw∈[0.99,1.01] 却不归一 ⇒ Σw=0.995 时 0.5% 永久留作现金，
    # 且响应里回显的权重（Σw=0.995）与实际执行口径不一致。现在统一归一后执行。
    weights_normalization: dict | None = None
    if abs(total_weight - 1.0) > 1e-9:
        weights_normalization = {
            "input_sum": round(total_weight, 6),
            "factor": round(1.0 / total_weight, 8),
            "note": ("输入权重之和非 1（容差 ±1%），已按 1/Σw 归一后再执行/回显；"
                     "此前不归一 ⇒ 差额会永久留作现金并使 drift 恒非零"),
        }
        assets = [{**a, "weight": float(a.get("weight", 0)) / total_weight} for a in assets]

    # 1. 取数据
    price_frames: list[pd.Series] = []
    failed: list[str] = []
    # 复权口径披露：逐资产收集**实际生效**的口径（东财 QFQ / 腾讯 QFQ / 新浪 RAW）。
    # ⚠️ 必须在下面 pd.concat **之前**读 series.attrs —— attrs 会随 concat 丢失。
    basis_metas: dict[str, dict[str, Any]] = {}
    for a in assets:
        code = a["code"]
        typ = a.get("type", "stock")
        try:
            series = price_loader(code, typ, start_date, end_date)
        except _PRICE_UNAVAILABLE_ERRORS as e:
            # 「取不到数据」类异常 → 按无数据处理。异常类名/详情只进服务端日志，
            # 对外文案不含实现细节（同 API 层 P1-7）；其余异常继续上抛，
            # 由 API 兜底归 ERR_SYSTEM(50000)，不再被伪装成 51001。
            logger.warning(f"[portfolio] 资产取数失败 {code}: {type(e).__name__}: {e}")
            failed.append(f"{code}: 无数据")
            continue
        if series is None or series.empty:
            failed.append(f"{code}: 无数据")
            continue
        meta = (getattr(series, "attrs", None) or {}).get("aqp_price_basis")
        if meta:
            basis_metas[code] = meta
        price_frames.append(series)

    # 基准指数同样按「取数失败 ⇒ 数据不可用」处理：不得让第三方裸异常（实测腾讯
    # 对未知基准代码返回空 data ⇒ akshare IndexError）逃逸成未分类系统异常
    # （50000），也不得把异常类名透传到对外文案。与资产失败合并为同一条 51001 语义。
    try:
        benchmark = benchmark_loader(benchmark_code, start_date, end_date)
    except _PRICE_UNAVAILABLE_ERRORS as e:
        logger.warning(f"[portfolio] 基准取数失败 {benchmark_code}: {type(e).__name__}: {e}")
        benchmark = None
    if benchmark is None or benchmark.empty:
        failed.append(f"基准 {benchmark_code}: 无数据")

    if failed:
        raise ValueError(f"部分资产数据获取失败: {'; '.join(failed)}")

    # 口径聚合（在 concat 之前完成，不依赖 attrs 是否能挺过 concat）
    price_basis = _aggregate_price_basis(codes, basis_metas)

    prices = pd.concat(price_frames, axis=1)

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
            commission = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
            stamp = amount * STAMP_DUTY_STOCK_RATE if not is_etf_arr[j] else 0.0  # 印花税仅股票
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
                fee = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
                if amount + fee <= cash:
                    break
                shares -= LOT_SIZE
            if shares <= 0:
                continue
            amount = shares * buy_px
            fee = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
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
    metrics = _compute_metrics(nav, bm_nav, RISK_FREE_ANNUAL, n_trials=n_trials)

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
        # 复权口径披露（形状对齐 api/v1/backtest.py：kind/basis/raw_fallback_symbols/note）：
        # ETF 备源降级为**不复权**时必须如实告知，此前被静默吞掉。
        "price_basis": price_basis,
        "assets": [{"code": a["code"], "type": a.get("type", "stock"),
                    "weight": float(a.get("weight", 0))} for a in assets],
        # 审计 B2-12：若输入 Σw≠1，这里披露归一因子（None 表示本就是 1）
        "weights_normalization": weights_normalization,
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
