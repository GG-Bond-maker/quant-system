/** 我的收藏 API 分组（多资产行情 + 量化联动）。 */

import { TIMEOUT } from './timeouts';
import { get, post } from './client';
import type { CorrResult, WatchDashboard, WatchlistRiskResponse } from '@/types/watchlist';

export const watchlistApi = {
  /** 看板：KPI 汇总 + 逐标的行情（K线状态/预警/迷你K线）。 */
  dashboard: (symbols: string[]) =>
    get<WatchDashboard>('/api/v1/watchlist/dashboard',
      { symbols: symbols.join(',') }, TIMEOUT.aggregate),

  /** 最近 N 交易日收益率相关系数矩阵。 */
  correlation: (symbols: string[], days = 60) =>
    get<CorrResult>('/api/v1/watchlist/correlation',
      { symbols: symbols.join(','), days }, TIMEOUT.aggregate),

  /** 自选池组合即时风险透视：年化波动率、基准 Beta、分散化倍数与行业分布。 */
  riskSummary: (symbols: string[], window_days = 120) =>
    post<WatchlistRiskResponse>('/api/v1/watchlist/risk-summary',
      { symbols, window_days }, TIMEOUT.aggregate),
};
