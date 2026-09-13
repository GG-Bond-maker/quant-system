"""组合回测 API（股票 / ETF 混合资产）。"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from ...core.errors import APIResponse, AQPException, ERR_DATA_EMPTY, ok
from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...data import etf as etf_mod
from ...data.portfolio_source import fetch_asset_close, fetch_benchmark_close
from ...domain.portfolio import run_portfolio_backtest

router = APIRouter()


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
    """搜索股票 + ETF（阻塞 IO，放在线程中执行）。"""
    import akshare as ak

    q = q.strip()
    results: list[dict[str, Any]] = []

    # A 股（akshare 不同版本列名可能是 code/name 或 代码/名称）
    try:
        stocks = ak.stock_info_a_code_name()
        code_col = "code" if "code" in stocks.columns else "代码"
        name_col = "name" if "name" in stocks.columns else "名称"
        mask = (
            stocks[code_col].astype(str).str.contains(q, case=False, na=False) |
            stocks[name_col].astype(str).str.contains(q, case=False, na=False)
        )
        for _, row in stocks[mask].head(limit).iterrows():
            results.append({
                "code": str(row[code_col]),
                "name": str(row[name_col]),
                "type": "stock",
            })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[portfolio/search] stock search failed: {type(e).__name__}: {e}")

    # ETF（复用已有目录，只返回中国场内 ETF）
    try:
        catalog = etf_mod.build_catalog()
        for item in catalog:
            if item.get("country") != "cn":
                continue
            code = item.get("code") or ""
            name = item.get("name") or ""
            if q.lower() in str(code).lower() or q.lower() in str(name).lower():
                results.append({
                    "code": str(code),
                    "name": str(name),
                    "type": "etf",
                    "tracking_index": item.get("tracking_index"),
                })
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[portfolio/search] etf search failed: {type(e).__name__}")

    # 去重，优先保留股票（用户截图示例均为个股）
    seen = set()
    out: list[dict[str, Any]] = []
    for r in results:
        key = (r["code"], r["type"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


@router.post("/backtest", response_model=APIResponse[dict])
async def portfolio_backtest(req: PortfolioBacktestRequest,
                             _user: dict = Depends(require_role("researcher")),
                             _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """组合回测：接收资产列表、权重、时间范围、调仓频率，返回净值曲线与风险指标。"""
    try:
        assets = [{"code": a.code, "type": a.type, "weight": a.weight} for a in req.assets]
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
        raise AQPException(ERR_DATA_EMPTY, str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("[portfolio/backtest] failed")
        raise AQPException(ERR_DATA_EMPTY, f"回测执行失败: {type(e).__name__}") from e

    return ok(result)


@router.get("/search", response_model=APIResponse[list])
async def portfolio_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=30),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[list]:
    """资产搜索：同时搜索 A 股与中国 ETF。"""
    results = await asyncio.to_thread(_search_assets, q, limit)
    return ok(results)
