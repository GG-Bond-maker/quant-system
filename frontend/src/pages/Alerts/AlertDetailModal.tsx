import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { AlertEventItem, AlertSeverity } from '@/api/alerts';
import { deskApi } from '@/api/production';
import { Modal } from '@/components/ui';
import { fmtNum } from '@/utils/format';

interface AlertDetailModalProps {
  event: AlertEventItem | null;
  open: boolean;
  onClose: () => void;
  onMarkRead?: (id: number) => Promise<void>;
  onExclusionAdded?: () => void;
}

const SEVERITY_CONFIG: Record<
  AlertSeverity,
  { label: string; badgeCls: string; dotCls: string }
> = {
  critical: {
    label: '致命告警 (Critical)',
    badgeCls: 'bg-danger-bg text-danger border-danger/30',
    dotCls: 'bg-danger',
  },
  warning: {
    label: '中级风险 (Warning)',
    badgeCls: 'bg-warn-bg text-warn border-warn/30',
    dotCls: 'bg-warn',
  },
  info: {
    label: '提示信息 (Info)',
    badgeCls: 'bg-brand-50 text-brand-600 border-brand-200',
    dotCls: 'bg-brand-500',
  },
};

export const AlertDetailModal: React.FC<AlertDetailModalProps> = ({
  event,
  open,
  onClose,
  onMarkRead,
  onExclusionAdded,
}) => {
  const navigate = useNavigate();
  const [addingExclusion, setAddingExclusion] = useState(false);
  const [exclusionSuccess, setExclusionSuccess] = useState(false);
  const [exclusionError, setExclusionError] = useState<string | null>(null);

  if (!event) return null;

  const p = event.payload ?? {};
  const severity: AlertSeverity = (p.severity as AlertSeverity) || 'info';
  const sevConfig = SEVERITY_CONFIG[severity] ?? SEVERITY_CONFIG.info;
  const ruleType = String(p.rule_type || '');

  // 跳转交易台
  const handleGoToDesk = () => {
    if (!event.symbol) return;
    onClose();
    navigate(`/desk?symbol=${encodeURIComponent(event.symbol)}&action=sell`);
  };

  // 一键列入禁买黑名单
  const handleAddToExclusion = async () => {
    if (!event.symbol) return;
    setAddingExclusion(true);
    setExclusionError(null);
    try {
      const reason = `预警中心联动 [${p.rule || ruleType}]: ${p.note || '风控指标超限'}`;
      await deskApi.addExclusion(event.symbol, 'risk_alert', reason);
      setExclusionSuccess(true);
      onExclusionAdded?.();
    } catch (e) {
      setExclusionError(e instanceof Error ? e.message : '列入禁买名单失败');
    } finally {
      setAddingExclusion(false);
    }
  };

  const renderMetricDiagnostics = () => {
    if (ruleType === 'portfolio_drawdown') {
      return (
        <div className="grid grid-cols-2 gap-3 rounded border border-hair bg-surface-sunken p-3 text-xs">
          <div>
            <div className="text-2xs text-ink-secondary">动态高水位当前回撤</div>
            <div className="num mt-1 text-base font-semibold text-danger">
              -{Number(p.current_drawdown ?? 0).toFixed(2)}%
            </div>
          </div>
          <div>
            <div className="text-2xs text-ink-secondary">高水位止损阈值</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              {Number(p.max_drawdown ?? 0).toFixed(2)}%
            </div>
          </div>
        </div>
      );
    }

    if (ruleType === 'portfolio_var') {
      return (
        <div className="grid grid-cols-2 gap-3 rounded border border-hair bg-surface-sunken p-3 text-xs">
          <div>
            <div className="text-2xs text-ink-secondary">95% Parametric VaR</div>
            <div className="num mt-1 text-base font-semibold text-danger">
              {Number(p.var_95 ?? 0).toFixed(2)}%
            </div>
          </div>
          <div>
            <div className="text-2xs text-ink-secondary">VaR 风控限额</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              {Number(p.limit_pct ?? 0).toFixed(2)}%
            </div>
          </div>
        </div>
      );
    }

    if (ruleType === 'price_pct') {
      return (
        <div className="grid grid-cols-2 gap-3 rounded border border-hair bg-surface-sunken p-3 text-xs">
          <div>
            <div className="text-2xs text-ink-secondary">单日涨跌幅</div>
            <div
              className={`num mt-1 text-base font-semibold ${
                Number(p.pct ?? 0) >= 0 ? 'text-up' : 'text-down'
              }`}
            >
              {Number(p.pct ?? 0) >= 0 ? '+' : ''}
              {Number(p.pct ?? 0).toFixed(2)}%
            </div>
          </div>
          <div>
            <div className="text-2xs text-ink-secondary">触发阈值</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              ±{Number(p.threshold_pct ?? 0).toFixed(2)}%
            </div>
          </div>
        </div>
      );
    }

    if (ruleType === 'liquidity_stress') {
      return (
        <div className="grid grid-cols-2 gap-3 rounded border border-hair bg-surface-sunken p-3 text-xs">
          <div>
            <div className="text-2xs text-ink-secondary">微观流动性指标</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              {String(p.metric || 'Amihud 冲击')}
            </div>
          </div>
          <div>
            <div className="text-2xs text-ink-secondary">冲击数值 / 阈值</div>
            <div className="num mt-1 text-base font-semibold text-danger">
              {p.amihud ? Number(p.amihud).toFixed(3) : '—'} (阈值 {Number(p.threshold ?? 1.5)})
            </div>
          </div>
        </div>
      );
    }

    if (ruleType === 'volume_spike') {
      return (
        <div className="grid grid-cols-2 gap-3 rounded border border-hair bg-surface-sunken p-3 text-xs">
          <div>
            <div className="text-2xs text-ink-secondary">现量 (手)</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              {fmtNum(Number(p.volume_hand ?? 0))}
            </div>
          </div>
          <div>
            <div className="text-2xs text-ink-secondary">均量倍数</div>
            <div className="num mt-1 text-base font-semibold text-brand-600">
              {Number(p.k ?? 0)} 倍均量
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="rounded border border-hair bg-surface-sunken p-3 text-xs">
        <div className="text-2xs text-ink-secondary">事件详情快照</div>
        <div className="num mt-1 font-mono text-xs text-ink break-all">
          {JSON.stringify(p)}
        </div>
      </div>
    );
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="预警事件穿透诊断与闭环处置"
      sub={`事件 ID: #${event.id} · 规则: ${p.rule || '#' + event.rule_id}`}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            {!event.is_read && onMarkRead && (
              <button
                type="button"
                onClick={() => void onMarkRead(event.id)}
                className="rounded border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink-secondary hover:border-brand-300"
              >
                标记已读
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-200"
            >
              关闭
            </button>
            {event.symbol && (
              <>
                <button
                  type="button"
                  disabled={addingExclusion || exclusionSuccess}
                  onClick={() => void handleAddToExclusion()}
                  className="rounded border border-danger/30 bg-danger-bg px-3 py-1.5 text-xs font-medium text-danger hover:opacity-80 disabled:opacity-50"
                >
                  {exclusionSuccess ? '已列入禁买名单' : addingExclusion ? '提交中…' : '列入禁买名单'}
                </button>
                <button
                  type="button"
                  onClick={handleGoToDesk}
                  className="rounded bg-brand-500 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
                >
                  转交易台平仓/卖出
                </button>
              </>
            )}
          </div>
        </div>
      }
    >
      <div className="space-y-3.5 text-xs">
        {/* 顶部严重度徽标与触发信息 */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${sevConfig.badgeCls}`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${sevConfig.dotCls}`} />
              {sevConfig.label}
            </span>
            {event.symbol && (
              <span className="num font-semibold text-ink text-sm">
                {event.symbol}
              </span>
            )}
          </div>
          <div className="num text-2xs text-ink-muted">
            触发时间: {p.ts ?? event.triggered_at.replace('T', ' ').slice(0, 19)}
          </div>
        </div>

        {/* 核心风控指标穿透卡片 */}
        <div>
          <div className="mb-1 text-2xs font-semibold text-ink-secondary">指标穿透诊断</div>
          {renderMetricDiagnostics()}
        </div>

        {/* 运筹学/量化诊断说明 */}
        {p.note && (
          <div className="rounded-lg border border-brand-200 bg-brand-50/50 p-3 text-xs text-brand-900">
            <div className="font-semibold text-brand-700">量化风控诊断说明</div>
            <div className="mt-1 leading-relaxed text-ink-secondary">{String(p.note)}</div>
          </div>
        )}

        {/* 建议处置闭环指导 */}
        <div className="rounded-lg border border-hair bg-surface p-3">
          <div className="font-semibold text-ink">建议处置策略 (Remediation)</div>
          <div className="mt-1 text-xs text-ink-secondary">
            {p.remediation_action === 'sell_or_reduce' && (
              <p>
                ⚠️ <span className="font-medium text-danger">卖出/减仓处置：</span>
                该标的已严重偏离风控安全边界，建议点击下方「转交易台平仓/卖出」进行快速平仓或通过 VWAP 算法执行止损。
              </p>
            )}
            {p.remediation_action === 'add_exclusion' && (
              <p>
                🛡️ <span className="font-medium text-warn">禁买治理处置：</span>
                该标的触发微观流动性冲击或合规限制，建议点击「列入禁买名单」，系统将在所有选股与执行流水线中即刻拦截买入。
              </p>
            )}
            {p.remediation_action === 'review' && (
              <p>
                🔍 <span className="font-medium text-ink">人工复核排查：</span>
                涉及全盘风险或底层基础设施健康问题，请及时检查调度管线与数据源探针。
              </p>
            )}
            {!p.remediation_action && (
              <p className="text-ink-muted">暂无预设处置策略，请按常规投研风控流程处理。</p>
            )}
          </div>
        </div>

        {/* 反馈信息 */}
        {exclusionSuccess && (
          <div className="rounded border border-success/30 bg-success-bg px-3 py-2 text-2xs font-medium text-success">
            ✓ 标的 {event.symbol} 已成功列入交易台禁买黑名单，策略建仓已实时熔断阻断。
          </div>
        )}
        {exclusionError && (
          <div className="rounded border border-danger/30 bg-danger-bg px-3 py-2 text-2xs font-medium text-danger">
            ✕ {exclusionError}
          </div>
        )}
      </div>
    </Modal>
  );
};
