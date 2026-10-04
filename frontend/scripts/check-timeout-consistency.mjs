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

/**
 * 裸超时字面量：出现在实参位（`, 120_000`）或 `timeout:` 位（`timeout: 15000`）。
 *
 * ⚠️ 数字组必须写成 `\d[\d_]{3,}`（**含下划线与数字**，长度 ≥4）。
 * 2026-10-04 上线前全检修掉的真实漏洞（T-2）：
 * 旧模式 `\d[\d_]{2,}_000|\d{4,}` 中，第一支要求 `_000` **前**至少 3 位数字
 * （`120_000` ✓），第二支要求 ≥4 位**连续**数字 ⇒
 * `10_000 / 15_000 / 20_000 / 30_000 / 60_000 / 90_000` **全部漏掉**，
 * 即"`src/api/**` 不得出现裸超时字面量"这条边界在**整个小档位区间**失效。
 * 当时 `src/api/market.ts:91` 就裸写着 `20_000`，而闸门仍报通过。
 *
 * 注意 `{3,}` 比旧第一支的 `{2,}`（即总计 ≥5 位）**多覆盖 4 位数字**
 * （如 `1500`）；本项目最小的请求超时档位是 `10_000`，4 位以上裸数字
 * 出现在实参位都不是超时，故仍可能有理论误报 —— 但那属于"该常量本该进
 * `timeouts.ts`"的范畴，报出来比漏报安全。`C7` 另对 `POLL_*` 常量名豁免。
 */
const RAW_TIMEOUT = /(?:,\s*|\btimeout:\s*)(\d[\d_]{3,})(?![\d_])/;

// --------------------------------------------------------------------------- //
// 纯校验函数（可单测；不碰 fs）
// --------------------------------------------------------------------------- //
export function runChecks({ tiers, longPaths, pyBudgets, defaultBudget, rawHits,
                            tierRefs = [], longRefs = [], cappedPaths = {},
                            pyMaxBudgets = {} }) {
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

  // C2 孤儿：后端每条预算前端都要有登记（延长 / 收紧两张表都算登记）
  for (const p of Object.keys(pyBudgets)) {
    if (!(p in longPaths) && !(p in cappedPaths)) {
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

  // C6 收紧（补 C1 的**反方向**漏洞）
  //
  // C1 只守"服务端预算 > 前端超时"（防误杀）。反方向从未设防：服务端预算**远大于**
  // 前端超时 ⇒ 用户早断了、服务端还在烧 worker（`asyncio.to_thread` 里的重算不可取消）。
  // 2026-10-04 全检实测：4 条路径落回 240s 默认预算、前端只有 60~120s，真实日志里
  // 6 次"用户 60s 就断了、服务端烧满 240s"的 504。
  const cappedKeys = new Set(Object.keys(cappedPaths));
  const longKeys = new Set(Object.keys(longPaths));
  for (const p of cappedKeys) {
    if (longKeys.has(p)) {
      errors.push(`C6 语义冲突：'${p}' 同时出现在 LONG_PATHS（要求预算调大）与 `
        + 'CAPPED_PATHS（要求预算压低）⇒ 二者互斥，必须只留一个');
    }
  }
  for (const [p, ms] of Object.entries(cappedPaths)) {
    const budget = pyMaxBudgets[p];
    if (budget === undefined) {
      errors.push(`C6 收紧未生效：前端 CAPPED_PATHS 登记了 '${p}'（${ms}ms），`
        + '但后端 _PATH_MAX_BUDGETS 没有该路径 ⇒ 仍会被默认预算白烧');
      continue;
    }
    if (!(budget < ms / 1000)) {
      errors.push(`C6 未真正收紧：'${p}' 前端 ${ms}ms（即 ${ms / 1000}s），`
        + `后端 max 预算 ${budget}s —— 必须**严格小于**前端超时，否则用户仍先断`);
    }
  }
  for (const p of Object.keys(pyMaxBudgets)) {
    if (!cappedKeys.has(p)) {
      errors.push(`C6 孤儿收紧：后端 _PATH_MAX_BUDGETS 有 '${p}'，但前端 CAPPED_PATHS 未登记`);
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
  const cappedPaths = {};
  // 两张登记表结构相同（`'<路径>': TIMEOUT.<档位>`），共用同一套解析。
  // ⚠️ 必须用 `LONG_PATHS[^{]*\{` 锚定**定义处**：`CAPPED_PATHS` 的文档注释里
  // 也提到了 `LONG_PATHS` 字样，宽松正则会跨块匹配、解析出错误的表。
  const parsePathTable = (block, label) => {
    if (!block) {
      parseErrors.push(`未在 timeouts.ts 中找到 ${label} 块`);
      return {};
    }
    const out = {};
    for (const m of block[1].matchAll(/'([^']+)':\s*TIMEOUT\.(\w+)/g)) {
      const v = tiers[m[2]];
      if (v === undefined) {
        parseErrors.push(`${label}['${m[1]}'] 引用了不存在的档位 TIMEOUT.${m[2]}`);
        continue;
      }
      out[m[1]] = v;
    }
    return out;
  };
  Object.assign(longPaths,
    parsePathTable(ts.match(/LONG_PATHS[^{]*\{([\s\S]*?)\n\};/), 'LONG_PATHS'));
  Object.assign(cappedPaths,
    parsePathTable(ts.match(/CAPPED_PATHS[^{]*\{([\s\S]*?)\n\};/), 'CAPPED_PATHS'));

  // 后端两张预算表结构相同（`"<路径>": <浮点>`），共用同一套解析。
  // ⚠️ 必须锚定「定义处」：模块 docstring 里也提到了 `_PATH_BUDGETS`，
  // 用宽松正则会从 docstring 处开始匹配、跨到 `_EXEMPT_PATHS` 的 `{`，
  // 于是解析出 0 条预算（静默失效，闸门形同不存在）。
  const guard = readOrFail(GUARD_PY, '后端超时兜底中间件');
  const parsePyBudgets = (name, label) => {
    const block = guard.match(new RegExp(`${name}:\\s*dict[^{]*\\{([\\s\\S]*?)\\n\\}`));
    if (!block) {
      parseErrors.push(`未在 timeout_guard.py 中找到 ${label} 定义块`);
      return {};
    }
    const out = {};
    for (const m of block[1].matchAll(/"([^"]+)":\s*([\d.]+)/g)) {
      out[m[1]] = Number(m[2]);
    }
    return out;
  };
  const pyBudgets = parsePyBudgets('_PATH_BUDGETS', '_PATH_BUDGETS');
  const pyMaxBudgets = parsePyBudgets('_PATH_MAX_BUDGETS', '_PATH_MAX_BUDGETS');

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

  return { tiers, longPaths, cappedPaths, pyBudgets, pyMaxBudgets,
           defaultBudget, rawHits, tierRefs, longRefs, parseErrors };
}

// --------------------------------------------------------------------------- //
// 证伪：合成夹具（不碰任何生产文件）
// --------------------------------------------------------------------------- //
const BASE = {
  tiers: { default: 15_000, aggregate: 30_000, heavy: 120_000, optimize: 600_000 },
  longPaths: { '/api/v1/backtest/strategy-run': 600_000 },
  pyBudgets: { '/api/v1/backtest/strategy-run': 660.0 },
  // 收紧方向基线：一条被压到前端超时之下的路径（与 C1 方向互不打扰）
  cappedPaths: { '/api/v1/settings': 60_000 },
  pyMaxBudgets: { '/api/v1/settings': 48.0 },
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
  // ── C4 回归：旧正则 `\d[\d_]{2,}_000|\d{4,}` 漏掉全部 10s~90s 档位，
  //    这 6 个值以前**一个都抓不到**（真实案例：`market.ts:91` 的 `20_000`）。
  //    本用例按生产正则做真匹配，确保该区间永久受保护。
  ['C4 回归：10_000~90_000 档位必须能被生产正则捕获（旧正则全漏）',
    { ...BASE, rawHits: ['10_000', '15_000', '20_000', '30_000', '60_000', '90_000']
      .filter((lit) => new RegExp(RAW_TIMEOUT.source).test(`, ${lit}))`))
      .map((literal, i) => ({ file: 'src/api/x.ts', line: i + 1, literal })) }, 'C4'],
  // ── C5：补 C3 的"按值豁免"漏洞 ──
  ['C5：新路径裸用保留档位（TIMEOUT.optimize）——C3 按值豁免会放行，故必须由 C5 拦',
    { ...BASE, tierRefs: [...BASE.tierRefs, { file: 'src/api/new.ts', line: 9, name: 'optimize' }] },
    'C5'],
  ['C5：longTimeout 传了未登记的路径',
    { ...BASE, longRefs: [...BASE.longRefs, { file: 'src/api/y.ts', line: 4, path: '/api/v1/nope' }] },
    'C5'],
  ['C5：LONG_PATHS 登记了却没人用（孤儿）',
    { ...BASE, longRefs: [] }, 'C5'],
  // ── C6：补 C1 的反方向（服务端预算远大于前端超时 ⇒ 白烧 worker）──
  ['C6：收紧未登记到后端（前端声称收紧、后端没这张表）',
    { ...BASE, pyMaxBudgets: {} }, 'C6'],
  ['C6：后端 max 预算未真正小于前端超时（等于也不行）',
    { ...BASE, pyMaxBudgets: { '/api/v1/settings': 60.0 } }, 'C6'],
  ['C6：后端收紧但前端未登记（孤儿收紧）',
    { ...BASE, cappedPaths: {}, pyMaxBudgets: { '/api/v1/settings': 48.0 } }, 'C6'],
  ['C6：同一路径同时登记 LONG_PATHS 与 CAPPED_PATHS（语义互斥）',
    { ...BASE, cappedPaths: { '/api/v1/backtest/strategy-run': 600_000 },
      pyMaxBudgets: { '/api/v1/backtest/strategy-run': 480.0 } }, 'C6'],
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
    + `LONG_PATHS ${Object.keys(src.longPaths).length} 条，`
    + `CAPPED_PATHS ${Object.keys(src.cappedPaths).length} 条；`
    + `后端 _PATH_BUDGETS ${Object.keys(src.pyBudgets).length} 条 / `
    + `_PATH_MAX_BUDGETS ${Object.keys(src.pyMaxBudgets).length} 条，`
    + `默认预算 ${src.defaultBudget}s`);

  const errors = [...src.parseErrors, ...runChecks(src)];
  if (errors.length) {
    console.error(`\n❌ 超时一致性闸门未通过（${errors.length} 项）：`);
    for (const e of errors) console.error(`  - ${e}`);
    return 1;
  }
  console.log('✅ 超时一致性闸门通过'
    + '（C1 误杀 / C2 孤儿 / C3 默认覆盖 / C4 集中登记 / C5 保留档位 / C6 收紧）');
  return 0;
}

process.exit(main());
