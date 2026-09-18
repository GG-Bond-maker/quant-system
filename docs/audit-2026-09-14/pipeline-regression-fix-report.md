# Pipeline 回归修复报告

## 结论

本次失败不是生产数据质量门禁回归，而是 `test_pipeline.py` 的测试夹具隔离不足。
生产侧 `validate_daily_bar()` 的 `|pct| <= 45%` 校验保持不变。

## 根因

流水线测试原先复用全套件常用标的 `600519.SH`，并在会话级共享的临时
`DATA_ROOT` 中覆盖 `daily_bar` / `daily_bar_hfq` 年分区。API、行情面板等前序
测试也会写同一标的同一年分区，因此流水线读取结果受套件顺序和残留分区影响。
异常行会在预期的 `build_features` 失败点之前触发涨跌幅质量门禁，使
`test_pipe_fail_fast` 和 `test_pipe_idempotent` 失败。

此外，流水线编排测试只需使用固定离线种子，不能让 `update_daily` 调用真实行情
抓取覆盖种子。涨跌幅字段采用收益率小数口径，固定为 `0.005`（0.5%），不是
`0.5`（50%）。

## 改动

仅修改测试代码：

- 流水线测试改用模块专属标的 `605888.SH`，不再与其他模块共享 `600519.SH`；
- 每条测试 setup / teardown 都删除该专属标的的 raw/hfq 分区；
- 每例重新生成确定性模型历史和验证日种子，验证日最后写入；
- `update_daily` 使用离线计数替身，不触发真实网络抓取；
- 保留真实 `validate`、`build_features`、`infer` 和 `screener_dump` 执行路径；
- 未修改任何业务源码，未削弱异常涨跌幅门禁。

## 最小回归结果

```powershell
backend\.venv\Scripts\python.exe -m pytest -q `
  backend\tests\test_pipeline.py::test_pipe_fail_fast `
  backend\tests\test_pipeline.py::test_pipe_idempotent
```

结果：`2 passed`，退出码 0。

```powershell
backend\.venv\Scripts\python.exe -m pytest -q `
  backend\tests\test_api.py backend\tests\test_pipeline.py
```

结果：退出码 0。该顺序覆盖“前序共享数据模块运行后再执行 pipeline”的污染场景。

完整离线基线按主任务统一验收阶段执行：

```powershell
backend\scripts\run_offline_tests.ps1
```
