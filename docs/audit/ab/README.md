# 回测 A/B 对比证据目录

每个回测类整改任务在本目录留三个文件：
- `taskN-before.json` / `taskN-after.json`：`backend/scripts/ab_backtest.py` 固定参数 runner 的输出（修复前 / 修复后）
- `taskN-AB.md`：metrics 对比表 + 差异来源解释

协议：`docs/superpowers/plans/2026-09-05-整改计划.md`「执行约定」。
固定参数（除非任务明确声明）：start=2025-08-08, end=2026-08-28, top-k=10, init-cash=1_000_000, daily, equal。
