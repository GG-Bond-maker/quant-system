/**
 * ETF 详情页「K 线走势」卡 —— 对标个股详情页（2026-10-04）。
 *
 * ## 被守的四个缺陷
 *
 * ### D1 成交量副图 y 轴标签溢出被裁
 * ETF 成交量以「份」计，可达 1e10；`5,000,000,000` 共 13 字符，超出 K 线布局
 * 硬编码的 62px 左边距，再被根节点 `overflow-hidden` **裁掉首位数字**
 * （实测渲染成 `,000,000,000`）。修法 = grid1 轴标签走 `fmtVolAxis`（亿/万）。
 * `fmtVolAxis` 自身的分档口径由 `components/charts/KLineChart.test.ts` 守；
 * **本文件守的是"它确实被挂到 grid1、且只挂 grid1"**——纯函数正确 ≠ 接线正确。
 *
 * ### D2 卡片套卡片（两层圆角边框相套）
 * `KLineChart` 根节点自带 `rounded-lg border border-hair bg-surface`，而本页又用
 * `SectionCard` 包了一层 ⇒ 实测外卡片 930×1049 / 内图表 904×978 两层边框相套，
 * 与个股详情页（图表**单独**成卡，单层）不一致。修法 = 给图表传 `bare`。
 *
 * ### D3 两套周期控件，作用于**不同层**，会双重聚合
 * 原页面卡片右上角有 `日K/周K/月K` ViewToggle（改后端 `kline_period` ⇒ **服务端**
 * 聚合并重新请求），而图表内部自带 `日K/周K/月K/季K/年K` 周期栏（对收到的 bars
 * 再做一次**前端**聚合）。两者互不同步：「页面选周K + 图表内选日K」会把周线当
 * 日线显示，「页面选月K + 图表内选季K」会把月线再聚成季线 —— **数字全错且不报错**。
 * 修法 = 删掉页面级那套，页面恒取日线，周期只由图表内部一套负责
 * （对齐个股详情页：`/stock/{symbol}/kline` 无 period 参数）。
 *
 * ### D4 图窗高度与个股页不一致 + 定高后必然产生的列内死区
 * 第一版用 `fill`（撑满父容器）把图窗拉到 903px，而个股页是 520px ⇒ 蜡烛被纵向
 * 拉伸。改为定高 520px 后，K 线卡固定 666px，但上区右列自然高 ≈1049px
 * ⇒ 左列空出 ≈380px。故**必须同时重排上区**：把「最新新闻与动态」挪到左列，
 * 并让两列各自的最后一格都 `grow` 吸收残余高度差（详见 D4 用例的说明）。
 *
 * ## 为什么断言 className / option，而不是几何量
 *
 * jsdom **没有布局引擎**（`getBoundingClientRect()` 恒 0），"边框是否真的只有一层"、
 * "两列是否等高"都无法由渲染几何证明 —— 任何"渲染后断言高度相等"的测试都会**恒真**。
 * 故这里守的是**结构不变量**：图表根节点不得再带卡片外观类、页面里周期按钮只有一套、
 * grid1 的 formatter 确实存在、两列结构与 `grow` 齐备。真实几何由 Playwright 实测验收。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EtfDetail, EtfKlineBar } from '@/types/etf';

import EtfDetailPage from './index';

/* ---------------- 共享 mock ---------------- */

const h = vi.hoisted(() => {
  const api = {
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
    dispatchAction: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    // `updatePriceScale()` 会读它；返回 undefined 会在 `opt.dataZoom[0]` 上抛错。
    getOption: vi.fn(() => ({ dataZoom: [{ start: 0, end: 100 }] })),
  };
  return { api, detail: vi.fn() };
});

// ⚠️ 本文件**不** mock `@/components/charts/KLineChart` —— 被守的正是它的渲染结果。
vi.mock('@/lib/echarts', () => ({
  init: () => h.api,
  getInstanceByDom: () => h.api,
}));
vi.mock('@/api/etf', () => ({ etfApi: { detail: h.detail } }));

/* ---------------- 夹具 ---------------- */

/** 120 根日线（> 80 ⇒ dataZoom 初始只显示尾部 80 根，与线上一致）。 */
function makeBars(n = 120): EtfKlineBar[] {
  const out: EtfKlineBar[] = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(Date.UTC(2026, 0, 1 + i));
    const base = 4 + Math.sin(i / 7) * 0.3;
    out.push({
      date: d.toISOString().slice(0, 10),
      open: Number(base.toFixed(3)),
      close: Number((base + 0.02).toFixed(3)),
      high: Number((base + 0.05).toFixed(3)),
      low: Number((base - 0.05).toFixed(3)),
      // ETF 量级：以「份」计，单日可达 5e9 ⇒ 原实现轴标签 13 字符被裁
      volume: 5_000_000_000,
    });
  }
  return out;
}

function makeDetail(): EtfDetail {
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
      kline: { status: 'ok', period: 'day', bars: makeBars() },
      holdings: {
        status: 'ok',
        holdings: { date: '2024-12-31', items: [] },
        industry: { date: '2024-12-31', items: [] },
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
 * 渲染整页并等一个**与被守契约无关**的稳定标记（页头「返回 ETF 中心」按钮）。
 *
 * 等待标记不能选「K 线走势」这类被守对象本身，否则注入缺陷会让用例在等待阶段
 * 就失败，掩盖真实信号（证伪盲区）。
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

/** 「K 线走势」卡的根节点（`SectionCard` 自带 `rounded-lg`） */
function klineCard(): HTMLElement {
  const card = screen.getByText('K 线走势').closest('div.rounded-lg');
  expect(card, '未找到「K 线走势」卡片根节点').not.toBeNull();
  return card as HTMLElement;
}

/** 图表根节点：全页唯一带 `overflow-hidden` 的节点即 `KLineChart` 的根 */
function chartRoot(): HTMLElement {
  const el = klineCard().querySelector('.overflow-hidden');
  expect(el, '「K 线走势」卡内没有图表根节点（KLineChart 未渲染？）').not.toBeNull();
  return el as HTMLElement;
}

/**
 * 取整页首次 `setOption` 的完整 option。
 *
 * ⚠️ 判据是「`yAxis` 里**有** `gridIndex === 1` 的那条」，**不能按数组下标取**：
 * `yAxis[0]` 与 `yAxis[1]` 都是**主图**的两条轴（左价格 / 右涨跌幅，`gridIndex` 均为 0），
 * 副图是从 `yAxis[2]` 才开始（按 `gridIndex = 1, 2, …` 顺序 push）。
 * 曾经按下标 1 取，取到的是**右轴涨跌幅**的 formatter（返回 `{u|+…%}`）。
 */
function fullOption(): {
  grid: unknown[];
  yAxis: Array<{ gridIndex?: number; axisLabel?: { formatter?: (v: number) => string } }>;
} {
  const hit = h.api.setOption.mock.calls
    .map((c) => c[0] as { grid?: unknown[]; yAxis?: Array<{ gridIndex?: number }> })
    .find((o) => o && Array.isArray(o.yAxis) && o.yAxis.some((a) => a.gridIndex === 1));
  expect(hit, '未捕获到 K 线的完整 setOption（yAxis 含 gridIndex=1 的成交量轴）').toBeTruthy();
  return hit as never;
}

/** 按 `gridIndex` 取某条轴（0=主图左价格 / 0=主图右涨跌幅 也在此 grid 上，故按顺序 find） */
function axisByGrid(opt: ReturnType<typeof fullOption>, gridIndex: number) {
  return opt.yAxis.find((a) => a.gridIndex === gridIndex);
}

beforeEach(() => {
  h.api.setOption.mockClear();
  h.api.getOption.mockClear();
  h.detail.mockReset();
  localStorage.clear();
});

/* ---------------- D2：卡片套卡片 ---------------- */

describe('D2 图表不再自带卡片外观（消除两层圆角边框）', () => {
  it('图表根节点不带 rounded-lg / border-hair / bg-surface（由外层 SectionCard 提供）', async () => {
    await renderPage();
    const cls = chartRoot().className.split(/\s+/);

    expect(cls, '🔴 图表又自带了圆角 ⇒ 卡片套卡片').not.toContain('rounded-lg');
    expect(cls, '🔴 图表又自带了边框 ⇒ 卡片套卡片').not.toContain('border-hair');
    expect(cls, '🔴 图表又自带了底色 ⇒ 卡片套卡片').not.toContain('bg-surface');
    // 反向断言：确认抓到的确实是图表根节点（否则上面的 not.toContain 会在抓到
    // 任意一个"没有这些类"的元素时**空过**）。`overflow-hidden` 是根节点的特征类，
    // 再要求它内部有周期栏按钮（只有 KLineChart 才渲染 日K/周K/…）。
    expect(cls).toContain('overflow-hidden');
    expect(
      [...chartRoot().querySelectorAll('button')].map((b) => b.textContent?.trim()),
      '抓到的节点里没有周期栏 ⇒ 不是 KLineChart 的根节点',
    ).toEqual(expect.arrayContaining(['日K', '年K']));
  });

  it('卡片外观只由外层 SectionCard 提供，且它仍在（修复不得把边框一起删掉）', async () => {
    await renderPage();
    const cardCls = klineCard().className.split(/\s+/);
    expect(cardCls).toContain('rounded-lg');
    expect(cardCls).toContain('border-hair');
    expect(cardCls).toContain('bg-surface');
  });

  it('图表内容与卡片齐边：body 用 !p-0（`p-0` 单独写会被硬编码的 p-3 盖掉）', async () => {
    await renderPage();
    const body = chartRoot().parentElement as HTMLElement;
    const cls = body.className.split(/\s+/);
    // Tailwind 3 的 padding 工具类按样式表顺序排列，`.p-3` 生成在 `.p-0` 之后，
    // 同属性同优先级下后者胜出（实测 class="p-3 p-2" 计算值 = 12px）⇒ 必须 `!`。
    expect(cls, 'body 缺 !p-0 ⇒ 图表无法与卡片齐边').toContain('!p-0');
  });
});

/* ---------------- D3：只允许一套周期控件 ---------------- */

describe('D3 页面级周期控件已移除，周期只由图表内部一套负责', () => {
  it('每个周期标签在全页只出现一次（两套控件会各出现一次 ⇒ 双重聚合）', async () => {
    await renderPage();
    for (const label of ['日K', '周K', '月K', '季K', '年K']) {
      expect(screen.getAllByText(label), `「${label}」出现了不止一次`).toHaveLength(1);
    }
  });

  it('卡片标题栏里只有标题，没有 ViewToggle', async () => {
    await renderPage();
    const header = klineCard().firstElementChild as HTMLElement;
    expect(header.textContent).toBe('K 线走势');
  });
});

/* ---------------- D4：上区两列重排（K 线定高 + 零空白） ---------------- */

/**
 * ## 被守的缺陷
 *
 * K 线图窗改为**定高 520px**（对齐个股页 `StockDetail`）后，K 线卡总高固定为 ≈666px。
 * 但上区右列（概况 238 + 新闻 193 + 持仓 281 + 行业对 307）自然高 ≈1049px
 * ⇒ 左列会在图表下方空出 ≈380px 死区。修法是**重排上区**：把「最新新闻与动态」
 * （193px，全页唯一能让两列齐平的选择）从右列挪到左列，并让**两列各自的最后一格
 * 都能 `grow`** 以吸收残余高度差。
 *
 * ## 为什么两列都要能 grow
 *
 * 哪一列更高**随视口宽度变化**：宽屏（≥1280）右列矮 23px，而 1024px 下
 * 「ETF 概况」文案换行使其长到 277px ⇒ 右列反而**高 16px**。只给一侧加 `grow`
 * 会在另一侧留出空白带（实测 1024px 下左列底部曾有 16px 空带）。
 *
 * 且必须用 `grow` 而**非** `flex-1`：`flex-1` = `flex:1 1 0%`，最后一项的**内容高度
 * 不再计入容器自然高** ⇒ 两列自然高同时被低估、栅格行高塌陷，行业对会被压到放不下
 * 甜甜圈的 `min-h-[200px]`。
 *
 * ## 为什么断言结构而不是几何
 *
 * jsdom **没有布局引擎**（`getBoundingClientRect()` 恒 0），"空白是否为 0"无法由渲染
 * 断言证明 —— 任何"渲染后断言高度相等"的测试都会**恒真**。故这里守**结构不变量**
 * （与 `test/layoutContract.test.tsx` 同一取舍）；真实几何由 Playwright 实测验收：
 * 1024 / 1120 / 1280 / 1440 / 1600 / 1920 / 2560 七个宽度下两列高度差与底部空带**全为 0**。
 */
describe('D4 上区两列重排：K 线与新闻同列，两列最后一格都能 grow', () => {
  /** 上区栅格的两列（左 = `lg:col-span-2`，右 = 第三列） */
  function topColumns(): [HTMLElement, HTMLElement] {
    const left = klineCard().parentElement as HTMLElement;
    const outer = left.parentElement as HTMLElement;
    expect(outer.className, '上区栅格结构变了').toContain('lg:grid-cols-3');
    return [left, outer.children[1] as HTMLElement];
  }

  const titlesOf = (el: HTMLElement) =>
    [...el.querySelectorAll('h2')].map((h) => h.textContent?.trim());

  it('「最新新闻与动态」与 K 线同处左列（挪回右列会让左列空出 ≈380px）', async () => {
    await renderPage();
    const [left] = topColumns();
    expect(titlesOf(left)).toEqual(['K 线走势', '最新新闻与动态']);
  });

  it('右列只剩「概况 + 持仓 Top 10 + 行业对」', async () => {
    await renderPage();
    const [, right] = topColumns();
    expect(titlesOf(right)).toEqual(['ETF 概况 & 资讯', '持仓 Top 10', '行业配置', '行业集中度']);
  });

  it('两列的最后一格都带 grow（谁矮谁补；缺一侧就会在某个宽度留空带）', async () => {
    await renderPage();
    const [left, right] = topColumns();
    expect(left.lastElementChild!.className.split(/\s+/)).toContain('grow');
    expect(right.lastElementChild!.className.split(/\s+/)).toContain('grow');
  });

  it('不得用 flex-1 代替 grow（flex-basis:0 会让栅格行高塌陷、压垮甜甜圈）', async () => {
    await renderPage();
    const [left, right] = topColumns();
    for (const col of [left, right]) {
      expect(
        col.lastElementChild!.className.split(/\s+/),
        '🔴 用了 flex-1 ⇒ flex-basis:0 使内容高不计入容器，两列自然高塌陷',
      ).not.toContain('flex-1');
    }
  });
});

/* ---------------- D1：成交量轴标签接线 ---------------- */

describe('D1 成交量副图轴标签走缩写（否则 13 字符被裁掉首位）', () => {
  it('grid1（成交量）的 axisLabel.formatter 能把 5e9 缩成 "50亿"', async () => {
    await renderPage();
    const opt = fullOption();

    // 布局：grid0=主图 / grid1=成交量 / grid2=副图1(MACD)
    expect(opt.grid).toHaveLength(3);
    const fmt = axisByGrid(opt, 1)?.axisLabel?.formatter;
    expect(fmt, '🔴 成交量轴缺 formatter ⇒ 原始值直出被裁').toBeTypeOf('function');
    expect(fmt!(5_000_000_000)).toBe('50亿');
    expect(fmt!(4.96e8)).toBe('4.96亿');
  });

  it('只对 grid1 生效：副图是无量纲指标值，加「亿/万」反而是错的', async () => {
    await renderPage();
    const opt = fullOption();
    // 先确认副图轴**存在**：否则下面那条 `toBeUndefined()` 会在轴缺失时**空过**
    // （`undefined?.axisLabel?.formatter` 恒 undefined），证伪时抓不到"轴被删掉"。
    const subAxis = axisByGrid(opt, 2);
    expect(subAxis, '副图1（MACD）的轴不存在，布局被改坏').toBeTruthy();
    expect(subAxis?.axisLabel?.formatter).toBeUndefined();
  });

  it('主图两条轴不受影响（左价格走精度 formatter、右涨跌幅走百分比 formatter）', async () => {
    await renderPage();
    const opt = fullOption();
    // 主图左轴：价格精度 formatter（返回纯数字串，不带「亿/万」）
    const priceFmt = opt.yAxis[0].axisLabel?.formatter;
    expect(priceFmt, '主图左轴精度 formatter 被误删').toBeTypeOf('function');
    expect(priceFmt!(4.35512)).not.toMatch(/[亿万]/);
    // 主图右轴：涨跌幅 formatter（富文本 `{u|+x%}`），不是 `fmtVolAxis`
    const pctFmt = opt.yAxis[1].axisLabel?.formatter;
    expect(pctFmt!(5_000_000_000)).not.toBe('50亿');
  });
});
