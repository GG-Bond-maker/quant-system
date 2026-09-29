# 数据中心页「请求超时，请稍后重试」根因定位与修复

**日期**：2026-09-29
**场景**：调试复盘（根因定位 → 修复实施 → 独立验证）
**参与成员**：排障手（investigator） + 独立验证者（verifier） + 主理人汇编
**触发**：用户在数据中心页看到「文本数据（公告情绪 · FinLLM 前置）」面板全部统计显示 `—`，下方红条 `请求超时，请稍后重试`

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 **修复成立**（已通过独立验证 + 浏览器端到端确认）
- 阻塞项数量：**0**（全部已修复并验证；2 项环境/文档类待办不阻断）
- 真正元凶**不是**「文本数据」接口，而是同一面板里的 **`GET /datacenter/mirror/status`**：前端只给 45s，后端实测冷扫 **57.79s**（验证者）/ **73.91s**（主理人），必然超时。
- 叠加缺陷：`TextDataPanel.load()` 用 `Promise.all`，mirror 失败会把**已成功**的 `textStatus`（0.01s）结果一并丢弃 ⇒ 整面板渲染 `—`。这解释了截图里"全 `—` + 红条"的组合。
- 顺带查出一个**独立的 500 bug**：`POST /datacenter/mirror/rebuild` 响应模型标注错误，**每次调用恒 HTTP 500**（尽管重建本身成功）。
- 下一步：提交本次改动（未提交，留在工作区）；处理数据盘 93% 水位。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go |
| 严重度分布 | 🔴 2 / 🟠 3 / 🟡 5 / 🟢 2（共 12 项：本次已修复 9 / 待办 3） |
| 关键行动项 | 5 条 |
| 改动规模 | 6 个文件（4 源 + 2 测试），185 insertions / 36 deletions |
| 回归基线 | `pytest -m "not network"` → **1903 passed / 0 failed**（基线 1901，+2 无回归） |
| 前端校验 | `tsc --noEmit` exit 0；`vite build` exit 0（755 模块） |
| 建议负责人 | 后端 owner（缓存/预热）+ 前端 owner（预算/容错）；运维处理磁盘水位 |

---

## 1. 各成员核心结论

### 🔧 排障手（investigator）— 根因定位 + 修复实施

- **核心判断**：根因是 **`mirrorStatus` 前端超时（45s）< 后端实耗（~50s）**，超时请求不是 `text/*`，而是 `mirror/status`。链路：数据中心页挂载 → `TextDataPanel.load()` → `Promise.all([textStatus(15s), mirrorStatus(45s)])` → `GET /datacenter/mirror/status` → `asyncio.to_thread(_cached(key,120,mirror_status))` → `_scan_source` ×3（32,764 个 parquet 读 `date` 列，~50s）→ 45s 前端放弃 → `ECONNABORTED` → 红条；`Promise.all` 被拒 ⇒ `setStatus` 不执行 ⇒ 统计条全 `—`。
- **分层归因**：① 前端 45s 按注释"实测 21~23s"标定，已失效；② 后端顺序扫 3 集共 32,764 文件（16.4~18.4s/集）；③ **缓存 TTL 失配**：`mirror_status` 硬编码 120s，而同类重扫的 `datasets`/`quality` 是 1800s ⇒ 冷扫过频；④ `Promise.all` 让一个超时拖垮整面板；⑤ 磁盘水位 93% 加剧 IO 慢。
- **排除项**：全局 timeout-guard 240s 未触发；`text_status`/`import`/`build-factor` 均毫秒级；fastapi 0.141.1 / starlette 1.7.0 与本 bug 无因果证据（纯磁盘扫描耗时）。
- **关键建议**：7 项修复全部落地（见第 2 节），并新增 `mirror/rebuild` 响应形状回归用例——该 bug 之所以漏网正是因为此前**零覆盖**。

### ✅ 独立验证者（verifier）— 以全新视角复验（未采信实施者结论）

- **核心判断**：**修复成立、可用**。9 项可证伪检查全部 PASS（1 项部分 FAIL，见第 2 节 #7），2 个次要问题均不阻断。
- **关键建议**：① `TextDataPanel.tsx:41` 陈旧注释（写 45s/60s，实际 120s/180s）；② `warm_datacenter_stats` 把 `storage_stats` 列为"被周期保温"属**言过其实**（其自身 TTL 仅 120s，1500s 循环保不住），功能无害但注释应更正。二者均已由主理人更正。
- **验证者主动披露的偏差**：`npm run build` 在沙箱内失败**仅**因 safe-delete 拦截 vite 清空 `dist/`，非代码错误；改用 `--outDir` 新目录跑通真实构建。这一区分很关键——避免把环境拦截误报成代码缺陷。

### 🧭 主理人（汇编）

- 独立复算并**交叉印证**了排障手的两组关键数：并发冷启动实测 `mirror_status` **62.34s** / `datasets` 31.19s / `quality` 39.48s / `overview` 2.07s / `text_status` 0.01s；并单独复测 `mirror_status` 冷算 **73.91s**，确认远超 45s。
- 复核了实施方 diff 的每一处（含 `allSettled` 是否破坏中断契约、预热是否真为顺序、TTL 上调是否有失效路径兜底），并**主动发现并补上**验证清单里未覆盖的一项：`Promise.all → allSettled` 后，中断路径不再依赖 `catch`，改为靠 `ctrl.signal.aborted` 前置守卫丢弃整轮结果——三条路径（卸载 / 打断上一轮 / 正常成功）均已确认正确。

---

## 2. 综合审查发现（去重合并后按严重度排序）

### 2.1 本次已修复

| # | 严重度 | 类别 | 位置 | 问题描述 | 处置 | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🔴 | 前端预算 | `frontend/src/api/datacenter.ts:96` | `mirrorStatus` 超时 45s < 实测冷扫 **57.79s**（验证者）/ **73.91s**（主理人）⇒ `ECONNABORTED` ⇒ 用户所见红条 | 改为 **120s**（< 服务端 240s 兜底，满足预算不变量）；注释更正（旧"21~23s"是孤立空载测量） | 排障手 + 验证者 |
| 2 | 🔴 | 后端响应模型 | `backend/app/api/v1/datacenter.py:1404` | `cs_mirror_rebuild` 标注 `APIResponse[dict]` 但返回 `ok(list)` ⇒ FastAPI `ResponseValidationError` ⇒ **每次调用恒 HTTP 500**（重建其实已成功） | 改为 `APIResponse[list]`；前端本就按数组消费 | 主理人（日志实证）+ 验证者 |
| 3 | 🟠 | 前端容错 | `frontend/src/pages/DataCenter/TextDataPanel.tsx:52` | `Promise.all` 使 mirror 失败**丢弃已成功的 `textStatus`** ⇒ 统计全 `—`（截图症状的直接成因） | 改为 `Promise.allSettled`，两面板各自落库、只报真正失败者（带面板名前缀） | 主理人 + 验证者 |
| 4 | 🟡 | 缓存 TTL 失配 | `backend/app/api/v1/datacenter.py:1393` | `mirror_status` TTL 硬编码 120s，兄弟端点 `datasets`/`quality` 为 1800s ⇒ 冷扫频率高 15× | 改用 `_TTL_DATASETS`(1800)，附**条件安全性**注释 | 排障手 |
| 5 | 🟡 | 前端预算 | `frontend/src/api/datacenter.ts:106` | `mirrorRebuild` 60s < 实测 ~51s/数据集 × 3 ≈ **150s** | 改为 **180s**（< 240s 兜底） | 排障手 |
| 6 | 🟡 | 架构（放大机制） | `backend/app/main.py:150` + `datacenter.py:1492` | 无预热 ⇒ 页面挂载并发触发 3 路全量扫描互抢磁盘，单请求被放大到 50~74s | 新增 `warm_datacenter_stats()`（**顺序**预热）+ 周期任务（1500s < 1800s TTL），受 `WARM_OVERVIEW_ON_STARTUP` 门控 | 排障手 |
| 7 | 🟡 | 注释准确性 | `TextDataPanel.tsx:41` / `datacenter.py:1492` docstring | ① L41 仍写"45s/60s"；② 预热 docstring 把 `storage_stats` 列为"被周期保温"，实际其 TTL 仅 120s 保不住 | 两处均已更正为准确表述（**仅注释，行为零变化**，故不影响已验证结论） | 验证者发现 → 主理人修正 |
| 8 | 🟢 | 可观测性 | `backend/app/api/v1/datacenter.py:1518` | 预热**成功路径零日志**，且 4 个被预热的 callee 自身都不打日志 ⇒ 运维**无法确认预热是否真的跑过**（主理人复核运行态时即撞上此盲区，只能靠 `WARM_OVERVIEW_ON_STARTUP` 开关间接推断） | 补 `logger.info("[datacenter] warm stats done in Xs")`，对齐 `market.warm_overview_cache` 既有做法 | 主理人 |
| 9 | 🟢 | 文档 | `backend/tests/test_resilient_loop.py` docstring | 站点清单自称"8 个站点"且不穷尽（缺 `_etf_overview_warmer`，也缺新增的 `_datacenter_stats_warmer`） | 补登为 10 站，并注明该清单是**文档性清单、无任何 in-code 断言** ⇒ 漏登不会致测试变红（这正是 `_etf_overview_warmer` 长期缺失的原因） | 验证者发现 → 主理人修正 |

### 2.2 未处理（待办 / 风险提示）

| # | 严重度 | 类别 | 位置 / 对象 | 问题描述 | 建议 | 来源 |
|---|--------|------|------------|---------|------|------|
| 8 | 🟠 | 环境 | 数据盘 | **水位 93%**，`app_settings` 反复告警；加剧扫描 IO 慢、且接近写满会导致抓取/落库失败 | 清理至 < 85% | 排障手 + 验证者 |
| 9 | 🟠 | 可用性风险 | `backend-run.log`（2026-09-28） | `GET /datacenter/datasets` **两次撞上 240s 服务端兜底**回 504 ⇒ 同步任务在跑时，连 90s 预算的 datasets 也会爆 | 评估同步与统计扫描的互斥/降级策略 | 主理人（日志实证） |
| 10 | 🟡 | 既有设计限制 | 进程外 CLI 写手 | `scripts/repair_data.py`、`data/ingest/__main__.py` 在**进程外**写数据，无法失效服务进程缓存 ⇒ 服务进程内 `mirror_status` 最长陈旧 1800s | 既有问题（`datasets`/`quality` 早已如此），注释已披露；如需强一致需跨进程失效机制 | 验证者 |

---

## 3. 验证证据（关键实测，均为原始输出）

| 检查项 | 命令 / 方式 | 结果 |
|--------|------------|------|
| 原始缺陷真实性 | 清缓存后计时 `cross_section.mirror_status()` | `COLD=57.79s` vs `OLD_BUDGET=45s` ⇒ `EXCEEDS=True` |
| 扫描成本归因 | 读 `cross_section.py:60-65` | 确为逐文件 `pl.read_parquet(f, columns=["date"])`；文件数 `10921+10924+10919 = 32764` |
| `mirror/rebuild` 500 已修 | in-process ASGI TestClient + 打桩 `build_mirror`（**非破坏性**） | `HTTP=200 / code=0 / data=list / build_calls=['daily_bar','daily_bar_hfq','daily_bar_qfq']` |
| 旧注解确属真坏 | 直接构造 `APIResponse[dict](data=[...])` | `ValidationError: data`（`OLD_ANNOTATION_ACCEPTS_LIST=False`） |
| TTL 无陈旧风险 | 读 `cross_section.build_mirror:139-144` + `stats_cache.invalidate_stats_cache:103` | 写入后 `invalidate` 清空除 `logs` 外全部键（含 `mirror_status`）；穷举 6 处写路径**全部调用** `invalidate` |
| 预热生效 | `await warm_datacenter_stats()` | 返回 `True`，耗时 102.41s；随后 4 个缓存探针 `hit=True / 0.0000s` |
| 预热失败安全 | monkeypatch `_scan_all_datasets` 抛错 | `return False` 且 `RAISED=False` |
| 预算不变量 | 解析 `timeout_guard.resolve_budget_seconds` | 两路径服务端预算均 **240.0s** > 前端 120s/180s ✔ |
| 端到端 | 重启后端（修复后代码）→ Playwright 打开 `/data` | 统计渲染真实值、镜像表真实数字；`HAS_TIMEOUT_BAR=false`、`HAS_LOAD_FAIL_BANNER=false`；相关 API 全 200 |
| 回归 | `pytest tests/ -q -m "not network"` | **1903 passed, 8 skipped, 3 deselected, 0 failed**（449.72s） |
| 前端 | `tsc --noEmit` / `vite build` | exit 0 / exit 0（755 模块） |

**证据留存**：端到端截图 `verify_data_page_20260929.png`（仓库根）；后端运行日志 `backend-run-verify.log`、`backend-run-0929b.log`。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 提交本次修复（4 源文件 + 2 测试文件；改动现留在工作区**未提交**） | 工程 owner | **P0** | 立即 |
| 2 | 清理数据盘至 < 85%（当前 **93%**，已反复告警） | 运维 | P1 | 本周 |
| 3 | 评估 `datasets`/`quality` 的 90s 前端预算是否足够：09-28 日志显示同步并发时 `datasets` 曾 >240s（**注意：单纯调大预算治不了本** —— 服务端 240s 兜底会先 504；根治需让统计扫描与同步互斥，或同步期间改走 SWR 陈旧值） | 后端 owner | P1 | 本周 |
| 4 | 评估 `mirror/rebuild` 改「提交-轮询」异步模式（全量重建 09-14 日志实测 ~8.5 分钟，远超 180s 预算） | 后端 owner | P1 | 两周内 |

---

## ⚠️ 待完善 / 已知局限

- **预热的保温范围是部分的**：`warm_datacenter_stats` 顺序预热 4 项，但 `storage_stats` 自身 TTL 仅 120s（刻意保留：`/overview` 的磁盘水位读数需保持新鲜，当前 93%），1500s 循环**无法**为其续期——已在代码注释中明确披露，不再宣称"全部保温"。
- **进程外 CLI 写手无法失效服务进程缓存**（既有设计限制，非本次引入），服务进程内 `mirror_status` 最长陈旧 1800s。
- **`mirror/rebuild` 全量重建耗时远超任何合理前端预算**（09-14 日志 ~8.5 分钟）。本次仅把预算提到 180s（覆盖增量场景 ~150s），全量场景仍会超时——根治需改为后台任务 + 轮询（行动项 #4）。
- **磁盘水位 93%** 是本次 IO 耗时偏高的环境成因之一，未处理。
- **前端无单元测试框架**（无 vitest/jest），前端修复的验证依赖 `tsc` + `build` + 代码级推理 + 浏览器端到端，而非组件级单测。
- **运行态与工作区存在 3 处行为等价的差异**：:8000 上运行的实例启动于验证阶段，**不含**验证后补的 3 项——`TextDataPanel.tsx:41` 注释更正、`warm_datacenter_stats` docstring 更正 + 成功日志行（#8/#9）。三者**均不改变行为**（2 处注释 + 1 行 `logger.info`），故已验证结论仍成立。**刻意不为此重启后端**：当前实例的统计缓存自 15:12 起已预热完成（页面加载即命中），重启反而会制造 ~100s 的冷扫窗口，让用户的首次开页变慢。
- 上述差异仅在**下一次重启**时消除；届时预热日志会出现在 `backend-run-*.log`（形如 `[datacenter] warm stats done in Xs`）。

---

## 📚 成员产出索引

- **gstack-investigator（排障手）**：根因报告（含分层归因、请求清单实测、排除项）+ 7 项修复实施报告（逐文件行号、`1903 passed` 自测、新增回归用例）。临时诊断脚本 `backend/scripts/_measure_dc*.py` 已按流程删除。
- **verifier（独立验证者）**：9 项可证伪检查原始输出（含 `COLD=57.79s`、非破坏性 500 复验、缓存失效路径穷举、预热失败路径、预算不变量解析、Playwright 端到端、全量回归）；截图 `verify_data_page_20260929.png`。
- **主理人（本报告作者）**：并发冷启动实测（`mirror_status` 62.34s 等 5 项）、单跑复测 73.91s、diff 逐处复核、`useAbortableTask` 中断契约复核、注释更正、报告汇编。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
