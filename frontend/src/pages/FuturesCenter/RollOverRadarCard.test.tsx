/**
 * 期货中心 · 品种移仓换月监控与理论无套利定价雷达卡片单元测试。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import RollOverRadarCard from './RollOverRadarCard';
import { futuresApi } from '@/api/futures';

// Mock futuresApi
vi.mock('@/api/futures', () => ({
  futuresApi: {
    termStructure: vi.fn(),
  },
}));

// Mock echarts to avoid canvas error in jsdom
vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}));

describe('RollOverRadarCard 移仓换月时机与理论无套利定价组件', () => {
  const mockTermStructureData = {
    symbol_prefix: 'IF',
    name: '沪深300股指期货',
    spot_price: 3960.0,
    structure_type: 'Backwardation (反向/贴水)',
    contracts: [
      {
        label: '当月合约',
        contract_code: 'IF2610',
        delivery_date: '2026-10-16',
        days_to_expiry: 6,
        price: 3950.0,
        spot_price: 3960.0,
        basis: 10.0,
        basis_rate: 0.25,
        annualized_basis_rate: 15.2,
        open_interest: 120000,
        volume: 85000,
        oi_share_pct: 58.5,
      },
      {
        label: '下月合约',
        contract_code: 'IF2611',
        delivery_date: '2026-11-20',
        days_to_expiry: 41,
        price: 3942.0,
        spot_price: 3960.0,
        basis: 18.0,
        basis_rate: 0.45,
        annualized_basis_rate: 4.0,
        open_interest: 55000,
        volume: 25000,
        oi_share_pct: 26.8,
      },
    ],
    rollover_stats: {
      near_code: 'IF2610',
      next_code: 'IF2611',
      days_to_expiry: 6,
      delivery_date: '2026-10-16',
      progress_pct: 31.4,
      stage: '移仓启动期',
      stage_desc: '次月合约沉淀资金快速上升，换月窗口开启，可分批顺延主力合约',
      status_tag: 'brand' as const,
      near_oi: 120000,
      next_oi: 55000,
      total_oi: 205000,
      capital_commitment_yi: 72.8,
    },
    cost_of_carry: {
      spot_price: 3960.0,
      dte: 6,
      interest_rate_pct: 2.3,
      dividend_yield_pct: 2.1,
      net_carry_rate_pct: 0.2,
      fair_price: 3960.13,
      fair_basis: -0.13,
      friction_val: 7.92,
      upper_band: 3968.05,
      lower_band: 3952.21,
      near_price: 3950.0,
      mispricing: -10.13,
      arbitrage_status: 'reverse_cash_and_carry' as const,
      arbitrage_label: '反向期现套利',
      arbitrage_desc: '期货价格低于无套利下轨（贴水过度），做多期货同时融券对冲现货可吃贴水收敛收益',
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    (futuresApi.termStructure as any).mockResolvedValue(mockTermStructureData);
  });

  it('成功加载并渲染移仓进度指标与理论定价核心卡片', async () => {
    render(<RollOverRadarCard symbol="IF" />);

    expect(screen.getByText('移仓换月时机与理论无套利定价')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/移仓启动期/)).toBeInTheDocument();
      expect(screen.getByText('反向期现套利')).toBeInTheDocument();
    });

    // 检查核心 KPI
    expect(screen.getByText('31.4%')).toBeInTheDocument();
    expect(screen.getByText('72.8')).toBeInTheDocument();
    expect(screen.getByText('IF2610 → IF2611')).toBeInTheDocument();
  });

  it('支持在持仓迁移分布与无套利通道视图之间切换', async () => {
    render(<RollOverRadarCard symbol="IF" />);

    await waitFor(() => {
      expect(screen.getByText(/次月合约沉淀资金快速上升/)).toBeInTheDocument();
    });

    // 切换到无套利通道
    const arbTab = screen.getByRole('button', { name: /无套利通道/ });
    fireEvent.click(arbTab);

    // 提示切换为套利策略文案
    expect(screen.getByText(/期货价格低于无套利下轨/)).toBeInTheDocument();
  });
});
