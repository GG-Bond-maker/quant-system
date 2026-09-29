import sys
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "backend")))
from app.core.logging import setup_logging  # noqa: E402

setup_logging()
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1 import portfolio as portfolio_api  # noqa: E402
from app.main import app  # noqa: E402


def boom(**k):
    raise AttributeError("R2_LOGCHECK_REAL_BUG_sentinel")


orig = portfolio_api.run_portfolio_backtest
portfolio_api.run_portfolio_backtest = boom
try:
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post(
        "/api/v1/portfolio/backtest",
        json={"assets": [{"code": "600519", "type": "stock", "weight": 1.0}],
              "start_date": "2024-01-01", "end_date": "2024-06-30"},
        headers={"Authorization": "Bearer cTwPZSdPU6WT_4DEcc02Z4KlCo4IgiN5HFoQQRMso2I"},
    )
    print("code=", r.json().get("code"), "msg=", r.json().get("message"))
finally:
    portfolio_api.run_portfolio_backtest = orig
