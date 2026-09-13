"""P3-1 认证路由：POST /api/v1/auth/login + /register + GET /api/v1/auth/me。

自助注册（/register）说明：
  * 开关由 ``AQP_ALLOW_REGISTRATION`` 控制（默认 true，公网部署必须关闭）；
  * 新用户角色由 ``AQP_REGISTER_DEFAULT_ROLE`` 决定（默认 viewer），
    即使配置写成 admin 也会被降级为 viewer —— 管理员只能由
    ``scripts/create_admin.py`` 创建，杜绝"注册即拿管理员"；
  * 注册成功直接签发 JWT（与登录同构），前端无需再走一次登录。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.auth import ROLE_RESEARCHER, ROLE_VIEWER, require_auth
from ...core.config import get_settings
from ...core.errors import (APIResponse, ERR_CREDENTIALS, ERR_PARAMS,
                            ERR_RATE_LIMITED, ERR_REGISTER_DISABLED,
                            ERR_REGISTER_LIMITED, ERR_USER_EXISTS, AQPException,
                            ok)
from ...db.models_auth import Role, User
from ...db.session import get_db

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


# 注册用户名规则：字母/数字/下划线/点/连字符，3~64 位。
# 收紧字符集是为了避免用户名被当作展示名塞进 HTML（前端渲染）或路径片段。
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
MIN_PASSWORD_LENGTH = 6

# 只允许自助注册拿到这两个角色（admin 必须走脚本）
_REGISTERABLE_ROLES = (ROLE_VIEWER, ROLE_RESEARCHER)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


# ---- 简单登录限速（进程级，内存即可；生产可换 Redis） ----
_login_attempts: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 300


def _check_rate_limit(username: str) -> None:
    import time

    now = time.time()
    # 清理全局过期条目（防内存泄漏：仅保留窗口内的 username）
    expired_users = [u for u, ts in _login_attempts.items()
                     if all(now - t >= _WINDOW_SECONDS for t in ts)]
    for u in expired_users:
        del _login_attempts[u]
    attempts = _login_attempts.get(username, [])
    attempts = [t for t in attempts if now - t < _WINDOW_SECONDS]
    if len(attempts) >= _MAX_ATTEMPTS:
        raise AQPException(ERR_RATE_LIMITED,
                           f"登录尝试过于频繁，请 {_WINDOW_SECONDS // 60} 分钟后重试")
    _login_attempts[username] = attempts


def _record_attempt(username: str, success: bool) -> None:
    import time

    if not success:
        _login_attempts.setdefault(username, []).append(time.time())
    else:
        _login_attempts.pop(username, None)


def _issue_session(username: str, role_name: str) -> dict:
    """签发 JWT 并组装与前端 `LoginResult` 对齐的响应体（登录/注册共用）。"""
    from ...core.auth import create_jwt_token

    token, expires_in = create_jwt_token(username, role_name)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        # 绝对过期时刻（UTC）：前端据此在发请求前就能判断是否需要重新登录，
        # 不必每次都等后端返回 TOKEN_EXPIRED 才知道过期了。
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(seconds=expires_in)).isoformat(timespec="seconds"),
        "user": {"username": username, "role": role_name},
    }


@router.post("/login", response_model=APIResponse[dict])
async def auth_login(req: LoginRequest, db: AsyncSession = Depends(get_db)) -> APIResponse[dict]:
    """用户登录 -> 签发 JWT。"""
    _check_rate_limit(req.username)

    row = (await db.scalars(
        select(User).where(User.username == req.username, User.is_active == True)
    )).first()
    from ...core.auth import verify_password

    # 用户不存在与密码错误返回同一提示，避免暴露"该用户名是否已注册"
    if row is None or not verify_password(req.password, row.password_hash):
        _record_attempt(req.username, False)
        raise AQPException(ERR_CREDENTIALS, "用户名或密码错误")

    _record_attempt(req.username, True)
    role_row = await db.get(Role, row.role_id)
    role_name = role_row.name if role_row else ROLE_VIEWER
    return ok(_issue_session(req.username, role_name))


# ---- 注册限速（同登录：进程级内存；注册按 username 计，防重名探测刷库） ----
_register_attempts: dict[str, list[float]] = {}
_REGISTER_MAX_ATTEMPTS = 5
_REGISTER_WINDOW_SECONDS = 3600


def _check_register_limit(username: str) -> None:
    now = time.time()
    for u, ts in list(_register_attempts.items()):
        if all(now - t >= _REGISTER_WINDOW_SECONDS for t in ts):
            del _register_attempts[u]
    attempts = [t for t in _register_attempts.get(username, [])
                if now - t < _REGISTER_WINDOW_SECONDS]
    if len(attempts) >= _REGISTER_MAX_ATTEMPTS:
        raise AQPException(ERR_REGISTER_LIMITED,
                           f"注册尝试过于频繁，请 {_REGISTER_WINDOW_SECONDS // 3600} 小时后重试")
    _register_attempts[username] = attempts


async def _ensure_role(db: AsyncSession, role_name: str) -> int:
    """取角色 id；角色缺失（全新库）时按需创建，返回其 id。"""
    row = (await db.scalars(select(Role).where(Role.name == role_name))).first()
    if row is not None:
        return int(row.id)
    _ROLE_DESC = {ROLE_VIEWER: "只读", ROLE_RESEARCHER: "研究"}
    db.add(Role(name=role_name, description=_ROLE_DESC.get(role_name)))
    await db.flush()
    row = (await db.scalars(select(Role).where(Role.name == role_name))).first()
    return int(row.id)  # type: ignore[union-attr]


def _default_register_role() -> str:
    """配置的默认角色；非法/越权（如 admin）一律降级 viewer。"""
    configured = (get_settings().REGISTER_DEFAULT_ROLE or ROLE_VIEWER).strip().lower()
    if configured not in _REGISTERABLE_ROLES:
        return ROLE_VIEWER  # 注册入口永远发不出管理员
    return configured


@router.get("/register/status", response_model=APIResponse[dict])
async def auth_register_status() -> APIResponse[dict]:
    """自助注册开关与规则（前端据此决定是否展示「注册」入口）。"""
    s = get_settings()
    enabled = bool(s.ALLOW_REGISTRATION)
    return ok({
        "enabled": enabled,
        "default_role": _default_register_role(),
        "min_password_length": MIN_PASSWORD_LENGTH,
        "username_pattern": _USERNAME_RE.pattern,
    })


@router.post("/register", response_model=APIResponse[dict])
async def auth_register(req: RegisterRequest,
                        db: AsyncSession = Depends(get_db)) -> APIResponse[dict]:
    """自助注册 -> 建号并直接签发 JWT（角色默认 viewer）。"""
    if not get_settings().ALLOW_REGISTRATION:
        raise AQPException(ERR_REGISTER_DISABLED,
                           "注册功能已关闭，请联系管理员创建账号")

    username = req.username.strip()
    password = req.password
    _check_register_limit(username)

    if not _USERNAME_RE.match(username):
        _register_attempts.setdefault(username, []).append(time.time())
        raise AQPException(ERR_PARAMS,
                           "用户名需为 3~64 位字母、数字、下划线、点或连字符")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AQPException(ERR_PARAMS, f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if username.lower() == password.lower():
        raise AQPException(ERR_PARAMS, "密码不能与用户名相同")

    exists = (await db.scalars(select(User.id).where(User.username == username))).first()
    if exists is not None:
        _register_attempts.setdefault(username, []).append(time.time())
        raise AQPException(ERR_USER_EXISTS, "该用户名已被注册")

    from ...core.auth import hash_password

    role_name = _default_register_role()
    role_id = await _ensure_role(db, role_name)
    db.add(User(username=username, password_hash=hash_password(password),
                role_id=role_id, is_active=True))
    try:
        await db.commit()
    except IntegrityError:
        # 并发注册同一用户名：唯一索引兜底（前面的 select 检查存在 TOCTOU 窗口）
        await db.rollback()
        raise AQPException(ERR_USER_EXISTS, "该用户名已被注册")
    _register_attempts.pop(username, None)
    return ok(_issue_session(username, role_name))


@router.get("/me", response_model=APIResponse[dict])
async def auth_me(user: dict = Depends(require_auth)) -> APIResponse[dict]:
    """返回当前用户信息（需要有效 JWT）。"""
    return ok(user)
