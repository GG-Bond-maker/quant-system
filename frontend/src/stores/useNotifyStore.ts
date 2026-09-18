/**
 * 通知中心 store：授权 SSE 订阅、最近事件轮询兜底及未读计数。
 *
 * EventSource 无法携带 Authorization header：先经 Axios Bearer 调用换取短期、
 * 单次使用的 SSE ticket，再只把 ticket 放入连接 URL；断线时必须重新换票。
 */
import { create } from 'zustand';
import { notifyApi, type NotifyEvent } from '@/api/notify';
import { useAuthStore } from '@/stores/useAuthStore';

const MAX_EVENTS = 20;
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 1_000;

type NotifyConnectionStatus = 'idle' | 'connecting' | 'connected' | 'unavailable';

interface NotifyState {
  events: NotifyEvent[];
  unread: number;
  connected: boolean;
  status: NotifyConnectionStatus;
  /** 建立订阅；未登录时不发起任何通知请求。 */
  open: () => void;
  /** 主动关闭连接及待执行重试，用于退出登录和组件卸载。 */
  close: () => void;
  /** 拉取最近事件，作为实时推送不可用时的单次轮询兜底。 */
  refresh: () => Promise<void>;
  markRead: () => void;
}

let eventSource: EventSource | null = null;
let retryTimer: number | null = null;
let retryCount = 0;
let shouldConnect = false;
let ticketRequestInFlight = false;
let connectionGeneration = 0;

function mergeRecentEvents(current: NotifyEvent[], recent: NotifyEvent[]): NotifyEvent[] {
  const merged = [...current];
  recent.forEach((item) => {
    const exists = merged.some((event) =>
      event.kind === item.kind && event.message === item.message && event.ts === item.ts);
    if (!exists) merged.push(item);
  });
  return merged.slice(-MAX_EVENTS);
}

function clearRetryTimer(): void {
  if (retryTimer !== null) {
    window.clearTimeout(retryTimer);
    retryTimer = null;
  }
}

function closeEventSource(): void {
  if (eventSource !== null) {
    eventSource.close();
    eventSource = null;
  }
}

export const useNotifyStore = create<NotifyState>((set, get) => {
  const refresh = async (): Promise<void> => {
    if (!useAuthStore.getState().isAuthenticated()) return;
    try {
      const recent = await notifyApi.recent();
      if (recent?.length) {
        set((state) => ({ events: mergeRecentEvents(state.events, recent) }));
      }
    } catch {
      // 最近事件接口不可用时保留已有事件；通知栏会明确显示降级状态。
    }
  };

  const scheduleRetry = (): void => {
    if (!shouldConnect || !useAuthStore.getState().isAuthenticated() || retryCount >= MAX_RETRIES) {
      set({ connected: false, status: 'unavailable' });
      return;
    }

    const delay = RETRY_BASE_DELAY_MS * (2 ** retryCount);
    retryCount += 1;
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      get().open();
    }, delay);
  };

  const connectWithFreshTicket = async (): Promise<void> => {
    if (!shouldConnect || !useAuthStore.getState().isAuthenticated()) return;
    if (eventSource !== null || ticketRequestInFlight) return;

    ticketRequestInFlight = true;
    const generation = connectionGeneration;
    try {
      // 每一个 EventSource 都使用新 ticket，禁止浏览器自动重连复用已消费的票据。
      const { ticket } = await notifyApi.createStreamTicket();
      if (!shouldConnect || generation !== connectionGeneration || !useAuthStore.getState().isAuthenticated()) {
        return;
      }

      const source = new EventSource(notifyApi.streamUrl(ticket));
      eventSource = source;
      source.onopen = () => {
        if (eventSource !== source) return;
        retryCount = 0;
        set({ connected: true, status: 'connected' });
      };
      source.onerror = () => {
        if (eventSource !== source) return;
        // 主动 close 可阻断 EventSource 内部无限重连；下一轮会先申请新 ticket。
        closeEventSource();
        set({ connected: false });
        scheduleRetry();
      };
      source.onmessage = (event: MessageEvent<string>) => {
        try {
          const item = JSON.parse(event.data) as NotifyEvent;
          set((state) => ({
            events: [...state.events, item].slice(-MAX_EVENTS),
            unread: state.unread + 1,
          }));
        } catch {
          // 忽略格式不符合契约的单个 SSE 帧。
        }
      };
    } catch {
      if (shouldConnect && generation === connectionGeneration) {
        set({ connected: false });
        scheduleRetry();
      }
    } finally {
      ticketRequestInFlight = false;
      // close 后立即重新登录/打开时，旧换票请求会被 generation 丢弃；这里补发新请求。
      if (shouldConnect && eventSource === null && retryTimer === null && useAuthStore.getState().isAuthenticated()) {
        void connectWithFreshTicket();
      }
    }
  };

  const open = (): void => {
    if (!useAuthStore.getState().isAuthenticated()) return;
    if (eventSource !== null || retryTimer !== null || ticketRequestInFlight) return;

    shouldConnect = true;
    set({ connected: false, status: 'connecting' });
    void refresh();
    void connectWithFreshTicket();
  };

  return {
    events: [],
    unread: 0,
    connected: false,
    status: 'idle',
    open,
    close: () => {
      shouldConnect = false;
      connectionGeneration += 1;
      retryCount = 0;
      clearRetryTimer();
      closeEventSource();
      set({ connected: false, status: 'idle' });
    },
    refresh,
    markRead: () => set({ unread: 0 }),
  };
});
