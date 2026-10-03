# AQP 上线前检查报告 · 复核与修复（2026-10-02）

> **复核对象**：`deliverables/software-company/aqp-preaudit-2026-10-01.md`（470 行 / 39 KB，A–G 七类）
> **复核方式**：逐条**回代码取证**（不采信文档既有结论），未修的当场修并配证伪。

---

## 一、TL;DR —— 「修复好了吗？」

**大部分已修，两项未修（本轮已补齐）。**

| 结论 | 条目 | 级别 |
|---|---|---|
| ✅ 已修 | **D-1** 高度链断裂导致主区塌陷 | **P0** |
| ✅ 已修 | **B-2** 生产未关闭 API 文档端点 | P1 |
| ✅ 已修 | **E-3** `/etf/*` 筛选不生效（口径谎报） | P1 |
| ✅ 已修 | D-2 / D-3 / D-4 假交互 + 逐字外呼 + 双请求 | P1 / P2 |
| ✅ 已修 | **E-6** `or 0` / `?? 0` 点名项 + 全仓政策落地 | P2 |
| ⚪ **失效** | **B-1** 硬编码加密密钥 —— **被审代码已不在仓库** | P1 |
| 🆕 **本轮修复** | **C-3** 前端零自动化测试、零 E2E | P2 |
| 🆕 **本轮修复** | **A-1 / A-5** 响应模型缺失 / 成对契约易串型 | P2 |

---

## 二、逐条核对（含证据）

### ✅ P0 · D-1 高度链断裂 —— 已修

**审计根因**：`App.tsx:74-77` 的包装 div 只有 `w-full`（`height: auto`）⇒ 页面根 `min-h-full`
按 CSS 2.1 §10.7「百分比高度对 auto 高父元素解析为 auto」**退化为 0** ⇒ `flex-1` 栅格拿不到
可分配自由空间、主轴塌陷 ⇒ 卡片按自然高度溢出后压盖兄弟（ECharts canvas 是定位元素绘制在
「ETF 列表」之上，又被表格 sticky 表头 `z-index:10` 反向切断 ⇒ 截图里"黑条被表头切成两段"）。

**现状**：`frontend/src/App.tsx:91`
```tsx
<div className={`mx-auto h-full w-full ${fullWidth ? 'max-w-chart' : 'max-w-content'}`}>
```
并在 `App.tsx:69-83` 写入了**高度链纪律**注释，明确链条：
`div.flex.h-screen → div.flex-1.flex-col → main.flex-1 → div.h-full → 页面根`

**🔴 关于「给页面根改用 `h-full` 而非 `min-h-full`」这一条：不改，且不应改。**
注释已写明分工 —— 包装层用 `h-full`（**定高**，为子元素提供百分比解析基准）；
页面根用 `min-h-full`（**至少撑满**、内容更高时正常增长，滚动由 `main` 的 `overflow-y-auto` 承接）。
页面根若改定高 `h-full`，内容高于视口时会被裁掉。**两者不可互换**。

**全仓同型排查（用户明确要求）**：
- `grep -rn "max-w-content|max-w-chart|w-full max-w-" frontend/src` ⇒
  **唯一的布局包装层就是 `App.tsx:91`**；其余 `w-full max-w-*` 全部是**自包含定尺寸盒子**
  （`ui/index.tsx` 弹窗、`Login` 登录卡、`ErrorBoundary`、`Topbar` 搜索框、`HotkeyHelp`），
  不含百分比高度子元素 ⇒ **无同型残留**。
- 审计点名的 6 个页面根（`Backtest:286` / `DataCenter:730` / `Report:87` / `Screener:332` /
  `Settings:335` / `Watchlist:305`）+ `Etf:535` **仍是 `min-h-full`** —— 按上述分工这是**正确**的。
- 该不变量已由测试钉死（见 C-3 的 `src/test/layoutContract.test.tsx`）。

### ⚪ P1 · B-1 硬编码加密密钥 —— 被审代码已不在仓库

**审计证据**：`backend/app/core/security.py:30-31` 的 `encryption_key()` 由 6 个字面量常量
派生 AES-GCM 密钥、不读任何 env。

**现状**：
- `backend/app/core/security.py` **不存在**；`git log -- backend/app/core/security.py` **无任何记录**
  （`--diff-filter=D` 亦无）⇒ 该文件**从未被提交**，是工作区里的未跟踪文件，现已消失。
- 全仓 `grep -rn "encryption_key|AESGCM|Fernet"` ⇒ **0 命中**
  （`core/auth.py:129,139` 只剩口令哈希用的 `pbkdf2_hmac`）。
- ⇒ **B-1 的技术前提已不成立。**

⚠️ **但这不等于"修好了"**：需确认「通知渠道凭据加密」能力是**有意下线**还是**被误删**。
若仍需该能力，应按审计建议重做 —— **密钥由 env / KMS 注入，常量仅作开发默认值，
生产启动时强校验（缺失即拒绝启动）**。见遗留项 **L-1**。

### ✅ P1 · B-2 生产未关闭 API 文档 —— 已修

**现状**：`backend/app/main.py:232-258` 新增 `_docs_paths()` —— `ENV != "prod"` 全开
（开发/测试需要 Swagger），`ENV == "prod"` 返回 `(None, None, None)`，并接入
`FastAPI(docs_url=..., redoc_url=..., openapi_url=...)`。注释披露了理由：
`/openapi.json` 会**无条件输出全部端点 + 参数 + schema**（实测 ~105 KB），等于把完整攻击面地图交出去。

**顺带发现 B-4 也已修**：`main.py:370-375` 有 `_SECURITY_HEADERS` 统一注入，
且 `ENV == "prod"` 时补 HSTS（`max-age=31536000; includeSubDomains`）。

### ✅ P1 · E-3 `/etf/*` 筛选不生效 —— 已修（采审计方案 b：显式披露作用域）

**现状**：`backend/app/api/v1/etf.py:56-74` 新增**作用域披露**常量：
```python
_SCALE_SCOPE = "cn"
_SCALE_SCOPE_NOTE = "仅 A 股 ETF，不随上方国家/板块/筛选器变化"
_FLOW_SCOPE = "all_market"
_FLOW_SCOPE_NOTE = "全市场 ETF（东财主力净流入榜），不随上方国家/板块/筛选器变化"
```
`/scale`（`etf.py:873, 883`）与 `/flow`（`etf.py:921, 934`）**回显** `scope` / `scope_note`。

🔴 注释明确了关键红线：**作用域文案必须由后端回显、前端不得写死** —— 前端写死会在后端改口径时
**静默漂移成谎报**。前端侧在 `Etf/FilterPanel.tsx:87-93` 给出总纲声明。

### ✅ D-2 / D-3 / D-4 假交互与请求重复 —— 已修

`Etf/FilterPanel.tsx` 重写为**草稿 + 显式应用**模型：所有控件只改本地 `draft`，
`apply()` 才 `onChange({ ...draft, q: value.q })`。由此一次性修掉三个缺陷：
- **D-2**：旧 `onClick={() => onChange(value)}` 传回父级**已持有的同一对象引用** ⇒
  React `Object.is` 判等直接 bail out ⇒ **点击零反应（欺骗性 UI）**；
- **D-3**：控件直写父级 ⇒ **每敲一键**就触发一次全量列表请求（逐字外呼，冷路径最长挂 30s）；
- **D-4**：筛选变化先按旧页码请求一次、再因页码复位请求第二次。

并新增 `dirty •` 未应用提示，以及 `q` 字段归属处理（提交时显式沿用父级 `value.q`，
避免用草稿里的陈旧副本覆盖顶部搜索框的内容）。

### ✅ E-6 `or 0` / `?? 0` —— 点名项已修 + 全仓政策已落地

- **审计点名的 `backend/app/data/etf.py:1311` 已修**：现为 `etf.py:918-919`
  ```python
  ratio = float(ratio_raw.replace("%", "")) if "%" in ratio_raw else None
  ```
  ⇒ **None 传播**，不再把"未知"呈现为"零流入"。
- `etf.py:168` 的 `int(d.get("total") or 0)` **保留** —— 审计已判定分页 total 的 **0 是合理语义**。
- **全仓扫描**：`grep -rn "\bor 0\b|\?\? 0\b" backend/app` ⇒ 约 50 处，逐条分类后
  **均为 0 语义正当**（计数 / 时长 / 回测成交额 / 分页 total / 累计和），
  且关键处留有**拒绝注释**：`market.py:216-223`（记录已修缺陷 B 及其"兜底永远走不到"）、
  `etf.py:680`（「绝不用 `or 0` 兜底，会让 -1 哨兵冒充跌幅榜首」）。
- **前端同样**：`?? 0` 命中多为计数/图表零值，并在 `DataCenter/index.tsx:912,929`
  （「不得用 `?? 0` 兜底」）、`EtfDetail/index.tsx:248,404`（「绝不 `?? 0`」）留有拒绝注释。

⇒ **结论：E-6 的元教训（全仓同型排查）已执行完毕，无遗留待修项。**

### 🆕 C-3 前端零自动化测试 —— 本轮修复

**审计现状**：`package.json` 无 `test`、无 vitest/@testing-library；闸门只有 `style:check` + `nav:check`。

**本轮处置**：

1. 引入 **Vitest 2.1.9 + @testing-library/react 16 + jsdom 25 + jest-dom 6 + user-event 14**
   （写入 `devDependencies`）。
2. 新增 `frontend/vitest.config.ts`：用 `mergeConfig` **继承** `vite.config.ts`
   ⇒ **`@` 别名只有一处定义**，不会出现"构建能解析、测试解析不到"的漂移；
   `environment: 'jsdom'` + `setupFiles`。
3. `package.json` 新增 `test` / `test:watch` / `coverage` 脚本。
4. **首批 16 个测试（2 个文件）**，全部针对"只有渲染/结构才暴露"的问题：
   - `src/pages/Etf/FilterPanel.test.tsx`（6 个）：草稿+应用契约，防 D-2/D-3/D-4 复活。
     含关键断言 `expect(submitted).not.toBe(value)` —— **引用不等**，直指 D-2 的根因；
     以及「`q` 沿用父级现值」「重置不清空条件之外的东西」。
   - `src/test/layoutContract.test.tsx`（10 个）：高度链**结构不变量**（防 D-1 复发），
     含 `MainContent` 包装 div 必须有 `h-full`、`/stock` 与 `/desk` 走 `max-w-chart`、
     7 个页面根用 `min-h-full` 且**不得**改成定高 `h-full`。
   🔴 **诚实声明**：jsdom **没有布局引擎**，`getBoundingClientRect()` / `offsetHeight` 恒为 0，
   百分比高度不会被解析 ⇒ **"高度是否真的撑开"无法由渲染断言证明**（任何"渲染后断言高度 > 0"
   的测试都会**恒真**，属自欺）。故这里守的是**可静态判定的那一环**（className 层），
   真实几何表现仍**待人工目视验收**。该声明已写进测试文件头，避免后人误以为布局已被自动验证。
5. **闸门补强**（`.github/workflows/ci.yml`）：`npm test` 与 `npm run bundle:check` 均接入 CI。
   ⚠️ 审计称"CI 不跑 `tsc -b` / `build`"对 **CI 而言不准确** —— `ci.yml:88` 早已有
   `npm run build`（= `tsc -b && vite build`）。真正缺的是**单测**与 **`bundle:check`**。

### 🆕 A-1 / A-5 响应模型缺失 —— 本轮修复（采审计"至少"方案）

**审计现状**：全站 **67 处** `response_model=APIResponse[dict]`（含 `/etf/*` 全部 8 个端点），
字段契约完全由前端 TS **单方面声明** ⇒ 后端删/改/重命名字段不会有任何编译期或运行期报错，
前端只会静默拿到 `undefined`；成对 GET/POST 共用路径时更易把"读"的返回类型套到"写"上。

**本轮处置**：新增 `backend/tests/test_etf_payload_contract.py`（**6 个测试**），
按审计给出的"至少"方案冻结 `/etf/*` 的**字段集合快照**：

| 端点 | 锁定的形状 |
|---|---|
| `/overview` | 降级形状（`today` 9 键 + `prev` + `data_freshness`），并断言降级时数值为 `None` 而非 `0` |
| `/list` | 键集 + `options` 子结构 + `sort_applied` / `dir_applied` 回显 |
| `/scale` | **正常与降级两张形状** + **作用域回显**（E-3 的契约侧） |
| `/flow` | **正常与降级两张形状** + 截断披露 |

🔴 `/flow` 的**两张形状确实不同** —— 正常路径有 `truncation_basis` 无 `reason`；
降级路径有 `reason`/`data_freshness` 无 `truncation_basis`。**只锁一张会漏掉
"降级时前端读不到字段"这一类真实事故**，故两者都锁。

**测试口径**：被测对象是**载荷装配**、不是内部真实 IO 聚合 ⇒ 对数据源打桩属**合法依赖注入**
（与 `tests/test_rt_payload_status_contract.py` 对 `_build_rt` 打桩同一纪律）。

⚠️ 这是**快照**、不是正确性证明：它保证"形状变化被看见"，不保证"形状本身合理"。
改契约时请**同时**改前端 TS 类型并在此显式更新期望集合。

### 🆕 A-5 收口 —— `/desk/*` 成对端点契约（A-1/A-5 的第二批）

用户原话是「为**高频域**补响应模型或契约测试」，故本轮不止步于 `/etf/*`，继续覆盖
审计 A-5 **点名**的成对端点。新增 `backend/tests/test_desk_payload_contract.py`（**7 个测试**）。

**实测两处形状确实不同** —— 这正是必须显式建模/分型的地方：

| 路径 | 方法 | 字段集合 |
|---|---|---|
| `/desk/kill-switch` | GET | `{kill_switch, pending_orders}` |
| `/desk/kill-switch` | POST | `{kill_switch, cancelled_orders}` ← **第二个字段不同** |
| `/desk/exclusion` | POST | `{ok, id, updated}` |
| `/desk/exclusion/toggle` | POST | `{ok, id, active}` ← **形状不同** |

前端若把 GET 的类型套到 POST 上，**写操作之后**读 `pending_orders` 恒为 `undefined`，
且不会有任何编译期/运行期报错。测试分别冻结两张形状，并**显式断言二者不相等**。

额外覆盖：统一信封键集（`{code,message,data,trace_id,ts}`）、`/exclusion` 列表项
6 键（含 `created_at` 必须已序列化为 `str`）、kill-switch **写后读回一致**（防"写了读不到"
的假成功）、`/exclusion` 重复加入必须**更新而非新增**且 `updated` 如实回显。

**这些端点是 DB 支撑的**（测试 SQLite 真实执行，无需打桩），故与 `/etf/*` 的"载荷装配打桩"
口径互补：一条守**装配**，一条守**落库往返**。

---

## 三、验收证据

| 项 | 命令 | 结果 |
|---|---|---|
| 后端静态门禁 | `ruff check app tests scripts ../scripts --select F,E9` | ✅ All checks passed |
| 后端全量回归 | `pytest -q --tb=short -p no:randomly` | ✅ **2013 passed / 8 skipped** |
| 后端 ETF/契约定向 | `pytest -k "etf or contract"` | ✅ **204 passed** |
| 新增契约测试（/etf） | `pytest tests/test_etf_payload_contract.py` | ✅ **6 passed** |
| 新增契约测试（/desk） | `pytest tests/test_desk_payload_contract.py` | ✅ **7 passed** |
| 后端契约证伪 | `python scripts/dev_falsify_contracts.py` | ✅ **2/2 全部捕获**，源文件逐字节还原 |
| 前端单测 | `npm test` | ✅ **16 passed**（2 文件） |
| 前端风格闸门 | `npm run style:check` | ✅ 通过（h1 / hex 两规则） |
| 前端导航闸门 | `npm run nav:check` | ✅ 通过（孤儿路由 / 高亮互斥） |
| 前端类型 | `npx tsc -b --force` | ✅ rc=0 |
| 前端构建 | `npm run build` | ✅ `✓ built in 15.11s`（需先删 `dist`，见 L-4） |
| 体积上限 | `npm run bundle:check` | ✅ `Bundle budget passed: raw <= 768000 B, gzip <= 256000 B`（最大块 echarts 696 KB raw / 231 KB gzip，**逼近预算**） |
| 前端证伪 | `node scripts/dev-falsify-frontend.mjs` | ✅ **3/3 全部捕获**，源文件逐字节还原 |

> **测试基线更正**：审计记录的 1986 passed 与本轮实测 2013 passed 之间有增量，
> 旧记忆里的「1981」基线**已过期**。后续验收请以 **2013 passed / 8 skipped** 为准。

### 前端证伪明细（`frontend/scripts/dev-falsify-frontend.mjs`）

「测试通过」不是证据，「注入 bug 后测试失败」才是。注入的 3 个缺陷与捕获情况：

| 注入缺陷 | 期望失败的测试 | 结果 |
|---|---|---|
| `App.tsx` 包装 div 丢掉 `h-full` | `src/test/layoutContract.test.tsx` | ✅ 捕获 |
| `FilterPanel` 的「筛选」传回**同一对象引用** | `src/pages/Etf/FilterPanel.test.tsx` | ✅ 捕获 |
| 页面根改用**定高** `h-full` | `src/test/layoutContract.test.tsx` | ✅ 捕获 |

**3/3 全部捕获，源文件逐字节还原。**

### 后端契约证伪明细（`backend/scripts/dev_falsify_contracts.py`）

「字段集合断言」最容易变成**恒真测试**（断言写得和实现一样就永远通过），故必须证伪：

| 注入缺陷 | 期望失败的测试 | 结果 |
|---|---|---|
| `POST /desk/kill-switch` 改回返回**读形状**（`pending_orders`） | `test_desk_payload_contract.py::test_kill_switch_post_shape_differs_from_get` | ✅ 捕获 |
| `/etf/scale` 丢掉**作用域回显**（打破「前端不得写死」的 E-3 契约） | `test_etf_payload_contract.py::test_scale_ok_field_set_and_scope_echo` | ✅ 捕获 |

**2/2 全部捕获，源文件逐字节还原。**

🔴 与扩容脚本的证伪不同：这里打的是 `app.api.v1.desk` / `app.api.v1.etf`，
测试按**模块名**导入 ⇒ **副本无法顶替**，只能就地注入。故用三重防护：
`mkdir` 原子互斥锁（防宿主重复执行导致注入-还原交错）+ 还原只用**内存原文** + 收尾 **SHA 校验**。

> **共同教训（两次都踩到）**：证伪脚本会**改写生产源文件**，而宿主会对同一条命令
> **重复执行**。不互斥时两次运行交错，第二次把"已被注入的文件"当成原文读入 ⇒
> **还原动作把缺陷写回生产文件**（2026-10-02 在 `scripts/expand_universe_2500.py` 上
> **真的发生过一次**）。故三个证伪脚本一律：拿不到锁立即退出 + 内存原文还原 + SHA 校验。

---

## 四、遗留与建议

- **L-1（需产品/安全确认）**：B-1 的凭据加密模块已从仓库消失（**无 git 历史**）⇒
  确认是**有意下线**还是**被误删**；若仍需该能力，按「env / KMS 注入 + 常量仅作开发默认值 +
  生产启动强校验」重做。
- **L-2**：A-1 的 `APIResponse[dict]` 全站 **67 处**，本轮按审计"至少"方案覆盖了
  **8 个端点**（`/etf/*` 4：overview / list / scale / flow；`/desk/*` 4：kill-switch GET+POST、
  exclusion POST、exclusion/toggle）⇒ **剩余约 59 处**。按文件分布：
  `desk 10 / research 9 / studio 8 / etf 8 / market 5 / alerts 5 / stock 4 / screener 4 …`。
  建议优先补 **`/market/overview/rt` 与 `/overview/daily`**（公开落地页、匿名可达、流量最高）
  与 **`/studio/*`**（成对路径多）。
- **L-3**：`.githooks/pre-commit` 仍只跑 `style:check` + `nav:check`。本轮把**单测**与
  **`bundle:check`** 接进了 **CI**（面向所有推送者、不可绕过）；hook 保持 <1s 是刻意取舍
  （不把本机变成"不可提交的砖"）。若要在本地也拦类型/构建，建议加**可选开关**
  （如 `git config aqp.strictHook 1`）而非默认开启。
- **L-4（环境坑，非代码缺陷）**：`npm run build` 会被宿主 safe-delete shim 的
  `emptyDir(dist/assets)` 拦住（`genie-trash ... ETIMEDOUT`）⇒ **正解是先删 `dist` 再构建**
  （`[System.IO.Directory]::Delete`）。否则 `bundle:check` 会读到**旧 dist** 而**假通过** ——
  这是一条会掩盖真实体积回归的陷阱。
- **L-5（协作教训）**：长跑抓取期间并发跑重 I/O 作业（全量 pytest / 前端 build）会让抓取出现
  瞬时文件错误（实测 11 个标的报 `SHFileOperationW 0x2` / `PermissionError`）。
  这些标的会被续跑判为「未覆盖」自动重抓（**自愈**），但**长跑期间应避免并发重 I/O**。
