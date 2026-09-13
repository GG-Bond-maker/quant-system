/** 通知中心 API（P2-15）：SSE 流 + 最近事件降级。 */
import { get } from './client';

export interface NotifyEvent {
  /** 事件类别：sync=数据同步 / mining=因子挖掘 / auto_sync=自动同步 … */
  kind: string;
  message: string;
  /** HH:MM:SS（服务端本地时间） */
  ts: string;
}

export const notifyApi = {
  /** 最近事件（建立 SSE 前先拉一次，覆盖页面刷新期间错过的事件） */
  recent: () => get<NotifyEvent[]>('/api/v1/notify/recent', undefined, 10_000),
};
