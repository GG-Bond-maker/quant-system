import React from 'react';
import type { AlertHealth, AlertSeverity, AlertStats } from '@/api/alerts';

interface AlertStatsCardsProps {
  stats: AlertStats | null;
  health: AlertHealth | null;
  loading: boolean;
  onFilterSeverity?: (sev: AlertSeverity | '') => void;
  onFilterSymbol?: (sym: string) => void;
}

export const AlertStatsCards: React.FC<AlertStatsCardsProps> = ({
  stats,
  health,
  loading,
  onFilterSeverity,
  onFilterSymbol,
}) => {
  const riskMeta = {
    NORMAL: { label: '正常受控', tone: 'text-success', bg: 'bg-success-bg border-success/30' },
    ELEVATED: { label: '风险预警', tone: 'text-warn', bg: 'bg-warn-bg border-warn/30' },
    HIGH_RISK: { label: '严重超限', tone: 'text-danger', bg: 'bg-danger-bg border-danger/30' },
    DEGRADED: { label: '源降级', tone: 'text-danger', bg: 'bg-danger-bg border-danger/30' },
  };

  const currentRisk = stats ? riskMeta[stats.risk_level] ?? riskMeta.NORMAL : null;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {/* 卡片 1: 今日预警触发与未读 */}
      <div className="flex flex-col justify-between rounded-lg border border-hair bg-surface p-3.5 transition-shadow hover:shadow-sm">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-medium text-ink-secondary">今日预警触发总数</span>
          {stats && stats.unread_count > 0 && (
            <span className="rounded-full bg-danger-bg px-2 py-0.5 text-2xs font-semibold text-danger">
              {stats.unread_count} 待处理
            </span>
          )}
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="num text-2xl font-semibold text-ink">
            {loading && !stats ? '—' : stats?.today_events_count ?? 0}
          </span>
          <span className="text-2xs text-ink-muted">次今日触发</span>
        </div>
        <div className="mt-2 flex items-center justify-between border-t border-hair/50 pt-2 text-2xs text-ink-muted">
          <span>未读待阅口径</span>
          <span className="num font-medium text-ink-secondary">{stats?.unread_count ?? 0} 条未读</span>
        </div>
      </div>

      {/* 卡片 2: 致命/严重风险告警 */}
      <div className="flex flex-col justify-between rounded-lg border border-hair bg-surface p-3.5 transition-shadow hover:shadow-sm">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-medium text-ink-secondary">致命告警 (Critical)</span>
          <button
            type="button"
            onClick={() => onFilterSeverity?.('critical')}
            className="rounded border border-danger/30 bg-danger-bg px-1.5 py-0.5 text-2xs font-semibold text-danger hover:opacity-80"
          >
            仅看致命
          </button>
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span
            className={`num text-2xl font-semibold ${
              stats && stats.critical_count > 0 ? 'text-danger' : 'text-success'
            }`}
          >
            {loading && !stats ? '—' : stats?.critical_count ?? 0}
          </span>
          <span className="text-2xs text-ink-muted">起致命穿透</span>
        </div>
        <div className="mt-2 flex items-center justify-between border-t border-hair/50 pt-2 text-2xs text-ink-muted">
          <button
            type="button"
            onClick={() => onFilterSeverity?.('warning')}
            className="text-warn hover:underline"
          >
            Warning: {stats?.warning_count ?? 0}
          </button>
          <button
            type="button"
            onClick={() => onFilterSeverity?.('info')}
            className="text-ink-secondary hover:underline"
          >
            Info: {stats?.info_count ?? 0}
          </button>
        </div>
      </div>

      {/* 卡片 3: 全局风控防御态势 */}
      <div className="flex flex-col justify-between rounded-lg border border-hair bg-surface p-3.5 transition-shadow hover:shadow-sm">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-medium text-ink-secondary">风控防御态势</span>
          <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-ink-muted">
            动态评估
          </span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          {currentRisk ? (
            <span className={`rounded border px-2 py-1 text-sm font-bold ${currentRisk.bg} ${currentRisk.tone}`}>
              {currentRisk.label}
            </span>
          ) : (
            <span className="text-lg font-semibold text-ink-muted">—</span>
          )}
          {stats?.health_degraded && (
            <span className="rounded bg-danger-bg px-1.5 py-0.5 text-2xs font-bold text-danger">
              数据源降级
            </span>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between border-t border-hair/50 pt-2 text-2xs text-ink-muted">
          <span>值守规则</span>
          <span className="num font-medium text-ink-secondary">
            {stats?.active_rules_count ?? 0} / {stats?.total_rules_count ?? 0} 启用
          </span>
        </div>
      </div>

      {/* 卡片 4: 高频告警标的与探针状态 */}
      <div className="flex flex-col justify-between rounded-lg border border-hair bg-surface p-3.5 transition-shadow hover:shadow-sm">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-medium text-ink-secondary">高频告警标的 Top 3</span>
          <span className="text-2xs text-ink-muted">24H 统计</span>
        </div>
        <div className="mt-2 min-h-[28px] flex flex-wrap items-center gap-1.5">
          {stats?.top_symbols && stats.top_symbols.length > 0 ? (
            stats.top_symbols.slice(0, 3).map((s) => (
              <button
                key={s.symbol}
                type="button"
                onClick={() => onFilterSymbol?.(s.symbol)}
                title={`点击筛选标的 ${s.symbol}（触发 ${s.count} 次）`}
                className="flex items-center gap-1 rounded border border-hair bg-surface-sunken px-1.5 py-0.5 text-2xs text-ink hover:border-brand-300"
              >
                <span className="num font-medium">{s.symbol}</span>
                <span className="rounded bg-danger-bg px-1 text-[10px] font-bold text-danger">
                  {s.count}
                </span>
              </button>
            ))
          ) : (
            <span className="text-xs text-ink-muted">无高频预警标的</span>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between border-t border-hair/50 pt-2 text-2xs text-ink-muted">
          <span>Top-K 数据探针</span>
          <span
            className={`font-medium ${
              health?.degraded ? 'text-danger' : health?.checked_at ? 'text-success' : 'text-ink-muted'
            }`}
          >
            {health?.degraded ? '⚠️ 降级' : health?.checked_at ? '✓ 正常' : '未评估'}
          </span>
        </div>
      </div>
    </div>
  );
};
