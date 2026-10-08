import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import DsrAnalysisCard from './DsrAnalysisCard';
import CostSensitivityCard from './CostSensitivityCard';
import ModelComparisonModal, { type ExperimentItem } from './ModelComparisonModal';
import ResearchPage from './index';
import { researchApi } from '@/api/research';

// 模拟图表钩子
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

// 模拟策略研究 API
vi.mock('@/api/research', () => ({
  researchApi: {
    overview: vi.fn(),
    experiments: vi.fn(),
    featureImportance: vi.fn(),
    labYearly: vi.fn(),
    factorIcir: vi.fn(),
    factorCorr: vi.fn(),
    factorQuantile: vi.fn(),
    cvFolds: vi.fn(),
    cpcv: vi.fn(),
    optimize: vi.fn(),
    impactSim: vi.fn(),
    stressTest: vi.fn(),
  },
}));

describe('DsrAnalysisCard 缩水夏普与多重检验抗过拟合组件', () => {
  it('正确渲染 DSR 理论卡片并展现各项指标', () => {
    render(
      <DsrAnalysisCard
        initialSharpe={1.8}
        initialObsDays={500}
        initialSkewness={-0.5}
        initialKurtosis={3.0}
      />
    );

    // 验证核心标题
    expect(screen.getByText('Deflated Sharpe Ratio (DSR) 缩水夏普抗过拟合体检')).toBeInTheDocument();

    // 验证核心指标标签存在
    expect(screen.getByText('观察回测夏普 (Observed SR)')).toBeInTheDocument();
    expect(screen.getByText('虚假最大夏普期望 E[max SR]')).toBeInTheDocument();
    expect(screen.getByText('真实缩水夏普 (Haircut SR)')).toBeInTheDocument();
    expect(screen.getByText('DSR 置信概率 P(SR>0)')).toBeInTheDocument();

    // 验证输入控件存在并能调整参数
    const trialsInput = screen.getByDisplayValue('30');
    expect(trialsInput).toBeInTheDocument();
    fireEvent.change(trialsInput, { target: { value: '100' } });
    expect(screen.getByDisplayValue('100')).toBeInTheDocument();
  });
});

describe('CostSensitivityCard 交易费率与换手率敏感性分析组件', () => {
  it('正确计算并渲染费率边界与保本摩擦上限', () => {
    render(
      <CostSensitivityCard
        initialGrossReturn={22}
        initialTurnover={18}
        initialBenchmarkReturn={4}
      />
    );

    // 验证标题
    expect(screen.getByText('交易费用与换手率敏感性分析 (Cost Frontier)')).toBeInTheDocument();

    // 验证保本指标卡
    expect(screen.getByText('超额保本成本上限 (Alpha Break-even)')).toBeInTheDocument();
    expect(screen.getByText('10bps基准下单边扣费损耗')).toBeInTheDocument();
    expect(screen.getByText('10bps摩擦下真实净超额 (Net Alpha)')).toBeInTheDocument();
    expect(screen.getByText('最大可持续年换手 (10bps成本下)')).toBeInTheDocument();

    // 验证衰减曲线与诊断结论
    expect(screen.getByText('单边摩擦成本爬升下的净收益衰减直线')).toBeInTheDocument();
    expect(screen.getByText('诊断结论')).toBeInTheDocument();

    // 验证修改参数响应
    const turnoverInput = screen.getByDisplayValue('18');
    fireEvent.change(turnoverInput, { target: { value: '25' } });
    expect(screen.getByDisplayValue('25')).toBeInTheDocument();
  });
});

describe('ModelComparisonModal MLOps 多版本 Head-to-Head 对垒弹窗', () => {
  const mockExperiments: ExperimentItem[] = [
    {
      model_name: 'lgb_core',
      version: 'v2.1.0',
      status: 'ready',
      is_production: 1,
      feature_version: 'feat_v3',
      created_at: '2026-10-01',
      train_start: '2020-01-01',
      train_end: '2024-12-31',
      metrics: { valid_rank_ic: 0.082, test_rank_ic: 0.065, icir: 1.85 },
      hyperparams: { learning_rate: 0.03, num_leaves: 63 },
      has_model_file: true,
    },
    {
      model_name: 'lgb_core',
      version: 'v2.0.0',
      status: 'archived',
      is_production: 0,
      feature_version: 'feat_v2',
      created_at: '2026-09-15',
      train_start: '2020-01-01',
      train_end: '2024-12-31',
      metrics: { valid_rank_ic: 0.071, test_rank_ic: 0.048, icir: 1.42 },
      hyperparams: { learning_rate: 0.05, num_leaves: 31 },
      has_model_file: true,
    },
  ];

  it('在 open=false 时不渲染内容', () => {
    const handleClose = vi.fn();
    render(
      <ModelComparisonModal
        open={false}
        onClose={handleClose}
        selectedExperiments={mockExperiments}
      />
    );
    expect(screen.queryByText(/MLOps 模型多版本 Head-to-Head/)).not.toBeInTheDocument();
  });

  it('在 open=true 时展示版本对比信息并支持关闭', () => {
    const handleClose = vi.fn();
    render(
      <ModelComparisonModal
        open={true}
        onClose={handleClose}
        selectedExperiments={mockExperiments}
      />
    );

    // 弹窗标题与描述
    expect(screen.getByText(/MLOps 模型多版本 Head-to-Head/)).toBeInTheDocument();

    // 检查版本标签
    expect(screen.getAllByText('v2.1.0').length).toBeGreaterThan(0);
    expect(screen.getByText('Production')).toBeInTheDocument();
    expect(screen.getAllByText('v2.0.0').length).toBeGreaterThan(0);

    // 检查雷达维度说明
    expect(screen.getByText(/核心能力雷达分布对比/)).toBeInTheDocument();

    // 点击关闭按钮触发回调
    const closeBtn = screen.getByRole('button', { name: '关闭对比' });
    fireEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });
});

describe('ResearchPage 全局架构与双模工作流', () => {
  beforeEach(() => {
    vi.mocked(researchApi.overview).mockResolvedValue({
      factor_count: 56,
      expression_count: 120,
      model_version_count: 8,
      model_version_note: 'model_registry 线上归档',
      capacity_estimate_yi: 15.5,
      capacity_formula: '日均流动性 × 参与率',
      factor_universe: '中证800全流动池',
    });

    vi.mocked(researchApi.experiments).mockResolvedValue([
      {
        model_name: 'lgb_alpha_core',
        version: 'v2.1.0',
        status: 'ready',
        is_production: 1,
        feature_version: 'feat_v3',
        created_at: '2026-10-01',
        train_start: '2020-01-01',
        train_end: '2024-12-31',
        metrics: { valid_rank_ic: 0.082, test_rank_ic: 0.065, icir: 1.85 },
        hyperparams: { learning_rate: 0.03, num_leaves: 63 },
        has_model_file: true,
      },
      {
        model_name: 'lgb_alpha_core',
        version: 'v2.0.0',
        status: 'archived',
        is_production: 0,
        feature_version: 'feat_v2',
        created_at: '2026-09-15',
        train_start: '2020-01-01',
        train_end: '2024-12-31',
        metrics: { valid_rank_ic: 0.071, test_rank_ic: 0.048, icir: 1.42 },
        hyperparams: { learning_rate: 0.05, num_leaves: 31 },
        has_model_file: true,
      },
    ]);

    vi.mocked(researchApi.featureImportance).mockResolvedValue({
      model_version: 'v2.1.0',
      items: [
        { feature: 'momentum_20d', gain: 0.28 },
        { feature: 'volatility_ratio', gain: 0.21 },
      ],
      effect_curves: [],
    });

    vi.mocked(researchApi.labYearly).mockResolvedValue([
      { year: 2024, rank_ic: 0.062, icir: 1.45, hit_rate: 0.58, n_days: 242 },
      { year: 2025, rank_ic: 0.071, icir: 1.68, hit_rate: 0.61, n_days: 242 },
    ]);

    vi.mocked(researchApi.factorIcir).mockResolvedValue({
      horizon: 5,
      neutralize_size: false,
      rows: [
        { factor: 'ret_5', mean_ic: 0.052, icir: 1.41, t_stat: 3.4, positive_ratio: 0.62, ic_series: [0.05, 0.04], n_days: 120 },
      ],
      available_factors: ['ret_5', 'vol_20'],
    });

    vi.mocked(researchApi.factorCorr).mockResolvedValue({
      factors: ['ret_5', 'vol_20'],
      matrix: [[1, 0.2], [0.2, 1]],
      high_corr_pairs: [],
      window_days: 60,
    });

    vi.mocked(researchApi.factorQuantile).mockResolvedValue({
      quantiles: 5,
      horizon: 5,
      curves: {
        Q1: [{ date: '2025-01-01', nav: 1.0 }, { date: '2025-02-01', nav: 0.95 }],
        Q5: [{ date: '2025-01-01', nav: 1.0 }, { date: '2025-02-01', nav: 1.15 }],
      },
      long_short_curve: [{ date: '2025-01-01', nav: 1.0 }, { date: '2025-02-01', nav: 1.20 }],
      annualized_returns: { Q1: -0.05, Q5: 0.15 },
      monotonicity_score: 0.85,
      is_monotonic: true,
      labels: { Q1: '多头组', Q5: '空头组' },
    });

    vi.mocked(researchApi.cvFolds).mockResolvedValue({
      total_days: 500,
      date_start: '2024-01-01',
      date_end: '2025-12-31',
      folds: [
        {
          fold: 1,
          train_start: '2024-01-01',
          train_end: '2024-06-30',
          test_start: '2024-07-01',
          test_end: '2024-09-30',
          train_days: 120,
          test_days: 60,
          gap_days: 5,
          purge_window: 5,
          embargo_window: 2,
        },
      ],
    });

    vi.mocked(researchApi.cpcv).mockResolvedValue({
      has_data: true,
      n_groups: 6,
      k_test: 2,
      n_paths: 15,
      purge_window: 5,
      embargo_window: 2,
      models_evaluated: ['v2.1.0', 'v2.0.0'],
      pbo: 0.12,
      is_robust: true,
      mean_is_ic: 0.082,
      mean_oos_ic: 0.065,
      degradation_pct: 0.207,
      rank_distribution: [{ bin: '0.8-1.0', count: 12 }],
      paths: [],
      summary: '低过拟合风险 (PBO < 20%)',
    });

    vi.mocked(researchApi.optimize).mockResolvedValue({
      method: 'risk_parity',
      symbols: ['000001.SZ', '600519.SH'],
      prev_weights: { '000001.SZ': 0.5, '600519.SH': 0.5 },
      weights: { '000001.SZ': 0.45, '600519.SH': 0.55 },
      exposure_before: {},
      exposure_after: {},
      style_factor_map: {},
      n_obs: 250,
    });

    vi.mocked(researchApi.impactSim).mockResolvedValue({
      symbol: '000001.SZ',
      algo: 'vwap',
      side: 'buy',
      order_amount: 20000000,
      fills: [
        { date: '2025-01-02', ref_price: 10.5, exec_price: 10.52, amount: 4000000, participation: 0.05, impact_bps: 4.2, side: 'buy' },
      ],
      unfilled: 0,
      total_impact_cost: 84000,
      avg_impact_bps: 4.2,
      price_series: [{ date: '2025-01-02', open: 10.4, close: 10.6, vwap: 10.52 }],
    });

    vi.mocked(researchApi.stressTest).mockResolvedValue({
      window: 250,
      scenarios: [
        {
          window_start: '2015-06-15',
          window_end: '2015-08-25',
          market_return: -0.35,
          market_mdd: -0.42,
          var_95: -0.065,
          cvar_95: -0.082,
          n_days: 50,
          portfolio_return: -0.28,
          portfolio_mdd: -0.32,
          portfolio_var_95: -0.051,
        },
      ],
      note: '历史最深回撤窗口',
    });
  });

  it('支持在沉浸式流水线与四象限全景看板之间灵活切换', async () => {
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>
    );

    // 默认展示沉浸式流水线
    expect(screen.getByText('🔬 沉浸式流水线')).toBeInTheDocument();
    expect(screen.getByText('📊 四象限全景看板')).toBeInTheDocument();

    // 沉浸式流水线 Tab 导航存在
    expect(screen.getByText('🔬 因子研发与正交纯化')).toBeInTheDocument();
    expect(screen.getByText('🤖 MLOps 建模与防过拟合检验')).toBeInTheDocument();
    expect(screen.getByText('⚖️ 极致组合优化与风控')).toBeInTheDocument();
    expect(screen.getByText('⚡ 执行冲击与极端压力测试')).toBeInTheDocument();

    // 切换到四象限全景看板
    const quadrantBtn = screen.getByText('📊 四象限全景看板');
    fireEvent.click(quadrantBtn);

    // 四象限均在 DOM 中呈现
    await waitFor(() => {
      expect(screen.getByText(/因子研发中心/)).toBeInTheDocument();
      expect(screen.getByText(/MLOps 建模中心/)).toBeInTheDocument();
      expect(screen.getByText(/极致组合与风控/)).toBeInTheDocument();
      expect(screen.getByText(/策略引擎进阶/)).toBeInTheDocument();
    });

    // 切换回沉浸式流水线
    const workflowBtn = screen.getByText('🔬 沉浸式流水线');
    fireEvent.click(workflowBtn);

    // 验证点击切换 Tab
    const mlTabBtn = screen.getByText('🤖 MLOps 建模与防过拟合检验');
    fireEvent.click(mlTabBtn);
    expect(screen.getByText(/MLOps 建模中心/)).toBeInTheDocument();
  });

  it('支持投研联动导入多头优质标的至组合优化', async () => {
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>
    );

    // 切换到 MLOps 建模 Tab
    const mlTabBtn = screen.getByText('🤖 MLOps 建模与防过拟合检验');
    fireEvent.click(mlTabBtn);

    // 点击「🎯 推荐标的→组合」
    const importBtn = await screen.findByTitle('提取模型推荐的多头优质标的至组合优化控制台');
    fireEvent.click(importBtn);

    // 验证出现投研联动通知
    await waitFor(() => {
      expect(screen.getByText(/投研联动：/)).toBeInTheDocument();
      expect(screen.getByText(/已成功从 MLOps 生产模型提取多头预测股票池/)).toBeInTheDocument();
    });
  });
});
