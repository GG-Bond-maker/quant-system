/**
 * 全局 UI 状态：当前选中标的 / 日期范围 / 复权与周期偏好。
 * 使用 zustand persist 中间件持久化到 localStorage，刷新后保留用户偏好。
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

  setCurrentSymbol: (symbol: string) => void;
  setRangeDays: (days: number) => void;
  setAdjust: (adjust: AdjustMode) => void;
  setPeriod: (period: KLinePeriod) => void;
  /** 由 rangeDays 推导的 [start, end]（YYYYMMDD），每次调用实时计算 */
  dateRange: () => { start: string; end: string };
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      currentSymbol: '600519.SH',
      rangeDays: 365,
      adjust: 'none',
      period: 'day',

      setCurrentSymbol: (symbol) => set({ currentSymbol: symbol }),
      setRangeDays: (days) => set({ rangeDays: Math.max(30, Math.min(days, 3650)) }),
      setAdjust: (adjust) => set({ adjust }),
      setPeriod: (period) => set({ period }),

      dateRange: () => {
        const end = dayjs().format('YYYYMMDD');
        const start = dayjs().subtract(get().rangeDays, 'day').format('YYYYMMDD');
        return { start, end };
      },
    }),
    { name: 'aqp-ui' },
  ),
);
