import React, { useState } from 'react';
import type { DagEdge, DagStage, DagStatus } from '@/api/production';

interface PipelineDagTopologyCardProps {
  dag: DagStatus;
  onSelectStepToRerun?: (stepId: string) => void;
}

const CATEGORY_LABEL: Record<string, { label: string; tone: string }> = {
  ingest: { label: '数据采集', tone: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  qc: { label: '数据质检', tone: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  metadata: { label: '元数据同步', tone: 'bg-purple-500/10 text-purple-600 dark:text-purple-400' },
  data_process: { label: '行情重算', tone: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400' },
  universe: { label: '股票宇宙', tone: 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400' },
  feature: { label: '特征工程', tone: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' },
  model: { label: '模型推理', tone: 'bg-rose-500/10 text-rose-600 dark:text-rose-400' },
  signal: { label: '选股雷达', tone: 'bg-brand/10 text-brand' },
};

export const PipelineDagTopologyCard: React.FC<PipelineDagTopologyCardProps> = ({
  dag,
  onSelectStepToRerun,
}) => {
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);

  const stages = dag.stages || [];
  const stageMap = new Map<string, DagStage>(stages.map((s) => [s.id, s]));
  const edges: DagEdge[] = dag.dag_edges || [];

  const selectedStage = selectedStepId ? stageMap.get(selectedStepId) : null;

  // 获取上游与下游
  const upstreamSteps = selectedStepId
    ? edges.filter((e) => e.target === selectedStepId).map((e) => stageMap.get(e.source)!)
    : [];
  const downstreamSteps = selectedStepId
    ? edges.filter((e) => e.source === selectedStepId).map((e) => stageMap.get(e.target)!)
    : [];

  const renderStageNode = (id: string) => {
    const stage = stageMap.get(id);
    if (!stage) return null;

    const isSelected = selectedStepId === id;
    const isCritical = Boolean(stage.is_critical_path);
    const cat = stage.category ? CATEGORY_LABEL[stage.category] : null;

    let statusStyle = 'border-border-subtle bg-surface-raised text-ink';
    let statusText = '未扫描';
    if (stage.status === 'ok') {
      statusStyle = 'border-success/40 bg-success-bg/40 text-success';
      statusText = '已更新';
    } else if (stage.status === 'running') {
      statusStyle = 'border-brand/40 bg-brand-bg text-brand animate-pulse';
      statusText = '执行中';
    } else if (stage.status === 'failed') {
      statusStyle = 'border-danger/40 bg-danger-bg text-danger';
      statusText = '失败中断';
    } else if (stage.status === 'partial') {
      statusStyle = 'border-warn/40 bg-warn-bg text-warn';
      statusText = '部分就绪';
    } else if (stage.status === 'stale') {
      statusStyle = 'border-warn/30 bg-surface-raised text-ink-muted';
      statusText = '待更新';
    }

    return (
      <div
        key={id}
        onClick={() => setSelectedStepId(isSelected ? null : id)}
        className={`group relative flex cursor-pointer flex-col justify-between rounded-lg border p-2.5 transition-all hover:shadow-md ${
          isSelected
            ? 'ring-2 ring-brand border-brand shadow-sm bg-surface-overlay'
            : isCritical
            ? 'border-warn/60 bg-surface-raised shadow-xs'
            : 'border-border-subtle bg-surface-raised'
        }`}
      >
        {/* Node Header */}
        <div className="flex items-start justify-between gap-1.5">
          <div className="flex flex-col">
            <span className="font-semibold text-xs text-ink group-hover:text-brand transition-colors">
              {stage.name}
            </span>
            <span className="font-mono text-2xs text-ink-muted">{stage.id}</span>
          </div>
          <div className="flex flex-col items-end gap-1">
            <span className={`rounded px-1.5 py-0.5 text-2xs font-semibold ${statusStyle}`}>
              {statusText}
            </span>
            {isCritical && (
              <span className="rounded bg-warn/20 px-1 py-0.2 text-[10px] font-bold text-warn tracking-tight">
                CRITICAL
              </span>
            )}
          </div>
        </div>

        {/* Node Body */}
        <div className="mt-2 space-y-1 text-2xs">
          <div className="flex items-center justify-between text-ink-secondary">
            <span>产物数据集:</span>
            <span className="font-mono text-ink truncate max-w-[120px]" title={stage.dataset}>
              {stage.dataset}
            </span>
          </div>
          <div className="flex items-center justify-between text-ink-secondary">
            <span>最新日期:</span>
            <span className="num font-medium text-ink">{stage.done_date ?? '—'}</span>
          </div>
          {stage.rows != null && (
            <div className="flex items-center justify-between text-ink-secondary">
              <span>分区行数:</span>
              <span className="num text-ink">{stage.rows.toLocaleString()} 行</span>
            </div>
          )}
        </div>

        {/* Node Footer with CPM metrics */}
        <div className="mt-2.5 flex items-center justify-between border-t border-border-subtle/60 pt-1.5 text-2xs">
          {cat && (
            <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${cat.tone}`}>
              {cat.label}
            </span>
          )}
          <div className="flex items-center gap-1.5 num text-ink-muted">
            <span>工期: {stage.duration_s ?? stage.base_duration_s ?? 0}s</span>
            <span
              className={stage.slack_s === 0 ? 'text-warn font-semibold' : 'text-ink-secondary'}
            >
              浮动: {stage.slack_s ?? 0}s
            </span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Topology Structure Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-ink">全流程 11 步真实有向无环图 (DAG) 拓扑</span>
          <span className="rounded bg-surface-base px-2 py-0.5 text-2xs font-mono text-ink-secondary">
            11 Nodes · {edges.length} Dependencies · 2 Branch/Join
          </span>
        </div>
        <div className="flex items-center gap-3 text-2xs text-ink-muted">
          <div className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full border border-warn bg-warn/40" />
            <span>关键路径 (Slack=0)</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full border border-border-subtle bg-surface-raised" />
            <span>旁路分支 (具备时差)</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-success" />
            <span>新鲜已更新</span>
          </div>
        </div>
      </div>

      {/* DAG Flow Pipeline Layout */}
      <div className="rounded-xl border border-border-subtle bg-surface-base p-4">
        {/* Layer 1: Ingest & Initial Validation */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-2xs font-semibold text-ink-muted uppercase tracking-wider">
            <span>阶段 1: 行情采集与前置门禁</span>
            <div className="h-px flex-1 bg-border-subtle" />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {renderStageNode('update_daily')}
            {renderStageNode('validate')}
          </div>
        </div>

        {/* Arrow to Layer 2 */}
        <div className="my-2 flex justify-center text-ink-muted">
          <span className="text-sm font-bold">↓ 分叉并行 (Fork)</span>
        </div>

        {/* Layer 2: State Enrichment & Sidecar Announcements */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-2xs font-semibold text-ink-muted uppercase tracking-wider">
            <span>阶段 2: 状态回填与前复权重算 (退市/前复权/旁路公告)</span>
            <div className="h-px flex-1 bg-border-subtle" />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {renderStageNode('enrich_delist')}
            {renderStageNode('sync_announcements')}
            {renderStageNode('rebuild_qfq')}
          </div>
        </div>

        {/* Arrow to Layer 3 */}
        <div className="my-2 flex justify-center text-ink-muted">
          <span className="text-sm font-bold">↓ 汇聚至股票宇宙 (Join)</span>
        </div>

        {/* Layer 3: Stock Universe Construction & Backtest Pool */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-2xs font-semibold text-ink-muted uppercase tracking-wider">
            <span>阶段 3: 交易股票宇宙收敛与回测数据物化</span>
            <div className="h-px flex-1 bg-border-subtle" />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {renderStageNode('build_universe')}
            {renderStageNode('build_universe_bt')}
          </div>
        </div>

        {/* Arrow to Layer 4 */}
        <div className="my-2 flex justify-center text-ink-muted">
          <span className="text-sm font-bold">↓ 核心生产主链 (Main Production Chain)</span>
        </div>

        {/* Layer 4: Feature Engineering, Model Inference, Screener & CS Mirror */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-2xs font-semibold text-ink-muted uppercase tracking-wider">
            <span>阶段 4: 特征计算、模型推理、选股雷达与截面镜像</span>
            <div className="h-px flex-1 bg-border-subtle" />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {renderStageNode('build_features')}
            {renderStageNode('infer')}
            {renderStageNode('screener_dump')}
            {renderStageNode('build_cs_mirror')}
          </div>
        </div>

        {/* Downstream Execution Desk Note */}
        <div className="mt-4 flex items-center justify-between rounded-lg border border-dashed border-border-subtle bg-surface-raised/40 p-3 text-xs">
          <div className="flex items-center gap-2">
            <span className="rounded bg-brand/10 px-2 py-0.5 text-2xs font-semibold text-brand">
              下游触发
            </span>
            <span className="font-medium text-ink">模拟盘与实盘执行中心 (Desk Orders)</span>
            <span className="text-2xs text-ink-muted">
              由交易员在交易执行台基于当日最新 Screener 快照一键生成母单
            </span>
          </div>
          <span className="num text-2xs text-ink-secondary">
            基准交易日: {dag.benchmark_date ?? '—'}
          </span>
        </div>
      </div>

      {/* Selected Node Drilldown Inspector */}
      {selectedStage && (
        <div className="rounded-xl border border-brand/40 bg-surface-raised p-4 transition-all">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-3">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm text-ink">{selectedStage.name}</span>
              <span className="font-mono text-xs text-ink-muted">[{selectedStage.id}]</span>
              <span className="rounded bg-brand/10 px-2 py-0.5 text-2xs font-medium text-brand">
                {selectedStage.dataset}
              </span>
            </div>
            {onSelectStepToRerun && (
              <button
                onClick={() => onSelectStepToRerun(selectedStage.id)}
                className="rounded-lg bg-brand px-3 py-1 text-xs font-medium text-white hover:bg-brand-hover shadow-xs"
              >
                从该步骤断点续跑 ➔
              </button>
            )}
          </div>

          <div className="mt-3 grid grid-cols-1 gap-4 text-xs sm:grid-cols-3">
            <div>
              <div className="text-2xs text-ink-muted">步骤功能说明</div>
              <div className="mt-1 text-ink">{selectedStage.desc ?? '无额外说明'}</div>
            </div>
            <div>
              <div className="text-2xs text-ink-muted">直接依赖的上游节点 (Upstream)</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {upstreamSteps.length > 0 ? (
                  upstreamSteps.map((u) => (
                    <span
                      key={u.id}
                      className="rounded bg-surface-base px-2 py-0.5 font-mono text-2xs text-ink border border-border-subtle"
                    >
                      {u.name} ({u.id})
                    </span>
                  ))
                ) : (
                  <span className="text-ink-muted">无前置依赖（根节点）</span>
                )}
              </div>
            </div>
            <div>
              <div className="text-2xs text-ink-muted">下游直接触发的节点 (Downstream)</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {downstreamSteps.length > 0 ? (
                  downstreamSteps.map((d) => (
                    <span
                      key={d.id}
                      className="rounded bg-surface-base px-2 py-0.5 font-mono text-2xs text-ink border border-border-subtle"
                    >
                      {d.name} ({d.id})
                    </span>
                  ))
                ) : (
                  <span className="text-ink-muted">无下游依赖（叶子节点）</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
