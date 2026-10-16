/** 生产端模块 API：因子工作室 / 数据质量与血缘 / 执行中心 / 调度 / 归因。 */

import { TIMEOUT, longTimeout } from './timeouts';
import { del, get, post, type RequestOptions } from './client';

/* ==================== 因子自动化挖掘工作室 ==================== */
export interface GpStatus {
  task_id: string;
  status: 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'CANCELLED';
  generation: number;
  total_generations: number;
  fitness_curve: number[];
  /** 同期 best 的 MeanIC 曲线（让用户看清真实 IC 水平，不只看复合 fitness） */
  mean_ic_curve: number[];
  /** 同期 best 的 ICIR 曲线 */
  icir_curve: number[];
  best: { expr: string; fitness: number; mean_ic: number; icir: number;
          t_stat: number; n_days: number; complexity: number } | null;
  /** I-13：后端求值失败的条目会以 `st={}` 落进 history[:8]（有效表达式 <8 个时），
   *  故指标字段可能整体缺失，消费方必须按可选处理。 */
  top_expressions: Array<{ expr: string; fitness: number; mean_ic?: number | null;
                           icir?: number | null; t_stat?: number | null;
                           n_days?: number | null; max_base_corr?: number | null;
                           corr_penalty?: number | null }>;
  error: string | null;
  elapsed_sec: number;
}

/** 单表达式评估结果（/studio/alpha-eval）：IC 时序 + 多空净值 + 关键指标。 */
export interface AlphaEvalResult {
  expr: string;
  horizon: number;
  n_symbols: number;
  mean_ic: number;
  icir: number;
  t_stat: number;
  n_days: number;
  ic_series: Array<{ date: string; ic: number }>;
  long_nav: Array<{ date: string; nav: number }>;
  short_nav: Array<{ date: string; nav: number }>;
  long_short_nav: Array<{ date: string; nav: number }>;
}

export const studioApi = {
  startMining: (req: { fields: string[]; horizon: number; population: number;
                       generations: number; seed: number; corr_penalty?: number }) =>
    post<{ task_id: string; params: Record<string, unknown>; snapshot_rows: number;
           snapshot_end: string }>('/api/v1/studio/mining/start', req, TIMEOUT.medium),
  miningStatus: (taskId: string) =>
    get<GpStatus>(`/api/v1/studio/mining/status/${taskId}`),
  /** 请求取消一个运行中的挖掘任务（优雅：当前代完成后退出）。 */
  cancelMining: (taskId: string) =>
    post<{ cancelled: boolean; task_id: string }>(
      `/api/v1/studio/mining/cancel/${taskId}`, {}),
  /** 评估单个 Alpha 表达式（Rank IC 时序 + 多空净值 + 关键指标）。 */
  alphaEval: (req: { expr: string; horizon: number; symbols?: string[] }, options?: RequestOptions) =>
    post<AlphaEvalResult>('/api/v1/studio/alpha-eval', req, TIMEOUT.heavy, options),

  /* ---------------- 因子库与检测报告（§4.3，Sprint4） ---------------- */
  /** 因子检测报告：5 分组分层净值/年化 + 1/5/10/20 日衰减 + Top 组换手率 */
  factorReport: (req: { expr: string; horizon: number }, options?: RequestOptions) =>
    post<FactorReportResult>('/api/v1/studio/factor-report', req, TIMEOUT.heavy, options),

  factors: () => get<CustomFactor[]>('/api/v1/studio/factors'),

  /** 保存自定义因子（后端 AST 白名单校验 + 真实截面评估后入库） */
  saveFactor: (req: { name: string; expression: string; horizon: number }, options?: RequestOptions) =>
    post<CustomFactor>('/api/v1/studio/factors', req, TIMEOUT.heavy, options),

  deleteFactor: (id: number) =>
    del<{ id: number }>(`/api/v1/studio/factors/${id}`),

  /** 因子正交化纯化与多重检验：截面多元回归剥离动量/波动/估值暴露，输出纯化 IC、FDR 与 Deflated IC */
  orthogonalize: (req: { expr: string; horizon: number; base_factors?: string[] }, options?: RequestOptions) =>
    post<FactorOrthogonalizeResult>('/api/v1/studio/orthogonalize', req, TIMEOUT.heavy, options),
};

export interface FactorOrthogonalizeResult {
  expr: string;
  horizon: number;
  raw_mean_ic: number;
  raw_icir: number;
  pure_mean_ic: number;
  pure_icir: number;
  t_stat: number;
  p_value_raw: number;
  p_value_fdr: number;
  deflated_ic: number;
  n_obs: number;
  is_significant: boolean;
  base_corrs: Array<{
    factor: string;
    name: string;
    raw_corr: number;
    pure_corr: number;
    reduction_pct: number;
  }>;
  summary: string;
}


/** 因子检测报告（/studio/factor-report） */
export interface FactorReportResult {
  expr: string;
  horizon: number;
  mean_ic: number;
  icir: number;
  t_stat: number;
  n_days: number;
  ic_series: Array<{ date: string; ic: number }>;
  cum_ic_series?: Array<{ date: string; cum_ic: number }>;
  monthly_ic?: Record<string, number>;
  ic_stats?: {
    mean_ic: number;
    std_ic: number;
    icir: number;
    t_stat: number;
    positive_ratio: number;
    significant_ratio: number;
    skewness: number;
    kurtosis: number;
    n_days: number;
  } | null;
  /** 5 分组分层累计净值（q1=最低分位 .. q5=最高分位） */
  quintile_navs: Array<{ q: string; nav: Array<{ date: string; nav: number }> }>;
  /** 各分位年化收益（持有 horizon 日口径） */
  quintile_annual: Record<string, number | null>;
  monotonicity_score?: number;
  is_monotonic?: boolean;
  cost_adjusted_navs?: Array<{ q: string; nav: Array<{ date: string; nav: number }> }>;
  long_short_gross_nav?: Array<{ date: string; nav: number }>;
  long_short_net_nav?: Array<{ date: string; nav: number }>;
  cost_fee_rate?: number;
  style_exposures?: Array<{
    style: string;
    name: string;
    factor_col: string;
    exposure: number;
  }>;
  /** 衰减分析：horizon → RankIC/ICIR */
  decay: Record<string, { rank_ic: number; icir: number | null; n_days: number }>;
  /** Top 20% 名单日度变动率均值（持有成本代理） */
  top_turnover: number | null;
}

/** 自定义因子库条目（/studio/factors） */
export interface CustomFactor {
  id: number;
  name: string;
  expression: string;
  horizon: number;
  metrics: { mean_ic: number; icir: number; t_stat: number; n_days: number } | null;
  enabled: boolean;
  created_by: string | null;
  created_at: string;
}

/* ==================== 数据质量与血缘 / 调度 DAG ==================== */
export interface QualityScanResult {
  dataset: string; year: number; rows_scanned: number; symbols_scanned: number;
  /* 截断契约：symbols_available 是**截断前**可用标的数，truncated 为真时
     symbols_scanned 只覆盖前 scan_limit 个目录（见后端 ops.py 的 F4 修复）。 */
  symbols_available: number; total_symbols: number;
  truncated: boolean; scan_limit: number;
  n_issues: number; n_errors: number;
  by_kind: Record<string, number>;
  worst_symbols: Array<[string, number]>;
  samples: Array<{ dataset: string; symbol: string; date: string | null;
                   kind: string; severity: string; detail: string }>;
}

/**
 * 数据血缘图谱。
 *
 * ⚠️ 口径说明：拓扑（谁流向谁）是流水线代码的静态依赖，不随数据变化；
 * 节点状态（exists / rows / latest_date / records …）才是实时扫描结果。
 * 此前本端点 13 个节点、18 条边全为硬编码常量，已改为实扫 + 显式声明来源。
 */
export interface LineageGraph {
  nodes: Array<{
    id: string;
    name: string;
    group: string;
    desc?: string;
    /** ok=有产物 / missing=无产物 / unknown=无法读取 / external=外部源 / derived=训练期现算 */
    status?: 'ok' | 'missing' | 'unknown' | 'external' | 'derived';
    exists?: boolean;
    /** parquet 总行数（dataset 节点） */
    rows?: number | null;
    size_mb?: number | null;
    /** 最新数据日期 YYYY-MM-DD */
    latest_date?: string | null;
    files?: number | null;
    partitions?: number | null;
    /** SQLite 业务表记录数（table 节点） */
    records?: number | null;
    /** 生产模型（model 节点） */
    version?: string | null;
    valid_rank_ic?: number | null;
    test_rank_ic?: number | null;
    registered?: number | null;
    /** QC 隔离分区数（qc 节点） */
    quarantined_partitions?: number | null;
  }>;
  edges: Array<{ source: string; target: string }>;
  note: string;
  /** 'static' 表示拓扑为代码静态声明（非扫描推断） */
  topology_source?: 'static';
  node_states_scanned?: boolean;
  generated_at?: string | null;
  benchmark_date?: string | null;
  /** 尚无本地产物的节点名 */
  missing_nodes?: string[];
  degraded_note?: string | null;
}

export interface DagStage {
  id: string;
  name: string;
  category?: string;
  dataset: string;
  desc?: string;
  done_date: string | null;
  status: 'ok' | 'partial' | 'stale' | 'running' | 'failed' | 'pending';
  rows?: number | null;
  depends_on?: string[];
  base_duration_s?: number;
  duration_s?: number;
  offset_s?: number;
  es_s?: number;
  ef_s?: number;
  ls_s?: number;
  lf_s?: number;
  slack_s?: number;
  total_float_s?: number;
  free_float_s?: number;
  is_critical_path?: boolean;
}

export interface DagEdge {
  source: string;
  target: string;
  is_critical: boolean;
}

export interface GanttTask {
  id: string;
  name: string;
  category?: string;
  offset_s: number;
  duration_s: number;
  es_s?: number;
  ef_s?: number;
  ls_s?: number;
  lf_s?: number;
  is_critical_path: boolean;
  slack_s: number;
  total_float_s?: number;
  free_float_s?: number;
  status?: string;
}

export interface SlaMetrics {
  scheduled_start: string;
  deadline: string;
  budget_seconds: number;
  critical_path_duration_s: number;
  p50_duration_s: number;
  p95_duration_s: number;
  buffer_remaining_s: number;
  compliance_probability: number;
  sla_status: 'COMPLIANT' | 'AT_RISK' | 'BREACHED';
  critical_steps_count: number;
}

export interface ScheduledTrigger {
  id: string;
  name: string;
  trigger_time: string;
  cron: string;
  category: string;
  target_steps: string[];
  status: 'ACTIVE' | 'PAUSED';
  desc: string;
  last_run: string | null;
  next_run: string | null;
}

export interface PipelineLockStatus {
  is_locked: boolean;
  owner: string | null;
  elapsed_seconds: number;
  status: 'LOCKED' | 'IDLE';
}

export interface DataJobRecord {
  id?: number;
  job_type: string;
  trade_date: string;
  status: string;
  current_step: string | null;
  started_at?: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  traceback?: string | null;
  codes_count?: number | null;
  job_scope?: 'market' | 'subset' | null;
  task_id?: string | null;
}

export interface ShardingMetrics {
  total_shards: number;
  healthy_shards: number;
  quarantined_shards: number;
  failure_isolated: boolean;
  checkpoint_resumption_enabled: boolean;
}

export interface DagStatus {
  benchmark_date: string | null;
  stages: DagStage[];
  dag_edges?: DagEdge[];
  recent_jobs: DataJobRecord[];
  gantt_timeline?: GanttTask[];
  critical_path?: string[];
  total_duration_s?: number;
  sla_metrics?: SlaMetrics;
  sharding_metrics?: ShardingMetrics;
  scheduled_triggers?: ScheduledTrigger[];
  lock_status?: PipelineLockStatus;
  note: string;
}

export interface FeatureDriftBin {
  bin: number;
  lower: number;
  upper: number;
  expected_pct: number;
  actual_pct: number;
}

export interface FeatureDriftResult {
  factor: string;
  version: string;
  baseline_year: number;
  target_year: number;
  psi: number;
  ks_statistic: number;
  ks_pvalue: number;
  drift_level: 'stable' | 'moderate' | 'severe' | 'unknown';
  baseline_samples: number;
  target_samples: number;
  available_factors: string[];
  bins: FeatureDriftBin[];
  note: string;
}

export interface MultiSourceArbitrageItem {
  symbol: string;
  name: string;
  trade_date: string;
  primary_source: string;
  primary_price: number;
  secondary_source: string;
  secondary_price: number;
  deviation_pct: number;
  status: 'CONSISTENT' | 'TOLERABLE' | 'DISCREPANCY';
  status_label: string;
}

export interface MultiSourceArbitrageResult {
  trade_date: string;
  total_checked: number;
  consistent_count: number;
  tolerable_count: number;
  discrepancy_count: number;
  tolerance_corridor_pct: number;
  mean_deviation_pct: number;
  max_deviation_pct: number;
  items: MultiSourceArbitrageItem[];
  note?: string;
}

export interface QualityDimension {
  key: string;
  name: string;
  score: number;
  weight: number;
  benchmark: number;
  status: string;
  detail: string;
}

export interface QualityRadarResult {
  dataset: string;
  composite_score: number;
  grade: 'AAA' | 'AA' | 'A' | 'BBB';
  dimensions: QualityDimension[];
  audited_at: string;
  summary: string;
}

export interface CrossSectionalDayItem {
  date: string;
  covered_count: number;
  expected_count: number;
  coverage_pct: number;
  null_close_count: number;
  null_volume_count: number;
  status: 'NORMAL' | 'WATCH';
}

export interface CrossSectionalCoverageResult {
  total_dates: number;
  expected_universe_size: number;
  mean_coverage_pct: number;
  min_coverage_pct: number;
  dates: string[];
  categories: string[];
  series: CrossSectionalDayItem[];
  heatmap_data: Array<[number, number, number]>;
  note: string;
}

export interface BlastRadiusNode {
  id: string;
  name: string;
  group: string;
  distance: number;
  severity_impact: 'CRITICAL' | 'HIGH' | 'MEDIUM';
  desc?: string;
}

export interface BlastRadiusResult {
  source_node: {
    id: string;
    name: string;
    group: string;
  };
  impacted_nodes: BlastRadiusNode[];
  total_impacted: number;
  affected_alpha_factors: string[];
  affected_models: string[];
  blast_summary: string;
  remediation_advice: string;
}

export const opsApi = {
  qualityScan: (req: { dataset?: string; year?: number }, options?: RequestOptions) =>
    post<QualityScanResult>('/api/v1/ops/quality-scan', req, TIMEOUT.export, options),
  /** 特征协变量漂移 (PSI / KS) 稳定性检验 */
  featureDrift: (params?: { factor?: string; version?: string; baseline_year?: number; target_year?: number }, options?: RequestOptions) =>
    get<FeatureDriftResult>('/api/v1/ops/quality/feature-drift', params, TIMEOUT.medium, options),
  /** 多源行情交叉对账与偏离度仲裁 */
  multiSourceArbitrage: (limit = 50, options?: RequestOptions) =>
    get<MultiSourceArbitrageResult>('/api/v1/ops/quality/multi-source-arbitrage', { limit }, TIMEOUT.medium, options),
  /** 五维数据质量健康雷达图 */
  qualityRadar: (dataset = 'daily_bar', options?: RequestOptions) =>
    get<QualityRadarResult>('/api/v1/ops/quality/radar', { dataset }, TIMEOUT.medium, options),
  /** 全市场截面完整性与因子空值时序分布 */
  crossSectionalCoverage: (days = 30, options?: RequestOptions) =>
    get<CrossSectionalCoverageResult>('/api/v1/ops/quality/cross-sectional-coverage', { days }, TIMEOUT.medium, options),
  /** 数据血缘受损扩散分析 (Blast Radius) */
  blastRadius: (nodeId = 'daily_bar', options?: RequestOptions) =>
    get<BlastRadiusResult>('/api/v1/ops/quality/blast-radius', { node_id: nodeId }, TIMEOUT.medium, options),
  /** 后端已加跨请求 SWR 持久化缓存（TTL 300s + 日界换键），稳态命中实测约 13~41ms
   *  （QA 独立复测 6 轮 avg 22.7ms）。
   *  但**冷路径**（缓存未预热/过期后台重建）实测仍需约 33s，远超 client 默认 15s，
   *  故保留 60s 兜底，避免冷启动时前端超时并永久停在"加载血缘…"。 */
  lineage: (options?: RequestOptions) =>
    get<LineageGraph>('/api/v1/ops/lineage', undefined, TIMEOUT.medium, options),
  /** 调度 DAG：同样实扫数据集新鲜度，冷路径可能超 15s，与 lineage 同口径放宽到 60s。 */
  dag: (options?: RequestOptions) =>
    get<DagStatus>('/api/v1/ops/dag', undefined, TIMEOUT.medium, options),
  dagRerun: (req: {
    trade_date: string;
    codes?: string[];
    steps?: string[];
    resume_from_step?: string;
    job_scope?: string;
  }, options?: RequestOptions) =>
    post<{ ok: boolean; executed_steps?: string[]; summary?: string; error?: string }>(
      '/api/v1/ops/dag/rerun', req,
      // 🔴 长超时必须经 longTimeout(路径) 取得（闸门 C5），理由见 timeouts.ts
      longTimeout('/api/v1/ops/dag/rerun'), options),
};

/* ==================== 执行中心（模拟盘）与风控闸门 ==================== */
export interface PaperFill {
  exec_date: string; qty: number; price: number; amount: number;
  fee: number; impact_bps: number; participation: number; basis_bps: number;
}

export interface PaperOrder {
  id: number; symbol: string; side: string; algo: string;
  order_amount: number; filled_amount: number; split_days: number;
  status: 'PENDING' | 'PART_FILLED' | 'FILLED' | 'CANCELLED' | 'REJECTED';
  decision_price: number | null; reject_reason: string | null;
  created_at: string; fills: PaperFill[];
}

export interface PaperAccount {
  initial_cash: number; cash: number; market_value: number; equity: number;
  total_fees: number; total_impact_cost: number; n_fills: number;
  positions: Record<string, { qty: number; last_price: number;
                              market_value: number; cost_price: number; pnl: number }>;
  /** 派生指标（活跃交易日 < 30 时为 null，样本不足） */
  max_drawdown: number | null;
  sharpe: number | null;
  annualized_return: number | null;
  n_active_days: number;
  /** 逐日净值序列（由真实成交 + 真实收盘价重建） */
  nav_series: Array<{ date: string; cash: number; mv: number; equity: number }>;
  /**
   * 数据完整性告警（P1-3）：历史上若存在「卖出股数 > 当时持仓」的成交，
   * 该笔曾凭空造出现金 ⇒ 现金/权益偏高。账目仍是对已记录成交的纯推导，
   * 此字段用于**显式披露**而不是静默呈现虚高数字。正常为空数组。
   */
  integrity_warnings: string[];
}

export interface ExclusionItemT {
  id: number; symbol: string; category: string; reason: string;
  active: boolean; created_at: string;
}

export interface AttributionTimeseriesPoint {
  date: string;
  portfolio_cum_ret: number;
  benchmark_cum_ret: number;
  excess_cum_ret: number;
  cum_allocation: number;
  cum_selection: number;
  cum_interaction: number;
}

export interface StockContributionItem {
  symbol: string;
  name: string;
  industry: string;
  weight: number;
  cum_return: number;
  pnl_contrib_pct: number;
  excess_contrib_bps: number;
}

export interface PortfolioBottleneckResult {
  has_data: boolean;
  target_aum_yi: number;
  participation_cap_pct: number;
  liquidation_days_target: number;
  bottleneck_symbol: string | null;
  bottleneck_name: string | null;
  bottleneck_capacity_yi: number | null;
  weighted_dtl: number;
  holdings_count: number;
  holdings: Array<{
    symbol: string;
    name: string;
    industry: string;
    weight: number;
    position_val_wan: number;
    adv_wan: number;
    days_to_liquidate: number;
    capacity_limit_yi: number;
    stress_level: 'low' | 'medium' | 'high';
  }>;
}

export interface AttributionResult {
  window_days: number;
  n_obs: number;
  portfolio_return: number;
  benchmark_return: number;
  excess_return: number;
  brinson: {
    sectors: Array<{
      industry: string;
      portfolio_weight: number;
      benchmark_weight: number;
      active_weight?: number;
      portfolio_return: number;
      benchmark_return: number;
      allocation: number;
      selection: number;
      interaction: number;
      total: number;
    }>;
    summary: {
      portfolio_return: number;
      benchmark_return: number;
      excess_return: number;
      allocation: number;
      selection: number;
      interaction: number;
      residual_alpha: number;
    };
  };
  multi_period_brinson?: {
    sectors: Array<{
      industry: string;
      portfolio_weight: number;
      benchmark_weight: number;
      active_weight: number;
      allocation: number;
      selection: number;
      interaction: number;
      total: number;
    }>;
    summary: {
      portfolio_return: number;
      benchmark_return: number;
      excess_return: number;
      allocation: number;
      selection: number;
      interaction: number;
      residual_alpha: number;
    };
    timeseries: AttributionTimeseriesPoint[];
  };
  stock_contributions?: {
    top_contributors: StockContributionItem[];
    bottom_drags: StockContributionItem[];
    all_holdings: StockContributionItem[];
  };
  liquidity_bottleneck?: PortfolioBottleneckResult;
  style: {
    n_days: number;
    betas: Record<string, number>;
    contributions: Record<string, number>;
    alpha_annualized: number | null;
    alpha_t_stat?: number;
    alpha_p_value?: number;
    alpha_significance?: string;
    r_squared: number | null;
    factor_stats?: Record<string, {
      beta: number;
      std_error: number;
      t_stat: number;
      p_value: number;
      significance: string;
      annualized_contrib: number;
    }>;
    risk_decomposition?: {
      tracking_error_pct: number;
      factor_risk_pct: number;
      specific_risk_pct: number;
      annual_factor_vol_pct: number;
      annual_specific_vol_pct: number;
    } | null;
  };
  benchmark_desc: string;
  benchmark_policy: { type: string; weight_basis: string; symbol_count: number; as_of: string };
}

export interface PreflightCheckResult {
  passed: boolean;
  reasons: string[];
  warnings: string[];
  metrics: {
    order_amount: number;
    current_cash: number;
    total_equity: number;
    max_position_ratio: number;
  };
}

export interface RiskStatusResult {
  kill_switch: boolean;
  halt_buys: boolean;
  pending_orders: number;
  rules: {
    max_position_ratio: number;
    max_single_order_amount: number;
    min_cash_buffer_ratio: number;
  };
}

export const deskApi = {
  riskStatus: (options?: RequestOptions) =>
    get<RiskStatusResult>('/api/v1/desk/risk/status', undefined, TIMEOUT.probe, options),
  setHaltBuys: (active: boolean, reason = '', options?: RequestOptions) =>
    post<{ halt_buys: boolean }>('/api/v1/desk/risk/halt-buys', { active, reason }, TIMEOUT.probe, options),
  liquidateAll: (options?: RequestOptions) =>
    post<{
      ok: boolean;
      cancelled_orders: number;
      liquidated_positions: number;
      symbols: string[];
      fills: { orders_scanned: number; fills_created: number; deferred_children: number };
    }>('/api/v1/desk/risk/liquidate-all', {}, TIMEOUT.aggregate, options),
  preflight: (
    req: { symbol: string; side: 'buy' | 'sell'; order_amount: number },
    options?: RequestOptions,
  ) =>
    post<PreflightCheckResult>('/api/v1/desk/orders/preflight', req, TIMEOUT.probe, options),
  killSwitch: () => get<{ kill_switch: boolean; pending_orders: number }>(
    '/api/v1/desk/kill-switch'),
  setKillSwitch: (active: boolean, reason = '') =>
    post<{ kill_switch: boolean; cancelled_orders: number }>(
      '/api/v1/desk/kill-switch', { active, reason }),
  exclusionList: () => get<ExclusionItemT[]>('/api/v1/desk/exclusion'),
  addExclusion: (symbol: string, category: string, reason = '') =>
    post<{ ok: boolean; id: number }>('/api/v1/desk/exclusion',
      { symbol, category, reason }),
  toggleExclusion: (id: number, active: boolean) =>
    post<{ ok: boolean }>('/api/v1/desk/exclusion/toggle', { id, active }),
  screenCandidates: () =>
    get<Array<{ symbol: string; name: string | null; category: string;
                reason: string }>>('/api/v1/desk/exclusion/screen'),
  placeOrder: (req: { symbol: string; side: 'buy' | 'sell'; order_amount: number;
                      algo: 'market' | 'vwap' | 'twap' | 'pov';
                      split_days: number; participation_cap: number }) =>
    post<{ ok: boolean; order_id?: number; decision_price?: number;
            reason?: string }>('/api/v1/desk/orders', req),
  cancelOrder: (orderId: number, options?: RequestOptions) =>
    post<{ ok: boolean; order_id: number }>(`/api/v1/desk/orders/${orderId}/cancel`, {}, TIMEOUT.probe, options),
  cancelAllOrders: (options?: RequestOptions) =>
    post<{ ok: boolean; cancelled_count: number }>('/api/v1/desk/orders/cancel-all', {}, TIMEOUT.probe, options),
  // 撮合需扫描全部到期母单并生成子单，母单多时实测可超 15s；前端先断后端仍在跑
  // ⇒ 用户以为失败会重复撮合。按长任务约定放宽到 30s。
  runFills: () => post<{ orders_scanned: number; fills_created: number;
                         deferred_children: number }>(
    '/api/v1/desk/fills/run', {}, TIMEOUT.aggregate),
  /** 母单列表（信封：items + 真实 total + 窗口披露）。
   *  后端已补 `total`（独立 COUNT）/`returned`/`limit`/`truncated`，
   *  调用方据此展示真实总数与是否截断，不再把窗口条数当全量。 */
  orders: (limit = 200) =>
    get<{ items: PaperOrder[]; total: number; returned: number; limit: number;
          truncated: boolean }>(`/api/v1/desk/orders?limit=${limit}`, undefined, TIMEOUT.aggregate),
  // /desk/account 需按逐日净值序列重建（母单/成交多时可能超 15s），放宽到 30s。
  account: () => get<PaperAccount>('/api/v1/desk/account', undefined, TIMEOUT.aggregate),
  capacity: (participation_cap = 0.01, holdings = 20, rebalance_per_year = 12) =>
    get<{ aum_threshold: number | null; aum_yi: number | null; formula: string }>(
      `/api/v1/desk/capacity?participation_cap=${participation_cap}&holdings=${holdings}&rebalance_per_year=${rebalance_per_year}`),
  attribution: (
    assets: Array<{ code: string; weight: number }>,
    window_days = 120,
    benchmark: {
      benchmark_type: 'universe_equal' | 'custom_portfolio' | 'single_symbol';
      benchmark_assets?: Array<{ code: string; weight: number }>;
      benchmark_symbol?: string;
      target_aum?: number;
      participation_cap?: number;
      liquidation_days_target?: number;
    } = { benchmark_type: 'universe_equal' },
    options?: RequestOptions,
  ) =>
    post<AttributionResult>('/api/v1/desk/attribution',
      { assets, window_days, ...benchmark }, TIMEOUT.export, options),

  portfolioCapacity: (
    assets: Array<{ code: string; weight: number }>,
    params?: { target_aum?: number; participation_cap?: number; liquidation_days_target?: number },
    options?: RequestOptions,
  ) =>
    post<PortfolioBottleneckResult>(
      '/api/v1/desk/portfolio-capacity',
      {
        assets,
        target_aum: params?.target_aum ?? 5e7,
        participation_cap: params?.participation_cap ?? 0.10,
        liquidation_days_target: params?.liquidation_days_target ?? 3,
      },
      TIMEOUT.aggregate,
      options,
    ),

  /** TCA 交易成本与执行落差归因（Perold Implementation Shortfall） */
  tca: (days = 60, options?: RequestOptions) =>
    get<TcaResult>(`/api/v1/desk/tca?days=${days}`, undefined, TIMEOUT.aggregate, options),

  /** 平方根冲击容量衰减曲面与黄金 AUM 测算 */
  capacityCurve: (params?: { participation_cap?: number; holdings?: number; rebalance_per_year?: number; gross_return?: number; gross_vol?: number }, options?: RequestOptions) => {
    const qs = new URLSearchParams();
    if (params?.participation_cap != null) qs.set('participation_cap', String(params.participation_cap));
    if (params?.holdings != null) qs.set('holdings', String(params.holdings));
    if (params?.rebalance_per_year != null) qs.set('rebalance_per_year', String(params.rebalance_per_year));
    if (params?.gross_return != null) qs.set('gross_return', String(params.gross_return));
    if (params?.gross_vol != null) qs.set('gross_vol', String(params.gross_vol));
    return get<CapacityCurveResult>(`/api/v1/desk/capacity-curve?${qs.toString()}`, undefined, TIMEOUT.aggregate, options);
  },

  /** S2-1: 云端价格条件单 */
  getConditionalOrders: (status?: string, options?: RequestOptions) =>
    get<ConditionalOrderItem[]>(status ? `/api/v1/desk/conditional-orders?status=${status}` : '/api/v1/desk/conditional-orders', undefined, TIMEOUT.probe, options),
  createConditionalOrder: (req: { symbol: string; side: 'buy' | 'sell'; trigger_type: 'price_ge' | 'price_le'; trigger_price: number; order_amount: number; order_type?: string; algo_type?: string }, options?: RequestOptions) =>
    post<ConditionalOrderItem>('/api/v1/desk/conditional-orders', req, TIMEOUT.probe, options),
  cancelConditionalOrder: (orderId: number, options?: RequestOptions) =>
    post<{ order_id: number; status: string }>(`/api/v1/desk/conditional-orders/${orderId}/cancel`, {}, TIMEOUT.probe, options),

  /** S2-1: 网格交易机器人 */
  getGridBots: (status?: string, options?: RequestOptions) =>
    get<GridBotItem[]>(status ? `/api/v1/desk/grid-bots?status=${status}` : '/api/v1/desk/grid-bots', undefined, TIMEOUT.probe, options),
  createGridBot: (req: { symbol: string; lower_price: number; upper_price: number; grid_count: number; amount_per_grid: number }, options?: RequestOptions) =>
    post<GridBotItem>('/api/v1/desk/grid-bots', req, TIMEOUT.probe, options),
  stopGridBot: (botId: number, options?: RequestOptions) =>
    post<{ bot_id: number; status: string }>(`/api/v1/desk/grid-bots/${botId}/stop`, {}, TIMEOUT.probe, options),
  evaluateBots: (options?: RequestOptions) =>
    post<{ quotes_scanned: number; triggered_conditional_orders: any[]; triggered_grid_trades: any[] }>('/api/v1/desk/conditional-orders/evaluate', {}, TIMEOUT.probe, options),
};

export interface ConditionalOrderItem {
  id: number;
  user_id: string;
  symbol: string;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit';
  trigger_type: 'price_ge' | 'price_le';
  trigger_price: number;
  order_amount: number;
  algo_type: string;
  status: 'ACTIVE' | 'TRIGGERED' | 'CANCELLED' | 'FAILED';
  message: string | null;
  triggered_at: string | null;
  fill_order_id: number | null;
  created_at: string | null;
}

export interface GridBotItem {
  id: number;
  user_id: string;
  symbol: string;
  lower_price: number;
  upper_price: number;
  grid_count: number;
  amount_per_grid: number;
  status: 'RUNNING' | 'STOPPED' | 'PAUSED';
  last_trigger_price: number | null;
  total_trades: number;
  realized_pnl: number;
  created_at: string | null;
}

export interface TcaResult {
  has_data: boolean;
  total_orders: number;
  total_fills: number;
  message?: string;
  summary: {
    total_volume: number;
    total_fee: number;
    fee_bps: number;
    avg_impact_bps: number;
    delay_bps: number;
    avg_basis_bps: number;
    total_shortfall_bps: number;
    waterfall?: Array<{ name: string; value: number }>;
  };
  algo_breakdown: Array<{
    algo: string;
    fill_count: number;
    volume: number;
    avg_impact_bps: number;
    avg_basis_bps: number;
  }>;
  recent_executions: Array<{
    id: number;
    order_id: number;
    symbol: string;
    side: string;
    exec_date: string;
    price: number;
    qty: number;
    amount: number;
    fee: number;
    impact_bps: number;
    basis_bps: number;
  }>;
}

export interface CapacityCurveResult {
  has_data: boolean;
  adv_median_yi: number;
  holdings: number;
  rebalance_per_year: number;
  gross_return_pct: number;
  gross_vol_pct: number;
  optimal_aum_yi: number;
  optimal_aum_wan: number;
  optimal_sharpe: number;
  max_profit_wan: number;
  breakeven_aum_yi: number | null;
  curve: Array<{
    aum_yi: number;
    aum_wan: number;
    participation_pct: number;
    impact_bps: number;
    total_cost_pct: number;
    net_return_pct: number;
    net_sharpe: number;
    alpha_profit_wan: number;
  }>;
  horizon_frontiers?: Array<{
    horizon_days: number;
    desc: string;
    capacity_yi: number;
  }>;
}

