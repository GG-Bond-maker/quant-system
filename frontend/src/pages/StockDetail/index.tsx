/**
 * 个股详情（P2-5 视觉重构）：专业头部 + K 线 + 右侧研究栏 + 3×2 研究模块。
 *
 * 布局约定：
 * - 右栏：8 个紧凑行情卡（含总市值 / 流通市值）→ AI 预测 → 技术指标
 * - 研究区：资金流向 | 近期事件 | 风险度量 / 筹码分布 | 股东信息 | 基本面
 * - 面板数据来自聚合接口 /panels，每个块独立降级（unavailable → "暂无数据"）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { alertsApi } from '@/api/alerts';
import { stockApi } from '@/api/stock';
import { useApi } from '@/api/swr';
import type { QuotesData } from '@/api/market';
import { hasMinimumRole } from '@/components/RequireAuth';
import KLineChart from '@/components/charts/KLineChart';
import {
  FactorBar, MetricCard, Modal, PageHeader, PanelEmpty, ProbabilityBar, RatioBar,
  SectionCard, SplitBar, StatRow, LoadingState, ViewToggle, Drawer,
} from '@/components/ui';
import { useAuthStore } from '@/stores/useAuthStore';
import { isMarketOpen } from '@/hooks/useMarketSession';
import { DEFAULT_GROUP, useWatchlistStore } from '@/stores/useWatchlistStore';
import { useUiStore } from '@/stores/useUiStore';
import type {
  ChipBlock, EventItem, EventsBlock, FundamentalsBlock, HoldersBlock,
  KLineResult, MLPredictResult, MoneyFlowBlock, NorthBlock, QuoteBlock,
  RiskBlock, SearchHit, StockPanels, StockProfile,
} from '@/types/stock';
import { factorName, fmtAmountYi, fmtNum, fmtPct, pctClass } from '@/utils/format';

/** 归一化进度条满格阈值：10% 视为满格（覆盖 A 股绝大多数个股的换手/北向占比） */
const FULL_SCALE_PCT = 10;

/** 事件面板首批展示条数（后端按 pub_date 倒序返回，此处只控制首屏渲染量） */
const EVENT_PAGE_SIZE = 15;

/** 事件面板单次增量拉取步长：滚到底时按此步长放大后端 limit */
const EVENT_FETCH_STEP = 15;

/** 事件面板后端 limit 上限（与后端 Query(le=200) 对齐，防止无限放大请求） */
const EVENT_FETCH_MAX = 200;

/* 事件列表可视高度：此前为常驻面板的 `max-h-[260px]`；改为摘要卡 + 抽屉后，
   高度由 Drawer 内部容器自适应（`calc(100vh - 200px)`），此常量已废弃。 */

/** 实时行情轮询间隔（秒）：与后端 QUOTES_TTL（15s 进程缓存）同频，轮询即命中缓存 */
const LIVE_QUOTE_REFRESH_S = 15;

/* 盘中判定已抽到共享 hook（此前与 MarketOverview 各存一份副本） */


/**
 * 个股实时价徽标（GET /market/quotes）。
 * 头部主价格取的是日线收盘价，盘中是过期的；本徽标补一份盘中快照（仅展示，不落库）。
 * 纪律：降级/失败/无数据都必须可见，不得静默隐藏（此前该端点无任何前端入口）。
 */
function LiveQuoteChip({ symbol }: { symbol: string }) {
  const open = isMarketOpen();
  const { data, error, isLoading, mutate } = useApi<QuotesData>(
    '/api/v1/market/quotes',
    { symbols: symbol, _t: open ? 'live' : 'off' },
    { refreshInterval: open ? LIVE_QUOTE_REFRESH_S * 1000 : 0 },
    20_000,
  );

  const baseCls = 'rounded px-1.5 py-0.5 text-2xs font-medium';
  const retry = (
    <button type="button" onClick={() => void mutate()}
      title={error instanceof ApiError ? error.message : '实时行情加载失败，点击重试'}
      className={`${baseCls} bg-warn-bg text-warn hover:bg-warn-bg`}>
      实时行情加载失败 · 点击重试
    </button>
  );

  if (error) return retry;
  if (isLoading && !data) return <span className={`${baseCls} bg-surface-sunken text-ink-muted`}>实时行情加载中…</span>;

  const q = data?.quotes?.[0];
  // 降级（source=degraded）或返回空数组：显式标注不可用，不拿日线价冒充实时价
  if (!q || data?.source === 'degraded') {
    return (
      <span className={`${baseCls} bg-surface-sunken text-ink-muted`}
        title={`数据源降级（source=${data?.source ?? 'unknown'}），实时快照暂不可用`}>
        实时行情不可用
      </span>
    );
  }
  const pct = q.pct;
  return (
    <span className={`${baseCls} bg-surface-alt ${pctClass(pct)}`}
      title={`盘中快照（${q.source}）${q.as_of ? ` · ${q.as_of}` : ''}${open ? '' : ' · 当前休市，为最近快照'}；仅用于展示，不写入日线`}>
      实时 {fmtNum(q.price)}{pct != null ? ` ${fmtPct(pct)}` : ''}{open ? '' : '（休市）'}
    </span>
  );
}

/**
 * 行动条（K 线下方）——把「看到机会」接回「采取动作」。
 *
 * ## 为什么需要它
 * 个股页 90% 的停留时间在盯 K 线，但此前页面上**没有任何出口**把看到的机会
 * 转成动作：想加自选要滚回页头、想设预警要跳到 /alerts 重新选股、想下单要
 * 去 /desk 手输代码。任务路径 B（盘中盯盘）在这里是断的。
 *
 * ## 与页头「+ 自选」的关系
 * 页头保留原有按钮（不变），本行动条在**盯盘视线焦点处**再给一次入口——
 * 这是刻意的冗余：盯盘时手在 K 线上，不该被迫回页头。
 *
 * ## 预警：原地建规则，不跳页
 * 设预警直接调用 `POST /alerts/rules` 建一条 `price_pct`（日涨跌幅超阈值）规则，
 * 避免"跳到预警中心 → 重新搜代码 → 再填一遍"的往返。
 * 注意：**建规则需要 researcher 及以上**（后端 RBAC），低角色用户按钮禁用并说明原因，
 * 不做成"点了才报错"。
 */
function ActionBar({ symbol, name, inWatchlist, canAlert, onToggleWatchlist }: {
  symbol: string;
  name: string | null;
  inWatchlist: boolean;
  canAlert: boolean;
  onToggleWatchlist: () => void;
}) {
  const navigate = useNavigate();
  const [alertOpen, setAlertOpen] = useState(false);
  const [threshold, setThreshold] = useState('5');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const submitAlert = async () => {
    const t = Number(threshold);
    if (!Number.isFinite(t) || t <= 0) { setMsg({ ok: false, text: '阈值须为正数' }); return; }
    setBusy(true);
    setMsg(null);
    try {
      await alertsApi.createRule({
        name: `${name ?? symbol} 日涨跌幅超 ±${t}%`,
        rule_type: 'price_pct',
        scope: 'symbol',
        symbol,
        params: { threshold: t, direction: 'up' },
        channels: ['inapp'],
        cooldown_minutes: 60,
        enabled: true,
      });
      setMsg({ ok: true, text: '预警已创建' });
      setAlertOpen(false);
    } catch (e) {
      // 失败必须显式呈现，不得静默（后端 HTTP 恒 200，异常是唯一信号）
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : '创建失败，请重试' });
    } finally {
      setBusy(false);
    }
  };

  const btn = 'rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs transition-colors';

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-hair bg-surface-alt px-3 py-2">
      <button type="button" onClick={onToggleWatchlist}
        className={`${btn} ${inWatchlist
          ? 'border-transparent bg-surface-sunken text-ink-secondary hover:text-ink'
          : 'border-brand-300 text-brand-600 hover:bg-brand-50'}`}>
        {inWatchlist ? '★ 已自选' : '+ 加自选'}
      </button>

      <div className="relative">
        <button type="button"
          disabled={!canAlert}
          title={canAlert ? '按日涨跌幅设预警，无需跳转预警中心' : '设预警需要研究员及以上权限'}
          onClick={() => setAlertOpen((v) => !v)}
          className={`${btn} ${canAlert
            ? 'text-ink-secondary hover:border-brand-300 hover:text-brand-600'
            : 'cursor-not-allowed opacity-50'}`}>
          🔔 设预警
        </button>
        {alertOpen && (
          <div className="absolute left-0 top-full z-20 mt-1 w-56 rounded-md border border-hair bg-surface p-2.5 shadow-lg">
            <label className="block text-2xs text-ink-secondary">
              日涨跌幅超过（%）
              <input value={threshold} onChange={(e) => setThreshold(e.target.value)}
                inputMode="decimal"
                className="num mt-1 w-full rounded-md border border-hair px-2 py-1 text-xs outline-none focus:border-brand-300" />
            </label>
            <div className="mt-2 flex justify-end gap-1">
              <button type="button" onClick={() => setAlertOpen(false)}
                className="rounded px-2 py-1 text-2xs text-ink-muted hover:text-ink">取消</button>
              <button type="button" onClick={() => void submitAlert()} disabled={busy}
                className="rounded bg-brand-500 px-2 py-1 text-2xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
                {busy ? '创建中…' : '创建'}
              </button>
            </div>
          </div>
        )}
      </div>

      <button type="button" onClick={() => navigate('/desk')}
        className={`${btn} text-ink-secondary hover:border-brand-300 hover:text-brand-600`}>
        → 去下单
      </button>

      <span className="ml-auto text-2xs text-ink-muted">
        {msg
          ? <span className={msg.ok ? 't-success' : 't-danger'}>{msg.text}</span>
          : '盯盘快捷操作'}
      </span>
    </div>
  );
}

export default function StockDetail() {
  const params = useParams<{ symbol: string }>();
  const navigate = useNavigate();
  const symbol = params.symbol ?? '600519.SH';
  const { adjust, setAdjust, setCurrentSymbol, dateRange } = useUiStore();
  const { addTo, removeFrom, contains } = useWatchlistStore();
  const role = useAuthStore((state) => state.user?.role);
  const canPredict = hasMinimumRole(role, 'researcher');
  const inWatchlist = contains(symbol);

  const [profile, setProfile] = useState<StockProfile | null>(null);
  const [kline, setKline] = useState<KLineResult | null>(null);
  const [predict, setPredict] = useState<MLPredictResult | null>(null);
  const [panels, setPanels] = useState<StockPanels | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [activeEvent, setActiveEvent] = useState<EventItem | null>(null);
  /** 近期事件当前请求条数（滚动加载更多时按步长放大，SWR key 随之变化触发新请求） */
  const [eventLimit, setEventLimit] = useState(EVENT_PAGE_SIZE);
  const seqRef = useRef(0);

  const load = useCallback(async () => {
    // F-01/F-02：代际守卫 + 先清空旧标的展示数据。
    // 切换标的/复权/区间后，旧响应后到不得覆盖新选择，旧状态也不得驻留。
    const seq = ++seqRef.current;
    setProfile(null); setKline(null); setPredict(null); setPanels(null);
    setLoading(true); setErrors({});
    setEventLimit(EVENT_PAGE_SIZE);   // 换标的时把"加载更多"进度归零
    const { start, end } = dateRange();
    const [p, k, m, pa] = await Promise.allSettled([
      stockApi.profile(symbol),
      stockApi.kline(symbol, adjust, start, end),
      canPredict ? stockApi.predict(symbol) : Promise.resolve(null),
      stockApi.panels(symbol, EVENT_PAGE_SIZE),
    ]);
    if (seq !== seqRef.current) return;
    const nextErrors: Record<string, string> = {};
    if (p.status === 'fulfilled') setProfile(p.value);
    else nextErrors.profile = p.reason instanceof ApiError ? p.reason.message : '加载失败';
    if (k.status === 'fulfilled') setKline(k.value);
    else nextErrors.kline = k.reason instanceof ApiError ? k.reason.message : '加载失败';
    if (m.status === 'fulfilled') setPredict(m.value);
    else nextErrors.predict = m.reason instanceof ApiError ? m.reason.message : '加载失败';
    if (pa.status === 'fulfilled') setPanels(pa.value);
    else nextErrors.panels = pa.reason instanceof ApiError ? pa.reason.message : '加载失败';
    setErrors(nextErrors); setLoading(false);
  }, [symbol, adjust, dateRange, canPredict]);

  useEffect(() => { setCurrentSymbol(symbol); void load(); }, [symbol, adjust, load, setCurrentSymbol]);

  /**
   * 「加载更多」：仅重新拉取 panels（放大 event_limit），不动 K 线/预测等重块。
   * 成功后**按块合并**进现有 panels —— 避免整块 setPanels 把其它块正在展示的
   * 数据也一并置空（本页其它块由 load() 统一负责，这里只补 events）。
   */
  const loadMoreEvents = useCallback(async (nextLimit: number) => {
    if (nextLimit <= eventLimit) return;
    setEventLimit(nextLimit);
    try {
      const next = await stockApi.panels(symbol, nextLimit);
      setPanels((prev) => (prev ? { ...prev, events: next.events } : next));
    } catch {
      // 加载更多失败不弹全局错误：保留已展示的公告，用户可再次滚动重试
    }
  }, [symbol, eventLimit]);

  // Esc 关闭事件弹窗
  useEffect(() => {
    if (!activeEvent) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setActiveEvent(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeEvent]);

  const doSearch = async () => {
    if (!query.trim()) return;
    try { setHits(await stockApi.search(query.trim(), 8)); } catch { setHits([]); }
  };

  const latest = profile?.latest ?? null;
  const maxAbs = predict ? Math.max(...predict.top5_factors.map((f) => Math.abs(f.contribution)), 1e-9) : 1;
  const quote = panels?.quote;
  const cap = useMemo(
    () => ({
      total: quote?.status === 'ok' ? quote.total_cap_yi : null,
      float: quote?.status === 'ok' ? quote.float_cap_yi : null,
    }),
    [quote],
  );

  return (
    <div className="space-y-3">
      {/* ===== 头部：名称 + 搜索 + 自选 ===== */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <PageHeader
            title={
              <>
                {profile?.name ?? '—'}
                <span className="num ml-2 text-sm font-normal text-ink-secondary">{symbol}</span>
                {profile?.is_st && <span className="ml-1.5 rounded bg-danger-bg px-1.5 py-0.5 text-2xs font-medium t-up">ST</span>}
              </>
            }
          />
          {latest && (
            <div className="mt-1 flex items-baseline gap-3">
              <span className={`num text-2xl font-bold ${pctClass(latest.pct)}`}>{fmtNum(latest.close)}</span>
              <span className={`num text-sm font-medium ${pctClass(latest.pct)}`}>{fmtPct(latest.pct)}</span>
              <LiveQuoteChip symbol={symbol} />
              {predict && (
                <span className="ml-2 flex items-center gap-3 rounded-md bg-surface-alt px-3 py-1">
                  <span className="text-2xs text-ink-muted">AI预测</span>
                  <span className={`num text-sm font-semibold ${pctClass(predict.pred_return * 100)}`}>{fmtPct(predict.pred_return * 100)}</span>
                  {/* confidence 是模型级常数映射（非个股上涨概率），ⓘ 披露口径；null=不可用 */}
                  <span className="text-2xs text-ink-muted">
                    模型置信度
                    <span className="ml-0.5 cursor-help text-ink-muted"
                      title={predict.confidence_basis ?? '模型级置信度：clip(0.5 + 2×验证集RankIC, 0, 1)，同一模型下所有个股同值，非个股上涨概率'}>ⓘ</span>
                  </span>
                  <span className="num text-sm font-medium text-ink">
                    {predict.confidence != null ? `${Math.round(predict.confidence * 100)}%` : '—'}
                  </span>
                </span>
              )}
            </div>
          )}
          <div className="mt-0.5 text-xs text-ink-muted">
            {profile?.industry ?? '—'} · {profile?.market ?? '—'}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative flex">
            <input value={query} onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void doSearch()}
              placeholder="搜索代码 / 名称"
              className="w-36 rounded-l-md border border-r-0 border-hair px-2.5 py-1.5 text-xs outline-none focus:border-brand-300" />
            <button onClick={() => void doSearch()}
              className="rounded-r-md bg-surface-sunken px-2.5 text-xs text-ink-secondary hover:bg-surface-sunken">搜索</button>
            {hits.length > 0 && (
              <ul className="absolute z-20 mt-1 w-52 rounded-md border border-hair bg-surface py-1 shadow-lg">
                {hits.map((h) => (
                  <li key={h.symbol}>
                    <button className="flex w-full items-center justify-between px-3 py-1.5 text-xs hover:bg-surface-alt"
                      onClick={() => { setHits([]); setQuery(''); navigate(`/stock/${h.symbol}`); }}>
                      <span className="num text-ink-muted">{h.code}</span><span>{h.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button onClick={() => (inWatchlist ? removeFrom(DEFAULT_GROUP, symbol) : addTo(DEFAULT_GROUP, symbol))}
            className={`rounded-md px-2.5 py-1.5 text-xs transition-colors ${
              inWatchlist ? 'bg-danger-bg t-up hover:bg-danger-bg' : 'bg-brand-500 text-white hover:bg-brand-600'}`}>
            {inWatchlist ? '移出自选' : '+ 自选'}
          </button>
        </div>
      </div>

      {loading && <LoadingState />}
      {Object.entries(errors).map(([k, msg]) => (
        <div key={k} className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
          {{ kline: 'K 线', predict: 'ML 预测', profile: '档案', panels: '研究面板' }[k] ?? k}：{msg}
        </div>
      ))}

      {/* ===== 主区 ===== */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* min-w-0：允许图表列收缩，防止画布把网格轨道撑破导致与右栏错位 */}
        <div className="min-w-0 space-y-3 lg:col-span-2">
          <KLineChart bars={kline?.bars ?? []} adjust={adjust}
            onAdjustChange={setAdjust} height={520} />
          {/* 行动条紧贴 K 线下方：盯盘视线焦点处即可完成「加自选 / 设预警 / 去下单」 */}
          <ActionBar
            symbol={symbol}
            name={profile?.name ?? null}
            inWatchlist={inWatchlist}
            canAlert={canPredict}
            onToggleWatchlist={() => (inWatchlist
              ? removeFrom(DEFAULT_GROUP, symbol)
              : addTo(DEFAULT_GROUP, symbol))}
          />
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          {/* 行情：8 个紧凑指标卡（新增总市值 / 流通市值） */}
          <div className="grid shrink-0 grid-cols-4 gap-2">
            <MetricCard compact label="开盘价" value={fmtNum(latest?.open)} />
            <MetricCard compact label="最高价" value={fmtNum(latest?.high)} />
            <MetricCard compact label="最低价" value={fmtNum(latest?.low)} />
            <MetricCard compact label="收盘价" value={fmtNum(latest?.close)} />
            <MetricCard compact label="成交量" value={fmtNum(latest?.volume, 0)} />
            <MetricCard compact label="成交额" value={fmtAmountYi(latest?.amount != null ? latest.amount / 1e8 : null)} />
            <MetricCard compact label="总市值" value={fmtAmountYi(cap.total)} />
            <MetricCard compact label="流通市值" value={fmtAmountYi(cap.float)} />
          </div>

          {/* AI 预测：压缩内部留白，因子列表顶部对齐、脚注底部吸附 */}
          <SectionCard title="AI 预测" className="flex-1"
            bodyClassName="flex flex-col px-3 py-2.5">
            {predict ? (
              <>
                <div className="flex shrink-0 items-end justify-between">
                  <div>
                    <div className="text-2xs text-ink-secondary">未来 {predict.horizon} 日预期收益</div>
                    <div className={`num text-lg font-bold leading-tight ${pctClass(predict.pred_return * 100)}`}>
                      {fmtPct(predict.pred_return * 100)}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-0.5">
                    <span className="text-2xs text-ink-muted">
                      模型置信度
                      <span className="ml-0.5 cursor-help text-ink-muted"
                        title={predict.confidence_basis ?? '模型级置信度：clip(0.5 + 2×验证集RankIC, 0, 1)，同一模型下所有个股同值，非个股上涨概率'}>ⓘ</span>
                    </span>
                    <ProbabilityBar value={predict.confidence} />
                  </div>
                </div>
                <div className="mt-2 flex flex-col gap-0.5">
                  <div className="text-2xs text-ink-muted">Top5 因子贡献 (TreeSHAP)</div>
                  {predict.top5_factors.map((f) => (
                    <FactorBar key={f.feature} name={factorName(f.feature)} value={f.contribution} maxAbs={maxAbs} />
                  ))}
                </div>
                {/* mt-auto：多余的剩余高度集中到因子列表与脚注之间，避免整块中间空一大片 */}
                <p className="num mt-auto shrink-0 border-t border-hair pt-1.5 text-2xs text-ink-muted">
                  {predict.model_version} · {predict.date} · 不构成投资建议
                </p>
              </>
            ) : canPredict ? (
              <PanelEmpty minH="min-h-[96px]" />
            ) : (
              <div className="flex min-h-[96px] items-center justify-center rounded-md bg-surface-alt px-3 text-center text-xs text-ink-muted">
                AI 预测需要研究员及以上角色；基础行情与研究面板仍可正常查看。
              </div>
            )}
          </SectionCard>

          {/* 技术指标（自研究区移入右栏，紧随 AI 预测下方） */}
          <SectionCard title="技术指标" bodyClassName="px-3 py-2.5">
            {kline?.bars?.length ? (
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                {(() => {
                  const last = kline.bars[kline.bars.length - 1];
                  const n = (v: number | null | undefined, d = 2) => (v != null ? v.toFixed(d) : '—');
                  return [
                    ['RSI(14)', n(last.rsi_14, 1)],
                    ['MACD DIF', n(last.macd_dif, 4)],
                    ['MACD DEA', n(last.macd_dea, 4)],
                    ['MACD BAR', n(last.macd_bar, 4)],
                    ['BOLL 上轨', last.boll_up != null ? fmtNum(last.boll_up) : '—'],
                    ['BOLL 中轨', last.boll_mid != null ? fmtNum(last.boll_mid) : '—'],
                    ['BOLL 下轨', last.boll_low != null ? fmtNum(last.boll_low) : '—'],
                    ['MA5', last.ma_5 != null ? fmtNum(last.ma_5) : '—'],
                    ['MA20', last.ma_20 != null ? fmtNum(last.ma_20) : '—'],
                    ['MA60', last.ma_60 != null ? fmtNum(last.ma_60) : '—'],
                  ].map(([k, v]) => <StatRow key={k} label={k} value={v} />);
                })()}
              </div>
            ) : (
              <PanelEmpty text={errors.kline ?? '暂无数据'} minH="min-h-[88px]" />
            )}
          </SectionCard>
        </div>
      </div>

      {/* ===== 3×2 研究模块 ===== */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <MoneyFlowPanel money={panels?.money_flow} north={panels?.north} quote={panels?.quote} />
        <EventsPanel
          symbol={symbol}
          block={panels?.events}
          requestedLimit={eventLimit}
          onOpen={setActiveEvent}
          onNeedMore={(n) => void loadMoreEvents(n)}
        />
        <RiskPanel block={panels?.risk} />

        <ChipPanel block={panels?.chip} />
        <HoldersPanel block={panels?.holders} />
        <FundamentalsPanel block={panels?.fundamentals} profile={profile} />
      </div>

      {/* ===== 事件详情弹窗 ===== */}
      <Modal
        open={activeEvent != null}
        onClose={() => setActiveEvent(null)}
        title={activeEvent?.title ?? ''}
        sub={activeEvent ? `${activeEvent.date ?? '—'} · ${activeEvent.event_type ?? '公告'}` : undefined}
        footer={activeEvent?.url ? (
          <a href={activeEvent.url} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-md bg-brand-500 px-3 py-1.5 text-xs text-white hover:bg-brand-600">
            查看原文
            <svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M7 4h9v9M16 4L5 15" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </a>
        ) : (
          <span className="text-2xs text-ink-muted">该来源未提供原文链接</span>
        )}
      >
        <p className="text-ink-secondary">
          数据源：{activeEvent?.source === 'cninfo' ? '巨潮资讯' : activeEvent?.source === 'sina' ? '新浪财经' : '—'}
          {activeEvent?.sentiment ? ` · 情绪标签：${SENTIMENT_TEXT[activeEvent.sentiment]}` : ''}
        </p>
        <p className="mt-2 text-ink-muted">
          公告正文未随列表接口返回，完整内容请点击下方「查看原文」跳转至信息披露页面。
        </p>
      </Modal>
    </div>
  );
}

const SENTIMENT_TEXT: Record<string, string> = {
  positive: '偏正面', negative: '偏负面', neutral: '中性',
};

// ==================== 资金流向 ====================
function MoneyFlowPanel({ money, north, quote }: {
  money?: MoneyFlowBlock; north?: NorthBlock; quote?: QuoteBlock;
}) {
  const mainOk = money?.status === 'ok' && money.main_net_yi != null;
  const northOk = north?.status === 'ok' && north.pct_of_float != null;
  const turnoverOk = quote?.status === 'ok' && quote.turnover != null;
  const splitOk = quote?.status === 'ok' && quote.outer_ratio != null;
  const anyOk = mainOk || northOk || turnoverOk || splitOk;

  const mainNet = money?.main_net_yi ?? null;
  const mainRatio = money?.main_net_ratio ?? null;
  const inflow = (mainNet ?? 0) >= 0;

  return (
    <SectionCard title="资金流向" bodyClassName="px-3 py-2.5">
      {anyOk ? (
        <div className="space-y-2">
          {mainOk ? (
            <RatioBar
              label="主力净流入"
              fill={Math.min(100, Math.abs(mainRatio ?? 0) / FULL_SCALE_PCT * 100)}
              value={`${mainNet != null && mainNet >= 0 ? '+' : ''}${fmtNum(mainNet)}亿`}
              tone={inflow ? 'bg-up' : 'bg-down'}
              hint={mainRatio != null ? `占成交额 ${fmtPct(mainRatio)}` : undefined}
            />
          ) : (
            <RowPlaceholder label="主力净流入" />
          )}

          {northOk ? (
            <RatioBar
              label="北向持股"
              fill={Math.min(100, (north.pct_of_float ?? 0) / FULL_SCALE_PCT * 100)}
              value={`${fmtNum(north.pct_of_float)}%`}
              tone="bg-brand-500"
              hint={north.hold_cap_yi != null ? `持股市值 ${fmtAmountYi(north.hold_cap_yi)}` : undefined}
            />
          ) : (
            <RowPlaceholder label="北向持股" />
          )}

          {turnoverOk ? (
            <RatioBar
              label="换手率"
              fill={Math.min(100, (quote.turnover ?? 0) / FULL_SCALE_PCT * 100)}
              value={fmtPct(quote.turnover)}
              tone="bg-brand-300"
            />
          ) : (
            <RowPlaceholder label="换手率" />
          )}

          {splitOk ? (
            <SplitBar
              label="内外盘"
              left={quote.outer_ratio}
              right={quote.inner_ratio}
              leftLabel={`外盘 ${fmtNum(quote.outer_ratio)}%`}
              rightLabel={`内盘 ${fmtNum(quote.inner_ratio)}%`}
            />
          ) : (
            <RowPlaceholder label="内外盘" />
          )}

          <p className="num border-t border-hair pt-1.5 text-2xs text-ink-muted">
            {money?.date ? `资金流 ${money.date}` : '资金流 —'}
            {north?.date ? ` · 北向 ${north.date}` : ''}
          </p>
        </div>
      ) : (
        <PanelEmpty minH="min-h-[120px]" />
      )}
    </SectionCard>
  );
}

/** 单行指标不可用时的占位（保持与 RatioBar 相同的高度与左对齐） */
function RowPlaceholder({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 shrink-0 truncate text-ink-secondary">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-sm bg-surface-sunken" />
      <span className="num w-24 shrink-0 text-right text-ink-muted">—</span>
    </div>
  );
}

// ==================== 近期事件 ====================
/**
 * 近期事件列表（滚动加载更多）。
 *
 * 展示口径（2026-09-30 变更）：后端按 ``pub_date`` **倒序取最近 N 条**、
 * **不限定时间窗口** —— 避免"近 1 月无公告"的标的（如茅台近期无披露）列表为空。
 *
 * 加载策略（两级）：
 *  1. **懒渲染**：首屏 / 首批只渲染 ``visible`` 条；滚到底时先就地展开已取到的
 *     数据（``visible += PAGE_SIZE``），不产生网络请求；
 *  2. **增量拉取**：当 ``visible`` 追平已取条数、且后端可能还有更多
 *     （``items.length >= requestedLimit``）时，按 ``FETCH_STEP`` 放大
 *     ``requestedLimit`` 触发一次新请求（SWR key 含 limit，故各档独立缓存）。
 *
 * 状态可见性纪律（沿用本页既有约定）：加载中 / 已到底 / 无数据都必须显式可见，
 * 不得静默截断 —— 否则用户会以为"公告只有这些"。
 */
/**
 * 近期事件（研究区摘要卡 + 独立抽屉）。
 *
 * ## 为什么从"常驻面板"改为"摘要 + 抽屉"
 * 公告/事件此前是 3×2 研究网格里的一个常驻格，内部自带 `max-h-[260px]` 滚动区
 * 与增量加载（`EVENT_PAGE_SIZE=15` / `EVENT_FETCH_MAX=200`）——它**已经是重度独立功能**，
 * 却和「风险度量」「筹码分布」等轻量卡片平分一格，导致 6 格同权、首屏需滚动。
 *
 * 现在：网格内只留**近 3 条摘要**（保持 6 格布局不变，视觉零破坏），
 * 完整列表移入右侧抽屉，点「查看全部」展开。
 *
 * ## 红线遵守（重要）
 * 降级态（`status === 'unavailable'`）**仍在摘要卡内原位可见**（`PanelEmpty`），
 * 不因"内容为空"就隐藏入口——否则用户会以为"这只票没有公告"，
 * 而实际是"公告数据取不到"。两者必须可区分。
 */
function EventsPanel({ symbol, block, requestedLimit, onOpen, onNeedMore }: {
  symbol: string;
  block?: EventsBlock;
  requestedLimit: number;
  onOpen: (e: EventItem) => void;
  onNeedMore: (limit: number) => void;
}) {
  const items = block?.status === 'unavailable' ? [] : (block?.items ?? []);
  const [visible, setVisible] = useState(EVENT_PAGE_SIZE);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);

  // 切换标的/数据变化时重置展开进度，避免上一个标的的展开量串到新标的
  useEffect(() => { setVisible(EVENT_PAGE_SIZE); }, [symbol, block?.status]);
  // 切换标的时关闭抽屉，避免残留上一个标的的列表
  useEffect(() => { setDrawerOpen(false); }, [symbol]);

  const shown = items.slice(0, visible);
  const hasMoreLocal = visible < items.length;
  // 后端"可能还有更多"的判据：本次返回条数已达到请求上限（说明被截断）
  const hasMoreRemote = items.length > 0 && items.length >= requestedLimit
    && requestedLimit < EVENT_FETCH_MAX;
  const hasMore = hasMoreLocal || hasMoreRemote;

  const loadMore = useCallback(() => {
    if (!hasMore) return;
    if (visible < items.length) {
      setVisible((v) => v + EVENT_PAGE_SIZE);
      return;
    }
    onNeedMore(Math.min(requestedLimit + EVENT_FETCH_STEP, EVENT_FETCH_MAX));
  }, [hasMore, visible, items.length, requestedLimit, onNeedMore]);

  // 滚动到底部附近（64px）自动加载更多
  const onScroll = useCallback((e: React.UIEvent<HTMLUListElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 64) loadMore();
  }, [loadMore]);

  // ---- 降级 / 无数据：摘要卡原位显示，入口不隐藏 ----
  if (block?.status === 'unavailable' || !block) {
    return (
      <SectionCard title="近期事件" bodyClassName="px-3 py-2.5">
        <PanelEmpty minH="min-h-[120px]" />
      </SectionCard>
    );
  }
  if (items.length === 0) {
    return (
      <SectionCard title="近期事件" bodyClassName="px-3 py-2.5">
        <PanelEmpty minH="min-h-[120px]" />
      </SectionCard>
    );
  }

  const SUMMARY_N = 3;
  const summary = items.slice(0, SUMMARY_N);

  /** 单条事件行（摘要卡与抽屉共用，避免两套渲染漂移） */
  const row = (it: EventItem, i: number) => (
    <li key={`${it.date ?? ''}-${it.title}-${i}`}>
      <div className="group flex items-center gap-2 rounded px-1 py-1 hover:bg-surface-alt">
        <button
          onClick={() => onOpen(it)}
          title={it.title}
          className="min-w-0 flex-1 truncate text-left text-xs text-ink hover:text-brand-600 hover:underline"
        >
          {it.title}
        </button>
        {it.url && (
          <a href={it.url} target="_blank" rel="noreferrer" title="跳转原文"
            onClick={(e) => e.stopPropagation()}
            className="shrink-0 rounded p-0.5 text-ink-muted opacity-0 transition-opacity hover:text-brand-600 group-hover:opacity-100">
            <svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M7 4h9v9M16 4L5 15" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </a>
        )}
        <span className="num w-16 shrink-0 text-right text-2xs text-ink-muted">{it.date ?? '—'}</span>
      </div>
    </li>
  );

  return (
    <>
      {/* 摘要卡：网格内只占一格，近 3 条 + 查看全部入口 */}
      <SectionCard
        title="近期事件"
        bodyClassName="px-3 py-2.5"
        action={
          <button type="button" onClick={() => setDrawerOpen(true)}
            className="rounded px-1.5 py-0.5 text-2xs text-brand-600 transition-colors hover:bg-brand-50">
            查看全部 {items.length} 条 →
          </button>
        }
      >
        <ul className="space-y-1">
          {summary.map((it, i) => row(it, i))}
        </ul>
        {items.length > SUMMARY_N && (
          <button type="button" onClick={() => setDrawerOpen(true)}
            className="mt-1.5 w-full rounded border border-dashed border-hair py-1 text-2xs text-ink-muted transition-colors hover:border-brand-300 hover:text-brand-600">
            还有 {items.length - SUMMARY_N} 条 · 展开完整列表
          </button>
        )}
      </SectionCard>

      {/* 完整列表抽屉 */}
      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="近期事件"
        sub={`${symbol} · 共 ${items.length} 条${hasMore ? '（可继续加载）' : ''}`}
      >
        <ul ref={listRef} onScroll={onScroll} data-testid="events-scroll"
          className="space-y-1 overflow-y-auto pr-0.5" style={{ maxHeight: 'calc(100vh - 200px)' }}>
          {shown.map((it, i) => row(it, i))}
          {/* 列表尾部状态行：加载中 / 已到底，显式可见（不静默截断） */}
          <li aria-hidden={!hasMore}>
            <div className="px-1 py-1 text-center text-2xs text-ink-muted">
              {hasMore ? '下拉加载更多…' : '已显示全部'}
            </div>
          </li>
        </ul>
      </Drawer>
    </>
  );
}

// ==================== 风险度量 ====================
function RiskPanel({ block }: { block?: RiskBlock }) {
  if (!block || block.status === 'unavailable') {
    return (
      <SectionCard title="风险度量" bodyClassName="px-3 py-2.5">
        <PanelEmpty minH="min-h-[120px]" />
      </SectionCard>
    );
  }
  const r = block;
  return (
    <SectionCard title="风险度量" bodyClassName="px-3 py-2.5">
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
        <StatRow label="年化波动率" value={r.annual_vol != null ? `${(r.annual_vol * 100).toFixed(2)}%` : '—'} />
        {/* ⚠️ 回撤恒为负 ⇒ 常量色 t-down 在**有值**时是对的，但 max_drawdown 为 null 时值显 '—'
            却仍染绿，等于替"未知"表态。null ⇒ 中性（与下一行 sharpe 的判空写法保持一致）。 */}
        <StatRow label="最大回撤" value={r.max_drawdown != null ? `${(r.max_drawdown * 100).toFixed(2)}%` : '—'}
          tone={r.max_drawdown == null ? undefined : 't-down'} />
        <StatRow label="夏普比率" value={r.sharpe != null ? r.sharpe.toFixed(2) : '—'}
          tone={r.sharpe == null ? undefined : r.sharpe >= 0 ? 't-up' : 't-down'} />
        <StatRow label={`Beta${r.benchmark ? `(${r.benchmark})` : ''}`} value={r.beta != null ? r.beta.toFixed(2) : '—'} />
      </div>
      <div className="mt-2 border-t border-hair pt-2">
        <RatioBar
          label="波动率分位"
          fill={r.vol_percentile ?? 0}
          value={r.vol_percentile != null ? `${r.vol_percentile.toFixed(1)}%` : '—'}
          // ⚠️ vol_percentile 可为 null（上方已显示 '—'）：不得用 `(x ?? 0)` 兜底 ——
          // null 会被判成 `0 <= 20` 染绿，把"缺失"说成"低波动"。null ⇒ 中性（bg-brand-500）。
          tone={r.vol_percentile == null ? 'bg-brand-500'
            : r.vol_percentile >= 80 ? 'bg-up'
              : r.vol_percentile <= 20 ? 'bg-down' : 'bg-brand-500'}
          hint={r.vol_short != null ? `近 20 日年化波动率 ${(r.vol_short * 100).toFixed(2)}%` : undefined}
        />
      </div>
      <p className="num mt-2 text-2xs text-ink-muted">
        {r.note ?? `近 ${r.window} 个交易日口径`}
        {/* 审计 B2-16：夏普的 rf 口径必须可见（个股页与组合页统一为年化 2%） */}
        {r.rf_annual != null && `；夏普按年化无风险利率 ${(r.rf_annual * 100).toFixed(2)}% 计算`}
      </p>
    </SectionCard>
  );
}

// ==================== 筹码分布 ====================
function ChipPanel({ block }: { block?: ChipBlock }) {
  if (!block || block.status === 'unavailable') {
    return (
      <SectionCard title="筹码分布" bodyClassName="px-3 py-2.5">
        <PanelEmpty minH="min-h-[160px]" />
      </SectionCard>
    );
  }
  const c = block;
  // ⚠️ 现价可为 null：不得用 `?? 0` 兜底 —— 兜成 0 时真实价格恒 > 0，曲线每一格都会落进
  // `p.price <= cur` 的假分支 ⇒ 整条画成全绿 = 把"现价未知"说成"100% 套牢"。
  // 故保留 null，由各消费点显式判空（沿用本函数平均成本卡的既有先例）。
  return (
    <SectionCard title="筹码分布" bodyClassName="px-3 py-2.5">
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
        <StatRow label="平均成本" value={fmtNum(c.avg_cost)}
          tone={c.avg_cost != null && c.current_price != null && c.current_price > 0
            ? (c.avg_cost <= c.current_price ? 't-down' : 't-up') : undefined} />
        <StatRow label="现价" value={fmtNum(c.current_price)} />
        <StatRow label="90% 区间" value={(c.p5 != null && c.p95 != null) ? `${fmtNum(c.p5)} ~ ${fmtNum(c.p95)}` : '—'} />
        <StatRow label="筹码集中度" value={c.concentration != null ? `${(c.concentration * 100).toFixed(2)}%` : '—'} />
      </div>

      <div className="mt-2 border-t border-hair pt-2">
        <SplitBar
          label="获利 / 套牢"
          left={c.profit_ratio != null ? c.profit_ratio * 100 : null}
          right={c.trapped_ratio != null ? c.trapped_ratio * 100 : null}
          leftLabel={`获利 ${c.profit_ratio != null ? (c.profit_ratio * 100).toFixed(1) : '—'}%`}
          rightLabel={`套牢 ${c.trapped_ratio != null ? (c.trapped_ratio * 100).toFixed(1) : '—'}%`}
        />
      </div>

      {/* 筹码密度曲线：以现价为界，下方为获利盘（红）、上方为套牢盘（绿） */}
      {c.curve.length > 0 && (
        <div className="mt-2 flex h-12 items-end gap-px" title="筹码密度分布">
          {c.curve.map((p, i) => (
            <div key={i}
              className={`flex-1 rounded-t-sm ${c.current_price == null || c.current_price <= 0 ? 'bg-hair2'
                : p.price <= c.current_price ? 'bg-up/50' : 'bg-down/50'}`}
              style={{ height: `${Math.max(3, p.pct * 100)}%` }} />
          ))}
        </div>
      )}

      <p className="num mt-2 text-2xs text-ink-muted">
        {c.as_of ? `${c.as_of} · ` : ''}回看 {c.lookback} 日 · {c.note ?? '日频换手衰减近似模型'}
      </p>
    </SectionCard>
  );
}

// ==================== 基本面 ====================

type FundKey = 'pe_ttm' | 'pb' | 'roe' | 'gross_margin' | 'net_margin';

interface FundMetric {
  key: FundKey;
  label: string;
  /** 进度条满格值，超过即满格 */
  scale: number;
  unit: '' | '%';
  /** 数值方向的语义：high = 越高越好（配红/绿），low = 估值类，只用中性色 */
  better: 'high' | 'low';
}

/**
 * 估值类（PE / PB）满格刻度取 A 股常见上沿；盈利类（ROE / 毛利率 / 净利率）
 * 取"优秀线"作为满格，便于一眼看出距离优秀还有多远。
 */
const FUND_METRICS: readonly FundMetric[] = [
  { key: 'pe_ttm', label: 'PE (TTM)', scale: 60, unit: '', better: 'low' },
  { key: 'pb', label: 'PB', scale: 10, unit: '', better: 'low' },
  { key: 'roe', label: 'ROE', scale: 30, unit: '%', better: 'high' },
  { key: 'gross_margin', label: '毛利率', scale: 100, unit: '%', better: 'high' },
  { key: 'net_margin', label: '净利率', scale: 60, unit: '%', better: 'high' },
];

type FundView = 'bar' | 'value';

const FUND_VIEW_OPTIONS = [
  { key: 'bar' as const, label: '进度条' },
  { key: 'value' as const, label: '数值' },
];

/** 单个基本面指标的展示文本：亏损 PE 显「亏损」，缺失显「—」 */
function fundValueText(m: FundMetric, v: number | null): string {
  if (v == null || !Number.isFinite(v)) return '—';
  if (m.key === 'pe_ttm' && v < 0) return '亏损';
  return `${v.toFixed(2)}${m.unit}`;
}

/** 进度条填充比例：0~100；亏损或缺失为 0 */
function fundFill(m: FundMetric, v: number | null): number {
  if (v == null || !Number.isFinite(v)) return 0;
  const base = m.better === 'high' ? Math.abs(v) : Math.max(v, 0);
  return Math.min(100, (base / m.scale) * 100);
}

function fundTone(m: FundMetric, v: number | null): string {
  if (m.better === 'low') return 'bg-brand-500';
  // ⚠️ v 可为 null（基本面指标缺失，值文本已显示 '—'、填充为 0）：不得用 `(v ?? 0) >= 0`
  // 兜底 —— 那会把"缺失"判成非负而选 bg-up（伪方向）。null ⇒ 中性（bg-brand-500）。
  if (v == null) return 'bg-brand-500';
  return v >= 0 ? 'bg-up' : 'bg-down';
}

function FundamentalsPanel({ block, profile }: {
  block?: FundamentalsBlock; profile?: StockProfile | null;
}) {
  const [view, setView] = useState<FundView>('bar');
  const b = block;
  const ok = !!b && b.status !== 'unavailable';

  return (
    <SectionCard
      title="基本面"
      action={ok ? <ViewToggle value={view} onChange={setView} options={FUND_VIEW_OPTIONS}
        title="切换估值 / 盈利指标的展示方式" /> : undefined}
      bodyClassName="px-3 py-2.5"
    >
      {ok && b ? (
        <div className="space-y-2">
          {view === 'bar' ? (
            // 进度条模式：一眼看出相对量级（满格刻度见 FUND_METRICS）
            <div className="space-y-2">
              {FUND_METRICS.map((m) => {
                const v = b[m.key];
                return (
                  <RatioBar
                    key={m.key}
                    label={m.label}
                    fill={fundFill(m, v)}
                    value={fundValueText(m, v)}
                    tone={fundTone(m, v)}
                    hint={v == null ? '该指标不适用或数据源缺失' : `满格刻度 ${m.scale}${m.unit}`}
                  />
                );
              })}
            </div>
          ) : (
            // 纯数值模式：与「技术指标」一致的 key-value 两列列表
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              {FUND_METRICS.map((m) => {
                const v = b[m.key];
                return (
                  <StatRow
                    key={m.key}
                    label={m.label}
                    value={fundValueText(m, v)}
                    tone={v == null ? 'text-ink-muted'
                      : m.better === 'low' ? undefined
                        : v >= 0 ? 't-up' : 't-down'}
                  />
                );
              })}
            </div>
          )}

          {/* 公司概况：与估值/盈利区以分隔线分区，避免 11 项挤在一起 */}
          <div className="border-t border-hair pt-2">
            <div className="mb-0.5 text-2xs text-ink-muted">公司概况</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <StatRow label="代码" value={profile?.code ?? '—'} />
              <StatRow label="市场" value={profile?.market ?? '—'} />
              <StatRow label="行业" value={profile?.industry ?? '—'} />
              <StatRow label="地区" value={profile?.area ?? '—'} />
              <StatRow label="上市日期" value={profile?.list_date ?? '—'} />
              <StatRow label="类型" value={profile?.type ?? '—'} />
            </div>
          </div>

          <p className="num border-t border-hair pt-1.5 text-2xs text-ink-muted">
            {b.report_date ? `报告期 ${b.report_date}` : '报告期 —'}
            {b.revenue_yi != null ? ` · 营收 ${fmtAmountYi(b.revenue_yi)}` : ''}
            {b.net_profit_yi != null ? ` · 归母净利 ${fmtAmountYi(b.net_profit_yi)}` : ''}
          </p>
          {/* 仅在"财报已加载但毛利率缺失"时提示，避免与整块降级混淆 */}
          {b.gross_margin == null && b.roe != null && b.note && (
            <p className="text-2xs text-ink-muted">{b.note}</p>
          )}
        </div>
      ) : (
        <PanelEmpty minH="min-h-[140px]" />
      )}
    </SectionCard>
  );
}

// ==================== 股东信息 ====================
function HoldersPanel({ block }: { block?: HoldersBlock }) {
  if (!block || block.status === 'unavailable') {
    return (
      <SectionCard title="股东信息" bodyClassName="px-3 py-2.5">
        <PanelEmpty minH="min-h-[160px]" />
      </SectionCard>
    );
  }
  const hn = block.holder_num;
  const top10 = block.top10 ?? [];
  return (
    <SectionCard title="股东信息" bodyClassName="px-3 py-2.5">
      {hn && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
          <StatRow label="股东户数" value={hn.holder_num != null ? hn.holder_num.toLocaleString('zh-CN') : '—'} />
          <StatRow label="较上期" value={fmtPct(hn.change_ratio)}
            tone={hn.change_ratio == null ? undefined : hn.change_ratio > 0 ? 't-up' : 't-down'} />
          <StatRow label="户均持股" value={hn.avg_hold_num != null ? `${Math.round(hn.avg_hold_num).toLocaleString('zh-CN')} 股` : '—'} />
          <StatRow label="户均市值" value={hn.avg_market_cap != null ? `${(hn.avg_market_cap / 1e4).toFixed(1)} 万` : '—'} />
        </div>
      )}

      {top10.length > 0 && (
        <div className="mt-2 border-t border-hair pt-2">
          <div className="mb-1 flex items-center justify-between text-2xs text-ink-muted">
            <span>十大流通股东</span>
            <span className="num">{block.end_date ?? ''}</span>
          </div>
          <ul className="space-y-0.5">
            {top10.slice(0, 5).map((t, i) => (
              <li key={i} className="flex items-center gap-2 text-xs">
                <span className="num w-3 shrink-0 text-ink-muted">{t.rank ?? i + 1}</span>
                <span className="min-w-0 flex-1 truncate text-ink-secondary" title={t.name ?? ''}>{t.name ?? '—'}</span>
                <span className="num w-14 shrink-0 text-right text-ink">
                  {t.pct_of_float != null ? `${t.pct_of_float.toFixed(2)}%` : '—'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!hn && top10.length === 0 && <PanelEmpty minH="min-h-[120px]" />}
    </SectionCard>
  );
}
