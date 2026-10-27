# 后端基线与静态诊断修复

用户已授权修复原审查报告并完成测试。读progress.md和docs/audit-2026-10-27/项目闭环审查.md的验证段。你负责与主链路实现独立的工程问题；使用系统化调试和TDD。

可修改范围：backend/app/api/v1/market.py、research.py；backend/app/data/futures.py、hk_instruments.py、ingest/announcements.py、panels.py、realtime.py、etf.py；backend/tests/中与这些文件或原22个失败对应的非交易/非ML测试。
禁止修改主代理正在修改的api/backtest.py、desk.py、report.py、screener.py、ml/*、orchestrator.py、db/*、trading/*、scripts/*、frontend/*；必要改动请发消息交主代理。

任务：
1. 复核并修复你范围内Ruff F/E9和mypy --ignore-missing-imports诊断；原基线92ruff/166mypy，日志在audit目录。未定义Any/date是真问题，不能仅忽略；类型问题使用边界归一化解决，避免广泛type:ignore。
2. futures.py裸Parquet写入违反atomic contract；etf.py硬编码费率违反single source，真实费用按ETF/股票正确豁免，不机械替常量改变语义。原测试失败test_atomic_write/test_trading_fee_single_source可复现。
3. 原22失败中你范围内的auth/test_api、baostock、同步fakeThread、前端源码字面量守卫、真实panel漂移测试请诊断。只有根因清楚且不触及禁改文件才能修；需碰主文件就反馈。前端源码字面量守卫尽量改为有意义语义/行为断言，不删保护；真实数据诊断不能硬绑某个旧快照分布，不能直接跳过/放宽来绿灯。

一次跑定向tests定位失败；完整全量由主代理统筹，不要并行长跑全套。不得接触生产库或模型，不提交git。报告engineering-result.md记录具体诊断、测试命令结果及需主代理处理的剩余事项。暂不对整个app执行ruff --fix以免改其他人的文件，可对你拥有的文件auto-fix安全unusedimports。
