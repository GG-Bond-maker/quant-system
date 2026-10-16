/**
 * 期货中心与期货分析 API 客户端。
 */
import { get, post } from './client';
import { TIMEOUT } from './timeouts';
import type {
  BasisHistoryResult,
  CalendarSpreadResult,
  CotResult,
  FuturesBacktestRequest,
  FuturesBacktestResult,
  FuturesInstrumentProfile,
  FuturesKlineBar,
  FuturesMarketSnapshot,
  HedgeCalcResult,
  InterCommoditySpreadResult,
  ParameterSensitivityResult,
  RollYieldMatrixResult,
  TermStructureResult,
} from '@/types/futures';

export const futuresApi = {
  /** 获取全市场主力合约看板 */
  contracts(category?: string) {
    const q = category && category !== 'all' ? `?category=${encodeURIComponent(category)}` : '';
    return get<FuturesMarketSnapshot>(`/api/v1/futures/contracts${q}`);
  },

  /** 获取基差与升贴水率看板 */
  basis(category: string = 'index') {
    return get<FuturesMarketSnapshot>(`/api/v1/futures/basis?category=${encodeURIComponent(category)}`);
  },

  /** 获取指定品种的期限结构 */
  termStructure(symbol: string = 'IF') {
    return get<TermStructureResult>(`/api/v1/futures/term-structure?symbol=${encodeURIComponent(symbol)}`);
  },

  /** 获取跨期套利价差与滚动 Z-Score (近月 vs 远月) */
  calendarSpread(symbol: string = 'IF', window: number = 20) {
    return get<CalendarSpreadResult>(`/api/v1/futures/calendar-spread?symbol=${encodeURIComponent(symbol)}&window=${window}`);
  },

  /** 获取主力合约历史日 K 线或复权连续合约 */
  kline(symbol: string = 'IF', days: number = 180, continuous: boolean = false) {
    return get<FuturesKlineBar[]>(`/api/v1/futures/kline?symbol=${encodeURIComponent(symbol)}&days=${days}&continuous=${continuous}`);
  },

  /** 运行期货 CTA 双向交易/做空/对冲回测 */
  backtest(payload: FuturesBacktestRequest) {
    return post<FuturesBacktestResult>('/api/v1/futures/backtest', payload, TIMEOUT.heavy);
  },

  /** 股票组合 Beta 套期保值与保证金测算 */
  hedgeCalc(portfolioValue: number, futuresPrice: number, symbol: string = 'IF', beta: number = 1.0) {
    const q = new URLSearchParams({
      portfolio_value: String(portfolioValue),
      futures_price: String(futuresPrice),
      symbol,
      beta: String(beta),
    });
    return get<HedgeCalcResult>(`/api/v1/futures/hedge-calc?${q.toString()}`);
  },

  /** 获取主力席位持仓龙虎榜 (前20会员多空持仓及净头寸) */
  cot(symbol: string = 'IF') {
    return get<CotResult>(`/api/v1/futures/cot?symbol=${encodeURIComponent(symbol)}`);
  },

  /** 获取产业链与跨品种比价套利时序 */
  interCommodity(pair: string = 'AU_AG') {
    return get<InterCommoditySpreadResult>(`/api/v1/futures/inter-commodity?pair=${encodeURIComponent(pair)}`);
  },

  /** 获取全市场年化展期收益率矩阵与升贴水天梯榜 */
  rollYieldMatrix() {
    return get<RollYieldMatrixResult>('/api/v1/futures/roll-yield-matrix');
  },

  /** 获取指定品种历史基差走势与分位数雷达 */
  basisHistory(symbol: string = 'IF', days: number = 180) {
    return get<BasisHistoryResult>(`/api/v1/futures/basis-history?symbol=${encodeURIComponent(symbol)}&days=${days}`);
  },

  /** 获取单个期货品种的微观画像、基础要素规范与量价+持仓量分析数据 */
  instrumentProfile(symbol: string = 'IF', days: number = 250, continuous: boolean = true) {
    return get<FuturesInstrumentProfile>(`/api/v1/futures/instrument-profile?symbol=${encodeURIComponent(symbol)}&days=${days}&continuous=${continuous}`);
  },

  /** 获取快慢均线参数敏感性网格寻优二维热力图数据 */
  paramSensitivity(symbol: string = 'IF', strategyType: string = 'dual_ma') {
    return get<ParameterSensitivityResult>(`/api/v1/futures/param-sensitivity?symbol=${encodeURIComponent(symbol)}&strategy_type=${encodeURIComponent(strategyType)}`);
  },
};


