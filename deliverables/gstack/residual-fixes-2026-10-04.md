# 残留项修复交付报告（2026-10-04 晚）

> 上游报告：`pre-launch-check-aqp-2026-10-04.md`（全检 33 项）、`prelaunch-fix-and-datasource-verify-2026-10-04.md`（修复 + 数据源验证）。
> 本轮 = 用户对"5 项残留"中的 **3 项**点头后执行「动手」，另 **新发现并修复第 4 项**。
> ⚠️ 所有改动**仍未提交**（37 改 + 新增文件），需人工 review 后 commit。

---

## 一、用户指定 3 项 · 处理情况

### 1) SSE 连接名额「窄窗永久泄漏」 — 已修

**缺陷**：名额在**建流前**占用（刻意设计，超限时才能直接回 429 信封而非先建流再关）。但归还原先只写在 `gen()` 的 `finally` 里，而 **CPython 对"尚未启动"的异步生成器调用 `close()` 不会执行函数体、也就不会跑 `finally`**。于是：客户端在「响应已建 → 生成器首次 `__anext__`」之间断开 ⇒ 任务被取消 ⇒ 生成器对象被 GC ⇒ 名额**永久泄漏**（每用户额度仅 5，泄漏 5 次后该用户被自己的额度永久拒绝）。窗口只有几层 await（微秒级），但后果与概率不成比例。

**改法**（`backend/app/api/v1/notify.py`）：
- 新增 `_SseSlotGuard`，把归还**唯一收敛**到 `_release()`，由**三处**共同触发且**幂等**（`_closed` 标志）：`__anext__` 正常结束/抛错、`aclose()`（Starlette 取消/收尾路径）、`__del__`（兜住"未启动即被 GC"的窄窗）。
- **显式实现 `athrow()`**：不实现时 `body_iterator.athrow(...)` 会抛 `AttributeError`，而调用方只捕"取消类异常" ⇒ AttributeError 被当"已断开"吞掉、**名额却没人归还**（最隐蔽的假绿）。
- `gen()` 内**移除** `finally` 里的释放（否则双释放会把别人的名额减掉）。
- 类型标注必须是 `AsyncGenerator[str, None]`（**不是** `AsyncIterator`）：`aclose/athrow` 只在后者上。首版用了宽类型 ⇒ mypy 报 `"AsyncIterator[str]" has no attribute "aclose"`（+1 error），已改回并删掉 `athrow` 上多余的 `type: ignore`。

**证据**：
- 新增 `tests/test_notify_sse_limits.py::test_unstarted_generator_release_on_gc`（`del r; gc.collect()` 后计数必须回 0）。
- **证伪**：把守卫替换成透传版（不释放）⇒ 该路径计数停在 **1（泄漏）**；真实守卫下为 **0**。
- `tests/test_notify_sse_limits.py` + `test_notify_sse_ticket.py` = **10 passed**。

### 2) prod CORS 黑名单只拦 `:5173` — 已修

**缺陷**：启动期"生产环境仍含开发源"的黑名单是**字面量**比对（只认 `:5173`），`http://localhost:8080`、`http://127.0.0.1:8000`、`https://0.0.0.0:443`、`http://[::1]:8080` 等一律漏过。

**改法**（`backend/app/core/config.py`）：新增 `_is_dev_origin(origin)`，按 **host** 判定（`localhost` / `0.0.0.0` / `::1` / `127.*`，**任意端口与协议**；不可解析 ⇒ 保守视为开发源），替换原字面量比对。

**证据**：`tests/test_runtime_safety.py::test_prod_rejects_unsafe` 新增 **5 组**参数化（含混合源），全绿。

### 3) `/market/overview/rt` 的 anomalies 子块状态不诚实 — 已修

**缺陷**：`_build_anomalies` 原对"上游快照源不可用"与"扫描成功但当日无异动"**返回同一形态**（空列表），且失败只 `logger.debug` ⇒ 运维与前端都无法区分"没数据"与"取不到数"。

**改法**（`backend/app/api/v1/market.py`）：改为三态并各自带 reason —
- 上游快照源不可用 ⇒ `status="unavailable"`；
- 扫描抛异常 ⇒ `status="degraded"` + `type(e).__name__`；
- 扫描成功 ⇒ `status="ok"`（**空列表仍算 ok**，reason 写明"实时源正常，当日无 ≥9.8% 的涨停近似标的"）。
- 日志级别 `debug` → `warning`。

**证据**：新增 `tests/test_anomalies_status_contract.py`（4 用例 + 参数化，用 `_FakeAk`/`_patch_ak` 桩钉住三态契约）。

---

## 二、本轮新发现第 4 项（非用户指定，已修）

### 4) `NoProductionModelError` 逃逸 ⇒ 被误报成 `ERR_SYSTEM(50000)`

**缺陷**：`/api/v1/stock/{symbol}/predict` 是前端个股详情页的核心端点。`predict_symbol` 只 catch `FileNotFoundError`，而 `registry.load_prod_model` 在"无 `is_production=1` 行 / 产物文件缺失 / 后缀不符"时抛的是 `NoProductionModelError`（`ModelRegistryError` 子类，**不是** `FileNotFoundError`）⇒ 异常**逃逸**到全局兜底 ⇒ 用户看到 **「系统暂不可用，请稍后重试」**（**假陈述**：平台没有故障，只是还没 promote 模型），同时打一条 **ERROR 全栈日志**污染告警。

关键点：`registry` 已经精心写好了**可操作提示**（"请先训练并显式执行 promote：python scripts/promote_model.py --version <v>"），却被丢进通用 50000 —— 典型的"意图与行为背离"。

**改法**（`backend/app/ml/predict.py`）：`except (FileNotFoundError, ModelRegistryError)` → `AQPException(ERR_INFER, str(e))`，与 `research.py::_resolve_production_lgbm` 的**既有口径**对齐（该函数有测试 `match="生产模型文件缺失"` 钉住）。

**刻意未做**：不改异常继承体系（把 `ModelRegistryError` 也变成 `AQPException`）。原因：`ModelRegistryError(RuntimeError)` **已被** `ml/monitor.py:1043` 的 `except RuntimeError` 捕获（判据 19 的作用域陷阱），改基类会波及该处，爆炸半径不可控。

**严重度 = 中（不阻塞）**：真实库 `data/sqlite/aqp.db` 中恰有 1 个生产模型（`lgbm_v1/20260905_181713_repaired`, `is_production=1`），故当前环境不显现；只在"尚未 promote / 产物被删 / 回滚"等**代码明确设计要处理**的状态下触发。

**发现路径（可复用）**：见下节。

---

## 三、第 4 项是怎么被发现的（方法论）

1. 跑针对性子集 `pytest -k "notify or stream or market or overview or rt or config or runtime or anomalies or body_limit"` ⇒ 红 1 例：`test_read_endpoints_rbac.py::test_researcher_read_allows_viewer_when_rbac_open[/api/v1/export/screener]`，报 `UnicodeDecodeError: byte 0x81`。查实现发现 `/export/screener` **有数据时返回 xlsx 二进制**、无数据时才抛 `ERR_DATA_EMPTY` 回 JSON，而用例用 `r.json()` ⇒ **数据依赖的脆弱断言**。
2. **`-k` 是子串匹配**：`rt` 命中了 `export`/`report`/`start`，把 568 个无关用例拉进来 ⇒ 先 `--collect-only` 认清命中集合。
3. 用**最小前置文件**复现（4 个候选污染源 + RBAC 文件）⇒ 失败端点**变成了** `/api/v1/stock/600519.SH/predict`（**随前置文件变化** ⇒ 顺序依赖数据污染坐实），异常是 `NoProductionModelError`。
4. 读代码：`test_api.py` 的 fixture teardown 删了模型目录与 hfq 行情，却**没有取消 promote**；另有测试写入 hfq 但注册表无生产模型行 ⇒ 两条路都会让该端点走到"有行情、无模型"分支。
5. **根因落在生产代码**（未映射的异常），**不是**测试问题 —— 且**全量套件 0 failed 完全抓不到它**，因为触发需要特定的执行顺序。

**证伪（before/after 同一命令）**：修复前该 5 文件复现 = **2 failed**；修复后 = **175 passed / 0 failed**。

---

## 四、验收证据

| 门禁 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `pytest tests -m "not network"` | **2235 passed / 8 skipped / 4 deselected / 0 failed**（618.78s，`PYTEST_EXIT=0`）。修复前基线 = 2234 passed / 0 failed；+1 = 本轮新增回归用例 |
| 类型 | `mypy app/ --ignore-missing-imports` | **2 errors**，均在 `research.py:669` `_bar_year_window` arg-type = **既有基线**（`git show HEAD:` 核实不在本次 diff 内）。CI 无 `continue-on-error` |
| 静态 | `ruff check app tests scripts --select F,E9`（**CI 口径**） | **All checks passed** |
| 前端 | `check-timeout-consistency.mjs` | ✅ 11 档 / LONG_PATHS 2 / CAPPED_PATHS 30 = 后端 `_PATH_BUDGETS` 2 / `_PATH_MAX_BUDGETS` 30 |
| 前端 | `npm run style:check` / `npm run nav:check` | ✅ 均通过 |

> ⚠️ **陷阱备忘**：裸 `ruff check`（本仓无 `[tool.ruff]`/`pyproject.toml` ⇒ 走默认规则集）会报 **47 项**（B008 / RUF100 / I001 / DTZ005 …），**与 CI 门禁无关**，不可据此"修"。
> ⚠️ **测试结果边界**：`-k` 子集红 ≠ 回归；全量绿 ≠ 无顺序敏感缺陷（本次即是反例）。
> ⚠️ **宿主守卫噪音**：`pytest_unconfigure`（`tests/conftest.py:313`）会 `rmtree` 整个临时数据根，偶发触发宿主的**批量删除守卫**并打出 `SystemExit: 1` 堆栈 —— 它在**汇总打印之后**才发生，`PYTEST_EXIT=0`，**不影响结论**（判据 39/40）。若只想看结论，请把输出重定向到文件后 `grep passed`，不要用 `tail -N`（堆栈会挤掉汇总行）。

---

## 五、仍未动手（明确保留）

| 项 | 为何不动 |
|---|---|
| 计算池**队列本身无界** | 当前靠 `compute_slot` 闸门 + single-flight 缓解；加"有界队列 + 提交处复用同一信号量"会**必然死锁**（持 1 等 1），需要独立基础设施 |
| 登录限速改为按 IP | 依赖反代 `X-Forwarded-For` 配置，属部署面而非代码面 |
| `data/etf.py` 的 `js.eval` | 影响面大、收益低，本轮明确不碰 |
| `test_read_endpoints_rbac.py` 里 `/export/screener` 等**数据依赖断言** | 属测试卫生问题（应改为断言"非鉴权错误"而不解析二进制体）；本轮只修生产侧根因，未改测试，避免扩大范围 |
| 跨端点**业务码不统一**：`/stock/{symbol}/predict` 用 `ERR_INFER(52001)`，而 `research._resolve_production_lgbm` 对同一条件用 `ERR_DATA_EMPTY(51001)` | 统一口径需决定"规范码"并同步前端 `types/api.ts`，属设计决策，留给用户裁决 |
| 第 4 项修复会**透传** `NoProductionModelError` 的原始消息，其中"产物文件缺失"分支**含服务端绝对路径**（`生产模型文件缺失：{model_path}`） | 这与既有先例 `research.py:352`（同款消息、有测试钉住）**一致**，属本仓既有实践；若要收紧，应两端一起改为"固定可操作文案 + 路径只进日志"，不宜单改一处 |
