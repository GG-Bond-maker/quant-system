"""
AQP 认证与授权模块（P3-1）。

- 密码：PBKDF2-HMAC-SHA256（Python 标准库，100k 轮迭代，不存明文）
- JWT：PyJWT HS256，有效期默认 7 天（配置 JWT_SECRET / JWT_EXPIRE_SECONDS）
- 角色：viewer / researcher / admin 三级 RBAC（可由 Settings.RBAC_ENFORCE 一键放开，
  默认放开：登录即可用全部功能，仅保留登录这道门）
- 依赖注入：require_auth() / require_role(...) 挂在 FastAPI 路由上
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import json
import secrets
import time
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from ..cache.memory import lru_set, lru_take
from ..cache.redis_client import RedisClient
from .config import get_settings, parse_trusted_proxies

# ---------------- 角色层级（后端授权的单一事实源） ----------------
ROLE_VIEWER = "viewer"
ROLE_RESEARCHER = "researcher"
ROLE_ADMIN = "admin"

# 路由统一声明最低角色，由 require_role / ensure_role 基于此层级判断。
# 不再维护与路由脱节的资源权限矩阵，避免出现“矩阵允许 predict，但路由拒绝”的
# 双重口径。具体端点权限以 require_role(...) 声明为准。
ROLE_RANK: dict[str, int] = {
    ROLE_VIEWER: 0,
    ROLE_RESEARCHER: 1,
    ROLE_ADMIN: 2,
}

# SSE ticket 是短期、一次性的 EventSource 授权委托，不是 JWT 的替代品。
# r. 前缀只可在 Redis 中消费；m. 前缀仅在 Redis 不可用时在单进程 LRU 中消费。
# 以存储域编码避免 Redis 故障期间错误回退到本地副本，导致一次性语义被破坏。
SSE_TICKET_TTL_SECONDS = 60
_SSE_TICKET_CACHE_PREFIX = "aqp:sse-ticket:"
_SSE_TICKET_REDIS_PREFIX = "r."
_SSE_TICKET_MEMORY_PREFIX = "m."


def _sse_ticket_key(ticket: str) -> str:
    """将不透明 ticket 映射为缓存键；ticket 本身绝不写入日志。"""
    return _SSE_TICKET_CACHE_PREFIX + ticket


async def issue_sse_ticket(user: dict[str, Any]) -> dict[str, Any]:
    """签发绑定当前用户的 60 秒、一次性 SSE ticket。

    Redis 健康时 ticket 仅存 Redis，消费使用原子 GETDEL；Redis 不可用时签发
    ``m.`` 前缀 ticket 并仅存本进程 LRU。这一降级模式不会跨 worker 接受票据，
    以可用性换取单进程内严格一次性，而不会放宽为跨进程可重放。
    """
    username = str(user.get("username", ""))
    role = str(user.get("role", ""))
    if not username or role not in ROLE_RANK:
        raise HTTPException(status_code=200, detail="UNAUTHORIZED")

    now = int(time.time())
    payload = json.dumps({
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + SSE_TICKET_TTL_SECONDS,
    }, separators=(",", ":")).encode("utf-8")
    random_value = secrets.token_urlsafe(32)

    redis_ticket = _SSE_TICKET_REDIS_PREFIX + random_value
    if await RedisClient.set_if_available(
            _sse_ticket_key(redis_ticket), payload, ex=SSE_TICKET_TTL_SECONDS):
        return {"ticket": redis_ticket, "expires_in": SSE_TICKET_TTL_SECONDS}

    memory_ticket = _SSE_TICKET_MEMORY_PREFIX + random_value
    lru_set(_sse_ticket_key(memory_ticket), payload, ttl=SSE_TICKET_TTL_SECONDS)
    return {"ticket": memory_ticket, "expires_in": SSE_TICKET_TTL_SECONDS}


async def consume_sse_ticket(ticket: str) -> dict[str, Any] | None:
    """原子消费 SSE ticket，返回其绑定用户；失败统一返回 ``None``。

    调用方不得根据失败原因区别响应，避免泄露 ticket 是否曾存在、是否已过期或
    是否已经消费。Ticket 只承担一次性建连，不应在重连时复用。
    """
    if not ticket or len(ticket) > 256:
        return None

    key = _sse_ticket_key(ticket)
    raw: bytes | None
    if ticket.startswith(_SSE_TICKET_REDIS_PREFIX):
        raw, redis_available = await RedisClient.getdel_if_available(key)
        if not redis_available:
            return None
    elif ticket.startswith(_SSE_TICKET_MEMORY_PREFIX):
        value = lru_take(key)
        raw = value if isinstance(value, bytes) else None
    else:
        return None

    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
        username = str(payload["username"])
        role = str(payload["role"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not username or role not in ROLE_RANK or expires_at < int(time.time()):
        return None
    return {"username": username, "role": role}


# ---------------- 密码安全（PBKDF2-HMAC-SHA256，不存明文） ----------------
def hash_password(password: str) -> str:
    """返回 "pbkdf2_sha256$iterations$salt_hex$hash_hex" 格式字符串。"""
    iterations = 100_000
    salt = secrets.token_hex(16)
    pwd_bytes = password.encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """安全校验密码（hmac.compare_digest 防时序攻击）。"""
    try:
        _, iterations_s, salt, hash_hex = hashed.split("$", 3)
        iterations = int(iterations_s)
        pwd_bytes = password.encode("utf-8")
        dk = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt.encode("utf-8"), iterations)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------------- JWT ----------------
def _jwt_secret() -> str:
    """JWT 密钥：优先环境变量 JWT_SECRET，否则 ADMIN_TOKEN 派生（生产必须配置 JWT_SECRET）。"""
    s = get_settings()
    secret = os.environ.get("JWT_SECRET") or getattr(s, "JWT_SECRET", None)
    if secret:
        return str(secret)
    return "aqp-derive:" + s.ADMIN_TOKEN  # 开发模式从 ADMIN_TOKEN 派生


def _jwt_expire_seconds() -> int:
    return int(os.environ.get("JWT_EXPIRE_SECONDS", "604800"))  # 默认 7 天


def create_jwt_token(username: str, role: str) -> tuple[str, int]:
    """签发 JWT。返回 (token, expires_in_seconds)。"""
    s = get_settings()
    expires_in = _jwt_expire_seconds()
    payload: dict[str, Any] = {
        "sub": username, "role": role, "iat": int(time.time()),
        "exp": int(time.time()) + expires_in, "iss": s.APP_NAME,
    }
    token = pyjwt.encode(payload, _jwt_secret(), algorithm="HS256")
    return token, expires_in


def decode_jwt_token(token: str) -> dict[str, Any]:
    """解码并校验 JWT（过期 / 签名错误均抛 HTTPException 40101）。"""
    try:
        return pyjwt.decode(token, _jwt_secret(), algorithms=["HS256"], issuer=get_settings().APP_NAME)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=200, detail="TOKEN_EXPIRED")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=200, detail="INVALID_TOKEN")


# ---------------- FastAPI 依赖 ----------------
_bearer_scheme = HTTPBearer(auto_error=False)


def _as_ip(value: str | None) -> str | None:
    """把请求头里的值规整为合法 IP 字面量；非法（垃圾 / 非 IP / 带换行）返回 ``None``。

    为什么必须校验而不是原样返回：这些值会进**审计日志**。头里塞
    ``"1.2.3.4\\n[伪造行]"`` 就能在日志里**伪造整行**（日志注入），塞超长串还能放大
    日志体积。校验成 IP 字面量后，可注入的字符集被压到 ``0-9a-f:.``，注入面消失。
    """
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _peer_is_trusted_proxy(peer: str) -> bool:
    """直连对端是否落在 ``TRUSTED_PROXY_IPS`` 声明的网段内。

    每次读配置（不缓存）：便于测试 monkeypatch，也让热改配置立即生效 —— 该函数在
    认证热路径上，但配置项通常为空或个位数，解析成本可忽略。
    """
    networks = parse_trusted_proxies(get_settings().TRUSTED_PROXY_IPS)[0]
    if not networks:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        # 对端不是合法 IP（如 TestClient 的 "testclient"）⇒ 不可能是受信代理
        return False
    return any(addr in net for net in networks)


def client_ip_info(request: Request | None) -> tuple[str, bool]:
    """返回 ``(来源 IP, 该 IP 是否可信可归因)``。

    ⚠️ 为什么需要第二个返回值（2026-10-04 上线前全检残留项）：
    ``X-Forwarded-For`` / ``X-Real-IP`` 都是**客户端可写**的请求头。旧实现无条件取
    XFF **首跳**当来源 IP，而 nginx 用的是 ``$proxy_add_x_forwarded_for``（语义 =
    ``"$http_x_forwarded_for, $remote_addr"``，**追加**）⇒ 首跳恰好就是**客户端自己
    塞进去的值**。两个后果：
      * 审计日志（OWASP A09 留痕）可被**投毒**成任意 IP；
      * 若拿它做限速键，**一个请求头即可绕过**（"已防护"的错觉比不做更糟）。

    现在的规则：
      * 直连对端是**已声明的受信代理** ⇒ 采信 ``X-Real-IP``（nginx 用 ``$remote_addr``
        **覆写**，客户端改不了），退回 XFF **尾跳**（追加语义下尾跳才是真实对端），
        两者都取不到时只拿得到代理地址 ⇒ ``attributable=False``；
      * 直连对端不是受信代理 ⇒ 代理头**一律不采信**，只用对端地址。

    ``attributable`` 的语义：**该 IP 是否归因到具体客户端**（不是伪造值、也不是
    代理/网关自身）。``False`` 时调用方**不得**把它用作限速键 —— 否则会把全部用户
    并进同一个桶（自伤）。是否真的启用 IP 限速是另一个开关（``LOGIN_IP_RATE_LIMIT``）。
    """
    if request is None:
        return "-", False
    peer = request.client.host if request.client else ""
    if not peer:
        return "-", False

    if not _peer_is_trusted_proxy(peer):
        # 对端不是受信代理：代理头全部不可信（客户端可伪造），只信 socket 对端。
        return peer, True

    real = _as_ip(request.headers.get("x-real-ip"))
    if real:
        return real, True
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = _as_ip(forwarded.split(",")[-1])
        if last:
            return last, True
    # 受信代理却没带可解析的头 ⇒ 只剩代理自己的地址，归因不到客户端
    return peer, False


def client_ip(request: Request | None) -> str:
    """提取来源 IP（**仅用于日志展示**；安全判定请用 :func:`client_ip_info`）。

    2026-10-01（OWASP A09 认证审计）新增：项目此前没有任何来源 IP 提取方式，
    此处简单封装供认证审计日志统一使用（api/v1/auth.py 同源复用）。
    走 nginx 反代时取 ``X-Real-IP``（或 XFF 尾跳）；直连时只用对端地址。
    """
    return client_ip_info(request)[0]


def _audit_path(request: Request | None) -> str:
    """审计日志用请求路径；无请求上下文（如 notify 直调）显示占位符。"""
    return request.url.path if request is not None else "-"


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    # 🔴 注解必须是裸 `Request`，**不能**写成 `Request | None`：
    # FastAPI 靠 `issubclass(annotation, Request)` 识别"注入请求对象"，Union 会让它
    # 认不出来，转而当普通参数处理 → 装载期直接抛
    # `FastAPIError: Invalid args for response field! starlette.requests.Request | None`
    # （实测：改成 `Request | None` 后 59 个测试文件在**收集期**就全挂）。
    # 而默认值 `None` 又必须保留 —— notify.py:67 与 main.py:427 会 `require_auth(credentials)`
    # 直调（无请求上下文）。两者冲突 ⇒ 用 ignore 压制 mypy 的 implicit-Optional 告警。
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """要求有效的 Bearer 凭证。未认证抛 40101。

    接受两种凭证：
    1. /auth/login 签发的 JWT（含 username/role）；
    2. ADMIN_TOKEN 本体（Settings 文档约定：后台管理接口的 Bearer Token），
       持有者直接视为 admin —— 前端 localStorage.AQP_ADMIN_TOKEN 走此通道。

    审计留痕（OWASP A09，2026-10-01）：未携带凭证 / token 校验失败 /
    ADMIN_TOKEN 直通各记一条 WARNING（登录成功/失败在 api/v1/auth.py）。
    ``request`` 由 FastAPI 自动注入（默认 None 兼容 notify.py 等直调路径）。
    **任何日志都不含 token / 密码本体**。
    """
    ip, path = client_ip(request), _audit_path(request)
    if credentials is None:
        logger.warning(f"[auth] 未携带凭证 ip={ip} path={path}")
        raise HTTPException(status_code=200, detail="UNAUTHORIZED")
    token = credentials.credentials
    # ADMIN_TOKEN 直通（hmac.compare_digest 防时序侧信道）
    settings = get_settings()
    if (settings.ALLOW_ADMIN_TOKEN_LOGIN and token
            and hmac.compare_digest(token, settings.ADMIN_TOKEN)):
        logger.warning(f"[auth] ADMIN_TOKEN 直通 ip={ip} path={path}")
        return {"username": "admin", "role": ROLE_ADMIN}
    try:
        payload = decode_jwt_token(token)
    except HTTPException as e:
        logger.warning(f"[auth] token 校验失败 detail={e.detail} ip={ip} path={path}")
        raise
    return {"username": payload.get("sub", ""), "role": payload.get("role", "")}


def rbac_enforced() -> bool:
    """是否启用角色最低等级（RBAC）校验。

    单一开关（``Settings.RBAC_ENFORCE``，默认 False = 全面放开）。这是"全面放开 /
    回滚"的**唯一判定点**：``ensure_role`` 是全部 ``require_role(...)`` 最终汇入的
    函数，故 60+ 处调用点无需逐个改动。

    - ``False``（默认，2026-09-23 用户裁决）：只要已通过 ``require_auth``（即已登录）
      即视为满足任何最低角色 —— 保留"登录"这道门，去掉"角色"这道门；
    - ``True``：回滚到 ``viewer < researcher < admin`` 的分级拦截。

    单独抽成函数而非内联，是为了让测试可以显式切换两种语义（证明"可回滚"）。
    """
    return bool(get_settings().RBAC_ENFORCE)


def ensure_role(user: dict[str, Any], minimum_role: str,
                request: Request | None = None) -> dict[str, Any]:
    """验证用户满足最低角色，并返回原用户上下文。

    当 :func:`rbac_enforced` 为 False（默认，全面放开）时直接放行——调用方已经过
    ``require_auth``（用户必为已登录账号），故此处不再比较角色等级。置
    ``Settings.RBAC_ENFORCE=True`` 即恢复分级校验（角色不足抛 40300）。

    审计留痕（OWASP A09，2026-10-01）：RBAC 拒绝记 WARNING（含用户、角色、
    所需角色、来源 IP 与路径）。
    """
    if not rbac_enforced():
        return user
    user_rank = ROLE_RANK.get(str(user.get("role", "")), -1)
    required_rank = ROLE_RANK.get(minimum_role, 99)
    if user_rank < required_rank:
        logger.warning(
            f"[auth] RBAC 拒绝 user={user.get('username', '-') or '-'} "
            f"role={user.get('role', '-') or '-'} required={minimum_role} "
            f"ip={client_ip(request)} path={_audit_path(request)}")
        raise HTTPException(status_code=200, detail="FORBIDDEN")
    return user


def require_role(minimum_role: str):
    """构造最低角色依赖；未知角色在应用装载时立即失败。"""
    if minimum_role not in ROLE_RANK:
        raise ValueError(f"未知最低角色: {minimum_role!r}")

    def checker(user: dict[str, Any] = Depends(require_auth),
                # 同 require_auth：注解必须裸 `Request`（FastAPI 靠 issubclass 识别），
                # 默认 None 供直调；Union 写法会让应用装载期直接崩。
                request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
        return ensure_role(user, minimum_role, request)

    return checker
