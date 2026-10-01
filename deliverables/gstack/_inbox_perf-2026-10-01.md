# AQP 上线前全检 —— 超时 / 性能专项报告

- **作者**：investigator（排障手）
- **日期**：2026-10-01
- **工作目录**：`D:\Python_Project\Alpha Quant Platform`
- **专用后端**：`127.0.0.1:8021`（与本项目其它成员共用的 8000 端口隔离；共用同一 Redis db0）
- **方法**：grep 全仓穷举预算点 + 源码判据分析 + **实测冷/热路径**（清 Redis key / 空进程内缓存）+ 外呼计数插桩（不改生产代码）

---

## ① 结论 TL;DR

| 项目 | 结论 |
|---|---|
| **超时/预算点总数** | 后端 **6 个显式业务预算**（daily/rt/compat/etf×2/watchlist）+ 1 个全局兜底（240s）+ 1 个设备面板块预算（4.5s） |
| **"无回填 + 唯一来源"判定为真缺陷** | **0 个当前触发型缺陷**；**1 个潜在/潜伏缺陷**（`/overview/daily` 缺 `background_build`，当前实测冷 4.76s < 20s 预算 ⇒ 暂不触发，但无自愈兜底） |
| **前后端超时错配（假失败风险）** | **0 个**（前端超时均 ≥ 服务端预算） |
| **前后端超时错配（掩盖降级）** | **普遍存在但属刻意设计**：`OVERVIEW_TIMEOUT=120s` >> 后端 20s/15s ⇒ 服务端预算是唯一约束，前端仅兜底 |
| **实测关键耗时** | `/overview/daily` 真冷 **4.76s** / 稳态 2.53s / 命中 0.01s；`/overview/rt` 冷 **6.89~11.75s**（预算 15s，占用率最高 78%）；单次 `_build_rt` 外呼 **10 次**，墙钟 12.53s |
| **Top3 性能机会** | ① RT 内 `stock_zh_a_spot_em` **重复调用**（heat+anomalies 各一次）→ **−1.2s（−9.6%）**；② 5 个核心指数**串行**新浪全历史拉取 = ~6s（**占 RT 墙钟 48%**）→ 并发/换源可 −4~5s；③ `build_money_flow` 内 3 个东财资金源**串行** = ~3.6s → 并发可 −2.4s |
| **挂死/泄漏风险** | 🟢 主风险已被 `socket.setdefaulttimeout(10s)` + 独立 compute_pool(6槽) 处置；🟡 残留：`stock.py` 面板 6 块经 `asyncio.to_thread` 落**默认 22 槽池**（与 `/health/ready` 共用），与项目自身"重计算下沉 compute_pool"的设计规则不一致 |
| **优先级** | 🟡 **Conditional Go**：无上线阻塞缺陷；建议跟进 2 项（RT 预算余量、daily 回填） |

---

## ② 超时 / 预算点全表

> **判据**：预算 < 工作量是否构成缺陷，取决于**降级路径能否自愈** —— 有"无预算的后台重建"回填 ⇒ 是设计；没有 ⇒ 是缺陷。

| # | 位置 | 预算值 | 包裹的工作量 | 降级后有**无预算回填**? | 是否缺陷 | 证据 |
|---|---|---|---|---|---|---|
| 1 | `market.py:96` `DAILY_BUILD_TIMEOUT_SECONDS` | **20.0s** | `_build_daily`（heat/sectors/recommend/ai_stats/sentiment，本地 parquet，4 子块并行） | ❌ **无**（`_overview_block_response` 未传 `background_build`，见 1140） | 🟡 **潜在**（当前冷 4.76s < 20s ⇒ 不触发；若数据/磁盘增大越过 20s，将永久钉在降级态） | 实测：真冷 4.76s / 稳态 2.53s；`grep background_build market.py` → 1063(rt)/1283(compat) 有、1140(daily) 无 |
| 2 | `market.py:942` `RT_BUILD_TIMEOUT_SECONDS` | **15.0s** | `_build_rt`（指数5 + 资金流3 + 快照2，经 akshare 1.2s 限速**全局串行**） | ✅ **有**（`background_build=_build_unbudgeted`，1063） | 🟢 设计（但余量紧张） | 实测冷 6.89/9.77/11.75s；源码注释称正常路径 ~14s = **93% 预算** |
| 3 | `market.py:925` `OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS` | **6.0s** | `_build_overview`（rt + daily 并发；实测 rt 单块即 ~12s） | ✅ **有**（`background_build=_build_unbudgeted`，1283） | 🟢 设计（DEPRECATED 端点，前端零调用） | 1283 传 background_build；注释 920 明示 6s 100% 超时、靠后台重建回填 |
| 4 | `etf.py:78` `_ETF_ENDPOINT_BUDGET_SECONDS` | **4.5s** | ETF overview 聚合（实测冷 6.22s） | ✅ **有**（`background_build=_build_unbudgeted` + 降级后 `_spawn_etf_rebuild`，235-243） | 🟢 设计（注释 150 自承"必然超时"，靠无预算回填） | `etf.py:235-243` 双保险：SWR background_build + 降级即触发重建 |
| 5 | `etf.py:79` `_ETF_DETAIL_BLOCK_BUDGET_SECONDS` | **4.0s** | ETF 详情单块外部源 | ✅ 有（同 `_run_unbudgeted` 体系） | 🟢 设计 | `etf.py:254-257` |
| 6 | `watchlist.py:34` `_WATCHLIST_BUDGET_SECONDS` | **5.5s** | 自选看板（行情+估值+资金流，多源） | ❌ **无**（`cached_or_build` 未传 `background_build`，383-385） | 🟡 **潜在**（需登录 Token 才能实测；TTL 60s + 无回填，同 daily 模式） | `watchlist.py:383` 无 background_build |
| 7 | `core/timeout_guard.py:113` 全局兜底 | **240s**（+ `/backtest/strategy-run` 660s、`/ops/dag/rerun` 330s、SSE 豁免） | 所有无服务端预算的 HTTP 请求 | N/A（基础设施层） | 🟢 设计 | 240s > 全仓最长前端非豁免超时 180s；660/330 对应前端 600/300s |
| 8 | `stock.py:68` `_PANEL_BLOCK_TIMEOUT` | **4.5s** | 个股面板单块（6 块并发 gather） | ✅ 有（超时回 SWR 影子键旧值，311-316） | 🟢 设计 | 超时优先回 stale，无旧值才 unavailable |
| 9 | `compute_guard.py` `COMPUTE_ACQUIRE_TIMEOUT_SECONDS` | **10.0s** | compute_pool 槽位排队等待 | N/A | 🟢 设计 | 满额时有界等待而非立即拒绝 |

**"无回填 + 唯一来源" 真缺陷计数 = 0（当前触发型）**。第 1、6 项为**潜在**（当前工作量 < 预算故不触发；结构上缺自愈兜底）。

---

## ③ 前后端超时错配表

> 前端常量全清单（`frontend/src/api/`）：`client.ts` 默认 **15s**；`download` **180s**；`ETF_TIMEOUT` **30s**；`OVERVIEW_TIMEOUT` **120s**；`portfolio` BACKTEST **120s** / SEARCH **15s**；`screener` **30s**；`stock` PANELS **30s**；`datacenter` **60/90/120/180s**；`research` **30/120/180s**；`production` **30/60/120/180/300s**；`strategyBacktest` **120/600s**；`monitor` **60/120s**；`settings` **30/60s**；`backtest` **120s**；`watchlist` **30s**。

| 后端路径 | 后端预算 | 前端超时 | 关系 | 判定 |
|---|---|---|---|---|
| `/market/overview/daily` | 20s | `OVERVIEW_TIMEOUT`=120s | 前端 >> 后端 | 🟢 刻意设计（注释明说"服务端预算是唯一约束"）；**掩盖服务端降级**——若 daily 降级，用户 15s 内拿到 degraded 载荷而非报错 |
| `/market/overview/rt` | 15s | 120s | 前端 >> 后端 | 🟢 同上 |
| `/etf/overview`, `/etf/detail` | 4.5s / 4.0s | `ETF_TIMEOUT`=30s | 前端 >> 后端 | 🟢 设计 |
| `/watchlist/*` | 5.5s | 30s | 前端 >> 后端 | 🟢 设计 |
| `/stock/{sym}/panels` | 4.5s/块 | `PANELS_TIMEOUT`=30s | 前端 >> 后端 | 🟢 设计 |
| `/backtest/strategy-run` | 660s（全局表） | **600s** | 后端 > 前端（+60s 余量） | 🟢 正确（服务端不误杀） |
| `/ops/dag/rerun` | 330s | **300s** | 后端 > 前端（+30s） | 🟢 正确 |
| 其它长任务（research/production 180s 等） | 240s 默认 | ≤180s | 后端 > 前端 | 🟢 正确 |

**前端超时 < 后端最坏耗时（假失败）错配 = 0 项。**
**前端超时 >> 后端预算（掩盖降级）= 6 类端点，均为刻意设计**，风险已在服务端用 `data_freshness.status`/`status` 字段显式披露，前端可感知。

> ⚠️ 唯一需留意：`client.ts` 默认 **15s**。任何未显式传 timeout 且可能走重计算的端点都会被 15s 前端超时**先于**服务端预算打断。经核查，所有重计算端点均已显式传 timeout，默认 15s 仅作用于轻端点 ⇒ 无实际错配。

---

## ④ 实测耗时数据（原始数字）

**环境**：后端 8021（`AQP_WARM_OVERVIEW_ON_STARTUP=false`），Redis db0，交易日 20260930。测量前 purge 对应 key。

### 4.1 HTTP 冷/热路径（清 Redis key 后首请求 = 冷）

| 端点 | 冷路径 | 热（缓存命中） | 备注 |
|---|---|---|---|
| `/market/overview/daily` | **2.59s**（ai_stats 进程内已热） | 0.01s | freshness=local_daily |
| `/market/overview/daily`（**真冷**，空 `_ai_stats_cache`，直接调 `_build_daily`） | **4.76s** | 2.53s | 子块 heat/sectors/ai_stats 全 ok，recommend degraded |
| `/market/overview/rt` | **6.89s / 9.77s / 11.75s**（3 次） | 0.01s | 均 status=degraded（外部源不可达，非超时） |
| `/market/overview`（DEPRECATED） | 需 Token | — | — |
| `/etf/overview`, `/watchlist`, `/screener`, `/market/quotes` | **需认证**（code=40100） | — | 无法匿名实测 |

### 4.2 单次 `_build_rt` 外呼计数（插桩，无改生产码）

```
_build_rt wall = 12.53s
akshare calls = 10:
  stock_zh_index_daily            ×5  ← 5 个核心指数，串行，新浪全历史
  stock_zh_a_spot_em              ×2  ← ⚠️ 重复！heat 一次 + anomalies 一次
  stock_hsgt_fund_flow_summary_em ×1
  stock_market_fund_flow          ×1
  stock_sector_fund_flow_rank     ×1
块状态: indices=ok  money_flow=unavailable  anomalies=degraded
```

**墙钟模型验证**：10 次外呼 × `AKSHARE_RATE_LIMIT`(1.2s，`_throttle` 持锁 sleep ⇒ 全局串行) = **12.0s ≈ 实测 12.53s** ✅

### 4.3 子块成本拆解（源码 + 实测推定）

| 子块 | 外呼数 | 串行成本 | 占 RT 墙钟 |
|---|---|---|---|
| `_build_indices` | 5（新浪指数） | ~6.0s | **~48%** 🔴 |
| `_build_heat` | 1（东财全市场快照） | ~1.2s+ | ~10% |
| `_build_anomalies` | 1（东财全市场快照，**与 heat 重复**） | ~1.2s+ | ~10% |
| `_build_money_flow` | 3（东财资金源，**块内串行**） | ~3.6s | ~29% 🔴 |

---

## ⑤ 性能优化机会清单（含量化预估）

| # | 机会 | 位置 | 量化收益（实证口径） | 风险/成本 |
|---|---|---|---|---|
| **O1** | **去重 `stock_zh_a_spot_em`**：`_build_heat` 与 `_build_anomalies` 各调一次全市场快照。让 `_build_heat` 返回原始 spot df 供 anomalies 复用（或 anomalies 只消费 heat 已算好的 pct 分布 + 单独取涨停名单） | `market.py:267` + `market.py:352` | 单请求外呼 10→9，**−1.2s（RT 墙钟 −9.6%）**；归因：插桩实测 2 次 spot 调用，各占 1.2s 限速 | 低。anomalies 需逐标的明细，heat 目前只返回聚合 ⇒ 需让 heat 顺带返回 raw 或 top 名单；注意不要破坏 heat 的聚合契约 |
| **O2** | **5 个核心指数并发拉取**：`_build_indices` 内 `for` 串行 5 次 `stock_zh_index_daily`（新浪源，与东财限速解耦可并发）；或换轻量指数据源（现拉**全历史**只为取 tail(60)） | `market.py:51` | 5×1.2s=6.0s 串行 → 并发 ~1.5s，**−4.5s（RT 墙钟 −36%）**。若换只取近 60 日的接口，单次 ~0.3s ⇒ 总 ~1s，**−5s（−40%）** | 中。新浪源与东财限速**共用** `_throttle` 全局锁（同 `akshare_adapter._throttle`）⇒ 单纯并发仍被 1.2s 锁串行化！需为新浪源单独限速域或升 `AKSHARE_RATE_LIMIT` 并发度。**这是注释 market.py:939 已自陈的"独立设计项"** |
| **O3** | **`build_money_flow` 内 3 个东财资金源并发**：现为 `hsgt → market_flow → sector_rank` 串行 | `services/market_service.py:72/107/117` | 3×1.2s=3.6s → 并发 ~1.4s，**−2.2s（RT 墙钟 −18%）** | 中。同 O2：受全局 `_throttle` 锁约束，需先解决锁粒度 |
| **O4** | **`/overview/daily` 补 `background_build`**：与 rt/compat 对齐，消除潜在自愈缺口 | `market.py:1140` | 非性能收益，是**健壮性**：冷 daily 若越过 20s 后可自愈而非永久降级 | 低。复用 `_build_unbudgeted` 范式 |
| **O5** | **`stock.py` 面板 6 块下沉 compute_pool**：现 `asyncio.to_thread(builder)`（`stock.py:243`）落默认 22 槽池 | `stock.py:243` | 非吞吐收益，是**爆炸半径隔离**（与 `/health/ready` 共用池的隐患） | 低。改用 `run_in_executor(get_compute_pool(), ...)` |
| **O6** | **`/overview/daily` ai_stats 独立缓存已有**（`_AI_STATS_TTL_SECONDS=3600`，进程内）→ 已验证有效（真冷 4.76s vs 稳态 2.53s，差 2.23s≈ai_stats 成本）。**无需再动** | `market.py:548` | — | — |

> ⚠️ **O2/O3 的共同前置**：`akshare_adapter._throttle()` 与 `realtime._throttle()` **都在持锁期间 sleep**，使所有外呼**全局串行**。任何"并发化"优化若不同时调整锁粒度/限速域，收益会被锁吃掉。这是本项目 RT 延迟的**结构性根因**，建议单独立项。

---

## ⑥ 挂死 / 泄漏风险

### 6.1 已处置（🟢）

- **socket 无超时挂死** → `main.py:59-61` `socket.setdefaulttimeout(10s)`（`SOCKET_DEFAULT_TIMEOUT_SECONDS`）。实测意义：被 `wait_for` 放弃的 akshare worker 最终能返回 ⇒ 进程可退出（rc 124→0）。
- **线程池耗尽挤占探针** → 重计算下沉 `compute_pool`（6 槽，`core/compute_pool.py`），与默认 22 槽池隔离。`/health/ready`（探针）与重计算不再争槽。
- **SSE 长连接不被误切** → `timeout_guard._EXEMPT_PATHS` 豁免 `/notify/stream`。
- **ASGI 协议正确性** → 超时后已 start 不再补发消息（`timeout_guard.py:185-192`），有专门测试覆盖。

### 6.2 残留风险（🟡）

| 风险 | 位置 | 说明 |
|---|---|---|
| 面板块落默认池 | `stock.py:243` `asyncio.to_thread(builder)` | 6 块 `gather` 并发 ⇒ 最多 6 个 worker 落默认 22 槽池（含探针）。虽每块 4.5s 预算，但**与项目"重计算必须走 compute_pool"的规则不一致**（audit P1-b 只改了 market/etf/datacenter，stock 面板漏改） |
| `to_thread` 分散 | `alerts.py`(603/620/642/646)、`app_settings.py`、`datacenter.py`(760/836/837/839/846/1093) | datacenter 主扫描已加 `compute_slot_ctx`；其余为轻量 IO，风险低。建议盘点是否有 parquet 全扫走 to_thread |
| RT 预算余量 | `RT_BUILD_TIMEOUT_SECONDS=15s` | 实测最坏 11.75s（78%）；源码注释称**正常路径 ~14s = 93%**。外部源全可达时**逼近预算**，抖动即超时降级。属"预算与工作量匹配度"隐患 |
| 静默消失复现 | — | 未复现。判据（`grep -c starting` vs `grep -c shutdown`）经 socket 超时修复后应平衡；本次未做多轮启停压测（受端口/环境限制），列为局限 |

---

## ⑦ 局限

1. **认证端点未实测**：`/etf/*`、`/watchlist/*`、`/screener/*`、`/market/quotes`、`/overview`(compat) 均需 Token/角色，无法匿名计时。其预算判定基于源码判据（是否传 `background_build`），**未做端到端实测**。
2. **共享 Redis**：所有测量在共享 db0 上做，purge 会影响其他成员 15s 内读缓存（已提前告知 team-lead）。可能存在其他成员的并发写入干扰冷路径纯度。
3. **进程内缓存干扰**：`_build_daily` 的 ai_stats 缓存是进程内（TCP 连接级别的独立进程可能有/无热），HTTP 测量（2.59s）与直接调用真冷（4.76s）已分别给出，但无法在**同一 HTTP 进程**内制造"完全空 ai_stats + 空 Redis"的双冷态。
4. **外部源不可达**：本次实测 RT 时东财/新浪部分不可达（money_flow=unavailable），RT 墙钟受**熔断 fast-fail** 影响偏低；**正常路径（源全可达）预计更慢（注释称 ~14s）**，实际余量可能比实测更紧。
5. **未做多轮进程启停压测**：端口环境限制（8000 被占、8021 为临时），"静默消失"仅做静态判据分析，未做 22 路并发 + 退出压测复现。
6. **`_throttle` 锁粒度**是 O2/O3 的结构性根因，其修改影响面大（全站外呼节奏），本次仅指出，未验证改法。
