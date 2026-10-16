/**
 * 策略研发中心（Alpha & Strategy Research Hub）。
 *
 * 聚合「策略工作台 (四象限策略研究)」与「因子实验室 (Factor Studio)」，
 * 提供统一的量化 Alpha 研发工作台，支持顶部标签一键切换：
 * - 策略工作台：四象限策略模型研究、特征工程、买卖信号生成与多模型对比
 * - 因子实验室：单因子深度分析、IC/IR 时序衰减、分层回测收益与因子正交去噪
 */
import { useLocation, useNavigate, useSearchParams, Link } from 'react-router-dom';
import Research from '@/pages/Research';
import FactorStudio from '@/pages/FactorStudio';

export type ResearchTab = 'research' | 'studio';

interface ResearchHubProps {
  initialTab?: ResearchTab;
}

export default function ResearchHub({ initialTab }: ResearchHubProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // 计算当前激活的子界面：优先根据 URL 路径，其次根据 ?tab= 参数，最后根据 initialTab
  const activeTab: ResearchTab = (() => {
    const p = location.pathname.toLowerCase();
    if (p.startsWith('/studio')) return 'studio';
    if (p.startsWith('/research')) {
      const qTab = searchParams.get('tab')?.toLowerCase();
      if (qTab === 'studio') return 'studio';
      if (qTab === 'research') return 'research';
    }
    return initialTab ?? 'research';
  })();

  const handleSwitchTab = (tab: ResearchTab) => {
    if (tab === 'research') navigate('/research');
    else if (tab === 'studio') navigate('/studio');
  };

  return (
    <div className="space-y-4">
      {/* 顶部策略研发中心切换栏 */}
      <div className="rounded-xl border border-hair bg-surface px-4 py-2.5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 pr-2 border-r border-hair">
              <span className="text-sm font-bold tracking-tight text-ink">策略研发</span>
              <span className="hidden rounded bg-surface-sunken px-1.5 py-0.5 text-3xs font-medium text-ink-muted sm:inline">
                Alpha 投研工坊
              </span>
            </div>

            {/* 子功能 Tab 切换按钮 */}
            <div className="flex items-center rounded-lg bg-surface-sunken p-1 text-xs font-medium" role="tablist" aria-label="策略研发模块切换">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'research'}
                onClick={() => handleSwitchTab('research')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'research'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M4 4h16l-6 7v7l-4 2v-9L4 4Z" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M4 20h16" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>策略工作台</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'research' ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  四象限 · 信号
                </span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'studio'}
                onClick={() => handleSwitchTab('studio')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'studio'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M9.5 3v6.2L4.8 17.4A2 2 0 0 0 6.6 20.4h10.8a2 2 0 0 0 1.8-3L14.5 9.2V3" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M8.5 3h7" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M7.2 14h9.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>因子实验室</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'studio' ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  IC/IR · 正交去噪
                </span>
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 text-2xs text-ink-muted">
            <span className="hidden sm:inline">研发上下游联动：</span>
            <Link
              to="/screener"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="前往标的中心"
            >
              ← 标的中心
            </Link>
            <span className="text-ink-muted/40">·</span>
            <Link
              to="/backtest"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="前往回测与组合中心"
            >
              回测与组合 →
            </Link>
          </div>
        </div>
      </div>

      {/* 对应界面的完整内容 */}
      <div className="min-h-0">
        {activeTab === 'research' && <Research />}
        {activeTab === 'studio' && <FactorStudio />}
      </div>
    </div>
  );
}
