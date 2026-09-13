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
}
