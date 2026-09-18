# SSE 鉴权票据修复报告

**日期：** 2026-09-14  
**范围：** `GET /api/v1/notify/stream` 通知 SSE 鉴权与前端重连流程

## 问题与威胁模型

浏览器原生 `EventSource` 不支持设置 `Authorization` 请求头。原实现虽然要求
`require_role("viewer")`，前端却只能直接访问 `/notify/stream`，因此每次连接均被
拒绝，且浏览器会持续尝试重连。

本次修复防范以下风险：

1. **长期凭据 URL 泄漏：** JWT 或 `ADMIN_TOKEN` 可能出现在浏览器历史、代理日志、监控及
   Referer 中，故两者均禁止拼接到 SSE URL。
2. **ticket 重放：** 获取到短期连接票据后，攻击者或浏览器自动重连不能用它创建第二条流。
3. **横向用户上下文混淆：** ticket 服务端存储 `username` 与 `role`，消费后使用该绑定上下文
   做原有 viewer RBAC 判定，而不是接受调用方传入的身份字段。
4. **Redis 故障时跨存储域重放：** Redis ticket 和进程内票据使用不同前缀，Redis ticket
   在 Redis 不可用时不会退回本地副本消费。

同时，错误日志查询参数脱敏名单已加入 `ticket`，避免票据出现在异常日志中。

## Ticket 设计

- 随机性：`secrets.token_urlsafe(32)`，约 256 位随机熵；不使用 JWT、用户 ID 或可预测序列。
- 生命周期：60 秒；缓存 TTL 与载荷中的 `exp` 双重约束。
- 绑定内容：服务端缓存 JSON `{username, role, iat, exp}`，客户端仅接收不透明 ticket。
- 单次消费：
  - Redis 健康时，键为 `aqp:sse-ticket:r.<random>`，使用 Redis `GETDEL` 原子读取并删除，
    在多 worker 部署中只允许一个请求成功。
  - Redis 不可用时，签发 `m.<random>`，仅保存在现有加锁进程内 LRU，并通过原子 `lru_take`
    读取删除。该降级票据不会在其他 worker 被接受；这是明确的单进程可用性降级，绝不为了
    可用性牺牲一次性语义。
- 失败语义：未知、格式错误、过期、已消费、Redis ticket 消费期间 Redis 不可用，都统一返回
  `UNAUTHORIZED`，经全局错误处理器映射为 HTTP 200 / `code=40100`，不泄露 ticket 状态。

## 端点契约

### `POST /api/v1/notify/stream-ticket`

- **认证：** `Authorization: Bearer <JWT 或兼容 ADMIN_TOKEN>`；需 `viewer` 及以上角色。
- **成功：** 统一信封 `data = {"ticket": "r.|m.<opaque>", "expires_in": 60}`。
- **失败：** 未认证或角色不足沿用现有认证业务错误。

### `GET /api/v1/notify/stream?ticket=<opaque>`

- 仅在 URL 中携带短期一次性 ticket，禁止携带长期 token。
- ticket 先原子消费，再按其绑定的用户上下文验证 `viewer` 权限。
- 成功响应为 `Content-Type: text/event-stream`，立即写入 `: connected` 注释帧，之后维持原有
  15 秒心跳及通知/quotes/alerts 事件协议。
- 对旧客户端保留 `Authorization: Bearer ...` 直连的兼容路径（无 `ticket` 时生效）。

## 前端流程

`useNotifyStore` 在每次建连前使用 Axios 客户端（自动附带 Bearer）调用 ticket 端点，成功后才
构造 `EventSource`。ticket 不写入 Zustand 状态、localStorage 或日志。连接失败时主动关闭
`EventSource` 以阻止浏览器内部复用同一 URL 自动重连；受限指数退避重新执行完整的“换新票 →
建连”流程。`connectionGeneration` 防止关闭期间返回的旧换票请求建立幽灵连接。

项目未配置前端单测框架，因此执行了 TypeScript 检查；该检查当前被
`src/pages/Settings/index.tsx` 第 511、656 行 JSX 语法错误阻断，未能完成全项目类型验证。
该文件不在本次通知改动范围内，已作为集成阻塞项记录。

## 自动化测试

新增 `backend/tests/test_notify_sse_ticket.py`，覆盖：

- ticket 签发必须使用 Bearer；
- 合法 ticket 返回签发用户、首次消费成功且第二次消费失败；
- 过期与错误格式拒绝；
- 两位用户签发的 ticket 保持各自服务端身份上下文；
- `/stream` 对无 ticket 和已消费 ticket 都返回统一 `40100`。

执行结果：

```text
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_notify_sse_ticket.py -q
5 passed, 14 warnings in 18.46s
```

## 真实冒烟证据

使用临时 FastAPI 实例 `127.0.0.1:8121`（验证后已关闭）创建 viewer JWT，完成
Bearer 换票、SSE 首字节读取、重放和缺票验证：

```json
{
  "issue_code": 0,
  "content_type": "text/event-stream",
  "initial_sse_byte": true,
  "replay_code": 40100,
  "missing_code": 40100
}
```

验证过程未输出或记录 JWT/ticket 原文。 
