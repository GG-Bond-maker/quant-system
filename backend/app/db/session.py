"""
SQLAlchemy 2.x 异步会话工厂 + 依赖注入（AQP）。

- 引擎惰性创建（首次使用时初始化，便于测试替换配置）；
- SQLite 异步驱动 aiosqlite：关闭同线程检查、busy_timeout 30s；
- WAL 模式等 PRAGMA 在 init_db.py 中执行（DB 级持久化设置）；
- get_db 作为 FastAPI 依赖：每请求一个 session，异常自动回滚。
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..core.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型继承此类。"""


_engine: AsyncEngine | None = None
_async_session: async_sessionmaker[AsyncSession] | None = None
_engine_loop: asyncio.AbstractEventLoop | None = None


def reset_engine() -> None:
    """显式重置全局引擎/工厂（供多事件循环测试与脚本使用）。"""
    global _engine, _async_session, _engine_loop
    _engine = None
    _async_session = None
    _engine_loop = None


def _get_engine() -> AsyncEngine:
    """惰性创建全局异步引擎（单例；跨事件循环时重建，防 MissingGreenlet）。"""
    global _engine, _engine_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _engine is not None and _engine_loop is not loop:
        # aiosqlite 连接绑定创建时的 loop；loop 变化（测试多次 asyncio.run /
        # 脚本场景）时必须重建，否则触发 greenlet 断言
        _engine = None
        _async_session = None
    if _engine is None:
        _engine_loop = loop
        s = get_settings()
        connect_args: dict[str, object] = {}
        if s.SQLITE_URL.startswith("sqlite+aiosqlite://"):
            # SQLite 并发写优化：
            #   check_same_thread=False -> 允许跨线程复用连接（aiosqlite 必需）
            #   timeout=30              -> busy_timeout 30s：写冲突时等待而非立即报错
            # WAL / synchronous 属于 DB 级持久化 PRAGMA，统一在 init_db.py 中执行
            connect_args = {
                "check_same_thread": False,
                "timeout": 30,
            }
        _engine = create_async_engine(
            s.SQLITE_URL,
            echo=s.DEBUG and s.ENV == "dev",
            future=True,
            connect_args=connect_args,
            pool_pre_ping=True,
            # 注意：aiosqlite 文件库默认 NullPool（每次新建连接），
            # 不接受 pool_size / max_overflow；SQLite 单写者场景下性能足够。
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """惰性创建全局 session 工厂（跟随引擎的 loop 生命周期）。"""
    global _async_session
    _get_engine()  # 触发 loop 守卫（必要时重置工厂）
    if _async_session is None:
        _async_session = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每次请求一个 session，异常回滚、正常提交。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
