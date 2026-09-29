/** 组合回测类型定义 */

export type AssetType = 'stock' | 'etf';
export type RebalanceFreq = 'M' | 'Q' | 'Y' | 'none';

export interface PortfolioAsset {
  code: string;
  name?: string;
  type: AssetType;
  weight: number;
}

export interface AssetSearchItem {
  code: string;
  name: string;
  type: AssetType;
  tracking_index?: string | null;
}

export interface PortfolioBacktestRequest {
  assets: PortfolioAsset[];
  start_date: string;
  end_date: string;
  rebalance: RebalanceFreq;
  benchmark: string;
  initial_cash: number;
  /** 权重方案（后端真实生效）：user=给定权重；其余由优化器按执行日前数据求解 */
  weighting?: PortfolioWeighting;
  /** 风险类 weighting 的协方差回看窗口（交易日，默认 60） */
  cov_window?: number;
}

/** 与后端 WEIGHTING_CHOICES 对齐 */
export type PortfolioWeighting = 'user' | 'risk_parity' | 'max_div' | 'inverse_vol';

export interface PortfolioMetrics {
  total_return: number;
  cagr: number;
  max_drawdown: number;
  volatility: number;
  sharpe: number;
  calmar: number | null;
  alpha: number;
  beta: number;
  risk_free: number;
}

export interface NavPoint {
  date: string;
  nav: number;
  benchmark: number;
}

export interface DrawdownPoint {
  date: string;
  drawdown: number;
}

export interface AnnualReturnPoint {
  year: string;
  portfolio: number;
  benchmark: number;
}

export interface HoldingsDriftPoint {
  date: string;
  weights: Record<string, number>;
}

/**
 * 复权口径披露（字段形状与后端 `api/v1/backtest.py` 的 `price_basis` 一致）。
 *
 * 背景：ETF 主源东财是**前复权(QFQ)**，降级到新浪备源后变为**不复权**——
 * 此前该口径变化被静默吞掉。`basis` 恒存在；`raw_fallback_symbols` 非空表示
 * 口径不纯（部分标的不复权），前端不得再硬编码 QFQ。
 */
export interface PriceBasis {
  kind: string;
  /** qfq=前复权 / raw=不复权 / mixed=QFQ 与不复权混用 / unknown=口径未知 */
  basis: 'qfq' | 'raw' | 'mixed' | 'unknown';
  raw_fallback_symbols: string[];
  note: string;
}

export interface PortfolioBacktestResult {
  status: string;
  start_date: string;
  end_date: string;
  initial_cash: number;
  rebalance: RebalanceFreq;
  benchmark: string;
  assets: Array<{ code: string; type: AssetType; weight: number }>;
  trading_days: number;
  metrics: PortfolioMetrics;
  nav_curve: NavPoint[];
  drawdown_curve: DrawdownPoint[];
  annual_returns: AnnualReturnPoint[];
  holdings_drift: HoldingsDriftPoint[];
  /**
   * 复权口径披露。**可选**：旧缓存 payload / 升级期间命中的响应可能缺失，
   * 缺失时前端按 Backtest 先例如实显示"口径未知"，不得硬编码 QFQ。
   */
  price_basis?: PriceBasis;
}
