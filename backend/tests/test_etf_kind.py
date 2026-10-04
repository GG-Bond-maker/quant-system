# -*- coding: utf-8 -*-
"""按投资类型分派 ETF 数据：`not_applicable`（结构性不适用）+ 债券/存单持仓。

本文件守的是**一个核心区分**：

    `unavailable`     本该有、这次没取到 ⇒ **真降级**，卡片照常渲染并显示原因
    `not_applicable`  这类基金**永远不会有** ⇒ 前端**整张不渲染**（判据 10）

旧实现把后者说成了前者：货币 ETF 页面上写着「重仓股解析失败」「无法获取估值数据」
「跟踪标的不可得」，而真相是货币基金**本来就不持有股票**、**没有 PE/PB 这个概念**、
**不跟踪任何指数**。

## 实证（2026-10-05，真实上游）
* F10 `jjcc`（股票持仓）对 511880 返回
  ``var apidata={ content:"",arryear:[],curyear:2026};`` —— 上游**明确**说"没有"。
  旧实现把空串交给 ``pd.read_html(StringIO(""))``，抛的是
  ``lxml.etree.XMLSyntaxError: no text parsed from document``，被 ``except Exception``
  一律映射成「重仓股解析失败」⇒ **把「没有」说成了「失败」**（本文件 `test_*_empty_payload_*`
  与 `test_parse_f10_holdings_rejects_empty_html` 钉住这条边界）。
* F10 `zqcc`（债券持仓）对 511880 返回 ``26兴业银行CD119 3.64%``（同业存单）、
  对 511260 返回 ``26国债07 8.60%`` —— 货币/债券 ETF 的"重仓"是**真实存在**的，
  只是不在 `jjcc` 里。

## 为什么"债券代码前导零"要单独立一条
``019833`` 交给 pandas 推断会变成 ``19833`` —— 代码是**标识符不是数字**。
真实数据里债券代码大量以 0 开头，静默截断后页面上的代码**点不开也搜不到**。

## 全部用例不联网
数据层：``app.data.etf._request`` 是唯一 HTTP 出口，按 URL 子串打桩。
API 层：直接打桩各 ``fetch_*`` 数据函数，并**断言不该被调用的那些不被调用**。
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import etf as etf_api  # noqa: E402
from app.cache.redis_client import RedisClient  # noqa: E402
from app.data import etf as etf_mod  # noqa: E402
from app.main import app  # noqa: E402

_AUTH_HEADERS = {"Authorization": "Bearer aqp-dev-token-change-me"}


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例从干净缓存开始（`fetch_etf_profile` 走 `_cached_ok`，会跨用例串味）。"""
    etf_mod._cache.clear()
    etf_mod._KEY_LOCKS.clear()
    yield
    etf_mod._cache.clear()
    etf_mod._KEY_LOCKS.clear()


# ==================== 打桩工具 ====================

def _fake_http(routes: dict, calls: list | None = None):
    """按 URL 子串分发的 `_request` 替身；`routes` 的值可为常量或 callable。"""
    def _fake(method, url, *, params=None, json_body=None, encoding=None,
              retries=1, headers=None, cookies=None, timeout=None):
        if calls is not None:
            calls.append({"method": method, "url": url, "params": params})
        for key, val in routes.items():
            if key in url:
                return val(method, url, params) if callable(val) else val
        raise AssertionError(f"未预期的请求: {method} {url}")
    return _fake


def _boom(what: str):
    def _inner(*_a, **_k):
        raise AssertionError(f"不该调用 {what}（该类型结构性不适用）")
    return _inner


def _profile_html(target: str = "沪深300指数", fund_type: str = "指数型-股票",
                  fund_name: str = "沪深300ETF") -> str:
    """F10「基金概况」页的最小结构：三个 `<th>…</th><td>…</td>` 同页。"""
    return (
        "<html><body><table>"
        f"<tr><th>基金简称</th><td>{fund_name}</td></tr>"
        f"<tr><th>基金类型</th><td>{fund_type}</td></tr>"
        f"<tr><th>跟踪标的</th><td>{target}</td></tr>"
        "</table></body></html>"
    )


def _apidata(html: str) -> str:
    """F10 `FundArchivesDatas.aspx` 的响应外壳（与线上同形）。"""
    return 'var apidata={ content:"%s",arryear:[],curyear:2026};' % html


_BOND_TABLE = (
    "<table><thead><tr><th>序号</th><th>债券代码</th><th>债券名称</th>"
    "<th>占净值比例</th><th>持仓市值</th></tr></thead><tbody>"
    "<tr><td>1</td><td>019833</td><td>26国债07</td><td>8.60%</td><td>12345.67</td></tr>"
    "<tr><td>2</td><td>019547</td><td>26国债12</td><td>5.20%</td><td>7000.00</td></tr>"
    "</tbody></table>"
    "<label class=right>截止至：<font class=px12>2026-06-30</font></label>"
)

# 股票持仓表：列序与线上一致（占净值比例在第 7 列 = 下标 6，持仓市值下标 8）
_STOCK_TABLE = (
    "<table><thead><tr>"
    "<th>序号</th><th>股票代码</th><th>股票名称</th><th>最新价</th><th>涨跌幅</th>"
    "<th>相关资讯</th><th>占净值比例</th><th>持股数</th><th>持仓市值</th>"
    "</tr></thead><tbody>"
    "<tr><td>1</td><td>600519</td><td>贵州茅台</td><td>1500</td><td>1.20%</td>"
    "<td>财报</td><td>5.30%</td><td>10</td><td>20000.00</td></tr>"
    "</tbody></table>"
)


def _info(code: str = "511880", name: str = "银华日利ETF") -> dict:
    return {"code": code, "name": name, "country": "cn", "tracking_index": None}


def _kind(kind: str):
    """把 `_etf_kind` 的判定源钉成常量（避免 F10 网络 + 名称规则混进来）。"""
    return lambda code, name=None: kind


# ==================== 一、F10 画像：一次请求拿三个字段 ====================

def test_f10_cell_strips_tags_and_whitespace():
    html = "<table><tr><th>跟踪标的</th><td><a href=#>沪深300指数</a></td></tr></table>"
    assert etf_mod._f10_cell(html, "跟踪标的") == "沪深300指数"


@pytest.mark.parametrize("placeholder", ["-", "—", "--", "---", "", "   "])
def test_f10_cell_placeholder_is_none(placeholder):
    """F10 用 `-`/`—` 表示"没有" ⇒ 必须归成 None，不能当成一个字符串值。"""
    html = f"<table><tr><th>跟踪标的</th><td>{placeholder}</td></tr></table>"
    assert etf_mod._f10_cell(html, "跟踪标的") is None


def test_f10_cell_missing_keyword_is_none():
    assert etf_mod._f10_cell("<table></table>", "跟踪标的") is None


def test_fetch_f10_profile_reads_three_fields_in_one_request(monkeypatch):
    """跟踪标的 / 基金类型 / 基金简称 **同页同源**：只允许打一次网络。"""
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request", _fake_http({"jbgk_": _profile_html()}, calls))
    prof = etf_mod._fetch_f10_profile("510300")
    assert prof["tracking"] == {"kind": "index", "name": "沪深300指数"}
    assert prof["fund_type"] == "指数型-股票"
    assert prof["fund_name"] == "沪深300ETF"
    assert len(calls) == 1, "三个字段必须同页同源，不能各打一次网络"


def test_fetch_f10_profile_no_target_is_kind_none(monkeypatch):
    """F10 白纸黑字写「该基金无跟踪标的」⇒ `kind='none'`（**是事实，不是解析失败**）。"""
    monkeypatch.setattr(etf_mod, "_request", _fake_http({"jbgk_": _profile_html(
        target="该基金无跟踪标的", fund_type="货币型-普通货币", fund_name="银华日利ETF")}))
    prof = etf_mod._fetch_f10_profile("511880")
    assert prof["tracking"]["kind"] == "none"
    assert prof["tracking"]["name"] is None
    assert prof["fund_type"] == "货币型-普通货币"


def test_fetch_f10_profile_unmatched_page_returns_none(monkeypatch):
    """整页结构不匹配（改版 / 404）⇒ None，调用方判 `unknown` 且**不入缓存**。"""
    monkeypatch.setattr(etf_mod, "_request", _fake_http({"jbgk_": "<html>404</html>"}))
    assert etf_mod._fetch_f10_profile("999999") is None


def test_fetch_etf_profile_rejects_non_cn_code_without_network(monkeypatch):
    monkeypatch.setattr(etf_mod, "_request", _boom("_request"))
    assert etf_mod.fetch_etf_profile("1329.T") is None
    assert etf_mod.fetch_etf_profile("SPY") is None
    assert etf_mod.fetch_etf_profile("") is None


def test_fetch_etf_profile_does_not_cache_none(monkeypatch):
    """取不到（None）不得进缓存 —— 否则一次瞬时失败被钉死整个 24h TTL。"""
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request", _fake_http({"jbgk_": "<html>404</html>"}, calls))
    assert etf_mod.fetch_etf_profile("999999") is None
    assert etf_mod.fetch_etf_profile("999999") is None
    assert len(calls) == 2, "🔴 None 进了缓存 ⇒ 页面一整天都取不到画像"


def test_fetch_etf_profile_caches_ok_result(monkeypatch):
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_http({"jbgk_": _profile_html()}, calls))
    first = etf_mod.fetch_etf_profile("510300")
    second = etf_mod.fetch_etf_profile("510300")
    assert first == second
    assert len(calls) == 1, "ok 载荷必须缓存（24h）"


# ==================== 二、投资类型判定 ====================

@pytest.mark.parametrize("fund_type,expected", [
    ("货币型-普通货币", "货币型"),
    ("指数型-固收", "债券型"),
    ("债券型-长债", "债券型"),
    # ⚠️ 同时含「海外」与「股票」⇒ 规则顺序必须先命中「海外」，否则跨境 ETF 被判成股票型
    ("指数型-海外股票", "跨境型"),
    ("QDII-商品", "商品型"),
    ("指数型-股票", "股票型"),
])
def test_fund_kind_uses_authoritative_f10_type(monkeypatch, fund_type, expected):
    monkeypatch.setattr(etf_mod, "fetch_etf_profile",
                        lambda code: {"tracking": {"kind": "none", "name": None},
                                      "fund_type": fund_type, "fund_name": "X"})
    assert etf_mod.fetch_etf_fund_kind("510300") == expected


def test_fund_kind_falls_back_to_name_rules_when_type_absent(monkeypatch):
    """F10 取不到时才退回名称规则（并用 F10 给的简称，而不是目录名）。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_profile", lambda code: None)
    assert etf_mod.fetch_etf_fund_kind("511880", "银华日利ETF") == "货币型"
    assert etf_mod.fetch_etf_fund_kind("511260", "十年国债ETF") == "债券型"
    assert etf_mod.fetch_etf_fund_kind("518880", "黄金ETF华安") == "商品型"
    assert etf_mod.fetch_etf_fund_kind("510300", "沪深300ETF") == "股票型"


def test_fund_kind_prefers_f10_short_name_over_catalog_name(monkeypatch):
    """目录名对个别标的取不到（`name=None`）⇒ 必须用 F10 简称，否则退回"股票型"兜底。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_profile",
                        lambda code: {"tracking": {"kind": "none", "name": None},
                                      "fund_type": None, "fund_name": "十年国债ETF"})
    assert etf_mod.fetch_etf_fund_kind("511260", None) == "债券型"


# ==================== 三、空载荷 ≠ 解析失败（本文件的核心边界） ====================

def test_empty_payload_raises_upstream_empty(monkeypatch):
    monkeypatch.setattr(etf_mod, "_request", _fake_http({"FundArchivesDatas": _apidata("")}))
    with pytest.raises(etf_mod.UpstreamEmpty) as ei:
        etf_mod._fetch_f10_holdings_html("511880", "jjcc")
    assert "未披露" in str(ei.value)


def test_unparseable_payload_raises_runtime_error(monkeypatch):
    """结构变了（拿不到 `apidata` 外壳）才是**真失败** —— 不能与"没有"混为一谈。"""
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_http({"FundArchivesDatas": "<html>oops</html>"}))
    with pytest.raises(RuntimeError):
        etf_mod._fetch_f10_holdings_html("511880", "jjcc")


def test_parse_f10_holdings_never_classifies_emptiness():
    """解析器**不负责**判定"没有" —— 空串交给它只会得到解析异常。

    正确分工：``_fetch_f10_holdings_html`` 先判空载荷并抛 `UpstreamEmpty`，
    解析器只在**非空** HTML 上工作。本用例钉住这条分工：若哪天有人把判空挪进解析器
    （或让 `UpstreamEmpty` 从这里漏出来），两种语义就又会糊在一起。

    反向锚定：解析器对空串的反应**就是** ``pd.read_html("")`` 的反应 —— 也就是旧实现
    被 `except Exception` 误报成「重仓股解析失败」的那个异常（实测
    ``lxml.etree.XMLSyntaxError: no text parsed from document``）。
    """
    with pytest.raises(Exception) as ei:  # noqa: B017 - 这里正是要断言"抛的不是我们的语义异常"
        etf_mod._parse_f10_holdings("", 3, 4)
    assert not isinstance(ei.value, etf_mod.UpstreamEmpty), (
        "🔴 解析器不该负责判定「没有」—— 那是上游空载荷的语义")
    with pytest.raises(Exception) as pd_exc:  # noqa: B017
        pd.read_html(StringIO(""))
    assert type(pd_exc.value) is type(ei.value), (
        "解析器对空串的行为应等于 pandas 本身 —— 这正是不该由它判「没有」的原因")


# ==================== 四、持仓解析：列序 / 前导零 / 报告期 ====================

def test_parse_bond_holdings_keeps_leading_zero_code():
    got = etf_mod._parse_f10_holdings(_BOND_TABLE, ratio_col=3, mv_col=4)
    first = got["items"][0]
    assert first["code"] == "019833", "🔴 债券代码被当成数字，前导零丢了（代码是标识符）"
    assert first["name"] == "26国债07"
    assert first["ratio"] == pytest.approx(8.60)
    assert first["mv_yi"] == pytest.approx(1.23)   # 12345.67 万元 → 1.23 亿
    assert got["date"] == "2026-06-30"


def test_parse_bond_holdings_date_none_when_absent():
    """取不到报告期必须**如实为 None**，不能编造一个 `{year}-12-31`。"""
    html = _BOND_TABLE.split("<label")[0]
    got = etf_mod._parse_f10_holdings(html, 3, 4)
    assert got["date"] is None


def test_parse_bond_holdings_caps_at_ten():
    rows = "".join(
        f"<tr><td>{i}</td><td>{i:06d}</td><td>债{i}</td><td>1.00%</td><td>100</td></tr>"
        for i in range(1, 16))
    html = ("<table><thead><tr><th>序号</th><th>债券代码</th><th>债券名称</th>"
            "<th>占净值比例</th><th>持仓市值</th></tr></thead><tbody>"
            + rows + "</tbody></table>")
    got = etf_mod._parse_f10_holdings(html, 3, 4)
    assert len(got["items"]) == 10


def test_parse_holdings_raises_on_too_few_columns():
    """列数不够（源改版）是**真失败**，不得静默返回空 items。"""
    html = ("<table><thead><tr><th>序号</th><th>债券代码</th></tr></thead><tbody>"
            "<tr><td>1</td><td>019833</td></tr></tbody></table>")
    with pytest.raises(RuntimeError):
        etf_mod._parse_f10_holdings(html, 3, 4)


def test_fetch_etf_bond_holdings_uses_zqcc_with_bond_columns(monkeypatch):
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_http({"FundArchivesDatas": _apidata(_BOND_TABLE)}, calls))
    got = etf_mod.fetch_etf_bond_holdings("511260")
    assert calls[0]["params"]["type"] == "zqcc"
    assert got["items"][0]["name"] == "26国债07"


def test_fetch_etf_holdings_uses_jjcc_with_stock_columns(monkeypatch):
    """两套列序**不能写反**：股票表占净值比例在下标 6、市值下标 8；债券表是 3 / 4。"""
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_http({"FundArchivesDatas": _apidata(_STOCK_TABLE)}, calls))
    got = etf_mod.fetch_etf_holdings("510300")
    assert calls[0]["params"]["type"] == "jjcc"
    first = got["items"][0]
    assert first["code"] == "600519"
    assert first["ratio"] == pytest.approx(5.30)
    assert first["mv_yi"] == pytest.approx(2.00)   # 20000 万元 → 2 亿


def test_fetch_etf_holdings_empty_payload_is_upstream_empty(monkeypatch):
    """货币基金走 `jjcc` 时上游返回空载荷 —— 必须可被调用方识别成「没有」。"""
    monkeypatch.setattr(etf_mod, "_request", _fake_http({"FundArchivesDatas": _apidata("")}))
    with pytest.raises(etf_mod.UpstreamEmpty):
        etf_mod.fetch_etf_holdings("511880")


# ==================== 五、行业配置：空表 vs 结构错 ====================

def test_fetch_etf_industry_empty_df_is_upstream_empty(monkeypatch):
    """`df.empty` = 上游说"这只基金没有行业配置" ⇒ `UpstreamEmpty`（不是失败）。"""
    import akshare as ak
    monkeypatch.setattr(ak, "fund_portfolio_industry_allocation_em",
                        lambda symbol, date: pd.DataFrame())
    with pytest.raises(etf_mod.UpstreamEmpty):
        etf_mod.fetch_etf_industry("511880")


def test_fetch_etf_industry_missing_column_is_runtime_error(monkeypatch):
    """表结构变了（缺「行业类别」）才是真失败 —— 不能当成"没有"。"""
    import akshare as ak
    monkeypatch.setattr(ak, "fund_portfolio_industry_allocation_em",
                        lambda symbol, date: pd.DataFrame({"占净值比例": [1.0]}))
    with pytest.raises(RuntimeError):
        etf_mod.fetch_etf_industry("510300")


# ==================== 六、API 分派：持仓块 ====================

def test_holdings_block_money_market_uses_bond_instrument(monkeypatch):
    """货币 ETF 的"重仓"是**同业存单/债券**（走 `zqcc`），不是"解析失败的重仓股"。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("货币型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_bond_holdings", lambda code, year=None: {
        "date": "2026-06-30",
        "items": [{"code": "112600000", "name": "26兴业银行CD119",
                   "ratio": 3.64, "mv_yi": 1.0}],
    })
    monkeypatch.setattr(etf_mod, "fetch_etf_holdings", _boom("jjcc 股票持仓"))
    monkeypatch.setattr(etf_mod, "fetch_etf_industry", _boom("行业配置"))

    blk = etf_api._build_holdings_block("511880", "cn", _info())
    assert blk["status"] == "ok"
    assert blk["instrument"] == "bond"
    assert blk["industry"] is None, "债券/存单没有行业分类 ⇒ 不返回占位结构（判据 10）"
    assert blk["holdings"]["items"][0]["name"] == "26兴业银行CD119"


def test_holdings_block_empty_payload_is_not_applicable(monkeypatch):
    """🔴 核心：空载荷 ⇒ `not_applicable`，**不是** `degraded`。

    旧实现在这里写「重仓股解析失败」，让用户以为"源挂了、修修就有"。
    """
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("货币型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_bond_holdings",
                        lambda code, year=None: (_ for _ in ()).throw(
                            etf_mod.UpstreamEmpty("空载荷")))
    blk = etf_api._build_holdings_block("511880", "cn", _info())
    assert blk["status"] == "not_applicable"
    assert "未披露重仓债券" in blk["reason"]
    assert "解析失败" not in blk["reason"]


def test_holdings_block_commodity_reason_is_about_not_holding_stocks(monkeypatch):
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("商品型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_holdings",
                        lambda code, year=None: (_ for _ in ()).throw(
                            etf_mod.UpstreamEmpty("空载荷")))
    blk = etf_api._build_holdings_block("518880", "cn", _info("518880", "黄金ETF华安"))
    assert blk["status"] == "not_applicable"
    assert "不持有股票" in blk["reason"]


def test_holdings_block_real_parse_error_stays_degraded(monkeypatch):
    """真失败必须仍是 `degraded` 且保留「重仓股解析失败」—— 不能把真失败洗成"不适用"。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("股票型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_holdings",
                        lambda code, year=None: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(etf_mod, "fetch_etf_industry",
                        lambda code, year=None: {"date": "2026-06-30", "items": []})
    blk = etf_api._build_holdings_block("510300", "cn", _info("510300", "沪深300ETF"))
    assert blk["status"] == "degraded"
    assert blk["reason"] == "重仓股解析失败"


def test_holdings_block_industry_failure_does_not_break_holdings(monkeypatch):
    """行业取不到只让 `industry` 为 None，**不得**把持仓卡整体拖成降级。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("股票型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_holdings", lambda code, year=None: {
        "date": "2026-06-30",
        "items": [{"code": "600519", "name": "贵州茅台", "ratio": 5.3, "mv_yi": 2.0}],
    })
    monkeypatch.setattr(etf_mod, "fetch_etf_industry",
                        lambda code, year=None: (_ for _ in ()).throw(ValueError("boom")))
    blk = etf_api._build_holdings_block("510300", "cn", _info("510300", "沪深300ETF"))
    assert blk["status"] == "ok"
    assert blk["instrument"] == "stock"
    assert blk["industry"] is None


def test_holdings_block_overseas_is_unavailable_not_not_applicable(monkeypatch):
    """非中国境内 ETF 是"暂不支持"，**不是**"结构性不适用" —— 两者语义不同。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("跨境型"))
    blk = etf_api._build_holdings_block("SPY", "us", None)
    assert blk["status"] == "unavailable"


# ==================== 七、API 分派：跟踪块 ====================

def test_tracking_block_money_market_is_not_applicable(monkeypatch):
    """货币基金**不跟踪指数** —— 不是"识别失败"，也省掉一次基准解析。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("货币型"))
    monkeypatch.setattr(etf_mod, "resolve_etf_benchmark", _boom("基准解析"))
    blk = etf_api._build_tracking_block("511880", "cn", _info())
    assert blk["status"] == "not_applicable"
    assert "不跟踪指数" in blk["reason"]


def test_tracking_block_no_target_reason_never_says_识别失败(monkeypatch):
    """F10 明确声明无跟踪标的 ⇒ `not_applicable`，且措辞不得是"识别失败"。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("债券型"))
    monkeypatch.setattr(etf_mod, "resolve_etf_benchmark",
                        lambda *a, **k: {"status": "no_target", "code": None, "name": None,
                                         "market": None, "source": "f10"})
    blk = etf_api._build_tracking_block("511010", "cn", _info("511010", "国债ETF"))
    assert blk["status"] == "not_applicable"
    assert "识别失败" not in blk["reason"]
    assert "无跟踪标的" in blk["reason"]


def test_tracking_block_unresolved_keeps_f10_index_name(monkeypatch):
    """解析不到**行情** ≠ 不知道跟的是什么：F10 有指数名就如实带上。

    实测 511260（十年国债ETF）跟的是「上证10年期国债指数」。

    ⚠️ 措辞**不得臆断原因**：2026-10-05 用中证官方全名（``indexNameCnAll``）核实，
    `上证10年期国债指数` **就在**中证名录里（短名 `10年国债`、代码 `H11077`，
    官方全名与 F10 逐字相同）—— 只是现有名称匹配没命中。
    故 reason 只能说"**未匹配到**行情"，不能说"不在中证指数名录内"（过度断言）。
    """
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("债券型"))
    monkeypatch.setattr(etf_mod, "resolve_etf_benchmark",
                        lambda *a, **k: {"status": "unresolved", "code": None, "name": None,
                                         "market": None, "source": None})
    monkeypatch.setattr(etf_mod, "fetch_etf_tracking_target",
                        lambda code: {"kind": "index", "name": "上证10年期国债指数"})
    blk = etf_api._build_tracking_block("511260", "cn", _info("511260", "十年国债ETF"))
    assert blk["status"] == "unavailable"
    assert blk["benchmark_name"] == "上证10年期国债指数"
    assert blk["benchmark_code"] is None
    assert "上证10年期国债指数" in blk["reason"]
    # 🔴 不得把"我们没匹配上"说成"上游没有"（判据 55：硬编码的用户可见"结论" = 假陈述）
    assert "不在中证指数名录内" not in blk["reason"]


def test_tracking_block_unresolved_without_name_is_generic_unavailable(monkeypatch):
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("股票型"))
    monkeypatch.setattr(etf_mod, "resolve_etf_benchmark",
                        lambda *a, **k: {"status": "unresolved", "code": None, "name": None,
                                         "market": None, "source": None})
    monkeypatch.setattr(etf_mod, "fetch_etf_tracking_target",
                        lambda code: {"kind": "unknown", "name": None})
    blk = etf_api._build_tracking_block("510300", "cn", _info("510300", "沪深300ETF"))
    assert blk["status"] == "unavailable"
    assert blk["reason"] == "无法识别跟踪指数"


@pytest.mark.parametrize("kind,bench", [
    ("债券型", {"status": "unresolved", "code": None, "name": None,
                "market": None, "source": None}),
    ("债券型", {"status": "no_target", "code": None, "name": None,
                "market": None, "source": "f10"}),
    ("股票型", {"status": "unresolved", "code": None, "name": None,
                "market": None, "source": None}),
])
def test_tracking_reasons_never_generalize_to_bonds(monkeypatch, kind, bench):
    """🔴 回归：旧文案「货币/债券类基金不跟踪指数」把两类混为一谈。

    **债券 ETF 是跟踪指数的**（511260 → 上证10年期国债指数），所以任何**非货币型**
    的 reason 都不得出现「不跟踪指数」这种一般化表述 —— 该表述只对货币型成立。
    """
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind(kind))
    monkeypatch.setattr(etf_mod, "resolve_etf_benchmark", lambda *a, **k: bench)
    monkeypatch.setattr(etf_mod, "fetch_etf_tracking_target",
                        lambda code: {"kind": "unknown", "name": None})
    blk = etf_api._build_tracking_block("511260", "cn", _info("511260", "十年国债ETF"))
    assert "不跟踪指数" not in (blk.get("reason") or "")


# ==================== 八、API 分派：估值 / 产业链 ====================

@pytest.mark.parametrize("kind", ["货币型", "债券型", "商品型"])
def test_valuation_not_applicable_for_non_equity(monkeypatch, kind):
    """非权益类没有 PE/PB 这个概念 —— 且**不去打源**。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind(kind))
    monkeypatch.setattr(etf_mod, "fetch_etf_valuation_proxy", _boom("估值代理"))
    blk = etf_api._build_valuation_block("511880", _info())
    assert blk["status"] == "not_applicable"
    assert kind in blk["reason"]


def test_valuation_ok_for_equity(monkeypatch):
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("股票型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_valuation_proxy", lambda code, info=None: {
        "index_code": "000300", "index_name": "沪深300",
        "pe_ttm": 12.0, "pb": 1.5, "pe_percentile": 45.0, "pb_percentile": 55.0})
    blk = etf_api._build_valuation_block("510300", _info("510300", "沪深300ETF"))
    assert blk["status"] == "ok"
    assert blk["pe_percentile"] == pytest.approx(45.0)


def test_valuation_unavailable_keeps_reason(monkeypatch):
    """真取不到时仍是 `unavailable` 且带具体原因（前端要显示它，不是藏起来）。"""
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("股票型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_valuation_proxy", lambda code, info=None: None)
    blk = etf_api._build_valuation_block("510300", _info("510300", "沪深300ETF"))
    assert blk["status"] == "unavailable"
    assert blk["reason"] == "无法获取估值数据"


@pytest.mark.parametrize("kind", ["货币型", "债券型", "商品型"])
def test_chain_not_applicable_for_non_equity(kind):
    blk = etf_api._build_chain_block({"status": "ok", "industry": {"items": []}}, kind)
    assert blk["status"] == "not_applicable"
    assert "不持有股票" in blk["reason"]


def test_chain_unavailable_when_holdings_not_ok():
    """持仓块真降级 ⇒ 产业链也是 `unavailable`（有原因），不能变成"不适用"。"""
    blk = etf_api._build_chain_block({"status": "unavailable", "reason": "源超时"}, "股票型")
    assert blk["status"] == "unavailable"


# ==================== 九、端到端：货币 ETF 详情页 ====================

_FAKE_CATALOG = [
    {"code": "511880", "name": "银华日利ETF", "country": "cn", "exchange": "sh",
     "type": "货币型", "board": "货币型", "tracking_index": None, "manager": None,
     "inception": None, "price": 100.0, "pct": 0.01, "amount": 1e8, "size_yi": 1112.0,
     "quote_status": "ok", "overseas": None},
]

_BARS = [
    {"date": "2026-01-%02d" % i, "open": 100.0, "close": 100.0 + i * 0.001,
     "high": 100.1, "low": 99.9, "volume": 1000}
    for i in range(1, 26)
]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(etf_mod, "build_catalog", lambda: _FAKE_CATALOG)
    monkeypatch.setattr(etf_mod, "fetch_kline", lambda market, sym, limit=320: _BARS)
    monkeypatch.setattr(etf_mod, "fetch_etf_fee",
                        lambda code: {"management": 0.15, "custody": 0.05, "unit": "%/年"})
    monkeypatch.setattr(etf_mod, "fetch_etf_fund_kind", _kind("货币型"))
    monkeypatch.setattr(etf_mod, "fetch_etf_bond_holdings", lambda code, year=None: {
        "date": "2026-06-30",
        "items": [{"code": "112600000", "name": "26兴业银行CD119",
                   "ratio": 3.64, "mv_yi": 1.0}],
    })
    # 资金流给 ≥5 条 ⇒ `ok`（<5 条会被判 degraded，那是另一条契约，
    # 会把"整体是否降级"的判定污染掉）
    monkeypatch.setattr(etf_mod, "fetch_etf_flow_history",
                        lambda code, days=60: [{"date": "2026-01-%02d" % i,
                                                "net_inflow": 1e8} for i in range(1, 8)])
    monkeypatch.setattr(etf_mod, "fetch_etf_news", lambda code, limit=12: [
        {"date": "2026-08-29", "title": "银华日利ETF收益支付公告",
         "category": "分红送配", "url": None}])
    # 不该被调用的三个（结构性不适用 ⇒ 连源都不该打）
    monkeypatch.setattr(etf_mod, "fetch_etf_holdings", _boom("jjcc 股票持仓"))
    monkeypatch.setattr(etf_mod, "fetch_etf_industry", _boom("行业配置"))
    monkeypatch.setattr(etf_mod, "fetch_etf_valuation_proxy", _boom("估值代理"))
    monkeypatch.setattr(etf_mod, "resolve_etf_benchmark", _boom("基准解析"))

    async def _no_get(cls, key): return None
    async def _no_set(cls, *a, **k): return None
    monkeypatch.setattr(RedisClient, "get", classmethod(_no_get))
    monkeypatch.setattr(RedisClient, "set", classmethod(_no_set))
    with TestClient(app) as c:
        c.headers.update(_AUTH_HEADERS)
        yield c


def _data(c: TestClient, path: str) -> dict:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.fixture()
def client_empty(client, monkeypatch):
    """511880 的**真实形态**：F10 债券持仓也是空载荷（该基金未披露此类持仓）。

    `client` 夹具把 `fetch_etf_bond_holdings` 打桩成"有数据"（用来验 happy path）；
    这里再覆一层，验"上游明说没有"这条路 —— 它才是用户截图里那只 ETF 的实际情形。
    """
    def _empty(code, year=None):
        raise etf_mod.UpstreamEmpty("F10 zqcc 返回空载荷（该基金未披露此类持仓）")
    monkeypatch.setattr(etf_mod, "fetch_etf_bond_holdings", _empty)
    return client


def test_money_market_detail_marks_three_blocks_not_applicable(client):
    """货币 ETF：跟踪 / 估值 / 产业链三块 `not_applicable`，持仓是**债券**且为 ok。"""
    d = _data(client, "/api/v1/etf/detail/511880")
    blocks = d["blocks"]

    assert d["kind"] == "货币型"
    assert blocks["holdings"]["status"] == "ok"
    assert blocks["holdings"]["instrument"] == "bond"
    assert blocks["holdings"]["holdings"]["items"][0]["name"] == "26兴业银行CD119"

    for key in ("tracking", "valuation", "chain"):
        assert blocks[key]["status"] == "not_applicable", f"{key} 应判结构性不适用"
        assert blocks[key].get("reason"), f"{key} 必须给出人话原因"


def test_money_market_detail_empty_payload_is_not_applicable(client_empty):
    """🔴 端到端：F10 空载荷 ⇒ 持仓块 `not_applicable`，整页仍是 `ok`。

    这正是旧实现被说成「重仓股解析失败」的那条路径（511880 的 F10 `jjcc`/`zqcc` 都是空）。
    """
    d = _data(client_empty, "/api/v1/etf/detail/511880")
    blocks = d["blocks"]
    assert blocks["holdings"]["status"] == "not_applicable"
    assert "未披露重仓债券" in blocks["holdings"]["reason"]
    assert blocks["chain"]["status"] == "not_applicable"
    assert d["status"] == "ok", "空载荷（本来就没有）不是降级"


def test_money_market_detail_not_applicable_is_not_a_degradation(client):
    """🔴 `not_applicable` 不得把整页标成 degraded —— 否则货币 ETF 永远顶着"降级"标签。"""
    d = _data(client, "/api/v1/etf/detail/511880")
    assert d["status"] == "ok", "结构性不适用被当成了降级"


def test_money_market_detail_never_says_parse_failed(client):
    """🔴 回归：整页任何 `reason` 都不得出现「重仓股解析失败」这类**把"没有"说成"失败"**的措辞。"""
    d = _data(client, "/api/v1/etf/detail/511880")
    joined = " ".join(
        str(b.get("reason") or "") for b in d["blocks"].values() if isinstance(b, dict))
    assert "重仓股解析失败" not in joined
    assert "无法识别跟踪指数" not in joined
