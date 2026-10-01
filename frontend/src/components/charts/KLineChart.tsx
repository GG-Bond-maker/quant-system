/**
 * 同花顺风格 K 线图（ECharts 5；标准参考 kline-chart-ths.html）。
 *
 * - 阳线空心 / 阴线实心（色值取自 CSS 变量 `--up` / `--down`，
 *   亮色下为红 #D92B2B / 绿 #12995B，暗色下自动降饱和；**不硬编码**）；
 * - 主图：蜡烛 + 可切指标 MA(5,10,20,60) / BOLL(20,2) / EXPMA(12,50) / SAR / 无；
 * - 副图：VOL(5,10) 固定 + 副图1/副图2 可选 MACD(12,26,9) / KDJ(9,3,3) / RSI(6,12,24) / BOLL / CCI(14)；
 * - 主图双轴：左价格、右相对可视首根收盘的涨跌幅；
 * - 交互：滚轮缩放、拖拽平移、底部缩略导航、十字光标联动 tooltip、
 *   悬停时各窗格左上角指标数值实时刷新（无悬停显示最新值）；
 * - 周期：日/周/月/季/年（前端聚合，指标按周期就地重算）；
 * - 快捷键：F8 循环周期 · Ctrl+Q 前复权 · Ctrl+B 后复权（复权由父组件经后端切换）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import type { AdjustMode, KLineBar } from '@/types/stock';
import { useTheme } from '@/hooks/useTheme';
import { fmtVol } from '@/utils/format';

/* ==================== 主题色读取 ==================== */
/**
 * 取色逻辑已抽到 `@/lib/chartTheme`（全站图表共用）。
 *
 * 抽出的动因：本文件的 `cssVar` 曾是私有函数，其它图表想合规也无从调用，
 * 导致 8 个文件里散落 24 处硬编码色。现在统一走共享模块。
 */
/** 每次取色都现读，主题切换后重新渲染即生效（父组件 theme 变化触发重渲染） */
function palette() {
  const p = chartPalette();
  return {
    UP: p.UP,
    DOWN: p.DOWN,
    BG: p.CARD,                    // 阳线填充（空心效果，与卡片底同色）
    AXIS: p.HAIR,
    SPLIT: p.HAIR,
    TEXT_C: p.INKM,
    GRID_BG: p.CARD,
    LINE_BG: p.CARD,
    // 成交量副图柱体（比主图 K 线淡一档）
    VOL_UP: p.UP,
    VOL_DOWN: p.DOWN,
    TIP_BG: p.TIP_BG,              // 半透明浮层，不能用实色
    TIP_BORDER: p.TIP_BORDER,
    TIP_TEXT: p.TIP_TEXT,
    ZOOM_BG: p.ALT,
    ZOOM_HANDLE: p.HAIR2,
    ZOOM_LINE: p.HAIR,
    ZOOM_AREA: p.SUNKEN,
    POINTER_LABEL_BG: p.INK2,
    // 十字光标标签文字：取自 palette 的 ON_SOLID，不在此处再写字面量
    // （原为 cssVar('--ink-inverse', '#FFFFFF')，与 chartTheme 的取值口径重复）
    POINTER_LABEL_TEXT: p.ON_SOLID,
  };
}

/* 指标线色相（MA5/MA10/...) 保持跨主题不变 —— 它们是**类别色**而非语义色，
 * 暗色下若跟随主题反而会让两条不同指标线撞色。仅取值亮度适配暗底。
 * 已集中到 `chartTheme.CATEGORY_COLORS`，避免各图表各写一套。 */
const C_W = CATEGORY_COLORS.C1;  // MA5 / K / RSI6 / DIF
const C_Y = CATEGORY_COLORS.C2;  // MA10 / D / RSI12 / DEA
const C_P = CATEGORY_COLORS.C3;  // MA20 / J / RSI24
const C_G = CATEGORY_COLORS.C4;  // MA60
const LINE_COLORS: Record<string, string> = {
  MA5: C_W, MA10: C_Y, MA20: C_P, MA60: C_G,
  UPPER: C_W, MID: C_Y, LOWER: C_P,
  EXPMA12: C_W, EXPMA50: C_Y, SAR: C_W,
  K: C_W, D: C_Y, J: C_P,
  RSI6: C_W, RSI12: C_Y, RSI24: C_P,
  CCI: C_W, DIF: C_W, DEA: C_Y,
};

type Period = 'day' | 'week' | 'month' | 'quarter' | 'year';
type MainInd = 'MA' | 'BOLL' | 'EXPMA' | 'SAR' | 'NONE';
type SubInd = 'MACD' | 'KDJ' | 'RSI' | 'BOLL' | 'CCI' | 'NONE';

const PERIODS: Array<{ key: Period; label: string }> = [
  { key: 'day', label: '日K' }, { key: 'week', label: '周K' }, { key: 'month', label: '月K' },
  { key: 'quarter', label: '季K' }, { key: 'year', label: '年K' },
];
const MAIN_LABELS: Record<MainInd, string> = {
  MA: 'MA(5,10,20,60)', BOLL: 'BOLL(20,2)', EXPMA: 'EXPMA(12,50)', SAR: 'SAR(0.02,0.2)', NONE: '',
};
const SUB_LABELS: Record<Exclude<SubInd, 'NONE'>, string> = {
  MACD: 'MACD(12,26,9)', KDJ: 'KDJ(9,3,3)', RSI: 'RSI(6,12,24)', BOLL: 'BOLL(20,2)', CCI: 'CCI(14)',
};

/* ==================== 工具与指标（纯函数，与标准口径一致） ==================== */
const r2 = (v: number) => Math.round(v * 100) / 100;

/* `fmtVol` 已抽到 `@/utils/format` 作为成交量格式化的单一事实源
 * （ETF 详情成交量图与其共用；两处实现逐字节一致）。 */
const disp = (iso: string) => iso.replaceAll('-', '/');

function ma(values: number[], n: number): Array<number | null> {
  const out: Array<number | null> = [];
  let sum = 0;
  values.forEach((v, i) => {
    sum += v;
    if (i >= n) sum -= values[i - n];
    out.push(i >= n - 1 ? r2(sum / n) : null);
  });
  return out;
}

function ema(values: number[], n: number): number[] {
  const k = 2 / (n + 1);
  const out: number[] = [];
  let prev = values[0] ?? 0;
  values.forEach((v, i) => {
    prev = i === 0 ? v : v * k + prev * (1 - k);
    out.push(prev);
  });
  return out;
}

/** Wilder 平滑（RSI 用） */
function wilderMa(values: number[], n: number): Array<number | null> {
  const out: Array<number | null> = [];
  let sum = 0;
  for (let i = 0; i < n && i < values.length; i++) sum += values[i];
  let prev = sum / n;
  values.forEach((_, i) => {
    if (i < n - 1) { out.push(null); return; }
    if (i >= n) prev = (values[i] + prev * (n - 1)) / n;
    out.push(prev);
  });
  return out;
}

function boll(closes: number[], n = 20, k = 2) {
  const mid = ma(closes, n);
  const up: Array<number | null> = [], low: Array<number | null> = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < n - 1) { up.push(null); low.push(null); continue; }
    const m = mid[i]!;
    let s = 0;
    for (let j = 0; j < n; j++) { const d = closes[i - j] - m; s += d * d; }
    const sd = Math.sqrt(s / n);
    up.push(r2(m + k * sd));
    low.push(r2(m - k * sd));
  }
  return { mid, up, low };
}

function expma(closes: number[], n1: number, n2: number) {
  return {
    e1: ema(closes, n1).map(r2),
    e2: ema(closes, n2).map(r2),
  };
}

/** 标准 SAR（Wilder） */
function sar(bars: KLineBar[], step = 0.02, maxStep = 0.2): number[] {
  const n = bars.length;
  const out = new Array<number>(n).fill(0);
  if (n === 0) return out;
  let trend = 1, ep = bars[0].high, af = step;
  out[0] = bars[0].low;
  for (let i = 1; i < n; i++) {
    const prev = out[i - 1];
    let v = prev + af * (ep - prev);
    if (trend === 1) {
      v = Math.min(v, bars[i - 1].low, i >= 2 ? bars[i - 2].low : bars[i - 1].low);
      if (bars[i].low < v) { trend = -1; v = ep; ep = bars[i].low; af = step; }
      else if (bars[i].high > ep) { ep = bars[i].high; af = Math.min(af + step, maxStep); }
    } else {
      v = Math.max(v, bars[i - 1].high, i >= 2 ? bars[i - 2].high : bars[i - 1].high);
      if (bars[i].high > v) { trend = 1; v = ep; ep = bars[i].high; af = step; }
      else if (bars[i].low < ep) { ep = bars[i].low; af = Math.min(af + step, maxStep); }
    }
    out[i] = r2(v);
  }
  return out;
}

function macdCalc(closes: number[], f = 12, s = 26, m = 9) {
  const ef = ema(closes, f), es = ema(closes, s);
  const dif = ef.map((v, i) => v - es[i]);
  const dea = ema(dif, m);
  const bar = dif.map((v, i) => (v - dea[i]) * 2);
  return {
    dif: dif.map((v) => Math.round(v * 1000) / 1000),
    dea: dea.map((v) => Math.round(v * 1000) / 1000),
    bar: bar.map((v) => Math.round(v * 1000) / 1000),
  };
}

function kdjCalc(bars: KLineBar[], n = 9, m1 = 3, m2 = 3) {
  const len = bars.length;
  const rsv: number[] = [];
  for (let i = 0; i < len; i++) {
    if (i < n - 1) { rsv.push(50); continue; }
    let hh = -Infinity, ll = Infinity;
    for (let j = 0; j < n; j++) {
      hh = Math.max(hh, bars[i - j].high);
      ll = Math.min(ll, bars[i - j].low);
    }
    rsv.push(hh === ll ? 50 : ((bars[i].close - ll) / (hh - ll)) * 100);
  }
  const K: number[] = [], D: number[] = [], J: number[] = [];
  let pk = 50, pd = 50;
  for (let i = 0; i < len; i++) {
    pk = (rsv[i] + pk * (m1 - 1)) / m1;
    pd = (pk + pd * (m2 - 1)) / m2;
    K.push(r2(pk)); D.push(r2(pd)); J.push(r2(3 * pk - 2 * pd));
  }
  return { k: K, d: D, j: J };
}

function rsiCalc(bars: KLineBar[], p1 = 6, p2 = 12, p3 = 24) {
  const up: number[] = [], dn: number[] = [];
  bars.forEach((b, i) => {
    if (i === 0) { up.push(0); dn.push(0); return; }
    const diff = b.close - bars[i - 1].close;
    up.push(diff > 0 ? diff : 0);
    dn.push(diff < 0 ? -diff : 0);
  });
  const one = (p: number) => {
    const au = wilderMa(up, p), ad = wilderMa(dn, p);
    return au.map((v, i) => {
      if (v == null || ad[i] == null) return null;
      const denom = v + ad[i]!;
      return denom === 0 ? 50 : r2((v / denom) * 100);
    });
  };
  return { r1: one(p1), r2: one(p2), r3: one(p3) };
}

function cciCalc(bars: KLineBar[], n = 14): Array<number | null> {
  const tp = bars.map((d) => (d.high + d.low + d.close) / 3);
  const out: Array<number | null> = [];
  for (let i = 0; i < bars.length; i++) {
    if (i < n - 1) { out.push(null); continue; }
    let s = 0;
    for (let j = 0; j < n; j++) s += tp[i - j];
    const m = s / n;
    let md = 0;
    for (let j = 0; j < n; j++) md += Math.abs(tp[i - j] - m);
    md /= n;
    out.push(md === 0 ? 0 : r2((tp[i] - m) / (0.015 * md)));
  }
  return out;
}

function volMa(vols: number[], n: number): Array<number | null> {
  return ma(vols, n);
}

/* ==================== 周期聚合 ==================== */
function periodKey(dateStr: string, period: Period): string {
  const [y, m] = dateStr.split('-');
  if (period === 'day') return dateStr;
  if (period === 'week') {
    const d = new Date(`${dateStr}T00:00:00Z`);
    const offset = (d.getUTCDay() + 6) % 7; // Mon=0
    d.setUTCDate(d.getUTCDate() - offset);
    return d.toISOString().slice(0, 10);
  }
  if (period === 'month') return `${y}-${m}`;
  if (period === 'quarter') return `${y}-Q${Math.floor((Number(m) - 1) / 3) + 1}`;
  return y;
}

function aggregate(bars: KLineBar[], period: Period): KLineBar[] {
  if (period === 'day') return bars;
  const out: KLineBar[] = [];
  let cur: KLineBar | null = null;
  let curKey = '';
  for (const b of bars) {
    const key = periodKey(b.date, period);
    if (!cur || key !== curKey) {
      if (cur) out.push(cur);
      curKey = key;
      cur = { ...b, date: b.date }; // 保留组内最后一根日期
    } else {
      cur.high = Math.max(cur.high, b.high);
      cur.low = Math.min(cur.low, b.low);
      cur.close = b.close;
      cur.volume += b.volume;
      cur.amount = (cur.amount ?? 0) + (b.amount ?? 0);
      cur.date = b.date;
    }
  }
  if (cur) out.push(cur);
  return out;
}

/* ==================== 组件 ==================== */
interface Props {
  bars: KLineBar[];
  adjust: AdjustMode;
  onAdjustChange: (a: AdjustMode) => void;
  /** 图窗像素高度。``fill`` 为 false 时按此值渲染（既有行为，全站默认路径）。 */
  height?: number;
  /**
   * 是否改为**撑满父容器剩余高度**（由容器 flex 布局给定可用高度）。
   *
   * 这是一个**显式开关**：默认 false，此时组件的行高、内部grid布局、setOption 内容
   * 与改造前**逐字节一致**——``KLineChart`` 是全站共用组件（个股详情等页面在用），
   * 不允许因为 ETF 详情页的布局需求改变既有调用点的渲染结果。
   *
   * ``fill`` 为 true 时：
   *   - 根节点改为 ``flex h-full flex-col``（周期栏/工具栏/底注 ``shrink-0``）；
   *   - 图窗容器 ``flex-1 min-h-0``，ECharts 宿主 ``absolute inset-0``；
   *   - 布局高度取自宿主**实测** clientHeight（ResizeObserver 跟随），而非 ``height``。
   */
  fill?: boolean;
}

const selectCls = 'rounded-sm border border-hair2 bg-surface px-1.5 py-0.5 text-xs text-ink-secondary outline-none cursor-pointer';

export default function KLineChart({ bars, adjust, onAdjustChange, height = 580, fill = false }: Props) {
  /** 主题：ECharts 是 canvas 渲染，不参与 CSS 级联，需据此重算色值 */
  const theme = useTheme();
  const [period, setPeriod] = useState<Period>('day');
  const [mainInd, setMainInd] = useState<MainInd>('MA');
  const [sub1, setSub1] = useState<SubInd>('MACD');
  const [sub2, setSub2] = useState<SubInd>('NONE');
  /** fill 模式下宿主的实测高度；非 fill 模式恒等于 ``height``（不参与任何计算） */
  const [hostHeight, setHostHeight] = useState<number>(height);

  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const labelMainRef = useRef<HTMLDivElement | null>(null);
  const labelVolRef = useRef<HTMLDivElement | null>(null);
  const labelSub1Ref = useRef<HTMLDivElement | null>(null);
  const labelSub2Ref = useRef<HTMLDivElement | null>(null);
  /** 供 tooltip / dataZoom 回调读取的当前渲染上下文（绕过 ECharts 闭包陈旧问题） */
  const ctxRef = useRef<{
    data: KLineBar[];
    main: Record<string, Array<number | null>>;
    mainType: MainInd; sub1: SubInd; sub2: SubInd; period: Period;
  }>({ data: [], main: {}, mainType: 'MA', sub1: 'MACD', sub2: 'NONE', period: 'day' });
  const periodRef = useRef(period);
  periodRef.current = period;

  const data = useMemo(() => aggregate(bars, period), [bars, period]);

  /* 主题色板：依赖 theme 触发重算（暗色/亮色切换后 ECharts 需要新色值重绘） */
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const pal = useMemo(() => palette(), [theme]);

  /* ---- 主图指标序列（同时写入 ctxRef） ---- */
  const mainSeries = useMemo(() => {
    const closes = data.map((b) => b.close);
    const series: echarts.SeriesOption[] = [];
    const ind: Record<string, Array<number | null>> = {};
    const line = (name: string, arr: Array<number | null>) => ({
      name, type: 'line' as const, xAxisIndex: 0, yAxisIndex: 0,
      data: arr, smooth: true, showSymbol: false,
      lineStyle: { color: LINE_COLORS[name], width: 1.1 },
    });
    if (mainInd === 'MA') {
      ([5, 10, 20, 60] as const).forEach((n) => {
        const arr = ma(closes, n);
        ind[`MA${n}`] = arr;
        series.push(line(`MA${n}`, arr));
      });
    } else if (mainInd === 'BOLL') {
      const b = boll(closes);
      ind.UPPER = b.up; ind.MID = b.mid; ind.LOWER = b.low;
      series.push(line('UPPER', b.up), line('MID', b.mid), line('LOWER', b.low));
    } else if (mainInd === 'EXPMA') {
      const e = expma(closes, 12, 50);
      ind.EXPMA12 = e.e1; ind.EXPMA50 = e.e2;
      series.push(line('EXPMA12', e.e1), line('EXPMA50', e.e2));
    } else if (mainInd === 'SAR') {
      const s = sar(data);
      ind.SAR = s;
      series.push({
        name: 'SAR', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 3.5,
        data: s.map((v, i) => ({ value: v, itemStyle: { color: data[i].close >= v ? pal.UP : pal.DOWN } })),
      });
    }
    return { series, ind };
  }, [data, mainInd]);

  /* ---- 副图指标序列 ---- */
  const buildSub = (type: SubInd, xIdx: number): { series: echarts.SeriesOption[]; label: string; lines: number[] } => {
    const series: echarts.SeriesOption[] = [];
    const line = (name: string, arr: Array<number | null>) => ({
      name, type: 'line' as const, xAxisIndex: xIdx, yAxisIndex: xIdx + 1,
      data: arr, smooth: true, showSymbol: false,
      lineStyle: { color: LINE_COLORS[name], width: 1.1 },
    });
    if (type === 'NONE') return { series, label: '', lines: [] };
    if (type === 'MACD') {
      const closes = data.map((b) => b.close);
      const m = macdCalc(closes);
      series.push({
        name: 'MACD', type: 'bar', xAxisIndex: xIdx, yAxisIndex: xIdx + 1, barWidth: '55%',
        data: m.bar.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? pal.UP : pal.DOWN } })),
      });
      series.push(line('DIF', m.dif), line('DEA', m.dea));
      return { series, label: SUB_LABELS.MACD, lines: [0] };
    }
    if (type === 'KDJ') {
      const k = kdjCalc(data);
      series.push(line('K', k.k), line('D', k.d), line('J', k.j));
      return { series, label: SUB_LABELS.KDJ, lines: [20, 50, 80] };
    }
    if (type === 'RSI') {
      const r = rsiCalc(data);
      series.push(line('RSI6', r.r1), line('RSI12', r.r2), line('RSI24', r.r3));
      return { series, label: SUB_LABELS.RSI, lines: [20, 50, 80] };
    }
    if (type === 'BOLL') {
      const b = boll(data.map((x) => x.close));
      series.push(line('UPPER', b.up), line('MID', b.mid), line('LOWER', b.low));
      return { series, label: SUB_LABELS.BOLL, lines: [] };
    }
    const c = cciCalc(data);
    series.push(line('CCI', c));
    return { series, label: SUB_LABELS.CCI, lines: [100, -100] };
  };

  /* ---- 布局：主图自适应高度 + 各副图固定 74px ---- */
  // fill 模式用宿主实测高度；非 fill 模式用 ``height`` —— 与改造前完全一致。
  const chartHeight = fill ? hostHeight : height;
  const layout = useMemo(() => {
    const subs = ['VOL', ...(sub1 !== 'NONE' ? ['S1'] : []), ...(sub2 !== 'NONE' ? ['S2'] : [])];
    const TOP = 8, SUB_H = 74, GAP = 14, BOTTOM = 46;
    const avail = chartHeight - TOP - BOTTOM;
    const mainH = avail - subs.length * (SUB_H + GAP);
    const grids = [{ left: 62, right: 64, top: TOP, height: mainH }];
    const labelTops = [TOP + 4];
    let y = TOP + mainH + GAP;
    subs.forEach(() => {
      grids.push({ left: 62, right: 64, top: y, height: SUB_H });
      labelTops.push(y + 3);
      y += SUB_H + GAP;
    });
    return { grids, labelTops, count: subs.length + 1 };
  }, [chartHeight, sub1, sub2]);

  /* ---- M9/M10 修复：dataZoom 防抖计时器与 ResizeObserver 实例（卸载时释放） ---- */
  const zoomTimerRef = useRef<number | undefined>(undefined);
  const roRef = useRef<ResizeObserver | null>(null);

  /** fill 开关的最新值：ResizeObserver 需要读它，但不能因它重建 observer */
  const fillRef = useRef(fill);
  fillRef.current = fill;

  /* ---- 图表构建与渲染 ---- */
  useEffect(() => {
    const host = hostRef.current;
    if (!host || data.length === 0) return;

    if (!chartRef.current) {
      chartRef.current = echarts.init(host);
      // M9 修复：updatePriceScale 每帧同步执行 getOption+setOption，
      // 长序列拖动缩放会卡顿 —— 改为 80ms 防抖
      chartRef.current.on('dataZoom', () => {
        window.clearTimeout(zoomTimerRef.current);
        zoomTimerRef.current = window.setTimeout(() => updatePriceScale(), 80);
      });
      host.addEventListener('contextmenu', (e) => e.preventDefault());
      // M10 修复：保存 observer 实例，组件卸载时 disconnect（原实现泄漏）
      const ro = new ResizeObserver(() => {
        chartRef.current?.resize();
        // fill 模式：容器高度变化必须回写，否则 grid 布局（像素级）停留在旧高度
        if (fillRef.current) {
          const h = Math.round(host.getBoundingClientRect().height);
          if (h > 0) setHostHeight(h);
        }
      });
      roRef.current = ro;
      ro.observe(host);
    }
    // fill 模式首帧：宿主已有确定高度，先同步一次，避免用旧值算出一版再纠正
    if (fill) {
      const h = Math.round(host.getBoundingClientRect().height);
      if (h > 0 && h !== hostHeight) setHostHeight(h);
    }
    const chart = chartRef.current;
    ctxRef.current = { data, main: mainSeries.ind, mainType: mainInd, sub1, sub2, period };
    const dates = data.map((b) => disp(b.date));

    const xAxis = layout.grids.map((_, g) => ({
      type: 'category' as const, gridIndex: g, data: dates, boundaryGap: true,
      axisLine: { lineStyle: { color: pal.AXIS } },
      axisTick: { show: false },
      axisLabel: { show: g === layout.count - 1, color: pal.TEXT_C, fontSize: 10 },
      splitLine: { show: false },
      axisPointer: { label: { backgroundColor: pal.POINTER_LABEL_BG, color: pal.POINTER_LABEL_TEXT } },
    }));

    const yAxis: echarts.YAXisComponentOption[] = [
      { // 主图左：价格
        scale: true, gridIndex: 0, position: 'left' as const, splitNumber: 5,
        axisLine: { show: false }, axisTick: { show: false },
        splitLine: { lineStyle: { color: pal.SPLIT } },
        axisLabel: { color: pal.TEXT_C, fontSize: 10, margin: 6 },
      },
      { // 主图右：相对可视首根收盘的涨跌幅
        scale: true, gridIndex: 0, position: 'right' as const, splitNumber: 5,
        axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: {
          fontSize: 10, margin: 6,
          formatter: (v: number) => {
            const base = baseRef.current;
            if (base == null) return '';
            const pct = ((v - base) / base) * 100;
            const t = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
            return `{${pct >= 0 ? 'u' : 'd'}|${t}}`;
          },
          rich: { u: { color: pal.UP, fontSize: 10 }, d: { color: pal.DOWN, fontSize: 10 } },
        },
      },
    ];
    for (let g = 1; g < layout.count; g++) {
      yAxis.push({
        scale: true, gridIndex: g, splitNumber: 2,
        axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: { color: pal.TEXT_C, fontSize: 9, margin: 5 },
      });
    }

    const series: echarts.SeriesOption[] = [
      {
        name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
        data: data.map((b) => [b.open, b.close, b.low, b.high]),
        itemStyle: { color: pal.BG, color0: pal.DOWN, borderColor: pal.UP, borderColor0: pal.DOWN, borderWidth: 1 },
      },
      ...mainSeries.series,
    ];

    // 成交量（颜色对比前收，与标准一致）
    const volGrid = 1;
    const vols = data.map((b) => b.volume);
    const volColors = data.map((b, i) =>
      (i === 0 ? b.close >= b.open : b.close >= data[i - 1].close) ? pal.UP : pal.DOWN);
    series.push({
      name: 'VOL', type: 'bar', xAxisIndex: volGrid, yAxisIndex: volGrid + 1, barWidth: '60%',
      data: vols.map((v, i) => ({ value: v, itemStyle: { color: volColors[i] } })),
    }, {
      name: 'VMA5', type: 'line', xAxisIndex: volGrid, yAxisIndex: volGrid + 1,
      data: volMa(vols, 5), smooth: true, showSymbol: false, lineStyle: { color: C_W, width: 1 },
    }, {
      name: 'VMA10', type: 'line', xAxisIndex: volGrid, yAxisIndex: volGrid + 1,
      data: volMa(vols, 10), smooth: true, showSymbol: false, lineStyle: { color: C_Y, width: 1 },
    });

    // 副图1 / 副图2
    let nextGrid = 2;
    const s1 = sub1 !== 'NONE' ? buildSub(sub1, nextGrid) : null;
    if (s1) { addMarkLines(s1, series); nextGrid++; }
    const s2 = sub2 !== 'NONE' ? buildSub(sub2, nextGrid) : null;
    if (s2) addMarkLines(s2, series);

    function addMarkLines(sub: { series: echarts.SeriesOption[]; lines: number[] }, target: echarts.SeriesOption[]) {
      if (sub.lines.length && sub.series.length) {
        (sub.series[0] as { markLine?: unknown }).markLine = {
          silent: true, symbol: 'none',
          lineStyle: { color: pal.AXIS, type: 'dashed', width: 1 },
          label: { show: false },
          data: sub.lines.map((v) => ({ yAxis: v })),
        };
      }
      target.push(...sub.series);
    }

    const initialStart = data.length > 80 ? 100 - (80 / data.length) * 100 : 0;
    const option: echarts.EChartsCoreOption = {
      backgroundColor: pal.GRID_BG,
      animation: false,
      tooltip: {
        trigger: 'axis',
        confine: true,
        axisPointer: { type: 'cross' },
        backgroundColor: pal.TIP_BG,
        borderColor: pal.TIP_BORDER,
        borderWidth: 1,
        extraCssText: 'box-shadow:0 2px 8px rgba(0,0,0,0.08);',
        textStyle: { color: pal.TIP_TEXT, fontSize: 12 },
        formatter: tooltipFormatter,
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }], label: { backgroundColor: pal.POINTER_LABEL_BG, color: pal.POINTER_LABEL_TEXT } },
      grid: layout.grids,
      xAxis,
      yAxis,
      dataZoom: [
        {
          type: 'inside', xAxisIndex: layout.grids.map((_, i) => i),
          start: initialStart, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true,
        },
        {
          type: 'slider', xAxisIndex: layout.grids.map((_, i) => i), bottom: 8, height: 16,
          start: initialStart, end: 100,
          borderColor: 'transparent', backgroundColor: pal.ZOOM_BG,
          fillerColor: 'rgba(22,119,255,0.08)',
          handleStyle: { color: pal.ZOOM_HANDLE },
          moveHandleStyle: { color: pal.ZOOM_HANDLE },
          dataBackground: { lineStyle: { color: pal.ZOOM_LINE }, areaStyle: { color: pal.ZOOM_AREA } },
          textStyle: { color: pal.TEXT_C, fontSize: 10 },
        },
      ],
      series,
    };
    chart.setOption(option, true);
    updatePriceScale();
    updateIndLabels(data.length - 1);

    // 各窗格左上角指标标签定位
    const place = (el: HTMLDivElement | null, top: number, text: string) => {
      if (!el) return;
      el.style.top = `${top}px`;
      el.style.display = 'block';
      el.dataset.label = text;
    };
    place(labelMainRef.current, layout.labelTops[0], '');
    place(labelVolRef.current, layout.labelTops[1], 'VOL(5,10)');
    if (s1) place(labelSub1Ref.current, layout.labelTops[2], s1.label);
    else if (labelSub1Ref.current) labelSub1Ref.current.style.display = 'none';
    if (s2) place(labelSub2Ref.current, layout.labelTops[layout.count - 1], s2.label);
    else if (labelSub2Ref.current) labelSub2Ref.current.style.display = 'none';
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, mainSeries, layout, sub1, sub2, period, mainInd]);

  useEffect(() => () => {
    // M9/M10 修复：清理待执行的防抖任务与 ResizeObserver，再销毁实例
    window.clearTimeout(zoomTimerRef.current);
    roRef.current?.disconnect();
    roRef.current = null;
    chartRef.current?.dispose();
    chartRef.current = null;
  }, []);

  /* ---- 涨幅基准：可视区间首根收盘（dataZoom 后更新） ---- */
  const baseRef = useRef<number | null>(null);
  function updatePriceScale() {
    const chart = chartRef.current;
    const ctx = ctxRef.current;
    if (!chart || !ctx.data.length) return;
    const opt = chart.getOption() as { dataZoom?: Array<{ start?: number; end?: number }> };
    const dz = opt.dataZoom?.[0];
    if (!dz) return;
    const n = ctx.data.length;
    let s = Math.floor(((dz.start ?? 0) / 100) * (n - 1));
    let e = Math.ceil(((dz.end ?? 100) / 100) * (n - 1));
    s = Math.max(0, Math.min(n - 1, s));
    e = Math.max(s, Math.min(n - 1, e));

    let min = Infinity, max = -Infinity;
    for (let i = s; i <= e; i++) {
      const d = ctx.data[i];
      min = Math.min(min, d.low); max = Math.max(max, d.high);
      Object.values(ctx.main).forEach((arr) => {
        const v = arr[i];
        if (v != null) { min = Math.min(min, v); max = Math.max(max, v); }
      });
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return;
    const pad = (max - min) * 0.06 || 0.05;
    min -= pad; max += pad;
    baseRef.current = ctx.data[s].close;
    chart.setOption({ yAxis: [{ min, max }, { min, max }] });
  }

  /* ---- 悬停 / 默认指标数值标签 ---- */
  function updateIndLabels(idx: number) {
    const ctx = ctxRef.current;
    const d = ctx.data[idx];
    if (!d) return;
    const names = Object.keys(ctx.main);
    const html = (ctx.mainType === 'NONE' ? '' : `${MAIN_LABELS[ctx.mainType]} `) +
      names.map((k) => {
        const v = ctx.main[k][idx];
        return `<b style="font-weight:500;color:${LINE_COLORS[k] ?? pal.TEXT_C}">${k}:${v == null ? '--' : v}</b>`;
      }).join(' ');
    if (labelMainRef.current) labelMainRef.current.innerHTML = html;
    if (labelVolRef.current) {
      labelVolRef.current.innerHTML =
        `<span style="color:${pal.TEXT_C}">VOL(5,10)</span> 成交量 <b style="font-weight:500">${fmtVol(d.volume)}</b>`;
    }
  }

  function tooltipFormatter(params: unknown): string {
    const ps = params as Array<{ seriesName: string; dataIndex: number; data: unknown; marker: string }>;
    if (!ps || !ps.length) return '';
    const ctx = ctxRef.current;
    const idx = ps[0].dataIndex;
    const d = ctx.data[idx];
    if (!d) return '';
    updateIndLabels(idx);
    const prev = idx > 0 ? ctx.data[idx - 1].close : d.open;
    const diff = idx === 0 ? 0 : d.close - prev;
    const pct = idx === 0 ? 0 : (diff / prev) * 100;
    const col = diff >= 0 ? pal.UP : pal.DOWN;

    const ff = pal.TIP_TEXT;
    let h = `<div style="font-weight:600;color:${ff};margin-bottom:4px">${disp(d.date)}</div>`;
    h += `<div>开 <b>${d.open}</b> 高 <b>${d.high}</b> 低 <b>${d.low}</b> 收 <b>${d.close}</b></div>`;
    h += `<div>涨跌 <span style="color:${col}">${diff >= 0 ? '+' : ''}${diff.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)</span></div>`;
    h += `<div style="margin-top:3px">成交量 <b>${fmtVol(d.volume)}</b></div>`;

    const order: string[] = [];
    if (ctx.mainType === 'MA') order.push('MA5', 'MA10', 'MA20', 'MA60');
    else if (ctx.mainType === 'BOLL') order.push('UPPER', 'MID', 'LOWER');
    else if (ctx.mainType === 'EXPMA') order.push('EXPMA12', 'EXPMA50');
    else if (ctx.mainType === 'SAR') order.push('SAR');
    if (ctx.sub1 === 'MACD') order.push('DIF', 'DEA', 'MACD');
    else if (ctx.sub1 === 'KDJ') order.push('K', 'D', 'J');
    else if (ctx.sub1 === 'RSI') order.push('RSI6', 'RSI12', 'RSI24');
    else if (ctx.sub1 === 'BOLL') order.push('UPPER', 'MID', 'LOWER');
    else if (ctx.sub1 === 'CCI') order.push('CCI');

    const seriesMap = new Map(ps.map((p) => [p.seriesName, p]));
    order.forEach((name) => {
      const p = seriesMap.get(name);
      if (!p) return;
      let v = p.data as number | { value: number } | null | undefined;
      if (v && typeof v === 'object') v = v.value;
      if (v == null || typeof v !== 'number') return;
      const shown = name === 'DIF' || name === 'DEA' || name === 'MACD' ? v.toFixed(3) : v.toFixed(2);
      h += `<div>${p.marker}${name} <b>${shown}</b></div>`;
    });
    return h;
  }

  /* ---- 快捷键：F8 循环周期 · Ctrl+Q/B 复权 ---- */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F8') {
        e.preventDefault();
        const order = PERIODS.map((p) => p.key);
        const i = order.indexOf(periodRef.current);
        setPeriod(order[(i + 1) % order.length]);
      } else if (e.ctrlKey && (e.key === 'q' || e.key === 'Q')) {
        e.preventDefault();
        onAdjustChange('qfq');
      } else if (e.ctrlKey && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault();
        onAdjustChange('hfq');
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onAdjustChange]);

  if (bars.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-hair bg-surface text-sm text-ink-muted">
        暂无 K 线数据
      </div>
    );
  }

  return (
    <div className={`w-full overflow-hidden rounded-lg border border-hair bg-surface ${
      fill ? 'flex h-full flex-col' : ''}`}>
      {/* 周期栏 */}
      <div className={`flex flex-wrap items-center gap-0.5 border-b border-hair px-3.5 py-1.5 ${
        fill ? 'shrink-0' : ''}`}>
        {PERIODS.map((p) => (
          <button key={p.key} onClick={() => setPeriod(p.key)}
            className={`rounded-sm border px-2.5 py-0.5 text-xs transition-colors ${
              period === p.key
                ? 'border-brand-300 bg-brand-50 text-brand-600'
                : 'border-transparent text-ink-secondary hover:bg-surface-alt hover:text-brand-600'}`}>
            {p.label}
          </button>
        ))}
      </div>

      {/* 工具栏 */}
      <div className={`flex flex-wrap items-center gap-3.5 border-b border-hair px-3.5 py-1.5 text-xs text-ink-muted ${
        fill ? 'shrink-0' : ''}`}>
        <label className="flex items-center gap-1">
          复权
          <select value={adjust} onChange={(e) => onAdjustChange(e.target.value as AdjustMode)} className={selectCls}>
            <option value="qfq">前复权</option>
            <option value="none">不复权</option>
            <option value="hfq">后复权</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          主图
          <select value={mainInd} onChange={(e) => setMainInd(e.target.value as MainInd)} className={selectCls}>
            <option value="MA">MA 均线</option>
            <option value="BOLL">BOLL 布林带</option>
            <option value="EXPMA">EXPMA 指数均线</option>
            <option value="SAR">SAR 抛物转向</option>
            <option value="NONE">不显示</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          副图1
          <select value={sub1} onChange={(e) => setSub1(e.target.value as SubInd)} className={selectCls}>
            <option value="MACD">MACD</option>
            <option value="KDJ">KDJ</option>
            <option value="RSI">RSI</option>
            <option value="BOLL">BOLL</option>
            <option value="CCI">CCI</option>
            <option value="NONE">关闭</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          副图2
          <select value={sub2} onChange={(e) => setSub2(e.target.value as SubInd)} className={selectCls}>
            <option value="NONE">关闭</option>
            <option value="KDJ">KDJ</option>
            <option value="MACD">MACD</option>
            <option value="RSI">RSI</option>
            <option value="BOLL">BOLL</option>
            <option value="CCI">CCI</option>
          </select>
        </label>
      </div>

      {/* 图表 + 指标数值标签 */}
      {/* fill 模式：图窗占满剩余高度（min-h-0 让 flex 子项可收缩），宿主 absolute inset-0
          脱离文档流，因此其高度变化不会反过来影响父容器 —— 不会出现尺寸抖动。 */}
      <div className={`relative ${fill ? 'min-h-0 flex-1' : ''}`}>
        <div ref={hostRef}
          style={fill ? undefined : { width: '100%', height }}
          className={fill ? 'absolute inset-0' : undefined} />
        <div ref={labelMainRef} className="ind-label" />
        <div ref={labelVolRef} className="ind-label" />
        <div ref={labelSub1Ref} className="ind-label" />
        <div ref={labelSub2Ref} className="ind-label" />
      </div>

      <div className={`border-t border-hair px-3.5 py-1.5 text-[11px] text-ink-muted ${
        fill ? 'shrink-0' : ''}`}>
        快捷键 <kbd className="rounded-sm border border-hair2 bg-surface-alt px-1">F8</kbd> 循环切换周期 ·{' '}
        <kbd className="rounded-sm border border-hair2 bg-surface-alt px-1">Ctrl+Q</kbd> 前复权 ·{' '}
        <kbd className="rounded-sm border border-hair2 bg-surface-alt px-1">Ctrl+B</kbd> 后复权 · 滚轮缩放 · 拖动平移
      </div>
    </div>
  );
}
