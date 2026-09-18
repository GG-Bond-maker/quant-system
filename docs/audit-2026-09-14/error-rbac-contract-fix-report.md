# 错误码与 RBAC 契约修复报告

## 本轮：错误码撞码修复

### 结论

- 保留 `ERR_CREDENTIALS = 40104`，兼容既有登录客户端与断言。
- 将 `ERR_PIPELINE_BUSY` 固定为 `40900`，表达同步、流水线、镜像或训练任务之间的资源状态冲突。
- 后端所有管道繁忙返回点均引用 `ERR_PIPELINE_BUSY`，未发现残留的业务硬编码 `40104`。
- 前端统一错误码表已补充 `PIPELINE_BUSY: 40900`；流水线繁忙显示“数据流水线正在执行，请稍后重试”，不会清除有效登录会话。
- 研究页同时识别计算限流 `40103` 与管道繁忙 `40900`，展示可重试提示。

### 修改范围

- `backend/app/core/errors.py`
- `backend/app/api/v1/datacenter.py`（修正文档注释中的旧码）
- `backend/tests/test_route_permission_contract.py`（固化 40104 / 40900 唯一性）
- `frontend/src/types/api.ts`
- `frontend/src/api/client.ts`
- `frontend/src/pages/Research/index.tsx`
- `docs/auth-register.md`
- `docs/audit-2026-09-14/AUDIT-SUMMARY.md`
- `docs/audit-2026-09-14/backend-architecture-review.md`
- `docs/audit-2026-09-14/backend-qa-report.md`

### 最小验证

```text
backend/.venv/Scripts/python.exe -m pytest \
  backend/tests/test_route_permission_contract.py::test_business_error_codes_are_unambiguous \
  backend/tests/test_auth.py::test_auth_bad_password \
  backend/tests/test_pipeline_lock.py -q
结果：通过（exit 0）

npm --prefix frontend run type-check
结果：通过（exit 0）
```

## RBAC 单一事实源与路由契约

### 后端授权口径

- 删除未被路由执行且与 predict 契约冲突的 `ROLE_PERMISSIONS` 死矩阵。
- `ROLE_RANK = {viewer: 0, researcher: 1, admin: 2}` 是角色层级的唯一事实源。
- `ensure_role` 基于 `ROLE_RANK` 执行单调层级判断；`require_role` 在应用装载时拒绝未知/拼错角色，避免静默生成永远拒绝的依赖。
- 关键路由声明保持：只读行情 viewer；predict/backtest/research researcher；引擎设置 admin。

### 自动化权限契约

`backend/tests/test_route_permission_contract.py` 覆盖：

- 匿名访问受保护端点返回 `40100`。
- viewer 访问 predict、backtest、research 返回 `40300`。
- researcher 成功越过上述研究端点的鉴权层。
- 只读行情：viewer 与 researcher 均成功。
- 管理设置：researcher 返回 `40300`，admin 成功。
- 运行时扫描 FastAPI 路由依赖，断言关键端点的 `require_role(...)` 声明未漂移。

### 前端守卫核对

- `App.tsx`：基础行情/个股/设置页面使用 viewer 守卫；backtest/research 等计算页面使用 researcher 守卫。
- `RequireAuth.tsx` 的 `ROLE_RANK` 与后端三层级一致，viewer < researcher < admin。
- 个股页整体允许 viewer 读取基础数据，但 viewer 不再发送必然返回 403 的 predict 请求；预测卡显示“需要研究员及以上角色”，researcher/admin 才请求预测。
- 403 与 40900 均不会被认证拦截器当作失效凭证清除会话。

### RBAC 最小验证

```text
backend/.venv/Scripts/python.exe -m pytest \
  backend/tests/test_route_permission_contract.py -q
结果：通过（exit 0）

backend/.venv/Scripts/python.exe -m pytest \
  backend/tests/test_rbac.py -q
结果：通过（exit 0）

npm --prefix frontend run type-check
结果：通过（exit 0）
```

补充：尝试合并执行 `test_rbac.py + test_read_endpoints_rbac.py` 时，在 120 秒限制内被 SIGTERM；该历史矩阵包含多条落入领域计算/外部路径的行为测试。本轮关键路由已由完全隔离的契约测试覆盖，无失败断言。
