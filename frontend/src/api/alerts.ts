/** 预警中心 API 分组（§4.1，Sprint2 MVP）。 */
import { get, post, put, del } from './client';
export type AlertRuleType =
  | 'price_pct'
  | 'price_cross'
  | 'volume_spike'
  | 'score_topk'
  | 'factor_quantile'
  | 'data_health';

export type AlertScope = 'symbol' | 'watchlist' | 'global';

/** 六类规则的参数（与后端 _PARAM_KEYS 白名单一致，前端只做表单映射） */
export interface AlertRuleParams {
  threshold?: number;    // price_pct: 涨跌幅阈值 %
  price?: number;        // price_cross: 价格阈值
  direction?: 'up' | 'down';
  window?: number;       // volume_spike / factor_quantile: 历史窗口
  k?: number;            // volume_spike: 放量倍数
  factor?: string;       // factor_quantile: features 列名
  quantile?: number;     // factor_quantile: 分位阈值
  metric?: 'disk' | 'pipeline' | 'source';  // data_health
}

export interface AlertRule {
  id: number;
  name: string;
  rule_type: AlertRuleType;
  scope: AlertScope;
  symbol: string | null;
  params: AlertRuleParams;
  channels: string[];
  cooldown_minutes: number;
  enabled: boolean;
  created_at: string;
}

export interface AlertRulePayload {
  name: string;
  rule_type: AlertRuleType;
  scope: AlertScope;
  symbol?: string | null;
  params: AlertRuleParams;
  channels: string[];
  cooldown_minutes: number;
  enabled: boolean;
}

export interface AlertEventItem {
  id: number;
  rule_id: number;
  symbol: string | null;
  triggered_at: string;
  payload: Record<string, unknown> & { rule?: string; rule_type?: string; ts?: string };
  is_read: boolean;
}

export const alertsApi = {
  rules: () => get<AlertRule[]>('/api/v1/alerts/rules'),

  createRule: (payload: AlertRulePayload) =>
    post<AlertRule>('/api/v1/alerts/rules', payload),

  updateRule: (id: number, payload: AlertRulePayload) =>
    put<AlertRule>(`/api/v1/alerts/rules/${id}`, payload),

  deleteRule: (id: number) => del<{ id: number }>(`/api/v1/alerts/rules/${id}`),

  events: (unread = false, limit = 50) =>
    get<AlertEventItem[]>('/api/v1/alerts/events', { unread: unread ? 1 : 0, limit }),

  markRead: (ids: number[]) => post<{ marked: number }>('/api/v1/alerts/events/read', { ids }),
};
