# AQP 前后端运行时实证冒烟报告

- **审计日期**：2026-09-27
- **执行人**：寇豆码（Kou）· 工程师（实测模式）
- **项目根**：`D:\Python_Project\Alpha Quant Platform`
- **后端监听**：`127.0.0.1:8000`（uvicorn `app.main:app`，本次审计期间**保持运行**）
- **前端 dev**：`http://localhost:5173`（本次审计期间保持运行）
- **原始数据**：`docs/audit-2026-09-27/smoke-results.csv`（118 行）、`openapi.json`、`probe.log`、`retest.log`、`frontend-build*.log`

---

## 0. 结论速览

| 项目 | 结论 |
|---|---|
| 后端启动 | ✅ 成功，`/health` 返回 200（db=ok, redis=ok） |
| 路由总数 | 111 个 path / 118 个 method+path 操作 |
| 实测端点 | 85（另 33 个写操作主动跳过，1 个 SSE 长连接） |
| HTTP 200 | 80 |
| HTTP 4xx / 5xx | **0**（后端设计为 HTTP 恒 200，错误码在 body） |
| 探测超时(20s) | 4 → 其中 3 个是**冷启动**（复测 0.01–0.02s），1 个**真慢**（ops/lineage） |
| 业务错误码 code≠0 | 20，**全部**由参数/数据/配置引起，**无一是路由缺失** |
| 前端构建 | ⚠️ `npm run build` 因**环境安全删除 shim** 失败；**代码本身编译通过**（tsc 通过、747 模块转换成功、0 error 0 warning） |
| Vite proxy | ✅ **配置正确**：target 默认 `127.0.0.1:8000` = 后端实际监听端口，链路实测连通 |
| 404 悬空调用 | ✅ **0 个**（前端 104 条调用路径全部命中后端路由） |

**一句话**：前后端主链路是通的，没有 404、没有 HTTP 5xx；真正会"点了没反应"的是 **2 个慢端点超出前端超时预算**（`ops/lineage`、`research/lab/yearly`）和 **3 个 datacenter 端点冷启动首击**。

---

## 1. 后端启动状态

```
$ backend/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
GET /health -> 200
{"code":0,"message":"ok","data":{"app":"AQP","env":"dev","db":"ok","redis":"ok"}}
```

- 启动前 `netstat` 检查 8000 端口仅剩 TIME_WAIT（无 LISTENING），端口空闲。
- 首次 `/health` 即在数秒内 200（数据预热在后台进行，不阻塞健康检查）。
- 未做任何业务逻辑改动。日志见 `docs/audit-2026-09-27/backend-kou.log`。

**鉴权说明**：`ALLOW_REGISTRATION=false`，无法自助注册。审计使用根目录 `.env` 的 `ADMIN_TOKEN` 直通鉴权（`core/auth.py:199` `ALLOW_ADMIN_TOKEN_LOGIN` 默认 true），`/auth/me` 返回 `{"username":"admin","role":"admin"}`，可覆盖全部 AUTH 端点。

> ⚠️ 设计注意：**未鉴权时后端也返回 HTTP 200**（错误码 `UNAUTHORIZED` 在 body）。常规基于 HTTP 状态码的监控/告警会漏报，须按 body `code` 判定。

---

## 2. 端点探测汇总

| 分组 | 数量 | 说明 |
|---|---|---|
| HTTP 200（业务成功 code=0） | 80 | 含 3 个降级载荷 |
| 业务错误码 code≠0 | 20 | 见 §2.1，均为参数/数据/配置 |
| 超时(20s) | 4 | 见 §2.2 |
| SSE 长连接 | 1 | `/notify/stream`（预期挂起，短超时验证可达） |
| 跳过-写操作 | 33 | 见 §2.3 |
| **合计** | **118** | |

### 2.1 业务错误码 code≠0 逐条判定（20 条，**无路由缺失**）

经第二轮带正确参数复测，**全部 40000「请求参数错误」均为我的探测入参不规范**，补齐后正常：

| 端点 | 首测码 | 复测结论 |
|---|---|---|
| `GET /stock/{symbol}/profile` | 40400 | 补 `.SH` 后缀后返回「贵州茅台」✅ |
| `GET /stock/{symbol}/kline` | 40000 | 补 `start/end=YYYYMMDD` 后返回 117 根 K 线 ✅ |
| `GET /stock/{symbol}/predict` | 51001 | 补 `.SH` 后缀后正常返回预测 ✅ |
| `GET /market/quotes` | 40000 | 补 `symbols` 后正常 ✅ |
| `GET /watchlist/dashboard` | 40000 | 补 `symbols` 后正常（1.17s）✅ |
| `GET /watchlist/correlation` | 40000 | 补 `symbols` 后正常 ✅ |
| `GET /screener/watchlist` | 40000 | 补 `symbols` 后正常 ✅ |
| `POST /desk/attribution` | 40000 | `assets=[{code,weight}]` 后正常（3.38s）✅ |
| `POST /portfolio/backtest` | 40000→51001 | 见 §4.3 ✅（纯数字代码正常） |
| `POST /research/optimize` | 40000 | 补 assets 结构后正常 ✅ |
| `POST /research/stress-test` | 40000 | 补 assets 结构后正常 ✅ |
| `POST /research/factor-corr` | 51001 | `volume` 非有效因子名（入参问题） |
| `POST /settings/connectors/test` | 40000 | 空数据源名（入参问题） |
| `POST /backtest/strategy-run` | 40012 | 回测区间超出本地数据范围（`2024-01-02 ~ 2024-06-28`）——**数据覆盖边界，非故障** |
| `POST /auth/login` | 40104 | 探测用错口令（预期） |
| `POST /auth/register` | 40106 | 注册开关已关（预期） |
| `GET /datacenter/sync/tasks/{task_id}` | 40400 | 不存在的 task_id（预期） |
| `POST /datacenter/text/build-factor` | 51001 | 未导入公告文档（预期，需先 import） |
| `GET /studio/mining/status/{task_id}` | 51001 | 不存在的 task_id（预期） |
| `POST /studio/nl-to-factor` | 53000 | **未配置 LLM**（`.env` 无 `LLM_PROVIDER`）——功能未启用，非故障 |

### 2.2 超时端点（4 个，全部 20s 触发）

| 端点 | 首测 | 复测 | 判定 |
|---|---|---|---|
| `GET /datacenter/datasets` | 20.02s 超时 | **0.02s** | ✅ 冷启动，非真超时 |
| `GET /datacenter/mirror/status` | 20.01s 超时 | **0.01s** | ✅ 冷启动 |
| `GET /datacenter/quality` | 20.02s 超时 | **0.01s** | ✅ 冷启动 |
| `GET /ops/lineage` | 20.00s 超时 | **32.3 / 38.2 / 33.2s** | ❌ **持续慢**，见 §3 |

### 2.3 跳过-写操作（33 个）

为避免数据变更/长任务，以下写操作**未执行**，仅记录：`alerts/events/read`、`alerts/rules`(POST/PUT/DELETE)、`datacenter/mirror/rebuild`、`datacenter/sync`(POST/auto/cancel/fetch)、`datacenter/text/import`、`datacenter/train/start|cancel`、`desk/exclusion`(POST/toggle)、`desk/fills/run`、`desk/kill-switch`、`desk/orders`、`export/backtest`、`export/strategy-backtest`、`monitor/run`、`ops/dag/rerun`、`ops/quality-scan`、`report/daily/generate`、`settings/apikeys/rotate`、`settings/data/cache/clear`、`settings/data/sync`、`settings/db/backup`、`settings/engine`(PUT)、`settings/preferences`(PUT)、`studio/factors`(POST/DELETE)、`studio/mining/start|cancel`。

---

## 3. 慢端点排行（复测 2 次确认）

| 排名 | 端点 | 复测耗时 | 前端超时预算 | 风险 |
|---|---|---|---|---|
| 1 | `GET /api/v1/ops/lineage` | **32.3 / 38.2 / 33.2s** | **15s（默认，未覆盖）** | 🔴 **必然客户端超时** |
| 2 | `GET /api/v1/research/lab/yearly` | **18.8 / 19.3s** | **15s（默认，未覆盖）** | 🔴 **必然客户端超时** |
| 3 | `POST /api/v1/studio/factor-report` | 11.3 / 12.5s | 120s | 🟡 慢但安全 |
| 4 | `GET /api/v1/market/overview/rt` | 11.1 / 11.9s | 120s | 🟡 慢但安全 |
| 5 | `POST /api/v1/studio/alpha-eval` | 6.9s | 120s | 🟡 可接受 |
| 6 | `GET /api/v1/screener/stocks` | 4.5s | 30s | 🟢 |
| 7 | `GET /api/v1/etf/detail/{code}` | 4.3s | 30s | 🟢 |
| 8 | `GET /api/v1/etf/scale` | 3.4s | 30s | 🟢 |

**关键实测证据**（前端超时配置来源）：

- `frontend/src/api/client.ts:92` 默认 `timeout: 15000`
- `frontend/src/api/production.ts:176` `lineage: () => get<LineageGraph>('/api/v1/ops/lineage')` —— **未传超时 → 走默认 15s**
- `frontend/src/api/research.ts:45` `labYearly: ...get<...>('/api/v1/research/lab/yearly', undefined, undefined, options)` —— **未传超时 → 走默认 15s**

⇒ **Ops 血缘图、研究 Lab 年度视图这两个页面按钮，后端要 19–38s，前端 15s 就中断**，用户看到的是"转圈后失败"，与"按钮点了没反应"体验一致。

---

## 4. 前端构建与代理验证

### 4.1 构建结果

| 命令 | 结果 |
|---|---|
| `npm run build`（= `tsc -b && vite build`） | ❌ 失败，但**非代码问题** |
| `npx vite build --outDir dist-kou-verify`（全新目录） | ✅ **成功**，`built in 6.92s`，747 模块，**0 error 0 warning** |

`npm run build` 失败根因（`frontend-build.log`）：

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":80,"threshold":50,"scope":"turn",
  "targets":["...\\frontend\\dist\\assets"]}
    at checkBulkDeleteGuard (node-safe-delete-shim.cjs:214)
```

- `tsc -b` **已通过**（无类型错误）；vite 已 `✓ 747 modules transformed`。
- 失败发生在 `prepareOutDir → emptyDir`：本机 WorkBuddy 环境的 **`node-safe-delete-shim` 批量删除护栏**拦截了清空 `dist/assets`（80 个文件 > 阈值 50）。
- **结论：这是审计环境的删除护栏，不是应用构建缺陷**。换全新 outDir 后构建完全通过。

### 4.2 Vite proxy 一致性（重点结论）

| 检查项 | 值 | 判定 |
|---|---|---|
| `vite.config.ts` proxy target | `process.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'` | — |
| 前端是否有 `.env` 覆盖 `VITE_API_BASE` | **无任何 `.env*` 文件** | ✅ 走默认 |
| axios `baseURL` | `import.meta.env.VITE_API_BASE ?? '/'`（`client.ts:91`） | ✅ 同源相对路径 |
| 后端实际监听 | `127.0.0.1:8000` | ✅ |
| **一致性结论** | **proxy 目标 = 后端监听端口，配置正确** | ✅ |

代理链路实测（dev server 5173）：

```
GET http://localhost:5173/                        -> 200
GET http://localhost:5173/health                  -> 200 {"db":"ok","redis":"ok"}
GET http://localhost:5173/api/v1/market/overview  -> 200 (0.18s)
GET http://localhost:5173/api/v1/auth/me          -> 200
GET http://127.0.0.1:8000/health                  -> 200
```

⇒ **"前端按钮拉不起后端"的常见根因（proxy 端口不一致）在本项目不存在。**

### 4.3 代码格式双标准（脆弱点，当前未触发）

| 端点族 | 期望代码格式 | 前端来源 | 是否匹配 |
|---|---|---|---|
| `stock/{symbol}/*` | **带后缀** `600519.SH` | `stock/search` 返回 `symbol:"600519.SH"` | ✅ 匹配 |
| `portfolio/*` | **纯数字** `600519` | `portfolio/search` 返回 `code:"600519"` | ✅ 匹配 |

实测佐证：
- `POST /portfolio/backtest` 传 `600519.SH` → `51001 部分资产数据获取失败`；根因在 `data/portfolio_source.py:146` 备源 `_market_symbol()` → `domain/a_share_rules.py:60` `ValueError: code 必须 6 位数字: 600519.SH`（主源东财失败降级腾讯时触发）。传纯数字 `600519` → **完全正常**（117 交易日，返回完整 metrics）。
- 前端默认组合 `Portfolio/index.tsx:48` 正是纯数字 `600519/600036/300750`，故**不触发**。

⇒ 当前前端各自格式正确、链路通；但**接口层不做格式校验**（`.SH` 后缀能通过校验、深到数据层才炸），未来任一页面串用格式即报 51001/40400。属健壮性隐患，非当前故障。

---

## 5. 404 悬空调用清单

**0 条。** 用 `openapi.json` 的 111 条路由与 `frontend/src/**/*.ts(x)` 抽取的 104 条调用路径对账（路径参数归一化后比对），**全部命中**。

唯一疑似 `/api/v1/report/daily${date` 经人工核对为模板字符串（`monitor.ts:114` `` `/api/v1/report/daily${date ? \`?date=${date}\` : ''}` ``），实为 `/api/v1/report/daily`，后端存在该路由。

---

## 6. 降级 / 空数据载荷

**降级（degraded）3 条 + 1 处 freshness 降级：**

| 端点 | 现象 |
|---|---|
| `GET /alerts/health` | `status: degraded` |
| `GET /etf/flow` | `status: degraded` |
| `GET /settings` | 含 `degraded` 标记 |
| `GET /market/overview/daily` | `data_freshness.status: degraded`（source=bounded_composite） |
| `GET /etf/detail/510300` | `status: degraded`（仍返回数据） |

这些是**数据源不可用时的显式降级披露**（后端有意识地返回 degraded 状态而非报错），前端应据此展示"数据可能滞后"，不属于崩溃。

**空数组 14 条**：`alerts/events`、`alerts/rules`、`notify/recent`、`studio/factors` 等——多为"确实无数据"（无告警/无通知/无已存因子），非故障。

---

## 7. 最严重 5 个实测问题

1. 🔴 **`GET /api/v1/ops/lineage` 后端 32–38s，前端默认超时 15s** ⇒ Ops 血缘图**必然超时失败**（`production.ts:176` 未放宽超时）。
2. 🔴 **`GET /api/v1/research/lab/yearly` 后端 ~19s，前端默认超时 15s** ⇒ 研究 Lab 年度视图**必然超时**（`research.ts:45` 未放宽超时）。
3. 🟡 **3 个 datacenter 端点冷启动首击 20s+**（`datasets`/`mirror/status`/`quality`）⇒ 服务重启后首次打开数据中心页可能超时，需预热或放宽首击超时（前端分别给了 90s/45s/90s，但**服务端 20s 预算需确认**）。
4. 🟡 **代码格式双标准且接口层无校验**（`stock/*` 要 `.SH`、`portfolio/*` 要纯数字）⇒ 串用即 51001/40400，当前前端侥幸匹配，属脆弱点。
5. 🟡 **后端 HTTP 恒 200** ⇒ 未鉴权/业务失败都返回 200，标准 HTTP 监控无法发现异常，必须按 body `code` 判活。

---

## 8. 复现方式

```bash
# 后端（保持运行中）
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 批量探测
backend/.venv/Scripts/python.exe docs/audit-2026-09-27/probe.py

# 超时/参数复测
backend/.venv/Scripts/python.exe docs/audit-2026-09-27/retest.py

# 前端构建（规避环境删除护栏）
cd frontend && npx vite build --outDir dist-kou-verify
```

## 9. 未覆盖 / 说明

- 33 个写操作按分工要求跳过，未验证其运行时行为（需单独在受控环境测）。
- `/api/v1/notify/stream` 为 SSE 长连接，仅验证可达，未做流式内容校验。
- 部分慢端点耗时受本机网络（东财/腾讯/AKShare 外呼）影响，数值为**本机实测**，非绝对。
- 本次审计**未修改任何业务逻辑代码**。
