import { useEffect, useState } from 'react';
import { datacenterApi } from '@/api/datacenter';
import { Modal, PanelEmpty } from '@/components/ui';
import type { DatasetPreviewResult } from '@/types/datacenter';

interface Props {
  dataset: string | null;
  datasetLabel: string;
  open: boolean;
  onClose: () => void;
}

function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}

export default function DatasetSchemaModal({ dataset, datasetLabel, open, onClose }: Props) {
  const [data, setData] = useState<DatasetPreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'schema' | 'sample'>('schema');

  useEffect(() => {
    if (!open || !dataset) {
      setData(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    datacenterApi.datasetPreview(dataset, 10)
      .then((res) => {
        if (!cancelled) {
          setData(res);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载 Schema 字典失败');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, dataset]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`数据字典与截面采样 — ${datasetLabel}`}
      sub={dataset ? `物理表: ${data?.table ?? dataset} · 存储格式: ${dataset.includes('report') ? 'SQLite' : 'Parquet 分区'}` : undefined}
      footer={(
        <div className="flex items-center justify-between w-full">
          <span className="text-2xs text-ink-muted">
            {data ? `落库总行数: ${fmtInt(data.total_rows)} 行 · 抽样展示最新 10 条记录` : ''}
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
        {/* 顶部二级 Tab 切换 */}
        <div className="flex items-center justify-between border-b border-hair pb-2">
          <div className="flex gap-2">
            <button
              onClick={() => setActiveTab('schema')}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                activeTab === 'schema'
                  ? 'bg-brand-500 text-white'
                  : 'bg-surface-alt text-ink-secondary hover:text-ink'
              }`}
            >
              📋 Schema 字段字典 ({data?.schema?.length ?? 0})
            </button>
            <button
              onClick={() => setActiveTab('sample')}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                activeTab === 'sample'
                  ? 'bg-brand-500 text-white'
                  : 'bg-surface-alt text-ink-secondary hover:text-ink'
              }`}
            >
              🔬 真实截面样本预览 ({data?.records?.length ?? 0})
            </button>
          </div>

          {data && (
            <div className="flex items-center gap-2 text-2xs text-ink-muted">
              <span>字段量: {data.schema.length}</span>
              <span>·</span>
              <span>样本容量: {data.records.length} 行</span>
            </div>
          )}
        </div>

        {loading ? (
          <div className="flex min-h-[220px] items-center justify-center text-xs text-ink-muted">
            <span className="animate-pulse">正在穿透底层读取 Parquet / SQLite Schema 与样本数据…</span>
          </div>
        ) : error ? (
          <div className="rounded-md border border-danger/30 bg-danger-bg p-4 text-xs text-danger">
            <div className="font-semibold">Schema 读取异常</div>
            <div className="mt-1">{error}</div>
          </div>
        ) : !data ? (
          <PanelEmpty text="暂无 Schema 数据" minH="min-h-[180px]" />
        ) : activeTab === 'schema' ? (
          /* Schema 字段表格 */
          <div className="max-h-[380px] overflow-y-auto rounded border border-hair">
            <table className="quant-table w-full">
              <thead>
                <tr>
                  <th>字段名称 (Physical)</th>
                  <th>类型 (Dtype)</th>
                  <th>业务中文释义</th>
                  <th>物理量纲</th>
                  <th className="text-right">空值率 (Null %)</th>
                </tr>
              </thead>
              <tbody>
                {data.schema.map((f) => (
                  <tr key={f.name}>
                    <td className="font-mono text-xs font-semibold text-ink">{f.name}</td>
                    <td>
                      <span className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-3xs text-ink-secondary">
                        {f.dtype}
                      </span>
                    </td>
                    <td className="text-xs text-ink">{f.label}</td>
                    <td className="text-xs text-ink-secondary">{f.unit || '—'}</td>
                    <td className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <div className="h-1.5 w-12 overflow-hidden rounded-full bg-surface-sunken">
                          <div
                            className={`h-full rounded-full ${
                              f.null_ratio > 0.1 ? 'bg-danger' : f.null_ratio > 0 ? 'bg-warn' : 'bg-success'
                            }`}
                            style={{ width: `${Math.min(100, Math.max(0, f.null_ratio * 100))}%` }}
                          />
                        </div>
                        <span className="num font-mono text-2xs text-ink-muted">
                          {(f.null_ratio * 100).toFixed(1)}%
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          /* 样本截面数据表格 */
          <div className="max-h-[380px] overflow-x-auto overflow-y-auto rounded border border-hair">
            {data.records.length ? (
              <table className="quant-table w-full whitespace-nowrap">
                <thead>
                  <tr>
                    {Object.keys(data.records[0]).map((col) => (
                      <th key={col} className="font-mono text-2xs">{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.records.map((row, idx) => (
                    <tr key={idx}>
                      {Object.keys(data.records[0]).map((col) => {
                        const val = row[col];
                        const str = val == null ? 'NULL' : typeof val === 'number' ? val.toLocaleString('en-US') : String(val);
                        return (
                          <td
                            key={col}
                            className={`font-mono text-2xs ${
                              val == null ? 'italic text-ink-muted/50' : 'text-ink'
                            }`}
                          >
                            {str}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <PanelEmpty text="样本数据为空" minH="min-h-[180px]" />
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
