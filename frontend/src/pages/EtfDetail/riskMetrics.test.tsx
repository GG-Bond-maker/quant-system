/**
 * 「风险度量」新增卡 + 「持仓集中度」收窄为 1 列（2026-10-04）。
 *
 * ## 背景
 * 用户指出「持仓集中度」卡所占位置过大（实测：`md:col-span-2` = 723px 宽，内容仅
 * 114px 高，**卡底 191px 死区**；同页其余 13 张卡的底部空白恒为 12px = body 内边距）。
 * 处置：收窄为 1 列，并在腾出的位置新增「风险度量」卡。
 *
 * ## 期望值从哪来（关键：不能由被测实现自证）
 * 夹具是 26 个**确定性**收盘价（见 `CLOSES`），下方所有期望数字都由**独立实现**
 * （Python 逐条复算）得出后**写死**在断言里 —— 不是跑一遍组件再把输出抄进来。
 * 这样"组件算错"才会失败，而不是"组件算什么都通过"。
 *
 * ## 为什么断言结构 / option 而不是像素
 * jsdom 没有布局引擎（`getBoundingClientRect()` 恒 0）⇒ "卡底有没有死区"无法由渲染
 * 断言证明。故这里守**结构不变量**（卡的列宽类、内容根节点的 flex 类、第二行的卡片
 * 构成），真实几何由 Playwright 实测验收：1600px 下 14 张卡的底部空白**全部为 12px**。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EtfDetail, EtfHoldingItem, EtfKlineBar } from '@/types/etf';

import EtfDetailPage from './index';

/* ---------------- 共享 mock（与 klineCardParity.test.tsx 同款） ---------------- */

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

// ⚠️ 不 mock `KLineChart` —— 风险度量的输入正是本页的 `klineBars`。
vi.mock('@/lib/echarts', () => ({
  init: () => h.api,
  getInstanceByDom: () => h.api,
}));
vi.mock('@/api/etf', () => ({ etfApi: { detail: h.detail } }));

/* ---------------- 夹具 ---------------- */

/**
 * 26 个确定性收盘价（⇒ 25 个日收益，刚好够 20 日窗口）。
 * 独立复算（Python）结果 —— 见各用例里写死的期望值：
 *   年化波动率 30.86% / 近 20 日 28.68% / 下行波动率 13.73%
 *   最大回撤 −4.85% / 当前回撤 −0.88% / 上行日占比 56.0%
 */
const CLOSES = [
  100, 102, 101, 103, 99, 98, 100, 104, 105, 103, 102, 106, 108,
  107, 109, 110, 108, 106, 105, 107, 109, 111, 110, 112, 114, 113,
];

/** 用给定收盘价造 K 线；`close === open`，只有 close 参与风险计算。 */
function barsFrom(closes: number[]): EtfKlineBar[] {
  return closes.map((c, i) => ({
    date: `2026-01-${String(i + 1).padStart(2, '0')}`,
    open: c, close: c, high: c + 1, low: c - 1, volume: 1e8,
  }));
}

/** 造持仓明细：`ratio` 依次递减（与后端源站顺序一致）。 */
function holdingsFrom(ratios: number[]): EtfHoldingItem[] {
  return ratios.map((r, i) => ({
    code: `60000${i}`, name: `股票${i + 1}`, ratio: r, mv_yi: r * 10,
  }));
}

function makeDetail(opts: {
  closes?: number[];
  ratios?: number[];
} = {}): EtfDetail {
  return {
    code: '510300',
    name: '沪深300ETF',
    country: 'cn',
    status: 'ok',
    blocks: {
      header: {
        status: 'ok', name: '沪深300ETF', code: '510300', country: 'cn',
        price: 4.12, pct: 0.73, amount: 1.2e9, size_yi: 1300.5,
        management_fee: 0.15, custody_fee: 0.05, tracking_index: '沪深300',
        manager: '华泰柏瑞基金', inception: '2012-05-04',
      },
      kline: { status: 'ok', period: 'day', bars: barsFrom(opts.closes ?? CLOSES) },
      holdings: {
        status: 'ok',
        holdings: { date: '2026-06-30', items: holdingsFrom(opts.ratios ?? [4.75, 3.55, 2.89, 2.52, 1.86, 1.8, 1.73, 1.49, 1.41, 1.25]) },
        industry: { date: '2026-06-30', items: [] },
      },
      tracking: {
        status: 'ok', benchmark_code: '000300', benchmark_name: '沪深300',
        points: [], tracking_error: 0.42,
      },
      valuation: {
        status: 'ok', pe_ttm: 12.3, pb: 1.4, index_code: '000300',
        index_name: '沪深300', pe_percentile: 35.2, pb_percentile: 22.1,
      },
      flow: { status: 'ok', items: [] },
      news: { status: 'ok', items: [] },
      sentiment: {
        status: 'ok', score: 50, label: '中性', positive: 2, negative: 2,
        samples: [], basis: '基于公告标题关键词统计',
      },
      chain: { status: 'ok', items: [] },
    },
  };
}

/**
 * 渲染整页，等一个**与被守契约无关**的稳定标记（页头「返回 ETF 中心」按钮）。
 * 若等被守对象本身，注入缺陷会让用例在等待阶段就挂，掩盖真实信号（判据 31）。
 */
async function renderPage(detail: EtfDetail = makeDetail()) {
  h.detail.mockResolvedValue(detail);
  const r = render(
    <MemoryRouter initialEntries={['/etf/510300']}>
      <Routes>
        <Route path="/etf/:code" element={<EtfDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText('返回 ETF 中心');
  return r;
}

/** 取某张卡（`SectionCard` 自带 `rounded-lg`）的根节点。 */
function cardOf(title: string): HTMLElement {
  const el = screen.getByText(title).closest('div.rounded-lg');
  expect(el, `未找到「${title}」卡片根节点`).not.toBeNull();
  return el as HTMLElement;
}

/**
 * 回撤图的 option —— 按 `series[].name` 定位，**不按 setOption 调用顺序取**
 * （同页还有 K 线 / 甜甜圈 / 成交量 / 资金流向四张图，顺序会随渲染变化）。
 */
function ddOption() {
  const hit = h.api.setOption.mock.calls
    .map((c) => c[0] as { yAxis?: { max?: number }; series?: Array<{ name?: string; data?: number[] }> })
    .find((o) => o && Array.isArray(o.series) && o.series.some((s) => s?.name === '距区间最高点'));
  expect(hit, '未捕获到回撤曲线的 setOption（series.name = 距区间最高点）').toBeTruthy();
  return hit as { yAxis: { max?: number }; series: Array<{ name: string; data: number[] }> };
}

/** 卡片内 指标名 → 数值 的文本对（卡内每行 = 一个含 2 个 span 的 div）。 */
function statRows(card: HTMLElement): Array<[string, string]> {
  return [...card.querySelectorAll('div')]
    .filter((d) => d.children.length === 2
      && d.children[0].tagName === 'SPAN' && d.children[1].tagName === 'SPAN')
    .map((d) => [d.children[0].textContent!.trim(), d.children[1].textContent!.trim()]);
}

beforeEach(() => {
  h.api.setOption.mockClear();
  h.api.getOption.mockClear();
  h.detail.mockReset();
  localStorage.clear();
});

/* ==================== 风险度量：指标正确性 ==================== */

describe('风险度量（新增卡）：六项指标与独立复算一致', () => {
  it('窗口 = 可用收益日数（不是硬写 252），六项指标逐项对上', async () => {
    await renderPage();
    const rows = statRows(cardOf('风险度量'));
    expect(Object.fromEntries(rows)).toEqual({
      年化波动率: '30.86%',
      最大回撤: '-4.85%',
      '近 20 日年化波动率': '28.68%',
      当前回撤: '-0.88%',
      下行波动率: '13.73%',
      上行日占比: '56.0%',
    });
  });

  it('口径披露带**真实**窗口日期与交易日数，并说明前复权与 320 根上限', async () => {
    await renderPage();
    const card = cardOf('风险度量');
    const text = card.textContent ?? '';
    expect(text).toContain('2026-01-01');
    expect(text).toContain('2026-01-26');
    expect(text).toContain('25 个交易日');
    expect(text).toContain('前复权');
    expect(text).toContain('320');
  });

  it('回撤曲线：y 轴钉在 0、点数 = 窗口收盘数、极值 = 最大回撤、末点 = 当前回撤', async () => {
    await renderPage();
    const opt = ddOption();
    // 回撤恒 ≤ 0 ⇒ 零轴必须在**顶端**（否则曲线会被读成"在 0 附近波动"）
    expect(opt.yAxis.max, '🔴 回撤图 y 轴 max 不是 0').toBe(0);
    const data = opt.series[0].data;
    expect(data.length, '回撤序列应含窗口每一根收盘（= 收盘数，不是收益数）')
      .toBe(CLOSES.length);
    expect(Math.min(...data)).toBeCloseTo(-4.854, 2);
    expect(data[data.length - 1]).toBeCloseTo(-0.877, 2);
  });
});

/* ==================== 风险度量：降级与边界 ==================== */

describe('风险度量：数据不足时不许编指标', () => {
  it('K 线不足 21 个交易日 ⇒ 走空态，不显示任何指标行', async () => {
    await renderPage(makeDetail({ closes: CLOSES.slice(0, 20) }));
    expect(screen.getByText(/K 线不足 21 个交易日/)).toBeInTheDocument();
    expect(statRows(cardOf('风险度量'))).toEqual([]);
  });

  it('全程零波动（全平）⇒ 波动率/回撤为 0 而不是空白，且**下行波动率整行消失**', async () => {
    await renderPage(makeDetail({ closes: Array(26).fill(100) }));
    const rows = statRows(cardOf('风险度量'));
    const map = Object.fromEntries(rows);
    expect(map['年化波动率']).toBe('0.00%');
    expect(map['最大回撤']).toBe('0.00%');
    expect(map['上行日占比']).toBe('0.0%');
    // 负收益日 = 0 < 2 ⇒ 样本不足，该行必须**结构上消失**，不能填 `—` 冒充
    expect(rows.map(([k]) => k)).not.toContain('下行波动率');
  });
});

/* ==================== 持仓集中度：收窄 + 补满 ==================== */

describe('持仓集中度：由 2 列收窄为 1 列并撑满卡高', () => {
  it('不再占 md:col-span-2（原先 723px 宽只放 114px 内容 ⇒ 191px 死区）', async () => {
    await renderPage();
    const cls = cardOf('持仓集中度').className;
    expect(cls, '🔴 又变回 2 列宽').not.toContain('md:col-span-2');
  });

  it('内容根节点带 flex-1 + justify-between（否则卡底会留死区）', async () => {
    await renderPage();
    const card = cardOf('持仓集中度');
    // SectionCard 的 body 是最后一个子 div；被守的是 body 里那层内容根节点
    const body = card.children[card.children.length - 1];
    const inner = body.firstElementChild as HTMLElement;
    const cls = inner.className.split(/\s+/);
    expect(cls, '🔴 内容根节点缺 flex-1 ⇒ 不会撑满 body').toContain('flex-1');
    expect(cls, '🔴 内容根节点缺 justify-between ⇒ 内容挤在顶部、底部留空带')
      .toContain('justify-between');
  });

  it('下区第二行恰好三张卡：资金流向 / 持仓集中度 / 风险度量', async () => {
    await renderPage();
    const row = cardOf('持仓集中度').parentElement as HTMLElement;
    const titles = [...row.children].map(
      (c) => c.querySelector('h2')?.textContent?.trim() ?? '(无标题)');
    expect(titles).toEqual(['资金流向', '持仓集中度', '风险度量']);
  });
});

describe('持仓集中度：前 N 大只在披露数够时出现', () => {
  it('披露 10 只 ⇒ 前 3 大 / 前 5 大 / 头部 10 只 三行齐全', async () => {
    await renderPage();
    const rows = statRows(cardOf('持仓集中度'));
    const map = Object.fromEntries(rows);
    expect(map['前 3 大合计']).toBe('11.19%');   // 4.75+3.55+2.89
    expect(map['前 5 大合计']).toBe('15.57%');   // +2.52+1.86
    expect(map['头部 10 只合计']).toBe('23.25%');
  });

  it('只披露 4 只 ⇒ 「前 5 大合计」整行消失（不能把 4 只的和冒充前 5 大）', async () => {
    await renderPage(makeDetail({ ratios: [4.75, 3.55, 2.89, 2.52] }));
    const rows = statRows(cardOf('持仓集中度'));
    const keys = rows.map(([k]) => k);
    expect(keys).toContain('前 3 大合计');
    expect(keys, '🔴 只有 4 只却显示了「前 5 大合计」').not.toContain('前 5 大合计');
    expect(keys).toContain('头部 4 只合计');
  });
});

describe('持仓集中度：脚注不得把 markdown 语法当文案（判据 50 回归）', () => {
  it('卡内可见文本不含 `**`', async () => {
    await renderPage();
    for (const t of ['持仓集中度', '风险度量']) {
      const text = cardOf(t).textContent ?? '';
      expect(text.includes('**'), `🔴 「${t}」卡里出现了原样渲染的 markdown 星号`).toBe(false);
    }
  });
});
