/**
 * ETF 中心：市场概览 + 板块 Tab + ETF表现 / 热门TOP5 / 筛选器 / 资金流向 / 规模变化 / 自选。
 *
 * 数据覆盖：中国（东财全量 1337 只）/ 美国（腾讯，16 只）/
 *          日本・韩国（目录 8 + 6 只，行情源在本网络不可达，显示"暂无数据"）。
 * 自选 ETF 存 localStorage，与股票自选一致，无后端依赖。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { ApiError } from '@/api/client';
import { etfApi } from '@/api/etf';
import { PanelEmpty, Pager, SortHeader } from '@/components/ui';
import type {
  EtfFlowItem, EtfItem, EtfListResult, EtfPerformance, EtfScale,
  EtfCountry, EtfOverview,
} from '@/types/etf';
import { fmtNum, pctClass, fmtPct } from '@/utils/format';
import OverviewCards from './OverviewCards';
import PerformanceChart from './PerformanceChart';
import FilterPanel, { type EtfFilters } from './FilterPanel';

/* ==================== 常量 ==================== */
const COUNTRIES = [
  { key: 'all', label: '全部' },
  { key: 'cn', label: '中国' },
  { key: 'us', label: '美国' },
  { key: 'jp', label: '日本' },
  { key: 'kr', label: '韩国' },
] as const;

/** 榜单板块 Tab（与后端 classify_board 输出一致） */
const BOARDS = ['市场总览', '宽基ETF', '行业ETF', '主题ETF', 'Smart Beta', '跨境ETF', '商品型', '债券型', '货币型'];

const FLOW_PERIODS = [
  { key: '1d', label: '近1日' },
  { key: '5d', label: '近5日' },
  { key: '10d', label: '近10日' },
] as const;

const SCALE_PERIODS = [
  { key: '1m', label: '近1月' },
  { key: '3m', label: '近3月' },
  { key: '1y', label: '近1年' },
] as const;

const WATCH_KEY = 'AQP_ETF_WATCH';

/** 列表排序状态；null = 取消排序（sort 传空串，走后端默认顺序）。
 *  默认 size / desc，与改造前「sort 恒为 size」的行为保持一致。 */
type SortState = { key: string; dir: 'asc' | 'desc' } | null;
const DEFAULT_SORT: SortState = { key: 'size', dir: 'desc' };

/* ==================== 工具 ==================== */
function writeWatch(codes: string[]) {
  try { localStorage.setItem(WATCH_KEY, JSON.stringify(codes)); } catch { /* 隐私模式忽略 */ }
}
function readWatch(): string[] {
  try {
    const raw = localStorage.getItem(WATCH_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [];
  } catch { return []; }
}

function yi(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v / 1e8).toFixed(2)} 亿`;
}

function Card({ title, extra, children, bodyCls = '' }: {
  title: string; extra?: React.ReactNode; children: React.ReactNode; bodyCls?: string;
}) {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
        {extra}
      </div>
      <div className={`min-w-0 flex-1 p-2.5 ${bodyCls}`}>{children}</div>
    </div>
  );
}

function Tabs<T extends string>({ value, onChange, items }: {
  value: T; onChange: (v: T) => void; items: ReadonlyArray<{ key: T; label: string }>;
}) {
  return (
    <div className="flex flex-wrap items-center gap-0.5 rounded-md bg-slate-100 p-0.5">
      {items.map((it) => (
        <button key={it.key} onClick={() => onChange(it.key)}
          className={`rounded px-2 py-0.5 text-2xs transition-colors ${
            value === it.key ? 'bg-white font-medium text-brand-600 shadow-sm'
              : 'text-ink-muted hover:text-ink-secondary'}`}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

/**
 * 通用 ECharts 容器：挂载初始化、卸载销毁、窗口 resize 跟随。
 * height 仅作最小高度兜底，容器在 flex/grid 拉伸时图表跟随铺满。
 */
function Chart({ option, height = 200, empty = '暂无数据' }: {
  option: echarts.EChartsOption | null; height?: number; empty?: string;
}) {
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

  if (!option) return <div style={{ minHeight: height }} className="h-full"><PanelEmpty text={empty} minH="h-full" /></div>;
  return <div ref={ref} style={{ minHeight: height }} className="h-full w-full" />;
}

/* ==================== 主组件 ==================== */
export default function EtfCenter() {
  const navigate = useNavigate();
  const [overview, setOverview] = useState<EtfOverview | null>(null);
  const [listRes, setListRes] = useState<EtfListResult | null>(null);
  const [hot, setHot] = useState<EtfItem[]>([]);
  const [flow, setFlow] = useState<EtfFlowItem[]>([]);
  const [scale, setScale] = useState<EtfScale | null>(null);
  const [perf, setPerf] = useState<EtfPerformance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [country, setCountry] = useState<string>('all');
  const [board, setBoard] = useState('市场总览');
  const [filters, setFilters] = useState<EtfFilters>({
    etype: 'all', index: '', manager: '', min_size: '', max_size: '',
    inception_from: '', inception_to: '', q: '',
  });
  const [page, setPage] = useState(1);
  /** 服务端排序：涨跌幅 / 规模(亿) / 成交额 三列可点表头，三态（降序 → 升序 → 取消） */
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);

  const [flowPeriod, setFlowPeriod] = useState<'1d' | '5d' | '10d'>('1d');
  const [scalePeriod, setScalePeriod] = useState<'1m' | '3m' | '1y'>('1m');
  const [watch, setWatch] = useState<string[]>(() => readWatch());

  /* ---------- 概览 / 热门 ---------- */
  const loadBase = useCallback(async () => {
    const [o, h] = await Promise.allSettled([etfApi.overview(), etfApi.hot(10)]);
    if (o.status === 'fulfilled') setOverview(o.value);
    if (h.status === 'fulfilled') setHot(h.value.items ?? []);
  }, []);

  /* ---------- 列表 ---------- */
  const loadList = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const p: Record<string, unknown> = {
        country, page, page_size: 20,
        sort: sort?.key ?? '',
        dir: sort?.dir ?? 'desc',
      };
      if (board !== '市场总览') p.board = board;
      if (filters.etype !== 'all') p.etype = filters.etype;
      if (filters.index) p.index = filters.index;
      if (filters.manager) p.manager = filters.manager;
      if (filters.q) p.q = filters.q;
      if (filters.min_size) p.min_size = Number(filters.min_size);
      if (filters.max_size) p.max_size = Number(filters.max_size);
      if (filters.inception_from) p.inception_from = filters.inception_from;
      if (filters.inception_to) p.inception_to = filters.inception_to;
      setListRes(await etfApi.list(p as never));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败');
    } finally { setLoading(false); }
  }, [country, board, filters, page, sort]);

  /** 点击可排序列头：降序 → 升序 → 取消（与选股中心 Alpha 榜同语义） */
  const toggleSort = (key: string) => {
    setPage(1);   // 排序结果整体变化，停留在旧页码没有意义
    setSort((cur) => {
      if (cur?.key !== key) return { key, dir: 'desc' };
      if (cur.dir === 'desc') return { key, dir: 'asc' };
      return null;
    });
  };

  /* ---------- 资金流向 / 规模 ---------- */
  const loadFlow = useCallback(async () => {
    try { setFlow((await etfApi.flow(flowPeriod, 10)).items ?? []); } catch { setFlow([]); }
  }, [flowPeriod]);

  const loadScale = useCallback(async () => {
    try { setScale(await etfApi.scale(scalePeriod, 10)); } catch { setScale(null); }
  }, [scalePeriod]);

  /* ---------- ETF表现：默认取热门前 5 的代码 ---------- */
  const perfSymbols = useMemo(
    () => (hot.length ? hot.slice(0, 5).map((h) => h.code).join(',') : '510300,510500,159915'),
    [hot],
  );
  const [perfPeriod, setPerfPeriod] = useState('1y');
  const [perfMetric, setPerfMetric] = useState<'pct' | 'price'>('pct');
  const loadPerf = useCallback(async () => {
    try { setPerf(await etfApi.performance(perfSymbols, perfMetric, perfPeriod)); }
    catch { setPerf(null); }
  }, [perfSymbols, perfMetric, perfPeriod]);

  useEffect(() => { void loadBase(); }, [loadBase]);
  useEffect(() => { void loadList(); }, [loadList]);
  useEffect(() => { void loadFlow(); }, [loadFlow]);
  useEffect(() => { void loadScale(); }, [loadScale]);
  useEffect(() => { void loadPerf(); }, [loadPerf]);
  useEffect(() => { setPage(1); }, [country, board, filters, sort]);

  /* ---------- 自选 ---------- */
  const toggleWatch = (code: string) => {
    setWatch((cur) => {
      const next = cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code];
      writeWatch(next);
      return next;
    });
  };
  /** 自选行情：从已加载的列表/热门里匹配，未加载到的显示代码 */
  const watchRows: EtfItem[] = useMemo(() => {
    const pool = [...hot, ...(listRes?.items ?? [])];
    return watch.map((code) => pool.find((x) => x.code === code) ?? {
      code, name: null, country: (code.includes('.T') ? 'jp' : code.includes('.KS') ? 'kr'
        : /^\d{6}$/.test(code) ? 'cn' : 'us') as EtfCountry,
      exchange: null, type: '', board: '', tracking_index: null, manager: null,
      inception: null, price: null, pct: null, amount: null, size_yi: null,
      quote_status: 'unavailable', overseas: null,
    });
  }, [watch, hot, listRes]);

  /* ---------- 图表 option ---------- */
  const scaleOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!scale?.points?.length) return null;
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 8, right: 8, top: 18, bottom: 4, containLabel: true },
      legend: { data: ['规模(亿元)', '样本数(只)'], top: 0, textStyle: { fontSize: 10 }, itemWidth: 10, itemHeight: 8 },
      xAxis: { type: 'category', data: scale.points.map((p) => p.date.slice(5)), axisLabel: { fontSize: 9 } },
      yAxis: [
        { type: 'value', name: '亿', nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 9 }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
        { type: 'value', name: '只', nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 9 }, splitLine: { show: false } },
      ],
      series: [
        { name: '规模(亿元)', type: 'bar', data: scale.points.map((p) => p.value), barMaxWidth: 18,
          itemStyle: { color: '#60A5FA', borderRadius: [2, 2, 0, 0] } },
        { name: '样本数(只)', type: 'line', yAxisIndex: 1, data: scale.points.map((p) => p.count),
          smooth: true, symbolSize: 3, lineStyle: { width: 1.6, color: '#F59E0B' }, itemStyle: { color: '#F59E0B' } },
      ],
    };
  }, [scale]);

  const items = listRes?.items ?? [];
  const totalPages = Math.max(1, Math.ceil((listRes?.total ?? 0) / 20));

  return (
    <div className="flex min-h-full flex-col gap-3">
      {/* 标题栏：市场概览 + 更新时间 | 搜索 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <h1 className="text-base font-semibold text-ink">市场概览</h1>
          <span className="text-2xs text-ink-secondary">
            更新于 {overview?.today.date ?? '—'}
            {overview?.prev ? ` · 对比 ${overview.prev.date}` : ' · 首次运行暂无对比数据'}
          </span>
          <span className="text-2xs text-ink-muted">覆盖 中国 / 美国 / 日本 / 韩国</span>
        </div>
        <div className="flex items-center gap-2">
          <input value={filters.q}
            onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
            placeholder="搜索 ETF 代码 / 名称"
            className="w-40 rounded border border-hair px-2 py-1 text-xs outline-none focus:border-brand-300" />
          <button onClick={() => void loadList()}
            className="rounded bg-brand-500 px-2.5 py-1 text-xs text-white hover:bg-brand-600">搜索</button>
        </div>
      </div>

      {/* 市场概览 5 卡 */}
      <OverviewCards data={overview} />

      {/* 板块 Tab + 国家筛选（同一行，参考图 Tab 栏样式） */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex flex-wrap items-center gap-1">
          {BOARDS.map((b) => (
            <button key={b} onClick={() => setBoard(b)}
              className={`rounded px-2 py-1 text-xs transition-colors ${
                board === b ? 'font-medium text-brand-600 underline decoration-brand-500 decoration-2 underline-offset-4'
                  : 'text-ink-secondary hover:text-ink'}`}>
              {b}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="mr-0.5 text-2xs text-ink-muted">国家</span>
          {COUNTRIES.map((c) => (
            <button key={c.key} onClick={() => setCountry(c.key)}
              className={`rounded-md border px-2 py-0.5 text-2xs transition-colors ${
                country === c.key
                  ? 'border-brand-500 bg-brand-500 text-white'
                  : 'border-hair bg-white text-ink-secondary hover:border-brand-200 hover:text-brand-600'}`}>
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">{error}</div>
      )}

      {/* 主区：左侧双行面板 + 右侧竖栏（筛选器 / 自选 / 数据源），撑满剩余高度 */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 2xl:grid-cols-12">
        {/* 左区（3/4） */}
        <div className="flex min-w-0 flex-col gap-3 2xl:col-span-9">
          {/* 第一行：ETF表现 + 热门TOP5 */}
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-3">
            <div className="min-w-0 lg:col-span-2">
              <Card title="ETF表现"
                extra={
                  <div className="flex items-center gap-1.5">
                    <Tabs value={perfMetric} onChange={setPerfMetric}
                      items={[{ key: 'pct', label: '涨跌幅' }, { key: 'price', label: '累计净值' }]} />
                    <select value={perfPeriod} onChange={(e) => setPerfPeriod(e.target.value)}
                      className="rounded border border-hair bg-white px-1.5 py-0.5 text-2xs text-ink">
                      {['1d', '5d', '1m', '3m', '6m', '1y', '3y', 'ytd'].map((p) => (
                        <option key={p} value={p}>
                          {p === 'ytd' ? '今年来' : `近${p.replace(/['dy]/g, (m) => (m === 'd' ? '日' : m === 'y' ? '年' : m))}`}
                        </option>
                      ))}
                    </select>
                  </div>
                }
                bodyCls="flex min-h-0 flex-col">
                <PerformanceChart data={perf} height={260} />
              </Card>
            </div>

            <div className="flex min-w-0 flex-col lg:col-span-1">
              <Card title="热门 ETF TOP 10"
                extra={<span className="text-2xs text-ink-muted">按成交额</span>}
                bodyCls="flex min-h-0 flex-col p-0">
                <div className="min-h-0 flex-1 overflow-x-auto overflow-y-auto">
                  <table className="quant-table compact w-full">
                    <thead><tr>
                      <th>代码</th><th>名称</th>
                      <th className="text-right">涨跌幅</th><th className="text-right">成交额</th>
                    </tr></thead>
                    <tbody>
                      {hot.length ? hot.map((it) => (
                        <tr key={it.code}>
                          <td><Link to={`/etf/${it.code}`} className="num text-brand-600 hover:underline">{it.code}</Link></td>
                          <td className="max-w-[6rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>{it.name ?? '—'}</td>
                          <td className="text-right"><span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span></td>
                          <td className="num text-right text-xs text-ink-secondary">{yi(it.amount)}</td>
                        </tr>
                      )) : (
                        <tr><td colSpan={4}><PanelEmpty minH="min-h-[120px]" /></td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className="border-t border-hair py-1.5 text-center">
                  <Link to="/etf" className="text-2xs text-brand-600 hover:underline">查看全部 ›</Link>
                </div>
              </Card>
            </div>
          </div>

          {/* 第二行：资金流向 + 规模变化 */}
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 md:grid-cols-[5fr_6fr]">
            <div className="flex min-w-0 flex-col">
              <Card title="资金流向" extra={<Tabs value={flowPeriod} onChange={setFlowPeriod} items={FLOW_PERIODS} />}
                bodyCls="min-h-0 flex-1 overflow-y-auto p-0">
                <table className="quant-table w-full">
                  <thead><tr>
                    <th>代码</th><th>名称</th>
                    <th className="text-right">净流入(元)</th><th className="text-right">净流入率</th>
                  </tr></thead>
                  <tbody>
                    {flow.length ? flow.map((it, i) => (
                      <tr key={`${it.code}-${i}`}>
                        <td className="num text-brand-600">{it.code}</td>
                        <td className="max-w-[9rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>{it.name ?? '—'}</td>
                        <td className={`num text-right ${(it.net_inflow ?? 0) >= 0 ? 't-up' : 't-down'}`}>
                          {(it.net_inflow ?? 0) >= 0 ? '+' : ''}{yi(it.net_inflow)}
                        </td>
                        <td className={`num text-right text-xs ${(it.inflow_ratio ?? 0) >= 0 ? 't-up' : 't-down'}`}>
                          {it.inflow_ratio != null ? `${it.inflow_ratio >= 0 ? '+' : ''}${it.inflow_ratio.toFixed(2)}%` : '—'}
                        </td>
                      </tr>
                    )) : (
                      <tr><td colSpan={4}><PanelEmpty minH="min-h-[120px]" /></td></tr>
                    )}
                  </tbody>
                </table>
              </Card>
            </div>

            <div className="flex min-w-0 flex-col">
              <Card title="ETF规模变化"
                extra={<Tabs value={scalePeriod} onChange={setScalePeriod} items={SCALE_PERIODS} />}
                bodyCls="flex min-h-0 flex-col">
                <div className="min-h-0 flex-1">
                  <Chart option={scaleOption} height={190} empty="样本不足" />
                </div>
                <p className="mt-1 text-2xs leading-snug text-ink-muted">
                  {scale?.note ?? '估算口径：最新份额 × 历史收盘价'}
                  {scale ? ` · 样本 ${scale.sample_size} 只` : ''}
                </p>
              </Card>
            </div>
          </div>
        </div>

        {/* 右栏（1/4）：筛选器 + 我的自选 + 数据源 */}
        <div className="flex min-w-0 flex-col gap-3 2xl:col-span-3">
          <FilterPanel
            options={listRes?.options}
            value={filters}
            onChange={setFilters}
            onReset={() => setFilters({ etype: 'all', index: '', manager: '', min_size: '', max_size: '',
              inception_from: '', inception_to: '', q: '' })}
          />

          <Card title="我的自选ETF"
            extra={<span className="text-2xs text-ink-muted">共 {watch.length} 只</span>}
            bodyCls="min-h-0 flex-1 overflow-y-auto p-0">
            <table className="quant-table w-full">
              <thead><tr>
                <th>代码</th><th>名称</th>
                <th className="text-right">最新价</th>
                <th className="text-right">涨跌幅</th>
                <th className="w-8 text-center">★</th>
              </tr></thead>
              <tbody>
                {watchRows.length ? watchRows.map((it) => {
                  const noQuote = it.quote_status === 'unavailable';
                  return (
                    <tr key={it.code}>
                      <td className="num text-brand-600">{it.code}</td>
                      <td className="max-w-[7rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>
                        {it.name ?? '—'}
                      </td>
                      <td className="num text-right text-xs text-ink">
                        {noQuote ? '暂无数据' : fmtNum(it.price)}
                      </td>
                      <td className="text-right">
                        {noQuote ? <span className="text-2xs text-ink-muted">暂无数据</span>
                          : <span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span>}
                      </td>
                      <td className="text-center">
                        <button onClick={() => toggleWatch(it.code)} title="取消自选"
                          className="text-xs text-amber-500 hover:text-amber-600">★</button>
                      </td>
                    </tr>
                  );
                }) : (
                  <tr><td colSpan={5}><PanelEmpty text="暂无自选" minH="min-h-[80px]" /></td></tr>
                )}
              </tbody>
            </table>
          </Card>

          <Card title="数据源" bodyCls="text-2xs leading-relaxed text-ink-muted">
            <ul className="list-disc space-y-1 pl-3">
              <li>中国：东方财富 ETF 全量行情（1337 只，实时）</li>
              <li>美国：腾讯行情（16 只主要 ETF，实时）</li>
              <li>日本 / 韩国：<span className="text-ink-secondary">本土行情源不可达</span>，
                仅提供标的目录，行情显示「暂无数据」</li>
              <li>历史 K 线：腾讯财经（日线，最多 800 根）</li>
              <li>资金流：东方财富主力净流入口径</li>
            </ul>
          </Card>
        </div>
      </div>

      {/* ETF 列表（筛选结果，全宽） */}
      <Card title={`ETF 列表（${listRes?.total ?? 0}）`} bodyCls="p-0"
        extra={loading ? <span className="text-2xs text-ink-muted">加载中…</span> : undefined}>
        <table className="quant-table w-full">
          <thead><tr>
            <th>代码</th><th>名称</th><th>国家</th><th>板块</th>
            <th className="text-right">最新价</th>
            <th className="text-right">
              <div className="flex items-center justify-end">
                <SortHeader label="涨跌幅" sortKey="pct" sort={sort} onSort={toggleSort} align="right" />
              </div>
            </th>
            <th className="text-right">
              <div className="flex items-center justify-end">
                <SortHeader label="规模(亿)" sortKey="size" sort={sort} onSort={toggleSort} align="right" />
              </div>
            </th>
            <th className="text-right">
              <div className="flex items-center justify-end">
                <SortHeader label="成交额" sortKey="amount" sort={sort} onSort={toggleSort} align="right" />
              </div>
            </th>
            <th className="text-center">操作</th>
          </tr></thead>
          <tbody>
            {items.length ? items.map((it) => {
              const noQuote = it.quote_status === 'unavailable';
              return (
                <tr key={`${it.country}-${it.code}`} className="cursor-pointer hover:bg-slate-50"
                  onClick={() => navigate(`/etf/${it.code}`)}>
                  <td>
                    <Link to={`/etf/${it.code}`} onClick={(e) => e.stopPropagation()}
                      className="num text-brand-600 hover:underline">{it.code}</Link>
                  </td>
                  <td className="max-w-[12rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>
                    {it.name ?? '—'}
                  </td>
                  <td className="text-2xs text-ink-muted">
                    {{ cn: '中国', us: '美国', jp: '日本', kr: '韩国' }[it.country]}
                  </td>
                  <td className="text-2xs text-ink-muted">{it.board}</td>
                  <td className="num text-right text-xs text-ink">
                    {noQuote ? '暂无数据' : fmtNum(it.price)}
                  </td>
                  <td className="text-right">
                    {noQuote ? <span className="text-2xs text-ink-muted">暂无数据</span>
                      : <span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span>}
                  </td>
                  <td className="num text-right text-xs text-ink-secondary">
                    {it.size_yi != null ? it.size_yi.toFixed(2) : '—'}
                  </td>
                  <td className="num text-right text-xs text-ink-secondary">{yi(it.amount)}</td>
                  <td className="whitespace-nowrap text-center">
                    <button onClick={(e) => { e.stopPropagation(); toggleWatch(it.code); }}
                      title="加入/移出自选"
                      className={`mr-1.5 text-xs ${watch.includes(it.code) ? 'text-amber-500' : 'text-ink-muted hover:text-amber-500'}`}>
                      {watch.includes(it.code) ? '★' : '☆'}
                    </button>
                    <Link to={`/etf/${it.code}`} onClick={(e) => e.stopPropagation()}
                      title="进入 ETF 分析"
                      className="rounded border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-2xs text-brand-600 hover:bg-brand-100">
                      分析
                    </Link>
                  </td>
                </tr>
              );
            }) : (
              <tr><td colSpan={9}><PanelEmpty text={loading ? '加载中…' : '无匹配结果'} minH="min-h-[140px]" /></td></tr>
            )}
          </tbody>
        </table>
        <Pager page={page} totalPages={totalPages} onChange={setPage} />
      </Card>
    </div>
  );
}
