/**
 * 「区间表现」新增卡（2026-10-05）。
 *
 * ## 被守的缺陷
 * 用户截图：「对于黄金型的，右边空白的地方过多」。实测（Playwright，1600px 视口）
 * 上区右列死区：**商品型 703px / 货币型 465px / 债券型 334px**（同页其余卡恒 12px）。
 * 根因是左列恒为 869px（K 线卡定高 666 + 新闻卡 193 + 间距 10），而非权益类右列缺了
 * 「行业对」（307px）这一块 ⇒ 差额被 `grow` 全部灌进右列最后一张卡成为死区。
 *
 * 处置：补一张只由本页**已取到**的 `kline.bars` 派生的「区间表现」卡，并让它的
 * **曲线**承担弹性高度（`min-h-0 flex-1`）。
 *
 * ## 期望值从哪来（不能由被测实现自证）
 * 夹具是 300 个**确定性整数**收盘价（`100 + (i%7)*5 + ⌊i/7⌋*2`）。下方所有期望数字
 * 都由**独立实现**（Python `datetime` + 逐条公式，另在 Node 侧用同一公式交叉核对过）
 * 算出后**写死**在断言里 —— 不是跑一遍组件再把输出抄进来。
 * 这样"组件算错"才会失败，而不是"组件算什么都通过"。
 *
 * ## 为什么断言结构 / option 而不是像素
 * jsdom 没有布局引擎（`getBoundingClientRect()` 恒 0）⇒ "卡底有没有死区"无法由渲染
 * 断言证明。这里守**结构不变量**（卡的列位与 `grow` 落点、行的有无、曲线 option 的
 * 归一化与零轴），真实几何由 Playwright 实测验收（三类页面所有卡 slack ≈ 12px）。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EtfDetail, EtfKlineBar } from '@/types/etf';

import EtfDetailPage from './index';

/* ---------------- 共享 mock（与 kindLayout.test.tsx 同款） ---------------- */

const h = vi.hoisted(() => {
  const api = {
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
    dispatchAction: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    getOption: vi.fn(() => ({ dataZoom: [{ start: 0, end: 100 }] })),
  };
  return { api, detail: vi.fn() };
});

// 本卡由 K 线派生 ⇒ 不 mock `KLineChart`（它的输入正是被守序列）
vi.mock('@/lib/echarts', () => ({
  init: () => h.api,
  getInstanceByDom: () => h.api,
  graphic: { LinearGradient: class { constructor(..._a: unknown[]) { void _a; } } },
}));
vi.mock('@/api/etf', () => ({ etfApi: { detail: h.detail } }));

/* ---------------- 夹具 ---------------- */

/** 300 个确定性整数收盘价（0~299）：`100 + (i%7)*5 + ⌊i/7⌋*2`。 */
function closes300(): number[] {
  return Array.from({ length: 300 }, (_, i) => 100 + (i % 7) * 5 + Math.floor(i / 7) * 2);
}

/** 连续自然日日期（仅用于展示与切片，本卡不做日期运算）。 */
function dates(n: number): string[] {
  const out: string[] = [];
  const d = new Date(Date.UTC(2024, 0, 1));
  for (let i = 0; i < n; i++) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

function barsFrom(closes: number[]): EtfKlineBar[] {
  const ds = dates(closes.length);
  return closes.map((c, i) => ({
    date: ds[i], open: c, close: c, high: c + 1, low: c - 1, volume: 1e8,
  }));
}

/**
 * 商品型夹具（`holdings` / `valuation` / `chain` 全 `not_applicable`）
 * ⇒ 上区右列只剩「概况」⇒ `rangeApplicable === true`。
 */
function makeCommodity(closes: number[]): EtfDetail {
  const na = (reason: string) => ({ status: 'not_applicable' as const, reason });
  return {
    code: '518880', name: '黄金ETF华安', country: 'cn', kind: '商品型', status: 'ok',
    blocks: {
      header: {
        status: 'ok', name: '黄金ETF华安', code: '518880', country: 'cn',
        price: 8.633, pct: 1.34, amount: 2.7e9, size_yi: 1024.4,
        management_fee: 0.5, custody_fee: 0.1, tracking_index: null,
        manager: null, inception: null, kind: '商品型',
      },
      kline: { status: 'ok', period: 'day', bars: barsFrom(closes) },
      holdings: na('商品型基金不持有股票（标的为现货/期货合约）'),
      tracking: {
        status: 'unavailable',
        reason: '未匹配到该指数的行情（黄金9999），暂无跟踪误差与超额对比',
        benchmark_code: null, benchmark_name: '黄金9999',
      },
      valuation: na('商品型基金不适用 PE/PB 估值'),
      flow: { status: 'ok', items: [] },
      news: { status: 'ok', items: [] },
      sentiment: {
        status: 'ok', score: 50, label: '中性', positive: 0, negative: 0,
        samples: [], basis: '基于公告标题关键词统计（自建词典）',
      },
      chain: na('商品型基金不持有股票，无行业/产业链分布'),
    },
  };
}

/** 股票型夹具（`instrument='stock'` ⇒ 有行业对 ⇒ **不**该出现「区间表现」）。 */
function makeEquity(closes: number[]): EtfDetail {
  const d = makeCommodity(closes);
  d.code = '510300';
  d.name = '沪深300ETF';
  d.kind = '股票型';
  d.blocks.header.kind = '股票型';
  d.blocks.holdings = {
    status: 'ok', instrument: 'stock',
    holdings: {
      date: '2026-06-30',
      items: [{ code: '600519', name: '贵州茅台', ratio: 5.3, mv_yi: 2.0 }],
    },
    industry: { date: '2026-06-30', items: [{ industry: '制造业', ratio: 60.0, mv_yi: 10.0 }] },
  };
  d.blocks.valuation = {
    status: 'ok', pe_ttm: 12.3, pb: 1.4, index_code: '000300',
    index_name: '沪深300', pe_percentile: 35.2, pb_percentile: 22.1,
  };
  d.blocks.chain = { status: 'ok', items: [] };
  return d;
}

/**
 * 渲染整页，等一个**与被守契约无关**的稳定标记（页头「返回 ETF 中心」按钮）。
 * 若等被守对象本身，注入缺陷会让用例在等待阶段就挂，掩盖真实信号（判据 31）。
 */
async function renderPage(detail: EtfDetail) {
  h.detail.mockResolvedValue(detail);
  const r = render(
    <MemoryRouter initialEntries={[`/etf/${detail.code}`]}>
      <Routes>
        <Route path="/etf/:code" element={<EtfDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText('返回 ETF 中心');
  return r;
}

/** 取某张卡（`SectionCard` 恒带 `rounded-lg`）的根节点。 */
function cardOf(title: string): HTMLElement {
  const heading = screen.getAllByText(title).find((el) => el.tagName === 'H2');
  expect(heading, `未找到「${title}」卡片标题（h2）`).toBeTruthy();
  const el = heading!.closest('div.rounded-lg');
  expect(el, `「${title}」标题不在 SectionCard 里`).not.toBeNull();
  return el as HTMLElement;
}

/** 卡内 指标名 → 数值 的文本对（每行 = 一个含 2 个 `span` 的 `div`）。 */
function statRows(card: HTMLElement): Array<[string, string]> {
  return [...card.querySelectorAll('div')]
    .filter((d) => d.children.length === 2
      && d.children[0].tagName === 'SPAN' && d.children[1].tagName === 'SPAN')
    .map((d) => [d.children[0].textContent!.trim(), d.children[1].textContent!.trim()]);
}

/**
 * 区间累计收益曲线的 option —— 按 `series[].name` 定位，**不按 setOption 调用顺序取**
 * （同页还有 K 线 / 甜甜圈 / 成交量 / 资金流向 / 回撤 五张图，顺序会随渲染变化）。
 */
function cumOption() {
  const hit = h.api.setOption.mock.calls
    .map((c) => c[0] as {
      yAxis?: unknown;
      series?: Array<{ name?: string; data?: number[]; markLine?: { data?: unknown[] } }>;
    })
    .find((o) => o && Array.isArray(o.series)
      && o.series.some((s) => s?.name === '区间累计收益'));
  expect(hit, '未捕获到区间累计收益曲线的 setOption（series.name = 区间累计收益）')
    .toBeTruthy();
  return hit as {
    series: Array<{ name: string; data: number[]; markLine?: { data?: unknown[] } }>;
  };
}

/** 上区右列（`InfoPanel` 所在的 flex 列）。 */
function rightColumn(): HTMLElement {
  return cardOf('ETF 概况 & 资讯').parentElement as HTMLElement;
}

function rightTitles(): string[] {
  return [...rightColumn().children].map(
    (c) => c.querySelector('h2')?.textContent?.trim() ?? '(无标题)');
}

/** 右列里带 `grow` 的卡片标题（**必须恰好一个**，见下）。 */
function growTitles(): string[] {
  return [...rightColumn().children]
    .filter((c) => c.className.split(/\s+/).includes('grow'))
    .map((c) => c.querySelector('h2')?.textContent?.trim() ?? '(无标题)');
}

beforeEach(() => {
  h.api.setOption.mockClear();
  h.api.getOption.mockClear();
  h.detail.mockReset();
  localStorage.clear();
});

/* ==================== 一、数值正确性（与独立复算逐位相符） ==================== */

describe('区间表现：多周期涨跌幅与独立复算一致', () => {
  it('300 根日线 ⇒ 五个周期行齐全，数值逐项对上', async () => {
    await renderPage(makeCommodity(closes300()));
    const rows = Object.fromEntries(statRows(cardOf('区间表现')));
    expect(rows).toEqual({
      '近 5 日': '+13.59%',
      '近 20 日': '+0.48%',
      '近 60 日': '+20.81%',
      '近 120 日': '+22.94%',
      '近 250 日': '+83.33%',
    });
  });

  it('区间分位：低 114 / 高 212 / 当前位置 96.9%，填色宽度同值', async () => {
    await renderPage(makeCommodity(closes300()));
    const card = cardOf('区间表现');
    expect(card.textContent).toContain('区间低 114.00');
    expect(card.textContent).toContain('区间高 212.00');
    expect(card.textContent).toContain('当前位置 96.9%');
    const fill = card.querySelector('div[style*="width"]') as HTMLElement | null;
    expect(fill, '未找到分位填色条').not.toBeNull();
    expect(fill!.style.width).toBe('96.9%');
  });

  it('曲线：点数 = 窗口日线数、首点恒 0%、末点 = 区间累计、零轴钉在 0', async () => {
    await renderPage(makeCommodity(closes300()));
    const opt = cumOption();
    const data = opt.series[0].data;
    // 窗口 = min(253, 可用根数)；300 根 ⇒ 253
    expect(data.length, '曲线点数应等于窗口日线根数').toBe(253);
    expect(data[0], '首点必须是窗口首根收盘归一化后的 0%').toBeCloseTo(0, 10);
    expect(data[data.length - 1]).toBeCloseTo(52.5547, 3);   // 209/137 − 1
    // 零轴是"区间内赚/亏"的分界，缺了它曲线只像"在 0 附近波动"
    const zero = opt.series[0].markLine?.data as Array<{ yAxis?: number }> | undefined;
    expect(zero?.[0]?.yAxis).toBe(0);
  });

  it('脚注带**真实**窗口日期与日线根数，并说明前复权与 320 根上限', async () => {
    await renderPage(makeCommodity(closes300()));
    const text = cardOf('区间表现').textContent ?? '';
    expect(text).toContain('2024-02-17');
    expect(text).toContain('2024-10-26');
    expect(text).toContain('253 根日线');
    expect(text).toContain('前复权');
    expect(text).toContain('320');
    // 口径差异必须写明：本卡是归一化，K 线是绝对价格
    expect(text).toContain('归一化');
  });
});

/* ==================== 二、边界：不许编数、不许留空占位 ==================== */

describe('区间表现：数据不足 / 退化情形', () => {
  it('26 根 ⇒ 只有 近 5 日 / 近 20 日 两行；60/120/250 三行**结构上消失**', async () => {
    await renderPage(makeCommodity(closes300().slice(0, 26)));
    const keys = statRows(cardOf('区间表现')).map(([k]) => k);
    expect(keys).toEqual(['近 5 日', '近 20 日']);
    // 不能填 `—` 冒充（判据 10：缺失必须在结构上消失）
    expect(cardOf('区间表现').textContent).not.toContain('近 60 日');
    expect(cardOf('区间表现').textContent).not.toContain('近 250 日');
  });

  it('26 根的数值同样与独立复算一致（近 5 日 −5.97% / 近 20 日 +0.80%）', async () => {
    await renderPage(makeCommodity(closes300().slice(0, 26)));
    expect(Object.fromEntries(statRows(cardOf('区间表现'))))
      .toEqual({ '近 5 日': '-5.97%', '近 20 日': '+0.80%' });
  });

  it('K 线不足 21 根 ⇒ 整卡走空态，一行指标都不出', async () => {
    await renderPage(makeCommodity(closes300().slice(0, 20)));
    // ⚠️ 不能对整页用 `getByText`：同页「风险度量」也写着「K 线不足 21 个交易日」
    // （两卡共用同一门槛，是有意的），整页查询会命中多个。故**限定在本卡内**断言。
    expect(cardOf('区间表现').textContent).toContain('K 线不足 21 个交易日');
    expect(statRows(cardOf('区间表现'))).toEqual([]);
  });

  it('全程零波动 ⇒ 所有周期 0.00%，且**分位区整块消失**（不填 0%/50% 假值）', async () => {
    await renderPage(makeCommodity(Array(26).fill(100)));
    const card = cardOf('区间表现');
    const rows = statRows(card);
    expect(rows.length).toBeGreaterThan(0);
    // 符号规则与全站 `fmtPct` 一致：0 是「0.00%」而不是「+0.00%」
    expect(new Set(rows.map(([, v]) => v))).toEqual(new Set(['0.00%']));
    // 高低相等 ⇒ 分位不可得 ⇒ 整块消失
    expect(card.textContent).not.toContain('当前位置');
    expect(card.querySelector('div[style*="width"]'),
      '🔴 区间无波动时不该画分位条').toBeNull();
  });

  it('卡内可见文本不含 `**`（判据 50：markdown 语法会被原样渲染）', async () => {
    await renderPage(makeCommodity(closes300()));
    expect(cardOf('区间表现').textContent!.includes('**')).toBe(false);
  });
});

/* ==================== 三、布局不变量：grow 恰好一个、落在最后 ==================== */

describe('上区右列：`grow` 必须恰好落在最后一张卡上', () => {
  it('商品型：右列 = 概况 + 区间表现，只有「区间表现」带 grow', async () => {
    await renderPage(makeCommodity(closes300()));
    expect(rightTitles()).toEqual(['ETF 概况 & 资讯', '区间表现']);
    expect(growTitles(), '🔴 两张卡同时带 grow 会把剩余高度对半分、各留一半死区')
      .toEqual(['区间表现']);
    expect(rightColumn().lastElementChild!.className.split(/\s+/)).toContain('grow');
  });

  it('曲线容器带 min-h-0 flex-1（被拉伸时由**曲线**吃掉高度，而不是卡底留白）', async () => {
    await renderPage(makeCommodity(closes300()));
    const card = cardOf('区间表现');
    const body = card.children[card.children.length - 1];
    const root = body.firstElementChild as HTMLElement;
    expect(root.className.split(/\s+/)).toContain('flex-1');
    // 曲线区是内容根节点的**倒数第二个**子节点（最后一个是脚注 p）
    const chartBox = root.children[root.children.length - 2] as HTMLElement;
    const cls = chartBox.className.split(/\s+/);
    expect(cls, '🔴 曲线区缺 flex-1 ⇒ 多出来的高度会变成卡底死区').toContain('flex-1');
    expect(cls, '🔴 曲线区缺 min-h-0 ⇒ flex 子项不会收缩，会撑破卡片').toContain('min-h-0');
  });

  it('权益类（有行业对）**不**渲染「区间表现」，grow 仍在行业对上', async () => {
    await renderPage(makeEquity(closes300()));
    // ⚠️ 「行业对」是**一个** `grid` 子节点（内含「行业配置」「行业集中度」两张卡），
    // 故按直接子节点取标题只会取到它内部第一张卡的标题。
    expect(rightTitles()).toEqual(['ETF 概况 & 资讯', '持仓 Top 10', '行业配置']);
    expect(screen.queryByText('区间表现')).toBeNull();
    expect(rightColumn().lastElementChild!.className.split(/\s+/)).toContain('grow');
  });
});
