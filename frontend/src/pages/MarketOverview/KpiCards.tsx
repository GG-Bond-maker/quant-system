/**
 * 顶部大盘全局指标栏：**1 大 + 4 小** 的非对称栅格。
 *
 * 设计意图（对照设计规范 §4 信息层级）：
 *   5 张等宽卡片会让「上证指数」这个**最重要**的盘面锚点与「成交额」等
 *   次要指标获得相同的视觉权重，用户扫视时缺少落点。
 *   故把上证指数提升为 hero 卡（跨 2 列 / 更大字号 / 带迷你走势），
 *   其余 4 项收敛为次级卡 —— 一眼即可分辨主次。
 *
 * 响应式：
 *   <md  1 列堆叠（移动端优先保证 hero 可读）
 *   md   2 列（hero 占整行，4 小项 2×2）
 *   xl   6 列栅格：hero 跨 2 列，4 小项各 1 列
 */
import type { MarketOverviewData } from '@/types/stock';
import { fmtNum, fmtPct } from '@/utils/format';
import { MiniSpark, SkeletonCard } from './pieces';

function fmtAmountYiWan(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v >= 10_000 ? `${(v / 10_000).toFixed(2)} 万亿` : `${v.toFixed(0)} 亿`;
}

/** 次级指标卡（成交额 / 资金流向 / AI 准确率） */
function Kpi({ label, value, sub, up, loading, className }: {
  label: string; value: string; sub?: React.ReactNode;
  /**
   * 方向：`true`=涨(红) / `false`=跌(绿) / `undefined`=**未知(中性，不表态)**。
   * ⚠️ 可空数值**不得**用 `(x ?? 0) >= 0` 兜出一个方向 —— 那会把"取数失败"染成涨/跌，
   * 即用兜底值冒充真实方向（项目红线）。未知时必须传 `undefined` 走中性分支。
   */
  up?: boolean; loading?: boolean; className?: string;
}) {
  return (
    <div className={`flex items-center justify-between gap-2 rounded-lg border border-hair bg-surface px-4 py-3 ${className ?? ''}`}>
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
      {sub === undefined && <span className="sr-only">—</span>}
    </div>
  );
}

/**
 * Hero 卡（上证指数）：盘面锚点。
 * 比次级卡大一档字号 + 带迷你走势 + 品牌色左边条，形成明确的视觉落点。
 */
function HeroKpi({ name, value, sub, spark, up, loading }: {
  name: string; value: string; sub?: string; spark?: number[];
  up?: boolean; loading?: boolean;
}) {
  const tone = up == null ? 'text-ink-muted' : up ? 't-up' : 't-down';
  return (
    <div className="relative flex items-center justify-between gap-3 overflow-hidden rounded-lg border border-hair bg-surface px-4 py-3
                    md:col-span-2 xl:col-span-2">
      {/* 品牌色左边条：唯一使用品牌色的 KPI，进一步强化主次 */}
      <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-brand-500" />
      <div className="min-w-0">
        <div className="truncate text-xs font-medium text-ink-secondary">{name}</div>
        {loading ? (
          <div className="mt-1.5 h-8 w-32 animate-pulse rounded bg-surface-sunken" />
        ) : (
          <>
            <div className="num mt-0.5 truncate text-2xl font-semibold leading-tight text-ink" title={value}>{value}</div>
            {sub && <div className={`num text-sm font-semibold ${tone}`}>{sub}</div>}
          </>
        )}
      </div>
      {spark && spark.length >= 3 && <MiniSpark data={spark} up={up} />}
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

  // 成交额口径披露（后端 `_heat_from_local` 本地降级路径）：
  //   `source==='local'` 说明实时快照不可用，数字来自**某个历史交易日**，
  //   且可能跳过了"已更新但覆盖不足"的日期 ⇒ 既不能叫「今日」，也不能不标口径日。
  //   故：① 标签去掉「今日」；② 副标题给出 统计日 · 覆盖标的数（跳过时追加说明）。
  const isLocalHeat = heat?.source === 'local';
  const amountSub = (() => {
    if (!isLocalHeat || !heat?.data_date) return undefined;
    const parts = [heat.data_date];
    if (heat.coverage_symbols != null) parts.push(`${heat.coverage_symbols} 只`);
    if (heat.latest_date && heat.latest_date !== heat.data_date) parts.push('已跳过覆盖不足日');
    return parts.join(' · ');
  })();

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-6">
      {/* Hero：上证指数 */}
      {sh ? (
        <HeroKpi name={`${sh.name} (SSEC)`} value={fmtNum(sh.close)} up={sh.pct >= 0}
          sub={fmtPct(sh.pct)} spark={sh.sparkline} />
      ) : loading ? (
        <div className="md:col-span-2 xl:col-span-2"><SkeletonCard lines={1} /></div>
      ) : (
        <HeroKpi name="上证指数 (SSEC)" value="—" />
      )}

      {/* 沪深300：次级卡里最重要的一张，紧跟 hero */}
      {hs300 ? (
        <Kpi label="沪深300 (CSI300)" value={fmtNum(hs300.close)}
          up={hs300.pct >= 0} sub={fmtPct(hs300.pct)} />
      ) : loading ? (
        <SkeletonCard lines={1} />
      ) : (
        <Kpi label="沪深300 (CSI300)" value="—" />
      )}

      <Kpi label={isLocalHeat ? '两市成交额' : '今日两市成交额'}
        value={fmtAmountYiWan(heat?.total_amount_yi)} sub={amountSub}
        loading={loading && !heat} />
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
