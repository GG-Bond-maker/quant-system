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

/** 预警数据源健康（GET /alerts/health）。
 *  score_topk 在股票池快照缺失时会**跳过本轮判定**（既不触发也不报错），
 *  该状态是本端点的唯一可见入口；degraded=true 表示 Top-K 迁移规则正在静默失效。 */
export interface AlertHealth {
  degraded: boolean;
  reason: string | null;
  detail: string | null;
  snapshot_date: string | null;
  checked_at: string | null;
  last_ok_at: string | null;
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

  /** 预警数据源健康：degraded=true 时 Top-K 迁移规则本轮被跳过（静默失效可视化）。 */
  health: () => get<AlertHealth>('/api/v1/alerts/health'),

  createRule: (payload: AlertRulePayload) =>
    post<AlertRule>('/api/v1/alerts/rules', payload),

  updateRule: (id: number, payload: AlertRulePayload) =>
    put<AlertRule>(`/api/v1/alerts/rules/${id}`, payload),

  deleteRule: (id: number) => del<{ id: number }>(`/api/v1/alerts/rules/${id}`),

  events: (unread = false, limit = 50) =>
    get<AlertEventItem[]>('/api/v1/alerts/events', { unread: unread ? 1 : 0, limit }),

  /** 批量已读（指定 id）。 */
  markRead: (ids: number[]) => post<{ marked: number }>('/api/v1/alerts/events/read', { ids }),

  /** C-4：全部已读——必须走后端 `{all:true}`，覆盖展示窗口之外的未读；
   *  此前前端只回传窗口内 ids，窗口外未读永远标不掉（假清零）。 */
  markAllRead: () => post<{ marked: number }>('/api/v1/alerts/events/read', { all: true }),
};
