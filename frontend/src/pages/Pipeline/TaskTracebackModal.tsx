import React, { useState } from 'react';
import type { DataJobRecord } from '@/api/production';

interface TaskTracebackModalProps {
  job: DataJobRecord | null;
  onClose: () => void;
  onResumeFromStep?: (tradeDate: string, step: string) => void;
}

export const TaskTracebackModal: React.FC<TaskTracebackModalProps> = ({
  job,
  onClose,
  onResumeFromStep,
}) => {
  const [copied, setCopied] = useState(false);

  if (!job) return null;

  const handleCopy = () => {
    const textToCopy = job.traceback || job.error_message || '无堆栈信息';
    navigator.clipboard.writeText(textToCopy).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const getDiagnosis = () => {
    const err = (job.error_message ?? '') + (job.traceback ?? '');
    if (err.includes('non_trade_day')) {
      return {
        level: 'info',
        title: '非交易日拦截',
        advice: '本日为非交易日（休市），交易日历门禁生效，属于正常幂等防护，无需重跑。',
      };
    }
    if (err.includes('PipelineBusy')) {
      return {
        level: 'warning',
        title: '并发互斥锁冲突 (PipelineBusy)',
        advice: '另一项同步、训练或流水线任务正在持有管道锁，请等待其执行完成或在锁释放后重试。',
      };
    }
    if (err.includes('Missing') || err.includes('schema') || err.includes('Column')) {
      return {
        level: 'danger',
        title: '数据契约 / Schema 缺失异常',
        advice: 'Parquet 分区缺少指定列或数据为空，请检查上游行情清洗或因子物化产物完整性。',
      };
    }
    if (err.includes('timeout') || err.includes('Connection') || err.includes('403')) {
      return {
        level: 'danger',
        title: '外部数据源网络超时 / 频控限制',
        advice: '行情抓取接口受到目标网站限流或网络丢包，建议稍后通过断点续跑从失败步骤重试。',
      };
    }
    return {
      level: 'warning',
      title: '生产流水线异常中断',
      advice: '执行过程出现未捕获异常，请参考下方 Python 堆栈日志排查，定位问题后可从中断步骤继续执行。',
    };
  };

  const diag = getDiagnosis();

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="traceback-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl border border-border-subtle bg-surface-base shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-3.5">
          <div className="flex items-center gap-3">
            <span
              className={`rounded px-2 py-0.5 text-2xs font-semibold ${
                job.status === 'SUCCESS'
                  ? 'bg-success-bg text-success'
                  : job.status === 'FAILED'
                  ? 'bg-danger-bg text-danger'
                  : 'bg-warn-bg text-warn'
              }`}
            >
              {job.status}
            </span>
            <div id="traceback-modal-title" className="text-sm font-semibold text-ink">
              任务异常诊断与执行堆栈 · {job.job_type}
            </div>
            <span className="num rounded bg-surface-raised px-2 py-0.5 text-2xs text-ink-secondary">
              交易日: {job.trade_date}
            </span>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-ink-muted hover:bg-surface-raised hover:text-ink"
            aria-label="关闭弹窗"
          >
            ✕
          </button>
        </div>

        {/* Modal Meta Summary */}
        <div className="grid grid-cols-2 gap-3 border-b border-border-subtle bg-surface-raised/50 px-5 py-3 text-xs sm:grid-cols-4">
          <div>
            <div className="text-2xs text-ink-muted">中断/当前步骤</div>
            <div className="font-mono font-medium text-ink">{job.current_step ?? '—'}</div>
          </div>
          <div>
            <div className="text-2xs text-ink-muted">耗时 / 规模</div>
            <div className="num font-medium text-ink">
              {job.duration_ms != null ? `${(job.duration_ms / 1000).toFixed(1)}s` : '—'}{' '}
              {job.codes_count != null ? `(${job.codes_count} 标的)` : ''}
            </div>
          </div>
          <div>
            <div className="text-2xs text-ink-muted">启动时刻</div>
            <div className="num text-2xs text-ink-secondary">{job.started_at ?? '—'}</div>
          </div>
          <div>
            <div className="text-2xs text-ink-muted">结束时刻</div>
            <div className="num text-2xs text-ink-secondary">{job.finished_at ?? '—'}</div>
          </div>
        </div>

        {/* Diagnosis Advice Box */}
        <div className="border-b border-border-subtle px-5 py-3">
          <div
            className={`rounded-lg border p-3 text-xs ${
              diag.level === 'danger'
                ? 'border-danger/30 bg-danger-bg/40 text-danger'
                : diag.level === 'warning'
                ? 'border-warn/30 bg-warn-bg/40 text-warn'
                : 'border-brand/30 bg-brand-bg/40 text-brand'
            }`}
          >
            <div className="font-semibold">{diag.title}</div>
            <div className="mt-1 text-ink-secondary">{diag.advice}</div>
          </div>
        </div>

        {/* Code Terminal Output */}
        <div className="flex-1 overflow-auto p-5">
          <div className="flex items-center justify-between pb-2 text-2xs text-ink-muted">
            <span className="font-mono">Python Exception Traceback & Error Output:</span>
            <button
              onClick={handleCopy}
              className="rounded border border-border-subtle bg-surface-raised px-2.5 py-1 text-2xs font-medium text-ink hover:bg-surface-overlay"
            >
              {copied ? '已复制到剪贴板 ✓' : '复制堆栈日志'}
            </button>
          </div>
          <div className="rounded-lg border border-border-subtle bg-surface-base p-4 font-mono text-2xs leading-relaxed text-ink shadow-inner overflow-x-auto whitespace-pre">
            {job.traceback ? (
              job.traceback
            ) : job.error_message ? (
              job.error_message
            ) : (
              <span className="text-ink-muted">本任务无异常错误信息，执行记录状态正常。</span>
            )}
          </div>
        </div>

        {/* Modal Footer with Actions */}
        <div className="flex items-center justify-between border-t border-border-subtle bg-surface-raised px-5 py-3">
          <div className="text-2xs text-ink-muted">
            任务 ID: {job.id ?? '—'} · 任务范围: {job.job_scope ?? '全市场'}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="rounded-lg border border-border-subtle bg-surface-base px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-raised"
            >
              关闭
            </button>
            {job.current_step && onResumeFromStep && (
              <button
                onClick={() => {
                  onResumeFromStep(job.trade_date, job.current_step!);
                  onClose();
                }}
                className="rounded-lg bg-brand px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-hover shadow-xs"
              >
                从中断步骤 [{job.current_step}] 断点续跑 ➔
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
