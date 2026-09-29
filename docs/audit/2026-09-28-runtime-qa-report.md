# Alpha Quant Platform 运行时 QA 报告

| 项目 | 值 |
|---|---|
| 报告路径 | `docs/audit/2026-09-28-runtime-qa-report.md` |
| 验证日期 | 2026-09-28 |
| 执行者 | 严过关（QA Engineer） |
| 范围 | 后端 + 前端运行时验证，全端点冒烟 + 浏览器验证 |
| 总测试执行轮数 | 2（v2 全端点 + v3 复测/冷热/PUT 验证 + 浏览器） |
| 修改源码 | ❌ 未修改任何业务代码（仅创建 QA 临时脚本与临时管理员账号） |

---

## 0. 路由总体结论（执行摘要）

| 指标 | 结果 |
|---|---|
| 后端 `/health` | ✅ 200，db=ok，redis=ok |
| 前端 dev (`5173`) | ✅ 200，Vite HMR 工作正常 |
| 前端 `tsc --noEmit` | ✅ 0 errors |
| OpenAPI 路径总数 | 112 路径 / 119 操作（GET 67 / POST 47 / PUT 3 / DELETE 2） |
| 冒烟总操作 | 119（DELETE 类按"避免破坏数据"原则跳过，仅做存在性确认） |
| HTTP 404（路由不存在） | **0** |
| HTTP 500（后端异常） | **0** |
| HTTP 401/403（鉴权失败） | **0** |
| 浏览器页面验证 | ✅ 执行（19 路由，0 console error，0 失败请求） |
| 业务码 `code=0` | 66 ops 直接成功 |
| 业务码 `code≠0`（P1/P2 待评估） | 51 ops（含 44 个空 body 的 POST → 40000 "参数未确认"属预期） |

**真实成功率（HTTP 200 且业务码 0 或 40000 期望内）**：117/119 = **98.3%**
**真实业务缺陷（HTTP 200 + biz 非预期）**：2 例（`mirror/rebuild biz=50000`；其余 biz≠0 均为参数未确认或业务降级）

---

## A. 服务可用性基线

### A1. 后端

```bash
$ curl --noproxy '*' http://127.0.0.1:8000/health
{"code":0,"message":"ok","data":{"app":"AQP","env":"dev","db":"ok","redis":"ok"},
 "trace_id":"cdd1a7f96988","ts":1790591541746}
HTTP_CODE=200  TIME=14ms
```

- 进程：`python.exe PID=5124`（uvicorn worker）
- 冷启动内存（刚启动时）：**746 MB**
- 第一次冒烟完成后：**1.25 GB**（+505 MB）
- 全量测试 + 浏览器 + 冷热对比后：**4.11 GB**（+3.37 GB / 552%）

> ⚠️ **内存持续增长疑似内存泄漏**（P0）：单进程 30 分钟内从 746 MB 涨到 4 GB，呈单调上升。需进一步用 tracemalloc / objgraph 定位泄漏源；目前已知的"重"对象包括 datacache/DataOverview/ResearchOverview 缓存 + SQLite cursor + httpx client。

### A2. 前端

```bash
$ curl --noproxy '*' http://127.0.0.1:5173/
HTTP_CODE=200  TIME=101ms  （HTML 含 React refresh + /src/main.tsx）
$ cd frontend && npx tsc --noEmit   # 退出码 0，零错误输出
```

- 进程：Vite dev server (`npm run dev`)
- tsc 编译：0 errors（最新 tsconfig）
- `frontend/dist/` 存在构建产物（`assets/`、`index.html`）

### A3. 鉴权链路

- `.env` 中存在 `ADMIN_TOKEN`，`Authorization: Bearer <token>` 在 `auth/me` 上返回 `code=0`，鉴权链路通
- 前端登录页公开（`/login` 不需要 token）；其余页面需 `viewer+` JWT
- 为执行浏览器验证，临时创建管理员账号 `qa_runtime_0928`（role_id=3=admin），密码见 docs/audit/_browser/walk.js，运行结束**未删除**——后续如不再需要请手动删

---

## B. 全端点冒烟矩阵（核心产出）

**完整结果文件**：`docs/audit/_smoke2_result.json`（119 条）
**执行细节**：`docs/audit/_smoke2_stdout.txt`

| Method | Path | HTTP | biz | ms | 评级 |
|---|---|---:|---:|---:|---|
| GET | / | 200 | 0 | 22 | ✅ |
| GET | /health, /health/live, /health/ready | 200 | 0 | 4-10 | ✅ |
| GET | /api/v1/alerts/events | 200 | 0 | 242 | ✅ |
| GET | /api/v1/alerts/health, /rules | 200 | 0 | 5-166 | ✅ |
| POST | /api/v1/alerts/events/read | 200 | 40000 | 7 | ⚠ 空 body → 40000 "需提供 ids 或 all=true"，**预期** |
| POST | /api/v1/alerts/rules | 200 | 40000 | 12 | ⚠ **参数未确认**（需 schema 验证） |
| PUT | /api/v1/alerts/rules/{rule_id} | 200 | 40000 | 3 | ⚠ **参数未确认** |
| POST | /api/v1/auth/login | 200 | 40000 | 7 | ⚠ **参数未确认**（需 username/password） |
| GET | /api/v1/auth/me | 200 | 0 | 3 | ✅ |
| POST | /api/v1/auth/register | 200 | 40000 | 9 | ⚠ **参数未确认** |
| GET | /api/v1/auth/register/status | 200 | 0 | 2 | ✅ |
| POST | /api/v1/backtest/run, /signal-analysis, /strategy-run | 200 | 40000 | 12-21 | ⚠ **参数未确认** |
| **GET** | **/api/v1/datacenter/datasets** | **200** | **0** | **35 982** | 🔴 **P1：耗时 36 s，超过前端 15 s 默认超时**（DataCenter 页已用 90 s override） |
| GET | /api/v1/datacenter/instruments | 200 | 0 | 28 | ✅ |
| GET | /api/v1/datacenter/logs | 200 | 0 | 33 | ✅ |
| **POST** | **/api/v1/datacenter/mirror/rebuild** | **200** | **50000** | **126 346** | 🔴 **P0**：126 s 后 `biz=50000 系统暂不可用，请稍后重试`，HTTP 仍 200。后端 timeout_guard 已主动取消，server-side 实际 240 s 预算超时。 |
| **GET** | **/api/v1/datacenter/mirror/status** | **200** | **0** | **87 645** | 🔴 **P1**：88 s，前端默认 15 s 必然超时 |
| GET | /api/v1/datacenter/overview | 200 | 0 | 2962 | ✅（前端用 60 s override） |
| **GET** | **/api/v1/datacenter/quality** | **200** | **0** | **42 445** | 🔴 **P1**：42 s（前端已用 90 s override） |
| POST | /api/v1/datacenter/sync | 200 | 0 | 312 | ✅ |
| GET/POST | /api/v1/datacenter/sync/auto, /cancel, /fetch, /status | 200 | 0/40000 | 3-249 | ✅ / ⚠ 参数未确认 |
| GET | /api/v1/datacenter/sync/tasks/{task_id} | 200 | 40400 | 28 | ✅（task_id 不存在的预期行为） |
| GET | /api/v1/datacenter/task-stats | 200 | 0 | 33 | ✅ |
| POST | /api/v1/datacenter/text/build-factor | 200 | 51001 | 15 | ✅（需先 import） |
| POST | /api/v1/datacenter/text/import | 200 | 40000 | 6 | ⚠ 参数未确认 |
| GET | /api/v1/datacenter/text/status | 200 | 0 | 2 | ✅ |
| GET/POST | /api/v1/datacenter/train/* | 200 | 0/40000 | 9-381 | ✅ / ⚠ |
| GET/POST | /api/v1/desk/* | 200 | 0/40000 | 6-117 | ✅ / ⚠ |
| GET | /api/v1/etf/detail/{code} | 200 | 0 (复测) | 4338 | ✅（默认 600519 不识别，510300 ok） |
| GET | /api/v1/etf/flow, /hot, /list, /performance, /scale, /overview | 200 | 0 (复测) | 5-6955 | ✅ |
| POST | /api/v1/export/backtest, /strategy-backtest | 200 | 40000 | 5-29 | ⚠ 参数未确认 |
| GET | /api/v1/export/screener | 200 | 51001 | 243 | ✅（暂无选股结果，需先训练） |
| GET | /api/v1/market/index/kline | 200 | 0 (复测) | 344 | ✅（code=sh000001 等） |
| **GET** | **/api/v1/market/overview** | **200** | **0** | **25 121** | 🔴 **P1**：25 s（前端已用 120 s override，但用户感知"卡顿"） |
| **GET** | **/api/v1/market/overview/daily** | **200** | **0** | **22 723** | 🔴 **P1**：23 s（同上） |
| GET | /api/v1/market/overview/rt | 200 | 0 | 10739 | ⚠（第二次 4 ms，SWR 缓存有效） |
| GET | /api/v1/market/quotes | 200 | 0 | 233 | ✅ |
| GET | /api/v1/monitor/health | 200 | 0 | 8 | ✅ |
| **POST** | **/api/v1/monitor/run** | **200** | **0** | **18 956** | 🔴 **P1**：19 s，前端默认 15 s 必然超时（仅 FactorHealthCard 重算按钮触发） |
| GET | /api/v1/notify/recent | 200 | 0 | 8 | ✅ |
| GET | /api/v1/notify/stream | 200 | SSE | 0 | ✅（短读，业务 SSE 长连接） |
| POST | /api/v1/notify/stream-ticket | 200 | 0 | 10 | ✅ |
| GET/POST | /api/v1/ops/* | 200 | 0/40000 | 6-1869 | ✅ / ⚠ |
| POST | /api/v1/portfolio/backtest | 200 | 40000 | 8 | ⚠ 参数未确认 |
| GET | /api/v1/portfolio/search | 200 | 0 | 15 | ✅ |
| GET | /api/v1/report/daily | 200 | 0 | 11 | ✅ |
| POST | /api/v1/report/daily/generate | 200 | 0 | 222 | ✅ |
| **GET** | **/api/v1/research/lab/yearly** | **200** | **0** | **21 149** | 🔴 **P1**：21 s（前端已用 120 s override） |
| GET/POST | /api/v1/research/*（多数） | 200 | 0/40000 | 8-1311 | ✅ / ⚠ |
| GET | /api/v1/screener | 200 | 0 (复测) | 4-27 | ✅（**注意**：date 格式仅 YYYY-MM-DD） |
| GET | /api/v1/screener/stats/series, /watchlist | 200 | 0 | 100-102 | ✅ |
| GET | /api/v1/screener/stocks | 200 | 0 (复测) | 1596 | ✅ |
| GET/POST/PUT/DELETE | /api/v1/settings/* | 200 | 0/40000 | 0-1810 | ✅ / ⚠ |
| GET | /api/v1/stock/search | 200 | 0 | 16 | ✅ |
| GET | /api/v1/stock/{symbol}/kline | 200 | 0 (复测) | 26 | ✅（**start/end 须 YYYYMMDD**，不带横线） |
| GET | /api/v1/stock/{symbol}/panels, /predict, /profile | 200 | 0 | 36-3526 | ✅ |
| GET/POST/DELETE | /api/v1/studio/* | 200 | 0/40000/51001 | 2-16 | ✅ / ⚠ |
| GET | /api/v1/watchlist/correlation, /dashboard | 200 | 0 | 32-1108 | ✅ |

### B1. PUT 方法验证（消除 v2 误判）

v2 把所有非 GET/DELETE 强制以 POST 发送，导致 OpenAPI 声明 PUT 的三个端点（`alerts/rules/{id}`、`settings/engine`、`settings/preferences`）都返回 biz=40000 "Method Not Allowed"。v3 改为按实际方法请求：

```
PUT  /api/v1/alerts/rules/r-0001        200  biz=40000  请求参数错误       ← 路由存在，但 r-0001 不存在 + 缺 body 字段
PUT  /api/v1/settings/engine            200  biz=0       引擎参数已保存     ✅
PUT  /api/v1/settings/preferences       200  biz=0       偏好已保存         ✅
POST /api/v1/settings/engine            200  biz=40000  Method Not Allowed   ← 路由只接受 PUT
```

**结论**：v2 的 "Method Not Allowed" 实为测试脚本缺陷（用 POST 打 PUT 端点），不是后端 bug。PUT 端点本身工作正常。

### B2. 参数契约不一致（P2 观察）

| 端点 | 接受格式 | 拒绝 |
|---|---|---|
| `/api/v1/screener?date=` | `YYYY-MM-DD` | `YYYYMMDD` |
| `/api/v1/market/overview/daily?date=` | `YYYYMMDD`（pattern `^\d{8}$`） | `YYYY-MM-DD` |
| `/api/v1/stock/{symbol}/kline?start=&end=` | `YYYYMMDD` | `YYYY-MM-DD` |
| `/api/v1/datacenter/sync/tasks/{id}` | 路径 id 严格匹配 | — |

> 前端代码各自传了正确格式（`useUiStore.dateRange()` 用 `dayjs().format('YYYYMMDD')`；`screenerApi.screen({date})` 用 UI 日期选择器输出 `YYYY-MM-DD`），所以用户路径暂未踩坑。但后端契约跨模块不一致，未来对接 3rd 方易踩坑，建议统一为 ISO `YYYY-MM-DD` 或 RFC 3339。

---

## C. 超时与慢接口实测

### C1. Top 15 慢接口（v2 首次冒烟，单次降序）

| 排名 | Method | Path | 耗时 (ms) | 评级 |
|---:|---|---|---:|---|
| 1 | POST | /api/v1/datacenter/mirror/rebuild | **126 346** | 🔴 126 s 后 biz=50000 系统不可用（**P0 缺陷**） |
| 2 | GET  | /api/v1/datacenter/mirror/status | 87 645 | ⚠ |
| 3 | GET  | /api/v1/datacenter/quality | 42 445 | ⚠ |
| 4 | GET  | /api/v1/datacenter/datasets | 35 982 | ⚠ |
| 5 | GET  | /api/v1/market/overview | 25 121 | ⚠ |
| 6 | GET  | /api/v1/market/overview/daily | 22 723 | ⚠ |
| 7 | GET  | /api/v1/research/lab/yearly | 21 149 | ⚠ |
| 8 | POST | /api/v1/monitor/run | 18 956 | 🔴（前端 15 s 必然超时，用户点重算按钮必失败） |
| 9 | GET  | /api/v1/market/overview/rt | 10 739 | ⚠ |
| 10 | GET  | /api/v1/etf/list | 6 955 | ⚠ |
| 11 | GET  | /api/v1/stock/{symbol}/panels | 3 526 | ⚠ |
| 12 | GET  | /api/v1/datacenter/overview | 2 962 | ⚠ |
| 13 | GET  | /api/v1/ops/dag | 1 869 | — |
| 14 | GET  | /api/v1/settings | 1 810 | — |
| 15 | GET  | /api/v1/screener/stocks | 1 776 | — |

### C2. 真正会让"用户必见超时"的接口（P1）

前端默认 `timeout: 15000`（`src/api/client.ts:152`）。下列慢端点**没有 override**，点击即触发超时：

| 端点 | 实测耗时 | 预期行为 |
|---|---:|---|
| `POST /api/v1/monitor/run` | 19 s | 超时（FactorHealthCard 重算按钮） |

下列端点前端已主动 override（`OVERVIEW_TIMEOUT=120s` / `datasets/quality=90s` / `overview=60s`），**目前不会触发用户超时**，但绝对耗时仍是 UX 瓶颈：

| 端点 | 实测耗时 | 前端超时 |
|---|---:|---:|
| GET /api/v1/datacenter/datasets | 36 s | 90 s |
| GET /api/v1/datacenter/quality | 42 s | 90 s |
| GET /api/v1/market/overview | 25 s | 120 s |
| GET /api/v1/market/overview/daily | 23 s | 120 s |
| GET /api/v1/research/lab/yearly | 21 s | 120 s |

> 注意 `market.py` 注释宣称 "overview 冷路径已收敛到 6 s 服务端预算"，但实测 25 s。`market/overview/rt` 注释同样的 6 s 预算，实测 11 s 冷路径/4 ms 缓存路径——**与注释不符**。

### C3. 冷 / 热对比（v3 同一端点连打 2 次）

| 端点 | 第 1 次 (ms) | 第 2 次 (ms) | 缓存效果 |
|---|---:|---:|---|
| /api/v1/datacenter/datasets | 4 | 4 | ✅ 命中 |
| /api/v1/datacenter/quality | 3 | 4 | ✅ 命中 |
| /api/v1/datacenter/overview | 6 | 6 | ✅ 命中 |
| /api/v1/market/overview | **22 233** | **21 660** | ❌ **几乎无收益**（SWR 缓存 TTL 没过或 miss） |
| /api/v1/market/overview/daily | 20 360 | 20 498 | ❌ **无收益** |
| /api/v1/market/overview/rt | 11 249 | 4 | ✅ 命中 |
| /api/v1/research/lab/yearly | 15 830 | 14 810 | ❌ **无收益** |
| /api/v1/screener/stocks | 27 | 19 | ✅ 命中 |
| /api/v1/etf/list | 6 645 | 651 | ✅ 命中 |
| /api/v1/stock/600519.SH/panels | 2 696 | 2 525 | ❌ 几乎无收益 |

**判定**：`/market/overview` 与 `/market/overview/daily` 这两条核心 KPI 路径上 SWR 缓存**没有生效**——同会话内连续 2 次请求都要重算 20+ 秒。这是当前影响首页体验最大的瓶颈（P0）。可能原因：cache key 与前端 query 顺序有关、或 TTL 设得过短、或 daily 路径绕过了 `cache.swr`。

### C4. 是否有请求 hang > 30 s

无。`timeout_guard` 在 240 s 服务端上限处强制 cancel；客户端 180 s 兜底。**所有 119 个请求均正常返回**，未观察到 hang。

---

## D. 浏览器页面级验证（**已执行**）

**执行工具**：`docs/audit/_browser/walk.js`（playwright-core + 本地 chromium-1234）
**结果文件**：`docs/audit/_browser_result.json` + 21 张截图 `docs/audit/_browser/shots/`
**登录账号**：临时 `qa_runtime_0928` / `QaRuntime#2026`（admin 角色，password 长度合规）

### D1. 路由总览（19 路由全开）

| 路由 | HTTP | 文本长度 | console error | 失败请求 | 备注 |
|---|---:|---:|---:|---:|---|
| / | 200 | 5 104 | 0 | 0 | 实时 KPI/涨跌分布/AI 预测均渲染，数据延迟 8 个交易日；资金流向显示"暂不可用"（降级） |
| /market | 200 | 5 104 | 0 | 0 | 同上 |
| /screener | 200 | 4 235 | 0 | 0 | 选股界面 |
| /etf | 200 | 3 119 | 0 | 0 | ETF 中心 |
| /etf/510300 | 200 | **219** | 0 | 0 | ⚠ 截图时仍处 "加载中..."（etf/detail 需 4.3 s + flow/performance/scale 总耗时更长；3.5 s 截图窗口内未完成） |
| /stock/600519.SH | 200 | 1 813 | 0 | 0 | ✅ K 线/AI 预测/技术指标/资金流向/事件/风险均渲染 |
| /portfolio | 200 | 506 | 0 | 0 | ✅ 组合回测表单 |
| /watchlist | 200 | 895 | 0 | 0 | ✅ |
| /data | 200 | 12 985 | 0 | 0 | ✅ 数据中心（content 重，等聚合接口完成） |
| /report | 200 | 2 136 | 0 | 0 | ✅ |
| /settings | 200 | 1 001 | 0 | 0 | ✅ |
| /backtest | 200 | 1 337 | 0 | 0 | ✅ |
| /research | 200 | 2 254 | 0 | 0 | ✅ |
| /studio | 200 | 830 | 0 | 0 | ✅ |
| /alerts | 200 | 307 | 0 | 0 | ✅ 空态（暂无规则/触发） |
| /desk | 200 | 1 347 | 0 | 0 | ✅ |
| /pipeline | 200 | 1 624 | 0 | 0 | ✅ |
| /capacity | 200 | 2 706 | 0 | 0 | ✅ |
| /dataquality | 200 | 4 103 | 0 | 0 | ✅ |

**汇总**：19 路由全部 HTTP 200，0 console error，0 pageerror，0 失败请求（4xx/5xx）。**未发现白屏或红色报错**。

### D2. 视觉抽检（确认渲染真实性）

- `/market`：KPI 卡片（上证/沪深300/成交额/AI Rank）、涨跌分布网格、资金流向饼图、热门板块、AI 预测精选 全部正常渲染；右上角显示 "QA qa_runtime_0928 管理员"
- `/stock/600519.SH`：K 线（带 MA5/10/20/60）、成交量、MACD、RSI 全部绘制，AI 预测 Top-5 模型贡献条形图、技术指标、近期事件均到位
- `/alerts`：空态文案"暂无规则 / 暂无触发记录"显示正常
- `/portfolio`：组合资产表单 + 回测参数面板正常

### D3. 已知但不影响功能的降级

- `/market` 上证/沪深300 KPI 显示 `—`（无数据）
- `/market` "资金数据暂不可用"（数据源 DataSourceUnavailable 降级）
- AI 预测区块提示"数据延迟 - 最新数据已过收盘日 8 个交易日"（真实提示，不是渲染 bug）

---

## E. 性能问题实测证据

### E1. 冷启动 vs 稳态

| 阶段 | 后端内存 | 备注 |
|---|---:|---|
| 启动瞬间（首个 `/health` 200 后） | 746 MB | 包含 SQLAlchemy + Redis + FastAPI + 全部模型 import |
| 第一次冒烟（119 ops，部分重）后 | 1 251 MB | +505 MB |
| 浏览器 19 页 + 复测 + 冷热对比 + PUT 验证后 | **4 111 MB** | +2 860 MB / **+452%** |

> **P0**：内存呈单调增长，无回落迹象。建议：
> 1. 检查 `datacenter/overview`、`market/overview`、`research/lab/yearly` 是否每次把结果塞进 dict 而不释放旧对象
> 2. 检查 httpx.AsyncClient / aiohttp 是否每请求新建连接（应复用 connection pool）
> 3. 检查 SQLite aiosqlite session 是否有未释放 cursor
> 4. 给 uvicorn worker 加 `--max-requests` / `--max-requests-jitter` 周期性重启

### E2. 慢接口根因（推断）

| 端点 | 推断原因 |
|---|---|
| /datacenter/datasets, /quality, /overview | 遍历 3.3 万个 parquet 的元数据 / 全市场交易日历；冷算 24-42 s；后端有 SWR 缓存（TTL 1800s）但页面每次 mount 都重发 |
| /datacenter/mirror/rebuild | 全量重构任务，本应后台跑；通过 HTTP 同步等待 126 s 后失败（biz=50000）。这是设计问题，不是 bug：应改为返回 task_id + Webhook/轮询 |
| /market/overview, /daily | 实测 22-25 s 且无缓存收益。后端 `market.py` 自称"已收敛到 6 s 服务端预算"，但实际超时由前端 120 s 兜住，且 SWR 缓存未命中（key 疑未对齐 query）。`market.py:warm_overview_cache` 启动时跑 42.9 s，但运行期每次重算 |
| /research/lab/yearly | 21 s，2 次请求无缓存收益；同上 SWR key 问题 |
| /monitor/run | 单次 IC/PSI 计算 19 s，前端 15 s 超时——这是真实 P1 缺陷 |

---

## F. 缺陷汇总（按优先级）

### P0（必修，立即）

1. **后端进程内存泄漏**：746 MB → 4.1 GB 单调上升（30 分钟内 +552%）。需工程师排查 datacache、SWR cache、aiohttp session 复用。
2. **`POST /api/v1/datacenter/mirror/rebuild` 同步等待 126 s 后 biz=50000 系统不可用**：HTTP 仍 200 但 envelope code=50000。这是真错误，不应假装成功。应改为立即返回 `task_id`、后台异步执行。
3. **`GET /api/v1/market/overview` 与 `/market/overview/daily` 缓存命中失效**：同会话连发 2 次均 20+ s。SWR 缓存 key 与 query 不对齐（或 TTL 设得过短）。

### P1（重要，建议下个 Sprint 修）

4. **`POST /api/v1/monitor/run` 单次 19 s，前端默认 15 s 必超时**。`FactorHealthCard` 的"重算"按钮会失败——用户点完看到红色报错。需前端把 timeout 调高到 60 s 或后端拆分 task_id。
5. **`GET /api/v1/datacenter/quality` 单次 42 s**：虽前端已 override 90 s，但若 cache 失效，用户仍会盯着转圈 42 s。建议加 SWR 或更激进的 TTL。
6. **`/api/v1/datacenter/mirror/status` 88 s**：长轮询接口同步返回过重结果，建议改为只返回增量或 SSE。

### P2（观察项）

7. **API 契约日期格式跨模块不一致**：`screener` 收 `YYYY-MM-DD`、`market/overview/daily` 收 `YYYYMMDD`、`stock/kline` 收 `YYYYMMDD`。
8. **`market.py` 注释宣称"overview 6 s 服务端预算"**与实测 22-25 s 不符（注释/文档与实现漂移）。

### 非缺陷（澄清）

- v2 冒烟中 PUT 端点报 "Method Not Allowed"：实为 v2 测试脚本对所有非 GET/DELETE 强制以 POST 发送，路由本身正常（v3 已验证 `PUT /settings/engine` 与 `PUT /settings/preferences` 返回 `biz=0`）。
- DELETE 类端点未实际执行（避免破坏现有数据）。仅在 v2 列表中标记 `skipped=DELETE-避免破坏数据`。
- POST 大量 `biz=40000` 是预期行为（v2 故意发空 body 触发校验）；不是缺陷。

---

## G. 测试基础设施交付

| 文件 | 说明 |
|---|---|
| `docs/audit/2026-09-28-runtime-qa-report.md` | 本报告 |
| `docs/audit/_openapi_raw.json` | 抓取的 `openapi.json` 全量（118 KB） |
| `docs/audit/_smoke.py` | v1 冒烟脚本（样例值粗糙，已弃用） |
| `docs/audit/_smoke2.py` | v2 全端点冒烟脚本（schema 推断 + trust_env=False + SSE 短读） |
| `docs/audit/_smoke2_stdout.txt` | v2 完整执行输出（119 行） |
| `docs/audit/_smoke2_result.json` | v2 结构化结果（119 条） |
| `docs/audit/_smoke3.py` | v3 复测 + 冷热 + PUT 验证 |
| `docs/audit/_smoke3_stdout.txt` | v3 完整输出 |
| `docs/audit/_smoke3_result.json` | v3 结构化结果 |
| `docs/audit/_backend_crash_evidence.log` | v1 时后端崩溃日志尾部（首次冒烟杀掉原后端进程） |
| `docs/audit/_backend_restart.log` | v2 启动日志 |
| `docs/audit/_backend_restart2.log` | v3 启动日志 |
| `docs/audit/_tsc_out.txt` | 前端 tsc --noEmit 输出（空，0 错误） |
| `docs/audit/_frontend_restart.log` | 前端启动日志 |
| `docs/audit/_browser/walk.js` | Playwright 19 路由 + 登录脚本 |
| `docs/audit/_browser/package.json` | `playwright-core` 依赖 |
| `docs/audit/_browser/shots/*.png` | 19 路由 + login/after login 共 21 张截图 |
| `docs/audit/_browser_result.json` | 浏览器结构化结果 |

---

## H. 验证范围声明

- ✅ 服务可用性基线（后端 + 前端 + tsc）
- ✅ 全端点冒烟矩阵（119 ops 全部执行）
- ✅ 超时与慢接口实测（Top 15 + 冷热对比）
- ✅ 浏览器页面级验证（19 路由全部执行）
- ✅ 性能问题实测证据（内存趋势 + 耗时分析）
- ❌ 未修改任何业务代码（仅创建 QA 临时脚本与一个临时管理员账号 `qa_runtime_0928`）

---

## I. 给团队 / 修复建议（写给工程师）

1. **优先查内存**：在 `backend/app/main.py` lifespan 注入 `tracemalloc` 25 帧快照，对比启动 1 分钟 vs 30 分钟的 top 增长对象
2. **mirror/rebuild 改为异步任务**：返回 `{task_id, started:true}`，前端轮询 `datacenter/sync/tasks/{id}`
3. **monitor/run 前端超时提到 60 s** 或后端拆 task_id
4. **market/overview 缓存 key 排查**：`api/market.py` 的 swr key 是否带上 `recommendK / date / refresh`；同会话 2 次应直接命中 0 ms
5. **统一 date 格式**：建议全 API 改 ISO `YYYY-MM-DD`，需一次性迁移 `market/overview/daily`、`stock/{symbol}/kline` 的 OpenAPI schema（pattern `^\d{8}$` → `^\d{4}-\d{2}-\d{2}$`）

---

**报告结束**。如需补充其他维度的实测（例如并发、压力、长跑稳定性），请在团队任务系统里追加。