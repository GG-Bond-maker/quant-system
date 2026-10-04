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
 *
 * ## 🔴 固定 pool + 串行执行（2026-10-04，门禁非确定性修复）
 *
 * 此前 `test` 未固定 pool ⇒ vitest 用默认多 worker 并行，实测**每次丢不同的测试
 * 文件**（磁盘 16 个 `src/**\/*.test.{ts,tsx}`，却只跑 15 个，且 run 之间丢的不是
 * 同一个），运行期伴 1 个未处理错误
 * `EPERM: operation not permitted, open '…\Temp\…\web\<hash>'`。
 *
 * 根因：本机宿主对临时目录写入做了 fs shim（`node-brokered-fs-shim.cjs`）；多个
 * worker **并发**写同一个 `<hash>` 临时文件时，其中一个被拒 ⇒ 该 worker 崩溃、它
 * 负责的那个测试文件被静默丢弃。`--no-file-parallelism` 能稳定跑满 16 文件，正
 * 说明问题出在**并行**而非某个具体用例。
 *
 * 处置：`pool: 'forks'` + `fileParallelism: false` —— 固定用 fork 进程池，且**一次
 * 只跑一个测试文件**，从根上消除多 worker 并发写临时文件的竞争。这与实测可稳定跑满
 * 16 文件的 `npx vitest run --no-file-parallelism` **逐字节等价**，只是写进配置后
 * `npx vitest run` / `npm test` 默认即生效，不再依赖谁记得加 flag。
 *
 * ⚠️ 为什么不用 `poolOptions.forks.singleFork: true`：实测它把**全部**测试文件塞进
 * 同一个 fork 进程且**不重置**文件间的隔离 ⇒ 模块级单例（zustand store）与 DOM 跨
 * 文件残留，`16 文件 / 143 tests` 里 **53 个用例** 因互相污染而失败（如
 * `getByRole` 撞到上一个文件遗留的同名按钮报 `getMultipleElementsFound`）。
 * `fileParallelism: false` 仍保持**每个文件独立 worker + 隔离重置**，只是串行执行。
 *
 * ⚠️ 串行只是"让门禁不再丢文件"，**不能**证明"没丢"。真正的护栏是
 * `scripts/check-test-count.mjs`（比对「磁盘上的测试文件数」与「vitest 实际执行数」，
 * 不等即失败）—— 已接进 `npm test`。见该脚本头注释。
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
      // 固定 fork 进程池 + 串行执行：消除多 worker 并发写临时文件的 EPERM 竞争（见文件头）
      pool: 'forks',
      fileParallelism: false,
    },
  }),
);
