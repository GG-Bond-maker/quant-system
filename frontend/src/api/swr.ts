/**
 * SWR 数据层基建（L4，Sprint4-遗留项闭环）。
 *
 * 约定：
 * - 全局 fetcher 走 api/client（统一信封解包 + 认证头 + ApiError）；
 * - refreshInterval 按端点分级（路线图：实时 30s / 快照 5min / 静态 0），
 *   常量集中在 REFRESH 分级表，页面不自行拍数字；
 * - revalidateOnFocus 开启（页面二次切换 = 缓存命中，目标 <100ms）；
 * - dedupingInterval 5s：同一 key 的并发请求合并；
 * - 后端 L1 的 stale 回旧值策略与此叠加：SWR 先回缓存再校验，感知不到
 *   后端 stale:true 的过渡期（前端徽标消费 rt.stale 已覆盖）。
 *
 * 迁移节奏：MarketOverview（rt/daily）为首个消费者；其余页面按需迁移，
 * 迁移页面应删除手写 setInterval 轮询（SWR refreshInterval 等价替代）。
 */
import useSWR, { SWRConfiguration } from 'swr';
import { get } from './client';

export const fetcher = <T,>(
  url: string,
  params?: Record<string, unknown>,
  timeout?: number,
): Promise<T> =>
  // 关闭 client 的网络层重试：SWR 自身已配 errorRetryCount: 2 / errorRetryInterval: 5000，
  // 若不 opt-out，两者叠加最坏会放大到 (1+1)×(1+2)=6 次请求（client 层重试 1 次 × SWR 层重试 2 次）。
  // 重试策略统一由 SWR 负责，网络层只做一次性重放，避免请求量失控。
  get<T>(url, params, timeout, { retry: false });

/** 端点刷新分级（秒）；0 = 不轮询 */
export const REFRESH = {
  realtime: 30,     // 实时块（行情快照类）
  snapshot: 300,    // 快照类（榜单/日报/研究面板）
  static: 0,        // 静态配置
} as const;

/** SWR 全局默认配置（页面可覆盖） */
export const swrDefaults: SWRConfiguration = {
  revalidateOnFocus: true,
  revalidateIfStale: true,
  dedupingInterval: 5_000,
  errorRetryCount: 2,
  errorRetryInterval: 5_000,
};

/** 组合全局默认的 hook：key 为 URL + params 序列化（params 变化自动重取）。
 *
 * timeout：per-request 超时（毫秒），透传到 client.get；聚合类端点（如
 * /market/overview 系列冷算 20~25s）必须显式放宽，否则落回 client 的 15s 默认值。
 * 不放进 SWR key —— 超时是端点的静态属性，纳入 key 会让 mutate 键失配。
 */
export function useApi<T>(url: string | null, params?: Record<string, unknown>,
                          config?: SWRConfiguration, timeout?: number) {
  const key = url ? [url, params ?? {}] : null;
  return useSWR<T>(
    key,
    ([u, p]) => fetcher<T>(u as string, p as Record<string, unknown>, timeout),
    { ...swrDefaults, ...config },
  );
}
