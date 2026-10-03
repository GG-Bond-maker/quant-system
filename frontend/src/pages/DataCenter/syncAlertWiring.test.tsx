/**
 * 数据中心告警条 —— 「接口状态 / 任务结论」语义拆分 + 建议派生的接线回归（2026-10-04）。
 *
 * ## 被守的缺陷
 *
 * 2026-09-30 实测：一次 `--codes 000007` 的单标的重跑被 `step_validate` 的**纯比例**
 * 容差判为 100% 丢失（`n_expected=1` ⇒ 容差 0.05 只），失败行落进 `data_jobs` 后
 * `/overview` 渲染成「最近同步异常：…」，而**前端对任何非 green 都硬编码**
 * 「数据源可能不可用，请检查网络/代理或稍后重试同步」⇒ 用户读到的结论是
 * 「市场级数据源故障」，与真实成因（单标的校验未通过）完全相反，且该结论在国庆
 * 假期里挂了 4 天。
 *
 * ## 被守的四件事
 * 1. 告警条的建议必须**消费后端派生的 advice**，不再出现硬编码的「数据源可能不可用」；
 * 2. 任务结论独立成行，并披露**规模 + 规模来源**（记录值 / 由错误文本推断 / 未知）；
 * 3. 陈旧告警必须可见（「陈旧 · N 天未复核」）；
 * 4. 「重新校验并补齐」必须走既有二次确认（repair 模式），不得是假可交互。
 *
 * ## 等待标记的选取（证伪盲区）
 * `settle()` 等的是页头 `数据中心`（与上面四条契约**无关**）。若图省事等
 * 「最近一次任务：」，一旦注入缺陷破坏任务行，**所有**用例都会卡在等待阶段，
 * 呈现全红而非"哪条契约坏了"——这正是本仓库记过的证伪盲区。
 *
 * ⚠️ 本文件不得出现 hex 字面量：`style:check` 会扫 `src/pages` 下的 `.tsx`（含测试）。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { DataOverview, LastJob } from '@/types/datacenter';

import DataCenter from './index';

/* ---------------- 桩 ---------------- */

const h = vi.hoisted(() => ({
  setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn(),
  overview: vi.fn(), datasets: vi.fn(), quality: vi.fn(), logs: vi.fn(),
  taskStats: vi.fn(), status: vi.fn(), autoStatus: vi.fn(), sync: vi.fn(),
  instruments: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({ setOption: h.setOption, dispose: h.dispose, resize: h.resize }),
}));

vi.mock('@/api/datacenter', () => ({
  datacenterApi: {
    overview: h.overview, datasets: h.datasets, quality: h.quality, logs: h.logs,
    taskStats: h.taskStats, status: h.status, autoStatus: h.autoStatus,
    sync: h.sync, instruments: h.instruments,
    cancel: vi.fn(), task: vi.fn(), fetch: vi.fn(), autoToggle: vi.fn(),
    textStatus: vi.fn(), textImport: vi.fn(), textBuildFactor: vi.fn(),
    mirrorStatus: vi.fn(), mirrorRebuild: vi.fn(),
    trainReadiness: vi.fn(), trainStatus: vi.fn(), trainStart: vi.fn(),
    trainCancel: vi.fn(),
  },
}));

vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: (sel: (s: { user: { role: string } }) => unknown) =>
    sel({ user: { role: 'researcher' } }),
}));
vi.mock('@/components/RequireAuth', () => ({ hasMinimumRole: () => true }));

// 与本用例无关的重组件：空渲染，避免被各自的数据依赖带偏（沿用 EtfCenter.test.tsx 的做法）
vi.mock('@/components/DataFreshness', () => ({ default: () => null }));
vi.mock('./TextDataPanel', () => ({ default: () => null }));
vi.mock('./TrainPanel', () => ({ default: () => null }));

/* ---------------- 夹具（对齐后端实测值） ---------------- */

/** 后端对 2026-09-30 那条历史行的真实派生结果（`/overview` 的 last_job）。 */
const LEGACY_JOB: LastJob = {
  id: 20,
  job_type: 'daily_pipeline',
  status: 'FAILED',
  verdict: 'failed_subset',
  trade_date: '2026-09-30',
  current_step: 'validate',
  finished_at: '2026-09-30 18:37:22',
  duration_ms: 173715,
  error_message: 'ValueError: validate failed: 昨有今无 1/1 只（阈值 5%）: 000007',
  codes_count: null,
  job_scope: 'subset',
  scope_source: 'message',
  scope_note: '代码集 ≥ 1 只（由错误文本「昨有今无 1/1 只」推断，为该值下界；该行未记录规模元数据）',
  age_days: 4,
  stale: true,
  stale_note: '该结论已 4 天未复核（阈值 3 天）',
  advice: '数据校验未通过（数据链路问题，与网络/代理无关）；'
    + '本次为小规模子集任务，不构成市场级同步异常的证据',
};

function overviewWith(lastJob: LastJob | null): DataOverview {
  return {
    storage_bytes: 1e9, storage_gb: 1, covered_total: 5208, covered_with_data: 5209,
    dataset_count: 12, last_sync: '2026-09-30 18:37:22', last_sync_source: 'pipeline',
    akshare_health: lastJob && lastJob.verdict !== 'success' ? 'yellow' : 'green',
    akshare_message: lastJob && lastJob.verdict !== 'success'
      ? '最近一次流水线校验未通过（非数据源/网络故障）：'
        + 'ValueError: validate failed: 昨有今无 1/1 只（阈值 5%）: 000007'
      : 'AKShare 连接正常，最近同步成功',
    disk_usage_percent: 60,
    daily_bar_range: ['2022-01-04', '2026-09-30'],
    last_job: lastJob,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/data']}>
      <DataCenter />
    </MemoryRouter>,
  );
}

/**
 * 等到页头渲染出来。页头与被守的四条契约**无关**，故注入缺陷不会让所有用例
 * 一起卡在等待阶段（否则缺陷互相掩盖、无法定位）。
 */
async function settle() {
  await screen.findByText('数据中心');
}

beforeEach(() => {
  localStorage.clear();
  Object.values(h).forEach((f) => f.mockReset());
  h.datasets.mockResolvedValue({ items: [] });
  h.quality.mockRejectedValue(new Error('本用例不关心质量面板'));
  h.logs.mockResolvedValue({ items: [] });
  h.taskStats.mockResolvedValue({ points: [] });
  h.status.mockResolvedValue({
    task_id: null, running: false, mode: null, total: 0, done: 0, percent: 0,
    current: '', error: null, elapsed_ms: 0, logs: [],
  });
  h.autoStatus.mockResolvedValue({ enabled: false, time: '15:45', today_done: false });
  h.instruments.mockResolvedValue({
    items: [], total: 0, returned: 0, truncated: false, limit: 50,
  });
  h.sync.mockResolvedValue({
    started: true, task_id: 't-1', mode: 'repair', total: 0, resume: false, skipped: 0,
  });
});

/* ---------------- 用例 ---------------- */

describe('接口状态与任务结论分离', () => {
  it('建议来自后端派生，不得再出现硬编码的「数据源可能不可用」', async () => {
    h.overview.mockResolvedValue(overviewWith(LEGACY_JOB));
    renderPage();
    await settle();

    // 后端 advice 必须被渲染出来（这是"与网络/代理无关"的唯一来源）
    await screen.findByText(/数据校验未通过（数据链路问题，与网络\/代理无关）/);
    // 硬编码的网络建议必须消失
    expect(screen.queryByText(/数据源可能不可用/)).toBeNull();
  });

  it('校验类失败不再写成「同步异常」', async () => {
    h.overview.mockResolvedValue(overviewWith(LEGACY_JOB));
    renderPage();
    await settle();

    const banner = await screen.findByText(/API 接口状态/);
    expect(banner.textContent).toContain('最近一次流水线校验未通过（非数据源/网络故障）');
    expect(banner.textContent).not.toContain('最近同步异常');
  });

  it('任务结论独立成行，并披露规模、规模来源与陈旧天数', async () => {
    h.overview.mockResolvedValue(overviewWith(LEGACY_JOB));
    renderPage();
    await settle();

    const row = (await screen.findByText(/最近一次任务：/)).closest('div');
    expect(row).not.toBeNull();
    const text = (row as HTMLElement).textContent ?? '';
    expect(text).toContain('daily_pipeline');
    expect(text).toContain('2026-09-30');
    expect(text).toContain('失败');
    expect(text).toContain('（小规模子集）');
    // 规模与**来源**必须披露（推断值不得被读成记录值）
    expect(text).toContain('由错误文本「昨有今无 1/1 只」推断');
    expect(text).toContain('该行未记录规模元数据');
    // 陈旧必须可见
    expect(text).toContain('陈旧');
    expect(text).toContain('4 天未复核');
  });

  it('成功态不渲染任务结论行（避免正常态多一行噪音）', async () => {
    h.overview.mockResolvedValue(overviewWith({
      ...LEGACY_JOB, status: 'SUCCESS', verdict: 'success', stale: false,
      age_days: 0, advice: '', error_message: '', scope_note: '本次代码集 5,212 只',
      codes_count: 5212, job_scope: 'market', scope_source: 'recorded',
    }));
    renderPage();
    await settle();

    await screen.findByText(/API 接口状态/);
    expect(screen.queryByText(/最近一次任务：/)).toBeNull();
  });
});

describe('任务结论详情与处置入口', () => {
  it('「查看详情」打开详情面板，展示规模来源与错误原文', async () => {
    h.overview.mockResolvedValue(overviewWith(LEGACY_JOB));
    renderPage();
    await settle();

    fireEvent.click(await screen.findByRole('button', { name: '查看详情' }));
    const dialog = await screen.findByText('最近一次任务详情');
    expect(dialog).toBeTruthy();
    await screen.findByText('由错误文本推断（为该值下界）');
    await screen.findByText('错误原文（后端不改写内容，仅截断到 300 字符）');
    expect(screen.getAllByText(LEGACY_JOB.error_message).length).toBeGreaterThan(0);
  });

  it('「重新校验并补齐」走既有二次确认，确认后以 repair 模式触发同步', async () => {
    h.overview.mockResolvedValue(overviewWith(LEGACY_JOB));
    renderPage();
    await settle();

    fireEvent.click(await screen.findByRole('button', { name: '重新校验并补齐' }));
    // 高危操作必须二次确认（复用既有 Modal），不得点了就悄悄发请求
    const confirmTitle = await screen.findByText('确认修复 K 线缺漏？');
    expect(confirmTitle).toBeTruthy();
    expect(h.sync).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认修复' }));
    await waitFor(() => expect(h.sync).toHaveBeenCalledTimes(1));
    expect(h.sync.mock.calls[0][0]).toBe('repair');
  });
});
