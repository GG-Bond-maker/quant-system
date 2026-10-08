import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ReportKpiCards } from './ReportKpiCards';
import { StyleFactorsCard } from './StyleFactorsCard';
import { PortfolioRiskCard } from './PortfolioRiskCard';
import { AiSynthesisCard } from './AiSynthesisCard';
import { MarketCompassCard } from './MarketCompassCard';
import { SignalPreviewCard } from './SignalPreviewCard';
import Report from './index';
import type {
  BrinsonSummary,
  DailyReport,
  MarketCompass,
  PortfolioRiskData,
  ReportSynthesis,
  SignalPreview,
  StyleFactorsData,
} from '@/api/monitor';

const h = vi.hoisted(() => ({
  report: vi.fn(),
  generateReport: vi.fn(),
  dispatchReport: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
  }),
}));

vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: (sel: (s: { user: { role: string } }) => unknown) =>
    sel({ user: { role: 'researcher' } }),
}));

vi.mock('@/api/monitor', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/monitor')>();
  return {
    ...actual,
    monitorApi: {
      ...actual.monitorApi,
      report: h.report,
      generateReport: h.generateReport,
      dispatchReport: h.dispatchReport,
    },
  };
});

describe('AI 日报 P0/P1/P2 演进重构组件测试', () => {
  const mockCompass: MarketCompass = {
    trading_date: '2026-10-08',
    up_count: 3100,
    down_count: 1750,
    flat_count: 250,
    limit_up_count: 58,
    limit_down_count: 6,
    adl_change: 1350,
    turnover_gini: 0.542,
    sentiment_score: 66,
    sentiment_label: '积极',
    breadth_bins: [
      { bin: '<-7%', count: 18 },
      { bin: '[-7%, -5%)', count: 42 },
      { bin: '[-5%, -3%)', count: 180 },
      { bin: '[-3%, 0%)', count: 1510 },
      { bin: '[0%, +3%)', count: 2150 },
      { bin: '[+3%, +5%)', count: 620 },
      { bin: '[+5%, +7%)', count: 272 },
      { bin: '>=+7%', count: 308 },
    ],
  };

  const mockBrinson: BrinsonSummary = {
    portfolio_return: 0.038,
    benchmark_return: 0.012,
    excess_return: 0.026,
    allocation: 0.009,
    selection: 0.015,
    interaction: 0.002,
  };

  const mockPreview: SignalPreview = {
    target_date: '2026-10-09',
    expected_turnover: 0.165,
    estimated_slippage_bps: 8.8,
    n_target_positions: 20,
    buys: [
      {
        symbol: '000001.SZ',
        action: 'BUY',
        target_weight: 0.055,
        current_weight: 0.01,
        weight_delta: 0.045,
        score: 0.892,
      },
    ],
    sells: [
      {
        symbol: '600519.SH',
        action: 'SELL',
        target_weight: 0.0,
        current_weight: 0.04,
        weight_delta: -0.04,
        score: 0.18,
      },
    ],
    summary: '预期单边换手 16.5%，预估冲击 8.8 bps',
  };

  const mockRisk: PortfolioRiskData = {
    portfolio_var_95: 2.15,
    portfolio_beta: 1.05,
    max_stock_weight: 6.8,
    top3_concentration: 18.5,
    liquidation_days: 1.2,
    leverage_ratio: 0.98,
    status: 'PASS',
  };

  const mockStyles: StyleFactorsData = {
    styles: [
      { name: 'Size (市值规模)', code: 'size', return: 0.0035, description: '大盘蓝筹相对微盘股超额' },
      { name: 'Momentum (动量趋势)', code: 'momentum', return: 0.0062, description: '20日动量多头超额' },
    ],
    top_industries: [
      { industry: '电子', return: 0.024 },
      { industry: '通信', return: 0.018 },
    ],
    bottom_industries: [
      { industry: '房地产', return: -0.019 },
      { industry: '建筑材料', return: -0.012 },
    ],
  };

  const mockSynthesis: ReportSynthesis = {
    macro_summary: '今日全市场微观情绪录得 66 分，呈积极态势。',
    attribution_takeaway: '组合实现窗口超额收益 +2.60%，选股 Alpha 贡献显著。',
    factor_health_verdict: '核心 Alpha 因子健康度评级为 healthy，RankIC 稳定。',
    execution_guidance: '建议次日调仓采用 VWAP 算法执行，防范开盘冲击。',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it('ReportKpiCards 正确渲染 4 联全局态势指标卡', () => {
    render(
      <ReportKpiCards
        brinson={mockBrinson}
        compass={mockCompass}
        preview={mockPreview}
        risk={mockRisk}
      />,
    );

    // 1. 超额收益
    expect(screen.getByText('窗口组合超额 (Active Return)')).toBeInTheDocument();
    expect(screen.getByText('+2.60%')).toBeInTheDocument();

    // 2. 微观情绪
    expect(screen.getByText('全市场微观情绪指数')).toBeInTheDocument();
    expect(screen.getByText('66')).toBeInTheDocument();
    expect(screen.getByText('积极')).toBeInTheDocument();
    expect(screen.getByText('+1350')).toBeInTheDocument();

    // 3. 次日换手与滑点
    expect(screen.getByText('次日预期单边换手 & 滑点')).toBeInTheDocument();
    expect(screen.getByText('16.5%')).toBeInTheDocument();
    expect(screen.getByText('8.8 bps')).toBeInTheDocument();

    // 4. 组合在险价值
    expect(screen.getByText('组合在险价值 (95% 1d VaR)')).toBeInTheDocument();
    expect(screen.getByText('2.15%')).toBeInTheDocument();
    expect(screen.getByText('PASS')).toBeInTheDocument();
  });

  it('StyleFactorsCard 渲染 Barra 风格因子多空收益与行业轮动排行', () => {
    render(<StyleFactorsCard data={mockStyles} />);

    expect(screen.getByText('Barra 风格因子每日全景与行业轮动透视')).toBeInTheDocument();
    expect(screen.getByText('Size (市值规模)')).toBeInTheDocument();
    expect(screen.getByText('+35.0 bps')).toBeInTheDocument();
    expect(screen.getByText('Momentum (动量趋势)')).toBeInTheDocument();
    expect(screen.getByText('+62.0 bps')).toBeInTheDocument();

    // 行业轮动
    expect(screen.getByText('▲ 领涨行业 Top 3')).toBeInTheDocument();
    expect(screen.getByText('电子')).toBeInTheDocument();
    expect(screen.getByText('+2.40%')).toBeInTheDocument();
    expect(screen.getByText('▼ 领跌行业 Bottom 3')).toBeInTheDocument();
    expect(screen.getByText('房地产')).toBeInTheDocument();
    expect(screen.getByText('-1.90%')).toBeInTheDocument();
  });

  it('PortfolioRiskCard 渲染 95% VaR、Beta、单股上限与变现天数', () => {
    render(<PortfolioRiskCard risk={mockRisk} />);

    expect(screen.getByText('组合在险价值 (VaR) 与风控雷达监控')).toBeInTheDocument();
    expect(screen.getByText('2.15%')).toBeInTheDocument();
    expect(screen.getByText('1.05')).toBeInTheDocument();
    expect(screen.getByText('6.80%')).toBeInTheDocument();
    expect(screen.getByText('18.50%')).toBeInTheDocument();
    expect(screen.getByText('1.2 天')).toBeInTheDocument();
    expect(screen.getByText('风控合规正常 (PASS)')).toBeInTheDocument();
  });

  it('AiSynthesisCard 渲染 4 维投委会智能综述与风险提示', () => {
    const tips = ['关注大盘微盘股风格切换风险'];
    render(<AiSynthesisCard synthesis={mockSynthesis} tips={tips} />);

    expect(screen.getByText('AI 投委会智能综述与多维决策指引')).toBeInTheDocument();
    expect(screen.getByText(/今日全市场微观情绪录得 66 分/)).toBeInTheDocument();
    expect(screen.getByText(/组合实现窗口超额收益 \+2.60%/)).toBeInTheDocument();
    expect(screen.getByText(/核心 Alpha 因子健康度评级为 healthy/)).toBeInTheDocument();
    expect(screen.getByText(/建议次日调仓采用 VWAP 算法执行/)).toBeInTheDocument();
    expect(screen.getByText(/关注大盘微盘股风格切换风险/)).toBeInTheDocument();
  });

  it('MarketCompassCard 渲染 8 分档截面涨跌幅直方图', () => {
    render(<MarketCompassCard compass={mockCompass} />);

    expect(screen.getByText('截面涨跌幅分布直方图（8 分位分箱统计）')).toBeInTheDocument();
    expect(screen.getByText('<-7%')).toBeInTheDocument();
    expect(screen.getByText('18')).toBeInTheDocument();
    expect(screen.getByText('>=+7%')).toBeInTheDocument();
    expect(screen.getByText('308')).toBeInTheDocument();
  });

  it('SignalPreviewCard 一键同步至交易台：将调仓母单写入 sessionStorage', () => {
    const onNav = vi.fn();
    render(<SignalPreviewCard preview={mockPreview} onNavigate={onNav} />);

    const syncBtn = screen.getByRole('button', { name: /一键同步至交易台/ });
    expect(syncBtn).toBeInTheDocument();
    expect(syncBtn).not.toBeDisabled();

    fireEvent.click(syncBtn);

    // 验证写入 sessionStorage('aqp:desk:batch_import')
    const raw = sessionStorage.getItem('aqp:desk:batch_import');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.source).toContain('AI 日报次日调仓推演');
    expect(parsed.orders).toHaveLength(2);
    expect(parsed.orders[0]).toMatchObject({
      symbol: '000001.SZ',
      side: 'buy',
      algo: 'vwap',
      target_weight: 5.5,
    });
    expect(parsed.orders[1]).toMatchObject({
      symbol: '600519.SH',
      side: 'sell',
      algo: 'vwap',
      target_weight: 0,
    });

    expect(onNav).toHaveBeenCalledWith('/desk?from=report');
  });

  it('Report 页面全景加载、Tab 切换、Markdown 复制与广播推送', async () => {
    const mockReport: DailyReport = {
      date: '2026-10-08',
      generated_at: '2026-10-08T17:30:00',
      sections: [
        {
          title: '一、数据面',
          lines: ['- 行情最新日期：2026-10-08'],
        },
      ],
      markdown: '# AI 日报 2026-10-08\n\n## 一、数据面\n- 行情最新日期：2026-10-08',
      tips: ['因子处于健康范围'],
      market_compass: mockCompass,
      brinson_summary: mockBrinson,
      brinson_sectors: [],
      style_factors: mockStyles,
      portfolio_risk: mockRisk,
      signal_preview: mockPreview,
      synthesis: mockSynthesis,
    };

    h.report.mockResolvedValueOnce({
      report: mockReport,
      history: ['2026-10-08'],
    });
    h.dispatchReport.mockResolvedValueOnce({
      ok: true,
      date: '2026-10-08',
      dispatched: true,
      message: '研报已成功广播推送',
    });

    // 模拟 clipboard
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: { writeText: writeTextMock },
    });

    render(<Report />);

    await waitFor(() => {
      expect(screen.getByText('AI 日报')).toBeInTheDocument();
    });

    // 1. 验证全景总览 Tab 渲染各部分
    await waitFor(() => {
      expect(screen.getByText('AI 投委会智能综述与多维决策指引')).toBeInTheDocument();
      expect(screen.getByText('全市场微观流动性与情绪罗盘')).toBeInTheDocument();
      expect(screen.getByText('Barra 风格因子每日全景与行业轮动透视')).toBeInTheDocument();
      expect(screen.getByText('Brinson 绩效归因（行业配置 vs 个股选择）')).toBeInTheDocument();
      expect(screen.getByText('组合在险价值 (VaR) 与风控雷达监控')).toBeInTheDocument();
      expect(screen.getByText('次日调仓推演与换手冲击预估（多因子信号预演）')).toBeInTheDocument();
    });

    // 2. 复制 Markdown
    const copyBtn = screen.getByRole('button', { name: /复制 Markdown/ });
    fireEvent.click(copyBtn);
    expect(writeTextMock).toHaveBeenCalledWith(mockReport.markdown);

    // 3. 广播推送
    const dispatchBtn = screen.getByRole('button', { name: /广播推送/ });
    fireEvent.click(dispatchBtn);
    await waitFor(() => {
      expect(h.dispatchReport).toHaveBeenCalled();
    });

    // 4. Tab 切换测试：切换至「情绪与风格」
    const compassTab = screen.getByRole('button', { name: '情绪与风格' });
    fireEvent.click(compassTab);
    expect(screen.getByText('全市场微观流动性与情绪罗盘')).toBeInTheDocument();
    expect(screen.getByText('Barra 风格因子每日全景与行业轮动透视')).toBeInTheDocument();
    expect(screen.queryByText('次日调仓推演与换手冲击预估（多因子信号预演）')).not.toBeInTheDocument();

    // 5. 切换至「次日调仓执行」
    const rebalanceTab = screen.getByRole('button', { name: '次日调仓执行' });
    fireEvent.click(rebalanceTab);
    expect(screen.getByText('组合在险价值 (VaR) 与风控雷达监控')).toBeInTheDocument();
    expect(screen.getByText('次日调仓推演与换手冲击预估（多因子信号预演）')).toBeInTheDocument();
  });
});
