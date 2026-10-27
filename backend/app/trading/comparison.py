"""Paper strategy accounts evaluated on an explicitly shared date window."""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..db.models import PaperAccount, PaperOrder
from . import paper


def compare_accounts(session: Session, account_ids: list[str], benchmark: str = "000300.SH") -> dict[str, Any]:
    """Compare executed NAVs; missing prices or history remain unavailable."""
    snapshots = [paper.account_summary(session, account_id) for account_id in account_ids]
    histories = [{row["date"]: row["equity"] for row in snapshot["nav_series"]
                  if row["equity"] is not None} for snapshot in snapshots]
    common = sorted(set.intersection(*(set(history) for history in histories))) if histories else []
    start, end = (common[0], common[-1]) if common else (None, None)
    benchmark_return = None
    if start and end and start != end:
        bars = paper._bars(benchmark)
        values = {str(day): float(close) for day, close in zip(bars["date"], bars["close"], strict=True)} if not bars.is_empty() else {}
        if values.get(start) and values.get(end):
            benchmark_return = values[end] / values[start] - 1
    rows = []
    for snapshot, history in zip(snapshots, histories, strict=True):
        result = history[end] / history[start] - 1 if start and end and start != end and history[start] > 0 else None
        account = session.get(PaperAccount, snapshot["account_id"])
        sources = list(session.scalars(sa.select(PaperOrder).where(
            PaperOrder.account_id == snapshot["account_id"]).order_by(PaperOrder.id.desc()).limit(20)))
        rows.append({"account_id": snapshot["account_id"], "strategy_version": account.strategy_version if account else None,
            "initial_cash": snapshot["initial_cash"], "equity": snapshot["equity"], "cash": snapshot["cash"],
            "frozen_cash": snapshot["frozen_cash"], "period_return": result,
            "benchmark_return": benchmark_return,
            "excess_return": result - benchmark_return if result is not None and benchmark_return is not None else None,
            "total_fees": snapshot["total_fees"], "n_fills": snapshot["n_fills"],
            "realized_pnl": snapshot["realized_pnl"], "integrity_warnings": snapshot["integrity_warnings"],
            "lineage": [{"order_id": o.id, "strategy_version": o.strategy_version, "model_version": o.model_version,
                         "signal_id": o.signal_id, "backtest_id": o.backtest_id, "run_id": o.run_id} for o in sources]})
    return {"start": start, "end": end, "benchmark": benchmark, "items": rows,
            "comparable": len(common) >= 2, "dates": common,
            "basis": "共同交易日收盘净值首尾收益，含实际成交价和显式税费；基准同窗收盘收益；缺行情不填零",
            "reason": None if len(common) >= 2 else "共同净值样本不足两个交易日"}
