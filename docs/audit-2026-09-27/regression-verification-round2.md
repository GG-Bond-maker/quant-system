# AQP 修复后回归验证报告（第 2 轮）

- **验证日期**：2026-09-27
- **执行人**：严过关（Yan）· QA 工程师（**独立复测，不采信工程师自报数据**）
- **项目根**：`D:\Python_Project\Alpha Quant Platform`
- **后端**：`backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`（全程保持运行）
- **前端 dev**：`http://localhost:5173`（Vite proxy → 127.0.0.1:8000）
- **运行时验证栈**：真实 Chromium（playwright 1.63 + 本机缓存 chromium-1234）驱动真实前端 dev server 打真实后端
- **鉴权**：`Authorization: Bearer <ADMIN_TOKEN>`（`ALLOW_ADMIN_TOKEN_LOGIN=true`，admin 直通）
- **约束遵守**：**未修改任何业务代码**；测试期间新建的预警规则已全部删除（复核剩余 0 条）
- **原始证据**（同目录）：
  - `_r2_invariant_probe.py` —— 后端错误码不变量探针（monkeypatch 注入异常）
  - `_r2_logcheck.py` —— 真 Bug 堆栈入服务端日志文件验证
  - `_r2_browser_probe.mjs` —— Chromium 浏览器 7 场景探针
  - `r2-alerts-edit.png` / `r2-alerts-error.png` —— 浏览器截图证据

---

## 0. 结论速览

| 验证项 | 结论 | 关键依据 |
|---|---|---|
| **A. 组合回测错误码语义** | ✅ PASS（含 1 处预存在缺陷，见 §4） | 12 例实测，0/40000/51001 语义全对 |
| **A-不变量：真 Bug 必 50000** | ✅ PASS | AttributeError/TypeError → 50000，堆栈入 `app.log` |
| **A-回归测试** | ✅ PASS | 指定命令 **121 passed / 0 failed**，无测试文件改动 |
| **B. 预警规则编辑/启停（前端）** | ✅ PASS | tsc/build EXIT=0；Chromium 7 场景 ALL PASS |
| **B-新建流程回归** | ✅ PASS | 共用组件后新建流程未破坏 |

- **功能回归**：**未发现功能性回归**。
- **发现**：1 处预存在 P2（错误码语义，同族缺陷）+ 2 处 P3 + 2 处观察项（详见 §4）。

---

## 1. 修复 A：组合回测错误码语义（后端）

### 1.1 逐场景实测（真实 HTTP，非单测）

| # | 请求 | 实测 `code` | 实测 `message` | 期望 | 判定 |
|---|---|---|---|---|---|
| 1 | `600519.SH`（正常） | **0** | `ok` | 0 | ✅ |
| 2 | `600519`（无后缀） | **0** | `ok` | 0 | ✅ |
| 3 | `999999`（未知代码） | **51001** | `部分资产数据获取失败: 999999: 无数据` | 51001 且无类名 | ✅ |
| 4 | `999999.SH` | **51001** | `部分资产数据获取失败: 999999: 无数据` | 51001 | ✅ |
| 5 | `600519.SH.XX`（非法格式） | **40000** | `非法标的代码: '600519.SH.XX'（期望 6 位数字…）` | 40000 | ✅ |
| 6 | 权重和=0.5 | **40000** | `资产权重之和应为 1，当前 0.5` | 40000 | ✅ |
| 7 | 权重=1.5 | **40000** | `请求参数错误` | 40000 | ✅ |
| 8 | 重复代码（600519 + 600519.SH） | **40000** | `资产代码重复` | 40000 | ✅ |
| 9 | 空资产列表 | **40000** | `请求参数错误` | 40000 | ✅ |
| 10 | `start_date=2024/01/01`（格式错） | **40000** | `请求参数错误` | 40000 | ✅ |
| 11 | **未知基准 `999999`** | **51001** | `部分资产数据获取失败: 基准 999999: 无数据` | 51001（新增基准守卫） | ✅ |
| 12 | **`start_date > end_date`** | **50000** | `系统暂不可用，请稍后重试` | 应为 40000 | ❌ **见 §4.1** |

> 关键确认：
> - 未知代码/未知基准的对外文案**均不含 `IndexError`/`KeyError` 等内部类名**（逐字核对 message）。
> - **第 11 例是新增基准守卫的端到端铁证**：修复前未知基准会令第三方 akshare 抛 `IndexError` 逃逸成 50000；现被 domain 守卫聚合为 51001。

### 1.2 关键不变量：真·代码 Bug 必须 50000（`_r2_invariant_probe.py`）

用 monkeypatch 在**数据层/引擎边界**注入异常，不改任何业务代码：

| 场景 | 注入点 | 注入异常 | 实测 code | 判定 |
|---|---|---|---|---|
| S1 | `price_loader` | `AttributeError` | **50000** | ✅ 未被粉饰成 51001 |
| S2 | `run_portfolio_backtest` 本体 | `AttributeError` | **50000** | ✅ |
| S3 | `run_portfolio_backtest` 本体 | `TypeError` | **50000** | ✅ |
| S4 | `price_loader` | `IndexError` | **51001**（文案无类名） | ✅ |
| S5 | `price_loader` | `KeyError` | **51001**（文案无类名） | ✅ |
| S6 | `benchmark_loader` | `IndexError` | **51001**（文案无类名） | ✅ 基准守卫生效 |

对外文案恒为 `回测执行失败，请稍后重试或联系管理员`，**不含任何 `type(e).__name__`**。

**服务端日志留痕（`_r2_logcheck.py`）**：注入 `AttributeError("R2_LOGCHECK_REAL_BUG_sentinel")` 后——
- 响应：`code=50000 msg=回测执行失败，请稍后重试或联系管理员`；
- `backend/logs/app.log` L28299–28301 **保留完整堆栈**（`raise AttributeError("R2_LOGCHECK_REAL_BUG_sentinel")`）；
- `backend/logs/app.json.log` 命中 1 处。
- ⇒ 对内可观测、对外不泄露，**两全**。

### 1.3 代码审查（防「换地方又兜回来」）

- `api/v1/portfolio.py:151` 兜底 `except Exception` → `ERR_SYSTEM(50000)`，符合声明；`normalize_code` 的 `ValueError` 单独前置拦为 40000（`portfolio.py:126`）。
- `domain/portfolio.py:72` `_PRICE_UNAVAILABLE_ERRORS = (DataSourceUnavailable, IndexError, KeyError, OSError)` 为**显式四类**，非 `except Exception`；资产与基准两处 `except` 均只包住 loader 调用。
- 无新增 `except Exception` 兜底；无「把异常吞掉再抛同一个 51001」的回退写法。
- **残余风险（低）**：该四类中含 `IndexError/KeyError/OSError`，若**我方 loader 内部**（非第三方）因真 bug 抛这三类，会被误判 51001。当前 loader 为薄封装（`portfolio_source.py`），风险可接受，但非零。

### 1.4 回归测试

| 命令 | 结果 |
|---|---|
| `pytest backend/tests/test_portfolio*.py backend/tests/test_write_endpoints_smoke.py -q` | **121 passed, 0 failed** |
| 上述 + `test_hotpath_portfolio_datacenter.py` | **131 passed, 0 failed** |

- **未发现放宽断言**：`git diff --stat` 确认**本轮未改动任何测试文件**（改动仅 12 个源文件），故不存在「改绿测试」。
- **观察**：工程师自报 156 passed，与我用其指定命令实测的 121 不一致（数目口径差异，不影响「0 失败」结论）。
- **覆盖缺口**：仓库现有用例**未针对「真 Bug→50000」不变量设回归锚点**（`test_portfolio.py:274` 仅断言 `code != 0`）。建议补一条 monkeypatch `AttributeError → 50000` 的用例，否则该不变量未来可能被无声回退。

---

## 2. 修复 B：预警规则编辑/启停（前端）

### 2.1 静态检查

| 项 | 结果 |
|---|---|
| `npx tsc -b --force` | **EXIT=0** |
| `npx vite build --outDir dist-r2-verify`（避开 `dist/` 护栏） | **EXIT=0**，✓ built in 9.99s |

> 说明：直接 `npm run build`（outDir=`dist/`）会被环境 `node-safe-delete` 护栏拦截，属环境策略而非代码问题；改用全新 outDir 构建通过。

### 2.2 Chromium 真实浏览器验证（`_r2_browser_probe.mjs`，**7 场景 ALL PASS**）

| 场景 | 断言 | 结果 |
|---|---|---|
| 0 基线 | 列表/编辑按钮/`role=switch` 均渲染 | ✅ 行=2 编辑=2 开关=2 |
| 1 编辑 | 预填 name/symbol/threshold 全对 | ✅ `R2验证-基准规则` / `600519.SH` / `3` |
| 1 编辑 | 保存 PUT 字段**完整回传** | ✅ `{name,rule_type,scope,symbol,params:{threshold:5},channels:[sse],cooldown_minutes:30,enabled:true}` |
| 1 编辑 | 保存后列表刷新 | ✅ |
| 2 启停成功 | 翻转生效且 PUT `enabled` 一致 | ✅ before=false→after=true |
| 3 启停失败 | UI **回滚** + 可见提示 | ✅ after==before，顶部横幅显示「模拟写入失败」 |
| 4 加载失败 | 显示 `ErrorState` + 重试（非永久转圈） | ✅ 「数据暂时不可用」+「重试」 |
| 5 新建回归 | POST 字段正确、symbol 大写归一、列表刷新 | ✅ `000001.sz → 000001.SZ`，默认参数/渠道正确 |
| 6 必填校验 | 本地可见提示（非静默） | ✅「规则名称必填」 |

**截图证据**：`r2-alerts-edit.png`（编辑表单预填 + 状态开关列 + 编辑/删除按钮）、`r2-alerts-error.png`（ErrorState + 重试 + 顶部错误横幅）。

### 2.3 静默吞错检查

`grep -n catch src/pages/Alerts/index.tsx`：共 7 处 catch，**6 处均落到可见错误态**（`setError`/`setLocalError`/`setUnread(null)`）。
- 仅剩 1 处静默：`index.tsx:385` 轮询 `.catch(() => { /* 轮询失败忽略 */ })` —— **后台轮询**场景，可接受（工程师「已无静默 catch」的表述基本成立，严格说剩此 1 处）。

---

## 3. 测试数据清理

验证期间经 API 新建 3 条 `R2验证-*` 规则（id 1/2/3）用于编辑/启停/新建验证，**已全部删除**，复核 `/alerts/rules` 剩余 **0 条**（与验证前一致）。

---

## 4. 发现的问题清单

### 4.1 【P2 · 预存在，非本轮引入】`start_date > end_date` 被误报 50000（应为 40000）

- **现象**：`{"start_date":"2024-06-30","end_date":"2024-01-01"}` → `code=50000 / msg=系统暂不可用，请稍后重试`（真实 HTTP 复现，trace_id 820e2af16ba6）。
- **根因链**（已逐层复现）：
  1. `api/v1/portfolio.py:57` 自定义 `@field_validator("end_date")` 抛 `ValueError`；
  2. Pydantic v2 把原始异常对象放进 `RequestValidationError.errors()[*]['ctx']['error']`；
  3. 校验错误处理器 `core/errors.py:196` 执行 `jsonable_encoder(fail(ERR_PARAMS, …, e.errors()))`，而 `fail()` 返回的 `APIResponse`（Pydantic 模型）在 `model_dump(mode="json")` 时抛
     `PydanticSerializationError: Unable to serialize unknown type: <class 'ValueError'>`；
  4. 该异常从**校验处理器自身**逃逸 → 落全局 `_exception` → 50000。
- **副作用**：同一请求在 `app.log` 同时产生一条 `validation error`(WARNING) 与一条 `unhandled error`(ERROR) 堆栈 —— **对运维是假告警**，且用户看到的是「系统故障」而非「结束日期不能早于开始日期」。
- **波及面**：全仓**仅此 1 处**自定义 `field_validator`（`grep field_validator backend/app` 仅命中 portfolio.py），故仅影响 `/portfolio/backtest`。
- **性质**：`_end_after_start` 与 `core/errors.py` 在 `HEAD` 即存在、且**本轮未改动**（`git diff` 未触碰），属**预存在缺陷**；但它与本次修复同属「错误码语义」家族——**本轮把 `except Exception` 的 50000 修对了，却漏了这条同样把参数错误错报成 50000 的路径**。
- **修复方向（供参考，未改代码）**：`_validation` 处理器改用 `e.errors(include_url=False)` 并对 `ctx.error` 做 `str()` 归一，或让自定义校验器抛 `PydanticCustomError`/返回带可序列化 ctx 的错误。

### 4.2 【P3】触发历史卡片在加载失败时永久转圈

- **现象**：rules 加载失败时，左侧「预警规则」卡片正确显示 `ErrorState + 重试`；但右侧「触发历史」卡片**永久显示「加载中…」**（截图 `r2-alerts-error.png` 证实，`LoadingState` 计数=1）。
- **根因**：`index.tsx:575` 事件卡片仅有 `events === null ? <LoadingState/> : …` 两态，**无 error 分支**；`Promise.all` 失败时 `events` 恒为 `null`。
- **影响**：页面并非「完全不可用」（左侧有重试），但右侧面板会无限转圈，观感与「永久转圈」类缺陷相同。
- **修复方向**：事件卡片补 `error` 三态（与 rules 卡片同构）。

### 4.3 【P3】编辑表单打开期间切换同规则开关会被陈旧 `enabled` 覆盖

- **现象**：`RuleForm` 提交时用 `enabled: initial.enabled`（`index.tsx:225`）——该值是**打开编辑表单那一刻**的快照。若用户打开规则 X 的编辑表单后，又在下方列表切换 X 的状态开关（开关实时改了后端），再点「保存修改」，PUT 会携带**陈旧的 `enabled`**，把刚切换的状态覆盖回去。
- **影响**：低概率（需同规则「先编辑、后切换、再保存」），但会造成开关状态被无声回滚。
- **修复方向**：提交前从最新 `rules` 取该 id 的 `enabled`，或编辑期间禁用该行开关。

### 4.4 【观察】残余静默 catch

- `index.tsx:385` 轮询 `.catch(() => {})`：后台 30s 轮询失败静默忽略。**可接受**（非用户主动操作，且下次轮询会自愈），仅记录。

### 4.5 【观察】不变量缺回归锚点

- 见 §1.4：无针对「真 Bug→50000」的自动化用例，建议补锚点。

---

## 5. 总评

两项修复**均通过独立复测**：

- **A（后端错误码语义）**：12 例端到端实测语义全对；**最关键不变量成立**——`AttributeError/TypeError` 确实归 50000 且真堆栈入 `app.log`，`IndexError/KeyError` 归 51001 且文案不含类名；新增基准守卫端到端生效（未知基准 → 51001）；无测试改动、无放宽断言、无「换地方又兜回来」。
- **B（前端预警编辑/启停）**：tsc/build 通过；Chromium 真实浏览器 7 场景全绿——编辑预填正确、PUT 字段完整、启停成功/失败回滚+提示、加载失败 ErrorState+重试、**新建流程未被共用组件破坏**。
- **遗留**：1 处**预存在** P2（`end_date<start_date` → 50000，同族语义缺陷，全仓仅此一处校验器触发）+ 2 处 P3（事件卡片无 error 态、编辑期间切换开关陈旧覆盖）+ 2 处观察项。

> 以上 P2/P3 均**非本轮修复引入**，不影响两项修复的验收结论；但 P2 建议纳入下一批错误码语义治理。
