# Alpha Quant Platform 前后端可用性审计报告

**审计日期**：2026-09-27
**审计范围**：后端 114 个 API 端点 + 前端 19 个页面 / 109 个接口调用
**参与角色**：QA 工程师（前端静态审计 ×2 独立复核）、架构师（后端架构与超时治理）、工程师（运行时实测）
**审计性质**：只读审计 + 运行时探测，未修改任何业务代码

---

## 一、TL;DR

**项目整体可用，无阻断性缺陷（P0 功能缺失 = 0）。** 前端构建、后端启动、接口连通性均正常。

真正让用户"觉得功能坏了"的不是接口不通，而是两类问题：

1. **超时预算不匹配** —— 后端部分端点耗时超过前端 15s 默认超时，前端先断、后端还在跑，表现为"点了没反应"
2. **错误态缺失** —— 接口失败时前端静默吞掉异常，页面显示空白 / "—" / 永久"加载中"，而不是报错

另外发现 **6 个"死端点"**（后端已实现但界面无入口），属于"功能做了但用不上"。

---

## 二、总体结论

| 维度 | 结果 | 说明 |
|---|---|---|
| 后端启动 | ✅ 正常 | uvicorn 监听 127.0.0.1:8000，`/` 探测 200 |
| 前端构建 | ✅ 通过（有环境注意事项） | `tsc -b` 0 错误；`vite build` 成功（747 模块 / 6.92s），产物 **0 error 0 warning** |
| 前端 dev server | ✅ 正常 | Vite 5173 就绪 |
| Vite 代理配置 | ✅ 一致 | `/api`、`/health` → `http://127.0.0.1:8000`，与后端实际端口匹配 |
| 接口对齐率 | ✅ 100% | 前端 109 个调用全部命中后端路由，**悬空调用 0** |
| 实测端点 | 118 个 | 200 = 80；写操作按约定跳过 = 33；超时 = 4；SSE = 1 |
| 死端点 | 6 个 | 后端有、前端无入口 |
| 问题总数（本汇总口径） | **P0=0 / P1=6 / P2=13** | 无阻断性功能缺失 |

> ⚠️ **本地构建注意事项**：直接执行项目默认的 `npm run build`（输出到 `dist/`）在**本机**会失败 ——
> 原因是构建前清理 `dist/` 目录的动作被本机的 `node-safe-delete` 护栏拦截（属环境安全策略，非代码缺陷）。
> 验证方式：改用 `--outDir dist-kou-verify` 后 **0 error 0 warning 构建成功**。
> 若在 CI 或其他机器上构建则不受此影响；本机建议换输出目录或为 `dist/` 配置护栏白名单。

### 关键判定：不存在"按钮点了完全没反应（404）"的情况

用户最担心的"前端有按钮却拉不起后端"——**经静态核对 + 运行实测双重验证，前端 109 个调用全部命中后端路由，实测无 404。**
（早期探测脚本曾报大量 4xx，经复核为占位参数导致的假阳性，已排除。）

用户感知到的"没反应"，实质是**超时截断 + 错误被静默吞掉**造成的假象，而非接口不存在。

---

## 三、实测数据（运行时证据）

### 端点探测汇总（118 个）

| 分类 | 数量 | 说明 |
|---|---|---|
| HTTP 200 | 80 | 全部读端点正常响应 |
| 未测（写操作） | 33 | POST/PUT/DELETE，按约定跳过以避免触发任务或数据变更 |
| 超时（20s 截断） | 4 | 见下表 |
| SSE 流 | 1 | `/api/v1/notify/stream`，3s 超时属预期行为 |

### 慢端点排行（实测耗时）

| 耗时 | 端点 | 状态 | 前端超时预算 | 判定 |
|---|---|---|---|---|
| **32–38s** | `/api/v1/ops/lineage` | 探测截断 | 15s（默认） | ❌ **必然超时**（真慢，非冷启动） |
| **~19.6s** | `/api/v1/research/lab/yearly` | 200 | 15s（默认） | ❌ **必然超时** |
| 11.94s | `/api/v1/studio/factor-report` | 200 | 120s | ✅ 已放宽 |
| 11.74s | `/api/v1/market/overview/rt` | 200 | 15s | ⚠️ **竞态**（日志曾达 15.0s 用满预算） |
| 6.88s | `/api/v1/studio/alpha-eval` | 200 | 120s | ✅ 已放宽 |
| 20s+ | `/api/v1/datacenter/datasets` | 探测截断 | 90s | ℹ️ 冷启动首击，**复测 0.01–0.02s** |
| 20s+ | `/api/v1/datacenter/mirror/status` | 探测截断 | 45s | ℹ️ 冷启动首击，复测 0.01–0.02s |
| 20s+ | `/api/v1/datacenter/quality` | 探测截断 | 90s | ℹ️ 冷启动首击，复测 0.01–0.02s |

> **重要修正**：3 个 datacenter 端点的 20s+ 是**冷启动首击**导致，复测仅需 0.01–0.02s，
> 不构成稳定性问题（首次访问体验不佳，建议预热）。真正持续慢的只有 `/ops/lineage` 与 `/research/lab/yearly`。
>
> 口径说明：冷路径单次实测，探测端 20s 截断；`/ops/lineage` 的 32–38s 为多轮复测确认值。

### 业务码异常（20 条）

实测中业务码 ≠ 0 共 20 条，**全部由参数 / 数据 / 配置引起，无一是路由缺失**；带正确参数复测后均正常。

### 降级载荷（3 处，属正常披露非崩溃）

`/api/v1/alerts/health`、`/api/v1/etf/flow`、`/api/v1/settings` —— 均为外部数据源不可用时的显式降级，非程序崩溃。

---

## 四、问题清单

### P1（严重，5 项）

| # | 问题 | 位置 | 现象与影响 |
|---|---|---|---|
| P1-1 | 公开首页无任何错误态 | `pages/MarketOverview/index.tsx:39-124` | rt/daily 双失败时只显示 "—" 和空图，无提示无重试，用户以为页面坏了 |
| P1-2 | 公开页顶栏搜索必然 401 且被静默吞 | `components/Topbar.tsx:145-148` | 公开路由未包鉴权，但搜索打 `/stock/search`、`/etf/list`（均需 viewer），未登录必然失败，`.catch(()=>[])` 吞掉 → **搜索框"点了没反应"** |
| P1-3 | 预警规则无法编辑/启停 | `api/alerts.ts:64` | 后端 `PUT /alerts/rules/{id}` 成死端点，要改规则只能删除重建 |
| P1-4 | 数据血缘图永久"加载中" | 前端 `pages/DataQuality/index.tsx:116,224`<br>根因 `backend/app/api/v1/ops.py:306` | 前端：失败时 `setGraph(null)` 与"加载中"共用 UI 分支 → 永远显示"加载血缘…"。<br>后端根因：每次请求**全量重扫 parquet footer**，实测 32.3 / 38.2 / 33.2s，且 `scan_cache` **仅单请求内有效**，跨请求不复用 → 无缓存无预算，属本次审计**最严重的后端性能缺陷** |
| P1-5 | 研究实验年度收益必然超时 | `api/research.ts:45` → `pages/Research/index.tsx:177` | 后端实测 19.57s（复测 18.8 / 19.3s），前端未传 timeout 落回默认 15s → 必超时；且与轻量请求共用 catch，会误点亮整页错误条 |
| P1-6 | 组合回测备源路径崩溃（**新增**） | `portfolio_source.py:146` → `a_share_rules.py:59-60`，被 `portfolio.py:126` 误转 | `api/v1/portfolio.py:126` 用**同一个 `except ValueError`** 把两类原因合并进 `51001`（ERR_DATA_EMPTY）：① 入参代码格式错（`a_share_rules.py:59-60`，本应 `40000` ERR_PARAMS）、② 真·本地无数据（`domain/portfolio.py:279`，才应是 `51001`）。→ 前端无法区分"用户填错"还是"数据没同步"，误导排查；接口层无入参格式归一化 |

### P2（一般，13 项，摘要）

- 4 个长耗时接口沿用 15s 默认：`mirror/rebuild`、`text/build-factor`、`desk/fills/run`、`/screener` —— 后端任务仍在跑，易诱发重复点击
- `/desk/orders` 响应缺 `total`/`truncated` 字段
- 其余为超时预算与错误提示的细节问题

> 已撤回：此前将 `/datacenter/mirror/status` 列为"45s 预算偏紧"，经复测确认其 20s 首测为**冷启动假阳**（复测 0.01–0.02s），前端 45s 预算充裕，不再列为问题。

### 6 个死端点（后端已实现，前端无入口）

```
GET   /api/v1/alerts/health
PUT   /api/v1/alerts/rules/{rule_id}
POST  /api/v1/settings/apikeys/rotate
GET   /api/v1/datacenter/sync/tasks/{task_id}
GET   /api/v1/market/overview
GET   /api/v1/market/quotes
```

---

## 五、超时治理专项

### 超时配置不一致（5 处，架构师认定）

| # | 问题 | 影响 |
|---|---|---|
| ① | 前端 `client.ts` 默认 15s == 后端 `market/overview/rt` 15s 预算 | **竞态**：后端用满预算时前端同时超时 |
| ② | LLM 类端点后端 60s vs 前端 15s | 必然截断，后端线程继续空转 |
| ③ | 无全局请求超时中间件 | 26 个重计算端点无服务端预算兜底 |
| ④ | `overview/daily` 预算硬编码 5.0s 未常量化 | 难维护 |
| ⑤ | 外部 HTTP timeout 分 5 档（3/4/8/10/60s） | 不统一 |

### 必然前端超时（2 个，实测确认）

1. `/api/v1/ops/lineage` —— 后端 >20s，前端 15s 默认
2. `/api/v1/research/lab/yearly` —— 后端 19.57s，前端 15s 默认

---

## 六、其他风险

- **磁盘水位 93.3%**（`backend-run.log` 告警）：接近写满将导致抓取/落库失败，写入型任务有失败风险，建议尽快清理
- **外部数据源不稳定**：东财 ETF 目录持续不可达（日志 28 次，已走新浪/腾讯兜底）；跨境 ETF（美股）行情源拉取失败降级为 unavailable
- **`overview/daily` 降级载荷缺顶层 `status`**：会被缓存最长 3 天不自愈
- **RBAC_ENFORCE 默认 False**：`require_role` 实际仅等价"需登录"，角色未真正生效
- **代码格式存在双标准（已有实锤缺陷，见 P1-6）**：`/stock/*` 要求带交易所后缀（如 `600519.SH`），`/portfolio/*` 要求纯数字代码（如 `600519`），串用即报 `51001` / `40400`。**当前前端恰好各自匹配正确**，但这是隐式约定。更严重的是：东财源降级到腾讯备源时，带后缀代码会抛 `ValueError` 并被误报为"数据为空"（P1-6），说明**格式校验缺失已不只是约定问题，而是会产生误导性错误的真实缺陷**
- **后端 HTTP 恒返回 200，错误码藏在响应体**：所有异常（含 51001/40400/40104）HTTP 状态都是 200，错误在 body 的 bizcode 里。这意味着**标准 HTTP 监控 / 网关告警会完全漏报业务错误**，需改用 body 内 bizcode 做监控口径

---

## 七、本次审计的覆盖缺口（需如实说明）

**首轮探测的局限（已标注，勿直接引用首轮结论）：**

- 33 个写操作端点（POST/PUT/DELETE）首轮按约定跳过，未实测。
- 首轮 20 条业务码异常中，有一部分源于**探测脚本自身的占位参数**（如 `{rule_id}`、`{factor_id}` 未替换），非后端真实缺陷。

**V2 复核已补上的证据（以此为准）：**

- 用真实参数重测 33 个写端点 + 20 条业务码异常 → **业务码 ≠ 0 仅剩 4 条**，且全部为真实参数/数据/配置问题，无一为路由缺失。
- 重测得 16 个新增慢端点实测值；`/ops/lineage`、`/research/lab/yearly` 均做了**多轮复测**（32.3/38.2/33.2s；18.8/19.3s），确认为持续慢而非冷启动。
- 因此"26 个无预算重计算端点"已从静态识别升级为部分实测，但**仍有部分重计算端点未做多轮复测**，其稳定耗时待确认。

---

## 八、修复建议（按优先级）

### 立即可做（低成本、高收益）

1. **P1-5 / P1-4 超时实参**：`api/research.ts:45` 补 `timeout=120`；`api/production.ts:176`（`/ops/lineage`）补 `timeout=60`
2. **P1-1 / P1-4 错误态**：MarketOverview 与 DataQuality 补 error 三态，替换静默 `.catch(()=>null/[])`
3. **P1-2 搜索鉴权**：公开页顶栏搜索改为提示登录，或后端对 viewer 端点降级为公开只读

### 中期（架构改进）

4. **`/ops/lineage` 根治（最高优先级）**：`ops.py:306` 的每次请求全量重扫 parquet footer，
   且 `scan_cache` 仅单请求内有效 → 改为**跨请求持久化缓存 + 后台重建**。
   正确做法是复用仓内既有的 **`cache.swr.cached_or_build`**（stale-while-revalidate）：
   命中直接返回、主键过期回旧值并后台无预算重建，请求路径永不扛冷扫；Redis 不可用时自动降级进程 LRU。
   > ⚠️ **勘误**：本报告前一版曾写"复用 `dag.py:307` 已有的持久化缓存实现"——**`dag.py` 在全仓并不存在**，
   > 该引用源自架构师报告的错误指引，已在实施阶段被核实更正。真正的现成机制是 `cache.swr.cached_or_build`。
5. **P1-6 组合回测**：`portfolio.py:126` 拆分 `except ValueError` —— 入参格式错返回 `40000`
   （ERR_PARAMS），真·无数据才返回 `51001`；并在接口层对入参做代码格式归一化（兼容 `.SH`/纯数字）。
6. 前端默认超时按端点分级（轻量 15s / 重计算 120s），消除"一刀切 15s"
7. 后端补全局请求超时中间件，为无预算的重计算端点设兜底预算
8. `overview/daily` 降级载荷补顶层 `status`，避免降级态被长期缓存

### 运维

9. 清理磁盘（当前 93.3%）
10. 为 6 个死端点补前端入口，或明确下线

---

## 九、产出文件

| 文件 | 内容 |
|---|---|
| `backend-architecture-audit.md` | 后端架构审计（114 端点基准表、慢端点、超时配置全景、P0/P1/P2 建议） |
| `frontend-qa-audit.md` | 前端审计（109 调用对齐矩阵、13 问题、附录 A 二次复核、附录 B 实测交叉校验） |
| `smoke-results.csv` | 118 端点首轮实测原始数据（状态码/耗时/响应摘要） |
| `smoke-results-v2.csv` | **V2 复核实测数据**（真实参数重测，含 16 个新增慢端点 + 多轮复测值）—— **引用实测数据以此为准** |
| `backend-routes-baseline.csv` | 114 条后端路由完整路径基线 |
| `openapi.json` | 运行时导出的 OpenAPI  schema |
| `probe.log` / `retest.log` / `backend-kou.log` | 探测与后端运行日志 |
| `regression-verification.md` | 修复后回归验证（8 项逐项 PASS/FAIL + 浏览器真实渲染验证） |
| `smoke-write-endpoints.csv` | 33 个写操作端点补测数据（含副作用复核） |

---

## 十、修复与验证结果（同日完成）

用户确认后已按优先级修复 5 项 P1，并经 QA **独立复测**（不采信工程师自报数据）。

| # | 修复项 | 验证结果 |
|---|---|---|
| 1 | `/ops/lineage` 跨请求持久化缓存 | ✅ PASS |
| 2 | 组合回测错误码拆分 + 入参归一化 | ✅ PASS |
| 3 | `labYearly` timeout=120s + 独立错误态 | ✅ PASS |
| 4 | `/ops/lineage` 前端 timeout=60s + 三态 | ✅ PASS |
| 5 | 首页双失败提示 + 重试 | ✅ PASS |
| 6 | 顶栏搜索未登录不发请求 + allSettled | ✅ PASS |
| 7 | 后端缓存实测 | ✅ PASS |
| 8 | 回测错误码实测 | ✅ PASS |

### `/ops/lineage` 性能（QA 独立复测，非工程师自报）

| 场景 | 耗时 |
|---|---|
| 修复前基线 | 32.3 / 38.2 / 33.2 **s** |
| 修复后稳态（6 轮） | 12.6 / 13.4 / 13.5 / 25.3 / 30.6 / 40.6 **ms**（avg 22.7ms） |
| 冷路径 `_compute_lineage()` | 33.68 s（原问题真实存在） |
| SWR stale 命中 | 39.4 ms 返回旧值 + 后台重建 40s 自愈 |
| Redis 不可用降级 | 首请求 32.66s → 后续 0.04 / 0.01 s（进程 LRU 兜底生效） |

**约 1300 倍提升**，且降级路径已验证可用。

### 质量门禁

- 后端全量测试：**1800 passed / 8 skipped**（8m05s），无回归
- `tsc -b --force` EXIT=0；`vite build`（新 outDir）EXIT=0
- **Chromium 真实渲染验证 7/7 PASS**：未登录搜索 0 请求 + 登录引导、已登录搜索正常出结果、
  血缘图成功渲染、首页正常路径 0 误报

### 写操作端点补测（33 个）

| 分类 | 数量 | 说明 |
|---|---|---|
| 已实测 | 14 | 0 个真实超时（最慢 `export/backtest` 1.33s） |
| 参数校验型探测 | 14 | 用非法参数触发校验分支，不真正执行 |
| 跳过 | 5 | `fills/run`、`monitor/run`、`cache/clear`、`data/sync`、`db/backup`（无请求体，无法安全探测） |

**副作用复核：PRE == POST，全程无数据变更。**

### 剩余盲区与遗留问题

1. **重计算写端点真实执行仍是盲区** —— `desk/fills/run`、`monitor/run`、`ops/dag/rerun`
   的真实执行耗时无法安全探测，仍无实测数据
2. `portfolio.py` 兜底 `except Exception` 仍把系统级异常映射成"数据为空"（既有行为，
   改动会牵动测试允许码集合，**待用户裁决**是否处理）
3. 后端 `51001` 文案含 `IndexError`，被前端 sanitize 冲淡成通用提示
4. `train/cancel` 空闲时仍返回 `cancel_requested: true`，语义欠妥
5. ~~P1-3 预警规则编辑未做~~ → **已于同日追加修复，见第十一章**

---

## 十一、追加修复（用户确认后完成）

用户裁决后，又修复了此前标记为"待裁决/待排期"的两项。

### 11.1 组合回测错误码语义根治（后端）

原问题：系统级异常被粉饰成"数据为空"（51001），真实 Bug 无法暴露。修复分两层：

**API 层** `backend/app/api/v1/portfolio.py` L151-161
- 兜底 `except Exception` 错误码 `ERR_DATA_EMPTY`(51001) → **`ERR_SYSTEM`(50000)**
- 对外文案改为固定「回测执行失败，请稍后重试或联系管理员」，**不再透传 `type(e).__name__`**
  （已确认 `AQPException.message` 会原样进入响应体，属内部细节泄露）
- trace_id 由响应信封自动携带，便于排查；对内 `logger.exception` 保留完整堆栈

**Domain 层** `backend/app/domain/portfolio.py` L247 附近（下层同类反模式）
- 原 `except Exception` 兜住一切 → 收敛成「部分资产数据获取失败: <类名>」→ 51001
- 改为显式捕获 `DataSourceUnavailable / IndexError / KeyError / OSError` 四类
- 对外文案 `<code>: 无数据`（去掉类名），类名只进 `logger.warning`
- 未命中的异常即真实缺陷，上抛由 API 层兜底归 50000

**`IndexError` 真凶定位**：第三方 **akshare** `index_stock_zh.py:346` 的 `get_tx_start_year`
对腾讯返回的**空 `data` 列表**取 `[0]`。触发链：东财查不到 `999999` → 降级腾讯备源
→ akshare 裸抛 `IndexError`。判定为**预期内无数据**（源站无此标的），非我方代码 Bug，
但第三方未做空列表守卫。

**意外发现**：**基准取数原先完全没有守卫**，失败会逃逸成 50000（表现为"系统错误"），
现已纳入同一聚合归 51001。

**修复后的语义不变量（关键）**

| 场景 | 错误码 |
|---|---|
| 正常 | `0` |
| 入参格式非法 / 权重和≠1 | `40000` ERR_PARAMS |
| 源站无此标的（预期内无数据） | `51001` ERR_DATA_EMPTY（message 不含内部类名） |
| **真·代码 Bug（如 AttributeError）** | **`50000` ERR_SYSTEM** ← 不再被粉饰 |

最后一行是本次修复的核心价值：**让真实故障暴露出来**。

### 11.2 预警规则编辑 / 启停（前端）

背景：后端 `PUT /api/v1/alerts/rules/{id}` 早已实现且有测试覆盖
（`test_alerts_rule_crud` 建→查→改→删），前端 `api/alerts.ts` 也已封装 `updateRule`，
但**页面从未调用** → 死端点。故本次只需补 UI，工作量低于审计时的预估。

改动仅 `frontend/src/pages/Alerts/index.tsx`（+235 / -87）：
- **复用**：新建表单抽成 `RuleForm` 组件，新建/编辑共用（含 `ParamsForm` 白名单），
  避免两套表单逻辑漂移
- **编辑**：列表每行新增「编辑」按钮，展开预填全部字段，保存调 `updateRule`，
  成功刷新并关闭，失败在表单内红条提示
- **启停**：新增「状态」列 `role=switch` 开关，乐观更新，请求期加锁防连点，
  失败回滚 + 顶部横幅提示
- **三态**：补 loading 态；加载失败显示 `ErrorState` + 重试（**原实现失败会永久转圈**）；
  页面已无静默 catch

工程师自证：`tsc -b --force` EXIT=0；`vite build`（新 outDir）EXIT=0；
**Chromium 真实渲染 + 路由桩 28/28 断言通过**（预填、PUT 字段完整回传、列表刷新、
启停翻转、失败错误可见 + UI 回滚）。

### 独立回归验证结果（第二轮）

QA 独立复测（不采信工程师自报数据），**A / B 两项均 PASS**，详见 `regression-verification-round2.md`。

**A 项实测错误码（真实 HTTP）**

| 入参 | 错误码 | 说明 |
|---|---|---|
| `600519.SH` / `600519` | `0` | 归一化后正常执行 ✅ |
| `999999` / `999999.SH` | `51001` | message =「部分资产数据获取失败: 999999: 无数据」，**无类名** ✅ |
| `600519.SH.XX` | `40000` | 格式非法 ✅ |
| 权重 0.5 / 1.5 / 重复代码 / 空列表 / 日期格式错 | `40000` | 参数语义错 ✅ |
| **未知基准 `999999`** | `51001` | **新守卫端到端生效**（修复前会逃逸成 50000）✅ |

**关键不变量已确认**：

| 注入异常 | 错误码 |
|---|---|
| `AttributeError` / `TypeError`（真·代码 Bug） | **50000** ✅ |
| `IndexError` / `KeyError`（源站无数据） | 51001，无类名 ✅ |

真实堆栈已入 `app.log` / `app.json.log`，对外文案固定不泄露内部细节。
`git diff` 确认**本轮未修改任何测试文件 → 无放宽断言**。

**B 项 Chromium 真实浏览器验证：7 场景 ALL PASS** —— 编辑预填正确、PUT 字段完整回传、
启停成功/失败回滚 + 可见提示、加载失败显示 ErrorState + 重试、**新建流程未被共用组件破坏**。
`tsc -b --force` 与 `vite build`（新 outDir）均 EXIT=0。

> 测试口径说明：工程师报 156 passed、QA 报 121 passed，系挑选的测试集范围不同，
> 两边均为 **0 failed**，不影响结论。

### 验证中发现的新问题（已派修）

| # | 问题 | 严重度 | 说明 |
|---|---|---|---|
| 1 | `start_date > end_date` **误报 50000** | P2 | 与本轮同族：用户填反日期被粉饰成"系统异常"。根因是自定义 validator 抛异常后 `APIResponse` 序列化失败 → 落全局兜底，且产生**假 ERROR 告警**。全仓仅此 1 处 |
| 2 | 编辑期间切换同规则开关被陈旧 `enabled` 覆盖 | P3 | 本轮新引入的陈旧状态竞态 |
| 3 | 事件卡片加载失败**永久转圈** | P3 | 预存在，漏了事件列表的 error 分支 |

### 11.3 验证后追加修复

**P2 — `start_date > end_date` 误报 50000（根因在通用层，非端点）**

根因链（比 QA 初判更深）：
1. `backend/app/api/v1/portfolio.py:57` 自定义 validator 抛 `ValueError`
2. pydantic 把它存进 `ctx["error"]`
3. `backend/app/core/errors.py:196` 的 `_validation` 把 `e.errors()` 塞进 `APIResponse` 再 `jsonable_encoder`
4. pydantic `model_dump(mode="json")` **无法序列化异常对象** → `PydanticSerializationError`
5. **校验处理器自身崩溃** → 全局兜底 → 50000 + 假 ERROR 告警

修复：`core/errors.py` 新增 `_jsonable_validation_errors()`，把 ctx 内的**异常对象转为字符串**
（其余字段原样保留），`_validation` 改用它，日志维持 WARNING 级。

> ⚠️ 该修复**动到了通用层**（所有端点校验错误路径的公共入口），已安排 QA 做定向影响面验证
> （复核全仓自定义 validator 数量、跨模块回归、响应体结构是否变形），结论待出。
> 工程师已 grep 确认全仓自定义 validator 仅 `portfolio.py:57` 一处，183 passed。

**P3 × 2 — Alerts 页面**
- **陈旧 `enabled` 竞态**：表单不再持有 `enabled`（提交类型改为 `Omit<AlertRulePayload,'enabled'>`），
  父级在**提交瞬间**从 `rules` 取最新值合并。`rules` 是唯一权威源（开关即时乐观更新、失败回滚）。
  选此方案而非"保存前再 GET"：少一次往返，且**不存在二次竞态窗口**；保存后 `load()` 仍以服务端状态刷新兜底
- **事件卡片永久转圈**：补三态 + `ErrorState` 重试，与规则列表同口径
- 轮询处的静默 catch **有意保留**并加注释：30s 后台刷新弹错会持续打断用户、下轮自愈，
  且首屏失败已由 ErrorState 明确暴露

自证：Chromium 实测 13/13 PASS（编辑期间切换开关再保存不被回退；事件加载失败显示错误+重试而非转圈）。

### 11.4 通用层影响面验证结论：保留，不回退

QA 对 `core/errors.py` 通用层改动做了定向影响面验证：

- **影响面复核成立**：全仓 `field_validator` / `model_validator` / `@validator` / `root_validator`
  **仅 `portfolio.py:57` 一处**；唯一的 `RequestValidationError` 处理器与唯一的 `.errors()` 调用
  均在 `errors.py`，改动确为全局单一入口
- **跨模块回归**：9 模块 14 例（portfolio×3、alerts×2、etf×3、market、screener、datacenter、
  notify、research、desk）**全部 40000**，响应体结构无变形
- **无副作用**：探针期间 `PydanticSerializationError = 0`、`unhandled error = 0`、validation WARNING = 14
- **目标场景**：`start_date > end_date` → **40000**（原 50000），ctx.error 已转字符串，仅 WARNING 无假告警
- **代码审查**：仅把 ctx 内的 `BaseException` 转为 `str`，`list/dict/None/float/str` 及
  `type/loc/msg/input` 全部原样，**无误伤**；只做变换不吞错；反证未转换时仍抛序列化异常，确认修的是真根因
- **全量 pytest：1800 passed / 8 skipped / 0 failed**

**结论：保留，不回退。**

### 遗留待决策

**✅ 已修复，但需更正一处描述（见下）**

> ### ⚠️ 勘误：所谓「40951」并不存在
>
> 本章此前（及 `backend-architecture-audit.md` 附-2）曾记载"`/ops/lineage` 冷启动返回
> `ERR_UNIFIED_TASK_CONFLICT`(40951)，8s 内 4 次并发全部命中"。**该描述经查证为错误**：
>
> - 全仓搜索 `40951` / `ERR_UNIFIED_TASK_CONFLICT` / `TASK_CONFLICT` / `unified` —— **0 命中**
>   （除审计文本自身）；`git log --all -S` 全历史搜索亦 0 命中
> - 实测复现（冷缓存 + 4 路并发）：**4/4 全部 HTTP 200 code=0**，无一例 40951
> - 早期取证（`smoke-results.csv`、`probe.log`、`retest.log`）中该端点记录过的失败
>   **只有客户端 20s 超时**，从未出现 40951
>
> 错误来源是主理人对未充分证实信息的转述。完整证据见
> `docs/audit-2026-09-27/lineage-40951-evidence.md`。

**真实问题：冷启动惊群（thundering herd）** —— 不是冲突码，而是并发放大：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 冷启动 4 路并发 | 60.89 ~ 63.05s（**> 前端 60s 预算，实际全部超时**） | **28.70s** |
| 单请求冷扫 | 29.34s | 29.34s（未变） |
| 稳态（6 轮） | 12.6 ~ 40.6ms | **8.1 ~ 17.3ms（avg 10.3ms）** |

4 路并发时磁盘争用把单次 29.34s 的冷扫劣化到 60s+，且**重复 4 倍 IO**。

**修复**：`cache/swr.py` 新增**可选** `single_flight`（默认 False，其他调用方零影响）——
同 key 只创建一个重建 Task，其余 `await asyncio.shield(task)` 共享结果；回写在任务内完成
（发起方断连不丢冷扫成果）。`ops.py` 端点与 `warm_lineage_cache()` 均置 True。
monkeypatch 计数验证：预热进行中发请求，`_compute_lineage` **真实调用次数 = 1**。

> 未采纳「冷启动无 stale 返回 202」方案：前端 `get<LineageGraph>` 会把非 0 code 当 `ApiError`，
> 202 只会把"冲突"换成"报错"反而劣化。single-flight 让冷启动直接返回 200 真实数据，是更稳的等价解。

全量 pytest **1804 passed / 8 skipped / 0 failed**（基线 1801 + 新增 3 个 single-flight 锚点）。

**✅ 回归锚点已补（已完成）** —— `backend/tests/test_portfolio.py::
test_backtest_endpoint_reversed_dates_return_40000`（未新建文件、未改既有断言）。

断言内容：`start_date > end_date` → HTTP 200 且 `code == ERR_PARAMS(40000)`、`!= 50000`；
响应体能正常 `json()`（即无 `PydanticSerializationError`）；`ctx.error` 已降级为字符串；
ERROR 级 sink 未捕获 "unhandled error"（参数错只应 WARNING）。

**变异验证（关键）**：临时回退 `core/errors.py` 的修复 → 该用例**立刻 FAILED**，
复现 `PydanticSerializationError` + ERROR 假告警 —— 证明锚点确实能守卫，而非摆设。

全量：**1801 passed / 8 skipped / 0 failed**（原 1800 + 新增 1）。

---

## 十二、收尾项处置（用户要求"剩下三件一起做"）

### 12.1 死端点处置：3 补 UI / 2 建议下线

| # | 端点 | 结论 | 依据 |
|---|---|---|---|
| 1 | `GET /alerts/health` | **补 UI** | 独立价值高：股票池快照缺失时 Top-K 迁移规则会**静默跳过**（既不触发也不报错），此端点是唯一可见入口 |
| 2 | `POST /settings/apikeys/rotate` | **建议下线** | 后端**故意恒返回失败**（ERR_PARAMS「API Key 功能未启用」），是兼容桩；补 UI 只会让用户看到莫名报错，误导性更强 |
| 3 | `GET /datacenter/sync/tasks/{id}` | **补 UI** | 后端 docstring 明确"供页面刷新后恢复查看"；前端原只用内存态 `/sync/status`，刷新即丢，`startSync` 拿到的 task_id 被丢弃 |
| 4 | `GET /market/overview` | **建议下线** | 功能被前端已在用的 `/overview/rt` + `/overview/daily` **完全覆盖**，且 6s 预算下冷路径必然返回 degraded |
| 5 | `GET /market/quotes` | **补 UI** | **真实业务缺口**：个股详情页「现价」取的是**日线收盘价**，盘中是过期数据 |

> 第 5 项性质特殊——它不是"死端点"，而是**用错了数据源**：盘中把收盘价当现价展示。
> 这比单纯补入口更有价值。

**实施（5 个前端文件，未动 backend）**
- `api/alerts.ts` + Alerts 页：顶部健康芯片（`checked_at` 为空显示「未评估」而非假装「正常」）
  + 降级红色横幅 + 不可读琥珀横幅含重试，与 30s 轮询同频
- `api/datacenter.ts` + DataCenter：task_id 存 localStorage，「最近任务」行 + 详情弹窗
  （状态/进度/时间/错误/result JSON，含 loading/error/空态；查询失败不误显示成"无此任务"）
- StockDetail：`LiveQuoteChip` 盘中 15s 轮询（同 `QUOTES_TTL`）、休市不轮询，
  **降级/失败/空态均显式可见，不用日线价冒充实时价**

**实测（后端 8000）**：`alerts/health` code=0；`market/quotes` code=0 source=tencent 真实价；
`sync/tasks/{真实id}` code=0（status=cancelled, 983/2499）；不存在 id → 40400 走错误态；
`apikeys/rotate` code=40000 **印证禁用桩判断**。

自证：`tsc -b --force` EXIT=0；`vite build`（新 outDir）EXIT=0，验证后已删除临时目录。
未做 Chromium 端到端（本机无 playwright，未强引新依赖）。

**未删任何后端代码** —— #2 / #4 的下线需你确认后由后端侧执行。

### 12.2 `/ops/lineage` 冷启动

见 11.4「遗留待决策」中的勘误与修复说明 —— 40951 描述已证伪，真实问题（冷启动惊群）
已用 single-flight 修复，冷启动 4 路并发 60.89~63.05s → **28.70s**。

### 12.3 磁盘水位：**水位高的主因不在项目内**

| 项 | 结果 |
|---|---|
| 清理前水位 | 94%（可用 45GB），已从审计时的 93.3% 升至 94% |
| 清理后水位 | 94%（**几乎无变化**） |
| 实际删除 | **约 13.5MB** |

**已删除（仅限本轮审计临时产物，安全）**：8 个 `frontend/dist-*` 验证产物（13.4MB，gitignored）、
10 个 vite 临时文件、审计目录内 11 个**未被报告引用**的临时脚本/日志、`scripts/smoke_probe.py`。
`data/`、数据库、源码、`.venv`、`node_modules` **一律未动**。

**关键结论：D 盘已用 608GB，本项目仅占 7.7GB（1.3%）**，其余约 600GB 是
游戏 / anaconda3 / ollama / Docker / 微信等用户数据。**清理项目内文件无法显著改善水位**，
需用户自行处置（建议用 WizTree 做全盘扫描）。

**待用户确认的清理候选（均未删除）**

| 位置 | 体积 | 说明 / 风险 |
|---|---|---|
| 项目外 `D:\tmp_gitfix` | 470MB | git 事故恢复残留 |
| 项目外 `D:\tmp_gitrecover` | 207MB | 同上 |
| 项目外 `D:\tmp_gitrecover_0915`、`D:\tmp_aqp` | 各约 10MB | 同上 |
| `.mypy_cache` ×2 | 117MB | 可安全重建 |
| `backend/backups` | 61MB | 213 份 `.db` 备份，建议做轮转策略 |
| `data/_purged_pre2022` | 49MB | **是行情数据，删前须确认** |
| `data/_backup_universe_daily_bt` | 39MB | 行情备份，删前须确认 |
| `backend/logs` | 31MB | 含 20MB 旧轮转日志 |
| `backend/.tmp_*` | 18MB | 非本轮产物 |

> **✅ 用户已决定：磁盘不再处理。** 上表候选**全部保持原样，一律不删**。
> 本轮磁盘治理到此为止——仅清理了审计自身产生的临时产物（13.5MB），
> **未触碰任何用户数据、行情数据、备份或缓存**。
>
> 后续若水位告警再次出现，需由用户自行做全盘处置（建议 WizTree），
> 因为主因在项目之外（D 盘 608GB 中本项目仅占 7.7GB）。

---

## 十三、P2 修复与端点下线（用户要求继续 A、C 项）

### 13.1 剩余 P2：6 项已改 / 3 项评估未改

**已修（6 项）**

| 项 | 内容 |
|---|---|
| **P2-2** | 长耗时接口补 timeout 实参：`datacenter` mirrorRebuild/textBuildFactor → 60s；`production` runFills/account → 30s、ops dag → 60s；`screener` screen → 30s；`research` overview/experiments/featureImportance → 30s。**补充-3 三组重型 GET 全覆盖** |
| **P2-1** | 三个无 onClick 的装饰按钮：「自选」改为**真导航**→ `/watchlist`；「市场概览」= 当前页；「AI专题」全项目无落点，**已移除** |
| **P2-6** | Settings 永久 disabled 的「启用外部接口」按钮 → 改为**非按钮徽标**「未接入·规划中」 |
| **P2-7** | QuantConnect/IB 无关文案 → 明示未接入 |
| **P2-8** | `desk.py` `/orders` 改信封 `{items, total, returned, limit, truncated}`（total 独立 COUNT、truncated 披露截断）；前端消费并展示真实总数与截断标记 |
| — | **新增运行时功能测试锁定新契约**（防回退） |

> **P2-8 的断言变更已复核**：旧断言是 `assert "后端未返回 total/truncated" in src`
> （**确认缺陷存在**），新断言检查 `total/returned/limit/truncated` 四字段**被前端消费**
> （**确认已修复**）。断言是**升级**而非放宽。

**评估后决定不改（3 项，附理由）**

| 项 | 结论与理由 |
|---|---|
| **P2-9** echarts 694KB | `lib/echarts.ts` **已是** `echarts/core` 按需注册；694KB 是 8 图表 + 12 组件的真实并集（仅 `markArea` 疑似未用，收益极小）。23 个懒加载页共享该 chunk、**首屏不加载**，拆分无净收益且牵动 23 个页面 |
| ~~**P2-3** 全局重试~~ | ⚠️ **本条已被后续决策推翻**：用户要求"如果能优化则修复"，经评估可行，**已实施**，见**第十四章**。（当时的顾虑是叠加放大，已用 `fetcher` 传 `retry:false` 的 opt-out 消除） |
| ~~**P2-4** 卸载取消请求~~ | ⚠️ **本条已被后续决策推翻**：同上，**已实施**（抽共享 hook，实际接入 16 个长任务点而非 15 个页面全量铺开），见**第十四章** |

### 13.2 端点下线：1 删 / 1 留（原计划 2 删）

工程师按前置验证条件主动停止并报告，据此调整决策：

| 端点 | 决策 | 理由 |
|---|---|---|
| `POST /settings/apikeys/rotate` | **✅ 已删** | 前端/后端均无引用；配套删 2 个用例 + ADMIN 清单 + 4 处计数 53→52 |
| `GET /market/overview` | **⏸ 保留** | 核心 `_build_overview` 被**启动预热** `warm_overview_cache`（main.py:113）共用；且 `test_overview_heal_chain.py` 整文件 **8 个用例**是围绕它的专用回归套件。删掉的只是薄兼容壳，**收益 << 损失** |

**保留端的处理**：`market.py:900` 加 deprecated 标注（写明已被 rt/daily 覆盖、新代码勿用、
保留原因）；前端死包装 `marketApi.overview()` 已确认全仓无调用并删除。

**复核记录**：删除 `rotate` 涉及 4 处计数改动（`:327/:328/:782` 及文案）。经查
`checked` 是 `for ... checked += 1` 的**动态计数**而非硬编码放行，删端点后自然由 53 变 52，
**断言仍在守护"剩余 52 个端点全部被检查信封契约"**（旁证：`else: continue` 分支若被走到
则计数不足、断言失败）。属机械同步，非放宽断言。

**实测**：`POST /api/v1/settings/apikeys/rotate` → **40400 Not Found**；
`/market/overview/rt`、`/overview/daily` 不受影响，相关用例全绿。

### 13.3 本阶段最终状态

- **改动**：31 个文件，**+1261 / −318**
- **测试**：后端全量 **1801 passed / 8 skipped / 0 failed**（较基线 1804 减少 3，系删除
  `rotate` 的 3 个专属用例，非回归）
- 未触碰 `ops.py` / `portfolio.py` / `core/errors.py` / `domain/portfolio.py` / `cache/swr.py`

审计目录内的其余脚本**保留**（均被报告引为证据，且审计未收尾）。

---

## 十四、P2-3 / P2-4 架构级改造（用户要求评估并修复）

> 详细文档：[`p2-3-p2-4-refactor.md`](./p2-3-p2-4-refactor.md)
> 这两项在第十三章曾被判为"本轮不做"，用户要求重新评估可行性后**已实施并通过独立复核**。

### 14.1 对原审计描述的两处纠正

**① P2-3 的原建议有害，已收窄。** 原建议"对幂等 GET 增加一次重试"没有区分
「前端超时」与「连接失败」。对本系统（后端 HTTP 恒 200、长任务真在跑）而言，
**超时后重试等于重复提交一次长计算**，会加剧后端负载。实施时明确排除：

| 场景 | 是否重试 |
|---|---|
| 连接失败 / 无响应、HTTP 502/503/504 | ✅ 重试 1 次（600ms ±20% 抖动） |
| **前端超时 `ECONNABORTED`** | ❌ **不重试**（后端任务仍在跑） |
| 主动取消 / `signal.aborted` | ❌ 不重试 |
| 4xx、500、业务码非 0、POST/PUT/DELETE | ❌ 不重试 |

判定顺序固定为 **取消 → 超时 → 无响应 → 状态码**。顺序是关键：取消和超时的
`error.response` 同样为空，若先判"无响应"就会把它们误当连接失败重试。

**② P2-4 原「影响」描述不成立。** 原文称"前端堆积在途请求会加剧后端排队"——
浏览器 abort **不会停止后端已在执行的任务**（FastAPI handler 照跑到底）。
真实收益只有：① 释放浏览器并发连接（HTTP/1.1 每域名 6 连接上限，长任务占槽会饿死
后续请求）；② 避免已卸载组件 setState。

### 14.2 实施结果

| 项 | 内容 |
|---|---|
| **P2-3** | `client.ts`（+99/−9）新增 `isRetryableNetworkError` + 拦截器重放；`swr.ts`（+5/−1）`fetcher` 传 `retry:false` |
| **P2-4** | 新增 `hooks/useAbortableTask.ts`（82 行共享 hook），接入 **16 个长任务点**（判定标准 >30s）；另为 4 个 api 文件补 `options` 透传 |

**排查中发现一处实现遗漏**：`Backtest/SignalAnalysisPanel.tsx` 的 `signalAnalysis`（120s）
——`api/backtest.ts:66-67` **早已接受 `signal` 参数，页面就是没传**，且该面板是 Tab 形式、
切 Tab 即卸载。价值最高。

**评估后不改**：MarketOverview（走 SWR，接入须改共享 fetcher，影响面过大）、
OrderDesk / Etf / Screener / StockDetail / Watchlist（≤30s 边界）、Settings（挂载期一次性
聚合）、各页短轮询（已在卸载时 `clearInterval`）、Research / Backtest / TopKPanel（已有等效实现）。

### 14.3 独立验证（QA 重建，不采信自报）

P2-3 工程师自报"28/28 通过"但**探针跑完即删、未入库**。QA 用 esbuild 打包真实 `client.ts`
+ 桩 axios adapter **独立重建**了断言集，结论一致：

```
PASS | 无响应 → GET 调用 2 次 / 502·503·504 → 各 2 次
PASS | 500·401·403·404 → 各 1 次
PASS | ECONNABORTED(超时) → 1 次，文案=请求超时，请稍后重试
PASS | ERR_CANCELED → 1 次 / 飞行中 abort(无 response) → 1 次   ← 证明判定顺序正确
PASS | retry:false → 1 次 / POST 无响应 → 1 次
PASS | 退避确实发生 ≈600ms（区间 480~720ms）
判定顺序单测 10/10 PASS ⇒ 汇总 28/28 PASS
```

P2-4 为 **16/16 全查（非抽样）**：13 处四要素齐全，3 处第 ④ 要素不适用（**该请求本就没有
loading state**，判定正确，未为凑要素新增 state）；16/16 signal 真传到 api 层、无漏透传；
**无一处把取消当错误弹给用户**。

**叠加风险已消除**：SWR 端点最坏请求数 6 次 → **3 次**。

**硬红线**：全仓搜"取消任务/已取消"命中 4 处，逐条判定**均非 P2-4 引入且均非误导**
（其中 3 处是真实后端撤销端点）。P2-4 未新增任何用户可见取消类文案。

**专项评估（原担忧的回归）**：Pipeline `dagRerun`（300s）加卸载 abort 是否会让用户重复触发
导致 `ERR_PIPELINE_BUSY`？——**不会**。`busy` 是组件局部 state，切路由即卸载销毁，
**改动前后用户切回看到的东西完全一致**；留在本页时 P2-4 根本不触发 abort。有代码依据，
非 P2-4 回归。

### 14.4 遗留项

1. `client.download()` 不支持 signal → 三个 180s 导出无法中断
2. `del` 不支持 signal → 一旦用于长任务即无法中断
3. Pipeline 页不提示"后端可能仍有任务在跑"（`recent_jobs` 的 RUNNING 行不门控按钮）——
   既有独立改进项，非 P2-4 引入

### 14.5 提交归属（需知悉）

- P2-4 的 commit `3531fa0` **混入了约 200 行并行工作线的未提交改动**
  （`api/production.ts`、`api/datacenter.ts`、`pages/DataCenter/index.tsx`、
  `pages/DataQuality/index.tsx`）。QA 已逐文件读全确认**内容完好、可编译、未被破坏**。
  拆分需交互式 hunk 编辑、风险高于收益，未做；如需拆分可 `git reset --soft HEAD~1` 重排
- **同时补提交了此前一直未入库的前序审计修复**：`5d0a879`（后端 16 文件）、
  `ee1f886`（前端 11 文件）。补提交前这些修复**仅存在于工作区**，有丢失风险；
  现已全部入库，**工作区干净**

### 14.6 本轮明确未做的项（如实登记，勿视为已完成）

**核心缺陷已全部闭环**：P1 六项全修、6 个死端点 5 处置 + 1 有理由保留、
P2 九项中 7 修 / 1 明示无影响（P2-7 纯静态文案）/ 1 评估不改（P2-9 echarts 体积）。

以下**未做**，均经核实：

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 1 | ~~**后端全局请求超时中间件**（第八节建议 #7）~~ | ✅ **已于第十五章实施** | 新增 `core/timeout_guard.py`（纯 ASGI，205 行） |
| 2 | ~~`overview/daily` 降级载荷顶层 `status`（建议 #8）~~ | ✅ **已于第十五章实施** | 三处降级分支补齐，含**新发现的 daily 更严重遗漏** |
| 3 | ~~**RBAC_ENFORCE 默认 False**（第六节风险）~~ | ✅ **判定为误判，维持现状** | **不是缺陷**：`config.py:203-206` 记录 2026-09-23 **用户主动裁决**"只要登录即可用全部功能"。详见**第十六章** |
| 4 | 磁盘水位（建议 #9） | ⛔ **用户明确取消** | 且主因在项目外（D 盘 608GB 中本项目仅 7.7GB） |
| 5 | 外部数据源不稳定 | ⏸ 现状接受 | 东财不可达已走新浪/腾讯兜底；跨境 ETF 降级 unavailable。属外部依赖，非项目缺陷 |
| 6 | HTTP 恒 200 / 错误码在 body | 🏛 **架构设计，不改** | 是本项目既定约定；仅需注意监控口径不能用 HTTP 状态码（已登记） |

**本轮改造新增的 3 个遗留**（见 14.4）：`client.download()` 不支持 signal（3 个 180s
导出无法中断）、`del` 不支持 signal、Pipeline 页未提示"后端可能仍有任务在跑"。

> 上述 6 项中，**#1、#2 已于第十五章实施**；**#3（RBAC 未真正生效）是剩余唯一具备
> 安全含义的**，建议单独排期。

---

## 十五、全局请求超时中间件 + 降级载荷自愈（用户要求完成 14.6 的 #1、#2）

### 15.1 全局请求超时中间件（新增）

**产出**：`backend/app/core/timeout_guard.py`（205 行）+ `backend/tests/test_timeout_guard.py`（391 行，14 用例）

**实现要点**

| 项 | 内容 |
|---|---|
| 范式 | **纯 ASGI**（`class` + `__call__(scope, receive, send)`）。项目严禁 `BaseHTTPMiddleware`（`panic_guard.py:11` 有理由：破坏异常堆栈/流式响应） |
| 挂载位置 | PanicGuard **之后** add、CORS **之前** add ⇒ 最外到内 `timing → CORS → TimeoutGuard → PanicGuard`。推导：① 必须在 CORS 内侧，否则 504 不带 CORS 头、前端只能看到不透明错误；② 必须在 PanicGuard 外侧，预算是对整条生命周期的硬边界 |
| 默认预算 | **240s**（`REQUEST_TIMEOUT_SECONDS`，可配置便于测试 monkeypatch） |
| 完全豁免 | `/api/v1/notify/stream`（SSE，全仓唯一流式端点；`while True` + 15s 心跳，加任何固定预算都会误切并触发前端自动重连风暴） |
| 延长预算 | `/api/v1/backtest/strategy-run` **660s**（前端 600s）、`/api/v1/ops/dag/rerun` **330s**（前端 300s） |

**规格勘误（我给的原始数据有误，工程师指出后采纳）**

> 我原本说"前端最长超时是 `download()` 的 180s，默认 240s 足够"。
> **实测最长是 600s**：`frontend/src/api/strategyBacktest.ts:154` 带 `optimize_params`
> 时传 `600_000`。若不把 `strategy-run` 延长到 >600s，**带寻优的策略回测会被必现误杀**。
> 已延长到 660s，并用测试 `test_default_budget_exceeds_every_frontend_timeout`
> 把"默认预算 > 前端最长超时"固化为断言。
>
> 另：我建议"导出 `/export/*` 需延长"——工程师判定**不需要**（180s < 240s 已满足不变量，
> 延长反而降低兜底灵敏度）。此判断成立。

**ASGI 协议正确性（最容易翻车处）**

包装 `send` 记录是否已发 `http.response.start`；超时时若**已 start** 则**一条消息都不发**
（否则触发 `Unexpected ASGI message 'http.response.start', after response start`）。
测试用 mock `send` 复刻真实服务器状态机——收到第二条 start 就抛错，**测试通过即等价于无协议错误**。
有变异反证：把 `if response_started:` 改成 `if False:` 后 2 个用例失败。

**与 PanicGuard 的交互**：`panic_guard.py:49-54` 的 `_PASSTHROUGH` 已含 `CancelledError`，
不会误吞。用测试证明（手搭 `TimeoutGuard(PanicGuard(挂住的app))`，断言仍回 504、
无 `[panic-guard]` 误导日志、`PANIC_CONTAINED_TOTAL` 不 +1），**非读代码推断**。

**响应格式：HTTP 504 + 统一信封 `code=50400`（`ERR_REQUEST_TIMEOUT`）**

我原本建议"破例不用统一信封"（理由是业务层可能已卡死）。工程师**未采纳**，理由是：
构造 `fail()` + `jsonable_encoder` 是纯本地操作、不 await 已卡死的协程，无新增挂起风险；
而用信封能让前端拦截器走 `isApiEnvelope` 分支，**从而短路 `isRetryableNetworkError`
（它对 504 返回 true）** ——否则前端会对一个已把 worker 卡住的 GET **自动重放一次，放大故障**。
该理由成立，采纳。两端码表已同步（`frontend/src/types/api.ts`），否则
`test_error_code_governance.py` 会红。

**已知边界（如实披露）**：`asyncio.wait_for` 只能取消协程的 await 点，**无法中断已进入
C 层/线程（`asyncio.to_thread`）的同步计算**（如 polars 重算）。超时后客户端拿到明确 504，
但那个线程仍在后台跑完。这是所有 asyncio 超时方案共同边界，改造前是"永久占用 worker"、
现在是"有界 + 明确 504"。根治需任务队列 + 进程级隔离，列入后续项。

### 15.2 降级载荷顶层 `status`（补齐）

**改动**：`backend/app/api/v1/market.py` 三处 += `"status": "degraded"`

| 出口 | 缓存 TTL | 处置 |
|---|---|---|
| `market_overview` 超时分支 | `cached_or_build` ttl=300 | ✅ 补 |
| `market_overview` 异常分支 | 同上 | ✅ 补 |
| **`market_overview_daily` 超时分支** | **`_daily_ttl()` 最长 3 天** | ✅ **新发现的遗漏，一并补** |

> **daily 那处比 audit 登记的更严重**：一次 5s 超时会让日频块**数天内**持续返回空态
> 且不自愈。属排查中的额外发现。

**为什么用 `degraded` 而不是 `unavailable`**

`cache/swr.py` 语义：`unavailable` ⇒ **完全不写缓存**；`degraded` ⇒ **短 TTL（≤15s）**。
这是被启动预热 `warm_overview_cache` 共用的兼容端点：用 `unavailable` 会导致下次请求
必然重走 6s 预算、大概率再超时 ⇒ **用户每次都要等满 6s**。用 `degraded` ⇒ 15s 内快速返回
降级、15s 后重新尝试。与 rt 路径（`market.py:593`）及预热的既有口径一致。

**顺带确认**：`warm_overview_cache` 调的是 `_build_overview` 而非 `_build()`；
`_build_overview`（`market.py:622-639`）显式 `rt.pop("status", None)`，故派生的 `daily`
不受本次改动影响——分工自洽，未扩大改动。

**测试**：`test_overview_heal_chain.py` **11 passed**（原 9 + 新增 2）。
新增用例断言 `data["status"] == "degraded"` 且 `_effective_ttl(...) == (15, 0)`、
Redis 里实际 `ttl == 15`。**既有断言一律未改、未放宽**（新日志保留 `unavailable` 字样
正是为了让既有断言继续成立）。

### 15.3 ⚠️ git 事故：`.git` 对象库**二次**被清空

实施过程中检测到 `.git` 被外部清空（`.pack` 数据文件全部消失、仅剩 2 个 `.idx`；
`refs/heads` / `packed-refs` / `hooks` / `info` 被删），`git log` 报
"does not have any commits yet"。

| 项 | 结果 |
|---|---|
| 代码 | **完全无损** —— 所有源码、配置、data 均在（已逐文件核实 `timeout_guard.py` 205 行、`test_timeout_guard.py` 391 行、`market.py` 12 处 `"status": "degraded"`） |
| 恢复尝试 | `git fetch origin` 失败：`fatal: fetch-pack: invalid index-pack output`（对象库损坏） |
| 处置 | 从完整工作区重建 master：`8940058`（691 文件，已核对 `.gitignore` 生效，无 `.env`/`.db`/parquet/node_modules 混入） |
| 损失 | **仅提交粒度**（原 7 个 commit 的拆分丢失），代码内容完整保留在重建 commit 的 message 中 |

这是本仓库**第二次**发生（首次 2026-09-23，reflog 首行即当时的恢复记录）。
**根因未定位**，建议排查是否有外部清理进程/IDE 插件（`.git/` 下还留有 `cursor/`、
`opencode/` 等非标准目录）定期清理大文件。若不解决，第三次仍会发生。

### 15.4 全量回归结论（重建后统一跑，非分头自证）

```
========== 1818 passed, 8 skipped, 216 warnings in 429.20s (0:07:09) ==========
```

| 项 | 结果 |
|---|---|
| 本轮基线（13.3） | 1801 passed / 8 skipped |
| 本轮结果 | **1818 passed / 8 skipped / 0 failed** |
| 增量 | **+17** = 超时中间件 14 用例（`test_timeout_guard.py`） + 降级 status 2 用例（`test_overview_heal_chain.py`） + 1 |

**增量与两个 worker 各自报的用例数完全吻合，无隐藏失败、无跳过新增。**

> 末尾出现的 `SystemExit: 1` 是 pytest 在 atexit 清理临时目录时被**本机的
> `node-safe-delete` 护栏**拦截（环境策略），发生在统计行**之后**，与测试结果无关。

---

## 十六、RBAC：⚠️ 审计误判纠正 —— 是用户主动放开，非缺陷

> **本节推翻了 14.6 与第六节把 RBAC 列为"待修安全项"的判定。**

14.6 曾把 `RBAC_ENFORCE=False` 列为"唯一有安全含义的未做项"，据此做了开启影响面评估。
评估过程中在 `backend/app/core/config.py:203-206` 发现了决定性证据——**这是用户
2026-09-23 的主动裁决，不是遗漏**：

```python
# ---- RBAC 开关（2026-09-23 用户裁决：全面放开）----
# 用户明确要求「只要登录即可使用全部功能（含下单 / 数据同步 / 模型训练等写操作），
# 对所有现有和将来新增的用户都生效」⇒ 默认 False。
```

**结论：维持现状（`RBAC_ENFORCE=False`），不开、不改、不提权。**

用户的二次确认（2026-09-27："这些账号是真人在用"）与 09-23 裁决口径**一致**——
即"所有登录用户都能用全部功能"。若按评估方案开启，反而会把 3 个真人账号挡在
70 个端点之外，直接违背用户既定意图。

**误判原因（如实记录）**：审计阶段只读了第六节登记的风险描述，未回溯
`config.py` 里的裁决注释，把"用户主动选择的放开策略"当成了"未实现的安全控制"。

以下评估数据**保留备查**（若将来用户改变策略需要开启，这份影响面清单仍然有效）。

### 16.1 机制现状（完备，可回滚）

| 项 | 现状 |
|---|---|
| 角色 | `viewer`(0) < `researcher`(1) < `admin`(2)，`auth.py:29-39` |
| 单一开关 | `Settings.RBAC_ENFORCE`，默认 `False`（`config.py:208`） |
| 关闭时行为 | `ensure_role` 直接放行 —— **只保留"登录"门，去掉"角色"门** |
| 开启时行为 | 比较 `ROLE_RANK`，不足抛 `40300`（HTTP 恒 200，码在 body） |
| 设计优点 | `rbac_enforced()` 是全部 `require_role` 的最终汇入点，60+ 处调用**无需逐个改动**即可整体回滚 |

### 16.2 影响面数据

全仓 `require_role` 调用点按最低角色分布：

| 最低角色 | 调用点数 |
|---|---|
| `admin` | **3** |
| `researcher` | **67** |
| `viewer` | 36 |
| **合计** | **106**（`require_role` 总引用 129，含定义/导入） |

### 16.3 存量用户实况（决定能不能开的关键）

只读查询生产库 `data/sqlite/aqp.db`：

| 用户 | 角色 | 创建时间 | 备注 |
|---|---|---|---|
| `testadmin` | **admin** | 2026-08-29 | **唯一拥有完整权限的账号** |
| `qauser_686823` | viewer | 2026-09-14 | 疑似 QA 测试账号 |
| `perfcheck_0914` | viewer | 2026-09-14 | 疑似压测账号 |
| `tet` | viewer | 2026-09-22 | 近期创建，用途不明 |

**⚠️ 关键结论**：
- **当前没有任何 researcher 用户**
- 若开启，3 个 viewer 账号将被挡掉 **67 个 researcher 端点 + 3 个 admin 端点（共 70 个）**
- 唯一不受影响的只有 `testadmin`

补充事实：注册**可自选** viewer / researcher（`auth.py:45` `_REGISTERABLE_ROLES`），
故新用户不是必然 viewer；但存量这 3 个都是 viewer。

### 16.4 配套与前端

- **测试覆盖已存在**：`conftest.py`、`test_read_endpoints_rbac.py`、
  `test_route_permission_contract.py`、`test_write_endpoints_smoke.py` 均涉及 RBAC，
  说明"开启态"也被测过，不是只测关闭态
- **前端已有角色门控**：`App.tsx` 多处 `<RequireRole minimum="...">` 包裹路由。
  开启后端校验后需确认前后端口径一致，否则会出现"前端显示入口、后端 403"

### 16.5 结论：维持现状（不开、不改、不提权）

**3 个 admin 端点**（开启后仅 admin 可用）：

| 端点 | 用途 |
|---|---|
| `PUT /api/v1/settings/engine` | 改引擎配置 |
| `POST /api/v1/settings/data/cache/clear` | 清数据缓存 |
| `POST /api/v1/settings/db/backup` | 数据库备份 |

按 09-23 裁决，这三个运维操作**本就对所有登录用户开放**——这是用户明确选择的
"单机/团队内部使用、不设权限墙"模式，与"3 个账号是真人在用"的口径自洽。

**本次未修改任何代码、未改动任何用户角色、未开启开关。**

> 若将来要切换为分级管控，需：① 先把 3 个 viewer 提为 researcher（否则瞬间 70 个
> 端点 403）；② 明确 admin 三端点的归属；③ 前后端 `RequireRole` 口径对齐；
> ④ 回归那 4 个 RBAC 测试文件。届时可复用本节的影响面清单。
