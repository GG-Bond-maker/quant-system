/** ETF 中心 API 分组。 */
import { get } from './client';
import type {
  EtfDetail, EtfFlowItem, EtfItem, EtfListResult, EtfOverview,
  EtfPerformance, EtfScale,
} from '@/types/etf';

/** 列表接口会带上四国全量目录，超时放宽到 30s */
const ETF_TIMEOUT = 30_000;

export const etfApi = {
  overview: () => get<EtfOverview>('/api/v1/etf/overview', undefined, ETF_TIMEOUT),

  list: (params: {
    country?: string;
    board?: string;
    etype?: string;
    index?: string;
    manager?: string;
    q?: string;
    min_size?: number;
    max_size?: number;
    inception_from?: string;
    inception_to?: string;
    sort?: string;
    page?: number;
    page_size?: number;
  }) => get<EtfListResult>('/api/v1/etf/list', params as Record<string, unknown>, ETF_TIMEOUT),

  hot: (limit = 5) => get<{ items: EtfItem[] }>(
    '/api/v1/etf/hot', { limit }, ETF_TIMEOUT),

  performance: (symbols: string, metric = 'pct', period = '1y') =>
    get<EtfPerformance>('/api/v1/etf/performance',
      { symbols, metric, period }, ETF_TIMEOUT),

  scale: (period = '1m', topN = 10) =>
    get<EtfScale>('/api/v1/etf/scale', { period, top_n: topN }, ETF_TIMEOUT),

  flow: (period = '1d', limit = 10) =>
    get<{ period: string; items: EtfFlowItem[] }>(
      '/api/v1/etf/flow', { period, limit }, ETF_TIMEOUT),

  detail: (code: string, klinePeriod: 'day' | 'week' | 'month' = 'day') =>
    get<EtfDetail>(`/api/v1/etf/detail/${code}`, { kline_period: klinePeriod }, ETF_TIMEOUT),
};
