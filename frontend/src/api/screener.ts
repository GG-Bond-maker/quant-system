/** 选股 API（P1-8）。
 *
 * 历史问题（审计 1.3）：此文件曾导出 backtestApi.run() 指向 /backtest/run，
 * 但全项目 0 处 import —— Backtest 页面实际走 strategyBacktestApi
 * （/backtest/strategy-run），属于「后端有实现、前端有壳、无页面消费」的死代码，
 * 已删除。回测导出请使用 api/export.ts。
 */
import { get } from './client';
import type { ScreenerResult, WatchlistQuotes } from '@/types/p1';

export const screenerApi = {
  screen: (params: { date?: string; strategy?: string; top_k?: number; board?: string },
           refresh = false) =>
    get<ScreenerResult>('/api/v1/screener',
      { ...params, ...(refresh ? { refresh: 1 } : {}) }),
  /** 自选股行情快照：symbols 为标的代码数组（后端以逗号拼接） */
  watchlist: (symbols: string[]) =>
    get<WatchlistQuotes>('/api/v1/screener/watchlist', { symbols: symbols.join(',') }),
};
