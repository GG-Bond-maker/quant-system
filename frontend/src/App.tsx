import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import AuthBootstrap from './components/AuthBootstrap';
import HotkeyHelp from './components/HotkeyHelp';
import { RequireRole } from './components/RequireAuth';
import { useHotkeys } from './hooks/useHotkeys';
import { useAuthStore } from './stores/useAuthStore';
import { useUiStore } from './stores/useUiStore';
import Login from './pages/Login';

/**
 * 路由懒加载（L4，Sprint3）：Login 保持 eager（登录入口极小），其余 17 页
 * 全部 React.lazy 分包——ECharts 与重页面不再进入首屏 JS（原单 chunk
 * 1.63MB 的主因），页面 chunk 首次导航按需加载，二次导航命中浏览器缓存。
 * 首页懒加载的 LCP 代价由 PageSkeleton 骨架兜底（本地网络 <100ms 量级）。
 */
const MarketOverview = lazy(() => import('./pages/MarketOverview'));
const Alerts = lazy(() => import('./pages/Alerts'));
const Report = lazy(() => import('./pages/Report'));
const StockDetail = lazy(() => import('./pages/StockDetail'));
const Screener = lazy(() => import('./pages/Screener'));
const EtfCenter = lazy(() => import('./pages/Etf'));
const EtfDetail = lazy(() => import('./pages/EtfDetail'));
const Backtest = lazy(() => import('./pages/Backtest'));
const Portfolio = lazy(() => import('./pages/Portfolio'));
const Research = lazy(() => import('./pages/Research'));
const FactorStudio = lazy(() => import('./pages/FactorStudio'));
const DataQuality = lazy(() => import('./pages/DataQuality'));
const OrderDesk = lazy(() => import('./pages/OrderDesk'));
const Pipeline = lazy(() => import('./pages/Pipeline'));
const CapacityAttribution = lazy(() => import('./pages/CapacityAttribution'));
const DataCenter = lazy(() => import('./pages/DataCenter'));
const Watchlist = lazy(() => import('./pages/Watchlist'));
const Settings = lazy(() => import('./pages/Settings'));

/** 页面级骨架（与卡片风格一致的轻量占位，替代白屏等待） */
function PageSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="页面加载中">
      <div className="h-7 w-40 animate-pulse rounded skeleton" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="h-16 animate-pulse rounded-lg border border-hair bg-surface" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="h-64 animate-pulse rounded-lg border border-hair bg-surface" />
        <div className="h-64 animate-pulse rounded-lg border border-hair bg-surface" />
      </div>
    </div>
  );
}

function NotFound() {
  return <div className="py-24 text-center text-sm text-ink-muted">404 Not Found</div>;
}

/** lazy 页面的统一 Suspense 包裹（骨架占位 + 保持滚动位置由路由自然处理） */
function Page({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<PageSkeleton />}>{children}</Suspense>;
}

/**
 * 主区容器：信息型页面居中限宽（避免超宽屏单行过长），
 * 行情型页面（K 线 / 下单）允许铺满整屏——铺满对表格是灾难，对图表是优点。
 */
function MainContent({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  // 行情型页面：个股详情（K线）、执行中心（下单+监控）
  const fullWidth = pathname.startsWith('/stock/') || pathname.startsWith('/desk');
  return (
    <main className="min-h-0 flex-1 overflow-y-auto px-4 py-4 lg:px-5">
      <div className={`mx-auto w-full ${fullWidth ? 'max-w-chart' : 'max-w-content'}`}>
        {children}
      </div>
    </main>
  );
}

export default function App() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { sidebarCollapsed, toggleSidebar, lastPath, setLastPath } = useUiStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [helpOpen, setHelpOpen] = useState(false);
  /** 会话恢复只做一次（首帧），避免后续导航被反复重定向 */
  const restoredRef = useRef(false);

  // 登录页独立渲染（不带侧边栏与顶栏）
  const isLogin = pathname === '/login';

  /* ---------- 会话恢复：首次挂载回到上次所在页面 ---------- */
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    // 仅当：本次进入的是首页（`/`）+ 有历史路径 + 已登录（避免把匿名访客弹进受限页）
    if (pathname === '/' && lastPath && lastPath !== '/' && isAuthenticated()) {
      navigate(lastPath, { replace: true });
    }
  }, [pathname, lastPath, isAuthenticated, navigate]);

  /* ---------- 记录当前路径（用于下一次会话恢复） ---------- */
  useEffect(() => {
    if (!isLogin) setLastPath(pathname);
  }, [pathname, isLogin, setLastPath]);

  /* ---------- 全局快捷键 ---------- */
  useHotkeys({
    // `/` 聚焦顶部搜索框（最高频入口）
    onSearch: () => {
      const el = document.querySelector<HTMLInputElement>('input[role="combobox"]');
      el?.focus();
      el?.select();
      setHelpOpen(false);
    },
    // `?` 打开快捷键帮助
    onHelp: () => setHelpOpen((v) => !v),
    // `Esc` 关闭帮助浮层（各页面自身的 Esc 行为不受影响）
    onEscape: () => setHelpOpen(false),
    // `g` + 字母：两段式跳页（GitHub 范式，避免与浏览器单键冲突）
    goto: {
      d: '/',                    // dashboard 市场概览
      w: '/watchlist',           // 自选池
      p: '/portfolio',           // portfolio 组合回测（多资产）
      k: '/capacity',            // kapazität→capacity 容量与归因（p 已被 portfolio 占用）
      o: '/desk',                // order 执行中心
      b: '/backtest',            // backtest 策略回测
      s: '/screener',            // screener 选股中心
      a: '/alerts',              // alerts 预警中心
      r: '/research',            // research 策略研究
      f: '/studio',              // factor 因子工作室
      e: '/etf',                 // ETF 中心
      q: '/dataquality',         // quality 数据质量
      t: '/pipeline',            // tasks 任务调度
      c: '/data',                // 数据中心
    },
    onNavigate: (to) => { setHelpOpen(false); navigate(to); },
  });

  if (isLogin) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      {/* 启动即校验本地 JWT（/auth/me）并预载偏好（refresh_freq），过期则清会话 */}
      <AuthBootstrap />
      <Sidebar collapsed={sidebarCollapsed} onToggle={toggleSidebar} />

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <MainContent>
          <Routes>
            {/* 市场概览是唯一公开业务页；/market 保留为公开兼容别名。 */}
            <Route path="/" element={<Page><MarketOverview /></Page>} />
            <Route path="/market" element={<Page><MarketOverview /></Page>} />

            {/* 已登录即可读取的个人/行情功能统一声明 viewer 门槛。 */}
            <Route path="/report" element={<RequireRole minimum="viewer"><Page><Report /></Page></RequireRole>} />
            <Route path="/stock/:symbol" element={<RequireRole minimum="viewer"><Page><StockDetail /></Page></RequireRole>} />
            <Route path="/screener" element={<RequireRole minimum="viewer"><Page><Screener /></Page></RequireRole>} />
            <Route path="/etf" element={<RequireRole minimum="viewer"><Page><EtfCenter /></Page></RequireRole>} />
            <Route path="/etf/:code" element={<RequireRole minimum="viewer"><Page><EtfDetail /></Page></RequireRole>} />
            <Route path="/portfolio" element={<RequireRole minimum="viewer"><Page><Portfolio /></Page></RequireRole>} />
            <Route path="/data" element={<RequireRole minimum="viewer"><Page><DataCenter /></Page></RequireRole>} />
            <Route path="/watchlist" element={<RequireRole minimum="viewer"><Page><Watchlist /></Page></RequireRole>} />
            <Route path="/settings" element={<RequireRole minimum="viewer"><Page><Settings /></Page></RequireRole>} />

            {/* 研究计算、任务写入及预警均须 researcher；低角色保留会话并展示权限说明。 */}
            <Route path="/backtest" element={<RequireRole><Page><Backtest /></Page></RequireRole>} />
            <Route path="/research" element={<RequireRole><Page><Research /></Page></RequireRole>} />
            <Route path="/alerts" element={<RequireRole><Page><Alerts /></Page></RequireRole>} />
            <Route path="/studio" element={<RequireRole><Page><FactorStudio /></Page></RequireRole>} />
            <Route path="/dataquality" element={<RequireRole><Page><DataQuality /></Page></RequireRole>} />
            <Route path="/desk" element={<RequireRole><Page><OrderDesk /></Page></RequireRole>} />
            <Route path="/pipeline" element={<RequireRole><Page><Pipeline /></Page></RequireRole>} />
            <Route path="/capacity" element={<RequireRole><Page><CapacityAttribution /></Page></RequireRole>} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </MainContent>

        <footer className="shrink-0 border-t border-hair bg-surface py-2 text-center text-2xs text-ink-muted">
          AQP · 仅用于学习研究，不构成任何投资建议 · 市场有风险，投资需谨慎
        </footer>
      </div>

      {/* 全局快捷键帮助浮层（? 切换） */}
      <HotkeyHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
