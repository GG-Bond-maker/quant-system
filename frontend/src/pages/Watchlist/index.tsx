/**
 * 我的收藏（/watchlist）：多资产自选监控 + 量化联动。
 *
 * 布局对照设计稿（参考图）：
 *   标题 + 提示条（行情实时刷新中 · 上次同步时间）
 *   KPI 4 卡：自选资产总数 / 今日平均涨跌幅 / 主力资金净流入 / 预警异动信号
 *   分组 Tab（全部/A股/ETF/自定义）+ 搜索 + 新增分组
 *   自选行情表：勾选 | 代码 | 名称 | 最新价 | 涨跌幅 | 成交额 | K线状态(迷你图+徽标) | 预警信号 | 操作(分析/回测)
 *   量化联动工具箱：一键导入组合回测 / 生成收益相关性矩阵 / 估值快照 / 导出CSV / 移出分组
 *
 * 行情来自后端 /api/v1/watchlist/dashboard（本地 Parquet + 腾讯 K 线，真实数据）；
 * 分组存 localStorage（useWatchlistStore）；行情轮询走共享 hook
 * useWatchlistQuotes（间隔 = 系统设置 refresh_freq × 12，默认 60s）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { watchlistApi } from '@/api/watchlist';
import { useTheme } from '@/hooks/useTheme';
import { Modal, PageHeader, PanelEmpty } from '@/components/ui';
import { useWatchlistQuotes } from '@/hooks/useWatchlistQuotes';
import { useWatchlistStore, DEFAULT_GROUP } from '@/stores/useWatchlistStore';
import type { CorrResult, WatchItem } from '@/types/watchlist';
import { fmtPct, pctClass } from '@/utils/format';

/* ==================== 常量 ==================== */
const SEED_FLAG = 'aqp-watchlist-seeded';
const PORTFOLIO_IMPORT_KEY = 'AQP_PORTFOLIO_IMPORT';

/** 首次访问预置示例组合（覆盖真实已落库股票 + 主流 ETF），可自由增删 */
const SEED: Record<string, string[]> = {
  [DEFAULT_GROUP]: ['600519.SH', '000001.SZ', '300750.SZ', '600036.SH', '510300', '159915', '513050', '510500'],
  核心观察: ['600519.SH', '300750.SZ', '510300'],
  高股息策略: ['000001.SZ', '600036.SH'],
};

const KLINE_BADGE: Record<string, string> = {
  均线多头: 'border-emerald-200 bg-emerald-50 text-emerald-600',
  均线空头: 'border-red-200 bg-red-50 text-red-500',
  放量突破: 'border-red-200 bg-red-50 text-red-600 font-medium',
  触及支撑: 'border-blue-200 bg-blue-50 text-blue-600',
  窄幅震荡: 'border-amber-200 bg-amber-50 text-amber-600',
  平稳: 'border-slate-200 bg-slate-50 text-ink-muted',
};

/* ==================== 工具 ==================== */
function isEtf(sym: string): boolean {
  return /^\d{6}$/.test(sym) && ['51', '56', '58', '15'].includes(sym.slice(0, 2));
}

/**
 * 迷你 K 线（SVG 折线，红涨绿跌跟随当日涨跌）。
 * `up` 可空：当日涨跌幅未知（`it.pct == null`）时传 `undefined`，走中性灰
 * （`#94A3B8` = 项目 flat/ink-muted 色）——不得默认染红，否则把"未知"画成"涨"。
 */
function Sparkline({ closes, up }: { closes: Array<number | null>; up?: boolean }) {
  const p = chartPalette();
  const pts = closes.filter((c): c is number => c != null);
  if (pts.length < 3) return <div className="h-7 w-16" />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const w = 64, h = 26;
  const path = pts.map((c, i) => {
    const x = (i / (pts.length - 1)) * w;
    const y = h - 2 - ((c - min) / range) * (h - 4);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  // A 股惯例：红涨绿跌；未知 = 中性色（不得染成涨或跌）
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
  if (kind === 'bars') return (
    <svg viewBox="0 0 24 24" fill="none" stroke={p.BRAND} strokeWidth={1.6} strokeLinecap="round" className={cls}>
      <path d="M4 20V10M9 20V4M14 20v-7M19 20V8" /><path d="M3 21h18" />
    </svg>
  );
  if (kind === 'trend') return (
    <svg viewBox="0 0 24 24" fill="none" stroke={p.UP} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={cls}>
      <path d="m3 17 6-6 4 4 8-9" /><path d="M15 6h6v6" />
    </svg>
  );
  if (kind === 'flow') return (
    <svg viewBox="0 0 24 24" fill="none" stroke={p.WARN} strokeWidth={1.6} strokeLinecap="round" className={cls}>
      <path d="M4 19V9m5 10V5m5 14v-7m5 7V8" /><path d="M3 21h18" opacity={0.4} />
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
function CorrModal({ open, onClose, data, loading }: {
  open: boolean; onClose: () => void; data: CorrResult | null; loading: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  useEffect(() => {
    if (!open || !ref.current || !data || data.symbols.length < 2) return;
    const chart = echarts.init(ref.current);
    const p = chartPalette();
    chart.setOption({
      tooltip: { position: 'top',
        formatter: (p: { data: [number, number, number] }) =>
          `${data.symbols[p.data[0]]} × ${data.symbols[p.data[1]]}<br/>相关系数 <b>${p.data[2].toFixed(3)}</b>` },
      grid: { left: 90, bottom: 80, top: 12, right: 12 },
      xAxis: { type: 'category', data: data.symbols, axisLabel: { fontSize: 9, rotate: 45 } },
      yAxis: { type: 'category', data: data.symbols, axisLabel: { fontSize: 9 } },
      visualMap: {
        min: -1, max: 1, calculable: true, orient: 'horizontal',
        left: 'center', bottom: 0, itemHeight: 60,
        inRange: { color: [p.DOWN, p.CARD, p.UP] },
        textStyle: { fontSize: 9 },
      },
      series: [{
        type: 'heatmap',
        data: data.matrix.flatMap((row, i) =>
          row.map((v, j) => [i, j, v ?? Number.NaN]) as [number, number, number][]),
        label: { show: data.symbols.length <= 10, fontSize: 8,
          formatter: (p: { data: [number, number, number] }) =>
            Number.isNaN(p.data[2]) ? '-' : p.data[2].toFixed(2) },
      }],
    });
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); chart.dispose(); };
  }, [open, data, theme]);
  return (
    <Modal title="收益相关性矩阵" sub={data ? `最近 ${data.days} 个交易日 · 日收益率 Pearson 相关系数` : ''}
      open={open} onClose={onClose}>
      {loading ? (
        <div className="py-10 text-center text-xs text-ink-muted">计算中…</div>
      ) : data && data.symbols.length >= 2 ? (
        <div ref={ref} style={{ height: 360 }} className="w-full" />
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
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [newGroupOpen, setNewGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [corrOpen, setCorrOpen] = useState(false);
  const [corrData, setCorrData] = useState<CorrResult | null>(null);
  const [corrLoading, setCorrLoading] = useState(false);
  const [valOpen, setValOpen] = useState(false);

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
    [store.groups]);

  const symbolGroup = useMemo(() => {
    const m: Record<string, string[]> = {};
    Object.entries(store.groups).forEach(([g, syms]) =>
      syms.forEach((s) => { (m[s] ??= []).push(g); }));
    return m;
  }, [store.groups]);

  /* ---------- 行情加载 + 轮询（共享 hook，P1-6） ----------
   * 间隔 = refresh_freq × 12（默认 5s × 12 = 60s，与原行为一致） */
  const { data: dash, loading, error } =
    useWatchlistQuotes(watchlistApi.dashboard, 12);
  const items = dash?.items ?? [];
  const summary = dash?.summary ?? null;
  useEffect(() => {
    if (dash) setLastSync(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
  }, [dash]);

  /* ---------- 派生 ---------- */
  const customGroups = useMemo(
    () => Object.keys(store.groups).filter((g) => g !== DEFAULT_GROUP),
    [store.groups]);

  const shown = useMemo(() => {
    let list = items;
    if (tab === 'stock') list = list.filter((i) => i.type === 'stock');
    else if (tab === 'etf') list = list.filter((i) => i.type === 'etf');
    else if (tab !== 'all') list = list.filter((i) => (store.groups[tab] ?? []).includes(i.symbol));
    const q = query.trim().toLowerCase();
    if (q) list = list.filter((i) =>
      i.symbol.toLowerCase().includes(q) || (i.name ?? '').toLowerCase().includes(q));
    return list;
  }, [items, tab, query, store.groups]);

  const counts = {
    all: items.length,
    stock: items.filter((i) => i.type === 'stock').length,
    etf: items.filter((i) => i.type === 'etf').length,
  };

  const shownSyms = shown.map((i) => i.symbol);
  const allChecked = shownSyms.length > 0 && shownSyms.every((s) => selected.has(s));
  const toggleAll = () => setSelected(allChecked ? new Set() : new Set(shownSyms));
  const toggleOne = (s: string) => setSelected((cur) => {
    const next = new Set(cur);
    if (next.has(s)) next.delete(s); else next.add(s);
    return next;
  });

  /* ---------- 联动 ---------- */
  const gotoAnalysis = (it: WatchItem) => {
    if (it.type === 'etf') navigate(`/etf/${it.code}`);
    else navigate(`/stock/${encodeURIComponent(it.symbol)}`);
  };

  const importToPortfolio = (syms: string[]) => {
    const assets = syms.map((s) => {
      const it = items.find((i) => i.symbol === s);
      return {
        code: it?.code ?? s.replace(/\..*$/, ''),
        name: it?.name ?? s,
        type: isEtf(s) ? 'etf' : 'stock',
        weight: Number((1 / syms.length).toFixed(4)),
      };
    });
    localStorage.setItem(PORTFOLIO_IMPORT_KEY, JSON.stringify(assets));
    navigate('/portfolio');
  };

  const openCorrelation = async () => {
    const syms = [...selected];
    if (syms.length < 2) return;
    setCorrOpen(true); setCorrLoading(true);
    try { setCorrData(await watchlistApi.correlation(syms, 60)); }
    catch { setCorrData(null); }
    finally { setCorrLoading(false); }
  };

  const exportCsv = () => {
    const rows = [['代码', '名称', '类型', '最新价', '涨跌幅%', '成交额(亿)', 'K线状态', '预警信号', '所属分组', 'PE', 'PB']];
    items.filter((i) => selected.has(i.symbol)).forEach((i) => {
      rows.push([i.code, i.name ?? '', i.type === 'etf' ? 'ETF' : 'A股',
        i.close?.toFixed(3) ?? '', i.pct?.toFixed(2) ?? '', i.amount_yi?.toFixed(2) ?? '',
        i.kline_state ?? '', i.alert ?? '', (symbolGroup[i.symbol] ?? []).join('|'),
        i.pe?.toFixed(2) ?? '', i.pb?.toFixed(2) ?? '']);
    });
    const csv = '\uFEFF' + rows.map((r) => r.join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = `aqp-watchlist-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click(); URL.revokeObjectURL(url);
  };

  const removeSelected = () => {
    // 当前 Tab 为自定义分组时从该组移除，否则从所有分组移除
    const group = tab !== 'all' && tab !== 'stock' && tab !== 'etf' ? tab : null;
    selected.forEach((s) => {
      if (group) store.removeFrom(group, s);
      else (symbolGroup[s] ?? []).forEach((g) => store.removeFrom(g, s));
    });
    setSelected(new Set());
  };

  const createGroup = () => {
    if (newGroupName.trim()) store.createGroup(newGroupName);
    setNewGroupName(''); setNewGroupOpen(false);
  };

  const selItems = items.filter((i) => selected.has(i.symbol));
  const alertDesc = summary?.alert_kinds?.length ? `（${summary.alert_kinds.slice(0, 2).join('、')}）` : '（暂无）';

  const TABS = [
    { key: 'all', label: `全部 (${counts.all})` },
    { key: 'stock', label: `A股 (${counts.stock})` },
    { key: 'etf', label: `ETF (${counts.etf})` },
    ...customGroups.map((g) => ({ key: g, label: `${g} (${store.groups[g]?.length ?? 0})` })),
  ];

  return (
    <div className="flex min-h-full flex-col gap-3">
      {/* 标题：统一走 PageHeader（全站唯一一级标题写法） */}
      <PageHeader title="我的收藏" />

      {/* 提示条 */}
      <div className="flex items-center gap-2 rounded-md border border-blue-100 bg-blue-50/70 px-3 py-1.5 text-xs text-ink-secondary">
        <span className="text-amber-400">💡</span>
        <span>提示：自选标的行情按设置的刷新频率自动更新（数据源：AKShare / 本地行情库）</span>
        <span className="num text-ink-muted">上次同步: {lastSync}</span>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">{error}</div>
      )}

      {/* F-06：消费后端 status='degraded'，不得把降级静默当正常 */}
      {dash?.status === 'degraded' && (
        <div role="alert"
          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          数据降级：{dash.reason ?? '部分数据源暂不可用'}；缺失字段保持空值，不做推测填充。
        </div>
      )}

      {/* KPI 4 卡 */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[
          { label: '自选资产总数', value: `${summary?.count ?? '—'} 只`, tone: 'text-ink', icon: <KpiIcon kind="bars" /> },
          { label: '今日平均涨跌幅',
            value: summary?.avg_pct != null ? `${summary.avg_pct >= 0 ? '+' : ''}${summary.avg_pct}% ${summary.avg_pct >= 0 ? '↑' : '↓'}` : '—',
            // ⚠️ avg_pct 可为 null（无数据）：不得用 `(avg_pct ?? 0)` 兜底 —— null 会被当非负染红，
            // 与上一行 value 的 '—' 自相矛盾。null ⇒ 中性色。
            tone: summary?.avg_pct == null ? 'text-ink-muted' : summary.avg_pct >= 0 ? 'text-red-500' : 'text-emerald-600',
            icon: <KpiIcon kind="trend" /> },
          { label: '主力资金净流入',
            value: summary?.flow_total_yi != null
              ? `${summary.flow_total_yi >= 0 ? '+' : ''}${summary.flow_total_yi.toFixed(2)} 亿元`
              : '—',
            // 同上：flow_total_yi 可为 null，不得用 `?? 0` 兜出红色方向。
            tone: summary?.flow_total_yi == null ? 'text-ink-muted' : summary.flow_total_yi >= 0 ? 'text-red-500' : 'text-emerald-600',
            icon: <KpiIcon kind="flow" /> },
          // ⚠️ 不得用 `summary?.alert_count ?? 0`：summary 为 null（首载/取数失败）时会把
          // "没有数据"写成「0 只 （暂无）」，伪造出"零条告警"。改为缺数显 '—' + 中性色。
          { label: '预警异动信号',
            value: summary?.alert_count != null ? `${summary.alert_count} 只 ${alertDesc}` : '—',
            tone: summary?.alert_count == null ? 'text-ink-muted' : 'text-red-500',
            icon: <KpiIcon kind="bell" /> },
        ].map((k) => (
          <div key={k.label} className="flex items-center justify-between rounded-lg border border-hair bg-white px-4 py-3.5">
            <div className="min-w-0">
              <div className="text-xs text-ink-secondary">{k.label}</div>
              <div className={`num mt-1 truncate text-xl font-semibold ${k.tone}`}>{k.value}</div>
            </div>
            {k.icon}
          </div>
        ))}
      </div>

      {/* 分组 Tab + 搜索 + 新增分组 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => { setTab(t.key); setSelected(new Set()); }}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                tab === t.key
                  ? 'bg-brand-500 font-medium text-white'
                  : 'bg-slate-100 text-ink-secondary hover:bg-slate-200'}`}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索自选标的..."
            className="w-44 rounded-md border border-hair px-2.5 py-1.5 text-xs outline-none focus:border-brand-300" />
          {newGroupOpen ? (
            <span className="flex items-center gap-1">
              <input autoFocus value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && createGroup()}
                placeholder="分组名称"
                className="w-28 rounded-md border border-brand-300 px-2 py-1.5 text-xs outline-none" />
              <button onClick={createGroup}
                className="rounded-md bg-brand-500 px-2 py-1.5 text-xs text-white hover:bg-brand-600">确定</button>
              <button onClick={() => { setNewGroupOpen(false); setNewGroupName(''); }}
                className="px-1 text-xs text-ink-muted hover:text-ink">取消</button>
            </span>
          ) : (
            <button onClick={() => setNewGroupOpen(true)}
              className="rounded-md bg-brand-500 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-brand-600">
              ＋ 新增分组
            </button>
          )}
        </div>
      </div>

      {/* 自选行情表 */}
      <div className="rounded-lg border border-hair bg-white">
        <div className="overflow-x-auto">
          <table className="quant-table w-full">
            <thead><tr>
              <th className="w-9"><input type="checkbox" checked={allChecked} onChange={toggleAll}
                className="h-3.5 w-3.5 accent-brand-500" /></th>
              <th>代码</th><th>名称</th>
              <th className="text-right">最新价</th>
              <th className="text-right">涨跌幅</th>
              <th className="text-right">成交额</th>
              <th>K线状态</th>
              <th>预警信号</th>
              <th className="text-center">操作</th>
            </tr></thead>
            <tbody>
              {shown.length ? shown.map((it) => {
                const checked = selected.has(it.symbol);
                return (
                  <tr key={it.symbol} className={checked ? 'bg-brand-50/60' : ''}>
                    <td><input type="checkbox" checked={checked} onChange={() => toggleOne(it.symbol)}
                      className="h-3.5 w-3.5 accent-brand-500" /></td>
                    <td className="num text-xs font-medium text-ink">{it.code}</td>
                    <td className="max-w-[11rem] truncate text-xs text-ink-secondary" title={it.name ?? it.symbol}>
                      {it.name ?? '—'}
                      {it.type === 'etf' && (
                        <span className="ml-1 rounded bg-slate-100 px-1 text-2xs text-ink-muted">ETF</span>
                      )}
                    </td>
                    <td className="num text-right text-xs text-ink">{it.close?.toFixed(3) ?? '—'}</td>
                    <td className={`num text-right text-xs font-medium ${pctClass(it.pct)}`}>
                      {it.pct != null ? `${fmtPct(it.pct)} ${it.pct >= 0 ? '▲' : '▼'}` : '—'}
                    </td>
                    <td className="num text-right text-xs text-ink-secondary">
                      {it.amount_yi != null ? `${it.amount_yi.toFixed(2)}亿` : '—'}
                    </td>
                    <td>
                      <span className="flex items-center gap-2">
                        {/* ⚠️ it.pct 可为 null（无行情）；不得用 `(it.pct ?? 0) >= 0` 兜底 ——
                            那会把"未知"染红（本行涨跌幅已诚实显示 '—'）。null 传 undefined 走中性。 */}
                        <Sparkline closes={it.closes ?? []} up={it.pct == null ? undefined : it.pct >= 0} />
                        {it.kline_state && (
                          <span className={`whitespace-nowrap rounded border px-1.5 py-0.5 text-2xs ${KLINE_BADGE[it.kline_state] ?? KLINE_BADGE['平稳']}`}>
                            {it.kline_state}
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="text-xs text-ink-secondary">{it.alert ?? '-'}</td>
                    <td className="whitespace-nowrap text-center">
                      <button onClick={() => gotoAnalysis(it)}
                        className="mr-1.5 rounded border border-brand-200 bg-brand-50 px-2 py-0.5 text-2xs text-brand-600 hover:bg-brand-100">
                        分析
                      </button>
                      <button onClick={() => importToPortfolio([it.symbol])} title="以单标的组合进入组合回测"
                        className="rounded border border-brand-200 bg-brand-50 px-2 py-0.5 text-2xs text-brand-600 hover:bg-brand-100">
                        回测
                      </button>
                    </td>
                  </tr>
                );
              }) : (
                <tr><td colSpan={9}>
                  <PanelEmpty text={loading ? '行情加载中…' : allSymbols.length ? '当前分组暂无标的' : '暂无自选，请在选股/ETF 页加入收藏'}
                    minH="min-h-[160px]" />
                </td></tr>
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
          <button onClick={() => selected.size && importToPortfolio([...selected])} disabled={!selected.size}
            className="flex items-center gap-1.5 rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
            一键导入组合回测
          </button>
          <button onClick={() => void openCorrelation()} disabled={selected.size < 2}
            className="flex items-center gap-1.5 rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
              <path d="M4 20V10M9 20V4M14 20v-7M19 20V8" />
            </svg>
            生成收益相关性矩阵
          </button>
          <button onClick={() => setValOpen(true)} disabled={!selected.size}
            className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
            仪表盘：估值快照
          </button>
          <button onClick={exportCsv} disabled={!selected.size}
            className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
            批量导出 CSV
          </button>
          <button onClick={removeSelected} disabled={!selected.size}
            className="rounded-md border border-red-200 px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
            移出分组
          </button>
        </div>
      </div>

      {/* 相关性弹窗 */}
      <CorrModal open={corrOpen} onClose={() => setCorrOpen(false)} data={corrData} loading={corrLoading} />

      {/* 估值快照弹窗 */}
      <Modal title="估值快照（PE / PB）" sub="腾讯行情实时快照 · 仅 A 股标的有效"
        open={valOpen} onClose={() => setValOpen(false)}>
        {selItems.some((i) => i.pe != null || i.pb != null) ? (
          <table className="quant-table w-full">
            <thead><tr><th>标的</th><th className="text-right">PE(TTM)</th><th className="text-right">PB</th></tr></thead>
            <tbody>
              {selItems.map((i) => (
                <tr key={i.symbol}>
                  <td className="text-xs">{i.name ?? i.code}
                    <span className="num ml-1 text-2xs text-ink-muted">{i.code}</span></td>
                  <td className="num text-right text-xs">{i.pe?.toFixed(2) ?? '—'}</td>
                  <td className="num text-right text-xs">{i.pb?.toFixed(2) ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <PanelEmpty text="选中标的中无估值数据（ETF 不适用 PE/PB）" minH="min-h-[80px]" />
        )}
      </Modal>
    </div>
  );
}
