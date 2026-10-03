/**
 * 选股中心 KPI 卡（6 张）—— 本轮三处「正确性」修复的回归测试（2026-10-03）。
 *
 * ## 被守的缺陷
 *
 * ### 一、「股票数量 5,000」被当成全市场
 * 5,000 是 `pool_size`（universe 剔除 ST/停牌、**且当日有预测快照**之后、top_k 截断
 * 前的池子）。用户看到 5,000 会以为这就是全市场（实测 universe 快照是 5,208 只）。
 * 现在主值旁并列「/ 全市场 5,208 只」。
 * ⚠️ 差额 **不得**被说成"已剔除 ST/停牌 N"：`pool_size` 还丢掉了"当日无预测"的标的
 * （实测 2026-09-30：universe 5,208、universe∩pred 5,197、ST∪停牌 208 ⇒ 5,000；
 * 208 与差额相等只是当日巧合）。故本文件断言 hint 里说的是"差额含 ST/停牌**以及**
 * 当日无预测快照的标的"。
 *
 * ### 二、对比行写死「较昨日」
 * 后端 `stats.prev_date` 是「上一份可用预测快照的日期」—— 实测 2026-09-30 的对比日
 * 是 2026-09-17（相隔 13 天）。写死「较昨日」是**陈述错误**。
 * 且**计数类**指标的差值本身不可解读：`pool_size` 由「当日有预测快照的标的数」
 * 驱动（2,431 → 5,000，预测覆盖度 2.3% → 96.0%），后端已标 `comparable=false`。
 *
 * ### 三、强信号占比跨口径
 * 后端 `compute_stats` 的 `strong_signal` 只在 `items`（= `total` 条榜单）上统计
 * （`signal_strength` 是按 `pred_score` **名次分位**给的档位），而原实现用
 * `pool_size` 做分母 ⇒ 40/5,000 = 0.8%，`toFixed(0)` 显示成「1%」（池子更大时「0%」）。
 * 真实口径是 40/50 = 80%。本文件用 `total=200 / pool_size=20000` 构造"旧口径恰好
 * 舍成 0%"的用例，确保这条不会再回来。
 *
 * ## 为什么这些断言在 jsdom 里有效
 * 断言的全是 **DOM 文本 / className / title**，与布局引擎无关。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ScreenerItem, ScreenerResult } from '@/types/p1';

import StatsCards, { type StatsBlock, type StatsDay } from './StatsCards';

/* ---------------- 夹具（对齐后端实测值） ---------------- */

/** 2026-09-30 实测：total=50 / pool_size=5,000 / strong_signal=40 / industry_count=12 */
const TODAY: StatsDay = {
  total: 50, pool_size: 5000, win_rate: 28.0, avg_pct: -0.0576, avg_score: 0.011139,
  strong_signal: 40, industry_count: 12, top_industry_ratio: 15.0,
};
/** 对比日 2026-09-17 实测：pool_size=2,431（当日预测覆盖不全）/ industry_count=20 */
const PREV: StatsDay = {
  total: 50, pool_size: 2431, win_rate: 20.0, avg_pct: -1.2, avg_score: 0.008,
  strong_signal: 20, industry_count: 20, top_industry_ratio: 12.12,
};
const STATS: StatsBlock = { today: TODAY, prev: PREV, prev_date: '2026-09-17' };

/** universe 快照实测：全市场在册 5,208 只（含 ST 201 / 停牌 11） */
const UNIVERSE: ScreenerResult['universe_filter'] = {
  applied: true, rows: 5208, date: '2026-09-30', reason: null, board_rows: null,
};

/** 榜单内的信号档位（后端按 pred_score 名次分位给出；这里只需能凑出真实计数） */
function strengthOf(i: number, strong: number): ScreenerItem['signal_strength'] {
  if (i < strong) return 'strong';
  if (i < strong + 10) return 'neutral';
  return 'weak';
}

function makeItems(n: number, strong: number): ScreenerItem[] {
  return Array.from({ length: n }, (_, i) => ({
    symbol: `${600000 + i}.SH`,
    name: `标的${i}`,
    industry: '制造业',
    close: 10, pct: 1, amount: 1e8, turnover: 1, limit_pct: 10, score: 0.01,
    signal_strength: strengthOf(i, strong),
  }));
}
const ITEMS = makeItems(50, 40);

/* ---------------- 取值助手 ---------------- */

/** 卡片根（KpiCard 的外壳）；用 `closest` 限定在**本卡内**断言，避免全页文本误匹配 */
function cardOf(label: string): HTMLElement {
  const el = screen.getByText(label).closest('div.rounded-lg');
  if (!el) throw new Error(`未找到卡片：${label}`);
  return el as HTMLElement;
}

/** 卡片底部的「对比 / 占比」行（KpiCard 里唯一的 `div.num.mt-1`） */
function compareRowOf(label: string): string {
  const row = cardOf(label).querySelector('div.num.mt-1');
  if (!row) throw new Error(`未找到对比行：${label}`);
  return (row.textContent ?? '').trim();
}

function renderCards(opts: {
  stats?: StatsBlock | null;
  items?: ScreenerItem[];
  universe?: ScreenerResult['universe_filter'] | null;
} = {}) {
  return render(
    <StatsCards
      stats={opts.stats === undefined ? STATS : opts.stats}
      series={null}
      items={opts.items ?? ITEMS}
      universe={opts.universe === undefined ? UNIVERSE : opts.universe}
    />,
  );
}

/* ==================== 一、全市场并列披露 ==================== */

describe('股票数量：并列全市场（本次改造的起因）', () => {
  it('universe 可用 ⇒ 主值旁并列「全市场 5,208 只」，主值仍是过滤后的 5,000', () => {
    renderCards();
    const card = cardOf('股票数量');
    expect(card.textContent).toContain('5,000');
    expect(card.textContent).toContain('全市场 5,208 只');
  });

  it('差额不得被断言为"已剔除 ST/停牌 N"（池子还丢了当日无预测的标的）', () => {
    renderCards();
    const hint = cardOf('股票数量').querySelector('[title]')?.getAttribute('title') ?? '';
    expect(hint).toContain('全市场（universe 快照）5,208 只');
    // 关键：必须说清差额的构成，不能把 208 全算成 ST/停牌
    expect(hint).toContain('以及当日无预测快照的标的');
    expect(hint).not.toContain('已剔除 ST/停牌 208');
  });

  it('universe 缺失 ⇒ 不并列全市场，并在 hint 里披露 ST/停牌过滤未生效', () => {
    renderCards({ universe: null });
    const card = cardOf('股票数量');
    expect(card.textContent).toContain('5,000');
    expect(card.textContent).not.toContain('全市场');
    const hint = card.querySelector('[title]')?.getAttribute('title') ?? '';
    expect(hint).toContain('ST/停牌过滤未生效');
    expect(hint).toContain('未取到当日 universe 快照');
  });

  it('快照在但缺关键列（applied=false）⇒ 措辞与「未取到快照」不同，且不并列', () => {
    renderCards({
      universe: { ...UNIVERSE, applied: false, reason: 'universe_schema_incomplete' },
    });
    const card = cardOf('股票数量');
    expect(card.textContent).not.toContain('全市场');
    const hint = card.querySelector('[title]')?.getAttribute('title') ?? '';
    expect(hint).toContain('ST/停牌过滤未生效');
    // 快照其实**在**（5,208 只），只是缺列 —— 不能说成"未取到快照"
    expect(hint).toContain('universe 快照有 5,208 只但缺关键列');
    expect(hint).toContain('universe_schema_incomplete');
  });
});

/* ==================== 二、真实对比日 ==================== */

describe('对比行：用真实对比日，不写死「较昨日」', () => {
  it('整页不得出现「较昨日」（对比日实测是 2026-09-17，相隔 13 天）', () => {
    renderCards();
    expect(screen.queryByText(/较昨日/)).toBeNull();
  });

  it('计数卡（股票数量 / 覆盖行业）不展示差值数字，显示「—（口径不同，不可比）」', () => {
    renderCards();
    const pool = compareRowOf('股票数量');
    expect(pool).toBe('对比 2026-09-17 —（口径不同，不可比）');
    // 旧实现的差值：5,000 - 2,431 = +2,569（由数据补全进度驱动，不是市场变化）
    expect(pool).not.toContain('2,569');

    const ind = compareRowOf('覆盖行业');
    expect(ind).toBe('对比 2026-09-17 —（口径不同，不可比）');
    expect(ind).not.toContain('-8');
  });

  it('不可比原因挂在 title 上，且写明前一份快照的池子规模', () => {
    renderCards();
    const title = cardOf('股票数量').querySelector('span[title]')?.getAttribute('title') ?? '';
    expect(title).toContain('2,431');
    expect(title).toContain('5,000');
    expect(title).toContain('不是市场变化');
  });

  it('榜单内百分比卡保留差值，标签同样是真实对比日', () => {
    renderCards();
    expect(compareRowOf('今日胜率')).toBe('对比 2026-09-17 +8.00%');
    expect(compareRowOf('平均涨跌幅')).toBe('对比 2026-09-17 +1.14%');
    expect(compareRowOf('平均 Score')).toBe('对比 2026-09-17 +0.0031');
  });

  it('无前一份快照 ⇒ 「无可比日 —」，仍不出现「较昨日」', () => {
    renderCards({ stats: { today: TODAY, prev: null, prev_date: null } });
    expect(compareRowOf('今日胜率')).toBe('无可比日 —');
    // 没有对比日就没有口径冲突 ⇒ 不得凭空多出「（口径不同，不可比）」
    expect(compareRowOf('股票数量')).toBe('无可比日 —');
    expect(compareRowOf('覆盖行业')).toBe('无可比日 —');
    expect(screen.queryByText(/较昨日/)).toBeNull();
  });
});

/* ==================== 三、强信号占比同口径 ==================== */

describe('强信号数量：分子分母同口径', () => {
  it('分母是榜单条目数（total），不是股票池（pool_size）', () => {
    renderCards();
    const card = cardOf('强信号数量');
    // ⚠️ 必须**精确取主值 span** 再全等比较：注入 `pool_size` 后渲染的是 `40 / 5000`
    //（该 span 不做千分位），若用 `toContain('40 / 50')` 会因前缀匹配而恒真
    //（本轮证伪 R5b 实测：该写法漏掉了这个缺陷）。
    const value = card.querySelector('span.num.text-xl');
    expect(value?.textContent).toBe('40 / 50');
  });

  it('占比 = strong_signal / total（40/50 = 80%）', () => {
    renderCards();
    expect(compareRowOf('强信号数量')).toBe('榜单内占比 80%');
  });

  it('池子远大于榜单时也不得被舍成 0%（旧口径 40/20,000 = 0.2% ⇒ 显示「0%」）', () => {
    renderCards({
      stats: {
        today: { ...TODAY, total: 200, pool_size: 20000, strong_signal: 40 },
        prev: PREV, prev_date: '2026-09-17',
      },
      items: makeItems(200, 40),
    });
    // 旧实现：40/20000*100 = 0.2 ⇒ toFixed(0) = '0%'；新实现：40/200 = 20%
    expect(compareRowOf('强信号数量')).toBe('榜单内占比 20%');
    expect(cardOf('强信号数量').textContent).toContain('40 / 200');
  });

  it('占比小于 1% 时显示「< 1%」，正数不得被舍成 0%（top_k 很大时会出现）', () => {
    renderCards({
      stats: {
        today: { ...TODAY, total: 5000, pool_size: 5208, strong_signal: 40 },
        prev: PREV, prev_date: '2026-09-17',
      },
      items: makeItems(5000, 40),
    });
    // 40/5000 = 0.8% ⇒ toFixed(0) 会显示 '1%'（更小的池子则 '0%'）
    expect(compareRowOf('强信号数量')).toBe('榜单内占比 < 1%');
  });

  it('占比是"档位构成"、不是价格方向 ⇒ 不得染涨跌色（t-up / t-down）', () => {
    renderCards();
    const span = cardOf('强信号数量').querySelector('span.num.font-medium');
    expect(span).not.toBeNull();
    expect(span!.className).not.toMatch(/t-(up|down)/);
    expect(span!.className).toContain('text-ink');
  });
});

/* ==================== 四、边界 ==================== */

describe('边界', () => {
  it('stats 为 null ⇒ 渲染 6 个骨架块，不崩', () => {
    renderCards({ stats: null });
    expect(document.querySelectorAll('.animate-pulse')).toHaveLength(6);
  });

  it('total = 0 ⇒ 占比显示「—」而不是 NaN%', () => {
    renderCards({
      stats: { today: { ...TODAY, total: 0, strong_signal: 0 }, prev: null, prev_date: null },
      items: [],
    });
    expect(compareRowOf('强信号数量')).toBe('榜单内占比 —');
    expect(cardOf('强信号数量').textContent).not.toContain('NaN');
  });
});
