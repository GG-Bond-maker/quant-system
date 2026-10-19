/**
 * 市场涨跌分布面板：红绿方块密度网格（按涨跌幅区间）+ 市场概况迷你网格 + 红绿盘环形图。
 * 悬浮方块显示该区间家数；A 股惯例红=涨 绿=跌 灰=平。
 */
import { useMemo } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import type { HeatBlock, SentimentBlock } from '@/types/stock';
import { fmtNum } from '@/utils/format';

/*
 * 9 档涨跌幅区间色（**语义色**）：深绿(≤-7%) → 浅绿 → 中性 → 浅红 → 深红(≥7%)。
 * 这是一个发散型渐变色阶，两端分别锚定 `p.DOWN` / `p.UP`，中间档用 `withAlpha`
 * 生成递减透明度的同色相——既保留了"越极端颜色越深"的观感，又跟随主题切换
 * （此前硬编码的蓝绿/红 在暗色下不跟随，且与 token 值不一致）。
 * 中点"平"锚定中性 `p.FLAT`，绝不用涨跌色表态。
 */
function bucketMeta() {
  const p = chartPalette();
  return [
    { key: 'b1', label: '≤-7%', color: p.DOWN },
    { key: 'b2', label: '-7~-5%', color: withAlpha(p.DOWN, 0.78) },
    { key: 'b3', label: '-5~-3%', color: withAlpha(p.DOWN, 0.55) },
    { key: 'b4', label: '-3~0%', color: withAlpha(p.DOWN, 0.3) },
    { key: 'b5', label: '平', color: p.FLAT },
    { key: 'b6', label: '0~3%', color: withAlpha(p.UP, 0.3) },
    { key: 'b7', label: '3~5%', color: withAlpha(p.UP, 0.55) },
    { key: 'b8', label: '5~7%', color: withAlpha(p.UP, 0.78) },
    { key: 'b9', label: '≥7%', color: p.UP },
  ];
}

/**
 * 用区间家数按比例铺 N×M 方块网格（一眼看出多空密度）。
 *
 * ## 为什么用「最大余数法」而不是各自 `Math.round` 后再 `slice(0, TARGET)`
 * 原实现对每档独立 `Math.round((n/total)*TARGET)`：9 档四舍五入后总和可 **>240**
 * （最多 +4），再用 `slice(0, TARGET)` 砍掉尾部 —— 被砍的格子是**静默丢失**的，
 * 热力图比例因此失真且界面上没有任何披露（本项目红线）。
 * 现改为**最大余数法（Hamilton 配额）**：先取每档下取整，再把剩余名额按小数部分
 * 从大到小补足，使各档格数之和**恰为 TARGET** —— 不丢格，比例最接近真值。
 */
function BlockGrid({ heat }: { heat: HeatBlock }) {
  const theme = useTheme();
  const cells = useMemo(() => {
    const buckets = heat.buckets;
    if (!buckets) return [];
    const metaWithCount = bucketMeta().map((m) => ({ ...m, n: buckets[m.key] ?? 0 }));
    const total = metaWithCount.reduce((a, m) => a + m.n, 0);
    if (!total) return [];
    const TARGET = 240; // 网格块总数（16 列 × 15 行）
    // 下取整 + 余数补足：sum(alloc) === TARGET（无截断、无丢格）
    const quotas = metaWithCount.map((m) => (m.n / total) * TARGET);
    const alloc = quotas.map((q) => Math.floor(q));
    let rest = TARGET - alloc.reduce((a, b) => a + b, 0);
    const byRemainder = quotas
      .map((q, i) => ({ i, frac: q - Math.floor(q) }))
      .sort((a, b) => b.frac - a.frac);
    for (let k = 0; k < byRemainder.length && rest > 0; k += 1, rest -= 1) {
      alloc[byRemainder[k].i] += 1;
    }
    const out: Array<{ color: string; label: string }> = [];
    metaWithCount.forEach((m, i) => {
      for (let c = 0; c < alloc[i]; c += 1) out.push({ color: m.color, label: m.label });
    });
    // 按颜色乱序穿插（参考稿的混合密度观感），稳定随机用 index 乘散列
    out.sort((a, b) =>
      ((a.label.charCodeAt(1) * 31 + a.color.charCodeAt(1)) % 97) -
      ((b.label.charCodeAt(1) * 31 + b.color.charCodeAt(1)) % 97));
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算（chartPalette 现读 DOM）
  }, [heat, theme]);

  return (
    <div className="grid flex-1 grid-cols-16 gap-[3px]" style={{ gridTemplateColumns: 'repeat(16, minmax(0, 1fr))' }}>
      {cells.map((c, i) => (
        <div key={i} title={`${c.label}（${c.color} 区间样本）`}
          className="aspect-square rounded-[2px]" style={{ backgroundColor: c.color, opacity: 0.92 }} />
      ))}
    </div>
  );
}

function Donut({ heat }: { heat: HeatBlock }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    const p = chartPalette();
    // up / down / flat 均为可选字段：未知（null）时**不画该扇区**，绝不用 `?? 0`
    // 把"家数未知"画成「0 家」的扇区（红线：替未知表态）。三者全未知 ⇒ 返回 null，
    // 渲染占位「家数未知」。与上方 `fmtCell` 的「未知 ⇒ —」口径一致。
    const segs: Array<{ name: string; value: number; color: string }> = [];
    if (heat.up != null) segs.push({ name: '红盘(涨)', value: heat.up, color: p.UP });
    if (heat.down != null) segs.push({ name: '绿盘(跌)', value: heat.down, color: p.DOWN });
    if (heat.flat != null) segs.push({ name: '平盘', value: heat.flat, color: p.FLAT });
    if (!segs.length) return null;
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{
        type: 'pie', radius: ['52%', '78%'], center: ['50%', '50%'],
        label: { show: false }, silent: false,
        data: segs.map((s) => ({ name: s.name, value: s.value, itemStyle: { color: s.color } })),
      }],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [heat, theme]);

  const chartRef = useChart(option);

  if (!option) {
    return (
      <div style={{ width: 130, height: 130 }}
        className="flex items-center justify-center text-xs text-ink-muted">
        家数未知
      </div>
    );
  }
  return <div ref={chartRef} style={{ width: 130, height: 130 }} />;
}

function SentimentGauge({ sentiment }: { sentiment?: SentimentBlock }) {
  const theme = useTheme();
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
  const chartRef = useChart(option);

  return (
    <div className="flex flex-col items-center py-1">
      <div ref={chartRef} style={{ width: 148, height: 88 }} />
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

export default function BreadthPanel({ heat, sentiment, loading }: {
  heat?: HeatBlock; sentiment?: SentimentBlock; loading: boolean;
}) {
  const theme = useTheme();
  const ok = heat?.status === 'ok';
  // ⚠️ up / down 均为可选字段：不得用 `?? 0` 兜底 —— 家数未知时会被算成 0，
  //    渲染成「红盘 0% / 绿盘 0%」，等于把"未知"说成 0%（判据沿用本组件 :124 的
  //    `n != null && n > 0 ? fmtNum(n, 0) : '—'` 先例）。未知 ⇒ null，渲染为 '—' 且不染色。
  const up = heat?.up ?? null;
  const down = heat?.down ?? null;
  const flat = heat?.flat ?? null;
  const total = (up ?? 0) + (down ?? 0) + (flat ?? 0) || 1;
  /**
   * 市场概况 6 格：**互不重复**的口径（同屏冗余审计 H8）。
   *
   * 未知一律渲染「—」且不染色：`up`/`down`/`flat` 均为可空字段，用 `?? 0`
   * 兜底会把"取数失败"画成"0 家"，等于替未知表态（本项目红线）。
   * 总家数只有在三个分量**都已知**时才给值，否则同样是未知。
   */
  const fmtCell = (n: number | null | undefined) =>
    n != null ? fmtNum(n, 0) : '—';
  const totalKnown = up != null && down != null && flat != null;
  const gridCells: Array<{ label: string; text: string; cls: string }> = [
    { label: '涨停', text: fmtCell(heat?.limit_up), cls: 'bg-up text-white' },
    { label: '上涨', text: fmtCell(up), cls: 'bg-danger-bg t-up' },
    { label: '平盘', text: fmtCell(flat), cls: 'bg-surface-sunken text-ink-muted' },
    { label: '下跌', text: fmtCell(down), cls: 'bg-down-bg t-down' },
    { label: '跌停', text: fmtCell(heat?.limit_down), cls: 'bg-down text-white' },
    {
      label: '总家数',
      text: totalKnown ? fmtNum(total, 0) : '—',
      cls: 'bg-surface-alt text-ink-secondary',
    },
  ];
  // 图例色阶依赖 theme 重算（bucketMeta 现读 DOM 取色）
  // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色阶重算
  const bucketLegend = useMemo(() => bucketMeta(), [theme]);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">市场涨跌分布</h2>
        {heat?.source === 'local' && (
          <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-ink-muted">本地样本</span>
        )}
      </div>
      <div className="min-w-0 flex-1 p-4">
        {ok ? (
          <div className="flex flex-wrap items-stretch gap-4">
            {/* 左：标题行 + 方块网格 */}
            <div className="flex min-w-[260px] flex-1 flex-col">
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-medium t-up">红盘 {fmtNum(heat?.up, 0)}</span>
                <span className="text-ink-muted">/ 绿盘 {fmtNum(heat?.down, 0)}</span>
              </div>
              <BlockGrid heat={heat} />
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-ink-muted">
                {bucketLegend.map((m) => (
                  <span key={m.key} className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: m.color }} />
                    {m.label}
                  </span>
                ))}
              </div>
            </div>
            {/* 右：市场概况迷你网格 + AI情绪仪表 + 环形图 */}
            <div className="flex w-44 shrink-0 flex-col items-center justify-between gap-1 border-l border-hair pl-4">
              <div className="w-full">
                <div className="mb-1.5 text-xs font-medium text-ink">市场概况</div>
                <div className="grid grid-cols-3 gap-[3px]">
                  {gridCells.map((c) => (
                    <div key={c.label} title={`${c.label}：${c.text}`}
                      className={`flex aspect-square flex-col items-center justify-center rounded-[3px] text-2xs font-medium ${c.cls}`}>
                      <span className="num leading-none">{c.text}</span>
                      <span className="mt-0.5 text-[8px] font-normal opacity-80">{c.label}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* AI 市场情绪指标（填补市场概况与环形图之间的空白区） */}
              <SentimentGauge sentiment={sentiment} />

              <Donut heat={heat} />
            </div>
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-2" style={{ minHeight: 200 }}>
            <div className="h-3 w-1/2 rounded bg-surface-sunken" />
            <div className="h-40 w-full rounded bg-surface-sunken" />
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-xs text-ink-muted">
            {heat?.reason ?? '涨跌分布暂不可用'}
          </div>
        )}

        {/* 市场广度透视 (Market Breadth) */}
        {heat?.breadth && (
          <div className="mt-3 rounded-md border border-hair bg-surface-alt/40 p-2.5 text-xs">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-1">
              <span className="flex items-center gap-1.5 font-medium text-ink">
                <span>市场广度 (Market Breadth)</span>
                <span className={`rounded px-1.5 py-0.5 text-2xs font-semibold ${
                  heat.breadth.status.includes('积极') ? 'bg-up-bg text-up' :
                  heat.breadth.status.includes('偏多') ? 'bg-brand-50 text-brand-600 dark:bg-brand-900/30 dark:text-brand-300' :
                  heat.breadth.status.includes('中性') ? 'bg-warn-bg text-warn' :
                  'bg-down-bg text-down'
                }`}>
                  {heat.breadth.status}
                </span>
              </span>
              <span className="text-2xs text-ink-muted">
                样本总数: {heat.breadth.total_stocks} 只
              </span>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div className="rounded bg-surface p-1.5 text-center">
                <div className="text-2xs text-ink-muted">站上20日均线占比</div>
                <div className="num mt-0.5 text-xs font-semibold text-ink">
                  {heat.breadth.pct_above_ma20}%
                </div>
                <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-surface-sunken">
                  <div
                    className="h-full rounded-full bg-brand-500"
                    style={{ width: `${Math.min(100, heat.breadth.pct_above_ma20)}%` }}
                  />
                </div>
              </div>

              <div className="rounded bg-surface p-1.5 text-center">
                <div className="text-2xs text-ink-muted">站上60日均线占比</div>
                <div className="num mt-0.5 text-xs font-semibold text-ink">
                  {heat.breadth.pct_above_ma60}%
                </div>
                <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-surface-sunken">
                  <div
                    className="h-full rounded-full bg-brand-500"
                    style={{ width: `${Math.min(100, heat.breadth.pct_above_ma60)}%` }}
                  />
                </div>
              </div>

              <div className="rounded bg-surface p-1.5 text-center">
                <div className="text-2xs text-ink-muted">腾落线 (ADL) 净变化</div>
                <div className={`num mt-0.5 text-xs font-semibold ${
                  heat.breadth.adl_change > 0 ? 'text-up' :
                  heat.breadth.adl_change < 0 ? 'text-down' : 'text-ink-muted'
                }`}>
                  {heat.breadth.adl_change > 0 ? `+${heat.breadth.adl_change}` : heat.breadth.adl_change}
                </div>
                <div className="mt-1 text-2xs text-ink-muted">
                  涨跌差 (净多空扩散)
                </div>
              </div>
            </div>
          </div>
        )}

        {heat?.note && <p className="mt-2 text-2xs text-ink-muted">※ {heat.note}</p>}
      </div>
    </div>
  );
}
