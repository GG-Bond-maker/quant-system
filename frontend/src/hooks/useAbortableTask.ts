/**
 * 长任务在途请求中断 hook（审计 P2-4）。
 *
 * 背景：多个长任务页面（60s ~ 300s 级请求）在组件卸载（切路由 / 切 Tab）时
 * 并不取消在途请求。浏览器对 HTTP/1.1 每域名只有 6 个并发连接，长任务会把
 * 槽位占满，饿死后续请求——这才是本项改造的主要收益。
 *
 * ⚠️ 认知前提：浏览器侧 abort 只是断开请求 / 取消 Promise，
 * **已经在后端开始执行的任务不会停止**（FastAPI handler 照跑到底）。
 * 因此 UI 文案只能写「中止等待」/「停止等待」，**不得宣称「已取消任务」**。
 * 本 hook 的另一收益是避免已卸载组件 setState 造成状态污染。
 *
 * 范式来源：`pages/Backtest/index.tsx` 既有实现（begin/finish 即其
 * `abortRef.current?.abort()` + `abortRef.current === ctrl` 守卫的等价封装）。
 *
 * 用法：
 * ```tsx
 * const task = useAbortableTask();
 * const run = async () => {
 *   const ctrl = task.begin();            // ① 新一轮请求前中断上一轮
 *   setRunning(true); setErr(null);
 *   try {
 *     const r = await api.run(req, ctrl.signal);
 *     if (ctrl.signal.aborted) return;    // ② 已中断：旧响应不得覆盖新结果
 *     setResult(r);
 *   } catch (e) {
 *     if (ctrl.signal.aborted) return;    // ③ 中断不是错误，不得弹给用户
 *     setErr(...);
 *   } finally {
 *     if (task.finish(ctrl)) setRunning(false);  // ④ 仅最新请求可关 loading
 *   }
 * };
 * ```
 * 卸载自动中断由 hook 内部完成（等价 `useEffect(() => () => abortRef.current?.abort(), [])`）。
 *
 * 一个组件若同时存在多条互不相关的长任务（如「扫描」与「血缘」），
 * 应各用一个 hook 实例，避免互相中断。
 */
import { useCallback, useEffect, useMemo, useRef } from 'react';

export interface AbortableTask {
  /** 开始新一轮请求：中断上一轮在途请求，返回本轮 controller。 */
  begin: () => AbortController;
  /** 结束本轮请求：仅当它仍是当前最新请求时返回 true（调用方据此决定是否关闭 loading）。 */
  finish: (ctrl: AbortController) => boolean;
  /** 本轮是否仍是最新且未中断（可选，用于需要显式判定的分支）。 */
  isLatest: (ctrl: AbortController) => boolean;
  /** 主动中断在途请求（供「中止等待」按钮使用）。 */
  abort: () => void;
}

export function useAbortableTask(): AbortableTask {
  const abortRef = useRef<AbortController | null>(null);

  // 卸载（切路由 / 切 Tab）时中断在途长任务请求，释放浏览器并发连接
  useEffect(() => () => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const begin = useCallback((): AbortController => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    return ctrl;
  }, []);

  const finish = useCallback(
    (ctrl: AbortController): boolean => abortRef.current === ctrl,
    [],
  );

  const isLatest = useCallback(
    (ctrl: AbortController): boolean => abortRef.current === ctrl && !ctrl.signal.aborted,
    [],
  );

  const abort = useCallback((): void => { abortRef.current?.abort(); }, []);

  // 返回稳定引用：四个方法均为空依赖 useCallback，故可安全放进 useEffect/useCallback 依赖
  return useMemo(() => ({ begin, finish, isLatest, abort }), [begin, finish, isLatest, abort]);
}
