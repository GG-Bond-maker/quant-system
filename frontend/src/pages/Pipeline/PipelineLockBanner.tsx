import React from 'react';
import type { PipelineLockStatus } from '@/api/production';

interface PipelineLockBannerProps {
  lock?: PipelineLockStatus;
}

export const PipelineLockBanner: React.FC<PipelineLockBannerProps> = ({ lock }) => {
  if (!lock) return null;

  const isLocked = lock.is_locked;

  return (
    <div
      data-testid="pipeline-lock-banner"
      className={`rounded-lg border px-4 py-2.5 text-xs transition-colors ${
        isLocked
          ? 'border-warn/40 bg-warn-bg text-ink'
          : 'border-border-subtle bg-surface-raised text-ink-secondary'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-2xs font-semibold ${
              isLocked
                ? 'bg-warn text-white animate-pulse'
                : 'bg-success-bg text-success border border-success/30'
            }`}
          >
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${
                isLocked ? 'bg-white' : 'bg-success'
              }`}
            />
            {isLocked ? '管道互斥锁占用中' : '调度引擎就绪 (IDLE)'}
          </span>
          <span className="font-medium text-ink">
            {isLocked
              ? `槽位持有者: [${lock.owner ?? 'unknown'}] · 已独占执行 ${lock.elapsed_seconds.toFixed(1)} 秒`
              : '并发控制槽位空闲，支持立即触发生产流水线或重跑任务'}
          </span>
        </div>
        <div className="flex items-center gap-3 text-2xs text-ink-muted">
          <span>防并发争用：生产流水线、增量同步与模型训练全局排他</span>
          {isLocked && (
            <span className="rounded bg-surface-base px-2 py-0.5 font-mono text-ink">
              锁定状态: {lock.status}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
