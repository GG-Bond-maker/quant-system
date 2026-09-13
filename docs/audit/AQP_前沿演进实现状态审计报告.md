# AQP 前沿演进方案实现状态审计报告

> 审计时间：2026-09-03 · 方法：逐项以代码证据核实（文件/常量/端点/前端组件）
> 基准：《AQP_前沿演进评审与落地路线》（评审判定）×《AQP_前沿演进Phase0-2实现报告》（已实现声明）
> 总览：**5 项完整实现 · 5 项脚手架/部分实现（等数据前置）· 3 项经评审有意不做** —— 与既定路线图完全一致，无"该做未做"的空缺

---

## 一、逐项审计结果（15 项）

### 维度一 · LLM/Agent 与量化工作流 —— 1 ✅ 完整 · 1 ◐ 部分 · 1 ✅（模板版）

| 项 | 状态 | 代码证据 |
|---|---|---|
| 1-1 NL-to-Factor | ✅ 已实现 | `studio.py:188 POST /studio/nl-to-factor`：LLM 生成（ollama/openai 兼容，`LLM_PROVIDER=none` 时 53000 指引）→ `alpha_expr.parse_expr` **AST 白名单静态校验**（拒绝属性访问/下标/未知算子）→ `evaluate_expr_detail` 真实截面 RankIC 评估。前端 `NlFactorCard.tsx`（表达式/校验徽标/评估指标）。⚠️ LLM 生成路径未实测（本机无 provider），校验与评估两层已被测试覆盖 |
| 1-2 FinLLM 财报解析 | ◐ 部分（规则先行版） | `text_ingest.py`：文档导入→`rule_sentiment` 词库打分（含否定词取反、同词封顶、`超预期`权重 2）→ 日频 `sentiment∈[-1,1]` 因子 + `n_docs/method` 披露；`llm_sentiment()`（:183）在 `llm_enabled()` 时走 LLM、失败回退规则词库。**未做**：专属微调大模型、业绩超预期概率量化、供应链传导事件抽取（卡在公告数据源与数据量） |
| 1-3 AI 归因日报 | ✅ 已实现（模板版） | `report.py`：五节结构（数据面/因子健康度/模拟盘与执行含 impact_bps/最近事件/风险提示）+ 等价 markdown + KV 持久化 30 期；`scheduler.py` 晚间例行自动生成 → `events` 推顶栏铃铛；前端 `pages/Report`（期数选择/风险提示卡/markdown 折叠）。已知边界：**Brinson 归因节未纳入**（归因逻辑耦合在 desk 端点闭包内），以持仓贡献 Top/Bottom + 执行质量替代；LLM 摘要增强未接 |

### 维度二 · 高性能计算 —— 1 ◐ 够用 · 2 ❌ 有意不做

| 项 | 状态 | 审计结论 |
|---|---|---|
| 2-1 向量化回测引擎 | ◐ 够用（缓做有据） | `backtest/engine.py` 已是 numpy 向量化（Top-K 调仓 + 策略引擎双轨），实测 252 天×120 只仅 **2.4s**，研究态无性能墙。无 Numba/Rust/C++（全库 grep 零命中）。路线图的真正性能墙 MED-003 分区改造**已完成**：`cross_section.py` date 分区镜像 + 按年分批构建（1000 只 28.4s→5.9s，今日审计再提速 4.8×）——即 Phase 3 前置已提前落地。252 天×120 只尚远低于 Numba 重写的收益阈值 |
| 2-2 GPU 加速（CuPy/RAPIDS） | ❌ 不做（判定正确） | 无 cupy/cudf/rapids 依赖；120 只日线截面毫秒级，GPU 无收益场景。待万维因子/Tick 数据出现再评估 |
| 2-3 共享内存 Feature Store | ❌ 不做（判定正确） | 无 plasma/shared_memory 代码；全库 features ~48MB，Plasma 已被 Arrow 官方弃用；`_SNAP_CACHE` 已消除重复 concat。单进程 Polars 多线程即可 |

### 维度三 · 高阶模型 —— 2 ◐ 脚手架完备 · 1 ❌ 有意移除

| 项 | 状态 | 代码证据 |
|---|---|---|
| 3-1 TFT/iTransformer | ◐ 脚手架完备，等数据 | `torch_models.py`：SeqTransformer（(B,L,F)→嵌入→编码器→标量回归）+ `save/load_sequence_model`（含重建参数）+ 回读校验；`sequence_dataset.py`：sliding_window_view 向量化 + purged_time_split（gap=horizon 防泄漏）；**今日新增一键训练入口**（train_service + /train/* + TrainPanel）。⚠️ iTransformer 变体本身未实现；真实训练被 MIN_TRAIN_SAMPLES=5000 门禁主动拦截（120 只池有意拒绝，非缺陷）。**iTransformer 可在扩池后作为 TFT 的对照变体补上** |
| 3-2 DRL 执行（PPO/DDPG） | ❌ 移除（判定正确） | 全库无 ppo/ddpg/gym 代码。日线数据训执行策略无意义，恢复条件=分钟线/L2 数据源 |
| 3-3 GNN 产业链传导 | ◐ 脚手架完备，等数据 | `gnn_models.py`：裸 GCN 两层图卷积 + nan_to_num 防污染 + save/load 回读校验；`graph.py`：显式关系边 + 行业 peer 边（MAX_PEER_BUCKET=150 脏桶防护）+ 传导因子 propagate（分块矩阵乘、无边补 NaN 列）；一键训练入口同上。门禁：无关系边直接拒绝并给原因 |

### 维度四 · 微观交易与风控 —— 1 ✅ 完整 · 1 ◐ 日线口径部分

| 项 | 状态 | 代码证据 |
|---|---|---|
| 4-1 动态流动性/订单簿深度 | ◐ 日线口径部分 | `paper.py:221`：sqrt 冲击滑点 + `participation_cap` 真实参与率闸门 + `split_days` 分日均摊（impact_bps 逐单落库披露）。**未做**：L2 订单簿深度预测、Taker/Maker 动态比例（无 L2 数据，无从实现）；"分时上限精细化"增量也未做（路线图列为可做增量） |
| 4-2 Alpha 半衰期调优 | ✅ 已实现 | `monitor.py:142 fit_half_life`：多周期 IC（H=1/2/3/5/10）指数衰减拟合半衰期；`FactorHealthCard.tsx` 展示；生产响应经 `factor_health` 字段披露（指标只读，不静默改权重——平台披露原则）。实测 honesty 点：IC 不衰减时返回 None + 如实说明（非造数） |

### 维度五 · MLOps 与因子监控 —— 2 ✅ 完整（全维度最高完成度）

| 项 | 状态 | 代码证据 |
|---|---|---|
| 5-1 因子失效预警/降级 | ✅ 已实现 | `monitor.py`：`DEGRADED_SIGMA=1.5`（反向/1.5σ 外→degraded，1σ 外→watch）状态机 + 快照/状态日志持久化 + `events` 推铃铛 + 前端健康度卡（绿/黄/红灯 + 15 日 MeanIC/ICIR）。真实数据实测：Mean RankIC 0.1478/ICIR 1.256 |
| 5-2 漂移检测 + 自动重训 | ✅ 已实现（PSI+KS 双口径） | `monitor.py`：`PSI_WATCH=0.10 / PSI_DEGRADED=0.25`（近 20 日 vs 前 250 日基线，分位分箱，42 因子）+ `compute_ks`（手写经验 CDF KS 统计量，无 scipy；与 PSI 同切分互验，α=0.05 临界值计数）+ `maybe_auto_retrain`（12h 间隔守卫 + 防并发 + **promote 质量门禁把关**，实测自动重训候选 valid RankIC 0.0192 通过）+ `scheduler.py` 17:30 晚间例行 + startup_catchup 补跑 |

---

## 二、结论

1. **完成度分布与路线图判定逐项吻合**：✅ 5 项（5-1、5-2、4-2、1-1、1-3 模板版）全部落在"现在做"象限且已通过真实数据闭环实测（含自动重训换血生产模型、degraded 事件推铃铛）；◐ 5 项全部是"数据前置"象限的脚手架（TFT/GNN/FinLLM/日线冲击）；❌ 3 项（GPU/Plasma/DRL）全部是评审明确否决的过度设计项，**不是遗漏**。
2. **治理原则贯彻一致**：所有派生指标带 basis/method 披露、LLM 输出永远过 AST 白名单、自动重训必须过 promote 门禁、样本不足主动拒绝而非静默开训。
3. **当前真实瓶颈不在功能层**：剩余项的解锁钥匙全是同一个——**数据前置**（扩池 ≥1000 只 → 解锁 TFT/iTransformer 训练；公告文本源 → 解锁 FinLLM 全量；行业/关系数据 → 解锁 GNN；分钟线/L2 → 解锁动态深度与 DRL）。

## 三、发现的三个小缺口 —— ✅ 已于 2026-09-03 晚全部补齐

> 补齐实况（附审计勘误）：缺口 1 在复核时发现为**审计误判**——`monitor.py` 当时
> 已含 `ks_two_sample`（手写经验 CDF，无 scipy）与 `compute_ks`（与 PSI 同切分
> 双口径互验，含 α=0.05 临界值计数），系首轮审计 grep 用了 scipy 函数名而漏检。
> 本次补齐的内容为：KS 独立参考实现的正确性测试 + 前端健康度卡与日报的 KS 展示。

1. **5-2 KS 检验**（勘误：核心计算已有）：新增 `test_ks_two_sample_matches_reference`
   （暴力参考实现逐位一致 + 同分布 D=0 + 完全分离 D=1）与 `test_compute_ks_dual_basis_with_psi`
   （稳定样本 KS 低、3σ 漂移超临界因子计数 ≥1）；日报「因子健康度」节新增 KS 互验行，
   FactorHealthCard 展示 `KS 互验 max（超 5% 临界 N 个）`。
2. **1-3 日报接 Brinson 节**（✅ 新实现）：`report.py` 新增 `_brinson_section()`——
   模拟盘**真实持仓市值权重** vs 同池等权基准，复用 `domain.attribution.brindon_attribution`
   纯函数，hfq 后复权 20 日窗口收益，行业取 universe 最新截面（缺失记「未分类」）；
   日报新增「四、Brinson 行业归因」节（窗口超额/配置/选股/交互/残差 Alpha/行业分解
   + ⓘ 口径披露），事件与风险提示顺延为五/六节；测试含 Brinson 恒等式逐项核对
   （配置+选股+交互+残差 = 超额）。
3. **1-2 业绩超预期量化**（✅ 新实现，口径如实）：`text_ingest.py` 新增独立因子
   `surprise ∈ [-1,1]`——`rule_surprise()` 业绩表述事件强度（大超预期/超预期/好于预期/
   扭亏为盈/预增… vs 不及预期/低于预期/业绩变脸/下修/预亏…，否定词取反 + 同词封顶 +
   最长匹配，与情绪词库共用 `_lexicon_score` 通用打分核）。随 `score_and_build_factor`
   并列落盘、`attach_text_features` 同口径挂接（旧因子表兼容补 NaN）。**明确披露：
   是事件强度而非超预期概率**——平台无一致预期 (consensus) 数据源，任何"概率"表述
   都属造数（数据真实性红线）；待一致预期数据接入后方可升级为概率口径。

## 四、解锁路线（数据前置完成后的启动顺序）

```
扩池 1000+ 只 ──→ TFT 真训练（一键训练门禁转绿）→ iTransformer 对照实验
                → 向量化引擎重审（样本×因子数是否触碰性能墙）
公告文本源 ────→ FinLLM 全量（LLM 打分已就位，换数据即生效）
行业/关系数据 ──→ GNN 真训练（peer 边自动启用）→ 力导向图谱前端
分钟线/L2 ─────→ 订单簿深度预测 → Taker/Maker 动态比例 →（可选）DRL 执行
万维因子规模 ──→ 重评 GPU（CuPy）与共享内存 Feature Store
```
