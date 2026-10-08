import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AlphalensAnalyticsCard from './AlphalensAnalyticsCard';
import QuantileSpreadCard from './QuantileSpreadCard';
import FactorExposureRadarCard from './FactorExposureRadarCard';
import AlphaExplainabilityCard from './AlphaExplainabilityCard';
import type { FactorReportResult } from '@/api/production';

// Mock useChart
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

const mockReport: FactorReportResult = {
  expr: 'Sub(ret_5, Ref(ret_5, 10))',
  horizon: 5,
  mean_ic: 0.0452,
  icir: 0.621,
  t_stat: 3.25,
  n_days: 120,
  ic_series: [
    { date: '2025-01-02', ic: 0.05 },
    { date: '2025-01-03', ic: -0.01 },
    { date: '2025-02-04', ic: 0.08 },
    { date: '2025-02-05', ic: 0.06 },
  ],
  cum_ic_series: [
    { date: '2025-01-02', cum_ic: 0.05 },
    { date: '2025-01-03', cum_ic: 0.04 },
    { date: '2025-02-04', cum_ic: 0.12 },
    { date: '2025-02-05', cum_ic: 0.18 },
  ],
  monthly_ic: {
    '2025-01': 0.02,
    '2025-02': 0.07,
  },
  ic_stats: {
    mean_ic: 0.0452,
    std_ic: 0.0728,
    icir: 0.621,
    t_stat: 3.25,
    positive_ratio: 0.65,
    significant_ratio: 0.35,
    skewness: 0.12,
    kurtosis: -0.05,
    n_days: 120,
  },
  quintile_navs: [
    { q: 'q1', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 0.95 }] },
    { q: 'q2', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 0.98 }] },
    { q: 'q3', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 1.02 }] },
    { q: 'q4', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 1.08 }] },
    { q: 'q5', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 1.15 }] },
  ],
  quintile_annual: {
    q1: -0.08,
    q2: -0.02,
    q3: 0.03,
    q4: 0.11,
    q5: 0.22,
  },
  monotonicity_score: 1.0,
  is_monotonic: true,
  cost_adjusted_navs: [
    { q: 'q1', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 0.92 }] },
    { q: 'q2', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 0.96 }] },
    { q: 'q3', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 1.00 }] },
    { q: 'q4', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 1.05 }] },
    { q: 'q5', nav: [{ date: '2025-01-02', nav: 1.0 }, { date: '2025-02-05', nav: 1.11 }] },
  ],
  long_short_gross_nav: [
    { date: '2025-01-02', nav: 1.0 },
    { date: '2025-02-05', nav: 1.21 },
  ],
  long_short_net_nav: [
    { date: '2025-01-02', nav: 1.0 },
    { date: '2025-02-05', nav: 1.16 },
  ],
  style_exposures: [
    { style: 'Size', name: '市值规模', factor_col: 'amount_per_share', exposure: 0.12 },
    { style: 'Beta/Vol', name: '波动风险', factor_col: 'vol_20', exposure: -0.08 },
    { style: 'Momentum', name: '动量趋势', factor_col: 'ret_20', exposure: 0.32 },
    { style: 'Reversal', name: '短期反转', factor_col: 'rsi_14', exposure: -0.15 },
    { style: 'Liquidity', name: '流动性', factor_col: 'v_rank_20', exposure: 0.05 },
    { style: 'Trend Gap', name: '均线乖离', factor_col: 'ma_gap_20', exposure: 0.18 },
  ],
  decay: {
    '1': { rank_ic: 0.052, icir: 0.71, n_days: 120 },
    '5': { rank_ic: 0.045, icir: 0.62, n_days: 120 },
    '10': { rank_ic: 0.031, icir: 0.44, n_days: 120 },
    '20': { rank_ic: 0.015, icir: 0.21, n_days: 120 },
  },
  top_turnover: 0.185,
};

describe('AlphalensAnalyticsCard', () => {
  it('正确渲染 Alphalens IC 统计指标、累计IC及月度热力日历', () => {
    render(<AlphalensAnalyticsCard report={mockReport} />);

    expect(screen.getByText('Alphalens IC 深度检验看板')).toBeInTheDocument();
    expect(screen.getByText('0.0452')).toBeInTheDocument(); // Mean IC
    expect(screen.getByText('0.621')).toBeInTheDocument(); // ICIR
    expect(screen.getByText('3.25')).toBeInTheDocument(); // t-stat
    expect(screen.getByText('65.0%')).toBeInTheDocument(); // 胜率
    expect(screen.getByText('月度 IC 均值分布热力矩阵 (Year × Month)')).toBeInTheDocument();
    expect(screen.getByText('2025')).toBeInTheDocument();
  });
});

describe('QuantileSpreadCard', () => {
  it('正确渲染毛收益与扣费净收益对比、单调性评级与Q1~Q5年化', () => {
    render(<QuantileSpreadCard report={mockReport} />);

    expect(screen.getByText('分层收益与成本穿透看板')).toBeInTheDocument();
    expect(screen.getByText('1.000')).toBeInTheDocument(); // Monotonicity score
    expect(screen.getByText('卓越单调 (Exceptional)')).toBeInTheDocument();
    expect(screen.getByText('18.5%/日')).toBeInTheDocument(); // 换手率
    expect(screen.getByText('22.0%')).toBeInTheDocument(); // Q5 年化

    // 切换到 Q1~Q5 分组净值
    const qNavBtn = screen.getByRole('button', { name: 'Q1~Q5 分组净值' });
    fireEvent.click(qNavBtn);
    expect(screen.getByText(/Q1~Q5 分组累计净值走势/)).toBeInTheDocument();
  });
});

describe('FactorExposureRadarCard', () => {
  it('正确渲染 Barra 6 大风格暴露与偏离度诊断', () => {
    const onNavigate = vi.fn();
    render(<FactorExposureRadarCard report={mockReport} onNavigateToOrtho={onNavigate} />);

    expect(screen.getByText('Barra 风格暴露雷达与中性化诊断')).toBeInTheDocument();
    expect(screen.getByText('存在 1 项显著偏离')).toBeInTheDocument(); // Momentum = 0.32 >= 0.25
    expect(screen.getAllByText(/动量趋势/)[0]).toBeInTheDocument();

    const orthoBtn = screen.getByRole('button', { name: '去正交纯化残差 →' });
    fireEvent.click(orthoBtn);
    expect(onNavigate).toHaveBeenCalled();
  });
});

describe('AlphaExplainabilityCard', () => {
  it('正确解析 AST 语法树、翻译金融经济学逻辑与三大量化风险审计', () => {
    render(<AlphaExplainabilityCard expression={mockReport.expr} horizon={5} />);

    expect(screen.getByText('Alpha 表达式 AST 语法分解与经济学解释')).toBeInTheDocument();
    expect(screen.getByText('Sub(ret_5, Ref(ret_5, 10))')).toBeInTheDocument();

    // AST 节点存在
    expect(screen.getByText('Sub')).toBeInTheDocument();
    expect(screen.getAllByText('ret_5')[0]).toBeInTheDocument();

    // 金融语义翻译
    expect(screen.getByText('金融经济学逻辑翻译')).toBeInTheDocument();

    // 风险审计
    expect(screen.getByText('前瞻未来偏差 (Look-Ahead Bias)')).toBeInTheDocument();
    expect(screen.getByText('换手交易摩擦风险 (Turnover Friction)')).toBeInTheDocument();
    expect(screen.getByText('多重假设过拟合偏差 (Data-Mining Bias)')).toBeInTheDocument();
  });
});
