/**
 * 全局 UI 状态：当前选中标的 / 日期范围 / 复权与周期偏好 / 侧栏折叠 / 上次路径。
 * 使用 zustand persist 中间件持久化到 localStorage，刷新后保留用户偏好。
 *
 * 持久化字段（key `aqp-ui`）：
 *   currentSymbol / rangeDays / adjust / period   —— 盯盘偏好（原有）
 *   sidebarCollapsed                              —— 布局记忆（新增，刷新不再重新展开）
 *   lastPath                                      —— 会话恢复（新增，刷新后回到原页面）
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import dayjs from 'dayjs';
import type { AdjustMode, KLinePeriod } from '@/types/stock';

export interface UiState {
  /** 当前选中标的（标准代码 600519.SH） */
  currentSymbol: string;
  /** K 线回看自然日数（日期范围长度） */
  rangeDays: number;
  adjust: AdjustMode;
  period: KLinePeriod;
  /** 左侧导航是否折叠（布局记忆；用户手动切换后长期保留） */
  sidebarCollapsed: boolean;
  /** 上次所在路径（会话恢复用）。空串=从未记录（首次访问） */
  lastPath: string;

  setCurrentSymbol: (symbol: string) => void;
  setRangeDays: (days: number) => void;
  setAdjust: (adjust: AdjustMode) => void;
  setPeriod: (period: KLinePeriod) => void;
  /** 切换侧栏折叠态 */
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  /** 记录当前路径（App 在路由变化时调用） */
  setLastPath: (path: string) => void;
  /** 由 rangeDays 推导的 [start, end]（YYYYMMDD），每次调用实时计算 */
  dateRange: () => { start: string; end: string };
}

/** 不应被"会话恢复"记录的路径（无侧栏 / 无意义的入口） */
const NON_RESTORABLE = new Set(['/login']);

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      currentSymbol: '600519.SH',
      rangeDays: 365,
      adjust: 'none',
      period: 'day',
      sidebarCollapsed: false,
      lastPath: '',

      setCurrentSymbol: (symbol) => set({ currentSymbol: symbol }),
      setRangeDays: (days) => set({ rangeDays: Math.max(30, Math.min(days, 3650)) }),
      setAdjust: (adjust) => set({ adjust }),
      setPeriod: (period) => set({ period }),

      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

      setLastPath: (path) => {
        // 只记录可恢复路径，避免把 /login 或空路径写进去
        if (!path || NON_RESTORABLE.has(path)) return;
        set({ lastPath: path });
      },

      dateRange: () => {
        const end = dayjs().format('YYYYMMDD');
        const start = dayjs().subtract(get().rangeDays, 'day').format('YYYYMMDD');
        return { start, end };
      },
    }),
    {
      name: 'aqp-ui',
      // 只持久化数据字段，不持久化函数（zustand 默认已忽略函数，此处显式声明更清晰）
      partialize: (s) => ({
        currentSymbol: s.currentSymbol,
        rangeDays: s.rangeDays,
        adjust: s.adjust,
        period: s.period,
        sidebarCollapsed: s.sidebarCollapsed,
        lastPath: s.lastPath,
      }),
    },
  ),
);
