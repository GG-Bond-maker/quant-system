# B3a 批次审核报告 · 数据层 · 存储 / 面板 / 日历

> 审核员：B3a（数据层·存储/面板/日历）｜日期：2026-09-21
> 纪律来源：`docs/audit-2026-09-18/AUDIT-BRIEF.md`（硬约束）
> 范围：`app/data/{parquet_store,panels,cross_section,features,pipeline,universe,portfolio_source}.py`、
> `app/data/calendar_store.py`、`app/domain/calendar.py`
> 跳过（B3b 已覆盖）：`data/ingest/*`、`quality.py`、`screening.py`、`etf.py`、`quotes_hub.py`、
> `realtime.py`、`announcements.py`、`text_ingest.py`、`repair.py`（仅做交叉引用）

## 0. 方法与证据

- 所有「确定」结论均以**合成数据探针**（临时 `DATA_ROOT` + `tempfile`）或**只读生产扫描**复现，
  探针与原始输出留在 `backend/.tmp_b3a/`：
  `probe1.py/.out.txt`（分区裁剪/坏文件/路径约定/manifest 自愈）、
  `probe2.py/.out.txt`（生产目录体检：大小/文件数/tmp/manifest）、
  `probe3.py/.out.txt`（并发写丢失/陈旧 manifest/跨年落错分区/投影退化）、
  `probe4.py/.out.txt`（features 版本与 schema、日历缓存）、
  `probe5.py/.out.txt`（版本切换年份缩水、日历边界、cs 扫描耗时、panels 打开文件数）、
  `probe6.py/.out.txt`（生产列集一致性、两条投影读语义相反）、
  `probe7.py/.out.txt`（manifest rows 语义冲突量化）、
  `probe8.py/.out.txt`（pipeline 空代码集边界）、
  `probe9.py/.out.txt`（universe_daily vs _bt 新鲜度、网格内存量级）。
- 生产数据（`data/parquet`、`data/sqlite/aqp.db`）**只读**访问，未写入、未修改。
- 定向 pytest（加 `audit_mkdtemp_fix` 补丁）：
  `tests/test_manifest_self_heal.py tests/test_data_prep.py` → **20 passed**；
  `tests/test_universe.py tests/test_atomic_write.py` → **8 passed**（无回归）。
- 沙箱修正：`tempfile.mkdtemp` 建的目录在 Windows 受限 DACL 下无法再建子目录，探针内强制
  `os.mkdir(path, 0o777)`（与 `audit_mkdtemp_fix` 同法），与本项目行为无关。

---

## 1. 逐文件结论

| 文件 | 结论 |
|---|---|
| `app/data/parquet_store.py` | **P0–P2 问题最多**：manifest `rows/first/last` 语义冲突（下游概览数字错 -34.9%/-91.6%）；`write_partition` 并发读-改-写丢失更新（实测 3→2 行）；自愈只做一次（`_audited`）后新旁路写被永久静默忽略；自愈单向（条目多于磁盘不修）；`read_symbol_dataset` 无年份分区裁剪、无坏文件容忍；`write_whole_symbol` 写出的 `all.snappy.parquet` 读取路径看不到却被 manifest 计为有数据 |
| `app/data/panels.py` | 未发现 P0–P2。**该文件不做面板对齐/ffill**（简报里的 ffill 前视风险不在本文件，见 §5 交叉参照）；`build_chip/build_risk` 文档声称「近似回看区间」但未传 `start/end`（P3）；3 个函数零调用方（P3 死代码） |
| `app/data/cross_section.py` | P2 两条：`_scan_source` 每次构建全量扫 11k 文件（实测 21.6s/数据集，每晚 3 个数据集）；「增量」失效粒度按“源文件 mtime”而非“日期”，改一只标的的当年分区会让**整年**的所有交易日判为 stale（实测 built=3/5）。`remove_mirror`/`read_cross_range` 无生产调用方（P3） |
| `app/data/calendar_store.py` | P2（B2 已知条目的延伸）：`_CACHE` 无 TTL、无自愈——启动时若 DB 缺失/读取失败，空日历会被缓存**整个进程生命周期**（实测：DB 随后建好仍返回 0）；只有 lifespan 与 sync 的 `set_calendar` 能刷新 |
| `app/domain/calendar.py` | P2：日历尾部边界行为不一致——`last_completed_trade_day`/`prev_trade_day` 在日历末日之后**静默**返回日历内最后一天（实测 `now=2027-01-04` → `2026-12-31`），而同一边界 `next_trade_day` 抛 `ValueError`；无「结果比今天落后 N 天」的告警。日历内容本身正确（节假日/周末判定与 6543 个真实交易日吻合） |
| `app/data/features.py` | P2：`FEATURE_VERSION` 默认空串 + 按 mtime 选版本 → `touch` 一个旧版本文件即静默切换版本并**缩短年份覆盖**（实测由 2018–2023 变成只剩 2018–2021）。与 `ml/monitor._resolve_feature_dir` 是两处同口径实现（后者已有 parity 测试，且文件枚举方式不同：`glob("year=*")` vs `rglob("*")`）。生产 `date` 为 `Datetime(ms)`，`cast(pl.Date)` 成立，无 round-trip 问题 |
| `app/data/pipeline.py` | P2（性能，源自 `read_symbol_dataset` 无分区裁剪）：`step_validate` 每晚对 ~2167 只各做 1–2 次**全历史读**（实测 10.6 ms/只 vs 单年读 1.5 ms/只 ⇒ 23–46s vs 3–7s）。P3：日历降级 + 空代码集时误报「整日/大面积数据丢失」致命 |
| `app/data/universe.py` | 只补缺口（4 条已知不重复验证）。新增：`universe_daily_bt`（回测真正读的数据集）**没有任何 pipeline 步骤**，仅手动脚本重建 → 生产实测停在 **2026-09-04**、最新年分区仅 **1133 只**，而每晚 `build_universe` 重建的是回测**不读**的 `universe_daily`（P2）；`is_new_issue` 用自然日而非 5 个交易日（P3）；全市场交叉连接无按年分批（P3 内存量级） |
| `app/data/portfolio_source.py` | P2：ETF 备源（新浪 `fund_etf_hist_sina`，**无 adjust 参数 = 不复权**）与主源（东财 `adjust="qfq"`）写入同一缓存键、同一返回类型、不披露来源/口径 ⇒ 组合回测的 ETF 价格序列会在无提示下切换复权口径 |
| 其它顶层模块 | 按简报跳过（属 B3b）；仅交叉引用 `repair.py`：`is_quarantined()` **零调用方**，隔离语义从未被任何读路径消费（§3） |

---

## 2. 问题详表（目标 A / 一致性）

### A-1（P2，一致性）manifest 的 `rows`/`first`/`last` 两条写路径语义冲突 → `/datacenter/overview` 数字错 34.9%~91.6%
**位置**：`app/data/parquet_store.py:118-130`（`_manifest_record`，写路径）、`:133-151`（`_manifest_scan_dataset`，只写 `rows`，**从不写 first/last**）、`:186-199`（自愈重扫）；
消费方 `app/api/v1/datacenter.py:274-310` + `:380-382`（`/overview` 的 `datasets` 字段）。

**触发条件**：任何一次 `manifest_invalidate()`/自愈重扫（repair、隔离、扩容脚本、每次进程首次发现条目数不足）之后，被扫描的条目只有 `rows`；
写过盘的条目（`_manifest_record`）`rows` 是**该年分区**的行数（`write_partition` 传的是合并后的整年 df），`first/last` 只是该年的区间。
两者被 `datacenter._manifest_dataset_summary` 当作同一语义聚合（`min(first)`/`max(last)`/Σ`rows`）。

**证据（生产实读，只读）**：
```
daily_bar     : entries=2499 有 first/last=983 (39%) | 真实总行 2,482,805 vs manifest 合计 1,617,190 (-34.9%)
daily_bar_hfq : entries=2500 有 first/last=982 (39%) | 真实总行 2,482,612 vs manifest 合计 1,617,962 (-34.8%)
universe_daily: entries=1   有 first/last=1        | 真实总行 5,160,640 vs manifest 合计   431,462 (-91.6%)
样本 000001.SZ: manifest rows=174（=year=2026 分区行数） vs 实际全历史 2116 行
模拟 /overview 摘要：daily_bar start=2026-01-05 end=2026-09-18（真实 2018-01-02 ~ 2026-09-18）
```
**验证命令**：`backend/.venv/Scripts/python.exe backend/.tmp_b3a/probe7.py`（输出 `probe7.out.txt`）；
`python -c "import json;m=json.load(open(r'data/parquet/.manifest.json',encoding='utf-8'));print(sum(1 for e in m['daily_bar'].values() if e.get('first')), len(m['daily_bar']))"` → `983 2499`

**标注**：**已知台账「manifest 口径与磁盘实际不一致（universe_daily/daily_bar_qfq/daily_bar）」的延伸**——本条目给出的是
「两条写路径对同一字段语义不同 + 61% 条目缺 first/last + 下游概览数字量化偏差」，不是台账原文的重复。
前端 `本地资产清单` 表走 `/datacenter/datasets`（真实逐文件扫描，正确），所以当前**用户可见面尚未被污染**；
但 `/overview` 的 `datasets[].rows/start/end` 是公开契约字段，任何消费方（运维面板/报表/CLI）都会读到错数字。
**建议**：`_manifest_scan_dataset` 补齐 first/last（footer 里可读到 min/max 统计信息），或把 `rows` 明确成
「全历史行数」并在写路径按「年→全历史」维护；两者取其一，不能让同一字段有两种语义。

### A-2（P1，Bug/数据丢失）`write_partition` 并发读-改-写会静默丢行
**位置**：`app/data/parquet_store.py:450-470`（读整年 → `_align_concat` → `unique(keep="last")` → `_atomic_write_parquet`）。
**触发条件**：两个写者对同一 `(dataset, symbol, year)` 并发（进程内两个线程 / 服务进程 + CLI 脚本进程）。
`atomic_write_parquet` 只保证单文件不半写，**不保证写者顺序**（`core/pipeline_lock.py:7-8` 自己写明），
且 `pipeline_slot` 由调用方持有（sync/pipeline/mirror/training），CLI（`app/data/ingest/__main__.py`）与
`scripts/*` 旁路进程完全不受它保护。

**证据（合成探针，两个线程各写一天，读窗口人为拉长 0.4s）**：
```
初始行数: 1
两个并发写者各写一日后，文件行数: 2 (无锁语义下正确值=3)
文件内日期: ['2024-01-02', '2024-01-03']   异常: []
manifest 登记: {'rows': 2, ...}     ← manifest 与“丢失后的事实”一致，事后无从发现
```
**验证命令**：`python backend/.tmp_b3a/probe3.py`（`probe3.out.txt` [C1]）
**生产可达性（真实的两个写者）**：`scripts/build_universe.py:46` → `build_universe_history(persist=True)`
→ 对 `universe_daily/symbol=__all__` 的 9 个年份分区逐个 `write_partition`（每年 ~60 万行、50MB；
读-改-写窗口数十毫秒），而服务端 `orchestrator.step_build_universe`（`FULL_STEPS`）在同一数据集上做同一件事
且持有 `pipeline_slot` —— 运维按文档手动跑脚本时完全不受 `pipeline_slot` 保护。
**建议**：`write_partition` 内按 `(dataset, symbol[, year])` 加进程内粒度锁（与 `pipeline_lock` 同层），
并在写前校验读到的 `max(date)`；跨进程场景用文件锁或把 CLI 纳入 `pipeline_slot`。

### A-3（P1，Bug/静默不可见）manifest 自愈只生效一次：之后的新旁路写被永久静默忽略
**位置**：`app/data/parquet_store.py:61-64`（`_audited`）、`:186-199`（仅在 `dataset not in _audited` 时重扫并 WARNING）。
**触发条件**：同一进程内某 dataset 已自愈过一次（`_audited` 命中），之后任何旁路写（扩容脚本、repair 直写、
离线重建）新增的 symbol 都不会再被重扫，也不会再有 WARNING。
**证据（合成探针）**：
```
旁路写 1 次后 list_symbols_with_data -> ['000001.SZ','000002.SZ','600519.SH']   _audited={'daily_bar'}
旁路写 2 次后 list_symbols_with_data -> ['000001.SZ','000002.SZ','600519.SH']   ← 000003.SZ 静默缺失
磁盘实际 symbol= 目录: [..., 'symbol=000003.SZ', ...]
```
**验证命令**：`python backend/.tmp_b3a/probe1.py`（`probe1.out.txt` [P5]）
**标注**：同样是已知 manifest 台账的**新机制延伸**（一次性预算让自愈退化为「首错遮百错」）。
**建议**：把「每进程一次」改成「按 mtime/目录数变化触发的节流」（例如目录数变化即重算，带最小间隔）。

### A-4（P2，一致性）自愈单向：manifest 条目多于磁盘时永不修正
**位置**：`app/data/parquet_store.py:186-199`（只判 `n_man < n_dir`）。
**触发条件**：整只标的被 `repair.quarantine_symbol` 移走（`repair.py:111-132`）、目录被改名/删除、
或 `daily_bar` 某只标的全部年份被清理。同进程内 `_manifest` 仍保留该 symbol 且 `rows>0`。
**证据（合成探针）**：
```
删掉 BBB.SZ 目录后（manifest 未失效）-> ['AAA.SZ','BBB.SZ']
BBB.SZ 仍被列为有数据: True        read_symbol_dataset(BBB.SZ) 行数: 0
```
**验证命令**：`python backend/.tmp_b3a/probe3.py`（`probe3.out.txt` [C2]）
**补充交叉证据**：`repair.quarantine_partition` 自身会 `manifest_invalidate()`（`repair.py:104`），
所以「同一进程内紧接着读取」是安全的；但**旁路进程/未调 invalidate 的清理路径**（脚本删目录、扩容脚本重命名）
以及 `skip_empty=False` 的调用方会读到幽灵 symbol。
**建议**：双向校验（条目数不等即重扫）+ 对 `read_symbol_dataset` 返回空但 manifest 有行的组合打 WARNING。

### A-5（P2，Bug/可用性）`read_symbol_dataset` 无年份分区裁剪：区间查询也要读全部年份
**位置**：`app/data/parquet_store.py:376-416`（`files = sorted(base.glob("year=*.parquet"))` 后**在内存里**过滤 date）。
**触发条件**：任何带 `start/end` 的调用。
**证据（生产实读，spy 记录打开的文件）**：
```
请求 2024 单年 -> 实际打开: year=2022, year=2023, year=2024   分区裁剪生效? False
生产 25 只标的：read_symbol_dataset(start=end=2026-09 单日区间) 10.6 ms/只
               read_symbol_year(2026) 单年文件                 1.5 ms/只（≈7×）
单只标的年份文件数: 2018…2026 共 9 个
外推 step_validate（每晚 ~2167 只 × 1~2 次）：≈23~46s（按年裁剪后 ≈3~7s）
panels.build_chip(600519.SH) 打开 5 个年份分区（2022–2026）只为取最后 120 行
panels.build_risk(600519.SH) 同样打开 5 个年份分区
```
**验证命令**：`python backend/.tmp_b3a/probe1.py`（[P1]）、`python backend/.tmp_b3a/probe5.py`（[L4]/[L5]）、
`python backend/.tmp_b3a/probe3.py`（[C5]）
**建议**：`files` 先按 `[start.year, end.year]` 裁剪；`panels.build_chip/build_risk` 的 docstring 声称
「近似回看区间/有限窗口」，实际未传 `start`（要么补 `start=今天-2*lookback*1.6`，要么改文档）。

### A-6（P2，Bug/一致性）一个坏分区让整个 symbol 不可读，且与另一条投影读路径行为相反
**位置**：`app/data/parquet_store.py:404-406`（`read_parquet_columns` 有 try/except 跳过坏文件）
vs `:398-406`（`read_symbol_dataset` 无任何容错，`pl.read_parquet` 直接抛）。
**触发条件**：任一年份分区截断/损坏（历史遗留、外部工具写入、磁盘故障）。
**证据（合成探针）**：
```
read_symbol_dataset: 抛 ComputeError -> 该 symbol 全部年份不可读
read_parquet_columns: 返回行数 = 6 (静默跳过坏文件)
```
`tests/test_atomic_write.py` 的守卫只覆盖 `read_parquet_columns` 路径，`read_symbol_dataset` 无测试。
**验证命令**：`python backend/.tmp_b3a/probe1.py`（[P2]）
**建议**：两条读路径统一容错口径（跳过坏文件 + WARNING/审计计数），避免「同一数据集两种可用性」。

### A-7（P2，性能）`cross_section._scan_source` 每次构建全量扫描（stale 计算无缓存）
**位置**：`app/data/cross_section.py:51-73`、`81-141`；调用方 `app/orchestrator.py:280-284`（每晚）、
`app/api/v1/datacenter.py:1070-1077`（`mirror_status`，120s 缓存）/`:1101-1107`（`/mirror/rebuild`）。
**证据（生产实读）**：
```
_scan_source(daily_bar): files=11087 dates=2116 耗时=21.6s（1.9 ms/文件）
MIRROR_DATASETS = ('daily_bar','daily_bar_hfq','daily_bar_qfq') -> 每晚 3 个数据集各扫一遍（≈65s）
```
**验证命令**：`python backend/.tmp_b3a/probe5.py`（[L4]）
**建议**：把「日期→源 mtime」索引落盘（与 manifest 同处），按数据集文件数/mtime 增量维护；
或至少把 `_scan_source` 结果按 dataset 缓存并在写路径失效。

### A-8（P2，Bug/设计）镜像「增量」失效粒度错误：改 1 只标的会让整年所有交易日重建
**位置**：`app/data/cross_section.py:60-73`（`date_mtime[d] = max(mtime of files containing d)`）+ `:99-100`、`:144-149`。
**触发条件**：`write_partition` 每晚重写**当年**整年分区 ⇒ 该年每个交易日的 `date_mtime` 都被推新
⇒ `_stale` 对当年**全部**日期为真 ⇒ 镜像重建整个当年，而非只建新增的 1 个交易日。
**证据（合成探针：2 只标的 × 2 年 × 2 日）**：
```
首次构建: built=4 skipped=0
无改动再构建: built=0 skipped=4
改动 1 只标的的 2024 分区（新增 1 日）后: built=3 skipped=2   ← 期望 built=1
```
**验证命令**：`python backend/.tmp_b3a/probe3.py`（[C6]）
**建议**：stale 判据改为「镜像缺失 或 该日期的源文件 mtime > 镜像 mtime，**且** 镜像行数/标的数与源不一致」
或直接比较镜像与源在(该日)的 (symbol 数, 行数) 指纹；否则 docstring 的「增量」承诺不成立。

### A-9（P2，一致性）`FEATURE_VERSION` 留空时按 mtime 选版本：一次 touch 即静默切换并缩短年份覆盖
**位置**：`app/data/features.py:21-52`（`candidates` 取版本目录内 `max(mtime_ns)`）。
**触发条件**：生产 `FEATURE_VERSION` 默认为空串（`core/config.py:79-86`）；任一被淘汰版本的部分年份文件被重写/触碰。
**证据（合成探针）**：
```
初始自动选择: v_new（2018–2023 中的 2022/2023）
touch v_old（2018–2021）一个文件后自动选择: v_old
读到的年份: [2018,2019,2020,2021]  行数: 4     ← 2022/2023 样本从读路径静默消失
生产当前自动选择 = alpha_basic_v2g（v1 为 424.7MB 的上一代；见 §4）
```
**验证命令**：`python backend/.tmp_b3a/probe5.py`（[L2]）、`python backend/.tmp_b3a/probe4.py`（[F2]）
**建议**：自动选择增加「年份覆盖不得少于上次选中版本」的守卫（或按版本目录名/mtime 取**全部文件**最大 mtime 并要求
写入完整性标记），生产显式固定 `FEATURE_VERSION`。

### A-10（P2，一致性）ETF 备源不复权却与主源共用缓存键、且不披露来源
**位置**：`app/data/portfolio_source.py:84-109`（主源 `adjust="qfq"`；`:99-100` 备源 `ak.fund_etf_hist_sina`）。
**证据（依赖源码 + akshare 实现）**：`fund_etf_hist_sina(symbol)` **没有 `adjust` 参数**
（`.venv/Lib/site-packages/akshare/fund/fund_etf_sina.py:116`，取新浪 `klc_kl` 原始价），
而股票备源 `stock_zh_a_hist_tx(..., adjust="qfq")` 是前复权（`stock_feature/stock_hist_tx.py:19-25`）。
返回值只有 `pd.Series`（无 `source`/`adjust` 字段），并写入同一 `aqp:pf:close:{type}:{code}:{start}:{end}`
键（TTL 600s）⇒ 主源抖动后的 10 分钟内，同一 code 的「价格序列」口径与另一只标的可能不同。
另外备源调用**没有 try/except**（主源有），源站异常会直接抛给调用方，降级语义不一致。
**验证命令**：`Select-String -Path "backend/.venv/Lib/site-packages/akshare/fund/fund_etf_sina.py" -Pattern "def fund_etf_hist_sina" -Context 0,3`；
`python -c "import inspect,akshare as ak;print(inspect.signature(ak.fund_etf_hist_sina))"` → `(symbol: str = 'sh510050')`（无 adjust）
**建议**：备源改用带复权的 ETF 源，或在返回结构/缓存键里带上 `source`+`adjust`，并在组合回测响应中披露。

### A-11（P2，一致性）日历缓存无 TTL / 无自愈：空日历会在进程内永久驻留
**位置**：`app/data/calendar_store.py:22`（`_CACHE`）、`:63-68`（`get_calendar` 一次加载后不再刷新）、
`:71-75`（`refresh_calendar_cache` 仅 lifespan）、`:78-81`（`set_calendar` 仅 sync 调用）。
**触发条件**：启动时 DB 不存在/表不存在（新部署、备份恢复、DB 被重建）→ `load_from_db_sync` 返回空日历并被缓存；
此后同一进程内即便 DB 已有 6543 个交易日也不会更新（只有 sync 的 `_refresh_trade_calendar` 或重启能救）。
**证据（合成探针）**：
```
DB 不存在时 get_calendar() 长度 = 0
DB 建好后再取（同进程）= 0 -> 是否仍为空: True
load_from_db_sync() 重新加载 = 2
```
**影响链（代码级）**：空日历 ⇒ `is_trade_day` 恒 False ⇒ `jobs/evening_routine.py:52-60` 直接
`_mark("skipped")` 并返回（当天不会再跑流水线）；`data/pipeline.step_validate` 走降级覆盖率判据；
`parquet_store.today_trade_date_or_last` 降级为周末回退。自动同步路径会在 `_run_incremental` 开头
`_refresh_trade_calendar()`（`services/sync_service.py:235`）自愈，所以需要「同步也失败」才会真正锁死；
但**没有任何告警升级**，只有 WARNING。
**验证命令**：`python backend/.tmp_b3a/probe4.py`（[K2]）
**标注**：**B2 已报的「空日历 → domain/calendar 降级为全非交易日」的延伸**（新增「无 TTL/负缓存常驻 +
evening_routine 整天静默 skip」这条影响链）。**建议**：空结果不缓存（或短 TTL 重试）+ 结果为空时
`logger.error` 并在 /health 暴露。

### A-12（P2，Bug/边界）日历尾部：`last_completed_trade_day` 静默返回日历末日，`next_trade_day` 同期抛错
**位置**：`app/domain/calendar.py:46-53`（20 天窗口）、`:56-63`、`:70-88`；`app/data/parquet_store.py:215-241`。
**触发条件**：日历覆盖到某个上限（生产实测 `2000-01-04 ~ 2026-12-31`，6543 行），之后日期继续使用。
**证据（生产日历直接调用）**：
```
prev_trade_day(首日 2000-01-04) -> 抛 ValueError（预期）
next_trade_day(末日 2026-12-31) -> 抛 ValueError（预期）
last_completed_trade_day(now=2027-01-04 16:00) -> 2026-12-31   ← 静默返回，不报错
today_trade_date_or_last() 同场景 -> 经 prev_trade_day(2027-01-05) 也命中 2026-12-31
```
即：跨年后若日历未刷新，平台「今天」会静默停在 2026-12-31（最长 20 天后才转而抛错→周末回退），
`sync` 的目标交易日同样冻结在 2026-12-31，而当晚「两侧都已 >= target」判据会让同步**报成功**。
**验证命令**：`python backend/.tmp_b3a/probe5.py`（[L3]）
**建议**：`last_completed_trade_day`/`today_trade_date_or_last` 增加「返回值与 `date.today()` 相差 > N 个自然日
或 > 日历 max」时 raise/WARNING；`next_trade_day` 的风格（抛错）应与之统一。

---

## 3. 目标 B · 死代码 / 未接线

| 位置 | 分类 | 依据 | 建议动作 |
|---|---|---|---|
| `parquet_store.path_for` (`:347-353`) | 真死代码 | 全仓 `rg "\bpath_for\b"` 仅命中定义与 `__all__`；`path_for_year` 才是活路径 | 删（顺带消除「`all.snappy.parquet` 约定」的误导） |
| `parquet_store.write_year_batch` (`:473-482`) | 真死代码（被取代的旧路径）+ 破坏性语义 | 仅 tests 调用（7 个测试文件）；`ingest/tasks.py:98-100` 明确记录了它「整年覆盖会抹掉该年其它日期」的旧事故 | 删或降为测试辅助；生产只留 `write_partition` |
| `parquet_store.write_whole_symbol` (`:485-495`) | **未接线 + 陷阱** | 生产零调用方（仅 `tests/test_data_prep.py:41`）。它写 `symbol=X/all.snappy.parquet`，而 `read_symbol_dataset`/`read_symbol_year` 只 glob `year=*.parquet` ⇒ 数据写进去**读不出来**，但 `_manifest_record` 会登记 rows>0，`read_all_symbols` 仍返回该 symbol | 删；若要保留小表全量写，必须同时让读取路径识别 `all.snappy.parquet` |
| `cross_section.remove_mirror` (`:205-209`) | 真死代码 | 无任何调用方（含 tests） | 删或接到 `/mirror/rebuild --full` |
| `cross_section.read_cross_range` (`:160-173`) | 未接线 | 仅 `tests/test_data_prep.py` 调用；生产只用 `read_cross_section`（`screening.py:672,679`） | 保留（公开 API 有测试）或删；若保留应有人用 |
| `repair.is_quarantined` (`:135-140`) | 真死代码 + 语义未接线 | 全仓零调用方（`quarantined_symbols` 仅 CLI 报表用）。读路径（`read_all_symbols`/`read_symbol_dataset`/features/screening）**从不查询隔离状态**；单年隔离（`scripts/repair_data.py:114/135`）后 symbol 仍留在池中，只有「整只隔离」因目录为空被 `skip_empty` 顺带排除 | 接线到标的池入口（`read_all_symbols` 过滤已隔离 symbol）或删 |
| `panels.recent_trade_date_str` (`:367-369`)、`panels.announcement_window` (`:372-373`)、`panels.code_of` (`:376-377`) | 真死代码 | 全仓（app+tests）零调用方 | 删 |
| `universe.build_universe_backtest` | **未接线（有 P2 后果）** | `FULL_STEPS`/`EVENING_STEPS`（`orchestrator.py:388-413`）不含它；唯一调用方是手动脚本 `scripts/expand_universe_2500.py:1003-1016` 与测试。生产 `universe_daily_bt` 实测停在 **2026-09-04**、最新年 **1133 只**（`universe_daily` 为 2026-09-17 / 2494 只）。而每晚 `build_universe`（59.4s）重建的 `universe_daily` **不是回测读的数据集**（`api/v1/backtest.py:90` 读 `universe_daily_bt`） | 把 `build_universe_backtest` 并入 `FULL_STEPS`（或明确文档化为手动步骤 + 增加新鲜度告警） |
| `universe._limit_pct_expr(days_since_list=…)` (`:103-104`) | 未使用参数 | 形参在函数体内从未使用 | 删参数（顺带确认 `is_new_issue` 已在外层算好） |

---

## 4. 目标 C · 存储效率（只读 `ls` / 大小统计，未删改任何数据）

生产 `data/parquet` 全量 **≈ 2.13 GB**：

| 数据集 | 文件数 | 大小 | 备注 |
|---|---|---|---|
| `features` | 14 | **1457.5 MB** | `alpha_basic_v2g` 1032.9 MB（9 年）+ `alpha_basic_v1` 424.7 MB（5 年，上一代，**无保留策略**） |
| `cs`（截面镜像） | 6345 | 297.7 MB | 3 个数据集各 2115 个 `date=` 文件；**源数据已有 431.6 MB** ⇒ 镜像 = 额外 +69% 存储 |
| `daily_bar_qfq` | 11081 | 156.9 MB | qfq 可由 hfq/factor 推导（`repair.build_qfq_dataset` 每晚全量重建），三口径共 431.6 MB |
| `daily_bar_hfq` | 11086 | 138.8 MB | |
| `daily_bar` | 11087 | 135.9 MB | 生产实测 11 列/文件，**列集完全一致**（probe6 [M1]） |
| `universe_daily` | 9 | 50.5 MB | 回测不读（screener 读） |
| `universe_daily_bt` | 5 | 40.0 MB | 回测读；停在 2026-09-04、仅 1133 只（§3） |
| `universe_daily_legacy` | 6 | 2.5 MB | 纯归档（120 只，无任何读取方，`scripts/build_universe.py:41-45` 归档产物） |
| `predictions` | 262 | 1.2 MB | |

- **重复存储**：三口径 bar（3×）、`cs` 镜像（+1×）、三份 universe（93 MB，其中 legacy 完全无消费者）。
  `cs` 镜像本身是**有意设计**（`read_cross_section` O(1)），但它的「增量」是假的（A-8），且
  `orphan_dates`（源已删、镜像残留）永不清理（模块 docstring 自认需手动全量重建）。
- **无 TTL 的历史增长**：`features` 占全仓 68%，其中 424.7 MB 是被 v2g 取代的 v1；`alpha_basic_v1`
  作为**策略名**仍在使用（`data/screening.py:33`、`screener.py:376/457`、`feature_runs`），但作为
  **特征数据集版本**只被 `scripts/p2_experiment.py:71`（A/B 实验）读取。建议：确认 A/B 结论已归档后，
  将 `version=alpha_basic_v1` 移到 `data/quarantine/` 或冷存（可省 424.7 MB ≈ 全仓 20%），
  并给 features/universe/cs 加显式保留策略（当前没有任何清理/归档任务）。
- **列裁剪实际收益**：`read_symbol_dataset(columns=…)` 逐文件投影确实生效（panels 只取 7 列），
  但**没有年份裁剪**（A-5），所以「列少读、年不少读」。features 无 symbol 分区（年度单文件），
  `read_feature_frame` 只能整年读（year=2026 单文件 428,685 行 × 88 列，0.13s）——可接受。
- **文件名与实际编码不一致（非缺陷，仅备注）**：所有分区名仍是 `year=YYYY.snappy.parquet`，
  但新写入已统一为 zstd level 7（`parquet_store.py:499-503`），读取靠 footer 自描述，新旧混存兼容；
  因此「按后缀估算压缩率/做替换」类操作会误判。

---

## 5. 明确「未发现问题」与已排除项

1. **`Decimal` / `date` round-trip 无漂移**（问题清单里点名的项）：写入 `Decimal('1.23')`（scale=2）→
   读出 `Decimal('1.23')`（precision 由 None 变 38，值不变）；`Date`/`Datetime(us)` 原样回读；`Float64` 精确
   （`1.2300000000000002` 保持不变）。合成探针 `probe1.py [P8]`。
2. **原子写在本机不会跨盘符失效**：tmp 与目标同目录（`target.with_suffix(".{hex}.tmp")`），
   `os.replace` 同卷原子；进程内异常路径的 tmp 由 `finally` 清理（`tests/test_atomic_write.py` 已覆盖，实测通过）。
   *未覆盖*的是「进程被强杀」路径（见 P3-2）。
3. **生产 `daily_bar/_hfq/_qfq`、`universe_daily(_bt)` 的列集与 dtype 完全统一**（33,259 个文件，footer 全扫）：
   不存在「新增列静默补 NULL / 类型变化」的现实触发（探针 `probe6.py [M1]`）。相关代码风险记为 P3 潜在项。
4. **`panels.py` 无面板对齐/前视风险**：该文件只做个股分块（quote/资金流/事件/股东/筹码/风险），
   没有 `pivot`/`reindex`/`ffill`。简报点名的「ffill 是否引入前视」实际位于
   `domain/portfolio.py:199-201`、`api/v1/desk.py:373/392/401`、`api/v1/research.py:546-558`、
   `ml/gp_miner.py:326`、`ml/train_service.py:247-254`（分属 B2/B4/B7 批次，本批只作交叉参照）。
   `panels.py` 各块的内存量级是「单只标的 × 全历史」（~2k 行），不构成 2499×N 面板峰值；
   真正的全市场面板峰值在 `universe.build_universe_history/backtest`（见 P3-6）。
5. **`panels.TTL` 不是死配置**：`api/v1/stock.py:46,243` 读取；缓存键含交易日（`td`）与参数键，日级失效正确。
6. **`data/pipeline.py` 不是 `orchestrator.py` 的重复实现**：`pipeline.py` 只保留原子步骤
   （`update_daily/validate/rebuild_qfq`），编排与 `STEP_FUNCTIONS` 在 `orchestrator.py:416-425` 注册，接线正常；
   `data/features.py` 与 `ml/features.py` 职责不同（版本化读取 vs 特征计算），**不是重复实现**；
   唯二同口径双实现是 `ml/monitor._resolve_feature_dir`（已有 parity 测试）与
   `parquet_store.read_parquet_columns` vs `read_symbol_dataset` 的投影读（§P3-3）。
7. **日历内容正确**：生产 6543 个交易日（2000-01-04 ~ 2026-12-31）；抽查 `2026-10-01`(国庆)=False、
   `2026-10-08`=True、`2026-02-17`(春节)=False、`2026-09-19`(周六)=False、`2026-09-21`(周一)=True。
   `date.fromisoformat` 对 `Date` 列（`db/models.py:69`）无类型风险。
8. **`manifest` 的 5s 节流落盘不会造成「池子缩水」**：节流丢失的条目会让磁盘 manifest 条目数 < 目录数，
   下次冷启动触发一次自愈重扫（A-3 的机制在**首次**是有效的）；但会污染 A-1 的 `rows/first/last`。
9. **`steps` 的年份缺失容忍正常**：`read_symbol_dataset` 用 glob 枚举年份文件，缺年份不报错（实测删掉某年分区后仍可读其它年）。

---

## 6. P3（低价值/潜在）问题清单

1. **读路径有 mkdir 副作用**：`path_for_year`（`:340-344`）会 `_ensure_dir`，而 `read_symbol_year`（`:419-427`）
   经它取路径 ⇒ **纯读**会创建空的 `symbol=XXX/` 目录。实测 `read_symbol_year("daily_bar","999999.SZ",2024)`
   后目录数 1→2（`probe1.py [P4]`）。后果：`_count_symbol_dirs` 虚高 → 无谓触发 manifest 重扫/WARNING；
   磁盘出现幽灵目录。**验证**：`python backend/.tmp_b3a/probe1.py [P4]`。建议：拆出 `_path_for_year_no_mkdir`，
   只在写路径建目录。
2. **崩溃残留 `.tmp` 无清理**：`_atomic_write_parquet` 的 `finally` 只管进程内异常；被强杀时残留。
   生产实测 **1 个**：`daily_bar_hfq/symbol=000065.SZ/year=2024.snappy.b5cdae54.tmp`（12,408 B）；
   读取路径用 `year=*.parquet` glob 天然忽略它，但它永久占据磁盘且不会被任何清理任务回收。
   另外 `_manifest_flush_locked`（`:93-96`）落盘失败时**不清理** tmp（与 `_atomic_write_parquet` 不一致）。
   **验证**：`Get-ChildItem data/parquet -Recurse -Filter *.tmp` → 1 个；`probe2.py [S2]`。
3. **两条投影读语义相反 + 投影全缺列时崩溃**：
   - `read_parquet_columns`（`:305-336`）缺任一投影列就**丢整个文件**；`read_symbol_dataset(columns=…)`
     是**丢列补 null**。合成演示：2023 分区缺 `amount` → 前者整年行消失（只剩 1 行），后者保留 2 行（amount=null）
     （`probe6.py [M2]`）。生产中两条路径都被用于同一数据集（`market.py:117/438`、`research.py:595` vs
     `panels.py:314/328`），当前列集统一（§5.3）故未触发。
   - `read_symbol_dataset(columns=["pe_ttm"])`（列表里没有 `date`）且文件无该列 → `pl.read_parquet(f, columns=[])`
     → `InvalidOperationError: index_columns([])`（`probe3.py [C4]`）；若列表含 `date` 则静默返回窄表。
     建议投影缺列统一为「补 null」并禁止空投影。
4. **`_align_concat` 的类型升格会静默把数字变字符串**：`vertical_relaxed` 对 `Float64`+`String` 取
   `String` 超类型 —— `pl.DataFrame({"v":[1.0]})` 与 `{"v":["1.5"]}` 合并后 `v` 变成 `['1.0','1.5']`
   （`probe1.py [P7]`，无异常无日志）。生产列集当前统一，属潜在风险；建议 `_align_concat` 对
   dtype 冲突报错而非静默升格。
5. **`write_partition`/`write_year_batch` 不校验 df 年份与分区参数一致**：`write_partition("misc","X.SZ", date(2023,1,1), <含 2024 行的 df>)`
   → 2024 行被写进 `year=2023` 分区（实测 2023 分区 2 行、2024 分区不存在，`probe3.py [C3]`）。
   当前生产调用方都先按年切片（`ingest/tasks.py:125-127`、`universe.py:254-259`），属潜在陷阱。
6. **全市场交叉连接无分批（内存量级）**：`build_universe_history:190-197` / `build_universe_backtest:398-410`
   先做 `instrument × 全部交易日` 交叉连接（2499 × 2115 = **5,285,385 行**，仅 6 列骨架
   `estimated_size=338 MB`），再左连接 bars（2,482,805 行 ≈ 163 MB）⇒ 峰值约 **1~2 GB** 量级
   （CPU-only 单机 Docker）。对照：`cross_section.build_mirror` 明确按年分批以约束内存，而 universe 构建没有。
   `probe9.py`。建议按年分批（与 mirror 同法）。
7. **`is_new_issue` 用自然日而非「5 个交易日」**：`data/universe.py:219-224`（`days_since_list < 5`，
   `days_since_list` 由 `date - list_date` 得自然日；`:425-430` 同）。交易所规则是**上市后前 5 个交易日不设涨跌幅**，
   自然日窗口在跨周末/长假时**少覆盖**交易日 ⇒ 对节前上市的新股会**提前**给出涨跌停价（broker 可能挡住本可成交的单）。
   同时 `_limit_pct_expr`（`:103-120`）的注释链是坏的：`:217-218` 写「主板 2023-04 前首日 ±44%/-36% …
   单独用 pct=0.44 表达，见 `_limit_pct_expr` 的 is_new_issue 分支」，而该函数对**所有** `is_new_issue` 恒返回 `0.0`，
   **不存在 0.44 分支**；`days_since_list` 形参也从未被使用（这是 B2 已报「两套实现不一致」的成因之一，此处只补注释失真的证据）。
   （`domain/limit.py:69-73` 同样用自然日并自述「前 n 个自然日」，属 B2 范围，此处只报 `data/universe.py` 侧。）
8. **`step_validate` 空代码集 + 日历降级 ⇒ 误报致命**：`data/pipeline.py:130-137`，`coverage = 0/max(1,0) = 0 < 0.5`
   → `ValueError: ... 疑似整日/大面积数据丢失`（实测 `probe8.py [E1]/[E3]`；日历可用时 `[]` 通过）。建议 `if not codes: return "…no codes…"`。
9. **`read_prev_and_today` 对 null 直接 `float()`**：`universe.py:502`（`float(today["close"][0])`/`float(volume)`）
   与同函数 `_num`（NaN→None 容错）口径不一致；写入 null close 的分区会让 `build_universe_daily`/screening 抛
   `TypeError`（当前列集统一且写门禁拦 null，属潜在）。
10. **`/datacenter/datasets` 的扫描成本**（交叉参照，非本批文件）：`datacenter._scan_dataset:120-186` 对每个数据集
    逐文件读 date 列 = `daily_bar` 类 ~11k 文件 ≈ 20s/数据集（120s 缓存）；这正是 `/overview` 改用（错误的）
    manifest 的动机（A-1）。根因是「按 symbol×year 分区 + 无日期索引」，属存储布局层面的取舍。

---

## 7. 与已知台账/其它批次的关系（避免误判为新增）

- 「manifest 与磁盘实际不一致（universe_daily/daily_bar_qfq/daily_bar）」——**已知**。本文 A-1/A-3/A-4 是
  其**新机制与新量化证据**（两条写路径语义冲突、`_audited` 一次性预算、单向自愈），已在正文显式标注。
- 「行情双源降级是设计」——本文 A-10 **不是**报降级本身，而是报 ETF 备源**复权口径不同且不披露**。
- 「`build_universe_daily` 零生产调用方 / `is_new_issue` 对所有板块不设限 / `list_date` 97.8% NULL /
  向量化 round 少一分钱」——B2/B5 已确认，本文未重复验证；`list_date` NULL 的实际影响在
  `universe.py:193-194`（NULL 视为「已上市」）与 `:220`（NULL → 非新股），本文只引用不重报。
- 「`app/data/universe.py` 与 `domain/limit.py` 两套涨跌停实现不一致」——已知，本文不重报；
  仅新增「自然日 vs 交易日」这一条独立偏离（P3-7）。
- 「空日历降级为全非交易日」——B2 已报；本文 A-11 是其延伸（无 TTL 负缓存 + evening_routine 静默 skip）。
- `upsert_delist_dates` 零调用方 / `delist_date` 全 NULL——B3b 事实，本文只用于解释
  `build_universe_backtest` 的 `delist_date` 过滤实际不生效（`universe.py:406-407` 在 delist_date 全空时恒真）。