/**
 * 共享自选数据源（P1-6）：统一「store 派生标的 → 拉取 → 按 refresh_freq 轮询」
 * 的页面侧逻辑，选股中心与我的收藏两个页面共用。
 *
 * 接口层保留两套领域端点（由 fetcher 注入，能力并不重复）：
 * - 选股域 /screener/watchlist：行情 + 预测分/行业/风险（选股上下文核心列）
 * - 收藏域 /watchlist/dashboard：行情 + K线形态/预警/迷你K线/资金流
 * 若强行并成一接口会丢其中一侧字段，故合并的是页面侧数据流而非端点。
 *
 * 轮询仅在页面可见时触发（visibilityState），与市场概览页一致。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '@/api/client';
import { useWatchlistStore } from '@/stores/useWatchlistStore';
import { useRefreshIntervalMs } from './useRefreshInterval';

export interface WatchlistQuotesState<T> {
  /** 全部分组去重后的自选标的 */
  symbols: string[];
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export function useWatchlistQuotes<T>(
  fetcher: (symbols: string[]) => Promise<T>,
  /** 轮询倍率（相对 refresh_freq）；1 = 与市场概览同频 */
  mult = 1,
  /** 轮询开关（如选股页仅在自选视图激活时轮询，榜单视图下不发无效请求） */
  pollEnabled = true,
): WatchlistQuotesState<T> {
  const groups = useWatchlistStore((s) => s.groups);
  // join 作 key 稳定数组引用：groups 内容不变时 symbols 不重建，load 不重触发
  const groupKey = Object.values(groups).flat().join(',');
  const symbols = useMemo(
    () => Array.from(new Set(groupKey ? groupKey.split(',') : [])),
    [groupKey],
  );

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const intervalMs = useRefreshIntervalMs(mult);

  // fetcher 存 ref：调用方传内联箭头函数也不会导致 load 身份变化而重触发请求
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  // 代际守卫（B8-06）：自选集合变化/轮询重入时，只有最新一次请求可写状态，
  // 旧响应后到不得覆盖新数据。
  const seqRef = useRef(0);

  const load = useCallback(async () => {
    const seq = ++seqRef.current;
    if (!symbols.length) { setData(null); setLoading(false); setError(null); return; }
    setLoading(true);
    try {
      const d = await fetcherRef.current(symbols);
      if (seq !== seqRef.current) return;
      setData(d);
      setError(null);
    } catch (e) {
      if (seq !== seqRef.current) return;
      setError(e instanceof ApiError ? e.message : '加载失败');
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, [symbols]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!pollEnabled) return;
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, intervalMs);
    return () => clearInterval(t);
  }, [load, intervalMs, pollEnabled]);

  return { symbols, data, loading, error, reload: load };
}
