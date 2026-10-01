/**
 * 导航完整性闸门（两条规则）。
 *
 * ## 为什么需要它
 *
 * 2026-10-01 的 IA 重构中，`Sidebar.tsx` 的「ETF 中心」与「ETF 分析」两个入口
 * **被一起删除**，但 `/etf` 与 `/etf/:code` 的**路由仍然存在**。用户报障原话：
 * 「etf中心和etf分析界面怎么没有了」。
 *
 * 更值得注意的是：`Sidebar.tsx` 的注释里**已经写明了这条纪律**
 *   「凡声明"由 X 进入"的，X 必须真的有链接指向它，否则就是把功能藏起来」
 * —— 这正是「约定只写进注释、没有执行点」的老毛病（见 `check-style.mjs` 头部）。
 *
 * ## 建模过程中踩的两个坑（记录以免重蹈）
 *
 * **坑 1：把规则写成"是否存在任意站内链接"** —— 抓不住本 bug。
 * 因为 `EtfDetail` 页有"返回 /etf"的链接、`Etf` 页有指向自己的"查看全部"链接，
 * 于是 `/etf` 被误判为"有入口"。**自引用链接不是入口。**
 *
 * **坑 2：改成"从导航出发做可达性 BFS"** —— 仍然抓不住。
 * 因为 `/etf/:code` 能从「自选池」的 ETF 项点进去（`Watchlist/index.tsx`），
 * 而 `EtfDetail` 又有"返回列表"链接 ⇒ BFS 顺着 自选池 → 详情 → 返回 绕回了 `/etf`。
 * 更糟的是 `Topbar` 的全局搜索框（`to={'/etf/'+code}`）渲染在**每个页面**上，
 * 会让 `/etf/*` 永远"可达"。
 *
 * **结论：真正的缺陷不是"不可达"，而是"索引页被静默降级"。**
 * 因此规则必须直接针对"**索引页必须有导航入口**"来写，而不是绕道可达性。
 *
 * ## 两条规则
 *
 * **规则 1（索引页必须有导航入口）**：若路由 R 存在子路由（某路由以 `R + '/'` 开头），
 *   则 R 是"列表/索引页"——它是功能区的入口，**必须有导航项**。
 *   只靠子页面的"返回"链接到达，等于把功能区藏起来（用户得先能到子页面）。
 *   确需降级时，登记到 `INDEX_WHITELIST` 并写明理由（强制显式声明）。
 *
 * **规则 2（孤儿路由）**：无导航入口的路由，必须能从**本路由子树之外**的页面链接到达。
 *   判定时排除两类"假入口"：
 *     · **自引用**：来自 R 自身子树的链接（含子页面"返回父页"）；
 *     · **全局 shell 组件**（`Topbar` 搜索框等）——它是工具，不是功能区入口。
 *
 * **规则 3（高亮互斥）**：同一路径空间下多个导航项在任一 URL 上**至多一个**高亮。
 *   反例：`ETF 中心`（`/etf`）若也用前缀匹配，会与 `ETF 分析`（`/etf/*`）在
 *   `/etf/510300` 上同时高亮；反之若两者都精确匹配，进详情页则都不亮。
 *   正确做法：列表页精确匹配 + 详情页 `prefix: true` + `matchBase`。
 *
 * ## 用法
 *
 *   node scripts/check-nav-integrity.mjs            # 跑三条规则
 *   node scripts/check-nav-integrity.mjs --json     # 机器可读输出
 *
 * ## 已知盲区（勿据此绕过）
 *
 * 1. **只做静态文本分析**，不理解运行时条件渲染。藏在 `{cond && <Link/>}` 里的入口
 *    仍算作有边（乐观判定）。刻意取舍：宁可漏报，不可误报 —— 误报会引发"改白名单"。
 * 2. **动态段链接按静态前缀匹配**：`` `/etf/${code}` `` → 前缀 `/etf/`。
 *    若前缀本身是变量（`` `${base}/x` ``）则无法识别。
 * 3. **不判断"入口是否显眼"**：导航项存在即算可达，可能在折叠组里或需要滚动。
 *    本闸门只保证"**可达**"，不保证"**易达**"。
 */
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, relative, dirname, resolve } from 'node:path';

const root = process.cwd();
const AS_JSON = process.argv.includes('--json');

const APP = 'src/App.tsx';
const SIDEBAR = 'src/components/Sidebar.tsx';

/**
 * 索引页白名单：**有意**不给导航入口的索引页（有子路由）。
 * 每项必须写明理由，否则就是掩盖缺陷。
 */
const INDEX_WHITELIST = {
  // 暂无。若将来把某索引页降级为"由 X 进入"，在此登记并写明 X 的入口位置。
};

/**
 * 全局 shell 组件：渲染在**每个页面**上的跨切面组件。
 * 它们内部的跳转（如 Topbar 搜索框）是**工具**，不构成功能区的入口。
 * ⚠️ 把它们算作入口会让任何页面都"永远可达"，规则形同虚设（踩过，见文件头坑 2）。
 */
const GLOBAL_SHELL = ['src/components/Topbar.tsx', 'src/components/Sidebar.tsx'];

/** 有意为之的无入口路由（非索引页） */
const ROUTE_WHITELIST = {
  '/market': 'App.tsx 注释：保留为 `/` 的公开兼容别名（旧链接不失效）',
  '/login': '登录页，由鉴权重定向到达，不经侧栏',
};

const SCAN_DIRS = ['src/pages', 'src/components', 'src/api', 'src/lib', 'src/stores'];

function collectFiles(dir) {
  const out = [];
  let names;
  try {
    names = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of names) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...collectFiles(full));
    else if (/\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

function resolveModule(fromFile, spec) {
  const base = resolve(dirname(fromFile), spec);
  for (const c of [`${base}.tsx`, `${base}.ts`, join(base, 'index.tsx'), join(base, 'index.ts')]) {
    if (existsSync(c)) return c;
  }
  return null;
}

const norm = (p) => relative(root, p).replace(/\\/g, '/');

/**
 * 剥离注释后再解析（**状态机扫描**，不用正则）。
 *
 * ## 为什么必须剥离
 * ⚠️ 实证过：把某个导航项**注释掉**时，正则仍能匹配到那行文本，闸门误以为入口还在。
 * 等于"注释掉入口 = 绕过闸门"，恰好是闸门要防的场景。
 *
 * ## 为什么不能用正则（踩过，务必别改回）
 * 最初写成"先剥块注释、再剥行注释"的两条正则，结果**把整个 NAV_GROUPS 吃掉**：
 * 本文件正文的注释里写了「覆盖所有斜杠 etf 斜杠 星号 详情页」这种字样，
 * 其中的「斜杠 + 星号」被块注释正则当成了块注释起点，一路吞到几十行后
 * 某个 JSDoc 的结束标记 —— 导航项全部消失，闸门随即误报 8 个孤儿。
 *
 * 教训：**注释的边界只能靠状态机判定**，任何"按标记切分"的正则都会在
 * "注释里出现注释标记"时失效。换顺序（先剥行注释）同样不安全：
 * 块注释里也可能出现连续两个斜杠。
 *
 * ## 实现
 * 单遍扫描，维护 6 个状态：代码 / 行注释 / 块注释 / 单引号串 / 双引号串 / 模板串。
 * 注释内容替换为等量空格（保留换行与列位置，便于将来做行号报错）。
 *
 * **已知局限**：模板串按整体字符串处理，不解析其内部插值表达式 ——
 * 故模板里的插值中若含注释不会被剥离。对本仓库的导航/路由字面量无影响。
 */
function stripComments(src) {
  let out = '';
  let i = 0;
  let state = 'code';
  const isQuote = (s) => s === 'single' || s === 'double' || s === 'template';

  while (i < src.length) {
    const c = src[i];
    const n = src[i + 1];

    if (state === 'code') {
      if (c === '/' && n === '/') { state = 'line'; out += '  '; i += 2; continue; }
      if (c === '/' && n === '*') { state = 'block'; out += '  '; i += 2; continue; }
      if (c === "'") state = 'single';
      else if (c === '"') state = 'double';
      else if (c === '`') state = 'template';
      out += c;
      i += 1;
      continue;
    }

    if (state === 'line') {
      if (c === '\n') { state = 'code'; out += c; i += 1; continue; }
      out += ' ';
      i += 1;
      continue;
    }

    if (state === 'block') {
      if (c === '*' && n === '/') { state = 'code'; out += '  '; i += 2; continue; }
      out += c === '\n' ? '\n' : ' ';
      i += 1;
      continue;
    }

    // 字符串态：原样保留（转义不结束字符串）
    if (c === '\\') { out += c + (n ?? ''); i += 2; continue; }
    if (
      (state === 'single' && c === "'") ||
      (state === 'double' && c === '"') ||
      (state === 'template' && c === '`')
    ) {
      state = 'code';
    }
    out += c;
    i += 1;
  }
  return out;
}

/* ---------------- 解析路由 ↔ 页面文件 ---------------- */

const appPath = join(root, APP);
const appSrc = stripComments(readFileSync(appPath, 'utf8'));

const lazyModules = {};
for (const m of appSrc.matchAll(
  /const\s+(\w+)\s*=\s*lazy\(\s*\(\)\s*=>\s*import\('([^']+)'\)\s*\)/g,
)) {
  const f = resolveModule(appPath, m[2]);
  if (f) lazyModules[m[1]] = f;
}

const routes = [];
const routeToFile = {};
for (const chunk of appSrc.split('<Route ').slice(1)) {
  const pm = chunk.match(/^path="([^"]+)"/);
  if (!pm) continue;
  const route = pm[1];
  routes.push(route);
  const end = chunk.indexOf('/>');
  const body = chunk.slice(0, end === -1 ? 400 : end);
  for (const [comp, file] of Object.entries(lazyModules)) {
    if (new RegExp('<' + comp + '\\b').test(body)) {
      routeToFile[route] = file;
      break;
    }
  }
}

/* ---------------- 解析导航项 ---------------- */

const sidebarSrc = stripComments(readFileSync(join(root, SIDEBAR), 'utf8'));

function parseNavItems(code) {
  const consts = {};
  for (const m of code.matchAll(/const\s+([A-Z_][A-Z0-9_]*)\s*=\s*'([^']+)'/g)) consts[m[1]] = m[2];
  const items = [];
  for (const m of code.matchAll(/\{([^{}]*?to:\s*([`'"])([^`'"]+)\2[^{}]*?)\}/g)) {
    const obj = m[1];
    const label = (obj.match(/label:\s*'([^']+)'/) || [])[1] ?? '?';
    const to = m[3].replace(/\$\{([A-Z_][A-Z0-9_]*)\}/g, (_, n) => consts[n] ?? '');
    items.push({
      to,
      label,
      prefix: /prefix:\s*true/.test(obj),
      matchBase: (obj.match(/matchBase:\s*'([^']+)'/) || [])[1],
    });
  }
  return items;
}

const navItems = parseNavItems(sidebarSrc);

/** 与 Sidebar 的 isActive 保持同一语义 */
function navMatches(route, item) {
  if (!item.prefix) return route === item.to;
  const base = item.matchBase ?? (item.to.endsWith('/') ? item.to : item.to + '/');
  return route === item.to || route.startsWith(base);
}

const hasNav = (r) => navItems.some((i) => navMatches(r, i));

/* ---------------- 链接图 ---------------- */

function extractLinks(src) {
  const links = [];
  const re = /(?:navigate\(\s*|to=\s*|href=\s*)['"`]([^'"`]+)['"`]/g;
  for (const m of src.matchAll(re)) {
    const raw = m[1];
    if (!raw.startsWith('/')) continue;
    const cut = raw.indexOf('${');
    if (cut === -1) links.push({ raw, prefix: false });
    else if (raw.slice(0, cut)) links.push({ raw: raw.slice(0, cut), prefix: true });
  }
  return links;
}

function matchRoute(link) {
  for (const r of routes) {
    if (r === '*') continue;
    if (r === link.raw) return r;
    if (link.prefix && r.startsWith(link.raw)) return r;
  }
  return null;
}

/** route → 指向它的「文件 → 是否属于该路由子树」 */
const inbound = new Map(); // route -> [{file, fromSubtree}]
for (const f of SCAN_DIRS.flatMap((d) => collectFiles(join(root, d)))) {
  const rel = norm(f);
  const src = readFileSync(f, 'utf8');
  for (const l of extractLinks(src)) {
    const r = matchRoute(l);
    if (!r) continue;
    // 该文件是否属于路由 r 的子树？（即它自己就是 r 或 r 的子路由页面）
    const subtreeFiles = new Set(
      [r, ...routes.filter((x) => x.startsWith(r + '/'))]
        .map((x) => routeToFile[x])
        .filter(Boolean)
        .map(norm),
    );
    if (!inbound.has(r)) inbound.set(r, []);
    inbound.get(r).push({ file: rel, fromSubtree: subtreeFiles.has(rel) });
  }
}

/* ---------------- 规则 1：索引页必须有导航入口 ---------------- */

const staticRoutes = routes.filter((r) => r !== '*' && !r.includes(':'));

const indexMissing = [];
for (const r of staticRoutes) {
  const children = routes.filter((x) => x !== r && x.startsWith(r + '/'));
  if (!children.length) continue;
  if (hasNav(r) || Object.hasOwn(INDEX_WHITELIST, r)) continue;
  indexMissing.push({ route: r, children });
}

/* ---------------- 规则 2：孤儿路由 ---------------- */

const orphans = [];
for (const r of staticRoutes) {
  if (hasNav(r) || Object.hasOwn(ROUTE_WHITELIST, r)) continue;
  const ext = (inbound.get(r) ?? []).filter(
    (e) => !e.fromSubtree && !GLOBAL_SHELL.includes(e.file),
  );
  if (!ext.length) orphans.push({ route: r, links: inbound.get(r) ?? [] });
}

/* ---------------- 规则 3：高亮互斥 ---------------- */

const probePaths = new Set();
for (const r of routes) {
  if (r === '*') continue;
  if (r.includes(':')) {
    for (const i of navItems) if (i.to.startsWith(r.split(':')[0])) probePaths.add(i.to);
  } else probePaths.add(r);
}

const collisions = [];
for (const p of probePaths) {
  const active = navItems.filter((i) => navMatches(p, i));
  if (active.length > 1) collisions.push({ path: p, active: active.map((i) => i.label) });
}

/* ---------------- 输出 ---------------- */

const total = indexMissing.length + orphans.length + collisions.length;

if (AS_JSON) {
  console.log(
    JSON.stringify(
      { indexMissing, orphans, collisions, navItems, routes: staticRoutes, total },
      null,
      2,
    ),
  );
} else {
  if (indexMissing.length) {
    console.error(`\n[规则 1] 索引页缺导航入口 —— 有子路由却无侧栏入口（${indexMissing.length} 个）:`);
    for (const v of indexMissing) {
      console.error(`  ${v.route}  (子路由: ${v.children.join(', ')})`);
    }
    console.error('  ⇒ 索引页是功能区的入口，不该静默降级。请补导航项，');
    console.error('     或在 INDEX_WHITELIST 登记并写明"由哪个已可达页面进入"。');
  } else {
    console.log('[规则 1] 索引页导航入口检查通过（所有有子路由的页面均有侧栏入口）');
  }

  if (orphans.length) {
    console.error(`\n[规则 2] 孤儿路由 —— 无导航入口且无子树外入链（${orphans.length} 个）:`);
    for (const o of orphans) {
      console.error(`  ${o.route}`);
      for (const l of o.links) {
        console.error(`      仅有的入链 ${l.file} ${l.fromSubtree ? '(自身子树，不算入口)' : ''}`);
      }
    }
    console.error('  ⇒ 用户进不去。自引用/子页返回链接与全局搜索框都**不算**入口。');
  } else {
    console.log(`[规则 2] 孤儿路由检查通过（无入口的路由均有子树外入链）`);
  }

  if (collisions.length) {
    console.error(`\n[规则 3] 导航高亮冲突 —— 同一路径下多入口同时高亮（${collisions.length} 处）:`);
    for (const c of collisions) console.error(`  ${c.path}: 同时高亮 [${c.active.join(', ')}]`);
    console.error('  ⇒ 列表页应**精确匹配**，详情页应配 `prefix: true` + `matchBase`。');
  } else {
    console.log(`[规则 3] 导航高亮互斥检查通过（${probePaths.size} 条路径均至多一个高亮）`);
  }
}

if (total > 0) {
  if (!AS_JSON) console.error(`\n共 ${total} 处违规。`);
  process.exit(1);
}
if (!AS_JSON) console.log('\n导航完整性闸门通过。');
