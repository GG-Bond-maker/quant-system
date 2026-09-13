"""scripts/create_admin.py：交互式创建管理员（或从环境变量读取）。"""
from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.auth import ROLE_ADMIN, ROLE_RESEARCHER, ROLE_VIEWER, hash_password  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
from app.db.models_auth import Role, User  # noqa: E402
from app.db.session import get_session_factory, reset_engine  # noqa: E402

_ROLES = [(ROLE_VIEWER, "只读"), (ROLE_RESEARCHER, "研究"), (ROLE_ADMIN, "管理员")]


async def _ensure_roles() -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as sess:
        for name, desc in _ROLES:
            row = (await sess.scalars(select(Role).where(Role.name == name))).first()
            if row is None:
                sess.add(Role(name=name, description=desc))
        await sess.commit()
        rows = {r.name: r.id for r in (await sess.scalars(select(Role))).all()}
    return rows


async def _create_user(username: str, password: str, role_name: str) -> str:
    role_ids = await _ensure_roles()
    factory = get_session_factory()
    async with factory() as sess:
        existing = (await sess.scalars(select(User).where(User.username == username))).first()
        if existing is not None:
            return f"用户 {username} 已存在（跳过）"
        sess.add(User(username=username, password_hash=hash_password(password),
                      role_id=role_ids[role_name], is_active=True))
        await sess.commit()
    return f"✅ 管理员 {username} 创建成功（角色 {role_name}）"


def main() -> None:
    setup_logging()
    reset_engine()
    asyncio.run(init_database())

    if len(sys.argv) >= 3:
        username, password = sys.argv[1], sys.argv[2]
    else:
        username = input("用户名: ").strip()
        password = getpass.getpass("密码: ")
        confirm = getpass.getpass("确认密码: ")
        if password != confirm:
            print("❌ 两次密码不一致")
            sys.exit(1)
    if len(password) < 6:
        print("❌ 密码至少 6 位")
        sys.exit(1)

    result = asyncio.run(_create_user(username, password, ROLE_ADMIN))
    print(result)


if __name__ == "__main__":
    main()
