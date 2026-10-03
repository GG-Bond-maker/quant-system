# 磁盘占用审计与清理方案（2026-10-02）

> 起因：用户反馈「C 盘突然只剩一点空间」。本文为**只读取证**结果，**未执行任何删除**。

---

## 一、先回答：数据存在哪？

| 项 | 位置 |
|---|---|
| **项目数据根** | **`D:\Python_Project\Alpha Quant Platform\data\parquet\`**（**D 盘**） |
| 数据规模 | 39,944 个 parquet（`daily_bar` / `_hfq` / `_qfq` / `universe_*` / `features` / `predictions` / `screener` …） |
| 分区结构 | `data/parquet/<dataset>/symbol=<代码>/year=<年>.snappy.parquet` |

⇒ **项目数据全部在 D 盘，不占 C 盘。** C 盘的紧张另有原因（见下）。

**数据实际体积（实测，2026-10-02）**：`data/parquet` 总计仅 **2.42 GB** / 39,944 文件。

| 数据集 | 大小 | 文件数 |
|---|---|---|
| features | 1.34 GB | 10 |
| daily_bar_hfq | 0.28 GB | 25,186 |
| daily_bar | 0.27 GB | 25,189 |
| cs | 0.26 GB | 3,438 |
| daily_bar_qfq | 0.14 GB | 10,919 |
| universe_daily_bt / universe_daily | 0.13 GB | 10 |
| 其余（predictions / screener / announcements …） | <0.01 GB | — |

> 📌 **口径提醒：数据体量仅 2.42 GB**（39,944 文件）—— 这是**全市场扩容前**的基线。
> 扩容完成后实测（`data/expand_full_report.json`，2026-10-02 22:09）：
> `data/parquet` = **2825.0 MB（2.76 GB）/ 77,886 文件**，较基线 2490.5 MB **仅 +334.5 MB**。
> ⇒ **即便标的数翻倍（2500 → 5223）、起点前移到 2022，项目数据仍只有 GB 级**，
> 与「C 盘紧张」**完全无关**。

⇒ **本项目的数据体量极小**（压缩后的日频行情本就很小）。因此：

- **下游重建无磁盘风险**：即使全量重算 qfq + features，增量也在 **1–2 GB** 量级，D 盘 36G 可用绰绰有余。
  （⚠️ 我曾担心"重建会写满 36G"，**实测后不成立，特此更正**。）
- **两盘紧张与本项目数据无关** —— 本会话对磁盘的**唯一显著贡献是回收站**（见 §三）。

**两盘现状**：C: 301G / 已用 277G / **可用 24G（93%）**；D: 652G / 已用 617G / **可用 36G（95%）**。

---

## 二、C 盘占用实测（>0.3 GB 的目录）

| 目录 | 大小 | 文件数 | 性质 |
|---|---|---|---|
| `AppData\Local\pip` | **8.06 GB** | 5,418 | pip 下载缓存（**可安全清空**） |
| `C:\$Recycle.Bin` | **6.33 GB** | **101,301** | **回收站**（本会话删除量激增） |
| `AppData\Local\Google` | 6.31 GB | 15,343 | Chrome 等（非本项目） |
| `AppData\Local\Programs` | 4.90 GB | 82,509 | 已安装程序（非本项目） |
| `AppData\Local\JetBrains` | 4.55 GB | 54,452 | IDE（非本项目） |
| `AppData\Local\hermes` | 4.31 GB | 142,387 | 本机 agent 运行时 |
| `AppData\Local\Microsoft` | 3.81 GB | 20,708 | 系统组件 |
| `AppData\Local\npm-cache` | 2.86 GB | 10,877 | npm 缓存（本会话装 vitest 有贡献） |
| `AppData\Local\Doubao` | 2.53 GB | 11,055 | 非本项目 |
| `AppData\Local\ms-playwright` | 1.84 GB | 1,621 | 浏览器自动化（**本机不可用**，可卸） |
| `AppData\Local\uv` | 1.51 GB | 49,061 | uv 托管的 Python |

> 另有 `%TEMP%`（9,144 个条目）与 `pytest-of-HY`（**仅 0.01 GB / 2,595 文件**，**已排除**）。

---

## 三、🔴 根因：safe-delete 守卫把删除「移入回收站」，而流水线删除量极大

**D 盘回收站实测：`D:\$Recycle.Bin` = 8.2 GB / 510,850 文件**（其中某用户 SID 下 451,684 个条目）。
抽样文件名与命中数：

| 抽样 | 命中数 | 含义 |
|---|---|---|
| `$R0006DI.rwlock` | **105,154** | 数据流水线的**分区写锁**（每写一个分区就删一个） |
| `$R....parquet` | **8,446** | 被覆盖/截断的**数据分区** |
| `$R000A95.tmpgnbwlc` | — | 原子写的**临时文件** |
| `$R000HZP.mts` / `$R000RGD.js` | — | 前端构建的**旧产物** |
| `$R000SIT.db-wal` | — | pytest 的 **SQLite WAL** |

**机制**：宿主 safe-delete 守卫的设计就是 **"trash, not delete"** —— 每一次 `unlink` 都被改成
「移入回收站」。而数据流水线是**写密集 + 删除密集**的：每写一个分区就创建并删除一个 `.rwlock`，
再加上原子写的临时文件、被覆盖的 parquet、构建旧产物、pytest 临时库 ⇒ 这些**全部堆进回收站**，
且**从不自动清空**。

**为什么"突然"**：本会话跑了 **3 轮全市场抓取**（4,260+ 标的 × 5 年 × 2 口径）
+ **2 次全量 pytest** + 多次前端构建 ⇒ 删除量激增 ⇒ 回收站暴涨。

**两盘回收站合计：14.5 GB / 61.2 万个文件。**

---

## 四、清理执行记录（**2026-10-02 19:50 起，用户已明确确认**）

### P0 · 清空两盘回收站 —— 执行中

用户明确确认后执行 `Clear-RecycleBin -DriveLetter C/D -Force`。前后状态：

| 盘 | 清理前可用 | 清理后可用 | 说明 |
|---|---|---|---|
| **C:** | **23.0 GB** | **29 GB** | ✅ 已基本清空（+6 GB） |
| **D:** | 35.2 GB | 36 GB（进行中） | ⏳ 45 万条目仍在删除 |

⚠️ **实测要点**：
- **删除很慢**：61 万个条目 + 等量的 `$I` 元数据文件 ⇒ **约 120 万次文件删除**；
  C 盘 10 万条目约 6 分钟，D 盘 45 万条目预计 **~25 分钟**（且磁盘 95% 满会进一步拖慢）。
- **抓取与清理并发**：清空期间第 2 轮抓取仍在跑，**持续往回收站写入**
  （实测 D 盘条目一度 451,684 → 455,141，+3,457）⇒ 两者是**此消彼长**的关系。
  **结论：清空回收站应在长跑任务结束后进行**，否则会互相拖慢。

### P1 / P2 · 未执行（等进一步确认）

`pip` 缓存（8.06 GB）/ `npm` 缓存（2.86 GB）/ `ms-playwright`（1.84 GB）尚未清理；
长跑 `CODEBUDDY_SAFE_DELETE_ENABLED=0`（防复发）尚未启用。

### P1 · 清理「可安全重建」的缓存 → 可回收 **≈12.7 GB**（待确认）

| 目标 | 大小 | 命令 |
|---|---|---|
| pip 缓存 | 8.06 GB | `pip cache purge`（或删 `AppData\Local\pip\cache`） |
| npm 缓存 | 2.86 GB | `npm cache clean --force` |
| playwright 浏览器 | 1.84 GB | 见 §六 的"精简方案"（**不建议全删**） |

#### 🔬 全清"会不会有影响" —— 实测证据（2026-10-02）

| 检查 | 结果 | 含义 |
|---|---|---|
| venv 已安装包 | **152 个**（`site-packages` 304 条目） | 包**在 venv 里**，不在缓存里 |
| `pip check` | **No broken requirements found** | venv 依赖**一致无破损** |
| 缓存里的「本地构建 wheel」340 MB（含 `pyspark` 318 MB） | **`pyspark` / `jieba` / `jax` 都不在 venv 的 site-packages 里** | ⇒ 那是**历史遗留、当前根本不需要** |
| 那 18 个 wheel 的类型 | 全部 `py3-none-any` / `py2.py3-none-any` | **纯 Python** ⇒ 即便将来要用，重建**不需编译器** |
| **PyPI 可达性** | **HTTP 200** ✅ | 将来 `pip install` 能重新下载 |
| `frontend/node_modules` | vitest / react / @testing-library **都在** ✅ | 缓存与已装包无关 |
| **npm registry 可达性** | **PONG** ✅ | 将来 `npm install` 能重新下载 |

**结论：全清对当前功能零影响。**

⚠️ **唯一真实的代价**：失去「**离线重装**」的退路 —— 若将来 PyPI / npm 变得不可达，
缓存本可作为重新安装的兜底。鉴于两者**实测均可达**、且项目已提交
`backend/requirements*.txt` 与 `frontend/package-lock.json`（在线重装路径完整），**该风险很低**。

> **可选保险**：清之前先导出一份精确快照（`pip freeze > docs/pip-freeze-2026-10-02.txt`，
> 约 10 KB），把"当前到底装了什么版本"钉死 —— 代价近乎为零。

> ⚠️ 这两项都是**纯缓存**，删除后下次使用会自动重建（只是首次慢一点）。**不动 `uv`**（venv 依赖它）。

### P2 · 防止复发（**关键**，待确认）

长跑批处理时给流水线设 **`CODEBUDDY_SAFE_DELETE_ENABLED=0`** ⇒ 其临时文件**直接永久删除**，
不再把 10 万+ 个锁文件塞进回收站。
⚠️ 代价：**该进程的删除保护被关闭**（不再进回收站）。仅建议对"只删自己产物"的批处理使用。
（此前我出于保守未启用它；现在有了实测依据，值得重新权衡。）

### P3 · 长期

- D 盘已 **95%**，`data/parquet` 有 39,944 个文件；继续扩容前先规划容量。
- **清空回收站应在长跑任务结束后做**（本次实测：与抓取并发会互相拖慢）。
- 不建议把「自动清空回收站」写进流水线 —— 破坏性操作应保持人工确认。

---

## 五、复现命令（只读）

```bash
df -h | grep -E "^[CD]:"                       # 两盘剩余
# 目录体积（PowerShell，递归枚举较慢）
Get-ChildItem 'C:\Users\HY\AppData\Local' -Directory | ForEach-Object {
  $m = Get-ChildItem $_.FullName -Recurse -Force -File -EA SilentlyContinue |
       Measure-Object Length -Sum
  "{0}`t{1:N2} GB" -f $_.Name, ($m.Sum/1GB)
}
# 回收站内容抽样
ls "/d/\$Recycle.Bin"/*/ | grep "^\$R" | head
```
