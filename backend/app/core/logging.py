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
    text_log: Path = settings.LOG_DIR / "app.log"
    logger.add(
        str(text_log),
        level=settings.LOG_LEVEL,
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
    )

    # 3) 结构化 JSON 文件
    json_log: Path = settings.LOG_DIR / "app.json.log"
    logger.add(
        str(json_log),
        level="WARNING",
        serialize=True,
        rotation="20 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
    )

    logger.info(f"logging initialized: level={settings.LOG_LEVEL}, log_dir={settings.LOG_DIR}")
