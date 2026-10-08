/**
 * 顶部大盘全局指标栏：7 核心指标单行卡片矩阵。
 *
 * 布局优化：
 *   包含 4 大宽基指数（上证指数锚点卡带品牌色条、沪深300、科创50、创业板指）
 *   与 3 大盘面/AI 核心指标（两市成交额、资金流向、AI RankIC 5日）。
 *   所有卡片统一为等宽栅格卡片，在宽屏（xl+）下完美排满 7 列单行，
 *   避免上证指数跨列造成末端指标卡（AI RankIC）被迫换行孤立。
 *
 * 响应式：
 *   <sm  1 列堆叠
 *   sm   2 列
 *   md   3 列
 *   lg   4 列
 *   xl+  7 列整齐单行
 */
import type { MarketOverviewData } from '@/types/stock';
import { fmtNum, fmtPct } from '@/utils/format';
import { MiniSpark, SkeletonCard } from './pieces';

function fmtAmountYiWan(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v >= 10_000 ? `${(v / 10_000).toFixed(2)} 万亿` : `${v.toFixed(0)} 亿`;
}

/** 次级指标卡（宽基指数 / 成交额 / 资金流向 / AI 准确率） */
function Kpi({ label, value, sub, up, spark, loading, className }: {
  label: string; value: string; sub?: React.ReactNode;
  up?: boolean; spark?: number[]; loading?: boolean; className?: string;
}) {
  return (
    <div className={`flex items-center justify-between gap-2 rounded-lg border border-hair bg-surface px-3 py-3 sm:px-4 ${className ?? ''}`}>
      <div className="min-w-0">
        <div className="truncate text-xs text-ink-secondary">{label}</div>
        {loading ? (
          <div className="mt-1.5 h-6 w-20 animate-pulse rounded bg-surface-sunken" />
        ) : (
          <>
            <div className="num mt-0.5 truncate text-lg font-semibold text-ink" title={value}>{value}</div>
            {sub && <div className={`num text-xs font-medium ${up == null ? 'text-ink-muted' : up ? 't-up' : 't-down'}`}>{sub}</div>}
          </>
        )}
      </div>
      {spark && spark.length >= 3 && (
        <div className="shrink-0">
          <MiniSpark data={spark} up={up} width={44} height={22} />
        </div>
      )}
      {sub === undefined && !spark && <span className="sr-only">—</span>}
    </div>
  );
}

/**
 * 盘面锚点卡（上证指数）：
 * 保留品牌色左侧标识条突出盘面基准地位，布局调整为与其他指数一致的单格卡片，
 * 在宽屏（xl/2xl）下整齐形成 7 卡单行，避免跨列排布导致末尾卡片被迫换行。
 */
function HeroKpi({ name, value, sub, spark, up, loading }: {
  name: string; value: string; sub?: string; spark?: number[];
  up?: boolean; loading?: boolean;
}) {
  const tone = up == null ? 'text-ink-muted' : up ? 't-up' : 't-down';
  return (
    <div className="relative flex items-center justify-between gap-2 overflow-hidden rounded-lg border border-hair bg-surface px-3 py-3 sm:px-4">
      {/* 品牌色左边条：标示盘面基准锚点 */}
      <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-brand-500" />
      <div className="min-w-0">
        <div className="truncate text-xs font-medium text-ink-secondary">{name}</div>
        {loading ? (
          <div className="mt-1.5 h-6 w-20 animate-pulse rounded bg-surface-sunken" />
        ) : (
          <>
            <div className="num mt-0.5 truncate text-lg font-semibold text-ink" title={value}>{value}</div>
            {sub && <div className={`num text-xs font-medium ${tone}`}>{sub}</div>}
          </>
        )}
      </div>
      {spark && spark.length >= 3 && (
        <div className="shrink-0">
          <MiniSpark data={spark} up={up} width={44} height={22} />
        </div>
      )}
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
  const sh = items.find((i) => i.code === 'sh000001') ?? null;
  const hs300 = items.find((i) => i.code === 'sh000300') ?? null;
  // 科创与创新指数 (用户明确要求新增)
  const star50 = items.find((i) => i.code === 'sh000688') ?? null;
  const innov = items.find((i) => i.code === 'sz399006' || i.code === 'sz399706') ?? null;
  const heat = data?.heat;
  const money = data?.money_flow;
  const ai = data?.ai_stats;

  const flowVal = money?.main_net_today ?? money?.north_net_today ?? null;

  // 成交额口径披露（后端 `_heat_from_local` 本地降级路径）：
  const isLocalHeat = heat?.source === 'local';
  const amountSub = (() => {
    if (!isLocalHeat || !heat?.data_date) return undefined;
    const parts = [heat.data_date];
    if (heat.coverage_symbols != null) parts.push(`${heat.coverage_symbols} 只`);
    if (heat.latest_date && heat.latest_date !== heat.data_date) parts.push('已跳过覆盖不足日');
    return parts.join(' · ');
  })();

  return (
    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
      {/* 锚点：上证指数 */}
      {sh ? (
        <HeroKpi name={`${sh.name} (SSEC)`} value={fmtNum(sh.close)} up={sh.pct >= 0}
          sub={fmtPct(sh.pct)} spark={sh.sparkline} />
      ) : loading ? (
        <SkeletonCard lines={1} />
      ) : (
        <HeroKpi name="上证指数 (SSEC)" value="—" />
      )}

      {/* 沪深300 */}
      {hs300 ? (
        <Kpi label="沪深300 (CSI300)" value={fmtNum(hs300.close)}
          up={hs300.pct >= 0} sub={fmtPct(hs300.pct)} spark={hs300.sparkline} />
      ) : loading ? (
        <SkeletonCard lines={1} />
      ) : (
        <Kpi label="沪深300 (CSI300)" value="—" />
      )}

      {/* 科创50（用户明确要求：科创指数） */}
      {star50 ? (
        <Kpi label="科创50 (STAR50)" value={fmtNum(star50.close)}
          up={star50.pct >= 0} sub={fmtPct(star50.pct)} spark={star50.sparkline} />
      ) : loading ? (
        <SkeletonCard lines={1} />
      ) : (
        <Kpi label="科创50 (STAR50)" value="—" />
      )}

      {/* 创业板指 / 双创50（用户明确要求：创新指数） */}
      {innov ? (
        <Kpi label={`${innov.name} (创新)`} value={fmtNum(innov.close)}
          up={innov.pct >= 0} sub={fmtPct(innov.pct)} spark={innov.sparkline} />
      ) : loading ? (
        <SkeletonCard lines={1} />
      ) : (
        <Kpi label="创业板指 (创新)" value="—" />
      )}

      <Kpi label={isLocalHeat ? '两市成交额' : '今日两市成交额'}
        value={fmtAmountYiWan(heat?.total_amount_yi)} sub={amountSub}
        loading={loading && !heat} />

      {/* 资金流向 */}
      <Kpi label="资金流向" up={flowVal == null ? undefined : flowVal >= 0}
        value={flowVal != null ? `${flowVal > 0 ? '+' : ''}${flowVal.toFixed(0)} 亿` : '—'}
        sub={money?.main_net_today != null ? '主力净流入' : money?.north_net_today != null ? '北向净流入' : undefined}
        loading={loading && !money} />

      {/* AI RankIC */}
      <Kpi label={`AI RankIC（${ai?.horizon ?? '—'}日）`} up={ai?.rank_ic == null ? undefined : ai.rank_ic >= 0}
        value={ai?.rank_ic != null ? ai.rank_ic.toFixed(3) : '—'}
        sub={ai?.top_k_precision != null
          ? `Top-${ai.top_k ?? 20} 正收益 ${(ai.top_k_precision * 100).toFixed(1)}%`
          : '样本不足'}
        loading={loading && !ai} />
    </div>
  );
}
