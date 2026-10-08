/** 数据中心 API 分组（运维监控 + 数据同步 + 自定义抓取）。 */

import { TIMEOUT } from './timeouts';
import { get, post, type RequestOptions } from './client';
import type {
  AssetType, AutoSyncConfig, DataOverview, DatasetItem, FetchRequest,
  InstrumentItem, LastJob, LogItem, QualityResult, SyncMode, SyncStatus,
  TaskStatPoint, DatasetPreviewResult, MicroQcRadarResult,
  SurvivorshipBiasAuditResult, PitLagDistributionResult, QuarantineVaultResult,
} from '@/types/datacenter';

/**
 * 任务与其 `data_jobs` 结论行的关联状态（B1+，后端 `_linked_job_for_task`）。
 *
 * * `linked` —— 结论行的 `task_id` **精确等于**本任务 ⇒ 两者是同一次执行的两个视角；
 * * `other`  —— 有结论行，但它属于**别的**任务（本任务被更新的执行覆盖，或本任务
 *               未落 `data_jobs`）⇒ 结论行**不能**当成本任务的结论；
 * * `none`   —— `data_jobs` 里没有可比对的结论行。
 *
 * 三态必须显式表达：若匹配不上就直接省略结论字段，前端只能显示残缺面板、
 * 既没解释也没线索；有状态才能**如实**说明"这条结论属于另一次执行"。
 */
export type SyncLinkState = 'linked' | 'other' | 'none';

/**
 * 持久化同步任务详情（GET /datacenter/sync/tasks/{id}，task_store 落库）。
 *
 * 与内存态 /sync/status 的区别：进程重启/页面刷新后仍可查，含最终 status/error。
 *
 * B1+ 起后端在原字段之上**追加** `link_state` 与 `job`（向后兼容，不改既有字段）：
 * 于是本端点同时给出"**过程**"（task 自身 progress/status/log）与"**结论**"
 * （`data_jobs` 的 verdict/advice/scope_note），前端不必再并排两个摘要。
 */
export interface SyncTaskDetail {
  task_id: string;
  task_type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  progress: { done?: number; total?: number };
  result: Record<string, unknown> | null;
  error_message: string | null;
  /** 时间戳（UTC ISO，task_store 记录）；用于确认任务实际起止 */
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  /** 与 data_jobs 结论行的关联状态；旧版后端不返回时为 undefined（按未知处理） */
  link_state?: SyncLinkState;
  /**
   * 同一 (job_type, trade_date) 的结论行，形态与 `/overview` 的 `last_job` 一致。
   * 仅当 `link_state === 'linked'` 时它才**确定**是本任务的结论；
   * `'other'` 时它是"该模式的另一次执行"，展示时必须如此标注。
   */
  job?: LastJob | null;
}

export const datacenterApi = {
  /** refresh=true 失效后端统计缓存强制重扫（§3.2 刷新按钮） */
  // 全量重扫分区（~22s）+ 目录体积统计（~6.5s），冷算约 29s，超 15s 默认超时，
  // 故按聚合接口约定放宽（与 /settings 同因）。2026-10-01 复测冷启动 >30s，
  // 60s 预算保持不变。
  overview: (refresh = false, options?: RequestOptions) =>
    get<DataOverview>('/api/v1/datacenter/overview',
      { ...(refresh ? { refresh: 1 } : {}) }, TIMEOUT.medium, options),

  // 冷算要遍历 3.3 万个 parquet 的元数据取行数与日期区间（footer 统计优化 + 单飞
  // 之后实测 ~24s；优化前 74s）。旧代码用默认 15s ⇒ /data 页「数据集」面板**必然**
  // 超时，横幅长期显示「部分数据面板加载失败：请求超时」。后端已把该统计的 TTL
  // 拉到 1800s（且同步写库后立即失效），故冷扫只在启动/同步后首次发生，这里放宽到
  // 90s 以覆盖最坏一次冷扫（与 overview 60s、mirrorStatus 120s 同因）。
  datasets: (options?: RequestOptions) =>
    get<{ items: DatasetItem[] }>('/api/v1/datacenter/datasets', undefined, TIMEOUT.extended, options),

  // 数据质量块按交易日历逐只标的算缺失交易日（实测 ~32s；优化前 86s），
  // 同样用默认 15s 会必然超时。TTL 同为 1800s，放宽到 90s。
  quality: (limit = 50, options?: RequestOptions) =>
    get<QualityResult>('/api/v1/datacenter/quality', { limit }, TIMEOUT.extended, options),

  logs: (limit = 60) => get<{ items: LogItem[] }>('/api/v1/datacenter/logs', { limit }),

  taskStats: () =>
    get<{ points: TaskStatPoint[] }>('/api/v1/datacenter/task-stats'),

  /** 触发后台同步任务（incremental 增量 / repair 修复 / rebuild 全量重构）。 */
  sync: (mode: SyncMode, symbols?: string[], resume = false) =>
    post<{ started: boolean; task_id: string; mode: SyncMode; total: number; resume: boolean; skipped: number }>(
      '/api/v1/datacenter/sync',
      symbols?.length ? { mode, symbols, resume } : { mode, resume },
    ),

  /** 请求停止正在运行的同步任务（优雅停止，已完成的 symbol 可断点续传）。 */
  cancel: () => post<{ cancelled: boolean; reason: string }>('/api/v1/datacenter/sync/cancel', {}),

  /** options 透传 signal：轮询（1.5s 级）需要在卸载/重排时中断在途请求，
   *  否则退避逻辑与 cleanup 无法真正取消已发出的那一枪。 */
  status: (options?: RequestOptions) =>
    get<SyncStatus>('/api/v1/datacenter/sync/status', undefined, undefined, options),

  task: (taskId: string) =>
    get<SyncTaskDetail>(`/api/v1/datacenter/sync/tasks/${taskId}`),

  /** 自定义抓取：按标的类型 + 起止日期抓取行情并落库。 */
  fetch: (req: FetchRequest) =>
    post<{ started: boolean; asset_type: AssetType; total: number; start: string; end: string }>(
      '/api/v1/datacenter/sync/fetch', req,
    ),

  /** 标的列表预览（按类型取前 N 只，供抓取面板选择）。
   *  `total` = 后端独立 COUNT 的真实总量（不受 limit 影响）；
   *  `returned/truncated/limit` 披露本次窗口，消费方不得把窗口条数当全量。 */
  instruments: (assetType: AssetType | 'all' = 'stock', limit = 50) =>
    get<{ items: InstrumentItem[]; total: number;
          returned?: number; truncated?: boolean; limit?: number }>(
      '/api/v1/datacenter/instruments', { asset_type: assetType, limit }),

  /** 读取 autoSync 配置 + 今日是否已触发。 */
  autoStatus: () => get<AutoSyncConfig>('/api/v1/datacenter/sync/auto'),

  /** 开关/改时间 autoSync（后端持久化）。 */
  autoToggle: (enabled: boolean, time = '15:45') =>
    post<AutoSyncConfig>('/api/v1/datacenter/sync/auto', { enabled, time }),

  // ---- 文本数据（FinLLM 前置）+ 截面镜像（MED-003 前置） ----
  textStatus: () => get<TextDataStatus>('/api/v1/datacenter/text/status'),
  textImport: (docs: object[], source = 'manual') =>
    post<TextImportResult>('/api/v1/datacenter/text/import', { docs, source }),
  // 文本因子构建为同步重计算：样本量大时实测可超 15s（默认超时），
  // 前端先断、后端仍在跑 ⇒ 用户以为失败并重复点击。按长任务约定放宽到 60s。
  textBuildFactor: (options?: RequestOptions) =>
    post<TextFactorResult>('/api/v1/datacenter/text/build-factor', undefined, TIMEOUT.medium, options),
  // 冷启动首次扫描需逐文件读取约 3.2 万个源 parquet 的 date 列。旧注释"实测 21~23s"
  // 是**孤立空载**测量；2026-09-29 复核：真实冷扫 50~74s，页面加载并发风暴下约 62s
  // （datasets/quality/mirror_status 同时抢磁盘 IO）。45s 预算必然超时 ⇒ ECONNABORTED
  // ⇒ TextDataPanel 红条「请求超时，请稍后重试」。放宽到 120s 以覆盖最坏冷扫；
  // 仍 < 服务端 240s 全局兜底（core/timeout_guard.py 默认 REQUEST_TIMEOUT_SECONDS），
  // 满足"前端预算 < 服务端预算"不变量（否则会被服务端 504 误杀）。
  mirrorStatus: (options?: RequestOptions) =>
    get<MirrorStatus>('/api/v1/datacenter/mirror/status', undefined, TIMEOUT.heavy, options),
  mirrorRebuild: (options?: RequestOptions) =>
    // 同步重建截面镜像：逐数据集扫描源 parquet + 落盘，实测 ~51s/数据集 × 3 ≈ 150s
    // （日志 cs-mirror daily_bar 18:17:43 → daily_bar_hfq 18:18:34）。60s 必然超时，
    // 故放宽到 180s；仍 < 服务端 240s 全局兜底，满足预算不变量。
    post<Array<{ dataset: string; built: number; dates: number; rows: number }>>(
      '/api/v1/datacenter/mirror/rebuild', {}, TIMEOUT.export, options),
  // ---- 模型训练（数据前置消费端：一键训练） ----
  trainReadiness: () => get<TrainReadiness>('/api/v1/datacenter/train/readiness'),
  trainStart: (model: 'tft' | 'gnn', params: Record<string, number> = {}) =>
    post<{ started: boolean; model: string; params: Record<string, number> }>(
      '/api/v1/datacenter/train/start', { model, params }),
  trainStatus: () => get<TrainStatus>('/api/v1/datacenter/train/status'),
  trainCancel: () =>
    post<{ cancel_requested: boolean; running: boolean }>(
      '/api/v1/datacenter/train/cancel', {}),

  // ---- 点截面 (PIT) 财务快照与复权因子链 ----
  pitFinancials: (symbol = '000001.SZ', limit = 20, options?: RequestOptions) =>
    get<PitFinancialsResult>('/api/v1/datacenter/pit-financials', { symbol, limit }, TIMEOUT.default, options),

  corporateActions: (symbol = '000001.SZ', limit = 20, options?: RequestOptions) =>
    get<CorporateActionsResult>('/api/v1/datacenter/corporate-actions', { symbol, limit }, TIMEOUT.default, options),

  tradabilityTimeline: (symbol = '000001.SZ', limit = 250, options?: RequestOptions) =>
    get<TradabilityTimelineResult>('/api/v1/datacenter/tradability-timeline', { symbol, limit }, TIMEOUT.default, options),

  // ---- P0/P1/P2 进化端点：Schema预览、微观质检、幸存者审计、时滞分布、隔离区 ----
  datasetPreview: (dataset = 'daily_bar', limit = 10, options?: RequestOptions) =>
    get<DatasetPreviewResult>('/api/v1/datacenter/dataset-preview', { dataset, limit }, TIMEOUT.default, options),

  microQcRadar: (limit = 20, options?: RequestOptions) =>
    get<MicroQcRadarResult>('/api/v1/datacenter/micro-qc-radar', { limit }, TIMEOUT.default, options),

  survivorshipBias: (options?: RequestOptions) =>
    get<SurvivorshipBiasAuditResult>('/api/v1/datacenter/survivorship-bias-audit', undefined, TIMEOUT.default, options),

  pitLagDistribution: (options?: RequestOptions) =>
    get<PitLagDistributionResult>('/api/v1/datacenter/pit-lag-distribution', undefined, TIMEOUT.default, options),

  quarantineVault: (options?: RequestOptions) =>
    get<QuarantineVaultResult>('/api/v1/datacenter/quarantine-vault', undefined, TIMEOUT.default, options),
};

// ---------------- 前置能力类型 ----------------
export interface TextDataStatus {
  docs: number;
  doc_dates: number;
  doc_symbols: number;
  doc_last: string | null;
  factor_rows: number;
  factor_files: number;
  surprise_active_rows?: number;
  llm_enabled: boolean;
  basis: string;
  note: string;
}

export interface TextImportResult {
  imported: number;
  duplicates: number;
  invalid: number;
  total?: number;
}

export interface TextFactorResult {
  ok: boolean;
  method: string;
  rows: number;
  dates: number;
  symbols: number;
}

export interface MirrorItem {
  source_dates: number;
  mirror_dates: number;
  source_last: string | null;
  mirror_last: string | null;
  lag_days: number;
}

export interface MirrorStatus extends Record<string, unknown> {
  root: string;
}

// ---------------- 模型训练（train_service） ----------------
export interface TrainFeaturesInfo {
  exists: boolean;
  rows: number;
  symbols: number;
  dates: number;
  last_date: string | null;
  dir: string;
}

export interface TorchDeviceInfo {
  torch_installed: boolean;
  /** 训练设备：'xpu'（Intel Arc 核显/独显）| 'cpu'；torch 未安装时为 null */
  device: string | null;
  device_name: string | null;
  torch_version: string | null;
}

export interface TrainReadiness {
  torch_ready: boolean;
  torch_device: TorchDeviceInfo;
  torch_install_hint: string;
  features: TrainFeaturesInfo;
  est_samples: number;
  min_samples: number;
  sample_ok: boolean;
  gnn_edges: number;
  tft_ready: boolean;
  gnn_ready: boolean;
  relation_note: string;
  note: string;
}

export interface TrainLogLine {
  ts: string;
  message: string;
}

export interface TrainResult {
  ok: boolean;
  model?: string;
  version?: string;
  samples?: number;
  /** 训练设备（'xpu' | 'cpu'），取 history 首条披露 */
  device?: string | null;
  valid_rank_ic?: number;
  valid_days?: number;
  test_rank_ic?: number;
  test_days?: number;
  model_dir?: string;
  reason?: string;
  error?: string;
  cancelled?: boolean;
}

export interface TrainStatus {
  running: boolean;
  model: string | null;
  params: Record<string, unknown>;
  error: string | null;
  started_at: number | null;
  elapsed_ms: number;
  logs: TrainLogLine[];
  cancelled: boolean;
  finished_at_wall?: number;
  last_model?: string;
  last_result: TrainResult | null;
}

export interface PitFinancialRecord {
  id: number;
  symbol: string;
  period: string;
  announce_date: string;
  lag_days: number;
  is_delayed: boolean;
  revenue: number | null;
  net_profit: number | null;
  roe: number | null;
  eps: number | null;
  bps: number | null;
  announce_basis: string;
  source: string;
}

export interface PitFinancialsResult {
  symbol: string;
  records: PitFinancialRecord[];
  total: number;
  summary: {
    avg_lag_days: number;
    pit_verified: boolean;
    latest_period: string | null;
    latest_announce_date: string | null;
    note: string;
  };
}

export interface CorporateActionRecord {
  date: string;
  close: number;
  cum_factor: number;
  jump_ratio: number;
  action_type: string;
  implied_div_per_share: number;
  is_aligned: boolean;
}

export interface CorporateActionsResult {
  symbol: string;
  actions: CorporateActionRecord[];
  total_actions: number;
  latest_cum_factor: number;
  is_verified: boolean;
  note: string;
}

export interface TradabilityTimelinePoint {
  date: string;
  status: 'NORMAL' | 'SUSPENDED' | 'ST' | 'LIMIT_UP' | 'LIMIT_DOWN';
  status_label: string;
  close: number;
  limit_up: number;
  limit_down: number;
  is_halted: boolean;
  is_st: boolean;
}

export interface TradabilityTimelineResult {
  symbol: string;
  name: string;
  total_days: number;
  normal_days: number;
  suspended_days: number;
  st_days: number;
  limit_up_days: number;
  limit_down_days: number;
  tradable_ratio: number;
  timeline: TradabilityTimelinePoint[];
  note?: string;
}
