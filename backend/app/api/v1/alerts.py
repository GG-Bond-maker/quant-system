"""智能盯盘与预警中心（§4.1，Sprint2 MVP）。

六类规则（rule_type + params_json 白名单）：
    price_pct       单日涨跌幅超 ±threshold%（依赖批量行情快照）
    price_cross     上穿/下穿价格阈值 direction=up/down & price（同上）
    volume_spike    现量 > window 日均量 × k 倍（快照 + daily_bar，单位均为手）
    score_topk      进入/跌出模型 top-K（predictions，日级）
    factor_quantile 因子值突破 window 日分位（features 最新日 + 历史）
    data_health     磁盘水位 metric=disk & threshold / 流水线失败 pipeline / 源降级 source

评估调度：lifespan 后台任务（alert_scheduler）——盘中每 30s，盘后每小时；
冷却期内同规则去重（cooldown_minutes）；渠道：sse（quotes_hub.alerts 频道）
+ webhook（NOTIFY_WEBHOOK_URL）。红线：不造数——行情缺失的规则本轮跳过。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...core.auth import require_role
from ...core.config import get_settings
from ...core.errors import APIResponse, ERR_PARAMS, AQPException, fail, ok
from ...data.features import read_feature_frame
from ...data.quotes_hub import publish_alert, quotes_snapshot
from ...db.models import AlertEvent, AlertRule, Watchlist
from ...db.session import get_session_factory
from .datacenter import _disk_usage_percent

router = APIRouter()

RULE_TYPES = {"price_pct", "price_cross", "volume_spike",
              "score_topk", "factor_quantile", "data_health"}
CHANNELS = {"sse", "webhook"}

# params_json 字段白名单（值域校验在 _validate_params）
_PARAM_KEYS = {
    "price_pct": {"threshold"},
    "price_cross": {"price", "direction"},
    "volume_spike": {"window", "k"},
    "score_topk": {"k"},
    "factor_quantile": {"factor", "window", "quantile"},
    "data_health": {"metric", "threshold"},
}


def _validate_params(rule_type: str, params: dict[str, Any]) -> None:
    """params 白名单 + 值域校验（违规抛 AQPException 40000）。"""
    unknown = set(params) - _PARAM_KEYS.get(rule_type, set())
    if unknown:
        raise AQPException(ERR_PARAMS,
                           f"{rule_type} 不支持参数: {sorted(unknown)}（允许: {sorted(_PARAM_KEYS[rule_type])}）")
    if rule_type == "price_pct" and not (0 < float(params.get("threshold", 0)) <= 30):
        raise AQPException(ERR_PARAMS, "price_pct.threshold 需在 (0, 30] 内（百分比）")
    if rule_type == "price_cross":
        if float(params.get("price", 0)) <= 0:
            raise AQPException(ERR_PARAMS, "price_cross.price 必须 > 0")
        if params.get("direction") not in ("up", "down"):
            raise AQPException(ERR_PARAMS, "price_cross.direction 仅支持 up/down")
    if rule_type == "volume_spike":
        if not (2 <= int(params.get("window", 20)) <= 120):
            raise AQPException(ERR_PARAMS, "volume_spike.window 需在 [2, 120] 内")
        if not (1.2 <= float(params.get("k", 3.0)) <= 20):
            raise AQPException(ERR_PARAMS, "volume_spike.k 需在 [1.2, 20] 内")
    if rule_type == "score_topk" and not (1 <= int(params.get("k", 50)) <= 500):
        raise AQPException(ERR_PARAMS, "score_topk.k 需在 [1, 500] 内")
    if rule_type == "factor_quantile":
        if not params.get("factor"):
            raise AQPException(ERR_PARAMS, "factor_quantile.factor 必填（features 列名）")
        if not (0.5 <= float(params.get("quantile", 0.95)) < 1.0):
            raise AQPException(ERR_PARAMS, "factor_quantile.quantile 需在 [0.5, 1.0) 内")
    if rule_type == "data_health":
        if params.get("metric") not in ("disk", "pipeline", "source"):
            raise AQPException(ERR_PARAMS, "data_health.metric 仅支持 disk/pipeline/source")


# ---------------- 请求/响应模型 ----------------
class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    rule_type: str
    scope: str = Field("symbol", pattern="^(symbol|watchlist|global)$")
    symbol: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=lambda: ["sse"])
    cooldown_minutes: int = Field(30, ge=0, le=1440)
    enabled: bool = True


def _rule_out(r: AlertRule) -> dict:
    return {
        "id": r.id, "name": r.name, "rule_type": r.rule_type, "scope": r.scope,
        "symbol": r.symbol, "params": json.loads(r.params_json or "{}"),
        "channels": json.loads(r.channels_json or '["sse"]'),
        "cooldown_minutes": r.cooldown_minutes, "enabled": bool(r.enabled),
        "created_at": str(r.created_at or ""),
    }


# ---------------- 预警数据源健康 ----------------
@router.get("/health", response_model=APIResponse[dict])
async def alerts_health(
        _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """预警数据源健康状态（当前仅 score_topk 的 predictions + universe 快照）。

    用途：score_topk 在股票池快照缺失时会跳过本轮判定，若没有本端点，该失效
    对外完全不可见（用户以为预警在正常值守）。运维可据此配 data_health 告警。
    """
    return ok(dict(_TOPK_HEALTH))


# ---------------- 规则 CRUD ----------------
@router.get("/rules", response_model=APIResponse[list])
async def list_rules(
        _user: dict = Depends(require_role("researcher"))) -> APIResponse[list]:
    """全部预警规则（含停用，供规则管理页展示）。"""

    async def _q() -> list[dict]:
        factory = get_session_factory()
        async with factory() as sess:
            rows = (await sess.execute(
                select(AlertRule).order_by(AlertRule.id.desc()))).scalars().all()
            return [_rule_out(r) for r in rows]

    return ok(await _q())


@router.post("/rules", response_model=APIResponse[dict])
async def create_rule(req: RuleIn, _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """新建预警规则（params/channels 白名单校验；symbol scope 必填 symbol）。"""
    if req.rule_type not in RULE_TYPES:
        return fail(ERR_PARAMS, f"未知规则类型: {req.rule_type}（支持: {sorted(RULE_TYPES)}）")
    bad_channels = set(req.channels) - CHANNELS
    if bad_channels:
        return fail(ERR_PARAMS, f"未知渠道: {sorted(bad_channels)}（支持: {sorted(CHANNELS)}）")
    if req.scope == "symbol" and not req.symbol:
        return fail(ERR_PARAMS, "scope=symbol 时 symbol 必填")
    _validate_params(req.rule_type, req.params)

    async def _q() -> dict:
        factory = get_session_factory()
        async with factory() as sess:
            rule = AlertRule(
                name=req.name, rule_type=req.rule_type, scope=req.scope,
                symbol=req.symbol.upper() if req.symbol else None,
                params_json=json.dumps(req.params, ensure_ascii=False),
                channels_json=json.dumps(req.channels, ensure_ascii=False),
                cooldown_minutes=req.cooldown_minutes, enabled=req.enabled)
            sess.add(rule)
            await sess.commit()
            await sess.refresh(rule)
            return _rule_out(rule)

    return ok(await _q(), message="规则已创建")


@router.put("/rules/{rule_id}", response_model=APIResponse[dict])
async def update_rule(rule_id: int, req: RuleIn,
                      _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """整体更新规则（PUT 语义：以请求体为准）。"""
    if req.rule_type not in RULE_TYPES:
        return fail(ERR_PARAMS, f"未知规则类型: {req.rule_type}")
    _validate_params(req.rule_type, req.params)

    async def _q() -> dict | None:
        factory = get_session_factory()
        async with factory() as sess:
            rule = (await sess.execute(
                select(AlertRule).where(AlertRule.id == rule_id))).scalar_one_or_none()
            if rule is None:
                return None
            rule.name = req.name
            rule.rule_type = req.rule_type
            rule.scope = req.scope
            rule.symbol = req.symbol.upper() if req.symbol else None
            rule.params_json = json.dumps(req.params, ensure_ascii=False)
            rule.channels_json = json.dumps(req.channels, ensure_ascii=False)
            rule.cooldown_minutes = req.cooldown_minutes
            rule.enabled = req.enabled
            await sess.commit()
            await sess.refresh(rule)
            return _rule_out(rule)

    out = await _q()
    if out is None:
        return fail(40400, f"规则不存在: {rule_id}")
    return ok(out, message="规则已更新")


@router.delete("/rules/{rule_id}", response_model=APIResponse[dict])
async def delete_rule(rule_id: int,
                      _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """删除规则（历史事件保留，rule_id 悬挂显示为已删除规则）。"""

    async def _q() -> bool:
        factory = get_session_factory()
        async with factory() as sess:
            rule = (await sess.execute(
                select(AlertRule).where(AlertRule.id == rule_id))).scalar_one_or_none()
            if rule is None:
                return False
            await sess.delete(rule)
            await sess.commit()
            return True

    if not await _q():
        return fail(40400, f"规则不存在: {rule_id}")
    return ok({"id": rule_id}, message="规则已删除")


# ---------------- 事件查询/已读 ----------------
@router.get("/events", response_model=APIResponse[list])
async def list_events(
    unread: int = Query(0, ge=0, le=1, description="1=仅未读"),
    limit: int = Query(50, ge=1, le=200),
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    """触发历史（倒序；unread=1 过滤未读）。"""

    async def _q() -> list[dict]:
        factory = get_session_factory()
        async with factory() as sess:
            stmt = select(AlertEvent).order_by(AlertEvent.id.desc()).limit(limit)
            if unread:
                stmt = stmt.where(AlertEvent.is_read == False)  # noqa: E712
            rows = (await sess.execute(stmt)).scalars().all()
            return [{
                "id": e.id, "rule_id": e.rule_id, "symbol": e.symbol,
                "triggered_at": str(e.triggered_at or ""),
                "payload": json.loads(e.payload_json or "{}"),
                "is_read": bool(e.is_read),
            } for e in rows]

    return ok(await _q())


@router.post("/events/read", response_model=APIResponse[dict])
async def mark_read(payload: dict,
                    _user: dict = Depends(require_role("viewer"))) -> APIResponse[dict]:
    """批量已读：payload {ids: [..]} 或 {all: true}。"""
    ids = payload.get("ids") or []
    mark_all = bool(payload.get("all"))
    if not mark_all and not ids:
        return fail(ERR_PARAMS, "需提供 ids 或 all=true")

    async def _q() -> int:
        factory = get_session_factory()
        async with factory() as sess:
            stmt = select(AlertEvent).where(AlertEvent.is_read == False)  # noqa: E712
            if not mark_all:
                stmt = stmt.where(AlertEvent.id.in_(int(i) for i in ids))
            rows = (await sess.execute(stmt)).scalars().all()
            for e in rows:
                e.is_read = True
            await sess.commit()
            return len(rows)

    n = await _q()
    return ok({"marked": n}, message=f"已标记 {n} 条")


# ---------------- 评估引擎 ----------------
async def _rule_symbols(rule: AlertRule) -> list[str]:
    """规则作用标的：symbol=自身；watchlist=自选表全部；global=仅 data_health。"""
    if rule.scope == "symbol" and rule.symbol:
        return [rule.symbol]
    if rule.scope == "watchlist":
        factory = get_session_factory()
        async with factory() as sess:
            rows = (await sess.execute(select(Watchlist.symbol))).all()
        return [r[0] for r in rows]
    return []


# score_topk 数据源健康状态：供 GET /alerts/health 与运维观测消费。
# 背景：require_universe=True 在股票池快照缺失时抛 AQPException，此前被 except 吞掉
# 直接 return 空集，导致预警规则"既不触发、也不报错、也不告警"地静默失效。
# 修复思路是**留痕**而非**放宽**：不通过 require_universe=False 降级校验（那会产出
# 未经 ST/停牌校验的榜单，等于拿未校验数据冒充已校验结果），而是把降级状态落到
# _TOPK_HEALTH 并限频打 warning，让失效变得可观测。
_TOPK_HEALTH: dict[str, Any] = {
    "degraded": False,
    "reason": None,
    "detail": None,
    "snapshot_date": None,
    "checked_at": None,
    "last_ok_at": None,
}
_LAST_TOPK_REASON: str | None = None  # 限频：仅在降级原因变化时才打 warning


def _mark_topk_health(reason: str | None, snapshot_date: str | None,
                      detail: str | None = None) -> None:
    """记录 score_topk 数据源健康状态（每轮评估调用一次）。

    Args:
        reason: 降级原因；``None`` 表示本轮数据源正常。
    """
    global _LAST_TOPK_REASON
    now = datetime.now().isoformat(timespec="seconds")
    _TOPK_HEALTH.update({
        "degraded": reason is not None,
        "reason": reason,
        "detail": detail,
        "snapshot_date": snapshot_date,
        "checked_at": now,
    })
    if reason is None:
        _LAST_TOPK_REASON = None
        _TOPK_HEALTH["last_ok_at"] = now
        return
    if reason != _LAST_TOPK_REASON:
        # 限频：盘中每 30s 评估一轮，原因不变时只刷新 checked_at，不刷日志
        logger.warning(
            f"[alerts] score_topk 数据源降级，规则本轮跳过（非静默）："
            f"reason={reason} snapshot_date={snapshot_date} detail={detail}")
    _LAST_TOPK_REASON = reason


def _load_latest_predictions(
        top_k: int) -> tuple[set[str], set[str], str | None, str | None]:
    """最新两期 predictions 的 top-K 集合。

    Returns:
        ``(cur_k, prev_k, snapshot_date, degraded)``：``degraded`` 为降级原因，
        ``None`` 表示数据源正常。
    """
    import polars as pl

    files = sorted((get_settings().DATA_ROOT / "predictions").glob("date=*.parquet"))
    if not files:
        _mark_topk_health("no_predictions", None)
        return set(), set(), None, "no_predictions"

    snap_date = files[-1].stem.replace("date=", "")
    cur = pl.read_parquet(files[-1])
    from ...data.screening import filter_universe
    try:
        cur, _ = filter_universe(cur, str(cur["date"][0])[:10], "all",
                                 require_universe=True)
    except AQPException as e:
        _mark_topk_health("universe_snapshot_missing", snap_date, str(e.message))
        return set(), set(), None, "universe_snapshot_missing"

    cur_k = set(cur.sort("pred_score", descending=True).head(top_k)["symbol"].to_list())
    prev_k: set[str] = set()
    if len(files) >= 2:
        prev = pl.read_parquet(files[-2])
        try:
            prev, _ = filter_universe(prev, str(prev["date"][0])[:10], "all",
                                      require_universe=True)
            prev_k = set(prev.sort("pred_score", descending=True)
                         .head(top_k)["symbol"].to_list())
        except AQPException as e:
            # 前日基准不可信：若退化为空集继续判定，会把全部 Top-K 标的误判为
            # "新进"（enter 风暴），因此放弃本轮判定并留痕，不用空集当基准。
            _mark_topk_health("prev_universe_snapshot_missing", snap_date,
                              str(e.message))
            return set(), set(), None, "prev_universe_snapshot_missing"
    _mark_topk_health(None, snap_date)
    return cur_k, prev_k, snap_date, None


def _check_data_health(rule: AlertRule) -> list[dict]:
    """data_health 三指标：磁盘水位 / 最近流水线失败 / 缓存源降级。"""
    params = json.loads(rule.params_json or "{}")
    metric = params.get("metric", "disk")
    out: list[dict] = []
    if metric == "disk":
        usage = _disk_usage_percent(get_settings().DATA_ROOT)
        threshold = float(params.get("threshold", 90))
        if usage is not None and usage >= threshold:
            out.append({"metric": "disk", "usage_percent": round(usage, 1),
                        "threshold": threshold})
    elif metric == "pipeline":
        import sqlite3

        s = get_settings()
        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT status, error_message, COALESCE(finished_at, started_at) "
                "FROM data_jobs WHERE COALESCE(finished_at, started_at) >= ? "
                "ORDER BY id DESC LIMIT 5", (cutoff,)).fetchall()
        failed = [r for r in row if r[0] not in ("SUCCESS", "RUNNING")]
        if failed:
            out.append({"metric": "pipeline", "failed_jobs": len(failed),
                        "last_error": str(failed[0][1])[:120]})
    elif metric == "source":
        from ..cache.redis_client import RedisClient

        s = get_settings()
        breaker = RedisClient.breaker_status()
        if s.REDIS_ENABLED and breaker.get("open"):
            out.append({"metric": "source", "reason": "redis_circuit_open",
                        "breaker": breaker})
    return out


async def evaluate_rules() -> list[dict]:
    """单轮评估：返回触发的 payloads（已落库 + 已广播）。"""
    factory = get_session_factory()
    async with factory() as sess:
        rules = (await sess.execute(
            select(AlertRule).where(AlertRule.enabled == True))).scalars().all()  # noqa: E712

    # 行情类规则：合并全部标的，一次批量快照（共享 quotes_snapshot 进程缓存）
    quote_rules = [r for r in rules
                   if r.rule_type in ("price_pct", "price_cross", "volume_spike")]
    quotes_by_sym: dict[str, dict] = {}
    if quote_rules:
        needed: list[str] = []
        for r in quote_rules:
            needed.extend(await _rule_symbols(r))
        needed = list(dict.fromkeys(needed))
        if needed:
            snapshot = await quotes_snapshot(needed)
            quotes_by_sym = {q["symbol"]: q for q in snapshot.get("quotes", [])
                             if q.get("price") is not None}

    triggered: list[dict] = []
    for rule in rules:
        # 冷却期去重：同规则 cooldown_minutes 内不重复触发
        async with factory() as sess:
            last = (await sess.execute(
                select(AlertEvent).where(AlertEvent.rule_id == rule.id)
                .order_by(AlertEvent.id.desc()).limit(1))).scalar_one_or_none()
        if last is not None and rule.cooldown_minutes > 0:
            last_at = last.triggered_at
            # SQLite CURRENT_TIMESTAMP 存 UTC（naive），冷却比较必须同样用 utcnow，
            # 否则本地时区差（如 CST+8）会让冷却恒不生效
            if last_at and datetime.utcnow() - last_at < timedelta(minutes=rule.cooldown_minutes):
                continue

        payloads = await _evaluate_one(rule, quotes_by_sym)
        for payload in payloads:
            await _dispatch(rule, payload)
            triggered.append({"rule_id": rule.id, "rule": rule.name,
                              "rule_type": rule.rule_type, **payload})
    return triggered


async def _evaluate_one(rule: AlertRule, quotes_by_sym: dict[str, dict]) -> list[dict]:
    """单规则评估，返回触发 payloads（0..N）。"""
    try:
        if rule.rule_type == "price_pct":
            threshold = float(json.loads(rule.params_json or "{}").get("threshold", 3))
            out = []
            for sym in await _rule_symbols(rule):
                q = quotes_by_sym.get(sym)
                if q and q.get("pct") is not None and abs(float(q["pct"])) >= threshold:
                    out.append({"symbol": sym, "pct": q["pct"], "price": q["price"],
                                "threshold_pct": threshold, "as_of": q.get("as_of")})
            return out

        if rule.rule_type == "price_cross":
            p = json.loads(rule.params_json or "{}")
            target, direction = float(p.get("price", 0)), p.get("direction", "up")
            out = []
            for sym in await _rule_symbols(rule):
                q = quotes_by_sym.get(sym)
                if not q or q.get("prev_close") is None:
                    continue
                price, prev = float(q["price"]), float(q["prev_close"])
                crossed = (prev < target <= price if direction == "up"
                           else prev > target >= price)
                if crossed:
                    out.append({"symbol": sym, "price": price, "prev_close": prev,
                                "threshold_price": target, "direction": direction,
                                "as_of": q.get("as_of")})
            return out

        if rule.rule_type == "volume_spike":
            p = json.loads(rule.params_json or "{}")
            window, k = int(p.get("window", 20)), float(p.get("k", 3.0))
            out = []
            for sym in await _rule_symbols(rule):
                q = quotes_by_sym.get(sym)
                if not q or q.get("volume") is None:
                    continue
                from ...data.parquet_store import read_symbol_dataset

                bars = read_symbol_dataset("daily_bar", sym).tail(window + 1)
                if bars.height < 3:
                    continue  # 历史不足，不判定（不造数）
                hist = [float(v) for v in bars["volume"].to_list()[:-1] if v and v == v]
                if not hist:
                    continue
                avg = sum(hist) / len(hist)
                if avg > 0 and float(q["volume"]) >= avg * k:
                    out.append({"symbol": sym, "volume_hand": q["volume"],
                                "avg_volume_hand": round(avg, 1), "k": k,
                                "window": len(hist), "as_of": q.get("as_of")})
            return out

        if rule.rule_type == "score_topk":
            k_top = int(json.loads(rule.params_json or "{}").get("k", 50))
            cur_k, prev_k, snap_date, degraded = _load_latest_predictions(k_top)
            if degraded:
                # 静默失效防护：降级状态已由 _mark_topk_health 写入 _TOPK_HEALTH
                # 并限频打点，运维可经 GET /alerts/health 观测——不允许悄悄什么都不做。
                logger.warning(
                    f"[alerts] rule {rule.id}({rule.rule_type}) 本轮跳过：{degraded}")
                return []
            if not cur_k:
                return []  # 无预测结果，不判定
            out = []
            for sym in await _rule_symbols(rule):
                if sym in cur_k and sym not in prev_k:
                    out.append({"symbol": sym, "action": "enter", "k": k_top,
                                "snapshot_date": snap_date})
                elif sym not in cur_k and sym in prev_k:
                    out.append({"symbol": sym, "action": "leave", "k": k_top,
                                "snapshot_date": snap_date})
            return out

        if rule.rule_type == "factor_quantile":
            return _evaluate_factor_quantile(rule)

        if rule.rule_type == "data_health":
            return _check_data_health(rule)
    except Exception as e:  # noqa: BLE001 单规则失败不拖垮整轮评估
        logger.warning(f"[alerts] evaluate rule {rule.id}({rule.rule_type}) failed: {e!r}")
    return []


def _evaluate_factor_quantile(rule: AlertRule) -> list[dict]:
    """因子分位规则：features 最新日值 >= 历史 window 日 (1-quantile) 分位。"""
    import polars as pl

    p = json.loads(rule.params_json or "{}")
    factor, window = str(p.get("factor")), int(p.get("window", 250))
    quantile = float(p.get("quantile", 0.95))

    root = get_settings().DATA_ROOT / "features"
    try:
        version, df = read_feature_frame()
    except AQPException as exc:
        logger.warning(
            f"[alerts] rule {rule.id} factor_quantile 无 features 文件；"
            f"root={root} detail={exc.message}")
        return []
    if factor not in df.columns:
        logger.warning(
            f"[alerts] rule {rule.id} factor_quantile 跳过："
            f"features 版本 {version} 不存在因子列 {factor!r}")
        return []
    df = df.filter(pl.col("symbol") == rule.symbol).sort("date").tail(window + 1)
    if df.height < 30:
        logger.warning(
            f"[alerts] rule {rule.id} factor_quantile 跳过："
            f"features 版本 {version} 标的 {rule.symbol} 样本不足（{df.height} < 30）")
        return []
    vals = [float(v) for v in df[factor].to_list()[:-1] if v is not None and v == v]
    cur = df[factor].to_list()[-1]
    if cur is None or cur != cur or not vals:
        logger.warning(
            f"[alerts] rule {rule.id} factor_quantile 跳过："
            f"features 版本 {version} 当前值或历史有效样本缺失")
        return []
    vals.sort()
    idx = min(len(vals) - 1, max(0, int(round(quantile * len(vals))) - 1))
    threshold = vals[idx]
    if float(cur) >= threshold:
        return [{"symbol": rule.symbol, "factor": factor, "value": round(float(cur), 6),
                 "quantile": quantile, "threshold": round(threshold, 6),
                 "window_days": len(vals), "date": str(df["date"].to_list()[-1])}]
    return []


async def _dispatch(rule: AlertRule, payload: dict) -> None:
    """触发分发：落库（站内信历史）+ SSE 广播 + webhook（渠道可选）。"""
    event_payload = {"rule_id": rule.id, "rule": rule.name,
                     "rule_type": rule.rule_type,
                     "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **payload}
    factory = get_session_factory()
    async with factory() as sess:
        sess.add(AlertEvent(rule_id=rule.id, symbol=payload.get("symbol"),
                            payload_json=json.dumps(event_payload, ensure_ascii=False)))
        await sess.commit()

    if "sse" in json.loads(rule.channels_json or '["sse"]'):
        publish_alert({**event_payload, "ts": datetime.now().strftime("%H:%M:%S")})
    if "webhook" in json.loads(rule.channels_json or "[]"):
        webhook_url = get_settings().NOTIFY_WEBHOOK_URL
        if webhook_url:
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    await client.post(webhook_url, json={"kind": "alert",
                                                         **event_payload})
            except Exception as e:  # noqa: BLE001 webhook 失败不影响站内信
                logger.warning(f"[alerts] webhook post failed: {e!r}")


# ---------------- 调度 ----------------
def _is_market_open(now: datetime | None = None) -> bool:
    """A股盘中（工作日 09:15-15:05，与前端 isMarketOpen 同口径）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 555 <= mins <= 905


async def alert_scheduler() -> None:
    """lifespan 后台调度：盘中每 30s，盘后每小时（autoSync 同款模式）。"""
    try:
        while True:
            interval = 30 if _is_market_open() else 3600
            try:
                events = await evaluate_rules()
                if events:
                    logger.info(f"[alerts] {len(events)} rule(s) triggered: "
                                + ", ".join(f"#{e['rule_id']}:{e.get('symbol')}" for e in events))
            except Exception as e:  # noqa: BLE001 调度轮空异常不终止循环
                logger.warning(f"[alerts] evaluate round failed: {e!r}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass

