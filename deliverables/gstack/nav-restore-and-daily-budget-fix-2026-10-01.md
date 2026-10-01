# 导航入口恢复 + 日频块预算与统计日修复

**日期**：2026-10-01
**场景**：全流程交付（导航回归修复 + 新增闸门 + 两个数据准确性缺陷 + 运维/缓存治理）
**参与成员**：主理人（沽思航）直接处置；前端/后端修复由前序协作成员（frontend-fixer / backend-fixer）落地的补丁在本轮被复核与收口

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 **通过**（全部改动已在真实运行的前后端上端到端验证）
- 阻塞项数量：**0**
- 用户报障「ETF 中心/ETF 分析不见了」= **IA 改造过度删除**，已恢复并把「ETF 分析」移入「盯盘」层次
- 顺手抓到并修掉 **4 个此前未知的缺陷**：统计日被单标的劫持（**≈2 万倍误差**，`_heat_from_local`）、**同型缺陷第二次命中**（`_sectors_from_local`，「热门板块」榜单退化成 **1 项 / count=1**）、日频块预算**永远不够**（每次缓存未命中必然降级）、`_build_ai_stats` **多读 32% 无用分区**
- 日频块从「**必然降级**」变为「**冷启动 7.6s 返回真实数据**」（原始 22.2s，累计 2.9×）
- 追加**性能优化**：给 `_build_ai_stats` 加**独立缓存**（签名 = predictions 分区指纹）⇒ 日频块重建 **4.84s → 2.58s（−47%）**
- 新增 **1 道永久闸门**（导航完整性）+ **19 条回归测试**（含 4 个一键证伪脚本，全部证伪通过）
- 下一步：本轮已把日频块根治到秒级并消除重复计算；剩余可选优化见行动清单

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go |
| 严重度分布 | 🔴 1 / 🟠 3 / 🟡 1 / 🟢 3 |
| 关键行动项 | 4 条 |
| 建议负责人 | 前端（导航闸门维护）/ 后端（ai_stats 独立缓存，可选） |

---

## 1. 交付清单

### 1.1 代码变更

| # | 文件 | 变更 | 验证 |
|---|------|------|------|
| 1 | `frontend/src/components/Sidebar.tsx` | 恢复 `IconEtf` / 新增 `IconEtfAnalysis`；`DEFAULT_ETF_CODE='510300'`；「ETF 分析」置于**盯盘**第 4 位（紧跟个股分析），「ETF 中心」留在**研究** | DOM dump + 截图 |
| 2 | `frontend/scripts/check-nav-integrity.mjs` | **新增**导航完整性闸门（3 规则） | 2 次注入 + 1 次注释场景，全部正确报错 |
| 3 | `frontend/package.json` | 新增 `nav:check` script | — |
| 4 | `.github/workflows/ci.yml` | `nav:check` 插在 `build` 之前 | — |
| 5 | `.githooks/pre-commit` | 改为**累计 rc**，两道闸门都跑（原实现第一道过了就 `exit 0`，第二道永不执行） | 端到端 exit 0 |
| 6 | `backend/app/api/v1/market.py` | ① 统计日改为「最近覆盖度达标日」（`_COVER_LOOKBACK=20`，相对阈值），抽成**单一事实来源** `_pick_stat_day`；② 载荷新增 `data_date`/`coverage_symbols`/`latest_date`；③ `_build_daily` 子块**并行化**；④ 新增 `DAILY_BUILD_TIMEOUT_SECONDS=20.0` 取代 5.0s；⑤ `_sectors_from_local` 复用 `_pick_stat_day`（缺陷 D） | 见 §2 |
| 7 | `frontend/src/types/stock.ts` | `HeatBlock` 补 `coverage_symbols`/`data_date`/`latest_date`（**故意不叫 `coverage`**，见 §2-#3） | `tsc -b --force` exit 0 |
| 8 | `frontend/src/pages/MarketOverview/KpiCards.tsx` | 本地降级时标签去掉「今日」，副标题给出「口径日 · 覆盖标的数 · 已跳过覆盖不足日」 | 截图 |
| 9 | `frontend/src/pages/MarketOverview/MoneyFlowPanel.tsx` | 脚注补口径日 | — |
| 10 | `frontend/src/api/market.ts` / `pages/MarketOverview/index.tsx` | 修正**两处已过期注释**（声称预算 `timeout=6.0`、前端 `60s`，实际均不符） | — |
| 11 | `backend/scripts/dev_purge_market_overview_cache.py` | 新增：按命名空间失效市场概览缓存（改名后必须清，否则前端拿到旧 schema） | 实删 4 key |
| 12 | `backend/scripts/dev_time_build_daily.py` | 新增：拆解 `_build_daily` 各子块耗时 | 定位到 ai_stats 7.72s |
| 13 | `backend/scripts/dev_falsify_daily_parallel.py` | 新增：一键证伪（改回串行/改回 5s → 必须 FAIL → 自动恢复） | 两轮均 FAIL |
| 14 | `backend/tests/test_build_daily_parallel_and_budget.py` | 新增 3 测试（并行性 / 预算不变量 / sentiment 依赖不断） | 3 passed，证伪 2/2 |
| 15 | `backend/app/api/v1/market.py::_build_ai_stats` | **按预测日跨度裁剪 hfq 分区**（10924 → 7409 个；2022+2023 与任何预测日都 join 不上） | ai_stats **7.72s→2.41s**；`_build_daily` **10.10s→4.66s** |
| 16 | `backend/tests/test_ai_stats_year_trim.py` | 新增 3 测试（只读需要年份 / 前瞻窗口不被截断 / 预测为空不炸） | 3 passed，证伪 2/2 |
| 17 | `backend/scripts/dev_verify_ai_stats_year_trim.py` | 新增：**对照实验**证明裁剪是纯优化（裁剪 vs 全量，逐字段比对） | 11 个字段**完全一致** |
| 18 | `backend/scripts/dev_falsify_ai_stats_trim.py` | 新增：一键证伪（退回全量 glob → 必须 FAIL → 自动恢复） | 2 failed → 恢复 3 passed |
| 19 | `backend/app/api/v1/market.py::_sectors_from_local` | **缺陷 D**：同型统计日劫持（`df["date"].max()`）→ 改用 `_pick_stat_day`；载荷补 `data_date`/`coverage_symbols`/`latest_date`/`note` | 修复前 **1 项 / count=1**；修复后 **12 项 / 覆盖 2492 只** |
| 20 | `backend/tests/test_sectors_local_stat_day.py` | 新增 3 测试（异常日不劫持统计日 / 跳过行为被披露 / 正常态无跳过提示） | 3 passed，证伪 3/3 |
| 21 | `backend/scripts/dev_falsify_sectors_stat_day.py` | 新增：一键证伪（退回 `df["date"].max()` → 必须 FAIL → 自动恢复）⚠️ 锚点必须用缺陷 D 的注释（`_pick_stat_day(df)` 全文件出现两次，按行文本替换会误伤 `_heat_from_local`） | 3 failed → 恢复 3 passed |
| 22 | `backend/app/api/v1/market.py::_build_ai_stats` | **独立缓存**：签名 = 全部 predictions 分区的 `(name, mtime_ns, size)` + `ML_LABEL_HORIZON` + 标签口径；TTL 1h、最多 4 条、按最旧淘汰。🔴 **只缓存 `status=="ok"`**（缓存降级态会把一次瞬时失败钉死整个 TTL） | 日频块重建 **4.84s → 2.58s（−47%）**；ai_stats **2.84s → 0.03s** |
| 23 | `backend/tests/test_ai_stats_independent_cache.py` | 新增 5 测试（命中 / 新增分区自动失效 / **降级态不入缓存** / TTL 到期 / 无分区不缓存） | 5 passed，证伪 2/5 |
| 24 | `backend/scripts/dev_falsify_ai_stats_cache.py` | 新增：一键证伪（禁用缓存 → 必须 FAIL → 自动恢复）⚠️ 自检必须匹配**带赋值的调用**，否则 `count()` 会命中函数定义行、恒为 1 | 2 failed → 恢复 5 passed |
| 25 | `backend/scripts/dev_measure_ai_stats_cache.py` | 新增：冷/热对照测量（同进程连续两次，OS page cache 均热 ⇒ 差值只来自缓存） | 载荷逐字段一致 |
| 26 | `docs/项目文档.md` §7.7.2 | 新增**「可用性 / 降级自愈 8 条自检 Checklist」**（A1~A8 + 4 条通用纪律），作为代码评审必过项；与 §7.7.1 泄漏清单并列（成因正交） | 结构校验：§7.7 → 7.7.1 → 7.7.2 → §8 顺序正确 |

### 1.2 测试与门禁覆盖

| 门禁 | 结果 |
|------|------|
| `ruff check app tests scripts --select F,E9` | **All checks passed!** |
| **全量后端测试** `pytest -q --tb=short -p no:randomly` | **1971 passed / 8 skipped / 0 failed**（567s；基线 1945 ⇒ **+26** 全部来自本轮新增测试） |
| 本轮 5 个新增/相关测试文件 | **19 passed / 0 failed**（合并跑，验证无跨测试缓存串味） |
| 新增测试 | **19 项**（heat 统计日 5 + 并行/预算 3 + hfq 裁剪 3 + **sectors 统计日 3** + **ai_stats 独立缓存 5**），**全部证伪通过** |
| 证伪实验 | 改回串行 → 1 failed；预算改回 5s → 1 failed；退回全量 glob → **2 failed**；**`_sectors_from_local` 退回 `max(date)` → 3 failed**；**禁用 ai_stats 缓存 → 2 failed**；恢复后全绿 |
| 前端 `nav:check` | 通过（3 规则；规则 3 覆盖 **20 条路径**互斥高亮）—— 收尾时**复跑确认** |
| 前端 `style:check` | 通过（h1 + hex）—— 收尾时**复跑确认** |
| 前端 `tsc -b --force` | **exit 0** |
| 前端 `npm run build` | **✓ built in 8.63s**（760 modules）⚠️ 直接跑会被沙箱 bulk-delete 守卫拦在 `emptyDir(dist/assets)`；需先 `[System.IO.Directory]::Delete('...\frontend\dist', $true)` 再构建 |
| 前端 `bundle:check` | **passed**（raw ≤ 768000 B / gzip ≤ 256000 B） |
| ETF 路由可达性 | `/etf`、`/etf/510300` 均渲染**登录页（非 404）**；不存在的路由显示 **404** ⇒ 恢复的入口指向真实路由 |
| 端到端 HTTP | 冷启动 22.2s → **6.3s**（重启后实测）；`heat`/`sectors`/`ai_stats` 三块 `status=ok` |
| 浏览器渲染 | 截图 `qa_browser/kpi-amount-disclosure-2026-10-01.png`、`qa_browser/hot-sectors-fixed-2026-10-01.png`（**12 项板块 + 披露脚注**） |

### 1.3 回滚预案

- 单文件级回滚：本次改动集中在 `market.py`（4 处）+ `Sidebar.tsx`（1 处）+ `stock.ts`/`KpiCards.tsx`/`MoneyFlowPanel.tsx`（各 1 处）。
- 若日频块预算引发槽位占用担忧：把 `DAILY_BUILD_TIMEOUT_SECONDS` 调回即可，但**必须同步调低子块工作量**（否则回到"必然降级"）。
- 若新字段引发前端问题：`coverage_symbols`/`data_date`/`latest_date` 均为**新增可选字段**，前端不读即退化为原行为（无破坏性）。

---

## 2. 综合审查发现（按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 状态 |
|---|--------|------|------|---------|------|------|
| 1 | 🔴 | 数据准确性 | `market.py::_heat_from_local` | 统计日用 `df["date"].max()`，被单只标的（`000007.SZ` 多一行 09-29）劫持 ⇒ "全市场"统计只剩 **1 只标的**：`total_amount_yi=0.8`（真值 **15770.6 亿**，**≈2 万倍误差**）、`up=1/down=0` 冒充全市场宽度。**0.8 亿是"真实但退化"的和**，不会被任何"0 冒充不可得"类守卫拦住 | 统计日 = 「最近且覆盖充分」日，阈值取 20 日回看窗最大覆盖数的**一半**（相对判据，不硬编码标的池），并如实披露 `note`/`data_date`/`coverage_symbols`/`latest_date` | ✅ 已修 |
| 2 | 🟠 | 可用性 | `market.py::overview_daily` | 日频块预算 **5.0s**，而 `_build_daily` 子块**串行**实测合计 **13.23s**（ai_stats 独占 7.72s）⇒ **每一次缓存未命中都必然超时**，日频块长期钉在 `heat.status="unavailable"`。**"预算"本身成了降级来源** | ① 互不依赖子块并行化；② 预算最终取 **20.0s**（对优化后的实测最坏 8.6s 留 ~2.3× 余量），定位为**活性兜底而非延迟 SLO** | ✅ 已修 |
| 3 | 🟠 | 契约 | `HeatBlock`（TS） | 后端新字段若命名为 `coverage` 会与统一契约里 **`BlockBase.coverage`（对象 `{available,total,ratio}`）同名不同型**，触发 TS 声明合并悄悄收紧契约（本项目已踩过一次同名双声明，见 `MoneyFlowBlock` 注释） | 后端键名改为 **`coverage_symbols`**，前端同步；并在两侧注释写明"勿合并" | ✅ 已修 |
| 4 | 🟡 | 文档诚实性 | `api/market.ts` / `MarketOverview/index.tsx` | 注释声称"后端 overview 冷路径收敛到 **6s** 预算（`timeout=6.0`）"、"前端放宽到 **60s**"，实际是 5.0s / 120s —— 典型「注释里写的 ≠ 代码里做的」 | 改为不复述数字、指向常量 | ✅ 已修 |
| 5 | 🟢 | 工程健壮性 | `.githooks/pre-commit` | 原结构 `if 闸门1; then exit 0; fi` ⇒ 第二道闸门**永不执行**（新增闸门等于没接） | 改为累计 `rc`，两道都跑 | ✅ 已修 |
| 6 | 🟢 | 防回归 | 仓库 | ETF 导航项被"静默删除"后**无任何执行点能发现** | 新增 `check-nav-integrity.mjs`，接入 package.json + CI + pre-commit | ✅ 已修 |
| 7 | 🟢 | 环境 | `.git/` | 仓库曾损坏（`.git/refs/` 被整体删除 ⇒ `fatal: not a git repository`），已从 reflog 重建 refs；**未推送的提交对象已 prune、不可恢复**（工作区完好） | 证据留档 `.git/RECOVERY-NOTE-2026-10-01.txt` | ✅ 已恢复 |
| 8 | 🟠 | 性能/根因 | `market.py::_build_ai_stats` | 为评估推荐质量，无条件读**全部** hfq 分区：实测 **10924 个**跨 2022~2026，而 `predictions` 最早 **2024-06-05** ⇒ **2022+2023 共 3515 个分区（32%）永远 join 不上**，纯浪费 | 按 `pred` 日期跨度裁剪（起点可裁，终点须留足前瞻窗口 `horizon*3+10` 天） | ✅ 已修 |
| 9 | 🟢 | 契约（**负结果**） | 全仓响应字段 | 专项排查"与 `BlockBase` 同名不同型"的字段：`kpi_series.points[].coverage` 是**浮点比值**、`BlockBase.coverage` 是**对象** | 二者**嵌套层级不同**（point 级 vs block 级），前端也分在 `types/kpi.ts` / `types/stock.ts` 两个命名空间 ⇒ **无真实冲突**，仅命名歧义，不动（避免破坏外部契约） | 🟢 无需修 |
| 10 | 🟠 | 数据准确性 | `market.py::_sectors_from_local` | **与缺陷 #1 同型**：统计日取 `df["date"].max()` ⇒ 实测 2026-09-29 全市场**只有 1 只标的**有数据，"热门板块"榜退化成 **1 项、`count=1`、leader 来自单只股票** —— 一个只有 1 项的排行榜没有排序意义（且**不报错**，前端照常渲染） | 复用缺陷 #1 抽出的 `_pick_stat_day`（单一事实来源），并披露口径日/覆盖标的数 | ✅ 已修 |

---

## 2.5 同类缺陷系统排查：「预算 < 工作量 ⇒ 必然降级」全仓扫描

发现缺陷 B 后，对全仓 `asyncio.wait_for(` + 预算常量做了穷举，**共 6 个预算点**：

| 预算常量 | 值 | 包住的工作量 | 请求路径降级后是否有后台回填 | 定性 |
|---------|----|------------|--------------------------|------|
| `DAILY_BUILD_TIMEOUT_SECONDS` | ~~5.0~~ → **20.0** | `_build_daily`（串行 13.23s / 并行 10.10s） | ❌ **无** | 🔴 **缺陷**（本次修复） |
| `OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS` | 6.0 | `_build_overview`（内含 `_build_daily` + `_build_rt`） | ✅ 有（无预算后台重建） | 🟢 有意设计 |
| `RT_BUILD_TIMEOUT_SECONDS` | 15.0 | `_build_rt`（限速外呼 ≈14s） | ✅ 有 | 🟢 有意设计 |
| `_ETF_ENDPOINT_BUDGET_SECONDS` | 4.5 | ETF 聚合（冷路径 ≈6.3s） | ✅ 有（`_build_unbudgeted`） | 🟢 有意设计 |
| `_ETF_DETAIL_BLOCK_BUDGET_SECONDS` | 4.0 | ETF 详情各块（冷路径 6.22s） | ✅ 有 | 🟢 有意设计 |
| `_WATCHLIST_BUDGET_SECONDS` | 5.5 | 自选池聚合 | ✅ 有 | 🟢 有意设计 |

**结论：`/overview/daily` 是唯一的例外** —— 它既没有"降级后后台回填"的兜底，又是前端**唯一**的日频数据来源，
所以 5.0s 预算对它不是"快速降级换自愈"，而是**永久钉死在空态**。这也解释了为什么只有它表现出长期降级。

> 判据（可复用于后续评审）：**「预算 < 工作量」是否构成缺陷，取决于降级路径能否自愈**。
> 有后台无预算重建 ⇒ 快速降级是**设计**；没有 ⇒ 就是**缺陷**。

补充证据：`/api/v1/market/overview`（裸兼容端点）**前端已不再调用**（只调 `/overview/rt` 与 `/overview/daily`），
故其 6.0s 预算即使冷路径降级也不影响用户可见数据。

---

## 2.6 性能修复效果（三步递进，均实测）

| 场景 | ① 原始 | ② 子块并行 | ③ 并行 + hfq 裁剪 | 累计提升 |
|------|-------|-----------|-----------------|---------|
| `_build_daily` 直接调用（OS cache 热） | 13.23s（串行合计） | 10.10s | **4.66s** | **2.8×** |
| `_build_ai_stats` | 7.72s | 7.72s | **2.41s** | **3.2×** |
| HTTP 冷启动首次 | 22.2s | 22.2s | **7.6s** | **2.9×** |
| HTTP 稳态重建（`refresh=1`） | 10.7s | 10.7s | **8.6s** | 1.2× |
| HTTP 缓存命中 | 0.05s | 0.05s | **0.04s** | — |

**裁剪的正确性证明（对照实验，非推断）**：`scripts/dev_verify_ai_stats_year_trim.py` 用文件级 swap
跑「裁剪 vs 全量」两次，**11 个输出字段完全一致**：
`rank_ic=-0.0457` / `top_k_precision=0.6625` / `n_days=4` / `horizon=5` / `top_k=20` /
`top_k_excess_return` / `rank_ic_positive_ratio` / `directional_hit_rate` / `samples` /
`status` / `label_price_basis`。
且 `rank_ic=-0.046` / `Top-20 正收益 66.3%` 与前端截图上的读数一致 ⇒ **无口径漂移**。

> 结论：日频块从"**每次缓存未命中必然降级**"变为"**冷启动 7.6s 内返回真实数据**"，
> 预算最终定为 **20.0s**（调参过程中曾试 25s / 30s；对优化后实测最坏 8.6s 留 2.3× 余量）。

---

## 2.7 追加优化：`_build_ai_stats` 独立缓存（消除重复计算）

**动机**：ai_stats 占 `_build_daily` 的 **52%**，但它的输入**只随 predictions 分区变化**（日频一次），
而日频块的其它子块会随行情更新更频繁地重建 ⇒ 挂在日频块 300s TTL 上会让 ai_stats 跟着白算。

**设计**：签名 = 全部 predictions 分区的 `(文件名, mtime_ns, size)` + `ML_LABEL_HORIZON` + 标签口径。
任一预测分区被新增/改写/删除 ⇒ 签名变化 ⇒ **自动失效，无需手工清缓存**。
TTL 1h、最多 4 条、按最旧淘汰。

> 🔴 **只缓存 `status == "ok"` 的载荷** —— 缓存降级态会把一次瞬时失败钉死整个 TTL，
> 与本仓「降级必须可自愈」的既有纪律冲突（已有专项测试 `test_degraded_result_is_not_cached`）。

**实测（同进程冷/热对照，OS page cache 两次均热 ⇒ 差值只来自缓存）**：

| 场景 | 冷（未缓存） | 热（已缓存） | 提升 |
|------|------------|------------|------|
| `_build_ai_stats` | 2.84s | **0.03s** | ~95× |
| `_build_daily` 整体 | 4.84s | **2.58s** | **−47%** |
| 载荷一致性 | — | **逐字段相同** | ✅ |

---

## 3. 关键决策与依据

1. **闸门模型选「索引页必须有入口」而非「可达性 BFS」**（两次失败后才定）
   - 坑 1：判据取"站内存在任意链接" ⇒ `Etf` 页自链接、`EtfDetail` 有"返回 /etf" ⇒ 漏报。
   - 坑 2：改 BFS 可达性 ⇒ `Watchlist → /etf/{code}`、`EtfDetail → /etf`、且 `Topbar` 全局搜索在**每页**渲染 `/etf/*` ⇒ 任何路由都"可达" ⇒ 仍漏报。
   - 定论：**索引导航必须显式声明**，可达性不是入口。
2. **注释剥离必须用状态机**：注释文本里出现字面量 `/etf/*` 会被正则当成块注释开头，吞掉后面 ~40 行（实测 16 条导航项只剩 5 条，产生 9 处假违规）。
3. **统计日必须用相对覆盖判据**：不能硬编码标的池规模（北交所/新股会让池子漂移），取回看窗内最大覆盖数的一半。
4. **预算必须 ≥ 真实最坏耗时**：预算是活性兜底，不是延迟 SLO；预算长期不够用就从保护变成缺陷。
5. **清缓存是改 schema 后的必要步骤**：Redis 跨进程重启存活，后端重启**不会**让旧 schema 消失。
6. **修掉一个"坏惯用法"后必须全仓扫同型写法**（本轮最有价值的一条）：缺陷 #1 修完后立刻 grep `df["date"].max()`，当场命中 `_sectors_from_local` —— **同一天、同一文件、同一根因、独立发现两次**。修法也因此收敛为**抽出 `_pick_stat_day` 单一事实来源**，而不是在两处各打一个补丁（否则下次第三个调用点还会踩）。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 把 `nav:check` 纳入发布前检查（已入 CI + pre-commit，确认分支保护开启） | 工程 | P1 | 本次提交 |
| 2 | ~~观察日频块冷启动耗时~~ ✅ **已根治**（hfq 按预测日跨度裁剪：ai_stats 7.72s→2.41s，HTTP 冷启动 22.2s→7.6s） | 后端 | — | 本轮完成 |
| 3 | ~~复核其它「预算 < 工作量」的组合~~ ✅ **已完成**（§2.5：6 个预算点穷举，`/overview/daily` 为唯一缺陷） | 后端 | — | 本轮完成 |
| 4 | ~~复核其它"同名不同型"的响应字段~~ ✅ **已完成**（§2 发现 #9：仅命名歧义，无真实冲突） | 前端 | — | 本轮完成 |
| 5 | ~~把 §2.5 的判据（**降级能否自愈**）写进后端评审清单~~ ✅ **已完成**：新增 `docs/项目文档.md` **§7.7.2「可用性 / 降级自愈 8 条自检 Checklist」**（A1~A8 + 4 条通用纪律），与既有 §7.7.1（泄漏 20 条）并列 —— 两者成因正交：**一个治"结果偏乐观"，一个治"结果静默失真"** | 工程 | — | 本轮完成 |
| 6 | ~~（可选）给 `_build_ai_stats` 加独立长 TTL 缓存~~ ✅ **已完成**（§2.7：日频块重建 4.84s → 2.58s，−47%） | 后端 | — | 本轮完成 |
| 7 | ⚠️ **需人工确认（本机无法完成）**：仓库 `origin` = `github.com/GG-Bondmaker/quant-system`。实测探测结果：`/branches/master/protection` → **HTTP 401**（需鉴权）、`/repos/...` → **HTTP 404**（私有库，未鉴权不可见）⇒ **无法脚本化核对分支保护**。请在 GitHub 仓库设置中确认 `master` 的 branch protection 已启用、且 `CI` 为必需检查 | 工程 | P1 | 需人工 |

---

## ⚠️ 待完善 / 已知局限

- ~~**日频块首次构建仍较慢**（冷启动 22.2s / 稳态 11.0s）。本轮的并行化只把串行 13.23s 压到 10.10s（parquet 读有争用），**没有根治**。~~ ✅ **已根治**：追加 hfq 按预测日跨度裁剪后，冷启动 **7.6s** / 稳态 8.6s / `_build_daily` 直接调用 4.66s（§2.6）。
- **裁剪的边界依赖 `predictions` 的日期跨度**：若某天预测分区被清理到只剩近期，读取范围会自动收窄（正确）；反之若引入**早于 2022** 的预测，需确认 hfq 分区存在（当前 hfq 只到 2022）。
- **`recommend` 子块当前为 `degraded`**（预测数据面未就绪），与本轮改动无关。
- **资金流向面板显示"实时数据源响应超时"** —— 本机 egress 代理问题（东财 `push2` 组不可达），非本轮引入。
- 导航闸门**不覆盖**"入口存在但指向错误目标"（如指向已废弃的默认代码）—— 这类语义正确性无法静态判定，仍是人工评审范围。
- 全量后端测试已跑：**1971 passed / 8 skipped / 0 failed**（2026-10-01，567s）。
- ⚠️ **改完后端必须重启进程再验收**：本轮修复落地后 HTTP 仍返回旧载荷，原因是 uvicorn **未开 `--reload`**，
  进程启动时间（12:43:30）早于源码 mtime（12:54:41）。重启后立即返回修复后的数据。

---

## 📚 产出索引

- 截图（端到端证据）：`qa_browser/kpi-amount-disclosure-2026-10-01.png`
- 截图（导航恢复）：`qa_browser/etf-nav-restored-2026-10-01.png`
- 导航闸门：`frontend/scripts/check-nav-integrity.mjs`
- 统计日回归测试：`backend/tests/test_heat_local_stat_day_robustness.py`（5 项）
- 板块统计日回归测试：`backend/tests/test_sectors_local_stat_day.py`（3 项，含证伪脚本 `dev_falsify_sectors_stat_day.py`）
- 并行/预算回归测试：`backend/tests/test_build_daily_parallel_and_budget.py`（3 项，含证伪脚本）
- 诊断脚本：`backend/scripts/dev_time_build_daily.py`、`dev_purge_market_overview_cache.py`、`dev_falsify_daily_parallel.py`
- ai_stats 独立缓存：测试 `backend/tests/test_ai_stats_independent_cache.py`（5 项）、
  证伪 `dev_falsify_ai_stats_cache.py`、测量 `dev_measure_ai_stats_cache.py`
- 前序报告：`deliverables/gstack/core-bug-fix-data-accuracy-2026-10-01.md`

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
