/** 数据管线与任务调度看板：真实产物新鲜度 DAG + 流水线重跑。 */
import { useCallback, useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { opsApi, type DagStatus } from '@/api/production';
import { PageHeader, SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';

/** C-7：后端 /ops/dag 只返回 5 个 stage（harvest/qc/features/infer/screener），
 *  没有 desk 节点——desk 是用户在执行中心提交母单触发，已在下方以虚线卡单独说明。
 *  原 STAGE_ORDER 含 'desk' ⇒ DAG 图恒多一个琥珀假节点。 */
const STAGE_ORDER = ['harvest', 'qc', 'features', 'infer', 'screener'];

/** C-8：只有 FAILED 是失败；PENDING/RUNNING 在跑，不能染红 */
const JOB_STATUS_TONE: Record<string, string> = {
  SUCCESS: 'text-success',
  FAILED: 'text-danger',
};

/**
 * DAG 节点图（同屏冗余审计 PL-1：**合并了原先重复的第二块**）。
 *
 * 修复前，同一份 `dag.stages` 被完整渲染两遍：本图（name/dataset/done_date + 状态配色），
 * 以及下方一组 5 张 stage 卡（**字段集完全相同**，唯一差别是把状态写成了
 * 「已就绪/待更新」文字）。即第二块除那句文字外没有任何新信息。
 *
 * 现把状态文字并入节点，并保留节点图的拓扑箭头（`stages` 之间的先后关系是
 * 扁平卡片给不出的信息），第二块整体删除。
 */
function DagGraph({ dag }: { dag: DagStatus }) {
  return (
    <div className="flex flex-wrap items-stretch gap-1">
      {STAGE_ORDER.map((id, i) => {
        const st = dag.stages.find((s) => s.id === id);
        const name = st?.name ?? id;
        const ok = st?.status === 'ok';
        return (
          <div key={id} className="flex items-stretch gap-1">
            <div className={`rounded-lg border px-3 py-2 text-center text-2xs ${
              ok ? 'border-success/30 bg-success-bg text-success'
                 : 'border-warn/30 bg-warn-bg text-warn'}`}>
              <div className="font-semibold">{name}</div>
              <div className="text-ink-secondary">{st?.dataset ?? id}</div>
              <div className="num">产物日期：{st?.done_date ?? '—'}</div>
              {/* 状态文字（原第二块唯一多出的信息） */}
              <div className="mt-0.5 font-medium">{ok ? '已就绪' : '待更新'}</div>
            </div>
            {i < STAGE_ORDER.length - 1 && (
              <span className="self-center text-ink-muted">➔</span>
            )}
          </div>
        );
      })}
      {/*
       * 调仓单：**刻意留在 DAG 之后、并以虚线区分** —— 它不是 /ops/dag 返回的
       * stage（C-7 注释已说明：desk 由用户在执行中心提交母单触发），
       * 故不能画进拓扑箭头链里冒充流水线的一环。
       */}
      <div className="flex items-stretch gap-1">
        <span className="self-center text-ink-muted">➔</span>
        <div className="rounded-lg border border-dashed border-hair bg-surface px-3 py-2 text-center text-2xs">
          <div className="font-medium">调仓单（执行中心）</div>
          <div className="text-ink-muted">由用户提交母单触发</div>
          <div className="num text-ink-secondary">基准交易日：{dag.benchmark_date ?? '—'}</div>
        </div>
      </div>
    </div>
  );
}

function GanttTimelineView({ timeline }: { timeline?: DagStatus['gantt_timeline'] }) {
  if (!timeline || !timeline.length) return null;
  const totalDuration = timeline.reduce((acc, t) => acc + t.duration_s, 0);

  return (
    <div className="space-y-2 rounded-lg border border-hair bg-surface p-3">
      <div className="flex items-center justify-between text-xs font-semibold text-ink">
        <div className="flex items-center gap-2">
          <span>关键路径甘特时序 (Critical Path Timeline · CPM)</span>
          <span className="rounded bg-danger-bg px-1.5 py-0.5 text-2xs text-danger font-normal">
            关键路径无浮动 (Slack=0)
          </span>
        </div>
        <span className="num text-2xs font-normal text-ink-secondary">
          全流程完工时间：{totalDuration}s
        </span>
      </div>

      <div className="space-y-1.5 pt-1">
        {timeline.map((item) => {
          const widthPct = Math.max(8, (item.duration_s / totalDuration) * 100);
          const leftPct = (item.offset_s / totalDuration) * 100;

          return (
            <div key={item.id} className="grid grid-cols-[5.5rem_1fr_4rem] items-center gap-2 text-2xs">
              <span className="truncate font-medium text-ink">{item.name}</span>
              <div className="relative h-5 w-full rounded bg-surface-alt">
                <div
                  className={`absolute top-0.5 bottom-0.5 rounded px-1.5 flex items-center justify-between transition-all ${
                    item.is_critical_path
                      ? 'border border-danger/40 bg-brand-500/80 text-white'
                      : 'border border-hair bg-surface text-ink-secondary'
                  }`}
                  style={{
                    left: `${leftPct}%`,
                    width: `${widthPct}%`,
                  }}
                >
                  <span className="truncate font-mono text-2xs font-semibold">{item.duration_s}s</span>
                </div>
              </div>
              <span className="text-right num text-ink-secondary">+{item.offset_s}s</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ShardingMetricsView({ metrics }: { metrics?: DagStatus['sharding_metrics'] }) {
  if (!metrics) return null;

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div className="rounded-lg border border-hair bg-surface px-3 py-2">
        <div className="text-2xs text-ink-secondary">Map-Reduce 分片总数</div>
        <div className="num mt-0.5 text-base font-semibold text-ink">{metrics.total_shards} 组</div>
        <div className="text-2xs text-ink-muted">行业与标的分片并行</div>
      </div>
      <div className="rounded-lg border border-hair bg-surface px-3 py-2">
        <div className="text-2xs text-ink-secondary">健康分片数</div>
        <div className="num mt-0.5 text-base font-semibold text-success">{metrics.healthy_shards} 组</div>
        <div className="text-2xs text-ink-muted">正常完成特征构建</div>
      </div>
      <div className="rounded-lg border border-hair bg-surface px-3 py-2">
        <div className="text-2xs text-ink-secondary">故障隔离区 (Quarantine)</div>
        <div className="num mt-0.5 text-base font-semibold text-ink">{metrics.quarantined_shards} 个</div>
        <div className="text-2xs text-ink-muted">脏数据隔离不阻塞全网</div>
      </div>
      <div className="rounded-lg border border-hair bg-surface px-3 py-2">
        <div className="text-2xs text-ink-secondary">断点恢复机制</div>
        <div className="mt-0.5 text-base font-semibold text-brand-600">
          {metrics.checkpoint_resumption_enabled ? '已就绪' : '关闭'}
        </div>
        <div className="text-2xs text-ink-muted">支持单 Step 失败幂等续跑</div>
      </div>
    </div>
  );
}

export default function Pipeline() {
  const [dag, setDag] = useState<DagStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // P2-4：/ops/dag 冷路径可能超 15s（60s 兜底），/ops/dag/rerun 真实跑流水线 300s。
  // 卸载（切路由）时中断在途请求，释放浏览器并发连接；两条请求互不相关，
  // 各用一个 hook 实例，避免重跑时把 DAG 刷新请求一起掐掉。
  const dagTask = useAbortableTask();
  const rerunTask = useAbortableTask();

  const load = useCallback(async () => {
    const ctrl = dagTask.begin();
    try {
      const d = await opsApi.dag({ signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setDag(d);
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setErr(e instanceof ApiError ? e.message : '加载失败');
    }
  }, [dagTask]);

  useEffect(() => { void load(); }, [load]);

  const rerun = useCallback(async (tradeDate: string) => {
    const ctrl = rerunTask.begin();
    setBusy(true); setMsg(null); setErr(null);
    try {
      const r = await opsApi.dagRerun({ trade_date: tradeDate }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setMsg(r.ok ? `流水线执行完成：${r.summary ?? ''}` : `执行失败：${r.error}`);
      await load();
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '重跑失败');
    } finally {
      if (rerunTask.finish(ctrl)) setBusy(false);
    }
  }, [load, rerunTask]);

  return (
    <div className="space-y-3">
      <PageHeader title="数据管线与任务调度看板" />
      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}
      {msg && <div className="rounded-md bg-brand-50 px-3 py-2 text-xs text-brand-700">{msg}</div>}

      <SectionCard title="每日收盘后 DAG（节点状态 = 数据产物真实新鲜度）" bodyClassName="space-y-3">
        {dag ? (
          <>
            <DagGraph dag={dag} />
            <GanttTimelineView timeline={dag.gantt_timeline} />
            <ShardingMetricsView metrics={dag.sharding_metrics} />
            <p className="text-2xs text-ink-muted">{dag.note}</p>
          </>
        ) : <div className="py-10 text-center text-xs text-ink-muted">加载 DAG…</div>}
      </SectionCard>

      <SectionCard title="任务重跑（真实流水线 run_pipeline，幂等）" bodyClassName="space-y-2">
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

      <SectionCard title="最近流水线任务（data_jobs 真实记录）">
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
                  <td className={`text-center ${JOB_STATUS_TONE[j.status] ?? 'text-warn'}`}>{j.status}</td>
                  <td className="text-center">{j.current_step ?? '—'}</td>
                  {/* C-8：duration_ms 为 NULL 时不得渲染成 "0.0s" */}
                  <td className="num text-center">
                    {j.duration_ms == null ? '—' : `${(j.duration_ms / 1000).toFixed(1)}s`}
                  </td>
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
