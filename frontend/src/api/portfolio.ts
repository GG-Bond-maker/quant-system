/** 组合回测 API */

import { TIMEOUT } from './timeouts';
import { get, post, type RequestOptions } from './client';
import type {
  AssetSearchItem, PortfolioBacktestRequest, PortfolioBacktestResult,
  EfficientFrontierRequest, EfficientFrontierResult,
} from '@/types/portfolio';

const SEARCH_TIMEOUT = TIMEOUT.default;
const BACKTEST_TIMEOUT = TIMEOUT.heavy; // 数据抓取 + 计算可能较慢

export const portfolioApi = {
  search: (q: string, signal?: AbortSignal) =>
    get<AssetSearchItem[]>('/api/v1/portfolio/search', { q }, SEARCH_TIMEOUT, { signal }),

  backtest: (req: PortfolioBacktestRequest, options?: RequestOptions) =>
    post<PortfolioBacktestResult>('/api/v1/portfolio/backtest', req, BACKTEST_TIMEOUT, options),

  efficientFrontier: (req: EfficientFrontierRequest, options?: RequestOptions) =>
    post<EfficientFrontierResult>('/api/v1/portfolio/efficient-frontier', req, BACKTEST_TIMEOUT, options),
};

