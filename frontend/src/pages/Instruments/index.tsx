/**
 * 标的中心（Multi-Asset Instruments Hub）。
 *
 * 聚合「选股中心 (股票)」、「ETF 中心」与「期货中心」，
 * 提供统一的多资产行情选品工作台，支持顶部标签一键切换：
 * - 股票选股：A股全市场 / 港股通 多因子选股与板块联动
 * - ETF 中心：境内 / 跨境 多资产 ETF 榜单、套利雷达与行业分布
 * - 期货中心：股指 / 国债 / 大宗商品 内盘主力合约看板与期限结构
 */
import { useLocation, useNavigate, useSearchParams, Link } from 'react-router-dom';
import Screener from '@/pages/Screener';
import EtfCenter from '@/pages/Etf';
import FuturesCenter from '@/pages/FuturesCenter';

export type InstrumentTab = 'stock' | 'etf' | 'futures';

interface InstrumentsHubProps {
  initialTab?: InstrumentTab;
}

export default function InstrumentsHub({ initialTab }: InstrumentsHubProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // 计算当前激活的子界面：优先根据 URL 路径，其次根据 ?tab= 参数，最后根据 initialTab
  const activeTab: InstrumentTab = (() => {
    const p = location.pathname.toLowerCase();
    if (p.startsWith('/etf')) return 'etf';
    if (p.startsWith('/futures')) return 'futures';
    if (p.startsWith('/screener') || p.startsWith('/instruments')) {
      const qTab = searchParams.get('tab')?.toLowerCase();
      if (qTab === 'etf') return 'etf';
      if (qTab === 'futures') return 'futures';
      if (qTab === 'stock') return 'stock';
    }
    return initialTab ?? 'stock';
  })();

  const handleSwitchTab = (tab: InstrumentTab) => {
    if (tab === 'stock') navigate('/screener');
    else if (tab === 'etf') navigate('/etf');
    else if (tab === 'futures') navigate('/futures');
  };

  return (
    <div className="space-y-4">
      {/* 顶部资产类型全景切换栏（位于页面顶部，随页面自然滚动） */}
      <div className="rounded-xl border border-surface-border bg-surface px-4 py-2.5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* 左侧：标的中心标识与主标签切换组 */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 pr-2 border-r border-hair">
              <span className="text-sm font-bold tracking-tight text-ink">标的中心</span>
              <span className="hidden rounded bg-surface-sunken px-1.5 py-0.5 text-3xs font-medium text-ink-muted sm:inline">
                多资产选品
              </span>
            </div>

            {/* 三大资产类别切换按钮 */}
            <div className="flex items-center rounded-lg bg-surface-sunken p-1 text-xs font-medium" role="tablist" aria-label="资产类别切换">
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
                  <path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>选股中心</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'stock' ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  A股 / 港股通
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
                  <path d="m12 3 9 5-9 5-9-5 9-5Z" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="m3 12.5 9 5 9-5" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="m3 17 9 5 9-5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>ETF 中心</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'etf' ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  境内 / 跨境
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
                  <path d="M4 19h16" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="m4 15 6-6 4 4 6-8" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M16 5h4v4" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M4 8l4 4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>期货中心</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'futures' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  内盘主力 / 衍生品
                </span>
              </button>
            </div>
          </div>

          {/* 右侧：单标的深度分析直通入口 */}
          <div className="flex items-center gap-2 text-2xs text-ink-muted">
            <span className="hidden sm:inline">深度分析直达：</span>
            <Link
              to="/stock/600519.SH"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="跳转至个股深度分析页面"
            >
              个股分析 →
            </Link>
            <span className="text-ink-muted/40">·</span>
            <Link
              to="/etf/510300"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="跳转至 ETF 深度分析页面"
            >
              ETF分析 →
            </Link>
            <span className="text-ink-muted/40">·</span>
            <Link
              to="/futures/analysis"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="跳转至期货品种深度分析页面"
            >
              期货分析 →
            </Link>
          </div>
        </div>
      </div>

      {/* 对应资产类别的完整界面内容 */}
      <div className="min-h-0">
        {activeTab === 'stock' && <Screener />}
        {activeTab === 'etf' && <EtfCenter />}
        {activeTab === 'futures' && <FuturesCenter />}
      </div>
    </div>
  );
}
