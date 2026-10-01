"""
ETF 中心接口测试。

网络数据源全部用 monkeypatch 替换为假目录（测试不依赖外网、不依赖东财限流），
重点验证：
- 四国目录合并与国家筛选
- 筛选器各维度（类型 / 规模 / 成立日期 / 关键词）的 AND 语义
- 日韩标的行情为 null 时接口仍可用（quote_status=unavailable）
- overview 在无历史存档时 prev 为 null（不编造对比值）
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import orjson
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import etf as etf_api  # noqa: E402
from app.cache.redis_client import RedisClient  # noqa: E402
from app.core.errors import ERR_PARAMS  # noqa: E402
from app.data import etf as etf_mod  # noqa: E402
from app.main import app  # noqa: E402

# ETF 读端点自 T-03 B 组起要求 viewer 及以上角色（require_role("viewer")），
# 匿名调用会得到 40100。本文件全部是正常读取路径，统一用默认 ADMIN_TOKEN；
# conftest.py 已把 ADMIN_TOKEN 钉回该默认值（生产 .env 的随机 token 不参与测试）。
_AUTH_HEADERS = {"Authorization": "Bearer aqp-dev-token-change-me"}


def _item(code, country, **kw):
    base = {
        "code": code, "name": kw.pop("name", f"ETF-{code}"), "country": country,
        "exchange": None, "type": "股票型", "board": "行业ETF",
        "tracking_index": None, "manager": None, "inception": None,
        "price": 1.0, "pct": 0.5, "amount": 1e8, "size_yi": 10.0,
        "quote_status": "ok", "overseas": None,
    }
    base.update(kw)
    return base


FAKE_CATALOG = [
    _item("510300", "cn", name="沪深300ETF华泰柏瑞", board="宽基ETF", size_yi=800.0,
          tracking_index="沪深300", manager="华泰柏瑞", inception="2012-05-04"),
    _item("512480", "cn", name="半导体ETF", board="行业ETF", size_yi=200.0,
          tracking_index="中证全指半导体", manager="国联安", inception="2019-05-08"),
    _item("511880", "cn", name="银华日利ETF", type="货币型", board="货币型", size_yi=1112.0),
    _item("SPY", "us", name="标普500ETF-SPDR", board="跨境ETF", size_yi=40000.0,
          tracking_index="标普500", manager="State Street", inception="1993-01-22"),
    _item("1329.T", "jp", name="日经225ETF", board="跨境ETF",
          price=None, pct=None, amount=None, size_yi=None,
          tracking_index="日经225", manager="野村アセットマネジメント",
          inception="2001-07-13", quote_status="unavailable"),
    _item("069500.KS", "kr", name="KODEX 200 ETF", board="跨境ETF",
          price=None, pct=None, amount=None, size_yi=None,
          tracking_index="KOSPI 200", manager="삼성자산운용",
          inception="2002-10-14", quote_status="unavailable"),
]

FAKE_FLOW = [
    {"code": "510300", "name": "沪深300ETF华泰柏瑞", "pct": -0.26, "amount": 2.8e9,
     "net_inflow": 2.4e8, "inflow_ratio": 8.64},
    {"code": "512480", "name": "半导体ETF", "pct": 1.2, "amount": 1.0e9,
     "net_inflow": -1.5e8, "inflow_ratio": -1.5},
]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(etf_mod, "build_catalog", lambda: FAKE_CATALOG)
    monkeypatch.setattr(etf_mod, "fetch_flow", lambda period="1d", limit=20: FAKE_FLOW)
    # 关闭缓存：否则 overview 的存档历史会在用例之间串味
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
    b = r.json()
    assert b["code"] == 0, b
    return b["data"]


def test_overview_aggregates_all_countries(client):
    d = _data(client, "/api/v1/etf/overview")
    # P2-8 口径拆分：etf_count 只统计境内有真实行情的 cn ETF（3 只），
    # 美/日/韩目录条目单独在 overseas 块披露，不再混入合计
    assert d["today"]["etf_count"] == 3
    assert d["today"]["overseas"] == {
        "us_count": 1, "jp_count": 1, "kr_count": 1,
        "us_size_yi": pytest.approx(40000.0),
        "note": d["today"]["overseas"]["note"],
    }
    # 3 只有行情的标的：涨跌幅 0.5 / 0.5 / 0.5 -> 均值 0.5
    assert d["today"]["avg_pct"] == pytest.approx(0.5)
    # 净流入 2.4e8 - 1.5e8 = 0.9e8 元 = 0.9 亿
    assert d["today"]["net_inflow_yi"] == pytest.approx(0.9)
    # 首日无历史存档 -> prev 为 None（不编造「较昨日」）
    assert d["prev"] is None


def test_list_filter_by_country(client):
    assert _data(client, "/api/v1/etf/list?country=jp")["total"] == 1
    assert _data(client, "/api/v1/etf/list?country=kr")["total"] == 1
    assert _data(client, "/api/v1/etf/list?country=us")["total"] == 1
    assert _data(client, "/api/v1/etf/list?country=cn")["total"] == 3
    assert _data(client, "/api/v1/etf/list")["total"] == 6


def test_jp_kr_items_have_no_quote(client):
    d = _data(client, "/api/v1/etf/list?country=jp")
    it = d["items"][0]
    assert it["quote_status"] == "unavailable"
    assert it["price"] is None and it["pct"] is None
    # 但目录元信息（跟踪指数 / 管理公司 / 成立日期）仍在
    assert it["tracking_index"] == "日经225"
    assert it["inception"] == "2001-07-13"


def test_filter_type_and_size_are_anded(client):
    # 货币型 且 规模 >= 500 亿 -> 只有 511880
    d = _data(client, "/api/v1/etf/list?etype=货币型&min_size=500")
    assert [x["code"] for x in d["items"]] == ["511880"]
    # 货币型 且 规模 >= 2000 亿 -> 空
    assert _data(client, "/api/v1/etf/list?etype=货币型&min_size=2000")["total"] == 0


def test_filter_keyword_and_inception(client):
    d = _data(client, "/api/v1/etf/list?q=半导体")
    assert [x["code"] for x in d["items"]] == ["512480"]

    # 2010-01-01 ~ 2015-12-31 区间内只有 510300（2012-05-04）；
    # SPY(1993) / 1329.T(2001) / 069500.KS(2002) 均早于该区间，应被排除
    d = _data(client, "/api/v1/etf/list?inception_from=2010-01-01&inception_to=2015-12-31")
    assert [x["code"] for x in d["items"]] == ["510300"]


def test_filter_by_manager_and_index(client):
    d = _data(client, "/api/v1/etf/list?manager=State")
    assert [x["code"] for x in d["items"]] == ["SPY"]
    d = _data(client, "/api/v1/etf/list?index=沪深300")
    assert [x["code"] for x in d["items"]] == ["510300"]


def test_options_expose_selectable_values(client):
    opts = _data(client, "/api/v1/etf/list")["options"]
    assert "行业ETF" in opts["boards"]
    assert "货币型" in opts["types"]
    assert "标普500" in opts["indexes"]
    assert "State Street" in opts["managers"]


def test_hot_sorted_by_amount(client):
    d = _data(client, "/api/v1/etf/hot?limit=3")
    amounts = [x["amount"] or 0 for x in d["items"]]
    assert amounts == sorted(amounts, reverse=True)
    assert len(d["items"]) == 3


# ============ `/etf/hot` 排序基准（sort=amount|pct，2026-09-23 新增） ============
# 背景：热门榜原先写死「按成交额降序」，页面上的「按涨跌幅排序」诉求无法用同一接口
# 满足。新增 ``sort`` 查询参数：默认 ``amount``（向后兼容），``pct`` 按涨跌幅降序。
# 排序统一走 :func:`_sort_catalog_items`（缺失值在升/降序下都排**末尾**），
# 绝不回到 ``x.get(k) or 0`` 的老写法（会让 -1 哨兵混进真实值、冒充跌幅榜首）。
#
# 目录桩：OPS(低价中盘) / OBA(成交巨无霸) / OTP(涨幅王) 三者的
# **成交额序与涨跌幅序刻意不同**（金额序 OBA > OTP > OPS，涨跌幅序 OTP > OPS > OBA），
# 以此保证"排序基准真的生效"而不是碰巧巧合；NOQ 为日/韩条目，行情字段全 None。
_HOT_SORT_CATALOG = [
    _item("551100", "cn", name="OPS-低价中盘", pct=1.5, amount=1.0e7, size_yi=10.0),
    _item("511990", "cn", name="OBA-成交巨无霸", pct=-2.0, amount=9.0e8, size_yi=900.0),
    _item("513300", "cn", name="OTP-涨幅王", pct=3.25, amount=2.0e7, size_yi=20.0),
    _item("1329.T", "jp", name="NOQ-日经225", pct=None, amount=None, size_yi=None,
          price=None, quote_status="unavailable"),
    _item("069500.KS", "kr", name="NOQ-KODEX200", pct=None, amount=None, size_yi=None,
          price=None, quote_status="unavailable"),
]


@pytest.fixture()
def hot_client(client, monkeypatch):
    """把目录换成上面专门构造的那份（金额序与涨跌幅序相反），复用既有 client。"""
    monkeypatch.setattr(etf_mod, "build_catalog", lambda: list(_HOT_SORT_CATALOG))
    return client


def test_hot_sort_pct_orders_by_pct_desc_with_nulls_last(hot_client):
    """回归：``sort=pct`` 必须按涨跌幅**降序**，且 ``pct`` 缺失（日/韩无行情）恒排末尾。

    守住两件事：
      1. ``sort`` 参数**真的被用作排序基准** —— 期望序 OTP(3.25) > OPS(1.5) >
         OBA(-2.0) 与金额序（OBA > OTP > OPS）**完全不同**，若排序基准仍是 amount，
         第一条断言就红；
      2. ``pct`` 为 None 的日/韩两条必须落在列表**末尾** —— 若写回 ``x.get("pct") or 0``
         的旧兜底，它们会被当成 0 插到 OPS(1.5) 与 OBA(-2.0) 之间，末尾断言变红
         （这条同时守住"东财 -1 哨兵冒充跌幅榜首"的历史坑）。

    **变异验证**：删掉/改动 ``etf_hot`` 内
    ``items, sort_applied, _dir_applied = _sort_catalog_items(items, sort, "desc")``
    这一行（单一锚点，改回 ``items.sort(key=lambda x: x.get("amount") or 0,
    reverse=True)``）⇒ 本用例两条断言同时变红。
    """
    d = _data(hot_client, "/api/v1/etf/hot?limit=5&sort=pct")

    assert d["sort_applied"] == "pct", d
    codes = [x["code"] for x in d["items"]]
    assert codes == ["513300", "551100", "511990", "1329.T", "069500.KS"], codes

    pcts = [x["pct"] for x in d["items"]]
    head = [p for p in pcts if p is not None]
    assert head == sorted(head, reverse=True), head
    # 末尾必为缺失行情的条目，且其 pct 如实为 None（不补 0、不合成）
    assert pcts[-2:] == [None, None], pcts


def test_hot_default_sort_stays_amount_backward_compatible(hot_client):
    """回归：**不传 sort 时结果必须与改造前（恒按成交额降序）逐条一致**。

    这是向后兼容的核心守卫：前端既有调用（ETF 中心热门榜，以及由它派生的
    「ETF表现」默认标的）都依赖默认仍是最活跃的那批标的。做法是：本用例用
    **改造前那一行**的算法在本地独立算一遍期望序，再与实际响应逐条比对。

    注：两份实现在「成交额完全相等」这种平局上的相对次序存在差异
    （新实现先升序再翻转非 null 段），因此桩数据的成交额定为两两不相等；真实数据
    里成交额精确到元，平局概率极低，且 ``/list`` 早就用同一语义，不另行处置。

    **变异验证**：把 ``_ETF_DEFAULT_HOT_SORT`` 从 ``"amount"`` 改成 ``"pct"``
    （单一锚点）⇒ 默认响应变成涨跌幅序 ⇒ 本用例变红。
    """
    d = _data(hot_client, "/api/v1/etf/hot?limit=5")

    # 前端 / 历史调用方都在意的回显键：默认必须报 amount
    assert d["sort_applied"] == "amount", d

    legacy = sorted(list(_HOT_SORT_CATALOG),
                    key=lambda x: x.get("amount") or 0, reverse=True)
    expected = [x["code"] for x in legacy]
    actual = [x["code"] for x in d["items"]]
    assert actual == expected, (
        f"默认行为必须与改造前一致（amount 降序）；期望 {expected}，实际 {actual}")

    # 显式传 amount 与不传必须完全等价（同一份数据、同一代码路径）
    same = _data(hot_client, "/api/v1/etf/hot?limit=5&sort=amount")
    assert [x["code"] for x in same["items"]] == actual


def test_hot_rejects_sort_outside_whitelist(hot_client):
    """回归：``sort`` 只接受 ``amount|pct``，白名单外的值**不得静默当作别的榜**。

    定义（本实现）：FastAPI 的 ``pattern`` 先拦截 ⇒ 命中统一的
    ``RequestValidationError`` 处理器 ⇒ **HTTP 恒 200** + 业务码 ``ERR_PARAMS``(40000)，
    响应体仍是信封结构。这样前端既能拿到非零 code，又不会被 HTTP 4xx 打断统一取数。
    特别注意 ``size`` 恰在 ``/list`` 的白名单里 —— 若少了这层 pattern，它会被
    ``_sort_catalog_items`` 静默回落成默认键，用户以为在按成交量排，实际拿到规模榜。

    **变异验证**：删掉 ``etf_hot`` 签名里 ``pattern=r"^(amount|pct)$"``（单一锚点）
    ⇒ ``sort=size`` 变成合法输入 ⇒ 业务码回到 0 ⇒ 本用例变红。
    """
    for bad in ("size", "code", "", "AMOUNT", "pct "):
        r = hot_client.get(f"/api/v1/etf/hot?limit=5&sort={bad}")
        # 信封契约：HTTP 恒 200，业务错误体现在 code 上
        assert r.status_code == 200, f"sort={bad!r} 应落到 200 信封，实际 {r.status_code}"
        body = r.json()
        assert body["code"] == ERR_PARAMS, f"sort={bad!r} 应被参数校验拒绝，实际 {body}"
        # data 域是校验错误明细（非目录数据）：能定位到具体是哪个参数越界
        errs = body["data"] or []
        assert errs and any("sort" in e.get("loc", ()) for e in errs), body


# ============ `/etf/hot` 筛选联动（country/board/etype，2026-09-30 新增） ============
# 回归背景：`etf_hot` 此前**未声明** country/board/etype，而 FastAPI 会**静默丢弃**
# 未声明的 query 参数（不报错、不生效）⇒ 前端即使传 `country=us`，榜单也固定全市场。
# 下面用例锁死"传了就生效"，并锁死"不传 = 全市场"的向后兼容语义。


def test_hot_filters_by_country(client):
    """``country=us`` 只返回美股；``country=cn`` 只返回中国。"""
    us = _data(client, "/api/v1/etf/hot?limit=10&country=us")
    assert us["items"], "us 过滤后不应为空（FAKE_CATALOG 含 SPY）"
    assert {x["country"] for x in us["items"]} == {"us"}
    assert us["filters_applied"] == {"country": "us"}
    # total 也应是过滤后的总数（而非全市场总数），否则前端"共 N 只"会谎报
    assert us["total"] == 1

    cn = _data(client, "/api/v1/etf/hot?limit=10&country=cn")
    assert {x["country"] for x in cn["items"]} == {"cn"}
    assert cn["total"] == 3


def test_hot_filters_by_board(client):
    """``board=宽基ETF`` 不得混入债券 / 货币等其它板块。"""
    d = _data(client, "/api/v1/etf/hot?limit=10&board=宽基ETF")
    assert d["items"], "宽基过滤后不应为空"
    assert {x["board"] for x in d["items"]} == {"宽基ETF"}


def test_hot_filters_are_anded(client):
    """多条件之间是 AND（与 /list 同语义）。"""
    d = _data(client, "/api/v1/etf/hot?limit=10&country=cn&board=宽基ETF")
    assert {x["country"] for x in d["items"]} == {"cn"}
    assert {x["board"] for x in d["items"]} == {"宽基ETF"}
    assert d["filters_applied"] == {"country": "cn", "board": "宽基ETF"}
    # 交集应比单条件更窄
    only_cn = _data(client, "/api/v1/etf/hot?limit=10&country=cn")
    assert d["total"] <= only_cn["total"]


def test_hot_filter_all_is_noop(client):
    """``all``（或缺省）等价于不过滤 —— 锁死向后兼容：不传参 = 全市场榜单。

    **变异验证**：若把 ``country: str = Query("all", ...)`` 改成无默认值，
    不传参的请求会 422/参数错误 ⇒ 本用例变红。
    """
    base = _data(client, "/api/v1/etf/hot?limit=10")
    for q in ("?limit=10&country=all", "?limit=10&board=all&etype=all"):
        assert _data(client, "/api/v1/etf/hot" + q)["total"] == base["total"]
    # 全市场 = 四国目录总数（3 中国 + 美/日/韩各 1）
    assert base["total"] == len(FAKE_CATALOG)
    # 无过滤时 filters_applied 为空 dict（而非含一堆 "all"，便于前端判空）
    assert base["filters_applied"] == {}


def test_hot_filter_matching_nothing_returns_empty(client):
    """过滤到空集时返回 0 条（而非回落到全市场）—— 空态必须如实。

    ``511880`` 本身就是「货币型」，故不能用 ``board=货币型`` 构造空集；
    这里用互斥组合：中国 + 跨境ETF（FAKE_CATALOG 里跨境ETF 只有美/日/韩）。
    """
    d = _data(client, "/api/v1/etf/hot?limit=10&country=cn&board=跨境ETF")
    assert d["items"] == [] and d["total"] == 0
    assert d["truncated"] is False


def test_flow_endpoint(client):
    d = _data(client, "/api/v1/etf/flow?period=1d&limit=5")
    assert d["period"] == "1d"
    assert len(d["items"]) == 2
    assert d["items"][0]["net_inflow"] == pytest.approx(2.4e8)
    assert d["items"][0]["inflow_ratio"] == pytest.approx(8.64)


def test_hot_and_flow_disclose_truncation(client):
    """**R10 / P5-S12**：`/etf/hot` 与 `/etf/flow` 的截断必须披露。

    两个端点此前都是"取满 limit 就静默丢其余"（hot 是 `items[:limit]`，
    flow 在数据层 `data/etf.py:232` 处 `diff[:limit]`），响应里没有任何
    `total`/`truncated` 痕迹。修法不改形状（前端 `api/etf.ts:33,44` 消费
    `{items}`/`{period,items}`），只补齐披露键。
    """
    hot = _data(client, "/api/v1/etf/hot?limit=1")     # 目录 3 只中国 + 美/日/韩
    assert hot["limit"] == 1 and hot["returned"] == len(hot["items"]) == 1
    assert hot["total"] >= 2, hot
    assert hot["truncated"] is True, f"取满 limit 且有更多条目时必须报截断：{hot}"

    hot_all = _data(client, "/api/v1/etf/hot?limit=50")
    assert hot_all["truncated"] is False
    assert hot_all["returned"] == hot_all["total"]

    flow = _data(client, "/api/v1/etf/flow?period=1d&limit=1")
    # 桩函数不按 limit 切片（真实数据层在 `data/etf.py:232` 处切），故此处只钉
    # **披露语义**：returned 与 items 一致、truncated 取保守判据（达到上限即可能更多）。
    assert flow["returned"] == len(flow["items"]) == 2
    assert flow["limit"] == 1
    assert flow["truncated"] is (flow["returned"] >= flow["limit"])
    # 数据源不回传总量 ⇒ total 必须如实为 None（不得伪造一个数字）
    assert flow["total"] is None and flow.get("truncation_basis")

    flow_all = _data(client, "/api/v1/etf/flow?period=1d&limit=5")
    assert flow_all["truncated"] is False, flow_all


def test_performance_marks_unavailable_symbols(monkeypatch, client):
    """日韩代码无 K 线源时返回 status=unavailable 的空序列，不伪造曲线。"""
    monkeypatch.setattr(etf_mod, "fetch_kline",
                        lambda market, sym, limit=800: (
                            [{"date": "2026-01-01", "open": 1.0, "close": 1.0,
                              "high": 1.0, "low": 1.0, "volume": 1.0},
                             {"date": "2026-01-02", "open": 1.0, "close": 1.1,
                              "high": 1.1, "low": 1.0, "volume": 1.0}]
                            if sym == "510300" else []))
    d = _data(client, "/api/v1/etf/performance?symbols=510300,1329.T&period=1m")
    statuses = {s["code"]: s["status"] for s in d["series"]}
    assert statuses["510300"] == "ok"
    assert statuses["1329.T"] == "unavailable"
    ok = next(s for s in d["series"] if s["code"] == "510300")
    # 基准 1.0 -> 1.1，累计 +10%
    assert ok["points"][-1]["value"] == pytest.approx(10.0)


def test_detail_endpoint_structure(monkeypatch, client):
    """ETF 分析详情页：各 block 结构正确，缺失数据源时降级。"""
    bars = [
        {"date": "2026-01-0%d" % i, "open": 1.0, "close": 1.0 + i * 0.01,
         "high": 1.05 + i * 0.01, "low": 0.95 + i * 0.01, "volume": 1000}
        for i in range(1, 25)
    ]
    monkeypatch.setattr(etf_mod, "fetch_kline", lambda market, sym, limit=320: bars)
    monkeypatch.setattr(etf_mod, "fetch_index_kline", lambda market, sym, limit=320: bars)
    monkeypatch.setattr(etf_mod, "fetch_etf_fee",
                        lambda code: {"management": 0.15, "custody": 0.05, "unit": "%/年"})
    monkeypatch.setattr(etf_mod, "fetch_etf_holdings",
                        lambda code, year=None: {
                            "date": "2026-06-30",
                            "items": [{"code": "000001", "name": "平安银行", "ratio": 5.0, "mv_yi": 10.0}],
                        })
    monkeypatch.setattr(etf_mod, "fetch_etf_industry",
                        lambda code, year=None: {
                            "date": "2026-06-30",
                            "items": [{"industry": "金融", "ratio": 30.0, "mv_yi": 100.0}],
                        })
    monkeypatch.setattr(etf_mod, "fetch_etf_valuation_proxy",
                        lambda code: {"index_code": "000300", "index_name": "沪深300",
                                      "pe_ttm": 12.0, "pb": 1.5,
                                      "pe_percentile": 45.0, "pb_percentile": 55.0})
    monkeypatch.setattr(etf_mod, "fetch_etf_flow_history",
                        lambda code, days=60: [{"date": "2026-01-24", "net_inflow": 1e8}])
    monkeypatch.setattr(etf_mod, "fetch_etf_news", lambda code, limit=12: [
        {"date": "2026-08-29", "title": "华泰柏瑞沪深300ETF分红公告",
         "category": "分红送配", "url": None},
        {"date": "2026-08-13", "title": "基金合同终止风险提示公告",
         "category": "基金公告", "url": None},
    ])

    d = _data(client, "/api/v1/etf/detail/510300?kline_period=day")
    assert d["code"] == "510300"
    assert d["country"] == "cn"
    blocks = d["blocks"]
    for k in ("header", "kline", "holdings", "tracking", "valuation", "flow",
              "news", "sentiment", "chain"):
        assert k in blocks

    assert blocks["header"]["status"] == "ok"
    assert blocks["header"]["management_fee"] == pytest.approx(0.15)
    assert blocks["header"]["tracking_index"] == "沪深300"

    assert blocks["kline"]["status"] == "ok"
    assert blocks["kline"]["period"] == "day"
    assert len(blocks["kline"]["bars"]) == len(bars)

    assert blocks["holdings"]["status"] == "ok"
    assert blocks["holdings"]["holdings"]["items"][0]["code"] == "000001"
    assert blocks["holdings"]["industry"]["items"][0]["industry"] == "金融"

    assert blocks["tracking"]["status"] == "ok"
    assert blocks["tracking"]["benchmark_code"] == "000300"
    assert "tracking_error" in blocks["tracking"]

    assert blocks["valuation"]["status"] == "ok"
    assert blocks["valuation"]["pe_percentile"] == pytest.approx(45.0)

    assert blocks["flow"]["status"] == "degraded"  # 只有 1 条历史

    # 新闻 / 情感 / 产业链（sentiment 与 chain 由 news / holdings 纯函数派生）
    assert blocks["news"]["status"] == "ok"
    assert len(blocks["news"]["items"]) == 2

    assert blocks["sentiment"]["status"] == "ok"
    # 正面 1 词（分红）/ 负面 2 词（终止、风险）-> 1/3
    assert blocks["sentiment"]["score"] == pytest.approx(33.3, abs=0.1)
    assert blocks["sentiment"]["label"] == "偏谨慎"

    assert blocks["chain"]["status"] == "ok"
    assert blocks["chain"]["items"][0]["chain"] == "金融"
    assert blocks["chain"]["items"][0]["ratio"] == pytest.approx(30.0)


def test_detail_jp_etf_degrades_gracefully(monkeypatch, client):
    """日本本土 ETF 无行情源时，详情页整体可用但各块降级。"""
    d = _data(client, "/api/v1/etf/detail/1329.T")
    assert d["country"] == "jp"
    blocks = d["blocks"]
    assert blocks["header"]["status"] == "ok"
    assert blocks["kline"]["status"] == "unavailable"
    assert blocks["holdings"]["status"] == "unavailable"


# ==================== 新闻 / 情感 / 产业链（纯函数） ====================
def test_score_sentiment_counts_and_label():
    """情感打分：正面/负面词计数正确，区间与标签一致。"""
    # 注意「创新高」会同时命中「创新」与「新高」，故用「上涨」避免重复计数
    s = etf_mod.score_sentiment(["净值上涨", "存在回撤风险", "常规运作公告"])
    assert s["positive"] == 1      # 上涨
    assert s["negative"] == 2      # 回撤 / 风险
    assert s["score"] == pytest.approx(100 / 3, abs=0.1)
    assert s["label"] == "偏谨慎"


def test_score_sentiment_neutral_when_no_hit():
    """没有任何情感词命中时返回中性 50 分，不编造倾向。"""
    s = etf_mod.score_sentiment(["2026 年第二季度报告"])
    assert s["positive"] == 0 and s["negative"] == 0
    assert s["score"] == 50.0
    assert s["label"] == "中性"


def test_build_industry_chain_aggregates_and_sorts():
    """产业链归集：同大类合并、按权重降序、未知行业落「其他」。"""
    items = [
        {"industry": "银行", "ratio": 20.0},
        {"industry": "证券", "ratio": 10.0},
        {"industry": "半导体", "ratio": 15.0},
        {"industry": "某某细分", "ratio": 5.0},
    ]
    out = etf_mod.build_industry_chain(items)
    assert out[0] == {"chain": "金融", "ratio": 30.0}
    assert out[1] == {"chain": "信息技术", "ratio": 15.0}
    assert out[-1] == {"chain": "其他", "ratio": 5.0}


def test_build_catalog_survives_us_quote_failure(monkeypatch):
    """美股行情外部源失败时，ETF 目录仍必须可用（境内条目照常返回）。

    回归背景（2026-09-11 审核）：build_catalog 直接调用 _tencent_us_batch，
    qt.gtimg.cn 超时会抛 RuntimeError，把整个 /etf/overview 打成 500 ——
    一个附加信息源不该拖垮境内 ETF 主流程。
    """
    monkeypatch.setattr(etf_mod, "fetch_cn_etfs", lambda: [{
        "code": "510300", "name": "沪深300ETF", "price": 4.12, "pct": 0.35,
        "amount": 1.2e8, "float_mv": 5.3e10,
    }])

    def _boom(codes):  # noqa: ANN001
        raise RuntimeError("外部数据源请求失败: ConnectTimeout")

    monkeypatch.setattr(etf_mod, "_tencent_us_batch", _boom)

    cat = etf_mod.build_catalog()  # 不应抛异常
    cn = [x for x in cat if x["country"] == "cn"]
    us = [x for x in cat if x["country"] == "us"]
    assert len(cn) == 1 and cn[0]["code"] == "510300", "境内 ETF 条目丢失"
    assert us, "美股静态目录应保留"
    # 行情不可用时如实标注，不得编造价格
    assert all(x["quote_status"] == "unavailable" for x in us)
    assert all(x["price"] is None for x in us)


def test_build_industry_chain_csrc_gate_beats_sub_keyword():
    """证监会门类优先：含「制造」的「制造业」不能被错归到「高端制造」。"""
    out = etf_mod.build_industry_chain([
        {"industry": "制造业", "ratio": 61.43},
        {"industry": "信息传输、软件和信息技术服务业", "ratio": 5.14},
        {"industry": "采矿业", "ratio": 4.03},
    ])
    by_name = {x["chain"]: x["ratio"] for x in out}
    assert by_name["制造业"] == pytest.approx(61.43)
    assert by_name["信息技术"] == pytest.approx(5.14)
    assert by_name["资源能源"] == pytest.approx(4.03)
    assert "高端制造" not in by_name


# ============ 新浪兜底源：翻页终止 / 截断防护（2026-09-23 修复静默截断） ============
# 背景：旧实现把「任一页不满 100 条（含**空页**）即当末页 break」。新浪限流时
# 中间页会整页返回空数组，于是目录被静默截断到 ~200 条，而页面与全量统计均无差别。
# 以下用例固化新语义：空页 ≠ 末页（要重试）；部分页才是权威末页；低于下限必须抛错。
import threading  # noqa: E402


class _FakeResp:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


class _FakeSinaClient:
    """模拟新浪 getHQNodeData：按页码返回预置页，并记录每页被请求的次数。"""

    def __init__(self, pages):
        self._pages = pages
        self.calls: dict[int, int] = {}
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, **kwargs):
        page = (params or {}).get("page")
        with self._lock:
            self.calls[page] = self.calls.get(page, 0) + 1
        return _FakeResp(self._pages.get(page, []))


def _sina_rows(n, offset):
    return [
        {"code": str(600000 + offset + i), "name": f"ETF-{offset + i}",
         "trade": "1.2300", "changepercent": "0.50", "amount": "1000000",
         "mktcap": "20000", "nmc": "10000"}
        for i in range(n)
    ]


def _install_fake_sina(monkeypatch, pages) -> _FakeSinaClient:
    """把 etf 模块的 httpx.Client 换成假客户端，并消除规模增强与退避等待。"""
    fake = _FakeSinaClient(pages)
    monkeypatch.setattr(etf_mod.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(etf_mod, "_enrich_size_from_tencent", lambda rows: None)
    monkeypatch.setattr(etf_mod, "_SINA_EMPTY_BACKOFF", 0.0)
    return fake


def test_sina_paging_stops_at_partial_last_page(monkeypatch):
    """正常目录：16 个满页 + 1 个 79 条的部分页 ⇒ 取全 1679 条，且不越过末页再翻一批。"""
    pages = {p: _sina_rows(100, p * 100) for p in range(1, 17)}
    pages[17] = _sina_rows(79, 1700)
    fake = _install_fake_sina(monkeypatch, pages)

    out = etf_mod._fetch_sina_cn_etfs()
    assert len(out) == 16 * 100 + 79 == 1679
    # 部分页出现后立即停止：不应再发起第 4 批（页 25+）
    assert 25 not in fake.calls


def test_sina_empty_middle_page_is_not_last_page(monkeypatch):
    """中间某页返回空数组 ≠ 末页：必须继续翻页，且该空页要被重试。"""
    pages = {p: _sina_rows(100, p * 100) for p in range(1, 18)}
    pages[4] = []                                   # 第 4 页被限流：整页空
    pages[18] = _sina_rows(10, 1800)                # 真实末页（部分页）
    fake = _install_fake_sina(monkeypatch, pages)

    out = etf_mod._fetch_sina_cn_etfs()
    # 17 个满页中第 4 页为空 ⇒ 实得 16 个满页 + 末页 10 条
    assert len(out) == 16 * 100 + 10 == 1610
    assert 5 in fake.calls, "空页之后的页必须继续抓取（空页不得当末页）"
    # 空页必须被重试（首次 + _SINA_EMPTY_RETRIES 次）
    assert fake.calls[4] == etf_mod._SINA_EMPTY_RETRIES + 1


def test_sina_throttled_catalog_raises_instead_of_truncating(monkeypatch):
    """限流截断（第 3 页起整页空）⇒ 必须抛 DataSourceUnavailable，不得静默返回 ~200 条。"""
    pages = {1: _sina_rows(100, 100), 2: _sina_rows(100, 200)}
    fake = _install_fake_sina(monkeypatch, pages)

    with pytest.raises(etf_mod.DataSourceUnavailable):
        etf_mod._fetch_sina_cn_etfs()
    # 确实发生过重试（不是一遇空页就当成末页而只请求一次）
    assert fake.calls.get(3, 0) >= etf_mod._SINA_EMPTY_RETRIES + 1


def test_overview_prev_takes_latest_past_snapshot(monkeypatch):
    """`prev` 必须取**日期最大**的过去存档，而不是 history 里第一条（最早的）。

    history 在 :func:`_overview_with_prev` 内按日期**升序**存储，旧实现用
    ``next((h for h in history if h["date"] < today), None)`` 会命中最早一条，
    使「较上一期」对比跨越任意多天（实测曾取到 9 天前的 09-11 而非最近的 09-14）。
    此处构造多条过去存档（升序），断言取到最大日期的那条。

    依赖隔离：``_overview_snapshot``（会打外部源）与 ``RedisClient.get/set``
    全部 monkeypatch 为内存桩，**零外网**。
    """
    today = date.today()
    # 升序存储：最早 -> 最近（全部 < today）
    oldest = (today - timedelta(days=12)).isoformat()
    middle = (today - timedelta(days=10)).isoformat()
    latest = (today - timedelta(days=9)).isoformat()
    history = [
        {"date": oldest, "etf_count": 1348, "total_size_yi": 111.0},
        {"date": middle, "etf_count": 1500, "total_size_yi": 222.0},
        {"date": latest, "etf_count": 1600, "total_size_yi": 333.0},
    ]

    snap = {
        "etf_count": 1679, "total_size_yi": 50078.8, "avg_pct": 0.0,
        "net_inflow_yi": 0.0, "amount_yi": 0.0,
        "overseas": {"us_count": 0, "jp_count": 0, "kr_count": 0,
                     "us_size_yi": 0.0, "note": ""},
    }
    monkeypatch.setattr(etf_api, "_overview_snapshot", lambda: dict(snap))

    async def _fake_get(cls, key):
        return orjson.dumps(history)

    async def _fake_set(cls, *a, **k):
        return None

    monkeypatch.setattr(etf_api.RedisClient, "get", classmethod(_fake_get))
    monkeypatch.setattr(etf_api.RedisClient, "set", classmethod(_fake_set))

    res = asyncio.run(etf_api._overview_with_prev())

    assert res["prev"] is not None, "存在过去存档时 prev 不应为 None"
    assert res["prev"]["date"] == latest, (
        f"prev 必须取日期最大的过去存档 {latest}，实际取到 {res['prev']['date']}"
        "（next() 会命中最早的存档）"
    )
    assert res["prev"]["etf_count"] == 1600
    # 当日快照如实取 today，不被历史污染
    assert res["today"]["date"] == today.isoformat()
    assert res["today"]["etf_count"] == 1679


# ============ Task A：概览降级不自锁缓存（无预算后台重建） ============
def test_overview_degraded_payload_not_locked_in_main_cache(monkeypatch):
    """回归：冷路径降级载荷**不得**占据 300s 主缓存。

    背景（2026-09-23）：``_cached_etf_payload._build`` 原先在预算内超时/异常时
    **原样返回**无顶层 ``status`` 的 fallback，被 ``cached_or_build`` 当成正常结果
    按 300s TTL 落地 ⇒ 一次外部源抖动（冷路径实测 6.22s > 4.5s 预算，100% 触发）
    就让概览降级**自锁整整 5 分钟**。修复后降级载荷经 ``_mark_payload_degraded``
    补齐顶层 ``status=degraded`` ⇒ swr 的 ``_effective_ttl`` 收敛为
    ≤ ``DEGRADED_TTL_SECONDS``(15s) 且不写影子键。

    **变异验证（见交付说明）**：删掉 ``_cached_etf_payload`` 内
    ``_mark_payload_degraded(data)`` 这一行（单一锚点），降级载荷即无顶层 status
    ⇒ 按 300s 落地 ⇒ 本用例断言 ``all(ex <= DEGRADED_TTL_SECONDS)`` 变红。
    """
    from app.cache import swr

    # 预算压到极小 + 构建必然超时 ⇒ 冷路径 100% 降级
    monkeypatch.setattr(etf_api, "_ETF_ENDPOINT_BUDGET_SECONDS", 0.01)

    async def _slow_build() -> dict:
        await asyncio.sleep(0.3)
        return {"today": {"etf_count": 1}, "prev": None}

    monkeypatch.setattr(etf_api, "_overview_with_prev", _slow_build)

    writes: list[dict] = []

    async def _cap_set(cls, key, value, ex=3600, stale_ex=None):
        writes.append({"key": key, "ex": ex, "stale_ex": stale_ex})

    async def _no_get(cls, key):
        return None

    monkeypatch.setattr(etf_api.RedisClient, "set", classmethod(_cap_set))
    monkeypatch.setattr(etf_api.RedisClient, "get", classmethod(_no_get))

    # 记录「冷路径降级是否触发无预算后台重建」。**不真正创建任务**：本用例只验证主键
    # TTL；真实 ``_spawn_rebuild`` 会在 asyncio.run 的循环里留下孤儿任务，污染后续用例
    # （swr._bg_tasks 是模块级全局，``_wait_bg_done`` 会因此超时）。
    spawned: list[str] = []
    monkeypatch.setattr(etf_api, "_spawn_etf_rebuild",
                        lambda key, build, ttl: spawned.append(key))

    resp = asyncio.run(etf_api.etf_overview(_user={"role": "viewer"}))
    data = resp.data

    # 降级：today 为空态且携带可读原因（诚实降级语义保留）
    assert data["today"]["status"] == "unavailable"
    assert data["today"]["reason"], "降级必须给出可读原因"

    # 关键断言：主键回写 TTL 必须被收敛（≤15s），绝不能是 300s
    key = etf_api.k_etf_overview(etf_api._etf_data_date())
    main_writes = [w for w in writes if w["key"] == key]
    assert main_writes, "降级载荷也应有一次主键回写（短 TTL）"
    assert all(w["ex"] <= swr.DEGRADED_TTL_SECONDS for w in main_writes), (
        "降级载荷不得以 300s TTL 自锁主缓存；实际 TTL="
        f"{[w['ex'] for w in main_writes]}"
    )
    # 降级不留影子键（不供旧值）
    assert all(w["stale_ex"] is None for w in main_writes)
    # 冷路径降级必须触发无预算后台重建（缓存自愈的唯一路径）
    assert spawned and spawned[0] == key, "冷路径降级未触发无预算后台重建"


def test_etf_cached_payload_provides_background_build(monkeypatch):
    """契约：``_cached_etf_payload`` 必须向 swr 传 ``background_build``（无预算重建）。

    反证：若后台重建继承请求预算，慢构建（>4.5s）每轮只会写回降级载荷 ⇒ 缓存永不
    自愈（datacenter/market 的 ``background_build`` 先例）。此处直接检查传参。
    """
    captured: dict = {}

    async def _spy(key, build, **kw):
        captured.update(kw)
        return {"ok": True}

    monkeypatch.setattr(etf_api, "cached_or_build", _spy)

    async def _fast() -> dict:
        return {"ok": True}

    def _fb(reason: str) -> dict:
        return {"status": "degraded", "reason": reason}

    asyncio.run(etf_api._cached_etf_payload("k:test", _fast, _fb))
    assert callable(captured.get("background_build")), "必须提供无预算后台重建 builder"


# ============ Task B：口径披露（source + count_comparable） ============
def _snap_with_source(source: str, count: int) -> dict:
    return {
        "source": source, "etf_count": count, "total_size_yi": 50078.0,
        "avg_pct": 0.0, "net_inflow_yi": 0.0, "amount_yi": 0.0,
        "overseas": {"us_count": 0, "jp_count": 0, "kr_count": 0,
                     "us_size_yi": 0.0, "note": ""},
    }


def _stub_history(monkeypatch, history: list[dict]) -> None:
    async def _fake_get(cls, key):
        return orjson.dumps(history)

    async def _fake_set(cls, *a, **k):
        return None

    monkeypatch.setattr(etf_api.RedisClient, "get", classmethod(_fake_get))
    monkeypatch.setattr(etf_api.RedisClient, "set", classmethod(_fake_set))


def test_overview_discloses_source_and_marks_incomparable(monkeypatch):
    """数据源切换（eastmoney → sina+tencent）⇒ count_comparable=false + note，且**保留 delta**。"""
    today = date.today()
    prev_date = (today - timedelta(days=1)).isoformat()
    _stub_history(monkeypatch, [{
        "date": prev_date, "etf_count": 1337, "total_size_yi": 40000.0,
        "source": "eastmoney",
    }])
    monkeypatch.setattr(etf_api, "_overview_snapshot",
                        lambda: _snap_with_source("sina+tencent", 1679))

    res = asyncio.run(etf_api._overview_with_prev())

    assert res["today"]["source"] == "sina+tencent"
    assert res["prev"]["source"] == "eastmoney"
    assert res["count_comparable"] is False
    assert res["comparison_note"] and "不同" in res["comparison_note"]
    # delta 仍保留（不删数据）：前后快照都在，前端可自行计算差值
    assert res["prev"]["etf_count"] == 1337 and res["today"]["etf_count"] == 1679


def test_overview_same_source_is_comparable(monkeypatch):
    """同一数据源 ⇒ count_comparable=true、note 为 None。"""
    today = date.today()
    prev_date = (today - timedelta(days=1)).isoformat()
    _stub_history(monkeypatch, [{
        "date": prev_date, "etf_count": 1650, "total_size_yi": 49000.0,
        "source": "sina+tencent",
    }])
    monkeypatch.setattr(etf_api, "_overview_snapshot",
                        lambda: _snap_with_source("sina+tencent", 1679))

    res = asyncio.run(etf_api._overview_with_prev())
    assert res["count_comparable"] is True
    assert res["comparison_note"] is None


def test_overview_legacy_snapshot_without_source_is_unknown(monkeypatch):
    """容忍老存档缺 source 字段 → unknown ⇒ 判不可比（不崩溃、不推断）。"""
    today = date.today()
    prev_date = (today - timedelta(days=1)).isoformat()
    _stub_history(monkeypatch, [{
        "date": prev_date, "etf_count": 1348, "total_size_yi": 111.0,
    }])  # 无 source（模拟历史存档）
    monkeypatch.setattr(etf_api, "_overview_snapshot",
                        lambda: _snap_with_source("sina+tencent", 1679))

    res = asyncio.run(etf_api._overview_with_prev())
    assert res["prev"]["source"] == "unknown"
    assert res["count_comparable"] is False
    assert res["comparison_note"]


def test_overview_snapshot_carries_catalog_source(monkeypatch, client):
    """``_overview_snapshot`` 必须记录目录实际来源（口径披露数据源）。"""
    monkeypatch.setattr(etf_mod, "cn_etf_source", lambda: "sina+tencent")
    snap = etf_api._overview_snapshot()
    assert snap["source"] == "sina+tencent"


# ============ Task C：资金流原因披露（结构化 unavailable） ============
def test_flow_unavailable_returns_structured_reason(monkeypatch, client):
    """数据源不可用时 /etf/flow 返回 HTTP 200 + status=unavailable + 可读 reason，
    而不是裸抛 51000（前端据此显示「数据源不可用 + 原因」而非「暂无数据」）。"""
    def _boom(period="1d", limit=20):
        raise etf_mod.DataSourceUnavailable("push2delay 不可达")

    monkeypatch.setattr(etf_mod, "fetch_flow", _boom)
    d = _data(client, "/api/v1/etf/flow?period=1d&limit=10")
    assert d["status"] == "unavailable"
    assert d["items"] == []
    assert d["reason"] and "替代源" in d["reason"], d["reason"]
    assert d["data_freshness"]["status"] == "degraded"
    assert d["returned"] == 0 and d["truncated"] is False

    # 正常路径：status=ok（新增键不改形状）
    monkeypatch.setattr(etf_mod, "fetch_flow", lambda period="1d", limit=20: FAKE_FLOW)
    d2 = _data(client, "/api/v1/etf/flow?period=1d&limit=5")
    assert d2["status"] == "ok" and len(d2["items"]) == 2


# ============ Task A：ETF overview 接入启动预热（命中请求路径缓存） ============
def test_warm_etf_overview_writes_request_path_cache(monkeypatch):
    """回归①：预热必须写入**请求路径同一** SWR 缓存键且为 fresh 载荷。

    背景（2026-09-23）：ETF 概览冷路径实测 6.22s > 请求预算 4.5s，无预热时**首个**
    用户请求必然降级（前端黄条）。修法是把慢构建挪到后台并写入 ``k_etf_overview``
    （与 ``etf_overview`` 端点**完全同键**）——否则只热了进程内 ``build_catalog``
    缓存、请求路径仍走冷 SWR。本用例以「键不存在（ttl=-2）」强制重建，钉死：
      - 回写键 == ``k_etf_overview(_etf_data_date())``；
      - 回写 TTL == ``_ETF_CACHE_TTL``(300) / stale_window == ``_ETF_STALE_WINDOW``(1800)；
      - 载荷是 fresh（顶层无 ``status=degraded``，``data_freshness.status == fresh``）。

    **变异验证（见交付说明）**：删掉 ``warm_etf_overview_cache`` 内
    ``await swr.write_cache(key, data, _ETF_CACHE_TTL, _ETF_STALE_WINDOW)`` 这一行
    （单一锚点）⇒ 无任何回写 ⇒ 本用例 ``written`` 断言变红。
    """
    from app.cache import swr

    async def _absent_ttl(cls, key):
        return -2  # 键不存在 -> 必须重建

    monkeypatch.setattr(etf_api.RedisClient, "ttl", classmethod(_absent_ttl))

    async def _fake_build() -> dict:
        return {"today": {"etf_count": 1679}, "prev": None,
                "count_comparable": True, "comparison_note": None}

    monkeypatch.setattr(etf_api, "_overview_with_prev", _fake_build)

    captured: list[dict] = []

    async def _cap_write(key, data, ttl, stale_window=0, **kw):
        captured.append({"key": key, "data": data, "ttl": ttl,
                         "stale_window": stale_window})

    monkeypatch.setattr(swr, "write_cache", _cap_write)

    built = asyncio.run(etf_api.warm_etf_overview_cache())

    assert built is True, "冷缓存（ttl=-2）时预热必须真正构建并回写"
    key = etf_api.k_etf_overview(etf_api._etf_data_date())
    written = [c for c in captured if c["key"] == key]
    assert written, "预热必须写入请求路径同一键 k_etf_overview(<date>)"
    assert written[0]["ttl"] == etf_api._ETF_CACHE_TTL == 300
    assert written[0]["stale_window"] == etf_api._ETF_STALE_WINDOW == 1800
    # 预热是 fresh 载荷：顶层绝不能带 degraded（否则会自我短命化）
    assert written[0]["data"].get("status") != "degraded"
    assert written[0]["data"]["data_freshness"]["status"] == "fresh"


def test_warm_etf_overview_skips_when_ttl_sufficient(monkeypatch):
    """续期判据：剩余 TTL >= 阈值(120s) 时**跳过**、不构建、不回写、返回 False。"""
    from app.cache import swr

    async def _plenty_ttl(cls, key):
        return 300  # 剩余充足 -> 跳过

    monkeypatch.setattr(etf_api.RedisClient, "ttl", classmethod(_plenty_ttl))

    calls: list[int] = []

    async def _fake_build() -> dict:
        calls.append(1)
        return {"today": {}}

    monkeypatch.setattr(etf_api, "_overview_with_prev", _fake_build)

    wrote: list = []

    async def _cap_write(*a, **k):
        wrote.append(a)

    monkeypatch.setattr(swr, "write_cache", _cap_write)

    assert asyncio.run(etf_api.warm_etf_overview_cache()) is False
    assert calls == [], "剩余 TTL 充足时不得触发构建"
    assert wrote == [], "跳过时不得回写"


def test_warm_etf_overview_failure_does_not_raise(monkeypatch):
    """回归②：预热失败**绝不影响启动**——吞掉异常、返回 False、不回写坏数据。

    预热在 ``main._etf_overview_warmer`` 里后台运行，其失败不得抛出（否则会终结
    预热协程 / 影响启动）。此处让构建**必然抛**，断言本函数不抛、返回 False、
    不产生任何回写（对齐 market ``warm_overview_cache`` 的 ``except`` 只 warning）。
    """
    from app.cache import swr

    async def _absent_ttl(cls, key):
        return -2

    monkeypatch.setattr(etf_api.RedisClient, "ttl", classmethod(_absent_ttl))

    async def _boom() -> dict:
        raise RuntimeError("外源不可达")

    monkeypatch.setattr(etf_api, "_overview_with_prev", _boom)

    wrote: list = []

    async def _cap_write(*a, **k):
        wrote.append(a)

    monkeypatch.setattr(swr, "write_cache", _cap_write)

    # 不抛：预热失败被吞并返回 False
    assert asyncio.run(etf_api.warm_etf_overview_cache()) is False
    assert wrote == [], "构建失败时不得回写缓存"


# ============ Task B：_mark_payload_degraded 防误用（仅降级路径可用） ============
def test_mark_payload_degraded_rejects_fresh_payload():
    """防误用：对**正常**（fresh）载荷调用必须被拒绝，绝不改变其顶层 status。

    背景（2026-09-23 QA 复核）：该助手是「误用即中毒」——对 fresh 载荷调用会补上
    顶层 ``status=degraded``，令 swr 把它当降级收敛成 ≤15s TTL，正常数据被短命化。
    加固后必须显式 ``degraded_path=True`` 才生效，否则直接 ``raise`` 拒绝；同时钉死
    降级路径的既有语义不变（无 status 补 degraded；已 unavailable 的保持原样）。
    """
    fresh = {"today": {"etf_count": 1679}, "prev": None,
             "count_comparable": True, "comparison_note": None}
    before = {k: v for k, v in fresh.items()}

    with pytest.raises(AssertionError):
        etf_api._mark_payload_degraded(fresh)  # 未声明降级路径 -> 拒绝
    assert fresh == before, "误用被拒后不得改动载荷"
    assert "status" not in fresh, "正常载荷顶层 status 绝不能被写入"

    # 降级路径（显式声明）才生效：无顶层 status 时补 degraded
    degraded = {"reason": "boom"}
    etf_api._mark_payload_degraded(degraded, degraded_path=True)
    assert degraded["status"] == "degraded"

    # 已显式 unavailable 的保持原样（swr → 不写缓存，快速自愈）
    unavailable = {"status": "unavailable", "reason": "x"}
    etf_api._mark_payload_degraded(unavailable, degraded_path=True)
    assert unavailable["status"] == "unavailable"


# ============ fetch_kline 的 volume 量纲（2026-10-01 修复） ============
def test_fetch_kline_cn_volume_normalized_to_shares(monkeypatch):
    """A 股 ``volume`` 必须 ×100 归一到【股】（腾讯 fqkline 原始字段是【手】）。

    为什么必须归一：同一字段名在两处差 100 倍会让"成交量副图"显示真值的 1%，
    并制造「主力净流入 = 成交额 32 倍」这类荒谬比例（比例荒谬先查分母单位）。

    实测证据（2026-10-01，**同日恒等式**，禁止跨日比大小）：
    7 只 ETF（510300/510500/512880/588000/159915/518880/513100）的
    ``amount / (volume × close)`` 归一前 ≈ 99.94、归一后 ≈ 0.9994。

    **证伪方式**：删掉 ``fetch_kline`` 里的 ``vol *= 100``，本测试必须 FAIL。
    """
    def _fake_request(method, url, **kw):  # noqa: ANN001, ARG001
        # 形状取自腾讯 fqkline 实返：['2026-09-30','4.421','4.432','4.444','4.415','4958544.000']
        return {"data": {"sh510300": {"qfqday": [
            ["2026-09-30", "4.421", "4.432", "4.444", "4.415", "4958544.000"],
        ]}}}

    monkeypatch.setattr(etf_mod, "_request", _fake_request)
    bars = etf_mod.fetch_kline("sh", "510300", limit=5, force=True)

    assert len(bars) == 1
    # 4,958,544 手 -> 495,854,400 股
    assert bars[0]["volume"] == 495_854_400.0, (
        f"A 股 volume 未归一到「股」：{bars[0]['volume']}")
    # 同日恒等式：股 × 价 ≈ 成交额（腾讯快照 219,623 万元 = 2,196,229,027 元）
    true_amount = 2_196_229_027.0
    got = bars[0]["volume"] * bars[0]["close"]
    assert abs(got - true_amount) / true_amount < 0.01, (
        f"归一后仍不满足 volume×close≈amount：{got:,.0f} vs {true_amount:,.0f}")


def test_fetch_kline_us_volume_left_untouched(monkeypatch):
    """美股分支**不做**换算 —— 其量纲未实测，禁止假设性换算。"""
    def _fake_request(method, url, **kw):  # noqa: ANN001, ARG001
        return {"data": {"usSPY.AM": {"day": [
            ["2026-09-30", "1", "1", "1", "1", "12345"],
        ]}}}

    monkeypatch.setattr(etf_mod, "_request", _fake_request)
    bars = etf_mod.fetch_kline("us", "SPY", limit=5, force=True)

    assert len(bars) == 1
    assert bars[0]["volume"] == 12345.0, "美股 volume 被误乘了 100"
