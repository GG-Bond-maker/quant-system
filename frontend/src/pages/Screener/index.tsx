/**
 * 选股中心（以 ETF 中心为模板标准，布局 + 功能双对齐）：
 * 标题栏（副标题 + 榜单内搜索 + 导出） → 概览 6 卡 → 板块下划线 Tab + 视图 pills →
 * 12 列主区（左 9：股票表现图 + 涨幅 TOP10 / 排名表 Card / 分布图；
 * 右 3：筛选器 + 我的自选 + 数据口径）。
 *
 * 数据真实性行为约束（不变）：
 * - 快照日期只来自后端 result.date，禁止用浏览器本地日期冒充交易日；
 * - 「较昨日」对比由后端 stats 块同口径计算，前端不编造；
 * - 股票表现 = 前复权收盘价按区间首日归一化（口径在数据口径卡披露），
 *   行情缺失的序列不画入图中，单独列出，不用 0 值伪造曲线。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { marketApi } from '@/api/market';
import { screenerApi } from '@/api/screener';
import { exportApi } from '@/api/export';
import { useWatchlistQuotes } from '@/hooks/useWatchlistQuotes';
import { DEFAULT_GROUP, useWatchlistStore } from '@/stores/useWatchlistStore';
import { EmptyState, LoadingState, PanelEmpty, SortHeader } from '@/components/ui';
import DataFreshness from '@/components/DataFreshness';
import type { OverviewDaily } from '@/types/stock';
import type { WatchlistQuote } from '@/types/p1';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';
import StatsCards from './StatsCards';
import DistributionCharts from './DistributionCharts';
import FilterPanel, { DEFAULT_FILTERS, type ScreenerFilters } from './FilterPanel';
import PerformanceChart, {
  PERF_PERIODS, type PerfMetric, type PerfPeriod, type PerfSeries,
} from './PerformanceChart';

/* ==================== 常量 ==================== */
/** 板块 Tab（与后端 board 参数一致） */
const BOARDS = [
  { key: 'all', label: '全部' },
  { key: 'main', label: '主板' },
  { key: 'chinext_star', label: '创业/科创' },
  { key: 'bse', label: '北交所' },
] as const;

/* ==================== 类型 ==================== */
interface ScreenerItem {
  symbol: string;
  name: string | null;
  industry: string | null;
  close: number | null;
  /** 当日涨跌幅（%），后端由 daily_bar 收盘价计算 */
  pct: number | null;
  /** 当日换手率（%），后端由 daily_bar.turnover 换算 */
  turnover: number | null;
  limit_pct: number | null;
  score: number;
  risk: string;
}

/** 排序状态；null = 未排序（保持后端默认顺序，即 Score 降序） */
type SortState = { key: string; dir: 'asc' | 'desc' } | null;

/**
 * 通用排序：支持递增 / 递减，null 值恒定排在末尾（不论方向），
 * 避免无预测 / 停牌标的把升序结果顶到最前面。
 */
function sortRows<T>(rows: T[], key: keyof T, dir: 'asc' | 'desc'): T[] {
  const factor = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    const an = typeof av === 'number' && Number.isFinite(av);
    const bn = typeof bv === 'number' && Number.isFinite(bv);
    if (!an && !bn) return 0;
    if (!an) return 1;   // null 恒排末尾
    if (!bn) return -1;
    return ((av as number) - (bv as number)) * factor;
  });
}

const RISK_MAP: Record<string, { label: string; cls: string }> = {
  low:  { label: '低', cls: 'bg-green-50 text-green-700' },
  mid:  { label: '中', cls: 'bg-amber-50 text-amber-700' },
  high: { label: '高', cls: 'bg-red-50 text-red-700' },
};

function RiskBadge({ level }: { level: string | null }) {
  const known = level != null ? RISK_MAP[level] : undefined;
  const r: { label: string; cls: string } = known ?? { label: '—', cls: 'bg-slate-50 text-slate-500' };
  return <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${r.cls}`}>{r.label}</span>;
}

/** 统一 Card 壳（对齐 ETF 中心模板：头部 border-b + extra） */
function Card({ title, extra, children, bodyCls = '' }: {
  title: React.ReactNode; extra?: React.ReactNode; children: React.ReactNode; bodyCls?: string;
}) {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
        {extra}
      </div>
      <div className={`min-w-0 flex-1 ${bodyCls}`}>{children}</div>
    </div>
  );
}

/** Tab 胶囊组（对齐 ETF 中心 Tabs 样式） */
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

/* ==================== 主组件 ==================== */

/** 大盘走势可选核心指数（与后端 CORE_INDICES 白名单一致） */
const INDEX_OPTIONS: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'sh000001', label: '上证指数' },
  { key: 'sz399001', label: '深证成指' },
  { key: 'sz399006', label: '创业板指' },
  { key: 'sh000300', label: '沪深300' },
  { key: 'sh000688', label: '科创50' },
];

export default function Screener() {
  const navigate = useNavigate();
  const [result, setResult] = useState<any>(null);  // API 返回类型扩展中
  const [market, setMarket] = useState<OverviewDaily | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [board, setBoard] = useState<string>('all');
  const [tab, setTab] = useState<'top' | 'watch'>('top');
  /** Alpha 榜 / 自选股各自独立维护排序，切换 tab 不互相干扰 */
  const [sort, setSort] = useState<SortState>(null);
  const [watchSort, setWatchSort] = useState<SortState>(null);
  const [filters, setFilters] = useState<ScreenerFilters>(DEFAULT_FILTERS);
  /** 榜单内搜索（客户端过滤代码 / 名称，仅作用于当前榜单） */
  const [q, setQ] = useState('');
  const { addTo, removeFrom, contains, groups } = useWatchlistStore();

  /* ---------- 股票表现（Top 5） ---------- */
  const [perfSeries, setPerfSeries] = useState<PerfSeries[] | null>(null);
  const [perfLoading, setPerfLoading] = useState(false);
  const [perfMetric, setPerfMetric] = useState<PerfMetric>('pct');
  const [perfPeriod, setPerfPeriod] = useState<PerfPeriod>('6m');

  /** 点击表头：降序 → 升序 → 取消（数值列默认先给降序，最常用） */
  const toggleSort = (
    key: string,
    cur: SortState,
    set: (s: SortState) => void,
  ) => {
    if (cur?.key !== key) set({ key, dir: 'desc' });
    else if (cur.dir === 'desc') set({ key, dir: 'asc' });
    else set(null);
  };

  /** 全部分组的自选标的（去重保序） */
  const watchSymbols = useMemo(
    () => Array.from(new Set(Object.values(groups).flat())),
    [groups],
  );

  /* 自选行情：共享 hook（P1-6）—— 挂载即加载 + 按 refresh_freq×12 轮询，
   * 与收藏页统一刷新逻辑；接口保留选股域端点（预测分/行业/风险列） */
  const { data: watchData, loading: watchLoading, error: watchError, reload: loadWatchlist } =
    useWatchlistQuotes((syms: string[]) => screenerApi.watchlist(syms), 12, tab === 'watch');
  const watchItems: WatchlistQuote[] = watchData?.items ?? [];

  /** 行情日期：取自选快照中最新的日期，统一在表头上方展示 */
  const watchQuoteDate = useMemo(
    () => watchItems.map((i) => i.date).filter((d): d is string => !!d).sort().at(-1) ?? '',
    [watchItems],
  );

  const load = useCallback(async (refresh = false) => {
    setLoading(true); setError(null);
    try {
      const p: Record<string, unknown> = { top_k: filters.topK, board };
      if (filters.day) p.date = filters.day;
      setResult(await screenerApi.screen(p, refresh));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败');
    } finally { setLoading(false); }
  }, [filters.topK, filters.day, board]);

  const loadMarket = useCallback(async () => {
    // 只消费 trade_date（交易日口径比对）：日频块长缓存，比整包 overview 便宜
    try { setMarket(await marketApi.overviewDaily(5)); } catch { /* 降级 */ }
  }, []);

  useEffect(() => { void load(); void loadMarket(); }, [load, loadMarket]);
  /** 切板块 / 改筛选条件时回到第一页 */
  useEffect(() => { setPage(1); }, [board, filters.topK, filters.day]);

  /** 导出当前筛选条件的选股结果（后端能力早已实现，此处为入口） */
  const [exporting, setExporting] = useState(false);
  const doExport = useCallback(async () => {
    setExporting(true);
    try {
      await exportApi.screener({ date: filters.day || undefined, top_k: filters.topK, board });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '导出失败');
    } finally {
      setExporting(false);
    }
  }, [filters.day, filters.topK, board]);

  const resetFilter = () => setFilters(DEFAULT_FILTERS);

  const raw: ScreenerItem[] = result?.items ?? [];
  /**
   * 客户端过滤（叠加而非互斥 —— 原实现设了最小 Score 就忽略风险筛选，不合理，已修）：
   * 服务端口径（date/top_k/board）+ 客户端口径（最小 Score / 风险 / 榜单内搜索）。
   */
  const filtered = useMemo(() => {
    let out = raw;
    if (filters.minScore) {
      const ms = parseFloat(filters.minScore);
      if (Number.isFinite(ms)) out = out.filter((r) => r.score >= ms);
    }
    if (filters.risk !== 'all') out = out.filter((r) => r.risk === filters.risk);
    const kw = q.trim().toLowerCase();
    if (kw) {
      out = out.filter((r) =>
        r.symbol.toLowerCase().includes(kw) || (r.name ?? '').toLowerCase().includes(kw));
    }
    return out;
  }, [raw, filters.minScore, filters.risk, q]);

  /** 排序只作用于当前筛选结果，不改变 items 的原始顺序 */
  const items = useMemo(
    () => (sort ? sortRows(filtered, sort.key as keyof ScreenerItem, sort.dir) : filtered),
    [filtered, sort],
  );
  const sortedWatch = useMemo(
    () => (watchSort ? sortRows(watchItems, watchSort.key as keyof WatchlistQuote, watchSort.dir) : watchItems),
    [watchItems, watchSort],
  );

  /** 表格分页：榜单默认 top_k=50，一屏 20 条更接近设计稿的密度 */
  const PAGE_SIZE = 20;
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pagedItems = useMemo(
    () => items.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [items, safePage],
  );

  /**
   * 快照是否为最新交易日：拿后端返回的快照日期与平台当前交易日比对。
   * 口径说明：result.date 为 YYYY-MM-DD，market.trade_date 为 YYYYMMDD，需归一化后比对。
   */
  const snapDate = result?.date ?? '';
  const tradeDate = market?.trade_date ?? '';
  /**
   * 数据时效：优先用后端 freshness（交易日历感知，直接给出落后的交易日数）；
   * 字段缺失/为 null 时回退到「快照日期 == 平台交易日」的字符串比对（旧口径）。
   */
  const freshness = result?.freshness;
  const lagDays = freshness?.lag_trading_days ?? null;
  const isLatest = lagDays != null
    ? lagDays === 0
    : !!snapDate && !!tradeDate && snapDate.replace(/-/g, '') === tradeDate;

  const boardLabel = BOARDS.find((b) => b.key === board)?.label ?? board;

  /* ---------- 大盘走势：五大核心指数同图对比（腾讯源），客户端按周期裁剪 + 归一化 ---------- */
  useEffect(() => {
    if (tab !== 'top') { setPerfSeries(null); return; }
    let cancelled = false;
    (async () => {
      setPerfLoading(true);
      const results = await Promise.allSettled(
        INDEX_OPTIONS.map((o) => marketApi.indexKline(o.key)),
      );
      if (cancelled) return;
      const series: PerfSeries[] = results.map((r, i) => ({
        symbol: INDEX_OPTIONS[i].key,
        name: (r.status === 'fulfilled' && r.value?.name) || INDEX_OPTIONS[i].label,
        points: r.status === 'fulfilled' && Array.isArray(r.value?.bars)
          ? r.value.bars.map((b) => ({ date: b.date, close: b.close }))
          : [],
      }));
      setPerfSeries(series);
      setPerfLoading(false);
    })();
    return () => { cancelled = true; };
  }, [tab]);

  /** 榜单涨幅 TOP 10：当前榜单内按当日实际涨跌幅排序（客户端聚合，不新增接口） */
  const topMovers = useMemo(
    () => [...items]
      .filter((i) => i.pct != null)
      .sort((a, b) => (b.pct as number) - (a.pct as number))
      .slice(0, 10),
    [items],
  );

  return (
    <div className="flex min-h-full flex-col gap-3">
      {/* ===== 标题栏（对齐 ETF 模板：主标题 + 副标题 | 右侧搜索 + 操作） ===== */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <h1 className="text-base font-semibold text-ink">选股中心</h1>
          <span className="text-2xs text-ink-secondary">
            Alpha Basic V1 · {boardLabel === '全部' ? '全市场' : boardLabel} · 数据日期 {snapDate || '—'}
            {result?.stats?.prev_date ? ` · 对比 ${result.stats.prev_date}` : ''}
          </span>
          {/* 数据新鲜度：快照日期 + 缓存徽标 + 强制刷新（§3.2；历史日期也允许重算） */}
          <DataFreshness
            asOf={snapDate || null}
            fromCache={result?.from_cache}
            stale={result?.stale}
            onRefresh={() => load(true)}
          />
        </div>
        <div className="flex items-center gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="搜索榜单内代码 / 名称"
            className="w-44 rounded border border-hair bg-white px-2 py-1 text-xs outline-none focus:border-brand-300" />
          {isLatest ? (
            <span className="text-2xs text-up">今日更新 ✓</span>
          ) : lagDays != null && lagDays > 0 ? (
            <span className="text-2xs text-amber-600"
              title={freshness?.note || '当前展示的不是最新交易日的快照'}>
              落后 {lagDays} 个交易日 · {snapDate}
            </span>
          ) : snapDate ? (
            <span className="text-2xs text-ink-muted" title="当前展示的不是最新交易日的快照">
              非最新 · {snapDate}
            </span>
          ) : null}
          <button onClick={() => void doExport()} disabled={exporting || tab !== 'top' || items.length === 0}
            title="按当前筛选条件导出 Top-N 选股结果（xlsx）"
            className="rounded bg-brand-500 px-2.5 py-1 text-xs text-white hover:bg-brand-600 disabled:opacity-50">
            {exporting ? '导出中…' : '导出 Excel'}
          </button>
        </div>
      </div>

      {/* ===== 概览统计卡（6 张，视觉对齐 ETF OverviewCards） ===== */}
      <StatsCards stats={result?.stats ?? null} />

      {error && (
        <div className="rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">{error}</div>
      )}

      {/* ===== Tab 行：板块下划线 Tab + 右侧视图 pills（对齐 ETF 板块/国家行） ===== */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex flex-wrap items-center gap-1">
          {BOARDS.map((b) => (
            <button key={b.key} onClick={() => setBoard(b.key)}
              className={`rounded px-2 py-1 text-xs transition-colors ${
                board === b.key
                  ? 'font-medium text-brand-600 underline decoration-brand-500 decoration-2 underline-offset-4'
                  : 'text-ink-secondary hover:text-ink'}`}>
              {b.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="mr-0.5 text-2xs text-ink-muted">视图</span>
          {([['top', `Alpha Top ${result?.top_k ?? filters.topK}`], ['watch', `自选股 (${watchSymbols.length})`]] as const).map(
            ([key, label]) => (
              <button key={key} onClick={() => setTab(key)}
                className={`rounded-md border px-2 py-0.5 text-2xs transition-colors ${
                  tab === key
                    ? 'border-brand-500 bg-brand-500 text-white'
                    : 'border-hair bg-white text-ink-secondary hover:border-brand-200 hover:text-brand-600'}`}>
                {label}
              </button>
            ),
          )}
        </div>
      </div>

      {/* ===== 主区：左 9 内容 + 右 3 筛选栏（对齐 ETF 12 列模板） ===== */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 2xl:grid-cols-12">
        {/* 左区 */}
        <div className="flex min-w-0 flex-col gap-3 2xl:col-span-9">
          {tab === 'top' && (
            /* 第一行：股票表现 + 涨幅 TOP 10（对齐 ETF 第一行布局） */
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
              <div className="min-w-0 lg:col-span-2">
                <Card title="大盘走势"
                  extra={
                    <div className="flex items-center gap-1.5">
                      <Tabs value={perfMetric} onChange={setPerfMetric}
                        items={[{ key: 'pct', label: '涨跌幅' }, { key: 'price', label: '净值' }]} />
                      <Tabs value={perfPeriod} onChange={setPerfPeriod} items={PERF_PERIODS} />
                    </div>
                  }
                  bodyCls="flex min-h-0 flex-col">
                  <PerformanceChart series={perfSeries} metric={perfMetric} period={perfPeriod} height={240} />
                  <p className="mt-1 text-2xs leading-snug text-ink-muted">
                    五大核心指数收盘价（腾讯源）· 各自区间首日归一化 · 同图对比
                    {perfLoading && ' · 加载中…'}
                  </p>
                </Card>
              </div>

              <div className="flex min-w-0 flex-col">
                <Card title="榜单涨幅 TOP 10"
                  extra={<span className="text-2xs text-ink-muted">按当日涨跌幅</span>}
                  bodyCls="flex min-h-0 flex-col p-0">
                  <div className="min-h-0 flex-1 overflow-x-auto overflow-y-auto">
                    <table className="quant-table compact w-full">
                      <thead><tr>
                        <th>代码</th><th>名称</th>
                        <th className="text-right">涨跌幅</th><th className="text-right">Score</th>
                      </tr></thead>
                      <tbody>
                        {topMovers.length ? topMovers.map((it) => (
                          <tr key={it.symbol} className="cursor-pointer hover:bg-slate-50"
                            onClick={() => navigate(`/stock/${it.symbol}`)}>
                            <td>
                              <Link to={`/stock/${it.symbol}`} onClick={(e) => e.stopPropagation()}
                                className="num text-brand-600 hover:underline">{it.symbol.split('.')[0]}</Link>
                            </td>
                            <td className="max-w-[6rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>
                              {it.name ?? '—'}
                            </td>
                            <td className="text-right">
                              <span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span>
                            </td>
                            <td className="num text-right text-xs text-ink-secondary">{it.score.toFixed(4)}</td>
                          </tr>
                        )) : (
                          <tr><td colSpan={4}><PanelEmpty minH="min-h-[120px]" /></td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </div>
            </div>
          )}

          {/* 排名表 */}
          {tab === 'watch' ? (
            <Card title={`自选股（${watchItems.length}）`}
              extra={
                <div className="flex items-center gap-2 text-2xs text-ink-muted">
                  {watchQuoteDate && <span>行情日期 {watchQuoteDate}</span>}
                  {watchSymbols.length > 0 && (
                    <button onClick={() => void loadWatchlist()} disabled={watchLoading}
                      className="rounded border border-hair bg-white px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
                      {watchLoading ? '…' : '刷新'}
                    </button>
                  )}
                </div>
              }
              bodyCls="p-0">
              <div className="overflow-x-auto">
                {watchLoading ? <LoadingState /> :
                watchError ? (
                  <div className="px-4 py-3 text-xs text-amber-700">{watchError}</div>
                ) : watchItems.length > 0 ? (
                  <table className="quant-table dense w-full">
                    <thead>
                      <tr>
                        <th>股票</th>
                        <th>名称</th>
                        <th>行业</th>
                        <th className="text-right">
                          <SortHeader label="现价" sortKey="close" sort={watchSort}
                            onSort={(k) => toggleSort(k, watchSort, setWatchSort)} align="right" />
                        </th>
                        <th className="text-right">
                          <SortHeader label="涨跌" sortKey="pct" sort={watchSort}
                            onSort={(k) => toggleSort(k, watchSort, setWatchSort)} align="right" />
                        </th>
                        <th className="text-right">
                          <SortHeader label="Score" sortKey="score" sort={watchSort}
                            onSort={(k) => toggleSort(k, watchSort, setWatchSort)} align="right" />
                        </th>
                        <th className="text-center">风险</th>
                        <th className="text-center">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedWatch.map((it) => (
                        <tr key={it.symbol} className="cursor-pointer hover:bg-slate-50"
                          onClick={() => navigate(`/stock/${it.symbol}`)}>
                          <td>
                            <Link to={`/stock/${it.symbol}`} onClick={(e) => e.stopPropagation()}
                              className="num text-brand-600 hover:underline">
                              {it.symbol.split('.')[0]}
                            </Link>
                          </td>
                          <td className="text-xs text-ink-secondary">{it.name ?? '—'}</td>
                          <td className="text-xs text-ink-secondary">{it.industry ?? '—'}</td>
                          <td className="text-right num text-ink">
                            {it.close != null ? fmtNum(it.close) : '—'}
                          </td>
                          <td className="text-right">
                            {it.pct != null ? (
                              <span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span>
                            ) : '—'}
                          </td>
                          <td className="text-right">
                            {it.score != null ? (
                              <span className="num rounded bg-slate-50 px-1.5 py-0.5 text-xs font-medium text-ink">
                                {it.score.toFixed(4)}
                              </span>
                            ) : <span className="text-xs text-ink-muted">无预测</span>}
                          </td>
                          <td className="text-center"><RiskBadge level={it.risk} /></td>
                          <td className="whitespace-nowrap text-center">
                            <button onClick={(e) => { e.stopPropagation(); removeFrom(DEFAULT_GROUP, it.symbol); }}
                              title="移出自选"
                              className="text-xs text-amber-500 hover:text-amber-600">★</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState title="暂无自选股" hint="在选股榜或个股页点击「+选」加入自选后，这里可查看行情" />
                )}
              </div>
            </Card>
          ) : (
            <Card title={`Alpha 榜（${items.length}）`}
              extra={
                <div className="flex items-center gap-3 text-2xs text-ink-muted">
                  <span>股票池 <span className="num font-medium text-ink">{result?.stats?.today?.pool_size ?? '—'}</span></span>
                  <span>结果 <span className="num font-medium text-ink">{items.length}</span></span>
                  {loading && <span>加载中…</span>}
                </div>
              }
              bodyCls="p-0">
              <div className="overflow-x-auto">
                {loading ? <LoadingState /> :
                error ? (
                  <div className="px-4 py-3 text-xs text-amber-700">{error}</div>
                ) : items.length > 0 ? (
                  <table className="quant-table dense w-full">
                    <thead>
                      <tr>
                        <th className="w-10">#</th>
                        <th>股票代码</th>
                        <th>名称</th>
                        <th className="text-right">
                          <SortHeader label="现价" sortKey="close" sort={sort}
                            onSort={(k) => toggleSort(k, sort, setSort)} align="right" />
                        </th>
                        <th className="text-right">
                          <SortHeader label="涨跌幅" sortKey="pct" sort={sort}
                            onSort={(k) => toggleSort(k, sort, setSort)} align="right" />
                        </th>
                        <th className="text-right">
                          <SortHeader label="Score" sortKey="score" sort={sort}
                            onSort={(k) => toggleSort(k, sort, setSort)} align="right" />
                        </th>
                        <th className="text-right">预期收益</th>
                        <th className="text-right">
                          <SortHeader label="换手率" sortKey="turnover" sort={sort}
                            onSort={(k) => toggleSort(k, sort, setSort)} align="right" />
                        </th>
                        <th>行业</th>
                        <th className="text-center">风险</th>
                        <th className="text-center">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pagedItems.map((it, i) => (
                        <tr key={it.symbol} className="cursor-pointer hover:bg-slate-50"
                          onClick={() => navigate(`/stock/${it.symbol}`)}>
                          <td className="num text-ink-muted">{String(i + 1).padStart(2, '0')}</td>
                          <td>
                            <Link to={`/stock/${it.symbol}`} onClick={(e) => e.stopPropagation()}
                              className="num text-brand-600 hover:underline">
                              {it.symbol.split('.')[0]}
                            </Link>
                          </td>
                          <td className="text-xs text-ink-secondary">{it.name ?? '—'}</td>
                          <td className="text-right num text-ink">
                            {it.close != null ? fmtNum(it.close) : '—'}
                          </td>
                          <td className="text-right">
                            {it.pct != null ? (
                              <span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span>
                            ) : '—'}
                          </td>
                          <td className="text-right">
                            <span className="num text-ink">
                              {it.score.toFixed(4)}
                            </span>
                          </td>
                          <td className="text-right">
                            <span className={`num ${it.score >= 0 ? 't-up' : 't-down'}`}>
                              {it.score >= 0 ? '+' : ''}{(it.score * 100).toFixed(1)}%
                            </span>
                          </td>
                          <td className="text-right num text-ink-secondary">
                            {it.turnover != null ? `${it.turnover.toFixed(2)}%` : '—'}
                          </td>
                          <td className="text-xs text-ink-secondary">{it.industry ?? '—'}</td>
                          <td className="text-center"><RiskBadge level={it.risk} /></td>
                          <td className="whitespace-nowrap text-center">
                            <button onClick={(e) => {
                              e.stopPropagation();
                              if (contains(it.symbol)) removeFrom(DEFAULT_GROUP, it.symbol);
                              else addTo(DEFAULT_GROUP, it.symbol);
                            }}
                              title={contains(it.symbol) ? '移出自选' : '加入自选'}
                              className={`mr-1.5 text-xs ${
                                contains(it.symbol) ? 'text-amber-500' : 'text-ink-muted hover:text-amber-500'}`}>
                              {contains(it.symbol) ? '★' : '☆'}
                            </button>
                            <Link to={`/stock/${it.symbol}`} onClick={(e) => e.stopPropagation()}
                              title="进入个股分析"
                              className="rounded border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-2xs text-brand-600 hover:bg-brand-100">
                              分析
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState title="暂无选股结果" hint={q ? '当前榜单内无匹配的代码或名称' : '请先运行每日流水线生成预测'} />
                )}
              </div>
              {pageCount > 1 && (
                <div className="flex items-center justify-center gap-1 border-t border-hair py-2">
                  <button onClick={() => setPage(Math.max(1, safePage - 1))} disabled={safePage <= 1}
                    className="rounded border border-hair px-2 py-0.5 text-xs disabled:opacity-40">‹</button>
                  <span className="num px-2 text-xs text-ink-secondary">{safePage} / {pageCount}</span>
                  <button onClick={() => setPage(Math.min(pageCount, safePage + 1))} disabled={safePage >= pageCount}
                    className="rounded border border-hair px-2 py-0.5 text-xs disabled:opacity-40">›</button>
                </div>
              )}
            </Card>
          )}

          {/* 分布图：左区第二行（在筛选器下方通栏，与设计稿一致） */}
          {tab === 'top' && items.length > 0 && (
            <DistributionCharts items={items} />
          )}
        </div>

        {/* 右栏：筛选器 + 我的自选 + 数据口径 */}
        <div className="flex min-w-0 flex-col gap-3 2xl:col-span-3">
          <FilterPanel
            value={filters}
            onChange={setFilters}
            onApply={() => void load()}
            onReset={resetFilter}
            loading={loading}
          />

          <Card title="我的自选股"
            extra={
              <div className="flex items-center gap-2 text-2xs text-ink-muted">
                <span>共 {watchSymbols.length} 只</span>
                {watchSymbols.length > 0 && (
                  <button onClick={() => void loadWatchlist()} disabled={watchLoading}
                    className="text-2xs text-brand-600 hover:underline disabled:opacity-50">
                    {watchLoading ? '…' : '刷新'}
                  </button>
                )}
              </div>
            }
            bodyCls="min-h-0 overflow-y-auto p-0">
            <table className="quant-table compact w-full">
              <thead><tr>
                <th>代码</th><th>名称</th>
                <th className="text-right">最新价</th>
                <th className="text-right">涨跌幅</th>
                <th className="w-8 text-center">★</th>
              </tr></thead>
              <tbody>
                {watchItems.length ? watchItems.map((it) => (
                  <tr key={it.symbol} className="cursor-pointer hover:bg-slate-50"
                    onClick={() => navigate(`/stock/${it.symbol}`)}>
                    <td>
                      <Link to={`/stock/${it.symbol}`} onClick={(e) => e.stopPropagation()}
                        className="num text-brand-600 hover:underline">{it.symbol.split('.')[0]}</Link>
                    </td>
                    <td className="max-w-[7rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>
                      {it.name ?? '—'}
                    </td>
                    <td className="num text-right text-xs text-ink">
                      {it.close != null ? fmtNum(it.close) : '—'}
                    </td>
                    <td className="text-right">
                      {it.pct != null
                        ? <span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span>
                        : <span className="text-2xs text-ink-muted">—</span>}
                    </td>
                    <td className="text-center">
                      <button onClick={(e) => { e.stopPropagation(); removeFrom(DEFAULT_GROUP, it.symbol); }}
                        title="移出自选"
                        className="text-xs text-amber-500 hover:text-amber-600">★</button>
                    </td>
                  </tr>
                )) : (
                  <tr><td colSpan={5}><PanelEmpty text="暂无自选" minH="min-h-[80px]" /></td></tr>
                )}
              </tbody>
            </table>
          </Card>

          <Card title="数据口径" bodyCls="text-2xs leading-relaxed text-ink-muted">
            <ul className="list-disc space-y-1 pl-3">
              <li>Score：alpha_basic_v1（LightGBM）预测的未来 5 日收益，非当日涨跌</li>
              <li>胜率 / 平均涨跌幅：榜单内标的当日实际收盘价计算</li>
              <li>股票表现：前复权收盘价，区间首日归一化（涨跌幅 / 净值）</li>
              <li>风险等级：由预测分数映射 low / mid / high</li>
              <li>股票数量：当日有预测快照、Alpha 榜 top_k 截断前的标的数（剔除 ST/停牌、按当前板块筛选）；≠ 全市场</li>
              <li>搜索为当前榜单内筛选（代码 / 名称），非全市场</li>
              <li>「较昨日」：前一交易日榜单同口径重算，首次运行无对比</li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
