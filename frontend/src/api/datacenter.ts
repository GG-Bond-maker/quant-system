/** 数据中心 API 分组（运维监控 + 数据同步 + 自定义抓取）。 */
import { get, post } from './client';
import type {
  AssetType, AutoSyncConfig, DataOverview, DatasetItem, FetchRequest,
  InstrumentItem, LogItem, QualityResult, SyncMode, SyncStatus, TaskStatPoint,
} from '@/types/datacenter';

export const datacenterApi = {
  /** refresh=true 失效后端统计缓存强制重扫（§3.2 刷新按钮） */
  // 全量重扫分区（~22s）+ 目录体积统计（~6.5s），冷算约 29s，超 15s 默认超时，
  // 故按聚合接口约定放宽（与 /settings 同因）。
  overview: (refresh = false) =>
    get<DataOverview>('/api/v1/datacenter/overview',
      { ...(refresh ? { refresh: 1 } : {}) }, 60_000),

  datasets: () => get<{ items: DatasetItem[] }>('/api/v1/datacenter/datasets'),

  quality: (limit = 50) =>
    get<QualityResult>('/api/v1/datacenter/quality', { limit }),

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

  status: () => get<SyncStatus>('/api/v1/datacenter/sync/status'),

  task: (taskId: string) =>
    get<{
      task_id: string; task_type: string;
      status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
      progress: { done?: number; total?: number };
      result: Record<string, unknown> | null; error_message: string | null;
    }>(`/api/v1/datacenter/sync/tasks/${taskId}`),

  /** 自定义抓取：按标的类型 + 起止日期抓取行情并落库。 */
  fetch: (req: FetchRequest) =>
    post<{ started: boolean; asset_type: AssetType; total: number; start: string; end: string }>(
      '/api/v1/datacenter/sync/fetch', req,
    ),

  /** 标的列表预览（按类型取前 N 只，供抓取面板选择）。 */
  instruments: (assetType: AssetType | 'all' = 'stock', limit = 50) =>
    get<{ items: InstrumentItem[]; total: number }>(
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
  textBuildFactor: () =>
    post<TextFactorResult>('/api/v1/datacenter/text/build-factor'),
  // 冷启动首次扫描需逐文件读取约 3.1 万个源 parquet 的 date 列（实测 21~23s），
  // 超默认 15s 会导致 /data 页数据面板必然超时；后端已加 120s 进程级缓存，
  // 这里放宽单次超时以覆盖首次冷扫。
  mirrorStatus: () =>
    get<MirrorStatus>('/api/v1/datacenter/mirror/status', undefined, 45_000),
  mirrorRebuild: () =>
    post<Array<{ dataset: string; built: number; dates: number; rows: number }>>(
      '/api/v1/datacenter/mirror/rebuild', {}),
  // ---- 模型训练（数据前置消费端：一键训练） ----
  trainReadiness: () => get<TrainReadiness>('/api/v1/datacenter/train/readiness'),
  trainStart: (model: 'tft' | 'gnn', params: Record<string, number> = {}) =>
    post<{ started: boolean; model: string; params: Record<string, number> }>(
      '/api/v1/datacenter/train/start', { model, params }),
  trainStatus: () => get<TrainStatus>('/api/v1/datacenter/train/status'),
  trainCancel: () =>
    post<{ cancel_requested: boolean; running: boolean }>(
      '/api/v1/datacenter/train/cancel', {}),
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
