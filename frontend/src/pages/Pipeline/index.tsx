/** 数据管线与任务调度看板：真实产物新鲜度 DAG + 流水线重跑。 */
import { useCallback, useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { opsApi, type DagStatus } from '@/api/production';
import { SectionCard } from '@/components/ui';

const STAGE_ORDER = ['harvest', 'qc', 'features', 'infer', 'screener', 'desk'];

function DagGraph({ dag }: { dag: DagStatus }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {STAGE_ORDER.map((id, i) => {
        const st = dag.stages.find((s) => s.id === id);
        const name = st?.name ?? id;
        const ok = st?.status === 'ok';
        return (
          <div key={id} className="flex items-center gap-1">
            <div className={`rounded-lg border px-3 py-2 text-center text-2xs ${
              ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                 : 'border-amber-200 bg-amber-50 text-amber-800'}`}>
              <div className="font-semibold">{name}</div>
              <div className="text-ink-secondary">{st?.dataset ?? id}</div>
              <div className="num">{st?.done_date ?? '—'}</div>
            </div>
            {i < STAGE_ORDER.length - 1 && (
              <span className="text-ink-muted">➔</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function Pipeline() {
  const [dag, setDag] = useState<DagStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setDag(await opsApi.dag()); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '加载失败'); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const rerun = useCallback(async (tradeDate: string) => {
    setBusy(true); setMsg(null); setErr(null);
    try {
      const r = await opsApi.dagRerun({ trade_date: tradeDate });
      setMsg(r.ok ? `流水线执行完成：${r.summary ?? ''}` : `执行失败：${r.error}`);
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '重跑失败');
    } finally { setBusy(false); }
  }, [load]);

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold text-ink">数据管线与任务调度看板</h1>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}
      {msg && <div className="rounded-md bg-brand-50 px-3 py-2 text-xs text-brand-700">{msg}</div>}

      <SectionCard title="每日收盘后 DAG（节点状态 = 数据产物真实新鲜度）" bodyClassName="p-3 space-y-2">
        {dag ? (
          <>
            <DagGraph dag={dag} />
            <div className="grid grid-cols-5 gap-2 text-2xs">
              {dag.stages.map((s) => (
                <div key={s.id} className="rounded-lg border border-hair bg-white px-2 py-2">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{s.name}</span>
                    <span className={`rounded px-1 py-0.5 ${s.status === 'ok'
                      ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                      {s.status === 'ok' ? '已就绪' : '待更新'}
                    </span>
                  </div>
                  <div className="mt-1 text-ink-muted">{s.dataset}</div>
                  <div className="num text-ink-secondary">产物日期：{s.done_date ?? '—'}</div>
                </div>
              ))}
              <div className="rounded-lg border border-dashed border-hair bg-white px-2 py-2">
                <div className="font-medium">调仓单（执行中心）</div>
                <div className="mt-1 text-ink-muted">由用户在执行中心提交母单触发</div>
                <div className="text-2xs text-ink-muted">基准交易日：{dag.benchmark_date ?? '—'}</div>
              </div>
            </div>
            <p className="text-2xs text-ink-muted">{dag.note}</p>
          </>
        ) : <div className="py-10 text-center text-xs text-ink-muted">加载 DAG…</div>}
      </SectionCard>

      <SectionCard title="任务重跑（真实流水线 run_pipeline，幂等）" bodyClassName="p-3 space-y-2">
        <div className="flex items-center gap-2 text-2xs">
          <input id="rerun-date" type="date" defaultValue={dag?.benchmark_date ?? ''}
                 className="rounded-md border border-hair px-2 py-1.5 text-xs" />
          <button disabled={busy}
                  onClick={() => {
                    const el = document.getElementById('rerun-date') as HTMLInputElement | null;
                    if (el?.value) void rerun(el.value);
                  }}
                  className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
            {busy ? '流水线运行中…' : '重跑该交易日流水线'}
          </button>
          <span className="text-ink-muted">
            收割 → 清洗校验 → 特征构建 → 模型推理 → 选股快照（fail-fast，SUCCESS 幂等不重复执行）
          </span>
        </div>
      </SectionCard>

      <SectionCard title="最近流水线任务（data_jobs 真实记录）" bodyClassName="p-3">
        {dag?.recent_jobs?.length ? (
          <table className="w-full text-2xs">
            <thead>
              <tr className="text-ink-secondary">
                <th className="text-left font-medium">任务</th>
                <th className="font-medium">交易日</th>
                <th className="font-medium">状态</th>
                <th className="font-medium">步骤</th>
                <th className="font-medium">耗时</th>
                <th className="text-left font-medium">错误</th>
              </tr>
            </thead>
            <tbody>
              {dag.recent_jobs.map((j, i) => (
                <tr key={i} className="border-t border-hair">
                  <td className="py-1 font-mono">{j.job_type}</td>
                  <td className="num text-center">{j.trade_date}</td>
                  <td className={`text-center ${j.status === 'SUCCESS'
                    ? 'text-emerald-600' : 'text-red-600'}`}>{j.status}</td>
                  <td className="text-center">{j.current_step ?? '—'}</td>
                  <td className="num text-center">{(j.duration_ms / 1000).toFixed(1)}s</td>
                  <td className="max-w-72 truncate text-ink-muted" title={j.error_message ?? ''}>
                    {j.error_message ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="py-6 text-center text-xs text-ink-muted">暂无任务记录</div>}
      </SectionCard>
    </div>
  );
}
