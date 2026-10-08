/**
 * AI 日报（工业级投研工作台版）：每日盘后由例行调度或研究员手动触发生成。
 *
 * 核心架构：
 * 1. 顶部 4 联全局态势 KPI（超额收益、微观情绪指数、预期调仓换手/滑点、在险价值 VaR）；
 * 2. 5-Tab 专业量化分析工作台（全景总览、情绪与风格、绩效归因、因子与模型、次日调仓执行）；
 * 3. 投研分发闭环（一键复制 Markdown、手动广播推送 Webhook、一键同步交易台母单）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/client';
import { monitorApi, type DailyReport } from '@/api/monitor';
import { hasMinimumRole } from '@/components/RequireAuth';
import { PageHeader, SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useAuthStore } from '@/stores/useAuthStore';
import { MarketCompassCard } from './MarketCompassCard';
import { BrinsonAttributionCard } from './BrinsonAttributionCard';
import { SignalPreviewCard } from './SignalPreviewCard';
import { StyleFactorsCard } from './StyleFactorsCard';
import { PortfolioRiskCard } from './PortfolioRiskCard';
import { AiSynthesisCard } from './AiSynthesisCard';
import { ReportKpiCards } from './ReportKpiCards';

/** 最小 markdown 行内渲染：**加粗** 与 `代码` */
function renderLine(line: string, key: number) {
  const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <li key={key} className="leading-relaxed">
      {parts.map((p, j) =>
        p.startsWith('**') ? (
          <strong key={j} className="text-ink">
            {p.slice(2, -2)}
          </strong>
        ) : p.startsWith('`') ? (
          <code key={j} className="rounded bg-surface-sunken px-1 font-mono">
            {p.slice(1, -1)}
          </code>
        ) : (
          <span key={j}>{p}</span>
        ),
      )}
    </li>
  );
}

type TabKey = 'all' | 'compass' | 'performance' | 'factors' | 'rebalance';

export default function Report() {
  const [report, setReport] = useState<DailyReport | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [activeTab, setActiveTab] = useState<TabKey>('all');
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [toastMsg, setToastMsg] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  /** 代际守卫：快速切换历史期 / 重新生成时只有最新请求可写状态 */
  const seqRef = useRef(0);
  /** 日报生成 60s 在途请求的中断控制 */
  const genTask = useAbortableTask();
  const role = useAuthStore((s) => s.user?.role);
  const canOperate = hasMinimumRole(role, 'researcher');

  const showToast = (text: string, type: 'success' | 'error' = 'success') => {
    setToastMsg({ text, type });
    setTimeout(() => {
      setToastMsg((prev) => (prev?.text === text ? null : prev));
    }, 3000);
  };

  const load = useCallback(async (date?: string) => {
    const seq = ++seqRef.current;
    setLoading(true);
    setErr(null);
    try {
      const d = await monitorApi.report(date);
      if (seq !== seqRef.current) return;
      setReport(d.report);
      setHistory(d.history);
    } catch (e) {
      if (seq !== seqRef.current) return;
      setErr(e instanceof ApiError ? e.message : '日报加载失败');
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(selected);
  }, [load, selected]);

  const regenerate = async () => {
    if (!canOperate) return;
    const seq = ++seqRef.current;
    const ctrl = genTask.begin();
    setRegenerating(true);
    setErr(null);
    try {
      const rep = await monitorApi.generateReport({ signal: ctrl.signal });
      if (ctrl.signal.aborted || seq !== seqRef.current) return;
      setReport(rep);
      setSelected(undefined);
      setHistory((h) => (h.includes(rep.date) ? h : [...h, rep.date].sort()));
      showToast('日报重新生成成功', 'success');
    } catch (e) {
      if (ctrl.signal.aborted || seq !== seqRef.current) return;
      setErr(e instanceof ApiError ? e.message : '生成失败（需要研究员权限）');
    } finally {
      if (genTask.finish(ctrl) && seq === seqRef.current) setRegenerating(false);
    }
  };

  const dispatch = async () => {
    if (!canOperate || dispatching) return;
    setDispatching(true);
    try {
      const res = await monitorApi.dispatchReport();
      showToast(res.message || '研报已成功广播推送至各风控与交易信道', 'success');
    } catch (e) {
      showToast(e instanceof ApiError ? e.message : '广播推送失败', 'error');
    } finally {
      setDispatching(false);
    }
  };

  const copyMarkdown = async () => {
    if (!report?.markdown) return;
    try {
      await navigator.clipboard.writeText(report.markdown);
      showToast('已复制完整研报 Markdown 至剪贴板', 'success');
    } catch {
      showToast('复制失败，请手动展开下方原文复制', 'error');
    }
  };

  return (
    <div className="flex min-h-full flex-col gap-3">
      {/* 顶部标题栏与控制台 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PageHeader
          title="AI 日报"
          sub="对标机构级投研驾驶舱（盘后复盘 · 风格雷达 · 绩效归因 · 因子健康 · 次日调仓与执行闭环）"
        />
        <div className="flex flex-wrap items-center gap-2">
          {history.length > 0 && (
            <select
              value={selected ?? ''}
              onChange={(e) => setSelected(e.target.value || undefined)}
              className="rounded-md border border-hair bg-surface px-2 py-1.5 text-xs outline-none focus:border-brand-300"
            >
              <option value="">最新一期</option>
              {[...history].reverse().map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          )}

          {/* 复制 Markdown 按钮 */}
          <button
            type="button"
            onClick={() => void copyMarkdown()}
            disabled={!report}
            title="复制完整 Markdown 报告以供离线分享或归档"
            className="rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs font-medium text-ink hover:bg-surface-alt disabled:opacity-50"
          >
            📋 复制 Markdown
          </button>

          {/* 广播分发按钮 */}
          <button
            type="button"
            onClick={() => void dispatch()}
            disabled={!canOperate || dispatching || !report}
            title={canOperate ? '广播推送日报至 Webhook / 飞书 / 钉钉群信道' : '需要 researcher 及以上角色'}
            className="rounded-md border border-brand-500/30 bg-brand-500/10 px-2.5 py-1.5 text-xs font-medium text-brand-600 hover:bg-brand-500/20 disabled:opacity-50 dark:text-brand-400"
          >
            {dispatching ? '广播中…' : '📢 广播推送'}
          </button>

          {/* 重新生成按钮 */}
          <button
            type="button"
            onClick={() => void regenerate()}
            disabled={!canOperate || regenerating}
            title={canOperate ? undefined : '需要 researcher 及以上角色'}
            className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {regenerating ? '生成中…' : '重新生成'}
          </button>
        </div>
      </div>

      {toastMsg && (
        <div
          className={`rounded-md px-3 py-2 text-xs font-medium ${
            toastMsg.type === 'success' ? 'bg-success-bg text-success' : 'bg-danger-bg text-danger'
          }`}
        >
          {toastMsg.text}
        </div>
      )}

      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}

      {loading ? (
        <div className="animate-pulse rounded-lg border border-hair bg-surface p-6 text-xs text-ink-muted">
          加载中…
        </div>
      ) : !report ? (
        <SectionCard title="尚无日报" bodyClassName="p-6 text-center">
          <p className="text-xs text-ink-muted">
            晚间例行（默认 17:30）会自动生成首期日报；也可以点击右上角「重新生成」立即生成。
          </p>
        </SectionCard>
      ) : (
        <>
          {/* 期数与生成时间条 */}
          <div className="flex flex-wrap items-center justify-between gap-2 text-2xs text-ink-muted">
            <div className="flex items-center gap-2">
              <span>
                期数：<b className="text-ink">{report.date}</b>
              </span>
              <span>生成于 {report.generated_at.replace('T', ' ')}</span>
            </div>
            {report.tips.length > 0 && (
              <span className="rounded bg-warn-bg px-2 py-0.5 text-3xs font-semibold text-warn">
                ⚠️ {report.tips.length} 项投委会特别关注事项
              </span>
            )}
          </div>

          {/* 全局 4 联态势 KPI 看板 */}
          <ReportKpiCards
            brinson={report.brinson_summary}
            compass={report.market_compass}
            preview={report.signal_preview}
            risk={report.portfolio_risk}
          />

          {/* 5-Tab 现代化研报工作台 */}
          <div className="border-b border-hair">
            <nav className="flex space-x-4">
              <button
                type="button"
                onClick={() => setActiveTab('all')}
                className={`border-b-2 py-2 text-xs font-medium transition-colors ${
                  activeTab === 'all'
                    ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                    : 'border-transparent text-ink-muted hover:text-ink'
                }`}
              >
                全景总览
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('compass')}
                className={`border-b-2 py-2 text-xs font-medium transition-colors ${
                  activeTab === 'compass'
                    ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                    : 'border-transparent text-ink-muted hover:text-ink'
                }`}
              >
                情绪与风格
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('performance')}
                className={`border-b-2 py-2 text-xs font-medium transition-colors ${
                  activeTab === 'performance'
                    ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                    : 'border-transparent text-ink-muted hover:text-ink'
                }`}
              >
                组合绩效归因
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('factors')}
                className={`border-b-2 py-2 text-xs font-medium transition-colors ${
                  activeTab === 'factors'
                    ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                    : 'border-transparent text-ink-muted hover:text-ink'
                }`}
              >
                因子与模型
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('rebalance')}
                className={`border-b-2 py-2 text-xs font-medium transition-colors ${
                  activeTab === 'rebalance'
                    ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                    : 'border-transparent text-ink-muted hover:text-ink'
                }`}
              >
                次日调仓执行
              </button>
            </nav>
          </div>

          {/* Tab 全景总览 (All) */}
          {activeTab === 'all' && (
            <div className="space-y-3">
              {report.synthesis && (
                <AiSynthesisCard synthesis={report.synthesis} tips={report.tips} />
              )}
              {report.market_compass && (
                <MarketCompassCard compass={report.market_compass} />
              )}
              {report.style_factors && (
                <StyleFactorsCard data={report.style_factors} />
              )}
              {report.brinson_summary && (
                <BrinsonAttributionCard
                  summary={report.brinson_summary}
                  sectors={report.brinson_sectors}
                />
              )}
              {report.portfolio_risk && (
                <PortfolioRiskCard risk={report.portfolio_risk} />
              )}
              {report.signal_preview && (
                <SignalPreviewCard preview={report.signal_preview} />
              )}
              {report.sections.map((s) => (
                <SectionCard key={s.title} title={s.title}>
                  <ul className="list-disc space-y-1 pl-4 text-xs text-ink-secondary">
                    {s.lines.map((line, i) => renderLine(line, i))}
                  </ul>
                </SectionCard>
              ))}
            </div>
          )}

          {/* Tab 1: 情绪与风格 */}
          {activeTab === 'compass' && (
            <div className="space-y-3">
              {report.synthesis && (
                <AiSynthesisCard synthesis={report.synthesis} tips={report.tips} />
              )}
              {report.market_compass && (
                <MarketCompassCard compass={report.market_compass} />
              )}
              {report.style_factors && (
                <StyleFactorsCard data={report.style_factors} />
              )}
            </div>
          )}

          {/* Tab 2: 组合绩效归因 */}
          {activeTab === 'performance' && (
            <div className="space-y-3">
              {report.brinson_summary && (
                <BrinsonAttributionCard
                  summary={report.brinson_summary}
                  sectors={report.brinson_sectors}
                />
              )}
              {report.sections
                .filter((s) => s.title.includes('模拟盘') || s.title.includes('绩效') || s.title.includes('归因'))
                .map((s) => (
                  <SectionCard key={s.title} title={s.title}>
                    <ul className="list-disc space-y-1 pl-4 text-xs text-ink-secondary">
                      {s.lines.map((line, i) => renderLine(line, i))}
                    </ul>
                  </SectionCard>
                ))}
            </div>
          )}

          {/* Tab 3: 因子与模型 */}
          {activeTab === 'factors' && (
            <div className="space-y-3">
              {report.tips.length > 0 && (
                <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2">
                  <div className="mb-1 text-xs font-semibold text-warn">因子与模型风险提示</div>
                  <ul className="list-disc space-y-0.5 pl-4 text-2xs text-warn">
                    {report.tips.map((t, i) => (
                      <li key={i}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}
              {report.sections
                .filter(
                  (s) =>
                    !s.title.includes('模拟盘') &&
                    !s.title.includes('调仓') &&
                    !s.title.includes('次日'),
                )
                .map((s) => (
                  <SectionCard key={s.title} title={s.title}>
                    <ul className="list-disc space-y-1 pl-4 text-xs text-ink-secondary">
                      {s.lines.map((line, i) => renderLine(line, i))}
                    </ul>
                  </SectionCard>
                ))}
            </div>
          )}

          {/* Tab 4: 次日调仓执行 */}
          {activeTab === 'rebalance' && (
            <div className="space-y-3">
              {report.portfolio_risk && (
                <PortfolioRiskCard risk={report.portfolio_risk} />
              )}
              {report.signal_preview && (
                <SignalPreviewCard preview={report.signal_preview} />
              )}
            </div>
          )}

          {/* Markdown 原文折叠 */}
          <details className="rounded-lg border border-hair bg-surface px-3 py-2">
            <summary className="cursor-pointer text-2xs text-ink-muted">
              Markdown 原文（复制/存档用）
            </summary>
            <pre className="mt-2 whitespace-pre-wrap break-words text-2xs leading-relaxed text-ink-secondary">
              {report.markdown}
            </pre>
          </details>
        </>
      )}

      <p className="text-center text-2xs text-ink-muted">
        日报全部由真实落库数据聚合生成 ｜ 仅供研究参考
      </p>
    </div>
  );
}
