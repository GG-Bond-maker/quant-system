/** 数据中心类型定义（与后端 /api/v1/datacenter 对齐）。 */

export type SyncMode = 'incremental' | 'repair' | 'rebuild';

export interface DataOverview {
  storage_bytes: number;
  storage_gb: number;
  /** instrument 表收录目标数；instrument 表读取失败时为 null（如实显示不可用） */
  covered_total: number | null;
  covered_with_data: number;
  dataset_count: number;
  last_sync: string | null;
  last_sync_source: string;
  akshare_health: 'green' | 'yellow' | 'red';
  akshare_message: string;
  /** DATA_ROOT 所在盘使用率；获取失败时为 null（不造 0% 假值） */
  disk_usage_percent: number | null;
  daily_bar_range: [string | null, string | null];
  /** 后端 from_cache：true=缓存命中 / 'refreshed'=刚强制重算 / false=实时构建（其余请求无此字段） */
  from_cache?: boolean | string;
  /** 后端 stale：true=缓存已软过期，旧值服务中 + 后台重建中（cached_or_build 标注）。
   *  注：旧字段 `refreshing` 后端全文无产出（恒 undefined），已删除并改消费此真实字段。 */
  stale?: boolean;
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
  /** 失败 symbol 数。**同时**含「全口径失败」与「部分口径失败」（如 raw 成功、
   *  hfq 失败）——两者都需 resume 重试，故后端一律计入 failed（保守口径）。
   *  口径为**本轮（最近一次任务）**：后端在每轮任务开始时（resume=False）
   *  清空 _sync.failed，故只反映最近一次任务，不含历史轮。 */
  failed_count?: number;
  /** 用户是否请求了停止 */
  cancelled?: boolean;
}

/** 自定义抓取的标的类型 */
export type AssetType = 'stock' | 'etf' | 'all';

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
