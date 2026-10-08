/**
 * 数据中心 —— 「修复缺漏后，已修复的标的必须从质量表消失」回归测试。
 *
 * ## 被守的缺陷（用户实测报告，2026-10-06）
 *
 * 截图现场：左侧同步日志明明白白写着
 *   `[INFO] 开始修复K线缺漏：50 只，回补至 2026-09-30`
 *   `[INFO] 修复 002656.SZ ... 4978 行成功`
 *   `[INFO] 修复 002731.SZ ... 5542 行成功`
 * 任务确实跑完了（右上来路显示「任务执行中」已结束），但右侧「数据质量与缺漏」
 * 表里 002656.SZ / 002731.SZ 这些**刚被修好的标的仍然挂着**（366 天 / 84 天）。
 *
 * 后端已在 `_run_repair` 收尾调用 `invalidate_stats_cache()`，故**不是**缓存问题；
 * 缺陷在**前端**：任务由「运行中」翻转为「已停止」的那一次，必须重新拉取
 * `/quality`（以及其余统计面板）。本文件把这条契约钉死：
 *
 *   · 任务结束（running true→false）⇒ `/quality` 必须被**再次**请求；
 *   · 且新一次请求返回的空缺列表必须**真的**渲染出来（旧标的消失）。
 *
 * ## 为什么必须单独一个文件
 *
 * 既有 `qualityLinks.test.tsx` 只测「静态渲染时链接是否正确」，
 * `syncAlertWiring.test.tsx` 只测告警条文案；两者都**没有**覆盖
 * 「任务结束 → 质量面板刷新」这条时序契约。本文件专测它。
 *
 * ## 等待标记（证伪盲区，判据 31）
 *
 * `settle()` 等的是页头「数据中心」——与被守的契约**无关**。若等的是
 * 「修复选定缺漏」之类按钮，一旦注入缺陷破坏该按钮，所有用例都会卡在等待阶段。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { DataOverview, QualityResult } from '@/types/datacenter';

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

vi.mock('@/components/DataFreshness', () => ({ default: () => null }));
vi.mock('./TextDataPanel', () => ({ default: () => null }));
vi.mock('./TrainPanel', () => ({ default: () => null }));
vi.mock('./PitAndCorporateActionsPanel', () => ({ default: () => null }));
vi.mock('./TradabilityTimelinePanel', () => ({ default: () => null }));

const OVERVIEW: DataOverview = {
  storage_bytes: 1e9, storage_gb: 1, covered_total: 5208, covered_with_data: 5209,
  dataset_count: 12, last_sync: '2026-09-30 18:37:22', last_sync_source: 'pipeline',
  akshare_health: 'green', akshare_message: 'AKShare 连接正常，最近同步成功',
  disk_usage_percent: 60, daily_bar_range: ['2022-01-04', '2026-09-30'], last_job: null,
};

/** 修复**前**：这两只（截图现场）都挂在表里。 */
const QUALITY_BEFORE: QualityResult = {
  checked: 100, total_missing_days: 450, affected_symbols: 2,
  items: [
    { dataset: 'daily_bar', symbol: '002656.SZ', missing_days: 366,
      first_missing: '2015-03-16', last_missing: '2026-09-30', sample: [] },
    { dataset: 'daily_bar', symbol: '002731.SZ', missing_days: 84,
      first_missing: '2017-09-06', last_missing: '2026-09-30', sample: [] },
  ],
};

/** 修复**后**：同一批标的已补齐 ⇒ 列表为空。 */
const QUALITY_AFTER: QualityResult = {
  checked: 100, total_missing_days: 0, affected_symbols: 0, items: [],
};

/** 运行中的同步状态（截图左上的「任务执行中」）。 */
function runningStatus() {
  return {
    task_id: 't-1', running: true, mode: 'repair', total: 50, done: 12, percent: 24,
    current: '修复 002731.SZ 历史K线 (12/50)', error: null, elapsed_ms: 1000,
    logs: [{ ts: '22:10:48', level: 'INFO', message: '开始修复K线缺漏：50 只，回补至 2026-09-30' }],
  };
}

/** 结束后的同步状态。 */
function stoppedStatus() {
  return { ...runningStatus(), running: false, done: 50, percent: 100, current: '' };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/data']}>
      <DataCenter />
    </MemoryRouter>,
  );
}

/** 等页头——与被守契约无关，注入缺陷不会让所有用例一起挂在等待阶段（判据 31）。 */
async function settle() {
  await screen.findByText('数据中心');
}

beforeEach(() => {
  localStorage.clear();
  Object.values(h).forEach((f) => f.mockReset());
  h.overview.mockResolvedValue(OVERVIEW);
  h.datasets.mockResolvedValue({ items: [] });
  h.logs.mockResolvedValue({ items: [] });
  h.taskStats.mockResolvedValue({ points: [] });
  h.autoStatus.mockResolvedValue({ enabled: false, time: '15:45', today_done: false });
  h.instruments.mockResolvedValue({
    items: [], total: 0, returned: 0, truncated: false, limit: 50,
  });
  h.sync.mockResolvedValue({
    started: true, task_id: 't-1', mode: 'repair', total: 50, resume: false, skipped: 0,
  });
});

/* ---------------- 用例 ---------------- */

describe('修复缺漏后质量表必须刷新', () => {
  it('任务 running→stopped 后再次请求 /quality，且已修复标的从表中消失', async () => {
    // 首轮：面板返回"修复前"的缺漏列表
    h.quality.mockResolvedValue(QUALITY_BEFORE);
    // 首次 status：任务已在运行（触发轮询循环）
    h.status.mockResolvedValue(runningStatus());

    renderPage();
    await settle();

    // 前提：修复前的标的确实渲染出来了（否则本用例没有区分力）
    await screen.findByRole('link', { name: '002656.SZ' });
    expect(screen.getByRole('link', { name: '002731.SZ' })).toBeInTheDocument();

    const qualityCallsBefore = h.quality.mock.calls.length;

    // 任务结束：下一次轮询返回 running=false，且 /quality 改为返回"修复后"
    h.status.mockResolvedValue(stoppedStatus());
    h.quality.mockResolvedValue(QUALITY_AFTER);

    // 轮询基础间隔 1.5s ⇒ 给足时间让「结束 → loadAll」这一拍发生
    await waitFor(
      () => expect(h.quality.mock.calls.length).toBeGreaterThan(qualityCallsBefore),
      { timeout: 6000 },
    );

    // 契约核心：新数据必须真的渲染出来 —— 已修复标的消失
    await waitFor(
      () => expect(screen.queryByRole('link', { name: '002656.SZ' })).toBeNull(),
      { timeout: 6000 },
    );
    expect(screen.queryByRole('link', { name: '002731.SZ' })).toBeNull();
    // 空列表必须有如实文案（不得留一片空白让人猜）
    await screen.findByText('未发现缺失交易日 ✓');
  });

  it('手动「修复选定缺漏」确认后，任务结束同样触发质量面板刷新', async () => {
    h.quality.mockResolvedValue(QUALITY_BEFORE);
    h.status.mockResolvedValue({ ...stoppedStatus(), running: false });

    renderPage();
    await settle();
    await screen.findByRole('link', { name: '002656.SZ' });

    // 全选 → 修复选定缺漏 → 二次确认 → 触发 sync（走 startSync，进入运行态）
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    fireEvent.click(screen.getByRole('button', { name: /修复选定缺漏/ }));
    fireEvent.click(await screen.findByRole('button', { name: '确认修复' }));

    await waitFor(() => expect(h.sync).toHaveBeenCalledTimes(1));
    // 启动后 status 应进入 running，从而开启轮询
    h.status.mockResolvedValue(runningStatus());
    const callsAfterStart = h.quality.mock.calls.length;

    // 任务结束
    h.status.mockResolvedValue(stoppedStatus());
    h.quality.mockResolvedValue(QUALITY_AFTER);

    await waitFor(
      () => expect(h.quality.mock.calls.length).toBeGreaterThan(callsAfterStart),
      { timeout: 6000 },
    );
    await waitFor(
      () => expect(screen.queryByRole('link', { name: '002656.SZ' })).toBeNull(),
      { timeout: 6000 },
    );
    // 轮询基础间隔 1.5s 且用真实定时器，vitest 默认单用例 5s 超时过紧
  }, 20000);

  /**
   * 兜底护栏（QA 场景 B）：任务结束后 `status` 若仍返回**非 null** 的 `task_id`
   * 且 `running:false`，轮询必须**停止**，不得凭「task_id 没变」一直空转。
   *
   * 被守的契约：轮询的退出条件是**任务语义**「running=false 即结束」，
   * 而不是「task_id 是否变化」这一本仓库后端的具体实现细节
   * （后端正常会在收尾把 task_id 置 None，但那是实现，不是语义）。
   *
   * 区分力：若把 tick 里的清理条件写回 `st.task_id !== ownTaskId`
   * （去掉 `!st.running`），本用例必红——status 调用次数会持续增长。
   */
  it('任务结束后 status 仍返回非 null task_id 且 running=false 时，轮询必须停止', async () => {
    h.quality.mockResolvedValue(QUALITY_BEFORE);
    // 全程 task_id 恒为 't-1'，running 在任务结束后维持 false（模拟中间态）
    h.status.mockResolvedValue({ ...stoppedStatus(), task_id: 't-1', running: false });

    renderPage();
    await settle();
    await screen.findByRole('link', { name: '002656.SZ' });

    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    fireEvent.click(screen.getByRole('button', { name: /修复选定缺漏/ }));
    fireEvent.click(await screen.findByRole('button', { name: '确认修复' }));
    await waitFor(() => expect(h.sync).toHaveBeenCalledTimes(1));

    // 先等轮询确实启动过（ownTaskId 驱动）：status 至少再多调一次
    const statusAtStart = h.status.mock.calls.length;
    await waitFor(
      () => expect(h.status.mock.calls.length).toBeGreaterThan(statusAtStart),
      { timeout: 6000 },
    );

    // 让轮询跑一会儿使其稳定在「已结束」并退出，记录一个基准
    await new Promise((r) => setTimeout(r, 2500));
    const frozen = h.status.mock.calls.length;

    // 再持续推进 4s（远超 1.5s 基础间隔）：轮询若未停止，次数必然继续增长
    await new Promise((r) => setTimeout(r, 4000));
    expect(h.status.mock.calls.length).toBe(frozen);
  }, 20000);
});
