/** 统一后台任务轮询：任务空闲时停止，页面失焦时由 SWR 暂停。 */
import type { SWRConfiguration } from 'swr';
import { useApi } from '@/api/swr';

type TaskLike = { running?: boolean; status?: string };

export function useTaskPolling<T extends TaskLike>(
  url: string | null,
  intervalMs = 2000,
  config?: SWRConfiguration,
) {
  return useApi<T>(url, undefined, {
    ...config,
    refreshInterval: (latest: T | undefined) => {
      if (!latest) return intervalMs;
      if (latest.running === true) return intervalMs;
      if (latest.status && ['queued', 'running', 'cancel_requested'].includes(latest.status)) {
        return intervalMs;
      }
      return 0;
    },
  });
}
