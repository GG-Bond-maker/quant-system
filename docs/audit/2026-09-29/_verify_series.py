"""真机验证（证据留档）：恢复后 GET /api/v1/etf/overview/series 的真实输出。

- Bearer 用 get_settings().ADMIN_TOKEN（否则 40102）；
- refresh=1 跳过 300s 缓存，确保读的是刚写回的存档；
- 逐指标打印 count / enough / comparable / 点位，并断言 net_inflow_yi
  序列里**不出现 0.0**（旧口径假零必须已净化为 null）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402

OUT = Path(__file__).with_name("_verify_series_out.txt")
URL = "http://127.0.0.1:8000/api/v1/etf/overview/series"
DAYS = 14

lines: list[str] = []


def log(msg: str) -> None:
    lines.append(str(msg))


def main() -> int:
    token = get_settings().ADMIN_TOKEN
    log(f"URL: {URL}?days={DAYS}&refresh=1")
    try:
        r = httpx.get(URL, params={"days": DAYS, "refresh": 1},
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except Exception as e:  # noqa: BLE001
        log(f"请求失败（后端未启动？）: {e!r}")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return 2
    log(f"HTTP {r.status_code}")
    body = r.json()
    log(f"code={body.get('code')}")
    data = body.get("data") or {}
    log(f"status={data.get('status')}  drawable={data.get('drawable')}  "
        f"min_points={data.get('min_points')}  as_of={data.get('as_of')}")
    log(f"reason={data.get('reason')}")

    metrics = data.get("metrics") or {}
    for name, m in metrics.items():
        pts = m.get("points") or []
        vals = [(p.get("date"), p.get("value")) for p in pts]
        log(f"\n[{name}] count={m.get('count')} enough={m.get('enough')} "
            f"comparable={m.get('comparable')} 点数={len(pts)}")
        log(f"  points={vals}")
        log(f"  dropped={[(d.get('date'), d.get('reason')[:28]) for d in (m.get('dropped') or [])]}")

    nf = (metrics.get("net_inflow_yi") or {}).get("points") or []
    zeros = [(p.get("date"), p.get("value")) for p in nf
             if isinstance(p.get("value"), (int, float)) and p.get("value") == 0]
    log(f"\nnet_inflow_yi 点数={len(nf)}；出现 0.0 的点位={zeros or '无'}")
    log(f"note={(metrics.get('net_inflow_yi') or {}).get('note')}")

    verdict = "PASS" if not zeros else "FAIL"
    log(f"\nVERDICT（net_inflow_yi 无假零）: {verdict}")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0 if verdict == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
