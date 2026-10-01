/**
 * 前端风格闸门（A1 + A6 的统一执行点）。
 *
 * ## 为什么需要它
 * 本项目有 4 处反复复发的"债"，根因是同一个：**约定只写进了注释，没有执行点**。
 *   - `ui/index.tsx` 的 `PageHeader` 注释自称"全站唯一的一级标题写法，
 *     禁止页面内自行拼 `<h1>`" —— 实际 19 个页面里 18 个自己写 `<h1>`；
 *   - `lib/chartTheme.ts` 声称色值唯一来源 —— 实际全站 144 处硬编码 hex。
 * 注释约束不住人。本脚本把两条约定变成可执行的检查。
 *
 * ## 用法
 *
 *   node scripts/check-style.mjs                 # 默认：查 <h1> **且** 查硬编码 hex
 *   node scripts/check-style.mjs --no-hex        # 逃生舱：本次跳过 hex（见下）
 *   node scripts/check-style.mjs --hex           # 显式开启 hex（与默认相同，语义别名）
 *   node scripts/check-style.mjs --all           # 同 --hex（语义别名）
 *   node scripts/check-style.mjs --json          # 机器可读输出
 *
 * ## 已知盲区（务必知悉，勿据此绕过闸门）
 *
 * 1. **注释行排除是"减少噪音"，不是"白名单"**：hex 规则跳过以 `//`、`*` 开头的行，
 *    以免把"修复说明里举例的旧色值"误报为违规。但这留下一个假阴性方向——
 *    若把色值写进**块注释内的代码示例**、或 **JSDoc @example 的续行**
 *    （行首无注释符），可能漏报。**不应用"塞进注释"来规避本检查**：
 *    闸门的存在意义是保证语义色有唯一来源，绕过它等于把债藏起来。
 * 2. **本脚本只保证"不新增硬编码"，不保证"颜色语义正确"**。
 *    它能抓 `#EF4444` 这种字面量，但抓不到"红到底代表涨还是跌"。
 *    实证：`Backtest/resultParts.tsx` 曾在同一页内让 KPI 卡（红涨绿跌）
 *    与 sparkline / MonthlyChart（涨=蓝、正收益=蓝）各说各话——**这类
 *    "正确性债"只能靠人工评审守**。⇒ 闸门通过 ≠ 颜色没骗人。
 * 3. 仅扫描 `src/pages` 与 `src/components`；`src/lib`、`src/utils` 等不在范围内。
 *
 * ## hex 规则为何从 opt-in 提升为默认开启（2026-10-01）
 *
 * 原设计中 hex 规则需 `--hex` 显式开启，理由是「存量未清零就默认开启 ⇒ 红灯疲劳 ⇒
 * 闸门被绕过」。该理由**只成立于存量非零期间**。hex 存量已从 144 处清到 **0**，
 * 前提消失，故规则提升为默认开启。历史记录（写在此处以免被遗忘）：
 * 该检查落地时暴露了 **136 处存量违规**（远超人工估算的 ~24 处），其中相当一部分
 * 是 ECharts 的**配置字面量**（如 `type: 'value', axisLabel: { color: '#94A3B8' }`），
 * 需要区分"该读 CSS 变量的语义色"与"合法的类别色 / 图表内固定配色"。
 *
 * ⚠️ **`--no-hex` 是逃生舱，不是常态。**
 * 仅用于「排查与 hex 无关的故障时临时闭嘴」这类一次性场景；若某次提交需要长期
 * 依赖它，说明真正的动作是**修代码或修白名单**，而不是关闸门。
 * pre-commit 与 CI **一律不带** `--no-hex`，逃生开关只在人工手动执行时可用。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = process.cwd();

/**
 * hex 规则是否启用。**默认开启**（存量已清零，见文件头「为何从 opt-in 提升为默认」）。
 * `--no-hex` 为逃生舱，仅人工临时使用；pre-commit / CI 不得携带。
 */
const CHECK_HEX = !process.argv.includes('--no-hex');
/** 机器可读输出（供 CI / 其它工具消费） */
const AS_JSON = process.argv.includes('--json');

/**
 * 白名单：**唯二**允许出现被检测内容的文件。
 *
 * - `lib/chartTheme.ts`：色值的唯一集中定义处，硬编码在这里是**设计意图**。
 * - `pages/Login/index.tsx`：登录页不在应用外壳内，无 `PageHeader` 可用，
 *   且其标题字号刻意为 `text-base`（非 `text-lg`），属合法例外。
 */
const WHITELIST = [
  'src/lib/chartTheme.ts',
  'src/pages/Login/index.tsx',
];

/** 只看这些目录，避免把测试 / 脚本自身扫进来 */
const SCAN_DIRS = ['src/pages', 'src/components'];

/** 匹配 3 或 6 位 hex 色值字面量（排除注释行） */
const HEX_RE = /#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?\b/g;
/** 匹配 JSX 中的 `<h1` 开标签 */
const H1_RE = /<h1[\s>]/g;
/** 匹配 `//` 行注释与 `*` 块注释续行（hex 规则跳过这些，注释里举例不算违规） */
const COMMENT_RE = /^\s*(\/\/|\/?\*)/;

/** 递归收集 .ts/.tsx 文件 */
function collectFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...collectFiles(full));
    else if (/\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

const violations = { h1: [], hex: [] };

for (const scanDir of SCAN_DIRS) {
  const abs = join(root, scanDir);
  for (const file of collectFiles(abs)) {
    const rel = relative(root, file).replace(/\\/g, '/');
    if (WHITELIST.includes(rel)) continue;

    const lines = readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, i) => {
      const lineNo = i + 1;

      // 规则 1：页面内不得手写 <h1>（须用 PageHeader）
      if (scanDir === 'src/pages') {
        H1_RE.lastIndex = 0;
        if (H1_RE.test(line)) {
          violations.h1.push({ file: rel, line: lineNo, text: line.trim() });
        }
      }

      // 规则 2：不得硬编码 hex 色值（注释中的举例不算）
      if (CHECK_HEX && !COMMENT_RE.test(line)) {
        HEX_RE.lastIndex = 0;
        const found = line.match(HEX_RE);
        if (found) {
          violations.hex.push({ file: rel, line: lineNo, text: line.trim(), values: found });
        }
      }
    });
  }
}

/* ---------------- 输出 ---------------- */
const total = violations.h1.length + violations.hex.length;

if (AS_JSON) {
  console.log(JSON.stringify({ h1: violations.h1, hex: violations.hex, total }, null, 2));
} else {
  if (violations.h1.length) {
    console.error(`\n[规则 1] 页面内手写 <h1>，应改用 <PageHeader>（${violations.h1.length} 处）:`);
    for (const v of violations.h1) console.error(`  ${v.file}:${v.line}: ${v.text}`);
  } else {
    console.log('[规则 1] <h1> 检查通过（页面均未手写 <h1>）');
  }

  if (CHECK_HEX) {
    if (violations.hex.length) {
      console.error(`\n[规则 2] 硬编码 hex 色值，应改用 @/lib/chartTheme 的 chartPalette()（${violations.hex.length} 处）:`);
      for (const v of violations.hex) {
        console.error(`  ${v.file}:${v.line}: [${v.values.join(', ')}] ${v.text}`);
      }
    } else {
      console.log('[规则 2] hex 色值检查通过（无硬编码色值）');
    }
  } else {
    console.log('[规则 2] hex 色值检查已跳过（--no-hex 逃生舱，仅限临时排查）');
  }
}

if (total > 0) {
  if (!AS_JSON) console.error(`\n共 ${total} 处违规。`);
  process.exit(1);
}
if (!AS_JSON) console.log('\n风格闸门通过。');
