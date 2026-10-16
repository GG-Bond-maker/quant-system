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

/**
 * 标的中心：多资产标的看板（股票、ETF、期货三合一选品）。
 */
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

/** 策略研究：漏斗+分层（区别于「因子实验室」的锥形瓶） */
function IconResearch({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M4 4h16l-6 7v7l-4 2v-9L4 4Z" />
      <path d="M4 20h16" />
    </svg>
  );
}



/** 执行与风控：仪表盘（区别于「策略回测」的时钟箭头） */
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

interface NavItem {
  to: string;
  label: string;
  Icon: (p: IconProps) => JSX.Element;
  /** 动态路由（如 /stock/:symbol）用前缀匹配激活态 */
  prefix?: boolean;
  /** 自定义前缀串（默认 `${to}/`）。用于「列表页 / 详情页」两个入口互斥高亮 */
  matchBase?: string;
  /** 额外视为匹配的路径（例如标的中心聚合了 /screener, /etf, /futures） */
  extraPaths?: string[];
  /** 尚未实现的页面：渲染为不可点，避免产生 404 死链 */
  disabled?: boolean;
  /** 该入口实际落到的页面（与 to 不同即为「整合入口」，用于 hover 提示） */
  mergedNote?: string;
}

/** 导航分组：按「用户任务」组织，而非按「功能」罗列 */
interface NavGroup {
  /** 组标题（collapsed 时不显示） */
  title: string;
  items: NavItem[];
}

/**
 * 导航结构（由 18 项平铺重组为 4 大核心业务工作流 + 底部固定区）。
 *
 * 分组依据 = 量化投研全生命周期标准工作流（对标 JoinQuant / RiceQuant / QuantConnect / QMT）：
 *   1. 行情与标的 → 盘前盘中全景盯盘、多资产选品与单标的微观画像深度穿透
 *   2. 策略研发   → 从因子挖掘、特征工程、买卖信号到单策略回测、多资产配置与容量归因
 *   3. 交易与风控 → 实盘/模拟算法拆单执行中枢与实时风控异动预警
 *   4. 数据工程   → 全市场行情湖仓、数据质量血缘与 11 步 DAG 任务调度
 */
const NAV_GROUPS: NavGroup[] = [
  {
    title: '行情与标的',
    items: [
      { to: '/', label: '市场概览', Icon: IconOverview },
      // 标的中心：多资产选品台（聚合股票选股、ETF中心与期货中心，顶部 Tab 随时切换）
      { to: '/screener', label: '标的中心', Icon: IconInstruments,
        extraPaths: ['/etf', '/futures'], mergedNote: '选股 · ETF · 期货' },
      { to: '/watchlist', label: '自选池', Icon: IconStar },
      // 深度行情：微观K线画像、F10分析，覆盖个股、ETF与期货标的
      { to: '/stock/600519.SH', label: '深度行情', Icon: IconStock,
        prefix: true, matchBase: '/stock/', extraPaths: ['/futures/analysis'],
        mergedNote: '个股 · ETF · 期货' },
    ],
  },
  {
    title: '策略研发',
    items: [
      // 策略研发：聚合策略工作台（四象限模型）与因子实验室（IC/IR时序分析）
      { to: '/research', label: '策略研发', Icon: IconResearch,
        extraPaths: ['/studio'], mergedNote: '策略工作台 · 因子实验室' },
      // 回测与组合：聚合单策略回测、多资产组合回测与容量归因
      { to: '/backtest', label: '回测与组合', Icon: IconBacktest,
        extraPaths: ['/portfolio', '/capacity'], mergedNote: '策略回测 · 组合配置 · 容量归因' },
    ],
  },
  {
    title: '交易与风控',
    items: [
      // 交易大厅：实盘/模拟盘执行中心、五档盘口、算法分批拆单与风控熔断控制台
      { to: '/desk', label: '交易大厅', Icon: IconGauge,
        extraPaths: ['/trading', '/orderdesk'], mergedNote: '盘口 · 委托 · 算法交易' },
      // 预警中心：异动监控、信号预警与风控触达通知
      { to: '/alerts', label: '预警中心', Icon: IconBell },
    ],
  },
  {
    title: '数据工程',
    items: [
      // 数据工程：聚合数据资产概览、质量巡检与调度流水线
      { to: '/data', label: '数据工程', Icon: IconDatabase,
        extraPaths: ['/dataquality', '/pipeline'], mergedNote: '湖仓资产 · 质量巡检 · 调度管线' },
    ],
  },
];

/** 底部固定区：低频、非交易动作，移出主列表但保持可达 */
const NAV_FOOTER: NavItem[] = [
  { to: '/report', label: 'AI 日报', Icon: IconReport },
  { to: '/settings', label: '系统设置', Icon: IconSettings },
];

function isActive(pathname: string, item: NavItem): boolean {
  if (item.disabled) return false;
  if (item.extraPaths?.includes(pathname)) return true;
  if (item.prefix) {
    // 前缀匹配：覆盖「详情/子页面」而不与同级入口争抢高亮。
    //   ① `/stock/:symbol`（个股分析）——`to` 即一个具体标的，覆盖所有 /stock/*；
    //   ② `/etf/510300`（ETF 分析）——`matchBase:'/etf/'` 覆盖所有 /etf/*，
    //      与「标的中心」（`to:'/screener'`，**精确匹配**）路径空间不重叠 ⇒ 恰好一个高亮。
    //   ③ `/futures/analysis`（期货分析）——覆盖其自身及子路径。
    const base = item.matchBase ?? (item.to.endsWith('/') ? item.to : `${item.to}/`);
    if (pathname === item.to || pathname.startsWith(base)) return true;
    // 深度行情入口：覆盖 ETF 单标的详情页（/etf/*）与期货分析页（/futures/analysis）
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
  // 「整合入口」hover 提示：告知被整合页面的真实落点，避免用户以为功能被删
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
    <aside className={`sticky top-0 flex h-screen shrink-0 flex-col border-r border-hair bg-surface transition-all ${
      collapsed ? 'w-14' : 'w-40'
    }`}>
      {/* 品牌区：汉堡按钮 + Logo（与截图一致，汉堡在侧边栏左上角） */}
      <div className={`flex items-center py-4 ${collapsed ? 'justify-center px-2' : 'gap-2 px-3.5'}`}>
        {onToggle && (
          <button onClick={onToggle} aria-label="收起/展开侧边栏"
            className="rounded p-1 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-ink">
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

      {/* 导航区：按「用户任务」分 3 组 + 底部固定区（collapsed 时隐藏组标题） */}
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
        {/* 底部固定区：低频动作，与主任务流分隔 */}
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
