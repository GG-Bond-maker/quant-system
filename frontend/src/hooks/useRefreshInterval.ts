/**
 * 消费系统设置中的 refresh_freq，保存偏好后自动更新轮询间隔（P1-5）。
 *
 * mult 为页面倍率：refresh_freq 语义是「市场行情基准刷新频率」，
 * 重接口页面按倍率放大（如收藏页 dashboard 12x，避免高频打后端）。
 * 直接从 zustand selector 计算（无本地 state），保存设置后所有
 * 消费组件随 store 变更自动重渲染并重建定时器。
 */
import { usePreferencesStore } from '@/stores/usePreferencesStore';

export function useRefreshIntervalMs(mult = 1): number {
  const sec = usePreferencesStore((s) => s.refreshFreqSec);
  return Math.max(1, Math.round(sec * mult)) * 1000;
}
