/**
 * 通知中心 store（P2-15）：EventSource 单例订阅 + 未读计数。
 *
 * 首个消费者（Topbar 铃铛）挂载时调用 open()（幂等）：
 * 先拉 /notify/recent 补齐错过的事件，再建立 SSE 长连接；
 * 断线由 EventSource 自动重连（25s 心跳保活）。
 */
import { create } from 'zustand';
import { notifyApi, type NotifyEvent } from '@/api/notify';

const MAX_EVENTS = 20;

interface NotifyState {
  events: NotifyEvent[];
  unread: number;
  connected: boolean;
  /** 建立订阅（幂等；模块级单例 EventSource） */
  open: () => void;
  markRead: () => void;
}

let es: EventSource | null = null;

export const useNotifyStore = create<NotifyState>((set) => ({
  events: [],
  unread: 0,
  connected: false,
  open: () => {
    if (es) return;
    // 先回放最近事件，再订阅实时流
    void notifyApi.recent()
      .then((list) => { if (list?.length) set({ events: list.slice(-MAX_EVENTS) }); })
      .catch(() => { /* 降级：仅实时流 */ });
    es = new EventSource('/api/v1/notify/stream');
    es.onopen = () => set({ connected: true });
    es.onerror = () => set({ connected: false }); // EventSource 自动重连
    es.onmessage = (ev) => {
      try {
        const item = JSON.parse(ev.data) as NotifyEvent;
        set((s) => ({
          events: [...s.events, item].slice(-MAX_EVENTS),
          unread: s.unread + 1,
        }));
      } catch { /* 忽略坏帧 */ }
    };
  },
  markRead: () => set({ unread: 0 }),
}));
