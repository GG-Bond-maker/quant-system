/**
 * 自选股状态（分组管理）。
 * 后端 watchlist 接口尚未实现，一期先本地持久化（localStorage），
 * 接口就绪后仅需在本 store 内同步增删，组件层无感。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const DEFAULT_GROUP = '默认分组';

export interface WatchlistState {
  /** 分组名 -> 标的代码列表（保序） */
  groups: Record<string, string[]>;

  createGroup: (name: string) => void;
  removeGroup: (name: string) => void;
  addTo: (group: string, symbol: string) => void;
  removeFrom: (group: string, symbol: string) => void;
  /** 该标的是否已在任一分组 */
  contains: (symbol: string) => boolean;
}

export const useWatchlistStore = create<WatchlistState>()(
  persist(
    (set, get) => ({
      groups: { [DEFAULT_GROUP]: [] },

      createGroup: (name) => {
        const trimmed = name.trim();
        if (!trimmed || get().groups[trimmed]) return;
        set({ groups: { ...get().groups, [trimmed]: [] } });
      },

      removeGroup: (name) => {
        if (name === DEFAULT_GROUP) return; // 默认分组不可删
        const next = { ...get().groups };
        // 组内成员迁移到默认分组
        const members = next[name] ?? [];
        delete next[name];
        next[DEFAULT_GROUP] = Array.from(
          new Set([...(next[DEFAULT_GROUP] ?? []), ...members]),
        );
        set({ groups: next });
      },

      addTo: (group, symbol) => {
        const g = get().groups[group] ?? get().groups[DEFAULT_GROUP];
        if (g.includes(symbol)) return;
        set({ groups: { ...get().groups, [group]: [...g, symbol] } });
      },

      removeFrom: (group, symbol) => {
        const g = get().groups[group];
        if (!g) return;
        set({ groups: { ...get().groups, [group]: g.filter((s) => s !== symbol) } });
      },

      contains: (symbol) =>
        Object.values(get().groups).some((list) => list.includes(symbol)),
    }),
    { name: 'aqp-watchlist' },
  ),
);
