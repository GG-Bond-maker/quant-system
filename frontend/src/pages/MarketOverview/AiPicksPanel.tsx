/**
 * AI 预测精选面板：推荐榜（代码 | AI标签 | 未来 5 日预测 | 全市场分位 | 迷你K线 | 近期资讯）
 * + 底部 AI 市场情绪仪表盘（ECharts gauge，0-100）。
 *
 * 关于「全市场分位」：取自后端 RecommendItem.rank_pct，是 pred_score 在当日
 * 全市场预测样本中的真实百分位。此处曾展示前端自造的"信心指数"
 * （|pred_score|*1400+40，保底 40、封顶 99），属于把算术包装成模型置信度，已移除。
 */
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import type { MiniBar, RecommendBlock } from '@/types/stock';
import ResearchDisclaimer from '@/components/ResearchDisclaimer';
import { fmtPct } from '@/utils/format';

/** 迷你K线（真实 OHLC，红涨绿跌） */
function MiniCandles({ bars }: { bars: MiniBar[] }) {
  const theme = useTheme();
  // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算（chartPalette 现读 DOM）
  const p = useMemo(() => chartPalette(), [theme]);
  if (!bars?.length) return <div className="h-10 w-24" />;
  const w = 96, h = 40;
  const lo = Math.min(...bars.map((b) => b.low));
  const hi = Math.max(...bars.map((b) => b.high));
  const range = hi - lo || 1;
  const step = w / bars.length;
  const y = (v: number) => 2 + (1 - (v - lo) / range) * (h - 4);
  return (
    <svg width={w} height={h} className="shrink-0">
      {bars.map((b, i) => {
        const up = b.close >= b.open;
        const color = up ? p.UP : p.DOWN;
        const cx = i * step + step / 2;
        const bodyTop = y(Math.max(b.open, b.close));
        const bodyH = Math.max(1.5, Math.abs(y(b.open) - y(b.close)));
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={y(b.high)} y2={y(b.low)} stroke={color} strokeWidth={0.8} />
            <rect x={cx - step * 0.3} y={bodyTop} width={step * 0.6} height={bodyH} fill={color} />
          </g>
        );
      })}
    </svg>
  );
}

/** AI 标签（按 pred_score 分档，与选股中心风险口径一致） */
function tagOf(score: number): { label: string; cls: string } {
  if (score >= 0.02) return { label: '强烈看多', cls: 'bg-up text-white' };
  if (score >= 0.008) return { label: '看多', cls: 'bg-danger-bg t-up' };
  if (score > -0.008) return { label: '震荡', cls: 'bg-surface-sunken text-ink-muted' };
  if (score > -0.02) return { label: '看空', cls: 'bg-down-bg t-down' };
  return { label: '强烈看空', cls: 'bg-down text-white' };
}

export default function AiPicksPanel({ recommend, loading }: {
  recommend: RecommendBlock; loading: boolean;
}) {
  const items = (recommend.items ?? []).slice(0, 5);
  const hasItems = items.length > 0;
  const statusMessage = recommend.message
    ?? (recommend.status === 'ok' ? '当前没有满足条件的有效信号' : '推荐数据暂不可用');

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">AI 预测精选</h2>
        {(recommend.as_of || recommend.date) && (
          <span className="num text-2xs text-ink-muted">
            更新于 {recommend.as_of ?? recommend.date}
            {recommend.sample_size ? ` · ${recommend.sample_size} 只参排` : ''}
          </span>
        )}
      </div>
      {hasItems && recommend.status !== 'ok' && (
        <div className="border-b border-warn/30 bg-warn-bg px-4 py-2 text-2xs text-warn">
          {statusMessage}{recommend.as_of ? ` · 数据时间 ${recommend.as_of}` : ''}
        </div>
      )}
      <div className="min-w-0 flex-1 overflow-x-auto">
        {hasItems ? (
          <table className="w-full text-xs">
            <thead><tr className="border-b border-hair text-2xs text-ink-muted">
              <th className="px-4 py-2 text-left font-normal">代码名称</th>
              <th className="py-2 text-center font-normal">AI标签</th>
              <th
                className="py-2 text-right font-normal"
                title="alpha_basic_v1 对未来 5 个交易日收益的模型预测，非收益承诺"
              >
                未来 5 日预测
              </th>
              <th className="py-2 text-right font-normal"
                title="pred_score 在当日全市场预测样本中的百分位（相对排名，非模型置信度）">
                全市场分位
              </th>
              <th className="py-2 text-center font-normal">近期K线</th>
              <th className="px-4 py-2 text-left font-normal">近期资讯</th>
            </tr></thead>
            <tbody>
              {items.map((it) => {
                const tag = tagOf(it.pred_score);
                return (
                  <tr key={it.symbol} className="border-b border-hair/50 hover:bg-surface-alt">
                    <td className="px-4 py-2">
                      <Link to={`/stock/${it.symbol}`} className="group block">
                        <span className="num text-brand-600 group-hover:underline">{it.symbol.split('.')[0]}</span>
                        <div className="max-w-[6rem] truncate text-2xs text-ink-muted" title={it.name ?? ''}>
                          {it.name ?? '—'}
                        </div>
                      </Link>
                    </td>
                    <td className="py-2 text-center">
                      <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${tag.cls}`}>{tag.label}</span>
                    </td>
                    <td className={`num py-2 text-right font-medium ${it.pred_score >= 0 ? 't-up' : 't-down'}`}>
                      {fmtPct(it.pred_score * 100)}
                    </td>
                    <td className="num py-2 text-right text-ink-secondary">
                      {it.rank_pct != null ? `${it.rank_pct.toFixed(1)}%` : '—'}
                    </td>
                    <td className="py-1">
                      <Link to={`/stock/${it.symbol}`} title="进入个股分析">
                        <MiniCandles bars={it.bars ?? []} />
                      </Link>
                    </td>
                    <td className="max-w-[11rem] px-4 py-2">
                      {it.news?.title ? (
                        <div className="truncate text-2xs text-ink-secondary"
                          title={`${it.news.pub_date ?? ''} ${it.news.title}`}>
                          <span className={it.news.sentiment === 'positive' ? 't-up'
                            : it.news.sentiment === 'negative' ? 't-down' : ''}>
                            {it.news.title}
                          </span>
                        </div>
                      ) : <span className="text-2xs text-ink-muted/60">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : loading ? (
          <div className="animate-pulse space-y-2.5 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-5 rounded bg-surface-sunken" style={{ width: `${94 - i * 9}%` }} />
            ))}
          </div>
        ) : (
          <div className="flex h-32 flex-col items-center justify-center gap-1 px-4 text-center text-xs text-ink-muted">
            <span>{statusMessage}</span>
            {recommend.as_of && <span className="num text-2xs">数据时间 {recommend.as_of}</span>}
          </div>
        )}
      </div>
      <div className="border-t border-hair px-4 py-2">
        <ResearchDisclaimer kind="model" />
      </div>
    </div>
  );
}
