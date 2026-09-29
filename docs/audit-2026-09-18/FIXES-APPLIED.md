# 修复记录（Fix Log）· 2026-09-21

对应审核报告：[AQP-全栈审核报告.md](AQP-全栈审核报告.md)。本文件按**已实际修改并验证**的顺序记录，
每条都含：改动位置、改法、**防回归用例**、以及修复前后对照（凡标"实测"均有命令与真实输出）。

原则（与简报一致）：
- 每条修复必须带**可执行的防回归断言**，而不是"改完看一眼"；
- 凡涉及框架层/历史行为的判断，必须与 `git show HEAD:<file>` 的原始版本做**真前后对照**；
- 不修改任何未在报告中定位到文件+行号的行为。

---

## 已修复（按提交批次）

| # | 报告编号 | 位置 | 改法摘要 | 防回归用例 | 状态 |
|---|---|---|---|---|---|
| 1 | **P0-5** 网格寻优 OOM | `app/backtest/param_search.py:80-90` | 先 `math.prod(len(...))` 判上限，**再**用 `itertools.product` 惰性迭代（原来是 `list(itertools.product(...))` 先物化后守卫） | 全量套件内既有 backtest 用例 + 手测 1e9 组合 | ✅ 已验证 |
| 2 | **P0-3** `train/readiness` 无鉴权 | `app/api/v1/datacenter.py:1124-1127` | 补 `_user: dict = Depends(require_role("viewer"))` | `tests/test_read_endpoints_rbac.py::test_no_undeclared_unauthenticated_get_route` | ✅ 已验证 |
| 3 | **P1-50** `/market/index/kline` 无鉴权 | `app/api/v1/market.py:606-612` | 同上补 `require_role("viewer")`（前端调用点在 `pages/Screener` 登录后，`Promise.allSettled` 已容错） | 同上 | ✅ 已验证 |
| 4 | **R5 缺口 1**（漏挂鉴权能连过三轮审核的根因） | `tests/test_read_endpoints_rbac.py:189-247` | 新增 `DECLARED_PUBLIC` 显式公开白名单 + `test_no_undeclared_unauthenticated_get_route`：**任何无角色约束的 `/api/v1` GET 必须登记为公开**，否则失败；并反向校验白名单端点仍存在 | 本身即断言；另用运行时摘除依赖做过精确捕获验证 | ✅ 已验证 |
| 5 | **P1-32 / R9** 外部源故障逃逸成 `50000` | `app/core/errors.py:114-131`、`app/data/realtime.py:139,151` | 新增 `DataSourceUnavailable(AQPException, RuntimeError)`（同时兼容历史 `except RuntimeError` 降级点），在 `_request`/`_first_source` 抛出 ⇒ 统一映射 `ERR_DATA_SOURCE=51000`，并给该码**第一个抛出点** | `tests/test_data_source_error_contract.py`（3 条） | ✅ 已验证 |
| 6 | **P0-7** 因子分层/多空净值**前视偏差** | `app/ml/gp_miner.py:258-269`（`evaluate_expr_detail`）、`:331-334`（`factor_report`） | `daily = close_w.pct_change()` → **`close_w.shift(-1)/close_w - 1`**（d 日收盘建仓、赚 d→d+1），并新增 `nav_basis`/`nav_kind`、`quintile_basis`/`quintile_kind` 口径披露 | `tests/test_gp_miner_no_lookahead.py`（4 条，含鉴别哨兵） | ✅ 已验证 |
| 7 | **P0-8** 备份脚本删生产库 | `scripts/backup.py`（重写 `main`/`create_backup`/`restore_backup`）、`scripts/restore.py:14-16` | ① 演练改到**临时目录**；② SQLite 用 `VACUUM INTO` 一致性快照（含 WAL 已提交数据）、归档剔除 `-wal`/`-shm`；③ 恢复前把现有数据改名为 `.pre-restore-<stamp>`，解压失败**回滚**；④ 拒绝归档绝对路径/`..` 穿越 | `tests/test_backup_safety.py`（4 条） | ✅ 已验证 |
| 8 | **死代码（F841）** | `app/ml/gp_miner.py:64` | 删除从未使用的 `head = expr.split("(", 1)[0]`（`app` 的 F841 由 5 条降至 **4 条**） | `ruff check app --select F841` | ✅ 已验证 |
| 9 | **R11 文档与实现脱节** | `README.md:205-224` | 备份章节补"安全属性"四条：一致性快照、演练隔离、恢复回滚、cron 可在服务运行时执行 | 人工核对 + `scripts/backup_drill.py` 仍 PASS | ✅ 已验证 |
| 10 | **P1-47 撤回**（审核报告自身的错误结论） | `AQP-全栈审核报告.md`（P1-47 行、§7.9 T5、§8.2b、§9.3 新增 R-6、§1.1 计数） | 实测证明「`volume<=0` 停牌闸门是死条件」**不成立** ⇒ 撤销「确定」定性，降为「已核实正确 + 加固建议」 | 只读面板交叉表实测（见下） | ✅ 已更正 |
| 11 | **P0-1 容器路径**（5 个失败机制） | `backend/app/core/config.py`（`_resolve_backend_root` / `_resolve_project_root` / `BACKEND_ROOT` / `LOG_DIR` / `_ensure_dir`）、`backend/app/core/logging.py`（`delay=True` + 降级）、新增 `backend/tests/test_project_root_resolution.py` | `PROJECT_ROOT` 改为「env 覆盖 > 仓库标记 > 后端根」推断；`LOG_DIR` 改为 `BACKEND_ROOT/"logs"`（容器 → `/app/logs`，对上 compose 卷）；mkdir 失败抛**点名配置项**的错误；日志文件 sink 失败降级而非"零 sink 崩溃" | 6 条新用例 + 伪容器布局前后对照（见下） | ✅ 已验证 |
| 12 | **P0-2 凭据姿态** | `docker-compose.yml`（`ENV=prod` + `${ADMIN_TOKEN:?}` / `${JWT_SECRET:?}` + `ALLOW_*=false` + `CORS_ORIGINS`）、`backend/app/core/config.py`（dev 高危暴露告警）、新增 `backend/tests/test_runtime_safety.py` | compose 此前**从不设 `ENV`** ⇒ prod 闸门永不执行。现改为解析期必填 + prod 生效；dev 下把"默认凭据 + 非回环监听"单列为高危并给出两条处置 | 13 条新用例 + compose 双向验证（见下） | ✅ 已验证 |
| 13 | **P1-44 部署文档幻觉** | `docs/项目开发文档.md`（§13.2 变量名、§13.4 Dockerfile、§13.5 compose、D4/D11/A1/DC3/SC1 检查项、S1.4）、`docs/auth-register.md:38-39` | 6 个不存在的变量名（`APP_ENV`→`ENV`、`APP_PORT`→`API_PORT`、`DATA_DIR`/`DATABASE_URL`/`REDIS_URL`/`APP_API_KEY` 删除并给真名）+ 必失败 Dockerfile（`router:app`→`app.main:app`、去掉未安装的 `uvloop`、COPY 前缀）+ 不存在的 `/api/health`→`/health` | `docker-compose config` + 端点实存核对 | ✅ 已更正 |
| 14 | **P1-39 训练口径未落盘** | `backend/app/ml/train_lgbm.py`（新增 `train_basis`，同时落 metrics.json 与 params.json，并补 `kept_features`）、`backend/app/ml/registry.py`（`params_json.basis` + `training_basis()` + `basis_mismatch_reason()` + 门禁只在同口径下比 RMSE）、新增 `backend/tests/test_promote_basis_comparability.py` | 门禁此前跨口径比 RMSE（同 split/dataset、只差 `xsec_demean`，0.07411 vs 0.05998 = 1.24×≫5%）⇒ 更优候选恒被拒（P1-16 的真实根因）。现在口径不一致就**跳过 RMSE** 并记 `basis_mismatch`，RankIC/ICIR 照常把关 | 13 条新用例 + **真实产物复算**（见下） | ✅ 已验证 |
| 15 | **P1-28 续传被启动抹掉** | `backend/app/services/sync_service.py`（抽出 `_start_sync_reset`：仅非续传启动才 `clear()`；新增 `completed_mode` 归属 + `_resume_skip_set()` 防跨 mode 误跳过）、`backend/app/api/v1/datacenter.py`（登记归属 mode）、新增 `backend/tests/test_sync_resume_not_wiped.py` | lifespan 恢复的断点被 auto-sync 每 60s 的启动无条件清空 ⇒ P2-14 跨重启续传从未生效（"修好又被抹掉"）。同时修掉 `completed` 不分 mode 的扁平集合导致 incremental/repair/rebuild 互相误跳过 | 12 条新用例（含源码级接线断言） | ✅ 已验证 |
| 16 | **P1-40 写分区无锁（丢行）** | `backend/app/data/parquet_store.py`（新增 `_path_lock` / `_rw_lock`：进程内分片锁 + 跨进程 `O_EXCL` 锁文件，含陈旧接管与超时显式报错；`write_partition`、`write_year_batch` 纳入互斥）、新增 `backend/tests/test_parquet_write_lock.py` | 「读-改-写」无互斥 ⇒ 后写者覆盖先写者新增行。真实对手**跨进程**（夜间 `build_universe` vs CLI），而 `pipeline_slot` 自述进程级、跨进程无效 ⇒ 必须文件锁 | 9 条新用例 + **前后对照实测（见下）** | ✅ 已验证 |
| 17 | **P1-41 `_audited` 一次性自愈** | `backend/app/data/parquet_store.py`（`_audited: set` → `dict[dataset,(已核对目录数, 时刻)]`，判据改为"增长驱动 + 30s 节流"）、`backend/tests/test_manifest_self_heal.py`（**推翻旧契约**并新增 3 条） | 首次旁路写自愈后，后续新增 symbol **永久静默缺失且不再 WARNING**。现改为目录数增长即再自愈；目录数不变（合法空目录）不重扫，防逐只扩容时反复全扫 footer | 5 条用例（含节流只推迟不取消） | ✅ 已验证 |
| 18 | **P1-1 换手衰减被反复扣** | `backend/app/backtest/broker.py`（新增 `begin_day()` 逐日复位 + `match()` 内**日期守卫** + 双腿累加账本）、`backend/app/backtest/engine.py`（主循环与分组循环各加 `begin_day()`）、新增 `backend/tests/test_turnover_and_decay.py` | 非调仓日 `match()` 不被调用 ⇒ `last_day_turnover` 沿用上一次调仓值，而 engine **每天都扣一次** decay。实测周频 21 天：ORIG 把同一周五的成本连扣 **5 次**（共 17 次），权益 −1.71% vs 修复后 −0.67%，Sharpe **−22.86 vs −7.29** | 9 条新用例 + 引擎层前后对照（见下） | ✅ 已验证 |
| 19 | **B5-08 结论撤回 + 同处真实缺陷修复** | `backend/app/backtest/broker.py`（`match()` 改为当日至此**累加**双腿并取 `(买+卖)/2`）、`AQP-全栈审核报告.md`（B5-08 行降级并注明实测） | 报告称"换手=双边累加 ⇒ 2×，实测 0.94、真实≈0.47"——**实测推翻**：`match` 仅 3 处调用、从不传买卖混合列表，且每次**覆盖**，原值本就是单边（全仓换标的 0.9483 = 买腿）。**但同处确有另一类缺陷**：取值取决于"最后一次 match 是哪条腿"，仅清仓日会拿到卖腿 0.95 而标准单边只有 0.475，即 **2× 多扣** | 9 条用例（含"仅卖腿"场景与 ≤1.0 上界） | ✅ 已更正 |
| 20 | **B5-10 费率与品种判定单一来源** | `backend/app/domain/a_share_rules.py`（新增 `ETF_PREFIXES`/`is_etf_symbol`/法定分段 `stamp_duty_rate`/`transfer_fee_rate`/`effective_stamp_duty`/`effective_transfer_fee`）、`broker.py`（卖出按品种豁免+分段+过户费；**买入补过户费**）、`ma_cross.py`、`trading/paper.py`、`api/v1/watchlist.py`、`engine.py`/`strategy_base.py`（`stamp_duty` 默认 `None`=法定分段）、`tests/test_backtest.py`（3 处断言随口径更新）、新增 `backend/tests/test_trading_fee_rules.py` | ① ETF 判定有**四套**且互不一致（`watchlist` 前缀表 `[:4]` 漏 `16x`，且 `and/or` 优先级使 `"159abc"` 被判为基金；**broker 根本不判品种**，对全部 ETF 照收印花税）；② 印花税恒 0.5‰ 而 2023-08-28 前法定 1‰ ⇒ 覆盖 2016 年起的行情**低估一半**；③ 过户费完全未建模（双边 0.01‰） | 22 条新用例 + 四套判定前后对照（见下） | ✅ 已验证 |
| 21 | **B2-8 涨跌停价少 1 分** | `backend/app/data/universe.py::_round_half_up_2`（`(x*100+0.5).floor()/100` → `(x*100+0.5+1e-9).floor()/100`，docstring 给出 epsilon 的**安全界推导**）、新增 `backend/tests/test_limit_price_rounding.py` | 浮点表示使 `v*100` 常落在略小于精确值处（`1.265*100 = 126.49999999999999`）⇒ `floor` 得 1.26 而交易所口径 1.27，**少 1 分**。实测 160 万样本（含 12 个密集半分边界）与 `Decimal ROUND_HALF_UP` 参照不一致 **0.826%**（limit_down **1.468%**、limit_up 0.184%）；修法 **0 不一致** | 27 条新用例（参数化边界 + 宽样本零不一致 + 与 `domain/limit._twop` 逐例一致） | ✅ 已验证 |
| 22 | **B5-17 策略路径 decay 静默失效** | `backend/app/backtest/strategy_base.py`（日循环加 `broker.begin_day()`；调仓段落后按当日单边换手 `apply_decay_cost`，与 `run_backtest` 同口径）、`tests/test_turnover_and_decay.py`（+4 条） | `run_strategy` 构造 `BrokerConfig(slippage_bps=…, enabled=True)`（`decay_bps` 取默认 10.0）却**从不调用** `apply_decay_cost` ⇒ 配置宣称的成本从未入账。实测：ORIG 全程 **0.00 元 / 0 天**，FIXED **13,174.61 元 / 62 天**（synthetic ma_cross 90 天） | 4 条新用例（真扣费 + 公式核对 + 非换手日不扣 + 费率可变 + `enabled=False` 不扣） | ✅ 已验证 |
| 23 | **换手口径的二次更正（我自己的 docstring 曾有错）** | `backend/app/backtest/broker.py`（`match` docstring 与 `last_day_turnover` 注释：删掉"仅清仓 Σ\|Δw\|=0.95 ⇒ 单边 0.475"的错误推导，改为两口径对照表 + `max(B,S)/E` 恒等式）、`tests/test_turnover_and_decay.py`（模块 docstring 同步更正 + 新增关系用例） | 我先前的注释把「现金算持仓」口径下的**现金腿漏掉**了：仅清仓时 Σ\|Δw\| = 0.95（股票）+ 0.95（现金）= 1.90 ⇒ 单边 **0.95**，**不是** 0.475。代码实现的是**买卖均值**口径 `(B+S)/2/E`，**不是**它声称的 Σ\|Δw\|/2。两口径在现金平衡日恒等、在单腿日为 2× 关系 | 1 条新用例钉住两口径关系（换标的相等、加仓/清仓 2×） | ✅ 已更正 |
| 24 | **B5-16 不可撮合订单被静默丢弃** | `backend/app/backtest/broker.py`（`match()` 的两处 `continue` 改为补 `reason="no_bar"` / `"no_cash"` 的 qty=0 记录；`price` 记 NaN 并说明理由；模块 docstring 列全拒绝原因）、新增 `backend/tests/test_rejected_orders_observable.py` | 不在 `uni_d` 的订单、以及未带 `cash` 的买单被**静默丢弃**（既无 Trade 也无 reason），与本模块开头"被拒绝的订单以 qty=0 的 Trade 记录返回，保证可观测性"的承诺直接矛盾；`/backtest/run` 的 `rejected_trades` 汇总（`backtest.py:164-167`）看不到这类订单，调用方无法回答"为什么某些目标当天没建仓" | 6 条新用例（买卖双向 + no_cash + price=NaN + 混合批不影响正常成交 + 不计入换手） | ✅ 已验证 |
| 25 | **P1-4 退市接线：披露不实 + 损失不可见** | `backend/app/api/v1/backtest.py`（新增 `_delist_coverage()` 只读实测 + `_universe_note()` 数据驱动文案，替换两处恒定 note；`universe_scope.delist_coverage` 结构化字段；`delisted_liquidations` 计数）、`backend/app/backtest/broker.py`（`friction_costs` 增 `delist_loss` 键并在 `liquidate()` 记账）、新增 `backend/tests/test_delist_path_disclosure.py` | ① **披露不实**：note 恒定宣称"含退市证券…直至其 delist_date；delist_date 之后的日期已从宇宙剔除"，而实测 `instrument.delist_date` 非空 **0/5552**、且**两套面板都没有 `delist_date` 列** ⇒ 该声称**结构上不可能为真**；② **折价损失完全不可见**（不进 `friction_costs`、无任何披露字段，用户无从知道 haircut 吃掉多少钱）。**⚠️ 同时更正报告措辞**：「三条路径全部空转」不准确——强平/haircut 由**面板缺席**驱动、与 `delist_date` 无关，**实测可达**（引擎级用例通过） | 9 条新用例（强平可达性 + 损失记账 + haircut 单调性 + 三种披露状态 + 查询不抛异常） | ✅ 已验证 |
| 26 | **P1-25 分层多空/IC 衰减的口径错误 + 前端图表恒空** | `backend/app/ml/signal_analysis.py`（新增 `overlapping_t_stat()`；年化改 `252/h`；t 值重叠校正；补 `long_short_nav` 序列与 `n_independent`/四个 `*_basis` 披露；修正失真 docstring）、`frontend/src/api/backtest.ts`（**修正写错的 TS 契约**）、`frontend/src/pages/Backtest/SignalAnalysisPanel.tsx`（改用真实键 + 展示此前被丢弃的 `monotonic`/`ls_t_stat`/有效样本量）、新增 `backend/tests/test_signal_analysis_caliber.py` | ① **年化放大 h 倍**：`ls_daily` 是 **h 日**远期收益，`ann = mean×252` 把它当日收益（调用方传 `max(horizons)=20`）⇒ 实测 h=20：ORIG **+6031.4%** vs FIXED **+301.6%**；② **t 值重叠高估 ≈√h**：h 日窗口相邻重叠 h−1 天，`sqrt(n)` 当独立样本 ⇒ 实测 h=20：ORIG **107.39** vs FIXED **24.01**（**4.47× = √20**）、h=1 完全一致（19.8659）；同一缺陷也在 `ic_decay_report`；③ **前端契约漂移**：TS 接口把**不存在的** `spread_annualized` 声明成必需字段、而真实键 `long_short_nav` 当时也不存在 ⇒ 类型检查通过、运行时静默 `undefined`，**图表恒空、年化永不显示**（根因在 TS 声明本身）；④ docstring 声称返回 `long_short_daily`/`ls_mean`，二者都未返回；⑤ 后端已算好的 `monotonic`/`ls_t_stat` 前端直接丢弃 | 16 条新用例（含 h 参数化年化/t 值、`n_eff`、nav 序列、`monotonic` 正反向、键集合契约、**跨层 TS 契约漂移守护**） | ✅ 已验证 |
| 27 | **P1-26 TopK 页三项指标小 100 倍且回撤显示为正号** | `frontend/src/pages/Backtest/TopKPanel.tsx:96-99` | `metrics` 来自 `domain/metrics.py:221-231` 的 `all_metrics`：`annual_return`/`win_rate` 是**小数**、`max_drawdown` 是**正幅值**；而 `utils/format.ts:10-14` 的 `fmtPct` 按**百分数**格式化 ⇒ 年化 `0.1234` 显示成「+0.12%」（应 +12.34%）、胜率 `0.55`→「+0.55%」（应 +55.00%）、最大回撤 `0.27`→「**+0.27%**」（应 −27.00%，**还带正号**，涨跌色语义也反）。同项目 `Portfolio/index.tsx:145-146,418-419` 与 `resultParts.tsx:28,275` 均有 ×100 ⇒ 单点漏乘 | 与兄弟页对齐（`×100`；回撤按 `resultParts.tsx:28` 显式加负号）；`tsc --noEmit` = 0 | ✅ 已验证 |
| 28 | **B5-20 逐笔 pnl 只减卖出费用 ⇒ 胜率/盈亏比偏乐观** | `backend/app/backtest/strategy_base.py:410-411`、`backend/app/backtest/ma_cross.py:214-215`（成本基准加入买入费用）、新增 `backend/tests/test_pnl_cost_basis.py` | 两处移动平均成本都**把买入费用排除在基准之外**，而卖出侧 `pnl = (price − base) × qty − 卖出cost` ⇒ **只减卖出腿**、往返成本少算买入腿 ⇒ `win_rate`/`avg_pnl_ratio` **系统性偏乐观**（把"毛赚、净亏"的往返记成盈利），且与 `nav`/`sharpe`（已扣全部费用）口径打架。**实测**（制造成交：买 9200@10.300 费 29.38、卖 9200@10.310 费 76.83）：ORIG pnl **+15.17 ⇒ 记盈利**（`win_rate` 100%）vs FIXED **−14.21 ⇒ 记亏损**（`win_rate` 0%），恒等式 `FIXED = ORIG − 买入fee` 精确成立 | 7 条新用例（双边净额恒等式、毛赚净亏必记亏损、`win_rate` 不为 1.0、原引擎同判据、由 pnl 反推基准含单位费用、与权益变化同向） | ✅ 已验证 |
| 29 | **P1-29 晚间例行：流水线 FAILED 仍记 `done`** | `backend/app/jobs/evening_routine.py`（`pipeline_failed` 判定 + `_mark("failed")` 终态 + 有界重试 `_MAX_ATTEMPTS=3`/`_RETRY_BACKOFF_SECONDS=900` + `attempts` 计数 + 模块 docstring 约束更新）、新增 `backend/tests/test_pipeline_state_truth.py` | 整条流水线 FAILED（或 `PipelineBusy` 撞车抛异常）时，原实现只发一条 SSE、把 `"FAILED/step"` 塞进 `detail` 字符串，随后 `:110` **无条件** `_mark("done", detail)` ⇒ 终态**没有 `failed`**、幂等标记（只认 done/skipped）使**当日不再重试** ⇒ **当晚榜单停更而日报照常生成**。修法：如实记 `failed`；同时**必须有界**，否则调度器每 60s 重跑整条流水线（单次 ≈60s） | 12 条新用例（失败记 failed、异常也 failed、成功仍 done、仅日报失败仍 done、重试→用尽上限、跨日归零、退避窗口、done/skipped 终态） | ✅ 已验证 |
| 30 | **P1-30 外部源部分降级被当健康** | `backend/app/services/market_service.py`（三态判定按**子源个数**：3/3=`ok`、1~2/3=`degraded`+`reason`+`n_ok`、0/3=`unavailable`）、`backend/app/api/v1/market.py`（重复两遍的 degraded 判据抽成单一 `_is_degraded()`）、新增 `backend/tests/test_pipeline_state_truth.py` | 三个子块（北向/主力/行业结构）**只成功一个**仍返回 `status="ok"` 且**不带 `reason`**；而消费方判据是 `status ∈ (degraded, unavailable)` ⇒ `_build_rt`/`_build_daily` 的 `data_freshness` 报 `fresh`，**三态契约退化为两态**（审核中该缺陷的唯一实例）。这是"外部源降级被当健康"的实质 | 10 条新用例（3 种全成功/全失败 + 6 种部分组合的参数化矩阵 + 消费方判据端到端） | ✅ 已验证 |
| 31 | **P1-31 `/sync/fetch` TOCTOU：谎报已启动且不留案底** | `backend/app/core/pipeline_lock.py`（新增 `acquire_pipeline_slot()` + `PipelineSlotHandle`，`pipeline_slot()` 改为其薄封装、语义不变）、`backend/app/api/v1/datacenter.py`（请求线程**真正持有**锁再交接给 worker；`_run_fetch(slot=...)` 不再二次申请；早退分支归还锁；相关 import 提到模块级）、新增 `backend/tests/test_pipeline_state_truth.py` | 原实现「预检取锁 `:941` → 立即释放 → worker 二次取锁 `:889`」，两个申请点之间的窗口内锁一旦被 sync/pipeline/mirror/training 抢走 ⇒ 接口已回 `started:true`，worker 一进 `_run_fetch` 即 `PipelineBusy`：任务**零执行**、`data_jobs` **零行**、且 `_sync.error` 会在下一个请求被 `:957` 清零 ⇒ **静默落空且不留案底**（同状态机另一入口却如实记 FAILED） | 9 条新用例（句柄可跨线程交接、重复释放幂等、`with` 语义、无 slot 时旧语义保持、**接口锁忙必须回业务码而非 started**、`_sync.running` 早退必须归还锁、交接期间互斥仍有效） | ✅ 已验证 |
| 32 | **P1-42 回测数据集未接线（夜间养的是另一份数据）** | `backend/app/orchestrator.py`（新增 `step_build_universe_bt` + 并入 `FULL_STEPS`/`STEP_FUNCTIONS` + 模块 docstring 顺序 + **无 hfq 时显式跳过**）、`backend/app/data/universe.py`（`HISTORY_START` 闸门 + `list_date` cast）、`backend/tests/test_sync_integrity.py`、`backend/tests/test_pipeline.py`（确切清单 + stub 列表）、新增 `backend/tests/test_pipeline_state_truth.py` | 回测真正读的 `universe_daily_bt`（`api/v1/backtest.py:91`）**不在任何步骤集**里，唯一写入方是手动脚本 ⇒ **实测冻结**：起始年 **2022**、每年仅 **1,132~1,133** 只、最新 **2026-09-04**；而每晚重建的 `universe_daily`（screener 读）有 **2,377~2,494** 只、最新 **2026-09-17** ⇒ 标的覆盖只剩 **45.4%**（缺 **1,367 只 = 54.7%**）、落后 9 个交易日。根因链：`rebuild_qfq` 每晚把 hfq 扩到 2,500 只，但**无人重建 `_bt`**。重建后 **2,495 只 / 2,851,203 行 / 最新 2026-09-18**（+120% 覆盖，52.1s）。接线过程**又暴露两处新缺陷**并一并修复：① 无 hfq 时硬失败会掐断整条夜间流水线（改显式跳过 + 留痕）；② `list_date` 全 NULL 时 dtype 退化为 `Null` 使 `date - null` 崩（改载入处 cast）——详见下文两节 | 8 + 10 条新用例（步骤集/顺序/persist/完备性/docstring 一致、跳过留痕、全 NULL 健壮性）；变异反证 2 组 | ✅ 已验证 |
| — | ⚠️ **P1-42 未覆盖的部分（不计入修复）** | 只读实测 | `_bt` 的 **2022 起点接线修复无法补回**：抽样 40 只 `daily_bar_hfq` 的最早日期**中位数 = 2022-08-05**（仅少数老标的有 2018+ 分区）⇒ 2018–2021 的缺口属**行情数据采集本身的深度限制**，非接线缺陷。重建立即可覆盖的年份仍会因多数标的缺早年数据而只含少量标的；回测响应已通过 `universe_scope.n_symbols`（**按请求窗口**计算）如实披露该窗口的标的数 | — | 已有披露，无新增改动 |
| 33 | **P1-3 模拟盘无持仓校验（裸卖凭空造现金）** | `backend/app/trading/paper.py`（新增 `_held_qty()`；`place_order` 卖出期按决策价折算股数**整手比对持仓**并拒绝且**不落单**；`run_fills` 撮合期**按当时实际持仓截断**、持仓 0 ⇒ 终态 `REJECTED` + `reject_reason`，回报新增 `rejected_orders`；`account_summary` 新增 `integrity_warnings` 披露历史越卖）、`backend/app/api/v1/desk.py`（端点 docstring 列出持仓校验）、`frontend/src/api/production.ts`（`PaperAccount.integrity_warnings` 类型）、`frontend/src/pages/OrderDesk/index.tsx`（**告警横幅**——披露字段若无人消费就等于没披露）、新增 `backend/tests/test_paper_position_guard.py` | 卖出路径**三层皆无持仓校验**（下单 `:128-134` / 撮合 `:220-233` / 记账 `:341`）⇒ 零持仓照常撮合、`cash += amount` 凭空造钱。**父审核员本轮独立复现**：零持仓卖单实测成交 **10,700 股**；"要卖 10 倍持仓"的单实测卖出 **115,200 股 vs 持仓 10,700 股（10.8×）**。修后：下单期拒绝（原因含**可卖股数**）、撮合期截断到持仓、零持仓置 `REJECTED` 终态（模型本就有该状态与 `reject_reason` 字段、前端 `PaperOrder.status` 也早已声明 `'REJECTED'`，但**全仓此前无任何写入方**）。**⚠️ 更正报告措辞**：「`paper.py` 无任何测试文件」不准确——`tests/test_production.py:143-198` 已有 `TestPaperDesk` 4 例 | 6 条新用例（零持仓拒绝且不落单、超持仓拒绝、**持仓充足必须放行**、绕过下单期的红队路径不产生成交且 `equity == INIT_CASH`、截断到持仓、历史越卖必须告警）；`tsc --noEmit` = 0 | ✅ 已验证 |
| 34 | **P1-5 `monthly_monotonic_ratio` 方向反了** | `backend/app/backtest/engine.py:519-533`（判定式 `rets[i] > rets[i+1]` → `rets[i] < rets[i+1]`，并删除同循环内**先算后覆盖**的死代码行）、`backend/tests/test_p2_backtest.py`（**替换恒真断言** `>= 0.0`，新增正反向双向用例） | `group_of()` 使 **Q1=分数最低组、Qg=最高组**，`long_short = Qg − Q1`（`:512`），注释亦写「Qg 月收益 > ... > Q1」；而判定式要求 **Q1 > … > Qg** ⇒ 方向恰好相反。**实测**（`backend/.tmp_testrun/p15_mono.py`）：收益随分数递增（Q1=1.0146 … Q5=1.2849，多空 **+0.2704**）比值 = **0.0**；完全反向（多空 **−0.2737**）比值 = **1.0** ⇒ **完美因子被判 0%、反向因子被判 100%**，且该数字由 `scripts/p2_experiment.py:86` 打印进实验报告。修后：递增 ⇒ 1.0、反向 ⇒ 0.0。原测试断言 `>= 0.0` 对比例**恒真**（本缺陷据此逃过测试） | 1 条双向用例（构造前提先自证 Q5>…>Q1 / 反向后 Q5 最差，再断言 1.0 / 0.0） | ✅ 已验证 |
| 35 | **P1-2 策略回测缓存键漏 `use_legacy_engine`** | `backend/app/api/v1/backtest.py::_strategy_cache_key`（键内补 `legacy{0,1}`）、`backend/tests/test_backtest_cache_key.py`（+1 条） | `/strategy-run` **先查缓存再执行**（`:723`），而 `use_legacy_engine=true` 走旧引擎（无停牌/涨跌停/T+1 闸门，结果偏乐观）、`false` 走真实闸门，两者**共用一个键** ⇒ 600s 内互取缓存返回**另一套引擎**的结果；连载荷里的 `liquidity.engine="legacy_no_gates"` 披露（`:677-683`）都会与实际所用引擎不符。修后键内显式带 `legacy0/legacy1`，默认与显式 `False` 同键 | 1 条用例（键必须不同 + 键内标记 + 默认等价 + 与其它字段仍独立） | ✅ 已验证 |
| 36 | **§4.8 序 16：把 `Query` 对象当 `refresh` ⇒ `/settings` 每次清空统计缓存** | `backend/app/api/v1/datacenter.py::overview`（签名改 `Annotated[int, Query(ge=0, le=1, ...)] = 0`）、`backend/app/api/v1/app_settings.py:157`（显式 `refresh=0`），新增 `backend/tests/test_settings_overview_cache.py` | `refresh: int = Query(0, …)` 在**直接调用**时拿到的是 **`Query` 对象**，而 `bool(Query(0)) is True` ⇒ `:466 if refresh:` 恒真 ⇒ **每次 `GET /settings` 都 `invalidate_stats_cache()`**，把 120s 进程级统计缓存清掉、逼出数百个 parquet 的全量重扫——这正是前端注释里「冷算 27~30s」的根因。修后：内部无参调用拿到真正的 `0`；`refresh=1` 能力保留；参数校验保留（本项目契约是 HTTP 恒 200 + 业务码 `40000`） | 7 条用例（默认值必须是假值、`/settings` 零失效、`refresh=1` **必须**失效、HTTP 默认零失效、越界 `5/-1` ⇒ 业务码 40000、OpenAPI 仍为 integer 查询参数） | ✅ 已验证 |
| 37 | **P1-15 快照静默截断到 200 只（第 201 只起预警永不触发）** | `backend/app/data/quotes_hub.py`（新增 `QUOTES_SHARD_SIZE`/`QUOTES_MAX_SYMBOLS_TOTAL`；`quotes_snapshot` 改为**逐片抓取并合并**，仅在超总上限时截断且必然 WARNING + `truncated`/`dropped_count`/`limit`/`requested`/`returned`/`n_shards` 披露，`source` 多片不一致时标 `mixed`；缓存命中按**本次**请求口径重新披露），新增 `backend/tests/test_quotes_snapshot_sharding.py` | `quotes_hub.py:68-69` 原为 `syms = syms[:200]`——**无日志、无字段**。而 `alerts.py:520` 把**全部**报价类规则（price_pct/price_cross/volume_spike）的标的合并成**一次**快照，且 `dict.fromkeys` 保序 ⇒ watchlist 类规则**第 201 只起永不触发，且响应里没有任何迹象**（`确定`）。修后 250 只实测 **2 片 200+50 = 全部 250 只返回**（原实现只回 200）；总上限 800 是**有界但绝不静默**的边界（限速 `realtime._MIN_INTERVAL=0.25s` ⇒ 4 片约 1~2.5s，落 30s 推送窗口内） | 6 条用例（>200 必须全返回且分片长度 = 200+50、超总上限必披露+WARNING、≤200 仍单片**不引入多请求**、缓存命中的口径不串味、多片混源标 `mixed`、空输入契约形状） | ✅ 已验证 |
| 38 | **P1-34/B7a-03 未做 ST/停牌过滤的榜单被当成已校验结果** | `backend/app/data/screening.py`（`filter_universe` 返回 `FilterUniverseResult`：**仍可两元解包**，另带 `universe_ok`/`universe_rows`/`universe_reason`；空快照**记 WARNING**；`write_screener_snapshot` 把口径写进 `stats_json`、`load_screener_snapshot` 读回）、`backend/app/api/v1/screener.py`（`_build_items` 回传口径；`_finalize_*` 口径字段**恒在**且 `applied=False` 时**不得报 ok**；`_unavailable_body` 也带该字段）、`tests/test_panic_rootcause_guard.py`（两条逐字段断言随契约**有意扩展**而更新），新增 `backend/tests/test_universe_filter_disclosure.py` | `filter_universe` 只在 `universe_daily` 当日快照存在时才 join + 过滤（`if universe.height:`），而 `require_universe` **默认 False** ⇒ 无快照时**不 join、不过滤、也不披露**，调用方照报 `status="ok"`：同一 pred 下 ST 股从「被剔除(count=1)」变成「进榜(count=2)」、`coverage` 仍 `2/2`。**直接违反 `screening.py:85-88` 自己写下的红线**（未校验的榜单不得当成已校验结果）。修后：`applied=False` ⇒ `degraded`/`universe_unfiltered`；口径随快照**持久化**（用 `stats_json`，**不迁移生产 SQLite**）⇒ 快照优先的**主路径**也带真值；旧快照无该键 ⇒ `applied=None`（未知，既不臆断"没过滤"也不谎称 ok） | 12 条用例（ST/停牌确被剔除的正向基线、分区缺失/当日无行**两种原因区分**、两元解包兼容、`require_universe=True` 不弱化、快照口径落盘与读回、旧快照报 None、未过滤**永不 ok**、已过滤仍 ok、未知不误伤、`unavailable` 优先级更高、空态也带口径） | ✅ 已验证 |
| 39 | **§4.8b S12 `datacenter/instruments` 的 `total=len(rows)`（总数随 limit 缩小）** | `backend/app/api/v1/datacenter.py::list_instruments`（`total` 改由独立 `COUNT(*)` 得出；补 `returned`/`truncated`/`limit`；`limit` 由裸默认值收紧为 `Query(ge=1, le=500)`），新增 `backend/tests/test_datacenter_instruments_total.py` | `:1042` 把**返回条数**当总数 ⇒ `limit=1 → total=1` 而真实 5552。前端 `DataCenter/index.tsx:307,346` 直接渲染成「可抓取 N 只」「… 等 N 只」⇒ **用户可见的错误数字**（比不返回 `total` 更误导）。附带：`limit` 原本**无任何校验**，`limit=-1` 在 SQLite 里等于**无上限**（全表返回）、`limit=0` 静默空列表 | 5 条用例（`limit=1` 时 total 仍为真实值、`limit≥total` 不谎报 truncated、类型过滤各有自己的 total、`all` 计全表、`-1/0/501` 必须被参数校验拦下为业务码 40000） | ✅ 已验证 |
| 40 | **P1-35（B7a-05）日历非法日期被报成系统故障 + 历史榜错标日期** | 新增 `backend/app/core/params.py`（`parse_iso_date`/`parse_yyyymmdd`：形状**与**日历双重严格，非法一律 `ERR_PARAMS(40000)`）、`api/v1/stock.py`（kline 的 `start`/`end`，并前移到缓存查询**之前**）、`api/v1/market.py`（`/overview`、`/overview/daily`）、`api/v1/screener.py`（`date`）、`market.py::_build_overview_hist`（改收 `date` 对象；回退最新榜时标 `fallback="latest"`+`fallback_reason`，`trade_date` 改为**实时块真实所属交易日**、请求日另放 `requested_date`），新增 `backend/tests/test_param_date_calendar_validation.py` | ① 三处端点只用 `Query(pattern=…)` 校验**形状**，`20269999`/`2026-02-30` 一路穿到 `date(...)`/`fromisoformat` 才抛 `ValueError` ⇒ 全局处理器归成 **`code=50000`（未分类系统异常）**：纯参数错误被报成平台故障；② 更糟的变体：`/overview?date=20269999` 反而 **`code=0`** —— `_build_overview_hist` 把 `ValueError` 吞进 `except` 后**保留最新榜**，调用方再把 `trade_date` 改成请求日 ⇒ **最新推荐榜被错标成请求日期**。修后：非法日期一律 40000；合法历史日的 `trade_date` 恒为实时块所属日、`requested_date` 单独回显；回退有标记。**自查发现**：`parse_yyyymmdd` 初版对 7 位输入 `"2026021"` 过宽（切片被当成 `date(2026,2,1)`），已补长度/数字校验 | 21 条用例（解析器 6+5 组非法值、闰年 2-29、三端点 40000、`overview` 两端点 40000、历史路径 `trade_date`≠请求日、回退必须带 `fallback`/`fallback_reason`） | ✅ 已验证 |
| 41 | **P1-36（B7a-07）空涨跌分布造「情绪 中性 50」** | `backend/app/api/v1/market.py::_heat_payload`（无有效样本 ⇒ `status:"unavailable"` + 固定 reason；`extra` 仍保留成交额/note）、`_build_sentiment`（**零样本**再兜一层：`up+down+flat==0` ⇒ unavailable，绝不推 50/中性），新增 `backend/tests/test_heat_sentiment_and_probe_truth.py` | 输入为空集/全 NaN 时原实现仍回 `status:"ok"` + `up=down=flat=0` + 九桶全 0（"看似正常实则为空"），并连带 `_build_sentiment` 用 `(0-0)/max(0,1)=0` 推出 **score=50「中性」** ⇒ **完全没有涨跌数据的日子对外宣称"市场情绪中性"**（零数据造结论）。**必然触发**：每年首个交易日（`_heat_from_local` 只取当年分区 ⇒ `shift(1)` 无前值 ⇒ pct 全空） | 13 条用例（pandas/polars × 空集/全 NaN/全 None ⇒ unavailable 且不得给 `up/down/flat`、有样本行为不变、降级仍保留 `total_amount_yi`/note、零样本不得给 score/label、正常广度仍给分与 `kind/basis` 披露、上游 unavailable 传导） | ✅ 已验证 |
| 42 | **B7a-06 降级 reason 外泄内部异常串** | `backend/app/api/v1/market.py::_build_heat`（原 `:151`）、`_build_ai_stats`（原 `:491`） | 实测形态：`{"status":"unavailable","reason":"_Boom: ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection'))"}` —— 远端主机/协议细节随 JSON 回前端。**同文件 `_build_sectors:218-223` 早有同类修复注释**（"不要把内部异常串直接放进 API 响应"），`_build_heat`/`_build_ai_stats` **漏改**。修后：细节只进日志，对外固定文案（heat 另带 `degraded_from:"em_snapshot"` 便于定位来源） | 2 条用例（桩掉 `_safe_call` 抛含 `RemoteDisconnected` 的异常，断言响应串里**不含**异常名与细节） | ✅ 已验证 |
| 43 | **P1-43（DEP-10）`/health/ready` 恒 HTTP 200 ⇒ K8s readiness 探针空转** | `backend/app/main.py::health_ready`（未就绪 ⇒ **HTTP 503** + `code=ERR_NOT_READY(50300)` + `Retry-After: 5` + 逐项 `checks`；就绪仍 200）、`core/errors.py`（新增登记 `ERR_NOT_READY=50300`）、`frontend/src/types/api.ts`（**双向登记**）、`docker-compose.yml`（healthcheck 由 `/health` 改为 `/health/ready`，与镜像 `Dockerfile:57` 一致，消除"镜像声明成死配置"）、`README.md:200`（写明各自 HTTP 语义与"不要拿 `/health` 当 readiness"） | 原实现两个探针都经 `ok()` ⇒ **恒 200**，`not_ready` 只写在 body ⇒ `curl -fsS` 仅在连接被拒时失败，**实际退化成 liveness**；而 README 把它们宣称为 K8s 探针 ⇒ 直接套用会让「sqlite/`DATA_ROOT` 检查失败」的 Pod 被判 **Ready** 并继续接流量；另 `Dockerfile:57` 的 `/health/ready` healthcheck 被 `compose:76` 的 `/health` 覆盖（死配置）。修法：运维探针走标准 HTTP 语义（503），与业务端点"HTTP 恒 200 + 业务码"契约**并存** | 3 条用例（未就绪必 503 + `50300` + `data_root:"failed"` + `Retry-After`、就绪 200 且 `/health`/`/health/live` 不被误伤、探针匿名可达） | ✅ 已验证 |
| 45 | **R9/S14 错误码治理：15 个裸码 + 3 处语义错位 + 两端码表漂移** | `backend/app/core/errors.py`（登记 `ERR_BT_*` 40010–40019；`40017` 按语义拆为 40017/40018/40019）、`api/v1/backtest.py`（10 处裸码→常量）、`api/v1/datacenter.py`（`4002→ERR_PIPELINE_BUSY`×2、`5000→ERR_PIPELINE_BUSY`、`4003→ERR_DATA_EMPTY`、`5001→ERR_SYSTEM`+去掉 `{e!r}` 外泄、`sync/tasks/{id}` 不存在 `51001→ERR_NOT_FOUND`）、`api/v1/alerts.py`（`40400`×2→常量）、`api/v1/screener.py`（`40000`×3→常量）、`frontend/src/types/api.ts`（**补齐** `PANIC_CONTAINED=50001` 与 10 个回测码，至此与后端**逐值逐名相等**）、`frontend/src/api/client.ts`（未知码统一追加 `（未登记错误码 N）`），新增 `backend/tests/test_error_code_governance.py` | **机制缺陷**：码表靠人肉维护、两侧各自为政 ⇒ `datacenter.py` 5 处 + `backtest.py` 10 处把**裸整数字面量**当错误码，`errors.py` 与 `types/api.ts` **都不含**这 15 个码 ⇒ 前端只能拿到文案、**无法按码分类**，QA 也看不出"码表缺项"；`alerts.py`/`screener.py` 另有 5 处"值已登记但仍写字面量"（靠人脑对齐）。**语义错位**：① `sync/tasks/{id}` 不存在报 `51001`（本地无数据）而非 `40400`；② `40017` **一码三义**（折数区间过短 / optuna 缺候选 / 寻优器 ValueError）；③ `5001` 把本地 SQLite 读失败说成"外部数据源"式的 `{e!r}` 异常串外泄。**修后**：裸码清零（AST 守卫兜底）、两端逐值逐名对账、未知码前端显式标记。**两处对 §8.3 建议的偏离（如实记录）**：`5000` 建议 `50000/52000` 二选一 → 实取 **40900**（语义是"任务已被别的执行者领取"＝互斥冲突；52000 是训练码，50000 是未分类桶）；`5001` 建议 `51000 ERR_DATA_SOURCE` → 实取 **50000**（那是**本地库**读失败，套用"外部数据源"会稀释该码含义——正是本批要消灭的问题） | 14 条用例：**变异反证**（合成源码 7 组：`fail(4002,…)`/`AQPException(40017,…)`/带空白/常量/变量/别的函数名，确保守卫**不空转**）、全仓 AST 扫描无裸码、引用的 `ERR_*` 必须存在、15 个码的现状（10 个已登记 + 前 4 个已归并且**不再**作为码值存在）、**两端码表逐值相等 + 逐名相等**、前端未知码标记（静态断言）、`sync/tasks/{id}` 行为断言（40400 + 回显 task_id）、回测参数错误行为断言（40014） | ✅ 已验证 |
| 46 | **B7b F6 复权口径回退零披露（P2，前端反向担保口径）+ P0-4 下半条基准口径不可辨** | `backend/app/api/v1/backtest.py`（`_load_strategy_bars` 返回 `(bars, raw_fallback)`；响应新增恒存在的 `price_basis{kind,basis,raw_fallback_symbols,note}` 与 `benchmark_basis{kind,synthetic,basis,reason,note}`（判据取自**实际净值曲线**）；顺带补 `date`/`close` 必需列守卫）、`frontend/src/pages/Backtest/index.tsx`（`basisLabel()`/`benchmarkLabel()`：只显示后端披露的事实，缺字段显示"复权口径未知"）、`frontend/src/pages/Backtest/parts.tsx`（规则说明不再无条件担保"本地前复权"）、`frontend/src/pages/Backtest/resultParts.tsx`（构造基准时 KPI 卡标题改为"基准年化收益（构造基准）"）、`frontend/src/api/strategyBacktest.ts`（两个披露字段声明为**可选**，兼容旧 Redis payload），新增 `backend/tests/test_backtest_price_basis_disclosure.py` | `_load_strategy_bars` 里 `source = "qfq"/"raw"` 跟踪了"缺 QFQ 分区 ⇒ 回退**不复权**日线"这一事实却**从不读取**（ruff F841 唯一命中点）。两层后果：① **后端零披露** —— 响应里没有任何复权口径字段，用户无法区分"QFQ 口径"与"部分标的不复权"（除权跳空会被均线/突破判定误读为真实行情）；② **前端反向担保** —— `index.tsx:88` 硬编码 "QFQ"、`parts.tsx:316` 写死"行情口径：本地前复权（QFQ）日线" ⇒ 前端把后端**没有担保**的口径展示成担保事实（比缺字段更危险）。**P0-4 下半条（基准口径）**：基准获取失败 / 区间无重叠交易日 / 引擎对齐后仍全 NaN 时，引擎代之以**常数基准** ⇒ `annual_benchmark` 变成**构造出来的 0.0**、`alpha/beta/IR` 为 null，而响应里**只有这个 0.0**、没有 `basis` 字段（红队已证明仅"α/β 为 null + 曲线是常数"这种**间接**可辨）。**修后**：回退清单外显为 `price_basis`、基准退化外显为 `benchmark_basis`（两者**口径字段不随数据可用性变化**，恒存在），`basis∈{qfq,raw,mixed}` / `synthetic∈{true,false}` 且退化时给 `reason`（`fetch_failed`/`no_overlap`）；前端标签改读披露、缺失即显示未知；顺带堵住"缺 `date`/`close` 列 ⇒ `ColumnNotFoundError` ⇒ 裸 50000"（改 `ERR_DATA_EMPTY` 并点名列）。**残留（如实）**：`strategy_base.py:296-298` 内部仍构造常数基准（**行为未改**，本轮只做披露；改行为需产品决策）；`/backtest/run`（Top-K 引擎）的基准口径未纳入本轮 | 10 条用例：有 QFQ ⇒ `basis=qfq` 且清单为空；缺 QFQ ⇒ **`basis=raw` 且点名回退标的**（F6 核心）；两标的只缺一个 ⇒ `basis=mixed` 且**只能点名真正回退的那个**；缺 `close` ⇒ 端到端 `51001` 且消息含 `close`（**并断言不是 50000**）；`_load_strategy_bars` 返回值形状（防"又只返回 dict ⇒ 披露再次丢失"）；缺列时单元级 `AQPException` 码；基准可得 ⇒ `synthetic=False` 且 `reason=None`；**基准获取失败 ⇒ `synthetic=True`/`reason=fetch_failed` 且 `annual_benchmark==0.0` + `alpha is None`**（诚实性锚点）；**区间无重叠 ⇒ `synthetic=True`/`reason=no_overlap`**（判据取自**实际净值曲线**而非只信 try/except）；前端静态断言（`index.tsx` 不得含 `· QFQ ·`、必须含 `price_basis`/`复权口径未知`/`QFQ + 不复权混用`/`benchmark_basis`/`基准不可得（构造基准）`，`resultParts.tsx` 必须含 `benchmarkSynthetic`/`基准年化收益（构造基准）`，`parts.tsx` 不得再无条件担保，`strategyBacktest.ts` 两个披露字段必须可选）。**独立复核**：`ruff check app --select F841` 由 4 → **3**（`backtest.py` 的 `source` 不再是死变量，而是被使用的返回值 —— F6 的机械证据）；剩余 3 条（`etf.py:436 last`、`ma_cross.py:105 day_idx`、`monitor.py:718 label`）均为§6.1 死代码批次里已定性为**无害残留**的项 | ✅ 已验证 |
| 47 | **schema 漂移（分区缺列）⇒ 裸 50000：同族 5 处** | `backend/app/data/parquet_store.py`（新增 `missing_columns()` 纯判据 + `require_columns()` 缺列即 `ERR_DATA_EMPTY` 并点名列）、`api/v1/watchlist.py::_bars_for`（必需列并入回退判据 + warning）、`api/v1/screener.py::_watchlist_quotes`（缺列 ⇒ 该标的价按不可得 + warning）、`data/screening.py::filter_universe`（过滤能力**由实际 join 进来的列决定**；缺 `is_st`/`board` 走 `board_of` 兜底并把 `universe_ok` 置 False + `universe_reason="universe_schema_incomplete"`；连 `date` 列都没有时按无快照处理）、`api/v1/desk.py::capacity`（`require_columns(close,volume)`）、`trading/paper.py::screen_universe_candidates`（按段跳过不可评估类别 + warning），新增 `backend/tests/test_schema_drift_column_guards.py` | **同一族的 5 处**：投影读 `read_symbol_dataset(columns=...)` 与直接 `pl.read_parquet` 都**容忍缺列**（文档化的有意设计），但消费方**不再校验列齐备**：`watchlist._bars_for` 只判 `is_empty()`+`date`、`screener` 直接 `tail["close"]`、`filter_universe` 在"快照非空"分支无条件 `pl.col("board")`/`pl.col("is_st")`、`desk.capacity` 直接 `close×volume`、`paper.screen_universe_candidates` 直接 `pl.col("is_st")` ⇒ 缺列时抛 `ColumnNotFoundError`（**非** `AQPException`）被全局兜底归成 **`code=50000`（未分类系统故障）**，把"本地数据集需重建"误导成"服务端崩了"，且这些端点**本来都已定义**了合法降级（51001 / 外部源回退 / 价格 None / applied=False 披露）——只是判据漏了列维度。**发现路径**：第 9 轮跑 24 文件子集时出现 6 条失败、全量却 0 失败，判定为"会话共享 DATA_ROOT 的顺序产物"但**不放过**其中的健壮性线索，本轮用私有 DATA_ROOT 逐个复现定性 | 10 条用例：判据本身有判别力（`missing_columns` 三态）、`require_columns` 码 + 消息含列名 + 含**具体文件名**、`filter_universe` 缺列不抛异常且 `universe_ok=False`/`reason=universe_schema_incomplete`/board 兜底仍生效、快照连 `date` 都缺时按无快照、5 个端点端到端（watchlist dashboard/correlation、screener watchlist 的 `close is None`、desk capacity 的 `51001` 且点名列、desk exclusion screen 的 `[]`）**全部 `assert code != 50000`**；**缺陷本体反证**：逐条执行修复前写法并断言抛 `ColumnNotFoundError`，且 `not issubclass(ColumnNotFoundError, AQPException)`（若不再抛则说明语料没复现缺陷，上面的断言会沦为自欺） | ✅ 已验证 |
| 48 | **P1-14 ETF 资金流字段口径错：`主力净额 − 小单净额`（偏高 +29.4%，且符号可错）** | `backend/app/data/etf.py::fetch_etf_flow_history`（`net_inflow` 直接取 `f52`（主力净额），不再做任何加减；新增分项净额 `super_large_net/large_net/medium_net/small_net` 供独立核对；缺失字段由 `or 0` 改 `None`；列数判据由 `<3` 收紧为 `<6`；重写 docstring 写明 `f51~f63` 真实列序与推导）、`frontend/src/types/etf.ts`（`EtfDetailFlow.items[].net_inflow` 改 `number \| null` + 分项可选字段，与"缺失不再伪造成 0"对齐；前端 `?? 0` 渲染已 null-safe），新增 `backend/tests/test_etf_flow_field_semantics.py` | 东财 `fflow/kline` 的 `f52`~`f56` 是**净额**序列（主力/小单/中单/大单/超大单），**不是** in/out 成对。修复前注释自称 `f52=main_in, f53=main_out`，代码算 `f52 − f53` = **主力净额 − 小单净额**；由"四类净额之和为 0"（`小单 = −(主力+中单)`）可知旧值 ≡ `2×主力 + 中单`，**偏差率 = 1 + 中单/主力** ⇒ 既有真实样本高估 **+29.4392%**，且**符号可错**（主力净流出而中单大幅净流入时，"流出"被显示成"流入"）。同仓 `realtime._fetch_em_fflow` 解读**正确**，两处口径分叉。**判据**：真实样本上 `f55+f56 ≡ f52`（65,674,576+208,524,128 = 274,198,704 = f52）精确成立 | 9 条用例：`net_inflow == f52` 且分项满足 `大单+超大单 = 主力`；**真实观测样本复算**（B3b `:128-130` 的真数）证明旧值 = 354,920,654、偏差 +29.4%、并核对两条恒等式；**缺陷本体反证**：对任意自洽样本证明旧值 ≡ `2×主力+中单`（偏差率 `1+中单/主力`）；**符号翻转**用例（主力 −1.0e8 + 中单 +2.5e8 ⇒ 旧值为正）；缺失字段 ⇒ `None`（非 0）；短行/空行跳过；空载荷 ⇒ `[]`；**与 `realtime._fetch_em_fflow` 同构**（同一行 kline 两处解读必须逐字段一致，防再次分叉）；静态断言误导性注释 `f52=main_in` 不得再出现 | ✅ 已验证 |
| 49 | **S4 监控触发器口径：PSI 假降级 15 天 + KS 全命中 + IC 阈值假阴性 + 虚假"恢复健康"（P1-17 / P1-21 / R5）** | `backend/app/ml/monitor.py`（新增 `_psi_one` 直方图核、`_xsec_standardize` 截面标准化、`prepare_xsec_frames` 共享切窗、`_pool_adjusted_std` 池宽折算；`compute_psi` 改**双口径**（判定=截面标准化，`raw`=池化原始值仅披露）、`compute_ks` 同口径 + `over_crit_ratio`/`crit_effective`/`cross_section_median`、`_STATE_RANK` 改 `healthy<unknown<watch<degraded`、`run_monitor` σ 用折算值并披露 `sigma_basis/pool_ratio/n_symbols_median_*`）、`backend/app/api/v1/report.py`（AI 日报两条文案改为口径披露 + "超限比只作强度读"）、前端 `components/FactorHealthCard.tsx`（标签改"PSI max（截面标准化·判定口径）"、增显 `raw.max` 与 KS 超限比/单日尺度）、`types` 侧 `api/monitor.ts`（新增 `basis`/`raw`/`over_crit_ratio`/`crit_effective`/`sigma_basis`/`pool_ratio`/`psi_max_raw` 等可选字段）；新增 `backend/tests/test_monitor_trigger_caliber.py`，改写 `tests/test_monitor.py` 3 条旧契约断言 + 新增 1 条 | **四条缺陷**（均由真实快照/真实面板复现）：① **PSI 口径**：池化原始值把"水平型因子随趋势的必然平移"判成漂移 —— 快照 `psi.max=3.3051`（`g1_ma_gap_250`）、`mean=0.2646`、**21/85 因子 >0.25** ⇒ `drift_state=degraded` 自 **2026-09-03 持续 15 天**，且每 12h 触发一次无效重训（`trigger:"PSI=3.3051>0.25"`）。② **KS 无判别力**：池化 `n_b≈6.2e5 / n_r≈5.0e4` ⇒ α=0.05 临界值仅 **0.0063**，而 `mean_D=0.1161`（由水平/尺度漂移主导）⇒ `n_over_crit` **恒 = 因子数（85/85）**。③ **IC 阈值假阴性**：`history.std_ic=0.1151` 取自"每日 ~120 只"时代（快照 `ic_series_tail.n_symbols` 实测历史 119~121、最近 **2486~2492**），未按池宽折算 ⇒ 降级阈值 = `0.0465 − 1.5×0.1151 = −0.1262`，**近期 IC 归零也判 healthy**（预测力消失却无告警）。④ **虚假恢复**：`_STATE_RANK={"unknown":0,...}` ⇒ `_worst(['unknown','healthy'])=='healthy'`，IC 不可评估时整体报健康，并在上一条为 degraded/watch 时推送"因子健康度恢复【健康】"。**独立复核（先复现再修）**：自写只读探针在真实面板（`alpha_basic_v2g` **1,052,956 行 × 85 因子**，2024-12-16→2026-09-17）上**逐位复现**快照 raw PSI（`max=3.3051 / mean=0.2646`，top5 完全一致）后才动代码；并**推翻了自己的一个假设**（曾以为 PSI/KS 误报源于股票池宽度混杂，实测两窗口每日中位 2468 vs 2492 只、`common_symbols` 口径与 raw **完全同值** ⇒ 该混杂不存在；池宽问题**只在 IC 序列**上成立） | 16 条新用例 + 1 条新断言 + 3 条旧断言改写。**不误报**：真实面板判定口径 **0/85 超 0.25**（`max=0.2024` 仅到 watch）；合成面板纯水平平移 3σ ⇒ 判定口径 `≤ PSI_WATCH` 且 `raw` 必须超标（口径拆分而非"看不见"）。**必须报警**：真实面板注入异质样本（`ma_gap_250 ← ret_1`）30%/50% ⇒ **0.3540/0.4166 > 0.25** 判 degraded。**如实固化的局限**（防止后人当成漂移预言机）：注入 70% ⇒ **0.2271（不报警，非单调）**、注入 `g1_ma_gap_250` 50% ⇒ 0.2024（该因子无响应）；截面标准化对"截面内单调变换/跨日打乱"不可见（PSI 类边际量的固有盲区，由 IC 通道兜底）。**字面方案被实测否决**："截面内排名后再算 PSI" ⇒ `mean=0.0011/max=0.0131/0-85` —— 秩在截面内构造上恒均匀 ⇒ 判别力归零，故改用"去中位+MAD 归一"并在代码中注明理由。**③ 数值复现**：`_pool_adjusted_std(0.1151, 120→2490) = 0.0253`，`_ic_state(0.0, 0.0465, 0.1151)=='healthy'`（复现假阴性）→ 折算后 `'degraded'`，而实测 0.0774 仍 `'healthy'`（不误报）。**④** 4 条用例含真实推送调用断言（`degraded→unknown` 不得发出"恢复"文案）。**顺带修性能回归**：初版按"每因子一次 `groupby.transform`"实现截面标准化，真实面板单次 PSI 达分钟级（首轮全量套件 6min→23min）；改为**整表 2 次 groupby** + PSI/KS **共享**帧后 `prepare 5.2s + psi 6.5s + ks 5.0s`，且数值与优化前**逐位一致**（`3.3051/0.2024/0.9647`）；前端 `npx tsc --noEmit` 通过。**集成锁 + 变异反证**：`test_monitor.py::test_run_monitor_structural_contract` 改为**确定性合成面板**（60 交易日 × 6 标的 + predictions）以**保证走 `ok=True` 分支**——首版该断言把 basis 写成不存在的 `"pool_adjusted"` 却仍然"绿"，实测原因是会话共享 DATA_ROOT 为空、断言**空转**（已如实记录）；现断言快照里 `psi.basis/raw.basis/raw.max/ks.basis/over_crit_ratio/thresholds.psi_basis/ic_sigma_basis/state_log[].psi_basis` 全部真实存在，并用**变异反证**（把 `compute_psi` 换成缺 `raw` 的版本 ⇒ 快照随之改变）证明断言非空转 | ✅ 已验证 | | `backend/tests/test_read_endpoints_rbac.py`（对非 JSON 响应不再无条件 `r.json()`；新增专条固化 xlsx 断言） | `test_read_endpoint_allows_authorized[/api/v1/export/screener]` 无条件 `r.json()`：该端点**有数据**时返回 **xlsx（ZIP）二进制** ⇒ `json.loads` 抛 `UnicodeDecodeError: ... byte 0xc7`；**DATA_ROOT 为空**时走 `ERR_DATA_EMPTY` 的 JSON 降级路径 ⇒ 用例"碰巧"通过。因 conftest 的 DATA_ROOT 是**会话共享**临时目录，该用例会随**同批文件集合/顺序**漂移（实测：单跑 50 passed；紧跟 `test_api.py` 后 1 failed）。已固化为正确断言（HTTP 200 + 非空 + xlsx 必须 `PK` 魔数） | ✅ 已验证 |
| 50 | **组合约束：上限不可行静默半仓 + Σw 不归一 + μ≡0 却称均值-方差 + rf/基准口径分裂（P1-13 / B2-12 / B2-11 / B2-16）** | `backend/app/domain/optimizer.py`（新增纯判据 `cap_feasibility(n,cap)` 与 `cap_info_for(w,cap)`；**`apply_weight_cap` 行为逐字不改**，仅在 docstring 写明 `n·cap<1` 无解）、`backend/app/backtest/engine.py`（`_compute_target_weights` 改返回 `(weights, cap_info)` 并由统一出口 `_finish()` 对**实际权重**判定；新增 `_summarize_cap_infos`（取最坏，非平均）+ `BacktestResult.weight_cap_info`）、`backend/app/api/v1/backtest.py`（响应加 `weight_cap_info`）、`backend/app/domain/research.py`（`optimize_portfolio` 带出 `weight_cap_info` + 新增 `_expected_returns_disclosure`）、`backend/app/api/v1/research.py`（响应加 `weight_cap_info`/`expected_returns`）、`backend/app/domain/portfolio.py`（容差内 **归一** + `weights_normalization` 披露；rf 改单源、形参 `daily_rf→rf_annual` 并**真正生效**）、`backend/app/domain/metrics.py`（新增唯一常量 `RISK_FREE_ANNUAL=0.02`）、`backend/app/domain/risk.py`（默认 `rf` 改年化同源、新增 `benchmark_symbol` 与 `rf_annual` 披露、删硬编码基准名）、`backend/app/data/panels.py`（新增 `_BENCHMARK_CODE/_BENCHMARK_LABEL` 并下传真实标识）、前端 `api/research.ts`（声明 `weight_cap_info`/`expected_returns`）、`pages/Research/index.tsx`（上限告警 `role="alert"` + 权重合计 + MVO 标签改"MVO（μ 不可用 ⇒ 实为最小方差）"）、`types/stock.ts` + `pages/StockDetail/index.tsx`（披露 `rf_annual`）；新增 2 个测试文件 | **四条独立缺陷**：① **P1-13**：`apply_weight_cap` 在 `n·cap<1` 时**静默**返回 `Σw=n·cap`（N=10、cap=5% ⇒ 只能投 **50%**＝一半永久现金），API 仍报 `status=ok`、`fallback=false`、**无任何字段可查**。② **P1-13 家族新发现（审核文字未点明，本轮由探针暴露）**：`weighting="equal"`（**默认方案**）**根本不调用** `apply_weight_cap` ⇒ 用户设的 `weight_cap` 被完全忽略（实测 top_k=4/cap=0.2 时实际 max w=**0.25 > 0.2**、Σw=1.0）⇒ 修复后必须区分"**不可行且真执行了**（半仓）"与"**压根没执行**（超限但满仓）"两种情形，否则文案会把"满仓且超限"说成"只能投 80%"。**有意不改行为**：`n·cap<1` 时强制 `equal` 执行上限会退化成半仓，比"超限满仓"更糟 ⇒ 只披露 + 给出 `min_feasible_cap`。③ **B2-12**：`portfolio.py` 接受 `Σw∈[0.99,1.01]` 却**不归一** ⇒ `Σw=0.995` 时 0.5% 永久现金、`holdings_drift` 恒非零、且响应回显的权重与实际执行口径不一致。④ **B2-16**：`risk_metrics` 默认 `rf=0.0` 而 `portfolio` 用年化 2% ⇒ 个股风险卡与组合页的"夏普"**不可比**；且 `portfolio._compute_metrics` 的形参名 `daily_rf` **从未被使用**（函数体直读模块常量）＝形参与实现分裂；`benchmark` 标签**硬编码"沪深300"**，与传入的基准无关。⑤ **B2-11**（独立复核后按报告建议**只披露不接线**）：`/research/optimize` 不收预期收益 ⇒ `μ≡0`，实测 **λ=8 与 λ=50 的权重 L1 距离 = 0.0**（COMPLETELY inert）；对照实验证明缺陷在**调用方未给 μ**而非优化器：μ 异质时 λ 立刻生效（L1=**0.4593**） | 23 条新用例（`test_portfolio_constraints.py` **13** 条 + `test_risk_caliber_consistency.py` 10 条），**含缺陷本体反证**：`apply_weight_cap` 行为不变（仍 `Σw=0.5`）但 `cap_info_for` 能判 `feasible=False`；`equal` 方案 `cap_enforced=False` 且 `max_weight=0.25>cap`；`_summarize_cap_infos` 取最坏（3 次调仓中 1 次半仓 ⇒ 整体 `invested_ratio_min=0.5`，平均值会掩盖）；`risk_metrics` **默认 `benchmark is None`**（证明硬编码已删）且 Sharpe 位移 `= rf/年化波动`（实测 0.1085 ≈ 0.02/18.4%，算术自洽）；`_compute_metrics` 的 rf 形参**现在真的改变 sharpe**（0.0907 ≈ 0.02/22.0%）；Σw=0.98 仍**拒绝**（归一不是"来者不拒"）；`mvo` 披露只在 mvo 出现。**另含一条潜在耦合守卫**（非缺陷，见下方观察）：`cov_window<20` 时风险类方案必然回退等权，而两个 HTTP API 的 `ge=20` 恰好守住 ⇒ 用例把 API 下界与 `_trailing_returns(min_obs=21)` 的耦合钉死，防止日后放宽下界导致**静默退化** | ✅ 已验证 |
| 51 | **P1-18 `g1_*` asof 不稳定（邻接 universe 未冻结）** | `backend/app/ml/graph.py`（新增 `edges_digest` / `universe_digest` / `load_universe_snapshot` / `save_universe_snapshot` / `resolve_universe` / `write_graph_lineage`；`build_adjacency(symbols, universe=None)`）、`backend/app/ml/features.py::apply_propagate(..., universe=None)`、`backend/app/orchestrator.py::step_build_features`（只读窥探→首次冻结过渡期守卫、边集漂移与"冻结标的无行情"两条默认拒绝、血缘落盘）、`backend/app/ml/predict.py:121`（实时回退也读冻结快照 ⇒ train/serve 同源）、`backend/scripts/build_features.py`（显式重建按"允许重冻结"处理并留痕） | **缺陷本体实测**（探针 `backend/.tmp_probe_s4/probe_p118b.py` / `probe_p118c.py`）：①同行业桶**新标的入池** ⇒ 老标的 A 的 `g1_` **3.0 → 252.0**（解析值 (2+3+4+999)/4）；②**已冻结标的行情消失**（`read_all_symbols(skip_empty=True)` 会跳过被隔离标的留下的空目录）⇒ **3.0 → 2.5**、不可复原。**修复实测**：冻结节点集后新标的不是节点、其取值不入分子分母 ⇒ 仍 **3.0**、其自身 `g1_=NaN`；三条漂移（首次冻结于既有历史之上 / 边集指纹变化 / 冻结标的无行情）默认 `ValueError` 且**不落任何年分区、快照与血缘**（年分区 mtime 快照比对 + 文件存在性断言），`FEATURE_ALLOW_GRAPH_DRIFT=1` 才重冻结并写 `refrozen_from`。新增 `backend/tests/test_graph_universe_freeze.py` **12 条**（含本体反证、边界"不可修的那半"、过渡期守卫、指纹标签/权重敏感性、AST 反向锁）；`test_feature_incremental.py::test_incremental_preserves_stale_frontier_rows` 补 2a「默认拒绝且不改写」+ 2b「显式授权后再验证增量合并 stale=1/filled=1」。**残余（未修，如实登记）**：GNN 训练器 `train_service.py::build_adjacency(symbols)` 未传冻结集——GNN 不可 promote、不在生产链路，改它会牵动 `test_train_service.py` 的 monkeypatch 签名，留待 GNN 转正时一并处理 | `pytest tests/test_graph_universe_freeze.py` → **12 passed**；受影响既有 6 文件（含 `test_pipeline.py`）→ **56 passed + 3 条沙箱产物**；`test_pipeline.py` 单跑 **6 passed** |
| 52 | **P0-6 空榜终态契约（人工指定"按最优解解决"）** | `backend/app/data/screening.py`（`FilterUniverseResult` 新增 `board_universe_rows`：板块在 `universe_daily` 快照里的成分股数，`all`/无快照 ⇒ `None`，缺 `board` 列按 symbol 前缀兜底）、`backend/app/api/v1/screener.py`（`_build_items` 随 `universe_filter.board_rows` 披露；`_finalize_screener_payload` 在 `total==0` 时按该字段分流） | **裁决**：**不采纳**"`total==0` 一律 `unavailable`"（会把"真跑了但当日无匹配"误报为数据不可用，且与 09-14 有意设计冲突），**也不采纳**"保留 `ok`"（会让 `board=bse` 这种**从未真正筛选**的请求谎报"当日无匹配信号"）。按**可判定事实**拆两类：`board != "all"` 且 `board_rows == 0` ⇒ `unavailable/empty_board`（新增机器码，文案显式写"并非「当日无匹配信号」"）；其余 `total==0` ⇒ 保持 `ok/no_matching_signals`；`board_rows is None`（无快照/旧 schema）⇒ **不臆断**，按原语义。**缺陷本体反证**：同一份真实 `_screen(board="bse")` 载荷，仅去掉新增字段即复现旧的 `ok/no_matching_signals`（差异只来自本裁决） | `pytest tests/test_screener_empty_board_contract.py` → **11 passed**（真值表 6 + `filter_universe` 计数本体 3 + 真实 `_screen` 端到端 1〔bse ⇒ `unavailable/empty_board`；同 pred+universe 的 `main` ⇒ 正常出榜 2 条〕+ 接线锁 1）；既有 09-14 断言 `test_data_freshness_degradation.py:111-127` **零改写仍绿**；受影响 11 文件 **109 passed** |
| 53 | **§8.2 第 17 项 死代码清理（35 项；含自建 AST 孤儿扫描）** | 死代码本体：`core/metrics.py`（7 个"从不写入"的指标 + `Gauge` import）、`domain/a_share_rules.py`（`is_t_plus_one`/`settlement_days`/`commission_rate_default`）、`data/parquet_store.py`（`path_for`/`write_whole_symbol` + `__all__`）、`data/repair.py::is_quarantined`、`core/pipeline_lock.py::current_owner`、`data/cross_section.py::remove_mirror`、`data/panels.py`（3 个工具函数）、`core/events.py::publish`、`ml/infer.py::infer_day`、`data/ingest/dividends.py`（3 函数，存储介质自相矛盾：save 写 SQLite / load 读 Parquet）、`data/ingest/akshare_adapter.py`（2 个并发批量函数）、`api/v1/market.py::_build_sectors`、`data/realtime.py::_fetch_ths_fflow`+`_cn_amount_to_float`、`data/quotes_hub.py`（2 个观测/投递函数）、`ml/train_lgbm.py::daily_ics`、`trading/paper.py::_exec_price_for`、3 处 F841（`etf.py::last`、`ma_cross.py::day_idx`、`monitor.py::label`）、5 处测试/脚本未用 import/局部 | **独立复核方式**：本报告"死代码"清单**不能直接照删**——我自建 AST 孤儿扫描（`backend/.tmp_probe_s4/orphan_scan.py`，297 文件/1069 顶层符号），结果与报告**三处不一致**：①`stamp_duty_rate`/`check_pct_limit` **是活的**（分别被 `ma_cross.py:185`+6 处断言、`ingest/validate.py:72` 调用）⇒ 未删；②`write_year_batch` 报告判定正确（**我上轮据粗 grep 的"纠正是错的"**：`app/` 内 5 处=定义+`__all__`+一句"原实现曾用它"的注释，`scripts/` 0 处，`tests/` 62 处 ⇒ 零生产调用方），但它同时是 53 处测试夹具的写入器 ⇒ **登记为待裁决而非直删**；③扫描额外发现 5 项报告未列的死代码（已删）。**不删的三类**：ORM 声明类（由 `Base.metadata` 注册，删=改 schema）、公告/财务/风格类"有读者无写者"的待接线能力、仅测试引用的能力（共 27 项，登记在 `P17-orphan-scan.md`）。**连带迁移**：`test_data_prep.py` 夹具改用生产写入器 `write_year_batch`（迁移前先证伪"写入即不可见"：`cross_section._source_files` 用 `rglob` ⇒ 镜像路径其实看得见 `all.snappy.parquet`，报告该说法只对 `read_symbol_dataset` 成立） | 新增 `backend/tests/test_dead_code_removed.py` **29 条**（成对锁"删的不复活/活的不误删"：模块属性 + 全 `app/` **AST** 引用扫描为空 + `__all__` 不导出 + `/metrics` exposition 里不得出现死指标）；`tests/test_data_prep.py + test_p1_data.py` → **22 passed**；受影响 8 个既有文件（stock_panels×2/hotpath_watchlist/pipeline_lock/etf/etf_instruments/etf_flow/read_endpoints_rbac）→ **186 passed, 3 skipped**；全量见套件行。完整台账：`docs/audit-2026-09-18/P17-orphan-scan.md` |
| 54 | **§8.2 第 20 项 CI/交付门禁（mypy 17→0、`.dockerignore`、`.gitignore`、lint 全仓清零）** | ①`mypy app/` 原有 **17 error/4 文件** ⇒ 0：`data/ingest/etf_instruments.py:27-34`（polars schema 用**类** `pl.String` 应为**实例** `pl.String()`）、`api/v1/screener.py:304-310`（`stats_block`/`today_stats` 因 `data.get` 返回 `Any` 无法收窄 ⇒ 显式 `dict` 标注，行为不变）、`core/logging.py:87`（`# type: ignore[arg-type]` 码不匹配实际 `[call-overload]`）、`api/v1/market.py:96`（`out` 由首个 dict 字面量推成 `dict[str,str]` ⇒ 标 `dict[str, Any]`）；②新建仓库根 `.dockerignore`（所有服务 `context: .`）；③`.gitignore` 补 `backend/.tmp_*/`、`qa_browser/`、`data/_purged_*/`、`.workbuddy-ai/`；④`ruff check app tests scripts --select F,E9` 从 **27 处**（tests 13 + scripts 14）⇒ **All checks passed**（**范围扩大的即时收益**：收口后随即又抓到本轮某子代理新测试文件里的 5 处 `F401`+`F811`——`test_promote_basis_comparability.py:29` 死 import + 4 处同名重复定义；按旧范围〔只 `app/`、只 3 条规则〕这类问题在 CI 里**永远不可见**） | **构建上下文实测**（加 `.dockerignore` 的量化依据）：`.git` 26.3MB + `backend/.venv` **4700.3MB** + `data` **2259.3MB** + `frontend/node_modules` 149.3MB + `backend/logs` 15.0MB + `.mypy_cache`×2 115.4MB + `backend/.tmp_testrun` 13.3MB + `qa_browser` 5.5MB + `docs` 7.6MB ⇒ **合计 7292.1MB = 7.12GB**，且 `.env`（0.8KB，含真实凭据）此前会一并进入构建上下文。**"禁止误排除"由测试锁住**：`.dockerignore` 若排除任何 Dockerfile 要 COPY 的路径（requirements/app/scripts/tests/pytest.ini/frontend 各配置）必须报红；`!.env.example` 例外必须写在排除**之后**才生效（同锁）。⚠️ 报告另称"`backend/Dockerfile:56-57` 的 HEALTHCHECK 被 compose 覆盖成死配置"——**已证伪**：compose:75-85 已用**同一 URL** `/health/ready`（`-fsS`），注释即 P1-43/DEP-10 的修复记录；镜像内那条对 `docker run`（无 compose）仍有效，故不改，改为**加锁**防再次分叉 | 新增 `backend/tests/test_ci_gate_hygiene.py` **9 条**：`.dockerignore` 存在且关键排除命中（pathspec 语义）＋**16 条必须保留路径逐条不得被排除**＋`.gitignore` 四项＋镜像/compose 健康检查 **URL 必须一致且该 URL 真是 FastAPI 路由**＋前端 `/healthz` 在 `nginx.conf` 有 location＋**`scripts/` 引导段必须真能执行**＋全部脚本可编译 → **12 passed**；`mypy app/` → **Success: no issues found in 129 source files**；`ruff check app tests scripts --select F,E9` → **All checks passed!**。⑤**工作流门禁（本轮追加，实测三处"门禁从不生效"）**：**(a)** `ci.yml` 原写 `push: branches: [main]`，而本仓 `git branch -a` 只有 **`master`**/`origin/master` ⇒ **主干推送从不触发 CI**（整套 pytest/mypy/ruff/前端 build 形同不存在）⇒ 改为 `[main, master]`；**(b)** ruff 步骤原为 `ruff check app/ --select F401,F811,E9`（只查 `app/`、只 3 条规则）⇒ `tests/`+`scripts/` 的 27 处问题（含 `scripts/alerter.py` 的 F821）**永远看不见** ⇒ 收口为已验证清零的 `ruff check app tests scripts --select F,E9`；**(c)** `nightly.yml` 的 Playwright E2E 原为 `pytest tests/test_e2e.py -v || echo "E2E non-blocking"` ⇒ 整步 exit 0、**失败也显示绿色**，夜间 E2E 从不上报问题 ⇒ 改用 `continue-on-error: true`（作业不红，但该步在 UI 标红并留失败日志）。三条均由测试锁定（分支过滤必须含真实默认分支、ruff 范围不得收窄、工作流不得出现 `\|\| true`/`\|\| echo`）；YAML 解析复验通过 |
| 55 | **新发现（P1，本轮实测复现）：`scripts/alerter.py` 必然 NameError —— 运维告警脚本 100% 不可用** | `backend/scripts/alerter.py:19-21`（`if sys_path not in sys.path: sys.path.insert(...)` **用了 `sys` 却从未 `import sys`**） | **缺陷本体实测复现**：`python scripts/alerter.py` ⇒ `File "scripts/alerter.py", line 20, in <module> ... NameError: name 'sys' is not defined`。**触发路径是活的**：`README.md:203` 明确写着 `python scripts/alerter.py` 是运维操作步骤；`B0-deploy.md:238-239` 把它列为 `NOTIFY_ENABLED`/`NOTIFY_WEBHOOK_URL` 的消费方。**为何长期漏网**：历史审计已记为 LOW-001（`docs/audit/AQP_最终项目综合评审.md:261`）但一直未修，而本仓此前**没有 lint 门禁**（`ruff F821` 一眼可见）—— 正是第 20 项要解决的问题。修法：补 `import sys`；`ruff --select F821` 由 2 处 ⇒ 0 | **修后复现**：脚本越过引导段进入业务逻辑（随后 `PermissionError [WinError 5]` 来自 loguru `enqueue=True` 需要的命名管道，是**本沙箱限制**、非产品缺陷）；引导段探针实测 `sys in ns` 且 backend 根已入 `sys.path`。反向锁：`test_ci_gate_hygiene.py::test_scripts_bootstrap_has_no_undefined_name` 真执行引导段（`compile()` 只查语法抓不到此类错），并新增 `test_all_scripts_compile` 覆盖全部 `scripts/*.py` | ✅ 已验证（`test_ci_gate_hygiene.py` 9 passed；全仓 `ruff F,E9` All checks passed） |
| 56 | **§8.2 第 7 项 F7/F4/F10/A2 静默修复（子代理产出，父审核员独立复跑验证）** | `api/v1/ops.py:19-20,461-478`（新增失败语义）、`ops.py:27,59,82-85`（`_SCAN_SYMBOL_LIMIT=200` + 披露）、`api/v1/datacenter.py:568`（`Query(50, ge=1, le=10000)`）、`:609`（`Query(60, ge=1, le=500)`）、`frontend/src/api/production.ts:104-107`、`frontend/src/pages/DataQuality/index.tsx:136-144` | **F7（假成功）**：非法日历日期此前返回 `code=0` + `data.ok=false`（且异常被吞在流水线内）⇒ 现在**在流水线之外**解析日期 ⇒ `40000`；`PipelineBusy` ⇒ `40900`（不再静默排队）；其余异常交全局处理器 ⇒ `50000` + 服务端堆栈。**F10（limit 无界）**：实测修前 `limit=-1`/`0`/`1e9` **全部 `code=0`**，其中 `/logs?limit=0` 真会返回**全部**日志 ⇒ 两处 `Query` 加上下界（`/desk/orders` 为第三处，见行 61 残余）。**F4（静默截断）**：`symbols_available`/`total_symbols`/`truncated`/`scan_limit` 披露，前端消费并把"截断扫描的通过"限定为"已扫描窗口内通过"、0 覆盖时显示"不能判定无质量问题"。**A2 证伪**：其唯一定义即 `quotes_snapshot` 200 截断，已由第 12 轮 P1-15 修复 ⇒ 无需另修。**变异反证 3 组**（还原旧实现 ⇒ `40000`/`50000`/`40900` 三条断言全红、前端 `KeyError: truncated`） | 新增 `tests/test_ops_silent_success_fixes.py`（7）+ 扩写 `test_datacenter_instruments_total.py:118-170`；**父审核员独立复跑**：`test_ops_silent_success_fixes.py + test_trading_fee_single_source.py + test_registry_rollback_and_windows.py` → **53 passed** |
| 57 | **§8.2 第 8 项 模型回滚/幂等/门禁可比性/regime 窗口（子代理产出，父审核员独立复跑验证）** | `ml/registry.py`（`_same_file`:346、`_copy_artifacts`:364、`_install_as_production`:389、`rollback_model`:462、`regime_windows`:600、`regime_check`:712、`lineage_comparability`:765、策略字段 87-107、regime 落库 244）、`ml/train_lgbm.py:40,86-106,339-358,386-435`、`scripts/promote_model.py:9-17,34,68,95-108`、`scripts/retrain.py:133-140` | ①**P1-7**：等路径拷贝先复现 `shutil.copy2(p,p)` 的 `SameFileError`，修后重复 promote/`a→b→a` 幂等（`prod` 唯一性不变量不变）；②**P1-6 经复核为"未修"并本轮补上**：`rollback_model()` 要求目标**曾被提升过** + 有当前生产 + `reason` 必填 + delta/`gate_decision_if_promoted` 全进 `checks.rollback` + 重复回滚幂等；CLI 无 `reason` ⇒ exit 1；③**R14**：血缘不可比 ⇒ 三分支（显式 opt-in / 换 regime 判据 / 拒绝），避免"修完仍冻结"；④**T8 部分修**：valid 段切 4 个连续窗口（披露边界/n/均值/符号 + `threshold=max(0.002,2·SE)`），`daily_ic_pairs` 带日期口径落 `params_json.regime_windows`，`enforce_regime_gate=True` 或 R14 替代分支时硬判据。**父审核员抽查不变量**：`DEFAULT_PROMOTE_POLICY.rank_ic_tolerance=0.0`/`icir_tolerance=0.0` **未被放松** | 新增 `tests/test_registry_rollback_and_windows.py`（24）；子代理回归合并 11 文件 **151 passed**；**父审核员独立复跑**三文件 → **53 passed**；真实 CLI 端到端探针（重复 promote exit 0；`--rollback --reason` 成功且记录 `delta_valid_rank_ic=-0.01` 与 `gate_decision_if_promoted.promote=False`） |
| 58 | **§8.2 第 18 项 费率单一来源（子代理产出，父审核员独立复跑验证）** | 新建 `domain/trading_rules.py:25-36`；改引用：`backtest/broker.py`、`backtest/ma_cross.py`、`backtest/engine.py`、`backtest/strategy_base.py`、`trading/paper.py`、`trading/portfolio.py`、`api/v1/backtest.py:394`、`db/models.py:125-126`、`domain/a_share_rules.py:10-15,152-153`（分档函数**未改写**，改为复用） | 收敛 6 个常量到唯一来源（`COMMISSION_RATE_DEFAULT=0.0003`/`COMMISSION_MIN=5.0`/`STAMP_DUTY_STOCK_RATE=0.0005`/`_LEGACY=0.001`/`CUT_DATE=2023-08-28`/`TRANSFER_FEE_RATE=0.00001`）。**数值逐位不变**：golden 对比 `diff_count=4`，4 条全部是"有意删除的本地副本名"，**所有计算数字逐位相同**（`stock_2024_sell_cost=80.18999999999998`、`etf_2024_sell_cost=29.699999999999996`、`engine_2020_friction nav_sum=11.925276233715007`）。AST 守卫（费率字面量只许出现在 `trading_rules.py`）+ 变异反证 | 新增 `tests/test_trading_fee_single_source.py`（22）；子代理合并回归 **77 passed**；**父审核员独立复核**：`grep` app/ 该批字面量现仅存于 `trading_rules.py`（`broker.py` 仅余注释/docstring），三文件复跑 **53 passed** |
| 59 | **§8.2 第 19 项 前端 B9a/B8/B9b/B9c + P1-22/P1-24/P1-23（33 项；子代理产出，父审核员独立复跑验证）** | `frontend/src/**`（未触碰 `backend/app/**`）：`pages/StockDetail/index.tsx:55-70`（请求序号守卫 + 切标的前清空旧状态）、`MarketOverview/KpiCards.tsx`（按 `code` 匹配核心指数，去掉伪造 loading）、`MoneyFlowPanel.tsx`（删后端从不返回的「走势」列）、`Watchlist/index.tsx`（降级横幅）、`Screener/index.tsx`（导出按钮按 researcher 门控）、`hooks/useWatchlistQuotes.ts`（序号守卫）、`ui/index.tsx::ErrorState`（不再吞后端消息）、`pages/Backtest/{index,parts,TopKPanel}.tsx`（搜索/回测/优化界 + AbortController + 成本口径面板 + 结果缓存）、`Portfolio/index.tsx`（搜索中止与失败可见、`benchDegenerate`）、`Research`/`CapacityAttribution`/`Report`（各 `seqRef`）、`DataCenter/index.tsx`（**hooks 顺序白屏**、二次确认、角色门控、`autoSync=null` 未知态、`DiskGauge` 提升）、`Pipeline/index.tsx`（去掉不存在的 `desk` stage、状态分档）、`OrderDesk`（`kill==null` 显示不可读且禁用解除熔断）、`Alerts`（`markAllRead`/未读上限标注/删除二次确认）、`Settings`（滑条删除、按角色门控、标签修正）、`utils/useChart.ts:19-20` 依赖修正、`stores/useAuthStore.ts`（移除 `AQP_ADMIN_TOKEN` 旧迁移） | 前端无测试框架 ⇒ 以 **`npx tsc --noEmit` + 源码锚点断言**验证；**父审核员独立复跑**：`npx tsc --noEmit` → **0 条 `error TS`，EXIT=0**；`backend/tests/test_frontend_b9_fixes.py` → **32 passed**。**父审核员另亲自读码确认最高危一条**：`DataCenter/index.tsx:72-118` 的 `useMemo`+`useChart` 已无条件调用、`percent == null` 的早退**排在 hooks 之后**（原状是 null 分支先 return ⇒ hooks 3↔0 ⇒ 根级 ErrorBoundary 整站降级）。**残留（需后端配合，已单列）**：`/desk/orders` 无 `total/truncated`（前端只能声明"最近 200 条窗口"）、`/alerts/events` 无 `unread_total`（未读只能标"≥N"）、`ops.py:54` 默认年份硬编码 `2026`、DataCenter 的 `TrainPanel` 对 viewer 整块不渲染（其实只读可用） | 子代理新增 `backend/tests/test_frontend_b9_fixes.py`（32，一个改动点一条锚点，禁止泛化断言）→ 父审核员复跑 **32 passed** |
| 60 | **§8.2 第 6 项 退市数据接线（子代理产出，父审核员独立复跑验证）** | `data/ingest/tasks.py:142 enrich_delist_dates`（惰性 import + **永不抛**；`availability ∈ {unavailable, empty_source, no_local_match, ok}`）、`:101 delist_status_path`、`:63 upsert_delist_dates`（幂等，`list_date` 只补空不覆盖）、`orchestrator.py:321 step_enrich_delist` + `:526` 进 `FULL_STEPS`（在 `validate` 之后、`build_universe`/`build_universe_bt` 之前 ⇒ 当晚生效）、`data/universe.py:527`（bt 面板输出 `delist_date`，+`:346` 显式 cast Date；旧分区缺列仍可读）、`api/v1/backtest.py:167/193/267/284`（面板列状态 `present/mixed/absent/no_panel` + 回填源可用性并入 note）、`data/ingest/akshare_adapter.py:323`（`fetch_delist_list` 增 `list_date`，B5-14 部分回填）、`scripts/enrich_delist.py`（改为复用同一实现，消灭第二套逻辑） | **缺陷本体（复核确认与报告一致）**：`instrument` 5552 行、`delist_date` 非空 **0**、`list_date` 非空 122（2.2%）；`universe_daily_bt` 5 个年分区**都没有** `delist_date` 列 ⇒ §7-S2 所述"按 delist_date 剔除"结构上不可能发生；唯一调用方是手工脚本 ⇒ **接线**而非新功能。修法遵循"唯一实现 + 优雅降级"：源不可用 ⇒ `availability=unavailable` 且**永不抛**（不阻塞晚间流水线），"空源"与"没有退市股"可辨（`empty_source`）。**父审核员独立复核**：`test_delist_wiring.py` → **9 passed**；接线点行号抽查属实（`orchestrator.py:321/526`、`tasks.py:63/101/142`、`universe.py:527`）；`test_pipeline.py` 期望顺序已**扩展**而非放宽；相关 7 文件联合跑 → **86 passed** | 新增 `tests/test_delist_wiring.py`（9：无源降级可辨 / 写入幂等可重跑 / 覆盖度=真实计数 / 旧 schema 分区仍可读且面板携带该列）；子代理 18 文件联合跑 **149 passed**；**父审核员复跑** `test_delist_wiring + test_universe + test_pipeline + test_sync_integrity + test_pipeline_state_truth + test_p1_data + test_api` → **86 passed** |
| 62 | **§8.2b 契约类剩余条目（P1-33 / P1-37 / P1-38 / P1-48 / `ERR_EXPR_INVALID` / 三个对象形状端点的截断披露 / R10 单点；子代理产出，父审核员逐条核验）** | ①**P1-33**：`services/market_service.py:41-50`（先校验 `资金方向` 列、再 `df[资金方向=="北向"]` 求和；缺列/无北向行 ⇒ 记 errs 走 degraded，**不再全表求和**）；②**P1-37**：`ml/train_lgbm.py:258` 默认 `xsec_demean=True`（`scripts/train.py`/`grid_search.py` 不传即继承）+ **新发现的配套守卫** `:43,325-341`（`_MIN_XSEC_NAMES=2`：只对宽度≥2 的交易日去均值，否则按绝对口径并**落盘生效口径**）；③**P1-38**：`train_lgbm.py:159-206` `select_features_by_ic(..., dates=)` 改**逐日截面 RankIC 均值**（与 `daily_rank_ic` 同口径，min_samples=10；逐日无定义时退回池化）+ `:438` `train_basis.selection.ic_basis="daily_xsec_rank_ic"` 留痕；④**P1-48**：`train_lgbm.py:445-470` 落盘 `pred_level{...}`、`registry.py:254/589`（注册表落盘 + `prediction_level()` 读取器）、`infer.py:61 prediction_return_basis()`、`predict.py:166` 响应披露 `return_basis`；门禁 `registry.py:782 pred_level_check()` + `:115` 策略字段 + `:1083` 接入 `evaluate_candidate`（**默认只披露 `enforced=False`**，与 T8/R14 同范式；置 True 即硬判）；⑤**53001 首次有真实 raiser**：`api/v1/studio.py:332,347` 非法表达式 `fail(ERR_EXPR_INVALID, ...)`（此前 40000；既有可接受码集合本就含 53001 ⇒ **零放宽**）；⑥**截断披露**：`api/v1/etf.py:326`（`/etf/hot`）、`:484-489`（`/etf/flow`，数据源不回传总量 ⇒ `total=None` + `truncation_basis` 如实说明）、`api/v1/datacenter.py:640`（`/logs`）均**只加键不改形状**；⑦**R10 单点**：`api/v1/market.py:407 _LABEL_PRICE_BASIS` + 6 条返回路径全部携带（`ai_stats` 的口径字段不再"一降级就整块消失"） | **P1-33 的实测依据**（B7a）：全表求和会把港股通（南向）计入"北向净流入"，实测 **840.0 亿 vs 真实 0.0**（直接显示在前端卡片）。**报告"量纲待外网复核"经源码核实=无需改**：akshare `stock_hsgt_em.py:88-90` 已把 `资金净流入` ÷10000 ⇒ 亿元，与前端 `KpiCards.tsx:11,74`/`MoneyFlowPanel.tsx:12` 的"亿"一致；`主力净流入-净额` 保持元、代码 ÷1e8 ⇒ 同为亿元 ⇒ **两侧口径一致，不存在量纲错配**（无需外网）。**P1-37 的连带发现（重要）**：默认改 True 后，**单标的/极薄截面**的合成数据标签会被"减当日均值"整体抹成 0（`test_api`/`test_pipeline` 一度 **30 例 error**）⇒ 子代理判定"是我的改动错了"并**改代码**（加宽度守卫）而非改测试；**父审核员逐条核验**了上述 7 处改动点确实存在且语义如述。**未改形状的两处（有意跳过）**：`stock/search`、`portfolio/search` 仍是裸 list——前端按**数组**消费（`api/stock.ts:16 SearchHit[]`、`api/portfolio.ts:11-12 AssetSearchItem[]`），改对象会破前端 ⇒ 登记为产品决策而非"统一形状" | 子代理定向复跑：`test_pipeline_state_truth` 34 / ML+registry 相关 130 / lab+error-governance+write-smoke+market-quotes 186 / `test_api`+`test_pipeline` 30 / etf+datacenter+deadbranch+lab 37 / promote_basis 19 全绿；**唯一一处既有断言改写是"收紧"**（`test_dead_branch_and_empty_data_codes.py:103-128`：原断言 `"label_price_basis" not in out` 把"口径只在 ok 路径返回"这个**缺口**固化成契约 ⇒ 改为断言降级路径也携带该口径 **且**新增 `status=="unavailable"` 正面断言；父审核员**亲自读码确认**改写与理由，见该用例 docstring 自述）。`mypy app/`=**Success(129)**、`ruff app tests scripts F,E9`=**All checks passed**（均由父审核员事后复跑；期间另修掉该子代理**自己**引入的 5 处 F401/F811 与另一子代理引入的 4 处 mypy 错） | `api/v1/desk.py:213`（`limit: int = Query(50, ge=1, le=1000)`，F10 第 4 处）、`api/v1/research.py:343`（`top_k: int = Query(12, ge=1, le=1000)`） | **F10 第 4 处**：`/desk/orders` 的 `limit` 此前无界（`limit=-1`/`0`/`1e9` 全 `code=0`）。**新发现**：`/research/feature-importance?top_k=-1` 此前无任何校验 ⇒ `np.argsort(...)[:−1]` 产生"除最后一个"的怪异语义、`top_k=1e9` 静默返回全部特征。**同时证伪两处**：①`/screener/stocks` 的 `page`/`page_size` 走函数内 `ERR_PARAMS` 中文校验（**不是**无界缺陷；父审核员一度加上 Query 边界，结果打破 3 条既有消息断言 ⇒ **已还原**，改为承认"有界 **或** 有显式校验"的等价判据）；②`/etf/list` 的 `page` 本就有 `ge=1`。**未改形状的产品决策**：`/desk/orders` 仍是裸数组（改 `{items,total,...}` 会破前端 `PaperOrder[]`；前端已披露"最近 200 条窗口"）、`/alerts/events` 同理无 `unread_total` | 新增 `tests/test_query_limit_bounds.py`（9）：①OpenAPI 层不变量——`limit`/`top_k`/`page_size` 必须**上下界齐备**，`page` 必须 ≥1，否则要求**同模块内存在同行的 `ERR_PARAMS` + 参数名显式校验**（自动判定，非手写豁免名单，避免永久后门）；②行为层——`/desk/orders` 非法 limit ⇒ HTTP 200 + `40000`，合法 limit ⇒ `code=0`；③`/research/feature-importance` 非法 `top_k` ⇒ `40000`。**变异反证**：临时撤掉 `desk.py` 的边界 ⇒ 该文件 **4 条断言全红**（含 API 形状不变量），恢复后 **9 passed**。事前还修掉了我自己那版内省探针的错（Pydantic v2 约束在 `metadata` 的 `Ge`/`Le` 里，不在 `FieldInfo.ge`——错版探针把**全部** 15 个站点误报为无界） | **9 passed**（含 4 条行为 + 5 条不变量）；`test_screener_stocks.py + test_read_endpoints_rbac.py + test_query_limit_bounds.py` → **152 passed, 3 skipped**（还原后零红灯）；ruff/mypy 对两个改动文件**全绿** |
| — | **第 12 轮"测试脆弱性"修复（非产品缺陷，如实单列）** | `backend/tests/test_overview_heal_chain.py`（`_Clock` 增 `freeze=True`：把真实时间源钉死在构造时刻，只保留 `advance()` 语义；`test_redis_ttl_semantics_on_degraded_path` 改用它） | **抖动机理**：该用例断言**精确**剩余 TTL（`ttl(key) == 100`、`advance(40)` 后 `== 60`），而 `app/cache/memory.py:66` 的 `lru_ttl` 对剩余量做 **`int()` 截断**，用例原先的 `_Clock` 是"**真实时间** + 偏移"（非冻结）⇒ 写入与读取之间跨一个时钟刻度就把 `100` 截成 `99`。本机实测 `time.time()` 刻度中位 **7.604 ms**（最小 0.62ms），真实路径复现：`lru_set`→`lru_ttl` 立即读取 **3/3000 = 0.10%** 返回 ≠100（冻结时钟对照 **0/3000**）⇒ 单次全量套件约 0.1% 概率假红，且**套件负载越高越易命中**（这正是它在第 12 轮首次全量运行时报红、单跑与 68 文件前缀运行均通过的原因）。**判定**：产品侧 `int()` 截断与 Redis `TTL` 语义一致（非缺陷）；缺陷在**测试依赖真实时间**。**修复不放宽任何断言**（`== 100`/`== 60`/`-2` 逐字未改），只去掉真实时间漂移 | ✅ 已验证：`test_overview_heal_chain.py + test_swr_cache.py` 23 passed；目标用例**重复 30 次 0 失败** |
| — | **第 7–8 轮"测试脆弱性"修复（非产品缺陷，如实单列）** | `backend/tests/test_read_endpoints_rbac.py`（对非 JSON 响应不再无条件 `r.json()`；新增专条固化 xlsx 断言） | `test_read_endpoint_allows_authorized[/api/v1/export/screener]` 无条件 `r.json()`：该端点**有数据**时返回 **xlsx（ZIP）二进制** ⇒ `json.loads` 抛 `UnicodeDecodeError: ... byte 0xc7`；**DATA_ROOT 为空**时走 `ERR_DATA_EMPTY` 的 JSON 降级路径 ⇒ 用例"碰巧"通过。因 conftest 的 DATA_ROOT 是**会话共享**临时目录，该用例会随**同批文件集合/顺序**漂移（实测：单跑 50 passed；紧跟 `test_api.py` 后 1 failed）。已固化为正确断言（HTTP 200 + 非空 + xlsx 必须 `PK` 魔数） | ✅ 已验证 |
| 44 | **B7a-08 空数据被报成裸 50000 + B7a-09 不可达死分支** | `backend/app/api/v1/research.py::stress_test`（`universe_daily` 无任何 parquet ⇒ 显式 `ERR_DATA_EMPTY(51001)`，与同函数 `:545` 同口径）、`backend/app/api/v1/market.py::_build_ai_stats`（删掉 `else "raw_fallback"`，OK 路径恒 `"hfq"`），新增 `backend/tests/test_dead_branch_and_empty_data_codes.py` | **B7a-08**：`pl.concat([])` 抛未捕获 `ValueError` → 全局兜底成 **裸 `code=50000`（"系统故障"）**，而真相是"本地还没有数据"（全新部署即触发）；同函数另一处空数据早已是 `ERR_DATA_EMPTY` ⇒ 口径自相矛盾。**B7a-09**：`"hfq" if hfq_files else "raw_fallback"` 的 else **不可达**（`:437-439` 无预测早退、`:456-457` 无 hfq 早退，都在该返回之前；全仓 `rg raw_fallback` 无消费方）⇒ 一句**永不兑现的降级承诺**（且在 `P5` 的 D12 里被当成可选口径值列入披露矩阵）。**独立复核**：先跑 `pl.concat([])` 证明原写法抛的**不是** `AQPException`（缺陷本体可复现），再断言端到端必为 51001 且消息含 `universe_daily`（**防止只断言 code 造成"两处早退都抛 51001"的假通过**） | 5 条用例（原写法非 AQPException、端到端 51001 且指明基准缺失、无 hfq 时**更早**的早退仍生效（顺序锚点）、OK 路径不再存在 `raw_fallback` 取值（源码扫描：只允许出现在注释里）、unavailable 分支不得携带 `label_price_basis`） | ✅ 已验证 |

### 本轮的自查（**证伪**，不计入发现数）

| 假说 | 核查方式 | 结论 |
|---|---|---|
| `CapacityAttribution/index.tsx` 16 处 `fmtPct` 未 ×100 ⇒ 全页百分比小 100 倍（与 P1-26 同类） | `read` 该文件 `:103-104` | **证伪**：该文件在 `:103` 定义了**局部** `fmtPct`，其实现是 `(v * 100).toFixed(digits)` ⇒ 本页 `×100` 已内含，**不是缺陷**。（`desk.py:305-307` 权重确为分数、API 确不 ×100，但展示层已正确乘） |
| 同上 → 顺带发现"同名函数语义相反"的维护风险 | 对比两处定义 | **成立但属 P3/一致性**：局部 `fmtPct` 吃**小数**、`utils/format.ts` 的共享 `fmtPct` 吃**百分数**，同名反义；`TopKPanel` 的 P1-26 正是这一类漏乘。建议后续重命名（不属本轮修复范围，未改，以免扩大回归面） |

### 第 15 轮的自查（**自纠/证伪**，不计入发现数）

| 假说 / 我的初版做法 | 核查方式 | 结论 |
|---|---|---|
| 我最初的机制假说：`g1_*` 随面板成员变化是因为 **`A` 的行归一化分母**只由节点集决定 ⇒ 冻结节点集即可修好 | 探针 `backend/.tmp_probe_s4/probe_p118.py`：面板 ABCD 去掉 D，分别用 `universe=None` 与 `universe=[A,B,C,D]` | **假说被证伪**：两种取值**逐值相同**（A 的 `g1_` 均为 2.5）——`propagate` 里 `agg=num/den` 用同一行归一化权重，**按行约掉**。冻结节点集在此路径上**毫无作用** |
| 修正后的机制：决定 `g1_` 的是"哪些邻居当天**有取值**" | 探针 `probe_p118b.py` / `probe_p118c.py` | **成立**：①同桶新标的入池 ⇒ A 的 `g1_` 3.0 → **252.0**；②已冻结标的行情消失 ⇒ 3.0 → **2.5**。前者可用"非 universe 不入图"挡住（3.0 保持），后者**不可复原** ⇒ 只能拒绝静默改写 |
| 初版把上述两条都当"可修" | 用 `read_all_symbols(skip_empty=True)` 的语义核对（"被隔离标的留空目录"⇒被跳过） | **自纠**：真实触发路径是**分区被隔离**，取值已落 `data/quarantine`，找不回来 ⇒ 修复目标从"保持数值不变"改为"**拒绝静默改写 + 披露 + 显式授权**"，并把"数值不变的边界"写成测试 |
| 初版断言"冻结后 `test_feature_incremental` 的 stale-frontier 场景照旧通过" | 跑既有用例 | **自纠**：该用例 monkeypatch 掉 A 的**全部**数据（强于其 docstring 声称的"单分区隔离"）⇒ 正是新策略要拒绝的情形。**未放宽守卫**，改为在该用例内补 2a「默认拒绝且不改写（mtime 比对）」+ 2b「显式 `FEATURE_ALLOW_GRAPH_DRIFT=1` 后再验增量合并」 |
| 反向锁「app/ 内不得有未传 `universe` 的 `apply_propagate` 调用」 | 首版用字符串匹配 | **自纠**：`predict.py:57` 命中的是 **docstring 里的示例调用**（假阳性）。改为 AST 找真实 `Call` 节点 |

### 第 17 轮的自查（**自纠**，不计入发现数）

| 我的初版做法 | 核查方式 | 结论 |
|---|---|---|
| 新测试用 `monkeypatch.setenv("DATA_ROOT", …)` + `get_settings.cache_clear()` 切数据根 | 把新文件与 `test_api.py::test_screener_filters_and_order`、`test_alerts.py::test_score_topk_enter_and_leave` 同批跑 | **自纠（实锤污染）**：`cache_clear()` 后的 lru_cache 里留下**指向 tmp_path 的 Settings**，而 teardown 只还原环境变量 ⇒ 同批后续用例读到空数据根，两条既有用例被我的测试打成红灯（单跑各自 2 passed）。已全部改为仓库既有惯例 `monkeypatch.setattr(get_settings(), "DATA_ROOT", …)`（32 处同款），**并回头修掉第 15 轮同样写法的** `test_graph_universe_freeze.py`（同一隐患） |
| 缺陷本体反证用 `legacy = dict(body)` 复制载荷 | 同批重跑 | **自纠**：`_finalize_screener_payload` 是**就地改写**入参（long-standing 设计），浅拷贝让 `status="unavailable"` 一并带过去 ⇒ 命中函数开头的早退分支。改为**重新取一份** `_screen(...)` 载荷再删字段 |
| 初版把 `bse` 判定写成"板块为空 ⇒ 一律 `unavailable`" | 读 `filter_universe`/`enrich_items` 取数链 | **自纠（设计层面）**：`total==0` 有两种成因且都可判定，一刀切会把"真跑了但当日无匹配"（09-14 有意设计）误报为数据不可用 ⇒ 改为按 `board_rows` 分流（见行 52），并把"`board_rows` 未知 ⇒ 不臆断"写成用例 |
| 用「行首」当锚点插入表格行（本轮两次） | 插入后立刻校验每行 `^\|…\|$` | **自纠（我自己的文档操作事故）**：两次把相邻轮次行的行首吃掉（行 52 / 套件对账行）。均已即时修复并复查：表格 0 条结构异常、被吃掉的第 12 轮行 798 字符完整。"改文档锚点必须用**行尾/唯一短串**"这条纪律再次被验证 |

### 第 18 轮的自查（**自纠**，不计入发现数）

| 我的初版做法 | 核查方式 | 结论 |
|---|---|---|
| 上一轮我断言"报告错了：`write_year_batch` 是生产活的" | 本轮 AST 精确分域计数（`app/` vs `scripts/` vs `tests/`） | **自纠（判断翻转，且方向是我错）**：`app/` 内 5 处 = 自身定义、`__all__`、以及 `ingest/tasks.py:252` 一句"**原实现**用 `write_year_batch` 整年覆盖…"的注释；`scripts/` **0** 处；`tests/` **62** 处 ⇒ **报告"零生产调用方"是对的，我上一轮的"纠正"错了**。教训：**"有引用"必须在正确目录域内计数**，否则把测试夹具当生产调用方。处置：保留该函数并登记待裁决（53 处夹具写入器） |
| 首版孤儿扫描输出"115 个零引用符号" | 抽查其中 `auth_login`/`etf_list`/`health_ready` 等 | **自纠（92/115 是假阳性）**：FastAPI 路由处理器靠 `@router.get` **装饰器注册**，本就没有名字引用 ⇒ 加"有 `decorator_list` 即视为存活"过滤后降到 **23**，再排除 ORM 声明类得 **14** |
| 首版反向锁用**子串**匹配 + `dict` 内置名当"必须保留的邻居" | 跑新测试（7 条红） | **自纠（我的测试错，非产品错）**：①`publish` 被子串命中 `publish_threadsafe`/`_publish_now`、`path_for` 命中 `path_for_year`、连我自己的 docstring 也被算成"引用" ⇒ 改为 **AST 精确匹配**（只认 Name/Attribute/import/`getattr` 字面量，排除 docstring）；②`panels.py` 里没有 `stock_panels`（真实公开函数是 `build_quote`）、`check_pct_limit` 在 `app.data.**ingest**.validate` ⇒ 三处事实错误由测试失败暴露并改正 |
| 用 `'return ok(' not in 源码片段` 去"验证"子代理 F7 是否修好 | 复查该判据 | **自纠（判据无效）**：成功路径本来就有 `ok(...)`，该表达式为 False 是**预期**，不能证伪任何东西 ⇒ 改为**自己复跑子代理的测试文件**（`test_ops_silent_success_fixes.py` 等三文件 → **53 passed**）+ 抽查可验证不变量（`DEFAULT_PROMOTE_POLICY.rank_ic_tolerance` 仍为 0、费率字面量只在 `trading_rules.py`） |
| 迁移 `test_data_prep.py` 夹具时准备按报告"`write_whole_symbol` 写出的文件读取路径不可见"直接改 | 读 `cross_section._source_files` | **自纠（报告结论只有一半对）**：该函数用 `rglob("*.parquet")` ⇒ **镜像路径看得见** `all.snappy.parquet`；"不可见"只对 `read_symbol_dataset`/`read_symbol_year`（glob `year=*.parquet`）成立。仍决定迁移到生产写入器 `write_year_batch`（夹具与线上同布局），但把这条**限定条件**写进文档而不是笼统采信 |
| 用 `edit` 删 `akshare_adapter.fetch_daily_bar_batch` 时只锚定了函数头（含 docstring 前半） | 立刻跑 `AST OK` 语法检查 | **自纠**：留下缩进孤块（会 SyntaxError）⇒ 随即补齐删除并复验 `ast.parse` 全通过。纪律：**删除整段代码要锚定"从定义到下一个定义"的完整文本或文件尾**，删完立刻做语法校验 |
| 采信子代理"ruff/mypy 已过"的自述（C 只声明跑了定向用例） | 我独立重跑 `mypy app/ --ignore-missing-imports` | **发现（子代理引入的门禁回归）**：C 的新代码在 `ml/registry.py:833/838/847/848` 引入 **4 个 mypy 错** —— `thr` 与上方 calibration 分支**复用了同一变量名**（函数级被推成 `float`），此处再赋 `None` 即 `assignment` 错；另两处是 `float(c_mean)` 无法穿过 `shift is None` 收窄。⇒ **第 20 项"mypy 17→0"一度被打回 4 错**。修法：该分支改用独立变量 `shift_thr`/`c_mean_f`/`p_mean_f` + 一处 `assert` 收窄（**数值语义逐位不变**，键名不变）⇒ `mypy app/` 重新 **Success: no issues found in 129 source files**，`test_registry_rollback_and_windows + test_promote_basis_comparability + test_model_registry` → **55 passed**。教训：**跨文件门禁（mypy/ruff/全量）只能由父审核员重跑**，子代理各自的定向用例覆盖不到 |
| 给自己的新测试加了"豁免名单"（`_PAGE_EXEMPT`）以避免红灯 | 重新审视"豁免是否会长成永久后门" | **自纠（判据设计）**：改为**自动判定的等价条件**——"Query 层有上下界 **或** 同模块存在同行的 `ERR_PARAMS` + 参数名显式校验"。这样既尊重 `screener.py` 的中文报错范式（实测若强行统一到 Query 边界，会打破 3 条既有消息断言），又不需要任何手写名单 |
| 用 `FieldInfo.ge/le` 内省"哪些 limit 无界" | 先看探针输出是否自相矛盾（已知有界的 `/datacenter/quality` 也报无界） | **自纠（探针错误）**：Pydantic v2 的 `ge`/`le` 在 `FieldInfo.metadata`（`annotated_types.Ge/Le`）里，**不是** `.ge` 属性 ⇒ 错版探针把 **15/15** 个站点全报成无界。改用 metadata 后正解：14/15 有界，仅 `/research/feature-importance?top_k` 真无界（已修）。若不复核探针本身，这版测试会**全是假阳性** |
| 全量套件首跑出现 **2 failed**（1645 passed），其中一条是子代理刚写的用例 | 用**字典序前缀**复现（只跑 `test_*.py` 中名字 ≤ `test_delist_wiring.py` 的 28 个文件） | **发现 ①（测试设计缺陷，非产品缺陷）**：`test_delist_wiring.py::test_coverage_fields_match_real_counts` 断言的是**全库**计数 `n_with_delist_date == 3`，而测试会话**共享同一个 SQLite**（conftest 只隔离 DATA_ROOT），且 `test_delist_liquidation.py:107` 会 `Instrument.__table__.delete()` **清空全表**后写入 `600099.SH`（带 `delist_date`）⇒ 前缀跑复现 `assert 4 == 3`（单独跑却是 9 passed，典型的顺序敏感假红）。修法：把"3"的作用域限定到本用例自己的 3 个 symbol（`_db_rows()` 本身就是按 `SYMS` 查的），同时**保留**"披露值 == 同源实测全库计数"这条真正的不变量 ⇒ 前缀跑 **252 passed**。⚠️ 这说明**子代理"单独跑绿"不足以证明用例稳健** |
| 另一条 full-suite 红灯 `test_write_endpoints_smoke.py::test_studio_factor_save_is_graceful` | 读失败断言与同文件兄弟用例 | **发现 ②（子代理的声称不完整）**：G 声明"既有可接受码集合本就含 53001 ⇒ 零放宽"，但那只对**同文件 :655**（非法表达式用例）成立；**:630** 的允许集合是 `(0, ERR_PARAMS, ERR_DATA_EMPTY)` ⇒ 非法表达式改判 `ERR_EXPR_INVALID=53001` 后该断言被打红。修法：把 53001 纳入 :630 的允许集合（**守卫内容不变**：不得 50000、不得静默入库），并补注释说明依据——同文件 :649 的 docstring **自己就写着**"有 features 时被 AST 白名单拒绝（40000/53001）" ⇒ 是契约对齐而非放宽。教训：**"我改的这一处没破测试"必须按文件全量核**，不能只看一处 |
| 又一次把不存在的测试文件名写进命令（`tests/test_studio_factors.py`） | pytest 立刻报 `file or directory not found`（且**没有** `N passed` 行） | **自纠（同类失误第 3 次）**：无 `N passed` 行即视为失败信号，不得读成"通过"。已按真实文件名重跑（151 passed） |
| 用"以 `\|` 开头但不以 `\|` 结尾"判定表格结构异常 | 逐条读被标记的行 | **自纠（探针假阳性类）**：本轮唯一 2 处告警都不是表格缺陷——`B4b-ml-train.md:757` 是数学记号 `\|IC\|`（绝对值）行、`REDTEAM-verify.md:88` 是 shell 输出续行（`Settings(...) → ... \| ALLOW_REGISTRATION=True`）。该判据缺少"行内 `\|` 数 ≥2 且处于表格上下文"的前置条件 ⇒ **真实异常 0**。教训与"Pydantic 探针"同族：**告警必须先自证再据以行动** |

---

### 第 13 轮的自查（**自纠/证伪**，不计入发现数）

| 假说 / 我的初版做法 | 核查方式 | 结论 |
|---|---|---|
| 初版披露文案把"上限不可行"放在"上限未执行"之前 | 用 `equal` 方案实跑（`cap_info_for(np.full(4,0.25), 0.2)`） | **自纠**：该情形下权重**根本没有被裁**（实际 `max w=0.25>cap=0.2`、`Σw=1.0`），初版文案会把它说成"只能投出 80%（现金）"——**与事实相反**。已把优先级改为"先报实际发生的事（是否违反上限），再说若要强制执行的代价" |
| 初版「引擎端到端」用例断言 `half_invested_rebalances > 0`（`risk_parity` + 恒定价格） | 跑用例 + 打印 `weight_cap_info` | **自纠**：恒定价格下 `_trailing_returns` 返回 `None` ⇒ 风险类方案**回退等权**（`cap_enforced=False`），断言不成立。改夹具为有起伏的行情（并发现下述耦合）。**未**调低断言去凑通过 |
| `_summarize_cap_infos` 的日志行读 `max_invested_ratio` | 混合情形（早期回退等权 + 后期半仓）实跑 | **自纠**：该键只在"不可行"分支赋值，混合情形走"未执行"分支 ⇒ `KeyError`。已改为只要存在不可行调仓就一律给出该键 |
| `cov_window<20` ⇒ 风险类方案静默退化为等权（疑似活缺陷） | 查 API 定义（`backtest.py:64`、`portfolio.py:43` 均 `ge=20`）+ 直接调 `_trailing_returns` | **降级为潜在耦合（非缺陷）**：`min_obs=21` 与切片 `cov_window+1` 的耦合恰好被两个 API 的 `ge=20` 守住，HTTP 路径**不可达**。故不记为发现，改为**守卫用例**（防日后放宽下界） |
| 源码锁 `"daily_rf" not in portfolio.py` | 跑用例 | **自纠**：该字符串出现在我新写的 docstring 里（说明历史），锁误伤文档。改为断言签名 `"daily_rf:" not in src` + `"rf_annual: float" in src` |

---

## P1-4 退市接线：只读实测（2026-09-21）

脚本 `backend/.tmp_testrun/p1_4_delist_state.py` 与 `p1_4_bt_panel_schema.py`
（均以 `sqlite3 file:…?mode=ro` 只读打开，**绝不写入生产库**）。

| 事实 | 实测值 |
|---|---|
| `instrument` 总标的数 | **5552** |
| `instrument.delist_date` 非空 | **0（0.0%）** |
| `instrument.list_date` 非空 | **122（2.2%）** ⇒ 97.8% NULL（与 B5-14 一致） |
| `universe_daily_bt` 面板列 | date/symbol/name/board/is_st/list_date/days_since_list/is_halted/limit_pct/limit_up/limit_down/industry/open/high/low/close/volume/amount/factor ⇒ **无 `delist_date`** |
| `universe_daily` 面板列 | 同上但无 amount/factor ⇒ **同样无 `delist_date`** |

**结论（含对报告的更正）**：
| 路径 | 驱动条件 | 当前数据下是否生效 |
|---|---|---|
| ① 建库期「按 `delist_date` 剔除」 | `instrument.delist_date` | **空转** |
| ② 面板携带 `delist_date` 列 | 面板 schema | **不含** ⇒ ①在结构上不可能发生 |
| ③ 持仓强平 + haircut | **面板缺席**（连续 N 日不在 `uni_d`） | **生效**（`engine.py:385-397`，与 `delist_date` 无关） |

⇒ 报告的「退市过滤/强平/haircut **三条路径全部空转**」**实为 1 条空转**；
真实缺陷是「披露不实」与「折价损失不可见」，两条本轮已修。

**仍未完成（需外部条件，非本轮可验证）**：`delist_date`/`list_date` 回填需要
外部退市名单数据源，本沙箱**无外网**；把联网步骤接进晚间例行会让流水线在
此环境下必然失败 ⇒ **接线属部署决策**，已在报告中标注。

---

## P1-25 前后对照（`backend/.tmp_testrun/p1_25_caliber.py`）

合成数据（260 个交易日 × 40 只，h=20；**仅用于口径对照，非策略业绩**）：

| 指标（h=20） | ORIG | FIXED | 偏差 |
|---|---|---|---|
| 年化价差 | **+6031.44%** | **+301.57%** | 放大 **20× = h** |
| 多空 t 值 | **107.39** | **24.01** | 高估 **4.47× = √20** |
| IC 衰减 t 值（h=1） | 19.8659 | 19.8659 | **完全一致**（1 日无重叠，口径不变） |
| `long_short_nav` | **不存在** | 存在（241 点） | 前端图表此前**恒空** |
| `spread_annualized` | 不存在 | 不存在 | 前端年化**永不显示** |

后端返回键实测：`long_short_nav`/`ls_annualized`/`ls_t_stat`/`monotonic`/
`n_independent`/`annualization_basis`/`t_stat_basis`/`nav_basis` **全部存在**。
前端**根因**：`frontend/src/api/backtest.ts` 的 `SignalAnalysisPanel` 类型把
不存在的 `spread_annualized` 声明为必需字段 ⇒ `tsc` 通过、运行时 `undefined`。
新增用例 `test_frontend_type_declares_all_backend_keys` 直接对 TS 源码断言以防再漂移。

## P1-26 前后对照（读码 + `tsc`）

| 字段 | 后端量纲（`domain/metrics.py:221-231`） | ORIG 显示 | FIXED 显示 |
|---|---|---|---|
| `annual_return` | 小数 `0.1234` | +0.12% | **+12.34%** |
| `win_rate` | 小数 `0.55` | +0.55% | **+55.00%** |
| `max_drawdown` | **正幅值** `0.27` | **+0.27%**（正号！） | **−27.00%** |

对齐依据：`Portfolio/index.tsx:145-146,418-419`、`resultParts.tsx:28,275`。
`tsc --noEmit` = 0。

## B5-20 前后对照（`backend/.tmp_testrun/b520_pnl_basis.py`）

制造成交（`ma_cross` 2/3 均线，单标的，佣金 3bp，无滑点）：

```
date        side   price    qty     fee   pnl(FIXED)   pnl(ORIG)
2026-01-08  buy   10.300   9200   29.38          —           —
2026-01-14  sell  10.310   9200   76.83     −14.21      +15.17
```

| | ORIG | FIXED |
|---|---|---|
| 该往返 pnl | **+15.17 ⇒ 记盈利** | **−14.21 ⇒ 记亏损** |
| `win_rate`（该组合仅此一笔往返） | **1.0000（100%）** | **0.0000（0%）** |
| 后端报告 `risk.win_rate` | 1.0 | **0.0** ✓ |

恒等式实测成立：`FIXED_pnl = ORIG_pnl − 买入fee`（15.17 − 29.38 = −14.21）。
该往返**毛盈利 92.0 元**（买 10.300 → 卖 10.310 × 9200）却**净亏**：
费用合计 106.21 元 > 92.0 元。这正是旧口径把亏损记成盈利的机制。

> 口径变更披露：逐笔 `pnl` 由「毛盈亏 − 卖出费用」变为
> 「毛盈亏 − 买入费用 − 卖出费用」，与 `nav`/`sharpe` 一致。
> 影响面：`/backtest/strategy-run` 的 `risk.win_rate` / `avg_pnl_ratio`
> 及策略页交易表的 `pnl` 列；换手越高、单笔越小，影响越大。

---

## B2-8 前后对照（Decimal 参照，160 万样本）

`backend/.tmp_testrun/b28_limit_rounding.py`：昨收 2 位小数（0.5–2000 元，
20 万随机 + 12 个**密集半分边界**）× pct ∈ {5%, 10%, 20%, 30%} × 买/卖。

| 实现 | 与 `Decimal ROUND_HALF_UP` 参照不一致 | limit_up | limit_down |
|---|---|---|---|
| **ORIG** `(x*100+0.5).floor()/100` | **13,221 / 1,600,096 = 0.826%** | 1,474（0.184%） | **11,747（1.468%）** |
| **FIXED** `+1e-9` | **0 / 1,600,096 = 0.000%** | 0 | 0 |

不一致样本（ORIG 恒**少 1 分**）：昨收 849.30 × 0.95 = 806.835 ⇒ ORIG 806.83、
参照 **806.84**；1419.35 × 0.70 = 993.545 ⇒ ORIG 993.54、参照 **993.55**。

> 报告只读实测的 0.391%/0.031% 与本文的 1.468%/0.184% 是**同一机制的不同抽样**
> （本脚本刻意加密了半分边界样本，且昨收为均匀随机）。机制与方向完全一致。
>
> **`+1e-9` 的安全性**：输入粒度 ≤4 位小数 ⇒ `v*100` 的粒度是 **0.01 分**，
> 非边界值距半分级距 ≥0.01 分，比 1e-9 大 **7 个数量级** ⇒ 不可能把合法的
> 非边界值上抬（已用 1.2649/1.2651 两个哨兵值专项验证）。
>
> ⚠️ **存量数据未自动重算**：已落盘的 `universe_daily` 分区仍带旧值，
> 需重建该数据集后修复才对历史生效（新写入的日期自动正确）。

---

## P1-1 前后对照（引擎层，真实 `run_backtest`）

场景：21 个交易日、4 只票、`rebalance_freq="weekly"`、`decay_bps=10`、`top_k=2`。
脚本 `backend/.tmp_testrun/p1_1_decay_repeat.py`（ORIG = 把 `begin_day` 置 no-op，
即 HEAD 的"从不复位"日程）。

| 版本 | 实际扣费次数 | 扣费日程 | 期末权益 | Sharpe |
|---|---|---|---|---|
| **ORIG（HEAD 日程）** | **17 次** | 每个周五的换手被连扣 **5 次**（周五+下周一~周四） | 0.9829（**−1.71%**） | **−22.86** |
| **FIXED（逐日复位）** | **4 次** | 仅 4 个调仓周五各 1 次 | 0.9933（−0.67%） | −7.29 |

> 重复倍数 = 两次调仓之间的交易日数（周频 ⇒ 5×；报告的场景为 16×，机制相同）。
> 同时 `nav_df["turnover"]` 在非调仓日由"上次调仓值"改为 **0**，
> 故 `annual_turnover = 日均 × 252` 对周频策略不再虚高约 5×。
> **默认 `rebalance_freq="daily"` 路径不受影响**，故报告 §7 实测的
> Top-10 134.7×/年、Top-50 81.0×/年 **仍然成立**。

## B5-08 的实测裁决（报告结论被推翻）

`backend/.tmp_testrun/b508_turnover_verdict.py`（建仓日与调仓日分开，
`rebalance_to_weights` 真实路径）：

| 场景 | 卖腿/权益 | 买腿/权益 | 两腿和/权益 | `last_day_turnover`（原实现） | 标准单边 Σ\|Δw\|/2 |
|---|---|---|---|---|---|
| 全仓换标的 | 0.9500 | 0.9483 | 1.8983 | **0.9483（=买腿）** | 0.9492 |
| 仅加仓 | 0.4750 | 0.4741 | 0.9491 | **0.4741（=买腿）** | 0.4746 |
| 仅清仓（买腿全被涨停拒） | 0.9500 | 0 | 0.9500 | **0.9500（=卖腿，2× 多扣）** | **0.4750** |

> 结论：原实现**不是**双边累加，而是"取最后一次 match 的那条腿"。
> 全仓换标的的 0.9483 **本就是标准单边换手**，报告所说"真实≈0.47"把
> (买+卖)/2 与全仓换标的的单边换手混为一谈 ⇒ **B5-08 的"2×"定性不成立**。
> 真实缺陷在**仅清仓/不对称**场景（原实现 2× 多扣），已按 Σ|Δw|/2 修正。

## B5-10 前后对照（四套 ETF 判定）

`backend/.tmp_testrun/b510_etf_predicate.py`（HEAD 版函数逐字复刻）：

| code | watchlist(HEAD) | ma_cross(HEAD) | paper(HEAD) | **broker(HEAD)** | 一致? | FIXED |
|---|---|---|---|---|---|---|
| 510300 / 512880 / 588000 / 159915 | True | True | True | **False（收税）** | 是 | True |
| **160123（深 LOF）** | **False** | True | True | **False** | **否** | True |
| **180101（深封闭式）** | False | False | False | **False** | 是（都漏） | True |
| 600519 / 300750 / 688981 | False | False | False | False | 是 | False |
| **159abc（损坏）** | **True** | **True** | **True** | False | 是（都错） | **False** |

**连带修掉的真缺陷**（`watchlist.is_etf_code`）：
- 原前缀表 `_ETF_PREFIXES[:4]` = `("51","56","58","15")` **漏 16xxxx**；
- 原表达式 `len==6 and isdigit() and … or startswith("159")` 因 `and` 优先级高于
  `or`，使长度/数字校验**被绕过** ⇒ `"159abc"`、`"159915.SH"` 均判为基金；
- 后果：`normalize_symbol("160123")` 走股票分支，被 `code_to_symbol` 兜底拼成
  **并不存在的 `160123.SH`**（现返回 `160123`）；`/watchlist` 汇总的
  `etf_count` 亦把 16x 基金误计为股票。

**费率口径变更（S1/T2）** —— 需知情的数字变化：
| 项目 | 变更前 | 变更后 |
|---|---|---|
| 印花税（股票卖出） | 恒 0.5‰ | **法定分段**：2023-08-28 前 **1‰**、之后 0.5‰（显式传入的数值仍原样使用） |
| 印花税（ETF/LOF 卖出） | **照收 0.5‰** | **免征** |
| 过户费 | 未建模 | **双边 0.01‰**（股票；场内基金 0），买入与卖出都计 |
| 影响 | — | 2016–2023-08 区间的回测成本**不再低估一半**；`tests/test_backtest.py` 3 处断言随口径更新（`cost` 现含过户费） |

> ⚠️ 未建模 2022-04-29 之前的过户费沪深差异（沪 0.02‰ / 深免）：无该区间的
> 费率权威复核，**不凭空写历史费率**。已在该函数 docstring 中显式声明为已知缺口。

---

## P1-40 前后对照（唯一差异 = 互斥）

`parquet_store.py` 有相对导入，无法把 HEAD 版单独 import；本轮对该函数的**唯一改动**
就是包了一层 `_rw_lock`，故用「把 `_rw_lock` 换成 no-op」等价复现 HEAD 语义
（脚本 `backend/.tmp_testrun/p40_beforeafter.py`）。

场景：已有 **3 行** + **2 个 barrier 同步的并发写者**各写一个不同交易日（期望 5 行）。

| 版本 | 第 1 次 | 第 2 次 | 第 3 次 | 丢行 |
|---|---|---|---|---|
| **ORIG（无互斥，= HEAD）** | 4 行 | 4 行 | 4 行 | **3/3 次丢行** |
| **FIXED（真实 `_rw_lock`）** | 5 行 | 5 行 | 5 行 | **0/3**，全部正确 |

> ORIG 期间还额外抛出 `PermissionError [WinError 5]`（两个写者竞争 `os.replace`
> 的临时文件）—— 说明该竞态除"静默丢行"外还会以文件系统错误形式外溢。

---

## P1-41：**推翻了旧测试的契约**

`tests/test_manifest_self_heal.py:92-121`（旧）把缺陷当契约断言：
「再新增一只磁盘目录 → 本进程该 dataset 已审计过，**不应再次重扫**（`_audited` 预算）」——
这**正是**审计 P1-41 认定的缺陷（后续新增 symbol 永久静默缺失）。
现该用例已改写为**反向回归**（新增目录必须再次自愈），并新增：

| 新用例 | 断言 |
|---|---|
| `test_new_bypass_write_after_audit_is_still_healed` | 首次自愈后的新增目录必须**再次**自愈（calls 1→2），且再次留 WARNING |
| `test_empty_dirs_do_not_cause_repeat_rescan` | 合法空目录（rows=0）连查 5 次仍只重扫 1 次（防开销） |
| `test_audit_throttle_defers_but_does_not_cancel_heal` | 30s 节流只**推迟**：窗口过后仍补上（不退回"永久静默"） |

---

## P1-39 真实产物复算（`data/models/exp/`）

| 产物 | valid_rmse | valid_rank_ic | 训练口径 |
|---|---|---|---|
| `lgbm_v1_20260905_181713_repaired`（去均值目标，**当前生产**） | **0.059975** | 0.10203 | 未落盘（旧产物） |
| `lgbm_v1_20260905_181337_repaired`（绝对收益目标，重训默认口径） | **0.074109** | 0.02113 | 未落盘（旧产物） |

> 与报告 §3 的 P1-39 数字**逐位吻合**（0.05998 / 0.07411；比值 1.2355× ≈ 报告中写的 1.25×）。

门禁行为对照（把 181337 当候选、181713 当生产，其余指标置为"不劣于生产"以隔离 RMSE 维度）：

| 场景 | 旧门禁 | 新门禁 |
|---|---|---|
| 两边都无口径（历史产物） | `rmse_vs_prod` 参与比较，limit=0.062975，0.074109 > limit ⇒ **判"恶化超限"拒绝** | 维持历史行为（不额外拦），但 RankIC 维度照旧把关 |
| 两边都落盘口径（修复后产物） | 同上，**误拒** | `rmse_vs_prod = {pass: None, skipped: True, reason: "basis_mismatch"}`，`basis_mismatch.reason = 训练口径不一致，RMSE 不可比：{"target": {"candidate": "absolute_forward_return", "production": "xsec_demean"}, "xsec_demean": {"candidate": false, "production": true}}` ⇒ **不再误拒** |

## P1-28 的测试边界发现（重要）

`tests/conftest.py:286-314` 有一个 **session 级 autouse** fixture
`_neutralize_auto_sync_start`，把 `app.services.sync_service._start_sync_bg`
整个替换为 `lambda mode, resume: None`（原因是：`auto_sync_scheduler` 的
module 级 `TestClient` lifespan 首 tick 会在墙钟 ≥15:45 时触发**真实联网增量同步**，
抢 `pipeline_slot("sync")` 使并发用例拿到 `PipelineBusy`）。

**副作用**：`_start_sync_bg` 的**函数体在测试套件里永远不可达** ⇒ 写在该函数内的
P1-28 缺陷**不可能被行为测试发现**，这也解释了它为何能"修好又被抹掉"。因此：

1. 修复把复位纪律抽成独立函数 `_start_sync_reset(state, mode, resume)`，**可被直接断言**；
2. 另加**源码级接线断言**（`_start_sync_bg` 必须调用该 helper，且函数体内不得再出现
   `_sync.completed.clear()`），作为该会话级替身下的唯一防线；
3. 并在测试中钉住 conftest 替身的存在 —— 若哪天移除，本文件的直接调用策略需重审。

---

## P0-1 前后对照（伪容器布局，实跑）

用 `git show HEAD:backend/app/core/config.py` 取原版，与工作区版**放进同一个伪容器树**
（`<tmp>/app/app/{main.py,core/config.py}`，无 `<tmp>/backend`）分别 import：

| | PROJECT_ROOT | LOG_DIR | DATA_ROOT | 目录在应用根内？ |
|---|---|---|---|---|
| **ORIG(HEAD)** | `<tmp>`（**根的上层**） | `<tmp>/backend/logs` | `<tmp>/data/parquet` | ❌ **False** |
| **FIXED** | `<tmp>/app` | `<tmp>/app/logs` | `<tmp>/app/data/parquet` | ✅ **True** |

真实容器路径 `/app/app/core/config.py`（算术）：ORIG 的 `parents[3]` = **`/`** ⇒
`LOG_DIR=/backend/logs`、`DATA_ROOT=/data/parquet`（`read_only` + uid 10001 ⇒ EROFS，容器起不来）；
FIXED 的 `BACKEND_ROOT=/app` ⇒ `/app/logs`、`/app/data/parquet`，**正好对上 compose 的
`./data:/app/data`、`./backend/logs:/app/logs`**。

本地仓库布局的取值**逐字未变**（`LOG_DIR` 仍是 `<repo>/backend/logs`），由
`test_repo_layout_values_unchanged` 钉死。

## P0-2 双向验证（实跑 `docker-compose config`）

| 场景 | 命令 | 结果 |
|---|---|---|
| 缺凭据 | `docker-compose --env-file <只有 REDIS_PASSWORD 的文件> config` | ✅ **报错退出**：`required variable ADMIN_TOKEN is missing a value: ADMIN_TOKEN 未设置：生产环境必须提供 ≥32 位强随机串` |
| 有凭据 | `docker-compose config`（读仓库根 `.env`） | ✅ 解析出 `ENV: prod`、`ALLOW_ADMIN_TOKEN_LOGIN: "false"`、`ALLOW_REGISTRATION: "false"`、`CORS_ORIGINS: http://localhost:8080,http://127.0.0.1:8080` |
| 该组合能否启动 | `validate_runtime_safety(Settings(**解析结果))` | ✅ `PROD-OK`（API 可启动） |
| 反证：CORS 用开发地址 | 同上 + `CORS_ORIGINS=http://localhost:5173` | ✅ 被拒：`CORS_ORIGINS 不能包含开发环境地址` |
| 反证：默认 token | 同上 + `ADMIN_TOKEN=aqp-dev-token-change-me` | ✅ 被拒：`ADMIN_TOKEN 仍为默认值；ADMIN_TOKEN 长度必须至少为 32` |

> 注意：`CORS_ORIGINS` 必须一起改——默认值就是 5173 开发地址，**prod 校验会拒绝启动**，
> 所以「只加 `ENV=prod`」会让 API 直接起不来。前端经 nginx 同源反代 `/api/`，浏览器本就不触发 CORS。

---

## 全量测试套件 前后对照（2026-09-21）

| 阶段 | passed | failed | skipped | 失败明细 |
|---|---|---|---|---|
| **修复前基线** | 1065 | 6 | 8 | 3 × `test_read_endpoints_rbac.py`（ETF，**真实缺陷 P1-32**）+ 3 × `test_pipeline.py`（**沙箱环境产物**） |
| 首批修复后 | 1087 | 3 | 8 | 仅 3 × `test_pipeline.py`（沙箱命名管道限制，与基线同一原因） |
| P1-1 / B5-10 / B2-8 | 1206 | 3 | 8 | 同上 |
| P1-25 + P1-26 + B5-20 | 1249 | 3 | 8 | 同上 |
| **P1-29 + P1-30 + P1-31 + P1-42（本轮）** | **1278** | **3** | **8** | 同上（3 条与本轮改动无关） |
| **P1-42 接线后暴露的两处缺陷修复完成** | **1287** | **3** | **8** | 同上（3 条与本轮改动无关） |
| **P1-3 / P1-5 / P1-2 / §4.8 序 16 修复完成** | **1305** | **0** | **8** | **全绿**：`test_pipeline.py` 那 3 条在本轮最后两次运行中**也通过了**（见下方"环境说明"，非代码改动所致） |
| **P1-15 / P1-34(B7a-03) / §4.8b S12 修复完成** | **1328** | **0** | **8** | **全绿**（继续全绿）。增量对账：`1305 → 1328` = **+23**，恰为本轮新增用例（`test_quotes_snapshot_sharding.py` 6 + `test_universe_filter_disclosure.py` 12 + `test_datacenter_instruments_total.py` 5）✓ |
| **P1-35 / P1-36 / B7a-06 / P1-43 修复完成** | **1363** | **0** | **8** | **全绿**。增量对账：`1328 → 1363` = **+35**，恰为新增用例（`test_param_date_calendar_validation.py` 21 + `test_heat_sentiment_and_probe_truth.py` 13 + `test_read_endpoints_rbac.py` 新增专条 1）✓ |
| **B7a-08 / B7a-09 修复完成（本轮收尾）** | **1368** | **0** | **8** | **全绿**。增量对账：`1363 → 1368` = **+5**，恰为 `test_dead_branch_and_empty_data_codes.py` 的 5 条 ✓ |
| **R9/S14 错误码治理完成** | **1382** | **0** | **8** | **全绿**。增量对账：`1368 → 1382` = **+14**，恰为 `test_error_code_governance.py` 的 14 条（7 条变异反证 + 7 条治理断言）✓ |
| **第 10 轮 · schema 漂移（缺列 ⇒ 裸 50000）同族 5 处** | **1392** | **0** | **8** | **全绿**。增量对账：`1382 → 1392` = **+10**，恰为 `test_schema_drift_column_guards.py` 的 10 条（含 1 条**缺陷本体反证**）✓ |
| **第 12 轮 · S4 监控触发器口径（P1-17 / P1-21 / R5）** | **1429** | **0** | **8** | **全绿**。增量对账：`1411 → 1429` = **+18**，恰为 `test_monitor_trigger_caliber.py` 17 条（16 条口径/真实面板 + 1 条前端披露锁）+ `test_monitor.py` 新增 1 条 ✓（另**改写** 3 条旧契约断言 + 1 条集成锁/变异反证）。真实面板验收含在套件内（本机有面板时真跑，CI 无面板自动跳过） |
| **第 11 轮 · P1-14 ETF 资金流字段口径** | **1411** | **0** | **8** | **全绿**。增量对账：`1402 → 1411` = **+9**，恰为 `test_etf_flow_field_semantics.py` 的 9 条（含真实观测样本复算与符号翻转反证）✓ |
| **第 10 轮 · B7b F6 复权口径披露 + P0-4 下半条基准口径** | **1402** | **0** | **8** | **全绿**。增量对账：`1392 → 1402` = **+10**，恰为 `test_backtest_price_basis_disclosure.py` 的 10 条（7 条复权口径 + 3 条基准口径）✓ |
| **第 17 轮 · P0-6 空榜终态裁决（`unavailable/empty_board` vs `ok/no_matching_signals`）** | **1475** | **0** | **8** | **全绿**（448s）。增量对账：`1464 → 1475` = **+11**，恰为 `test_screener_empty_board_contract.py` 的 11 条（真值表 6〔板块无成分股 ⇒ unavailable；池在无匹配 ⇒ 保持 ok；board=all 不适用；field 未知不臆断；09-14 最小载荷反向锁；缺行情优先级不变〕+ `filter_universe` 计数本体 3〔真快照 bse=0/main=2/all=None；无快照 ⇒ None；缺 board 列前缀兜底〕+ 真实 `_screen` 端到端 1〔bse ⇒ `unavailable/empty_board` 且 `universe_filter.rows=2` 证明非数据缺失；同 pred+universe 的 main 出榜 2 条；**缺陷本体反证**：仅去掉新增字段即复现旧 `ok/no_matching_signals`〕+ 接线锁 1）✓。**既有断言零改写**：`test_data_freshness_degradation.py:111-127` 原样通过；受影响 11 文件 **109 passed** |
| **第 15 轮 · P1-18 `g1_*` 邻接 universe 冻结（asof 稳定性 + 拒绝静默改写）** | **1464** | **0** | **8** | **全绿**（828s）。增量对账：`1452 → 1464` = **+12**，恰为 `test_graph_universe_freeze.py` 的 12 条（本体反证 3.0→252.0 / 冻结后挡住 / 边界"取值消失不可复原" / **首次冻结于既有历史之上必须显式授权** / 指纹标签不敏感而权重敏感 / 首次冻结复用 / 边集漂移默认拒绝且快照不被改写 / 重冻结留痕 / 血缘落盘 / orchestrator 两条拒绝路径且不落盘 / 接线锁 / AST 反向锁）✓。受影响既有 6 文件 **56 passed**（`test_feature_incremental` 由"照旧通过"预期**转为需补断言**：该用例 monkeypatch 掉整只标的数据，正是新策略拒绝的情形——**未放宽守卫**，补 2a 默认拒绝且年分区 mtime 不变 + 2b 显式授权后再验 `stale=1/filled=1`；同批另有一次 10 分钟挂起，见下方"环境产物"行，重跑未复现） |
| **第 13 轮 · 组合层约束（P1-13 / B2-12，含默认 `equal` 方案不执行上限的新发现）** | **1440** | **0** | **8** | **全绿**。增量对账：`1429 → 1440` = **+11**，恰为 `test_portfolio_constraints.py` 的 11 条（纯判据真值表 / 缺陷本体反证 / 引擎两条真实路径 / 汇总取最坏 / Σw 归一三态 / 前端披露锁）✓ |
| **第 13 轮 · rf 与基准口径统一 + μ 披露（B2-16 / B2-11）** | **1450** | **0** | **8** | **全绿**。增量对账：`1440 → 1450` = **+10**，恰为 `test_risk_caliber_consistency.py` 的 10 条（rf 单源/年化、形参生效、旧值不变、基准标签不伪造、λ 惰性的独立复核、前端标签与 `rf_annual` 披露锁）✓ |
| **第 13 轮 · 潜在耦合守卫（`cov_window<20` ⇒ 风险方案静默退化为等权）** | **1451** | **0** | **8** | **全绿**。增量对账：`1450 → 1451` = **+1**，为 `test_portfolio_constraints.py::test_risk_scheme_silently_degrades_below_cov_window_20`（钉住 `_trailing_returns(min_obs=21)` 与两个 API `ge=20` 的耦合）✓ **非缺陷**，API 下界当前恰好守住 |
| **第 18 轮 · §8.2 收官批次（第 6/7/8/17/18/19/20 项 + §8.2b 契约类；7 个子代理并行产出，父审核员逐条复核）** | **1647** | **0** | **8** | **全绿**（467s）。增量对账：`1475 → 1647` = **+172**。可核对的新增用例来源：`test_dead_code_removed.py` 29（死代码双向 AST 锁：删除项必缺 + 存留候选必在 + `__all__` 干净 + `/metrics` 无"永不被写"的序列）、`test_graph_universe_freeze.py` 12、`test_screener_empty_board_contract.py` 11、`test_ci_gate_hygiene.py` 12（`.dockerignore`/`.gitignore`/Dockerfile HEALTHCHECK 与 compose 一致但路径必须真实存在于路由表/前端 `/healthz`/**alerter 引导段可执行**/全脚本可编译/**workflow 锁**：push 过滤分支必须是真实默认分支、ruff 步骤必须覆盖 `app tests scripts`+`F`、任何步骤不得 `\|\| true`）、`test_query_limit_bounds.py` 9、`test_delist_wiring.py` 9、`test_ops_quality_scan_year.py` 3、`test_frontend_b9_fixes.py` 32（前端 B8/B9a/B9b/B9c，配 `npx tsc --noEmit` EXIT=0）、B/C/D 三个新文件 53、其余为子代理向既有文件补的用例（ML 泄漏口径 1 / `pred_level` 披露 5 / `train_basis` 落盘 2 / `etf`/`datacenter` 截断披露 2 / `ai_stats` 口径 2 等）。**首跑曾 2 failed，均由父审核员独立定位并修复**：①`test_delist_wiring::test_coverage_fields_match_real_counts` 是**测试设计缺陷**（断言全库计数，而 `test_delist_liquidation.py:107` 会清空 `instrument` 全表；字典序前缀复现 `assert 4 == 3`）⇒ 改为作用域计数，**保留**"披露==同源实测"不变量（前缀跑 252 passed）；②`test_write_endpoints_smoke.py:630` 的允许集合未含新引入的 `ERR_EXPR_INVALID=53001`（子代理只核对了同文件 :655）⇒ 纳入 53001（守卫内容不变，同文件 :649 docstring 本就写"40000/53001"）。**门禁**：`mypy app/` **Success（129 文件）**、`ruff check app tests scripts --select F,E9` **All checks passed**——两条均由父审核员在子代理收工后重跑，期间修掉 1 个子代理引入的 **4 处 mypy 错**（变量名复用）与 2 个子代理引入的 **5 处 F401/F811** |
| **第 13 轮 · 披露字段端到端序列化 + 响应接线锁（收尾）** | **1452** | **0** | **8** | **全绿**。增量对账：`1451 → 1452` = **+1**。**发现依据**：既有 API 用例调 `/backtest/run` 时都不传 `weight_cap`（默认 0 ⇒ 披露为空）⇒ **非空披露的序列化路径从未被覆盖**，若混入 numpy 标量会在生产才炸；新用例对引擎与端口函数产物各做一次 `json.dumps` 往返，并锁住 `api/v1/backtest.py`、`api/v1/research.py`、`domain/portfolio.py` 三处响应接线 ✓ |

> **环境说明（如实记录，不冒充修复）**：`tests/test_pipeline.py` 的 3 条此前连续 4 轮
> 以 `PermissionError [WinError 5]` 失败于 `multiprocessing/connection.py:575 CreateFile`
> （loguru `core/logging.py:46,66,78` 硬编码 `enqueue=True` ⇒ `SimpleQueue` 需要命名管道），
> 属沙箱环境限制、与产品代码无关。本轮最后两次运行该限制**未生效**（单独跑
> `tests/test_pipeline.py` = 6 passed），故套件转为全绿。**未做任何针对它的代码改动**，
> 该限制若再次生效，这 3 条会重新变红——这正是"3 条环境产物"与"产品缺陷"必须分开记账的原因。
> 增量对账：`1287+3=1290` 条在跑 → 本轮 `1305` 条，**+15 恰为新增用例**
> （`test_paper_position_guard.py` 6 + `test_settings_overview_cache.py` 7
> + `test_backtest_cache_key.py` 1 + `test_p2_backtest.py` 1）✓

- **零产品回归**：失败数 6 → 3，且剩下 3 条与本轮改动无关。
- 剩余 3 条的根因（沙箱边界，非产品缺陷）：`app/orchestrator.py:153 step_build_features` →
  `multiprocessing/connection.py:575 Pipe() → _winapi.CreateFile` → `PermissionError [WinError 5]`。
  沙箱禁止进程打开命名管道；`-p audit_mkdtemp_fix` 只压掉了 loguru 的 `enqueue=True`，未覆盖
  `build_features` 自身的进程/管道使用。
- **本轮增量精确对账**：1249 → 1278 = **+29**，恰为本轮新增用例数
  （`test_pipeline_state_truth.py` 29 条）；另有 1 条既有用例按契约变更重写
  （`test_sync_integrity.py::test_default_steps_include_offline_rebuilds` 的确切清单断言，
  见下文 P1-42 小节），不计入增量。
- 本轮期间出现过的**唯一**新失败即上述被重写的 1 条，原因是它硬编码了 `FULL_STEPS` 确切清单；
  确认属**设计变更**（步骤集按 P1-42 增加一员）而非回归后随契约更新。

---

## 审核报告自身被实测证伪的条目（修复阶段自查，新增 R-6）

### P1-47「停牌判定是死条件」→ **撤回**

原结论（标**确定**）：`is_halted` 的 44,280 行中 44,278 行 `volume` 为 NULL、仅 2 行 `volume<=0`
⇒ 所有 `volume<=0` 型停牌闸门在面板上**永不命中**。

**实测（只读生产面板）**：

```
universe_daily   year=2026  431,462 行  is_halted: True=3107 / False=428355
  volume 为 NULL: 3107    volume<=0: 0
  交叉表 vol_null×halted: {False,False:428355} {True,True:3107}   ← 完全对角，零例外
universe_daily_bt year=2026 185,812 行  is_halted: True=1125 / False=184687
  交叉表 vol_null×halted: {False,False:184687} {True,True:1125}   ← 完全对角，零例外
```

**三条推翻理由**：
1. `is_halted` 是用 `volume_f = volume.fill_null(0.0)` 算的（`data/universe.py:200,243`）⇒ **停牌标注正确**；
2. `broker._num(None, default=0.0)`（`broker.py:200-212`）把 NULL 映射成 `0.0` ⇒ `volume <= 0`
   **确实会命中**，它与 `halted` 是**冗余双保险**，不是死条件；
3. `trading/paper.py:109-110` 的 `close*volume` 得到 NULL `amt`，被 `.filter(pl.col("amt") > 0)`
   丢弃 ⇒ 停牌股不会混进"流动性不足"名单，行为正确。

**方法教训（已写入报告 §9.3）**：探针统计的**列口径**（NULL vs 0）不能代替对**消费端归一化逻辑**
（`_num` / `fill_null`）的核对。本轮错在只看面板 NULL 率就断定闸门失效。

**残留（低优先，非 Bug）**：面板以 NULL 而非 0 表达停牌，因此**新增**消费点若直接读 `volume`
原始列且不做归一，会拿到 NULL（polars 中 `null > 0` 为 null、在 `when()` 里视作不成立）。
故仍建议提供 `is_halted_row()` 统一入口，但定位从"修缺陷"改为"防新增踩坑"。

---

## 修复前后对照（实测证据）

### P0-7 前视偏差 —— 同一判据把"无预测力因子"和"完全预见因子"的角色完全对调

用 `git show HEAD:backend/app/ml/gp_miner.py` 加载原始版本作独立模块，跑**同一份独立同分布收益面板**
（60 只 × 400 日，`np.random.default_rng(20260921)`）：

| 版本 | `same_day` 因子 L/S 净值（真实预测力 = 0） | `next_day` 因子 L/S 净值（完全预见） | 哨兵 `<br>`(next > 10×same) | `same_day` 是否表现为无边缘 |
|---|---|---|---|---|
| **ORIG（HEAD）** | **2.2363e+09** ← 无预测力因子暴富 = 前视 | 1.0865 | ❌ False | ❌ False |
| **FIXED** | 1.2475 | **2.1336e+09** | ✅ True | ✅ True |

- 复现命令：先 `git show HEAD:backend/app/ml/gp_miner.py | Set-Content backend/app/ml/_orig_head_check.py`，
  再 `python backend/.tmp_testrun/p07_beforeafter.py`（脚本为纯只读对照，运行后自动清理临时模块）。
- 解读：原实现里"当日收益因子"（对次日毫无预测力）拿到 2.24e9 倍净值，而真正"完全预见"的因子只有 1.09
  —— 二者角色对调即为前视偏差的直接指纹。修复后只有完全预见因子暴富，符合正确口径。
- 影响面：该净值经 `/api/v1/studio/alpha-eval`（`gp_miner.evaluate_expr_detail(with_groups=True)`）
  与 `factor_report` **直接展示给用户**，属用户可见的错误结论。

### P0-8 备份脚本 —— 归档一致性与 main() 破坏性

| 检查项 | ORIG（HEAD） | FIXED |
|---|---|---|
| 归档成员 | `sqlite/aqp.db` + **`aqp.db-shm` + `aqp.db-wal`** | 仅 `sqlite/aqp.db`（sidecar 已由 `VACUUM INTO` 折叠） |
| 归档里的 DB 单独解出 | **打不开（`OperationalError`）** ← 撕裂副本 | **50 行完整** |
| `main()` 的"恢复演练"（库被应用占用） | **`PermissionError [WinError 32]` 崩溃**（Windows）；Linux/Docker 下 `unlink()` 会成功 ⇒ 用撕裂副本**替换生产库** | 正常返回，生产库 **50 → 50 行未改动** |

- 复现命令：`git show HEAD:backend/scripts/backup.py | Set-Content backend/.tmp_testrun/backup_orig.py`，
  再 `python backend/.tmp_testrun/p08_beforeafter.py`。
- 关键点：`WinError 32` 说明该脚本在生产应用运行时**连演练都跑不完**；而在 Docker/Linux（真实部署环境）
  同一步会成功删除生产库。README:213 原把它推荐为 nightly cron ⇒ 每晚一次数据破坏风险。

### P0-3 / P1-50 / R5 —— 不变式能**精确**抓住这两条回归

运行时摘掉这两条路由的鉴权依赖后跑同一判据：

```
A) 修复后现状 -> 无未分类端点（通过）
B) 模拟撤回修复 -> ['/api/v1/datacenter/train/readiness', '/api/v1/market/index/kline']
C) 是否精确抓住这两条: True
```

- 复现命令见本节下方"验证命令汇总"第 4 条。
- 这是 R5 的**机制级**修复：此前 `test_read_endpoints_rbac.py` 虽然遍历了全部 GET 路由
  （`_live_get_roles()`），但断言只迭代手工白名单 `EXPECTED_ROLES`，因此白名单外的路由
  是"**未被分类**"而非"通过"——这正是 `/datacenter/train/readiness` 被三份历史报告登记却三次
  未修、以及 `/market/index/kline` 直到本轮才发现的原因。

### P1-32 —— 3 条长期失败的测试用例转绿

```
修复前：3 failed, 154 passed, 3 skipped
        FAILED test_read_endpoints_rbac.py::test_read_endpoint_allows_authorized[/api/v1/etf/flow-viewer]
        FAILED ...[/api/v1/etf/hot-viewer]  ...[/api/v1/etf/list-viewer]
        （RuntimeError: 外部数据源请求失败: RemoteProtocolError 直接穿透 TestClient）
修复后：126 passed, 3 skipped（该文件全绿）
```

---

## 验证命令汇总（可直接复跑）

```powershell
# 0) 沙箱专用：conftest 的 tempfile ACL 补丁（非项目源码，仅测试环境）
$env:PYTHONPATH='D:\Python_Project\Alpha Quant Platform\backend\.tmp_testrun'

# 1) 静态检查（改动文件全部通过；app 的 F841 由 5 → 4）
python -m ruff check app scripts tests --select F,E9

# 2) 本轮新增的 3 个防回归用例文件（11 条）
python -m pytest tests/test_read_endpoints_rbac.py tests/test_data_source_error_contract.py `
                 tests/test_gp_miner_no_lookahead.py tests/test_backup_safety.py `
                 -p audit_mkdtemp_fix -q

# 3) 全量套件
python -m pytest tests -p audit_mkdtemp_fix -q

# 4) R5 不变式的精确捕获验证（运行时摘除两条路由的鉴权依赖）
#    见本文件同名小节的脚本片段；或直接读测试文件的 docstring

# 5) 本轮（P1-29/30/31/42 + 数据治理）的定向复跑
python -m pytest tests/test_pipeline_state_truth.py tests/test_history_start_floor.py `
                 tests/test_pipeline.py tests/test_pipeline_lock.py tests/test_sync_integrity.py `
                 tests/test_api.py -p audit_mkdtemp_fix -q

# 6) 2022 前数据治理：盘点 → 干跑 → 执行 → 回滚（默认干跑，绝不误删）
python scripts/purge_pre2022.py              # 干跑：只列清单
python scripts/purge_pre2022.py --apply      # 移入 data/_purged_pre2022/（可回滚）
python scripts/purge_pre2022.py --restore    # 回滚：全部移回

# 7) 重建回测数据集（P1-42 收益；约 52s，先备份旧面板）
python -c "from app.data.universe import build_universe_backtest; build_universe_backtest()"

# 8) 本轮实测探针（只读/隔离，均在 backend/.tmp_testrun/）
#    p1_42_staleness.py       _bt 与 universe_daily 的覆盖对照
#    p1_42_rootcause.py       hfq 2500 只 vs _bt 1133 只（根因）
#    pre2022_inventory.py     各数据集 2022 前分布（行数取自 parquet footer）
#    verify_purge.py          隔离后残留 = 0 的核验
#    b_null_listdate.py       全 NULL list_date 的 dtype 崩溃复现
#    b_overview_cache_key.py  缓存键含交易日的判定实验（跨日翻转）
#    b_overview_ttl.py        排除 15s 短 TTL 路径
#    p15_mono.py              P1-5 单调比方向的最小复现（递增 0.0→1.0 / 反向 1.0→0.0）
#    seq16_422.py             参数越界时信封业务码（确认 HTTP 恒 200 契约）

# 9) 第 5 轮四项修复的定向复跑
python -m pytest tests/test_paper_position_guard.py tests/test_settings_overview_cache.py `
                 tests/test_backtest_cache_key.py tests/test_p2_backtest.py `
                 tests/test_production.py tests/test_desk_attribution_benchmark.py `
                 -p audit_mkdtemp_fix -q

# 10) 第 6 轮三项修复的定向复跑（含 `filter_universe` 返回类型变更的回归面）
python -m pytest tests/test_quotes_snapshot_sharding.py tests/test_universe_filter_disclosure.py `
                 tests/test_datacenter_instruments_total.py tests/test_panic_rootcause_guard.py `
                 tests/test_panic_c_site_guards.py tests/test_screener_stocks.py `
                 tests/test_screener_snapshot.py tests/test_filter_universe_join.py `
                 tests/test_recommendation_candidate_parity.py tests/test_alerts.py `
                 -p audit_mkdtemp_fix -q

# 11) 第 7–8 轮四项修复的定向复跑（P1-35 / P1-36 / B7a-06 / P1-43）
python -m pytest tests/test_param_date_calendar_validation.py `
                 tests/test_heat_sentiment_and_probe_truth.py `
                 tests/test_read_endpoints_rbac.py tests/test_overview_heal_chain.py `
                 tests/test_settings_overview_cache.py tests/test_p3_security.py `
                 tests/test_api.py tests/test_trace_id.py `
                 -p audit_mkdtemp_fix -p no:randomly -q

# 12) P1-43 的最小复现（探针必须能失败：把 DATA_ROOT 指向不存在的盘）
python -c "import asyncio,pathlib;from app.main import app;from fastapi.testclient import TestClient;from app.core.config import get_settings;s=get_settings();s.DATA_ROOT=pathlib.Path('Z:/nope');c=TestClient(app);r=c.get('/health/ready');print(r.status_code, r.json()['code'], r.json()['data']['status'])"
# 期望：503 50300 not_ready（修复前：200 0 not_ready —— 编排系统只看状态码 ⇒ 永远 Ready）

# 13) P1-35 的最小复现（日历非法日期不得报 50000/0）
python -c "from fastapi.testclient import TestClient;from app.main import app;c=TestClient(app);h={'Authorization':'Bearer aqp-dev-token-change-me'};print([ (p, c.get(p, params={'date':'20269999'}, headers=h).json()['code']) for p in ('/api/v1/market/overview','/api/v1/market/overview/daily')])"
# 期望：[('/api/v1/market/overview', 40000), ('/api/v1/market/overview/daily', 40000)]（修复前：0（错标日期）/ 50000）

# 14) 本轮收尾两项（B7a-08 / B7a-09）的定向复跑
python -m pytest tests/test_dead_branch_and_empty_data_codes.py tests/test_panic_c_site_guards.py `
                 -p audit_mkdtemp_fix -p no:randomly -q

# 15) B7a-08 的"缺陷本体"最小复现（证明原写法抛的**不是** AQPException）
python -m pytest tests/test_dead_branch_and_empty_data_codes.py -k "concat or empty_universe" `
                 -p audit_mkdtemp_fix -p no:randomly -q
# 期望：2 passed（其中端到端那条同时断言错误消息含 `universe_daily`，防止"两处早退都抛 51001"的假通过）

# 16) R9/S14 错误码治理守卫（含变异反证 + 两端码表对账 + 行为断言）
python -m pytest tests/test_error_code_governance.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：14 passed

# 17) 裸码清零的独立复核（应为空；注释里的 `4001x` 不算）
Select-String -Path 'backend\app\api\v1\*.py','backend\app\*.py','backend\app\core\*.py' `
              -Pattern '(fail|AQPException)\(\s*[0-9]'

# 18) 两端码表逐值对账（独立复核，不依赖测试文件）
python -c "import re,ast,pathlib;b=pathlib.Path('backend/app/core/errors.py').read_text('utf-8');t=pathlib.Path('frontend/src/types/api.ts').read_text('utf-8');bv={n.value for n in ast.parse(b).body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,int) for t2 in n.targets if isinstance(t2,ast.Name) and t2.id.startswith('ERR_')};fv={int(m.group(2)) for m in re.finditer(r'(\w+):\s*(\d+)',t.split('export const ERR = {',1)[1].split('} as const;',1)[0])};print('仅后端:',sorted(bv-fv),'仅前端:',sorted(fv-bv))"
# 期望：仅后端: [] 仅前端: []

# 19) 第 10 轮 · schema 漂移守卫（含缺陷本体反证）
python -m pytest tests/test_schema_drift_column_guards.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：10 passed

# 20) 第 10 轮 · F6 复权口径披露
python -m pytest tests/test_backtest_price_basis_disclosure.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：7 passed

# 21) 端到端：缺 QFQ 分区时 `price_basis.basis` 必须为 raw 且点名回退标的
#     （测试已固化；手工复核可用 --tb=long 看断言上下文）
python -m pytest tests/test_backtest_price_basis_disclosure.py -k "raw_fallback or mixed" -q `
                 -p audit_mkdtemp_fix -p no:randomly -v

# 22) 前端静态复核（前端无测试框架：tsc + 源码断言）
cd frontend; npx tsc --noEmit
Select-String -Path 'src\pages\Backtest\index.tsx' -Pattern '· QFQ ·'   # 期望：无输出（已不再硬编码）

# 23) 第 11 轮 · P1-14 ETF 资金流字段口径（含真实观测样本复算）
python -m pytest tests/test_etf_flow_field_semantics.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：9 passed

# 24) 独立复核：真实样本两条恒等式（大单+超大单=主力；四类净额之和=0）
python -c "m,s,mi,lg,sl=274198704.0,-80721950.0,-193476752.0,65674576.0,208524128.0;print('f55+f56-f52 =',lg+sl-m,'| f53+f54+f52 =',s+mi+m,'| 旧写法 =',m-s,'| 偏差 =',(m-s)/m-1)"
# 期望：f55+f56-f52 = 0.0（主力净额已确证）
#       f53+f54+f52 = 2.0（四类净额之和为 0；此处 2 元系源数据四舍五入）
#       旧写法 = 354920654.0（与 B3b 的"代码输出"一致）
#       偏差 = 0.29439216459608075（= 红队手算 +29.4392%，本报告 +29.4% 正确）

# 25) 第 15 轮 · P1-18 g1_* 邻接 universe 冻结（缺陷本体反证 + 修复验证，12 条）
python -m pytest tests/test_graph_universe_freeze.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：12 passed
# 反证要点：同桶新标的入池 ⇒ 老标的 g1_ 3.0 → 252.0；冻结节点集后仍 3.0、新标的 g1_=NaN
#           冻结标的行情消失 ⇒ 3.0 → 2.5（不可复原）⇒ 三条漂移默认 ValueError 且不落盘
#           首次冻结于"已有年分区但无快照"之上同样拒绝（否则部署首夜即静默改全部历史）

# 26) 机制复核探针（只读；把"是不是行归一化分母"这件事钉死）
$env:PYTHONPATH='D:\Python_Project\Alpha Quant Platform\backend'
python backend\.tmp_probe_s4\probe_p118.py    # 期望：第 3 步恒等 ⇒ 行归一化假说被证伪
python backend\.tmp_probe_s4\probe_p118b.py   # 期望：⑤ 面板缺 D 但取值仍在 ⇒ 与原值相同
python backend\.tmp_probe_s4\probe_p118c.py   # 期望：② 252.0 / ③ 冻结后 3.0

# 27) 受影响既有用例（含被新策略改变契约的那条）
python -m pytest tests/test_feature_incremental.py tests/test_graph_universe_freeze.py `
                 tests/test_predict_feature_contract.py tests/test_data_prep.py `
                 tests/test_train_service.py tests/test_feature_asof.py `
                 -p audit_mkdtemp_fix -p no:randomly -q
# 期望：52 passed（其中 test_incremental_preserves_stale_frontier_rows 内含 2a 拒绝 + 2b 授权两段）

# 28) 环境产物单跑核对（与产品缺陷分账：组合跑偶发，单跑必绿）
python -m pytest tests/test_pipeline.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：6 passed（组合跑偶发的 3 条 PermissionError[WinError 5] @multiprocessing\connection.py:575
#       CreateFile + loguru enqueue=True 是沙箱命名管道限制；本轮另见一次 10 分钟挂起，
#       挂起点为 test_market_quotes.py 的 SSE 线程池 shutdown —— 该文件单跑 10 passed/45s，
#       且不触碰 features/graph/pipeline，与 P1-18 改动无关）

# 29) 第 17 轮 · P0-6 空榜裁决（真值表 + 计数本体 + 真实 _screen 端到端，11 条）
python -m pytest tests/test_screener_empty_board_contract.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：11 passed
# 要点：board=bse（快照里 0 只成分股）⇒ unavailable/empty_board，文案含"并非"；
#       同一份 pred+universe 的 board=main ⇒ 正常出榜（证明不是数据缺失）；
#       仅去掉新增的 board_rows 字段 ⇒ 复现旧的 ok/no_matching_signals（缺陷本体反证）

# 30) 反向锁：09-14 的有意设计必须**零改写**仍绿
python -m pytest tests/test_data_freshness_degradation.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：13 passed（含 test_screener_distinguishes_no_signal_and_missing_market_data）

# 31) 受影响面（选股/告警/市场/宇宙 11 个文件）
python -m pytest tests/test_screener_empty_board_contract.py tests/test_graph_universe_freeze.py `
                 tests/test_data_freshness_degradation.py tests/test_screener_snapshot.py `
                 tests/test_screener_dump_step.py tests/test_screener_stocks.py tests/test_api.py `
                 tests/test_alerts.py tests/test_universe.py `
                 tests/test_dead_branch_and_empty_data_codes.py tests/test_p1_data.py `
                 -p audit_mkdtemp_fix -p no:randomly -q
# 期望：109 passed
# ⚠️ 跨用例陷阱（本轮实测踩到并已修）：测试里改 DATA_ROOT 必须用
#    monkeypatch.setattr(get_settings(), "DATA_ROOT", path)（仓库既有 32 处惯例）；
#    用 setenv+DATA_ROOT+cache_clear 会把指向 tmp_path 的 Settings 留在 lru_cache 里，
#    teardown 只还原环境变量 ⇒ 同批次后续用例（test_api/test_alerts）被污染成红灯。

# 32) 第 18 轮 · §8.2 第 17 项 死代码清理的反向锁（成对：删的不复活 / 活的不误删）
python -m pytest tests/test_dead_code_removed.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：29 passed
# 要点：引用扫描用 AST（子串会把 publish_threadsafe / path_for_year / docstring 算成引用）；
#       同时锁住"报告误判为死、实际仍活"的 stamp_duty_rate / check_pct_limit / write_year_batch。

# 33) 第 18 轮 · §8.2 第 20 项 CI/交付门禁
python -m mypy app/ --ignore-missing-imports      # 期望：Success: no issues found in 129 source files
python -m ruff check app tests scripts --select F,E9   # 期望：All checks passed!
python -m pytest tests/test_ci_gate_hygiene.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：9 passed
# 要点：①.dockerignore 必须排除 .env/**.venv/data/node_modules，且**不得**排除 Dockerfile 要 COPY 的
#       16 条路径（逐条断言，防"清理上下文"顺手删掉镜像必需文件）；②镜像 HEALTHCHECK 与 compose
#       healthcheck 必须同 URL **且该 URL 真是 FastAPI 路由**（历史上是 /health 覆盖 /health/ready）；
#       ③scripts/ 的引导段要**真执行**（compile() 只查语法，抓不到 alerter.py 的 NameError）。

# 34) 第 18 轮 · alerter.py NameError（新发现 P1-51）
python -m ruff check scripts/alerter.py --select F821   # 期望：All checks passed!
python scripts/alerter.py                                # 修前：第 20 行 NameError；修后：越过引导段
# 注：修后最终会因沙箱禁止命名管道而在 loguru enqueue 处 PermissionError（环境产物，非产品缺陷）。

# 35) 第 18 轮 · 子代理产出的三份新测试（**父审核员独立复跑，不采信子代理自述**）
python -m pytest tests/test_ops_silent_success_fixes.py tests/test_trading_fee_single_source.py `
                 tests/test_registry_rollback_and_windows.py -p audit_mkdtemp_fix -p no:randomly -q
# 期望：53 passed（7 + 22 + 24）
# 另抽查可验证不变量：DEFAULT_PROMOTE_POLICY.rank_ic_tolerance==0.0（不得为了回滚/regime 门禁放松阈值）；
#       费率字面量 0.0003/0.0005/0.001/0.00001 在 app/ 下只剩 domain/trading_rules.py。

# 36) 第 18 轮 · 全 app 导入烟测（并发编辑后一致性）
python -c "import importlib,pkgutil,app; ns=[m.name for m in pkgutil.walk_packages(app.__path__,'app.')];
[importlib.import_module(n) for n in ns]; print('imported', len(ns))"
# 期望：imported 128（0 失败）
```

### 第 12 轮（S4 监控触发器口径）验证步骤

```powershell
# 25) S4 定向用例（17 条，含真实面板验收；本机有 data/parquet/features 时真跑）
cd backend
$env:PYTHONPATH='D:\Python_Project\Alpha Quant Platform\backend\.tmp_testrun'
python -m pytest tests/test_monitor_trigger_caliber.py -p audit_mkdtemp_fix -p no:randomly -q -s
# 期望：17 passed，且打印
#   [real] psi.raw.max=3.3051 psi.max=0.2024 ks.over_crit_ratio=0.9647
#   [real-inject] ma_gap_250@30% psi.max=0.354 (baseline 0.2024)
#   [real-inject] ma_gap_250@50% psi.max=0.4166 (baseline 0.2024)

# 26) 既有 monitor 契约（3 条旧断言已按新契约改写 + 集成锁/变异反证）
python -m pytest tests/test_monitor.py -q
# 期望：24 passed（含 test_run_monitor_structural_contract 的确定性合成面板 + 变异反证）

# 27) 真实快照独立复核（池宽证据 + 判定口径 vs 池化口径）
python -c "import sqlite3,json;d=json.loads(sqlite3.connect('file:../data/sqlite/aqp.db?mode=ro',uri=True).execute(\"SELECT value FROM app_state WHERE key='monitor_factor_health'\").fetchone()[0]);print('psi.max',d['psi']['max'],'ks.n_over_crit',d['ks']['n_over_crit'],'/',d['ks']['n_factors'],'ic',d['ic_state'],'drift',d['drift_state'],'std_ic',d['history']['std_ic']);print('n_symbols tail',[e['n_symbols'] for e in d['ic_series_tail'][-6:]])"
# 期望（修复前的旧快照）：psi.max 3.3051 | ks 85/85 | ic healthy | drift degraded | std_ic 0.1151
#                            n_symbols tail [..., 1127, 19, 2486, 2484, 2488, 2492]
# 说明：这是**修复前**留下的快照，用于证明缺陷真实存在；修复后重新运行监控会写入新快照
#       （`psi.basis="xsec_standardized"`、`psi.raw.max` 保留 3.3051 量级、`history.sigma_basis`）。

# 28) 性能回归（截面标准化必须整表 2 次 groupby + PSI/KS 共享帧）
python -c "
import time,polars as pl
from pathlib import Path
from app.ml import monitor as M
from datetime import timedelta
v=max([d for d in Path('../data/parquet/features').iterdir() if d.name.startswith('version=')],key=lambda d:max(f.stat().st_mtime_ns for f in d.rglob('*.parquet')))
df=M._normalize_date_col(pl.concat([pl.read_parquet(f) for f in sorted(v.rglob('*.parquet'))],how='diagonal_relaxed'))
df=df.filter(pl.col('date')>=df['date'].max()-timedelta(days=640))
t=time.time();sh=M.prepare_xsec_frames(df);print('prepare',round(time.time()-t,1),'s')
t=time.time();psi=M.compute_psi(df,shared=sh);print('psi',round(time.time()-t,1),'s')
t=time.time();ks=M.compute_ks(df,shared=sh);print('ks',round(time.time()-t,1),'s')
print('psi.raw.max',psi['raw']['max'],'psi.max',psi['max'],'ks.ratio',ks['over_crit_ratio'])"
# 期望：prepare ~5s / psi ~6.5s / ks ~5s（若某次改动退回"每因子一次 groupby"会变成分钟级）
#       psi.raw.max 3.3051 | psi.max 0.2024 | ks.ratio 0.9647（**必须与优化前逐位一致**）

# 29) 第 13 轮 · 组合层约束（P1-13 / B2-12）定向复跑
python -m pytest tests/test_portfolio_constraints.py -q      # 期望：13 passed
# 关键反证（缺陷本体）：行为不变但可判定
python -c "
import numpy as np
from app.domain.optimizer import apply_weight_cap, cap_info_for
w=apply_weight_cap(np.full(10,0.1),0.05)
print('Σw =',float(w.sum()),'（仍为 0.5，行为未改）')
i=cap_info_for(w,0.05);print('feasible =',i['feasible'],'| max_invested =',i['max_invested_ratio'],'| min_cap =',i['min_feasible_cap'])"
# 期望：Σw = 0.5 | feasible = False | max_invested = 0.5 | min_cap = 0.1
#       （修复前：无任何字段可查，API 报 status=ok）
python -c "
import numpy as np
from app.backtest.engine import _summarize_cap_infos
from app.domain.optimizer import cap_info_for
ok=cap_info_for(np.full(10,0.1),0.1);half=cap_info_for(np.full(10,0.05),0.05)
print(_summarize_cap_infos([ok,ok,half],0.05)['invested_ratio_min'])"
# 期望：0.5（取最坏；平均会给出 0.833 而掩盖半仓）

# 30) 第 13 轮 · rf/基准口径 + μ 披露（B2-16 / B2-11）定向复跑
python -m pytest tests/test_risk_caliber_consistency.py -q   # 期望：10 passed
python -c "
import numpy as np,polars as pl,pandas as pd
from app.domain.risk import risk_metrics
from app.domain.metrics import RISK_FREE_ANNUAL
dates=[pd.Timestamp('2024-01-01').date()+pd.Timedelta(days=i) for i in range(300)]
rng=np.random.default_rng(5);close=100*np.exp(np.cumsum(rng.normal(0.0004,0.012,300)))
df=pl.DataFrame({'date':dates,'close':close.tolist()})
r=risk_metrics(df,benchmark=df,window=252);r0=risk_metrics(df,benchmark=df,window=252,rf=0.0)
print('benchmark(默认) =',r['benchmark'],'| rf_annual =',r['rf_annual'])
print('sharpe 位移 =',round(r['sharpe']-r0['sharpe'],4),'| rf/年化波动 =',round(-RISK_FREE_ANNUAL/r['annual_vol'],4))"
# 期望：benchmark(默认) = None（证明硬编码"沪深300"已删）| rf_annual = 0.02
#       sharpe 位移 ≈ rf/年化波动（本例 0.1085），算术自洽
python -c "
import numpy as np
from app.domain.optimizer import mean_variance_weights,robust_cov
rng=np.random.default_rng(3);C=robust_cov(rng.normal(0.0005,0.012,(250,10)))
w8=mean_variance_weights(np.zeros(10),C,risk_aversion=8.0);w50=mean_variance_weights(np.zeros(10),C,risk_aversion=50.0)
print('μ=0: L1(λ=8,λ=50) =',float(np.abs(w8-w50).sum()))
mu=0.0005*np.arange(1,11)/10
print('μ 异质: L1 =',round(float(np.abs(mean_variance_weights(mu,C,risk_aversion=8.0)-mean_variance_weights(mu,C,risk_aversion=50.0)).sum()),4))"
# 期望：μ=0 时 L1 = 0.0（λ 完全无效）；μ 异质时 L1 = 0.4593（缺陷在调用方没给 μ，不在优化器）
```

### ⚠️ 未决观察 → ✅ 第 10 轮已定性并修复（转为发现 #47）

第 9 轮跑"backtest/datacenter/alert/screener/error/rbac/panic/probe/governance/sync"这 **24 个文件的子集**时
出现 6 条失败；同一批文件的全量套件为 0 失败（`1382 passed`），单独跑其中两个文件也全绿（136 passed）。
当时判定为 conftest **会话共享 DATA_ROOT** 下的顺序/数据态产物（与 R9 改动无关，R9 全是等值替换），
但**未放过**失败信息里的健壮性线索。第 10 轮用**私有 DATA_ROOT** 播种"缺列但不空"的分区逐个复现，
**确认为真缺陷并已修**（= 上表 #47）：

| 现象 | 定性结论 |
|---|---|
| 数据集分区**存在但缺列**时，读端点抛未捕获 `ColumnNotFoundError` → `code=50000`（"系统故障"），而非可解释降级 | **真 bug（P2）**，5 处同族：`watchlist._bars_for`、`screener._watchlist_quotes`、`screening.filter_universe`、`desk.capacity`、`paper.screen_universe_candidates`。这些端点**本已定义**合法降级（51001 / 外部源回退 / 价格 None / applied=False 披露），只是判据漏了"列维度"。已加共享判据 `missing_columns`/`require_columns` 并逐处修；反证用例证明修复前写法确实抛 `ColumnNotFoundError`（非 `AQPException`） |

---

## 尚未修复（按 §8.2 顺序，下一批）

| 优先级 | 编号 | 一句话 | 备注 |
|---|---|---|---|
| 1 | ~~P0-1 / P0-2~~ | ~~容器 `PROJECT_ROOT=/` + 默认凭据姿态~~ | ✅ **已修**（见上表 11/12），含 compose 必填校验与文档 §13 重写 |
| 2 | P0-6 | 「空榜」终态契约（需产品裁决） | 已降 P2，待裁决 |
| 3 | ~~P1 摩擦口径三连~~ | ~~换手/印花税/滑点默认值~~ | ✅ **已修 4 项**：P1-1（见 18）、B5-08 区域（见 19）、B5-10（见 20）、**B2-8 涨跌停少 1 分**（见 21）；**滑点默认 5bp 已在 `BrokerConfig` 存在**，仅"策略路径静默失效"（B5-17）未处理 |
| 4 | ~~P1-39~~ | ~~`xsec_demean` 未落盘 ⇒ 门禁比较不可比~~ | ✅ **已修**（见上表 14） |
| 5 | ~~停牌判定~~ | ~~`volume<=0` 是死条件~~ | **已撤回（R-6）：实测证明判定正确** |
| 6 | ~~P1-28~~ | ~~`sync_service.py:657` 的 `clear()` 抹掉断点续传~~ | ✅ **已修**（见上表 15） |
| 7 | ~~P1-40/41~~ | ~~`write_partition` 无锁 + `_audited` 一次性~~ | ✅ **已修**（见上表 16/17） |
| 8 | R10 / §4.13 | 披露字段零落地（17 项两端都无 + 10 项前端零消费），`datacenter/instruments` 的 `total=len(rows)` | 需先裁决 status 取值域归一 |
| 9 | ~~P1-3~~ | ~~模拟盘无持仓校验（裸卖凭空造现金）~~ | ✅ **已修**（见上表 33）：三层校验 + 历史越卖告警 |
| 10 | ~~P1-5~~ | ~~`monthly_monotonic_ratio` 方向反了~~ | ✅ **已修**（见上表 34）：实测完美因子 0.0 / 反向 1.0 → 1.0 / 0.0 |
| 11 | ~~P1-2~~ | ~~策略回测缓存键漏 `use_legacy_engine`~~ | ✅ **已修**（见上表 35） |
| 12 | ~~§4.8 序 16~~ | ~~`Query` 当 `refresh` ⇒ `/settings` 每次清统计缓存（27~30s 重扫）~~ | ✅ **已修**（见上表 36）。**残留同类风险**：全仓仍有 **71 处** `= Query(...)` 作默认值，其中若有被内部直接调用的函数会复现同类问题（本轮只证实并修复 `overview` 这一处，未做 71 处普查） |
| 13 | ~~P1-15 + B7a-03~~ | ~~`quotes_snapshot` 200 截断未披露；ST 过滤静默失效~~ | ✅ **两项均已修**（见上表 37/38）：分片抓取 + 披露；口径随快照落盘 + 未过滤不得报 ok。**残留**：§4.13.3 的 **status 三套取值域归一**（`ok\|degraded\|unavailable` vs `fresh\|degraded`）与其余 5 个裸 list 端点（`stock/search`、`portfolio/search`、`etf/hot`、`etf/flow`、`datacenter/logs`）未做 |
| 13b | ~~第 7 项（静默/错误码簇）~~ | ~~P1-35 日历非法日期 → 40000；P1-36 空涨跌分布不造"情绪 50"；B7a-06 降级 reason 外泄异常串；P1-43 `/health*` 恒 200~~ | ✅ **四项全部已修**（见上表 40/41/42/43）。**残留**：**R9 错误码治理**见下行（已完成） |
| 13d | ~~R9/S14 错误码治理~~ | ~~15 个裸码两端都未登记；`sync/tasks/{id}` 用 `51001` 表"任务不存在"；`40017` 一码三义；两端码表人肉维护~~ | ✅ **已完成**（见上表 45）：裸码清零 + AST 守卫 + 两端逐值逐名对账 + 未知码前端显式标记。**残留（下一批）**：`ERR_DATA_SOURCE`/`ERR_EXPR_INVALID` 仍无抛出点（前者 P1-32 已加 `DataSourceUnavailable`，但 `ERR_EXPR_INVALID` 仍无抛出方）；`_AUTH_DETAIL_CODES` 内 `RATE_LIMITED`/`PIPELINE_BUSY` 映射的清理需与 §8.1 一起做；前端"12 条零引用码"待码表治理完成后才能删（§8.2 第 13 项） |
| 13c | 同簇剩余（R3 静默） | ~~**B7a-08** `research/stress-test` 在 `universe_daily` 无 parquet 时 `pl.concat([])` → 50000；**B7a-09** `market.py` 的 `raw_fallback` **不可达分支**（死代码）~~ | ✅ **两项均已修**（见上表 44）：空数据改 `ERR_DATA_EMPTY(51001)`；死分支删除（OK 路径恒 `hfq`），5 条防回归用例 |
| 14 | P1-13 / B2-11 / B2-12 / B2-16 | 组合层约束（`weight_cap` 不可行 / μ / 归一 / 基准标签） | 影响风险数字可信度 |
| 15 | P1-6 / T8 | 模型回滚通道；验证窗口 regime 化（4 段×6 月滚动 + 符号一致性 + `max(0.002, 2·SE)`） | T8 是 P1-39 之后的下一环 |
| 16 | P1-14 / P1-17 / P1-21 | ETF 资金流字段口径；监控触发器 PSI/KS/σ | 同簇一起做最省 |
| 17 | §6.1 死代码第一批 + CI 门禁 | 删无调用方函数 / 4 个 F841 / 7 个 metrics / 死配置；mypy 10 错、E2E 非阻塞、备份污染 | 机械清理，先跑全量 |
| 18 | 前端 B9a/B8/B9b/B9c + §4.13 披露消费 | 竞态 / 骨架屏 / 角色门槛 / 披露字段前端零消费 | 影响误导呈现 |

> 统计：**P0 六条已全部处置** —— 6 条已修（P0-1/P0-2/P0-3/P0-5/P0-7/P0-8），
> P0-4/P0-6 经红队/证据复核**下调 P2**（编号保留以免引用错位）。
> P1 侧已完成：**P1-1**、**P1-4**、P1-25、P1-26、P1-28、**P1-29**、**P1-30**、
> **P1-31**、P1-32、**P1-39**、**P1-40**、**P1-41**、**P1-42**、P1-44、P1-50、
> R5（未声明公开路由不变量）、**B2-8**、**B5-08 区域的换手取腿漂移**、**B5-10**、
> **B5-16**、**B5-17**、**B5-20**；
> 撤回 2 条报告自身错误结论（**P1-47 = R-6**、**B5-08 = R-7**）。
> 本轮（P1-29/30/31/42 + 数据治理）**新建 2 个测试文件 / 40 条用例**
> （`test_pipeline_state_truth.py` 31 + `test_history_start_floor.py` 7 + 数据治理相关），
> 另修改 3 个既有测试文件（`test_sync_integrity.py` / `test_pipeline.py` / `test_api.py`）。
> 第 5 轮（P1-3/P1-5/P1-2 + §4.8 序 16）**再新建 2 个测试文件 / 13 条 + 改 2 个既有文件各 1 条**
> （`test_paper_position_guard.py` 6、`test_settings_overview_cache.py` 7；
> `test_backtest_cache_key.py` / `test_p2_backtest.py` 各 +1）。
> 第 6 轮（P1-15/P1-34/§4.8b S12）**再新建 3 个测试文件 / 23 条 + 改 1 个既有文件
> 的 2 条逐字段断言**（`test_quotes_snapshot_sharding.py` 6、
> `test_universe_filter_disclosure.py` 12、`test_datacenter_instruments_total.py` 5；
> `test_panic_rootcause_guard.py` 的空态逐字段断言随契约**有意扩展**更新）。
> 第 7–8 轮（P1-35/P1-36/B7a-06/P1-43 + B7a-08/B7a-09）**再新建 3 个测试文件 / 39 条 +
> 改 1 个既有文件（新增 1 条专条 + 去掉 1 处脆弱断言）**（`test_param_date_calendar_validation.py` 21、
> `test_heat_sentiment_and_probe_truth.py` 13、`test_dead_branch_and_empty_data_codes.py` 5、
> `test_read_endpoints_rbac.py`）。
> 累计**新建 30 个测试文件 / 348 条用例**（另在既有 `test_monitor.py` 内新增 1 条、**改写 3 条旧契约断言 + 1 条集成锁并附变异反证**）。
> **全量套件：`1065 passed / 6 failed` → `1429 passed / 0 failed / 8 skipped`（此行为第 12 轮时点快照；累计最新值见上文套件对账表末行 `1464 passed / 0 failed / 8 skipped`）**
> （此前 4 轮稳定残留的 3 条 `test_pipeline.py` 失败系沙箱命名管道限制，第 5 轮起环境未生效而转绿，
> **非代码改动所致**，详见上文"环境说明"）。
> 第 9 轮（R9/S14 错误码治理）新建 1 个文件 / 14 条（含 7 条**变异反证**）；
> 第 10 轮新建 2 个文件 / 20 条（schema 漂移守卫 10 条含 1 条缺陷本体反证；
> F6 复权口径 + P0-4 基准口径 10 条）；
> 第 11 轮新建 1 个文件 / 9 条（P1-14 ETF 资金流口径：真实观测样本复算 + 符号翻转反证）；
> 第 12 轮新建 1 个文件 / 17 条（S4 监控触发器口径：真实面板验收 + 池宽折算 + `_worst`
> + 前端披露锁；另修 3 条旧断言 —— 它们固化的正是审核认定为缺陷的旧契约）。
> 数据治理：2022 前分区 3,417 文件 / 2,584,222 行已移入 `data/_purged_pre2022/`
> （可 `--restore` 回滚；SQLite `trade_calendar` 按决定保留），并由 `HISTORY_START`
> 闸门防止再生。

### 本轮新增的口径变更（会改变历史回测数字，需知情）

| 变更 | 变更前 | 变更后 | 影响 |
|---|---|---|---|
| 换手衰减扣费日程 | 每个交易日都扣（沿用上次调仓换手） | **仅在实际发生换手的交易日扣**（engine 与**策略路径**均如此） | 周频策略成本虚高约 5×→消除；`turnover` 列恢复"当日"语义 |
| 策略路径 decay | **完全没扣**（`apply_decay_cost` 从不被调，B5-17） | 按当日单边换手与声明费率扣（B5-17） | 策略回测成本上升 ≈10bp × 换手；实测 62/90 天入账 13,174.61 元 |
| 单边换手取值 | 取"最后一次 `match` 的那条腿"（口径随执行顺序漂移） | 当日双腿累加后取 `(买+卖)/2`（**买卖均值**口径） | 仅清仓/不对称调仓日不再取错腿；**注意**该口径≠含现金的 Σ\|Δw\|/2（见 23） |
| 印花税 | 恒 0.5‰、ETF 也收 | **法定分段**（2023-08-28 前 1‰）、**ETF 免征** | 2016–2023-08 区间成本不再低估一半 |
| 过户费 | 未建模（B5-18） | **双边 0.01‰**（股票；基金 0） | 换手越高影响越大（81×/年 ⇒ 约 +0.16%/年） |
| ETF 判定 | 四套实现（含 broker 不判品种） | **单一来源** `a_share_rules.is_etf_symbol` | 修掉 `watchlist` 漏 16x + `and/or` 优先级缺陷 |
| 涨跌停取整 | 半分边界**少 1 分**（0.826% 的样本） | 与交易所 `ROUND_HALF_UP` **零不一致** | 涨跌停闸门的判定边缘行改变；**存量分区需重建才生效** |
| 订单可观测性 | 不在 `uni_d`/缺 `cash` 的订单**静默丢弃**（B5-16） | 补 `reason="no_bar"`/`"no_cash"` 记录 | `/backtest/run` 的 `rejected_trades` 汇总新增两类可归因拒绝 |
| `friction_costs` 键 | `{slippage, impact, decay}` | **+`delist_loss`**（P1-4/S2） | 退市强平折价损失首次可见；2 处"全零"断言随契约更新 |
| 退市披露 | 恒定文案宣称"按 delist_date 剔除"（**结构上做不到**） | 由**实测覆盖度**生成 + `delist_coverage` 结构化字段 | 幸存者偏差状态首次显式披露 |
| 逐笔 `pnl` 口径 | 毛盈亏 − **卖出**费用（成本基准不含买入费用） | 毛盈亏 − **买入**费用 − **卖出**费用 | 与 `nav`/`sharpe` 口径统一；`win_rate`/`avg_pnl_ratio` 不再偏乐观（实测同一往返 100%→0% 胜率） |
| 分层多空年化 | `mean × 252`（把 h 日收益当日收益） | `mean × 252 / h` | h=20 时 `+6031%`→`+302%`（消除 20× 放大）；h=1 不变 |
| IC/多空 t 值 | `mean/std × √n`（重叠样本当独立） | `mean/std × √(n/h)` + 披露 `n_independent` | h=20 时 `107.39`→`24.01`（消除 4.47× 高估）；h=1 不变 |
| TopK 页指标显示 | `fmtPct(小数)` ⇒ 小 100 倍、回撤带正号 | `fmtPct(小数 × 100)`、回撤显式取负 | 年化 `+0.12%`→`+12.34%`、胜率 `+0.55%`→`+55.00%`、回撤 `+0.27%`→`−27.00%` |
| `money_flow` 状态 | 任一子源成功即 `ok`（无 `reason`） | 按子源个数三态：3/3 `ok`、1–2/3 `degraded`+`reason`+`n_ok`、0/3 `unavailable` | `/market/overview` 的 `data_freshness` 在外部源部分失效时首次报 `degraded`（此前报 `fresh`） |
| 晚间例行终态 | 流水线 FAILED 也记 `done`（当日不再重试） | 记 `failed` + 有界重试（≤3 次、退避 900s）+ `attempts` 计数 | 榜单停更不再伪装成成功；重试上限避免调度器每 60s 重跑整条流水线 |
| `/sync/fetch` 取锁 | 预检取锁→立即释放→worker **二次取锁**（TOCTOU） | 请求线程**真正持有**并跨线程交接句柄，早退分支归还 | 锁被抢时改为如实返回业务码，不再"回 `started:true` 却零执行且不留案底" |
| 有效历史起点 | 骨架纳入行情里出现过的全部日期（含 2018–2021） | `HISTORY_START = 2022-01-01` 截断（可显式放宽 `start=`） | 早年占位行不再生成；`universe_daily` 减 2,313,074 行、`_bt` 不再有早年残缺宇宙 |
| `universe_daily_bt` 内容 | 冻结在 2026-09-04 / 1,133 只（唯一写入方是手动脚本） | 并入晚间例行 + 重建后 2,495 只 / 2026-09-18 | 回测覆盖面 **+120%**、行数 **+122%**（见下节实测） |

---

## P1-42 实测：回测数据集未接线（本轮最重要的数据面缺陷）

### 症状（只读盘点，`backend/.tmp_testrun/p1_42_staleness.py`）

| 数据集 | 起始年 | 每年标的数 | 最新日期 | 总行数 |
|---|---|---|---|---|
| `universe_daily_bt`（**回测读**，`api/v1/backtest.py:91`） | **2022** | **1,132 ~ 1,133** | **2026-09-04** | 1,283,107 |
| `universe_daily`（screener 读） | 2018 | 2,377 ~ 2,494 | 2026-09-17 | 5,160,640 |

标的覆盖只剩 **45.4%**（缺 1,367 只 = **54.7%**），落后 **9 个交易日**。

### 根因链（`backend/.tmp_testrun/p1_42_rootcause.py`）

```
rebuild_qfq（每晚，在 EVENING_STEPS 内）
   └─> daily_bar_hfq 覆盖扩到 2,500 只
        └─> 但**无人重建** universe_daily_bt（不在任何步骤集，唯一写入方是手动脚本）
             └─> _bt 冻结在"构建当时 hfq 只有 1,133 只"的状态（2026-09-04）
```

实测 `read_all_symbols("daily_bar_hfq")` = **2,500**，而 `_bt` 只有 **1,133** ⇒ 差 1,367（54.7%）✓ 根因成立。

### 修复与收益

1. `orchestrator.py` 新增 `step_build_universe_bt`，并进 `FULL_STEPS` / `STEP_FUNCTIONS`
   （`EVENING_STEPS` 是有序派生，自动纳入），位置**紧随 `build_universe`、在 `build_features` 之前**；
2. 重建 `universe_daily_bt`（先备份旧面板到 `data/_backup_universe_daily_bt_20260921/`）：

| 指标 | 重建前 | 重建后 |
|---|---|---|
| 总行数 | 1,283,107 | **2,851,203**（+122%） |
| 标的数 | 1,133 | **2,495**（+1,362） |
| 最新日期 | 2026-09-04 | **2026-09-18**（+9 交易日） |
| 2022 年标的数 | 1,132 | 2,494 |
| 用时 | — | 52.1s |

重建后 `_bt` 2,851,203 行 与清理后的 `universe_daily` 2,847,566 行**基本一致**
（差 3,637 行 = 退市过滤 + hfq 可得性差异）⇒ 两份面板口径首次对齐。

### ⚠️ 本修复**不覆盖**的部分（如实记录）

`_bt` 的 **2022 起点不是接线缺陷**：抽样 40 只 `daily_bar_hfq` 的最早日期
**中位数 = 2022-08-05**（仅个别老标的有 2018+ 分区）⇒ 2018–2021 缺口属**行情采集
本身的深度限制**，接线修复无法补回，也不打算回填（见下节）。

---

## 2022 前数据治理：隔离 + 防再生闸门（2026-09-21）

### 决定

有效历史自 **2022-01-01** 起，不回填更早数据，并清除既有 2022 前分区。

### 为什么必须清掉（而非"留着也无害"）

骨架是「行情里出现过的全部交易日 × instrument 全表」的笛卡尔积，而
`instrument.list_date` 有 97.8% 为 NULL（缺陷 B5-14）⇒ 上市判据对绝大多数标的失效。
于是仅有个别老标的带 2018+ 分区时，早年**每一天都为所有标的生成占位行**：
实测 `universe_daily` 的 2018–2021 分区共 **2,313,074 行（占其总量 44.8%）**，
绝大多数无任何行情；以早年窗口回测则呈现"全市场"假象（实际仅极少数标的可交易）。

### 执行（已落地）

`backend/scripts/purge_pre2022.py`（新增；默认**干跑**，`--apply` 才移动，`--restore` 可回滚）：

| 数据集 | 文件数 | 行数 | 磁盘 |
|---|---|---|---|
| `universe_daily` | 4 | 2,313,074 | 0.5 MB |
| `cs` | 2,919 | 116,344 | 16.4 MB |
| `features` | 4 | 38,460 | 17.8 MB |
| `daily_bar` | 166 | 39,424 | 1.9 MB |
| `daily_bar_hfq` | 162 | 38,460 | 1.9 MB |
| `daily_bar_qfq` | 162 | 38,460 | 2.3 MB |
| **合计** | **3,417** | **2,584,222** | **40.9 MB** |

- 已移入 `data/_purged_pre2022/`（**保留相对路径、完整性已核**：3,417 文件全部在册）；
  用户选择"移入隔离目录"而非物理删除，确认无误后可直接删该目录。
- 核验（`backend/.tmp_testrun/verify_purge.py`）：数据集内 2022 前残留 = **0**；
  各数据集现覆盖 2022–2026；2022+ 数据未受影响。
- **未动 SQLite `trade_calendar`**（5,332 行 2022 前日期）：它是日历参考表而非行情，
  删掉会让 `prev_trade_day(2022-01-04)=2021-12-31` 查不到，`validate` 退化为覆盖率判据、
  跨年校验失真。SQLite 其余业务表经查无 2022 前数据。

### 防再生（为什么删了不会"自动长回来"）

- 夜间流水线**不会**回抓：`data/pipeline.py` 的 `step_update_daily` 只抓当日
  （`start=end=trade_date`）；`rebuild_qfq` 从 raw 重建，raw 的 2022 前分区已清 ⇒ 无源可依。
- 闸门（新增）：`app/data/universe.py` 的 `HISTORY_START = date(2022, 1, 1)`，
  两个构建器（`build_universe_history` / `build_universe_backtest`）都新增 `start`
  形参且**默认即闸门**、骨架日期先按它截断；显式传更早的 `start` 仍可放宽
  （闸门是默认值，不是硬编码禁区）。
  - 仍未覆盖的口子：手动 `POST /sync/fetch` 显式传 `start<2022` 仍可抓回早年行情；
    届时重建的宇宙也只会纳入该标的（不会像从前那样为全表生成占位行，因为
    `all_dates` 只来自实际行情）。

### 变异反证（确认闸门测试非空转）

临时把两个构建器里的 `.filter(pl.col("date") >= start)` 改成 `>= date(1900,1,1)`
（等价于摘掉闸门）后重跑：

```
FAILED tests/test_history_start_floor.py::test_early_bars_are_excluded_from_grid[build_universe_history]
FAILED tests/test_history_start_floor.py::test_early_bars_are_excluded_from_grid[build_universe_backtest]
E  AssertionError: build_universe_history 骨架含 2019-06-03（2022 前）⇒ 早年占位行会再次生成
E  AssertionError: build_universe_backtest 骨架含 2019-06-03（2022 前）⇒ 早年占位行会再次生成
```

恢复闸门后 7/7 通过 ✓（用例用 `list_date=2019-01-01` 的非 NULL 标的，确保 2019 行被排除
**只可能**源自闸门，而不是"上市前日期过滤"顺手挡掉）。

---

## P1-42 接线过程**暴露的两处新缺陷**（均属"接线才有价值"的收获）

把 `build_universe_bt` 真正接进流水线后，立即在测试套件里炸出两个此前无法暴露的问题
（都不在审核报告里，属**修复阶段新发现**）：

### 新缺陷 A：无 hfq 行情时硬失败会掐断整条夜间流水线

- **现象**：`tests/test_pipeline.py::test_pipe_fail_fast` 变红，流水线在
  `build_universe_bt` 就终止，**够不到** `build_features`——即当日 features / infer /
  榜单全部产不出来。
- **性质**：真实回归风险（不是测试问题）。`universe_daily_bt` 是**回测专用**派生数据，
  一个尚未跑过 `rebuild_qfq`（或 hfq 为空）的部署会因此连**主产品**都停。
- **修法**：无输入 ⇒ **显式跳过**而非 fail-fast：`step_build_universe_bt` 先查
  `read_all_symbols("daily_bar_hfq")`，为空则 `logger.warning` + 返回
  `"skipped=no_hfq_bars"`（在 `data_jobs` 的步骤 detail 里可见）。
  跳过**必须留痕**，否则就退回 P1-42 最初的"静默停更"。
- **验证**：`test_build_universe_bt_skips_loudly_when_no_hfq`（detail 必须等于
  `skipped=no_hfq_bars` 且不得调用构建器）+ 既有 `test_pipe_fail_fast` **无需改动**即恢复
  （它同时就是"流水线仍能走到 `build_features`"的端到端守卫）。
- 未采用"把该步挪到 infer 之后"的方案：会破坏"数据重建完再派生"的顺序，且仍会在后续
  步骤处 fail-fast。

### 新缺陷 B：`list_date` 全为 NULL 时构建器直接崩（dtype 退化）

- **现象**：`InvalidOperationError: - not allowed on date and null`
  （`build_universe_backtest`）。
- **根因（实测复现）**：若 instrument 表里**每一条** `list_date` 都为 NULL，polars 把该列
  推断为 **`Null` dtype**，而骨架要算 `date - list_date`：

  ```
  [单个标的、list_date=NULL] list_date dtype = Null
    未 cast：**失败** InvalidOperationError: - not allowed on date and null
    cast 后：OK  days_since_list=[None]
  [混合（一个 NULL 一个有值）] list_date dtype = Date
    未 cast：OK  days_since_list=[None, 790]
  ```

  生产有 122 条非空（列是 Date，cast 为空操作），故**只在全新库/未跑 enrich 的库炸**——
  正是"接线后才可能触发"的潜伏缺陷。
- **修法**：在**载入处**归一 dtype —— `_load_instruments_df()`（bt 构建器）与
  `build_universe_history` 内的 `_load_instruments()`（raw 构建器）都对 `list_date`
  显式 `cast(pl.Date)`。
- **变异反证**：临时去掉 cast ⇒ `test_builder_survives_all_null_list_date` 立刻复现
  `InvalidOperationError: - not allowed on date and null`；恢复后 31/31 通过。
- 附带修掉测试侧的同类隐患：`tests/test_pipeline.py` 的 `pipeline_env` 把
  `build_universe_bt` 加入"需数据步骤"的 stub 列表（与 `rebuild_qfq`/`build_universe`/
  `build_cs_mirror` 同列，理由同为"依赖 instrument 表、有专项测试"），并顺带**避免**
  真实执行往共享临时 `DATA_ROOT` 写 `universe_daily_bt` 分区——
  `tests/test_api.py` 也往同一数据集 seed 自己的内容，真实执行会**污染兄弟用例**。
  相应地，`test_pipe_order_and_success` / `test_pipe_retry_after_failure` 的确切
  `calls` 清单断言随契约加入 `build_universe_bt`（位置紧随 `build_universe`）。

### 附带发现：`test_overview_async` 的跨用例耦合（日界/顺序 flake）

- **现象**：全量套件在 **00:04** 那次运行里，该用例
  `assert data["from_cache"] is True` 变红（同一次运行的产品失败只有它一条）。单独跑
  `tests/test_api.py` = 24 passed；我的改动与该用例无关（`market.py` 的
  `_is_degraded` 抽取为**语义等价**改动：同一 `.values()` 集合、同一条件）。
- **机制判定**（`backend/.tmp_testrun/b_overview_cache_key.py`，可判定实验）：
  缓存键 `k_market_overview(date_yyyymmdd, k)` **含交易日**（`app/cache/keys.py:11-16`）：

  ```
  第 2 次（同一交易日）：from_cache=True   → 命中
  第 3 次（交易日 +1 天）：from_cache=False  trade_date=20260923
  ```

  该用例原本**只发一次请求**，命中依赖同文件前序用例预热 LRU ⇒ 一旦跨过**日界**（或
  预热被其它因素清掉），键就变了，必然为红。
- **排除短 TTL 路径**（`backend/.tmp_testrun/b_overview_ttl.py`）：
  载荷**无顶层 `status`**（实测 `顶层 status=None`），而 `swr._effective_ttl` 仅在顶层
  `status == "degraded"` 时收敛到 `DEGRADED_TTL_SECONDS=15` ⇒ 该键走完整 TTL；
  16s 后复请求仍 `from_cache=True`。故**不是** 15s 短 TTL 导致。
- **修法**（测试侧加固，不掩盖问题）：改为**自证式**——本用例自己连发两次并断言第二次
  命中（保留原意图"异步路径可写可读 LRU"，且比原来更强：不再依赖任何外部状态），
  并在两次 `trade_date` 不一致时直接给出"跨日界"诊断。
- **验证**：`tests/test_api.py` 68 passed（与相关两文件合跑）。

> 统计口径说明：本轮**新建 2 个测试文件**（`test_pipeline_state_truth.py` 31 条、
> `test_history_start_floor.py` 7 条），另修改 3 个既有测试文件
> （`test_sync_integrity.py` / `test_pipeline.py` / `test_api.py`）。全量
> `1249 → 1287`，增量 **+38** = 新增 29（P1-29/30/31/42）+ 新增 9（闸门与本次两处修复）
> = 38 ✓ 逐条对账成立。