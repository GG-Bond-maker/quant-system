/**
 * 自选股状态（分组管理）。
 *
 * 说明：后端自选看板接口已实现（GET /api/v1/watchlist/dashboard，返回行情/
 * K线形态/预警/资金流等），本 store 只负责**分组结构的本地持久化**（localStorage，
 * key=aqp-watchlist）；行情与量化判定由后端提供，组件层按需组合。
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
