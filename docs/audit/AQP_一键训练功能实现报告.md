# AQP 数据中心「一键训练」功能实现报告

> 需求：在前端新增训练按钮，数据抓取完成后可直接启动训练。
> 日期：2026-09-03 · 状态：✅ 完成（后端 325 passed / 8 skipped，tsc 0 错，build 通过）

## 功能形态

数据中心页新增 **「模型训练」面板**（位于文本数据/镜像面板之后）：

- **就绪度门禁清单**（4 项，绿/红点直观显示）：PyTorch 已安装 / features 已构建 /
  预估样本 ≥ 5000 / 关系边 > 0（GNN 用）。数据没抓够时按钮禁用并明确提示"先抓数据"。
- **模型二选一**：TFT 时序模型（SeqTransformer，无需关系边）/ GNN 产业链传导（需关系边），
  各自独立标注就绪状态；epochs 可调（默认 30）。
- **启动 / 取消**：运行中每 2s 轮询状态，日志实时滚动；取消为优雅模式（逐 epoch 检查，
  当前 epoch 走完即停，未落盘不登记）。
- **结果持久化**：上次训练的 valid/test RankIC 落 app_state kv，后端进程重启后仍可见；
  失败/中止原因（含"样本不足已主动中止"）也如实展示。

## 架构要点

| 层 | 改动 |
|---|---|
| `backend/app/ml/train_service.py`（新） | 训练逻辑**唯一真源**：`train_readiness()` 四项门禁、`run_tft_training()`/`run_gnn_training()` 全流程（含存盘回读校验 → RankIC → register_candidate）、`_TrainJob` 单任务串行状态机（参照 datacenter._SyncState）、`_ALLOWED_PARAMS` 白名单防 kwarg 注入 |
| `torch_models.py` / `gnn_models.py` | `train_sequence_model`/`train_gnn` 新增 `should_stop` 取消回调（逐 epoch 检查） |
| `scripts/train_tft.py` / `train_gnn.py` | 薄壳化：argparse + 调 service，消除 CLI/API 双份逻辑漂移 |
| `api/v1/datacenter.py` | 新增 4 端点：`GET /train/readiness`、`POST /train/start`（researcher）、`GET /train/status`、`POST /train/cancel`（researcher） |
| `frontend/api/datacenter.ts` | TrainReadiness/TrainStatus/TrainResult 类型 + 4 个 API 方法 |
| `frontend/pages/DataCenter/TrainPanel.tsx`（新） | 训练面板组件，挂载于 DataCenter/index.tsx |

## 关键设计决策

1. **样本量门禁 MIN_TRAIN_SAMPLES=5000**：120 只池训深模型没有统计意义（研究判断），
   启动时主动拒绝并提示扩池——不是工程缺陷，是如实的研究结论。
2. **torch 可选依赖**：未安装时 readiness 如实标注、start 明确拒绝并给 CPU 版安装指引，绝不静默降级。
3. **启动时门禁前置**：`/train/start` 先评估 readiness，不满足直接返回非零 code（ERR_TRAIN=52000）
   + 具体原因列表，前端展示——避免起了任务再失败。
4. **单任务串行**：与数据同步任务同款模式（线程 + lock + cancel_event + 环形日志），并发请求被拒。
5. **逻辑唯一真源**：CLI 与 API 共用 train_service，参数白名单过滤后才透传。

## 验证记录

- 新增 `tests/test_train_service.py`（13 例）：空环境门禁、无 torch 拒绝（ERR_TRAIN）、
  features 缺失拒绝、样本不足拒绝、参数白名单过滤、should_stop 首轮即停（torch 装了才跑）、
  RankIC 完美/反向/薄日跳过、取消与状态生命周期。
- 全量后端 pytest：**325 passed, 8 skipped**（含既有 313 例不回归）。
- `npx tsc --noEmit` 0 错误；`npm run build` 通过；`/train/*` 4 路由注册冒烟验证通过。

## 后续（数据抓到位后）

1. 数据中心页点「增量同步」扩池（目标 ≥1000 只、5 年历史）；
2. `pip install torch --index-url https://download.pytorch.org/whl/cpu`（CPU 版够用）；
3. 门禁全绿后选模型点「开始训练」，日志区观察进度，完成后在研究页可见登记的候选模型。
