/**
 * ETF 详情页：**降级态**的两条前端纪律（2026-10-04）。
 *
 * ## 背景（用户实测现象）
 * 部分 ETF 的「估值指标」卡四行全是 `—`、「跟踪误差分析」卡显示"无法识别跟踪指数"，
 * 但**看不出为什么**。排查（`backend/app/data/etf.py` + 真实接口）确认数据侧有两道
 * 硬编码白名单（`_TRACKING_INDEX_MAP` 21 项 / `_LEGULEGU_NAME_MAP` 12 项），
 * 那是**数据覆盖问题**；而下面两条是**前端缺陷**，本次单独修：
 *
 *   ① 「估值指标」卡**无条件**渲染 4 行、把不可得字段一律写成 `—`，并把后端下发的
 *      `reason` **整个丢弃** ⇒ 用户分不清「这只 ETF 没有估值数据」与「数据源超时」。
 *      同页其余各卡（情绪 / 产业链 / 跟踪 / 持仓…）都走
 *      `status === 'ok' ? 内容 : EmptyPanel(reason)`，本卡是全页**唯一**的例外。
 *   ② 「跟踪误差分析」的区间切换器 `ViewToggle` **无条件**渲染，而它只影响由
 *      `tracking.points` 派生的 `trackPoints`；降级时后端返回 `points = []` ⇒
 *      点「1月/3月/6月/1年」**界面毫无变化** = 假交互（项目红线）。
 *
 * ## 为什么断言"结构"而不是"像素"
 * jsdom 没有布局引擎。这里守的是**结构不变量**：降级态必须出现**原因文本**、
 * 不得只剩横杠；图不渲染时**不得**渲染那个无效控件。控件与图体是否**同源**
 * （同一个条件表达式）正是靠"三种 tracking 形态下的控件有无"来判定的。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  EtfDetail, EtfDetailTracking, EtfDetailValuation, EtfTrackingPoint,
} from '@/types/etf';

import EtfDetailPage from './index';

/* ---------------- 共享 mock（与 riskMetrics.test.tsx 同款） ---------------- */

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

// ⚠️ 比 riskMetrics.test.tsx 多一个 `graphic`：那边 `tracking.points` 是空数组、
// `TrackingErrorChart` 从不渲染，所以最小 mock 够用；本文件要用**真实点数**渲染它，
// 而它内部 `new echarts.graphic.LinearGradient(...)` 取面积渐变 ⇒ 必须提供。
vi.mock('@/lib/echarts', () => ({
  init: () => h.api,
  getInstanceByDom: () => h.api,
  graphic: { LinearGradient: class { constructor(..._a: unknown[]) { void _a; } } },
}));
vi.mock('@/api/etf', () => ({ etfApi: { detail: h.detail } }));

/* ---------------- 夹具 ---------------- */

/** 造 n 个跟踪点；`etf`/`index` 是累计涨跌幅，`diff` 是当日收益差。 */
function pointsFrom(n: number): EtfTrackingPoint[] {
  return Array.from({ length: n }, (_, i) => ({
    date: `2026-01-${String((i % 28) + 1).padStart(2, '0')}`,
    etf: i * 0.1,
    index: i * 0.09,
    diff: 0.01,
  }));
}

const TRACKING_OK: EtfDetailTracking = {
  status: 'ok', benchmark_code: '000300', benchmark_name: '沪深300',
  points: pointsFrom(60), tracking_error: 1.7874,
};

const VALUATION_OK: EtfDetailValuation = {
  status: 'ok', pe_ttm: 12.3, pb: 1.4, index_code: '000300', index_name: '沪深300',
  pe_percentile: 35.2, pb_percentile: 22.1,
};

function makeDetail(opts: {
  tracking?: EtfDetailTracking;
  valuation?: EtfDetailValuation;
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
        manager: null, inception: null,
      },
      kline: {
        status: 'ok', period: 'day',
        bars: pointsFrom(30).map((p) => ({
          date: p.date, open: 4, close: 4, high: 4.1, low: 3.9, volume: 1e8,
        })),
      },
      holdings: {
        status: 'ok',
        holdings: { date: '2026-06-30', items: [] },
        industry: { date: '2026-06-30', items: [] },
      },
      tracking: opts.tracking ?? TRACKING_OK,
      valuation: opts.valuation ?? VALUATION_OK,
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

/** 卡片内 标签 → 值 的文本对（卡内每行 = 一个含 2 个 span 的 div）。 */
function statRows(card: HTMLElement): Array<[string, string]> {
  return [...card.querySelectorAll('div')]
    .filter((d) => d.children.length === 2
      && d.children[0].tagName === 'SPAN' && d.children[1].tagName === 'SPAN')
    .map((d) => [d.children[0].textContent!.trim(), d.children[1].textContent!.trim()]);
}

/** 区间切换器（`ViewToggle` 根节点带 `title="观察区间"`）。 */
function periodToggle(card: HTMLElement): Element | null {
  return card.querySelector('[title="观察区间"]');
}

beforeEach(() => {
  h.api.setOption.mockClear();
  h.api.getOption.mockClear();
  h.detail.mockReset();
  localStorage.clear();
});

/* ==================== ① 估值指标：降级必须说明原因 ==================== */

describe('估值指标：降级态必须给出原因，不得只留横杠', () => {
  it('unavailable ⇒ 显示后端 reason，且不再渲染 4 个字段行', async () => {
    await renderPage(makeDetail({
      valuation: {
        status: 'unavailable', reason: '无法获取估值数据',
        pe_ttm: null, pb: null, index_code: null, index_name: null,
        pe_percentile: null, pb_percentile: null,
      },
    }));
    const card = cardOf('估值指标');
    expect(card.textContent, '🔴 后端给了 reason，卡片却把它丢了').toContain('无法获取估值数据');
    expect(statRows(card), '🔴 不可用时不该再渲染 4 个「字段名 + —」行').toEqual([]);
  });

  it('degraded ⇒ 照常显示真实 PE/PB，并披露分位缺失的原因', async () => {
    await renderPage(makeDetail({
      valuation: {
        status: 'degraded', reason: '仅获取到当前 PE/PB，历史分位计算失败',
        pe_ttm: 12.3, pb: 1.4, index_code: '000300', index_name: '沪深300',
        pe_percentile: null, pb_percentile: null,
      },
    }));
    const card = cardOf('估值指标');
    const map = Object.fromEntries(statRows(card));
    // 有真值就照常显示（不能因为"整块降级"把有效数字也藏掉）
    expect(map['PE(TTM)']).toBe('12.30');
    expect(map['PB']).toBe('1.40');
    // 算不出的分位保持 `—`，但**必须**说明为什么
    expect(map['PE 分位']).toBe('—');
    expect(map['PB 分位']).toBe('—');
    expect(card.textContent, '🔴 两个 `—` 旁边没有任何解释')
      .toContain('仅获取到当前 PE/PB，历史分位计算失败');
  });

  it('ok ⇒ 4 行数值齐全，且不出现任何降级文案', async () => {
    await renderPage();
    const card = cardOf('估值指标');
    const map = Object.fromEntries(statRows(card));
    expect(map['PE(TTM)']).toBe('12.30');
    expect(map['PB']).toBe('1.40');
    expect(map['PE 分位']).toBe('35.2%');
    expect(map['PB 分位']).toBe('22.1%');
    expect(card.textContent).not.toContain('无法获取估值数据');
    expect(card.textContent).not.toContain('加载中');
  });
});

/* ==================== ② 跟踪误差分析：控件与图体同源 ==================== */

describe('跟踪误差分析：不能渲染图时，区间切换器必须一并消失', () => {
  it('unavailable ⇒ 无切换器（原先是"点了没反应"的假交互）', async () => {
    await renderPage(makeDetail({
      tracking: {
        status: 'unavailable', reason: '无法识别跟踪指数',
        benchmark_code: '', benchmark_name: '', points: [], tracking_error: null,
      },
    }));
    const card = cardOf('跟踪误差分析');
    expect(card.textContent).toContain('无法识别跟踪指数');
    expect(periodToggle(card), '🔴 图没渲染，切换器却还在 ⇒ 假交互').toBeNull();
    for (const label of ['1月', '3月', '6月', '1年']) {
      expect(card.textContent, `🔴 降级态仍渲染了「${label}」`).not.toContain(label);
    }
  });

  it('ok 且点数充足 ⇒ 切换器在，图也在', async () => {
    await renderPage();
    const card = cardOf('跟踪误差分析');
    expect(periodToggle(card), '🔴 正常态反而没有切换器').not.toBeNull();
    expect(card.textContent).toContain('1年');
  });

  it('ok 但只有 1 个点 ⇒ 图不渲染，切换器也不许出现', async () => {
    // 边界：`status === 'ok'` 不足以判定"图会渲染"，必须同时看点数
    await renderPage(makeDetail({
      tracking: { ...TRACKING_OK, points: pointsFrom(1) },
    }));
    const card = cardOf('跟踪误差分析');
    expect(periodToggle(card), '🔴 只有 1 个点画不出图，切换器却还在').toBeNull();
  });
});
