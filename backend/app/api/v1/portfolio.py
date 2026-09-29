"""组合回测 API（股票 / ETF 混合资产）。"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date
from typing import Any

import orjson
from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from ...cache.keys import k_portfolio_search
from ...cache.redis_client import RedisClient
from ...core.errors import (APIResponse, AQPException, ERR_DATA_EMPTY,
                            ERR_PARAMS, ERR_SYSTEM, ok)
from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...core.config import get_settings
from ...data.parquet_store import today_trade_date_or_last
from ...data.portfolio_source import fetch_asset_close, fetch_benchmark_close
from ...domain.a_share_rules import normalize_code
from ...domain.portfolio import run_portfolio_backtest

router = APIRouter()

# domain/portfolio.py 抛出的「本地数据不可用」类 ValueError 文案前缀。
# 命中 → ERR_DATA_EMPTY(51001，数据未同步 / 区间交易日不足)；
# 未命中 → ERR_PARAMS(40000，入参语义错误，如权重和≠1、代码重复)。
# 之所以按文案判定：domain 层为纯函数，只以 ValueError 表达失败且不区分原因
# （见 domain/portfolio.py:259/279）；此处是唯一能按「来源」分流的边界。
_DOMAIN_DATA_ERROR_PREFIXES = ("部分资产数据获取失败", "对齐后有效交易日不足")


class PortfolioAsset(BaseModel):
    code: str = Field(..., min_length=1)
    type: str = Field("stock", pattern=r"^(stock|etf)$")
    weight: float = Field(..., ge=0.0, le=1.0)


class PortfolioBacktestRequest(BaseModel):
    # max_length 限制：每资产一次外部拉取（已限速），防止超大请求拖垮上游
    assets: list[PortfolioAsset] = Field(..., min_length=1, max_length=20)
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    rebalance: str = Field("M", pattern=r"^(M|Q|Y|none)$")
    benchmark: str = Field("000300")
    initial_cash: float = Field(1_000_000.0, gt=0)
    # 机构级组合构建：user=给定权重；risk_parity/max_div/inverse_vol=优化器求解
    weighting: str = Field("user", pattern=r"^(user|risk_parity|max_div|inverse_vol)$")
    cov_window: int = Field(60, ge=20, le=250,
                            description="风险类 weighting 的协方差回看窗口（交易日）")
    n_trials: int = Field(1, ge=1, le=10000,
                          description="Deflated Sharpe 的多重试验次数")

    @field_validator("end_date")
    @classmethod
    def _end_after_start(cls, end: str, info) -> str:
        data = info.data
        start = data.get("start_date")
        if start and date.fromisoformat(end) < date.fromisoformat(start):
            raise ValueError("结束日期不能早于开始日期")
        return end


def _search_assets(q: str, limit: int) -> list[dict[str, Any]]:
    """从本地 instrument 索引搜索股票与 ETF，不在请求路径抓全量远端目录。"""
    db_path = get_settings().SQLITE_PATH
    if not db_path.exists():
        return []
    normalized = q.strip().lower()
    # LIKE 通配符必须转义，否则用户输入 ``%`` 会退化成无条件全表查询。
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    contains = f"%{escaped}%"
    prefix = f"{escaped}%"
    sql = """
        SELECT code, name, instrument_type
        FROM instrument
        WHERE instrument_type IN ('stock', 'etf')
          AND (lower(code) LIKE ? ESCAPE '\\'
               OR lower(symbol) LIKE ? ESCAPE '\\'
               OR lower(name) LIKE ? ESCAPE '\\')
        ORDER BY CASE
                   WHEN lower(code) = ? OR lower(symbol) = ? THEN 0
                   WHEN lower(code) LIKE ? ESCAPE '\\' THEN 1
                   ELSE 2
                 END,
                 CASE instrument_type WHEN 'stock' THEN 0 ELSE 1 END,
                 code
        LIMIT ?
    """
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as conn:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                sql,
                (contains, contains, contains, normalized, normalized, prefix, limit),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning(f"[portfolio/search] local instrument search failed: {exc!r}")
        return []
    return [
        {
            "code": str(code),
            "name": str(name),
            "type": str(asset_type),
            **({"tracking_index": None} if asset_type == "etf" else {}),
        }
        for code, name, asset_type in rows
    ]


@router.post("/backtest", response_model=APIResponse[dict])
async def portfolio_backtest(req: PortfolioBacktestRequest,
                             _user: dict = Depends(require_role("researcher")),
                             _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """组合回测：接收资产列表、权重、时间范围、调仓频率，返回净值曲线与风险指标。"""
    # 入参代码格式归一化：本仓存在两套口径——/stock/* 要求带交易所后缀，
    # /portfolio/* 要求纯 6 位数字。此处仅在 portfolio 入口把 `600519.SH` 与
    # `600519` 两种写法收敛为纯数字（不改动其它模块的既有行为）。
    # 格式非法属**参数错误** → ERR_PARAMS(40000)，不再被下游误报成「数据为空」。
    try:
        assets = [{"code": normalize_code(a.code), "type": a.type, "weight": a.weight}
                  for a in req.assets]
    except ValueError as e:
        raise AQPException(ERR_PARAMS, str(e)) from e

    try:
        result = await asyncio.to_thread(
            run_portfolio_backtest,
            assets=assets,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_cash=req.initial_cash,
            rebalance=req.rebalance,
            benchmark_code=req.benchmark,
            weighting=req.weighting,
            cov_window=req.cov_window,
            n_trials=req.n_trials,
            price_loader=fetch_asset_close,
            benchmark_loader=fetch_benchmark_close,
        )
    except ValueError as e:
        # 拆分错误来源（审计 P1-6）：入参格式错已在上方拦成 40000；此处 domain 抛的
        # ValueError 再分两类——「本地无数据 / 区间交易日不足」→ 51001（数据为空），
        # 其余（权重和≠1、代码重复等入参语义错误）→ 40000（参数错误）。
        if str(e).startswith(_DOMAIN_DATA_ERROR_PREFIXES):
            raise AQPException(ERR_DATA_EMPTY, str(e)) from e
        raise AQPException(ERR_PARAMS, str(e)) from e
    except Exception as e:  # noqa: BLE001
        # 审计 P1-7：兜底必须归 ERR_SYSTEM(50000)，不得再粉饰成 ERR_DATA_EMPTY(51001)。
        # 走到这里的是**未分类系统异常**（IndexError/AttributeError/KeyError/DB 崩等），
        # 与「本地数据为空」语义完全不同；错报 51001 会让用户误以为只是数据没同步，
        # 也会让运维在数据链路里白排查。正常/半正常路径已在上方被 40000/51001 分流拦截。
        # 对内保留完整堆栈与真实异常类型（trace_id 由 logging patcher 自动注入）；
        # 对外只给稳定、面向用户的文案，绝不透传 type(e).__name__ 等实现细节
        # （AQPException.message 会原样进响应体，见 core/errors.py:184）。
        logger.exception("[portfolio/backtest] failed")
        raise AQPException(
            ERR_SYSTEM, "回测执行失败，请稍后重试或联系管理员") from e

    return ok(result)


@router.get("/search", response_model=APIResponse[list])
async def portfolio_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=30),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[list]:
    """资产搜索：本地 SQLite 查询；结果按用户、数据日和全部参数缓存。"""
    normalized = q.strip().lower()
    username = str(_user.get("username") or "anonymous")
    data_date = today_trade_date_or_last().strftime("%Y%m%d")
    key = k_portfolio_search(username, data_date, normalized, limit)
    cached = await RedisClient.get(key)
    if cached is not None:
        return ok(orjson.loads(cached))
    results = await asyncio.to_thread(_search_assets, normalized, limit)
    await RedisClient.set(key, orjson.dumps(results), ex=3600)
    return ok(results)
