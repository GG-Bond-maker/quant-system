#!/usr/bin/env node
/**
 * 前后端超时一致性闸门（A-6 / F-3 修复，2026-10-02）。
 *
 * ## 为什么需要它
 *
 * 后端 `core/timeout_guard.py` 已把不变量写在模块 docstring 里：
 * **「服务端预算必须严格大于前端对同一路径的超时」** —— 否则出现
 * 「前端还在等、服务端已经回 504」的**误杀**。
 *
 * 但在本闸门存在之前，这条不变量只靠**手工快照**维持：`timeout_guard.py` 的注释里写着
 * 「全仓前端超时实测清单（`frontend/src/api/*.ts`，2026-09-27 核对）」。手工清单必然漂移 ——
 * 前端把某路径超时调大、或新增一条长超时，后端不会知道，也没有任何东西会 FAIL。
 *
 * ## 校验项（双向，缺一不可）
 *
 * - **C1 误杀**：`LONG_PATHS` 里每条路径，后端 `_PATH_BUDGETS` 必须存在且**严格大于**该超时。
 * - **C2 孤儿**：后端 `_PATH_BUDGETS` 的每条路径，前端 `LONG_PATHS` 必须有对应登记。
 * - **C3 默认预算覆盖**：后端默认预算必须**严格大于**所有**未登记进 `LONG_PATHS`** 的前端档位。
 * - **C4 集中登记**：`src/api/**`（除 `timeouts.ts`）**不得出现裸超时字面量**，必须引用 `TIMEOUT.*`。
 * - **C5 保留档位**（补 C3 的漏洞）：`LONG_PATHS` 用到的那几个档位是**保留值**，
 *   在 `src/api/**` 里**不得裸用** `TIMEOUT.<name>`，必须经 `longTimeout('<路径>')` 取得；
 *   且 `longTimeout` 的实参必须已登记、登记的路径必须真被用到（无孤儿）。
 *
 *   > 为什么需要 C5：C3 按「**档位值**」豁免 —— 只要某档位的值出现在 `LONG_PATHS` 里就跳过检查。
 *   > 于是"给一条**新路径**直接写 `TIMEOUT.rerun`（300s）却忘了登记 `LONG_PATHS`"会被**放行**，
 *   > 而该路径会被后端 240s 默认预算**误杀**。C5 把"路径"变成必需实参，从而可静态校验。
 *
 * ## 为什么校验逻辑是**纯函数**
 *
 * 闸门自身的可信度不能靠"我看着它对"。`--self-test` 用**合成夹具**对每条规则做一次证伪
 * （构造违规输入 ⇒ 断言对应错误码出现）。这样验证闸门**不需要改写任何生产文件** ——
 * 本项目有过"证伪脚本就地注入、宿主重复执行导致缺陷写回生产文件"的事故（2026-10-02），
 * 故此处刻意走纯函数 + 夹具路线。
 *
 * 用法：
 *   node scripts/check-timeout-consistency.mjs              # 校验真实仓库
 *   node scripts/check-timeout-consistency.mjs --self-test  # 证伪闸门自身
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, '..');
const REPO = path.resolve(FRONTEND, '..');
const API_DIR = path.join(FRONTEND, 'src', 'api');
const TIMEOUTS_TS = path.join(API_DIR, 'timeouts.ts');
const GUARD_PY = path.join(REPO, 'backend', 'app', 'core', 'timeout_guard.py');
const CONFIG_PY = path.join(REPO, 'backend', 'app', 'core', 'config.py');

const rel = (p) => path.relative(REPO, p);

/** 裸超时字面量：出现在实参位（`, 120_000`）或 `timeout:` 位（`timeout: 15000`） */
const RAW_TIMEOUT = /(?:,\s*|\btimeout:\s*)(\d[\d_]{2,}_000|\d{4,})(?![\d_])/;

// --------------------------------------------------------------------------- //
// 纯校验函数（可单测；不碰 fs）
// --------------------------------------------------------------------------- //
export function runChecks({ tiers, longPaths, pyBudgets, defaultBudget, rawHits,
                            tierRefs = [], longRefs = [] }) {
  const errors = [];

  // C1 误杀：后端预算必须严格大于前端同路径超时
  for (const [p, ms] of Object.entries(longPaths)) {
    const budget = pyBudgets[p];
    if (budget === undefined) {
      errors.push(`C1 误杀风险：前端 LONG_PATHS 登记了 '${p}'（${ms}ms），`
        + '但后端 _PATH_BUDGETS 没有该路径 ⇒ 会被默认预算误杀');
      continue;
    }
    if (!(budget > ms / 1000)) {
      errors.push(`C1 误杀：'${p}' 前端 ${ms}ms，后端预算 ${budget}s —— 必须**严格大于**前端超时`);
    }
  }

  // C2 孤儿：后端每条预算前端都要有登记
  for (const p of Object.keys(pyBudgets)) {
    if (!(p in longPaths)) {
      errors.push(`C2 孤儿预算：后端 _PATH_BUDGETS 有 '${p}'，但前端 LONG_PATHS 未登记`);
    }
  }

  // C3 默认预算必须覆盖所有未登记的档位
  if (Number.isFinite(defaultBudget)) {
    const paired = new Set(Object.values(longPaths));
    for (const [name, ms] of Object.entries(tiers)) {
      if (paired.has(ms)) continue;
      if (!(defaultBudget > ms / 1000)) {
        errors.push(`C3 误杀风险：档位 TIMEOUT.${name}=${ms}ms 未登记进 LONG_PATHS，`
          + `而后端默认预算只有 ${defaultBudget}s ⇒ 该路径会被误杀`);
      }
    }
  } else {
    errors.push('C3 无法校验：未取到后端默认预算');
  }

  // C4 集中登记
  for (const hit of rawHits) {
    errors.push(`C4 裸超时字面量：${hit.file}:${hit.line} 出现 ${hit.literal}`
      + ' ⇒ 必须改用 TIMEOUT.*（集中登记）');
  }

  // C5 保留档位必须经 longTimeout(路径) 取得
  //
  // 补的是 C3 的漏洞：C3 按「**档位值**」豁免（值出现在 LONG_PATHS 里就跳过检查），
  // 于是"给一条**新路径**直接写 TIMEOUT.rerun（300s）却忘了登记 LONG_PATHS"会被放行，
  // 而该路径会被后端 240s 默认预算**误杀**。C5 把"路径"变成必需实参，从而可静态校验。
  const reserved = new Set(Object.values(longPaths));
  for (const ref of tierRefs) {
    if (reserved.has(tiers[ref.name])) {
      errors.push(`C5 保留档位裸用：${ref.file}:${ref.line} 直接使用 TIMEOUT.${ref.name}`
        + `（${tiers[ref.name]}ms 属 LONG_PATHS 保留值）`
        + " ⇒ 必须改用 longTimeout('<路径>')，否则新路径会漏登记、被后端默认预算误杀");
    }
  }
  for (const ref of longRefs) {
    if (!(ref.path in longPaths)) {
      errors.push(`C5 未登记的 longTimeout：${ref.file}:${ref.line} 传入 '${ref.path}'，`
        + '但 LONG_PATHS 没有该键（运行时会直接抛错）');
    }
  }
  const usedLongPaths = new Set(longRefs.map((r) => r.path));
  for (const p of Object.keys(longPaths)) {
    if (!usedLongPaths.has(p)) {
      errors.push(`C5 孤儿登记：LONG_PATHS 有 '${p}'，但 src/api/** 无任何 `
        + `longTimeout('${p}') 调用（登记了却没人用 ⇒ 某侧改过而另一侧没跟）`);
    }
  }

  return errors;
}

// --------------------------------------------------------------------------- //
// 真实仓库取样
// --------------------------------------------------------------------------- //
function collectSources() {
  const parseErrors = [];
  const readOrFail = (p, label) => {
    if (!fs.existsSync(p)) {
      parseErrors.push(`找不到${label}：${rel(p)}`);
      return '';
    }
    return fs.readFileSync(p, 'utf8');
  };

  const ts = readOrFail(TIMEOUTS_TS, '前端超时集中表');
  const tiers = {};
  for (const m of ts.matchAll(/^ {2}(\w+):\s*([\d_]+),/gm)) {
    tiers[m[1]] = Number(m[2].replace(/_/g, ''));
  }
  if (Object.keys(tiers).length === 0) parseErrors.push('未从 timeouts.ts 解析出任何 TIMEOUT 档位');

  const longPaths = {};
  const lpBlock = ts.match(/LONG_PATHS[^{]*\{([\s\S]*?)\n\};/);
  if (!lpBlock) {
    parseErrors.push('未在 timeouts.ts 中找到 LONG_PATHS 块');
  } else {
    for (const m of lpBlock[1].matchAll(/'([^']+)':\s*TIMEOUT\.(\w+)/g)) {
      const v = tiers[m[2]];
      if (v === undefined) {
        parseErrors.push(`LONG_PATHS['${m[1]}'] 引用了不存在的档位 TIMEOUT.${m[2]}`);
        continue;
      }
      longPaths[m[1]] = v;
    }
  }

  const guard = readOrFail(GUARD_PY, '后端超时兜底中间件');
  const pyBudgets = {};
  // ⚠️ 必须锚定「定义处」：模块 docstring 里也提到了 `_PATH_BUDGETS`，
  // 用宽松正则会从 docstring 处开始匹配、跨到 `_EXEMPT_PATHS` 的 `{`，
  // 于是解析出 0 条预算（静默失效，闸门形同不存在）。
  const pbBlock = guard.match(/_PATH_BUDGETS:\s*dict[^{]*\{([\s\S]*?)\n\}/);
  if (!pbBlock) {
    parseErrors.push('未在 timeout_guard.py 中找到 _PATH_BUDGETS 定义块');
  } else {
    for (const m of pbBlock[1].matchAll(/"([^"]+)":\s*([\d.]+)/g)) {
      pyBudgets[m[1]] = Number(m[2]);
    }
  }

  const cfg = readOrFail(CONFIG_PY, '后端配置');
  const dm = cfg.match(/REQUEST_TIMEOUT_SECONDS[\s\S]{0,240}?default=([\d.]+)/);
  if (!dm) parseErrors.push('未从 config.py 解析出 REQUEST_TIMEOUT_SECONDS 默认值');
  const defaultBudget = dm ? Number(dm[1]) : NaN;

  const rawHits = [];
  const tierRefs = [];   // 形如 TIMEOUT.<name> 的引用（C5 用）
  const longRefs = [];   // 形如 longTimeout('<path>') 的调用（C5 用）
  if (fs.existsSync(API_DIR)) {
    // ⚠️ 排除测试文件：`*.test.ts` 里"故意传未登记的路径以验证 longTimeout 会抛错"
    // 正是测试的目的，把它当生产调用点会误报（2026-10-02 实测被 C5 误报过）。
    // C4/C5 守的是**生产调用点**，测试文件不参与。
    const isProdApi = (n) => n.endsWith('.ts') && !n.endsWith('.test.ts')
      && !n.endsWith('.test.tsx') && n !== 'timeouts.ts';
    for (const f of fs.readdirSync(API_DIR).filter(isProdApi)) {
      const where = `src/api/${f}`;
      fs.readFileSync(path.join(API_DIR, f), 'utf8').split('\n').forEach((line, i) => {
        const t = line.trimStart();
        if (t.startsWith('//') || t.startsWith('*')) return;   // 注释里举例不算
        const m = line.match(RAW_TIMEOUT);
        if (m) rawHits.push({ file: where, line: i + 1, literal: m[1] });
        for (const tm of line.matchAll(/TIMEOUT\.(\w+)/g)) {
          tierRefs.push({ file: where, line: i + 1, name: tm[1] });
        }
        for (const lm of line.matchAll(/longTimeout\(\s*'([^']+)'\s*\)/g)) {
          longRefs.push({ file: where, line: i + 1, path: lm[1] });
        }
      });
    }
  }

  return { tiers, longPaths, pyBudgets, defaultBudget, rawHits, tierRefs, longRefs, parseErrors };
}

// --------------------------------------------------------------------------- //
// 证伪：合成夹具（不碰任何生产文件）
// --------------------------------------------------------------------------- //
const BASE = {
  tiers: { default: 15_000, aggregate: 30_000, heavy: 120_000, optimize: 600_000 },
  longPaths: { '/api/v1/backtest/strategy-run': 600_000 },
  pyBudgets: { '/api/v1/backtest/strategy-run': 660.0 },
  defaultBudget: 240,
  rawHits: [],
  // 与上面自洽的基线：该路径经 longTimeout 取得；heavy 不是保留档位，可裸用
  tierRefs: [{ file: 'src/api/x.ts', line: 2, name: 'heavy' }],
  longRefs: [{ file: 'src/api/x.ts', line: 1, path: '/api/v1/backtest/strategy-run' }],
};

const SELF_TESTS = [
  ['基线（无违规）应通过', BASE, null],
  ['C1：后端预算等于前端超时（非严格大于）',
    { ...BASE, pyBudgets: { '/api/v1/backtest/strategy-run': 600.0 } }, 'C1'],
  ['C1：前端登记了路径但后端没有预算',
    { ...BASE, pyBudgets: {} }, 'C1'],
  ['C2：后端有预算但前端未登记',
    { ...BASE, longPaths: {}, pyBudgets: { '/api/v1/x': 300.0 }, longRefs: [] }, 'C2'],
  // ⚠️ 夹具必须**只**触发被测规则：这里两侧路径表都清空、且不动 _PATH_BUDGETS 的配对，
  // 否则 C2 会先于 C3 报错、把断言的真实性掩盖掉（初版就踩过）。
  ['C3：未登记档位超过默认预算',
    { tiers: { default: 15_000, huge: 400_000 }, longPaths: {}, pyBudgets: {},
      defaultBudget: 240, rawHits: [], tierRefs: [], longRefs: [] },
    'C3'],
  ['C4：api 层出现裸超时字面量',
    { ...BASE, rawHits: [{ file: 'src/api/x.ts', line: 3, literal: '120_000' }] }, 'C4'],
  // ── C5：补 C3 的"按值豁免"漏洞 ──
  ['C5：新路径裸用保留档位（TIMEOUT.optimize）——C3 按值豁免会放行，故必须由 C5 拦',
    { ...BASE, tierRefs: [...BASE.tierRefs, { file: 'src/api/new.ts', line: 9, name: 'optimize' }] },
    'C5'],
  ['C5：longTimeout 传了未登记的路径',
    { ...BASE, longRefs: [...BASE.longRefs, { file: 'src/api/y.ts', line: 4, path: '/api/v1/nope' }] },
    'C5'],
  ['C5：LONG_PATHS 登记了却没人用（孤儿）',
    { ...BASE, longRefs: [] }, 'C5'],
];

function selfTest() {
  let bad = 0;
  for (const [label, fixture, expectCode] of SELF_TESTS) {
    const errs = runChecks(fixture);
    const hit = expectCode === null ? errs.length === 0 : errs.some((e) => e.startsWith(expectCode));
    if (!hit) bad += 1;
    // 打印**命中断言的那条**（而非首条），否则多规则同时触发时会看不出真实原因
    const shown = expectCode === null
      ? (errs.length ? errs[0] : '无错误')
      : (errs.find((e) => e.startsWith(expectCode)) ?? `未触发（实为 ${errs[0] ?? '无错误'}）`);
    console.log(`${hit ? '✅' : '❌'} ${label}（期望 ${expectCode ?? '通过'}）`
      + `${errs.length ? ` → ${shown.slice(0, 72)}` : ''}`);
  }
  console.log(`\n证伪结果：${SELF_TESTS.length - bad}/${SELF_TESTS.length} 条规则可被夹具触发`);
  return bad ? 1 : 0;
}

// --------------------------------------------------------------------------- //
// 入口
// --------------------------------------------------------------------------- //
function main() {
  if (process.argv.includes('--self-test')) return selfTest();

  const src = collectSources();
  console.log(`前端 TIMEOUT 档位 ${Object.keys(src.tiers).length} 个，`
    + `LONG_PATHS ${Object.keys(src.longPaths).length} 条；`
    + `后端 _PATH_BUDGETS ${Object.keys(src.pyBudgets).length} 条，`
    + `默认预算 ${src.defaultBudget}s`);

  const errors = [...src.parseErrors, ...runChecks(src)];
  if (errors.length) {
    console.error(`\n❌ 超时一致性闸门未通过（${errors.length} 项）：`);
    for (const e of errors) console.error(`  - ${e}`);
    return 1;
  }
  console.log('✅ 超时一致性闸门通过'
    + '（C1 误杀 / C2 孤儿 / C3 默认覆盖 / C4 集中登记 / C5 保留档位）');
  return 0;
}

process.exit(main());
