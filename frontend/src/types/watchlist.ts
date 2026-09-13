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
  summary: WatchSummary;
  items: WatchItem[];
}

export interface CorrResult {
  symbols: string[];
  /** corr.matrix[i][j] 为 symbols[i] 与 symbols[j] 的相关系数（-1~1，缺失为 null） */
  matrix: Array<Array<number | null>>;
  days: number;
}
