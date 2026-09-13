# AQP 前后端对齐与数据真实性审计报告

> 审计时间：2026-09-02
> 审计范围：后端 70 条路由（`backend/app/`）、前端 16 个页面 + 12 个 api 模块（`frontend/src/`，57 个 ts/tsx、12093 行）
> 方法：三路独立取证（后端实现真实性 / 前后端路径映射 / 前端造假扫描）+ 关键指控逐条人工复核

---

## 结论速览

| 维度 | 结果 |
|---|---|
| 前端有、后端没有 | **0 条** ✅ 前端 60 条请求路径 100% 命中后端路由 |
| 后端有、前端没有 | **5 条完全不可达 + 1 条死代码** |
| 前端确凿造假 | **3 处** |
| 后端确凿空壳 | **1 条**（`/ops/lineage`） |
| 后端半真实（关键字段造假） | **12 条** |
| 前端可疑兜底 | **5 处** |
| 完全干净的页面 | **11 / 16** |

**总体判断**：不存在"前端固定好一整套假数据"的系统性造假——全库 `Math.random()` 命中数为 0，无 `mock/fake/demo` 命名模块，无写死的行情数组。问题集中在 **3 处"看起来像模型输出、实为前端编造"的展示细节**，以及 **认证体系与导出功能完全未接线**。

---

## 一、后端有、前端没有（功能不可达）

### 1.1 认证体系整体悬空 ⚠️ 最严重

| 路由 | 后端 | 前端引用 |
|---|---|---|
| `POST /api/v1/auth/login` | `backend/app/api/v1/auth.py:53` | **0 处** |
| `GET /api/v1/auth/me` | `backend/app/api/v1/auth.py:86` | **0 处** |

**后果**：后端实现了完整的 JWT + RBAC（viewer / researcher / admin 三级权限），但前端**没有任何登录页面**，也从不调用这两个接口。

前端的"登录态"是假的——`frontend/src/api/client.ts:45`：

```ts
const token = localStorage.getItem('AQP_ADMIN_TOKEN');
if (token) { config.headers.Authorization = `Bearer ${token}`; }
```

只是从 localStorage 读一个**用户手动塞进去的静态字符串**，从不做会话校验、不校验过期、不调用 `/auth/me`。这意味着：
- 后端的整套权限体系在生产上形同虚设
- 用户不手动配置 Token 时，所有需要鉴权的接口静默失败
- README 里"创建管理员 / 登录获取 JWT"的文档描述与前端实际能力不符

### 1.2 其余不可达接口

| 路由 | 后端 | 影响 |
|---|---|---|
| `POST /api/v1/backtest/signal-analysis` | `backtest.py:437` | 信号衰减/分层回测能力无任何入口，纯浪费 |
| `GET /api/v1/export/screener` | `export.py:14` | 选股结果导出 Excel 已实现（含 workbook 生成），无 UI 入口 |
| `GET /api/v1/export/backtest` | `export.py:39` | 回测结果导出同上 |

### 1.3 死代码

`frontend/src/api/screener.ts:22-24` 定义了 `backtestApi.run()`（指向 `/backtest/run`），但全项目 grep **仅此 1 处定义、0 处 import**。`Backtest` 页面实际走的是 `strategyBacktestApi`（`api/strategyBacktest.ts:89` → `/backtest/strategy-run`）。

**即 `/backtest/run` 后端有实现、前端有壳、但无页面消费。**

### 1.4 覆盖度统计

后端 66 条业务路由中，前端消费 60 条（**90.9%**）。

---

## 二、前端有、后端没有

**结论：0 条。**

独立程序化验证方式：从 OpenAPI 导出全部路由，与前端实际请求路径集合做差集。

```
前端请求路径总数: 55（静态字面量路径）
前端调用但后端无此路由: (无)
```

12 个 api 模块逐文件通读后确认，全部 60 条路径（含 2 条动态模板路径 `/etf/detail/{code}`、`/studio/mining/status/{task_id}`）均正确命中后端装饰器。无拼错、无前端自造假路径。

**可达性也完全闭合**：`App.tsx:38-53` 注册 16 条路由 ↔ `pages/` 16 个目录一一对应 ↔ `Sidebar.tsx:133-154` 15 个入口（`/etf` 双入口指向 Etf 与 EtfDetail）。无孤儿页面、无断头入口。

---

## 三、数据造假

### 3.1 前端确凿造假（3 处）

#### 🔴 F1. 「信心指数」是前端编出来的公式

`frontend/src/pages/MarketOverview/AiPicksPanel.tsx:117`

```ts
const conf = Math.min(Math.round(Math.abs(it.pred_score) * 1400) + 40, 99);
```

- 表头列名写的是「信心指数」，渲染在「AI 预测精选」表格里，用户会认为是模型输出的置信度
- 实际是前端拿 `pred_score` 绝对值乘 1400 加 40 硬凑的
- **保底 40%**：即使预测涨幅为 0，也显示 40% 信心
- **封顶 99%**：`pred_score ≥ 0.042` 一律显示 99%
- **后端接口无此字段**

这是全项目最具误导性的一处——把一个纯前端算术包装成模型置信度。

#### 🔴 F2. 「今日更新 ✓」无条件显示

`frontend/src/pages/Screener/index.tsx:281`

```tsx
<span className="text-up">今日更新 ✓</span>
```

绿色成功标记**硬编码渲染**，与 `result?.date` 完全无关。即使后端返回上周快照、或接口失败走 error 分支，"今日更新 ✓" 依然亮着。

#### 🔴 F3. 用本地系统时间冒充交易日期

`frontend/src/pages/Screener/index.tsx:193`

```tsx
<span>{result?.date ?? dayjs().format('YYYY-MM-DD')}</span>
<span>{dayjs().format('HH:mm')}</span>
```

- 后端没返回快照日期时，直接拿**浏览器本地日期**冒充交易日
- 旁边的 `HH:mm` 永远是**用户当前时刻**，伪装成行情刷新时间戳
- 周末打开页面会显示周末日期作为"交易日"

### 3.2 后端确凿空壳（1 条）

#### 🔴 B1. `/ops/lineage` 数据血缘图谱完全硬编码

`backend/app/api/v1/ops.py:74-97`

```python
"""数据血缘：节点与边由平台真实数据流抽象（ingest→qc→features→model→pred→desk）。"""
nodes = [{"id": "akshare", "name": "AKShare 上游", ...}, ...]   # 13 个节点全字面量
edges = [("akshare", "daily_bar"), ("daily_bar", "qc"), ...]    # 18 条边全字面量
```

docstring 声称"由平台真实数据流抽象"，实际是写死的常量数组，**不随任何真实数据变化**。前端 `DataQuality` 页把它当真实血缘图展示。

### 3.3 后端半真实：关键字段造假（挑影响最大的）

| # | 接口 | 假的字段 | 证据 | 后果 |
|---|---|---|---|---|
| B2 | `/stock/{sym}/predict` | **confidence** | `ml/predict.py:10` 注释自认"模型级置信度**占位**"；`:40` `0.5 + 2.0*ric`；metrics 缺失时 `:33/:37` 硬编码 `return 0.5` | 同一模型下**所有个股返回同一个置信度**，非个股级 |
| B3 | `/backtest/strategy-run` | **冲击成本** | `backtest/strategy_base.py:383-385` `"volume": {s: 1e12...}, "amount": {s: 1e12...}`，注释："合成流动性……冲击成本因参与率无法计算**自动为 0**" | 策略回测**摩擦成本只有佣金+印花税，冲击成本恒为 0**，回测收益系统性偏乐观 |
| B4 | `/etf/overview`、`/etf/hot` | **etf_count、total_size_yi** | `data/etf.py:284` `JP_CATALOG`（8 只写死）、`:303` `KR_CATALOG`（6 只）、`:319` `US_CATALOG`（16 只）；`:388` `"size_yi": round(mv * 7.2, 2)` 汇率硬编码 | 日韩美 ETF 只有静态目录无行情，却被计入总数与总规模；汇率锁死 7.2 |
| B5 | `/export/screener` | **5 列恒空** | `api/v1/export.py:26-28` `{"name": None, "industry": None, "pred_return": None, "prob_up": None, "confidence": None}` | 导出的 Excel 9 列中 **5 列永远是空的** |
| B6 | `/market/overview` | **sentiment、limit_up/down** | `api/v1/market.py:417` `score = 50 + breadth*45 + (limit_up-limit_down)*1.5`（自造加权）；`:100` `limit_up += p >= 9.8  # 近似：未区分 20%/30% 档` | 情绪分是自拍脑袋的公式，非市场公认指标；涨停判定不看板块 |
| B7 | `/market/overview` | **trade_date** | `data/parquet_store.py:42` `"""占位实现：返回今天（周末回退到周五）"""` | 明明有 6460 个交易日的日历表，却用"周末回退"猜交易日 |
| B8 | `/research/overview` | **capacity_estimate_yi** | `research.py:128` `cap = med_amt * 0.01 * 20` | 1% 参与率、20 只持仓是**固定假设**，非实测容量 |
| B9 | `/datacenter/api-stats` | **整个接口名实不符** | `datacenter.py:419/422` 实际读 `data_update_log` / `data_jobs` | 端点叫"API 请求统计"，实际是**流水线任务统计** |
| B10 | `/settings/apikeys/rotate` | **key** | `app_settings.py:210` `f"aqpx_{secrets.token_urlsafe(24)}"` | 本地随机串，不对接任何鉴权服务，纯装饰 |
| B11 | `/etf/list` | **manager、inception** | `data/etf.py:367` 中国 ETF 恒为 `None`；tracking_index 靠名称关键词猜 | 中国 ETF 的管理人/成立日永远空白 |
| B12 | `/etf/scale` | **points 全为估算** | `etf.py:255` `shares = size_yi*1e8/price`；`:268` `note: "估算口径：最新份额 × 历史收盘价"` | 份额曲线是反推估算值，非真实份额序列 |

### 3.4 前端可疑兜底（5 处）

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| F4 | `pages/EtfDetail/index.tsx:582-591` | 标题「资金流动（基金特定）」下塞的是 `GaugeQuad`（PE/PB/费率/规模分位），代码注释自认"用估值/费率仪表盘**占位**" | 用户以为在看资金流，实际是估值指标 |
| F5 | `pages/Backtest/parts.tsx:22-23` | 默认区间 `start:'2024-08-28', end:'2026-08-28'`，页面挂载即自动首跑 | 结束日期是**未来日期**，回测区间看起来"覆盖到今天之后" |
| F6 | `pages/MarketOverview/BreadthPanel.tsx:119` | 六宫格第 6 格写死 `0`：`[limit_up, up, flat, down, limit_down, 0]` | 该位置本应是真实统计项，现在是永远空白的洞 |
| F7 | `pages/Etf/index.tsx:184` | 热门接口为空时用 `'510300,510500,159915'` 写死代码请求表现曲线 | 图表照样出线，用户无从分辨这 3 只是不是真热门 |
| F8 | `pages/Settings/index.tsx:133-136` | `load()` 失败时回退显示硬编码假用户 `nickname:'Quant User', email:'quant_alpha_user@platform.com'` | 看起来像已登录账户 |

### 3.5 完全干净的页面（11 / 16）

数据 100% 来自真实 API，无硬编码数值、无假兜底：

| 页面 | 数据来源 |
|---|---|
| CapacityAttribution | `deskApi.capacity()/attribution()`，Brinson + 风格回归全在后端 |
| DataCenter | 6 个 `datacenterApi.*`，`Promise.allSettled` 逐块降级 |
| DataQuality | `opsApi.qualityScan()/lineage()` |
| FactorStudio | `studioApi` 真实 GP 进化，fitness_curve 后端轮询 |
| OrderDesk | `deskApi.*` 模拟盘真实撮合，冲击成本真算 |
| Pipeline | `opsApi.dag()`，节点日期来自 `data_jobs` |
| Portfolio | `portfolioApi.backtest()`，无结果显示 EmptyState |
| Research | 10 个 `researchApi.*` 并发，全后端计算 |
| StockDetail | `/panels` 聚合，`status==='unavailable'` 才显示 PanelEmpty |
| Watchlist | `/watchlist/dashboard`，60s 轮询 |
| Backtest（结果区） | `resultParts.tsx` 全部消费 `result` 字段 |

**后端真实实现 56 条**（占 81%），包括：GP 因子挖掘（适应度为真实 RankIC）、PurgedCV 时序切分、`desk` 模拟盘（`impact_bps = IMPACT_COEF_BPS * sqrt(participation)` 真实冲击模型）、特征重要性（真实 LightGBM gain + PDP）、回测 T+1/涨跌停/停牌/整手/佣金印花税闸门。

---

## 四、修复优先级建议

### P0 — 直接误导用户，必须改

1. **`AiPicksPanel.tsx:117` 信心指数**：删掉该列，或改由后端返回真实置信度。现状是前端编造数值冒充模型输出。
2. **`Screener/index.tsx:281`「今日更新 ✓」**：改为 `result?.date === 今日` 才显示。
3. **`Screener/index.tsx:193` 本地时间冒充交易日**：无 `result.date` 时显示"无数据"或"—"，不要用浏览器时间。
4. **`ops.py:74` `/ops/lineage`**：docstring 谎称动态生成，实际硬编码。要么改成真实扫描代码产物，要么在响应里明确标注 `static: true` 让前端标识。

### P1 — 功能断层

5. **认证体系**：二选一——补登录页接上 `/auth/login` + `/auth/me`，或明确下线并删除后端 auth 模块 + README 相关描述。现状最糟：后端有完整 RBAC，前端完全不用。
6. **导出功能**：`/export/*` 两条已实现，前端加个按钮即可，成本很低。
7. **`backtest/strategy_base.py:383-385` 合成流动性 1e12**：让策略回测补上真实成交额数据，否则冲击成本恒为 0，回测收益不可信。建议至少在结果里明示"未计入冲击成本"。

### P2 — 数据质量

8. `etf.py` 日韩美硬编码目录：前端应把它们与有真实行情的中国 ETF 分区展示，不要混进总数与总规模；汇率 7.2 应可配置。
9. `export/screener` 五列空值：补上 name/industry 查询，或去掉这些列。
10. `market.py:417` 情绪分、`parquet_store.py:42` 占位交易日：前者标注为"平台自研口径"，后者改用已有的 6460 日交易日历表。
11. `datacenter/api-stats` 改名或改实现，与端点名对齐。
12. 删掉 `api/screener.ts:22-24` 的 `backtestApi` 死代码。
