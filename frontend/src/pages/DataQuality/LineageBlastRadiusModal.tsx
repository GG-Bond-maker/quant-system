import { useEffect, useState } from 'react';
import { opsApi, type BlastRadiusResult } from '@/api/production';
import { Modal, PanelEmpty } from '@/components/ui';

interface Props {
  nodeId: string | null;
  open: boolean;
  onClose: () => void;
}

export default function LineageBlastRadiusModal({ nodeId, open, onClose }: Props) {
  const [data, setData] = useState<BlastRadiusResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !nodeId) {
      setData(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    opsApi.blastRadius(nodeId)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载受损扩散分析失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, nodeId]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`数据血缘拓扑受损扩散分析 (Blast Radius) — ${data?.source_node.name ?? nodeId}`}
      sub={nodeId ? `以 [${nodeId}] 为故障源头的下游全链路污染可达性分析` : undefined}
      footer={(
        <div className="flex w-full items-center justify-between">
          <span className="text-2xs text-ink-muted">
            {data ? `波及下游节点: ${data.total_impacted} 个 · 影响 Alpha 因子: ${data.affected_alpha_factors.length} 个` : ''}
          </span>
          <button
            onClick={onClose}
            className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 transition-colors"
          >
            关闭
          </button>
        </div>
      )}
    >
      <div className="space-y-4">
        {loading ? (
          <div className="flex min-h-[200px] items-center justify-center text-xs text-ink-muted">
            <span className="animate-pulse">遍历数据血缘 DAG 拓扑计算下游受损扩散面…</span>
          </div>
        ) : error ? (
          <div className="rounded border border-danger/30 bg-danger-bg p-3 text-xs text-danger">{error}</div>
        ) : !data ? (
          <PanelEmpty text="暂无扩散分析数据" minH="min-h-[160px]" />
        ) : (
          <div className="space-y-3">
            {/* 概述与处置建议卡片 */}
            <div className="rounded-md border border-warn/30 bg-warn-bg/30 p-3">
              <div className="flex items-center justify-between text-xs font-semibold text-ink">
                <span>⚠️ 下游链条扩散总述 (Blast Radius Summary)</span>
                <span className="rounded bg-warn-bg px-1.5 py-0.5 text-3xs font-bold text-warn">
                  波及 {data.total_impacted} 个阶段
                </span>
              </div>
              <p className="mt-1 text-2xs text-ink-secondary leading-relaxed">{data.blast_summary}</p>
              <div className="mt-2 rounded bg-surface p-2 text-2xs text-ink">
                <span className="font-semibold text-brand-600">🛡️ 处置建议：</span>
                {data.remediation_advice}
              </div>
            </div>

            {/* 受波及的因子与模型 */}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                <div className="text-2xs font-semibold text-ink">受波及的 Alpha 因子特征库</div>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {data.affected_alpha_factors.length ? (
                    data.affected_alpha_factors.map((f) => (
                      <span key={f} className="rounded bg-brand-50 px-1.5 py-0.5 font-mono text-3xs text-brand-600">
                        {f}
                      </span>
                    ))
                  ) : (
                    <span className="text-3xs text-ink-muted">无受波及因子</span>
                  )}
                </div>
              </div>

              <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                <div className="text-2xs font-semibold text-ink">受波及的预测模型与生产阶段</div>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {data.affected_models.length ? (
                    data.affected_models.map((m) => (
                      <span key={m} className="rounded bg-danger-bg px-1.5 py-0.5 text-3xs text-danger font-medium">
                        {m}
                      </span>
                    ))
                  ) : (
                    <span className="text-3xs text-ink-muted">无直接受波及模型</span>
                  )}
                </div>
              </div>
            </div>

            {/* 受损节点层级拓扑表 */}
            <div className="space-y-1.5">
              <div className="text-xs font-semibold text-ink">下游受损节点层级清单</div>
              <div className="max-h-[220px] overflow-y-auto rounded border border-hair">
                <table className="quant-table w-full">
                  <thead>
                    <tr>
                      <th>拓扑距离</th>
                      <th>节点名称</th>
                      <th>分组</th>
                      <th>严重级</th>
                      <th>影响简述</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.impacted_nodes.map((n) => (
                      <tr key={n.id}>
                        <td className="font-mono text-2xs font-semibold">+{n.distance} 阶</td>
                        <td className="text-xs font-medium text-ink">{n.name}</td>
                        <td>
                          <span className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-3xs text-ink-secondary">
                            {n.group}
                          </span>
                        </td>
                        <td>
                          <span
                            className={`rounded px-1.5 py-0.5 text-3xs font-semibold ${
                              n.severity_impact === 'CRITICAL'
                                ? 'bg-danger-bg text-danger'
                                : n.severity_impact === 'HIGH'
                                ? 'bg-warn-bg text-warn'
                                : 'bg-surface-sunken text-ink-muted'
                            }`}
                          >
                            {n.severity_impact}
                          </span>
                        </td>
                        <td className="text-2xs text-ink-muted truncate max-w-xs">{n.desc || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
