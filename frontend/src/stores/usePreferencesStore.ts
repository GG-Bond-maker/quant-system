/**
 * 用户偏好（刷新频率等）：与系统设置同步，供各页面轮询间隔消费。
 */
import { create } from 'zustand';
import { settingsApi } from '@/api/settings';

const DEFAULT_REFRESH_SEC = 5;

export interface PreferencesState {
  refreshFreqSec: number;
  loaded: boolean;
  setRefreshFreq: (sec: number) => void;
  /** 已登录时从 /settings 拉取；失败则保持当前值 */
  loadFromServer: () => Promise<void>;
}

export const usePreferencesStore = create<PreferencesState>((set, get) => ({
  refreshFreqSec: DEFAULT_REFRESH_SEC,
  loaded: false,

  setRefreshFreq: (sec) => {
    const v = Math.max(1, Math.min(120, sec));
    set({ refreshFreqSec: v, loaded: true });
  },

  loadFromServer: async () => {
    try {
      const bundle = await settingsApi.all();
      const freq = bundle.settings.preferences.refresh_freq;
      if (typeof freq === 'number' && freq > 0) {
        get().setRefreshFreq(freq);
      } else {
        set({ loaded: true });
      }
    } catch {
      set({ loaded: true });
    }
  },
}));

/** 读取当前刷新间隔（秒） */
export function getRefreshIntervalMs(): number {
  return usePreferencesStore.getState().refreshFreqSec * 1000;
}
