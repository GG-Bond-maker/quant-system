/**
 * A 股交易时段判定（共享 hook）。
 *
 * 此前 `isMarketOpen()` 在 `MarketOverview/index.tsx` 与 `StockDetail/index.tsx`
 * 各写了一份逐字节相同的副本——任一处改动都可能造成两个页面轮询行为不一致。
 * 现收敛到此单点，两处 import 复用。
 *
 * 口径：周一~周五 09:15–15:05（含集合竞价缓冲与收盘后 5 分钟尾差）。
 * 注意：**不含法定节假日**，仅按星期过滤；节假日会退化为「盘中」，
 * 表现为多轮询几次空数据，不会产生错误展示。
 */

/** 早盘集合竞价起点（含）：09:15 → 555 分钟 */
const OPEN_MIN = 9 * 60 + 15;

/** 收盘缓冲终点（含）：15:05 → 905 分钟 */
const CLOSE_MIN = 15 * 60 + 5;

/**
 * 判断给定时刻是否处于 A 股（沪深）盘中时段。
 *
 * @param now 判定基准时刻，默认当前时间；传入固定值便于测试。
 * @returns 盘中返回 true；周末与盘外时段返回 false。
 */
export function isMarketOpen(now: Date = new Date()): boolean {
  const day = now.getDay();
  // 0 = 周日，6 = 周六
  if (day === 0 || day === 6) return false;
  const mins = now.getHours() * 60 + now.getMinutes();
  return mins >= OPEN_MIN && mins <= CLOSE_MIN;
}

/**
 * React hook 形态：返回当前的盘中状态。
 *
 * 说明：本 hook **不订阅时钟**——调用方若需要在跨越时段边界时自动翻转状态，
 * 应把返回值放进自身的 refreshInterval 依赖里（现有两个页面均为轮询场景，
 * 每次请求会重新求值，因此无需额外定时器）。
 */
export function useMarketSession(now?: Date): { isOpen: boolean } {
  return { isOpen: isMarketOpen(now) };
}
