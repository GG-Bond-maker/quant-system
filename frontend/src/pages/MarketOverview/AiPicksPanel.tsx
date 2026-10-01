/**
 * AI 预测精选面板：推荐榜（代码 | AI标签 | 未来 5 日预测 | 全市场分位 | 迷你K线 | 近期资讯）
 * + 底部 AI 市场情绪仪表盘（ECharts gauge，0-100）。
 *
 * 关于「全市场分位」：取自后端 RecommendItem.rank_pct，是 pred_score 在当日
 * 全市场预测样本中的真实百分位。此处曾展示前端自造的"信心指数"
 * （|pred_score|*1400+40，保底 40、封顶 99），属于把算术包装成模型置信度，已移除。
 */
import { useMemo, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import type { MiniBar, RecommendBlock, SentimentBlock } from '@/types/stock';
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

function SentimentGauge({ sentiment }: { sentiment?: SentimentBlock }) {
  const theme = useTheme();
  const ref = useRef<HTMLDivElement>(null);
  // ⚠️ score 是 0~100 **有界刻度**上的语义值：0 = 极度恐慌，是**合法极值**而不是"没有值"。
  // 因此：① 不得用 `?? 0` 兜底——那会把"未知"钉在刻度最左端（显示为极度恐慌）；
  //       ② 也不得用 `!score` / `if (score)` 判空——那会把真实的 0 分当成"未知"不画，
  //          等于用"未知"吞掉一个真实极值（方向相反，同样是谎报）。
  //    判空只能用严格空值判断。未知时：不画指针、不显示数值 ⇒ 不表态。
  const score = sentiment?.score ?? null;
  const hasScore = score != null && Number.isFinite(score);
  const label = sentiment?.label ?? '—';
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    return {
    series: [{
      type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100,
      radius: '98%', center: ['50%', '62%'],
      // 情绪刻度为发散语义色带：0(极度恐慌,绿) → 中性(灰) → 100(极度贪婪,红)，
      // 与下方图例「恐慌=绿 / 贪婪=红」及全站涨跌口径一致。两端锚定 token，中间档位用 withAlpha。
      axisLine: {
        lineStyle: { width: 9, color: [
          [0.2, p.DOWN], [0.4, withAlpha(p.DOWN, 0.55)], [0.6, p.FLAT],
          [0.8, withAlpha(p.UP, 0.55)], [1, p.UP],
        ] },
      },
      // 未知时不画指针/锚点（否则指针会落在某个有语义的位置 ⇒ 替"未知"表态）
      pointer: { show: hasScore, length: '58%', width: 3, itemStyle: { color: p.INK2 } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      anchor: { show: hasScore, size: 5, itemStyle: { color: p.INK2 } },
      title: { show: false },
      detail: {
        fontSize: 12, fontWeight: 600, color: p.INK, offsetCenter: [0, '52%'],
        formatter: () => (hasScore ? `${score} · ${label}` : '—'),
      },
      data: hasScore ? [{ value: score }] : [],
    }],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [score, hasScore, label, theme]);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    return () => chart.dispose();
  }, [option]);
  return (
    <div className="flex flex-col items-center">
      <div ref={ref} style={{ width: 150, height: 92 }} />
      <div className="-mt-1 text-center text-2xs text-ink-muted"
        title={sentiment?.basis ?? '平台自研口径：广度+涨跌停温差确定性加权，非第三方情绪指数'}>
        AI 市场情绪指标<span className="ml-1 cursor-help text-ink-muted">ⓘ</span>
      </div>
      <div className="mt-0.5 flex gap-2 text-2xs text-ink-muted">
        <span className="t-down">恐慌</span><span>中性</span><span className="t-up">贪婪</span>
      </div>
    </div>
  );
}

export default function AiPicksPanel({ recommend, sentiment, loading }: {
  recommend: RecommendBlock; sentiment?: SentimentBlock; loading: boolean;
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
      {/* 情绪仪表 */}
      <div className="flex items-center justify-center border-t border-hair py-2">
        <SentimentGauge sentiment={sentiment} />
      </div>
    </div>
  );
}
