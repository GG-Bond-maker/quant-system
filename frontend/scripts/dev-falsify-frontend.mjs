#!/usr/bin/env node
/**
 * 前端证伪：确认新增测试真的能捕获它们声称要防的缺陷（C-3，2026-10-02）。
 *
 * 纪律：「测试通过」不是证据，「注入 bug 后测试失败」才是。
 * 本脚本对生产源文件依次注入 3 个缺陷，每个都应让**指定**测试文件 FAIL。
 *
 * 🔴 **为什么必须加锁**
 * 宿主会对**同一条命令重复执行**。若不互斥，两次运行会交错：
 * 第二次把"已被注入的文件"当成 original 读入，还原动作就**把缺陷写回生产文件**
 * （2026-10-02 实测在 `scripts/expand_universe_2500.py` 上真的发生过一次）。
 * 故这里用 `mkdir`（原子）做互斥锁：拿不到锁的运行**立即退出**，绝不并发改写。
 *
 * 另有两道兜底：① 还原用**内存里读到的原文**，不依赖任何可能被污染的外部备份；
 * ② 收尾逐个校验源文件 SHA 与开始时一致，不一致即 `exit 2`（禁止提交）。
 *
 * 用法：node scripts/dev-falsify-frontend.mjs
 */
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, '..');
const LOCK_DIR = path.join(FRONTEND, '.falsify.lock');
const VITEST = path.join(FRONTEND, 'node_modules', 'vitest', 'vitest.mjs');

const CASES = [
  {
    label: '缺陷1：App.tsx 包装 div 丢掉 h-full（P0 高度链断裂复发）',
    file: 'src/App.tsx',
    from: 'mx-auto h-full w-full',
    to: 'mx-auto w-full',
    test: 'src/test/layoutContract.test.tsx',
  },
  {
    label: '缺陷2：FilterPanel 的「筛选」传回同一对象引用（D-2 欺骗性 UI 复发）',
    file: 'src/pages/Etf/FilterPanel.tsx',
    from: 'const apply = () => onChange({ ...draft, q: value.q });',
    to: 'const apply = () => onChange(value);',
    test: 'src/pages/Etf/FilterPanel.test.tsx',
  },
  {
    label: '缺陷3：页面根改用定高 h-full（内容高于视口会被裁掉）',
    file: 'src/pages/Watchlist/index.tsx',
    from: 'flex min-h-full flex-col gap-3',
    to: 'flex h-full flex-col gap-3',
    test: 'src/test/layoutContract.test.tsx',
  },
];

const sha = (p) => createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const abs = (rel) => path.join(FRONTEND, rel);

function acquireLock() {
  try {
    fs.mkdirSync(LOCK_DIR);
  } catch {
    console.error(
      `❌ 已存在 ${path.relative(FRONTEND, LOCK_DIR)}（另一次证伪运行中，或上次异常退出）。\n` +
      '   本脚本会改写生产源文件，故拒绝并发。确认无运行后手工删除该目录再重试。');
    process.exit(3);
  }
}

function main() {
  if (!fs.existsSync(VITEST)) {
    console.error(`❌ 未找到 ${path.relative(FRONTEND, VITEST)}，请先 npm install。`);
    return 4;
  }

  acquireLock();
  // 读入全部原文（内存即权威副本）
  const originals = new Map(CASES.map((c) => [c.file, fs.readFileSync(abs(c.file), 'utf8')]));
  const before = new Map(CASES.map((c) => [c.file, sha(abs(c.file))]));
  const results = [];

  try {
    for (const c of CASES) {
      const orig = originals.get(c.file);
      const n = orig.split(c.from).length - 1;
      if (n !== 1) {
        console.log(`❌ 锚点不唯一（count=${n}）：${c.file} :: ${JSON.stringify(c.from)}`);
        results.push([c.label, false]);
        continue;
      }
      fs.writeFileSync(abs(c.file), orig.replace(c.from, c.to), 'utf8');

      let failed = false;
      try {
        execFileSync(process.execPath, [VITEST, 'run', '--config', 'vitest.config.ts', c.test], {
          cwd: FRONTEND, stdio: 'pipe',
        });
      } catch {
        failed = true;   // 非零退出 = 测试确实 FAIL 了
      }
      results.push([c.label, failed]);
      console.log(`${failed ? '✅ 捕获' : '❌ 未捕获'} | ${c.label} -> ${c.test}`);

      fs.writeFileSync(abs(c.file), orig, 'utf8');   // 立即还原
    }
  } finally {
    // 双保险：无论上面怎么退出，都把原文写回
    for (const [file, orig] of originals) {
      try { fs.writeFileSync(abs(file), orig, 'utf8'); } catch { /* 交给 SHA 校验暴露 */ }
    }
    fs.rmSync(LOCK_DIR, { recursive: true, force: true });
  }

  // 🔴 原文件必须逐字节还原
  const drifted = CASES.filter((c) => sha(abs(c.file)) !== before.get(c.file));
  if (drifted.length) {
    console.error(`\n❌ 源文件未还原（证伪机制本身有 bug，禁止提交）：${drifted.map((d) => d.file).join(', ')}`);
    return 2;
  }
  console.log('\n源文件校验：OK 全部逐字节还原');

  const bad = results.filter(([, ok]) => !ok);
  console.log(`证伪结果：${results.length - bad.length}/${results.length} 组被捕获`);
  return bad.length ? 1 : 0;
}

process.exit(main());
