from __future__ import annotations

import pandas as pd

from app.domain.attribution import brindon_attribution


def test_brinson_benchmark_includes_benchmark_only_symbols() -> None:
    result = brindon_attribution(
        portfolio_w={"A": 1.0},
        benchmark_w={"A": 1 / 3, "B": 1 / 3, "C": 1 / 3},
        returns=pd.Series({"A": 0.06, "B": 0.03, "C": 0.00}),
        industry=pd.Series({"A": "tech", "B": "finance", "C": "energy"}),
    )

    assert result["summary"]["benchmark_return"] == 0.03
    assert abs(result["summary"]["portfolio_return"] - 0.06) < 1e-9
