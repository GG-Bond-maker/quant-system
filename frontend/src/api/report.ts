/**
 * AI 投研日报 API 客户端。
 */
import { get, post } from './client';
import { TIMEOUT } from './timeouts';
import type { DailyReportItem, DailyReportResponse, DispatchResponse } from '@/types/report';

export const reportApi = {
  /** 获取最新或指定日期的投研日报 */
  daily(date?: string) {
    const q = date ? `?date=${encodeURIComponent(date)}` : '';
    return get<DailyReportResponse>(`/api/v1/report/daily${q}`, undefined, TIMEOUT.aggregate);
  },

  /** 手动重新生成当日投研日报 (研究员及以上权限) */
  generate() {
    return post<DailyReportItem>('/api/v1/report/daily/generate', {}, TIMEOUT.heavy);
  },

  /** 手动将当前最新日报分发至 Webhook 通道 (钉钉/企微/飞书) */
  dispatch() {
    return post<DispatchResponse>('/api/v1/report/daily/dispatch', {}, TIMEOUT.probe);
  },
};
