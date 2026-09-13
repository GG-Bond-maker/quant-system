# 代码摘要 — Sprint 1（止血与快速见效）

> 工程师：寇豆码 ｜ 日期：2026-09-05 ｜ 基线：路线图 v2（§2.1 / §3.2 / §6.1）
> 本轮环境限制：Task 子代理模型服务不可用，由主理人代行工程师角色，产出标准不变。

## 文件清单

| 文件路径 | 变更 | 职责 | 关键实现点 |
|---|---|---|---|
| `backend/app/cache/redis_client.py` | 修改 | Redis 封装 | ① `set(..., stale_ex=)` 影子键（`key:swr`）双写；② `get_stale(key)` 返回 `(value, is_stale)`；③ `try_lock/unlock` SET NX EX 防抖锁（Redis 故障退化进程内时间戳锁）；④ `delete` 同步删影子键 |
| `backend/app/cache/swr.py` | **新增** | SWR + refresh 统一封装 | `cached_or_build()`：主键命中直回 → 影子键命中回旧值（stale=true）+ 后台重建（`rebuild:{key}` 锁合并，TTL 覆盖 50s 级构建）→ 同步重建；`refresh=1` 走 `refresh:{key}` 5s 防抖锁，未抢到回当前缓存 + `refreshed_recently=true`；`write_cache()` 主键+影子键双写 |
| `backend/app/api/v1/market.py` | 修改 | 市场接口 | `/overview` 接入 `cached_or_build`（TTL 300 / stale 窗口 1800 / rebuild 锁 180s），加 `refresh` 参数，埋点 hit/miss 保留；`/index/kline` 加 `refresh` → `fetch_index_kline(force=True)` 旁路 fetch 层缓存；`warm_overview_cache` 改为影子键双写（重启后首请求可 stale 回旧值） |
| `backend/app/api/v1/screener.py` | 修改 | 选股接口 | 同款 SWR + `refresh` 参数；FeatureRun 落库改为 `after_build` 副作用（前台/后台重建都留痕，失败仅告警不影响响应） |
| `backend/app/api/v1/datacenter.py` | 修改 | 数据中心 | `/overview` 加 `refresh` 参数 → `invalidate_stats_cache()` 后重扫，响应标 `from_cache="refreshed"` |
| `backend/app/api/v1/app_settings.py` | 修改 | 系统设置 | 新增 `_system_status()` 并挂到 `GET /settings`：缓存降级披露（ping 失败 + WARN 日志，enabled=false 不算降级）+ 磁盘水位（>90% warning / >95% critical，WARN 日志） |
| `backend/app/data/etf.py` | 修改 | 行情抓取 | `_cached(..., force=)` / `fetch_kline(..., force=)` / `fetch_index_kline(..., force=)`：force 跳过进程级 TTL 缓存读，重拉结果照常回写 |
| `backend/app/data/parquet_store.py` | 修改 | Parquet 仓库 | `_atomic_write_parquet` 统一 `compression="zstd", compression_level=7`（常量 `PARQUET_COMPRESSION*`）；仅影响新写入，存量不重写 |
| `backend/app/data/pipeline.py` | 修改 | 盘后流水线 | `step_screener_dump` 直写 parquet 同步 zstd 口径 |
| `backend/app/data/text_ingest.py` | 修改 | 文本入库 | 两处 `write_parquet` 直写同步 zstd 口径 |
| `backend/tests/test_api.py` | 修改 | 测试 | `_fake` 桩补 `force` 形参（随接口签名演进的测试维护），新增默认不走 force 的断言 |
| `frontend/src/components/DataFreshness.tsx` | **新增** | 数据新鲜度组件 | 「数据截至 X · 徽标 + ⟳」；徽标：stale→橙「缓存·更新中」/ from_cache=true→灰「缓存」/ 'refreshed'→绿「已刷新」/ 其余绿「实时」；刷新中禁用 + 图标旋转；5s 内重复点击合并 |
| `frontend/src/api/market.ts` | 修改 | API | `overview`/`indexKline` 加 `refresh` 参数 |
| `frontend/src/api/screener.ts` | 修改 | API | `screen` 加 `refresh` 参数 |
| `frontend/src/api/datacenter.ts` | 修改 | API | `overview` 加 `refresh` 参数 |
| `frontend/src/api/settings.ts` | 修改 | API | 新增 `CacheStatus/DiskStatus/SystemStatus` 类型，`SettingsBundle.system?` |
| `frontend/src/types/stock.ts` | 修改 | 类型 | `MarketOverviewData.from_cache: boolean \| 'refreshed'` + `stale?` + `refreshed_recently?` |
| `frontend/src/types/p1.ts` | 修改 | 类型 | `ScreenerResult` 同款扩展 |
| `frontend/src/types/datacenter.ts` | 修改 | 类型 | `DataOverview.from_cache?: boolean \| string` |
| `frontend/src/pages/MarketOverview/index.tsx` | 修改 | 页面 | 标题行挂 `DataFreshness`（asOf=trade_date，onRefresh 带 refresh=1） |
| `frontend/src/pages/Screener/index.tsx` | 修改 | 页面 | 同上（asOf=快照日期） |
| `frontend/src/pages/DataCenter/index.tsx` | 修改 | 页面 | 同上（asOf=last_sync）；「刷新看板」按钮升级为 refresh=1 强制重扫 |
| `frontend/src/pages/Settings/index.tsx` | 修改 | 页面 | 「系统健康」块：缓存降级徽标（⚠ 缓存降级 / 缓存正常 / 缓存已关闭）+ 磁盘水位徽标（三级配色） |
| `run.md` | 修改 | 部署文档 | Redis 启动升格为「部署默认步骤」，容器名兼容 `aqp-redis`/`redis`，说明降级徽标提示 |
| `.gitignore` | 修改 | 版本管理 | `data/archive_legacy/**` 防误提交 |
| `data/archive_legacy/` | **归档（移动，未删除）** | 磁盘治理 | `models_legacy`(8.9M) / `backup_turnover_fix_20260902`(680K) / `test_parquet`(316K) 移入，合计 ~9.9M |

## 关键技术决策

1. **SWR 用影子键实现**（而非改 TTL 语义）：主键 TTL 不变保证「新鲜即命中」，影子键多保留 1800s 承接「过期先回旧值」。Redis 与内存 LRU 降级路径行为一致（LRU 双写由 `set` 统一处理）；`delete`/`aqp:*` 清理均覆盖影子键，无脏旧值残留。
2. **后台重建锁 TTL=180s**（overview 实测最坏 48~142s），与 refresh 防抖锁（5s）分开：防抖锁保护外部源不被刷爆，重建锁保护重复计算。
3. **refresh 红线落实**：refresh 只改变「读缓存」这一步，重建路径与普通 miss 完全共用（`_throttle`/质量门禁在数据层不变）。
4. **zstd 只管新写入**：读路径按 footer 自描述，新旧压缩混存兼容；不做存量重写（D 盘余量 <10G，重写风险不可接受）。
5. **磁盘水位/缓存降级挂在 `GET /settings`**：复用设置页既有轮询与展示位，为 Sprint 2 的 data_health 预警规则直接供数。

## 验证结果

- `pytest tests/test_api.py tests/test_pipeline.py` → 30 passed（首跑 1 failed：测试桩不认识新增 `force` 形参，已修）
- `pytest tests/test_p1_data.py tests/test_p2_rest.py tests/test_etf.py tests/test_universe.py tests/test_production.py` → 43 passed
- `npx tsc --noEmit` → 零错误
- `npm run build` → 成功（chunk >1.5MB 警告为存量问题，属 Sprint 3 L4 懒加载范畴）
- 全部编辑文件 `ast.parse` + 实际 import 冒烟通过；未用 import 已清理

## 已知限制 / 遗留项

1. **git 远端未配置**（Sprint 1 清单项）：需要用户提供远端地址/凭据，本轮未做。
2. `scripts/fix_bad_turnover.py` 硬编码引用 `data/backup_turnover_fix_20260902`，归档后该一次性修复脚本不可直接重跑（历史脚本，数据修复早已完成）。
3. Redis 防抖锁降级路径是进程内锁：多 worker 部署下防抖仅单进程有效（当前单实例部署无影响）。
4. overview 仍是单键整体缓存，实时块/日频块拆分（L2-2）在 Sprint 3。
5. `npx tsc`/`pytest` 全量回归由 QA 执行（见测试报告）。
