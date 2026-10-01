/** 个股 API 分组。 */
import { get } from './client';
import type {
  AdjustMode,
  KLineResult,
  MLPredictResult,
  SearchHit,
  StockPanels,
  StockProfile,
} from '@/types/stock';

/** 面板聚合接口需并发访问多个外部数据源，超时放宽到 30s */
const PANELS_TIMEOUT = 30_000;

export const stockApi = {
  search: (q: string, limit = 20) =>
    get<SearchHit[]>('/api/v1/stock/search', { q, limit }),

  profile: (symbol: string) =>
    get<StockProfile>(`/api/v1/stock/${encodeURIComponent(symbol)}/profile`),

  kline: (symbol: string, adjust: AdjustMode, start: string, end: string) =>
    get<KLineResult>(`/api/v1/stock/${encodeURIComponent(symbol)}/kline`, {
      adjust,
      start,
      end,
      indicators: true,
    }),

  predict: (symbol: string) =>
    get<MLPredictResult>(`/api/v1/stock/${encodeURIComponent(symbol)}/predict`),

  /** 详情面板聚合（资金流向 / 近期事件 / 筹码 / 风险 / 股东），分块独立降级。
   *
   * ``eventLimit``：近期事件条数（后端按 pub_date 倒序取最近 N 条、不限时间窗口）。
   * 前端「滚动加载更多」通过放大该值触发增量请求；默认 15，后端上限 200。
   */
  panels: (symbol: string, eventLimit = 15) =>
    get<StockPanels>(
      `/api/v1/stock/${encodeURIComponent(symbol)}/panels`,
      { event_limit: eventLimit },
      PANELS_TIMEOUT,
    ),
};
