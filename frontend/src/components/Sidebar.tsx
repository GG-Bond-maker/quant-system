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

/**
 * ETF 中心：层叠篮子（代表「一篮子标的」，即 ETF 的本质）。
 *
 * 刻意区别于相邻图标：市场概览=方格、选股中心=漏斗、个股分析=折线。
 * ⚠️ 曾于 IA 重构中被**误删**（连同 ETF 中心的导航项），2026-10-01 恢复。
 * 详情页（/etf/:code）按 IA 评审结论**不设导航项**，从列表点击进入即可。
 */
function IconEtf({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 12.5 9 5 9-5" />
      <path d="m3 17 9 5 9-5" />
    </svg>
  );
}

/**
 * ETF 分析：蜡烛图（单标的行情分析）。
 *
 * 刻意与另两个"行情类"图标区分：个股分析=折线+箭头、ETF 中心=层叠篮子。
 * ⚠️ 与 `IconEtf` 是**两个不同的对象**（中心=一篮子列表，分析=单只标的），
 * 故不复用同一图标 —— 复用会让用户无法区分两个入口。
 */
function IconEtfAnalysis({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M7 2.5v3M7 18.5v3" />
      <rect x="4.5" y="5.5" width="5" height="13" rx="1" />
      <path d="M17 2.5v3.5M17 16.5v5" />
      <rect x="14.5" y="6" width="5" height="10.5" rx="1" />
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

/** 因子实验室：锥形瓶（区别于「策略研究」的漏斗，避免同图标复用） */
function IconFlask({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M9.5 3v6.2L4.8 17.4A2 2 0 0 0 6.6 20.4h10.8a2 2 0 0 0 1.8-3L14.5 9.2V3" />
      <path d="M8.5 3h7" />
      <path d="M7.2 14h9.6" />
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

/** 任务调度：日历钟（区别于「数据中心」的数据库柱） */
function IconSchedule({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
      <path d="M12 13v3l2 1.5" />
    </svg>
  );
}

/**
 * 组合回测（多资产）：三条并列的收益曲线 + 组内的方块标记。
 *
 * 刻意区别于「策略回测」的时钟箭头：策略回测回答"**单个**策略的历史表现"，
 * 组合回测回答"**多资产拼在一起**的整体表现" ⇒ 用"多条线并列"表达"多资产"。
 */
function IconPortfolio({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M3 19 8 13l3.5 3L21 6" />
      <path d="M3 19h18" />
      <rect x="15" y="3" width="6" height="6" rx="1" />
    </svg>
  );
}

/**
 * 容量与归因：容器 + 内部载重条（容量）与一条归因折线。
 *
 * 刻意区别于「执行与风控」的仪表盘：容量问的是"这套策略**最多能装多少钱**"，
 * 故用"容器/仓位被填满"的表达；归因则用容器内的一条曲线表示收益拆解。
 */
function IconCapacity({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M4 8h16v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" />
      <path d="M9 8V6a3 3 0 0 1 6 0v2" />
      <path d="M7.5 14.5 11 12l2.5 2 3-3" />
    </svg>
  );
}

/** 数据质量：带勾选的盾牌 */
function IconShield({ className }: IconProps) {
  return (
    <svg {...ICON_BASE} className={className}>
      <path d="M12 3 5 6v6c0 4.2 2.9 8 7 9 4.1-1 7-4.8 7-9V6l-7-3Z" />
      <path d="m9 12 2 2 4-4" />
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
  /** 该入口实际落到的页面（与 to 不同即为「整合入口」，用于 hover 提示） */  mergedNote?: string;
}

/** 导航分组：按「用户任务」组织，而非按「功能」罗列 */
interface NavGroup {
  /** 组标题（collapsed 时不显示） */
  title: string;
  items: NavItem[];
}

/**
 * 「ETF 分析」入口的默认落点标的。
 *
 * ⚠️ 这不是随手写的魔数，而是**产品声明的默认标的**（沪深300ETF，流动性最好的宽基）：
 * 与后端 `api/v1/etf.py::DEFAULT_PERF` 首位、`pages/Watchlist` 默认分组**保持一致**。
 * 改这里必须同步那两处，否则三处默认值会静默漂移。
 *
 * 注意语义边界：它只是**导航入口的落点**；`/etf/:code` 页面展示的仍是该标的的真实数据
 * （不是"写死样本冒充数据"——那种反模式见 `pages/Etf/index.tsx` 对 `DEFAULT_PERF` 的告警）。
 */
const DEFAULT_ETF_CODE = '510300';

/**
 * 导航结构（由 18 项平铺重组为 4 组 + 底部固定区）。
 *
 * 分组依据 = 用户任务流：
 *   盯盘      → 盘前预案 / 盘中盯盘的起点
 *   研究      → 盘后研究链路（选股 → 因子 → 回测）
 *   组合分析  → 回测的**下游**：由"单策略"走向"多资产组合"与"容量约束"
 *   数据资产  → 数据运维链路
 *
 * ⚠️ 关于「整合入口」的纪律（此前踩过坑，勿重蹈）：
 * 曾经把 `/portfolio`、`/capacity` 从侧栏移除、改用 `mergedNote` 提示
 * 「由相邻入口进入」，但**并未真的在任何页面长期做出那个入口**——结果这两页
 * 变成"导航降级 = 功能孤立"，用户只能手敲 URL 才到得了。
 * 追认历史：曾经补过一版真实入口（`Backtest` 页顶部「下游分析」按钮条），
 * 但那只是**临时修复**——把一级功能塞进另一个页面的页头，本身信息层级就不对。
 * 2026-10-01 二次改造：两者**升回独立一级入口**并单列一组，`Backtest` 页的
 * 「下游分析」按钮条**彻底删除**（不留重复入口）。
 * ⇒ **纪律不变：凡声明"由 X 进入"的，X 必须真的有链接指向它**；
 *    而现在无需任何"由 X 进入"的声明，因为它们自己就在导航里。
 */
const NAV_GROUPS: NavGroup[] = [
  {
    title: '盯盘',
    items: [
      { to: '/', label: '市场概览', Icon: IconOverview },
      { to: '/watchlist', label: '自选池', Icon: IconStar },
      { to: '/stock/600519.SH', label: '个股分析', Icon: IconStock, prefix: true },
      // ETF 分析紧跟「个股分析」——两者是**同一任务在两类标的上的对偶**
      // （都是"看单只标的现在怎么样"），放在同一组才符合用户心智。
      // 注意它与「ETF 中心」（研究组，列表页）**不在同一组**：这是刻意的——
      // 分析是盯盘动作，筛选/浏览是研究动作。
      // ⚠️ `DEFAULT_ETF_CODE` 是**产品声明的默认标的**，不是随手写的魔数：
      // 与后端 `api/v1/etf.py::DEFAULT_PERF` 首位、`Watchlist` 默认分组一致（沪深300ETF，
      // 流动性最好的宽基 ETF）。改这里须同步那两处，否则三处默认值会漂移。
      // 它只是**入口落点**，页面展示的仍是该 ETF 的真实数据（非写死样本）。
      // 高亮：`matchBase:'/etf/'` 覆盖所有 /etf/* 详情页，与「ETF 中心」（精确匹配 /etf）
      // 路径空间不重叠 ⇒ 任一时刻**恰好一个**高亮。
      { to: `/etf/${DEFAULT_ETF_CODE}`, label: 'ETF 分析', Icon: IconEtfAnalysis,
        prefix: true, matchBase: '/etf/' },
    ],
  },
  {
    title: '研究',
    items: [
      { to: '/screener', label: '选股中心', Icon: IconScreener },
      // ── ETF 中心（2026-10-01 恢复）────────────────────────────────────────
      // 背景：IA 重构时曾把 ETF 两个入口**一起删掉**，导致 /etf 与 /etf/:code
      // 变成"只能靠 g+e 快捷键或手敲 URL 到达"的孤岛（用户报障"怎么没有了"）。
      // 「ETF 分析」已归入盯盘组（与个股分析对偶）；此处保留**列表页**入口。
      { to: '/etf', label: 'ETF 中心', Icon: IconEtf },
      { to: '/studio', label: '因子实验室', Icon: IconFlask },
      // 策略研究（四象限研究台）与因子工作室是因子生命周期的两段，
      // 但页面级合并成本高（710 + 479 行），当前**保留为独立导航项**，
      // 不做"标签合并、底层没合并"的假整合。
      { to: '/research', label: '策略研究', Icon: IconResearch },
      // 策略回测：只管"**单个**策略"的回测（趋势跟踪 / Top-K / 信号分析）。
      // 它的下游（多资产组合、容量约束）已独立成「组合分析」组，**不在本页内**。
      { to: '/backtest', label: '策略回测', Icon: IconBacktest },
      // ⚠️ 「执行与风控」必须留在导航里：它是 `/desk` 的**唯一显眼入口**
      // （另有 `StockDetail` 的"→ 去下单"断点续接，属子链路而非入口）。
      // 2026-10-01 移动组合回测/容量归因时**曾误删本项**，`tsc` 以
      // `TS6133 'IconGauge' is declared but its value is never read` 报出
      // —— 这正是"删导航项会静默产生孤儿页面"的可检测信号。
      // ⇒ 移动/删除导航项后**必跑 `tsc`**：未使用图标报错 = 入口被误删的告警。
      { to: '/desk', label: '执行与风控', Icon: IconGauge },
    ],
  },
  {
    // ── 组合分析（2026-10-01 新建）─────────────────────────────────────────
    // 为什么单列一组、而不并进「研究」：
    //   ① 语义上二者是**回测的下游**，不是"研究"的一环（前者产出组合权重与容量结论，
    //      后者的产物是信号与因子）——并进研究会把"研究"变回杂物筐；
    //   ② 「研究」组当前已有 5 项，再加 2 项变 7 项，层级会重新糊掉；
    //   ③ 单列一组后，两个入口的**信息层级**与其功能量级匹配（都是一级功能）。
    // ⚠️ 本组 2 项**没有子路由**（`/portfolio`、`/capacity` 均为叶子页），
    //    不构成 `check-nav-integrity.mjs` 规则 1 意义上的"索引页"，但入口必须存在——
    //    否则立刻退化成规则 2 的孤儿路由。
    // ⚠️ 高亮口径：两者用**精确匹配**（不加 `prefix`）——它们的路径空间与
    //    `/backtest` 不重叠，任一 URL 上恰好一个高亮。
    title: '组合分析',
    items: [
      { to: '/portfolio', label: '组合回测（多资产）', Icon: IconPortfolio },
      { to: '/capacity', label: '容量与归因', Icon: IconCapacity },
    ],
  },
  {
    title: '数据资产',
    items: [
      // 数据中心已内嵌「数据质量与缺漏」「数据任务统计」监控卡片；
      // 独立的质量扫描页与调度页仍保留各自入口（页面级合并未做，不假装已做）。
      { to: '/data', label: '数据中心', Icon: IconDatabase },
      { to: '/dataquality', label: '数据质量', Icon: IconShield },
      { to: '/pipeline', label: '任务调度', Icon: IconSchedule },
      { to: '/alerts', label: '预警中心', Icon: IconBell },
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
  if (item.prefix) {
    // 前缀匹配：覆盖「详情/子页面」而不与同级入口争抢高亮。
    //   ① `/stock/:symbol`（个股分析）——`to` 即一个具体标的，覆盖所有 /stock/*；
    //   ② `/etf/510300`（ETF 分析）——`matchBase:'/etf/'` 覆盖所有 /etf/*，
    //      与「ETF 中心」（`to:'/etf'`，**精确匹配**）路径空间不重叠 ⇒ 恰好一个高亮。
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
