# 代码摘要 — Sprint 3（物化与 ML 增量）

> 工程师环节（python-fullstack-engineer SOP，主理人代行）｜ 日期：2026-09-05 ｜ 前置：Sprint 1/2 已交付
> 实测基线（验收对照）：/screener 冷查 2.6s；/overview 整体缓存 48s；首屏 JS 1,630KB

## 文件清单

| 文件路径 | 变更 | 关键实现点 |
|---|---|---|
| `backend/app/db/models.py` | 修改 | `ScreenerSnapshot`（路线图 DDL + 富化列 close/pct/turnover/amount/limit_pct/risk）与 `ScreenerSnapshotStats`（+stats_json）——**DDL 超集设计：写入时一次算好富化列，读取单次 SELECT 组装响应** |
| `backend/app/data/screening.py` | **新增** | 单一事实源：`filter_universe`（universe join + ST/停牌/板块过滤 + score 降序）、`enrich_items`、`compute_stats`；`write_screener_snapshot`（4 板块 top-200 幂等 upsert）、`load_screener_snapshot`（日期错位/top_k 超窗回退 None；**stats 按请求 top_k 口径现算**，prev 用前一日行表同口径重算） |
| `backend/app/api/v1/screener.py` | 修改 | `_build_items`/`_stats`/`_load_instrument_info` 改薄委托 screening（口径不分叉）；端点快照优先（refresh=1 例外=强制重算），快照响应按 SWR 同款写回 Redis（同 key） |
| `backend/app/data/pipeline.py` | 修改 | `step_screener_dump` 扩展快照写入；`step_build_features` 接线 propagate + zstd 写盘；`step_infer` 特征目录版本改为**跟随生产模型 registry 记录**（缺失回退 FEATURE_VERSION 并告警） |
| `backend/app/ml/features.py` | 修改 | `FEATURE_VERSION` bump → **`alpha_basic_v2g`**（v2 号被 P2-6 扩展因子实验占用，g=graph）；新增 `apply_propagate()`（对全部因子列做 g1_ 邻居传导，无关系数据透明降级 NaN 列） |
| `backend/scripts/build_features.py` | 修改 | 接线 apply_propagate + zstd 写盘（feature_version 单一来源自动跟随） |
| `backend/scripts/retrain.py` | 修改 | **默认采用生产配方 xsec_demean**（新增 `--no-xsec-demean` 供 A/B）——修复了重训与生产 `*_demean` 模型不对等比较导致的必然拒门禁 |
| `backend/app/data/parquet_store.py` | 修改 | **manifest（L2-3）**：`DATA_ROOT/.manifest.json`，写路径（partition/year_batch/whole_symbol）增量登记（内存+5s 节流原子落盘），`read_all_symbols`/`list_symbols_with_data` manifest 优先（首扫 pyarrow footer 一次性建索引）、`manifest_invalidate()` 供旁路写失效 |
| `scripts/fetch_universe_batch.py`（根） | 修改 | 「已有分区跳过」私有实现切换到公共 `list_symbols_with_data` |
| `backend/app/api/v1/market.py` | 修改 | **overview 拆分（L2-2）**：`_build_rt`（指数/资金/异动，TTL 45s+SWR 600s）与 `_build_daily`（**本地口径**分布/板块/推荐榜/情绪，零外部依赖，TTL 至次日 15:30）；新端点 `GET /market/overview/rt`、`GET /market/overview/daily`（支持 date 历史切换/refresh）；`/overview` 兼容端点 = 两块合并（字段全兼容）；warm 切片复用一次构建预填三键 |
| `backend/app/cache/keys.py` | 修改 | `k_market_overview_rt` / `k_market_overview_daily` |
| `frontend/src/api/market.ts` | 修改 | `overviewRt`/`overviewDaily` + `OverviewRt/OverviewDaily` 类型 |
| `frontend/src/types/stock.ts` | 修改 | rt/daily 独立接口（含 as_of/stale 契约） |
| `frontend/src/pages/MarketOverview/index.tsx` | 修改 | rt+daily **并行拉取**；盘中轮询与 ⟳ **轻刷新只刷实时块**；交易日切换只重拉 daily；子组件消费合并视图（接口零改动） |
| `frontend/src/pages/Screener/index.tsx` | 修改 | loadMarket 切日频块（只消费 trade_date，长缓存更省） |
| `frontend/src/lib/echarts.ts` | **新增** | **ECharts 按需统一出口（L4）**：echarts/core + 8 charts + 14 components + CanvasRenderer；类型全量透传（type-only 零运行时），`ECharts` 取 core 的 `EChartsType` 别名保证与 init 返回同源 |
| `frontend/src/**`（21 文件） | 修改 | `from 'echarts'` → `from '@/lib/echarts'`（运行时按需 + 类型兼容，页面内 `echarts.EChartsOption` 等引用零改动） |
| `frontend/src/App.tsx` | 修改 | **路由懒加载**：17 页 React.lazy + `PageSkeleton` 骨架 fallback（Login eager） |
| `backend/tests/test_screener_snapshot.py` | **新增** | 5 用例：四板块写入幂等/ST 剔除、快照优先读取（from_snapshot）、板块过滤、refresh 跳过快照、predictions 领先时回落实时 |

## 关键技术决策

1. **快照表 DDL 超集**（富化列 + stats_json）：路线图行表设计保留 SQL 可查性，同时把富化计算挪到写入时——读取路径单查询零回查，是 <200ms 验收的根基；stats 按请求 top_k **现算**而非读物化 JSON，避免 total/均值与 count 口径错位（实测中抓到并修复）。
2. **propagate 版本号 `alpha_basic_v2g`**：`alpha_basic_v2` 已被未接线的 P2-6 实验模块（features_v2.py，220 列方向）占用；v1 分区保留磁盘供 A/B。
3. **step_infer 特征目录跟随生产模型 registry.feature_version**（而非全局常量）：v1/v2g 并存期间推理正确性由 registry 单点保证——这是 bump 版本号最容易踩的前视坑。
4. **重训默认 xsec_demean**：首跑门禁拒绁（RankIC 0.021 vs 0.092）根因是 retrain 未传生产配方，属比较口径错误而非特征无效——修复后门禁四维全过。
5. **daily 块零外部依赖**：heat/sectors 用本地日线聚合口径（note 披露），外部源全挂时市场页日频内容完全不受影响（验收达成）。
6. **ECharts 按需的类型策略**：运行时 core + type-only 全量透传，43 处 `echarts.EChartsOption` 类型引用零改动；`ECharts` 别名必须与 init 返回同源（私有属性不兼容问题实测踩到）。

## 验证结果（性能验收）

| 指标 | 基线 | 验收目标 | 实测 | 结论 |
|---|---|---|---|---|
| /screener 快照冷查 | 2.6s | <200ms | **2.5~26ms**（from_snapshot=true） | ✅ ~1000× |
| overview 日频块 | — | <100ms、外部全挂不受影响 | 冷 15ms / 命中 11ms（纯本地） | ✅ |
| overview 实时块命中 | — | <500ms | **8ms**（冷路径为本机外部源死重试 33s，SWR 后用户恒走命中/旧值） | ✅（命中口径） |
| 首屏 eager JS | 1,630KB（gzip 521KB） | -60% | **236KB（-85.5%）** | ✅ |
| propagate 信号增量 | RankIC 0.0920 / ICIR 0.779 | +0.01~0.03 | **RankIC 0.1020（+0.010）/ ICIR 0.885**，四维门禁全过 → **已 promote** | ✅ |
| 全量回归 | 374 用例 | 0 失败 | **376 passed / 5 skipped / 0 failed**（11.5min） | ✅ |

磁盘影响：features v2g 579M（zstd，v1 406M 保留 A/B）；D 盘余量 9.8G（水位规则监控中）。

## 已知限制 / 遗留项

1. **SWR 数据层未引入**（L4 的 TanStack/SWR 部分）：页面二次切换加速当前来自代码分割 + 浏览器缓存；SWR 引入涉及 18 页数据层改造，建议独立一轮（路线图 L4 剩余项 + 骨架屏升级三类组件）。
2. L3 列裁剪/谓词下推与热月分区未做（全市场扩容前再评估，roadmap 口径）。
3. 快照 prev 口径：前一交易日无快照时 prev=None（首日部署期属预期，流水线连续运行两日后自动齐全）。
4. manifest 为读优化缓存：repair 等旁路直写不感知（已提供 `manifest_invalidate`，repair_data.py 接线列为遗留）。
5. rt 块冷路径 33s 系本机东财/新浪断连下的重试耗时（三源降级链如实执行），非回归；有网环境或命中路径无此问题。
