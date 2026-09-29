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
                      t_stat: number | null; n_days: number;
                      n_independent?: number }>;
  /**
   * ⚠️ 2026-09-21（审计 P1-25）**本接口此前声明的是不存在的键**：
   * 后端 `quantile_spread_report` 返回 `ls_annualized` / `ls_t_stat` /
   * `long_short_nav` / `monotonic`，而这里写的是 `spread_annualized` +
   * `long_short_nav`（后者当时也不存在）⇒ TS 检查通过、运行时静默 `undefined`，
   * 前端图表恒空、年化价差永不显示。现按后端真实返回值对齐。
   */
  quantile_spread: {
    horizon: number;
    n_quantiles: number;
    quantile_mean_ret: Record<string, number>;
    /** 多空净值序列（按观测日复利 h 日多空价差，窗口重叠） */
    long_short_nav: Array<{ date: string; nav: number }>;
    /** 每日期多空价差均值，**单位是 h 日期收益**（非日收益） */
    ls_mean_daily: number | null;
    /** 重叠校正后的 t 值（n_eff = n/h） */
    ls_t_stat: number | null;
    /** 年化价差 = ls_mean_daily × 252 / horizon */
    ls_annualized: number | null;
    monotonic: boolean;
    n_days: number;
    n_independent?: number;
    annualization_basis?: string;
    t_stat_basis?: string;
    nav_basis?: string;
    ls_mean_basis?: string;
  };
  from_cache?: boolean;
}

export const backtestApi = {
  runTopK: (req: TopKBacktestRequest, signal?: AbortSignal) =>
    post<BacktestResultData>('/api/v1/backtest/run', req, 120_000, { signal }),

  signalAnalysis: (req: SignalAnalysisRequest, signal?: AbortSignal) =>
    post<SignalAnalysisResult>('/api/v1/backtest/signal-analysis', req, 120_000, { signal }),
};
