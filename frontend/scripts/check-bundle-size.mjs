/**
 * 前端产物体积上限检查。
 *
 * ## 🔴 陈旧性守卫（L-1，2026-10-02）
 *
 * 本脚本原先直接 `readdirSync(dist/assets)` —— 于是当 `npm run build` **失败**时，
 * `dist` 里仍留着**上一次**的产物，本脚本会为**旧产物**背书并打印
 * `Bundle budget passed` ⇒ **假通过**（掩盖真实的体积回归）。
 *
 * 这不是假设：实测 `npm run build` 会被宿主 safe-delete shim 拦住 ——
 * vite 的 `emptyDir(dist/assets)` 走 `fs.rmSync` → `genie-trash … ETIMEDOUT`
 * ⇒ **构建直接失败，但 dist 完好无损**。正解是先删 `dist` 再构建；
 * 而"忘了删"这条路径现在会被下面的守卫**明确拦下**，不再静默通过。
 *
 * 判据：`dist/` 中最新的文件 mtime 必须 **不早于** `src/` + 构建配置中最新的 mtime。
 * 否则说明产物陈旧（构建未运行 / 未成功 / 源码改后没重建）。
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';

const projectRoot = process.cwd();
const distDir = join(projectRoot, 'dist');
const assetsDir = join(distDir, 'assets');
const srcDir = join(projectRoot, 'src');

const maxRaw = 750 * 1024;
const maxGzip = 250 * 1024;

function newestMtime(dir) {
  let newest = 0;
  const walk = (d) => {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, entry.name);
      if (entry.isDirectory()) {
        walk(p);
        continue;
      }
      const t = statSync(p).mtimeMs;
      if (t > newest) newest = t;
    }
  };
  walk(dir);
  return newest;
}

// ---- 0) 产物必须存在 ----
if (!existsSync(assetsDir)) {
  console.error(`❌ 未找到 ${assetsDir} —— 请先运行 npm run build。`);
  process.exit(1);
}

// ---- 1) 产物不得陈旧（否则下面的体积结论是在为旧产物背书）----
const mtimeOf = (p) => (existsSync(p) ? statSync(p).mtimeMs : 0);
const distNewest = newestMtime(distDir);
const srcNewest = Math.max(
  newestMtime(srcDir),
  mtimeOf(join(projectRoot, 'vite.config.ts')),
  mtimeOf(join(projectRoot, 'package.json')),
);

if (distNewest < srcNewest) {
  const gapS = ((srcNewest - distNewest) / 1000).toFixed(0);
  console.error(
    `❌ dist 比源码陈旧（落后 ${gapS}s）—— 体积检查拒绝为**旧产物**背书。\n`
    + '   常见原因：npm run build 失败（本机 safe-delete shim 会拦 vite 的\n'
    + '   emptyDir(dist/assets)，报 genie-trash … ETIMEDOUT）。\n'
    + '   处置：先删掉 dist 再重新构建\n'
    + '   （PowerShell: [System.IO.Directory]::Delete("<dist 绝对路径>", $true)），然后重跑本检查。',
  );
  process.exit(1);
}

// ---- 2) 体积上限 ----
const offenders = [];
for (const file of readdirSync(assetsDir)) {
  if (!/\.(js|css)$/.test(file)) continue;
  const bytes = readFileSync(join(assetsDir, file));
  const raw = bytes.byteLength;
  const gzip = gzipSync(bytes).byteLength;
  if (raw > maxRaw || gzip > maxGzip) offenders.push({ file, raw, gzip });
}

if (offenders.length) {
  for (const item of offenders) {
    console.error(`${item.file}: ${item.raw} bytes raw, ${item.gzip} bytes gzip`);
  }
  process.exit(1);
}
console.log(`Bundle budget passed: raw <= ${maxRaw} B, gzip <= ${maxGzip} B（产物新鲜度已校验）`);
