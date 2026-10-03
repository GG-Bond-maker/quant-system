"""只读验证：用「当前代码」跑 validate，证明分母塌缩机制 + 市场硬门禁。

严禁写入任何源码或数据（validate 只读 parquet）。
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, "backend")

from app.data.parquet_store import read_all_symbols  # noqa: E402
from app.data.pipeline import (  # noqa: E402
    VALIDATE_MIN_MARKET_UNIVERSE, VALIDATE_MISSING_TOLERANCE_RATIO,
    VALIDATE_MIN_MARKET_COVERAGE, step_validate,
)
from app.domain.a_share_rules import normalize_code  # noqa: E402

full = list(dict.fromkeys(normalize_code(c) for c in read_all_symbols("daily_bar")))
print(f"[ctx] 全市场 codes = {len(full)}  "
      f"(market_gate 下限={VALIDATE_MIN_MARKET_UNIVERSE}, "
      f"tolerance={VALIDATE_MISSING_TOLERANCE_RATIO}, coverage_floor={VALIDATE_MIN_MARKET_COVERAGE})")


def run(label: str, td: date, codes: list[str]) -> None:
    print("-" * 80)
    print(f"[{label}] step_validate({td}, {len(codes)} codes)")
    try:
        r = step_validate(td, codes)
        print(f"    PASS -> {r}")
    except Exception as e:
        print(f"    RAISE -> {type(e).__name__}: {e}")


# 1) 数据已补齐：全市场跑 09-30 应通过
run("A 现状-全市场", date(2026, 9, 30), full)

# 2) 塌缩演示：选一个「当日与上一交易日都无数据」的交易日（节后 10-08/10-09，
#    数据只到 09-30）。全市场 → 硬门禁拦截（语义正确）。
run("B 塌缩-全市场", date(2026, 10, 9), full)

# 3) 同一塌缩、但代码集 <500 ⇒ 市场门禁不启用 ⇒ 100% 丢失被静默放行
run("C 塌缩-小池(3)", date(2026, 10, 9), full[:3])
run("C 塌缩-小池(100)", date(2026, 10, 9), full[:100])
