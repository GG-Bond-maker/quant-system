# P2-3 / P2-4 架构级改造与验证

> 日期：2026-09-27 · 状态：**已实施并通过独立复核**
> 相关 commit：`910c3d8`（P2-3）、`3531fa0`（P2-4）、`5d0a879`+`ee1f886`（前序修复补提交）

---

## 一、改造前的具体问题描述

### P2-3 前端无全局重试策略

**位置**：`frontend/src/api/client.ts:90-139`

全项目唯一的 axios 实例只配置了 `timeout: 15000`，响应拦截器（L125-138）对任何错误
都是**直接 `throw new ApiError`，一次失败即终态**。全项目约 109 个 HTTP 调用中，
只有走 SWR `useApi` 的两个端点具备重试（`api/swr.ts:33-39`：`errorRetryCount: 2` /
`errorRetryInterval: 5000`），其余 **107 个调用网络抖一下就必须手动刷新**。

**审计原建议**："对幂等 GET 增加一次指数退避重试；POST 不重试"。

**该建议的问题**：没有区分「前端超时」与「连接失败」两类网络错误。
对本系统而言这是**有害**的——后端 HTTP 恒 200、长任务是真在跑的，前端 15s 断开后
后端仍在计算，此时重试等于**重复提交一次长计算**，会加剧后端负载。因此实施时收窄了口径。

### P2-4 大部分页面卸载时不取消在途请求

**位置**：仅 `pages/Backtest/index.tsx:63-88`、`pages/Portfolio/index.tsx:271-285`（search）、
`pages/Research/index.tsx`（signal）使用了 abort。

DataQuality 的 180s `qualityScan`、Pipeline 的 300s `dagRerun`、DataCenter、
OrderDesk、CapacityAttribution 等长任务，切换路由后请求仍在飞。

**对审计原「影响」描述的勘误**：原文称"前端堆积在途请求会加剧后端排队"——
**这个因果关系不成立**。浏览器 abort 只是断开请求 / 取消 Promise，
**已经在后端开始执行的任务不会停止**（FastAPI handler 照跑到底）。

真实收益只有两条：

1. **释放浏览器并发连接** —— HTTP/1.1 每域名 6 连接上限，180s/300s 长任务占着槽位
   会饿死后续请求（**主要收益**）
2. **避免已卸载组件 setState** —— 多数页面已有 `seqRef` 代际守卫，那只防了「结果覆盖」，
   没防「连接占用」

---

## 二、P2-3 实施方案

### 2.1 重试判定矩阵（核心设计）

判定顺序固定为 **取消 → 超时 → 无响应 → 状态码**。
顺序是关键：因为「取消」和「超时」的 `error.response` 同样为空，
若先判"无响应"就会把它们错误地当成连接失败来重试。

| 场景 | 判定依据 | 是否重试 | 理由 |
|---|---|---|---|
| 连接失败 / 后端未启动 / DNS 失败 | `response` 为空 | ✅ 重试 | 瞬时抖动可恢复，GET 幂等 |
| HTTP 502 / 503 / 504 | `status` | ✅ 重试 | 网关层瞬时故障 |
| **前端超时** | `code === 'ECONNABORTED'` | ❌ **不重试** | 后端任务仍在跑，重试 = 重复提交长计算 |
| **主动取消 / 页面卸载** | `ERR_CANCELED` 或 `signal.aborted` | ❌ 不重试 | 用户已放弃 |
| HTTP 4xx（401/403/404/422） | `status` | ❌ 不重试 | 重试无意义 |
| HTTP 500 | `status` | ❌ 不重试 | 后端真实异常，重试大概率仍 500，且可能重复写 |
| HTTP 200 但 `code !== 0` | 业务码 | ❌ 不重试 | 在成功拦截器已抛 `ApiError`，根本不进错误分支 |
| POST / PUT / DELETE | method | ❌ 不重试 | 避免重复下单、重复触发同步 |

### 2.2 实现要点

- **只重放一次**：用 `config.__aqpRetried = true` 标记，防无限重试
- **退避 600ms ±20% 抖动**（`600 * (0.8 + random()*0.4)`），避免多个失败请求同时重试形成尖峰
- **复用原 config**（保留 params / timeout / signal），重放前**再检查一次 `signal.aborted`**
- **opt-out**：`RequestOptions` 新增 `retry?: boolean`（默认 true），`get()` 透传
- 不引入 `axios-retry` 依赖——所需判定是定制的，第三方库做不到"超时不重试"

### 2.3 叠加风险与消除

SWR 自身 `errorRetryCount: 2`，若与客户端重试叠加，最坏请求次数为
`(1+1) × (1+2) = 6` 次。因此 `api/swr.ts` 的 `fetcher` 改为：

```ts
export const fetcher = <T,>(
  url: string,
  params?: Record<string, unknown>,
  timeout?: number,
): Promise<T> =>
  // 关闭 client 的网络层重试：SWR 自身已配 errorRetryCount: 2 / errorRetryInterval: 5000，
  // 若不 opt-out，两者叠加最坏会放大到 (1+1)×(1+2)=6 次请求。
  get<T>(url, params, timeout, { retry: false });
```

链路闭环：`fetcher` → `get(..., {retry:false})` → 拦截器门限 `config?.retry !== false` 判否
→ 不重试。**走 SWR 的端点最坏请求数由 6 次降回 3 次**（1 + SWR 的 2 次）。

### 2.4 改动面

| 文件 | +/- |
|---|---|
| `frontend/src/api/client.ts` | +99 / −9 |
| `frontend/src/api/swr.ts` | +5 / −1 |

---

## 三、P2-4 实施方案

### 3.1 共享 hook

新增 `frontend/src/hooks/useAbortableTask.ts`（82 行），提供 `begin / finish / isLatest / abort`。

设计要点：

- 卸载 `useEffect` 依赖数组为 `[]`，不会每次渲染都 abort
- 四个方法均为 `useCallback(..., [])` 再用 `useMemo` 包成**稳定引用**——
  否则放进 `useCallback`/`useEffect` 依赖会造成每次渲染重建、下游 `useEffect` 无限循环
- `finish(ctrl)` 判定 `abortRef.current === ctrl`：旧请求晚返回时返回 false，不关 loading
- hook 头注释**显式写明**：浏览器 abort 不停后端任务，UI 文案只能写「中止等待」

### 3.2 四要素规范

每个接入点必须满足：

1. 新一轮请求前 `abort` 上一轮
2. 卸载时 `abort`
3. catch 里 `if (ctrl.signal.aborted) return;` —— **不把取消当错误弹给用户**
4. finally 里只在 `abortRef.current === ctrl` 时才置 `running=false`

### 3.3 接入范围：16 个长任务点

判定标准：**单次请求可能 > 30s**，或页面有长轮询且切路由后仍在跑。

| # | 接入点 | 文件 | 超时 |
|---|---|---|---|
| 1 | DataQuality `runScan` | `pages/DataQuality/index.tsx:115` | 180s |
| 2 | DataQuality `loadLineage` | 同上 `:131` | 60s |
| 3 | FactorHealthCard `run` | `components/FactorHealthCard.tsx:35` | 120s |
| 4 | Pipeline `load`(dag) | `pages/Pipeline/index.tsx:57` | 60s |
| 5 | Pipeline `rerun` | 同上 `:71` | **300s** |
| 6 | CapacityAttribution `runAttr` | `pages/CapacityAttribution/index.tsx:64` | 180s |
| 7 | DataCenter `loadAll` | `pages/DataCenter/index.tsx:477` | 60/90/90s |
| 8 | TextDataPanel `load` | `pages/DataCenter/TextDataPanel.tsx:44` | 45s |
| 9 | TextDataPanel `run` | 同上 `:57` | 60s |
| 10 | SignalAnalysisPanel `run` | `pages/Backtest/SignalAnalysisPanel.tsx:34` | 120s |
| 11 | FactorStudio `runEval` | `pages/FactorStudio/index.tsx:142` | 120s |
| 12 | NlFactorCard `run` | `pages/FactorStudio/NlFactorCard.tsx:31` | 120s |
| 13 | FactorLab `save` | `pages/FactorStudio/FactorLab.tsx:47` | 120s |
| 14 | FactorLab `runReport` | 同上 `:69` | 120s |
| 15 | Portfolio `run` | `pages/Portfolio/index.tsx:313` | 120s |
| 16 | Report `regenerate` | `pages/Report/index.tsx:71` | 60s |

**排查中发现的一处实现遗漏**：`Backtest/SignalAnalysisPanel.tsx` 的 `signalAnalysis`（120s）——
`api/backtest.ts:66-67` **早已接受 `signal` 参数，页面就是没传**，且该面板是 Tab 形式、
切 Tab 即卸载。属价值最高的修复点。

### 3.4 评估后决定不改的清单

| 页面 / 对象 | 最长 timeout | 理由 |
|---|---|---|
| `pages/MarketOverview` | 120s（名义） | 后端已把冷路径收敛到 6s 服务端预算；走 SWR（无 abort 支持），接入须改共享 `api/swr.ts` fetcher，影响全部 SWR 消费者，收益/风险不成比例 |
| `pages/OrderDesk` | 30s | 恰为 30s **边界**而非 >30s；16s 轮询已在卸载时 `clearInterval` |
| `Etf` / `EtfDetail` / `Screener` / `StockDetail` / `Watchlist` | ≤30s | 不满足 >30s |
| `pages/Settings` | 60s | `settingsApi.all()` 是挂载期一次性聚合读取（≈边界）；`backupDb` 低频且页面常驻 |
| 各页轮询（DataCenter 1.5s / FactorStudio 2s / OrderDesk 16s） | 15s 默认 | 定时器均在卸载时 `clearInterval`，不构成连接饥饿 |
| `pages/Research`、`pages/Backtest`、`Backtest/TopKPanel` | 120~600s | **已有等效实现**（含并发队列 + 代际守卫） |

### 3.5 遗留项

1. **`client.download()` 不支持 signal**（`api/client.ts:203-236`，timeout 180s）→
   三个 180s 导出无法中断。属独立遗留项
2. **`del` 不支持 signal**（`api/client.ts:188-191`）→ 本轮无 `del` 触发的长任务，
   但一旦用于长任务即无法中断
3. Pipeline 页不提示"后端可能仍有任务在跑"——`dag.recent_jobs` 的 RUNNING 行虽展示
   但**不门控按钮**。属既有独立改进项，非 P2-4 引入

---

## 四、独立验证结果（QA 重建，不采信自报数据）

> P2-3 的工程师自报"28/28 通过"，但**探针脚本跑完即删、未入库**，无法复核。
> QA 独立重建了验证脚手架（esbuild 打包**真实** `client.ts` + 桩 axios adapter），
> 断言集与工程师的不同，结论一致。

### 4.1 P2-3 运行时断言（28/28 PASS）

```
PASS | A1  无响应(连接失败) → GET 调用 2 次                    calls=2  718ms
PASS | A2  无响应且重试仍失败 → 抛 ApiError code===-1          calls=2  533ms
PASS | A3  无响应后重试成功 → 返回业务数据(解包正常)            calls=2  674ms  val={"v":42}
PASS | A4  status=503 / 502 / 504 → 各调用 2 次                 calls=2
PASS | A5  status=500 / 401 / 403 / 404 → 各调用 1 次           calls=1
PASS | A6  ECONNABORTED(超时) → 调用 1 次，文案=请求超时，请稍后重试  calls=1
PASS | A7  ERR_CANCELED → 调用 1 次                             calls=1
PASS | A8  飞行中 abort(无 response) → 调用 1 次，不重试         calls=1
PASS | A8b 拦截器 signal.aborted 分支 → 调用 1 次                calls=1
PASS | A9  retry:false → 调用 1 次（opt-out 生效）               calls=1
PASS | A10 POST 无响应 → 调用 1 次（非 GET/HEAD 不重试）         calls=1
PASS | A11 HTTP200 但 code!==0 → 调用 1 次，抛 ApiError(40000)   calls=1
PASS | A12 退避确实发生（两次调用时间差 ≈600ms，区间 480~720ms）  634ms

判定顺序单测（直接调用 isRetryableNetworkError）：10/10 PASS
===== 汇总: 28/28 PASS =====
```

A8/A8b 是最关键的验证：证明**判定顺序正确**，abort 不会被误当成连接失败重试。

### 4.2 P2-4 接入点复核（16/16 全查，非抽样）

- **13 处四要素齐全**；3 处（#4 Pipeline dag、#7 DataCenter loadAll、#8 TextDataPanel load）
  第 ④ 要素不适用——**该请求本就没有 loading state**（用 `data===null` 判定加载中）。
  判定正确，未为凑要素而新增 state（那属行为变更）
- **16/16 的 signal 真传到了 api 层**；逐函数核实 `api/datacenter.ts`（6 个函数）、
  `api/monitor.ts`（3 个）、`api/portfolio.ts`、`api/production.ts`（8 个）、
  `api/backtest.ts`（本就支持）**无漏透传**
- **16/16 的 catch 都在 `setErr` 前做 `if (ctrl.signal.aborted) return`**，
  **无一处把取消当错误弹窗**
- hook 稳定引用经 6 处使用方抽查确认，无无限循环

### 4.3 硬红线：误导性文案

全仓搜索"取消任务/已取消/终止/停止"命中 4 处，**逐条判定均非 P2-4 引入、且均非误导**：

| 命中 | 判定 |
|---|---|
| `hooks/useAbortableTask.ts:10` | 是"禁止宣称已取消"的**禁令注释本身** |
| `api/production.ts:249-253` | `killSwitch` 是**真实后端撤销**端点，与浏览器 abort 无关 |
| `pages/DataCenter/index.tsx:56` | `cancelled:{label:'已取消'}` 映射**后端 task_store 真实状态**（真端点 `/datacenter/sync/cancel` 产生） |
| `pages/FactorStudio/index.tsx:298` | "取消任务（当前代完成后退出）"——真后端端点，且文案**已明说不是立即杀**；既有代码，不在 P2-4 diff 内 |

**P2-4 未新增任何用户可见的取消类文案**，不存在误导风险。

### 4.4 专项评估：Pipeline `dagRerun` 是否会加剧重复触发？

**结论：不会加剧，不构成回归。** 代码依据：

1. `busy` 是**组件局部 state**（`pages/Pipeline/index.tsx:50` `useState(false)`）。
   路由是 react-router v6 声明式（`App.tsx:108`），**切路由即卸载组件，`busy` 随实例销毁**；
   切回来是全新挂载，`busy` 回到 `false`
2. 按钮唯一门控就是 `busy`（`:126` `disabled={busy}`、`:132` 文案切换）。
   **没有**任何基于 `dag.recent_jobs` RUNNING 状态的门控
3. 因此**改动前**：点重跑 → 切走（旧代码无 abort，请求继续飞）→ 切回
   （新挂载、`busy=false`、按钮可点）→ 再点 → 后端槽位仍被第一条占用 → `ERR_PIPELINE_BUSY`。
   **与改动后逐字相同**
4. 用户若**留在本页**，P2-4 根本不会 abort（只在卸载或新 `begin()` 时触发），
   按钮保持 disabled + "流水线运行中…"，同样无法重复点

原担忧"改动前用户看到运行中不会重复点"**只在组件仍挂载时成立**；
一旦切走，改动前同样显示空闲。故**不加剧**。

### 4.5 回归门禁

| 项 | 结果 |
|---|---|
| `npx tsc --noEmit` | ✅ exit 0，0 error |
| `npx vite build --outDir dist-qa-p2x` | ✅ `✓ built`，exit 0 |
| 后端全量 pytest | 见主报告（本轮改动为纯前端，未触碰后端） |

---

## 五、提交归属说明（需知悉）

P2-4 的 commit `3531fa0` **混入了约 200 行另一条并行工作线的未提交改动**
（4 个文件：`api/production.ts`、`api/datacenter.ts`、`pages/DataCenter/index.tsx`、
`pages/DataQuality/index.tsx`）。

**处置**：QA 已逐文件读全并确认**这些混入改动逻辑自洽、无悬空符号、可编译、未被破坏**
（内容为 DataCenter task_id 持久化、desk orders 信封、lineage/dag/textBuildFactor 超时放宽等）。
因拆分需交互式 hunk 编辑、风险高于收益，**未做 reset 拆分**，现状保留。

如需拆分，可执行 `git reset --soft HEAD~1` 后重新分组提交（工作区文件不受影响）。

**同时补提交了此前一直未入库的前序审计修复**：

| commit | 内容 |
|---|---|
| `5d0a879` | 后端 16 文件：lineage SWR 缓存 + single-flight、错误码语义三层治理、rotate 端点删除、desk orders 信封 |
| `ee1f886` | 前端 11 文件：超时预算对齐、预警规则编辑 UI 补全、错误可见性、死包装清理 |

补提交前这些修复**仅存在于工作区**，存在丢失风险；现已全部入库，工作区干净。

---

## 六、结论

| 项 | 状态 |
|---|---|
| P2-3 幂等 GET 受限重试 | ✅ 已实施，28/28 独立验证 PASS |
| P2-4 长任务页面请求取消 | ✅ 已实施，16/16 独立复核 PASS |
| SWR 重试叠加风险 | ✅ 已消除（最坏 6 次 → 3 次） |
| 误导性文案 | ✅ 未发现 |
| Pipeline 重复触发回归 | ✅ 经代码证明不构成回归 |
| 提交归属污染 | ⚠️ `3531fa0` 混入约 200 行他人改动，内容完好，未拆分 |
