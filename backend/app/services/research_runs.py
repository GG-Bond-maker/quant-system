"""Persist the lineage from a reproducible research run to paper execution."""
from __future__ import annotations

import hashlib
import json
import orjson
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from ..db.models import BacktestRun


def record_backtest(parameters: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Persist a successful computation before exposing its execution reference."""
    from ..trading.paper import sync_session_factory

    encoded = json.dumps(parameters, sort_keys=True, ensure_ascii=False)
    run_id = uuid4().hex
    version = "topk:" + hashlib.sha256(encoded.encode()).hexdigest()[:20]
    row = BacktestRun(name=run_id, run_id=run_id, strategy_version=version,
        strategy_name=parameters.get("strategy_name", "lgbm_topk"),
        start_date=date.fromisoformat(parameters["start"]), end_date=date.fromisoformat(parameters["end"]),
        init_cash=parameters.get("init_cash", 1_000_000), top_k=parameters.get("top_k", 10),
        params_json=encoded, status="success", finished_at=datetime.now())
    with sync_session_factory()() as session:
        session.add(row)
        session.flush()
        # Unavailable financial metrics must survive persistence as null, never NaN/Inf.
        result = json.loads(orjson.dumps({**result, "backtest_id": str(row.id),
            "run_id": run_id, "strategy_version": version}, default=str))
        row.summary_json = json.dumps(result, ensure_ascii=False, allow_nan=False)
        session.commit()
    return result
