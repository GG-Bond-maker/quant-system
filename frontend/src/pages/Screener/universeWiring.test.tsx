/**
 * 选股中心页 —— `/screener` 的 `universe_filter` 是否**真的**到了卡片上（接线回归）。
 *
 * ## 为什么还要一条页面级用例
 * `StatsCards.test.tsx` 只证明「给了 `universe` 就能渲染全市场」，**证明不了**页面
 * 把 `result.universe_filter` 传了下去 —— 少传一个 prop 时单元测试全绿、用户却看不到
 * 任何变化。这正是本仓库记过的同型缺陷（「可达 ≠ 有入口」）。
 *
 * ## 被守的两件事
 * 1. 「股票数量」卡主值旁出现「全市场 5,208 只」（来自响应里的 `universe_filter.rows`）；
 * 2. 整页不出现「较昨日」—— 对比行必须用后端 `stats.prev_date` 的真实日期
 *    （实测 2026-09-30 的对比日是 2026-09-17）。
 *
 * ## 等待标记的选取（证伪盲区）
 * 两条用例都等 `Alpha 榜（50）`（由 `items.length` 决定），**与被守契约无关** ——
 * 否则注入"删掉全市场并列"会让用例在等待阶段就失败，掩盖真实信号。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ScreenerItem, ScreenerResult } from '@/types/p1';

import Screener from './index';

/* ---------------- 桩 ---------------- */

const h = vi.hoisted(() => ({
  screen: vi.fn(),
  statsSeries: vi.fn(),
  watchlist: vi.fn(),
  overviewDaily: vi.fn(),
  indexKline: vi.fn(),
}));

vi.mock('@/api/screener', () => ({
  screenerApi: { screen: h.screen, statsSeries: h.statsSeries, watchlist: h.watchlist },
}));
vi.mock('@/api/market', () => ({
  marketApi: { overviewDaily: h.overviewDaily, indexKline: h.indexKline },
}));
vi.mock('@/api/export', () => ({ exportApi: { screener: vi.fn() } }));
vi.mock('@/hooks/useWatchlistQuotes', () => ({
  useWatchlistQuotes: () => ({
    data: { items: [] }, loading: false, error: null, reload: vi.fn(),
  }),
}));
vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: (sel: (s: { user: { role: string } }) => unknown) =>
    sel({ user: { role: 'admin' } }),
}));
vi.mock('@/stores/useWatchlistStore', () => ({
  DEFAULT_GROUP: 'default',
  useWatchlistStore: () => ({
    addTo: vi.fn(), removeFrom: vi.fn(), contains: () => false, groups: {},
  }),
}));
vi.mock('@/components/RequireAuth', () => ({ hasMinimumRole: () => false }));

// 与本用例无关的重子组件：空渲染，避免被各自的数据依赖带偏（沿用 EtfCenter.test.tsx 的做法）
vi.mock('./PerformanceChart', () => ({
  default: () => null,
  PERF_PERIODS: [{ key: '6m', label: '6月' }],
}));
vi.mock('./DistributionCharts', () => ({ default: () => null }));
vi.mock('./StockList', () => ({ default: () => null }));
vi.mock('./FilterPanel', () => ({
  default: () => null,
  DEFAULT_FILTERS: { day: '', topK: 20, minScore: '', signal: 'all' },
}));

/* ---------------- 夹具（对齐后端实测值） ---------------- */

const ITEMS: ScreenerItem[] = Array.from({ length: 50 }, (_, i) => ({
  symbol: `${600000 + i}.SH`,
  name: `标的${i}`,
  industry: '制造业',
  close: 10, pct: 1, amount: 1e8, turnover: 1, limit_pct: 10, score: 0.01,
  signal_strength: i < 40 ? 'strong' : i < 46 ? 'neutral' : 'weak',
}));

const RESULT: ScreenerResult = {
  date: '2026-09-30',
  status: 'ok',
  as_of: '2026-09-30T15:00:00',
  reason: null,
  message: 'ok',
  coverage: { available: 5209, total: 5209, ratio: 1 },
  strategy: 'alpha_basic_v1',
  top_k: 50,
  board: 'all',
  count: 50,
  items: ITEMS,
  stats: {
    today: {
      total: 50, pool_size: 5000, win_rate: 28.0, avg_pct: -0.0576, avg_score: 0.011139,
      strong_signal: 40, industry_count: 12, top_industry_ratio: 15.0,
    },
    prev: {
      total: 50, pool_size: 2431, win_rate: 20.0, avg_pct: -1.2, avg_score: 0.008,
      strong_signal: 20, industry_count: 20, top_industry_ratio: 12.12,
    },
    prev_date: '2026-09-17',
  },
  from_cache: false,
  universe_filter: {
    applied: true, rows: 5208, date: '2026-09-30', reason: null, board_rows: null,
  },
};

beforeEach(() => {
  h.screen.mockResolvedValue(RESULT);
  h.statsSeries.mockResolvedValue(null);
  h.overviewDaily.mockResolvedValue({ trade_date: '20260930' });
  h.indexKline.mockResolvedValue({ name: '上证指数', bars: [] });
});

function renderPage() {
  return render(<MemoryRouter><Screener /></MemoryRouter>);
}

function cardOf(label: string): HTMLElement {
  const el = screen.getByText(label).closest('div.rounded-lg');
  if (!el) throw new Error(`未找到卡片：${label}`);
  return el as HTMLElement;
}

describe('页面接线：universe_filter 与真实对比日', () => {
  it('「股票数量」卡并列渲染响应里的全市场数（5,208）', async () => {
    renderPage();
    await screen.findByText('Alpha 榜（50）'); // 与本节被守契约无关的稳定标记

    const card = cardOf('股票数量');
    expect(card.textContent).toContain('5,000');
    expect(card.textContent).toContain('全市场 5,208 只');
  });

  it('整页不出现「较昨日」，对比行用后端 prev_date', async () => {
    renderPage();
    await screen.findByText('Alpha 榜（50）');

    expect(screen.queryByText(/较昨日/)).toBeNull();
    const row = cardOf('股票数量').querySelector('div.num.mt-1');
    expect(row?.textContent).toContain('对比 2026-09-17');
    expect(row?.textContent).toContain('口径不同，不可比');
  });
});
