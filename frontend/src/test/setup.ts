/** Vitest 全局初始化（C-3 修复，2026-10-02）。 */
// jest-dom 的匹配器（toBeInTheDocument / toHaveClass 等）。
// 走 `/vitest` 子入口：它会把匹配器注册到 Vitest 的 `expect` 上，
// 用默认入口则需手工 `expect.extend`，且在 `globals: true` 下容易漏挂。
import '@testing-library/jest-dom/vitest';

/**
 * jsdom 未实现 `ResizeObserver`，而项目内**图表组件普遍依赖它**
 * （`utils/useChart.ts`、`EtfDetail`、`MarketHeatmap`、`Portfolio` …）。
 * 不补这个桩，任何渲染图表的测试都会以 `ResizeObserver is not defined` 崩掉。
 *
 * ⚠️ 这里只提供**最小可用**实现（不触发回调）—— 它让组件能挂载，
 * 但**不能**用来断言"容器变化时图表会 resize"（jsdom 没有布局引擎，
 * 根本不会产生尺寸变化事件）。需要断言 `observe` 调用本身的测试，
 * 应在自己的用例里 `vi.stubGlobal('ResizeObserver', <带 spy 的假类>)` 覆盖本桩。
 */
class NoopResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver;
}
