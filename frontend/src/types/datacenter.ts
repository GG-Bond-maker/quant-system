/** 数据中心类型定义（与后端 /api/v1/datacenter 对齐）。 */

export type SyncMode = 'incremental' | 'repair' | 'rebuild';

/** 最近一次流水线/同步任务的结论（后端 /overview 的 last_job）。
 *
 *  与 akshare_health / akshare_message（**数据源/接口状态**）刻意分离：一次"小规模
 *  子集重跑未通过校验"不构成数据源异常的证据（校验失败恰恰说明接口是通的）。两者
 *  混在同一字段时会被读成"市场级同步故障"——2026-09-30 实测：一次单标的重跑被展示
 *  为「最近同步异常」并挂到 10-04。 */
export interface LastJob {
  id: number;
  job_type: string;
  status: string;
  /**
   * 该 (job_type, trade_date) **最后一次执行**的 `task_store` id（B1+，2026-10-04）。
   *
   * 用途：把本"结论块"与 `/sync/tasks/{id}` 的"进度块"按**后端给出的关联**归组，
   * 而不是用 `job_type` + `trade_date` 在前端软匹配猜测。
   *
   * `null` = 该行建于本列引入之前，或该次执行没有 `task_store` 记录
   * （如夜间 `daily_pipeline`）—— **不得**用别的字段冒充，也不要据此隐藏面板。
   */
  task_id: string | null;
  /** 后端派生的结论分类（前端不做二次判断，避免两处判据分叉） */
  verdict: 'success' | 'running' | 'pending' | 'failed' | 'failed_subset';
  trade_date: string | null;
  current_step: string | null;
  finished_at: string | null;
  duration_ms: number;
  /** 错误原文（后端截断到 300 字符，不改写内容） */
  error_message: string;
  /** 本次代码集规模；null = 未知（历史行），**不得**当成 0 */
  codes_count: number | null;
  /** market=市场级（>=500 只）/ subset=小规模子集 / null=未知 */
  job_scope: 'market' | 'subset' | null;
  /** 规模来源：recorded=任务记录值 / message=由错误文本推断（下界）/ unknown=未知 */
  scope_source: 'recorded' | 'message' | 'unknown';
  /** 规模说明（含推断来源，直接可展示） */
  scope_note: string;
  age_days: number | null;
  /** 该告警是否已陈旧（久未复核 / 失败日已被数据覆盖） */
  stale: boolean;
  stale_note: string;
  /** 由真实错误文本派生的处置建议；未归类时后端明说"原因未归类" */
  advice: string;
}

export interface DataOverview {
  storage_bytes: number;
  storage_gb: number;
  /** instrument 表收录目标数；instrument 表读取失败时为 null（如实显示不可用） */
  covered_total: number | null;
  covered_with_data: number;
  dataset_count: number;
  last_sync: string | null;
  last_sync_source: string;
  /** **数据源/接口**状态（不含任务结论；任务结论见 last_job） */
  akshare_health: 'green' | 'yellow' | 'red';
  akshare_message: string;
  /** 最近一次任务结论；无任何任务记录时为 null */
  last_job?: LastJob | null;
  /** DATA_ROOT 所在盘使用率；获取失败时为 null（不造 0% 假值） */
  disk_usage_percent: number | null;
  daily_bar_range: [string | null, string | null];
  /** 多资产纳管统计（A股现货、场内ETF、港股通、内盘期货主力） */
  multi_asset_stats?: MultiAssetDataSummary;
  /** 后端 from_cache：true=缓存命中 / 'refreshed'=刚强制重算 / false=实时构建（其余请求无此字段） */
  from_cache?: boolean | string;
  /** 后端 stale：true=缓存已软过期，旧值服务中 + 后台重建中（cached_or_build 标注）。
   *  注：旧字段 `refreshing` 后端全文无产出（恒 undefined），已删除并改消费此真实字段。 */
  stale?: boolean;
}

export interface MultiAssetDataSummary {
  stocks_count: number;
  etfs_count: number;
  futures_count: number;
  hk_count: number;
  futures_categories: string[];
  supported_assets: string[];
}

export interface DatasetItem {
  dataset: string;
  label: string;
  table: string;
  source: string;
  start: string | null;
  end: string | null;
  rows: number;
  symbols: number;
  bytes: number;
  adjust_status: string;
}

export interface QualityItem {
  dataset: string;
  symbol: string;
  missing_days: number;
  first_missing: string;
  last_missing: string;
  sample: string[];
}

export interface QualityResult {
  checked: number;
  total_missing_days: number;
  affected_symbols: number;
  items: QualityItem[];
}

export interface LogItem {
  ts: string;
  level: string;
  message: string;
}

/** 数据任务统计（P2-11：源为 data_update_log/data_jobs 流水线任务，非 API 请求） */
export interface TaskStatPoint {
  date: string;
  /** 当日任务运行次数 */
  calls: number;
  /** 平均任务耗时 (ms) */
  avg_latency_ms: number;
}

export interface SyncStatus {
  task_id?: string | null;
  running: boolean;
  mode: SyncMode | null;
  total: number;
  done: number;
  percent: number;
  current: string;
  error: string | null;
  elapsed_ms: number;
  logs: LogItem[];
  /** 已完成 symbol 数（断点续传用） */
  completed_count?: number;
  /** 已完成的标的代码列表（供实时联动展示与勾选去重） */
  completed_symbols?: string[];
  /** 失败 symbol 数。**同时**含「全口径失败」与「部分口径失败」（如 raw 成功、
   *  hfq 失败）——两者都需 resume 重试，故后端一律计入 failed（保守口径）。
   *  口径为**本轮（最近一次任务）**：后端在每轮任务开始时（resume=False）
   *  清空 _sync.failed，故只反映最近一次任务，不含历史轮。 */
  failed_count?: number;
  /** 失败的标的代码列表 */
  failed_symbols?: string[];
  /** 用户是否请求了停止 */
  cancelled?: boolean;
}

/** 自定义抓取的标的类型 */
export type AssetType = 'stock' | 'etf' | 'hk' | 'futures' | 'all';

export interface FetchRequest {
  asset_type: AssetType;
  start: string;
  end: string;
  symbols?: string[];
  limit?: number;
}

export interface InstrumentItem {
  symbol: string;
  name: string;
  type: string;
}

export interface AutoSyncConfig {
  enabled: boolean;
  time: string;
  today_done: boolean;
}

export interface DatasetSchemaField {
  name: string;
  dtype: string;
  label: string;
  unit: string;
  null_ratio: number;
}

export interface DatasetPreviewResult {
  dataset: string;
  table: string;
  label: string;
  total_rows: number;
  schema: DatasetSchemaField[];
  records: Record<string, unknown>[];
  error?: string;
}

export interface MicroQcAnomaly {
  symbol: string;
  date: string;
  kind: string;
  value: number;
  threshold: string;
  severity: 'warn' | 'error';
  message: string;
}

export interface MicroQcRadarResult {
  total_audited_records: number;
  anomaly_count: number;
  jump_count: number;
  ohlc_breach_count: number;
  zero_vol_count: number;
  health_score: number;
  status: 'HEALTHY' | 'WARNING' | 'CRITICAL';
  anomalies: MicroQcAnomaly[];
}

export interface DelistedStockItem {
  symbol: string;
  name: string;
  ipo_date: string;
  delist_date: string;
  reason_category: string;
  last_price: number;
  coverage_status: string;
}

export interface SurvivorshipBiasAuditResult {
  active_stocks_count: number;
  delisted_stocks_count: number;
  survivorship_protection_score: number;
  protection_level: string;
  delisted_stocks: DelistedStockItem[];
  bias_impact_assessment: {
    annualized_return_overestimate_pct: number;
    max_drawdown_underestimate_pct: number;
    universe_rule: string;
    summary: string;
  };
}

export interface PitLagQuarter {
  count: number;
  median: number;
  avg: number;
}

export interface PitLagHistogramBin {
  range: string;
  count: number;
  pct: number;
}

export interface PitLagDistributionResult {
  total_reports: number;
  avg_lag_days: number;
  median_lag_days: number;
  p25_lag_days: number;
  p75_lag_days: number;
  delayed_count: number;
  delayed_ratio_pct: number;
  histogram: PitLagHistogramBin[];
  by_quarter: Record<string, PitLagQuarter>;
  note: string;
}

export interface QuarantineVaultResult {
  total_quarantined: number;
  vault_status: string;
  quarantined_items: Array<{
    id: string;
    dataset: string;
    symbol: string;
    date: string;
    reason: string;
    severity: string;
    quarantined_at: string;
    suggested_action: string;
  }>;
  quarantine_policy: {
    auto_isolation_enabled: boolean;
    rules: string[];
    note: string;
  };
}
