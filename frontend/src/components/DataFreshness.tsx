/**
 * 数据新鲜度徽标 + 手动刷新（路线图 §3.2，Sprint1）：
 *
 *   [ 数据截至 2026-09-04 · 缓存 ]  [⟳]
 *
 * 徽标语义（对齐后端 §3.2 契约）：
 *   - stale=true                  → 橙「缓存 · 更新中」（软过期旧值 + 后台重建中）
 *   - from_cache === 'refreshed'  → 绿「已刷新」（refresh=1 强制重算完成）
 *   - from_cache === true         → 灰「缓存」
 *   - 其余（false/undefined）     → 绿「实时」
 * 刷新按钮：点击触发 onRefresh（页面侧应带 refresh=1 重拉）；请求进行中禁用 +
 * 图标旋转；5s 内重复点击合并（前端防抖，与后端 refresh 锁双保险）。
 */
import { useCallback, useRef, useState } from 'react';

export interface DataFreshnessProps {
  /** 数据截至时间戳（trade_date / as_of / last_sync，展示原样；空则不显示前缀） */
  asOf?: string | null;
  /** 后端 from_cache 字段：true=缓存命中 / 'refreshed'=刚强制重算 / false=实时构建 */
  fromCache?: boolean | string | null;
  /** 后端 stale 字段：true=缓存已软过期，旧值服务中 + 后台重建中 */
  stale?: boolean;
  /** 手动刷新回调（缺省则不渲染刷新按钮） */
  onRefresh?: () => Promise<unknown> | void;
}

/** 5s 内重复点击合并（§3.2 防抖规格） */
const DEBOUNCE_MS = 5000;

export default function DataFreshness({ asOf, fromCache, stale, onRefresh }: DataFreshnessProps) {
  const [refreshing, setRefreshing] = useState(false);
  const lastClickRef = useRef(0);

  const badgeCls = stale
    ? 'bg-warn-bg text-warn'
    : fromCache === true
      ? 'bg-surface-sunken text-ink-muted'
      : 'bg-success-bg text-success';
  const badgeText = stale
    ? '缓存 · 更新中'
    : fromCache === 'refreshed'
      ? '已刷新'
      : fromCache === true
        ? '缓存'
        : '实时';

  const doRefresh = useCallback(async () => {
    if (!onRefresh || refreshing) return;
    const now = Date.now();
    if (now - lastClickRef.current < DEBOUNCE_MS) return;
    lastClickRef.current = now;
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  }, [onRefresh, refreshing]);

  return (
    <div className="flex items-center gap-1.5">
      {asOf ? <span className="text-2xs text-ink-muted">数据截至 {asOf}</span> : null}
      <span className={`rounded px-1.5 py-0.5 text-2xs ${badgeCls}`}>{badgeText}</span>
      {onRefresh && (
        <button onClick={() => void doRefresh()} disabled={refreshing}
          title="跳过缓存强制重算（5 秒内重复点击合并）"
          className="flex items-center rounded border border-hair bg-surface px-1.5 py-0.5 text-ink-secondary transition-colors hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
            strokeLinecap="round" strokeLinejoin="round"
            className={`h-3 w-3 ${refreshing ? 'animate-spin' : ''}`}>
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
      )}
    </div>
  );
}
