import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

/**
 * 前端单测配置（C-3 修复，2026-10-02）。
 *
 * 为什么**另开一个**配置文件而不是把 `test` 塞进 `vite.config.ts`：
 *  - `vite.config.ts` 是**构建**配置（产物形状、chunk 上限、dev 代理），
 *    被 CI 的 `npm run build` 使用；测试环境（jsdom / setupFiles / include）
 *    与它无关，混在一起会让「构建配置」被测试细节污染。
 *  - 这里用 `mergeConfig` 继承构建配置 ⇒ **`@` 别名只有一处定义**，
 *    不会出现「构建能解析、测试解析不到」的漂移。
 *
 * 环境：jsdom（组件渲染需要 DOM）。
 * ⚠️ jsdom **没有布局引擎** —— `height` / `overflow` 这类几何量在测试里恒为 0，
 * 因此「高度链是否真的撑开」**无法**由渲染断言证明。本仓库对该类"只有真实渲染
 * 才暴露"的问题采取的是**结构不变量断言**（见 `src/test/layoutContract.test.tsx`
 * 的文件头说明），而不是假装 jsdom 能验证布局。
 */
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      // 测试不应依赖开发机上跑着的后端/Redis
      restoreMocks: true,
    },
  }),
);
