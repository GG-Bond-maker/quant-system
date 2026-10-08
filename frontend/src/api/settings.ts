/** 系统设置 API 分组（偏好持久化 / 摩擦力与引擎 / 数据网关 / Webhook / API Key / 运维探针）。 */

import { TIMEOUT } from './timeouts';
import { get, post, put, del } from './client';

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
  commission_sell_pct: number;
  stamp_duty_pct: number;
  transfer_fee_pct: number;
  min_commission: number;
  slippage_model: 'fixed' | 'almgren_chriss' | 'volatility';
  slippage_pct: number;
  default_benchmark: string;
  risk_free_rate: number;
  t_plus_one: boolean;
  limit_up_down_guard: boolean;
}

export interface WebhookConfig {
  enabled: boolean;
  channel_type: 'feishu' | 'dingtalk' | 'wecom' | 'custom';
  webhook_url: string;
  secret: string;
  events: string[];
}

export interface DataSourceConfig {
  primary_source: string;
  fallback_source: string;
  request_timeout_sec: number;
  rate_limit_qps: number;
}

export interface ApiKeyItem {
  key_id: string;
  name: string;
  prefix: string;
  permission: 'read_only' | 'full_access';
  created_at: string;
  expires_at: string | null;
  created_by?: string;
}

export interface ApiKeyCreated {
  key_id: string;
  name: string;
  raw_token: string;
  prefix: string;
  permission: string;
  created_at: string;
  expires_at: string | null;
}

export interface AuditLogItem {
  id: string;
  action: string;
  operator: string;
  timestamp: string;
  details?: string;
}

export interface TelemetryData {
  python_version: string;
  platform_system: string;
  polars_version: string;
  numpy_version: string;
  cpu_percent: number;
  memory_used_gb: number;
  memory_total_gb: number;
  server_time: string;
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
  webhook?: WebhookConfig;
  datasources?: DataSourceConfig;
  api_keys?: ApiKeyItem[];
  last_tests: Record<string, ConnectorTest>;
  audit_logs?: AuditLogItem[];
}

export interface CacheStatus {
  enabled: boolean;
  ok: boolean;
  degraded: boolean;
  breaker: { fail_count: number; open: boolean; open_until: number };
}

export interface DiskStatus {
  usage_percent: number | null;
  level: 'normal' | 'warning' | 'critical' | null;
}

export interface SystemStatus {
  cache: CacheStatus;
  disk: DiskStatus;
}

export interface SettingsBundle {
  settings: SettingsData;
  storage_gb: number | null;
  last_sync: string | null;
  system?: SystemStatus;
  telemetry?: TelemetryData;
  audit_logs?: AuditLogItem[];
}

export const settingsApi = {
  all: () => get<SettingsBundle>('/api/v1/settings', undefined, TIMEOUT.medium),

  savePreferences: (p: Partial<Preferences>) =>
    put<Preferences>('/api/v1/settings/preferences', p),

  saveEngine: (e: Partial<EngineConfig>) =>
    put<EngineConfig>('/api/v1/settings/engine', e),

  saveWebhook: (w: Partial<WebhookConfig>) =>
    put<WebhookConfig>('/api/v1/settings/webhook', w),

  testWebhook: (payload: { webhook_url?: string; channel_type?: string; secret?: string }) =>
    post<{ sent: boolean; status_code?: number; latency_ms?: number; message?: string }>(
      '/api/v1/settings/webhook/test', payload, TIMEOUT.medium),

  saveDataSources: (d: Partial<DataSourceConfig>) =>
    put<DataSourceConfig>('/api/v1/settings/datasources', d),

  createApiKey: (payload: { name: string; permission: string; expires_days?: number | null }) =>
    post<ApiKeyCreated>('/api/v1/settings/api-keys', payload),

  deleteApiKey: (keyId: string) =>
    del<{ key_id: string; deleted: boolean }>(`/api/v1/settings/api-keys/${keyId}`),

  getTelemetry: () =>
    get<TelemetryData>('/api/v1/settings/telemetry'),

  testConnector: (connector: 'akshare' | 'eastmoney') =>
    post<{ connector: string; status: 'success' | 'error'; latency_ms: number | null; message?: string }>(
      '/api/v1/settings/connectors/test', { connector }, TIMEOUT.aggregate),

  clearCache: () =>
    post<{ redis_cleared: boolean; deleted: number; protected: string[]; freed_mb: number; message: string }>(
      '/api/v1/settings/data/cache/clear', {}, TIMEOUT.aggregate),

  backupDb: () =>
    post<{ file: string; size_mb: number }>('/api/v1/settings/db/backup', {}, TIMEOUT.medium),

  syncDaily: () =>
    post<{ started: boolean; mode: string; total: number }>(
      '/api/v1/settings/data/sync', {}, TIMEOUT.default),
};
