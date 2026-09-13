/** Top-K 模型回测与信号分析（/api/v1/backtest/run、signal-analysis）。 */
import { post } from './client';
import type { BacktestResultData } from '@/types/p1';

export interface TopKBacktestRequest {
  start: string;
  end: string;
  top_k: number;
  init_cash: number;
  rebalance_freq: 'daily' | 'weekly';
  enable_friction: boolean;
  model_version?: string;
}

export interface SignalAnalysisRequest {
  start: string;
  end: string;
  horizons?: number[];
  n_quantiles?: number;
  model_version?: string;
}

export interface SignalAnalysisResult {
  start: string;
  end: string;
  model_version: string;
  n_symbols: number;
  n_days: number;
  ic_summary: Array<{ horizon: number; mean_ic: number | null; icir: number | null;
                      t_stat: number | null; n_days: number }>;
  quantile_spread: {
    horizon: number;
    n_quantiles: number;
    spread_annualized: number | null;
    long_short_nav: Array<{ date: string; nav: number }>;
  };
  from_cache?: boolean;
}

export const backtestApi = {
  runTopK: (req: TopKBacktestRequest) =>
    post<BacktestResultData>('/api/v1/backtest/run', req, 120_000),

  signalAnalysis: (req: SignalAnalysisRequest) =>
    post<SignalAnalysisResult>('/api/v1/backtest/signal-analysis', req, 120_000),
};
