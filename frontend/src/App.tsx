import { lazy, Suspense, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import AuthBootstrap from './components/AuthBootstrap';
import RequireAuth, { RequireRole } from './components/RequireAuth';
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

/** 页面级骨架（与暗色主题/卡片风格一致的轻量占位，替代白屏等待） */
function PageSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="页面加载中">
      <div className="h-7 w-40 animate-pulse rounded bg-slate-100" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="h-16 animate-pulse rounded-lg border border-hair bg-white" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="h-64 animate-pulse rounded-lg border border-hair bg-white" />
        <div className="h-64 animate-pulse rounded-lg border border-hair bg-white" />
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

export default function App() {
  const [collapsed, setCollapsed] = useState(false);
  const { pathname } = useLocation();

  // 登录页独立渲染（不带侧边栏与顶栏）
  if (pathname === '/login') {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <div className="flex min-h-screen bg-surface">
      {/* 启动即校验本地 JWT（/auth/me）并预载偏好（refresh_freq），过期则清会话 */}
      <AuthBootstrap />
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        {/* 去掉 max-w-terminal 限制，内容铺满整屏宽度（用户要求不留大片空白） */}
        <main className="w-full flex-1 px-4 py-4 lg:px-5">
          <Routes>
            <Route path="/" element={<Page><MarketOverview /></Page>} />
            <Route path="/report" element={<Page><Report /></Page>} />
            <Route path="/stock/:symbol" element={<Page><StockDetail /></Page>} />
            <Route path="/screener" element={<Page><Screener /></Page>} />
            <Route path="/etf" element={<Page><EtfCenter /></Page>} />
            <Route path="/etf/:code" element={<Page><EtfDetail /></Page>} />
            <Route path="/backtest" element={<Page><Backtest /></Page>} />
            <Route path="/portfolio" element={<Page><Portfolio /></Page>} />
            <Route path="/research" element={<Page><Research /></Page>} />
            <Route path="/studio" element={<RequireRole><Page><FactorStudio /></Page></RequireRole>} />
            {/* /dataquality 的两个后端接口 ops/quality-scan(POST) 与 ops/lineage(GET)
                均需 researcher：匿名/低角色访问本就 40100，故按同级角色守卫，
                避免"血缘要登录、扫描永远报错"的半可用页面 */}
            <Route path="/dataquality" element={<RequireRole><Page><DataQuality /></Page></RequireRole>} />
            <Route path="/desk" element={<RequireRole><Page><OrderDesk /></Page></RequireRole>} />
            <Route path="/pipeline" element={<RequireRole><Page><Pipeline /></Page></RequireRole>} />
            {/* /capacity 的 desk/capacity(GET) 已需 researcher（strategy 容量含 ADV/资金规模），
                与 OrderDesk 同级，故加守卫保持一致 */}
            <Route path="/capacity" element={<RequireRole><Page><CapacityAttribution /></Page></RequireRole>} />
            <Route path="/data" element={<Page><DataCenter /></Page>} />
            <Route path="/watchlist" element={<Page><Watchlist /></Page>} />
            <Route path="/alerts" element={<Page><Alerts /></Page>} />
            {/* 系统设置的后端接口全部 require_role 保护，未登录时跳登录页 */}
            <Route path="/settings"
              element={<RequireAuth><Page><Settings /></Page></RequireAuth>} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>

        <footer className="border-t border-hair py-3 text-center text-2xs text-ink-muted">
          AQP · 仅用于学习研究，不构成任何投资建议 · 市场有风险，投资需谨慎
        </footer>
      </div>
    </div>
  );
}
