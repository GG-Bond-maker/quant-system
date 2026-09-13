# 测试报告 — Sprint 1（第 1 轮）

> QA：严过关（Yan）｜ 日期：2026-09-05 ｜ 被测：Sprint 1 SWR 缓存与系统集成（工程师：寇豆码）
> 新增测试文件：`backend/tests/test_swr_cache.py`（14 个用例）

## ROUTE

```
ROUTE: NoOne
```

判定依据：第 1 轮共发现 1 个失败用例，经定位为**测试代码自身设计缺陷**（非源码 Bug），修复后重跑全部通过；未发现源码缺陷，无需路由给工程师。

## 用例统计

### 1. 新增测试 `tests/test_swr_cache.py`

| 项 | 结果 |
|---|---|
| 用例数 | 14 |
| 通过 | 14 |
| 失败 | 0（首轮 1 个测试自身缺陷，修复重跑后通过） |
| 耗时 | ~0.2s |

测试环境与隔离策略：
- **全程离线**：autouse 夹具将 `RedisClient._ensure` monkeypatch 为返回 None，强制覆盖「Redis 不可用 → 进程内 LRU + 进程内时间戳锁」兜底路径（实测本机 127.0.0.1:6379 不可达，TimeoutError，与离线纪律一致）；
- TTL 过期场景一律用 `_Clock` 时间源替换（monkeypatch `app.cache.memory.time` / `app.cache.redis_client.time`），不真实 sleep，确定性通过；
- 复用 `tests/conftest.py` 既有隔离（SQLITE/DATA_ROOT/MODEL_ROOT 临时目录 + autouse 建库夹具），pytest-asyncio auto 模式（pytest.ini `asyncio_mode = auto`）。

覆盖清单（对照任务要求 ①–⑦ 全覆盖 + 2 个补充边界）：

| 用例 | 覆盖点 |
|---|---|
| `test_try_lock_mutex_and_unlock_reacquire` | try_lock 互斥；unlock 后立即可再抢；不同 key 互不干扰 |
| `test_try_lock_expires_after_ttl` | TTL 过期后可再抢（含未到期仍互斥的中间检查点） |
| `test_set_get_stale_lifecycle` | set(stale_ex=) 双写 + get_stale 三段生命周期：(v,False) → 主键过期影子键在 (v,True) → 全过期 (None,False) |
| `test_set_no_shadow_when_stale_ex_missing_or_le_ex` | stale_ex 缺省 / stale_ex ≤ ex 不写影子键（spy 拦截 lru_set 验证守卫，含对照组：stale_ex > ex 正常双写） |
| `test_delete_clears_shadow_key` | delete 同时删主键与影子键（过期临界点删后不得再回 stale 旧值） |
| `test_cached_or_build_primary_hit_no_build` | ① 主键命中：不调 build、from_cache=True、metrics=hit |
| `test_cached_or_build_stale_hit_background_rebuild` | ② 影子键命中：响应立即返回 stale=True、build 不被同步调用；后台重建完成后再次请求得新值（轮询 `_bg_tasks`） |
| `test_cached_or_build_full_miss_sync_rebuild` | ③ 全 miss：同步重建 from_cache=False、metrics=miss、回写后可命中 |
| `test_cached_or_build_refresh_recomputes` | ④ refresh=1：from_cache="refreshed" |
| `test_cached_or_build_refresh_debounce_merges` | ⑤ refresh=1 二连发：第二次 refreshed_recently=True 且 build 只调 1 次 |
| `test_cached_or_build_after_build_failure_sync_path` | ⑥ after_build 抛异常：不影响响应、缓存已写（同步重建路径） |
| `test_cached_or_build_metrics_hit_miss_callback` | ⑦ on_metrics hit/miss 分派正确；埋点回调自身抛异常被吞、不影响主流程 |
| `test_cached_or_build_refresh_lock_occupied_empty_cache_falls_back` | 补充边界：refresh=1 锁被占 + 缓存全空 → 退化同步重建，不返回空响应 |
| `test_cached_or_build_stale_bg_rebuild_after_build_failure` | 补充边界：后台重建路径 after_build 抛异常 → 只告警，新值照常可读 |

### 2. 全量回归（backend）

命令：`backend/.venv/Scripts/python.exe -m pytest -q`

| 项 | 结果 |
|---|---|
| 总数 | 359 |
| 通过 | **354** |
| 失败 | **0** |
| 跳过 | 5（均为 `tests/test_e2e.py`，原因：`E2E 需设置 E2E_AVAILABLE=true 且启动服务`——环境门控默认跳过，属正常） |
| 耗时 | 896.44s（14:56） |
| 环境 | Python 3.11.15 / pytest 8.3.3 / pytest-asyncio 0.24.0 / redis-py 5.0.8 / win32 |

无因外部网络波动失败的用例，未触发重跑。

### 3. 前端类型检查

命令：`cd frontend && npx tsc --noEmit`

| 项 | 结果 |
|---|---|
| 错误 | **0** |

## 失败用例明细

| # | 用例 | 轮次 | 定位 | 处置 | 回归 |
|---|---|---|---|---|---|
| 1 | `tests/test_swr_cache.py::test_set_no_shadow_when_stale_ex_missing_or_le_ex` | 第 1 轮（测试自身） | **测试设计缺陷**，非源码 Bug：`stale_ex <= ex` 时影子键 TTL 必然短于主键，主键过期时假想的影子键也已过期，`get_stale` 黑盒结果无法区分「未写影子键」与「写了先过期」 | 改用 spy 拦截 `redis_client.lru_set` 写调用，直接断言守卫（stale_ex 缺省 / ≤ ex 均不写影子键；> ex 正常双写，含对照组） | 重跑通过 |

源码 Bug：0 个。

## 遗留问题

1. **Redis 真实链路未在本轮覆盖**：按「全部离线、不得联网」纪律，`test_swr_cache.py` 强制走进程内兜底路径；Redis SET NX EX / 影子键的 Redis 侧行为（含跨进程锁语义）需在部署环境用真 Redis 做一次冒烟（建议 Sprint 2 补 docker-compose redis 的集成测试，用 marker 门控）。
2. `tests/test_p1_data.py:81` 使用未注册的 `@pytest.mark.real_network`（存量问题，产生 PytestUnknownMarkWarning），建议在 pytest.ini 注册 `markers`。
3. `test_pipeline.py` 运行时有 pytest-asyncio「unclosed event loop」DeprecationWarning（存量，pytest-asyncio 0.24 提示未来版本不兼容，非本轮变更引入）。
4. 全量回归含 matplotlib/pyparsing 第三方 DeprecationWarning（存量，无碍）。
5. E2E 5 例默认跳过：需要 `E2E_AVAILABLE=true` + 启动服务，本轮按惯例未执行。

## 结论

Sprint 1 被测变更（SWR 影子键双写 / get_stale / try_lock-unlock / cached_or_build 全路径 / after_build 隔离 / 埋点）在离线兜底路径下行为符合验收口径；全量回归无回归；前端类型检查零错误。**ROUTE: NoOne**（无需返工）。
