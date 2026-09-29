"""
loguru 日志配置（AQP）。

- 控制台：彩色输出；
- 文件：按 10MB 切分、保留 30 天；
- 结构化 JSON 文件（WARNING+）：用于后续接入 ELK / Filebeat。

setup_logging 为幂等调用：重复执行不会重复添加 sink。
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from .config import Settings, get_settings
from .trace import current_trace_id


def setup_logging(settings: Settings | None = None) -> None:
    """初始化 loguru：控制台 + 文本日志 + JSON 结构化日志。"""
    settings = settings or get_settings()

    # 先移除默认 handler，保证幂等
    logger.remove()

    # 每条日志动态注入 trace_id（core/trace.py 的 contextvar；非请求上下文显示 '-'）。
    # patcher 在每条日志渲染前调用，因此同一进程内不同请求的日志各带各的 trace_id。
    logger.configure(patcher=lambda record: record["extra"].update(
        trace_id=current_trace_id() or "-"))

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<dim>{extra[trace_id]}</dim> | "
        "<level>{message}</level>"
    )

    # 1) 控制台
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        format=fmt,
        enqueue=True,
        backtrace=settings.DEBUG,
        diagnose=settings.DEBUG,
    )

    # 2) 文本文件
    #
    # ⚠️ 2026-09-21 审计 P0-1 修复：
    #   * ``delay=True``：把文件打开推迟到**第一条日志**，而不是 add 时就打开。
    #     原实现下若 LOG_DIR 不可写（容器 read_only、uid 无权限、卷没挂），
    #     ``logger.add`` 会直接抛异常；而此时 ``logger.remove()`` 已经执行，
    #     **一个 sink 都不剩** ⇒ 进程带着"零日志"崩溃，问题被彻底掩盖。
    #   * 两个文件 sink 各自 try/except 降级：失败只丢文件日志，控制台照常输出，
    #     并显式告警说明原因，绝不让日志系统把主流程拖死。
    _add_file_sink(
        settings.LOG_DIR / "app.log",
        level=settings.LOG_LEVEL,
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        # ⚠️ 2026-09-30 全检 F-11：**必须显式传 diagnose**。
        # loguru 的默认值是 ``loguru._defaults.LOGURU_DIAGNOSE``，实测为 **True**；
        # 原实现只传了 backtrace 而未传 diagnose ⇒ 文件 sink 的 diagnose 是**开启**的，
        # 异常时会**转储局部变量值**（登录路径上可能含 password / token 明文）。
        # 注意 console sink 用 ``diagnose=settings.DEBUG`` 受控，但**改 DEBUG 修不好文件 sink**
        # —— 这正是"以为 DEBUG=false 就安全"的误判来源。
        diagnose=False,
        # rotation 只限**单文件**大小；30 天 retention 窗口内的累积总量原本无上界
        # （"rotation 会保护磁盘"是错觉）。压缩把窗口内的占用降到约 1/5。
        compression="zip",
    )

    # 3) 结构化 JSON 文件
    _add_file_sink(
        settings.LOG_DIR / "app.json.log",
        level="WARNING",
        serialize=True,
        rotation="20 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        # 同上：两个文件 sink 都必须显式 diagnose=False（本 sink 原先连 backtrace 都没传，
        # 全靠 loguru 默认值 ⇒ 同样落入了"默认 True"的坑）。
        diagnose=False,
        compression="zip",
    )

    logger.info(f"logging initialized: level={settings.LOG_LEVEL}, log_dir={settings.LOG_DIR}")


def _add_file_sink(path: Path, **kwargs: object) -> None:
    """添加文件 sink；失败时降级为"仅控制台"并告警（不抛异常）。"""
    try:
        logger.add(str(path), delay=True, **kwargs)  # type: ignore[call-overload]
    except Exception as e:  # noqa: BLE001 日志降级不得影响主流程
        print(f"[logging] 文件日志 sink 不可用（{path}）：{type(e).__name__}: {e}；"
              f"已降级为仅控制台输出", file=sys.stderr)
