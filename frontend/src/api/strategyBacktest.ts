/** 策略回测（趋势跟踪）API 与类型：与后端 /api/v1/backtest/strategy-run 对齐。 */
import { post } from './client';

export interface StrategyBacktestRequest {
  strategy_name: string;
  start: string;
  end: string;
  init_cash: number;
  /** 小数（0.0003 = 0.03%） */
  commission_rate: number;
  short_ma: number;
  long_ma: number;
  /** %（3 = 3%） */
  trailing_stop_pct: number;
  symbols: string[];
  /** P2-16：参数网格寻优（给出即先搜索最优组合，再用最优参数跑回测） */
  optimize_params?: Record<string, number[]>;
  optimize_method?: 'grid' | 'ga' | 'optuna';
  /** walk-forward 折外验证（§4.4 防过拟合）：IS 寻优 → OOS 折外评估 */
  walk_forward?: boolean;
  wf_folds?: number;
}

export interface StrategyNavPoint {
  date: string;
  strategy: number;
  benchmark: number | null;
}

export interface StrategySignal {
  date: string;
  action: '买入' | '卖出';
  symbol: string;
  nav: number;
}

export interface StrategyTrade {
  date: string;
  symbol: string;
  side: '买入' | '卖出';
  price: number;
  qty: number;
  fee: number;
  /** 卖出单盈亏（元）；买入为 null */
  pnl: number | null;
}

export interface StrategyMonthly {
  month: string;
  value: number;
}

export interface StrategyKpi {
  annual_strategy: number | null;
  annual_benchmark: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
}

export interface StrategyRisk {
  alpha: number | null;
  beta: number | null;
  sortino: number | null;
  info_ratio: number | null;
  win_rate: number | null;
  avg_pnl_ratio: number | null;
  total_trades: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  annual_strategy: number | null;
  annual_benchmark: number | null;
}

/** 流动性/摩擦成本披露（P1-7） */
export interface StrategyLiquidity {
  /** 流动性数据来源：real_daily_bar = 本地日线真实量额（原始口径） */
  source: string;
  /** 是否计入冲击成本（ma_cross 原引擎未计） */
  impact_cost_included: boolean;
  note: string;
}

/** 参数寻优结果（P2-16 + §4.4 walk-forward；响应中 optimization 块） */
export interface StrategyOptimization {
  method: string;
  n_trials?: number;
  search_method?: string;
  best_params?: Record<string, number>;
  best_sharpe?: number;
  /** Deflated Sharpe（多重试验惩罚后的夏普；NaN 时为 null） */
  deflated_sharpe?: number | null;
  top5?: Array<{ params: Record<string, number>; objective: number }>;
  /** walk-forward 折外验证（method='walk_forward' 时存在） */
  n_folds?: number;
  folds?: Array<{ fold: number; is_window: string[]; oos_window: string[];
                  best_params: Record<string, number>;
                  is_sharpe: number; oos_sharpe: number; n_trials: number }>;
  mean_is_sharpe?: number;
  mean_oos_sharpe?: number;
  overfit_ratio?: number | null;
  note?: string;
}

export interface StrategyBacktestResult {
  strategy_name: string;
  start: string;
  end: string;
  init_cash: number;
  commission_rate: number;
  short_ma: number;
  long_ma: number;
  trailing_stop_pct: number;
  symbols: string[];
  kpi: StrategyKpi;
  nav_curve: StrategyNavPoint[];
  signals: StrategySignal[];
  monthly_returns: StrategyMonthly[];
  trades: StrategyTrade[];
  risk: StrategyRisk;
  liquidity?: StrategyLiquidity;
  /**
   * 复权口径披露（审计 B7b F6）。后端恒返回；**旧缓存 payload 可能没有**，
   * 故前端按可选处理并显示"复权口径未知"，不得硬编码 QFQ。
   */
  price_basis?: {
    kind: 'platform';
    /** qfq=全部前复权 / raw=全部回退不复权 / mixed=部分回退 */
    basis: 'qfq' | 'raw' | 'mixed';
    /** 缺 QFQ 分区而回退到不复权日线的标的（basis≠qfq 时非空） */
    raw_fallback_symbols: string[];
    note: string;
  };
  /**
   * 基准口径披露（审计 P0-4 下半条）。`synthetic=true` 时 `kpi.annual_benchmark`
   * 的 0.0 是**构造值**（基准不可得/无重叠交易日），前端必须标注而非直显。
   * 旧缓存 payload 可能没有此字段 ⇒ 按可选处理。
   */
  benchmark_basis?: {
    kind: 'platform';
    synthetic: boolean;
    basis: 'index_sh000300' | 'synthetic_flat';
    reason: string | null;
    note: string;
  };
  /** P2-16：开启寻优时返回（最优参数已应用到此回测结果） */
  optimization?: StrategyOptimization;
  from_cache?: boolean;
}

export const strategyBacktestApi = {
  // 寻优 = 网格组合数 × 单次回测耗时，超时相应放宽
  run: (req: StrategyBacktestRequest, signal?: AbortSignal) =>
    post<StrategyBacktestResult>('/api/v1/backtest/strategy-run', req,
      req.optimize_params ? 600_000 : 120_000, { signal }),
};
