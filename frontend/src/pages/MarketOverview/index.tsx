/**
 * 市场概览（组件化重构，对照设计稿）：
 *   顶部 5 列 KPI 卡（指数/成交额/资金/AI 准确率 + 迷你走势）
 *   Row1 左 市场涨跌分布（方块密度网格 + 环形图） | 右 资金流向（双向堆叠柱 + 榜单）
 *   Row2 左 热门板块排行 | 右 AI 预测精选 + 情绪仪表盘
 *
 * 数据流（L2-2 拆分 + L4 SWR）：rt/daily 两个独立 useApi —— 页面二次切换
 * 缓存命中 <100ms，盘中轮询由 refreshInterval 接管（轻刷新只作用于实时块，
 * refresh=1 经 mutate 手动触发）；交易日下拉切换只重拉日频块（推荐榜历史快照）；
 * 子组件消费合并视图（{...daily, ...rt}），接口不变。
 */
import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSWRConfig } from 'swr';
import { ApiError } from '@/api/client';
import { marketApi, OVERVIEW_TIMEOUT } from '@/api/market';
import { REFRESH, useApi } from '@/api/swr';
import DataFreshness from '@/components/DataFreshness';
import { PageHeader } from '@/components/ui';
import { isMarketOpen } from '@/hooks/useMarketSession';
import type { AhSpreadResult, GlobalIndicesBlock, MarketOverviewData, OverviewDaily, OverviewRt } from '@/types/stock';
import KpiCards from './KpiCards';
import GlobalIndices from './GlobalIndices';
import BreadthPanel from './BreadthPanel';
import MoneyFlowPanel from './MoneyFlowPanel';
import HotSectorsPanel from './HotSectorsPanel';
import AiPicksPanel from './AiPicksPanel';
import StyleFactorsPanel from './StyleFactorsPanel';
import ValuationErpPanel from './ValuationErpPanel';
import SpeculationPanel from './SpeculationPanel';
import AhSpreadPanel from './AhSpreadPanel';
import AbnormalMovesCard from './AbnormalMovesCard';
import LimitLadderCard from './LimitLadderCard';

/* 盘中判定已抽到共享 hook（此前与 StockDetail 各存一份副本） */

function overviewErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return '行情数据加载失败，请稍后重试';
}

export type MarketSectionTab = 'all' | 'core' | 'tactical' | 'factors' | 'picks';

interface SectionHeaderProps {
  icon: string;
  title: string;
  subtitle: string;
  tag?: string;
}

function SectionTitle({ icon, title, subtitle, tag }: SectionHeaderProps) {
  return (
    <div className="flex items-center justify-between border-b border-hair/60 pb-1 pt-1.5">
      <div className="flex items-center gap-1.5">
        <span className="flex h-4.5 w-4.5 items-center justify-center rounded bg-surface-sunken text-xs font-semibold text-brand-600 shadow-2xs">
          {icon}
        </span>
        <h2 className="text-xs font-semibold text-ink tracking-tight">{title}</h2>
        <span className="hidden text-3xs text-ink-muted sm:inline">{subtitle}</span>
      </div>
      {tag && (
        <span className="rounded bg-brand-50 px-1.5 py-0.5 text-3xs font-medium text-brand-600 dark:bg-brand-950/40 dark:text-brand-400">
          {tag}
        </span>
      )}
    </div>
  );
}

const TABS: { id: MarketSectionTab; label: string; icon: string }[] = [
  { id: 'all', label: '全景总览', icon: '🌟' },
  { id: 'core', label: '核心大盘与生态', icon: '📊' },
  { id: 'tactical', label: '连板天梯与异动', icon: '⚡' },
  { id: 'factors', label: '风格因子与资金', icon: '📈' },
  { id: 'picks', label: '跨市场与精选', icon: '🌐' },
];

export default function MarketOverview() {
  const [activeTab, setActiveTab] = useState<MarketSectionTab>('all');
  const [predDate, setPredDate] = useState<string>(''); // '' = 最新
  const { mutate } = useSWRConfig();
  const navigate = useNavigate();

  // 实时块：SWR 轮询接管（盘中 30s / 非盘中不轮询——key 携带盘中态，切换即重取）
  // 冷算实测 22.9s（QA 探针）> client 默认 15s，故显式放宽到 OVERVIEW_TIMEOUT。
  const open = isMarketOpen();
  const rt = useApi<OverviewRt>('/api/v1/market/overview/rt',
    open ? { _t: 'live' } : { _t: 'off' },
    { refreshInterval: open ? REFRESH.realtime * 1000 : 0 },
    OVERVIEW_TIMEOUT);

  // 日频块：TTL 至次日盘后（后端长缓存），前端不轮询；日期切换换 key
  // 冷算实测 25.3s > client 默认 15s，同样显式放宽到 OVERVIEW_TIMEOUT。
  const daily = useApi<OverviewDaily>('/api/v1/market/overview/daily',
    { recommend_k: 50, ...(predDate ? { date: predDate } : {}) },
    { refreshInterval: 0 },
    OVERVIEW_TIMEOUT);

  // 外盘块（独立端点）：日频、日内不变，前端不轮询；与 rt/daily 一样显式放宽超时。
  const global = useApi<GlobalIndicesBlock>('/api/v1/market/overview/global',
    { _t: 'daily' },
    { refreshInterval: 0 },
    OVERVIEW_TIMEOUT);

  // AH 股溢价与跨市场套利监控（港股通扩展）：独立端点，TTL 30s
  const ahSpread = useApi<AhSpreadResult>('/api/v1/market/ah-spread',
    { _t: open ? 'live' : 'off' },
    { refreshInterval: open ? REFRESH.realtime * 1000 : 0 },
    OVERVIEW_TIMEOUT);

  const loading = !rt.data && !daily.data && (rt.isLoading || daily.isLoading);
  const rtData = rt.data;

  // 错误态：rt/daily 双失败且无任何数据可展示时，给用户可见提示与重试入口
  // （此前只显示 "—" 和空图，用户无法区分"加载中"与"已失败"）。
  const bothFailed = !rt.data && !daily.data && !loading && Boolean(rt.error || daily.error);
  const errorMessage = bothFailed ? overviewErrorMessage(rt.error ?? daily.error) : null;
  const retryAll = useCallback(() => {
    void rt.mutate();
    void daily.mutate();
    void global.mutate();
    void ahSpread.mutate();
  }, [rt.mutate, daily.mutate, global.mutate, ahSpread.mutate]);

  // 轻刷新（⟳）：只强制重取实时块（refresh=1 透传后端防抖锁）
  const lightRefresh = useCallback(async () => {
    await mutate(['/api/v1/market/overview/rt', { _t: open ? 'live' : 'off' }],
      async (cur?: OverviewRt) => {
        const fresh = await marketApi.overviewRt(true);
        return fresh ?? cur;
      }, { revalidate: false });
  }, [mutate, open]);

  // 合并视图：rt 覆盖 daily 的同名字段（trade_date），子组件接口不变。
  // 单块先到时另一块字段为 undefined，子组件按可选降级（?. 取值 + 骨架）。
  const data = useMemo<MarketOverviewData | null>(
    () => (daily.data || rtData
      ? { ...(daily.data as object), ...(rtData as object) } as unknown as MarketOverviewData
      : null),
    [daily.data, rtData],
  );

  const dates = data?.pred_dates ?? [];
  const today = data?.trade_date ?? '—';
  const fmtDate = (s: string) => (s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : s);

  const showCore = activeTab === 'all' || activeTab === 'core';
  const showTactical = activeTab === 'all' || activeTab === 'tactical';
  const showFactors = activeTab === 'all' || activeTab === 'factors';
  const showPicks = activeTab === 'all' || activeTab === 'picks';

  return (
    <div className="space-y-3.5">
      {/* 标题行：市场概览 | 视图切换 + 交易日下拉 + 数据新鲜度 */}
      <PageHeader
        title="市场概览"
        actions={
          <>
            {/* 视图切换：仅保留有真实落点的项。「自选」跳转 /watchlist（独立页面），
                「市场概览」为当前页（点击回到 /）。 */}
            <div className="flex items-center gap-0.5 rounded-md bg-surface-sunken p-0.5">
              <button onClick={() => navigate('/watchlist')} title="前往自选收藏"
                className="rounded px-2 py-0.5 text-2xs text-ink-muted transition-colors hover:text-ink-secondary">自选</button>
              <button onClick={() => navigate('/')} aria-current="page"
                className="rounded bg-surface px-2 py-0.5 text-2xs font-medium text-brand-600 shadow-sm">市场概览</button>
            </div>
            <select
              value={predDate}
              onChange={(e) => setPredDate(e.target.value)}
              title="切换 AI 推荐历史快照日期"
              className="rounded-md border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300">
              <option value="">交易日 {fmtDate(today)}（最新）</option>
              {dates.slice().reverse().map((d) => (
                <option key={d} value={d}>交易日 {fmtDate(d)}</option>
              ))}
            </select>
            {/* 数据新鲜度：as_of 取实时块快照时间；⟳ = 轻刷新（只刷实时块，§3.2/L2-2） */}
            <DataFreshness
              asOf={rtData?.as_of ?? (data?.trade_date ? fmtDate(data.trade_date) : null)}
              fromCache={rtData?.from_cache}
              stale={rtData?.stale}
              onRefresh={lightRefresh}
            />
          </>
        }
      />

      {/* 模块快速导航与聚焦过滤栏 */}
      <div className="flex items-center justify-between gap-2 overflow-x-auto rounded-lg border border-hair bg-surface px-2.5 py-1.5 shadow-2xs">
        <div className="flex items-center gap-1">
          <span className="text-3xs font-medium uppercase tracking-wider text-ink-muted pr-1">视图聚焦</span>
          {TABS.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-2xs font-medium transition-all ${
                  isActive
                    ? 'bg-brand-500 text-white shadow-2xs'
                    : 'text-ink-secondary hover:bg-surface-sunken hover:text-ink'
                }`}
              >
                <span>{tab.icon}</span>
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>
        <div className="hidden text-3xs text-ink-muted xl:block">
          专业量化交易驾驶舱 · 盘中实时自适应刷新
        </div>
      </div>

      {/* 错误态：双块失败时显式提示 + 重试（不再静默显示 "—"） */}
      {errorMessage && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">
          <span>{errorMessage}</span>
          <button onClick={retryAll}
            className="rounded-md bg-brand-500 px-2.5 py-0.5 text-2xs font-medium text-white hover:bg-brand-600">
            重试
          </button>
        </div>
      )}

      {/* Section 1: 核心大盘、全球指数与市场微观生态 */}
      {showCore && (
        <section className="space-y-2.5 [content-visibility:auto] [contain-intrinsic-size:auto_500px]">
          {activeTab === 'all' && (
            <SectionTitle
              icon="📊"
              title="核心大盘与量能监控"
              subtitle="4大主线指数 · 两市成交量能 · 全球主要基准"
              tag="大盘基准"
            />
          )}

          {/* A股核心主线指数看板（含上证、300、科创50、创业板指创新）+ 两市成交量能 */}
          <KpiCards data={data} loading={loading} />

          {/* 外盘指数（美日韩主流指数独立端点；逐卡各自的 data_date，日频不轮询） */}
          <GlobalIndices block={global.data} loading={!global.data && global.isLoading} />

          {/* 市场涨跌分布(6) | 微观投机生态与流动性(6) —— 6:6 对称平衡栅格 */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
            <div className="lg:col-span-6">
              <BreadthPanel heat={data?.heat} sentiment={data?.sentiment} loading={loading} />
            </div>
            <div className="lg:col-span-6">
              <SpeculationPanel speculation={data?.speculation} sentiment={data?.sentiment} loading={loading} />
            </div>
          </div>
        </section>
      )}

      {/* Section 2: 短线战术驾驶舱（连板天梯 6 : 6 异动雷达） */}
      {showTactical && (
        <section className="space-y-2.5 [content-visibility:auto] [contain-intrinsic-size:auto_500px]">
          {activeTab === 'all' && (
            <SectionTitle
              icon="⚡"
              title="超短战术：连板梯队与异动雷达"
              subtitle="对标 TSP 连板天梯晋级图 · 盘口脉冲速递 · 交易所偏离监控"
              tag="超短盯盘"
            />
          )}

          {/* 左右 6:6 对称并列布局：消除长单列纵深，打造一体化超短战术雷达 */}
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-12 items-start">
            <div className="xl:col-span-6">
              <LimitLadderCard />
            </div>
            <div className="xl:col-span-6">
              <AbnormalMovesCard />
            </div>
          </div>
        </section>
      )}

      {/* Section 3: Barra 风格因子多空收益与行业资金轮动 */}
      {showFactors && (
        <section className="space-y-2.5 [content-visibility:auto] [contain-intrinsic-size:auto_500px]">
          {activeTab === 'all' && (
            <SectionTitle
              icon="📈"
              title="因子定价、板块轮动与资金博弈"
              subtitle="Barra 风格多空收益 · RRG 动量象限 · 宽基估值 ERP · 行业主力净流向"
              tag="风格与资金"
            />
          )}

          {/* Row 1: Barra 风格因子多空收益(6) | 热门板块与 RRG 轮动(6) */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
            <div className="lg:col-span-6">
              <StyleFactorsPanel data={data?.style_factors} loading={loading} />
            </div>
            <div className="lg:col-span-6">
              <HotSectorsPanel sectors={data?.sectors} loading={loading} />
            </div>
          </div>

          {/* Row 2: 大盘估值与股债平衡 ERP(6) | 全市场行业资金流向(6) */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
            <div className="lg:col-span-6">
              <ValuationErpPanel data={data?.valuation} loading={loading} />
            </div>
            <div className="lg:col-span-6">
              <MoneyFlowPanel data={data} loading={loading} />
            </div>
          </div>
        </section>
      )}

      {/* Section 4: 跨市场比价与量化精选 */}
      {showPicks && (
        <section className="space-y-2.5 [content-visibility:auto] [contain-intrinsic-size:auto_500px]">
          {activeTab === 'all' && (
            <SectionTitle
              icon="🌐"
              title="跨市场互联与 AI 量化精选"
              subtitle="AH 股折溢价率与跨市场套利 · 机器学习多因子前向胜率推荐"
              tag="跨市场与选股"
            />
          )}

          {/* AH 股溢价与跨市场套利看板 */}
          <div className="grid grid-cols-1 gap-3">
            <AhSpreadPanel
              data={ahSpread.data}
              loading={!ahSpread.data && ahSpread.isLoading}
              onRefresh={() => void ahSpread.mutate()}
            />
          </div>

          {/* AI 预测精选 */}
          <div className="grid grid-cols-1 gap-3">
            <AiPicksPanel
              recommend={data?.recommend ?? { status: 'unavailable' }}
              loading={loading}
            />
          </div>
        </section>
      )}
    </div>
  );
}
