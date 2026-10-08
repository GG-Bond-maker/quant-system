"""
单元测试：数据源生命线高可用与降级容灾兜底 (test_failover_lifeline)。
验证：
1. fetch_quote 在外部源不可用时能够降级到本地 Parquet 缓存 (local_parquet_cache)；
2. fetch_benchmark_daily 支持本地 Parquet 持久化缓存降级；
3. _fundamentals_from_local 支持纯数字代码模糊匹配与毛利率完整提取。
"""
import pytest
from app.data.realtime import fetch_quote, _quote_from_local_parquet, fetch_benchmark_daily
from app.data.panels import _fundamentals_from_local


from app.core.config import PROJECT_ROOT


def test_quote_local_parquet_fallback():
    """验证当外部网络中断时，能从本地 Parquet 读取已收盘快照且标注 source=local_parquet_cache。"""
    prod_data = PROJECT_ROOT / "data" / "parquet"
    q = _quote_from_local_parquet("600519.SH", data_root=prod_data)
    assert q["price"] is not None and q["price"] > 0
    assert q["source"] == "local_parquet_cache"
    assert q["quote_time"] is not None


def test_fundamentals_local_fuzzy_symbol_and_gross_margin():
    """验证本地 SQLite 基本面支持不带后缀的纯数字代码匹配，并能提取真实毛利率。"""
    prod_db = PROJECT_ROOT / "data" / "sqlite" / "aqp.db"
    # 纯数字 600519
    res1 = _fundamentals_from_local("600519", db_path=prod_db)
    assert res1 != {}
    assert res1["report_date"] is not None
    assert res1["gross_margin"] is not None and res1["gross_margin"] > 80.0
    assert res1["source"] == "local-sqlite-pit"

    # 带后缀 600519.SH
    res2 = _fundamentals_from_local("600519.SH", db_path=prod_db)
    assert res2 != {}
    assert res2["gross_margin"] == res1["gross_margin"]


def test_fetch_quote_returns_valid_data():
    """验证主入口 fetch_quote 对已知标的恒可用（无论网络状态）。"""
    q = fetch_quote("600519.SH")
    assert q["price"] is not None
    assert q["source"] in ("tencent", "sina", "local_parquet_cache")


def test_benchmark_tencent_fallback(monkeypatch):
    """验证 AKShare 失败时，fetch_benchmark_daily 能无缝切换到腾讯直接指数日线接口。"""
    from app.data import realtime as rt

    # mock AKShare 失败
    monkeypatch.setattr(rt, "_ak", lambda: (_ for _ in ()).throw(RuntimeError("akshare down")))
    df = rt.fetch_benchmark_daily("sh000300")
    assert df is not None and not df.empty
    assert "date" in df.columns and "close" in df.columns
    assert len(df) > 100


def test_holders_cache_failover(monkeypatch):
    """验证东财与新浪均离线时，股东户数与十大流通股东能从本地持久化快照兜底读取。"""
    from app.data import realtime as rt

    # 先保存快照
    fake_holder = {"end_date": "2026-06-30", "holder_num": 123456.0}
    fake_top10 = [{"rank": 1, "name": "股东A", "hold_num": 1000.0, "pct_of_float": 10.0}]
    rt._save_local_json_cache("holders", "600519.SH_num", fake_holder)
    rt._save_local_json_cache("holders", "600519.SH_top10", fake_top10)

    # mock 网络失败
    monkeypatch.setattr(rt, "_dc_get", lambda *a, **k: [])
    monkeypatch.setattr(rt, "_fetch_sina_top10_float_holders", lambda *a, **k: [])

    h_num = rt.fetch_holder_num("600519.SH")
    assert h_num["holder_num"] == 123456.0
    assert h_num["source"] == "local-cache"

    top10 = rt.fetch_top10_float_holders("600519.SH")
    assert len(top10) == 1
    assert top10[0]["name"] == "股东A"


def test_fund_flow_cache_failover(monkeypatch):
    """验证东财 push2 机器组全部断开时，大盘资金流能从本地快照读取兜底。"""
    from app.data import realtime as rt

    fake_flow = [{"日期": "2026-09-30", "主力净流入-净额": 1000000.0}]
    rt._save_local_json_cache("fund_flow", "market", fake_flow)

    monkeypatch.setattr(rt, "_first_source", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("push2 down")))

    flow = rt.fetch_market_fund_flow()
    assert flow == fake_flow


def test_financial_indicators_cache_and_sqlite_failover(monkeypatch):
    """验证东财 datacenter 离线时，财务关键指标能从本地快照或本地 SQLite 兜底读取。"""
    from app.data import realtime as rt

    # 1. 模拟本地快照命中
    fake_ind = {
        "report_date": "2026-06-30",
        "roe": 15.5,
        "gross_margin": 45.0,
        "net_margin": 20.0,
        "eps": 1.2,
        "bps": 10.0,
    }
    rt._save_local_json_cache("financials", "600519.SH_indicators", fake_ind)
    monkeypatch.setattr(rt, "_dc_get", lambda *a, **k: [])

    ind = rt.fetch_financial_indicators("600519.SH")
    assert ind["roe"] == 15.5
    assert ind["source"] == "local-cache"

    # 2. 模拟无快照时回退到 SQLite
    rt._save_local_json_cache("financials", "600519.SH_indicators", None)
    monkeypatch.setattr(rt, "_load_local_json_cache", lambda *a, **k: None)
    ind2 = rt.fetch_financial_indicators("600519.SH")
    assert ind2["gross_margin"] is not None and ind2["gross_margin"] > 80.0
    assert ind2["source"] == "local-sqlite-pit"


def test_etf_catalog_cache_failover(monkeypatch):
    """验证东财与新浪均离线时，中国境内 ETF 目录能从本地磁盘快照兜底读取。"""
    from app.data import etf as etf_mod

    fake_etfs = [
        {"code": "510300", "name": "沪深300ETF", "price": 4.12, "pct": 0.5, "amount": 1e8, "total_mv": 1e11, "float_mv": 1e11}
    ]
    etf_mod._save_local_etf_cache("cn_etfs", fake_etfs)

    monkeypatch.setattr(etf_mod, "_fetch_em_cn_etfs", lambda: (_ for _ in ()).throw(RuntimeError("em down")))
    monkeypatch.setattr(etf_mod, "_fetch_sina_cn_etfs", lambda: (_ for _ in ()).throw(RuntimeError("sina down")))

    rows = etf_mod.fetch_cn_etfs()
    assert len(rows) == 1
    assert rows[0]["code"] == "510300"
    assert etf_mod.cn_etf_source() == "local_cache"


def test_csindex_dict_cache_failover(monkeypatch):
    """验证中证官网失败或冷却时，中证指数名录能从本地磁盘快照兜底读取。"""
    from app.data import etf as etf_mod

    fake_dict = {"沪深300": [("000300", "沪深300")]}
    etf_mod._save_local_etf_cache("csindex_dict", fake_dict)

    # 模拟网络不可达
    monkeypatch.setattr(etf_mod, "_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("csindex 403")))

    d = etf_mod._build_index_dict()
    assert "沪深300" in d
    assert d["沪深300"] == [("000300", "沪深300")]


def test_etf_fee_and_profile_cache_failover(monkeypatch):
    """验证天天基金 F10 离线时，基金费率与画像能从本地快照兜底。"""
    from app.data import etf as etf_mod

    # 1. 费率
    fake_fee = {"management": 0.15, "custody": 0.05, "unit": "%/年"}
    etf_mod._save_local_etf_cache("fees_510300", fake_fee)
    monkeypatch.setattr(etf_mod, "_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("f10 timeout")))

    fee = etf_mod.fetch_etf_fee("510300")
    assert fee["management"] == 0.15

    # 2. 画像
    fake_prof = {"tracking": {"kind": "index", "name": "沪深300"}, "fund_type": "指数型-股票", "fund_name": "华泰柏瑞沪深300ETF"}
    etf_mod._save_local_etf_cache("profile_510300", fake_prof)

    prof = etf_mod.fetch_etf_profile("510300")
    assert prof["fund_type"] == "指数型-股票"
    assert prof["tracking"]["name"] == "沪深300"


def test_etf_benchmark_kline_parquet_fallback(monkeypatch):
    """验证腾讯与中证 perf 均离线时，ETF 基准日线能从本地 Parquet 兜底读取。"""
    from app.data import etf as etf_mod
    from app.core.config import get_settings
    from app.data.parquet_store import atomic_write_parquet
    import pandas as pd

    # 在当前测试环境 DATA_ROOT 下准备本地基准文件
    s = get_settings()
    bench_dir = s.DATA_ROOT / "benchmarks"
    bench_dir.mkdir(parents=True, exist_ok=True)
    fake_df = pd.DataFrame([
        {"date": f"2026-01-{i+1:02d}", "open": 3800.0, "high": 3850.0, "low": 3780.0, "close": 3820.0, "volume": 10000000.0}
        for i in range(25)
    ])
    atomic_write_parquet(bench_dir / "sh000300.parquet", fake_df)

    # 模拟外部源失败
    monkeypatch.setattr(etf_mod, "fetch_index_kline", lambda *a, **k: [])
    monkeypatch.setattr(etf_mod, "fetch_cn_index_perf", lambda *a, **k: None)

    bars = etf_mod.fetch_benchmark_kline("sh", "000300", limit=50)
    assert len(bars) == 25
    assert bars[0]["close"] == 3820.0
    assert bars[0]["date"] == "2026-01-01"


