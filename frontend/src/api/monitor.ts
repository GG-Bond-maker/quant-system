/** 因子健康度监控 + AI 日报 + NL-to-Factor（前沿演进 Phase 1/2）。 */
import { get, post, type RequestOptions } from './client';

// ---------------- 因子健康度 ----------------
export type FactorHealthState = 'unknown' | 'healthy' | 'watch' | 'degraded';

export interface IcSeriesPoint {
  date: string;
  ic: number | null;
  n_symbols: number;
}

export interface PsiFactor {
  factor: string;
  psi: number;
}

export interface MonitorSnapshot {
  ok?: boolean;
  state: FactorHealthState;
  ic_state?: FactorHealthState;
  drift_state?: FactorHealthState;
  prev_state?: FactorHealthState | null;
  computed_at?: string;
  trigger?: string;
  model_horizon?: number;
  n_pred_dates?: number;
  n_eval_dates?: number;
  pred_model_versions?: Record<string, number>;
  recent?: { window: number; n_days: number; start?: string; end?: string;
             mean_ic?: number; icir?: number | null };
  history?: { window: number; mean_ic?: number; std_ic?: number | null;
              /** P1-17/R5：判定用的 σ 已按当日股票池宽度折算；raw 为历史原值 */
              std_ic_raw?: number | null; sigma_basis?: string;
              pool_ratio?: number | null; n_symbols_median_hist?: number | null;
              n_symbols_median_recent?: number | null; note?: string };
  half_life?: { value: number | null; note: string;
                mean_ic_by_horizon: Record<string, number | null> };
  /** P1-17 双口径：`max`/`top` = 按交易日截面标准化（**判定口径**）；
   *  `raw` = 池化原始值（含水平/尺度平移，仅披露，不参与状态判定）。 */
  psi?: { ok?: boolean; error?: string; recent_days?: number; baseline_days?: number;
          basis?: string; n_factors?: number; mean?: number; max?: number;
          top?: PsiFactor[]; note?: string;
          raw?: { basis?: string; n_factors?: number; mean?: number; max?: number;
                  top?: PsiFactor[] } };
  ks?: { ok?: boolean; error?: string; recent_days?: number; baseline_days?: number;
         basis?: string; n_factors?: number; mean?: number; max?: number;
         n_over_crit?: number;
         /** 池化样本量达 1e4~1e5 ⇒ 只能当强度读，不作结论 */
         over_crit_ratio?: number; crit_effective?: number | null;
         cross_section_median?: number | null;
         note?: string; top?: Array<{ factor: string; ks: number }> };
  ic_series_tail?: IcSeriesPoint[];
  thresholds?: Record<string, number | string | null>;
  state_log?: Array<{ ts: string; state: string; ic_state: string;
                      drift_state: string; mean_ic15: number | null;
                      psi_max: number | null; psi_max_raw?: number | null;
                      psi_basis?: string }>;
  retrain?: RetrainRecord | null;
  note?: string;
  error?: string;
}

export interface RetrainRecord {
  status?: string;
  model_version?: string;
  promoted?: boolean;
  gate_reason?: string;
  finished_at?: string;
  trigger?: string;
  started_at?: string;
  error?: string;
}

// ---------------- AI 日报 ----------------
export interface ReportSection {
  title: string;
  lines: string[];
}

export interface DailyReport {
  date: string;
  generated_at: string;
  sections: ReportSection[];
  markdown: string;
  tips: string[];
}

// ---------------- NL-to-Factor ----------------
export interface NlFactorEvaluation {
  mean_ic?: number | null;
  icir?: number | null;
  t_stat?: number | null;
  n_days?: number;
  ic_series?: Array<{ date: string; ic: number | null }>;
  long_short_nav?: Array<{ date: string; nav: number }>;
}

export interface NlFactorResult {
  provider: string;
  model: string;
  expression: string;
  horizon: number;
  valid: boolean;
  error?: string;
  evaluation?: NlFactorEvaluation | null;
}

export const monitorApi = {
  health: () => get<MonitorSnapshot>('/api/v1/monitor/health', undefined, 15_000),
  run: (options?: RequestOptions) =>
    post<MonitorSnapshot>('/api/v1/monitor/run', undefined, 120_000, options),
  report: (date?: string) =>
    get<{ report: DailyReport | null; history: string[] }>(
      `/api/v1/report/daily${date ? `?date=${date}` : ''}`, undefined, 15_000),
  generateReport: (options?: RequestOptions) =>
    post<DailyReport>('/api/v1/report/daily/generate', undefined, 60_000, options),
  nlToFactor: (text: string, horizon = 5, options?: RequestOptions) =>
    post<NlFactorResult>('/api/v1/studio/nl-to-factor', { text, horizon }, 120_000, options),
};
