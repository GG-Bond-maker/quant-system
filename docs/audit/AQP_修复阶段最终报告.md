# AQP 正确性修复阶段 — 最终报告

> 目标：修复数据、模型、回测三条核心研究链路，并用真实数据重新验收。
> 所有数字均来自本机真实执行，未采信文档说法。未验证项标注 `NOT VERIFIED`。

---

## 0. 一句话结论

**基础设施已修好并可信任；但当前策略不赚钱。**

8 项 READY 门槛（数据无 Critical 污染 / schema 全一致 / 生产模型有治理 / universe 覆盖历史 / 回测仓位正确 / pytest 全绿 / 真实重训完成 / 真实回测完成）**全部满足**。

但必须明确：**"平台可信" ≠ "策略可用"**。修复后的真实回测显示该策略在样本外是亏损的（年化 −9.56%，计入摩擦成本后 −26.96%）。这是**研究发现**，不是基础设施缺陷 —— 修复前你根本得不到这个诚实的结论（回测跑不起来，指标也是假的）。

---

## 1. 关键前置约束：网络不可用

本次环境 akshare **3/3 重试全部失败**（`RemoteDisconnected`），**无法重新抓取数据**。

因此数据修复只能是「隔离 + 可证明的推导重建」，不能「重抓」。`scripts/repair_data.py --refetch` 已预留，网络恢复后可一键补齐。

**受此影响的遗留项**：600519.SH 被整只隔离，需网络恢复后重新获取。

---

## 2. 数据层根因（比原审计更深一层）

原审计只发现"数据坏了"，本次定位到**产生损坏的代码**：

`app/data/ingest/tasks.py::write_daily_bars` 有两个致命缺陷：

1. **`adjust` 只用于拼目录名**，同一份 DataFrame 被写进所有复权口径
   → raw 价格进了 `daily_bar_hfq`，复权因子塌缩为 1.0；
2. **用 `write_year_batch` 整年覆盖做每日增量**
   → 抹掉该年其它日期（000001/300750/600519 的 **2026 年 1~5 月就是这样永久丢失的**）。

默认 codes 恰好是 `["600519","000001","300750"]`，所以只有这 3 只中招 —— 与审计发现完全吻合。

**已重写为** `fetch_and_write_daily_bars()`：按口径分别抓取 + 写入前质量门禁 + 增量合并。
已验证：一份 df 写两个口径会被拒绝；注入三个不同价格的 fetcher 会正确落到三个数据集；脏数据（high<low）被门禁拒绝。

---

## 3. 修复文件清单

### 新增
| 文件 | 作用 |
|---|---|
| `app/data/quality.py` | 集中阈值 + 7 类数据质量检查（schema/null/dup/OHLC/收益连续性/复权因子/日历密度） |
| `app/data/repair.py` | 隔离（不删除）、锚点重建、schema 归一、qfq 推导、scan 验收 |
| `app/ml/labeling.py` | 标签质量策略（reject/winsorize + 跨空洞检测 + 审计日志） |
| `app/ml/registry.py` | 模型注册 + 生产治理（promote / 单例 / 质量门槛 / 样本规模自适应阈值） |
| `tests/conftest.py` | 测试路径隔离 |
| `tests/test_label_quality.py` | 标签质量 10 例 |
| `tests/test_equal_weight.py` | TC-EQUAL-WEIGHT 7 例 |
| `tests/test_model_registry.py` | 模型治理 12 例 |
| `scripts/qc_scan.py` | 数据质量扫描 |
| `scripts/repair_data.py` | 数据修复 CLI |
| `scripts/build_qfq.py` | qfq 生成 + 三套数据校验 |
| `scripts/build_universe.py` | 全历史 universe |
| `scripts/promote_model.py` | promote CLI |
| `scripts/purge_legacy_models.py` | 清理无效 candidate |
| `scripts/retrain.py` | 重训 + 门禁评估 + promote |
| `scripts/run_backtest_real.py` | 真实回测 |

### 修改
`app/data/ingest/tasks.py`（根因修复）、`app/data/pipeline.py`、`app/data/ingest/__main__.py`、
`app/data/parquet_store.py`（跳过空目录）、`app/data/universe.py`（全历史构建 + OHLC）、
`app/data/migrations.py`（注册表补列）、`app/db/models.py`、`app/db/init_db.py`、
`app/ml/train_lgbm.py`（接入标签治理 / exp 目录 / 血缘）、`app/ml/infer.py`（改读注册表）、
`app/backtest/engine.py`（等权再平衡）、`app/backtest/broker.py`、
`app/api/v1/market.py`（错误脱敏）、`app/api/v1/backtest.py`、`app/core/config.py`（密钥告警）、
`tests/test_api.py`、`tests/test_ml.py`、`tests/test_pipeline.py`

---

## 4. 数据修复统计

| 项 | 修复前 | 修复后 |
|---|---|---|
| QC error 数 | **12** | **0** |
| schema drift 文件 | 3 | **0** |
| `scan_parquet` 全量 collect | **SchemaError 失败** | **7 个 dataset 全部 OK** |
| 非交易日数据（合成数据） | 25 条 | **0** |
| 600519.SH | 2023/2024 为合成数据 | **整只隔离**（10 个分区 → `data/quarantine/`，含 manifest） |
| 000001.SZ / 300750.SZ 2026 | hfq 误写为 raw | **锚点因子重建**（82 行/只），复检通过 |
| daily_bar_qfq | **不存在** | **新建** 120 只 × 134,811 行 |
| 极端标签 | 103 条，最大 **+66,910%** | 82 条截尾 + 34 条跨空洞剔除 |
| 标签 std | **3.5227** | **0.0625** |
| 标签 \|r\| max | 669.11 | 0.868 → 截尾至 0.5 |

**三套数据校验（qfq/hfq/raw）**：120 只全部通过 —— qfq 末日收盘 == raw、qfq/hfq 常数比、日期集合一致。

---

## 5. 模型治理变化

| 维度 | 修复前 | 修复后 |
|---|---|---|
| 选模型方式 | 目录名排序取最后一个 | 只读 `is_production=1` |
| 无生产模型时 | 静默回退到实验模型 | **抛 `NoProductionModelError` 并指引 promote** |
| 目录布局 | 全部堆在 `models/` | `models/prod/` + `models/exp/` |
| 训练后是否自动上线 | 是（等于自动上线） | **否**，只登记 candidate |
| 生产模型数量 | 不保证 | **事务保证恒为 1**（promote 后断言校验） |
| registry `is_production` | 恒 0（只写不读） | 真实驱动推理 |
| 旧产物 | 452 个目录、72 条记录 | 归档至 `data/models_legacy/`，registry 清空 |

**质量门槛设计（第六阶段）** — 没有写死 `RankIC >= 0.03`：

绝对下限用 `auto_min_rank_ic(N, D)` 按样本规模推导：
```
SE = 1 / (sqrt(N-1) * sqrt(D))       # H0 下日度 RankIC 的标准误
threshold = max(经济下限 0.015, 2σ × SE)
```
当前 N=120、D=252 → SE≈0.0058 → 阈值 **0.0150**。
同一 RankIC 在 3000 只股票池下会更显著，固定阈值会在小样本误杀、大样本失守。

相对比较（有生产模型时）：valid_rank_ic / valid_icir / valid_rmse 三项逐项对比。
**test 指标不参与决策**，只入注册表供审计（避免用 test 调参）。

---

## 6. 生产模型

```
version          = 20260830_101756_repaired
feature_version  = alpha_basic_v1
dataset_version  = ds_120s134959r20220104-20260828
train_period     = 2022-01-04 ~ 2024-07-10   (72,913 行)
valid_period     = 2024-07-18 ~ 2025-07-31   (30,173 行)
test_period      = 2025-08-08 ~ 2026-08-21   (30,039 行)
best_iteration   = 2
valid  RankIC    = +0.0192    ICIR = +0.3238   RMSE = 0.0677
test   RankIC    = +0.0241    ICIR = +0.1870   RMSE = 0.0624
promoted_at      = 2026-08-30 10:17:58
path             = data/models/prod/lgbm_v1_20260830_101756_repaired/
```

**指标解读（不因修好了就夸大）**：
- RankIC 0.019 在 120 只横截面下约 3.3σ，**统计上显著**，但幅度偏弱；
- ICIR 0.32 刚过 0.3 的经验线，稳定性一般；
- **样本规模只有 120 只**，横截面太窄，IC 估计的置信区间很宽；
- 结论：这是一个"能用但很弱"的因子，远未达到可交易水准。

---

## 7. universe 数据规模

| 项 | 修复前 | 修复后 |
|---|---|---|
| 行数 | **7 行** | **134,778 行** |
| 交易日 | **1 天**（2024-06-05） | **1,128 天**（2022-01-04 ~ 2026-08-28） |
| 标的 | 2 | **120** |
| 与 daily_bar 交易日差集 | 缺失 1,127 天 | **0** |

字段齐全（17 列，含 open/high/low 供撮合定价）。`industry` 非空率仅 13.8% —— SQLite `instrument` 表本身缺行业数据，属数据源缺失而非代码问题。

---

## 8. 回测规模

- 区间 **2025-08-08 ~ 2026-08-21**（252 个交易日，取模型 test 段 = 真实样本外）
- 120 标的 / Top-K 10 / 日频 / 初始 100 万 / signal_lag=1
- 2,751 笔成交，348 笔拒绝（lot 347、limit_up 1）

| 指标 | 无摩擦 | 含摩擦（滑点+衰减+冲击） |
|---|---:|---:|
| 累计收益 | −9.85% | **−27.71%** |
| 年化收益 | −9.56% | −26.96% |
| 年化波动 | 25.20% | 25.22% |
| Sharpe | −0.379 | **−1.069** |
| 最大回撤 | 28.14% | 41.21% |
| 日胜率 | 48.21% | 45.02% |
| **日均换手** | **43.95%** | 43.88% |
| **平均仓位利用率** | **93.92%** | 93.91% |

**A 股规则命中统计**：`limit_up` 拒绝 1 次；`lot`（不足一手）347 次。
`T+1`、跌停、停牌在本次区间内**未被触发**（0 次）—— 规则已实现并有单测，但这段行情未覆盖到，属覆盖度不足而非功能缺失。

---

## 9. 修复前后对比（第十五阶段）

### 回测仓位（核心修复）
| | 修复前 | 修复后 |
|---|---|---|
| 平均仓位利用率 | **58.7%** | **93.92%** |
| 最低利用率 | 47.8% | — |
| 等权持仓（市值口径） | 38,800 : 19,400 股（**2:1**） | 开盘时偏差 **1.6%~17.1%** |
| 成因 | 买单预算用**卖出前**现金 | 先卖 → 更新现金 → 按 target_weight 分配 |

剩余 17% 偏差的成因（非分配逻辑错误）：A 股 100 股整手粒度（单仓约 9 万时，1 手在高价股上可占 10%+）+ 10% 不削仓容忍带。**按股数比较会显示 85%+，那是股价差异而非仓位失衡**，本次已改用市值口径衡量。

### 数据与模型
| | 修复前 | 修复后 |
|---|---|---|
| train RMSE | **4.7755** | **0.0602** |
| 生产模型 | 1 棵树，test IC **−0.365** | 2 轮，test RankIC **+0.0241** |
| 选股分值多样性 | 121 只仅 **7 个**不同分数 | 连续分布 |
| 回测可运行性 | **跑不了**（universe 1 天） | 252 天完整回测 |

### 测试
| | 修复前 | 修复后 |
|---|---|---|
| 结果 | 5 failed / 129 passed，且**顺序依赖** | **全绿**，单独跑与全量跑一致 |
| 生产库污染 | feature_runs 685 行、models 384+ 目录 | 全部走临时目录 |

---

## 10. pytest / mypy / ruff / build

| 项 | 结果 |
|---|---|
| `pytest -q` | **163 passed, 5 skipped**（最终一轮见下） |
| `mypy app` | **Success: no issues found in 63 source files** |
| `ruff check app` | 54 项（修复前 251 项，本次修复 197 项）；剩余为 datetime 时区 / B008 / blind-except 等既有风格问题 |
| `npm run build`（vite） | **OK**（tsc -b 通过，668 模块，1.30 MB / gzip 434 KB） |
| `docker compose config` | **OK** |
| `git` | 已初始化，189 文件 / 2 次提交 |

---

## 11. 仍存在的问题

### 未完成的工作
1. **Stage 10 仅部分完成**：`build_universe_history()` 已就绪，但**尚未挂进 pipeline 的 `STEPS`**，也没有建 Windows 任务计划 / cron 调度入口。
2. **MEDIUM 未做**：`model_version` 仍只进回测缓存键（未真正选择模型）；退市处理未实现；特征选择仍是池化 IC（未改日度 IC/ICIR）；LightGBM 早停仍看 L2 而非研究指标。

### 客观限制
3. **600519.SH 被隔离**，需网络恢复后 `scripts/repair_data.py --refetch` 补回。
4. **000001.SZ / 300750.SZ 的 2026 年 1~5 月数据已永久丢失**（被整年覆盖抹掉，无网络不可恢复）。
5. **`industry` 字段 86% 为空** —— instrument 表缺行业数据，影响行业中性化与前端展示。

### 结论性风险
6. **策略不赚钱**：样本外年化 −9.56%（含成本 −26.96%），日均换手 43.95% 过高。当前 `alpha_basic_v1` 因子**不可用于实盘**。
7. **样本仅 120 只**：横截面过窄，IC 估计不稳。扩到 1000+ 需要网络抓数。
8. **`best_iteration` 仍然很低（2）**：早停仍在极早轮次触发，说明特征对 5 日收益的解释力很弱。这是因子本身的能力问题，不是训练流程问题。

---

## 12. 最终判定

# READY FOR RESEARCH（有条件）

**判据**：用户列出的 8 项门槛全部满足。

| 门槛 | 状态 |
|---|---|
| 数据无 Critical 污染 | ✅ QC error = 0 |
| schema 全一致 | ✅ 7 个 dataset 全部 scan OK、列一致 |
| production model 有明确治理 | ✅ 注册表驱动、单例、显式 promote |
| universe 覆盖历史 | ✅ 1,128 天 × 120 标的 |
| 回测仓位正确 | ✅ 利用率 93.92%，等权偏差已量化 |
| pytest 全绿 | ✅ |
| 真实 ML 重新训练完成 | ✅ 含 promote 门禁 |
| 真实回测完成 | ✅ 252 天，含摩擦成本 |

**"有条件"的含义 —— 请务必同时接受这四条：**

1. **平台现在可信，但策略不可用。** 现在的亏钱结论是真实的；修复前的"看起来能跑"才是假的。
2. **600519.SH 已隔离**，股票池实际 120 只；网络恢复后需重新抓取补回。
3. **2026 年 1~5 月对 000001/300750 永久缺失**，这两只的最新数据不完整。
4. **调度入口未建**，每日流水线仍需手动执行；`build_universe` 尚未接入 `STEPS`。

**建议的下一步（按投入产出排序）**：
1. 补 Stage 10（universe 步骤 + 调度入口）—— 否则修复成果无法自动维持
2. 网络恢复后重抓全市场数据，扩到 1000+ 只
3. 处理 `model_version` 真实生效、退市、特征选择改日度 IC 三项 MEDIUM
4. 之后才谈新因子 / 新模型（当前因子能力是瓶颈，不是架构）
