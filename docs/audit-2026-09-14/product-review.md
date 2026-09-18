# AQP 产品层功能合理性与文档一致性审查报告

- **审查人**：许清楚（产品经理）
- **审查日期**：2026-09-14
- **审查范围**：产品功能合理性、用户闭环自洽性、量化口径误导风险、文档↔实现一致性、合规与展示风险
- **审查方式**：只读静态审查（未启动服务、未修改任何源码）
- **对象版本**：工作区当前代码（后端 21 个 v1 路由模块 / 111 个端点；前端 19 个页面目录 / 18 条路由）
- **配套报告**：`backend-smoke.log`、`pytest-full.log`（工程师同事产出）——本报告只回答"**是否合理**"，不重复"是否可用"

---

## 0. 一句话结论

**AQP 的"功能完成度"其实是高的（19 个页面背后基本都有真实后端计算，无空壳页面），真正拖后腿的是三件产品层面的事：**

1. **权限分层与页面入口错位**——产品主打的"AI 个股预测"被挂在 `researcher` 角色上，导致未登录用户打开个股详情页会被强制踢到登录页；同时 5 个"页面无守卫 / 接口要 researcher"的页面构成半可用死胡同。
2. **存在金融语义错误的展示口径**——选股中心的「风险等级」实际是"模型预测收益高低"的映射（预测收益越高标为"低风险"），这是会真实误导用户的 P0 级问题。
3. **README / run.md 严重过时**——登录方式写错（按文档操作必然失败）、页面数写 4 个（实际 19）、测试写 68 条（实际 743）、"未实现清单"把已上线的 SSE 列为未实现。

---

## 1. 功能全景清单

> 完成度定义：
> **完整** = 前后端打通、数据真实、用户可独立完成闭环；
> **完整（有缺陷）** = 链路通但存在产品/口径缺陷（见第 3 节）；
> **半成品** = 主链路通但关键子能力缺失（如无持久化）；
> **未接线** = 页面在用但后端能力缺失/或后端有而前端无入口。

| # | 功能模块 | 前端页面（路由） | 后端能力（v1 路由） | 面向场景 / 目标用户 | 文档是否声明 | 完成度 |
|---|---|---|---|---|---|---|
| 1 | 市场概览 | MarketOverview（`/`） | `market/overview/{rt,daily}`、`market/quotes`、`market/index/kline` | 所有人：30 秒建立市场温度判断 | README 核心接口表 ✅ | 完整（有缺陷） |
| 2 | 个股分析 | StockDetail（`/stock/:symbol`） | `stock/search`、`stock/{sym}/{profile,kline,predict,panels}` | 所有人：看 K 线 + AI 预测 + 资金/筹码/基本面 | README/run.md 主打页 ✅ | 完整（**有 P0 缺陷**） |
| 3 | 选股中心 | Screener（`/screener`） | `screener/{stocks,watchlist}` + `export/screener` | 研究员：按模型分数选标的 | README 接口表 ✅ | 完整（**有 P0 缺陷**） |
| 4 | ETF 中心 | Etf（`/etf`） | `etf/{overview,list,hot,performance,scale,flow}` | 所有人：ETF 筛选与横评 | 项目文档 §6 未覆盖 ❌ | 完整 |
| 5 | ETF 详情 | EtfDetail（`/etf/:code`） | `etf/detail/{code}` | 所有人：单只 ETF 深度分析 | ❌ 文档无此模块 | 完整（有文案缺陷） |
| 6 | 策略回测 | Backtest（`/backtest`） | `backtest/{run,strategy-run,signal-analysis}` + `export/strategy-backtest` | 研究员：MA 交叉 / Top-K / 信号分析 | README 回测层 ✅ | 完整（有缺陷） |
| 7 | 组合回测 | Portfolio（`/portfolio`） | `portfolio/{backtest,search}` | 研究员：多资产权重 + 风险平价等优化器 | ❌ README 未提 | 完整（**有 P0 缺陷**） |
| 8 | 策略研究 | Research（`/research`） | `research/*` 共 11 个（IC/相关/分层/CV/重要性/组合优化/冲击/压力） | 量化研究员：因子与模型诊断 | ❌ README 未提 | 完整（**有 P0 缺陷**） |
| 9 | 因子工作室 | FactorStudio（`/studio`，RequireRole） | `studio/*` 共 9 个（GP 挖掘 / NL 转因子 / alpha 评估 / 因子库） | 高级研究员：因子自动挖掘 | ❌ README 未提 | 完整 |
| 10 | 执行中心 | OrderDesk（`/desk`，RequireRole） | `desk/*` 共 12 个（模拟盘下单/撮合/禁买池/风控闸门） | 研究员：信号→模拟交易 | ❌ README 未提（文档 13.3 称"未接入"） | 完整 |
| 11 | 容量与归因 | CapacityAttribution（`/capacity`，RequireRole） | `desk/capacity`、`desk/attribution`（**复用 desk 模块，无独立后端模块**） | 研究员：ADV 容量 + Brinson 归因 | ❌ | 完整 |
| 12 | AI 日报 | Report（`/report`） | `report/{daily, daily/generate}` | 所有人：盘后自动生成研究日报 | ❌ README 未提 | 完整（有缺陷） |
| 13 | 数据中心 | DataCenter（`/data`） | `datacenter/*` 共 22 个（同步/抓取/训练/文本数据/镜像） | 运维+研究员：数据运维与一键训练 | ❌ README 未提 | 完整 |
| 14 | 数据质量 | DataQuality（`/dataquality`，RequireRole） | `ops/quality-scan`、`ops/lineage` | 运维：QC 扫描 + 血缘 | ❌ | 完整 |
| 15 | 任务调度 | Pipeline（`/pipeline`，RequireRole） | `ops/dag`、`ops/dag/rerun` | 运维：DAG 新鲜度与重跑 | ❌ | 完整 |
| 16 | 我的收藏 | Watchlist（`/watchlist`） | `watchlist/{dashboard, correlation}`（**仅只读行情，无增删接口**） | 所有人：自选监控 + 量化联动 | 项目文档 §6.5 ✅ | **半成品** |
| 17 | 预警中心 | Alerts（`/alerts`） | `alerts/*` 共 7 个（六类规则 CRUD + 触发历史） | 研究员：价格/放量/Top-K 迁移告警 | ❌ README 未提 | 完整（**有 P0 缺陷**） |
| 18 | 系统设置 | Settings（`/settings`，RequireAuth） | `settings/*` 共 7 个（偏好/引擎/数据源/密钥/缓存/备份） | 管理员 | ❌ | 完整 |
| 19 | 登录/注册 | Login（`/login`） | `auth/{login,register,register/status,me}` | 所有人 | README 认证与权限 ✅ | 完整（**文档说明错误**） |
| 20 | 通知中心（横切） | Topbar 铃铛 | `notify/{stream(SSE), recent}` | 所有人：任务事件实时推送 | ❌ | 完整 |
| 21 | 监控指标（横切） | 无前端入口 | `/metrics`、`/health/{ready,live}` | 运维 | README 监控与告警 ✅ | **未接线**（无 UI 消费） |

**完成度分布**：完整 17 / 完整但有缺陷 6 / 半成品 1（自选无持久化）/ 空壳 0 / 未接线 1（Prometheus 指标无 UI）。
**值得肯定**：全站没有"点了没反应"的空壳页面；Screener、MarketOverview、DataQuality 等页面的代码注释里明确写了"禁止用 0 值伪造曲线""缺失一律显示 —"等数据真实性红线，这是同类项目里做得扎实的地方。

---

## 2. 文档 ↔ 实现一致性核对（核心）

### 2.1 承诺了但按文档操作会失败 / 与现状不符（P0）

| # | 文档位置 | 文档的说法 | 实际实现 | 影响 | 证据 |
|---|---|---|---|---|---|
| D-01 | `README.md:100`<br>`run.md:180-190` | "首次打开前端会要求登录，Token 取项目根目录 `.env` 中的 `ADMIN_TOKEN`（复制粘贴进去即可）" | 登录页只有**用户名 + 密码**（JWT）两个输入框，没有任何"Token"输入位。ADMIN_TOKEN 仅支持手动写入 `localStorage['AQP_ADMIN_TOKEN']` 由 store 迁移 | **按文档操作 100% 登录失败**，新用户第一次就用不了 | `frontend/src/pages/Login/index.tsx:63-93`（只提交 username/password）；`stores/useAuthStore.ts:86-100`（legacy token 迁移分支） |
| D-02 | `README.md:162-178` | 角色表：viewer = 只读（行情/个股/**预测**/选股）；researcher = +回测/训练 | `GET /stock/{symbol}/predict` 实际要求 **researcher** | viewer 登录后看不了 AI 预测；与 README 自述矛盾 | `backend/app/api/v1/stock.py:194-196` `require_role("researcher")` |
| D-03 | `backend/app/core/auth.py:31-38` | `ROLE_PERMISSIONS[viewer]` 含 `"predict:read"` | 该权限矩阵**未被任何路由使用**（路由用的是 `require_role(minimum_role)` 等级比较） | 权限矩阵是"装饰性"声明，代码与声明不一致，后续按矩阵加权限会踩坑 | `core/auth.py:129-137` `require_role` 只用 `_ROLE_RANK` |
| D-04 | `README.md:203-209` | "未实现（未来展望）：… **Celery / MongoDB / SSE 异步任务队列**（P3-8）" | SSE **已完整实现并已接线**：后端 `GET /notify/stream`（含 quotes/alerts 频道、15s 心跳），前端 Topbar 铃铛用 EventSource 订阅 | README 把已上线能力标成未实现；同时 Celery/MongoDB 确实未做，一句并列造成误判 | `backend/app/api/v1/notify.py:36`；`frontend/src/stores/useNotifyStore.ts:22`；`components/Topbar.tsx:76-80` |
| D-05 | `README.md:176-178` + `Login/index.tsx:199-200` | "行情、选股等只读页面无需登录" | 个股详情（只读页）在**首屏并发调用** `stock/predict`（researcher）→ 匿名时返回 40100 → 客户端**强制跳转登录页** | 匿名浏览个股详情页会被踢出，与"只读页无需登录"的承诺相反 | `pages/StockDetail/index.tsx:57` 并发 `stockApi.predict`；`api/client.ts:24-32` `handleAuthFailure` |

### 2.2 文档描述过时（P1）

| # | 文档位置 | 文档说法 | 现状 | 证据 |
|---|---|---|---|---|
| D-06 | `README.md:16` | 前端"市场概览 / 个股详情" | 19 个页面目录 / 18 条路由 | `frontend/src/App.tsx:86-110` |
| D-07 | `README.md:31` | `api/v1/  # market / stock 路由` | 21 个路由模块、111 个端点 | `api/v1/router.py:26-44` |
| D-08 | `README.md:34`、`README.md:123` | "pytest **68 条**" | 70 个测试文件、547 个测试函数；`overview.md:4` 记录 **743 passed** | `backend/tests/*.py` 计数；`overview.md:4` |
| D-09 | `run.md:279` | `frontend/src/pages/  # 4 个页面` | 19 个 | `ls frontend/src/pages` |
| D-10 | `docs/项目文档.md:3017-3060`（附录 A） | 接口清单：`/admin/*` 共 8 条、`/watchlist/{groups,members,rules,messages,stream}` CRUD、`/market/ai-interpret`、`/stock/{code}/ai-interpret`、`/backtest/task/{id}`、`/backtest/report/{id}.md`、健康检查 **`/healthz`** | 实际：无 `/admin/*`（改为 `/settings` + `/ops` + `/datacenter`）、watchlist 仅 2 个**只读**端点、无 ai-interpret 端点、回测改同步返回、健康检查为 **`/health`** | `api/v1/router.py`；`api/v1/watchlist.py:225,307`；`main.py:140,179,224` |
| D-11 | `docs/项目开发文档.md §9`（API 契约） | 示例路径 `/api/v1/admin/update`、`/api/v1/screener/query`、`/api/v1/backtest/runs`、`/api/v1/market/kline/{code}` | 均不存在（现为 `/settings/*`、`/screener/stocks`、`/backtest/run`、`/market/index/kline`） | 同上 |
| D-12 | `docs/项目文档.md:13.3` | "项目局限：…未做多用户 RBAC 细粒度权限；信号到下单链路为空" | 三级 RBAC 已实现且全站挂载；执行中心（模拟盘下单/撮合/禁买池）已完整 | `core/auth.py:26-37`；`api/v1/desk.py` 12 端点 |
| D-13 | `docs/项目文档.md:3.2.1.6` | 推荐榜展示列应含：行业、score、yhat_return、**prob_up**、**confidence**、ic_20、acc_20 | 实际仅：代码名称 / AI标签 / 预测涨幅 / 全市场分位 / 迷你K线 / 资讯 | `pages/MarketOverview/AiPicksPanel.tsx:116-126` |
| D-14 | `docs/项目文档.md:3.2.1.1` | 市场概览展示 **8 条核心指数** + 点击指数卡跳转"指数详情页" | 实际 KPI 卡只放 **2 条指数**（上证 + 沪深300）；无指数详情页路由 | `pages/MarketOverview/KpiCards.tsx:43-82`；`App.tsx` 无 `/index/:code` |
| D-15 | `docs/项目文档.md:情绪分算法` | 5 因子加权（涨跌家数 + 涨停质量 + 成交额强度 + **北向净流入 zscore** + ML 上涨概率均值） | 实际只用了 2 项：广度 + 涨跌停温差（`50 + breadth×45 + (涨停-跌停)×1.5`） | `api/v1/market.py:_build_sentiment`（428-450 行区间） |
| D-16 | `docs/项目文档.md:3.2.1.5` | 异动监控 5 条规则（含曾涨停/放量/快速波动/突破新高）+ 阈值可在系统管理配置 | `market/anomalies` 存在，但**无独立异动参数配置 UI**；异动数据未在市场概览页展示（只在 rt 块返回，前端未渲染） | `pages/MarketOverview/index.tsx:107-121` 仅渲染 4 个面板 |
| D-17 | `docs/项目文档.md:10.9 / 附录 C.2` | "AI 解读"模块（LLM 生成，含固定免责结尾）为模块能力 | LLM 仅用于 **因子工作室的自然语言转因子**（`studio/nl-to-factor`）与文本数据入库；**无市场/个股 AI 解读端点**，C.2 免责文案无落点 | `core/llm.py` 仅被 `api/v1/studio.py:167`、`data/text_ingest.py:219` 引用 |
| D-18 | `frontend/src/stores/useWatchlistStore.ts:3` | 注释"后端 watchlist 接口尚未实现" | 后端 `/watchlist/dashboard`、`/watchlist/correlation` 已实现并被本页使用 | `api/v1/watchlist.py:225,307`；`api/watchlist.ts` |

### 2.3 文档↔实现相符、值得保留的部分（正面清单）

- `run.md` 的 Redis 启动/降级说明（熔断 5 次 / 60s、设置页"⚠ 缓存降级"徽标）与实现一致：`pages/Settings/index.tsx:554-557`。
- `README.md:143-145` 的防泄漏设计（train 段筛选、gap ≥ horizon、hfq asof 稳定性守卫）与 `tests/test_feature_asof.py`、`test_ml_leakage.py` 对应。
- `run.md:294-298` 日常使用命令（`app.data.pipeline`、`build_features/train`）脚本均存在。
- 回测"T+1 开盘撮合 / 印花税 0.05% / 整手"等口径在参数卡有明确披露：`pages/Backtest/parts.tsx:309-318`。

---

## 3. 产品合理性评估（核心）

### 3.1 【P0-1】选股中心「风险等级」是金融语义错误，会真实误导用户

- **现象**：筛选器提供"风险等级：全部 / 低 / 中 / 高"，列表每行打风险标。
- **实际口径**：`risk = score >= 0.3 ? "low" : score >= 0.1 ? "mid" : "high"`，其中 `score` 是模型预测的**未来 5 日收益率**。页面自己的"数据口径"卡也写明"风险等级：由预测分数映射 low/mid/high"。
- **为什么不合理**：**预测收益高 ≠ 风险低**。在金融语义里"低风险"指波动小、回撤小、流动性好；而这里"低风险"= "模型最看好的票"，恰恰通常是高波动、高弹性的标的。用户按"低风险"筛选后买入，得到的风险敞口与预期完全相反。这是把"收益预期"贴成"风险标签"的典型误导。
- **证据**：`backend/app/api/v1/screener.py:127`；`frontend/src/pages/Screener/FilterPanel.tsx:14-18`；`pages/Screener/index.tsx:724`。
- **建议**：立即改名为「模型看多程度 / 信号强度」（强/中/弱）；若确需风险维度，应换成真实风险指标（如 20 日波动率、最大回撤、Beta、流动性），并在列名加 ⓘ 口径说明。

### 3.2 【P0-2】产品主打能力（AI 个股预测）对默认用户不可用，且匿名访问会被踢出

- `stock/{symbol}/predict` 要求 `researcher`；而个股详情页是**无守卫的只读页**，且是 README 与 run.md 第六步指定的"第一个要看的页面"。
- 匿名访问 → 40100 → `client.ts` 清空会话并 `window.location.href = '/login'`（硬跳转）。用户体感是"一点个股就把我登出了"。
- 刚注册用户默认角色是 `viewer`（`auth.py:154-159` 明确注册入口发不出 admin），同样看不了预测。
- 而同属"模型预测展示"的市场概览推荐榜（`market/overview/*`）是**完全匿名**的——同一个模型的输出，在 A 页面公开、在 B 页面要 researcher，**权限设计自相矛盾**。
- **建议**：`predict` 降为 `viewer`（与 README 角色表、与 `ROLE_PERMISSIONS` 的声明对齐）；或给 StockDetail 加 `RequireAuth` 并在未登录时展示"登录查看 AI 预测"的引导卡片，而不是硬跳转。

### 3.3 【P0-3】5 个"页面无守卫 / 接口要 researcher"的半可用死胡同

| 页面 | 页面守卫 | 依赖接口角色 | 匿名实际表现 |
|---|---|---|---|
| `/stock/:symbol` | 无 | predict=researcher | 硬跳转登录页 |
| `/backtest` | 无 | strategy-run / run / signal-analysis = researcher（**且挂载即自动跑一次**） | 打开即跳转登录页 |
| `/portfolio` | 无 | portfolio/backtest = researcher（search 是 viewer） | 能搜标的，一点"开始回测"就跳登录页 |
| `/research` | 无 | research/* 全部 researcher | 打开即跳转登录页 |
| `/alerts` | 无 | alerts/* 除 `events/read` 外全部 researcher | 打开即跳转登录页 |

- 对比：`/studio`、`/desk`、`/capacity`、`/dataquality`、`/pipeline` 都正确加了 `RequireRole`，说明**守卫是逐页手写、没有统一约定**，漏了 5 个。
- 证据：`frontend/src/App.tsx:86-110`；`api/v1/{stock,backtest,portfolio,research,alerts}.py` 的 `require_role`。
- **建议**：把角色要求提升为路由表的声明式元数据（一个 `ROUTES` 配置里同时写 path/component/minRole），由统一守卫消费，杜绝逐页遗漏。

### 3.4 【P0-4】默认登录凭据是硬编码弱口令 + 默认开启 admin 直通

- `ADMIN_TOKEN` 默认 `"aqp-dev-token-change-me"`，`ALLOW_ADMIN_TOKEN_LOGIN` 默认 `True`，持有者直接视为 `admin`。
- README 却告诉用户"Token 从 `.env` 取"——如果用户没建 `.env`，任何人用这个公开常量即可获得全站 admin（含备份、清缓存、轮转密钥）。
- 生产校验逻辑（`config.py:184-188`）已经明确要求这两项必须关闭，说明团队知道风险，但**默认值与文档站在风险一侧**。
- **建议**：默认 `ALLOW_ADMIN_TOKEN_LOGIN=False`；`ADMIN_TOKEN` 留空且缺失时启动即报错（或自动生成随机值写入 `.env`）；README 改成 JWT 登录流程。

### 3.5 【P1】量化研究逻辑上的其他不合理 / 易误导点

| # | 模块 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| Q-1 | 市场概览 · AI 预测精选 | 列名「**预测涨幅**」但**不显示 horizon**（是未来 5 日？20 日？），且"AI标签"按 ±0.8%/±2% 硬切「强烈看多/看多/震荡/看空」，无任何口径提示。用户极易把"5 日预期 +2%"读成"明天涨 2%" | `AiPicksPanel.tsx:44-50, 119` | 列名改为「预期 5 日收益」，标签加 ⓘ 说明切档阈值与样本口径 |
| Q-2 | 市场概览 | 视图切换条「自选 / 市场概览 / **AI专题**」——「自选」和「AI专题」是**纯装饰按钮，没有任何 onClick**，点了毫无反应 | `MarketOverview/index.tsx:83,85` | 未实现的入口要么删除，要么按 Sidebar 已有机制标"建设中"（`Sidebar.tsx:206-208`） |
| Q-3 | 选股中心 · 股票表现图 | 展示"Alpha 榜 Top5 标的近 1 年（前复权）归一化走势"。这是**用今天的选股结果回看过去 1 年**，属于典型的"后视之选"展示，用户极易读成"跟着这个榜单能赚 X%" | `Screener/PerformanceChart.tsx:1-10` | 图上加显著标注"历史回看，非策略收益、含幸存者偏差"；或直接改为展示榜单**发布后**的模拟净值 |
| Q-4 | 策略回测 | 默认标的写死 `'000001.SZ, 300750.SZ'`（2 只），默认窗口 2 年。2 只股票的 MA 交叉"策略回测"既无统计意义也无基准对比入口；且策略类型下拉**只有 1 个选项且 disabled**，看起来像功能齐全实则不可选 | `Backtest/parts.tsx:50-64, 207` | 默认改为"从自选/选股榜导入股票池"，提供基准选择（沪深300）与滑点参数；单选项下拉改为静态标签 |
| Q-5 | 组合回测 | 支持 risk_parity / max_div / inverse_vol 等优化器与 `n_trials`（Deflated Sharpe），但**前端只有"策略类型"下拉，未见协方差窗口/试验次数/优化器口径披露** | `api/v1/portfolio.py:36-41`；`pages/Portfolio/index.tsx` | 暴露 cov_window / n_trials 并加口径说明，否则"风险平价"结果无法解释 |
| Q-6 | ETF 详情 | 卡片标题「**资金流动（基金特定）**」里放的是 **PE/PB/费率/规模仪表盘**——文不对题，用户会以为看到了资金流数据 | `EtfDetail/index.tsx:583` | 标题改为「估值与费率」 |
| Q-7 | 我的收藏 | 分组数据**只存 localStorage**，无服务端持久化。项目已有账号体系，但换浏览器/换设备自选全丢；且后端有 `/watchlist/*` 只读接口，形成"能看不能存"的断层 | `useWatchlistStore.ts:3,7-24`；`api/v1/watchlist.py` 仅 2 个 GET | 补 watchlist 的 CRUD 接口 + 登录态下服务端持久化（本地作为匿名兜底） |
| Q-8 | AI 日报 | 「重新生成」按钮对所有人可见，但需要 researcher，viewer 点击只能看到报错 | `Report/index.tsx:86-90, 63` | 按角色禁用/隐藏按钮（已有 `hasMinimumRole` 工具可复用） |
| Q-9 | 容量与归因 | 与执行中心共用 `desk` 模块、无独立后端模块；归因基准支持"universe 等权/自定义组合/单一标的"，但**没有提供"沪深300"这类市场基准**，归因结论缺少参照系 | `CapacityAttribution/index.tsx:19-23` | 增加常见指数基准选项 |
| Q-10 | 全站 | 缺"策略/回测的保存与对比"——回测结果不落库（只有缓存），刷新即丢，无法做实验管理 | `api/v1/backtest.py`（无 runs 历史端点；文档 A-24 曾承诺 `/backtest/tasks`） | 至少提供"回测结果保存 + 历史列表 + 两条净值对比" |

### 3.6 缺失的关键能力（对照同类量化平台 / 对照本项目自己的文档承诺）

| 缺失项 | 同类平台通常有 | 本项目状态 | 备注 |
|---|---|---|---|
| AI 解读（LLM 生成 + 合规结尾） | ✅ | ❌ 仅因子工作室的 NL→因子 | 项目文档 §3.2.1.7 / 附录 C.2 明确承诺 |
| 指数详情页 | ✅ | ❌ | 文档 §3.2.1.1 承诺"点击指数卡跳转指数详情" |
| 个股/同行对比 | ✅ | ❌ | 文档 A-14 `peers` 曾承诺 |
| 策略/实验管理（保存、对比、版本） | ✅ | ❌ | 文档 A-19/A-24 曾承诺 |
| 回测报告导出（MD/HTML/PDF） | ✅ | 仅 Excel | 文档 A-22/A-23 曾承诺 |
| 用户/角色管理后台 | ✅ | ❌（只能 `scripts/create_admin.py`） | 与"多用户 RBAC 已实现"不匹配 |
| 自选服务端持久化 | ✅ | ❌ | 见 Q-7 |
| 异动参数可视化配置 | 文档承诺 | ❌ | 见 D-16 |
| 监控指标（`/metrics`）可视化 | ✅ | ❌ 无 UI 消费 | 后端已产出 Prometheus 指标 |

---

## 4. 合规与展示风险

### 4.1 免责声明覆盖情况

README 与 `docs/项目文档.md` 附录 C 定义了 5 处强制声明（C.1 全局页脚 / C.2 AI 解读结尾 / C.3 回测报告首页 / C.4 筹码近似声明 / C.5 模型预测面板声明）。实际落地：

| 声明 | 要求位置 | 落地情况 |
|---|---|---|
| C.1 全局页脚 | 所有页面底部 | ✅ `App.tsx:116`（2xs 灰色小字，视觉权重极低） |
| C.2 AI 解读结尾 | LLM 解读处 | ➖ **无 AI 解读功能，无落点**（D-17） |
| C.3 回测报告首页固定声明 | 回测报告 | ⚠️ 弱化为页脚"结果仅供研究参考"（`Backtest/index.tsx:240`），**缺失"含假设、不代表未来、极端情形无法覆盖"等核心表述** |
| C.4 筹码分布诚实声明 | 个股详情筹码卡（强制） | ⚠️ **部分落地**——ChipPanel 脚注显示"回看 N 日 · 日频换手衰减近似模型"（`StockDetail/index.tsx:510-512`），但**缺 C.4 要求的关键表述**："非 Level-2 真实盘口筹码数据 / 不反映真实持仓成本分布 / 请勿据此单独决策" |
| C.5 模型预测面板声明 | 模型预测展示处 | ⚠️ 仅 StockDetail 面板脚注"不构成投资建议"（`StockDetail/index.tsx:219`）；**市场概览 AI 精选榜、选股中心同属模型预测展示，均无 C.5 声明** |

### 4.2 风险等级分布（按页面诱导性排序）

| 风险等级 | 页面 | 问题 |
|---|---|---|
| 🔴 高 | **选股中心 `/screener`** | 直接产出"买什么"的榜单 + 「风险等级」语义错误（3.1）+ 无 C.5 声明 |
| 🔴 高 | **市场概览 AI 预测精选** | 「预测涨幅」无 horizon、标签无口径（Q-1）+ 无 C.5 声明 |
| 🟠 中 | 策略回测 / 组合回测 | 只有弱免责，缺 C.3 完整表述；组合回测页**连"仅供研究参考"都没有** |
| 🟠 中 | 执行中心 `/desk` | 有"模拟盘"标注（好），但页面无"不构成投资建议"字样；"下单台"名称本身有实盘联想 |
| 🟡 低 | 策略研究 / 容量与归因 | 有"仅用于研究参考"（`Research/index.tsx:484`），归因页无 |
| 🟢 达标 | 个股详情 / ETF 详情 / 数据中心 / 设置 / 日报 | 有明确声明 |

### 4.3 其他合规点（做得好的）

- 全站页脚统一声明 ✅
- 模型置信度明确标注"模型级常数，非个股上涨概率"，并在 tooltip 披露公式 `clip(0.5 + 2×验证集RankIC, 0, 1)` —— 这是**同类项目里少见的诚实做法**，应保留 ✅（`StockDetail/index.tsx:204-208`）
- 情绪指标标注"平台自研口径，非第三方情绪指数" ✅（`AiPicksPanel.tsx:86`）
- 曾存在的"前端自造信心指数（|score|×1400+40）"已被移除并在注释中记录 ✅（`AiPicksPanel.tsx:5-7`）
- AI RankIC 卡披露 horizon 与评估日数 ✅（`KpiCards.tsx:72-77`）
- ETF 详情披露全部数据来源与派生口径 ✅（`EtfDetail/index.tsx:686-690`）

---

## 5. 优先级建议

### P0（必须修，影响可用性或构成误导）

| # | 项目 | 类型 | 建议动作 | 涉及文件 |
|---|---|---|---|---|
| P0-1 | 选股「风险等级」= 预测收益映射 | 误导/合规 | 改名为「信号强度/看多程度」，或换真实风险指标 | `screener.py:127`、`FilterPanel.tsx:14-18`、`Screener/index.tsx:724` |
| P0-2 | `stock/predict` 要求 researcher，匿名访问个股页被硬跳登录 | 可用性 | 降为 viewer（与 README/`ROLE_PERMISSIONS` 对齐），或加守卫+引导卡 | `stock.py:194`、`App.tsx:88`、`client.ts:24-32` |
| P0-3 | 5 个页面无守卫但接口要 researcher | 可用性 | 路由表声明式补 `RequireRole`（backtest/research/portfolio/alerts/stock） | `App.tsx:86-110` |
| P0-4 | README/run.md 登录说明错误（ADMIN_TOKEN vs JWT） | 文档 | 改为"用 `create_admin.py` 建号 → 用户名密码登录"，删除 ADMIN_TOKEN 粘贴说法 | `README.md:100`、`run.md:180-190` |
| P0-5 | 默认弱口令 + admin 直通默认开启 | 安全 | `ALLOW_ADMIN_TOKEN_LOGIN` 默认 False；`ADMIN_TOKEN` 缺失即报错 | `config.py:40-47` |
| P0-6 | 市场概览「预测涨幅」无 horizon、「AI标签」无口径 | 误导 | 列名加 horizon、标签加 ⓘ 口径 | `AiPicksPanel.tsx:44-50,119` |
| P0-7 | 回测/选股结果缺 C.3/C.5 级声明 | 合规 | 在选股榜、回测结果区、组合回测页补充完整风险声明 | `Screener/index.tsx`、`Backtest/index.tsx`、`Portfolio/index.tsx` |

### P1（应修，明显影响产品完成度与可信度）

| # | 项目 | 建议动作 |
|---|---|---|
| P1-1 | 自选无服务端持久化 | 补 watchlist CRUD + 登录态同步（Q-7） |
| P1-2 | README 全面过时（页面数/端点数/测试数/未实现清单） | 按第 2.2 节表逐条更新；"未实现"清单只保留 PWA / Redis Sentinel / PostgreSQL / Celery |
| P1-3 | 市场概览「自选 / AI专题」装饰按钮 | 删除或标"建设中"（Q-2） |
| P1-4 | 毕业设计文档接口清单/情绪分公式/局限章节与实现脱节 | 更新附录 A、§3.2.1 情绪分、§13.3 项目局限（D-10~D-17） |
| P1-5 | 回测默认股票池不可配、无基准与滑点参数 | 支持自选/选股榜导入 + 基准选择（Q-4） |
| P1-6 | 个股详情筹码卡 C.4 声明不完整 | 在 ChipPanel 脚注补齐"非 Level-2 真实盘口、不反映真实持仓成本、请勿据此单独决策" |
| P1-7 | 策略回测结果不落库，无法对比/复现 | 增加回测结果保存与历史列表 |

### P2（可排期，体验与一致性）

| # | 项目 |
|---|---|
| P2-1 | ETF 详情「资金流动」卡片文不对题（Q-6） |
| P2-2 | AI 日报「重新生成」按角色禁用（Q-8） |
| P2-3 | 选股表现图补充"后视之选"警示（Q-3） |
| P2-4 | 组合回测暴露 cov_window / n_trials 并披露优化器口径（Q-5） |
| P2-5 | 归因模块增加沪深300 等市场基准（Q-9） |
| P2-6 | 补充指数详情页 / 同行对比 / 用户管理 / 回测报告导出（文档曾承诺） |
| P2-7 | `/metrics` 增加可视化入口，或明确标注为运维专用 |
| P2-8 | `etf/list` 未挂 `require_role`，与同模块其余 6 个端点不一致，统一为 viewer |

---

## 6. 附：证据索引速查

| 结论 | 关键证据 |
|---|---|
| 登录方式文档错误 | `README.md:100`；`run.md:180-190`；`pages/Login/index.tsx:63-93` |
| predict 角色过严 | `api/v1/stock.py:194-196`；`core/auth.py:31-38`；`README.md:176-178` |
| 权限矩阵未被使用 | `core/auth.py:129-137` |
| SSE 已实现 | `api/v1/notify.py:36`；`stores/useNotifyStore.ts:22`；`components/Topbar.tsx:76-80` |
| 风险等级口径 | `api/v1/screener.py:127`；`pages/Screener/index.tsx:724` |
| 死胡同按钮 | `pages/MarketOverview/index.tsx:83,85` |
| 页面守卫缺失 | `App.tsx:86-110` vs `api/v1/{backtest,research,portfolio,alerts}.py` 的 `require_role` |
| 弱口令默认值 | `core/config.py:40-47`；生产校验 `config.py:184-188` |
| 文档接口清单过时 | `docs/项目文档.md:3017-3060`；`docs/项目开发文档.md §9` |
| 情绪分实现与文档不符 | `api/v1/market.py:_build_sentiment` vs `docs/项目文档.md` 情绪分 5 因子公式 |
| 测试数量 | `overview.md:4`（743 passed） vs `README.md:34`（68 条） |
| 免责声明分布 | `App.tsx:116`；`StockDetail:219`；`EtfDetail:690`；`DataCenter:898`；`Settings:626`；`Backtest:240`；`Research:484` |
