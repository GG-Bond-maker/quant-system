/**
 * 回测与组合中心（Backtest & Portfolio Management Hub）。
 *
 * 聚合「策略回测 (单策略)」、「组合回测 (多资产)」与「容量与归因」，
 * 提供从单策略历史检验、资产组合配置优化到实盘容量压力测试的完整量化验证闭环：
 * - 策略回测：单策略多周期历史回测、网格寻优、Walk-Forward 走步分析、蒙特卡洛模拟
 * - 组合回测：多资产组合回测、Markowitz 有效前沿求解、Black-Litterman 模型、调仓漂移跟踪
 * - 容量与归因：AUM 资金容量瓶颈、Almgren-Chriss 流动性冲击曲面、Menchero-Brinson 业绩与 Barra 风险归因
 */
import { useLocation, useNavigate, useSearchParams, Link } from 'react-router-dom';
import Backtest from '@/pages/Backtest';
import Portfolio from '@/pages/Portfolio';
import CapacityAttribution from '@/pages/CapacityAttribution';

export type BacktestTab = 'backtest' | 'portfolio' | 'capacity';

interface BacktestHubProps {
  initialTab?: BacktestTab;
}

export default function BacktestHub({ initialTab }: BacktestHubProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // 计算当前激活的子界面：优先根据 URL 路径，其次根据 ?tab= 参数，最后根据 initialTab
  const activeTab: BacktestTab = (() => {
    const p = location.pathname.toLowerCase();
    if (p.startsWith('/portfolio')) return 'portfolio';
    if (p.startsWith('/capacity')) return 'capacity';
    if (p.startsWith('/backtest')) {
      const qTab = searchParams.get('tab')?.toLowerCase();
      if (qTab === 'portfolio') return 'portfolio';
      if (qTab === 'capacity') return 'capacity';
      if (qTab === 'backtest') return 'backtest';
    }
    return initialTab ?? 'backtest';
  })();

  const handleSwitchTab = (tab: BacktestTab) => {
    if (tab === 'backtest') navigate('/backtest');
    else if (tab === 'portfolio') navigate('/portfolio');
    else if (tab === 'capacity') navigate('/capacity');
  };

  return (
    <div className="space-y-4">
      {/* 顶部回测与组合管理中心切换栏 */}
      <div className="rounded-xl border border-hair bg-surface px-4 py-2.5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* 左侧：模块标题与 Tab 组 */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 pr-2 border-r border-hair">
              <span className="text-sm font-bold tracking-tight text-ink">回测与组合</span>
              <span className="hidden rounded bg-surface-sunken px-1.5 py-0.5 text-3xs font-medium text-ink-muted sm:inline">
                量化验证与组合配置
              </span>
            </div>

            {/* 三大子模块 Tab 切换按钮 */}
            <div className="flex items-center rounded-lg bg-surface-sunken p-1 text-xs font-medium" role="tablist" aria-label="回测与组合模块切换">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'backtest'}
                onClick={() => handleSwitchTab('backtest')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'backtest'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M12 3a9 9 0 1 0 9 9" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M12 7v5l4 2" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M17 3v4h4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>策略回测</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'backtest' ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  单策略 · 蒙特卡洛
                </span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'portfolio'}
                onClick={() => handleSwitchTab('portfolio')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'portfolio'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M3 19 8 13l3.5 3L21 6" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M3 19h18" strokeLinecap="round" strokeLinejoin="round" />
                  <rect x="15" y="3" width="6" height="6" rx="1" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>组合回测</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'portfolio' ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  多资产 · 有效前沿
                </span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'capacity'}
                onClick={() => handleSwitchTab('capacity')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'capacity'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M4 8h16v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M9 8V6a3 3 0 0 1 6 0v2" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M7.5 14.5 11 12l2.5 2 3-3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>容量与归因</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'capacity' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  AUM容量 · Brinson/Barra
                </span>
              </button>
            </div>
          </div>

          {/* 右侧：快速直达策略研发 */}
          <div className="flex items-center gap-2 text-2xs text-ink-muted">
            <span className="hidden sm:inline">研发上游联动：</span>
            <Link
              to="/research"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="前往策略研发中心"
            >
              ← 策略研发
            </Link>
            <span className="text-ink-muted/40">·</span>
            <Link
              to="/desk"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="前往交易大厅进行下单执行"
            >
              交易大厅 →
            </Link>
          </div>
        </div>
      </div>

      {/* 对应子界面的完整渲染 */}
      <div className="min-h-0">
        {activeTab === 'backtest' && <Backtest />}
        {activeTab === 'portfolio' && <Portfolio />}
        {activeTab === 'capacity' && <CapacityAttribution />}
      </div>
    </div>
  );
}
