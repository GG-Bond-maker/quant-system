/** 组合回测 API */
import { get, post } from './client';
import type {
  AssetSearchItem, PortfolioBacktestRequest, PortfolioBacktestResult,
} from '@/types/portfolio';

const SEARCH_TIMEOUT = 15_000;
const BACKTEST_TIMEOUT = 120_000; // 数据抓取 + 计算可能较慢

export const portfolioApi = {
  search: (q: string) =>
    get<AssetSearchItem[]>('/api/v1/portfolio/search', { q }, SEARCH_TIMEOUT),

  backtest: (req: PortfolioBacktestRequest) =>
    post<PortfolioBacktestResult>('/api/v1/portfolio/backtest', req, BACKTEST_TIMEOUT),
};
