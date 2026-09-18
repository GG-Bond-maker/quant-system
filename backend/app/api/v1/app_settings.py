"""系统设置 API（AQP）：偏好持久化 / 数据源心跳 / 缓存与备份运维。

设计：
1. **配置持久化**：user_settings 单行 JSON（SQLite），启动由 init_db 自动建表；
   读写均为 sqlite3 直连（单行 KV，无并发压力），偏好默认值在代码中集中定义。
2. **连接测试**：真实探测上游——AKShare 走新浪指数切片，东方财富走 push2delay
   轻量快照；返回 {status, latency_ms}，结果持久化到 settings 供页面初始化展示。
3. **数据运维**：增量同步复用数据中心的后台线程任务（datacenter.trigger_sync）；
   缓存清理清 Redis 命名空间 `aqp:*` + 进程内 LRU，返回真实释放字节数；
   备份将 SQLite 落到 backend/backups/ 并返回文件名与大小。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import fastapi
import httpx
from fastapi import Depends
from loguru import logger
from pydantic import BaseModel

from ...cache import memory
from ...cache.redis_client import RedisClient
from ...core.auth import require_role
from ...core.config import PROJECT_ROOT, get_settings
from ...core.errors import APIResponse, ERR_PARAMS, fail, ok

router = fastapi.APIRouter()

# settings 是「读-改-写整行 JSON」，并发写会互相覆盖（实测：页面加载时的
# 自动连接测试晚于用户保存完成，把旧 theme 写了回去）。全部写端点经此锁串行。
_WRITE_LOCK = asyncio.Lock()

DEFAULTS: dict[str, Any] = {
    "preferences": {
        "theme": "light",            # light / dark / auto
        "language": "zh-CN",
        "market": "A",
        "notify_backtest": True,
        "refresh_freq": 3,           # 实时行情刷新频率（秒）
        "nickname": "Quant User",
        "email": "quant_alpha_user@platform.com",
    },
    "engine": {
        "name": "vectorbt",
        "risk_indicators": ["annual", "sortino"],
        "commission_pct": 0.03,
        "slippage_pct": 0.1,
    },
    "api_keys": [],
    "last_tests": {},                # {connector: {status, latency_ms, ts}}
}

# ---------------- 存取 ----------------
def _db_path():
    return get_settings().SQLITE_PATH


def _load_settings() -> dict[str, Any]:
    import sqlite3

    merged = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        with sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT data FROM user_settings WHERE user_id='default'").fetchone()
        if row:
            stored = json.loads(row[0])
            for k, v in stored.items():
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
    except Exception as e:  # noqa: BLE001 表未建/损坏时用默认值
        logger.warning(f"[settings] load fallback: {e!r}")
    return merged


def _save_settings(data: dict[str, Any]) -> None:
    import sqlite3

    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_settings ("
            "user_id VARCHAR(64) PRIMARY KEY, data TEXT NOT NULL, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        conn.execute(
            "INSERT INTO user_settings (user_id, data) VALUES ('default', ?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, "
            "updated_at=CURRENT_TIMESTAMP", (json.dumps(data, ensure_ascii=False),))


# ---------------- 系统状态（缓存降级 + 磁盘水位，L1-1 / §6.1） ----------------
async def _system_status() -> dict[str, Any]:
    """缓存与磁盘健康状态，供设置页展示 + data_health 预警规则（§4.1）铺路。

    - 缓存：Redis ping 失败（含熔断开启）→ degraded=true + WARN 日志；
      REDIS_ENABLED=False 属用户显式关闭，不算降级。
    - 磁盘：DATA_ROOT 所在盘使用率，>90% warning / >95% critical（含 WARN 日志）。
    """
    s = get_settings()
    redis_ok = await RedisClient.ping()
    breaker = RedisClient.breaker_status()
    degraded = bool(s.REDIS_ENABLED and not redis_ok)
    if degraded:
        logger.warning("[settings] Redis 不可用，缓存已降级为进程内 LRU"
                       "（重启即失效，建议 docker start aqp-redis"
                       "，或 docker-compose start redis）")

    usage: float | None = None
    level: str | None = None
    try:
        from .datacenter import _disk_usage_percent

        usage = _disk_usage_percent(s.DATA_ROOT)
    except Exception as e:  # noqa: BLE001 磁盘信息缺失不阻塞配置返回
        logger.debug(f"[settings] disk usage degraded: {e!r}")
    if usage is not None:
        level = "critical" if usage > 95 else "warning" if usage > 90 else "normal"
        if level != "normal":
            logger.warning(f"[settings] 数据盘水位 {usage:.1f}%（{level}），"
                           "接近写满将导致抓取/落库失败，请及时清理")
    return {
        "cache": {
            "enabled": s.REDIS_ENABLED,
            "ok": redis_ok,
            "degraded": degraded,
            "breaker": breaker,
        },
        "disk": {"usage_percent": usage, "level": level},
    }


# ---------------- 端点：读 / 偏好 / 引擎 ----------------
@router.get("")
async def get_settings_all(
    _user: dict = Depends(require_role("viewer"))
) -> APIResponse[dict]:
    """全量配置 + 数据底座状态（存储量 / 上次同步 / 缓存与磁盘健康）。

    鉴权：与「个人偏好」同级别（viewer）。前端仅在登录成功后经 AuthBootstrap
    调用（authApi.me() 通过后 loadFromServer），故收紧为会话内可读不影响登录前流程。
    """
    data = await asyncio.to_thread(_load_settings)

    storage_gb = None
    last_sync = None
    try:
        from .datacenter import overview

        # data 可能为 None（overview 内部降级），用空字典兜底保证状态字段缺失
        # 时只返回 null，而不是把整个 /settings 打成 500
        ov = (await overview()).data or {}
        storage_gb = ov.get("storage_gb")
        last_sync = ov.get("last_sync")
    except Exception as e:  # noqa: BLE001 状态缺失不阻塞配置返回
        logger.debug(f"[settings] overview degraded: {e!r}")
    system = await _system_status()
    return ok({"settings": data, "storage_gb": storage_gb, "last_sync": last_sync,
               "system": system})


class PreferencesIn(BaseModel):
    theme: str | None = None
    language: str | None = None
    market: str | None = None
    notify_backtest: bool | None = None
    refresh_freq: int | None = None
    nickname: str | None = None
    email: str | None = None


@router.put("/preferences")
async def put_preferences(
    req: PreferencesIn, _user: dict = Depends(require_role("viewer"))
) -> APIResponse[dict]:
    """更新个人与平台偏好（主题/语言/市场/通知/刷新频率/资料）。"""
    async with _WRITE_LOCK:
        data = await asyncio.to_thread(_load_settings)
        for field, value in req.model_dump(exclude_none=True).items():
            data["preferences"][field] = value
        await asyncio.to_thread(_save_settings, data)
    return ok(data["preferences"], message="偏好已保存")


class EngineIn(BaseModel):
    name: str | None = None
    risk_indicators: list[str] | None = None
    commission_pct: float | None = None
    slippage_pct: float | None = None


@router.put("/engine")
async def put_engine(
    req: EngineIn, _user: dict = Depends(require_role("admin"))
) -> APIResponse[dict]:
    """更新量化引擎与摩擦力参数（影响后续回测默认口径，仅 admin）。"""
    async with _WRITE_LOCK:
        data = await asyncio.to_thread(_load_settings)
        for field, value in req.model_dump(exclude_none=True).items():
            data["engine"][field] = value
        await asyncio.to_thread(_save_settings, data)
    return ok(data["engine"], message="引擎参数已保存")


# ---------------- 数据源连接测试 ----------------
async def _test_one(connector: str) -> dict:
    t0 = time.perf_counter()
    try:
        if connector == "akshare":
            def _ping():
                from ...data.ingest.akshare_adapter import fetch_index_daily

                fetch_index_daily("sh000001").tail(1)
            await asyncio.wait_for(asyncio.to_thread(_ping), timeout=20)
        elif connector == "eastmoney":
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
                    params={"secids": "1.000001", "fields": "f2,f4",
                            "ut": "b2884a393a59ad64002292a3e90d46a5"},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            r.raise_for_status()
        else:
            return {"status": "error", "latency_ms": None, "message": f"未知数据源 {connector}"}
        return {"status": "success", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "latency_ms": None,
                "message": f"{type(e).__name__}: 上游无响应"}


@router.post("/connectors/test")
async def test_connector(
    payload: dict, _user: dict = Depends(require_role("researcher"))
) -> APIResponse[dict]:
    """真实探测数据源上游并持久化结果。payload: {connector: akshare|eastmoney}"""
    connector = str(payload.get("connector", ""))
    if connector not in ("akshare", "eastmoney"):
        return fail(ERR_PARAMS, f"未知数据源: {connector}")
    result = await _test_one(connector)
    async with _WRITE_LOCK:
        data = await asyncio.to_thread(_load_settings)
        data.setdefault("last_tests", {})[connector] = {
            **result, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        await asyncio.to_thread(_save_settings, data)
    return ok({"connector": connector, **result})


# ---------------- API Keys ----------------
@router.post("/apikeys/rotate")
async def rotate_api_key(
    _user: dict = Depends(require_role("admin"))
) -> APIResponse[dict]:
    """明确禁用未接入认证链路的 API Key 轮换入口。

    历史实现只把掩码写入用户偏好，任何认证依赖均不会校验生成的明文；继续
    返回“成功”会误导用户以为密钥可用于鉴权。因此保留兼容路由但拒绝请求。
    """
    return fail(ERR_PARAMS, "API Key 功能未启用：平台当前不验证此类密钥，不能生成可用凭证")


# ---------------- 数据运维 ----------------
@router.post("/data/sync")
async def sync_daily(
    _user: dict = Depends(require_role("researcher"))
) -> APIResponse[dict]:
    """增量同步当日日线：复用数据中心后台任务（前端轮询 /datacenter/sync/status）。"""
    from .datacenter import SyncRequest, trigger_sync

    return await trigger_sync(SyncRequest(mode="incremental"))


@router.post("/data/cache/clear")
async def clear_cache(
    _user: dict = Depends(require_role("admin"))
) -> APIResponse[dict]:
    """清空 Redis `aqp:*` 缓存与进程内 LRU，返回真实释放空间。"""
    freed = 0
    redis_ok = False
    r = RedisClient._ensure()
    if r is not None:
        try:
            info = await r.info("memory")
            before = int(info.get("used_memory", 0))
            keys = [k async for k in r.scan_iter(match=f"aqp:*", count=500)]
            if keys:
                await r.delete(*keys)
            info_after = await r.info("memory")
            freed = max(0, before - int(info_after.get("used_memory", 0)))
            redis_ok = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[settings] redis clear degraded: {e!r}")
    memory.lru_clear()
    freed_mb = round(freed / 1024 / 1024, 1)
    return ok({
        "redis_cleared": redis_ok,
        "freed_mb": freed_mb,
        "message": (f"已清理 {freed_mb} MB Redis 缓存与进程内缓存" if redis_ok
                    else "Redis 离线，已清理进程内缓存"),
    })


@router.post("/db/backup")
async def backup_db(
    _user: dict = Depends(require_role("admin"))
) -> APIResponse[dict]:
    """备份 SQLite 数据库到 backend/backups/。"""
    s = get_settings()

    def _do() -> dict:
        import sqlite3

        backup_dir = PROJECT_ROOT / "backend" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        name = f"aqp_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        target = backup_dir / name
        src = sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return {"file": name, "size_mb": round(target.stat().st_size / 1024 / 1024, 2)}

    result = await asyncio.to_thread(_do)
    return ok(result, message=f"备份完成：{result['file']}")
