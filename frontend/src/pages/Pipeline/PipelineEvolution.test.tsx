import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DagStatus } from '@/api/production';
import Pipeline from './index';

const { mockDagData } = vi.hoisted(() => {
  const data: DagStatus = {
    benchmark_date: '2026-09-30',
    stages: [
      { id: 'update_daily', name: '行情增量同步', dataset: 'daily_bar', done_date: '2026-09-30', status: 'ok', duration_s: 42, slack_s: 0, is_critical_path: true, category: 'ingest' },
      { id: 'validate', name: '行情数据校验', dataset: 'daily_bar_hfq', done_date: '2026-09-30', status: 'ok', duration_s: 12, slack_s: 0, is_critical_path: true, category: 'qc' },
      { id: 'enrich_delist', name: '退市名单回填', dataset: 'delist', done_date: '2026-09-30', status: 'ok', duration_s: 8, slack_s: 14, is_critical_path: false, category: 'metadata' },
      { id: 'sync_announcements', name: '全市场公告同步', dataset: 'announcements', done_date: '2026-09-30', status: 'ok', duration_s: 15, slack_s: 142, is_critical_path: false, category: 'metadata' },
      { id: 'rebuild_qfq', name: '前复权因子重算', dataset: 'daily_bar_qfq', done_date: '2026-09-30', status: 'ok', duration_s: 22, slack_s: 0, is_critical_path: true, category: 'data_process' },
      { id: 'build_universe', name: '交易宇宙构建', dataset: 'universe_daily', done_date: '2026-09-30', status: 'ok', duration_s: 18, slack_s: 0, is_critical_path: true, category: 'universe' },
      { id: 'build_universe_bt', name: '回测宇宙物化', dataset: 'universe_daily_bt', done_date: '2026-09-30', status: 'ok', duration_s: 20, slack_s: 105, is_critical_path: false, category: 'universe' },
      { id: 'build_features', name: '截面特征工程', dataset: 'features', done_date: '2026-09-30', status: 'ok', duration_s: 75, slack_s: 0, is_critical_path: true, category: 'feature' },
      { id: 'infer', name: '模型离线推理', dataset: 'predictions', done_date: '2026-09-30', status: 'ok', duration_s: 25, slack_s: 0, is_critical_path: true, category: 'model' },
      { id: 'screener_dump', name: '选股雷达快照', dataset: 'screener', done_date: '2026-09-30', status: 'ok', duration_s: 10, slack_s: 0, is_critical_path: true, category: 'signal' },
      { id: 'build_cs_mirror', name: '截面双轴镜像', dataset: 'cs_mirror', done_date: '2026-09-30', status: 'ok', duration_s: 15, slack_s: 0, is_critical_path: true, category: 'data_process' },
    ],
    dag_edges: [
      { source: 'update_daily', target: 'validate', is_critical: true },
      { source: 'validate', target: 'rebuild_qfq', is_critical: true },
      { source: 'validate', target: 'enrich_delist', is_critical: false },
      { source: 'enrich_delist', target: 'sync_announcements', is_critical: false },
      { source: 'enrich_delist', target: 'build_universe', is_critical: false },
      { source: 'rebuild_qfq', target: 'build_universe', is_critical: true },
      { source: 'build_universe', target: 'build_universe_bt', is_critical: false },
      { source: 'build_universe', target: 'build_features', is_critical: true },
      { source: 'build_features', target: 'infer', is_critical: true },
      { source: 'infer', target: 'screener_dump', is_critical: true },
      { source: 'screener_dump', target: 'build_cs_mirror', is_critical: true },
    ],
    critical_path: [
      'update_daily', 'validate', 'rebuild_qfq', 'build_universe',
      'build_features', 'infer', 'screener_dump', 'build_cs_mirror'
    ],
    total_duration_s: 219,
    gantt_timeline: [
      { id: 'update_daily', name: '行情增量同步', offset_s: 0, duration_s: 42, es_s: 0, ef_s: 42, ls_s: 0, lf_s: 42, slack_s: 0, is_critical_path: true },
      { id: 'validate', name: '行情数据校验', offset_s: 42, duration_s: 12, es_s: 42, ef_s: 54, ls_s: 42, lf_s: 54, slack_s: 0, is_critical_path: true },
      { id: 'enrich_delist', name: '退市名单回填', offset_s: 54, duration_s: 8, es_s: 54, ef_s: 62, ls_s: 68, lf_s: 76, slack_s: 14, is_critical_path: false },
      { id: 'sync_announcements', name: '全市场公告同步', offset_s: 62, duration_s: 15, es_s: 62, ef_s: 77, ls_s: 204, lf_s: 219, slack_s: 142, is_critical_path: false },
      { id: 'rebuild_qfq', name: '前复权因子重算', offset_s: 54, duration_s: 22, es_s: 54, ef_s: 76, ls_s: 54, lf_s: 76, slack_s: 0, is_critical_path: true },
      { id: 'build_universe', name: '交易宇宙构建', offset_s: 76, duration_s: 18, es_s: 76, ef_s: 94, ls_s: 76, lf_s: 94, slack_s: 0, is_critical_path: true },
      { id: 'build_universe_bt', name: '回测宇宙物化', offset_s: 94, duration_s: 20, es_s: 94, ef_s: 114, ls_s: 199, lf_s: 219, slack_s: 105, is_critical_path: false },
      { id: 'build_features', name: '截面特征工程', offset_s: 94, duration_s: 75, es_s: 94, ef_s: 169, ls_s: 94, lf_s: 169, slack_s: 0, is_critical_path: true },
      { id: 'infer', name: '模型离线推理', offset_s: 169, duration_s: 25, es_s: 169, ef_s: 194, ls_s: 169, lf_s: 194, slack_s: 0, is_critical_path: true },
      { id: 'screener_dump', name: '选股雷达快照', offset_s: 194, duration_s: 10, es_s: 194, ef_s: 204, ls_s: 194, lf_s: 204, slack_s: 0, is_critical_path: true },
      { id: 'build_cs_mirror', name: '截面双轴镜像', offset_s: 204, duration_s: 15, es_s: 204, ef_s: 219, ls_s: 204, lf_s: 219, slack_s: 0, is_critical_path: true },
    ],
    sla_metrics: {
      scheduled_start: '17:30:00',
      deadline: '20:30:00',
      budget_seconds: 10800,
      critical_path_duration_s: 219,
      p50_duration_s: 219,
      p95_duration_s: 245.5,
      buffer_remaining_s: 10554.5,
      compliance_probability: 99.99,
      sla_status: 'COMPLIANT',
      critical_steps_count: 8,
    },
    scheduled_triggers: [
      {
        id: 'pre_market_init',
        name: '盘前交易日历与除权除息初始化',
        trigger_time: '工作日 09:15',
        cron: '15 9 * * 1-5',
        category: '盘前准备',
        target_steps: ['calendar_check', 'dividend_split_sync'],
        status: 'ACTIVE',
        desc: '校准 A 股交易日历并同步除权除息',
        last_run: '2026-09-30 09:15:00',
        next_run: '次交易日 09:15:00',
      },
      {
        id: 'midday_qc',
        name: '午间行情与成交量轻量校验',
        trigger_time: '工作日 11:35',
        cron: '35 11 * * 1-5',
        category: '日内巡检',
        target_steps: ['midday_probe'],
        status: 'ACTIVE',
        desc: '抽检半日成交量及行情完整度',
        last_run: '2026-09-30 11:35:00',
        next_run: '次交易日 11:35:00',
      },
      {
        id: 'evening_routine',
        name: '盘后核心量化生产流水线 (11 步 DAG)',
        trigger_time: '工作日 17:30',
        cron: '30 17 * * 1-5',
        category: '生产主链',
        target_steps: ['update_daily', 'validate', 'build_features', 'infer', 'screener_dump'],
        status: 'ACTIVE',
        desc: '全市场收盘行情同步与模型推理选股',
        last_run: '2026-09-30 17:30:00',
        next_run: '次交易日 17:30:00',
      },
      {
        id: 'weekend_reconcile',
        name: '周末全量坏道巡检与因子漂移基线更新',
        trigger_time: '周日 02:00',
        cron: '0 2 * * 0',
        category: '离线治理',
        target_steps: ['crc_audit', 'psi_baseline'],
        status: 'ACTIVE',
        desc: '全量坏道巡检与基线更新',
        last_run: '上周日 02:00:00',
        next_run: '本周日 02:00:00',
      },
    ],
    lock_status: {
      is_locked: false,
      owner: null,
      elapsed_seconds: 0.0,
      status: 'IDLE',
    },
    sharding_metrics: {
      total_shards: 8,
      healthy_shards: 8,
      quarantined_shards: 0,
      failure_isolated: true,
      checkpoint_resumption_enabled: true,
    },
    recent_jobs: [
      {
        id: 101,
        job_type: 'daily_pipeline',
        trade_date: '2026-09-30',
        status: 'SUCCESS',
        current_step: 'build_cs_mirror',
        started_at: '2026-09-30 17:30:00',
        finished_at: '2026-09-30 17:33:39',
        duration_ms: 219000,
        error_message: null,
        traceback: null,
        codes_count: 2499,
        job_scope: 'market',
      },
      {
        id: 100,
        job_type: 'daily_pipeline',
        trade_date: '2026-09-29',
        status: 'FAILED',
        current_step: 'validate',
        started_at: '2026-09-29 17:30:00',
        finished_at: '2026-09-29 17:30:12',
        duration_ms: 12000,
        error_message: 'Missing column error in daily_bar',
        traceback: 'Traceback (most recent call last):\n  File "orchestrator.py", line 47\n    raise ValueError("Missing column error in daily_bar")',
        codes_count: 2499,
        job_scope: 'market',
      },
    ],
    note: '11 步 DAG 拓扑由真实产物驱动',
  };
  return { mockDagData: data };
});

vi.mock('@/api/production', async () => {
  const actual = await vi.importActual('@/api/production');
  return {
    ...actual,
    opsApi: {
      dag: vi.fn().mockResolvedValue(mockDagData),
      dagRerun: vi.fn().mockResolvedValue({
        ok: true,
        executed_steps: ['build_features', 'infer', 'screener_dump', 'build_cs_mirror'],
        summary: '断点续跑完成',
      }),
    },
  };
});

import { opsApi } from '@/api/production';
import { TaskTracebackModal } from './TaskTracebackModal';

describe('Pipeline Evolution & Industrial Quantitative Dispatch Workbench', () => {
  beforeEach(() => {
    vi.mocked(opsApi.dag).mockResolvedValue(mockDagData);
    vi.mocked(opsApi.dagRerun).mockResolvedValue({
      ok: true,
      executed_steps: ['build_features', 'infer', 'screener_dump', 'build_cs_mirror'],
      summary: '断点续跑完成',
    });
  });

  it('renders TaskTracebackModal with failure diagnostics', () => {
    const failedJob = mockDagData.recent_jobs[1];
    render(
      <TaskTracebackModal
        job={failedJob}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText(/任务异常诊断与执行堆栈/)).toBeInTheDocument();
    expect(screen.getByText('数据契约 / Schema 缺失异常')).toBeInTheDocument();
    expect(screen.getByText(/Missing column error in daily_bar/)).toBeInTheDocument();
  });

  it('renders 11-step DAG topology with real dependencies and critical path badges', async () => {
    render(<Pipeline />);

    await waitFor(() => {
      expect(screen.getByText('数据管线与调度中枢')).toBeInTheDocument();
    });

    // 检查 11 个原子步骤全部呈现在拓扑卡片中
    expect(screen.getByText('行情增量同步')).toBeInTheDocument();
    expect(screen.getByText('行情数据校验')).toBeInTheDocument();
    expect(screen.getByText('退市名单回填')).toBeInTheDocument();
    expect(screen.getByText('全市场公告同步')).toBeInTheDocument();
    expect(screen.getByText('前复权因子重算')).toBeInTheDocument();
    expect(screen.getByText('交易宇宙构建')).toBeInTheDocument();
    expect(screen.getByText('回测宇宙物化')).toBeInTheDocument();
    expect(screen.getByText('截面特征工程')).toBeInTheDocument();
    expect(screen.getByText('模型离线推理')).toBeInTheDocument();
    expect(screen.getByText('选股雷达快照')).toBeInTheDocument();
    expect(screen.getByText('截面双轴镜像')).toBeInTheDocument();

    // 关键路径标记
    const criticalBadges = screen.getAllByText('CRITICAL');
    expect(criticalBadges.length).toBeGreaterThanOrEqual(1);

    // 管道锁透明度
    expect(screen.getByText('调度引擎就绪 (IDLE)')).toBeInTheDocument();
  });

  it('switches to CPM Gantt and SLA Governance tab and displays predictions', async () => {
    render(<Pipeline />);

    await waitFor(() => {
      expect(screen.getByText('CPM 甘特与 SLA 履约治理')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('CPM 甘特与 SLA 履约治理'));

    // 验证 SLA 预测指标
    expect(screen.getByText('履约合规 (COMPLIANT)')).toBeInTheDocument();
    expect(screen.getByText('219s')).toBeInTheDocument();
    expect(screen.getAllByText('99.99%').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/图论 CPM 关键路径甘特时序/)).toBeInTheDocument();
  });

  it('switches to History and Daemons tab, verifies scheduled triggers and opens traceback modal', async () => {
    render(<Pipeline />);

    // 等待 DAG 数据加载完成
    await waitFor(() => {
      expect(screen.getByText('行情增量同步')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('任务执行历史与常驻守护'));

    // 等待历史 tab 内容呈现
    await waitFor(() => {
      expect(screen.getByTestId('trace-btn-100')).toBeInTheDocument();
    });

    // 验证 4 大常驻守护计划
    expect(screen.getByText('盘前交易日历与除权除息初始化')).toBeInTheDocument();
    expect(screen.getByText('午间行情与成交量轻量校验')).toBeInTheDocument();
    expect(screen.getByText('盘后核心量化生产流水线 (11 步 DAG)')).toBeInTheDocument();
    expect(screen.getByText('周末全量坏道巡检与因子漂移基线更新')).toBeInTheDocument();

    // 验证历史记录表格与错误信息
    expect(screen.getByText('Missing column error in daily_bar')).toBeInTheDocument();

    // 点击堆栈诊断按钮
    fireEvent.click(screen.getByTestId('trace-btn-100'));

    // 堆栈弹窗应打开
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('数据契约 / Schema 缺失异常')).toBeInTheDocument();
    expect(screen.getAllByText(/Missing column error in daily_bar/)[0]).toBeInTheDocument();

    // 关闭弹窗
    fireEvent.click(screen.getByLabelText('关闭弹窗'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('supports breakpoint resumption click to trigger pipeline rerun', async () => {
    render(<Pipeline />);

    // 等待 DAG 数据加载完成
    await waitFor(() => {
      expect(screen.getByText('行情增量同步')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('任务执行历史与常驻守护'));

    // 失败任务具有"断点续跑"按钮
    await waitFor(() => {
      expect(screen.getByTestId('resume-btn-100')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('resume-btn-100'));

    // 验证 opsApi.dagRerun 被调用
    await waitFor(() => {
      expect(opsApi.dagRerun).toHaveBeenCalledWith(
        expect.objectContaining({
          trade_date: '2026-09-29',
          resume_from_step: 'validate',
        }),
        expect.anything()
      );
    });
  });
});
