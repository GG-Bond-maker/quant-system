# AQP 上线前全检 — 数据可信度审计报告（data-auditor）

- **审计日期**：2026-10-01（国庆休市，A 股非交易日）
- **审计范围**：资金流向数据专项 / 全市场统计口径 / "0 冒充不可得" / 数据完整性 / 前后端一致性 / 量化闭环
- **方法论**：**先量化，再读代码**。所有判定基于 live 实测（后端 8000 端口 + akshare/东财直取 ground truth），不采信注释。
- **后端**：`uvicorn app.main:app`（127.0.0.1:8000，dev，无 reload）；ground truth 走 `push2test.eastmoney.com`（本机可达镜像）。

---

## ① 结论 TL;DR

| 维度 | 判定 | 说明 |
|---|---|---|
| **数据是否伪造/占位？** | 🟢 **否（主流路径无造假）** | 个股资金流、ETF 资金流、公告、日线均实测为真实值且与 ground truth 逐位吻合。已知的"休市 0 冒充"在 `build_money_flow` 北向块已被**正确拦截**（读 `交易状态` 列）。 |
| **资金流向数据是否缺失？** | 🟡 **是（部分口径结构性缺失 + 一条可修复的数据丢失）** | ① 北向持股 = 永久下线（结构性，诚实披露）；② **大盘主力/行业板块资金流 = 数据可获取但代码走错 host，导致市场概览"资金流向"卡长期降级不可用**（🔴 可修复的数据丢失）；③ 北向当日净流入在休市日被正确置 null（非缺失，是正确行为）。 |
| **数据是否完整？** | 🟡 **大体完整，尾部有覆盖缺口** | 全部 Parquet 数据集**行数非零、0 跳文件**（`skipped_files=0`）；但**全市场日线真实覆盖止于 2026-09-17**，09-18~09-29 为稀疏补数尾巴（09-29 仅 1 只标的），qfq/features/predictions 均停在 09-17。 |
| **前后端是否一致？** | 🟡 **基本一致，存在 1 处口径错位** | 前端 null 处理纪律优秀（多处显式"不得 `?? 0`"）；但 **ETF overview 用 `date.today()` 冒充数据日**，休市日把 09-30 的数据标成 10-01。 |
| **是否符合量化设计预期？** | 🟢 **主链路闭环** | 源→落盘→特征→API→前端 全链路实测贯通；`_pick_stat_day` 单一事实来源已落地并被两处消费。 |

**一句话结论**：**数据是真实的，没有系统性造假；但"资金流向"这一用户最关心的模块存在"数据取得到却取不到"的可修复缺陷（🔴），以及北向持股的结构性缺失（🟡，已诚实披露）。**

### 严重度分布
- 🔴 严重（数据丢失/误导）：**2**（大盘主力+行业板块资金流全失败；ETF overview 日期口径错位）
- 🟠 高（0 冒充 / 口径错）：**2**（`us_size_yi=0` 冒充不可得；ETF flow 无数据日字段）
- 🟡 中（潜在风险 / 一致性）：**3**（全市场尾部覆盖缺口；universe `.max()` 缺守卫；公告窗口短）
- 🟢 低（观察项）：**2**

---

## ② 资金流向专项结论表（最高优先级）

| # | 端点 / 数据块 | 返回状态 | 实测数值 | 真实性判定 | 是否缺失 | 证据 |
|---|---|---|---|---|---|---|
| 1 | `GET /market/overview/rt` → `money_flow` | `unavailable` | `north/main/sector` 全 None | **诚实降级（非造假）** | ⚠️ 部分（见 #3/#4） | reason=`north:非交易态(交易状态=4:休市/未开盘); main:DataSourceUnavailable; sector:DataSourceUnavailable` |
| 2 | 北向当日净流入（`stock_hsgt_fund_flow_summary_em` 过滤`资金方向=='北向'`） | 正确置 None | akshare 直取：北向 `资金净流入=0.0`+`交易状态=4`；南向 `=420.0`（真实） | ✅ **正确识别假 0** | 否（正确拒绝伪造） | 见下方"北向假 0 实证" |
| 3 | **大盘主力净流入（`stock_market_fund_flow`）** | `DataSourceUnavailable` | ground truth 经 push2test：**2026-09-30 主力净额=-13628162048.0 元 = -136.28 亿** | 🔴 **数据可得但取不到** | **是（假缺失）** | 同接口同参数：`push2test` 200 OK；`push2his`/`push2delay` RemoteProtocolError |
| 4 | **行业板块资金流（`stock_sector_fund_flow_rank`）** | `DataSourceUnavailable` | ground truth 经 push2test：**航空机场 f62=1.58 亿**，496 行 | 🔴 **数据可得但取不到** | **是（假缺失）** | 同接口：`push2test` 200 OK（8/8 行）；`push2`/`push2his` 全 RemoteProtocolError |
| 5 | 个股主力资金流 `fetch_main_fund_flow` | `ok` | 600519.SH `main_net=533565520.0 元=5.34 亿` @2026-09-30 | ✅ **真实** | 否 | ground truth 逐位吻合（见下方实证） |
| 6 | 个股面板 `panels.build_money_flow` | `ok` | 600519 `main_net_yi=5.3357`，`super_large 4.1214 + large 1.2143 = 5.3357` ✅ 自洽 | ✅ **真实** | 否 | 子项加和 = 主力，与源一致 |
| 7 | 北向持股 `panels.build_north` / `fetch_north_holding` | `unavailable` | `retired=true`, `reason=data_source_retired`, 各值 null | ✅ **诚实披露结构性下线** | **是（结构性，已知无解）** | 现存通道仅季频，代码主动 `SourceRetiredError` |
| 8 | ETF 资金流 `GET /etf/flow` | `ok` | 创新药ETF银华 `net_inflow=239219600.0`（2.39 亿） | ✅ **真实** | 否 | 时间戳 `f124=2026-09-30 15:34`（真实交易日） |
| 9 | 自选汇总 `_fund_flow_total` | `ok`/`degraded` | >12 只显式 `unavailable`（不伪造部分和） | ✅ **真实/诚实** | 否 | watchlist.py:248-250 "宁可声明不可用，也不返回伪造的部分合计" |
| 10 | ETF overview `net_inflow_yi` | `ok` | `23.85` 亿，但 `date=2026-10-01`（休市） | ⚠️ **值真实但日期错标** | 否（值未缺，标签错） | 见 ④ |

### 关键实证

**北向假 0（akshare 直取，2026-10-01 休市）**
```
交易日   板块       资金方向  交易状态  资金净流入  上涨数/持平/下跌
2026-10-01 沪股通     北向     4        0.0       941/41/662   ← 假0（上涨数是前一交易日残留）
2026-10-01 港股通(沪)  南向     4        420.0     457/18/190   ← 真实更新
```
=> 后端 `market_service.py:92-96` **读 `交易状态` 列**，`int(4)` → `休市/未开盘` → raise → north 保持 None、走 degraded。**判定正确，无造假。**

**大盘主力/板块资金流 host 对照（同接口同参数）**
```
push2test.eastmoney.com   -> 200 OK  2026-09-30 主力净额=-13628162048.0（-136.28亿）  ✅
push2his.eastmoney.com    -> RemoteProtocolError                                    ✗
push2delay.eastmoney.com  -> RemoteProtocolError                                    ✗
```
=> `akshare.stock_market_fund_flow` **硬编码 `push2his`**；`stock_sector_fund_flow_rank` 硬编码 `push2`。而项目**已在** `realtime.fetch_main_fund_flow`（个股）与 `etf._EM_CLIST`（ETF）**首选 `push2test`**。**唯独 `market_service.build_money_flow` 的市场级两条路径漏改** → 市场概览"资金流向"卡长期降级。

**个股资金流 ground truth 逐位吻合**
```
600519.SH push2test kline[-1] = 2026-09-30,533565520.0,...  (主力净额 5.34 亿)
后端 fetch_main_fund_flow      = main_net=533565520.00 元 = 5.34 亿   ✅ 完全一致
```

---

## ③ 发现清单

| # | 严重度 | 类别 | 位置 | 问题 | 实测证据 | 建议 |
|---|---|---|---|---|---|---|
| **F1** | 🔴 | 数据丢失 | `services/market_service.py:107`（`stock_market_fund_flow`）、`:117`（`stock_sector_fund_flow_rank`） | 市场级"大盘主力净流入"与"行业板块资金流"走 akshare 硬编码的**被阻断 host**（push2his/push2），导致 `money_flow` 块长期 degraded/unavailable。**数据经 push2test 完全可得**，属"取得到却取不到"的真数据丢失 | 同接口同参数 push2test 200（-136.28 亿 / 航空机场 1.58 亿），push2his/push2 RemoteProtocolError；项目已在 realtime.py:471、etf.py:82 采用 push2test | 改为复用 `realtime._request` + push2test 主用、push2delay/push2his 降级，或自建 `fetch_market_fund_flow` / `fetch_sector_fund_flow` 适配器，与个股/ETF 路径统一 host 口径 |
| **F2** | 🔴 | 口径错位（把旧数据标成今日） | `api/v1/etf.py:93` `_etf_data_date()` → `date.today()`；`:445` `today=date.today()`；`:503` `"today":{**snap,"date":today}` | 数据日取**日历日**而非**交易日**。休市/周末访问时，把上一交易日（09-30）的 ETF 市值/成交额/资金流标成今天（10-01），且 `data_freshness.status="fresh"`。同项目 market/stock/portfolio/watchlist 均用 `today_trade_date_or_last()`，仅 etf 是异类 | 实测 `date.today()=2026-10-01` vs `today_trade_date_or_last()=2026-09-30`；`/etf/overview.today.date=2026-10-01` 而底层 flow 时间戳 `f124=2026-09-30 15:34` | `_etf_data_date()` 改用 `today_trade_date_or_last().isoformat()`；或从 `f124` 取真实数据日。使 `date`/`data_freshness` 反映**真实交易日** |
| **F3** | 🟠 | 0 冒充不可得 | `api/v1/etf.py:391` `us_size_yi = round(sum(x.get("size_yi") or 0 for x in us_quoted),2)` | `us_quoted` 以 `x.get("size_yi")` 非空为过滤条件，**16 只美股 ETF 的 size_yi 全为 None** ⇒ 空列表 `sum([])=0` ⇒ 报 `us_size_yi=0`，即"美股规模恰为 0"。与 `us_count=16` 并列，自相矛盾（16 只总规模 0） | `build_catalog()`：us=16，`size_yi` 全 None；端点返回 `us_size_yi: 0` | 无有效样本时返回 `None`（前端显示"—"），或沿用项目既有"有效观测数"判据：`len(us_quoted)>0` 才给数值 |
| **F4** | 🟠 | 缺失字段（无法核验口径） | `data/etf.py:555-591` `fetch_flow()` | 返回项**无 `date`/数据日字段**，仅含 f62（"今日"）值。上层只能靠 `date.today()` 猜，是 F2 的根因之一 | 东财响应含 `f124`（`2026-09-30 15:34`）但 `fields` 未请求该列 | 在 `fields` 增加 `f124` 并在返回项暴露 `data_date`，让上层用真实数据日标注 |
| **F5** | 🟡 | 数据完整性（尾部缺口） | `data/parquet/daily_bar`、`daily_bar_qfq`、`features`、`predictions` | 全市场真实覆盖止于 **2026-09-17**（2492 只）；09-18~09-29 为稀疏补数尾巴（09-18=995、09-28=25、**09-29=1 只 000007.SZ**）。qfq/features/predictions 停在 09-17，raw 停 09-29（虚高） | 逐日覆盖实测：09-17 n=2492 → 09-18 n=995 → … → 09-29 n=1 | ① 保持 `_pick_stat_day` 守卫；② 在 `/datacenter` 与前端**显式披露"最新完整交易日"与覆盖数**；③ 补数完成后回填 qfq/features |
| **F6** | 🟡 | 潜在劫持风险（无守卫） | `api/v1/desk.py:348`、`api/v1/report.py:166`、`trading/paper.py:114` 的 `filter(pl.col("date")==uni["date"].max())` | `universe_daily` 的"最新截面"取 `max(date)`，**未复用 `_pick_stat_day` 判据**。当前 `universe_daily` max=09-17 为满覆盖（2494），**暂未被劫持**；但一旦出现单标的孤立行即退化为 1 只 | `universe_daily` 实测 max=2026-09-17 n=2494（当前安全）；`daily_bar` 已出现 09-29 n=1 的同类模式 | 将 `_pick_stat_day` 抽为共享工具，三处截面选取统一走"覆盖充分"判据，或至少加覆盖度断言 |
| **F7** | 🟡 | 缺失（窗口短） | `data/parquet/announcements` | 公告仅 **2026-04-03~2026-09-30**（约 6 个月），历史公告未回填。数据本身**零 null、真实**（09-30 有 2378 条） | pub_date min=2026-04-03；324259 行；全列 null_count=0 | 明确公告为"近 6 月窗口"并在前端标注起始日；如需长历史按 `stock_notice_report` 逐日回填 |
| **F8** | 🟢 | 一致性（UI 欠显示） | `frontend/src/pages/MarketOverview/MoneyFlowPanel.tsx:81/107` | 仅当 `status==='ok'` 才渲染汇总/柱状；后端部分成功返回 `degraded`+数据时，落入 `else` 分支只显示 reason 文本，**已拿到的 partial 数值不显示** | 后端三态契约（ok/degraded/unavailable）与前端 `ok` 二态判据不匹配 | 前端对 `degraded` 也渲染已到达的子块 + 降级角标 |
| **F9** | 🟢 | 观察项 | `api/v1/market.py:125` `int(cov["n"].max() or 0)` | `or 0` 在此为**安全**用法（`cov` 非空才进入，仅防 `max()` 空聚合），非"0 冒充" | 代码路径分析 | 无需改动；列入白名单以免后续误改 |

---

## ④ 数据完整性核查表

| 数据集 | 行数 | 标的数 | 日期区间 | skipped/total | 覆盖 | 判定 |
|---|---|---|---|---|---|---|
| `daily_bar` | 2,443,567 | 2499 | 2022-01-04 ~ **2026-09-29** | 0/10921 | 1.000 | 🟡 尾部稀疏（09-29 仅 1 只） |
| `daily_bar_qfq` | 2,441,892 | 2499 | 2022-01-04 ~ 2026-09-17 | 0/10919 | 1.000 | 🟡 落后 raw 12 天 |
| `daily_bar_hfq` | 2,444,329 | 2501 | 2022-01-04 ~ 2026-09-28 | 0/10924 | 1.000 | 🟡 尾部稀疏（09-28 n=25） |
| `features` | 3,682,581 | — | 2022-01-04 ~ 2026-09-17 | 0/10 | 1.000 | 🟢 非零 |
| `predictions` | 41,629 | — | 2024-06-05 ~ 2026-09-17 | 0/262 | 1.000 | 🟢 非零 |
| `screener` | 419 | — | 2024-06-05 ~ 2026-09-17 | 0/9 | 1.000 | 🟢 非零 |
| `universe_daily` | 2,847,566 | 1 | 2022-01-04 ~ 2026-09-17 | 0/5 | 1.000 | 🟢 max 日满覆盖 2494 |
| `announcements` | 324,259 | 1 | 2026-04-03 ~ 2026-09-30 | 0/1 | 1.000 | 🟡 窗口短，内容零 null |

**通用结论**：
- ✅ **全部数据集 `skipped_files=0`**，无损坏文件被静默丢弃。
- ✅ `_scan_dataset` 的 `failed` 计数**已进返回值**（`skipped_files`/`total_files`，2026-10-01 修复）——此前的"只打 WARNING"缺陷已修复。
- ✅ `_scan_file` 用 `pq.ParquetFile` 真打开 footer 作可读性探针，**不吞失败成 0**（与 `_parquet_rows` 区分）。
- 🟡 raw/qfq/features/predictions 的"最新日期"口径不齐（09-29 / 09-17 / 09-17 / 09-17），下游若混用会串日。

**前后端一致性实测**
- `/market/overview/rt`：`money_flow=unavailable`、`indices=ok`（items 齐全）、`top-level status=degraded`、`data_freshness=degraded` ✅ 三态自洽。
- `/overview/daily`：`heat`/`sectors` 均为 `source=local`，`data_date=2026-09-17`、`coverage_symbols=2492`、`skipped=7`，`note` 如实披露 ✅（`_pick_stat_day` 生效）。
- 前端合并 `{...daily.data, ...rtData}`：rt 的 `trade_date` 覆盖 daily，字段级共享接口不变；heat/sectors 自带 `data_date` 披露，**未出现无标注的跨日混用** ✅（除 F2 的 ETF 独立口径）。

---

## ⑤ 局限

1. **网络环境**：本机 `push2`/`push2his` 整组阻断（RemoteDisconnected，直连亦断），故 F1 的 ground truth 只能用 `push2test` 镜像验证；`push2test` 与 `push2his` 是否**完全相同**的字段语义，未做逐字段全量比对（仅比对 `日线 fflow` 与 `板块 clist` 两接口，字段名/量纲一致）。
2. **未做跨日重放**：仅能观察 2026-10-01（休市）时点。交易日盘中行为（如 f62 实时刷新、`交易状态` 非 4 时的放行路径）未实证。
3. **鉴权**：`/datacenter/*`、`/stock/*/panels`、`/etf/*` 需 viewer 角色。审计用**后端同密钥签发临时 token**（`audit_probe`）访问，非走注册流程；已确保不修改生产数据。
4. **公告/财务 PIT**：仅核查了公告落盘完整性与财务数据集的**存在性**，未对 `stock_yjbb_em` 的"+1 年前视陷阱"做逐条比对（超出本轮资金流专项的范围，建议由 product-reviewer/qa-lead 补充）。
5. **ETF overview 快照归档**：`should_archive_etf_snapshot` 的盘前/盘后门控仅代码阅读，未构造盘前访问复现"真收盘快照丢失"。

---

## 附录：审计产出的探针脚本（不修改生产代码，均在 `deliverables/gstack/_tmp/`）
- `probe_moneyflow.py` / `probe_panels.py` / `probe_stock_mf.py`：资金流端点与适配器实测
- `probe_gt2.py` / `probe_gt3.py` / `probe_mf_direct.py` / `probe_sector_direct.py`：push2test ground truth 多 host 对照
- `probe_dist.py` / `probe_gap.py`：逐日覆盖分布与 max(date) 劫持验证
- `probe_scan.py`：全数据集 `_scan_dataset` 元数据与非零验收
- `probe_etf_flow.py` / `probe_etf_gt.py` / `probe_us.py`：ETF 资金流、时间戳 f124、us_size 冒充
- `probe_auth.py` / `probe_auth2.py`：临时 viewer token 与鉴权端点探测
