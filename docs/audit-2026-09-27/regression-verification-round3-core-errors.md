# core/errors.py 通用层改动 —— 定向影响面验证报告（第 3 轮）

- **验证日期**：2026-09-27
- **执行人**：严过关（Yan）· QA 工程师（独立复核，不采信工程师自报）
- **改动**：`backend/app/core/errors.py` 新增 `_jsonable_validation_errors()`，`_validation` 处理器改用它
- **约束**：未修改任何业务代码
- **原始证据**（同目录）：
  - `_r3_git.txt` —— git 状态 + errors.py 完整 diff
  - `_r3_endpoint_probe.py` / `_r3_probe.out` —— 9 模块 14 例参数校验回归
  - `_r3_logregion.txt` —— 探针期间 app.log 区间 + 关键计数
  - `_r3_target.json.txt` —— 目标场景 / 内置约束对照完整响应体
  - `_r3_helper_unit.py` / `_r3_helper_unit.out` —— 转换函数直接单元核验（含反证）
  - `_r3_pytest.out` / `_r3_pytest_summary.txt` —— 全量测试

---

## 0. 结论速览

| 验证项 | 结论 |
|---|---|
| 影响面复核（全仓自定义 validator 是否仅 1 处） | ✅ 成立，仅 `portfolio.py:57` |
| 其他端点参数校验回归（9 模块 14 例） | ✅ 全 40000，结构无变形 |
| 目标场景 `start_date > end_date` | ✅ 40000，日志仅 WARNING，0 假 ERROR |
| 代码审查（误伤 / 过度兜底） | ✅ 无误伤、无过度兜底 |
| 全量回归测试 | ✅ **1800 passed / 8 skipped / 0 failed** |
| **最终判断** | ✅ **保留（不回退）** |

---

## 1. 影响面复核（最重要）

### 1.1 全仓自定义 validator 清点

`grep -rn "field_validator|model_validator|@validator|root_validator" backend/` →

| 命中 | 位置 |
|---|---|
| 1 | `backend/app/api/v1/portfolio.py:12`（import） |
| 1 | `backend/app/api/v1/portfolio.py:57`（`@field_validator("end_date")`） |

⇒ **工程师「全仓自定义 validator 仅 portfolio.py:57 一处」的结论独立复核成立。**

### 1.2 是否存在其他「异常对象进 ctx」的路径

`grep -rn "RequestValidationError|\.errors\(\)" backend/app/` → 唯一处理器与唯一 `.errors()` 调用都在 `core/errors.py`（`:215/:220`）。

- 结论：该改动是**全局唯一校验入口**，符合「公共入口」定位。
- **补充**：`ctx["error"]` 并非只由自定义 validator 产生——pydantic 自带的 `date_parsing` / `datetime_parsing` 等错误类型同样把 `ValueError` 放进 `ctx["error"]`。本 helper 用 `isinstance(v, BaseException)` **泛化**处理，顺带覆盖了这类潜在路径（正面评价）。
- 其他把「非 JSON 原生对象」塞进响应的路径（如 endpoint 把 ORM 对象放 `fail(data=...)`）不由本改动负责，且 `jsonable_encoder` 会处理常规类型，非本次关注点。

---

## 2. 多端点参数校验回归（9 模块 14 例）

每例传一个明显非法参数，断言：`code==40000` 且响应体结构未变形（`data` 为 `list[dict]`，每项含 `type/loc/msg`，`ctx` 值均为可序列化类型）。

| 模块 | 端点 | 非法输入 | code | 结构 |
|---|---|---|---|---|
| portfolio | POST /portfolio/backtest | `start_date>end_date` | 40000 | 好 |
| portfolio | POST /portfolio/backtest | `weight=1.5` | 40000 | 好 |
| portfolio | GET /portfolio/search | `limit=0` | 40000 | 好 |
| alerts | GET /alerts/events | `limit=0` | 40000 | 好 |
| alerts | GET /alerts/events | `unread=9` | 40000 | 好 |
| etf | GET /etf/hot | `limit=0` | 40000 | 好 |
| etf | GET /etf/hot | `sort=bad` | 40000 | 好 |
| etf | GET /etf/list | `page=0&page_size=9999` | 40000 | 好（2 条错误项） |
| market | GET /market/index/kline | `limit=1` | 40000 | 好 |
| screener | GET /screener | `top_k=0` | 40000 | 好 |
| datacenter | GET /datacenter/logs | `limit=0` | 40000 | 好 |
| notify | GET /notify/stream | `ttl=1` | 40000 | 好 |
| research | GET /research/feature-importance | `top_k=0` | 40000 | 好 |
| desk | GET /desk/orders | `limit=0` | 40000 | 好 |

**14/14 全 40000，ALL_OK=True**，无一例退化成 50000。

### 2.1 日志核对（探针期间新增区间）

| 指标 | 计数 |
|---|---|
| `PydanticSerializationError` | **0** |
| `unhandled error`（假 ERROR 告警） | **0** |
| `validation error`（WARNING） | **14**（= 请求数） |

日志样例（目标场景）：
```
WARNING app.core.errors:_validation:221 - validation error:
[{'type': 'value_error', 'loc': ('body','end_date'), 'msg': 'Value error, 结束日期不能早于开始日期',
  'input': '2024-01-01', 'ctx': {'error': '结束日期不能早于开始日期'}}]
```
⇒ `ctx.error` 已是**字符串**；内置约束的 `ctx`（`{'le': 1.0}` / `{'ge': 1}` / `{'pattern': '...'}`）保持原始类型。

---

## 3. 目标场景确认

`POST /api/v1/portfolio/backtest`，`start_date=2024-06-30 > end_date=2024-01-01`：

```json
{
  "code": 40000,
  "message": "请求参数错误",
  "data": [
    {"type": "value_error", "loc": ["body","end_date"],
     "msg": "Value error, 结束日期不能早于开始日期",
     "input": "2024-01-01",
     "ctx": {"error": "结束日期不能早于开始日期"}}
  ],
  "trace_id": "6ce03aacd212"
}
```

- 修复前：`code=50000 / msg=系统暂不可用` + 一条假 ERROR 堆栈。
- 修复后：**40000**，日志**仅 WARNING**，**无** unhandled error。

---

## 4. 代码审查

### 4.1 是否误伤正常字段

`_jsonable_validation_errors` 逻辑：逐项 `dict(err)` 浅拷贝；仅当 `ctx` 为 dict 时，对其中 **`isinstance(v, BaseException)`** 的值做 `str(v)`，其余原样保留。

直接单元核验（`_r3_helper_unit.out`）：

| 输入 ctx | 转换后 | 判定 |
|---|---|---|
| `{"error": ValueError(...)}` | `{"error": "结束日期不能早于开始日期"}`（str） | ✅ 目标转换 |
| `{"le": 1.0}` | `1.0`（float 原样） | ✅ 未误伤 |
| `{"pattern": "^(a|b)$"}` | 原样 str | ✅ |
| `{"choices": ["a","b"], "meta": {"k":1}, "nothing": None}` | list/dict/None **全部原样** | ✅ 未误伤 |
| 无 ctx 项 | 整项不变 | ✅ |

- `type/loc/msg/input` 与键集合逐项比对：**全部一致**。
- 结论：**无误伤**（不会把合法字符串/list/dict 错转成字符串）。

### 4.2 是否过度兜底 / 吞错

- 函数**只做变换**，不捕获、不丢弃任何错误；无 `try/except`、无静默分支。
- `_validation` 仍为 `logger.warning`（未降级为 debug、也未升为 error）；`code` 仍为 `ERR_PARAMS(40000)`，`message` 未改。
- **反证**（确认修的是真根因）：把未转换的 `errors` 直接交给 `fail()+jsonable_encoder` → 仍抛 `PydanticSerializationError: Unable to serialize unknown type: <class 'ValueError'>`。说明修复精准命中该缺陷，而非掩盖。

### 4.3 残余风险（低）

- 若 `ctx` 内出现**非 `BaseException` 的不可序列化对象**（理论上可能存在），本 helper 不会转换，仍可能崩。但 pydantic 的 `ctx` 实际只有原始值或异常对象两类，风险极低，可接受。

---

## 5. 回归测试

| 范围 | 结果 |
|---|---|
| 全量 `pytest backend/tests -q` | **1800 passed / 8 skipped / 0 failed**（445s） |
| `PydanticSerializationError` / `unhandled error` | 0 |

> 工程师报「183 passed」应为某子集；本次跑的是**全量**，结论更强。

### 5.1 覆盖缺口（建议补，非阻塞）

- **无**任何测试针对本修复（`_jsonable_validation_errors` / `start>end` / ctx 序列化）设锚点（`grep _jsonable_validation_errors|PydanticSerializationError backend/tests` → 0 命中）。
- 现有 `test_param_date_calendar_validation.py`、`test_query_limit_bounds.py` 覆盖的是**内置约束**（ctx 为原始值），**无法捕获** ctx 序列化回归。
- 建议：补一条「`start>end` → 40000 且 `data[0].ctx.error` 为字符串」的用例，锁死本修复。

---

## 6. 最终判断

**保留该改动，不回退。**

理由：
1. 修的是**真根因**（反证成立），且是通用层的正确位置——所有端点校验错误都经此路径；
2. 影响面**已独立复核**（全仓仅 1 处自定义 validator，唯一校验处理器）；
3. 9 模块 14 例回归**全绿**，结构无变形，0 序列化错误、0 假 ERROR；
4. 转换逻辑**最小且精确**（仅异常对象→字符串，其余原样），无过度兜底；
5. 全量测试 1800 passed / 0 failed。

唯一建议：补一条回归锚点用例（§5.1）。
