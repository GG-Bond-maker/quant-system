/**
 * 应用级错误边界（P2-1）：捕获渲染期异常，避免任一子组件抛错导致整站白屏。
 *
 * 语义边界：React 错误边界只捕获其**子树**在渲染/生命周期中的异常，不捕获
 * 事件处理、异步回调（Promise）与 SSR 错误——因此挂在应用根即可兜住全部路由
 * 页面与全局组件的渲染崩溃；事件/异步异常仍由各页面的 ApiError 处理与
 * try/catch 负责。
 *
 * 不引入任何新依赖；降级 UI 复用与全站一致的 Tailwind token。
 */
import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface ErrorBoundaryProps {
  /** 被保护的子树 */
  children: ReactNode;
  /** 可选：自定义降级 UI（缺省用内置面板） */
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 保留原始异常与组件栈到控制台，便于定位（生产可接入远端上报）
    console.error('[ErrorBoundary] 渲染异常:', error, info.componentStack);
  }

  private handleRetry = (): void => {
    this.setState({ error: null });
  };

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback;

    return (
      <div className="flex min-h-screen items-center justify-center bg-surface px-4">
        <div className="w-full max-w-md rounded-lg border border-hair bg-white p-6 text-center shadow-sm">
          <h1 className="text-base font-bold text-ink">页面出现异常</h1>
          <p className="mt-2 text-xs text-ink-muted">
            该模块渲染失败，已阻止异常扩散以保护其余页面。可尝试重试或刷新。
          </p>
          <pre className="mt-3 max-h-32 overflow-auto rounded bg-slate-50 p-2 text-left text-2xs text-ink-secondary">
            {error.message || String(error)}
          </pre>
          <div className="mt-4 flex justify-center gap-2">
            <button
              type="button"
              onClick={this.handleRetry}
              className="rounded border border-hair bg-white px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-brand-200 hover:text-brand-600">
              重试
            </button>
            <button
              type="button"
              onClick={this.handleReload}
              className="rounded bg-brand-600 px-3 py-1.5 text-xs text-white transition-opacity hover:opacity-90">
              刷新页面
            </button>
          </div>
        </div>
      </div>
    );
  }
}
