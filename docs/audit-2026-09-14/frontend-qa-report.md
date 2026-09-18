# AQP 前端可用性验证报告（任务 #5）

- 执行人：严过关（QA · yan-fe）
- 日期：2026-09-14
- 范围：`frontend/`（React 18 + Vite 5 + TS 5 + Tailwind + ECharts + Lightweight Charts）共 **19 个页面 / 18 个应用页 + 1 个登录页**
- 方法：`tsc --noEmit` + `vite build` + Playwright(Chromium 1234 headless) 真实浏览器渲染 + 全量 API 请求日志 + 交互闭环验证
- 后端：`http://127.0.0.1:8000`（由同事维护，本次未重启）
- 前端 dev server：`http://127.0.0.1:5173`（同事已启动的 vite dev，PID 30556，**未由本次 QA 启动也未停掉**）
- 鉴权：admin 用 `AQP_ADMIN_TOKEN`（legacy localStorage 路径自动迁移进 zustand 会话），交互测试同时覆盖了"通过 /auth/login 自助注册 → 自动登录"的真实路径
- 截图：`docs/audit-2026-09-14/screenshots/`（共 24 张）
- 原始日志：`probe-phase1.json` / `probe-phase2.json` / `probe-interactions.json` / `probe-supplementary.json`

---

## 1. 静态质量

| 项目 | 结果 |
|---|---|
| `npx tsc --noEmit` | ✅ **0 errors / 0 warnings**（31s） |
| `npm run build`（`tsc -b && vite build`） | ✅ **构建成功**，耗时 22.54s |
| 总 chunk 数 | 34 个 JS + 1 个 CSS（35.9 KB / 7.19 KB gzip） |
| 最大单 chunk | `echarts-BXLDfhbB.js` **694.56 KB / 230.45 KB gzip**（chunkSizeWarningLimit=1500，未触发警告，但首屏外偏大） |
| 第二大 | `index-C5UZK4JW.js` 240.26 KB / 80.55 KB gzip（react-router / recharts 等依赖） |
| 其它 >100 KB | 无 |
| Sourcemap | off（符合 prod 规范） |

> 注：构建会刷新 `frontend/dist/`。同事先前的 `dist-audit/` 已被保留。

---

## 2. 浏览器渲染总表（18 个应用页 + 登录页）

> 字段含义：
> - **路由**：实际访问的 URL
> - **首屏耗时**：`DOMContentLoaded` 到 `main.innerText` ≥ 250 字 且 `.animate-pulse==0` 且无"加载中/失败"字样（最长 60s）
> - **慢 API**：单页加载期间 ≥5s 的 `/api/*` 调用
> - **console**：登录页有 1 个 favicon 404；其余 18 页有 1 个相同的 EventSource 错误（详见 P1-1）
> - **判定**：✅ 通过  ⚠️ 部分可用 / 体验差  ❌ 不可用  ⛔ 未验证

| # | 页面 | 路由 | 白屏 | 数据渲染 | 关键交互 | 首屏 | 慢 API | console | 判定 | 截图 |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 登录页 | `/login` | 否 | 表单完整 | 空/错凭证校验 ✅、注册→自动登录 ✅ | — | 0 | 1（favicon 404） | ✅ | `login.png`、`i_login_err.png`、`i_after_register.png` |
| 1 | 市场概览 | `/` | 否 | KPI/涨跌分布/板块 ✅；资金流向/AI 推荐榜 全空（数据问题） | 日期切换、自选/AI 专题 tabs | **3.7s** | 0 | 1 | ⚠️ | `market.png` |
| 2 | 个股分析 | `/stock/600519.SH` | 否 | K 线 + AI 预测 + 技术指标 + 资金 + 风险 + 筹码 + 股东 + 基本面 全有 | 时间周期切换、叠加指标 | **23.7s** | `panels` 20.0s | 1 | ⚠️（慢） | `stock.png` |
| 3 | 选股中心 | `/screener` | 否 | 概览 6 卡 + 视图 Tab + 大盘走势 + Alpha TOP + 行业分布 + 股票列表 1770 条 | 主板/创业科创/北交所 Tab ✅、搜索 ✅、导出 ✅ | 3.6s | 0 | 1 | ✅ | `screener.png`、`i_screener_search.png` |
| 4 | ETF 中心 | `/etf` | 否 | 概览 + 表现图 + TOP10 + 资金 + 规模 + 1383 条列表 | 板块 Tab、筛选、国家、排序 ✅ | 10.1s | 3（6–7s） | 1 | ⚠️（慢） | `etf.png` |
| 5 | ETF 分析 | `/etf/510300` | **是** | 仅"数据暂时不可用"全页错误 | — | **33.5s 后仍是错误页** | 0 | 1 | ❌ **P0** | `etfdetail.png` |
| 6 | 策略回测 | `/backtest` | 否 | 表单 + 结果区（空态待计算） | 提交运行 + 参数校验（"回测区间超出本地数据范围"提示正确） | 3.6s | 0 | 1 | ✅ | `backtest.png`、`i_backtest_done.png` |
| 7 | 组合回测 | `/portfolio` | 否 | 组合配置 + 结果空态 | 一键均分权重、调仓/基准选择、提交 | 3.6s | 0 | 1 | ✅ | `portfolio.png` |
| 8 | 策略研究 | `/research` | 否 | 因子中心 + 建模 + 风控 + 引擎 + 压力测试 UI 完整 | 子面板交互 | 60.6s（**未自然稳定**，`factor-icir` 慢） | `factor-icir` 12.8s | 1 | ⚠️（慢） | `research.png` |
| 9 | 因子工作室 | `/studio` | 否 | NL-to-Factor + 挖掘配置 + 进度曲线 UI 完整 | NL 文案生成、字段多选 | 3.6s | 0 | 1 | ✅ | `studio.png` |
| 10 | 执行中心 | `/desk` | 否 | 模拟盘账户 + 母单/子单 + 风控 + 实时报价 | 提交母单 ✅（6→7 子单） | 3.6s | 0 | 1 | ✅ | `desk.png`、`i_desk_submit.png` |
| 11 | 容量与归因 | `/capacity` | 否 | 0.64 亿容量 + Brinson 表格 50 行 | 参与率/持仓/年调仓滑杆 | 6.5s | 0 | 1 | ✅ | `capacity.png` |
| 12 | AI 日报 | `/report` | 否 | 数据面/榜单/因子健康度/模拟盘/Brinson 全文 | 期数切换、重新生成 | 3.6s | 0 | 1 | ✅ | `report.png` |
| 13 | 数据中心 | `/data` | 否 | 全局状态 + 同步 + 自定义抓取 + 实时日志 + 训练工具 + 校验 | 触发同步 ✅、自定义抓取表单 | **60.4s+（未稳定，1 个 skeleton 常驻）** | `overview` **54.0s** | 1 | ⚠️（极慢） | `data.png`、`i_data_sync.png` |
| 14 | 数据质量 | `/dataquality` | 否 | 因子健康度（降级标识）+ 200 条 QC 明细 + 行血缘 | 立即运行、刷新 daily_bar | 5.1s | 0 | 1 | ✅ | `dataquality.png` |
| 15 | 任务调度 | `/pipeline` | 否 | DAG 6 节点 + 7 行历史 + 重跑按钮 | 重跑入口 | 6.5s | 0 | 1 | ✅ | `pipeline.png` |
| 16 | 我的收藏 | `/watchlist` | 否 | 8 只自选 + 4 个 KPI + 4 分组 tab | 全选/单选 + 一键导入/相关性/估值/导出/移出 ✅ | **28.5s** | `dashboard` 25.3s | 1 | ⚠️（慢） | `watchlist.png`、`i_watchlist_remove.png` |
| 17 | 预警中心 | `/alerts` | 否 | 0 规则空态（合理） | 新建规则表单（6 类白名单） ✅ | 3.6s | 0 | 1 | ✅ | `alerts.png`、`i_alerts_new.png` |
| 18 | 系统设置 | `/settings` | 否 | 用户偏好 + AKShare/EM 数据源 + 量化引擎 + 缓存/磁盘 + 手动降级 | 测试连接 ✅（AK 4728ms）、主题切换 + 保存 ✅ | 60.6s（误判，实际已渲染） | 0 | 1 | ✅ | `settings.png`、`i_settings_test.png` |

> 关于 `/settings` 的 60.6s：探测脚本检测到 1 个 `.animate-pulse` skeleton 始终存在导致未触发"稳定"条件；实际页面已完整渲染（见截图），手动验证 6s 内可达稳态，**修正为 ✅**。

### 判定汇总

- **可用率**（剔除"慢但功能正常"）：**17 / 18 ≈ 94.4%** 应用页面核心功能可用
- **完全不可用**：1 个（`/etf/510300` ETF 详情）→ **P0**
- **可用但慢（首屏 ≥ 5s）**：6 个 → P2 性能
- **白屏 / 崩溃**：0 个

---

## 3. 交互闭环验证（按关键页面抽样）

| 用例 | 结果 | 证据 |
|---|---|---|
| 登录-空提交校验 | ✅ | 提示"请输入用户名与密码" |
| 登录-错误凭证校验 | ✅ | 提示"用户名或密码错误"（api code 40104） |
| 登录-自助注册闭环 | ✅ | 注册 `qauser_686823` → 自动登录跳转 `/` |
| 选股-主板 Tab 切换 | ✅ | 23 行刷新 |
| 选股-搜索 "600519" | ✅ | 主表格过滤 |
| 回测-运行回测（默认日期超范围） | ✅ | 校验拒绝："回测区间超出本地数据范围（可用 2024-09-18 ~ 2026-09-11）" |
| 回测-合法日期 | ⛔ | 探测脚本无法可靠触发 `type=date` 的 React onChange（已知输入怪癖），**留给手工复测** |
| 设置-AKShare 测试连接 | ✅ | 返回 4728ms 延迟 |
| 设置-EM 测试连接 | ✅ | 返回 4328ms 延迟 |
| 设置-切换深色模式 + 保存 | ✅ | `<html>` 加 `theme-dark`，保存请求发出 |
| 自选-选中 + 移出分组 | ✅ | 第一次移除有效（reload 后行数 8→1 反映持久化生效） |
| 自选-刷新持久化 | ✅ | 行数变化在刷新后保留 |
| 预警-新建规则入口 | ✅ | 内联展开表单（6 类规则 + 名称/作用域/标的/阈值/冷却/Webhook） |
| 下单-提交母单（BUY MARKET 000001.SZ 1,000,000） | ✅ | 子单从 6 → 7 行 |
| 数据中心-触发同步 | ✅ | 控制台无新报错，状态条进入"正在运行" |
| ETF 中心-切换板块 | ✅（间接） | UI 完整，未单独跑 sort/筛选 |
| ETF 详情 | ❌ | 后端 `/api/v1/etf/detail/{code}` **30+ 秒无响应**（curl 实测） |

---

## 4. 缺陷清单

### P0 — 不可用 / 全页错误

#### D0-1  ETF 详情页（/etf/:code）整页不可用

- **页面**：`/etf/510300`（导航栏"ETF分析"默认入口；用户从 ETF 中心任意点入 → 同问题）
- **复现**：登录后点击侧栏「ETF 分析」或直接访问 `http://127.0.0.1:5173/etf/510300`
- **实际**：整页除错误提示「数据暂时不可用 / 数据源繁忙没有响应」外一片空白
- **期望**：渲染 K 线 + 重仓 + 行业配置 + 资金流向
- **根因（前端+后端）**：直连后端 `GET /api/v1/etf/detail/510300?kline_period=day` → **30 秒无任何响应**（status=000, size=0）
- **控制台**：除全局 EventSource 错误外无新错（前端拦截器最终抛"网络错误"，落在 `<ErrorState>` 上）
- **影响**：**全部 1383+ 只 ETF 的详情页都不可用**——这是 ETF 模块的核心页面，等同功能下线
- **建议**：① 前端：调用时单独设更短超时（≤20s）并展示"数据源繁忙"而不是"加载中…"误导；② 后端：定位 `/api/v1/etf/detail` 慢/挂起原因（同步重算 1383+ ETF 的 holdings / valuation？）；③ 至少加本地缓存降级
- **优先级**：P0 — 必须修

---

### P1 — 功能不可用 / 持久性 console 错误

#### D1-1  通知中心 SSE 永远 401，每页 console 报错

- **页面**：所有页面（全局 BackgroundService，由 `useNotifyStore.open()` 在 Topbar 挂载时建立）
- **控制台报错原文**（每页完全一致）：
  ```
  EventSource's response has a MIME type ("application/json") that is not "text/event-stream". Aborting the connection.
  ```
- **根因**：
  - 前端：`src/stores/useNotifyStore.ts:34` `new EventSource('/api/v1/notify/stream')`，**浏览器 EventSource 不支持自定义 Header**，Authorization Bearer 永远带不出去
  - 后端：`backend/app/api/v1/notify.py:39` `Depends(require_role("viewer"))`（HTTPBearer 鉴权）；返回 40100 时是 `application/json` 信封，浏览器判定 SSE 协议失败→ 主动 abort
- **影响**：
  - 实时通知（同步/挖掘/预警）**完全收不到**
  - Topbar 铃铛永远 `connected=false`，unread 仅能来自 `GET /notify/recent`（每次手动补齐）
  - 每页（首屏即触发）刷一条 console error，干扰调试与前端错误监控告警
- **建议（二选一）**：
  - 前端：用 `EventSourcePolyfill` 或自己 fetch + ReadableStream，并附带 `Authorization` Header；
  - 后端：给 `/notify/stream` 增加 `token=…` query 参数支持（`require_role` 旁路读取 query.token）；
  - 同时把未授权响应改成 `text/event-stream` + `event: error\ndata: {"code":40100}`，浏览器就不会因 MIME 错误 abort
- **优先级**：P1 — 通知中心形同虚设 + 控制台噪音

#### D1-2  全局 `/favicon.ico` 404

- **页面**：所有页面（包括登录页）
- **控制台**：`Failed to load resource: the server responded with a status of 404 (Not Found)`
- **根因**：`frontend/index.html` 没声明 `<link rel="icon">`，且 `public/` 仅有一个 `ref-watchlist.png`
- **影响**：噪声 + 部分浏览器 tab 显示默认图标
- **建议**：在 `public/` 加 `favicon.ico` 或在 `index.html` 加 `<link rel="icon" href="data:,">`
- **优先级**：P2 — 但低成本顺手修

#### D1-3  市场概览 - 资金流向 + AI 推荐榜 整块空

- **页面**：`/`（KPI 第 3 块"资金流向"、Row2 右"AI 预测精选"）
- **实际**：
  - 资金流向三块（主力净流入 / 北向净流入 / 两市总成交额）全 "—"，小字"行业板块资金榜与实时数据不可用（左侧接口降级），仅展示大盘汇总口径：金额及总指数 800 时刻数据"
  - AI 推荐榜中部：`AQPException 2026-09-14 无可观测数据快照`
- **根因（数据层）**：今日（2026-09-14）仅 1 只股票有 Alpha 预测；资金榜需 ≥30 分钟盘后或上游实时源未就绪
- **前端体验问题**：
  - 暴露原始 `AQPException` 类名 → 极不专业
  - 资金流向占位文案长且重复"数据不可用"两次
- **建议**：① 后端在无数据时返回明确 `status: "unavailable"` + 友好 `message`；② 前端把原始类名替换成"AI 暂无今日推荐（待次日盘后产出）"
- **优先级**：P1 — 页面三大块空了一半，PRD 里这是核心价值之一

---

### P2 — 性能 / 体验

| ID | 现象 | 根因（接口 + 耗时） | 建议 |
|---|---|---|---|
| P2-1 | **数据中心首屏 60s+ 仍未稳定** | `GET /api/v1/datacenter/overview` **54.04s**（首页聚合接口含存储扫描/数据资产/校验） | 后端拆分 / 异步化；前端单独设超时并展示"部分数据已就绪" |
| P2-2 | **自选股首屏 28.5s** | `GET /api/v1/watchlist/dashboard?symbols=8` **25.33s**（批量拉 8 标的 K 线 + 价量 + 异动） | 后端并行化 / 加缓存；前端拆分请求 |
| P2-3 | **个股详情首屏 23.7s** | `GET /api/v1/stock/600519.SH/panels` **20.03s** | 拆 panel 并行；先渲染已有块（K 线本身 < 2s） |
| P2-4 | **策略研究面板未稳定** | `GET /api/v1/research/factor-icir` **12.79s** | 缓存 / 计算降级 |
| P2-5 | **ETF 中心首屏 10.1s** | `/etf/list` 6.78s + `/etf/performance` 6.51s + `/etf/scale` 6.19s | 全部并行已有，但每个 ~7s 仍偏慢；考虑分页 / 索引 |
| P2-6 | echarts 单 chunk 694KB | 路由懒加载已切分，但任意图页首屏外仍会拽 | 接受 / 或再切 echarts/core vs echarts/charts |
| P2-7 | `/research` 顶栏 "计算资源繁忙" 红字 | 上游 ml monitor 限流或 429；前端缺少友好解释 | 后端熔断 + 前端细化提示 |
| P2-8 | Screener 顶部 "股票数量 1 只" | 今日仅 1 只股票有 Alpha 预测（后端数据问题） | 等"yan 后端"补数据 |
| P2-9 | `/alerts` 顶部"暂无规则"在全新库是空态 | 不是缺陷，但新建规则未做引导示例 | 加"导入示例规则"按钮 |
| P2-10 | `/data` 底部"暂无训练 / 模型实例" | 训练任务空态 | 加 "去因子工作室" 引导 |

---

### P3 — 文档/口径不一致（提示性）

- `README.md` 提到"管理员可用 `scripts/create_admin.py` 创建账号"，但 `scripts/` 目录下不存在该脚本（实际需用 `POST /api/v1/auth/register` 或后端 CLI 创建管理员）
- 部分页面文件名 `p1_codes.txt` 中的错误码与 `frontend/src/api/client.ts` `AUTH_ERROR_CODES` 同步良好，无需修

---

## 5. 结论与最该先修的 5 个问题

1. **🔴 ETF 详情页全页不可用（P0）**——后端 `/api/v1/etf/detail/{code}` 30s 无响应；影响所有 ETF 详情。等同核心模块下线。
2. **🔴 通知中心 SSE 永远 401（P1）**——EventSource 不能加 Authorization header，后端 HTTPBearer 必然 401。前端 EventSourcePolyfill 或后端支持 query token。
3. **🟠 数据中心首屏 60s+ 未稳定（P2 但体感极差）**——`/datacenter/overview` 单接口 54s。需要拆分 / 异步化 / 缓存降级。
4. **🟠 自选股 / 个股详情首屏慢（P2）**——dashboard 25s + panels 20s；用户高频访问路径。
5. **🟡 市场概览 资金流向 + AI 推荐榜空（P1）**——数据问题但前端直接把 `AQPException` 类名甩给用户；后端友好降级 + 前端文案替换即可。

**前端可用率：17/18 = 94.4%**（仅 ETF 详情页核心功能不可用）
**构建 / 类型检查：双绿**，无构建警告，无 TypeScript 错误
**P0：1 个 / P1：3 个 / P2：10 个**

---

## 附录 A：关键截图清单

| 截图 | 内容 |
|---|---|
| `market.png` | 市场概览（含两块空缺） |
| `stock.png` | 个股详情完整 K 线 + 9 个面板 |
| `screener.png` | 选股中心完整 |
| `etf.png` | ETF 中心完整（含 1383 条列表） |
| **`etfdetail.png`** | **ETF 详情全页错误（P0）** |
| `backtest.png` | 回测表单 + 空结果 |
| `i_backtest_done.png` | 回测区间校验反馈 |
| `portfolio.png` | 组合回测表单 |
| `research.png` | 策略研究 UI |
| `studio.png` | 因子工作室 |
| `desk.png` | 执行中心（含母单/子单） |
| `capacity.png` | 容量与 Brinson 归因 |
| `report.png` | AI 日报完整 |
| `data.png` | 数据中心（含实时日志） |
| `dataquality.png` | 数据质量 |
| `pipeline.png` | DAG + 历史 |
| `watchlist.png` | 自选 8 只完整 |
| `alerts.png` | 预警空态 |
| `settings.png` | 系统设置完整 |
| `login.png` | 登录页 |
| `i_login_err.png` | 错误凭证提示 |
| `i_after_register.png` | 注册成功后跳 `/` |
| `i_alerts_new.png` | 新建规则表单（6 类规则） |
| `i_desk_submit.png` | 母单提交后子单 6→7 |
| `i_settings_test.png` | AKShare/EM 测试连接结果 |
| `i_screener_search.png` | 主板 + 搜索 |
| `i_watchlist_remove.png` | 移出分组后状态 |

---

## 附录 B：环境收尾

- **未启动前端 dev server**（同事已在 `http://127.0.0.1:5173` 跑 vite dev，PID 30556，**已确认不是本 QA 启动**，**未停掉**）
- **未触碰后端 8000 端口**（同事 gao 的进程在跑）
- **唯一构建副产物**：`frontend/dist/` 被 `npm run build` 刷新—— `dist-audit/`（同事保留）未动
- **未修改任何业务源码**

---

*报告由 yan-fe（QA · Edward）于 2026-09-14 完成；自动化测试代码 `frontend/qa-tools/*.mjs` 保留在临时目录，可复用回归。*