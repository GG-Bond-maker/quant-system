/** 数据质量与血缘监控中心：真实 QC 扫描 + 代码级血缘图谱。 */
import { useCallback, useEffect, useState } from 'react';
import * as echarts from '@/lib/echarts';

import { ApiError } from '@/api/client';
import { opsApi, type LineageGraph, type QualityScanResult } from '@/api/production';
import FactorHealthCard from '@/components/FactorHealthCard';
import { SectionCard, ErrorState } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useChart } from '@/utils/useChart';

const GROUP_COLOR: Record<string, string> = {
  source: '#94A3B8', raw: '#2563EB', process: '#F59E0B',
  feature: '#7C3AED', model: '#0D9488', prod: '#DC2626',
};

type LineageNode = LineageGraph['nodes'][number];

/**
 * 拼接节点的真实状态（全部来自后端实扫）。
 * 无产物节点显式写"尚无本地产物"，而不是留空让人以为它正常。
 */
function nodeStats(n: LineageNode): string {
  const p: string[] = [];
  if (n.rows != null) p.push(`${n.rows.toLocaleString('en-US')} 行`);
  if (n.size_mb != null) p.push(`${n.size_mb} MB`);
  if (n.latest_date) p.push(`最新 ${n.latest_date}`);
  if (n.records != null) p.push(`${n.records} 条记录`);
  if (n.version) p.push(`版本 ${n.version}`);
  if (n.test_rank_ic != null) p.push(`test RankIC ${n.test_rank_ic.toFixed(4)}`);
  if (n.registered != null) p.push(`已注册 ${n.registered} 个模型`);
  if (n.quarantined_partitions != null) p.push(`隔离 ${n.quarantined_partitions} 个分区`);
  if (p.length) return p.join(' · ');
  return n.status === 'missing' ? '尚无本地产物' : '—';
}

function LineageGraphView({ graph }: { graph: LineageGraph }) {
  const option = {
    tooltip: {
      formatter: (p: { data?: { desc?: string; name?: string; stats?: string;
                                source?: string; target?: string } }) => {
        const d = p.data;
        if (!d) return '';
        if (d.target) return `${d.source} → ${d.target}`;
        const head = `<b>${d.name ?? ''}</b>`;
        const desc = d.desc ? `<br/>${d.desc}` : '';
        const stats = d.stats ? `<br/><span style="color:#64748B">${d.stats}</span>` : '';
        return head + desc + stats;
      },
    },
    series: [{
      type: 'graph', layout: 'force', roam: true,
      force: { repulsion: 320, edgeLength: 90 },
      label: { show: true, fontSize: 10 },
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 7,
      lineStyle: { color: '#CBD5E1', width: 1.4 },
      itemStyle: { borderColor: '#fff', borderWidth: 1.5 },
      data: graph.nodes.map((n) => ({
        id: n.id, name: n.name, desc: n.desc, stats: nodeStats(n),
        symbolSize: n.group === 'process' || n.group === 'prod' ? 46 : 38,
        // 无产物的节点置灰，避免"图上有节点"被误读成"这条链路已就绪"
        itemStyle: {
          color: n.status === 'missing' ? '#CBD5E1'
            : (GROUP_COLOR[n.group] ?? '#64748B'),
          borderColor: n.status === 'missing' ? '#94A3B8' : '#fff',
          borderWidth: 1.5,
        },
      })),
      links: graph.edges.map((e) => ({ source: e.source, target: e.target })),
    }],
  } as echarts.EChartsOption;
  const ref = useChart(option);
  return (
    <div>
      <div ref={ref} className="h-80 w-full" />
      <div className="flex flex-wrap gap-2 text-2xs text-ink-secondary">
        {Object.entries(GROUP_COLOR).map(([g, c]) => (
          <span key={g} className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: c }} />
            {g}
          </span>
        ))}
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: '#CBD5E1' }} />
          尚无产物
        </span>
      </div>
      {graph.degraded_note && (
        <p className="mt-1 text-2xs text-amber-600">⚠ {graph.degraded_note}</p>
      )}
      <p className="mt-1 text-2xs text-ink-muted">
        {graph.node_states_scanned
          ? '拓扑为代码静态依赖，节点状态为实时扫描'
          : ''}
        {graph.benchmark_date ? ` · 最新数据 ${graph.benchmark_date}` : ''}
        {graph.generated_at ? ` · 扫描于 ${graph.generated_at.replace('T', ' ').replace('+00:00', ' UTC')}` : ''}
      </p>
      <p className="mt-0.5 text-2xs text-ink-muted">{graph.note}</p>
    </div>
  );
}

export default function DataQuality() {
  const [scan, setScan] = useState<QualityScanResult | null>(null);
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // P2-4：QC 扫描 180s / 血缘冷路径约 33s，卸载（切路由）时中断在途请求，
  // 释放浏览器并发连接；两条请求互不相关，各用一个 hook 实例避免互相中断。
  const scanTask = useAbortableTask();
  const lineageTask = useAbortableTask();

  const runScan = useCallback(async () => {
    const ctrl = scanTask.begin();
    setScanning(true); setErr(null);
    try {
      const r = await opsApi.qualityScan({ dataset: 'daily_bar' }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setScan(r);
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setErr(e instanceof ApiError ? e.message : '扫描失败');
    } finally {
      if (scanTask.finish(ctrl)) setScanning(false);
    }
  }, [scanTask]);

  /** 血缘图三态：加载中 / 失败（可重试）/ 成功。失败不得再落回"加载中"分支。 */
  const loadLineage = useCallback(async () => {
    const ctrl = lineageTask.begin();
    setGraphLoading(true); setGraphError(null);
    try {
      const r = await opsApi.lineage({ signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setGraph(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setGraph(null);
      setGraphError(e instanceof ApiError ? e.message : '血缘图谱加载失败');
    } finally {
      if (lineageTask.finish(ctrl)) setGraphLoading(false);
    }
  }, [lineageTask]);

  useEffect(() => {
    void loadLineage();
    void runScan();
  }, [loadLineage, runScan]);

  // C-6：0 覆盖（0 只 / 0 行）时"未检出问题"是伪清白——本次根本没看到数据，
  // 必须与"已覆盖且干净"区分开；同理截断扫描只能声明"窗口内未检出"。
  const noCoverage = !!scan && (scan.symbols_scanned === 0 || scan.rows_scanned === 0);
  const partialCoverage = !!scan && !noCoverage && scan.truncated;
  const clearClaim = noCoverage
    ? null
    : partialCoverage
      ? `已扫描的 ${scan!.symbols_scanned}/${scan!.symbols_available} 只内未检出质量问题（窗口外未覆盖）`
      : '未检出任何质量问题（阈值口径见 data/quality.py）';

  return (
    <div className="space-y-3">
      {/* 因子健康度（维度五）：滚动 RankIC / 半衰期 / PSI 漂移 / 状态机 */}
      <FactorHealthCard />
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-ink">数据质量与血缘监控中心</h1>
        <button onClick={() => void runScan()} disabled={scanning}
                className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
          {scanning ? '扫描中…' : '重新扫描 daily_bar'}
        </button>
      </div>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}

      {scan && (
        <div className="grid grid-cols-4 gap-2">
          <div className="rounded-lg border border-hair bg-white px-3 py-2">
            <div className="text-2xs text-ink-secondary">扫描范围{noCoverage ? '（空）' : ''}</div>
            <div className={`num text-base font-semibold ${noCoverage ? 'text-amber-600' : ''}`}>
              {scan.symbols_scanned}{scan.truncated ? ` / ${scan.symbols_available}` : ''} 只
            </div>
            <div className="text-2xs text-ink-muted">
              {scan.dataset} · {scan.year} · {scan.rows_scanned.toLocaleString()} 行
              {scan.truncated ? ` · 已达扫描上限 ${scan.scan_limit} 只` : ''}
            </div>
            {noCoverage && (
              <div className="mt-0.5 text-2xs text-amber-600">
                本次未覆盖任何标的/行，不能据此判定数据健康
              </div>
            )}
          </div>
          <div className="rounded-lg border border-hair bg-white px-3 py-2">
            <div className="text-2xs text-ink-secondary">问题总数</div>
            <div className="num text-base font-semibold">{scan.n_issues}</div>
          </div>
          <div className="rounded-lg border border-hair bg-white px-3 py-2">
            <div className="text-2xs text-ink-secondary">其中 error 级</div>
            <div className="num text-base font-semibold text-red-600">{scan.n_errors}</div>
          </div>
          <div className="rounded-lg border border-hair bg-white px-3 py-2">
            <div className="text-2xs text-ink-secondary">检查项分布</div>
            <div className="mt-0.5 flex flex-wrap gap-1">
              {Object.entries(scan.by_kind).slice(0, 4).map(([k, v]) => (
                <span key={k} className="rounded bg-slate-100 px-1 font-mono text-2xs">{k}:{v}</span>
              ))}
              {!Object.keys(scan.by_kind).length &&
                (noCoverage
                  ? <span className="text-2xs text-amber-600">未覆盖数据，不能判定通过</span>
                  : <span className="text-2xs text-emerald-600">
                      {partialCoverage ? '已覆盖窗口内全部通过 ✓' : '全部通过 ✓'}
                    </span>)}
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <SectionCard title="异常告警明细（QC 引擎真实检查项）" bodyClassName="p-3">
          {scan ? (
            <div className="max-h-72 overflow-auto">
              <table className="w-full text-2xs">
                <thead className="sticky top-0 bg-white">
                  <tr className="text-ink-secondary">
                    <th className="text-left font-medium">标的</th>
                    <th className="text-left font-medium">日期</th>
                    <th className="font-medium">检查项</th>
                    <th className="font-medium">级别</th>
                    <th className="text-left font-medium">明细</th>
                  </tr>
                </thead>
                <tbody>
                  {scan.samples.map((s, i) => (
                    <tr key={i} className="border-t border-hair">
                      <td className="py-1 font-mono">{s.symbol}</td>
                      <td className="num">{s.date ?? '—'}</td>
                      <td className="text-center font-mono">{s.kind}</td>
                      <td className={`text-center ${s.severity === 'error' ? 'font-semibold text-red-600' : 'text-amber-600'}`}>
                        {s.severity}</td>
                      <td className="max-w-56 truncate text-ink-secondary" title={s.detail}>{s.detail}</td>
                    </tr>
                  ))}
                  {!scan.samples.length && (
                    <tr><td colSpan={5}
                      className={`py-6 text-center ${clearClaim ? 'text-emerald-600' : 'text-amber-600'}`}>
                      {clearClaim ?? '本次扫描未覆盖任何数据（0 只 / 0 行），不能判定"无质量问题"'}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          ) : <div className="py-10 text-center text-xs text-ink-muted">扫描中…</div>}
        </SectionCard>

        <SectionCard title="数据血缘图谱（原始数据 ➔ 清洗 ➔ 特征 ➔ 模型 ➔ 生产）" bodyClassName="p-3">
          {graphLoading
            ? <div className="py-10 text-center text-xs text-ink-muted">加载血缘…</div>
            : graph
              ? <LineageGraphView graph={graph} />
              : <ErrorState message={graphError ?? '血缘图谱加载失败'} onRetry={() => void loadLineage()} />}
        </SectionCard>
      </div>
    </div>
  );
}
