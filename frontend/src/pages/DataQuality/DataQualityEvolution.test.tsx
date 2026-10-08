import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import DataQuality from './index';
import QualityRadarCard from './QualityRadarCard';
import CrossSectionalCoverageCard from './CrossSectionalCoverageCard';
import LineageBlastRadiusModal from './LineageBlastRadiusModal';
import QualityAuditReportModal from './QualityAuditReportModal';
import type {
  QualityScanResult,
  LineageGraph,
  QualityRadarResult,
  CrossSectionalCoverageResult,
  BlastRadiusResult,
  MultiSourceArbitrageResult,
  FeatureDriftResult,
} from '@/api/production';

const h = vi.hoisted(() => ({
  qualityScan: vi.fn(),
  lineage: vi.fn(),
  qualityRadar: vi.fn(),
  crossSectionalCoverage: vi.fn(),
  blastRadius: vi.fn(),
  multiSourceArbitrage: vi.fn(),
  featureDrift: vi.fn(),
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: h.setOption,
    dispose: h.dispose,
    resize: h.resize,
  }),
}));

vi.mock('@/api/production', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/production')>();
  return {
    ...actual,
    opsApi: {
      ...actual.opsApi,
      qualityScan: h.qualityScan,
      lineage: h.lineage,
      qualityRadar: h.qualityRadar,
      crossSectionalCoverage: h.crossSectionalCoverage,
      blastRadius: h.blastRadius,
      multiSourceArbitrage: h.multiSourceArbitrage,
      featureDrift: h.featureDrift,
    },
  };
});

vi.mock('@/api/monitor', () => ({
  monitorApi: {
    health: vi.fn().mockResolvedValue({
      healthy_count: 8,
      watch_count: 2,
      degraded_count: 0,
      factors: [],
    }),
    run: vi.fn().mockResolvedValue({ success: true }),
  },
}));

describe('DataQuality 数据质量与治理工作台进化测试', () => {
  const mockScan: QualityScanResult = {
    dataset: 'daily_bar',
    year: 2026,
    symbols_scanned: 5200,
    symbols_available: 5200,
    total_symbols: 5200,
    truncated: false,
    scan_limit: 5200,
    rows_scanned: 1250000,
    n_issues: 3,
    n_errors: 1,
    by_kind: {
      daily_return: 1,
      ohlc: 1,
      null: 1,
    },
    worst_symbols: [['600519.SH', 1]],
    samples: [
      {
        dataset: 'daily_bar',
        symbol: '300059.SZ',
        date: '2026-09-30',
        kind: 'daily_return',
        severity: 'warning',
        detail: '涨跌幅 18.5% 异常 (创业板缓冲上限 22%)',
      },
      {
        dataset: 'daily_bar',
        symbol: '600519.SH',
        date: '2026-09-30',
        kind: 'ohlc',
        severity: 'error',
        detail: 'High < Low 几何违背',
      },
      {
        dataset: 'daily_bar',
        symbol: '000001.SZ',
        date: '2026-09-30',
        kind: 'null',
        severity: 'warning',
        detail: '成交量字段存在空值',
      },
    ],
  };

  const mockLineage: LineageGraph = {
    nodes: [
      { id: 'source:tushare', name: 'Tushare 行情', group: 'source', status: 'ok', rows: 500000 },
      { id: 'raw:daily_bar', name: '日频行情 Raw', group: 'raw', status: 'ok', rows: 1250000 },
      { id: 'feature:alpha_momentum', name: '动量因子表', group: 'feature', status: 'ok', records: 800000 },
    ],
    edges: [
      { source: 'source:tushare', target: 'raw:daily_bar' },
      { source: 'raw:daily_bar', target: 'feature:alpha_momentum' },
    ],
    note: '静态拓扑测试',
  };

  const mockRadar: QualityRadarResult = {
    dataset: 'daily_bar',
    composite_score: 94.6,
    grade: 'AAA',
    audited_at: '2026-09-30 15:00:00',
    summary: '数据质量表现极佳，满足实盘高频与多因子计算严苛标准',
    dimensions: [
      { key: 'completeness', name: '完整性 (Completeness)', score: 98.2, weight: 0.25, benchmark: 95.0, status: 'EXCELLENT', detail: '全市场截面与时序覆盖' },
      { key: 'accuracy', name: '准确性 (Accuracy)', score: 96.5, weight: 0.25, benchmark: 95.0, status: 'EXCELLENT', detail: 'OHLC 几何合规与板块限价' },
      { key: 'consistency', name: '一致性 (Consistency)', score: 93.0, weight: 0.20, benchmark: 90.0, status: 'GOOD', detail: '主备数据源交叉对账' },
      { key: 'stability', name: '稳定性 (Stability)', score: 91.8, weight: 0.15, benchmark: 85.0, status: 'GOOD', detail: '时序缺失与协变量漂移' },
      { key: 'timeliness', name: '及时性 (Timeliness)', score: 93.5, weight: 0.15, benchmark: 90.0, status: 'GOOD', detail: '数据就绪与落地延迟' },
    ],
  };

  const mockCoverage: CrossSectionalCoverageResult = {
    total_dates: 30,
    expected_universe_size: 5240,
    mean_coverage_pct: 99.2,
    min_coverage_pct: 98.5,
    dates: ['2026-09-28', '2026-09-29', '2026-09-30'],
    categories: ['主板 (Main)', '创业板 (ChiNext)', '科创板 (STAR)', '北交所 (BSE)'],
    series: [],
    heatmap_data: [
      [0, 0, 99.8],
      [1, 0, 99.7],
      [2, 0, 99.9],
      [0, 1, 98.5],
      [1, 1, 98.7],
      [2, 1, 98.6],
    ],
    note: '截面覆盖良好',
  };

  const mockBlastRadius: BlastRadiusResult = {
    source_node: { id: 'daily_bar', name: '日频行情 Raw', group: 'raw' },
    total_impacted: 3,
    impacted_nodes: [
      { id: 'daily_bar', name: '日频行情 Raw', group: 'raw', distance: 0, severity_impact: 'CRITICAL', desc: '故障源头' },
      { id: 'feature:alpha_momentum', name: '动量因子表', group: 'feature', distance: 1, severity_impact: 'HIGH', desc: '下游特征' },
      { id: 'model:xgboost_alpha', name: 'XGBoost 选股模型', group: 'model', distance: 2, severity_impact: 'MEDIUM', desc: '下游模型' },
    ],
    affected_alpha_factors: ['momentum_20d', 'reversal_5d'],
    affected_models: ['xgboost_alpha_v1'],
    blast_summary: '受波及节点 3 个',
    remediation_advice: '建议隔离下游因子与模型的执行调度',
  };

  const mockArbitrage: MultiSourceArbitrageResult = {
    trade_date: '2026-09-30',
    total_checked: 50,
    consistent_count: 48,
    tolerable_count: 2,
    discrepancy_count: 0,
    tolerance_corridor_pct: 0.3,
    mean_deviation_pct: 0.0012,
    max_deviation_pct: 0.045,
    items: [],
  };

  const mockDrift: FeatureDriftResult = {
    factor: 'ret_20',
    version: 'v1',
    baseline_year: 2025,
    target_year: 2026,
    psi: 0.008,
    drift_level: 'stable',
    ks_statistic: 0.0123,
    ks_pvalue: 0.85,
    baseline_samples: 50000,
    target_samples: 50000,
    available_factors: ['ret_20'],
    bins: [
      { bin: 1, lower: -0.1, upper: 0.0, expected_pct: 50.0, actual_pct: 48.0 },
      { bin: 2, lower: 0.0, upper: 0.1, expected_pct: 50.0, actual_pct: 52.0 },
    ],
    note: '漂移检验正常',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    h.qualityScan.mockResolvedValue(mockScan);
    h.lineage.mockResolvedValue(mockLineage);
    h.qualityRadar.mockResolvedValue(mockRadar);
    h.crossSectionalCoverage.mockResolvedValue(mockCoverage);
    h.blastRadius.mockResolvedValue(mockBlastRadius);
    h.multiSourceArbitrage.mockResolvedValue(mockArbitrage);
    h.featureDrift.mockResolvedValue(mockDrift);
  });

  it('正确渲染 DataQuality 4-Tab 架构并支持 Tab 切换', async () => {
    render(
      <MemoryRouter>
        <DataQuality />
      </MemoryRouter>
    );

    // 验证页面主标题与 Tab 栏存在
    expect(screen.getByText('数据质量与血缘监控中心')).toBeInTheDocument();
    expect(screen.getByText(/L1 Market QC/)).toBeInTheDocument();
    expect(screen.getByText(/L2 Feature Store/)).toBeInTheDocument();
    expect(screen.getByText(/L3 Alpha Health/)).toBeInTheDocument();
    expect(screen.getByText(/L4 Lineage & Blast/)).toBeInTheDocument();

    // 默认展示 Tab 1：包含质量雷达卡、多源对账
    await waitFor(() => {
      expect(screen.getByText(/多源行情高频交叉对账与偏离度仲裁/)).toBeInTheDocument();
    });

    // 切换至 Tab 2: 特征工程与截面完整性
    fireEvent.click(screen.getByText(/L2 Feature Store/));
    await waitFor(() => {
      expect(screen.getByText(/全市场截面完整性与因子空值时序分布/)).toBeInTheDocument();
      expect(screen.getByText(/特征协变量漂移监控/)).toBeInTheDocument();
    });

    // 切换至 Tab 3: 因子生产健康与衰退监控
    fireEvent.click(screen.getByText(/L3 Alpha Health/));
    await waitFor(() => {
      expect(screen.getByText(/因子健康度（生产信号监控）/)).toBeInTheDocument();
    });

    // 切换至 Tab 4: 全链路血缘与受损扩散
    fireEvent.click(screen.getByText(/L4 Lineage & Blast/));
    await waitFor(() => {
      expect(screen.getByText(/数据血缘全链路拓扑图谱/)).toBeInTheDocument();
    });
  });

  it('支持告警明细过滤与受损扩散分析弹窗联动', async () => {
    render(
      <MemoryRouter>
        <DataQuality />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('High < Low 几何违背')).toBeInTheDocument();
      expect(screen.getByText('涨跌幅 18.5% 异常 (创业板缓冲上限 22%)')).toBeInTheDocument();
    });

    // 类别筛选: 点击 ohlc 过滤
    fireEvent.click(screen.getByRole('button', { name: 'ohlc' }));
    expect(screen.getByText('High < Low 几何违背')).toBeInTheDocument();
    expect(screen.queryByText('涨跌幅 18.5% 异常 (创业板缓冲上限 22%)')).not.toBeInTheDocument();

    // 点击该行关联的「扩散半径」按钮触发 BlastRadiusModal
    const blastButtons = screen.getAllByRole('button', { name: /扩散半径/ });
    expect(blastButtons.length).toBeGreaterThan(0);
    fireEvent.click(blastButtons[0]);

    // 验证扩散弹窗被打开
    await waitFor(() => {
      expect(screen.getByText(/数据血缘拓扑受损扩散分析/)).toBeInTheDocument();
      expect(screen.getByText(/波及下游节点: 3 个/)).toBeInTheDocument();
      expect(screen.getByText('momentum_20d')).toBeInTheDocument();
    });
  });

  it('QualityAuditReportModal 独立测试：渲染合规审计报告与导出', async () => {
    const handleClose = vi.fn();
    render(
      <QualityAuditReportModal
        open={true}
        onClose={handleClose}
        scan={mockScan}
        dataset="daily_bar"
      />
    );

    expect(screen.getByText('数据质量与血缘全链路合规审计报告')).toBeInTheDocument();
    expect(screen.getByText(/核心审计概览快照/)).toBeInTheDocument();
    expect(screen.getByText(/板块自适应涨跌幅判定/)).toBeInTheDocument();

    const copyBtn = screen.getByRole('button', { name: /复制 Markdown/ });
    expect(copyBtn).toBeInTheDocument();
  });

  it('QualityRadarCard 独立测试：渲染五维雷达评分与评级', async () => {
    render(<QualityRadarCard dataset="daily_bar" />);

    await waitFor(() => {
      expect(screen.getByText('完整性 (Completeness)')).toBeInTheDocument();
      expect(screen.getByText('准确性 (Accuracy)')).toBeInTheDocument();
      expect(screen.getAllByText(/AAA/).length).toBeGreaterThan(0);
      expect(screen.getByText(/数据质量表现极佳/)).toBeInTheDocument();
    });
  });

  it('CrossSectionalCoverageCard 独立测试：渲染全市场截面热力图与指标', async () => {
    render(<CrossSectionalCoverageCard />);

    await waitFor(() => {
      expect(screen.getAllByText(/99.2%/).length).toBeGreaterThan(0);
      expect(screen.getByText(/5240/)).toBeInTheDocument();
      expect(screen.getByText(/全市场动态基准股票池/)).toBeInTheDocument();
    });

    // 切换时序跨度
    const select = screen.getByDisplayValue('近 30 个交易日');
    fireEvent.change(select, { target: { value: '60' } });
    await waitFor(() => {
      expect(h.crossSectionalCoverage).toHaveBeenCalledWith(60);
    });
  });

  it('LineageBlastRadiusModal 独立测试：正常关闭与加载受波及因子', async () => {
    const handleClose = vi.fn();
    const { rerender } = render(
      <LineageBlastRadiusModal nodeId="daily_bar" open={true} onClose={handleClose} />
    );

    await waitFor(() => {
      expect(screen.getByText(/数据血缘拓扑受损扩散分析/)).toBeInTheDocument();
      expect(screen.getByText('xgboost_alpha_v1')).toBeInTheDocument();
    });

    // 点击底部的「关闭」按钮
    const closeButtons = screen.getAllByRole('button', { name: '关闭' });
    expect(closeButtons.length).toBeGreaterThan(0);
    fireEvent.click(closeButtons[closeButtons.length - 1]);
    expect(handleClose).toHaveBeenCalled();

    // 当 open 为 false 时不展示
    rerender(<LineageBlastRadiusModal nodeId="daily_bar" open={false} onClose={handleClose} />);
  });
});
