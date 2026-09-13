# AQP 前沿演进方案评审与落地路线

> 评审时间：2026-09-03
> 评审对象：用户提出的 5 维度前沿演进方案（LLM/Agent、高性能计算、高阶模型、微观风控、MLOps）及 CPU 适配实现设计
> 评审基准：当前仓库真实状态 + 本机实测结果（120 只股票池 / 纯日线 / 无 L2/Tick / CPU·32GB 单机）

---

## 一、总体判定

方案方向符合机构演进规律，CPU 适配意识正确；但**五个维度的前提数据成熟度差异极大**，按当前平台实际直接铺开会形成大量空转工程。核心校准依据（均为历史审计与本轮复审实测确认过的事实）：

| 平台现实 | 对方案的约束 |
|---|---|
| 股票池 120 只（600519 被隔离），日线 2022-01~2026-08 | 深度学习（TFT/GNN/DRL）样本量严重不足；IC 估计置信区间宽 |
| 无 L2/Tick/订单簿数据，announcements 为占位分区 | 动态深度滑点、OFI、DRL 执行、FinLLM 均无数据前提 |
| industry 字段 86% 为空 | GNN 产业链连行业边都建不起来 |
| 因子解释力弱（best_iteration=1~2），但 registry/promote 治理已闭环 | 换模型类别不是瓶颈，扩数据 + 监控闭环才是 |
| 单机 Windows + SQLite + Parquet，32GB 内存 | Celery/Ray/Plasma 类分布式组件属过度设计 |
| 网络时好时坏（akshare/eastmoney 断连实测频发） | 一切依赖外部 LLM API 的功能必须有离线降级 |
| P2-15 刚落成 events.py 通知总线 + SSE 铃铛 | 因子告警/漂移告警有现成推送通道，零新增成本 |

**结论：五（MLOps 监控）→ 一（LLM 薄层）→ 二（局部性能）→ 其余在数据前置完成后启动。**

---

## 二、逐项判定与理由

### 5-1 因子失效预警 + 自动降级 —— ✅ 现在做（优先级最高）

- 前提全部就绪：IC 计算链路（`research.py` factor-icir）、registry 质量门槛（promote 门禁）、`app_state` KV（P2-14）、**告警通道 `core/events.py`（直接 `events.publish("monitor", ...)` → 顶栏铃铛）**。
- 落地设计：
  - 新增 `app/ml/monitor.py`：每日盘后计算近 N 日滚动 RankIC / Cumulative IC / 因子自相关半衰期（4-2 与本项共用同一服务）；
  - 健康度写 `app_state`（复用 kv.py），状态机 `healthy → watch → degraded`；
  - 规则：连续 15 日 RankIC < 均值−1.5σ 或出现反向 → 置 `degraded`，推事件 + 写日志；
  - 「降权」在当前单因子模型下的正确实现是：screener/推理前检查生产因子健康度，degraded 时在响应中带 `factor_health` 披露字段（而非静默改权重——平台一贯的披露原则）。
- 前端：数据质量页或因子工作室加「因子健康度」卡（绿/黄/红），复用既有 SectionCard 模式。

### 5-2 概念漂移检测 + 自动重训 —— ✅ 现在做（与 Phase 0 合并）

- PSI/KS 用 Polars 计算是几十行的事：当日特征截面 vs 训练基线（registry 记录的训练区间）分桶对比，PSI 阈值 0.1/0.25 标准线。
- 自动重训不引入 Celery：**复用 `scripts/retrain.py` + registry promote 门禁**（质量门槛已实现，防止漂移期训出坏模型自动上线），由 APScheduler 触发。
- 关键前提是 Phase 0 的调度入口——这正是《修复阶段最终报告》§11 的遗留项（`build_universe` 未进 STEPS、无调度），本方案恰好把它补上。

### 1-1 NL-to-Factor —— ✅ 薄层可做

- 锚点正确：方案提出的「AST 白名单校验」必须保留。现有 `alpha_expr.py` 表达式引擎 + `POST /studio/alpha-eval` 校验端点就是验证层，LLM 只产出 DSL 文本，**永远不让 LLM 输出直接变成可执行代码**。
- 配置走 .env（与 USD_CNY_RATE 同模式）：`LLM_PROVIDER=ollama|openai|none`、`LLM_BASE_URL`、`LLM_MODEL`；`none` 时前端隐藏入口，不硬依赖。
- 前端卡片化展示（表达式/校验状态/一键送入挖掘）与现有 FactorStudio 风格一致。
- 交互流：自然语言 → LLM（带算子 Schema 的 system prompt）→ 表达式 → alpha-eval 校验（真实数据回测 RankIC）→ 结果卡。失败时展示校验错误而非静默重试。

### 1-3 AI 归因日报 —— ✅ 可做，但先做无 LLM 模板版

- 数据齐备：Brinson 归因（desk/attribution）、执行滑点（paper fills 的 impact_bps/basis_bps）、kill-switch/风控事件日志、因子健康度（5-1 产出）。
- **先做确定性模板日报**（纯后端拼装 → Markdown → 存库 → events 推送），原因：本环境外网不稳定是实测事实，日报这种「每日必须出」的东西不能单点依赖 LLM API。LLM 摘要作为可选增强层（provider 可配）。
- 前端：首页顶部「AI 日报」面板，展示 Markdown，异常段落可展开（方案设计合理，照做）。

### 1-2 FinLLM 财报解析 —— ⏸ 缓做（数据前置）

平台无任何文本数据源：announcements 是单占位分区、无抓取管线、无研报源。启动顺序应为：公告文本抓取管线（datacenter 新 dataset）→ 清洗分块 → 情绪/超预期抽取（先规则+词典，LLM 后置）→ 写 features 新分区。在抓数管线存在之前做这一项是空转。

### 2-1 向量化回测 —— ⏸ 缓做（当前非瓶颈）

实测 252 天 × 120 只策略回测仅 2.4s（本轮复审），研究态性能不缺。**真正该先做的是审计 MED-003：`symbol=X/year=Y` 分区在 5000 只时变成 25,000 个小文件、截面查询全量打开**——这才是扩池路上的性能墙。顺序：扩池 → 分区改造（date 分区镜像或宽表）→ 此时再谈 Numba/Polars 向量化引擎，投入才有杠杆。
方案中「统一 Strategy 接口（矩阵信号与事件驱动共享因子输入）」的设计是好的，保留到 Phase 3。

### 2-2 共享内存 Feature Store —— ❌ 现在不做（过度设计）

全库 features ~48MB；P2-12 的 `_SNAP_CACHE`（签名缓存）已消除重复 concat。Plasma 已被 Arrow 官方弃用归档；单进程 Polars 本身多线程，32GB 内存下跨进程共享内存没有收益场景。待扩池到 GB 级特征再评估（届时直接用 Arrow IPC / `multiprocessing.shared_memory`，不用 Plasma）。

### 3-1 TFT/iTransformer —— ⏸ 缓做（样本前提）

120 只 × 4.6 年的样本下，时序深模型几乎必然过拟合，CPU 上训练还慢；历史对照实验已证明「特征对 5 日收益解释力弱」是瓶颈（best_iteration 恒为 1），换模型类别不解决它。正确顺序：扩池 1000+ → 重验线性/LGBM 基线 → 再引入 TFT 做 A/B（PyTorch CPU + `torch.set_num_threads(effective_cpu_threads())` 与平台线程统一配置衔接）。

### 3-2 GNN 产业链 —— ⏸ 缓做（数据前置）

无供应链/股权关系数据源，instrument 表 industry 86% 为空，图连边都建不起来。前置：行业数据补齐（数据源修复清单里已有此项）→ 关系数据抓取 → 才是 PyG 建模。前端力导向图谱的设计可保留到那时实现。

### 4-1 动态订单簿深度 —— ⏸ 缓做；日线口径内可做增量

无 L2/Tick，OFI/深度预测无从谈起。**可做的增量**：paper.py 当前已按「当日成交额 × participation_cap」计提 sqrt 冲击（真实参与率闸门），可在其上做「按 split_days 分日均摊参与率 + 分时上限」的精细化，成本图披露拐点。L2 版本等数据源。

### 4-2 Alpha 半衰期调优 —— ✅ 现在做（并入 5-1 服务）

纯衍生计算：滚动 RankIC 自相关 → 指数拟合半衰期 → 健康度看板新指标 + screener 响应披露。与 5-1 共用 monitor.py 与前端卡片。

### DRL 执行算法（PPO/DDPG）—— ❌ 从路线图移除

执行算法需要分钟级/Tick 级撮合环境训练，日线数据训出的执行策略无意义。恢复条件：接入分钟线或 L2 数据源之后。

---

## 三、建议路线图

### Phase 0 · 前置（1~2 天）
1. `pipeline.STEPS` 补 `build_universe`（修复报告遗留项）；
2. APScheduler 进程内调度：每日收盘后 pipeline → monitor → 重训判定，任务记录复用 `data_jobs` 表；
3. `.env` 增加 LLM provider 配置骨架（本阶段只留空位）。

### Phase 1 · MLOps 监控闭环（维度五 + 4-2，最高性价比）
- 后端：`app/ml/monitor.py`（滚动 IC/半衰期/PSI/KS + 状态机 + 告警事件）；
- 告警走 `events.publish` → 顶栏铃铛（P2-15 零新增成本）；
- 自动重训：漂移超标 → 触发 `retrain.py` → registry promote 门禁把关；
- 前端：数据质量页新增「因子健康度」卡（状态灯 + 半衰期 + PSI 柱图）。

### Phase 2 · LLM 薄层（维度一的 1/3）
- NL-to-Factor（AST 白名单 + alpha-eval 校验闭环）；
- 模板版 AI 日报 → 可选 LLM 摘要增强；
- 两项均要求 provider 离线时优雅降级（日报必须每日可出）。

### Phase 3+ · 数据前置后启动
- 扩池抓数（1000+ 只）→ 分区改造（MED-003）→ 向量化研究引擎；
- 公告文本管线 → FinLLM；行业/关系数据 → GNN；
- 分钟线/L2 → 动态深度滑点 →（可选）DRL 执行；
- TFT 对比实验。

---

## 四、数据流拓扑修正（在原方案基础上叠加现有组件）

```
[行情/文本(待建)/L2(待建) 原始数据]
        │
        ▼
【Data & Feature Pipeline (Polars)】◄── NL-to-Factor（AST 白名单 + alpha-eval）
   │  STEPS 补 build_universe；APScheduler 调度（Phase 0）
        ├───► 【ml/monitor.py：滚动IC/半衰期/PSI/KS】──告警──► events.publish → SSE 铃铛（P2-15 复用）
        │              │ degraded / PSI>0.25
        │              ▼
        │      【retrain.py + registry promote 门禁（已有，防坏模型上线）】
        ▼
【LightGBM（现状主力）；TFT/GNN 为扩池后的 A/B 候选】
        │
        ▼
【screener / paper desk（参与率冲击闸门已内置；L2 动态化待数据）】
        │
        ▼
【AI 日报：模板拼装（必出）→ LLM 摘要（可选增强）】
```

## 五、与原方案的三点关键分歧（备忘）

1. **不引入 Plasma / Celery / Ray**：单机 SQLite+Polars 现状下属过度设计；执行层用现有 threading + data_jobs + app_state，调度用 APScheduler。
2. **LLM 功能全部「可有可无」化**：provider 可配、可关、可降级——平台网络环境不稳定是实测事实，模板日报/规则抽取先打底。
3. **监控先行于建模升级**：当前瓶颈是「特征解释力 + 无调度无监控」，不是模型类别；先把维度五建成，扩池后的一切新模型才有裁判。
