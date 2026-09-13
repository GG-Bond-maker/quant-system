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
import { useSWRConfig } from 'swr';
import { marketApi, OVERVIEW_TIMEOUT } from '@/api/market';
import { REFRESH, useApi } from '@/api/swr';
import DataFreshness from '@/components/DataFreshness';
import type { MarketOverviewData, OverviewDaily, OverviewRt } from '@/types/stock';
import KpiCards from './KpiCards';
import BreadthPanel from './BreadthPanel';
import MoneyFlowPanel from './MoneyFlowPanel';
import HotSectorsPanel from './HotSectorsPanel';
import AiPicksPanel from './AiPicksPanel';

/** 客户端判断 A 股盘中（周一~周五 09:15-15:05，含集合竞价缓冲） */
function isMarketOpen(now = new Date()): boolean {
  const day = now.getDay();
  if (day === 0 || day === 6) return false;
  const mins = now.getHours() * 60 + now.getMinutes();
  return mins >= 555 && mins <= 905; // 09:15 - 15:05
}

export default function MarketOverview() {
  const [predDate, setPredDate] = useState<string>(''); // '' = 最新
  const { mutate } = useSWRConfig();

  // 实时块：SWR 轮询接管（盘中 30s / 非盘中不轮询——key 携带盘中态，切换即重取）
  // 冷算实测 22.9s（QA 探针）> client 默认 15s，故显式放宽到 OVERVIEW_TIMEOUT(60s)。
  const open = isMarketOpen();
  const rt = useApi<OverviewRt>('/api/v1/market/overview/rt',
    open ? { _t: 'live' } : { _t: 'off' },
    { refreshInterval: open ? REFRESH.realtime * 1000 : 0 },
    OVERVIEW_TIMEOUT);

  // 日频块：TTL 至次日盘后（后端长缓存），前端不轮询；日期切换换 key
  // 冷算实测 25.3s > 15s，同样显式放宽到 60s。
  const daily = useApi<OverviewDaily>('/api/v1/market/overview/daily',
    { recommend_k: 50, ...(predDate ? { date: predDate } : {}) },
    { refreshInterval: 0 },
    OVERVIEW_TIMEOUT);

  const loading = !rt.data && !daily.data && (rt.isLoading || daily.isLoading);
  const rtData = rt.data;

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

  return (
    <div className="space-y-3">
      {/* 标题行：市场概览 | 视图切换 + 交易日下拉 + 数据新鲜度 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-bold text-ink">市场概览</h1>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-md bg-slate-100 p-0.5">
            <button className="rounded px-2 py-0.5 text-2xs text-ink-muted hover:text-ink-secondary">自选</button>
            <button className="rounded bg-white px-2 py-0.5 text-2xs font-medium text-brand-600 shadow-sm">市场概览</button>
            <button className="rounded px-2 py-0.5 text-2xs text-ink-muted hover:text-ink-secondary">AI专题</button>
          </div>
          <select
            value={predDate}
            onChange={(e) => setPredDate(e.target.value)}
            title="切换 AI 推荐历史快照日期"
            className="rounded-md border border-hair bg-white px-2 py-1 text-xs text-ink outline-none focus:border-brand-300">
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
        </div>
      </div>

      {/* Row 0: 5 列 KPI */}
      <KpiCards data={data} loading={loading} />

      {/* Row 1: 涨跌分布 | 资金流向 */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <BreadthPanel heat={data?.heat} loading={loading} />
        <MoneyFlowPanel data={data} loading={loading} />
      </div>

      {/* Row 2: 热门板块 | AI 预测精选 */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <HotSectorsPanel sectors={data?.sectors} loading={loading} />
        <AiPicksPanel recommend={data?.recommend ?? { status: 'unavailable' }}
          sentiment={data?.sentiment} loading={loading} />
      </div>
    </div>
  );
}
