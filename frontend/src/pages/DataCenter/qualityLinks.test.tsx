/**
 * 数据中心 —— 数据质量与缺漏标的跳转链接测试。
 *
 * 测试目标：
 * 1. 质量扫描发现的缺漏标的代码为超链接，点击可跳转至对应的详情分析页；
 * 2. A 股个股代码（如 002656.SZ）正确路由至 /stock/:symbol；
 * 3. 场内 ETF 代码（如 510050.SH、159915）正确识别并路由至 /etf/:code；
 * 4. 包含 hover tooltip 提示；
 * 5. 复选框独立工作，不被链接点击影响。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { DataOverview, QualityResult } from '@/types/datacenter';
import DataCenter from './index';

/* ---------------- 桩 ---------------- */

const h = vi.hoisted(() => ({
  setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn(),
  overview: vi.fn(), datasets: vi.fn(), quality: vi.fn(), logs: vi.fn(),
  taskStats: vi.fn(), status: vi.fn(), autoStatus: vi.fn(), sync: vi.fn(),
  instruments: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({ setOption: h.setOption, dispose: h.dispose, resize: h.resize }),
}));

vi.mock('@/api/datacenter', () => ({
  datacenterApi: {
    overview: h.overview, datasets: h.datasets, quality: h.quality, logs: h.logs,
    taskStats: h.taskStats, status: h.status, autoStatus: h.autoStatus,
    sync: h.sync, instruments: h.instruments,
    cancel: vi.fn(), task: vi.fn(), fetch: vi.fn(), autoToggle: vi.fn(),
    textStatus: vi.fn(), textImport: vi.fn(), textBuildFactor: vi.fn(),
    mirrorStatus: vi.fn(), mirrorRebuild: vi.fn(),
    trainReadiness: vi.fn(), trainStatus: vi.fn(), trainStart: vi.fn(),
    trainCancel: vi.fn(),
  },
}));

vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: (sel: (s: { user: { role: string } }) => unknown) =>
    sel({ user: { role: 'researcher' } }),
}));
vi.mock('@/components/RequireAuth', () => ({ hasMinimumRole: () => true }));

vi.mock('@/components/DataFreshness', () => ({ default: () => null }));
vi.mock('./TextDataPanel', () => ({ default: () => null }));
vi.mock('./TrainPanel', () => ({ default: () => null }));
vi.mock('./PitAndCorporateActionsPanel', () => ({ default: () => null }));
vi.mock('./TradabilityTimelinePanel', () => ({ default: () => null }));

const DUMMY_OVERVIEW: DataOverview = {
  storage_bytes: 1e9, storage_gb: 1, covered_total: 5208, covered_with_data: 5209,
  dataset_count: 12, last_sync: '2026-09-30 18:37:22', last_sync_source: 'pipeline',
  akshare_health: 'green',
  akshare_message: 'AKShare 连接正常，最近同步成功',
  disk_usage_percent: 60,
  daily_bar_range: ['2022-01-04', '2026-09-30'],
  last_job: null,
};

const MOCK_QUALITY: QualityResult = {
  checked: 100,
  total_missing_days: 18,
  affected_symbols: 2,
  items: [
    {
      dataset: 'daily_bar',
      symbol: '002656.SZ',
      missing_days: 15,
      first_missing: '2024-03-29',
      last_missing: '2024-04-18',
      sample: ['2024-03-29'],
    },
    {
      dataset: 'daily_bar',
      symbol: '510050.SH',
      missing_days: 3,
      first_missing: '2024-05-10',
      last_missing: '2024-05-13',
      sample: ['2024-05-10'],
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/data']}>
      <DataCenter />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  Object.values(h).forEach((f) => f.mockReset());
  h.overview.mockResolvedValue(DUMMY_OVERVIEW);
  h.datasets.mockResolvedValue({ items: [] });
  h.quality.mockResolvedValue(MOCK_QUALITY);
  h.logs.mockResolvedValue({ items: [] });
  h.taskStats.mockResolvedValue({ points: [] });
  h.status.mockResolvedValue({
    task_id: null, running: false, mode: null, total: 0, done: 0, percent: 0,
    current: '', error: null, elapsed_ms: 0, logs: [],
  });
  h.autoStatus.mockResolvedValue({ enabled: false, time: '15:45', today_done: false });
  h.instruments.mockResolvedValue({
    items: [], total: 0, returned: 0, truncated: false, limit: 50,
  });
  h.sync.mockResolvedValue({
    started: true, task_id: 't-1', mode: 'repair', total: 0, resume: false, skipped: 0,
  });
});

describe('数据中心：数据质量与缺漏标的跳转链接', () => {
  it('A 股个股代码为链接，href 指向个股详情页 (/stock/:symbol)', async () => {
    renderPage();
    const stockLink = await screen.findByRole('link', { name: '002656.SZ' });
    expect(stockLink).toBeInTheDocument();
    expect(stockLink).toHaveAttribute('href', '/stock/002656.SZ');
    expect(stockLink).toHaveAttribute('title', '查看 002656.SZ 详情');
  });

  it('ETF 代码自动识别并路由至 ETF 详情页 (/etf/:code)', async () => {
    renderPage();
    const etfLink = await screen.findByRole('link', { name: '510050.SH' });
    expect(etfLink).toBeInTheDocument();
    expect(etfLink).toHaveAttribute('href', '/etf/510050');
    expect(etfLink).toHaveAttribute('title', '查看 510050.SH 详情');
  });

  it('点击复选框选中标的不影响路由链接', async () => {
    renderPage();
    await screen.findByRole('link', { name: '002656.SZ' });
    const checkboxes = screen.getAllByRole('checkbox');
    // 找到表格行的复选框（最后一个或者按交互改变）
    const rowCheckbox = checkboxes.find(
      (cb) => (cb as HTMLInputElement).closest('tr')?.textContent?.includes('002656.SZ'),
    );
    expect(rowCheckbox).toBeDefined();
    expect((rowCheckbox as HTMLInputElement).checked).toBe(false);

    fireEvent.click(rowCheckbox!);
    expect((rowCheckbox as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText('已选 1 只')).toBeInTheDocument();
  });
});
