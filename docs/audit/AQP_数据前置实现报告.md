# AQP 数据前置实现报告（FinLLM / MED-003 / TFT / GNN）

> 实现时间：2026-09-03
> 范围：《AQP_前沿演进评审与落地路线》Phase 3+ 的**数据前置部分**——按用户要求
> "先不抓取数据"，三条线的基础设施全部建成，数据到位后即可直接使用/训练。
> 验证：pytest 307 收集（新增 8 例）· tsc 0 错 · build OK · 真实服务全链路实测

---

## 一、Track A：截面分区镜像（MED-003 性能墙拆除）

**问题回顾**：daily_bar 系按 (symbol, year) 分区，5000 只股票 = 25,000 个小文件，
按日期取截面需全量打开——扩池路上的最大 IO 瓶颈。

**方案**（双 store 并存，源数据不动）：

| 项 | 说明 |
|---|---|
| 新文件 | `app/data/cross_section.py` |
| 镜像布局 | `DATA_ROOT/cs/<dataset>/year=YYYY/date=YYYYMMDD.parquet`（每交易日一个文件，全部标的 × 全列） |
| 覆盖数据集 | daily_bar / daily_bar_hfq / daily_bar_qfq |
| 构建 | `build_mirror(dataset, incremental=True)`：以「源文件 mtime > 镜像 mtime」判定过期日期，幂等增量（粒度=源文件级，宁多读不漏更） |
| 读取 | `read_cross_section(dataset, d)`（O(1) 文件）、`read_cross_range(dataset, start, end)`（年目录谓词下推） |
| 自动化 | pipeline 注册 `step_build_cs_mirror`（不进默认 STEPS），晚间例行 17:30 每日自动增量重建 |
| 手动兜底 | `GET /datacenter/mirror/status`、`POST /datacenter/mirror/rebuild`（researcher） |
| 前端 | 数据中心新增「截面分区镜像」卡：各数据集源/镜像日期覆盖、滞后天数、手动重建按钮 |

**实测**（真实数据，121 只 × 1,131 个交易日）：
- 首次全量构建：3 个数据集各 1,131 个日期全部建成，零滞后（约 3-4 分钟，一次性）；
- 增量重跑：**2.3 秒**全跳过；
- 单日截面读取：**0.004 秒**（单文件）；2026-08 全月区间读取 0.016 秒。
- 扩池到 5000 只后，源方案截面读取需打开 5,000+ 文件，镜像仍保持 O(1)。

---

## 二、Track B：公告文本管线（1-2 FinLLM 数据前置）

**设计原则**：无网络抓取源阶段"路"先修好——文档导入 → 清洗分块 → 情绪打分 →
日频因子落盘 → 训练侧挂接，全链路现在就可工作（数据从 API/人工导入来），
抓取源落地后只需接入适配器。

| 项 | 说明 |
|---|---|
| 新文件 | `app/data/text_ingest.py`、`app/core/llm.py`（从 studio 抽取的共享 LLM 客户端） |
| 文档存储 | `DATA_ROOT/announcements_docs/year=YYYY.parquet`（symbol/date/title/content/source/doc_id，hash 去重幂等） |
| 清洗分块 | 控制字符剔除 + 空白归一 + 500 字滑窗（overlap 50） |
| 情绪打分 | **规则 A 股词库打底**（40+ 词，tanh 归一 [-1,1]，离线必出）；`LLM_PROVIDER` 配置后自动升级为 LLM 批量打分（JSON 输出解析，任何失败回退规则，绝不阻塞） |
| 因子落盘 | `DATA_ROOT/text_features/version=sentiment_v1/year=YYYY.parquet`（sentiment/n_docs/method，按 symbol×date 聚合） |
| 防未来函数 | `attach_text_features(df)`：T 日公告 **T+1 起可用**（merge_asof 可用日后移），训练侧一行挂接，无覆盖样本保持 NaN 不造数 |
| 端点 | `GET /datacenter/text/status`、`POST /datacenter/text/import`（researcher，JSON 批量）、`POST /datacenter/text/build-factor`（researcher） |
| 前端 | 数据中心新增「文本数据」卡：状态总览 + JSON 导入 + 因子构建按钮 |
| 抓取源 | `register_source()` 接口预留——akshare 公告源接入后仅新增适配器，管线其余零改动 |

**实测**：真实服务导入 2 条演练公告 → 规则打分（利好 +0.91 / 利空 -0.87）→ 因子聚合 2 行 → status 正确；演练数据已清理（docs 归零），未污染真实库。

---

## 三、Track C：TFT / GNN 训练前置（3-1 / 3-2）

**原则**：torch 为**可选依赖**（未安装时 CLI 明确提示，其余功能零影响）；
治理复用——TFT/GNN 候选走与 LGBM 同一 model_registry/promote 通道；
120 只池下训练被 CLI 主动劝阻（样本 <5000 直接中止），这是研究判断不是工程缺陷。

| 文件 | 内容 |
|---|---|
| `app/ml/sequence_dataset.py` **新** | ① `build_sequences()`：features 长表 → (N, Lookback, F) float32 张量 + 前向收益标签（含 NaN 窗口整行剔除，剔除比例如实返回）；② `purged_time_split()`：按交易日切 train/valid/test，段间 gap=horizon（与 train_lgbm 同纪律，防标签跨段泄漏）。纯 numpy/pandas，无 torch 依赖 |
| `app/ml/torch_models.py` **新** | 轻量 Transformer 编码器（线性嵌入→Encoder→均值池化→回归）；`train_sequence_model()`（早停/MSE、`torch.set_num_threads(effective_cpu_threads())` 线程纪律）+ `predict_sequence()`；torch 懒加载 |
| `app/ml/graph.py` **新** | ① 关系边双来源：`relations/edges.parquet`（供应链/股权显式边）+ instrument 表 industry 同业 peer 边（当前 86% 为空，扩池补齐后自动变稠）；② `build_adjacency()` 行归一化邻接矩阵；③ `propagate()`：**邻居聚合传导因子** `g{h}_{col}`（按有值邻居归一，无覆盖保持 NaN）——最低成本的产业链传导近似，可直接并入 LGBM/TFT 特征；无关系数据时透明降级 |
| `app/ml/gnn_models.py` **新** | 裸 GCN（两层 A·X·W，无 PyG 依赖）+ 训练/推理 |
| `scripts/train_tft.py` **新** | CLI：features → 序列张量 → purged 切分 → 训练 → 逐日 RankIC 评估 → 候选登记（`tft_v1`，走 promote 门禁）。样本 <5000 主动中止并提示扩池 |
| `scripts/train_gnn.py` **新** | CLI：逐日截面矩阵 + 邻接 → GCN → 候选登记（`gnn_v1`）。无关系边时拒绝训练 |
| `app/ml/registry.py` | `register_candidate` 增加 `model_file` 参数（TFT/GNN 记 model.pt，不再硬编码 model.lgbm） |
| `requirements.txt` | 追加可选依赖说明（torch CPU 安装命令） |

**数据到位后的使用路径**（用户训练时）：
```
pip install torch --index-url https://download.pytorch.org/whl/cpu
python scripts/train_tft.py --lookback 30 --epochs 30     # 扩池 1000+ 后
python scripts/train_gnn.py                                # 关系数据落地后
python scripts/promote_model.py                            # 门禁把关后 promote
```

---

## 四、验证记录

| 项 | 结果 |
|---|---|
| 新增测试 `tests/test_data_prep.py` | 8 例全绿：镜像往返/增量/未知数据集拒绝、导入去重/因子聚合/T+1 挂接、序列张量形状与 NaN 剔除、purged gap 纪律、图传播（含孤立节点 NaN、缺失邻居不稀释） |
| 全量 pytest | 见下方说明 |
| tsc / build | 0 错 / 9.2s 构建成功 |
| 真实服务烟雾 | 镜像全量 3×1,131 日零滞后、增量 2.3s、截面读 0.004s；文本导入→打分→因子→status 全链路 OK（演练数据已清理） |

**pytest 说明**：本轮全量 306 passed + 1 failed——失败为 `test_p2_ml.py::TestGridSearch::test_feature_runs_migration_and_record`（对共享测试库 feature_runs 的**精确计数断言**，audit HIGH-002 家族的顺序敏感类问题）。该测试单独跑、与新增测试组合跑均通过；已重跑全量复核（结果见复审对话），判定为偶发 flake 而非本次改动回归，但精确计数断言的脆弱性值得后续修复（改为范围断言或 per-test 隔离 DB）。

---

## 五、遗留与后续

1. **抓取源适配器**：公告抓取（akshare）与供应链/股权关系抓取待网络数据阶段接入 `text_ingest.register_source()` / `relations/edges.parquet`；
2. **Krusel KS 检验**：漂移监控目前 PSI（5-2 已实现），KS 需引入 scipy 后补；
3. **test_p2_ml 精确计数断言**加固（独立小任务）；
4. mirror 目前只镜像 bar 三口径——features 本身仅 5 个年度文件，暂无必要。
