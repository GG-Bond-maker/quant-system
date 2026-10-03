/**
 * 外盘指数区块：日经225 / 韩国KOSPI / 道琼斯 / 纳斯达克 / 标普500。
 *
 * 放在市场概览 KPI 行下方，一行 5 张卡。
 *
 * ## 三条硬约束（本项目反复踩过）
 * 1. **每张卡展示自己的 `data_date`**：不同市场交易日/时区不同
 *    （实测美股/日韩 10-02、A 股 09-30），若共用 A 股 trade_date，
 *    会把"上一交易日的外盘收盘"错标成"A 股当日"。
 * 2. **未知不表态**：缺失项渲染「—」，**不得**用 0 冒充、**不得**替未知染色。
 * 3. **不得把「上一收盘」说成「实时」**：美股 3 项走后端腾讯报价，带**数据源
 *    自己的时间戳**（`as_of`），此时如实展示该时刻（`10-02 17:00 更新`）；
 *    其余（日经/KOSPI 恒为此态）只有日线日期，展示`截至 10-02 收盘`。
 *    ⚠️ 即便带时间戳也**不写**「实时」二字 —— 北京时间白天美股休市，
 *    该时间戳就是上一收盘时刻，写「实时」是假陈述。
 */
import type { GlobalIndexItem, GlobalIndicesBlock } from '@/types/stock';
import { fmtNum, fmtPct } from '@/utils/format';
import { MiniSpark, SkeletonCard } from './pieces';

/**
 * 卡片的**固定顺序与展示名**（与后端 `GLOBAL_INDICES` 的 display_name 对齐）。
 * 卡片始终渲染这 5 项 —— 降级时布局不塌，缺失项显示「—」。
 * `code` 即后端返回的 `code`（新浪 symbol），用于把数据并回对应卡片。
 */
const GLOBAL_SLOTS: Array<{ code: string; name: string }> = [
  { code: '日经225指数', name: '日经225 (NKY)' },
  { code: '首尔综合指数', name: '韩国KOSPI' },
  { code: '.DJI', name: '道琼斯 (.DJI)' },
  { code: '.IXIC', name: '纳斯达克 (.IXIC)' },
  { code: '.INX', name: '标普500 (.INX)' },
];

/** '2026-10-02' -> '10-02'（卡片副标题只需月-日） */
function mdOf(iso: string): string {
  return iso.length >= 10 ? iso.slice(5, 10) : iso;
}

/** '2026-10-02 17:00:17' -> '10-02 17:00'（带时刻的报价只到分钟） */
function mdhmOf(s: string): string {
  return s.length >= 16 ? `${s.slice(5, 10)} ${s.slice(11, 16)}` : mdOf(s);
}

/** 单张外盘卡：缺失时全渲染「—」，方向未知走中性色。 */
function GlobalCard({ name, item }: { name: string; item?: GlobalIndexItem }) {
  // 未知方向不染色（不得用 `?? false` 把"取数失败"兜成跌）
  const tone = item == null ? 'text-ink-muted' : item.pct >= 0 ? 't-up' : 't-down';
  const value = item ? fmtNum(item.close) : '—';
  // 有 `as_of` ⇒ 后端头部数值来自腾讯报价（带源时间戳）；否则来自新浪日线收盘。
  const asOf = item?.as_of ?? null;
  const timeLabel = item == null
    ? '—'
    : asOf ? `${mdhmOf(asOf)} 更新` : `截至 ${mdOf(item.data_date)} 收盘`;
  const timeTitle = item == null
    ? undefined
    : asOf ? `来源：腾讯行情 · 报价时间 ${asOf}` : `来源：新浪财经 · 收盘 ${item.data_date}`;
  return (
    <div data-testid="global-index-card"
      className="flex items-start justify-between gap-2 rounded-lg border border-hair bg-surface px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-xs text-ink-secondary" title={name}>{name}</div>
        <div className="num mt-0.5 truncate text-lg font-semibold text-ink" title={value}>{value}</div>
        <div className={`num text-xs font-medium ${tone}`}>
          {item ? fmtPct(item.pct) : '—'}
        </div>
        <div className="mt-1 text-2xs text-ink-muted" title={timeTitle}>
          {timeLabel}
        </div>
      </div>
      {item?.sparkline && item.sparkline.length >= 3 && (
        <MiniSpark data={item.sparkline} up={item.pct >= 0} width={56} height={28} />
      )}
    </div>
  );
}

export default function GlobalIndices({ block, loading }: {
  block?: GlobalIndicesBlock | null; loading?: boolean;
}) {
  const items = block?.status === 'ok' ? block.items ?? [] : [];
  const byCode = new Map(items.map((i) => [i.code, i]));

  return (
    <section className="space-y-2" aria-label="外盘指数">
      <h2 className="text-xs font-medium text-ink-secondary">外盘指数</h2>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {loading && !block
          ? GLOBAL_SLOTS.map((s) => <SkeletonCard key={s.code} lines={2} />)
          : GLOBAL_SLOTS.map((s) => (
            <GlobalCard key={s.code} name={s.name} item={byCode.get(s.code)} />
          ))}
      </div>
    </section>
  );
}
