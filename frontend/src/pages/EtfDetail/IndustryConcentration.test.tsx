/**
 * ETF「行业集中度」卡 —— 替换原「ETF 中心」纯导航促销卡（2026-10-03）。
 *
 * ## 被守的缺陷
 *
 * ### 一、原位置是一张**纯导航促销卡**（本次移除的）
 * `「ETF 中心 / 返回列表浏览全部 ETF…」`：全页十余张卡里只有它没有数据；它的链接与
 * 页头「返回 ETF 中心」按钮**完全重复**；且它绕过了共享 `SectionCard`（手写 `text-sm`
 * + `p-3`，缺规范 4.3 要求的标题栏 `border-b`），风格与全页不一致。
 * 本文件第一组用例即**页面级回归**：该促销卡不得复活。
 *
 * ### 二、新卡的算术必须自洽且诚实
 * 权重直接取后端 `ratio`（占净值比例 %）**不归一化**，与相邻甜甜圈同口径。
 * 故：
 * - 前 3 大 / 前 5 大 = 原始权重之和；
 * - 条形图按「占净值」画 ⇒ 各段宽度合计**恒为 100%**，右端未被覆盖的部分就是
 *   「未披露」（后端只披露主要行业，合计未必 100%）——**不允许把未披露偷偷摊掉**；
 * - `ratio === null` 是"不可得"，**不得当 0 计入分母/计数**（本项目红线）。
 *
 * ### 三、降级态不得弱化
 * `holdings` 块不可用时，本卡原位显示 `PanelEmpty` + 后端 `reason`，**不得**渲染
 * 一堆 `0.0%`（那会让用户以为"这只 ETF 没有行业集中度"）。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { BlockStatus, EtfDetail, EtfIndustryItem } from '@/types/etf';

import EtfDetailPage, { IndustryConcentration } from './index';

/* ---------------- 共享 mock ---------------- */

const h = vi.hoisted(() => {
  const api = {
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
    dispatchAction: vi.fn(),
  };
  return { api, detail: vi.fn() };
});

vi.mock('@/lib/echarts', () => ({
  init: () => h.api,
  getInstanceByDom: () => h.api,
}));
vi.mock('@/components/charts/KLineChart', () => ({ default: () => null }));
vi.mock('@/api/etf', () => ({ etfApi: { detail: h.detail } }));

/* ---------------- 夹具 ---------------- */

/** 14 个行业门类（对齐后端注释里 510300 的实测值），占比合计 93.8% */
const INDUSTRIES = [
  '制造业', '金融业', '信息传输、软件和信息技术服务业', '采矿业',
  '电力、热力、燃气及水生产和供应业', '交通运输、仓储和邮政业', '批发和零售业',
  '建筑业', '房地产业', '租赁和商务服务业', '科学研究和技术服务业',
  '水利、环境和公共设施管理业', '文化、体育和娱乐业', '农、林、牧、渔业',
];
const RATIOS = [30, 20, 12, 8, 6, 4, 3, 2.5, 2, 1.8, 1.5, 1.2, 1, 0.8];

const TOTAL = 93.8;              // 30+20+12+8+6+4+3+2.5+2+1.8+1.5+1.2+1+0.8
const TOP3 = 62.0;               // 30+20+12
const TOP5 = 76.0;               // +8+6
const REST = 17.8;               // 93.8-76.0
const UNDISCLOSED = 6.2;         // 100-93.8

const ITEMS: EtfIndustryItem[] =
  INDUSTRIES.map((industry, i) => ({ industry, ratio: RATIOS[i], mv_yi: null }));

/** 单一行业行的取值（用于断言"标签与数值同属一行"，避免全页文本误匹配） */
function rowText(label: string): string {
  return screen.getByText(label).parentElement!.textContent ?? '';
}

function renderCard(items: EtfIndustryItem[] = ITEMS) {
  return render(<IndustryConcentration items={items} />);
}

beforeEach(() => {
  h.api.setOption.mockClear();
  h.api.dispatchAction.mockClear();
  h.detail.mockReset();
  localStorage.clear();
});

/* ---------------- 组件级 ---------------- */

describe('行业集中度：数值口径', () => {
  it('前 3 大 / 前 5 大 = 原始权重之和（不归一化，与甜甜圈同口径）', () => {
    renderCard();
    expect(rowText('前 3 大合计')).toContain(`${TOP3.toFixed(1)}%`);
    expect(rowText('前 5 大合计')).toContain(`${TOP5.toFixed(1)}%`);
  });

  it('最大行业 = 排序后的第一名及其权重', () => {
    renderCard();
    expect(rowText('最大行业')).toContain('制造业');
    expect(rowText('最大行业')).toContain('30.0%');
  });

  it('有效行业数 = 1 / Σwᵢ²（归一化份额），保留 1 位小数', () => {
    renderCard();
    // Σwᵢ² = Σ(ratioᵢ/93.8)² ≈ 0.180466 ⇒ 1/Σ ≈ 5.541 ⇒ 5.5
    expect(rowText('有效行业数')).toContain('5.5');
  });

  it('脚注如实披露覆盖率与"派生"属性', () => {
    renderCard();
    expect(screen.getByText(new RegExp(`按已披露 14 个行业（合计 ${TOTAL.toFixed(1)}%）`)))
      .toBeInTheDocument();
    expect(screen.getByText(/非第三方直供/)).toBeInTheDocument();
  });
});

describe('行业集中度：条形图不许偷偷摊掉"未披露"', () => {
  it('各段宽度合计 = 已披露合计，未披露段 = 100 − 已披露合计', () => {
    renderCard();
    const top5 = screen.getByTestId('conc-bar-top5');
    const rest = screen.getByTestId('conc-bar-rest');

    expect(top5.style.width).toBe(`${TOP5}%`);
    expect(rest.style.width).toBe(`${REST}%`);

    // 三段（前 5 大 / 其余已披露 / 未披露）合计恒为 100%：
    // 前两段是实色 div，第三段由 track 底色承担 ⇒ 条永远铺满，不会露出"半截"。
    const w5 = parseFloat(top5.style.width);
    const wRest = parseFloat(rest.style.width);
    expect(w5 + wRest + UNDISCLOSED).toBeCloseTo(100, 6);
    expect(screen.getByText('未披露')).toBeInTheDocument();
  });

  it('合计恰好 100% 时不出现「未披露」图例（不画发丝线）', () => {
    renderCard([
      { industry: '制造业', ratio: 50, mv_yi: null },
      { industry: '金融业', ratio: 30, mv_yi: null },
      { industry: '采矿业', ratio: 20, mv_yi: null },
    ]);
    expect(screen.queryByText('未披露')).toBeNull();
    expect(screen.getByTestId('conc-bar-top5').style.width).toBe('100%');
    expect(screen.getByTestId('conc-bar-rest').style.width).toBe('0%');
  });

  it('条形图色块与图例色块同源（前 5 大段颜色 == 前 5 大图例色块颜色）', () => {
    renderCard();
    const barColor = screen.getByTestId('conc-bar-top5').style.backgroundColor;
    // `getByText('前 5 大')` 命中的是外层 label span，色块是它的第一个子 span
    const legendSwatch = screen.getByText('前 5 大').querySelector('span') as HTMLElement;
    expect(barColor).not.toBe('');
    expect(legendSwatch).not.toBeNull();
    expect(legendSwatch.style.backgroundColor).toBe(barColor);
  });
});

describe('行业集中度：null 是"不可得"，不是 0', () => {
  it('ratio 为 null 的行业不计入条数，也不进分母', () => {
    renderCard([
      { industry: '制造业', ratio: 60, mv_yi: null },
      { industry: '金融业', ratio: null, mv_yi: null },
      { industry: '采矿业', ratio: 40, mv_yi: null },
    ]);
    // 若把 null 当 0 计入，这里会是"已披露 3 个行业"
    expect(screen.getByText(/按已披露 2 个行业/)).toBeInTheDocument();
    expect(rowText('前 3 大合计')).toContain('100.0%');
  });

  it('全部不可得 ⇒ 显式空态，而不是一堆 0.0%', () => {
    renderCard([{ industry: '制造业', ratio: null, mv_yi: null }]);
    expect(screen.getByText('暂无行业数据')).toBeInTheDocument();
    expect(screen.queryByText('前 3 大合计')).toBeNull();
  });

  it('空数组 ⇒ 显式空态', () => {
    renderCard([]);
    expect(screen.getByText('暂无行业数据')).toBeInTheDocument();
  });
});

/* ---------------- 页面级回归 ---------------- */

const HOLDINGS_ITEMS = [
  { code: '600519', name: '贵州茅台', ratio: 5.2, mv_yi: 67.6 },
  { code: '300750', name: '宁德时代', ratio: 3.1, mv_yi: 40.3 },
];

function makeDetail(holdingsStatus: BlockStatus, reason?: string): EtfDetail {
  const ok = holdingsStatus === 'ok';
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
      kline: { status: 'ok', period: 'day', bars: [] },
      holdings: ok
        ? {
          status: 'ok',
          holdings: { date: '2024-12-31', items: HOLDINGS_ITEMS },
          industry: { date: '2024-12-31', items: ITEMS },
        }
        : {
          status: holdingsStatus, reason,
          holdings: { date: '', items: [] },
          industry: { date: '', items: [] },
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

function renderPage(detail: EtfDetail) {
  h.detail.mockResolvedValue(detail);
  return render(
    <MemoryRouter initialEntries={['/etf/510300']}>
      <Routes>
        <Route path="/etf/:code" element={<EtfDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('页面回归：纯导航促销卡已移除，原位换成行业集中度', () => {
  // ⚠️ 每条用例的"等待"必须等一个**与其它用例的被守契约无关**的稳定标记，
  // 否则注入"删掉某卡"会让所有用例在等待阶段一起失败，掩盖真实信号（证伪盲区）。
  it('「ETF 中心」促销卡不再出现（文案与链接都不得复活）', async () => {
    renderPage(makeDetail('ok'));
    await screen.findByText('返回 ETF 中心'); // 页头按钮，与本用例的被守对象无关

    expect(screen.queryByText(/返回列表浏览全部 ETF/)).toBeNull();
    expect(screen.queryByText(/对比业绩与资金流向/)).toBeNull();
    // 精确匹配 'ETF 中心'：页头按钮文案是「返回 ETF 中心」，不构成命中
    expect(screen.queryByText('ETF 中心')).toBeNull();
  });

  it('原位渲染「行业集中度」卡，并显示派生数值', async () => {
    renderPage(makeDetail('ok'));
    await screen.findByText('行业集中度');

    expect(rowText('前 3 大合计')).toContain(`${TOP3.toFixed(1)}%`);
  });

  it('页头「返回 ETF 中心」按钮保留（导航入口不因删卡而丢失）', async () => {
    renderPage(makeDetail('ok'));
    await screen.findByText('返回 ETF 中心');
    expect(screen.getByText('返回 ETF 中心')).toBeInTheDocument();
  });

  it('holdings 降级 ⇒ 原位显示后端 reason，不渲染 0.0%（降级态不弱化）', async () => {
    renderPage(makeDetail('unavailable', '持仓数据源超时'));
    await screen.findAllByText('持仓数据源超时');

    // 同一 reason 会在 4 张降级卡里都出现（持仓Top10 / 行业配置 / 行业集中度 /
    // 投资组合配置），故必须**限定在本卡内**断言，不能用全局 getByText。
    const card = screen.getByText('行业集中度').closest('div.rounded-lg') as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.textContent).toContain('持仓数据源超时');
    // 关键：降级时不得退化成"一堆 0.0%"，那会让用户以为"没有集中度"
    expect(card.textContent).not.toContain('前 3 大合计');
    expect(card.textContent).not.toContain('0.0%');
  });

  it('hover 图例不影响页面其它部分（联动只作用于本卡画布）', async () => {
    renderPage(makeDetail('ok'));
    await screen.findAllByTestId('pie-legend-row'); // 等图例本身，不等集中度卡

    const rows = screen.getAllByTestId('pie-legend-row');
    fireEvent.mouseEnter(rows[0]);
    await waitFor(() => expect(h.api.dispatchAction).toHaveBeenCalledWith(
      { type: 'highlight', seriesIndex: 0, dataIndex: 0 },
    ));
    fireEvent.mouseLeave(rows[0]);
    expect(h.api.dispatchAction).toHaveBeenLastCalledWith({ type: 'downplay', seriesIndex: 0 });
  });
});
