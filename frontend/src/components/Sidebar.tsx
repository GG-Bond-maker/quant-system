/**
 * 全局左侧导航栏（AQP 终端风格）。
 *
 * - 图标全部为内联 SVG（项目未引入图标库，避免新增依赖）；
 * - 导航项按功能分组：主功能区 / 数据区 / 个人区；
 * - 「个股分析」是动态路由（/stock/:symbol），激活态用路径前缀判断，
 *   否则从个股页跳到其他页时会丢失高亮。
 */
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ROLE_LABEL } from '@/api/auth';
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

function IconScreener({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z" />
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

function IconEtf({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M4 7h16l-1.5 12H5.5L4 7Z" />
      <path d="M9 7V5a3 3 0 0 1 6 0v2" />
      <path d="M9 12h6" />
    </svg>
  );
}

function IconPortfolio({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </svg>
  );
}

function IconResearch({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M9 3h6M10 3v6.5L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9.5V3" />
      <path d="M7 15h10" />
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

function IconSettings({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.1V4a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 11h.1a2 2 0 1 1 0 4H21Z" />
    </svg>
  );
}

interface NavItem {
  to: string;
  label: string;
  Icon: (p: IconProps) => JSX.Element;
  /** 动态路由（如 /stock/:symbol）用前缀匹配激活态 */
  prefix?: boolean;
  /** 自定义前缀串（默认 `${to}/`）。用于「列表页 / 详情页」两个入口互斥高亮 */
  matchBase?: string;
  /** 尚未实现的页面：渲染为不可点，避免产生 404 死链 */
  disabled?: boolean;
}

const NAV_MAIN: NavItem[] = [
  { to: '/', label: '市场概览', Icon: IconOverview },
  { to: '/stock/600519.SH', label: '个股分析', Icon: IconStock, prefix: true },
  { to: '/screener', label: '选股中心', Icon: IconScreener },
  // ETF 中心（列表）精确匹配 /etf；ETF 分析（详情）匹配所有 /etf/:code，两者互斥高亮
  { to: '/etf', label: 'ETF中心', Icon: IconEtf },
  { to: '/etf/510300', label: 'ETF分析', Icon: IconEtf, prefix: true, matchBase: '/etf/' },
  { to: '/backtest', label: '策略回测', Icon: IconBacktest },
  { to: '/portfolio', label: '组合回测', Icon: IconPortfolio },
  { to: '/research', label: '策略研究', Icon: IconResearch },
  { to: '/studio', label: '因子工作室', Icon: IconResearch },
  { to: '/desk', label: '执行中心', Icon: IconBacktest },
  { to: '/capacity', label: '容量与归因', Icon: IconPortfolio },
  { to: '/report', label: 'AI 日报', Icon: IconReport },
];

const NAV_DATA: NavItem[] = [
  { to: '/data', label: '数据中心', Icon: IconDatabase },
  { to: '/dataquality', label: '数据质量', Icon: IconDatabase },
  { to: '/pipeline', label: '任务调度', Icon: IconMenu },
  { to: '/watchlist', label: '我的收藏', Icon: IconStar },
  { to: '/alerts', label: '预警中心', Icon: IconBell },
  { to: '/settings', label: '系统设置', Icon: IconSettings },
];

function isActive(pathname: string, item: NavItem): boolean {
  if (item.disabled) return false;
  if (item.prefix) {
    // 动态路由（/stock/:symbol、/etf/:code）：匹配自身及其所有子路径
    const base = item.matchBase ?? (item.to.endsWith('/') ? item.to : `${item.to}/`);
    return pathname === item.to || pathname.startsWith(base);
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
        : 'text-ink-secondary hover:bg-slate-50 hover:text-ink'
  }`;
  const body = (
    <>
      {active && <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r bg-brand-500" />}
      <Icon className="h-4 w-4 shrink-0" />
      {!collapsed && <span className="truncate">{item.label}</span>}
      {!collapsed && item.disabled && (
        <span className="ml-auto rounded bg-slate-100 px-1 text-2xs text-ink-muted">建设中</span>
      )}
    </>
  );

  if (collapsed) {
    const tip = (
      <span className="pointer-events-none absolute left-full z-30 ml-2 hidden whitespace-nowrap rounded bg-slate-800 px-1.5 py-0.5 text-2xs text-white group-hover:block">
        {item.label}
      </span>
    );
    if (item.disabled) {
      return (
        <div className="group relative" title={item.label}>
          <div className={cls}>{body}</div>
          {tip}
        </div>
      );
    }
    return (
      <div className="group relative">
        <Link to={item.to} className={cls} title={item.label}>{body}</Link>
        {tip}
      </div>
    );
  }

  if (item.disabled) {
    return <div className={cls} title={`${item.label} · 功能建设中`}>{body}</div>;
  }
  return <Link to={item.to} className={cls}>{body}</Link>;
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
    <aside className={`sticky top-0 flex h-screen shrink-0 flex-col border-r border-hair bg-white transition-all ${
      collapsed ? 'w-14' : 'w-40'
    }`}>
      {/* 品牌区：汉堡按钮 + Logo（与截图一致，汉堡在侧边栏左上角） */}
      <div className={`flex items-center py-4 ${collapsed ? 'justify-center px-2' : 'gap-2 px-3.5'}`}>
        {onToggle && (
          <button onClick={onToggle} aria-label="收起/展开侧边栏"
            className="rounded p-1 text-ink-secondary transition-colors hover:bg-slate-100 hover:text-ink">
            <IconMenu className="h-4 w-4" />
          </button>
        )}
        {!collapsed && (
          <div className="min-w-0">
            <div className="text-sm font-bold leading-tight tracking-tight text-ink">量化分析平台</div>
            <div className="truncate text-2xs text-ink-muted">AQP Terminal</div>
          </div>
        )}
      </div>

      {/* 导航区 */}
      <nav className={`flex-1 space-y-4 overflow-y-auto pb-3 ${collapsed ? 'px-2' : 'px-2.5'}`}>
        <div className="space-y-0.5">
          {NAV_MAIN.map((item) => (
            <NavRow key={item.label} item={item} pathname={pathname} collapsed={collapsed} />
          ))}
        </div>
        <div>
          {!collapsed && (
            <div className="px-2.5 pb-1 text-2xs font-medium uppercase tracking-wide text-ink-muted">
              数据
            </div>
          )}
          <div className="space-y-0.5">
            {NAV_DATA.map((item) => (
              <NavRow key={item.label} item={item} pathname={pathname} collapsed={collapsed} />
            ))}
          </div>
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
                className="shrink-0 rounded px-1 py-0.5 text-2xs text-ink-muted hover:bg-slate-100 hover:text-ink"
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
