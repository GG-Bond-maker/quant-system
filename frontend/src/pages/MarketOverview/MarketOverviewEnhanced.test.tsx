/**
 * 市场概览增强组件回归测试：
 *   - KpiCards 支持科创50与创新指数
 *   - StyleFactorsPanel 支持 Barra 风格因子多空收益
 *   - ValuationErpPanel 支持大盘估值与股债平衡 ERP
 *   - SpeculationPanel 支持连板天梯与炸板率
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import KpiCards from './KpiCards';
import StyleFactorsPanel from './StyleFactorsPanel';
import ValuationErpPanel from './ValuationErpPanel';
import SpeculationPanel from './SpeculationPanel';
import type { MarketOverviewData, StyleFactorsBlock, ValuationErpBlock, SpeculationBlock } from '@/types/stock';

describe('MarketOverview Enhanced Panels', () => {
  it('KpiCards 正确渲染 7 大核心指标且上证指数不跨列独占双倍宽度', () => {
    const mockData: Partial<MarketOverviewData> = {
      indices: {
        status: 'ok',
        items: [
          { code: 'sh000001', name: '上证指数', close: 3842.2, pct: 0.31, sparkline: [1, 2, 3] },
          { code: 'sh000300', name: '沪深300', close: 4357.6, pct: 0.29, sparkline: [1, 2, 3] },
          { code: 'sh000688', name: '科创50', close: 1530.0, pct: -2.51, sparkline: [1, 2, 3] },
          { code: 'sz399006', name: '创业板指', close: 3135.3, pct: -0.23, sparkline: [1, 2, 3] },
        ],
      },
      heat: { status: 'ok', total_amount_yi: 18500 },
      money_flow: { status: 'ok', main_net_today: -216 },
      ai_stats: { status: 'ok', horizon: 5, rank_ic: -0.126, top_k: 20, top_k_precision: 0.625 },
    };

    const { container } = render(<KpiCards data={mockData as MarketOverviewData} loading={false} />);
    expect(screen.getByText('上证指数 (SSEC)')).toBeInTheDocument();
    expect(screen.getByText('沪深300 (CSI300)')).toBeInTheDocument();
    expect(screen.getByText('科创50 (STAR50)')).toBeInTheDocument();
    expect(screen.getByText('创业板指 (创新)')).toBeInTheDocument();
    expect(screen.getByText('今日两市成交额')).toBeInTheDocument();
    expect(screen.getByText('资金流向')).toBeInTheDocument();
    expect(screen.getByText('AI RankIC（5日）')).toBeInTheDocument();
    expect(screen.getByText('-0.126')).toBeInTheDocument();

    // 栅格必须支持 xl:grid-cols-7 保证 7 项单行排满，不挤压 AI RankIC
    const grid = container.firstChild as HTMLElement;
    expect(grid.className).toContain('xl:grid-cols-7');

    // 上证指数卡片必须不再跨 2 列 (消除 col-span-2)
    const heroCard = screen.getByText('上证指数 (SSEC)').closest('div.relative');
    expect(heroCard).not.toBeNull();
    expect(heroCard?.className).not.toContain('col-span-2');
  });

  it('StyleFactorsPanel 正确渲染 Barra 风格因子多空收益', () => {
    const mockFactors: StyleFactorsBlock = {
      status: 'ok',
      stat_date: '2026-09-30',
      sample_size: 5120,
      items: [
        { code: 'SIZE', name: '规模 (Size)', ls_return: -0.68, direction: 'short', desc: '大盘 vs 小盘' },
        { code: 'MOM', name: '动量 (Momentum)', ls_return: 1.37, direction: 'long', desc: '高动量 vs 弱势' },
        { code: 'VOL', name: '低波 (Volatility)', ls_return: 1.3, direction: 'long', desc: '低波 vs 高波' },
      ],
    };

    render(<StyleFactorsPanel data={mockFactors} loading={false} />);
    expect(screen.getByText('Barra 风格因子收益')).toBeInTheDocument();
    expect(screen.getByText('规模 (Size)')).toBeInTheDocument();
    expect(screen.getByText('动量 (Momentum)')).toBeInTheDocument();
    expect(screen.getByText('+1.37%')).toBeInTheDocument();
  });

  it('ValuationErpPanel 正确渲染宽基估值与股债平衡', () => {
    const mockValuation: ValuationErpBlock = {
      status: 'ok',
      csi300: { pe_ttm: 12.8, percentile: 28.5, date: '2026-09-30' },
      erp: { val: 5.66, bond_yield: 2.15, zone: '机会区间 (低估高赔率)' },
      indices: [
        { code: '000300', name: '沪深300', pe: 12.8, percentile: 28.5, zone: '低估' },
        { code: '000905', name: '中证500', pe: 24.2, percentile: 22.0, zone: '低估' },
        { code: '000688', name: '科创50', pe: 45.6, percentile: 42.0, zone: '合理' },
      ],
    };

    render(<ValuationErpPanel data={mockValuation} loading={false} />);
    expect(screen.getByText('大盘估值与股债平衡 (ERP)')).toBeInTheDocument();
    expect(screen.getByText('5.66%')).toBeInTheDocument();
    expect(screen.getByText('机会区间 (低估高赔率)')).toBeInTheDocument();
    expect(screen.getByText('科创50')).toBeInTheDocument();
  });

  it('SpeculationPanel 正确渲染连板天梯与炸板率', () => {
    const mockSpec: SpeculationBlock = {
      status: 'ok',
      stat_date: '2026-09-30',
      limit_up_count: 70,
      broken_board_count: 39,
      broken_board_rate: 35.8,
      yesterday_zt_premium: 0.93,
      ladder: { first_board: 58, second_board: 8, high_board: 4 },
    };

    render(<SpeculationPanel speculation={mockSpec} loading={false} />);
    expect(screen.getByText('微观投机生态与流动性')).toBeInTheDocument();
    expect(screen.getByText('35.8%')).toBeInTheDocument();
    expect(screen.getByText('+0.93%')).toBeInTheDocument();
    expect(screen.getByText('70')).toBeInTheDocument();
  });
});
