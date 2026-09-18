# 文档与当前实现对齐报告

- **日期**：2026-09-14
- **范围**：`README.md`、`run.md`、`.env.example`，以及两份历史长文档的现行参考提示；补充记录当前实现依据。
- **原则**：只根据当前源码和脚本修订；未在文档中承诺不稳定的测试通过数量。

## 1. 已修订内容

| 文档 | 修订项 | 对齐后的说明 |
|---|---|---|
| `README.md` | 登录说明 | 前端登录表单采用用户名、密码，调用 `POST /api/v1/auth/login` 后获得 JWT；明确 `ADMIN_TOKEN` 不是登录表单输入。 |
| `README.md` | 权限说明 | 按当前前端路由与后端 `require_role` 说明未登录、viewer、researcher、admin 的能力；特别标注个股预测端点当前要求 researcher。 |
| `README.md` | 项目规模/结构 | 去除“market / stock 路由”“pytest 68 条”等过时描述，改为实际的多模块路由与测试类别，不写未经本次验证的计数。 |
| `README.md` | 核心端点 | 更新为当前的登录、市场分块、个股、预测及 SSE ticket 端点，并标注关键角色门槛。 |
| `README.md` | 测试命令 | 推荐 `backend/scripts/run_offline_tests.ps1`；完整 pytest 命令作为可能包含环境/网络依赖的可选命令。 |
| `README.md` | SSE 状态 | 从“未实现”清单移除 SSE；记录当前为 JWT 换取 60 秒一次性 ticket 后建立 EventSource 连接。 |
| `run.md` | 本地配置 | 指向 `.env.example`，说明 JWT/注册及 ADMIN_TOKEN 的用途和生产约束。 |
| `run.md` | 登录步骤 | 删除“从 `.env` 复制 ADMIN_TOKEN 粘贴登录”的错误步骤；改为 `create_admin.py` 创建账号，再用用户名/密码登录。 |
| `run.md` | 访问提示 | 明确市场概览公开、个股详情需登录、AI 预测需研究员及以上。 |
| `run.md` | 项目规模/测试 | 页面目录从旧“4 个页面”改为“19 个页面目录（含登录）”；日常测试改为离线脚本与完整 pytest 两档命令。 |
| `run.md` | Redis 示例 | 移除文档中硬编码的本机密码，改为 `<你的REDIS_PASSWORD>` 占位符。 |
| `.env.example` | 配置说明 | 增加 `JWT_SECRET`、`JWT_EXPIRE_SECONDS`、`ALLOW_ADMIN_TOKEN_LOGIN`、`ALLOW_REGISTRATION`、`REGISTER_DEFAULT_ROLE` 的真实配置项及安全说明。 |
| `docs/项目文档.md`、`docs/项目开发文档.md` | 历史文档边界 | 在文首加入现行参考提示，防止早期的 `/api/v1/admin/*`、`/healthz` 等示例被误作当前 API 契约。 |

## 2. 当前实现依据

### 2.1 登录与 JWT

| 结论 | 源码证据 |
|---|---|
| 登录请求只接收 `username`、`password`，成功后返回 `access_token`、`expires_at`、`user`。 | `backend/app/api/v1/auth.py:34-36, 85-99, 102-120` |
| 登录页面只有用户名/密码输入，调用 `authApi.login(username, password)`；注册成功亦复用 JWT 会话。 | `frontend/src/pages/Login/index.tsx:63-93, 139-187` |
| JWT 默认有效期为 604800 秒（7 天）。 | `backend/app/core/auth.py:159-172` |
| ADMIN_TOKEN 仅是兼容 Bearer 分支，且由 `ALLOW_ADMIN_TOKEN_LOGIN` 控制；不是前端登录表单流程。 | `backend/app/core/auth.py:189-208`；`backend/app/core/config.py:40-47` |
| 自助注册由开关控制，默认角色仅可为 viewer/researcher，不能经注册取得 admin。 | `backend/app/api/v1/auth.py:154-159, 175-214`；`backend/app/core/config.py:118-126` |

### 2.2 当前权限与页面入口

| 结论 | 源码证据 |
|---|---|
| 未登录时公开业务页面为 `/` 和 `/market`；其他页面由 `RequireRole` 保护。 | `frontend/src/App.tsx:85-110` |
| viewer 可进入个股、选股、ETF、组合、数据等页面；researcher 用于回测、研究、预警、工作室、数据质量、执行、流水线及归因。 | `frontend/src/App.tsx:90-109` |
| 个股预测端点当前是 `require_role("researcher")`，不能表述为 viewer 预测权限。 | `backend/app/api/v1/stock.py:194-199` |
| 后端角色级别按 viewer < researcher < admin 校验。 | `backend/app/core/auth.py:211-224` |

### 2.3 当前端点、健康检查与 SSE

| 结论 | 源码证据 |
|---|---|
| v1 当前注册 auth、alerts、market、notify、stock、ETF、数据、选股、回测、组合、研究、工作室、运维、执行、导出、监控、日报等路由模块。 | `backend/app/api/v1/router.py:4-44` |
| 健康检查路径为 `/health`，另有 `/health/ready`、`/health/live`；不是 `/healthz`。 | `backend/app/main.py:140-152, 179-227` |
| SSE ticket 由 `POST /api/v1/notify/stream-ticket` 签发，票据有效期 60 秒、一次性消费。 | `backend/app/api/v1/notify.py:39-59`；`backend/app/core/auth.py:47-124` |
| 前端以 Axios Bearer 请求 ticket，再将 ticket（非 JWT/ADMIN_TOKEN）拼入 EventSource URL。 | `frontend/src/api/notify.ts:24-32` |

### 2.4 测试与页面规模

| 结论 | 源码证据 |
|---|---|
| 推荐离线回归脚本使用项目 `.venv` 执行 `pytest -q -m "not network"`。 | `backend/scripts/run_offline_tests.ps1:1-17` |
| 完整 pytest 命令可执行，但本报告不承诺通过数量或耗时。 | `backend/scripts/run_offline_tests.ps1:12`（离线筛选依据）；`README.md` / `run.md` 已仅保留命令 |
| 前端含 19 个页面目录（含 Login）；应用中 MarketOverview 等 18 个业务组件采取懒加载，登录页 eager。 | `frontend/src/pages/` 目录清单；`frontend/src/App.tsx:10-32` |

## 3. 未在本轮直接重写的历史设计文档

`docs/项目文档.md` 与 `docs/项目开发文档.md` 含早期的设计目标、示例 API 和部署片段，其中存在 `/api/v1/admin/*`、`/healthz` 等与当前实现不一致的历史描述。本轮已在两文档文首加入现行参考提示；为避免在未逐章节复核的情况下重写大篇幅毕业设计材料，未把历史章节伪装成现行 API 参考。

使用规则：**运行、认证、测试与当前 API 以 README、run.md、FastAPI `/docs` 和源码路由表为准**；后续若要修订两份长文档，应单独按章节对照 `backend/app/api/v1/router.py` 与 `backend/app/main.py`，不能用全局替换方式处理。

## 4. 验收检查

- README 不再包含“粘贴 ADMIN_TOKEN 登录”“68 条 pytest”“SSE 未实现”的运行性说明。
- run.md 不再包含“登录 Token 在 `.env` 中复制粘贴”“4 个页面”的说明。
- 文档中测试仅给出已核验的脚本/命令，不承诺本轮未执行的结果数量。
- `.env.example` 与 `Settings` 的 JWT、注册、ADMIN_TOKEN 兼容开关字段一致。
