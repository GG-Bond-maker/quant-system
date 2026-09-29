# B9c · 前端 · 数据中心 / 运维 / 设置 / 预警 / 订单页（审核报告）

审核日期：2026-09-18
审核范围（逐个读完）：
`frontend/src/pages/DataCenter/{index.tsx,TextDataPanel.tsx,TrainPanel.tsx}`、`pages/DataQuality/index.tsx`、
`pages/Pipeline/index.tsx`、`pages/Alerts/index.tsx`、`pages/OrderDesk/index.tsx`、`pages/Settings/index.tsx`、
`pages/Login/index.tsx`、`components/{RequireAuth.tsx,AuthBootstrap.tsx}`、
`api/{datacenter,production,alerts,notify,settings,auth}.ts`、`stores/{useNotifyStore,useAuthStore,usePreferencesStore}.ts`、
`hooks/useTaskPolling.ts`、`types/datacenter.ts`。

跨层只读核对（`rg` / 逐段读）：`backend/app/api/v1/{datacenter,app_settings,alerts,desk,ops,notify,auth}.py`、
`backend/app/core/{auth,errors,events,config}.py`、`backend/app/services/{sync_service}.py`、
`backend/app/ml/train_service.py`、`backend/app/trading/paper.py`、`backend/app/orchestrator.py`、
`frontend/src/api/client.ts`、`frontend/src/App.tsx`、`frontend/src/utils/useChart.ts`、
`frontend/node_modules/react-dom/cjs/react-dom.development.js`（React 18.3.1 源码，用于判定 #1）。

前置事实复用：`B7b-api2.md` 的 F1 / F4 / F9 / F10 / F12 / F14 / F15。

## 0. 运行 / 验证基线（本批实际执行）

| 命令（只读，未启动 dev server、未改任何源码） | 结果 |
| --- | --- |
| `cd frontend/src; rg -n "hasMinimumRole" .` | 仅 `components/RequireAuth.tsx:20/45`（定义 + 守卫内部）、`pages/StockDetail/index.tsx:42`。**B9c 全部页面 0 处按角色禁用入口** |
| `rg -n "cacheDays" frontend/src` | 3 处：`Settings/index.tsx:163`（state）、`:603`（文案）、`:605`（滑条）。**无任何 API 调用消费它** |
| `rg -n "updateRule\|datacenterApi\.task\(" frontend/src` | 仅 `api/alerts.ts:64`（定义）。`datacenterApi.task(` **0 命中** → 两者无调用方 |
| `rg -n "truncated\|failed_count\|recent_truncated\|supplement_limit\|last_error" frontend/src` | **0 命中**（exit 1）→ 后端 4 处截断/失败数披露在前端无任何消费位 |
| `rg -n "min_password_length\|username_pattern\|default_role" frontend/src` | 仅 `api/auth.ts:30-32` 类型声明 → 3 个字段从未被读取 |
| `rg -n "require_role" backend/app/api/v1/datacenter.py` | `logs/sync/status/sync/auto/sync/fetch/text/import/text/build-factor/mirror/rebuild/train/*` = **researcher**；`overview/datasets/quality/task-stats/instruments/text/status/mirror/status` = viewer |
| `rg -n "require_role" backend/app/api/v1/app_settings.py` | `141 GET /settings`、`179 /preferences` = viewer；`238 /connectors/test`、`269 /data/sync` = **researcher**；`199 /engine`、`256 apikeys`、`279 /data/cache/clear`、`309 /db/backup` = **admin** |
| `rg -n "limit" backend/app/api/v1/desk.py` | `:97 .limit(200)`（禁买池）、`:212 limit: int = 50`（母单）→ 均无 total / truncated |
| `rg -n -A3 "function setDefaultValue" frontend/node_modules/react-dom/cjs/react-dom.development.js` | `updateWrapper`（`:1854-1855`）在每次更新都对 `defaultValue` 调用 `setDefaultValue` → **非受控 input 的 defaultValue 变化会写回 DOM**，据此否证了「Pipeline 日期框永远为空」的初判（见 §4.1） |
| `rg -n "ErrorBoundary" frontend/src` | `main.tsx:12` 根级存在 → 渲染异常会整站降级为错误面板（#1 的后果边界） |
| `npx tsc --noEmit --noUnusedLocals --noUnusedParameters` | **未重复执行**（父审核员基线 = 0 错误） |

> 说明：本批全部结论来自静态阅读 + `rg` 只读核对，**未运行前端/Cypress/pytest**，未修改任何源码。
> 按 AUDIT-BRIEF §0，凡只能推理的条目已标注「疑似」。

## 1. 逐文件结论行

| 文件 | 结论 |
| --- | --- |
| `pages/DataCenter/index.tsx` | **4 项**：#1 条件调用 hooks（白屏风险）、#2「可抓取 N 只」拿截断值冒充全量、#11 autoSync 复选框硬编码 `true` 且失败静默、#15 `modeLabel['fetch']`→`undefined执行中…`；另有 #12 页面级越权入口、#19 死分支 |
| `pages/DataCenter/TrainPanel.tsx` | 无角色门槛（#12，F15 延伸）；轮询/终态/取消/门禁清单正确；`{epochs}` 与 `_ALLOWED_PARAMS` 两模型均含 ✅ |
| `pages/DataCenter/TextDataPanel.tsx` | 无角色门槛（#12，F15 延伸）：viewer 可见 3 个 researcher 级写按钮；状态读取（viewer 级）正确 ✅ |
| `pages/DataQuality/index.tsx` | **#6**：`ops.py` 的 200 只硬截断 + `year=2026` 硬编码被当成「扫描范围 / 全部通过」展示；血缘图谱的 `degraded_note`/`status` 消费正确 ✅ |
| `pages/Pipeline/index.tsx` | **#7** DAG 图渲染后端不存在的 `desk` 节点；**#8** RUNNING/PENDING 染红 + `0.0s`；**#14** 日期框清空后点击静默无操作；`dagRerun` 的 `ok=false` 分支消费正确 ✅ |
| `pages/Alerts/index.tsx` | **#4** 未读徽标只统计 50 条窗口、「全部已读」非全部（后端支持 `all:true` 未用）；**#5** `data_health` 事件的 `recent_truncated/truncated` 披露被丢弃；参数白名单与 `_PARAM_KEYS` 逐字段一致 ✅ |
| `pages/OrderDesk/index.tsx` | **#3** 母单列表静默截断 50（客户端分页/搜索/计数）；**#20** 熔断状态读取失败时显示绿色「正常运行」；#18「自动刷新（8s）」与 `refresh_freq×2` 不符；熔断二次确认 ✅ |
| `pages/Settings/index.tsx` | **#9** admin/researcher 级写操作未按角色门控（engine 卡片却有门控，自相矛盾）；**#10** `cacheDays` 滑条完全未接线；#21 刷新频率下拉三项同标签「管理配置」 |
| `pages/Login/index.tsx` | 登录/注册/跳转/错误展示正确；#17 `register/status` 的 `default_role/min_password_length/username_pattern` 全未消费（硬编码 6 位 / 「只读（viewer）」） |
| `components/RequireAuth.tsx` | 未发现 P0–P2；`RequireRole` 的过期判断与后端一致；旧 default export 确已删除（无残留） |
| `components/AuthBootstrap.tsx` | 未发现 P0–P2；仅在 `UNAUTHORIZED/TOKEN_EXPIRED/INVALID_TOKEN` 时清会话 ✅ |
| `stores/useAuthStore.ts` | **#13** 把 `AQP_ADMIN_TOKEN` 迁移为持久化 admin 会话（违反 P0 契约第 4 条） |
| `stores/useNotifyStore.ts` | **未发现 P0–P2**：每次建连都重取一次性 ticket、`onerror` 先 `close()` 阻断浏览器自动重连、generation 守卫丢弃旧换票、`close()` 在登出/卸载调用、退避 1s/2s/4s；仅「3 次失败后本次会话不再自动重连」为 P3 观察（UI 已披露） |
| `stores/usePreferencesStore.ts` | `DEFAULT_REFRESH_SEC = 5` 与后端 `DEFAULTS.refresh_freq = 3` 两个事实源不一致（#18 的成因，P3） |
| `hooks/useTaskPolling.ts` | 未发现 P0–P2：终态（`running=false` / 非 queued/running/cancel_requested）返回 `refreshInterval=0` 停止轮询 ✅ |
| `api/datacenter.ts` `api/production.ts` `api/alerts.ts` `api/settings.ts` `api/auth.ts` | 请求参数名/字段名与后端逐项核对**一致**（见 §2）；`datacenterApi.task` / `alertsApi.updateRule` 无调用方（#16）；`alertsApi.events` 返回类型是裸数组，与后端 `APIResponse[list]` 一致 ✅ |
| `types/datacenter.ts` | `SyncStatus.mode: SyncMode \| null` **与后端实际值不符**（后端可为 `"fetch"`，见 #15）；缺 `failed_count`/`finished_at`（#19） |

## 2. 跨层对账：参数与字段名（重点项，结论：一致）

| 前端发送 / 读取 | 后端 | 结论 |
| --- | --- | --- |
| `overview(refresh)` → `{refresh:1}` | `datacenter.py:367 refresh: int = Query(0, ge=0, le=1)` | ✅ 名称/值域一致 |
| `quality(limit)` / `logs(limit)` / `instruments(asset_type,limit)` / `sync(mode,symbols,resume)` / `fetch({asset_type,start,end,symbols,limit})` | `datacenter.py:553/593/999/676/916-921` | ✅ 全一致 |
| `textImport({docs,source})`、`textBuildFactor()`、`mirrorRebuild({})`、`trainStart({model,params:{epochs}})`、`trainCancel({})` | `datacenter.py:1022-1026/1051/1084/1132/1160`；`ml/train_service.py:363-364` 两模型白名单均含 `epochs` | ✅ 一致 |
| `desk.placeOrder({symbol,side,order_amount,algo,split_days,participation_cap})`、`addExclusion(symbol,category,reason)`、`setKillSwitch(active,reason)` | `desk.py:178/111/72` | ✅ 一致 |
| `ops.qualityScan({dataset})`、`dagRerun({trade_date})` | `ops.py:30/429-431`（`year` 缺省后端硬编码 2026 = 已知 F12） | ✅ 名称一致；#6 由此引出 |
| `alerts.createRule(rule_type/scope/symbol/params/channels/cooldown_minutes/enabled)` | `alerts.py:45-52 _PARAM_KEYS` 逐键一致（`threshold/price/direction/window/k/factor/quantile/metric`） | ✅ |
| `settings.savePreferences(全量 prefs)`、`saveEngine`、`testConnector({connector})`、`clearCache({})`、`backupDb({})`、`syncDaily({})` | `app_settings.py:172-177 PreferencesIn`（全字段可空）、`:197`、`:236`、`:277`、`:307`、`:267` | ✅ 一致 |
| 事件 payload 读取键 `pct/pct_threshold…` | `alerts.py:555`（`pct`+`threshold_pct`）、`:571-573`、`:594-596`、`:613-617`、`:668-670`、`:417/:481` | ✅ 全部命中，无 NaN 来源（除历史遗留 payload） |

**角色—页面一致性（本批核心）**：`App.tsx:97 /data`、`:99 /settings` 均为 `RequireRole minimum="viewer"`，而两页内嵌大量 researcher/admin 级端点调用 → 见 #9 / #12（F15 同类，本批新增页面级证据）。

## 3. 问题详述

### #1 [P2][Bug][疑似] `DiskGauge` 在 null 分支直接 return，hooks 调用次数随 props 变化（React 会整站降级）
位置：`frontend/src/pages/DataCenter/index.tsx:70-110`（挂载点 `:614`）

```tsx
70: function DiskGauge({ percent, storageGb }: { percent: number | null; storageGb?: number }) {
71:   if (percent == null) {
72-80:   return (<div>…磁盘信息不可用…</div>);      // ← 0 个 hook
81:   }
82:   const option = useMemo<echarts.EChartsOption>(() => ({ … }), [percent]);   // ← hook #1
110:  const { ref } = useChart(option, 130);   // ← 本地 useChart(:52-66) = useRef + useEffect（hook #2-#3）
```
`percent == null` 与 `percent` 为数值是**同一个组件实例的两种渲染**（`DiskGauge` 位置固定，无 `key`），
因此当 `overview.disk_usage_percent` 在一次挂载内由 `null` 变为数值（或反向）时，hooks 数量从 3 变 0，
React 抛 `Rendered more/fewer hooks than during the previous render`。项目根级 `ErrorBoundary`（`main.tsx:12`）
会接住并**把整站替换为错误面板**（不是单卡降级）；`types/stock.ts:503` 记录了同类渲染异常曾经发生过一次。

触发条件（疑似，需一次真实翻转）：磁盘查询失败时 `_disk_usage_percent` 返回 `None`
（`datacenter.py:480-484` 明确「获取失败返回 None，绝不静默返回 0.0」），随后的 `刷新看板`/`loadAll(true)`
（`:553`/`:561`）或 120s 缓存过期重建（`datacenter.py:469-476`）返回数值。
最小验证：用 stub 让 `/api/v1/datacenter/overview` 第一次返回 `disk_usage_percent: null`、第二次返回 `42.0`，
访问 `/data` → 断言 ErrorBoundary 面板出现。
诚实边界：正常 Windows 本地盘上 `disk_usage_percent` 稳定为非 null，因此**可达性未实测**；
但「条件调用 hooks」本身是确定性代码缺陷，且同批其余组件（`LatencyChart:126-170`、
`utils/useChart.ts`）都把 hook 放在 early return 之前，属于漏改。

### #2 [P2][一致性][确定] 自定义抓取面板把「截断后的 total」当全量展示：「可抓取 50 只」「… 等 50 只」
位置：`frontend/src/pages/DataCenter/index.tsx:269-271`、`:306-308`、`:343-347`
```tsx
269: void datacenterApi.instruments(assetType, 50).then((r) => { setPreview(r.items); setPreviewTotal(r.total); })
306: {previewTotal > 0 ? `可抓取 ${previewTotal} 只` : ''}
346: {previewTotal > 5 ? ` … 等 ${previewTotal} 只` : ''}
```
后端 `datacenter.py:997-1015`：
```python
async def list_instruments(asset_type: str = "stock", limit: int = 50, ...):
    rows = conn.execute("SELECT symbol, name, instrument_type FROM instrument … LIMIT ?", (limit,)).fetchall()
    return ok({"items": [...], "total": len(rows)})      # ← total = 被截断后的行数
```
`total` 是「本页返回行数」而非 instrument 表总数，于是面板恒定显示「可抓取 50 只」/「… 等 50 只」，
而同一面板 `:327` 又写着「（留空抓取全部）」。用户实际有 ~数千只标的（`overview.covered_total` 同页展示收录目标）。
这是 B7b F10「`/instruments` 截断零披露」在前端的直接后果：**截断值被当成人口数**。
最小验证：`curl -s '/api/v1/datacenter/instruments?asset_type=stock&limit=50' | jq '.data.total'` → 恒为 50；
同时 `curl -s '/api/v1/datacenter/overview' | jq '.data.covered_total'` → 数千 → 两数并列即证。

### #3 [P2][一致性][确定] 执行中心母单列表静默截断 50 条，客户端分页/搜索/计数全部建立在截断窗口上
位置：`frontend/src/pages/OrderDesk/index.tsx:123-134`（`filteredOrders`/`totalPages`/`pagedOrders`）、`:403`（计数文案）、`api/production.ts:249`（`orders: () => get<PaperOrder[]>('/api/v1/desk/orders')` 不传 limit）
后端 `desk.py:210-219`：`limit: int = 50` 且 `ORDER BY id DESC LIMIT limit`，响应是裸数组（无 total / truncated）。
因此累计下单超过 50 笔后：面板「母单 / 子单监控」只显示最近 50 笔，
`:403` 的 `{filteredOrders.length} 笔`、搜索、状态/方向筛选、`orderPage` 分页**全部只在这 50 笔内**，
用户看到的是一个自称完整、实际缺尾的列表（B7b F10 的另一条零披露端点）。
最小验证：连续 `POST /api/v1/desk/orders` 造 60 笔 → 打开 `/desk`，页脚计数最大 50，翻页也到不了第 51 笔。

### #4 [P2][一致性][确定] 预警「N 条未读」只统计 50 条窗口，「全部已读」并非全部
位置：`frontend/src/pages/Alerts/index.tsx:200`、`:212`（`alertsApi.events(false, 50)`）、`:218`（`unreadCount`）、`:248-253`（`markAllRead`）
```tsx
218: const unreadCount = useMemo(() => (events ?? []).filter((e) => !e.is_read).length, [events]);
249: const ids = (events ?? []).filter((e) => !e.is_read).map((e) => e.id);
251: await alertsApi.markRead(ids);
```
后端 `alerts.py:247`（`limit: int = Query(50, ge=1, le=200)`）+ `:255`（`ORDER BY id DESC LIMIT limit`）：
「最近 50 条」是硬窗口，且 `/events/read`（`:269-276`）已支持 `{all: true}`，前端未使用。
触发条件：事件总数 > 50（多规则 + 30s 级触发很常见）。此时若最近 50 条均已读而更早有未读，
徽标显示 **0 条未读**（用户以为清空了），点击「全部已读」也只覆盖这 50 条 → **系统性少计 + 假清零**。
最小验证：造 60 条事件并把其中较早的 10 条置为未读、最近 50 条置为已读 → 打开 `/alerts` 观察徽标不显示未读。

### #5 [P2][一致性][确定] `data_health` 事件的截断披露被 UI 丢弃，饱和的 `failed_jobs` 被当精确值
位置：`frontend/src/pages/Alerts/index.tsx:167-170`
```tsx
167: } else if (p.rule_type === 'data_health') {
168:   bits.push(p.metric === 'disk' ? `磁盘 ${Number(p.usage_percent).toFixed(1)}%`
169:     : p.metric === 'pipeline' ? `流水线失败 ×${p.failed_jobs}`
170:     : '数据源降级');
```
后端 `alerts.py:481-491`（全仓最规范的一处披露）：
```python
entry = {"metric": "pipeline", "failed_jobs": len(failed), "last_error": failed[0][1][:120]}
if recent_truncated:   entry["recent_truncated"] = True; entry["recent_limit"] = PIPELINE_RECENT_LIMIT   # =5
if supplement_truncated: entry["truncated"] = True;      entry["supplement_limit"] = PIPELINE_SUPPLEMENT_LIMIT  # =20
```
`rg -n "truncated" frontend/src` → **0 命中**：`recent_truncated / truncated / recent_limit / supplement_limit`
四个字段在前端无任何消费位，`last_error` 也没展示。
触发条件：24h 内失败/残留行数超过 5（主查询上限）或超过 20（补捞上限）——正是后端注释里「实测 100 条只报 5」
的场景；用户看到「流水线失败 ×5」，会读成「最近只有 5 条失败」。
最小验证：向 `data_jobs` 造 30 条 FAILED 后触发一次 `pipeline` 规则，比较 payload（含 `recent_truncated`）
与页面文案（只有 ×5）。

### #6 [P2][一致性][确定] 数据质量页把 200 只硬截断 + 硬编码年份当成「扫描范围 / 全部通过」（F4、F12 的前端延伸）
位置：`frontend/src/pages/DataQuality/index.tsx:110`（不传 year）、`:137-138`（「扫描范围 N 只 · year · N 行」）、`:154-155`（`全部通过 ✓`）、`:186-188`（`未检出任何质量问题（阈值口径见 data/quality.py）`）
后端 `ops.py:44-52`：`sym_dirs = sorted(...)` → `for sym_dir in sym_dirs[:200]`（全市场约 2500 只 → 覆盖 ~8%），
`:47 year = req.year or 2026`，`:78 samples = issues[:50]`；三处截断在响应里**没有任何字段**可披露
（响应只有 `dataset/year/rows_scanned/symbols_scanned/n_issues/n_errors/by_kind/worst_symbols/samples`）。
前端也没有任何「覆盖不足」守卫：`n_issues == 0` 时直接渲染绿色「未检出任何质量问题」，`:137` 把
`symbols_scanned`（= 截断后的 200）标成「扫描范围」。
触发条件 A（覆盖 8%，P2）：任何一次点击「重新扫描 daily_bar」都会只扫前 200 个 symbol 目录。
触发条件 B（假全通过，P2）：`year=2026` 硬编码（F12）在 2027 年运行时该年分区不存在 →
`symbols_scanned = 0 / rows_scanned = 0 / samples = []` → 页面显示「扫描范围 0 只」「未检出任何质量问题（✓）」，
即**零覆盖的全通过**。
最小验证：`python -c "from app.api.v1 import ops"` 侧改用 `req.year=2027` 直调 `POST /ops/quality-scan`
→ 观察 `symbols_scanned=0, samples=[]`，页面仍为绿色通过。

### #7 [P2][一致性][确定] 数据管线 DAG 图固定渲染后端**不存在**的 `desk` 节点（永久琥珀「待更新」）
位置：`frontend/src/pages/Pipeline/index.tsx:8`、`:13-24`
```tsx
8:  const STAGE_ORDER = ['harvest', 'qc', 'features', 'infer', 'screener', 'desk'];
14:   const st = dag.stages.find((s) => s.id === id);
15:   const name = st?.name ?? id;            // → 'desk'
16:   const ok = st?.status === 'ok';         // → false（永远）
23:   <div className="text-ink-secondary">{st?.dataset ?? id}</div>   // → 'desk'
24:   <div className="num">{st?.done_date ?? '—'}</div>               // → '—'
```
后端 `ops.py:387-398` 的 `stages` 只有 5 个 id（harvest/qc/features/infer/screener），**没有 desk**
（desk 是 SQLite 业务表，在血缘图里是 `prod` 组节点，不是 DAG stage）。
于是图里恒多一个琥珀色「desk / desk / —」方块，面板标题却是「节点状态 = 数据产物真实新鲜度」→
用户会认为调仓环节长期未就绪。同一页 `:70-88` 的明细网格用的是 `dag.stages.map`（5 个）+
一张手写的「调仓单（执行中心）」卡片，证明作者知道 desk 不是 stage，`STAGE_ORDER` 的第 6 项是漏改残留。
最小验证：打开 `/pipeline`，对比 `curl -s /api/v1/ops/dag | jq '.data.stages[].id'`（5 项）与图上节点数（6 个）。

### #8 [P2][一致性][确定] 「最近流水线任务」把 RUNNING/PENDING 行染成红色失败态，且耗时显示 `0.0s`
位置：`frontend/src/pages/Pipeline/index.tsx:131-134`
```tsx
131: <td className={`text-center ${j.status === 'SUCCESS' ? 'text-emerald-600' : 'text-red-600'}`}>{j.status}</td>
134: <td className="num text-center">{(j.duration_ms / 1000).toFixed(1)}s</td>
```
后端状态词表是 `PENDING / RUNNING / SUCCESS / FAILED`（`orchestrator.py:58`、`:62`、`:75-77`），
且 `/dag` 的查询（`ops.py:415-419`）`ORDER BY id DESC LIMIT 10` **不过滤状态** → 正在跑的任务必定出现在表里。
于是流水线执行期间：该行以红色显示（红色在本项目一律表示失败），`duration_ms` 尚未写入（NULL）→
`null / 1000 → 0` → 右列显示「0.0s」。用户在监控表上读到的是「一条失败且瞬时完成的任务」。
最小验证：跑一次 `POST /api/v1/ops/dag/rerun`，在其执行中打开 `/pipeline`，观察最新行为红色 `RUNNING` + `0.0s`。

### #9 [P2][策略][确定] `/settings`（viewer 级）未按角色门控 admin/researcher 写操作，而同页 engine 卡片却有门控
位置：`frontend/src/pages/Settings/index.tsx:588`（立即同步）、`:609-612`（清理所有缓存）、`:618-621`（备份所有策略数据）、`:414/440`（测试连接）、`:467`（刷新所有连接数据）、`:196`（挂载即静默 `testAll()`）；对照 `:477 {isAdmin && (<Card title="量化引擎设置">)`
后端：`app_settings.py:269 /data/sync` = researcher、`:238 /connectors/test` = researcher、`:279 /data/cache/clear` = admin、`:309 /db/backup` = admin；`App.tsx:99` 只要求 `viewer`。
证据（本批实测）：`rg -n "hasMinimumRole" frontend/src` → `Settings/index.tsx` **0 命中**。
触发条件与现象：viewer/researcher 打开 `/settings` →
① 挂载即发两次 `POST /connectors/test` → 40300，被 `testAll` 的 `allSettled` 静默吞掉，状态徽标停在「未检测」；
② 点「清理所有缓存」→ 有二次确认 Modal（`ConfirmModal` 做对了）→ 确认后 toast 只显示「缓存清理失败」（不提示是权限问题）；
③ 点「备份所有策略数据」/「立即同步当日日线数据」→ 同样只得到泛化失败提示。
④ 「立即同步」还会进入 `runSync` 的轮询：`datacenterApi.status()` 也是 researcher 级（`datacenter.py:721`），
`syncing` 会一直挂在「正在增量同步…」直到用户离开页面。
对照组说明缺陷性质：同一页面 admin-only 的 `PUT /engine` 已被 `isAdmin` 正确门控 → 其余按钮属漏改（F15 同类，新页面）。
最小验证：以 viewer JWT 请求 `curl -s -X POST -H "Authorization: Bearer <viewer>" /api/v1/settings/db/backup` → 非 0 code；
页面按钮可点且无角色提示。

### #10 [P2][死代码][确定] 「自动清理缓存（N 天前数据）」滑条完全未接线（未接线功能）
位置：`frontend/src/pages/Settings/index.tsx:163`、`:603`、`:605-607`
```tsx
163: const [cacheDays, setCacheDays] = useState(30);
603: 自动清理缓存（{cacheDays}天前数据）
605: <input type="range" min={7} max={90} step={1} value={cacheDays} onChange={…}/>
```
证据：`rg -n "cacheDays" frontend/src` → **仅这 3 行**；`settingsApi.clearCache()`（`api/settings.ts:77-79`）不传任何参数，
后端 `POST /settings/data/cache/clear`（`app_settings.py:277-304`）也**没有任何保留期参数**（清空 `aqp:*` 全量 + 进程内 LRU）。
所以这是一个「看起来在配置数据保留策略、实则对系统零影响」的控件：用户把它从 30 调到 90 后，
下一次点「清理所有缓存」仍然全量清空。属 BRIEF 目标 B 的「写了但没有入口能触达（反向：有入口但无效果）」。
最小验证：把滑条拖到 90 → 立刻点清理 → 观察 `POST /settings/data/cache/clear` 请求体为空 `{}`，
且后端只回 `freed_mb`（全清），与 90 天保留无关。

### #11 [P2][Bug][确定] autoSync 复选框用硬编码 `true` 兜底 + 读写失败静默 → viewer 长期看到「每天 15:45 自动更新」已勾选
位置：`frontend/src/pages/DataCenter/index.tsx:381-383`、`:391-397`、`:491-497`、`:644-648`
```tsx
381: const [autoSync, setAutoSync] = useState(true);          // ← 硬编码默认「已开启」
382: const [autoTime, setAutoTime] = useState(DEFAULT_AUTO_TIME); // '15:45'
391: void datacenterApi.autoStatus().then(cfg => {…}).catch(() => { /* 降级用默认值 */ });
491: const toggleAuto = async (on) => { setAutoSync(on);       // ← 乐观更新
494:   const cfg = await datacenterApi.autoToggle(on, autoTime); …
496:   } catch { /* 降级 */ }                                  // ← 失败不改回，也不提示
644: <input type="checkbox" checked={autoSync} onChange={…} /> 每天 {autoTime} 自动更新
```
渲染依据的注释也承认默认值来自前端（`:380`「autoSync 改后端调度：前端只展示状态」），而 `GET/POST /sync/auto`
都是 **researcher** 级（`datacenter.py:750/763`），`/data` 却是 viewer 级（`App.tsx:97`）。
触发条件：viewer 打开 `/data` → `autoStatus()` 40300 → catch 静默 → 复选框显示**已勾选**「每天 15:45 自动更新」+
无「今日已触发」；即使后端 `enabled=false`，页面也永远宣称开着一套后台调度。viewer 再点一下取消勾选，
`toggleAuto` 乐观置 false、`autoToggle` 40300 静默失败 → 界面停在 false，直到下一次 `loadAll` 又恢复 true。
最小验证：viewer 登录访问 `/data`，DevTools 观察 `GET /api/v1/datacenter/sync/auto` 返回 40300，
而页面复选框为 checked；以 researcher 把 `.auto_sync.json` 设为 `enabled:false` 后 viewer 页面依旧勾选。

### #12 [P2][策略][确定] viewer 级页面内嵌 researcher 级操作（F15 同类延伸）：本批新增 3 页 / 13 个入口
**已知条目的延伸**（B7b F15 只点了 `DataCenter/index.tsx:890` + `TrainPanel.tsx:53/76/86`）。本批确认的**新增**范围：

| 页面（路由角色） | 未按角色禁用的入口/调用 | 对应后端角色 |
| --- | --- | --- |
| `/data` viewer（`App.tsx:97`） | `index.tsx:404 logs(60)`、`:404/:446/:464/:482 status()`、`:392/:405 autoStatus()`、`:565/621/627/633` 三个同步按钮、`:747/754` 修复/全量、`:862` 修复选定缺漏、`:778`（按钮在 `FetchPanel:352`）开始抓取 | `logs:594`、`sync/status:721`、`sync/auto:750`、`sync:677`、`sync/fetch:927` 全 researcher |
| `/data` viewer | `TextDataPanel.tsx:107` 导入文档、`:113-114` 构建情绪因子、`:164-165` 增量重建镜像 | `datacenter.py:1041/1053/1087` researcher |
| `/data` viewer | `TrainPanel.tsx:164-165` 开始训练、`:171-172` 取消训练（API 调用点 `:76/:86`） | `datacenter.py:1135/1162` researcher |
| `/settings` viewer | 见 #9（4 类写操作） | `app_settings.py:238/269/279/309` researcher/admin |
| `/alerts` researcher | 无角色问题（路由 researcher = 端点 researcher） | ✅ |

**后果（本批实测的新证据）**：viewer 打开 `/data` 时，`loadAll` 的 7 个请求有 3 个（logs/status/autoSync）必失败，
横幅文案由 `:433-434` 生成：
```tsx
const labels = ['overview','datasets','quality','logs','taskStats','status','autoSync'];  // :424
failures[labels[i]] = reason.message;                                                     // :428
setError(Object.keys(failures).length
  ? `部分数据面板加载失败：${Object.values(failures).join('；')}` : null);                 // :433-434
```
`:434` 只拼 `Object.values(...)`（message），**从不拼 labels**（`panelErrors` 的键仅在 `:577` 用于判空），
而 40300 的 message 是 `errors.py:140 message = detail` 即字面量 `"FORBIDDEN"`（`sanitizeApiMessage`
不翻译它）→ viewer 实际看到：**「部分数据面板加载失败：FORBIDDEN；FORBIDDEN；FORBIDDEN」**——
不点名哪个面板、无角色指引；同时「近期同步日志」面板显示「[等待日志输出…]」（`:202`，像"没有日志"而非"无权读取"）。
最小验证：viewer 登录访问 `/data`，观察横幅三个 `FORBIDDEN` 与日志面板占位文案；
`curl -s -H "Authorization: Bearer <viewer>" '/api/v1/datacenter/logs?limit=60'` → code 40300。

### #13 [P2][策略][确定] `useAuthStore` 把 `AQP_ADMIN_TOKEN` 迁移成持久化 admin 会话（违反 P0 契约第 4 条）
位置：`frontend/src/stores/useAuthStore.ts:14-15`、`:40-46`、`:86-98`
```ts
14: const LEGACY_TOKEN_KEY = 'AQP_ADMIN_TOKEN';
90: if (!useAuthStore.getState().token) {
91:   const legacy = readLegacyToken();
93:     useAuthStore.getState().setSession(legacy, { username: 'admin', role: 'admin' }, null);
```
契约（`chatgpt-full-review-prompt.md` §1 第 4 条）：「**ADMIN_TOKEN 仅用于运维直连，不是前端登录凭据**」。
当前实现把运维 Token 存进 `localStorage['aqp-auth']`（zustand `persist`，`:81`）并**在本地硬编码 `role:'admin'`**，
`RequireRole`（`RequireAuth.tsx:45`）完全信任这个本地角色 → 首屏即按 admin 渲染（如 Settings 的 engine 卡片）。
后端确实仍接受它（`core/auth.py:196-200`，且 `config.py:44-47 ALLOW_ADMIN_TOKEN_LOGIN` **默认 True**），
但这是「运维直连」通道，前端把它固化成用户会话后：
① 角色不再来自服务端（`/auth/me` 只在 `AuthBootstrap` 成功后覆盖，首屏到响应之间是自认 admin）；
② 该 Token 无过期时间（`expiresAt: null` → `isAuthenticated()` 恒 true，`:76-77`），只能靠后端 40101 兜底；
③ 一个已轮换/错误的历史 Token 会让用户以为「我已登录为管理员」。
最小验证：`localStorage.setItem('AQP_ADMIN_TOKEN','whatever'); localStorage.removeItem('aqp-auth')` 后刷新 →
`localStorage['aqp-auth']` 出现 `role:"admin"` 的会话，页面进入管理员 UI，直到 `/auth/me` 返回 40100 才被清空。

### #14 [P3][Bug][确定] 「重跑该交易日流水线」在日期框为空时静默无操作（无提示、无禁用）
位置：`frontend/src/pages/Pipeline/index.tsx:97-103`
```tsx
97: <input id="rerun-date" type="date" defaultValue={dag?.benchmark_date ?? ''} …/>
100: onClick={() => { const el = document.getElementById('rerun-date') as HTMLInputElement | null;
102:   if (el?.value) void rerun(el.value); }}   // ← 无 else：空值直接什么都不做
```
（`defaultValue` 会在 `dag` 到达后写回 DOM —— 已按 React 18.3.1 `updateWrapper`→`setDefaultValue`
（`react-dom.development.js:1854-1855`、`:1996-2005`）核对，所以「框一直为空」的初判**不成立**，已否证。）
可复现路径：用户清空日期框（date input 可直接删空）后点按钮 → 不发请求、不报错、`busy` 也不变，
用户无法区分「幂等跳过」与「点击没生效」。属空态无提示，非数据错误。
最小验证：打开 `/pipeline`，清空日期，点「重跑该交易日流水线」→ Network 无 `POST /ops/dag/rerun`，页面无任何变化。

### #15 [P3][Bug][确定] 自定义抓取下进度行渲染 `undefined执行中…`（`modeLabel` 未覆盖后端 `mode="fetch"`）
位置：`frontend/src/pages/DataCenter/index.tsx:515-517`、`:662`；类型 `types/datacenter.ts:73`
```tsx
515: const modeLabel: Record<SyncMode, string> = { incremental: '增量更新', repair: '修复缺漏', rebuild: '全量重构' };
662: {sync?.current || `${modeLabel[sync?.mode ?? 'incremental']}执行中…`}
```
后端 `datacenter.py:954 _sync.mode = "fetch"`（抓取任务复用同一 `_sync`），而 `_sync.current` 在
`trigger_fetch`（`:950-960`）**未被重置**、初始值恒为 `""`（`sync_service.py:56`）→
新进程里第一次点「开始抓取」后，在第一个 symbol 完成前进度行显示 `undefined执行中…`；
之后每次抓取会显示**上一次任务的最后一条 current 文案**（未重置）。
类型 `SyncStatus.mode: SyncMode | null` 因此与后端实际取值不符（契约不一致项）。
最小验证：重启后端 → researcher 在 `/data` 点「开始抓取」→ 进度行出现 `undefined执行中…`（约 1-2s，取决于首只耗时）。

### #16 [P3][死代码][确定] `alertsApi.updateRule` 与 `datacenterApi.task` 无调用方（`/sync/tasks/{id}` 的「刷新后恢复查看」未接线）
位置：`frontend/src/api/alerts.ts:64-65`、`frontend/src/api/datacenter.ts:38-44`
证据：`rg -n "updateRule" frontend/src` → 仅定义行；`rg -n "datacenterApi\.task\(" frontend/src` → 0 命中。
连带后果：
① 预警规则**没有编辑/启停入口**：`Alerts/index.tsx` 只有「新建」（`:230 enabled: true`）与「删除」（`:244`），
`AlertRule.enabled` 列在表格里也不渲染（`:344-350` 只有名称/类型/作用域/参数/渠道/删除）→
用 API 停用的规则在 UI 上无任何标识；
② 后端 `GET /sync/tasks/{task_id}`（`datacenter.py:726-735`，docstring 明写「供页面刷新后恢复查看」）
没有前端消费者，`SyncStatus.task_id` 也没人用 → 刷新页面后无法恢复任务视图（`/data` 只靠 1.5s 轮询内存态）。
最小验证：`rg -n "task_id" frontend/src` → 仅类型声明与 `useTaskPolling` 无关；页面上无任何按 task_id 查询的入口。

### #17 [P3][一致性][确定] 登录页忽略 `register/status` 的 `default_role` / `min_password_length` / `username_pattern`
位置：`frontend/src/pages/Login/index.tsx:37-50`（只取 `enabled`）、`:70-72`（硬编码 6 位）、`:209-213`（硬编码「只读（viewer）」）；类型 `api/auth.ts:27-33`
后端 `auth.py:154-172`：`default_role = _default_register_role()`，而 `_REGISTERABLE_ROLES = (viewer, researcher)`
（`:45`）+ 配置项 `REGISTER_DEFAULT_ROLE`（`config.py:139`）→ 运维可设 `AQP_REGISTER_DEFAULT_ROLE=researcher`，
此时注册**真的会授予 researcher**，而登录页仍然写「注册账号默认角色为只读（viewer）」。
`min_password_length`/`username_pattern` 目前数值恰好等于硬编码值（`MIN_PASSWORD_LENGTH=6`、`{3,64}`），
属「双事实源」，改后端即静默漂移。
最小验证：`AQP_REGISTER_DEFAULT_ROLE=researcher` 重启后端 → `GET /api/v1/auth/register/status` 返回 `researcher`，
登录页注册 Tab 仍显示「只读（viewer）」。

### #18 [P3][一致性][确定] 执行中心「自动刷新（8s）」与真实间隔不符（真实 = refresh_freq × 2）
位置：`frontend/src/pages/OrderDesk/index.tsx:79`（`useRefreshIntervalMs(2)`）、`:205`（文案 `自动刷新（8s）`）、`:44`（注释「开启后每 8s 刷新」）
`usePreferencesStore.ts:7 DEFAULT_REFRESH_SEC = 5`，后端 `app_settings.py:44 "refresh_freq": 3`：
默认真实间隔是 3×2 = **6s**（打开过设置页后为 6s，未加载偏好时 10s），恒定不是 8s。
同类文档漂移：`Watchlist/index.tsx:184` 注释「refresh_freq × 12（默认 5s × 12 = 60s）」按后端默认实为 36s。
最小验证：打开 `/desk`，DevTools 勾选自动刷新后统计 `/api/v1/desk/account` 的请求间隔 ≈6s。

### #19 [P3][死代码][确定] `loadAll` 的 `setError` 分支被 3 行后无条件覆盖；`failed_count` 未声明/未消费
位置：`frontend/src/pages/DataCenter/index.tsx:412-419` vs `:424-434`
```tsx
415: } else {
416:   setError(results[0].status === 'rejected' ? … : null);     // ← 立刻被下面的 setError 覆盖
417-418: }
424-432: … failures[...] = …; setPanelErrors(failures);
433: setError(Object.keys(failures).length ? `部分数据面板加载失败：…` : null);   // ← 总是执行，含 else 分支
```
`:416` 写入的值在本函数返回前必被 `:433` 覆盖（同一同步执行段），属不可达效果；
另外后端快照带 `failed_count`（`sync_service.py:118`）且「缺口不再静默固化」是 Task 9 的整改目标，
但 `types/datacenter.ts:70-85 SyncStatus` **没有该字段**，面板空闲态（`:684-686`）只显示 `done/total`，
失败只数只能从顶栏 SSE 文案（`sync_service.py:503` 的 `counts`）看到（`rg failed_count frontend/src` → 0 命中）。
最小验证：让 `datacenterApi.status()` 返回 rejected、其余 6 个 fulfilled → 观察 error 文案不含 `:416` 的「后端服务不可用」。

### #20 [P3][Bug][确定] 熔断开关状态读取失败时显示绿色「正常运行」（未知被当成安全）
位置：`frontend/src/pages/OrderDesk/index.tsx:366-371`
```tsx
366: <span className={`… ${kill?.kill_switch ? 'bg-red-100 text-red-700' : 'bg-emerald-50 text-emerald-700'}`}>
368:   {kill?.kill_switch ? '熔断激活' : '正常运行'}
371: <div …>未完成母单：{kill?.pending_orders ?? '—'}</div>
```
`refresh()`（`:58-67`）用 `Promise.all`：任一请求失败（含 `GET /desk/kill-switch` 本身）→ 四个 state 全部不更新 →
`kill === null` → 风控闸门卡片渲染**绿色「正常运行」**（同一批的账户卡则停在「加载中…」）+
「未完成母单：—」。即风控状态**不可读时默认显示为"无熔断、安全"**，与同批要求的
「不完整不得当成完整/安全展示」相反（对照 `Alerts`/`Settings` 的不可用文案都显式写了「不可用」）。
最小验证：DevTools 把 `/api/v1/desk/kill-switch` 置为失败 → 打开 `/desk`，观察红色错误条 + 绿色「正常运行」。

### #21 [P3][死代码][确定] 设置页刷新频率下拉的三个选项标签全部是「管理配置」
位置：`frontend/src/pages/Settings/index.tsx:421-425`
```tsx
421: <select value={prefs.refresh_freq} onChange={…}>
424:   {[3, 5, 10].map((s) => <option key={s} value={s}>管理配置</option>)}   // ← 三个选项同标签
425: </select>
```
选项值与文案错位（疑似从右侧 Eastmoney 的「[管理配置]」链接（`:447-448`）复制而来）：
用户展开下拉看到三个完全相同的「管理配置」，无法据此选择 3/5/10 秒，只能靠左侧
「实时行情刷新的频率: 每{prefs.refresh_freq}秒」（`:420`）反推；若后端存过非 {3,5,10} 的值
（`PreferencesIn.refresh_freq: int | None` 无值域校验，`app_settings.py:172`），`<select>` 还会显示为空。
最小验证：打开 `/settings` 展开该下拉 → 三项文案均为「管理配置」。

### #22 [P3][一致性][确定] 危险操作二次确认口径不一致：全量重构/修复一键即发、预警删除规则无确认
位置：`frontend/src/pages/DataCenter/index.tsx:633-638`（全量数据重构，仅 `disabled={busy}`）、`:627-632`（修复K线缺漏）、`frontend/src/pages/Alerts/index.tsx:366-369`（删除规则，单击即 `deleteRule`）
对照组（做对了的）：`Settings/index.tsx:613-615` 清理缓存有 `ConfirmModal`；`OrderDesk/index.tsx:221` 起熔断有 Modal。
「全量数据重构」会按 `mode=rebuild` 重写全市场 daily_bar 分区（`sync_service.py:363-392`），
误点一次的代价是长时间重跑 + 期间数据不一致；`DELETE /alerts/rules/{id}` 不可撤销（连带不再产生事件）。
最小验证：单击「全量数据重构」→ 直接发出 `POST /api/v1/datacenter/sync`（无确认步骤）。

## 4. 未达 P2 的观察（记录，不计入结论表）

1. **已否证的初判（保留以免后人重踩）**：`Pipeline/index.tsx:97` 的 `<input type="date" defaultValue={dag?.benchmark_date ?? ''}>`
   一度被认为会永远停留在空值；按 React 18.3.1 源码 `updateWrapper`→`setDefaultValue`
   （`frontend/node_modules/react-dom/cjs/react-dom.development.js:1854-1855`，`setDefaultValue` 实现 `:1996-2005`）
   `defaultValue` prop 变化会写回 DOM（dirty flag 未被用户置位时）→ **框会被正确填充**，该条不成立（#14 只保留「空值点击无提示」）。
2. **并发重复触发（本批重点）= 通过**：
   - `POST /datacenter/sync` 在 `_sync.lock` 内判 `_sync.running` → `fail(4002, "已有同步任务在执行中…")`（`datacenter.py:682-684`）；
   - `POST /datacenter/sync/fetch` 先 `pipeline_slot("fetch")` 预检 → `ERR_PIPELINE_BUSY`，再判 `_sync.running`（`:937-952`）；
   - `POST /datacenter/train/start` → 管道互斥 `ERR_PIPELINE_BUSY`、`_job.running` → `ERR_PARAMS`（`train_service.py:420-442`）。
   前端没有幂等键，但 `disabled={busy||running}` + 上面三道服务端互斥保证不会真的起两个任务，
   重复点击只多一条业务错误提示（`client.ts:103-104` 抛 ApiError → 各页面 catch 展示）。
   **唯一可改进点**：`DataCenter/index.tsx:460-470 startSync` 不设本地 in-flight 标志，
   按钮在 RTT 窗口内仍可点（`busy` 依赖 `sync.running`，最多晚 1.5s）→ 用户会看到一条 `code=4002` 的报错，
   属可接受但不够优雅（P3，未单列）。
3. **SSE 通知链路（本批重点）= 通过**：`useNotifyStore.ts:97` 每次建连都重新 `createStreamTicket()`（一次性 ticket 不复用）；
   `:109-115 onerror` 先 `closeEventSource()` 阻断 EventSource 自动重连、再 `scheduleRetry()`（1s/2s/4s，`MAX_RETRIES=3`）；
   `:98` 用 `connectionGeneration` 丢弃 `close()` 之后回来的旧换票；`Topbar.tsx:91-98` 在登出/卸载调用 `close()`；
   `:284-286` 对「实时推送未连接」有明确文案 + 「刷新」兜底按钮 → 断线不静默。
   仅 3 次重试耗尽后**本次会话内不再自动重连**（需刷新页面或重新登录才恢复），UI 已披露，故不计。
   另：前端只订阅默认 `notify` 频道，未订阅 `alerts` 频道（`notify.py:94` 缺省 `channels=notify`），
   因此预警触发只出现在 `/alerts` 页（30s 轮询），不会进顶栏铃铛——与 `Alerts` 页文案「规则触发后将在此…」一致，不计为缺陷。
4. **信封错误处理（本批重点）= 通过**：`api/client.ts:95-128` 统一判定 `isApiEnvelope` + `code !== 0` → `ApiError`，
   并映射 `40100/40101/40102` → 清会话；因此**不需要**每个调用点各自判 `code`。
   本批所有调用点均 try/catch 或 `allSettled` 并落到用户可见位置（除 #12 的静默 `catch{}`，已单独计入）。
5. **空值访问链专项**：本批未发现缺 `?.` 导致的解引用崩溃。
   - `PaperAccount`（`paper.py:383-397`）字段齐全（`pnl`/`nav_series`/`positions` 必存在）；
   - `AlertEventItem.triggered_at` 后端强制 `str(... or "")`（`alerts.py:261`）→ `:399 slice` 安全；
   - `DataOverview.storage_gb`（`:591 toFixed`）、`QualityScanResult.by_kind`（`:151 Object.entries`）、
     `SettingsBundle.settings.preferences`（`app_settings.py:66` deep-merge DEFAULTS 保证存在）均有后端不变式兜底；
   - 唯一的 hooks 级渲染崩溃点是 #1（不是属性链）。
6. **长任务轮询（本批重点）**：`useTaskPolling.ts:14-21` 在终态返回 `refreshInterval=0` ✅；
   `DataCenter/index.tsx:441-456` 的 1.5s 轮询用 `sync?.running` 条件挂载 + `clearInterval` 清理、终态触发一次全量刷新 ✅；
   `Alerts/index.tsx:211-216` 30s 轮询带 `visibilityState` 判活 + 清理 ✅；`Settings/index.tsx:174-177` 卸载清理 ✅。
   **缺失项**：轮询失败无退避（`DataCenter:453 catch{}`、`Alerts:213 catch{}` 都是固定间隔重试），
   后端长时间不可用时会持续按原频率发无效请求（低频、有界，未达 P2）。
7. **`DataCenter/index.tsx:52-66` 的本地 `useChart` 与 `utils/useChart.ts` 是两套实现**：
   本地版按 `[option]` 做 `init/dispose`（每次数据变化重建实例），且返回值 `height` 无任何消费者
   （两个调用点 `:110`/`:169` 都把高度写死在 JSX 里）→ 重复实现 + 死参数（P3，未单列）。
8. `cancelSync`（`:473-475 catch{}`）失败静默：点「停止同步」若 40300/网络失败，界面继续显示进度条，
   用户会以为"停止没生效"（后端 `cancel_sync` 本身幂等，`datacenter.py:783-788`）。
9. `Alerts/index.tsx:251 markAllRead` 的 `catch{}` 静默、`:366 删除规则` 无确认（后者已并入 #22）。
10. `Settings/index.tsx:588` 「立即同步」的 `runSync` 轮询在 `datacenterApi.status()` 持续失败时永不退出
    （`:298-300` 注释「保留轮询」），对 viewer 即恒真 → `syncing` 永久为 true（并入 #9）。
11. `DataCenter/index.tsx:269-272` 的 `instruments` 预览请求无竞态守卫：快速切换 股票→ETF→全部
    可能让后到的旧响应覆盖新预览（只有 5 行文本受影响，P3）。
12. `Login/index.tsx:53-55`：存在 token 即 `<Navigate>`，配合 #13 的旧 Token 迁移，
    在旧 Token 失效前用户看不到登录页（`AuthBootstrap` 的 401 会清会话，为临时态）。

## 5. 与本批重点的对照小结

| 本批重点 | 结论 |
| --- | --- |
| 长任务轮询：终态停止 / 卸载停止 / 失败退避 / 并发重复触发 | 终态与卸载清理 ✅（4 处轮询全部正确清理）；失败退避 ❌（观察 6）；并发重复触发**服务端三道互斥** ✅（观察 2） |
| 任务 ID 失效（40400/51001）后是否给指引 | 本批**没有**按 task_id 查询的 UI（#16）：`/sync/tasks/{id}` 与 `datacenterApi.task` 全未接线；`TrainPanel` 无 task_id 概念，40300 时显示「训练状态加载失败」（可感知） |
| 任务列表分页与服务端语义一致 | ❌ #3（母单 50 条窗口 + 客户端分页/搜索/计数）、#4（预警 50 条窗口 + 未读/已读），均为「客户端分页覆盖服务端截断」 |
| 危险操作二次确认 + 角色门槛 + 失败可感知 | 确认：熔断 ✅、清缓存 ✅；缺确认：#22。角色门槛：**本批唯一重灾区**（#9、#12）。失败可感知：#20（绿"正常"）、#10、观察 8/9 |
| SSE 通知：一次性 ticket 重取 / 断线丢事件 / 卸载关闭 | ✅ 全部正确（观察 3）；仅 3 次退避后本会话不再自动重连（已披露） |
| `useAuthStore` 登录/登出/过期/注册开关；ADMIN_TOKEN 误用 | 登出 ✅（`Topbar:321 clear()` + 通知关闭）；过期 ✅（`isAuthenticated` + `client` 401 清会话）；注册开关 ✅（`enabled` 门控入口）；**ADMIN_TOKEN 误用 ❌ #13** |
| 错误处理：检查信封 `code != 0` / `?.` / NaN | 信封由 `client.ts` 统一处理 ✅；`?.` 未发现缺失（观察 5）；NaN 无来源（本批所有数值字段有后端数值不变式） |
| 与后端契约不一致（参数/字段名） | 参数/字段名逐项一致（§2）；**不一致项**：`SyncStatus.mode` 缺 `"fetch"`（#15）、`SyncStatus` 缺 `failed_count`（#19）、`AlertRule.enabled`/`updateRule` 无 UI（#16）、`RegisterStatus` 三字段未消费（#17） |
| 死代码（无路由引用 / 未接线 / 定义未使用） | #10（滑条无效果）、#16（两个 API 函数 + 一条后端恢复链路无 UI）、#19（不可达 `setError`）、#21（三选项同标签）、观察 7（两套 `useChart`）；**未重复报告** `MarketHeatmap.tsx` |
| 前端把「不完整」当「完整」展示（父审核员点名） | ❌ 本批共 4 处：#2（instruments total）、#3（orders 50）、#6（质量扫描 200 只/0 覆盖）、#5（failed_jobs 饱和）；`/logs` 无对应提示位（面板固定写「app.log 尾部」，无「已截断」语义） |