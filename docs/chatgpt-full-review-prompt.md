# AQP 全栈审核 · ChatGPT 提示词包

> 用途：把 Alpha Quant Platform（后端 FastAPI / 前端 React+Vite）交给 ChatGPT 做**三件事**：
> ① 找出前后端各功能的真实 Bug；② 找出死代码；③ 评估量化策略合理性并给出优化方向。
>
> 用法：**按「P0 背景块 + P1 主提示词」开一轮对话，然后按第 3 节的批次清单逐批投喂代码**。每批都在**同一条对话**里追加，让 ChatGPT 累积上下文。

---

## 0. 先读这里：为什么必须分批

| 项 | 实测规模 |
|---|---|
| 后端 Python（`backend/app`） | 约 **3.0 万行** |
| 前端 TS/TSX（`frontend/src`） | 93 个文件 / 约 **1.9 万行** |
| 测试 | 101 个 `test_*.py` |

全仓代码约 5 万行 ≈ 60–80 万 tokens，**任何模型都无法一次吃下**。所以：

- **不要**把整个仓库压缩包丢给 ChatGPT 就一句「帮我找 bug」——它会抽样阅读后给你一堆泛泛之谈（"建议加日志"、"建议处理异常"），信噪比极低。
- **要**按模块分批，每批控制在 **2000–3500 行**，并要求它**逐文件、逐函数**给出结论。
- 分批投喂时，**每批都重复贴一次「P0 背景块」**。否则它会忘记项目契约，把有意设计误报成 bug。

### 三种投喂方式

1. **ChatGPT Projects（推荐）**：新建 Project，把「P0 背景块」写进 Project Instructions，再把 `docs/audit-2026-09-17/` 下已有审核报告上传到 Project Files（让它知道哪些问题已知、别重复报），然后按批上传代码文件。
2. **逐批粘贴**：适合单文件深审。一条对话审一个批次。
3. **本地先跑静态扫描，把输出喂给 ChatGPT 做研判**（第 5 节给了命令）。这比让它"读代码猜死代码"准确一个量级。

### 重要前提：ChatGPT 是静态审核

它**不能运行你的项目**，不能连数据库、不能跑 AKShare。

- 它给的一切结论都是**基于文本的推理**，可能有幻觉。
- 因此必须强制它输出「**最小验证方法**」——一段你本地能跑的 5 行脚本 / 一条 curl，用来证实或证伪它的判断。
- 一旦它出现「我运行了测试，结果显示…」之类的措辞，**直接作废该条结论**——那是编的。

---

## 1. 【P0 背景块】—— 每次开新对话都完整贴一遍

```text
【项目背景 · 请先完整读取并在本次会话中始终遵守】

项目：Alpha Quant Platform（AQP），A 股量化研究平台。教学研究工具，非投资建议。
技术栈：后端 FastAPI + SQLAlchemy 2 async + SQLite(WAL) + Redis(可选) + Polars/Parquet + LightGBM；
       前端 React 18 + Vite + TypeScript + Tailwind + ECharts + Lightweight Charts。
规模：后端约 3 万行 Python；前端 93 个文件约 1.9 万行 TS/TSX；后端 101 个测试文件。
硬件前提：CPU-only 单机（Dockerfile 显式 --workers 1），单进程部署。

【一、必须遵守的契约（违反=缺陷，符合=<不要报>）】
1. 所有 API 响应统一信封：{"code": int, "message": str, "data": ..., "trace_id": str, "ts": ...}
2. HTTP 状态码恒为 200；业务错误通过信封里的非零 code 表达。
   → 裸 500 属于缺陷（兜底中间件应把未捕获异常包成业务码，panic 兜底码 50001 = ERR_PANIC_CONTAINED）。
3. 字段名契约：选股列表的分数列叫 score；选股三态在 data.status，取值 ok / degraded / unavailable。
   空榜的终态必须是 unavailable（不能是 ok 空数组）。
4. 鉴权：读端点最低 require_role("viewer")；后端用 JWT；ADMIN_TOKEN 仅用于运维直连，不是前端登录凭据。
5. 派生指标必须披露口径（字段 basis 或 kind:"platform" 标注），不允许把合成/平台自算数据冒充真实行情。
6. 单位/口径标注：涨幅用小数还是百分比、复权口径（none/qfq/hfq）必须在响应或文档中可辨。

【二、属于「有意设计」的东西（不要当成 bug 报）】
1. Redis 不可用时自动降级为进程内 LRU 缓存，/health 返回 redis: degraded —— 这是设计，不是缺陷。
2. 行情主源东方财富失败时自动降级新浪源 —— 这是设计。外部数据源独立降级、不影响接口返回 200。
3. 多处使用 try/except 包住外部调用并降级返回空数据 —— 属于设计，但请重点检查：
   降级路径是否**静默吞掉了本应上报的错误**、是否产生**看似正常实则为空的数据**（这类才算缺陷）。
4. 启动时无条件对账回收历史残留任务（reclaim_stale_retrain / reap_stale_running_tasks）—— 这是设计。
   注意其前提是单实例部署；请评估「若将来多 worker」的风险，但不要把它当成当前 bug。
5. bse（北交所）板块池为 0 —— 是需求，不是缺陷。

【三、已知问题台账（这些已被记录/已裁决，不要重复报告）】
- manifest 口径与磁盘实际不一致（universe_daily / daily_bar_qfq / daily_bar）—— 已登记待裁决。
- app/ml/gp_miner.py 约 470 行附近缺 except BaseException，panic 穿出时会把 RUNNING 状态落盘 —— 已知。
- backtest 存在两套回测实现（历史遗留），特征有 v1/v2 两代口径 —— 已知，但请评估是否应统一、如何统一。
- 已有审核报告见 docs/audit-2026-09-15 / docs/audit-2026-09-17。

【四、审核范围与硬性输出要求】
1. 只报告**能指出具体文件 + 具体函数（行号或函数名）+ 具体触发条件**的问题。
   无法定位到代码的"建议"一律不要输出。
2. 严禁输出以下低价值内容：加日志、加注释、加类型标注、格式化、命名风格、文档字符串缺失。
   除非该处缺失直接导致了可复现的错误行为。
3. 每条问题必须标注：严重度（P0 阻断 / P1 功能错误 / P2 边界或性能 / P3 优化）、类型（Bug / 死代码 / 策略 / 一致性）、置信度（确定/疑似）。
4. 每条问题必须给出「最小验证方法」：一段可本地运行的脚本、一条 curl，或一个可断言的单测思路。
5. 严禁声称你运行过代码、跑过测试、访问过数据库。你只能做静态阅读推理。
   如果你不确定，请写「疑似，需验证」并说明需要什么信息才能确认。
6. 若某文件你读完了没发现问题，请明确写「该文件未发现 P0–P2 问题」，不要沉默跳过。
—— 收到请回复「背景已加载」，然后等待我投喂代码批次。
```

---

## 2. 【P1 主提示词】—— 投喂第一批代码时贴上

```text
你是由 4 名资深工程师组成的代码审核团，同时具备以下视角，每发现一个问题都要标明是哪位成员提出的：

- 【后端工程师｜陈稳】专长 FastAPI/异步/SQLAlchemy/并发与状态机。关注：竞态条件、async 阻塞、
  事务边界、连接池耗尽、任务状态非法流转、幂等性、时区与时间戳混用。
- 【量化研究员｜苏砚】专长 A 股交易规则与因子/回测。关注：未来函数、幸存者偏差、前视泄漏、
  复权口径一致性、涨跌停/停牌/T+1/整手/费用建模是否正确、样本外有效性、IC 与换手、成本假设是否现实。
- 【前端工程师｜林知】专长 React/TS/数据可视化。关注：竞态（快速切换请求导致旧响应覆盖新响应）、
  useEffect 依赖与清理、内存泄漏、空值访问、超时与重试、错误态与加载态缺失、
  与后端契约不一致（字段名/枚举值/角色要求）。
- 【测试与架构｜柯见】关注：死代码、多实现并存、未接线功能（写了但没人调）、
  测试覆盖盲区、模块反向依赖、配置项声明了但从未读取。

【本次审核的三个目标，请分别作答，不要混在一起】
目标 A — Bug：会导致错误结果、崩溃、数据损坏、权限绕过、或用户可见错误的问题。
目标 B — 死代码：定义了但无调用方的函数/类/模块/常量/配置项；被后续实现取代的旧路径；
         永远不会成立的判断分支；前端组件已无路由引用。
目标 C — 策略合理性：选股/因子的经济逻辑是否成立、回测假设是否与现实相符、
         参数是否过拟合、是否有更强的替代方案。这一项要给出「优化建议 + 预期收益 + 风险」。

【审核纪律】
- 逐文件推进。每读完一个文件，先写一行「文件名 — 结论」，再展开细节。
- 禁止把「看起来可能有问题」写成结论。要么给出触发条件，要么标注「疑似，需验证」。
- 你无法运行代码。任何涉及运行结果的陈述都是编造，请勿出现。
- 宁少报、勿滥报。3 条定位精确的真问题，好过 30 条泛泛之谈。

现在开始，请先审阅我投喂的这批文件。
```

---

## 3. 分批投喂清单

> 按依赖顺序排列，从底向上。每批独立开一轮（同一条对话内继续追加）。
> 路径均为相对 `D:\Python_Project\Alpha Quant Platform\`。

### B0 · 骨架与契约（约 1200 行）—— 必投，后续批次都靠它建立全局认识

```
README.md
overview.md
backend/app/main.py
backend/app/orchestrator.py
backend/app/core/config.py
backend/app/core/errors.py
backend/app/core/trace.py
backend/app/api/v1/router.py
```
**本批重点**：响应信封是否在全局一致；异常兜底与 panic 兜底是否覆盖所有路径；trace_id 是否贯穿；路由注册是否有重复/冲突/遗漏；启动 lifespan 里的各项初始化的失败是否会拖垮整个进程。

---

### B1 · 基础设施层（约 3100 行）

```
backend/app/core/*.py        （除 config/errors/trace，已投）
backend/app/db/*.py
backend/app/cache/*.py
```
**本批重点**
- `compute_guard.py` / `pipeline_lock.py`：锁的作用域是进程内还是跨进程？在 `--workers 1` 下成立，但**代码里有没有依赖它做数据一致性保护**？若有，是否有注释/断言说明这个前提？
- `panic_guard.py`：能否真正捕获 `BaseException`（`asyncio.CancelledError`、`KeyboardInterrupt`、`SystemExit`）？捕获后是否破坏了优雅退出？
- `resilience.py`：重试是否有退避与上限？是否可能无限重试？超时是否存在默认无上限的情况？
- `cache/redis_client.py` + `swr.py`：熔断状态机是否有半开探测？降级到 LRU 后，**两种后端的 key 与序列化格式是否兼容**？
- `db/session.py`：连接是否设了 `timeout`？session 是否在所有分支都被关闭？WAL 下是否有长事务风险？
- `db/kv.py`：SQLite 的 `SQLITE_PATH` 与主库是否分离？两处写入是否可能互相锁死？
- 逐个检查是否有**声明了但从未被读取**的配置项（死配置）。

---

### B2 · 领域纯函数层（约 2570 行）

```
backend/app/domain/*.py
```
**本批重点（量化视角，逐条核对）**
- `limit.py`：涨跌停幅度是否按板块正确区分（主板 10% / 创业板科创板 20% / ST 5% / 北交所 30%）？是否处理了**新股上市首日**、**复牌**等特殊情形？四舍五入是否与交易所一致（A 股是四舍五入到分）？
- `adjust.py`：前复权/后复权公式是否正确？**除权除息日当日的处理**是否正确？前复权是否会因为基准日变化导致历史值变动（asof 稳定性）？
- `indicators.py`：MACD/RSI/BOLL 的**初始化窗口**是否正确？是否有前视（例如用中心化窗口计算）？空值/fillna 策略是否会造成早期数据失真？
- `calendar.py`：交易日判定来源是否可靠？跨年、节假日调休是否覆盖？
- `attribution.py`：归因的**权重守恒**是否成立（各项贡献加总 = 总收益）？基准选择是否一致？
- `risk.py` / `optimizer.py` / `neutralize.py`：协方差估计是否有奇异性处理？优化器是否有无解/不收敛的兜底？中性化是否在正确的截面上做？
- `a_share_rules.py`：交易费用、最低佣金、印花税（**单边仅卖出**）是否与现行规则一致？

---

### B3a · 数据层 · 存储与面板（约 3500 行）

```
backend/app/data/parquet_store.py
backend/app/data/panels.py
backend/app/data/features.py
backend/app/data/universe.py
backend/app/data/calendar_store.py
backend/app/data/cross_section.py
backend/app/data/portfolio_source.py
```
**本批重点**：原子写入是否真原子（临时文件 + rename，跨盘符是否失效）？分区读取是否处理缺失年份？`ffill().reindex(t).ffill()` 类操作是否有零长度守卫？截面计算是否用了未来数据？universe 的时点正确性（**是否用当前成分股回填历史 ⇒ 幸存者偏差**）。

---

### B3b · 数据层 · 采集与质量（约 4300 行）

```
backend/app/data/ingest/*.py
backend/app/data/pipeline.py
backend/app/data/quality.py
backend/app/data/repair.py
backend/app/data/realtime.py
backend/app/data/quotes_hub.py
backend/app/data/screening.py
backend/app/data/etf.py
backend/app/data/announcements.py
backend/app/data/text_ingest.py
```
**本批重点**
- 采集：限速/重试/双源降级的**降级后是否会把残缺数据当完整数据落盘**？断点续传的 state 是否会被无意清空？分页/分片边界是否漏数据（尤其 >200 只标的时的静默截断）？
- `quality.py`：体检项判定阈值是否合理？**是否存在永远不会触发的检查**（例如依赖一个从未写入的字段）？
- `screening.py`：**这是选股策略核心，请最仔细看。** 因子权重、打分方式、排序稳定性（同分如何处理）、`signal_strength` 的分位定义与实际数据的匹配度、空榜/全停牌等极端截面的处理。
- `realtime.py`：缓存时效与交易时段判定是否正确？非交易时段是否返回过期数据却标为实时？
- 逐个确认：这些模块**是否真的被上层调用**（未接线即死代码）。

---

### B4a · ML · 特征与推理（约 2400 行）

```
backend/app/ml/features.py
backend/app/ml/features_v2.py
backend/app/ml/labeling.py
backend/app/ml/infer.py
backend/app/ml/predict.py
backend/app/ml/registry.py
backend/app/ml/monitor.py
```
**本批重点**
- **前视泄漏**：特征计算的窗口是否严格只用到 `t` 及之前？标签窗口与特征窗口是否有 gap？标准化/中性化的统计量是用**全样本**算的还是只用训练段？（用全样本 ⇒ 泄漏）
- 训练与推理的**特征口径是否完全一致**（同一套派生流程、同一列顺序、同一缺失值填充）？不一致会导致线上预测静默失真——请重点对比 `features_v2.py` 与 `infer.py` 的调用链。
- `monitor.py`：模型漂移/衰减判定是否有误报风险？触发重训的阈值是否合理？读取侧的守卫（例如"距上次重训不足 N 秒则跳过"）是否会造成**长期静默阻断**？
- `registry.py`：模型版本回滚是否安全？并发读写注册表是否有竞态？

---

### B4b · ML · 训练与模型（约 2500 行）

```
backend/app/ml/train_lgbm.py
backend/app/ml/train_service.py
backend/app/ml/purged_cv.py
backend/app/ml/signal_analysis.py
backend/app/ml/alpha_expr.py
backend/app/ml/gp_miner.py
backend/app/ml/graph.py
backend/app/ml/gnn_models.py
backend/app/ml/torch_models.py
backend/app/ml/sequence_dataset.py
backend/app/ml/torch_device.py
```
**本批重点**
- `purged_cv.py`：purge 与 embargo 的宽度是否 ≥ 标签 horizon？是否有边界 off-by-one？
- `train_lgbm.py`：三段切分（train/gap/valid/gap/test）是否正确？**验证段是否被用于早停之外的任何决策**（用 valid 调参再报 test ⇒ 间接泄漏）？特征筛选是否只用训练段？
- 超参数搜索（若有）是否在**同一份数据上反复评估**⇒ 过拟合风险。是否有 walk-forward？
- `gp_miner.py`：遗传规划挖因子的**适应度函数是否用了未来数据**？是否对多重检验做了控制（挖 1000 个因子必然有几个历史 IC 很高）？
- **死代码专项**：`gnn_models.py` / `graph.py` / `sequence_dataset.py` / `torch_models.py` / `torch_device.py` —— 这些是**实际接入推理链路**，还是实验性遗留？请明确指出哪些模块当前无任何调用方。

---

### B5 · 回测与交易（约 2300 行）

```
backend/app/backtest/engine.py
backend/app/backtest/broker.py
backend/app/backtest/strategy_base.py
backend/app/backtest/ma_cross.py
backend/app/backtest/param_search.py
backend/app/trading/paper.py
backend/app/jobs/evening_routine.py
```
**本批重点（本项目的命门，请最严格）**
- 撮合时序：信号在 `t` 日收盘生成、在 `t+1` 开盘成交，还是**同日以收盘价成交**（后者=前视）？
- T+1 是否真实现（当日买入不可卖）？
- 涨跌停：涨停能否买入、跌停能否卖出，是否按**当日实际涨跌停价**判定（而不是按幅度近似）？
- 停牌：停牌期间是否错误地按最后价成交？复牌如何处理？
- 整手：买入是否向下取整到 100 股？
- 费用：佣金（含最低 5 元）、印花税（**仅卖出**）、过户费是否齐全？
- 滑点/冲击成本假设是否现实（尤其对小市值、涨停板标的）？
- 退市/ST/新股：是否被错误地纳入可交易池？（幸存者偏差）
- **`param_search.py` 是过拟合高发区**：是否在同一段历史上挑最优参数？是否报了样本外结果？
- `engine.py` 与 `ma_cross.py`（或另一套实现）是否存在重复逻辑，两套结果是否可能不一致？
- **性能**：是否有 O(n²) 的 pandas 逐行循环可以向量化？

---

### B6 · 服务层与任务编排（约 1500 行）

```
backend/app/services/market_service.py
backend/app/services/stats_cache.py
backend/app/services/sync_service.py
backend/app/services/task_store.py
backend/app/data/ingest/tasks.py
```
**本批重点**：任务状态机是否可能进入**非终态残留**（running 卡死、无人回收）？进程被强杀时状态是否会被永久留在 running？`task_store` 的状态流转是否有非法跃迁？多个任务同时写同一 DATA_ROOT 是否有互斥？**时间戳是否统一时区**（混用本地时间与 UTC 会让数据"凭空变老/变新"）？

---

### B7a · API 路由 · 第一部分（约 4700 行）

```
backend/app/api/v1/auth.py
backend/app/api/v1/market.py
backend/app/api/v1/stock.py
backend/app/api/v1/screener.py
backend/app/api/v1/etf.py
backend/app/api/v1/report.py
backend/app/api/v1/research.py
```
**本批重点**：每个端点的 `require_role` 是否**与文档声明的角色要求一致**（缺失装饰器=权限绕过，是 P0）？路径参数是否有注入/越界风险？分页参数是否限制了上限（客户端传 limit=10^9 会怎样）？响应字段名是否与契约一致（`score` / `data.status`）？是否有端点返回**裸 500** 的路径（未捕获异常）？

---

### B7b · API 路由 · 第二部分（约 4600 行）

```
backend/app/api/v1/backtest.py
backend/app/api/v1/portfolio.py
backend/app/api/v1/datacenter.py
backend/app/api/v1/ops.py
backend/app/api/v1/monitor.py
backend/app/api/v1/alerts.py
backend/app/api/v1/notify.py
backend/app/api/v1/desk.py
backend/app/api/v1/studio.py
backend/app/api/v1/export.py
backend/app/api/v1/watchlist.py
backend/app/api/v1/app_settings.py
```
**本批重点**
- `datacenter.py` / `ops.py`：**管理类端点是否都做了 admin 保护**？有没有只校验登录不校验角色的写操作（越权）？
- `notify.py`：SSE 的一次性 ticket 是否真的只能消费一次？ticket 是否可重放？建连后 JWT 是否泄漏到 URL？
- 任务类端点：是否允许**并发触发同一个重活**（重复跑训练/重复同步）？是否有幂等键？
- 分页/截断：返回被截断的结果时是否**向调用方披露截断**（否则前端会展示"看起来完整其实缺数据"的列表）？
- 导出类端点（`export.py`）：是否有路径穿越、公式注入（CSV injection）？

---

### B8 · 前端 · 基础设施（约 2500 行）

```
frontend/src/main.tsx
frontend/src/App.tsx
frontend/src/api/client.ts
frontend/src/api/swr.ts
frontend/src/stores/*.ts
frontend/src/hooks/*.ts
frontend/src/utils/*.ts
frontend/src/lib/echarts.ts
frontend/src/types/*.ts
frontend/src/components/*.tsx
frontend/src/components/ui/index.tsx
```
**本批重点**
- `client.ts`：超时是否设置？**是否有全局超时缺失导致请求永久挂起**？错误信封如何解析（HTTP 200 + 非零 code 是否被正确识别为错误）？401 是否触发登出？重试是否可能导致写操作重复执行？
- **可选链 / 空值安全**：`?.` 覆盖是否完整？后端 `data` 字段缺失或为 `null` 时是否会白屏（**这是本类项目最高频的前端崩溃原因**，请逐处检查属性访问链）。
- `swr.ts`：请求竞态——快速切换筛选条件时，**旧响应是否会覆盖新响应**？
- `stores/useAuthStore.ts`：角色判断是否与后端一致？token 存取是否安全（localStorage vs 内存）？过期处理？
- `types/*.ts`：类型定义是否与后端实际响应逐字段对应（不是"大概对应"）？枚举值是否一致？

---

### B9a · 前端 · 市场与个股页（约 3500 行）

```
frontend/src/pages/MarketOverview/*
frontend/src/pages/StockDetail/*
frontend/src/pages/Screener/*
frontend/src/pages/Watchlist/*
frontend/src/components/charts/KLineChart.tsx
frontend/src/components/charts/MarketHeatmap.tsx
```
**本批重点**：图表数据为空时的渲染是否安全？大量数据点是否做了降采样（性能）？数值格式化是否处理了 `null`/`NaN`/极值（涨跌幅颜色约定：**A 股红色=涨、绿色=跌**，若反了是 P1）？选股页的筛选条件是否与后端参数一一对应，有没有前端传了后端不认的字段？

---

### B9b · 前端 · 回测/组合/研究页（约 4000 行）

```
frontend/src/pages/Backtest/*
frontend/src/pages/Portfolio/*
frontend/src/pages/Report/*
frontend/src/pages/Research/*
frontend/src/pages/FactorStudio/*
frontend/src/pages/CapacityAttribution/*
```
**本批重点**：回测结果展示是否有**误导性呈现**（例如只展示最优参数、未标注样本外、未扣成本）？风险提示是否到位（合规）？表格分页与后端分页语义是否一致（客户端分页 vs 服务端分页混用是高发 bug）？

---

### B9c · 前端 · 数据/运维/其他页（约 4000 行）

```
frontend/src/pages/DataCenter/*
frontend/src/pages/DataQuality/*
frontend/src/pages/Pipeline/*
frontend/src/pages/OrderDesk/*
frontend/src/pages/Alerts/*
frontend/src/pages/Settings/*
frontend/src/pages/Etf/*
frontend/src/pages/EtfDetail/*
frontend/src/pages/Login/*
```
**本批重点**：长任务轮询是否有**清理与退避**（组件卸载后继续轮询 = 内存泄漏 + 无效请求）？轮询是否在任务终态后停止？管理操作是否有二次确认？登录页是否误把 ADMIN_TOKEN 当密码用（项目文档明确说过这是易错点）？

---

## 4. 【P2 策略专项提示词】—— 单独开一轮，只贴策略相关文件

```text
现在做**策略专项审核**，不看代码风格，只回答「这个策略在真实 A 股市场上能不能赚钱、假设是否成立」。

请分别审核以下四组：

A. 选股策略（screening.py + api/v1/screener.py + ml/signal_analysis.py）
   1. 因子选择的**经济逻辑**是否成立？有没有只是历史上碰巧有效的伪因子？
   2. 打分与权重是拍定的还是回归/IC 加权的？权重是否稳健？
   3. 排序在同分、极端截面、停牌股上的行为是否可接受？
   4. `data.status` 的三态语义是否在所有分支下都正确（空榜必须 unavailable）？

B. 回测假设（backtest/*）
   1. 逐条核对：T+1、涨跌停、停牌、整手、佣金、印花税、过户费、滑点 —— 哪一条与现实不符？
   2. 成交价选择是否引入前视？
   3. 换手率与交易成本是否被低估？年化收益是否被高估？给个数量级估计。
   4. 是否存在幸存者偏差、退市股处理缺失、新股纳入时点错误？

C. 机器学习建模（ml/features_v2.py, labeling.py, purged_cv.py, train_lgbm.py, infer.py）
   1. 标签 horizon 与经济含义是否匹配？标签是否做了截面标准化？
   2. purge/embargo 是否足够？三段切分是否正确？
   3. 特征是否严格 asof 稳定？训练/推理口径是否完全一致？
   4. 模型是绝对收益回归还是截面排序？**对 A 股而言哪个更合理**？
   5. 有没有可以显著提升的方向（例如：行业中性化、市值分层、换手率过滤、多周期集成、加入量价外的另类数据）？

D. 风控与组合（domain/risk.py, attribution.py, optimizer.py, neutralize.py, portfolio.py）
   1. 组合约束是否现实（个股权重上限、行业暴露上限、换手约束）？
   2. 风险模型是否用了足够长的窗口？协方差的奇异性如何处理？
   3. 归因是否守恒？基准是否恰当？

【对每一条优化建议，必须给出】
- 当前做法的问题（一句话）
- 具体改法（可落地到代码层面）
- 预期收益（收益提升 / 回撤降低 / 成本节省，给量级）
- 引入的风险或副作用
- 验证方式（怎么判定改完确实更好了）

【禁止】
- 不要给"建议增加更多因子""建议使用深度学习""建议做集成"这类无法落地的空话。
- 不要声称你知道当前的实测收益、IC 或夏普——你没有这些数据。若需要，请明确列出你需要我提供哪些指标。
```

---

## 5. 【P3 死代码专项提示词】

**先本地跑扫描，再把输出贴给 ChatGPT 研判**——这比让它读代码猜准确得多。

```bash
# 后端：未使用导入 / 未使用变量 / 未定义名 / 重复定义
cd backend
.venv/Scripts/python.exe -m ruff check app --select F401,F811,F841,F821,F811,F402,F403,F405,F501,F522

# 后端：未使用的函数 / 类 / 变量（跨文件）
.venv/Scripts/python.exe -m pip install vulture -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv/Scripts/python.exe -m vulture app --min-confidence 80

# 后端：未被引用的模块（入口/插件除外）
# 前端：未使用的文件与导出（强烈推荐，专治前端死组件）
cd frontend
npx knip --reporter json
npx tsc --noEmit --noUnusedLocals --noUnusedParameters
```

然后把输出贴给 ChatGPT，附以下提示词：

```text
下面是 AQP 项目的死代码扫描输出（ruff / vulture / knip 等），以及相关文件清单。

请你**研判**，而不是照单全收。对每一条输出判定：
1. 真死代码 — 确实无任何调用方，可以删除。请给出删除的具体位置与影响范围。
2. 假阳性 — 属于以下被动态引用的情形，请说明原因并保留：
   - FastAPI 路由/依赖通过装饰器注册，不被显式调用
   - Pydantic 模型字段、SQLAlchemy 模型字段
   - pytest 的 fixture、`test_*` 函数
   - 字符串动态导入、反射调用、`getattr`
   - 前端按路由懒加载的组件、动态 import
   - 序列化/反序列化需要的字段
3. 未接线代码 — 代码写完了、能跑，但**没有任何入口能触达**（既不暴露为 API，也没有被任何任务调度）。
   这一类比普通死代码更危险，因为它意味着"功能声称存在但实际不可用"。请单独列出。

另外请主动做两件事（不依赖扫描输出）：
A. 识别**多实现并存**：同一个功能有两套代码路径（例如两套回测、两代特征），
   指出哪套是活的、哪套是死的、能否安全删除、删除前需要确认什么。
B. 识别**永远不会成立的分支**：条件恒真/恒假、被上游提前 return 掉、被类型约束排除的 except 子句等。

输出用表格：| 位置 | 分类（真死/假阳性/未接线） | 依据 | 建议动作（删除/保留/接线） | 置信度 |
```

---

## 6. 【P4 跨层一致性对账提示词】—— 最后汇总轮，价值最高

> 单文件审核**永远发现不了**跨层错配。这一轮必须做，而且要把前后端的清单一起给它。

```text
现在做**前后端契约对账**。这是最容易被单文件审核漏掉的一类缺陷。

我已提供：
- 后端全部路由定义（api/v1/*.py）与响应构造代码
- 前端全部 API 调用（frontend/src/api/*.ts）与类型定义（frontend/src/types/*.ts）
- 前端各页面对接口的调用点

请逐项核对，输出不一致清单：

1. **路径与方法对账**：前端调用的每个 URL + method，后端是否真实存在？
   （注意 baseURL、路径拼接、尾部斜杠、路径参数替换方式）
   → 列出：前端调用但后端不存在的接口（P0，功能必然报错）
   → 列出：后端存在但前端从未调用的接口（可能是死接口）

2. **请求参数对账**：前端传的 query/body 字段名与类型，后端 `Query(...)` / Pydantic 模型是否一致？
   → 错别字、大小写、复数形式、日期格式、枚举取值不一致都要列。

3. **响应字段对账**：前端读取的 `data.xxx` 字段，后端是否真的返回？
   → 重点查 `?.` 没有覆盖的可选字段（会白屏）。
   → 重点查 `data.status`、`score`、分页字段（total/page/total_pages 命名是否一致）。

4. **错误处理对账**：前端是否所有调用点都检查了信封的 `code != 0`？
   → 有哪些地方把 HTTP 200 当成成功、直接读 `data`（会在业务错误时崩溃或展示垃圾数据）？

5. **权限对账**：前端按角色隐藏的按钮/页面，与后端 `require_role` 是否一致？
   → 前端隐藏但后端不校验 = 越权可绕过（P0）。
   → 后端要求但前端未做限制 = 用户点了必然 403/报错（P1）。

6. **错误码对账**：后端定义的业务码，前端是否有对应的分支处理？有没有"码表里存在但前端从不识别"的码？

【输出格式】
| 序号 | 严重度 | 不一致类型（路径/参数/响应/错误/权限/码） | 前端位置 | 后端位置 | 现象 | 修复建议 |
最后给出一个「必须立刻修的 Top 10」清单，按影响面排序。
```

---

## 7. 【P5 汇总提示词】—— 所有批次审完后，开一轮新对话做归并

```text
以下是我按模块分批审核后得到的 N 份问题清单（我全部贴给你）。
请做汇总与归并，不要简单罗列：

1. **去重**：合并同一根因在不同文件/批次重复出现的问题（例如"未检查信封 code"出现在 6 个文件，
   应归并为 1 条 + 6 处位置）。
2. **根因归类**：把问题按根因聚类，例如"状态机非终态残留"可能表现为 5 个不同症状。
   对每个根因给出：根因描述、影响的所有位置、统一修复方案。
3. **优先级排序**：按 (影响面 × 严重度 × 修复成本倒数) 排序，给出 Top 20 建议的修复顺序。
4. **标注我的判断错误**：若两份清单对同一处给出矛盾结论，请指出并说明哪个更可能正确、如何验证。
5. **诚实统计**：明确区分「确定要修」「疑似待验证」「建议但不紧急」三类各多少条。
   不要把疑似算进确定里来凑数。

最终输出结构：
- 一、总览（各部分问题数统计表）
- 二、P0 必须立刻修（每条含位置、现象、修复方案、验证方法）
- 三、P1 功能错误
- 四、P2 边界与性能
- 五、死代码清单（含删除顺序与风险）
- 六、策略优化建议（按预期收益排序）
- 七、我需要向审核方补充确认的信息清单
```

---

## 8. 反面清单：这样用会浪费时间

| 做法 | 后果 |
|---|---|
| 整仓压缩包 + "帮我找 bug" | 得到 20 条"建议加日志/加注释" |
| 不贴 P0 背景块 | 把"Redis 降级""双源降级""空榜 unavailable"当成严重 bug 报一堆 |
| 一次喂 1 万行以上 | 模型只精读开头和结尾，中间糊弄过去 |
| 让它"顺便优化代码风格" | 淹没真问题 |
| 不问「最小验证方法」 | 拿到无法证实也无法证伪的结论，改完不知道对不对 |
| 只做单文件审核，不做第 6 节对账 | 漏掉最高频的一类缺陷：前后端字段/路径错配 |
| 相信它说"我测试过" | 幻觉。它无法运行你的代码 |

---

## 9. 复核清单：拿到回答后怎么验收

ChatGPT 的回答**必须由你本地验证后才算数**。建议按此流程落地：

1. 对每条「确定」级别的问题，先跑它给的**最小验证方法**。跑不通 ⇒ 直接划掉。
2. 修复前先建**回归用例**：把问题写成一条会失败的测试，修完必须转绿。
   这是本项目已确立的纪律——*只有把缺陷改回去、用例必须变红，才算真正覆盖*。
3. 死代码**不要一次删完**。按"未被引用 → 本地构建/测试全绿 → 提交"的顺序小批量推进，
   每次删除后跑一次全量离线测试确认无回归。
4. 策略类建议**必须在样本外验证**后才能采纳，禁止凭"看起来更合理"就改生产口径。
5. 所有涉及生产模型/特征口径的改动，改完必须复核**训练与推理口径一致性**。

> 本项目的既有事实：全量离线测试基线为 **1071 passed / 8 skipped / 3 deselected / 0 failed**。
> 任何改动后若偏离此基线，先查是回归还是用例本身需要更新。
