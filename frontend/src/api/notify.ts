/** 通知中心 API（P2-15）：SSE 流 + 最近事件降级。 */
import { get, post } from './client';

export interface StreamTicket {
  /** 高熵、短期、单次使用的 EventSource 建连票据；绝不写入持久化存储。 */
  ticket: string;
  /** 服务端票据剩余有效期（秒），当前固定为 60。 */
  expires_in: number;
}

export interface NotifyEvent {
  /** 事件类别：sync=数据同步 / mining=因子挖掘 / auto_sync=自动同步 … */
  kind: string;
  message: string;
  /** HH:MM:SS（服务端本地时间） */
  ts: string;
}

function apiBaseUrl(): string {
  const configuredBase = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? '';
  return configuredBase;
}

export const notifyApi = {
  /** 最近事件（建立 SSE 前先拉一次，覆盖页面刷新期间错过的事件） */
  recent: () => get<NotifyEvent[]>('/api/v1/notify/recent', undefined, 10_000),
  /** 用 Axios Bearer 拦截器换取一次性 ticket，供 EventSource 无 header 场景使用。 */
  createStreamTicket: () => post<StreamTicket>('/api/v1/notify/stream-ticket', undefined, 10_000),
  /** 仅拼接短期一次性 ticket；不得向 URL 放入 JWT 或 ADMIN_TOKEN。 */
  streamUrl: (ticket: string): string => (
    `${apiBaseUrl()}/api/v1/notify/stream?ticket=${encodeURIComponent(ticket)}`
  ),
};
