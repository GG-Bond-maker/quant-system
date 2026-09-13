# 测试报告 — Sprint 4（第 2 轮终）

> QA：严过关（兼）｜ 日期：2026-09-05 ｜ 被测：因子库 / 因子检测报告 / 分年稳定性 / 晨报增强
> ROUTE: NoOne（第 1 轮 3 个失败均定位为跨测试数据契约与 polars 多文件读的列集演进缺陷，修复后全绿）

## 用例统计（最终全量回归）

| 项 | 结果 |
|---|---|
| 通过 | **381** / 失败 **0** / 跳过 5（E2E 门控） |
| 新增 | `tests/test_lab_factor.py` 5 用例 |
| 前端 | `tsc --noEmit` 0 错误；`npm run build` 成功 |

## 新增用例覆盖

| 用例 | 覆盖点 |
|---|---|
| test_factor_report_structure | 报告内核：IC 时序 + 5 分组净值/年化 + 1/5/10/20 衰减 + 换手率值域 |
| test_factor_save_list_delete | 入库（真实截面评估摘要随档）/ 重名拒绝 / 非法表达式拒绝 / 列表 / 删除 |
| test_report_score_shift_section | 榜单变动（新进/跌出，快照表两期 diff）+ 分数迁移（top-K 重合率） |
| test_report_build_includes_section | 整报生成含新节且不因 monitor/paper 缺失失败 |
| test_lab_yearly | 分年聚合：2025/2026 齐全、RankIC∈[-1,1]、命中率∈[0,1]、n_days≥10 |

## 首轮失败与修复（3 例，均转化为生产加固）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | `test_factor_save_list_delete` ModuleNotFoundError: app.api.db | studio.py 相对导入层级错（`..db` → 应为 `...db`） | 修正导入 |
| 2 | `test_lab_yearly` int(tuple) TypeError | polars `group_by` 迭代键是元组 | 解包 `year_t[0]` |
| 3 | 全量回归 3 例失败：`projection index 9 out of bounds` / Date vs Datetime | **polars 多文件投影读要求各分区 schema 全一致**——测试混合列集分区暴露了生产演进风险（如 P1-2 加列后同类崩溃迟早发生） | 新增 `parquet_store.read_parquet_columns()`（逐文件 footer 校验 + 缺列跳过 + date 归一）并收敛 `_heat_from_local`/`_build_ai_stats`/`lab yearly` 三处读点 |

## 线上验证（生产端口，真实数据）

- `/research/lab/yearly`：2025 RankIC **−0.0078**（98 IC 日）/ 2026 **+0.0241**（157 IC 日）——弱负年份如实呈现，热力图有真实信息量
- `/studio/factors` 17ms；`/report/daily` 20ms；晨报 webhook 已配置即可分发
- Report 页下期日报（19:35 后触发或手动 `POST /report/daily/generate`）将包含「榜单与模型」新节

## 遗留问题

1. 回测增强（Optuna/walk-forward/组合优化）整线遗留——需要新增依赖的决策。
2. 因子库因子暂不进入 LGBM 特征空间（研究对比用途）。
3. `read_parquet_columns` 逐文件 footer 读在 ~13k 文件数据集上比整扫慢数秒；日频块 TTL 缓存（至次日盘后）吸收了该成本，L3 scan_parquet 下推可再优化。
