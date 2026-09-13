/**
 * 全局顶部栏（按设计稿还原）。
 *
 * - 搜索框：对接 stock/search + etf/list，支持代码与名称模糊搜索；
 *   6 位纯数字 / XXXXXX.SH 形式仍可直接回车跳转。
 * - 右侧：自选收藏 / 通知 / 登录态。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ROLE_LABEL } from '@/api/auth';
import { etfApi } from '@/api/etf';
import { stockApi } from '@/api/stock';
import { useAuthStore } from '@/stores/useAuthStore';
import { useNotifyStore } from '@/stores/useNotifyStore';

const ICON_BASE = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

function IconSearch({ className }: { className?: string }) {
  return (
    <svg {...ICON_BASE} className={className}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function IconStar({ className }: { className?: string }) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="m12 3 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3-5.8 3 1.1-6.5L3.6 9.8l6.5-.9L12 3Z" />
    </svg>
  );
}

function IconBell({ className }: { className?: string }) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8" />
      <path d="M13.7 21a2 2 0 0 1-3.4 0" />
    </svg>
  );
}

type SearchItem =
  | { kind: 'stock'; symbol: string; code: string; name: string }
  | { kind: 'etf'; code: string; name: string | null };

/** 通知事件类别 -> 标签（P2-15 顶栏铃铛下拉） */
const NOTIFY_KIND_LABEL: Record<string, string> = {
  sync: '数据同步',
  mining: '因子挖掘',
  auto_sync: '自动同步',
  backtest: '回测',
  monitor: '健康监控',
  report: 'AI 日报',
};

function isDirectCode(text: string): boolean {
  return /^\d{6}$/.test(text) || /^\d{6}\.(SH|SZ|BJ)$/i.test(text);
}

export default function Topbar() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [results, setResults] = useState<SearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  // P2-15：通知铃铛（SSE 实时事件 + 未读角标 + 下拉最近事件）
  const [notifyOpen, setNotifyOpen] = useState(false);
  const notifyRef = useRef<HTMLDivElement>(null);
  const { events, unread, connected, open: openNotify, markRead } = useNotifyStore();
  useEffect(() => { openNotify(); }, [openNotify]);
  const { user, isAuthenticated, clear } = useAuthStore();
  const authed = isAuthenticated();

  const goTo = useCallback((item: SearchItem) => {
    if (item.kind === 'stock') navigate(`/stock/${item.symbol}`);
    else navigate(`/etf/${item.code}`);
    setQ('');
    setResults([]);
    setOpen(false);
  }, [navigate]);

  const submit = useCallback(() => {
    const text = q.trim();
    if (!text) return;
    if (/^\d{6}$/.test(text)) {
      navigate(`/etf/${text}`);
    } else if (/^\d{6}\.(SH|SZ|BJ)$/i.test(text)) {
      navigate(`/stock/${text.toUpperCase()}`);
    } else if (results.length > 0) {
      goTo(results[0]);
    } else {
      navigate(`/screener`);
    }
    setQ('');
    setResults([]);
    setOpen(false);
  }, [q, results, navigate, goTo]);

  useEffect(() => {
    const text = q.trim();
    if (text.length < 2 || isDirectCode(text)) {
      setResults([]);
      setOpen(false);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        setSearching(true);
        try {
          const [stocks, etfRes] = await Promise.all([
            stockApi.search(text, 8).catch(() => []),
            etfApi.list({ q: text, country: 'cn', page_size: 8 }).catch(() => ({ items: [] })),
          ]);
          if (cancelled) return;
          const items: SearchItem[] = [
            ...stocks.map((s) => ({
              kind: 'stock' as const,
              symbol: s.symbol,
              code: s.code,
              name: s.name,
            })),
            ...etfRes.items.map((e) => ({
              kind: 'etf' as const,
              code: e.code,
              name: e.name,
            })),
          ];
          setResults(items.slice(0, 12));
          setOpen(items.length > 0);
        } finally {
          if (!cancelled) setSearching(false);
        }
      })();
    }, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [q]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
      if (notifyRef.current && !notifyRef.current.contains(e.target as Node)) {
        setNotifyOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-hair bg-white px-4 lg:px-5">
      {/* 搜索 */}
      <div ref={wrapRef} className="relative w-full max-w-md">
        <IconSearch className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => { if (results.length) setOpen(true); }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') setOpen(false); }}
          placeholder="搜索股票 / ETF 代码或名称"
          className="h-8 w-full rounded-md border border-hair bg-slate-50 pl-8 pr-3 text-xs text-ink outline-none transition-colors placeholder:text-ink-muted focus:border-brand-300 focus:bg-white"
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
          aria-controls="topbar-search-list"
        />
        {open && results.length > 0 && (
          <ul
            id="topbar-search-list"
            className="absolute left-0 right-0 top-full z-30 mt-1 max-h-72 overflow-y-auto rounded-md border border-hair bg-white py-1 shadow-lg"
          >
            {results.map((item) => (
              <li key={`${item.kind}-${item.kind === 'stock' ? item.symbol : item.code}`}>
                <button
                  type="button"
                  onClick={() => goTo(item)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-slate-50"
                >
                  <span className={`shrink-0 rounded px-1 py-0.5 text-2xs ${
                    item.kind === 'stock' ? 'bg-brand-50 text-brand-700' : 'bg-violet-50 text-violet-700'
                  }`}>
                    {item.kind === 'stock' ? '股票' : 'ETF'}
                  </span>
                  <span className="num font-medium text-ink">
                    {item.kind === 'stock' ? item.code : item.code}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-ink-secondary">
                    {item.name ?? '—'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {searching && q.trim().length >= 2 && !isDirectCode(q.trim()) && (
          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-2xs text-ink-muted">
            搜索中…
          </span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        <button onClick={() => navigate('/watchlist')} title="自选收藏"
          className="rounded-md p-1.5 text-ink-secondary transition-colors hover:bg-slate-100 hover:text-ink">
          <IconStar className="h-4 w-4" />
        </button>
        {/* P2-15：通知铃铛（SSE 实时推送后台任务事件，替代原装饰性红点） */}
        <div ref={notifyRef} className="relative">
          <button
            title={connected ? '通知（实时）' : '通知（连接中…）'}
            onClick={() => { setNotifyOpen((v) => !v); markRead(); }}
            className="relative rounded-md p-1.5 text-ink-secondary transition-colors hover:bg-slate-100 hover:text-ink">
            <IconBell className="h-4 w-4" />
            {unread > 0 && (
              <span className="num absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-up px-1 text-[9px] font-semibold text-white">
                {unread > 99 ? '99+' : unread}
              </span>
            )}
          </button>
          {notifyOpen && (
            <div className="absolute right-0 top-full z-30 mt-1 w-80 rounded-md border border-hair bg-white shadow-lg">
              <div className="flex items-center justify-between border-b border-hair px-3 py-2">
                <span className="text-xs font-semibold text-ink">通知</span>
                <span className="text-2xs text-ink-muted">
                  {connected ? '实时' : '连接中…'}
                </span>
              </div>
              <div className="max-h-72 overflow-y-auto py-1">
                {events.length === 0 ? (
                  <div className="px-3 py-6 text-center text-2xs text-ink-muted">
                    暂无通知（数据同步 / 因子挖掘完成后会推送到这里）
                  </div>
                ) : [...events].reverse().map((e, i) => (
                  <div key={`${e.ts}-${i}`}
                    className="flex items-start gap-2 px-3 py-2 hover:bg-slate-50">
                    <span className={`mt-0.5 shrink-0 rounded px-1 py-0.5 text-[10px] ${
                      e.kind === 'mining' ? 'bg-violet-50 text-violet-700'
                        : 'bg-brand-50 text-brand-700'
                    }`}>
                      {NOTIFY_KIND_LABEL[e.kind] ?? e.kind}
                    </span>
                    <span className="min-w-0 flex-1 break-words text-2xs text-ink-secondary">
                      {e.message}
                    </span>
                    <span className="num shrink-0 text-[10px] text-ink-muted">{e.ts}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="ml-1 flex items-center gap-2 border-l border-hair pl-3">
          {authed && user ? (
            <>
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-500 text-2xs font-semibold text-white"
                title={user.username}>
                {user.username.slice(0, 2).toUpperCase()}
              </div>
              <span className="hidden text-xs font-medium text-ink md:inline">{user.username}</span>
              {ROLE_LABEL[user.role] && (
                <span className="hidden rounded bg-slate-100 px-1.5 py-0.5 text-2xs text-ink-secondary md:inline">
                  {ROLE_LABEL[user.role]}
                </span>
              )}
              <button
                onClick={() => { clear(); navigate('/login'); }}
                className="rounded px-1.5 py-0.5 text-2xs text-ink-secondary transition-colors hover:bg-slate-100 hover:text-ink">
                退出
              </button>
            </>
          ) : (
            <button
              onClick={() => navigate('/login')}
              className="rounded-md bg-brand-500 px-2.5 py-1 text-2xs font-medium text-white transition-colors hover:bg-brand-600">
              登录
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
