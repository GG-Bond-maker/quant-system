/** 组合回测 API */
import { get, post, type RequestOptions } from './client';
import type {
  AssetSearchItem, PortfolioBacktestRequest, PortfolioBacktestResult,
} from '@/types/portfolio';

const SEARCH_TIMEOUT = 15_000;
const BACKTEST_TIMEOUT = 120_000; // 数据抓取 + 计算可能较慢

export const portfolioApi = {
  search: (q: string, signal?: AbortSignal) =>
    get<AssetSearchItem[]>('/api/v1/portfolio/search', { q }, SEARCH_TIMEOUT, { signal }),

  backtest: (req: PortfolioBacktestRequest, options?: RequestOptions) =>
    post<PortfolioBacktestResult>('/api/v1/portfolio/backtest', req, BACKTEST_TIMEOUT, options),
};
