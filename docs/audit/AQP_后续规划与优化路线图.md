# AQP 后续规划与优化路线图（实施详版 v2）

> 版本：v2 · 2026-09-05 ｜ 基线：commit `ce4afa6` ｜ 数据规模：1134 只 × 1133 交易日 × 3 复权口径
> 本版在前版基础上补充：架构图、API 契约、数据模型 DDL、涉及文件清单、验收标准、Sprint 排期
> 所有性能/体积数字均为 2026-09-04/05 实测

---

## 目录

- 一、项目现状基线
- 二、数据加载提速（L1~L4 四层方案）
- 三、数据刷新体系（刷新按钮 + 分钟级行情 + SSE）
- 四、新功能模块规划（九大模块）
- 五、模型迭代路线
- 六、工程化与运维
- 七、实施节奏（Sprint 排期）
- 八、风险登记册
- 附录 A：术语表 ／ 附录 B：现有 API 与规划改造对照表

---

## 一、项目现状基线

### 1.1 架构总览

```
┌────────────────────────── 前端 React+Vite+ECharts（:5173）──────────────────────────┐
│  18 页面：MarketOverview / Screener / StockDetail / Backtest / Portfolio /           │
│  DataCenter / DataQuality / FactorStudio / Research / Report / Watchlist / OrderDesk │
│  / Etf(+Detail) / CapacityAttribution / Pipeline / Settings / Login                  │
└───────────────┬──────────────────────────────┬──────────────────────────────────────┘
        REST（统一信封）                SSE（/notify/stream 已有）
┌───────────────▼──────────────────────────────▼──────────────────────────────────────┐
│                    后端 FastAPI（:8000，lifespan 管后台任务）                          │
│  ┌─ api/v1：market·stock·screener·datacenter·backtest·research·etf·notify·monitor…  │
│  ├─ cache：RedisClient（断路器+内存LRU兜底）│ keys.py（k_screener/k_market_overview）│
│  ├─ data：parquet_store·pipeline（7步骤）·ingest(akshare_adapter多源)·realtime·graph │
│  ├─ ml：train_lgbm(生产)·train_service(GNN/TFT)·registry(promote门禁)·monitor(漂移)  │
│  └─ 后台任务：warm_overview_cache（启动预热）·autoSync（60s 盘后增量检查）            │
└──────┬─────────────────┬─────────────────┬──────────────────────────────────────────┘
       │                 │                 │
  SQLite(aqp.db)     Parquet 分区       外部数据源（akshare）
  registry/instrument  daily_bar×3 复权   东财（本机断连）→新浪（慢）→腾讯（稳）
  universe/FeatureRun  features(406M)     三源冗余已在 3 处落地
  DataJob/roles/users  cs镜像(188M)/predictions/announcements
```

### 1.2 页面 × 数据依赖矩阵（提速改造的靶点）

| 页面 | 主要接口 | 数据新鲜度诉求 | 当前体感痛点 |
|---|---|---|---|
| MarketOverview | `/market/overview`（TTL 300s） | 分钟级（实时块）/日级（日频块） | **无 Redis 时常 48s 重建**；降级时 82~142s |
| Screener | `/screener`（TTL 300s）、`/market/index/kline` | 日级榜单 + 分钟级盘面 | 冷查 2.6s；**不知道数据几点更新的、无法手动刷新** |
| StockDetail | `/stock/{sym}/kline·predict·profile` | 日级 K 线 + 实时报价 | 实时报价单只抓取（`fetch_quote`） |
| Watchlist | `useWatchlistQuotes` 轮询 | 分钟级 | 单只串行抓取，标的多了易触限速 |
| DataCenter | `/datacenter/*`、`/train/*` | 实时任务状态 | 任务进度已有轮询，无问题 |
| Backtest/Research/Report | `/backtest*`、`/research/*` | 日级 | 回测冷启动秒级，可接受 |

### 1.3 数据资产盘点（实测磁盘分布，2026-09-05）

| 目录 | 体积 | 说明 | 增长预期（扩到 5552 只全市场） |
|---|---|---|---|
| `data/parquet/features` | **406M** | 124 万行 × 44 列，按年分区 | ×5 ≈ 2G；propagate 接线后 ×2 ≈ 4G |
| `data/parquet/cs` | **188M** | 三口径截面镜像 | ×5 ≈ 1G |
| `data/parquet/daily_bar{,_hfq,_qfq}` | 87M ×3 = 261M | 124 万行/口径 | ×5 ≈ 1.3G |
| `data/parquet/universe_daily` | 40M | 128 万行 | ×5 ≈ 200M |
| `data/parquet/predictions` 等 | ~5M | — | 线性小增 |
| `data/models`（prod+exp） | 528K | LGBM/GNN 产物 | 每模型 <1M |
| legacy 目录（models_legacy / backup_* / quarantine / test_parquet / universe_daily_legacy） | **合计 ~12M** | 历史遗留，可归档 | — |
| `.git` | 9.9M | 重建后（2026-09-02） | — |

**关键结论**：AQP 项目本体 ≈ 1GB，D 盘 99% 占用主要来自**外部数据**（652G 总量）。治理动作应是①对 D 盘外部大文件做排查清理指引；②AQP 自身设置水位预警；③legacy 目录归档（收益小但顺手）。**全市场扩容（预计 +3~4G）在清理后完全可行。**

### 1.4 性能实测基线（优化前，验收对照用）

| 环节 | 实测值 | 场景 |
|---|---|---|
| `/market/overview` 全量重建 | ~48s（设计文档口径）/ 82.4s、142.4s（本机日志实测，外部源降级时） | 无缓存首请求 |
| `/screener` 冷查询 | 2.6s | Redis 命中后 <5ms（TTL 300s） |
| `/market/index/kline` | 秒级（400 根，腾讯源 + fetch 层 TTL 缓存） | 五指数同图 |
| cs mirror 全量重建 | ~6s/口径（1134 只） | 批量抓取后 |
| features 全量重建 | 27s（124 万行 × 44 列） | 盘后流水线 |
| GNN 训练 | 26.5s（30 epochs，XPU，panel 1133×1133×42） | — |
| LGBM 训练 | 22s（124 万行，早停 26 轮） | xsec_demean 配方 |
| 批量行情抓取 | 串行 9~19s/只、3 线程 ~5s/只（新浪限速为延迟型） | 扩容脚本实测 |

### 1.5 风险与债务登记表

| # | 项 | 现状 | 等级 | 对应章节 |
|---|---|---|---|---|
| R1 | D 盘余量 8.6G（99%） | 持续写入随时写满 | 🔴 | §6.1 |
| R2 | git 仓库无远端 | 单机裸奔（9-02 曾发生 .git 损坏） | 🔴 | §6.2 |
| R3 | 数据源单点脆弱 | 东财断连 24h+；新浪限速；仅腾讯稳定 | 🟠 | §3.3/§6 |
| R4 | `propagate` 未接线 | 代码就绪（分块矩阵乘已优化） | 🟠 | §4.2 |
| R5 | 用户对数据新鲜度无感知、无刷新入口 | screener 缓存 5 分钟不可控 | 🟠 | §3.2 |
| R6 | GNN 无信号 | 1 层 GCN+MSE 容量瓶颈（已排除标签因素） | 🟡 | §5 |
| R7 | 申万行业接口 bug | akshare 本版解析错误（Length mismatch） | 🟡 | §4.2 |
| R8 | `app/e2e` 依赖 Playwright | 本地默认跳过，CI 未跑 | 🟡 | §6.4 |

---

## 二、数据加载提速（四层方案）

> 总思路：**把"计算"和"外部抓取"从用户请求路径上挪走**，用户永远只读"已算好的东西"。

### 2.1 L1 · 缓存层改造（工作量 2~3 天，收益最大）

**L1-1 Redis 常驻化**

- 现状：docker-compose 已含 Redis 定义，但默认未启动；无 Redis 时内存 LRU 兜底 TTL 300s，**进程重启即失**，重启后首个用户请求扛全量重建。
- 改法：
  1. `run.md` / `docker-compose.yml` 把 `docker start aqp-redis`（或 compose profile）设为部署默认步骤；后端启动时 `RedisClient.ping()` 失败则打 WARN 日志并在前端设置页展示"缓存降级"状态（`/settings` 已有基建可挂）。
  2. `warm_overview_cache`（`app/api/v1/market.py:510`）已实现"启动预热 + 后台重建"，Redis 常驻后预热成果跨重启存活。
- 涉及文件：`docker-compose.yml`、`run.md`、`app/api/v1/app_settings.py`（状态披露）、`app/cache/redis_client.py`（无需改）。
- 验收：重启后端，首个 `/market/overview` 请求 <300ms；`from_cache=true`。

**L1-2 stale-while-revalidate（过期先回旧值）**

- 现状：TTL 过期 → 该请求同步等待全量重建（overview 即 48s）。
- 改法：读缓存时若 `TTL < 过期阈值` 但 `TTL > 硬过期阈值`（如 300s 过期后 30 分钟内），**直接返回旧值**（响应加 `"stale": true`），同时 `asyncio.create_task` 后台重建并回写；超过硬过期才同步重建。
- 涉及文件：`app/api/v1/market.py`（overview 读写缓存段）、`app/api/v1/screener.py`（同款逻辑）。
- 前端配合：`stale=true` 时时间戳旁显示"（缓存，正在更新）"。
- 验收：TTL 过期后的首个请求 <500ms 且带 `stale:true`，10s 后再请求为新值。

**L1-3 缓存击穿合并（Redis 层）**

- 现状：进程内 `_cached` 已做并发合并（`app/api/v1/datacenter.py:65`），但 Redis 层多 worker 并发时仍可能同时重建。
- 改法：`RedisClient` 增加 `get_or_build(key, ttl, builder)`：`SET NX EX 10` 抢重建锁，未抢到的请求短轮询读新值（上限 3s），超时回退旧值/内存值。
- 验收：压测 50 并发未命中请求，重建仅执行 1 次。

### 2.2 L2 · 物化层（工作量 3~5 天）

**L2-1 screener 按日快照表**

- 现状：`/screener` 每次实时计算（读 predictions parquet + universe parquet join + 过滤 + 截断），冷 2.6s。
- 数据模型（SQLite，与现有库同文件）：

```sql
CREATE TABLE IF NOT EXISTS screener_snapshot (
  date TEXT NOT NULL,            -- YYYY-MM-DD
  strategy TEXT NOT NULL,        -- alpha_basic_v1
  board TEXT NOT NULL,           -- all/main/chinext_star/bse
  rank INTEGER NOT NULL,
  symbol TEXT NOT NULL, name TEXT, industry TEXT,
  pred_score REAL, model_version TEXT,
  PRIMARY KEY (date, strategy, board, rank)
);
CREATE TABLE IF NOT EXISTS screener_snapshot_stats (
  date TEXT NOT NULL, strategy TEXT NOT NULL, board TEXT NOT NULL,
  pool_size INTEGER, total INTEGER, trade_date TEXT,
  PRIMARY KEY (date, strategy, board)
);
```

- 写入时机：盘后流水线 `step_screener_dump`（`app/data/pipeline.py:201`）扩展——对 all/main/chinext_star/bse 四个 board 各物化 top-200（写入用 `INSERT OR REPLACE`，幂等）。
- 读取路径：`/screener`（`app/api/v1/screener.py:263`）优先查快照表（目标 <200ms），无当日快照才回落实时计算，并在响应标注 `from_snapshot: true/false`。
- Redis 缓存保留（快照读也走缓存，命中率极高且零重建成本）。
- 验收：当日快照存在时 P95 <200ms；盘后流水线跑完快照 4 板块齐全。

**L2-2 overview 聚合分离**

- 现状：`_build_overview`（market.py）把指数快照、涨跌分布、资金流、AI 推荐榜、情绪指标一次性聚合，任一外部源慢则整体慢。
- 改法：拆分为两个缓存键、两种节奏——
  - **实时块**（`k_market_overview_rt`，TTL 30~60s）：指数最新值 + 资金流，只依赖腾讯源（稳定快）；
  - **日频块**（`k_market_overview_daily`，TTL 至次日盘后）：涨跌分布（读 cs mirror 本地计算，无外部依赖）、推荐榜（读 predictions）、情绪（读 announcements 派生）。
  - 前端两个请求并行拉取，实时块单独刷新（配合 §3 刷新按钮的"轻刷新"只刷实时块）。
- 验收：实时块 P95 <500ms；日频块盘后物化后 <100ms；外部源全挂时日频块不受影响。

**L2-3 数据集 stats manifest**

- 现状：`read_all_symbols`（parquet_store.py:170）每次 glob 分区目录 + 读 footer；`fetch_universe_batch.py` 的跳过逻辑是脚本内私有实现。
- 改法：`parquet_store` 维护 `DATA_ROOT/.manifest.json`（dataset → {symbol: {rows, first_date, last_date}}），写入路径（`write_partition` 等）增量更新；读取 API 优先查 manifest。抽出脚本里的"已有分区跳过"为公共函数。
- 验收：`read_all_symbols('daily_bar')` <10ms；批量脚本与 manifest 一致。

### 2.3 L3 · 存储层（工作量 3~4 天，为全市场扩容铺路）

| 项 | 方案 | 理由 |
|---|---|---|
| 列裁剪+谓词下推 | 全仓统一 `scan_parquet().select().filter().collect()` 风格，禁止 collect 后过滤 | features 44 列只取所需 8~10 列时 IO 降 75%+ |
| 热月分区 | 当年数据按 `year=/month=` 双层分区（仅新写入迁移，历史不动） | K 线/截面查询只扫必要月份；5552 只下收益显著 |
| 压缩调优 | 写 parquet 时显式 `compression="zstd", compression_level=7`（当前默认 snappy） | 预估体积 -30~40%；读性能持平或略优 |
| DuckDB（可选） | 引入 DuckDB 直接查 parquet 服务研究类 SQL 查询 | 当前 polars 够用；仅在因子工场 SQL 化需求出现后再评估，**暂不做** |

**验收**：全市场（5552 只）扩容后 `/screener`（走快照）仍 <300ms、K 线 <600ms、mirror 重建 <60s。

### 2.4 L4 · 前端层（工作量 2~3 天）

- **路由懒加载**：`App.tsx` 18 个页面改 `React.lazy(() => import(...))` + `<Suspense fallback={<PageSkeleton/>}>`；首屏 JS 体积预计 -60%（ECharts 与重页面是大头）。
- **请求层缓存**：引入 SWR（体积小、API 简单；TanStack Query 功能全但重）。统一配置 `refreshInterval` 按端点分级（实时 30s / 快照 5min / 静态 0）、`revalidateOnFocus`、`dedupingInterval`；与后端 L1 的 stale 策略叠加后**页面二次切换 <100ms**。
- **骨架屏**：K 线/榜单/表格三类骨架组件（现有 `LoadingState` 升级），替换所有 spinner。
- **ECharts 按需引入**：`echarts/core` + 按需注册（line/bar/graph/heatmap），当前全量引入。
- **验收**：生产构建后 Lighthouse 首屏 LCP <2s（本地网络）；任意页面二次切换 <100ms。

### 2.5 性能预算总表（验收基准）

| 接口/场景 | 现状 | 热路径目标 | 冷路径目标 | 达成手段 |
|---|---|---|---|---|
| /market/overview 实时块 | 48s 重建 | <300ms | <500ms（stale 回旧值） | L1-1/2 + L2-2 |
| /market/overview 日频块 | 同上 | <100ms | <300ms | L2-2 物化 |
| /screener | 2.6s 冷 | <200ms | <800ms | L2-1 快照 |
| /screener 页面二次切换 | 1~3s | **<100ms** | — | L4 SWR |
| /stock/{sym}/kline | ~1s | <300ms | <600ms | L3 列裁剪 + 前端缓存 |
| /market/quotes（新） | — | <200ms | — | §3.3 批量+短 TTL |

---

## 三、数据刷新体系（刷新按钮 + 分钟级行情 + SSE）

### 3.1 需求场景矩阵

| 场景 | 数据 | 新鲜度 | 实现层 |
|---|---|---|---|
| 用户怀疑页面数据旧了 | 任一缓存接口 | 立即重算 | 刷新按钮 `refresh=1` |
| 盘中看指数/自选股价格 | 实时快照 | 15~60s | 批量行情接口 |
| 盘中多页面同时盯盘 | 实时快照广播 | 30s | SSE quotes 频道 |
| 预警触发通知 | 事件 | 秒级 | SSE alerts 频道 |
| 盘后补最新日线 | daily_bar 增量 | 盘后 | autoSync（已有）+ 强制刷新入口 |

### 3.2 全局刷新按钮（P0，1~2 天）

**前端组件规格**（新建 `frontend/src/components/DataFreshness.tsx`，投放所有数据页 Card 头部）：

```
[ 数据截至 2026-09-04 15:00 · 缓存 ]  [⟳ 刷新]
└─ 时间戳来源：响应体 trade_date / as_of / ts
└─ 状态徽标：实时(绿) / 缓存(灰) / 降级(橙, from_cache=false 且 source 降级)
└─ 刷新点击：图标旋转 → 请求完成后时间戳与徽标更新
└─ 防抖：请求进行中禁用；5s 内重复点击合并
```

**后端 API 契约**（适用于 `/market/overview`、`/screener`、`/market/index/kline`、`/datacenter/overview`）：

```
GET /api/v1/<endpoint>?refresh=1
语义：
  1. 跳过缓存读，强制重算（重建路径与现有缓存 miss 相同）
  2. 重算结果回写缓存（Redis + 内存）
  3. 响应体 from_cache = "refreshed"（与 true/false 区分）
防抖（防刷爆外部源）：
  Redis SET NX EX 5 锁 "refresh:{cache_key}"
  锁被占时直接返回当前缓存值 + from_cache=true + refreshed_recently=true
```

- 涉及文件：`app/cache/redis_client.py`（加 `try_lock` helper）、`app/api/v1/market.py`、`screener.py`、`datacenter.py`（各端点加 `refresh: int = Query(0)` 参数）。
- **红线**：`refresh=1` 不得绕过限速（`_throttle`）与质量门禁，只绕过缓存**读**。

### 3.3 批量实时行情接口（P1，2~3 天）

**API 设计**：

```
GET /api/v1/market/quotes?symbols=600519.SH,000001.SZ,...&fields=default
  · symbols 必填，上限 200 只（超限业务码 4001）
  · 返回统一信封；quotes 为数组
响应示例：
{
  "code": 0, "data": {
    "as_of": "2026-09-05 14:59:59",
    "source": "tencent",
    "quotes": [
      {"symbol": "600519.SH", "name": "贵州茅台", "price": 1302.8,
       "pct": 0.0062, "open": 1295.0, "high": 1307.99, "low": 1286.1,
       "prev_close": 1294.8, "volume": 3120000, "amount": 4.05e9}
    ]
  }
}
```

**实现要点**：
- 数据源：腾讯批量 `https://qt.gtimg.cn/q=sh600519,sz000001,...`（一次 HTTP 拉 N 只，实测稳定；`realtime.py` 已有 `fetch_tencent_quote` 单只版与 `_request` 基建，抽出批量解析）；
- 降级链：腾讯 → 新浪（`stock_sector_detail` 同款 host 可达）→ 空数组 + `source:"degraded"`（**不造数**）；
- 限速：批量接口本身 1 次请求 = 1 次 `_throttle` 配额，全局限速锁复用（`AKSHARE_RATE_LIMIT`）；
- 缓存：进程内 TTL 15s（配置 `QUOTES_TTL`，`app/core/config.py` 新增），相同 symbols 集合共享（key 为排序后的 symbol 列表哈希）；
- 前端消费方：Watchlist `useWatchlistQuotes`（升级为批量）、Screener"今日实时"列、MarketOverview KPI 卡。

**验收**：50 只单次 <300ms；200 只 <500ms；盘中轮询 30s 间隔持续 10 分钟无封禁告警。

### 3.4 SSE 推送协议（P1，2~3 天）

**现状**：`/notify/stream`（`app/api/v1/notify.py:18`）已有 SSE 通道（当前仅通知用途）。

**扩展协议**：

```
GET /api/v1/notify/stream?channels=quotes,alerts&symbols=600519.SH,...&ttl=30

event: quotes                        ← 每 ttl 秒一条
data: {"as_of": "...", "quotes": [...]}

event: alerts                        ← 预警触发时（见 §4.1）
data: {"rule_id": 3, "rule": "贵州茅台涨跌幅>3%", "symbol": "600519.SH", ...}

: ping                               ← 心跳，每 15s
```

**实现要点**：
- 服务端单个抓取任务广播给所有订阅者（`asyncio.Queue` 每 client 一份；快照语义，无需 Last-Event-ID）；
- 断线：浏览器 `EventSource` 自动重连，重连后拿最新快照即对齐；
- 关闭页面即断订阅，无僵尸抓取（抓取任务引用计数归零时停）；
- **纪律**：SSE 抓取频率 ≥ 全局限速允许值；多订阅者共享同一次抓取，外部源压力与订阅数无关。

**验收**：3 个标签页同时订阅，Network 面板各只有 1 条 stream 连接，10 分钟内外部请求次数 = 单订阅者。

### 3.5 数据一致性口径（纪律红线）

- 盘中快照（`/market/quotes`、SSE）**只用于展示**，禁止写入 `daily_bar`（日线必须走盘后抓取 + `validate_write_gate` 质量门禁）；
- 盘中页面的时间戳必须显示 `as_of`（快照时间），与日线 `trade_date` 视觉区分（如"实时 14:59:59"vs"日线 2026-09-04"）；
- 涨停/跌停价等衍生值如需展示，须标注口径来源。

### 3.6 实施顺序与测试

1. 刷新按钮（后端 refresh 参数 + 前端组件）→ 2. 批量行情接口 → 3. SSE quotes → 4. Watchlist/Screener 接入。
测试：后端新增 `test_market_quotes.py`（批量解析 mock + 上限校验 + 缓存命中）；SSE 用 `TestClient.stream` 断言事件格式；前端 `tsc` + 手动验收清单。

---

## 四、新功能模块规划（九大模块）

> 每个模块按「场景 → 功能清单 → 数据模型 → API → 依赖 → 验收」展开，P0/P1 功能在模块内标注。

### 4.1 智能盯盘与预警中心（P1，价值最高）

**场景**：因子、模型、自选股能力都已就位，但全部是"用户主动看"。预警中心让平台**主动找用户**——也是把 §3 实时基建变现的最短路径。

**功能清单**：
- P0：规则 CRUD（六类规则）+ 站内信（SSE alerts + 历史落库）+ webhook 通知
- P0：数据健康类规则（数据源降级、流水线失败、磁盘水位——当前 D 盘 99% 即典型场景）
- P1：企业微信/钉钉/邮件渠道；冷却期内同规则去重
- P2：规则回测（"这条规则过去 30 天会触发几次"，防骚扰调参）

**规则类型**（`rule_type` + `params_json`）：

| 类型 | 触发条件示例 | 数据依赖 |
|---|---|---|
| `price_pct` | 单日涨跌幅超 ±N% | 批量行情快照 |
| `price_cross` | 上穿/下穿价格阈值 | 同上 |
| `volume_spike` | 成交量 > N 日均量 × k | 快照 + daily_bar |
| `score_topk` | 自选股进入/跌出模型 top-K | predictions（日级） |
| `factor_quantile` | 因子值突破 N 日分位 | features 最新日 |
| `data_health` | 源降级/流水线失败/磁盘水位 | 内部状态 |

**数据模型**（SQLite，与主库同文件）：

```sql
CREATE TABLE IF NOT EXISTS alert_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'symbol',   -- symbol | watchlist | global
  symbol TEXT,
  params_json TEXT NOT NULL,              -- {"threshold":0.03,"window":20,"k":3.0}
  channels_json TEXT NOT NULL DEFAULT '["sse"]',
  cooldown_minutes INTEGER NOT NULL DEFAULT 30,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id INTEGER NOT NULL,
  symbol TEXT,
  triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  payload_json TEXT NOT NULL,
  is_read INTEGER NOT NULL DEFAULT 0
);
```

**API**：
- `GET/POST /alerts/rules`，`PUT/DELETE /alerts/rules/{id}`
- `GET /alerts/events?unread=1&limit=50`，`POST /alerts/events/read`（批量已读）
- 评估调度：lifespan 后台任务（autoSync 同款模式），盘中每 30s，盘后每小时

**依赖**：§3.3 批量行情（价格/量类规则）；`NOTIFY_WEBHOOK_URL` 已有。
**验收**：建一条"自选股涨跌幅>3%"规则，模拟数据触发后 SSE 收到事件 + webhook 收到 POST + 历史可查。

### 4.2 产业链图谱 + propagate 因子接线（P1，ML 性价比最高）

**propagate 接线方案**（先做，2~3 天）：

1. `step_build_features`（`app/data/pipeline.py:128`）末尾追加：
   ```python
   from ..ml.graph import build_adjacency, propagate
   idx, A = build_adjacency(sorted(df["symbol"].unique()))
   df = propagate(df, A, idx, factor_columns(df), hops=1)   # 追加 g1_* 44 列
   ```
2. **特征版本 bump**：`FEATURE_VERSION = "alpha_basic_v2"`（特征空间扩展属语义变化；v1 保留供 A/B 对照，registry 的 feature_version 字段已支持区分）；
3. `train_lgbm._EXCLUDE` 白名单无需改（g1_ 自动入选，IC 筛选自动取舍）；
4. 重训（xsec_demean 配方）→ promote 门禁 → 观察 valid RankIC 增量（**预期 +0.01~0.03**，行业边已 4589 条）；
5. 磁盘预估：features 406M → ~800M（可控）。

**图谱可视化模块**（propagate 落地后，2~3 天）：
- 新页面 `GraphExplorer`：ECharts `graph` 力导布局渲染同业网络（618 只标注节点 + 4589 边已就绪）；点击节点 → 侧栏"本股 vs 邻居均值"因子对比条形图（直接用 g1_ 列）；
- 行业过滤器 + 桶规模上限提示（`MAX_PEER_BUCKET=150` 跳过的脏桶如实展示）；
- 数据扩充入口：`relations/edges.parquet` 管理界面（上传 CSV → 校验 src/dst/weight → 版本记录）。

**依赖**：R7 申万行业（换源：乐咕乐股页面自建解析，或等 akshare 修复；当前 49 新浪桶可先行）。
**验收**：v2 特征重训后 LGBM 候选进门禁评估；图谱页面 3s 内完成渲染。

### 4.3 因子工场增强（P2，1~2 周）

| 功能 | 说明 | 依赖 |
|---|---|---|
| 自定义因子表达式 | 白名单表达式引擎（`+ - * / log rank ts_mean ts_std abs clip` 等，拒绝任意 eval）；校验 → 试算（最近 30 日）→ 入库 | FactorStudio 已有框架 |
| 因子检测报告 | IC/RankIC 时序、ICIR、**5 分组分层收益**、衰减分析（1/5/10/20 日）、换手率；一键导出 | features + predictions 基建 |
| 因子库治理 | 元数据表（口径/依赖/负责人/版本）+ 死因子下线流程（被模型 kept_features 长期未引用） | registry 已有 kept_features 字段 |
| 一键 propagate | 任意因子生成 g1_/g2_ 邻居传导版（矩阵乘已优化，1133 只毫秒级） | §4.2 |

### 4.4 回测引擎增强（P2，1~2 周）

- **组合构建**：Top-K 等权 → 可选**波动率倒数加权** / 风险预算，行业中性约束（行业数据已有 618 只覆盖）；
- **成本模型**：佣金/印花税/过户费按真实费率表 + 冲击成本（成交额占比非线性），`enable_friction` 已有开关，细化参数化；
- **参数寻优**：Optuna（TPE）+ walk-forward 折外验证防过拟合；寻优报告自动落 `data/models/exp/`；
- **归因**：Brinson 行业归因 + 因子归因（配合 4.3）；
- **基准叠加**：任意核心指数基准曲线（`/market/index/kline` 已上线，直接复用）；
- 验收：寻优一次 ≤30 分钟（124 万行数据量级）；归因报告数字与回测引擎自洽。

### 4.5 模型实验中心 ML Lab（P2，1~2 周）

- **实验看板**（Research 页扩展）：多候选对比视图——RankIC/ICIR/RMSE 雷达图 + 分年度稳定性热力图（registry 已有全部 lineage 数据）；
- **Ensemble**：LGBM + TFT rank-average / 加权融合，融合版同样走 register → 门禁 → promote 流程（治理不变）；
- **超参搜索**：Optuna + 固化 xsec_demean 配方为默认；
- **champion-challenger 常态化**：每次盘后自动训一版 challenger，周末自动对比报告（monitor 已有大半能力，加对比推送）。

### 4.6 报告自动化（P2，1 周）

- **每日晨报**（盘后流水线尾部挂载）：昨日复盘（指数/涨跌分布）+ 榜单变动 + 模型分数迁移 + 数据质量摘要；Jinja2 模板 → HTML/PDF；走 notify 渠道分发；
- **周报**：组合归因 + 因子表现 + 回测对照；
- 依赖：Report 页已有框架 + §4.1 预警渠道复用。

### 4.7 组合与风控（P3，2~3 周）

- 持仓体检：行业/风格集中度、Beta 暴露、个股权重上限告警；
- 风险度量：VaR/ES（历史模拟法）、最大回撤预算、相关性矩阵热力图；
- 压力测试：历史极端日重放（场景库：2015 股灾、2024 年初流动性冲击等）。

### 4.8 数据扩展（P2~P3，按需）

| 数据 | 动作 | 前置 | 预估成本 |
|---|---|---|---|
| 全市场 4400 只 | `fetch_universe_batch.py` 后台续跑（断点续传已就绪） | **磁盘治理（§6.1）先行** | 3~5 天机器时 + 3~4G 磁盘 |
| 申万行业分类 | 换源自建解析（akshare 接口 bug 规避） | 无 | 半天 |
| 供应链/股权关系 | 公告 NLP 抽取或第三方数据源 | 4.2 图谱模块 | 1~2 周 |
| 财报数据 | `financials.py` 骨架补全 + 基本面因子 | 无 | 1 周 |
| 期权/股指期货 | akshare 接口接入，对冲回测 | 4.4 | 1 周 |
| 港股/美股 ETF | etf 模块已部分支持（美股规模换算已有） | 无 | 按需 |

### 4.9 体验与终端（P3）

- 响应式改造（平板/手机看盘）或 PWA 离线缓存；
- 命令面板 Ctrl+K（快速跳转 + 搜索接口已有）；
- 暗色主题（现有 CSS 变量体系改造成本低）。

---

## 五、模型迭代路线

### 5.1 迭代矩阵（按性价比排序）

| 优先级 | 实验 | 依据/假设 | 评估口径 | 预期 |
|---|---|---|---|---|
| ✅ 已完成 | xsec_demean 标签 | L2 被市场方差主导 | valid RankIC/ICIR | **0.043→0.092 / 0.42→0.78（已入生产）** |
| ① | propagate g1_ 因子接 LGBM | 行业邻居均值含增量信息 | v1 vs v2 特征同门禁对比 | RankIC +0.01~0.03 |
| ② | 标签行业中性化（残差化） | 行业共性收益是噪声 | 同上 | ICIR 提升为主 |
| ③ | 特征交互项 + top_k 放宽 | 非线性组合有增量 | 同上 | 保守预期 |
| ④ | LGBM+TFT ensemble | 序列模型与树模型互补 | 融合 vs 单模型 | 稳健性提升 |
| ⑤ | GNN 排序目标（pairwise loss） | MSE 非瓶颈、目标错位（去均值实验已证明） | valid RankIC | 未知，风险中高 |
| ⑥ | GNN 2 层 + 残差 / GAT | 容量瓶颈 | 同上 | 未知 |

### 5.2 统一评估协议（所有实验遵守）

- 三段切分 + embargo 不变（`split_dates`，gap ≥ horizon）；
- 决策只看 valid 段四维指标（promote 门禁现行标准），test 只做审计；
- 每个实验 registry 留痕（version_suffix 命名实验名），`feature_version` 区分特征空间；
- **红线**：禁止用 test 调参；禁止合成/硬编码数据；派生指标必须带 basis。

---

## 六、工程化与运维

### 6.1 磁盘治理（🔴 P0）

实测结论：AQP 本体 ≈ 1G（features 406M + cs 188M + daily_bar×3 261M + 其他），**D 盘压力来自外部**（652G 总量、余 8.6G）。动作：

1. **AQP 侧**（半天）：legacy 目录归档（models_legacy / universe_daily_legacy / backup_turnover_fix_20260902 / test_parquet，合计 ~12M，移至归档盘或删除前确认）；parquet 新写入启用 zstd（§2.3，预估 -30%）；
2. **外部侧**（用户配合）：排查 D 盘大文件（可用 WizTree/TreeSize 扫描，非 AQP 范畴）；
3. **防复发**：磁盘水位进 `data_health` 预警规则（§4.1，阈值 90%/95% 两级）；
4. 全市场扩容（+3~4G）在上述完成后无风险。

### 6.2 备份与容灾（🔴 P0）

- **git 远端**（立即）：GitHub/Gitee 私库 + push（当前 9.9M，秒级）；后续可自动化为每日 push 的 scheduled 任务；
- SQLite：每日 `VACUUM INTO` 备份 + 保留 7 份；WAL 模式下备份用 sqlite3 在线 API（`.backup`），不直接拷文件；
- Parquet：增量备份方案（rsync 式同步到外置盘/云盘，1G 起步量小）；
- 模型产物：`models/prod` 随 git LFS 或单独归档（当前 <1M，直接 git 即可）。

### 6.3 可观测性（P1）

- 慢接口中间件：>500ms 记 WARN（含 trace_id），聚合出周 P95 报表（挂 Report 或独立 `/ops/metrics` 页）；
- 前端错误上报：`window.onerror` → `/ops/frontend-error`（采样 10%）；
- 任务审计：DataJob 表已有，补一个失败率看板。

### 6.4 CI/CD（P1）

- GitHub Actions 三门禁：`pytest -q`（337+ 用例）→ `npx tsc --noEmit` → `npm run build`；
- 注意沙箱坑已沉淀技能（safe-delete 环境变量解法、禁 git gc 红线——见项目记忆）；
- Playwright E2E 设为 nightly（`E2E_AVAILABLE=true`），不进 PR 门禁（R8）。

### 6.5 部署（P2）

- Dockerfile（后端/前端）+ compose profile 完善，目标 `docker compose up` 一键起全栈（含 Redis）；
- `.env.example` 补全所有配置项说明；
- HTTPS 在反代层（Caddy/Nginx）解决。

---

## 七、实施节奏（Sprint 排期，2 周/Sprint）

### Sprint 1（本周）——止血与快速见效

| 事项 | 工作量 | 交付物 |
|---|---|---|
| git 远端 + 每日 push | 0.5 天 | 容灾闭环 |
| 磁盘治理（归档+zstd+水位预警接线） | 0.5~1 天 | 余量恢复 + 防复发 |
| 刷新按钮（refresh=1 + DataFreshness 组件） | 1~2 天 | 每页可见"数据截至+手动刷新" |
| Redis 常驻 + SWR（L1-1/2） | 1~2 天 | overview 不再 48s |
| 全量回归 + 验收报告 | 0.5 天 | — |

### Sprint 2——实时体系

| 事项 | 工作量 | 交付物 |
|---|---|---|
| 批量行情接口 `/market/quotes` | 2~3 天 | 200 只 <500ms |
| SSE quotes/alerts 频道 | 2 天 | 多页面共享单次抓取 |
| Watchlist/Screener/Overview 接入实时数据 | 1~2 天 | 分钟级看盘 |
| 预警中心 MVP（规则引擎 + SSE + webhook + data_health） | 3 天 | 平台主动化第一步 |

### Sprint 3——物化与 ML 增量

| 事项 | 工作量 | 交付物 |
|---|---|---|
| screener 快照表 + overview 拆分（L2） | 3 天 | 冷查 2.6s→<200ms |
| propagate 接线 + v2 特征 + LGBM 重训进门禁 | 3 天 | 预期信号增量 |
| 前端懒加载 + SWR + 骨架屏（L4） | 2~3 天 | 页面切换 <100ms |
| manifest（L2-3） | 1 天 | 全市场扩容铺路 |

### Sprint 4——深度功能（按需排期）

因子工场表达式引擎 / 回测组合优化 / ML Lab 看板 / 报告自动化（各 1~2 周，可并行两条线）；全市场数据扩容后台跑（不占人力）。

---

## 八、风险登记册

| # | 风险 | 概率 | 影响 | 缓解措施 | 触发预案 |
|---|---|---|---|---|---|
| R1 | 磁盘写满 | 高（当前 99%） | 全平台不可写 | §6.1 治理 + 水位预警 | 预警 90% 即冻结抓取任务 |
| R2 | 数据源封禁（东财已断 24h+） | 中 | 抓取中断 | 三源冗余已验证；批量接口降请求频次；全局限速统一 | 切换源优先级；告警进 data_health |
| R3 | 新浪限速升级（拒绝型） | 中 | 扩容/实时受阻 | 监控延迟形态变化；腾讯源扩容行情能力 | 降并发 + 错峰 |
| R4 | 单机性能边界（XPU 共享显存 16.5G） | 低 | 全市场训练受限 | GNN 面板 (1133×1133×42) 已近舒适上限 | 特征采样/分批训练 |
| R5 | 全市场 parquet 扫描线性劣化 | 中 | 查询变慢 | L2/L3 先行再扩容 | manifest + 热月分区兜底 |
| R6 | 复杂度债务（18 页面+多引擎） | 中 | 维护成本升 | 新模块"代码+测试+文档"三件套；CI 门禁 | — |
| R7 | Redis 依赖加深 | 低 | Redis 挂时退化 | 断路器+内存 LRU 兜底已有 | stale 回旧值策略兜底 |

---

## 附录 A：术语表

| 术语 | 含义 |
|---|---|
| 三段切分 | train / gap(embargo) / valid / gap / test，防前视泄漏 |
| xsec_demean | 标签横截面去均值（同日减全市场均值），2026-09-05 起生产配方 |
| promote 门禁 | 候选转生产的四维校验（valid RankIC/IC/ICIR/RMSE 全面不劣于现生产） |
| propagate | 邻居传导因子：g1_x = (A·X)_symbol，同业因子均值 |
| cs mirror | daily_bar 三口径的按日截面分区镜像（MED-003） |
| SWR | stale-while-revalidate：先回旧值、后台刷新 |
| stale 响应 | 缓存已过软过期但未过硬过期时返回的旧值（带 stale:true） |

## 附录 B：现有 API 与规划改造对照表

| 端点 | 现状 | 规划改造 |
|---|---|---|
| `GET /market/overview` | TTL 300s 整体缓存 | 拆实时块/日频块；`refresh=1`；stale 回旧值 |
| `GET /screener` | Redis TTL 300s 实时计算 | 快照表优先；`refresh=1`；stale |
| `GET /market/index/kline` | 腾讯源 + fetch 层 TTL | `refresh=1`（低优先） |
| `GET /stock/{sym}/kline` | parquet + 指标增强 | L3 列裁剪；前端 SWR |
| `GET /notify/stream` | SSE（通知） | 扩 quotes/alerts 频道 |
| `POST /datacenter/sync` 等 | 盘后同步（断点续传） | 强制刷新入口复用；autoSync 不变 |
| `GET /train/readiness·status` 等 | 训练任务管理 | 不变 |
| `GET /backtest*` | Top-K / 策略双引擎 | 组合优化与寻优（P2） |
| **新增** `GET /market/quotes` | — | 批量实时快照（§3.3） |
| **新增** `GET/POST/PUT/DELETE /alerts/*` | — | 预警中心（§4.1） |

---

*本文档为滚动规划，实施时以代码现状为准；每个 Sprint 结束后回顾修订。*
