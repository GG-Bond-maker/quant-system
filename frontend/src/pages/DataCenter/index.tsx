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
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { ApiError } from '@/api/client';
import { datacenterApi, type SyncTaskDetail } from '@/api/datacenter';
import DataFreshness from '@/components/DataFreshness';
import { hasMinimumRole } from '@/components/RequireAuth';
import { Modal, PageHeader, PanelEmpty } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useAuthStore } from '@/stores/useAuthStore';
import TextDataPanel from './TextDataPanel';
import TrainPanel from './TrainPanel';
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

/** 持久化任务状态 → 展示文案 + 配色（与后端 task_store status 枚举一致） */
const TASK_STATUS_META: Record<string, { label: string; cls: string }> = {
  queued: { label: '排队中', cls: 'text-ink-secondary' },
  running: { label: '执行中', cls: 'text-brand-600' },
  succeeded: { label: '成功', cls: 'text-success' },
  failed: { label: '失败', cls: 'text-danger' },
  cancelled: { label: '已取消', cls: 'text-warn' },
};

/* ==================== 小工具 ==================== */
function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
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

/* ==================== 图表容器（同 ETF 页模式） ==================== */
function useChart(option: echarts.EChartsOption | null, height: number) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [option]);
  return { ref, height };
}

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
  const { ref } = useChart(option, 130);
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
  const { ref } = useChart(option, 150);
  if (!points.length) return <PanelEmpty text="暂无请求统计（执行一次同步后生成）" minH="min-h-[150px]" />;
  return <div ref={ref} style={{ height: 150 }} className="w-full" />;
}

/* ==================== 暗色日志控制台（可折叠：默认 h-52，展开 h-96） ==================== */
function LogConsole({ lines }: { lines: LogItem[] }) {
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
        )) : (
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
function IconRefresh({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 3v5h-5" />
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

  const typeLabel: Record<AssetType, string> = { stock: '股票', etf: 'ETF', all: '全部' };

  return (
    <Card title="自定义抓取" bodyCls="space-y-3"
      extra={<span className="text-2xs text-ink-muted">按类型/日期抓取行情落库 daily_bar</span>}>
      {/* 类型选择 */}
      <div className="flex items-center gap-1.5">
        <span className="text-2xs text-ink-secondary w-12">标的类型</span>
        <div className="flex gap-1">
          {(['stock', 'etf', 'all'] as AssetType[]).map((t) => (
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

  const [query, setQuery] = useState('');
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
  useEffect(() => {
    const active = canResearch && sync?.running;
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
        if (!st.running && wasRunning.current) {
          wasRunning.current = false;
          void loadAll(); // 任务结束：刷新统计（缓存已由后端失效）
        }
        wasRunning.current = st.running;
      } catch {
        // 轮询失败：指数退避 1.5s → 3s → 6s → 上限 12s，避免后端异常时持续放大请求
        if (stopped) return;
        delay = Math.min(delay * 2, MAX_MS);
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
  }, [sync?.running, loadAll, canResearch]);
  useEffect(() => { wasRunning.current = !!sync?.running; }, [sync?.running]);

  /* ---------- 触发同步 ---------- */
  const startSync = useCallback(async (mode: SyncMode, symbols?: string[], resumeFlag = false) => {
    setError(null);
    try {
      const r = await datacenterApi.sync(mode, symbols, resumeFlag);
      // 记录 task_id：刷新/重启后仍可按 id 查持久化任务详情
      if (r?.task_id) { writeLastTaskId(r.task_id); setLastTaskId(r.task_id); }
      const st = await datacenterApi.status();
      setSync(st);
      wasRunning.current = true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '同步任务启动失败');
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
    incremental: '增量更新', repair: '修复缺漏', rebuild: '全量重构',
  };

  return (
    <div className="flex min-h-full flex-col gap-3">
      {/* 顶部告警条：按 akshare_health 变色（green 转绿/隐藏，yellow 保留琥珀，red 加红边） */}
      {(() => {
        const h = overview?.akshare_health ?? 'yellow';
        const cls = h === 'green'
          ? 'border-success/30 bg-success-bg text-success'
          : h === 'red'
            ? 'border-danger/30 bg-danger-bg text-danger'
            : 'border-warn/30 bg-warn-bg text-warn';
        const icon = h === 'green' ? 'M9 12l2 2 4-4' : 'M12 3 2.5 20h19L12 3Z M12 10v4 M12 17.5v.5';
        return (
          <div className={`flex items-center gap-2 rounded-md border px-3 py-2 text-xs ${cls}`}>
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 shrink-0" fill="none"
              stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <path d={icon} />
            </svg>
            <span>
              API 接口状态: {overview?.akshare_message ?? '检测中…'}，
              {h === 'green' ? '数据同步正常' : '数据源可能不可用，请检查网络/代理或稍后重试同步'}。
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
              className="rounded-md bg-brand-500 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
              {busy ? '同步中…' : '开始同步'}
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
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
              <button onClick={() => void startSync('incremental', undefined, resume)} disabled={busy}
                className="flex min-w-0 items-center justify-center gap-1 rounded-md bg-brand-500 px-1.5 py-2
                  text-2xs font-medium text-white transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
                <IconRefresh className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">一键更新昨日数据</span>
              </button>
              <button onClick={() => setConfirmSync({ mode: 'repair' })} disabled={busy}
                className="flex min-w-0 items-center justify-center gap-1 rounded-md border border-hair bg-surface px-1.5 py-2
                  text-2xs text-ink transition-colors hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
                <IconWrench className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">修复K线缺漏</span>
              </button>
              <button onClick={() => setConfirmSync({ mode: 'rebuild' })} disabled={busy}
                className="flex min-w-0 items-center justify-center gap-1 rounded-md border border-hair bg-surface px-1.5 py-2
                  text-2xs text-ink transition-colors hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
                <IconDataset className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">全量数据重构</span>
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
              {/* C-11：开关状态只能来自后端（null=未知）；读取失败不得显示成已勾选 */}
              {canResearch && (
                <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-ink-secondary"
                  title="后端调度：每日到点自动触发增量同步，浏览器关闭后仍生效">
                  <input type="checkbox" checked={autoSync === true} disabled={autoSync === null}
                    onChange={(e) => void toggleAuto(e.target.checked)}
                    className="h-3.5 w-3.5 accent-brand-500" />
                  {autoSync === null ? '每天 自动更新（状态不可读）' : `每天 ${autoTime} 自动更新`}
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
                    <span className="truncate text-ink-secondary" title={sync?.current}>
                      {sync?.current || `${modeLabel[sync?.mode ?? 'incremental']}执行中…`}
                    </span>
                    <span className="num shrink-0 font-medium text-ink">{sync?.percent ?? 0}%</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-surface-sunken">
                    <div className="h-full rounded-full bg-brand-500 transition-all"
                      style={{ width: `${sync?.percent ?? 0}%` }} />
                  </div>
                  <button onClick={() => void cancelSync()}
                    className="mt-1.5 w-full rounded-md border border-danger/30 bg-danger-bg py-1 text-2xs font-medium text-danger transition-colors hover:bg-danger-bg">
                    停止同步（已抓取 {sync?.completed_count ?? 0} 只，可断点续传）
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
            {lastTaskId && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-hair pt-2 text-2xs text-ink-muted">
                <span className="min-w-0 truncate" title={lastTaskId}>
                  最近任务 <span className="num text-ink-secondary">{lastTaskId}</span>
                </span>
                <div className="flex shrink-0 gap-1">
                  <button onClick={() => void openTaskDetail()}
                    className="rounded border border-hair px-1.5 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                    查看详情
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
          {canResearch ? <LogConsole lines={consoleLines} /> : (
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
                  <button onClick={() => setSelectedGaps(new Set(quality.items.map((it) => it.symbol)))}
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
                {quality?.items?.length ? quality.items.map((it) => (
                  <tr key={it.symbol}>
                    <td className="num text-xs font-medium text-brand-600">{it.symbol}</td>
                    <td className="num text-right text-xs font-semibold text-danger">
                      {it.missing_days}天
                    </td>
                    <td className="num text-2xs text-ink-secondary">{it.first_missing}</td>
                    <td className="text-center">
                      <input type="checkbox" checked={selectedGaps.has(it.symbol)}
                        onChange={(e) => {
                          setSelectedGaps((cur) => {
                            const next = new Set(cur);
                            if (e.target.checked) next.add(it.symbol);
                            else next.delete(it.symbol);
                            return next;
                          });
                        }}
                        className="h-3.5 w-3.5 accent-brand-500" />
                    </td>
                  </tr>
                )) : (
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

      {/* 数据前置：公告文本（FinLLM）+ 截面分区镜像（MED-003） */}
      <TextDataPanel canResearch={canResearch} />

      {/* 模型训练（数据前置消费端：数据抓够后一键启动 TFT/GNN 训练）
          C-9：train/status 与 train/* 全部要求 researcher，
          此前 viewer 挂载即轮询 → 恒红框 + 40300 ⇒ 非 researcher 不渲染 */}
      {canResearch && <TrainPanel />}

      {/* C-22：全量重构 / 修复缺漏与清缓存、熔断同口径，必须二次确认 */}
      <Modal open={confirmSync != null}
        title={confirmSync?.mode === 'rebuild' ? '确认全量数据重构？' : '确认修复 K 线缺漏？'}
        sub="该操作耗时较长、会重写落库分区，且不可撤销"
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
                if (pending) void startSync(pending.mode, pending.symbols, resume);
              }}
              className="rounded-md bg-up px-3 py-1.5 text-xs font-medium text-white hover:bg-up">
              {confirmSync?.mode === 'rebuild' ? '确认全量重构' : '确认修复'}
            </button>
          </div>
        )}>
        <p className="text-xs leading-relaxed text-ink-secondary">
          {confirmSync?.mode === 'rebuild'
            ? '全量重构会重新抓取并覆盖 daily_bar 分区，期间不可并行其他同步任务，耗时可能很长。'
            : confirmSync?.symbols?.length
              ? `将仅对选定的 ${confirmSync.symbols.length} 只标的回补缺失交易日。`
              : '将按缺失交易日回补 K 线数据；建议同时开启断点续传。'}
        </p>
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
