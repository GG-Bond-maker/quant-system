"""Batch 1：统一「裸代码 -> 交易所」映射（``market_prefix``）的唯一事实来源。

背景（一级发现）
----------------
ETF 修复（portfolio_source 备源口径）之前，``code_to_symbol("159915")`` 会返回
**错误的** ``159915.SH``——深市 ETF 被兜底拼成上交所。该错误 symbol 会经
``datacenter._standardize_daily`` 等入库路径**静默落盘**，故必须先修映射，再修口径。

本文件钉住三件事：
    1. :func:`app.domain.a_share_rules.market_prefix` 的前缀规则
       ``6/9/5 -> sh；0/1/2/3 -> sz；4/8 -> bj``；
    2. ``code_to_symbol`` 与 ``data.portfolio_source._market_symbol`` **委托**同一函数，
       三者在全部代表代码上**逐字一致**（消除"入库 symbol 与外呼 symbol 各算各的"）；
    3. 既有股票/沪 ETF/北交所代码**零行为变更**，仅 ``1xxxxx`` 由 ``.SH`` 修正为 ``.SZ``。

不依赖网络、不碰生产 ``data/``（仅纯函数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data import portfolio_source as psrc  # noqa: E402
from app.data.ingest import etf_instruments as etf_ing  # noqa: E402
from app.domain import a_share_rules as rules  # noqa: E402

# 代表代码 -> (期望前缀, 期望 code_to_symbol, 期望 _market_symbol, 说明)
_CASES: list[tuple[str, str, str, str, str]] = [
    ("600519", "sh", "600519.SH", "sh600519", "沪主板股(6)"),
    ("000001", "sz", "000001.SZ", "sz000001", "深主板股(0)"),
    ("300750", "sz", "300750.SZ", "sz300750", "创业板股(3)"),
    ("900901", "sh", "900901.SH", "sh900901", "沪B股(9)"),
    ("510300", "sh", "510300.SH", "sh510300", "沪ETF(5)"),
    ("159915", "sz", "159915.SZ", "sz159915", "深ETF(1) ＜修正点＞"),
    ("430047", "bj", "430047.BJ", "bj430047", "北交所(4)"),
]


@pytest.mark.parametrize("code,prefix,_sym,_msym,desc", _CASES)
def test_market_prefix_rule(code: str, prefix: str, _sym: str, _msym: str, desc: str) -> None:
    """前缀规则逐个钉住（6/9/5->sh；0/1/2/3->sz；4/8->bj）。"""
    assert rules.market_prefix(code) == prefix, f"{desc} {code} 前缀错误"


@pytest.mark.parametrize("code,_prefix,symbol,_msym,desc", _CASES)
def test_code_to_symbol_delegates(code: str, _prefix: str, symbol: str, _msym: str, desc: str) -> None:
    """``code_to_symbol`` 输出正确 symbol；``159915`` 必须为 ``.SZ``（原缺陷回归钉）。"""
    assert rules.code_to_symbol(code) == symbol, f"{desc} {code} symbol 错误"


@pytest.mark.parametrize("code,_prefix,_sym,msym,desc", _CASES)
def test_market_symbol_delegates(code: str, _prefix: str, _sym: str, msym: str, desc: str) -> None:
    """``portfolio_source._market_symbol`` 与 :func:`market_prefix` 委托后逐字一致。"""
    assert psrc._market_symbol(code) == msym, f"{desc} {code} 外呼 symbol 错误"


@pytest.mark.parametrize("code,_prefix,_sym,_msym,desc", _CASES)
def test_triple_consistency_single_source_of_truth(
    code: str, _prefix: str, _sym: str, _msym: str, desc: str,
) -> None:
    """核心不变量：入库后缀 == 外呼前缀 == ``market_prefix``（唯一事实来源）。"""
    suffix = rules.code_to_symbol(code).split(".")[1].lower()
    assert suffix == psrc._market_symbol(code)[:2] == rules.market_prefix(code), (
        f"{desc} {code} 三处映射不一致：{suffix} / {psrc._market_symbol(code)[:2]} / "
        f"{rules.market_prefix(code)}"
    )


def test_code_to_symbol_contract_unchanged() -> None:
    """钉住既有契约：``code_to_symbol`` 仍只认纯 6 位，且错误信息不变。"""
    assert rules.code_to_symbol("600519") == "600519.SH"
    with pytest.raises(ValueError, match="code 必须 6 位数字"):
        rules.code_to_symbol("600519.SH")


def test_market_prefix_rejects_bad_input() -> None:
    """非 6 位纯数字一律 ``ValueError``（与 code_to_symbol 同款错误信息）。"""
    for bad in ("600519.SH", "159abc", "", "51030", "5103001", "60051A"):
        with pytest.raises(ValueError, match="code 必须 6 位数字"):
            rules.market_prefix(bad)


def test_market_prefix_else_fallback_is_sh() -> None:
    """兜底前缀（``7xxxxx`` 沪市配股/申购）= ``sh``，与历史 else 分支逐字一致（零行为变更）。"""
    assert rules.market_prefix("730001") == "sh"
    assert rules.code_to_symbol("730001") == "730001.SH"


@pytest.mark.parametrize("code", ["600519", "000001", "300750", "688981"])
def test_existing_stock_symbols_unchanged(code: str) -> None:
    """既有 A 股股票 symbol 零变更（Batch 1 硬要求：不改变既有股票行为）。"""
    expected = {"600519": "600519.SH", "000001": "000001.SZ",
                "300750": "300750.SZ", "688981": "688981.SH"}[code]
    assert rules.code_to_symbol(code) == expected


@pytest.mark.parametrize("code", ["510300", "512880", "588000", "159915", "160123", "180101"])
def test_etf_symbol_matches_etf_instruments_helper(code: str) -> None:
    """ETF/LOF 代码：``code_to_symbol`` 必须与既有 ``cn_etf_symbol`` 口径一致。

    ``cn_etf_symbol`` 早已正确处理 ``1xxxxx``（深市），本用例把 ``code_to_symbol``
    拉齐到同一口径——两处若再分叉，ETF 入库 symbol 会再次错。
    """
    expected_symbol, _market = etf_ing.cn_etf_symbol(code)
    assert rules.code_to_symbol(code) == expected_symbol, f"{code} 与 cn_etf_symbol 口径分叉"


def test_convertible_bond_scope_limitation_documented() -> None:
    """**可转债适用范围声明**（范围外限制，显式钉住，不遮掩）。

    本仓当前仅做 A 股股票 + 场内基金，**无可转债定价/行情路径**（``grep 可转债``
    仅命中 ``data/etf.py:482`` 一个债基分类字符串）。故 :func:`market_prefix` 只按首字符
    粗判，对**沪市**可转债（``110xxx / 111xxx / 113xxx``）会误判为 ``sz``。

    本用例把该**已知限制**显式记录下来：
        - 当前行为 = ``sz``（错误但暂无路径踩到）；
        - **将来接入可转债时，必须**在 ``market_prefix`` 按 ``"11"`` 段特判为 ``"sh"``，
          并把本用例改为断言 ``"sh"``，否则会静默取错交易所行情。
    深市可转债（``123xxx / 127xxx / 128xxx``）判为 ``sz`` 恰好正确，一并钉住。
    """
    # ⚠️ 已知限制（沪市转债被粗判为 sz）—— 接入转债前必须修正
    assert rules.market_prefix("113044") == "sz", "沪市转债已知限制：当前粗判为 sz（见 docstring）"
    assert rules.market_prefix("110059") == "sz", "沪市转债已知限制：当前粗判为 sz（见 docstring）"
    # 深市转债判为 sz 恰好正确
    assert rules.market_prefix("128136") == "sz"
    assert rules.market_prefix("123108") == "sz"
