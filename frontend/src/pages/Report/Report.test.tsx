import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MarketCompassCard } from './MarketCompassCard';
import { BrinsonAttributionCard } from './BrinsonAttributionCard';
import { SignalPreviewCard } from './SignalPreviewCard';
import Report from './index';
import type { MarketCompass, BrinsonSummary, BrinsonSector, DailyReport } from '@/api/monitor';

const h = vi.hoisted(() => ({
  report: vi.fn(),
  generateReport: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
  }),
}));

vi.mock('@/api/monitor', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/monitor')>();
  return {
    ...actual,
    monitorApi: {
      ...actual.monitorApi,
      report: h.report,
      generateReport: h.generateReport,
    },
  };
});

describe('AI 日报增强组件测试', () => {
  const mockCompass: MarketCompass = {
    trading_date: '2026-09-30',
    up_count: 3200,
    down_count: 1800,
    flat_count: 208,
    limit_up_count: 65,
    limit_down_count: 8,
    adl_change: 1400,
    turnover_gini: 0.528,
    sentiment_score: 68,
    sentiment_label: '积极',
  };

  const mockSummary: BrinsonSummary = {
    portfolio_return: 0.045,
    benchmark_return: 0.012,
    excess_return: 0.033,
    allocation: 0.011,
    selection: 0.018,
    interaction: 0.0042,
    residual_alpha: 0.0,
  };

  const mockSectors: BrinsonSector[] = [
    {
      industry: '电子',
      allocation: 0.006,
      selection: 0.012,
      total: 0.018,
    },
    {
      industry: '计算机',
      allocation: 0.004,
      selection: 0.005,
      total: 0.009,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('MarketCompassCard 渲染全市场情绪、腾落差与基尼系数', () => {
    render(<MarketCompassCard compass={mockCompass} />);

    expect(screen.getByText('全市场微观流动性与情绪罗盘')).toBeInTheDocument();
    expect(screen.getByText('基准日: 2026-09-30')).toBeInTheDocument();
    expect(screen.getAllByText(/积极/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('68').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/腾落差 \+1400/)).toBeInTheDocument();
    expect(screen.getByText(/涨 3200/)).toBeInTheDocument();
    expect(screen.getByText(/跌 1800/)).toBeInTheDocument();
    expect(screen.getByText('65')).toBeInTheDocument(); // 涨停
    expect(screen.getByText('8')).toBeInTheDocument(); // 跌停
    expect(screen.getAllByText('0.528').length).toBeGreaterThanOrEqual(1); // 基尼系数
  });

  it('BrinsonAttributionCard 渲染配置/选股超额分解与行业贡献明细', () => {
    render(<BrinsonAttributionCard summary={mockSummary} sectors={mockSectors} />);

    expect(screen.getByText('Brinson 绩效归因（行业配置 vs 个股选择）')).toBeInTheDocument();
    expect(screen.getByText('+3.30%')).toBeInTheDocument(); // excess
    expect(screen.getByText('+1.10%')).toBeInTheDocument(); // allocation
    expect(screen.getAllByText('+1.80%').length).toBeGreaterThanOrEqual(1); // selection & sector total
    expect(screen.getByText('+0.42%')).toBeInTheDocument(); // interaction

    // 行业明细
    expect(screen.getByText('电子')).toBeInTheDocument();
    expect(screen.getByText('计算机')).toBeInTheDocument();
  });

  it('SignalPreviewCard 渲染次日调仓预期换手、冲击滑点与标的明细', () => {
    const mockPreview = {
      target_date: '2026-09-30',
      expected_turnover: 0.185,
      estimated_slippage_bps: 9.2,
      n_target_positions: 20,
      buys: [
        {
          symbol: '000001.SZ',
          action: 'BUY' as const,
          target_weight: 0.05,
          current_weight: 0.0,
          weight_delta: 0.05,
          score: 0.885,
        },
      ],
      sells: [
        {
          symbol: '600519.SH',
          action: 'SELL' as const,
          target_weight: 0.0,
          current_weight: 0.04,
          weight_delta: -0.04,
          score: 0.12,
        },
      ],
      summary: '次日调仓预期单边换手率 18.5%，预估市场冲击滑点 9.2 bps',
    };

    render(<SignalPreviewCard preview={mockPreview} />);

    expect(screen.getByText('次日调仓推演与换手冲击预估（多因子信号预演）')).toBeInTheDocument();
    expect(screen.getByText('18.50%')).toBeInTheDocument(); // turnover
    expect(screen.getByText('9.2 bps')).toBeInTheDocument(); // slippage
    expect(screen.getByText('000001.SZ')).toBeInTheDocument(); // buy sym
    expect(screen.getByText('+5.00%')).toBeInTheDocument(); // buy delta
    expect(screen.getByText('600519.SH')).toBeInTheDocument(); // sell sym
    expect(screen.getByText('-4.00%')).toBeInTheDocument(); // sell delta
  });

  it('Report 页面集成：成功加载并渲染市场罗盘、Brinson 归因卡与次日调仓卡', async () => {
    const mockReport: DailyReport = {
      date: '2026-09-30',
      generated_at: '2026-09-30T17:30:00',
      sections: [
        {
          title: '一、数据面',
          lines: ['- 行情最新日期：2026-09-30'],
        },
      ],
      markdown: '## 一、数据面\n- 行情最新日期：2026-09-30',
      tips: ['因子处于健康范围'],
      market_compass: mockCompass,
      brinson_summary: mockSummary,
      brinson_sectors: mockSectors,
      signal_preview: {
        target_date: '2026-09-30',
        expected_turnover: 0.15,
        estimated_slippage_bps: 8.5,
        n_target_positions: 20,
        buys: [],
        sells: [],
        summary: '预期换手 15.0%',
      },
    };

    h.report.mockResolvedValueOnce({
      report: mockReport,
      history: ['2026-09-30'],
    });

    render(<Report />);

    await waitFor(() => {
      expect(screen.getByText('AI 日报')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText('全市场微观流动性与情绪罗盘')).toBeInTheDocument();
      expect(screen.getByText('Brinson 绩效归因（行业配置 vs 个股选择）')).toBeInTheDocument();
      expect(screen.getByText('次日调仓推演与换手冲击预估（多因子信号预演）')).toBeInTheDocument();
      expect(screen.getByText('一、数据面')).toBeInTheDocument();
    });
  });
});
