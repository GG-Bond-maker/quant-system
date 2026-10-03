# AQP 增量数据补齐 + 审查问题并行修复 · 交付报告

- **日期**：2026-10-02
- **目标交易日**：2026-09-30（`last_completed_trade_day(get_calendar())` 实测值；10-01/10-02 非交易日）
- **模式**：Agent（可写文件）—— 上一轮为「只报告不改」，本轮解除限制，既抓数据又修问题
- **对应上轮报告**：`deliverables/software-company/aqp-preaudit-2026-10-01.md`

---

## 0. TL;DR

1. **前提校正**：用户所说「只有 2400 条」经实测**不成立**。`daily_bar` 实际有 **2499** 个标的、**244.6 万**行。真实缺陷是**时间轴滞后**（大量标的停在 09-17/09-18，而最近已完成交易日是 09-30）。
2. **增量抓取**：新增可续跑运维 CLI `backend/scripts/backfill_incremental.py`，复用**生产同步同一套**判据与写入路径；五项策略（范围 / 来源 / 频率 / 去重 / 断点续传）见 §1。
3. **并行修复**：修掉 **1 个 P0 + 5 个 P1**；同时**回原文核对**发现上轮审计报告有 **6 处指控不成立**（含 2 处引用了**根本不存在的文件**），已在 §3 逐条更正。
4. **过程中的自我纠错**：一次 mypy 修复（`Request = None` → `Request | None`）**引入了新缺陷**——FastAPI 靠 `issubclass` 识别 Request 注入，Union 会让它认不出来，**59 个测试文件在收集期直接全挂**。已回退并改用 `# type: ignore[assignment]`。这条已写进代码注释（§2.6）。

---

## 一、增量抓取

### 1.0 前提校正：「只有 2400 条」

| 口径 | 实测值 |
|---|---|
| `data/parquet/daily_bar/` 下 `symbol=` 分区目录 | **2501**（含 1 个杂项目录 `evil` ⇒ 有效 **2500**） |
| `read_all_symbols("daily_bar")`（跳过空分区） | **2499** |
| manifest 登记 `daily_bar` 行数 | **2,445,969** |
| `universe_2500.json` | `target=2500`、`eligible_non_bj=5218`、`excluding_bse=343` |

⇒ 「2400」**既不是行数**（实际 244 万行）**也不是分区数**（实际 2499）。它最接近的是**目标标的池规模 2500** 的约数。
**真实缺陷不是"条数不足"，而是"时间轴滞后"**：大量标的的最新交易日停在 09-17/09-18，而最近已完成交易日是 09-30。

**补齐前尾部日期分布**（实测）：

| 尾部日期 | 标的数 |
|---|---|
| 2026-09-17 | 1496 |
| 2026-09-18 | 623 |
| 2026-09-30 | 361 |
| 2026-09-24 | 12 |
| 2026-09-11 | 3 |

### 1.1 策略① 范围（Scope）

**范围判定不靠"猜缺口"，而是逐标的读磁盘事实**（`build_plan()`）：

```python
last     = _symbol_last_date_in("daily_bar",     sym, target.year)
last_hfq = _symbol_last_date_in("daily_bar_hfq", sym, target.year)
if last is None and last_hfq is None:            → no_data   （库内无数据）
if last >= target and last_hfq >= target:        → up_to_date（跳过）
else:
    ref   = min(last, last_hfq)                  # 🔴 取两侧**较早者**
    start = next_trade_day(ref, cal)             # 从缺口次一交易日开始抓
```

三个刻意的设计决定：

1. **起点取 raw/hfq 两侧较早者**。若只看 `daily_bar`，会出现「raw 已到 09-30、hfq 还停在 09-18」的标的被**误判为已完成**，缺口被永久固化（`daily_bar_hfq` 是标签/特征/评估的**唯一价格基准** `_LABEL_PRICE_BASIS="hfq"`，落后会污染下游全部因子）。
2. **复用生产同步的同一函数** `from app.services.sync_service import _symbol_last_date_in`，而非另写一份。理由：跳过判据一旦分叉，CLI 与生产 `auto_sync_scheduler` 就会对"谁该抓"给出不同答案——这正是本项目反复踩过的「同型写法分叉」缺陷（记忆条目 C-17/元教训）。
3. **目标日 = `last_completed_trade_day()`**，盘前/盘中/非交易日**回退上一交易日**。🔴 绝不把盘中快照写进日线：否则次日 `last >= target` 判定为"已最新"，**残缺快照永久保留**。

**dry-run 产出（`deliverables/backfill-dryrun.log`）**：

```
增量补齐范围（目标交易日 = 2026-09-30）
  数据源降级链      : eastmoney → sina → baostock
  标的池（daily_bar）: 2499
  ├ 已最新（跳过）  : 360
  ├ 待补齐          : 2139
  └ 库内无数据      : 0
  其中 hfq 侧缺失   : 0

  待补标的的区间起点分布：
    2026-09-18 起   1498 只
    2026-09-21 起    623 只
    2026-09-28 起     11 只
    2026-09-14 起      3 只
    2026-09-15 起      3 只
    2026-09-04 起      1 只

  预计网络调用      : 4278 次（2139 只 × 2 口径）
```

### 1.2 策略② 数据来源

**降级链**：`AQP_DAILY_SOURCE_PRIORITY` 默认 `eastmoney → sina → baostock`，`daily_source_priority()` 在**调用时**经 `globals()` 解析（保证 monkeypatch 可生效）。

**本轮连通性实测**（重要——不能假设源可用）：

| 源 | 主机 | 实测结果 |
|---|---|---|
| 东财 | `push2his.eastmoney.com` | ❌ `ProxyError: Unable to connect to proxy, RemoteDisconnected`（本机被阻断） |
| 东财 | `push2.eastmoney.com` | ❌ `RemoteProtocolError: Server disconnected` |
| 东财 | `datacenter-web.eastmoney.com` | ✅ 可达 |
| 新浪 | `hq.sinajs.cn` / `stock_zh_a_daily` | ✅ **可用**（≈1.3s/只），8 个交易日与本地日历**逐日对齐**，`amount` 非空 |
| 腾讯 | `web.ifzq.gtimg.cn` | ✅ 可用，但**不提供成交额** ⇒ 未选为首源 |
| baostock | `baostock.com` | ⚠️ 可达但报 `AttributeError: 'NoneType' object has no attribute 'error_code'` + `日期格式不正确` ⇒ **兜底源实际失效**（见 §4 遗留） |

⇒ 实际生效源 = **新浪**（降级链第 2 位）。**这印证了降级链的价值**：首源挂死不会让整批任务失败。
⚠️ 同时暴露一个隐患：**兜底源 baostock 已失效**——降级链名义上 3 级，实际只有 2 级可用。

### 1.3 策略③ 频率

| 约束 | 值 | 说明 |
|---|---|---|
| 全局限速 | `AKSHARE_RATE_LIMIT = 1.2s/次` + ≤30% 随机抖动 | 进程级 `threading.Lock`，**全局串行** |
| 并发 | **单线程串行**（刻意） | 限速是全局锁 ⇒ 多线程**无收益**，只会让抖动叠加、更容易触发源端风控 |
| 熔断 | `_SourceBreaker`：`THRESHOLD=1`、`COOLDOWN=15s` | 仅对**连接类**失败计数（`_is_connection_error` 走异常因果链），任一成功即复位 |
| 实测速率 | **0.33 只/s**（每只 2 次外呼 ⇒ ≈0.66 次/s，与 1.2s 限速吻合） | 全量 2115 只 ≈ **97 分钟** |

**调度**：本 CLI 是一次性补齐，非周期任务。生产侧的周期同步由 `auto_sync_scheduler`（每 60s 检查一次）负责，两者共用同一套 `fetch_and_write_daily_bars`。

### 1.4 策略④ 去重

去重**不在 CLI 里做**，而是复用写入层的既有语义（`parquet_store.write_partition`）：

```
读该年分区文件 → _align_concat → unique(subset=["date"], keep="last") → _atomic_write_parquet
```

- `keep="last"` ⇒ **后写覆盖先写**：重跑时新抓到的数据（含修正值）覆盖旧值，天然幂等。
- 全程持 `_rw_lock`：进程内 `_path_lock` + **跨进程** `.rwlock` 锁文件（`_LOCK_WAIT_SECONDS` 超时后接管陈旧锁）。这保证 CLI 与生产同步进程**同时跑也不会互相覆盖**。
- **验收判据**：抽样标的 `date` 必须唯一（见 §5 判据 2）。

⚠️ **代价**：逐日「读-改-写全分区」是 O(N²) 写放大。本项目已有 `write_year_batch` 用于批量场景；本 CLI 逐只写入（每只 1~2 次），写放大可接受。

### 1.5 策略⑤ 断点续传

状态文件：`data/parquet/.backfill_incremental_state.json`（原子写：`.tmp` + `replace`，**5s 节流**合并落盘）

```json
{ "target": "2026-09-30",
  "completed": ["000607.SZ", "001237.SZ", ...],
  "failed": [],
  "rows_written": 5564,
  "updated_at": "2026-10-02 00:49:53" }
```

四个关键设计：

1. 🔴 **仅当 `target` 相同才复用 `completed`**。目标日一变（跨过一个交易日）必须**重算范围**——否则新交易日会被旧记录挡住，`completed` 里的标的永远补不上。
2. **`completed` / `failed` 互斥不变式**：进 `completed` 必 `failed.discard(sym)`，反之亦然。避免同一标的在"成功"与"失败"两个集合里同时出现，导致续传语义自相矛盾。
3. **续传 ≠ 跳过校验**：续传只跳过"已确认到位"的标的，判定依据是**写后回读**（见下），不是 `fetch_and_write` 的返回值。
4. **`--no-resume`** 可强制从头重跑（仍幂等，因为去重层保证）。

### 1.6 「抓取成功」≠「数据到位」——写后回读校验

CLI 默认开启 `verify_each`：每只标的外呼并落库后，**重新回读磁盘**确认两侧真的到达目标日：

```python
v_raw = _symbol_last_date_in("daily_bar",     sym, target.year)
v_hfq = _symbol_last_date_in("daily_bar_hfq", sym, target.year)
if not (v_raw >= target and v_hfq >= target):
    failed.add(sym); completed.discard(sym)   # 不计入成功
```

理由：`fetch_and_write_daily_bars` 返回成功**只代表调用链没抛异常**。写入可能被 `validate_write_gate`（`WRITE_GATE_THRESHOLDS`）拒绝、可能被跨源混列守卫 `_guard_cross_source` 拦下、也可能源端返回了空帧。**不回读就无法区分**。
实测价值：验收脚本已发现 `300061.SZ` 出现 `raw=2026-09-30 / hfq=2026-09-18` 的**单侧到位**状态——正是"不回读就会误判"的现场。

### 1.7 执行结果

<!-- RESULT_SECTION -->

---

## 二、并行修复的问题（按优先级）

**优先级定义**：
- **P0** = 上线阻断（用户可见的布局崩塌 / 数据错 / 服务不可用）
- **P1** = 诚实性红线（口径谎报 / 假交互 / 欺骗性 UI）或门禁失效
- **P2** = 体验或健壮性缺陷（不阻断上线）
- **P3** = 建议项

### 2.1 【P0】ETF 中心布局崩塌（高度链断裂）

| 项 | 内容 |
|---|---|
| **缺陷** | 刷新后主区塌陷、卡片溢出压盖，「ETF列表」被黑色条纹矩形压住 |
| **根因** | CSS 2.1 §10.7：`min-height: 100%` 落在 `height: auto` 的父元素上**解析为 auto**。页面根用 `min-h-full`，但中间包装 div **没有 `h-full`** ⇒ 百分比无解析基准 ⇒ 高度链断裂；再叠加 `flex-1`（`flex-basis: 0%`）+ `min-h-0` ⇒ 无自由空间 ⇒ 主轴塌陷 |
| **修复** | `frontend/src/App.tsx` 的 `MainContent` 中间包装 div 补 `h-full` |
| **同型排查** | 上轮报告列的 6 个同型页面（Backtest / DataCenter / Report / Screener / Settings / Watchlist）**均靠该中间包装 div 提供高度** ⇒ **单点修复全覆盖**（grep 确认） |
| **验证** | `tsc -b --force` ✅ / `vite build` ✅ / `bundle:check` ✅；⚠️ **本机浏览器自动化不可用，布局为静态推导 + 闸门间接证据，待人工目视验收** |

关键区分（已写进代码注释）：`h-full`（**包装层**，提供定高基准）与 `min-h-full`（**页面根**，至少撑满）**不可互换**。

### 2.2 【P0→P1】Chart 不跟随容器尺寸

| 项 | 内容 |
|---|---|
| **缺陷** | `Etf/index.tsx` 的 Chart 只监听 `window.resize`，与注释声称的"图表跟随铺满"不符 |
| **根因** | ECharts/zrender 的 canvas 是 `position:absolute`，**像素尺寸在 `init()` 时刻写死**。筛选切换、栅格从单列切双栏等**不触发 `window.resize`** 的变化，图表不会跟随 |
| **修复** | 补 `ResizeObserver` 观察**容器本身**（与项目既有正确范式 `utils/useChart.ts` 对齐） |
| **验证** | `tsc -b --force` ✅ / build ✅ |

### 2.3 【P1】筛选器「筛选」按钮是假交互（欺骗性 UI）

| 项 | 内容 |
|---|---|
| **缺陷** | 点「筛选」按钮**零反应** |
| **根因** | 每个控件直接 `onChange({...value, [k]: v})` 立即回写父级；「筛选」按钮执行 `onClick={() => onChange(value)}` —— 传回的正是父级**已经持有的同一个对象引用**，React 以 `Object.is` 判定无变化直接 **bail out**，不重渲染 |
| **修复** | 改为**草稿 + 显式应用**模型：控件只改本地 `draft`，「筛选」才提交；导出 `EMPTY_ETF_FILTERS` 统一初值 |
| **连带修复** | ① 数字/日期输入框**每敲一键**就触发一次列表请求（逐字外呼）；② 每次筛选变化触发 2 次请求（见 2.4） |
| **字段归属** | `q`（关键字）由顶部搜索框拥有，提交时显式沿用父级 `value.q`，避免用草稿里的陈旧副本覆盖搜索框 |
| **验证** | 源码断言（`test_frontend_*` 范式）+ **证伪**（见 §2.7） |

### 2.4 【P1】筛选条件变化触发 2 次请求

| 项 | 内容 |
|---|---|
| **缺陷** | 条件变化时固定发 **2 次**请求；两次响应乱序时**旧页码结果可能后到并覆盖新结果**（表格显示第 3 页内容、分页器却停在第 1 页） |
| **根因** | 两条独立 effect：`useEffect(loadList)`（deps 含 `page`）+ `useEffect(() => setPage(1), [country, board, filters, sort])`。条件变化 ⇒ 先以**旧页码**发一次；`setPage(1)` 生效 ⇒ `loadList` 身份再变 ⇒ 又发一次 |
| **修复** | 用「筛选指纹 + ref」把两件事收敛到**一条 effect**：指纹刚变且页码不在 1 时**只归页码、不发请求**，页码归位后由同一 effect 触发唯一一次请求 |
| **验证** | 逻辑推演覆盖 4 条路径（条件变化×页码是否为 1、点分页器、首挂载）+ `tsc` / build |

### 2.5 【P1】列表标题「（0）」把「未加载」冒充「零结果」

| 项 | 内容 |
|---|---|
| **缺陷** | `Card title={`ETF 列表（${listRes?.total ?? 0}）`}` —— 加载中/失败时显示「（0）」 |
| **性质** | 项目头号红线：**「0 冒充不可得」**。`0` 是「确实筛出 0 条」这个**有效结论**；拿它冒充「还不知道」，会把请求失败伪装成"没有匹配结果" |
| **修复** | 改 `listRes?.total ?? '—'`；`null` ⇒ 中性「—」（与全站 `PanelEmpty` / `DegradedBadge` 口径一致） |
| **验证** | `tsc` ✅ |

### 2.6 【P1】资金流向 / 规模变化不随筛选，却无任何披露（口径谎报）

| 项 | 内容 |
|---|---|
| **缺陷** | 页面顶部的**板块 Tab / 国家筛选**与右侧 **ETF筛选器**视觉上是**页面级**条件，但真正消费它们的只有 `/list` 与 `/hot`。`/scale` 只统计 **A 股**（源码硬编码 `x["country"] == "cn"`），`/flow` 是东财**全市场**净流入榜——两者都不随筛选变化，**界面上没有任何披露** |
| **影响** | 用户把国家切到「美国」后，会默认「资金流向 / 规模变化」也变成了美股 ⇒ **把未筛选的数据当筛选结果读**。这正是项目反复出现的「同一页面多数据块口径错位」 |
| **处置** | 不假装筛选生效（`/flow` 上游东财榜本就不支持按国家/板块切片，硬过滤只能靠本地目录反查、会与榜单口径打架）。改为**后端回显作用域 + 前端如实渲染**：<br>① `/scale` 新增 `scope="cn"` + `scope_note`（**正常与降级两条路径都带**）<br>② `/flow` 新增 `scope="all_market"` + `scope_note`（同上）<br>③ 前端在两张卡上渲染「口径：…」行；资金流向卡的披露用 `sticky bottom-0`，因为卡 body 可滚动，普通行尾元素在 10 行榜单下会被推出可视区（等于没披露）<br>④ `FilterPanel` 增加**总纲声明**：「条件作用于「ETF列表」与「热门榜」；资金流向、规模变化为固定口径，不随之变化」 |
| 🔴 纪律 | **口径文案只能由后端回显，前端不得写死**——前端写死会在后端改口径时静默漂移成新的谎报。测试里有一条专门断言前端**没有**逐字写死这两段文案 |
| **验证** | 新增 2 条测试 + **3/3 证伪**（见 §2.7） |

### 2.7 【P1】全站缺失 HTTP 安全响应头

| 项 | 内容 |
|---|---|
| **缺陷** | 全仓 grep 安全响应头 **0 命中** |
| **修复** | `backend/app/main.py` 在既有 `add_response_time_header` 中间件内调用 `_apply_security_headers()`，注入 `X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer` / `Permissions-Policy: geolocation=(), microphone=(), camera=()`；`ENV == "prod"` 时额外下发 HSTS |
| **两个刻意的"不做"** | ① **不下发 CSP**——会打坏非 prod 的 Swagger UI；CSP 应由前置网关按站点维度统一加（收益远低于误伤风险）<br>② **HSTS 仅 prod**——非 prod 多为 `http://localhost`，下发会强制升级 https 使本地开发不可用 |
| **验证** | 导入冒烟 + 直接调用断言（见下） |

```
IMPORT OK
{'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
 'Referrer-Policy': 'no-referrer',
 'Permissions-Policy': 'geolocation=(), microphone=(), camera=()'}
HSTS in non-prod: None          ← 符合预期
```

### 2.8 【P1 · 门禁失效】mypy 3 处 implicit-Optional + **一次修复引入新缺陷**

**原始问题**：`mypy app/ --ignore-missing-imports` 报 3 处错误（`app/core/auth.py:207/257/286`，`request: Request = None` 违反 PEP 484 的 implicit-Optional 禁令）。门禁不绿。

**⚠️ 我的第一次修复引入了更严重的新缺陷**——必须如实记录：

```python
# 第一次修复（错）：
request: Request | None = None
```

FastAPI 靠 `issubclass(annotation, Request)` 识别"注入请求对象"；`Request | None` 是 **Union**，`issubclass` 认不出来 ⇒ 它被当**普通参数**处理 ⇒ **应用装载期直接抛**：

```
fastapi.exceptions.FastAPIError: Invalid args for response field!
Hint: check that starlette.requests.Request | None is a valid Pydantic field type
```

后果：**59 个测试文件在收集期全部 ERROR**（`Interrupted: 59 errors during collection`）。

**正确修复**：注解保持**裸 `Request`**（FastAPI 需要），默认值 `None` 保留（`notify.py:67` 与 `main.py:427` 会 `require_auth(credentials)` 直调，无请求上下文），用 `# type: ignore[assignment]` 压制 mypy 的 implicit-Optional 告警：

```python
request: Request = None,  # type: ignore[assignment]
```

**教训（已写进代码注释）**：
1. **门禁必须跑全量**——只跑改动相关的测试不会发现"应用装载期崩溃"这类**收集期**故障（它影响的是**所有**测试文件）。
2. **静态类型工具的建议不能盲目照做**：mypy 的 `Request | None` 是"类型上正确"的，但它**破坏了运行时的框架契约**。框架的魔法（`issubclass` 分发）是类型系统看不见的。
3. 顺带说明 `ensure_role()`（**普通函数**，非依赖）保留 `Request | None` 是正确的——只有作为 FastAPI 依赖的 `require_auth` / `checker` 必须用裸 `Request`。

### 2.9 证伪记录（「测试通过」不是证据）

按项目方法论 A-6：每个修复必须配一次**注入缺陷后测试失败**的证伪。本轮对作用域披露的 3 条断言做了证伪：

```
[证伪] ① /flow 降级路径缺 scope_note        : FAIL(符合预期，断言有效)
[证伪] ② 规模卡不渲染 scope_note            : FAIL(符合预期，断言有效)
[证伪] ③ 筛选器缺总纲作用域声明            : FAIL(符合预期，断言有效)

基线测试应已还原为 PASS： True
证伪结论：3/3 条注入缺陷被测试捕获
```

---

## 三、上轮审计报告的更正（6 处指控不成立）

> 方法论：**审计脚本/推断产出的每一条差异，写进结论前必须回原文核对**。本轮逐条回原文核对，发现上轮报告有 6 处不成立——**其中 2 处引用了根本不存在的文件**。

| 编号 | 上轮指控 | 回原文核对结果 | 判定 |
|---|---|---|---|
| **B-1** | `backend/app/core/security.py:30-31` 硬编码 AES-GCM 密钥，`encryption_key()` 不读环境变量 | **该文件不存在**。`ls app/core/` 无 `security.py`；`grep -rniE "encryption_key\|fernet\|aesgcm\|AESGCM\|cryptography" app/` **0 命中** | ❌ **不成立**（引用了不存在的文件） |
| **B-2** | `main.py:222-233` 生产环境未关闭 `/docs`、`/redoc`、`/openapi.json` | `main.py:231-247` 的 `_docs_paths()` **已实现**：`ENV != "prod"` 全开，`ENV == "prod"` **全关**（`AQP_DOCS_ENABLED=1` 可显式覆盖）。2026-09-30 全检 P1-d 已修 | ❌ **不成立**（且报告建议的修法**正是现有实现**） |
| **B-3** | `backend/app/core/rate_limit.py:12` —— "全站无速率限制" | **该文件不存在**（`ls app/core/rate_limit.py` → No such file）。限流实现在 `app/api/v1/auth.py:57-77`：登录 `_MAX_ATTEMPTS=10 / _WINDOW_SECONDS=300`，注册 `_REGISTER_MAX_ATTEMPTS=5`；且 `ALLOW_REGISTRATION` 在 prod 被 `required_prod_safe` **强制为 false** | ❌ **不成立**（引证错误 + 事实相反）<br>⚠️ 残留有效点：匿名端点 `/market/overview/rt`、`/overview/daily` 确实无限流——但那是**刻意的公开落地页设计**（`App.tsx:87-88`），项目约定**勿"顺手补齐"** |
| **B-4** | 全站缺失 HTTP 安全响应头 | grep 0 命中，**成立** | ✅ **成立 → 已修**（§2.7） |
| **E-5** | 热门榜只对前 300 只算行情 | `etf.py:644` `etf_hot(limit: int = Query(5, ge=1, le=50))`，`rows = items[:limit]`，且带**截断三件套**（`truncated`/`total`/`limit`）如实披露 | ❌ **不成立** |
| **C-3（CI 部分）** | 闸门（`.githooks/pre-commit` + `ci.yml`）不跑 `tsc -b` / `build` / `bundle:check` | 拆开看：**hook 确实只跑 `style:check` + `nav:check`**（grep `tsc\|build\|bundle:check` = 0 命中）✅；但 **`ci.yml` 已跑 `style:check` → `nav:check` → `build`**（`build` 含 `tsc -b`）❌ | ⚠️ **部分成立**（hook 未覆盖，CI 已覆盖） |
| **E-3** | ETF 三重筛选对 `/overview`、`/scale`、`/flow` 不生效 | 端点签名核实：`etf_overview(L522)` 仅 `_user`；`etf_scale(L806)` 仅 `period/top_n`；`etf_flow(L873)` 仅 `period/limit` | ✅ **成立**（但性质是"未披露的口径错位"而非"接口坏了"）→ 已修（§2.6） |

**元教训**：上一轮已出现过「31 条解析器误报」同型问题。**"文件不存在"这种错误本应在生成报告时就被 `ls` 拦下**——说明当时对引用的文件路径**没有做存在性校验**。本轮的核对清单已固化为：引用文件 → 必须 `ls`；引用行号 → 必须回读该行；引用函数 → 必须 grep 定义。

---

## 四、门禁与回归

<!-- GATE_SECTION -->

---

## 五、遗留问题与下一步

<!-- TODO_SECTION -->

---

## 六、覆盖度与局限（诚实声明）

1. **前端布局结论为静态推导**。本机（Windows）浏览器自动化**不可用**，无法截图。P0 修复的证据是「视口推导 + 源码断言 + 闸门全绿」三重**间接**证据，**必须人工目视验收**：以 1320px 视口打开 `/etf` 并刷新，确认主区撑满、无卡片压盖。
2. **前端零运行时测试**（`frontend/src` 下 `*.test.*` / `*.spec.*` = 0 个）。本轮对前端的验证全部是**源码字符串断言**（沿用项目既有范式），它能证明"修复特征在/不在"，**不能证明渲染正确**。
3. **增量补齐的执行结果以最终验收输出为准**（§1.7 / §5）。运行中途的进度快照不构成结论。
4. **本报告未覆盖的**：`baostock` 兜底源失效（§1.2）、`300061.SZ` 单侧到位现象，均在 §5 列出。
