# AQP 最终项目综合评审

> 评审基准：当前仓库真实代码 + 真实执行结果，不采信 README / 项目开发文档 / 历史完成报告。
> 未验证项已标注 `NOT VERIFIED`。
> 执行环境：Windows / Python 3.11.15 (`backend/.venv`) / Intel Core Ultra 5 125H / 32GB。
>
> 实际执行的验证动作：
> `pytest -q` · `mypy app` · `ruff check .` · `tsc -b && vite build` ·
> `uvicorn` 实启动 + 4 个接口冒烟 · 真实 121 只股票全量训练（3 组对照）·
> 数据审计（schema / 复权连续性 / 标签分布）· 回测仓位复现脚本。

---

## 1. 总体结论

**一句话：架构和代码质量明显好于同规模个人项目，但它现在还不是一个可以做量化研究的平台——数据层损坏、回测跑不起来、被服务的模型是反预测的。**

分层是干净的：`domain/` 确实保持纯函数，`data / ml / backtest / api` 职责边界清楚，`mypy` 59 个文件全绿，前后端错误契约统一。这些是真实优点，不是文档吹出来的。

但把这些部件接起来之后，实际研究链路是断的：

```
数据采集 ──▶ Parquet ──▶ 因子 ──▶ 训练 ──▶ 预测 ──▶ 选股 ──▶ 回测
   ✅         ⚠️损坏      ✅      ⚠️退化    ⚠️退化     ✅       ❌跑不动
              (600519    (架构对)  (1棵树)  (7个值)   (真数据)  (1个交易日)
               断裂)
```

三个互相独立的 Critical 缺陷，恰好分别打在数据、回测、ML 三条主链上：

1. **回测买单按「卖出前」现金计算** → 等权策略稳态仓位只有 58.7%，两只持仓 2:1 而非 1:1；
2. **后复权数据损坏且无校验拦截** → 0.076% 的标签达到 +66,910%，把 L2 回归彻底带偏；
3. **生产模型无治理** → 线上服务的是 1 棵树的模型，valid IC −0.037、test IC −0.365。

加上 `universe_daily` 只有 **7 行 / 1 个日期**，回测子系统在真实数据上最多只能"回测" 1 天。

**好消息是：这些全是定点可修的，不需要推倒重来。** 缺陷集中在"数据治理"与"实验治理"两个层面，而不是架构设计。架构本身值得继续投入。

---

## 2. 各模块评分

| 模块 | 评分 | 依据（每条都有执行证据） |
|---|:---:|---|
| **Architecture** | **B+** | 分层清晰、domain 纯函数、依赖方向正确；但流水线缺 train 步骤、无调度器，`step_build_features` 每天全量重建 |
| **Data** | **C** | 真实 akshare 数据 121 只 × 2022-01~2026-08；但 3 个文件 schema 漂移、600519 后复权断裂、103 条污染标签、校验层未拦截 |
| **ML** | **C−** | 切分纪律正确（无泄漏）；但生产模型 1 棵树、test IC −0.365、`is_production` 恒为 0、按目录名选"最新"模型 |
| **Backtest** | **B−** | T+1/涨跌停/停牌/整手/佣金/印花税规则实现质量高；但仓位计算 Critical bug，且因 universe 缺失实际跑不动 |
| **Backend** | **B+** | 统一响应信封 + trace_id、全局异常不泄漏堆栈、mypy 全绿；但 251 条 ruff 问题、含 1 个真实 `NameError` |
| **Frontend** | **B−** | 仅 2,743 行，axios 拦截器错误处理干净、tsc/build 通过；但信息密度低、1.3MB 单包无分包、industry/risk 字段失效 |
| **Security** | **C+** | PBKDF2 100k + `compare_digest` + JWT + RBAC 实现质量好；但 RBAC 只挂在 `/auth/me`，无 `.env` 导致密钥为默认值 |
| **Performance** | **C+** | `/market/overview` 9.57s；分区方案在 5000 股规模会退化成 25,000 个小文件 |
| **Reliability** | **C+** | Redis 不可用优雅降级内存缓存 ✅；但测试直写生产 DB、无并发锁、fire-and-forget 任务 |
| **Maintainability** | **B+** | 4,271 行 app 代码 / 2,640 行测试，规模可控，类型注解与文档字符串完整；但非 git 仓库、测试顺序依赖 |
| **Extensibility** | **B+** | 加因子/模型/API/图表很容易；换数据源与改分区较难 |
| **Observability** | **C+** | 有 trace_id / job 表 / feature_runs；但无指标采集、模型版本不可用于追踪线上预测、feature_runs 685 行几乎全是空指标 |

**总评：B−**

---

## 3. Critical / High 风险

### CRIT-001 · 回测买单金额按「卖出前」现金计算，等权策略仓位严重失真

| 项 | 内容 |
|---|---|
| **Severity** | Critical |
| **文件/模块** | `backend/app/backtest/engine.py:114`（`run_backtest`）、`:218`（`run_group_backtest` 同款） |
| **问题** | `each_cash = broker.cash * 0.95 / len(tradable)` 在**订单生成阶段**取值，而 `broker.match()` 是**先卖后买**。卖出回笼的资金不会进入当日建仓预算。 |
| **原因** | 订单生成与撮合分两阶段，但资金预算用了阶段一的快照。 |
| **可能后果** | 等权 Top-K 策略实际只在"昨日剩余现金"范围内建仓，卖出所得闲置，导致仓位利用率随换手随机震荡，绩效指标不可信。 |
| **复现方式** | 4 只股票、价格恒定 10 元、top_k=2、每天换 1 只（25% 换手）、20 个交易日，直接调 `run_backtest`。 |

**实测输出（节选）：**

| date | cash | equity | 股票市值 | 仓位利用率 | 持仓 |
|---|---:|---:|---:|---:|---|
| 2024-01-02 | 49,715 | 999,715 | 950,000 | **95.0%** | A 47500 / B 47500 |
| 2024-01-03 | 478,321 | 999,321 | 521,000 | **52.1%** | B 49800 / C 2300 |
| 2024-01-04 | 521,787 | 998,787 | 477,000 | **47.8%** | C 25000 / D 22700 |
| 2024-01-05 | 277,438 | 998,438 | 721,000 | **72.2%** | D 47400 / A 24700 |
| … | | | | | |
| 2024-01-26 | 409,985 | 991,985 | 582,000 | **58.7%** | C 38800 / D 19400 |

- 平均仓位利用率 **57.1%**（应为 ~95%）
- 稳态下两只"等权"持仓为 **38,800 : 19,400 股 = 2:1**（应 1:1）

**建议修改**：把目标权重先算出来（`target_weight = 1/len(targets)`），在 `match()` 之后再按「卖出后可用现金 + 持仓市值」统一计算目标股数；或改为 `broker.match()` 内部两阶段（先卖 → 再按可用现金分配买）。

---

### CRIT-002 · 后复权价格断裂 + Parquet schema 漂移，且校验层完全未拦截

| 项 | 内容 |
|---|---|
| **Severity** | Critical |
| **文件/模块** | `data/parquet/daily_bar*`、`app/data/ingest/validate.py`、`app/data/ingest/tasks.py` |
| **问题** | 600519.SH 的 2023/2024 分区被一次错误回补覆盖，后复权价格量级从 ~1.5 万掉到 ~20；同时这 3 个文件的列结构与其他 604 个文件不一致。 |
| **原因** | 不同批次写入路径产生了不同 schema 与不同复权基准；`validate_daily_bar` 只做单日字段校验，不做跨分区/跨批次连续性校验。 |
| **复现方式** | 见下方实测。 |

**实测 A — schema 漂移（对数据集整体扫描直接失败）：**

```
daily_bar      : 604 文件 cols=[date,open,...,symbol,source]
                   1 文件 cols=[symbol,code,date,open,...,pct,turnover]   ← 600519.SH/year=2024
daily_bar_hfq  : 603 文件 cols=[date,...] (11 列)
                   2 文件 cols=[symbol,code,date,...] (8 列)              ← 600519.SH/year=2023,2024

pl.scan_parquet("daily_bar/**/*.parquet").collect()
  → SchemaError('schema names differ at index 0: date != symbol')
pl.scan_parquet("daily_bar_hfq/**/*.parquet").collect()
  → SchemaError('schemas contained differing number of columns: 11 != 8')
```

**实测 B — 600519.SH（贵州茅台）后复权断裂：**

| 分区 | daily_bar (raw) | daily_bar_hfq |
|---|---|---|
| 2022 | 1350.00 ~ 2051.23 ✅ | 10476 ~ 15869 ✅ |
| 2023 | 1628.90 ~ 1912.90 ✅ | **15.94 ~ 26.71** ❌ |
| **2024** | **1 行，close = 10.10** ❌ | **18.95 ~ 25.75** ❌ |
| 2025 | 1377.18 ~ 1637.86 ✅ | 11746 ~ 13705 ✅ |

单日跳变实测：`2023-01-02 −99.85%`、`2025-01-02 +56,147%`、`2026-05-06 −88.49%`。
另有 `000001.SZ` 与 `300750.SZ` 在 `2026-05-06` 复权因子同时塌缩为 **1.0**（hfq 写成了 raw）。

**实测 C — 传导到标签：**

```
|5日收益| > 50% 的样本：103 条（占 0.0762%），涉及 32 只股票
  600519.SH 2023-12-26  close=18.58    label = +669.11   (+66,910%)
标签 std = 3.5227      ← 正常 A 股 5 日收益 std 应约 0.05 ~ 0.08
```

**可能后果**：0.076% 的样本足以主导 L2 损失，直接导致 CRIT-003 的模型退化；任何包含 600519 的回测/IC 计算均无效。

**建议修改**
1. 增加**跨批次连续性校验**：新数据落库前，与上一交易日的 `hfq/raw` 复权因子比对，单日因子变动 > 阈值（如 25%）或 |日收益| > 22%（非 ST/新股）即拒绝写入并告警。
2. 落库前统一 schema（`select` 固定列顺序 + 列集合），写完后做一次 `scan_parquet` 全量可读性自检。
3. 训练侧对 label 做 winsorize/clip（如 ±3×MAD 或 ±0.5），并对特征做同样的极值处理。
4. 隔离并重建 600519.SH 的 2023/2024 分区。

---

### CRIT-003 · 生产模型退化：服务的是 1 棵树、test IC 为负的模型

| 项 | 内容 |
|---|---|
| **Severity** | Critical |
| **文件/模块** | `app/ml/infer.py:25 load_prod_model`、`app/ml/train_lgbm.py:173 _register_model` |
| **问题** | `load_prod_model()` 取 `MODEL_ROOT` 下**按目录名排序的最后一个**目录；而 `_register_model()` 恒写 `is_production=0`，`model_registry` 表**只写不读**。 |
| **原因** | 模型治理未闭环：注册表建了但没接进推理路径。 |
| **可能后果** | 任何一次实验性/测试性训练（当前 MODEL_ROOT 下有 **384+** 个目录，含 `pytest` / `leakA` / `leakB` / `audit` / `pipe` 后缀）都会静默成为"生产模型"。 |

**实测（查询当前被服务的模型）：**

```
is_production 分布：[(0, 72)]        ← 72 条全是 0，没有任何一条被标记为生产
load_prod_model() 选中：lgbm_v1_20260830_021429_pipe     （一个 pipeline 跑出来的模型）

该模型指标：
  best_iteration = 1        ← 只有 1 棵树
  num_trees      = 1
  gain > 0 的特征  = 6 / 30
  valid_ic       = -0.0366
  test_ic        = -0.3653        ← 强负相关
  test_rank_ic   = nan
```

**传导到选股（实测）：**

```
predictions/date=20260828.parquet：121 只股票，pred_score 只有 7 个不同取值
  min=0.002317  max=0.008655  std=0.001075
risk 分布：low(>=0.3)=0   mid(>=0.1)=0   high=121    ← 121/121 全是 "high"
```

`risk` 阈值 0.3 / 0.1 与实际 pred_score 量级（0.002~0.009）差了两个数量级，该字段 100% 无意义。

**建议修改**
1. `load_prod_model()` 改为读 `model_registry WHERE is_production=1`（唯一一条），读不到就报错而非回退"最新"。
2. 增加 promote 步骤：只有当 `valid_rank_ic`、`test_rank_ic` 同时超过阈值（建议 |RankIC| ≥ 0.03）且优于当前生产模型时，才允许置 `is_production=1`。
3. 训练产物目录与实验目录分离（`models/prod/` vs `models/exp/`），测试用 `MODEL_ROOT` 必须走 tmp_path。
4. 修掉 `risk` 字段：要么改为真实风险口径（波动率/回撤），要么从 API 移除。

---

### HIGH-001 · `universe_daily` 只有 1 个交易日，回测子系统实际不可用

| 项 | 内容 |
|---|---|
| **Severity** | High（功能性致命） |
| **文件/模块** | `data/parquet/universe_daily/`、`app/api/v1/backtest.py:49` |
| **问题** | `universe_daily` 仅 `year=2024` 一个分区、**7 行**、**单日 2024-06-05**；而 predictions 只有 4 个日期（20240605 / 20260826 / 27 / 28）。两者交集 = **1 个交易日**。 |
| **复现方式** | `POST /api/v1/backtest/run {"start":"2026-01-01","end":"2026-08-28","top_k":5}` |
| **实测** | `HTTP 200, code=51001, message="回测区间内无 universe 数据"` |

**可能后果**：engine + broker 中精心实现的 T+1 / 涨停 / 跌停 / 停牌 / 整手 / 佣金 / 印花税，在真实数据上端到端几乎无法被触发 —— 这些规则**存在但没进最终业务链路**，恰恰是本次评审重点检查项。

**建议修改**：补一个 `build_universe` 步骤（或进流水线 `STEPS`），按交易日全市场生成 `universe_daily`（含 is_st / is_halted / limit_up / limit_down / industry）。

---

### HIGH-002 · 测试直写生产数据库，且存在顺序依赖型不稳定

| 项 | 内容 |
|---|---|
| **Severity** | High |
| **文件/模块** | 缺 `tests/conftest.py`；`tests/test_p2_ml.py:99`、多处在用 `get_settings().SQLITE_PATH` |
| **问题** | 无 conftest 做 fixture 隔离，测试直接用生产 SQLite 路径，结果跨运行累积。 |
| **复现方式** | `pytest -q`（首次运行 5 failed / 129 passed / 5 skipped；`test_p2_ml.py` 单独跑稳定失败） |

```
FAILED tests/test_p2_ml.py::TestGridSearch::test_feature_runs_migration_and_record
  assert 648 == 81        ← 81 × 8 次运行累积

生产库现状：
  feature_runs   = 685 行（且 metrics 列几乎全为 NULL）
  model_registry = 72 行（全部来自 pipe / pytest 运行）
```

同时 `test_pipeline.py` 单独跑 **6 passed**，完整套件里却**间歇性 4 连败** → 存在全局单例（`get_settings` 为 `lru_cache`）导致的状态污染。

**建议修改**：加 `conftest.py`，用 `tmp_path_factory` 为每个 session 注入独立的 `AQP_SQLITE_PATH` / `DATA_ROOT` / `MODEL_ROOT`，并 `get_settings.cache_clear()`。

---

### HIGH-003 · 每日流水线不含训练步骤，且项目内无任何调度器

| 项 | 内容 |
|---|---|
| **Severity** | High |
| **文件/模块** | `app/data/pipeline.py:35` |
| **问题** | `STEPS = ["update_daily", "validate", "build_features", "infer", "screener_dump"]` —— **没有 train**。全仓无 `schedule` / `cron` / `apscheduler`（已 grep 确认）。 |
| **可能后果** | 模型永不自动重训；每日 infer 用的是几个月前某次手工训练的产物。对一个"长期量化研究平台"来说，研究闭环是断的。 |

附带：`step_build_features` 每天 `pd.concat` 全部标的全历史重算因子（docstring 却写"增量重建"），且 `update_daily` 默认只更新 3 只（`codes=["600519","000001","300750"]`），而 `build_features` / `infer` 用的是全部 121 只 —— 步骤间口径不一致。

**建议修改**：在 `build_features` 与 `infer` 之间插入 `train` 步骤（走 CRIT-003 的 promote 门禁）；或至少提供带调度的一等公民脚本（Windows 任务计划 / cron）。

---

## 4. Medium / Low 风险

| ID | Sev | 文件/模块 | 问题 | 建议 |
|---|---|---|---|---|
| MED-001 | Medium | `app/api/v1/market.py` | **内部异常串直接进 API 响应**：`/market/overview` 实测返回 `sectors.reason = "ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"` —— 正是本次要求排查的"内部错误暴露给用户"。 | 统一降级函数，对外只回固定文案（如"板块数据暂不可用"），细节只进日志 |
| MED-002 | Medium | `app/api/v1/market.py` | `/market/overview` 实测 **9.57s**（P95 NOT VERIFIED）。个人工作台交互不可接受。 | 拆分为并行子接口 + 缓存；板块/异动等外部源改为后台刷新 |
| MED-003 | Medium | `app/data/parquet_store.py` | 分区为 `symbol=X/year=Y`，121 股 = 605 个文件；按日期取截面需打开**全部**文件。5000 股 × 5 年 → **25,000 个小文件**，每次截面读取 25k 次 IO。 | 增加按 `date=` 分区的镜像（或改为 `year=Y/date=D` 的宽表），截面查询走列裁剪 + 谓词下推 |
| MED-004 | Medium | `app/ml/train_lgbm.py:148` | 特征筛选用**全期池化 RankIC**，非日度 IC 均值；阈值 `min_abs_rank_ic=0.003` 过松（实测 42 个因子保留 40 个，等于没筛）。 | 改用日度 RankIC 均值 + ICIR 联合筛选；提高阈值 |
| MED-005 | Medium | `app/ml/train_lgbm.py:292` | 早停监控 `l2`，但对外汇报 `IC / RankIC / ICIR` —— **优化目标与评价指标不一致**。 | 自定义 feval 用 RankIC 早停，或至少两者都报并说明 |
| MED-006 | Medium | `app/ml/train_lgbm.py:251` | `train_lgbm` **内部重算 label**（`shift(-horizon)`），忽略调用方传入的 label → 调用方无法提供已清洗标签；且删行会产生新的跨界污染标签（实测清洗后 train_rmse 仍 4.63）。 | 支持传入预计算 label；或内置 clip/winsorize |
| MED-007 | Medium | `app/backtest/engine.py:252` | `group_turnover` 返回的是 `b.last_day_turnover`（**最后一天**的换手），字段名暗示是分组换手率。 | 改为期间均值并重命名 |
| MED-008 | Medium | `app/backtest/broker.py:142` | **无退市处理**：退市后 `uni_d` 中查无此股，估值沿用 `_prev_close`，市值被永久冻结 → NAV 虚高。 | 增加退市日标记，按最后成交价平仓并移出持仓 |
| MED-009 | Medium | `app/api/v1/backtest.py:36` | `model_version` 参数**只进缓存键**，不参与模型选择 —— 用户以为在回测某版本，实际用的还是全量 predictions 文件。 | 实现真实版本选择，或移除该参数避免误导 |
| MED-010 | Medium | `app/core/config.py:39` | 无 `.env` → `ADMIN_TOKEN = "aqp-dev-token-change-me"`，JWT 密钥派生为 `"aqp-derive:aqp-dev-token-change-me"`（公开已知）。`API_HOST` 默认 `0.0.0.0`，业务接口无鉴权。 | 首次启动若检测到默认 token 则强制拒绝启动或自动随机生成 |
| MED-011 | Medium | `app/core/auth.py` | RBAC 三角色 + 权限矩阵 + PBKDF2 实现质量好，但 grep 显示**只挂在 `/auth/me`**，market/stock/screener/backtest/export 全部无鉴权。 | 若定位纯本地可保留现状，但应在 README 明确写"不可暴露到非本机网络"；否则补鉴权 |
| MED-012 | Medium | 项目根 | **不是 git 仓库**（`git rev-parse` 失败）。长期研究平台无版本历史 = 无法回溯任何一次实验对应的代码状态。 | 立即 `git init` 并提交（注意 `.env` 已 gitignore ✅） |
| LOW-001 | Low | `scripts/alerter.py:20,21` | **ruff F821 `Undefined name sys`** —— 使用了 `sys.` 但未 import，运行时必抛 `NameError`。 | 补 `import sys` |
| LOW-002 | Low | `app/api/v1/screener.py:204` | `loop.create_task(_log_run())` fire-and-forget 且未 await，异常仅靠 callback 记日志；进程退出时可能丢写并产生 "Task exception was never retrieved"。 | 改为 `await` 或后台任务队列 |
| LOW-003 | Low | `app/data/pipeline.py:192` | `asyncio.get_event_loop().run_until_complete()` 在 3.12+ 已废弃。 | 复用外层 loop 或改同步写入 |
| LOW-004 | Low | `app/domain/limit.py:69` | `is_new_issue_first_n_days` 用**自然日**判断"上市前 5 个交易日"（`(today - list_date).days < n`）——周五上市的新股会把无涨跌幅期少算 2 天。 | 改用交易日历计数 |
| LOW-005 | Low | `app/backtest/broker.py` | 缺**过户费**（2022 起沪深双边 0.001%）；`_locked_today` 的 T+1 分支在 engine 主路径不可达（engine 只卖 `holdings`）。 | 补过户费；补一条显式 T+1 用例让规则真正被触发 |
| LOW-006 | Low | `app/data/migrations.py:10,14` | `text(f"PRAGMA table_info({table})")` / `ALTER TABLE ... {col}` 用 f-string 拼 SQL（当前入参均为内部常量，风险为 0，但模式不安全）。 | 白名单校验表名/列名 |
| LOW-007 | Low | `frontend` | 单包 **1,304 KB / gzip 434 KB**，无 code splitting。 | `manualChunks` 拆出 echarts / lightweight-charts / react |
| LOW-008 | Low | `app/domain/limit.py:99` | 主板 2023-04 前新股首日 ±44%/−36% 以 `prev_close` 近似发行价（首日二者相等，可接受，但注释未说明）。 | 补注释说明近似前提 |

---

## 5. 数据与量化正确性

### 数据层规模与真实性

| 数据集 | 分区 | 文件数 | 覆盖 | 状态 |
|---|---|---:|---|---|
| `daily_bar` | `symbol=X/year=Y` | 605 | 121 股 × 2022-01-04~2026-08-28，135,620 行 | 真实（source=akshare 100%）⚠️ 3 文件 schema 漂移 |
| `daily_bar_hfq` | 同上 | 605 | 同上 | ⚠️ 同上 + 复权断裂 |
| `features` | `version=alpha_basic_v1/year=Y` | 5 | 135,583 行 × 45 列 | ✅ 基于 hfq 构建，asof 稳定 |
| `predictions` | `date=YYYYMMDD` | 4 | 20240605 / 20260826 / 27 / 28 | ⚠️ 7 个不同分值 |
| `universe_daily` | `symbol=__all__/year=Y` | 1 | **7 行 / 1 个日期** | ❌ 见 HIGH-001 |
| `announcements` | 同上 | 1 | 1 个分区 | NOT VERIFIED（未接入主链路） |

**数据层是否足够可靠，可以作为长期量化研究的数据基础？—— 目前不能。**

理由：真实性和覆盖度没问题（真 akshare、4.7 年、121 股），但**可靠性机制缺失**：schema 漂移未被发现、复权断裂未被拦截、污染标签直达训练。这三个问题都属于"加一道校验就能防住"，但没有那一道校验。

**但也有做对的地方**：分区用 `symbol=/year=` hive 格式、写入带 `source` 字段、`MODEL_ROOT` 每次训练独立目录、`features` 按 `version=` 分区（数据版本可追踪 ✅）、`features` 明确基于 hfq 构建并在 `test_feature_asof.py` 中守卫 asof 稳定性 —— 这个设计意识是对的。

### 量化规则是否真正进入最终业务链路

| 规则 | 实现位置 | 是否进入链路 | 实测判定 |
|---|:--|:--:|---|
| 交易日 | `domain/calendar.py` + `data/calendar_store.py` | ✅ | 流水线非交易日直接 FAILED，不伪造行情 ✅ |
| 复权 qfq/hfq | `domain/adjust.py` | ✅ | 因子基于 hfq，asof 稳定；**但源数据 hfq 本身损坏**（CRIT-002） |
| 涨停 / 跌停 | `broker.py:204 / :174` | ⚠️ | 规则正确（含 ST ±5%、创业板/科创板 ±20%、北交所 ±30%、Decimal ROUND_HALF_UP）；但缺少 universe 数据无法端到端触发 |
| 停牌 | `broker.py:172` `halted or volume<=0` | ⚠️ | 同上 |
| 新股 | `domain/limit.py:93` | ⚠️ | 有实现，但用自然日而非交易日（LOW-004） |
| ST | `domain/limit.py:109`（±5%） | ⚠️ | 计算正确；screener 侧 ST 过滤依赖 universe，universe 缺失 → 未生效 |
| T+1 | `broker.py:178` + `mark_to_market` 解冻 | ⚠️ | 机制正确，但 engine 只卖 `holdings`，`_locked_today` 的拒绝分支在主路径不可达 |
| 100 股整手 | `broker.py:212` `floor(qty/100)*100` | ✅ | 实测生效（47,229 元 → 4,700 股） |
| 佣金 | `broker.py:135` `max(5, amt*0.0003)` 双边 | ✅ | 最低 5 元 + 双边 ✅ |
| 印花税 | `broker.py:138` 卖出 0.0005 | ✅ | 仅卖出 ✅ |
| 过户费 | —— | ❌ | 缺失（LOW-005） |
| Signal Lag | `engine.py:96` `dates[i-signal_lag]`，且 `signal_lag<1` 直接抛错 | ✅ | **防未来信号纪律正确**，值得肯定 |
| Lookahead | 特征全部 `rolling/shift(+)`，无 `shift(-N)` 特征 | ✅ | `test_feature_asof.py` + `test_ml_leakage.py` 守卫 |

**结论**：A 股规则的实现质量（尤其涨跌停用 Decimal 规避银行家舍入、signal_lag 硬约束）在个人项目里属于上乘。问题不在"有没有写"，而在"跑不起来"—— 缺 universe 数据让这些闸门无法在真实回测中被检验。

---

## 6. ML 质量

### 逐项回答

**1) 有没有数据泄漏？—— 没有发现。**
切分纪律是这次评审里最扎实的部分：
- `train ──gap── valid ──gap── test`，`gap_days` 默认 = horizon（5），保证 label 窗口不跨界 ✅
- 特征筛选**仅用 train 段**（`X.loc[train_mask]`）✅
- `X_te` 刻意不提前构建，"从代码路径上杜绝 test 参与训练/早停" ✅
- 特征白名单排除 `close` 与 `label_ret` ✅
- `test_feature_asof.py` / `test_ml_leakage.py` 有专门守卫 ✅

**2) 有没有 train/serve skew？—— 有风险，未防护。**
`FEATURE_VERSION` 只在日志里打印（`infer.py:118`），**推理时不校验特征版本与训练时是否一致**。若用 `features_v2` 跑推理而模型训于 `v1`，只要列名对得上就会静默通过。

**3) 有没有 test contamination？—— 没有。**
test 段仅在 `_eval(test_mask)` 出现一次，早停只看 valid。

**4) 模型是否真正按时间顺序训练？—— 是。**
`split_dates` 按日期排序切分，无 shuffle。

**5) inference 是否使用正确生产模型？—— 否。** 见 CRIT-003。

**6) 模型版本是否可追踪？—— 部分。**
`model_registry` 表存在，`predictions` parquet 里带 `model_version` 列 ✅；但 `is_production` 恒 0、推理不读表（CRIT-003）。

**7) 数据版本是否可追踪？—— 是。** `features/version=alpha_basic_v1/` ✅

**8) 实验能否复现？—— 弱。**
`seed=42` 固定 ✅；但 `feature_runs` 表 685 行几乎全为 NULL 指标，无超参记录、无 git commit 关联，且项目非 git 仓库。

### 指标可信度（真实数据实测）

在**真实 121 只股票、2022-01~2026-08、holdout=252 / test=252 / gap=5** 下完整训练：

```
train: 2022-01-04 ~ 2024-07-10  n=73,435
valid: 2024-07-18 ~ 2025-07-31  n=30,318
test : 2025-08-08 ~ 2026-08-21  n=30,224

best_iteration = 1        ← 早停在第 1 轮就触发
train_rmse     = 4.7755   ← 与 valid_rmse 0.0729 差 65 倍
[train] IC=+0.1275  RankIC=+0.0624  ICIR=+0.4677
[valid] IC=-0.0051  RankIC=+0.0066  ICIR=+0.3069
[test ] IC=+0.0654  RankIC=+0.0185  ICIR=+0.2142
```

**对照实验（验证 CRIT-002 是否为退化根因）：**

| 方案 | best_iter | test RankIC | test ICIR | train RMSE |
|---|---:|---:|---:|---:|
| A 原始（含 103 条污染标签） | 1 | +0.0111 | −0.0303 | 4.7967 |
| B 剔除污染标签 | 1 | **+0.0306** | **+0.1483** | 4.6266 |
| C 剔除 + 300 轮（关早停） | 1 | +0.0306 | +0.1483 | 4.6266 |

- 剔除 0.076% 的污染样本，test RankIC 从 **0.0111 → 0.0306**（近 3 倍），ICIR 由负转正 —— **确证数据损坏是模型退化的重要成因**。
- 但 `best_iteration` 在三种配置下**都恒为 1**，说明还有第二个独立问题（valid L2 从第 1 轮起就不改善）。C 组关掉早停、跑满 300 轮结果完全不变，说明 `best_iteration=1` 不是早停参数问题。

**指标是否可信？—— 不可作为研究结论。**

- 参考门槛：可研究使用的因子通常要求 **|RankIC| ≥ 0.03 且 ICIR ≥ 0.3**。当前最优情形 RankIC 0.031 / ICIR 0.148 —— RankIC 刚够线，ICIR 明显不足。
- `test_rank_ic = nan`、`test_icir` 依赖日度 IC 序列，样本仅 121 只/日，统计功效不足。
- 121 只股票的横截面太窄：任何 IC 估计的置信区间都会很宽。**在扩到 1000+ 只之前，不要对 IC 数值做过度解读。**
- 生产模型 test IC = **−0.365**，即当前线上是**反向**信号。

---

## 7. 回测质量

| 检查项 | 实现 | 判定 |
|---|:--:|---|
| Broker 撮合 | `broker.py`，先卖后买，拒绝原因可观测 | ✅ 设计好 |
| Engine 时间推进 | `engine.py`，按交易日推进，signal_lag 硬约束 | ✅ |
| Order / Trade / Portfolio | dataclass，拒绝单记为 `qty=0` + `reason` | ✅ 可观测性好 |
| T+1 | 有，但主路径不可达 | ⚠️ LOW-005 |
| 涨停 / 跌停 | 有，Decimal + ROUND_HALF_UP | ✅ 规则正确 |
| 停牌 | `halted or volume<=0` | ✅ |
| 交易手数 | `floor(qty/100)*100` | ✅ 实测生效 |
| 佣金 | `max(5, amt*0.0003)` 双边 | ✅ |
| 印花税 | 卖出 0.0005 | ✅ |
| 过户费 | 无 | ❌ |
| Signal Lag | `signal_lag>=1` 强制 | ✅ |
| Lookahead | 无未来函数 | ✅ |
| **等权仓位计算** | **用卖出前现金** | ❌ **CRIT-001** |
| 退市 | 无 | ❌ MED-008 |
| 分组回测 | `run_group_backtest` 有 | ⚠️ `group_turnover` 口径错（MED-007） |
| 滑点 / 冲击 / 换手衰减 | `BrokerConfig` 有（默认关闭） | ✅ 可启用 |
| 行业中性化 | `domain/neutralize.py` 存在 | NOT VERIFIED（未接入回测链路） |

**回测结果是否具有基本的研究可信度？—— 目前没有。**

两个层次的问题：
1. **跑不动**：universe 只有 1 天，真实回测无法进行（HIGH-001）。
2. **算不对**：即使跑起来，CRIT-001 让等权策略实际只运行约 6 成仓位，且持仓严重不均（2:1）。所有 NAV / 年化 / Sharpe / 最大回撤都是**另一个策略**的结果。

代码质量层面 engine+broker 是本项目最扎实的部分之一，修掉 CRIT-001 并补齐 universe 后，可信度可以快速回到可用水平。

---

## 8. 前端质量

规模：2,743 行 TS/TSX，4 个页面（MarketOverview / StockDetail / Screener / Backtest）。

| 维度 | 评价 | 依据 |
|---|---|---|
| 错误处理 | **好** | `api/client.ts` 拦截器把 `AxiosError` 统一转 `ApiError`，超时/HTTP/网络各返回中文文案；**未发现 traceback / Python 异常串进 UI** |
| 契约一致性 | 好 | 后端 HTTP 恒 200 + `code` 信封，前端据此解包；后端全局异常只回 `系统异常: {类名}`，不泄漏堆栈 |
| Loading / Empty / Error | ⚠️ | 有基础状态；但实测 Screener 的 `industry` 全为 `null`、`risk` 全为 `"high"`，前端无"字段缺失/降级"提示 |
| Degraded | ⚠️ | 后端已返回 `status: "degraded" / "unavailable"` 字段，但**后端把内部异常串塞进了 `reason`**（MED-001），前端若直接展示就会暴露内部错误 |
| 信息密度 | 中低 | 2,743 行支撑 4 个页面，属"能用但不厚"；作为研究工作台缺少因子明细、IC 曲线、分组回测可视化等 |
| 图表 | 中 | ECharts + Lightweight Charts；1,304 KB 单包未分包（LOW-007） |
| Responsive | NOT VERIFIED | Tailwind 已配置，未做实际断点测试 |
| 构建 | ✅ | `tsc -b` 通过、vite 668 模块转换成功、产物可生成 |

**前端是否真正适合作为个人量化研究工作台？—— 是一个不错的"看板"，还不是"工作台"。**

看行情、查个股、看选股结果够用；缺的是研究闭环需要的：因子探索、实验对比、IC 分析、回测归因。另外后端已具备的 `degraded` 语义没有在前端形成一致的降级 UI 语言。

---

## 9. 性能

硬件：Ultra 5 125H（14 核 16 线程）/ 32GB / 无独显。

**已实测：**
- `/api/v1/market/overview`：**9.57s**（首次，无缓存）—— 主要瓶颈在外部数据源（板块/实时快照），失败后又退化到本地 121 只计算
- `/api/v1/screener?top_k=5`：2.15s
- `/health`：0.56s
- 前端产物：1,304 KB / gzip 434 KB，单包

**规模推演（121 股实测外推，标注为估算 NOT VERIFIED）：**

| 规模 | Parquet 文件数 | 每日截面读取 | 训练样本量 | 主要瓶颈 |
|---|---:|---|---:|---|
| 121 股（现状） | 605 | ~605 文件 | 135k 行 × 45 列 | 外部 API 延迟 |
| 1,000 股 | ~5,000 | 5,000 文件 | ~1.1M 行 | **小文件 IO + `step_build_features` 全量重算** |
| 5,000 股 | ~25,000 | 25,000 文件 | ~5.6M 行 | **小文件 IO 致命 + 单进程 pandas groupby 因子计算** |

**CPU oversubscription 专项检查（用户重点要求）：**
配置层**做对了** —— `effective_cpu_threads()` 统一取 `min(逻辑核-2, 12)`，`POLARS_MAX_THREADS` 与 LightGBM `num_threads` 共用同一个值，注释明确写了"避免 18x18 争用" ✅。这是有意识的设计，值得肯定。

但仍存在争用风险：
- `step_build_features` 用**单线程 pandas** 逐 symbol `groupby` 循环（5000 股会非常慢），此时 Polars 线程未被利用 → 不是争用而是**利用不足**；
- `asyncio.to_thread` 跑同步回测，若叠加定时任务与 Polars 扫描，仍可能瞬时超订。

**瓶颈排序**：Disk IO（小文件）> 单线程因子计算 > 外部 API 串行 > LightGBM 训练（数据量下不是瓶颈）。

---

## 10. 可维护性与可扩展性

**可维护性：B+（好）**
- 4,271 行 app + 2,640 行测试，规模小、认知负担低
- `mypy app` **59 文件 0 issue** ✅
- 类型注解完整、docstring 质量高（多数函数带"为什么这么写"的说明，如涨跌停的 Decimal 陷阱、hfq asof 稳定性论证）—— 这是本项目最被低估的优点
- 命名一致、无 God Class（最大文件 365 行）、magic number 集中在模块常量

**一年后能否快速理解和修改？—— 能，前提是先解决两件事**：① 进 git；② 把 MODEL_ROOT 里 384+ 个实验目录清掉。否则打开项目首先面对的是"哪个模型是线上在用的"这个无法回答的问题。

**可扩展性：B+**

| 扩展场景 | 难度 | 说明 |
|---|:--:|---|
| 新因子 | **易** | `features.py::_one_symbol_feats` 里加一个 `out[...]` 即可 |
| 新模型 | **易** | train/infer 解耦；换 XGBoost 只需替换 `train_lgbm` |
| 新选股策略 | 中 | `_screen` 里 strategy 目前硬编码为 `alpha_basic_v1`，需要先抽出策略注册表 |
| 新回测规则 | **易** | 规则全部收敛在 `broker.py`，符合"撮合规则单点"设计 |
| 新 API | **易** | router + `APIResponse` 信封，模式统一 |
| 新图表 | **易** | 前端组件化 |
| **新数据源** | 中 | `multi_source.py` 已有三源冗余框架，但落盘 schema 需严格统一（否则重演 CRIT-002） |
| **改分区策略** | 难 | `symbol=/year=` 已渗透到读写路径，改动面较大（MED-003） |

---

## 11. 最值得修改的问题

### 高优先级（做完之前不要相信任何策略结论）

1. **修 CRIT-001 回测仓位计算** — `engine.py:114`，改为先撮合卖出、再按可用资金分配买入。这是单点修改，收益最大。
2. **修 CRIT-002 数据校验** — 加跨批次复权因子连续性校验 + 落盘 schema 统一 + label winsorize；重建 600519.SH 的 2023/2024 分区。
3. **修 CRIT-003 模型治理** — `load_prod_model` 改读 `is_production=1`；加 promote 门禁（|RankIC| ≥ 0.03）；prod/exp 目录分离。
4. **补 universe_daily**（HIGH-001）— 否则回测子系统等于不存在，A 股规则全部无法端到端验证。
5. **git init**（MED-012）+ 清掉 MODEL_ROOT 里 384+ 个实验目录。

### 中优先级

6. `conftest.py` 隔离测试 DB（HIGH-002），顺便修掉 flaky。
7. 流水线补 `train` 步骤 + 调度（HIGH-003）。
8. `/market/overview` 拆分 + 缓存（MED-002），异常文案脱敏（MED-001）。
9. 特征筛选改日度 IC 均值 + ICIR、提高阈值（MED-004）；早停目标与汇报指标对齐（MED-005）。
10. 扩大股票池到 1000+（121 只的 IC 估计统计功效不足，这是所有 ML 结论的前提）。

### 低优先级

11. `scripts/alerter.py` 补 `import sys`（LOW-001）
12. 新股无涨跌幅期改用交易日计数（LOW-004）
13. 补过户费、补 T+1 显式用例（LOW-005）
14. 前端 `manualChunks` 分包（LOW-007）
15. `group_turnover` 口径修正（MED-007）、退市处理（MED-008）、`model_version` 参数要么实现要么移除（MED-009）

---

## 12. 暂时可以不管的问题

- **过户费（0.001% 双边）**：相对佣金 + 印花税低一个数量级，年化影响 < 0.05%，先记 TODO。
- **行业中性化 / 市值中性化**：`domain/neutralize.py` 已存在但未接入；121 只股票谈中性化没有统计意义，等扩池后再说。
- **冲击成本模型的精度**：`BrokerConfig` 的线性冲击是合理的一阶近似，在个人研究尺度上足够。
- **北交所 / ETF 的完整支持**：当前股票池 121 只以沪深为主，规则骨架已留了 `bse` 分支，需要时再补。
- **announcements / financials 数据集**：目前只有一个占位分区，未接入主链路；若不做基本面因子，不必现在投入。
- **RBAC 全量鉴权**：若确认永远只在本机 localhost 使用，可以不管；但请务必保证 API 不暴露到局域网外（MED-010/011）。
- **前端响应式断点细节**：个人单机使用，桌面分辨率优先即可。
- **`announcements` 的 `symbol=__all__` 单分区设计**：数据量小时无影响。

---

## 13. 当前项目真正做得好的地方

这些是评审中**实测确认**的，不是客套：

1. **domain 层确实是纯函数。** `calendar` / `adjust` / `limit` / `a_share_rules` 零 IO，`CalendarData` 用 `frozen=True` 不可变快照，依赖方向单向（api → data → domain）。这不是"写了个 utils 文件夹"，是真的分层。

2. **ML 防泄漏纪律扎实。** train/gap/valid/gap/test 三段切分、`gap_days` 默认等于 horizon、特征筛选只用 train、`X_te` 刻意延迟构建（注释写明"从代码路径上杜绝 test 参与早停"）、特征白名单排除 `close`。`test_feature_asof.py` 和 `test_ml_leakage.py` 两个专门测试守卫。这是本次评审中质量最高的部分。

3. **涨跌停用 Decimal + ROUND_HALF_UP。** 注释明确点出"Python 内置 round() 是银行家舍入，对 xx.xx5 类价格会少 0.01 元"——这是真正踩过坑才写得出来的代码。

4. **signal_lag 硬约束。** `if signal_lag < 1: raise ValueError("当日信号严禁当日成交，防未来信号泄漏")` —— 把纪律写进代码而不是文档。

5. **统一错误契约 + 优雅降级。** HTTP 恒 200 + `code/trace_id` 信封；全局异常只回异常类名不泄漏堆栈；Redis 不可用时自动降级内存缓存（实测 `/health` 返回 `redis: "degraded"` 而服务正常）。前端 axios 拦截器把 `AxiosError` 统一转中文提示。**前后端都没有把 Python traceback 抛给用户。**

6. **`mypy` 全绿。** 59 个源文件 0 issue —— 在个人项目里不多见。

7. **CPU 线程统一配置。** `effective_cpu_threads()` 让 Polars 和 LightGBM 共用同一个值，注释里明确写了避免 18×18 争用。用户担心的 oversubscription 问题，**配置层是做对了的**。

8. **流水线幂等 + 不伪造数据。** `data_jobs` 表按 `(job_type, trade_date)` 幂等，SUCCESS 不可重复执行；非交易日直接 FAILED 而非编造行情。这个取舍很正确。

9. **因子基于 hfq 构建并论证了 asof 稳定性** —— 文档字符串里把"为什么不用 qfq"（qfq 锚点在窗口末，全量重算会漂移历史特征 → train/serve skew）讲清楚了。

10. **代码规模克制。** 4,271 行 app 代码撑起完整链路，没有过度设计，没有 God Class（最大文件 365 行）。

---

## 14. 最终评级

### PROJECT STATUS： **USABLE BUT NEEDS FIXES**

**原因：**

选这个而不是 MAJOR FIXES REQUIRED，是因为**架构不需要重写**——分层、纯函数、防泄漏纪律、类型完备性都经得起检查，三个 Critical 都是定点修复（一处仓位计算、一道数据校验、一个模型选择函数），不是设计缺陷。

选这个而不是 READY FOR LONG-TERM USE，是因为**研究闭环目前是断的**：

- 回测子系统因 `universe_daily` 只有 1 天而实际不可用（HIGH-001）；
- 线上服务的是 1 棵树、test IC −0.365 的模型（CRIT-003）；
- 即使修好前两者，CRIT-001 会让等权策略只跑 6 成仓位，绩效数字仍然不可信。

**明确的边界条件：在修复 CRIT-001 / CRIT-002 / CRIT-003 + HIGH-001 之前，不要相信本平台给出的任何选股或回测结论。** 但作为"行情浏览 + 个股查看 + 数据管理"的工具，它现在就能用。

### 六问简答

**1. 当前 AQP 到底属于什么水平？**
一个**架构合格、工程质量良好、但数据治理与实验治理尚未建立**的量化研究平台雏形。代码水平明显好于同规模个人项目（mypy 全绿、分层干净、防泄漏有纪律）；但"可信度基础设施"（数据校验、模型治理、实验追踪）基本是空白。目前处在"骨架搭好了，内脏还没接上"的阶段。

**2. 作为个人长期量化研究工具，是否已经基本成熟？**
**没有。** 但离成熟不远——核心差距不是代码能力，而是三道"保险的缺失"：数据写入前的连续性校验、模型上线的 promote 门禁、以及让回测真正能跑的 universe 数据。这三样补齐大约是可控的工作量。

**3. 当前最严重的 3～5 个问题？**
① CRIT-001 回测仓位计算错误（等权变 2:1、仓位 58.7%）
② CRIT-002 后复权数据断裂 + schema 漂移 + 无校验拦截
③ CRIT-003 生产模型无治理（服务 1 棵树、test IC −0.365 的模型）
④ HIGH-001 `universe_daily` 只有 1 个交易日 → 回测跑不动
⑤ HIGH-002/HIGH-003 测试污染生产库 + 流水线不含训练且无调度

**4. 最值得继续投入的 3～5 个方向？**
① **数据校验层**（投入产出比最高：一道校验同时防住 CRIT-002 与模型退化）
② **扩池到 1000+ 只股票**（121 只的 IC 估计统计功效不足，这是所有 ML 结论的前提，也顺带逼你解决 MED-003 的分区问题）
③ **修回测 + 补 universe**（量化平台的价值核心是回测可信度）
④ **模型治理闭环**（registry 读得回来、promote 有门禁、prod/exp 分离）
⑤ **实验追踪**（feature_runs 现在 685 行几乎全是空指标，等于没有）

**5. 哪些问题现在完全可以不管？**
过户费、行业/市值中性化、冲击成本精度、北交所/ETF 完整支持、announcements/financials 数据集、RBAC 全量鉴权（若确认纯本机）、前端响应式细节。详见第 12 节。

**6. 如果未来继续开发一年，当前架构最大的潜在瓶颈是什么？**
**Parquet 分区方案 `symbol=X/year=Y`。** 它决定了三件事：121 股 → 605 文件尚可，5000 股 → 25,000 个小文件，任何按日期取截面的操作（因子计算、回测、选股）都要全量打开；并且这个约定已渗透到 `parquet_store` 的所有读写路径，越晚改成本越高。

次要瓶颈是 **`step_build_features` 的单线程全量重算**——每天把所有标的全历史 `pd.concat` 后逐 symbol `groupby`，在 5000 股规模会成为日常流水线的性能墙。

好消息是 CPU 线程这块你已经做对了（Polars / LightGBM 统一 `effective_cpu_threads()`），oversubscription 不是问题，真正的瓶颈在 **IO 与单线程因子计算**。

---

*评审结束。所有结论均以真实执行为依据；推演部分已标注 NOT VERIFIED。*
