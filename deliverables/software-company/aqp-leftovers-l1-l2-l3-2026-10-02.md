# 遗留项 L-1 / L-2 / L-3 · 处置建议与实施（2026-10-02）

> 这三条是我在《超时一致性 · 界面布局 · 性能优化》报告 §六 里自己留下的遗留项。
> 本文逐条**先取证、再给建议**，并把建议为"应修"的两条当场实施。

---

## 一、结论速览

| 项 | 需要修吗 | 核心理由 | 状态 |
|---|---|---|---|
| **L-1** `bundle:check` 会为陈旧 dist 背书 | ✅ **需要** | **假通过**是本项目红线；且不是假设，是实测 | ✅ **已修** |
| **L-2** 页面层字面量未纳入闸门 | ❌ **不需要** | 取证后：页面层 `_000` 主体是**业务数值**，**无一是请求超时**；纳入会大量误报 | ⚪ **不改，并更正我上一轮的表述** |
| **L-3** `_PATH_BUDGETS` 只 2 条 | ✅ **需要**（但原因与我原先写的不同） | 真正的漏洞不是"条数少"，而是**我的 C3 用"档位值"豁免** ⇒ 新路径复用保留档位会漏网、被后端**误杀** | ✅ **已修** |

---

## 二、L-1：`bundle:check` 的假通过通道 —— **已修**

### 定位原因

`frontend/scripts/check-bundle-size.mjs`（修复前）直接：

```js
const dist = join(process.cwd(), 'dist', 'assets');
for (const file of readdirSync(dist)) { … }
```

**没有任何陈旧性校验**。而 `npm run build` 在本机会**失败**：vite 的
`emptyDir(dist/assets)` 走 `fs.rmSync` → 被宿主 safe-delete shim 拦截 →
`genie-trash … ETIMEDOUT` ⇒ **构建抛错退出，但 `dist` 完好无损**。

⇒ 于是「build 失败 → dist 还是旧的 → `bundle:check` 为**旧产物**背书 → 打印
`Bundle budget passed`」形成一条**假通过**通道，会把真实的体积回归掩盖掉。

> ⚠️ 补充澄清：在 **CI** 里不存在这条通道（每个 step 独立，build 失败即中断，
> `bundle:check` 根本不会执行）。**受害的是本地** —— 尤其是把两步用 `;` 串起来跑的人
> （我自己就是这么发现它的）。

### 修复方案

给 `check-bundle-size.mjs` 加**陈旧性守卫**（并补上"产物不存在"的显式报错）：

```js
const distNewest = newestMtime(distDir);
const srcNewest = Math.max(newestMtime(srcDir),
                           mtimeOf('vite.config.ts'), mtimeOf('package.json'));
if (distNewest < srcNewest) {
  console.error(`❌ dist 比源码陈旧（落后 ${gapS}s）—— 体积检查拒绝为**旧产物**背书。`);
  process.exit(1);
}
```

报错信息里直接给出**处置办法**（先删 `dist` 再构建，附 PowerShell 命令），
让下一个人不必重新踩一遍。

### 验收（实测，非推演）

| 场景 | 结果 |
|---|---|
| 改过 `src/` 但未重建（天然陈旧态） | ❌ `rc=1` —— `dist 比源码陈旧（落后 3041s）` ✅ 拦住 |
| 删 `dist` → 重新构建 → 再检查 | ✅ `Bundle budget passed: raw <= 768000 B, gzip <= 256000 B（产物新鲜度已校验）` |

---

## 三、L-2：页面层字面量 —— **不建议修，并更正我的表述**

### 我上一轮写错了

我原先写的是：「页面层仍有少量**超时字面量**（如 `DataCenter/index.tsx` 的 `12_000`）」。
**取证后发现这是错的。**

### 取证：页面层 `_000` 字面量到底是什么

| 位置 | 实际语义 |
|---|---|
| `Backtest/parts.tsx:54` `initCash: 1_000_000` | **初始资金** |
| `TopKPanel.tsx:29` / `Portfolio:254` `useState(1_000_000)` | **初始资金** |
| `OrderDesk:50` / `Research:157,232` `order_amount: 20_000_000` | **下单金额** |
| `KpiCards.tsx:21` `v >= 10_000 ? …万亿` | **单位换算（亿 → 万亿）** |
| `Research:669` `step={5_000_000} min={100_000}` | **数字输入框步长** |
| `Alerts:412` `}, 30_000);` | **轮询间隔**（`setInterval`） |
| `DataCenter:582` `BASE_MS = 1500; MAX_MS = 12_000;` | **轮询退避上限**（不是超时） |

⇒ **没有一个是请求超时。**

### 建议：**不修**，且不建议扩展闸门

- 用"超时位"正则去扫页面层，会把**资金 / 金额 / 单位换算 / 步长**全部误报 ——
  一个满是误报的闸门会被绕过或直接删掉，**反而更糟**。
- 真正的边界应该是「**超时只在 api 层声明**」，这条已由 **C4** 保证
  （`src/api/**` 不得出现裸超时字面量）。页面层不该出现请求超时。
- 若将来确实要治理"**轮询间隔**散落"（`30_000` / `BASE_MS` / `MAX_MS`），
  那是一个**正交**的问题：应另立 `POLL.*` 命名空间 + 独立闸门，
  且**不要**与"前后端超时预算一致性"混在一张表里 —— 两者的不变量完全不同
  （间隔没有"服务端预算"这个对偶量）。

**结论：L-2 从遗留清单移除，替换为一条明确的边界约定（写进 `timeouts.ts` 与 MEMORY）。**

---

## 四、L-3：`_PATH_BUDGETS` —— 真正该修的是**我的闸门漏洞**（已修）

### 我原先的判断不准确

我原话是「`_PATH_BUDGETS` 目前 2 条。闸门的 C1/C2 已保证两侧同步；新增长超时路径时
两侧都要改，闸门会拦住漏改。」—— **前半句对，后半句漏了一个真实漏洞。**

### 定位原因：C3 用「**档位值**」豁免

闸门 C3 的原实现：

```js
const paired = new Set(Object.values(longPaths));   // {600000, 300000}
for (const [name, ms] of Object.entries(tiers)) {
  if (paired.has(ms)) continue;        // 🔴 只要"值"出现在 LONG_PATHS 里就跳过检查
  if (!(defaultBudget > ms / 1000)) errors.push(...);
}
```

于是这条路径**会静默漏网**：

> 有人给一条**新路径**（例如 `/api/v1/studio/xxx`）直接写 `TIMEOUT.rerun`（300s），
> 却**忘了**登记 `LONG_PATHS`。
> - C3：`rerun` 的值 300_000 在 `paired` 里 ⇒ **跳过检查** ⇒ 放行；
> - C1/C2：只校验 `LONG_PATHS` 与 `_PATH_BUDGETS` 的**交集** ⇒ 不涉及这条新路径；
> - ⇒ 闸门**通过**，但该路径会被后端 240s 默认预算**误杀**（前端还在等、服务端已回 504）。

**这是我建的闸门里的真实漏洞，必须补。**

### 修复方案：把"路径"变成必需实参

**① `timeouts.ts` 新增 `longTimeout(path)`** —— 长超时**只能**经它取得：

```ts
export function longTimeout(path: string): number {
  const ms = LONG_PATHS[path];
  if (ms === undefined) {
    throw new Error(`[timeouts] 路径 ${path} 未在 LONG_PATHS 登记。`
      + '超过后端默认预算（240s）的路径必须显式登记，否则会被后端默认预算误杀（前端还在等、服务端已回 504）。');
  }
  return ms;
}
```

⇒ **运行时**即断言（开发期就炸，不会静默上线），且**路径进入实参**从而可被静态校验。

**② 两个调用点改用它**：`strategyBacktest.ts` 的 `strategy-run`、
`production.ts` 的 `dag/rerun`。

**③ 闸门新增 C5**：

| 子规则 | 防的是 |
|---|---|
| 保留档位**不得**在 `src/api/**` 裸用 `TIMEOUT.<name>` | 新路径复用保留档位却漏登记 |
| `longTimeout('X')` 的实参必须是 `LONG_PATHS` 的键 | 传错/未登记路径（运行时会抛错） |
| `LONG_PATHS` 的每条登记必须**真被用到** | 孤儿登记（某侧改过另一侧没跟） |

**④ 顺带修掉 C5 的一处误报**：C5 初版把我自己的
`src/api/timeouts.test.ts` 当成了生产调用点（测试里"故意传未登记路径以验证抛错"
正是测试目的）⇒ 已排除 `*.test.ts(x)`。**这反过来证明 C5 确实在工作。**

### 验收

```
前端 TIMEOUT 档位 10 个，LONG_PATHS 2 条；后端 _PATH_BUDGETS 2 条，默认预算 240s
✅ 超时一致性闸门通过（C1 误杀 / C2 孤儿 / C3 默认覆盖 / C4 集中登记 / C5 保留档位）
```

**闸门自证伪 9/9**（新增 3 条 C5 夹具，其中一条正是
「**C3 会放行、C5 拦住**」的新路径裸用保留档位场景）。

---

## 五、验收汇总

| 项 | 命令 | 结果 |
|---|---|---|
| 超时一致性闸门 | `npm run timeout:check` | ✅ C1–C5 全绿 |
| **闸门自证伪** | `npm run timeout:check -- --self-test` | ✅ **9/9 条规则可被夹具触发** |
| 前端单测 | `npm test` | ✅ **29 passed**（4 文件，含新增 `timeouts.test.ts` 8 个） |
| 类型 | `npx tsc -b --force` | ✅ rc=0 |
| 构建 | `npm run build` | ✅ |
| **体积上限（含新鲜度）** | `npm run bundle:check` | ✅ 通过（陈旧时 `rc=1`，已实测） |

---

## 六、元教训（本轮最大收获）

> **带漏洞的闸门比没有闸门更糟 —— 它给的是「虚假信心」。**

L-3 的漏洞不是"忘了写某条规则"，而是**规则写对了、豁免条件写错了**：
`paired.has(ms)` 看起来很自然（"这个值已经被长超时路径用了，所以是安全的"），
但豁免的粒度错了 —— **该豁免的是"路径"，不是"值"**。

⇒ 教训：**凡"豁免/白名单"逻辑，必须问一句"豁免的粒度与不变量作用的粒度是否一致"**。
本不变量作用在**路径**上（后端预算按路径配置），豁免却写在**值**上 ⇒ 必然漏网。

配套做法：新增 C5 后，把该场景**做成自证伪夹具**（"C3 会放行、C5 拦住"），
使这条推理变成**可回归的证据**，而不是留在文档里的一句解释。
