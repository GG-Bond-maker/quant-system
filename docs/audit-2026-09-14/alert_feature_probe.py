"""独立验证 factor_quantile 只读取一个 features 版本。"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl

sys.path.insert(0, r"D:\Python_Project\Alpha Quant Platform\backend")

from app.api.v1 import alerts
from app.data import features

root = Path(tempfile.mkdtemp(prefix="aqp_alert_feature_probe_"))
for version, current in (("v1", -1.0), ("v2", 100.0)):
    directory = root / "features" / f"version={version}"
    directory.mkdir(parents=True)
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(31)]
    values = [float(i) for i in range(30)] + [current]
    pl.DataFrame({"date": dates, "symbol": ["000001.SZ"] * 31,
                  "factor_x": values}).write_parquet(directory / "year=2026.parquet")

rule = SimpleNamespace(id=999, symbol="000001.SZ", params_json=json.dumps(
    {"factor": "factor_x", "window": 30, "quantile": 0.95}))

features.get_settings = lambda: SimpleNamespace(DATA_ROOT=root, FEATURE_VERSION="v1")
v1 = alerts._evaluate_factor_quantile(rule)
features.get_settings = lambda: SimpleNamespace(DATA_ROOT=root, FEATURE_VERSION="v2")
v2 = alerts._evaluate_factor_quantile(rule)
features.get_settings = lambda: SimpleNamespace(DATA_ROOT=root, FEATURE_VERSION="")
auto = alerts._evaluate_factor_quantile(rule)
missing = SimpleNamespace(id=1000, symbol="000001.SZ", params_json=json.dumps(
    {"factor": "missing", "window": 30, "quantile": 0.95}))
missing_result = alerts._evaluate_factor_quantile(missing)

assert v1 == [], v1
assert len(v2) == 1 and v2[0]["value"] == 100.0, v2
assert len(auto) == 1 and auto[0]["value"] == 100.0, auto
assert missing_result == [], missing_result
print(json.dumps({"v1_events": v1, "v2_events": v2, "auto_events": auto,
                  "missing_factor_events": missing_result}, ensure_ascii=False))
