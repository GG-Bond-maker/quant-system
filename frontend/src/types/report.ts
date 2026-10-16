/**
 * AI 投研日报数据类型（与后端 /api/v1/report/daily 深度对齐）。
 */

export interface MarketCompass {
  trading_date: string;
  sentiment_score: number;
  sentiment_label: string;
  up_count: number;
  down_count: number;
  flat_count: number;
  adl_change: number;
  limit_up_count: number;
  limit_down_count: number;
  turnover_gini: number;
  histogram?: Array<{ bin: string; count: number }>;
}

export interface StyleFactorItem {
  style: string;
  name: string;
  return: number;
}

export interface IndustryItem {
  industry: string;
  return: number;
}

export interface StyleFactorsData {
  styles: StyleFactorItem[];
  top_industries: IndustryItem[];
  bottom_industries: IndustryItem[];
}

export interface PortfolioRiskData {
  portfolio_var_95: number;
  portfolio_beta: number;
  leverage_ratio: number;
  max_stock_weight: number;
  top3_concentration: number;
  liquidation_days: number;
  status: string;
}

export interface RebalanceItem {
  symbol: string;
  name?: string;
  target_weight: number;
  current_weight: number;
  delta_weight: number;
  score?: number;
  slippage_bps?: number;
}

export interface SignalPreviewData {
  target_date: string;
  n_target_positions: number;
  expected_turnover: number;
  estimated_slippage_bps: number;
  buys: RebalanceItem[];
  sells: RebalanceItem[];
}

export interface ReportSynthesis {
  stance: string;
  market_view: string;
  risk_alert: string;
  action_recommendation: string;
}

export interface ReportSection {
  title: string;
  lines: string[];
}

export interface DailyReportItem {
  date: string;
  generated_at: string;
  sections: ReportSection[];
  markdown: string;
  tips: string[];
  market_compass?: MarketCompass;
  style_factors?: StyleFactorsData;
  portfolio_risk?: PortfolioRiskData;
  signal_preview?: SignalPreviewData;
  synthesis?: ReportSynthesis;
}

export interface DailyReportResponse {
  report: DailyReportItem | null;
  history: string[];
}

export interface DispatchResponse {
  ok: boolean;
  date: string;
  dispatched: boolean;
  message: string;
}
