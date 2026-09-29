/**
 * 因子健康度卡（前沿演进维度五）：滚动 RankIC / 半衰期 / PSI 漂移 / 状态机。
 *
 * 数据来自 /monitor/health（晚间例行或手动 run 产生的快照，只读渲染）；
 * 「立即运行」触发真实计算（researcher 权限，未授权展示信封错误）。
 */
import { useCallback, useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { monitorApi, type MonitorSnapshot } from '@/api/monitor';
import { SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';

const STATE_STYLE: Record<string, {badge: string; label: string}> = {
  healthy: { badge: 'bg-emerald-50 text-emerald-700', label: '健康' },
  watch: { badge: 'bg-amber-50 text-amber-700', label: '观察' },
  degraded: { badge: 'bg-red-50 text-red-600', label: '降级' },
  unknown: { badge: 'bg-slate-100 text-ink-muted', label: '数据不足' },
};

export default function FactorHealthCard() {
  const [snap, setSnap] = useState<MonitorSnapshot | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // P2-4：/monitor/run 真实计算 120s，卸载时中断在途请求
  const runTask = useAbortableTask();

  const load = useCallback(async () => {
    try { setSnap(await monitorApi.health()); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '健康度加载失败'); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const run = async () => {
    const ctrl = runTask.begin();
    setRunning(true); setErr(null);
    try {
      const r = await monitorApi.run({ signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setSnap(r);
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setErr(e instanceof ApiError ? e.message : '监控运行失败');
    } finally {
      if (runTask.finish(ctrl)) setRunning(false);
    }
  };

  const st = STATE_STYLE[snap?.state ?? 'unknown'] ?? STATE_STYLE.unknown;
  const r = snap?.recent;
  const psi = snap?.psi;
  const ks = snap?.ks;

  return (
    <SectionCard title="因子健康度（生产信号监控）" bodyClassName="p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${st.badge}`}>
          {st.label}
        </span>
        <span className="text-2xs text-ink-muted">
          {snap?.computed_at ? `快照 @ ${snap.computed_at.replace('T', ' ')}`
            : '尚未运行（晚间例行 17:30 自动执行）'}
        </span>
        <button onClick={() => void run()} disabled={running}
          className="ml-auto rounded-md border border-hair px-2.5 py-1 text-2xs text-ink-secondary
            hover:border-brand-200 hover:text-brand-600 disabled:opacity-60">
          {running ? '计算中…' : '立即运行'}
        </button>
      </div>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}
      {snap?.note && !snap.computed_at && (
        <p className="text-2xs text-ink-muted">{snap.note}</p>
      )}
      {snap?.computed_at && (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {[
              { label: '近 15 日 Mean RankIC',
                value: r?.mean_ic != null ? r.mean_ic.toFixed(4) : '—' },
              { label: 'ICIR', value: r?.icir != null ? r.icir.toFixed(2) : '—' },
              { label: 'Alpha 半衰期（日）',
                value: snap.half_life?.value != null ? String(snap.half_life.value) : '—' },
              { label: 'PSI max（截面标准化·判定口径）',
                value: psi?.ok ? (psi.max ?? '—').toString() : '—' },
            ].map((k) => (
              <div key={k.label} className="rounded-md border border-hair bg-white px-2.5 py-2">
                <div className="text-2xs text-ink-muted">{k.label}</div>
                <div className="num text-sm font-semibold text-ink">{k.value}</div>
              </div>
            ))}
          </div>
          <p className="text-2xs leading-relaxed text-ink-muted">
            IC 状态 {STATE_STYLE[snap.ic_state ?? 'unknown']?.label ?? '—'} ·
            漂移状态 {STATE_STYLE[snap.drift_state ?? 'unknown']?.label ?? '—'}
            {snap.half_life?.note ? ` · ${snap.half_life.note}` : ''}
            {psi?.ok && psi.top && psi.top.length > 0
              ? ` · 形状漂移最大：${psi.top[0].factor}（${psi.top[0].psi}）`
              : ''}
            {psi?.ok && psi.raw?.max != null
              ? ` · 池化原始 PSI max ${psi.raw.max}（含水平/尺度平移，仅披露、不触发降级）`
              : ''}
            {ks?.ok
              ? ` · KS 同口径互验 ${ks.max}（超限比 ${((ks.over_crit_ratio ?? 0) * 100).toFixed(0)}%，单日临界尺度 ${ks.crit_effective ?? '—'}；池化样本量大 ⇒ 只能当强度读）`
              : ''}
            。降级表示近期 RankIC 反向或超出历史 σ（按当日股票池宽度折算）
            / 截面标准化 PSI 超标——建议降权观察；PSI 超标会自动触发重训（promote 门禁把关）。
          </p>
        </>
      )}
    </SectionCard>
  );
}
