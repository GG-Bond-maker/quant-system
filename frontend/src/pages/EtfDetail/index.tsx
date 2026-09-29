/**
 * ETF 分析详情页（按设计稿重排）。
 *
 * 布局参考用户截图：
 *   1. 顶部：标题 + 关键指标行 + 自选/返回操作
 *   2. 上区：左侧 K 线 + 右侧资讯/新闻/持仓/行业
 *   3. 中区：K 线下方 Tab 切换条（视觉还原）
 *   4. 下区：跟踪误差分析 / 资金流动仪表盘 / 跟踪与基准 / 投资组合配置 / 成交量
 *            + 右侧「估值指标 / 讨论与情绪 / 相关产业链」信息卡片
 *
 * 数据来自 /api/v1/etf/detail/{code}，所有块独立降级。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import * as echarts from '@/lib/echarts';

import { etfApi } from '@/api/etf';
import KLineChart from '@/components/charts/KLineChart';
import {
  ErrorState, LoadingState, PanelEmpty, SectionCard, ViewToggle,
} from '@/components/ui';
import type { EtfDetail, EtfKlineBar } from '@/types/etf';
import { fmtAmountYi, fmtNum, fmtPct, pctClass } from '@/utils/format';

/* ==================== 常量 ==================== */
const WATCH_KEY = 'AQP_ETF_WATCH';
const KLINE_TABS = ['基本信息', '重仓明细', '行业配置', '资金流向'] as const;
const TRACK_PERIODS = [
  { key: '1m', label: '1月' },
  { key: '3m', label: '3月' },
  { key: '6m', label: '6月' },
  { key: '1y', label: '1年' },
] as const;

/* ==================== 工具 ==================== */
function readWatch(): string[] {
  try {
    const raw = localStorage.getItem(WATCH_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [];
  } catch { return []; }
}
function writeWatch(codes: string[]) {
  try { localStorage.setItem(WATCH_KEY, JSON.stringify(codes)); } catch { /* 隐私模式忽略 */ }
}

/**
 * 通用 ECharts 图容器。
 *
 * **本次改动（详情页去空白）**：图表的实际像素高度由**外层容器**决定（grid 拉伸 +
 * `flex-1 min-h-0`），容器不再写死 `style={{height: N}}`。因此这里补上
 * `ResizeObserver`：容器尺寸变化（窗口 resize / 行高被同行最高的卡片拉高）时
 * 主动 `chart.resize()`，让画布而不是留白带 shoulder 掉多余的高度。
 *
 * `minHeight` 退化为**兜底最小高度**（容器高度未解算出来的首帧），不再是渲染目标。
 */
function useChart(option: echarts.EChartsOption | null, minHeight: number) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !option) return;
    const host = ref.current;
    const chart = echarts.init(host);
    chart.setOption(option, true);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    // 行高由 grid 行内最高的兄弟卡片决定；容器一变高就必须跟上，否则图下方留白带
    const ro = new ResizeObserver(onResize);
    ro.observe(host);
    return () => {
      window.removeEventListener('resize', onResize);
      ro.disconnect();
      chart.dispose();
    };
  }, [option, minHeight]);
  return { ref };
}

/* ==================== 通用占位 ==================== */
function EmptyPanel({ text = '暂无数据' }: { text?: string }) {
  return <PanelEmpty text={text} minH="min-h-[120px]" />;
}

/* ==================== 子组件 ==================== */

/** 前十大重仓股列表（带横向条）
 *
 * 加 `max-h` + 内部滚动：右栏是决定上区 grid 行高的那一列，列表再长也不能把行撑高
 * （行高变大 ⇒ 左侧 K 线图被拉得过高）。
 * `justify-between`：在「投资组合配置」这类**被行高拉伸**的卡里，行均匀铺满卡内
 * 高度（不留底部空带）；在自然高度的卡（上区「持仓 Top 10」）里 justify-between
 * 退化为普通排列，行为不变。 */
function HoldingsList({ items }: { items: Array<{ code: string; name: string; ratio: number | null; mv_yi: number | null }> }) {
  if (!items.length) return <EmptyPanel />;
  const list = items.slice(0, 10);
  const max = Math.max(1, ...list.map((i) => i.ratio ?? 0));
  return (
    <div className="flex h-full max-h-[232px] flex-col justify-between gap-1.5 overflow-y-auto pr-0.5">
      {list.map((it, idx) => {
        const ratio = it.ratio ?? 0;
        const width = Math.min(100, Math.max(2, (ratio / max) * 100));
        return (
          <div key={it.code} className="flex items-center gap-2 text-xs">
            <span className="num w-4 shrink-0 text-center text-2xs text-ink-muted">{idx + 1}</span>
            <span className="w-20 shrink-0 truncate text-ink" title={it.name}>{it.name || it.code}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-sm bg-slate-100">
              <div className="h-full rounded-sm bg-blue-500" style={{ width: `${width}%` }} />
            </div>
            <span className="num w-12 shrink-0 text-right text-2xs text-ink">{ratio.toFixed(2)}%</span>
            <span className="num w-14 shrink-0 text-right text-2xs text-ink-muted">
              {it.mv_yi != null ? `${it.mv_yi.toFixed(2)}亿` : '—'}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** 行业配置甜甜圈 */
function IndustryPie({ items }: { items: Array<{ industry: string; ratio: number | null }> }) {
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!items.length) return null;
    const data = items
      .map((it) => ({ name: it.industry, value: it.ratio ?? 0 }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c}%', textStyle: { fontSize: 11 } },
      legend: { show: false },
      series: [{
        type: 'pie', radius: ['42%', '68%'], center: ['50%', '50%'],
        data,
        label: { fontSize: 9, formatter: '{b}\n{d}%' },
        labelLine: { length: 6, length2: 6 },
      }],
    };
  }, [items]);
  const { ref } = useChart(option, 200);
  if (!option) return <EmptyPanel text="暂无行业数据" />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 累计走势：ETF vs 基准 */
function TrackingChart({ points }: { points: Array<{ date: string; etf: number; index: number }> }) {
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!points.length) return null;
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 6, right: 6, top: 24, bottom: 18, containLabel: true },
      legend: { data: ['ETF', '基准指数'], top: 0, textStyle: { fontSize: 10 }, itemWidth: 10, itemHeight: 8 },
      xAxis: { type: 'category', data: points.map((p) => p.date.slice(5)), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', name: '%', nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 9, formatter: '{value}%' }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
      series: [
        { name: 'ETF', type: 'line', data: points.map((p) => p.etf), smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: '#3B82F6' }, itemStyle: { color: '#3B82F6' } },
        { name: '基准指数', type: 'line', data: points.map((p) => p.index), smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: '#F59E0B' }, itemStyle: { color: '#F59E0B' } },
      ],
    };
  }, [points]);
  const { ref } = useChart(option, 200);
  if (!option) return <EmptyPanel />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 跟踪误差时序：日收益差 */
function TrackingErrorChart({ points }: { points: Array<{ date: string; etf: number; index: number }> }) {
  const data = useMemo(() => {
    const diffs: { date: string; value: number }[] = [];
    for (let i = 1; i < points.length; i++) {
      const e = (points[i].etf / (points[i - 1].etf || 1) - 1) * 100;
      const b = (points[i].index / (points[i - 1].index || 1) - 1) * 100;
      diffs.push({ date: points[i].date, value: e - b });
    }
    return diffs;
  }, [points]);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data.length) return null;
    return {
      tooltip: { trigger: 'axis', formatter: (p: unknown) => {
        const ps = p as Array<{ axisValue: string; data: number }>;
        return `${ps[0]?.axisValue ?? ''}<br/>偏离: ${(ps[0]?.data ?? 0).toFixed(3)}%`;
      }},
      grid: { left: 6, right: 6, top: 8, bottom: 18, containLabel: true },
      xAxis: { type: 'category', data: data.map((d) => d.date.slice(5)), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, formatter: '{value}%' }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
      series: [{
        type: 'line', data: data.map((d) => d.value), smooth: true, showSymbol: false,
        lineStyle: { width: 1.5, color: '#8B5CF6' },
        areaStyle: { color: new (echarts as any).graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(139,92,246,0.25)' }, { offset: 1, color: 'rgba(139,92,246,0.02)' }]) },
      }],
    };
  }, [data]);
  const { ref } = useChart(option, 200);
  if (!option) return <EmptyPanel />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 成交量图 */
function VolumeChart({ bars }: { bars: EtfKlineBar[] }) {
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!bars.length) return null;
    const data = bars.map((b) => ({
      date: b.date,
      value: b.volume ?? 0,
      color: (b.close ?? 0) >= (b.open ?? 0) ? '#DC2626' : '#16A34A',
    }));
    return {
      tooltip: { trigger: 'axis', formatter: (p: unknown) => {
        const ps = p as Array<{ axisValue: string; data: number }>;
        return `${ps[0]?.axisValue ?? ''}<br/>成交量: ${fmtAmountYi((ps[0]?.data ?? 0) / 1e8)}`;
      }},
      grid: { left: 6, right: 6, top: 8, bottom: 18, containLabel: true },
      xAxis: { type: 'category', data: data.map((d) => d.date.slice(5)), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, formatter: (v: number) => fmtAmountYi(v / 1e8) }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
      series: [{
        type: 'bar', data: data.map((d) => ({ value: d.value, itemStyle: { color: d.color } })),
        barMaxWidth: 10,
      }],
    };
  }, [bars]);
  const { ref } = useChart(option, 200);
  if (!option) return <EmptyPanel />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 新闻与动态：真实基金公告（天天基金 F10） */
function NewsList({ items }: {
  items: Array<{ date: string; title: string; category: string; url: string | null }>;
}) {
  if (!items.length) return <EmptyPanel text="暂无公告数据" />;
  // 同上：限高 + 内部滚动，防止公告条数波动把右栏撑高进而拉高左侧 K 线图
  return (
    <ul className="max-h-[168px] space-y-1.5 overflow-y-auto pr-0.5">
      {items.slice(0, 6).map((it, i) => (
        <li key={`${it.date}-${i}`} className="flex items-start gap-2 text-xs">
          <span className="num shrink-0 text-2xs text-ink-muted">{it.date.slice(5)}</span>
          {it.url ? (
            <a
              href={it.url} target="_blank" rel="noreferrer"
              className="min-w-0 flex-1 truncate text-ink-secondary hover:text-brand-600 hover:underline"
              title={`[${it.category}] ${it.title}`}>
              {it.title}
            </a>
          ) : (
            <span className="min-w-0 flex-1 truncate text-ink-secondary" title={it.title}>{it.title}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** 仪表盘（单个） */
function Gauge({ title, value, pct, color }: { title: string; value: string; pct: number; color: string }) {
  const option = useMemo<echarts.EChartsOption>(() => ({
    series: [{
      type: 'gauge', startAngle: 180, endAngle: 0,
      min: 0, max: 100, splitNumber: 5,
      axisLine: { lineStyle: { width: 10, color: [[pct / 100, color], [1, '#E2E8F0']] as [number, string][] } },
      pointer: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      title: { offsetCenter: [0, '-35%'], fontSize: 10, color: '#64748B' },
      detail: {
        valueAnimation: true, offsetCenter: [0, '5%'], fontSize: 16, fontWeight: 'bold' as const, color: '#0F172A',
        formatter: () => value,
      },
      data: [{ value: pct, name: title }],
    }],
  }), [title, value, pct, color]);
  const { ref } = useChart(option, 110);
  return <div ref={ref} className="h-full min-h-[110px] w-full" />;
}

/** 资金流动 / 估值仪表盘组 */
function GaugeQuad({
  pe, pb, pePct, pbPct, fee, size,
}: {
  pe: number | null; pb: number | null; pePct: number | null; pbPct: number | null;
  fee: number | null; size: number | null;
}) {
  const p = (v: number | null, max: number) => Math.min(100, Math.max(0, (v ?? 0) / max * 100));
  // grid-rows-2 + flex-1：2×2 仪表盘随卡片可用高度长大（行高由同行最高的卡片决定）
  return (
    <div className="grid h-full min-h-[232px] grid-cols-2 grid-rows-2 gap-2">
      <Gauge title="PE 分位" value={pe != null ? pe.toFixed(2) : '—'} pct={p(pePct, 100)} color="#3B82F6" />
      <Gauge title="PB 分位" value={pb != null ? pb.toFixed(2) : '—'} pct={p(pbPct, 100)} color="#10B981" />
      <Gauge title="管理费率" value={fee != null ? `${fee}%` : '—'} pct={p(fee, 1)} color="#F59E0B" />
      <Gauge title="规模分位" value={size != null ? `${size.toFixed(1)}亿` : '—'} pct={p(size, 100)} color="#8B5CF6" />
    </div>
  );
}

/** ETF 概况 & 资讯 */
function InfoPanel({
  header, tracking, valuation,
}: {
  header: EtfDetail['blocks']['header'] | undefined;
  tracking: EtfDetail['blocks']['tracking'] | undefined;
  valuation: EtfDetail['blocks']['valuation'] | undefined;
}) {
  const last = tracking?.points?.length ? tracking.points[tracking.points.length - 1] : null;
  const out = last ? (last.etf - last.index).toFixed(2) : null;
  return (
    <SectionCard
      title="ETF 概况 & 资讯"
      action={<span className="text-2xs text-brand-600 hover:underline cursor-pointer">更多</span>}
      bodyClassName="p-3"
    >
      <div className="space-y-3 text-xs text-ink-secondary">
        <div>
          <h4 className="mb-1 font-medium text-ink">业绩 vs 基准</h4>
          <p className="leading-relaxed">
            该基金追踪 <span className="font-medium text-ink">{header?.tracking_index ?? '—'}</span>，
            {out ? ` 近区间相对基准${Number(out) >= 0 ? '超额' : '落后'} ${out}%。` : ' 暂无基准对比数据。'}
          </p>
        </div>

        <div>
          <h4 className="mb-1 font-medium text-ink">估值指标</h4>
          <div className="space-y-1">
            <div className="flex justify-between">
              <span>PE(TTM)</span>
              <span className="num text-ink">{valuation?.pe_ttm != null ? valuation.pe_ttm.toFixed(2) : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>市净率 PB</span>
              <span className="num text-ink">{valuation?.pb != null ? valuation.pb.toFixed(2) : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>PE 历史分位</span>
              <span className="num text-ink">{valuation?.pe_percentile != null ? `${valuation.pe_percentile.toFixed(1)}%` : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>PB 历史分位</span>
              <span className="num text-ink">{valuation?.pb_percentile != null ? `${valuation.pb_percentile.toFixed(1)}%` : '—'}</span>
            </div>
          </div>
        </div>
      </div>
    </SectionCard>
  );
}

/* ==================== 主页面 ==================== */
export default function EtfDetailPage() {
  const { code } = useParams<{ code: string }>();
  const [data, setData] = useState<EtfDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('day');
  const [watch, setWatch] = useState<string[]>(() => readWatch());
  const [klineTab, setKlineTab] = useState<(typeof KLINE_TABS)[number]>('基本信息');
  const [trackPeriod, setTrackPeriod] = useState<(typeof TRACK_PERIODS)[number]['key']>('1y');

  const load = async () => {
    if (!code) return;
    setLoading(true); setError(null);
    try {
      setData(await etfApi.detail(code, period));
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [code, period]);

  const toggleWatch = () => {
    if (!code) return;
    setWatch((cur) => {
      const next = cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code];
      writeWatch(next);
      return next;
    });
  };

  // blocks 为后端聚合块，单块缺失时整体可能为 undefined → 必须 `data?.blocks?.X`
  const header = data?.blocks?.header;
  const kline = data?.blocks?.kline;
  const holdings = data?.blocks?.holdings;
  const tracking = data?.blocks?.tracking;
  const valuation = data?.blocks?.valuation;
  const news = data?.blocks?.news;
  const sentiment = data?.blocks?.sentiment;
  const chain = data?.blocks?.chain;

  const klineBars = useMemo(() => {
    if (!kline?.bars) return [];
    return kline.bars.map((b) => ({
      date: b.date,
      open: b.open ?? 0,
      close: b.close ?? 0,
      high: b.high ?? 0,
      low: b.low ?? 0,
      volume: b.volume ?? 0,
      amount: null as number | null,
    }));
  }, [kline]);

  if (loading) return <LoadingState />;
  if (error || !data) return <ErrorState message={error || '加载失败'} onRetry={load} />;

  const isWatched = code ? watch.includes(code) : false;

  return (
    <div className="space-y-3">
      {/* ===== 顶部信息栏 ===== */}
      <div className="rounded-lg border border-hair bg-white p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-base font-semibold text-ink">
                ETF详情 — {header?.name ?? data.name}（{code}）
              </h1>
              {data.country && (
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-2xs text-ink-muted">
                  {{ cn: '中国', us: '美国', jp: '日本', kr: '韩国' }[data.country]}
                </span>
              )}
            </div>

            <div className="mt-3 grid grid-cols-3 gap-x-6 gap-y-2 md:grid-cols-4 lg:grid-cols-7">
              <div>
                <div className="text-2xs text-ink-secondary">最新价</div>
                <div className={`num text-lg font-semibold ${pctClass(header?.pct)}`}>
                  {header?.price != null ? fmtNum(header.price) : '—'}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">涨跌幅</div>
                <div className={`num text-lg font-semibold ${pctClass(header?.pct)}`}>
                  {fmtPct(header?.pct)}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">成交额</div>
                <div className="num text-base font-medium text-ink">
                  {fmtAmountYi(header?.amount != null ? header.amount / 1e8 : null)}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">管理费率</div>
                <div className="num text-base font-medium text-ink">
                  {header?.management_fee != null ? `${header.management_fee}%` : '—'}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">基金规模</div>
                <div className="num text-base font-medium text-ink">
                  {header?.size_yi != null ? `${header.size_yi.toFixed(2)} 亿` : '—'}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">成立日期</div>
                <div className="num text-base font-medium text-ink">{header?.inception ?? '—'}</div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">追踪指数</div>
                <div className="text-base font-medium text-ink">{header?.tracking_index ?? '—'}</div>
              </div>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <button onClick={toggleWatch}
              className={`flex items-center gap-1 rounded border px-3 py-1.5 text-xs transition-colors ${
                isWatched
                  ? 'border-amber-200 bg-amber-50 text-amber-600 hover:bg-amber-100'
                  : 'border-hair bg-white text-ink-secondary hover:border-brand-200 hover:text-brand-600'
              }`}>
              <span>{isWatched ? '★' : '☆'}</span>
              <span>{isWatched ? '已加自选' : '加自选'}</span>
            </button>
            <Link to="/etf"
              className="rounded bg-brand-500 px-3 py-1.5 text-xs text-white hover:bg-brand-600">
              返回 ETF 中心
            </Link>
          </div>
        </div>
      </div>

      {/* ===== 核心图表区 ===== */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        {/* 左侧 K 线 */}
        {/* 左侧 K 线
            `flex` + SectionCard `w-full`：让卡片**撑满 grid 行高**（grid 默认
            items-stretch 只拉伸到列 div，卡卡片自身是 auto 高，之前因此在图表下方
            留下整列 ≈380px 的死区）。卡片撑满后 body 用 flex-1 min-h-0 把剩余高度
            交给 KLineChart（`fill`）→ 图窗实测高度随行高走，空白消失。 */}
        <div className="flex min-w-0 xl:col-span-2">
          <SectionCard
            title="K 线走势"
            action={(
              <ViewToggle
                value={period}
                onChange={setPeriod}
                options={[
                  { key: 'day', label: '日K' },
                  { key: 'week', label: '周K' },
                  { key: 'month', label: '月K' },
                ]}
                title="K 线周期"
              />
            )}
            className="w-full"
            bodyClassName="flex min-h-0 flex-col"
          >
            {kline?.status === 'ok' && klineBars.length > 0 ? (
              <KLineChart bars={klineBars} adjust="qfq" onAdjustChange={() => {}} height={460} fill />
            ) : kline?.status === 'unavailable' ? (
              <EmptyPanel text={kline.reason || '暂无 K 线数据'} />
            ) : (
              <EmptyPanel text="加载中…" />
            )}
          </SectionCard>
        </div>

        {/* 右侧资讯/持仓/行业：这一列的内容决定上区 grid 行高 */}
        <div className="min-w-0 space-y-3">
          <InfoPanel header={header} tracking={tracking} valuation={valuation} />

          <SectionCard
            title="最新新闻与动态"
            action={news?.note ? <span className="text-2xs text-ink-muted">{news.note}</span> : undefined}
            bodyClassName="p-3"
          >
            {news?.status === 'ok' ? (
              <NewsList items={news.items} />
            ) : (
              <EmptyPanel text={news?.reason || '暂无公告数据'} />
            )}
          </SectionCard>

          <SectionCard
            title="持仓 Top 10"
            action={holdings?.holdings?.date ? (
              <span className="text-2xs text-ink-muted">报告期 {holdings.holdings.date}</span>
            ) : undefined}
            bodyClassName="p-3"
          >
            {holdings?.status === 'ok' ? (
              <HoldingsList items={holdings.holdings.items} />
            ) : (
              <EmptyPanel text={holdings?.reason || '暂无持仓数据'} />
            )}
          </SectionCard>

          <div className="grid grid-cols-2 gap-3">
            <SectionCard title="行业配置" bodyClassName="p-2">
              {holdings?.status === 'ok' ? (
                <IndustryPie items={holdings.industry.items} />
              ) : (
                <EmptyPanel text={holdings?.reason || '暂无行业数据'} />
              )}
            </SectionCard>
            <div className="flex flex-col justify-between rounded-lg border border-hair bg-white p-3">
              <div>
                <div className="text-sm font-semibold text-ink">ETF 中心</div>
                <p className="mt-1 text-2xs leading-relaxed text-ink-secondary">
                  返回列表浏览全部 ETF、对比业绩与资金流向。
                </p>
              </div>
              <Link to="/etf" className="mt-2 text-center text-xs text-brand-600 hover:underline">
                返回 ETF 中心 →
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* K 线下方 Tab（视觉还原） */}
      <div className="flex items-center gap-2 overflow-x-auto rounded-lg border border-hair bg-white px-3 py-2">
        {KLINE_TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setKlineTab(tab)}
            className={`whitespace-nowrap rounded-md px-3 py-1 text-xs transition-colors ${
              klineTab === tab
                ? 'bg-brand-50 font-medium text-brand-600'
                : 'text-ink-secondary hover:bg-slate-50'
            }`}>
            {tab}
          </button>
        ))}
      </div>

      {/* ===== 量化深度指标区 =====
          布局改造（去空白 / 视觉节奏）：
            旧版是单一 `xl:grid-cols-6` —— 1920 下每张卡只有 277px 宽，而内部图表
            写死 height=200，于是「窄而高 + 图表下方 476~606px 空白」；且第 6 列三卡
            堆叠（自然高 ~604px）把整行拉高，前 5 张卡全部跟着留白带（实测见报告）。
            新版：**图表区 + 右侧信息列** 两栏，图表区内部两行各 3 列 ⇒
            每张卡 ~454px（1920）/ ~308px（1440）；所有图表改为**随容器高度**渲染，
            卡片撑满所在行的剩余高度，行内最高者的「超出部分」被子图吃掉而非留白。 */}
      <div className="flex flex-col gap-3 xl:flex-row">
        {/* 图表区：两行 × 三列。
            两行都用 flex-auto：右侧信息列（自然高）比图表内容高时，多出的高度
            **分给两行图表**（卡片拉伸 → 图表随之变大），而不是在图表区底部留空带。 */}
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="grid flex-auto grid-cols-1 gap-3 md:grid-cols-3">
            {/* Tracking Error Analysis */}
            <SectionCard
              title="跟踪误差分析"
              action={(
                <ViewToggle
                  value={trackPeriod}
                  onChange={setTrackPeriod}
                  options={TRACK_PERIODS}
                  title="观察区间"
                />
              )}
              className="min-w-0"
              bodyClassName="flex min-h-0 flex-col"
            >
              {tracking?.status === 'ok' ? (
                <div className="min-h-0 flex-1">
                  <TrackingErrorChart points={tracking.points} />
                </div>
              ) : (
                <EmptyPanel text={tracking?.reason || '暂无数据'} />
              )}
              {tracking?.tracking_error != null && (
                <p className="mt-1 shrink-0 text-center text-2xs text-ink-muted">
                  年化跟踪误差 {tracking.tracking_error.toFixed(2)}%
                </p>
              )}
            </SectionCard>

            {/* 跟踪与基准分析 */}
            <SectionCard title="跟踪与基准分析" className="min-w-0"
              bodyClassName="flex min-h-0 flex-col">
              {tracking?.status === 'ok' ? (
                <div className="min-h-0 flex-1">
                  <TrackingChart points={tracking.points} />
                </div>
              ) : (
                <EmptyPanel text={tracking?.reason || '暂无数据'} />
              )}
            </SectionCard>

            {/* Volume */}
            <SectionCard title="成交量" className="min-w-0"
              bodyClassName="flex min-h-0 flex-col">
              {kline?.status === 'ok' ? (
                <div className="min-h-0 flex-1">
                  <VolumeChart bars={kline.bars} />
                </div>
              ) : (
                <EmptyPanel text={kline?.reason || '暂无数据'} />
              )}
            </SectionCard>
          </div>

          {/* 第二行：资金流动仪表盘（1 份宽）+ 投资组合配置（2 份宽） */}
          <div className="grid flex-auto grid-cols-1 gap-3 md:grid-cols-3">
            {/* 资金流动（基金特定）—— 用估值/费率仪表盘占位 */}
            <SectionCard title="资金流动（基金特定）" className="min-w-0"
              bodyClassName="flex min-h-0 flex-col">
              <div className="min-h-0 flex-1">
                <GaugeQuad
                  pe={valuation?.pe_ttm ?? null}
                  pb={valuation?.pb ?? null}
                  pePct={valuation?.pe_percentile ?? null}
                  pbPct={valuation?.pb_percentile ?? null}
                  fee={header?.management_fee ?? null}
                  size={header?.size_yi ?? null}
                />
              </div>
            </SectionCard>

            {/* 投资组合配置 */}
            <SectionCard title="投资组合配置" className="min-w-0 md:col-span-2"
              bodyClassName="flex min-h-0 flex-col">
              {holdings?.status === 'ok' ? (
                <HoldingsList items={holdings.holdings.items} />
              ) : (
                <EmptyPanel text={holdings?.reason || '暂无数据'} />
              )}
            </SectionCard>
          </div>
        </div>

        {/* 右侧信息卡片列：作为两行图表区的**等高旁栏**，避免旧版「第 6 列矮一截」 */}
        <aside className="flex w-full shrink-0 flex-col gap-3 xl:w-[300px]">
          <SectionCard title="估值指标" className="flex-1" bodyClassName="p-2">
            <div className="space-y-1 text-xs">
              <div className="flex justify-between">
                <span className="text-ink-secondary">PE(TTM)</span>
                <span className="num text-ink">{valuation?.pe_ttm != null ? valuation.pe_ttm.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-secondary">PB</span>
                <span className="num text-ink">{valuation?.pb != null ? valuation.pb.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-secondary">PE 分位</span>
                <span className="num text-ink">{valuation?.pe_percentile != null ? `${valuation.pe_percentile.toFixed(1)}%` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-secondary">PB 分位</span>
                <span className="num text-ink">{valuation?.pb_percentile != null ? `${valuation.pb_percentile.toFixed(1)}%` : '—'}</span>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="讨论与情绪" className="flex-1"
            bodyClassName="flex flex-col p-2">
            {sentiment?.status === 'ok' ? (
              <>
                <div className="min-h-0 flex-1">
                  <Gauge title="公告情绪" value={sentiment.label} pct={sentiment.score} color="#F59E0B" />
                </div>
                <div className="mt-1 flex shrink-0 justify-between text-2xs text-ink-muted">
                  <span>正面词 {sentiment.positive}</span>
                  <span>负面词 {sentiment.negative}</span>
                </div>
                <p className="mt-1 shrink-0 text-2xs leading-relaxed text-ink-muted">{sentiment.basis}</p>
              </>
            ) : (
              <EmptyPanel text={sentiment?.reason || '暂无情感数据'} />
            )}
          </SectionCard>

          <SectionCard
            title="相关产业链"
            action={chain?.note ? <span className="text-2xs text-ink-muted">归集</span> : undefined}
            className="flex-1"
            bodyClassName="p-2"
          >
            {chain?.status === 'ok' ? (
              <div className="space-y-1.5">
                {chain.items.map((it) => (
                  <div key={it.chain} className="flex items-center gap-2 text-xs">
                    <span className="w-16 shrink-0 truncate text-ink-secondary" title={it.chain}>{it.chain}</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-sm bg-slate-100">
                      <div className="h-full rounded-sm bg-indigo-500"
                           style={{ width: `${Math.min(100, Math.max(2, it.ratio))}%` }} />
                    </div>
                    <span className="num w-10 shrink-0 text-right text-xs text-ink">{it.ratio.toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyPanel text={chain?.reason || '暂无产业链数据'} />
            )}
          </SectionCard>
        </aside>
      </div>

      {/* ===== 数据来源说明 ===== */}
      <div className="rounded-lg border border-hair bg-white p-3 text-2xs leading-relaxed text-ink-muted">
        数据来源：K 线与行情来自腾讯财经；重仓股/行业配置/基金公告来自天天基金 F10；
        估值百分位来自乐咕乐股；资金流向来自东方财富（目前仅返回最近 1 个交易日）；
        情绪由基金公告标题经自建关键词词典统计得出，产业链由持仓细分行业归集，均为派生指标而非第三方直供。
        本页面仅用于学习研究，不构成投资建议。
      </div>
    </div>
  );
}
