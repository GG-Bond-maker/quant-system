/** 策略研究工作台 API（/api/v1/research/*）：四象限全部真实计算。 */
import { get, post } from './client';

/* ==================== 顶栏总览 ==================== */
export interface ResearchOverview {
  factor_count: number;
  expression_count: number;
  model_version_count: number;
  capacity_estimate_yi: number | null;
  capacity_formula: string;
  factor_universe: string;
}

/* ==================== 左上：因子研发 ==================== */
export interface FactorIcirRow {
  factor: string;
  mean_ic: number | null;
  icir: number | null;
  t_stat: number | null;
  positive_ratio: number | null;
  /** 近 60 个交易日的逐日 Rank IC（真实序列） */
  ic_series: number[];
  n_days: number;
}

export const researchApi = {
  overview: () => get<ResearchOverview>('/api/v1/research/overview'),

  factorIcir: (req: { factors: string[]; horizon: number; neutralize_size: boolean }) =>
    post<{ horizon: number; neutralize_size: boolean; rows: FactorIcirRow[]; available_factors: string[] }>(
      '/api/v1/research/factor-icir', req, 120_000),

  factorCorr: (req: { factors: string[]; window_days?: number }) =>
    post<{ factors: string[]; matrix: number[][]; high_corr_pairs: Array<{ a: string; b: string; r: number }>; window_days: number }>(
      '/api/v1/research/factor-corr', req, 120_000),

  factorQuantile: (req: { factor: string; horizon: number; n_quantiles?: number }) =>
    post<{
      quantiles: number; horizon: number;
      curves: Record<string, Array<{ date: string; nav: number }>>;
      labels: Record<string, string>;
    }>('/api/v1/research/factor-quantile', req, 120_000),

  /** ML Lab：生产模型预测的分年稳定性（逐年 RankIC/ICIR/命中率，§4.5） */
  labYearly: () => get<LabYearlyRow[]>('/api/v1/research/lab/yearly'),

  experiments: () =>
    get<Array<{
      model_name: string; version: string; status: string; is_production: number;
      feature_version: string; created_at: string;
      train_start: string | null; train_end: string | null;
      metrics: Record<string, number | null>;
      hyperparams: Record<string, number | null>;
      has_model_file: boolean;
    }>>('/api/v1/research/experiments'),

  cvFolds: (req: { n_splits: number; purge_window: number; embargo_window: number }) =>
    post<{
      total_days: number; date_start: string; date_end: string;
      folds: Array<{
        fold: number; train_start: string; train_end: string;
        test_start: string; test_end: string;
        train_days: number; test_days: number; gap_days: number;
        purge_window: number; embargo_window: number;
      }>;
    }>('/api/v1/research/cv-folds', req, 120_000),

  featureImportance: (topK = 12) =>
    get<{
      model_version: string;
      items: Array<{ feature: string; gain: number }>;
      effect_curves: Array<{ feature: string; points: Array<{ x: number; y: number }> }>;
    }>(`/api/v1/research/feature-importance?top_k=${topK}`),

  optimize: (req: {
    assets: Array<{ code: string; weight: number }>;
    method: 'risk_parity' | 'max_div' | 'mvo' | 'inverse_vol';
    weight_cap: number; turnover_penalty: number; cov_window?: number;
  }) => post<{
    method: string; symbols: string[];
    prev_weights: Record<string, number>;
    weights: Record<string, number>;
    exposure_before: Record<string, number | null>;
    exposure_after: Record<string, number | null>;
    style_factor_map: Record<string, string>;
    n_obs: number;
  }>('/api/v1/research/optimize', req, 180_000),

  impactSim: (req: {
    symbol: string; side: 'buy' | 'sell'; order_amount: number;
    participation_cap: number; algo: 'market' | 'vwap' | 'twap';
    split_days: number; lookback_days?: number;
  }) => post<{
    symbol: string; algo: string; side: string; order_amount: number;
    fills: Array<{
      date: string; ref_price: number; exec_price: number; amount: number;
      participation: number; impact_bps: number; side: string;
    }>;
    unfilled: number; total_impact_cost: number; avg_impact_bps: number;
    /** VWAP 坏点修正等数据质量提示（如实透出） */
    data_warnings?: string[];
    price_series: Array<{ date: string; open: number; close: number; vwap: number }>;
  }>('/api/v1/research/impact-sim', req, 120_000),

  stressTest: (req: {
    assets: Array<{ code: string; weight: number }>;
    top_windows?: number; window?: number;
  }) => post<{
    window: number;
    scenarios: Array<{
      window_start: string; window_end: string;
      market_return: number; market_mdd: number;
      var_95: number; cvar_95: number; n_days: number;
      portfolio_return: number | null; portfolio_mdd: number | null;
      portfolio_var_95: number | null;
    }>;
    note: string;
  }>('/api/v1/research/stress-test', req, 180_000),
};


/* ==================== ML Lab：分年稳定性（§4.5，Sprint4） ==================== */
export interface LabYearlyRow {
  year: number;
  rank_ic: number;
  icir: number | null;
  hit_rate: number;
  n_days: number;
}
