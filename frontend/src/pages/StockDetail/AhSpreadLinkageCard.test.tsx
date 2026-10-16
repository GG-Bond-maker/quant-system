import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { AhSpreadBlock } from '@/types/stock';
import { AhSpreadLinkageCard } from './AhSpreadLinkageCard';

describe('AhSpreadLinkageCard 组件', () => {
  it('正确渲染 AH 双重上市标的的溢价率、汇率与套利信号', () => {
    const dualBlock: AhSpreadBlock = {
      status: 'ok',
      is_ah_dual: true,
      current_market: 'A',
      pair_name: '中国平安',
      industry: '保险',
      a_symbol: '601318.SH',
      h_symbol: '02318.HK',
      a_price: 50.5,
      h_price: 48.2,
      h_price_cny: 44.59,
      hkd_cny_fx: 0.925,
      ah_premium_rate: 13.26,
      ha_discount_rate: -11.71,
      ah_ratio: 1.133,
      arbitrage_signal: 'ah_near_parity',
      signal_desc: 'AH接近平价(+13.26%)，价差处于正常估值中枢区间',
      counterpart_symbol: '02318.HK',
    };

    render(
      <BrowserRouter>
        <AhSpreadLinkageCard block={dualBlock} currentSymbol="601318.SH" />
      </BrowserRouter>,
    );

    expect(screen.getByText('AH 联动与港股通套利')).toBeInTheDocument();
    expect(screen.getByText('AH 双重上市 · 联动套利')).toBeInTheDocument();
    expect(screen.getByText('中国平安')).toBeInTheDocument();
    expect(screen.getByText('￥50.50')).toBeInTheDocument();
    expect(screen.getByText('HK$48.20')).toBeInTheDocument();
    expect(screen.getByText('￥44.59')).toBeInTheDocument();
    expect(screen.getByText('+13.26%')).toBeInTheDocument();
    expect(screen.getByText('-11.71%')).toBeInTheDocument();
    expect(screen.getByText('1.133x')).toBeInTheDocument();
    expect(screen.getByText('切换至 02318.HK >')).toBeInTheDocument();
  });

  it('对纯 A 股标的诚实展示互联互通标的提示与全市场 AH 溢价参考，不虚构 H 股对标', () => {
    const singleBlock: AhSpreadBlock = {
      status: 'ok',
      is_ah_dual: false,
      current_market: 'A',
      market_category: 'A股主板 · 互联互通标的',
      pair_name: null,
      arbitrage_signal: 'unapplicable',
      signal_desc: '本标的为纯 A 股，无直接 H 股镜像标的',
      counterpart_symbol: null,
    };

    render(
      <BrowserRouter>
        <AhSpreadLinkageCard block={singleBlock} currentSymbol="600519.SH" />
      </BrowserRouter>,
    );

    expect(screen.getByText('纯 A 股上市公司')).toBeInTheDocument();
    expect(screen.getByText('无直接 H 股镜像')).toBeInTheDocument();
    expect(screen.getByText(/全市场 AH 溢价指数参考/)).toBeInTheDocument();
    expect(screen.getByText('~ 138.5 点')).toBeInTheDocument();
  });

  it('在降级不可用状态下安全回退展示 PanelEmpty，绝不抛错崩溃', () => {
    const unavailBlock: AhSpreadBlock = {
      status: 'unavailable',
      reason: '数据源暂时不可用',
    };

    render(
      <BrowserRouter>
        <AhSpreadLinkageCard block={unavailBlock} currentSymbol="600519.SH" />
      </BrowserRouter>,
    );

    expect(screen.getByText('AH 联动数据暂时不可用')).toBeInTheDocument();
  });
});
