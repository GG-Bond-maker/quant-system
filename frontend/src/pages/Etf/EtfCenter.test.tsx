/**
 * ETF 中心页 —— 三处改动的回归测试（2026-10-03）。
 *
 * ## 被守的缺陷
 *
 * ### 一、资金流向 / 我的自选ETF 的「代码」列是**假可交互**
 * 两处都是 `<td className="num text-brand-600">{code}</td>` —— 纯文本，却被染成品牌蓝，
 * 视觉上与旁边真能点的热门榜代码**一模一样**。用户会去点，然后以为坏了。
 * 同页正确的写法是 `<Link to={`/etf/${code}`}>`（热门榜 L636 / ETF列表 L851）。
 *
 * ### 二、热门榜「查看全部」是**自链接**
 * 原实现 `<Link to="/etf">`，而**本页本身就是 `/etf`** ⇒ 点击等于导航到当前路由，
 * React Router 匹配同一条 route、组件不重挂载 ⇒ 界面上什么都不发生。
 * 改为页内滚动到本页底部「ETF 列表」卡片（`#etf-list`），并给落点加短暂高亮。
 *
 * ### 三、规模柱误用共享类别色
 * `CATEGORY_COLORS.C1`（近黑）是**共享**常量（MA5 / KDJ-K / RSI6 / DIF 都在用），
 * 改它会让全站图表一起变色 ⇒ 本图改用主题品牌蓝 `chartPalette().BRAND`。
 *
 * ## 为什么能在 jsdom 里测
 *
 * 断言的都是 **DOM 状态 / 捕获到的 option 纯数据字段**，与布局引擎无关：
 * `href`、`className`、`setOption` 收到的 `itemStyle.color`。
 * ⚠️ 唯独 `scrollIntoView` 是 jsdom **未实现**的方法（调用会抛 TypeError），
 * 故本文件显式打桩 —— 这恰恰也证明了"点击真的走到了滚动这一步"。
 *
 * ⚠️ 本文件不得出现 hex 字面量：`style:check` 会扫 `src/pages` 下所有 `.tsx`（含测试），
 * 硬编码色值一律判违规。故色值断言一律与 `chartPalette()` 比较。
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import type { EtfFlowItem, EtfItem, EtfListResult, EtfOverview, EtfScale } from '@/types/etf';

import EtfCenter from './index';

/* ---------------- 桩 ---------------- */

const h = vi.hoisted(() => ({ setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() }));

vi.mock('@/lib/echarts', () => ({
  init: () => ({ setOption: h.setOption, dispose: h.dispose, resize: h.resize }),
}));

// 子组件与本页这三处改动无关，替换成空渲染，避免测试被它们各自的数据依赖带偏
vi.mock('./PerformanceChart', () => ({ default: () => null }));
vi.mock('./OverviewCards', () => ({ default: () => null }));
vi.mock('./FilterPanel', () => ({
  default: () => null,
  EMPTY_ETF_FILTERS: {
    etype: 'all', index: '', manager: '', min_size: '', max_size: '',
    inception_from: '', inception_to: '', q: '',
  },
}));

const api = vi.hoisted(() => ({
  overview: vi.fn(), overviewSeries: vi.fn(), list: vi.fn(), hot: vi.fn(),
  performance: vi.fn(), scale: vi.fn(), flow: vi.fn(), detail: vi.fn(),
  arbitrage: vi.fn(), allocation: vi.fn(),
}));

vi.mock('@/api/etf', () => ({ etfApi: api }));

/* ---------------- 夹具 ---------------- */

const item = (code: string, name: string, amount: number): EtfItem => ({
  code, name, country: 'cn', exchange: 'SH', type: '股票型', board: '宽基ETF',
  tracking_index: null, manager: '某基金', inception: '2013-04-01', price: 3.5,
  pct: 0.04, amount, size_yi: 500, quote_status: 'ok', overseas: null,
});

const HOT: EtfItem[] = [item('511880', '银华日利ETF', 338e8), item('511990', '华宝添益ETF', 234e8)];

/** 资金流向：代码与热门榜**不重叠**，便于用 accessible name 精确定位链接 */
const FLOW: EtfFlowItem[] = [
  { code: '159992', name: '创新药ETF银华', pct: 2.1, amount: 1e9, net_inflow: 2.39e8, inflow_ratio: 21.8 },
  { code: '515120', name: '创新药ETF广发', pct: 1.5, amount: 9e8, net_inflow: 1.73e8, inflow_ratio: 25.26 },
];

const SCALE: EtfScale = {
  period: '1m', sample_size: 10,
  points: [
    { date: '2026-08-31', value: 7500, count: 10 },
    { date: '2026-09-01', value: 7420, count: 10 },
  ],
  note: '估算口径：最新份额 × 历史收盘价', scope: 'cn', scope_note: '仅 A 股',
};

const LIST: EtfListResult = {
  total: 2, page: 1, page_size: 20, items: HOT,
  options: { boards: [], types: [], indexes: [], managers: [] },
  sort_applied: '', dir_applied: 'desc',
};

const OVERVIEW: EtfOverview = {
  today: {
    date: '2026-10-03', source: 'eastmoney', etf_count: 1337, total_size_yi: 20000,
    avg_pct: 0.5, net_inflow_yi: 12.3, amount_yi: 3000, status: 'ok',
  },
  prev: null,
};

/* ---------------- 环境 ---------------- */

/**
 * jsdom 未实现 `scrollIntoView`（调用会抛 `not a function`）。
 * 用普通函数（非箭头）打桩才能拿到 `this`，从而断言"滚到了哪个元素"。
 */
let scrolledTo: HTMLElement[] = [];
const scrollSpy = vi.fn(function (this: HTMLElement) { scrolledTo.push(this); });
const originalScrollIntoView = Element.prototype.scrollIntoView;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/etf']}>
      <Routes>
        <Route path="/etf" element={<EtfCenter />} />
        <Route path="/etf/:code" element={<div>ETF_DETAIL_STUB</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/**
 * 等到资金流向表格渲染出来（说明 flow / list / hot 均已 resolve）。
 *
 * ⚠️ 这里**必须**等一个与所有被守契约都无关的稳定标记（数据行的**名称**文本），
 * 不能图省事写成 `findByRole('link', { name: '159992' })` —— 那正是本文件第一个
 * 用例所断言的契约：一旦注入缺陷把它破坏，**所有**用例都会卡在这一步，
 * 结果是 7/7 全红、缺陷互相掩盖，完全无法定位到底是哪个契约坏了。
 * （实测踩过：证伪轮次 A 曾因此呈现 7/7 全红而非预期的 4 个失败。）
 */
async function settle() {
  await screen.findByText('创新药ETF银华');
}

beforeEach(() => {
  localStorage.clear();
  scrolledTo = [];
  scrollSpy.mockClear();
  Element.prototype.scrollIntoView = scrollSpy as unknown as Element['scrollIntoView'];

  Object.values(api).forEach((f) => f.mockReset());
  api.overview.mockResolvedValue(OVERVIEW);
  api.overviewSeries.mockRejectedValue(new Error('本用例不关心历史序列'));
  api.list.mockResolvedValue(LIST);
  api.hot.mockResolvedValue({ items: HOT, sort_applied: 'amount' });
  api.performance.mockRejectedValue(new Error('本用例不关心表现图'));
  api.scale.mockResolvedValue(SCALE);
  api.flow.mockResolvedValue({
    period: '1d', items: FLOW, status: 'ok', scope: 'all_market', scope_note: '全市场 ETF',
  });
  api.arbitrage.mockResolvedValue({
    summary: { sample_count: 2, median_premium: 0.1, avg_premium: 0.15, positive_count: 1, negative_count: 1 },
    high_premium: [HOT[0]],
    high_discount: [HOT[1]],
  });
  api.allocation.mockResolvedValue({
    total_size_yi: 1000,
    boards: [],
  });
  h.setOption.mockClear();
});

afterEach(() => {
  if (originalScrollIntoView) Element.prototype.scrollIntoView = originalScrollIntoView;
  else delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

/* ---------------- 一、代码列可跳转 ---------------- */

describe('资金流向 / 自选 的代码列可跳转', () => {
  it('资金流向的代码列是链接，href 指向该 ETF 的分析页', async () => {
    renderPage();
    await settle();

    const link = screen.getByRole('link', { name: '159992' });
    expect(link).toHaveAttribute('href', '/etf/159992');
    expect(screen.getByRole('link', { name: '515120' })).toHaveAttribute('href', '/etf/515120');
  });

  it('点击资金流向的代码真的会导航到 /etf/:code', async () => {
    renderPage();
    await settle();

    fireEvent.click(screen.getByRole('link', { name: '159992' }));
    expect(await screen.findByText('ETF_DETAIL_STUB')).toBeInTheDocument();
  });

  it('「我的自选ETF」的代码列同样是链接（同型缺陷一并修）', async () => {
    localStorage.setItem('AQP_ETF_WATCH', JSON.stringify(['511880']));
    renderPage();
    await settle();

    // h3 → 头栏 div → 卡片根 div
    const watchCard = screen.getByText('我的自选ETF').parentElement!.parentElement!;
    const link = within(watchCard).getByRole('link', { name: '511880' });
    expect(link).toHaveAttribute('href', '/etf/511880');
  });
});

/* ---------------- 二、「查看全部」不再是自链接 ---------------- */

describe('热门榜「查看全部」', () => {
  it('不再是 <Link to="/etf">（自链接点了没反应），而是按钮', async () => {
    renderPage();
    await settle();

    expect(screen.queryByRole('link', { name: /查看全部/ })).toBeNull();
    expect(screen.getByRole('button', { name: /查看全部/ })).toBeInTheDocument();
  });

  it('点击后滚动到本页 ETF 列表锚点，而不是导航走', async () => {
    renderPage();
    await settle();

    fireEvent.click(screen.getByRole('button', { name: /查看全部/ }));

    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrolledTo).toHaveLength(1);
    expect(scrolledTo[0].id).toBe('etf-list');
    // 页面没被导航走（若仍是自链接，这里会因为路由变化而找不到按钮）
    expect(screen.getByRole('button', { name: /查看全部/ })).toBeInTheDocument();
  });

  it('滚动后给落点加高亮（否则用户不知道跳到了哪）', async () => {
    renderPage();
    await settle();

    const listCard = document.getElementById('etf-list');
    expect(listCard).not.toBeNull();
    expect(listCard!.className).not.toContain('ring-2');

    fireEvent.click(screen.getByRole('button', { name: /查看全部/ }));
    expect(document.getElementById('etf-list')!.className).toContain('ring-2');
  });
});

/* ---------------- 三、规模柱取色 ---------------- */

describe('ETF规模变化 的规模柱取色', () => {
  /** 取最近一次 setOption 里「规模(亿元)」柱的颜色 */
  function scaleBarColor(): unknown {
    const calls = h.setOption.mock.calls;
    const opt = (calls[calls.length - 1]?.[0] ?? {}) as {
      series?: Array<{ name?: string; itemStyle?: { color?: unknown } }>;
    };
    return opt.series?.find((s) => s.name === '规模(亿元)')?.itemStyle?.color;
  }

  it('用主题品牌蓝，且**不是**会被全站复用的共享类别色 C1', async () => {
    renderPage();
    await settle();
    await waitFor(() => expect(scaleBarColor()).toBeDefined());

    expect(scaleBarColor()).toBe(chartPalette().BRAND);
    // 这条才是真正的回归守卫：C1 同时服务 MA5 / KDJ-K / RSI6 / DIF，
    // 一旦有人把它改回来，全站图表会一起变色。
    expect(scaleBarColor()).not.toBe(CATEGORY_COLORS.C1);
  });
});

/* ---------------- 四、三重视图切换与多选回测 ---------------- */

describe('ETF列表三重视图与多资产勾选', () => {
  it('支持切换至「微观流动性」视图并展示换手率列头', async () => {
    renderPage();
    await settle();

    const liquidityTab = screen.getByRole('button', { name: '微观流动性' });
    fireEvent.click(liquidityTab);

    expect(screen.getByText('换手率')).toBeInTheDocument();
    expect(screen.getByText('流动性状态')).toBeInTheDocument();
  });

  it('支持切换至「折溢价套利」视图并展示 IOPV 和折溢价率列头', async () => {
    renderPage();
    await settle();

    const arbTab = screen.getByRole('button', { name: '折溢价套利' });
    fireEvent.click(arbTab);

    expect(screen.getByText('IOPV (参考净值)')).toBeInTheDocument();
    expect(screen.getAllByText('折溢价率').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('套利评级')).toBeInTheDocument();
  });

  it('勾选标的后支持触发多资产配置回测按钮', async () => {
    renderPage();
    await settle();

    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes.length).toBeGreaterThan(1);

    // 勾选第一只标的
    fireEvent.click(checkboxes[1]);
    expect(screen.getByRole('button', { name: /多资产组合回测 \(1\)/ })).toBeInTheDocument();

    // 勾选第二只标的
    fireEvent.click(checkboxes[2]);
    expect(screen.getByRole('button', { name: /多资产组合回测 \(2\)/ })).toBeInTheDocument();

    // 点击打开回测弹窗
    fireEvent.click(screen.getByRole('button', { name: /多资产组合回测 \(2\)/ }));
    expect(await screen.findByText('多资产 ETF 篮子配置与风险平价回测')).toBeInTheDocument();
  });
});
