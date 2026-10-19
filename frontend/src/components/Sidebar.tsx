/**
 * 全局左侧导航栏（AQP 终端级交互工作台，吸收 TSP 设计精髓）。
 *
 * - 桌面三态布局：展开 (14rem, w-56) <-> 紧凑图标 (3.5rem, w-14) <-> 移动端抽屉；
 * - 核心指数常驻条：上证、深成、创业板、沪深300股指期货主力四条行情微缩矩阵；
 * - 数据源能力健康度 5 格微标：日K、实时、分时、财务、期货健康度与延迟悬浮看板；
 * - 实时心跳指示：盘中实时（呼吸光晕）与非交易时段状态指示。
 */
import { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ROLE_LABEL } from '@/api/auth';
import type { CapabilityMatrixData } from '@/api/datacenter';
import type { QuotesData } from '@/api/market';
import { useApi } from '@/api/swr';
import { isMarketOpen } from '@/hooks/useMarketSession';
import { useAuthStore } from '@/stores/useAuthStore';

type IconProps = { className?: string };

/** 统一的 24×24 线性图标规范：stroke=1.6，无填充 */
const ICON_BASE = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

function IconOverview({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M3 13h6V3H3v10Zm0 8h6v-6H3v6Zm8 0h10V11H11v10Zm0-18v6h10V3H11Z" />
    </svg>
  );
}

function IconStock({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M3 17.5 9 11l4 4 8-8.5" />
      <path d="M15 6h6v6" />
      <path d="M3 21h18" />
    </svg>
  );
}

function IconInstruments({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <path d="m14 17.5 3-3.5 4 4" />
    </svg>
  );
}

function IconBacktest({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M12 3a9 9 0 1 0 9 9" />
      <path d="M12 7v5l4 2" />
      <path d="M17 3v4h4" />
    </svg>
  );
}

function IconDatabase({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v12c0 1.66 3.58 3 8 3s8-1.34 8-3V6" />
      <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
    </svg>
  );
}

function IconStar({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="m12 3 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3-5.8 3 1.1-6.5L3.6 9.8l6.5-.9L12 3Z" />
    </svg>
  );
}

function IconBell({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" />
      <path d="M10 19a2 2 0 0 0 4 0" />
    </svg>
  );
}

function IconReport({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M6 3h9l4 4v14H6V3Z" />
      <path d="M14 3v5h5" />
      <path d="M9 12h7M9 16h7" />
    </svg>
  );
}

function IconMenu({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

function IconResearch({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M4 4h16l-6 7v7l-4 2v-9L4 4Z" />
      <path d="M4 20h16" />
    </svg>
  );
}

function IconGauge({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M4 18a9 9 0 1 1 16 0" />
      <path d="m12 14 4.5-4.5" />
      <circle cx="12" cy="14" r="1.4" />
      <path d="M3 18h18" />
    </svg>
  );
}

function IconSettings({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.1V4a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 11h.1a2 2 0 1 1 0 4H21Z" />
    </svg>
  );
}

function IconZap({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  );
}

interface NavItem {
  to: string;
  label: string;
  Icon: (p: IconProps) => JSX.Element;
  prefix?: boolean;
  matchBase?: string;
  extraPaths?: string[];
  disabled?: boolean;
  mergedNote?: string;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: '行情与标的',
    items: [
      { to: '/', label: '市场概览', Icon: IconOverview },
      { to: '/screener', label: '标的中心', Icon: IconInstruments, extraPaths: ['/etf', '/futures'], mergedNote: '选股 · ETF · 期货' },
      { to: '/watchlist', label: '自选池', Icon: IconStar },
      { to: '/stock/600519.SH', label: '深度行情', Icon: IconStock, prefix: true, matchBase: '/stock/', extraPaths: ['/futures/analysis'], mergedNote: '个股 · ETF · 期货' },
    ],
  },
  {
    title: '策略研发',
    items: [
      { to: '/research', label: '策略研发', Icon: IconResearch, extraPaths: ['/studio'], mergedNote: '策略工作台 · 因子实验室' },
      { to: '/backtest', label: '回测与组合', Icon: IconBacktest, extraPaths: ['/portfolio', '/capacity'], mergedNote: '策略回测 · 组合配置 · 容量归因' },
    ],
  },
  {
    title: '交易与风控',
    items: [
      { to: '/desk', label: '交易大厅', Icon: IconGauge, extraPaths: ['/trading', '/orderdesk'], mergedNote: '盘口 · 委托 · 算法交易' },
      { to: '/alerts', label: '预警中心', Icon: IconBell },
    ],
  },
  {
    title: '数据工程',
    items: [
      { to: '/data', label: '数据工程', Icon: IconDatabase, extraPaths: ['/dataquality', '/pipeline'], mergedNote: '湖仓资产 · 质量巡检 · 调度管线' },
    ],
  },
];

const NAV_FOOTER: NavItem[] = [
  { to: '/report', label: 'AI 日报', Icon: IconReport },
  { to: '/settings', label: '系统设置', Icon: IconSettings },
];

function fmtIndexPct(pct?: number | null) {
  if (pct == null || isNaN(pct)) return '--';
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function fmtIndexPrice(price?: number | null) {
  if (price == null || isNaN(price)) return '--';
  return price.toFixed(2);
}

function indexPctClass(pct?: number | null) {
  if (pct == null || isNaN(pct) || pct === 0) return 'text-ink-muted';
  return pct > 0 ? 't-up' : 't-down';
}

function DataSourceHealthBadge({ collapsed }: { collapsed: boolean }) {
  const { data } = useApi<CapabilityMatrixData>('/api/v1/datacenter/capabilities', undefined, { refreshInterval: 60_000 });
  const [showPop, setShowPop] = useState(false);
  const caps = data?.capabilities ?? [];
  const usable = data?.usable_count ?? 5;
  const total = data?.total_count ?? 5;

  const dotColor = (status: string) => {
    if (status === 'healthy') return 'bg-emerald-500';
    if (status === 'degraded') return 'bg-amber-500';
    return 'bg-rose-500';
  };

  return (
    <div className="relative">
      <button
        onClick={() => setShowPop(v => !v)}
        onMouseEnter={() => setShowPop(true)}
        onMouseLeave={() => setShowPop(false)}
        className={`group flex items-center rounded-md border border-hair bg-surface-alt/70 transition-colors hover:bg-surface-alt ${
          collapsed ? 'h-8 w-full justify-center px-1' : 'h-7 w-full justify-between px-2'
        }`}
        title="数据源能力矩阵"
      >
        <div className="flex items-center gap-1.5 min-w-0">
          <IconZap className="h-3 w-3 shrink-0 text-brand-500" />
          {!collapsed && (
            <span className="text-[11px] font-medium text-ink-secondary">数据源</span>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <div className="flex items-center gap-0.5">
            {caps.length === 0 ? (
              Array.from({ length: 5 }, (_, i) => (
                <span key={i} className="h-1.5 w-1.5 rounded-[1px] bg-ink-muted/40 animate-pulse" />
              ))
            ) : (
              caps.map((c) => (
                <span key={c.id} className={`h-1.5 w-1.5 rounded-[1px] ${dotColor(c.status)}`} />
              ))
            )}
          </div>
          {!collapsed && (
            <span className="ml-1 text-[10px] font-mono text-ink-muted">
              {usable}/{total}
            </span>
          )}
        </div>
      </button>

      {showPop && (
        <div
          onMouseEnter={() => setShowPop(true)}
          onMouseLeave={() => setShowPop(false)}
          className="absolute left-full top-0 z-50 ml-2 w-64 rounded-lg border border-hair bg-surface p-2.5 shadow-xl shadow-black/20"
        >
          <div className="mb-2 flex items-center justify-between border-b border-hair pb-1.5">
            <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
              <IconZap className="h-3.5 w-3.5 text-brand-500" />
              数据源能力矩阵
            </span>
            <span className="text-[10px] font-mono text-emerald-500 font-medium">
              {usable}/{total} 可用
            </span>
          </div>
          <div className="space-y-1.5">
            {caps.map((c) => (
              <div key={c.id} className="flex items-center justify-between text-2xs">
                <div className="flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-[2px] ${dotColor(c.status)}`} />
                  <span className="text-ink font-medium">{c.label}</span>
                </div>
                <div className="flex items-center gap-1.5 text-ink-muted font-mono text-[10px]">
                  <span>{c.provider}</span>
                  <span>{c.latency_ms}ms</span>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-2 border-t border-hair pt-1 text-[9px] text-ink-muted">
            全免费多源路由 · 零外部付费依赖
          </div>
        </div>
      )}
    </div>
  );
}

function RealtimeHeartbeat({ collapsed }: { collapsed: boolean }) {
  const open = isMarketOpen();
  return (
    <div
      className={`flex items-center rounded-md border border-hair bg-surface-alt/50 ${
        collapsed ? 'h-7 justify-center px-1' : 'h-6 justify-between px-2'
      }`}
      title={open ? '当前为交易时段，实时行情轮询中' : '非交易时段，行情处于收盘休市态'}
    >
      <div className="flex items-center gap-1.5 min-w-0">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
            open ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'
          }`}
        />
        {!collapsed && (
          <span className="truncate text-[10px] text-ink-muted">
            {open ? '盘中实时' : '闭市休整'}
          </span>
        )}
      </div>
      {!collapsed && (
        <span className="text-[9px] font-mono text-ink-muted/80">
          {open ? 'SSE/SWR' : 'OFF'}
        </span>
      )}
    </div>
  );
}

const CORE_INDEX_ITEMS = [
  { symbol: '000001.SH', name: '上证指数', path: '/stock/000001.SH' },
  { symbol: '399001.SZ', name: '深证成指', path: '/stock/399001.SZ' },
  { symbol: '399006.SZ', name: '创业板指', path: '/stock/399006.SZ' },
  { symbol: 'IF', name: '沪深300期指', path: '/futures/analysis?symbol=IF' },
] as const;

function SidebarIndexQuotes({ collapsed }: { collapsed: boolean }) {
  if (collapsed) return null;
  const { data } = useApi<QuotesData>(
    '/api/v1/market/quotes',
    { symbols: '000001.SH,399001.SZ,399006.SZ,IF' },
    { refreshInterval: 15_000 }
  );
  const quotesMap = useMemo(() => {
    const map = new Map<string, any>();
    for (const q of data?.quotes ?? []) {
      map.set(q.symbol, q);
    }
    return map;
  }, [data]);

  return (
    <div className="grid grid-cols-2 gap-1.5 border-t border-hair pt-2">
      {CORE_INDEX_ITEMS.map((item) => {
        const q = quotesMap.get(item.symbol);
        const price = q?.price;
        const pct = q?.pct;
        return (
          <Link
            key={item.symbol}
            to={item.path}
            className="block rounded bg-surface-alt/70 p-1.5 transition-colors hover:bg-surface-alt"
            title={`${item.name} (${item.symbol})`}
          >
            <div className="flex items-center justify-between gap-1">
              <span className="truncate text-[10px] text-ink-secondary">{item.name}</span>
              <span className={`text-[10px] font-mono ${indexPctClass(pct)}`}>
                {fmtIndexPct(pct)}
              </span>
            </div>
            <div className={`mt-0.5 truncate text-[10px] font-mono font-medium ${indexPctClass(pct)}`}>
              {fmtIndexPrice(price)}
            </div>
          </Link>
        );
      })}
    </div>
  );
}

function isActive(pathname: string, item: NavItem): boolean {
  if (item.disabled) return false;
  if (item.extraPaths?.includes(pathname)) return true;
  if (item.prefix) {
    const base = item.matchBase ?? (item.to.endsWith('/') ? item.to : `${item.to}/`);
    if (pathname === item.to || pathname.startsWith(base)) return true;
    if (item.matchBase === '/stock/' && (pathname.startsWith('/etf/') || pathname.startsWith('/futures/analysis'))) {
      return true;
    }
    return false;
  }
  return pathname === item.to;
}

function NavRow({ item, pathname, collapsed }: { item: NavItem; pathname: string; collapsed: boolean }) {
  const active = isActive(pathname, item);
  const { Icon } = item;
  const cls = `relative flex items-center rounded-md py-2 text-xs transition-colors ${
    collapsed ? 'justify-center px-0' : 'gap-2 px-2.5'
  } ${
    active
      ? 'bg-brand-50 font-medium text-brand-600'
      : item.disabled
        ? 'cursor-not-allowed text-ink-muted/60'
        : 'text-ink-secondary hover:bg-surface-alt hover:text-ink'
  }`;
  const hint = item.mergedNote ? `${item.label} · ${item.mergedNote}` : item.label;
  const body = (
    <>
      {active && <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r bg-brand-500" />}
      <Icon className="h-4 w-4 shrink-0" />
      {!collapsed && <span className="truncate">{item.label}</span>}
      {!collapsed && item.disabled && (
        <span className="ml-auto rounded bg-surface-sunken px-1 text-2xs text-ink-muted">建设中</span>
      )}
    </>
  );

  if (collapsed) {
    const tip = (
      <span className="pointer-events-none absolute left-full z-30 ml-2 hidden max-w-[220px] rounded bg-ink px-1.5 py-0.5 text-2xs leading-snug text-white group-hover:block">
        {hint}
      </span>
    );
    if (item.disabled) {
      return (
        <div className="group relative" title={hint}>
          <div className={cls}>{body}</div>
          {tip}
        </div>
      );
    }
    return (
      <div className="group relative">
        <Link to={item.to} className={cls} title={hint}>{body}</Link>
        {tip}
      </div>
    );
  }

  if (item.disabled) {
    return <div className={cls} title={`${item.label} · 功能建设中`}>{body}</div>;
  }
  return <Link to={item.to} className={cls} title={item.mergedNote ? hint : undefined}>{body}</Link>;
}

export default function Sidebar({ collapsed = false, onToggle }: {
  collapsed?: boolean;
  onToggle?: () => void;
}) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user, isAuthenticated, clear } = useAuthStore();
  const authed = isAuthenticated();

  return (
    <aside className={`sticky top-0 flex h-screen shrink-0 flex-col border-r border-hair bg-surface transition-all duration-200 ${
      collapsed ? 'w-14' : 'w-56'
    }`}>
      {/* 品牌区：汉堡/折叠按钮 + Logo */}
      <div className={`flex items-center py-3.5 ${collapsed ? 'justify-center px-2' : 'gap-2 px-3.5'}`}>
        {onToggle && (
          <button onClick={onToggle} aria-label="收起/展开侧边栏"
            className="rounded p-1 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-ink"
            title={collapsed ? '展开侧边栏' : '收起侧边栏'}>
            <IconMenu className="h-4 w-4" />
          </button>
        )}
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="text-xs font-bold leading-tight tracking-tight text-ink">AQP 智能量化</div>
            <div className="truncate text-[10px] text-ink-muted">Alpha Quant Platform</div>
          </div>
        )}
      </div>

      {/* 状态与指数监控工作台（借鉴 TSP 设计） */}
      <div className={`space-y-1.5 pb-2 ${collapsed ? 'px-2' : 'px-3'}`}>
        <RealtimeHeartbeat collapsed={collapsed} />
        <DataSourceHealthBadge collapsed={collapsed} />
        <SidebarIndexQuotes collapsed={collapsed} />
      </div>

      {/* 导航区 */}
      <nav className={`flex-1 space-y-3 overflow-y-auto pb-3 ${collapsed ? 'px-2' : 'px-2.5'}`}>
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            {!collapsed && (
              <div className="px-2.5 pb-1 text-2xs font-medium uppercase tracking-wide text-ink-muted">
                {group.title}
              </div>
            )}
            {collapsed && <div className="mx-1.5 mb-1.5 border-t border-hair" />}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavRow key={item.label} item={item} pathname={pathname} collapsed={collapsed} />
              ))}
            </div>
          </div>
        ))}
        {/* 底部固定区 */}
        <div className="space-y-0.5 pt-1">
          <div className={`mx-1.5 mb-1.5 border-t border-hair`} />
          {NAV_FOOTER.map((item) => (
            <NavRow key={item.label} item={item} pathname={pathname} collapsed={collapsed} />
          ))}
        </div>
      </nav>

      {/* 用户区 */}
      <div className={`border-t border-hair p-3 ${collapsed ? 'flex justify-center' : ''}`}>
        {authed && user ? (
          <div className={`flex items-center ${collapsed ? '' : 'gap-2.5'}`}>
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-500 text-2xs font-semibold text-white"
              title={user.username}>
              {user.username.slice(0, 2).toUpperCase()}
            </div>
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <div className="truncate text-2xs text-ink-muted">Hello,</div>
                <div className="truncate text-xs font-medium text-ink">{user.username}</div>
                {ROLE_LABEL[user.role] && (
                  <div className="truncate text-2xs text-ink-muted">{ROLE_LABEL[user.role]}</div>
                )}
              </div>
            )}
            {!collapsed && (
              <button
                onClick={() => { clear(); navigate('/login'); }}
                className="shrink-0 rounded px-1 py-0.5 text-2xs text-ink-muted hover:bg-surface-alt hover:text-ink"
                title="退出登录">
                退出
              </button>
            )}
          </div>
        ) : (
          <button
            onClick={() => navigate('/login')}
            className={`rounded-md bg-brand-500 text-2xs font-medium text-white hover:bg-brand-600 ${
              collapsed ? 'px-2 py-2' : 'w-full px-2 py-1.5'
            }`}
            title="登录">
            {collapsed ? '登' : '登录'}
          </button>
        )}
      </div>
    </aside>
  );
}
