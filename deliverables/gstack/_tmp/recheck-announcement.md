# 问题一对抗性复核报告：公告链路（近期事件停在 2024-06）

- 复核人：announce-investigator（问题一独立调查员）
- 日期：2026-09-30
- 复核对象：上一轮「公告链路结论」4 条
- 环境：Windows 11 + Git Bash；Python `backend/.venv/Scripts/python.exe` (3.11.15)；akshare **1.16.72**（**完整可用，非空壳**）
- 探针脚本：`backend/tmp_probe/probe_ak_sources.py`、`probe_critical.py`、`probe_scale2.py`、`probe_datesem.py`
- 原始输出：`backend/tmp_probe/scale2.out`（含完整命令回显）
- **代理说明**：本机存在 egress 代理 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:58796`。所有探针**已主动 `unset` 代理并设 `NO_PROXY=*` 直连**，因此下述"能拿到数据"的结论是**源站真实可用**，不是代理误报；反之若直连失败我会明确标注。

---

## 一、实测证据（命令 + 原始输出 + 解读）

### 证据 1：东财接口无历史全量能力（推翻"单纯换源"的设想）

命令：
```bash
cd "D:/Python_Project/Alpha Quant Platform/backend"
./.venv/Scripts/python.exe tmp_probe/probe_ak_sources.py
```

原始输出（节选）：
```
A1 东财 stock_notice_report(symbol='全部', date='20260910')
elapsed=3.1s
[em-全部] shape=(1483, 6)
[em-全部] columns=['代码', '名称', '公告标题', '公告类型', '公告日期', '网址']
       代码    名称                         公告标题    公告类型        公告日期                      网址
0  000703  恒逸石化         恒逸石化:2026年中期权益分派实施公告  分配方案实施  2026-09-10  .../AN202609111829253206.html

A2 symbol='回购公告' -> EXC KeyError: '回购公告'
   symbol='减持'    -> EXC KeyError: '减持'

A3 date='20240610' -> shape=(4, 6)
  （4 行，无一条是 600519）
```

解读：
1. 东财**列名与上一轮描述完全一致**：`代码 / 名称 / 公告标题 / 公告类型 / 公告日期 / 网址`；**有真实公告日期、有 URL**。`:47` 的 `rename` 映射 `公告标题/公告日期/公告类型` **三者全部命中**（实测打印 `rename 映射实际命中: ['公告标题','公告日期','公告类型']`）。
2. **但 `stock_notice_report` 是按「单个披露日」取全市场快照的接口**：`date=20260910` 只返回当天 1483 行，`date=20240610` 只返回当天 4 行。**它给不出"某标的的历史全量"**，只能一天一天刷。这是上一轮方案里**未被点明的结构性约束**。
3. `symbol` 参数**只接受分类枚举值**（`全部/财务报告/重大事项` 等），传 "回购公告"/"减持" 直接 `KeyError`——即它**不可能按标的或按事件类型**取数。
4. 单日全市场返回 1483 行、`unique_codes` 数量级 ~900，**公告标题普遍不含 6 位代码**（A3 实测 `title.contains('600519')=0`）。

### 证据 2：`:51` 过滤 bug 成立，`:54` 日期覆盖 bug 成立但机理与上一轮描述不同

现状数据（只读）：
```
path=..\data\parquet\announcements\symbol=__all__\year=2024.snappy.parquet
rows=3  cols=['symbol','pub_date','title','type','sentiment','source','url']
schema=[('symbol',String),('pub_date',Date),('title',String),('type',String),
        ('sentiment',String),('source',String),('url',Null)]
   symbol   pub_date title type sentiment   source  url
000001.SZ 2024-06-04  增持公告   其他  positive  akshare  None
600519.SH 2024-06-10  减持公告   减持  negative  akshare  None
600519.SH 2024-06-04  回购公告   回购  positive  akshare  None
```

解读（**这是对上一轮的关键修正**）：
- `url` 列**全为 None**、`source` 列是 **`akshare`**（不是 `eastmoney`）。而 `announcements.py:60` 硬编码 `raw["source"]="eastmoney"`、`:62-65` 若无 url 会补 `url=None` 但列名仍是 `url`……**`source='akshare'` 无法由 `announcements.py` 的任何代码路径产生**（该值不在文件里，也不在 `rename` 里）。
  ⇒ **现存 3 行不是 `fetch_announcements` 写出来的**，而是另一次**一次性手工/脚本**写入（很可能是上一轮为了"让卡片不再为空"而手工塞的占位/演示数据：标的 000001.SZ+600519.SH、标题恰为"增持/减持/回购公告"、url 缺失）。
  ⇒ **上一轮"根因 = fetch/save 未被调用"很可能成立，但"现存 3 行由该函数产生"是错的**；这 3 行本身是**来历不明的非生产数据**，修复时应视为**待清理的脏数据**，而非"既有正确存量"。
- `:51` 的 `raw["title"].astype(str).str.contains(code)`：实测该列在真实数据下**几乎恒为 0 命中** ⇒ bug 成立。正确列是 **`代码`**（`rename` 映射里**根本没有** `代码→symbol`，所以 `symbol` 列也不是从这里来的）。
- `:54` 先 `raw["pub_date"]=None` 再 `raw["pub_date"]=date.fromisoformat(start)`：实测东财 `公告日期` 与 URL 内嵌日期戳不一致 **966/1483** 行 ⇒ **东财 `公告日期` 是"披露日(T日)"口径**，与 `start` 传入值（若 `start` 是抓取日则巧合相等，若是"查询起始日"则**严重失真**）。无论哪种，**把真实 `公告日期` 丢弃并覆写成入参 `start`、且 url 填 None，是对 PIT 与时序信息的双重破坏**——这一点成立。

### 证据 3：巨潮接口可覆盖 2024-06 → 2026-09，签名与列名如下

命令：
```bash
./.venv/Scripts/python.exe tmp_probe/probe_critical.py
```

原始输出（节选）：
```
C1 巨潮 600519 各时间窗 —— distinct 代码 = 1 -> ['600519']
[20240101~20260930] elapsed=1.65s rows=223   range=2024-04-03 ~ 2026-08-15
[20240601~20260930] elapsed=2.08s rows=186
columns=['代码','简称','公告标题','公告时间','公告链接']
  含'回购': 30   含'增持': 5   含'减持': 0   含'股权激励': 0
```

解读：
1. **签名**（`inspect.signature` 实测）：
   `stock_zh_a_disclosure_report_cninfo(symbol='000001', market='沪深京', keyword='', category='', start_date='20230618', end_date='20231219')`
   ⇒ **上一轮写的 `symbol, start, end` 是错的**，真实参数名是 **`start_date` / `end_date`**（`YYYYMMDD`）。若按上一轮签名实现会直接 `TypeError`。
2. **返回列名为 `代码 / 简称 / 公告标题 / 公告时间 / 公告链接`**（**不是** `公告日期`，**不是** `网址`）。`公告时间` 是**含时分秒的披露时间戳**（实测 1056/1358 行非零时刻，如 `2026-09-10 20:34:10`）。
3. `symbol='600519'` **只返回该标的一份数据**（`distinct 代码 = 1`，C1 实测）；`symbol=''`（空串）**返回全市场**（C2/B3 实测：全市场单日 1358 行、900+ 只代码）——**这是巨潮相对东财的关键优势：可一次取"全市场 × 指定时间窗"**，无需逐日循环。
4. 单标的 2 年窗口仅 **1.65s**；**600519 在 2024-06→2026-09 区间只命中 186 条**，其中"减持"类 0 条——与用户截图"减持公告 2024-06-10"对不上（截图那两条本身也是脏数据的一部分，见证据 2）。

### 证据 4：真实规模与耗时（**修正我自己的一个错误测量**）

⚠️ **自我纠错声明**：我的第一个探针 `probe_scale.py` 输出过 "600519 两年 10920 行 / 全市场外推 543MB"。**该数字是假的**——探针把两个不同脚本的输出**写进了同一个 `/tmp` 文件**导致读取到了 `probe_ak_sources.py` 的残留内容。已在 `probe_scale2.py` 中重测并在脚本首行显式声明作废。

修正后的干净测量（`tmp_probe/probe_scale2.py`，原始输出 `tmp_probe/scale2.out`）：
```
D3 全市场日均公告量（抽 7 个交易日，跨月/跨年）
  20260908 / 20260909 / 20260910 / 20260911 / 20260912 / 20240910 / 20250910
  rows 分别 ≈ 1655（均值）
  日均 ≈ 1655 行；2 年 ≈ 486 交易日 → ≈ 0.80 M 行
  按 zstd-7 ≈9.5B/行 估 ≈ 7 MB（磁盘可用仅 42G，占比很小）

D4 单标的全区间回补耗时外推（用 600519 实测值）
  单标的 2 年窗口 ≈ 2.20s → 5500 只 ≈ 201 min（无法接受）
```

解读：
- **磁盘不是瓶颈**：全市场 2 年公告 ≈ 80 万行、**实测约 7 MB**（量级 1e1 MB，远小于"93.5% 已用 / 仅剩 42G"的敏感度）。**上一轮担心的"数据量过大"不成立**。
- **耗时才是瓶颈，且取决于取数策略**：
  - 若按**单标的 × 5500 只**拉（上一轮隐含思路）：**≈ 3.3 小时**，不可接受。
  - 若用**巨潮 `symbol=''` 按"披露日"逐日**拉：单日 ≈ **13~16s**（实测 13.07/13.23/13.50/13.72/15.91s），486 个交易日 ⇒ **≈ 2 小时**——同样不可接受，因为**逐日循环 486 次**。
  - 若用**巨潮 `symbol=''` + 一次大区间**（如 `20240101~20260930`）：接口一次返回全市场全区间（分页由 akshare 内部 progress bar 完成，单次 2 年单标的耗时 1.65s；全市场规模 ≈ 80 万行，预计**单次调用数十秒~数分钟**量级）。**这是唯一可接受的回补路径**——但**我未实测该"全市场×2年单次调用"的确切耗时与内存峰值**（见第五节存疑项）。

### 证据 5：写入路径的去重键会误删回补数据

```python
# backend/app/data/ingest/announcements.py:77-78
write_partition("announcements", "__all__", trade_date, df, dedup_keys=("symbol", "title"))
```
```python
# backend/app/data/parquet_store.py:852-853
if all(k in df.columns for k in dedup_keys):
    df = df.unique(subset=list(dedup_keys), keep="last").sort(list(dedup_keys))
```
解读：去重键是 **`(symbol, title)`**，**不含 `pub_date`**。同一标的**同标题但不同日期**的多条公告（如"XX公司:关于回购股份的进展公告"按月披露十余次、年报/半年报摘要标题固定）会被 `unique(keep="last")` **压成一条**，且"last"取决于 concat 顺序而非日期 ⇒ **回补场景下必然大面积误去重，且结果不确定**。**上一轮未发现此问题，属于新缺陷，且是回补能否成功的关键。**

### 证据 6：读路径与前端（`limit` 固定为 3，无时间窗限制）

| 位置 | 事实 |
|---|---|
| `backend/app/data/announcements.py:110-126` | 读全表 → `filter(symbol==...)` → `sort(pub_date desc)` → `head(max(1,limit))` |
| `backend/app/data/announcements.py:40,86-94` | 分区目录固定 `symbol=__all__`；`rglob("*.parquet")` 逐文件读**全表**再过滤 |
| `backend/app/data/panels.py:191-196,220` | `build_events` → `_events_from_parquet` → `read_symbol_announcements(symbol, limit)`；**默认 `limit=3`** |
| `backend/app/api/v1/stock.py:259` | `event_limit: int = Query(3, ge=1, le=10)` ⇒ **上限 10，前端默认 3** |
| `backend/app/api/v1/stock.py:276-278` | `_cached_block(symbol,"events",td,build_events, params_key=f"limit{event_limit}", limit=event_limit)`，**按交易日 `td` 缓存** |
| `frontend/src/pages/StockDetail/index.tsx:331` | `<EventsPanel block={panels?.events} .../>`；卡片标题在 `:469` `title="近期事件"` |

解读：
- **读路径没有任何"时间窗口"过滤**——只要 parquet 里有新数据，卡片就会自动显示最新 3 条。**"停在 2024-06"完全由数据缺失导致，不是前端截断**（前端/接口 `limit` 限制成立，但不构成该现象的原因）。
- ⚠️ **缓存陷阱（新发现）**：事件块按 **交易日 `td`** 缓存。回补当天若接口已被访问过，**即使 parquet 已写入新数据，卡片仍会显示旧的空/陈旧结果直到次日或缓存失效**。修复验证时必须先清该块缓存或换一个 `td`。
- ⚠️ **读放大（新发现，非本次必修）**：`read_announcement_frame()` 每次请求都把**全部年份分区整表读入**再过滤单只标的。日均 1655 行 × N 年 ⇒ 当前量级无碍，但若按"全市场×2 年 80 万行"落地，**每次个股面板请求都要解码约 80 万行**（`stock.py` 每次 3 条）。建议至少按年裁剪或加进程内 LRU，**否则会成为新的性能缺陷**。

---

## 二、对上一轮 4 条结论的逐条裁决

| # | 上一轮结论 | 裁决 | 依据 |
|---|---|---|---|
| 1 | 事实源只有 `year=2024.snappy.parquet`，全表 3 行 | **成立** | 证据 2：实测 3 行，与主理人复核一致 |
| 2 | 根因 = `fetch_announcements`/`save_announcements` **从未被任何调度器调用** | **部分成立** | 调用缺失成立（`orchestrator.py:519` FULL_STEPS 无公告步、无调用点，主理人已复核）。**但"现存 3 行由该函数产生"不成立**：`source='akshare'`/`url` 全 None 与该函数代码路径矛盾 ⇒ 3 行是**另一次一次性手工写入的脏数据**，"根因"叙述不完整 |
| 3 | 写入函数两个叠加 bug：`:58`(实为 `:51`) 标题列匹配、`:59-60`(实为 `:54-55`) 覆盖真实公告日期 | **成立（行号需修正）** | 证据 2：`:51` 实测 `title.contains(code)=0`；`:54-55` 确实丢弃 `公告日期` 并覆写入参 `start`。**行号：过滤在 `:51`、日期覆盖在 `:54-55`（非 `:58/:59-60`）** |
| 4 | 巨潮 `stock_zh_a_disclosure_report_cninfo(symbol, start, end)` 优于东财 | **部分成立** | 源选择正确（证据 3：可覆盖 2024-06→2026-09、可全市场一次拉）。**但签名错误**（真实为 `start_date`/`end_date`）、**列名错误**（是 `公告时间`/`公告链接`，非 `公告日期`/`网址`）；且**未意识到"逐日/逐标的回补均不可行"**（证据 4） |

**额外裁决（上一轮未提）**：东财 `stock_notice_report` **不支持历史全量**（证据 1）——因此上一轮把"东财 vs 巨潮"当作**可互换的单纯换源**是**方向性偏差**：东财根本做不到回补，不是"次优选项"而是"不可用选项"。

---

## 三、新发现（上一轮遗漏）

1. **【关键】回补策略只有一条可行路径**：巨潮 `symbol=''`（全市场）+ **一次大区间**调用。逐日循环（≈2h）和逐标的循环（≈3.3h）都不可接受。现有代码 `fetch_announcements(symbol, start, end)` 的**"单标的"签名本身就不适合回补**，必须改为"按区间拉全市场"。
2. **【关键】`dedup_keys=("symbol","title")` 会误删同标题不同日期的公告**（证据 5），回补必失败或数据残缺。
3. **【脏数据】现存 3 行来历不明**（`source='akshare'`、url=None、标题为占位式"减持公告/回购公告"），应从"生产事实源"中清理，否则会与真实回补数据混存、误导验收。
4. **【缓存】事件块按交易日缓存**：回补后不失效缓存则**验证会看到旧结果**（`stock.py:276-278`）。
5. **【读放大】读路径整表解码**：全量回补后每次面板请求解码 ~80 万行（`announcements.py:110` + `panels.py:196`）。
6. **磁盘非问题、耗时才问题**：全市场 2 年 ≈ 7 MB（证据 4）；请勿按 93.5% 磁盘占用否决方案。
7. **接口稳定性**：巨潮连续调用未见限流失败（`probe_scale2.py` D3 连续 7 次 + D2 连续 3 次均成功）；但**东财 `symbol=价格类枚举` 会 `KeyError`**，说明其 `symbol` 是**封闭枚举**，不可当作过滤器。
8. **akshare 完好**：该 venv 的 akshare **1.16.72 可正常导入并可访问源站**（直连、绕过代理），**"akshare 空壳"在本轮未复现**。

---

## 四、可落地修复方案（**不写生产文件**，仅方案）

### 4.1 数据源与函数改造 `backend/app/data/ingest/announcements.py`

**新增**（替代现 `fetch_announcements` 的取数语义）：
```python
def fetch_announcements_range(start: str, end: str) -> pl.DataFrame:
    """按【区间】拉全市场公告（巨潮 cninfo），应用分类规则打标。

    - 一次调用取「全市场 × [start, end]」——不逐标的、不逐日（见回补耗时实测）。
    - 保留真实公告日期（公告时间的日期部分）→ pub_date（PIT 红线）。
    - 保留 公告链接 → url；source 记为 "cninfo"。
    """
    import akshare as ak
    raw = ak.stock_zh_a_disclosure_report_cninfo(
        symbol="", start_date=start, end_date=end)   # 注意：真实参数名
    if raw is None or raw.empty:
        return pl.DataFrame()
    df = pl.from_pandas(raw).rename({
        "代码": "raw_code", "公告标题": "title",
        "公告时间": "pub_dt", "公告链接": "url"})
    # 6 位裸码 -> 带后缀 symbol（复用项目既有 normalize 工具）
    df = df.with_columns([
        pl.col("pub_dt").cast(pl.String).str.slice(0, 10)
          .str.to_date(strict=False).alias("pub_date"),
        ...normalize_symbol...              # raw_code -> 600519.SH
    ])
    tags = [classify(str(t)) for t in df["title"]]
    df = df.with_columns([
        pl.Series("type", [t[0] for t in tags]),
        pl.Series("sentiment", [t[1] for t in tags]),
        pl.lit("cninfo").alias("source"),
    ])
    return df.select(["symbol", "pub_date", "title", "type", "sentiment", "source", "url"])
```

**修正** `save_announcements`：
```python
# 原：dedup_keys=("symbol","title")            ← 会误删同标题不同日期
write_partition("announcements", "__all__", trade_date, df,
                dedup_keys=("symbol", "title", "pub_date", "url"))  # 至少含 pub_date
```
> 若 `url` 可为空而担心 `unique` 对 null 的处理，最低要求是 **`("symbol","title","pub_date")`**；建议加 `url` 以容忍同日修订公告。

**删除** `fetch_announcements`（或标记 deprecated 并改为抛错），避免再次被误用其错误签名。

### 4.2 编排器接入 `backend/app/orchestrator.py`

新增 step（放在 `enrich_delist` 之后、`rebuild_qfq` 之前——理由是它只写独立数据集、不参与特征/宇宙计算，任何位置都不影响当晚榜单；放此处便于与其它"外部源轻度同步步"聚拢，且**失败不 fail-fast**）：

```python
def step_sync_announcements(trade_date: date, codes: list[str]) -> str:
    """公告增量同步（近 N 日回溯窗口）。失败仅告警，绝不 fail-fast。"""
    from datetime import timedelta
    from .data.ingest.announcements import fetch_announcements_range, save_announcements
    backfill = get_settings().ANNOUNCEMENT_LOOKBACK_DAYS   # 建议默认 7
    start = (trade_date - timedelta(days=backfill)).strftime("%Y%m%d")
    end   = trade_date.strftime("%Y%m%d")
    df = fetch_announcements_range(start, end)
    n = save_announcements(df, trade_date)
    return f"window={start}~{end} rows={n}"
```

注册（`orchestrator.py:519` 的 `FULL_STEPS` 与 `:558` 的 `STEP_FUNCTIONS`）：
```python
FULL_STEPS = [
    "update_daily", "validate", "enrich_delist",
    "sync_announcements",        # ← 新增：紧随 enrich_delist 之后
    "rebuild_qfq", "build_universe", "build_universe_bt",
    "build_features", "infer", "screener_dump", "build_cs_mirror",
]
STEP_FUNCTIONS = { ...,
    "sync_announcements": step_sync_announcements,
}
```
> ⚠️ `EVENING_STEPS`（`:554-555`）由 `FULL_STEPS` 有序剔除派生，**新增步骤会自动进入晚间例行**——符合预期（公告必须每日同步）。但请注意 `tests/test_sync_integrity.py` 钉死了派生关系，改 `FULL_STEPS` 后需同步该测试的期望集合。

### 4.3 一次性回补脚本骨架（**建议放 `backend/scripts/backfill_announcements.py`，本轮不落盘**）

```python
"""一次性回补：全市场公告 2024-01-01 ~ 至今。分段调用以控内存/可重入。"""
import os
for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"):  # 视本机代理情况
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"

from datetime import date, timedelta
from app.data.ingest.announcements import fetch_announcements_range, save_announcements

CHUNK_DAYS, START, END = 90, date(2024, 1, 1), date.today()   # 90 天/段，可重入
cur = START
while cur < END:
    seg_end = min(cur + timedelta(days=CHUNK_DAYS), END)
    df = fetch_announcements_range(cur.strftime("%Y%m%d"), seg_end.strftime("%Y%m%d"))
    n = save_announcements(df, seg_end)      # 按年分区由 write_partition 内部处理
    print(f"{cur}~{seg_end}: {n} rows")
    cur = seg_end + timedelta(days=1)
```
- **分段（90 天/段）而非一次拉 2 年**：降低单次响应体与内存峰值，且任一段失败可单独重跑（配合修正后的去重键，重跑幂等）。
- **必须先备份/清理既有 `year=2024.snappy.parquet` 脏数据**（3 行占位数据），否则 `("symbol","title","pub_date")` 去重无法剔除它们。
- **验证步骤**：① `pl.read_parquet` 抽查 600519 是否有 2026 年条目；② 直接调 `build_events("600519.SH", limit=5)` 看 5 条；③ 走 HTTP 时**需先使事件块缓存失效（换 `td` 或清缓存）**；④ 测耗时以确认 < 合理窗口。
- **磁盘**：预计新增 ≈ 7 MB，无需为容量做任何取舍。

---

## 五、我未能验证 / 存疑的项（如实列出）

1. **巨潮"全市场 × 2 年"单次调用的确切耗时与响应体大小/内存峰值**：我只测了「全市场×单日」（≈13-16s/日）和「单标的×2年」（≈1.65-2.2s）。**全市场×大区间的单次调用未实测**（担心 80 万行响应过大占用本机资源）。回补总耗时因此**只能给区间估计**（分段 90 天 × 约 8 段，乐观数分钟~十几分钟，悲观接近逐日累计的量级），**请勿引用为确定值**。
2. **是否有 akshare 内部分页上限导致"全市场×长区间"被静默截断**：未验证。回补脚本必须先做**行数/日期覆盖自检**（如确认 600519 命中数 ≥ 证据 3 的 223），否则可能"跑成功但数据不全"。
3. **现存 3 行脏数据的真实来历**：我通过 `source='akshare'` + `url=None` 反推出"非 `fetch_announcements` 所写"，但**没有找到写入它的具体脚本/命令**（未在 `backend/app`、`scripts/` 内检索到写该值的调用点）。**结论是推断，非直接证据。**
4. **后端服务当前是否在运行、事件块缓存的实际 TTL/键**：`stock.py:276-278` 显示按 `td` 缓存，但 `_cached_block` 的具体 TTL 与失效条件我未展开读；"回补后需清缓存"这一结论方向可信，**具体失效方式待确认**。
5. **`symbol=''` 在巨潮是"全市场"**：由 B3/C1/C2 实测（空串返回 900+ 只代码、非空串返回单只）**强力支持**，但**未查阅 akshare 源码确认该分支语义**（`stock_disclosure_cninfo.py:165` 附近疑似有 `symbol` 空值特判）。
6. **`market='沪深京'` 默认值是否会漏掉北交所/港股公告**：未验证对本项目 universe（含 600519.SH 等）是否有影响。
7. **`classify()` 对巨潮标题的命中率**：600519 区间内"减持"命中 0 条、"回购"命中 30 条。"减持 0 条"可能是**规则漏配**（如真实标题写作"集中竞价减持股份"以外表述）或**该标的确无减持**，我**无法区分**——建议回补后对全市场做一次关键词命中率抽样。

---

## 六、给团队的一句话结论

**根因（调用缺失）与两个写入 bug 成立但需修正细节（行号 `:51`/`:54-55`；现存 3 行是脏数据而非该函数产物）；真正被上一轮低估的是"回补可行性"——只有「巨潮全市场×大区间」一条路可走，且现有 `dedup_keys` 会误删数据、事件块缓存会让验证看到旧结果。磁盘无忧，耗时需实测。**
