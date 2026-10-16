/**
 * 我的收藏（/watchlist）：多资产跨市场自选监控 + 专业量化视图矩阵 + 组合风险透视。
 *
 * 核心功能矩阵：
 * 1. 跨资产统一纳池：覆盖 A股、ETF、内盘期货主力 (IF/IC/AU/RB等)、港股通 (.HK)；
 * 2. 多重视图切换矩阵：
 *    - 「交易行情」：最新价、涨跌幅、成交额、K线多空形态走势图、异动预警、品类属性；
 *    - 「量化因子」：综合多头Alpha评分 (1~99)、20日动量%、20日年化波动率、14日RSI、30日最大回撤；
 *    - 「估值基本面」：市盈率 PE(TTM)、市净率 PB、股息率%、净资产收益率 ROE%、换手率%；
 *    - 「风险与衍生」：年化波动率、最大回撤、K线形态、预警信号、资产分类；
 * 3. 组合风险即时透视：Barra 7维风格雷达画像、多权重模拟器 (等权/逆波/市值加权) 与极端压力测试；
 * 4. 量化工具箱：一键导入组合回测与凸优化求解器、收益率相关性热力图、估值快照、CSV批量导出。
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { watchlistApi } from '@/api/watchlist';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { Modal, PageHeader, PanelEmpty } from '@/components/ui';
import { useWatchlistQuotes } from '@/hooks/useWatchlistQuotes';
import { useWatchlistStore, DEFAULT_GROUP } from '@/stores/useWatchlistStore';
import type { CorrResult, WatchItem } from '@/types/watchlist';
import { fmtPct, pctClass } from '@/utils/format';
import { WatchlistRiskRadar } from './WatchlistRiskRadar';

/* ==================== 常量 ==================== */
const SEED_FLAG = 'aqp-watchlist-seeded';
const PORTFOLIO_IMPORT_KEY = 'AQP_PORTFOLIO_IMPORT';

/** 首次访问预置示例组合（覆盖股票 + 主流 ETF + 期货主力） */
const SEED: Record<string, string[]> = {
  [DEFAULT_GROUP]: ['600519.SH', '000001.SZ', '300750.SZ', '600036.SH', '510300', '159915', '513050', 'IF', 'AU'],
  核心观察: ['600519.SH', '300750.SZ', '510300', 'IF'],
  高股息与大宗: ['000001.SZ', '600036.SH', 'AU'],
};

const KLINE_BADGE: Record<string, string> = {
  均线多头: 'border-emerald-200 bg-emerald-50 text-emerald-600',
  均线空头: 'border-red-200 bg-red-50 text-red-500',
  放量突破: 'border-red-200 bg-red-50 text-red-600 font-medium',
  触及支撑: 'border-blue-200 bg-blue-50 text-blue-600',
  窄幅震荡: 'border-amber-200 bg-amber-50 text-amber-600',
  平稳: 'border-slate-200 bg-slate-50 text-ink-muted',
};

type ViewMode = 'trade' | 'factor' | 'fundamental' | 'risk';

const VIEW_MODES: Array<{ key: ViewMode; label: string; icon: string }> = [
  { key: 'trade', label: '交易行情', icon: '📈' },
  { key: 'factor', label: '量化因子', icon: '⚡' },
  { key: 'fundamental', label: '估值基本面', icon: '📊' },
  { key: 'risk', label: '风险与衍生', icon: '🛡️' },
];

type SortableField =
  | 'code'
  | 'close'
  | 'pct'
  | 'amount_yi'
  | 'alpha_score'
  | 'momentum_20'
  | 'volatility_20'
  | 'pe'
  | 'pb'
  | 'dividend_yield'
  | 'roe'
  | 'rsi_14'
  | 'mdd_30'
  | 'turnover_rate';

/* ==================== 渲染辅助组件 ==================== */
function isEtf(sym: string): boolean {
  return /^\d{6}$/.test(sym) && ['51', '56', '58', '15'].includes(sym.slice(0, 2));
}

function AssetTypeBadge({ type }: { type: string }) {
  switch (type) {
    case 'etf':
      return <span className="ml-1 rounded bg-blue-50 px-1 py-0.2 text-2xs font-medium text-blue-600 border border-blue-200">ETF</span>;
    case 'futures':
      return <span className="ml-1 rounded bg-amber-50 px-1 py-0.2 text-2xs font-medium text-amber-700 border border-amber-200">期货主力</span>;
    case 'hk':
      return <span className="ml-1 rounded bg-indigo-50 px-1 py-0.2 text-2xs font-medium text-indigo-600 border border-indigo-200">港股通</span>;
    default:
      return null;
  }
}

function AlphaScoreBadge({ score }: { score?: number | null }) {
  if (score == null) return <span className="text-ink-muted text-xs">—</span>;
  let bgCls = 'bg-slate-100 text-slate-700 border-slate-200';
  if (score >= 80) bgCls = 'bg-emerald-50 text-emerald-700 border-emerald-300 font-bold';
  else if (score >= 60) bgCls = 'bg-blue-50 text-blue-700 border-blue-200 font-semibold';
  else if (score >= 40) bgCls = 'bg-amber-50 text-amber-700 border-amber-200';
  else bgCls = 'bg-red-50 text-red-600 border-red-200';

  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${bgCls}`}>
      <span className="text-3xs opacity-70">Alpha</span>
      <span className="font-mono font-semibold">{score}</span>
    </span>
  );
}

/**
 * 迷你 K 线（SVG 折线，红涨绿跌跟随当日涨跌）。
 */
function Sparkline({ closes, up }: { closes: Array<number | null>; up?: boolean }) {
  const p = chartPalette();
  const pts = closes.filter((c): c is number => c != null);
  if (pts.length < 3) return <div className="h-7 w-16" />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const w = 64,
    h = 26;
  const path = pts
    .map((c, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = h - 2 - ((c - min) / range) * (h - 4);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const color = up == null ? p.INKM : up ? p.UP : p.DOWN;
  return (
    <svg width={w} height={h} className="shrink-0">
      <path d={path} fill="none" stroke={color} strokeWidth={1.4} strokeLinejoin="round" />
    </svg>
  );
}

function KpiIcon({ kind }: { kind: 'bars' | 'trend' | 'flow' | 'bell' }) {
  const p = chartPalette();
  const cls = 'h-8 w-8';
  if (kind === 'bars')
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke={p.BRAND} strokeWidth={1.6} strokeLinecap="round" className={cls}>
        <path d="M4 20V10M9 20V4M14 20v-7M19 20V8" />
        <path d="M3 21h18" />
      </svg>
    );
  if (kind === 'trend')
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke={p.UP} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={cls}>
        <path d="m3 17 6-6 4 4 8-9" />
        <path d="M15 6h6v6" />
      </svg>
    );
  if (kind === 'flow')
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke={p.WARN} strokeWidth={1.6} strokeLinecap="round" className={cls}>
        <path d="M4 19V9m5 10V5m5 14v-7m5 7V8" />
        <path d="M3 21h18" opacity={0.4} />
      </svg>
    );
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke={p.INFO} strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={cls}>
      <path d="M18 9a6 6 0 1 0-12 0c0 6-2.5 7-2.5 7h17S18 15 18 9" />
      <path d="M10 20a2.2 2.2 0 0 0 4 0" />
    </svg>
  );
}

/* ==================== 相关性热力图弹窗 ==================== */
function CorrModal({
  open,
  onClose,
  data,
  loading,
}: {
  open: boolean;
  onClose: () => void;
  data: CorrResult | null;
  loading: boolean;
}) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!open || !data || data.symbols.length < 2) return null;
    const p = chartPalette();
    return {
      tooltip: {
        position: 'top',
        formatter: (params: unknown) => {
          const pt = params as { data: [number, number, number] };
          return `${data.symbols[pt.data[0]]} × ${data.symbols[pt.data[1]]}<br/>相关系数 <b>${pt.data[2].toFixed(3)}</b>`;
        },
      },
      grid: { left: 90, bottom: 80, top: 12, right: 12 },
      xAxis: { type: 'category', data: data.symbols, axisLabel: { fontSize: 9, rotate: 45 } },
      yAxis: { type: 'category', data: data.symbols, axisLabel: { fontSize: 9 } },
      visualMap: {
        min: -1,
        max: 1,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemHeight: 60,
        inRange: { color: [p.DOWN, p.CARD, p.UP] },
        textStyle: { fontSize: 9 },
      },
      series: [
        {
          type: 'heatmap',
          data: data.matrix.flatMap((row, i) =>
            row.map((v, j) => [i, j, v ?? Number.NaN]) as [number, number, number][]
          ),
          label: {
            show: data.symbols.length <= 10,
            fontSize: 8,
            formatter: (params: unknown) => {
              const pt = params as { data: [number, number, number] };
              return Number.isNaN(pt.data[2]) ? '-' : pt.data[2].toFixed(2);
            },
          },
        },
      ],
    };
  }, [open, data, theme]);

  const chartRef = useChart(option);

  return (
    <Modal
      title="收益相关性矩阵"
      sub={data ? `最近 ${data.days} 个交易日 · 日收益率 Pearson 相关系数` : ''}
      open={open}
      onClose={onClose}
    >
      {loading ? (
        <div className="py-10 text-center text-xs text-ink-muted">计算中…</div>
      ) : data && data.symbols.length >= 2 ? (
        <div ref={chartRef} style={{ height: 360 }} className="w-full" />
      ) : (
        <PanelEmpty text="可选标的不足 2 只（或历史数据不足），无法计算相关性" minH="min-h-[120px]" />
      )}
    </Modal>
  );
}

/* ==================== 主组件 ==================== */
export default function Watchlist() {
  const navigate = useNavigate();
  const store = useWatchlistStore();
  const [lastSync, setLastSync] = useState<string>('—');
  const [tab, setTab] = useState<string>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('trade');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [newGroupOpen, setNewGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [corrOpen, setCorrOpen] = useState(false);
  const [corrData, setCorrData] = useState<CorrResult | null>(null);
  const [corrLoading, setCorrLoading] = useState(false);
  const [valOpen, setValOpen] = useState(false);
  const [sortField, setSortField] = useState<SortableField | null>(null);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [onlyAlert, setOnlyAlert] = useState(false);

  const handleSort = (field: SortableField) => {
    if (sortField !== field) {
      setSortField(field);
      setSortOrder('desc');
    } else if (sortOrder === 'desc') {
      setSortOrder('asc');
    } else {
      setSortField(null);
      setSortOrder('desc');
    }
  };

  const sortIcon = (field: SortableField) => {
    if (sortField !== field) {
      return <span className="ml-0.5 text-ink-muted/30 font-mono text-2xs">⇅</span>;
    }
    return (
      <span className="ml-0.5 font-mono text-2xs text-brand-600">
        {sortOrder === 'asc' ? '▲' : '▼'}
      </span>
    );
  };

  /* 首次访问预置示例组合 */
  useEffect(() => {
    if (localStorage.getItem(SEED_FLAG)) return;
    localStorage.setItem(SEED_FLAG, '1');
    if (!Object.values(store.groups).some((l) => l.length)) {
      Object.entries(SEED).forEach(([g, syms]) => {
        store.createGroup(g);
        syms.forEach((s) => store.addTo(g, s));
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const allSymbols = useMemo(
    () => Array.from(new Set(Object.values(store.groups).flat())),
    [store.groups]
  );

  const symbolGroup = useMemo(() => {
    const m: Record<string, string[]> = {};
    Object.entries(store.groups).forEach(([g, syms]) =>
      syms.forEach((s) => {
        (m[s] ??= []).push(g);
      })
    );
    return m;
  }, [store.groups]);

  /* ---------- 行情加载 + 轮询 ---------- */
  const { data: dash, loading, error } = useWatchlistQuotes(watchlistApi.dashboard, 12);
  const items = dash?.items ?? [];
  const summary = dash?.summary ?? null;

  useEffect(() => {
    if (dash) setLastSync(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
  }, [dash]);

  /* ---------- 派生分类统计 ---------- */
  const customGroups = useMemo(
    () => Object.keys(store.groups).filter((g) => g !== DEFAULT_GROUP),
    [store.groups]
  );

  const alertCount = useMemo(
    () => items.filter((i) => !!i.alert && i.alert.trim() !== '').length,
    [items]
  );

  const counts = {
    all: items.length,
    stock: items.filter((i) => i.type === 'stock').length,
    etf: items.filter((i) => i.type === 'etf').length,
    futures: items.filter((i) => i.type === 'futures').length,
    hk: items.filter((i) => i.type === 'hk').length,
  };

  const shown = useMemo(() => {
    let list = items;
    if (tab === 'stock') list = list.filter((i) => i.type === 'stock');
    else if (tab === 'etf') list = list.filter((i) => i.type === 'etf');
    else if (tab === 'futures') list = list.filter((i) => i.type === 'futures');
    else if (tab === 'hk') list = list.filter((i) => i.type === 'hk');
    else if (tab !== 'all') list = list.filter((i) => (store.groups[tab] ?? []).includes(i.symbol));

    if (onlyAlert) {
      list = list.filter((i) => !!i.alert && i.alert.trim() !== '');
    }

    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (i) => i.symbol.toLowerCase().includes(q) || (i.name ?? '').toLowerCase().includes(q)
      );
    }

    if (sortField) {
      list = [...list].sort((a, b) => {
        const va = a[sortField];
        const vb = b[sortField];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === 'string' && typeof vb === 'string') {
          return sortOrder === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        return sortOrder === 'asc' ? Number(va) - Number(vb) : Number(vb) - Number(va);
      });
    }

    return list;
  }, [items, tab, onlyAlert, query, sortField, sortOrder, store.groups]);

  const shownSyms = shown.map((i) => i.symbol);
  const allChecked = shownSyms.length > 0 && shownSyms.every((s) => selected.has(s));
  const toggleAll = () => setSelected(allChecked ? new Set() : new Set(shownSyms));
  const toggleOne = (s: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  /* ---------- 联动跳转 ---------- */
  const gotoAnalysis = (it: WatchItem) => {
    if (it.type === 'etf') {
      navigate(`/etf/${it.code}`);
    } else if (it.type === 'futures') {
      navigate(`/futures/analysis?symbol=${encodeURIComponent(it.code)}`);
    } else {
      navigate(`/stock/${encodeURIComponent(it.symbol)}`);
    }
  };

  const importToPortfolio = (syms: string[]) => {
    const assets = syms.map((s) => {
      const it = items.find((i) => i.symbol === s);
      return {
        code: it?.code ?? s.replace(/\..*$/, ''),
        name: it?.name ?? s,
        type: it?.type ?? (isEtf(s) ? 'etf' : 'stock'),
        weight: Number((1 / syms.length).toFixed(4)),
      };
    });
    localStorage.setItem(PORTFOLIO_IMPORT_KEY, JSON.stringify(assets));
    navigate('/portfolio');
  };

  const openCorrelation = async () => {
    const syms = [...selected];
    if (syms.length < 2) return;
    setCorrOpen(true);
    setCorrLoading(true);
    try {
      setCorrData(await watchlistApi.correlation(syms, 60));
    } catch {
      setCorrData(null);
    } finally {
      setCorrLoading(false);
    }
  };

  const exportCsv = () => {
    const rows = [
      [
        '代码',
        '名称',
        '品类',
        '最新价',
        '涨跌幅%',
        '成交额(亿)',
        '量化评分',
        '20日动量%',
        '20日波动%',
        '30日回撤%',
        '14日RSI',
        'K线形态',
        '预警信号',
        '所属分组',
        'PE',
        'PB',
        '股息率%',
        'ROE%',
      ],
    ];
    items
      .filter((i) => selected.has(i.symbol))
      .forEach((i) => {
        const typeLabel =
          i.type === 'etf'
            ? 'ETF'
            : i.type === 'futures'
            ? '期货主力'
            : i.type === 'hk'
            ? '港股通'
            : 'A股';
        rows.push([
          i.code,
          i.name ?? '',
          typeLabel,
          i.close?.toFixed(3) ?? '',
          i.pct?.toFixed(2) ?? '',
          i.amount_yi?.toFixed(2) ?? '',
          i.alpha_score != null ? String(i.alpha_score) : '',
          i.momentum_20?.toFixed(2) ?? '',
          i.volatility_20?.toFixed(2) ?? '',
          i.mdd_30 != null ? `-${i.mdd_30.toFixed(2)}` : '',
          i.rsi_14?.toFixed(1) ?? '',
          i.kline_state ?? '',
          i.alert ?? '',
          (symbolGroup[i.symbol] ?? []).join('|'),
          i.pe?.toFixed(2) ?? '',
          i.pb?.toFixed(2) ?? '',
          i.dividend_yield?.toFixed(2) ?? '',
          i.roe?.toFixed(2) ?? '',
        ]);
      });
    const csv = '\uFEFF' + rows.map((r) => r.join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `aqp-watchlist-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const removeSelected = () => {
    const group = tab !== 'all' && tab !== 'stock' && tab !== 'etf' && tab !== 'futures' && tab !== 'hk' ? tab : null;
    selected.forEach((s) => {
      if (group) store.removeFrom(group, s);
      else (symbolGroup[s] ?? []).forEach((g) => store.removeFrom(g, s));
    });
    setSelected(new Set());
  };

  const createGroup = () => {
    if (newGroupName.trim()) store.createGroup(newGroupName);
    setNewGroupName('');
    setNewGroupOpen(false);
  };

  const selItems = items.filter((i) => selected.has(i.symbol));
  const alertDesc = summary?.alert_kinds?.length ? `（${summary.alert_kinds.slice(0, 2).join('、')}）` : '（暂无）';

  const TABS = [
    { key: 'all', label: `全部 (${counts.all})` },
    { key: 'stock', label: `A股 (${counts.stock})` },
    { key: 'etf', label: `ETF (${counts.etf})` },
    { key: 'futures', label: `期货主力 (${counts.futures})` },
    { key: 'hk', label: `港股通 (${counts.hk})` },
    ...customGroups.map((g) => ({ key: g, label: `${g} (${store.groups[g]?.length ?? 0})` })),
  ];

  return (
    <div className="flex min-h-full flex-col gap-3">
      {/* 标题 */}
      <PageHeader title="我的收藏" />

      {/* 提示条 */}
      <div className="flex items-center justify-between rounded-md border border-blue-100 bg-blue-50/70 px-3 py-1.5 text-xs text-ink-secondary">
        <div className="flex items-center gap-2">
          <span className="text-amber-400">💡</span>
          <span>提示：自选池支持 A股/ETF/期货主力/港股通 多资产跨市场统一纳池，量化指标按刷新频率动态计算</span>
        </div>
        <span className="num text-ink-muted">上次同步: {lastSync}</span>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}

      {dash?.status === 'degraded' && (
        <div
          role="alert"
          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
        >
          数据降级：{dash.reason ?? '部分数据源暂不可用'}；缺失字段保持空值，不做推测填充。
        </div>
      )}

      {/* KPI 4 卡 */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[
          {
            label: '自选资产总数',
            value: `${summary?.count ?? '—'} 只`,
            tone: 'text-ink',
            icon: <KpiIcon kind="bars" />,
          },
          {
            label: '今日平均涨跌幅',
            value:
              summary?.avg_pct != null
                ? `${summary.avg_pct >= 0 ? '+' : ''}${summary.avg_pct}% ${summary.avg_pct >= 0 ? '↑' : '↓'}`
                : '—',
            tone: summary?.avg_pct == null ? 'text-ink-muted' : summary.avg_pct >= 0 ? 'text-red-500' : 'text-emerald-600',
            icon: <KpiIcon kind="trend" />,
          },
          {
            label: '主力资金净流入',
            value:
              summary?.flow_total_yi != null
                ? `${summary.flow_total_yi >= 0 ? '+' : ''}${summary.flow_total_yi.toFixed(2)} 亿元`
                : '—',
            tone:
              summary?.flow_total_yi == null
                ? 'text-ink-muted'
                : summary.flow_total_yi >= 0
                ? 'text-red-500'
                : 'text-emerald-600',
            icon: <KpiIcon kind="flow" />,
          },
          {
            label: '预警异动信号',
            value: summary?.alert_count != null ? `${summary.alert_count} 只 ${alertDesc}` : '—',
            tone: summary?.alert_count == null ? 'text-ink-muted' : 'text-red-500',
            icon: <KpiIcon kind="bell" />,
          },
        ].map((k) => (
          <div
            key={k.label}
            className="flex items-center justify-between rounded-lg border border-hair bg-white px-4 py-3.5"
          >
            <div className="min-w-0">
              <div className="text-xs text-ink-secondary">{k.label}</div>
              <div className={`num mt-1 truncate text-xl font-semibold ${k.tone}`}>{k.value}</div>
            </div>
            {k.icon}
          </div>
        ))}
      </div>

      {/* 自选池组合即时风险透视 */}
      <WatchlistRiskRadar
        symbols={selected.size > 0 ? Array.from(selected) : shownSyms.length ? shownSyms : allSymbols}
        totalSymbolsCount={items.length}
      />

      {/* 分组 Tab 与 搜索栏 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => {
                setTab(t.key);
                setSelected(new Set());
              }}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                tab === t.key
                  ? 'bg-brand-500 font-medium text-white'
                  : 'bg-slate-100 text-ink-secondary hover:bg-slate-200'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          {/* 仅看预警异动快捷过滤 Chip */}
          <button
            onClick={() => setOnlyAlert(!onlyAlert)}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors border ${
              onlyAlert
                ? 'border-red-300 bg-red-50 text-red-600 font-semibold'
                : 'border-hair bg-white text-ink-secondary hover:bg-slate-50'
            }`}
            title="仅筛选包含突破MA20/放量等异动预警信号的标的"
          >
            <span>⚡ 仅看异动</span>
            <span
              className={`num rounded px-1 text-2xs ${
                onlyAlert ? 'bg-red-200 text-red-700' : 'bg-slate-100 text-ink-muted'
              }`}
            >
              {alertCount}
            </span>
          </button>

          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索自选代码/名称..."
            className="w-40 rounded-md border border-hair px-2.5 py-1.5 text-xs outline-none focus:border-brand-300"
          />
          {newGroupOpen ? (
            <span className="flex items-center gap-1">
              <input
                autoFocus
                value={newGroupName}
                onChange={(e) => setNewGroupName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && createGroup()}
                placeholder="分组名称"
                className="w-28 rounded-md border border-brand-300 px-2 py-1.5 text-xs outline-none"
              />
              <button
                onClick={createGroup}
                className="rounded-md bg-brand-500 px-2 py-1.5 text-xs text-white hover:bg-brand-600"
              >
                确定
              </button>
              <button
                onClick={() => {
                  setNewGroupOpen(false);
                  setNewGroupName('');
                }}
                className="px-1 text-xs text-ink-muted hover:text-ink"
              >
                取消
              </button>
            </span>
          ) : (
            <button
              onClick={() => setNewGroupOpen(true)}
              className="rounded-md bg-brand-500 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
            >
              ＋ 新增分组
            </button>
          )}
        </div>
      </div>

      {/* 阶段二：专业多重视图切换矩阵卡片 */}
      <div className="rounded-lg border border-hair bg-white">
        {/* 顶部视图切换工具条 */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair px-4 py-2 bg-slate-50/50">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-semibold text-ink">自选多维看板</span>
            <span className="text-2xs text-ink-muted">（共展示 {shown.length} 只标的）</span>
          </div>

          <div className="inline-flex rounded-md border border-hair p-0.5 bg-slate-100">
            {VIEW_MODES.map((vm) => (
              <button
                key={vm.key}
                onClick={() => setViewMode(vm.key)}
                className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  viewMode === vm.key
                    ? 'bg-white text-brand-600 shadow-2xs font-semibold'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <span>{vm.icon}</span>
                <span>{vm.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 自选主表 */}
        <div className="overflow-x-auto">
          <table className="quant-table w-full">
            <thead>
              <tr>
                <th className="w-9">
                  <input
                    type="checkbox"
                    checked={allChecked}
                    onChange={toggleAll}
                    className="h-3.5 w-3.5 accent-brand-500"
                  />
                </th>
                <th
                  className="cursor-pointer select-none hover:text-brand-600 transition-colors"
                  onClick={() => handleSort('code')}
                >
                  代码 {sortIcon('code')}
                </th>
                <th>标的名称 / 品类</th>

                {/* 视图 1：交易行情列 */}
                {viewMode === 'trade' && (
                  <>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('close')}
                    >
                      最新价 {sortIcon('close')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('pct')}
                    >
                      涨跌幅 {sortIcon('pct')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('amount_yi')}
                    >
                      成交额 {sortIcon('amount_yi')}
                    </th>
                    <th>K线多空形态</th>
                    <th>预警信号</th>
                  </>
                )}

                {/* 视图 2：量化因子列 */}
                {viewMode === 'factor' && (
                  <>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('alpha_score')}
                    >
                      多头评分 {sortIcon('alpha_score')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('momentum_20')}
                    >
                      20日动量 {sortIcon('momentum_20')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('volatility_20')}
                    >
                      年化波动(20D) {sortIcon('volatility_20')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('rsi_14')}
                    >
                      RSI(14) {sortIcon('rsi_14')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('mdd_30')}
                    >
                      30日回撤 {sortIcon('mdd_30')}
                    </th>
                  </>
                )}

                {/* 视图 3：估值基本面列 */}
                {viewMode === 'fundamental' && (
                  <>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('close')}
                    >
                      最新价 {sortIcon('close')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('pe')}
                    >
                      PE(TTM) {sortIcon('pe')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('pb')}
                    >
                      PB(市净率) {sortIcon('pb')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('dividend_yield')}
                    >
                      股息率 {sortIcon('dividend_yield')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('roe')}
                    >
                      ROE(%) {sortIcon('roe')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('turnover_rate')}
                    >
                      换手率 {sortIcon('turnover_rate')}
                    </th>
                  </>
                )}

                {/* 视图 4：风险与衍生列 */}
                {viewMode === 'risk' && (
                  <>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('close')}
                    >
                      最新价 {sortIcon('close')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('volatility_20')}
                    >
                      20日年化波动率 {sortIcon('volatility_20')}
                    </th>
                    <th
                      className="text-right cursor-pointer select-none hover:text-brand-600 transition-colors"
                      onClick={() => handleSort('mdd_30')}
                    >
                      30日区间最大回撤 {sortIcon('mdd_30')}
                    </th>
                    <th>K线多空形态</th>
                    <th>异动预警</th>
                  </>
                )}

                <th className="text-center">操作</th>
              </tr>
            </thead>

            <tbody>
              {shown.length ? (
                shown.map((it) => {
                  const checked = selected.has(it.symbol);
                  return (
                    <tr key={it.symbol} className={checked ? 'bg-brand-50/60' : ''}>
                      <td>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleOne(it.symbol)}
                          className="h-3.5 w-3.5 accent-brand-500"
                        />
                      </td>
                      <td className="num text-xs font-medium text-ink">{it.code}</td>
                      <td className="max-w-[13rem] truncate text-xs text-ink-secondary" title={it.name ?? it.symbol}>
                        <div className="flex items-center gap-1 truncate">
                          <span className="truncate">{it.name ?? '—'}</span>
                          <AssetTypeBadge type={it.type} />
                        </div>
                      </td>

                      {/* 视图 1：交易行情 */}
                      {viewMode === 'trade' && (
                        <>
                          <td className="num text-right text-xs text-ink">{it.close?.toFixed(3) ?? '—'}</td>
                          <td className={`num text-right text-xs font-medium ${pctClass(it.pct)}`}>
                            {it.pct != null ? `${fmtPct(it.pct)} ${it.pct >= 0 ? '▲' : '▼'}` : '—'}
                          </td>
                          <td className="num text-right text-xs text-ink-secondary">
                            {it.amount_yi != null ? `${it.amount_yi.toFixed(2)}亿` : '—'}
                          </td>
                          <td>
                            <span className="flex items-center gap-2">
                              <Sparkline closes={it.closes ?? []} up={it.pct == null ? undefined : it.pct >= 0} />
                              {it.kline_state && (
                                <span
                                  className={`whitespace-nowrap rounded border px-1.5 py-0.5 text-2xs ${
                                    KLINE_BADGE[it.kline_state] ?? KLINE_BADGE['平稳']
                                  }`}
                                >
                                  {it.kline_state}
                                </span>
                              )}
                            </span>
                          </td>
                          <td className="text-xs text-ink-secondary">{it.alert ?? '-'}</td>
                        </>
                      )}

                      {/* 视图 2：量化因子 */}
                      {viewMode === 'factor' && (
                        <>
                          <td className="text-right">
                            <AlphaScoreBadge score={it.alpha_score} />
                          </td>
                          <td className={`num text-right text-xs font-medium ${pctClass(it.momentum_20)}`}>
                            {it.momentum_20 != null ? fmtPct(it.momentum_20) : '—'}
                          </td>
                          <td className="num text-right text-xs text-ink">
                            {it.volatility_20 != null ? `${it.volatility_20.toFixed(2)}%` : '—'}
                          </td>
                          <td className="num text-right text-xs text-ink">
                            {it.rsi_14 != null ? it.rsi_14.toFixed(1) : '—'}
                          </td>
                          <td className="num text-right text-xs text-rose-500">
                            {it.mdd_30 != null ? `-${it.mdd_30.toFixed(2)}%` : '—'}
                          </td>
                        </>
                      )}

                      {/* 视图 3：估值基本面 */}
                      {viewMode === 'fundamental' && (
                        <>
                          <td className="num text-right text-xs text-ink">{it.close?.toFixed(3) ?? '—'}</td>
                          <td className="num text-right text-xs text-ink">
                            {it.pe != null ? it.pe.toFixed(2) : '—'}
                          </td>
                          <td className="num text-right text-xs text-ink">
                            {it.pb != null ? it.pb.toFixed(2) : '—'}
                          </td>
                          <td className="num text-right text-xs text-ink">
                            {it.dividend_yield != null ? `${it.dividend_yield.toFixed(2)}%` : '—'}
                          </td>
                          <td className="num text-right text-xs text-ink">
                            {it.roe != null ? `${it.roe.toFixed(2)}%` : '—'}
                          </td>
                          <td className="num text-right text-xs text-ink-secondary">
                            {it.turnover_rate != null ? `${it.turnover_rate.toFixed(2)}%` : '—'}
                          </td>
                        </>
                      )}

                      {/* 视图 4：风险与衍生 */}
                      {viewMode === 'risk' && (
                        <>
                          <td className="num text-right text-xs text-ink">{it.close?.toFixed(3) ?? '—'}</td>
                          <td className="num text-right text-xs text-ink">
                            {it.volatility_20 != null ? `${it.volatility_20.toFixed(2)}%` : '—'}
                          </td>
                          <td className="num text-right text-xs text-rose-500">
                            {it.mdd_30 != null ? `-${it.mdd_30.toFixed(2)}%` : '—'}
                          </td>
                          <td>
                            {it.kline_state && (
                              <span
                                className={`whitespace-nowrap rounded border px-1.5 py-0.5 text-2xs ${
                                  KLINE_BADGE[it.kline_state] ?? KLINE_BADGE['平稳']
                                }`}
                              >
                                {it.kline_state}
                              </span>
                            )}
                          </td>
                          <td className="text-xs text-ink-secondary">{it.alert ?? '-'}</td>
                        </>
                      )}

                      {/* 操作列 */}
                      <td className="whitespace-nowrap text-center">
                        <button
                          onClick={() => gotoAnalysis(it)}
                          className="mr-1.5 rounded border border-brand-200 bg-brand-50 px-2 py-0.5 text-2xs text-brand-600 hover:bg-brand-100"
                        >
                          深度分析
                        </button>
                        <button
                          onClick={() => importToPortfolio([it.symbol])}
                          title="以单标的组合进入组合回测"
                          className="mr-1.5 rounded border border-brand-200 bg-brand-50 px-2 py-0.5 text-2xs text-brand-600 hover:bg-brand-100"
                        >
                          回测
                        </button>
                        <button
                          onClick={() => navigate(`/desk?symbol=${encodeURIComponent(it.symbol)}&side=buy`)}
                          title="带入交易大厅挂单执行"
                          className="rounded border border-hair bg-surface px-2 py-0.5 text-2xs text-ink-secondary hover:bg-surface-alt hover:text-ink"
                        >
                          下单
                        </button>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={10}>
                    <PanelEmpty
                      text={
                        loading
                          ? '行情加载中…'
                          : allSymbols.length
                          ? '当前分组暂无标的'
                          : '暂无自选，请在选股/ETF/期货页加入收藏'
                      }
                      minH="min-h-[160px]"
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 量化联动工具箱 */}
      <div className="rounded-lg border border-hair bg-white px-4 py-3">
        <div className="mb-2.5 text-sm font-semibold text-ink">
          选中标的的量化联动工具箱
          <span className="ml-1 text-xs font-normal text-ink-muted">（已选择 {selected.size} 项）</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => selected.size && importToPortfolio([...selected])}
            disabled={!selected.size}
            className="flex items-center gap-1.5 rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
            一键导入组合回测
          </button>
          <button
            onClick={() => selected.size && importToPortfolio([...selected])}
            disabled={!selected.size}
            className="flex items-center gap-1.5 rounded-md border border-brand-300 bg-brand-50 px-3 py-1.5 text-xs font-medium text-brand-700 hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-50"
            title="将选中标的以均等权重载入组合分析并使用马科维茨/凸优化求解器"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v5l3 3" />
            </svg>
            导入组合优化求解器
          </button>
          <button
            onClick={() => void openCorrelation()}
            disabled={selected.size < 2}
            className="flex items-center gap-1.5 rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
              <path d="M4 20V10M9 20V4M14 20v-7M19 20V8" />
            </svg>
            生成收益相关性矩阵
          </button>
          <button
            onClick={() => setValOpen(true)}
            disabled={!selected.size}
            className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            仪表盘：估值快照
          </button>
          <button
            onClick={exportCsv}
            disabled={!selected.size}
            className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            批量导出 CSV
          </button>
          <button
            onClick={removeSelected}
            disabled={!selected.size}
            className="rounded-md border border-red-200 px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            移出分组
          </button>
        </div>
      </div>

      {/* 相关性弹窗 */}
      <CorrModal open={corrOpen} onClose={() => setCorrOpen(false)} data={corrData} loading={corrLoading} />

      {/* 估值快照弹窗 */}
      <Modal
        title="估值快照（PE / PB）"
        sub="多维行情实时快照 · 仅股权标的有效"
        open={valOpen}
        onClose={() => setValOpen(false)}
      >
        {selItems.some((i) => i.pe != null || i.pb != null) ? (
          <table className="quant-table w-full">
            <thead>
              <tr>
                <th>标的</th>
                <th className="text-right">PE(TTM)</th>
                <th className="text-right">PB</th>
                <th className="text-right">股息率</th>
                <th className="text-right">ROE</th>
              </tr>
            </thead>
            <tbody>
              {selItems.map((i) => (
                <tr key={i.symbol}>
                  <td className="text-xs">
                    {i.name ?? i.code}
                    <span className="num ml-1 text-2xs text-ink-muted">{i.code}</span>
                  </td>
                  <td className="num text-right text-xs">{i.pe?.toFixed(2) ?? '—'}</td>
                  <td className="num text-right text-xs">{i.pb?.toFixed(2) ?? '—'}</td>
                  <td className="num text-right text-xs">{i.dividend_yield != null ? `${i.dividend_yield.toFixed(2)}%` : '—'}</td>
                  <td className="num text-right text-xs">{i.roe != null ? `${i.roe.toFixed(2)}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <PanelEmpty text="选中标的中无估值数据（ETF与商品期货不适用 PE/PB）" minH="min-h-[80px]" />
        )}
      </Modal>
    </div>
  );
}
