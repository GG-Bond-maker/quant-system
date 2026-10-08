/**
 * 数据管线与任务调度中枢（对标工业级量化中枢 Airflow / DolphinScheduler / JoinQuant）
 *
 * 核心功能：
 * 1. 11 步全流程 DAG 有向无环图拓扑（分支、汇聚、关键路径透视）
 * 2. 运筹学 CPM 图论甘特时序与 20:30 SLA 履约预测模型
 * 3. 管道互斥锁透明度与防并发争用监测
 * 4. 断点续跑、步骤子集调度与黑底代码终端 Traceback 深度诊断
 * 5. 4 大常驻守护计划（盘前 09:15 / 午盘 11:35 / 盘后 17:30 / 周末 02:00）全景看板
 */
import { useCallback, useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { opsApi, type DagStatus, type DataJobRecord } from '@/api/production';
import { PageHeader, SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { CpmTimelineSlaCard } from './CpmTimelineSlaCard';
import { PipelineDagTopologyCard } from './PipelineDagTopologyCard';
import { PipelineLockBanner } from './PipelineLockBanner';
import { ScheduledTriggersCard } from './ScheduledTriggersCard';
import { TaskTracebackModal } from './TaskTracebackModal';

const JOB_STATUS_TONE: Record<string, string> = {
  SUCCESS: 'text-success bg-success-bg/40 border-success/30',
  FAILED: 'text-danger bg-danger-bg/40 border-danger/30',
  RUNNING: 'text-brand bg-brand-bg/40 border-brand/30 animate-pulse',
  PENDING: 'text-warn bg-warn-bg/40 border-warn/30',
};

const ALL_11_STEPS = [
  { id: 'update_daily', name: '行情增量同步' },
  { id: 'validate', name: '行情数据校验' },
  { id: 'enrich_delist', name: '退市名单回填' },
  { id: 'sync_announcements', name: '全市场公告同步' },
  { id: 'rebuild_qfq', name: '前复权因子重算' },
  { id: 'build_universe', name: '交易宇宙构建' },
  { id: 'build_universe_bt', name: '回测宇宙物化' },
  { id: 'build_features', name: '截面特征工程' },
  { id: 'infer', name: '模型离线推理' },
  { id: 'screener_dump', name: '选股雷达快照' },
  { id: 'build_cs_mirror', name: '截面双轴镜像' },
];

export default function Pipeline() {
  const [activeTab, setActiveTab] = useState<'topology' | 'cpm' | 'history'>('topology');
  const [dag, setDag] = useState<DagStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 重跑控制表单状态
  const [rerunDate, setRerunDate] = useState<string>('');
  const [rerunMode, setRerunMode] = useState<'full' | 'resume' | 'subset'>('full');
  const [resumeStep, setResumeStep] = useState<string>('update_daily');
  const [selectedSubSteps, setSelectedSubSteps] = useState<string[]>([]);
  const [targetCodes, setTargetCodes] = useState<string>('');

  // 堆栈详情弹窗
  const [selectedTraceJob, setSelectedTraceJob] = useState<DataJobRecord | null>(null);

  const dagTask = useAbortableTask();
  const rerunTask = useAbortableTask();

  const load = useCallback(async () => {
    const ctrl = dagTask.begin();
    setLoading(true);
    setErr(null);
    try {
      const d = await opsApi.dag({ signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setDag(d);
      if (d.benchmark_date) {
        setRerunDate((prev) => prev || d.benchmark_date || '');
      }
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '加载流水线拓扑状态失败');
    } finally {
      setLoading(false);
    }
  }, [dagTask]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRerun = async (options?: {
    dateOverride?: string;
    resumeStepOverride?: string;
    subStepsOverride?: string[];
  }) => {
    const ctrl = rerunTask.begin();
    setBusy(true);
    setMsg(null);
    setErr(null);

    const execDate = options?.dateOverride || rerunDate;
    if (!execDate) {
      setErr('请指定需要执行或重跑的交易日期');
      setBusy(false);
      return;
    }

    const codes = targetCodes.trim()
      ? targetCodes
          .split(/[,，\s]+/)
          .map((c) => c.trim())
          .filter(Boolean)
      : undefined;

    let payload: {
      trade_date: string;
      codes?: string[];
      steps?: string[];
      resume_from_step?: string;
    } = { trade_date: execDate, codes };

    if (options?.resumeStepOverride) {
      payload.resume_from_step = options.resumeStepOverride;
    } else if (rerunMode === 'resume') {
      payload.resume_from_step = resumeStep;
    } else if (rerunMode === 'subset') {
      const stepsToRun = options?.subStepsOverride || selectedSubSteps;
      if (!stepsToRun.length) {
        setErr('请至少勾选一个需要执行的步骤');
        setBusy(false);
        return;
      }
      payload.steps = stepsToRun;
    }

    try {
      const r = await opsApi.dagRerun(payload, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      if (r.ok) {
        const stepDesc = r.executed_steps
          ? `(执行 ${r.executed_steps.length} 个步骤: ${r.executed_steps.join(' ➔ ')})`
          : '';
        setMsg(`流水线触发成功 ${stepDesc}：${r.summary ?? '执行完成'}`);
        await load();
      } else {
        setErr(`流水线执行返回异常：${r.error ?? '未知错误'}`);
      }
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '触发重跑失败');
    } finally {
      if (rerunTask.finish(ctrl)) setBusy(false);
    }
  };

  const handleResumeFromStep = (tradeDate: string, step: string) => {
    setRerunDate(tradeDate);
    setRerunMode('resume');
    setResumeStep(step);
    void handleRerun({ dateOverride: tradeDate, resumeStepOverride: step });
  };

  const toggleSubStep = (stepId: string) => {
    setSelectedSubSteps((prev) =>
      prev.includes(stepId) ? prev.filter((id) => id !== stepId) : [...prev, stepId]
    );
  };

  return (
    <div className="space-y-4">
      {/* 顶部标题栏与全局动作 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader
          title="数据管线与调度中枢"
          sub="11 步 DAG 拓扑流向 · 运筹学 CPM 关键路径求解 · 20:30 SLA 履约监控 · 断点续跑与常驻守护治理"
        />
        <div className="flex items-center gap-2">
          {dag?.benchmark_date && (
            <span className="num rounded-lg border border-border-subtle bg-surface-raised px-2.5 py-1 text-xs text-ink-secondary">
              基准交易日: <span className="font-semibold text-ink">{dag.benchmark_date}</span>
            </span>
          )}
          <button
            onClick={() => void load()}
            disabled={loading}
            className="rounded-lg border border-border-subtle bg-surface-raised px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-overlay disabled:opacity-60"
          >
            {loading ? '刷新中…' : '刷新看板'}
          </button>
        </div>
      </div>

      {/* 管道互斥锁透明度横幅 */}
      <PipelineLockBanner lock={dag?.lock_status} />

      {/* 全局消息与错误反馈 */}
      {err && (
        <div className="rounded-lg border border-danger/30 bg-danger-bg px-4 py-2.5 text-xs text-danger flex items-center justify-between">
          <span>{err}</span>
          <button onClick={() => setErr(null)} className="text-danger hover:opacity-80">✕</button>
        </div>
      )}
      {msg && (
        <div className="rounded-lg border border-success/30 bg-success-bg px-4 py-2.5 text-xs text-success flex items-center justify-between">
          <span>{msg}</span>
          <button onClick={() => setMsg(null)} className="text-success hover:opacity-80">✕</button>
        </div>
      )}

      {/* 3-Tab 导航页签 */}
      <div className="flex items-center border-b border-border-subtle text-xs">
        <button
          onClick={() => setActiveTab('topology')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'topology'
              ? 'border-brand text-brand font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>DAG 拓扑全景与调度控制</span>
          <span className="rounded-full bg-surface-raised px-1.5 py-0.2 text-2xs text-ink-muted">
            11 步
          </span>
        </button>

        <button
          onClick={() => setActiveTab('cpm')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'cpm'
              ? 'border-brand text-brand font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>CPM 甘特与 SLA 履约治理</span>
          {dag?.sla_metrics && (
            <span
              className={`rounded-full px-1.5 py-0.2 text-[10px] font-semibold ${
                dag.sla_metrics.sla_status === 'COMPLIANT'
                  ? 'bg-success-bg text-success'
                  : 'bg-warn-bg text-warn'
              }`}
            >
              {dag.sla_metrics.compliance_probability}%
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveTab('history')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'history'
              ? 'border-brand text-brand font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>任务执行历史与常驻守护</span>
          <span className="rounded-full bg-surface-raised px-1.5 py-0.2 text-2xs text-ink-muted">
            {dag?.recent_jobs?.length ?? 0}
          </span>
        </button>
      </div>

      {/* Tab 1: DAG 拓扑全景与调度控制 */}
      {activeTab === 'topology' && (
        <div className="space-y-4">
          <SectionCard
            title="每日生产 DAG 拓扑（数据产物实时新鲜度映射）"
            action={
              <span className="text-2xs text-ink-muted">
                支持节点单选下钻、依赖/后继穿透诊断与一键断点续跑
              </span>
            }
          >
            {dag ? (
              <PipelineDagTopologyCard
                dag={dag}
                onSelectStepToRerun={(stepId) => {
                  setRerunMode('resume');
                  setResumeStep(stepId);
                  const el = document.getElementById('rerun-controller-section');
                  el?.scrollIntoView({ behavior: 'smooth' });
                }}
              />
            ) : (
              <div className="py-12 text-center text-xs text-ink-muted">正在扫描 DAG 状态…</div>
            )}
          </SectionCard>

          {/* 交互式重跑调度控制中心 */}
          <div id="rerun-controller-section">
            <SectionCard
              title="生产流水线执行与断点重跑控制中心"
              action={
                <span className="text-2xs text-ink-muted">
                  全量 11 步调度 · 中断步骤断点续跑 · 灵活子集构建
                </span>
              }
            >
              <div className="space-y-4 text-xs">
                {/* 调度模式选择 */}
                <div className="flex flex-wrap items-center gap-4 border-b border-border-subtle pb-3">
                  <span className="font-semibold text-ink">调度执行模式:</span>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="rerunMode"
                      value="full"
                      checked={rerunMode === 'full'}
                      onChange={() => setRerunMode('full')}
                      className="text-brand focus:ring-brand"
                    />
                    <span>全量 11 步生产主链 (默认)</span>
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="rerunMode"
                      value="resume"
                      checked={rerunMode === 'resume'}
                      onChange={() => setRerunMode('resume')}
                      className="text-brand focus:ring-brand"
                    />
                    <span>从指定步骤断点续跑 (Resume from Step)</span>
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="rerunMode"
                      value="subset"
                      checked={rerunMode === 'subset'}
                      onChange={() => setRerunMode('subset')}
                      className="text-brand focus:ring-brand"
                    />
                    <span>自定义指定步骤子集 (Custom Steps)</span>
                  </label>
                </div>

                {/* 参数输入网格 */}
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                  <div>
                    <label htmlFor="rerun-trade-date" className="block text-2xs font-medium text-ink-muted mb-1">
                      执行目标交易日 (YYYY-MM-DD):
                    </label>
                    <input
                      id="rerun-trade-date"
                      type="date"
                      value={rerunDate}
                      onChange={(e) => setRerunDate(e.target.value)}
                      className="w-full rounded-lg border border-border-subtle bg-surface-raised px-3 py-1.5 text-xs text-ink focus:border-brand focus:outline-hidden"
                    />
                  </div>

                  {rerunMode === 'resume' && (
                    <div>
                      <label htmlFor="resume-step-select" className="block text-2xs font-medium text-ink-muted mb-1">
                        断点起始步骤 (从该步向后全量执行):
                      </label>
                      <select
                        id="resume-step-select"
                        value={resumeStep}
                        onChange={(e) => setResumeStep(e.target.value)}
                        className="w-full rounded-lg border border-border-subtle bg-surface-raised px-3 py-1.5 text-xs text-ink focus:border-brand focus:outline-hidden"
                      >
                        {ALL_11_STEPS.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name} ({s.id})
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  <div>
                    <label htmlFor="target-codes-input" className="block text-2xs font-medium text-ink-muted mb-1">
                      目标股票池代码 (可选，逗号分隔，上限 20 只):
                    </label>
                    <input
                      id="target-codes-input"
                      type="text"
                      placeholder="留空为全市场，例如: 600519, 000001"
                      value={targetCodes}
                      onChange={(e) => setTargetCodes(e.target.value)}
                      className="w-full rounded-lg border border-border-subtle bg-surface-raised px-3 py-1.5 text-xs text-ink focus:border-brand focus:outline-hidden font-mono"
                    />
                  </div>
                </div>

                {/* 子集模式复选框 */}
                {rerunMode === 'subset' && (
                  <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
                    <div className="text-2xs font-semibold text-ink-muted mb-2">
                      请勾选需要按序执行的步骤子集:
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                      {ALL_11_STEPS.map((s) => {
                        const checked = selectedSubSteps.includes(s.id);
                        return (
                          <label
                            key={s.id}
                            className={`flex items-center gap-2 rounded border px-2.5 py-1.5 cursor-pointer text-2xs transition-colors ${
                              checked
                                ? 'border-brand bg-brand/10 text-brand font-medium'
                                : 'border-border-subtle bg-surface-base text-ink-secondary'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleSubStep(s.id)}
                              className="text-brand rounded"
                            />
                            <span className="truncate">{s.name}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* 触发重跑按钮 */}
                <div className="flex items-center justify-between pt-2">
                  <div className="text-2xs text-ink-muted">
                    安全保证：已 SUCCESS 的任务具有幂等保护；重跑将在专用计算池执行，保障事件循环探针流畅。
                  </div>
                  <button
                    disabled={busy}
                    onClick={() => void handleRerun()}
                    className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-brand-hover disabled:opacity-60 transition-colors"
                  >
                    {busy ? (
                      <>
                        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
                        <span>流水线执行中…</span>
                      </>
                    ) : (
                      <span>
                        立即触发调度执行 (
                        {rerunMode === 'full'
                          ? '全量 11 步'
                          : rerunMode === 'resume'
                          ? `续跑: ${resumeStep}`
                          : `子集: ${selectedSubSteps.length}步`}
                        ) ➔
                      </span>
                    )}
                  </button>
                </div>
              </div>
            </SectionCard>
          </div>

          {/* 分片隔离与坏道隔离监控 */}
          {dag?.sharding_metrics && (
            <SectionCard title="Map-Reduce 分片计算与质量隔离监控 (Quarantine Sharding)">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 text-xs">
                <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
                  <div className="text-2xs text-ink-muted">Map-Reduce 分片总数</div>
                  <div className="num mt-1 text-base font-bold text-ink">
                    {dag.sharding_metrics.total_shards} 组
                  </div>
                  <div className="text-2xs text-ink-secondary">标的及行业并行分区</div>
                </div>
                <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
                  <div className="text-2xs text-ink-muted">健康活跃分片</div>
                  <div className="num mt-1 text-base font-bold text-success">
                    {dag.sharding_metrics.healthy_shards} 组
                  </div>
                  <div className="text-2xs text-ink-secondary">正常完成特征构建与推理</div>
                </div>
                <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
                  <div className="text-2xs text-ink-muted">坏道隔离分区 (Quarantine)</div>
                  <div className="num mt-1 text-base font-bold text-warn">
                    {dag.sharding_metrics.quarantined_shards} 个
                  </div>
                  <div className="text-2xs text-ink-secondary">脏数据隔离不阻塞主链路</div>
                </div>
                <div className="rounded-lg border border-border-subtle bg-surface-raised p-3">
                  <div className="text-2xs text-ink-muted">断点检查点就绪性</div>
                  <div className="mt-1 text-base font-bold text-brand">
                    {dag.sharding_metrics.checkpoint_resumption_enabled ? '已就绪' : '关闭'}
                  </div>
                  <div className="text-2xs text-ink-secondary">支持单 Step 幂等续跑</div>
                </div>
              </div>
            </SectionCard>
          )}
        </div>
      )}

      {/* Tab 2: CPM 甘特与 SLA 履约治理 */}
      {activeTab === 'cpm' && (
        <div className="space-y-4">
          {dag ? (
            <CpmTimelineSlaCard dag={dag} />
          ) : (
            <div className="py-12 text-center text-xs text-ink-muted">加载 CPM 与 SLA 时序中…</div>
          )}
        </div>
      )}

      {/* Tab 3: 任务执行历史与常驻守护 */}
      {activeTab === 'history' && (
        <div className="space-y-4">
          {/* 常驻后台定时计划看板 */}
          <ScheduledTriggersCard triggers={dag?.scheduled_triggers} />

          {/* 真实 data_jobs 执行流水记录 */}
          <SectionCard
            title="最近流水线作业执行历史 (data_jobs 审计底账)"
            action={
              <span className="text-2xs text-ink-muted">
                点击「堆栈诊断」可查看完整 Python Traceback 与智能排错建议
              </span>
            }
          >
            {dag?.recent_jobs?.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-2xs">
                  <thead>
                    <tr className="border-b border-border-subtle text-ink-secondary">
                      <th className="py-2 text-left font-medium">任务 ID</th>
                      <th className="text-left font-medium">任务类型</th>
                      <th className="text-center font-medium">交易日</th>
                      <th className="text-center font-medium">执行状态</th>
                      <th className="text-center font-medium">中断/完成步骤</th>
                      <th className="text-center font-medium">耗时</th>
                      <th className="text-center font-medium">标的规模</th>
                      <th className="text-left font-medium">启动时间</th>
                      <th className="text-left font-medium">异常摘要</th>
                      <th className="text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {dag.recent_jobs.map((j) => (
                      <tr
                        key={j.id ?? `${j.trade_date}-${j.job_type}`}
                        className="hover:bg-surface-raised transition-colors"
                      >
                        <td className="py-2.5 font-mono text-ink-muted">#{j.id ?? '—'}</td>
                        <td className="font-mono font-medium text-ink">{j.job_type}</td>
                        <td className="num text-center text-ink">{j.trade_date}</td>
                        <td className="text-center">
                          <span
                            className={`inline-block rounded px-2 py-0.5 text-2xs font-semibold border ${
                              JOB_STATUS_TONE[j.status] ?? 'text-ink-secondary bg-surface-raised'
                            }`}
                          >
                            {j.status}
                          </span>
                        </td>
                        <td className="text-center font-mono text-ink">
                          {j.current_step ?? '—'}
                        </td>
                        <td className="num text-center text-ink">
                          {j.duration_ms != null ? `${(j.duration_ms / 1000).toFixed(1)}s` : '—'}
                        </td>
                        <td className="num text-center text-ink-secondary">
                          {j.codes_count != null ? `${j.codes_count} 只` : '全市场'}
                        </td>
                        <td className="num text-ink-muted">{j.started_at ?? '—'}</td>
                        <td className="max-w-[180px] truncate text-ink-secondary" title={j.error_message ?? ''}>
                          {j.error_message ?? <span className="text-ink-muted">正常</span>}
                        </td>
                        <td className="text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              data-testid={`trace-btn-${j.id ?? ''}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedTraceJob(j);
                              }}
                              className="rounded border border-border-subtle bg-surface-base px-2 py-1 text-2xs font-medium text-ink hover:bg-surface-overlay"
                            >
                              堆栈诊断
                            </button>
                            {j.current_step && j.status === 'FAILED' && (
                              <button
                                data-testid={`resume-btn-${j.id ?? ''}`}
                                onClick={() => handleResumeFromStep(j.trade_date, j.current_step!)}
                                className="rounded bg-brand px-2 py-1 text-2xs font-medium text-white hover:bg-brand-hover"
                              >
                                断点续跑
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="py-8 text-center text-xs text-ink-muted">暂无历史执行记录</div>
            )}
          </SectionCard>
        </div>
      )}

      {/* 黑底代码终端 Traceback 详情弹窗 */}
      <TaskTracebackModal
        job={selectedTraceJob}
        onClose={() => setSelectedTraceJob(null)}
        onResumeFromStep={handleResumeFromStep}
      />
    </div>
  );
}
