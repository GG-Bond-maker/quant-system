# `backfill_announcements_em.py` 只读复核报告

复核对象：`backend/scripts/backfill_announcements_em.py`（team-lead 新写的东财逐日版）
复核方式：静态阅读 + 依赖源码核对（akshare `stock_notice_report`）。**未改任何文件、未执行删除。**

---

## 结论速览

| # | 复核点 | 结论 |
|---|--------|------|
| 1 | `_fetch_day` 的 PIT 正确性 | **正确**（有 1 个语义说明需修正，非缺陷） |
| 2 | `_build_frame` 入参列匹配 | **正确** |
| 3 | `save_announcements(df, d)` 年份分区归属 | **实践中正确，但缺一层不变量保护**（低风险） |
| 4 | 4 元组去重键跨日合并 | **正确**（不会误压同标题不同日公告） |

---

## 逐点复核

### 1. PIT 正确性 —— 正确 `_fetch_day` (`:126-132`)

```python
df = df.with_columns(pl.col("pub_date").cast(pl.String, strict=False).str.slice(0, 10)
                     .str.to_date(strict=False).alias("pub_date"))
df = df.with_columns(pl.col("pub_date").fill_null(pl.lit(d, dtype=pl.Date)))
```

- 依赖源码核对：`akshare/stock_fundamental/stock_notice.py:107`
  `big_df["公告日期"] = pd.to_datetime(big_df["公告日期"], errors="coerce").dt.date`
  ⇒ `公告日期` 是 **Python `date` 对象**，`pl.from_pandas` 后为 `pl.Date`。
  `.cast(pl.String)` → `"2026-09-30"`，`.str.slice(0,10)` 得到同值，`.str.to_date` 还原 `pl.Date`。**真实日期未被破坏。**
- `:132` 的 `fill_null(d)` **只兜底空值**，不覆盖已有真实日期 —— 与「PIT 红线」要求一致。
- **语义说明（非缺陷）**：东财接口 `params` 是 `begin_time == end_time == date`（依赖源码 `:47-48`），
  是**单披露日快照**，故所有行的真实 `公告日期` 恒等于 `d`。于是 `:132` 的 `fill_null(d)` 近乎 no-op，
  且 `:22` docstring「pub_date 用接口返回的真实公告日期，绝不写成抓取日」在**本接口语义下等价于 `d`**
  —— 即该保证**成立但比字面弱**：若将来换成多日区间接口，必须复核 `fill_null(d)` 是否会把真实日期盖掉。
- **建议（可选，不阻断）**：把 `:132` 注释从「极少数公告日期为空」改为
  「东财为单日快照，空值兜底为披露日 d；d 即真实公告日」，避免后人误读为强 PIT 校验。

### 2. `_build_frame` 入参列匹配 —— 正确

- `_build_frame` (`announcements.py:50-66`) 读取 `title`、并 `select(["symbol","pub_date","title","type","sentiment","source","url"])`。
- 调用点 `:135-136` 传入 `df.select(["symbol","pub_date","title","url"])` —— **四个必需列齐全**。
- `:109` 已保证 `title` 存在；`url` 来自 `RENAME` 的 `网址`（依赖源码 `:96` 确有此列）。
- `classify(str(t))` (`announcements.py:57`) 对 `None` 标题安全（`str(None)="None"` → 其他/neutral，不崩）。
- **结论：列名/列集完全匹配，无漂移。**

### 3. 年份分区归属 —— 实践中正确，但缺不变量保护（低风险）

- 链路：`save_announcements(df, d)` (`announcements.py:212`) → `write_partition(..., trade_date=d)`
  → `_year_of(d)` (`parquet_store.py:846`) → 写到 `year={d.year}`。
- **风险点**：分区年份取自**披露日 `d`**，而非行内 `pub_date.year`。当前因东财快照
  `begin_time==end_time==d`，二者必然一致 ⇒ **不会错放**。
- **但代码未强制该不变量**：若 `_fetch_day` 某行 `pub_date` 落在别的年份（接口变更 / 跨年
  时间戳），该行会被写进 `year={d.year}` 分区，与其 `pub_date` 的年份不一致。读路径
  `load_announcements_asof` 是全分区 glob + 按 `pub_date` 过滤 ⇒ **仍读得到**，不会丢数据，
  但按年分区语义会被污染。
- **建议（可选，不阻断）**：`_fetch_day` 末尾加一行健壮性过滤，
  `df = df.filter(pl.col("pub_date").dt.year() == d.year)`（或断言二者一致并告警），
  把不变量显式化。当前不回补也能正确落盘，故**不阻断进行中的回补**。

### 4. 4 元组去重键跨日合并 —— 正确

- 键 = `(symbol, title, pub_date, url)`（`announcements.py:213`）。
- **关键性质**：同标题、不同公告日的两条公告 → `pub_date` 不同 → **键不同 → 都保留**，不会被压。
  （这正是本次修复的目的：旧 `(symbol,title)` 会把「按月披露的回购进展」等同名公告压成 1 条。）
- `url` 由 `网址` 映射，依赖源码 `:96` 为
  `https://data.eastmoney.com/notices/detail/{代码}/{编码(art_code)}.html` —— **每条公告唯一**，
  进一步保证不同公告不会被误并。
- 跨日合并（`write_partition:851-853` 读整年 + concat + `unique(keep="last")`）在 4 元组下安全。
- **结论：不会误压同标题不同日公告。**

---

## 附：额外发现（不影响本次回补，供知悉）

1. **东财无分页上限**，优于巨潮：依赖源码 `stock_notice.py:52` `total_page = ceil(total_hits/100)`，
   无 ~100 页重置问题 ⇒ 峰值日（2026-04-29 = 26424 行）可完整取。team-lead 换源决策正确。
2. **`save_announcements` 的逐日累加写是 O(n²) 级**：`write_partition` 每写一天都要
   「读整年 + concat + 去重 + 重写整年」。666 天 × 逐年文件重写 ⇒ 后段会明显变慢（年文件越大越慢），
   但**正确性无损**，仅耗时。若耗时超预期，可改「按年 `write_year_batch` 一次性落盘」（我旧脚本的做法）。
3. `_build_frame` 的 `source` 硬编码为 `"eastmoney"`，与团队先前「清理 akshare 来源脏数据」的口径不同
   —— 本次是**真实**东财数据（非脏数据），语义正确，但**存量清理规则**（我曾按 `source=='akshare' & url.isNull()` 清）
   不会误伤它，放心。

---

**复核人**：announce-fixer · **时间**：2026-09-30 · **未改文件、未执行删除**
