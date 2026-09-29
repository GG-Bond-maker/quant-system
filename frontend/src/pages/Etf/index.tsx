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
  EtfFlowItem, EtfItem, EtfListResult, EtfOverview, EtfOverviewSeries,
  EtfPerformance, EtfScale, EtfCountry,
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

/**
 * ETF表现 周期选项。**故意不含 '1d'**：后端 `app/api/v1/etf.py:549` 的
 * `_PERIOD_DAYS['1d'] = 1`，`:593` 取 `bars[-days:]` 后 `:594` 又要求 `len(bars) >= 2`，
 * 单根 K 线 ⇒ 每条序列都判 unavailable ⇒ 必然空图（页面上会「有下拉项却永远画不出线」）。
 * 「近1日」的涨跌信息本来就在右侧热门榜和列表的涨跌幅列里，折线图表现不出一个点，
 * 故不提供该选项，也不为凑出曲线去放宽口径。
 *
 * **故意不含「成立以来 / 全历史」**：这里曾短暂加过一个 `all`（不截断）选项，但腾讯
 * 数据源单次最多返回约 800 根日线（≈3.3 年，实测依据见
 * `backend/app/api/v1/etf.py` 的 `_KLINE_LIMIT` 注释；调大到 810/1000 反而只返回
 * 640 根，≥2400 直接 param error），而最早的上证50ETF（510050）上市于 2005-02-23。
 * 即「成立以来」实际拿不到全历史，提供它会变成标签与数据不符的口径谎报；
 * 且它与已有的「近3年」几乎重复。故不做该选项——**不要因为缺这个选项就来加回来**。
 *
 * **标签为什么写成常量而不是用 `code.replace()` 拼**：原实现是
 * `` `近${p.replace(/['dy]/g, (m) => (m === 'd' ? '日' : m === 'y' ? '年' : m))}` ``，
 * 字符类 `['dy]` 里**没有 `m`**，导致 `1m/3m/6m` 完全不被替换，下拉实际渲染成
 * 「近1m / 近3m / 近6m」。显式中文表杜绝这类字符类漏写的静默错误。
 */
const PERF_PERIODS = [
  { key: '5d', label: '近5日' },
  { key: '1m', label: '近1个月' },
  { key: '3m', label: '近3个月' },
  { key: '6m', label: '近6个月' },
  { key: '1y', label: '近1年' },
  { key: '3y', label: '近3年' },
  /** 后端已修正为真正的「年初至今」（此前 `_PERIOD_DAYS['ytd'] = 0` 是 falsy，
   *  导致完全不截断、实际返回全部约 800 根 ≈ 3.3 年，属于口径谎报）。 */
  { key: 'ytd', label: '今年来' },
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

/** 中国 ETF 全量目录数据源标识 -> 展示文案（口径披露） */
function sourceLabel(src: string | undefined): string {
  if (src === 'eastmoney') return '东方财富 clist（主源）';
  if (src === 'sina+tencent') return '新浪目录 + 腾讯市值（兜底源）';
  return '—';
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
    // setOption 默认 merge 语义：新 series 按**下标**合并，数组变短时多余的旧 series 不删除。
    // ⚠️ 诚实说明：本组件的 effect 依赖 [option]，cleanup 里 chart.dispose()，
    // option 一变化就 dispose + init 重建全新实例 ⇒ **merge 残留在当前实现下不可能发生**。
    // 因此这里传 notMerge 是**防御性加固 + 与 utils/useChart.ts 的语义对齐**（该 hook 是
    // 全项目唯一「init 一次、复用实例」的实现，早已用 setOption(option, true)），
    // 不是修复某个线上 bug —— 将来若把容器改成 init 一次以省掉实例抖动，此处的
    // notMerge 才会真正开始承担作用。
    chart.setOption(option, true);
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

/**
 * 热门榜请求失败时的**可读原因**，保证返回非空串。
 *
 * `ApiError.message` 可能是空串（后端信封缺 message），若直接把它写进 `hotReason`，
 * 渲染层的 `hotApplied === 'unavailable' && hotReason` 会整体为假 ⇒ 界面落回
 * "热榜有数据但 A 股为 0"那句假文案（P3-2）。故此处显式兜底，宁可给一句笼统原因也不给空串。
 */
function hotFailureReason(err: unknown): string {
  const msg = err instanceof ApiError ? err.message : '';
  return msg.trim() || '热门榜数据加载失败（后端未提供具体原因）';
}

/* ==================== 主组件 ==================== */
export default function EtfCenter() {
  const navigate = useNavigate();
  const [overview, setOverview] = useState<EtfOverview | null>(null);
  const [listRes, setListRes] = useState<EtfListResult | null>(null);
  const [hot, setHot] = useState<EtfItem[]>([]);
  const [flow, setFlow] = useState<EtfFlowItem[]>([]);
  const [flowStatus, setFlowStatus] = useState<'ok' | 'unavailable'>('ok');
  const [flowReason, setFlowReason] = useState<string | null>(null);
  const [scale, setScale] = useState<EtfScale | null>(null);
  const [perf, setPerf] = useState<EtfPerformance | null>(null);
  /**
   * 市场概览 5 张卡右侧图形的**真实历史序列**（`/etf/overview/series`）。
   *
   * 与 `overview`（快照）分开取：快照是当前值、序列是历史点，端点与缓存 TTL 都不同。
   * 取不到时为 null ⇒ `Sparkline` 一律显示「暂无历史序列」占位（当前后端各指标
   * `enough=false`：本地无落库，靠每日盘后归档累积，需 ≥6 天自动转为可绘制）。
   */
  const [overviewSeries, setOverviewSeries] = useState<EtfOverviewSeries | null>(null);
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
  /**
   * 「热门 ETF TOP 10」的排序基准（对标后端 `/etf/hot?sort=`）。
   *
   * **默认 `amount`**（成交额）：保持与改造前一致的行为，成交额榜头部标的最具代表性。
   *
   * 这份 `hot` 在本页有两处下游依赖：① 「ETF表现」折线图的取样标的（见 perfCodes ——
   * 从中取前 5 只 **A 股**，**跟随本 state 变化**）；② 自选行情的兜底匹配池（见 watchRows）。
   */
  const [hotSort, setHotSort] = useState<'amount' | 'pct'>('amount');
  /**
   * 热门榜的**已应用状态（applied）** —— 只在一次请求**落地**时推进，绝不用"请求相位"当文案依据。
   *
   * - `'loading'`：**还没有任何一次成功响应**，此时 `hot` 必为空。文案只能说"加载中、样本待定"，
   *   **不得**断言"本次 N 只均为境外标的"之类的标的构成事实 —— 此刻根本没有数据可供断言。
   * - `'ok'`：已应用一份**非空** `hot`（可能是上一次刷新留下的结果，其排序口径由
   *   `hotSortApplied` 同步披露）。**只有此态**才允许出现样本口径文案。
   * - `'unavailable'`：请求失败，或成功但返回 0 条 ⇒ 走琥珀色原因行。
   *
   * **为什么用"已应用值"而不是裸的请求相位**：① 请求在途（冷路径可达 30s）时 `hot` 仍是
   * 上一份榜，若文案跟着"在途"立刻翻成"无样本"，就会出现"图上有曲线、文案却说没样本"的谎报；
   * ② 反过来，首屏还没有任何一次落地时若沿用 `'ok'`（旧实现的初值就是 `'ok'` + `hot=[]`），
   * 会输出"本次 0 只均为境外标的"——断言了不存在的事实。故状态只在数据落地时推进，
   * 与 `hotSortApplied`（而非 `hotSort`）是同一原则：**滞后状态不得抢先表述**。
   */
  const [hotApplied, setHotApplied] = useState<'loading' | 'ok' | 'unavailable'>('loading');
  /** 热门榜请求是否在途：仅用于在"已应用文案"后追加「正在刷新」提示，不参与有无样本的判定 */
  const [hotInFlight, setHotInFlight] = useState(true);
  /** 热门榜不可用时后端/网络给出的可读原因（沿用 flowReason 那套结构化降级风格） */
  const [hotReason, setHotReason] = useState<string | null>(null);
  /**
   * 后端回显的**本次 `hot` 实际生效**的排序键（`/etf/hot` 响应 `sort_applied`）。
   * 口径披露文案只认它、不认 `hotSort`：热门榜请求在途（冷路径可达 30s）时 `hot` 仍是
   * 上一份榜，若文案跟着 `hotSort` 立刻翻转，就会出现"文案说按涨跌幅、图上还是成交额前 5"
   * 的短暂谎报。后端 sort 白名单只允许 amount|pct（前端也只传这两个），故按二值收敛。
   */
  const [hotSortApplied, setHotSortApplied] = useState<'amount' | 'pct'>('amount');

  /* ---------- 概览 / 热门 ---------- */
  const loadBase = useCallback(async () => {
    setHotInFlight(true);
    const [o, h] = await Promise.allSettled([
      etfApi.overview(), etfApi.hot(10, hotSort),
    ]);
    try {
      if (o.status === 'fulfilled') setOverview(o.value);
      // 「ETF表现」的取样来源就是这份 hot：切排序 / 刷新后 hot 变，perfSymbols 随之重算。
      if (h.status === 'fulfilled') {
        const rows = h.value.items ?? [];
        setHot(rows);
        setHotSortApplied(h.value.sort_applied === 'pct' ? 'pct' : 'amount');
        // 成功但 0 条：等同"取不到样本"，**不能**落进 'ok' —— 否则文案会把"没有数据"
        // 说成"本次 N 只均为境外标的"（N=0 时那是对不存在的构成下断言）。
        setHotApplied(rows.length ? 'ok' : 'unavailable');
        setHotReason(rows.length ? null : '热门榜本次返回 0 条，无法确定折线图样本');
      } else {
        // 失败保持既有降级 / 空态（hot 为 []），不抛白屏；原因交给卡片如实披露
        setHot([]);
        setHotApplied('unavailable');
        // ⚠️ 原因必须兜底成**非空串**：渲染层若用 `hotReason` 参与判定，
        // 空串会让整个条件为假、把界面落回那句假文案（P3-2 同源缺陷）。
        setHotReason(hotFailureReason(h.reason));
      }
    } finally {
      setHotInFlight(false);
    }
  }, [hotSort]);

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
    try {
      const res = await etfApi.flow(flowPeriod, 10);
      setFlow(res.items ?? []);
      // 数据源不可用时后端返回结构化 status=unavailable + reason（HTTP 200 信封），
      // 据此展示「数据源不可用 + 原因」，而不是笼统的「暂无数据」。
      setFlowStatus(res.status === 'unavailable' ? 'unavailable' : 'ok');
      setFlowReason(res.reason ?? null);
    } catch (e) {
      setFlow([]);
      setFlowStatus('unavailable');
      setFlowReason(e instanceof ApiError ? e.message : '资金流数据源不可用');
    }
  }, [flowPeriod]);

  const loadScale = useCallback(async () => {
    try { setScale(await etfApi.scale(scalePeriod, 10)); } catch { setScale(null); }
  }, [scalePeriod]);

  /**
   * 取市场概览 5 张卡的 KPI 历史序列（近 30 天）。
   *
   * 失败一律置 null（不抛白屏、不编造）：卡片**数值**仍来自 `/etf/overview` 快照，
   * 只有趋势位显示「暂无历史序列」占位，两者互不拖累。
   */
  const loadOverviewSeries = useCallback(async () => {
    try { setOverviewSeries(await etfApi.overviewSeries(30)); }
    catch { setOverviewSeries(null); }
  }, []);

  /* ---------- ETF表现：序列标的 = 当前「热门榜」前 5 只 A 股 ---------- */
  /**
   * **当前口径**：折线图的标的 = 当前「热门 ETF TOP 10」里的**前 5 只 A 股**
   * （判定用类型里已有的权威字段 `EtfItem.country === 'cn'`），随 `hotSort` 走 ——
   * 热门榜按成交额排就取成交额前 5 只 A 股，按涨跌幅排就取涨跌幅前 5 只 A 股，
   * 热门榜刷新后同样跟着变。
   *
   * **为什么只取 A 股（第二轮口径收紧）**：实测把热门榜切到「涨跌幅」排序时，前 5 里会
   * 混进 4 只美股（SOXL / EEM / DIA / XLK）。它们在目录里**有**实时行情（腾讯美股，
   * `quote_status='ok'`），但后端 `/etf/performance` 拉不到它们的 K 线（`points=0`），
   * 于是它们全部落进图下方「以下标的暂无行情数据」，图上只剩 1 条 A 股曲线，
   * 「ETF表现」形同虚设。经用户裁决，表现图只取 A 股标的。
   * 判定**不用「代码是否 6 位数字」去猜**：跨境 ETF（中国上市、跟踪纳指/日经等）
   * `country` 仍然是 `cn`，它们有真实 K 线，应当保留。
   *
   * **顺序必须是「先过滤 → 后切片」**，不能「先切片 5 只 → 再过滤」：后者在美股占比
   * 高的榜单（如涨跌幅榜）里会取不足 5 只，白丢样本。
   *
   * **变更记录（重要，旧取舍已被推翻）**：旧实现刻意**不**跟随 `hotSort`，而是只在首次
   * （amount 榜）取样一次写进 `perfBase`，并用硬编码 `'510300,510500,159915'` 兜底；
   * 理由是担心用户在「热门 ETF TOP 10」点一下「涨跌幅」就**静默**把另一张卡的折线图
   * 换成别的高风险标的（隐性副作用）。该取舍已被产品决策推翻：用户明确要求折线图跟随
   * 热门榜变化且不得写死。因此这里直接由 `hot` 派生，`perfBase` 与硬编码兜底一并删除，
   * 不保留两套逻辑。
   *
   * **为什么空串时不发请求**：后端 `/etf/performance` 在前端不传 symbols 时会回落到自己的
   * `DEFAULT_PERF`（`510300,510500,159915,513500,512100`）——那等于用写死样本冒充榜单数据，
   * 正是本项目的红线。故 A 股样本为 0 时 `perfSymbols` 为空串，`loadPerf` 直接置空走空态，
   * 并如实展示原因，宁可空着也不画假数据。
   *
   * 热门榜只拉 10 条，A 股不足 5 只时（例如只有 3 只）按实际条数传，不补默认值。
   */
  const perfCodes = useMemo(
    () => hot.filter((it) => it.country === 'cn').slice(0, 5),
    [hot],
  );
  const perfSymbols = useMemo(
    () => perfCodes.map((it) => it.code).join(','),
    [perfCodes],
  );
  /**
   * 样本口径披露文案（红线：派生样本须披露口径）。
   * 条数取自 `perfCodes`（过滤后的 A 股样本），排序口径取自后端回显的 `hotSortApplied`。
   *
   * ⚠️ **调用契约：本函数只在 `hotApplied === 'ok'` 时被渲染**（见下方渲染分支）。
   * 三态必须分开，否则任何两态合并出来都是谎报：
   *   ① `hotApplied === 'loading'`：还没有任何一次成功响应，**无数据可断言**，
   *      文案只说「加载中、样本待定」（此态不得调用本函数）；
   *   ② `hotApplied === 'unavailable'`：请求失败 / 返回 0 条 ⇒ 走琥珀色原因行；
   *   ③ 本函数：榜**确实有**数据（`hot` 非空），只是当前排序下头部全是境外标的
   *      （美股/日韩），过滤后 A 股为 0 —— 此时"本次 N 只均为境外标的"才是事实。
   */
  const perfSampleNote = useMemo(() => {
    if (!perfCodes.length) {
      return `热门榜当前排序下没有 A 股标的（本次 ${hot.length} 只均为境外标的），折线图不展示样本`;
    }
    return `样本：热门榜前 ${perfCodes.length} 只 A 股（按${hotSortApplied === 'amount' ? '成交额' : '涨跌幅'}排序）`;
  }, [perfCodes, hot.length, hotSortApplied]);
  const [perfPeriod, setPerfPeriod] = useState('1y');
  const [perfMetric, setPerfMetric] = useState<'pct' | 'price'>('pct');
  const loadPerf = useCallback(async () => {
    // A 股样本为 0 时**不请求**：后端会拿写死的默认样本兜底（见上方注释），那属于造假。
    if (!perfSymbols) { setPerf(null); return; }
    try { setPerf(await etfApi.performance(perfSymbols, perfMetric, perfPeriod)); }
    catch { setPerf(null); }
  }, [perfSymbols, perfMetric, perfPeriod]);

  useEffect(() => { void loadBase(); }, [loadBase]);
  useEffect(() => { void loadList(); }, [loadList]);
  useEffect(() => { void loadFlow(); }, [loadFlow]);
  useEffect(() => { void loadScale(); }, [loadScale]);
  useEffect(() => { void loadPerf(); }, [loadPerf]);
  useEffect(() => { void loadOverviewSeries(); }, [loadOverviewSeries]);
  useEffect(() => { setPage(1); }, [country, board, filters, sort]);

  /* ---------- 自选 ---------- */
  const toggleWatch = (code: string) => {
    setWatch((cur) => {
      const next = cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code];
      writeWatch(next);
      return next;
    });
  };
  /** 自选行情：从已加载的列表/热门里匹配，未加载到的显示代码。
   *  **取舍**：pool 只用于「按代码查不到就显示代码本身」的兜底查找，**顺序无意义**；
   *  `hotSort` 切到 pct 后，成交额头部标的可能不在 TOP10 里，若用户自选了它们
   *  则先由列表页数据匹配、仍匹配不到就退化为仅显示代码（既有行为），故不因此
   *  增加额外请求。 */
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
            {overview?.count_comparable === false ? '（口径不同，数量不可比）' : ''}
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
      <OverviewCards data={overview} series={overviewSeries} />

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
                      {PERF_PERIODS.map((p) => (
                        <option key={p.key} value={p.key}>{p.label}</option>
                      ))}
                    </select>
                  </div>
                }
                bodyCls="flex min-h-0 flex-col">
                <PerformanceChart data={perf} height={260} />
                {hotApplied === 'unavailable' ? (
                  <p className="mt-1 text-2xs leading-snug text-amber-600">
                    热门榜不可用，折线图不展示样本（不用写死标的兜底）：{hotReason ?? '热门榜数据暂时不可用'}
                  </p>
                ) : hotApplied === 'loading' ? (
                  // 首屏 / 热榜在途：**此刻没有任何数据可断言**，禁止出现任何标的构成描述
                  <p className="mt-1 text-2xs leading-snug text-ink-muted">
                    热门榜加载中…，折线图样本待定
                  </p>
                ) : (
                  <p className="mt-1 text-2xs leading-snug text-ink-muted">
                    {perfSampleNote}{hotInFlight ? '（正在刷新热门榜，样本可能更新）' : ''}
                  </p>
                )}
              </Card>
            </div>

            <div className="flex min-w-0 flex-col lg:col-span-1">
              <Card title="热门 ETF TOP 10"
                extra={
                  <Tabs value={hotSort} onChange={setHotSort}
                    items={[{ key: 'amount', label: '成交额' }, { key: 'pct', label: '涨跌幅' }]} />
                }
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
                        <tr><td colSpan={4}>
                          {hotApplied === 'unavailable' ? (
                            <div className="px-3 py-6 text-center text-xs leading-relaxed text-amber-600">
                              热门榜暂不可用：{hotReason ?? '数据源暂时不可用'}
                            </div>
                          ) : hotApplied === 'loading' ? (
                            <PanelEmpty minH="min-h-[120px]" text="热门榜加载中…" />
                          ) : (
                            <PanelEmpty minH="min-h-[120px]" />
                          )}
                        </td></tr>
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
                        {/* ⚠️ 不得用 `(it.net_inflow ?? 0)` 兜底：JS 里 `null ?? 0 === 0`，
                            而 `0 >= 0` 为 true ⇒ 数据源不可达(net_inflow 为 null)时会被判成
                            t-up(红) 且加上 '+'，渲染出红色的 `+—` —— 把"未知"呈现成了"正值方向"。
                            判空口径与紧邻的「净流入率」列一致：null 保持中性色、不加符号。 */}
                        <td className={`num text-right ${it.net_inflow == null ? '' : it.net_inflow >= 0 ? 't-up' : 't-down'}`}>
                          {it.net_inflow != null && it.net_inflow >= 0 ? '+' : ''}{yi(it.net_inflow)}
                        </td>
                        {/* ⚠️ 同上一格：不得用 `(it.inflow_ratio ?? 0)` 兜底。`null ?? 0 === 0` 且
                            `0 >= 0` 为 true ⇒ null 时该格被染成 t-up(红) —— 值虽已显示 '—'，
                            颜色却仍在替"未知"表方向。null 一律中性、不表态。 */}
                        <td className={`num text-right text-xs ${it.inflow_ratio == null ? '' : it.inflow_ratio >= 0 ? 't-up' : 't-down'}`}>
                          {it.inflow_ratio != null ? `${it.inflow_ratio >= 0 ? '+' : ''}${it.inflow_ratio.toFixed(2)}%` : '—'}
                        </td>
                      </tr>
                    )) : (
                      <tr><td colSpan={4}>
                        {flowStatus === 'unavailable' && flowReason ? (
                          <div className="px-3 py-6 text-center text-xs leading-relaxed text-amber-600">
                            资金流数据源暂不可用：{flowReason}
                          </div>
                        ) : (
                          <PanelEmpty minH="min-h-[120px]" />
                        )}
                      </td></tr>
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
                  <Chart option={scaleOption} height={190}
                    empty={scale?.reason ? `规模数据暂不可用：${scale.reason}` : '样本不足'} />
                </div>
                {scale?.status === 'unavailable' && scale.reason ? (
                  <p className="mt-1 text-2xs leading-snug text-amber-600">
                    规模数据暂不可用：{scale.reason}
                  </p>
                ) : (
                  <p className="mt-1 text-2xs leading-snug text-ink-muted">
                    {scale?.note ?? '估算口径：最新份额 × 历史收盘价'}
                    {scale ? ` · 样本 ${scale.sample_size} 只` : ''}
                  </p>
                )}
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
              <li>中国：东方财富 clist（主源）/ 新浪目录 + 腾讯市值（兜底源）；
                本次口径：<span className="text-ink-secondary">{sourceLabel(overview?.today?.source)}</span>
              </li>
              <li>美国：腾讯行情（16 只主要 ETF，实时）</li>
              <li>日本 / 韩国：<span className="text-ink-secondary">本土行情源不可达</span>，
                仅提供标的目录，行情显示「暂无数据」</li>
              <li>历史 K 线：腾讯财经（日线，最多 800 根）</li>
              <li>资金流：东方财富主力净流入口径
                <span className="text-ink-secondary">（源不可达时如实显示不可用，不用成交额冒充）</span></li>
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
