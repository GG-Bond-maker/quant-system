# B4a · ML · 特征与推理 —— 审核报告（2026-09-18 批次）

审核对象：`backend/app/ml/features.py` / `features_v2.py` / `labeling.py` / `infer.py` / `predict.py` /
`registry.py` / `monitor.py`；相邻参照 `train_lgbm.py` / `purged_cv.py` / `data/features.py`。

纪律：本报告所有「确定」结论均已在**本机真实执行**验证（见每条「验证」栏与第 9 节复现脚本）。
所有写操作只落在 `%TEMP%\aqp_audit_b4a\`（SQLite 副本 + 模型目录副本），**未触碰仓库 `data/`**。

---

## 0. 逐文件结论速览

| 文件 | 结论 |
|---|---|
| `ml/features.py` (v1 因子内核) | **未发现 P0–P2 前视泄漏**。全部窗口为后向（rolling/shift(1)/ewm），无 `shift(-N)`；截断不变性已有测试覆盖。但 `LEGACY_FEATURE_VERSION` 为死常量（R17）；`g1_*` 传导列对当日 universe/关系快照敏感、并非 asof 稳定（R12，属 `apply_propagate` 语义，见下） |
| `ml/features_v2.py` | **整模块未接线**（R11，目标 B）。`build_alpha_v2` 全仓仅被 `tests/test_p2_rest.py` 调用；`FEATURE_VERSION="alpha_basic_v2"` 亦未登记进 `KNOWN_FEATURE_VERSIONS`，即使接线也会落库 fail-fast |
| `ml/labeling.py` | 标签构造正确：`shift(-horizon)` 只用在标签、特征列零改动；跨空洞（自然日 >25）与非有限值剔除逻辑正确。仅审计计数重复（R15，P3） |
| `ml/infer.py` | `load_prod_model` 正确代理注册表；`predict_with_contrib` 只校验「列缺失」、不校验列序（R18，P3 加固）。`infer_day` **无任何调用方**（R10，目标 B） |
| `ml/predict.py` | 面板优先 + 实时回退的链路与训练同源；但 `pred_return` 语义披露缺失（R13）、`_MIN_HISTORY_ROWS=60` 与 250 日预热要求不一致（R16） |
| `ml/registry.py` | promote 治理不变量（唯一 production、事务、产物先复制后写库）成立；但**版本回滚不可用**（R1、R2）、审计字段 `kept_features` 恒空（R9）、门禁不校验可比性（R14） |
| `ml/monitor.py` | 前视无问题、`reclaim_stale_retrain` 的启动回收接线正确（`main.py:64-68` 实测调用）；但**漂移判定误报 + 重训闭环无效 + IC 阈值尺度错配 + unknown 被 healthy 掩盖**（R3–R7），`monitor.py:718 label` = **无害残留**（P3，见 R21） |
| `train_lgbm.py`（参照） | 三段切分正确且 gap 恰好 = horizon（零冗余、无双段污染）；特征筛选仅 train 段；但 `gap_days` 无 `>= horizon` 断言（R19）、`xsec_demean` 未落库（R13）、`_register_model` 的 `kept`/`horizons` 形参未使用（R9） |
| `purged_cv.py`（参照） | Purge/Embargo 隔离带宽度 = purge+embargo 且 train 末日与 test 首日间隔 = 该值 +1，**数学上足够**；但 `purge_window` 默认硬编码 5、不与 `ML_LABEL_HORIZON` 联动，且 `verify_no_overlap` 的比较基准是同义反复（R20） |
| `data/features.py`（参照） | 单版本读取 + 重复键 fail-fast 正确；与 `monitor._resolve_feature_dir` 有一套 parity 测试守卫。但**全仓存在三套 feature_version 解析口径**（R8） |

---

## 1. 前视泄漏专项（本批第一重点）

### 1.1 特征窗口是否严格只用 t 及之前 —— 结论：是

逐列核对 `features.py::_one_symbol_feats`（L107–188）：`pct_change(w)`/`shift(1)`/`rolling(w)`/`ewm(adjust=False)`
全部为后向窗口，**无任何 `shift(-N)`**；`high/low/close/volume[t]` 当日常量不属于未来信息。
`labeling.py` 明确「标签只允许用 t 之后、特征只允许用 t 及之前」，实测 `build_forward_return_labels`
不改任何特征列（只新增 `label_ret`）。

已有守卫：`tests/test_feature_asof.py`（hfq asof 稳定）、`tests/test_ml_leakage.py::test_tc_leak_feature_truncation_invariance`。

### 1.2 未被现有测试覆盖的泄漏路径（本次补测）

| 路径 | 现有测试 | 本次实测结论 |
|---|---|---|
| `apply_propagate`（v2g 的 43 个 `g1_*` 列，占生产模型 60 特征中的 **20 个**） | **无任何测试**（`test_ml_leakage` / `test_feature_asof` 只调 `build_factors`） | **对「追加未来交易日」是 asof 稳定的**：同 universe 下 400 日 vs 300 日面板逐值比较，`g1_*` 不一致 **0/42**（脚本 probe11） |
| 同上，但对 **universe 变化** | 无 | **不稳定**：同一历史交易日，去掉一只标的 → **40/42 个 `g1_*` 列改变**（例：S1 2023-06-01 `g1_ma_gap_20` = −0.00082 → +0.02442）。原因：`graph.build_adjacency` 的行归一化邻接来自 `instrument.industry`（**无时序的快照**）+ `relations/edges.parquet`（当前快照），当日邻居集合变化即改变历史值 |
| `features_v2.build_alpha_v2` | 仅测列数 ≥220 | 模块未接线；且其 `market_ret`（L121–124）对未排序 DataFrame 直接 `groupby.pct_change()`，而 v1 走组内 `sort_values` —— 同一入参下两处口径可分歧 |

> 结论（延伸自「已知台账 v1/v2 两代口径」）：**v2g 的 `g1_*` 不是 asof 稳定量**，
> `features.py` 模块文档 L7–10 声称的「追加未来数据不会改写历史特征值」只对 v1 因子成立。
> 实际后果见 R12（`orchestrator` 增量守卫会因此回退全量重算，从而**重写历史 `g1_*`**）。

### 1.3 标签窗口与特征窗口是否有 gap —— 结论：恰好够，零冗余

`split_dates`（`train_lgbm.py:120-150`）实测算术（`tests/test_ml_leakage.py::test_tc_leak_gap` 已守卫）：

```
valid_start_idx = test_start_idx - gap_days - holdout_days
train_end_idx   = valid_start_idx - gap_days - 1
⇒ train_end 的标签窗口 [train_end, train_end+horizon] 落在 index train_end_idx+horizon
  = valid_start_idx - 1  (gap_days == horizon)
⇒ 恰好落在 gap1 的最后一个交易日，与 valid 零重叠、也零冗余
```
`valid_end` 与 `test_start` 同理。**gap_days 默认 = horizon，没有断言强制**（R19）。

### 1.4 标准化/中性化统计量用全样本还是训练段 —— 结论：无跨样本标准化；但标签的截面去均值未落库

- 生产特征空间**没有做任何全样本标准化**：`features.py` 只做「除以 close」的逐标的归一化；
  `xsec_demean` 只作用于**标签**（`train_lgbm.py:276-282`），逐日截面去均值只用同日信息，**不是泄漏**。
- 但该变换**没有被记录**在任何产物里（`metrics.json` 26 个键、`params.json` 15 个键、注册表 `params_json`
  均无该标志）；`monitor.py:540-546` 只能靠**注释里写死的具体生产版本号**（`20260905_003646_bulk1133_demean`）
  来复刻配方。见 R13/R14。

---

## 2. 训练与推理口径一致性（本批第二重点）—— 逐项对比

对比对象：训练入口 `train_lgbm`（含 `monitor._retrain_worker` 调用）vs 推理入口 `infer.predict_with_contrib`
（被 `orchestrator.step_infer`、`scripts/infer.py`、`predict.predict_symbol`、`run_backtest_real.py` 使用）。

| 维度 | 训练侧 | 推理侧 | 一致？ |
|---|---|---|---|
| 特征来源 | `DATA_ROOT/features/version=alpha_basic_v2g/year=*.parquet`（`monitor.py:534`；`train_lgbm` 由调用方传面板） | 同一面板：`orchestrator.step_infer:234` 用 `prod.feature_version`；`predict._latest_panel_row:71` 用常量 | ⚠ 版本解析**三套口径**（R8），值当前一致（实测 predictions 09-17 的 `feature_version=alpha_basic_v2g` ⊆ 面板） |
| 派生流程 | 面板已含 v1 因子 + `g1_*`（`apply_propagate`） | 面板优先；面板不可用时 `apply_propagate(build_factors(df))` 实时回退 | ✅ 同一函数族 |
| 列集合 | `kept = select_features_by_ic(...)`，写入 `features.json` | `X[features]`，`features = json.loads(features.json)` | ✅ **实测生产模型 features.json 的 60 列全部存在于面板**（脚本 probe1：missing = []） |
| 列顺序 | `X_tr = X.loc[train_mask, kept]`（`train_lgbm.py:311`） | `matrix = X[features]`（`infer.py:51`） | ✅ 两边都是 `kept` 序（**已逐项核对**）。注意 LightGBM 默认 `validate_features=False`（实测重排列序预测值不同）→ 顺序正确**纯靠调用点自觉**，无守卫（R18） |
| 缺失值填充 | 不填充，保留 NaN（LightGBM 原生处理） | 同 | ✅ |
| 截面上计算 | 面板构建期一次性全市场截面 | 推理读面板（同源）；实时回退单标的 → `g1_*` 全 NaN（已披露 `feature_source`） | ⚠ 已披露的降级，非静默 |
| 标签变换 | 质量策略 + `xsec_demean` | `pred_score` 即被去均值目标的输出 | ⚠ 变换未落库、语义未披露（R13） |
| 目标函数 | L2 回归 + 验证段早停（best_iteration） | `num_iteration=booster.best_iteration or -1`（`infer.py:53,57`） | ✅ 一致 |

**差异清单（可执行项）**：① R8 版本解析三口径；② R13 标签变换未落库/未披露；③ R18 列序无守卫；
④ `features_v2` 完全不在链路内（R11，因此 **不存在** features_v2 ↔ infer 的线上口径分歧——这点可以明确排除）。

---

## 3. 目标 A — Bug

### R1（P1 · Bug/运维 · 确定）版本回滚被 promote 门禁的单调性锁死 → 回滚功能实际不可用

**位置**：`app/ml/registry.py:462-479`（`evaluate_candidate` 相对比较）、`registry.py:339-344`（门禁先于复制块）。

**论证**：`promote_model` 要求候选 `valid_rank_ic >= 当前生产 - rank_ic_tolerance`，而 `rank_ic_tolerance` 默认 **0.0**。
由于每次成功 promote 都通过了这条检查，**生产模型的 valid_rank_ic 沿提升链单调不减**；
因此任何「回滚到历史版本」的尝试，其 IC 必然 ≤ 当前生产值 → **恒被拒绝**（除非恰好相等）。

**实测**（沙箱 DB 副本，脚本 repro.py）：
```
PROD = 20260905_181713_repaired 0.10203323242532483
=== 实验2：回滚到上一个生产版本 20260905_003646_bulk1133_demean ===
  evaluate_candidate -> False | valid_rank_ic 劣于生产 0.0920 < 0.1020
  checks: {"min_valid_rank_ic": {...pass: true}, "min_valid_icir": {...pass: true},
           "rank_ic_vs_prod": {"candidate": 0.092016, "production": 0.102033, "pass": false}}
  returned: {"promoted": false, ...}
```
**触发条件**：`python scripts/promote_model.py --version <任一历史版本>` —— 这是运维在「新模型线上表现异常」时唯一
的回退手段。

### R2（P2 · Bug · 确定）回滚路径的上游：门禁一旦放行即抛未捕获的 `shutil.SameFileError`

**位置**：`registry.py:350-356`
```python
dst_dir = prod_root() / f"{model_name}_{version}"
for name in (artifact, "features.json", "params.json", "metrics.json"):
    src = model_file.parent / name          # 已 promote 过的版本，model_path 已被改写进 prod/（L372-375）
    if src.exists():
        shutil.copy2(src, dst_dir / name)   # src == dst → SameFileError
```
已 promote 过的版本，其注册表 `model_path` 被更新为 `prod/<name>_<version>/model.lgbm`，
而 `dst_dir` 恰好等于 `prod_root()/<name>_<version>` → **同一路径自拷贝**。

**实测**（沙箱，脚本 repro.py；把 DB 的 model_path 重写到沙箱模型目录以忠实复现）：
```
=== 实验1：重推当前生产版本 ===
  RAISED shutil.SameFileError: ...prod/lgbm_v1_20260905_181713_repaired/model.lgbm
     and ...prod/lgbm_v1_20260905_181713_repaired/model.lgbm are the same file
```
**触发条件**：① `promote_model.py --version 20260905_181713_repaired`（当前生产版本，用户从 `--current` 拷下来就会踩）；
② 任何「IC 恰好相等或自定义 policy 放宽」的回滚。异常发生在 `_connect()` 之前，**不破坏注册表**，
但 `SameFileError` 不是 `ModelRegistryError`，CLI 的 `except`（`promote_model.py:104`）接不住 → 裸 traceback。

### R3（P2 · 策略/误报 · 确定）PSI 对「水平型」价量因子必然误报 → 永久 degraded + 12h 一次重训 churn

> **✅ 已修（2026-09-22，第 12 轮 = 报告 P1-17 / §8.2 第 14 项）**：
> ① PSI **判定口径**改为**按交易日截面标准化**（`z=(x−当日中位)/(当日 MAD×1.4826)`），
> 池化原始 PSI 降为 `psi.raw` **披露字段**；② 报告此处建议的"改比**截面分位/秩分布**"
> **经实测退化**（秩在截面内构造上恒均匀 ⇒ `mean=0.0011/max=0.0131`、判别力归零），
> 故改用同思路的非退化版本，并在代码里写明理由；③ IC 通道的 σ 同时按**当日池宽**折算
> （本条未提，但属同一 S4 项：见报告 R5）。
>
> 真实面板（`alpha_basic_v2g` **1,052,956 行 × 85 因子**，2024-12-16→2026-09-17）**逐位复现**
> 本条证据（`psi.max=3.3051 / mean=0.2646`、top5 一致）后重算：

| 口径 | mean | max | >0.10 | >0.25（降级） |
|---|---|---|---|---|
| 池化原始值（**修复前判定口径**） | 0.2646 | **3.3051**（`g1_ma_gap_250`） | 39/85 | **21/85** |
| **截面标准化（修复后判定口径）** | 0.0415 | **0.2024**（`g1_vol_20`） | 12/85 | **0/85** ✅ |
| 截面内秩分布（报告字面建议） | 0.0011 | 0.0131 | 0/85 | 0/85 ❌ **退化** |
| 同一股票池 + 原始值（混杂对照） | 0.2647 | 3.3051 | 39/85 | 21/85 |

> **附带纠错（本页/报告的池宽推断）**：曾推测 PSI/KS 误报源于"基线窗口是 120 只时代"，
> 实测**不成立**——两窗口每日标的中位数为 **2468 vs 2492**，"同一股票池"口径与池化口径
> **完全同值**（0.2647/3.3051）。池宽问题**只在 IC 序列**上成立（`ic_series_tail.n_symbols`
> 历史 119~121、最近 2486~2492 ⇒ `std_ic=0.1151` 需折算为 **0.0253**）。
> **不误报 + 仍敏感**：真实面板判定口径 0/85 超 0.25；注入异质样本（`ma_gap_250 ← ret_1`）
> 30%/50% ⇒ **0.3540/0.4166 > 0.25** 报警；**如实固化局限**：注入 70% ⇒ 0.2271（非单调）、
> 注入 `g1_ma_gap_250` 50% ⇒ 0.2024（无响应），截面内单调变换/跨日打乱均不可见。

**位置**：`monitor.py:285-323`（`compute_psi`，跨标的**逐行池化**分布，近 20 日 vs 之前 250 日）、
`monitor.py:702-704`（`psi.max > 0.25` 触发 `maybe_auto_retrain`）。

**实测（真实生产面板 + 真实快照）**：
- 快照 `monitor_factor_health`：`psi.max = 3.3051`（`g1_ma_gap_250`）、`psi.mean = 0.2646`、`drift_state = degraded`；
  `state_log` 显示 **2026-09-03 起一直是 degraded**；`retrain.attempted = true`。
- 我用真实面板**重算**该因子 PSI = **3.3051**（与快照逐位一致，脚本 probe7）；根因是分布整体平移：
  ```
  ma_gap_250   q10/q50/q90 : 近20日  -0.2468 / -0.0610 / +0.2971
                            基线250日 -0.1580 / +0.0899 / +0.5946   → PSI = 0.4686
  ```
  即市场从「价格远在 250 日均线上方」转为「均线附近/下方」——**因子分布随市场中枢平移**，
  与「因子预测力是否变化」无关。样本量还极不对称（近 20 日 × 2490 ≈ 2.2 万行 vs 基线 250 日 ≈ 62 万行）。
**触发条件**：任何一次像样的趋势反转（A 股每年都会发生）→ `drift_state=degraded` → 只要距上次重训 >12h 就再训一次。

### R4（P3 · 统计口径 · 确定）KS 的 `n_over_crit` 在该样本量下恒等于因子数，无判别力

`monitor.py:367-369`：`crit = 1.36*sqrt((n1+n2)/(n1*n2))`。真实面板下 `n1≈277k, n2≈22k`
→ `crit ≈ 0.0095`，而实测 `mean KS = 0.1161`。快照实测：`n_factors = 85`，`n_over_crit = 85` ——
**85/85 全部"超临界"，该列恒满**（等于没有信息，容易被读成"全部因子都漂移了"的强信号）。

### R5（P2 · 策略/一致性 · 确定）自动重训与 promote 门禁构成「永远拒绝」闭环，生产模型自 09-05 冻结

**位置**：`monitor.py:552-555` + `registry.py:472-479`。

**实测**（repro.py 实验3 + DB 副本）：
```
20260914_174827_monitor: promote=False reason=valid_rank_ic 劣于生产 0.0871 < 0.1020
20260907_195605_monitor: promote=False reason=valid_rank_ic 劣于生产 0.1020 < 0.1020   ← 差 4e-5 被拒
model_registry 生产模型 = 20260905_181713_repaired（created_at 2026-09-05），prod 数量 = 1
```
即：至少 2 次漂移重训产出的候选全部被拒，**生产模型 13 天未更新**，而 PSI 仍处 degraded →
每 12h 再训一次的循环会无限持续（重训实测很快：候选 `20260914_174827_monitor` 的版本时间戳 17:48:27
与 `created_at` 09:49:05 UTC 相差约 38 秒，说明训练本体仅数十秒，churn 主要浪费在重复劳动与磁盘）。
`rank_ic_tolerance=0.0` 使「与生产同水平但更新数据」的候选几乎必然被拒——这是一个**偏向不更新**的稳态。

### R6（P2 · 策略 · 确定）IC 状态机的 σ 阈值跨股票池宽度不可比 → 衰减检测近乎失效

**位置**：`monitor.py:641-647`（`hist_std = prev_dates.std()`）、`monitor.py:383-391`（1.0σ/1.5σ）。

**实测**：
- 快照：`history.std_ic = 0.1151`（窗口 105 日）、`recent.mean_ic = 0.0774`、`ic_state = healthy`。
- 该 105 日窗口全部由 **118–121 只**截面的预测日构成（`predictions/date=20250808..20260821` 实测 118–121 行）；
  理论噪声 `1/sqrt(119) ≈ 0.092` ✓ 与 0.1151 同量级。
- 09-14 起预测截面变为 **2484–2492 只**（同样实测）→ 逐日 RankIC 噪声 ≈ `1/sqrt(2489) ≈ 0.020`，
  而判定阈值仍是 `hist_mean − 1.5σ = 0.0465 − 0.173 = −0.126` —— **要 IC 塌到 −0.126 才报警**，
  在 2490 只池上几乎不可能发生 ⇒ **漏报**。
- 内部不一致：`registry.auto_min_rank_ic`（`registry.py:95-126`）已经按横截面宽度推导显著性门槛，
  monitor 的状态机没有采纳同一口径（`evaluate_candidate` 用了它，`_ic_state` 没用）。

### R7（P2 · Bug · 确定）`_worst` 把 `unknown` 排在 `healthy` 之下 → 数据不足时误报健康 + 虚假「恢复」通知

**位置**：`monitor.py:52`（`_STATE_RANK = {"unknown": 0, "healthy": 1, ...}`）、`monitor.py:679`、`monitor.py:714-727`。

**实测**：
```
_worst(['unknown', 'healthy'])  = 'healthy'
_worst(['unknown', 'degraded']) = 'degraded'
```
即 `ic_state = unknown`（可评估日 < `MIN_EVAL_DATES=10`）而 `drift_state = healthy` 时，快照 `state` 报 **healthy**；
若上一状态是 degraded，`_notify_state_change` 会推送「因子健康度恢复【健康】」——**在 IC 完全无法评估时的虚假恢复**。

### R8（P2 · 一致性 · 确定）同一个 feature_version 有 3 套解析口径；monitor 快照不披露自己读了哪一版

| 消费方 | 解析方式 | 位置 |
|---|---|---|
| 写面板 / 训练 / 漂移重训 | 模块常量 `ml.features.FEATURE_VERSION`（`alpha_basic_v2g`），**忽略配置** | `features.py:33`、`orchestrator.py:103,116`、`scripts/build_features.py:24`、`train_service.py:45`、`monitor.py:525,534` |
| 推理 | 注册表 `prod.feature_version`，目录缺失时回退常量并告警 | `orchestrator.py:233-238` |
| 监控 / data.features 读取 | `settings.FEATURE_VERSION`；`.env` **未设置**（实测 .env 无该键）→ 按 parquet **mtime 自动选版** | `monitor.py:138-156`、`data/features.py:21-52` |

**实测现状**：`version=alpha_basic_v1` 最新 mtime `2026-09-05 00:12`，`version=alpha_basic_v2g` 最新 mtime `2026-09-18 00:15`
→ 当前自动选到 v2g（与推理一致，暂时无害）。**风险**：任何一次对 v1 分区的重写都会让 monitor 切到 v1 去算 PSI/IC，
而 predictions 用的是 v2g；同时 `run_monitor` 的快照里**没有** `feature_version` 字段
（predictions 分区自 09-14 起已带 `feature_version` 列，无人交叉校验）。

### R9（P2 · 死数据/审计 · 确定）`params_json.kept_features` 恒为空；注册表 `test_end` 列永不被写入

**位置**：`train_lgbm.py:179-200`（`_register_model(version, metrics, model_dir, kept, horizons)` 的 `kept`/`horizons` **形参未被使用**；
`metrics` 字典里没有 `kept_features`，该键只在函数**返回值**里 `train_lgbm.py:396`）、`registry.py:213`（`metrics.get("kept_features", [])`）、
`registry.py:207`（`_d(split.get("test_start")), None,` → `test_end` 恒 NULL）。

**实测**：
```
prod params_json: {'kept_features': [], 'split': {...7 keys...}}   ← kept_features 长度 0
同一模型 features.json: 60 列（含 20 个 g1_*）
model_registry 全部行 test_end = None
```
影响：以注册表为唯一事实源的审计/合规视图拿不到因子清单（磁盘 `features.json` 仍在，故非数据丢失，属**静默空值**）。

### R12（P2 · 一致性/asof · 确定）`g1_*` 传导列不是 asof 稳定量 → 增量守卫失败时全量重写历史

**位置**：`ml/graph.py:86-105`（邻接来自 `instrument.industry` 当前快照 + `relations/edges.parquet`）、
`ml/features.py:241-263`（`apply_propagate`）、`orchestrator.py:159-181,215-218`（增量一致性守卫 → 失败回退 `_full()`）。

**实测**：同一历史交易日，去掉一只标的 → 40/42 个 `g1_*` 列改变（第 1.2 节表格）。
**后果链**：某标的当日从面板消失/新增（停牌、退市、扩池、行业字段补齐）→ 守卫比对失败 →
`except` 分支 `_full()` **按当前 universe 重算并覆盖全部历史 `g1_*`**，即 `orchestrator` 文档所称
「历史段逐字不动」对 `g1_*` 不成立（对 v1 因子成立）。守卫会记 WARNING，**不是静默**，但历史特征值确实会变。

### R13（P2 · 一致性/策略 · 确定）`xsec_demean` 未落库 + `pred_return` 语义与实现不符

**位置**：`train_lgbm.py:224,276-282`（逐日截面去均值）、`monitor.py:540-546`（靠注释复刻配方）、
`predict.py:8-9,132-137`（`pred_return` 描述为「未来 N 日收益率点估计」，`confidence_basis` 披露了 confidence 却没披露标签变换）。

**实测**：`metrics.json` 键 26 个、`params.json` 15 个，**均无 `demean` 相关字段**（`any("demean" in k)` = False）；
生产 `pred_score` 在 2026-09-17 的 2492 只截面上 `mean = -0.00228, std = 0.00562`
（std ≈ IC×σ_y ≈ 0.1×0.078 = 0.008，属 L2 收缩估计的正常量级，**不是**尺度 bug）。
真正的问题是**语义**：训练目标是 `r − mean_t(r)`，模型的逐日常数分量被有意剥离，因此 `pred_return`
是**截面相对收益**的估计；把它当绝对预期收益展示会带上一个与信号同量级（~0.5%）的未披露偏置。
用于排序（screener）不受影响。

### R14（P2 · 一致性 · 确定）promote 门禁不校验候选与生产的可比性（feature_version / dataset / 验证区间）

**位置**：`registry.py:393-508`（四个比较维度全为裸数值）。
**实测（DB 副本）**：
```
生产   20260905_181713_repaired | feature_version=alpha_basic_v2g | ds_1133s1239411r20220104-20260904 | train_start 2022-01-04 | valid 2024-07-25..2025-08-07
候选   20260914_174827_monitor  | feature_version=alpha_basic_v2g | ds_1729s1610075r20180102-20260914 | train_start 2018-01-02 | valid 2024-08-02..2025-08-15
```
两个 `valid_rank_ic` 来自**不同 universe（1133 vs 1729 只）与不同验证区间**，却被直接比大小（0.10199 vs 0.10203）。
`PromotePolicy` 文档（`registry.py:70-75`）自己承认「不同样本规模下 IC 绝对水平不可比」并为此引入了
`auto_min_rank_ic`（只用于绝对门槛），**相对比较却未做同样的可比性检查**。

### R15（P3 · 审计口径 · 确定）标签审计把同一批样本计两次

`labeling.py:151-160`（跨空洞 → 置 NaN）先于 `labeling.py:172-179`（统计非有限并剔除）→ 后者把前者计进 `n_non_finite`。
**实测**：生产 `label_quality` 中 `n_gap_invalid = 119` 且 `n_non_finite = 119`（完全相等）→ 日志 `跨越空洞=119 | 非有限=119`
读起来像 238 个问题，实为同一批 119 行。

### R16（P3 · 边界 · 疑似）`_MIN_HISTORY_ROWS = 60` 与 250 日预热要求不一致

`predict.py:32` 允许 ≥60 行即进入实时构建分支，而 `features.py:13-14` 明确要求 ≥250 个交易日，
否则 `ma_gap_250 / mom_250 / beta_250` 等长窗口因子必为 NaN。60–249 行时（次新股 + 面板缺失同时发生）
LightGBM 会用默认分支给出预测，响应中只有 `feature_source="realtime"`（该字段语义是「g1_* 为 NaN」），
没有「大量特征缺失」的披露。需真实次新股样本才能定量确认为何种偏差。

### R19（P2 · 边界/潜在泄漏 · 疑似，当前调用方未触发）`train_lgbm` 不校验 `gap_days >= horizon`

`train_lgbm.py:244`：`gap_days = gap_days if gap_days is not None else horizon` —— **无断言**。
`tests/test_ml_leakage.py:135-137` 显式承认「split_dates 不做拦截（由调用方保证 gap >= horizon）」。
一旦有人传 `gap_days=0`，train 末日的标签窗口直接跨进 valid 段（单参数即可造成泄漏）。
**实测当前所有调用方**：`monitor._retrain_worker` 不传（默认=horizon ✓）、`scripts/retrain.py` 不传、
`grid_search.py:69,83` 硬编码 5、`train_service.py:149` 传 `horizon`、测试全传 5 → **当前无触发**，属加强守卫的建议。

---

## 4. 目标 B — 死代码 / 未接线

### R10（P3 · 死代码 · 确定）`infer.infer_day` 无任何调用方

`infer.py:96` 定义；全仓 grep 只有定义与模块 docstring。真实推理走 `orchestrator.step_infer`（L221-257）
与 `scripts/infer.py`，两者各自重写了同一段逻辑（读分区 → `predict_with_contrib` → `atomic_write_parquet`）。
`infer_day` 还**不写** `model_version/feature_version/label_horizon` 三列，而下游 `step_screener_dump` 依赖
`feature_version`（L332-336）→ 即使被接线，产出的 predictions 分区也会让 `feature_versions` 落 NULL。
建议：删除，或让 `step_infer`/`scripts/infer.py` 收敛到它并补齐三列。

### R11（P2 · 死代码/多实现 · 确定）`features_v2.py` 整模块未接线

- `build_alpha_v2` 的唯一调用方是 `tests/test_p2_rest.py:75`；
- `FEATURE_VERSION = "alpha_basic_v2"` 未登记进 `KNOWN_FEATURE_VERSIONS`（`features.py:44-47`），
  `features.py:42-43` 亦明确注明「未接线」；
- 模块内部另有 3 处口径瑕疵：`market_ret` 不排序即 `pct_change`（L121-124）、
  `ret_skew_rank_{w}` 实为滚动 z-score 而非分位（L90-91）、`amount_z_{w}` 实为时序偏离而非截面 z（L92）。
**处置建议**：先决定是否并入生产（若并入需登记版本 + 补 asof/一致性测试），否则整文件删除，
或移入 `experiments/` 并加 `# 未接线` 标注——当前状态是「20 万行级代码占用维护带宽但无人运行」。

### R17（P3 · 死代码 · 确定）`LEGACY_FEATURE_VERSION` 是死常量

`features.py:37` 定义，全仓唯一读取点是同一文件的 `KNOWN_FEATURE_VERSIONS`（L46）。
没有任何代码用它在两代特征空间之间做 A/B 读取（A/B 实际靠 `research.py`/`studio.py` 的 `version` 查询参数）。
价值仅在于给 `assert_known_feature_version` 白名单留一个 v1 名额（该名额本身有用，但不需要这个具名常量）。

### R21（P3 · 无害残留 · 确定）`monitor.py:718 label` 的判定

```python
label = {"healthy": "健康", "watch": "观察", "degraded": "降级", "unknown": "数据不足"}  # 从未使用
if cur == "degraded": kind_msg = "因子健康度【降级】"   # 状态已由 kind_msg 完整表达
```
下游消息（L727）只用 `kind_msg` + `mean_ic`，**四种状态的中文标签在 `kind_msg` 里已全覆盖**，
故 `label` 是**无害残留**（ruff F841 唯一命中点，无行为影响），可直接删除。
（同批静态基线里 `gp_miner.py:64 head` 未在本质疑范围内。）

---

## 5. 目标 C — 策略合理性（按简报五要素）

### C-1 标签 horizon 与经济含义是否匹配 —— 基本匹配，但 horizon 与调仓/成本脱节

- **当前问题**：`ML_LABEL_HORIZON=5`（5 交易日 ≈ 1 周），标签是 hfq 总收益（含分红再投），经济含义清晰；
  但系统没有任何地方把 horizon 与**换手/成本**联系起来：`pred_score` 每日重排（`step_infer` 每日落 predictions），
  而标签是 5 日收益 ⇒ 若按日调仓，实际持有期与训练目标不一致（等效 5 倍换手）。
- **具体改法**：① 在 `metrics`/注册表记录 horizon（已有 `horizon` 键 ✓）并在 `predict.py` 的
  `confidence_basis` 旁加 `horizon_basis`；② 若要日频调仓，训练叠加 horizon ∈ {1,5} 并用预测差
  `pred_5 − pred_1` 近似「持有 1 日后剩余 4 日收益」；③ 更省力：在选股/回测侧**每 5 日才换仓**（与标签对齐）。
- **预期收益**：消除「日频换手 vs 5 日标签」的口径错配，按 A 股 0.05% 印花税 + 0.025% 佣金 + 滑点的量级，
  把换手从 250 次/年降到 50 次/年可省约 **0.5%–1.5%/年**。
- **引入风险**：降低换手会削弱 IC 兑现速度（信号衰减半衰期实测**无法拟合**：快照
  `mean_ic_by_horizon = {1:0.0322, 2:0.0393, 3:0.0392, 5:0.0503, 10:0.0465}`，IC 随期数**上升**，
  5 日最高）→ 说明信号本身偏中周期，缩短 horizon 反而更差。
- **验证方式**：用现有 `predictions` + 特征面板 `close` 构造 h∈{1,2,3,5,10} 的逐日 RankIC/ICIR 曲线
  （可直接用 `monitor.compute_ic_by_horizon`），并在回测里对比换手与净值。

### C-2 标签是否做了截面标准化 —— 做了去均值，**没做**标准差标准化；且未落库

- **当前问题**：`xsec_demean` 只减同日均值，不减同日 std。横截面收益离散度在不同日期差别很大
  （实测 09-07 那周 5 日收益 std = 0.078），L2 损失因此让高波动日主导梯度；且该标志未落库（R13），
  生产配方靠注释复刻。
- **具体改法**：`label_ret_z = (label_ret − mean_t) / (std_t + eps)`，`eps` 用截面 std 的下限（如 1e-4）防止
  窄截面爆量；同时把 `xsec_demean`/`label_scale` 写进 `metrics` 与 `params_json`。
  ⚠️ 注意：逐日去均值/除以 std 都是**逐日常数变换**，对逐日 RankIC/IC 无影响，只改 RMSE 与训练动态——
  因此门禁**无法**因此变化（见 C-4 的加固）。
- **预期收益**：高波动日不再主导损失，历史实测同类改动使早停轮数从个位数改善（仅在 `xsec_demean`
  的注释中有该项记录，未测 std 标准化）——预计 ICIR 提升量级 **0.05–0.2**（需实测）。
- **引入风险**：极窄截面（<30 只，`predictions/date=20260907` 实测仅 19 行）上 std 估计不稳 → 必须加下限与最小截面守卫。
- **验证方式**：同一 train/valid/test 切分下对比 `valid_rank_ic`/`valid_icir`/`best_iteration`；
  并断言「逐日 RankIC 序列不变、RMSE 下降」以确认只是尺度变化。

### C-3 purge/embargo 是否足够 —— 训练路径够（但零冗余、无断言），CV 路径有漏洞

- **当前问题**：① `train_lgbm` 的 gap 恰好 = horizon（第 1.3 节），**零冗余**且无 `gap >= horizon` 断言（R19）；
  ② `purged_cv` 的 `purge_window` 默认硬编码 5、不与 `ML_LABEL_HORIZON` 联动，若 horizon 改成 10 则默认 purge 不足；
  ③ `cv_rank_ic_report` 的验收 `verify_no_overlap(..., horizon=purge_window+embargo_window)`
  是**同义反复**（`train_end = test_start − (purge+embargo)`，间隔恒 = purge+embargo+1 ≥ 传入值）→
  `min_gap_ok` **永远为 True**（脚本 probe 未跑该函数，但由 `purged_cv.py:62,68-75,119` 的构造可直接推出，
  且 `tests/test_quant_upgrade.py:370-398` 也从反面印证它恒真）；
  ④ `/research/cv-folds` 允许 `purge_window=0`（`research.py:281` `ge=0`）→ 可在「防泄漏 Purged CV」页面上
  展示一个零 gap 的划分。
- **具体改法**：`train_lgbm` 加 `assert gap_days >= horizon`；`PurgedGroupTimeSeriesSplit.__init__` 的
  `purge_window` 默认值改为 `None → 取 settings.ML_LABEL_HORIZON`；`cv_rank_ic_report` 把
  `verify_no_overlap(..., horizon=label_horizon)` 改用**标签 horizon** 而不是自身构造常数；
  `/cv-folds` 加 `ge=ML_LABEL_HORIZON` 校验或明确返回 `leak_risk` 提示。
- **预期收益**：消除「配置改 horizon 就静默泄漏」的单点，并把 CV 页面的可信度恢复到名副其实。
- **引入风险**：默认 purge 变大（若 horizon 调大）会减少可用 fold 数 → 需提示而非静默 `continue`。
- **验证方式**：新增单测「horizon=10 时默认 purge ≥ 10」与「purge_window=0 时 `min_gap_ok=False`」（当前恒 True，改后会变红）。

### C-4 模型是绝对收益回归还是截面排序？对 A 股哪个更合理？

- **当前问题**：形式上是**L2 绝对收益回归**，但标签被逐日去均值 ⇒ 实际学的是**截面相对收益**；
  对外字段却叫 `pred_return`（R13）。A 股（散户主导、行业/风格轮动剧烈、市场 β 占 5 日收益方差的绝大多数）
  应当以**截面排序**为主目标。
- **具体改法**：① 明确切到排序目标：LightGBM `objective="lambdarank"`（按日期做 group）或
  `objective="regression"` + `label_gain`，并保留 RankIC 早停；② 至少把 `pred_return` 明确更名为
  `pred_rel_return`（或保留字段名但在响应里加 `return_basis` 披露「截面相对收益，已剔除当日市场均值」）；
  ③ 排序场景下 `pred_score` 绝对水平无意义，前端不应展示为百分比收益。
- **预期收益**：与评价指标（RankIC/ICIR）同口径，避免「用 L2 训练、用 RankIC 验收」的错配；
  实测证据是该模型 `best_iteration = 42`（很浅）却 `valid_rank_ic = 0.102`——说明树主要在做粗排序。
- **引入风险**：lambdarank 对 group（每日截面）内样本量敏感（`predictions/date=20260907` 实测仅 19 行），
  窄截面日期需跳过；且排序目标输出的 score 不可解释为收益率，需同步改前端措辞。
- **验证方式**：同一 train/valid 下比较两类目标的 `valid_rank_ic`/`valid_icir`/换手率；门禁需先修 R14
  （否则跨配方比较无意义）。

### C-5 可显著提升的方向（按预期收益/成本排序）

| # | 当前问题 | 具体改法 | 预期收益（量级） | 引入风险 | 验证方式 |
|---|---|---|---|---|---|
| 1 | **漂移触发器测的是特征分布而非预测力**（R3/R5）→ 永久 degraded + 每日无效重训 | 触发条件改为「PSI 超标 **且** IC 状态非 healthy」，或直接用 `mean_ic15` 跌破 `hist_mean − k·SE`（SE 按池宽度，见 C-6）；PSI 对水平型因子（`ma_gap_*`/`bias_*`/`mom_*`/`dist_*`/`channel_pos_*`）改为**截面内排名后再算 PSI** | 省下每日一次全量训练 + 恢复漂移告警的可信度（实测 PSI 3.3051、KS 85/85 全是假阳性） | 调高触发门槛会延迟发现真实漂移 → 必须保留 IC 通道作为并联信号 | 用现有面板重算 PSI/KS，断言「趋势反转期水平型因子不再单独触发 degraded」；回放 09-03~09-18 的状态日志 |
| 2 | **重训永远无法上线**（R5） | `rank_ic_tolerance` 改为按噪声推导（如 `k·auto_min_rank_ic(n_sym, n_days)`），并要求同时满足「IC 不劣」与「RMSE 不劣」才拒绝 | 让「同水平但更新数据」的候选能上线，缓解生产模型陈旧（实测已 13 天未更新） | 容忍度放宽会让略差的模型上线 → 用 test 段做一次性审计 | 用 DB 副本重放 20260907/20260914 两个候选，断言在新 policy 下 promote 决策与理由 |
| 3 | **门禁跨口径比较**（R14） | `evaluate_candidate` 增加 `feature_version` / `dataset_version` 一致性检查，不一致时要求绝对门槛 + 打印警告 | 避免 v1/v2g 或不同 universe 的 IC 被直接比大小（实测 1133 vs 1729 只） | 新增硬失败可能挡住合法的数据更新 → 用「同 feature_version 且 dataset_version 为前缀兼容」的宽松判据 | 单测：构造两个不同 feature_version 的注册行，断言拒绝并给出原因 |
| 4 | **标签未记录配方**（R13） | `metrics`/`params_json` 写入 `xsec_demean`、`label_scale`、`label_policy`；`predict.py` 增加 `return_basis` 口径字段 | 消除「靠注释复刻生产配方」的隐患（当前 `monitor.py:540` 就是这么做的） | 前端需同步展示新字段 | 训练一次后断言 `metrics.json` 含该键；对照 `test_predict_feature_contract` 同风格加契约测试 |
| 5 | **`g1_*` 不是 asof 稳定量**（R12） | 引入行业/关系数据的**时点版本**（`edges_snapshot_date`），或把 `g1_*` 改为「用当期截面 + 冻结邻接」并在面板元数据记录邻接指纹；至少把邻接指纹写进 features 目录的 manifest | 让历史 `g1_*` 可复现，避免增量守卫失败时的历史重写（生产模型 60 特征中 20 个是 `g1_*`） | 冻结邻接会牺牲「行业分类修正」的正确性 | 记录邻接指纹后，重放两次构建并断言历史段逐值一致 |
| 6 | **次新股/短历史标的产生无意义预测**（R16） | `_MIN_HISTORY_ROWS` 提到 250（与 `features.py` 文档一致），或在响应里披露 `n_features_nan` | 避免把「60 行历史 + 20 个 g1_* 全 NaN」的预测当正常结果展示 | 会让部分次新股 `/predict` 直接报 `ERR_DATA_EMPTY` → 需前端提示 | 用面板里 history < 250 行的标的（可从 hfq 分区统计）跑 `predict_symbol`，对比改动前后 |

### C-6 一处可直接量化的策略改进：IC 阈值按池宽度归一（R6 的正面改法）

- **当前问题**：`_ic_state` 用跨池宽度混合的 `hist_std` 当 σ（实测 0.1151 来自 119 只时代；
  当前池 2490 只，理论噪声 0.020）→ 1.5σ 门槛 ≈ −0.126，等于关闭衰减检测。
- **具体改法**：把逐日 RankIC 标准化为 `z_d = IC_d · sqrt(N_d − 1)`（`N_d` = 当日截面数，`compute_ic_series`
  已经输出 `n_symbols`），用 z 序列做状态机；阈值仍取 1.0/1.5σ，但 σ 现在是**可比的**。
- **预期收益**：恢复衰减检测的灵敏度（当前实际失效），并消除池扩容对基线统计的污染。
- **引入风险**：z 变换假设独立同分布，A 股截面存在行业聚集 → 阈值偏紧会误报；需先用历史 z 序列标定。
- **验证方式**：用快照里 262 个预测日的 `ic_series_tail` 重算 z 序列，比较两种口径下 09-03~09-18 的状态判定。

---

## 6. monitor.py 专项问答（简报点名问题的直接回答）

1. **漂移/衰减判定误报风险**：**存在且已发生**（R3/R4）。真实快照 `psi.max=3.3051`、`ks.n_over_crit=85/85`、
   `drift_state=degraded` 自 2026-09-03 持续；我用真实面板重算 `ma_gap_250` 的 PSI = 0.4686、`g1_ma_gap_250` = 3.3051，
   与快照一致，根因是市场中枢平移而非因子失效。
2. **触发重训阈值**：`psi.max > PSI_DEGRADED(0.25)` 对水平型因子过于灵敏（实测超阈 13 倍）；
   `RETRAIN_MIN_INTERVAL_HOURS=12`（config.py:165-166）是唯一节流。
3. **读取侧守卫是否造成长期静默阻断**：
   - `maybe_auto_retrain` 的 `status=="running" and now−started_ts < 7200` 只挡 2 小时；
     实测记录 `monitor_last_retrain` 为 `status=aborted, started 2026-09-18T19:32:53,
     finished 2026-09-19T22:36:53, aborted_reason="进程在重训期间退出（启动时回收）"` —— 即**真的发生过
     「重训线程随进程死亡、KV 停在 running」**，靠 `main.py:64-68` 的启动回收才解除（实测
     `tests/test_monitor_retrain_resilience.py::test_lifespan_invokes_reclaim_stale_retrain` 亦 guarding 该接线）。
   - **残留风险（P3，疑似）**：并发守卫是**基于时间**的（`now − started_ts < 7200`），不是**基于存活**的 ——
     项目自己在 `tests/test_monitor_retrain_resilience.py:314-315` 注明「`started_ts` 取守卫窗口内的近值——
     否则（如 24h 前）并发守卫本就不会触发」，即 `running` 记录超过 2h 后守卫**自动失效**。
     触发路径（已核清调用点）：`maybe_auto_retrain` 全仓唯一调用点是 `run_monitor`，而 `run_monitor` 只有两个入口 ——
     ① 每日 17:30 routine（`evening_routine.py:93`）；② 启动补跑，且**仅当快照缺失**才执行
     （`evening_routine.py:149-150`，快照存在时该路径不触发，故「重启即重复重训」不成立于当前实现）。
     因此重复重训需要：进程持续存活 + 重训线程仍在飞 + 次日 17:30 例行再次触发 ⇒ 守卫已过期
     → 再起一个线程，且 `kv_set`（L495）会清掉 `finished_ts` 使 12h 间隔守卫同时失效。
     实测训练本体仅约 38 秒（候选 `20260914_174827_monitor` 版本时间戳 17:48:27 vs `created_at` 09:49:05 UTC），
     故**当前大概率不会触发**；未测的是 `_compute_dataset_version()`（`monitor.py:409-428`，对 ~2490 只逐只
     `read_symbol_dataset`）在训练前的耗时。建议：把守卫从固定 7200 秒改为心跳（KV 已有 `owner_pid` 但按
     `monitor.py:453-455` 的 Windows `os.kill` 风险刻意不做存活探测 → 可用「worker 每 N 秒刷新 `heartbeat_ts`」替代）。
   - **另一处节流副作用（P3）**：`reclaim_stale_retrain` 把 `finished_ts` 设为**回收时刻**（L473），
     于是崩溃重启后会以「启动时间」为起点再节流 12 小时——语义上把「很久以前就死掉的训练」当成刚结束。
4. **`monitor.py:718 label`**：**无害残留**（R21），`kind_msg` 已完整表达状态，可直接删。

---

## 7. registry.py 专项问答（简报点名问题的直接回答）

1. **版本回滚是否安全**：**不安全且当前不可用**。① 门禁单调性使其恒被拒绝（R1，实测）；
   ② 若门禁放行则抛未捕获的 `shutil.SameFileError`（R2，实测）。
   唯一「安全」的部分是：异常发生在 DB 事务之前，注册表不会被写坏。
2. **并发读写注册表是否有竞态**：
   - `promote_model` 用 `BEGIN IMMEDIATE` + 全置 0 + 目标置 1 + 校验 `count_production == 1`，
     在单进程内是正确的（**单进程部署是既定前提**，见 P0 背景块）；
   - 产物**先复制后写库**（L350-360 → L362-376），所以不存在「注册表说有、磁盘没有」的窗口 ✓；
   - **多 worker 前提下的风险（按要求只标注，不作为当前 bug）**：`_connect()` 每次新建连接，
     若两个 worker 同时 promote，SQLite 的 `BEGIN IMMEDIATE` + `busy_timeout=30s` 会串行化到 30 秒；
     但 `dst_dir.mkdir(exist_ok=True)` + `copy2` 在事务**之外**，两个 worker 会同时覆盖同一 `prod/` 目录，
     且 `count_production != 1` 的校验是**事务提交后**才做的，无法回滚已提交的错误状态。
   - 另：`_RETRAIN_LOCK`（monitor）是进程内锁，多 worker 下自动重训可并发 —— 同属前提问题，不列为当前 bug。

---

## 8. 未发现问题（明确记录，避免沉默跳过）

- `features.py::_one_symbol_feats` 全部因子：无 `shift(-N)`、无中心化窗口、无全样本统计量；
  `test_ml_leakage` 的截断不变性 + 无跨截面 fit 状态两条断言覆盖到位。
- `labeling.build_forward_return_labels`：标签与特征严格分离；尾部结构性缺失按位置判定（避免把中间 NaN 误归为设计）；
  跨空洞用自然日阈值（`horizon*3+10` = 25 天）而非交易日计数，逻辑正确（实测生产数据命中 119 行，量级合理）。
- `train_lgbm` 的三段切分与早停：`_eval(test_mask)` 只在最终指标处使用；`X_te` 未提前构建；
  `tests/test_ml_leakage.py::test_tc_leak_early_stop_ignores_test` 用「篡改 test 段 close」证明早停不受 test 影响。
- `infer.load_prod_model` / `registry.load_prod_model`：只认 `is_production=1`，缺失时明确抛错，
  不回退实验模型；`.pt` 产物给出可读诊断 ✓。
- `predict.predict_symbol`：面板优先 + 实时回退的**列集合**已被实测证明完整（prod features.json 60 列 ⊆ 面板）；
  `close_basis`/`feature_source`/`confidence_basis` 三项口径披露齐全 ✓（唯一缺的是标签变换，见 R13）。
- `apply_propagate` 对**追加未来交易日**是 asof 稳定的（0/42 变化，本次新增验证，原无测试覆盖）。
- `data/features.resolve_feature_version` 与 `monitor._resolve_feature_dir` 的 parity 由
  `tests/test_monitor_feature_version_guard.py` 两个用例守卫，选版规则一致 ✓（差异仅在 `glob` vs `rglob`：
  `data/features.py:43` 用 `year=*.parquet`，`monitor.py:146` 用 `rglob("*.parquet")`，在同时存在其它 parquet
  的目录布局下 mtime 可能不同 → 值得顺手统一，属 P3 观察，未单列条目）。

---

## 9. 复现命令与脚本（全部在 `%TEMP%\aqp_audit_b4a\`，不触碰仓库数据）

沙箱准备（DB 与模型目录均为副本，并把注册表 `model_path` 重写到副本内，忠实复现 `src == dst`）：

```powershell
$repo='D:\Python_Project\Alpha Quant Platform'
$sand=Join-Path $env:TEMP 'aqp_audit_b4a\sandbox'
Remove-Item $sand -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $sand,"$sand\models\prod","$sand\parquet" | Out-Null
Copy-Item "$repo\data\sqlite\aqp.db" "$sand\aqp.db" -Force
Copy-Item "$repo\data\models\prod\*" "$sand\models\prod\" -Recurse -Force
$env:SQLITE_URL="sqlite+aiosqlite:///$sand/aqp.db"; $env:MODEL_ROOT="$sand\models"
$env:DATA_ROOT="$sand\parquet"; $env:PYTHONPATH="$repo\backend"; $env:PYTHONIOENCODING='utf-8'
# repro.py：R1/R2/R5 的复现（实验1/2/3）
& "$repo\backend\.venv\Scripts\python.exe" (Join-Path $sand 'repro.py')
```

真实数据探针（只读）：`probe1.py`（prod 特征覆盖 + pred 截面统计）、`probe5.py`（monitor 快照/重训记录）、
`probe7.py`（PSI 复算 = 3.3051）、`probe9.py`/`probe11.py`（`g1_*` universe 依赖 / 时间截断不变性）、
`probe8.py`（`_worst` 与 LightGBM 列序）、`probe3.py`（predictions 分区截面宽度）。

关键命令与实测输出摘要：

| 条目 | 命令 | 实测输出 |
|---|---|---|
| ruff F841 | `backend> .venv\Scripts\python.exe -m ruff check app --select F*` | 5 条，含 `monitor.py:718 label` |
| R1 | repro.py 实验2 | `evaluate_candidate -> False | valid_rank_ic 劣于生产 0.0920 < 0.1020` |
| R2 | repro.py 实验1 | `RAISED shutil.SameFileError: ... are the same file` |
| R5 | repro.py 实验3 | `20260907_195605_monitor: promote=False ... 0.1020 < 0.1020` |
| R9 | probe1.py | `prod params_json kept_features 长度 0` / `features.json 60 列` |
| R3 | probe7.py + probe5.py | `PSI(recomputed) = 3.3051`（= 快照 `psi.max`）；`ma_gap_250` 中位数 +0.0899 → −0.0610 |
| R4 | probe5.py | `ks: {"n_factors": 85, "n_over_crit": 85, "mean": 0.1161}` |
| R6 | probe3.py + probe5.py | 预测截面 118–121 只（至 08-21）vs 2484–2492 只（09-14 起）；`history.std_ic=0.1151` |
| R7 | probe8.py | `_worst(['unknown','healthy']) = 'healthy'` |
| R12 | probe9.py | `去掉 S2：40/42 个 g1_* 列在同一历史交易日发生变化` |
| R13 | probe1.py + probe5.py | `has 'xsec_demean' recorded? False`；`pred_score 09-17 mean=-0.00228 std=0.00562` |
| R18 | probe8.py | `重排列序后预测一致 = False`；lightgbm `basic.py:1090 validate_features: bool = False` |
| R14 | repro.py 输出 + DB 副本 | prod `ds_1133s…r20220104-20260904` vs 候选 `ds_1729s…r20180102-20260914` |

> 未运行任何全量 pytest（遵守简报第 2 节；父审核员正在跑全量套件）。本批结论均不依赖全量套件。