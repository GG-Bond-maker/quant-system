# 自助注册功能说明（2026-09-13）

登录页新增「注册」入口，前后端配套实现。

## 端点

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/v1/auth/register` | 公开 | 建号并直接签发 JWT（与 login 同构，前端无需二次登录） |
| GET | `/api/v1/auth/register/status` | 公开 | 返回开关与规则，前端据此决定是否展示入口 |
| POST | `/api/v1/auth/login` | 公开 | 既有 |
| GET | `/api/v1/auth/me` | 需 JWT | 既有 |

### POST /auth/register

请求：`{"username": "...", "password": "..."}`

| 校验 | 规则 | 错误码 |
|---|---|---|
| 开关 | `ALLOW_REGISTRATION=false` 时拒绝 | 40106 |
| 用户名 | 3~64 位，仅 `字母/数字/_/./-` | 40000 |
| 密码 | ≥6 位，且不可与用户名相同 | 40000 |
| 重名 | 已存在（含并发唯一索引兜底） | 40105 |
| 限速 | 同一用户名 5 次/小时 | 40107 |

成功响应与 `/login` 完全一致（`access_token` / `expires_at` / `user`），角色默认 `viewer`。

### GET /auth/register/status

```json
{"enabled": true, "default_role": "viewer", "min_password_length": 6,
 "username_pattern": "^[A-Za-z0-9_.-]{3,64}$"}
```

## 配置（`.env`）

```
ALLOW_REGISTRATION=true          # 公网部署必须设为 false（生产闸门会校验）
REGISTER_DEFAULT_ROLE=viewer     # 只允许 viewer/researcher；写成 admin 也会被降级为 viewer
```

> ⚠️ 变量名**没有** `AQP_` 前缀（`Settings` 未设 `env_prefix`），写成 `AQP_ALLOW_REGISTRATION`
> 会被 `extra="ignore"` **静默丢弃**、开关保持默认值。真值以仓库根 `.env.example`
> 与 `backend/app/core/config.py` 的 `Settings` 字段为准。

安全约束：**注册入口永远发不出管理员**，管理员只能由 `backend/scripts/create_admin.py` 创建。

## 前端

`frontend/src/pages/Login/index.tsx`：登录/注册双 Tab、确认密码、注册成功自动登录；
开关关闭时隐藏入口并提示「自助注册已由管理员关闭」。注册页文案已披露默认角色为只读。

## 错误码补充

`core/errors.py`：`ERR_USER_EXISTS=40105`、`ERR_REGISTER_DISABLED=40106`、`ERR_REGISTER_LIMITED=40107`。
`ERR_CREDENTIALS` 保持为 40104 以兼容既有登录客户端；`ERR_PIPELINE_BUSY` 使用 40900（资源状态冲突），两者语义和处理路径相互独立。新增认证错误码从 40105 起。

## 测试

- `backend/tests/test_auth.py`：新增 6 例（成功+自动登录 / 重名 / 非法用户名 / 弱口令 / 开关关闭 / 角色降级），共 11 passed。
- `backend/tests/test_write_endpoints_smoke.py`：注册表纳入 `POST /auth/register`，写端点计数 51→52。
