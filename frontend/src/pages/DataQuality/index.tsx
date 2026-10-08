/** 数据质量与血缘监控中心：真实 QC 扫描 + 截面热力图 + 多源对账 + 代码级血缘与受损扩散图谱。 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';

import { ApiError } from '@/api/client';
import { opsApi, type LineageGraph, type QualityScanResult } from '@/api/production';
import FactorHealthCard from '@/components/FactorHealthCard';
import { PageHeader, SectionCard, ErrorState } from '@/components/ui';
import FeatureDriftPanel from './FeatureDriftPanel';
import MultiSourceArbitragePanel from './MultiSourceArbitragePanel';
import QualityRadarCard from './QualityRadarCard';
import CrossSectionalCoverageCard from './CrossSectionalCoverageCard';
import LineageBlastRadiusModal from './LineageBlastRadiusModal';
import QualityAuditReportModal from './QualityAuditReportModal';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useChart } from '@/utils/useChart';

/*
 * 血缘图谱节点分组色（类别色，刻意不跟主题）。
 */
export const GROUP_COLOR: Record<string, string> = {
  source: CATEGORY_COLORS.SEQ[5],  // 灰（源数据）
  raw: CATEGORY_COLORS.SEQ[0],     // 蓝
  process: CATEGORY_COLORS.SEQ[2], // 橙
  feature: CATEGORY_COLORS.SEQ[3], // 紫
  model: CATEGORY_COLORS.SEQ[1],   // 青绿
  prod: CATEGORY_COLORS.SEQ[4],    // 红（生产，仅作分组标识）
};

/** 无产物节点的中性灰（未知/未就绪） */
const MISSING_NODE_COLOR = CATEGORY_COLORS.SEQ[5];

type LineageNode = LineageGraph['nodes'][number];

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

function LineageGraphView({ graph, onInspectNode }: { graph: LineageGraph; onInspectNode?: (nodeId: string) => void }) {
  const theme = useTheme();
  // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  const p = useMemo(() => chartPalette(), [theme]);
  const option = {
    tooltip: {
      formatter: (pt: { data?: { desc?: string; name?: string; stats?: string;
                                source?: string; target?: string } }) => {
        const d = pt.data;
        if (!d) return '';
        if (d.target) return `${d.source} → ${d.target}`;
        const head = `<b>${d.name ?? ''}</b>`;
        const desc = d.desc ? `<br/>${d.desc}` : '';
        const stats = d.stats ? `<br/><span style="color:${p.INK2}">${d.stats}</span>` : '';
        return head + desc + stats;
      },
    },
    series: [{
      type: 'graph', layout: 'force', roam: true,
      force: { repulsion: 320, edgeLength: 90 },
      label: { show: true, fontSize: 10, color: p.INK },
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 7,
      lineStyle: { color: p.HAIR2, width: 1.4 },
      itemStyle: { borderColor: p.CARD, borderWidth: 1.5 },
      data: graph.nodes.map((n) => ({
        id: n.id, name: n.name, desc: n.desc, stats: nodeStats(n),
        symbolSize: n.group === 'process' || n.group === 'prod' ? 46 : 38,
        itemStyle: {
          color: n.status === 'missing' ? MISSING_NODE_COLOR
            : (GROUP_COLOR[n.group] ?? MISSING_NODE_COLOR),
          borderColor: n.status === 'missing' ? p.INKM : p.CARD,
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
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: MISSING_NODE_COLOR }} />
          尚无产物
        </span>
      </div>

      {/* 节点受损扩散分析入口 */}
      {onInspectNode && (
        <div className="mt-3 rounded border border-hair bg-surface-alt p-2.5">
          <div className="mb-1.5 flex items-center justify-between text-2xs font-semibold text-ink">
            <span>🔍 点击血缘节点实时计算下游受损扩散面 (Blast Radius Analysis)：</span>
            <span className="text-3xs text-ink-muted">基于 DAG 拓扑遍历</span>
          </div>
          <div className="flex flex-wrap gap-1">
            {graph.nodes.map((n) => (
              <button
                key={n.id}
                onClick={() => onInspectNode(n.id)}
                className="rounded border border-hair bg-surface px-2 py-0.5 text-3xs text-ink hover:border-brand-200 hover:text-brand-600 transition-colors"
              >
                {n.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {graph.degraded_note && (
        <p className="mt-2 text-2xs text-warn">⚠ {graph.degraded_note}</p>
      )}
      <p className="mt-1 text-2xs text-ink-muted">
        {graph.node_states_scanned ? '拓扑为代码静态依赖，节点状态为实时扫描' : ''}
        {graph.benchmark_date ? ` · 最新数据 ${graph.benchmark_date}` : ''}
        {graph.generated_at ? ` · 扫描于 ${graph.generated_at.replace('T', ' ').replace('+00:00', ' UTC')}` : ''}
      </p>
    </div>
  );
}

const QUALITY_TABS = [
  { id: 'l1', label: '📊 基础行情合规与多源对账 (L1 Market QC)', desc: '多源量价对账、几何与涨跌幅合规、告警明细' },
  { id: 'l2', label: '🔬 特征工程与截面完整性 (L2 Feature Store)', desc: '全市场截面覆盖度热力图与特征协变量漂移' },
  { id: 'l3', label: '📈 因子生产健康与衰退监控 (L3 Alpha Health)', desc: '五维数据质量综合雷达与滚动 RankIC 监控' },
  { id: 'l4', label: '🕸️ 全链路数据血缘与影响扩散 (L4 Lineage & Blast)', desc: '血缘拓扑依赖与下游受损扩散分析 (Blast Radius)' },
] as const;

type QualityTabId = typeof QUALITY_TABS[number]['id'];

export default function DataQuality() {
  const [activeTab, setActiveTab] = useState<QualityTabId>('l1');
  const [targetDataset, setTargetDataset] = useState('daily_bar');
  const [scanYear, setScanYear] = useState<number | undefined>(undefined);
  const [scan, setScan] = useState<QualityScanResult | null>(null);
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 告警明细过滤与弹窗
  const [kindFilter, setKindFilter] = useState('all');
  const [severityFilter, setSeverityFilter] = useState('all');
  const [selectedBlastNode, setSelectedBlastNode] = useState<string | null>(null);
  const [reportModalOpen, setReportModalOpen] = useState(false);

  const scanTask = useAbortableTask();
  const lineageTask = useAbortableTask();

  const runScan = useCallback(async (datasetName = targetDataset, year = scanYear) => {
    const ctrl = scanTask.begin();
    setScanning(true); setErr(null);
    try {
      const r = await opsApi.qualityScan({ dataset: datasetName, year }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setScan(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '扫描失败');
    } finally {
      if (scanTask.finish(ctrl)) setScanning(false);
    }
  }, [scanTask, targetDataset, scanYear]);

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
    void runScan(targetDataset, scanYear);
  }, [loadLineage, runScan, targetDataset, scanYear]);

  const noCoverage = !!scan && (scan.symbols_scanned === 0 || scan.rows_scanned === 0);
  const partialCoverage = !!scan && !noCoverage && scan.truncated;
  const clearClaim = noCoverage
    ? null
    : partialCoverage
      ? `已扫描的 ${scan!.symbols_scanned}/${scan!.symbols_available} 只内未检出质量问题（窗口外未覆盖）`
      : '未检出任何质量问题（阈值口径见 data/quality.py）';

  // 筛选告警样本
  const filteredSamples = useMemo(() => {
    if (!scan?.samples) return [];
    return scan.samples.filter((s) => {
      const matchKind = kindFilter === 'all' || s.kind === kindFilter;
      const matchSev = severityFilter === 'all' || s.severity === severityFilter;
      return matchKind && matchSev;
    });
  }, [scan, kindFilter, severityFilter]);

  return (
    <div className="space-y-3">
      {/* 顶部标题与多维控制栏 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <PageHeader title="数据质量与血缘监控中心" />
          <div className="text-2xs text-ink-muted -mt-1">
            对标 Bloomberg B-PIPE / FactSet Data Governance · 行情多源对账 / 截面完整度 / 协变量漂移 / 信号健康 / 血缘扩散
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* 数据集选择 */}
          <select
            value={targetDataset}
            onChange={(e) => setTargetDataset(e.target.value)}
            disabled={scanning}
            className="rounded-md border border-hair bg-surface px-2 py-1.5 text-xs text-ink outline-none"
          >
            <option value="daily_bar">daily_bar (原始日线)</option>
            <option value="daily_bar_hfq">daily_bar_hfq (后复权)</option>
            <option value="daily_bar_qfq">daily_bar_qfq (前复权)</option>
          </select>

          {/* 年份选择 */}
          <select
            value={scanYear ?? ''}
            onChange={(e) => setScanYear(e.target.value ? Number(e.target.value) : undefined)}
            disabled={scanning}
            className="rounded-md border border-hair bg-surface px-2 py-1.5 text-xs text-ink outline-none"
          >
            <option value="">最新可用年份</option>
            <option value="2026">2026 年度</option>
            <option value="2025">2025 年度</option>
            <option value="2024">2024 年度</option>
          </select>

          <button
            onClick={() => void runScan(targetDataset, scanYear)}
            disabled={scanning}
            className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60 transition-colors"
          >
            {scanning ? '扫描中…' : '重新扫描'}
          </button>

          <button
            onClick={() => setReportModalOpen(true)}
            className="flex items-center gap-1 rounded-md border border-hair bg-surface px-3 py-1.5 text-xs font-medium text-ink-secondary hover:border-brand-200 hover:text-brand-600 transition-colors"
          >
            <span>📑 导出质量审计报告</span>
          </button>
        </div>
      </div>

      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}

      {/* 现代化四维工作台 Tab 导航切换 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex flex-wrap gap-1 rounded-lg border border-hair bg-surface-alt p-1">
          {QUALITY_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
                activeTab === tab.id
                  ? 'border border-hair bg-surface font-semibold text-brand-600 shadow-sm'
                  : 'text-ink-secondary hover:bg-surface/50 hover:text-ink'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* ==================== Tab 1: 基础行情合规与多源对账 (L1) ==================== */}
      {activeTab === 'l1' && (
        <div className="space-y-3">
          {/* KPI 概览卡片 */}
          {scan && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                <div className="text-2xs text-ink-secondary">扫描范围{noCoverage ? '（空）' : ''}</div>
                <div className={`num text-base font-semibold ${noCoverage ? 'text-warn' : ''}`}>
                  {scan.symbols_scanned}{scan.truncated ? ` / ${scan.symbols_available}` : ''} 只
                </div>
                <div className="text-2xs text-ink-muted">
                  {scan.dataset} · {scan.year} · {scan.rows_scanned.toLocaleString()} 行
                  {scan.truncated ? ` · 已达上限 ${scan.scan_limit}` : ''}
                </div>
                {noCoverage && (
                  <div className="mt-0.5 text-2xs text-warn">
                    本次未覆盖任何标的/行，不能据此判定数据健康
                  </div>
                )}
              </div>
              <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                <div className="text-2xs text-ink-secondary">问题总数</div>
                <div className="num text-base font-semibold">{scan.n_issues}</div>
                <div className="text-3xs text-ink-muted">覆盖 OHLC/收益率/复权/日历</div>
              </div>
              <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                <div className="text-2xs text-ink-secondary">其中 Error 阻断级</div>
                <div className="num text-base font-semibold text-danger">{scan.n_errors}</div>
                <div className="text-3xs text-ink-muted">触发物理隔离与写入阻断</div>
              </div>
              <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                <div className="text-2xs text-ink-secondary">检查项分布</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {Object.entries(scan.by_kind).slice(0, 4).map(([k, v]) => (
                    <span key={k} className="rounded bg-surface-sunken px-1 font-mono text-2xs">{k}:{v}</span>
                  ))}
                  {!Object.keys(scan.by_kind).length &&
                    (noCoverage
                      ? <span className="text-2xs text-warn">未覆盖数据，不能判定通过</span>
                      : <span className="text-2xs text-success">
                          {partialCoverage ? '已覆盖窗口内全部通过 ✓' : '全部通过 ✓'}
                        </span>)}
                </div>
              </div>
            </div>
          )}

          {/* 多源行情高频交叉对账面板 */}
          <MultiSourceArbitragePanel />

          {/* 异常告警明细增强表格 */}
          <SectionCard
            title="异常告警明细（QC 引擎真实检查项 · 支持穿透与受损扩散分析）"
            action={(
              <div className="flex items-center gap-2">
                {/* 类别筛选 */}
                <div className="flex gap-1 text-3xs">
                  {['all', 'ohlc', 'daily_return', 'null', 'duplicate'].map((k) => (
                    <button
                      key={k}
                      onClick={() => setKindFilter(k)}
                      className={`rounded px-1.5 py-0.5 transition-colors ${
                        kindFilter === k
                          ? 'bg-brand-500 text-white font-medium'
                          : 'bg-surface-alt text-ink-secondary hover:text-ink'
                      }`}
                    >
                      {k}
                    </button>
                  ))}
                </div>
                {/* 严重级筛选 */}
                <select
                  value={severityFilter}
                  onChange={(e) => setSeverityFilter(e.target.value)}
                  className="rounded border border-hair bg-surface px-1.5 py-0.5 text-3xs text-ink outline-none"
                >
                  <option value="all">全部级别</option>
                  <option value="error">Error 阻断</option>
                  <option value="warn">Warn 告警</option>
                </select>
              </div>
            )}
          >
            {scan ? (
              <div className="max-h-72 overflow-auto">
                <table className="quant-table w-full text-2xs">
                  <thead className="sticky top-0 bg-surface">
                    <tr className="text-ink-secondary">
                      <th className="text-left font-medium">标的代码</th>
                      <th className="text-left font-medium">异常交易日</th>
                      <th className="font-medium text-center">检查项</th>
                      <th className="font-medium text-center">级别</th>
                      <th className="text-left font-medium">质检诊断明细</th>
                      <th className="font-medium text-center">扩散分析</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSamples.map((s, i) => (
                      <tr key={i} className="border-t border-hair">
                        <td className="py-1 font-mono">
                          <Link
                            to={`/stock/${encodeURIComponent(s.symbol)}`}
                            className="text-brand-600 hover:text-brand-700 hover:underline font-semibold"
                          >
                            {s.symbol}
                          </Link>
                        </td>
                        <td className="num">{s.date ?? '—'}</td>
                        <td className="text-center font-mono">
                          <span className="rounded bg-surface-sunken px-1.5 py-0.2 text-3xs text-ink">
                            {s.kind}
                          </span>
                        </td>
                        <td className="text-center">
                          <span
                            className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                              s.severity === 'error' ? 'bg-danger-bg text-danger' : 'bg-warn-bg text-warn'
                            }`}
                          >
                            {s.severity.toUpperCase()}
                          </span>
                        </td>
                        <td className="max-w-xs truncate text-ink-secondary" title={s.detail}>
                          {s.detail}
                        </td>
                        <td className="text-center">
                          <button
                            onClick={() => setSelectedBlastNode(s.dataset || targetDataset)}
                            className="rounded border border-hair px-1.5 py-0.5 text-3xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 transition-colors"
                          >
                            扩散半径
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!filteredSamples.length && (
                      <tr>
                        <td
                          colSpan={6}
                          className={`py-6 text-center ${clearClaim ? 'text-success' : 'text-warn'}`}
                        >
                          {clearClaim ?? '本次扫描未覆盖任何数据（0 只 / 0 行），不能判定"无质量问题"'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="py-10 text-center text-xs text-ink-muted">质检扫描中…</div>
            )}
          </SectionCard>
        </div>
      )}

      {/* ==================== Tab 2: 特征工程与截面完整性 (L2) ==================== */}
      {activeTab === 'l2' && (
        <div className="space-y-3">
          <CrossSectionalCoverageCard />
          <FeatureDriftPanel />
        </div>
      )}

      {/* ==================== Tab 3: 因子生产健康与衰退监控 (L3) ==================== */}
      {activeTab === 'l3' && (
        <div className="space-y-3">
          <QualityRadarCard
            dataset={targetDataset}
            onDatasetChange={(d) => setTargetDataset(d)}
          />
          <FactorHealthCard />
        </div>
      )}

      {/* ==================== Tab 4: 全链路数据血缘与影响扩散 (L4) ==================== */}
      {activeTab === 'l4' && (
        <div className="space-y-3">
          <SectionCard title="数据血缘全链路拓扑图谱（原始数据 ➔ 清洗 ➔ 特征 ➔ 模型 ➔ 生产）">
            {graphLoading ? (
              <div className="py-10 text-center text-xs text-ink-muted">加载血缘图谱中…</div>
            ) : graph ? (
              <LineageGraphView
                graph={graph}
                onInspectNode={(nodeId) => setSelectedBlastNode(nodeId)}
              />
            ) : (
              <ErrorState message={graphError ?? '血缘图谱加载失败'} onRetry={() => void loadLineage()} />
            )}
          </SectionCard>
        </div>
      )}

      {/* 弹窗：数据血缘受损扩散分析 (Blast Radius Modal) */}
      <LineageBlastRadiusModal
        nodeId={selectedBlastNode}
        open={selectedBlastNode != null}
        onClose={() => setSelectedBlastNode(null)}
      />

      {/* 弹窗：数据质量全链路合规审计报告 (Audit Report Modal) */}
      <QualityAuditReportModal
        open={reportModalOpen}
        onClose={() => setReportModalOpen(false)}
        scan={scan}
        dataset={targetDataset}
      />
    </div>
  );
}
