# B8 · 前端 · 基础设施（审核报告）

> 审核范围：`frontend/src/main.tsx`、`App.tsx`、`api/client.ts`、`api/swr.ts`、`stores/*.ts`、
> `hooks/*.ts`、`utils/format.ts`、`utils/useChart.ts`、`lib/echarts.ts`、`types/*.ts`、
> `components/*.tsx`、`components/ui/index.tsx`。
> 为完成「路由 × 角色对账」，额外只读核对了 `frontend/src/api/*.ts` 与
> `backend/app/api/v1/*.py`、`backend/app/core/auth.py|errors.py`（不改任何源码）。
>
> 契约依据：`docs/audit-2026-09-18/AUDIT-BRIEF.md`、`docs/chatgpt-full-review-prompt.md` §1/§3-B8。
> 静态基线复用父审核员结论：`npx tsc --noEmit --noUnusedLocals --noUnusedParameters` = 0 错误（未重复跑）。
> 未启动 dev server，未修改任何源码；临时探针写在 `%TEMP%\aqp-b8-merge\`。

---

## 1. 逐文件结论

| 文件 | 结论 |
|---|---|
| `main.tsx` | 未发现 P0–P2。关闭 StrictMode 是有意设计（注释已说明 charts 双 init 泄漏）。 |
| `App.tsx` | 未发现 P0–P2 本身；但它是「路由门槛 vs 端点门槛」错配的载体，见 #01–#04。 |
| `api/client.ts` | 超时齐全（全局 15s，写路径可选放宽），`del` 缺 `signal` 但**无调用方需要取消**（#11）。`isApiEnvelope` 的宽松判定**未找到真实触发路径**（§5-1）。 |
| `api/swr.ts` | SWR 按 key 缓存，**不存在旧响应覆盖新响应**（#12 反向结论）；但 `REFRESH.snapshot/static` 是死常量（#14）。 |
| `stores/useAuthStore.ts` | 角色等级与后端一致；`expiresAt` 来自后端 `expires_at`（无秒/毫秒误算）；**遗留 `AQP_ADMIN_TOKEN` 迁移仍在生效**（#08）。 |
| `stores/useNotifyStore.ts` | SSE 走默认 `data:` 帧（后端缺省 `channels=notify` 恰好用 `tag=None`），`onmessage` 能收到；重连换票、generation 丢弃旧票、`close` 清 timer 均正确。未发现 P0–P2。 |
| `stores/usePreferencesStore.ts` | `getRefreshIntervalMs` 全仓 0 引用（#14）；`loadFromServer` 取值经 `setRefreshFreq` 已钳制 1–120。未发现 P0–P2。 |
| `stores/useUiStore.ts` | `period`/`setPeriod`/`setRangeDays` 三个成员无任何消费者（KLineChart 自带 local period），见 #14。 |
| `stores/useWatchlistStore.ts` | `removeGroup` 死方法（#14）；`addTo` 的「组不存在则回落默认组」分支语义错误但**不可达**（§5-2）。 |
| `hooks/useRefreshInterval.ts` | 未发现 P0–P2。 |
| `hooks/useTaskPolling.ts` | **已核 SWR 源码**：错误态下 SWR 不再发请求（`use-swr-guqrv3k1.js:624`），卸载/终态均停止（#12 反向结论）。 |
| `hooks/useWatchlistQuotes.ts` | 无请求代际守卫，**存在旧响应覆盖新响应**（#05）。 |
| `utils/format.ts` | 未发现 P0–P2（涨红跌绿方向正确、`null`/`NaN` 全部兜底）。 |
| `utils/useChart.ts` | 第二个 effect 依赖缺 `node` → **首次 option 被丢弃**（#06，本批最严重）。 |
| `lib/echarts.ts` | 按需注册与类型透传正确；未发现 P0–P2。 |
| `types/api.ts` | 19 个码中 12 个零引用，2 个后端从不抛出，且**缺 50001（panic 兜底码）**（#09/#10）。 |
| `types/stock.ts` | `MoneyFlowBlock` 双声明合并导致市场侧类型被**收紧成个股形状**（#07）；另有 3 个死类型（#14）。 |
| `types/{datacenter,etf,p1,portfolio,watchlist}.ts` | 未发现 P0–P2（`EtfListResult.sort_applied/dir_applied` 类型注释已自述「前端不使用」）。 |
| `components/AuthBootstrap.tsx` | 未发现 P0–P2（过期时提前 return 不清会话，但 `RequireRole` 会跳登录，无害）。 |
| `components/DataFreshness.tsx` | 文件本身正确；`onRefresh` 的 rejection 无 catch 已由 B9a #17 覆盖，不重复。 |
| `components/ErrorBoundary.tsx` | 未发现 P0–P2。 |
| `components/FactorHealthCard.tsx` | 未发现 P0–P2（`psi.max` 有 `?? '—'`）。 |
| `components/RequireAuth.tsx` | `ROLE_RANK` 与 `backend/app/core/auth.py:35` 完全一致；未发现 P0–P2。 |
| `components/ResearchDisclaimer.tsx` | 未发现 P0–P2。 |
| `components/Sidebar.tsx` | 未发现 P0–P2。 |
| `components/Topbar.tsx` | 裸 6 位数字被**无条件当作 ETF**（#13）；匿名用户搜索必失败（#03 附表）。 |
| `components/ui/index.tsx` | `ErrorState` 的 `message` 形参被丢弃、恒显示错误文案（#15）；`ScoreBadge`/`PctCell`/`DegradedBadge` 死组件（#14）。 |

---

## 2. 问题清单

### #01 [P1][一致性][确定] viewer 级 `/data` 页打开即报错：日志/同步状态端点是 researcher

位置：`frontend/src/App.tsx:97`（`/data` minimum=viewer）+ `frontend/src/pages/DataCenter/index.tsx:401-435`
+ `backend/app/api/v1/datacenter.py:592`（`/logs` researcher）、`:719`（`/sync/status` researcher）

论证：`loadAll()` 用 `Promise.allSettled` 并发 7 个请求，其中 `datacenterApi.logs(60)`（`/datacenter/logs`，
researcher）与 `datacenterApi.status()`（`/datacenter/sync/status`，researcher）对 viewer 必然 40300。
`:433-434` 把失败项拼成横幅：`部分数据面板加载失败：{...}`，而 40300 的 message 就是后端 detail 常量
字符串 `FORBIDDEN`（`core/errors.py:140` + `core/auth.py:210`）。因此 **viewer 每次进入 `/data` 都会看到
「部分数据面板加载失败：FORBIDDEN；FORBIDDEN」**，且同步控制台恒为空、`sync` 状态永不可得。

验证：
```
$ rg -n "require_role" backend/app/api/v1/datacenter.py | Select-String "logs|sync/status"
  592:  _user: dict = Depends(require_role("researcher")),   # /logs
  719:  _user: dict = Depends(require_role("researcher")),   # /sync/status
$ rg -n "datacenterApi.logs|datacenterApi.status" frontend/src/pages/DataCenter/index.tsx
  404:  datacenterApi.logs(60), datacenterApi.status(), datacenterApi.autoStatus(),
  446:  const st = await datacenterApi.status();
  464:  ...
```
运行时最小验证：用 viewer 账号打开 `/data`，横幅出现两条 `FORBIDDEN`。
修复方向（不实施）：`loadAll` 里按 `hasMinimumRole(user.role,'researcher')` 决定是否请求
`logs`/`status`，或把这两个端点降到 viewer。

### #02 [P1][一致性][确定] viewer 级 `/data` 页内嵌 TrainPanel：状态轮询恒 40300 + 「开始训练」必 40300

位置：`frontend/src/pages/DataCenter/index.tsx:890`（`<TrainPanel />` 无角色门禁）
+ `frontend/src/pages/DataCenter/TrainPanel.tsx:43-44`、`:73-81`
+ `backend/app/api/v1/datacenter.py:1132`（`/train/start` researcher）、`:1150`（`/train/status` researcher）

论证：`useTaskPolling('/api/v1/datacenter/train/status')` 对 viewer 返回 40300 → `statusError`
（`TrainPanel.tsx:60-62`）→ 页面常驻红色错误框 `FORBIDDEN`；`/train/readiness`（`datacenter.py:1124`）
**没有任何鉴权依赖**所以能成功，于是就绪度清单正常显示、而「开始训练」按钮可点（gate 通过时）→ 点击必 40300。
这正是父审核员线索的确认与延伸（不止按钮，加载即报错）。
（轮询不会无限发请求：SWR 在 error 态下**不再 revalidate**，见 #12 的源码证据。）

验证：
```
$ rg -n "train/start|train/status|train/readiness" backend/app/api/v1/datacenter.py
  1124: @router.get("/train/readiness")   ← 无 Depends
  1132: @router.post("/train/start")     ← researcher
  1150: @router.get("/train/status")     ← researcher
$ rg -n "TrainPanel" frontend/src/pages/DataCenter/index.tsx
  890:  <TrainPanel />            ← 无角色条件
```

### #03 [P1][一致性][确定] viewer 级 `/settings` 页暴露 admin/researcher 操作，点击必 40300

位置：`frontend/src/pages/Settings/index.tsx:588`（立即同步）、`:609-612`（清理所有缓存）、`:618-621`（备份）、
`:467`（刷新所有连接数据，且 `:218` 页面挂载时自动调用）
+ `backend/app/api/v1/app_settings.py:267`（`/data/sync` researcher）、`:277`（`/data/cache/clear` admin）、
`:307`（`/db/backup` admin）、`:236`（`/connectors/test` researcher）
+ `frontend/src/App.tsx:99`（`/settings` minimum=viewer）

论证：`isAdmin` 只门禁了「量化引擎设置」卡片（`:477`），**「数据中心与缓存管理」卡片完全没有角色判断**。
于是 viewer 可见并可点两个 admin 端点与一个 researcher 端点，全部返回 40300（`FORBIDDEN`）。
「刷新所有连接数据」更糟：页面加载时 `load(true)` → `testAll()`（`:218`）**自动**打两次 researcher 端点，
对 viewer 是每次进页面必然产生的静默失败请求（`Promise.allSettled` 吞掉）。

验证：
```
$ rg -n "isAdmin" frontend/src/pages/Settings/index.tsx
  137: const isAdmin = authUser?.role === 'admin';
  238: if (isAdmin) { ... saveEngine ... }        ← 只挡引擎配置
  477: {isAdmin && ( <Card title="量化引擎设置">  ← 只挡引擎卡片
$ rg -n "data/cache/clear|db/backup|data/sync" backend/app/api/v1/app_settings.py
  267: @router.post("/data/sync")            ← researcher
  277: @router.post("/data/cache/clear")     ← admin
  307: @router.post("/db/backup")            ← admin
```

### #04 [P1][一致性][确定] viewer 级 `/screener` 与 `/portfolio` 页的写操作按钮未做角色门禁

位置：`frontend/src/pages/Screener/index.tsx:199-208` + `:338-342`（导出 Excel）
+ `backend/app/api/v1/export.py:23`（`/export/screener` researcher）
+ `frontend/src/pages/Portfolio/index.tsx:251`（`portfolioApi.backtest`）
+ `backend/app/api/v1/portfolio.py:105`（`/portfolio/backtest` researcher）

论证：两个页面路由门槛都是 viewer（`App.tsx:93`、`:96`），按钮无条件渲染：
viewer 点「导出 Excel」/「运行回测」→ 40300，页面 `setError(e.message)` 展示裸 `FORBIDDEN`。
`StockDetail` 已用 `hasMinimumRole(role,'researcher')` 正确门禁 `predict`（`StockDetail/index.tsx:42,61`），
说明「按角色隐藏」的写法在项目内已有先例，这两处是漏做，不是设计。

验证：
```
$ rg -n "exportApi.screener" frontend/src/pages/Screener/index.tsx
  202: await exportApi.screener({...})     ← 无角色判断，onClick 直接调
$ rg -n "hasMinimumRole|role ===" frontend/src/pages/Screener/index.tsx frontend/src/pages/Portfolio/index.tsx
  (无输出)
```

### #05 [P2][Bug][确定(代码层)] `useWatchlistQuotes` 无请求代际守卫：旧响应可覆盖新响应

位置：`frontend/src/hooks/useWatchlistQuotes.ts:50-64`

论证：`load` 只依赖 `symbols`，`useEffect(() => { void load(); }, [load])`。当自选集合变化
（用户删/加标的、切换分组）时，旧请求不会被取消，也没有 `cancelled`/代数检查，旧请求后到即
`setData(旧数据)`。同文件注释声称「轮询仅在页面可见时触发」，但可见性检查只作用于 setInterval，
不作用于手动 `load`。消费者：`Watchlist/index.tsx:186`、`Screener/index.tsx:168`。
最小验证（无需 DOM）：把 `fetcher` 换成 `async (syms) => { await sleep(syms.length>1?300:0); return {syms}; }`，
先传 2 只再加 0 只，观察最终 `data` 是否回落为 2 只的旧值。
修复方向：`load` 内引入 `const gen = ++genRef.current`，返回后 `if (gen !== genRef.current) return;`。

### #06 [P1][Bug][确定] `utils/useChart` 的 `setOption` effect 缺 `node` 依赖 → 首个 option 被丢弃、图表空白

位置：`frontend/src/utils/useChart.ts:12-20`

```ts
12  useEffect(() => {
13    if (!node) return;                      // node 来自 useState，挂在「下一次 commit」
14    inst.current = echarts.init(node);
...
18  }, [node]);
19  useEffect(() => { if (option && inst.current) inst.current.setOption(option, true); },
20    [option]);                              // ← 缺 node
```

论证：`node` 是 `useState`（回调 ref 里 `setNode(el)`），因此**挂载那次 commit 的 passive effect 里
`node` 仍是 `null`**；`[option]` 这个 effect 在同一次 flush 里先跑（mount 必跑），此时 `inst.current`
仍为 `null` → 什么都不做；随后 `setNode` 触发的第二次 commit 才 `init`，而 `[option]` 依赖未变 →
不会再跑。结论：**只要 option 引用在 node 挂载后不再变化，这个 option 就永远不会被 setOption**。

React 自身的源码保证了这个顺序（已核对安装版本 `react-dom@18.3.1`）：
```
$ rg -n -A10 "function performSyncWorkOnRoot" frontend/node_modules/react-dom/cjs/react-dom.development.js
26106:function performSyncWorkOnRoot(root) {
...
26115:  flushPassiveEffects();        ← 任何新 render 之前，先把上一次 commit 的 passive effects 冲干净
```
ref 回调里的 `setNode` 在 commit 的 layout 阶段被调度为 sync work，而 `performSyncWorkOnRoot` 入口即
`flushPassiveEffects()`，所以 commit1 的 `[option]` effect 一定先于 commit2 的 init 执行 —— 顺序是确定的，
不是竞态。

旁证（同一仓库三种写法对比，这是最有力的证据）：
- `pages/DataCenter/index.tsx:52-66`、`pages/EtfDetail/index.tsx:47-58`：用 `useRef` + `[option]`，
  ref 在 commit 的 layout 阶段就已附上，**同一次 flush 内 `ref.current` 可用** → 正常。
- `pages/Portfolio/index.tsx:52-63`：init effect 是 `[]`，声明在前 → 同一次 flush 内先 init 再
  setOption → 正常。
- 只有共享 hook 用 `useState<node>`，才把 init 推迟到下一次 commit。

可达触发（node 与首个非空 option 同一次 commit 出现）：
`Research/index.tsx:416`（`{quantile && <QuantileChart ...>}`）、`:487`（`{cv && <CvGantt ...>}`）、
`Research/parts.tsx:97/172/201/246/284`、`FactorStudio/index.tsx:328/407/409`、`DataQuality/index.tsx:71-74`
（option 非空且与 div 同 commit 挂载）。症状：图表区域空白，直到 option 因数据再次变化（重跑/重新筛选/刷新）才绘出。

最小验证（二选一）：
1. 浏览器：`/research` 运行一次因子分析，观察「因子分层收益曲线」「因子相关矩阵热力图」是否空白；
   再随便切换一个条件使数据重算，若图表立刻出现即确认。
2. 或把 deps 改成 `[node, option]`，图表首次即出现即确认。
   （注：仓库无 vitest/jsdom，`frontend/package.json` devDependencies 无测试栈，无法本地跑 DOM 用例。）

### #07 [P2][一致性][确定] `types/stock.ts` 同名 `MoneyFlowBlock` 双声明合并：市场侧类型被收紧成个股形状

位置：`frontend/src/types/stock.ts:145`（个股 `MoneyFlowBlock extends PanelBlock`）与 `:430`
（市场 `MoneyFlowBlock extends BlockBase`）

论证：TS 接口声明合并把两份成员并到一起（同名字段类型冲突才会报错，此处无冲突）。合并结果是
**个股侧的 8 个必填字段成为市场侧类型的一部分**：`date`、`main_net`、`main_net_yi`、`main_net_ratio`、
`super_large_net_yi`、`large_net_yi`、`medium_net_yi`、`small_net_yi`。于是
`MarketOverviewData.money_flow: MoneyFlowBlock`（`:507`）与 `OverviewRt.money_flow`（`:523`）
「声明必填、后端从不返回」。因为消费方全用 `?.`，`tsc` 不会报错，类型文件对市场块的契约描述是**假的**；
一旦有人据此写 `money.main_net_yi.toFixed(0)`（类型上合法）就会在运行时拿到 `undefined` 崩掉。
同时这也解释了为什么 `MarketMoneyFlow`（`:352`，市场块真实形状）是死类型：市场侧的形状**无法被单独命名**。

已实测（把两份声明原样复制到 `%TEMP%` 用仓库 tsc 探针编译）：
```
$ cd %TEMP%\aqp-b8-merge
$ tsc --noEmit --strict --target es2020 --moduleResolution node --module esnext probe.ts
probe.ts(4,7): error TS2740: Type '{ status: "ok"; north_net_today: number; main_net_today: number; sector_flows: never[]; }'
  is missing the following properties from type 'MoneyFlowBlock': date, main_net, main_net_yi, main_net_ratio, and 4 more.
exit=2
```
（探针文件：`%TEMP%\aqp-b8-merge\{stock.ts,api.ts,probe.ts}`，未写入仓库。）
反向结论：合并**不会**把必填放宽成可选（合并只会收紧或报错），所以不存在「本该必填却被放宽」的字段。
修复方向：把市场块改名 `MarketMoneyFlowBlock`（或把个股块改名 `StockMoneyFlowBlock`），
同时删掉死类型 `MarketMoneyFlow`/`MarketHeat`。

### #08 [P2][一致性][确定] `useAuthStore` 仍在把 `localStorage.AQP_ADMIN_TOKEN` 迁移成 admin 会话

位置：`frontend/src/stores/useAuthStore.ts:40-46`、`:85-99`

论证：模块加载时只要 `localStorage.AQP_ADMIN_TOKEN` 有值，就 `setSession(token, {username:'admin',
role:'admin'}, null)` 并持久化进 `aqp-auth`。P0 契约明确「`ADMIN_TOKEN` 仅用于运维直连，**不是前端登录凭据**」。
两处实际影响：
1. 生产（`ALLOW_ADMIN_TOKEN_LOGIN=false`，`core/config.py:203-223` 强制）下该 token 后端不认，
   但前端**本地已把角色标成 admin**：`Settings` 会渲染 admin 卡片、`RequireRole` 放行 admin 页面，
   直到 `/auth/me` 返回 40100 才清会话 —— 出现「UI 说我是 admin、后端说不是」的窗口期。
2. `ApiError` 里的 message 与角色标注都基于这个未经验证的字符串。
另注：更早的审核已提出「前端停止迁移旧版 `AQP_ADMIN_TOKEN`」（`docs/audit/AQP_前后端功能审查与修改建议_执行版_20260911.md:100`），
本轮**确认该建议未落地**——这是已知条目的新证据，不是新问题。
补一句反向结论（回答父审核员的「是否误用 ADMIN_TOKEN 当登录凭据」）：**`pages/Login/index.tsx` 没有把
ADMIN_TOKEN 当密码用**（只提交 `username/password`，`:82-86`），全前端引用该键的只有本 store。

验证：
```
$ rg -n "AQP_ADMIN_TOKEN" frontend/src
  stores/useAuthStore.ts:15   const LEGACY_TOKEN_KEY = 'AQP_ADMIN_TOKEN';
  stores/useAuthStore.ts:42   localStorage.getItem(LEGACY_TOKEN_KEY)
  stores/useAuthStore.ts:90-98 迁移分支
$ rg -n "ALLOW_ADMIN_TOKEN_LOGIN" backend/app/core/config.py
  44-47  default=True        ← dev 可用
  205-206 生产必须为 false（否则 ValueError，:222-223）
```

### #09 [P2][一致性][确定] 前端码表 19 个码中 12 个零引用；`51000`/`53001` 后端从不抛出；**缺 50001**

位置：`frontend/src/types/api.ts:19-38`

已核实（`rg` 全仓）：
- 通过 `ERR.xxx` 引用的只有 **5 个**：`UNAUTHORIZED`、`TOKEN_EXPIRED`、`INVALID_TOKEN`（`client.ts:20-22`、
  `AuthBootstrap.tsx:12-14`）、`PIPELINE_BUSY`（`client.ts:49`）、`RATE_LIMITED`（`Research/index.tsx:60`）。
- `ERR.FORBIDDEN` 只出现在 **注释**（`client.ts:23`），无代码引用。
- 另有 **同一码两套表示**：`FactorStudio/index.tsx:89` 用裸字面量 `e.code === 51001 || e.code === 40400`
  等价于 `ERR.DATA_EMPTY`/`ERR.NOT_FOUND`（而 `NlFactorCard.tsx:6` 注释里写的是 `53000`）。
- **后端从不抛出**：`ERR_DATA_SOURCE=51000`、`ERR_EXPR_INVALID=53001`（`backend/app/core/errors.py:96,101`
  是全仓唯二出现处）；`studio.py:344,353,376` 用 `ERR_PARAMS` 代替了本该是 53001 的位置。
- **前端没有 50001**：panic 兜底码 `ERR_PANIC_CONTAINED=50001`（`core/panic_guard.py:114`）在
  `types/api.ts` 中无对应常量、无分支（用户只会看到 message，行为上不崩）。

验证：
```
$ rg -n "ERR\.[A-Z_]+" frontend/src
  client.ts:20,21,22,49 / AuthBootstrap.tsx:12,13,14 / Research/index.tsx:60   （共 5 个码 + 2 处注释）
$ rg -n "51000|53001" backend/app
  backend/app/core/errors.py:96 / :101        ← 仅定义
$ rg -n "\b(51001|40400)\b" frontend/src
  pages/FactorStudio/index.tsx:89             ← 裸数字
```

### #10 [P3][死代码][确定] 前端错误码表声明但不可达清单（12 条）

| 码 | 常量 | 前端引用 | 后端是否抛出 |
|---|---|---|---|
| 40000 | `ERR.PARAMS` | 无（仅 `types/api.ts:6` 注释） | 是 |
| 40104 | `ERR.CREDENTIALS` | 无 | 是（`auth.py:115`） |
| 40105 | `ERR.USER_EXISTS` | 无 | 是 |
| 40106 | `ERR.REGISTER_DISABLED` | 无 | 是 |
| 40107 | `ERR.REGISTER_LIMITED` | 无 | 是 |
| 40300 | `ERR.FORBIDDEN` | 仅注释（`client.ts:23`） | 是 |
| 40400 | `ERR.NOT_FOUND` | 仅裸字面量 | 是 |
| 50000 | `ERR.SYSTEM` | 无 | 是 |
| 51000 | `ERR.DATA_SOURCE` | 无 | **否（死码）** |
| 51001 | `ERR.DATA_EMPTY` | 仅裸字面量 | 是 |
| 52000 | `ERR.TRAIN` | 无 | 是 |
| 52001 | `ERR.INFER` | 无 | 是 |
| 53000 | `ERR.LLM_UNAVAILABLE` | 无（`NlFactorCard.tsx:6` 仅注释） | 是 |
| 53001 | `ERR.EXPR_INVALID` | 无 | **否（死码）** |
| 50001 | *（前端无此常量）* | — | 是（panic 兜底） |

说明：这些码「声明了但不区分处理」本身不产生错误行为（`ApiError.message` 会兜底），
故按死代码计 P3；两条**双端都不可达**（51000/53001）属可安全删除。

---

## 3. 路由 × 最低角色 清单（前端门槛 vs 该页实际调用的端点门槛）

前端门槛取自 `App.tsx`；端点门槛取自本批用脚本对 `backend/app/api/v1/*.py` 全路由提取的
「装饰器 → 下一个 `async def` 段内的 `require_role(...)`」。

| 前端路由 | 页面 | 前端门槛 | 该页调用的端点（后端门槛） | 对账结论 |
|---|---|---|---|---|
| `/`, `/market` | MarketOverview | **公开** | `/market/overview/rt`·`/daily`·`/index/kline`（无鉴权）；顶栏搜索 `/stock/search`(viewer)、`/etf/list`(viewer) | 页面本体与匿名契约一致；顶栏搜索对匿名用户必然静默失败（见 §5-3） |
| `/report` | Report | viewer | `/report/daily`(viewer)；`/report/daily/generate`(**researcher**) | 重新生成对 viewer 必 40300，页面已 try/catch 且注释声明为有意（不计问题） |
| `/stock/:symbol` | StockDetail | viewer | profile/kline/panels(viewer)；`/stock/{s}/predict`(**researcher**) | ✅ 前端已 `canPredict` 门禁（`StockDetail:42,61`） |
| `/screener` | Screener | viewer | `/screener`·`/stocks`·`/watchlist`(viewer)；`/export/screener`(**researcher**) | ❌ #04 |
| `/etf`, `/etf/:code` | Etf / EtfDetail | viewer | `/etf/*`(viewer) | ✅ |
| `/portfolio` | Portfolio | viewer | `/portfolio/search`(viewer)；`/portfolio/backtest`(**researcher**) | ❌ #04 |
| `/data` | DataCenter | viewer | overview/datasets/quality/task-stats/instruments/text-status/mirror-status(viewer)、`/train/readiness`(**无鉴权**)；logs·sync·sync/status·sync/cancel·sync/fetch·sync/auto(GET+POST)·text/import·text/build-factor·mirror/rebuild·train/start·train/status·train/cancel(**researcher**) | ❌ #01 #02 |
| `/watchlist` | Watchlist | viewer | `/watchlist/dashboard`·`/correlation`(viewer)、`/market/quotes`(viewer) | ✅ |
| `/settings` | Settings | viewer | `/settings`·`/preferences`(viewer)、`/settings/engine`(admin，已门禁)；connectors/test·data/sync(**researcher**)、data/cache/clear·db/backup(**admin**) | ❌ #03 |
| `/backtest` | Backtest | researcher | `/backtest/strategy-run`(researcher)、`/export/strategy-backtest`(researcher)、`/datacenter/datasets`(viewer)、`/monitor/*` | ✅ |
| `/research` | Research | researcher | `/research/*`(researcher) | ✅ |
| `/alerts` | Alerts | researcher | `/alerts/*`(researcher)；`/alerts/events/read`(viewer) | ✅ |
| `/studio` | FactorStudio | researcher | `/studio/*`(researcher；`/mining/status/{id}` viewer)、`/studio/nl-to-factor`(researcher) | ✅ |
| `/dataquality` | DataQuality | researcher | `/ops/quality-scan`·`/ops/lineage`(researcher)、`/monitor/health`(viewer)、`/monitor/run`(researcher) | ✅ |
| `/desk` | OrderDesk | researcher | `/desk/*`(researcher) | ✅ |
| `/pipeline` | Pipeline | researcher | `/ops/dag`·`/ops/dag/rerun`(researcher) | ✅ |
| `/capacity` | CapacityAttribution | researcher | `/desk/capacity`·`/desk/attribution`(researcher) | ✅ |
| `*` | NotFound | — | — | ✅ |

附带对账（父审核员线索的收敛）：`/data` 页是**唯一**「viewer 路由 + 大量 researcher 端点」的重灾区；
其余 viewer 路由各泄漏 1–2 个更高权限端点。反向（前端严于后端）只有一处无风险项：
`/alerts`、`/studio` 等 researcher 路由下的 viewer 级只读端点被前端抬高，属收紧，不构成缺陷。

---

## 4. 死代码清单（全仓 `rg` 核对）

| 位置 | 分类 | 依据 | 建议 |
|---|---|---|---|
| `types/stock.ts:315 MarketHeat` | 真死 | 全仓 1 处引用（自身） | 删除（已被 `HeatBlock` 取代） |
| `types/stock.ts:352 MarketMoneyFlow` | 真死 | 全仓 1 处引用（自身） | 删除（原因见 #07） |
| `types/stock.ts:546 ApiResponseOf<T>` | 真死 | 全仓 1 处引用（自身） | 删除 |
| `components/ui/index.tsx:86 ScoreBadge` | 真死 | 0 处引用 | 删除 |
| `components/ui/index.tsx:141 PctCell` | 真死 | 0 处引用 | 删除 |
| `components/ui/index.tsx:76 DegradedBadge` | 真死 | 0 处引用，且 `reason` 只判真值不显示内容 | 删除 |
| `stores/useUiStore.ts:16 period` / `:21 setPeriod` | 真死 | 唯一消费者 `StockDetail:39` 只取 `adjust/setAdjust/setCurrentSymbol/dateRange`；`KLineChart.tsx:268` 另有 local `period` | 删除（含 persist 字段） |
| `stores/useUiStore.ts:19 setRangeDays` | 真死 | 0 处调用 → `rangeDays` 永远只能是持久化初值 365 | 删除或接线 |
| `stores/useWatchlistStore.ts:18/36 removeGroup` | 真死 | 0 处调用（`Watchlist` 只用 `createGroup/addTo/removeFrom`） | 删除 |
| `stores/usePreferencesStore.ts:42 getRefreshIntervalMs` | 真死 | 0 处引用 | 删除 |
| `api/swr.ts:28 REFRESH.snapshot` / `:29 REFRESH.static` | 真死 | 只有 `REFRESH.realtime` 被 `MarketOverview:41` 使用 | 删除或接入页面 |
| `types/api.ts` 中 #10 表里的 12 条 | 真死 | 见 #10 | 逐条删除（先删双端不可达的 51000/53001） |
| `api/export.ts:34 exportApi.backtest` | 未接线（自述） | 0 处调用，注释已声明「前端没有对应页面」 | 保留待接线或删除 |
| `types/etf.ts:92/94 sort_applied/dir_applied` | 未接线（自述） | 0 处读取，注释已声明 | 保留 |
| `components/charts/MarketHeatmap.tsx` | 真死 | 已在父审核员/B9a #15 确认，本批复核仍为 0 引用（含 `HeatBlock` 唯一额外引用） | 删除（不在本报告重复计分） |

补充：`main.tsx`/`RequireAuth.tsx:5` 注释里所述「已删除的旧 `RequireAuth` 默认导出」经复核确实不存在，
属正确清理，无残留。

---

## 5. 未达 P2 的观察（记录，不计入结论表）

1. **`isApiEnvelope` 的宽松判定未找到真实触发路径**（回答父审核员线索）。
   `client.ts:70-77` 只看 `'code' in v && typeof v.code === 'number'`。逐一核对后：唯一的非信封
   端点族是 `/export/*`（`export.py` 用 `response_class=Response`），而它走 `download()` 且
   `responseType:'blob'`（Blob 无 `code` 属性 → 判定为 false，放行，由 `download` 自行解包）；
   `/{symbol}/panels` 里出现的 `code` 字段都在 `data` 内部（嵌套），不会命中顶层判定；
   后端所有 4xx 都被 `errors.py:_http` 包成信封。故**当前无触发**，仅属脆弱启发式。
2. **`useWatchlistStore.addTo` 的回落分支语义错误但不可达**：`addTo('不存在的组', s)` 会以
   `groups[DEFAULT_GROUP]` 为起点**新建**一个同名组并复制默认组全部成员（`:48-52`）。
   全仓唯一调用点是 `StockDetail:158` 的 `addTo(DEFAULT_GROUP, symbol)`，而分组删除只能靠
   已死的 `removeGroup`，故当前不可达；仅当 `aqp-watchlist` 里存在过期分组名时才可能命中。
3. **公开页顶栏搜索对匿名用户恒为空**：`Topbar.tsx:145-148` 调 `/stock/search` 与 `/etf/list`，
   两者都是 viewer 级；未登录时 40100 被 `.catch(() => [])` 吞掉，用户输入「茅台」下拉**始终不出现**
   （`:164 setOpen(items.length > 0)`，也没有「请先登录」或空态提示）。页面本体是公开的，搜索框却是
   登录后才可用。（间接出路：不回车的空结果下按 Enter 会 `navigate('/screener')`（`:126`），
   再被 `RequireRole` 重定向到登录页 —— 但用户在下拉里得不到任何解释。）
4. **`Topbar.tsx:119-120` 裸 6 位数字一律跳 ETF**：`/^\d{6}$/` → `navigate('/etf/'+text)`，
   且 `:135` 对纯数字**主动跳过搜索**（`isDirectCode`），因此输入股票代码 `600519` 进入 ETF 详情页，
   用户无法用裸代码直达个股（`600519.SH` 才行）。股票与 ETF 代码同为 6 位，本应由后端搜索消歧。
5. **`Login` 的 `next` 参数未校验 → 开放重定向**：`Login/index.tsx:54,86` 直接
   `navigate(params.get('next') ?? '/')`。`RequireRole` 侧生成的值是安全的相对路径
   （`encodeURIComponent(pathname+search)`），但手工构造的 `/login?next=//evil.com` 会走到
   react-router 的 `history.push`：`pushState('//evil.com')` 跨域抛 `SecurityError`，
   而 `@remix-run/router/history.ts:648-661` 的 catch 分支执行 `window.location.assign(url)`
   → 登录成功后**整页跳转到外部站点**。属钓鱼/开放重定向（需受害者点击链接并完成登录）。
   建议 `next` 必须满足 `startsWith('/') && !startsWith('//')`。位置跨 B9c（Login），故列在观察区。
6. **`download()` 的两点小瑕疵**：失败时抛的是 `ApiError(message, -2)`（`client.ts:210`），
   丢掉真实业务码与 `trace_id`；`URL.revokeObjectURL` 紧跟在 `anchor.click()` 之后同步调用
   （`:222-224`），Chrome 可用但按 MDN 建议应延迟回收，在部分浏览器上可能下载失败（**疑似**，未验证）。
7. **`client.ts:177 del()` 缺 `options`/`signal`——本批结论：无功能影响**。
   调用方只有 `api/alerts.ts:67 deleteRule` 与 `api/production.ts:65 deleteFactor`，均未传 options，
   也不存在「路由切换时取消」的需求；全局 15s 超时由 axios 实例默认值生效，不会永久挂起。
   属 API 不对称（P3，非 P1/P2），建议补齐签名以保持一致。
8. **`useTaskPolling`/SWR 的轮询与错误语义（已核源码，无缺陷）**：
   `node_modules/swr/dist/use-swr-guqrv3k1.js:621-630` 的 `execute()` 首行即
   `if (!getCache().error && ...) revalidate() else next()` —— 错误态下**只重排定时器、不再发请求**，
   因此 #02 的 40300 不会变成无限请求风暴；终态（`running!==true` 且 `status` 不在
   queued/running/cancel_requested）返回 0 停止；卸载时 `clearTimeout` 清理。
9. **`swr.ts` 请求竞态（回答父审核员线索）**：`useApi` 的 key 为 `[url, params]`，
   `stableHash` 按 key 分桶，不同筛选条件落在不同 key 上，**旧响应不会覆盖新响应**；
   `MarketOverview:56` 的 `mutate(['/api/v1/market/overview/rt', {...}])` 与 `useApi` 的 key 形状一致，
   键匹配正确。真正的竞态在自建 hook `useWatchlistQuotes`（#05）。
10. **401 清会话不会误伤公开页**：`handleAuthFailure` 只 `clear()` 不导航（`client.ts:30-35`）；
    `AUTH_ERROR_CODES` 不含 40300。另核实 `backend` 中**没有任何** `HTTPException(status_code=403)`
   （`rg "status_code=(401|403)"` 无结果），而 `errors.py:136` 会把 HTTP 403 映射成 40100 → 清会话；
   当前无路由走该分支，属潜在坑而非现网缺陷。
11. **重试不会重复执行写操作**：axios 无全局重试；`swrDefaults.errorRetryCount=2` 只作用于 `useApi`
   的 GET；所有写操作走 `post/put/del` 且未被 SWR 包装。
12. **`/datacenter/train/readiness` 后端无任何鉴权**：
   `backend/app/api/v1/datacenter.py:1124-1129` 的 `train_readiness_status()` 连 `Depends(require_auth)` 都没有，
   与「读端点最低 viewer」契约不符（对外暴露 features 目录路径、样本量、设备信息）。
   这是后端缺陷（属 B7b 范围），此处仅登记为交叉引用。