/** 系统设置 API 分组（偏好持久化 / 连接心跳 / 数据运维）。 */
import { get, post, put } from './client';

export interface Preferences {
  theme: 'light' | 'dark' | 'auto';
  language: string;
  market: string;
  notify_backtest: boolean;
  refresh_freq: number;
  nickname: string;
  email: string;
}

export interface EngineConfig {
  name: string;
  risk_indicators: string[];
  commission_pct: number;
  slippage_pct: number;
}

export interface ConnectorTest {
  status: 'success' | 'error';
  latency_ms: number | null;
  ts?: string;
  message?: string;
}

export interface SettingsData {
  preferences: Preferences;
  engine: EngineConfig;
  last_tests: Record<string, ConnectorTest>;
}

export interface SettingsBundle {
  settings: SettingsData;
  storage_gb: number | null;
  last_sync: string | null;
  /** 系统健康状态（L1-1 缓存降级披露 + §6.1 磁盘水位），后端缺失时为 undefined */
  system?: SystemStatus;
}

/** Redis 缓存状态：degraded=true 表示 Redis 不可用，已降级进程内 LRU（重启即失效） */
export interface CacheStatus {
  enabled: boolean;
  ok: boolean;
  degraded: boolean;
  breaker: { fail_count: number; open: boolean; open_until: number };
}

/** 磁盘水位：>90% warning / >95% critical（data_health 预警规则的数据源） */
export interface DiskStatus {
  usage_percent: number | null;
  level: 'normal' | 'warning' | 'critical' | null;
}

export interface SystemStatus {
  cache: CacheStatus;
  disk: DiskStatus;
}

export const settingsApi = {
  // 聚合端点：内部会调用 datacenter.overview()（缓存过期时全量重扫分区，
  // 实测冷算 27~30s），远超 15s 默认超时 → /data 页会显示"请求超时"。
  // 按本项目对聚合接口的既有约定放宽单次超时。
  all: () => get<SettingsBundle>('/api/v1/settings', undefined, 60_000),

  savePreferences: (p: Partial<Preferences>) =>
    put<Preferences>('/api/v1/settings/preferences', p),

  saveEngine: (e: Partial<EngineConfig>) =>
    put<EngineConfig>('/api/v1/settings/engine', e),

  testConnector: (connector: 'akshare' | 'eastmoney') =>
    post<{ connector: string; status: 'success' | 'error'; latency_ms: number | null; message?: string }>(
      '/api/v1/settings/connectors/test', { connector }, 30_000),

  clearCache: () =>
    // deleted = 真实删除的缓存键数；protected = 被保留的持久状态键
    // （如 ETF 快照存档 aqp:etf:snap:history，删掉不可再生，后端已排除）。
    post<{ redis_cleared: boolean; deleted: number; protected: string[]; freed_mb: number; message: string }>(
      '/api/v1/settings/data/cache/clear', {}, 30_000),

  backupDb: () =>
    post<{ file: string; size_mb: number }>('/api/v1/settings/db/backup', {}, 60_000),

  /** 增量同步日线：与数据中心共用后台任务 */
  syncDaily: () =>
    post<{ started: boolean; mode: string; total: number }>(
      '/api/v1/settings/data/sync', {}, 15_000),
};
