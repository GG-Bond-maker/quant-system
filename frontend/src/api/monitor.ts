/** 因子健康度监控 + AI 日报 + NL-to-Factor（前沿演进 Phase 1/2）。 */
import { get, post } from './client';

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
              note?: string };
  half_life?: { value: number | null; note: string;
                mean_ic_by_horizon: Record<string, number | null> };
  psi?: { ok?: boolean; error?: string; recent_days?: number; baseline_days?: number;
          n_factors?: number; mean?: number; max?: number; top?: PsiFactor[] };
  ks?: { ok?: boolean; error?: string; recent_days?: number; baseline_days?: number;
         n_factors?: number; mean?: number; max?: number; n_over_crit?: number;
         note?: string; top?: Array<{ factor: string; ks: number }> };
  ic_series_tail?: IcSeriesPoint[];
  thresholds?: Record<string, number>;
  state_log?: Array<{ ts: string; state: string; ic_state: string;
                      drift_state: string; mean_ic15: number | null;
                      psi_max: number | null }>;
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
  run: () => post<MonitorSnapshot>('/api/v1/monitor/run', undefined, 120_000),
  report: (date?: string) =>
    get<{ report: DailyReport | null; history: string[] }>(
      `/api/v1/report/daily${date ? `?date=${date}` : ''}`, undefined, 15_000),
  generateReport: () =>
    post<DailyReport>('/api/v1/report/daily/generate', undefined, 60_000),
  nlToFactor: (text: string, horizon = 5) =>
    post<NlFactorResult>('/api/v1/studio/nl-to-factor', { text, horizon }, 120_000),
};
