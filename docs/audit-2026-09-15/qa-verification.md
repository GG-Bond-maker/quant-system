# AQP 未提交改动 · QA 回归验证报告

- 执行人：严过关（QA）
- 执行时间：2026-09-15 19:15 ~ 19:55（**注意：墙钟 19:xx 是本报告一个关键变量，见 F-05**）
- 验证对象：`git status` = 60 个 M + 32 个未跟踪（后端 api/v1/*、cache/*、core/*、data/*、main.py、pytest.ini、tests/*；前端 api/*、components/*、pages/*、types/*、stores/*）
- 结论一句话：**改动整体质量高，全量离线套件历史上首次跑完（282s / 808 passed），但仍有 1 个真实源码回归（export 500）、2 处 RBAC 治理登记遗漏、1 条过期断言、1 个跨测试锁泄漏（4 用例）**。

---

## 一、环境信息

| 项 | 值 |
|---|---|
| 项目根 | `D:/Python_Project/Alpha Quant Platform` |
| Shell | Git Bash（Windows，`timeout` 可用） |
| 后端解释器 | `backend/.venv/Scripts/python.exe` → **Python 3.11.15** |
| pytest | 8.3.3；pytest-asyncio 0.24.0；**pytest-timeout 未安装** |
| 其他 | fastapi 0.115.0 / httpx 0.27.2 / anyio 4.15.1 |
| 前端 | node_modules 已存在（134 项），typescript 5.5.4，vite 5.4.6 |
| 服务进程 | **未启动任何常驻服务**（全部用 TestClient 进程内 / `npx` 一次性命令） |

---

## 二、A. 后端静态健康度

### A-1 全量语法编译 —— 通过 ✅

```bash
cd backend
./.venv/Scripts/python.exe -m compileall -q app
./.venv/Scripts/python.exe -m compileall -q app tests     # COMPILEALL_EXIT=0
# 补充：ast.parse 遍历 backend 下全部 *.py（排除 .venv / __pycache__）
# → AST_BAD_COUNT= 0
```

**结论：0 个 SyntaxError**。未跟踪的新文件 `app/data/features.py` 语法正常，且已被 `api/v1/alerts.py:31`、`research.py:32`、`studio.py:15` 正常引用（非孤儿文件）。

### A-2 venv 位置

`backend/.venv/Scripts/python.exe`（存在且可用）。本报告所有后端命令统一使用该解释器。

### A-3 `backend/pytest.ini` 改动（P0 #9 的修复点）

```diff
-addopts = -v --tb=short
+addopts = -v --tb=short -p faulthandler
+faulthandler_timeout = 600
+markers =
+    network: test performs a real external network request and is excluded from offline baselines
```

**评价（重要）**：

1. ✅ `network` marker + `scripts/run_offline_tests.{sh,ps1}`（`-m "not network"`）确实给出了可复现的离线基线入口，这是 P0 #9 的正解方向。
2. ⚠️ **`faulthandler` 并不是真正的超时修复**：`faulthandler_timeout=600` 只会在卡死 600s 后 **dump 线程栈**，**不会杀掉/跳过**卡死的用例，套件仍会继续挂。而且 600s 远超常规 CI 单测预算。
3. ⚠️ **未安装 `pytest-timeout`**，所以 `--timeout=120` 这类硬超时不可用。建议补 `pytest-timeout` 到 requirements 并加 `timeout = 120`（这才是 P0 #9 的完整修复）。
4. ✅ 真正的加速来自 `tests/conftest.py` 的一处同批改动：`os.environ.setdefault("WARM_OVERVIEW_ON_STARTUP","0")` → `os.environ[...]="0"`（强制覆盖而非 setdefault），把每次 TestClient 启动的 48s+ 预热彻底关掉。

---

## 三、B. 后端测试

### B-0 采集

```bash
timeout 240 ./.venv/Scripts/python.exe -m pytest --collect-only -q -p no:cacheprovider
# 827 tests collected in 8.29s   RC=0
```

采集无错误、无导入崩溃。

### B-1 作者提供的离线脚本

`scripts/run_offline_tests.sh` / `.ps1` 内容一致，均为：

```
cd backend && .venv/Scripts/python.exe -m pytest -q -m "not network"
```

脚本本身**正确可用**（venv 探测、失败退出码透传都对）。本次即用等价命令执行（加 `-p no:cacheprovider --no-header --tb=line` 降低噪声）。

### B-2 全量离线套件（核心结果）

```bash
timeout 1500 ./.venv/Scripts/python.exe -m pytest -q --tb=line \
  -p no:cacheprovider --no-header -m "not network"
```

```
= 8 failed, 808 passed, 8 skipped, 3 deselected, 22 warnings in 282.54s (0:04:42) =
```

**🎉 全量离线套件跑完了！** 对比历史基线：

| 基线文件 | 时间 | 结果 | 耗时 |
|---|---|---|---|
| `backend/pytest_offline_result.txt` | 09-15 01:16 | 2 failed / 773 passed | 30m23s |
| `backend/pytest_offline_result_2.txt` | 09-15 01:36 | **0 failed** / 775 passed | 15m36s |
| **本次（未提交改动后）** | 09-15 19:15 | **8 failed** / 808 passed | **4m42s** |

- **P0 #9「全量 pytest 无法跑完」：实测已解决**（耗时从 15~30min 降到 4m42s）。
- **但失败数从 0 → 8**，即本轮改动引入了 8 个新失败（详见第四节）。
- ⚠️ 该结论**带墙钟依赖**：本次在 19:xx 执行，见 F-05。凌晨跑大概率 0 failed。

### B-3 新增 8 个测试文件（本次改动核心验证对象）—— **全部通过 ✅**

每个文件单独运行（`timeout 170~280`），逐个结果：

| # | 文件 | 结果 | 耗时 |
|---|---|---|---|
| 1 | `tests/test_data_freshness_degradation.py` | **6 passed** | 1.36s |
| 2 | `tests/test_feature_version_guard.py` | **2 passed** | 0.53s |
| 3 | `tests/test_notify_sse_ticket.py` | **5 passed** | 3.23s |
| 4 | `tests/test_ops_model_governance.py` | **5 passed** | 3.18s |
| 5 | `tests/test_route_permission_contract.py` | **11 passed** | 3.35s |
| 6 | `tests/test_market_etf_performance.py` | **3 passed** | 1.45s |
| 7 | `tests/test_hotpath_portfolio_datacenter.py` | **6 passed** | 1.22s |
| 8 | `tests/test_hotpath_watchlist_panels.py` | **5 passed** | 2.82s |
| | **合计** | **43 passed / 0 failed** | ~17s |

新增测试本身质量好、离线自足、无网络依赖、无 HANG。

### B-4 经典回归文件

| 文件 | 结果 | 耗时 | 备注 |
|---|---|---|---|
| `tests/test_api.py` | **24 passed** | 60.92s | |
| `tests/test_rbac.py` | **6 passed** | 3.75s | |
| `tests/test_pipeline_lock.py` | **6 passed** | 1.13s | `TASKS` 新增 `"fetch"` 后仍全绿 |
| `tests/test_prod_config_guard.py` | **8 passed** | 0.16s | `validate_runtime_safety` 重构后仍全绿 |
| `tests/test_universe.py` | **4 passed** | **111.91s** | ⚠️ 前科挂死文件，**本次未 HANG**（`timeout 280` 内完成） |
| `tests/test_multi_source.py` | **5 passed** | 20.13s | ⚠️ 前科挂死文件，**本次未 HANG** |

**两个历史挂死文件本次均正常完成**，无 HANG 记录。

---

## 四、失败清单与智能路由判定

> 8 个失败归并后为 **5 个根因**。

### F-01【源码 Bug / 回归】`/api/v1/export/screener` 空数据时 500 崩溃 🔴 最严重

- **失败用例**：`tests/test_read_endpoints_rbac.py::test_read_endpoint_allows_authorized[/api/v1/export/screener-researcher]`
- **堆栈**：`app/api/v1/export.py:44` → `AttributeError: 'NoneType' object has no attribute 'replace'`
- **代码**：

```python
# backend/app/api/v1/export.py:44
filename = f"screener_{data['date'].replace('-', '')}.xlsx"
```

- **根因（本轮改动直接引入）**：`api/v1/screener.py` 的 `_screen()` 新增了「无预测结果 / 分区为空」的不可用分支，返回 `"date": as_of`，而 `as_of = target.isoformat() if target else None` → **`date` 现在可能为 `None`**。前端同步把 `ScreenerResult.date` 从 `string` 改成了 `string | null`（`types/p1.ts`），但**后端唯一的另一个消费方 `export.py` 没有同步适配**，仍假设 `date` 是字符串。
- **判定：源码 Bug（回归）** → **路由：工程师（Alex）**
- **建议**：`export.py:44` 改为 `day_label = (data.get('date') or 'nodata').replace('-','')`；更好的做法是在 `date is None` 时直接返回业务错误码（51001 DATA_EMPTY）。

**同一根因的第二处（低危，被 try/except 吞掉）**：

```python
# backend/app/api/v1/screener.py:402
trade_date=date.fromisoformat(_data["date"]),   # _data["date"] 为 None → TypeError
```

`_log_run` 外层有 `except Exception` 只记 warning，因此不会 500，但**会导致 `feature_runs` 审计落库静默丢失**。建议一并处理。

### F-02【测试需适配 / 治理登记遗漏】`/api/v1/notify/stream` RBAC 内省失效

- **失败用例**：`tests/test_read_endpoints_rbac.py::test_read_endpoints_have_expected_role`
- **断言输出**：

```
AssertionError: 读端点 RBAC 与预期不符 (path: (实际, 期望)): {'/api/v1/notify/stream': (None, 'viewer')}
```

- **根因**：本轮把 `/notify/stream` 的鉴权从 `Depends(require_role("viewer"))` 换成 `Depends(require_stream_viewer)`（为支持一次性 ticket）。**源码鉴权行为是正确的**——`require_stream_viewer` 内部两条分支都做了 `ensure_role(..., "viewer")`，安全性未下降；但该测试的「静态内省依赖树取角色」机制认不出新函数，取到 `None`。
- **判定：源码行为正确，测试内省机制需适配** → **路由：QA（我）/ 工程师协商**
  - 方案 A（推荐，治本）：给 `require_stream_viewer` 加可内省的角色声明（如 `@requires_role("viewer")` 标记或统一 RBAC 注册表），让治理测试自动识别；
  - 方案 B（治标）：在测试的例外表里显式登记 `/api/v1/notify/stream: viewer`。
- **⚠️ 注意**：这条测试是「防 RBAC 腐化」的治理网，直接加白名单会降低其价值，倾向方案 A。

### F-03【治理登记遗漏】新增写端点 `POST /api/v1/notify/stream-ticket` 未进注册表

- **失败用例**：`tests/test_write_endpoints_smoke.py::test_registry_matches_runtime_rbac`
- **断言输出**：

```
AssertionError: 存在未纳管的写端点（请补进注册表）: [('POST', '/api/v1/notify/stream-ticket')]
```

- **根因**：本轮新增了 `POST /api/v1/notify/stream-ticket`（`require_role("viewer")`），但硬编码的写端点注册表（`tests/test_write_endpoints_smoke.py:57`）未同步补录。
- **判定：改了但改漏（登记遗漏），源码行为正确** → **路由：工程师（补注册表）**
- 这正是团队要找的「改漏」典型：端点功能实现没问题，但**治理资产（RBAC 注册表）没跟上**。

### F-04【测试过期】`/settings/apikeys/rotate` 断言与新语义冲突

- **失败用例**：`tests/test_write_endpoints_smoke.py::test_settings_apikeys_rotate_writes_isolated`
- **断言输出**：

```
AssertionError: {'code': 40000, 'data': None,
  'message': 'API Key 功能未启用：平台当前不验证此类密钥，不能生成可用凭证', ...}
assert 40000 == 0
```

- **根因**：后端 `app/api/v1/app_settings.py:254` 已改为**刻意拒绝**（恒返回 `ERR_PARAMS`），注释写明「保留兼容路由但拒绝请求」；前端也同步删掉了 `rotateKey` / `ApiKeyEntry` / `api_keys`。**前后端一致，属于有意的废弃**。测试仍期望 `code == 0`。
- **判定：测试代码过期，源码正确** → **路由：QA（改测试断言为期望 40000）**

### F-05【跨测试污染 + 源码健壮性缺口】全局 pipeline 锁被 `sync` 长期占用，拖垮 4 个训练用例 🟠

- **失败用例（4 个，均在 `tests/test_train_service.py`）**：

```
test_start_rejected_without_torch              → assert 40900 == 52000
test_start_rejected_when_features_missing      → assert 40900 == 52000
test_start_rejected_when_samples_insufficient  → assert 40900 == 52000
test_param_whitelist_filters_unknown_kwargs    → assert 40900 == 52000
app/ml/train_service.py:422  AQPException: 管道任务 [sync] 执行中，暂不能开始训练
```

- **隔离验证**：`pytest tests/test_train_service.py` 单独跑 → **15 passed**（0 失败）。→ 确认是**跨文件污染**，非源码逻辑错误。
- **二分定位**：逐个前置文件配对运行，只有 `tests/test_screener_snapshot.py` 会触发：

| 前置文件 + test_train_service | 结果 |
|---|---|
| `test_screener_snapshot.py` | **4 failed, 19 passed** ❌ |
| `test_swr_cache.py` | 29 passed ✅ |
| `test_task_store.py` | 18 passed ✅ |
| `test_torch_device.py` | 17 passed ✅ |
| `test_trace_id.py` | 20 passed ✅ |

- **机制（用外挂 pytest 插件探针验证了 `_OWNER` 变化，未改动仓库任何文件）**：
  插件打印 `app.core.pipeline_lock._OWNER`，结果显示 `_OWNER` 在 `test_screener_snapshot.py` 的**第一个用例 setup 时**就变成 `'sync'`，并在**整个会话余下时间一直是 `'sync'`**：

  ```
  [AQP-COLLECT-FINISH] owner=None
  [AQP-SETUP-DONE] test_write_snapshot_four_boards owner='sync'   ← 从此处开始
  ...（之后 20 个用例 setup 全是 owner='sync'）
  ```

- **完整因果链**：
  1. `tests/test_screener_snapshot.py:44` 的模块级 fixture 用 `mp.setattr(get_settings(), "DATA_ROOT", private_root)` 把 `DATA_ROOT` 指向**私有临时目录**；
  2. `sync_service._auto_sync_path()` = `get_settings().DATA_ROOT.parent / ".auto_sync.json"`（`sync_service.py:415-422`）→ 私有目录下**没有这个文件**；
  3. `_load_auto_sync()` 在文件缺失时**默认返回 `{"enabled": True, "time": "15:45"}`**（`sync_service.py:425-433`）；
  4. `conftest.py:162` 已经在**共享**临时根目录写了 `enabled=False`，但被第 1 步的私有重定向**绕过了**；
  5. `main.py` lifespan 起 `auto_sync_scheduler()`，**墙钟 19:xx ≥ "15:45"** 且当日未跑过 → 立即触发**真实增量同步**（akshare 联网）→ `_sync_worker` 在后台线程里 `with pipeline_slot("sync"):`（`sync_service.py:335-339`）；
  6. TestClient 退出时 `await asyncio.wait(bg, timeout=10)`（`main.py:85`）只等 10s，**不 join 后台同步线程** → `pipeline_slot` 的 `finally` 永不执行 → **全局 `_OWNER` 永久停在 `'sync'`**；
  7. 后续所有 `start_training()` 在 `train_service.py:414` 检查 `current_pipeline_owner()` → 直接抛 `ERR_PIPELINE_BUSY(40900)`，永远走不到期望的 `ERR_TRAIN(52000)`。

- **判定：既是测试隔离缺陷，也是源码健壮性缺口** → **路由：工程师为主 + QA 配合**
  - 源码侧（工程师）：
    a. `_load_auto_sync()` 缺失配置时默认 `enabled=True` 属于「失败即开放到真实网络」，建议改为默认 `False` 或至少在 `ENV in ("test","dev")` 下强制关闭；
    b. `pipeline_slot` 被**未 join 的后台线程**持有，进程/客户端关闭时会泄漏全局锁；建议加 TTL 兜底或让 shutdown 显式等待/强制回收；
    c. `_sync_worker` 的 sync 应在测试环境（`ENV=test`）直接短路。
  - 测试侧（QA）：`test_screener_snapshot.py` 重定向 `DATA_ROOT` 后，需**同步在新目录写 `.auto_sync.json = {"enabled": false}`**。
- **⚠️ 复现提示**：该失败**依赖墙钟 ≥ 15:45**。凌晨跑同一套命令大概率全绿（历史 01:36 基线 0 failed 与此吻合）。

### 失败汇总表

| ID | 失败用例 | 数量 | 类别 | 路由 |
|---|---|---|---|---|
| F-01 | `test_read_endpoints_rbac.py::test_read_endpoint_allows_authorized[/api/v1/export/screener-researcher]` | 1 | **源码 Bug（回归，500）** | **工程师** |
| F-02 | `test_read_endpoints_rbac.py::test_read_endpoints_have_expected_role` | 1 | 治理内省未适配（源码正确） | QA / 工程师（方案 A） |
| F-03 | `test_write_endpoints_smoke.py::test_registry_matches_runtime_rbac` | 1 | **改漏：新端点未登记** | **工程师** |
| F-04 | `test_write_endpoints_smoke.py::test_settings_apikeys_rotate_writes_isolated` | 1 | 测试过期（源码有意废弃） | QA |
| F-05 | `test_train_service.py` × 4 | 4 | **跨测试锁泄漏 + 源码健壮性** | **工程师为主** |
| | **合计** | **8** | | |

---

## 五、C. 前端静态健康度

### C-1 `npx tsc --noEmit` —— 0 错误 ✅

```bash
cd frontend && timeout 280 npx tsc --noEmit
# TSC_RC=0 ; ERROR_COUNT=0
```

`node_modules` 存在（134 项），**未执行 npm install**。

### C-2 `npx vite build` —— 通过 ✅

```bash
cd frontend && timeout 280 npx vite build
# VITE_RC=0 ; ✓ built in 5.89s
# 最大 chunk：echarts 694.56 kB (gzip 230.45 kB)
```

### C-3 `node scripts/check-bundle-size.mjs` —— 通过 ✅

```
Bundle budget passed: raw <= 768000 B, gzip <= 256000 B
```

---

## 六、D. 前后端契约抽查

抽查范围：`research / notify / settings / etf / screener / stock / watchlist / portfolio`。

### ✅ 一致的项

| 模块 | 结论 |
|---|---|
| **错误码** | `frontend/src/types/api.ts` 的 `ERR` 常量与 `backend/app/core/errors.py` **逐条对齐**：40100/40101/40102/40103/40104/40105/40106/40107/40300/40400/40900/50000/51000/51001/52000/52001/53000/53001 ✅ |
| **notify** | 前端 `StreamTicket{ticket, expires_in}` 与 `auth.issue_sse_ticket()` 返回（`{"ticket":..., "expires_in": 60}`）**一致**；`streamUrl` 只拼一次性 ticket，不落 JWT ✅ |
| **screener** | `ScreenerResult` 新增 `status/as_of/reason/message/coverage/stats{today,prev,prev_date}` 与 `screener._finalize_screener_payload()` 输出**字段名完全一致** ✅ |
| **watchlist** | `WatchSummary / WatchItem` 全部字段（`amount_yi / kline_state / alert / closes / pe / pb / source / flow_total_yi / alert_kinds / quote_date`）后端均返回 ✅ |
| **portfolio** | `AssetSearchItem{code,name,type,tracking_index}` 与后端新增返回一致 ✅ |
| **settings** | 前端删 `api_keys / ApiKeyEntry / rotateKey`，后端保留路由但恒返 40000（有意废弃）→ **无契约破坏**，仅遗留可清理路由 |
| **research** | 本轮仅增加 `RequestOptions` 透传，无字段变更 ✅ |

### ⚠️ 不一致 / 半成品项

**D-01 `BlockBase` 降级契约只落地了 1/9（中等，建议补）**

- `frontend/src/types/stock.ts:393` 的 `BlockBase` 本轮新增了 `message? / as_of? / coverage? / freshness?`（全部可选）。
- 继承 `BlockBase` 的 9 个类型中，**只有 `RecommendBlock` 真正能从后端拿到这 4 个字段**（`market._build_recommend` 返回 `status/as_of/reason/message/coverage/freshness/items`）。
- 其余 **`IndicesBlock / HeatBlock / MoneyFlowBlock / SectorsBlock / AnomaliesBlock / AiStatsBlock / SentimentBlock`** 对应的 `_build_indices / _build_heat / _build_sectors / _build_anomalies / _build_ai_stats / _build_sentiment` **只返回 `status` + `reason`**，没有 `message / as_of / coverage / freshness`。
- 当前影响：无功能故障（这些面板现在仍渲染 `reason`，`AiPicksPanel` 用 `recommend.message ?? 兜底`）。但契约是**半成品**——TS 因为字段可选不会报错，**未来任何按 `message` 渲染的改动都会在这些面板静默空白**。
- 建议：要么后端补齐 6 个块的 `message/as_of/coverage/freshness`，要么前端把这些字段收窄到只有 `RecommendBlock` 声明。

**D-02 ETF 与 Market 存在两套降级契约（低）**

- `backend/app/api/v1/etf.py:495-505` 的 `_block_ok / _block_degraded / _block_unavailable` **只返回 `status` + `reason`**，没有 `message`；
- `frontend/src/types/etf.ts` 也只有 `status / reason`（`BlockStatus`），与 `stock.ts` 的 `BlockBase`（含 `message`）**不同源**。
- 同一产品内两处「降级块」语义不一致，建议统一。

**D-03 `RecommendItem` 新增 `close/pct/amount` 已对齐（提示）**

- `types/stock.ts` 为 `RecommendItem` 增加 `close? / pct? / amount?`，后端 `market._build_recommend` 的 items 确实带这三个字段（且 `_has_market_data` 用它们过滤空壳）→ 一致 ✅。

---

## 七、给决策者的 3 个最重要发现

1. **🔴 `/api/v1/export/screener` 在无数据时 500 崩溃**（`export.py:44`）。本轮把 screener 的 `date` 改成可空（前端类型也同步改了），却漏改唯一的下游消费方 `export.py`。这是**确凿的、由本轮改动引入的回归**，用户点「导出选股」遇到空榜就会白屏 500。修复成本极小，建议必修。
2. **🎉 P0 #9 实测解除**：全量离线套件**首次跑完**——808 passed / 8 failed / **4m42s**（历史基线 15~30min 且两次挂死）。两个有挂死前科的 `test_universe.py`（111s）和 `test_multi_source.py`（20s）本次均正常完成。但修复功劳主要在 `conftest` 强制关闭 `WARM_OVERVIEW_ON_STARTUP`；`pytest.ini` 的 `faulthandler` **并不杀卡死用例**，建议补 `pytest-timeout` 收口。
3. **🟠 新增端点未进 RBAC 注册表 + 全局 pipeline 锁可跨关闭泄漏**：`POST /notify/stream-ticket` 漏登记（治理网已抓到）；且 `test_screener_snapshot` 私有 `DATA_ROOT` 绕过了 conftest 的 autoSync 关闭开关，导致 19:xx 跑测试时启动真实联网同步并**永久占住全局 `sync` 锁**（后台线程不被 join），连带 4 个训练用例误报 40900。前者是「改漏」，后者是**时间炸弹**——每天 15:45 之后跑 CI 就会红。

---

## 八、附：本次执行的命令清单（可复现）

```bash
# A 静态
cd backend && ./.venv/Scripts/python.exe -m compileall -q app tests
cd backend && ./.venv/Scripts/python.exe -c "<ast.parse 全量遍历>"

# B 采集 + 全量离线
cd backend && timeout 240 ./.venv/Scripts/python.exe -m pytest --collect-only -q -p no:cacheprovider
cd backend && timeout 1500 ./.venv/Scripts/python.exe -m pytest -q --tb=line -p no:cacheprovider --no-header -m "not network"

# B 新增 8 文件（逐个）
cd backend && timeout 170 ./.venv/Scripts/python.exe -m pytest tests/<file>.py -q --tb=line -p no:cacheprovider --no-header

# B 回归文件（挂死风险者单独 timeout 280 / 工具超时 300s）
cd backend && timeout 280 ./.venv/Scripts/python.exe -m pytest tests/test_universe.py     -q --tb=line -p no:cacheprovider
cd backend && timeout 280 ./.venv/Scripts/python.exe -m pytest tests/test_multi_source.py -q --tb=line -p no:cacheprovider

# C 前端
cd frontend && timeout 280 npx tsc --noEmit
cd frontend && timeout 280 npx vite build
cd frontend && timeout 120 node scripts/check-bundle-size.mjs

# F-05 定位（外挂探针插件，写在系统临时目录，未改动仓库）
PYTHONPATH="<TEMP>/qaprobe" ./.venv/Scripts/python.exe -m pytest \
  tests/test_screener_snapshot.py tests/test_train_service.py -q --tb=no -p aqp_probe -s
```

**遵守约束说明**：全程未修改任何业务源码；未修改任何仓库内测试代码（F-05 的探针插件写在系统临时目录，未落在仓库内）；未启动任何常驻服务；单条命令均 ≤ 5 分钟（超时即记 TIMEOUT/HANG）。
