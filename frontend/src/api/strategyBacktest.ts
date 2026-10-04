/** 策略回测（趋势跟踪）API 与类型：与后端 /api/v1/backtest/strategy-run 对齐。 */

import { TIMEOUT, longTimeout } from './timeouts';
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
  /**
   * 本次寻优**跳过**的组合数（P8-c 续跑）。
   *
   * 后端为网格寻优落了检查点：同一请求体重试时，已完成的参数组合会被跳过并复用其结果。
   * >0 表示本次是**续跑**（例如上次跑到 480/500 被取消或超时）。
   * 缺失/0 表示全新搜索。展示时建议注明"续跑跳过 N 组"，避免误读为"只试了这么多次"。
   */
  n_skipped?: number;
  /**
   * 寻优被**显式取消**（P8-c）。为 true 时 `best_params` 为空、`n_trials=0`，
   * 后端不返回半成品的"最优参数"（绝不编造未跑完的搜索结论）。
   * 取消粒度是**一个参数组合**：当前组合会跑完才停。
   */
  cancelled?: boolean;
  /** 取消原因（`cancelled=true` 时存在） */
  reason?: string;
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

/**
 * 寻优检查点进度（P8-c）。
 *
 * 后端网格级寻优每完成一个参数组合就落检查点；本接口读它，用于展示
 * "已算 N / 共 M 组"。`cancel_requested` 反映取消标志是否已置位。
 */
export interface OptimizationProgress {
  /** 后端缓存键前 8 位（与 strategy-run 同源，仅用于排障） */
  signature: string;
  /** 已完成组合数（含续跑跳过的） */
  n_done: number;
  /** 总组合数；未开寻优或无网格时为 null */
  total: number | null;
  /** 剩余组合数（total 为 null 时也是 null） */
  remaining: number | null;
  /** 取消标志是否已置位 */
  cancel_requested: boolean;
  has_checkpoint: boolean;
}

export const strategyBacktestApi = {
  // 寻优 = 网格组合数 × 单次回测耗时，超时相应放宽。
  // 🔴 长超时必须经 longTimeout(路径) 取得（闸门 C5）：未在 LONG_PATHS 登记会立即抛错，
  // 避免"新路径复用了保留档位却漏登记 ⇒ 被后端默认预算误杀"。
  run: (req: StrategyBacktestRequest, signal?: AbortSignal) =>
    post<StrategyBacktestResult>('/api/v1/backtest/strategy-run', req,
      req.optimize_params
        ? longTimeout('/api/v1/backtest/strategy-run')
        : TIMEOUT.heavy, { signal }),

  /**
   * 取消进行中的参数寻优（P8-c）。
   *
   * 必须传**与 run 完全相同的请求体** —— 后端用请求签名（即缓存键）定位目标，
   * 因而不需要"任务 ID"，也就不改变任何现有契约。
   *
   * ⚠️ 取消粒度是**一个参数组合**：当前组合会跑完才停（不动回测引擎日循环的
   * 刻意取舍）。因此调用后 `run` 不会立刻返回，界面应提示"将在当前组合跑完后停止"。
   * 已完成组合的检查点会保留 ⇒ 同一请求体重试可续跑。
   */
  cancel: (req: StrategyBacktestRequest) =>
    post<{ cancelled: boolean; granularity: string; note: string }>(
      '/api/v1/backtest/strategy-run/cancel', req, TIMEOUT.default),

  /**
   * 查询寻优进度（P8-c）。
   *
   * ⚠️ 用 `TIMEOUT.probe`（10s）而不是默认 15s：它是**轮询**接口，
   * 只需要读一个检查点键，绝不该拖住界面。
   */
  optStatus: (req: StrategyBacktestRequest) =>
    post<OptimizationProgress>('/api/v1/backtest/strategy-run/opt-status',
      req, TIMEOUT.probe),
};
