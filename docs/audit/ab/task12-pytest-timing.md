# Task 12 / 验收总门：全量 pytest 计时记录

- 命令（计划规定）：`cd backend && ./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
- 环境：主工作区 master（最终 commit 见下）、backend/.venv（2026-09-06 重建，
  requirements.txt 锁定版本原样恢复；**torch 未装**——可选依赖，相关测试按设计跳过）
- 目标：<15 分钟

## 三次全量运行记录（2026-09-06 晚间，同一天内完成）

| # | 耗时 | 结果 | 说明 |
|---|---|---|---|
| 1 | 351s | 摘要未捕获 | 测试全部跑完后，pytest 退出清理临时目录阶段被宿主环境的
  批量删除守卫（safe-delete shim）中断（SystemExit 1）——环境问题，非测试问题 |
| 2 | 324.41s | 1 failed, 423 passed, 10 skipped | 失败项
  `test_delist_liquidation.py::test_universe_excludes_rows_after_delist_date`
  （assert 263 == 3）。隔离单跑 4 passed → 顺序依赖 flake |
| 3 | **296.98s（4:56）** | **424 passed, 10 skipped, 0 failed，退出码 0** | ✅ 最终验收运行 |

## 第 2 次失败根因与修复（commit `25b9f20`）

- 根因：`tests/test_auth.py` 与 `tests/test_rbac.py` 是 conftest 会话级隔离机制
  （第九阶段 HIGH-002）落地**之前**的遗留文件，在**模块顶层**直接写
  `os.environ["SQLITE_URL"]/["DATA_ROOT"]`，把整个会话环境从 conftest 的
  临时目录改写到生产侧路径（`data/sqlite/aqp_test.db`、`data/test_parquet`）。
- 触发机制：模块导入按字母序，test_auth 早于 test_delist_liquidation；
  多数测试因 `get_settings` 的 lru_cache 已缓存临时路径而幸免，任何触发
  `cache_clear` 后再读配置的测试即读到被污染的环境—— hence 全量跑失败、
  隔离跑通过。
- 修复：删除两个文件的模块顶层环境变量改写（统一走 conftest 隔离），
  冲突组合（auth+rbac+data_prep+delist+production）复跑 40 passed 验证。
- 附带收益：全量跑不再向生产侧 `data/sqlite/aqp_test.db` 写入。

## 补记（2026-09-07 21:56，torch 重装后复测）

| # | 耗时 | 结果 | 说明 |
|---|---|---|---|
| 4 | **453.37s（7:33）** | **429 passed, 5 skipped, 0 failed，退出码 0** | ✅ torch 2.14.0+xpu 装回后复测：
  此前 10 skipped 中的 5 项 torch 相关测试转为真实执行并全部通过（424 → 429 passed） |

注：复测期间后端服务正在跑盘后增量同步，存在 CPU 争用，纯空闲耗时应更短；
历史"装 torch ≈22min"基线本次未复现（实测 7:33）。

## 结论

- 全量 pytest **4:56**，远低于 15 分钟目标（历史基线：13–17min，装 torch XPU 后 ≈22min；
  本次更快与用例基数变化及 torch 未装有关，如实记录）。
- 10 skipped：torch 等可选依赖与平台相关跳过，符合设计（torch 为可选依赖，
  venv 重建后未恢复，见任务交接说明）。
