"""
AQP 认证与授权模块（P3-1）。

- 密码：PBKDF2-HMAC-SHA256（Python 标准库，100k 轮迭代，不存明文）
- JWT：PyJWT HS256，有效期默认 7 天（配置 JWT_SECRET / JWT_EXPIRE_SECONDS）
- 角色：viewer / researcher / admin 三级 RBAC
- 依赖注入：require_auth() / require_role(...) 挂在 FastAPI 路由上
"""
from __future__ import annotations

import hashlib
import hmac
import os
import json
import secrets
import time
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..cache.memory import lru_set, lru_take
from ..cache.redis_client import RedisClient
from .config import get_settings

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


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """要求有效的 Bearer 凭证。未认证抛 40101。

    接受两种凭证：
    1. /auth/login 签发的 JWT（含 username/role）；
    2. ADMIN_TOKEN 本体（Settings 文档约定：后台管理接口的 Bearer Token），
       持有者直接视为 admin —— 前端 localStorage.AQP_ADMIN_TOKEN 走此通道。
    """
    if credentials is None:
        raise HTTPException(status_code=200, detail="UNAUTHORIZED")
    token = credentials.credentials
    # ADMIN_TOKEN 直通（hmac.compare_digest 防时序侧信道）
    settings = get_settings()
    if (settings.ALLOW_ADMIN_TOKEN_LOGIN and token
            and hmac.compare_digest(token, settings.ADMIN_TOKEN)):
        return {"username": "admin", "role": ROLE_ADMIN}
    payload = decode_jwt_token(token)
    return {"username": payload.get("sub", ""), "role": payload.get("role", "")}


def ensure_role(user: dict[str, Any], minimum_role: str) -> dict[str, Any]:
    """验证用户满足最低角色，并返回原用户上下文。"""
    user_rank = ROLE_RANK.get(str(user.get("role", "")), -1)
    required_rank = ROLE_RANK.get(minimum_role, 99)
    if user_rank < required_rank:
        raise HTTPException(status_code=200, detail="FORBIDDEN")
    return user


def require_role(minimum_role: str):
    """构造最低角色依赖；未知角色在应用装载时立即失败。"""
    if minimum_role not in ROLE_RANK:
        raise ValueError(f"未知最低角色: {minimum_role!r}")

    def checker(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        return ensure_role(user, minimum_role)

    return checker
