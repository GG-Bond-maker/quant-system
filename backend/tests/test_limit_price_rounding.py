"""审计 B2-8 防回归：涨跌停价必须与交易所「十进制 ROUND_HALF_UP」口径一致。

缺陷（P2，2026-09-21 审计）：`data/universe.py::_round_half_up_2` 用
``(x*100 + 0.5).floor() / 100``，而浮点表示使 ``v * 100`` 常落在略小于精确值的
位置（``1.265 * 100 = 126.49999999999999``）⇒ ``floor`` 得到 1.26，
交易所口径为 **1.27**，**少 1 分**。生产影响（报告只读实测）：
`limit_down` 不一致约 0.4%、`limit_up` 约 0.03%；本文件用 Decimal 参照独立复算。

本文件不依赖真实数据：用 `Decimal` 精确复刻交易所口径作为参照，
扫「半分边界」与随机昨收 × A 股实际涨跌幅档位。
"""
from __future__ import annotations

import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
import pytest  # noqa: E402

from app.data.universe import _round_half_up_2  # noqa: E402

_TWO = Decimal("0.01")


def _decimal_ref(prev_close: float, pct: float, sign: int) -> float:
    """交易所口径：十进制精确乘 + ROUND_HALF_UP 到分。"""
    v = Decimal(str(prev_close)) * (Decimal("1") + sign * Decimal(str(pct)))
    return float(v.quantize(_TWO, rounding=ROUND_HALF_UP))


# 昨收 × 涨跌幅 恰好落在 x.xx5 上的密集边界样本（原实现必错）
BOUNDARY_CASES = [
    (1.15, 0.05), (2.30, 0.10), (4.60, 0.20), (11.50, 0.30),
    (685.55, 0.30), (849.30, 0.05), (1419.35, 0.30), (1061.05, 0.30),
    (413.35, 0.30), (1463.95, 0.30), (1254.25, 0.30), (12.50, 0.20),
]
PCTS = [0.05, 0.10, 0.20, 0.30]


def _apply(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        raw=pl.col("prev_close") * (1.0 + pl.col("sign") * pl.col("pct")),
    ).with_columns(got=_round_half_up_2(pl.col("raw")))


@pytest.mark.parametrize("prev_close,pct", BOUNDARY_CASES)
@pytest.mark.parametrize("sign", [1, -1])
def test_half_cent_boundary_matches_exchange_rounding(
    prev_close: float, pct: float, sign: int
) -> None:
    """半分边界样本：涨/跌停价都必须与 Decimal 参照逐分吻合。"""
    df = pl.DataFrame([{"prev_close": prev_close, "pct": pct, "sign": sign}])
    got = float(_apply(df)["got"][0])
    want = _decimal_ref(prev_close, pct, sign)
    assert got == pytest.approx(want, abs=1e-9), (
        f"昨收 {prev_close} × (1{'+' if sign > 0 else '-'}{pct}) 的涨跌停价："
        f"实得 {got}，交易所口径 {want}（差 {(want - got) * 100:.0f} 分）")


def test_no_mismatch_over_wide_sample() -> None:
    """宽样本（随机昨收 + 实际档位）与 Decimal 参照**零不一致**。

    原实现在同口径样本上不一致率约 0.8%（limit_down 更高）。
    """
    import random

    rng = random.Random(20260921)
    closes = [round(rng.uniform(0.5, 2000.0), 2) for _ in range(20_000)]
    closes += [c for c, _ in BOUNDARY_CASES]
    rows = [{"prev_close": c, "pct": p, "sign": s}
            for c in closes for p in PCTS for s in (1, -1)]
    df = _apply(pl.DataFrame(rows)).with_columns(
        ref=pl.struct(["prev_close", "pct", "sign"]).map_elements(
            lambda s: _decimal_ref(s["prev_close"], s["pct"], s["sign"]),
            return_dtype=pl.Float64))

    bad = df.filter((pl.col("got") - pl.col("ref")).abs() > 1e-9)
    assert bad.height == 0, (
        f"{bad.height}/{df.height} 行与交易所口径不一致：\n"
        f"{bad.select(['prev_close', 'pct', 'sign', 'got', 'ref']).head(5)}")


def test_rounding_does_not_over_round_legit_values() -> None:
    """`+1e-9` 不得把合法的非边界值错误上抬（只修半分级距内的表示误差）。

    输入粒度 ≤4 位小数 ⇒ 非边界值距半分级距 ≥0.01 分，比 1e-9 大 7 个数量级。
    """
    # 明确低于边界的值：1.2649 应仍是 1.26（不得因 epsilon 变成 1.27）
    assert float(_apply(pl.DataFrame([{"prev_close": 1.2649, "pct": 0.0,
                                       "sign": 1}]))["got"][0]) == \
        pytest.approx(1.26, abs=1e-9)
    # 明确高于边界的值：1.2651 应为 1.27
    assert float(_apply(pl.DataFrame([{"prev_close": 1.2651, "pct": 0.0,
                                       "sign": 1}]))["got"][0]) == \
        pytest.approx(1.27, abs=1e-9)


def test_matches_domain_limit_decimal_helper() -> None:
    """与 `domain/limit.py` 的 `_twop`（Decimal ROUND_HALF_UP）逐例一致。

    两套实现必须给出同一涨跌停价（报告的"涨跌停 2 套实现"根因项）。
    """
    from app.domain.limit import _twop

    for prev_close, pct in BOUNDARY_CASES:
        for sign in (1, -1):
            got = float(_apply(pl.DataFrame([{"prev_close": prev_close,
                                              "pct": pct, "sign": sign}]))["got"][0])
            want = float(_twop(Decimal(str(prev_close))
                               * (Decimal("1") + sign * Decimal(str(pct)))))
            assert got == pytest.approx(want, abs=1e-9), (
                f"向量化实现 {got} ≠ domain/limit._twop {want}"
                f"（昨收 {prev_close}，pct {pct}，sign {sign}）")