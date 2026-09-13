# AQP 数据前置审计修复报告

> 审计时间：2026-09-03
> 对象：《AQP_数据前置实现报告》所交付的三条线（Track A 截面镜像 / Track B 公告文本管线 / Track C TFT·GNN 训练前置）
> 方式：逐文件代码审查 + 真实数据差分基准 + 单元回归验证
> 结果：**P0 4 项 · P1 5 项 · P2 7 项**，全部修复；新增 8 例回归测试（8 → 16 例，含 torch 用例自动 skip）

---

## 一、P0：会崩溃或产生错误结果的缺陷

### P0-1 `scripts/train_gnn.py` 训练完成后必崩 ⚠️（最严重）
- **问题**：第 100 行 `from app.ml.torch_models import predict_gnn`——`predict_gnn` 实际定义在
  `app/ml/gnn_models.py`。脚本在**跑完 40 轮训练、存完模型之后**才触发 ImportError，
  候选登记（`register_candidate`）根本不会执行：整次训练白跑。
- **为何测试没拦**：torch 是可选依赖且未安装，CLI 入口没有覆盖。
- **修复**：导入来源改回 `gnn_models`；新增 `test_cli_script_imports_resolvable`
  （AST 解析两个 CLI 脚本里的每个 `from X import Y` 并逐一验证可解析），把这类
  "只在真跑时才暴露"的错误提前到收集阶段拦截。

### P0-2 `gnn_models.train_gnn` 特征 NaN 污染 → 静默废模型
- **问题**：`torch.tensor(X)` 直接吃原始特征矩阵。features 早期窗口（rolling 20 日等）
  普遍含 NaN，任一 NaN 进入 `A·X·W` 都会让前向/反向全变 NaN——训练日志里的
  loss 也是 NaN 但没人看，产物是一个权重全 NaN 的模型。
- **修复**：特征显式 `nan_to_num(0)`；标签掩码沿用；首轮 history 记录
  `nan_feature_ratio` / `nan_label_ratio` 供复核；`mask.sum()==0` 时显式报错而非除零产 NaN。
- **测试**：`test_gnn_train_nan_features_do_not_poison_weights`（含 NaN 特征训练后断言权重有限）。

### P0-3 `graph.propagate` 无关系边时静默返回原表
- **问题**：`A.sum()==0` 时直接 `return features`，不加 `g{h}_{col}` 列。下游
  （GNN 特征拼装 / LGBM 特征表）按列名取值会在 KeyError 与正常之间摇摆，
  与模块宣称的"透明降级"矛盾。
- **修复**：无关系数据时补全 `g{h}_{col}` 全 NaN 列（NaN 明确表达"无覆盖"，不造数）。
- **测试**：`test_propagate_without_edges_adds_nan_columns`。

### P0-4 `train_gnn.py` 标签 shift 依赖行序 → 潜在前视泄漏
- **问题**：`groupby("symbol")["close"].shift(-horizon)` 的正确性完全依赖组内行序，
  代码未显式排序。features 分区文件若非 (date, symbol) 升序，标签会**静默错位**，
  造成前视泄漏——这类错误不会报错，只会让回测结果虚高，危害大于崩溃。
- **修复**：显式 `sort_values(["symbol","date"])` + `(symbol,date)` 去重后再 shift。

---

## 二、P1：规模性能（与"扩池到 5000 只"的核心主张直接冲突）

### P1-1 `cross_section.build_mirror` 内层 O(文件数 × 每年交易日) filter 循环
实测（真实写入路径，250 交易日单年分区）：

| 规模 | 原实现 | 修复后 | 提速 |
|---|---|---|---|
| 121 只 | 7.16s | 4.31s | 1.7× |
| 1000 只 | **28.41s** | **5.87s** | **4.8×** |
| （纯计算段 1000 只） | 21.38s | 1.81s | **11.8×** |

外推 5000 只 ×5 年 ≈ 10+ 分钟/数据集（×3 口径）→ 修复后进入分钟级以内。
- **修复**：每文件**一次** `is_in` 过滤 + `partition_by("date")` 原生分派，替代
  "每个日期各 `filter` 一次"的 DataFrame 反复物化；**按年分批**控制内存上界
  （单年全截面而非全历史）；利用扫描得到的 file→dates 映射**只全量读取含
  待建日期的文件**（日常增量不必重读全部源分区）。
- **防回归**：性能纪律写进 docstring，附实测数字，防止将来改回去。

### P1-2 `mirror_status` 新增孤儿日期披露
源分区被删除/重建后镜像不会自动清理，但原状态接口对此不可见。
新增 `orphan_dates`（镜像有而源没有的日期数），不为 0 即提示手动全量重建。
（扫描成本实测很便宜：1000 文件 0.97s，无需引入 manifest 缓存。）

### P1-3 `graph.propagate` 逐日 Python 三重循环
- **问题**：逐日 × 逐符号 × 逐列的 Python 字典查找循环，5000 只 ×1131 日 ×20 因子
  是千万级 Python 迭代。
- **修复**：面板转 (N, C×F) 后**分块矩阵乘**（一次 BLAS 调用完成邻域聚合），
  块大小按 节点×因子 自适应（≈2e7 元素上界）。分母仍按"有值邻居"归一，
  无值邻居不稀释信号、孤立节点保持 NaN——语义逐位保留。
- **验证**：与逐日循环参考实现差分对比（多日、多列、NaN、孤立节点、hops=1/2）数值完全一致。

### P1-4 `build_sequences` 逐样本 Python 循环
- **修复**：`sliding_window_view` 滑窗一次成型 + 布尔掩码批量剔除。
- **验证**：与原实现**逐位等价**（X/y/dates/symbols 全一致，含 NaN 注入与
  `max_rows_per_symbol` 分支）；121 只 0.19→0.07s、600 只 0.93→0.34s（2.5–2.8×）。

### P1-5 `purged_time_split` 逐样本集合查找
- **修复**：先做"日期→交易日序号"映射，每段切分退化为一次 O(N) 比较；
  重复/乱序日期输入下与原实现输出完全一致。

---

## 三、P2：口径披露、一致性、健壮性

### P2-1 情绪词库重叠词条重复计数 + 否定词缺失
实测（修复前 → 修复后）：

| 文本 | 修复前 | 修复后 | 说明 |
|---|---|---|---|
| 业绩**增长至** 20% | +0.5828 | **+0.3215** | "增长"/"增长至"两条重叠词条各计一次 → 2 倍权重 |
| **解除质押** | -0.3215 | **+0.3215** | 实为利好事件，被"质押"词条误判为利空 |
| **未减持** | -0.4621 | **+0.4621** | 否定语义完全丢失 |
| **不予立案** | — | +0.5828 | 同上 |

- **修复**：词条按长度降序做正则有序交替（最长匹配优先、不重复计重叠词条）；
  情绪词前 3 字内出现否定词（未/不/无/没有/解除/撤销/终止/不予…）→ 权重取反；
  同一词条封顶计 3 次。**口径写进 docstring**：tanh 归一是尺度假设而非概率，仅供排序类因子使用。

### P2-2 `attach_text_features` 文档与实现不符 + 口径未披露
- 模块注释与测试 docstring 都写着 `merge_asof allow_exact_matches=False`，**代码并未设置**——
  实际是靠因子表日期整体位移实现 T+1。若后来者照 docstring 补上该参数，可用日会被推到 T+2。
- `tolerance=30 天`的"过期即 NaN"是主动口径（事件型信号不前向填充），原实现零披露，
  违反平台"派生指标必须披露口径"红线。
- **修复**：docstring 如实描述可用日语义并明确"勿叠加 allow_exact_matches=False"；
  `tolerance` 提为参数 `stale_days=30` 并写明理由；`text_status` 新增 `basis` 字段。

### P2-3 模型产物无法加载（TFT/GNN 都受影响）
- **问题**：`torch.save(model.state_dict())` 只存权重，而 `predict_sequence` 需要模型对象，
  平台也没有 `load_*` 配套函数——promote 之后磁盘上的 `model.pt` 是一个**打不开的权重包**，
  生产推理链路实际断裂。
- **修复**：`save_sequence_model` / `load_sequence_model`（含 n_features/lookback/d_model 重建参数）、
  `save_gnn` / `load_gnn`（含 n_features/hidden）；两个 CLI 存盘后**立即回读校验**，
  load 失败就不登记候选；metrics.json 补齐 `cols`/`symbols`/`hops`/`horizon` 等推理所需元信息。
- **测试**：`test_sequence_model_save_load_roundtrip`（torch 未装自动 skip）。

### P2-4 模型列表把深度候选拍成"无产物"
- `research.py` 的 `has_model_file` 硬编码 `(mdir / "model.lgbm").exists()`，TFT/GNN 候选
  在建模中心永远显示缺产物。
- **修复**：SELECT 补 `model_path`，以登记时路径为准（TFT/GNN=model.pt），旧行退回目录约定。

### P2-5 `graph.industry_edges` 同业全连接边数 O(n²) 爆炸防护
单一脏桶（如全部归入 "-"）在 5000 只池下能产出上千万条边、把邻接矩阵灌成稠密图，
且这种"边"不再表达产业链信息。新增 `MAX_PEER_BUCKET=150` 上限，超限桶跳过并告警。

### P2-6 已知 flake 修复：`test_p2_ml` 精确计数断言
- **问题**：`assert n == 81` 对共享测试库做全量精确计数，同会话其它用例先写入
  `grid_%` 行就会失败（顺序敏感，单跑必过）——即报告遗留项 3。
- **修复**：改为断言"本次运行**净增** 81 行"，精确性保留、对执行顺序免疫。

### P2-7 其它健壮性
- `torch_models.build_model` 的 `import torch` 移到函数首行（原写在类定义之后，能跑但脆弱）；
  pos 编码长度约束（输入序列 ≤ lookback）写进 docstring；
- `train_gnn.py` 重复导入清理（`train_gnn` 导入两次、`_train` 别名）；补
  `torch.set_num_threads(effective_cpu_threads())` 线程纪律（与 torch_models 对齐）；
- `gnn_models` 训练/推理均显式 `nan_to_num`。

---

## 四、验证记录

| 项 | 结果 |
|---|---|
| `tests/test_data_prep.py` | **16 例**（原 8 例 + 新增 8 例）14 passed + 2 skipped（torch 用例，torch 未安装自动 skip） |
| **全量 pytest（修复后）** | **313 passed / 0 failed / 7 skipped**（12m45s；修复前为 306 passed + 1 failed，唯一失败即 P2-6 的顺序敏感 flake，已根除） |
| 前端 tsc | 0 错（`basis` 口径披露接入文本数据卡） |
| 差分验证 | build_sequences / purged_time_split / propagate 均与原实现**数值逐位一致**（含 NaN、孤立节点、hops=2、max_rows 分支） |
| 规模基准 | 镜像构建 1000 只×250日 28.4s→5.9s；序列构建 2.5–2.8×；图传播由 Python 三重循环改为分块 BLAS |
| 导入检查 | 全部触及模块 import 无错；两个 CLI 脚本 `ast.parse` 通过 |

**新增测试清单**（防回归锚点）：
镜像孤儿日期披露 · 无边补 NaN 列 · 缺失邻居不稀释 · 分块传播=参考实现 ·
脏行业桶跳过 · CLI 脚本导入可解析（parametrize 双脚本）· GNN NaN 特征不污染权重 ·
序列模型存取往返

---

## 五、遗留与建议（更新后）

1. ~~test_p2_ml 精确计数断言加固~~ —— **本轮已修**（改为净增计数）；
2. torch 相关用例在安装 torch 后会自动启用（skip → 运行），建议扩池装 torch 时先跑
   `pytest tests/test_data_prep.py` 确认 4 个 torch 用例全绿再开始训练；
3. **报告路径写法**：《数据前置实现报告》把 `scripts/train_tft.py`、`tests/test_data_prep.py`
   写在仓库根，实际都在 `backend/` 下（根目录另有同名 `scripts/`，易误读）；
4. 报告中"扩池到 5000 后镜像仍保持 O(1)"指的是**读取**；本轮修复的是**构建**侧的
   O(文件×日期) 隐患——两条主张现在都成立；
5. KS 漂移检验仍待 scipy（未动）。
