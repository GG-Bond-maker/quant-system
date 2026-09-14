/** 选股 API（P1-8）。
 *
 * 历史问题（审计 1.3）：此文件曾导出 backtestApi.run() 指向 /backtest/run，
 * 但全项目 0 处 import —— Backtest 页面实际走 strategyBacktestApi
 * （/backtest/strategy-run），属于「后端有实现、前端有壳、无页面消费」的死代码，
 * 已删除。回测导出请使用 api/export.ts。
 */
import { get } from './client';
import type { ScreenerResult, StockListResult, WatchlistQuotes } from '@/types/p1';

/** 全市场股票列表：冷算要遍历全市场 + 打外部快照，超时放宽到 30s（默认 15s 会误判断线） */
const STOCKS_TIMEOUT = 30_000;

export const screenerApi = {
  screen: (params: { date?: string; strategy?: string; top_k?: number; board?: string },
           refresh = false) =>
    get<ScreenerResult>('/api/v1/screener',
      { ...params, ...(refresh ? { refresh: 1 } : {}) }),

  /**
   * 全市场股票列表（服务端筛选 + 排序 + 分页）。
   *
   * board / industry 默认 'all'；sort 传空串表示后端默认顺序（code 升序）；
   * dir 默认 desc；basis=auto 由后端自行决定实时 / 日终口径并回传 basis_desc。
   */
  stocks: (params: {
    board?: string;
    industry?: string;
    q?: string;
    exclude_st?: number;
    sort?: string;
    dir?: string;
    page?: number;
    page_size?: number;
    basis?: string;
    refresh?: number;
  }) => get<StockListResult>(
    '/api/v1/screener/stocks', params as Record<string, unknown>, STOCKS_TIMEOUT),

  /** 自选股行情快照：symbols 为标的代码数组（后端以逗号拼接） */
  watchlist: (symbols: string[]) =>
    get<WatchlistQuotes>('/api/v1/screener/watchlist', { symbols: symbols.join(',') }),
};
