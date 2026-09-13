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
import secrets
import time
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

# ---------------- 角色权限矩阵 ----------------
ROLE_VIEWER = "viewer"
ROLE_RESEARCHER = "researcher"
ROLE_ADMIN = "admin"

ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_VIEWER: {
        "market:read", "stock:read", "kline:read", "predict:read",
        "screener:read", "backtest:read", "export:read",
    },
    ROLE_RESEARCHER: {
        "market:read", "stock:read", "kline:read", "predict:read",
        "screener:read", "backtest:read", "backtest:write", "export:read",
        "train:write", "infer:write", "data:update", "model:read",
    },
    ROLE_ADMIN: {"*"},  # 全部权限
}

_ROLE_RANK = {ROLE_VIEWER: 0, ROLE_RESEARCHER: 1, ROLE_ADMIN: 2}


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


def require_role(minimum_role: str):
    """要求至少达到指定角色级别。角色权限由 _ROLE_RANK 决定。"""
    def checker(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        user_rank = _ROLE_RANK.get(user["role"], -1)
        required_rank = _ROLE_RANK.get(minimum_role, 99)
        if user_rank < required_rank:
            raise HTTPException(status_code=200, detail="FORBIDDEN")
        return user
    return checker
