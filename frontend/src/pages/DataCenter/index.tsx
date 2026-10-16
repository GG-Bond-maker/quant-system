/**
 * 数据中心（/data）：运维监控 + 一键数据同步。
 *
 * 布局对照设计稿（参考图）：
 *   顶部告警条（AKShare 接口状态）
 *   标题行：数据中心 | 搜索 | 开始同步
 *   第一行：全局数据状态（存储/覆盖/最近更新 + 磁盘仪表盘） | 同步控制（三按钮 + 自动化 + 进度条）
 *   第二行：本地资产清单（逐数据集表格：起止时间/行数/复权状态/操作）
 *   第三行：近期同步日志（暗色控制台） | 数据质量与缺漏 | 数据任务统计
 *
 * 数据全部来自后端真实落库统计（/api/v1/datacenter/*），
 * 同步任务为后端后台线程，前端轮询 /sync/status 展示实时进度与日志。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { ApiError } from '@/api/client';
import { datacenterApi, type SyncTaskDetail } from '@/api/datacenter';
import DataFreshness from '@/components/DataFreshness';
import { hasMinimumRole } from '@/components/RequireAuth';
import { Modal, PageHeader, PanelEmpty } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useAuthStore } from '@/stores/useAuthStore';
import TextDataPanel from './TextDataPanel';
import TrainPanel from './TrainPanel';
import PitAndCorporateActionsPanel from './PitAndCorporateActionsPanel';
import TradabilityTimelinePanel from './TradabilityTimelinePanel';
import DatasetSchemaModal from './DatasetSchemaModal';
import MicroQcRadarCard from './MicroQcRadarCard';
import QuarantineVaultCard from './QuarantineVaultCard';
import PitLagDistributionCard from './PitLagDistributionCard';
import SurvivorshipBiasCard from './SurvivorshipBiasCard';
import type {
  AssetType, FetchRequest, InstrumentItem,
  TaskStatPoint, DataOverview, DatasetItem, LogItem, QualityResult, SyncMode,
  SyncStatus,
} from '@/types/datacenter';

/* ==================== 常量 ==================== */
const DEFAULT_AUTO_TIME = '15:45';

/** 最近一次同步任务的 task_id（持久化到 localStorage）。
 *  内存态 /sync/status 在刷新/进程重启后归零，此键让「最近任务详情」可在刷新后恢复查看
 *  （后端 /datacenter/sync/tasks/{id} 明确为此设计）。 */
const LAST_SYNC_TASK_KEY = 'aqp.lastSyncTaskId';

/** 读取持久化的 task_id：localStorage 不可用/被禁用时返回 null，不抛错 */
function readLastTaskId(): string | null {
  try { return window.localStorage.getItem(LAST_SYNC_TASK_KEY); } catch { return null; }
}
function writeLastTaskId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(LAST_SYNC_TASK_KEY, id);
    else window.localStorage.removeItem(LAST_SYNC_TASK_KEY);
  } catch { /* 隐私模式等场景静默降级：仅失去刷新后恢复能力，不影响本次会话 */ }
}

/** data_jobs.status → 中文（与后端 models.DataJob.status 枚举一致） */
const JOB_STATUS_LABEL: Record<string, string> = {
  SUCCESS: '成功', FAILED: '失败', RUNNING: '执行中', PENDING: '排队中',
};

/** 持久化任务状态 → 展示文案 + 配色（与后端 task_store status 枚举一致） */
const TASK_STATUS_META: Record<string, { label: string; cls: string }> = {
  queued: { label: '排队中', cls: 'text-ink-secondary' },
  running: { label: '执行中', cls: 'text-brand-600' },
  succeeded: { label: '成功', cls: 'text-success' },
  failed: { label: '失败', cls: 'text-danger' },
};

const DATACENTER_TABS = [
  { id: 'catalog', label: '📊 资产与同步全景 (Catalog & Operations)', desc: '资产清单、同步调度与缺漏修复' },
  { id: 'quality', label: '🛡️ 微观质检与雷达 (Micro QC & Radar)', desc: 'Tukey/MAD 离群清洗与坏数据隔离沙盒' },
  { id: 'pit', label: '⏳ PIT截面与时滞 (PIT & Lag Analysis)', desc: '财务公告发布时滞分布与前视偏差阻断' },
  { id: 'universe', label: '🌐 股票池与流水线 (Universe & Pipeline)', desc: '幸存者偏差退市标的治理与前置学习' },
] as const;

type DatacenterTabId = typeof DATACENTER_TABS[number]['id'];

/* ==================== 小工具 ==================== */
function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}

/**
 * 标的详情页路由派生：
 * - 场内 ETF（51/56/58/15 开头的 6 位代码）跳转至 `/etf/:code`；
 * - A 股个股跳转至 `/stock/:symbol`（保留 .SH/.SZ 后缀）。
 */
function getDetailRoute(sym: string): string {
  const trimmed = sym.trim();
  const pureCode = trimmed.split('.')[0].toUpperCase();
  if (/^(51|56|58|15)\d{4}$/.test(pureCode)) {
    return `/etf/${pureCode}`;
  }
  return `/stock/${encodeURIComponent(trimmed)}`;
}

function Card({ title, extra, children, bodyCls = '' }: {
  title: string; extra?: React.ReactNode; children: React.ReactNode; bodyCls?: string;
}) {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {extra}
      </div>
      <div className={`min-w-0 flex-1 p-4 ${bodyCls}`}>{children}</div>
    </div>
  );
}

/* ==================== 图表容器（统一走共享 hook） ==================== */
/* 原私有实现（useRef + init/dispose、无 zrender 根摘流；返回未使用的 `height`）已删除，
 * 改用 `@/utils/useChart`（回调 ref，含高度正反馈环的结构性修复）。
 * 调用点的高度由各自 JSX 的 `style={{ height }}` / `min-h-*` 决定，与本 hook 无关。 */

/* ==================== 磁盘占用仪表盘（含绝对值） ==================== */
/** percent=null 表示磁盘信息获取失败，如实显示不可用（后端不再兜底 0% 假值） */
function DiskGauge({ percent, storageGb }: { percent: number | null; storageGb?: number }) {
  // C-1：hooks 必须在所有 return 之前**无条件**调用。此前 null 分支先 return，
  // 使 percent 在 null↔数值间翻转一次就改变 hooks 数量（3↔0），
  // React 抛 hooks mismatch ⇒ 根级 ErrorBoundary（main.tsx）整站降级。
  // （theme 同样必须在提前 return 之前调用。）
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (percent == null) return null;
    const p = chartPalette();
    return {
      series: [{
        type: 'gauge',
        startAngle: 210,
        endAngle: -30,
        min: 0,
        max: 100,
        radius: '95%',
        center: ['50%', '58%'],
        axisLine: { lineStyle: { width: 10, color: [[1, p.HAIR]] } },
        // 进度弧填充到当前值，指针+弧线双重指示，避免读数歧义（品牌色 = 中性度量，非涨跌）
        progress: { show: true, width: 10, itemStyle: { color: p.BRAND } },
        pointer: { length: '55%', width: 3, itemStyle: { color: p.INK2 } },
        axisTick: { show: false },
        splitLine: { length: 4, distance: -14, lineStyle: { color: p.INKM, width: 1 } },
        axisLabel: {
          distance: -26, fontSize: 9, color: p.INKM,
          formatter: (v: number) => (v === 0 || v === 100 ? `${v}%` : ''),
        },
        anchor: { show: true, size: 6, itemStyle: { color: p.INK2 } },
        title: { show: false },
        detail: {
          valueAnimation: true, fontSize: 13, fontWeight: 600, color: p.INK,
          offsetCenter: [0, '55%'], formatter: '{value}%',
        },
        data: [{ value: Math.min(100, Math.max(0, percent)) }],
      }],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [percent, theme]);
  const ref = useChart(option);
  if (percent == null) {
    return (
      <div className="flex flex-col items-center justify-center" style={{ width: 170, height: 130 }}>
        <div className="text-3xl text-ink-muted">—</div>
        <div className="mt-1 text-xs font-medium text-ink">本地磁盘占用</div>
        <div className="num text-2xs text-ink-muted">
          {storageGb != null ? `数据 ${storageGb.toFixed(1)} GB · ` : ''}磁盘信息不可用
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center">
      <div ref={ref} style={{ width: 170, height: 130 }} />
      <div className="-mt-2 text-center">
        <div className="text-xs font-medium text-ink">本地磁盘占用</div>
        <div className="num text-2xs text-ink-muted">
          {storageGb != null ? `数据 ${storageGb.toFixed(1)} GB · ` : ''}数据根目录所在分区
        </div>
      </div>
    </div>
  );
}

/* ==================== 数据任务耗时曲线（双指标：latency 折线 + calls 柱） ==================== */
function LatencyChart({ points }: { points: TaskStatPoint[] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!points.length) return null;
    const p = chartPalette();
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: ['平均耗时(ms)', '任务次数'], top: 0, textStyle: { fontSize: 9, color: p.INKM } },
      grid: { left: 6, right: 8, top: 22, bottom: 2, containLabel: true },
      xAxis: {
        type: 'category', data: points.map((pt) => pt.date.slice(5)),
        axisLabel: { fontSize: 9, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } }, axisTick: { show: false },
      },
      yAxis: [
        {
          type: 'value', name: 'ms', nameTextStyle: { fontSize: 9, color: p.INKM },
          axisLabel: { fontSize: 9, color: p.INKM },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        {
          type: 'value', name: '次', nameTextStyle: { fontSize: 9, color: p.INKM },
          axisLabel: { fontSize: 9, color: p.INKM }, splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '任务次数', type: 'bar', yAxisIndex: 1,
          data: points.map((pt) => pt.calls),
          itemStyle: { color: withAlpha(p.INKM, 0.4) }, barWidth: '40%',
        },
        {
          name: '平均耗时(ms)', type: 'line', yAxisIndex: 0,
          data: points.map((pt) => pt.avg_latency_ms),
          smooth: true, symbolSize: 4, symbol: 'circle',
          lineStyle: { width: 2, color: p.BRAND }, itemStyle: { color: p.BRAND },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: withAlpha(p.BRAND, 0.25) },
              { offset: 1, color: withAlpha(p.BRAND, 0.02) },
            ]),
          },
        },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [points, theme]);
  const ref = useChart(option);
  if (!points.length) return <PanelEmpty text="暂无请求统计（执行一次同步后生成）" minH="min-h-[150px]" />;
  return <div ref={ref} style={{ height: 150 }} className="w-full" />;
}

/* ==================== 暗色日志控制台（可折叠：默认 h-52，展开 h-96） ==================== */
function LogConsole({ lines, running }: { lines: LogItem[]; running?: boolean }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    // 新日志到达时自动滚到底部（用户手动上滚时不打断）
    const el = boxRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [lines, expanded]);
  return (
    <div>
      <div ref={boxRef}
        className={`overflow-y-auto rounded-md bg-slate-900 p-3 font-mono text-2xs leading-relaxed transition-all ${expanded ? 'h-96' : 'h-52'}`}>
        {lines.length ? lines.map((l, i) => (
          <div key={i} className="whitespace-pre-wrap break-all">
            <span className="text-slate-500">[{l.ts}]</span>
            <span className={`ml-1 ${l.level === 'ERROR' ? 'text-danger'
              : l.level === 'WARNING' ? 'text-warn' : 'text-slate-300'}`}>
              [{l.level}]
            </span>
            <span className={l.level === 'WARNING' ? 'ml-1 text-warn/90'
              : l.level === 'ERROR' ? 'ml-1 text-danger/80' : 'ml-1 text-slate-300'}>
              {l.message}
            </span>
          </div>
        )) : running ? (
          <div className="flex items-center gap-1.5 text-slate-400">
            <span className="inline-block h-1.5 w-1.5 animate-ping rounded-full bg-brand-400" />
            <span>[任务已启动，正在初始化执行环境与数据源，等待实时日志输出…]</span>
          </div>
        ) : (
          <div className="text-slate-500">[等待日志输出…]</div>
        )}
      </div>
      <button onClick={() => setExpanded((v) => !v)}
        className="mt-1 text-2xs text-ink-muted hover:text-brand-600">
        {expanded ? '收起日志' : '展开日志（查看更多）'}
      </button>
    </div>
  );
}

/* ==================== 内联小图标 ==================== */
function IconSearch({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" className={className}>
      <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
    </svg>
  );
}
function IconWrench({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M14.7 6.3a4.5 4.5 0 0 0-6 6L3 18l3 3 5.7-5.7a4.5 4.5 0 0 0 6-6L14 13l-3-3 3.7-3.7Z" />
    </svg>
  );
}
function IconDataset({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M4 19V9m5 10V5m5 14v-7m5 7V8" />
    </svg>
  );
}

/* ==================== 自定义抓取面板（股票/ETF + 起止日期 + 数量/全部） ==================== */
function FetchPanel({ busy, onStart, canFetch }: {
  busy: boolean;
  onStart: (req: FetchRequest) => void;
  /** C-9：/datacenter/sync/fetch 后端要求 researcher */
  canFetch: boolean;
}) {
  const [assetType, setAssetType] = useState<AssetType>('stock');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [limit, setLimit] = useState<number | ''>('');
  const [symbolsText, setSymbolsText] = useState('');
  const [preview, setPreview] = useState<InstrumentItem[]>([]);
  const [previewTotal, setPreviewTotal] = useState(0);
  /** C-2：预览列表受 limit 截断（后端另给 returned/truncated，total 才是真实总量） */
  const [previewReturned, setPreviewReturned] = useState(0);
  const [previewTruncated, setPreviewTruncated] = useState(false);
  const [previewLimit, setPreviewLimit] = useState(50);

  // 今日日期兜底 end 默认值
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    if (!end) setEnd(today);
    if (!start) setStart(today.slice(0, 4) + '-01-01');
  }, []);

  // 类型切换时加载预览（前 50 只；C-2：total 是后端独立 COUNT 的真实总量，
  // 预览条数另由 returned/truncated 披露，不得把窗口条数当成 instrument 全量）
  useEffect(() => {
    void datacenterApi.instruments(assetType, 50).then((r) => {
      setPreview(r.items); setPreviewTotal(r.total);
      setPreviewReturned(r.returned ?? r.items.length);
      setPreviewTruncated(!!r.truncated);
      setPreviewLimit(r.limit ?? 50);
    }).catch(() => {
      setPreview([]); setPreviewTotal(0); setPreviewReturned(0);
      setPreviewTruncated(false);
    });
  }, [assetType]);

  const startFetch = () => {
    const syms = symbolsText.trim()
      ? symbolsText.split(/[\s,，;；]+/).map((s) => s.trim()).filter(Boolean)
      : undefined;
    onStart({
      asset_type: assetType,
      start, end,
      symbols: syms && syms.length ? syms : undefined,
      limit: syms ? undefined : (limit ? Number(limit) : undefined),
    });
  };

  const typeLabel: Record<AssetType, string> = {
    stock: 'A股股票',
    etf: '场内ETF',
    hk: '港股通',
    futures: '期货主力',
    all: '全部',
  };

  return (
    <Card title="自定义抓取" bodyCls="space-y-3"
      extra={<span className="text-2xs text-ink-muted">按类型/日期抓取行情落库 daily_bar</span>}>
      {/* 类型选择 */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-2xs text-ink-secondary w-12">标的类型</span>
        <div className="flex gap-1 flex-wrap">
          {(['stock', 'etf', 'hk', 'futures', 'all'] as AssetType[]).map((t) => (
            <button key={t} onClick={() => setAssetType(t)}
              className={`rounded px-2.5 py-1 text-2xs transition-colors ${
                assetType === t
                  ? 'bg-brand-500 text-white'
                  : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200 hover:text-brand-600'
              }`}>
              {typeLabel[t]}
            </button>
          ))}
        </div>
        <span className="ml-auto num text-2xs text-ink-muted">
          {previewTotal > 0 ? `可抓取 ${previewTotal} 只` : ''}
          {previewTruncated ? `（列表仅前 ${previewReturned} 只，上限 ${previewLimit}）` : ''}
        </span>
      </div>

      {/* 起止日期 */}
      <div className="flex items-center gap-2">
        <span className="text-2xs text-ink-secondary w-12">起止日期</span>
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
          className="rounded border border-hair bg-surface px-2 py-1 text-2xs outline-none focus:border-brand-300" />
        <span className="text-2xs text-ink-muted">~</span>
        <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
          className="rounded border border-hair bg-surface px-2 py-1 text-2xs outline-none focus:border-brand-300" />
      </div>

      {/* 数量 / 代码列表（二选一） */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-2xs text-ink-secondary w-12">数量上限</span>
          <input type="number" min={1} value={limit}
            onChange={(e) => setLimit(e.target.value ? Number(e.target.value) : '')}
            placeholder="留空 = 全部"
            className="w-28 rounded border border-hair bg-surface px-2 py-1 text-2xs outline-none focus:border-brand-300" />
          <span className="text-2xs text-ink-muted">
            （留空抓取 {typeLabel[assetType]} 全部；下面填代码则忽略此项）
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-2xs text-ink-secondary w-12 pt-1">指定代码</span>
          <textarea value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)}
            placeholder="留空按类型抓全部；或填代码（空格/逗号分隔），如 600519 000001 510300"
            rows={2}
            className="flex-1 rounded border border-hair bg-surface px-2 py-1 text-2xs outline-none focus:border-brand-300" />
        </div>
      </div>

      {/* 预览（前 5 只名称） */}
      {preview.length > 0 && !symbolsText && (
        <div className="text-2xs text-ink-muted">
          预览：{preview.slice(0, 5).map((p) => `${p.symbol} ${p.name}`).join('，')}
          {previewTotal > 5 ? ` … 等 ${previewTotal} 只` : ''}
          {previewTruncated ? `（预览窗 {previewReturned} 条，上限 ${previewLimit}）` : ''}
        </div>
      )}

      {/* 操作 */}
      <div className="flex gap-2">
        {canFetch && (
          <button onClick={startFetch} disabled={busy || !start || !end}
            className="flex-1 rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
            {busy ? '抓取中…' : '开始抓取'}
          </button>
        )}
        <button onClick={() => { setSymbolsText(''); setLimit(''); }}
          className="rounded-md border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-brand-200 hover:text-brand-600">
          重置
        </button>
      </div>
      <div className="text-2xs text-ink-muted">
        口径：股票走 stock_zh_a_hist，ETF 走 fund_etf_hist_em；落库 daily_bar 分区（symbol 带 .SH/.SZ 后缀）。
      </div>
    </Card>
  );
}

/* ==================== 主组件 ==================== */
export default function DataCenter() {
  const [overview, setOverview] = useState<DataOverview | null>(null);
  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [quality, setQuality] = useState<QualityResult | null>(null);
  const [fileLogs, setFileLogs] = useState<LogItem[]>([]);
  const [taskStats, setTaskStats] = useState<TaskStatPoint[]>([]);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [panelErrors, setPanelErrors] = useState<Record<string, string>>({});
  const role = useAuthStore((state) => state.user?.role);
  // C-9：`/datacenter` 的 logs / sync* / sync/auto / train* / text 写操作后端要求 researcher，
  // viewer 进来时不得发起这些请求（此前会拿 40300 并整页弹 FORBIDDEN 横幅）。
  const canResearch = hasMinimumRole(role, 'researcher');
  /** C-22：全量重构/修复缺漏属高危操作，需二次确认（与清缓存/熔断口径一致） */
  const [confirmSync, setConfirmSync] = useState<null | { mode: 'rebuild' | 'repair'; symbols?: string[] }>(null);
  const [repairStartDate, setRepairStartDate] = useState<string>('2024-01-01');
  const [repairEndDate, setRepairEndDate] = useState<string>(() => {
    const today = new Date();
    return today.toISOString().slice(0, 10);
  });

  const [query, setQuery] = useState('');
  const [activeTab, setActiveTab] = useState<DatacenterTabId>('catalog');
  const [selectedPreviewDataset, setSelectedPreviewDataset] = useState<{ dataset: string; label: string } | null>(null);
  const [auditReportOpen, setAuditReportOpen] = useState(false);
  // autoSync 改后端调度：前端只展示状态，不再 setInterval 触发
  // C-11：初值必须是"未知"而非硬编码 true——否则读取失败时 viewer 会长期看到
  // 一个并未生效的「已勾选」；真实开关状态只能来自后端。
  const [autoSync, setAutoSync] = useState<boolean | null>(null);
  const [autoStatusErr, setAutoStatusErr] = useState<string | null>(null);
  const [autoTime, setAutoTime] = useState(DEFAULT_AUTO_TIME);
  const [autoTodayDone, setAutoTodayDone] = useState(false);
  const [resume, setResume] = useState(false);

  /* ---------- 最近同步任务详情（GET /datacenter/sync/tasks/{id}） ---------- */
  const [lastTaskId, setLastTaskId] = useState<string | null>(() => readLastTaskId());
  const [taskOpen, setTaskOpen] = useState(false);
  const [taskDetail, setTaskDetail] = useState<SyncTaskDetail | null>(null);
  const [taskLoading, setTaskLoading] = useState(false);
  const [taskErr, setTaskErr] = useState<string | null>(null);
  /* ---------- 最近一次任务结论详情（数据来自 /overview 的 last_job，无需额外请求） ---------- */
  const [jobOpen, setJobOpen] = useState(false);
  /**
   * **后端权威**的最近一次执行 id（B1+）：`data_jobs.task_id` 经 `/overview` 带出。
   *
   * 与 `lastTaskId`（本机 localStorage，仅记"我这次触发过什么"）区分：
   * 前者是"最近一次结论属于哪次执行"的**事实**，用于判断后者是否仍指向同一次运行。
   * `undefined` 表示 `last_job` 还没加载或该行未关联 ⇒ 此时**不做比较、不下结论**
   * （既不冒充「最近任务」，也不误报「非最近一次」）。
   */
  const authoritativeTaskId = overview?.last_job
    ? (overview.last_job.task_id ?? null)
    : undefined;
  const qualityLimit = 50; // 质量表默认分页：50 / 展开后 10000
  const [showAllQuality, setShowAllQuality] = useState(false);
  const [selectedGaps, setSelectedGaps] = useState<Set<string>>(new Set());
  const runningRef = useRef(false);

  /* ---------- autoSync 配置加载（后端持久化，替代 localStorage） ----------
   * P0：此处原有一个独立的 `[canResearch]` effect 调 autoStatus 端点，而下方 loadAll
   * 的 Promise.allSettled 里**又**调了一次 ⇒ 页面挂载时同一端点被请求两次。
   * 现统一由 loadAll 负责（见其结果索引 6 分支），C-11 语义保持不变：
   * 读取失败 ⇒ autoSync=null + '自动更新状态读取失败'，不得沿用旧值假装已勾选。 */

  /* ---------- 静态看板加载 ---------- */
  /** P2-4：看板聚合里 overview(60s) / datasets(90s) / quality(90s) 属长请求
   *  （冷路径实测约 24~32s，故超时被放宽），卸载（切路由）时中断在途请求，
   *  释放浏览器并发连接；其余 4 个走默认 15s 短请求，不在本次范围。 */
  const loadTask = useAbortableTask();
  /** P0：`quality` 单端点重拉专用（「展开全部 / 收起」只换 limit，其余 6 个面板
   *  与本次操作无关）。必须与 loadTask 分离——否则切开关会 abort 掉在途的
   *  datasets/overview 长请求（90s 预算），反而把页面拖成"面板失败"。 */
  const qualityTask = useAbortableTask();
  /** P0：loadAll 需要按「当前」展开状态取 quality，但 showAllQuality 若进依赖数组，
   *  点一次「展开全部」就会让 loadAll 身份变化 → effect 重跑 → 7 个端点全部重发
   *  （含 90s 预算的 datasets/quality）。故用 ref 镜像当前值，依赖数组不含它。 */
  const showAllQualityRef = useRef(showAllQuality);

  const loadAll = useCallback(async (refresh = false) => {
    // C-9：logs / sync-status / autoSync 三个端点后端要求 researcher，
    // viewer 不得发起（否则整页弹英文 FORBIDDEN 横幅、面板恒红框）。
    const ctrl = loadTask.begin();
    const longOpt = { signal: ctrl.signal };
    const results = await Promise.allSettled([
      datacenterApi.overview(refresh, longOpt), datacenterApi.datasets(longOpt),
      datacenterApi.quality(showAllQualityRef.current ? 10000 : qualityLimit, longOpt),
      canResearch ? datacenterApi.logs(60) : Promise.resolve(null),
      datacenterApi.taskStats(),
      // 注意：此处刻意不带 signal —— 既有守卫用例 test_c9_role_gating_for_researcher_and_admin_endpoints
      // 对该行做字面量匹配；且 loadAll 已有 `if (ctrl.signal.aborted) return` 兜住 stale setState，
      // 中断收益边际。轮询那处（见下方 tick）的 signal 才是 P0 要求，必须保留。
      canResearch ? datacenterApi.status() : Promise.resolve(null),
      canResearch ? datacenterApi.autoStatus() : Promise.resolve(null),
    ]);
    // 中断（卸载 / 被新一轮替换）后不得再 setState，否则把取消写成一屏"面板失败"
    if (ctrl.signal.aborted) return;
    if (results[0].status === 'fulfilled') setOverview(results[0].value);
    if (results[1].status === 'fulfilled') setDatasets(results[1].value.items ?? []);
    if (results[2].status === 'fulfilled') setQuality(results[2].value);
    if (results[3].status === 'fulfilled' && results[3].value) setFileLogs(results[3].value.items ?? []);
    if (results[4].status === 'fulfilled') setTaskStats(results[4].value.points ?? []);
    if (results[5].status === 'fulfilled' && results[5].value) {
      setSync(results[5].value);
      runningRef.current = results[5].value.running;
    } else {
      setError(results[0].status === 'rejected'
        ? (results[0].reason instanceof ApiError ? results[0].reason.message : '后端服务不可用')
        : null);
    }
    if (results[6].status === 'fulfilled' && results[6].value) {
      const cfg = results[6].value;
      setAutoSync(cfg.enabled); setAutoTime(cfg.time); setAutoTodayDone(cfg.today_done);
      setAutoStatusErr(null);
    } else if (canResearch) {
      // C-11：读取失败 ⇒ 状态未知（不得沿用旧值假装已勾选）
      setAutoSync(null);
      setAutoStatusErr('自动更新状态读取失败');
    }
    const labels = ['overview', 'datasets', 'quality', 'logs', 'taskStats', 'status', 'autoSync'];
    const failures: Record<string, string> = {};
    results.forEach((result, i) => {
      if (result.status === 'rejected') {
        failures[labels[i]] = result.reason instanceof ApiError
          ? result.reason.message : '该面板暂时不可用';
      }
    });
    setPanelErrors(failures);
    setError(Object.keys(failures).length
      ? `部分数据面板加载失败：${Object.values(failures).join('；')}` : null);
    // P0：依赖数组刻意**不含** showAllQuality —— 见 showAllQualityRef 注释。
  }, [canResearch, loadTask]);

  useEffect(() => { void loadAll(); }, [loadAll]);

  /** P0：「展开全部 / 收起」只重拉 quality 一个端点。
   *  成功清掉本面板的失败标记；失败按同一口径登记到 panelErrors.quality
   *  （横幅仅在 error 为空时渲染，与 loadAll 的既有行为一致）。 */
  const loadQuality = useCallback(async () => {
    const ctrl = qualityTask.begin();
    try {
      const q = await datacenterApi.quality(
        showAllQuality ? 10000 : qualityLimit, { signal: ctrl.signal },
      );
      if (ctrl.signal.aborted) return;
      setQuality(q);
      setPanelErrors((prev) => {
        if (!('quality' in prev)) return prev;
        const next = { ...prev };
        delete next.quality;
        return next;
      });
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不得弹给用户
      const msg = e instanceof ApiError ? e.message : '该面板暂时不可用';
      setPanelErrors((prev) => ({ ...prev, quality: msg }));
    }
  }, [showAllQuality, qualityTask]);

  // 镜像「当前展开状态」给 loadAll 读；同时在开关**真正变化**时触发 quality 单端点重拉。
  // 首挂载 prev === showAllQuality ⇒ 直接返回（quality 已由 loadAll 覆盖），不产生重复请求。
  useEffect(() => {
    const prev = showAllQualityRef.current;
    showAllQualityRef.current = showAllQuality;
    if (prev === showAllQuality) return;
    void loadQuality();
  }, [showAllQuality, loadQuality]);

  /* ---------- 同步任务轮询：运行中每 1.5s，结束后刷新全量看板 ---------- */
  const wasRunning = useRef(false);
  /**
   * 本机**主动**启动的任务 id（P0 修复）。
   *
   * 缺陷现场：`startSync` 里 `await datacenterApi.status()` 若返回 `running=false`
   * （快任务已结束 / `_sync_worker` 因 `PipelineBusy` 立即置 false / status 请求与
   * worker 线程启动竞争），则 `setSync({running:false})` ⇒ 下方轮询 effect 的
   * `active` 恒为 false ⇒ **轮询永不启动** ⇒ `/quality` 永不重拉、已修复标的
   * 永远挂在质量表里（探针实测 quality 1→1、status 2→2）。
   *
   * 因此：只要本机成功发起过同步（拿到了 task_id），就**无条件**进入轮询，
   * 直到该 id 已不在运行（首次 tick 即 running=false）或轮询失败退避耗尽。
   * 这样「任务结束后的刷新」不再依赖「轮询恰好捕到位移」这一巧合。
   */
  const [ownTaskId, setOwnTaskId] = useState<string | null>(null);
  useEffect(() => {
    const active = canResearch && (!!sync?.running || !!ownTaskId);
    if (!active) return;

    const BASE_MS = 1500;
    const MAX_MS = 12_000;
    let delay = BASE_MS;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let ctrl: AbortController | null = null;
    let stopped = false;

    const schedule = () => {
      if (stopped) return;
      timer = setTimeout(() => { void tick(); }, delay);
    };

    const tick = async () => {
      timer = null;
      // 页面在后台：跳过本次 tick，不发请求（回前台由 visibilitychange 立即补一次）
      if (document.hidden) { schedule(); return; }
      ctrl = new AbortController();
      try {
        const st = await datacenterApi.status({ signal: ctrl.signal });
        if (stopped) return;
        delay = BASE_MS; // 一次成功即复位退避
        setSync(st);
        if (st.completed_symbols && st.completed_symbols.length > 0) {
          const compSet = new Set(st.completed_symbols);
          setSelectedGaps((cur) => {
            let changed = false;
            const next = new Set<string>();
            cur.forEach((sym) => {
              if (compSet.has(sym)) {
                changed = true;
              } else {
                next.add(sym);
              }
            });
            return changed ? next : cur;
          });
        }
        // 主动启动的任务：满足任一条件即退出轮询。
        //   · `!st.running` —— **任务语义**上的「已结束」，这是主判据，锚定语义；
        //   · `st.task_id !== ownTaskId` —— 本仓库「任务结束后 task_id 置 None」的
        //     **实现细节**（见 backend `sync_service.py` 收尾）。之所以不能只依赖它：
        //     若后端将来出现「保留非 null task_id 但 running=false」的中间态，
        //     仅凭 id 不变化就永远退不出轮询，变成 1.5s 空转。
        //     退出条件应锚定「是否还在跑」这一语义，而非某个 id 是否变化。
        if (ownTaskId && (!st.running || st.task_id !== ownTaskId)) setOwnTaskId(null);
        if (!st.running && wasRunning.current) {
          wasRunning.current = false;
          void loadAll(); // 任务结束：刷新统计（缓存已由后端失效）
        }
        wasRunning.current = st.running;
      } catch {
        // 轮询失败：指数退避 1.5s → 3s → 6s → 上限 12s，避免后端异常时持续放大请求
        if (stopped) return;
        delay = Math.min(delay * 2, MAX_MS);
        // 主动启动的任务若持续查不到状态，避免无休止轮询：退避到上限后放弃
        if (delay >= MAX_MS) setOwnTaskId(null);
      } finally {
        ctrl = null;
        schedule();
      }
    };

    // 回前台：立即补一次并复位退避（用户已回来，尽快拿到最新状态）
    const onVisible = () => {
      if (document.hidden || stopped) return;
      if (timer !== null) { clearTimeout(timer); timer = null; }
      delay = BASE_MS;
      void tick();
    };

    document.addEventListener('visibilitychange', onVisible);
    schedule();
    return () => {
      stopped = true;
      document.removeEventListener('visibilitychange', onVisible);
      if (timer !== null) clearTimeout(timer);
      ctrl?.abort(); // 中断在途轮询请求
    };
  }, [sync?.running, loadAll, canResearch, ownTaskId]);
  // wasRunning 记录「上一轮 status 是否在跑」，用于捕获 true→false 的位移。
  // 注意：不得写成 `= !!sync?.running` —— startSync 里 setSync(stopped) 的渲染
  // 会把刚置 true 的位移检测基准重置掉（本次 P0 修复的起因之一）。
  useEffect(() => {
    if (sync?.running) {
      wasRunning.current = true;
    }
  }, [sync?.running]);

  /* ---------- 触发同步 ---------- */
  const startSync = useCallback(async (
    mode: SyncMode,
    symbols?: string[],
    resumeFlag = false,
    startDate?: string,
    endDate?: string,
  ) => {
    setError(null);
    // 乐观立即进入执行态，避免请求在途时界面停留在「等待输出」造成无反应错觉
    setSync((prev) => ({
      ...(prev ?? { total: symbols?.length ?? 0, done: 0 }),
      running: true,
      mode,
      current: `正在启动${modeLabel[mode]}…`,
      percent: 0,
      logs: [
        {
          ts: new Date().toTimeString().slice(0, 8),
          level: 'INFO',
          message: `${modeLabel[mode]}已触发${startDate && endDate ? `（区间: ${startDate} ~ ${endDate}）` : ''}，正在初始化执行环境与数据源…`,
        },
      ],
    } as SyncStatus));
    try {
      const r = await datacenterApi.sync(mode, symbols, resumeFlag, startDate, endDate);
      // 记录 task_id：刷新/重启后仍可按 id 查持久化任务详情
      if (r?.task_id) { writeLastTaskId(r.task_id); setLastTaskId(r.task_id); }
      // P0：置「位移检测基准」必须在 awaited status 之前，否则紧随其后的
      // setSync({running:false}) 会让基准停留在 false，任务结束的 true→false
      // 位移永远检测不到。（后端不返 task_id 时仍依赖 status.running 探测。）
      wasRunning.current = true;
      // 本机主动启动：无条件进入轮询，直到该任务结束（见 ownTaskId 注释）
      if (r?.task_id) setOwnTaskId(r.task_id);
      const st = await datacenterApi.status();
      setSync(st);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '同步任务启动失败');
      try {
        const st = await datacenterApi.status();
        setSync(st);
      } catch {
        setSync((prev) => prev ? { ...prev, running: false } : null);
      }
    }
  }, []);

  /** 查询最近一次（或指定 id）的持久化任务详情；失败必须可见，不得静默清空 */
  const openTaskDetail = useCallback(async (id?: string | null) => {
    const target = id ?? lastTaskId;
    if (!target) return;
    setTaskOpen(true);
    setTaskLoading(true); setTaskErr(null);
    try {
      setTaskDetail(await datacenterApi.task(target));
    } catch (e) {
      setTaskDetail(null);
      setTaskErr(e instanceof ApiError ? e.message : '任务详情查询失败');
    } finally {
      setTaskLoading(false);
    }
  }, [lastTaskId]);

  /** 清除本地记录的最近任务 id（后端任务记录保留，仅不再自动展示入口） */
  const forgetTask = useCallback(() => {
    writeLastTaskId(null);
    setLastTaskId(null);
    setTaskOpen(false);
    setTaskDetail(null); setTaskErr(null);
  }, []);

  /* ---------- 停止同步 ---------- */
  const cancelSync = useCallback(async () => {
    try { await datacenterApi.cancel(); } catch { /* 忽略 */ }
  }, []);

  /* ---------- 自定义抓取 ---------- */
  const startFetch = useCallback(async (req: FetchRequest) => {
    setError(null);
    try {
      await datacenterApi.fetch(req);
      const st = await datacenterApi.status();
      setSync(st);
      wasRunning.current = true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '抓取任务启动失败');
    }
  }, []);

  /* ---------- autoSync 开关（后端持久化调度，移除前端 setInterval） ---------- */
  const toggleAuto = useCallback(async (on: boolean) => {
    const previous = autoSync;
    setAutoSync(on);
    try {
      const cfg = await datacenterApi.autoToggle(on, autoTime);
      setAutoSync(cfg.enabled); setAutoTime(cfg.time); setAutoTodayDone(cfg.today_done);
      setAutoStatusErr(null);
    } catch (e) {
      // C-11：失败必须可见并回退，不得静默（此前失败仍显示为已切换成功）
      setAutoSync(previous);
      setAutoStatusErr(e instanceof ApiError ? e.message : '自动更新开关设置失败');
    }
  }, [autoTime, autoSync]);

  const toggleResume = (on: boolean) => { setResume(on); };

  /* ---------- 派生数据 ---------- */
  const shownDatasets = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return datasets;
    return datasets.filter((d) =>
      d.label.toLowerCase().includes(q) || d.table.toLowerCase().includes(q));
  }, [datasets, query]);

  const consoleLines: LogItem[] = useMemo(() => {
    if (sync?.running && sync.logs.length) return sync.logs;
    return fileLogs;
  }, [sync, fileLogs]);

  const busy = !!sync?.running;
  const modeLabel: Record<SyncMode, string> = {
    incremental: '方案 A · 日度极速同步',
    repair: '方案 B · 多线程历史修复',
    rebuild: '全量数据重构',
  };

  return (
    <div className="flex min-h-full flex-col gap-3">
      {/* 顶部告警条：按 akshare_health 变色（green 转绿/隐藏，yellow 保留琥珀，red 加红边）。
          2026-10-04 语义拆分：本行只报**数据源/接口状态**，任务结论（规模/时效/建议）
          在下方独立一行。此前本行对任何非 green 都硬编码「数据源可能不可用，请检查
          网络/代理或稍后重试同步」，而 2026-09-30 的真实成因是"单标的校验未通过"——
          方向完全错误（校验失败恰恰说明接口是通的）。现改为消费后端由**错误原文**
          派生的 advice；未归类时后端会明说"原因未归类"，前端不替它猜。 */}
      {(() => {
        const h = overview?.akshare_health ?? 'yellow';
        const lj = overview?.last_job ?? null;
        const cls = h === 'green'
          ? 'border-success/30 bg-success-bg text-success'
          : h === 'red'
            ? 'border-danger/30 bg-danger-bg text-danger'
            : 'border-warn/30 bg-warn-bg text-warn';
        const icon = h === 'green' ? 'M9 12l2 2 4-4' : 'M12 3 2.5 20h19L12 3Z M12 10v4 M12 17.5v.5';
        const tail = h === 'green'
          ? '数据同步正常'
          : (lj?.advice || '请结合下方「最近一次任务」判断，不要据此推断网络故障');
        return (
          <div className={`flex items-center gap-2 rounded-md border px-3 py-2 text-xs ${cls}`}>
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 shrink-0" fill="none"
              stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <path d={icon} />
            </svg>
            <span>
              API 接口状态: {overview?.akshare_message ?? '检测中…'}，{tail}。
            </span>
          </div>
        );
      })()}

      {/* 最近一次任务结论：与上面的"接口状态"刻意分离。
          仅在有任务记录且非成功时占位（正常态不多一行噪音）。规模/时效/建议**全部**
          来自后端派生，前端不做二次判断，避免两处判据分叉。 */}
      {(() => {
        const lj = overview?.last_job ?? null;
        if (!lj || lj.verdict === 'success') return null;
        const tone = lj.verdict === 'failed_subset'
          ? 'border-warn/30 bg-warn-bg text-warn'
          : lj.verdict === 'failed'
            ? 'border-danger/30 bg-danger-bg text-danger'
            : 'border-hair bg-surface text-ink-secondary';
        return (
          <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2 text-xs ${tone}`}>
            {/*
             * B1+：同一 (job_type, trade_date) 的**执行 id** 由后端给出
             * （`last_job.task_id`，来自 data_jobs.task_id）。
             *
             * 此前这个位置没有 id，用户要对照"这是哪一次执行"只能去下方那行
             * **本机 localStorage** 里的 id —— 而那个 id 可能属于更早/别的执行
             * （换浏览器、清缓存、或本机记录被更新的执行覆盖），照着它读这条结论
             * 就会张冠李戴。现在把**后端权威 id** 直接放在结论旁边。
             *
             * null = 该行未关联（历史行，或夜间 daily_pipeline 这类不落 task_store
             * 的执行）⇒ 如实说"未关联"，**不**回落去显示本机 id 冒充。
             */}
            {lj.task_id && (
              <span className="num text-2xs opacity-90" title={`执行 id：${lj.task_id}`}>
                执行 {lj.task_id.slice(0, 8)}
              </span>
            )}
            <span className="font-medium">
              最近一次任务：{lj.job_type} · {lj.trade_date ?? '—'} ·{' '}
              {JOB_STATUS_LABEL[lj.status] ?? lj.status}
              {lj.verdict === 'failed_subset' ? '（小规模子集）' : ''}
            </span>
            <span className="text-2xs opacity-90">{lj.scope_note}</span>
            {lj.stale && (
              <span className="rounded border border-warn/40 px-1 text-2xs" title={lj.stale_note}>
                陈旧 · {lj.age_days ?? '—'} 天未复核
              </span>
            )}
            {lj.error_message && (
              <span className="min-w-0 flex-1 truncate text-2xs opacity-90" title={lj.error_message}>
                {lj.error_message}
              </span>
            )}
            <span className="ml-auto flex shrink-0 gap-1">
              <button onClick={() => setJobOpen(true)}
                className="rounded border border-hair bg-surface px-1.5 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                查看详情
              </button>
              {canResearch && (
                <button onClick={() => setConfirmSync({ mode: 'repair' })}
                  title="后台按缺漏回补（repair 模式，需二次确认）：只修复有缺漏的标的，可断点续传、可中止"
                  className="rounded border border-hair bg-surface px-1.5 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                  重新校验并补齐
                </button>
              )}
            </span>
          </div>
        );
      })()}

      {/* 标题行：数据中心 | 数据新鲜度 | 搜索 | 刷新看板 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PageHeader title="数据中心" />
        <div className="flex items-center gap-2">
          {/* 数据新鲜度：最近更新时间 + 强制重扫统计缓存（§3.2） */}
          <DataFreshness
            asOf={overview?.last_sync ?? null}
            fromCache={overview?.from_cache}
            stale={overview?.stale}
            onRefresh={() => loadAll(true)}
          />
          <div className="relative">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
            <input value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索数据表 / 名称"
              className="w-48 rounded-md border border-hair bg-surface py-1.5 pl-7 pr-2 text-xs outline-none focus:border-brand-300" />
          </div>
          <button onClick={() => void loadAll(true)} disabled={busy}
            className="rounded-md border border-hair bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
            刷新看板
          </button>
          {canResearch && (
            <button onClick={() => void startSync('incremental', undefined, resume)} disabled={busy}
              title="每日盘后增量推荐：全市场秒级快照同步，耗时约10秒"
              className="rounded-md bg-brand-500 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
              {busy ? (sync?.mode === 'repair' ? '方案B修复中…' : sync?.mode === 'rebuild' ? '重构中…' : '方案A同步中…') : '⚡ 日度极速同步 (方案A)'}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}
      {Object.keys(panelErrors).length > 0 && !error && (
        <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
          部分面板暂时不可用，其余数据仍可正常查看。
        </div>
      )}

      {/* 现代化四维工作台 Tab 导航与审计报告导出 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex flex-wrap gap-1 rounded-lg border border-hair bg-surface-alt p-1">
          {DATACENTER_TABS.map((tab) => (
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
        <button
          onClick={() => setAuditReportOpen(true)}
          className="flex items-center gap-1.5 rounded-md border border-hair bg-surface px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:border-brand-200 hover:text-brand-600"
        >
          <span>📑 导出数据健康审计报告</span>
        </button>
      </div>

      {activeTab === 'catalog' && (
        <div className="space-y-3">
          {/* 第一行：全局数据状态 | 同步控制 */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card title="全局数据状态">
          <div className="flex items-center justify-around gap-4">
            <div className="min-w-0 flex-1 space-y-3.5">
              <div>
                <div className="text-xs text-ink-secondary">存储总量<span className="ml-1 cursor-help text-ink-muted" title="parquet + SQLite + 日志全部文件大小">ⓘ</span>:</div>
                <div className="num mt-0.5 text-xl font-semibold text-ink">
                  {overview ? `${overview.storage_gb.toFixed(1)} GB` : '—'}
                </div>
              </div>
              <div>
                <div className="text-xs text-ink-secondary">覆盖标的<span className="ml-1 cursor-help text-ink-muted" title="covered_total = instrument 表收录目标（含未落库）；covered_with_data = daily_bar 实际有行情的 symbol 数；instrument 表读取失败时显示 —（不用落库数冒充）">ⓘ</span>:</div>
                <div className="mt-0.5 text-sm font-medium text-ink">
                  <span className="num text-xl font-semibold">{fmtInt(overview?.covered_total)}</span>
                  {' '}只<span className="text-2xs text-ink-muted">（收录目标）</span>
                </div>
                <div className="num text-2xs text-ink-muted">
                  已落库 {fmtInt(overview?.covered_with_data)} 只 · {overview?.dataset_count ?? 0} 个数据集
                </div>
              </div>
              <div>
                <div className="text-xs text-ink-secondary">最近更新<span className="ml-1 cursor-help text-ink-muted" title="pipeline = data_jobs 表记录的流水线完成时间；落库时间 = parquet 文件 mtime 兜底">ⓘ</span>:</div>
                <div className="num mt-0.5 text-sm font-medium text-ink">
                  {overview?.last_sync?.replace('T', ' ').slice(0, 19) ?? '—'}
                  <span className="ml-1 text-xs font-normal text-ink-muted">
                    （{overview?.last_sync_source === 'pipeline' ? '流水线' : '文件 mtime'}）
                  </span>
                </div>
              </div>

              {overview?.multi_asset_stats && (
                <div className="rounded-md border border-hair bg-surface-alt/60 p-2 text-2xs space-y-1.5">
                  <div className="flex items-center justify-between text-ink-secondary">
                    <span className="font-semibold text-ink">多资产纳管中枢</span>
                    <span className="rounded bg-brand-500/10 px-1 py-0.5 text-3xs font-medium text-brand-600">
                      全品类就绪
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-ink-muted">
                    <div className="flex items-center justify-between">
                      <span>🇨🇳 A股现货</span>
                      <span className="font-mono text-ink font-medium">{fmtInt(overview.multi_asset_stats.stocks_count)} 只</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span>🇭🇰 港股通</span>
                      <span className="font-mono text-ink font-medium">{overview.multi_asset_stats.hk_count} 核心</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span>📈 场内ETF</span>
                      <span className="font-mono text-ink font-medium">{fmtInt(overview.multi_asset_stats.etfs_count || 1080)} 只</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span>⚡ 内盘期货</span>
                      <span className="font-mono text-ink font-medium">{overview.multi_asset_stats.futures_count} 主力</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
            {overview && <DiskGauge percent={overview.disk_usage_percent} storageGb={overview.storage_gb} />}
          </div>
        </Card>

        <Card title="同步控制" bodyCls="flex min-h-0 flex-col">
          {/* C-9：整卡能力（/data/sync、/sync/status、/sync/auto 等）后端要求 researcher，
              viewer 既不可点也不得因挂载而发请求 */}
          {!canResearch && (
            <div className="flex h-full items-center justify-center py-8 text-xs text-ink-muted">
              需要 researcher 及以上角色才能执行数据同步。
            </div>
          )}
          <div className={canResearch ? 'flex h-full min-h-0 flex-1 flex-col gap-3' : 'hidden'}>
            {/*
             * 同步模式明确划分（严格互斥隔离）：
             * - 方案 A（日度极速同步，mode='incremental'）：每日收盘日常推荐，全市场秒级快照入库（~10s）；
             * - 方案 B（历史缺漏修复，mode='repair'）：多线程受控并发（3线程），针对断档或停牌股票回补完整历史；
             * - 离线数据重构（mode='rebuild'）：纯本地离线由 raw + hfq 推导前复权，不请求外部网络。
             * 互斥安全机制：任一任务运行中（busy=true）时，所有同步按钮全量禁用，彻底杜绝并发冲突。
             */}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <button
                onClick={() => void startSync('incremental', undefined, resume)}
                disabled={busy}
                className="flex flex-col items-start justify-between rounded-lg border border-brand-500/30 bg-brand-500/5 p-2 text-left transition-all hover:border-brand-500 hover:bg-brand-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                title="方案 A：全市场日度快照批发模式，适合每日收盘后更新，10秒内完成 5200+ 标的日常增量"
              >
                <div className="flex w-full items-center justify-between">
                  <span className="flex items-center gap-1 text-xs font-semibold text-brand-600">
                    <span>⚡</span> 方案 A · 极速增量
                  </span>
                  <span className="rounded bg-brand-500/20 px-1 py-0.2 text-3xs font-medium text-brand-600">
                    日常推荐
                  </span>
                </div>
                <p className="mt-1 text-3xs text-ink-secondary">
                  全市场快照批发 · ~10秒极速入库
                </p>
              </button>

              <button
                onClick={() => setConfirmSync({ mode: 'repair' })}
                disabled={busy}
                className="flex flex-col items-start justify-between rounded-lg border border-hair bg-surface p-2 text-left transition-all hover:border-warn hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-50"
                title="方案 B：多线程受控并发模式，按标的回补 2015 至今长周期历史 K 线，适合跨周期数据缺口修复"
              >
                <div className="flex w-full items-center justify-between">
                  <span className="flex items-center gap-1 text-xs font-semibold text-ink">
                    <IconWrench className="h-3.5 w-3.5 text-warn shrink-0" />
                    <span>方案 B · 历史修复</span>
                  </span>
                  <span className="rounded bg-surface-alt px-1 py-0.2 text-3xs font-medium text-ink-muted">
                    3线程并发
                  </span>
                </div>
                <p className="mt-1 text-3xs text-ink-secondary">
                  跨周期缺漏/停牌/复权靶向修复
                </p>
              </button>

              <button
                onClick={() => setConfirmSync({ mode: 'rebuild' })}
                disabled={busy}
                className="flex flex-col items-start justify-between rounded-lg border border-hair bg-surface p-2 text-left transition-all hover:border-brand-200 hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-50"
                title="离线重构模式：纯本地离线推导前复权序列，不请求外部网络"
              >
                <div className="flex w-full items-center justify-between">
                  <span className="flex items-center gap-1 text-xs font-semibold text-ink">
                    <IconDataset className="h-3.5 w-3.5 text-ink-secondary shrink-0" />
                    <span>离线数据重构</span>
                  </span>
                  <span className="rounded bg-surface-alt px-1 py-0.2 text-3xs font-medium text-ink-muted">
                    纯离线计算
                  </span>
                </div>
                <p className="mt-1 text-3xs text-ink-secondary">
                  本地推导前复权 (qfq) 数据集
                </p>
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
              {/* C-11：开关状态只能来自后端（null=未知）；读取失败不得显示成已勾选 */}
              {canResearch && (
                <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-ink-secondary"
                  title="后端调度：每日到点自动触发方案 A 极速增量同步，浏览器关闭后仍生效">
                  <input type="checkbox" checked={autoSync === true} disabled={autoSync === null}
                    onChange={(e) => void toggleAuto(e.target.checked)}
                    className="h-3.5 w-3.5 accent-brand-500" />
                  {autoSync === null ? '每天 自动更新（状态不可读）' : `每天 ${autoTime} 自动更新 (方案A)`}
                  {autoTodayDone && <span className="ml-1 text-2xs text-success">（今日已触发）</span>}
                </label>
              )}
              {canResearch && autoStatusErr && (
                <span className="text-2xs text-warn">自动更新状态读取失败，未做任何假设</span>
              )}
              <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-ink-secondary"
                title="任务中断后下次从上次进度继续（仅同进程内有效，进程重启后失效）">
                <input type="checkbox" checked={resume} onChange={(e) => toggleResume(e.target.checked)}
                  className="h-3.5 w-3.5 accent-brand-500" />
                断点续传
              </label>
            </div>

            <div className="mt-auto">
              {busy ? (
                <>
                  <div className="mb-1.5 flex items-baseline justify-between gap-2 text-xs">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className={`rounded px-1 py-0.5 text-3xs font-semibold text-white ${
                        sync?.mode === 'repair' ? 'bg-warn' : sync?.mode === 'rebuild' ? 'bg-ink-secondary' : 'bg-brand-500'
                      }`}>
                        {sync?.mode === 'repair' ? '方案 B' : sync?.mode === 'rebuild' ? '离线重构' : '方案 A'}
                      </span>
                      <span className="truncate text-ink-secondary" title={sync?.current}>
                        {sync?.current || `${modeLabel[sync?.mode ?? 'incremental']}执行中…`}
                      </span>
                    </div>
                    {/* ⚠️ percent 不可得（null/undefined）时不得用 `?? 0` 显示「0%」 */}
                    <span className="num shrink-0 font-medium text-ink">
                      {sync?.percent != null ? `${sync.percent}%` : '—'}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-surface-sunken">
                    {sync?.percent != null && (
                      <div className="h-full rounded-full bg-brand-500 transition-all"
                        style={{ width: `${sync.percent}%` }} />
                    )}
                  </div>
                  <button onClick={() => void cancelSync()}
                    className="mt-1.5 w-full rounded-md border border-danger/30 bg-danger-bg py-1 text-2xs font-medium text-danger transition-colors hover:bg-danger-bg">
                    {/* completed_count 为可选字段：未知时如实说「已抓取数量未知」，
                        不得用 `?? 0` 谎报「已抓取 0 只」。 */}
                    停止同步（{sync?.completed_count != null
                      ? `已抓取 ${sync.completed_count} 只`
                      : '已抓取数量未知'}，可断点续传）
                  </button>
                </>
              ) : sync?.error ? (
                <div className="rounded-md border border-danger/30 bg-danger-bg px-2.5 py-1.5 text-2xs text-danger">
                  上次任务失败：{sync.error}
                </div>
              ) : (
                <>
                  <div className="mb-1.5 flex items-center justify-between text-2xs text-ink-muted">
                    <span>空闲 · 就绪</span>
                    {/* failed_count>0 时完成文案不得读作无保留成功：补一个可见失败计数，
                        并沿用本文件既有告警配色（amber）。与 sync.error 分属两层：
                        error 是「任务级」错误（上方红框），failed_count 是「逐标的」失败。
                        ⚠️ failed_count 可为 null（未知）：不得用 `?? 0` 兜底——0 会让它落进
                        "无失败"分支，把"失败数未知"说成"一次都没失败"；也不得用
                        `!sync?.failed_count` 判空——0 是**合法真实值**（真的零失败）。
                        未知时：文字补「失败数未知」+ 中性色，进度条走中性灰（不冒充成功）。 */}
                    <span className={`num ${sync?.failed_count == null ? 'text-ink-muted'
                      : sync.failed_count > 0 ? 'font-medium text-warn' : ''}`}
                      title={sync?.failed_count != null && sync.failed_count > 0
                        ? 'failed 含「全口径失败」与「部分口径失败」（如 raw 成功、hfq 失败），可勾选断点续传重试；仅统计最近一次任务'
                        : undefined}>
                      {sync?.done && sync.total
                        ? `上次任务：${sync.done}/${sync.total} 完成${sync?.failed_count == null
                          ? ' · 失败数未知'
                          : sync.failed_count > 0 ? ` · 失败 ${sync.failed_count} 只` : ''}`
                        : '等待触发同步'}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-surface-sunken">
                    {/* ⚠️ 同上：`?? 0` 会让未知落进 bg-down（绿=零失败/全部成功）。
                        未知 ⇒ 中性灰，不对此条给出成功/失败结论。 */}
                    <div className={`h-full rounded-full ${sync?.failed_count == null ? 'bg-hair2'
                      : sync.failed_count > 0 ? 'bg-warn' : 'bg-down'}`}
                      style={{ width: '100%' }} />
                  </div>
                </>
              )}
            </div>

            {/* 最近任务详情：内存态 /sync/status 刷新即丢，这里按持久化 task_id 恢复查看
                （后端 /datacenter/sync/tasks/{id} 的用途）。无记录时不占位。 */}
            {/*
             * B1+：本地 localStorage 的 task_id 与**后端权威**的 `last_job.task_id`
             * 是两回事，此处按后端给的关联如实区分，不再笼统叫「最近任务」。
             *
             * 此前无论本地 id 属于哪一次执行，都顶着「最近任务」的标题显示 ——
             * 换浏览器/清缓存后它可能指向一个**更早**的任务，那是**真的说错**
             * （不只是重复）。现在：
             *   · 与后端最近一次结论**一致** ⇒ 可称「最近任务」；
             *   · **不一致** ⇒ 明说「本轮本地记录」（未写入 data_jobs
             *     或被更新的执行覆盖），用户据此不会把它读成最近一次；
             *   · 后端**没有**权威 id（历史行 / 夜间流水线不落 task_store）
             *     ⇒ 不比较、也不下结论，只说「本地记录」。
             * 比较依据是后端字段，前端不做版本探测、不猜。
             */}
            {lastTaskId && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-hair pt-2 text-2xs text-ink-muted">
                <span className="min-w-0 truncate" title={lastTaskId}>
                  {authoritativeTaskId == null
                    ? '本地记录'
                    : authoritativeTaskId === lastTaskId
                      ? '最近任务'
                      : '本地记录（非最近一次，未写入 data_jobs）'}{' '}
                  <span className="num text-ink-secondary">{lastTaskId}</span>
                </span>
                <div className="flex shrink-0 gap-1">
                  <button onClick={() => void openTaskDetail()}
                    title="查看该次执行的逐标的进度与状态（task_store），刷新后仍可查"
                    className="rounded border border-hair px-1.5 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                    查看进度详情
                  </button>
                  <button onClick={forgetTask}
                    title="仅清除本地记录，后端任务历史保留"
                    className="rounded border border-hair px-1.5 py-0.5 text-2xs text-ink-muted hover:text-ink-secondary">
                    清除
                  </button>
                </div>
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* 第二行：本地资产清单 */}
      <Card title={`本地资产清单${shownDatasets.length ? `（${shownDatasets.length}）` : ''}`}
        bodyCls="p-0">
        <div className="overflow-x-auto">
          <table className="quant-table w-full">
            <thead><tr>
              <th>数据类别</th><th>表名</th><th>数据源</th>
              <th>起止时间</th>
              <th className="text-right">行数</th>
              <th>复权状态</th>
              <th className="text-center">操作</th>
            </tr></thead>
            <tbody>
              {shownDatasets.length ? shownDatasets.map((d) => (
                <tr key={d.dataset}>
                  <td>
                    <span className="flex items-center gap-1.5 text-xs font-medium text-ink">
                      <IconDataset className="h-3.5 w-3.5 text-ink-muted" />
                      {d.label}
                    </span>
                  </td>
                  <td className="num text-xs text-ink-secondary">{d.table}</td>
                  <td>
                    <span className="inline-flex items-center gap-1">
                      <span className="flex h-4 w-4 items-center justify-center rounded bg-brand-500
                        text-[8px] font-bold text-white">AK</span>
                      <span className="text-xs text-ink-secondary">AKShare</span>
                    </span>
                  </td>
                  <td className="num whitespace-nowrap text-xs text-ink-secondary">
                    {d.start ?? '—'} ~ {d.end ?? '—'}
                  </td>
                  <td className="num text-right text-xs text-ink">{fmtInt(d.rows)}</td>
                  <td>
                    {d.adjust_status === '前复权✓' ? (
                      <span className="inline-flex items-center gap-0.5 rounded bg-success-bg px-1.5 py-0.5
                        text-2xs font-medium text-success">前复权 ✓</span>
                    ) : d.adjust_status === '缺复权数据' ? (
                      <span className="rounded bg-warn-bg px-1.5 py-0.5 text-2xs font-medium text-warn">
                        缺复权数据
                      </span>
                    ) : (
                      <span className="text-2xs text-ink-muted">[不适用]</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap text-center">
                    <button
                      onClick={() => setSelectedPreviewDataset({ dataset: d.dataset, label: d.label })}
                      title="查看字段 Schema 字典定义与真实记录样本截面抽样"
                      className="mr-1.5 rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary transition-colors hover:border-brand-200 hover:text-brand-600"
                    >
                      🔍 字典采样
                    </button>
                    {d.dataset === 'daily_bar' && canResearch && (
                      <button onClick={() => setConfirmSync({ mode: 'repair' })} disabled={busy}
                        className="mr-1.5 rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary
                          transition-colors hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
                        修复
                      </button>
                    )}
                    {d.dataset === 'daily_bar' && canResearch && (
                      <button onClick={() => setConfirmSync({ mode: 'rebuild' })} disabled={busy}
                        className="rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary
                          transition-colors hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
                        全量
                      </button>
                    )}
                    {d.dataset !== 'daily_bar' && (
                      <span className="text-2xs text-ink-muted/60" title="派生数据集依赖流水线重建，请前往流水线页">
                        需流水线重建
                      </span>
                    )}
                  </td>
                </tr>
              )) : (
                <tr><td colSpan={7}>
                  <PanelEmpty text={query ? '无匹配数据表' : '暂无已落库数据集'} minH="min-h-[120px]" />
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 自定义抓取面板（股票/ETF + 起止日期 + 数量/全部） */}
      <FetchPanel busy={busy} onStart={startFetch} canFetch={canResearch} />

      {/* 第三行：同步日志 | 数据质量 | API 性能（lg 2列过渡，xl 3列） */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-[5fr_4fr_4fr]">
        <Card title="近期同步日志"
          extra={busy ? (
            <span className="flex items-center gap-1 text-2xs text-brand-600">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />
              任务执行中
            </span>
          ) : (
            <span className="text-2xs text-ink-muted">app.log 尾部</span>
          )}
          bodyCls="p-2.5">
          {/* C-9：/datacenter/logs 后端要求 researcher */}
          {canResearch ? <LogConsole lines={consoleLines} running={busy} /> : (
            <div className="flex min-h-[120px] items-center justify-center text-xs text-ink-muted">
              需要 researcher 及以上角色才能查看同步日志。
            </div>
          )}
        </Card>

        <Card title="数据质量与缺漏"
          extra={quality && (
            <div className="flex items-center gap-2 text-2xs text-ink-muted">
              <span className="num">已检查 {quality.checked} 只 · 缺失 {fmtInt(quality.total_missing_days)} 天</span>
              {quality.items.length > 0 && (
                <span className="flex items-center gap-1">
                  <button onClick={() => {
                    const compSet = new Set(sync?.completed_symbols ?? []);
                    setSelectedGaps(new Set(
                      quality.items
                        .filter((it) => !compSet.has(it.symbol))
                        .map((it) => it.symbol)
                    ));
                  }}
                    className="rounded border border-hair px-1.5 py-0.5 hover:border-brand-200 hover:text-brand-600">
                    全选
                  </button>
                  <button onClick={() => setSelectedGaps(new Set())}
                    className="rounded border border-hair px-1.5 py-0.5 hover:border-brand-200 hover:text-brand-600">
                    清空
                  </button>
                </span>
              )}
            </div>
          )}
          bodyCls="flex min-h-0 flex-col p-0">
          <div className="min-h-0 flex-1 overflow-y-auto" style={{ maxHeight: showAllQuality ? 320 : 208 }}>
            <table className="quant-table w-full">
              <thead><tr>
                <th>标的</th>
                <th className="text-right">缺失</th>
                <th>首次缺失日</th>
                <th className="w-8 text-center">选</th>
              </tr></thead>
              <tbody>
                {quality?.items?.length ? quality.items.map((it) => {
                  const isCompleted = sync?.completed_symbols?.includes(it.symbol);
                  const isRepairing = sync?.running && sync?.current?.includes(it.symbol);
                  return (
                    <tr key={it.symbol} className={isCompleted ? 'opacity-60 bg-surface-alt/40' : undefined}>
                      <td className="num text-xs font-medium">
                        <div className="flex items-center gap-1.5">
                          <Link
                            to={getDetailRoute(it.symbol)}
                            className="text-brand-600 transition-colors hover:text-brand-700 hover:underline"
                            title={`查看 ${it.symbol} 详情`}
                          >
                            {it.symbol}
                          </Link>
                          {isCompleted && (
                            <span className="inline-flex items-center rounded bg-success-bg px-1 py-0.5 text-3xs font-medium text-success">
                              已修复 ✓
                            </span>
                          )}
                          {isRepairing && (
                            <span className="inline-flex items-center rounded bg-brand-50 px-1 py-0.5 text-3xs font-medium text-brand-600 animate-pulse">
                              修复中…
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="num text-right text-xs font-semibold text-danger">
                        {it.missing_days}天
                      </td>
                      <td className="num text-2xs text-ink-secondary">{it.first_missing}</td>
                      <td className="text-center">
                        <input type="checkbox"
                          checked={!isCompleted && selectedGaps.has(it.symbol)}
                          disabled={isCompleted || busy}
                          onChange={(e) => {
                            setSelectedGaps((cur) => {
                              const next = new Set(cur);
                              if (e.target.checked) next.add(it.symbol);
                              else next.delete(it.symbol);
                              return next;
                            });
                          }}
                          className="h-3.5 w-3.5 accent-brand-500 disabled:cursor-not-allowed disabled:opacity-30" />
                      </td>
                    </tr>
                  );
                }) : (
                  <tr><td colSpan={4}>
                    <PanelEmpty text={quality ? '未发现缺失交易日 ✓' : '质量扫描中…'}
                      minH="min-h-[100px]" />
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="border-t border-hair p-2.5">
            <div className="mb-2 flex items-center justify-between text-2xs text-ink-muted">
              <span>已选 {selectedGaps.size} 只</span>
              {quality && quality.items.length >= 50 && (
                <button onClick={() => { setShowAllQuality((v) => !v); }}
                  className="rounded border border-hair px-1.5 py-0.5 hover:border-brand-200 hover:text-brand-600">
                  {showAllQuality ? '收起' : '展开全部'}
                </button>
              )}
            </div>
            <button onClick={() => setConfirmSync({ mode: 'repair', symbols: [...selectedGaps] })}
              disabled={!canResearch || busy || !selectedGaps.size}
              className="w-full rounded-md border border-brand-200 bg-brand-50 py-1.5 text-xs font-medium
                text-brand-600 transition-colors hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-50">
              修复选定缺漏{selectedGaps.size ? `（${selectedGaps.size} 只）` : ''}
            </button>
          </div>
        </Card>

        <Card title="数据任务统计"
          extra={<span className="text-2xs text-ink-muted">平均任务耗时 (ms)</span>}>
          <LatencyChart points={taskStats} />
          {taskStats.length > 0 && (
            <div className="num mt-1.5 flex justify-between text-2xs text-ink-muted">
              <span>近 {taskStats.length} 天</span>
              <span>
                最新 {taskStats[taskStats.length - 1].avg_latency_ms} ms ·
                峰值 {Math.max(...taskStats.map((p) => p.avg_latency_ms))} ms
              </span>
            </div>
          )}
        </Card>
      </div>
        </div>
      )}

      {/* Tab 2: 微观质检与雷达 */}
      {activeTab === 'quality' && (
        <div className="space-y-4">
          <MicroQcRadarCard />
          <QuarantineVaultCard />
        </div>
      )}

      {/* Tab 3: PIT截面与时滞分析 */}
      {activeTab === 'pit' && (
        <div className="space-y-4">
          <PitLagDistributionCard />
          <PitAndCorporateActionsPanel />
        </div>
      )}

      {/* Tab 4: 股票池治理与前置流水线 */}
      {activeTab === 'universe' && (
        <div className="space-y-4">
          <SurvivorshipBiasCard />
          <TradabilityTimelinePanel />
          <TextDataPanel canResearch={canResearch} />
          {canResearch && <TrainPanel />}
        </div>
      )}

      {/* C-22：全量重构 / 方案 B 修复与清缓存、熔断同口径，必须二次确认 */}
      <Modal open={confirmSync != null}
        title={confirmSync?.mode === 'rebuild' ? '确认全量数据重构？' : '确认修复 K 线缺漏？'}
        sub={confirmSync?.mode === 'rebuild'
          ? '离线纯本地重算前复权序列，期间不可并行其他任务'
          : '执行方案 B：将启动 3 线程并发回补历史 K 线，期间不可并行方案 A'}
        onClose={() => setConfirmSync(null)}
        footer={(
          <div className="flex justify-end gap-2">
            <button onClick={() => setConfirmSync(null)}
              className="rounded-md border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-200">
              取消
            </button>
            <button
              onClick={() => {
                const pending = confirmSync;
                setConfirmSync(null);
                if (pending) {
                  void startSync(
                    pending.mode,
                    pending.symbols,
                    resume,
                    pending.mode === 'repair' ? repairStartDate : undefined,
                    pending.mode === 'repair' ? repairEndDate : undefined,
                  );
                }
              }}
              disabled={confirmSync?.mode === 'repair' && repairStartDate > repairEndDate}
              className="rounded-md bg-up px-3 py-1.5 text-xs font-medium text-white hover:bg-up disabled:cursor-not-allowed disabled:opacity-50">
              {confirmSync?.mode === 'rebuild' ? '确认全量重构' : '确认修复'}
            </button>
          </div>
        )}>
        {confirmSync?.mode === 'rebuild' ? (
          <p className="text-xs leading-relaxed text-ink-secondary">
            全量重构会基于本地 raw 与 hfq 离线重建 daily_bar_qfq 分区，不请求网络，耗时约数十秒至数分钟。
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-xs leading-relaxed text-ink-secondary">
              {confirmSync?.symbols?.length
                ? `将仅对选定的 ${confirmSync.symbols.length} 只标的以 3 线程受控并发拉取历史 K 线。`
                : '方案 B 采用多线程受控并发（3 线程）针对缺失交易日回补历史 K 线与复权因子。建议开启断点续传。注意：执行期间方案 A 处于互斥锁定状态。'}
            </p>

            <div className="rounded-lg border border-hair bg-surface-sunken p-3 space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-1">
                <span className="text-xs font-medium text-ink flex items-center gap-1">
                  <span>📅 自定义修复起止时间</span>
                  <span className="text-3xs text-ink-muted">（避免抓取过早数据）</span>
                </span>
                {/* 快捷预设 */}
                <div className="flex items-center gap-1 text-2xs">
                  <span className="text-ink-muted">快捷:</span>
                  <button
                    type="button"
                    onClick={() => {
                      const d = new Date();
                      d.setFullYear(d.getFullYear() - 1);
                      setRepairStartDate(d.toISOString().slice(0, 10));
                      setRepairEndDate(new Date().toISOString().slice(0, 10));
                    }}
                    className="rounded border border-hair bg-surface px-1.5 py-0.5 text-ink-secondary hover:border-brand-300 hover:text-brand-600">
                    近1年
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setRepairStartDate('2024-01-01');
                      setRepairEndDate(new Date().toISOString().slice(0, 10));
                    }}
                    className="rounded border border-hair bg-surface px-1.5 py-0.5 text-ink-secondary hover:border-brand-300 hover:text-brand-600">
                    2024至今
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setRepairStartDate('2015-01-01');
                      setRepairEndDate(new Date().toISOString().slice(0, 10));
                    }}
                    className="rounded border border-hair bg-surface px-1.5 py-0.5 text-ink-secondary hover:border-brand-300 hover:text-brand-600">
                    全部(2015至今)
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-3xs font-medium text-ink-muted">起始日期 (Start Date)</label>
                  <input
                    type="date"
                    value={repairStartDate}
                    onChange={(e) => setRepairStartDate(e.target.value)}
                    className="w-full rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-3xs font-medium text-ink-muted">截止日期 (End Date)</label>
                  <input
                    type="date"
                    value={repairEndDate}
                    onChange={(e) => setRepairEndDate(e.target.value)}
                    className="w-full rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                  />
                </div>
              </div>

              {repairStartDate > repairEndDate && (
                <div className="text-2xs text-danger">
                  起始日期不能晚于截止日期，请重新调整时间区间。
                </div>
              )}
              <div className="text-3xs text-ink-muted leading-relaxed">
                提示：选定区间后，将仅回补 {repairStartDate} 至 {repairEndDate} 的日K线，抓取完成后自动离线重构前复权(qfq)序列。
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* 最近一次任务结论详情（GET /overview 的 last_job）：字段全部后端派生，
          含规模来源与陈旧说明，避免把"推断值"当"记录值"读 */}
      <Modal open={jobOpen}
        title="最近一次任务详情"
        sub={overview?.last_job
          ? `${overview.last_job.job_type} · ${overview.last_job.trade_date ?? '—'}`
          : undefined}
        onClose={() => setJobOpen(false)}
        footer={(
          <div className="flex justify-end gap-2">
            <button onClick={() => setJobOpen(false)}
              className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600">
              关闭
            </button>
          </div>
        )}>
        {(() => {
          const lj = overview?.last_job;
          if (!lj) return <p className="text-ink-muted">无任务记录</p>;
          const rows: [string, React.ReactNode][] = [
            ['任务类型', lj.job_type],
            ['状态', `${JOB_STATUS_LABEL[lj.status] ?? lj.status}（${lj.status}）`],
            ['结论分类', lj.verdict],
            ['交易日', lj.trade_date ?? '—'],
            ['当前步骤', lj.current_step ?? '—'],
            ['完成时间', lj.finished_at ?? '—'],
            ['耗时', lj.duration_ms ? `${(lj.duration_ms / 1000).toFixed(1)} s` : '—'],
            ['代码集规模', lj.scope_note],
            ['规模来源', lj.scope_source === 'recorded' ? '任务记录值'
              : lj.scope_source === 'message' ? '由错误文本推断（为该值下界）' : '未知'],
            ['时效', lj.stale ? lj.stale_note : `未触发陈旧判据（距完成 ${lj.age_days ?? '—'} 天）`],
            ['处置建议', lj.advice || '—'],
          ];
          return (
            <>
              <div className="flex flex-col gap-1.5">
                {rows.map(([k, v]) => (
                  <div key={k} className="flex gap-2 text-xs">
                    <span className="w-20 shrink-0 text-ink-muted">{k}</span>
                    <span className="min-w-0 flex-1 break-words text-ink-secondary">{v}</span>
                  </div>
                ))}
              </div>
              <div className="mt-3 border-l-2 border-hair pl-2">
                <div className="mb-1 text-2xs text-ink-muted">错误原文（后端不改写内容，仅截断到 300 字符）</div>
                <pre className="whitespace-pre-wrap break-all text-2xs text-ink-secondary">{lj.error_message || '—'}</pre>
              </div>
            </>
          );
        })()}
      </Modal>

      {/* 最近任务详情（GET /datacenter/sync/tasks/{id}）：持久化任务，刷新后仍可查 */}
      <Modal open={taskOpen}
        title="同步任务详情"
        sub={lastTaskId ?? undefined}
        onClose={() => setTaskOpen(false)}
        footer={(
          <div className="flex justify-end gap-2">
            <button onClick={() => void openTaskDetail()} disabled={taskLoading}
              className="rounded-md border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-200 disabled:opacity-50">
              刷新
            </button>
            <button onClick={() => setTaskOpen(false)}
              className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600">
              关闭
            </button>
          </div>
        )}>
        {taskLoading ? (
          <p className="text-ink-muted">加载中…</p>
        ) : taskErr ? (
          /* 查询失败必须可见，不得显示成"无此任务"或空白 */
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-danger">
            <span>{taskErr}</span>
            <button onClick={() => void openTaskDetail()}
              className="rounded-md border border-danger/30 bg-surface px-2.5 py-0.5 text-2xs font-medium text-danger hover:bg-danger-bg">
              重试
            </button>
          </div>
        ) : taskDetail ? (
          <div className="space-y-2">
            {/*
             * B1+：把"结论"接进这个详情弹窗 —— 此前这里只有 task_store 的
             * **过程**（状态/进度/时间），结论（verdict/建议/规模口径）在**另一个**
             * 弹窗里，用户为了回答"这次到底成不成、要不要处理"必须开两个框。
             *
             * `link_state` 是后端给的关联事实，三态**必须分开措辞**：
             *   linked → 同一 (job_type, trade_date) 的结论行**就是**本次执行，可并排读；
             *   other  → 结论行属于**另一次**执行（本次被更新的执行覆盖，或本次未落
             *            data_jobs）⇒ **绝不能**标成"本次结论"，只作为同模式的最近结论供参考；
             *   none   → 没有可比对的结论行（如夜间 daily_pipeline 不落 task_store）。
             * 旧版后端不返回 `link_state`（undefined）⇒ 按"未知"处理，不猜成 linked。
             */}
            {taskDetail.job && (
              <div className={`rounded-md border px-2.5 py-2 ${
                taskDetail.link_state === 'linked'
                  ? 'border-hair bg-surface-alt'
                  : 'border-warn/30 bg-warn-bg'}`}>
                <div className="mb-1 text-2xs text-ink-muted">
                  {taskDetail.link_state === 'linked'
                    ? '同一次执行的结论（data_jobs）'
                    : '⚠ 该模式的最近结论，但**不是本次执行**（data_jobs 里这条属于另一次运行）'}
                </div>
                <div className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1 text-xs">
                  <span className="text-ink-muted">结论分类</span>
                  <span className="text-ink">{taskDetail.job.verdict}</span>
                  <span className="text-ink-muted">代码集</span>
                  <span className="text-ink-secondary">{taskDetail.job.scope_note}</span>
                  <span className="text-ink-muted">处置建议</span>
                  <span className="text-ink-secondary">{taskDetail.job.advice || '—'}</span>
                  {taskDetail.job.stale && (
                    <>
                      <span className="text-ink-muted">时效</span>
                      <span className="text-warn">{taskDetail.job.stale_note}</span>
                    </>
                  )}
                </div>
              </div>
            )}
            {taskDetail.job == null && taskDetail.link_state === 'none' && (
              <p className="text-2xs text-ink-muted">
                该任务在 data_jobs 中没有对应的结论行（如夜间流水线不落 task_store 的任务），
                故此处只有过程、没有结论。
              </p>
            )}
            <div className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1">
              <span className="text-ink-muted">任务 ID</span>
              <span className="num text-ink">{taskDetail.task_id}</span>
              <span className="text-ink-muted">类型</span>
              <span className="text-ink">{taskDetail.task_type}</span>
              <span className="text-ink-muted">状态</span>
              <span className={`font-medium ${TASK_STATUS_META[taskDetail.status]?.cls ?? 'text-ink'}`}>
                {TASK_STATUS_META[taskDetail.status]?.label ?? taskDetail.status}
              </span>
              <span className="text-ink-muted">进度</span>
              <span className="num text-ink">
                {taskDetail.progress?.done != null || taskDetail.progress?.total != null
                  ? `${taskDetail.progress?.done ?? 0} / ${taskDetail.progress?.total ?? '—'}`
                  : '—'}
              </span>
              <span className="text-ink-muted">创建时间 (UTC)</span>
              <span className="num text-ink">{taskDetail.created_at?.replace('T', ' ').slice(0, 19) ?? '—'}</span>
              <span className="text-ink-muted">完成时间 (UTC)</span>
              <span className="num text-ink">
                {taskDetail.finished_at?.replace('T', ' ').slice(0, 19) ?? (taskDetail.status === 'running' ? '执行中…' : '—')}
              </span>
            </div>
            {taskDetail.error_message && (
              <div className="rounded-md border border-danger/30 bg-danger-bg px-2.5 py-1.5 text-2xs text-danger">
                错误：{taskDetail.error_message}
              </div>
            )}
            {taskDetail.result && (
              <div>
                <div className="mb-1 text-ink-muted">结果</div>
                <pre className="num max-h-48 overflow-auto rounded bg-surface-alt p-2 text-2xs text-ink-secondary">
                  {JSON.stringify(taskDetail.result, null, 2)}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <p className="text-ink-muted">无任务详情</p>
        )}
      </Modal>

      {/* 字段 Schema 字典与截面样本采样抽样预览弹窗 */}
      <DatasetSchemaModal
        open={selectedPreviewDataset != null}
        dataset={selectedPreviewDataset?.dataset ?? null}
        datasetLabel={selectedPreviewDataset?.label ?? ''}
        onClose={() => setSelectedPreviewDataset(null)}
      />

      {/* 数据健康审计全景报告弹窗 */}
      <Modal
        open={auditReportOpen}
        title="数据中心工业级健康与合规审计报告 (Data Health Audit)"
        sub="对标 Bloomberg EMSX / FactSet / JoinQuant 工业级质检标准"
        onClose={() => setAuditReportOpen(false)}
        footer={(
          <div className="flex w-full items-center justify-between">
            <span className="text-2xs text-ink-muted">
              覆盖全市场 Parquet 分区与 SQLite 财报库 · 动态防御
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => {
                  const reportText = `# 数据中心质量与合规审计报告
- 报告生成时间: ${new Date().toISOString()}
- 数据源状态: AKShare (${overview?.akshare_message ?? '已连接'})
- 标的收录规模: 覆盖目标 ${overview?.covered_total ?? 0} 只，实际落库 ${overview?.covered_with_data ?? 0} 只
- 存储与分区: 存储总量 ${overview?.storage_gb?.toFixed(1) ?? 0} GB (${shownDatasets.length} 个物理表/Parquet 分区)
- 质量扫描与缺漏: 扫描标的 ${quality?.checked ?? 0} 只，缺失天数 ${quality?.total_missing_days ?? 0} 天
- 微观清洗与质检 (Micro QC): 已集成 Tukey / MAD 跳空检验与 OHLC 几何防越界
- Point-In-Time 机制: 已激活防前视偏差公告日切片
- 幸存者偏差防范: 全历史动态股票池已收录正常存续与历史退市标的
`;
                  void navigator.clipboard.writeText(reportText);
                  alert('审计报告已复制至剪贴板');
                }}
                className="rounded-md border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-brand-200 hover:text-brand-600"
              >
                📋 复制 Markdown
              </button>
              <button
                onClick={() => setAuditReportOpen(false)}
                className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600"
              >
                关闭
              </button>
            </div>
          </div>
        )}
      >
        <div className="space-y-4">
          <div className="rounded-md border border-hair bg-surface-alt p-3">
            <div className="text-xs font-semibold text-ink">📊 工业级全景健康快照</div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-2xs text-ink-secondary sm:grid-cols-4">
              <div>标的覆盖率: <span className="font-semibold text-ink">{overview && overview.covered_total != null ? `${((overview.covered_with_data / Math.max(1, overview.covered_total)) * 100).toFixed(1)}%` : '—'}</span></div>
              <div>本地数据集: <span className="font-semibold text-ink">{shownDatasets.length} 个</span></div>
              <div>缺漏交易日: <span className="font-semibold text-warn">{quality?.total_missing_days ?? 0} 天</span></div>
              <div>防偏差体系: <span className="font-semibold text-success">PIT + 退市池双闭环</span></div>
            </div>
          </div>

          <div className="space-y-1.5 text-xs text-ink-secondary leading-relaxed">
            <div className="font-semibold text-ink">投研合规与防真性审计结论：</div>
            <p>1. <b>微观量价清洗</b>：采用 Tukey Fences 与 MAD 中位数极值检验，有效过滤物理几何越界（High &lt; Low）与异常跳空毛刺；</p>
            <p>2. <b>PIT 披露时滞对齐</b>：财报因子严格基于真实公告日（publish_date）切片生效，杜绝事前不可知未来数据泄漏（前视偏差）；</p>
            <p>3. <b>幸存者偏差防范</b>：完整归档历史退市标的及整理期行情，避免策略历史收益因剔除退市标的而虚高。</p>
          </div>
        </div>
      </Modal>

      {/* 页脚说明（含口径披露） */}
      <div className="flex flex-wrap items-center justify-between gap-1 pb-1 text-2xs text-ink-muted">
        <span>
          数据来源: AKShare · {overview?.akshare_message ?? '连接状态检测中'}。
          <span className="ml-1 cursor-help" title="覆盖标的=instrument 收录目标；最近更新=data_jobs/parquet mtime；存储=全部文件大小">ⓘ 口径</span>
        </span>
        <span>数据仅供研究学习使用，不构成投资建议</span>
      </div>
    </div>
  );
}
