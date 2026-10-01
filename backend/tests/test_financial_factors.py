"""财务因子 PIT 安全回归守卫（Task #19）。

本文件钉死三件事，任一条被破坏都会让"财务因子接线"变成前视泄漏：

TC-FIN-ASOF            公告日之前该财报**不可见**（核心反证）
TC-FIN-ORACLE          代理公告日（period+45d）与 A 股法定截止日重叠 ⇒
                       全 NaN（把"接线即喂噪声"从口头判断变成可执行断言）
TC-FIN-NO-FILL         无财报覆盖保留 NaN，绝不填 0（不造数）
TC-FIN-DEFAULT-OFF     生产特征空间默认**不含**财务列（FEATURE_VERSION 不 bump）
TC-FIN-SAME-DAY        同 announce_date 多期取报告期最新一期
TC-FIN-YOY-BASIS       同比只认"恰好早一年的同季"，缺项置 NaN

数据全部手工构造（不依赖 data-engineer 的真实公告日改造）。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.features import build_factors, factor_columns
from app.ml.financial_factors import (
    FINANCIAL_FACTOR_COLUMNS,
    build_financial_factors,
)

SYM = "000001.SZ"


def _panel(dates: list[date], sym: str = SYM) -> pd.DataFrame:
    """构造只用 date/symbol 的最小面板（财务 join 不读 OHLCV）。"""
    return pd.DataFrame({
        "symbol": [sym] * len(dates),
        "date": dates,
        "close": np.linspace(10.0, 12.0, len(dates)),
    })


def _fin(sym: str, rows: list[dict]) -> pd.DataFrame:
    """构造数据源返回帧（symbol 列 + 调用方给定的财报行）。"""
    return pd.DataFrame([dict(r, symbol=sym) for r in rows])


def _source(rows: list[dict]) -> tuple[object, list[date]]:
    """返回 (source, calls)：source 记录每次被问的 asof（用于断言 asof 语义）。"""
    calls: list[date] = []

    def src(symbol: str, asof: date) -> pd.DataFrame:
        calls.append(asof)
        return _fin(symbol, rows)

    return src, calls


# ---------------- 核心：前视偏差证明 ----------------
def test_tc_fin_asof_not_visible_before_announce_date():
    """**前视偏差证明**：d1 < announce_date <= d2 ⇒ d1 不可见该期财报。

    构造：两根 K 线日（财报公告前 / 公告后），一期 ROE=30% 的财报在两者之间
    公告。断言：
      - d1（公告前）的 f_roe **不等于** 30%，且为 NaN（此前无已公告财报）；
      - d2（公告当日）的 f_roe == 30%。
    反证补丁：把 cutoff 从 announce_date 改成 period 或 announce_date-1
    （等价于"用报告期当可见日"这一常见错误）会立刻让 d1 取到 30% ⇒ 本断言
    确实在守 PIT，而不是恒真。
    """
    announce = date(2024, 3, 20)
    d1, d2 = date(2024, 3, 1), date(2024, 3, 20)   # d1 < announce <= d2（同日可见）
    panel = _panel([d1, d2])
    src, _ = _source([{"period": date(2023, 12, 31), "announce_date": announce,
                       "roe": 30.0, "revenue": 1e9}])

    out = build_financial_factors(panel, source=src, enabled=True)

    assert np.isnan(out.loc[0, "f_roe"]), (
        f"公告日之前该财报不可见，实际 d1 f_roe={out.loc[0, 'f_roe']}")
    assert np.isnan(out.loc[0, "f_revenue_yoy"])
    assert out.loc[1, "f_roe"] == pytest.approx(30.0), "公告当日必须可见"
    assert out.index.tolist() == panel.index.tolist(), "索引/行序必须与面板对齐"

    # 反证：把财报行的可见日改成"报告期"（典型前视错误）⇒ d1 立刻取到 30%
    leaky = panel.copy()
    leaky["date"] = [date(2024, 1, 5), d2]   # 报告期之后、公告之前的 K 线日
    out2 = build_financial_factors(
        leaky, source=lambda s, a: _fin(s, [{"period": date(2023, 12, 31),
                                             "announce_date": date(2023, 12, 31),
                                             "roe": 30.0, "revenue": 1e9}]),
        enabled=True)
    assert out2.loc[0, "f_roe"] == pytest.approx(30.0), (
        "对照组失效：若报告期可见也取不到值，说明本用例没有真正验证 asof")


def test_tc_fin_asof_uses_previous_period_before_new_announce():
    """公告前应显示**上一期**财报（而非 NaN）——asof 的"最新已公告"语义。"""
    d_prev, d_ann, d_next = date(2024, 4, 1), date(2024, 4, 25), date(2024, 7, 1)
    panel = _panel([d_prev, d_ann, d_next])
    src, _ = _source([
        {"period": date(2023, 12, 31), "announce_date": d_prev - timedelta(days=10),
         "roe": 8.0, "revenue": 1e9},
        {"period": date(2024, 3, 31), "announce_date": d_ann,
         "roe": 20.0, "revenue": 2e9},
    ])
    out = build_financial_factors(panel, source=src, enabled=True)

    assert out.loc[0, "f_roe"] == pytest.approx(8.0), "公告前应见上一期（Q4）"
    assert out.loc[1, "f_roe"] == pytest.approx(20.0), "公告当日切到本期（Q1）"
    assert out.loc[2, "f_roe"] == pytest.approx(20.0)


def test_tc_fin_oracle_trap_all_nan_when_announce_after_deadline():
    """**oracle 陷阱本体**：announce_date = 报告期 + 45 天 ⇒ 因子恒全 NaN。

    A 股法定披露截止日：Q1 = 4/30（= 3/31 + 30 天）比代理日 (3/31 + 45 天)
    **早 15 天** ⇒ 代理公告日在结构上必然晚于截止日。而面板行情是当日收盘，
    于是每根 K 线的"当日"都早于该财报的可见日 ⇒ asof join 永不命中。

    本用例把这条经济事实固化成断言：只要它通过，就证明"现在把
    FEATURE_FINANCIAL 打开 = 给模型喂全 NaN 的假因子"，任何声称
    "已接线财务因子且有效"的说法都必须先让本用例失败。
    """
    # 2023 全年交易日（工作日近似），只关心 4/30 截止日前后
    days = [date(2023, 1, 1) + timedelta(days=i) for i in range(365)]
    days = [d for d in days if d.weekday() < 5]
    panel = _panel(days)
    proxy_ann = date(2023, 3, 31) + timedelta(days=45)   # 2023-05-15
    src, _ = _source([{"period": date(2023, 3, 31), "announce_date": proxy_ann,
                       "roe": 12.0, "revenue": 5e8}])

    out = build_financial_factors(panel, source=src, enabled=True)
    # 代理公告日（5/15）之前的行必然全 NaN —— 这就是 oracle 陷阱
    early = panel["date"] < proxy_ann
    assert out.loc[early, list(FINANCIAL_FACTOR_COLUMNS)].isna().all().all(), (
        "代理公告日之前就取到了财报值 ⇒ 代理日未晚于截止日，前提被破坏")
    # 而 4/30（法定截止日）时真实财报早已公告；下面的对照组证明如果
    # announce_date 用真实公告日（4/20），4/30 就能取到值 ⇒ 结论是
    # "数据源口径"导致的全 NaN，不是 asof 实现错误。
    src_real, _ = _source([{"period": date(2023, 3, 31),
                            "announce_date": date(2023, 4, 20),
                            "roe": 12.0, "revenue": 5e8}])
    out_real = build_financial_factors(panel, source=src_real, enabled=True)
    d_430 = panel.index[panel["date"] == date(2023, 4, 28)][0]  # 4/30 为周日
    assert out_real.loc[d_430, "f_roe"] == pytest.approx(12.0), (
        "真实公告日下 4/30 前后应可见 ⇒ 实现正确，全 NaN 由代理公告日造成")


def test_tc_fin_no_fill_na_for_uncovered_symbol():
    """无财报覆盖（新股/未入库）保留 NaN，绝不填 0。"""
    panel = _panel([date(2024, 5, 1), date(2024, 5, 2)])
    out = build_financial_factors(panel, source=lambda s, a: pd.DataFrame(),
                                  enabled=True)
    assert out[list(FINANCIAL_FACTOR_COLUMNS)].isna().all().all()
    # 0 会把"无覆盖"与"ROE=0 / 营收同比 0%"混为一谈（后者在 A 股是极端值）
    assert not (out[list(FINANCIAL_FACTOR_COLUMNS)] == 0).any().any()


def test_tc_fin_data_source_failure_does_not_break_panel():
    """单只标的读取抛错时全 NaN 且不拖垮整表（与 build_factors 的容错一致）。"""
    panel = pd.concat([
        _panel([date(2024, 5, 1)], sym="A.SZ"),
        _panel([date(2024, 5, 1)], sym="B.SZ"),
    ], ignore_index=True)

    def bad_src(symbol: str, asof: date) -> pd.DataFrame:
        if symbol == "A.SZ":
            raise RuntimeError("db locked")
        return _fin(symbol, [{"period": date(2024, 3, 31),
                              "announce_date": date(2024, 4, 20),
                              "roe": 5.0, "revenue": 1e8}])

    out = build_financial_factors(panel, source=bad_src, enabled=True)
    assert np.isnan(out.loc[0, "f_roe"]), "失败标的应 NaN"
    assert out.loc[1, "f_roe"] == pytest.approx(5.0), "其余标的必须照常产出"


def test_tc_fin_same_day_multiple_periods_takes_latest():
    """同 announce_date 多期（追溯重述）取报告期最新的一期。"""
    d = date(2024, 5, 10)
    panel = _panel([d])
    src, _ = _source([
        {"period": date(2023, 9, 30), "announce_date": d, "roe": 3.0, "revenue": 1e8},
        {"period": date(2023, 12, 31), "announce_date": d, "roe": 9.0, "revenue": 2e8},
    ])
    out = build_financial_factors(panel, source=src, enabled=True)
    assert out.loc[0, "f_roe"] == pytest.approx(9.0), "应取报告期最新一期"


def test_tc_fin_yoy_basis_requires_exact_prior_year_quarter():
    """同比只认"恰好早一年且同月同日"的报告期；缺项/跨年错配 => NaN。"""
    d = date(2024, 5, 10)
    panel = _panel([d])

    # (a) 完整去年同期 => 有值：2e8 / 1e8 - 1 = 1.0
    src, _ = _source([
        {"period": date(2023, 3, 31), "announce_date": date(2023, 4, 20),
         "roe": 5.0, "revenue": 1e8},
        {"period": date(2024, 3, 31), "announce_date": date(2024, 4, 20),
         "roe": 6.0, "revenue": 2e8},
    ])
    out = build_financial_factors(panel, source=src, enabled=True)
    assert out.loc[0, "f_revenue_yoy"] == pytest.approx(1.0)
    assert out.loc[0, "f_log_revenue"] == pytest.approx(np.log(2e8))

    # (b) 缺去年同期 => NaN（不得拿相邻季度冒充同比）
    src_missing, _ = _source([
        {"period": date(2024, 3, 31), "announce_date": date(2024, 4, 20),
         "roe": 6.0, "revenue": 2e8},
    ])
    out_m = build_financial_factors(panel, source=src_missing, enabled=True)
    assert np.isnan(out_m.loc[0, "f_revenue_yoy"])

    # (c) 去年同期 revenue <= 0（ST 巨亏）=> NaN，不产出符号翻转的假增速
    src_neg, _ = _source([
        {"period": date(2023, 3, 31), "announce_date": date(2023, 4, 20),
         "roe": 5.0, "revenue": -1e8},
        {"period": date(2024, 3, 31), "announce_date": date(2024, 4, 20),
         "roe": 6.0, "revenue": 2e8},
    ])
    out_n = build_financial_factors(panel, source=src_neg, enabled=True)
    assert np.isnan(out_n.loc[0, "f_revenue_yoy"])


def test_tc_fin_source_queried_with_panel_max_date_not_today():
    """asof 必须取**面板最大日期**：用 today 会让全部历史日看见最新财报（前视）。"""
    dates = [date(2020, 1, 2), date(2024, 6, 3)]
    panel = _panel(dates)
    src, calls = _source([])
    build_financial_factors(panel, source=src, enabled=True)
    assert calls == [date(2024, 6, 3)], (
        f"asof 应为面板最大日期 2024-06-03，实际 {calls}")


def test_tc_fin_accepts_ms_resolution_dates_from_parquet():
    """面板日期为 datetime64[ms]（parquet 实测口径）时不得抛 MergeError。

    回归背景：真实 ``daily_bar_hfq`` parquet 读出的是 datetime64[ms]，而
    ``to_datetime`` 默认 datetime64[ns] ⇒ ``merge_asof`` 直接
    ``MergeError: incompatible merge keys``。这是用真实数据跑出来的，不是
    理论风险 —— 本用例把该口径钉死。
    """
    d = date(2024, 5, 10)
    panel = _panel([d])
    panel["date"] = panel["date"].astype("datetime64[ms]")
    assert panel["date"].dtype == "datetime64[ms]"
    src, _ = _source([{"period": date(2024, 3, 31), "announce_date": date(2024, 4, 20),
                       "roe": 7.0, "revenue": 1e8}])
    out = build_financial_factors(panel, source=src, enabled=True)
    assert out.loc[0, "f_roe"] == pytest.approx(7.0)


# ---------------- 生产接线默认关闭（FEATURE_VERSION 不 bump） ----------------
def test_tc_fin_default_off_and_feature_space_unchanged(monkeypatch):
    """默认关闭时：全 NaN 列；且 ``build_factors`` 的特征空间**不含** f_* 列。

    这是"不 bump FEATURE_VERSION"这一决定的**可执行依据**：生产
    ``build_factors`` 输出未变 ⇒ 现有模型 features.json 与面板仍匹配，
    无需重训、不会 trigger predict 500。
    """
    monkeypatch.delenv("FEATURE_FINANCIAL", raising=False)
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2023-01-02", periods=300)
    close = 10.0 * np.exp(np.cumsum(0.01 * rng.standard_normal(300)))
    raw = pd.DataFrame({
        "symbol": [SYM] * 300, "date": dates,
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 1e5,
    })
    base = build_factors(raw)
    cols = factor_columns(base)
    assert not [c for c in cols if c.startswith("f_")], (
        f"默认特征空间不应含财务列，实际 {[c for c in cols if c.startswith('f_')]}")

    # 未启用开关：即便数据源有数据也必须全 NaN（不被意外接线）
    src, _ = _source([{"period": date(2023, 3, 31),
                       "announce_date": date(2023, 4, 20),
                       "roe": 42.0, "revenue": 1e9}])
    out = build_financial_factors(base, source=src, enabled=False)
    assert out[list(FINANCIAL_FACTOR_COLUMNS)].isna().all().all()
    assert out.index.equals(base.index)

    monkeypatch.setenv("FEATURE_FINANCIAL", "1")
    out_on = build_financial_factors(base, source=src, enabled=None)
    assert out_on["f_roe"].notna().any(), "开关打开后应能取到值"
