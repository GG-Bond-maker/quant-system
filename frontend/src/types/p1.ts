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
  /** 当日成交额（元）；后端 Alpha 榜已返回，前端此前缺字段 */
  amount: number | null;
  /** 当日换手率（%） */
  turnover: number | null;
  /** 涨跌停幅度（%，板块规则值，非当日涨跌） */
  limit_pct: number | null;
  score: number;
  /** 模型预测信号强度；不表示投资风险。 */
  signal_strength: 'weak' | 'neutral' | 'strong';
}

export interface ScreenerResult {
  date: string | null;
  /** 统一可用性契约：空结果仍通过 HTTP 200 返回。 */
  status: 'ok' | 'degraded' | 'unavailable';
  as_of: string | null;
  /** 稳定机器码；面向用户展示 message。 */
  reason: string | null;
  message: string;
  coverage: { available: number; total: number; ratio: number | null };
  strategy: string;
  top_k: number;
  board: string;
  count: number;
  items: ScreenerItem[];
  stats: {
    today: {
      total: number; pool_size: number; win_rate: number | null;
      avg_pct: number | null; avg_score: number | null; strong_signal: number;
      industry_count: number; top_industry_ratio: number | null;
    };
    prev: {
      total: number; pool_size: number; win_rate: number | null;
      avg_pct: number | null; avg_score: number | null; strong_signal: number;
      industry_count: number; top_industry_ratio: number | null;
    } | null;
    prev_date: string | null;
  };
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

/* ==================== 全市场股票列表（GET /api/v1/screener/stocks） ==================== */

/** 服务端筛选 + 排序 + 分页返回的股票列表条目。 */
export interface StockListItem {
  symbol: string;
  name: string | null;
  industry: string | null;
  /** 板块：main / chinext_star / bse */
  board: string | null;
  /** 最新价（元） */
  close: number | null;
  /** 涨跌幅（%） */
  pct: number | null;
  /** 成交额（元） */
  amount: number | null;
  /** 成交额（亿元）；展示直接用它，禁止前端再自行 /1e8 */
  amount_yi: number | null;
  /** 总市值（亿元） */
  total_cap_yi: number | null;
  /** 流通市值（亿元） */
  float_cap_yi: number | null;
  /** 换手率（%） */
  turnover: number | null;
  is_st: boolean | null;
  is_halted: boolean | null;
  quote_status: string | null;
}

/** 各数值字段的口径说明（字段名 -> 中文说明），降级时会整体缺失 */
export type StockListBasisFields = Record<string, string>;

export interface StockListResult {
  total: number;
  page: number;
  page_size: number;
  /** 本地日终截面交易日（YYYY-MM-DD） */
  trade_date: string | null;
  /** 行情快照时刻 HH:MM:SS */
  as_of: string | null;
  /** auto / realtime / daily */
  basis: string | null;
  /** 口径一句话说明，直接展示给用户 */
  basis_desc: string | null;
  basis_fields: StockListBasisFields | null;
  source: string | null;
  /** true=外部实时行情源不可达，已回退本地日终截面 */
  degraded: boolean | null;
  /** 实时行情命中覆盖率 */
  quote_coverage: { hit: number; total: number } | null;
  /** 后端实际生效的排序键（空串=默认 code 升序） */
  sort_applied: string | null;
  dir_applied: string | null;
  items: StockListItem[];
  options: { boards: string[]; industries: string[] } | null;
  stale: boolean | null;
  from_cache: boolean | null;
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
  /** 模型预测信号强度（strong/neutral/weak）；不表示投资风险。 */
  signal_strength: 'weak' | 'neutral' | 'strong' | null;
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
