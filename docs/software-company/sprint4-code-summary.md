# 代码摘要 — Sprint 4（按需线：因子工场 / ML Lab / 晨报）

> 工程师环节（python-fullstack-engineer SOP）｜ 日期：2026-09-05 ｜ 前置：Sprint 1/2/3 已交付
> 范围决策：探索发现「表达式引擎（alpha_expr AST 白名单）/ 晨报骨架 / 候选实验表」已有存量基建，
> 本轮做**增量闭环**；回测寻优（Optuna 新依赖）+ Brinson 已有（report 四节）→ 列遗留。

## 存量盘点 → 增量收敛

| 路线图项 | 存量 | 本轮增量 |
|---|---|---|
| 4.3 表达式引擎 | ✅ 已有（alpha_expr 白名单 AST + /alpha-eval + NL→因子） | **入库闭环**：CustomFactor 表 + CRUD |
| 4.3 检测报告 | 部分（IC 时序 + 多空净值） | **5 分组分层/年化 + 1/5/10/20 日衰减 + Top 组换手率** + 前端 |
| 4.5 实验看板 | ✅ 已有（/research/experiments 候选表） | **分年稳定性热力**（生产预测 × 次日收益逐年聚合） |
| 4.6 每日晨报 | ✅ 已有（build_daily_report + evening routine 挂载 + kv 持久化） | **榜单变动 + 模型分数迁移** section + webhook 分发 |
| 4.4 回测寻优 | Brinson 已在晨报；Optuna 未装 | **遗留**（新依赖 + walk-forward，独立一轮） |

## 文件清单

| 文件路径 | 变更 | 关键实现点 |
|---|---|---|
| `backend/app/db/models.py` | 修改 | `CustomFactor` 表（name 唯一 / metrics_json 评估摘要 / enabled 下线位） |
| `backend/app/ml/gp_miner.py` | 修改 | 新增 `factor_report()`：与 evaluate_expr_detail 同底座（eval_expr → 截面宽表），产出 5 分位分层累计净值与年化、1/5/10/20 日衰减 RankIC/ICIR、Top 20% 名单日度变动率（Jaccard）；样本不足返回 None 不造数 |
| `backend/app/api/v1/studio.py` | 修改 | `GET/POST/DELETE /studio/factors`（入库前 AST 校验 + 真实截面评估，摘要随档；重名拒绝）、`POST /studio/factor-report`（纯报告无副作用） |
| `backend/app/api/v1/research.py` | 修改 | `GET /research/lab/yearly`：predictions × daily_bar 次日真实收益逐年聚合 RankIC/ICIR/命中率（<500 对齐样本年份如实缺席）；`_run_sync` 助手 |
| `backend/app/api/v1/report.py` | 修改 | 新 section「二、榜单与模型」（快照 top-N 新进/跌出 + 近两期 predictions top-K 重合率与均分迁移，数据源=Sprint3 快照表）；webhook 分发（NOTIFY_WEBHOOK_URL）；后续节号顺延 |
| `backend/app/data/parquet_store.py` | 修改 | **`read_parquet_columns()`：多文件投影读的列集演进容忍**——polars 多文件读要求 schema 全一致，混合列集分区直接崩（projection index OOB）；逐文件 footer 校验 + 缺列跳过 + date 混存归一 |
| `backend/app/api/v1/market.py` | 修改 | `_heat_from_local` / `_build_ai_stats` 切换容错读 |
| `backend/tests/test_lab_factor.py` | **新增** | 5 用例：报告内核结构、因子库 CRUD 往返+重名/非法表达式拒绝、晨报新 section、整报生成、Lab 分年聚合 |
| `frontend/src/api/production.ts` | 修改 | factorReport / factors CRUD + `FactorReportResult/CustomFactor` 类型 |
| `frontend/src/api/research.ts` | 修改 | `labYearly()` + 类型 |
| `frontend/src/pages/FactorStudio/FactorLab.tsx` | **新增** | 因子库面板（入库/删除/列表）+ 检测报告（分层净值曲线、年化徽标、衰减徽标、换手率；口径注释） |
| `frontend/src/pages/FactorStudio/index.tsx` | 修改 | 挂载 FactorLab（评估表达式一键带出） |
| `frontend/src/pages/Research/index.tsx` | 修改 | MLOps 卡内新增分年稳定性热力格（色深=RankIC，悬停看 ICIR/命中率） |
| `frontend/src/utils/useChart.ts` | 修改 | 参数放宽为 EChartsCoreOption（严格类型可赋值方向不变） |

## 验证结果

- 新增测试 5/5；全量回归 **381 passed / 5 skipped / 0 failed**（13.2min）
- 线上冒烟（生产端口）：`/studio/factors` 17ms、`/research/lab/yearly` 1.55s（真实预测 × 行情逐年聚合：2025 RankIC −0.008 / 2026 +0.024，如实呈现）、`/report/daily` 20ms
- `tsc --noEmit` 0 错误；`npm run build` 成功

## 过程修复（源码级）

1. **多文件投影读的列集演进容忍**（本轮最重要的生产加固）：测试环境暴露了 `pl.read_parquet(多文件, columns=)` 在混合列集分区（13 列新分区 + 精简旧分区）下整体崩溃——生产演进（如 P1-2 加 source 列）迟早踩中。`read_parquet_columns()` 统一收敛三处读点。
2. predictions/daily_bar 混存 Date/Datetime → 读取侧统一 cast（与写入口径闭环）。
3. polars `group_by` 迭代键为元组（int(year) TypeError）。

## 已知限制 / 遗留项

1. **回测引擎增强整线遗留**（4.4）：Optuna 未安装（需新增依赖决策）+ 波动率倒数加权/walk-forward/组合优化——建议独立一轮。
2. 因子库的因子当前不自动进入 LGBM 特征空间（评估/对比用途；接线属 §4.3 后续）。
3. `read_parquet_columns` 比整扫慢（逐文件 footer）；`_build_ai_stats` 在日频块 TTL 内仅构建一次，可接受；后续 L3 scan_parquet 谓词下推可再优化。
4. 晨报 PDF 导出未做（HTML/结构化 JSON 已覆盖 Report 页渲染），需 weasyprint 类重依赖时再评估。
5. Ensemble / Optuna 超参搜索 / champion-challenger 自动对比推送（4.5 其余项）遗留。
