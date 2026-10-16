/**
 * 深度行情 / 单标的微观画像中心（Multi-Asset Analysis Hub）。
 *
 * 聚合「个股分析」、「ETF分析」与「期货分析」，
 * 提供单标的深度多维度量化剖析与微观行情工作台，支持顶部标签一键切换：
 * - 个股分析：A股 / 港股通 K线技术指标、AI收益预测、F10财务健康度、资金流向与筹码分布
 * - ETF分析：宽基/行业/跨境 ETF 走势、重仓持仓跟踪、跟踪误差分解与折溢价走势
 * - 期货分析：内盘主力合约微观量价与持仓量(OI)、CTA动量策略回测、期限结构与敏感性热力图
 */
import { useLocation, useNavigate, useSearchParams, Link } from 'react-router-dom';
import StockDetail from '@/pages/StockDetail';
import EtfDetail from '@/pages/EtfDetail';
import FuturesAnalysis from '@/pages/FuturesAnalysis';
import { useUiStore } from '@/stores/useUiStore';

export type AnalysisTab = 'stock' | 'etf' | 'futures';

interface AnalysisHubProps {
  initialTab?: AnalysisTab;
}

export default function AnalysisHub({ initialTab }: AnalysisHubProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const currentSymbol = useUiStore((s) => s.currentSymbol);

  // 计算当前激活的子界面：优先根据 URL 路径，其次根据 ?tab= 参数，最后根据 initialTab
  const activeTab: AnalysisTab = (() => {
    const p = location.pathname.toLowerCase();
    if (p.startsWith('/etf')) return 'etf';
    if (p.startsWith('/futures')) return 'futures';
    if (p.startsWith('/stock')) return 'stock';
    const qTab = searchParams.get('tab')?.toLowerCase();
    if (qTab === 'etf') return 'etf';
    if (qTab === 'futures') return 'futures';
    if (qTab === 'stock') return 'stock';
    return initialTab ?? 'stock';
  })();

  const handleSwitchTab = (tab: AnalysisTab) => {
    if (tab === 'stock') {
      const sym = currentSymbol || '600519.SH';
      navigate(`/stock/${sym}`);
    } else if (tab === 'etf') {
      navigate('/etf/510300');
    } else if (tab === 'futures') {
      navigate('/futures/analysis');
    }
  };

  return (
    <div className="space-y-4">
      {/* 顶部单标的微观画像类型切换栏 */}
      <div className="rounded-xl border border-hair bg-surface px-4 py-2.5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* 左侧：深度行情标识与主标签切换组 */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 pr-2 border-r border-hair">
              <span className="text-sm font-bold tracking-tight text-ink">深度行情</span>
              <span className="hidden rounded bg-surface-sunken px-1.5 py-0.5 text-3xs font-medium text-ink-muted sm:inline">
                单标的微观画像
              </span>
            </div>

            {/* 三大资产分析 Tab 切换按钮 */}
            <div className="flex items-center rounded-lg bg-surface-sunken p-1 text-xs font-medium" role="tablist" aria-label="单标的行情分析类型切换">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'stock'}
                onClick={() => handleSwitchTab('stock')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'stock'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M3 17.5 9 11l4 4 8-8.5" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M15 6h6v6" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M3 21h18" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>个股分析</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'stock' ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  A股 · 港股通
                </span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'etf'}
                onClick={() => handleSwitchTab('etf')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'etf'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M7 2.5v3M7 18.5v3" strokeLinecap="round" strokeLinejoin="round" />
                  <rect x="4.5" y="5.5" width="5" height="13" rx="1" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M17 2.5v3.5M17 16.5v5" strokeLinecap="round" strokeLinejoin="round" />
                  <rect x="14.5" y="6" width="5" height="10.5" rx="1" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>ETF分析</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'etf' ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  持仓 · 跟踪误差
                </span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'futures'}
                onClick={() => handleSwitchTab('futures')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'futures'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="m3 9 4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M7 5v14" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="m21 15-4 4-4-4" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M17 19V5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>期货分析</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'futures' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  主力 · CTA回测
                </span>
              </button>
            </div>
          </div>

          {/* 右侧：上游标的中心与自选联动 */}
          <div className="flex items-center gap-2 text-2xs text-ink-muted">
            <span className="hidden sm:inline">选品上游联动：</span>
            <Link
              to="/screener"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="前往标的中心进行多资产选品"
            >
              ← 标的中心
            </Link>
            <span className="text-ink-muted/40">·</span>
            <Link
              to="/watchlist"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="查看自选池"
            >
              自选池 →
            </Link>
          </div>
        </div>
      </div>

      {/* 对应子界面的完整渲染 */}
      <div className="min-h-0">
        {activeTab === 'stock' && <StockDetail />}
        {activeTab === 'etf' && <EtfDetail />}
        {activeTab === 'futures' && <FuturesAnalysis />}
      </div>
    </div>
  );
}
