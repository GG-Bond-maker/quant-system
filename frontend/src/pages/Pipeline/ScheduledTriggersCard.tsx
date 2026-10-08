import React from 'react';
import type { ScheduledTrigger } from '@/api/production';

interface ScheduledTriggersCardProps {
  triggers?: ScheduledTrigger[];
}

export const ScheduledTriggersCard: React.FC<ScheduledTriggersCardProps> = ({ triggers }) => {
  if (!triggers || !triggers.length) return null;

  return (
    <div className="rounded-xl border border-border-subtle bg-surface-base p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-3">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm text-ink">
            常驻后台定时调度计划 (Cron Scheduled Triggers)
          </span>
          <span className="rounded bg-success-bg px-2 py-0.5 text-2xs font-semibold text-success border border-success/30">
            守护进程活跃 (4 个常驻触发点)
          </span>
        </div>
        <div className="text-2xs text-ink-muted">
          由系统调度中枢自动触发，支持工作日盘前、午盘、盘后与周末全周期覆盖
        </div>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        {triggers.map((t) => (
          <div
            key={t.id}
            className="flex flex-col justify-between rounded-lg border border-border-subtle bg-surface-raised p-3 text-xs"
          >
            <div>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-semibold text-ink">{t.name}</div>
                  <div className="font-mono text-2xs text-ink-muted">[{t.id}] · {t.category}</div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className="rounded bg-success-bg px-1.5 py-0.5 text-2xs font-semibold text-success border border-success/30">
                    {t.status}
                  </span>
                  <span className="font-mono text-2xs text-brand font-medium">{t.trigger_time}</span>
                </div>
              </div>

              <div className="mt-2 text-2xs text-ink-secondary leading-relaxed">{t.desc}</div>

              <div className="mt-2 flex flex-wrap items-center gap-1">
                <span className="text-2xs text-ink-muted">触发目标:</span>
                {t.target_steps.slice(0, 5).map((step) => (
                  <span
                    key={step}
                    className="rounded bg-surface-base px-1.5 py-0.2 font-mono text-[10px] text-ink border border-border-subtle"
                  >
                    {step}
                  </span>
                ))}
                {t.target_steps.length > 5 && (
                  <span className="text-2xs text-ink-muted">
                    +{t.target_steps.length - 5} 步
                  </span>
                )}
              </div>
            </div>

            <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-2 text-2xs text-ink-muted">
              <span>Cron: <code className="font-mono text-ink">{t.cron}</code></span>
              <span>上次: <span className="num text-ink">{t.last_run ?? '—'}</span></span>
              <span>下次: <span className="num text-brand font-medium">{t.next_run ?? '—'}</span></span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
