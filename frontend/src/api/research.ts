/** 策略研究工作台 API（/api/v1/research/*）：四象限全部真实计算。 */

import { TIMEOUT } from './timeouts';
import { get, post, type RequestOptions } from './client';

/* ==================== 顶栏总览 ==================== */
export interface ResearchOverview {
  factor_count: number;
  expression_count: number;
  /** 已登记模型版本数。⚠️ 读取 model_registry 失败时后端如实回 `null`（**不再静默显示 0**
   *  —— 0 会被读成"没有模型"，与"读取失败"是两回事）。消费方必须 null-safe：未知渲染「—」。 */
  model_version_count: number | null;
  /** `model_version_count` 为 `null` 时的失败说明（成功时通常为 null/缺省）。 */
  model_version_note?: string | null;
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

export interface FactorDecayRow {
  lag: number;
  mean_ic: number | null;
  icir: number | null;
  t_stat: number | null;
  positive_ratio?: number | null;
  n_days?: number | null;
}

export interface FactorDecayResponse {
  factor: string;
  neutralize_size: boolean;
  lags: FactorDecayRow[];
  half_life_lag: number | null;
  suggested_holding_period: string;
}


export const researchApi = {
  overview: (options?: RequestOptions) => get<ResearchOverview>('/api/v1/research/overview', undefined, TIMEOUT.aggregate, options),

  factorIcir: (req: { factors: string[]; horizon: number; neutralize_size: boolean }, options?: RequestOptions) =>
    post<{ horizon: number; neutralize_size: boolean; rows: FactorIcirRow[]; available_factors: string[] }>(
      '/api/v1/research/factor-icir', req, TIMEOUT.heavy, options),

  factorCorr: (req: { factors: string[]; window_days?: number }, options?: RequestOptions) =>
    post<{ factors: string[]; matrix: number[][]; high_corr_pairs: Array<{ a: string; b: string; r: number }>; window_days: number }>(
      '/api/v1/research/factor-corr', req, TIMEOUT.heavy, options),

  factorQuantile: (req: { factor: string; horizon: number; n_quantiles?: number }, options?: RequestOptions) =>
    post<{
      quantiles: number; horizon: number;
      curves: Record<string, Array<{ date: string; nav: number }>>;
      long_short_curve?: Array<{ date: string; nav: number }>;
      annualized_returns?: Record<string, number>;
      monotonicity_score?: number;
      is_monotonic?: boolean;
      labels: Record<string, string>;
    }>('/api/v1/research/factor-quantile', req, TIMEOUT.heavy, options),

  factorDecay: (req: { factor: string; lags?: number[]; neutralize_size?: boolean }, options?: RequestOptions) =>
    post<FactorDecayResponse>('/api/v1/research/factor-decay', req, TIMEOUT.heavy, options),

  /** ML Lab：生产模型预测的分年稳定性（逐年 RankIC/ICIR/命中率，§4.5）。
   *  ⚠️ 后端实测 18.8~19.6s（逐年 RankIC 真实计算）> client 默认 15s，
   *  必须显式放宽到 120s，否则前端必然超时。2026-10-01 复测冷启动 >30s，
   *  120s 预算保持不变（已高于 60s 档）。 */
  labYearly: (options?: RequestOptions) => get<LabYearlyRow[]>('/api/v1/research/lab/yearly', undefined, TIMEOUT.heavy, options),

  experiments: (options?: RequestOptions) =>
    get<Array<{
      model_name: string; version: string; status: string; is_production: number;
      feature_version: string; created_at: string;
      train_start: string | null; train_end: string | null;
      metrics: Record<string, number | null>;
      hyperparams: Record<string, number | null>;
      has_model_file: boolean;
    }>>('/api/v1/research/experiments', undefined, TIMEOUT.aggregate, options),

  cvFolds: (req: { n_splits: number; purge_window: number; embargo_window: number }, options?: RequestOptions) =>
    post<{
      total_days: number; date_start: string; date_end: string;
      folds: Array<{
        fold: number; train_start: string; train_end: string;
        test_start: string; test_end: string;
        train_days: number; test_days: number; gap_days: number;
        purge_window: number; embargo_window: number;
      }>;
    }>('/api/v1/research/cv-folds', req, TIMEOUT.heavy, options),

  /** 组合净化交叉验证 (CPCV) 与过拟合概率 (PBO) 诊断 */
  cpcv: (req: { n_groups?: number; k_test?: number; purge_window?: number; embargo_window?: number; factors?: string[] }, options?: RequestOptions) =>
    post<CpcvResult>('/api/v1/research/cpcv', req, TIMEOUT.heavy, options),


  featureImportance: (topK = 12, options?: RequestOptions) =>
    get<{
      model_version: string;
      items: Array<{ feature: string; gain: number }>;
      effect_curves: Array<{ feature: string; points: Array<{ x: number; y: number }> }>;
      // 2026-10-01 实测冷启动 19.4s（SWR 缓存过期后首个请求），超 client 默认 15s，
      // 放宽到 60s（与 datacenter/overview 同因）。
    }>(`/api/v1/research/feature-importance?top_k=${topK}`, undefined, TIMEOUT.medium, options),

  optimize: (req: {
    assets: Array<{ code: string; weight: number }>;
    method: 'risk_parity' | 'max_div' | 'mvo' | 'inverse_vol' | 'hrp' | 'black_litterman';
    weight_cap: number; turnover_penalty: number; cov_window?: number;
  }, options?: RequestOptions) => post<{
    method: string; symbols: string[];
    prev_weights: Record<string, number>;
    weights: Record<string, number>;
    exposure_before: Record<string, number | null>;
    exposure_after: Record<string, number | null>;
    style_factor_map: Record<string, string>;
    n_obs: number;
    /** 审计 P1-13：单资产上限可行性/生效性。
     *  `feasible=false` 表示 n·cap<1 ⇒ 解只能投出 max_invested_ratio（其余现金）；
     *  `cap_enforced=false` 表示该方案根本没执行上限（实际 max_weight > cap）。 */
    weight_cap_info?: {
      cap: number; n_assets: number; feasible: boolean; cap_enforced: boolean;
      invested_ratio: number; max_weight: number | null;
      max_invested_ratio: number; min_feasible_cap: number; note: string;
    };
    /** 审计 B2-11：`mvo` 的预期收益口径。`basis='unavailable'` ⇒ μ≡0，
     *  风险厌恶系数无影响，结果实为纯风险最小化（不是均值-方差最优解）。 */
    expected_returns?: {
      basis: 'unavailable'; value: 'zero'; risk_aversion_effective: boolean;
      note: string;
    } | Record<string, never>;
  }>('/api/v1/research/optimize', req, TIMEOUT.export, options),

  impactSim: (req: {
    symbol: string; side: 'buy' | 'sell'; order_amount: number;
    participation_cap: number; algo: 'market' | 'vwap' | 'twap';
    split_days: number; lookback_days?: number;
  }, options?: RequestOptions) => post<{
    symbol: string; algo: string; side: string; order_amount: number;
    fills: Array<{
      date: string; ref_price: number; exec_price: number; amount: number;
      participation: number; impact_bps: number; side: string;
    }>;
    unfilled: number; total_impact_cost: number; avg_impact_bps: number;
    /** VWAP 坏点修正等数据质量提示（如实透出） */
    data_warnings?: string[];
    price_series: Array<{ date: string; open: number; close: number; vwap: number }>;
  }>('/api/v1/research/impact-sim', req, TIMEOUT.heavy, options),

  stressTest: (req: {
    assets: Array<{ code: string; weight: number }>;
    top_windows?: number; window?: number;
  }, options?: RequestOptions) => post<{
    window: number;
    scenarios: Array<{
      window_start: string; window_end: string;
      market_return: number; market_mdd: number;
      var_95: number; cvar_95: number; n_days: number;
      portfolio_return: number | null; portfolio_mdd: number | null;
      portfolio_var_95: number | null;
    }>;
    note: string;
  }>('/api/v1/research/stress-test', req, TIMEOUT.export, options),

  /** S3-2: 策略版本快照变更记录 */
  getStrategyChangelogs: (strategyId: string, options?: RequestOptions) =>
    get<StrategyChangelogItem[]>(`/api/v1/research/strategies/${encodeURIComponent(strategyId)}/changelogs`, undefined, TIMEOUT.aggregate, options),
  createStrategyChangelog: (
    strategyId: string,
    req: { version_tag: string; commit_message: string; params: Record<string, any>; metrics: Record<string, any> },
    options?: RequestOptions,
  ) =>
    post<StrategyChangelogItem>(`/api/v1/research/strategies/${encodeURIComponent(strategyId)}/changelogs`, req, TIMEOUT.aggregate, options),
  diffStrategyChangelogs: (
    strategyId: string,
    v1_id: number,
    v2_id: number,
    options?: RequestOptions,
  ) =>
    get<StrategyChangelogDiffResult>(`/api/v1/research/strategies/${encodeURIComponent(strategyId)}/changelogs/diff?v1_id=${v1_id}&v2_id=${v2_id}`, undefined, TIMEOUT.aggregate, options),
};

export interface StrategyChangelogItem {
  id: number;
  strategy_id: string;
  version_tag: string;
  commit_message: string;
  params: Record<string, any>;
  metrics: Record<string, any>;
  created_by?: string;
  created_at: string | null;
}

export interface StrategyChangelogDiffResult {
  v1: { id: number; version_tag: string; commit_message: string };
  v2: { id: number; version_tag: string; commit_message: string };
  param_diffs: Array<{ param: string; v1_val: any; v2_val: any }>;
  metric_diffs: Array<{ metric: string; v1_val: number | null; v2_val: number | null; diff: number | null }>;
}


/* ==================== ML Lab：分年稳定性（§4.5，Sprint4） ==================== */
export interface LabYearlyRow {
  year: number;
  rank_ic: number;
  icir: number | null;
  hit_rate: number;
  n_days: number;
}

export interface CpcvResult {
  has_data: boolean;
  n_groups: number;
  k_test: number;
  n_paths: number;
  purge_window: number;
  embargo_window: number;
  models_evaluated: string[];
  pbo: number;
  is_robust: boolean;
  mean_is_ic: number;
  mean_oos_ic: number;
  degradation_pct: number;
  rank_distribution: Array<{ bin: string; count: number }>;
  paths: Array<{
    path_id: number;
    train_groups: number[];
    test_groups: number[];
    best_model: string;
    is_rank_ic: number | null;
    oos_rank_ic: number | null;
    oos_rank_pct: number;
    is_overfit: boolean;
  }>;
  summary: string;
}

