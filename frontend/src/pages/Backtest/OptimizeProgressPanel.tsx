/**
 * 参数寻优**进度与取消**面板（P8-c）。
 *
 * ## 为什么要有它
 *
 * 后端 `POST /backtest/strategy-run` 带 `optimize_params` 时前端超时 600s
 * （后端 660s），寻优 = 网格组合数 × 单次回测。P8-c 给后端加了
 * **网格级取消 + 检查点续跑**，但在此之前用户面对的是一个"转圈 10 分钟、
 * 无法中止、也不知道跑到哪了"的黑盒。
 *
 * ## 面板展示什么（全部来自后端真实数据，前端不编造）
 *
 * | 展示项 | 来源 | 缺失时 |
 * |---|---|---|
 * | 已算 N / 共 M 组 | `opt-status` 的 `n_done` / `total` | total 为 null 时只显示 N |
 * | 进度条 | `n_done / total` | 比例不可得则**不画进度条**（不假装） |
 * | 已用时 | 本地计时（仅界面参考） | — |
 * | 取消按钮 | `POST /strategy-run/cancel` | 非 grid 方法**不显示** |
 *
 * ## 两条刻意的取舍，写在界面上而不是藏起来
 *
 * 1. **取消粒度是一个参数组合**：后端不动回测引擎日循环，所以当前组合会跑完
 *    才停。界面必须说清"将在当前参数组合跑完后停止"，否则用户会以为点了没反应。
 * 2. **只对 grid 显示取消**：后端取消钩子只接在网格循环上（ga / optuna 是各自
 *    独立的循环，未接入）。给它们显示一个按不动的按钮是更糟的欺骗 —— 所以不显示。
 */
import { useEffect, useState } from 'react';
import type { OptimizationProgress } from '@/api/strategyBacktest';

/** 已用时（mm:ss）。仅界面参考，与后端进度无因果关系。 */
function useElapsedSeconds(active: boolean): number {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t0 = Date.now();
    setSecs(0);
    const id = setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 1000);
    // ⚠️ 必须清定时器：本仓有过 setInterval 未清导致轮询泄漏的事故
    // （pages/FactorStudio/index.tsx，审计 C.4）。
    return () => clearInterval(id);
  }, [active]);
  return secs;
}

function mmss(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function OptimizeProgressPanel({
  progress, cancelling, onCancel, canCancel,
}: {
  progress: OptimizationProgress | null;
  cancelling: boolean;
  onCancel: () => void;
  /** false ⇒ 不显示取消按钮（非 grid 寻优，后端未接入取消） */
  canCancel: boolean;
}) {
  const elapsed = useElapsedSeconds(true);

  const total = progress?.total ?? null;
  const done = progress?.n_done ?? 0;
  // 只在**两个数都有**且 total > 0 时才算比例；否则不画进度条，不假装
  const pct = total && total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
  const cancelRequested = progress?.cancel_requested ?? false;

  return (
    <div className="rounded-lg border border-brand-200 bg-brand-50/40 px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-ink">
          <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10"
              stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
          </svg>
          参数寻优进行中
        </span>
        <span className="flex items-center gap-3 text-2xs text-ink-secondary">
          <span className="num">已用时 {mmss(elapsed)}</span>
          {canCancel && (
            <button
              type="button"
              onClick={onCancel}
              disabled={cancelling || cancelRequested}
              className="rounded-md border border-danger/40 bg-surface px-2.5 py-1 text-2xs text-danger
                transition-colors hover:bg-danger-bg disabled:cursor-not-allowed disabled:opacity-60"
            >
              {cancelRequested ? '正在停止…' : (cancelling ? '提交取消中…' : '取消寻优')}
            </button>
          )}
        </span>
      </div>

      {/* 进度：有 total 才画条；否则只报"已算 N 组"（不编造比例） */}
      <div className="flex items-center gap-3">
        <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-hair">
          <div
            className="h-full rounded-full bg-brand-500 transition-all duration-500"
            style={{ width: `${pct ?? 0}%` }}
          />
        </div>
        <span className="num shrink-0 text-2xs text-ink-secondary">
          {total != null
            ? `${done} / ${total} 组${pct != null ? ` · ${pct}%` : ''}`
            : `${done} 组已完成`}
        </span>
      </div>

      <p className="mt-2 text-2xs text-ink-muted">
        {cancelRequested
          ? '已请求取消：当前参数组合会跑完才停止，其余组合将被跳过。'
            + '已完成组合的进度已保存 —— 用相同参数重跑即可从断点继续。'
          : canCancel
            ? '每个参数组合跑完即保存进度。取消的粒度是「一个参数组合」'
              + '（当前组合会跑完），之后可用相同参数重跑从断点继续。'
            : '该寻优方法暂不支持中途取消（取消仅接在网格寻优上）。'}
      </p>
    </div>
  );
}
