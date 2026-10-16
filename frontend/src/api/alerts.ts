/** 预警中心 API 分组（§4.1，Sprint2 MVP + 智能风控中枢演进）。 */
import { get, post, put, del } from './client';

export type AlertRuleType =
  | 'price_pct'
  | 'price_cross'
  | 'volume_spike'
  | 'score_topk'
  | 'factor_quantile'
  | 'data_health'
  | 'portfolio_risk'
  | 'liquidity_stress'
  | 'portfolio_drawdown'
  | 'portfolio_var'
  | 'basis_spread'
  | 'ah_premium';

export type AlertSeverity = 'critical' | 'warning' | 'info';

export type AlertScope = 'symbol' | 'watchlist' | 'global';

/** 十类规则的参数（与后端 _PARAM_KEYS 白名单一致，前端做表单映射） */
export interface AlertRuleParams {
  threshold?: number;    // price_pct / portfolio_risk / liquidity_stress: 阈值
  price?: number;        // price_cross: 价格阈值
  direction?: 'up' | 'down' | 'above' | 'below';
  window?: number;       // volume_spike / factor_quantile: 历史窗口
  k?: number;            // volume_spike: 放量倍数
  factor?: string;       // factor_quantile / portfolio_risk 列名
  quantile?: number;     // factor_quantile: 分位阈值
  metric?: 'disk' | 'pipeline' | 'source' | 'amihud' | 'seal_ratio' | 'turnover_burst';  // data_health / liquidity_stress
  // P0 新增参数：
  max_drawdown?: number; // portfolio_drawdown: 动态高水位回撤最大百分比，如 5.0
  benchmark?: string;    // portfolio_drawdown: 基准代码，如 000300.SH
  confidence?: number;   // portfolio_var: 置信度，如 0.95
  limit_pct?: number;    // portfolio_var: VaR 超限百分比，如 2.5
  time_horizon?: number; // portfolio_var: 持有期(日)，如 1
  severity?: AlertSeverity; // 规则默认严重度
  hysteresis?: number;   // 迟滞防抖容差
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

export interface AlertTopSymbol {
  symbol: string;
  count: number;
  last_severity?: AlertSeverity;
}

/** 全局预警与风控态势统计 KPI（GET /alerts/stats） */
export interface AlertStats {
  today_events_count: number;
  unread_count: number;
  critical_count: number;
  warning_count: number;
  info_count: number;
  active_rules_count: number;
  total_rules_count: number;
  health_degraded: boolean;
  risk_level: 'NORMAL' | 'ELEVATED' | 'HIGH_RISK' | 'DEGRADED';
  top_symbols: AlertTopSymbol[];
}

export interface AlertEventPayload {
  rule?: string;
  rule_type?: AlertRuleType | string;
  ts?: string;
  severity?: AlertSeverity;
  remediation_action?: 'sell_or_reduce' | 'add_exclusion' | 'review';
  note?: string;
  pct?: number;
  threshold_pct?: number;
  price?: number;
  threshold_price?: number;
  direction?: string;
  volume_hand?: number;
  k?: number;
  action?: string;
  factor?: string;
  quantile?: number;
  metric?: string;
  usage_percent?: number;
  failed_jobs?: number;
  recent_truncated?: boolean;
  truncated?: boolean;
  recent_limit?: number;
  supplement_limit?: number;
  last_error?: string;
  current_drawdown?: number;
  max_drawdown?: number;
  high_watermark?: number;
  var_95?: number;
  limit_pct?: number;
  amihud?: number;
  [key: string]: unknown;
}

export interface AlertEventItem {
  id: number;
  rule_id: number;
  symbol: string | null;
  triggered_at: string;
  payload: AlertEventPayload;
  is_read: boolean;
}

export interface AlertEventFilterParams {
  unread?: boolean;
  limit?: number;
  severity?: AlertSeverity | '';
  rule_type?: AlertRuleType | '';
  symbol?: string;
}

export const alertsApi = {
  rules: () => get<AlertRule[]>('/api/v1/alerts/rules'),

  /** 全局风控态势统计 KPI 汇总 */
  stats: () => get<AlertStats>('/api/v1/alerts/stats'),

  /** 预警数据源健康：degraded=true 时 Top-K 迁移规则本轮被跳过（静默失效可视化）。 */
  health: () => get<AlertHealth>('/api/v1/alerts/health'),

  createRule: (payload: AlertRulePayload) =>
    post<AlertRule>('/api/v1/alerts/rules', payload),

  updateRule: (id: number, payload: AlertRulePayload) =>
    put<AlertRule>(`/api/v1/alerts/rules/${id}`, payload),

  deleteRule: (id: number) => del<{ id: number }>(`/api/v1/alerts/rules/${id}`),

  /** 获取预警触发事件：支持参数对象或 (unread, limit) 经典入参 */
  events: (
    unreadOrFilter: boolean | AlertEventFilterParams = false,
    limit = 50,
  ) => {
    if (typeof unreadOrFilter === 'object' && unreadOrFilter !== null) {
      const q: Record<string, string | number> = {};
      if (unreadOrFilter.unread !== undefined) q.unread = unreadOrFilter.unread ? 1 : 0;
      if (unreadOrFilter.limit !== undefined) q.limit = unreadOrFilter.limit;
      if (unreadOrFilter.severity) q.severity = unreadOrFilter.severity;
      if (unreadOrFilter.rule_type) q.rule_type = unreadOrFilter.rule_type;
      if (unreadOrFilter.symbol) q.symbol = unreadOrFilter.symbol.trim().toUpperCase();
      return get<AlertEventItem[]>('/api/v1/alerts/events', q);
    }
    return get<AlertEventItem[]>('/api/v1/alerts/events', {
      unread: unreadOrFilter ? 1 : 0,
      limit,
    });
  },

  /** 批量已读（指定 id）。 */
  markRead: (ids: number[]) => post<{ marked: number }>('/api/v1/alerts/events/read', { ids }),

  /** C-4：全部已读——必须走后端 `{all:true}`，覆盖展示窗口之外的未读；
   *  此前前端只回传窗口内 ids，窗口外未读永远标不掉（假清零）。 */
  markAllRead: () => post<{ marked: number }>('/api/v1/alerts/events/read', { all: true }),
};
