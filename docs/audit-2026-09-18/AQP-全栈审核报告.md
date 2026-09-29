# AQP 全栈审核报告（2026-09-18 轮次）

> **审核对象**：Alpha Quant Platform（A 股量化研究平台）· HEAD `f7c9410`（master，2026-09-20）
> **审核日期**：2026-09-21
> **审核方式**：13 个批次并行深审（后端基础设施/领域/数据/存储/ML/回测/API/编排 + 前端基础设施/行情/量化页/运维页 + 跨层对账 + 策略专项），**可运行代码**：每条标「确定」的结论都实机执行并附命令与真实输出。
> **基线与环境边界**：见 [00-BASELINE-AND-VERIFICATION.md](00-BASELINE-AND-VERIFICATION.md)（父审核员自测，含沙箱两项限制与全量套件 1065 passed / 6 failed 的逐例根因分解）。
>
> **本报告遵循的纪律**：只报能定位到文件+行号+触发条件的问题；每条给最小验证方法与修法；不把有意设计当 bug；与既有台账重复的条目一律标注「已知」并给出交叉引用。
>
> **状态：全部 20 个批次已并入完毕**（B0/B1/B2/B3a/B3b/B4a/B4b/B5/B6/B7a/B7b/B8/B9a/B9b/B9c/P4/P2-strategy/P3-dedup/P5/REDTEAM）。**§9.2 已按简报第 7 节第 4 条显式标注 9 条被本轮推翻的历史判断；§9.3 另登记 5 条红队对「本报告自身结论」的证伪（含 1 处父审核员自我更正，见 §8.3b）。** 本报告已定稿。

---

## 1. 总览

### 1.1 问题数统计（按严重度与类型）

> **计数口径**：下表为**已并入批次**的原始条目数（同根因的跨批重复尚未归并，归并视图见 §8.4）。**全部 20 个批次已并入完毕**（B0/B1/B2/B3a/B3b/B4a/B4b/B5/B6/B7a/B7b/B8/B9a/B9b/B9c/P4/P2-strategy/P3-dedup/P5/REDTEAM）。

| 严重度 | Bug | 一致性/契约 | 策略 | 死代码/未接线 | 合计 |
|---|---|---|---|---|---|
| **P0** | 4 | 1 | 1 | 0 | **6**（原 8；P0-4、P0-6 经红队复核下调为 P2） |
| **P1** | 33 | 12 | 4 | 0 | **49**（编号至 **P1-51**；P1-8 经红队证伪、**P1-47 经实测证伪**，两条均已下调；**P1-51 为第 18 轮修复过程中新发现**：`scripts/alerter.py` 缺 `import sys` ⇒ 运维告警脚本必然 NameError） |
| **P2** | 58 | 69 | 14 | 8 | **149**（含下调的 P0-4、P0-6、P1-8，以及 P5 的 17 项静默 + 10 项零消费） |
| **P3** | 24 | 36 | 8 | 50 | **118** |
| **合计** | 119 | 118 | 27 | 58 | **322**（原始条目；归并后 ≈ 205，见 §8.4） |

**红队复核引起的定级变动（详见 §9.3）**：P1-8 **P1→P2**、P0-6 **P0→P2**、P0-4 **P0→P2**、P0-5 **维持 P0 但修正触发条件与影响面**。**P5 批次另新增 P1-50**（`/market/index/kline` 无鉴权）并**更正了基线的一处裁决**（见 §8.3b）。**第 18 轮修复过程中另新增 P1-51**（`scripts/alerter.py` 缺 `import sys`，实测该脚本必然 `NameError`；见 §3 末行与 `FIXES-APPLIED` 行 55）。

> ## 🔧 修复进展（2026-09-21 起，详见 [FIXES-APPLIED.md](FIXES-APPLIED.md)）
>
> **P0 六条已全部处置**：P0-1 / P0-2 / P0-3 / P0-5 / P0-7 / P0-8 已修并各自带防回归用例；
> P0-4 / P0-6 经证据复核下调 P2（编号保留以免引用错位）；**P0-4（下调后 P2）的两条腿
> ——复权口径与基准口径——已于第 10 轮补齐披露并测试固化**。
> **P1 已完成 35 条**：P1-1（换手衰减被反复扣）、**P1-2（策略回测缓存键漏 `use_legacy_engine`：两套引擎互取缓存）**、
> **P1-3（模拟盘无持仓校验：裸卖凭空造现金）**、**P1-4（退市接线：披露不实 + 折价损失不可见）**、
> **P1-5（`monthly_monotonic_ratio` 方向反了：完美因子得 0%、反向得 100%）**、
> **P1-15（快照静默截断 200 只：第 201 只起预警永不触发且不可观测）**、
> P1-28（续传被启动抹掉）、**P1-29（晚间例行把 FAILED 记成 done ⇒ 榜单停更而日报照常）**、
> **P1-30（外部源部分降级被当健康：`money_flow` 三态退化）**、
> **P1-31（`/sync/fetch` TOCTOU：已回 `started:true` 却零执行且不留案底）**、
> P1-32（ETF RBAC 契约）、**P1-34/B7a-03（未做 ST/停牌过滤的榜单被当成已校验结果）**、
> **P1-35（日历非法日期被报成系统故障 + 历史榜错标请求日期）**、
> **P1-36（空涨跌分布造「情绪 中性 50」：零数据造结论）**、
> P1-39（训练口径未落盘 ⇒ 门禁跨口径比 RMSE）、
> P1-40（`write_partition` 读-改-写丢行）、P1-41（`_audited` 一次性自愈）、
> **P1-42（回测数据集 `universe_daily_bt` 未接线：夜间养的是另一份数据）**、
> **P1-43（`/health*` 恒 200 ⇒ K8s readiness 探针空转；compose 覆盖镜像 healthcheck）**、
> P1-44（部署文档幻觉）、P1-50、R5（未声明公开路由不变量）、
> **P1-25（分层多空/IC 衰减口径 + 前端图表恒空）**、**P1-26（TopK 三项指标小 100 倍）**、
> 以及 B5-10（费率/品种判定单一来源：ETF 免征 + 印花税法定分段 + 补过户费）、
> **B2-8（涨跌停价半分边界少 1 分）**、**B5-16（不可撮合订单静默丢弃）**、
> **B5-17（策略路径 decay 静默失效）**、**B5-18（过户费缺失）**、**B5-20（逐笔 pnl 只减卖出费用）**
> 与 B5-08 区域的"换手取腿漂移"；另修 **§4.8 序 16**（`Query` 当 `refresh` ⇒ 每次 `/settings`
> 清空统计缓存、逼出 27~30s 重扫，即前端"冷算 27~30s"的根因）。
> 并**撤回了本报告自身的两条错误结论**：P1-47（§9.3 **R-6**）与
> B5-08（§9.3 **R-7**）；**更正了本报告的三处措辞/推导**：
> ①「买卖均值」口径被误说成等价于含现金的 Σ\|Δw\|/2（仅现金平衡日相等）；
> ②「退市三条路径全部空转」实为 **1 条空转**（强平/haircut 由面板缺席驱动、实测可达）；
> ③「两套面板含 `delist_date`」不成立（**两套都没有该列**，见 §7-S2）。
> 另在修复过程中**自查证伪了一条本轮假说**：`CapacityAttribution` 的 16 处 `fmtPct` 并非
> 同类单位缺陷（该文件有自带 `×100` 的局部实现），故**不计入发现数**。
> **新增两条「报告细节不准确」更正**：P1-3 原文称"`paper.py` **无任何测试文件**"——实为
> `tests/test_production.py:143-198` 已有 `TestPaperDesk` 4 例（缺陷本身**成立且已独立复现**：
> 零持仓卖单实测成交 10,700 股、超持仓单卖出 115,200 股 vs 持仓 10,700 股）；
> P1-5 原文称测试断言"恒真"——准确，`>= 0.0` 对比例确实恒真（已替换为双向实质断言）。
>
> **P1-42 接线过程又暴露两处新缺陷**（均属"接线才可能触发"，已一并修复，见 FIXES-APPLIED）：
> ① 无 `daily_bar_hfq` 时 `build_universe_bt` 硬失败会掐断整条夜间流水线（含当日榜单）⇒ 改**显式跳过 + 留痕**；
> ② instrument 表 `list_date` **全为 NULL** 时 polars 推断为 `Null` dtype，`date - null` 抛
> `InvalidOperationError` 直接崩（全新库/未跑 enrich 时必现）⇒ 改在载入处 `cast(pl.Date)`（含变异反证）。
> 另**加固**了一条跨用例耦合的既有 flaky 用例：`test_overview_async` 原只发一次请求、依赖兄弟用例
> 预热 LRU，而缓存键**含交易日**（判定实验：交易日 +1 天后 `from_cache=False`）⇒ 跨日界必红；
> 已改为自证式（自己连发两次），并实测**排除** 15s 短 TTL 路径（载荷无顶层 `status`，走完整 TTL）。
>
> **第 9 轮（R9/S14 错误码治理）**：15 个裸码（两端码表都不含）全部登记，`40017` 按语义拆为
> 40017/40018/40019，`sync/tasks/{id}` 不存在改 `40400`，`alerts.py`/`screener.py` 另有 5 处字面量→常量；
> 两端码表**逐值逐名对账**（补齐前端 `50001` 与 10 个回测码）+ 未知码前端显式标记；
> 新增**AST 守卫**（含 7 条变异反证，杜绝第 22 个裸码）。
>
> **第 10 轮（新发现 2 类 + P0-4 收尾）**：
> ① **schema 漂移（分区存在但缺列）⇒ 裸 `50000`** 同族 **5 处**（`watchlist._bars_for`、`screener._watchlist_quotes`、
> `screening.filter_universe`、`desk.capacity`、`paper.screen_universe_candidates`）——这些端点**本已定义**合法降级
> （51001 / 外部源回退 / 价格 None / applied=False 披露），只是判据漏了"列维度"；现加共享判据
> `missing_columns`/`require_columns` 并逐处修（**发现路径**：第 9 轮一次子集运行的 6 条失败被判定为
> "会话共享 DATA_ROOT 的顺序产物"，但未放过其中的健壮性线索，本轮用私有 DATA_ROOT 复现定性）。
> ② **B7b F6 复权口径回退零披露**（P2）：`price_basis` 恒存在 + 前端不再硬编码 "QFQ"。
> ③ **P0-4 下半条基准口径**：`benchmark_basis` 标注构造基准（覆盖获取失败/无重叠/对齐失败三条退化路径）。
>
> **数据治理（本轮新增，经用户裁决）**：有效历史自 **2022-01-01** 起、不回填更早数据；
> 2022 前分区 **3,417 文件 / 2,584,222 行 / 40.9MB** 已移入 `data/_purged_pre2022/`
> （**可 `--restore` 回滚**，非物理删除；SQLite `trade_calendar` 按决定保留），
> 并由 `universe.py:HISTORY_START` 闸门防止再生（含变异反证）。
> 随后重建 `universe_daily_bt`：**1,133 → 2,495 只**、**1,283,107 → 2,851,203 行**、
> 最新日期 **2026-09-04 → 2026-09-18**。
>
> **全量测试套件：修复前 `1065 passed / 6 failed` → 修复后 `1647 passed / 0 failed / 8 skipped`**（第 18 轮收口实测，467s）。
> 第 7–8 轮（P1-35 / P1-36 / B7a-06 / P1-43 / B7a-08 / B7a-09）新增 3 个测试文件 39 条 + 1 条专条，
> 增量对账 `1328 → 1363 → 1368`（+35、+5）**精确等于新增用例数**；
> 第 9 轮（**R9/S14 错误码治理**）新增 1 个文件 14 条（含 7 条变异反证），`1368 → 1382`（+14）；
> 第 10 轮新增 2 个文件 20 条（**schema 漂移缺列守卫** 10 条含 1 条缺陷本体反证；**F6 复权口径 + P0-4 基准口径** 10 条），
> `1382 → 1392 → 1402`（+10、+10）；
> 第 11 轮新增 1 个文件 9 条（**P1-14 ETF 资金流口径**，含真实观测样本复算），`1402 → 1411`（+9）；
> 第 12 轮新增 1 个文件 17 条（**S4 监控触发器口径** P1-17/P1-21/R5 + 前端披露锁）并在 `test_monitor.py`
> 新增 1 条、改写 3 条旧契约断言（集成锁附**变异反证**），`1411 → 1429`（**+18 精确对账**）。
> 第 13 轮新增 2 个文件 23 条（**组合层约束** P1-13/B2-12 共 13 条；**rf/基准口径 + μ 披露**
> B2-16/B2-11 共 10 条），`1429 → 1440 → 1450 → 1452`（**+11、+10、+2 精确对账**，末两条为
> `cov_window<20` 潜在耦合守卫与"非空披露字段"的 JSON 序列化/接线锁）；本轮亦由探针
> 暴露一条**审核文字未点明的新缺陷**（默认 `weighting="equal"` 从不执行 `weight_cap`），
> 见 §S6 与 `FIXES-APPLIED.md` 第 50 行。
> 第 15 轮（**P1-18 `g1_*` asof 不稳定的邻接 universe 冻结**）新增 1 个文件 12 条，
> `1452 → 1464`（**+12 精确对账**）；该轮先由探针**证伪了我自己的初版机制假说**
> （"行归一化分母"实为按行约掉，真正机制是"哪些邻居当天有取值"），再据此把修复目标
> 从"保持数值不变"改为"可挡的挡住 + 不可复原的**拒绝静默改写**"（含"首次冻结于既有
> 历史之上"这一过渡期守卫，否则部署首夜就会静默改掉全部历史 `g1_`），自纠过程见
> `FIXES-APPLIED.md` 第 15 轮自查表。
> 第 17 轮（**P0-6 空榜契约裁决**，人工指定"按最优解解决"）新增 1 个文件 11 条，
> `1464 → 1475`（**+11 精确对账**）；**不采纳**"`total==0` 一律 `unavailable`"
> （会把"真跑了但当日无匹配"误报为数据不可用），改为按 `universe_filter.board_rows`
> 这一可判定事实拆成 `unavailable/empty_board` 与 `ok/no_matching_signals` 两态，
> 既有 09-14 断言**零改写**、`board=bse` 不再谎报"当日无匹配信号"。
> **第 18 轮（§8.2 收官批次：第 6/7/8/17/18/19/20 项 + §8.2b 契约类）** `1475 → 1647`（**+172**），
> 由 7 个子代理并行产出、父审核员逐条复核（`mypy app/` 归零 + `ruff app tests scripts` 全仓清零两条硬门禁
> 均由父侧在子代理收工后重跑，并因此修掉子代理引入的 **4 处 mypy 错 + 5 处 F401/F811**）。
> **首跑 2 failed，两条都不是"重跑变绿"**：①`test_delist_wiring` 的用例断言了**全库**计数，而
> `test_delist_liquidation.py:107` 会清空 `instrument` 全表（共享同一 SQLite）⇒ 字典序前缀复现
> `assert 4 == 3`，改为**作用域计数**并保留"披露 == 同源实测"不变量（前缀 252 passed）；
> ②`test_write_endpoints_smoke.py:630` 的允许集合未含新引入的 `ERR_EXPR_INVALID=53001`
> （该端点此前一律 40000 冒充"参数错"）⇒ 契约对齐（守卫内容不变）。
> 本轮另在修复过程中**新发现 P1-51**（`scripts/alerter.py` 缺 `import sys` ⇒ 文档推荐的运维脚本
> 必然 `NameError`，`README.md:203` 是失效入口），以及一条**审核文字未点明**的缺陷
> （`/ops/quality-scan` 的 `year = req.year or 2026` 硬编码 ⇒ 2026 年后默认静默指向过期年份却回
> `n_issues: 0`，而请求体描述写的是"默认最新一年"）；两条均已修并各自带防回归用例。
> 此前连续 4 轮稳定残留的 3 条 `test_pipeline.py` 失败系**沙箱命名管道限制**
> （`core/logging.py:46,66,78` 硬编码 `enqueue=True` ⇒ `SimpleQueue` 需建命名管道），
> 本轮最后两次运行该限制未生效而转绿——**未做任何针对它的代码改动，非本次功劳**，
> 限制若再现这 3 条会重新变红（环境产物与产品缺陷必须分开记账）。
> 第 12 轮额外发现并修掉一条**真实时间依赖型测试脆弱性**
> （`test_overview_heal_chain.py::test_redis_ttl_semantics_on_degraded_path` 断言精确 TTL，
> 而 `lru_ttl` 对剩余量做 `int()` 截断、其 `_Clock` 又用真实时间源 ⇒ 跨时钟刻度即假红；
> 本机实测刻度中位 **7.604ms**、真实路径 **3/3000 = 0.10%** 命中、冻结后 **0/3000**，
> 修复不放宽任何断言），未修改产品代码。
> 新增**22 个测试文件 / 248 条用例**，另修改 6 个既有测试文件（含
> `test_sync_integrity.py` / `test_pipeline.py` / `test_api.py` / `test_backtest_cache_key.py` /
> `test_p2_backtest.py` / `test_panic_rootcause_guard.py` 的契约与加固更新）；
> `ruff check app --select F,E9` 全通过（`app` 域 5 条均为基线既有；
> 本轮改动文件 F,E9 = 0）；`tsc --noEmit` = 0。
>
> **⚠️ 三处口径变更需知情**（都会改变历史回测数字，见 §7-S1 与 FIXES-APPLIED）：
> ① 印花税改为**法定分段**（2023-08-28 前 1‰、之后 0.5‰，ETF 免征）；
> ② 新增**双边过户费** 0.01‰；③ 涨跌停取整修正（**存量 `universe_daily` 分区需重建才生效**）；
> ④ 策略路径开始**真实收取** `decay_bps`（此前 0 元）。2016–2023-08 区间的成本此前被低估约一半。

诚实统计（按简报第 7 节要求，不含凑数）：
- **确定要修**：**约 280 条**（均有实机命令与真实输出，或纯代码链可静态确证且无外部依赖；其中带实测输出的 ≥ 170 条）。
- **疑似待验证**：**约 20 条**（逐条标注「疑似」并给出验证方式；**不计入「确定」**）。
- **建议但不紧急**：**约 26 条**（§7 的 S1–S8 + §7.9 的 T1–T8 + 其余）。
- **与既有台账重复**：**31 条**（P3-dedup 逐条对账：本轮新增 16 / 已登记未修 8 / 已修但回归 2（含 1 条存疑）/ 历史判断被推翻 9，见 §9/§9.2）。
- **另有 7 条"框架层/确定性"结论被证伪**（R-1…R-7，见 §9.3；其中 **R-6 与 R-7 是父审核员在修复阶段自查发现的**），**另有 1 处父审核员自己的计数误判已在 §8.3b 保留自我更正过程**（`/market/index/kline` 的"8→9"更正本身是错的，实为裁决错误而非计数错误）。

### 1.2 一句话结论

平台**工程完成度高、防御性设计明显优于同类项目**（panic 兜底链路完整、SSE 一次性 ticket 威胁模型逐条落地、Redis 双存储域不降级、契约信封全域统一、前端 strict+零未用变量、表达式白名单实测不可绕过、LGBM 侧 purge 纪律正确），但存在**四类系统性问题**：

1. **「交付了代码」≠「功能可达」**：至少 **9 处功能声称存在但生产上恒空转**（退市回填、公告写入、PIT 财务、文本因子入模、watchlist 写入、API Token/会话撤销、分红送转、**同步断点续传（已被修复又被抹掉）**、**ML 的 walk-forward 与波动率倒数加权**），且其中 4 处**读路径已接线**——数据契约的另一半缺失，用户看到的是空数据而不是错误。
2. **同一规则多份实现导致口径漂移**：费率 **8 份拷贝**、涨跌停 **2 套实现**（实测 2024 年 `limit_down` **0.39%** 的行与交易所口径差 1 分）、印花税 ETF 豁免 **4 处免 / 1 处收**、夏普 **2 套无风险利率口径**、`feature_version` **3 套解析**、中性化 **2 套**（且未接线的那套会 IndexError）、**因子有效性度量 2 套（池化 IC vs 逐日截面 RankIC，实测偏差 270×）**。
3. **静默：截断不披露、降级不披露、失败改写成成功**：`quality-scan` 只扫 **8%（200/2499）**却返回 `n_issues:0`（且**单文件损坏会让整次扫描的 199 个分区结果全丢**）、`quotes_snapshot` 静默砍到 200 只（第 201 只起预警永不触发）、晚间例行把 FAILED 落成 `done`、`/sync/fetch` 抢锁失败不留案底、外部数据源故障逃逸成 `code=50000`、`limit` 无界或被当成 `LIMIT -1`。**P5 批次把这一条量化成了完整矩阵**：21 个带 `limit` 读端点全量实测 ⇒ **17 项「两端都没有披露字段」**（含 6 个**裸 list**）、**10 项「后端有、前端零消费」**（`rg "truncated\|failed_count" frontend/src` = **0**）、**3 项「有字段但值错」**；其中 **`datacenter/instruments` 的 `total` 竟是 `len(rows)`**（`limit=1 → total=1`，真实 5552，**总数随 limit 一起缩小**）、`market/overview` **当前生产态就处于 degraded**（"兼容概览六秒预算已用尽"+ 四块 `unavailable`）而前端 `DataFreshness` 组件**只接 3 个 prop 从不读 `status/reason`**、`ai_stats` 降级被 `KpiCards.tsx` 写死的 **"样本不足"** 文案伪装成样本问题。
4. **研究结论本身被污染，且最核心的策略假设不成立**：**因子分层/多空净值存在前视偏差**（P0-7，实测荒谬到 +7.6 亿%）、默认训练入口因 `xsec_demean=False` 产出 `best_iteration=1` 的退化模型（RankIC 差 5.8×）、门禁在比较**目标函数不同**的候选（RMSE 1.25×）因而**回滚与自愈通道同时失效**；而 P2 批次的实测给出**本报告最重要的策略结论**——真实换手 **Top-50 81×/年**、默认无摩擦口径对年化高估 **8–20pp**，且 **Top-50 毛超额仅 +5%/年 < 成本下限 8.2%/年 ⇒ 日频调仓在真实成本下净值为负**（这是设计层面的错配，不是参数问题）。
5. **运维路径可造成不可逆数据丢失**：P0-8（README 推荐的夜间 cron 会**删生产库**）、DEP-21（`DELETE FROM model_registry` 无 `WHERE`）、DEP-22（`restore.py` 无确认且 `extractall` 直接覆盖生产 `data/`）。

以及一个**比单条缺陷更值钱的发现**：**测试网在结构上无法发现「漏挂鉴权」**（§2.1 / §8.3 R5），同一缺陷已被三份历史报告登记却依然存在；另有 **9 条历史审核结论被本轮实测推翻**（§9.2），其中 3 条（`useChart`、特征 asof、错误码对齐）都是"检查了机制存在、没验证它在目标场景生效"。

---

## 2. P0 · 必须立刻修（**6 条**；原 P0-4 与 P0-6 经红队复核均下调为 P2，保留编号仅为引用稳定）

### P0-1 容器化部署路径不可用：`PROJECT_ROOT` 在镜像内退化为 `/`（**五个独立失败机制，实测由 B0 补齐**）
- **位置**：`backend/app/core/config.py:24,64,77,78,96,234-237` + `backend/app/core/compute_guard.py:11` + `backend/Dockerfile:42-48,56-57` + `docker-compose.yml:43,49-58,61-64,76-78`
- **链路（静态可确证，父审核员独立复核；B0 用 `docker-compose config` 实机解析 compose 佐证）**：
  - 镜像内 `config.py` 位于 `/app/app/core/config.py`（`Dockerfile:42-43` `WORKDIR $APP_HOME` + `COPY backend/app $APP_HOME/app`）⇒ `Path(__file__).resolve().parents[3]` = **`/`**（`parents[2]` 已是 `/app`）。
  - 于是 `LOG_DIR`=`/backend/logs`、`DATA_ROOT`=`/data/parquet`、`MODEL_ROOT`=`/data/models`、`SQLITE_URL`=`sqlite+aiosqlite:////data/sqlite/aqp.db`（`config.py:64/77/78/96`）。
  - compose 的挂载点是 `/app/data`、`/app/logs`（`:56-58`）——**与代码使用的路径 100% 错位**（`APP_HOME` 变量在运行期零读取方）；又 `read_only: true`（`:43`）+ 非 root `aqp(10001)`（`Dockerfile:49-52`）。
- **五个独立失败机制**（B0 逐条给出验证命令；**F1 与 F3 互相独立**，修一个另一个仍会崩）：
  1. **F1 EROFS**：`get_settings()` 在**导入期**就被调用（`compute_guard.py:11` 模块级 `get_settings().COMPUTE_CONCURRENCY`），其中 `config.py:234-237` 执行 4 次 `mkdir(parents=True)` ⇒ 只读根文件系统上 **`mkdir('/backend')` 必失败** ⇒ uvicorn **启动阶段即死**（无 lifespan、无日志、无 healthcheck 响应），容器无限重启。**⚠️ 红队修正**：**首个失败点是 `LOG_DIR` 的父目录 `/backend`（不是 `/data/parquet`）**——`import app.main` 即触发 4 次 `os.mkdir`，按 `config.py:234-237` 的顺序第一个撞墙的是 `/backend`。另：**不存在旁路**，要绕过需**同时**覆盖 4 个 env **且**去掉 `read_only`/补卷挂载。
  2. **F2 即便删掉 `read_only` 也失败**：uid 10001 无权在 `/` 下建目录。
  3. **F3 日志 sink 独立致死**：`logging.py:52-61` 的 loguru **文件 sink 无 `delay=True`**（立即 open）+ `main.py:41` lifespan 调用 ⇒ `LOG_DIR` 不可写即抛错，**且没有 stdout 降级**。与 F1 独立（F1 发生在 import、F3 发生在 lifespan）。
  4. **F4 DB**：`init_db.py:36,41` 在 `/data/sqlite` 建库/WAL 失败（该目录未挂载且只读）。
  5. **F5 全栈不可达（不只是降级）**：`compose:76-78` 的 `aqp-web` `depends_on: aqp-api: service_healthy`，而 api 永不 healthy ⇒ **web 容器根本不被创建，8080 无监听**。
- **触发条件**：`docker compose up -d --build`（compose 头部自述的「P0 最小三服务」部署方式，且 `run.md:241`/`README.md:160-166` 把它称为**生产部署方案** ⇒ 属**虚假可用性声称**，见 DEP-31）。
- **置信度**：确定（静态链路完整 + B0 的 `docker-compose config` 实机输出）；本沙箱**有 docker/compose CLI 但无 daemon**，故未实机起容器——已在报告显式标注。
- **最小验证（在有 daemon 的机器上）**：
  ```
  docker compose config --format json | jq '.services["aqp-api"].environment'   # → 仅 5 键，无 ENV/ADMIN_TOKEN/DATA_ROOT/LOG_DIR
  docker run --rm --read-only --tmpfs /tmp aqp-api python -c "from app.core.config import get_settings;get_settings()"   # → EROFS
  docker compose run --rm --entrypoint sh aqp-api -c 'ls -ld /data /backend/logs /app/data'                              # → /data 与 /backend/logs 不存在
  docker compose up -d && docker compose ps -a                                                                            # → web 未被创建
  ```
- **修法（四件事一起做，只改环境变量是不够的）**：
  ① `PROJECT_ROOT` 改为「显式环境变量优先、缺省时按存在性探测」（`AQP_PROJECT_ROOT`），Dockerfile 显式 `ENV AQP_PROJECT_ROOT=/app`；
  ② compose 挂载点对齐为 `/app/data`、`/app/logs`（或把代码路径改为 `/data`、`/backend/logs` 并同步挂载）；
  ③ **宿主目录 owner 必须是 10001**（构建期 `chown` 被 bind mount 遮蔽，DEP-08）——文档应给 `chown -R 10001:10001 ./data ./backend/logs`；
  ④ loguru 文件 sink 加 `delay=True` 并加 **stdout 兜底**（`except` 后降级为 stderr-only），使「日志目录不可写」不再致命。
- **顺带修**：`Dockerfile:56-57` 的 `/health/ready` healthcheck 被 `compose:62` 的 `/health` **覆盖**（死配置）；两者语义差异见 P1-43。

### P0-2 容器化部署的凭据姿态：默认 `ADMIN_TOKEN` + 可登录 + 仅 warn（**已知台账未修，本次补容器侧新证据**）
- **位置**：`backend/app/core/config.py:40-47,200-226,242-248` + `backend/app/core/auth.py:198` + `docker-compose.yml:49-55`
- **链路**：compose 的 `aqp-api` **没有 `env_file`**，Dockerfile **不 COPY `.env`** ⇒ 容器内 `env_file=/.env`（P0-1）也不存在 ⇒ `ADMIN_TOKEN` 取默认 `aqp-dev-token-change-me`、`ALLOW_ADMIN_TOKEN_LOGIN` 默认 `True`、`ENV` 默认 `dev`；`validate_runtime_safety` 只在 `ENV=="prod"` 时 raise（`config.py:210-223`），`dev` 下**只 warn**（:224-226）。而 `auth.py:198` 在 `ALLOW_ADMIN_TOKEN_LOGIN` 为真时接受该 token；JWT 密钥由 `ADMIN_TOKEN` 派生（`secret = "aqp-derive:" + ADMIN_TOKEN`，代码注释自陈「任何人都能伪造 admin token」）。
- **后果**：按文档部署出的实例，**任何能访问 8000 端口（或经 aqp-web 代理）的人 = admin**，且可伪造任意角色 JWT；同时 `ALLOW_REGISTRATION` 默认 `True` 允许自助注册。
- **置信度**：确定（代码链完整）；触发需网络可达（compose 仅 `expose` 8000，是否外露取决于前端 Nginx 代理与部署网络）。
- **最小验证**：`docker compose run --rm aqp-api python -c "from app.core.config import get_settings as g;s=g();print(s.ENV, s.ADMIN_TOKEN, s.ALLOW_ADMIN_TOKEN_LOGIN)"`。
- **修法**：compose 显式 `ENV=prod`（或至少把 `validate_runtime_safety` 的「默认 token」条件从 `ENV=="prod"` 提升为「非 loopback 监听即 fail-fast」）；`ADMIN_TOKEN` 默认值改为 `None` 并在缺失时强制报错；`env_file: .env` 补进 compose。
- **⚠️ 去重（P3-dedup 批次纠正了本报告初稿）**：本条**主体已登记 4 次**——`docs/audit/AQP_前后端逻辑审查_20260912.md:64`（F-12）、`docs/audit/AQP_架构审查_20260912.md:134`（O-03）、`docs/audit-2026-09-14/backend-architecture-review.md:187`（P1-7）、`docs/audit-2026-09-14/product-review.md:132,214`（P0-5）。**本轮真正的新增只有「容器侧」两角度**：① compose **无 `env_file`** 且 Dockerfile 不 COPY `.env`（已由 `docker compose config --format json` 的**工具输出**证实：api 环境仅 5 个 `REDIS_*`/`TZ` 键）；② `docs/项目开发文档.md` §13 把变量名写成 `APP_ENV`（真名 `ENV`）⇒ **照文档配置则 prod 闸门永不触发**（P1-44 / DEP-13）。**不得再按「全新问题」上报。**

### P0-3 `GET /datacenter/train/readiness` 完全无鉴权（**已知未修**，本次独立确认）
- **位置**：`backend/app/api/v1/datacenter.py:1124`（路由内省：无 `require_role` / `require_auth` 依赖）
- **现象**：匿名返回 `code=0`，泄露 torch/设备名、features 绝对路径、样本量、GNN 关系边数；兄弟端点 `/train/status` 匿名正确返回 `40100`。
- **置信度**：确定（运行时路由内省 114 个 `/api/v1` 端点，仅 8 个无鉴权，其中 3 个 `/auth/*` 与 4 个 `/market/overview*` 为有意公开，剩此 1 个无出处）。
- **最小验证**：复用 [00-BASELINE §4](00-BASELINE-AND-VERIFICATION.md) 的 30 行路由内省脚本，或 `curl http://127.0.0.1:8000/api/v1/datacenter/train/readiness`。
- **修法**：挂 `Depends(require_role("viewer"))`。
- **⚠️ 去重**：**此条已三次登记未修** — `docs/audit-2026-09-14/backend-architecture-review.md:150,182`（P1-2）、`docs/audit/AQP_QA审查_20260912.md:117,166`、`docs/audit-2026-09-14/backend-qa-report.md`（P2-6）。**本次新增价值不在缺陷本身，而在「为什么三轮审核都没修掉」**：见 §8.3 R5 与 §8.3b——**根因是 RBAC 测试只验白名单、不验完备性**（`test_read_endpoints_rbac.py:190-199`），故任何未列入白名单的路由都"未被分类"而非"通过"；同批还发现了**第 2 个无出处端点** `/market/index/kline`（P1-50）。

### P0-4（**红队复核后下调为 P2**）回测/策略回测的**基准与复权口径不可辨**（契约第 6 条）
> **✅ 已修（2026-09-21，第 10 轮）**：两条腿都补上了显式披露，且都由测试固化。
> - **复权口径（= B7b F6）**：`_load_strategy_bars` 返回 `(bars, raw_fallback_symbols)`，
>   响应新增恒存在的 `price_basis{kind,basis(qfq|raw|mixed),raw_fallback_symbols,note}`；
>   前端不再硬编码 "QFQ"（缺字段显示"复权口径未知"），规则说明不再无条件担保前复权。
> - **基准口径**：响应新增 `benchmark_basis{kind,synthetic,basis,reason,note}`，判据取自**实际净值曲线**
>   （覆盖"获取失败"/"区间无重叠交易日"/"引擎对齐后仍全 NaN"三条退化路径）；前端在 KPI 卡标题与
>   结果页标签上标注"构造基准"，避免把构造出来的 `annual_benchmark=0.0` 读成真实基准收益。
> - 顺带堵住"缺 `date`/`close` 列 ⇒ `ColumnNotFoundError` ⇒ 裸 50000"（改 `ERR_DATA_EMPTY` 并点名列）。
> - 验证：`backend/tests/test_backtest_price_basis_disclosure.py`（10 条，含 3 条基准口径）+ 全量
>   `1392 → 1402 passed / 0 failed`。
> - **残留**：`strategy_base.py:296-298` 内部仍会用常数基准（**行为未改**，本轮只做披露；
>   若要改行为需产品决策），`/backtest/run`（Top-K 引擎）的基准口径未纳入本轮。

- **位置**：`backend/app/api/v1/backtest.py:313-316`（`source` 跟踪了 qfq→raw 回退却从不读取，F841）＋ `:525-528`、`app/backtest/strategy_base.py:296-298,480-481`（基准不可得时造一行 `close=1.0` ⇒ `annual_benchmark=0.0`）
- **现象**：同一回测可能**混用 qfq 与 raw**（除权日产生假亏损/假信号）而响应里只有 `"source":"real_daily_bar"`；基准降级被报成「基准年化 0%」。
- **⚠️ 红队复核（推翻初稿措辞，下调定级）**：初稿称「与真实 0% 基准**不可区分**」——**不成立**。实测两条路径在 KPI 层之外是**可辨的**：
  | | 合成基准（造 `close=1.0`） | 真实 0% 基准 |
  |---|---|---|
  | `risk.alpha` / `beta` / `IR` | **全部 `null`** | 0.696 / −0.036 / **5.32** |
  | benchmark 曲线 | **字面常量 1.0** | 真实曲线 |
  ⇒ **间接可辨**（α/β/IR 为 null + 曲线是常数），只是**响应里没有 `basis` 字段显式披露**。故定级由 P0 下调 **P2**，缺陷描述改为「**KPI 层不可辨；披露缺失是真实违约**」。
- **诚实收窄（不变）**：实测 `daily_bar_qfq` 2499 == `daily_bar` 2499、差集为空 ⇒ **复权回退分支当前不可达**，故复权部分仍属 P2 级影响。
- **置信度**：确定（缺陷确证 + 红队实测两条路径的 KPI 差异）。
- **最小验证**：`ruff check app/api/v1/backtest.py --select F` → F841；`run_strategy(..., benchmark=None)` → `risk['annual_benchmark']==0.0` 且 `alpha is None`。
- **修法**：响应增 `basis: {price_adjust: "qfq"|"raw"|"mixed", benchmark: "real"|"synthetic"}`；基准缺失时置 `status=degraded` 而非 0.0。

### P0-5 网格寻优先物化笛卡尔积再守卫 ⇒ 单请求即可 OOM 打死 **API 服务**（**红队已修正触发条件与影响面**）
- **位置**：`backend/app/backtest/param_search.py:80-85`（`combos = list(itertools.product(...))` 后才 `len(combos) > max_trials`）
- **现象**：父审核员静态复核确认；B5 实测 `grid_search(150×150×150)` 在抛错前**已吃 243MB / 1.47s**。
- **⚠️ 红队修正（必须按修正版理解）**：
  ① **`150^6` 经 HTTP 不可达** —— `backtest.py:535` 的白名单 `_STRATEGY_PARAM_KEYS` 只允许 **2–3 个键**（我初稿「候选值长度无上限 ⇒ 150^6」是**经过 HTTP 的错误推论**，已推翻）；
  ② **但真实可利用面更小、更容易**：**3 个键 × 1000 个候选 = 22.5 KB JSON → 1e9 组合 → 实测 72 B/组合 ⇒ ≈72 GB**。上游**无任何拦截**：pydantic `max_length=6` 只限**键数**，nginx **未设 `client_max_body_size`**（默认 1m，22.5 KB 轻松通过）；
  ③ **影响面收窄**：compose 有 `mem_limit: 2g` ⇒ OOM 只杀 `aqp-api` 容器（`restart: unless-stopped` 会重启它）⇒ 准确表述是「**打死 API 服务**」而非「打死整站」；**非容器部署（原生 `run.md` 路径）没有内存上限，才是真正的整机 OOM**。
- **置信度**：确定（代码 + B5 实测内存峰值 + 红队重算请求体大小）。
- **最小验证**：`python -c "from app.backtest.param_search import grid_search; grid_search(lambda p:{}, {f'k{i}':list(range(150)) for i in range(3)})"`（观察内存峰值）；或按 `curl` 发 3 键 × 1000 候选的请求体（22.5 KB）。
- **修法**：先 `math.prod(len(v) for v in param_grid.values())` 判上限（**不要先物化**），或 `itertools.islice` 惰性取；`optimize_params` 校验每键候选数上限；nginx 补 `client_max_body_size`。

### P0-6（**红队复核后已下调为 P2**）「空榜」终态与契约冲突 —— ✅ **已裁决并落地（2026-09-22，第 17 轮）**
- **位置**：`backend/app/data/screening.py` → `backend/app/api/v1/screener.py:303-309`（`total == 0` ⇒ `status="ok"`、`reason="no_matching_signals"`）
- **冲突**：本次派单契约明确「空榜终态必须是 `unavailable`（不能是 ok 空数组）」；而 09-14 的修复报告把 `no_matching_signals`+`ok` 当作**有意设计落地**（`docs/audit-2026-09-14/data-freshness-degradation-fix-report.md:39,56`），且 `tests/test_data_freshness_degradation.py:111-127` 正断言该行为（`no_signal["reason"]=="no_matching_signals"`，只对「缺行情」判 `unavailable`）。
- **触发**：`board=bse`（池恒 0，属需求）**必然**命中；任意无匹配信号的交易日同样命中。
- **置信度**：确定（行为已实测，测试已确认是有意的）。
- **⚠️ 红队复核结论（下调依据）**：① **分支顺序正确且可达**（`board=bse` 实测 → 快照路径 `return None` → 实时路径 `total==0` → `status=ok/no_matching_signals`）；② **前端两种状态都区分渲染**（`Screener/index.tsx:213-214`、`AiPicksPanel.tsx:102-103`），空榜显示中文「当前没有满足条件的有效信号」⇒ **用户可见行为无误**。⇒ 这是**纯契约冲突、不是代码 bug**，故**从 §2 P0 下调为 P2**，并移入 §10 裁决清单（第 1 项）。
- **裁决建议（不变）**：(a) 若采纳新契约 ⇒ 改 `screener.py:303-309` 并**同步改写** `test_data_freshness_degradation.py:111-127`（否则修复会被判红）；(b) 若保留现语义 ⇒ 修正本报告所依据的契约措辞为「空榜必须 `reason=no_matching_signals`，缺行情才 `unavailable`」。
- **✅ 最终落地（2026-09-22，人工指定"按最优解解决"）**：**不选 (a) 也不选 (b)**，而是把"空榜"按**可判定的事实**拆成两类（理由见 §10 第 1 项）：
  - `backend/app/data/screening.py`：`FilterUniverseResult` 新增 `board_universe_rows`（板块在 universe 快照里的成分股数；`all`/无快照 ⇒ `None`，缺 `board` 列时按 symbol 前缀兜底），并随响应 `universe_filter.board_rows` 披露（**纯增量字段**，前端忽略未知键）。
  - `backend/app/api/v1/screener.py::_finalize_screener_payload`：`total == 0` 且 `board != "all"` 且 `board_rows == 0` ⇒ `unavailable/empty_board`（文案："板块 X 当前没有可筛选的成分股（未纳入数据源），并非「当日无匹配信号」"）；否则仍是 `ok/no_matching_signals`。`board_rows is None` ⇒ 不臆断。
  - **既有断言零改写**：`test_data_freshness_degradation.py:111-127` 原样通过（其载荷无 `board`/`board_rows`）——这正是"没放宽守卫"的反向锁。
  - 新增 `backend/tests/test_screener_empty_board_contract.py` **11 条**；实测 `board=bse` 端到端 `unavailable/empty_board`，同一份 pred+universe 的 `board=main` 正常出榜 2 条（证明"空白来自板块维度、不是数据缺失"）。

### P0-7 因子分层/多空净值**前视偏差**（遗传规划模块），且结果直接展示给用户
- **位置**：`backend/app/ml/gp_miner.py:260,279-286`（多空）与 `:331,337,343-346`（5 分组）
- **现象（父审核员独立读码确认索引数学）**：`:260 daily = close_w.pct_change()`，故 `daily.loc[d] = close[d]/close[d-1] - 1` **就是 d 日当天已实现的收益**；而 `:271-281` 的分组用的是 `wide.loc[d]`（**d 日收盘后才知道的因子值**），再 `:279 dr = daily.loc[d]` 与 `:280-281` 相乘 ⇒ **用 d 日的因子赚 d 日已经赚到的钱**。正确写法是 `daily.shift(-1)`（或 `pct_change().shift(-1)`）。B4b 实测：因子写成 `close/Ref(close,1)`（当日收益率的自反式）时 `mean_ic ≈ 0`，而分层净值显示 **+759,906,574%**。
- **内部矛盾（证明是笔误而非设计）**：同函数的 `:330` 注释自称「持有 horizon 日」，而 `:348+` 的 decay 分析用的**是正确的仅前向窗口**。
- **触发**：`/studio/alpha-eval`、`/studio/factor-report`（**前端 FactorStudio/分层净值页直接展示**）。
- **置信度**：确定（B4b 探针实测 + 父审核员读码确认索引位移方向）。
- **最小验证**：`python %TEMP%\b4b_probe_gp_nav.py`；或令因子 = `close/Ref(close,1)`，断言净值应 ≈0 而非天文数字。
- **修法**：净值累计改用 `t+1`（或 `t+1..t+h`）收益，与 decay 口径统一；并加一条**自反式因子净值为 0** 的回归用例（最便宜的前视探测器）。
- **易混淆点（避免误判）**：`long_short_nav` 这个键**在 `gp_miner.py:286` 是存在的**（studio 侧），所以 P1-25「前端读 `long_short_nav` 而后端不返回」指的是**回测侧 `/backtest` 的 `quantile_spread`**（`signal_analysis.py`），两者是不同端点——修 P1-25 时不要误以为键名不存在，也不要因为键名存在就以为净值算得对（本条说的是净值**算错**）。
- **为什么是 P0 而不是 P1**：前视偏差会让「因子研究结论」系统性错误，且它已被展示到 UI 上；错误结论会驱动后续所有策略决策。修复成本极低（一行位移 + 一个用例）。

### P0-8 文档推荐的夜间备份脚本会**删除生产数据库**（数据破坏型运维指令）
- **位置**：`backend/scripts/backup.py:57-73`（`main()` 先 `unlink()` 生产 `aqp.db` 再解压还原，B0 实测 `:67-68`）+ **`README.md:213` 把它作为每晚 cron 推荐**
- **现象**：该脚本的语义不是「备份」而是「备份 → **删库** → 从压缩包还原」。以 README 推荐的方式挂 cron ⇒ **每晚删除一次生产库**；WAL 模式下还可能还原出 malformed 库。**同仓早已存在安全版本 `scripts/backup_drill.py`（用 tempdir）**；而 API 端点 `/settings/db/backup` 走 `sqlite3.backup()`（**正确**）⇒ 同仓**三套备份语义**，文档推荐了最危险的那套。
- **触发条件**：按 `README.md:213` 配置 cron，或运维手动执行 `python scripts/backup.py`。
- **置信度**：确定（B0 读码 + 干跑观察；**未**在真实生产库上执行）。
- **最小验证**：在**数据副本**上执行 `python scripts/backup.py`，观察 `aqp.db` 被 unlink；或只读审查 `backup.py:57-73`。
- **修法**：`backup.py` 的 `main()` 改为纯导出（复用 API 端点同款 `sqlite3.backup()`）；恢复行为独立到 `restore.py` 且**必须二次确认**（DEP-22）；`README.md:213` 改推荐 `backup_drill.py` 或 `POST /settings/db/backup`。
- **风险等级说明**：之所以定 P0 而不是 P1 —— 它是**唯一一条「按现有文档操作就会造成不可逆数据丢失」**的路径，且修复只需改脚本+改文档一行。同类但需显式 `--apply` 的 `purge_legacy_models.py:71`（`DELETE FROM model_registry` 无 WHERE，会清 `is_production=1`）列为 DEP-21（P2）。

---

## 3. P1 · 功能错误（49 条；编号至 P1-51，其中 P1-8 经红队证伪、P1-47 经实测证伪、P1-51 为第 18 轮新发现）

| ID | 位置 | 现象与触发条件 | 类型 | 置信度 | 最小验证 |
|---|---|---|---|---|---|
| **P1-1** | `backend/app/backtest/engine.py:369-371,490-491` + `broker.py:397` | 摩擦成本 `last_day_turnover` 只在有成交日更新，非交易日沿用旧值 ⇒ **衰减成本被反复扣**：21 日仅 1 笔成交却扣 1882 元（权益 −1.9%，Sharpe −3.55→−240） | Bug | 确定 | `run_backtest(恒定价21天, friction=BrokerConfig(enabled=True,decay_bps=10))` |
| **P1-2** ✅ **已修（见 FIXES-APPLIED 35）** | `backend/app/api/v1/backtest.py:243-254` | 策略回测缓存键漏 `use_legacy_engine`（父审核员静态复核确认键串无该字段）⇒ 600s 内**两套引擎互取缓存**，返回「另一套闸门口径」的结果（且载荷里 `liquidity.engine="legacy_no_gates"` 披露会与实际引擎不符） | Bug | 确定 | `_strategy_cache_key(legacy=False)==_strategy_cache_key(legacy=True)` → 修前 `True`；`tests/test_backtest_cache_key.py::test_strategy_key_differs_by_use_legacy_engine` |
| **P1-3** ✅ **已修（见 FIXES-APPLIED 33）** | `backend/app/trading/paper.py:118-147,158-248,317-397` | 模拟盘**无持仓校验**：裸卖凭空造现金。**红队端到端实测（内存 sqlite 全链路）**：API 参数是 `order_amount`，故卖 **10 万元 → 成交 8400 股** → 权益 1,000,000 → **1,098,871.19**、持仓变 `{}`；`place_order`/`run_fills`/API **三层均无校验**；~~`paper.py` **无任何测试文件**~~（**更正**：`tests/test_production.py:143-198` 已有 `TestPaperDesk` 4 例）。**父审核员独立复现**：零持仓卖单成交 **10,700 股**；超持仓单卖出 **115,200 股 vs 持仓 10,700**。修法：下单期拒绝（含可卖股数）+ 撮合期按持仓截断 + 零持仓置 `REJECTED` + 历史越卖 `integrity_warnings` | Bug | 确定 | `tests/test_paper_position_guard.py`（6 条） |
| **P1-4** | `backend/app/data/universe.py:406-407` + `ingest/tasks.py:60` + `api/v1/backtest.py:199-200,680-681` | **幸存者偏差**：`delist_date` 全 NULL ⇒ 退市过滤/强平/haircut 三条路径全部空转，**而响应 note 仍宣称「含退市证券…超期持仓强平减记」= 披露不实**（父审核员复核 `instrument` 表 `delist_date` 非空 **0/15**；`upsert_delist_dates` 唯一调用方是**手工脚本** `scripts/enrich_delist.py`，未进任何流水线/定时任务） | Bug+披露 | 确定 | 只读 sqlite 计数 + `rg upsert_delist_dates` ／ **已修（2026-09-21）**：① 披露改为数据驱动（`delist_coverage` + 实测文案）；② 折价损失计入 `friction_costs["delist_loss"]`（此前完全不可见）。**⚠️ 报告措辞更正**：实测生产库 `delist_date` **0/5552**、两套面板**均无 `delist_date` 列** ⇒ 「按 delist_date 剔除」**在结构上不可能发生**（披露不实成立）；但**强平/haircut 路径由「面板缺席」驱动、与 `delist_date` 无关，实测可达**（`tests/test_delist_path_disclosure.py` 引擎级用例如实通过）⇒「三条路径全部空转」**不准确，实为 1 条空转 |
| **P1-5** ✅ **已修（见 FIXES-APPLIED 34）** | `backend/app/backtest/engine.py:511-517` | `monthly_monotonic_ratio` **方向反了**（要求 Q1>…>Q5，而 Qg 是最高分组；父审核员读码确认 `long_short = Qg − Q1` 且判定式 `rets[i] > rets[i+1]`）⇒ 完美单调因子实测得 **0.0**；测试断言是恒真的 `>=0.0`；该数字被 `scripts/p2_experiment.py:86` 打印进实验报告。**本轮实测**：收益随分数递增（Q1=1.0146…Q5=1.2849，多空 **+0.2704**）比值 **0.0**；完全反向（**−0.2737**）比值 **1.0** ⇒ 修后 1.0 / 0.0 | Bug | 确定 | `python backend/.tmp_testrun/p15_mono.py`；`tests/test_p2_backtest.py::test_monthly_monotonic_ratio_direction` |
| **P1-6** | `backend/app/ml/registry.py:472-479,67-81` | 提升门禁 `rank_ic_tolerance` 默认 **0.0**（注释「0 = 必须不劣」）⇒ **任何版本回滚恒被拒**（实测 `0.0920 < 0.1020`）——即生产模型变坏时**没有回滚通道** | Bug/运维 | 确定 | 沙箱复现 `evaluate_candidate(旧模型, 现生产)` → `promote=False` |
| **P1-7** | `backend/app/ml/registry.py:350-356` | 已 promote 版本 `model_path` 已在 `prod/` ⇒ `src == dst` ⇒ `shutil.copy2` 抛**未捕获** `SameFileError`；`promote_model.py --version <当前生产版本>` 即踩 | Bug | 确定 | 沙箱复现（B4a 实验 1） |
| **P1-8**（**红队证伪后降为 P2 披露类**） | `backend/app/api/v1/portfolio.py` + `app/domain/portfolio.py:365-366` | **⚠️ 本报告初稿的「恒返回 `code=50000`」被红队推翻**。真实路径：端点带 `response_model=APIResponse[dict]` ⇒ 走 **Pydantic v2 序列化**（`ser_json_inf_nan='null'`）⇒ NaN/Inf 被渲染为 **`null`**，实测 **http=200 / code=0 / `"benchmark": null`、无 ValueError**。初稿所测的 `JSONResponse({'x':inf})` 抛 ValueError **不是端点路径**；全站 **114 个 `/api/v1` 路由中仅 4 个无 `response_model`**（`notify/stream` + 3 个 export），且都不产出 NaN 浮点 ⇒「恒 50000」**不可达**。**其次，触发条件也被否证**：`portfolio.py:201` 的 `benchmark.reindex(union_idx).ffill().dropna()` 会丢掉首部 NaN ⇒ `benchmark.iloc[0]` 恒为首个**有效**收盘（实测 `first=NaN → code=0` 正常），**只有字面 0 才触发**。⇒ **降为 P2 披露类**：真实缺陷是「基准异常时**静默置 `null`、无 `basis` 披露**」 | Bug/披露 | **确定（红队实测）** | 真 app + 自造 `data` 走真实 `response_model` 路径：`http=200, code=0, benchmark=null`；`first=NaN` → `code=0` |
| **P1-9** | `backend/app/domain/portfolio.py:209-228` | 「疑似退市/停牌缺口」分支**永不触发**（`raw_prices` 索引是 `union_idx` ⇒ `last_valid_index` 恒为首/末）⇒ 三种断档 `data_warnings` 全为空，退市标的以最后价永久冻结估值 | Bug | 确定 | B2 探针 G2/G4 |
| **P1-10** | `backend/app/domain/neutralize.py:62-69` | `out[idx] = res` 用**标签索引**写 numpy（父审核员读码确认 `idx = g.index.to_numpy()`）⇒ 索引非 `0..n-1`（过滤/子集）必 `IndexError`；同时该模块**全仓零调用方** | Bug+死代码 | 确定 | `apply_industry_neutral(df.iloc[2:])` → IndexError |
| **P1-11** | `backend/app/domain/adjust.py:64` | `adj_factor.fill_null(1.0)`：因子缺行日退化为**不复权** ⇒ hfq/qfq 出现虚构单日收益（触发频率需数据层确认；模块整仓未接线，仅测试守护） | Bug | 确定 | B2 探针 A2 |
| **P1-12** | `backend/app/domain/limit.py:69-73` + `data/universe.py:219-224,425-430` | 新股「前 5 日不设涨跌幅」按**自然日**而非交易日 ⇒ 长假前上市的新股节后首日被套 ±20%（实测 2024-09-30 创业板新股，10-08 才是真实第 1 个交易日却 `pct=0.20`） | Bug | 确定 | B2 探针 P1 |
| ~~**P1-13**~~ | `backend/app/domain/optimizer.py:116-138` | `weight_cap` 不可行（N*cap<1）时**静默返回** Σw=N*cap（N=10/cap=5% → **0.5，一半现金**），API 仍 `status=ok`、`fallback=False` | Bug | 确定 | B2 探针 W1 → **修复后**：新增 `cap_feasibility`/`cap_info_for` 判定 `feasible=false` 并给出 `max_invested_ratio`/`min_feasible_cap`（权重行为不变），沿 `BacktestResult.weight_cap_info` → `/backtest/run`、`/research/optimize`、前端告警全链路披露；**并发现原文未点明的一条**：默认 `weighting="equal"` 分支从不调用 `apply_weight_cap`（实测 top_k=4/cap=0.2 ⇒ `max w=0.25>cap`、`Σw=1.0`）⇒ 披露区分"半仓"与"超限未执行"（第 13 轮） |
| **P1-14** | `backend/app/data/etf.py:659-689` | ETF 资金流字段口径错：`fflow` 是**净额**序列，代码把 `f52` 当 `main_in`、`f53` 当 `main_out` ⇒ `net_inflow = 主力净额 − 小单净额`；红队**手算 +29.4392%**（本报告 +29.4% 正确；`B3b-data.md` 写的 29.5% 是**笔误**），且符号可错（同仓 `realtime._fetch_em_fflow` 解读正确）。**置信度标注（红队修正）**：`f55+f56≡f52` 精确成立 ⇒ `f52` 是净额**已确证**；但**原始响应无外网无法独立取数**，故「偏差 +29.4%」是基于既有样本的重算值而非新取数。**✅ 已修（2026-09-21，第 11 轮）**：`net_inflow` 直接取 `f52`（不做任何加减）；新增分项净额（超大/大/中/小单）供独立核对；缺失字段由 `or 0` 改 `None`（不再伪造成 0）；列数判据 `<3`→`<6`。**偏差公式已推导并固化**：旧值 ≡ `2×主力 + 中单` ⇒ 偏差率 = `1 + 中单/主力`，用 **B3b/红队的真实观测样本**（f52=274,198,704、小单=−80,721,950、中单=−193,476,752、大单=65,674,576、超大单=208,524,128）复算得旧值 = 354,920,654、偏差 **+29.4392%**（独立复核 ①：`f55+f56−f52 = 0`）。9 条用例，全量 `1402 → 1411 / 0 failed` | Bug | 确定（字段语义）/ 需外网复核（原始值） | 恒等式实测：大单 + 超大单 = `f52` |
| **P1-15** ✅ **已修（见 FIXES-APPLIED 37）** | `backend/app/data/quotes_hub.py:68-69` + `api/v1/alerts.py:520` | >200 只时**静默截断前 200**（无日志、无字段）；watchlist 类预警规则标的无上限 ⇒ **第 201 只起永不触发且完全不可观测**。修法：按 200 **逐片抓取并合并**（250 只实测 2 片 = 全部返回）+ 总上限 800 超限必披露/WARNING + 截断五件套 | Bug | 确定 | `tests/test_quotes_snapshot_sharding.py`（6 条） |
| **P1-16** | `backend/app/ml/monitor.py:552-555` + `registry.py:472-479` | 自动重训候选**恒被拒**（实测 `0.0871` 与 **`0.10199 < 0.10203`**，差 4e-5）⇒ 生产模型自 09-05 冻结，漂移→重训闭环失效（重训本体仅 ~38s，churn 纯浪费） | 策略 | 确定 | 沙箱复现实验 3 + DB 副本 |
| **P1-17** | `backend/app/ml/monitor.py:285-323,702-704` | PSI 对**水平型价量因子**随趋势反转必然超标（实测 `ma_gap_250` 中位数 +0.0899→−0.0610，PSI=0.4686/3.3051）⇒ 永久 `degraded` + 12h 一次重训；KS 临界值≈0.0095 而均值 0.1161 ⇒ `n_over_crit` 恒 = 因子数（85/85）。**✅ 已修（2026-09-22，第 12 轮）**：① PSI 判定口径改为**按交易日截面标准化**（去当日中位、除当日 MAD），池化原始 PSI 降为**披露字段**；② KS 同口径 + 增 `over_crit_ratio`/`crit_effective` 并以"强度而非全命中"表述；③ IC 阈值的 σ 按**当日池宽**折算。真实面板（`alpha_basic_v2g` 105.3 万行 × 85 因子）逐位复现快照后重算：raw `mean=0.2646/max=3.3051/21-85 超 0.25` → 判定口径 **`mean=0.0415/max=0.2024/0-85 超 0.25`**（`drift_state` 由 degraded 降为 **watch**，不再假降级）；KS `mean_D 0.1161→0.0387`。**未按字面实现"截面内排名后再算 PSI"**：实测该口径 `mean=0.0011/max=0.0131/0-85` —— 秩在每个截面内构造上恒均匀 ⇒ 完全丧失判别力（等于把监控改成空转），故取"去中位+MAD 归一"；注入 30% 异质样本时该口径 **0.3540 > 0.25**（仍会报警） | 策略/误报 | 确定 | 真实面板重算 PSI 逐位一致（探针 5/7）→ 修复后同面板 `0/85` 超标 + 注入报警（17 条用例） |
| **P1-18** | `backend/app/ml/features.py:241-263` + `graph.py:86-105` + `orchestrator.py:159-181` | `g1_*` 依赖当日 universe/行业快照：**去掉一只标的，同一历史交易日 42 列中 40 列改变** ⇒ 增量守卫失败触发全量重算并**重写历史 g1_**（生产模型 60 特征中 20 个是 g1_），asof 稳定性承诺被破坏 | 一致性 | 确定 | 探针 9 | ✅ **已修（第 15 轮）**：机制实测为"邻居有无取值"（非行归一化分母，见 `FIXES-APPLIED` 第 15 轮自查）；新标的污染可挡（3.0 vs 252.0），取值消失不可复原 ⇒ 默认拒绝静默改写 |
| **P1-19** | `backend/app/data/quality.py:436-459` + `api/v1/ops.py:34,48-64` | 用户可见的质量体检**欺骗性**：合法 10 送 10 被判 `daily_return/error`（CLI 路径有除权豁免→无此错）；且 `validate_partition` 不调 `check_factor_jump`、不传 `trade_days` ⇒ docstring 宣称的「复权突变/日历」检查**永不触发** | 一致性 | 确定 | 同数据 API 报 error / CLI 报 warn |
| **P1-20** | `backend/app/api/v1/backtest.py:550,571` | walk-forward 最终 KPI 用**最后一折参数跑全区间**（样本内），前端把 KPI 放 WF 表上方且无标注 ⇒ 用户读成样本外成绩 | 一致性 | 确定 | 比对 `kpi.sharpe` vs `mean_oos_sharpe` |
| **P1-21** | `backend/app/ml/monitor.py:52,679,714-727` | `_worst(['unknown','healthy'])=='healthy'` ⇒ IC 不可评估时报 healthy 并推送**虚假「恢复健康」**通知。**✅ 已修（2026-09-22，第 12 轮）**：状态优先级改为 `healthy < unknown < watch < degraded`（`unknown` 是"证据不足"而非"没问题"）；`_notify_state_change` 只对 `cur=='healthy'` 发恢复文案，故 `degraded/watch → unknown` 不再推送任何"变好"通知。4 条用例（含真实推送调用断言） | Bug | 确定 | 探针 8 → 修复后 `_worst(['unknown','healthy'])=='unknown'` |
| **P1-22** | `frontend/src/utils/useChart.ts:12-20` | 第二个 effect 依赖数组只有 `[option]`、**缺 `node`**；`node` 是 `useState`（init 推迟到下一次 commit），而 React 18 保证新 render 前先 flush 旧 passive effects ⇒ **首个 option 永不 `setOption`**。**⚠️ 红队收窄影响面（必须按此理解）**：图表空白**只发生在"条件挂载 + memo `option`"的 5 处**（`Research` 4 张图 + IC 衰减图）；同仓 **8 处 inline option** 因每次 render 引用都变而**正常工作**——所以不能笼统说"所有用该 hook 的图表都空白"，否则会被当场证伪。**旁证强**：同仓三份局部实现（`DataCenter:52`/`EtfDetail:47`/`Portfolio:52`）都用 `useRef` 因而正常，唯共享 hook 用 `useState`。修法：deps 改 `[node, option]` | Bug | 确定 | B8 用 `react-dom@18.3.1` 内部 `performSyncWorkOnRoot:26115` 的 `flushPassiveEffects()` 时序证明；红队用 `react-dom.development.js:26731-26739`（提交前 flush 旧 effects）独立复核 |
| **P1-23** | 前端 16 处调用点，集中在 `pages/DataCenter/index.tsx:404-494`、`pages/Settings/index.tsx:193-224,588-621`、`pages/Screener/index.tsx:202,341`、`pages/Portfolio/index.tsx:251,389`、`pages/Report/index.tsx:89` | **viewer 必然 40300 的角色错配簇**（B8 与 P4 双向确证；红队实测 **7 个 GET 对 viewer=40300 / 匿名=40100**，路由只声明 `minimum="viewer"`、按钮无门禁）：`/data` 页 viewer 只 5 块可用、一进页即弹英文 `FORBIDDEN` 横幅；`TrainPanel` 恒红框；`/settings` 的 admin「清缓存/备份」与 researcher「同步/连接测试」按钮对**所有登录用户可见可点**，且连接测试在**挂载时自动调用**（403 被 `allSettled` 静默吞掉）；`/settings` 的「量化引擎」卡已用 `isAdmin` 门禁 ⇒ **证明是漏做而非设计**（对照 `StockDetail` 已用 `hasMinimumRole`）。**⚠️ 红队修正两处**：① 初稿「1.5s 轮询持续失败」**不成立**——`sync` 恒为 `null`，轮询根本不启动；② `desk`（`/api/v1/desk/*`）相关调用点被 `RequireRole(researcher)` **挡住**，viewer 到不了 | 一致性 | 确定 | `rg -n "isAdmin\|hasMinimumRole" frontend/src/pages`；`rg -n "require_role" backend/app/api/v1/datacenter.py` |
| **P1-24** | `frontend/src/stores/useAuthStore.ts:40-46,85-99` | 模块加载即把 `localStorage.AQP_ADMIN_TOKEN` **迁移成 `role=admin` 会话**（zustand persist 持久化、`expiresAt=null`）—— 与 P0 背景块「`ADMIN_TOKEN` 仅运维直连、**不是前端登录凭据**」直接冲突；生产 `ALLOW_ADMIN_TOKEN_LOGIN=false` 时会出现「UI 说 admin、后端说不是」 | 一致性 | 确定 | `rg -n "AQP_ADMIN_TOKEN" frontend/src`；B8 与 B9c 双向确证 |
| **P1-25** | `frontend/src/pages/Backtest/SignalAnalysisPanel.tsx:39-53,97-108` ↔ `backend/app/ml/signal_analysis.py:134-144` | **分层多空模块整体是死的，而且这不是"键名不一致"这么轻**。**父审核员独立读码确认**：后端 `quantile_spread_report` 实际只返回 `{horizon, n_quantiles, quantile_mean_ret, ls_mean_daily, ls_t_stat, ls_annualized, monotonic, n_days}`（`:134-144`）——前端读的 `long_short_nav` 与 `spread_annualized` **两个键都不存在**；**docstring `:93` 声称返回 `long_short_daily` 也未返回**（文档又一处失真）；后端已算好的**单调性 `monotonic` 与 `ls_t_stat` 被前端直接丢弃** ⇒ 图恒空、年化价差永不显示。**同一端点还叠加数值口径错**（见 B4b-16 / P2-T7）：`:119` 的 `ls_daily` 每元素是 **h 日**远期收益（`:104 forward_return_pivot(..., h)`），`:126` 却 `ann = mean_ls × 252` ⇒ **放大 h×**（函数自带默认 `h=5` ⇒ 5×；调用方传 `max(horizons)=20` ⇒ **20×**）；`:125` 的 t 统计量按"日度独立样本"算，而 h 日窗口**重叠** ⇒ 有效样本 ≈ n/h ⇒ **t 虚高 ~√h**（`n_days` 计的也正是**日期数**而非独立观测数） **数值口径实测（修复前后对照，见 FIXES-APPLIED「P1-25」节）** | ✅ **已修（2026-09-21）**：① 年化改 `mean × 252 / h`（h=20 实测 **+6031.44% → +301.57%**，即消除 20× 放大）；② t 值重叠校正 `n_eff = n/h`（h=20 实测 **107.39 → 24.01**，即消除 **4.47× = √20**；**h=1 完全一致**，口径不变）；③ 补 `long_short_nav` 序列 + `n_independent` + 四个 `*_basis` 披露字段，前端图表不再恒空、年化可显示、`monotonic`/`ls_t_stat` 已展示；④ **根因在 TS 声明本身**——`frontend/src/api/backtest.ts` 把不存在的 `spread_annualized` 声明为必需字段 ⇒ 类型检查通过、运行时静默 `undefined`；已修正并新增跨层契约守护用例 `test_frontend_type_declares_all_backend_keys` |
| **P1-26** | `frontend/src/pages/Backtest/TopKPanel.tsx:95-99` | `fmtPct(m.annual_return/win_rate/max_drawdown)` 直接吃后端**小数** ⇒ 年化/胜率**小 100 倍**，最大回撤显示成「**+0.27%**」（正号）；同页 KpiCards/Portfolio 都有 ×100 ⇒ 单点单位错误 | Bug | 确定 | `rg -n "def annual_return\|def max_drawdown" backend/app/domain/metrics.py`；`utils/format.ts:10-14` 无 ×100 ／ **已修（2026-09-21）**：三处按兄弟页约定 `×100`（回撤另按 `resultParts.tsx:28` 显式取负）⇒ `+0.12%→+12.34%`、`+0.55%→+55.00%`、`+0.27%→−27.00%`；`tsc --noEmit`=0。**同轮自查证伪**：`CapacityAttribution/index.tsx` 的 16 处 `fmtPct` **不是**同类缺陷——该文件 `:103-104` 定义了**局部** `fmtPct` 且其实现已 `×100`（详见 FIXES-APPLIED「本轮的自查」） |
| **P1-27** | `frontend/src/pages/Backtest/index.tsx:105-119`（与 P1-20 同根因，此处为**前端呈现证据**） | walk-forward 时 KPI 卡（年化/夏普/回撤）取自「最后一折参数跑全区间」的**样本内**结果且**无任何标注**；真正的 `mean_oos_sharpe` 只在折表小字、`mean_is_sharpe` **从未渲染** ⇒ 恰好把防过拟合的结论讲反 | Bug/误导 | 确定 | `rg -n "base_overrides\|walk_forward" backend/app/api/v1/backtest.py`（`:550,:571`） |
| **P1-28** (B6-01) | `backend/app/services/sync_service.py:642-658` | `_start_sync_bg` **无条件** `_sync.completed.clear()`，抹掉 `restore_sync_state`（`main.py:57`）刚恢复的续传集合 ⇒ **断点续传从未生效**：autoSync 首次触发后所有 `resume=True` 都重头全量。实测 `before=[3 只] → after=[]` | Bug | 确定 | `python backend/.tmp_testrun/b6_probe_s2.py`（F1） |
| **P1-29** (B6-02) | `backend/app/jobs/evening_routine.py:75-87,110` | 整条流水线 FAILED 时**只**发一条 SSE（`:82-84`）与把 `"FAILED/step"` 写进 `detail` 字符串，随后 **`:110` 无条件 `_mark("done", detail)`** ⇒ 终态**没有 `failed`**、幂等标记使**当日不再重试**。**精修（父审核员独立复核）**：失败信息并非完全丢失（`detail["pipeline"]` 字符串可辨），但**状态机不可辨、无法自动重试**，且 15:45/17:30 的 `PipelineBusy` 撞车即触发 ⇒ **当晚榜单停更而日报照常生成** | Bug | 确定 | `python backend/.tmp_testrun/b6_probe_s3.py`（F4/F6）；父审核员读码确认 `:82-84` 与 `:110` |
| **P1-30** (B6-03) | `backend/app/services/market_service.py:59-62` + 消费方 `api/v1/market.py:550-553,579-583` | 三个子块只成功一个仍返回 `status="ok"` 且无 `reason` ⇒ `_build_rt`/`data_freshness` 的 `degraded` 判据**永久失效**（契约三态退化为两态）；这是本轮唯一「外部源降级被当健康」的实例 | Bug/一致性 | 确定 | `python backend/.tmp_testrun/b6_probe_s2.py`（F3） |
| **P1-31** (B6-04) | `backend/app/api/v1/datacenter.py:936-942,878-887,974-994` | `/sync/fetch` **TOCTOU**：预检取锁后立即释放、worker 二次取锁；窗口内锁被抢则接口已回 `started:true`、`data_jobs` **零行**、`_sync.error` 下次请求被清零 ⇒ 任务静默落空且不留案底（同状态机另一入口却如实记 FAILED） | Bug | 确定 | `python backend/.tmp_testrun/b6_probe_s8.py`（拦截线程确定性复现） |
| **P1-32** (B7a-01) | `backend/app/api/v1/etf.py:288,299,319,477`（源 `data/etf.py:110,247` → `realtime.py:139`） | `GET /etf/flow\|list\|hot` 外部源失败时抛 `RuntimeError` ⇒ `code=50000`（契约要求独立降级）；**`:299` 是第二处易漏点**。同文件 `overview/performance/scale/detail` 已正确包裹 ⇒ 属漏改。**这正是父审核员全量套件里那 3 例 RBAC 失败的根因**（见 [00-BASELINE §2.1](00-BASELINE-AND-VERIFICATION.md)）：环境（无外网）只是触发器，**逃逸本身是产品缺陷** | Bug/一致性 | 确定 | `python backend/.tmp_testrun/b7a_probe1.py`；原 3 例失败用例 `tests/test_read_endpoints_rbac.py::test_read_endpoint_allows_authorized[/api/v1/etf/{flow,hot,list}-viewer]` |
| **P1-33** (B7a-02) | `backend/app/services/market_service.py:22-27`（延伸 `:59-62`） | 「北向净流入」**未按资金方向过滤行**：`stock_hsgt_fund_flow_summary_em` 的汇总表同时含沪股通/深股通（北向）与港股通(沪/深)（南向）行，而代码取 `north_cols[0]` 后对**全表求和** ⇒ **南向被计入北向**（B7a 实测 **840.0 亿 vs 真实北向 0.0**），直接显示在前端「北向净流入」卡；且 3 块中 2 块失败仍 `status:"ok"`、`errs` 被吞。**根因已由父审核员读码确证**（`:23-27` 无任何行过滤/方向过滤）。**待外网复核项**：该块的数值**未做单位换算**而相邻 `main` 块 `÷1e8`（`:34`）⇒ 量纲口径也可能不一致，需在有外网的环境比对 akshare 原始列单位 | Bug | 确定（结构）/量级待外网复核 | `python backend/.tmp_testrun/b7a_probe6.py`；父审核员读 `market_service.py:22-34` |
| **P1-34** (B7a-03) ✅ **已修（见 FIXES-APPLIED 38）** | `backend/app/api/v1/screener.py:262` + `data/screening.py:71-72,95-103` | 预测日无 `universe_daily` 分区时 **ST/停牌过滤整段静默失效**仍报 `ok`：同一 pred 下 ST 股从「被剔除(count=1)」变成「进榜(count=2)、coverage 2/2」，无任何披露（`require_universe=False` 是默认值，**违反 `screening.py:85-88` 自述的「未校验榜单」红线**）。修法：`filter_universe` 返回可两元解包的 `FilterUniverseResult`（带 `universe_ok`/`universe_reason`）+ 空快照记 WARNING + 口径随快照落盘（读路径为主路径）+ `applied=False` 时**不得报 ok** | Bug | 确定 | `tests/test_universe_filter_disclosure.py`（12 条）；原复现 `python backend/.tmp_testrun/b7a_probe8.py` |
| **P1-35** (B7a-05) | `backend/app/api/v1/stock.py:167-168`、`market.py:769-770`、`screener.py:472` | **日期形状合法但日历非法** ⇒ 纯参数错误被报成系统故障：`kline?start=20269999`、`overview/daily?date=20269999\|20260230`、`screener?date=2026-02-30` 全部 `code=50000`（应为 40000）；且 `market/overview?date=20269999` 反而 `code=0`，把**最新推荐榜错标成请求日期** | Bug | 确定 | `python backend/.tmp_testrun/b7a_probe9.py` | **✅ 已修（2026-09-21）**：新增 `core/params.py` 严格解析（非法⇒40000），四处调用点改用；`_build_overview_hist` 回退最新榜时标 `fallback="latest"`，`trade_date` 恒为实时块所属交易日、请求日放 `requested_date`；21 条用例。**备注**：`_build_overview` 之外的实时块在历史请求下**仍是当日口径**（属该端点既有设计，已在 docstring 明示） |
| **P1-36** (B7a-07) | `backend/app/api/v1/market.py:86-110,131` + `:494-515` | 涨跌分布输入为空/全 NaN 时仍 `status:"ok"` + 全 0 桶，并连带 `_build_sentiment` 推出「**情绪 中性 50**」= 零数据造结论；**每年首个交易日必然触发**（`:119` 只取当年分区、`:126-130` 前收 `shift` 无前值 → pct 全空） | Bug | 确定 | `python backend/.tmp_testrun/b7a_probe7.py` | **✅ 已修（2026-09-21）**：`_heat_payload` 无有效样本 ⇒ `status:"unavailable"` + 固定 reason（保留 `extra` 口径字段）；`_build_sentiment` 再兜一层零样本拒绝（绝不推 50/中性）；13 条用例 |
| **P1-37** (B4b-2) | `backend/app/ml/train_lgbm.py:224` + `backend/scripts/train.py:44` + `backend/scripts/grid_search.py:83` | `xsec_demean` **默认 False**，只有 `retrain.py` 打开 ⇒ **默认训练入口产出退化模型**：真实面板上 `best_iteration=1`、`valid_rank_ic 0.0144`（同数据 `demean=True` 为 **0.0832**，**5.8×**）。即：按文档跑 `scripts/train.py` 得到的是一个只分裂一次、几乎无预测力的模型，且**两入口差异无任何日志/产物字段留痕** | 策略/一致性 | 确定 | `& $py "$env:TEMP\b4b_probe_train_lgbm_real.py"` |
| **P1-38** (B4b-3) | `backend/app/ml/train_lgbm.py:154-175,307` | 特征筛选用**池化 Spearman IC**，而报表与训练目标是**逐日截面 RankIC** ⇒ 口径错配把最强的截面因子剔除：真实面板 `atr_14` pooled 0.0003 vs daily **0.0816（270×）**，`hl_range`/`vol_20`/`v_rank_20` 同被剔除 | 策略/一致性 | 确定 | `& $py "$env:TEMP\b4b_probe_featsel.py"` |
| **P1-39** (B4b-5) | `backend/app/ml/train_lgbm.py:368-391` + `backend/app/ml/registry.py:213` | `xsec_demean` **未落盘**（metrics/params/registry 三处均无）⇒ 门禁**跨口径比 RMSE**：真实库 181337(RMSE 0.07411) vs 181713(0.05998) 同 split 同 dataset、**只差这一个 flag**，比值 **1.25×** 远超 `max_rmse_worsen_ratio=0.05` 被判「恶化超限」。**这条解释了 P1-16（自动重训候选恒被拒）与 P1-6/P1-7 的根因**：门禁在比较**不可比的候选** | 一致性/策略 | 确定 | 同 P1-37 探针 + 读 `prod/181337` 的 `metrics.json` |
| **P1-40** (B3a-2) | `backend/app/data/parquet_store.py:450-470` | `write_partition` 的**读-改-写无锁**：两个写者并发各写一日 → 实测文件 **3 行变 2 行**，且 manifest 与「丢失后的事实」一致（**事后无痕**）。真实对手存在：夜间 `build_universe`（持锁）vs **`scripts/build_universe.py` CLI（不受 `pipeline_slot` 保护，且写同一 `universe_daily/symbol=__all__`）**——而文档正是让运维手动跑这个脚本 | Bug | 确定 | `python backend/.tmp_b3a/probe3.py`［C1］ |
| **P1-41** (B3a-3) | `backend/app/data/parquet_store.py:61-64,186-199` | `_audited` **每 dataset 每进程只自愈一次**：首次旁路写触发重扫后，**后续新增的 symbol 永久静默缺失**且无 WARNING（`_manifest_scan_dataset` 不再被调用） | Bug | 确定 | `python backend/.tmp_b3a/probe1.py`［P5］ |
| **P1-42** (B3a-13) | `backend/app/data/universe.py:349-472` ↔ `backend/app/orchestrator.py:388-425` | **回测真正读的 `universe_daily_bt` 没有任何 pipeline 步骤**（仅手动 `scripts/expand_universe_2500.py`）：生产实测**停在 2026-09-04、最新年仅 1133 只**；而每晚耗时 **59.4s** 重建的 `universe_daily` **回测不读**（screener 读）。即：**夜间最重的离线步骤养的是另一份数据，回测在用过期的宇宙** ⇒ 近期区间的回测（含新股/次新）系统性失真。**须与 P1-4（delist 全 NULL）合并评估对历史业绩的影响** | Bug/未接线 | 确定 | `python backend/.tmp_b3a/probe9.py` |

| **P1-43** (DEP-10) | `backend/app/main.py:254-296` + `core/errors.py:57-59` + `README.md:200` | **健康检查链路整体空转**：`/health` 与 `/health/ready` 都经 `ok()` 包装 ⇒ **恒返 HTTP 200**，`not_ready` 只体现在 body ⇒ `curl -fsS` 仅在连接被拒时失败，实际退化为 liveness；而 `README.md:200` 把它们宣称为 **K8s readiness/liveness 探针** ⇒ 直接套用会让「sqlite/`DATA_ROOT` 检查失败」的 Pod 被判 **Ready**。另 `Dockerfile:56-57` 的 `/health/ready` healthcheck 被 `compose:62` 的 `/health` **覆盖**（死配置） | Bug/运维语义 | 确定 | `curl -s -o /dev/null -w "%{http_code}\n" :8000/health/ready` → 200；`curl -s :8000/health/ready` → body 里才有 not_ready | **✅ 已修（2026-09-21）**：`/health/ready` 未就绪 ⇒ **HTTP 503** + 新登记码 `ERR_NOT_READY=50300`（`core/errors.py` ↔ 前端 `types/api.ts` 双向登记）+ `Retry-After: 5`；`docker-compose.yml` healthcheck 对齐 `/health/ready`（消除死配置）；README 写明 `/health` **不是** readiness；3 条用例（含"未就绪必须能失败"） |
| **P1-44** (DEP-13+DEP-14) | `docs/项目开发文档.md:5564-5583,5893,5944,5950,5707-5724` + `docs/auth-register.md:38-39` | **文档部署契约会主动误导**（自称「部署所需模板已全部给出」）：§13 有 **6 个变量名根本不存在**——其中 **`APP_ENV` 的真名是 `ENV`**（⇒ 照做则 P0-2 的 prod 安全闸门**永不触发**）、`DATA_DIR` 真名是 `DATA_ROOT`、还有 `DATABASE_URL`/`REDIS_URL`/`APP_API_KEY`/`APP_PORT`（无 `env_prefix` ⇒ 被 `extra="ignore"` **静默丢弃**）；其 Dockerfile 示例三处照抄即失败（`COPY pytest.ini`/`tests` 为根相对、healthcheck `/api/health` **不存在**、`CMD ...router:app --loop uvloop` 的 router 非 ASGI app 且 uvloop 未装）；`chown $USER` 恰好制造 DEP-08 的 uid 冲突 | 一致性/文档 | 确定 | `python -c "from app.core.config import Settings as S;print([k for k in ('DATABASE_URL','REDIS_URL','APP_API_KEY','APP_ENV','APP_PORT','DATA_DIR') if k in S.model_fields])"` → `[]` |
| **P1-45** (DEP-15) | `core/config.py:159-166` + `main.py:90-93` | 只读容器内**默认开启写任务**且**无法关闭**：`EVENING_ROUTINE_ENABLED`/`AUTO_RETRAIN_ON_DRIFT` 默认 `true`，而 compose 不给环境变量注入通道（DEP-06）⇒ `startup_catchup()` 启动即触发、每日 17:30 也触发，全部写向只读路径（配合 P0-1 必失败） | Bug/配置 | 确定 | `docker compose exec aqp-api python -c "from app.core.config import get_settings as g;s=g();print(s.EVENING_ROUTINE_ENABLED,s.AUTO_RETRAIN_ON_DRIFT)"` |
| **P1-46** (DEP-11+DEP-12) | `backend/Dockerfile:59` + `frontend/nginx.conf:24-34` | **SSE ticket 同时落两份 access log**：Dockerfile CMD 缺 `--no-access-log` **且** nginx `/api/` 未 `access_log off` ⇒ uvicorn + nginx 各留一份含 `stream?ticket=` 的记录（应用自身 `_sanitize_query` 已做，**加固只漏在 access log 层**）；同因缺 `--proxy-headers`/`--forwarded-allow-ips`（nginx 已发 `X-Forwarded-For`）⇒ 应用侧**真实客户端 IP 丢失**。这是 B7b 的 F2 在 B0 侧的独立确认与扩面 | Bug/安全 | 确定 | `docker compose logs \| grep -c "stream?ticket="`；`docker compose exec aqp-web curl -H 'X-Forwarded-For: 1.2.3.4' http://aqp-api:8000/health` |
| **P1-47**（P2-T5）<br>**⚠️ 已被本轮实测证伪，降为「已核实正确 + 加固建议」** | `domain/portfolio.py`、`broker.py:268,310`、`data/universe.py:243,449`、`data/realtime.py`、`trading/paper.py:109-114` 的停牌判定 | **原结论「`volume<=0` 停牌闸门是死条件」错误，现予更正**。父审核员在**生产面板**上实测（`data/parquet/universe_daily/year=2026` 431,462 行、`universe_daily_bt/year=2026` 185,812 行）：<br>① `is_halted` 与 `volume IS NULL` 的交叉表是**完全对角的**——`universe_daily` 3107 行 NULL/全部 `is_halted=True`、428,355 行非 NULL/全部 `is_halted=False`；`universe_daily_bt` 1125/1125 同样对角，**零例外** ⇒ **停牌标注正确**；<br>② `volume<=0` 计数为 0 是因为面板存的是 **NULL 而非 0**，但 `universe.py:243` 用 `volume_f = volume.fill_null(0.0)` 计算 ⇒ `is_halted` 正确；<br>③ `broker._num(None, default=0.0)`（`broker.py:200-212`）把 NULL 映射为 0.0 ⇒ `volume <= 0` **确实会命中**，它与 `halted` 是**冗余双保险**而非死条件；<br>④ `paper.py:109-110` 的 `close*volume` 得到 NULL `amt`，被 `.filter(pl.col("amt") > 0)` 丢弃 ⇒ 停牌股不会混入"流动性不足"名单，**行为正确**。<br>**残留（低优先，非 Bug）**：面板用 NULL 而非 0 表达停牌，因此任何**直接读 `volume` 原始列且不做 `fill_null`/`_num` 归一**的新增消费点会得到 NULL（polars 中 `null > 0` 为 null、在 `when()` 里视作不成立）。建议加一条 `is_halted_row()` 统一入口作为**防新增消费点踩坑**的加固，而非修缺陷 | 一致性（原判 Bug）/加固建议 | **已证伪**（原「确定」撤回；残留项「疑似」） | 只读面板交叉表实测（见 FIXES-APPLIED.md"验证命令汇总"）；构造「第 10–15 日停牌」用例断言双边拒单 |
| **P1-48** (P2-T3) | `app/ml/train_lgbm.py`（落盘字段）+ `app/ml/registry.py:472-479`（门禁）+ `prediction` 落盘 | **同一列 `pred_score` 混装两种语义**（`_pipe` 版 vs `_repaired` 版），而**生产模型水平负偏置**、`RankIC` 门禁与该偏置**完全正交、结构上拦不住**。**父审核员只读 DB 独立复核（262 个分区全量，2026-09-18）**，结果**确认偏置、并细化两点**：<br>① **生产模型** `lgbm_v1_20260905_181713_repaired`（5 个分区）逐日截面均值 = **−0.002364 / −0.001849 / −0.002361 / −0.002281 / +0.000689** ⇒ 均值 ≈ **−0.0016（≈ −0.16%/5d）**，**与 P2 的 −0.2208%/5d 同量级**（差异来自日期/口径）；而同期 `std ≈ 0.0056~0.0072` ⇒ **均值是 std 的 1/3 量级，即整个分布被整体下移**，正是"水平偏置"而非噪声。<br>② **全量 262 个分区中 82.4% 的交易日截面均值为负**、总均值 −0.000060、中位数 −0.000212 ⇒ 偏置**方向稳定**。<br>③ **重要细化**：**252/262 个分区来自更早的 `lgbm_v1_20260830_095940_repaired`**，那批正是**约 120 只滚动小池**；**当前生产模型的 5 个分区都已是全截面**（2486/2484/2488/2492 只）⇒「预测落盘只覆盖 120 只」是**历史欠账**而非当前行为（影响的是**监控基线**，见 S4，不是当前模型质量） | 策略/一致性 | 确定（语义混装 + 偏置方向与量级均实测）/疑似（绩效影响） | 只读 DB 副本比较各版本 `pred_score` 的 `mean/std`；影子跑 20 日比 `mean(pred)` vs 实际均值 |
| **P1-49** (P2-T6) | `app/api/v1/portfolio.py:33-44` + `app/domain/optimizer.py` + `app/domain/research.py`（容量） | **组合层零约束**：`/portfolio/backtest` 的 Request **完全没有** `weight_cap`/行业/换手字段，`weighting` 只有 `{user, risk_parity, max_div, inverse_vol}`（**连 `mvo` 都不可选**）；`/backtest/run` 的 `weight_cap` 默认 **0**；全仓 `expected_returns=` **零调用方**（MVO 的 μ 恒 0，与 B2-11 同源）；`strategy_capacity` 只算 ADV **上限**且 `adv_ratio_in_pool` 参数**从不使用**。**A 股最低 5 元佣金反而给出容量下限**：Top-50（日换手 0.3215）在 AUM < 20000×50/0.3215 ≈ **311 万元** 时佣金费率超 2.5bp 并迅速恶化（A=100 万时约 **7.8bp/笔**，全年多 ~**6.3%** 成本） | 策略/一致性 | 确定 | 读 Request 字段清单（`rg "weight_cap\|industry_cap\|max_turnover" backend/app/api/v1/portfolio.py` → 0 命中）；`rg "expected_returns=" backend/app` → 0 命中 |

| **P1-50**（**P5 批次新发现，父审核员独立内省确认**） | `backend/app/api/v1/market.py:606-624` | **`GET /market/index/kline` 无鉴权依赖**：`deps=[]`，**不带任何 Bearer 即返回 `code=0` 与完整 `bars`**（P5 用 `fastapi.testclient` 直连 ASGI 实测；父审核员用路由内省独立确认）。**⚠️ 父审核员自我更正**：本报告基线的「8 个完全无鉴权端点」**计数是对的**（`00-BASELINE:107` 已列出该路由），**真正的错误在裁决步**——基线把它与 4 个 `/market/overview*` 一起判为「有意公开」，而 `App.tsx:86` 的注释只讲**市场概览**，**不覆盖 `index/kline`** ⇒ 它是**第 2 个"无出处"的未鉴权端点**（第 1 个是 P0-3）。严重度 P1（泄露的是已可经 `/market/overview` 公开获取的指数行情，非内部信息），但**它证明 R5 的机制漏洞比基线测量到的更大** | 一致性/安全 | 确定 | 路由内省打印 `deps=[]` 的 `/api/v1` 路由；或 `curl http://127.0.0.1:8000/api/v1/market/index/kline?symbol=sh000300` 不带 Bearer |

| **P1-51**（**第 18 轮修复时新发现，实测复现**） | `backend/scripts/alerter.py:19-21` | **运维告警脚本必然 `NameError`**：引导段用 `sys.path` 却**从未 `import sys`** ⇒ `python scripts/alerter.py` 在**第 20 行**即 `NameError: name 'sys' is not defined`，脚本 100% 不可用。触发路径是**文档化的运维步骤**（`README.md:203`；`B0-deploy.md:238-239` 把它列为 `NOTIFY_ENABLED`/`NOTIFY_WEBHOOK_URL` 的消费方）。**为何长期漏网**：历史审计已记为 LOW-001（`docs/audit/AQP_最终项目综合评审.md:261`）但一直未修，而本仓此前**没有 lint 门禁**——`ruff --select F821` 一眼可见（这正是 §8.2 第 20 项要解决的问题）。**已修**：补 `import sys`；新增"真执行脚本引导段"的反向锁（`compile()` 只查语法抓不到这类错） | Bug（脚本不可用） | 确定 | 修前：`python scripts/alerter.py` ⇒ `File "scripts/alerter.py", line 20 ... NameError`；修后：越过引导段进入业务逻辑（后续 `PermissionError [WinError 5]` 来自 loguru `enqueue=True` 需要命名管道，属沙箱限制非产品缺陷）。见 `FIXES-APPLIED` 行 55 |

**§3 至此收束（编号至 P1-51；P1-51 为第 18 轮修复过程中新发现）**。红队证伪与披露矩阵两批的结论已全部并入本节及 §2/§4.13/§8/§9.3。

---

## 4. P2 · 边界、一致性、披露（149 条）

### 4.1 基础设施与缓存（B1）

| ID | 位置 | 现象与触发 | 置信度 | 最小验证 |
|---|---|---|---|---|
| B1-1 | `core/compute_guard.py:29-35` | 等 slot 期间请求被取消 → 线程稍后拿到名额但协程已死、`finally: release()` 永不执行 ⇒ **名额永久泄漏**；`COMPUTE_CONCURRENCY≤2`、14 个端点共用，累 2 次即全部恒 40103 直到重启。B1 亲自推翻了自己的 P1 假设：实测 uvicorn 0.30.6 断连只置 flag、Starlette `BaseHTTPMiddleware` 不 cancel 内层任务，故降为 P2，**升级判据已写明**（一旦加 `--timeout-graceful-shutdown`/换 ASGI server 立即升 P1） | 确定（机制已复现） | `_b1_probe_e_compute_leak.py` |
| B1-2 | `cache/redis_client.py:104-118,162-164` | Redis 故障期新值只落 LRU；恢复后 `get()` 命中 Redis **旧值**且 `get_stale→is_stale=False` ⇒ 旧值当新鲜返回（窗口=剩余 TTL，`stock.north` 最长 21600s） | 确定 | `_b1_probe_c_redis.py` → recovery get()=OLD |
| B1-3 | `db/init_db.py:40-44` | `foreign_keys`/`synchronous` 是**逐连接** PRAGMA，非 DB 级持久化；新连接 `foreign_keys=0` ⇒ `watchlist→instrument` 外键全程不生效（注释与实现不符） | 确定 | `_b1_probe_d_pragma.py` |
| B1-4 | `db/models.py:77-92` + `api/v1/alerts.py:295-304` | `watchlist` 表**全仓无生产写入方**（该 API 只有 GET，写方仅存在于 tests）⇒ `scope="watchlist"` 预警规则永远空转、不触发也不报错（父审核员复核 sqlite `watchlist` 行数 = 0） | 确定 | `rg -n "Watchlist" backend/app` |
| B1-7 | `core/auth.py:198-199` | 非 ASCII Bearer token 使 `hmac.compare_digest` 抛 `TypeError` ⇒ 鉴权失败被报成 **50000 系统错误** + 全栈 ERROR 日志（未认证者可刷，不越权） | 确定 | `require_auth(HTTPAuthorizationCredentials('Bearer','é'))` |
| B1-9 | `db/kv.py:41-45` vs `db/models.py:396-398` | 同一 `app_state.updated_at`：kv 写 `localtime`、ORM 写 `CURRENT_TIMESTAMP`(UTC)，**差 8h**（当前无读取方，影响小） | 疑似 | 对比 DB 值与 `datetime('now','localtime')` |
| B1-10 | `cache/redis_client.py:243-273` | 防抖锁两域不互斥（Redis 可用只写 Redis、故障只写本地），`unlock` 无条件双清 ⇒ Redis 恢复瞬间可双跑重建（浪费算力，无数据损坏） | 确定(推理) | 双域桩探针 |

### 4.2 领域与组合（B2）

| ID | 位置 | 现象与触发 | 置信度 | 最小验证 |
|---|---|---|---|---|
| B2-8 | `data/universe.py:93-100` vs `domain/limit.py:50-52` | 向量化 `(x*100+0.5).floor()/100` 在**二分位少一分**：只读核对生产 `universe_daily` 抽样 20 万行 ⇒ `limit_down` 不一致 **0.391%**、`limit_up` 0.031%（昨收 1.15 → 存 1.26，domain 算 1.27） | 确定 | `b2_probe13.py E2` ／ **已修**：加 `1e-9` 修正浮点表示误差，独立复算 160 万样本从 **0.826% 不一致 → 0**（见 §1 进展与 FIXES-APPLIED） |
| B2-9 | `data/universe.py:103-120,219-224` | `is_new_issue` 对所有板块都给「不设涨跌幅」；实测 473 行哨兵中 **460 行是主板 2023-04 前上市首日**（应 ±44%/−36%）；`_limit_pct_expr` 的 `days_since_list` 参数体内**从未使用** | 确定 | `b2_probe13.py E1` |
| B2-10 | `domain/limit.py:62-66` + `a_share_rules.py:18-25` | 北交所新代码段 `920xxx` 被判 main（`[48]\d{5}` 不匹配）→ `.SH`；真实 universe 516 万行 `board` 只有 main/chinext_star ⇒ `MARK_UP["bse"]` 与 bse 分支是**死分支** | 确定 | `b2_probe1.py P2` |
| ~~B2-11~~ | `domain/optimizer.py:302-306` + `engine.py:276` + `api/v1/research.py:421` | 两处调用都**不传 `expected_returns`** ⇒ μ=0 ⇒ 实测 λ=8 与 λ=50 权重**四位小数完全相同**（≈等权），「风险厌恶/MVO」无任何效果 | 确定 | `b2_probe9.py W4` → **修复后（按本报告 §S6 建议只披露、不接 μ）**：`/research/optimize` 返回 `expected_returns{basis:"unavailable",risk_aversion_effective:false,note}`；前端下拉改「MVO（μ 不可用 ⇒ 实为最小方差）」并显示告警。λ 惰性已固化为用例（L1=**0.0**），并以对照实验证明缺陷在调用方未给 μ：μ 异质时 λ 立刻生效（L1=**0.4593**）（第 13 轮） |
| ~~B2-12~~ | `domain/portfolio.py:169-171,285` | 接受 Σw∈[0.99,1.01] 但不归一 ⇒ 闲置现金恒在（Σw=0.995 → 0.6% 现金），drift 与目标权重永久不一致 | 确定 | `b2_probe7.py PF3` → **修复后**：容差内按 `1/Σw` **归一**后再执行/回显，并返回 `weights_normalization{input_sum,factor,note}`；Σw=0.98 仍**拒绝**（归一不是"来者不拒"）（第 13 轮） |
| B2-13 | `domain/factor_processing.py:137-140` | 布尔掩码索引二维数组导致**形状广播**：完全共线列正交化后仍完全相关（`F=[x,2x]` → `F'F=[[0.2,0.4],[0.4,0.8]]`）；`orthogonalize_factor_panel` d1 截面相关 = 1.0 | 确定 | `b2_probe11.py S2/S4` |
| B2-14 | `domain/attribution.py:27-31,71-72` | Σwb≠1 时缺口进 `residual`（Σwb=0.8 → residual=0.0046）却被文档称「纯 Alpha」；收益缺失的持仓整行静默丢弃。Σ 都为 1 且收益完整时守恒严格（residual=0.0） | 确定 | `b2_probe3.py AT1-AT4` |
| B2-15 | `domain/research.py:199-261` | 成交额缺失/为 0 时输出 `unfilled=nan`、`avg_impact_bps=0.0`，fill 记录含 NaN 金额 | 确定 | `b2_probe10.py N5` |
| B2-16 | `domain/risk.py:145,36-46` | `benchmark` 标签**硬编码「沪深300」**（与传入基准无关，违契约 5/6）；`risk_metrics` 夏普（日度 rf）与 `portfolio._compute_metrics`（年化 rf）**两条口径并存** | 确定 | `b2_probe2.py M1` → **修复后（影响面比原文更大，已一并修）**：①`benchmark` 标签由调用方传入的 `benchmark_symbol` 决定（**默认 `None`**，不再硬编码），`panels.py` 下传真实标识 `沪深300(sh000300)`；②rf **单源化**为 `metrics.RISK_FREE_ANNUAL=0.02`（年化，`risk.py` 默认改用它、响应披露 `rf_annual`），`portfolio._compute_metrics` 的形参由 `daily_rf` 改 `rf_annual` 并**真正生效**（此前形参从未被使用，函数体直读模块常量）⇒ 组合页数字**逐位不变**、个股页 Sharpe 按 2% 重算（第 13 轮） |
| B2-17 | `domain/risk.py:116-121` | `close[close>0]` 过滤掉中间 0 价后不重置窗口 ⇒ 收益序列出现跨日合成跳变，window 仍按过滤后长度报告 | 确定 | `b2_probe3.py RK1` |
| B2-20 | `domain/research.py:68-69`；`portfolio.py:87`；`chip.py:98,123-124`；`calendar.py:39`+`data/calendar_store.py:33-45` | MAD 按**列（时序）**去极值（docstring 说截面）；`cagr` 用 `len(nav)` 而非 `len(nav)-1`（~6bp）；`if current_price` 把 0 变收盘价；全零成交量仍产出筹码；空日历降级为「全非交易日」 | 确定 | `b2_probe6.py RS2` 等 |

### 4.3 数据层与选股（B3b）

| ID | 位置 | 现象与触发 | 置信度 | 最小验证 |
|---|---|---|---|---|
| A3 | `data/screening.py:755,837-845` | `as_of` 被截成 `"16:14:37"`：非交易时段返回**上一交易日收盘值**却标 `degraded=False` +「腾讯实时快照，截至 16:14:37」，无字段可辨日期（score 榜有 `_freshness`，stocks 无） | 确定 | `_as_of_time('2026-09-18 16:14:37')` |
| A4 | `data/screening.py:132,238` + `orchestrator.py:319-323` | `pred_score` 部分为 null 时 **null 排最前**（占 rank1 且被标 strong）；`enrich_items` 抛 `TypeError` 且**不走 unavailable 降级**；守卫仅覆盖整列 Null | 机制确定/触发疑似 | `pred_score=[None,0.9,...]` |
| A5 | `data/ingest/akshare_adapter.py:230,238-257` | 主源**结构性 ValueError**（缺 date 列）被 except 吞掉降级新浪（仅 WARNING），与 docstring「结构性异常会抛」矛盾；降级数据 `source` 仍写 `"akshare"` ⇒ EM/Sina 不可审计 | 确定 | 注入 EM ValueError + Sina 有数据 → rows=1 source=['akshare'] |
| A7 | `data/ingest/tasks.py:115-129,182-194` + `quality.py:78-79` | 写入门禁**不含日期/行数完整性校验**（`check_year_density` 既不调用、阈值也被归零）⇒ 短窗/截断抓取按 date 合并后**缺口静默留空**；`step_validate` 只看当日一行 | 确定 | 比对年分区行数 vs 该年交易日数 |
| A8 | `data/quotes_hub.py:180-181` + `api/v1/notify.py:88-90,124` | SSE 推送节奏硬编码 30s，请求参数 `interval`（文档称钳制 [15,120]）只当本地超时 ⇒ 传 15/120 都无效；订阅后首帧最长等 30s | 确定 | 读码；interval=120 时 60s 内仍推 2 次 |
| B1 | `data/ingest/multi_source.py`（整模块） | 「三源冗余 P1-2」**无任何生产调用方**（仅 tests）⇒ 生产只有 EM→Sina 两源；且 `_from_akshare:50-52` 把**空数据**（停牌/退市）当失败 raise，与「空=无数据非故障」语义相反 | 确定 | `rg fetch_daily_bar_multi backend --glob '!**/.venv/**'` |
| B2 | `data/ingest/announcements.py:71,82` | 写路径 `save_announcements` 无生产调用方，而**读路径已接线**（`panels.py:199`、`market.py:259`）⇒ 生产公告块依赖手工种数据（sqlite `news_announcement` 行数 = 0） | 确定 | `rg save_announcements` |
| B3 | `data/ingest/financials.py:20,44,61` | 三个函数均无生产调用方 ⇒ **PIT 财务表永不落库**；个股财务块走东财实时（无 PIT 保证） | 确定 | `rg load_financials_asof\|save_financials` |
| B4 | `data/ingest/dividends.py:52-79` vs `:82-94` | save 写 SQLite `dividend_split`、load 读 `DATA_ROOT/dividend_split/...parquet`（全仓无写入方）⇒ 即便接线仍恒空；save 用 `asyncio.run`，async 上下文调用即 RuntimeError（父审核员复核 `dividend_split` 行数 = 0） | 确定 | `rg dividend_split` |
| B7 | `data/text_ingest.py:324` | `attach_text_features` 无调用方，`app/ml` 全目录不引用 `text_features/sentiment` ⇒ **文本因子永不入模**，docstring 声称「训练/推理侧消费」未实现 | 确定 | `rg 'text_features\|sentiment\|attach_text' backend/app/ml` |

### 4.4 ML（B4a）

| ID | 位置 | 现象与触发 | 置信度 | 最小验证 |
|---|---|---|---|---|
| R3 | `ml/monitor.py:285-323,702-704` | （与 P1-17 同根因，此处记其运维后果）drift 自 09-03 持续 degraded 15 天，12h 一次重训 | 确定 | 真实快照 PSI 重算 |
| R5 | `ml/monitor.py:641-647` | 1.5σ 用跨池宽度混合的 `hist_std=0.1151`（119 只时代），当前池 2490 只噪声 0.020 ⇒ 门槛≈−0.126，**衰减检测近乎失效** | 策略 | 确定 | 探针 3/5 |
| R8 | `ml/features.py:33`、`orchestrator.py:233-238`、`monitor.py:138-156` | 同一 `feature_version` **三套解析口径**（常量/注册表/配置+mtime）；monitor 快照不披露读了哪一版；`predictions.feature_version` 列无人交叉校验 | 一致性 | 确定 | grep + probe3 |
| R9 | `ml/train_lgbm.py:179-200,396`、`registry.py:207,213` | `_register_model` 的 `kept/horizons` 形参未使用 ⇒ `params_json.kept_features` 恒 `[]`（features.json 实有 60 列）；`test_end` 列永写 NULL | 死数据/审计 | 确定 | probe1 + DB 副本 |
| R11 | `ml/features_v2.py:110`、`features.py:44-47` | 整模块**未接线**（仅 `tests/test_p2_rest.py` 调用，版本未登记白名单）；另 `market_ret` 不排序即 `pct_change`、`ret_skew_rank` 名不符实 | 死代码/多实现 | 确定 | `rg build_alpha_v2` |
| R12 | `ml/features.py:241-263` 等 | （与 P1-18 同源）增量守卫失败会触发全量重算并重写历史 g1_ | 一致性 | 确定 | 探针 9 | ✅ **已修（第 15 轮）**：守卫失败回退全量这一路径仍在，但已把它从"**静默**改写"改为受控——冻结节点集后新标的取值不入图，两条漂移（边集指纹变化 / 冻结标的无行情）默认抛错且不落盘 |
| R13 | `ml/train_lgbm.py:224,276-282`、`monitor.py:540-546`、`predict.py:8-9,132-137` | `xsec_demean` **未落** metrics/params/注册表 ⇒ 输出实为**截面相对收益**，`pred_return` 仍描述为绝对收益点估计 | 一致性 | 确定 | probe1 |
| R14 | `ml/registry.py:393-508` | 门禁**不校验** `feature_version/dataset_version/验证区间可比性`（生产 `ds_1133s…r2022` vs 候选 `ds_1729s…r2018` 直接比 IC） | 一致性 | 确定 | DB 副本 + repro |
| R19 | `ml/train_lgbm.py:244` | `gap_days` 无 `>= horizon` 断言（`test_ml_leakage:135-137` 自认「由调用方保证」）；当前调用方均 ≥horizon，未触发 | 疑似 | grep `gap_days=` |
| R20 | `ml/purged_cv.py:38-46,94-119` + `api/v1/research.py:281` | purge 默认硬编码 5 不与 `ML_LABEL_HORIZON` 联动；`verify_no_overlap` 比较基准是同义反复（恒 True）；`/cv-folds` 允许 `purge_window=0` | 一致性 | 确定 | 读码推导 |

### 4.5 回测与交易（B5）

| ID | 位置 | 现象与触发 | 置信度 | 最小验证 |
|---|---|---|---|---|
| B5-03 | `trading/paper.py:220-237` vs `:298,325` | 冲击已打进 `exec_px`，又在 fee 里按 `amount×impact_bps` **再收一次**（二次计费）：100 股@10.01/10bp → 多扣 1.00 元 | 确定 | 探针 |
| ~~B5-08~~ **已撤回（2026-09-21 实测证伪，见 §9.3 R-7）** | `broker.py:380-397` + `domain/metrics.py:109-116` | ~~换手 = 买卖**双边累加** ⇒ `annual_turnover` 与 decay 成本 **2×**；全仓换标的实测 0.94（真实≈0.47）~~ → **实测：`match` 仅 3 处调用、从不传买卖混合列表，且每次覆盖 ⇒ 原值本就单边（全仓换标的 0.9483 = 买腿，即标准单边换手）**。同处**确有另一类真实缺陷**：取值取决于"最后一次 match 是哪条腿"，仅清仓日拿到卖腿 0.95 而标准单边 0.475 ⇒ **2× 多扣**（已按 Σ\|Δw\|/2 修正） | ~~确定~~ → **疑似（方向对、场景错）** | `backend/.tmp_testrun/b508_turnover_verdict.py`（三场景） |
| B5-10 | `broker.py:171-175`；`domain/a_share_rules.py:90-111` | Broker 对**所有卖出收印花税（含 ETF）**，而 `ma_cross.py:41`、`portfolio.py:303`、`paper.py`、`a_share_rules` 四处豁免 ETF；且 `a_share_rules` 的 4 个规则函数**零调用方**（费率硬编码 4 份） | 确定 | `rg "stamp_duty_rate\|is_t_plus_one" app/` ／ **已修**：单一来源 + 实测四套判定对照（见 §1 修复进展） |
| B5-11 | `api/v1/backtest.py:335-338` | 策略路径涨跌停是「昨收×(1+板块pct)」**幅度近似**：丢 ST 5%、丢新股无涨跌幅、qfq 域 `round(2)` 与 raw 域取整错位（容差仅 1bp） | 确定 | `_load_strategy_bars` 对 ST 返回 ×1.10 |
| B5-12 | `api/v1/backtest.py:525-528` + `strategy_base.py:296-298,480-481` | 基准不可得时造一行 `close=1.0` ⇒ `annual_benchmark=0.0`，与真实 0% 不可区分（合成数据冒充真实行情） | 确定 | `run_strategy(benchmark=None)` |
| B5-14 | `data/universe.py:402-403,425-430` | `list_date` **97.8% NULL** ⇒「仅保留已上市」过滤失效（001306.SZ 有 452 行上市前占位）；`is_new_issue` 恒 False ⇒ 新股不设涨跌幅失效（大涨误拒买、暴跌误拒卖） | 确定 | 读 `universe_daily_bt` 该标的首行 |
| B5-19 | `api/v1/backtest.py:505` vs `:540-542` | `short_ma>=long_ma` 校验只作用于请求级参数，**寻优 overrides 绕过** ⇒ 非法组合进入选优 | 确定 | 读码 |
| B5-21 | `backtest/engine.py:73-74,361-363` | `sig_d.empty` 时返回现有持仓但仍被等权再平衡，权重漂移>10% 时**无信号日也会产生交易** | 疑似 | 造 prediction 空洞区间 |

### 4.6 API 层（B7b）

| ID | 位置 | 现象与触发 | 置信度 | 最小验证 |
|---|---|---|---|---|
| F2 | `notify.py:83-91` + `Dockerfile:59` + `docs/项目开发文档.md:5814` | SSE ticket 走 query string，而**无任何启动路径加 `--no-access-log`**（Dockerfile/README/run.md/nightly.yml 零命中），uvicorn access log 含 query ⇒ **ticket 落容器 stdout**；Nginx `/api/` 也未关。违背 `auth.py:51` 自述「ticket 绝不写入日志」 | 确定（静态链） | 全仓 grep + `uvicorn ... --port 8010` + curl |
| F3 | `export.py:22,46-49,93` + `core/excel.py:44,106` | `board`（无 pattern）、`strategy_name`（仅 max_length）原样进 xlsx `meta` sheet；openpyxl 把前导 `=` 写成公式单元格 ⇒ **Excel 打开即求值**（实测 `"=cmd\|'/c calc'!A1"` → `data_type='f'`） | 确定 | openpyxl 探针 |
| F4 | `ops.py:52` | 质量扫描 `sym_dirs[:200]` **硬截断**（实测 2499 个标的目录全部有 year=2026 分区，故仅扫 **8.0%**），响应无 `truncated`/`total_symbols` ⇒ `n_issues:0` 被读成「全库干净」 | 确定 | `Get-ChildItem data/parquet/daily_bar -Directory -Filter 'symbol=*'` → 2499 |
| F5 | `export.py:20,38` | `?date=not-a-date` → 客户端见 `http=200 code=50000`「系统暂不可用」（应为 40000）；ValueError 被 `ServerErrorMiddleware` 重抛 ⇒ uvicorn 额外打 ERROR 全栈（告警误报） | 确定 | ASGI 直调 |
| F7 | `ops.py:448-455` | `_run` 吞掉所有异常返回 `ok({"ok":False})` ⇒ **重跑失败/管道繁忙仍返回 `code=0 message=ok`** | 确定 | `POST /ops/dag/rerun {"trade_date":"9999-99-99"}` → code=0（对照 `{"trade_date":"bad"}` → 40000） |
| F8 | `studio.py:67` + `gp_miner.py:457-482` | `/mining/start` **无** `compute_guard`/`pipeline_lock`/running 门禁（训练/同步/回测均有）；`_TASKS>20` 淘汰**不区分状态** ⇒ ≥21 次启动后运行中任务变「任务不存在」，不可查不可取消但仍在烧 CPU | 确定（静态） | 读码对照 `train_service.py:419-443` |
| F9 | `datacenter.py:132-197` | `/datasets` 分区读失败只 `logger.warning`，返回体无 `failed`/`partial` ⇒ 响应自相矛盾（`symbols:2, rows:2`，实为 1 可读） | 确定 | 造 1 健康 + 1 损坏分区 |

### 4.7 前端（B9a 行情/个股 · B8 基础设施 · B9b 量化页 · B9c 运维页）

**B9a 行情/个股页（P1/P2）**

| ID | 位置 | 现象与触发 | 置信度 |
|---|---|---|---|
| F-01/F-02 | `pages/StockDetail/*` | 切换标的时**旧状态驻留 + 竞态**（旧响应覆盖新响应） | 确定 |
| F-03 | `pages/MarketOverview/KpiCards.tsx` | 字面量 `loading` ⇒ **永久骨架屏** | 确定 |
| F-04 | `pages/MarketOverview/*` | 沪深300 兜底取 `items[1]` ⇒ **错标指数** | 确定 |
| F-05 | `pages/MarketOverview/MoneyFlowPanel.tsx` | 「走势」列实为**上证 sparkline 伪数据** | 确定 |
| F-06 | `pages/Watchlist/*` | 不消费 `status='degraded'` | 确定 |
| F-07 | `pages/Screener/*` | 导出按钮未按角色禁用（= P1-23 之一） | 确定 |

**B9b 回测/组合/研究页（P2 为主；P1 已入 §3）**

| ID | 位置 | 现象与触发 | 置信度 |
|---|---|---|---|
| I-4 | `Backtest/parts.tsx:83-91,96-106` | 寻优候选值**无条数/笛卡尔积上限**（只校验「是数字」）⇒ 前端可构造 1e6+ 组合打到后端先物化后守卫的 OOM 路径（P0-5），单进程部署 | 确定 |
| I-5 | `Portfolio/index.tsx:145-147,164,418-419` | `fmtPct(metrics.x*100)`：JS `null*100===0` ⇒ NaN 经 pydantic 变 null 后显示**假「+0.00%」**；基准首值 0 时 beta/alpha 静默退化 1.0/0.0 且基准线消失无提示 | 确定(机制)/疑似(触发) |
| I-6 | `Portfolio/index.tsx:213-223` | 资产搜索无 abort/序号 ⇒ 旧响应覆盖新响应；300ms 内清空输入则 `searching` 永久卡 true；catch 把失败吞成「无匹配」 | 确定 |
| I-7 | `Report/index.tsx:42-53` | 快速切换历史期/重新生成时旧日报覆盖新选择（期数标签与选项不符） | 确定 |
| I-8 | `Research/index.tsx:269-285,298-305,307-320` | 因子勾选/中性化/CV 参数/优化器**四类重算并发无序**（ComputeQueue 只限并发 2、不保证顺序）⇒ chips 与图表不同源 | 确定 |
| I-9 | `CapacityAttribution/index.tsx:40-49` | 容量滑块 400ms 防抖不足，两请求并发时旧响应覆盖 ⇒ aum 与滑块值/公式串不一致 | 疑似 |
| I-10 | `Backtest/index.tsx:36-63,237-239` | 回测类 api **不接受 `AbortSignal`**（对比 `api/research.ts` 有）⇒ 卸载不取消 120s/600s 请求；切 Tab 重挂载自动重跑整次回测 | 确定 |
| I-11 | `TopKPanel.tsx:56-118`；`api/strategyBacktest.ts:74-81,120` | `liquidity.note`（含 impact 口径）/`friction_costs`/`rejected_trades` **全部不渲染**（默认 `enable_friction=false` 且无提示）；文案写 `universe_daily` 实为 `universe_daily_bt`(hfq)；`index.tsx:88` **硬编码「QFQ」**而后端 `source` 死变量从不披露（原 P0-4，现 P2 的**前端延伸**） | 确定 |
| I-12 | `Research/index.tsx:635-643`；`api/research.ts:101` | 后端已按 close 修正 VWAP 坏点并返 `data_warnings`（默认标的 000001.SZ 正命中已知坏点），**前端无消费** ⇒ 修过的数据不告知 | 确定 |
| I-13 | `FactorStudio/index.tsx:349-357` | 后端 `scored` 里求值失败条目 `st={}` 会进 `task.history[:8]`（有效表达式 <8 个时），前端 `e.icir.toFixed(3)` 无 `?.` ⇒ TypeError 白屏 | 疑似（落库数据未复现） |
| ~~I-14~~ | `Research/index.tsx:559-569` | 下拉「Mean-Variance（均值-方差）」实际 μ 恒 0（后端未传 `expected_returns`，B2-11）⇒ **名不副实** | 确定（标签）/后端已证 | **已修（第 13 轮）**：标签改「MVO（μ 不可用 ⇒ 实为最小方差）」+ 消费后端 `expected_returns` 告警（`role="alert"`） |

**B8 基础设施（P2）**

| ID | 位置 | 现象与触发 | 置信度 |
|---|---|---|---|
| B8-06 | `hooks/useWatchlistQuotes.ts:50-64` | `load` 无代际守卫/无 abort ⇒ 自选集合变化时旧响应后到覆盖新数据（`Watchlist:186`、`Screener:168`） | 确定 |
| B8-07 | `types/stock.ts:145` 与 `:430` | `MoneyFlowBlock` **同名双声明被 TS 合并** ⇒ 市场侧类型被收紧成个股形状（8 个必填），`MarketOverviewData:507`、`OverviewRt:523` 的契约是假的（B8 用 `tsc --strict` 探针实测 **TS2740**） | 确定 |
| B8-10 | `components/ui/index.tsx:61-74` | `ErrorState` **丢弃 `message` 形参**（只用 `onRetry`）⇒ 恒显示「数据源暂时没有响应」，`EtfDetail:376` 传的真实错误被吞 | 确定 |

**B9c 数据中心/运维页（P2）**

| ID | 位置 | 现象与触发 | 置信度 |
|---|---|---|---|
| C-1 | `DataCenter/index.tsx:70-110` | `DiskGauge` 在 `percent==null` 分支**先 return**，hooks 数量 3↔0；`disk_usage_percent` 由 null↔数值翻转一次即抛 hooks mismatch ⇒ 根级 ErrorBoundary（`main.tsx:12`）**整站降级** | 疑似 |
| C-2 | `DataCenter/index.tsx:269-271,306-308,343-347` | 「可抓取 N 只/…等 N 只」用的 `total` = 后端 `len(rows)`（≤limit=50），被当成 instrument 全量（同页 `covered_total` 是数千） | 确定 |
| C-3 | `OrderDesk/index.tsx:123-134,403`；`api/production.ts:249` | 母单只取最近 50 条（`desk.py:212` 无 total/truncated），客户端搜索/筛选/分页/计数**全建立在截断窗口上** | 确定 |
| C-4 | `Alerts/index.tsx:200,212,218,248-253` | 未读徽标与「全部已读」只覆盖 `events?limit=50` 窗口 ⇒ **假清零**（后端已支持 `{all:true}` 未用）；超窗口未读永不显示 | 确定 |
| C-5 | `Alerts/index.tsx:167-170` | `recent_truncated`/`truncated`/`recent_limit`/`supplement_limit` 与 `last_error` 全被丢弃，饱和的 `failed_jobs`(5/20) 当精确值 | 确定 |
| C-6 | `DataQuality/index.tsx:110,137,154-155,186-188` | `ops.py:52` 的 200 只截断（覆盖 ~8%）+ `:47` `year=2026` 硬编码均无披露字段；页面把截断值写成「扫描范围」，**0 覆盖时显示绿色「未检出任何质量问题」** | 确定 |
| C-7 | `Pipeline/index.tsx:8,13-24` | `STAGE_ORDER` 含后端**不存在**的 `desk` ⇒ DAG 图恒多一个琥珀假节点（后端只有 5 个 stage） | 确定 |
| C-8 | `Pipeline/index.tsx:131-134` | 状态非 SUCCESS 即染红（PENDING/RUNNING 也红），`duration_ms` 为 NULL 时 `null/1000` → 「0.0s」 | 确定 |
| C-9 | `Settings/index.tsx:588,609,618,414,440,467,196` | viewer 级页面无角色门控：`/data/sync`、`/connectors/test`(researcher)、`/data/cache/clear`、`/db/backup`(admin) 均可点；**挂载即 2 次越权 test**（= P1-23） | 确定 |
| C-10 | `Settings/index.tsx:163,603,605-607` | 「自动清理缓存（N 天前数据）」滑条**完全未接线**：无 API 消费，后端 clear 端点也无保留期参数 | 确定 |
| C-11 | `DataCenter/index.tsx:381-383,391-397,491-497,644-646` | `autoSync` 复选框 `useState(true)` **硬编码默认** + `autoStatus`/`toggleAuto` 失败静默 ⇒ viewer 长期看到「每天 15:45 自动更新」已勾选，取消后也被下次 `loadAll` 复位 | 确定 |
| C-20 | `OrderDesk/index.tsx:366-371` | 熔断状态**读取失败**(kill=null) ⇒ 渲染绿色「正常运行」+「未完成母单：—」，把「不可读」当「安全」 | 确定 |
| C-21 | `Settings/index.tsx:421-425` | 刷新频率下拉 3 个选项标签**全是「管理配置」**（值与文案错位），用户无法辨别 3/5/10 秒 | 确定 |
| C-22 | `DataCenter/index.tsx:633-637`；`Alerts/index.tsx:366-369` | 全量重构/修复缺漏**一键即发**、删除预警规则**无确认**（对照 `:613-615` 清缓存、`OrderDesk:221` 熔断都有 `ConfirmModal`）⇒ 危险操作确认口径不一致 | 确定 |

### 4.8 P4 前后端契约对账（19 条，全量非抽样）

方法：后端端点清单 = AST 扫描 + 运行时 `app.routes` 内省（两侧均 **114** 条）；前端调用清单 = 平衡括号扫描器（**111** 处调用点）；六项对账。**反向清单：前端调用不存在的端点 = 空（111/111 命中）**——这是重要的正面结论（无「功能必然报错」类缺陷）。

| 序 | 类型 | 位置（前端 ↔ 后端） | 现象 | 严重度 |
|---|---|---|---|---|
| 8 | 错误/码 | `client.ts:52,62`；`types/api.ts:29`（`ERR.FORBIDDEN` **零引用**）↔ `auth.py:210`+`errors.py:94` | 角色不足**直出英文 `FORBIDDEN`**（sanitize 正则匹配不到该串）⇒ 用户看到 `FORBIDDEN` 而非中文提示。**父审核员独立复核**：`ERR.FORBIDDEN` 全前端仅 `client.ts:23` 的**注释**提及，**无任何代码引用** | P2 |
| 9 | 码 | `client.ts:49`（特判 `ERR.PIPELINE_BUSY`）↔ `datacenter.py:684,952` `fail(4002)` | 同步忙返 `4002` ⇒ 前端「流水线正在执行」提示**永不触发**。**父审核员独立复核**：`ERR.PIPELINE_BUSY` 确实有处理器（`client.ts:49`、`Research:60`），但后端该路径返回的是裸 `4002`，两者不是同一个码 ⇒ 提示路径不可达 | P2 |
| 10 | 码 | 前端 `ERR` 表未定义 ↔ `backtest.py:318,325,352,431,504,506,508,537,545,566` | **8 个码 `40010~40017` 两侧均未注册** | P2 |
| 11 | 码 | 同上 ↔ `datacenter.py:689(5000),969(4003),1017(5001)` | 越界码落系统码段；`5001` 与 `ERR_PANIC_CONTAINED=50001` **形近易混** | P2 |
| 12 | 响应 | `types/watchlist.ts:37-40`（`status`/`reason`/`flow_status` 全 0 命中）↔ `watchlist.py:327,343-365` | **降级态无人消费**：全 null 行情按正常数据渲染，无降级横幅 | P2 |
| 13 | 响应 | `types/p1.ts:169` + `TopKPanel.tsx:112-117` ↔ `backtest.py:186-224` | `nav_tail` **后端从不返回**（全仓 rg 0 命中）⇒ 「最近净值」块因可选链静默 false **永不渲染** | P2 |
| 14 | 响应 | `TopKPanel`（`equity`/`drawdown`/`annual`/`holdings`/`trades`/`friction`/`liquidity`/`universe_scope` 全 0 命中）↔ `backtest.py:209-223` | Top-K 回测**无任何曲线**；生存偏差与冲击成本披露到不了用户 | P2 |
| 16 | 一致性 | `AuthBootstrap.tsx:26`、`usePreferencesStore.ts:28`、`Settings/index.tsx:210` ↔ `app_settings.py:157`+`datacenter.py:461` | **把 `Query` 对象当 `refresh` 传给 `overview()`**（`bool(Query(0)) is True`，已实证）⇒ **每次 `/settings` 都清空 DC 统计缓存**，逼出 27~30s 重扫（这正是前端注释里"冷算 27~30s"的成因） | P2 |
| 17 | 死代码 | `alerts.ts:64`、`market.ts:55,83`、`datacenter.ts:39,83` ↔ `alerts.py:189`、`market.py:805`、`datacenter.py:726-729,1149-1152` | 5 个包装死；`/market/overview` 与 `/market/quotes` **整条端点无消费者** | P3 |
| 18 | 响应 | `types/p1.ts:112`(`basis`/`basis_fields`)、`types/stock.ts`(`kind`) ↔ `screener.py:647`、`market.py:512` | **机读口径字段无消费者**，只剩自由文本 + 硬编码兜底 | P3 |
| 19 | 一致性 | `Alerts/index.tsx:200,251` ↔ `alerts.py:248`(researcher) vs `:271`(viewer) | 同组内 `events/read` 门槛**低于** `events`（= P3 F11） | P3 |
| 15 | 响应 | `client.ts:111-112` | 非信封 200 **被原样放行**（有意为静态文件），仅指出副作用 | P3 |

**后端存在但前端从未调用（死接口）**：`GET /alerts/health`、`POST /settings/apikeys/rotate`、`GET /market/overview`、`GET /market/quotes`、`PUT /alerts/rules/{id}`、`GET /datacenter/sync/tasks/{task_id}`、`GET /datacenter/train/status`（包装死、端点活）、`notify/stream?channels=…`（前端从不传 `channels`）。

**P4 已排除的假阳性**（避免后续重复投入）：`turnover` 单位（`daily_bar` 实测小数口径 0.0050，`*100` 是必要换算，仅注释陈旧）、`screener` 的 `total==0→ok`（9-14 已裁定并落测试，见 P0-6）、聚合接口的 45~60s 超时放宽（与实测耗时匹配）。

### 4.9 API 读端点其余（B7a 的 P2/P3，独立实测）

| 序 | 位置 | 现象 | 严重度/置信度 |
|---|---|---|---|
| 06 | `market.py:151`（同型 `:491`） | 降级 `reason` **外泄内部异常串**（含 `RemoteDisconnected`）并原样进 JSON；同文件 `:217-223` 注释称该泄漏「已修」，`_build_heat`/`_build_ai_stats` **漏改** | P2/确定 |
| 08 | `research.py:548-551` | `universe_daily` 无 parquet 时 `pl.concat([])` → `ValueError` 未捕获 ⇒ `code=50000`（现网盘上 9 分区故未爆，属**部分数据态裸 500**） | P2/确定 |
| 11 | `research.py:244` | `/research/experiments` SQL 硬编码 `LIMIT 30`、响应裸 list 无 total/truncated，docstring 却写「全部实验」（本仓 registry 有 72 行）⇒ 静默截断 | P2/确定 |
| 12 | `etf.py:507-530`（对照 `data/etf.py:422-423`） | 详情 header 丢弃 catalog 的 `size_basis`/`quote_status`：美股 ETF 规模是平台按汇率**自算值**，列表披露、**详情不披露**（前端类型也未声明 `size_basis`） | P2/确定 |
| 13 | `market.py:606/707/749/805` | 4 个 market 读端点无 `require_role`；**判为有意**（`docs/项目文档.md:1424` 登记匿名 + `App.tsx:86` 注释 + B7b 判定）。**但**由此 `recommend`（ML 推荐榜/候选/公告）也匿名可取 → 建议**收敛 recommend 字段**而非整端点 | P2（裁决项） |
| 14 | `report.py:243` | 重合率分母**硬编码 `k=50`**（分子来自 `head(50)`，分区 <50 行时系统性低估；相邻 mean 用 `len()` 口径不一致） | P3/确定 |
| 16 | `report.py:453-464` | `/report/daily` 的 `date` **无 pattern**（同批 market/screener 均有）⇒ `?date=abc\|2026-99-99` → `code=0` + `report:null`，无法区分「无日报/未生成」，且无三态 | P3/确定 |
| 17 | `research.py:585-594` | `/research/lab/yearly` 三种无数据情形一律 `data:[]` + `code=0`，无 status/reason ⇒ 前端无法区分「没数据/被样本阈值过滤」 | P3/确定 |
| 18 | `data/screening.py:236` vs `:245` | `:236` ×100（已是百分比，前端只加 `%`），`:245` 注释却写「小数口径、前端换算」（与同文件 `:655` 自相矛盾）；实测 `daily_bar` turnover 均值 0.0067 确为小数 ⇒ **当前数值正确、注释误导**；`domain/chip.py:38-43` 承认双口径共存，**无归一化则上游改口径会 100× 放大** | P3/确定 |
| 19 | `research.py:147-148` | 容量口径披露字段名为 `capacity_formula` 而非契约建议的 `basis`/`kind:"platform"`（披露存在，**命名偏离**） | P3/确定 |
| 09 | `market.py:480` | `"label_price_basis": "hfq" if hfq_files else "raw_fallback"` 的 **else 不可达**（`:435-437` 已提前 return unavailable）⇒ 永假的口径值 | P3/死代码 **✅ 已修（第 7–8 轮）**：删除 else，OK 路径恒 `"hfq"`；同批修 B7a-08（空 `universe_daily` ⇒ `ERR_DATA_EMPTY`，原为裸 50000） |

### 4.10 ML 训练与模型（B4b）

| ID | 位置 | 现象与触发 | 严重度/置信度 |
|---|---|---|---|
| B4b-4 | `scripts/grid_search.py:35,37` + `train_lgbm.py:53` | `min_child_samples` 被 `min_data_in_leaf` **别名覆盖**（canonical 恒胜）⇒ **81 组网格只有 27 组有效**（2/3 纯空转）；生产 `feature_runs` 648 行的 distinct `valid_ic` 只有 24 个，且 `params_json` 记下的是**没生效的超参值** | P2/确定 |
| B4b-6 | `scripts/grid_search.py:71,72,116` | `dataset_version="p1_2022plus"` 是**函数默认值被原样写库**（648 行全是该字面量），实际数据是 `alpha_basic_v1`；top-1 模型登记的 `dataset_version=""` ⇒ 模型血缘不可信 | P2/确定 |
| B4b-7 | `scripts/p2_experiment.py:71-75` | 硬编码 `features/version=alpha_basic_v1`，而生产 `features.json` 含 20 个 `g1_*` ⇒ `predict_with_contrib` 首步 raise「特征列缺失」——**该实验脚本已不可运行** | P2/确定 |
| B4b-8 | `app/ml/purged_cv.py:57-75,119` | `min_gap_ok` 的阈值传的就是切分器内部**同一个** purge+embargo ⇒ **恒真**（`purge=0/embargo=0` 时 gap=0、标签重叠仍报 True）——无效守卫 | P2/确定 |
| B4b-9 | `app/ml/purged_cv.py:38-39` + `api/v1/research.py:280-282` | `purge=5/embargo=2` 裸常量，与 `ML_LABEL_HORIZON` **无关联**；`/research/cv-folds` 允许 `0/0` 且无 horizon 入参（而 `train_lgbm` 的纪律是 `gap=horizon`） | P2/确定 |
| B4b-10 | `app/ml/purged_cv.py:94,145` + `app/ml/alpha_expr.py:286,289` | `cv_rank_ic_report`/`volatility_inverse_weights_from_close`/`build_alpha158_lite` **全仓仅测试调用** ⇒ **ML 侧没有 walk-forward/嵌套 CV**（optuna+WF 只存在于回测 `param_search`） | P2/确定 |
| B4b-12 | `scripts/train*.py:42/83` + `app/services/train_service.py:244-256` | **全表进内存**：真实 v2g 2,481,630 行 × 88 列（parquet 合计 1.03GB），峰值副本 4–5 份 ≈ **4–6GB**；GNN 路径 float32 单份 1.8GB 且 `X[tr]` 再复制，**无内存守卫**。与 P0-5（param_search OOM）构成同一类风险 | P2/确定 |
| B4b-20 | `scripts/grid_search.py:60-64` + `train_lgbm.py:340` | 超参选优**第一键是池化 Pearson `valid_ic`**，而门禁与业务口径是**截面 RankIC** ⇒ 选参在优化一个与目标正交的量（与 B4b-3/B4b-5 同根因） | P2/确定 |
| B4b-13 | `app/ml/alpha_expr.py:87,96-99` | **正向结论**：白名单**不可绕过**（15 种逃逸全拒）、无 DoS 路径（大指数/超大窗口 0.00s）。残留加固缺口：`Power` 不在 `errstate`（stderr 刷 overflow 警告）、`Log(0)`→`AttributeError`、`1/0`→`ZeroDivisionError`、width ≥ ~2000→`RecursionError`（被 500 字符上限挡住） | P3/确定 |
| B4b-16 | `app/ml/signal_analysis.py:119-126` | **重叠 h 日收益当日度序列**算 t 统计量（无 HAC ⇒ 虚高 ~√h），`ls_annualized = mean_ls*252` **夸大 h×**（**父审核员独立读码确认**：`:104` `fwd` 是 h 日远期收益、`:119` 的 `ls_daily` 每元素即 h 日价差、`:126` 却乘 252 ⇒ 函数默认 `h=5` 时 5×，调用方 `h=20` 时 **20×**）；docstring 只给 `ic_decay` 声明了未做 NW。**注意**：`ls_annualized` 正是前端 P1-25 想读的字段之一，但前端读的键名是 `spread_annualized`（**既"读不到"又"算错"**）。**P2-T7 已给出修法与断言** | P2/确定（升级：P3→P2） |
| B4b-17 | `train_service.py:172,283` + `train_lgbm.py:389` | `metrics.json`/`torch.save` **非 tmp+rename 原子写**；**不会覆盖旧模型**（时间戳目录 + 先落盘后登记）⇒ 仅产生孤儿/截断产物（风险有限） | P3/确定 |
| B4b-18 | `app/ml/torch_models.py:11-13,41-67` | `tft_v1` 实为 `Linear+TransformerEncoder+mean-pool` 的序列回归（**无变量选择/门控/分位**），**非真 TFT**；docstring 已声明 ⇒ **命名口径误导**（不报为 bug） | P3/确定 |
| B4b-11 | `scripts/train_tft.py:48` + `train_service.py:214` | 「TFT/GNN 不能 promote」是 `registry.py:531-539` 的**有意设计**（不报）；残留：`train_tft` 打印的 promote 指引**必失败**、`gnn_v1` 无 test 段（4 条候选 `test_rank_ic` 全 NULL） | P3/确定 |
| B4b-19 | `app/ml/gp_miner.py:469-479` | 台账条目延伸：`finally` 必执行 ⇒ 确会落 RUNNING；且**无任何 reaper 覆盖 `gp_tasks`**（启动期只回收 `retrain`/`task_store`）⇒ 状态永久卡死（触发概率低：线程内 `BaseException`）。与 F8（`_TASKS>20` 淘汰不区分 RUNNING，B4b-15 亦实测）**同根因，合并计一条** | P3/确定 |

**B4b 的负结论（重要，避免后续重复投入）**：**purge 纪律在 LGBM 侧是正确的** —— 真实生产产物验证 `train_end 2024-07-17 / gap1 5 日 / valid 252 日 / gap2 5 日 / test 252 日`，test 不参与早停与筛选；`num_threads` 确生效（`booster.params` 实测）；训练/推理特征同源且 `features.json` 列序被 `X[features]` 严格执行。缺口只在 `purged_cv` 模块（未接线 + 宽度与 horizon 脱钩）与 GNN 无 test 段。

### 4.11 存储 / 面板 / 日历（B3a）

| ID | 位置 | 现象与触发 | 严重度/置信度 |
|---|---|---|---|
| B3a-1 | `parquet_store.py:118-151,186-199` → `datacenter.py:274-310,380-382` | manifest `rows` **两条写路径语义冲突**（写路径=当年分区行数、扫描=全历史且不写 `first/last`）：生产 **61% 条目缺 first/last**，`/overview` 的 daily_bar 行数少报 **34.9%**、universe_daily **91.6%**，起止缩到 2026-01-05~09-18（真实 2018~2026）。**已知台账（manifest 口径）的机制级延伸**——前端资产清单走真实扫描故未污染，**`/overview` 契约字段已被污染** | P2/确定 |
| B3a-4 | `parquet_store.py:186-199` | 自愈**单向**（只判 `n_man < n_dir`）：目录被删/整只隔离后 manifest 仍报有数据（`read_symbol_dataset` 实测 0 行） | P2/确定 |
| B3a-5 | `parquet_store.py:376-416` | **无年份分区裁剪**：区间查询仍读全部 9 个年份文件（10.6 ms/只 vs 单年 1.5 ms/只，IO 7×）⇒ `step_validate` 每晚 ≈23~46s（可降到 3~7s）；`panels.build_chip/build_risk` 文档称「近似回看区间/有限窗口」实际开 5 个年份分区 | P2/确定 |
| B3a-6 | `parquet_store.py:398-406` vs `:305-336` | **一个坏分区 ⇒ `read_symbol_dataset` 抛 ComputeError 整只不可读**；而相邻 `read_parquet_columns` 静默跳过（返回 6 行）——同一数据集**两种可用性**，且后者有测试、前者无 | P2/确定 |
| B3a-7 | `cross_section.py:51-73` | `_scan_source` 每次构建**全量扫 11,087 文件 / 21.6s**，每晚 3 个 `MIRROR_DATASETS` ≈65s，无 mtime 索引/缓存 | P2/确定 |
| B3a-8 | `cross_section.py:60-73,99-149` | 「增量」**按源文件 mtime 而非日期**失效：改 1 只标的的当年分区 → **该年全部交易日判 stale**（built=3，期望 1）；叠加 nightly `write_partition` 重写整年 ⇒ **每天重建整个当年镜像**（实测 built 3/5，越补越慢），docstring 的增量承诺不成立 | P2/确定 |
| B3a-9 | `ml/features.py:21-52` | `FEATURE_VERSION` **为空**（生产默认）时按 **mtime 选版**：`touch` 旧版任一文件即**静默切版**且年份覆盖缩短（2018–2023 → 仅 2018–2021，**2022/2023 从读路径消失**）。与 B4a R8（三套解析口径）同根因 | P2/确定 |
| B3a-10 | `data/portfolio_source.py:84-109` | ETF 备源 `fund_etf_hist_sina` **无 `adjust` 参数=不复权**，主源用 `adjust="qfq"`；两者**共用缓存键**且返回类型无 `source`/`adjust` 字段 ⇒ 主源抖动后 **10 分钟内 ETF 价格口径静默改变**；备源调用未包 try（主源被吞、备源不吞） | P2/确定 |
| B3a-11 | `calendar_store.py:22,63-81` | `_CACHE` **无 TTL、无自愈**：启动时 DB 缺失 ⇒ **空日历常驻整个进程**（实测 DB 建好后仍返回 0）；空日历 ⇒ `is_trade_day` 恒 False ⇒ `evening_routine.py:52-60` **整天静默 `skipped`**（B2 条目延伸） | P2/确定 |
| B3a-12 | `domain/calendar.py:46-88`、`parquet_store.py:215-241` | **日历尾部边界不一致**：`last_completed_trade_day(now=2027-01-04)` 静默返回 2026-12-31（同日 `next_trade_day` 却抛 ValueError）；跨年后「今天」**冻结在日历末日最长 20 天**，且同步两侧都 ≥target 时**报成功** | P2/确定 |
| B3a-16 | `parquet_store.py:340-344,419-427` | **读路径有 mkdir 副作用**：`read_symbol_year` 读不存在的 symbol 会**创建空 `symbol=` 目录**（实测 1→2）⇒ 虚高目录数 → 无谓触发 manifest 重扫/WARNING | P3/确定 |
| B3a-17 | `parquet_store.py:517-527,93-96` | 进程被强杀残留 `.tmp` **无清理**（生产实测 1 个 `daily_bar_hfq/symbol=000065.SZ/year=2024…tmp` 12,408B，**永不回收**）；`_manifest_flush_locked` 失败时也不清理 tmp | P3/确定 |
| B3a-18 | `parquet_store.py:305-336/376-416/357-372` | 两条投影读**语义相反**（缺列→丢整个文件 vs 补 null）；投影无 `date` 且文件缺该列 → `InvalidOperationError`；`Float64+String` **静默升格为 String**（`1.0`→`'1.0'`）。生产 33,259 个文件列集/dtype 完全统一 ⇒ **当前为潜在** | P3/确定 |
| B3a-19 | `parquet_store.py:450-482` | `write_partition`/`write_year_batch` **不校验 df 年份与分区参数一致** ⇒ 2024 年行可写进 `year=2023` 分区（当前调用方都先按年切片，属潜在） | P3/确定 |
| B3a-20 | `universe.py:219-224,103-120` | `is_new_issue` 用**自然日** `<5`（规则是上市后 5 个**交易日**）；且 `:217-218` 注释声称主板 2023-04 前首日走 `pct=0.44` 分支，代码**对所有 `is_new_issue` 恒返回 `0.0`**，`days_since_list` 形参从未使用（= B2-9 的注释失真证据） | P3/确定 |
| B3a-21 | `universe.py:190-197,399-410` | 全市场**交叉连接不分批**：grid 5,285,385 行（6 列骨架 `estimated_size=338MB`）+ bars 163MB ⇒ 峰值 1~2GB；`cross_section.build_mirror` 明确按年分批，universe 没有（**疑似量级**） | P3/疑似 |
| B3a-22 | `pipeline.py:130-137` | **空代码集 + 日历降级** → coverage `0/max(1,0)=0 < 0.5` ⇒ 误报「整日/大面积数据丢失」致命错误（日历可用时空集合可通过） | P3/确定 |
| B3a-23 | `universe.py:495-504` | `read_prev_and_today` 对 close/volume 直接 `float()`，与同函数 `_num`(NaN→None) **口径不一致** ⇒ 含 null close 的分区抛 TypeError（写门禁当前拦 null，属潜在） | P3/疑似 |
| B3a-24 | `features/`(v1 **424.7MB**)、`universe_daily*`(93MB)、`cs/`(+69%) | 全仓 ≈**2.13GB**：`columns` 1457.5MB 中 424.7MB 是被 v2g 取代的 **v1**（仅 `scripts/p2_experiment.py` A/B 读）；三份 universe（legacy 无消费者）；cs 镜像每晚重建整年且 orphan 永不清；**无任何 TTL/保留/归档任务** | P3/确定 |

**B3a 的交叉定位（重要，避免后续误报归属）**：简报里的 **ffill 前视风险不在 `data/panels.py`**（该文件不做面板对齐/ffill，B3a 判定为「未发现 P0–P2」）。实际位置是：**`domain/portfolio.py:199-201`、`api/v1/desk.py:373,392,401`、`api/v1/research.py:546-558`、`ml/gp_miner.py:326`、`ml/train_service.py:247-254`**（分属 B2/B4/B7 权责）。其中 `gp_miner.py:326` 与 P0-7 同一个函数 ⇒ **修 P0-7 时须一并检查该 ffill 是否引入额外前视**。

**B3a 的负结论**：Decimal/date round-trip 精确；原子写同卷 `os.replace` + 进程内异常清理正常；生产 daily_bar/_hfq/_qfq、universe_daily(_bt) 共 33,259 文件**列集/dtype 完全统一**（故 §4.11 两条「投影」项为潜在而非现发）；`data/pipeline.py` 与 `orchestrator.py` **非重复实现**且接线正常；日历内容正确（6543 个交易日、节假日/周末抽查全对）；`panels.TTL` 有活跃消费方。

### 4.12 部署与运行链路（B0，P2/P3）

**方法说明**：本沙箱**有 `docker`/`docker-compose` CLI 但无 daemon** ⇒ B0 用 `docker-compose config` **真实解析** compose（`--no-interpolate` exit 0；空 `REDIS_PASSWORD` 时 `${:?}` exit 1 生效），故 DEP-06 的「api 环境只有 5 个键」是**工具输出而非推理**。P0 级结论见 §2 P0-1/P0-2/P0-8；P1 级见 §3 P1-43～P1-46。其余：

| # | 位置 | 现象 | 严重度/置信度 |
|---|---|---|---|
| DEP-16 | `.github/workflows/ci.yml`、`nightly.yml`（全文） | **CI 结构性看不见整套部署故障**：两个 workflow **从不 `docker build` / `docker compose config`**；且 CI 用**相对** `DATA_ROOT=./data/test_parquet` + 显式 `SQLITE_URL` 覆盖，**恰好绕过** `PROJECT_ROOT="/"` 派生的全部绝对路径；Nightly 还用 `npm run dev` 而非生产产物 ⇒ nginx/Dockerfile/compose 零端到端覆盖。**加两条 CI 步骤即可拦住全部 5 个 P0** | P2/确定 |
| DEP-21 | `scripts/purge_legacy_models.py:71` | `DELETE FROM model_registry` **无 `WHERE`** ⇒ 清空含 `is_production=1` 的行；脚本已查出 `n_prod` 却**不设防**（仅 `--apply` 作护栏） | P2/确定 |
| DEP-22 | `scripts/restore.py:14-31` + `scripts/backup.py:53` | **无确认**；`--file` 缺失/末位抛**裸 `IndexError`/`ValueError`**；`extractall` **无 `filter=`** 直接覆盖生产 `data/` | P2/确定 |
| DEP-23 | 全仓无 `.dockerignore` | 构建上下文 = 仓库根：实测 `.venv` **4.7GB** + `data` **2.18GB** + `node_modules` 149MB + `.git` 26MB ⇒ 每次 build 送 **≈7GB**（**含未跟踪的 `.env` 与生产数据**）进 daemon | P2/确定 |
| DEP-19 | 全仓无 `deploy/`、无 `prometheus.yml` | `/metrics` 已实现但**无抓取配置**；且 `ENV=dev` ⇒ **免认证**（未 publish 8000、nginx 不代理，暂不外泄） | P2/确定 |
| DEP-18 | `docker-compose.yml` 三服务均无 `logging:` | 默认 json-file **无 `max-size`**；容器内唯一日志出口是 stdout（且 P0-1/F3 会把它一起打死）⇒ 日志无限增长至磁盘满 | P2/确定 |
| DEP-20 | `app_settings.py:317-318` | 只读容器内 `backend/backups` 不可建 ⇒ 备份端点**必失败**，且被 `errors.py:160-174` 转成 **HTTP 200 + `code=50000`**（无根因、监控无法按状态码告警） | P2/确定 |
| DEP-24 | `frontend/Dockerfile` 无 `ARG`/`ENV` | 无 `VITE_API_BASE` 注入通道 ⇒ 无法产出指向异源 API 的镜像（**当前同源默认可用，非阻断**） | P2/确定 |
| DEP-25 | 全仓无 systemd/supervisor | 原生路径**无进程守护/开机自启/崩溃重启**（`run.md:160` 如实说明「没有一键脚本」，**非幻觉**） | P2/确定 |
| DEP-26 | `run.md:55,231-235` + `Dockerfile:2,9` | TA-Lib 排障段落指向 `requirements.txt` 中**不存在**的 `TA-Lib==0.4.28`；连带 builder 的 `build-essential`/`wget` 是**死重量** | P3/确定 |
| DEP-27 | `scripts/create_admin.py:53-54` + `run.md:198` | 密码经 **argv** 传入（shell history/进程列表可见）；已存在用户时「跳过」⇒ **无法轮换管理员密码** | P3/确定 |
| DEP-30 | `run_offline_tests.sh:6-10` + `pytest.ini:8` | `.sh` **硬编码 Windows venv 路径**（回落裸 `python3` ⇒ 环境漂移）；`pytest.ini` 的 `-v` 覆盖脚本 `-q` | P3/确定 |
| DEP-31 | `run.md:241` + `README.md:160-166` | 称 compose 是「**生产部署用的整体容器化方案**」并给出三步部署，而该方案**必崩**（P0-1 的 F1–F5）⇒ **虚假可用性声称** | P3/确定 |
| DEP-32 | `frontend/nginx.conf:16-21` | `location /assets/` 内的 `add_header` 会**覆盖** server 级 `add_header`（nginx 语义）⇒ `/assets/` 响应**丢失 3 个安全头** | P3/确定 |
| DEP-17 | `frontend/Dockerfile:12-15` | 未 COPY `frontend/public/` ⇒ 缺 `ref-watchlist.png`；SPA 源码零引用 ⇒ **无用户可见影响**（低危记录） | P2/确定 |
| DEP-28/29 | `.env.example:16` + `auth.py:154`；`docs/auth-register.md:38-39` + `config.py:137` | **已知 A-06**：`JWT_EXPIRE_SECONDS` 写进 `.env` **无效**（真源 `os.environ`）⇒ 恒 7 天；文档教写 `AQP_ALLOW_REGISTRATION`/`AQP_REGISTER_DEFAULT_ROLE`，但 `Settings` **无 `env_prefix`** ⇒ 静默丢弃 | P3/确定（已登记未修） |

**`.env.example` × `Settings` 三向对账（49 字段）**：生效 **25** / 写了无效 **1**（`JWT_EXPIRE_SECONDS`）/ 仅注释 **3** / **完全缺失 21**（含 `LOG_DIR`、`DATA_ROOT`、`MODEL_ROOT`、`EVENING_ROUTINE_ENABLED`、`AUTO_RETRAIN_ON_DRIFT`）。**`.env.example` 自身只有 2 个幻觉项**（`TZ` 非字段、`AQP_PANEL_BLOCK_TIMEOUT` 非字段）——**主要幻觉源在 `docs/项目开发文档.md` §13 与 `docs/auth-register.md:38-39`**（见 P1-44）。明细表见 [B0-deploy.md](B0-deploy.md) §3.1。

**B0 的两个「不构成缺陷」结论（避免后续按 P0 误报）**：
1. **SSE 没有被 nginx 缓冲** —— `nginx.conf:33` 确实 `proxy_buffering on`，但应用在 SSE 响应上设了 `X-Accel-Buffering: no`（`notify.py:34`→`:100/:153`），本配置**未设** `proxy_ignore_headers` 故该头生效；心跳 15s < `proxy_read_timeout 180s`；`gzip_types` 不含 `text/event-stream`。**「通知中心永远收不到事件」不成立**；但这是"靠一个响应头兜住"的脆弱等价（无 SSE 专用 location、容器路径零测试）⇒ 建议加专用 location，**但不要按 P0 缓冲故障上报**。
2. **前端不需要构建期 `VITE_API_BASE`，也不会白屏** —— `client.ts:80` `baseURL: import.meta.env.VITE_API_BASE ?? '/'` 回落**同源相对路径**，正好命中 nginx `location /api/`；`window.__DSH_BOOT__` 确属 DSH、**AQP 全仓无此符号**。真缺口只是「Dockerfile 无 ARG 钩子」（DEP-24），非故障。

**B0 的原生路径结论**：**按 `run.md` 步骤本身是能起来的**（bootstrap/ingest/init_db 入口均存在）；风险集中在**运维脚本语义**（P0-8 的备份删库、DEP-21/22）与缺少进程守护（DEP-25）。

### 4.13 披露字段端到端矩阵（P5，21 个带 `limit` 读端点全量实测）

**方法**：`fastapi.testclient` **不进入 `with`** 直连 ASGI（lifespan 不执行 ⇒ autoSync/evening_routine/alert_scheduler/overview warmer 全不启动，**零后台写入**）；遍历 21 个带 `limit` 的读端点各打 `limit=1` 与 `limit=-1` 并打印真实 `sorted(data.keys())`；`alerts._check_data_health` 用 `%TEMP%` 构造 SQLite（100 FAILED + 25 RUNNING）实测；`ops/quality-scan` 用 `%TEMP%` 构造 250 个 symbol 分区实测；`_scan_dataset` 用 1 好 1 坏分区实测。唯一 POST 是 `ops/quality-scan`（纯 `validate_partition`，不写 DB/parquet/quarantine）。

#### 4.13.1 ❌ 两端都无 = 静默（17 项，**这是 R10 的完整清单**）

| # | 场景 | 实测证据 |
|---|---|---|
| S1 | `ops/quality-scan` `sym_dirs[:200]` | 250 分区 → `symbols_scanned=200`，**响应无截断键**；UI（`DataQuality/index.tsx:137`）原样渲染"200 只"（生产 `daily_bar` 有 **2499** 分区 ⇒ 上限**必然生效**） |
| S2 | `datacenter._scan_dataset` 分区读失败 | 1 好 1 坏 → 返回 `{symbols:2,rows:200}`，**无 `failed`**，仅 WARNING（`datacenter.py:158-184`） |
| S3 | `overview.dataset_count` vs `/datacenter/datasets` | **同页 3 vs 8**，无解释字段 |
| S4 | `predict` 的 `xsec_demean` 标签口径 | `predict` keys 无 `demean`/`train_basis`（`train_lgbm.py:224-276`）；`pred_return`/`base_value` **绝对水平不可解释**（= P1-39 的对外表现） |
| S5 | `ml/labeling.n_gap_invalid` | 仅 `logger.warning`，无 API 无 UI |
| S6 | `watchlist/dashboard` 行情陈旧 | 实测 `quote_date="2026-09-17"`（滞后 2 交易日），无 `is_stale`/`lag_trading_days`，顶层无 `status`/`reason` |
| S7 | `stock/{symbol}/kline` 复权/日期口径 | keys `['adjust','bars','count','end','start','symbol']`，`adjust` **仅回显请求**，无 `basis`/`as_of`/`source` |
| S8 | `export/screener` | xlsx 内**无口径 sheet**，只在文件名带日期 |
| S9 | `alerts/events?limit` | **裸 list**，信封仅 5 键，无 `total`/`truncated`/`has_more` |
| S10 | `notify/recent` | `core/events.py:18 _RECENT_MAX=20` **静默丢弃**，裸 list |
| S11 | `desk/orders?limit`（默认 50） | 裸 list（当前库 6 笔，**潜在**） |
| S12 | 其余裸 list / 伪 total | `stock/search`、`portfolio/search`、`etf/hot`、`etf/flow`、`datacenter/logs`，以及 ~~**`datacenter/instruments` 的 `total=len(rows)`** —— 实测 **`limit=1 → total=1`**，真实 **5552** ⇒ **总数随 limit 一起缩小**（比没有 `total` 更糟）~~ **✅ 已修（P1-34 同批，FIXES-APPLIED 39）**：`total` 改独立 `COUNT(*)`，补 `returned`/`truncated`/`limit`，前端「可抓取 N 只」不再显示错误数字；另修 `limit` 无校验（`-1` 曾等于**全表**）。其余 5 个端点仍待办 |
| S13 | `screener` vs `screener/stocks` **三态互斥** | 同一时刻 `status:"degraded",reason:"data_stale"` **vs** `degraded:false,stale:false` |
| S14 | **裸错误码 15 个** | `4002/5000/4003/5001`（`datacenter.py:684,689,952,969,1017`）+ `40010~40017`（`backtest.py:318,325,352,431,504,506,508,537,545,566`），**两端码表都不含**（= R9） | **✅ 已修（2026-09-21，第 9 轮）**：① 15 个码全部登记（`40017` 按语义拆为 40017/40018/40019）；② `4002→40900`、`5000→40900`、`4003→51001`、`5001→50000`（**两处偏离 §8.3 建议**，理由见 `FIXES-APPLIED.md` 行 45）；③ `alerts.py`/`screener.py` 另 5 处字面量→常量；④ `sync/tasks/{id}` 不存在改 `40400`；⑤ 新增 AST 守卫（**含 7 条变异反证**）杜绝第 22 个裸码；⑥ 两端码表**逐值逐名对账**（补齐前端缺失的 `50001` 与 10 个回测码）；⑦ 前端未知码统一追加「（未登记错误码 N）」。14 条用例，全量 `1382 passed / 0 failed / 8 skipped` |
| S15 | `etf/detail` **字段互斥** | 顶层 `status:"degraded"` 与 `data_freshness.status:"fresh"` 并存（根因 `etf.py:89` 无条件 `setdefault("fresh")` vs `etf.py:744-748` 由 blocks 汇总） |
| S16 | `monitor/health` | `state:"degraded"` 与 **`ok:true` 并存** |
| S17 | `sync/tasks/{id}` 不存在 | 返回 **`51001`（本地无数据）而非 `40400`** |

> **S15–S17 是"有字段但值错"，比"没有字段"更危险**：前端与监控都会把它们当可信信号消费。

#### 4.13.2 ⚠️ 后端有、前端零消费（Top 10，按用户影响排序）

| # | 字段 | 实测/证据 |
|---|---|---|
| 1 | `market/overview.data_freshness.{status,reason}` | **实测当前生产态就是 `{"status":"degraded","source":"timeout","reason":"兼容概览六秒预算已用尽"}`**，且 `anomalies`/`money_flow`/`sentiment`/`ai_stats` 四块全 `unavailable`；而 `components/DataFreshness.tsx:16-25` 签名只有 `asOf/fromCache/stale`，**从不读 status/reason** |
| 2 | `market/overview.ai_stats.{status,reason}` | `KpiCards.tsx:75-80` 写死兜底文案 **"样本不足"** ⇒ **把超时/异常伪装成样本问题** |
| 3 | `stock/predict.close_basis` | 原文「latest_close 为后复权(hfq)收盘价…**非实际成交价**」，但 `latest_close=11253.97` **会直接显示**，`close_basis`/`feature_basis` **全前端 0 引用**（仅 `confidence_basis` 被渲染）⇒ 极易被误读为真实股价 |
| 4 | `datacenter/sync/status.failed_count` | 字段存在（`sync_service.py:118`），**全前端 0 引用**（`DataCenter/index.tsx:672` 只用 `completed_count`） |
| 5 | `alerts` payload 的 `truncated`/`recent_truncated`/`recent_limit`/`supplement_limit` | **全库唯一合格的截断披露模板**（实测 100+25 输入下四字段全部出现、基线时全不出现 ⇒ 条件披露语义正确），但 `rg "truncated\|failed_count" frontend/src` = **0**（本轮独立复现）；`pages/Alerts/index.tsx:169` 只渲染 `×${failed_jobs}` ⇒ 看到"失败 ×25"**不知还有 75 条被截** |
| 6 | `GET /alerts/health` 全 6 键（`degraded/reason/detail/snapshot_date/last_ok_at/checked_at`） | `api/alerts.ts` **无 `health()`**、调用点 = 0 ⇒ score_topk 预警**静默失效** |
| 7 | `market/overview.anomalies` | **全前端 0 渲染**（`types/stock.ts:453,509,524` 仅类型），而 `_build_anomalies` 自带「±9.8% 近似口径」note（`market.py:246`）**也无人读** |
| 8 | `ai_stats.label_price_basis`（`market.py:480`） | 仅 `types/stock.ts:468` 类型、0 渲染；**且只在 ok 路径返回 ⇒ 一降级该口径字段就整块消失**（**口径不应随数据可用性变化**——这是本条最危险之处） |
| 9 | `watchlist/dashboard.summary.flow_status="unavailable"` | 全前端 0 引用；`Watchlist/index.tsx:318-321` 只渲染 `'—'` ⇒ 用户**不知是"源不可用"还是"今天没数据"** |
| 10 | `stock/panels.north.{message,retired,reason}` | 实测 `retired:true` / `reason:"data_source_retired"`；`retired` **全前端 0 引用**，只显示通用"暂无数据" |

> **次席（同样 0 消费）**：`screener/stocks.basis_fields`/`quote_coverage`、`backtest` 系列 `universe_scope`/`friction_costs`/`rejected_trades`/`data_warnings`、`desk/capacity` 之外的 `liquidity`、`monitor.retrain.aborted_reason`、`mirror.orphan_dates` —— 全部**仅类型声明或无类型**。

#### 4.13.3 统一披露契约建议（P5 §8）

| 维度 | 建议（单一真源） |
|---|---|
| **字段命名** | 截断三件套 `truncated:bool` + `limit_applied:int` + `total_available:int`；多上限用 `truncations:[{scope,limit,total,truncated}]`；部分失败 `partial:bool` + `failed_count:int` + `failed_items:[{id,reason}]`；状态 `status` + `reason`（枚举 `data_stale`/`source_timeout`/`source_retired`/`market_data_partial`/`partial_scan`/`budget_exhausted`…）+ `message`；口径 `basis` + `basis_fields:{field:说明}`；时效 `freshness:{as_of,expected,lag_trading_days,is_stale,note}`。**硬约束**：任何 `LIMIT`/`[:N]`/`head(N)` **必带三件套**；任何被吞的 `except` **必带 `partial`+`failed_count`**；`total` **只允许**表示不随 limit 变化的真实总量（`datacenter/instruments` 必修）；"不推算、不填 0"这类说明**必须可见** |
| **三态映射** | `ok`（完整新鲜）/ `degraded`（有值但前提不成立，**必带** `reason`+`message`+`is_stale`）/ `unavailable`（无值，**必带真实 reason，禁止猜测文案如"样本不足"**）/ 建议新增 `partial`（`failed_count`+`failed_items`）。**禁止**同一功能多端点各自派生 `status`、**禁止** `ok` 独立于 `status` 计算、**禁止** `data_freshness.status` 与顶层 `status` 分叉 |
| **⚠️ status 取值域现状有三套（必须先归一）** | ① `ok\|degraded\|unavailable`（screener/panels/blocks）；② **`fresh\|degraded`**（`market.py:555,583,599-600`、`etf.py:89,256,393,461,771` 的 `data_freshness`）；③ `ok\|degraded` **仅二态**（`market.py:243-244`）。⇒ 归一方案：**`fresh` 作为 `ok` 的别名过渡**，新增 `partial`，其余按 ① 收敛 |
| **错误码映射** | `4002→40900`、`5000→50000/52000`、`4003→51001`、`5001→51000`；`40010~40017` **保留值但必须在 `errors.py` 与 `types/api.ts` 双向登记**；`sync/tasks/{id}` 改 `40400`。配套加测试断言「`fail(`/`AQPException(` 第一实参必须是 `core.errors` 常量」；前端 `apiErrorMessage` 对未知码统一追加「（未登记错误码 N）」 |

#### 4.13.4 P5 的 6 条结论与负结论

1. **静默截断重灾区是"裸 list 读端点"**（`alerts/events`、`notify/recent`、`desk/orders`、`stock/search`、`portfolio/search`、`datacenter/logs` 零披露）；其中 **`datacenter/instruments` 更糟**——`total` 是 `len(rows)`，**数字随 limit 一起缩小**（`limit=1→total=1`，真实 5552）⇒ **本轮前端「instruments total=50 当全量」的根因在后端**（F 系列的对应项应升级为「后端字段错」而非「前端误用」）。
2. **`ops/quality-scan` 最典型**：`sym_dirs[:200]` 对 2499 个分区必然生效却被 UI 当作"扫描范围 200 只"；且**单文件损坏让整次扫描回 `code=50000, data=null`**（已扫的 199 个分区结果**全丢**）⇒ **截断静默 + 部分失败全有或全无，双重缺陷**。
3. **`alerts.py` 的截断字段是全库唯一合格模板**，应升格为 §4.13.3 的命名标准并**强制前端补齐消费**。
4. **降级字段的产出端已相当好，断点在前端组件签名**（`DataFreshness` 只接 3 个 prop、`anomalies` 零渲染）。
5. **口径披露最危险的不是缺失，而是"降级时口径消失"**（`label_price_basis` 只在 ok 路径返回，而当前生产态正是 degraded）。
6. **错误码两端各缺同一批 15 个裸码**，并伴 3 处语义矛盾（`51001` 表"任务不存在"、`etf/detail` 与 `/screener`×`/screener/stocks` 三态互斥）。

**P5 的诚实边界（无法验证项，已记账）**：`/backtest/run`、`/backtest/strategy-run`、`/portfolio/backtest`、`/research/impact-sim` 的 `universe_scope`/`friction_costs`/`rejected_trades`/`data_warnings`/`liquidity` **实测值未取**（均为 POST 且部分写库，纪律禁止触发）⇒ 结论仅由源码 + 前端类型核对；`alerts/events` 的真实超限无法构造（生产 `alert_events` 当前 **0 行**）⇒ 改为"结构证明 + `%TEMP%` 直调 `_check_data_health` 证明模板字段确会出现"。

**P5 的附带发现**：`GET /market/index/kline` 无 RBAC ⇒ 已立为 **P1-50**（并经父审核员内省确认与基线裁决更正，见 §8.3b）。

---

## 5. P3 · 卫生、边界与死配置（118 条 · 摘要）

| 组 | 条目 |
|---|---|
| **API 参数/一致性（B7b）** | F10 `limit` 无界：`quality?limit=-1` 丢最后一条、`logs?limit=0` 返回全部、`instruments?limit=-1` = SQLite `LIMIT -1` **全表 2499+**（四处 `limit=-1/0/999999999` 均 code=0）；F11 读 `/alerts/events` 要 researcher 但写 `/alerts/events/read` 只要 viewer，且 `is_read` 是**全局列** ⇒ viewer 可清空他人未读预警（UI 不可达但直连可达）；F12 `ops.py:25` 声明「默认最新一年」但实现 `year = req.year or 2026` **硬编码**（今日巧合成立，2027+ 会扫陈旧年却自称最新）；F15 viewer 级 `/data` 页无条件内嵌要 researcher 的 `TrainPanel`/`TextDataPanel` ⇒ viewer 看到按钮、点了必 40300 |
| **ML（B4a）** | R4 KS 无判别力；R7 unknown 被 healthy 掩盖；R10 `infer_day` 无调用方且不写 model_version/feature_version/label_horizon 三列；R15 跨空洞样本计两次（`n_gap_invalid=n_non_finite=119`）；R16 `_MIN_HISTORY_ROWS=60` 低于文档 250 日（疑似）；R17 `LEGACY_FEATURE_VERSION` 死常量；R18 只校验列缺失不校验**列序**（lightgbm `validate_features` 默认 False，重排列序预测值不同）；R21 `monitor.py:718 label` 无害残留 |
| **回测（B5）** | B5-15 T+1 门整单拒绝而非部分成交（引擎不可达）；~~B5-16 不在 `uni_d` 的订单被静默丢弃、无 Trade 记录，与 `broker.py:17` 可观测性承诺矛盾~~ → **已修（2026-09-21）**：补 `reason="no_bar"`/`"no_cash"` 的 qty=0 记录，`/backtest/run` 的 `rejected_trades` 直接可见；~~B5-17 `strategy_base.py:365-367` 构造 `BrokerConfig(decay_bps=10)` 但该路径从不调 `apply_decay_cost` ⇒ decay 在策略回测**静默失效**~~ → **已修（2026-09-21）**：实测 ORIG 全程 **0.00 元**入账、FIXED 在 62 个换手日入账 **13,174.61 元**（见 §1 进展与 FIXES-APPLIED）；~~B5-18 过户费完全缺失（往返少计 ~0.2bp）~~ → **已随 B5-10 修复**（`effective_transfer_fee` 双边 0.01‰）；~~B5-20 逐笔 pnl 只减卖出费用（前端 win_rate/avg_pnl 偏乐观）~~ → **已修（2026-09-21）**：两处成本基准都补入买入费用，实测同一往返由「**+15.17 记盈利**（win_rate 100%）」变为「**−14.21 记亏损**（win_rate 0%）」（见 FIXES-APPLIED）；B5-15 T+1 门整单拒绝而非部分成交（引擎不可达） |
| **数据（B3b）** | C1 `signal_strength` 参考总体恒为 `head(200)` ⇒ 池≥200 时榜内 rank1–40 恒 strong（top_k=50 时 40/50 全「强信号」，从不出现 weak，与分数无关）；C2 join 丢弃 universe 现成的 `list_date/days_since_list/limit_up/limit_down` ⇒ 次新股可进前 40、涨停（不可买）无标记，核心榜单无同分次级键；C3 分片混源只记第一个非 degraded 来源，新浪行 `turnover=null` ⇒ 同列混「日内/日终」而 `basis_fields` 无 turnover 键；C4 `avg_score/max_score/up_ratio` 用 top_k 行算而 `pool_size` 是全池，无 scope 披露；C5 `STRATEGY` 恒 `alpha_basic_v1` 而生产特征空间是 v2g ⇒ 跨代产出无法区分；B5 `check_pct_limit` 永不触发（pct 从不落盘）且 `max_abs=0.45` 按小数写（akshare pct 是百分数，接线会把一切 >0.45% 判错）；B6 `is_quarantined` 无调用方且语义与 docstring 相反；B8 `fetch_daily_bar_batch(_by_year)` 无调用方，且「逐年抓取再拼接」正是 CRIT-002 禁止做法 |
| **领域（B2）** | 死代码 3 组（见 §6）、`research.py:173-182 style_exposure` 缺列返回 0.0（假装零暴露）且零调用方 |
| **基础设施（B1）** | 死代码/死配置（见 §6）、`kv.py` 时间口径、防抖锁双域 |
| **工程门禁（父审核员）** | CI 的 mypy 步骤**恒红**（10 errors / 2 files，全为良性误报：`etf_instruments.py:28-33` polars dtype 6 条 + `screener.py:286-291` 收窄误报 4 条）；nightly E2E 用 `\|\| echo` **非阻塞**；前端**零单测**（无 vitest/jest）；`backend/backups/` 无保留策略（117 文件 / 35.3MB / 3 周）且 `test_write_endpoints_smoke.py:529` **真的会调备份端点** ⇒ 每次跑测试都往仓库堆真实备份；`qa_browser/`（16 PNG / 5.52MB）与 `.workbuddy-ai/` **未被 .gitignore 覆盖**（误提交风险），已跟踪 PNG 32 个 / 4.65MB |
| **环境相关（非项目缺陷，记录在案）** | `setup_logging` 是 lifespan 中唯一无 try/except 兜底的步骤，且 `enqueue=True` 使日志可用性成为**启动硬依赖**（管道/权限/磁盘不可写 ⇒ 进程起不来 → 建议降级为 stderr-only） |

---

## 6. 死代码清单（按简报第 5 节三分类）

### 6.1 真死代码（可删；已核对无动态引用）

**后端 · 整模块/整套实现**
| 位置 | 依据 | 删除顺序建议 |
|---|---|---|
| `app/backtest/ma_cross.py` 旧引擎（`run_ma_cross`/`execute_target`）**半死** | 仅 `use_legacy_engine=true` 可达，前端从不下发（**已知 F-07**，`docs/audit/AQP_前后端逻辑审查_20260912.md:59`），零测试、零脚本调用 | 先摘 `StrategyBacktestRequest.use_legacy_engine`（`backtest.py:300`）→ legacy 分支（:375-390）→ liquidity note（:594-600）→ 最后删模块；并确认 `docs/audit/ab/*` 不再需要复现 |
| `app/domain/adjust.py`（整模块） | 生产 0 调用方，仅测试守护；且含 P1-11 缺陷 | 与 `domain/limit.py` 一并决策（见 §8.2 R2） |
| `app/domain/neutralize.py`（整模块） | 生产 0 调用方；含 P1-10 崩溃缺陷；`factor_processing.py:7` 文档声称使用但未接线 | 修 IndexError 后**接线**或删除（二选一，勿保留现状） |
| `app/ml/features_v2.py`（整模块，220 列实验） | 仅 `tests/test_p2_rest.py` 调用，版本未进白名单 | 涉产品决策（是否保留 v2g 实验线），暂不删 |
| `app/data/ingest/multi_source.py`（整模块） | 仅 tests 调用 | 若「三源冗余」仍是路线图 ⇒ 接线；否则删 |

**后端 · 函数/常量**
| 位置 | 依据 |
|---|---|
| `domain/a_share_rules.py:90-111`：`is_t_plus_one`/`settlement_days`/`stamp_duty_rate`/`commission_rate_default` | 全仓仅定义处；费率硬编码 8 份拷贝（`broker.py:100-101`、`ma_cross.py:55-56`、`portfolio.py:48-50`、`paper.py:32-33`、`strategy_base.py:307-308`、`db/models.py:124-125`、`app_settings.py:51`、`backtest.py:284`） |
| `data/ingest/dividends.py`：`fetch_dividends`/`save_dividends`/`load_dividends_asof` | 全仓仅定义处；save/load 存储介质都不一致 |
| `data/ingest/announcements.py:71,82` `save_announcements` | 读路径已接线、写路径断链 |
| `data/ingest/financials.py` 三函数 | PIT 财务永不落库 |
| `data/ingest/akshare_adapter.py:359,416-429` `fetch_daily_bar_batch(_by_year)` | 生产 sync 是单线程顺序循环；`_by_year` 违反 CRIT-002 |
| `data/ingest/validate.py:56-62` `check_pct_limit` | pct 从不落盘 |
| `data/repair.py:135-141` `is_quarantined` | 无调用方 + 语义反向 |
| `data/text_ingest.py:324` `attach_text_features` | 文本因子永不入模 |
| `ml/infer.py:96` `infer_day` | 无调用方且不写 3 个下游必需列 |
| `ml/purged_cv.py:94,145` `cv_rank_ic_report`、`ml/alpha_expr.py:286,289` `build_alpha158_lite`/`volatility_inverse_weights_from_close` | 全仓**仅测试**调用 ⇒ ML 侧实际没有 walk-forward/嵌套 CV、没有接上已实现的波动率倒数样本加权（B4b-10） |
| `scripts/p2_experiment.py:71-75` | 硬编码 `alpha_basic_v1`，与生产 `features.json`（含 20 个 `g1_*`）不兼容 ⇒ **不可运行**（B4b-7）；`scripts/p2_experiment.py:86` 还打印 P1-5 的反向单调率 |
| `ml/features.py:37` `LEGACY_FEATURE_VERSION` | 除自身白名单集合外无读取方 |
| `core/metrics.py:19,36,37,44,45,50,52` 7 个指标 | 定义后从未被写入：`HTTP_ERRORS_TOTAL`/`REDIS_STATUS`/`REDIS_CIRCUIT_OPEN`/`PIPELINE_TOTAL`/`PIPELINE_DURATION`/`MODEL_PREDICTION_TOTAL`/`MODEL_PREDICTION_ERROR` ⇒ `/metrics` **永远看不到 Redis 熔断** |
| `core/errors.py:91-93` `_AUTH_DETAIL_CODES` 的 `RATE_LIMITED`/`PIPELINE_BUSY` | 全仓无生产者（只有 4 个 detail 字面量被 raise） |
| `core/errors.py:96,101` `ERR_DATA_SOURCE=51000` / `ERR_EXPR_INVALID=53001` | **全后端无人抛出**（父审核员复核：全仓仅定义处）⇒ 外部源失败逃逸成 50000、非法表达式报 40000 |
| `core/pipeline_lock.py:54` `current_owner()`、`core/events.py:48` `publish()`、`db/models_auth.py:45,58` `UserSession`/`ApiToken`、`core/excel.py:20-21` `_sheet` dict 分支 | 零调用方；其中 `UserSession`/`ApiToken` 意味着**登出后旧 JWT 7 天内仍有效、API Token 认证未实现**（父审核员复核两表行数 = 0） |
| `db/models.py` 10 个 ORM 类只出现一次（`PriceAlert`/`BacktestRun`/`ModelRegistry`/`DataUpdateLog`/`NewsAnnouncement`/`UserSetting`/`ScreenerSnapshot`/`ScreenerSnapshotStats`/`UserSession`/`ApiToken`） | 表多经**裸 SQL** 访问（`screening.py:314,328`、`registry.py:194,290,295,367,371`、`app_settings.py:92`、`datacenter.py:638`）⇒ ORM 层是死抽象 |
| `parquet_store.py:347 path_for`、`:473-482 write_year_batch` | 零生产调用方（`write_year_batch` 仅 tests；旧路径的「整年覆盖抹日期」事故已记录在案）（B3a-14） |
| `parquet_store.py:485-495 write_whole_symbol` | 写 `all.snappy.parquet`，而读取路径（`read_symbol_dataset`/year）**只 glob `year=*.parquet`** ⇒ **写入即不可见**，却被 manifest 计为「有数据」——**语义陷阱**（B3a-14） |
| `cross_section.py:205 remove_mirror`、`repair.py:135 is_quarantined`、`panels.py:367,372,376 recent_trade_date_str`/`announcement_window`/`code_of` | 全仓零调用方；**隔离状态从未被任何读路径消费** ⇒ 单年隔离后 symbol 仍留在池中（B3a-15，与 B3b 的 `is_quarantined` 相同条目） |
| `backend/Dockerfile:56-57` 的 `/health/ready` HEALTHCHECK | 被 `docker-compose.yml:62` 的 `/health` **覆盖** ⇒ 死配置（DEP-10，且两者语义差异见 P1-43） |
| `.dockerignore`（不存在） | 缺该文件使构建上下文 ≈**7GB**（含 `.venv` 4.7GB、`data` 2.18GB、未跟踪 `.env` 与生产数据）（DEP-23） |
| F841 无害残留 5 处 | `ma_cross.py:104 day_idx`、`engine.py:512-513 rets`（第一版推导被 514 行覆盖）、`gp_miner.py:64 head`、`monitor.py:718 label`、`etf.py:436 last`（父审核员复核：份额用 `e["price"]` 有注释说明是有意的，故 `last` 确为无害残留）。**现状（2026-09-21）**：`ruff check app --select F841` 已由 5 → **3** —— `gp_miner.py:64 head`（第 1 轮删除）、`engine.py:512-513 rets`（P1-5 修复重写月度单调性判定时消失）、`backtest.py` 的 `source`（第 10 轮**接线**成 `price_basis` 披露，非删除）均已消；剩余 **3 条**：`ma_cross.py:105 day_idx`、`monitor.py:718 label`、`etf.py:436 last`（仍在 §6.1 死代码批次） |
| `core/stats_cache.py:17-27` docstring 与实现不符 | 称「并发下只算一次」，但 `fn()` 在**锁外**执行 ⇒ 实测 6 并发 = 6 次计算（B6-08 与 B7a-15 独立确认） |
| `services/task_store.py:67-69,80-93,109-149` 租约机制 | **整条恒不生效**：`update_task("running")` 不续租、`claim_task` 唯一点传入新 id（过期分支不可达）、`reap` 明确无条件不看 lease ⇒ **两套互斥策略并存**（B6-09） |
| `api/v1/app_settings.py:254-263` `POST /settings/apikeys/rotate` | **永远返回 fail(ERR_PARAMS)** 且前端零引用 ⇒ 伪功能（**已知 P1-5**，`docs/audit-2026-09-14/backend-architecture-review.md:250`） |
| `db/models.py` 的 `data_update_log` 表 | **全仓无 INSERT**（父审核员复核行数 = 0）⇒ 只读端点永远空 |

**死配置（`core/config.py`，B1 专项）**
| 档位 | 项 |
|---|---|
| 完全死（0 读取方） | `NOTIFY_EMAIL_TO`（:147，且 `app` 内**无任何 SMTP 实现**，通知渠道白名单只有 `{"sse","webhook"}`）、`API_HOST`（:50）、`API_PORT`（:51） |
| `.env` 写了无效（字段从不被读，只有 `os.environ` 生效） | `JWT_EXPIRE_SECONDS`（:132，真源 `auth.py:154` 的 `os.environ.get(...,"604800")`；实测 `.env=1234` ⇒ 仍签 604800）、`AQP_PANEL_BLOCK_TIMEOUT`（`.env.example:59` 文档化但**不是 Settings 字段**，真源 `stock.py:69`）、`FEATURE_INCREMENTAL`（`orchestrator.py:110`，未纳入 Settings）（三者均为**已知 A-06**） |
| 名字写错（`Settings` 无 `env_prefix`，`AQP_` 前缀被 `extra="ignore"` 静默丢弃） | `AQP_ALLOW_REGISTRATION`、`AQP_REGISTER_DEFAULT_ROLE`（实测设 `AQP_ALLOW_REGISTRATION=false` 后 `Settings.ALLOW_REGISTRATION` 仍为 True） |
| 文档幻觉（代码里根本没有） | `AQP_TALIB_BACKEND`、`AQP_REDIS_URL`、`AQP_SMTP_*` |

**前端**
| 位置 | 依据 |
|---|---|
| `components/charts/MarketHeatmap.tsx`（整文件） | 0 处 import；替代实现为 `BreadthPanel`+`MoneyFlowPanel` |
| `types/stock.ts:315 MarketHeat`、`:352 MarketMoneyFlow`、`:546 ApiResponseOf` | 被取代的死类型（另注：`MoneyFlowBlock` 在 :145 与 :430 **重复声明**，靠 declaration merging 合并 ⇒ 市场侧类型被错误收紧，见 §4.7 B8-07——**这不是死代码而是活性缺陷**） |
| `components/ui/index.tsx:76 DegradedBadge`、`:86 ScoreBadge`、`:141 PctCell`、`pages/MarketOverview/pieces.tsx:22 SkeletonPanel` | 未引用导出（B8 逐名 rg 计数 0） |
| `stores/useUiStore.ts:16,19,21 period/setPeriod/setRangeDays` | 无消费者（`KLineChart:268` 自带 local period）⇒ `rangeDays` 永远只能是持久化初值（B8-12） |
| `stores/useWatchlistStore.ts:36 removeGroup`、`stores/usePreferencesStore.ts:42 getRefreshIntervalMs`、`api/swr.ts:28-29 REFRESH.snapshot/static` | 全仓 0 引用（B8-13） |
| `api/export.ts backtest()` | 自述未接线 |
| `api/strategyBacktest.ts:74-81,98,120 StrategyLiquidity` + `mean_is_sharpe` | 定义无消费 ⇒ 成本披露与 IS/OOS 对照双双丢失（B9b D-1） |
| `types/p1.ts:158,167,170-177`（`rejected_trades`/`equity_curve`/`drawdown_curve`/`annual_returns`/`holdings`/`friction_costs`/`enable_friction` + 4 个 metrics 字段） | 全无渲染点（B9b D-2） |
| `api/alerts.ts:64 updateRule`、`api/datacenter.ts:38-44 task` | 0 调用方 ⇒ 预警规则**无编辑/启停入口**、`/sync/tasks/{id}`「刷新后恢复查看」整条链路未接线（B9c #16） |
| `types/api.ts:19-38` `ERR` 表 19 码中 **12 条零引用**（含 `DATA_SOURCE=51000`/`EXPR_INVALID=53001`——后端从不抛） | 不可达分支；且页面另有**裸数字**写法（`51001`/`40400`）⇒ 同一码两套表示；前端**缺 50001**（panic 码）（B8-09 + B9b D-3） |
| `Settings/index.tsx:163,603,605-607`「自动清理缓存（N 天前）」滑条 | 完全未接线：无 API 消费，后端 clear 端点也无保留期参数（B9c #10）——**伪功能** |
| `types/datacenter.ts:70-85 SyncStatus` | 缺 `failed_count`（`sync_service.py:118` 已提供）⇒ 失败标的数在面板无提示位；同文件 `SyncMode` 与后端不符（后端可为 `"fetch"`，`datacenter.py:954`）（B9c #15/#19） |

### 6.2 假阳性（**保留**，不删）

| 位置 | 为什么是假阳性 |
|---|---|
| FastAPI 路由函数（`api/v1/*` 全部） | 装饰器注册，不被显式调用；**但**必须补「角色不为 None」不变式（§8.3 R5） |
| Pydantic 模型字段、SQLAlchemy `Mapped[...]` 字段 | 序列化/建表需要 |
| `tests/**` 的 fixture 与 `test_*` | pytest 发现机制 |
| `main.py` 的 lifespan / panic_guard / errors 注册 | 框架回调 |
| `frontend` 按路由懒加载的页面组件 | `App.tsx` 动态引用 |
| `domain/indicators.py`、`domain/chip.py`、`data/panels.py` 等的 `__all__` 导出 | 公共 API 面 |

### 6.3 未接线（**比死代码更危险**：功能声称存在但无入口）

| 功能 | 代码位置 | 现状证据 | 建议动作 |
|---|---|---|---|
| 三源冗余行情 | `data/ingest/multi_source.py` | 仅 tests 引用 | 接线或删 |
| 公告采集 | `data/ingest/announcements.py:71` | 读路径已接线、写路径断链；`news_announcement` 行数 0 | **接线**（否则前端公告块是空壳） |
| PIT 财务表 | `data/ingest/financials.py` | 零调用方；个股财务走实时接口 | 接线或明确记为已知限制 |
| 文本因子入模 | `data/text_ingest.py:324` | `app/ml` 全目录零引用 | 接线或删 |
| 退市名单回填 | `data/ingest/tasks.py:60` | 唯一调用方是**手工脚本** `scripts/enrich_delist.py`（未进任何流水线/定时任务）；`instrument.delist_date` 实测 0/15 | **接线（P1-4 前置）** |
| 分红/送转 | `data/ingest/dividends.py` | 读写介质都不一致；`dividend_split` 行数 0 | 接线前先统一存储 |
| 自选股写入 | `db/models.py:77-92` | `watchlist` 表无生产写入方；预警 `scope="watchlist"` 恒空转 | 补写端点或下线该 scope |
| API Token / 会话撤销 | `db/models_auth.py:45,58` | 两表行数 0；登出后旧 JWT 7 天有效 | 明确为已知限制或实现 |
| `/settings/apikeys/rotate` | `app_settings.py:254-263` | 恒 fail + 前端零引用 | 下线端点 |
| `vec` 旧引擎 | `backtest/ma_cross.py` | 仅 API 开关可达 | 见 §6.1 |
| **8 个后端端点前端零调用** | `alerts.py:133`、`app_settings.py:255`、`market.py:606,805`、`alerts.py` PUT rules、`datacenter.py` sync/tasks/{id}、`datacenter.py` train/status | P4 全量对账（114 端点 × 111 调用点） | 逐条裁决：`/alerts/health` 为运维端点（保留）；`apikeys/rotate` 恒 fail（下线）；`train/status` 端点活但包装死（改前端）；其余补接线或下线 |
| **预警规则的编辑/启停** | `api/alerts.ts:64 updateRule` 0 调用方 | 后端 `PUT /alerts/rules/{id}` 存在（即"活端点"），前端无入口 ⇒ **规则只能建不能改**（B9c #16） | 补 UI 或明确记为限制 |
| **`data_jobs` 的回收与重试字段卫生** | `orchestrator.py:61-63,524-530` | 进程 RUNNING 时被杀 ⇒ 该行**永久 RUNNING**（回收只覆盖 background_tasks）；重跑只重置 4 个字段，`finished_at`/`duration_ms`/`current_step` **留上次值**（B6-06/07/14） | 见 §8.2 第 7 项 |

### 6.4 多实现并存（同一功能两套代码路径）

| 功能 | 活的 | 死的/半死 | 差异风险 |
|---|---|---|---|
| 涨跌停价计算 | `data/universe.py`（向量化，生产） | `domain/limit.py`（`build_universe_daily` 零生产调用方） | **实测 0.39% 的 limit_down 差 1 分**（P2 B2-8） |
| 行业中性化 | `domain/factor_processing.py`（部分接线） | `domain/neutralize.py`（零调用方 + IndexError） | 数学对但会崩（P1-10） |
| 回测引擎 | `backtest/engine.py` + `broker.py`（有闸门） | `backtest/ma_cross.py`（无闸门） | **已知台账**（两套回测实现） |
| 特征代际 | `ml/features.py` v2g | `ml/features_v2.py`（未接线） | **已知台账**（v1/v2 两代口径） |
| 夏普口径 | ~~`domain/risk.py`（日度 rf）~~ **✅ 已修（第 13 轮）**：统一为 `metrics.RISK_FREE_ANNUAL`（年化 2%，单一来源），`risk_metrics` 默认取它并披露 `rf_annual`；`portfolio._compute_metrics` 的形参改 `rf_annual` 且**真正生效** | ~~`portfolio._compute_metrics`（年化 rf）~~ | ~~同指标两个数（B2-16）~~ 两页口径已可**逐位对齐** |
| `feature_version` 解析 | `ml/features.py:33` 常量 | `monitor.py:138-156`（配置+mtime）/ 注册表 | **三套解析**，monitor 不披露版本（R8） |
| 费率/印花税/过户费 | 8 份硬编码拷贝 | `domain/a_share_rules.py`（死） | ETF 豁免 4 处免 / 1 处收（B5-10） |
| ETF 资金流 | `realtime._fetch_em_fflow`（正确） | ~~`data/etf.py:659-689`（口径错，偏差 +29.4%）~~ **✅ 已修（第 11 轮）**：改取 `f52`，并加"两处解读必须逐字段一致"的同构守卫用例 | P1-14 |
| 涨跌停「新股不设限」 | `domain/limit.py` | `data/universe.py` | 460 行主板误标（B2-9） |

### 6.5 永远不会成立的分支（恒真/恒假/被上游排除）

| 位置 | 为什么不成立 |
|---|---|
| `domain/a_share_rules.py` 的 `MARK_UP["bse"]` 与 bse 分支 | 真实 universe 516 万行 `board` 只有 main/chinext_star（bse 池恒 0，属需求） |
| `core/errors.py:91-93` `RATE_LIMITED`/`PIPELINE_BUSY` 映射 | 无生产者 |
| `core/metrics.py` 7 个指标 | 无写入方 |
| `data/quality.py:436-459` 的 `factor_jump`/`calendar` 分支 | `validate_partition` 不调用/不传参 ⇒ 永不触发（P1-19） |
| `data/ingest/validate.py:56-62` `check_pct_limit` | `pct` 列从不落盘 |
| `domain/portfolio.py:209-228` 退市/停牌缺口分支 | 索引恒为 union_idx ⇒ `last_valid_index` 恒首/末（P1-9） |
| `account_summary`/`place_order` 的空头保护 | 无持仓校验 ⇒ 裸卖分支恒不触发（P1-3） |
| `governance`：`screener.py` 的 `available==0 → unavailable` | 被 `total==0 → ok` 覆盖（P0-6 裁决点） |
| `ml/purged_cv.py:57-75,119` 的 `min_gap_ok` | 阈值传的就是切分器内部**同一个** purge+embargo ⇒ **恒真**；`purge=0/embargo=0`（gap=0、标签重叠）仍报 True（B4b-8） |
| `ml/train_lgbm.py` 的 `min_child_samples` 网格轴 | 被 `min_data_in_leaf` 别名覆盖（canonical 恒胜）⇒ 81 组中 54 组**永不生效**（B4b-4） |
| `api/v1/market.py:480` 的 `"raw_fallback"` 分支 | `:435-437` 已提前 return unavailable ⇒ else 不可达（B7a-09） |
| 前端 `ERR.DATA_SOURCE`/`ERR.EXPR_INVALID` 分支 | 后端从不抛这两个码 |

### 6.6 删除顺序与风险（分三阶段，每阶段可独立提交与回滚）

**阶段 1 · 零风险机械清理**（建议一次提交，前置：`pytest -q` 全绿 + `npm run build` 通过）
1. **3 个 F841 残留**（`ma_cross.py:105`、`monitor.py:718`、`etf.py:436`；基线 5 条中 `gp_miner.py:64` 已删、
   `engine.py:512-513` 随 P1-5 重写消失、`backtest.py` 的 `source` 已接线）与 `debug_helpers` 无引用导出。
2. `core/metrics.py` 的 7 个无写入方指标 + `/metrics` 文案同步（**先确认 Grafana/Prometheus 抓取配置里没有引用这些名字**）。
3. `core/errors.py` 的 `_AUTH_DETAIL_CODES` 中 `RATE_LIMITED`/`PIPELINE_BUSY` 映射（**先与 §8.1 R9 的错误码治理一起做**，否则会删掉将来要用的名字）。
4. 前端 0 引用导出：`MarketHeatmap.tsx`、`ScoreBadge`/`PctCell`/`DegradedBadge`/`SkeletonPanel`、`MarketHeat`/`MarketMoneyFlow`/`ApiResponseOf`、`useUiStore.period/setPeriod/setRangeDays`、`removeGroup`、`getRefreshIntervalMs`、`REFRESH.snapshot/static`、`api/export.ts backtest()`。
   - **风险**：`DegradedBadge` 很可能是"降级披露"要做 UI 的现成组件（§8.1 R10）⇒ **建议保留 `DegradedBadge` 并在 P5 披露矩阵落地时接线**，其余删除。
5. 死配置：`NOTIFY_EMAIL_TO`、`API_HOST`、`API_PORT`（**先确认部署文档/脚本没有依赖这两个名字**）。
6. `.env.example` 中 3 个"文档幻觉"项（`AQP_TALIB_BACKEND`/`AQP_REDIS_URL`/`AQP_SMTP_*`）与 2 个拼错项（`AQP_ALLOW_REGISTRATION`/`AQP_REGISTER_DEFAULT_ROLE`）——拼错项要**改名为正确形式并补齐 env_prefix 语义**，不是直接删。

**阶段 2 · 需先接线或先裁决**（每项单独提交）
7. `data/ingest/multi_source.py`、`data/text_ingest.py:324 attach_text_features`：**先问产品**（§10 第 5/6 项），决定接线还是删除；若删除，同时清 `tests/` 中对应用例。
8. `domain/adjust.py`、`domain/neutralize.py`：**先决定"是否要做复权/中性化"**。若做 ⇒ 修 IndexError（`neutralize.py:63` 标签索引）后接线，并把 `factor_processing` 与它合并为一份实现；若不做 ⇒ 删模块 + 删测试。
9. `domain/a_share_rules.py` 的 4 个函数：**先建 `domain/trading_rules.py` 单一费率来源**（§8.2 第 18 项），把 8 处硬编码收敛过来，**最后**才删这 4 个函数；顺序颠倒会造成费率口径短暂分叉。
10. `db/models.py` 的 10 个只出现一次的 ORM 类：**先把裸 SQL 调用点改为 ORM 或反之**（统一一层），再删另一层。**风险最高的一项**——`screening.py:314,328`、`registry.py:194,290,295,367,371`、`app_settings.py:92`、`datacenter.py:638` 都是裸 SQL，直接删 ORM 类会让 `create_all` 少建表（**改用 Alembic 迁移或保留模型仅作 schema 源**）。

**阶段 3 · 需产品决策（不建议本轮动）**
11. `backtest/ma_cross.py` 旧引擎 + `use_legacy_engine`：先摘掉 API 字段（否则第三方脚本可命中缓存键碰撞 P1-2），保留模块一个版本周期；确认 `docs/audit/ab/*` 无需复现后再删。
12. `ml/features_v2.py`（220 列实验线）、`ml/infer.py:96 infer_day`：涉及模型路线，等 P2 策略批次结论。
13. 前端 `types/api.ts` 的 12 条零引用码：**不要单独删**——必须先做 §8.1 R9 的码表治理（构建期从 `errors.py` 生成），否则删完又会出现同样问题。
14. `/settings/apikeys/rotate` 端点：**下线**（恒 fail + 前端零引用），但需同步删 `docs/` 中的接口文档与 `openapi.json` 快照。

**删除前的统一验证命令**（每阶段都跑）：
```
# 后端
cd backend && .venv\Scripts\python.exe -m pytest -q -p audit_mkdtemp_fix   # 期望 1065 passed / 0 failed（P1-32 修完后）
ruff check app --select F                                                 # 期望 0
# 前端
cd frontend && npm run build                                              # 含 tsc --noEmit + noUnusedLocals
```
**风险总述**：本清单里唯一可能造成生产事故的是第 10 项（ORM/裸 SQL 双轨）与第 9 项（费率单一来源顺序），其余均为「删了才发现有人动态引用」类风险，可用一次全量测试 + 一次构建覆盖。

---

## 7. 策略优化建议（按预期收益排序 · 五要素）

> 每条给：**当前问题 / 具体改法 / 预期收益量级 / 引入风险 / 验证方式**。量级为**基于本项目已实测口径的推算**，不是对真实收益的承诺；采纳前必须样本外验证（简报第 9 节第 4 条）。

### S1 成本与摩擦口径归正（**P2 批次实测收敛**：Top-50 年化高估 8–20pp、Top-10 14–34pp）
- **当前问题**：默认回测**无摩擦**（~~`decay_bps` 在策略路径静默失效 B5-17~~ **已修 2026-09-21**），而摩擦开启后 `last_day_turnover` 被非交易日反复扣（P1-1 **已修**）、换手双边累加导致成本 **2×**（~~B5-08~~ **该定性已撤回，见 §9.3 R-7**）。
- **✅ 截至 2026-09-21 的修复状态（S1 的 T2 清单）**：`decay_bps` 策略路径已补齐（B5-17）；`last_day_turnover` 改**当日单边**（P1-1 + B5-08 区域）；建 `domain/a_share_rules.py` 统一费率并补**过户费 0.01‰**（B5-10/B5-18）、**印花税历史分段**（2023-08-28 前 1‰）、**ETF 全链路豁免**（含买入端）；涨跌停取整修正（B2-8）。**未做**：默认 `enable_friction=True` 的取值决策（涉及产品口径，见 §8.2 队列）、`engine.py` 启发式 decay 是否改为按成交额计（S1-T2 提到的另一条路线）。
- **⚠️ 新增口径披露（2026-09-21 二次复核）**：`last_day_turnover` 采用**买卖均值**口径 `(B+S)/2/E`，与「现金算持仓的 Σ|Δw|/2」（恒等于 `max(B,S)/E`）**仅在现金平衡日相等**；建仓/清仓日前者是后者的一半。选前者的理由是它与**实际成交名义额**成正比（全清仓名义额只有全仓换标的一半）。**对本节结论无影响**：稳态调仓日 B≈S ⇒ 两口径恒等，`134.7×/年` 与 `81.0×/年` 不变；差异仅限每次回测最多 1 个建仓日 + 1 个清仓日。
- **实测（P2 批次，真实信号非合成，与 B5 互证）**：真实换手 **Top-10 单边 53.46%/日 = 134.7×/年、Top-50 32.15%/日 = 81.0×/年**；单边费率 = 佣金 2.5bp + 过户费 0.1bp + 印花税 2.5bp + 滑点 s。默认无摩擦口径对 **Top-50 年化高估 8.2% / 12.2% / 20.3%**（s = 5 / 10 / 20bp）、**Top-10 13.6% / 20.3% / 33.8%**。B5-C1 的 29.7pp 是**高换手(236×)场景的上界**（29.7×81/236 ≈ 10.2pp，与上区间吻合）。
- **关键策略推论（本报告最重要的单一结论）**：实测 **Top-50 毛超额仅 +0.10%/5d（≈ +5%/年）< 成本下限 8.2%/年** ⇒ **日频调仓在真实成本下净值为负**。这不是"参数没调好"，而是**调仓频率与成本量级的结构性错配**。
- **具体改法（T2）**：`backtest.py:36` 默认 `enable_friction=True` + 滑点 5bp + sqrt 冲击 + `max_participation=0.05`；`broker.py:380-397` 换手改**单边**；`engine.py:369-371` **删掉启发式 decay**、改按当日成交额计；建 `domain/trading_rules.py` 统一费率（补**过户费**、**印花税历史分段** 2023-08-28 前 10bp、**ETF 全链路豁免**）。
- **预期收益量级**：**不是提升收益，而是消除 8–34pp/年 的虚高**；并对高换手策略给出"扣费后为负"的真实结论。
- **引入风险**：所有历史回测数字会变 ⇒ 必须重跑基线；默认变保守后前端需标注口径（P1-11/I-11 的披露缺口）。
- **验证方式**：`scripts/ab_backtest.py` 带/不带 `--enable-friction` 双跑；红用例「仅 1 天成交」断言 decay = 当日成交额 × bps；对 2023-08 前后各取一段验证印花税分段。
- **顺带（P2 的 S1 裁定）**：S1 采纳，量级按实测收敛（原稿「+15~30pp」改为 **8–34pp**，并明确这是"口径纠正"而非收益提升）。

### S2 退市与 PIT 一致性（预期收益量级：**消除 1~3%/年的收益虚高**，文献经验值，需补数据后重测）
- **当前问题**：`delist_date` 全 NULL（实测）、~~退市过滤/强平/haircut 三条路径空转~~ **⚠️ 2026-09-21 实测更正**，而 API note 宣称已处理（P1-4）；`list_date` 97.8% NULL 导致「上市前占位行」与「新股涨跌幅窗口」双失效（B5-14）；universe 属性非 PIT（**已知台账**）。
- **⚠️ 路径可达性的实测更正（本轮只读复核）**（命令见 FIXES-APPLIED「P1-4」节）：
  | 路径 | 驱动条件 | 当前数据下 |
  |---|---|---|
  | ① 建库期「按 `delist_date` 剔除」 | `instrument.delist_date` | **空转**（非空 **0/5552**） |
  | ② 面板是否携带该列 | `universe_daily[_bt]` schema | **两套面板都没有 `delist_date` 列** ⇒ ①在**结构上**不可能发生 |
  | ③ 持仓强平 + haircut | **面板缺席**（连续 N 日不在 `uni_d`） | **生效**，与 `delist_date` **无关**（`tests/test_delist_path_disclosure.py` 引擎级用例实测通过） |
  ⇒ 「三条路径全部空转」**不准确，实为 1 条空转**；**真实缺陷是「披露不实」（note 声称做了结构上做不到的事）+「折价损失不可见」**，两者**本轮已修**（`delist_coverage` 数据驱动披露 + `friction_costs["delist_loss"]`）。
- **具体改法**：把 `scripts/enrich_delist.py` 接进晚间例行 + `step_validate` 前置（现在它只是手工脚本）→ 回填 `delist_date` 与 `list_date` → 用**历史 in-market** 重建 `universe_daily` → 引擎对退出宇宙持仓在 `delist_grace_days` 后按 `last_hfq_close × haircut` 强平并计入 `friction_costs["delist_loss"]`（**最后一项本轮已落地**）。
  - **⚠️ 未完成部分的原因**：回填需要外部退市名单数据源，而本沙箱**无外网**；且把联网步骤接进晚间例行会让流水线在此环境下必然失败 ⇒ **接线属部署决策**，本轮只落地了可离线验证的披露与账务两半。
- **预期收益量级**：早期窗口（2022-2024）等权全市场类策略文献经验 **1–3%/年** 的收益虚高被消除；对 Top-10 集中组合影响较小但**尾部风险数字（MDD）会恶化**（更真实）。
- **引入风险**：数据源字段不稳定（akshare 退市接口）；重建 universe 会改变所有历史回测基线 ⇒ 必须走「基线→修复→A/B 对比→差异归因」闭环。
- **验证方式**：`instrument.delist_date` 非空率 > 0；构造「第 10 日退市」用例断言网格剔除 + 减记入账；A/B 差异 = 各退市事件减记额之和（`docs/audit/ab/task16-final-AB.md` 已有模板）。

### S3 模型门禁与回滚闭环（预期收益量级：**避免"冻结在坏模型上"**，无法量化但影响所有下游）
- **当前问题**：`rank_ic_tolerance=0` ⇒ 回滚恒被拒（P1-6）且放行后踩 `SameFileError`（P1-7）；门禁不比 `feature_version/dataset_version`（R14）；自动重训候选因 4e-5 之差恒被拒（P1-16）⇒ 生产模型自 09-05 冻结。
- **具体改法**：① 回滚走**独立通道**（`demote/promote` 分离，回滚不计入 IC 单调门禁）；② `src.resolve()==dst.resolve()` 短路；③ 门禁强制同 `feature_version`+同验证区间才可比，跨版本改走"影子运行 N 日"；④ 容忍度改为按验证段样本量归一（如 `max(0.002, 2*SE(rank_ic))`）。
- **预期收益量级**：恢复「坏模型可回滚」与「漂移可自愈」两个闭环；重训 churn 从「纯浪费 ~38s×12h/次」变为有效。
- **引入风险**：容忍度放宽会放进略差的模型 ⇒ 需配合影子期与人工确认。
- **验证方式**：新增「回滚必须成功」红用例 + 「同版本 promote 不抛 SameFileError」用例 + 「跨 feature_version 门禁必须拒绝」用例。

### S4 监控触发器口径（预期收益量级：消除 ~15 天/次 的假 degraded 与无效重训）

> **✅ 已修（2026-09-22，第 12 轮）——四条全部落地，并如实记录两条"未照字面实现"与两条实测局限**
>
> | 改法 | 落地方式 | 真实面板实测 |
> |---|---|---|
> | ① PSI 口径 | 判定口径改为**按交易日截面标准化**（去当日中位 / 除当日 MAD×1.4826），池化原始 PSI 降为 `psi.raw` **披露字段** | raw `mean 0.2646 / max 3.3051 / 21-85 超 0.25` → 判定 `mean 0.0415 / **max 0.2024** / **0-85 超 0.25**`（`drift_state: degraded → watch`） |
> | ② KS | 与 PSI **同窗口同口径** + 新增 `over_crit_ratio`（强度）与 `crit_effective`（单日截面尺度），`note` 明示池化 n 的过度功效 | `mean_D 0.1161 → 0.0387`；超限比 85/85 → **82/85（96%）** —— 仍近全命中，**已如实标注"只能当强度读"** |
> | ③ σ 池宽折算 | `σ_adj = σ_raw·sqrt(n_hist_median/n_recent_median)`；池宽不可得则不折算并 `sigma_basis` 标明 | 快照 `std_ic 0.1151`（池宽 120）→ **0.0253**（当前 2490）：旧阈值 `−0.1262` 令 **IC 归零仍判 healthy**（假阴性），新阈值 `0.0085` 正确判 degraded；实测近期 IC 0.0774 仍 healthy（不误报） |
> | ④ `_worst` | `healthy < unknown < watch < degraded` | `_worst(['unknown','healthy'])`：`healthy` → **`unknown`**，虚假"恢复健康"推送消除 |
>
> **未照字面实现（附实测理由）**：
> - 报告 ① 的字面写法"PSI 改比**截面分位/秩分布**"经实测**退化**：`mean 0.0011 / max 0.0131 / 0-85 超限`
>   —— 秩在每个截面内构造上就是均匀分布 ⇒ 所有因子 PSI≈0、判别力归零（等于把监控改成空转）。
>   故取同一思路的**非退化**版本（去中位 + MAD 归一，保留形状信息）。
> - "或用 `n_over_crit/n_factors` 作为强度而非全命中"已实现为 `over_crit_ratio`，但**没有**把它降级成唯一指标。
>
> **实测局限（写进代码与测试，防止后人误以为它成了漂移预言机）**：
> 截面标准化口径对①纯水平/尺度平移、②截面内**单调**变换、③**跨日打乱**（池化边际不变）**均不可见**；
> 且敏感度**依赖因子、对污染比例非单调**（`ma_gap_250` 注入 30%/50% → 0.3540/0.4166 **报警**，
> 70% → 0.2271 **不报警**；`g1_ma_gap_250` 注入 50% → 0.2024 无响应）。
> 这些盲区由 **IC 通道并联兜底**（本项已同时修复其池宽假阴性），不是"PSI 能测一切"。
>
> **顺带修正的性能回归**：截面标准化若按"每因子一次 `groupby.transform`"实现，真实面板上
> 单次 PSI 要数分钟（首轮全量套件因此从 6 分钟涨到 23 分钟）。已改为**整表 2 次 groupby**
> 并让 PSI/KS **共享**切窗与标准化帧：`prepare 5.2s + psi 6.5s + ks 5.0s`，且数值与优化前**逐位一致**
> （`3.3051 / 0.2024 / 0.9647`）。
>
> **验证**：`backend/tests/test_monitor_trigger_caliber.py`（17 条，含**真实面板**验收：复现快照
> `raw.max=3.3051`、判定口径 `0/85` 超标、注入报警与非单调局限固化、前端披露锁；`test_monitor.py`
> 新增 1 条并改写 3 条旧契约断言 + 1 条**集成锁/变异反证**）；全量 `1411 → 1429 / 0 failed`。

- **当前问题**：PSI 对水平型因子随趋势必然超标（实测 PSI 3.3051）、KS 临界值无判别力（85/85）、1.5σ 用 119 只时代的 `hist_std`（P1-17/R5）、`_worst(['unknown','healthy'])=='healthy'` 推虚假恢复（P1-21）。
- **具体改法**：① PSI 对**水平型**因子改比**截面分位/秩分布**（或先做去趋势）；② KS 临界值按当前池宽重算（或用 `n_over_crit/n_factors` 作为强度而非全命中）；③ σ 阈值改为**当日池宽下重估的滚动 std**；④ `_worst` 把 `unknown` 视为 unknown 而非 healthy。
- **预期收益量级**：drift 状态从「长期 degraded」恢复正常敏感度；自动重训频率从 12h 降到事件驱动。
- **引入风险**：阈值放宽可能漏掉真实漂移 ⇒ 需保留「人工复核队列」。
- **验证方式**：用真实历史面板重算 PSI/KS 并检查在**趋势反转但分布未变**的月份不再报警；注入真实漂移（打乱标签）必须报警。

### S5 ML 标签与目标函数（**本轮已取得实测增量**：同一数据 RankIC 5.8×）
- **当前问题**：标签为未来 N 日**绝对收益**，且 `xsec_demean` 默认 `False`（只有 `retrain.py` 打开）⇒ 训练目标与「截面选股排序」用途错配；`xsec_demean` 未落盘（R13）导致门禁跨口径比较（P1-39）；特征筛选用**池化** Spearman IC 而目标是**逐日截面** RankIC（P1-38）；purge 硬编码 5 不与 `ML_LABEL_HORIZON` 联动（B4b-9/R20）。
- **具体改法（B4b §5 五条，均已给样本外验证方式）**：① 标签截面 zscore 化 + 统一筛选口径（`train_lgbm.py:224`、`:154-175`、`scripts/train.py:44`）；② 复用回测侧已实现的 `walk_forward_search` 做**嵌套选参**，替换池化 IC 选优第一键（`grid_search.py:60-64`）；③ 目标函数改秩相关/lambdarank 替代 L2；④ 多 seed rank 集成 + **接线已实现却未接线的** `volatility_inverse_weights_from_close`（`alpha_expr.py:286`）；⑤ GP 加留出段 + `n_evaluated` 显著性门槛。
- **预期收益量级**：**①③ 有实测支撑**——真实面板同一数据，`xsec_demean=True` 使 `valid_rank_ic` 从 **0.0144 → 0.0832（5.8×）**、`best_iteration` 从 **1** 回到正常；而池化 IC 与逐日截面 IC 的偏差在 `atr_14` 上达 **270×**（0.0003 vs 0.0816），即当前筛选正在剔除最强的一批截面因子。**②④⑤ 属方向性收益，需 A/B**。
- **引入风险**：改默认值会使**所有历史模型产物不可比**（须同时落盘 `xsec_demean` 并标注血缘，见 P1-39）；排序目标会改变 `score` 语义（前端展示与阈值都要复核）。
- **验证方式**：①同一特征集/同一验证段比较 `valid_rank_ic` 与扣费后净值（B4b 已给真实面板探针）；②为「自反式因子净值应为 0」（P0-7）与「门禁必须拒绝跨 `xsec_demean` 对比」各加一条红用例；③门禁 shadow 模式跑 20 个交易日。

### S6 组合构建与风险模型（**P2 批次纠错并扩面**：零约束 + 容量下限 + 协方差条件于选股）

> ✅ **第 13 轮已修其中的 P1-13 / B2-11 / B2-12 / B2-16（本报告 §8.2 第 15 项）**。落地方式与
> 本节原「具体改法」的偏离，如实记录于此：
> ① **`apply_weight_cap` 行为未改** —— 只在 `n·cap<1` 时由新增纯判据 `cap_feasibility` /
>    `cap_info_for` 给出 `feasible=false` + `max_invested_ratio` + `min_feasible_cap`，并沿
>    `BacktestResult.weight_cap_info` / `/backtest/run` / `/research/optimize` / 前端告警
>    全链路披露。**理由**：改默认权重会平移全部历史净值（本节「引入风险」自己就要求重跑基线），
>    而本轮的缺口是"用户误读"，披露即已消除。
> ② **新发现（原文未点明）**：`weighting="equal"`（**引擎默认值**）分支**从不调用**
>    `apply_weight_cap` ⇒ 用户传入的 `weight_cap` 被静默忽略（实测 top_k=4、cap=0.2 时
>    实际 `max w = 0.25 > 0.2`、`Σw = 1.0`）。故披露必须区分两种情形：**"不可行且真执行了
>    （半仓）"** 与 **"压根没执行（超限但满仓）"**；若强行让 `equal` 执行上限，`n·cap<1` 时
>    会退化成半仓，**比超限满仓更糟** ⇒ 有意不修行为，只披露并给出 `min_feasible_cap`。
> ③ **§S6 内部口径矛盾（如实标注）**：本节「引入风险」写"**把接 μ 进 MVO 后移**"，但
>    「验证方式」要求"断言 λ=8 vs 50 权重必须显著不同"——后者**只能靠接线 μ 达成**。
>    本轮按前者执行（不接线 μ），并把该验证改写为**可判定的等价断言**：μ≡0 时 λ 完全惰性
>    （L1 距离实测 **0.0**，已固化为用例）＋ μ 异质时 λ 立刻生效（L1 = **0.4593**，证明缺陷
>    在调用方未给 μ、不在优化器）＋ `mvo` 响应带 `expected_returns.basis="unavailable"`。
>    μ 的样本外收缩估计与 §S6 其余各项（B2-14 归因、P1-49 零约束、容量下限、协方差条件于选股）
>    **仍未修**，留在队列。
> ④ **B2-16 的实际影响面比原文更大**：原文只提「标签硬编码」，实测同一 `risk_metrics` 的
>    `rf` 默认 **0.0**、而组合页用年化 **2%** ⇒ 两个页面的"夏普"不可比；且
>    `portfolio._compute_metrics` 的形参名 `daily_rf` **从未被使用**（函数体直读模块常量）
>    ⇒ 形参与实现分裂。均已修（rf 单源 `metrics.RISK_FREE_ANNUAL`，年化口径，响应披露
>    `rf_annual`；组合页数字**逐位不变**，个股页 Sharpe 按 2% 重算）。

- **当前问题**：`weight_cap` 不可行时静默半仓（P1-13）；MVO 从未收到 `expected_returns` ⇒ λ 无效、实为等权+裁上限（B2-11）；Σw∈[0.99,1.01] 不归一（B2-12）；归因把缺口与被丢弃持仓塞进 `residual_alpha` 却称「纯 Alpha」（B2-14）。
- **P2 的纠错（本报告 §7 初稿有误，已更正）**：**归因路径的基准是可选且正确的**（`api/v1/desk.py:417-429` 三选一、Σwb=1）；硬编码「沪深300」**只发生在 `domain/risk.py:145` 个股风险卡的标签上** ⇒ 从 S6 主体**降级为 P3 标签问题**（`risk_metrics` 增 `benchmark_symbol` 即可）。
- **P2 的新增面（原 S6 未提）**：
  ① **组合层零约束**（P1-49）：`/portfolio/backtest` 根本没有 `weight_cap`/行业/换手字段，`weighting` 连 `mvo` 都不可选；
  ② **容量只有上限、没有下限**：`strategy_capacity` 只算 ADV 上限且 `adv_ratio_in_pool` 从不使用；而 A 股最低 5 元佣金给出**容量下限**——Top-50 在 AUM < ≈**311 万元** 时佣金费率超 2.5bp（A=100 万时约 7.8bp/笔、全年多 ~6.3% 成本）；
  ③ **协方差"条件于选股"**：风险模型在**已被选中的组合**上估协方差 ⇒ 系统性低估风险（一个原报告未提出的独立缺口）。
- **具体改法**：`portfolio.py:33-44` 增 `weight_cap`（默认 `max(0.10, 1.2/N)`）/`industry_cap(0.30)`/`max_turnover_per_rebalance(0.50)` 并透传优化器；`apply_weight_cap` 增 `feasible` 标志（P1-13）；`attribution.py:27-37` 强制 wp/wb 归一 + `residual` 更名 `unexplained` + `dropped_symbols` 兜住；`risk_metrics` 增 `benchmark_symbol`；容量补**最低佣金下限**。
- **预期收益量级**：暴露/回撤数字可信；容量区间可执行（**Top-50 最小 AUM ≈ 311 万元**）——**但不改收益**。
- **引入风险**：默认 `weight_cap` 改变历史净值须重跑基线；**μ 仍为 0 时接线 `mvo` 只是"改名的等权"** ⇒ P2 建议**把"接 μ 进 MVO"后移**（实测 Top-10 无正超额，先接 μ 只会放大噪声）。
- **验证方式**：断言 `N*cap<1` 时 `feasible=false`；λ=8 vs 50 权重必须显著不同（当前 L1 距离实测 = **0**）；断言 Σwb=0.8 时 `|unexplained| < 1e-9`；合成 μ=0.05%/日 断言 h=20 时 `ls_annualized ≈ 12.6%`（而非 252%）。

### S7 选股展示层口径（预期收益量级：消除"40/50 强信号"这类失真）
- **当前问题**：`signal_strength` 参考总体恒为 `head(200)` ⇒ top_k=50 时 40 个恒 strong（C1）；榜单丢掉 universe 现成的次新/涨跌停标记（C2）；混源 turnover 同列混日内/日终且无 basis（C3）；`avg_score` 用 top_k 行算而 `pool_size` 是全池（C4）。
- **具体改法**：① `signal_strength` 分位改按**全池**或显式披露「榜内分位」；② join 保留 `list_date/days_since_list/limit_up/limit_down` 并在前端标注「不可买/次新」；③ turnover 加 `basis_fields` 键；④ stats 增 `scope: "top_k"|"pool"` 披露。
- **预期收益量级**：不改收益，改**可信度**（避免把展示口径当信号强度）。
- **引入风险**：字段新增，前端需同步（类型文件已 strict）。
- **验证方式**：构造 top_k=50 的池断言 strong 数量随分数变化；对混源响应断言 `basis_fields.turnover` 存在。

### S8 存储与计算效率（预期收益量级：扫描量/内存下降，非收益）
- **当前问题**：`quality-scan` 只扫 8%（F4）；`param_search` 可 OOM（P0-5）；`/instruments?limit=-1` 全表（F10）；全量读 1.1GB 面板（**已知 P0-1**）；`data/models/prod` 与 `exp` 双份模型（**已知 P2-3**）。
- **具体改法**：扫描改流式分批 + `truncated/total_symbols` 披露；`limit` 全部加上限（默认 500、最大 5000）；面板按版本+按年裁剪；接 `scripts/purge_legacy_models.py` 定时清理。
- **验证方式**：`/instruments?limit=-1` 必须 40000；扫描 2499 只耗时与内存实测；面板读取行数下降幅度。

### S9 策略专项裁定与新增建议（P2 批次 T1–T8；**对 S1–S8 的逐条裁定**）

**对 §7 既有建议的裁定**：
| 原建议 | 裁定 | 说明 |
|---|---|---|
| S1 成本与摩擦 | **采纳 + 量级收敛** | 高估区间从「+15~30pp」改为实测 **8–34pp**；新增"毛超额 < 成本下限 ⇒ 日频调仓扣费后为负" |
| S2 退市与 PIT | **退市维持最高优先级；新股窗口下调** | 实测新股窗口的可达面只有 **0.04%** 且**零触发** ⇒ 优先级下调；退市（`delist_date` 全 NULL）仍是所有历史业绩偏差的主源 |
| S3 模型门禁与回滚 | **纠错为「判据比较对象错」** | 不只是 `tolerance=0`：`valid_rank_ic=0.1020` 来自**单一 regime**（2024-07~2025-08），42 棵树 train 0.156 / valid 0.102 / test 0.0705 的**单调衰减说明关系非平稳** ⇒ 加容量治不了，必须改**多窗口滚动验证**（见 T8） |
| S4 监控触发器 | **新增前置：先修预测落盘覆盖率**（但**范围已收窄**） | **父审核员只读复核（262 分区全量）**：**141 个分区是完全相同的 120 只**、共仅 **19 个不同标的集合**（多数 117~121 只）、**只有 5 个分区是全截面**、全体并集 2498。**关键细化**：这 141 个属**更早的 `lgbm_v1_20260830_095940_repaired`（252/262 分区）**，而**当前生产模型的分区已全是全截面** ⇒ 缺的是**监控基线窗口的历史代表性**（`mean_ic_by_horizon` 建立在约 120 只幸存者样本上），**不是当前模型的行为缺陷**。修法优先级因此调整为：**先按 model_version 分段重建基线**（而非先改落盘） |
| S5 ML 标签与目标 | **深化为 T1（见下）** | 标签污染形态是**尾部符号翻转**而非整体虚高 |
| S6 组合与风险 | **部分纠错 + 扩面** | 基准标签降级 P3、μ 接线后移、新增容量下限与「协方差条件于选股」 |
| S7 选股展示层 | **新增一条** | 三态参考集随池宽浮动 ⇒ 两条路径（screener / stocks）不一致 |
| S8 存储与计算效率 | 无策略层补充 | — |

**P2 新增建议（T1–T8，五要素见 [P2-strategy.md](P2-strategy.md) §5、逐条样本外验证方式）**：

| # | 一句话 | 预期收益（量级） | 验证 |
|---|---|---|---|
| **T1** | **可成交标签 + 排序目标**：`close[t+5]/close[t]` 与 `open[t+1]` 成交**错开一天** ⇒ 标签改 `open[t+1+h]/open[t+1]−1`；objective 改 `lambdarank(group=date)` | 修掉两个高 gain 特征（`overnight_gap` 6.10% + `ret_1` 4.11%）的**符号错误**；口径自身虚高实测 −9.4%（RankIC 0.0203→0.0184） | 同 train/valid 段比折外 RankIC + 折外扣费净值 |
| **T2** | 成本口径三修（= S1 的可执行版） | Top-50 消除 8–20pp 虚高、Top-10 14–34pp | `scripts/ab_backtest.py` 双跑 |
| **T3** | `pred_score` 语义落盘 + **水平偏置门禁**（生产模型 `mean=−0.2208%/5d` ≈ −11%/年，RankIC 门禁与它正交） | 拦住「RankIC 达标但水平带 −11%/年漂移」的模型 | DB 只读副本重放 20260907/20260914 候选 |
| **T4** | 特征去共线（波动率家族占 **36.63% gain**）+ 排除 `g1_close`（hfq 绝对价位、非截面可比） | 特征 60 → 约 35；ICIR 提升 **0.05–0.2**（需实测） | 同段比 `valid_rank_ic`/`icir`/`best_iteration` |
| **T5** | 停牌判定统一（`is_halted_row`，见 P1-47）—— **⚠️ 优先级下调**：P1-47 的"死条件"结论已被实测证伪（停牌标注正确、闸门会命中），此建议现仅为**防新增消费点踩坑的加固**，不再是修复缺陷 | 无（原预期收益已不存在） | 「第 10–15 日停牌」用例仍值得加，作为**行为基线** |
| **T6** | 组合约束落地 + 容量下限（见 S6/P1-49） | Top-50 最小 AUM ≈ **311 万元** | `feasible` 断言 + λ 敏感性 |
| **T7** | 归因守恒 + `ls_annualized` 纠错（默认 h=20 ⇒ **放大 20×**、`t` 放大 √20≈4.5×） | 归因可用于业绩归因；消除「多空年化 = 20 倍真值」 | 合成 μ=0.05%/日 断言 h=20 时 ≈12.6% |
| **T8** | **验证窗口 regime 化**：门槛改 4 段×6 月滚动 + 符号一致性≥3/4 + 按样本量归一 `max(0.002, 2·SE)`；同水平新数据走**影子通道**；训练窗口改滚动 3–4 年 | 恢复「漂移可自愈」闭环（当前生产模型自 09-05 冻结） | 重放注册表 **5 个真实候选**的决策 |

**P2 的补充指标清单（M1–M9，未进结论、需先补数据）**：M1 全截面（≥1000 只）每日预测落盘（**父审核员独立复核**：262 个分区中 141 个是完全相同的 120 只、共 19 个不同集合、仅 5 个全截面、全体并集 2498；**但当前生产模型的 5 个分区已是全截面** ⇒ 缺的是历史基线代表性）｜M2 生产模型实盘 OOS RankIC/ICIR/扣费净值（现仅 3 天）｜M2b `daily_bar_hfq` 近期覆盖缺口（09-17 仅 **978/2,488** 只）｜M3 分年度/分 regime RankIC｜M4 真实信号扣费前后 A/B 净值｜M5 退市股数量与平均损失｜M6 约束前后行业暴露与 MDD｜M7 参与率闸门打开后的成交截断率｜M8 `g1_*` 行业快照指纹与 asof 稳定性（`relations/edges.parquet` **不存在**）｜M9 公告/文本特征覆盖度与 PIT 对齐。

**P2 的诚实边界（写入结论限制）**：① 实盘读数**仅 3 个交易日**，**不作为绩效结论**；② 生产模型水平偏置稳定（−0.18~−0.24%）但样本不足以判定为"模型失效"；③ T1/T4 的收益量级属**推算**，采纳前必须样本外验证。

---

## 8. 根因聚类、修复路线与优先级

### 8.1 根因聚类（跨批次，按简报第 7 节第 2 条）

| 簇 | 根因描述 | 影响位置（示例） | 统一修复方案 |
|---|---|---|---|
| **R1 入口可达性缺失** | 「写了代码」被当作「交付了功能」；无「端到端可达」门禁 | 退市回填、公告写入、PIT 财务、文本因子、watchlist 写入、API Token、分红送转、7 个 metrics、`data_update_log` | 加一条**冒烟不变式**：每个对外声称的能力必须有一条「入口→数据非空」的用例（可沿 `test_write_endpoints_smoke.py` 扩展） |
| **R2 单一事实来源缺失** | 同一规则多份拷贝，改一处漏一处 | 费率 8 份、涨跌停 2 套、印花税 ETF 豁免 4:1、夏普 2 套、feature_version 3 套、中性化 2 套、purge 常量 | 建 `domain/trading_rules.py` 单一来源，全部调用方改为引用；CI 加「不得出现费率字面量」的 grep 门禁 |
| **R3 静默三连** | 截断不披露、降级不披露、失败改写成成功 | `ops.py:52`、`quotes_hub` 200、`_scan_dataset`、`/logs`/`/instruments`/`/orders`、`ops._run`、外部源逃逸 50000、`export.py:38` | 按 `alerts.py` 的 `truncated/recent_truncated` 模板统一；错误码按 `errors.py` 码表归类（含启用 `ERR_DATA_SOURCE`/`ERR_EXPR_INVALID`） |
| **R4 状态机与并发边界** | 互斥/幂等/状态淘汰缺失或不对称 | studio `/mining/start` 无互斥、`_TASKS>20` 无状态感知、compute_guard 取消泄漏、ops `_run` 吞异常、param_search OOM、promote 门禁 + SameFileError | 统一走 `pipeline_lock`/`compute_guard`；任务表加 `running` 不可淘汰断言；`_run` 失败必须非零 code |
| **R5 测试网结构性缺口** | 守护规则靠手工白名单，漏挂鉴权天然不可发现 | 读端点 `EXPECTED_ROLES` 手工表、写端点扫描只取 POST/PUT/DELETE/PATCH、`checker` 缺失时 `_role_of` 返回 `None`（**不被当错误**）、`test_backtest_cache_key.py` 漏不对称用例、`paper.py`/`run_ma_cross` 零测试、monotonic 断言恒真、CI mypy 恒红、nightly E2E 非阻塞 | ① 加全局不变式「`/api/v1` GET 的角色不得为 `None`（除显式公开白名单）」；② 写端点扫描纳入 GET 并**把 `None` 视为失败**；③ 高风险模块（paper/broker/param_search/registry）补红用例 |
| **R6 阈值/统计口径错** | 监控与门禁的阈值不随样本规模/分布形态调整，或有方向错 | PSI 水平型误报、KS 临界值、1.5σ 陈旧 std、`_worst` unknown、`rank_ic_tolerance=0`、`monthly_monotonic_ratio` 反向 | 阈值全部改为**由当前池宽/验证段长度推导**；单调性判定补方向用例 |
| **R7 PIT/asof 与口径混用** | 同一张表面向不同口径 | universe 属性非 PIT（已知）、`g1_*` 对 universe 变化不稳定、复权口径不可辨、v1/v2 混读（已知）、`as_of` 无日期、turnover 混源 | 派生面板一律带 `version` + `asof` 指纹；API 响应强制 `basis` 字段 |
| **R8 部署默认姿态** | 默认值面向开发，但部署路径未覆盖 | 容器 `PROJECT_ROOT=/`（**5 个独立失败机制**：EROFS、uid 无权限、loguru sink 无 `delay=True` 且无 stdout 降级、`/data/sqlite` 未挂载、`web` 因 `service_healthy` 永不被创建）、无 env_file、默认 ADMIN_TOKEN、`ENV` 默认 dev 只 warn、`.env` 3 项无效、ticket 进两份 access log、无 `.dockerignore`（7GB 上下文）、CI 从不 `docker build`、`/health*` 恒 200 | 见 P0-1/P0-2/F2/P1-43；**CI 加两条步骤（`docker compose config` + `docker build`）即可拦住全部 5 个 P0**；加「启动自检」打印生效配置来源与最终路径 |
| **R8b 数据破坏型运维路径**（R8 的同源子项，单列因后果不可逆） | 破坏性脚本与文档推荐用法不匹配，且缺少确认/护栏 | P0-8 `backup.py` 先 `unlink` 生产库再还原，而 `README.md:213` 推荐其为 nightly cron；DEP-21 `DELETE FROM model_registry` 无 `WHERE`；DEP-22 `restore.py` 无确认 + `extractall` 无 `filter=`（直接覆盖生产 `data/`）；`create_admin.py` 密码走 argv 且无法改密 | 破坏性操作一律：**默认 dry-run**、二次确认、**先写 tempdir 再原子替换**、危险 SQL 强制带 `WHERE`；文档推荐路径必须指向安全实现（`backup_drill.py` / `POST /settings/db/backup`） |
| R9 错误码治理缺失 | 码表靠人肉维护，两侧各自为政 | `ERR_DATA_SOURCE=51000`/`ERR_EXPR_INVALID=53001` **无抛出点**；`40010~40017`（8 个）、`4002`、`5000`/`4003`/**`5001`**（与 `50001` 形近）两侧均未注册（**P5 批次用 21 个读端点全量实测独立确认：两端各缺同一批 15 个裸码** —— `datacenter.py:684,689,952,969,1017` + `backtest.py:318,325,352,431,504,506,508,537,545,566`）；角色不足直出英文 `FORBIDDEN`；日历非法日期报 50000；`sync/tasks/{id}` 不存在报 **`51001`** 而非 `40400`；前端另有裸数字 `51001`/`40400` ⇒ 同码两套表示 | 以 `errors.py` 为单一事实源**生成**前端 `ERR` 表（构建期同步）；新增码必须**双向登记**；加测试断言「`fail(`/`AQPException(` 第一实参必须是 `core.errors` 常量」；前端对未知码统一追加「（未登记错误码 N）」；外部源失败统一 51000、参数错误统一 40000 **→ ✅ 已按此修复（2026-09-21，第 9 轮）**：裸码清零 + AST 守卫（含变异反证）+ 两端逐值逐名对账（补齐前端 `50001` 与 10 个回测码）+ 未知码标记 + `sync/tasks/{id}` 改 `40400`；**残留**：`ERR_EXPR_INVALID` 仍无抛出点、`_AUTH_DETAIL_CODES` 清理（与 §8.1 同做）、前端 12 条零引用码待删。详见 `FIXES-APPLIED.md` 行 45 |
| **R10 披露字段零落地** | 后端产出、前端不消费（或两端都无） | **P5 批次已给出完整矩阵（21 个带 limit 读端点全量实测）**：**17 项「两端都无」**（S1–S17：`quality-scan` 静默 200、`_scan_dataset` 无 `failed`、`instruments` 的 `total` 随 limit 缩小、`predict` 无 `demean` 标签口径、裸 list×6、三态互斥、15 个裸码…）+ **10 项「后端有前端零消费」**（Top：`data_freshness.status/reason`、`ai_stats` 写死"样本不足"、`close_basis` hfq 提示、`failed_count`、`alerts` 的截断四件套、`/alerts/health` 调用点 0、`anomalies`、`label_price_basis`、`flow_status`、`north.retired`）+ 次席十余项；`nav_tail` 反向（前端读、后端不返）；**`label_price_basis` 只在 ok 路径返回 ⇒ 降级时口径字段消失**（最危险形态：口径不应随数据可用性变化）。**全库唯一合格模板是 `alerts.py` 的截断四件套** | 见 §4.13.3 的统一契约；先归一 **status 的三套取值域**（`ok\|degraded\|unavailable` vs `fresh\|degraded` vs 仅二态），再按「任何 `LIMIT`/`[:N]`/`head(N)` 必带截断三件套、任何被吞的 `except` 必带 `partial`+`failed_count`、`total` 只表示不随 limit 变化的真实总量」加检查；**`datacenter/instruments` 的 `total=len(rows)` 必修**（它是"总数随 limit 缩小"的实现级错误） |
| **R11 文档与代码脱节**（含 docstring 自称；**比无文档更危险**） | 文档/docstring 声称的行为与实现不符，且照做会误导 | `docs/项目开发文档.md` §13 有 **6 个不存在的变量名**（`APP_ENV` 真名 `ENV`）+ 一个必失败的 Dockerfile；`docs/auth-register.md:38-39` 教写不存在的 `AQP_*` 变量；`README.md:200` 把恒 200 的 `/health*` 称 K8s 探针、`:213` 推荐删库脚本、`run.md:241` 称必崩的 compose 为生产方案、`run.md:55` 指向不存在的 `TA-Lib==0.4.28`；代码侧 docstring 失真 ≥4 处（`signal_analysis.py:93` 声称返回 `long_short_daily` 实则不返、`core/stats_cache.py:17-27` 称"并发只算一次"实测 6 并发算 6 次、`quality.py` 称检查复权突变/日历密度实则永不触发、`cross_section.py` 称"增量构建"实为全量扫描+整年重建） | 文档中的**变量名/Dockerfile/健康检查语义**进 CI 校验（`.env.example` × `Settings` 对账脚本可复用）；docstring 承诺的返回值必须由测试断言覆盖 |

### 8.2 修复顺序（影响面 × 严重度 × 成本倒数）

| 序 | 动作 | 对应条目 | 成本 | 为什么排这个位置 |
|---|---|---|---|---|
| 1 | ✅ **已修（第 1–3 轮；证据：`tests/test_backup_safety.py` 4 条、`tests/test_ci_gate_hygiene.py` 的 Dockerfile HEALTHCHECK 路径必须真实存在于 `app.main.app.routes` + compose URL 一致 + `docs/项目开发文档.md` §13 已改写为 `ENV` 真名）**：**修 `PROJECT_ROOT` + compose 挂载/env + 默认凭据 + 重写文档部署契约** | P0-1, P0-2, P0-8, P1-44, DEP-08/09/21 | 1 天 | 容器化部署当前**直接不可用**（5 个独立失败机制）、默认凭据=管理员后门、**唯一一条按文档操作就丢数据的路径**（备份脚本），且文档本身是第二误导入口 |
| 2 | ✅ **已修（第 2 轮；证据：`backtest/param_search.py:79,88` `max_trials` 组合数守卫直接抛错、`tests/test_qlib_vnpy_upgrade.py:243` 参数搜索用例、前端 I-4 断言披露上限 500）**：**`param_search` 先算组合数再物化** | P0-5 | 10 分钟 | 一行修复；**红队修正后的准确影响面**：单请求（3 键 × 1000 候选 ≈ 72 GB）即可 OOM **API 服务**（容器有 `mem_limit: 2g` 会重启，原生部署则整机 OOM） |
| 3 | ✅ **已修（第 3 轮；证据：`tests/test_read_endpoints_rbac.py` 全绿 + 第 18 轮把同类缺口 `P1-50 /market/index/kline` 一并补 `require_role`）**：**补 `/train/readiness` 鉴权 + 加全局 GET 角色不变式** | P0-3, §8.3 R5 | 0.5 天 | 三轮审核漏掉的机制性漏洞，修复一次永久生效 |
| 4 | ~~**裁决空榜契约并同步测试**~~ ✅ **已修（第 17 轮）**：按可判定事实拆成 `unavailable/empty_board`（板块结构性无成分股，如 `board=bse`）与 `ok/no_matching_signals`（池在、当日无匹配），既有 09-14 断言零改写；`FIXES-APPLIED` 行 52 | P0-6 | 0.5 天 | ~~它**不再阻断**紧急修复；仍需裁决，否则 B3b 后续修复可能被判红~~ → 已裁决落地，B3b 后续修复不再有判红风险 |
| 5 | ✅ **已修（第 1 轮 + 第 18 轮单一来源；证据：`domain/trading_rules.py` 成为唯一事实来源（`broker.py:118,191`、`ma_cross.py:44,179`、`a_share_rules.py:102` 全部改调用），`tests/test_trading_fee_single_source.py` 22 条 + 费用黄金值 `stock_2024_sell_cost=80.18999999999998` 逐位不变）**：**摩擦/换手/印花税口径三连修** | P1-1, B5-08, B5-10, B2-8 | 1–2 天 | 直接决定回测数字可信度；B2-8 影响 0.39% 行 |
| 6 | ~~**退市数据接线（enrich_delist 进流水线）**~~ | P1-4, B5-14 | 1–2 天 | ✅ **已修（第 18 轮，`FIXES-APPLIED` 行 60）**：①`enrich_delist_dates()` **永不抛**、`availability ∈ {unavailable, empty_source, no_local_match, ok}`（区分"源不可用"与"没有退市股"）；②`step_enrich_delist` 进 `FULL_STEPS`（`validate` 之后、`build_universe*` 之前 ⇒ 当晚生效）；③bt 面板新增 `delist_date` 列（旧分区缺列仍可读），`/backtest` 四个端点披露 `present/mixed/absent/no_panel` + 源可用性；④`fetch_delist_list` 增 `list_date` 只补空、`scripts/enrich_delist.py` 改为复用同一实现。**本体复核**：`delist_date` 非空 **0/5552**、`list_date` 122（2.2%）、bt 面板 5 个年分区都没有该列 ⇒ 报告描述属实。验证：`test_delist_wiring.py` 9 passed + 相关 7 文件 **86 passed**（父审核员复跑）。**残余**：生产库仍需跑一次流水线回填（源实测可达，361 条）；重建 `universe_daily_bt` 改历史基线需 A/B 闭环，未做；`list_date` 在册标的 97.8% NULL 仍需别的来源 |
| 7 | ~~**`ops._run` / 外部源 / `limit` 的静默修复**~~ | F7, F4, F10, A2 | 1 天 | ✅ **已修（第 18 轮，`FIXES-APPLIED` 行 56）**：①**F7**：`ops.py:461-478` 不再吞异常——非法日历日期在流水线**之外**解析 ⇒ `40000`（此前 `code=0` + `data.ok=false` 假成功），`PipelineBusy` ⇒ `40900`，其余交全局处理器 ⇒ `50000` + 服务端堆栈；②**F4**：`_SCAN_SYMBOL_LIMIT=200` 常量同源 + 披露 `symbols_available`/`total_symbols`/`truncated`/`scan_limit`（前端 `production.ts`/`DataQuality` 消费）；③**F10**：`/quality` ⇒ `Query(50, ge=1, le=10000)`、`/logs` ⇒ `Query(60, ge=1, le=500)`（实测修前 `limit=-1/0/1e9` 全部 `code=0`，`/logs?limit=0` 真返回全部日志）；④**A2 证伪**：其唯一定义（B3b-data.md:84）即 `quotes_snapshot` 200 截断，已由第 12 轮 P1-15 修复。变异反证 3 组（还原旧实现 ⇒ `40000`/`50000`/`40900` 三条断言全红、`KeyError: truncated`） |
| 8 | ~~**模型回滚通道 + SameFileError + 门禁可比性 + 验证窗口 regime 化**~~ | P1-6, P1-7, P1-39, R14, **T8** | 2–3 天 | ✅ **已修（第 18 轮，`FIXES-APPLIED` 行 57；T8 为部分修、残余如实登记）**：①**P1-7**：`registry.py::_same_file`/`_copy_artifacts` ⇒ equal-path 幂等跳过拷贝（`shutil.copy2(p,p)` 抛 `SameFileError` 已先复现；重复 promote 由崩溃变 exit 0）；②**P1-6 复核为"未修"并本轮补上**：新增 `rollback_model()`（目标必须**曾被提升过**、必须有当前生产、`reason` 必填、delta 与"普通门禁会拒"的事实进 `checks.rollback`、幂等），CLI `promote_model.py --rollback`；**默认阈值一个都没放松**（实测 `DEFAULT_PROMOTE_POLICY.rank_ic_tolerance/icir_tolerance` 仍为 `0.0`）；③**R14**：`lineage_comparability()` 默认披露 `feature_version`/`dataset_version`/验证区间；不可比时三分支（显式 opt-in / **换 T8 regime 判据** / 拒绝），避免"修完仍冻结"；④**T8 部分修**：`regime_windows()` 4 段连续窗口 + 边界/均值/符号披露、`regime_check()`（符号一致性 ≥3/4 且均值 ≥ max(0.002, 2·SE)）默认披露、`enforce_regime_gate=True` 或 R14 替代分支时强制生效。残余：20 日影子跟踪、训练窗口滚动 3–4 年未做；旧候选无 `valid_regime_windows` 时不可比即拒绝（需重训）。新增 24 条用例 + 回归 151 passed |
| 9 | ✅ **已修（第 3 轮；证据：`tests/test_paper_position_guard.py`）**：**模拟盘持仓校验** | P1-3 | 0.5 天 | 造钱缺陷；paper.py 无测试 |
| 10 | ✅ **已修（第 1 轮；证据：`backtest/engine.py:607` 原判定式 `all(rets[i] > rets[i+1])` 已按正确方向改写，`scripts/p2_experiment.py:84` 打印值随之更正）**：**`monthly_monotonic_ratio` 方向 + 测试断言** | P1-5 | 2 小时 | 实验报告数字是错的 |
| 11 | ✅ **已修（第 1 轮；证据：`api/v1/backtest.py:433-448` 缓存键补 `legacy{0|1}`，`tests/test_backtest_cache_key.py` 7 条含不对称用例）**：**缓存键补 `use_legacy_engine` + 不对称用例** | P1-2 | 2 小时 | 键碰撞返回另一套引擎结果 |
| 12 | ✅ **已修（第 1 轮；证据：`services/quotes_hub.py:31,71` 分片 `QUOTES_SHARD_SIZE=200` + 截断披露，`tests/test_quotes_snapshot_sharding.py` 专条防"第 201 只起静默失效"）**：**`quotes_snapshot` 200 截断披露 + 分片** | P1-15 | 0.5 天 | 第 201 只起预警静默失效 |
| 13 | ~~**ETF 资金流字段口径**~~ | P1-14 | 1 小时 | ✅ **已修（第 11 轮）**：`net_inflow` 直接取 `f52`；分项净额外显；缺失不再伪造成 0；真实观测样本复算 +29.4392% 已固化（9 条用例） |
| 14 | ~~**监控触发器 PSI/KS/σ/_worst**~~ | P1-17, P1-21, R5 | 1–2 天 | ✅ **已修（第 12 轮）**：①PSI 判定口径 = 按交易日截面标准化（raw 降为披露）；②KS 同口径 + 超限比/单日临界尺度；③σ 按当日池宽折算（修正"IC 归零仍判 healthy"的假阴性）；④`unknown` 不再被 `healthy` 压过。真实面板 `0/85` 超 0.25（原 21/85）、注入污染 0.354 仍报警；17+1 条用例（含集成锁与变异反证） |
| 15 | ~~**组合约束（weight_cap 不可行/μ/归一/基准标签）**~~ | P1-13, B2-11, B2-12, B2-16 | 1–2 天 | ✅ **已修（第 13 轮）**：①`cap_feasibility`/`cap_info_for` 纯判据 + `n·cap<1` 时 `feasible=false`（**行为有意不改**，避免动历史净值）+ 引擎/两个 API/前端全链路披露；②**新发现**：默认 `equal` 方案**从不执行** `weight_cap`（实测 max w=0.25>cap=0.2）⇒ 披露区分"半仓"与"未执行"；③Σw∈[0.99,1.01] 容差内**归一**并披露因子；④rf 单源化（`metrics.RISK_FREE_ANNUAL`）且 `_compute_metrics` 形参**真正生效**、基准标签由调用方给出（不再硬编码"沪深300"）；⑤`mvo` 的 μ≡0 披露 + 前端标签改"MVO（μ 不可用 ⇒ 实为最小方差）"。23 条用例（含 4 处缺陷本体反证） |
| 16 | ~~**`g1_*` asof 不稳定（universe 快照指纹化）**~~ | P1-18 | 2–3 天 | ✅ **已修（第 15 轮，`FIXES-APPLIED` 行 51）**：机制经探针**证伪后重写**——不是行归一化分母，而是"哪些邻居当天有取值"。①同桶新标的入池 ⇒ 老标的 `g1_` 3.0→**252.0**（冻结节点集后可挡：非 universe 不入分子分母，仍 3.0，其自身 `g1_=NaN`）；②已冻结标的行情消失（`read_all_symbols(skip_empty=True)` 跳过被隔离标的空目录）⇒ 3.0→**2.5**、**不可复原** ⇒ 改为**默认拒绝静默改写历史**+血缘落盘+`FEATURE_ALLOW_GRAPH_DRIFT=1` 显式授权。新增 11 条用例 + 既有用例补"拒绝且不改写"断言；离线/服务两条链路同源接线（AST 反向锁） |
| 17 | ~~**删死代码第一批（无调用方函数 + 剩余 3 个 F841 + 7 个 metrics + 死配置）**~~ | §6.1 | 1 天 | ✅ **已修（第 18 轮，`FIXES-APPLIED` 行 53；台账见 `P17-orphan-scan.md`）**：共删 **35 项**。**不照单直删**：自建 AST 孤儿扫描（297 文件 / 1069 顶层符号，装饰器注册的路由不算孤儿）复核后，①`stamp_duty_rate`/`check_pct_limit` **是活的**（未删）；②`write_year_batch` 报告判定正确（`app/` 5 处=定义+`__all__`+一句"原实现曾用它"的注释、`scripts/` 0、`tests/` 62）但它是 53 处夹具的写入器 ⇒ **登记待裁决**；③额外发现并删掉 5 项报告未列的死代码（`market._build_sectors`、`realtime._fetch_ths_fflow`+其专用解析器 `_cn_amount_to_float`、`quotes_hub` 2 个、`train_lgbm.daily_ics`、`paper._exec_price_for`）。**三类不删**：ORM 声明类（`Base.metadata` 注册，删=改 schema）、"有读者无写者"的待接线能力（公告/财务/风格）、仅测试引用的 27 项能力。连带迁移 `test_data_prep.py` 夹具到生产写入器；反向锁 29 条（AST 引用扫描 + `/metrics` 不得再出现 7 个从不写入的指标） |
| 18 | ~~**统一费率单一来源**~~ | R2, §6.4 | 1–2 天 | ✅ **已修（第 18 轮，`FIXES-APPLIED` 行 58）**：新建 `domain/trading_rules.py` 收敛**唯一数字来源**（`COMMISSION_RATE_DEFAULT=0.0003`/`COMMISSION_MIN=5.0`/`STAMP_DUTY_STOCK_RATE=0.0005`/`_LEGACY=0.001`/`CUT_DATE=2023-08-28`/`TRANSFER_FEE_RATE=0.00001`），13 个数值点 / 8 个模块改引用（broker/ma_cross/engine/strategy_base/paper/portfolio/backtest API/ORM 默认值），`a_share_rules` 的分档函数**一行未动**（复用而非重写）。**数值逐位不变**：改动前后 golden 对比 `diff_count=4`，仅 4 条为**有意删除的本地副本名**，所有计算数字逐位相同（如 `stock_2024_sell_cost=80.18999999999998`）；AST 守卫（费率字面量只许出现在 `trading_rules.py`，带变异反证）；**独立复核**：`grep` app/ 该批字面量现仅存于 `trading_rules.py`，22 条新用例 + 既有 4 文件 77 passed。保留未合并：`orchestrator` 的 `rtol=1e-5`（数值容差）、`app_settings.commission_pct=0.03`（百分数单位）、slippage/decay 6 处（属第 5 项产品决策） |
| 19 | ~~**前端 B9a/B8/B9b/B9c 缺陷（竞态/骨架屏/角色门槛）**~~ | §4.7 + 待并入 | 2–3 天 | ✅ **已修（第 18 轮，`FIXES-APPLIED` 行 59；34 项中 33 项已修、1 项部分修）**：**竞态类**（B9a-F01/F02、B8-06、B9b-I-7/I-8/I-9/I-10/I-12）统一用「请求序号 + AbortController」丢弃旧响应，切标的时清空旧状态；**"假信息"类**：核心指数改按 `code` 匹配（不再按位置猜）、删掉后端从不返回的「走势」列、删掉伪造的 loading 标记、`ErrorState` 不再吞后端消息、`Pipeline` 去掉不存在的 `desk` stage、`OrderDesk` 熔断状态不可读时显示「状态不可读」而非染绿；**角色门槛（P1-23）**：前端按 `hasMinimumRole(role,'researcher')` 生成门控（导出/日志/状态/自动更新/训练/清缓存/备份/回测/日报），且**挂载不再越权发请求**（原 Settings 挂载即跑 2 次连接测试）；**白屏类**：`DataCenter.DiskGauge` 的 hooks 提升到所有 return 之前（原 percent 翻转 ⇒ hooks 3↔0 ⇒ 整站降级）；**二次确认**：全量重构/修复缺漏/删预警规则统一 `ConfirmModal`。**部分修（C-3）**：`/desk/orders` 后端无 `total/truncated` ⇒ 前端只能声明"数据窗口：最近 200 条"，真实母单总数与"是否还有更早"需后端补字段（已单列为残余）。验证：`npx tsc --noEmit` **0 error / EXIT=0** + 32 条源码锚点断言（父审核员独立复跑通过） |
| 20 | ~~**CI 门禁修复（mypy 10 错、E2E 阻塞、备份污染、qa_browser 忽略）**~~ | §5 工程门禁 | 0.5 天 | ✅ **已修（第 18 轮，`FIXES-APPLIED` 行 54）**：①`mypy app/` **17 错/4 文件 ⇒ 0**（polars schema 类→实例、`stats_block` 收窄、`type: ignore` 码纠正、`dict[str, Any]` 标注；全部零行为变更）；②新建仓库根 `.dockerignore`——实测构建上下文 **7.12 GB**（`backend/.venv` 4.70GB + `data` 2.26GB + `node_modules` 149MB…）**且含真实凭据 `.env`**；③`.gitignore` 补 `backend/.tmp_*/`、`qa_browser/`、`data/_purged_*/`、`.workbuddy-ai/`（此前这些一直以未跟踪文件污染 `git status`）；④`ruff check app tests scripts --select F,E9` **27 处 ⇒ All checks passed**，并揪出一条**真 bug**：见行 55（`scripts/alerter.py` 缺 `import sys` ⇒ 运维告警脚本必然 NameError，历史审计记为 LOW-001 长期未修）。**证伪一项**：报告称"`backend/Dockerfile:56-57` 的 HEALTHCHECK 被 compose 覆盖成死配置"——compose:75-85 已用同一 URL `/health/ready`（该注释即 P1-43/DEP-10 的修复记录），镜像内那条对无 compose 的 `docker run` 仍有效 ⇒ 不改，改为**加锁防再次分叉**（并锁"该 URL 必须是真实 FastAPI 路由"）。⑤**工作流门禁（本轮追加，三处"门禁从不生效"）**：`ci.yml` 的 push 过滤只有 `main` 而本仓默认分支是 **`master`** ⇒ 主干推送从不触发 CI；ruff 步骤只查 `app/` 且只 3 条规则 ⇒ `tests`/`scripts` 的问题（含 alerter 的 F821）永不出现；`nightly.yml` 用 `|| echo` 把 E2E 失败显示成绿色 ⇒ 改为 `[main, master]` / `ruff check app tests scripts --select F,E9` / `continue-on-error: true`，并各自加锁。新增 `test_ci_gate_hygiene.py` 12 条 |

### 8.2b 后续批次新增项在优先级中的插入位置（不重排上表，避免编号漂移）

> **§8.2b 收口现状（第 18 轮末，逐条取证）**：下表插入项**除下列 4 类外全部已修**，各自带防回归用例：
> ①**已修**（证据锚点）——P1-33（`market_service.py:41-50` 方向过滤，本体反证"840.0 亿 vs 真实 0.0"）、B6-03（`market_service.py:70-89` 三态 + `market.py:529-534` 消费方）、P1-28/P1-40/P1-41（`parquet_store.py:73-77` 读-改-写互斥 + `sync_service.py:36-42` `completed/resume` 语义，锁在 `tests/test_sync_resume_not_wiped.py`）、P1-42（`universe_daily_bt` 已重建：**1,133→2,495 只 / 1,283,107→2,851,203 行 / 2026-09-04→2026-09-18**）、P1-25+B4b-16（`signal_analysis.py:140-142` 明确改 `252/h` 年化，原实现 `mean×252` 对 20 日视界放大 20 倍）、P1-39 / P1-37 / P1-38 / P1-48（第 8 轮 + 第 18 轮：`xsec_demean` 默认 `True` + `_MIN_XSEC_NAMES=2` 退化截面守卫 + 逐日截面 RankIC 筛选 + `pred_level` 落盘/披露/门禁，见 `FIXES-APPLIED` 行 62）、B6-02（`evening_routine.py:126-135,158-175` failed 终态 + 有界重试）、B7a-03（`screening.py:985` `exclude_st and r.get("is_st")` + `screener.py:679` 归一，ST 过滤不再是空转）、前端 P1-22/P1-24/P1-25/P1-26（第 18 轮 B9 批次，`npx tsc --noEmit` EXIT=0 + 32 条源码锚点锁）、§4.8 序 16（`Query` 当 `refresh` ⇒ 已修）、P1-50 + §8.3 缺口 1 的两行断言、`datacenter/instruments` 的 `total` 改真实总量、P1-35/P1-36/B7a-06/P1-43（第 7–8 轮）。
> ②**仍未做（3 条，均需产品/流程裁决而非代码能力）**——**T8 验证窗口 regime 化**（影子通道已接线、`enforce_*` 默认关闭；改判据会改变生产模型晋级结果，属产品决策）、**status 取值域归一**（三套并存，需先定 `fresh` 是否保留为 `ok` 别名）、**DEP-16 两条 CI 步骤**（`docker compose config` + `docker build`；本机无 docker，无法在此环境验证 ⇒ 未加，已登记）。
> ③**为保持"零假绿"而**不做**的两处其中一处已如实收口**：`stock/search`、`portfolio/search` 仍是裸 list（前端按数组消费，改形状会破前端）；其截断披露以"文档登记 + 产品决策"形式保留，**不假装已修**。
> ④**另有一条本轮新发现**（`P1-51` `scripts/alerter.py` 缺 `import sys`）已修并加"引导段必须可执行"的 CI 锁（`test_ci_gate_hygiene.py`），因此该类问题今后**在 CI 里当场变红**。

| 插入位置 | 新增项 | 成本 | 理由 |
|---|---|---|---|
| **与第 1 项并列（P0，先做）** | **P0-8 改 `scripts/backup.py` 的 `main()` + 改 `README.md:213` 的 cron 推荐** | 1 小时 | **唯一一条「按现有文档操作就会不可逆丢数据」的路径**；同时把 `purge_legacy_models.py:71` 的 `DELETE` 补 `WHERE`（DEP-21） |
| **与第 1 项并列（P0）** | **P0-7 因子净值前视偏差**（`gp_miner.py:260,279-286,331,337,343` 改用 `t+1` 收益） | 2 小时 | 用户可见的错误研究结论；修复=索引位移+一个"自反式因子净值应为 0"的用例 |
| **第 1 项之后立刻** | **P1-44 重写 `docs/项目开发文档.md` §13 + `docs/auth-register.md:38-39`** | 半天 | 变量名错（`APP_ENV`→`ENV`）使 P0-2 的 prod 闸门永不触发；文档是 P0-1/P0-2 的**第二入口** |
| **紧随第 1 项**（部署之后） | **P1-32 ETF 三端点补降级包裹**（`etf.py:288,299,319,477` + `data/etf.py:384`） | 1 小时 | ①修掉全量套件的 3 例红灯；②让 `ERR_DATA_SOURCE=51000` 首次有真实抛出点；③同文件已有正确范式可抄 |
| **紧随其后** | **P1-33 北向资金方向过滤 + 量纲复核**（`market_service.py:22-27`） | 2 小时 | 前端首屏可见的数字错误，用户可直接据此决策 |
| **与 P1-33 同批** | **B6-03 `market_service` 三态退化**（`market.py:550-583` 消费方） | 2 小时 | 与 P1-33 同文件同函数，一起改最省 |
| **第 5 项（摩擦口径）同批** | **P1-25 分层多空键名 + B4b-16/P2-T7 的 20× 年化放大**（`signal_analysis.py:126` 改 `mean_ls×252/horizon`） | 2 小时 | 与 S1 同一批口径修正；**前端"读不到"且"算错"**两件事一起修 |
| **第 6 项（退市）同批** | **P1-28 断点续传被 `clear()` 抹掉**（`sync_service.py:657`）+ **P1-40 `write_partition` 加锁** + **P1-41 `_audited` 自愈** | 半天 | 三条都是「删一行/加一把锁 + 补不变式用例」；P1-40 的前提是坚持 `--workers 1` |
| **第 6 项同批（策略有效性）** | **P1-42 `universe_daily_bt` 纳入夜间流水线** | 半天 | 不修则**近期区间回测结论全部不可用**；与退市修复一起做最省 |
| **第 7 项（静默修复）同批** | ~~**P1-35 日历非法日期 → 40000**、**P1-36 空涨跌分布不造"情绪 50"**、**B7a-06 降级 reason 外泄异常串**、**P1-43 `/health*` 恒 200`**~~ | 半天 | ✅ **四项已全部修完（2026-09-21，第 7–8 轮）**，见 `FIXES-APPLIED.md` 行 40–43；同批**顺手清掉 B7a-08/B7a-09**（行 44）。套件 `1368 passed / 0 failed / 8 skipped`。**同簇残留**：R9 错误码治理（两侧码表人肉维护、`ERR_DATA_SOURCE`/`ERR_EXPR_INVALID` 无抛出点、`40010~40017`/`4002`/`5000`/`4003`/`5001` 未登记、`sync/tasks/{id}` 不存在报 `51001` 而非 `40400`） |
| **第 8 项（模型门禁）之前必须先做** | **P1-39 把 `xsec_demean`（及筛选口径）落盘 + 门禁只比同口径候选** | 半天 | **不做这一步，第 8 项的"回滚通道"修完仍会误判** |
| **第 8 项改为 T8** | **T8 验证窗口 regime 化**（4 段×6 月滚动 + 符号一致性 + `max(0.002, 2·SE)` + 影子通道） | 2–3 天 | 实测：`valid_rank_ic=0.1020` 来自**单一段 regime**，42 棵树 train/valid/test = 0.156/0.102/0.0705 单调衰减 ⇒ **加容量治不了**，必须改判据的比较对象 |
| **第 8 项同批** | **P1-37 `xsec_demean` 默认值与入口统一**、**P1-38 特征筛选改逐日截面 IC**、**P1-48 `pred_score` 语义落盘 + 水平偏置门禁** | 2–3 天 | 真实面板实测 RankIC 0.0144 → **0.0832**（5.8×）；池化 IC 偏差 270×；生产模型水平偏置 −11%/年而门禁与之**正交** |
| 第 9 项之后 | **B6-02 晚间例行失败终态**（`evening_routine.py:110`） | 2 小时 | 一行改 `_mark("failed")` + 补重试判据 |
| 第 12 项（截断披露）同批 | **B7a-03 ST 过滤静默失效**（`screener.py:262` + `screening.py:71-103`） | 半天 | 与 P0-6 空榜裁决同一处代码 |
| **第 15 项（组合约束）之前** | ~~**P1-47 停牌判定统一 `is_halted_row`**~~ **（已从优先级表移除：P1-47 的"死条件"前提被实测证伪，见 §9.3 R-6）** | — | 原理由是"停牌日以陈旧价成交的唯一通道"，该理由不成立：面板 `is_halted` 与 `volume IS NULL` 完全对角、`volume<=0` 亦会命中。保留为可选加固 |
| 第 19 项（前端）同批 | **P1-22 `useChart` deps 缺 `node`**、**P1-25 键名错位**、**P1-26 Top-K 单位 ×100**、**P1-24 移除 ADMIN_TOKEN 迁移** | 半天 | 四条都是"改一行/改一处"的高性价比前端修复 |
| 第 20 项（CI）同批（**优先级应提前**） | **DEP-16 CI 增 `docker compose config` + `docker build` 两步**、**P3-dedup 的"修复不变式"用例规范** | 半天 | **两条 CI 步骤即可拦住全部 5 个 P0**；不变式规范可防「已修但回归」再次发生 |
| 第 20 项同批 | **§4.8 序 16 `Query` 当 `refresh`**（`app_settings.py:157`） | 10 分钟 | 一行修掉系统性缓存击穿（27~30s 冷算的根因） |
| **与第 3 项同批（鉴权，最高性价比）** | **加 §8.3 缺口 1 的两行断言** + **P1-50 `/market/index/kline` 补 `require_role`** | 30 分钟 | 这两行断言会让 P0-3 与 P1-50 **当场变红**；不加则第 3 条会继续被漏——**修的是"发现机制"而非单条缺陷** |
| **第 12 项（截断披露）同批** | **`datacenter/instruments` 的 `total=len(rows)` 改为真实总量** + **归一 status 三套取值域**（`fresh`→`ok` 别名过渡） | 半天 | 「总数随 limit 一起缩小」是实现级错误；三套取值域不归一，§4.13.3 的契约无法落地 |

### 8.3 R5 专项：为什么「漏挂鉴权」能连过三轮审核（**P5 批次已定位到精确机制**）

**结论：存在两条互相独立的机制缺口，各自都足以让漏挂鉴权永远通不过测试。**

**缺口 1 —— RBAC 测试只验白名单、不验完备性（P5 定位 + 父审核员读码确认）**
- `backend/tests/test_read_endpoints_rbac.py:184-187` 的 `_live_get_roles()` **确实遍历了全部 GET 路由**，但 `:190-199` 的断言**只迭代 `EXPECTED_ROLES.items()`**（手工白名单）：
  ```python
  mismatched = {path: (_match_route(live, path), role)
                for path, role in EXPECTED_ROLES.items()      # ← 只走白名单
                if _match_route(live, path) != role}
  ```
  ⇒ **一条路由只要不在 `EXPECTED_ROLES` 里，就既不要求它有角色、也不要求它被声明为公开**——它是"未被分类"而非"通过"。
- 这解释了为什么 `/datacenter/train/readiness`（P0-3，已被三份历史报告登记）和 `/market/index/kline`（P1-50，本轮新发现）能长期存在：**它们从未进入测试的视野**。
- **修复只要两行**：
  ```python
  unclassified = set(_live_get_roles()) - set(EXPECTED_ROLES) - DECLARED_PUBLIC
  assert not unclassified, f"未分类的 GET 路由（必须显式声明角色或公开）: {unclassified}"
  ```
  其中 `DECLARED_PUBLIC = {"/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/register/status",
  "/api/v1/market/overview", "/api/v1/market/overview/daily", "/api/v1/market/overview/rt"}`（即 `App.tsx:86` 已声明的公开集）。**这两行会让 P0-3 与 P1-50 当场变红。**

**缺口 2 —— `_role_of` 的缺省语义把"查不到"当成"无要求"**
- 当路由上没有可识别的 checker 时，`_role_of` 返回 `None`，而调用方**不把 `None` 当错误**（只在白名单比对时用）。

**缺口 3 —— 写端点扫描只取 POST/PUT/DELETE/PATCH**，GET 型写副作用（如 `:340-344` 的读路径 `mkdir`，B3a-16）天然不在扫描范围。

**缺口 4 —— 手工白名单本身会漏**：`test_backtest_cache_key.py` 漏了不对称用例（B5 已报），`paper.py`/`run_ma_cross` 零测试。

**因此本报告对 P0-3 的定位不是"某个端点忘了加装饰器"，而是"这条链路上不存在能发现'忘加'的断言"。** 修复优先级：**缺口 1 的两行断言 > 补 P0-3/P1-50 的装饰器**（否则第 3 条会继续被漏）。

---

### 8.3b 基线更正：`/market/index/kline` 的**裁决**（计数不变，仍为 8 条无鉴权路由）

**先说结论：`00-BASELINE` §4 的「8 个完全无鉴权端点」计数是正确的**（该清单 `:107` 本来就列出了 `/market/index/kline`）。**父审核员在整理 P5 结论时一度误以为计数漏了它、写成「应更正为 9」——那是错的**：我的复跑得到 9，是因为我的过滤器把 `/notify/stream` 也算进来，而它**另有独立 ticket checker**（属定义放宽的假阳性）。**此处保留这次自我更正的过程，以符合简报对"诚实标注矛盾结论"的要求。**

**真正的错误在裁决步**：基线 `:115` 把 `/market/index/kline` 与 4 个 `/market/overview*` 归为一类判为「有意公开」，依据是 `App.tsx:86` 的注释。但该注释只说「**市场概览**是唯一公开业务页」——**不覆盖指数 K 线**。

| 分类 | 端点 | 是否可解释 |
|---|---|---|
| 有意公开（`App.tsx:86` 声明） | `/auth/login`、`/auth/register`、`/auth/register/status`、`/market/overview`、`/market/overview/daily`、`/market/overview/rt` | ✅ 6 个 |
| **无出处** | `/datacenter/train/readiness`（P0-3）、**`/market/index/kline`（P1-50）** | ❌ **2 个**（基线只认了 1 个） |
| 合计「无任何鉴权依赖」 | 上述 8 条 | 计数正确 |

⇒ **本轮对该基线的更正是"裁决"而非"计数"**：无出处端点 **1 → 2**。两处证据均已按 §8.3 的机制解释（白名单外的路由从未被断言）。

1. `tests/test_write_endpoints_smoke.py:171-179` 的运行时扫描**只取 POST/PUT/DELETE/PATCH** ⇒ GET 天生不在守护范围。
2. `tests/test_read_endpoints_rbac.py:45-95` 的 `EXPECTED_ROLES` 是**手工白名单**，`/datacenter/train/readiness` 不在其中。
3. 漏挂装饰器 ⇒ 没有 `checker` 闭包 ⇒ 内省 `_role_of` 返回 `None` ⇒ **两套扫描器都不把 `None` 当错误**（写端点那套甚至把它当「公开端点」放行）。
4. 直接后果：`/train/readiness` 在 `docs/audit-2026-09-14/backend-qa-report.md`、`docs/audit/AQP_QA审查_20260912.md`、`docs/audit-2026-09-14/backend-architecture-review.md` **三次被登记**却依然存在。

**修法（30 行脚本即可，见 [00-BASELINE §4](00-BASELINE-AND-VERIFICATION.md)）**：断言「所有 `/api/v1` 端点的角色集合非 `None`，除非在显式公开白名单 `{/auth/login, /auth/register, /auth/register/status, /market/overview*, /market/index/kline}` 内」——反向断言（白名单外的 `None` 即失败），而不是正向白名单。

### 8.4 归并后的问题数（去重同根因）

| 归并项 | 原始条目 | 归并后 |
|---|---|---|
| 印花税/过户费/费率拷贝 | B5-10, B5-18, B2-18, §6.1 费率 | 1 条（+8 处位置） |
| 静默截断/降级 | F4, F9, A2, F10, A3 | 1 条根因 + 5 处 |
| 涨跌停两套实现 | B2-8, B2-9, B2-10, B5-11, P1-12 | 1 条根因 + 5 处 |
| 漂移误报 | P1-17, R3, R5, R4 | 1 条根因 + 4 处 |
| 版本可比性/回滚 | P1-6, P1-7, P1-16, R14, R9 | 1 条根因 + 5 处 |
| 未接线功能 | B1,B2,B3,B7(B3b)、§6.3 共 10 项 | 1 条根因 R1 |
| 前端 `ERR` 死码 + 裸数字 | §6.1 前端 | 1 条 |
| **部署路径不可用** | P0-1 的 F1–F5、DEP-03/04/05/08、Dockerfile/compose 全部路径项 | **1 条根因 R8 + 5 个失败机制**（不是 5 条独立问题） |
| **manifest 数字口径** | B3a-1 + 已知「manifest 口径不一致」 | 1 条根因 + 2 条写路径 |
| **数据破坏型脚本** | P0-8、DEP-21、DEP-22、DEP-27 | 1 条根因 R8b + 4 处 |
| **文档/docstring 失真** | P1-44、DEP-13/14/26/29/31、`signal_analysis.py:93`、`stats_cache.py:17-27`、`quality.py`、`cross_section.py` docstring | 1 条根因 R11 + 10 处 |
| **披露字段零消费** | R10 + P5 批次 | 1 条根因 + 全字段 |
| **归并后总计** | **322**（原始条目） | **约 205 条独立问题**（已含全部 20 批：B0/B3a/P2/P3-dedup/P5/REDTEAM） |

---

## 9. 与既有台账的关系（去重，避免重复报）

| 本次条目 | 既有登记处 | 状态 | 本次新增价值 |
|---|---|---|---|
| P0-3 `/train/readiness` 无鉴权 | `audit-2026-09-14/backend-architecture-review.md:150,182`、`audit/AQP_QA审查_20260912.md:117,166`、`audit-2026-09-14/backend-qa-report.md` | **已知未修**（3 次） | **机制漏洞定位**（§8.3）+ 反向不变式修法 |
| P1-4 幸存者偏差 / `delist_date` 0 填充 | `audit/2026-09-05-量化专项审查报告.md:74,88`、`audit/AQP_修复阶段最终报告.md:229`、`audit/ab/task16-final-AB.md:39-44` | 已知（修复未落地） | ①**父审核员实测今日仍 0/15**；②发现 `upsert_delist_dates` 唯一调用方是**未接线的手工脚本**；③**API note 披露不实**（新） |
| 印花税不分段 / 过户费未建模 | `audit/2026-09-05-量化专项审查报告.md` 问题 13、14 | 已知 | **ETF 豁免口径 4:1 不一致**（新）+ 费率 8 份拷贝清单 |
| 网格寻优样本内自证 | 同上 问题 11（P2） | 已知 | B5-07 补充「前端把 KPI 放 WF 表上方且无标注」（新） |
| manifest 口径不一致 | 既有台账 | 已知 | 不重复 |
| `gp_miner` 缺 `except BaseException` | 既有台账 | 已知 | B4b 待并入时只给延伸结论 |
| 两套回测 / 特征 v1-v2 两代 | 既有台账 | 已知 | 本次补「删除前置条件清单」（§6.1） |
| `feature_version` 硬编码 v1 | `audit/AQP_修复报告_20260912.md` T-08 | 已知 | R8 补「三套解析口径 + monitor 不披露」（新） |
| 前端 `strategy_type/slippage_bps/use_legacy_engine` 从不下发 | `audit/AQP_前后端逻辑审查_20260912.md:59`（F-07） | 已知 | 本次补「缓存键漏 `use_legacy_engine` ⇒ 键碰撞」（新，P1-2） |
| `/settings/apikeys/rotate` 伪功能 | `audit-2026-09-14/backend-architecture-review.md:250` | 已知 | 本次确认「永远 fail + 前端零引用」 |
| `.env` 无效配置（JWT_EXPIRE_SECONDS 等） | `audit/AQP_架构审查_20260912.md` A-06 | 已知 | 补实测证据 + 新增「名字写错」与「文档幻觉」两档 |
| 空榜 `no_matching_signals` | `audit-2026-09-14/data-freshness-degradation-fix-report.md:39,56` | **有意设计** | 与新契约冲突 ⇒ P0-6 裁决（不是 bug）；**红队复核后已由 P0 下调为 P2**（前端渲染正确） |
| SSE ticket 设计 | `audit-2026-09-15/architecture-review.md`、`sse-ticket-fix-report.md` | 已修且高质量 | **F2（ticket 进 access log）是新缺口**：错误日志已脱敏但 access log 未关 |
| `/market/overview*` 免鉴权 | `audit-2026-09-14/backend-architecture-review.md:182` | 已知（有意公开有注释） | **不报**（`App.tsx:86` 明确注释） |
| `compute_guard` | `audit-2026-09-15/REMEDIATION-SUMMARY.md:93` | 半修 | B1-1 补「取消泄漏 slot」机制复现 |
| `export.py` 空榜 500 | `audit-2026-09-15/qa-verification.md:337` | 已修 | F5 是**另一个**：非法日期 → 50000（新） |
| 退市清算用例 | `tests/test_delist_liquidation.py:4` 自陈「`delist_date` 从未填充」 | **项目自己已承认** | 用于证明「披露不实」 |
| **P1-28 断点续传从未生效** | **「已修」侧（可核验原文）**：`docs/audit/AQP_前后端逻辑审查_20260912.md:194`「失败续跑 ✅…`restore_sync_state` 跨进程恢复」+ `docs/audit/AQP_修复端到端验证_20260911.md:30` 实测启动 `restore_sync_state: 214 completed` | **⚠️ 已修但回归（确证）**：被 `sync_service.py:657` 的无条件 `clear()` 抹掉（实测 3 只 → `[]`） | 本轮最值钱的一类结论。**修正**：§9 初稿所引「09-15 P2-14」在 `docs/audit-2026-09-15/` 关键词无法定位（该目录无此条；P2-14 在 09-14 是 `js.eval`、在 `AQP_P0P1P2复审报告.md:12,75` 是任务持久化 —— **编号体系混用**），应以 09-12:194 + 09-11:30 为准 |
| P1-32 ETF 三端点不降级 | 09-14「数据新鲜度/降级」修复只覆盖 screener/market，未覆盖 `etf` 的 `flow/list/hot` | 改动不完整（新增定位：`etf.py:288,299,319,477`） | 与父审核员全量套件的 3 例失败**精确对应** |
| P1-33 北向净流入含南向 | 既有台账无 | **本轮新增**（数据错误，840 vs 0，前端可见） | 最高性价比的数据修复之一 |
| **P1-39 `xsec_demean` 未落盘 ⇒ 门禁跨口径比 RMSE** | `docs/audit-2026-09-18/B4a-ml-features.md` 的 R14（门禁不校验 `feature_version`/`dataset_version`/验证区间可比性） | 已知（R14）→ **本次给出训练侧的具体触发路径与实测 1.25×** | **重要纠偏**：P1-16「自动重训候选恒被拒」的原因**不只是** `rank_ic_tolerance=0`，更是门禁在比较**目标函数不同的候选**（RMSE 0.0741 vs 0.0600）。只放宽阈值而不落盘口径，问题会以另一种形式复发 |
| P1-34 ST/停牌过滤静默失效 | `screening.py:85-88` **自述**「未校验榜单」是红线，`require_universe=True` 的守卫只加在部分路径 | 部分修（新增：默认值仍是 `False`） | 与 09-14 空榜裁定同一处代码 |
| P1-22 `useChart.ts` 首个 option 丢失 | 既有台账无 | **本轮新增**（B8 用 React 内部时序证明） | 影响所有使用共享 hook 的图表 |
| P1-24 前端 `AQP_ADMIN_TOKEN` 迁移成 admin | 既有台账无（但契约块明确禁止） | **本轮新增** | 与 P0-2（默认 token）叠加后风险放大 |
| P4 的 `ERR.FORBIDDEN` 零引用 / 英文直出 | 既有台账无 | **本轮新增** | — |
| §4.8 序 16 `Query` 当 `refresh` 击穿缓存 | 既有台账无（前端注释只提到"冷算 27~30s"这一现象） | **本轮新增（且是现象的根因）** | 一行修复，去掉系统性缓存击穿 |
| §4.7 C-2/C-3/C-4/C-5/C-6「不完整当完整」 | 既有台账无 | **本轮新增**（`rg "truncated\|failed_count" frontend/src` = 0 命中） | 见 P5 披露矩阵批次 |
| P0-1/P0-2 容器路径与默认凭据 | 既有台账无 | **本轮新增**（本沙箱无 docker daemon，静态链确证 + B0 批次深化） | — |

---

### 9.2 本轮推翻的历史判断（9 条 · **矛盾结论必须显式标注**）

> 这一节是简报第 7 节第 4 条要求的「标注矛盾结论」。**推翻意味着：先前审核明确写了结论、本轮实测证明不成立。** 这类条目比「新增缺陷」更值钱——它说明历史修复/复核本身有系统性盲点。

| # | 历史判断（原文位置） | 本轮证据 | 性质 |
|---|---|---|---|
| 1 | `docs/audit-2026-09-14/frontend-architecture-review.md:257` 点名 `utils/useChart.ts:12-18` 判「**✅ 正确，无泄漏**」 | 同一文件 `:12-20` deps 缺 `node` ⇒ **首个 option 永不 setOption**（P1-22，B8 用 react-dom 内部时序证明） | **正面结论被反转**（同类：以"无泄漏"为目标复核，漏掉了功能正确性） |
| 2 | `docs/audit-2026-09-14/backend-architecture-review.md:220` 判「**特征 asof 稳定性 ✅**」 | 该守卫只覆盖 `build_factors`(v1)；生产 v2g 的 `g1_*`（**20/60 特征**）对 universe 变化不稳定（去掉 1 只 ⇒ 40/42 列变），且守卫失败会**重写历史** `g1_*`（P1-18） | **结论覆盖范围被高估** |
| 3 | `docs/audit/AQP_前后端逻辑审查_20260912.md:173,246` 判模拟盘无持仓校验「**P2 边缘、UI 无法构造、影响低**」 | 提交 1 张 SELL 即造钱（红队全链路实测：卖 10 万元 → 成交 8400 股 → 权益 1,000,000 → **1,098,871.19**、持仓 `{}`），**UI 完全可构造** ⇒ 升 **P1**（P1-3） | **严重度被显著低估** |
| 4 | 同文 `:66`（F-14）对同一行（`broker.py:396-397` + `engine.py:369-371,490-491`）判「**分母滞后一日、量级小**」 | 真因是**非交易日沿用陈旧 `last_day_turnover`**（21 日仅 1 笔成交却扣 1882 元、Sharpe −3.55→−240）⇒ **根因与量级双推翻**（P1-1） | **根因判错 ⇒ 修复方向必然错** |
| 5 | `docs/audit-2026-09-15/qa-verification.md:305` 判「**错误码逐条对齐 ✅**」 | 只比对了**常量存在性、未验可达性** ⇒ 假阳性；`ERR_DATA_SOURCE=51000` 全仓**零抛出点**（仅 `core/errors.py:96` 定义） | **验证方法本身无效**（应改为"每个码必须有产生点"） |
| 6 | `docs/software-company/sprint3-code-summary.md:16,35` 声称「retrain 默认生产配方 `xsec_demean`…**修复后门禁四维全过**」 | `train_lgbm.py:224` 默认**仍为 False**，且 `rg xsec_demean backend/app/ml/registry.py` **零命中**（始终未落盘）⇒ 09-18 仍出现「候选恒被拒」（P1-16/P1-39）。**留待判定**：两侧触发路径不同，需用 DB 副本重放一个 retrain 候选才能定论 | **存疑（不直接判回归）** |
| 7 | 「P0-2 = 本轮新增」（本报告初稿 §9） | **已登记 4 次**（见 P0-2 的去重框） ⇒ 已更正 | 本报告自身错误，已改 |
| 8 | 「P1-24 = 本轮新增」（本报告初稿 §9） | **已登记 ≥4 次且带行号**（`docs/audit/AQP_前后端逻辑审查_20260912.md:64` 含 `useAuthStore.ts:90-99`、`docs/audit-2026-09-14/product-review.md:65` D-01 等） ⇒ 已更正 | 本报告自身错误，已改 |
| 9 | 本报告初稿 §9 引「09-15 轮次的 P2-14 断点续传落盘」 | 该引用**不可核验**：`docs/audit-2026-09-15/` 无此条；`P2-14` 在 09-14 是 `js.eval`、在 `AQP_P0P1P2复审报告.md:12,75` 是任务持久化 ⇒ **编号体系混用**；正确的"已修"引用是 `AQP_前后端逻辑审查_20260912.md:194` + `AQP_修复端到端验证_20260911.md:30` | 本报告引用错误，已改 |

**从这 9 条推出的流程改进（建议纳入 CI/审核规范）**：
1. **复核不能只验"有没有"**：第 1/2/5 条都是"检查了机制是否存在，却没验证它在目标场景下是否生效"⇒ 复核项必须写成**可执行断言**（如「自反式因子净值必须为 0」「每个错误码必须有产生点」）。
2. **修复必须配"修复不变式"用例**：第 6 条与 D25（断点续传被 `clear()` 抵消）同属"修复在位但被后续改动抹掉"⇒ 每个 P0/P1 修复都应带一条**防回归不变式**。
3. **同一代码位置的多次审核要有"已审行号台账"**：第 3/4 条是**同一行被审过两次、两次都判错**（`broker.py:380-397`、`paper.py:118-248`）⇒ 建议对高风险文件维护已审行号 + 结论，避免第 3 次以新根因重复审计。

### 9.3 红队证伪清单（**本报告自身结论被推翻的条目**，逐条已修正）

> 方法：红队（REDTEAM 批次）对 §2/§3 的前 12 条高危结论做**独立复现**，只接受「经真实 app 端到端走通」的证据。以下条目**已在正文同步修正**，此处集中登记以免读者按旧稿理解。

| # | 本报告初稿结论 | 红队实测 | 处置 |
|---|---|---|---|
| R-1 | **P1-8**「基准首值 0 ⇒ 该接口对该输入**恒返回 `code=50000`**」（依据：`JSONResponse` 直测抛 ValueError） | **推翻**：端点带 `response_model` ⇒ 走 **Pydantic v2**（`ser_json_inf_nan='null'`）⇒ **http=200 / code=0 / `benchmark: null`、无异常**；全站 114 路由仅 4 个无 `response_model` 且都不产 NaN ⇒「恒 50000」**不可达**。且触发条件也被否证：`portfolio.py:201` 的 `ffill().dropna()` 使 `benchmark.iloc[0]` 恒为**首个有效值**（`first=NaN` 实测 `code=0`），**只有字面 0 才触发** | **P1 → P2（披露类）**：真实缺陷是「基准异常时静默置 `null`、无 `basis` 披露」。**00-BASELINE §6 已加 6.1 修正节** |
| R-2 | **P0-6**「空榜 `ok` 与新契约冲突」（列为 P0） | **下调**：分支顺序正确且可达（`board=bse` → 快照 `None` → 实时 `total==0`）；**前端两种状态都区分渲染**（`Screener/index.tsx:213-214`、`AiPicksPanel.tsx:102-103`）并显示中文空态文案 ⇒ **用户可见行为无误**，属**纯契约冲突** | **P0 → P2**，移入 §10 裁决清单第 1 项 |
| R-3 | **P0-5**「`150^6 ≈ 1.1e13` ⇒ 单请求即可 OOM 打死**整站**」 | **触发条件与影响面双修正**：`150^6` **经 HTTP 不可达**（`backtest.py:535` 白名单只允许 2–3 个键）；但真实面更易达——**3 键 × 1000 候选 = 22.5 KB → 1e9 组合 → ≈72 GB**（实测 **72 B/组合**），且 nginx **未设 `client_max_body_size`**；compose `mem_limit: 2g` ⇒ OOM **只杀 `aqp-api` 容器**（`restart: unless-stopped` 会拉起）⇒ 准确表述是「打死 **API 服务**」，**非容器部署才整机 OOM** | **维持 P0**，正文已改写触发条件与影响面 |
| R-4 | **P0-4**「基准降级造 `close=1.0` ⇒ `annual_benchmark=0.0`，**与真实 0% 基准不可区分**」 | **部分推翻（下调）**：实测两条路径**间接可辨**——合成路径 `risk.alpha/beta/IR` **全为 `null`** 且基准曲线是**字面常量 1.0**；真实 0% 基准为 α=0.696 / β=−0.036 / **IR=5.32**。⇒ 「KPI 层不可辨」成立，「完全不可区分」不成立；**披露缺失仍是真实违约** | **P0 → P2**；描述改为「KPI 层不可辨 + 两处间接可辨，但响应无 `basis` 显式披露」 |
| R-5 | **P0-1** 首失败点写作 `mkdir('/data/parquet')` | **细节修正**：`import app.main` 即触发 4 次 `os.mkdir`，**首个撞墙的是 `LOG_DIR` 的父目录 `/backend`**；且**不存在旁路** —— 要绕过需**同时**覆盖 4 个 env **且**去掉 `read_only`/补卷挂载 | 维持 P0，正文已改 |
| R-6 | **P1-47**「所有 `volume<=0` 停牌闸门在真实面板上是**死条件**」（原标**确定**，依据：P2 探针统计"44,280 行中 44,278 行 `volume` 为 NULL、仅 2 行 `volume<=0`"） | **推翻（父审核员在修复阶段自查发现）**：只读实测生产面板 —— `universe_daily/year=2026`（431,462 行）**`is_halted` 与 `volume IS NULL` 交叉表完全对角**（3107 NULL 行全部 `is_halted=True`；428,355 非 NULL 行全部 `False`），`universe_daily_bt`（185,812 行）1125/1125 同样对角、**零例外**；且 `universe.py:243` 用 `volume_f = fill_null(0.0)` 计算该标记 ⇒ **停牌标注正确**。`broker._num(None, default=0.0)` 把 NULL 映射为 0.0 ⇒ `volume <= 0` **会命中**，与 `halted` 构成**冗余双保险**而非死条件；`paper.py:109-110` 的 NULL `amt` 被 `.filter(amt > 0)` 丢弃，行为亦正确 | **撤回「确定」；P1-47 降为「已核实正确 + 加固建议（疑似）」**。§7.9 T5 预期收益作废、§8.2b 插入项移除。**方法教训：探针的列口径（NULL vs 0）不能代替对"消费端归一化逻辑"的核对**——本轮错在只看面板 NULL 率，没看 `_num`/`fill_null` 这一层 |
| R-7 | **B5-08**「换手 = 买卖**双边累加** ⇒ `annual_turnover` 与 decay 成本 **2×**；全仓换标的实测 0.94（真实≈0.47）」（原标**确定**） | **推翻（修复阶段实测，2026-09-21）**：`broker.match` 全仓仅 **3 处**调用（`engine.py:169/188`、`strategy_base.py:439`），**没有任何一处传入买卖混合列表**；`engine.rebalance_to_weights` 是「先卖一次 `match`、再买一次 `match`」，而 `match` 每次都 **`self.last_day_turnover = …` 覆盖** ⇒ 最终留下的是**买入腿**，**本来就是单边**。实测三场景（`backend/.tmp_testrun/b508_turnover_verdict.py`，建仓日与调仓日分开）：全仓换标的 卖腿 0.9500 / 买腿 0.9483 / 两腿和 1.8983 ⇒ `last_day_turnover` = **0.9483（=买腿）**；仅加仓 0.4741（=买腿，两腿和 0.9491）；仅清仓 0.9500（=卖腿）。**0.9483 本就是全仓换标的的标准单边换手 Σ\|Δw\|/2 ≈ 0.9492**，报告所说"真实≈0.47"是把 (买+卖)/2 当成了全仓换标的的单边换手 | **撤回「确定」；B5-08 的"2×"定性不成立**。**但同处确有另一类真实缺陷**（本次已修）：取值取决于"最后一次 `match` 是哪条腿"，**仅清仓日**（卖腿成交、买腿被涨停拒）会拿到卖腿 0.9500 而标准单边仅 **0.4750** ⇒ **2× 多扣**；不对称调仓日则取到较小的一条腿。修法：当日至此**累加双腿**，取 `(买+卖)/2/权益` —— **方法教训：报告凭"合成回测实测 0.94"反推机制时，未核对 `match` 的调用形态（分两次调用 + 覆盖赋值），把一个"巧合正确"的值当成了错误值** |

**红队维持不变的 8 条**：P0-1 / P0-2 / P0-3 / P1-1 / P1-3 / P1-14 / P1-22 / P1-23。其中：
- **P1-1 红队独立复算到分**：单笔成交却扣 **17 天 = 16,027.77 元**（逐日累加手算完全吻合），单次应约 950 元 ⇒ **16× 重复扣**。与 B5 的「21 日窗口 1,882 元」是同缺陷的**不同窗口/费率参数**，两次独立实测互证「非交易日重复扣」成立。
- **P1-3 红队端到端实测**（内存 sqlite 全链路）：裸卖 **10 万元 → 成交 8400 股** → 权益 1,000,000 → **1,098,871.19**、持仓 `{}`；三层无校验。
- **P1-14 置信度标注修正**：红队手算 **+29.4392%**（本报告 +29.4% 正确，`B3b-data.md` 的 29.5% 是笔误）；`f55+f56≡f52` 精确成立 ⇒ `f52` 是净额**已确证**；但**原始响应无外网无法独立取数** ⇒ 「+29.4%」是重算值。
- **P1-22 影响面收窄**：空白仅限「条件挂载 + memo option」的 **5 处**，另 **8 处 inline option 正常**。
- **P1-23 两处修正**：删除「1.5s 轮询持续失败」（`sync` 恒 null，轮询不启动）；`desk` 调用点被 `RequireRole(researcher)` 挡住。

细节见 [REDTEAM-verify.md](REDTEAM-verify.md)。

**方法教训（已并入 §9.2 流程改进）**：**直测库函数 ≠ 端到端路径**。凡涉及框架层的结论必须经真实 app 走一遍，否则会从"某个库抛异常"错误推出"端点必崩"。

---

## 10. 待确认信息清单（需产品/运维答复）

1. ~~**空榜契约**：采纳 `unavailable`（改代码+改测试）还是保留 `ok + no_matching_signals`（改契约措辞）？~~ ✅ **已裁决并落地（2026-09-22，第 17 轮）**：**两个选项都不采纳**——按可判定事实拆开报。`total==0` 有两种成因，一刀切"`unavailable`"会把"真跑了但当日无匹配"（09-14 有意设计、前端正确渲染）误报成数据不可用；一刀切"保留 `ok`"则让 `board=bse` 这种**从未真正筛选**的请求谎报"当日无匹配信号"。实现：`filter_universe` 新增 `board_universe_rows`（请求板块在 `universe_daily` 快照里的成分股数，与当日有无 pred 信号无关）并随 `universe_filter.board_rows` 披露；`board != "all"` 且该值为 `0` ⇒ 终态 `unavailable/empty_board`（新增机器码，中文文案显式说明"并非当日无匹配信号"），其余 `total==0` ⇒ 保持 `ok/no_matching_signals`；取不到该值（无快照/旧 schema）⇒ **不臆断**，按原语义。测试：`tests/test_screener_empty_board_contract.py` 11 条（真值表 6 + `filter_universe` 计数本体 3 + 真实 `_screen` 端到端 1 + 接线锁 1，含**缺陷本体反证**：去掉新增字段即复现旧的 `ok/no_matching_signals`），既有 09-14 断言**未改写**、仍绿。
2. **部署目标**：生产是否使用 `docker-compose.yml`？若是，P0-1/P0-2 必须立刻修；若生产是原生 `uvicorn`，请确认 `.env` 与 `ENV=prod` 的落地方式。
3. ~~**`delist_date` 数据源**：东财退市接口在当前网络环境可用吗？~~ ✅ **已实测可用（2026-09-22，第 18 轮）**：只读探针取回 **361 条**退市记录 ⇒ 走 akshare 路线（无需 Tushare）。接线已完成（§8.2 第 6 项），**剩下的是部署侧跑一次流水线回填**，不是选型问题。
4. **`monthly_monotonic_ratio` 的历史用途**：`scripts/p2_experiment.py:86` 打印的数字是否已进入任何对外报告？（决定是否需更正历史结论）
5. **`features_v2.py` 的产品定位**：保留为实验线还是删除？（决定 §6.1 删除顺序）
6. **`multi_source.py` 三源冗余**：是否仍在路线图？（决定接线还是删）
7. **API Token / 会话撤销**：是否要求实现（登出即失效）？当前旧 JWT 7 天内仍有效是否为可接受风险？
8. **`watchlist` 写入入口**：前端「自选股」是只读展示吗？（若是，预警 `scope="watchlist"` 应下线）
9. **`bse` 池**：恒 0 是需求（已知），但 `MARK_UP["bse"]` 死分支与 `920xxx` 板块判定是否要一并清理？
10. ~~**CI mypy 门禁**：修掉这 10 个良性误报，还是降级为 `continue-on-error` + 增量 strict 白名单？~~ ✅ **已裁决并落地（2026-09-22，第 18 轮）**：**修掉而非降级**——`mypy app/` 由 17 错/4 文件 ⇒ **0**（129 文件全绿），并保持为**硬门禁**；同批把 `ruff` 范围从 `app/`(+3 规则) 扩到 `app tests scripts / F,E9`（否则 alerter 的 F821 与 tests/scripts 的 27 处永远看不见）。见 `FIXES-APPLIED` 行 54。
11. **前端单测框架**：是否引入 vitest？（当前前端零单测；**第 18 轮已把 nightly E2E 从"静默吞失败"改为 `continue-on-error`（失败可见但仍不阻塞）**，并以 `npx tsc --noEmit` + 32 条源码锚点断言作为过渡护栏——但这**不能替代运行时/DOM 级验证**，C-1（hooks 顺序）、C-8（状态颜色）等 UI 语义仍建议人工点检。）
12. **模型回滚策略**：允许「回滚到 IC 更差的版本」是否需要审批字段（`promoted_by`/`promote_reason` 已存在）？验证门槛是否改为 T8 的**多窗口滚动 + 符号一致性**？
13. ~~**`docs/项目开发文档.md` §13 部署契约**（6 个变量名不存在…）：**由谁负责重写？**~~ ✅ **已修（第 13 轮，`FIXES-APPLIED` 行 13）**：§13.2 变量名 / §13.4 Dockerfile / §13.5 compose / `docs/auth-register.md:38-39` 已全部改写，并在原文处标注「真名是 `ENV`（**不是** `APP_ENV`）；生产必须 prod，否则安全闸门失效」（本轮复核：`docs/项目开发文档.md:5562/5575/5950` 均已是正确口径）。**仍开放的是第 19 条**（其余章节的全量文档-代码对账）。
14. ~~**`README.md:213` 的备份 cron 推荐**：是否同意立刻改为 `backup_drill.py` / `POST /settings/db/backup`？~~ ✅ **已修（第 2 轮 P0-8，`FIXES-APPLIED` 行 2 区）**：`README.md` cron 推荐与 `scripts/backup.py::main()` 均已改（并补 `purge_legacy_models.py` 缺失的 `WHERE`），防回归用例 `tests/test_backup_safety.py`（4 条）。
15. **【产品级决策】日频调仓是否保留？** 实测 Top-50 毛超额 **+0.10%/5d（≈+5%/年）< 成本下限 8.2%/年** ⇒ 扣费后为负。若目标是"可实盘的策略"，需改为**周频/双周频调仓**并把调仓成本内生化到目标函数；这是本报告唯一一条**改变策略本身**而非修 bug 的建议。
16. **训练/验证窗口是否改滚动 3–4 年**（T8）？当前 `valid_rank_ic=0.1020` 来自 2024-07~2025-08 **单一段 regime**，是生产模型自 09-05 冻结的直接原因。
17. **`/data` 与 `/settings` 的角色**：是**提升为 researcher**，还是按角色分块渲染？（P1-23，16 处 viewer 必然 40300）
18. ~~**`universe_daily_bt` 是否纳入夜间流水线**（P1-42）？~~ ✅ **已结项（第 18 轮末实测复核）**：该面板**已重建且为当前态**——`data/parquet/universe_daily_bt/symbol=__all__/year={2022..2026}.snappy.parquet`，实测 **2,851,203 行 / 最大日期 2026-09-18**（与 §0 记载的 `1,283,107 → 2,851,203 行`、`2026-09-04 → 2026-09-18` 完全一致；同批 `universe_daily` 为 2,847,566 行 / 2026-09-17）。⚠️ **须知的伴随事实**：本次按用户指示清除 2022 年之前数据后，两套面板与日线**只覆盖 2022 年起**（`data/_purged_pre2022/` 留档，未删除）；因此**跨 2022 年之前的长周期回测已不可能**，既有长周期结论需标注数据范围。
19. **`docs/` 里其余"已过期即会误导"的章节**（B0 指出 §13 与 `docs/auth-register.md:38-39`）：是否要做一次**全量文档-代码对账**？（本轮已发现 6 个不存在变量 + 1 个必失败的 Dockerfile + TA-Lib 幻觉 + 1 个"compose 是生产方案"的虚假声称）
20. **多 worker 是否在路线图上**？当前 `--workers 1` 是隐性正确性前提（`write_partition` 无锁 P1-40、内存状态机遍布 `sync_service`/`task_store`）⇒ 若要扩展必须先做这些，否则会静默丢数据。
21. **status 取值域归一方案**（§4.13.3）：接受「`fresh` 作为 `ok` 别名过渡 + 新增 `partial`」，还是直接把 `data_freshness.status` 收敛为 `ok|degraded|unavailable`？当前**三套并存**（`ok|degraded|unavailable` / `fresh|degraded` / 仅二态）。
22. **披露字段是否强制前端消费**？P5 实测 **17 项两端都无 + 10 项后端有前端零消费**，其中 `quality-scan` 的"200 只"与 `instruments` 的 `total` 会**直接误导用户对数据覆盖面的判断**。是否加 CI 检查（「声明的披露字段必须有消费点或显式标注 API-only」）？
23. **15 个裸错误码**（`4002/5000/4003/5001` + `40010~40017`）：是在 `errors.py`/`types/api.ts` 双向登记保留现值，还是按 §4.13.3 做映射（`4002→40900`、`4003→51001`、`5001→51000`）？前端对未知码是否统一追加「（未登记错误码 N）」？

---

## 11. 批次索引与证据文件

| 批次 | 范围 | 报告 | 规模 |
|---|---|---|---|
| 基线 | 环境/套件/静态检查/路由内省/序列化/数据面 | [00-BASELINE-AND-VERIFICATION.md](00-BASELINE-AND-VERIFICATION.md) | 父审核员自测 |
| B1 | 基础设施（core/db/cache/logging/metrics/config） | [B1-infra.md](B1-infra.md) | 4×P2 + 6×P3 |
| B2 | 领域纯函数（domain/*） | [B2-domain.md](B2-domain.md) | 6×P1 + 12×P2 + 9×P3 |
| B3a | 数据存储/面板/日历 | [B3a-storage.md](B3a-storage.md) | 2×P1 + 9×P2 + 12×P3（含负结论：panels.py 无 P0–P2） |
| B3b | 数据采集/质量/选股 | [B3b-data.md](B3b-data.md) | 3×P1 + 12×P2 + 5×P3 |
| B4a | ML 特征与推理 | [B4a-ml-features.md](B4a-ml-features.md) | 1×P1 + 13×P2 + 7×P3 |
| B4b | ML 训练与模型（含负结论：purge 纪律正确） | [B4b-ml-train.md](B4b-ml-train.md) | 3×P1（+1 升 P0）+ 8×P2 + 8×P3 |
| B5 | 回测与交易 | [B5-backtest.md](B5-backtest.md) | 6×P1 + 8×P2 + 10×P3 |
| B6 | 编排/服务/定时任务 | [B6-services.md](B6-services.md) | 4×P1 + 5×P2 + 6×P3 + 1 疑似 |
| B7a | API 读端点（9 文件全读 + 29 次真打端点） | [B7a-api1.md](B7a-api1.md) | 5×P1 + 6×P2 + 9×P3 + 4 有意设计 |
| B7b | API 写端点/运维/SSE/导出 | [B7b-api2.md](B7b-api2.md) | 1×P0 + 6×P2 + 7×P3 |
| B8 | 前端基础设施（+ 路由×角色对账） | [B8-frontend-infra.md](B8-frontend-infra.md) | 5×P1 + 5×P2 + 5×P3 |
| B9a | 前端行情/个股页 | [B9a-frontend-market.md](B9a-frontend-market.md) | 2×P1 + 6×P2 级 |
| B9b | 前端回测/组合/研究页 | [B9b-frontend-quant.md](B9b-frontend-quant.md) | 3×P1 + 11×P2 + 4×P3 |
| B9c | 前端数据中心/运维/设置页 | [B9c-frontend-ops.md](B9c-frontend-ops.md) | 13×P2 + 9×P3 |
| P4 | 前后端契约对账（114 端点 × 111 调用点，全量） | [P4-contract.md](P4-contract.md) | 6×P1 + 8×P2 + 5×P3 |
| B0 | 部署与运行链路（容器/原生/`.env` 契约；`docker-compose config` 实机解析） | [B0-deploy.md](B0-deploy.md) | 5 个 P0 失败机制 + 9×P1 + 11×P2 + 7×P3 |
| P2 | 策略专项（目标 C：A/B/C/D 四组 + 6 组只读实证） | [P2-strategy.md](P2-strategy.md) | 8 条建议 T1–T8 + 对 S1–S8 的逐条裁定 + 9 项待补指标 |
| P5 | 披露字段端到端矩阵（21 个带 `limit` 读端点全量实测；testclient 不进入 `with` ⇒ 零后台写入） | [P5-disclosure-matrix.md](P5-disclosure-matrix.md) | 17 项「两端都无」+ 10 项「后端有前端零消费」+ 统一契约建议 + **附带发现 P1-50** |
| REDTEAM | 红队证伪复核（P0/P1 前 12 条，只接受端到端证据） | [REDTEAM-verify.md](REDTEAM-verify.md)（完整报告） | **推翻 1（P1-8）+ 下调 1（P0-6）+ 修正触发条件 1（P0-5）+ 维持 9**；已在 §9.3 登记 |
| P3-dedup | 既有台账去重对账（26 条逐条对账 + 9 条历史判断推翻） | [P3-prior-ledger-dedup.md](P3-prior-ledger-dedup.md) | 新增 16 / 已登记未修 8 / **已修但回归 2** |

工具与证据：
- 死代码扫描器：[tools/deadcode_scan.py](tools/deadcode_scan.py) → `deadcode-candidates.json`
- 子审核员共享简报：[AUDIT-BRIEF.md](AUDIT-BRIEF.md)
- 测试补丁（仅测试用，非项目源码）：`backend/.tmp_testrun/audit_mkdtemp_fix.py`（强制 `os.mkdir(mode=0o777)` 与 loguru `enqueue=False`；全量套件在此补丁下跑出 **1065 passed / 6 failed / 8 skipped / 3 deselected**）
- P5 披露矩阵证据（同目录）：`p5_probe_out.txt`（#1 21 个读端点的 `limit=1`/`limit=-1` keys 快照）、`p5_probe2_out.txt`（#2 `alerts._check_data_health` 100 FAILED + 25 RUNNING）、`p5_probe3_out.txt`（#3 `quality-scan` 250 分区 + `_scan_dataset` 1 好 1 坏）
- B3a 存储探针：`backend/.tmp_b3a/probe1..9.py` + `.out.txt`（生产 `data/parquet`、`aqp.db` 全程只读）

### 11.1 临时目录与清理状态（**须在合并前处理**）

| 路径 | 内容 | 大小 | 处置建议 |
|---|---|---|---|
| `backend/.tmp_testrun/` | 133 个文件：pytest 补丁插件 + `b6_probe_s1..s8.py` / `b7a_probe1..9.py` 及其日志/JSON | 12.34 MB | **保留至修复完成**（B6/B7a 的「最小验证方法」直接引用这些探针）；合并前可整体删除，不影响 `docs/` 证据（报告内已内嵌关键输出） |
| `backend/.tmp_b2/` | 14 个文件：`b2_probe1..14.py` | 0.06 MB | 同上（B2 领域纯函数探针） |
| `backend/.tmp_b3a/` | 18 个文件：`probe1..9.py` + `.out.txt` | 0.06 MB | 同上（B3a 存储探针；**生产 `data/parquet`、`aqp.db` 全程只读，`.manifest.json` 与 11 个目录未变动**） |
| `%TEMP%\aqp_p2\` | P2 策略专项 6 组探针（仓库外） | — | 仓库外，无需清理 |
| `backend/.tmp_b3a/p1_tlipqpuz/`、`t_2vj_qarr/` | **两个空目录，创建时继承了受限 ACL** | 0 B | **已知残留**：父审核员实测 `Remove-Item` / `takeown` / `icacls /grant` **三者全部 Access denied**（token 无 WRITE_DAC，非本仓库代码问题）⇒ 需管理员权限或重启后由 Explorer 删除。**无内容、非数据** |
| `qa_browser/`（仓库根） | 16 张 PNG / 5.52 MB | 5.52 MB | **未被 `.gitignore` 覆盖**；属审查过程产物，建议删除或加入 `.gitignore` |
| `data/_purged_pre2022/`（**本轮新增**） | 2022 前分区隔离副本：3,417 文件 / 2,584,222 行（6 个数据集） | 40.9 MB | **按用户裁决移入隔离而非物理删除**；数据侧功能等价于删除，确认无误后可整体删除；需回滚则 `python scripts/purge_pre2022.py --restore`。**该目录不在 `DATA_ROOT` 之内**，不会被任何盘点/构建器误当数据集 |
| `data/_backup_universe_daily_bt_20260921/`（**本轮新增**） | 重建前的 `universe_daily_bt` 面板（冻结版：1,133 只 / 2026-09-04） | 38.1 MB | 重建前的**原样备份**，用于对照/回退；确认新面板无误后可删除 |

**注意**：`.tmp_*` 与 `qa_browser/` 均为**未跟踪**文件，`git status` 会显示为 untracked；本条仅为合并前的整洁性义务，**不影响任何审核结论**。`data/` 下的两个新增目录同样被 `.gitignore` 覆盖（不会进入版本控制）。