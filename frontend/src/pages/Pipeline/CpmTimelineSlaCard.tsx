import React from 'react';
import type { DagStatus, GanttTask, SlaMetrics } from '@/api/production';

interface CpmTimelineSlaCardProps {
  dag: DagStatus;
}

export const CpmTimelineSlaCard: React.FC<CpmTimelineSlaCardProps> = ({ dag }) => {
  const timeline: GanttTask[] = dag.gantt_timeline || [];
  const sla: SlaMetrics | undefined = dag.sla_metrics;
  const criticalPath = dag.critical_path || [];
  const totalDuration = dag.total_duration_s || timeline.reduce((acc, t) => acc + t.duration_s, 0);

  if (!timeline.length) {
    return <div className="py-8 text-center text-xs text-ink-muted">暂无甘特时序数据</div>;
  }

  // 计算最大跨度用于百分比绘制
  const maxSpan = Math.max(
    totalDuration,
    ...timeline.map((t) => (t.lf_s != null ? t.lf_s : t.offset_s + t.duration_s))
  );

  return (
    <div className="space-y-4">
      {/* SLA Predictive Governance Card */}
      {sla && (
        <div className="rounded-xl border border-border-subtle bg-surface-base p-4">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-3">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm text-ink">
                SLA 履约预测与时效红线治理 (Operations Research SLA Model)
              </span>
              <span
                className={`rounded px-2 py-0.5 text-2xs font-semibold ${
                  sla.sla_status === 'COMPLIANT'
                    ? 'bg-success-bg text-success border border-success/30'
                    : sla.sla_status === 'AT_RISK'
                    ? 'bg-warn-bg text-warn border border-warn/30'
                    : 'bg-danger-bg text-danger border border-danger/30'
                }`}
              >
                {sla.sla_status === 'COMPLIANT'
                  ? '履约合规 (COMPLIANT)'
                  : sla.sla_status === 'AT_RISK'
                  ? '时效受压 (AT_RISK)'
                  : 'SLA 违约 (BREACHED)'}
              </span>
            </div>
            <div className="text-2xs text-ink-muted">
              启动计划: {sla.scheduled_start} · 投研交付红线: {sla.deadline} (窗口预算 180 分钟)
            </div>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
              <div className="text-2xs text-ink-muted">关键路径总工期 (T_CP)</div>
              <div className="num mt-1 text-base font-bold text-ink">
                {sla.critical_path_duration_s}s
              </div>
              <div className="text-2xs text-ink-secondary">
                约 {(sla.critical_path_duration_s / 60).toFixed(1)} 分钟 · {sla.critical_steps_count} 步骤
              </div>
            </div>

            <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
              <div className="text-2xs text-ink-muted">完工分位数预测 (P50 / P95)</div>
              <div className="num mt-1 text-base font-bold text-brand">
                {sla.p50_duration_s}s / {sla.p95_duration_s}s
              </div>
              <div className="text-2xs text-ink-secondary">95% 置信度最晚完工时间</div>
            </div>

            <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
              <div className="text-2xs text-ink-muted">红线安全缓冲 (Buffer Margin)</div>
              <div className="num mt-1 text-base font-bold text-success">
                {(sla.buffer_remaining_s / 60).toFixed(1)} 分钟
              </div>
              <div className="text-2xs text-ink-secondary">
                预算剩余: {sla.buffer_remaining_s.toFixed(0)} 秒
              </div>
            </div>

            <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
              <div className="text-2xs text-ink-muted">20:30 履约交付概率</div>
              <div className="num mt-1 text-base font-bold text-success">
                {sla.compliance_probability}%
              </div>
              <div className="text-2xs text-ink-secondary">正态累积分布 Φ(Z) 积分值</div>
            </div>
          </div>
        </div>
      )}

      {/* Operations Research CPM Timeline & Gantt View */}
      <div className="rounded-xl border border-border-subtle bg-surface-base p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-3">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm text-ink">
              图论 CPM 关键路径甘特时序 (Critical Path Method · Float Analysis)
            </span>
            <span className="rounded bg-warn-bg px-2 py-0.5 text-2xs font-medium text-warn border border-warn/30">
              关键路径无浮动 (Total Float = 0)
            </span>
          </div>
          <div className="flex items-center gap-2 text-2xs text-ink-muted">
            <span>理论最短完工工期: </span>
            <span className="num font-semibold text-ink">{totalDuration} 秒</span>
          </div>
        </div>

        {/* Legend */}
        <div className="my-3 flex flex-wrap items-center gap-4 text-2xs text-ink-secondary">
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-4 rounded bg-warn border border-warn/80" />
            <span>关键路径步骤 (Critical Path, 延误将直接推迟全流程交付)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-4 rounded bg-brand/70 border border-brand/50" />
            <span>旁路步骤 (具备总时差 Total Float)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-1.5 w-6 border-b-2 border-dashed border-ink-muted" />
            <span>浮动时差 (Slack / Float 宽限窗口)</span>
          </div>
        </div>

        {/* Timeline Gantt Rows */}
        <div className="space-y-2 pt-1">
          {timeline.map((item) => {
            const es = item.es_s ?? item.offset_s;
            const dur = item.duration_s;
            const tf = item.total_float_s ?? item.slack_s;
            const lf = item.lf_s ?? es + dur + tf;

            const leftPct = (es / maxSpan) * 100;
            const widthPct = Math.max(3, (dur / maxSpan) * 100);
            const floatWidthPct = (tf / maxSpan) * 100;

            const isCritical = Boolean(item.is_critical_path);

            return (
              <div
                key={item.id}
                className="grid grid-cols-[11rem_1fr_6rem] items-center gap-3 text-2xs py-1 hover:bg-surface-raised rounded px-1.5 transition-colors"
              >
                {/* Step Name & Critical Badge */}
                <div className="flex items-center gap-1.5 truncate">
                  <span
                    className={`font-mono text-ink truncate font-medium ${
                      isCritical ? 'font-semibold text-warn' : ''
                    }`}
                    title={`${item.name} (${item.id})`}
                  >
                    {item.name}
                  </span>
                  {isCritical && (
                    <span className="rounded bg-warn/20 px-1 py-0.2 text-[9px] font-bold text-warn">
                      CP
                    </span>
                  )}
                </div>

                {/* Gantt Bar with Slack Line */}
                <div className="relative h-6 w-full rounded bg-surface-raised">
                  {/* Total Float / Slack Area (Dashed extension) */}
                  {tf > 0 && (
                    <div
                      className="absolute top-1/2 h-0 border-b-2 border-dashed border-ink-muted/50 -translate-y-1/2"
                      style={{
                        left: `${leftPct + widthPct}%`,
                        width: `${floatWidthPct}%`,
                      }}
                      title={`总时差 (Total Float): ${tf}s (最迟完成: ${lf}s)`}
                    />
                  )}

                  {/* Main Execution Bar */}
                  <div
                    className={`absolute top-0.5 bottom-0.5 rounded px-2 flex items-center justify-between transition-all ${
                      isCritical
                        ? 'border border-warn bg-warn text-white shadow-xs'
                        : 'border border-brand/50 bg-brand/70 text-white'
                    }`}
                    style={{
                      left: `${leftPct}%`,
                      width: `${widthPct}%`,
                    }}
                    title={`${item.name}: 工期 ${dur}s · 最早启动 ES=${es}s · 最早完成 EF=${es + dur}s`}
                  >
                    <span className="truncate font-mono text-[10px] font-semibold">{dur}s</span>
                  </div>
                </div>

                {/* Timing Meta */}
                <div className="flex flex-col items-end text-right num text-2xs">
                  <span className="text-ink">+{es}s ~ {es + dur}s</span>
                  <span className={tf === 0 ? 'text-warn font-semibold' : 'text-ink-muted'}>
                    时差: {tf}s
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Math Model Note */}
        <div className="mt-4 border-t border-border-subtle pt-3 text-2xs text-ink-muted leading-relaxed">
          <span className="font-semibold text-ink">运筹学图论求解注记：</span>
          Forward Pass 正向推导计算各步骤最早开工时刻 \(ES\) 与最早完工时刻 \(EF\)；
          Backward Pass 逆向反推各步骤最迟完工时刻 \(LF\) 与最迟开工时刻 \(LS\)；
          时差 \(Total\ Float = LS - ES = LF - EF\)。
          当前识别出 {criticalPath.length} 个关键路径步骤：
          <span className="font-mono text-ink font-medium"> {criticalPath.join(' ➔ ')}</span>。
        </div>
      </div>
    </div>
  );
};
