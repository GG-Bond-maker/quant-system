/**
 * 前端测试「文件数」门禁 —— vitest 非确定性回归护栏（2026-10-04）。
 *
 * ## 🔴 为什么需要它（它拦的是真发生过的假绿灯）
 *
 * `vitest.config.ts` 此前未固定 pool，默认多 worker 并行时实测**每次丢不同的测试
 * 文件**：磁盘上有 16 个 `src/**\/*.test.{ts,tsx}`，`npx vitest run` 却只跑 15 个，
 * 且 run 之间丢的不是同一个（run1 丢 `EtfDetail/IndustryPie.test.tsx` ⇒ 131 tests，
 * run2 丢 `DataCenter/syncAlertWiring.test.tsx` ⇒ 137 tests）。运行期伴 1 个未处理错误
 * `EPERM: operation not permitted, open '…\Temp\…\web\<hash>'`（本机宿主 fs shim 在
 * 多 worker 并发写同一临时文件时拒绝其中一个 ⇒ 该 worker 崩溃、其测试文件被静默丢弃）。
 *
 * 固定 pool（`forks` + `fileParallelism: false`）只是**降低**丢文件概率；本脚本才是
 * **证明** —— 它把「磁盘上应跑的测试文件集合」与「vitest 实际执行的集合」逐一比对，
 * **不等即失败**。
 *
 * ## 判据（不写死常量，随测试文件增减自动跟随）
 *
 *   expected = 递归扫描 `src/` 下匹配 `*.test.{ts,tsx}` 的文件（与 vitest `include` 同约定）
 *   ran      = 本次 vitest 的 JSON 报告中列出的测试文件
 *   expected 中凡不在 ran 里 ⇒ 文件被丢弃 ⇒ exit 1（并逐个列出）
 *   ran 中凡不在 expected 里 ⇒ include 约定与磁盘 glob 漂移 ⇒ exit 1
 *
 * 注意：本脚本**不**替代 vitest 自身的失败判定 —— 若 vitest 退出码非 0（用例失败或
 * 存在未处理错误），同样 exit 1。两者是「跑得全」与「跑得过」两层，缺一不可。
 *
 * 维护：新增/删除测试文件无需改本脚本（expected 现算）；只有改变 `include` 约定或
 * 测试目录时才需同步本脚本的 `TEST_RE` / `srcDir`。
 */
import { execSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';

const projectRoot = process.cwd();
const srcDir = join(projectRoot, 'src');
// 报告写到 node_modules/.cache（已 gitignore），避免污染工作区
const reportPath = join(projectRoot, 'node_modules', '.cache', 'vitest-filecount.json');
const TEST_RE = /\.test\.(ts|tsx)$/;

/** 递归收集磁盘上的测试文件（与 vitest `include: ['src/**\/*.test.{ts,tsx}']` 同约定） */
function collectTestFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...collectTestFiles(full));
    else if (TEST_RE.test(entry.name)) out.push(full);
  }
  return out;
}

const toRel = (p) => relative(projectRoot, p).replace(/\\/g, '/');

if (!existsSync(srcDir)) {
  console.error(`❌ 未找到 ${srcDir} —— 请在前端根目录运行本脚本。`);
  process.exit(1);
}

const expected = collectTestFiles(srcDir).map(toRel).sort();
if (expected.length === 0) {
  console.error('❌ src/ 下未找到任何测试文件 —— include 约定可能已被改坏（0 个文件全绿是假绿灯）。');
  process.exit(1);
}

// 清掉旧报告，避免把上一次的结论当成本次
rmSync(reportPath, { force: true });
mkdirSync(dirname(reportPath), { recursive: true });

let vitestExitCode = 0;
try {
  // 同时挂 default（实时进度）+ json（机器可读）两个 reporter
  execSync(
    `npx vitest run --reporter=default --reporter=json --outputFile=${JSON.stringify(reportPath)}`,
    { stdio: 'inherit' },
  );
} catch (err) {
  vitestExitCode = (err && typeof err === 'object' && 'status' in err && err.status) || 1;
}

if (!existsSync(reportPath)) {
  console.error('❌ vitest 未产出 JSON 报告 —— 测试进程很可能已崩溃（连报告都没写出来）。');
  process.exit(1);
}

let report;
try {
  report = JSON.parse(readFileSync(reportPath, 'utf8'));
} catch (err) {
  console.error(`❌ vitest JSON 报告无法解析：${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
} finally {
  rmSync(reportPath, { force: true });
}

const ran = (report.testResults ?? [])
  .map((r) => toRel(r.name ?? r.filepath ?? ''))
  .filter(Boolean)
  .sort();
const ranSet = new Set(ran);
const expectedSet = new Set(expected);

const missing = expected.filter((f) => !ranSet.has(f));
const extra = ran.filter((f) => !expectedSet.has(f));

if (missing.length || extra.length) {
  console.error('\n❌ 测试文件数与磁盘不一致 —— 门禁丢弃/多跑了文件，绿灯不可信：');
  if (missing.length) {
    console.error(`   未执行（磁盘有、vitest 没跑）共 ${missing.length} 个：`);
    for (const f of missing) console.error(`     - ${f}`);
  }
  if (extra.length) {
    console.error(`   意外执行（vitest 跑了、磁盘 glob 没匹配）共 ${extra.length} 个：`);
    for (const f of extra) console.error(`     - ${f}`);
  }
  console.error('   处置：多为 pool 并行写临时文件失败（EPERM）导致 worker 崩溃、其文件被丢。'
    + '确认 `vitest.config.ts` 的 `pool: forks` + `fileParallelism: false` 未被改回默认。');
  process.exit(1);
}

if (vitestExitCode !== 0) {
  console.error(`\n❌ vitest 退出码 ${vitestExitCode}（用例失败或存在未处理错误）。`);
  process.exit(vitestExitCode);
}

console.log(`\n✅ 测试门禁通过：磁盘 ${expected.length} 个测试文件全部执行，无丢弃，vitest 退出码 0。`);
