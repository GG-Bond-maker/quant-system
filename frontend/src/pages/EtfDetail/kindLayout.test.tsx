/**
 * ETF 详情页：按**投资类型**组装专属界面（2026-10-05）。
 *
 * ## 被守的缺陷（用户截图实测）
 *
 * 511880（银华日利ETF，货币型）的详情页上，有四张卡写着「重仓股解析失败」
 * 「无法获取估值数据」「跟踪标的不可得」「暂无产业链数据」—— 而真相是
 * **货币基金本来就不持有股票、没有 PE/PB、不跟踪任何指数**。
 * 那不是"取不到"，是**不存在**。把它们渲染成"空的/失败的"卡片，等于在页面上
 * 留出"半页空洞"，并暗示用户"修修就有"。
 *
 * ## 修法：`not_applicable` 与 `unavailable` 严格分流
 *
 * | 状态              | 含义                    | 前端行为                          |
 * |-------------------|-------------------------|-----------------------------------|
 * | `not_applicable`  | 这类基金**永远不会有**  | **整张不渲染**（判据 10）          |
 * | `unavailable`     | 本该有、这次没取到      | 照常渲染 + 显示后端 `reason`       |
 *
 * 两条都要测：只测前者会漏掉"把真降级也一起藏了"，只测后者会漏掉"空洞仍在"。
 *
 * ## 为什么断言结构而不是像素
 * jsdom 没有布局引擎。这里守的是**结构不变量**：卡片的有无、标题文案、
 * 每一行**实际渲染了哪几张卡**（顺序即栅格顺序）、`grow` 落在哪一格上。
 * 真实几何由 Playwright 实测验收。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EtfDetail, EtfKind } from '@/types/etf';

import EtfDetailPage from './index';

/* ---------------- 共享 mock（与 degradedBlocks.test.tsx 同款） ---------------- */

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

// 权益类夹具会真的渲染 `TrackingErrorChart` ⇒ 它内部取 `echarts.graphic.LinearGradient`
vi.mock('@/lib/echarts', () => ({
  init: () => h.api,
  getInstanceByDom: () => h.api,
  graphic: { LinearGradient: class { constructor(..._a: unknown[]) { void _a; } } },
}));
vi.mock('@/api/etf', () => ({ etfApi: { detail: h.detail } }));

/* ---------------- 夹具 ---------------- */

/** 26 根日线（> 21 ⇒ 「风险度量」会真的算指标，卡片不是空态）。 */
function bars(n = 26) {
  return Array.from({ length: n }, (_, i) => ({
    date: `2026-01-${String(i + 1).padStart(2, '0')}`,
    open: 100 + i * 0.1, close: 100 + i * 0.1, high: 100.5 + i * 0.1, low: 99.5 + i * 0.1,
    volume: 1e8,
  }));
}

const FLOW_OK = {
  status: 'ok' as const,
  items: [
    { date: '2026-01-23', net_inflow: 1e8 },
    { date: '2026-01-24', net_inflow: -5e7 },
  ],
};

const SENTIMENT_OK = {
  status: 'ok' as const, score: 50, label: '中性', positive: 1, negative: 1,
  samples: [], basis: '基于公告标题关键词统计',
};

/** `not_applicable` 块的真实形态：后端 `_block_not_applicable` **只返回** `{status, reason}`。 */
function na(reason: string) {
  return { status: 'not_applicable' as const, reason };
}

interface Opts {
  holdings?: EtfDetail['blocks']['holdings'];
  tracking?: EtfDetail['blocks']['tracking'];
  valuation?: EtfDetail['blocks']['valuation'];
  chain?: EtfDetail['blocks']['chain'];
}

function makeDetail(kind: EtfKind, opts: Opts = {}): EtfDetail {
  const defaults: Record<EtfKind, Opts> = {
    货币型: {
      holdings: {
        status: 'ok', instrument: 'bond',
        holdings: {
          date: '2026-06-30',
          items: [{ code: '112600000', name: '26兴业银行CD119', ratio: 3.64, mv_yi: 1.0 }],
        },
        industry: null,
      },
      tracking: na('货币基金不跟踪指数，无跟踪误差与超额收益可言'),
      valuation: na('货币型基金不适用 PE/PB 估值'),
      chain: na('货币型基金不持有股票，无行业/产业链分布'),
    },
    债券型: {
      holdings: {
        status: 'ok', instrument: 'bond',
        holdings: {
          date: '2026-06-30',
          items: [{ code: '019833', name: '26国债07', ratio: 8.6, mv_yi: 1.23 }],
        },
        industry: null,
      },
      tracking: {
        status: 'unavailable',
        reason: '未取得该指数行情（上证10年期国债指数 不在中证指数名录内），暂无跟踪误差与超额对比',
        benchmark_code: null, benchmark_name: '上证10年期国债指数',
      },
      valuation: na('债券型基金不适用 PE/PB 估值'),
      chain: na('债券型基金不持有股票，无行业/产业链分布'),
    },
    商品型: {
      holdings: na('商品型基金不持有股票（标的为现货/期货合约）'),
      tracking: {
        status: 'unavailable',
        reason: '未取得该指数行情（黄金9999 不在中证指数名录内），暂无跟踪误差与超额对比',
        benchmark_code: null, benchmark_name: '黄金9999',
      },
      valuation: na('商品型基金不适用 PE/PB 估值'),
      chain: na('商品型基金不持有股票，无行业/产业链分布'),
    },
    跨境型: {
      holdings: { status: 'unavailable', reason: '非中国境内 ETF 暂无持仓明细' },
      tracking: { status: 'ok', benchmark_code: '000300', benchmark_name: '沪深300',
        points: [], tracking_error: 0.42 },
      valuation: { status: 'ok', pe_ttm: 12.3, pb: 1.4, index_code: '000300',
        index_name: '沪深300', pe_percentile: 35.2, pb_percentile: 22.1 },
      chain: { status: 'ok', items: [] },
    },
    股票型: {
      holdings: {
        status: 'ok', instrument: 'stock',
        holdings: {
          date: '2026-06-30',
          items: [{ code: '600519', name: '贵州茅台', ratio: 5.3, mv_yi: 2.0 }],
        },
        industry: {
          date: '2026-06-30',
          items: [{ industry: '制造业', ratio: 60.0, mv_yi: 10.0 },
                  { industry: '金融业', ratio: 20.0, mv_yi: 3.0 }],
        },
      },
      tracking: { status: 'ok', benchmark_code: '000300', benchmark_name: '沪深300',
        points: [], tracking_error: 0.42 },
      valuation: { status: 'ok', pe_ttm: 12.3, pb: 1.4, index_code: '000300',
        index_name: '沪深300', pe_percentile: 35.2, pb_percentile: 22.1 },
      chain: { status: 'ok', items: [{ chain: '金融', ratio: 20.0 }] },
    },
  };

  const merged = { ...defaults[kind], ...opts };
  const code = kind === '货币型' ? '511880' : kind === '商品型' ? '518880' : '510300';
  const name = kind === '货币型' ? '银华日利ETF' : kind === '商品型' ? '黄金ETF华安' : '沪深300ETF';

  return {
    code, name, country: 'cn', kind, status: 'ok',
    blocks: {
      header: {
        status: 'ok', name, code, country: 'cn',
        price: 100.0, pct: 0.01, amount: 1e8, size_yi: 1112.0,
        management_fee: 0.15, custody_fee: 0.05, tracking_index: null,
        manager: null, inception: null, kind,
      },
      kline: { status: 'ok', period: 'day', bars: bars() },
      holdings: merged.holdings!,
      tracking: merged.tracking!,
      valuation: merged.valuation!,
      flow: FLOW_OK,
      news: { status: 'ok', items: [] },
      sentiment: SENTIMENT_OK,
      chain: merged.chain!,
    },
  };
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

/**
 * 取某张卡的根节点 —— **必须按 `h2` 定位，不能直接 `getByText`**。
 *
 * 实测：K 线图自带一个副图标签 `<div class="ind-label">VOL(5,10) 成交量 1.00亿</div>`，
 * 于是「成交量」在 DOM 里有两处匹配，`getByText` 会抛
 * "Found multiple elements"。卡片标题恒为 `h2`（`SectionCard` 的约定），
 * 按它取才唯一。
 */
function cardOf(title: string): HTMLElement {
  const heading = screen.getAllByText(title).find((el) => el.tagName === 'H2');
  expect(heading, `未找到「${title}」卡片标题（h2）`).toBeTruthy();
  const el = heading!.closest('div.rounded-lg');
  expect(el, `「${title}」标题不在 SectionCard 里`).not.toBeNull();
  return el as HTMLElement;
}

/** 该卡所在的那一行栅格（`parentElement`）。 */
function rowOf(title: string): HTMLElement {
  return cardOf(title).parentElement as HTMLElement;
}

/** 一行里**实际渲染**的卡片标题，顺序即栅格顺序。 */
function rowTitles(title: string): string[] {
  return [...rowOf(title).children].map(
    (c) => c.querySelector('h2')?.textContent?.trim() ?? '(无标题)');
}

const GONE = (t: string) => screen.queryByText(t) === null;

beforeEach(() => {
  h.api.setOption.mockClear();
  h.api.getOption.mockClear();
  h.detail.mockReset();
  localStorage.clear();
});

/* ==================== 一、货币型：结构性不适用的卡整张消失 ==================== */

describe('货币 ETF：结构性不适用的卡整张消失，界面让给真正有的数据', () => {
  it('跟踪 / 估值 / 产业链 / 行业 六张卡都不渲染', async () => {
    await renderPage(makeDetail('货币型'));
    for (const t of ['跟踪误差分析', '跟踪与基准分析', '估值指标', '相关产业链',
                     '行业配置', '行业集中度']) {
      expect(GONE(t), `🔴 「${t}」是结构性不适用的卡，不该在货币 ETF 页面上出现`).toBe(true);
    }
  });

  it('持仓卡改标题为「重仓债券 Top 10」，并显示真实报告期', async () => {
    await renderPage(makeDetail('货币型'));
    expect(screen.queryByText('持仓 Top 10'), '🔴 货币基金不持有"重仓股"，标题必须改').toBeNull();
    const card = cardOf('重仓债券 Top 10');
    expect(card.textContent).toContain('26兴业银行CD119');
    expect(card.textContent).toContain('2026-06-30');
  });

  it('第一行整行消失：成交量下沉到第二行，第二行恰好四张卡（2×2，不留空列）', async () => {
    await renderPage(makeDetail('货币型'));
    expect(rowTitles('成交量')).toEqual(['成交量', '资金流向', '持仓集中度', '风险度量']);
    expect(rowOf('成交量').className.split(/\s+/), '🔴 4 张卡仍走 3 列 ⇒ 行尾留空列')
      .not.toContain('xl:grid-cols-3');
  });

  it('右列只剩「公告情绪」（估值 / 产业链都被裁掉）', async () => {
    await renderPage(makeDetail('货币型'));
    const aside = cardOf('公告情绪').parentElement as HTMLElement;
    expect([...aside.children].map((c) => c.querySelector('h2')?.textContent?.trim()))
      .toEqual(['公告情绪']);
  });
});

/* ==================== 二、商品型：持仓派生卡消失，真降级照常渲染 ==================== */

describe('商品 ETF：持仓派生卡消失，但真降级（unavailable）必须照常渲染', () => {
  it('持仓卡与持仓集中度都不渲染（商品基金不持有股票）', async () => {
    await renderPage(makeDetail('商品型'));
    expect(GONE('持仓 Top 10')).toBe(true);
    expect(GONE('重仓债券 Top 10')).toBe(true);
    expect(GONE('持仓集中度')).toBe(true);
  });

  it('跟踪误差分析仍在，并显示后端 reason —— 那是真降级，藏起来才是失真', async () => {
    await renderPage(makeDetail('商品型'));
    const card = cardOf('跟踪误差分析');
    expect(card.textContent).toContain('黄金9999');
    expect(card.textContent).toContain('不在中证指数名录内');
  });

  it('第二行恰好两张卡：资金流向 / 风险度量（成交量仍在第一行）', async () => {
    await renderPage(makeDetail('商品型'));
    expect(rowTitles('资金流向')).toEqual(['资金流向', '风险度量']);
    expect(rowTitles('跟踪误差分析')).toEqual(['跟踪误差分析', '跟踪与基准分析', '成交量']);
  });

  it('右列最后一格仍带 grow（InfoPanel 接住高度差，不留死区）', async () => {
    await renderPage(makeDetail('商品型'));
    const right = cardOf('ETF 概况 & 资讯').parentElement as HTMLElement;
    expect([...right.children].map((c) => c.querySelector('h2')?.textContent?.trim()))
      .toEqual(['ETF 概况 & 资讯']);
    expect(right.lastElementChild!.className.split(/\s+/), '🔴 持仓卡不渲染后没人吸收高度差')
      .toContain('grow');
  });
});

/* ==================== 三、债券型：跟踪是真降级、持仓是债券 ==================== */

describe('债券 ETF：跟踪块 `unavailable` 照常渲染并带上指数名', () => {
  it('跟踪两卡在，且说清"跟的是哪个指数、只是没拿到行情"', async () => {
    await renderPage(makeDetail('债券型'));
    const card = cardOf('跟踪误差分析');
    expect(card.textContent).toContain('上证10年期国债指数');
  });

  it('持仓是重仓债券、行业两卡不渲染、估值与产业链不渲染', async () => {
    await renderPage(makeDetail('债券型'));
    expect(cardOf('重仓债券 Top 10').textContent).toContain('26国债07');
    expect(GONE('行业配置')).toBe(true);
    expect(GONE('估值指标')).toBe(true);
    expect(GONE('相关产业链')).toBe(true);
  });
});

/* ==================== 四、权益类：与改造前逐项一致（对照，防止误伤） ==================== */

describe('权益类 ETF：卡片构成与改造前一致', () => {
  it('两行各三张卡，标题不变', async () => {
    await renderPage(makeDetail('股票型'));
    expect(rowTitles('跟踪误差分析')).toEqual(['跟踪误差分析', '跟踪与基准分析', '成交量']);
    expect(rowTitles('资金流向')).toEqual(['资金流向', '持仓集中度', '风险度量']);
    expect(cardOf('持仓 Top 10').textContent).toContain('贵州茅台');
  });

  it('上区右列最后一格仍带 grow（行业对接住高度差）', async () => {
    await renderPage(makeDetail('股票型'));
    const right = cardOf('ETF 概况 & 资讯').parentElement as HTMLElement;
    expect(right.lastElementChild!.className.split(/\s+/)).toContain('grow');
  });
});

/* ==================== 五、真降级不得被一起藏掉（反向对照） ==================== */

describe('真降级（unavailable）：卡片必须照常渲染并给出原因', () => {
  it('holdings unavailable ⇒ 持仓卡在、标题不变、显示原因；行业两卡也在', async () => {
    await renderPage(makeDetail('股票型', {
      holdings: { status: 'unavailable', reason: '持仓数据源超时' },
    }));
    // 标题未被误改成"重仓债券"（instrument 缺失 ⇒ 按权益处理）
    const card = cardOf('持仓 Top 10');
    expect(card.textContent).toContain('持仓数据源超时');
    expect(GONE('行业配置'), '🔴 真降级被当成"结构性不适用"藏掉了').toBe(false);
    expect(GONE('行业集中度')).toBe(false);
  });

  it('chain unavailable ⇒ 相关产业链卡在并显示原因', async () => {
    await renderPage(makeDetail('股票型', {
      chain: { status: 'unavailable', reason: '暂无持仓行业数据' },
    }));
    expect(cardOf('相关产业链').textContent).toContain('暂无持仓行业数据');
  });
});
