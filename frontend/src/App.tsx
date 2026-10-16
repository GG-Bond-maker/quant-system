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
const InstrumentsHub = lazy(() => import('./pages/Instruments'));
const OrderDesk = lazy(() => import('./pages/OrderDesk'));
const Watchlist = lazy(() => import('./pages/Watchlist'));
const Settings = lazy(() => import('./pages/Settings'));
const ResearchHub = lazy(() => import('./pages/ResearchHub'));
const BacktestHub = lazy(() => import('./pages/BacktestHub'));
const DataHub = lazy(() => import('./pages/DataHub'));
const AnalysisHub = lazy(() => import('./pages/AnalysisHub'));

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
 *
 * 🔴 高度链纪律（P0 修复，2026-10-02）——**内层包装 div 必须带 `h-full`**：
 *
 *    div.flex.h-screen（定高）→ div.flex-1.flex-col（定高）→ main.flex-1（定高）
 *      → **div.h-full（定高）** → 页面根（`min-h-full` / `flex-1` 才有意义）
 *
 * 原实现内层 div 只有 `w-full`（`height: auto`）⇒ 页面根的 `min-h-full`
 * 按 CSS 2.1 §10.7「百分比高度对 auto 高父元素解析为 auto」**退化为 0**，
 * 于是 `flex-1` 的栅格拿不到可分配自由空间、主轴塌陷；卡片按自然高度溢出后
 * 压盖后续兄弟（ETF 中心实测：ECharts canvas 是定位元素，绘制在下方
 * 「ETF 列表」卡片之上，又被表格 sticky 表头的 `z-index:10` 反向切断）。
 *
 * ⚠️ `h-full` 与 `min-h-full` 的分工：包装层用 `h-full`（**定高**，为子元素
 * 提供百分比解析基准）；页面根用 `min-h-full`（至少撑满，内容更高时正常增长，
 * 由 main 的 `overflow-y-auto` 承接滚动）。两者不可互换——包装层若也用
 * `min-h-full`，其 `height` 仍是 auto，页面根的百分比会**再次退化为 0**。
 */
export function MainContent({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  // 行情型页面：个股/ETF/期货深度行情（K线+指标）、交易大厅（下单+盘口+监控）
  const fullWidth =
    pathname.startsWith('/stock/') ||
    pathname.startsWith('/desk') ||
    pathname.startsWith('/trading') ||
    pathname.startsWith('/etf/') ||
    pathname.startsWith('/futures/analysis');
  return (
    // data-scroll-root：浮层（Modal / Drawer）滚动锁定的唯一锚点。
    // 外壳是 `h-screen overflow-hidden`（见本文件根 div）⇒ document.body 从不滚动，
    // 照老注释去锁 body 是一行永不生效的死代码。锁定必须落在**这个**元素上。
    <main data-scroll-root className="min-h-0 flex-1 overflow-y-auto px-4 py-4 lg:px-5">
      <div className={`mx-auto h-full w-full ${fullWidth ? 'max-w-chart' : 'max-w-content'}`}>
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
      u: '/futures',             // futures 期货中心
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
            {/* 深度行情：多资产单标的深度画像与分析（个股/ETF/期货），支持顶部 Tab 随时切换 */}
            <Route path="/stock/:symbol" element={<RequireRole minimum="viewer"><Page><AnalysisHub initialTab="stock" /></Page></RequireRole>} />
            <Route path="/etf/:code" element={<RequireRole minimum="viewer"><Page><AnalysisHub initialTab="etf" /></Page></RequireRole>} />
            <Route path="/futures/analysis" element={<RequireRole><Page><AnalysisHub initialTab="futures" /></Page></RequireRole>} />

            {/* 标的中心：多资产统一选品工作台（股票/ETF/期货），各资产入口直达对应 Tab */}
            <Route path="/screener" element={<RequireRole minimum="viewer"><Page><InstrumentsHub initialTab="stock" /></Page></RequireRole>} />
            <Route path="/etf" element={<RequireRole minimum="viewer"><Page><InstrumentsHub initialTab="etf" /></Page></RequireRole>} />
            <Route path="/futures" element={<RequireRole minimum="viewer"><Page><InstrumentsHub initialTab="futures" /></Page></RequireRole>} />

            {/* 数据工程中枢：聚合数据资产、质量巡检与调度流水线 */}
            <Route path="/data" element={<RequireRole minimum="viewer"><Page><DataHub initialTab="data" /></Page></RequireRole>} />
            <Route path="/dataquality" element={<RequireRole><Page><DataHub initialTab="dataquality" /></Page></RequireRole>} />
            <Route path="/pipeline" element={<RequireRole><Page><DataHub initialTab="pipeline" /></Page></RequireRole>} />

            {/* 策略研发中枢：聚合策略工作台与因子实验室 */}
            <Route path="/research" element={<RequireRole><Page><ResearchHub initialTab="research" /></Page></RequireRole>} />
            <Route path="/studio" element={<RequireRole><Page><ResearchHub initialTab="studio" /></Page></RequireRole>} />

            {/* 回测与组合中枢：聚合单策略回测、多资产组合回测与容量归因 */}
            <Route path="/backtest" element={<RequireRole><Page><BacktestHub initialTab="backtest" /></Page></RequireRole>} />
            <Route path="/portfolio" element={<RequireRole minimum="viewer"><Page><BacktestHub initialTab="portfolio" /></Page></RequireRole>} />
            <Route path="/capacity" element={<RequireRole><Page><BacktestHub initialTab="capacity" /></Page></RequireRole>} />

            {/* 盯盘与交易执行 */}
            <Route path="/watchlist" element={<RequireRole minimum="viewer"><Page><Watchlist /></Page></RequireRole>} />
            <Route path="/desk" element={<RequireRole><Page><OrderDesk /></Page></RequireRole>} />
            <Route path="/trading" element={<RequireRole><Page><OrderDesk /></Page></RequireRole>} />
            <Route path="/alerts" element={<RequireRole><Page><Alerts /></Page></RequireRole>} />
            <Route path="/settings" element={<RequireRole minimum="viewer"><Page><Settings /></Page></RequireRole>} />
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
