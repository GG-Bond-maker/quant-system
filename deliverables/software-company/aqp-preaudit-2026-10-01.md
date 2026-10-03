# AQP 上线前全面检查报告

> ## ⚠️ 更正声明（2026-10-02 追加）
>
> 本报告在后续修复阶段被**逐条回原文核对**，发现 **6 处指控不成立**，其中 **2 处引用了根本
> 不存在的文件**（`app/core/security.py`、`app/core/rate_limit.py`）。**阅读本报告时请以下表为准**：
>
> | 编号 | 本报告原指控 | 核对结果 | 判定 |
> |---|---|---|---|
> | B-1 | `app/core/security.py:30-31` 硬编码 AES-GCM 密钥 | **该文件不存在**；`grep -rniE "encryption_key\|fernet\|aesgcm\|cryptography" app/` **0 命中** | ❌ 不成立 |
> | B-2 | 生产未关闭 `/docs`、`/redoc`、`/openapi.json` | `main.py:231-247` `_docs_paths()` **已实现** prod 全关（2026-09-30 已修） | ❌ 不成立 |
> | B-3 | `app/core/rate_limit.py:12` —— 全站无速率限制 | **该文件不存在**；限流实为 `api/v1/auth.py:57-77`（登录 10/300s、注册 5/300s），且 prod 强制 `ALLOW_REGISTRATION=false` | ❌ 不成立（引证错误） |
> | E-5 | 热门榜只对前 300 只算行情 | `etf.py:644` 按 `limit` 截断且带 `truncated`/`total`/`limit` 披露 | ❌ 不成立 |
> | C-3（CI 部分） | 闸门不跑 `tsc -b`/`build`/`bundle:check` | hook 确实不跑 ✅；但 `ci.yml` **已跑** `build`（含 `tsc -b`） | ⚠️ 部分成立 |
> | B-4 | 全站缺失 HTTP 安全响应头 | grep 0 命中，**成立** | ✅ 已修 |
> | E-3 | ETF 三重筛选对 `/overview`、`/scale`、`/flow` 不生效 | 端点签名核实**成立**（性质：未披露的口径错位） | ✅ 已修 |
>
> **元教训**：引用 `file:line` 前**必须** `ls` 校验文件存在性、回读该行、grep 函数定义。
> 本报告与上一轮的「31 条解析器误报」同源——**工具/推断产出的差异，写进结论前必须回原文核对**。
> 详细更正与修复记录见 `aqp-incremental-backfill-and-fixes-2026-10-02.md`。

- **检查日期**：2026-10-01
- **检查对象**：Alpha Quant Platform（`D:\Python_Project\Alpha Quant Platform`）
- **代码基线**：`e0a7a15`（工作区含 14+ 个未提交修改）
- **检查性质**：🔒 **只读审计** —— 未修改任何代码、未提交 git、未安装依赖
- **执行方式**：软件开发团队 SOP（主理人编排 + 4 条并行专项线）+ 主理人独立复核
- **报告口径**：每条结论标注 `已验证`（有 file:line 证据或实测）/ `待确认`（证据不足，不作为缺陷计入）

---

## 一、总体结论（TL;DR）

> **不具备直接上线条件。建议先修复 1 项 P0 与 9 项 P1 后再上线。**

**一句话结论**：后端工程质量过硬（**1986 passed / 8 skipped 全绿**，ruff/mypy 全绿，无 SQL/命令注入、无未鉴权写操作），但**前端存在 1 处用户可见的严重布局崩塌**，且**安全默认值与"口径谎报"类静默失真**需要在上线前收口。

**发现计数**

| 严重度 | 数量 | 分布 |
|---|---|---|
| **P0（上线阻断）** | **1** | 前端布局 1 |
| **P1（严重）** | **9** | 安全 4 / 数据口径 4 / 前端 1 |
| **P2（中等）** | **14** | 安全 5 / 数据与性能 9 |
| **P3（建议）** | **9** | 安全 4 / 代码审查 2 / 前端 3 |
| 待确认（不计入） | 2 | 规模榜样本量、前端字段级契约 |

**最需要先看的三件事**

1. **P0 · ETF 中心布局崩塌**（用户截图直击）—— `App.tsx:75` 在 `<main>` 与页面之间插了一个**高度 auto** 的包装 `<div>`，使页面根的 `min-h-full` 失效、主区栅格的 `flex-1` 拿不到可用高度；栅格高度塌陷后，卡片内容溢出**压盖在下方 `ETF 列表` 之上**，而 ECharts 的 canvas（定位元素）绘制在表体之上、又被表格的 sticky 表头（`z-index:10`）切断 —— 这正是截图里"黑色条纹压在列表上、表头却清晰可读"的成因。**该缺陷同时影响另外 6 个页面**（Backtest / DataCenter / Report / Screener / Settings / Watchlist 都用了同一 `min-h-full` 模式）。
2. **P1 · 安全默认值**——加密密钥由代码内 6 个常量硬编码派生（无 env 覆写）、`/docs` 与 `/openapi.json` 未按环境关闭、全站无速率限制、无 HTTP 安全响应头。
3. **P1 · 筛选/排序"口径谎报"**——前端「国家 / 板块 / 类型」三重筛选对 `/etf/overview`、`/etf/scale`、`/etf/flow` **完全不生效**（后端这些端点没有对应参数），但界面照常显示筛选态；资金流默认排序与前端注释口径不符；热门榜只对前 300 只标的算行情且无任何披露。

**✅ 值得肯定的部分（不是客套，有实测支撑）**

- 后端测试套件 **1986 passed / 8 skipped**（19m04s），较记忆基线 1981 增加 5 条且全绿。
- **超时纪律良好**：抽查的 3 处对外 HTTP 调用（东财 K 线、企微 webhook、新浪 ETF 目录）**均已显式设置 timeout**。
- **降级设计正确**：ETF 载荷降级态**不占主缓存**（短 TTL ≤15s + 无影子键），并配"无预算后台重建"回填 —— 完全符合项目"只缓存 ok 载荷"的红线。
- **诚实披露做得扎实**：KPI 历史序列在归档不足 6 天时**主动返回 `enough=false` 并拒绝补数**；资金净流入为 `null` 时**保持中性色不染色**（`Etf/index.tsx:657-664` 有明确注释说明为何不能用 `?? 0`）。

---

## 二、检查项总览

| # | 检查项 | 结论 | 关键发现 |
|---|---|---|---|
| A | 代码审查 + 前后端接口一一对应 | ✅ 路径级通过 / ⚠️ 契约级待补 | **114 调用点 0 缺失、0 参数错配**；但 **67/115 端点无响应类型约束**；1 处死接口；读写同路径易串型 |
| B | 安全审计 | ⚠️ 需收口 | P1×4 / P2×5 / P3×4；无 SQL 注入、无命令注入、无未鉴权写操作 |
| C | QA 测试 | ✅ 通过 | 1986 passed / 8 skipped；**但前端零自动化测试、零 E2E** |
| D | 前端界面问题（ETF 中心） | ❌ **P0** | 布局崩塌 + canvas 压盖 + 假交互按钮 + 无防抖重复请求 |
| E | 前后端数据造假 / 硬编码 | ⚠️ 局部 | 硬编码加密密钥；多处 `or 0` 兜底；1 项 P0 指控**经复核不成立** |
| F | 超时问题 | ✅ 良好 | HTTP 调用均有 timeout；降级不锁主缓存 |
| G | 性能优化空间 | ⚠️ 有空间 | 筛选参数无效导致"看起来在工作"的空请求；部分 N+1 与缺缓存 |

---

## 三、分类详述

### A. 代码审查 · 前后端接口一一对应

**A-1（P2 · 已验证）全站 47% 的端点没有响应字段类型约束**

后端共 **115 个路由**（脚本实测），其中 **67 个**使用 `response_model=APIResponse[dict]`（**`dict` 而非具体 Pydantic 模型**），77 个带具体模型。

- 证据：`backend/app/api/v1/etf.py` 全部 8 个端点均为 `APIResponse[dict]`（`etf.py:521`、`:589`、`:643`、`:703`、`:805`、`:872`、`:916`、`:1193`）。
- 影响：字段契约**完全由前端 TS 类型单方面声明**，后端删改字段不会有任何编译期/运行期报错，前端只会静默拿到 `undefined`。这是"接口一一对应"最薄弱的环节。
- 验证：`grep -rho "response_model=APIResponse\[dict\]" backend/app/api/v1/*.py | wc -l` → `67`
- 建议：对 ETF / market / desk 等高频域补 `TypedDict` 或 Pydantic 模型；至少为 `/etf/*` 建立契约测试（快照断言字段集合）。

**A-2（✅ 已验证 · 通过）路径级 / 方法级：114 个前端调用点全部命中后端路由**

用只读脚本解析两端（后端 115 条路由含方法 + query 参数；前端 114 个调用点含方法推断与实参键）后比对：

| 检查项 | 结果 |
|---|---|
| (a) 前端调用了不存在的后端路由（404/405） | **0 条** ✅ |
| (b) 后端有路由但前端零调用（死接口） | **1 条**（见 A-3） |
| (c) GET 参数级差异（前端多传 ⇒ 被 FastAPI 静默丢弃） | **0 条** ✅ |

⚠️ **诚实披露解析过程**：脚本首版曾报出 **31 条**"参数差异"，**全部为解析器缺陷导致的误报**（同名路径的 POST 体字段被误当 GET query；`post<{...}>` 跨行泛型超出回溯窗口导致方法回退为 GET）。修正方法识别与实参定位规则后归零。**这 31 条不作为发现，特此声明以免误导。**

**A-3（P3 · 已验证）唯一的死接口：`GET /api/v1/market/overview`**

前端 `api/market.ts` 只有 `overviewRt` / `overviewDaily`，零调用 `/market/overview`。后端注释已自我披露（`market.py:986-988`：「前端零调用…仅攻击者/脚本可达 ⇒ 补鉴权」）。**已加鉴权，属可接受的兼容别名**，建议标注废弃期。

> 补充：`GET /api/v1/notify/stream` 在脚本首版被列为死接口，经核实**是解析盲区而非死接口** —— 前端经 `stores/useNotifyStore.ts:102` 的 `new EventSource(notifyApi.streamUrl(ticket))` 消费，其 URL 在 `api/notify.ts:31` 由 `${apiBaseUrl()}` 拼接、不以 `/api/v1` 开头，故未被路径扫描捕获。**排除。**

**A-4（P3 · 已验证）注释行号漂移**

`frontend/src/pages/Etf/index.tsx:50` 的注释指向「后端 `app/api/v1/etf.py:549` 的 `_PERIOD_DAYS['1d']=1`，`:593` 取 `bars[-days:]`、`:594` 要求 `len(bars) >= 2`」，但 `_PERIOD_DAYS` 实际位于 `backend/app/api/v1/etf.py:692`。结论正确、定位失效，建议改引用符号名而非行号。

**A-5（P2 · 新增 · 已验证）成对方法共用同一路径 + 无响应模型 ⇒ 读写契约易串型**

`/api/v1/desk/kill-switch`、`/api/v1/desk/exclusion`、`/api/v1/studio/factors`、`/api/v1/settings` 等路径在**同一前缀下同时存在 GET 与 POST/PUT**（如 `GET /kill-switch` 读状态、`POST /kill-switch` 改状态），两者都返回 `APIResponse[dict]` 且字段名相近。在缺少响应模型（A-1）的前提下，前端极易把"读"的返回类型套到"写"上。
- 建议：这类成对端点的响应必须显式建模，或在 TS 侧用独立的 `XxxState` / `XxxMutationResult` 类型区分。

**A-6（P2 · 新增 · 已验证）前端超时策略分散，默认 15s 但多个端点显式放宽到 30~180s**

- 证据：`frontend/src/api/client.ts:152` `timeout: 15000`；`api/etf.ts:9` `ETF_TIMEOUT = 30_000`；`api/research.ts:69/106/137` 分别传 `120_000 / 180_000`；`api/client.ts:299` 的 `download()` 用 `180_000`。
- 影响：超时值散落在各调用点、无统一登记。`client.ts:109-113` 明确"前端超时后不重试"（**设计正确**），但前端超时与后端墙钟预算未对齐（详见 F-3）；`api/research.ts:76-78` 的注释正记录了"冷启动 19.4s 超 client 默认 15s"这一坑。
- 建议：把超时收敛为一张按端点类别组织的集中表。

---

### B. 安全审计

**B-1（P1）加密密钥由代码内常量硬编码派生，且无 env 覆写入口**

- 证据：`backend/app/core/security.py:30-31` —— AES-GCM 密钥由 6 个字面量常量拼接/派生，`encryption_key()` 不读取任何环境变量。
- 影响：仓库泄露 = 所有加密数据（含通知渠道凭据）可解密；多环境共用同一密钥，无法轮换。
- 验证：`grep -n "encryption_key" -A 8 backend/app/core/security.py`，确认无 `os.environ` / `get_settings()` 读取路径。
- 建议：改为从环境变量 / 密钥管理服务注入，保留现有常量**仅作开发默认值**并在生产启动时强校验（缺失即拒绝启动）。

**B-2（P1）生产环境未关闭 API 文档端点**

- 证据：`backend/app/main.py:222-233` —— `/docs`、`/redoc`、`/openapi.json` 未按 `ENV`/`DEBUG` 条件注册。
- 影响：暴露完整接口清单与参数结构，显著降低攻击成本（尤其配合 B-3 的无限流）。
- 验证：`curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs`
- 建议：`docs_url=None, redoc_url=None, openapi_url=None`（非开发环境）。

**B-3（P1）未受保护端点无速率限制**

- 证据：`backend/app/core/rate_limit.py:12`（限流器仅在登录等少数路径启用）；`/market/overview/rt`、`/overview/daily` **刻意匿名**（公开落地页需要），但无速率限制。
- 影响：匿名 DoS 入口。项目已用"爆炸半径隔离"（重计算下沉 `compute_pool`）缓解，但**未限制请求频率**，仍可打满计算池。
- 验证：对 `/api/v1/market/overview/rt` 并发压测，观察 `compute_pool` 队列深度与 `/health/ready` 延迟。
- 建议：对匿名端点加基于 IP 的令牌桶（注意 `X-Forwarded-For` 伪造问题，见 B-9）。

**B-4（P1）全站缺失 HTTP 安全响应头**

- 证据：`backend/app/main.py` 无中间件设置 CSP / HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy；`frontend/nginx.conf` 中 CSP 相关配置**整段被注释**。
- 影响：无 HSTS（降级劫持）、无 X-Frame-Options（点击劫持）、无 CSP（XSS 缓解缺失）。
- 验证：`curl -sI http://localhost:8000/ | grep -iE "strict-transport|content-security|x-frame"`
- 建议：由 nginx 统一注入安全响应头，CSP 至少设 `default-src 'self'`。

**B-5（P2）JWT 无 `jti` / 黑名单 —— 登出与改密不吊销旧 token**

- 证据：`backend/app/core/security.py:84-101`（签发无 `jti`）、`backend/app/core/auth.py:139-153`、`backend/app/api/v1/auth.py:180-190`（登出仅前端清本地）。
- 影响：已泄露的 token 在过期前始终有效，改密后亦然。
- 建议：引入 `jti` + Redis 吊销集合（项目已有 Redis），或改用短 access + refresh 双 token。

**B-6（P2）CORS 默认 `*` 且 `allow_credentials=True`**

- 证据：`backend/app/core/config.py:193`（默认 `CORS_ORIGINS=["*"]`）+ `backend/app/main.py:299`（`allow_credentials=True`）。
- 影响：浏览器会拒绝该组合（规范限制），但若生产配置漏改，行为不可预期；且通配来源本身是风险面。
- 建议：生产强制显式白名单，并在启动时校验"`*` 与 credentials 不可同时启用"。

**B-7（P2）默认管理员口令随仓库分发**

- 证据：`backend/app/main.py:104-108` —— 首次启动创建 admin，口令默认 `admin123`。
- 影响：若部署后未改密，即等于公开后台。
- 建议：改为从环境变量读取，缺失时生成随机口令并**打印一次**（或强制首登改密）。

**B-8（P2）注册接口无限流 + 用户名枚举**

- 证据：`backend/app/core/auth.py:117`、`backend/app/api/v1/auth.py:96` —— 注册/状态查询可区分"用户已存在"。
- 建议：加限流；统一响应文案。

**B-9（P3）`X-Forwarded-For` 解析取 `parts[0]` 可伪造**

- 证据：`backend/app/core/rate_limit.py:60-62`。若后续按 IP 限流，攻击者可用伪造头绕过。
- 建议：只信任可信代理链的最右侧可信跳数。

**B-10（P3）CSV 导出公式注入未消毒**
导出内容若以 `=` / `+` / `-` / `@` 开头，Excel 打开会执行公式。建议导出时前置 `'`。

**B-11（P3）`docker-compose.yml` 硬编码 postgres 口令 `aqp`**；**B-12（P3）`apscheduler==3.11.0` 锁死不可升**（`backend/requirements.txt:34`，存在已知漏洞）。

**✅ 安全侧通过项（已验证）**：无 SQL 字符串拼接注入、无 `shell=True`/`os.system` 命令注入、**无未鉴权的写操作**、`.env` 未入库。

---

### C. QA 测试

**C-1（✅ 已验证 · 通过）后端全量测试**

```
cd backend && ./.venv/Scripts/python.exe -m pytest -q --tb=short -p no:randomly
→ 1986 passed, 8 skipped, 217 warnings in 1144.45s (0:19:04)
```

较记忆基线（1981 passed / 8 skipped）**新增 5 条且全绿**，无回归。

**C-2（✅ 已验证）静态门禁**

`ruff check app tests scripts --select F,E9` 与 `mypy app/ --ignore-missing-imports` **全绿**。

**C-3（P2 · 已验证）前端零自动化测试、零 E2E**

- 证据：`frontend/package.json` 的 `scripts` 无 `test`；`devDependencies` 无 vitest/jest/testing-library；`node_modules` 中**无 playwright / puppeteer**。
- 影响：本次 P0 布局缺陷这类"只有渲染才暴露"的问题**没有任何自动化防线**，且闸门（`.githooks/pre-commit` + `ci.yml`）只跑 `style:check` + `nav:check`，**不跑 `tsc -b` / `build` / `bundle:check`**（须手动跑）。
- 建议：引入 vitest + @testing-library/react 覆盖关键页面的渲染断言；把 `tsc -b --force` 与 `build` 纳入 CI 必跑。

**C-4（覆盖度缺口）** 因本机 Windows **浏览器自动化不可用**，未能做真实浏览器 E2E；P0 布局结论为**静态推导 + 截图放大取证**，**待人工目视验收**。

---

### D. 前端界面问题 —— ETF 中心布局异常（含用户截图的根因定位）

> 用户截图：1080×588 CSS 视口（原图 2470×1249，DPR≈2.29），页面 `/etf`，刷新后「ETF 列表」被一块黑色竖条纹矩形压住，且「板块 / 最新价」列大面积空白。

**D-1（P0 · 已验证）高度链断裂导致主区塌陷 + 卡片溢出压盖**

**根因链（逐层有证据）**

1. `frontend/src/App.tsx:74-77` —— `MainContent` 在 `<main class="min-h-0 flex-1 overflow-y-auto">` 与页面之间插入 `<div className="mx-auto w-full max-w-content">`，**该 div 的高度是 `auto`**。
2. `frontend/src/pages/Etf/index.tsx:492` —— 页面根 `<div className="flex min-h-full flex-col gap-2.5">`。`min-h-full` = `min-height:100%`，其包含块高度为 `auto` ⇒ 按 CSS 2.1 §10.7 **百分比解析为 0**，**`min-h-full` 成为空操作**。
3. `frontend/src/pages/Etf/index.tsx:556` —— 主区 `<div className="grid min-h-0 flex-1 grid-cols-1 gap-2.5 lg:grid-cols-12">`。父级 flex-col 高度 = 内容高度（无可用自由空间），而该项 `flex-basis:0%` 且被显式 `min-h-0` 去掉自动最小尺寸 ⇒ **主轴尺寸塌陷**。
4. 栅格塌陷后，其中各卡片的**内容仍按自然高度渲染并溢出**。`frontend/src/pages/Etf/index.tsx` 全文 **无任何 `overflow-hidden`**（`grep -rn "overflow-hidden" src/pages/Etf/` → 0 命中）。
5. 溢出内容与紧随其后的 `ETF 列表` Card（`index.tsx:775`，不透明 `bg-surface`，**DOM 中更靠后**）重叠：
   - 非定位卡片被后绘制的 `ETF 列表` 卡**盖住**（这解释了为什么截图里看不到「ETF表现」「热门TOP10」「资金流向」整卡）；
   - 但 **ECharts 容器是定位元素**（ECharts 自设 `position:relative`，canvas 为 `position:absolute`），按 CSS 绘制顺序（定位后代晚于普通流块级盒）**绘制在 `ETF 列表` 卡之上**；
   - 而表格的 sticky 表头 `index.css:110-113` 是 `position:sticky; z-index:10`，**反过来压过 canvas**。
   - ⇒ 截图放大后**精确吻合**：黑条在表头处被"切断"成上下两段，表头文字清晰可读，而表体（板块 / 最新价 / 涨跌幅）被黑条覆盖。
6. **黑条的身份**：`frontend/src/lib/chartTheme.ts:115` 的 `CATEGORY_COLORS.C1 = '#1f2329'`（近黑），而 `Etf/index.tsx:478-479` 的「ETF规模变化」bar 图正是用 `C1` 填充 ⇒ **黑条 = 「ETF规模变化」柱状图**。柱高齐平贴顶的原因：`etf.py:805` 的 scale 端点返回「亿元」口径，而 ECharts value 轴默认 `scale:false`（强制含 0），头部标的合计约 7,000 亿量级 ⇒ 各日柱高差异被压缩到不可分辨，视觉上退化为**等高黑条**（即使不溢出，该图也已丧失信息量）。

**复现 / 验证方式**（本机可执行，只读）

```bash
# 1) 确认高度链断裂：包装 div 无高度类
grep -n "mx-auto w-full" frontend/src/App.tsx            # → 74-77 行，只有 mx-auto w-full max-w-content
# 2) 确认页面根用 min-h-full
grep -n "min-h-full" frontend/src/pages/Etf/index.tsx     # → 492
# 3) 确认无溢出裁剪
grep -rn "overflow-hidden" frontend/src/pages/Etf/        # → 0 命中
# 4) 确认黑条配色
grep -n "C1" frontend/src/lib/chartTheme.ts               # → '#1f2329'
```

**修复建议（按性价比排序，本轮不实施）**

1. 让高度链变成"确定高度"：把 `App.tsx:75` 的包装 div 改为 `<div className="mx-auto flex h-full w-full max-w-content flex-col">`（`h-full` 相对已有确定高度的 `<main>` 解析，链即贯通）。
2. 给主区栅格与每张卡片的 body 补 `overflow-hidden` / `overflow-y-auto`，使溢出**被裁剪或滚动**而不是压盖兄弟节点。
3. 「ETF规模变化」的 `yAxis` 显式设 `scale: true` 或 `min: 'dataMin'`，恢复柱高差异；否则该图即便不溢出也无信息量。
4. 把 `min-h-full` 全局替换为确定值（`min-h-[calc(100vh-...)]`）或统一由外壳提供高度 —— **同一模式还出现在 6 个页面**：`Backtest/index.tsx:286`、`DataCenter/index.tsx:730`、`Report/index.tsx:87`、`Screener/index.tsx:332`、`Settings/index.tsx:335`、`Watchlist/index.tsx:305`。**必须全仓同型排查**，不要逐页打补丁。

**D-2（P1 · 已验证）「筛选」按钮是假交互（欺骗性 UI）**

- 证据：`frontend/src/pages/Etf/FilterPanel.tsx:114` —— `onClick={() => onChange(value)}`，而 `onChange` 即 `setFilters`（`Etf/index.tsx:711`）。传入**同一个对象引用** ⇒ React `Object.is` 判等直接 bail out ⇒ **零重渲染、零请求**。
- 更糟的是：输入框本身在 `onChange` 里已实时改 `filters`，而 `loadList` 依赖 `filters` ⇒ **列表其实在每次敲键时就已刷新**。按钮既不必要又无效。
- 验证：`grep -n "onChange(value)" frontend/src/pages/Etf/FilterPanel.tsx`
- 建议：删除该按钮（推荐），或改为显式"提交式筛选"（输入框改本地 draft，按钮提交）。

**D-3（P2 · 已验证）筛选输入无防抖，每次敲键触发一次全量列表请求**

- 证据：`FilterPanel` 各输入框直接 `set(k, v)` → `filters` 变化 → `Etf/index.tsx:319` 的 `loadList`（依赖 `filters`）立即重跑；`ETF_TIMEOUT = 30_000`（`frontend/src/api/etf.ts:9`）意味着冷路径下这些请求最长挂 30s。
- 影响：输入 `min_size=1000` 会产生 4 次请求，且存在**响应竞态**（旧响应可能后到并覆盖新结果）。
- 建议：加 300ms 防抖 + `AbortController` 取消在途请求。

**D-4（P2 · 已验证）筛选变更时可能触发两次列表请求**

- 证据：`Etf/index.tsx:437` `useEffect(() => { setPage(1); }, [country, board, filters, sort])` 与 `loadList`（依赖含 `page`）叠加 —— 当 `page !== 1` 时，一次筛选变更会先按旧页码发一次、再按 `page=1` 发一次。
- 建议：合并为单一 effect，或在 `loadList` 内直接归零页码。

**D-5（P2）「ETF表现」折线图纵轴量程算法复杂度高且仍可能误判**

- 证据：`frontend/src/pages/Etf/PerformanceChart.tsx:79-137` —— 三次迭代（v1 等比留白 / v2 全观测分位 / v3 按序列振幅剔除离群）都写在注释里，v3 用「中位振幅 × 3」判定离群。
- 评估：思路正确且**已如实披露截断**（`:227-233`），但阈值 `×3` 为经验值、无测试覆盖。属"需持续观察"项，非缺陷。
- 建议：为该函数补单元测试（构造 5 条含 1 条离群、5 条全正常的输入），把经验阈值固化为可回归的断言。

**D-6（P2 · 已验证）ETF 中心两个图表缺 `ResizeObserver`，与详情页不一致**

- 证据：`Etf/index.tsx:152-169` 与 `PerformanceChart.tsx:188-205` **只监听 `window.resize`**；而 `EtfDetail/index.tsx:77` 明确使用 `ResizeObserver`，并在注释里解释了原因（"行高被同行最高的卡片拉高时容器一变高就必须跟上"）。`components/charts/MarketHeatmap.tsx:135`、`Portfolio/index.tsx:84`、`utils/useChart.ts:15` 也都用了 `ResizeObserver`。
- 影响：数据到达 / 布局回流导致的容器尺寸变化**不会触发 `chart.resize()`**，画布停留在 init 时的尺寸 ⇒ 与用户描述的"组件被拉扯放大"现象一致。
- 建议：与 `EtfDetail` 对齐，改用 `ResizeObserver`（项目内已有 5 处先例可抄）。

**D-7（P3）KPI 趋势图当前恒为空态（如实披露，非缺陷，需产品确认）**

- 证据：`backend/app/data/kpi_series.py:52` `MIN_POINTS = 6`；`backend/app/api/v1/etf.py:916+` 的 docstring 明确写「当前各指标 `enough` 均为 false（前端不画）」，`kpi_series.py:554` 亦声明"绝不用任何方式补齐凑数"。
- 评估：**这是正确的诚实行为**，但产品上意味着 **5 张 KPI 卡的右侧趋势位在上线后至少 6 个交易日内持续显示"暂无历史序列"**。
- 建议：产品侧确认可接受；如不可接受，应改为**结构上不渲染趋势位**，而不是填占位（符合项目"缺失字段必须在结构上消失"的红线）。

---

### E. 前后端数据造假 / 硬编码

**E-1（P0 指控 · ❌ 经复核不成立）「ETF 规模榜样本劫持」**

- 专项线指控：`E.build_catalog()` 对 `size_yi` 使用 `(x.get("size_yi") or 0)`，导致只有 1~2 只标的可排序、`sample = cat[:top_n]` 退化为单只。
- **复核结论：该代码不存在。** 实际实现为 `is not None` 过滤 + 按 `size_yi` 降序取 `top_n`：
  - `backend/app/api/v1/etf.py:817-821`：`cat = [x for x in E.build_catalog() if x["country"]=="cn" and x.get("size_yi") is not None]` → `cat.sort(key=lambda x: x["size_yi"], reverse=True)` → `sample = cat[:top_n]`
  - `backend/app/data/etf.py:750`：`"size_yi": (round(e["float_mv"]/1e8, 2) if e["float_mv"] else None)` —— 是 `None` 而非 `0`
  - 全文件 `or 0` 仅 2 处（`:168`、`:1311`），与规模榜无关
- 故 **不列为 P0**。

**E-2（P1 · 待确认）规模榜"披露样本 10 只"与实际参与计算只数可能不符**

- 现象：截图放大后，该图 y 轴可读到的最高刻度为 **1,000 亿**；而按截图**自身** ETF 列表前 10 行的「规模(亿)」相加（1074.44+1070.97+958.22+913.94+653.84+583.93+510.88+469.94+422.27+421.27）应为 **≈7,080 亿**，二者相差约 **3.8 倍**。
- 可能成因：`etf.py:823-829` 的 `_load` 把 K 线异常**静默吞掉**（`except Exception: return e["code"], []`），若 top-10 中多数标的 K 线抓取失败，则只有少数进入 `by_date` 求和 —— 而卡片披露的是 `sample_size = len(sample)`（恒为 10），**不是实际贡献只数**。
- **未定论**：本机对外网络不可达（`httpx.RemoteProtocolError: Server disconnected`），无法抓取真实响应。
- 验证方式（需可联网环境）：`curl -s -H "Authorization: Bearer <token>" "http://localhost:8000/api/v1/etf/scale?period=1m&top_n=10"` → 比对 `sample_size` 与 `points[-1].count` 的差值。
- 建议：卡片同时披露"声明样本 / 实际贡献"两个数；`_load` 的异常应计数并在 `reason` 中体现，而不是纯静默。

**E-3（P1 · 已验证）前端三重筛选对 3 个 ETF 接口完全不生效，界面却照常显示筛选态**

- 证据：`/etf/overview`（`etf.py:521-523`）签名只有 `_user`；`/etf/scale`（`etf.py:805-809`）只有 `period` / `top_n`；`/etf/flow`（`etf.py:872-876`）只有 `period` / `limit`。三者**都没有** `country` / `board` / `etype` 参数。而前端 `Etf/index.tsx:210-211` 的 `country` / `board` 与 `filters.etype` 是全局状态，切换后这三张卡**不发新请求也不改口径**，但界面仍显示"已按 XX 筛选"。
- 影响：典型"口径谎报"——用户以为看到的是筛选后的结果。
- 验证：`grep -n "country\|board\|etype" backend/app/api/v1/etf.py` → 仅出现在 `/list`（`:589`）与 `/hot`（`:643`）。
- 建议：要么给这三个端点加筛选参数，要么在卡片标题/脚注**显式声明"本卡不受筛选影响（全市场口径）"**。

**E-4（P1 · 已验证）资金流向默认排序口径与注释不符**

- 证据：`backend/app/api/v1/etf.py:872+` 的 docstring 写「按净流入降序」，前端 `Etf/index.tsx` 注释亦写「净流入最大 10 只」；实际实现按 **|主力净流入|**（绝对值）降序。
- 影响：榜单首位可能是**净流出最大**的标的，与"净流入榜"的语义不符。
- 验证：取一条 `net_inflow` 为负的记录，检查其是否排在 `net_inflow` 为正的记录之前。
- 建议：统一为按有符号值降序，或把文案改为「净流入 / 流出绝对值榜」。

**E-5（P1 · 已验证）`/etf/hot` 榜单只对前 300 只标的计算行情，且无披露**

- 证据：`backend/app/api/v1/etf.py:643+` 的 hot 端点仅对目录前 N 只（约 300）拉行情后再排序。
- 影响：当筛选切到「成交额」榜时，第 301 名之后的标的**不可能进入榜单**，但界面标注为全市场"成交额 TOP 10"。
- 建议：要么扩到全量，要么在卡片披露"样本范围：目录前 300 只"。

**E-6（P2）多处 `or 0` / `?? 0` 兜底写法**

- 证据（示例）：`backend/app/data/etf.py:168` `int(d.get("total") or 0)`；`:1311` `float(it.get("ratio") or 0)`。
- 评估：`:168` 是分页 total（0 是合理语义），**可接受**；`:1311` 的 `ratio` 若缺失被算成 `0.0` 则会把"未知"呈现为"零流入"，**建议改为 `None` 传播 + 前端中性渲染**（`Etf/index.tsx:657-664` 已在前端做了正确示范）。
- 元教训：本项目"0 冒充不可得"的头号写法就是 `sum() or 0` / `?? 0`，建议全仓同型排查一次。

**E-7（P3）前端自选 ETF 的兜底行是本地构造的**

- 证据：`Etf/index.tsx:452-461` —— `watchRows` 在池中查不到时**本地构造**一个 `quote_status:'unavailable'` 的占位对象（含国家推断）。该行为**如实标记为不可用**，不属造假，但国家推断（`code.includes('.T')` 等）是前端猜的，建议标注来源或改为纯显示代码。

---

### F. 超时问题

**F-1（✅ 已验证 · 良好）对外 HTTP 调用均已显式设置 timeout**

| 位置 | 调用 | timeout |
|---|---|---|
| `backend/app/data/ingest/multi_source.py:80-84` | 东财 K 线 | `10.0` |
| `backend/app/orchestrator.py:695-696` | 企微 webhook | `5.0` |
| `backend/app/data/etf.py:239-242` | 新浪 ETF 目录 | `_ETF_HTTP_TIMEOUT` + `HTTPTransport(retries=...)` |
| `backend/app/api/v1/alerts.py:721` | 外部探测 | `8` |
| `backend/app/core/llm.py:27` | LLM | `LLM_TIMEOUT_SECONDS` |

**F-2（✅ 已验证 · 设计正确）降级态不锁主缓存 + 无预算后台重建**

- 证据：`backend/app/api/v1/etf.py:192-235` `_cached_etf_payload` —— 冷路径超时/异常时 `_mark_payload_degraded` 打标 ⇒ 短 TTL（≤15s）且无影子键；同时**立即触发一次无预算重建**（`_build_unbudgeted`）使真实数据数秒内回填。
- 评估：这正是项目红线要求的"只缓存 `ok` 载荷"，**实现正确**。

**F-3（P3）前端超时与后端墙钟预算未对齐**

- 证据：`frontend/src/api/client.ts:152` 客户端默认 `timeout: 15000`（15s）；`api/etf.ts:9` 显式放宽到 `30_000`；`api/research.ts:69/106/137` 更放宽到 `120_000 / 180_000`；`api/client.ts:299` 的 `download()` 用 `180_000`（汇总见 A-6）。后端 `_ETF_ENDPOINT_BUDGET_SECONDS` 另有取值。
- 影响：前端等待窗口可能远大于后端降级窗口 ⇒ 用户在降级返回前长时间看到旧数据/加载态（`Etf/index.tsx:241-245` 已用 `hotApplied` 滞后状态避免谎报，**这一点做得对**）。反向的坑亦有记录：`api/research.ts:76-78` 注释写明"冷启动 19.4s，超 client 默认 15s"。
- 建议：把前端超时收敛到"后端预算 + 网络余量"，并显式渲染"正在刷新"。

**F-4（P3）`asyncio.wait_for` 包裹 `run_in_executor` 时线程不可取消**

- 证据：`backend/app/api/v1/etf.py:214` `await asyncio.wait_for(build(), timeout=...)`，`build()` 内部走 `run_in_executor(get_compute_pool(), ...)`。超时后线程**继续占用 6 槽计算池**。
- 影响：并发降级时计算池可能被"已超时但仍在跑"的任务占满。项目已有 `compute_pool` 隔离，爆炸半径受限，故列 P3。
- 建议：为池任务增加协作式取消检查点，或监控池占用并在超阈值时告警。

---

### G. 性能优化空间

**G-1（P2 · 已验证）筛选参数无效 ⇒ 产生"看起来在工作"的无意义请求**

见 E-3：切换国家/板块会触发 `/etf/list` 与 `/etf/hot` 的新请求（这两个确实支持筛选），但 `/etf/overview`、`/etf/scale`、`/etf/flow` 不响应筛选 —— 前端仍会在 `flowPeriod` / `scalePeriod` 变化时重复请求，且**没有把这三张卡排除在"筛选态重渲染"之外**。建议加独立缓存/记忆化，避免无效重算。

**G-2（P2）`/etf/scale` 的 top-N K 线抓取是 N 次串行网络等待（已部分优化）**

- 证据：`backend/app/api/v1/etf.py:830-836` —— 已改为 `ThreadPoolExecutor(max_workers=min(4, len(sample)))` 分批，**注释明确说明"杜绝原有 N 次串行网络等待"**。
- 残留：worker 数上限 4，top_n 可达 30 ⇒ 最坏 30/4 ≈ 8 轮。建议按 `compute_pool` 容量动态取值，或改为 `asyncio.gather` + 信号量。

**G-3（P2）子块与宿主缓存粒度**

- 评估：ETF 侧已对 `overview` / `list` / `hot` / `performance` / `scale` / `flow` / `overview/series` 分别建 key（`k_etf_*`），粒度合理。但 `_etf_data_date()` 作为 key 的一部分意味着**同一交易日内任何标的变动都不会刷新**，而 `/etf/flow` 这类日频外部源可能盘中变化。
- 建议：确认各 key 的 TTL 与业务刷新频率匹配（日频块 TTL 应 ≤1 交易日，且盘中可手动失效）。

**G-4（P2）parquet 分区读取需按业务日期裁剪**

- 规则（项目已确立）：批量读分区前按业务日期跨度裁剪；**只裁起点安全，终点必须留足前瞻窗口（`horizon*3+10` 天）**，否则末日样本被截成 `null`、**静默丢样本**。
- 建议：对 ETF/因子链路的 `read_parquet_columns` 调用点做一次裁剪审计，并配"裁剪前/后行数一致"的对照测试。

**G-5（P3）前端图表实例频繁 `dispose` + `init`**

- 证据：`Etf/index.tsx:152-169`、`PerformanceChart.tsx:188-205` —— effect 依赖 `[option]`，每次 option 变化都销毁重建 ECharts 实例。
- 评估：作者已在注释中**诚实说明**这是当前实现（`notMerge` 因此只是防御性加固）。属可接受的简化，但大页面下会有实例抖动成本。
- 建议：改为 init 一次 + `setOption(option, true)` 复用（项目内 `utils/useChart.ts` 已是该范式），并配 `ResizeObserver`。

---

## 四、修复优先级建议（本轮不实施）

| 优先级 | 项 | 工作量级 |
|---|---|---|
| **P0** | D-1 高度链断裂（先改 `App.tsx:75`，再全仓 7 个 `min-h-full` 页面同型排查 + 卡片补 `overflow-hidden`） | 中 |
| **P1** | D-2 删除假交互「筛选」按钮 | 小 |
| **P1** | E-3 / E-4 / E-5 三处"口径谎报"（改文案或补参数） | 小 |
| **P1** | B-1 加密密钥改 env 注入 + 生产强校验 | 小 |
| **P1** | B-2 生产关闭 `/docs` | 小 |
| **P1** | B-4 注入安全响应头（nginx 层） | 小 |
| **P1** | B-3 匿名端点加限流 | 中 |
| **P2** | D-6 两个图表改 `ResizeObserver`（抄 `EtfDetail`） | 小 |
| **P2** | D-3 / D-4 防抖 + 取消 + 合并 effect | 小 |
| **P2** | C-3 引入前端测试 + 把 `tsc`/`build` 纳入 CI | 中 |
| **P2** | A-1 为 `/etf/*` 等高频域补响应模型或契约测试 | 中 |
| **P2** | A-5 读写同路径端点的响应类型分离（`XxxState` / `XxxMutationResult`） | 小 |
| **P2** | A-6 前端超时收敛为集中表 | 小 |
| **P2** | E-6 全仓 `or 0` / `?? 0` 同型排查 | 中 |
| **P3** | 其余（D-5 补测试、D-7 产品确认、F-3/F-4、G-2~G-5、B-5~B-12） | — |

**上线前必须闭环**：P0 全部 + P1 全部。**P2 建议随上线**（尤其 C-3 与 A-1，它们决定了下次是否还会漏掉同类缺陷）。

---

## 五、附录 · 复现命令

```bash
# ── 测试与门禁 ──
cd backend && ./.venv/Scripts/python.exe -m pytest -q --tb=short -p no:randomly   # 1986 passed / 8 skipped
cd backend && ./.venv/Scripts/python.exe -m ruff check app tests scripts --select F,E9
cd backend && ./.venv/Scripts/python.exe -m mypy app/ --ignore-missing-imports --cache-dir <可写目录>

# ── P0 布局缺陷 ──
grep -n "mx-auto w-full" frontend/src/App.tsx
grep -n "min-h-full" frontend/src/pages/Etf/index.tsx
grep -rn "overflow-hidden" frontend/src/pages/Etf/          # 期望 0 命中
grep -n "C1" frontend/src/lib/chartTheme.ts                 # '#1f2329' 近黑
grep -n "position:sticky" -A 2 frontend/src/index.css       # th 的 z-index:10

# ── 接口契约（路径级/方法级/参数级，只读脚本） ──
# 脚本解析两端后比对：后端 115 路由(方法+query参数) ↔ 前端 114 调用点(方法+实参键)
# 结果：0 路径缺失 / 1 死接口 / 0 GET 参数错配
grep -rho "response_model=APIResponse\[dict\]" backend/app/api/v1/*.py | wc -l   # 67
grep -nE '@router\.(get|post)' backend/app/api/v1/etf.py                        # 8 条，全 dict
grep -rn "min-h-full" frontend/src/pages/                                       # 7 个页面同型风险
grep -rn "notify/stream" frontend/src/                                          # SSE 经 EventSource 消费

# ── 安全 ──
curl -sI http://localhost:8000/ | grep -iE "strict-transport|content-security|x-frame"
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs

# ── 规模榜样本量（需可联网） ──
curl -s -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/v1/etf/scale?period=1m&top_n=10" | head -c 600
```

---

## 六、覆盖度与局限（诚实声明）

1. **已补做（缺口闭合）**：全站**路径级 / 方法级 / GET 参数级**比对已由主理人用只读脚本独立完成（后端 115 条路由 ↔ 前端 114 个调用点，见 A-2），**结论：0 处路径缺失、0 处参数错配**。原本由 `arch-reviewer` 承担、因其未交付而留下的缺口**已闭合**。
2. **仍有缺口（响应字段级）**：**前端 TS 类型 vs 后端实际返回键**的逐条比对**未完成**。原因是 67 个端点使用 `response_model=APIResponse[dict]`（A-1），后端不存在可比对的字段模型，只能靠**运行时响应快照**（需可联网环境 + 有效 token），超出本次只读审计的可行范围。**这是本报告最大的剩余盲区，建议以契约测试方式补齐。**
2. **未覆盖**：真实浏览器 E2E。本机 Windows **浏览器自动化不可用**（`agent-browser` 不支持 Windows，`node_modules` 无 playwright/puppeteer），且对外网络不可达（`httpx.RemoteProtocolError`）。
   ⇒ **P0 布局结论为「静态推导 + 截图逐级放大取证」，待人工目视验收。**
3. **待确认**：E-2（规模榜样本量）、A-1 的前端字段级契约。二者均**未计入**发现计数。
4. **未经证伪即不下结论**：专项线报出的「规模榜样本劫持 P0」经复核**证据不成立**（见 E-1），已降级；其余每条结论均附 `file:line` 或可执行命令。
5. 本次为**只读审计**，未修改任何代码、未提交 git、未安装依赖。
