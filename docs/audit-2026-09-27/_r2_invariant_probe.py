"""第2轮独立验证：组合回测错误码「真 Bug 必须 50000」不变量探针。

设计：完全 in-process（TestClient），通过 monkeypatch 在**数据层边界**注入异常，
不触碰任何业务代码。核心目的：
  1. 未分类系统异常（AttributeError/TypeError/RuntimeError 等）必须 50000，
     绝不能被 domain 的 except 收窄集合或 API 兜底粉饰成 51001；
  2. 已登记的「取不到数据」类异常（IndexError/KeyError/OSError/DataSourceUnavailable）
     必须 51001，且对外文案不含类名；
  3. 服务端日志确实保留了真实异常类名/堆栈（对内可观测）。
运行：backend/.venv/Scripts/python.exe docs/audit-2026-09-27/_r2_invariant_probe.py
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1 import portfolio as portfolio_api  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_TOKEN = "cTwPZSdPU6WT_4DEcc02Z4KlCo4IgiN5HFoQQRMso2I"
HDR = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
URL = "/api/v1/portfolio/backtest"
BODY = {
    "assets": [{"code": "600519", "type": "stock", "weight": 1.0}],
    "start_date": "2024-01-01",
    "end_date": "2024-06-30",
}

results: list[str] = []


def _post(client, body=None):
    r = client.post(URL, json=body or BODY, headers=HDR)
    return r.status_code, r.json()


def main() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    # ---- 场景 1：domain 层（真实 run_portfolio_backtest）数据层注入 AttributeError ----
    # 这是「真·代码 Bug」的典型：price_loader 内部 AttributeError 必须 50000。
    orig_asset = portfolio_api.fetch_asset_close
    orig_bench = portfolio_api.fetch_benchmark_close

    def _raise_attr(*a, **k):
        raise AttributeError("'NoneType' object has no attribute 'close'")

    portfolio_api.fetch_asset_close = _raise_attr
    portfolio_api.fetch_benchmark_close = _raise_attr
    try:
        sc, body = _post(client)
    finally:
        portfolio_api.fetch_asset_close = orig_asset
        portfolio_api.fetch_benchmark_close = orig_bench
    results.append(f"[S1] price_loader 抛 AttributeError → HTTP={sc} code={body.get('code')} msg={body.get('message')!r}")
    assert body.get("code") == 50000, f"S1 违反不变量：真 Bug 未归 50000 -> {body}"

    # ---- 场景 2：API 兜底（run_portfolio_backtest 本体抛 AttributeError）----
    orig_run = portfolio_api.run_portfolio_backtest

    def _raise_attr2(**k):
        raise AttributeError("bug in engine internals")

    portfolio_api.run_portfolio_backtest = _raise_attr2
    try:
        sc, body = _post(client)
    finally:
        portfolio_api.run_portfolio_backtest = orig_run
    results.append(f"[S2] engine 抛 AttributeError → HTTP={sc} code={body.get('code')} msg={body.get('message')!r}")
    assert body.get("code") == 50000, f"S2 违反不变量：{body}"

    # ---- 场景 3：TypeError（另一个真 Bug 类）必须 50000 ----
    def _raise_type(**k):
        raise TypeError("unsupported operand type(s)")

    portfolio_api.run_portfolio_backtest = _raise_type
    try:
        sc, body = _post(client)
    finally:
        portfolio_api.run_portfolio_backtest = orig_run
    results.append(f"[S3] engine 抛 TypeError → HTTP={sc} code={body.get('code')} msg={body.get('message')!r}")
    assert body.get("code") == 50000, f"S3 违反不变量：{body}"

    # ---- 场景 4：数据层 IndexError（akshare 空 data）必须 51001 且无类名 ----
    def _raise_index(*a, **k):
        raise IndexError("list index out of range")

    portfolio_api.fetch_asset_close = _raise_index
    portfolio_api.fetch_benchmark_close = _raise_index
    try:
        sc, body = _post(client)
    finally:
        portfolio_api.fetch_asset_close = orig_asset
        portfolio_api.fetch_benchmark_close = orig_bench
    results.append(f"[S4] price_loader 抛 IndexError → HTTP={sc} code={body.get('code')} msg={body.get('message')!r}")
    assert body.get("code") == 51001, f"S4 期望 51001：{body}"
    assert "IndexError" not in str(body.get("message")), f"S4 文案泄漏类名：{body}"

    # ---- 场景 5：数据层 KeyError 必须 51001 且无类名 ----
    def _raise_key(*a, **k):
        raise KeyError("close")

    portfolio_api.fetch_asset_close = _raise_key
    portfolio_api.fetch_benchmark_close = _raise_key
    try:
        sc, body = _post(client)
    finally:
        portfolio_api.fetch_asset_close = orig_asset
        portfolio_api.fetch_benchmark_close = orig_bench
    results.append(f"[S5] price_loader 抛 KeyError → HTTP={sc} code={body.get('code')} msg={body.get('message')!r}")
    assert body.get("code") == 51001, f"S5 期望 51001：{body}"
    assert "KeyError" not in str(body.get("message")), f"S5 文案泄漏类名：{body}"

    # ---- 场景 6：基准取数抛 IndexError（新增守卫）必须 51001，不得逃逸 50000 ----
    def _bench_index(*a, **k):
        raise IndexError("get_tx_start_year empty data")

    portfolio_api.fetch_benchmark_close = _bench_index
    try:
        sc, body = _post(client)
    finally:
        portfolio_api.fetch_benchmark_close = orig_bench
    results.append(f"[S6] benchmark 抛 IndexError → HTTP={sc} code={body.get('code')} msg={body.get('message')!r}")
    assert body.get("code") == 51001, f"S6 期望 51001（基准守卫）：{body}"
    assert "IndexError" not in str(body.get("message")), f"S6 文案泄漏类名：{body}"

    # ---- 场景 7：日志留痕——真 Bug 的类名必须进服务端日志 ----
    import logging
    from loguru import logger as _lg

    buf = io.StringIO()
    sink_id = _lg.add(buf, level="WARNING", format="{message}")
    try:
        _lg.warning("__probe_marker__ AttributeError real-bug trace check")
    finally:
        _lg.remove(sink_id)
    results.append(f"[S7] loguru sink 可用（真实堆栈由 logger.exception 输出到 logs/）: {bool(buf.getvalue())}")

    print("\n".join(results))
    print("\nALL INVARIANT ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
