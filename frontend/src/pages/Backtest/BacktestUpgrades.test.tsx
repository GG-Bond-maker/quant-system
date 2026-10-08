/**
 * 策略回测 P0、P1、P2 升级组件单测回归：
 * 覆盖 Calmar Ratio、Active Alpha 模式切换、2D 参数敏感性热力图、多资产盈亏贡献度分解与持仓导出闭环。
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { KpiCards, NavChart } from './resultParts';
import ParamHeatmapModal from './ParamHeatmapModal';
import AssetAttributionCard from './AssetAttributionCard';
import type { StrategyKpi, StrategyNavPoint } from '@/api/strategyBacktest';

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('Backtest Upgrades - KpiCards with Calmar Ratio', () => {
  const dummyNav: StrategyNavPoint[] = [
    { date: '2025-01-01', strategy: 1.0, benchmark: 1.0 },
    { date: '2025-01-02', strategy: 1.05, benchmark: 1.01 },
  ];

  it('展示包含卡玛比率的 5 张核心 KPI 卡片', () => {
    const kpi: StrategyKpi = {
      annual_strategy: 0.25,
      annual_benchmark: 0.05,
      sharpe: 1.85,
      max_drawdown: 0.10,
      calmar: 2.50,
    };

    render(
      <MemoryRouter>
        <KpiCards kpi={kpi} nav={dummyNav} loading={false} />
      </MemoryRouter>
    );

    expect(screen.getByText('策略年化收益')).toBeDefined();
    expect(screen.getByText('基准年化收益')).toBeDefined();
    expect(screen.getByText('夏普比率')).toBeDefined();
    expect(screen.getByText('卡玛比率')).toBeDefined();
    expect(screen.getByText('最大回撤')).toBeDefined();

    expect(screen.getByText('25.0%')).toBeDefined();
    expect(screen.getByText('1.85')).toBeDefined();
    expect(screen.getByText('2.50')).toBeDefined();
    expect(screen.getByText('-10.0%')).toBeDefined();
  });

  it('当卡玛比率为 null 时安全降级展示破折号', () => {
    const kpi: StrategyKpi = {
      annual_strategy: 0.15,
      annual_benchmark: null,
      sharpe: null,
      max_drawdown: null,
      calmar: null,
    };

    render(
      <MemoryRouter>
        <KpiCards kpi={kpi} nav={dummyNav} loading={false} />
      </MemoryRouter>
    );

    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });
});

describe('Backtest Upgrades - NavChart Active Alpha Mode', () => {
  const navData: StrategyNavPoint[] = [
    { date: '2025-01-01', strategy: 1.0, benchmark: 1.0 },
    { date: '2025-01-02', strategy: 1.1, benchmark: 1.02 },
    { date: '2025-01-03', strategy: 1.15, benchmark: 1.03 },
  ];

  it('支持在绝对净值与累计超额 Alpha 视图间平滑切换', () => {
    render(
      <MemoryRouter>
        <NavChart nav={navData} signals={[]} loading={false} />
      </MemoryRouter>
    );

    expect(screen.getByText('策略与基准')).toBeDefined();
    expect(screen.getByText('超额 Alpha')).toBeDefined();
    expect(screen.getByText('标记点为 MA 交叉成交')).toBeDefined();

    // 切换至超额 Alpha 模式
    fireEvent.click(screen.getByText('超额 Alpha'));
    expect(screen.getByText('对标基准的相对累计超额 Alpha')).toBeDefined();

    // 切回策略与基准
    fireEvent.click(screen.getByText('策略与基准'));
    expect(screen.getByText('标记点为 MA 交叉成交')).toBeDefined();
  });
});

describe('Backtest Upgrades - ParamHeatmapModal', () => {
  const trials = [
    { params: { short_ma: 5, long_ma: 20 }, objective: 1.8 },
    { params: { short_ma: 5, long_ma: 30 }, objective: 1.5 },
    { params: { short_ma: 10, long_ma: 20 }, objective: 1.2 },
    { params: { short_ma: 10, long_ma: 30 }, objective: 1.6 },
  ];

  it('渲染 2D 参数热力图、计算敏感度并支持点击关闭', () => {
    const handleClose = vi.fn();
    render(
      <ParamHeatmapModal
        isOpen={true}
        onClose={handleClose}
        trials={trials}
        bestParams={{ short_ma: 5, long_ma: 20 }}
        method="grid"
      />
    );

    expect(screen.getByText('参数敏感性 2D 热力分析')).toBeDefined();
    expect(screen.getByText(/GRID 试验采样/)).toBeDefined();
    expect(screen.getByText('最优目标夏普 (Max)')).toBeDefined();
    expect(screen.getByText('全域均值夏普 (Mean)')).toBeDefined();
    expect(screen.getByText('量化投研知识库：参数高原 vs 孤峰识别')).toBeDefined();

    fireEvent.click(screen.getByRole('button', { name: '关闭分析' }));
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('当 isOpen 为 false 时不渲染任何 DOM', () => {
    const { container } = render(
      <ParamHeatmapModal
        isOpen={false}
        onClose={() => {}}
        trials={trials}
      />
    );
    expect(container.firstChild).toBeNull();
  });
});

describe('Backtest Upgrades - AssetAttributionCard', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  const assetPnl = {
    '000001.SZ': 35000,
    '300750.SZ': -12000,
    '600519.SH': 58000,
  };

  it('计算累计 PnL、胜亏标的并支持一键导出 Universe 至 localStorage', () => {
    const onExport = vi.fn();
    render(
      <AssetAttributionCard
        assetPnl={assetPnl}
        loading={false}
        onExportUniverse={onExport}
      />
    );

    expect(screen.getByText('多资产 PnL 盈亏贡献度分解')).toBeDefined();
    expect(screen.getByText('累计总 PnL')).toBeDefined();
    // 总盈亏 35000 - 12000 + 58000 = 81000 元 = 8.1 万元
    expect(screen.getByText('+8.1 万元')).toBeDefined();
    expect(screen.getByText('标的 PnL 净盈亏排行 (元)')).toBeDefined();

    // 点击一键导出标的
    const exportBtn = screen.getByText('🎯 导出标的至组合优化');
    fireEvent.click(exportBtn);

    expect(onExport).toHaveBeenCalledTimes(1);
    const stored = localStorage.getItem('aqp:optimizer_imported_universe');
    expect(stored).not.toBeNull();
    const parsed = JSON.parse(stored!);
    expect(parsed).toEqual(['600519.SH', '000001.SZ', '300750.SZ']);
    expect(screen.getByText(/已成功导出 3 只标的至组合优化工作台/)).toBeDefined();
  });

  it('在无多资产数据时展示友好提示', () => {
    render(
      <AssetAttributionCard
        assetPnl={{}}
        loading={false}
      />
    );

    expect(screen.getByText('单资产或暂无资产盈亏归因数据（回测多标的策略后展示）')).toBeDefined();
  });
});
