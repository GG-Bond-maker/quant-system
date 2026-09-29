# §8.2 第 17 项 · 死代码清理：孤儿符号全量扫描（AST）与处置台账

> 生成时间：2026-09-22（第 18 轮）｜脚本：`backend/.tmp_probe_s4/orphan_scan.py`（一次性只读探针，未入库）
> 汇总：扫描 **297** 个 `.py`（`app/` + `scripts/` + `tests/`），`app/` 顶层符号 **1069** 个
> ⇒ ① 完全零引用 **14** 个（其中 **10 个是 ORM 声明类**）；② 仅测试引用 **27** 个。

## 1. 判据（为什么不能只用 `grep`）

| 维度 | 做法 | 避免的假阳性/假阴性 |
|---|---|---|
| 引用识别 | **AST**：`Name(id=sym)` / `Attribute(attr=sym)` / import 名 / `getattr("sym")` 字面量 | 子串匹配会让 `publish` 命中 `publish_threadsafe`、`_publish_now`；`path_for` 命中 `path_for_year` |
| 说明文字 | 排除 docstring 的 `Constant` 节点与注释（注释本就不在 AST 里） | 否则我为本轮删除写的说明性 docstring 会被当成"仍有引用"，反向锁失效 |
| 装饰器注册 | 有 `decorator_list` 的顶层符号**一律视为存活** | FastAPI 路由（`@router.get`）、中间件、ORM 类都靠装饰器/元类注册，零显式引用是正常的（初版未过滤时误报 **115** 个，其中 ~92 个是路由处理器） |
| 定义自身 | 排除 `def sym` 那一行 | 否则每个函数都"引用"自己 |

## 2. ① 类：完全零引用（14 个）

### 2.1 ORM 声明类（10 个）——**保留**，理由：不是死代码

`models.py`：`PriceAlert` / `ModelRegistry` / `DataUpdateLog` / `NewsAnnouncement` /
`DividendSplit` / `UserSetting` / `ScreenerSnapshot` / `ScreenerSnapshotStats`；
`models_auth.py`：`UserSession` / `ApiToken`。

* 它们由 SQLAlchemy `Base` 元类在**导入时注册进 `Base.metadata`** ⇒ 表结构由它们定义，
  `Base.metadata.create_all()` 依赖它们。零"名字引用"≠ 未使用。
* 删除其中任何一个 = **改持久化 schema**（新库不再建该表），属产品决策，不属死代码清理。
* 逐项跟踪：`DividendSplit` 之所以变成零引用，正是因为本轮删掉了它唯一的写入方
  `save_dividends`（见 §3）。表定义保留 ⇒ 老库的 `dividend_split` 表仍在，读取方也已在同批删除 ⇒
  口径一致，不产生"半条链路"。

### 2.2 非 ORM（4 个）——分类登记，不删

| 符号 | 位置 | 处置 | 理由 |
|---|---|---|---|
| `fetch_announcements()` | `data/ingest/announcements.py:38` | **登记为接线缺口** | 与 `save_announcements()`（仅测试）构成"取数→落盘"两半，而**读取端是活的**（`panels.build_events` → `read_symbol_announcements`）。删掉取数端等于把未来接线所需的零件毁掉；属"有读者无写者"（同 S1 类），应接线而非删除 |
| `fetch_cninfo_announcements()` | `data/realtime.py` | 同上 | 公告多源之一（cninfo） |
| `fetch_sina_notices()` | `data/realtime.py` | 同上 | 公告多源之一（新浪） |
| `style_exposure()` | `domain/research.py:173` | **登记为待裁决** | 组合风格暴露（Σw·z）是 S6 归因类能力，报告 S6 区将其列为待补能力；零调用方是"未接线"，不是"没人要" |

> 说明：本轮**已删**的零引用符号见 §3；上表 4 项是扫描后**决定不删**的部分，
> 避免把"待接线能力"当成死代码清掉（这正是 §5 分类里第 2 类的边界）。

## 3. 本轮实际删除清单（第 17 项累计）

计数以"删除的函数/常量"为单位，共 **35** 项（另有 22 处 `tests/`+`scripts/` 的未用 import/局部变量，属 lint 类，见第 20 项，不计入此数）：

| 类别 | 明细 | 数量 |
|---|---|---|
| 未用局部变量（F841） | `api/v1/etf.py::last`、`backtest/ma_cross.py::day_idx`、`ml/monitor.py::label`（中文标签表，从未被读） | 3 |
| 从不写入的指标（`core/metrics.py`） | `HTTP_ERRORS_TOTAL`、`REDIS_STATUS`、`REDIS_CIRCUIT_OPEN`、`PIPELINE_TOTAL`、`PIPELINE_DURATION`、`MODEL_PREDICTION_TOTAL`、`MODEL_PREDICTION_ERROR` | 7 |
| 域规则死函数 | `a_share_rules.py`：`is_t_plus_one`、`settlement_days`、`commission_rate_default` | 3 |
| 存储层 | `parquet_store.py`：`path_for`、`write_whole_symbol`（写 `all.snappy.parquet`，而 `read_symbol_dataset` 只 glob `year=*.parquet` ⇒ 写入即对读路径不可见） | 2 |
| 存储/修复层 | `repair.py::is_quarantined`、`cross_section.py::remove_mirror`、`panels.py`：`recent_trade_date_str`/`announcement_window`/`code_of` | 5 |
| 事件/推理 | `events.py::publish`（另有 `publish_threadsafe` 在用）、`infer.py::infer_day`（及其专用 import `logger`/`FEATURE_VERSION`） | 2 |
| 采集层 | `ingest/akshare_adapter.py`：`fetch_daily_bar_batch`/`fetch_daily_bar_batch_by_year`；`ingest/dividends.py`：`fetch_dividends`/`save_dividends`/`load_dividends_asof` | 5 |
| 并发锁 | `pipeline_lock.py::current_owner`（别名，无调用方） | 1 |
| 本轮 AST 扫描**额外发现** | `market.py::_build_sectors`（已被 `_sectors_from_local` 取代）1、`realtime.py::_fetch_ths_fflow`（同花顺兜底源；删除后其专用解析器 `_cn_amount_to_float` 亦成孤儿，一并删）2、`quotes_hub.py::publish_alert_threadsafe`/`alerts_subscriber_count` 2、`train_lgbm.py::daily_ics`（`daily_ic_pairs` 是其等价替代）1、`paper.py::_exec_price_for` 1 | 7 |

### 3.1 删除留下的连带清理（必须做，否则 lint/行为不自洽）

* `core/metrics.py`：`Gauge` import 随 `REDIS_STATUS`/`REDIS_CIRCUIT_OPEN` 一并移除；
  模块 docstring 记录了 7 个指标**将来若要恢复，写入点分别在哪**（HTTP 中间件 / pipeline step / predict / Redis 熔断器）。
* `data/panels.py`：`date`、`timedelta`、`today_trade_date_or_last`、`symbol_to_code` 四个 import 变成未用 ⇒ 移除。
* `data/cross_section.py`：`shutil` import 移除。
* `data/ingest/akshare_adapter.py`：`ThreadPoolExecutor`、`as_completed` 移除。
* `data/parquet_store.py`：`__all__` 去掉 `path_for`/`write_whole_symbol`；一条提及死函数的注释改写。
* `data/realtime.py`：`_fetch_ths_fflow` 删除后 `_cn_amount_to_float` 亦无引用 ⇒ 一并删（避免"删了调用方的孤儿子函数"残留）。
* `api/v1/market.py`：一处注释原以 `_build_sectors`（:218-223 的既有修复）为参照 ⇒ 改为直接表述口径，不留悬空引用。
* `ml/train_lgbm.py`：`daily_ic_pairs` 的 docstring 原写"与 `daily_ics` 完全同口径" ⇒ 改为直接表述口径。

### 3.2 连带迁移：`tests/test_data_prep.py` 的夹具写入器

删除 `write_whole_symbol` 后，唯一调用方是该文件的 `_write_symbol_dataset`。
**迁移前先验证了一个反直觉事实**：`cross_section._source_files()` 用 `rglob("*.parquet")`
⇒ 镜像路径**看得见** `all.snappy.parquet`，所以报告"写入即不可见"只对
`read_symbol_dataset`/`read_symbol_year`（glob `year=*.parquet`）成立。
迁移为生产写入器 `write_year_batch`（按年分组落 `year=YYYY.parquet`）后，
夹具与线上同布局；`tests/test_data_prep.py + tests/test_p1_data.py` ⇒ **22 passed**。

## 4. ② 类：仅测试引用（27 个）——登记，不删

这些是"生产零引用、测试有守护"的能力，按 §5 第 2 类**属产品裁决**（接线 / 转正 / 删除三选一），
本轮不动，如需清理应由产品决定（删它们必须同时删/改对应测试）：

`BacktestRun`(ORM)、`CrossSectionalScaler`、`apply_adjust_df`、`apply_industry_neutral`、
`attach_text_features`、`build_alpha158_lite`、`build_alpha_v2`、`build_universe_daily`、
`chunk_text`、`clear_registry`、`cv_rank_ic_report`、`diversification_ratio`、
`ex_date_coverage`、`fetch_daily_bar_multi`、`fetch_financials`、`future_contamination_check`、
`global_feature_importance`、`load_announcements_asof`、`load_financials_asof`、
`orthogonalize_factor_panel`、`read_cross_range`、`risk_contributions`、`save_announcements`、
`save_financials`、`stop_quotes_task`、`volatility_inverse_weights_from_close`、
**`write_year_batch`（tests=53 处，全部为夹具）**。

### 4.1 自我纠正（必须记录，因为它改变过我的行动）

* 上一轮我根据一次**粗 grep**（把 `tests/`、`docs/` 的命中与 `app/` 混在一起数）宣称
  "`write_year_batch` 是生产活的，报告错了"。本轮用 AST 精确复核后**推翻我自己的结论**：
  `app/` 内 5 处 = 自身定义 + `__all__` + 一句"**原实现**用 `write_year_batch` 整年覆盖，
  每日增量会把该年其它日期全部抹掉"的注释（`ingest/tasks.py:252`），`scripts/` 0 处，
  `tests/` 62 处 ⇒ **报告"零生产调用方"是对的，我上轮的"纠正"是错的**（文档里当时写的是正确结论，故无需回改文档）。
* 处置：**保留**该函数并登记为待裁决 —— 它是 53 处测试夹具的公共写入器，删除需迁移 53 处调用点；
  同时保留其风险说明（整年覆盖语义，生产禁用）。
* 教训（再次验证）：**"有引用"必须在正确的目录域内计数**，否则会把测试夹具当成生产调用方。

## 5. 反向锁

`backend/tests/test_dead_code_removed.py`（**29 条**）成对锁住"删了的不复活 / 活着的不误删"：

* 每个被删符号：模块属性不存在 + 全 `app/` 的 **AST 引用扫描为空**（注释/docstring 不算引用）；
* 每个同模块的活符号：仍可 import/调用（含报告曾误判为死的 `stamp_duty_rate`、`check_pct_limit`、`write_year_batch`）；
* `parquet_store.__all__` 不再导出死符号；
* `metrics` 的删除项在 `/metrics` exposition 里**不得出现**（否则抓取方会把"从未埋点"读成"业务零发生"）。