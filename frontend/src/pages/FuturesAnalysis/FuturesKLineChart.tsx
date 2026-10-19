/**
 * 机构级期货专业 K 线图组件 (Futures K-Line Chart)。
 *
 * 对标国内主流量化与行情软件（同花顺 / 文华财经 / 掘金 / QuantConnect / TradingView）：
 * 1. 经典中国期货规范：阳线镂空、阴线实心，暗黑/明亮主题自动适配；
 * 2. 主图：日蜡烛图 + 均线系统 MA(5,10,20,60) / BOLL(20,2) / EXPMA(12,50) / SAR(0.02,0.2)；
 * 3. 双 Y 轴联动：主图左轴自适应价格刻度、右轴可视首根基准相对涨跌幅 (%)；
 * 4. 副图 1：主力成交量 (VOL) 柱状图 + 5/10日成交量均线 (VMA5/VMA10)；
 * 5. 副图 2 (期货微观核心)：未平仓持仓量 (OI) 走势 + 日增减仓 (OI Delta) 正红负绿柱图；
 *    亦支持一键切换 MACD(12,26,9) / KDJ(9,3,3) / RSI(6,12,24) / ATR(14) 经典量化指标；
 * 6. 交互：鼠标十字光标移动实时刷新顶部浮动指标值、底部 DataZoom 滑块 + 鼠标滚轮/拖拽平移；
 * 7. 展期后复权 (Continuous) 切换与快捷历史周期切换 (60/120/250/500/全量)。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { fmtAxisNum, fmtNum, fmtPct, fmtVol, fmtVolAxis } from '@/utils/format';
import type { FuturesKlineWithIndicator } from '@/types/futures';

/* ==================== 指标与工具纯函数 ==================== */
const r2 = (v: number) => Math.round(v * 100) / 100;
const disp = (iso: string) => iso.replaceAll('-', '/');

const C_W = CATEGORY_COLORS.C1; // MA5 / UPPER / K / DIF
const C_Y = CATEGORY_COLORS.C2; // MA10 / MID / D / DEA
const C_P = CATEGORY_COLORS.C3; // MA20 / LOWER / J
const C_G = CATEGORY_COLORS.C4; // MA60 / ATR

const LINE_COLORS: Record<string, string> = {
  MA5: C_W, MA10: C_Y, MA20: C_P, MA60: C_G,
  UPPER: C_W, MID: C_Y, LOWER: C_P,
  EXPMA12: C_W, EXPMA50: C_Y, SAR: C_W,
  K: C_W, D: C_Y, J: C_P,
  RSI6: C_W, RSI12: C_Y, RSI24: C_P,
  DIF: C_W, DEA: C_Y, ATR: C_W,
};

function priceAxisDecimals(span: number): number {
  if (!Number.isFinite(span) || span <= 0) return 2;
  if (span >= 0.5) return 2;
  if (span >= 0.05) return 3;
  return 4;
}

function formatPriceAxis(v: number, span: number): string {
  return fmtAxisNum(v, priceAxisDecimals(span));
}

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

function sar(bars: FuturesKlineWithIndicator[], step = 0.02, maxStep = 0.2): number[] {
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

function kdjCalc(bars: FuturesKlineWithIndicator[], n = 9, m1 = 3, m2 = 3) {
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

function rsiCalc(bars: FuturesKlineWithIndicator[], p1 = 6, p2 = 12, p3 = 24) {
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

function atrCalc(bars: FuturesKlineWithIndicator[], n = 14): Array<number | null> {
  const trs: number[] = [];
  bars.forEach((b, i) => {
    if (i === 0) { trs.push(b.high - b.low); return; }
    const prevClose = bars[i - 1].close;
    trs.push(Math.max(b.high - b.low, Math.abs(b.high - prevClose), Math.abs(b.low - prevClose)));
  });
  return wilderMa(trs, n);
}

/* ==================== 选项与常数 ==================== */
type MainInd = 'MA' | 'BOLL' | 'EXPMA' | 'SAR' | 'NONE';
type SubInd = 'OI' | 'MACD' | 'KDJ' | 'RSI' | 'ATR' | 'NONE';

const MAIN_OPTIONS: Array<{ key: MainInd; label: string }> = [
  { key: 'MA', label: 'MA(5,10,20,60)' },
  { key: 'BOLL', label: 'BOLL(20,2)' },
  { key: 'EXPMA', label: 'EXPMA(12,50)' },
  { key: 'SAR', label: 'SAR(0.02,0.2)' },
  { key: 'NONE', label: '主图无叠加' },
];

const SUB_OPTIONS: Array<{ key: SubInd; label: string }> = [
  { key: 'OI', label: '持仓量(OI) & 日增仓' },
  { key: 'MACD', label: 'MACD(12,26,9)' },
  { key: 'KDJ', label: 'KDJ(9,3,3)' },
  { key: 'RSI', label: 'RSI(6,12,24)' },
  { key: 'ATR', label: 'ATR(14) 真实波幅' },
  { key: 'NONE', label: '关闭副图2' },
];

interface Props {
  bars: FuturesKlineWithIndicator[];
  symbol?: string;
  name?: string;
  continuous?: boolean;
  activeRange?: number;
  onToggleContinuous?: (c: boolean) => void;
  onRangeChange?: (days: number) => void;
  height?: number;
}

export function FuturesKLineChart({
  bars = [],
  symbol = 'IF',
  name = '沪深300股指',
  continuous = true,
  activeRange: activeRangeProp,
  onToggleContinuous,
  onRangeChange,
  height = 560,
}: Props) {
  const theme = useTheme();
  const [mainInd, setMainInd] = useState<MainInd>('MA');
  const [subInd, setSubInd] = useState<SubInd>('OI');
  const [activeRange, setActiveRange] = useState<number>(activeRangeProp ?? 250);

  useEffect(() => {
    if (activeRangeProp !== undefined) {
      setActiveRange(activeRangeProp);
    }
  }, [activeRangeProp]);

  // 悬停交互状态：当前聚焦的 K 线索引（默认最后一条）
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const pal = useMemo(() => {
    const p = chartPalette();
    return {
      UP: p.UP,
      DOWN: p.DOWN,
      BG: p.CARD,
      AXIS: p.HAIR,
      SPLIT: p.HAIR,
      TEXT_C: p.INKM,
      GRID_BG: p.CARD,
      TIP_BG: p.TIP_BG,
      TIP_BORDER: p.TIP_BORDER,
      TIP_TEXT: p.TIP_TEXT,
      ZOOM_BG: p.ALT,
      ZOOM_HANDLE: p.HAIR2,
      POINTER_LABEL_BG: p.INK2,
      POINTER_LABEL_TEXT: p.ON_SOLID,
      INFO: p.INFO,
      WARN: p.WARN,
      BRAND: p.BRAND,
    };
  }, [theme]);

  // 根据当前选择的范围筛选 K 线
  const data = useMemo(() => {
    if (!bars.length) return [];
    if (activeRange >= bars.length) return bars;
    return bars.slice(-activeRange);
  }, [bars, activeRange]);

  // 当前聚焦或最新一根 K 线数据
  const currentBar = useMemo(() => {
    if (!data.length) return null;
    const idx = hoverIdx !== null && hoverIdx >= 0 && hoverIdx < data.length ? hoverIdx : data.length - 1;
    const cur = data[idx];
    const prev = idx > 0 ? data[idx - 1] : cur;
    const chg = cur.close - prev.close;
    const chgPct = prev.close > 0 ? (chg / prev.close) * 100 : 0;
    const oiDelta = cur.oi_delta ?? (idx > 0 ? cur.open_interest - prev.open_interest : 0);
    const amp = prev.close > 0 ? ((cur.high - cur.low) / prev.close) * 100 : 0;
    return {
      cur,
      idx,
      chg,
      chgPct,
      oiDelta,
      amp,
    };
  }, [data, hoverIdx]);

  // 主图指标序列计算
  const mainSeriesData = useMemo(() => {
    const closes = data.map((b) => b.close);
    const ind: Record<string, Array<number | null>> = {};
    const series: echarts.SeriesOption[] = [];

    const line = (lineName: string, arr: Array<number | null>) => ({
      name: lineName,
      type: 'line' as const,
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: arr,
      smooth: true,
      showSymbol: false,
      lineStyle: { color: LINE_COLORS[lineName] || pal.INFO, width: 1.2 },
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
        name: 'SAR',
        type: 'scatter',
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbolSize: 3.5,
        data: s.map((v, i) => ({
          value: v,
          itemStyle: { color: data[i].close >= v ? pal.UP : pal.DOWN },
        })),
      });
    }

    return { series, ind };
  }, [data, mainInd, pal]);

  // 副图指标序列计算
  const subSeriesData = useMemo(() => {
    const series: echarts.SeriesOption[] = [];
    const closes = data.map((b) => b.close);

    const line = (lineName: string, arr: Array<number | null>, yIdx: number) => ({
      name: lineName,
      type: 'line' as const,
      xAxisIndex: 2,
      yAxisIndex: yIdx,
      data: arr,
      smooth: true,
      showSymbol: false,
      lineStyle: { color: LINE_COLORS[lineName] || pal.INFO, width: 1.2 },
    });

    if (subInd === 'OI') {
      // 期货专属：持仓量 (OI) 折线 + 日增减仓 (OI Delta) 柱状图
      const ois = data.map((b) => b.open_interest);
      const deltas = data.map((b, i) => {
        const delta = b.oi_delta ?? (i > 0 ? b.open_interest - data[i - 1].open_interest : 0);
        return {
          value: delta,
          itemStyle: {
            color: delta >= 0 ? pal.UP : pal.DOWN,
          },
        };
      });

      series.push(
        {
          name: '持仓量(OI)',
          type: 'line',
          xAxisIndex: 2,
          yAxisIndex: 3, // 左轴: OI 总量
          data: ois,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: pal.INFO, width: 1.6 },
        },
        {
          name: '日增仓(Delta)',
          type: 'bar',
          xAxisIndex: 2,
          yAxisIndex: 4, // 右轴: 增减仓
          data: deltas,
          barWidth: '45%',
        }
      );
    } else if (subInd === 'MACD') {
      const m = macdCalc(closes);
      series.push(
        {
          name: 'MACD',
          type: 'bar',
          xAxisIndex: 2,
          yAxisIndex: 3,
          barWidth: '50%',
          data: m.bar.map((v) => ({
            value: v,
            itemStyle: { color: v >= 0 ? pal.UP : pal.DOWN },
          })),
        },
        line('DIF', m.dif, 3),
        line('DEA', m.dea, 3)
      );
    } else if (subInd === 'KDJ') {
      const k = kdjCalc(data);
      series.push(line('K', k.k, 3), line('D', k.d, 3), line('J', k.j, 3));
    } else if (subInd === 'RSI') {
      const r = rsiCalc(data);
      series.push(line('RSI6', r.r1, 3), line('RSI12', r.r2, 3), line('RSI24', r.r3, 3));
    } else if (subInd === 'ATR') {
      const atrs = atrCalc(data);
      series.push(line('ATR', atrs, 3));
    }

    return series;
  }, [data, subInd, pal]);

  // 三格布局计算：主图 52% + 成交量 20% + 副图(OI/MACD) 20%
  const layout = useMemo(() => {
    const hasSub = subInd !== 'NONE';
    const TOP = 24;
    const BOTTOM = 32;
    const avail = height - TOP - BOTTOM;

    if (!hasSub) {
      const volH = 90;
      const mainH = avail - volH - 16;
      return {
        grids: [
          { left: 56, right: 60, top: TOP, height: mainH },
          { left: 56, right: 60, top: TOP + mainH + 16, height: volH },
        ],
        hasSub: false,
      };
    }

    const volH = 75;
    const subH = 75;
    const mainH = avail - volH - subH - 32;

    return {
      grids: [
        { left: 56, right: 60, top: TOP, height: mainH },
        { left: 56, right: 60, top: TOP + mainH + 14, height: volH },
        { left: 56, right: 60, top: TOP + mainH + 14 + volH + 14, height: subH },
      ],
      hasSub: true,
    };
  }, [height, subInd]);

  // 全量配置对象构建（供 useChart 响应式挂载）
  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!data.length) return null;

    const dates = data.map((b) => disp(b.date));
    const isThreeGrid = layout.hasSub;
    const lastGridIdx = isThreeGrid ? 2 : 1;

    // 价格跨度
    const highs = data.map((d) => d.high);
    const lows = data.map((d) => d.low);
    const maxP = Math.max(...highs);
    const minP = Math.min(...lows);
    const span = maxP > minP ? maxP - minP : 0;
    const basePrice = data[0]?.close || 1;

    // x 轴配置
    const xAxis = layout.grids.map((_, g) => ({
      type: 'category' as const,
      gridIndex: g,
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: pal.AXIS } },
      axisTick: { show: false },
      axisLabel: { show: g === lastGridIdx, color: pal.TEXT_C, fontSize: 10 },
      splitLine: { show: false },
      axisPointer: { label: { backgroundColor: pal.POINTER_LABEL_BG, color: pal.POINTER_LABEL_TEXT } },
    }));

    // y 轴配置
    const yAxis: echarts.YAXisComponentOption[] = [
      {
        // 0: 主图左轴 (价格)
        scale: true,
        gridIndex: 0,
        position: 'left',
        splitNumber: 5,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: pal.SPLIT, type: 'dashed' } },
        axisLabel: {
          color: pal.TEXT_C,
          fontSize: 10,
          formatter: (v: number) => formatPriceAxis(v, span),
        },
      },
      {
        // 1: 主图右轴 (区间涨跌幅)
        scale: true,
        gridIndex: 0,
        position: 'right',
        splitNumber: 5,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          fontSize: 10,
          formatter: (v: number) => {
            const pct = ((v - basePrice) / basePrice) * 100;
            const t = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
            return `{${pct >= 0 ? 'u' : 'd'}|${t}}`;
          },
          rich: {
            u: { color: pal.UP, fontSize: 10 },
            d: { color: pal.DOWN, fontSize: 10 },
          },
        },
      },
      {
        // 2: 成交量 Y 轴
        scale: true,
        gridIndex: 1,
        position: 'left',
        splitNumber: 2,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          color: pal.TEXT_C,
          fontSize: 9,
          formatter: (v: number) => fmtVolAxis(v),
        },
      },
    ];

    if (isThreeGrid) {
      if (subInd === 'OI') {
        yAxis.push(
          {
            // 3: 副图左轴 (OI 总持仓)
            scale: true,
            gridIndex: 2,
            position: 'left',
            splitNumber: 2,
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: {
              color: pal.INFO,
              fontSize: 9,
              formatter: (v: number) => (v >= 10000 ? `${(v / 10000).toFixed(0)}万` : String(v)),
            },
          },
          {
            // 4: 副图右轴 (日增减仓 Delta)
            scale: true,
            gridIndex: 2,
            position: 'right',
            splitNumber: 2,
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: {
              color: pal.TEXT_C,
              fontSize: 9,
              formatter: (v: number) => (Math.abs(v) >= 10000 ? `${(v / 10000).toFixed(1)}万` : String(v)),
            },
          }
        );
      } else {
        yAxis.push({
          // 3: 通用副图指标 Y 轴
          scale: true,
          gridIndex: 2,
          position: 'left',
          splitNumber: 2,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: {
            color: pal.TEXT_C,
            fontSize: 9,
          },
        });
      }
    }

    // 蜡烛图与成交量 Series
    const candleSeries: echarts.SeriesOption = {
      name: 'K线',
      type: 'candlestick',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: data.map((b) => [b.open, b.close, b.low, b.high]),
      itemStyle: {
        color: pal.BG, // 阳线空心镂空
        color0: pal.DOWN, // 阴线实心
        borderColor: pal.UP,
        borderColor0: pal.DOWN,
        borderWidth: 1.2,
      },
    };

    // 成交量柱状图 (比照前收盘红绿)
    const vols = data.map((b) => b.volume);
    const volColors = data.map((b, i) =>
      (i === 0 ? b.close >= b.open : b.close >= data[i - 1].close) ? pal.UP : pal.DOWN
    );
    const volSeries: echarts.SeriesOption[] = [
      {
        name: 'VOL',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 2,
        barWidth: '60%',
        data: vols.map((v, i) => ({ value: v, itemStyle: { color: volColors[i] } })),
      },
      {
        name: 'VMA5',
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 2,
        data: ma(vols, 5),
        smooth: true,
        showSymbol: false,
        lineStyle: { color: C_W, width: 1 },
      },
      {
        name: 'VMA10',
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 2,
        data: ma(vols, 10),
        smooth: true,
        showSymbol: false,
        lineStyle: { color: C_Y, width: 1 },
      },
    ];

    const initialStart = data.length > 80 ? 100 - (80 / data.length) * 100 : 0;

    return {
      backgroundColor: pal.GRID_BG,
      animation: false,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: pal.TIP_BG,
        borderColor: pal.TIP_BORDER,
        borderWidth: 1,
        textStyle: { color: pal.TIP_TEXT, fontSize: 11 },
        formatter: (params: any) => {
          if (!Array.isArray(params) || !params.length) return '';
          const idx = params[0].dataIndex;
          if (idx < 0 || idx >= data.length) return '';
          const b = data[idx];
          const prev = idx > 0 ? data[idx - 1] : b;
          const chg = b.close - prev.close;
          const chgPct = prev.close > 0 ? (chg / prev.close) * 100 : 0;
          const delta = b.oi_delta ?? (idx > 0 ? b.open_interest - prev.open_interest : 0);

          return `<div style="font-size:11px; font-weight:600; margin-bottom:4px; border-bottom:1px solid ${pal.SPLIT}; padding-bottom:3px;">
            ${b.date} · ${name} (${symbol})
          </div>
          <div style="display:grid; grid-template-columns:auto auto; gap:4px 12px; font-size:11px; font-family:monospace;">
            <span>开盘: <strong>${fmtNum(b.open, 2)}</strong></span>
            <span>最高: <strong>${fmtNum(b.high, 2)}</strong></span>
            <span>最低: <strong>${fmtNum(b.low, 2)}</strong></span>
            <span>收盘: <strong style="color:${chg >= 0 ? pal.UP : pal.DOWN}">${fmtNum(b.close, 2)}</strong></span>
            <span>涨跌: <strong style="color:${chg >= 0 ? pal.UP : pal.DOWN}">${chg >= 0 ? '+' : ''}${fmtNum(chg, 2)} (${fmtPct(chgPct)})</strong></span>
            <span>成交: <strong>${fmtVol(b.volume)}</strong></span>
            <span>持仓(OI): <strong>${fmtNum(b.open_interest, 0)}</strong></span>
            <span>增仓: <strong style="color:${delta >= 0 ? pal.UP : pal.DOWN}">${delta >= 0 ? '+' : ''}${fmtNum(delta, 0)}</strong></span>
          </div>`;
        },
      },
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: pal.POINTER_LABEL_BG, color: pal.POINTER_LABEL_TEXT },
      },
      grid: layout.grids,
      xAxis,
      yAxis,
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: layout.grids.map((_, i) => i),
          start: initialStart,
          end: 100,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
        },
        {
          type: 'slider',
          xAxisIndex: layout.grids.map((_, i) => i),
          bottom: 4,
          height: 16,
          start: initialStart,
          end: 100,
          borderColor: 'transparent',
          backgroundColor: pal.ZOOM_BG,
          fillerColor: 'rgba(22,119,255,0.1)',
          handleStyle: { color: pal.ZOOM_HANDLE },
        },
      ],
      series: [
        candleSeries,
        ...mainSeriesData.series,
        ...volSeries,
        ...subSeriesData,
      ],
    };
  }, [data, layout, mainSeriesData, subSeriesData, pal, subInd, name, symbol]);

  const chartRef = useChart(chartOption);

  // 计算当前 K 线对应的持仓量研判结构
  const currentStructure = useMemo(() => {
    if (!currentBar) return '持仓平稳';
    const { chg, oiDelta } = currentBar;
    if (chg > 0 && oiDelta > 0) return '增仓上涨 (多头主动上攻，上行动能充沛)';
    if (chg > 0 && oiDelta < 0) return '减仓上涨 (空头止损离场主导反弹，警惕动能衰竭)';
    if (chg < 0 && oiDelta > 0) return '增仓下跌 (空头主动打压放量开仓，下行抛压沉重)';
    if (chg < 0 && oiDelta < 0) return '减仓下跌 (多头平仓止损主导回调，杀跌意愿趋弱)';
    return '持仓震荡 (多空力量均衡，等待方向突破)';
  }, [currentBar]);

  const selectCls =
    'rounded border border-border bg-surface-raised px-2 py-0.5 text-xs text-text focus:border-primary focus:outline-none cursor-pointer';

  return (
    <div className="rounded-lg border border-border bg-card shadow-sm space-y-2 p-3">
      {/* 顶部工具栏：周期、复权、主副图指标切换 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2.5">
        <div className="flex flex-wrap items-center gap-2">
          {/* 合约标识 */}
          <div className="flex items-center gap-1.5">
            <span className="font-bold text-sm text-text font-mono">{symbol}</span>
            <span className="text-xs text-text-muted">{name}</span>
          </div>

          {/* 快捷查看历史跨度 */}
          <div className="flex items-center rounded-md bg-surface-raised p-0.5 text-2xs">
            {[
              { days: 60, label: '60日' },
              { days: 120, label: '120日(半年)' },
              { days: 250, label: '250日(1年)' },
              { days: 500, label: '500日(2年)' },
              { days: 2500, label: '全历史' },
            ].map((rng) => (
              <button
                key={rng.days}
                type="button"
                onClick={() => {
                  setActiveRange(rng.days);
                  onRangeChange?.(rng.days);
                }}
                className={`rounded px-2 py-1 font-medium transition-colors ${
                  activeRange === rng.days
                    ? 'bg-card text-primary shadow-sm font-semibold'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                {rng.label}
              </button>
            ))}
          </div>

          {/* 展期后复权切换 */}
          {onToggleContinuous && (
            <label className="flex items-center gap-1.5 text-2xs text-text cursor-pointer select-none ml-1">
              <input
                type="checkbox"
                checked={continuous}
                onChange={(e) => onToggleContinuous(e.target.checked)}
                className="rounded border-border text-primary focus:ring-primary"
              />
              <span title="采用换月价差平滑算法，消除主力换月跳空假突破">
                后复权连续合约
              </span>
            </label>
          )}
        </div>

        {/* 指标选择器 */}
        <div className="flex flex-wrap items-center gap-2 text-2xs">
          <div className="flex items-center gap-1">
            <span className="text-text-muted">主图:</span>
            <select
              value={mainInd}
              onChange={(e) => setMainInd(e.target.value as MainInd)}
              className={selectCls}
            >
              {MAIN_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-1">
            <span className="text-text-muted">副图2:</span>
            <select
              value={subInd}
              onChange={(e) => setSubInd(e.target.value as SubInd)}
              className={selectCls}
            >
              {SUB_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* 实时浮动指标数值与 OHLCV+OI 详情栏 */}
      {currentBar && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded bg-surface-raised/70 px-3 py-1.5 text-2xs font-mono">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-text-muted font-sans">{currentBar.cur.date}</span>
            <span>
              开:<strong className="text-text ml-0.5">{fmtNum(currentBar.cur.open, 2)}</strong>
            </span>
            <span>
              高:<strong className="text-text ml-0.5">{fmtNum(currentBar.cur.high, 2)}</strong>
            </span>
            <span>
              低:<strong className="text-text ml-0.5">{fmtNum(currentBar.cur.low, 2)}</strong>
            </span>
            <span>
              收:
              <strong className={`ml-0.5 ${currentBar.chg >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                {fmtNum(currentBar.cur.close, 2)}
              </strong>
            </span>
            <span>
              涨跌:
              <strong className={`ml-0.5 ${currentBar.chg >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                {`${currentBar.chg >= 0 ? '+' : ''}${fmtNum(currentBar.chg, 2)} (${fmtPct(currentBar.chgPct)})`}
              </strong>
            </span>
            <span>
              振幅:<strong className="text-text ml-0.5">{fmtPct(currentBar.amp)}</strong>
            </span>
            <span>
              成交量:<strong className="text-text ml-0.5">{fmtVol(currentBar.cur.volume)}</strong>
            </span>
            <span>
              持仓量(OI):<strong className="text-primary ml-0.5">{fmtNum(currentBar.cur.open_interest, 0)}</strong>
            </span>
            <span>
              增仓:
              <strong className={`ml-0.5 ${currentBar.oiDelta >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                {`${currentBar.oiDelta >= 0 ? '+' : ''}${fmtNum(currentBar.oiDelta, 0)}`}
              </strong>
            </span>
          </div>

          {/* 量价持仓结构即时研判 */}
          <div className="flex items-center gap-1.5 font-sans">
            <span className="text-3xs text-text-muted">持仓研判:</span>
            <span
              className={`rounded px-1.5 py-0.5 text-3xs font-medium ${
                currentBar.chg >= 0 && currentBar.oiDelta >= 0
                  ? 'bg-rose-500/10 text-rose-500 border border-rose-500/20'
                  : currentBar.chg < 0 && currentBar.oiDelta >= 0
                  ? 'bg-rose-500/10 text-rose-500 border border-rose-500/20'
                  : 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
              }`}
            >
              {currentStructure}
            </span>
          </div>
        </div>
      )}

      {/* 主 K 线图窗（三格联动） */}
      <div className="relative">
        <div ref={chartRef} onMouseLeave={() => setHoverIdx(null)} style={{ width: '100%', height: `${height}px` }} />
      </div>
    </div>
  );
}
