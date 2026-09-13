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
import { ApiError } from '@/api/client';
import { datacenterApi } from '@/api/datacenter';
import DataFreshness from '@/components/DataFreshness';
import { PanelEmpty } from '@/components/ui';
import TextDataPanel from './TextDataPanel';
import TrainPanel from './TrainPanel';
import type {
  AssetType, AutoSyncConfig, FetchRequest, InstrumentItem,
  TaskStatPoint, DataOverview, DatasetItem, LogItem, QualityResult, SyncMode,
  SyncStatus,
} from '@/types/datacenter';

/* ==================== 常量 ==================== */
const DEFAULT_AUTO_TIME = '15:45';

/* ==================== 小工具 ==================== */
function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}

function Card({ title, extra, children, bodyCls = '' }: {
  title: string; extra?: React.ReactNode; children: React.ReactNode; bodyCls?: string;
}) {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
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
  const option = useMemo<echarts.EChartsOption>(() => ({
    series: [{
      type: 'gauge',
      startAngle: 210,
      endAngle: -30,
      min: 0,
      max: 100,
      radius: '95%',
      center: ['50%', '58%'],
      axisLine: { lineStyle: { width: 10, color: [[1, '#E2E8F0']] } },
      // 进度弧填充到当前值，指针+弧线双重指示，避免读数歧义
      progress: { show: true, width: 10, itemStyle: { color: '#3B82F6' } },
      pointer: { length: '55%', width: 3, itemStyle: { color: '#334155' } },
      axisTick: { show: false },
      splitLine: { length: 4, distance: -14, lineStyle: { color: '#94A3B8', width: 1 } },
      axisLabel: {
        distance: -26, fontSize: 9, color: '#94A3B8',
        formatter: (v: number) => (v === 0 || v === 100 ? `${v}%` : ''),
      },
      anchor: { show: true, size: 6, itemStyle: { color: '#334155' } },
      title: { show: false },
      detail: {
        valueAnimation: true, fontSize: 13, fontWeight: 600, color: '#0F172A',
        offsetCenter: [0, '55%'], formatter: '{value}%',
      },
      data: [{ value: Math.min(100, Math.max(0, percent)) }],
    }],
  }), [percent]);
  const { ref } = useChart(option, 130);
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
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!points.length) return null;
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: ['平均耗时(ms)', '任务次数'], top: 0, textStyle: { fontSize: 9, color: '#94A3B8' } },
      grid: { left: 6, right: 8, top: 22, bottom: 2, containLabel: true },
      xAxis: {
        type: 'category', data: points.map((p) => p.date.slice(5)),
        axisLabel: { fontSize: 9, color: '#94A3B8' },
        axisLine: { lineStyle: { color: '#E2E8F0' } }, axisTick: { show: false },
      },
      yAxis: [
        {
          type: 'value', name: 'ms', nameTextStyle: { fontSize: 9, color: '#94A3B8' },
          axisLabel: { fontSize: 9, color: '#94A3B8' },
          splitLine: { lineStyle: { color: '#F1F5F9' } },
        },
        {
          type: 'value', name: '次', nameTextStyle: { fontSize: 9, color: '#94A3B8' },
          axisLabel: { fontSize: 9, color: '#94A3B8' }, splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '任务次数', type: 'bar', yAxisIndex: 1,
          data: points.map((p) => p.calls),
          itemStyle: { color: 'rgba(148,163,184,0.4)' }, barWidth: '40%',
        },
        {
          name: '平均耗时(ms)', type: 'line', yAxisIndex: 0,
          data: points.map((p) => p.avg_latency_ms),
          smooth: true, symbolSize: 4, symbol: 'circle',
          lineStyle: { width: 2, color: '#3B82F6' }, itemStyle: { color: '#3B82F6' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(59,130,246,0.25)' },
              { offset: 1, color: 'rgba(59,130,246,0.02)' },
            ]),
          },
        },
      ],
    };
  }, [points]);
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
            <span className={`ml-1 ${l.level === 'ERROR' ? 'text-red-400'
              : l.level === 'WARNING' ? 'text-amber-400' : 'text-slate-300'}`}>
              [{l.level}]
            </span>
            <span className={l.level === 'WARNING' ? 'ml-1 text-amber-200/90'
              : l.level === 'ERROR' ? 'ml-1 text-red-300' : 'ml-1 text-slate-300'}>
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
function FetchPanel({ busy, onStart }: {
  busy: boolean;
  onStart: (req: FetchRequest) => void;
}) {
  const [assetType, setAssetType] = useState<AssetType>('stock');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [limit, setLimit] = useState<number | ''>('');
  const [symbolsText, setSymbolsText] = useState('');
  const [preview, setPreview] = useState<InstrumentItem[]>([]);
  const [previewTotal, setPreviewTotal] = useState(0);

  // 今日日期兜底 end 默认值
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    if (!end) setEnd(today);
    if (!start) setStart(today.slice(0, 4) + '-01-01');
  }, []);

  // 类型切换时加载预览（前 50 只）
  useEffect(() => {
    void datacenterApi.instruments(assetType, 50).then((r) => {
      setPreview(r.items); setPreviewTotal(r.total);
    }).catch(() => { setPreview([]); setPreviewTotal(0); });
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
                  : 'border border-hair bg-white text-ink-secondary hover:border-brand-200 hover:text-brand-600'
              }`}>
              {typeLabel[t]}
            </button>
          ))}
        </div>
        <span className="ml-auto num text-2xs text-ink-muted">
          {previewTotal > 0 ? `可抓取 ${previewTotal} 只` : ''}
        </span>
      </div>

      {/* 起止日期 */}
      <div className="flex items-center gap-2">
        <span className="text-2xs text-ink-secondary w-12">起止日期</span>
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
          className="rounded border border-hair bg-white px-2 py-1 text-2xs outline-none focus:border-brand-300" />
        <span className="text-2xs text-ink-muted">~</span>
        <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
          className="rounded border border-hair bg-white px-2 py-1 text-2xs outline-none focus:border-brand-300" />
      </div>

      {/* 数量 / 代码列表（二选一） */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-2xs text-ink-secondary w-12">数量上限</span>
          <input type="number" min={1} value={limit}
            onChange={(e) => setLimit(e.target.value ? Number(e.target.value) : '')}
            placeholder="留空 = 全部"
            className="w-28 rounded border border-hair bg-white px-2 py-1 text-2xs outline-none focus:border-brand-300" />
          <span className="text-2xs text-ink-muted">
            （留空抓取 {typeLabel[assetType]} 全部；下面填代码则忽略此项）
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-2xs text-ink-secondary w-12 pt-1">指定代码</span>
          <textarea value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)}
            placeholder="留空按类型抓全部；或填代码（空格/逗号分隔），如 600519 000001 510300"
            rows={2}
            className="flex-1 rounded border border-hair bg-white px-2 py-1 text-2xs outline-none focus:border-brand-300" />
        </div>
      </div>

      {/* 预览（前 5 只名称） */}
      {preview.length > 0 && !symbolsText && (
        <div className="text-2xs text-ink-muted">
          预览：{preview.slice(0, 5).map((p) => `${p.symbol} ${p.name}`).join('，')}
          {previewTotal > 5 ? ` … 等 ${previewTotal} 只` : ''}
        </div>
      )}

      {/* 操作 */}
      <div className="flex gap-2">
        <button onClick={startFetch} disabled={busy || !start || !end}
          className="flex-1 rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
          {busy ? '抓取中…' : '开始抓取'}
        </button>
        <button onClick={() => { setSymbolsText(''); setLimit(''); }}
          className="rounded-md border border-hair bg-white px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-brand-200 hover:text-brand-600">
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

  const [query, setQuery] = useState('');
  // autoSync 改后端调度：前端只展示状态，不再 setInterval 触发
  const [autoSync, setAutoSync] = useState(true);
  const [autoTime, setAutoTime] = useState(DEFAULT_AUTO_TIME);
  const [autoTodayDone, setAutoTodayDone] = useState(false);
  const [resume, setResume] = useState(false);
  const qualityLimit = 50; // 质量表默认分页：50 / 展开后 10000
  const [showAllQuality, setShowAllQuality] = useState(false);
  const [selectedGaps, setSelectedGaps] = useState<Set<string>>(new Set());
  const runningRef = useRef(false);

  /* ---------- autoSync 配置加载（后端持久化，替代 localStorage） ---------- */
  useEffect(() => {
    void datacenterApi.autoStatus().then((cfg: AutoSyncConfig) => {
      setAutoSync(cfg.enabled);
      setAutoTime(cfg.time);
      setAutoTodayDone(cfg.today_done);
    }).catch(() => { /* 降级用默认值 */ });
  }, []);

  /* ---------- 静态看板加载 ---------- */
  const loadAll = useCallback(async (refresh = false) => {
    const results = await Promise.allSettled([
      datacenterApi.overview(refresh), datacenterApi.datasets(),
      datacenterApi.quality(showAllQuality ? 10000 : qualityLimit),
      datacenterApi.logs(60), datacenterApi.taskStats(), datacenterApi.status(),
      datacenterApi.autoStatus(),
    ]);
    if (results[0].status === 'fulfilled') setOverview(results[0].value);
    if (results[1].status === 'fulfilled') setDatasets(results[1].value.items ?? []);
    if (results[2].status === 'fulfilled') setQuality(results[2].value);
    if (results[3].status === 'fulfilled') setFileLogs(results[3].value.items ?? []);
    if (results[4].status === 'fulfilled') setTaskStats(results[4].value.points ?? []);
    if (results[5].status === 'fulfilled') {
      setSync(results[5].value);
      runningRef.current = results[5].value.running;
    } else {
      setError(results[0].status === 'rejected'
        ? (results[0].reason instanceof ApiError ? results[0].reason.message : '后端服务不可用')
        : null);
    }
    if (results[6].status === 'fulfilled') {
      const cfg = results[6].value;
      setAutoSync(cfg.enabled); setAutoTime(cfg.time); setAutoTodayDone(cfg.today_done);
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
  }, [showAllQuality]);

  useEffect(() => { void loadAll(); }, [loadAll]);

  /* ---------- 同步任务轮询：运行中每 1.5s，结束后刷新全量看板 ---------- */
  const wasRunning = useRef(false);
  useEffect(() => {
    const active = sync?.running;
    if (!active) return;
    const timer = setInterval(async () => {
      try {
        const st = await datacenterApi.status();
        setSync(st);
        if (!st.running && wasRunning.current) {
          wasRunning.current = false;
          void loadAll(); // 任务结束：刷新统计（缓存已由后端失效）
        }
        wasRunning.current = st.running;
      } catch { /* 轮询失败忽略 */ }
    }, 1500);
    return () => clearInterval(timer);
  }, [sync?.running, loadAll]);
  useEffect(() => { wasRunning.current = !!sync?.running; }, [sync?.running]);

  /* ---------- 触发同步 ---------- */
  const startSync = useCallback(async (mode: SyncMode, symbols?: string[], resumeFlag = false) => {
    setError(null);
    try {
      await datacenterApi.sync(mode, symbols, resumeFlag);
      const st = await datacenterApi.status();
      setSync(st);
      wasRunning.current = true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '同步任务启动失败');
    }
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
    setAutoSync(on);
    try {
      const cfg = await datacenterApi.autoToggle(on, autoTime);
      setAutoSync(cfg.enabled); setAutoTime(cfg.time); setAutoTodayDone(cfg.today_done);
    } catch { /* 降级 */ }
  }, [autoTime]);

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
          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
          : h === 'red'
            ? 'border-red-200 bg-red-50 text-red-700'
            : 'border-amber-200 bg-amber-50 text-amber-700';
        const icon = h === 'green' ? 'M9 12l2 2 4-4' : 'M12 3 2.5 20h19L12 3Z M12 10v4 M12 17.5v.5';
        return (
          <div className={`flex items-center gap-2 rounded-md border px-3 py-2 text-xs ${cls}`}>
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 shrink-0" fill="none"
              stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
              <path d={icon} />
            </svg>
            <span>
              API 接口状态: {overview?.akshare_message ?? '检测中…'}，
              {h === 'green' ? '数据同步正常' : '部分高频接口建议开启本地缓存同步'}。
            </span>
          </div>
        );
      })()}

      {/* 标题行：数据中心 | 数据新鲜度 | 搜索 | 刷新看板 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-bold text-ink">数据中心</h1>
        <div className="flex items-center gap-2">
          {/* 数据新鲜度：最近更新时间 + 强制重扫统计缓存（§3.2） */}
          <DataFreshness
            asOf={overview?.last_sync ?? null}
            fromCache={overview?.from_cache}
            stale={overview?.refreshing}
            onRefresh={() => loadAll(true)}
          />
          <div className="relative">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
            <input value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索数据表 / 名称"
              className="w-48 rounded-md border border-hair bg-white py-1.5 pl-7 pr-2 text-xs outline-none focus:border-brand-300" />
          </div>
          <button onClick={() => void loadAll(true)} disabled={busy}
            className="rounded-md border border-hair bg-white px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
            刷新看板
          </button>
          <button onClick={() => void startSync('incremental', undefined, resume)} disabled={busy}
            className="rounded-md bg-brand-500 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
            {busy ? '同步中…' : '开始同步'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}
      {Object.keys(panelErrors).length > 0 && !error && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
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
          <div className="flex h-full min-h-0 flex-1 flex-col gap-3">
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
              <button onClick={() => void startSync('incremental', undefined, resume)} disabled={busy}
                className="flex min-w-0 items-center justify-center gap-1 rounded-md bg-brand-500 px-1.5 py-2
                  text-2xs font-medium text-white transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
                <IconRefresh className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">一键更新昨日数据</span>
              </button>
              <button onClick={() => void startSync('repair', undefined, resume)} disabled={busy}
                className="flex min-w-0 items-center justify-center gap-1 rounded-md border border-hair bg-white px-1.5 py-2
                  text-2xs text-ink transition-colors hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
                <IconWrench className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">修复K线缺漏</span>
              </button>
              <button onClick={() => void startSync('rebuild', undefined, resume)} disabled={busy}
                className="flex min-w-0 items-center justify-center gap-1 rounded-md border border-hair bg-white px-1.5 py-2
                  text-2xs text-ink transition-colors hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
                <IconDataset className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">全量数据重构</span>
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
              <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-ink-secondary"
                title="后端调度：每日到点自动触发增量同步，浏览器关闭后仍生效">
                <input type="checkbox" checked={autoSync} onChange={(e) => void toggleAuto(e.target.checked)}
                  className="h-3.5 w-3.5 accent-brand-500" />
                每天 {autoTime} 自动更新
                {autoTodayDone && <span className="ml-1 text-2xs text-emerald-600">（今日已触发）</span>}
              </label>
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
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-brand-500 transition-all"
                      style={{ width: `${sync?.percent ?? 0}%` }} />
                  </div>
                  <button onClick={() => void cancelSync()}
                    className="mt-1.5 w-full rounded-md border border-red-200 bg-red-50 py-1 text-2xs font-medium text-red-600 transition-colors hover:bg-red-100">
                    停止同步（已抓取 {sync?.completed_count ?? 0} 只，可断点续传）
                  </button>
                </>
              ) : sync?.error ? (
                <div className="rounded-md border border-red-100 bg-red-50 px-2.5 py-1.5 text-2xs text-red-600">
                  上次任务失败：{sync.error}
                </div>
              ) : (
                <>
                  <div className="mb-1.5 flex items-center justify-between text-2xs text-ink-muted">
                    <span>空闲 · 就绪</span>
                    <span className="num">
                      {sync?.done && sync.total
                        ? `上次任务：${sync.done}/${sync.total} 完成`
                        : '等待触发同步'}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-emerald-400"
                      style={{ width: '100%' }} />
                  </div>
                </>
              )}
            </div>
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
                      <span className="inline-flex items-center gap-0.5 rounded bg-emerald-50 px-1.5 py-0.5
                        text-2xs font-medium text-emerald-600">前复权 ✓</span>
                    ) : d.adjust_status === '缺复权数据' ? (
                      <span className="rounded bg-amber-50 px-1.5 py-0.5 text-2xs font-medium text-amber-600">
                        缺复权数据
                      </span>
                    ) : (
                      <span className="text-2xs text-ink-muted">[不适用]</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap text-center">
                    {d.dataset === 'daily_bar' && (
                      <button onClick={() => void startSync('repair', undefined, resume)} disabled={busy}
                        className="mr-1.5 rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary
                          transition-colors hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
                        修复
                      </button>
                    )}
                    {d.dataset === 'daily_bar' && (
                      <button onClick={() => void startSync('rebuild', undefined, resume)} disabled={busy}
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
      <FetchPanel busy={busy} onStart={startFetch} />

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
          <LogConsole lines={consoleLines} />
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
                    <td className="num text-right text-xs font-semibold text-red-500">
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
            <button onClick={() => void startSync('repair', [...selectedGaps], resume)}
              disabled={busy || !selectedGaps.size}
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
      <TextDataPanel />

      {/* 模型训练（数据前置消费端：数据抓够后一键启动 TFT/GNN 训练） */}
      <TrainPanel />

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
