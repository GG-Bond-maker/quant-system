/**
 * 顶部大盘全局指标栏：5 列等宽卡片，右侧迷你走势，涨跌动态红绿。
 * 数据块缺失时对应卡片显示骨架或 —。
 */
import type { MarketOverviewData } from '@/types/stock';
import { fmtNum, fmtPct } from '@/utils/format';
import { MiniSpark, SkeletonCard } from './pieces';

function fmtAmountYiWan(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v >= 10_000 ? `${(v / 10_000).toFixed(2)} 万亿` : `${v.toFixed(0)} 亿`;
}

function Kpi({ label, value, sub, spark, up, loading }: {
  label: string; value: string; sub?: React.ReactNode; spark?: number[];
  /**
   * 方向：`true`=涨(红) / `false`=跌(绿) / `undefined`=**未知(中性，不表态)**。
   * ⚠️ 可空数值**不得**用 `(x ?? 0) >= 0` 兜出一个方向 —— 那会把"取数失败"染成涨/跌，
   * 即用兜底值冒充真实方向（项目红线）。未知时必须传 `undefined` 走中性分支。
   */
  up?: boolean; loading?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-hair bg-white px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-xs text-ink-secondary">{label}</div>
        {loading ? (
          <div className="mt-1.5 h-6 w-20 animate-pulse rounded bg-slate-100" />
        ) : (
          <>
            <div className="num mt-0.5 truncate text-lg font-semibold text-ink" title={value}>{value}</div>
            {sub && <div className={`num text-xs font-medium ${up == null ? 'text-ink-muted' : up ? 't-up' : 't-down'}`}>{sub}</div>}
          </>
        )}
      </div>
      {spark && spark.length >= 3 && <MiniSpark data={spark} up={up ?? false} />}
    </div>
  );
}

export default function KpiCards({ data, loading }: {
  data: MarketOverviewData | null; loading: boolean;
}) {
  // 注意：data 由 daily/rt 两块的浅合并而来，单块先到时 indices 可能为 undefined，
  // 故必须用 `data?.indices?.status`（仅 `data?.` 不足以保护 indices 本身）。
  const items = data?.indices?.status === 'ok' ? data.indices.items ?? [] : [];
  // F-04：指数兜底一律按代码匹配，取不到就显示不可用；
  // 不得用位置下标（如 items[1]）兜底，否则会把深证成指标成「沪深300」。
  const sh = items.find((i) => i.code === 'sh000001') ?? null;
  const hs300 = items.find((i) => i.code === 'sh000300') ?? null;
  const heat = data?.heat;
  const money = data?.money_flow;
  const ai = data?.ai_stats;

  const flowVal = money?.main_net_today ?? money?.north_net_today ?? null;

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {sh ? (
        <Kpi label={`${sh.name} (SSEC)`} value={fmtNum(sh.close)} up={sh.pct >= 0}
          sub={fmtPct(sh.pct)} spark={sh.sparkline} />
      ) : loading ? (
        <SkeletonCard lines={1} />
      ) : (
        <Kpi label="上证指数 (SSEC)" value="—" up />
      )}
      {hs300 ? (
        <Kpi label={`沪深300 (CSI300)`} value={fmtNum(hs300.close)} up={hs300.pct >= 0}
          sub={fmtPct(hs300.pct)} spark={hs300.sparkline} />
      ) : loading ? (
        <SkeletonCard lines={1} />
      ) : (
        <Kpi label="沪深300 (CSI300)" value="—" up />
      )}
      <Kpi label="今日两市成交额" up={false}
        value={fmtAmountYiWan(heat?.total_amount_yi)} loading={loading && !heat} />
      {/* 资金流向 / AI 准确率无真实时序，不配迷你走势（避免伪数据） */}
      <Kpi label="资金流向" up={flowVal == null ? undefined : flowVal >= 0}
        value={flowVal != null ? `${flowVal > 0 ? '+' : ''}${flowVal.toFixed(0)} 亿` : '—'}
        sub={money?.main_net_today != null ? '主力净流入' : money?.north_net_today != null ? '北向净流入' : undefined}
        loading={loading && !money} />
      <Kpi label={`AI RankIC（${ai?.horizon ?? '—'}日）`} up={ai?.rank_ic == null ? undefined : ai.rank_ic >= 0}
        value={ai?.rank_ic != null ? ai.rank_ic.toFixed(3) : '—'}
        sub={ai?.top_k_precision != null
          ? `Top-${ai.top_k ?? 20} 正收益 ${(ai.top_k_precision * 100).toFixed(1)}% · ${ai.n_days ?? 0} 个评估日`
          : '样本不足'}
        loading={loading && !ai} />
    </div>
  );
}
