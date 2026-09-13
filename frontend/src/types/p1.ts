/**
 * P1 业务类型：Screener / Backtest。
 */
export interface ScreenerItem {
  symbol: string;
  name: string | null;
  industry: string | null;
  close: number | null;
  /** 当日涨跌幅（%） */
  pct: number | null;
  /** 涨跌停幅度（%，板块规则值，非当日涨跌） */
  limit_pct: number | null;
  score: number;
  risk: 'low' | 'mid' | 'high';
}

export interface ScreenerResult {
  date: string;
  strategy: string;
  top_k: number;
  board: string;
  count: number;
  items: ScreenerItem[];
  /** true=缓存命中 / 'refreshed'=refresh=1 强制重算完成 / false=实时计算 */
  from_cache: boolean | 'refreshed';
  /** 缓存已软过期（旧值服务中，后端后台重建里） */
  stale?: boolean;
  /**
   * 数据时效披露（后端交易日历口径，审计 P1-4）。
   *
   * `lag_trading_days` = 榜单日期相对「最近已收盘交易日」落后的交易日数；
   * 为 null 表示日历不可用/日期异常，前端按「未知」处理（不误报陈旧或最新）。
   * 不参与缓存，每次请求实时计算。
   */
  freshness?: {
    as_of: string | null;
    expected: string | null;
    lag_trading_days: number | null;
    is_stale: boolean | null;
    note: string;
  };
}

/** 自选股行情快照条目（GET /api/v1/screener/watchlist） */
export interface WatchlistQuote {
  symbol: string;
  name: string | null;
  industry: string | null;
  close: number | null;
  /** 当日涨跌幅（%） */
  pct: number | null;
  /** 行情日期（YYYY-MM-DD） */
  date: string | null;
  /** 最新一期预测分（无预测为 null） */
  score: number | null;
  risk: 'low' | 'mid' | 'high' | null;
}

export interface WatchlistQuotes {
  count: number;
  items: WatchlistQuote[];
}

export interface BacktestMetrics {
  n_days: number;
  total_return: number;
  annual_return: number;
  annual_vol: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
  profit_loss_ratio: number;
  annual_turnover: number | null;
  [k: string]: number | string | null | undefined;
}

export interface BacktestResultData {
  start: string;
  end: string;
  top_k: number;
  trading_days: number;
  filled_trades: number;
  rejected_trades: Record<string, number>;
  metrics: BacktestMetrics;
  nav_tail: Array<{ date: string; nav: number }>;
  equity_curve: Array<{ date: string; nav: number; equity: number; cash: number }>;
  drawdown_curve: Array<{ date: string; drawdown: number }>;
  annual_returns: Record<string, number>;
  holdings: Array<{ date: string; holdings: Record<string, number> }>;
  trades: Array<{ date: string; symbol: string; side: string; price: number;
                 qty: number; amount: number; cost: number; reason: string }>;
  friction_costs: Record<string, number>;
  enable_friction: boolean;
  model_version: string;
  from_cache: boolean;
}
