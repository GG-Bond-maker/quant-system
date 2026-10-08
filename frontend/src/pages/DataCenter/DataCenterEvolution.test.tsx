/**
 * 数据中心 P0/P1/P2 进化套件测试：
 * 1. 现代化四 Tab 导航切换与工作台分区渲染；
 * 2. 字段级 Schema 字典与样本抽样弹窗联动；
 * 3. 微观量价清洗雷达 (Micro QC Radar) 异常检测与健康分；
 * 4. PIT 财务披露时滞直方图与防前视偏差告示；
 * 5. 幸存者偏差防范与历史退市股票池治理看板；
 * 6. 坏数据隔离区 (Quarantine Vault) 策略与审计单导出。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  DataOverview,
  DatasetItem,
  DatasetPreviewResult,
  MicroQcRadarResult,
  PitLagDistributionResult,
  QuarantineVaultResult,
  SurvivorshipBiasAuditResult,
} from '@/types/datacenter';
import DataCenter from './index';

/* ---------------- 桩与模拟 ---------------- */

const h = vi.hoisted(() => ({
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
  overview: vi.fn(),
  datasets: vi.fn(),
  quality: vi.fn(),
  logs: vi.fn(),
  taskStats: vi.fn(),
  status: vi.fn(),
  autoStatus: vi.fn(),
  sync: vi.fn(),
  instruments: vi.fn(),
  datasetPreview: vi.fn(),
  microQcRadar: vi.fn(),
  survivorshipBias: vi.fn(),
  pitLagDistribution: vi.fn(),
  quarantineVault: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({ setOption: h.setOption, dispose: h.dispose, resize: h.resize }),
  graphic: {
    LinearGradient: vi.fn(),
  },
}));

vi.mock('@/api/datacenter', () => ({
  datacenterApi: {
    overview: h.overview,
    datasets: h.datasets,
    quality: h.quality,
    logs: h.logs,
    taskStats: h.taskStats,
    status: h.status,
    autoStatus: h.autoStatus,
    sync: h.sync,
    instruments: h.instruments,
    cancel: vi.fn(),
    task: vi.fn(),
    fetch: vi.fn(),
    autoToggle: vi.fn(),
    textStatus: vi.fn(),
    textImport: vi.fn(),
    textBuildFactor: vi.fn(),
    mirrorStatus: vi.fn(),
    mirrorRebuild: vi.fn(),
    trainReadiness: vi.fn(),
    trainStatus: vi.fn(),
    trainStart: vi.fn(),
    trainCancel: vi.fn(),
    pitFinancials: vi.fn(),
    corporateActions: vi.fn(),
    tradabilityTimeline: vi.fn(),
    datasetPreview: h.datasetPreview,
    microQcRadar: h.microQcRadar,
    survivorshipBias: h.survivorshipBias,
    pitLagDistribution: h.pitLagDistribution,
    quarantineVault: h.quarantineVault,
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

const MOCK_OVERVIEW: DataOverview = {
  storage_bytes: 2.4e9,
  storage_gb: 2.4,
  covered_total: 5208,
  covered_with_data: 5200,
  dataset_count: 8,
  last_sync: '2026-10-08 14:00:00',
  last_sync_source: 'pipeline',
  akshare_health: 'green',
  akshare_message: 'AKShare 连接正常',
  disk_usage_percent: 45,
  daily_bar_range: ['2020-01-01', '2026-10-08'],
  last_job: null,
};

const MOCK_DATASETS: DatasetItem[] = [
  {
    dataset: 'daily_bar',
    table: 'daily_bar',
    label: '日线行情 (未复权)',
    rows: 15000000,
    start: '2020-01-01',
    end: '2026-10-08',
    source: 'akshare',
    symbols: 5200,
    bytes: 1.2e9,
    adjust_status: '不适用',
  },
];

const MOCK_PREVIEW: DatasetPreviewResult = {
  dataset: 'daily_bar',
  table: 'daily_bar',
  label: '日线行情 (未复权)',
  total_rows: 15000000,
  schema: [
    { name: 'symbol', dtype: 'string', label: '股票代码', unit: '', null_ratio: 0 },
    { name: 'date', dtype: 'string', label: '交易日期', unit: '', null_ratio: 0 },
    { name: 'close', dtype: 'float64', label: '收盘价', unit: '元', null_ratio: 0.001 },
  ],
  records: [
    { symbol: '000001.SZ', date: '2026-10-08', close: 11.58 },
  ],
};

const MOCK_RADAR: MicroQcRadarResult = {
  total_audited_records: 12000,
  anomaly_count: 5,
  jump_count: 3,
  ohlc_breach_count: 1,
  zero_vol_count: 1,
  health_score: 98.5,
  status: 'HEALTHY',
  anomalies: [
    {
      symbol: '000001.SZ',
      date: '2026-09-10',
      kind: 'MAD_JUMP',
      value: 12.8,
      threshold: '±10.0%',
      severity: 'warn',
      message: '收益率偏离中位数 4.8 个 MAD 差值',
    },
  ],
};

const MOCK_SURVIVORSHIP: SurvivorshipBiasAuditResult = {
  active_stocks_count: 5208,
  delisted_stocks_count: 214,
  survivorship_protection_score: 96.5,
  protection_level: 'PROTECTED · 防范完备',
  delisted_stocks: [
    {
      symbol: '600086.SH',
      name: '退市东方',
      ipo_date: '1997-07-11',
      delist_date: '2022-06-30',
      reason_category: '连续亏损财务类',
      last_price: 0.42,
      coverage_status: '已纳档',
    },
  ],
  bias_impact_assessment: {
    annualized_return_overestimate_pct: 3.4,
    max_drawdown_underestimate_pct: 6.8,
    universe_rule: '动态可交易股票池',
    summary: '已纳档退市标的，防范年化收益虚高',
  },
};

const MOCK_PIT_LAG: PitLagDistributionResult = {
  total_reports: 84520,
  avg_lag_days: 42.8,
  median_lag_days: 38.0,
  p25_lag_days: 26.0,
  p75_lag_days: 68.0,
  delayed_count: 1420,
  delayed_ratio_pct: 1.68,
  histogram: [
    { range: '< 30天', count: 28000, pct: 33.1 },
    { range: '30-60天', count: 35000, pct: 41.4 },
    { range: '60-90天', count: 12000, pct: 14.2 },
    { range: '90-120天', count: 8100, pct: 9.6 },
    { range: '≥ 120天延期', count: 1420, pct: 1.7 },
  ],
  by_quarter: {
    Q1: { count: 21000, median: 26, avg: 25.4 },
    Q2: { count: 21000, median: 52, avg: 53.1 },
    Q3: { count: 21000, median: 27, avg: 26.8 },
    Q4: { count: 21520, median: 108, avg: 106.5 },
  },
  note: '严禁在回测中以报告期截止日使用财务因子',
};

const MOCK_VAULT: QuarantineVaultResult = {
  total_quarantined: 3,
  vault_status: 'CLEAN',
  quarantined_items: [
    {
      id: 'Q-001',
      dataset: 'daily_bar',
      symbol: '000001.SZ',
      date: '2026-09-01',
      reason: 'High < Low 物理越界',
      severity: 'error',
      quarantined_at: '2026-09-02 01:00:00',
      suggested_action: '重抓该日分时逐笔并修正',
    },
  ],
  quarantine_policy: {
    auto_isolation_enabled: true,
    rules: ['物理几何越界立即隔离', 'MAD跳空极值暂缓入库'],
    note: '质检引擎在特征工程读取前自动阻断脏数据入库',
  },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/data']}>
      <DataCenter />
    </MemoryRouter>,
  );
}

describe('数据中心进化工作台 (DataCenter Evolution)', () => {
  beforeEach(() => {
    localStorage.clear();
    Object.values(h).forEach((f) => f.mockReset());
    h.overview.mockResolvedValue(MOCK_OVERVIEW);
    h.datasets.mockResolvedValue({ items: MOCK_DATASETS });
    h.quality.mockResolvedValue({ checked: 100, total_missing_days: 0, affected_symbols: 0, items: [] });
    h.logs.mockResolvedValue({ items: [] });
    h.taskStats.mockResolvedValue({ points: [] });
    h.status.mockResolvedValue({ running: false, mode: null, total: 0, done: 0, percent: 0, current: '', error: null, elapsed_ms: 0, logs: [] });
    h.autoStatus.mockResolvedValue({ enabled: false, time: '15:45', today_done: false });
    h.instruments.mockResolvedValue({ items: [], total: 0, returned: 0 });
    h.datasetPreview.mockResolvedValue(MOCK_PREVIEW);
    h.microQcRadar.mockResolvedValue(MOCK_RADAR);
    h.survivorshipBias.mockResolvedValue(MOCK_SURVIVORSHIP);
    h.pitLagDistribution.mockResolvedValue(MOCK_PIT_LAG);
    h.quarantineVault.mockResolvedValue(MOCK_VAULT);
  });

  it('初始正确渲染四大专业 Tab 切换导航', async () => {
    renderPage();

    expect(await screen.findByText(/资产与同步全景/)).toBeInTheDocument();
    expect(screen.getByText(/微观质检与雷达/)).toBeInTheDocument();
    expect(screen.getByText(/PIT截面与时滞/)).toBeInTheDocument();
    expect(screen.getByText(/股票池与流水线/)).toBeInTheDocument();
  });

  it('支持在资产清单点击「🔍 字典采样」打开 Schema 字典与截面预览弹窗', async () => {
    renderPage();

    const previewBtn = await screen.findByRole('button', { name: /字典采样/ });
    expect(previewBtn).toBeInTheDocument();

    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(h.datasetPreview).toHaveBeenCalledWith('daily_bar', 10);
    });

    expect(await screen.findByText(/数据字典与截面采样/)).toBeInTheDocument();
    expect(screen.getByText('收盘价')).toBeInTheDocument();
    expect(screen.getByText('float64')).toBeInTheDocument();
  });

  it('切换到「微观质检与雷达」Tab 成功渲染质检雷达与隔离沙盒', async () => {
    renderPage();

    const qualityTabBtn = screen.getByText(/微观质检与雷达/);
    fireEvent.click(qualityTabBtn);

    await waitFor(() => {
      expect(h.microQcRadar).toHaveBeenCalled();
      expect(h.quarantineVault).toHaveBeenCalled();
    });

    expect(await screen.findByText('微观量价清洗雷达 (Micro QC Radar)')).toBeInTheDocument();
    expect(screen.getByText('98.5 / 100')).toBeInTheDocument();
    expect(screen.getByText('坏数据隔离区 (Quarantine Vault)')).toBeInTheDocument();
  });

  it('切换到「PIT截面与时滞」Tab 成功渲染披露时滞与分位统计', async () => {
    renderPage();

    const pitTabBtn = screen.getByText(/PIT截面与时滞/);
    fireEvent.click(pitTabBtn);

    await waitFor(() => {
      expect(h.pitLagDistribution).toHaveBeenCalled();
    });

    expect(await screen.findByText(/全市场 PIT 财务披露时滞分布/)).toBeInTheDocument();
    expect(screen.getByText(/中位数时滞 38 天/)).toBeInTheDocument();
  });

  it('切换到「股票池与流水线」Tab 成功渲染幸存者偏差与退市标的库', async () => {
    renderPage();

    const universeTabBtn = screen.getByText(/股票池与流水线/);
    fireEvent.click(universeTabBtn);

    await waitFor(() => {
      expect(h.survivorshipBias).toHaveBeenCalled();
    });

    expect(await screen.findByText(/幸存者偏差防范与退市标的治理/)).toBeInTheDocument();
    expect(screen.getByText('96.5 / 100')).toBeInTheDocument();
    expect(screen.getByText('退市东方')).toBeInTheDocument();
  });

  it('点击「导出数据健康审计报告」弹窗并支持复制 Markdown', async () => {
    renderPage();

    const exportBtn = screen.getByRole('button', { name: /导出数据健康审计报告/ });
    fireEvent.click(exportBtn);

    expect(await screen.findByText(/数据中心工业级健康与合规审计报告/)).toBeInTheDocument();
    expect(screen.getByText(/工业级全景健康快照/)).toBeInTheDocument();
  });
});
