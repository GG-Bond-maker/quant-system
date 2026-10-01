# AQP 上线前全检报告（代码审查 · 安全审计 · QA · 前后端超时 · 性能 · 数据真实性）

**日期**：2026-10-01
**场景**：全流程上线前检查（pre-launch full check）
**参与成员**：产品评审员（代码审查）+ 安全官（OWASP/STRIDE）+ 排障手（超时/性能）+ 数据可信度审计员（数据真实性）+ QA 与发布（测试/门禁）
**工作目录**：`D:\Python_Project\Alpha Quant Platform`
**检查对象**：工作区**未提交改动**（20+ 文件 M）+ 运行时实测（后端 dev 实例）+ 数据仓库实核

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟡 **有条件 Go** —— 无"运行时即时崩溃"类问题，但存在 **4 项上线前必须处理的阻塞项**，其中 **1 项会直接令 CI 失败**。
- **最严重发现（🔴 4 项）**：
  1. **数据丢失**：市场概览"大盘主力 / 行业板块资金流"走了**被网络阻断的 host**，数据实际**可得却取不到** —— 用户最关心的"资金流向卡"在市场概览中长期降级不可用（*可修复*）。
  2. **CI 必红**：本轮未提交改动**新引入 19 个 mypy 错误**（`market.py:125/618`），而 `mypy` 是 CI 硬门禁 ⇒ 一旦提交，CI 失败。
  3. **日期口径错位**：ETF overview 用 `date.today()` 冒充数据日，休市日把上一交易日数据标成"今天"且报 `fresh`。
  4. **量化闸门缺失**：`max_participation`（机构级流动性闸门）在**框架策略回测路径**上根本没接线，docstring 却担保"已走 5% 默认"（注释 ≠ 代码）。
- **关于"数据是否真实可靠 / 是否造假"**：🟢 **主流路径数据真实、无系统性造假** —— 个股资金流、ETF 资金流、公告、日线均与 ground truth **逐位吻合**；"休市 0 冒充"已被正确拦截。**问题不在"造假"，而在"数据可得却取不到"（host 走错）与"口径错位"。**
- **关于"数据是否完整（尤其资金流向）"**：🟡 **部分缺失** —— ① 北向持股 = 永久下线（结构性，已诚实披露 `retired`）；② **大盘主力/行业板块资金流 = 假性缺失（可修复）**；③ 北向当日净流入休市日被**正确**置 null（非缺失，是正确拒绝假 0）。
- **下一步**：修 4 项阻塞（见"行动清单"），其中 **mypy 一行类型收窄** 与 **host 切换（复用既有 push2test 适配器）** 均为小改动、高收益。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| **Go / No-Go** | 🟡 **条件 Go**（清 4 项阻塞后 Go） |
| **严重度分布** | 🔴 **4** / 🟠 **6** / 🟡 **11** / 🟢 **12** |
| **关键行动项** | 4 条阻塞 + 5 条建议 |
| **数据是否造假** | 🟢 否（主流路径逐位吻合 ground truth） |
| **资金流向是否缺失** | 🟡 部分（1 项可修复的数据丢失 + 1 项结构性下线） |
| **是否符合量化系统设计预期** | 🟡 主链路闭环，但**流动性闸门在框架回测路径缺失**（与设计预期不符） |
| **建议负责人** | 后端（数据源/host 修复）、量化（闸门接线）、工程（mypy/日期口径） |

---

## 1. 各成员核心结论

### 🔍 产品评审员（代码审查 · `_inbox_codereview-2026-10-01.md`）
- **核心判断**：🟡 有条件 Go（🔴0 / 🟠3 / 🟡6 / 🟢5）。本轮 diff 质量**高** —— §7.7.2 的 A1~A7 七条纪律**大部分被主动落实并配了证伪测试**（100 项相关测试全绿）；**未发现前视泄漏**（hfq 基准唯一、volume=股 已修正、财务/公告 PIT 正确）。
- **关键建议**：① `max_participation` 三处默认值漂移且**两条 API 路径实际未接线**（`enable_friction=False` 时是死参数；`StrategyBacktestRequest` 根本没该字段）→ 最常用的策略回测路径**完全无流动性闸门**；② ETF 总量 `sum(x.get("size_yi") or 0 ...)` 残留 A2"0 冒充"；③ `broker.py:328` 的 T+1 闸门为"整单拒绝"而非"部分成交"（保守向，无泄漏风险）。

### 🛡️ 安全官（OWASP Top10 + STRIDE · `_inbox_security-2026-10-01.md`）
- **核心判断**：🟡 可上线（Go）。**无确认可利用漏洞**；1 个高危设计面（已被 prod fail-fast 兜底）+ 若干加固项。
- **关键建议**：① **F-001（🟠 置信9）**：`ALLOW_ADMIN_TOKEN_LOGIN` 默认 True + `RBAC_ENFORCE` 默认 False ⇒ 默认配置下任何已登录用户等同 admin；**缓解已到位**（prod 启动 fail-fast 逐项拒绝、compose 强制 `ENV=prod`）→ 仅需运维核对生产 `.env` 真实用了 prod 键与强随机 `ADMIN_TOKEN(≥32)`；② **F-002（🟡）** `symbol` 未白名单直拼路径（**实测不可穿越**，属纵深防御缺口）；③ **F-003（🟡）** 无 CSP/HSTS（JWT 存在 localStorage）。已确认安全：全 112 路由穷举后**仅 5 个匿名端点且全部正当**、SQL 全参数化、外呼全带 timeout、CORS 白名单、PBKDF2+随机盐。

### ✅ QA 与发布（`_inbox_qa-2026-10-01.md`）
- **核心判断**：🟡 有条件 Go —— **功能测试与前端闸门全绿，但后端 mypy 门禁红**。
- **关键建议**：① **pytest 1971 passed / 8 skipped / 0 failed** ✅；② **ruff All checks passed** ✅；③ 前端链 `style:check`→`nav:check`→`tsc -b --force`(exit 0)→`build`(✓30.52s)→`bundle:check` **全绿** ✅；④ 🔴 **mypy 19 errors 全在 `market.py:125/618`，为本轮新增 ⇒ CI 必红**，必须修。
  > ⚠️ qa-lead 在收尾时遭遇 **429 速率限制失败**，未能自行落盘；其**测试与门禁结果均为实测真实产出**（日志在 `_tmp/`），由主理人依据日志汇编，`tsc` 由主理人复跑确认 exit=0。

### 🔧 排障手（超时 / 性能 · `_inbox_perf-2026-10-01.md`）
- **核心判断**：🟡 Conditional Go —— **0 个当前触发型真缺陷，2 个潜在结构缺口**。
- **关键建议**：① 后端 6 个显式预算 + 全局 240s 兜底；**判据"无回填+唯一来源=真缺陷" ⇒ 当前触发型 0 个**；② **纠正了团队此前判断**：`/overview/daily`（`market.py:1140`）**仍未传 `background_build`**（此前"已修"不准确）—— 但实测真冷 **4.76s < 20s 预算** ⇒ 暂不触发，属**潜在缺口**（数据增大后无自愈）；`/watchlist/dashboard` 同型；③ 前后端超时**假失败错配 = 0**；④ Top3 性能机会：RT 内 `stock_zh_a_spot_em` **重复调用**（−1.2s）、5 个指数**串行**（占 RT 墙钟 48%）、`build_money_flow` 3 源**串行**（−2.2s）—— 共同前置是 `_throttle` **持锁 sleep 致外呼全局串行**（结构性根因）。

### 📊 数据可信度审计员（数据真实性 · `_inbox_data-20261001.md`）
- **核心判断**：🟢 **数据真实、无系统性造假**；🟡 **资金流向存在"取得到却取不到"的真数据丢失**。
- **关键建议**：① **F1（🔴）**：`build_money_flow` 的市场级两条路径走 akshare 硬编码的**被阻断 host**（push2his/push2），而项目**已**在个股(`realtime.py:471`)与 ETF(`etf.py:82`)改用可达的 `push2test` —— **唯独这里漏改**，导致市场概览资金流卡长期不可用（实测同接口经 push2test：09-30 主力 **−136.28 亿**、航空机场 1.58 亿，**完全可得**）；② **F2（🔴）** ETF overview 用 `date.today()` 冒充数据日（同项目 market/stock/portfolio 均用 `today_trade_date_or_last()`，**仅 etf 是异类**）；③ **F3（🟠）** `us_size_yi` 16 只美股 ETF 规模全 None ⇒ `sum([])=0` 冒充"规模恰为 0"；④ 全市场日线真实覆盖**止于 2026-09-17**（09-29 仅 1 只标的），属尾部补数缺口。

---

## 2. 综合审查发现（去重合并，按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|--------|------|------|---------|------|------|
| **1** | 🔴 | 数据丢失 | `services/market_service.py:107,117` | 大盘主力/行业板块资金流走 akshare 硬编码的**被阻断 host**（push2his/push2）→ 市场概览资金流向卡长期 degraded/unavailable。**数据经 push2test 完全可得** | 复用 `realtime._request` + push2test 为主、push2delay/push2his 降级；与个股/ETF 路径统一 host 口径 | 数据审计 |
| **2** | 🔴 | CI 门禁 | `api/v1/market.py:125,618` | 本轮未提交 diff **新引入 19 个 mypy 错误**（polars 联合类型未收窄）⇒ mypy 是 CI 硬门禁、无 `continue-on-error` ⇒ **提交即 CI 失败** | `cov["n"].max()` / `pred["date"].min()/max()` 显式 `cast(pl.Date, ...)` 或 `.item()` 后转 `date`；重跑 mypy 归零 | QA |
| **3** | 🔴 | 口径错位 | `api/v1/etf.py:93,445,503` | ETF overview 用 `date.today()` 当作数据日 ⇒ 休市/周末把上一交易日数据标成"今天"且 `data_freshness=fresh` | 改用 `today_trade_date_or_last()`，或从东财 `f124` 取真实数据日 | 数据审计 |
| **4** | 🔴 | 量化逻辑 | `backtest/broker.py:65`、`strategy_base.py:311`、`api/v1/backtest.py:65` | `max_participation` 三处默认值漂移（0.05/0.0），且 `StrategyBacktestRequest` **无该字段**、`_run_single` 不传 ⇒ **框架策略回测路径完全无流动性闸门**；docstring 却担保"已走 5% 默认" | 统一三处默认并让 `run_strategy` 显式接收；给 `StrategyBacktestRequest` 补字段并透传；修正 docstring | 代码审查 |
| **5** | 🟠 | 数据丢失（潜在） | `api/v1/market.py:1140` | `/overview/daily` **仍未传 `background_build`**（rt/compat 都有）⇒ 无自愈回填。当前实测冷 4.76s<20s 暂不触发，数据增大后将被永久钉在降级态（**此前"已修"判断不准确**） | 与 rt/compat 对齐，复用 `_build_unbudgeted` 范式 | 排障手 |
| **6** | 🟠 | 静默退化 | `api/v1/etf.py:371,383,389,391` | ETF 总量 `sum(x.get("size_yi") or 0 ...)` 残留 A2"0 冒充"：`us_size_yi=0` 与 `us_count=16` 自相矛盾 | 无有效样本时返回 `None`（前端"—"），判据用"有效观测数" | 代码审查 + 数据审计 |
| **7** | 🟠 | 安全设计面 | `core/auth.py:199` + `core/config.py:86-89,223-229` | `ALLOW_ADMIN_TOKEN_LOGIN` 默认 True + `RBAC_ENFORCE` 默认 False ⇒ 默认配置下任何已登录用户等同 admin。**prod 已 fail-fast 兜底** | 运维核对生产 `.env` 真用 prod 键 + 强随机 `ADMIN_TOKEN`；建议默认值改为安全默认（False/True） | 安全官 |
| **8** | 🟠 | 缺失字段 | `data/etf.py:555-591` | `fetch_flow()` 返回项**无数据日字段**（东财 `f124` 可得却未请求）⇒ 上层只能 `date.today()` 猜（F2 根因） | `fields` 增加 `f124` 并暴露 `data_date` | 数据审计 |
| **9** | 🟡 | 架构违规 | `api/v1/backtest.py:906`、`api/v1/stock.py:243` | 重计算走 `asyncio.to_thread`（落默认 22 槽池，与 `/health/ready` 共用），未走 `compute_pool` —— 与项目约定不一致（audit P1-b 漏改） | 改 `run_in_executor(get_compute_pool(), ...)` | 代码审查 + 排障手 |
| **10** | 🟡 | 性能 | `market.py:267,352` / `market.py:51` / `market_service.py:72,107,117` | ①`stock_zh_a_spot_em` **重复调用**（heat+anomalies 各一次）−1.2s；②5 个核心指数**串行**拉全历史占 RT 墙钟 48%；③资金流 3 源**串行** −2.2s。共同前置：`_throttle` **持锁 sleep** 致外呼全局串行 | 去重 spot、并发指数/资金源；先改锁粒度（单独立项） | 排障手 |
| **11** | 🟡 | 预算余量 | `market.py:942` | `RT_BUILD_TIMEOUT_SECONDS=15s`，实测最坏 11.75s（78%）；源码注释称正常路径 ~14s=**93%** ⇒ 外部源全可达时**逼近预算**，抖动即降级 | 降 RT 墙钟（见 #10）后再评估是否上调预算 | 排障手 |
| **12** | 🟡 | 数据完整性 | `data/parquet/daily_bar*`、`features`、`predictions` | 全市场真实覆盖**止于 2026-09-17**（2492 只）；09-18~09-29 为稀疏尾巴（09-29 仅 1 只）；raw/qfq/features/predictions **最新日期口径不齐**（09-29/09-17/09-17/09-17） | 前端显式披露"最新完整交易日"与覆盖数；补数后回填 | 数据审计 |
| **13** | 🟡 | 潜在劫持 | `api/v1/desk.py:348`、`report.py:166`、`trading/paper.py:114` | `universe_daily` 最新截面取 `max(date)`，未复用 `_pick_stat_day`。当前满覆盖（2494）未被劫持，但一旦出现孤立行即退化 | 抽 `_pick_stat_day` 为共享工具，三处统一；或加覆盖度断言 | 数据审计 |
| **14** | 🟡 | 逻辑 | `backtest/broker.py:328-335` | T+1 闸门**整单拒绝**（可卖的 200 股也被全拒）→ 换手/成本偏低。**方向保守、无泄漏** | 改为"截断到 available 部分成交 + 剩余 reason=t1"，或在 docstring 注明取舍 | 代码审查 |
| **15** | 🟡 | 逻辑 | `data/etf.py:439-478` | `us_real_symbol` 的 `qt` 回传取**第一个**含点号项 ⇒ 可能取到非本次候选的交易所 | 以 `qt` 的 `key==cand` 精确取值 | 代码审查 |
| **16** | 🟡 | 静默退化 | `data/etf.py:534-543`、`api/v1/etf.py:360` | `fetch_kline` 超限自检只打 WARNING 未回传 degraded；`sum(... or 0)` 死代码形态 | 让 API 层可感知 degraded；去 `or 0` | 代码审查 |
| **17** | 🟡 | 一致性/UI | `frontend/.../MoneyFlowPanel.tsx:81,107` | 仅 `status==='ok'` 才渲染汇总；后端 `degraded` 带数据时落入 else 只显示 reason，**已到达的 partial 数值不显示** | 对 `degraded` 也渲染已到达子块 + 降级角标 | 数据审计 |
| **18** | 🟡 | 安全加固 | `data/parquet_store.py:773` / `frontend/index.html`+`nginx.conf` | `symbol` 未白名单直拼路径（**实测不可穿越**）；无 CSP/HSTS（JWT 在 localStorage） | 加 `_norm_symbol` + `is_relative_to(DATA_ROOT)`；补 CSP/HSTS | 安全官 |
| **19** | 🟡 | 潜在劫持/一致性 | `data/parquet/announcements` | 公告仅覆盖 **2026-04~09**（约 6 个月，无历史回填），内容零 null | 前端标注"近 6 月窗口"；如需长历史按 `stock_notice_report` 逐日回填 | 数据审计 |
| **20** | 🟢 | 观察项 | `market.py:125` 的 `or 0`、`api/v1/watchlist.py:82-84` | `int(cov["n"].max() or 0)` 为**安全**用法（非 0 冒充）；watchlist volume `or 0` 会污染量能判据 | 前者列入白名单；后者缺失时跳过该 bar | 代码审查 + 数据审计 |

> 说明：🟢 级项（12 条）另含"核对通过"项：`_sell_cost` 单位正确、前端涨跌染色 `null⇒中性` 合规、`setOption(option,true)` 合规、`_scan_dataset` 的 `failed` 已进返回值、`ensure_columns` 幂等迁移等，详见各成员报告。

### 关键实证（原始数据）

**资金流 host 对照（同接口同参数）**
```
push2test.eastmoney.com -> 200 OK  主板 2026-09-30 主力净额 = -13628162048.0 元 (-136.28 亿)  ✅可得
push2his.eastmoney.com  -> RemoteProtocolError   ✗阻断
push2delay.eastmoney.com-> RemoteProtocolError   ✗阻断
=> akshare.stock_market_fund_flow 硬编码 push2his；项目已在 realtime.py:471 / etf.py:82 用 push2test，唯独 market_service 市场级两条路漏改
```

**北向假 0 被正确拦截（2026-10-01 休市）**
```
2026-10-01 沪股通 北向 交易状态=4 资金净流入=0.0  ← 假0（上涨数是前一交易日残留）
后端 market_service.py:92-96 读「交易状态」列 → int(4)=休市/未开盘 → raise → north=None → 走 degraded
=> 判定正确，非造假（前端渲染中性「—」而非红色「+0亿」）
```

**mypy 新增证据**
```
git show HEAD:backend/app/api/v1/market.py | grep -E "n_max = int\(cov|_pred_min\.year"
  → 无匹配  ⇒ 这两行（mypy 报错行）为本轮未提交改动新引入
当前 worktree: mypy app/ --ignore-missing-imports → Found 19 errors in 1 file (market.py:125,618)
```

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望 |
|---|------|--------|--------|------|
| 1 | 修 `market.py:125/618` 的 polars 类型收窄（`cast(pl.Date,...)`/`.item()`），使 `mypy app/ --ignore-missing-imports` 归零 | 后端 | **P0** | 上线前 |
| 2 | `market_service.build_money_flow` 的市场级两条路径改用 `push2test`（复用 `realtime._request` 与降级链），恢复"大盘主力/行业板块"资金流 | 后端/数据 | **P0** | 上线前 |
| 3 | ETF overview 数据日改用 `today_trade_date_or_last()`（或东财 `f124`），休市不再把旧数据标成今日；并给 `fetch_flow` 补 `data_date` 字段 | 后端 | **P0** | 上线前 |
| 4 | `max_participation` 统一默认值 + 让 `StrategyBacktestRequest` 接线并透传；修正 `run_strategy` docstring 的失实担保 | 量化/后端 | **P0** | 上线前 |
| 5 | `/overview/daily` 与 `/watchlist/dashboard` 补 `background_build`（自愈回填，消除潜在永久降级） | 后端 | P1 | 上线前（建议） |
| 6 | 修 ETF 总量 `or 0`（#6/#16），无有效样本返回 `None` + 状态披露 | 后端 | P1 | 上线后首迭代 |
| 7 | 生产 `.env` 核对：`ENV=prod`、`ADMIN_TOKEN≥32` 强随机、`ALLOW_ADMIN_TOKEN_LOGIN=false`、`RBAC_ENFORCE=true`（安全官 F-001） | 运维 | P1 | 部署前确认 |
| 8 | `backtest.py:906` / `stock.py:243` 重计算下沉 `compute_pool`；去重 spot + 并发指数/资金源（性能 #10） | 后端 | P2 | 下迭代 |
| 9 | 前端 `MoneyFlowPanel` 支持 `degraded` 渲染，展示已到达的 partial 子块 + 降级角标 | 前端 | P2 | 下迭代 |
| 10 | 补 CSP/HSTS、`symbol` 白名单、CI 依赖 CVE 扫描（安全加固） | 全栈 | P3 | backlog |

---

## ⚠️ 待完善 / 已知局限

- **qa-lead 因 429 速率限制失败**，未自行落盘报告；其测试/门禁结果为**真实实测产出**（日志在 `deliverables/gstack/_tmp/`），由主理人汇编，`tsc` 由主理人复跑确认。**功能测试结论可信**。
- **git 仓库存在对象级损坏**（`git fsck` 报 `invalid reflog entry` + `missing blob/tree/commit`，`.git/index` cache-tree 失效）⇒ **全量 `git diff` 不可用**。本轮所有 diff 审查用**逐文件 `git show`/`git diff HEAD -- <file>`** 绕过；`api/v1/etf.py`、`frontend/src/pages/Etf/index.tsx` 的**旧版 blob 缺失**，其改动按当前内容 + 上下文判定。**这不影响上线，但影响可追溯性与回滚能力**（详见 `.git/RECOVERY-NOTE-2026-10-01.txt`）。
- **网络环境**：本机 `push2`/`push2his` 整组阻断（`HTTP_PROXY` 指向本地代理、`NO_PROXY` 为空），故 F1 的 ground truth 用 `push2test` 镜像验证，未做逐字段全量比对。
- **未覆盖面**：需认证端点（`/etf/*`、`/watchlist/*`、`/screener/*`）的超时未做端到端实测（按源码判据）；未做多副本/长稳/并发压测；未逐条比对依赖 CVE；前端 40+ 改动仅抽查 4 类高危模式。
- **一处与既有结论的冲突（需人工复核）**：上一提交（`87d8e51`）自称"🟢 Go，3/3 阻塞已清"，但**当前工作区实测**：mypy 19 errors（CI 必红）、daily 仍缺 `background_build` ⇒ **"已清"的判断不准确**。建议以本报告为准。

---

## 📚 成员产出索引

- 产品评审员（代码审查）：`deliverables/gstack/_inbox_codereview-2026-10-01.md`
- 安全官（OWASP+STRIDE）：`deliverables/gstack/_inbox_security-2026-10-01.md`
- QA 与发布（测试/门禁）：`deliverables/gstack/_inbox_qa-2026-10-01.md`
- 排障手（超时/性能）：`deliverables/gstack/_inbox_perf-2026-10-01.md`
- 数据可信度审计员（数据真实性）：`deliverables/gstack/_inbox_data-20261001.md`
- 原始测试日志：`deliverables/gstack/_tmp/{pytest-full,pytest-degrade,ruff,mypy,fe-*.log}`

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
