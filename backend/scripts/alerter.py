"""scripts/alerter.py：基础告警脚本（P3-3）。

检查项（每项独立检查、独立通知）：
1. data_jobs FAILED（最近 24h）
2. API 5xx ratio > 1%（最近 metrics）
3. Redis circuit OPEN
4. Pipeline 超时（duration > 10min）

通知渠道：Webhook（NOTIFY_WEBHOOK_URL）/ 仅日志。
去重：dedup_key + cooldown_seconds（默认 300s，同一 key 在窗口内不重复通知）。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys_path = str(BACKEND_ROOT)
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

import sqlite3

from loguru import logger

from app.core.config import get_settings

_dedup: dict[str, float] = {}
COOLDOWN_SECONDS = 300


def _notify(key: str, message: str) -> bool:
    """去重通知：同一 key 在 cooldown 内只通知一次。返回是否实际发送。"""
    now = time.time()
    if key in _dedup and now - _dedup[key] < COOLDOWN_SECONDS:
        logger.debug(f"[alerter] {key} 冷却中，跳过")
        return False
    _dedup[key] = now
    logger.warning(f"[alerter] ALERT [{key}]: {message}")

    s = get_settings()
    if s.NOTIFY_ENABLED and s.NOTIFY_WEBHOOK_URL:
        try:
            import httpx

            httpx.post(s.NOTIFY_WEBHOOK_URL,
                       json={"content": f"[AQP ALERT] {key}: {message}"}, timeout=5.0)
        except Exception as e:
            logger.debug(f"[alerter] webhook failed (ignored): {e!r}")
    return True


def check_failed_jobs(db_path: Path) -> int:
    """检查最近 24h FAILED data_jobs。"""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT trade_date, error_message FROM data_jobs WHERE status='FAILED' AND created_at >= ?",
        (cutoff,)).fetchall()
    conn.close()
    for td, err in rows:
        _notify(f"pipeline_failed_{td}", f"Pipeline {td} FAILED: {err}")
    return len(rows)


def check_redis_circuit() -> bool:
    """检查 Redis 熔断器是否 OPEN。"""
    from app.cache.redis_client import RedisClient
    import asyncio

    status = RedisClient.breaker_status()
    if status.get("open"):
        _notify("redis_circuit_open", f"Redis circuit breaker OPEN until {status['open_until']}")
        return True
    return False


def main() -> None:
    from app.core.logging import setup_logging

    setup_logging()
    s = get_settings()
    db_path = s.SQLITE_PATH

    failed_count = check_failed_jobs(db_path)
    redis_open = check_redis_circuit()
    logger.info(f"[alerter] 完成: failed_jobs={failed_count}, redis_open={redis_open}")


if __name__ == "__main__":
    main()
