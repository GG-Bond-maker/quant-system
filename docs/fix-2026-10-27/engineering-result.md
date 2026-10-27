# 工程修复结果（2026-10-28）

## 范围与约束

本子任务依据 engineering-brief.md，随后获主代理新增授权处理 attribution/optimizer/portfolio、datacenter/watchlist/stock/alerts/ops/screening，并修复两个数据质量端点的虚假健康展示。仅修改拥有的生产文件及相关测试；未修改 ML、数据库、交易撮合、前端、scripts 或其他主代理文件。test_api.py 中 qfq 行为夹具先由本子任务调整，随后其 ML/PIT 相关三个用例已交回主代理，停止修改该文件。未提交 Git，未迁移生产数据或模型。

## 根因与修复

1. **静态诊断**：补齐 research/panels 的 Any 与 announcements 的 date；Polars 聚合在数值边界归一，研究日期范围经真实 date 校验；明确混合字典结构的 TypedDict（持仓贡献、流动性、有效前沿、行业暴露、行业分布），避免 object 参与排序/数值运算。None 值先赋局部变量并检查，再 round/int/算术。datacenter 不复用不同类型 snapshot，复权集合明确为变长 tuple。未添加广泛 type:ignore 或关闭检查。
2. **期货缓存**：裸 write_parquet 改走 atomic_write_parquet；补充异常替换时旧缓存字节不变、返回旧快照且无临时残留的真实文件回归。又发现原路径硬编码相对 data/futures_cache，绕过测试 DATA_ROOT；新增隔离路径用例先复现失败后改为 DATA_ROOT/futures_cache。测试均在 tmp_path 内，未写实际行情根目录。现有相对路径旧缓存未自动迁移；新路径取数会使用配置的缓存目录。
3. **费率单源误报**：etf.py 中 0.0005 是合成 IOPV 时序价格步长，并非印花税。改为具名 iopv_step_bps=5.0 / 10_000，保持原数值与语义，未错误引用股票税率。原费率 AST 守卫及其反证测试完整保留并通过。
4. **旧测试根因**：capabilities 路由已要求登录，测试新增匿名 40100 与合法 viewer 成功两条路径；BaoStock 日期契约为 YYYY-MM-DD，修正旧 YYYYMMDD 预期并增加结束日断言；FakeThread 补齐真实 Thread 的 is_alive 协议；pipeline 重建扫描全部源标的，旧夹具受共享 DATA_ROOT 污染，改在 tmp_path 生成其自身完整数据源。
5. **前端守卫**：排版、多账户 API 参数、确认同步新增日期参数均保持原行为，改为对应条件/消费字段的结构断言。情绪 Gauge 已删除，守卫检查不重新引入 Gauge 或 sentiment.score 消费，仍禁止缺值兜 0。累计收益率 null 落成功绿为真实缺陷，先保持红并通知前端负责者；前端修复后两条守卫均通过，没有放宽 null 中性色保护。
6. **真实面板漂移诊断**：旧测试绑定某个历史版本的 max 必须大于 0.25、特定因子互换必须敏感，真实新面板已不满足这些历史观察。改为重放最近真实截面的受控对照：无变化 PSI/KS 必须为 0；仅近期整体平移/缩放，原始 PSI 必须超线而标准化 PSI 仍健康；近期真实因子全体冻结的形状故障，mean/越线占比/最终 degraded 均必须报警，且退化计数非零。保留本机真实数据缺失时原有 skip 条件；存在数据但不可读/不足时明确失败，不新增跳过或放宽报警线。此测试验证统计合同，不预设当下真实市场必然健康。
7. **写端点覆盖**：按照主代理确认的 researcher 权限，将新增 ensemble/accounts/conditional/grid/cancel/changelog 共十个端点纳入原静态注册表（72→82），保留运行时权限一致性、缺凭证/无效凭证及响应信封保护；新增写端点不被视为 viewer 或 admin 专属。

## 数据质量端点的实质修复

### cross-sectional-coverage

删除固定 5208、i%5/i%3 和热图固定扣减。按实际 universe_daily 的 date/symbol 去重行数作为每日分母；价格/成交量可用数与 null/NaN/无穷计数来自实际列。只返回真实存在的最近 N 个日期，不生成工作日。

若有特征源，选一个实际最新修改的版本，按该版本数值因子列逐 cell 计数，缺失股票行的全部因子计空；热图价格、成交量、因子可用率均可从分子/分母复算。source 披露数据集、文件数、特征版本/列与分母定义。缺 universe 返回 unavailable、空数组、摘要 null；缺 feature 仅展示实际量价，feature_status=unavailable。不会猜测股票池之外的标的或缺失交易日。

### quality/radar

删除固定 98.x/99.x、固定 PASS 与 AAA。每标的最新分区末 60 行、最多 200 标的做明确披露的实际抽样：完整性为 OHLCV 有限非空 cell 占比；准确性为完整行中符合正价、OHLC 包络、非负成交量的比例。source 披露样本行数、完整行/有效行、文件/标的数、截断和读取失败。

未独立执行多源对账、同版本漂移诊断、交易日完成 SLA 核验时，对应三维 score=null/status=UNAVAILABLE。维度证据不足时不合成评分或评级（composite_score=null、grade=null）；顶层 partial 或 unavailable。缺源全部未知，不再默认 95 分。已同步前端负责者 nullable 与状态/source 合同。

## 验证证据

所有命令均在 backend 下使用 .venv/Scripts/python.exe；测试由 conftest 隔离 SQLite、DATA_ROOT、模型与日志路径。未运行完整后端套件，遵循主代理统一全量调度。

- 最新组合 `pytest tests/test_futures_atomic_cache.py tests/test_atomic_write.py tests/test_trading_fee_single_source.py tests/test_baostock_adapter.py tests/test_abnormal_api.py tests/test_failed_count_consumer_side.py tests/test_incremental_sync_pipeline.py tests/test_frontend_b9_fixes.py tests/test_frontend_null_direction_leaks_qa3.py tests/test_ops_coverage_real.py tests/test_data_quality_evolution.py tests/test_ops_silent_success_fixes.py tests/test_ops_quality_scan_year.py -q --tb=short`：**154 passed**，31.14s，1 条既有 Starlette/httpx 弃用警告。日志 engineering-final-tests.log。
- `pytest tests/test_monitor_trigger_caliber.py tests/test_write_endpoints_smoke.py -q --tb=short`：**136 passed**，27.98s，3 条既有弃用/空均值警告。日志 engineering-monitor-write-final-tests.log。
- 市场/ETF/panels/research 定向 11 文件，`-m 'not network'`：**147 passed**，106.73s，2 条依赖弃用警告。完整命令及输出见 engineering-data-final-tests.log。
- 扩展模块业务 13 文件（ops/watchlist/datacenter/attribution/portfolio/alerts）：**85 passed**，43.22s，2 条依赖警告。日志 engineering-expanded-final-tests.log；此后 radar/coverage 新实现又由最新 154 组合复验。
- 前端源码保护单独复验：**67 passed**，4.21s，日志 engineering-frontend-guards-final-tests.log；随后亦纳入最新 154 组合。
- 新质量回归：coverage 三条先 **3 failed**，修后 **3 passed**；radar 两条先 **2 failed**，修后与旧质量演进测试共 **9 passed**。日志 engineering-coverage-red-tests.log、engineering-coverage-green-tests.log、engineering-radar-red-tests.log、engineering-quality-green-tests.log。
- 新 futures 路径/原子替换两条先 **2 failed**，修后纳入最新 154 组合全通过。日志 engineering-futures-cache-red-tests.log。
- `ruff check <17个拥有生产文件及修改测试> --select F,E9 --output-format concise`：**All checks passed**。日志 engineering-all-ruff-final.log。
- `mypy --ignore-missing-imports --follow-imports=silent <17个拥有生产文件>`：**Success: no issues found in 17 source files**。日志 engineering-all-mypy-final.log。这是限定范围结果，不代表全 app 全局检查。
- `git diff --check -- <17个拥有生产文件>`：退出 0。仅 hk_instruments.py 的 LF/CRLF 提示，没有 whitespace error。

## 主代理统筹/边界

- 初次完整 test_api 定向在当时新 PIT 契约下为 22 passed/3 failed：predict 将刚用尾日期训练的模型用于同一历史期；两个 backtest 测试种子缺统一信号元数据。已逐名通知主代理，并按其要求交回处理。不能通过虚构可用日期或移除时点保护改绿。本报告不代表这些用例最终状态。
- 全 app mypy/Ruff 与完整后端套件由主代理统一复验；本子任务没有触碰其 ML/db/交易实现。
- radar 的独立多源一致性、特征稳定性、SLA 及时性尚无证据链，当前如实不可用；没有声称这三个能力已经完成。
- coverage 的总体分母只覆盖本地持久化 universe 快照，不能据此推断整个上市市场覆盖率。特征版本、日期与分母均公开可追溯。
- futures 旧相对缓存未迁移，新配置缓存目录已实现原子写入与测试隔离；不得将后续数据源不可达理解成已获得真实历史。
