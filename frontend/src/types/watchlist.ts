/** 我的收藏（Watchlist）类型定义（与后端 /api/v1/watchlist 对齐）。 */

export interface WatchSummary {
  count: number;
  stock_count: number;
  etf_count: number;
  avg_pct: number | null;
  /** 主力资金净流入合计（亿元），仅股票口径；无数据为 null */
  flow_total_yi: number | null;
  alert_count: number;
  alert_kinds: string[];
  quote_date: string | null;
}

export interface WatchItem {
  symbol: string;
  code: string;
  type: 'stock' | 'etf';
  name: string | null;
  close: number | null;
  /** 当日涨跌幅（%） */
  pct: number | null;
  /** 成交额（亿元） */
  amount_yi: number | null;
  /** K线形态：均线多头/均线空头/放量突破/触及支撑/窄幅震荡/平稳 */
  kline_state: string | null;
  /** 预警信号：突破MA20 / 放量异动 / 组合 */
  alert: string | null;
  date: string | null;
  /** 最近 30 个交易日收盘价（迷你 K 线用） */
  closes: Array<number | null>;
  pe: number | null;
  pb: number | null;
  source: string | null;
}

export interface WatchDashboard {
  /** 后端整体状态：超时/部分数据源不可用时为 degraded（F-06 必须消费） */
  status?: 'ok' | 'degraded';
  reason?: string | null;
  summary: WatchSummary;
  items: WatchItem[];
}

export interface CorrResult {
  symbols: string[];
  /** corr.matrix[i][j] 为 symbols[i] 与 symbols[j] 的相关系数（-1~1，缺失为 null） */
  matrix: Array<Array<number | null>>;
  days: number;
}

export interface WatchlistRiskSummaryData {
  asset_count: number;
  valid_count: number;
  window_days: number;
  start_date: string | null;
  end_date: string | null;
  /** 组合等权年化波动率（%） */
  portfolio_ann_vol: number | null;
  /** 相对沪深 300 基准 Beta */
  portfolio_beta: number | null;
  /** 与基准收益率相关系数 */
  benchmark_corr: number | null;
  /** 区间等权组合最大回撤（%） */
  max_drawdown: number | null;
  /** 95% 历史日度在险价值（%） */
  var_95?: number | null;
  /** 95% 条件在险价值/预期尾部亏损（%） */
  cvar_95?: number | null;
  /** 分散化倍数 (Diversification Ratio): 资产波动率加权均值 / 组合波动率 */
  diversification_ratio: number | null;
  /** 权重最大的第一大行业 */
  top_industry: string | null;
  /** 第一大行业权重占比（%） */
  top_industry_ratio: number | null;
  /** 有效行业数（基于赫芬达尔指数反推的分散维度） */
  effective_sectors: number | null;
}

export interface WatchlistIndustryBreakdown {
  industry: string;
  /** 占比（%） */
  weight: number;
  count: number;
  symbols: string[];
}

export interface WatchlistAssetRiskItem {
  symbol: string;
  code: string;
  name: string;
  type: 'stock' | 'etf';
  industry: string;
  /** 等权权重（%） */
  weight: number;
  /** 单标的年化波动率（%） */
  ann_vol: number | null;
  /** 单标的基准 Beta */
  beta: number | null;
  /** 单标的区间最大回撤（%） */
  mdd: number | null;
  /** 区间总收益率（%） */
  period_return: number | null;
}

export interface WatchlistRiskResponse {
  status: 'ok' | 'degraded';
  reason?: string | null;
  summary: WatchlistRiskSummaryData;
  industry_breakdown: WatchlistIndustryBreakdown[];
  assets: WatchlistAssetRiskItem[];
}
