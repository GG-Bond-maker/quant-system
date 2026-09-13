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

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.cache.redis_client import RedisClient  # noqa: E402
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


def test_flow_endpoint(client):
    d = _data(client, "/api/v1/etf/flow?period=1d&limit=5")
    assert d["period"] == "1d"
    assert len(d["items"]) == 2
    assert d["items"][0]["net_inflow"] == pytest.approx(2.4e8)
    assert d["items"][0]["inflow_ratio"] == pytest.approx(8.64)


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
