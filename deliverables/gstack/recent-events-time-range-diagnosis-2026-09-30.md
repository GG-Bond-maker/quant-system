# 「近期事件」停滞在 2024-06 而 K 线到 2026-09 —— 根因诊断报告

- 日期：2026-09-30
- 标的：贵州茅台 600519.SH
- 诊断人：gstack-investigator（排障手）
- 环境：后端 8000 端口在线，Redis 在线（`/health/ready` 全 ok），SQLite `data/sqlite/aqp.db`

---

## 0. 一句话根因结论

**这是数据管道缺陷，不是查询/过滤/缓存缺陷。**

「近期事件」的唯一事实源是 `DATA_ROOT/announcements/` 下的公告 parquet 分区，
而该数据集**只有 `year=2024` 一个文件、全表仅 3 行、`pub_date` 最大值为 `2024-06-10`**；
写入它的同步函数 `save_announcements()` **从未被任何调度器/流水线/脚本调用过**
（全仓仅测试引用），因此公告数据从 2024-06 起就再没进过库。
后端接口忠实地把这份「最新的旧数据」原样返回，与 K 线（daily_bar 已到 2026-09-17）完全解耦。

> 分水岭判据（实测）：**表里最新就是 2024 ⇒ 数据源/同步问题**。本案例落在此路径。

---

## 1. 实测证据（全部可复现）

### 证据 A — 公告 parquet 文件系统事实
```
$ ls data/parquet/announcements/symbol=__all__/
year=2024.snappy.parquet        # 只有一个文件，mtime 2026-08-29 17:17
```
- 数据集目录 `data/parquet/announcements/` 下**无 year=2025 / year=2026 文件**。

### 证据 B — 公告 parquet 内容事实（polars 实读）
```
rows= 3   cols= ['symbol','pub_date','title','type','sentiment','source','url']
pub_date min/max: 2024-06-04  2024-06-10
unique pub_date: [datetime.date(2024,6,4), datetime.date(2024,6,10)]
600519.SH 命中 2 行：
  {'symbol':'600519.SH','pub_date':2024-06-10,'title':'减持公告','type':'减持','source':'akshare'}
  {'symbol':'600519.SH','pub_date':2024-06-04,'title':'回购公告','type':'回购','source':'akshare'}
```
→ 与用户截图「减持公告 2024-06-10 / 回购公告 2024-06-04」**逐字吻合**。全市场公告总量只有 3 行（另一行为 000001.SZ 增持公告）。

### 证据 C — K 线（daily_bar）同源对照，确认不是路径/时区问题
```
data/parquet/daily_bar/symbol=600519.SH/
  year=2022 / 2023 / 2024 / 2025 / 2026 .snappy.parquet
  K线 date min/max: 2022-01-04  2026-09-17
```
→ 同一个 `DATA_ROOT`，K 线有 5 个年分区直到 2026-09-17，公告只有 1 个 2024 年分区。
**同一根目录、同一读取框架，一个最新一个陈旧 ⇒ 排除「根路径写错 / 统一时区转换错」。**

### 证据 D — 后端接口真实返回（临时 viewer 账号实测，用完已删）
```
GET http://127.0.0.1:8000/api/v1/stock/600519.SH/panels?event_limit=5
Authorization: Bearer <token>

data.events = {
  "status":"ok",
  "items":[
    {"title":"减持公告","date":"2024-06-10","source":"akshare","event_type":"减持","sentiment":"negative"},
    {"title":"回购公告","date":"2024-06-04","source":"akshare","event_type":"回购","sentiment":"positive"}
  ],
  "source":"akshare",
  "from_cache": false          <-- 关键：本次为实时构建，非缓存命中
}
```
→ 接口返回值 = parquet 内容，`from_cache=false` 说明**这次不是缓存作祟**，是事实源本身只有 2024 数据。

### 证据 E — SQLite `news_announcement` 备份口径为空（证明无第二数据源兜底）
```
news_announcement rows = 0
600519.SH rows = 0
data_jobs 历史 job_type: [('daily_pipeline',10,'2026-09-29'),
                          ('sync_incremental',9,'2026-09-29')]
```
→ 表存在但**从无写入**；`data_jobs` 里也**从来没有任何公告类任务**执行过。

### 证据 F — 代码静态事实：写入函数无调用方
- `save_announcements` / `fetch_announcements` 全仓（不含 .venv）**唯一出现在测试**：
  `backend/tests/test_p1_data.py:16,73,77`。
- 调度链 `main.py` 启动的任务只有：`evening_routine_scheduler` / `startup_catchup` /
  `alert_scheduler` / `auto_sync_scheduler` / `_overview_warmer` —— **无公告任务**。
- 晚间例行 `jobs/evening_routine.py:_run_routine()` 只跑 4 件事：pipeline / monitor / report / ETF 归档。
- 流水线步骤常量 `orchestrator.FULL_STEPS` 与 `EVENING_STEPS`（orchestrator.py:519-555）
  与 `STEP_FUNCTIONS`（orchestrator.py:558-569）**共 10 步，无任何公告步骤**。
- 日志 `backend/logs/app.log` 全量搜索 `save_announcements|fetch_announcements|公告同步` → **0 命中**（从未运行）。

### 证据 G — 缓存键带数据日（用于排除「缓存未失效」）
`cache/keys.py:84-87`：
```python
def k_stock_block(symbol, block, trade_date, params_key="default"):
    return f"{NS}:stock:block:{symbol}:{block}:{trade_date}:{params_key}"
```
key 含 `trade_date` ⇒ 每个交易日自动换 key、跨天必然失效；`TTL["events"]=3600`（panels.py:52）。
配合证据 D 的 `from_cache:false`，**缓存不是成因**。

---

## 2. 用户 5 个可能原因逐一裁决

| # | 假设 | 裁决 | 依据 |
|---|------|------|------|
| 1 | **事件数据源更新滞后 / 同步任务未正确调度** | ✅ **成立（即根因）** | 写入函数 `save_announcements` 全仓仅测试引用（证据 F）；无调度注册、无 data_jobs 记录、日志 0 命中；parquet 仅 year=2024（证据 A/B）。**不是"任务坏了"，而是"任务从未存在"** |
| 2 | 查询时时间窗口写死 / 过短 | ❌ 已排除 | `read_symbol_announcements`（announcements.py:99-138）与 `build_events`（panels.py:220-278）**无任何写死年份 / start_date / end_date / 窗口天数**，排序仅 `pub_date desc + head(limit)`；接口 `event_limit=5` 仍只返回 2 条 2024 记录（证据 D）——若是窗口过短，`limit` 放大会出更多旧记录而非导致"最新=2024" |
| 3 | 缓存未失效（Redis/内存/SWR/HTTP） | ❌ 已排除 | cache key 含 `trade_date`（keys.py:84-87），跨天换 key；实测 `from_cache:false`（证据 D）；且 2024 值本身就是事实源内容，清缓存也不会变 2026。日志中 events 的 `超时>20s` 告警最后出现在 **2026-09-14**（旧远端路径），E-01 改本地读后消失 |
| 4 | 过滤条件误过滤新数据（成交量/活跃度/状态） | ❌ 已排除 | 事件链路 SQL/ORM 只有 `WHERE symbol=?`（panels.py:210 SQLite 分支）与 `pl.col("symbol")==symbol`（announcements.py:116），**无成交量/活跃度/status 类过滤**；退一步说，2024 之后的记录在 parquet 里根本不存在（证据 B），无从被过滤 |
| 5 | 分区/索引/时区转换错误（把 2026 当越界丢掉） | ❌ 已排除 | `pub_date` 存为 `pl.Date`（polars 原生 date），`_normalize_pub_date` 只做 `cast(Date, strict=False)`，**无 tz_convert**；同一 `DATA_ROOT` 下 daily_bar 的 2026 分区读得好好的（证据 C）⇒ 框架/时区/分区逻辑本身无问题，独公告分区缺 2025/2026 文件 |

**结论**：仅假设 1 成立；2/3/4/5 均被实测排除。

---

## 3. 定位到的具体文件:行号

### 3.1 链路全景（前端 → 后端 → 数据）
| 层 | 文件:行号 | 作用 |
|----|-----------|------|
| 前端组件 | `frontend/src/pages/StockDetail/index.tsx:461-469`（`EventsPanel`，标题「近期事件」） | 渲染事件列表 |
| 前端 API | `frontend/src/api/stock.ts:34-39`（`panels(symbol)` → `/api/v1/stock/{symbol}/panels`） | 请求聚合面板 |
| 后端端点 | `backend/app/api/v1/stock.py:256`（`stock_panels`）；jobs 注册 `stock.py:276-278`；缓存封装 `stock.py:220-253`（`_cached_block`） | 分块聚合、Redis 优先生成 |
| 后端构建 | `backend/app/data/panels.py:220-278`（`build_events`）；`panels.py:191-197`（`_events_from_parquet`，读 parquet） | 事件块构建，读本地 parquet |
| 数据读取口径 | `backend/app/data/announcements.py:99-138`（`read_symbol_announcements`）；`announcements.py:43-78`（`read_announcement_frame`） | 唯一读口径，按 symbol 过滤 + pub_date desc |
| **写入（缺失的环）** | `backend/app/data/ingest/announcements.py:38-68`（`fetch_announcements`）、`:71-79`（`save_announcements`） | **唯一写入函数，无任何调用方** |
| 缓存键 | `backend/app/cache/keys.py:84-87` | 含 trade_date，可排除缓存成因 |

### 3.2 关键「断点」
1. **`backend/app/data/ingest/announcements.py`** —— 文件存在、函数可用，但**没有任何地方 import 调用它**（对照组：`panels.py:30` 只 import 了 `classify`，没 import 抓取/写盘函数）。
2. **`backend/app/orchestrator.py:558-569`** —— `STEP_FUNCTIONS` 缺一个 `update_announcements` 步骤。
3. **`backend/app/jobs/evening_routine.py:119-191`** —— 晚间例行 4 个环节无公告抓取。
4. **`backend/app/services/sync_service.py`** —— autoSync（15:45）覆盖行情增量，不含公告。

### 3.3 附带发现（次要，未单独修复）
- `ingest/announcements.py:54-55` 把 `pub_date` **强制写成拉取日 `start`**（注释自认「东财接口以拉取日期为公告日口径」），且 `fetch_announcements(symbol,start,end)` 的 `end` 参数**未被使用**——即使接上调度，历史回补也得逐日循环，且日期口径是"抓取日"而非"真实公告日"。修复调度时需一并处理。
- `ingest/announcements.py:67` 去重为 `unique(subset=["symbol","title"])`，长期按日增量会因"标题相同"误合并不同日期的公告，需评估。

---

## 4. 修复方案

> 分两层：**A. 止血（先让数据源有货）** + **B. 治本（把公告纳入调度，防止再次停更）**。
> 根因是"写入方缺失"，所以核心修复 = 给 `save_announcements` 接上调度。

### A. 止血：一次性回补（先把 2025/2026 数据灌进去）
在 `backend` 下执行一次性回补脚本（**务实做法**：东财公告接口按日期分页，需逐交易日循环；
建议封一个小工具函数，并对 2024→今逐日调用）。伪代码（需在实现时按 `fetch_announcements`
现有签名与限速 `_throttle` 适配）：

```python
# backend/scripts/backfill_announcements.py（新建，一次性）
from datetime import date, timedelta
from app.data.ingest.announcements import fetch_announcements, save_announcements

d = date(2024, 6, 11)          # 从现有最大 pub_date 次日开始
end = date.today()
while d <= end:
    df = fetch_announcements("__ALL__", d.isoformat(), d.isoformat())  # 全市场
    if not df.is_empty():
        save_announcements(df, d)   # 按 d.year 落 year=YYYY 分区
    d += timedelta(days=1)
```
⚠️ 注意：`fetch_announcements` 现在按 `symbol.split(".")[0]` 过滤，全市场回补需把 symbol
参数与过滤逻辑改成"不过滤/遍历所有 symbol"，**这是必须的代码改动**（当前实现无法一次拿全市场）。

### B. 治本：把公告同步纳入既有调度（防再停更）

**改动 1 —— 新增流水线步骤** `backend/app/orchestrator.py`：
```python
# 1) 新增 step 函数
def step_update_announcements(trade_date: date, codes: list[str]) -> str:
    from .data.ingest.announcements import fetch_announcements, save_announcements
    df = fetch_announcements("__ALL__", trade_date.isoformat(), trade_date.isoformat())
    n = save_announcements(df, trade_date) if not df.is_empty() else 0
    return f"announcements +{n}"

# 2) 注册进 STEP_FUNCTIONS（orchestrator.py:558-569）
STEP_FUNCTIONS = { ..., "update_announcements": step_update_announcements }

# 3) 并入默认步骤集（orchestrator.py:519-539 FULL_STEPS 末尾）
FULL_STEPS = [ ..., "build_cs_mirror", "update_announcements" ]
```
> 因 `EVENING_STEPS = [s for s in FULL_STEPS if s not in ("update_daily",)]`（orchestrator.py:555），
> 新增步会**自动**同时进入晚间例行，无需再改 evening_routine.py。
> ⚠️ 但晚间例行 17:30 跑在盘后、且公告接口按"抓取日"口径 → 当日公告 T 日盘后落盘合理。

**改动 2 —— autoSync 也覆盖公告（可选，双保险）** `backend/app/services/sync_service.py`：
在 15:45 增量同步 job 里追加一次 `update_announcements(today, [])`。

**改动 3 —— 修正公告日口径（重要，改变数据语义）** `ingest/announcements.py:54-55`：
当前 `raw["pub_date"] = date.fromisoformat(start)` 把所有公告日期写成"抓取日"。
应改为从东财返回的 `公告日期` 字段取值（rename 里已有 `"公告日期":"pub_date"` 映射，
但第 47-48 行 rename 后又在 54 行把它覆盖成 None 再重写）。**此改动会让历史 pub_date 变化，属口径变更，需评审。**

### 风险提示
- **改动 3 改变口径**：会使已落盘的 2024 数据 pub_date 重算，若有下游按 pub_date 做 PIT
  回测（`load_announcements_asof`，announcements.py:82-94），历史回测结果会变。必须先确认。
- **限速**：akshare 全局限速默认 1.2s/次，逐日回补 2 年 ≈ 500+ 次调用 ≈ 10min+，需离线跑。
- **去重语义**：`unique(subset=["symbol","title"])`（announcements.py:67）跨日会误合并同名公告，
  治本时应改为 `["symbol","title","pub_date"]`。

---

## 5. 验证方式（可执行）

### V1 — SQL/parquet 层：断言事实源已长到近期
```bash
cd "D:/Python_Project/Alpha Quant Platform/backend" && ./.venv/Scripts/python.exe -c "
import polars as pl, glob
fs = glob.glob(r'D:\Python_Project\Alpha Quant Platform\data\parquet\announcements\symbol=__all__\*.parquet')
df = pl.concat([pl.read_parquet(f) for f in fs])
mx = df['pub_date'].max()
print('announcements max pub_date =', mx, '| rows =', df.height)
import datetime; assert (datetime.date.today() - mx).days <= 5, 'FAIL: 公告仍滞后 >5 天'
print('V1 PASS')
"
```

### V2 — 接口层：时间窗口断言（返回的最新事件不得早于 N 天前）
```bash
# 期望：latest_event_date 距今天数 <= 阈值（交易日历容差建议 <=7 天）
curl -s "http://127.0.0.1:8000/api/v1/stock/600519.SH/panels?event_limit=5" \
  -H "Authorization: Bearer $TOKEN" | ./.venv/Scripts/python.exe -c "
import sys, json, datetime
d = json.load(sys.stdin)['data']['events']
dates = [i['date'] for i in d.get('items', []) if i.get('date')]
mx = max(dates) if dates else None
print('返回最新事件日 =', mx)
assert mx and (datetime.date.today() - datetime.date.fromisoformat(mx)).days <= 7, 'FAIL: 近期事件滞后'
print('V2 PASS')
"
```

### V3 — 缓存 key 检查（确认不是缓存把旧值钉住）
```bash
# 直接看 key 是否含当日 trade_date；跨日 key 不同即证明会失效
./.venv/Scripts/python.exe -c "
from app.cache.keys import k_stock_block
print(k_stock_block('600519.SH','events','20260930','limit5'))
print(k_stock_block('600519.SH','events','20261008','limit5'))   # 换日后 key 必须不同
"
# Redis 侧（若可用）：
#   redis-cli KEYS "*stock:block:600519.SH:events:*"
#   redis-cli TTL  "<上述 key>"
```

### V4 — 调度是否真的挂上（根治的验收）
```bash
# a) 静态：步骤集必须含 update_announcements
./.venv/Scripts/python.exe -c "from app.orchestrator import FULL_STEPS, EVENING_STEPS; print(FULL_STEPS); assert 'update_announcements' in FULL_STEPS and 'update_announcements' in EVENING_STEPS; print('V4a PASS')"
# b) 动态：运行后 data_jobs 应出现公告任务记录
./.venv/Scripts/python.exe -c "
import sqlite3; from app.core.config import get_settings
c=sqlite3.connect(get_settings().SQLITE_PATH)
print(c.execute(\"SELECT job_type,status,trade_date FROM data_jobs WHERE job_type LIKE '%announce%' ORDER BY trade_date DESC LIMIT 5\").fetchall())
"
```

### V5 — 开启查询日志判读（可选）
- polars/SQLite 分支无内置 SQL 日志；如需，可在
  `panels.py:_events_from_sqlite`（:200-217）临时 `logger.info(sql)` 观察 where 条件；
  parquet 分支可在 `announcements.py:116` 后打印 `df.height` 与 `df['pub_date'].max()`。

---

## 6. 未确认 / 待办（如实标注）

1. **东财公告接口当前可用性未实测**：`fetch_announcements` 依赖 `ak.stock_notice_report`，
   本次**未联网实跑**，无法确认该接口今天是否仍返回数据、字段是否漂移。修复前应先单点验证。
2. **为何只有 3 行 2024 数据**：文件 mtime 为 2026-08-29，疑为早期手工/测试种子数据，
   但这 3 行的确切来源**未确认**（可能是某次手工脚本或迁移产物）。
3. **前端事件条数**：前端 `EVENT_SLOTS` 常量值未逐行核对（不影响根因）。
4. **修复代码未实施**：本报告只给方案与文件:行号，**未改动任何源码**（遵循 Scope Lock）。
   所有修复项均标注了风险（尤其口径变更项 3）。
5. 证据 C 提到的 `data/parquet/daily_bar/evil` 目录来历未查（与本次问题无关）。

---

## 附：一句话总结给团队

> 「近期事件」停在 2024-06，**不是接口/缓存/过滤的锅，是公告 parquet 唯一事实源自 2024-06 后就没被写入过**——
> 写入函数 `save_announcements` 从未挂进任何调度（全仓仅测试引用）。
> 修复 = 把公告抓取接进 `orchestrator.FULL_STEPS`（自动进晚间例行）+ 一次性回补 2024→今，
> 并顺带修正"公告日=抓取日"的口径缺陷（此项会改历史语义，需评审）。

---

# 补充验证（2026-09-30 追加，应 team-lead 要求）

> 本节全部为**联网实测**（akshare 1.16.72，本机直连，无代理），非代码推断。
> 环境说明：本环境对东财 `push2*` 服务组有网络层阻断，但公告子域 `np-anotice-stock.eastmoney.com`
> 与巨潮 `cninfo.com.cn` **实测均可达**。

## S1. 数据源当前可用性（实测）

### S1.1 东方财富 `stock_notice_report` —— ✅ 可用
```
ak.stock_notice_report(symbol="全部", date="20260929")
→ OK rows=1878  elapsed=5.0s
  columns = ['代码','名称','公告标题','公告类型','公告日期','网址']
  样例: 300456 赛微电子 "...法律意见书" 法律意见书 2026-09-29
        https://data.eastmoney.com/notices/detail/300456/AN202609291830005442.html
```
- 多日实测单日均值 **~4.26s**（样本5日：3.17~5.28s）：
  - 20260922 rows=1717 / 3.60s；20260923 rows=1656 / 4.16s；20260924 rows=1993 / 5.07s；
    20260925 rows=805 / 3.17s；20260929 rows=1878 / 5.28s
- **返回真实 `公告日期` 与 `网址`（外链）** —— 与 `ingest/announcements.py:54-55` "以拉取日为公告日、url=None"
  的假设矛盾；即当前实现是在**丢弃**接口已提供的真实字段。

### S1.2 ⚠️ 东财接口存在**日期相关的结构不稳定**
```
ak.stock_notice_report(symbol="全部", date="20260926")
→ ERR KeyError: '代码'   （异常在 akshare 内部解析阶段抛出，非本项目代码）
```
- 20260926 当天该接口返回的 payload 不含 `代码` 字段（疑为非交易日/半日市/接口当日异常口径），
  akshare 1.16.72 内部硬取 `代码` 直接 `KeyError`。
- **含义**：逐日回补时**必须对单日异常做 try/except 跳过**，否则一天失败会中断整轮回补。

### S1.3 巨潮 `stock_zh_a_disclosure_report_cninfo` —— ✅ 可用（且更适合本场景）
```
ak.stock_zh_a_disclosure_report_cninfo(symbol="600519", start_date="20260601", end_date="20260930")
→ OK rows=16  elapsed=4.2s
  columns = ['代码','简称','公告标题','公告时间','公告链接']
  600519 最新公告: 贵州茅台2026年半年度报告 2026-08-15
```
- **单标的跨长区间一次返回全量**：`600519, 20240610~20260930` → **rows=186 / 2.04s**，
  时间跨度 2024-06-12 ~ 2026-08-15。
- 返回**真实公告时间** + **可点击外链**，无需再按标题猜代码。
- `realtime.py:562 fetch_cninfo_announcements` 已有可用实现（但当前只被"已下线的远端兜底"引用）。

## S2. 关键疑点：`:44/:51` 的过滤逻辑 —— ❌ **实测证明是坏的**

`ingest/announcements.py:44` 用 `date=start` 拉"当日全市场公告"，`:51` 用
`raw[raw["title"].astype(str).str.contains(code)]` 过滤（`code` = 裸 6 位码，如 `600519`）。

**实测复刻（同一份 date=20260929 全市场数据）**：
| 过滤方式 | 命中行数 | 说明 |
|----------|---------|------|
| `title.contains("600519")`（**现实现**） | **0** | 标题是公司**简称**（"长鑫科技:…"），不含数字代码 |
| `代码`列 == `"600519"`（正确方式） | 0（当日茅台无公告，正常） | —— |
| `代码`列 == `"688825"`（当日有公告的公司） | **27** | 按代码列过滤**有效** |
| `title.contains("688825")`（现实现） | **0** | 同样 0 命中 |

**决定性证据**：
```
当日公告最多的公司: 688825→27条, 002387→17, 601123→17, 920493→17, 688559→17
代码=688825: 按 代码列 过滤命中=27 ; 按 title.contains 命中=0
标题中含任意 6 位数字的行数 = 11 / 1878
```
- **只有 11/1878（0.6%）的标题含 6 位数字**，且都非"公司代码"语义（多为年份/编号）。
- 结论：`str.contains(code)` 对**几乎全部标的**都返回空。**即使把 `save_announcements` 接进调度，
  用现实现也拿不到任何数据** —— 修复方案必须**同时改掉这个过滤**，否则接调度=空跑。

**正确修法**：改用 `代码` 列过滤（`raw[raw["代码"].astype(str).str.zfill(6) == code]`），
并在 rename 里保留 `代码`（当前 rename 只映射了标题/日期/类型，**丢掉了 `代码` 列**）。

## S3. 回补可行性量级估算（实测外推）

### 路径 A：东财逐日全市场（按 `公告日期` 分页）
- 单日全市场 **~4.26s**（实测均值）。
- 2024-06-11 → 2026-09-30 ≈ **约 480 个自然日 / 约 335 个交易日**。
- 量级：335 × 4.26s ≈ **~24 分钟**（纯网络耗时，未计 akshare 全局限速 1.2s/次与异常重试）。
- 风险：S1.2 的结构不稳定日期需跳过；且每日本实现只保留"标题含 code"→ 见 S2，**此路径基于现实现等于无效**。

### 路径 B：巨潮按标的拉全区间（推荐）
```
5 标的实测: 600519(186行/1.67s) 000001(178/1.26s) 000002(428/2.99s) 600036(223/1.34s) 601318(254/1.65s)
→ 5 标的合计 8.91s，平均 1.78s/标的
```
- 全市场约 **2500 标的 × 1.78s ≈ 74 分钟**（单线程，无并发）。
- 优势：一次调用即得该标的 2024→今**全部**公告（不用逐日循环），且带真实公告日与外链。
- **推荐修复走巨潮路径**（`realtime.fetch_cninfo_announcements` 已有实现，改造量为"落盘"）。

## S4. 对修复方案的修正（重要）

原方案（报告 §4）"把 `step_update_announcements` 接进流水线" **不足以修复**，必须叠加：

1. **改过滤**：`ingest/announcements.py:47-51` —— 保留并用 `代码` 列过滤（见 S2）。**这是硬前提**。
2. **改数据源**：建议整体切到巨潮 `stock_zh_a_disclosure_report_cninfo`（按标的+区间，带外链+真实日期），
   而非东财逐日（逐日慢、结构不稳、且不提供外链）。
3. **改日期口径**：`ingest/announcements.py:54-55` 不再把 `pub_date` 写成抓取日，
   改用接口真实 `公告时间`（东财为 `公告日期`）。
4. **回补脚本**按 S3 路径 B 实现（~74min），对单标的异常 try/except 跳过。

### 验证方式（补充，可执行）
```bash
# S-V1 证伪原过滤逻辑（应为 0，证明现实现坏）
cd "D:/Python_Project/Alpha Quant Platform/backend" && ./.venv/Scripts/python.exe -c "
import akshare as ak
df = ak.stock_notice_report(symbol='全部', date='20260929')
print('现实现 title.contains 命中 =', len(df[df['公告标题'].astype(str).str.contains('688825', na=False)]))
print('正确 代码列 命中 =', len(df[df['代码'].astype(str).str.zfill(6)=='688825']))
"
# 期望输出: 0 与 27

# S-V2 巨潮可达性 + 单标的全区间
./.venv/Scripts/python.exe -c "
import akshare as ak
df = ak.stock_zh_a_disclosure_report_cninfo(symbol='600519', start_date='20240610', end_date='20260930')
print('cninfo 600519 rows=', len(df), 'max=', df['公告时间'].max())
"
# 期望: rows>0, max 接近 2026-08
```

## S5. 补充验证的「未确认 / 限制」

1. **本次 Bash/PowerShell stdout 一度失效**：首轮联网探针（东财）疑似挂起后，工具 stdout 采集中断；
   改以"Python 写文件 + Read 工具读回"方式完成全部实测，结果可信，但**首轮挂起的确切原因未确认**。
2. **东财 20260926 的 KeyError 根因未深挖**：只确认异常在 akshare 内部、由缺 `代码` 字段触发；
   未确认该日是休市/半日市还是接口口径异常。
3. **巨潮全市场 2500 标的估算基于 5 标的样本**，真实总量与失败率未全跑（按 team-lead 要求不真跑全量）。
4. **未改动任何源码**，本节为诊断/可行性结论。
