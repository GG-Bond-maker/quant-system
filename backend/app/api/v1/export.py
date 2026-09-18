"""P2-8 导出路由：Screener xlsx + Backtest / StrategyBacktest xlsx。"""
from __future__ import annotations

import asyncio
from datetime import date as date_cls, datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from .backtest import BacktestRequest, StrategyBacktestRequest, _run
from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...core.errors import AQPException, ERR_DATA_EMPTY

router = APIRouter()


@router.get("/screener", response_class=Response)
async def export_screener(
    day: str | None = Query(None, alias="date"),
    top_k: int = Query(50, ge=1, le=200),
    board: str = Query("all"),
    _user: dict = Depends(require_role("researcher")),
) -> Response:
    """Screener Top-N Excel（UTF-8 中文安全，日期 YYYY-MM-DD）。

    P2-9：复用页面同一条富化链路 _screen（universe 关联 + 行情回填），
    导出内容与页面榜单口径一致；删除从未有数据的 pred_return /
    prob_up / confidence 三列。
    """
    from ...core.excel import screener_workbook
    from .screener import _screen

    # `_screen` 返回 `(响应体, 真实特征版本)` 二元组（D-02 起第二个返回值用于
    # 审计落库）。此前这里当成 dict 直接下标取值 → 运行期 TypeError 变成裸 50000，
    # mypy 也能抓到该类型错误。
    data, _feature_version = await asyncio.to_thread(
        _screen, date_cls.fromisoformat(day) if day else None,
        "alpha_basic_v1", top_k, board)
    # [AQP 空数据降级] _screen 在无预测时返回 date=None / items=[] 的 unavailable 分支；
    # 直接 data["date"].replace 会让 None.replace → AttributeError（TestClient 默认重抛未捕获异常）。
    # 按项目"降级恒 200、用业务码表达"口径，缺数据时抛 ERR_DATA_EMPTY，不裸 50000。
    if not data.get("date") or not data.get("items"):
        raise AQPException(ERR_DATA_EMPTY, "暂无可用选股结果，请先运行训练与推理流水线")
    items = data["items"]
    content = screener_workbook(items, meta={
        "date": data.get("date"), "top_k": top_k, "board": board,
        "strategy": data.get("strategy"), "count": data.get("count"),
    })
    filename = f"screener_{data['date'].replace('-', '')}.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/backtest", response_class=Response)
async def export_backtest(req: BacktestRequest,
                          _user: dict = Depends(require_role("researcher")),
                          _compute: None = Depends(compute_slot)) -> Response:
    """回测导出：trades + holdings 双 sheet（重跑真实引擎）。"""
    from ...core.excel import backtest_workbook

    # 重跑 Top-K 回测是 CPU 密集长任务，必须丢线程池；直接同步调用会阻塞
    # asyncio 事件循环数十秒，导致所有并发请求一起卡死（同文件
    # export_strategy_backtest 已正确使用 to_thread，此处此前漏了）。
    data = await asyncio.to_thread(_run, req)
    trades = data.get("trades", [])
    holdings = data.get("holdings", [])
    content = backtest_workbook(trades, holdings)
    filename = f"backtest_{req.start}_{req.end}.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/strategy-backtest", response_class=Response)
async def export_strategy_backtest(req: StrategyBacktestRequest,
                                   _user: dict = Depends(require_role("researcher")),
                                   _compute: None = Depends(compute_slot)) -> Response:
    """策略回测导出：trades + nav + 参数快照（用相同参数重跑真实引擎）。

    与 /export/backtest（Top-K 调仓回测）是两个不同引擎：前端回测页跑的是
    /backtest/strategy-run，此前没有任何导出入口。这里复用同一引擎，
    保证导出的交易明细 / 净值曲线与页面展示口径一致，而不是导出别的回测。
    """
    from ...core.excel import strategy_backtest_workbook
    from .backtest import _run_strategy

    data = await asyncio.to_thread(_run_strategy, req)
    meta = {
        "strategy_name": data.get("strategy_name"),
        "strategy_type": data.get("strategy_type"),
        "start": data.get("start"), "end": data.get("end"),
        "init_cash": data.get("init_cash"),
        "commission_rate": data.get("commission_rate"),
        "short_ma": data.get("short_ma"), "long_ma": data.get("long_ma"),
        "trailing_stop_pct": data.get("trailing_stop_pct"),
        "symbols": ",".join(data.get("symbols", [])),
        "annual_strategy": (data.get("kpi") or {}).get("annual_strategy"),
        "annual_benchmark": (data.get("kpi") or {}).get("annual_benchmark"),
        "sharpe": (data.get("kpi") or {}).get("sharpe"),
        "max_drawdown": (data.get("kpi") or {}).get("max_drawdown"),
        # P1-7：导出文件与页面同口径披露流动性/摩擦成本
        "liquidity_note": (data.get("liquidity") or {}).get("note", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    content = strategy_backtest_workbook(
        data.get("trades", []), data.get("nav_curve", []), meta)
    filename = f"strategy_backtest_{req.start}_{req.end}.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
