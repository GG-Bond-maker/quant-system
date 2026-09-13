# AQP 前后端对齐与数据真实性审计 —— 修复完成报告

> 依据《AQP_前后端对齐与数据真实性审计.md》完成全部 P0 / P1 / P2 修复项。
> 所有修复均通过项目 venv（`backend/.venv`）真实数据验证；前端 `tsc --noEmit` 通过。

## 1. P0 —— 前端造假（4 项，全部完成）

| # | 问题 | 修复 | 验证 |
|---|------|------|------|
| P0-1 | AiPicksPanel「信心指数」为 `pred_score×1400+40` 捏造 | 改为真实横截面排名分位（polars `rank("average")`），响应新增 `rank_pct`/`sample_size`，列名改「全市场分位」 | 真实数据跑通 |
| P0-2 | Screener「今日更新 ✓」硬编码 | 以 `result.date === market.trade_date` 判定，非最新显示 `非最新 · 日期` | tsc 通过 |
| P0-3 | Screener 用本地时间冒充交易日 | 移除 dayjs 本地时间，直接展示后端 `result.date` | tsc 通过 |
| P0-4 | `/ops/lineage` 拓扑硬编码 | 全面重写：pyarrow footer 实扫行数、`model_registry` production 权威、QC 隔离扫描；缺失节点灰色渲染、`topology_source:"static"`+`node_states_scanned:true` 披露 | 实测 daily_bar 134,811 行、模型版本、10 个隔离分区；18→16 边正确衰减 |

## 2. P1 —— 后端半真实（3 项，全部完成）

### P1-5 认证体系（原为悬空JWT）
- `errors.py`：新增 401xx 错误码族 + `StarletteHTTPException` 兜底 handler（裸 `{"detail":...}` 转统一信封，实测 `/boom-auth` → `code 40100`）
- `auth.py`：限流/密码错误走 `AQPException`；登录响应带 `expires_at`；未知用户与密码错误返回一致文案
- 前端：`useAuthStore`（zustand+persist，迁移旧 `AQP_ADMIN_TOKEN`）、`RequireAuth` 路由守卫、独立 `/login` 页、Topbar 显示真实用户/角色/退出；`/settings` 收保护

### P1-6 导出功能接线
- 新增 `POST /export/strategy-backtest`（复用 `_run_strategy` 引擎，导出=页面口径）+ `strategy_backtest_workbook`（trades/nav/meta 三 sheet）
- 前端 `exportApi`（screener/strategyBacktest/backtest）+ Backtest 页「导出 Excel」按钮 + blob 下载（解析 Content-Disposition）
- 拒绝将 Backtest 页错接到 `/export/backtest`（Top-K 引擎≠策略引擎，避免制造新造假）

### P1-7 策略回测合成流动性（本次完成）
- **根因**：`strategy_base.py` 给 `volume/amount = 1e12` 合成值 → 冲击成本恒 0，且停牌日以填充价成交
- **修复**：
  - `_load_strategy_bars` 透传真实 `volume/amount`（原始口径，实测 qfq 与 raw 量额一致）
  - `run_strategy` 构建真实量额索引；缺失日 → NaN → broker 按 `halted` 拒绝（停牌闸门真实生效）
  - 冲击成本按当日真实成交额参与率计算（linear/sqrt 模型）
  - 响应新增 `liquidity{source, impact_cost_included, note}` 披露；ma_cross 原引擎如实标注「未计冲击成本」；前端 Backtest 页头部与导出 meta 同步披露
- **顺带修复存量致命 bug**：`run_strategy` 从未调用 `strategy.on_init()`，donchian/rsi_reversion 首根 bar 即抛 `AttributeError` 且被 EventEngine 容错静默吞掉 → 表现为 0 信号 0 交易。补调 `on_init` 后：donchian 0→62 笔、rsi_reversion 0→17 笔（2024 年 3 标的实测）
- **机制验证**（合成数据）：真实量额下冲击成本如约计提（NAV 1.7591→1.3688）；充足流动性成本为 0；停牌日零成交、不崩溃

## 3. P2 —— 数据质量项（5 项，全部完成）

| # | 问题 | 修复 |
|---|------|------|
| P2-8 | etf.py 固定汇率 7.2、四国混入合计 | `Settings.USD_CNY_RATE` 可配置（.env）；概览拆分：核心统计仅境内有行情 ETF，新增 `overseas{us/jp/kr 计数, us_size_yi, note}`；前端卡片 ⓘ 提示口径 |
| P2-9 | screener 导出 5 个恒 None 列 | 导出改走页面同一 `_screen` 富化链路（name 20/20、industry/行情字段真实）；删除 pred_return/prob_up/confidence 三列；表头对齐页面 |
| P2-10 | 情绪分未标注口径 | 响应加 `kind:"platform"` + `basis`（公式与权重来源）；前端指标名加 ⓘ 提示 |
| P2-11 | `/api-stats` 命名失实 | 改名 `/task-stats`（数据源实为 data_update_log/data_jobs）；前端 API/类型/卡片（「数据任务统计/平均任务耗时」）同步；旧路径返回信封 `code 40400` |
| P2-12 | screener.ts 死代码 backtestApi | 已删除（0 引用确认后） |

其余：F5 回测默认区间改动态；F8 Settings 页假用户资料移除；1.3 死代码清理。

## 4. 验证结论

- `npx tsc --noEmit` → EXIT 0（最终版）
- 后端全部改动模块导入 OK；`/task-stats` 实测 200/code 0（1 个数据点）；`/api-stats` → 40400
- 策略回测、screener 导出、ETF 配置汇率均以真实数据跑通

## 5. 需要知悉的风险与后续

1. **⚠️ git 仓库损坏（存量问题）**：`refs/heads/master` 与 HEAD 指向无效对象（`git fsck` 报 invalid sha1），本次未做任何 git 变更/暂存操作。建议尽快：从工作区完好代码重新 `git init` 或用远端重建，并设置每日提交保护。
2. **配置告警**：`ADMIN_TOKEN 仍为默认值`（后端日志持续告警）——生产前必须在 `.env` 设置 `ADMIN_TOKEN`/`JWT_SECRET`，可顺带配置 `USD_CNY_RATE`。
3. **回测口径变化**：on_init 修复后 donchian/rsi_reversion 从「0 笔」恢复真实交易，且冲击成本按真实量额计提——历史展示的回测结果会与修复前不同，这是纠偏而非回归。
4. **ETF 概览口径变化**：`etf_count`/`total_size_yi` 现仅统计境内 ETF；Redis 存档的前一日快照仍为旧口径，首日「较昨日」差值会出现一次性偏移。
5. 情绪分、rank_pct 等自研口径均已在前端标注，注意后续新指标沿用同一披露模式。
