# `/ops/lineage` 冷启动 40951 复核与修复证据（kou-lineage-40951）

> 2026-09-27 收尾项。针对 `docs/audit-2026-09-27/backend-architecture-audit.md` 附-2
> 「`/ops/lineage` 冷启动抛 `ERR_UNIFIED_TASK_CONFLICT`(40951)」。

## 一、40951 无法复现（且全仓不存在）

| 检查 | 结果 |
| --- | --- |
| 全仓搜索字面量 `40951`（排除 `logs/`、模型二进制） | 仅命中 `docs/` 审计文本本身，**无任何代码生产者** |
| 搜索 `ERR_UNIFIED_TASK_CONFLICT` / `TASK_CONFLICT` / `unified`（全 git 历史 `git log --all -S`） | **0 命中**，从未存在过 |
| 搜索全仓 `conflict`（大小写不敏感，`backend/app`） | 仅 SQLite `ON CONFLICT` 语句 |
| 既有取证（`smoke-results.csv` / `probe.log` / `retest.log` / `backend-kou.log`） | 该端点**唯一**记录的失败形态是**客户端超时**（20s timeout / 32.32s 实测），**从未出现 40951** |

**实测复现（冷缓存 + 8s 内 4 路并发，修复前）**：

```
[clear] redis 删除 lineage 键 aqp:ops:lineage:2026-09-27 -> 0 个
=== 冷启动并发 4 路（同一瞬间发出）===
  #0: 发出+     1ms | HTTP=200 code=0 |   63046.7ms | from_cache=False nodes=13 | ok
  #1: 发出+     2ms | HTTP=200 code=0 |   63021.6ms | from_cache=False nodes=13 | ok
  #2: 发出+     3ms | HTTP=200 code=0 |   60893.1ms | from_cache=False nodes=13 | ok
  #3: 发出+     4ms | HTTP=200 code=0 |   62983.8ms | from_cache=False nodes=13 | ok
  code 分布: {0: 4}
```

结论：**4/4 全部 HTTP 200 + code=0，无一例 40951**。附-2 所述错误码在本仓库不可产生。

## 二、真实缺陷：冷启动惊群（thundering herd）

复现过程中量到的**真问题**：冷缓存下 N 路并发**各自**跑一次全量重扫，磁盘争用使
墙钟从单次冷扫劣化到超过前端 60s 预算。

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 单请求冷扫（1 路） | 29.34s | 29.34s（未变） |
| 冷启动并发 4 路（同一瞬间） | **60.89 ~ 63.05s**（> 前端 60s 预算 ⇒ 全部超时） | **28.70s**（4 路完成时刻一致 ⇒ 共享同一次重建） |
| 稳态命中（6 轮） | 12.6 ~ 40.6ms（上一轮 QA 复测） | 8.1 ~ 17.3ms（avg 10.3ms，未退化） |

## 三、修复方案：single-flight（同 key 只放行一次重建）

`cache/swr.py` 新增**可选** `single_flight`（默认 `False`，其余调用方行为零变化）：

- `_single_flight()`：同 key 并发只创建一个重建 `Task`，其余 `await asyncio.shield(task)`
  等待并共享结果；`write_cache` / `after_build` 在任务内部完成，故发起方中途取消
  也不会丢掉 30s 冷扫成果；完成后按身份摘除、并消费一次 `exception()` 避免
  never-retrieved 告警；每个调用方拿浅拷贝，避免并发响应互相污染标记。
- `ops.py`：端点与 `warm_lineage_cache()` 均置 `single_flight=True`，
  **同一缓存键**下「启动预热」与「预热期到达的用户请求」共享同一次冷扫。

未改动 `portfolio.py` / `core/errors.py`；未引入任何新的业务错误码。

### 预热 × 请求共享的精确验证（计数 `_compute_lineage` 真实调用次数）

```
[share] t+3.0s 发出用户请求（预热仍在进行）
  <<< 第 1 次冷扫结束 (t+28.1s)
[share] 请求返回 code=0 nodes=13 from_cache=False
[share] _compute_lineage 真实调用次数 = 1  （1 = 预热与请求共享同一次冷扫）
```

## 四、回归

```
1804 passed, 8 skipped, 216 warnings in 457.61s
```

基线 1801 passed / 8 skipped / 0 failed ⇒ 新增 3 个 single-flight 用例，**0 失败**。

新增锚点（`backend/tests/test_swr_cache.py`）：
- `test_single_flight_shares_one_build_across_concurrent_misses`（5 路并发 → build 仅 1 次，副作用仅 1 次）
- `test_single_flight_failure_propagates_to_all_waiters`（失败对每个等待方可见，且不留残骸）
- `test_single_flight_default_off_keeps_thundering_herd_behavior`（默认关闭 = 行为向后兼容）

## 五、复现命令

```bash
# 后端（关闭预热以获得真冷启动）
cd backend && WARM_OVERVIEW_ON_STARTUP=false .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 冷启动 4 路并发
python docs/audit-2026-09-27/lineage_cold_concurrency_probe.py --clear
# 预热 × 请求共享计数
python docs/audit-2026-09-27/lineage_warm_share_probe.py
```
