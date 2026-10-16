/**
 * 数据工程中枢（DataOps & Engineering Hub）。
 *
 * 聚合「数据中心 (数据资产概览与落库)」、「数据质量 (质检与血缘分析)」与「任务调度 (DAG管线与调度)」，
 * 构建专业级量化基础设施 DataOps 一体化平台：
 * - 数据中心：全市场行情与财务本地数据湖、增量同步控制、数据集 Schema、存储概览
 * - 质量巡检：真实 QC 扫描、截面热力图、多源交叉对账、代码级血缘受损扩散图谱
 * - 调度流水线：11 步量化 DAG 有向无环图、CPM 运筹甘特时序、20:30 SLA 履约模型、互斥锁透视
 */
import { useLocation, useNavigate, useSearchParams, Link } from 'react-router-dom';
import DataCenter from '@/pages/DataCenter';
import DataQuality from '@/pages/DataQuality';
import Pipeline from '@/pages/Pipeline';

export type DataTab = 'data' | 'dataquality' | 'pipeline';

interface DataHubProps {
  initialTab?: DataTab;
}

export default function DataHub({ initialTab }: DataHubProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // 计算当前激活的子界面：优先根据 URL 路径，其次根据 ?tab= 参数，最后根据 initialTab
  const activeTab: DataTab = (() => {
    const p = location.pathname.toLowerCase();
    if (p.startsWith('/dataquality')) return 'dataquality';
    if (p.startsWith('/pipeline')) return 'pipeline';
    if (p.startsWith('/data')) {
      const qTab = searchParams.get('tab')?.toLowerCase();
      if (qTab === 'dataquality' || qTab === 'quality') return 'dataquality';
      if (qTab === 'pipeline' || qTab === 'tasks') return 'pipeline';
      if (qTab === 'data') return 'data';
    }
    return initialTab ?? 'data';
  })();

  const handleSwitchTab = (tab: DataTab) => {
    if (tab === 'data') navigate('/data');
    else if (tab === 'dataquality') navigate('/dataquality');
    else if (tab === 'pipeline') navigate('/pipeline');
  };

  return (
    <div className="space-y-4">
      {/* 顶部数据工程中枢切换栏 */}
      <div className="rounded-xl border border-hair bg-surface px-4 py-2.5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* 左侧：模块标题与 Tab 组 */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 pr-2 border-r border-hair">
              <span className="text-sm font-bold tracking-tight text-ink">数据工程</span>
              <span className="hidden rounded bg-surface-sunken px-1.5 py-0.5 text-3xs font-medium text-ink-muted sm:inline">
                DataOps 基础设施
              </span>
            </div>

            {/* 三大子模块 Tab 切换按钮 */}
            <div className="flex items-center rounded-lg bg-surface-sunken p-1 text-xs font-medium" role="tablist" aria-label="数据工程模块切换">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'data'}
                onClick={() => handleSwitchTab('data')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'data'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <ellipse cx="12" cy="6" rx="8" ry="3" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M4 6v12c0 1.66 3.58 3 8 3s8-1.34 8-3V6" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>数据资产</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'data' ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  湖仓 · 同步控制
                </span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'dataquality'}
                onClick={() => handleSwitchTab('dataquality')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'dataquality'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <path d="M12 3 5 6v6c0 4.2 2.9 8 7 9 4.1-1 7-4.8 7-9V6l-7-3Z" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>质量巡检</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'dataquality' ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  QC扫描 · 代码血缘
                </span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'pipeline'}
                onClick={() => handleSwitchTab('pipeline')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all ${
                  activeTab === 'pipeline'
                    ? 'bg-surface font-semibold text-brand-600 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                    : 'text-ink-secondary hover:text-ink'
                }`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-3.5 w-3.5">
                  <rect x="3" y="5" width="18" height="16" rx="2" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M3 10h18M8 3v4M16 3v4" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M12 13v3l2 1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>调度流水线</span>
                <span className={`rounded px-1 py-0.2 text-3xs ${
                  activeTab === 'pipeline' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300' : 'bg-surface-alt text-ink-muted'
                }`}>
                  11步DAG · SLA预测
                </span>
              </button>
            </div>
          </div>

          {/* 右侧：快速直达预警中心与系统设置 */}
          <div className="flex items-center gap-2 text-2xs text-ink-muted">
            <span className="hidden sm:inline">运维监控：</span>
            <Link
              to="/alerts"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="前往预警中心"
            >
              预警中心 →
            </Link>
            <span className="text-ink-muted/40">·</span>
            <Link
              to="/settings"
              className="rounded px-1.5 py-0.5 text-ink-secondary transition-colors hover:bg-surface-alt hover:text-brand-600"
              title="前往系统设置"
            >
              网关配置 →
            </Link>
          </div>
        </div>
      </div>

      {/* 对应子界面的完整渲染 */}
      <div className="min-h-0">
        {activeTab === 'data' && <DataCenter />}
        {activeTab === 'dataquality' && <DataQuality />}
        {activeTab === 'pipeline' && <Pipeline />}
      </div>
    </div>
  );
}
