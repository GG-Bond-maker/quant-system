# P5 — 披露口径端到端落地矩阵（静默截断 / 静默降级 唯一权威清单）

- **审计员**：披露口径专项审计员（AQP 审核团）
- **日期**：2026-09-21
- **仓库根**：`D:\Python_Project\Alpha Quant Platform`
- **主题**：「后端是否产出披露字段」×「前端是否消费」的横切审计，产出「静默截断 / 静默降级」唯一权威清单
- **纪律**：只读。源码 / 测试 / 生产数据均未修改；探针与临时 SQLite/parquet 全部落在 `%TEMP%`；未触发任何写端点（唯一 POST 为 `ops/quality-scan`，见 §1.4 说明）

---

## 1. 实测方法与证据

### 1.1 探针

| 探针 | 位置 | 手段 |
|---|---|---|
| #1 | `%TEMP%\aqp_p5_probe.py` → 输出 `backend/.tmp_testrun/p5_probe_out.txt` | `fastapi.testclient` 直连 ASGI，**不进入 `with TestClient`** ⇒ lifespan 不执行 ⇒ autoSync / evening_routine / alert_scheduler / overview warmer 全部不启动，无任何后台写入。`raise_server_exceptions` 默认（#1）与 False（#2/#3）两种都用了 |
| #2 | `%TEMP%\aqp_p5_probe2.py` → 输出 `backend/.tmp_testrun/p5_probe2_out.txt` | 同上 + monkeypatch `ops.get_settings` / `datacenter.get_settings` / `alerts.get_settings`（模块级 global）指向 `%TEMP%` 构造的 DATA_ROOT / SQLite；调 `_check_data_health` / `_scan_dataset` / `_scan_all_datasets` 纯函数 |
| #3 | `%TEMP%\aqp_p5_probe3.py` → 输出 `backend/.tmp_testrun/p5_probe3_out.txt` | 残余口径核对 |

认证：从仓库根 `.env` 读取真实 `ADMIN_TOKEN` 作为 Bearer（**未打印、未落盘**），故所有受 RBAC 保护的端点都按 researcher/admin 真实取数。

### 1.2 带 limit 读端点实测：真实 `data` keys 与截断字段有无

`limit=1`（或最小合法值）与 `limit=-1` 各打一遍，打印 `sorted(body['data'].keys())`：

| # | 端点（实测 URL） | `data` 真实 keys | 截断披露字段 |
|---|---|---|---|
| 1 | `GET /api/v1/datacenter/instruments?asset_type=stock&limit=1` | `['items','total']` | **无**（且 `total` = `1`，见 §1.3） |
| 2 | `GET /api/v1/datacenter/instruments?asset_type=stock&limit=-1` | `['items','total']` | **无**（`items`=5552, `total`=5552） |
| 3 | `GET /api/v1/datacenter/logs?limit=3` | `['items']` | **无** |
| 4 | `GET /api/v1/datacenter/logs?limit=-1` | `['items']` | **无**（`out[-limit:]` ⇒ 悄悄丢掉第 1 条） |
| 5 | `GET /api/v1/datacenter/quality?limit=3` | `['affected_symbols','checked','items','total_missing_days']` | **无**（间接可见：`items`=3 / `affected_symbols`=1731） |
| 6 | `GET /api/v1/datacenter/quality?limit=-1` | 同上 | **无**（`items[:-1]` ⇒ 1730，比 `affected_symbols` 少 1） |
| 7 | `GET /api/v1/datacenter/datasets` | `['items']` | **无** |
| 8 | `GET /api/v1/datacenter/overview` | `['akshare_health','akshare_message','covered_total','covered_with_data','daily_bar_range','dataset_count','disk_usage_percent','from_cache','last_sync','last_sync_source','storage_bytes','storage_gb']` | 无（`dataset_count` 与 `/datasets` 矛盾，见 §3.2） |
| 9 | `GET /api/v1/alerts/events?limit=1` | `<list len=0>`（信封 `['code','data','message','trace_id','ts']`） | **无** |
| 10 | `GET /api/v1/notify/recent` | `<list len=0>` | **无**（进程内 `_RECENT_MAX=20` 硬顶） |
| 11 | `GET /api/v1/desk/orders?limit=1` | `<list len=1>` | **无** |
| 12 | `GET /api/v1/stock/search?q=6&limit=1` | `<list len=1>` | **无** |
| 13 | `GET /api/v1/portfolio/search?q=6&limit=1` | `<list len=1>` | **无** |
| 14 | `GET /api/v1/market/index/kline?code=sh000001&limit=30` | `['bars','code','name']` | **无**（且该端点**无鉴权**，见 §5.4） |
| 15 | `GET /api/v1/screener/stocks?...&page=1&page_size=1` | `['as_of','basis','basis_desc','basis_fields','degraded','dir_applied','from_cache','items','options','page','page_size','quote_coverage','sort_applied','source','stale','total','trade_date']` | ✅ 有 `total`(=2492) + 分页字段 |
| 16 | `GET /api/v1/alerts/health` | `['checked_at','degraded','detail','last_ok_at','reason','snapshot_date']` | ✅ 后端齐备（前端 0 调用，见 §4.2） |
| 17 | `GET /api/v1/datacenter/sync/status` | `['cancelled','completed_count','current','done','elapsed_ms','error','failed_count','logs','mode','percent','running','started_at','task_id','total']` | 有 `failed_count`（前端 0 消费） |
| 18 | `GET /api/v1/etf/overview` | `['data_freshness','from_cache','prev','today']` | 有 `data_freshness` |
| 19 | `GET /api/v1/ops/lineage` | `['benchmark_date','degraded_note','edges','generated_at','missing_nodes','node_states_scanned','nodes','note','topology_source']` | ✅ 模板级齐全 |
| 20 | `GET /api/v1/ops/dag` | `['benchmark_date','note','recent_jobs','stages']` | 无 `truncated`（`recent_jobs` 是 `LIMIT 10`） |
| 21 | `POST /api/v1/ops/quality-scan` | `['by_kind','dataset','n_errors','n_issues','rows_scanned','samples','symbols_scanned','worst_symbols','year']` | **无**（`sym_dirs[:200]`、`samples[:50]`、`worst_symbols[:10]` 三处硬截断全无披露） |

### 1.3 `total` 字段名不副实（实测）

```
GET /datacenter/instruments?asset_type=stock&limit=1  -> items_len=1     total=1
GET /datacenter/instruments?asset_type=stock&limit=-1 -> items_len=5552  total=5552
```

`total` 由 `datacenter.py:1015` 写作 `"total": len(rows)` —— 即**本次返回条数**，而非可用总数（真实 5552）。任何以 `total` 作为「总数」的消费方都会得到与 `limit` 同步缩小的数字，是**结构性误导**，比"没有 total"更糟（这也是本报告 A6 与 S12 的核心证据）。

### 1.4 `ops/quality-scan` 的 POST 例外说明

该端点是 `POST`（`ops.py:29`），但**不是写端点**：函数体只调用 `data.quality.validate_partition`（`quality.py:436-460`，纯函数），模块 docstring 明示「本模块只负责检出，不做静默删除」；不写 SQLite、不写 parquet、不写 quarantine。任务书第 5 条明确要求对该端点造超限输入实测，故执行之，并用 monkeypatch 把 `DATA_ROOT` 指向 `%TEMP%` 构造目录，**未触碰生产数据**。

### 1.5 前端引用计数（`frontend/src`，93 个 `.ts/.tsx` 全量递归）

任务书口径：`rg "truncated|failed_count" frontend/src` = **0** ✅（与 `B9c-frontend-ops.md` 一致，本次独立复现）

| 字段 | 引用数 | 字段 | 引用数 | 字段 | 引用数 |
|---|---|---|---|---|---|
| `truncated` | **0** | `recent_truncated` | **0** | `recent_limit` | **0** |
| `supplement_limit` | **0** | `failed_count` | **0** | `failed_jobs` | 1（仅渲染，无截断语义） |
| `universe_scope` | **0** | `n_gap_invalid` | **0** | `xsec_demean` | **0** |
| `flow_status` | **0** | `data_stale` | **0** | `close_basis` | **0** |
| `feature_basis` | **0** | `retired` | **0** | `orphan_dates` | **0** |
| `from_snapshot` | **0** | `data_warnings` | 1（**仅类型声明**） | `worst_symbols` | 1（仅类型声明） |
| `quote_coverage` | 1（仅类型声明） | `basis_fields` | 1（仅类型声明） | `label_price_basis` | 1（仅类型声明） |
| `missing_nodes` | 1（仅类型声明） | `data_freshness` | 5 | `reject_reason` | 2（真渲染） |
| `basis_desc` | 3（真渲染） | `confidence_basis` | 4（真渲染） | `adjust_status` | 3（真渲染） |
| `degraded_note` | 3（真渲染） | `symbols_scanned` | 2（真渲染） | `lag_trading_days` | 4（真渲染） |

> 注意：`Types-only` 的字段在 TS 里"被消费"仅止于类型层，React 渲染路径完全不读，用户看不到 —— 本报告一律记为 **⚠️ 后端有前端无**，不记 ✅。

---

## 2. 矩阵 A — 行数 / 条目截断

| 场景 | 后端端点/函数(文件:行) | 后端是否产出该字段 | 字段名/示例值 | 前端消费点 | 用户能否看到不完整 | 结论分类 | 建议 |
|---|---|---|---|---|---|---|---|
| A1 预警事件列表截断 | `GET /alerts/events`；`alerts.py:244-266`（`limit=Query(50,ge=1,le=200)`，`stmt.limit(limit)`） | **否** | 无。响应是裸 list，信封只有 `code/data/message/trace_id/ts` | `api/alerts.ts:69-70`；`pages/Alerts/index.tsx:200,212` | 不能 | ❌ **两端都无=静默** | 改为 `{items, returned, limit, has_more}`；或加 `X-Total-Count` 头 |
| A2 **流水线失败数被截断**（正面模板） | `alerts._check_data_health`；`alerts.py:481-491`，常量 `alerts.py:63,67` | **是** | `recent_truncated:true` / `recent_limit:5` / `truncated:true` / `supplement_limit:20`。**实测**（100 FAILED + 25 RUNNING）：`{"metric":"pipeline","failed_jobs":25,"last_error":"boom-99","recent_truncated":true,"recent_limit":5,"truncated":true,"supplement_limit":20}`；基线（2 FAILED+1 RUNNING）时四个字段**全部不出现**（条件披露，正确） | `pages/Alerts/index.tsx:169` 只渲染 `×${p.failed_jobs}`；`truncated` 全前端 0 引用 | **不能**（看到 `×25`，不知道还有 75 条被截） | ⚠️ **后端有前端无** | 前端 EventSummary 追加 `（已截断，仅显示前 25 / 20）` 徽标；把四字段加入 `api/alerts.ts` 的 `AlertRuleParams` 同级 payload 类型 |
| A3 **score_topk 数据源降级留痕** | `GET /alerts/health`；`alerts.py:133-141` + `_TOPK_HEALTH` `alerts.py:313-320`、`_mark_topk_health` `alerts.py:324-349` | **是** | `degraded:false` / `reason:null` / `detail` / `snapshot_date` / `checked_at` / `last_ok_at`（实测返回全部 6 键） | **无**：`api/alerts.ts` 无 `health()`；全前端 `alerts/health` 调用点 = 0 | 不能 | ⚠️ **后端有前端无** | 预警页顶部加「预警引擎数据源健康」灯；`reason!==null` 时显式文案「本规则本轮跳过」 |
| A4 质量扫描标的数硬截断 | `POST /ops/quality-scan`；`ops.py:44-52`（`sym_dirs[:200]`） | **否** | 无。**实测**：构造 250 个 `symbol=` 分区 → `symbols_scanned=200`、`rows_scanned=40000`，无任何截断键 | `pages/DataQuality/index.tsx:137-138` 渲染 `{scan.symbols_scanned} 只`；`api/production.ts:101` 仅类型 | 不能（**且被误导**：UI 写"200 只"，真实 250） | ❌ **两端都无=静默** | 加 `symbols_total` / `dirs_scanned` / `dirs_truncated:true` / `scan_limit:200`；UI 显示「200 / 250 只（上限 200）」 |
| A5 QC 问题样本截断 | `ops.py:77-78`（`worst_symbols[:10]`、`samples[:50]`） | **否** | 无 | `samples`：`pages/DataQuality/index.tsx:176` 渲染；`worst_symbols`：仅 `api/production.ts:104` 类型声明，0 渲染 | 不能 | `samples` ❌ 静默；`worst_symbols` ⚠️ 后端有前端无 | 加 `n_issues_shown` / `samples_truncated`；`worst_symbols` 补渲染或删字段 |
| A6 标的列表 limit + 假 `total` | `GET /datacenter/instruments`；`datacenter.py:997-1017`（`LIMIT ?`；`"total": len(rows)` 在 **`:1015`**） | **否**（`total` 是伪总数） | `total = len(rows)`。**实测** `limit=1 → total=1`；`limit=-1 → total=5552`（SQLite `LIMIT -1` = 全量） | `api/datacenter.ts:53-55`；`pages/DataCenter/index.tsx:269`（取前 50 只做抓取预览） | 不能 | ❌ **两端都无=静默** | `total` 必须来自独立 `SELECT COUNT(*)`；加 `truncated` |
| A7 同步日志条数截断 | `GET /datacenter/logs`；`datacenter.py:592-619`（`out[-limit:]`） | **否** | 无 | `pages/DataCenter/index.tsx:404,410,510-511` | 不能 | ❌ **两端都无=静默** | 加 `scanned_lines` / `limit_applied`；`limit<=0` 显式 40000 而非 `out[1:]` |
| A8 缺漏标的表分页 | `GET /datacenter/quality`；`datacenter.py:551-560`（`data["items"][:limit]`） | **部分**（有 `checked`/`affected_symbols`/`total_missing_days`，无 `truncated`） | `checked:2499` / `affected_symbols:1731` / `total_missing_days:5020`。**实测** `limit=3 → items=3`；`limit=-1 → items=1730`（比 `affected_symbols` 少 1，静默 off-by-one） | `pages/DataCenter/index.tsx:796-799`（渲染 `checked`/`total_missing_days`）、`:855`（`items.length>=50` 时"展开全部"→ `limit=10000`） | 部分能（汇总可见，逐条截断不可见） | ⚠️ 后端有（汇总）前端部分无；`affected_symbols` + `truncated` 缺失 | 加 `truncated:true` / `limit_applied` / `n_items_total`；修正 `limit<=0` 语义 |
| A9 通知回放截断 | `GET /notify/recent`；`notify.py:156-160` + `core/events.py:17-18`（`_RECENT_MAX=20`，`del _recent[:-20]`） | **否** | 无，裸 list | `api/notify.ts:26`；`stores/useNotifyStore.ts:66-68` | 不能 | ❌ **两端都无=静默** | 返回 `{items, max_kept, dropped}`；顶栏铃铛在 `dropped>0` 时提示"仅保留最近 20 条" |
| A10 母单列表截断 | `GET /desk/orders`；`desk.py:210-242`（`.limit(limit)`，默认 50） | **否** | 无，裸 list | `api/production.ts`（`orders()` 不带 limit）；`pages/OrderDesk/index.tsx:61,403,430` | 不能 | ❌ **两端都无=静默** | 同 A1；当前库仅 6 笔故未暴露，属**潜在**静默 |
| A11 代码搜索截断 | `GET /stock/search`；`stock.py:79-95`（`.limit(limit)`，默认 20） | **否** | 无，裸 list | `components/Topbar.tsx:146`（`stockApi.search(text, 8)`） | 不能 | ❌ **两端都无=静默** | 加 `has_more`；Topbar 显示"还有更多结果" |
| A12 组合资产搜索截断 | `GET /portfolio/search`；`portfolio.py:135-150`（`LIMIT ?`，默认 10） | **否** | 无，裸 list | `api/portfolio.ts`（`portfolioApi`） | 不能 | ❌ **两端都无=静默** | 同 A11 |
| A13 ETF 热榜/资金流 limit | `GET /etf/hot` `etf.py:313-321`；`GET /etf/flow` `etf.py:470-477` | **否** | 无 | 见 A14 | 不能 | ❌ **两端都无=静默** | 与 `etf/scale` 统一走 `data_freshness` + `total` |
| A14 ETF 列表/热榜无降级兜底 | `GET /etf/list` `etf.py:264-311`；`/etf/hot`；`/etf/flow` | **否** | **实测**外部源失败 → 未捕获 `RuntimeError` → 全局兜底 `code=50000 "系统暂不可用，请稍后重试"`，`data=null`。同模块的 `/etf/scale`、`/etf/performance`、`/etf/detail` 却有结构化 `data_freshness` | `pages/Etf/OverviewCards.tsx:127`、`PerformanceChart.tsx:71` 渲染 `data_freshness.reason`；`etf/list` 无载荷可渲染 | 能看到"失败"，但看不到**为什么**与**有没有旧值可用** | ⚠️ 同模块口径不一致 | `/etf/list`、`/etf/hot`、`/etf/flow` 补 `data_freshness`/`status`/`reason`，禁止裸 50000 |
| A15 榜单分页（正面对照） | `GET /screener/stocks`；`screener.py:566+` | **是** | `total:2492` / `page` / `page_size` / `sort_applied` / `dir_applied` | `pages/Screener/StockList.tsx` | 能 | ✅ **对齐** | 作为 S1 类端点的参考实现 |
| A16 选股 Excel 导出无口径元数据 | `GET /export/screener`；`export.py:18-56` | **否** | **实测**响应头只有 `content-disposition: filename="screener_20260917.xlsx"`；工作簿内无 basis/口径 sheet、无 `truncated` | `pages/Screener/index.tsx:338-342` | 不能（拿到 xlsx 不知道是日终还是实时口径、被 `top_k` 截了多少） | ❌ **两端都无=静默** | 加首个"口径"sheet（basis/basis_desc/as_of/top_k/数据源）；文件名带 basis |

---

## 3. 矩阵 B — 部分失败

| 场景 | 后端端点/函数(文件:行) | 后端产出该字段 | 字段名/示例值 | 前端消费点 | 用户能否看到不完整 | 结论分类 | 建议 |
|---|---|---|---|---|---|---|---|
| B1 **分区读失败被吞** | `_scan_dataset`；`datacenter.py:120-197`，失败计数 `:158-184`，仅 `logger.warning` | **只 warn 日志** | **实测**：2 个分区（1 好 1 坏）→ 返回 `{"symbols":2,"rows":200,"bytes":4612,...}`，**无 `failed`/`skipped` 键**；日志 `"[datacenter] daily_bar 扫描完成：1/2 个文件成功，1 个被跳过"` 只在服务端 | `pages/DataCenter/index.tsx:713-737`（渲染 rows/symbols/adjust_status） | 不能 | ❌ **两端都无=静默** | 返回体加 `files_total` / `files_failed` / `partial:true`；`symbols` 应区分"有分区的"与"可读的" |
| B2 数据集从清单消失 | `GET /datacenter/datasets`；`datacenter.py:513-545`（`if not st: continue`） | **否** | 无。`_scan_dataset` 返回 `None` ⇒ 该行被 `continue` 删除，用户看不到"少了哪个数据集"；`rows==0` 也返回 `None`（`datacenter.py:186-187`） | 同上 | 不能 | ❌ **两端都无=静默** | 加 `dropped_datasets:[{key,reason}]` |
| B3 **同页两个端点数据集数矛盾** | `GET /datacenter/overview`（`_manifest_dataset_summary`，`datacenter.py:274-310,449`）vs `GET /datacenter/datasets`（`_scan_all_datasets`，`:259-266`） | **否**（两个口径都无披露） | **实测**：`overview.dataset_count=3`，`datasets.items.length=8`（`daily_bar/daily_bar_qfq/daily_bar_hfq/features/predictions/screener/universe_daily/announcements`）。另 `covered_total=5552` vs `covered_with_data=2499` | `types/datacenter.ts` + `pages/DataCenter/index.tsx:402-409` 两处同页渲染 | 不能（同页两个数字自相矛盾且无解释） | ❌ **两端都无=静默** | `overview` 的 `dataset_count` 必须来自与 `/datasets` 同源扫描（或注明 `dataset_count_source:"manifest"`） |
| B4 质量扫描单文件损坏 → 整次失败 | `POST /ops/quality-scan`；`ops.py:52-64`（`pl.read_parquet(files[0])` 无 try） | **否** | **实测**：损坏文件落在前 200 个分区内 → `code=50000 "系统暂不可用，请稍后重试"`、`data=null`。**无任何部分结果** | `pages/DataQuality/index.tsx:110-112`（`catch` → `setErr('扫描失败')`），整块卡片消失 | 能看到"失败"，但已扫完的 199 个分区结果被整块丢弃 | ⚠️ 全有或全无（非静默，但无部分失败契约） | 逐分区 try/except（对齐 `_scan_dataset` 的按文件隔离）；响应加 `files_failed` / `partial:true`；损坏分区至少回传列表 |
| B5 血缘单文件损坏 | `_scan_parquet_dataset`；`ops.py:98-125`（`:113-117` 单文件 `except` 只 debug 日志） | **只 warn(debug) 日志** | 无字段。`_latest_partition_date` 失败也仅 debug 日志（`ops.py:144-146`）；`_sqlite_count` 失败返回 `None`（`:165-167`）、`_model_stat` 失败保持 `exists:false`（`:198-200`） | `pages/DataQuality/index.tsx:60-97` | 不能（行数偏小但 `status:"ok"`） | ❌ **两端都无=静默** | 节点加 `files_failed`；`status` 增加 `partial` 档 |
| B6 **同步部分失败计数** | `GET /datacenter/sync/status`；`services/sync_service.py:118`（`"failed_count": len(self.failed)`） | **是** | **实测** `failed_count:0`（与 `completed_count` 并列） | **无**：全前端 `failed_count` 引用 = 0；`pages/DataCenter/index.tsx:672` 只用 `completed_count` | 不能 | ⚠️ **后端有前端无** | 停止/完成后若 `failed_count>0` 显示"成功 N / 失败 M，可断点续传" |
| B7 同步中断原因 | `GET /datacenter/sync/status`（`error`/`cancelled`/`logs`）；`datacenter.py:719-723` | **是** | `error:null` / `cancelled:false` / `logs:[]` | `pages/DataCenter/index.tsx:675-677`（`上次任务失败：{sync.error}`）、`:510`（日志） | 能 | ✅ **对齐** | — |
| B8 监控数据不足 / 重训被中断 | `GET /monitor/health`；`monitor.py:19-32` + `ml/monitor.py` | **是** | **实测**：`state:"degraded"`、`drift_state:"degraded"`、`ic_state:"healthy"`、`retrain.status:"aborted"`、`retrain.aborted_reason:"进程在重训期间退出（启动时回收）"`、`trigger:"PSI=3.3051>0.25（g1_ma_gap_250 漂移最大）"`、**`ok:true`** | `components/FactorHealthCard.tsx:14-16,20-27`（渲染 `state`）；`retrain` 仅 `api/monitor.ts:46,51` 类型声明，0 渲染 | 部分能（状态灯能，重训中断原因不能） | ⚠️ 后端有前端部分无 + 字段自相矛盾 | 前端渲染 `retrain.aborted_reason`；`ok` 与 `state` 必须同源（`state==='degraded'` 时 `ok` 不得为 true） |
| B9 日报降级 | `GET /report/daily`；`report.py:323`（`mon.get("state")=="degraded"` 时注入降级文案）+ `report.py:453-466`（路由） | **是**（嵌入 markdown） | **实测** `data.report.keys=['date','generated_at','markdown','sections','tips']`；降级文案在 `report.py:323` 注入 markdown | `pages/Report/index.tsx`（渲染 markdown） | 能（间接） | ✅ **对齐（经 markdown）** | 另在 `report` 顶层加结构化 `degraded_sections:[]`，便于程序化消费 |
| **B8d** **图传导口径（`g1_*`）的邻接 universe 无 API 字段** | `app/orchestrator.py::step_build_features` → `app/ml/graph.py::resolve_universe/write_graph_lineage`（落 `features/version=<v>/universe_snapshot.json` + `graph_lineage.json`） | **是（仅文件血缘 + 步骤失败）** | `graph_lineage.json`：`fingerprint` / `n_nodes` / `edges_digest` / `n_panel_symbols_not_in_universe` / `graph_drift_detected` / `graph_refrozen`；漂移时 `step_build_features` 抛 `ValueError`（文案含"示例标的 / 恢复建议 / 显式授权开关"） | **无**（`GET /ops/dag` 只给步骤状态；前端零消费） | **部分**：失败可见且给了行动指引，但"这批预测用的哪套图"要读文件才看得到 | ⚠️ **部分对齐** | 建议把 `graph_lineage` 摘要挂到 `GET /monitor/health` 或 `/ops/dag` 的 `features` 步骤详情（沿用 B8b 的做法） |
| **B8c** ~~**组合约束与风险口径未披露**~~ | `POST /backtest/run` → `weight_cap_info`；`POST /research/optimize` → `weight_cap_info`/`expected_returns`；`POST /portfolio/backtest` → `weights_normalization`；`GET /stock/panels`（风险块）→ `rf_annual`/`benchmark` | **是**（修复后） | **修复前**：①`weight_cap` 不可行（`n·cap<1`）时静默返回 `Σw=n·cap`（N=10/cap=5% ⇒ 只投 **50%**＝一半现金）而 `status=ok`、`fallback=false`，**无任何字段可查**；②**默认 `weighting="equal"` 分支从不调用 `apply_weight_cap`** ⇒ 用户设的上限被静默忽略（实测 `max w=0.25>cap=0.2`）；③`/portfolio/backtest` 接受 `Σw∈[0.99,1.01]` 却**不归一**（Σw=0.995 ⇒ 0.5% 永久现金、drift 恒非零）；④`mvo` 从不收到 `expected_returns` ⇒ μ≡0、λ 完全无效（实测 L1=0）却仍叫"均值-方差"；⑤个股风险卡 `rf=0` 而组合页 `rf=2%`、`benchmark` 硬编码"沪深300"。<br>**修复后（第 13 轮）**：`weight_cap_info{cap,n_assets,feasible,cap_enforced,invested_ratio,max_weight,max_invested_ratio,min_feasible_cap,note}`（引擎侧汇总**取最坏**并区分 `half_invested_rebalances` 与 `cap_unenforced_rebalances`）；`weights_normalization{input_sum,factor,note}`；`expected_returns{basis:"unavailable",risk_aversion_effective:false,note}`；风险块 `rf_annual` + `benchmark`（默认 `null`，由 `panels.py` 传真实标识）。**行为不改**（`apply_weight_cap` 逐字不变、组合页数字逐位不变）：`n·cap<1` 时强制 `equal` 执行上限会退化成半仓，比"超限满仓"更糟 | `pages/Research/index.tsx`（上限告警 `role="alert"` + 权重合计 + 下拉改"MVO（μ 不可用 ⇒ 实为最小方差）"）、`api/research.ts`（类型）；`pages/StockDetail/index.tsx`（风险卡 note 增"夏普按年化无风险利率 x% 计算"）、`types/stock.ts`。`/backtest/run` 与 `/portfolio/backtest` 目前**无对应前端调用方**（后端字段供程序化消费者与后续接线） | 能（"半仓/超限/未归一/μ 缺失/rf 口径"五件事都可读） | ✅ **对齐** | — |
| **B8b** ~~**漂移判定口径未披露**~~ | `GET /monitor/health` → `snap["psi"]/["ks"]/["history"]`；`ml/monitor.py` | **是**（修复后） | **修复前**：只给 `psi.max`（跨标的**逐行池化**原始值）与 `ks.n_over_crit`，**不说明口径** ⇒ 前端/AI 日报把"水平型因子随趋势的必然平移"读成"因子坏了"（真实快照 `psi.max=3.3051`、`ks.n_over_crit=85/85`），且 `history.std_ic`（取自每日 ~120 只时代）未标注池宽。<br>**修复后（第 12 轮）**：`psi.basis="xsec_standardized"`（判定口径）+ `psi.raw.{basis,mean,max,top}`（池化原始值，仅披露）+ `psi.note`；`ks.basis/over_crit_ratio/crit_effective/cross_section_median` + note 明示"池化样本量达万级 ⇒ 只作强度读"；`history.std_ic_raw/sigma_basis/pool_ratio/n_symbols_median_hist/n_symbols_median_recent/note`；`thresholds.psi_basis/ic_sigma_basis/raw_psi_degraded_reference`；`state_log[].psi_max_raw/psi_basis`。**兼容**：`psi.basis != "xsec_standardized"`（历史/第三方快照）⇒ 记 warning 并回退按 raw 判定，旧载荷语义不被静默改写 | `components/FactorHealthCard.tsx`（标签改"PSI max（截面标准化·判定口径）"，增显 `raw.max`、KS 超限比与单日临界尺度、σ 池宽折算说明）+ `api/monitor.ts` 类型；AI 日报 `api/v1/report.py:308-320` 两条文案同步 | 能（口径、原始值、池宽三件事都可读） | ✅ **对齐** | — |

---

## 4. 矩阵 C — 降级（外部源 / Redis / 陈旧 / as_of）

| 场景 | 后端端点/函数(文件:行) | 后端产出该字段 | 字段名/示例值 | 前端消费点 | 用户能否看到不完整 | 结论分类 | 建议 |
|---|---|---|---|---|---|---|---|
| C1 **行情总览整体降级** | `GET /market/overview`；`market.py:540-600,706-746` | **是** | **实测（当前生产态）** `data_freshness = {"status":"degraded","source":"timeout","reason":"兼容概览六秒预算已用尽"}`；顶层**无** `status`/`degraded` | `pages/MarketOverview/index.tsx:16,97-101` 用了 `DataFreshness` 组件，但**只传 `asOf`/`fromCache`/`stale`**；组件签名 `components/DataFreshness.tsx:16-25` 无 `status`/`reason`；`data_freshness.status` 全前端 0 渲染 | **不能** | ⚠️ **后端有前端无**（**本次 Top 1**） | `DataFreshness` 增 `freshness` prop；`status==='degraded'` 渲染琥珀条 + `reason`。后端补顶层 `status`（与 `data_freshness.status` 同源） |
| C2 **异动块整块不可用** | `GET /market/overview`；`_build_anomalies` `market.py:226-247`（只产 `ok`/`degraded` + `note`），观测到的 `unavailable` 来自**冷路径超时占位填充** `market.py:846`（`{"status":"unavailable","reason":"概览冷路径响应超时"}`，对 `anomalies`/`money_flow`/`sentiment`/`ai_stats` 一视同仁） | **是** | 函数自身：`{status:"ok"\|"degraded", items:[…], note:"涨停为 ±9.8% 近似口径；盘中放量/快速波动异动需实时行情源，P2 接入"}`；**实测（生产态）** `anomalies = {"status":"unavailable","reason":"概览冷路径响应超时"}` | **无**：`anomalies` 在 `pages/MarketOverview/*` 0 引用；`types/stock.ts:453,509,524` 仅类型声明 | **不能** | ⚠️ **后端有前端无** | 补 `AnomaliesPanel`，或从响应中移除该块（不留"死字段"）；`_build_anomalies` 自身的 `note`（±9.8% 近似口径）也必须可见 |
| C3 资金/情绪/广度/板块/指数/推荐降级 | `market.py:190-260,280-410,494-505,540-600` | **是** | `money_flow:{status:unavailable,reason:"概览冷路径响应超时"}`、`sentiment.basis:"平台自研口径：…"`、`recommend:{status,reason,message,coverage}`、`heat/sectors/indices.status` | `pages/MarketOverview/MoneyFlowPanel.tsx:21,133`、`BreadthPanel.tsx:82,142`、`HotSectorsPanel.tsx:13,66`、`KpiCards.tsx:41`、`AiPicksPanel.tsx:87,102-118` | 能 | ✅ **对齐** | 作为"块级降级"的参考实现 |
| C4 **AI 准确率降级被伪装成"样本不足"** | `market.py:413-491`（失败路径 `:489-491` 返回 `{status:"unavailable", reason:"TypeError: …"}`） | **是** | `ai_stats.status:"unavailable"` + `reason` | `pages/MarketOverview/KpiCards.tsx:46,75-80`：`ai?.rank_ic ?? '—'`，`sub` 兜底文案写死 **`'样本不足'`**；**不读 `ai.status`/`ai.reason`** | **不能（且被误导）**：超时/异常显示为"样本不足" | ⚠️ **后端有前端无** | `KpiCards` 读 `ai.status`/`ai.reason`，`unavailable` 时显示真实原因而非"样本不足" |
| C5 实时块降级 | `GET /market/overview/rt`；`market.py:706-746` | **是** | **实测** `data_freshness = {"status":"degraded","source":"timeout","reason":"实时块五秒预算已用尽"}`，`as_of:"2026-09-21 19:52:01"` | 同 C1（合并视图 `index.tsx:63`）；`data_freshness` 0 渲染 | 不能 | ⚠️ **后端有前端无** | 同 C1 |
| C6 **前端传了后端不产出的 `stale`** | `GET /market/overview/rt`（`market.py:748+`）与 `GET /screener`（`screener.py:454+`） | **否** | **实测** 两端点 `data` 均**无 `stale` 键**（`'stale' in data == False`） | `pages/MarketOverview/index.tsx:101` `stale={rtData?.stale}`；`pages/Screener/index.tsx:318` `stale={result?.stale}` | 不能（`stale` 恒 `undefined` ⇒ 徽标永远不转琥珀） | ⚠️ **前端有后端无** | 后端补 `stale`（与 `freshness.is_stale` 同源），或前端改读 `freshness.is_stale`（`types/p1.ts:64` 已有） |
| C7 选股榜单时效 | `GET /screener`；`screener.py:293-340` | **是** | **实测** `status:"degraded"`、`reason:"data_stale"`、`freshness:{as_of:"2026-09-17",expected:"2026-09-21",lag_trading_days:2,is_stale:true,note:"数据落后最近已收盘交易日 2 个交易日"}`、`coverage:{available:50,total:50,ratio:1.0}`、`message` | `pages/Screener/index.tsx:213-214,264-268,315-336,350-355`（status/message/lag_trading_days/note/from_cache 全用） | **能** | ✅ **对齐**（`coverage`/`from_snapshot`/`top_k` 未用，次要） | `coverage` 可补渲染（当前恒 50/50，价值低） |
| C8 **同一功能两个端点三态矛盾** | `GET /screener`（`status:"degraded"`,`reason:"data_stale"`,`as_of:"2026-09-17"`）vs `GET /screener/stocks`（`degraded:false`,`stale:false`,`trade_date:…`） | 两者都产出 | **实测**同一时刻：`/screener` 说降级、`/screener/stocks` 说正常 | `pages/Screener/index.tsx:350`（读 root `status`）+ `pages/Screener/StockList.tsx:132,176`（读 stocks `degraded`） | 不能（同页两个结论互斥） | ❌ **口径冲突（两端都"有"，但语义不一致）** | 统一由 `screener._freshness()` 单一真源派生；`/screener/stocks` 必须回传同一 `status` |
| **C7b** ✅ **空榜两态可区分（P0-6 裁决落地，2026-09-22）** | `GET /screener*`；`screener.py::_finalize_screener_payload` + `data/screening.py::filter_universe`（`board_universe_rows`） | **是**（新增） | **实测** `board=bse`（`universe_daily` 快照里 0 只成分股）⇒ `status:"unavailable"`、`reason:"empty_board"`、`message:"板块 bse 当前没有可筛选的成分股（未纳入数据源），并非「当日无匹配信号」"`，并**新增披露字段** `universe_filter.board_rows = 0`（板块在快照里的成分股数，`all`/无快照 ⇒ `null`）；同 pred+universe 的 `board=main` ⇒ 正常出榜且 `board_rows=2`；池在但当日无匹配 ⇒ 仍 `ok`/`reason:"no_matching_signals"` | `pages/Screener/index.tsx:211-213`（`result?.message` 优先渲染 ⇒ 新文案自动可见；前端不 switch `reason`，新机器码**零改动即可显示**） | **能** | ✅ **对齐**（原缺陷"`board=bse` 谎报当日无匹配信号"已消除） | `universe_filter.board_rows` 前端暂未渲染（次要；建议进 ⓘ tooltip 说明"该板块成分股数"） |
| C9 实时报价双源降级 | `quotes_hub.quotes_snapshot` `data/quotes_hub.py:56-67`；`realtime.fetch_quotes_batch` `data/realtime.py:403-423` | **是** | `source ∈ {tencent, sina, degraded}`，全失败返回 `{"as_of":None,"source":"degraded","quotes":[]}` | `api/market.ts:47-50`（`QuotesData.source` 类型含 `'degraded'`）；`hooks/useWatchlistQuotes.ts` | 能（`source` 已在类型里） | ✅ **对齐**（渲染细节取决于页面） | — |
| C10 选股实时/日终口径回退 | `screening.build_market_stock_rows`；`data/screening.py:630-845` | **是** | **实测**（`basis=auto` 实时源不可用时）`degraded:true`、`source:"local"`、`quote_coverage:null`、`basis_desc:"本地日终截面 2026-09-17：实时源不可用…"`、`basis_fields:{...}`。实时可用时 `degraded:false`、`quote_coverage:{hit:2492,total:2492}` | `pages/Screener/StockList.tsx:132,176`（`degraded` 真渲染）；`basis_desc` `:133,143-144` 真渲染；**`basis_fields` / `quote_coverage` 仅 `types/p1.ts:112,117` 类型声明，0 渲染** | 部分能 | `degraded`/`basis_desc` ✅；`basis_fields`/`quote_coverage` ⚠️ 后端有前端无 | 把 `quote_coverage` 渲染为"命中 2492/2492"；`basis_fields` 进 ⓘ tooltip |
| C11 **自选面板 flow 降级无提示** | `GET /watchlist/dashboard`；`watchlist.py:253-371` | **是** | **实测** `summary = {"count":1,…,"flow_total_yi":null,"flow_status":"unavailable","quote_date":"2026-09-17",…}`；顶层**无** `status`/`reason` | `pages/Watchlist/index.tsx:318-321` 只渲染 `flow_total_yi ?? '—'`；**`flow_status` 全前端 0 引用** | **不能**（看到"—"，不知道是"资金源不可用"还是"今天没数据"） | ⚠️ **后端有前端无** | 读 `flow_status`，`unavailable` 时显示"资金流向数据源不可用"；顶层补 `status`/`reason`/`freshness` |
| C12 **自选面板行情陈旧无披露** | `GET /watchlist/dashboard`；`watchlist.py:138-140,253+` | **否**（只有裸 `quote_date`） | **实测** `summary.quote_date = "2026-09-17"`（滞后 2 个交易日），无 `is_stale`/`lag_trading_days` | `types/watchlist.ts:12`（`quote_date` 类型）；页面渲染 `date` | 不能 | ❌ **两端都无=静默** | 复用 `screener._freshness()`，补 `freshness:{is_stale,lag_trading_days,note}` |
| C13 Redis 降级 | `GET /settings`；`app_settings.py:101-131`（`degraded:bool`） | **是** | `degraded:false`（Redis 不可用时 true） | `api/settings.ts:42-46` 类型 + `pages/Settings/index.tsx:558`（`system.cache.degraded ? …`） | 能 | ✅ **对齐** | — |
| C14 指数快照降级 | `GET /health/ready`；`main.py:254-296` | **是** | `checks:{sqlite,data_root,redis,core_snapshot}`，`core_snapshot:"ok"/"degraded"` | 无页面消费（运维端点） | 不适用（运维用） | ✅ 设计如此 | — |
| C15 ETF 规模/业绩降级 | `GET /etf/scale` `etf.py:403-468`；`/etf/performance` `:329-401` | **是** | **实测** `status:"unavailable"`、`reason:"数据源暂不可用（RuntimeError）"`、`data_freshness:{status:"degraded",as_of:"2026-09-21",reason:…}` | `pages/Etf/OverviewCards.tsx:127`、`PerformanceChart.tsx:71`（渲染 `data_freshness.reason`） | 能 | ✅ **对齐** | — |
| C16 **ETF 详情三态自相矛盾** | `GET /etf/detail/{code}`；`etf.py:710-775`。**根因定位**：`_cached_etf_payload` 成功路径 `etf.py:89` 无条件 `data.setdefault("data_freshness", _freshness("fresh"))`；而顶层 `status` 在 `etf.py:744-748` 由 blocks 汇总（任一 block `degraded`/`unavailable` ⇒ `overall="degraded"`）。二者**互不知情** | 是 | **实测**（`510300`）顶层 `status:"degraded"`、`data_freshness:{"status":"fresh","as_of":"2026-09-21"}`、`blocks.header:{"status":"unavailable","reason":"未找到该 ETF"}` | `pages/Etf/index.tsx` / `EtfDetail` | 不能（顶层说降级，新鲜度徽标说 fresh） | ❌ **口径冲突** | `data_freshness.status` 必须由 blocks 汇总（复用 `etf.py:744-748` 的 `overall`），删掉 `etf.py:89` 的无条件 `"fresh"` 默认 |
| C17 as_of 日期不明 | `stock/{symbol}/kline` `stock.py:151-193`；`stock/{symbol}/profile` `:108-149` | **部分** | **实测** kline `keys=['adjust','bars','count','end','start','symbol']`（`adjust` 只回显请求，无 `basis`/`as_of`）；profile `keys=[…,'latest',…]` 无 `as_of`/`basis` | `pages/StockDetail/index.tsx:67`（kline）；K 线图不显示口径 | 不能（用户看不到 qfq/hfq/raw 的实际生效口径来源） | ❌ **两端都无=静默**（`adjust` 仅回显，非披露） | 补 `basis:{adjust,price_basis,as_of,source}`（与 `stock/predict.close_basis` 同款） |
| C18 面板块级降级（正面） | `GET /stock/{symbol}/panels`；`data/panels.py:151-180,293-348` | **是** | **实测** `quote.status:"ok"`；`north:{status:"unavailable",reason:"data_source_retired",message:"北向（陆股通）持股数据已停用…",retired:true}`；`money_flow:{status:"unavailable",reason:"数据源暂时不可用"}`；`chip.note`/`risk.note`/`fundamentals.note` 口径齐备 | `pages/StockDetail/index.tsx:7,93-97,312-316,400-405,448,474,481,522,585,651-652,664`（大量 `status==='unavailable'` 与 `note` 渲染） | 能 | ✅ **对齐**（`note`/`status` 类） | — |
| C19 **北向停用原因不可见** | `data/panels.py:293-303` | **是** | `north.message` / `north.reason:"data_source_retired"` / `north.retired:true` | `pages/StockDetail/index.tsx:338-344,375` 只用 `north.pct_of_float`/`north.date`；**`retired` 全前端 0 引用**，`message` 未渲染 | 不能（只显示"暂无数据"，不说"数据源已永久下线"） | ⚠️ **后端有前端无** | `MoneyFlowPanel` 读 `north.message`/`retired`，显示"北向数据已停止提供" |

---

## 5. 矩阵 D — 口径 / 派生

| 场景 | 后端端点/函数(文件:行) | 后端产出该字段 | 字段名/示例值 | 前端消费点 | 用户能否看到口径 | 结论分类 | 建议 |
|---|---|---|---|---|---|---|---|
| D1 选股列表口径（复权/基准） | `data/screening.py:605-845`（`_local_basis_fields`/`_realtime_basis_fields`） | **是** | **实测** `basis:"daily"`、`basis_desc:"本地日终截面 2026-09-17（用户指定日终口径）"`、`basis_fields:{"close":"本地日终收盘（2026-09-17）","pct":"…前一有效截面收盘 - 1","amount":"本地日终成交额（元）","total_cap_yi":"不可用：本地无市值数据（不推算、不填 0）","float_cap_yi":"不可用：…"}` | `pages/Screener/StockList.tsx:133,143-144` 渲染 `basis_desc`；**`basis_fields` 仅 `types/p1.ts:112` 类型声明，0 渲染** | 部分能（一句话口径可见，逐字段口径不可见 —— 而 `total_cap_yi:"不可用：不推算、不填 0"` 恰是最该说的） | ⚠️ **后端有前端无（部分）** | `basis_fields` 进 ⓘ tooltip（逐字段口径）；`types/p1.ts` 的 `StockListBasisFields` 已有类型，只需接渲染 |
| D2 `kind:"platform"` 契约条目 | 全后端 `grep '"platform"'` = **0 命中** | **否**（改用 `basis`/`basis_desc`/`note` 表达） | 无 `kind` 字段；等价语义散落在 `sentiment.basis:"平台自研口径：…"`（`market.py:513`）、`text_status.basis`（`data/text_ingest.py:316`）、`etf … basis`（`data/etf.py:879`）、`report.basis`（`report.py:180`） | `pages/MarketOverview/AiPicksPanel.tsx:87`（渲染 `sentiment.basis`） | 能（语义达成，字面契约未达成） | ⚠️ 契约字面未落地 | 二选一：① 统一补 `kind:"platform"` 枚举（`derived|platform|external`）；② 修订契约文档，把"须披露 `basis` 或 `kind:"platform"`"改写为"须披露 `basis`"。**建议②**（改动面小、不引入双口径） |
| D3 `universe_scope` | `backtest.py:196-201`（`/backtest/run`）、`backtest.py:677-682`（`/backtest/signal-analysis`） | **是** | `{"dataset":"universe_daily_bt","n_symbols":N,"note":"股票池=本地已下载数据集（含退市证券的历史 bar，直至其 delist_date）；delist_date 之后的日期已从宇宙剔除，超期持仓按最后收盘×haircut 强平减记"}` | **无**：全前端 0 引用（连类型都没有） | 不能 | ⚠️ **后端有前端无**（**无法实测**：`/backtest/run` 与 `/signal-analysis` 均为 POST，`/backtest/run` 会写 `backtest` 表，属写端点，纪律禁止触发；结论仅由源码核对得出） | 回测结果页"参数"卡加"股票池口径"，把 `note` 原文展示 |
| D4 `liquidity` 口径 | `backtest.py:215-220`（`/backtest/run`）；`api/strategyBacktest.ts:75-79` 对应 `/strategy-backtest` | **是** | `{source:"universe_daily_bt", impact_cost_included:bool, participation_cap:…, note:…}`。`/backtest/run` 的 `liquidity` **无前端类型**；`/strategy-backtest` 的有（`StrategyLiquidity`） | `api/strategyBacktest.ts:75,120`（类型）；回测页未渲染 | 不能 | ⚠️ 后端有前端无（`/backtest/run` 口径）/ 部分（`/strategy-backtest`） | 两处回测统一 `LiquidityBlock` 组件（含"冲击成本是否计入"） |
| D5 `friction_costs` | `backtest.py:214`；`domain/portfolio.py:413` | **是** | `{k: round(float(v),2)}`（slippage/impact/decay） | **仅 `types/p1.ts:176` 类型声明，0 渲染** | 不能 | ⚠️ **后端有前端无** | 结果页"成本拆解"表 |
| D6 `rejected_trades` | `backtest.py:204` | **是** | `rejects` 列表（含拒绝原因） | **仅 `types/p1.ts:167` 类型声明，0 渲染** | 不能 | ⚠️ **后端有前端无** | 结果页"被拒交易"折叠表（含原因统计） |
| D7 `data_warnings` | `domain/portfolio.py:210-227,415`（组合回测）；`research.py:498-509`（`/research/impact-sim`） | **是** | `["000001.SZ 有 3 个交易日无行情（停牌/缺口），以前收盘价估值", "…", "N 个交易日 VWAP 异常（volume 坏点），已按 close 修正：…"]` | **仅 `api/research.ts:101` 类型声明，0 渲染**；组合回测的 `data_warnings` 连类型都没有 | 不能 | ⚠️ **后端有前端无**（**无法实测**：两处均为 POST，纪律禁止触发；结论由源码核对得出） | 结果页顶部黄色 `warnings` 条（`data_warnings` 是"数据被就地修正"的告知，必须可见） |
| D8 `failed_count` | `services/sync_service.py:118` | **是** | `0`（实测） | 0 引用 | 不能 | ⚠️ **后端有前端无**（同 B6） | 同 B6 |
| D9 `coverage` / `quote_coverage` | `market.py:369-370`（recommend）、`screener.py:293`、`data/screening.py:840` | **是** | recommend：`{available:0,total:0,ratio:null}`（实测 unavailable 态）；stocks：`{hit:2492,total:2492}`（实测） | `quote_coverage` 仅 `types/p1.ts:117` 类型声明；`coverage` 0 渲染（`recommend.message` 已间接表达） | 不能 | ⚠️ **后端有前端无** | 渲染 `hit/total`（"行情命中 2492/2492"） |
| D10 **标签数据空洞计数** | `ml/labeling.py:66,91,155-158`（`n_gap_invalid`，仅 `logger.warning`） | **只 warn 日志**（未暴露到任何 API） | 无 | 0 引用 | 不能 | ❌ **两端都无=静默** | `ml/monitor` 或 `/research/overview` 暴露 `label_gap_invalid_total`；训练报告落盘 |
| D11 **预测语义：横截面去均值** | `ml/train_lgbm.py:224-276`（`xsec_demean`）、`ml/monitor.py:540-546`、`ml/train_service.py:264` | **否**（响应不披露） | **实测** `/stock/{symbol}/predict` keys = `['base_value','close_basis','confidence','confidence_basis','date','feature_basis','feature_source','horizon','latest_close','model_version','pred_return','symbol','top5_factors']` —— **无 `xsec_demean` / 无 train_basis** | 0 引用 | 不能 | ❌ **两端都无=静默** | `predict` 响应加 `label_basis:{xsec_demean:bool,label_horizon,label_price_basis}`。**这是最严重的语义静默**：`base_value=-2.43e-05`、`pred_return=-0.0012` 只有在知道"标签已横截面去均值"时才可解释（绝对水平无意义，只可比大小） |
| D12 `label_price_basis` | `market.py:480`（`ai_stats` 的 **ok 路径**）；`data/predict.py` 的 `close_basis` 等价物 | **是（仅正常路径）** | `"hfq"` 或 `"raw_fallback"` | **仅 `types/stock.ts:468` 类型声明，0 渲染** | 不能 | ⚠️ **后端有前端无**（**且降级路径整字段消失**，见 C4） | 渲染为"AI 指标标签价基准：hfq"；`unavailable` 路径也要保留该字段（口径不随数据可用性变化） |
| D13 预测价基准 / 特征来源 | `ml/predict.py:140,154,160` | **是** | **实测** `close_basis:"latest_close 为后复权（hfq）收盘价，与训练特征/标签同基准；非实际成交价（后复权以 IPO 为基准累计，数值通常远大于现价）"`、`feature_basis:"panel=…；realtime=…单标的无邻居，g1_* 全为 NaN"`、`confidence_basis:"模型级置信度：clip(0.5+2×验证集RankIC,0,1)，同一模型下所有个股同值，非个股上涨概率"` | `confidence_basis`：`pages/StockDetail/index.tsx:124,210` 真渲染 ✅；**`close_basis` / `feature_basis` 全前端 0 引用** ❌ | 不能（`latest_close=11253.97` 显示在 UI 上而"这是 hfq 不是成交价"的说明不可见 ⇒ 极易被误读为真实股价） | ⚠️ **后端有前端无（高影响）** | 在 `latest_close` 旁加 ⓘ 用 `close_basis` 原文；`feature_source==='realtime'` 时用 `feature_basis` 提示"单标的无邻居" |
| D14 文本因子口径 | `GET /datacenter/text/status`；`datacenter.py:1029-1035` → `data/text_ingest.py:267,316` | **是** | **实测** `keys=['basis','doc_dates','doc_last','doc_symbols','docs','factor_files','factor_rows','llm_enabled','note','surprise_active_rows']`，`basis` 说明词库/LLM 两种打分口径 | `api/datacenter.ts:99-100` 类型 + `pages/DataCenter/TextDataPanel.tsx`（渲染 `note`/`basis`） | 能 | ✅ **对齐** | — |
| D15 镜像滞后 | `GET /datacenter/mirror/status`；`datacenter.py:1066-1077` → `data/cross_section.mirror_status` | **是** | **实测** `daily_bar:{source_dates:2116,mirror_dates:2115,source_last:"2026-09-18",mirror_last:"2026-09-17",lag_days:1,orphan_dates:0}` | `pages/DataCenter/TextDataPanel.tsx:75-79,143-150`（`lag_days` 真渲染 ✅）；**`orphan_dates` 全前端 0 引用** | 部分能 | `lag_days` ✅；`orphan_dates` ⚠️ | `orphan_dates>0` 时高亮（孤儿分区=镜像残留脏数据） |
| D16 容量公式 | `GET /desk/capacity` `desk.py:260-279`；`GET /research/overview` `research.py:115+` | **是** | **实测** `desk/capacity`：`{aum_threshold,aum_yi,formula}`；`research/overview`：`{capacity_formula,capacity_estimate_yi,factor_universe,expression_count,factor_count,model_version_count}` | `pages/CapacityAttribution/index.tsx:25,116`（`capacity.formula`）、`:354`（`overview.capacity_formula`）、`:248`（`benchmark_policy.weight_basis`） | 能 | ✅ **对齐** | 作为"公式透明"的参考实现 |
| D17 逐笔基差 | `GET /desk/orders`；`desk.py:233-239`（`basis_bps`） | **是** | **实测** `fills[].basis_bps`（决策价 vs 实际成交价，bps） | `pages/OrderDesk/index.tsx:455-461`（`基差 {f.basis_bps}bps`） | 能 | ✅ **对齐** | — |
| D18 **归因基准口径** | `POST /desk/attribution`；`desk.py:297+`、`desk.py:468`（`weight_basis`） | **是** | `benchmark_policy:{type,weight_basis,symbol_count,as_of}` | `pages/CapacityAttribution/index.tsx:248`（渲染 `benchmark_desc` + `weight_basis`） | 能 | ✅ **对齐** | — |
| D19 情绪指数口径 | `market.py:494-520`（`basis` 字符串，`:513`） | **是** | `"平台自研口径：score = 50 + (涨-跌)/(涨+跌)×45 + (涨停-跌停)×1.5，权重为产品设定而非拟合结果"` | `pages/MarketOverview/AiPicksPanel.tsx:87`（ⓘ tooltip） | 能 | ✅ **对齐** | — |
| D20 Brinson 口径 | `report.py:179-180,386` | **是**（写入 markdown） | `basis:"模拟盘真实持仓市值权重 vs 同池等权基准；hfq 后复权"` | `pages/Report/index.tsx`（markdown） | 能 | ✅ **对齐** | — |

---

## 6. 矩阵 E — 错误 / 状态

| 场景 | 后端端点/函数(文件:行) | 后端产出 | 字段名/示例值 | 前端消费点 | 用户能否看到 | 结论分类 | 建议 |
|---|---|---|---|---|---|---|---|
| E1 **未注册裸错误码（datacenter）** | `datacenter.py:684` `fail(4002,…)`；`:689` `fail(5000,…)`；`:952` `fail(4002,…)`；`:969` `fail(4003,…)`；`:1017` `fail(5001,…)` | 是（但码未注册） | `4002` / `5000` / `4003` / `5001`；`errors.py:68-101` 常量表**不含**这四个 | `types/api.ts:17-37` 的 `ERR` 表**也不含**；落到 `apiErrorMessage(body)` 直接显示 `message`（`api/client.ts:48-54,104`） | 能（文案在），但**码不可分类** | ❌ 两端都无（码表未注册） | 四码收敛到已注册段：`4002→40900 ERR_PIPELINE_BUSY`、`5000→50000/52000`、`4003→51001 ERR_DATA_EMPTY`、`5001→51000 ERR_DATA_SOURCE`；并补 `tests` 断言"`fail()` 只接受 `errors.py` 常量" → **✅ 已按此修复（第 9 轮）**，仅 `5000→40900`、`5001→50000` 两处按实际语义调整（见 `FIXES-APPLIED.md` 行 45） |
| E2 **未注册裸错误码（backtest 段）** | `backtest.py:318(40010) / 325(40011) / 352(40012) / 431(40017) / 504(40013) / 506(40014) / 508(40015) / 537(40016) / 545(40017) / 566(40017)` | 是（但码未注册） | `40010~40017`（10 处） | 同上，不在 `types/api.ts` 的 `ERR` 表 | 能（文案在），码不可分类 | ❌ 两端都无（码表未注册） | 注册为显式常量（如 `ERR_BT_WINDOW=40010…`）并补进两端码表；否则前端无法针对性提示 → **✅ 已修复（第 9 轮）**：登记为 `ERR_BT_SYMBOL_NO_DATA…ERR_BT_OPTIMIZE_FAILED`（40010–40019），并**按语义拆分**原一码三义的 `40017`；两端逐值逐名对账 |
| E3 **40400 未启用 / 语义错配** | `GET /datacenter/sync/tasks/{task_id}`；`datacenter.py:726-735` 用 `fail(ERR_DATA_EMPTY,…)` | 是（错的码） | **实测**任务不存在 → `code=51001`（"本地无数据"）而 `message="任务不存在"`；`fail(40400,…)` 在 `alerts.py:218,239` 用了，但该端点没用 | `api/datacenter.ts:181`（`error?`）；`types/api.ts:28` 有 `NOT_FOUND:40400` | 能看到"任务不存在"，但**码与语义不符**，前端无法用 `ERR.NOT_FOUND` 分类 | ❌ 口径不一致 | 改 `fail(ERR_NOT_FOUND,…)` |
| E4 方法错误码 | 全局 `errors.py:117-142` | 是 | **实测** `POST /datacenter/instruments` → `code=40000 "Method Not Allowed"`（`errors.py:136-139` 把 4xx 全归 `ERR_PARAMS`） | `api/client.ts:100,104` | 能 | ✅ 可接受（已在 `errors.py:132-135` 注释中显式说明设计） | 可选：405 单独给码 |
| E5 认证错误码 | `core/auth.py:193-201` + `errors.py:88-95` | 是 | `40100/40101/40102/40103/40300/40900`（实测未带 Bearer → `code=40100`） | `api/client.ts:19-23`（`AUTH_ERROR_CODES`）；`components/AuthBootstrap.tsx:28` | 能 | ✅ **对齐** | — |
| E6 **三态齐备性：选股双端点矛盾** | 见 C8 | 是（但互斥） | `/screener` = `degraded/data_stale`；`/screener/stocks` = `degraded:false,stale:false` | 同 C8 | 不能 | ❌ 静默（用户看到"正常"） | 同 C8 |
| E7 **`ok` 与 `state` 矛盾** | `GET /monitor/health`；`monitor.py:19-32` | 是（自相矛盾） | **实测** `state:"degraded"` 且 `ok:true` | `components/FactorHealthCard.tsx:20-27`（读 `state`）；`ok` 0 渲染 | 能（状态灯对），但字段自相矛盾 | ❌ 字段语义冲突 | `ok` 由 `state` 派生（`ok = state in ('healthy','watch')`），或删除 `ok` |
| E8 **`status` 未覆盖全部端点** | 全量 61 个 GET 路由（探针 §7 枚举） | 否 | 有结构化三态的：`screener`、`screener/stocks`、`market/overview*`（仅块级 + `data_freshness`）、`etf/scale|performance|detail`、`stock/panels`（块级）、`ops/lineage`（`missing_nodes`/`degraded_note`）、`datacenter/mirror/status`、`monitor/health`。**无任何状态字段的**：`alerts/events`、`notify/recent`、`desk/orders`、`desk/account`、`desk/capacity`、`stock/search`、`stock/kline`、`stock/profile`、`watchlist/dashboard`、`watchlist/correlation`、`datacenter/logs`、`datacenter/quality`、`datacenter/instruments`、`datacenter/task-stats`、`datacenter/text/status`、`screener/watchlist`、`research/*`、`report/daily`、`studio/factors`、`portfolio/search` | — | — | ❌ **系统性缺口**：`data.status` 仅存在于少数端点 | 见 §8「统一披露契约」：把 `status` 提升为**信封级**（`APIResponse.status`）或所有 `data` 必带 `status` |
| E9 `stale` 前端有后端无 | 见 C6 | 否 | 无 | `MarketOverview/index.tsx:101`、`Screener/index.tsx:318` | 不能 | ⚠️ 前端有后端无 | 同 C6 |
| E10 **`market/index/kline` 无鉴权** | `market.py:606-624`：签名只有 `code`/`limit`/`refresh`，**无 `_user: dict = Depends(require_role(...))`** | 不适用 | **实测**不带任何 Bearer 即返回 `code=0` 与完整 `bars` | `api/market.ts`（`indexKline`） | 不适用（安全项，非披露项） | ❌ 契约外（RBAC 缺口） | 补 `Depends(require_role("viewer"))`；并把"全部 GET 路由必须有 `require_role`"加入测试断言（现有 `tests/test_read_endpoints_rbac.py` 应已覆盖，需确认该端点未被漏扫） |

---

## 7. ❌ 两端都无 = 静默 —— 唯一权威清单（14 项）

> 判定口径：**后端没有任何字段/日志以外的产出**（或产出的是伪值/矛盾值），**且前端没有任何消费点** ⇒ 用户在任何时刻都无法感知"数据不完整 / 已降级 / 口径有前提"。

| # | 场景 | 静默的具体表现 | 证据 |
|---|---|---|---|
| **S1** | **`ops/quality-scan` 标的数硬截断**（`sym_dirs[:200]`） | 250 个分区只扫 200，响应无任何截断字段，UI 直接把 `symbols_scanned=200` 当作"扫描范围 200 只"展示 | 实测：`symbols_scanned=200`（真实 250）；`ops.py:52`；`pages/DataQuality/index.tsx:137` |
| **S2** | **`datacenter/datasets` 分区读失败** | 坏文件只写服务端 WARNING，响应无 `failed`；`symbols` 仍把坏分区计入 | 实测：返回 `{symbols:2,rows:200}`，日志"1/2 个文件成功"；`datacenter.py:158-184` |
| **S3** | **`datacenter/overview.dataset_count` 与 `/datasets` 同页矛盾** | 同页 3 vs 8，无任何解释字段 | 实测：`dataset_count=3`，`items.length=8` |
| **S4** | **`predict` 的 `xsec_demean` 标签口径** | `pred_return` / `base_value` 的绝对水平在"标签已横截面去均值"前提下才可解释，该前提不出现在响应、不出现在 UI | 实测 `predict` keys 无 `demean`/`train_basis`；`train_lgbm.py:224-276` |
| **S5** | **`ml/labeling.n_gap_invalid`** | 标签跨越数据空洞的条数只进 `logger.warning`，无 API、无 UI | `ml/labeling.py:66,91,155-158`；前端 0 引用 |
| **S6** | **`watchlist/dashboard` 行情陈旧** | `quote_date=2026-09-17`（滞后 2 交易日），无 `is_stale`/`lag_trading_days`，顶层无 `status`/`reason` | 实测 `summary` 全文；`watchlist.py:253-371` |
| **S7** | **`stock/{symbol}/kline` 复权/日期口径** | `adjust` 仅回显请求参数，无 `basis`/`price_basis`/`as_of`/`source`，UI 不显示生效口径 | 实测 keys `['adjust','bars','count','end','start','symbol']` |
| **S8** | **`export/screener` 导出无口径元数据** | xlsx 内无 basis/口径 sheet；只在文件名里带日期 | 实测响应头 + `export.py:18-56` |
| **S9** | **`alerts/events` limit 截断** | 裸 list，无 `total`/`truncated`/`has_more` | 实测信封 `['code','data','message','trace_id','ts']` |
| **S10** | **`notify/recent` 20 条硬顶** | `_RECENT_MAX=20` 静默丢弃，裸 list | `core/events.py:17-18,31` |
| **S11** | **`desk/orders` limit 截断**（潜在） | 裸 list，默认 `LIMIT 50`；前端客户端分页让用户以为"这就是全部" | `desk.py:210-219`；`pages/OrderDesk/index.tsx:403` |
| **S12** | **`stock/search` / `portfolio/search` / `etf/hot` / `etf/flow` / `datacenter/logs` / `datacenter/instruments`(假 `total`) 截断** | 全部裸 list 或伪 `total`，无 `has_more`/`truncated` | §2 A6/A11/A12/A13 |
| **S13** | **`screener` vs `screener/stocks` 三态互斥** | 同一功能一个说降级、一个说正常；UI 同页同时消费两者 | 实测 `status:degraded/data_stale` vs `degraded:false,stale:false` |
| **S14** | **裸错误码 `4002/5000/4003/5001/40010~40017`** | 两端码表都不含这 15 个码，无法分类 | `errors.py:68-101` vs `types/api.ts:17-37`；10+5 处 `fail()` |

**另记 3 项「码与语义不符」的静默**（严格说是"有字段但值错"，比"没有"更危险）：

| # | 场景 | 表现 |
|---|---|---|
| S15 | `etf/detail` 三态自相矛盾 | 顶层 `status:"degraded"` + `data_freshness.status:"fresh"` + `blocks.header.status:"unavailable"` |
| S16 | `monitor/health` `ok:true` 与 `state:"degraded"` 并存 | 字段自相矛盾 |
| S17 | `sync/tasks/{id}` 不存在 → `code=51001`（本地无数据）而非 `40400` | 码与 message 语义不符，前端无法用 `ERR.NOT_FOUND` 分类 |

---

## 8. 统一披露契约建议

### 8.1 字段命名（单一真源，命名不得再分叉）

| 语义 | 统一字段 | 类型 | 位置 | 取代 |
|---|---|---|---|---|
| 数据是否完整 | `truncated` | `bool` | 与列表同级的 `data` | — |
| 截断上限 | `limit_applied` | `int` | 同上 | `recent_limit` / `supplement_limit` / 匿名 `200`/`50`/`10` |
| 截断前的真实总量 | `total_available` | `int \| null` | 同上 | 伪 `total`（A6） |
| 多个独立上限 | `truncations: [{scope, limit, total, truncated}]` | `list` | 同上 | `recent_truncated` + `truncated` 并列（`alerts.py` 模板可保留，但需归一命名） |
| 部分失败 | `partial` + `failed_count` + `failed_items:[{id,reason}]` | `bool`/`int`/`list` | 同上 | 仅 `logger.warning`（S2/S5） |
| 三态状态 | `status` | `"ok"\|"degraded"\|"unavailable"` | **信封级**（`APIResponse.status`）**或** 所有 `data` 必带 | 现仅少数端点有（E8） |
| 降级原因码 | `reason` | `str`（枚举：`data_stale`/`source_timeout`/`source_retired`/`market_data_partial`/`partial_scan`/`budget_exhausted`/`no_matching_signals`/`empty_board`〔2026-09-22 P0-6 新增〕…） | 与 `status` 同级 | 自由文本散落 |
| 降级原因文案 | `message` | `str` | 同上 | `degraded_note` / `note` / `akshare_message` |
| 口径（派生指标） | `basis` + `basis_fields: {field: 说明}` | `str` + `map` | 同上 | `kind:"platform"`（D2，建议改契约文档） |
| 数据时效 | `freshness: {as_of, expected, lag_trading_days, is_stale, note}` | `map` | 同上 | 裸 `quote_date` / `trade_date`；`stale`（E9 删或由 `is_stale` 派生） |
| 前端刷新语义 | `from_cache` | `bool \| "refreshed"` | 同上 | — |

**硬性约束**：
1. 任何 `LIMIT` / `[:N]` / `head(N)` / `tail(N)` 的**读**路径必须伴随 `truncated`+`limit_applied`+`total_available`（三件套）。
2. 任何 `except` 吞掉的部分失败必须伴随 `partial`+`failed_count`。
3. `total` 只允许表示"不随 `limit` 变化的真实总量"；当前 `datacenter/instruments.total = len(rows)` 必须修（A6）。
4. `basis_fields` 不得包含"不可用：不推算、不填 0"这类**必须让用户看到**的说明（D1）。

### 8.2 三态映射（`status` 的唯一映射表）

| `status` | 语义 | 必备伴随字段 | 前端呈现 |
|---|---|---|---|
| `ok` | 数据完整且新鲜 | `as_of` | 绿色"实时" |
| `degraded` | **有值但前提不成立**（陈旧 / 口径回退 / 部分候选被过滤 / 部分文件被跳过） | `reason` + `message` + `freshness.is_stale`（如适用） | **琥珀**条 + `reason` 文案 + `as_of`；**不得**静默用 `—` 兜底 |
| `unavailable` | 无值 | `reason` + `message` | 灰"暂无数据" + **真实 `reason`**（禁止写死"样本不足"之类猜测文案，见 C4） |
| `partial`（建议新增） | 有值但扫描/读取不完整 | `failed_count` + `failed_items` | 琥珀条 + "N 个分区未读取" |

**禁止**：同一功能的多端点各自派生 `status`（C8/E6）；`ok` 独立于 `status` 计算（S16）；`data_freshness.status` 与顶层 `status` 分叉（C16/S15）。

**⚠️ 现状：连 `status` 的取值域都有三套**（这是比字段缺失更隐蔽的口径分裂，必须先归一）：

| 取值域 | 出现位置 | 实测值 |
|---|---|---|
| `ok` / `degraded` / `unavailable` | `screener.py:319-338`、`stock/panels`（`data/panels.py`）、`etf/detail` 的 blocks | `screener.status="degraded"`；`panels.north.status="unavailable"` |
| **`fresh`** / `degraded` | `market.py:555,583,599-600`（`data_freshness.status`）；`etf.py:89,256,393,461,771`（`_freshness()` 的入参字面量） | `market/overview.data_freshness.status="degraded"`；`etf/detail.data_freshness.status="fresh"` |
| `ok` / `degraded`（仅二态） | `market.py:243-244`（`_build_anomalies`）、`ops/lineage` 的节点 `status` | `anomalies.status="degraded"` |

⇒ `data_freshness` 用 `"fresh"` 而块状态用 `"ok"`，是**同一份响应里两套词汇**，任何"统一读 `status`"的前端逻辑都会漏判（正确做法是显式枚举两套，正是当前前端的境况）。建议统一为 §8.2 的 `ok|degraded|unavailable`，`fresh` 作为 `ok` 的别名在**过渡期**双向接受并打 deprecation 日志。

### 8.3 错误码映射（收敛 + 注册）

把 `core/errors.py` 扩为唯一码表，并**禁止裸字面量**：

| 现有裸码 | 出现位置 | 建议归并 |
|---|---|---|
| `4002` | `datacenter.py:684,952` | `ERR_PIPELINE_BUSY (40900)` |
| `5000` | `datacenter.py:689` | `ERR_SYSTEM (50000)` 或 `ERR_TRAIN (52000)`（按领取任务失败语义） |
| `4003` | `datacenter.py:969` | `ERR_DATA_EMPTY (51001)` |
| `5001` | `datacenter.py:1017` | `ERR_DATA_SOURCE (51000)` |
| `40010`–`40017` | `backtest.py` 10 处 | 新增显式常量：`ERR_BT_WINDOW/RANGE/SYMBOL/SIGNAL/…`，值保留 `4001x` 但**必须在 `errors.py` 与 `types/api.ts` 双向登记** |
| `51001` 表"任务不存在" | `datacenter.py:734` | 改 `ERR_NOT_FOUND (40400)` |

配套：加一条测试断言「`app/**` 中所有 `fail(` / `AQPException(` 的第一个实参必须是 `core.errors` 的常量或已登记的常量名」，从机制上杜绝第 16 个裸码出现。

**前端侧**：`types/api.ts:17-37` 的 `ERR` 表必须与 `errors.py` 常量表**逐字对账**（当前已对上 `errors.py`，缺的正是上面 15 个裸码）；`api/client.ts:48-54` 的 `apiErrorMessage` 对未知码应统一附加 `（未登记错误码 {code}）`，让用户与 QA 都能识别"码表缺项"，而不是当成普通业务错误。

---

## 9. 无法验证 / 局限

| 项 | 原因 |
|---|---|
| `POST /backtest/run`、`/backtest/strategy-run`、`/portfolio/backtest`、`/research/impact-sim` 的 `universe_scope` / `friction_costs` / `rejected_trades` / `data_warnings` / `liquidity` 实测值 | 均为 POST；`/backtest/run` 会写 `backtest` 表、组合回测会写业务表，属写端点，纪律禁止触发。结论仅由源码 + 前端类型核对（D3/D4/D5/D6/D7） |
| `alerts/events` 的"超限"实测 | 生产库 `alert_events` 当前 **0 行**（实测 `COUNT(*)=0`），无法构造 `>limit` 的真实超限；已改为(a)结构证明 —— 响应为裸 list、信封仅 5 键、无 `total`/`truncated`；(b)在 `%TEMP%` 构造 100 FAILED + 25 RUNNING 的 SQLite，直调 `_check_data_health` 证明模板字段真会出现（A2 实测成立） |
| `sync/status.failed_count > 0` 的真实取值 | 当前 `failed_count=0`（无失败任务）；字段存在性与前端零消费已实测/核实 |
| `datacenter/datasets` 在**生产**数据上出现坏文件 | 未人为破坏生产 parquet；改为在 `%TEMP%` 构造 1 好 1 坏分区直调 `_scan_dataset`（B1） |
| `ops/quality-scan` 生产数据的 200 上限是否真的生效 | 生产 `daily_bar` 有 2499 个 symbol 分区（实测 `/datacenter/datasets`），**已 >200 ⇒ 上限必然生效**；同时用 250 个临时分区实测确认 `symbols_scanned==200` |
| 沙箱限制 | DSH workspace-write 沙箱禁止命名管道（loguru `enqueue=True`）与 `mkdtemp` 的 0o700 DACL，探针通过 `-p audit_mkdtemp_fix` 插件修复（只改测试运行环境，不改被测行为） |

---

## 10. 结论（附：给审核团的 6 条）

1. **静默截断的唯一重灾区是"裸 list 读端点"**：`alerts/events`、`notify/recent`、`desk/orders`、`stock/search`、`portfolio/search`、`datacenter/logs` 六个端点返回裸 list，`total`/`truncated`/`has_more` 一个都没有；`datacenter/instruments` 更糟 —— 它的 `total` 是 `len(rows)`，**数字随 `limit` 一起缩小**（`limit=1 → total=1`，真实 5552），属"有字段但该字段在骗人"。
2. **`ops/quality-scan` 是全库最典型的静默截断**：`sym_dirs[:200]` 对生产 2499 个分区必然生效，响应却把 `symbols_scanned=200` 交给 UI，`pages/DataQuality/index.tsx:137` 原样渲染成"扫描范围 200 只"。同时单文件损坏会让**整次扫描**回 `code=50000, data=null`（已扫的 199 个分区结果全丢）—— 截断静默 + 部分失败全有或全无，双重缺陷。
3. **`alerts.py` 的 `truncated/recent_truncated/recent_limit/supplement_limit` 是全库唯一合格的截断披露模板**（实测在 100+25 输入下四个字段全部出现、基线时全部不出现，条件披露语义正确），但价值被前端抵消：`rg "truncated|failed_count" frontend/src = 0`，`pages/Alerts/index.tsx:169` 只渲染 `×${p.failed_jobs}` ⇒ 用户看到"流水线失败 ×25"却不知道还有 75 条被截。**该模板必须推广到 §8.1 的字段命名，并由前端补齐消费。**
4. **降级字段的产出端已经相当好，消费端是主要缺口**：`market/overview` 当前**实测处于 `data_freshness.status="degraded"`（`reason="兼容概览六秒预算已用尽"`）**，且 `anomalies`/`money_flow`/`sentiment`/`ai_stats` 四块全部 `unavailable`；而 `DataFreshness` 组件虽然被 MarketOverview 引用，**只接 `asOf/fromCache/stale` 三个 prop，从不读 `status/reason`**，`anomalies` 更是全前端 0 引用。降级信号生产端齐全、传输到用户的路断在组件签名上。
5. **口径披露里最危险的不是缺失而是"降级时口径字段消失"**：`predict.latest_close=11253.97` 是 hfq 后复权价，`close_basis` 原文已明确"非实际成交价"，但前端 0 引用（`confidence_basis` 渲染了、`close_basis`/`feature_basis` 没渲染）；`ai_stats.label_price_basis` 只在 **ok 路径**返回（`market.py:480`），一旦降级（正是当前状态）该字段直接消失 —— 口径不应随数据可用性变化。
6. **错误码表两端各缺同一批码**：`4002/5000/4003/5001`（`datacenter.py` 5 处）与 `40010~40017`（`backtest.py` 10 处）既不在 `errors.py` 常量表也不在 `types/api.ts` 的 `ERR` 表；另 `sync/tasks/{id}` 用 `51001`（本地无数据）表达"任务不存在"、`monitor/health` 出现 `state="degraded"` 与 `ok=true` 并存、`etf/detail` 出现 `status="degraded"` 与 `data_freshness.status="fresh"` 并存、`/screener` 与 `/screener/stocks` 三态互斥 —— 建议按 §8.2/§8.3 收敛为"单一映射表 + 禁止裸码 + 前端未知码显式标记"。

---

### 附：证据文件

- `backend/.tmp_testrun/p5_probe_out.txt`（探针 #1，263 行）
- `backend/.tmp_testrun/p5_probe2_out.txt`（探针 #2，270 行）
- `backend/.tmp_testrun/p5_probe3_out.txt`（探针 #3，40 行）
- 探针源码：`%TEMP%\aqp_p5_probe.py` / `aqp_p5_probe2.py` / `aqp_p5_probe3.py`（刻意不落在仓库内）