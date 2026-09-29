# B4b · ML · 训练与模型 — 审核报告

> 审核员：B4b（ML·训练与模型）｜日期：2026-09-18 批次｜仓库：`D:\Python_Project\Alpha Quant Platform`
> 纪律来源：`docs/audit-2026-09-18/AUDIT-BRIEF.md`；本报告所有"确定"级结论均已用命令实跑验证，
> 输出片段原样贴出（未跑通的一律标注「疑似，需验证」）。
> 本批次**未修改任何源码**；所有探针脚本写在 `%TEMP%`（`C:\Users\HY\AppData\Local\Temp\dsh-Q7ynto\`），
> 唯一的仓库内改动是本报告文件本身。真实数据只读访问；`train_lgbm` 探针一律 `persist=False`（不落盘、不写注册表）。

## 0. 结论速览

| # | 严重度 | 类型 | 位置 | 一句话 |
|---|---|---|---|---|
| B4b-1 | **P1** | Bug/策略 | `app/ml/gp_miner.py:260,279-286,331,337,343` | 多空/5 分组净值用**同日**涨跌幅（前视），用户可见净值可虚增数百万倍 |
| B4b-2 | **P1** | 策略/一致性 | `app/ml/train_lgbm.py:224` + `scripts/train.py:44` + `scripts/grid_search.py:83` | `xsec_demean` 默认 False 且只有 `retrain.py` 打开 → 默认路径真实数据上早停在第 1 轮 |
| B4b-3 | **P1** | 策略/一致性 | `app/ml/train_lgbm.py:154-175,307` | 特征筛选用**池化** Spearman IC，与报表/目标的**逐日截面 RankIC** 口径不同 → 系统性丢掉最强截面因子（atr_14 偏差 270×） |
| B4b-4 | P2 | Bug/死配置 | `scripts/grid_search.py:35,37` + `app/ml/train_lgbm.py:53` | `GRID` 的 `min_child_samples` 轴被 `DEFAULT_PARAMS` 的 `min_data_in_leaf` 别名覆盖 → 81 组网格只有 27 组有效 |
| B4b-5 | P2 | 一致性/审计 | `app/ml/train_lgbm.py:368-391` + `app/ml/registry.py:213` | `xsec_demean` 未落盘（metrics/params/registry 三处都没有）→ promote 门禁跨口径比 RMSE |
| B4b-6 | P2 | 一致性/审计 | `scripts/grid_search.py:71,72,116` | `dataset_version="p1_2022plus"` 是函数默认值，被原样写进 `feature_runs`（648 行全是这个字面量）；top-1 模型登记 `dataset_version=""` |
| B4b-7 | P2 | 死代码/不可运行 | `scripts/p2_experiment.py:71-75` | 硬编码 `features/version=alpha_basic_v1`，而生产模型 features.json 含 20 个 `g1_*` 列 → 首次预测即抛「特征列缺失」 |
| B4b-8 | P2 | 无效守卫 | `app/ml/purged_cv.py:57-75,119` | `min_gap_ok` 用调用方传入的 `purge+embargo` 作阈值，**恒真**；0/0 配置下 gap=0 仍报 True |
| B4b-9 | P2 | 策略/死配置 | `app/ml/purged_cv.py:38-39` + `app/api/v1/research.py:280-282` | purge 宽度是硬编码常量，与 `ML_LABEL_HORIZON` 无关联；`/research/cv-folds` 允许 0/0 且无 horizon 入参 |
| B4b-10 | P2 | 死代码/未接线 | `app/ml/purged_cv.py:94,145` + `app/ml/alpha_expr.py:286,289` | Purged CV 报告函数、波动率倒数加权、`build_alpha158_lite` 全仓无生产调用方 → 模型选择实际无 walk-forward |
| B4b-11 | P3 | 未接线/误导 | `scripts/train_tft.py:48` + `app/ml/train_service.py:214` | TFT/GNN「不能 promote 上线」属**有意设计**（`registry.py:531-539` 明确声明"请保留 candidate"）；残留缺陷两条：`train_tft.py` 打印的 promote 指引必然失败、`gnn_v1` 链路无 test 段（4 条候选 `test_rank_ic` 全 NULL） |
| B4b-12 | P2 | 策略/性能 | `backend/scripts/train*.py:42/83` + `app/ml/train_service.py:244-256` | 全表进内存（真实 v2g 面板 2,481,630 行 × 88 列 ≈ 1.03 GB parquet），无内存守卫 |
| B4b-13 | P3 | 加固缺失 | `app/ml/alpha_expr.py:87,96-99` | 白名单**不可绕过**（实测 15 种逃逸全拒）；但 `Power` 不在 `errstate` 内、标量参数触发 `AttributeError`、超长表达式抛 `RecursionError`（非 ValueError） |
| B4b-14 | P3 | 死代码/一致性 | `app/core/errors.py:101` + `app/api/v1/studio.py:344` | `ERR_EXPR_INVALID=53001` 全仓从未被 raise（前端码表已预留）→ 不可达错误码 |
| B4b-15 | P3 | Bug/边界 | `app/ml/gp_miner.py:462-467` | `_TASKS` 超 20 个即淘汰最老任务，**不区分是否仍在运行** → 运行中任务失去查询与取消能力 |
| B4b-16 | P3 | 一致性/统计 | `app/ml/signal_analysis.py:119-126` | 重叠 h 日收益当日度序列算 t 统计量（未做 HAC），`ls_annualized=mean*252` 夸大年化 |
| B4b-17 | P3 | 健壮性 | `app/ml/train_service.py:172,283` + `app/ml/train_lgbm.py:389` | `metrics.json` 用 `open("w")` 直写、`torch.save` 直写，非 tmp+rename 原子写 |
| B4b-18 | P3 | 关键口径未披露 | `app/ml/torch_models.py:11-13,41-67` | `tft_v1` 实为轻量 Transformer 编码器（无变量选择/门控/分位输出），命名容易被读成真 TFT |
| B4b-19 | P3 | 已知条目延伸 | `app/ml/gp_miner.py:469-479` | 台账「缺 except BaseException」延伸：`finally` 确会执行并落 RUNNING；**且无任何 reaper 覆盖 gp_tasks** |
| B4b-20 | P2 | 策略/一致性 | `scripts/grid_search.py:60-64` + `app/ml/train_lgbm.py:340` | 超参选优**主键**是池化 Pearson IC（`valid_ic`），而门禁与业务口径是截面 RankIC → 选参在优化一个与目标正交的量 |

`_DEFAULT_FIELDS` / `_NUM_OPS`（`gp_miner.py:30,33`）判定见 §4.4：前者**无害残留**，后者是**漏用**
（`If` 条件算子从未接线进 GP 搜索空间），均为 P3。

---

## 1. 逐文件结论行

| 文件 | 结论 |
|---|---|
| `app/ml/train_lgbm.py` | 三段切分/gap/早停纪律**正确**（真实产物验证 gap1=gap2=5 交易日，test 不参与早停/筛选/落盘决策）；问题集中在 B4b-2/3/5、P3 内存与未使用参数 |
| `app/ml/train_service.py` | 并发守卫（`_ALLOWED_PARAMS` 白名单 + `_job.running` + `pipeline_slot`）与失败可见性成立，**未发现 P0–P2**；P3：非原子落盘、GNN 无 test 段 |
| `app/ml/purged_cv.py` | 边界实现**正确**（实际隔离带恰为 purge+embargo；`verify_no_overlap` 能识别无 purge 的越界切分）；但 `min_gap_ok` 自证恒真、purge 宽度与 horizon 无关联、两个函数未接线 → B4b-8/9/10 |
| `app/ml/gp_miner.py` | 适应度 IC **口径正确**（`close_w.shift(-horizon)` 前向收益）；多空/分层净值**前视**（B4b-1，P1）；无多重检验控制（P2 策略，见 §5.2） |
| `app/ml/graph.py` | 同日截面邻居聚合，无未来数据；`MAX_PEER_BUCKET` 脏桶守卫实测生效（日志：3 个行业桶被跳过）；**未发现 P0–P2** |
| `app/ml/alpha_expr.py` | AST 白名单**不可绕过**（实测）；P3 加固缺口见 B4b-13；`build_alpha158_lite` 未接线（B4b-10） |
| `app/ml/torch_models.py` | torch 缺失降级路径明确（`torch_ready()` 前置门禁 + 明确报错，不静默降级）；有 early stopping（只看 valid）；**未发现 P0–P2** |
| `app/ml/gnn_models.py` | 同上；NaN 显式 `nan_to_num` + 标签掩码（正确）；无 early stopping、无 test 段（见 B4b-11 备注） |
| `app/ml/torch_device.py` | 设备探测失败一律回退 cpu，**未发现 P0–P2** |
| `app/ml/sequence_dataset.py` | 滑窗与标签对齐正确（`wins[k]` 对应 `fwd[k+lookback-1]`），`purged_time_split` 的 gap 语义与 `train_lgbm` 一致；**未发现 P0–P2** |
| `app/ml/signal_analysis.py` | 前向收益口径正确；P3 统计口径问题见 B4b-16 |
| `scripts/train.py` | 退化默认（B4b-2）；且与 `retrain.py` 的"生产配方"不一致，无任何告警 |
| `scripts/retrain.py` | 唯一正确使用生产配方的入口（`xsec_demean=True`）；真实 dataset_version 指纹、门禁+显式 promote 流程成立 |
| `scripts/grid_search.py` | 排序键不含 test（纪律成立）；但死轴 + 假 dataset_version → B4b-4/6 |
| `scripts/promote_model.py` | 治理流程成立（排序只按 valid、`--auto` 只在 `lgbm_v1` 里挑）；CLI 硬编码 `model_name="lgbm_v1"` 与"TFT/GNN 保留 candidate"的设计一致 → 仅 B4b-11 的 P3 |
| `scripts/p2_experiment.py` | 一次性实验脚本，当前**不可运行** → B4b-7 |
| `scripts/train_gnn.py` / `train_tft.py` | 薄壳，与 API 共用 `train_service`，**未发现 P0–P2**；但打印的 promote 指引不可用（B4b-11） |

---

## 2. 过拟合与验证纪律（本批重点一）

### 2.1 三段切分是正确的（负结论，已用真实产物验证）

`train_lgbm.split_dates`（`:120-150`）语义：

```
test_start_idx  = len(dates) - test_days
valid_start_idx = test_start_idx - gap_days - holdout_days
train_end_idx   = valid_start_idx - gap_days - 1
```

真实生产模型的 `metrics.json`（`data/models/prod/lgbm_v1_20260905_181713_repaired/`）验证：

```
train_end   = 2024-07-17
gap1_start  = 2024-07-18      # gap1 恰 5 个交易日 = horizon
valid_start = 2024-07-25
valid_end   = 2025-08-07      # 252 个交易日
gap2_start  = 2025-08-08      # gap2 恰 5 个交易日
test_start  = 2025-08-15
n_symbols=1132 valid_days=252 test_days_n=252
```

label(t)=close[t+5]/close[t]−1 ⇒ train 末日标签用到 2024-07-24（落在 gap1 内），valid 末日用到
2025-08-14（落在 gap2 内）——**purge 宽度 = horizon，边界无 off-by-one，test 段不参与早停/筛选**
（`train_lgbm.py:312-313` 刻意不提前构建 `X_te`）。`tests/test_ml_leakage.py::test_tc_leak_early_stop_ignores_test`
是这条纪律的回归守卫，实跑通过。

**未见**「用 valid 调参再报 test」的间接泄漏：`test_days=252` 只出现在 `_eval(test_mask,"test")`（`:348`）
与落盘 metrics；promote 决策在 `registry.evaluate_candidate:393-508` 里只读 valid 字段（`require_test_rank_ic_positive`
默认 False）。`grid_search.pick_top1:55-65` 的排序键不含任何 test 字段（`tests/test_p2_ml.py:63-68` 用
poisoned 行做了守卫）。**这三条纪律是成立的，不要重复报。**

### 2.2 B4b-2（P1）默认配方在真实数据上是退化的 —— 且"哪个入口用哪个配方"没有留痕

`xsec_demean` 是 `train_lgbm` 的**关键字默认 False**（`:224`），而 `train_lgbm` 自己的 docstring
（`:233-238`）就写明不去均值时早停会退化。三个入口的实际取值：

| 入口 | `xsec_demean` | 结果 |
|---|---|---|
| `scripts/train.py:44-50`（docstring 里推荐的入口） | 未传 → **False** | 退化 |
| `scripts/grid_search.py:83,137,147`（唯一的超参搜索） | 未传 → **False** | 81 组都在退化目标上评估 |
| `app/ml/monitor.py:542`（自动重训） | 见 B4a | — |
| `scripts/retrain.py:100` | `not args.no_xsec_demean` → **True** | 生产配方 |

真实数据端到端探针（`%TEMP%\b4b_probe_train_lgbm_real.py`，真实 v2g 面板随机 400 只 × 2115 日 =
401,233 行，`persist=False`）：

```
--- xsec_demean=False ---
  best_iteration=1  n_kept=60
  train_ic=0.2197 valid_ic=0.0138 valid_rank_ic=0.0144 valid_icir=0.0714
  test_ic=0.0390  test_rank_ic=0.0075
  train_rmse=0.06506 valid_rmse=0.07681 test_rmse=0.07946
  atr_14 in kept? False  hl_range? False  vol_20? False  v_rank_20? False
  kept 前 10: ['g1_ma_gap_120','g1_ret_60','g1_ma_gap_60','ret_60','ma_gap_120',...]
--- xsec_demean=True ---
  best_iteration=15 n_kept=60
  train_ic=0.2125 valid_ic=0.0524 valid_rank_ic=0.0832 valid_icir=0.4325
  test_ic=0.0330  test_rank_ic=0.0385
  train_rmse=0.05558 valid_rmse=0.06146 test_rmse=0.07369
  atr_14 in kept? True   hl_range? True   vol_20? True   v_rank_20? True
```

`best_iteration=1`、`valid_rmse=0.0768 > 标签 std 0.069`（比"永远预测均值"更差）——默认路径产出的
**就是 docstring 里说的那个退化模型**。触发条件：按 `train_lgbm.py` 模块 docstring 或 `scripts/train.py`
的用法说明执行 `python scripts/train.py --horizon 5 ...`。最小验证：上表命令，或读生产库里的
`lgbm_v1_20260905_181337_repaired`（valid_rank_ic=0.0211 / valid_rmse=0.07411）与
`lgbm_v1_20260905_181713_repaired`（0.1020 / 0.05998）这对同 split 同数据集、只差该 flag 的对照。

> 说明：`xsec_demean` 的去均值是**逐日截面**运算（`:281-282`），同日所有行必在同一段内，
> **不构成跨段泄漏**。这条不是泄漏问题，而是"默认值与入口不一致 + 未留痕"。

### 2.3 B4b-3（P1）特征筛选口径 ≠ 评估口径，且真实数据上系统性丢好因子

`select_features_by_ic`（`:154-175`）对每个因子调用 `rank_ic(X[col], y_ret)` —— 把 train 段**所有
(symbol,date) 行混在一起排秩**（池化 Spearman）。而 train/valid/test 的 IC 报表用的是
`daily_rank_ic` = **逐日截面** RankIC 再平均（`:109-116`）。两者对"横截面型因子"的偏好完全不同。

真实 v2g 面板（2023–2024，随机 300 只 × 484 日 = 143,033 行）实测（`%TEMP%\b4b_probe_featsel.py`）：

```
factor            pooledIC   dailyIC
atr_14              0.0003    0.0816     ← 池化把最强的截面因子压成 0.0003（270× 偏差）
hl_range            0.0075    0.0782     ← 10×
vol_20              0.0109    0.0746     ← 7×
v_rank_20           0.0006    0.0264     ← 池化 0.0006 < 阈值 0.003，直接落选
g1_vol_20           0.0643    0.0282     ← 反向：被高估 2.3×
ret_5               0.0472    0.0278     ← 反向 1.7×
池化 RankIC top12 vs 截面 RankIC top12 重合度 = 8/12
```

仓库默认 `min_abs_rank_ic=0.003`：`atr_14(0.0003)`、`v_rank_20(0.0006)`、`hl_range(0.0075)` 本就
贴着/低于阈值。上表 §2.2 的实跑结果正是这个后果——`xsec_demean=False` 时这四个因子**全部不在**
`kept` 里；`xsec_demean=True` 时它们是 `kept` 的前四名。

反证（同一探针）：**标签去均值后**两套口径高度一致（top12 重合 11/12，`atr_14` ratio 1.07）。
也就是说这个 bug 与 §2.2 同根：**先对标签做截面去均值（或直接把 IC 口径换成逐日截面）二选一即可**。
只做其中一件（现状）就会让"用哪个入口训练"决定特征集，而入口差异没有任何日志或产物字段。

最小验证：

```powershell
$env:PYTHONPATH='D:\Python_Project\Alpha Quant Platform\backend'
& 'D:\Python_Project\Alpha Quant Platform\backend\.venv\Scripts\python.exe' "$env:TEMP\b4b_probe_featsel.py"
# 断言式：daily_ic(atr_14) / pooled_ic(atr_14) > 100 且 pooled_ic(atr_14) < 0.003
```

**同一口径病还有第二个出口（B4b-20，P2）**：`_eval` 里
`{name}_ic = pearson_ic(pred, y)`（`:340`）是**池化 Pearson**，`{name}_rank_ic` 才是逐日截面；
而 `grid_search.pick_top1` 的**第一排序键是 `valid_ic`（池化）**、第二键才是
`valid_rankic`（`:60-64`）。也就是说：**超参是按"池化 Pearson IC"挑的，模型却是按"截面
RankIC"被 promote 门禁评判的**（`registry.evaluate_candidate` 只读 `valid_rank_ic`/
`valid_icir`/`valid_rmse`）。在 `xsec_demean=False` 的网格里，池化 Pearson IC 几乎等于
"能不能预测全市场 60 天的共同涨跌"，与选股能力正交 —— 生产库 648 行的
`valid_ic` 均值 0.193 / `test_ic` 均值 −0.219（符号翻转）正是这个错配的典型表现。
修法：`pick_top1` 把 `valid_rankic` 提到第一键（或直接按折外 RankIC，见 §5.2）。

### 2.4 B4b-4（P2）81 组网格搜索的 `min_child_samples` 轴是死的

`DEFAULT_PARAMS` 用 `min_data_in_leaf: 200`（`train_lgbm.py:53`），`GRID` 用 `min_child_samples`
（`grid_search.py:35`），`run_grid_search` 把用户参数 merge 到默认值之上（`:79-81`）后两者**同时存在**。
LightGBM 中这两个是**别名**，实测**canonical 名 `min_data_in_leaf` 恒定胜出**、与字典顺序无关。

复刻 `train_lgbm` 的 `Dataset(params=...) + lgb.train(params,...)` 调用形状（`%TEMP%\b4b_probe_lgbm_alias.py`）：

```
默认(无 min_*)                                     -> trees=20 splits_total=600
min_data_in_leaf=200(平台默认)                      -> trees=20 splits_total=283
min_child_samples=20(网格最小)                      -> trees=20 splits_total=600
网格: min_data_in_leaf=200 + min_child_samples=20   -> trees=20 splits_total=283
网格: min_data_in_leaf=200 + min_child_samples=100  -> trees=20 splits_total=283   ← 与 20 完全相同
只用户参数 min_child_samples=100                     -> trees=20 splits_total=585   ← 单独用是有效的
```

生产库 `feature_runs` 的 648 行（81 组 × 8 次运行）同款证据（`%TEMP%\b4b_probe_feature_runs.py`）：

```
grid rows = 648     distinct grid indices 81    runs per index [8]
所有 grid 行的 distinct valid_ic 数量 = 24          ← 81 组只有 24 个不同结果
同一 (lr,num_leaves,feature_fraction) 下 min_child_samples 三档的 valid_ic:
  (0.08, 31, 0.6): mcs=20->0.23042  mcs=50->0.23042  mcs=100->0.23042
```

后果：(a) 每轮网格 2/3 的训练是纯浪费（本机 648 行合计 0.1 h，但全量数据下按 `train.py` 的规模
是小时级）；(b) `feature_runs.params_json` 记下了**没有生效**的超参值 → 实验台账失真；
(c) "81 组网格搜索"这一说法与实际搜索空间（27 组）不符。
`tests/test_p2_ml.py:44-52` 只断言 `GRID` 的**字面**笛卡尔积是 81 与 `min_child_samples == [20,50,100]`，
不检查该轴是否影响结果 → 测试无法发现。

修复方向：把 `GRID` 的键改成 `min_data_in_leaf`（或从 `DEFAULT_PARAMS` 里删掉同义键）；
顺带把 `assert len(combos) == 81` 改成断言"去重后的有效组合数"。

### 2.5 B4b-10（P2）平台**没有** ML 侧的 walk-forward / 嵌套 CV

- `train_lgbm` 只有单一三段切分；`grid_search` 的 81（有效 27）组**共用同一份 train/valid**，
  没有嵌套：选参用的 valid 段就是报指标的那一段。
- `purged_cv.cv_rank_ic_report`（`:94`）与 `volatility_inverse_weights_from_close`（`:145`）
  全仓仅被 `tests/test_quant_upgrade.py` 调用（`rg` 输出见下）；`PurgedGroupTimeSeriesSplit`
  在生产里只被 `/api/v1/research/cv-folds`（`research.py:293`）用来**画甘特图**。
- 真正的 walk-forward + optuna 只存在于**回测**参数搜索（`app/backtest/param_search.py:211,287`，B5 批次）。
  ML 训练侧没有复用这套已经写好的机器。

```
$ rg -n "cv_rank_ic_report|volatility_inverse_weights_from_close" backend
backend\app\ml\purged_cv.py:94,145        ← 定义
backend\tests\test_quant_upgrade.py:34,35,381,410  ← 唯一调用方
```

这意味着"超参搜索是否嵌套"的答案是**否**；对 A 股 5 日标签这种低信噪比目标，单段 valid 选参会
把噪声选成最优（§5.2 给了可落地的改法与验证方式）。

---

## 3. 口径一致性与留痕（本批重点二）

### 3.1 训练/推理特征口径 —— 当前是一致的（负结论）

- 生产特征空间 `FEATURE_VERSION="alpha_basic_v2g"`（`features.py:33`），训练与推理都从
  `DATA_ROOT/features/version=<FEATURE_VERSION>` 读同一批分区；
- `features.json` 的**列顺序被严格执行**：`infer.predict_with_contrib:48-51` 用
  `missing = [c for c in features if c not in X.columns]` + `matrix = X[features]`，
  列顺序取 `features.json` 的列表顺序，缺列直接 raise（不静默）；
- `registry.load_prod_model:541-545` 读 `model_path.parent/features.json`，与训练落盘同源；
- 无标准化/填充的跨样本 fit 状态（`train_lgbm` 不做 impute/scale，NaN 交给 LightGBM），
  因此不存在"用全样本统计量标准化"的泄漏；`tests/test_ml_leakage.py` 的
  `test_tc_leak_scaler_no_cross_sectional_fit` 守卫了这一点，实跑通过。

### 3.2 B4b-5（P2）`xsec_demean` 未落盘 → 门禁跨口径比较（B4a R14 的延伸）

`metrics` 字典（即 `metrics.json` 的内容，`:368-387`）字段全集实测：

```
['best_iteration','dataset_version','gap_days','holdout_days','horizon','kept_features',
 'label_quality','model_path','n_symbols','split','test_days','test_days_n','test_rows',
 'train_rows','valid_days','valid_rows','version']
```

**没有** `xsec_demean`，也没有任何 LightGBM 超参（超参只在 `params.json`）。
`registry.register_candidate` 写进 `params_json` 的只有 `{"kept_features", "split"}`
（`registry.py:213-214`）→ 注册表里同样查不到训练目标。于是：

| 模型 | 来源 | valid_rank_ic | valid_rmse |
|---|---|---|---|
| `lgbm_v1_20260905_181337_repaired` (id=12, candidate) | 同 split / 同 dataset_version | 0.0211 | **0.07411** |
| `lgbm_v1_20260905_181713_repaired` (id=13, **production**) | 同 split / 同 dataset_version | 0.1020 | **0.05998** |

两者 `label_quality` 逐字段相同（winsorize, n_final=1233631），只有 `xsec_demean` 不同。
`PromotePolicy.max_rmse_worsen_ratio=0.05`（`registry.py:79`）会拿 0.0741/0.0600=1.25 直接判
「valid_rmse 恶化超限」。探针实测比值 **1.250**：

```
=== 两套配方的 valid_rmse 比值（门禁 max_rmse_worsen_ratio=0.05 直接比较它） ===
  raw=0.07681 demeaned=0.06146 ratio=1.250  （>1.05 即被 promote 门禁判为 'rmse 恶化超限'）
```

即：**一个未记录的训练目标开关，被门禁当成"模型变差了"**；反过来若生产是 raw 口径，任何
demean 候选会因为 rmse 变小而无条件通过 RMSE 门。这是 B4a R14「门禁不校验可比性」在训练侧的
具体触发路径（R14 只看到注册表，看不到 `xsec_demean` 这个训练侧 flag）。

修法（最小）：把 `xsec_demean` / `label_mode` / `max_abs_label_return` 一起写进 `metrics`，并在
`evaluate_candidate` 里对"目标口径不一致"直接拒绝比较（而不是比数值）。验证方式：新增回归用例，
断言两套口径产物的 `metrics["target"]` 不同且门禁拒绝跨口径 RMSE 比较。

### 3.3 B4b-6（P2）`feature_runs.dataset_version` 是写死的字面量

`run_grid_search(dataset_version: str = "p1_2022plus")`（`grid_search.py:71`）把该默认值原样写库
（`:98`），而 `main()`（`:142-144`）**不传**真实指纹：

```
grid rows = 648
dataset_version {'p1_2022plus': 648}        ← 648 行全都是这个字面量
feature_version {'alpha_basic_v1': 648}     ← 而生产已是 alpha_basic_v2g
```

同一文件的 `main()` 里 top-1 的持久化重训（`:147-150`）也不传 `dataset_version` →
`train_lgbm` 写 `""`（`:386`）。对比 `retrain.py:35-57` 的 `compute_dataset_version()` 是真实指纹
（`ds_1133s1239411r20220104-20260904`）。**结果：超参搜索的实验台账无法与任何数据版本对账**，
且这 648 行是用 `alpha_basic_v1`（45 列）跑出来的，与线上 v2g（88 列）不可比。
修法：`main()` 里调用 `retrain.compute_dataset_version()`（或抽到公共模块）并显式传入。

### 3.4 B4b-7（P2）`p2_experiment.py` 当前不可运行

```python
feat_parts = sorted((s.DATA_ROOT / "features" / "version=alpha_basic_v1").glob("year=*.parquet"))  # :71
booster, feats_cols, model_dir = load_prod_model()                                                  # :74
pred, _ = predict_with_contrib(booster, feats, feats_cols)                                         # :75
```

实测（只读真实产物）：

```
prod features n = 60   g1_ cols in prod features = 20
v1 cols n= 45  has g1_: False
missing count (prod features not in v1 panel) = 20
missing sample ['g1_vol_10', 'g1_vol_60', 'g1_atr_14', 'g1_vol_20', 'g1_vol_5', 'g1_boll_w']
```

`predict_with_contrib:48-50` 会直接 `raise ValueError("特征列缺失: [g1_vol_10, ...]")`。
即：只要生产模型是 v2g 训出来的（现状），该脚本第一步预测就崩。
处置建议：要么改成读 `FEATURE_VERSION`，要么明确标注为历史一次性脚本并归档（它不在任何 CI/入口里）。

---

## 4. 逐文件深审

### 4.1 `app/ml/train_lgbm.py`

**成立的部分**：三段切分与 gap（§2.1）、`test` 不参与选择、`num_threads` 统一注入、标签质量策略
（`labeling.build_forward_return_labels` 的尾部按位置判定 + 跨空洞按自然日跨度判定，逻辑正确）。

**`num_threads` / `effective_cpu_threads` 确实生效**（任务重点之一，负结论）：

```python
merged_params["num_threads"] = effective_cpu_threads()   # :246，无条件覆盖用户传入
```
```
booster.params['num_threads'] = 3            ← 探针实测（b4b_probe_purged_cv.py 末段）
生产 params.json: "num_threads": 12          ← data/models/prod/.../params.json
```
`effective_cpu_threads()` = `AQP_CPU_THREADS` > `min(cpu-2, 12)`（`config.py:252-266`），
与 Polars / torch 共用同一配置（`torch_models.py:118`、`gnn_models.py:63`）→ 一致，无问题。

**类别/缺失处理无泄漏**：不做分类特征编码、不做 impute、无 `scale_pos_weight`（本就是
`objective=regression` 回归而非分类）。`sample_weight` 全仓未被 `train_lgbm` 使用，而
`purged_cv.volatility_inverse_weights_from_close` 存在的意义正是它 → 未接线（B4b-10）。

**内存（B4b-12）**：真实 v2g 分区合计 ~1.03 GB parquet / 2,481,630 行 × 88 列（实测）。调用链：

```
scripts/train.py:42   pd.concat([pd.read_parquet(p) for p in parts])   # 9 个年分区一次性 concat
train_lgbm:260        df = features.copy()
train_lgbm:271        build_forward_return_labels(df) 内部：.copy() → .loc[~tail].copy() → .loc[~nonfinite].copy()
train_lgbm:289        X = df[feat_cols_all]                            # 再一份
train_lgbm:311        X_tr / X_va = X.loc[mask, kept]                  # 各一份
train_lgbm:316-317    lgb.Dataset(...)                                 # LightGBM 内部再转 float64 + 分箱
```

float32 面板约 0.85 GB/份，峰值路径上有 4–5 份活着的整表副本 → **峰值 4–6 GB**，且没有任何
内存守卫（对比 B5 已报的 `param_search.py` 笛卡尔积 OOM 属同类风险，此处不重复，仅补
训练侧的量化估计）。`train_service.run_gnn_training:245-256` 更重：`wide.to_numpy(float32)`
单份约 1.8 GB（2500×2115×86），`X[tr]` 是 fancy-index **复制**再 +1.5 GB。

**未使用参数**：`_register_model(version, metrics, model_dir, kept, horizons)`（`:179`）中
`kept` 与 `horizons` 在函数体内**从未被引用** —— 这正是 B4a R9「`params_json.kept_features` 恒为
空」的直接成因（`metrics` 里没有 `kept_features` 键，而 `kept` 又没被塞进去）。**B4a 已报 R9，
此处不重复计数**，只补位置与成因。

**其它**：`df.sort_values(["symbol","date"])`（`:261`）与 `build_forward_return_labels` 内部
再次排序（`labeling.py:136`）重复一次，无害。

### 4.2 `app/ml/train_service.py`

**并发守卫**（任务重点）：三层，成立。

1. `_ALLOWED_PARAMS` 白名单（`:362-365`）过滤任意 kwarg（`tests/test_train_service.py:117-131`
   用 `evil_param` 覆盖）；
2. `current_pipeline_owner()` 前置检查（`:417-423`）+ 线程内 `with pipeline_slot("training")`（`:383`）；
3. `_job.running` 进程内串行（`:440-443`）。`pipeline_slot` 是**进程级** threading.Lock
   （`pipeline_lock.py:11-12` 已如实声明前提），CLI 与 API 并发不受它保护 —— 这属于已声明的
   单 worker 前提（简报第 1 节【二】），不报。

**状态机与失败可见性**：`snapshot()` 返回 `running/error/last_result/logs/cancelled`，
`_worker` 的 `except Exception`（`:398-404`）会写 `_job.error` 并落 kv → **失败不静默**（负结论）。
`run_tft/gnn_training` 的样本门禁返回 `{"ok": False, "reason": ...}` 也会被前端读到。
`cancel_training` 只 set event，`_worker` 的 `finally` 保证 `running=False` 闭环。

**未见「旧模型被半成品覆盖」**（B4b-17 的诚实结论）：模型目录名带秒级时间戳
（`train_lgbm.py:247`、`train_service.py:166,278`），promote 才复制到 `prod/`，且
`register_candidate` 在产物落盘之后。因此非原子写（`metrics.json` 用 `open("w")`、
`torch.save` 直写）**不会覆盖任何旧模型**，最坏是留下孤儿目录或截断的 `metrics.json`；
`train_lgbm._register_model` 的失败是显式"仅告警不阻断"设计（`:202-203`）→ **P3，非 P0/P1**。

**GNN 无 test 段**（任务重点"是否用 test 段做选择"）：`run_gnn_training` 的签名里**没有**
`test_days`（`:214`），`tr = arange(train_end)`、`va = arange(valid_start, n_days)` → 最后 252 天
全算 valid，**没有 test 段**。真实库佐证：`gnn_v1` 的 4 行 `test_rank_ic` 全为 NULL。
同时 `gnn_models.train_gnn` 无 early stopping（固定 epochs），用户只能反复看 `valid_rank_ic`
调 epochs → 唯一的报表数字就是被优化对象。**注意**：TFT/GNN 不能 promote 是**有意设计**
（`registry.py:531-539` 逐字声明），因此这条只算 P3（研究链路缺 OOS 校验），不是"死代码 bug"；
配套的 P3 还有 `scripts/train_tft.py:48` 打印 `promote: python scripts/promote_model.py（门禁把关）`
—— 该命令对 `tft_v1` 必然报 `ModelRegistryError`（CLI 硬编码 `lgbm_v1`，见 `promote_model.py:103`）。

**未发现 P0–P2**（除上述隐含于 B4b-11/12/17 的项）。

### 4.3 `app/ml/purged_cv.py`

**边界实现正确**（负结论，实测 `%TEMP%\b4b_probe_purged_cv.py`）：

```
purge=5 emb=2 fold=0: gap_days=  7 verify(>=purge+emb)=True verify(>=horizon5)=True
purge=0 emb=0 fold=0: gap_days=  0 verify(>=purge+emb)=True verify(>=horizon5)=False
no-purge fold=0:      gap_days=  0 verify(>=7)=False        ← 构造的越界切分器能被识别
```

即 `embargo = embargo_window + purge_window`（`:62`）合成隔离带、`train_end = test_start - embargo`
（`:68`）的实现没有 off-by-one，`verify_no_overlap` **本身**也不是恒真函数（对人为越界的切分器
返回 False）。

**B4b-8（P2）但 `cv_rank_ic_report` 里的调用是自证的**：

```python
if not verify_no_overlap(tr, te, groups, horizon=purge_window + embargo_window):   # :119
```
阈值传的就是切分器内部使用的同一个 `purge+embargo`，而实际 gap 恒等于它 → **`min_gap_ok` 永远为 True**。
实测 `purge=0/embargo=0` 时真实 gap=0（标签重叠达 horizon−1 天），报告照样 `min_gap_ok=True`。
这个字段被文档描述为"全部折是否满足隔离带"，会被读成泄漏守卫 → 属于**永不成立的分支**。
正确做法：阈值应是 `horizon`（标签窗口）而不是 `purge+embargo`，并断言 `purge+embargo >= horizon`。

**B4b-9（P2）purge 宽度与 horizon 无关联**：`purge_window` 默认 5、`embargo_window` 默认 2
（`:38-39`）是裸常量；`ML_LABEL_HORIZON` 改了（比如 10）不会有任何告警。API 侧
`research.py:280-282` 的 `CvFoldRequest` 允许 `purge_window=0, embargo_window=0`（`ge=0`）且
**没有 horizon 入参**，`/research/cv-folds` 会照画一张"Purged CV 甘特图"。对比 `train_lgbm` 的
纪律是 `gap = horizon`（正确）→ 同一平台两套口径。

### 4.4 `app/ml/gp_miner.py`

#### B4b-1（P1）多空/分层净值用了同日收益（前视）

```python
fwd = close_w.shift(-horizon) / close_w - 1.0          # :244  ← IC 口径正确（前向收益）
daily = close_w.pct_change()                           # :260  ← 同日收益 close[t]/close[t-1]-1
dr = daily.loc[d]                                      # :279
long_rets.append(dr.reindex(srt.index[-k:]).mean())     # :280  ← 用 t 日因子选股，赚 t 日的收益
```

`factor_report` 里同样：`daily = close_w.pct_change()`（`:331`）、`dr = daily.loc[d]`（`:337`）、
`q_cur[q] *= (1.0 + r)`（`:345`）→ `quintile_navs` / `quintile_annual`。而同一函数的 `decay`
用的是**正确**的前向收益（`:351` `close_w.shift(-h)/close_w - 1.0`）→ **同一份报告里两套口径**，
这本身就证明 `daily` 那套是笔误而非设计。

真实探针（`%TEMP%\b4b_probe_gp_nav.py`，60 只 × 300 日随机游走，`with_groups=True`）：

```
expr='close / Ref(close, 1)'                mean_ic=+0.0072  long_short_nav_total=+759906574.51%
expr='Ref(close, 1) / Ref(close, 2)'        mean_ic=+0.0026  long_short_nav_total=-13.71%
expr='Ref(close, 1) / Ref(close, 6) - 1'    mean_ic=-0.0034  long_short_nav_total=+20.69%
corr(f_self(t), daily[t])   = 1.0
corr(f_self(t), fwd5[t])    = 0.0051
```

因子取"当日涨跌幅"时，它对**前向 5 日收益**的 IC ≈ 0（0.0072，正确），但报告的多空净值
累计 **+759,906,574%**（7.6 百万倍）——纯粹因为信号与"同日已实现收益"完全同步（corr=1.0）。
把因子滞后一天（`Ref(close,1)/Ref(close,2)`）后净值回到 −13.7%（噪声水平）→ 对照有效。

**触发条件与影响面**（用户可见）：
- `POST /api/v1/studio/alpha-eval` → `with_groups=True`（`studio.py:265-267`）直接返回
  `long_nav`/`short_nav`/`long_short_nav`；
- `POST /api/v1/studio/factor-report`（`studio.py:425`）返回 `quintile_navs`/`quintile_annual`；
- `POST /api/v1/studio/factors/{nl-to-factor 的 evaluation}` 会清掉 nav（`studio.py:233-234`），
  但 `/factor-report` 没有 → 前端"因子检测报告"页会画出完全失真的分层净值曲线与年化。
- `factor_report` 里 `top_turnover`（`:359-366`）只涉及名单，不受影响；`decay` 不受影响。

**最小验证**：注入 `mom_20 = close/Ref(close,20)-1`（一个自相关的动量式因子）与
`"close / Ref(close, 1)"`（= 当日收益）两个表达式，前者净值应在合理量级、后者应爆表；
或直接断言 `evaluate_expr_detail("close / Ref(close,1)", ...)["long_short_nav"][-1]["nav"]`
不超过某个数量级（正确实现下应接近 1）。
**修法**：`daily` 改成 `close_w.shift(-1)/close_w - 1`（信号 t 日收盘可得，收益从 t+1 起算），
或干脆复用 `:244` 的前向收益做逐日再平衡近似。

#### 适应度是否用未来数据 —— 否（负结论）

`evaluate_expr_detail` 的特征来自 `eval_expr`（全部滚动向后算子 + `Ref(n>=0)` 红线），标签来自
`close_w.shift(-horizon)`，两者分离 → GP 适应度**没有**把未来数据喂给特征。

#### 多重检验控制 —— 没有（P2 策略，见 §5.2）

`_run_task`（`:405-454`）的适应度 `score = abs(mean_ic) - 0.004*complexity` 是**全样本** IC；
`task.best` 直接把它当"挖到的因子"回给前端与 SSE 通知（`:146-150`）。没有任何 holdout、
没有 Deflated Sharpe / Bonferroni 类多重检验校正，也没有记录"搜索了多少个表达式"
（`scored` 只在内存里）。pop=30 × gens=8 ⇒ 一次任务评估 240 个表达式，多次任务累积后
**必然**能挑出历史 |IC| 高但纯噪声的表达式。

#### `_DEFAULT_FIELDS` / `_NUM_OPS` 判定（父审核员指定）

- `_DEFAULT_FIELDS`（`:30`）：**无害残留**。字段实际由 API 传入并校验
  （`studio.py:74-79`），GP 的 `_gen_expr(rng, fields, ...)` 用的是 `task.params["fields"]`
  （`:390,415`）。删掉即可，无行为影响。
- `_NUM_OPS`（`:33`）：**漏用（真 bug 的"静默功能缺口"级）**。`_NUM_OPS = {v[1]==3}` 实际只
  含 `{Corr, Cov, If}`（注释写的 "If/Greater/Less 等" 有误，`Greater`/`Less` 是 2 参、已被算进
  `_WINDOW_OPS`），而 `_gen_expr` 只对 `Corr/Cov` 做了特判（`:47-50`）——**`If` 条件算子从未被
  生成过**。因此 GP 的搜索空间静默缺了条件/分段结构（qlib Alpha158 里 `If` 正是 SUMP/SUMN 等
  因子的核心）。判定：不是"算了但没用"的残留，而是"本来要接线、最终没接"。
  证据：`rg "_NUM_OPS" backend` 只有定义处；`_gen_expr` 的分支只覆盖 `Ref`/`Corr`/`Cov`/其余 2 参。

#### B4b-15（P3）`_TASKS` 淘汰不保护运行中任务

```python
if len(_TASKS) > 20:
    for old in sorted(_TASKS, key=lambda k: _TASKS[k].started_at)[:-20]:
        _TASKS.pop(old, None)              # :465-467
```
`sorted(...)` 升序 → 丢掉的正是**最老的**；GP 任务是长任务，"最老的"通常**仍在运行**。
实测（`%TEMP%\b4b_probe_gp_tasks.py`，monkeypatch `_run_task` 为不结束的桩）：

```
started = 21  仍在 _TASKS 中 = 20
被淘汰的 task_id = ['083e6c7d0f0f']
→ 被淘汰任务仍在线程中执行（_noop 调用次数 = 21），但 get_task() 返回 None
→ cancel_task() 对已淘汰任务返回 False
```

后果：第 21 次启动挖掘后，最老的那个**运行中**任务既查不到状态也无法取消（线程继续吃 CPU，
终态仍会写 kv，所以不丢数据，但失去可观测/可取消性）。修法：淘汰时跳过 `status in ("PENDING","RUNNING")`。

#### B4b-19（P3）已知条目的延伸：状态落盘 + **无守卫**

台账条目说「`gp_miner.py` 约 470 行缺 except BaseException，panic 穿出时会把 RUNNING 状态落盘」。
延伸结论（不重复原条目）：

1. **`finally` 一定执行**（Python 语义），所以 `_persist_task(task)`（`:478`）确会把
   `status="RUNNING"` 写进 `app_state.gp_tasks` → 台账描述的落盘行为成立；
2. **进程内存里的 `_TASKS[task_id]` 也永久停在 RUNNING**，于是 `get_task` 命中内存分支
   （`:160-163`）而**不会**去读持久层 → 前端在本次进程生命周期内一直看到 RUNNING；
3. **没有任何 reaper 覆盖 gp 任务**：启动期只回收 `monitor.reclaim_stale_retrain`
   （`main.py:65-66`）与 `task_store.reap_stale_running_tasks`（`main.py:75-76`），
   `rg "_PERSIST_KEY|gp_tasks"` 显示 gp 侧只有写、没有对账 → **永久卡死，无自愈**；
4. 触发概率要如实说明：CPython 里信号处理只发生在主线程，后台线程实际能撞到的 BaseException
   基本只有第三方库抛的 `SystemExit`/`GeneratorExit`，**概率低**；但一旦发生，代价是永久状态污染。
   修法：`except BaseException:` 分支里把 `task.status` 置 `FAILED`（或 `CANCELLED`）后再
   `raise`/记录，并在启动期加一条把 `gp_tasks` 里 RUNNING 翻成 FAILED 的对账（与既有 reaper 同款）。

### 4.5 `app/ml/graph.py`

- `propagate`（`:108-164`）是**同日**截面聚合（`An @ flat`，`:156`），只用当日的邻居因子值，
  没有跨日/未来引用 → PIT 安全（列名契约与 `tests/test_data_prep.py:207-302` 的
  `build_adjacency`/`propagate` 用例一致）。
- `MAX_PEER_BUCKET=150` 守卫实测生效：`train_readiness()` 实跑日志
  `[graph] 3 个行业桶超过 150 只，已跳过同业边（疑似分类脏桶）`。
- 内存：`A` 是 N×N float64（2500 只 ≈ 50 MB），可接受；`build_adjacency` 的 Python 双层循环
  只遍历边数（71944 条 industry 边 + 显式边），非 O(N²)。
- **未发现 P0–P2 问题。**

### 4.6 `app/ml/alpha_expr.py`（白名单绕过探针，任务硬要求）

探针：`%TEMP%\b4b_probe_alpha_expr.py`（800 行合成日线，覆盖逃逸/大指数/窗口极值/递归/宽度）。

**结论：白名单不能被绕过。** 15 种逃逸尝试全部在 `parse_expr` 阶段被拒：

```
'().__class__'                        -> PARSE-REJECT 节点 Attribute
'close.__class__.__mro__'             -> PARSE-REJECT 节点 Attribute
"__import__('os').system('echo pwn')" -> PARSE-REJECT 节点 Attribute
'close[0]' / '[1,2][0]'               -> PARSE-REJECT 节点 Subscript
'(lambda: 1)()'                       -> PARSE-REJECT 节点 Lambda
"{'a':1}" / '[x for x in (1,2)]'      -> PARSE-REJECT Dict/ListComp
'close if 1 else 0'                   -> PARSE-REJECT IfExp
"'abc'" / 'True' / 'None'             -> PARSE-REJECT 仅允许数值常量
"getattr(close,'mean')()"             -> PARSE-REJECT 未知算子
```

第二遍校验（`:137-144`）把函数名与字段名分开处理（`func_nodes` 用 `id()` 比对），
`ast.Call` 还额外拒绝了 `keywords`（`:133`）；`ast.Name` 必须落在 `FIELDS ∪ extra_fields`。
`_PARSE_CACHE` 有上限与 FIFO 淘汰（`:145-147`），不会因 GP 产生无界缓存。

**资源耗尽：经 API 不可达；库调用下有三个加固缺口（P3/B4b-13）**

```
Power(close, 1e9)                -> ACCEPTED（np.power → inf → 后续替换为 NaN）  0.00s
close ** 999999999999999999999   -> ACCEPTED（float 转换后溢出为 inf）            0.00s
'1e300 ** 1e300'                 -> EVAL-RAISE OverflowError（向上穿出 eval_expr）
'1 / 0'                          -> EVAL-RAISE ZeroDivisionError
'Log(0)'                         -> EVAL-RAISE AttributeError: 'float' object has no attribute 'clip'
Mean(close, 1e18) / Rank(close, 1e8) / Std(close, 1e18)  -> ACCEPTED 且 0.00s（窗口>序列长 → 全 NaN，不做无用功）
Mad(close, 400)                  -> ACCEPTED 0.01s
depth=200 嵌套 Mean(...)          -> OK 0.10s / peak 0.5MB
width=200  'close + close + ...' -> OK 0.02s
width=1500 'close + close + ...' -> PARSE-OK
width=2000 'close + close + ...' -> RecursionError: maximum recursion depth exceeded during ast construction
paren_depth=2000/20000/100000    -> ValueError（语法错误，正常拒绝）
```

要点：
1. **不构成 DoS**：`**` 大指数不会挂住（0.00s，溢出→inf→NaN）；超大滚动窗口因
   `min_periods=window > len` 不产生计算；`Mad/IdxMax` 的 `.apply` 成本被 GP 的
   `population<=80 / generations<=20 / depth<=3` 与 API 的 500 字符上限双重限住。
2. `RecursionError` 是**栈深相关**的（width=1500 通过、2000 失败）→ 阈值不固定，标**疑似**；
   且 `parse_expr` 只 catch `SyntaxError`（`:120-121`），`RecursionError` 会穿出。所幸所有
   用户入口的表达式都有 `max_length=500`（`studio.py:137,145,282,407`）→ 约 62 个顶层项，
   **不可达**。但 `nl-to-factor` 的表达式来自 LLM 输出（`studio.py:211` 后直接
   `parse_expr(expr, ...)`），只有 `req.text` 受 500 字符限制 → **理论上** LLM 若返回超长表达式
   会走 `except ValueError`（`:220`）捕不到的分支 → 落到 panic 兜底（`panic_guard.py:114`
   返回 `ERR_PANIC_CONTAINED=50001`），而不是合法的 53001/40000。
3. `Log/Sqrt/Abs` 等把标量常量当 Series 用 → `Abs(5)` 因 numpy ufunc 恰好可用，但
   `Log(0)`/`Sqrt(4)` 抛 `AttributeError`（`_op_log` 的 `x.clip` 假设入参是 Series）。
   `eval_expr` 文档说"除零/溢出统一转 NaN"，但 `1/0`（两个常量）抛 `ZeroDivisionError`、
   `1e300**1e300` 抛 `OverflowError` —— 与文档不符。影响面小（`gp_miner`/`build_alpha158_lite`
   都在 per-symbol 的 `except Exception` 里吞掉，退化成一个无效因子），但**异常类型不该由
   "常量还是 Series" 决定**。
4. `Power` 的 lambda（`:87`）不在 `np.errstate` 上下文里（`errstate` 只包住 `ast.BinOp` 分支
   `:186-198`），实测 stderr 出现
   `RuntimeWarning: overflow encountered in power` / `divide by zero encountered in power`
   —— 在 GP 的成百上千次求值下会刷屏。

### 4.7 `app/ml/torch_models.py` / `gnn_models.py` / `torch_device.py`

- **torch 缺失降级路径明确**：`torch_ready()` → `train_readiness()["torch_ready"]=False` →
  `start_training` 抛 `ERR_TRAIN` 并给出安装指引（`train_service.py:429-430`），
  `run_tft_training`/`run_gnn_training`/`train_sequence_model`/`train_gnn` 各自也有
  `_require_torch()`/显式报错 → **绝不静默降级**（符合设计，不报）。
  本机实测 torch 已装且可训：`torch 2.14.0+xpu`，`device=xpu`（Intel Arc），
  `tft_ready=true / gnn_ready=true / gnn_edges=71944 / est_samples=5202500`。
- **TFT 是否真有实现（任务重点）**：有**可训练**的实现，但**不是 TFT**。
  `build_model`（`:41-67`）= Linear 嵌入 + 位置参数 + `nn.TransformerEncoder`（2 层）
  + mean pooling + Linear 头，纯标量回归。真 TFT 的变量选择网络、静态协变量、门控残差、
  分位输出（`QuantileLoss`）都没有，也没有 `pytorch-forecasting`。模块 docstring
  （`:11-13`）已如实声明"不是完整 TFT"。**问题只在命名**：模型名为 `tft_v1`
  （`train_service.py:166`）、前端/文档口径也叫 TFT → 用户会以为在用最前沿 TFT（P3/B4b-18）。
- **与 LightGBM 主线是否重复**：不重复（不同模型族、不同数据形态）。它们是**研究态链路**，
  且"不能 promote 上线"这一点在 `registry.py:531-539` 有逐字声明，属**有意设计**（不报）；
  残留 P3 见 §4.8 / B4b-11。`sequence_dataset`/`graph` 是这两条链路的真实数据前置（v2g 的 20 个
  `g1_*` 列就是 `graph.propagate` 的产物，已进生产特征空间）→ 这两块**不是**死代码。
- `train_sequence_model` 的早停只看 valid（`:154-162`），取 best state（`:164-165`），
  训练完统一 `.to("cpu")`（`:168`）→ 与存盘/推理路径一致，无跨设备张量残留。
- `gnn_models.train_gnn` 无 early stopping / 无 valid 监控（见 §4.2 末）。

### 4.8 死代码 / 未接线专项（目标 B）

`rg` 核对结果：

| 位置 | 分类 | 依据 | 建议动作 |
|---|---|---|---|
| `scripts/p2_experiment.py`（全文） | **真死/不可运行** | 硬编码 v1 特征 + 生产模型 features.json 需 20 个 `g1_*` → 首步 raise（B4b-7） | 改版本号或归档 |
| `purged_cv.cv_rank_ic_report`(:94) | **未接线** | 仅 `tests/test_quant_upgrade.py:381` | 接进 grid_search 的 walk-forward，或删除 |
| `purged_cv.volatility_inverse_weights_from_close`(:145) | **未接线** | 仅 `tests/test_quant_upgrade.py:410`；`train_lgbm` 从不使用 `sample_weight` | 接线（见 §5.1）或删除 |
| `alpha_expr.build_alpha158_lite`(:289) / `ALPHA158_LITE`(:286) | **未接线** | 生产只用了 `len(ALPHA158_LITE)` 做计数（`research.py:154`） | 说明取舍：要么接进特征构建，要么从文档里降级为"示例库" |
| `torch_models` + `gnn_models` + `sequence_dataset` + `torch_device` | **研究态（有意设计，不算 bug）** | `registry.py:531-539` 明确写"TFT/GNN 等候选可正常登记/promote（治理通道复用），但 promote 后不能被本函数加载……请勿将其 promote 为生产模型"；`promote_model.py`/`list_models` 默认 `model_name="lgbm_v1"` → 治理通道天然隔离。真实库 `gnn_v1` 4 candidate / `tft_v1` 0 行 / 生产仅 `lgbm_v1` | **不报**。残留 P3 两条见 B4b-11：`train_tft.py:48` 的 promote 指引会失败；GNN 无 test 段 |
| `purged_cv.PurgedGroupTimeSeriesSplit` | **半死** | 仅 `/research/cv-folds` 甘特图（`research.py:293`，展示用） | 保留，但把 horizon 纳入请求并做 `purge+embargo >= horizon` 校验 |
| `_DEFAULT_FIELDS`(:30) | 无害残留 | 字段由 API 传入 | 删除 |
| `_NUM_OPS`(:33) | **漏用（未接线）** | `If` 算子从未进入 GP 搜索空间（§4.4） | 接进 `_gen_expr` 或删除并修正注释 |
| `_register_model` 的 `kept`/`horizons` 形参 | 无害残留（有下游后果） | 未使用 → 成因见 B4a R9 | 随 R9 一起修 |

**不是死代码**：`signal_analysis`（`backtest.py:663-664` 调用）、`graph.propagate`
（`features.apply_propagate` → v2g 的 `g1_*` 列，真实分区里存在）、`train_lgbm`
（CLI + `monitor` 自动重训）、`train_service`（`datacenter.py:1127-1167` 四个端点）、
`grid_search`（CLI + 已产生 648 行 feature_runs）。

---

## 5. 目标 C：策略合理性（每条含五要素）

### 5.1 把标签改成"截面标准化"（免去均值开关，同时修正特征筛选口径）

- **当前问题**：标签是原始 5 日收益；不去均值时 L2 损失被市场共同波动主导，早停在第 1 轮
  （实测 `best_iteration=1`、`valid_rmse=0.0768` 比常数预测还差），且特征筛选的池化 IC 与
  评估的截面 IC 口径不一致（实测 atr_14 偏差 270×）—— 而"去不去均值"只由调用方 kwarg 决定、
  不落盘。
- **具体改法**：`train_lgbm` 里把 `xsec_demean` 换成 `label_xsec: Literal["raw","demean","zscore"]`
  **默认 `"zscore"`**：`label = (r − mean_t(r)) / (std_t(r) + eps)`（同日截面）。同时把
  `select_features_by_ic` 的 `rank_ic` 换成"逐日截面 RankIC 的均值"（i.e. `daily_rank_ic` 的
  per-factor 版本），或在筛选前无条件对标签做截面去均值。把 `label_xsec` 写进
  `metrics.json` + `registry.params_json`，并在 `evaluate_candidate` 里对口径不一致的候选
  拒绝 RMSE 比较。
- **预期收益（量级）**：本次 400 只真实面板实测 demean 使 `valid_rank_ic` 0.0144 → 0.0832、
  `ICIR` 0.071 → 0.433（**5.8× / 6×**）；zscore 相对 demean 还能消除"高波动日主导 L2"的
  残差（波动大的日子 σ_t 大，其平方误差天然更大）。特征筛选口径修正后可回收
  atr_14/hl_range/vol_20/v_rank_20 这四个截面 IC 0.026–0.082 的因子（当前全部落选）。
- **引入风险**：zscore 后 `valid_rmse` 的绝对值不再可比（必须靠口径字段而非数值比较；
  这正是 §3.2 的配套改动）；若某些交易日 `std_t` 极小（极端截面），需要 `eps` 兜底否则放大噪声。
- **验证方式**：同一 split 上跑 `raw/demean/zscore` 三条 A/B（`train_lgbm` 现有
  `retrain.py --no-xsec-demean` 已是雏形），比 `valid_rank_ic`/`valid_icir`/`test_rank_ic`
  三者同时改善才算成立；回归用例断言 `metrics["label_xsec"]` 存在且两口径候选被门禁拒绝比较。

### 5.2 用"样本外可验证"的方式做超参选择（现在是单段 valid 选参）

- **当前问题**：81（有效 27）组共用一个 train/valid，`pick_top1` 按 **池化 Pearson `valid_ic`**
  （而非截面 RankIC）取最大（B4b-20）；
  该段信噪比极低（生产库记录的 648 行里 `valid_ic` 均值 0.193 而 `test_ic` 均值 −0.219，
  即按 valid 选出来的参数在 test 上普遍为负），且 `min_child_samples` 轴是死的。
- **具体改法**：把 `app/backtest/param_search.py` 里已经写好、已被单测覆盖的
  `walk_forward_search`（`:287`，含 IS/OOS 折外拼接与 Deflated Sharpe，`:261`）接到
  `grid_search.run_grid_search` 上：在 **train 段内部**按 purged 折外推（复用
  `purged_cv.PurgedGroupTimeSeriesSplit`，`purge+embargo >= horizon`），用**折外 IC 均值**
  作排序键；valid 段只用来确认，test 段仍只在最后出镜一次。同时把 `GRID` 的
  `min_child_samples` 改成 `min_data_in_leaf`（或从 `DEFAULT_PARAMS` 删掉同义键）并把
  真实 `dataset_version` 写库。
- **预期收益（量级）**：有效搜索空间从"81 组假网格"变成 27 组真网格 + 5 折折外评分；
  按 §5.1 同源证据，口径修正带来的 IC 提升量级（5×）远大于超参本身的贡献，因此这一步的
  **主要收益是"选参不再噪声化"**（消除目前 ~0.02–0.1 的 IC 抖动）而不是绝对提升；
  同时省掉 2/3 的无效训练时间（648 行 → 216 行）。
- **引入风险**：折外评分方差大，5 折 × 252 天的窗口下 IC 标准误约 ±0.01，需固定折数/种子
  并同时报 ICIR；用折外 IC 选参会更保守（可能拒绝历史最优参数）。
- **验证方式**：把现有 `test` 段当"真 OOS"，对比"单段 valid 选参"与"walk-forward 选参"两条
  路径产出的模型在 test 上的 `test_rank_ic`/`test_icir`；同时给 `pick_top1` 加一条断言
  "不同 `min_data_in_leaf` 必须产生不同 valid_ic"（防止死轴复活）。

### 5.3 绝对收益回归 → 截面排序/相对目标（模型选择）

- **当前问题**：`objective="regression"` + L2 直接拟合收益，评估却是 RankIC —— 目标与评估不同源；
  早期退化（第 1 轮早停）就是这个错配的直接后果。
- **具体改法**：两条路，建议都做 A/B：
  1. 保留 L2，但标签用截面 rank 变换（`label = rank_t(r)/(N_t−1) − 0.5`），使损失与秩相关同源，
     且天然抗极值（现在靠 `max_abs_return=0.5` 手工 winsorize）；
  2. 改用 LightGBM 的排序目标：`objective="lambdarank"`（或 `rank_xendcg`）+ `group=每日截面` +
     `label_gain`，`metric` 改 `ndcg@k`。A 股截面选股本质是"每天从 N 只里挑前 k"，
     排序目标与 IC/多空组合的单调性直接对齐。
- **预期收益（量级）**：秩相关目标通常能把"日度 RankIC 的均值"提高 10–30%（主要来自极值鲁棒性
  与损失-评估同源），并把 ICIR 的波动降低；`lambdarank` 在 top-k 组合上的提升通常大于全体 RankIC。
- **引入风险**：`lambdarank` 的 `label_gain` 需要把连续收益分档（分档粒度是新的超参，会引入
  额外过拟合风险）；排序目标输出的分数只有序数意义，`pred_score` 的绝对量级变化会影响
  下游任何把 pred 当收益用的地方（`infer.predict_with_contrib` 只用排序 + SHAP，安全）。
- **验证方式**：同一 split 上比较三条目标的 `valid/test RankIC + ICIR + long_short`；
  另外用 `signal_analysis.quantile_spread_report` 检查分组单调性（现在已有该工具）。

### 5.4 多随机种子集成 + 波动率倒数样本加权（集成方式）

- **当前问题**：单模型单种子（`seed=42`，`:56`），`bagging_fraction=0.8` 只提供行/列采样；
  且 `/studio/alpha-eval` 明确表明平台的正式评估工具里 IC 的日度波动很大（生产模型
  `valid_icir=0.88`、`test_icir=0.53`）。`purged_cv.volatility_inverse_weights_from_close`
  已实现却未接线。
- **具体改法**：`train_lgbm` 增加 `n_seeds: int = 5`：用 `[42,43,44,45,46]` 各训一个 booster
  （同 `best_iteration` 中位数），`predict` 取 `mean(rank(pred_i))` 而非均值原始分；同时把
  `volatility_inverse_weights_from_close` 的输出接到 `lgb.Dataset(weight=...)`（20 日波动率
  倒数，已实现、有单测）。产物结构改为 `model_seed{k}.lgbm` + `features.json`，`infer` 端做
  多模型 rank 平均。
- **预期收益（量级）**：5 seed 集成通常把 ICIR 提高 10–20%、把 `best_iteration` 的种子敏感度
  （目前 42 号种子给 15 轮、另一初始化可能给 5 轮）压下去，直接降低 promote 门禁的"月度抖动"
  （真实库里 3 个 monitor 候选的 valid_rank_ic 在 0.087–0.102 之间反复越线/越线失败）；
  波动率加权按 `purged_cv` 文档的动机抑制微盘/高波动股对 L2 损失的支配。
- **引入风险**：训练时间 ×n_seeds（本机单次全量约分钟级，可接受）；`promote/load_prod_model`
  的产物契约要同步改（`model.lgbm` → 多文件），否则推理读到不存在的单文件 →
  这是一次**必须配套改 `registry`/`infer` 的结构性改动**，建议先加 `n_seeds=1` 的兼容路径。
- **验证方式**：同一 split 上比 `n_seeds=1 vs 5` 的 `valid_icir`/`test_icir` 与
  "换种子后 `valid_rank_ic` 的极差"（稳定性指标）；用 `tests/test_model_registry.py`
  的多产物场景补回归。

### 5.5 GP 挖掘必须有样本外与多重检验控制

- **当前问题**：`gp_miner` 的适应度是**全样本** |mean_ic|（`evaluate_expr_detail:244`），
  一次任务评估 pop×gens 个表达式、可以无限次启动，`task.best` 直接当成"挖到的因子"展示
  并推送通知（`:146-150`）；同时它的多空净值还是前视的（B4b-1）。
- **具体改法**：(a) `evaluate_expr_detail` 增加 `oos_start` 参数：适应度只用 `date < oos_start`
  的数据，`oos_start` 之后只算一次"留出 IC"随结果一起返回（前端并列展示）；(b) 记录并返回
  `n_evaluated`（本次任务实际评估的表达式数），据此给出 Bonferroni/Deflated-Sharpe 风格的
  |IC| 显著性门槛 `1.96/sqrt(N_eff·D)`；(c) 修掉 B4b-1 的同日收益后，多空净值再作为展示指标。
- **预期收益（量级）**：把"矿山"里的假因子筛掉 —— 现有机制下只要多跑几次任务，
  挑出 `|mean_ic|` 0.03–0.06 的纯噪声因子是必然事件（E[max|IC|] 随测试数增长）；
  留出段 + 显著性门槛能把这个数字压到接近 0（代价是召回率下降）。
- **引入风险**：留出段会缩短适应度可用样本（520 日快照里再切一半 → `min_days=60` 可能不满足，
  需要按比例放宽快照天数或降低 `min_days`）；用户会感觉"挖不出东西了"，需要在 UI 上说明门槛。
- **验证方式**：合成一个**纯随机**因子池（如从真实面板随机抽列并打乱日期），跑同一 GP 配置，
  断言"报告的 best 因子在留出段的 |IC| 不超过显著性门槛"——这是可自动化的反证用例。

---

## 6. 任务重点逐条回答（对照检查表）

| 重点项 | 结论 |
|---|---|
| purge/embargo 宽度是否 ≥ horizon | `train_lgbm` 是（`gap=horizon`，真实产物验证 5 交易日）；`purged_cv` **不是**（裸常量 5/2，与 horizon 无关联；API 允许 0/0）→ B4b-9 |
| 是否有边界 off-by-one | 无：`split_dates` 与 `PurgedGroupTimeSeriesSplit` 实测 gap 恰为设计值 |
| 早停是否用验证集 | 是（`valid_sets=[lgb_valid]`，`:318-328`；`train_sequence_model` 同）；test 不出镜 |
| 超参搜索是否嵌套 | **否**（单段 valid 选参，无 walk-forward，且选优主键用错口径）→ B4b-10/20 / §5.2 |
| 最终模型是否用 test 做选择 | 否（`registry.evaluate_candidate` 只读 valid；`grid_search.pick_top1` 排序键无 test） |
| train/valid/test 是否时间连续无重叠 | 是（含 gap，两端各 5 交易日） |
| 训练/推理特征是否同一套 | 是（同一 `FEATURE_VERSION` 分区 + 同一 `features_v2`/`build_factors` 调用链） |
| `features.json` 列顺序是否被严格执行 | 是（`infer.predict_with_contrib` 用 `X[features]`，缺列 raise） |
| `num_threads`/`effective_cpu_threads` 是否生效 | 是（booster.params 实测；生产 params.json `num_threads=12`） |
| 是否整表进内存 | 是（真实 2.48M 行 × 88 列，峰值 4–6 GB，无守卫）→ B4b-12 |
| 类别/缺失处理是否泄漏 | 无（不做编码/填充/scale；NaN 交给 LightGBM） |
| `scale_pos_weight` 类设定 | 存在但**未接线**（`volatility_inverse_weights_from_close`）→ §5.4 |
| `compute_guard`/`pipeline_lock` 并发守卫 | 成立（进程级，前提已声明）；训练任务三层门禁，失败不静默 |
| 状态机/失败是否静默 | 不静默（error 与 last_result 都落 kv）；GP 任务除外（B4b-19） |
| 旧模型是否被半成品覆盖 | 否（时间戳目录 + 先落盘后登记） |
| `metrics.json` 是否原子写 | **否**（`open("w")` 直写）→ B4b-17（P3，无覆盖风险） |
| GP 评估是否用未来数据 | 特征/标签分离，**没有**（但多空净值前视 → B4b-1） |
| 算子是否除零/log 负数 | `Log` 有 `clip(lower=EPS)`；除零对 Series 转 NaN（对常量抛异常，P3） |
| `_NUM_OPS` 意图 | 漏用：`If` 从未进入 GP 搜索空间 → §4.4 |
| `graph.py` 有无注入/危险属性访问 | 无（纯 numpy 聚合，无 eval/表达式） |
| `alpha_expr` 白名单能否绕过 | **不能**（15 种逃逸实测全拒） |
| 表达式能否资源耗尽 | 经 API 不可达（500 字符 + 有界算子）；库调用下有 `RecursionError`/`OverflowError` 类型缺口（P3） |
| torch 缺失降级路径 | 明确拒绝 + 安装指引，不静默降级 |
| 是否与 LGBM 主线重复 | 不重复；TFT/GNN 不能上线是**有意设计**（`registry.py:531-539`），残留 P3 两条 → B4b-11 |
| TFT 是否真有实现 | 有可训练的 Transformer 序列回归实现，**不是**真 TFT（命名口径问题）→ B4b-18 |

---

## 7. 复现命令汇总（全部已实跑）

```powershell
$base='D:\Python_Project\Alpha Quant Platform\backend'
$env:PYTHONPATH=$base; $env:PYTHONIOENCODING='utf-8'
$py="$base\.venv\Scripts\python.exe"

# ① 表达式白名单绕过 + 资源耗尽（B4b-13，结论：不可绕过）
& $py "$env:TEMP\b4b_probe_alpha_expr.py"

# ② GP 多空净值前视（B4b-1，结论：+759906574%）
& $py "$env:TEMP\b4b_probe_gp_nav.py"

# ③ purged_cv 隔离带 + LightGBM 别名（B4b-4/8/9）
& $py "$env:TEMP\b4b_probe_purged_cv.py"
& $py "$env:TEMP\b4b_probe_lgbm_alias.py"

# ④ 真实面板：池化 IC vs 截面 IC（B4b-3）
& $py "$env:TEMP\b4b_probe_featsel.py"

# ⑤ 真实面板端到端 train_lgbm（B4b-2/5，persist=False，不污染仓库）
& $py "$env:TEMP\b4b_probe_train_lgbm_real.py"

# ⑥ 生产库台账（B4b-4/6/11，只读 mode=ro）
& $py "$env:TEMP\b4b_probe_feature_runs.py"

# ⑦ GP 任务淘汰 + parse_expr 异常类型（B4b-13/15）
& $py "$env:TEMP\b4b_probe_gp_tasks.py"

# ⑧ 定向 pytest（按简报规定跑法；结果 17 passed）
$env:PYTHONPATH="$base\.tmp_testrun"; $env:TEMP="$base\.tmp_testrun"; $env:TMP=$env:TEMP
& $py -m pytest tests/test_ml.py tests/test_ml_leakage.py `
  "tests/test_lab_factor.py::test_factor_report_structure" `
  "tests/test_p2_ml.py::TestGridSearch::test_grid_space_is_81" `
  -q -p audit_mkdtemp_fix -p no:cacheprovider
# => 17 passed, 15 warnings in 9.52s
```

> 注：`pwsh` 会把子进程 stderr（LightGBM/loguru 的日志）当成 NativeCommandError，命令尾部的
> `[exit code: 1]` 属 PowerShell 行为，不是测试失败；判定以 pytest 自身的 `17 passed` 为准。

**测试覆盖盲区（可直接写成新用例）**：

1. `tests/test_lab_factor.py:149` 只断言 `quintile_navs` 的**结构**（长度 5、键名齐全），
   不断言净值口径 → B4b-1 完全没被覆盖。建议加：随机因子（与未来收益无关）的多空净值必须
   在合理量级，且"把因子滞后 1 日"后净值量级不变。
2. `tests/test_p2_ml.py:44-52` 只断言 `GRID` 字面笛卡尔积 = 81 → B4b-4 没被覆盖。
   建议加：同一 `(lr, leaves, ff)` 下三个 `mcs` 的 `valid_ic` 必须**互不相同**。
3. 没有用例断言 `metrics.json` 里含训练目标口径 → B4b-5 没被覆盖。
4. `tests/test_quant_upgrade.py:372` 用 `verify_no_overlap(tr, te, groups, horizon=7)`
   验证"5+2 隔离带"，与 `purged_cv.py:119` 的自证写法同源 → B4b-8 没被覆盖。
   建议加：`purge_window=0, embargo_window=0` 时 `cv_rank_ic_report(...)["min_gap_ok"] is False`。

---

## 8. 与其它批次的关系（避免重复计数）

- **不重复**：已知台账「`gp_miner.py` 缺 `except BaseException`」→ 只在 §4.4 B4b-19 给延伸
  （`finally` 必执行 + **无 reaper** + 永久卡死），单列为 P3。
- **不重复**：B4a R9「`params_json.kept_features` 恒空」→ 只在 §4.1 补"成因是
  `_register_model` 的 `kept` 形参未被使用"，不计入本批新问题。
- **不重复**：B5 已报的 `backtest/param_search.py:80-85` 笛卡尔积先物化后守卫 OOM → 本批只在
  §4.1 给训练侧的**内存量级估计**（同类风险的不同位置），不重报那条。
- **B4a R14（门禁不校验可比性）的延伸**：B4b-5 给出了 B4a 看不到的训练侧触发路径
  （`xsec_demean` 未落盘 + 生产库 181337/181713 的 1.25× RMSE 差）。
- **跨批次提示（主责 B7b）**：`/api/v1/datacenter/train/readiness`（`datacenter.py:1124-1129`）
  **没有任何鉴权依赖**（无 `require_role`），匿名可读 DATA_ROOT 路径与特征/关系边统计；
  `/train/status`、`/train/cancel` 只有 `viewer` 级别的 `researcher` 校验。本批不展开。
- **跨批次提示（主责 B4a）**：`graph.industry_edges` 读 `instrument.industry` 时
  3 个行业桶被 `MAX_PEER_BUCKET` 跳过（实测日志），即 v2g 的 `g1_*` 列在这 3 个桶的标的上
  是**部分覆盖**（NaN 邻居），不是全 NaN —— 与"无关系数据即全 NaN"的降级语义叠加时，
  训练侧看到的 `g1_*` 缺失模式比文档描述的更复杂。