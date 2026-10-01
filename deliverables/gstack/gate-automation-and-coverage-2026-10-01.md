# AQP 工程收口报告：闸门自动化 + 覆盖度核查

**日期**：2026-10-01
**场景**：前端脚手架硬化（P0/P1/P2）+ 后端-前端功能覆盖度矩阵（多成员协作）
**参与成员**：脚手架工程师（scaffold-engineer） + 覆盖度审计员（coverage-auditor） + 配色审计员（color-auditor） + 设计系统（design-system） + 主理人（team-lead）
**承接**：`deliverables/gstack/frontend-ia-execution-2026-10-01.md`（同日上场的 IA 改造执行报告）

---

## 📌 TL;DR

- **整体结论：🟢 通过**
- **6 项工作全部完成**（5 项推进项 + 1 项新增覆盖度核查）。
- **P0 门禁已双点落地**：pre-commit（本机首拦）+ CI（不可绕过），**且经端到端实测拦截**。
- **hex 规则已翻转默认 ON**，硬编码色值 0 违规。
- **覆盖度：115 个后端端点，114 已覆盖 / 0 部分覆盖 / 0 缺失 / 1 内部端点；幽灵调用 0。**
- 🔴 **收口过程中发现并修复 1 处上一轮的静默回退**（`Watchlist/index.tsx` 被还原，7 处违规复活）。
- 🔴 **配色清单发现 1 处契约级隐患**（`MiniSpark` 的 `up` 参数会把「未知」静默染成「跌」），已修复。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 **Go** |
| 完成度 | **6 / 6** |
| 质量闸门 | **5 / 5 全绿**（tsc / build / bundle / style-h1 / style-hex） |
| 覆盖度 | **114/115 已覆盖（99.1%）**、缺失 0、幽灵调用 0 |
| 修复的隐患 | 1 处静默回退（P0）+ 1 处契约隐患（P1） |
| 关键行动项 | 4 条（见文末） |

---

## 1. P0 — `style:check` 接入 CI 与 pre-commit

### 交付

| 执行点 | 载体 | 说明 |
|--------|------|------|
| **本机首拦** | `.githooks/pre-commit`（新增，3312 B） | 零依赖 shell；`git config core.hooksPath .githooks` 已设置 |
| **一键启用** | `scripts/install-hooks.sh`（新增） | 供新克隆环境一次性配置 |
| **CI 不可绕过** | `.github/workflows/ci.yml:80` | 插入 `npm run style:check`，位于 `npm run build` **之前** |

### 为什么用 `core.hooksPath` 而不是 husky

本仓是**单人量化项目**，且历史上反复出现「门禁形同不存在」的问题（`ci.yml` 曾因只监听 `main` 而推送 `master` 从不触发；`requirements.txt` 缺 mypy/ruff 导致门禁从未真正运行）。因此裁定 **可靠性 > 优雅**：

- husky 需在 `frontend/` 加依赖、挂 `prepare` 脚本（CI 里 `npm ci` 会触发 `prepare`，需额外防抖），**引入更多可失效环节**。
- `core.hooksPath` 是 git 原生功能，无依赖、无 install 期副作用，失效面最小。

### 为什么 hook 检查全量而非只查 staged files

闸门全量扫描 **< 1s**；而「只查 staged」需 `git diff --cached --name-only | grep` 过滤，引入三类漏检：**重命名**、**部分暂存（`git add -p`）**、**未暂存的存量违规被改文件带过**。收益（省不到 1 秒）远小于风险。

### ✅ 端到端实测（关键：未实测的门禁等于没有门禁）

| 测试 | 操作 | 结果 |
|------|------|------|
| 拦截能力 | 植入一处 `<h1>` 违规 → 跑 hook | **exit 1**，输出「提交被拦截」+ 违规明细 + 修复指引 |
| 逃生舱 | `SKIP_STYLE_HOOK=1` 跑 hook | **exit 0**，输出「逃生舱，勿常态化」 |
| 还原 | 恢复文件 → 跑闸门 | 0 违规，全绿 |

> hook 的取舍已写入注释：**node 缺失时默认放行**（hook 价值是「顺手多拦一道」，不是把本机变成不可提交的砖）；CI 独立检查，不会形成「本地放行 + CI 也放行」的双漏。

---

## 2. P1 — hex 规则翻转默认开启

| 项 | 变更 |
|---|---|
| 默认行为 | 原需 `--hex` 显式开启 → 现 **默认 ON**（`const CHECK_HEX = !process.argv.includes('--no-hex')`） |
| 逃生舱 | 新增 `--no-hex`，注释明确「**是逃生舱，不是常态**」「pre-commit / CI 一律不得携带」 |
| 脚本 | `style:check` 现在即含 hex；`style:check:hex` 退化为语义别名（保留兼容） |
| 文档 | 头部注释同步更新（原「双开关设计」章节改写为「为何翻转为默认」），并保留「已知盲区」章节 |

**改后实测**：`npm run style:check` → 两道规则均通过，0 违规。

---

## 3. P1 — 涨跌配色方向一致性评审清单

**产出**：`deliverables/gstack/checklist-updown-color-audit-2026-10-01.md`（35 KB，**118 条**清单项）

### 结构（超出任务要求）

除逐页清单外，还先审了 **「判定基元」**（被全站复用的单一事实源，如 `pctClass()` / `upDownColor()`），再从基元往下推 —— 这比逐页盲扫更能定位系统性问题。

覆盖 19 个页面 + 状态语义页，含附录（提取脚本 + 三方核对步骤，可复跑）。

### 🔴 发现 1：契约级隐患（已修复）

**`MiniSpark` 的 `up` 参数会把「未知」静默染成「跌」**

- `MarketOverview/pieces.tsx:44` 声明 `up: boolean`（**不可空**）
- 但调用处 `KpiCards.tsx:77` 传 `up={up ?? false}` —— 当方向未知（同卡片文字已用 `text-ink-muted` 表态为**中性**）时，**图形被强制落到 `false` 分支 = 染成跌色绿**
- 后果：**文字说「未知」（灰）、图形说「跌」（绿）** —— 正是本项目反复清理的「替未知表态」模式
- 与同仓正确写法不一致（`Watchlist` 用 `up == null ? FLAT : ...`）

**已修复**（主理人执行）：
- `MiniSpark` 的 `up` 改 `boolean | undefined`，注释写明「**不要**在调用处用 `?? false` 兜底」
- 取色改 `up == null ? pal.FLAT : up ? pal.UP : pal.DOWN`（未知 → 中性）
- 调用处去掉 `?? false`

### 🟡 关键结论：上一轮的 4 处修复已确认自洽

逐行复核确认上一轮修的 4 处（`resultParts` KPI sparkline、月度柱图、`OverviewCards` 资金流卡、`StatsCards` win_rate）**均已修复且自洽**，**未发现新的「文字与图形方向相反」硬矛盾**。

### 🟡 语义歧义（需产品决定，非缺陷）

清单单列了一类 **「色值承担双重语义」** 的位置 —— 红同时表「涨」与「危险/删除」，绿同时表「跌」与「成功」：

| 类别 | 典型位置 | 建议 |
|---|---|---|
| 危险操作按钮用 `bg-up`（红） | `DataCenter:1179`、`Alerts:687`、`Settings:118`、`FactorStudio:311`、`OrderDesk:249,419` | 改走 `danger` token（玫红 `#E11D48`），与 `up`（纯红）色相区分 |
| 「成功/已连接」用 `bg-down`（绿） | `Settings:80,86,415,557` | 圆点改 `bg-success`（文字已用 `text-success`，原文案与圆点色相不一致） |
| 表单校验错误用 `text-up`（红） | `Portfolio:421,440` | 改 `text-danger` |
| 未读角标用 `bg-up`（红） | `Topbar:303` | 通知计数非涨，改 `danger` |
| 装饰性图标用 `stroke={p.UP}` | `Watchlist:99` | 注释自陈「不表示涨跌」却用了涨跌 token ⇒ **注释约束不住代码**的典型 |

> 这类**方向本身不矛盾**（文字/图形同色），但色值串了两套语义系。`index.css:27-28` 已声明「涨跌系与状态系正交解耦」的意图，实现层尚未贯彻。

---

## 4. P2 — `SectionCard` 默认内边距收敛

### 结论：已收敛，但**原「124 处覆盖」这个数字口径有误**

实测口径澄清：

| 口径 | 实测值 |
|---|---|
| `bodyClassName` 出现次数（真正的 prop 覆盖点） | **52 处** |
| 其中**显式覆写 padding** 的 | **仅 21 处**（`py-2.5` 13 / `px-3` 13 / `p-2` 7 / `p-6` 1，部分重叠计数） |
| 其余 31 处 | 只加结构类（`flex flex-col` 等），**未覆写 padding** |

⇒ 原「124 处覆盖」应为**混算了**「含 `bodyClassName` 的调用点」与「显式覆写 padding 的调用点」，且可能含其它 prop。

### 实际所做的收敛

`SectionCard` 默认 body padding 由 **`p-4` → `p-3`**，并附注释说明：

```
默认 p-3：全站 32 处调用点原本显式覆盖为 p-3（占主导），故内联为默认值。
⚠️ 仍显式传 bodyClassName 的调用点，都是与 p-3 有**实际差异**的意图
   （p-2 密集表格 / p-0 贴边容器 / px-3 py-2.5 紧凑行 / 仅加 flex 结构类），
   不要为了统一而删除。
```

**保留的 21 处覆盖全部有实际差异**，逐类核实：

- `EtfDetail` ×4、`Portfolio` ×3 → `p-2`（密集表格，需更紧）
- `Report` ×1 → `p-6 text-center`（空态居中，需更松）
- `StockDetail` ×13 → `px-3 py-2.5`（紧凑列表行，纵向节奏不同）

**验证**：`npm run build` 通过（7.11s）。

> 判定：**收敛目标成立**（默认值从「被覆盖」变成「被多数采用」），但**收益比原描述小** —— 因为真实覆写只有 21 处而非 124 处。

---

## 5. P2 — 移动端适配立项

### 结论：**建议不做全量适配，仅做「最小可接受档」**

**理由（基于实测，非印象）**：

| 证据 | 数值 | 含义 |
|---|---|---|
| `sm` 断点使用 | **2 次** | 移动端等于未适配 |
| `xl` / `lg` | 各 33 次 | 布局是**为宽屏设计的**（数据密集） |
| `grid-cols-2` / `grid-cols-1` | 42 / 33 | 已有一定降级，但非移动优先 |
| `<table>` 总数 | **33** | 表格是移动端最大的敌人 |
| `overflow-x-auto` 包裹数 | **13** | ⇒ **20 个表格裸露**，窄屏会撑破布局 |
| `viewport` meta | ✅ 已存在 | 至少不会整体缩放失真 |

**核心判断**：这是**个人量化系统**，观察场景以桌面盯盘为主。全量移动适配会为了窄屏牺牲宽屏的信息密度（数据密集页强行降级 = 两端都不好用），**可能负收益**。

### 最小可接受档（建议执行，成本 S）

**只做一件事：给 20 个裸露表格加 `overflow-x-auto` 包裹。**

这能保证 390px 下**不出现横向溢出/布局崩坏**，且**零风险**（不改任何视觉设计，只加滚动容器）。

裸露表格清单（按文件）：

```
Backtest/index.tsx (2/1)      Backtest/resultParts.tsx (1/0)
CapacityAttribution (1/0)     DataCenter/TextDataPanel (1/0)
DataCenter/index.tsx (2/1)    DataQuality/index.tsx (1/0)
Etf/index.tsx (4/1)           FactorStudio/FactorLab (1/0)
FactorStudio/index.tsx (2/0)  MarketOverview/MoneyFlowPanel (1/0)
OrderDesk/index.tsx (2/0)     Pipeline/index.tsx (1/0)
Research/index.tsx (1/0)      Research/parts.tsx (2/0)
Screener/index.tsx (4/3)      Watchlist/index.tsx (2/1)
```

### 明确「不做清单」

侧边栏抽屉化、K 线图触控手势、表格卡片化降级、`sm` 以下断点体系 —— **均不建议做**，除非用户确认「需要在手机上看盘」。

---

## 6. 【新增】后端-前端功能覆盖度矩阵

**产出**：`deliverables/gstack/coverage-backend-frontend-2026-10-01.md`（19.7 KB）

### 汇总

| 状态 | 数量 | 占比 |
|---|---:|---:|
| **已覆盖** | **114** | **99.1%** |
| 部分覆盖 | 0 | 0% |
| **缺失** | **0** | **0%** |
| 内部端点（无需前端） | 1 | 0.9% |
| 合计 | 115 | 100% |

**幽灵调用（前端调了但后端没有）：0 个。**
前端 119 处 `/api/v1` 路径字面量（去重 114 个不同路径，含 8 处模板字符串），**100% 命中后端真实路由**。

### 逐模块状态（19 个路由模块）

`auth` 4/4 · `alerts` 7/7 · `market` 4/5(1 内部) · `notify` 3/3 · `stock` 5/5 · `etf` 8/8 ·
`datacenter` 22/22 · `desk` 12/12 · `export` 3/3 · `screener` 4/4 · `backtest` 3/3 ·
`portfolio` 2/2 · `research` 11/11 · `studio` 9/9 · `ops` 4/4 · `watchlist` 2/2 ·
`settings` 7/7 · `monitor` 2/2 · `report` 2/2

**全部 = 已覆盖（或内部端点）**，无一处缺失。

### 方法论要点（本次审计最重要的贡献）

1. **聚合层不可机械匹配** —— `production.ts`(19 路径) / `datacenter.ts`(21 路径) 是**跨路由聚合层**，一个文件打多个后端前缀（如 `researchApi` → `/research` + `/studio`）。若按「文件名 ↔ 路由名」配对会**误判大片缺失**。
2. **运维端点不应计入缺失** —— 识别出 1 个真正的内部端点（`GET /api/v1/market/overview-rt` 等健康/定时类用途），单独归类而非报缺失。
3. **路径计数口径必须声明** —— 审计员**主动废弃了自己早期的「159 处 @ 130 路径」口径**（因把 `api/` 目录外的重复引用与类型定义字符串一并计入），最终口径为 **119 处字面量 / 114 去重路径**。这种自我纠错应予肯定。

### 结论

**后端能力已被前端完整暴露**，无「后端做了但用户摸不到」的功能。这一点与上一轮发现的「2 个页面被摘除但入口没建」形成对照 —— 覆盖度问题已在上一轮修复。

---

## 🔴 收口过程中发现的两个问题（均已修复）

### 1. 上一轮成果的静默回退（P0）

`frontend/src/pages/Watchlist/index.tsx` **被还原到 git HEAD 版本**，导致上一轮修的 **7 处违规复活**：
- 1 处手写 `<h1>`（应走 `PageHeader`）
- 6 处硬编码 hex（`#94A3B8` `#EF4444` `#22C55E` `#3B82F6` `#F59E0B`）

**发现方式**：脚手架工程师在接入 CI 时跑 `style:check` 发现 **exit=1**，而主理人上一轮验收时该文件是绿的 —— 正是**闸门第一次发挥了「发现回退」的作用**。

**修复**（主理人执行）：
- `Sparkline` 取色改 `up == null ? p.INKM : up ? p.UP : p.DOWN`（未知 → 中性，不替未知表态）
- `KpiIcon` 4 个图标色改走 `p.BRAND` / `p.UP` / `p.WARN` / `p.INFO`
- `CorrModal` 热力图 `['#22C55E','#FFFFFF','#EF4444']` → `[p.DOWN, p.CARD, p.UP]`；effect deps 补 `theme`
- `<h1>` → `<PageHeader title="我的收藏" />`

**修复后验证**：`style:check` 0 违规、`tsc` 0 error。

### 2. `MiniSpark` 契约隐患（P1）

见 §3。已修复并把「不得用 `?? false` 把未知染成跌」写进类型注释。

---

## 5 道闸门验收证据（全绿）

| 闸门 | 命令 | 结果 |
|------|------|------|
| 样式（含 hex，默认开） | `npm run style:check` | ✅ 0 违规 |
| 类型检查 | `npx tsc -b --force` | ✅ 0 error |
| 生产构建 | `npx vite build` | ✅ built in 7.11s |
| 体积预算 | `npm run bundle:check` | ✅ raw ≤ 768000 B、gzip ≤ 256000 B |
| pre-commit 实测 | `sh .githooks/pre-commit`（植入违规） | ✅ **exit 1 拦截**；`SKIP_STYLE_HOOK=1` → exit 0 |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 |
|---|------|--------|--------|
| 1 | **给 20 个裸露 `<table>` 加 `overflow-x-auto`**（移动端最小可接受档，成本 S、零视觉风险） | 前端 | **P1** |
| 2 | **决定「语义歧义」类是否引入第三组色** —— 红=涨 vs 红=危险、绿=跌 vs 绿=成功共 5 处典型位置（清单 §A/B 已列全） | 产品 | **P1** |
| 3 | 把 `.githooks/pre-commit` 的启用写进 README（新环境需跑 `git config core.hooksPath .githooks`） | 工程 | P2 |
| 4 | 移动端全量适配**暂缓**；若确认需手机看盘再立项（本报告 §5 已给出判断依据） | 产品 | P2 |

---

## ⚠️ 待完善 / 已知局限

1. **`SectionCard` 原「124 处覆盖」口径有误**（实测真实覆写仅 21 处）—— 本报告已更正，但**上一轮报告中的该数字未回改**，引用时请以本报告为准。
2. **pre-commit 在 node 缺失时默认放行** —— 刻意取舍（避免把本机变成不可提交的砖），CI 端独立检查，不形成双漏。但如果 CI 也被绕过（比如直接推 tag），仍存在漏网。
3. **配色清单的「语义歧义」类未做修改** —— 属产品决策项，审计员只记录不改（避免越权改变视觉语义）。
4. **覆盖度审计是「端点级」而非「字段级」** —— 确认了 114/115 端点有前端调用，但**未逐字段核对**（例如某端点返回 8 个字段、前端只用 3 个，不会体现为「部分覆盖」）。本次抽样未发现此类，但不排除存在。
5. **移动端立项基于静态代码勘察** —— 未在真机/模拟器实测，`sm` 断点下实际渲染效果未验证。
6. **`design-system` 成员在 SectionCard 议题上未回传正式报告**（代码已落地并经主理人独立复核 + build 验证），移动端议题由其独立判断补完。该成员的产出是**代码 + 本报告 §4/§5 的独立复核**，而非自述。

---

## 📚 成员产出索引

| 成员 | 产出 |
|------|------|
| **scaffold-engineer** | `.githooks/pre-commit`、`scripts/install-hooks.sh`、`ci.yml` 接入、hex 默认翻转；**端到端实测拦截**；发现 Watchlist 静默回退 |
| **coverage-auditor** | `coverage-backend-frontend-2026-10-01.md`（115 端点矩阵）；纠正主理人 3 处错误假设；主动废弃自身早期口径 |
| **color-auditor** | `checklist-updown-color-audit-2026-10-01.md`（118 条）；发现 `MiniSpark` 契约隐患；识别 5 类语义歧义 |
| **design-system** | `SectionCard` 默认 `p-3` 收敛（含注释）；移动端判断；口径澄清（124 → 实际 21） |
| **team-lead** | 团队编排；修复 Watchlist 回退 + MiniSpark 契约；独立复核全部队友产出；本报告汇编 |

**关联文档**：
- 前序：`deliverables/gstack/frontend-ia-execution-2026-10-01.md`
- 设计规范：`deliverables/gstack/AQP-frontend-design-spec.md`
- 覆盖度矩阵：`deliverables/gstack/coverage-backend-frontend-2026-10-01.md`
- 配色清单：`deliverables/gstack/checklist-updown-color-audit-2026-10-01.md`

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
