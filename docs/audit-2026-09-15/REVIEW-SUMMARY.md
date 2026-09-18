# AQP 增量修改审核 · 主理人结论

> **审核日期**：2026-09-15
> **审核对象**：工作区未提交的 60 个已修改 + 32 个未跟踪文件
> **对照基线**：`docs/audit-2026-09-14/AUDIT-SUMMARY.md`（P0 ×10 / P1 ×26 / P2 ×47）
> **分工**：架构评审（高见远）+ 回归验证（严过关），全程只读，未修改任何业务源码
> **专项报告**：`architecture-review.md`（632 行）、`qa-verification.md`

---

## 〇、一句话结论

> **10 条 P0 里 5 条已修、4 条只改了一半、1 条没动。本轮最大的风险不是"没改完"，
> 而是"改了一半却看起来像改完了"——两处功能被静默下线、一处搜索能力丢失、
> 一条既有测试必然失败，且全部不会在界面上报错。**

- 后端全量语法 **0 错误**；前端 `tsc --noEmit` **0 错误**、`vite build` 通过
- 全量离线测试 **808 passed / 8 failed / 8 skipped，4m42s** —— **历史上首次跑完**
- 新增 8 个测试文件 **43 passed / 0 failed，无空壳**（118 条断言）
- 遗留：**18 条半成品** + **10 条本轮新问题**

---

## 一、P0 验收矩阵（最终判定）

| # | 问题 | 判定 | 关键证据 |
|---|---|---|---|
| 1 | 特征双版本污染 | 🟡 部分 | `research/studio/alerts` 已切 `data/features.py:read_feature_frame`；但 `ml/monitor.py:107-124` 仍 `rglob("*.parquet")` 全版本混读 |
| 2 | 首屏 overview 冷缓存 | 🟡 部分 | 3/2 路并发 + 5s/6s 预算，45s 悬挂消失；但降级空态被 `swr.py:129` 缓存 300s，**从"慢"变成"空"** |
| 3 | 研究页并发超限 | 🟡 部分 | 前端 ComputeQueue(2) + AbortController 全做完；后端 `compute_guard.py:10-20` 仍"超限抛错不排队" |
| 4 | 401 无条件跳登录 | ✅ 已修 | 拦截器只清会话不跳转，Topbar 未登录不建 SSE |
| 5 | SSE 从未工作 | ✅ 已修 | ticket 方案（双存储域 + GETDEL 原子消费 + 60s 一次性），设计质量高 |
| 6 | ETF 四端点超时 | 🟡 部分 | 实测 4.5s；但 `frontend/src/pages/Etf*` **本次 0 改动**，新增字段全不消费 |
| 7 | 抓取未纳入互斥锁 | ✅ 已修 | `_run_fetch` 整体包 `pipeline_slot("fetch")` + 启动前预检 + 契约测试 |
| 8 | 因子分位预警路径 | ✅ 已修 | 改用 `read_feature_frame()`，跳过路径有 warning 留痕 |
| 9 | 全量 pytest 跑不完 | ✅ **已修**（实测） | **QA 实测 4m42s 跑完，808 passed** —— 架构师因缺日志判"部分"，以 QA 实测为准 |
| 10 | 产品误导与合规倒挂 | ⚪ **未动** | `screener.py:127` 仍 `risk = low if score>=0.3`；`ADMIN_TOKEN` 仍默认 `aqp-dev-token-change-me`；`ALLOW_REGISTRATION` 仍默认 true |

**统计：✅ 5 / 🟡 4 / ⚪ 1**

> ⚠️ **两成员结论冲突已调和**：架构师因 `pytest_task12_offline_final.txt` 日志被截断、
> 无 summary 行，判定 #9"无跑完证据"；QA 实测 `-m "not network"` 全量 **4m42s 正常退出**。
> 采信 QA 实测结果。遗留项降级为：`faulthandler_timeout=600` 偏长、未装 `pytest-timeout`。

---

## 二、8 个失败用例的归属（智能路由）

| 归属 | 数量 | 内容 |
|---|---|---|
| **源码 Bug → 工程师** | 1 | `/api/v1/export/screener` 空数据 500 崩溃 |
| **源码 Bug → 工程师** | 2 | `POST /notify/stream-ticket` 未登记 RBAC 注册表；`/notify/stream` 换用 `require_stream_viewer` 后内省取 None |
| **测试隔离 → 工程师** | 4 | `train_service` 4 用例：私有 DATA_ROOT 绕过 `.auto_sync.json`，墙钟 19:xx 触发真实联网同步，后台线程持锁未 join → 全局 pipeline 锁跨关闭泄漏（单独跑 15 passed） |
| **测试过时 → QA** | 1 | `test_settings_apikeys_rotate_writes_isolated` 期望 code 0，后端已改为刻意拒绝返 40000 |

---

## 三、最需要立刻处理的 5 件事

| 优先级 | 问题 | 位置 | 为什么急 |
|---|---|---|---|
| 🔴 1 | **导出接口 500 崩溃** | `export.py:44` | 本轮新增的不可用分支让 `date=None`，唯一下游 `export.py` 漏改 → 空数据时直接 500 |
| 🔴 2 | **一条既有测试必然失败** | `tests/test_write_endpoints_smoke.py:538` | 后端 rotate 已改为明确拒绝，测试仍断言返回 `aqpx_` 明文密钥；该文件**不在本轮改动里** |
| 🔴 3 | **公告 / 北向持股被静默下线** | `panels.py:165` + `realtime.py:499,652` | 改查本地 `news_announcement`（实测 0 行、全项目无写入方）+ 兜底改成恒抛异常 → 功能 100% 空且无人知情 |
| 🟠 4 | **`/portfolio/search` 搜不到 ETF** | `portfolio.py:57-104` | 改查本地 `instrument` 表，实测只有 `('stock', 5552)`、**0 条 etf** |
| 🟠 5 | **降级空态被缓存 300s** | `swr.py:129` | 一次外部源抖动 → 全站连续 5 分钟显示"不可用"且不自动重试 |

---

## 四、18 条半成品清单

| 类别 | 条数 | 典型 |
|---|---|---|
| A 孤儿代码 | 7 | `watchlist.py:152 _etf_names()`、`datacenter.py:286 _dir_size()`、`datacenter.py:307` 整套 `_refresh_overview` 死状态机、`RequireAuth.tsx:24` 无人使用 |
| B 单侧改动 | 8 | B1 portfolio 丢 ETF、B2 公告下线、B3 北向下线、B4 watchlist `flow_status` 前端未补、B5 ETF 新增字段前端 0 消费、B6 `RecommendBlock` 缺 `status/message/coverage`、B7 `refreshing` 类型残留、B8 风险等级前后端字面不一致 |
| C 配置漂移 | 4 | `FEATURE_VERSION`、`METRICS_REQUIRE_AUTH`、`AQP_PANEL_BLOCK_TIMEOUT`(20→4.5 腰斩)、`WARM_OVERVIEW_ON_STARTUP` —— 均**未收录进 `.env.example`** |
| D 重复实现 | 4 | `refreshing` 双口径、`_build_money_flow` 兼容别名、ETF 两套缓存键、SSE 两套过期概念 |
| E 遗留标记 | 3 | `useWatchlistStore.ts:3` 注释说"后端未实现"（实际早已存在）、`pipeline_lock.py:12` 分布式锁未实现、`market.ts:8` 旧注释"冷算约 49.5s"与新 6s 预算矛盾 |

---

## 五、本轮做得好的部分（建议保留）

1. **SSE ticket 设计是本轮最高质量的改动** —— 威胁模型（URL 泄漏 / 重放 / 跨存储域）逐条对应到实现，Redis 不可用时不回落本地副本避免重复消费，可作为授权委托的范例
2. **全量测试基线首次跑通** —— 从"跑不完"到 4m42s / 808 passed，这是所有后续验证的前提
3. **P0#10 的合规部分已修** —— 新增 `ResearchDisclaimer` 组件并在 4 个页面落地
4. **异常脱敏口径** —— `market.py` / `errors.py` / `client.ts:41` 已统一（ETF 模块未跟上，见 N8）
5. **契约质量** —— 18 个错误码前后端全对齐，screener / watchlist / portfolio / notify / research 字段一致

---

## 六、建议的下一步

**立刻（半天内）**：修 `export.py:44` 500 崩溃 → 改 `test_write_endpoints_smoke.py:538` 断言 → 决定公告/北向是要补数据源还是明确下线 → 恢复 ETF 搜索。

**本周内**：把 4 条"部分修复"推到完成（`monitor.py` 单版本化、`compute_guard` 有界排队、ETF 前端消费新字段、降级不缓存）。

**卫生收尾**：删 7 处孤儿代码、`.env.example` 补 4 个新开关、清理 `backend/` 根目录 13 个 `pytest_*.txt` 调试产物（未纳入 `.gitignore`）。

> **前置建议**：本轮 92 个改动**全部未提交**。建议先按主题拆成若干提交（如 `fix(sse)`、`perf(market)`、`refactor(features)`），
> 再开始收尾，否则一旦某个半成品需要回滚会连带整批。
