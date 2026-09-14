"""选股中心 · 股票列表（GET /api/v1/screener/stocks）契约 / 排序 / 降级测试。

覆盖（对应任务单 5 项 + 回归）：
1. 契约字段齐全（信封 + data + item 键集）；
2. 排序 null 恒末尾（asc / desc 都测）；
3. 非法 sort 静默回落且 ``sort_applied`` / ``dir_applied`` 回显实际生效值；
4. 非法 board 返回业务码 40000；
5. 实时源不可达 → HTTP 恒 200、列表非空、``degraded=true``、两列市值 null；
6. 鉴权：不带 token → 40100。

附加：
- 有效截面日回退（构造只有 3 行的「今日」分区，验证回退到前一完整截面日）；
- basis=daily 不触网；
- ETF 列表排序白名单 + null 恒末尾（纯函数单测，离线）。

数据全部为**本地合成**（写入私有 DATA_ROOT 的 parquet），不触网、不读生产库。
唯一的例外是 ``test_realtime_path_*`` 对外部行情源做了显式打桩（stub），
打桩数据只在测试内用于验证**合并与降级逻辑**，不进入任何生产路径。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import etf as etf_api  # noqa: E402
from app.api.v1 import screener as screener_api  # noqa: E402
from app.cache.memory import lru_clear  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.data import screening  # noqa: E402
from app.main import app  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}
_URL = "/api/v1/screener/stocks"

DAY = date(2026, 9, 4)      # 完整截面（8 只）
PREV = date(2026, 9, 3)     # 前收基准（8 只）
TODAY = date(2026, 9, 7)    # 「今日」残缺截面（3 只）—— 必须被回退掉

# symbol, name, industry, board, is_st
_SYMS = [
    ("600001.SH", "浦发测试", "银行", "main", False),
    ("600002.SH", "华夏测试", "银行", "main", False),
    ("600003.SH", "停牌测试", "银行", "main", False),   # 当日无收盘 -> pct/amount 为 null
    ("600004.SH", "ST测试股", "综合", "main", True),
    ("000001.SZ", "平安测试", "银行", "main", False),
    ("300001.SZ", "创业测试A", "电子", "chinext_star", False),
    ("300002.SZ", "创业测试B", "电子", "chinext_star", False),
    ("430047.BJ", "北交测试", "机械", "bse", False),
]
# universe_daily 故意只覆盖 6 只（缺 600003 / 300002），用于验证 SQLite 兜底
_UNI_MISSING = {"600003.SH", "300002.SZ"}

# 当日 close / amount / turnover（turnover 为小数口径，接口应 ×100）
_DAY_CLOSE = {"600001.SH": 10.0, "600002.SH": 20.0, "600003.SH": None,
              "600004.SH": 3.0, "000001.SZ": 5.0, "300001.SZ": 100.0,
              "300002.SZ": 30.0, "430047.BJ": 8.0}
_DAY_AMOUNT = {"600001.SH": 1.0e8, "600002.SH": 2.0e8, "600003.SH": None,
               "600004.SH": 3.0e7, "000001.SZ": 5.0e8, "300001.SZ": 1.0e9,
               "300002.SZ": 4.0e8, "430047.BJ": 2.0e7}
_DAY_TURNOVER = {"600001.SH": 0.0123, "600002.SH": 0.02, "600003.SH": None,
                 "600004.SH": 0.05, "000001.SZ": 0.03, "300001.SZ": 0.10,
                 "300002.SZ": 0.01, "430047.BJ": 0.04}
# 前收 -> 预期 pct：600001 +11.11 / 600002 -20 / 600003 null / 600004 0
#                  000001 +25 / 300001 +100 / 300002 0 / 430047 -20
_PREV_CLOSE = {"600001.SH": 9.0, "600002.SH": 25.0, "600003.SH": 12.0,
               "600004.SH": 3.0, "000001.SZ": 4.0, "300001.SZ": 50.0,
               "300002.SZ": 30.0, "430047.BJ": 10.0}


def _write_cross_section(root: Path, d: date, symbols: list[str]) -> None:
    """写一个 cs/daily_bar 截面分区（列与生产镜像一致）。"""
    df = pl.DataFrame({
        "date": [d] * len(symbols),
        "open": [_DAY_CLOSE.get(s) for s in symbols],
        "high": [_DAY_CLOSE.get(s) for s in symbols],
        "low": [_DAY_CLOSE.get(s) for s in symbols],
        "close": [_DAY_CLOSE.get(s) for s in symbols],
        "volume": [1000.0] * len(symbols),
        "amount": [_DAY_AMOUNT.get(s) for s in symbols],
        "turnover": [_DAY_TURNOVER.get(s) for s in symbols],
        "code": [s.split(".")[0] for s in symbols],
        "symbol": symbols,
        "source": ["test"] * len(symbols),
    })
    if d == PREV:  # 前收分区：close 用 PREV_CLOSE，amount/turnover 不参与计算
        df = df.with_columns(
            pl.Series("close", [_PREV_CLOSE.get(s) for s in symbols]),
            pl.Series("amount", [1.0e8] * len(symbols)),
        )
    out = root / "cs" / "daily_bar" / f"year={d.year}"
    out.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out / f"date={d.strftime('%Y%m%d')}.parquet")


def _seed(root: Path, db_path: Path) -> None:
    """种入本地截面 + universe_daily + SQLite instrument。"""
    all_syms = [s for s, *_ in _SYMS]
    _write_cross_section(root, DAY, all_syms)
    _write_cross_section(root, PREV, all_syms)
    # 「今日」只有 3 只（模拟增量同步没落全），必须被 latest_valid_section_date 跳过
    _write_cross_section(root, TODAY, ["600001.SH", "600002.SH", "000001.SZ"])

    # universe 故意缺 600003 / 300002 —— 逼出 SQLite instrument + board_of 兜底
    uni_syms = [s for s in all_syms if s not in _UNI_MISSING]
    meta = {s: (n, ind, b, st) for s, n, ind, b, st in _SYMS}
    uni = pl.DataFrame({
        "date": [DAY] * len(uni_syms),
        "symbol": uni_syms,
        "name": [meta[s][0] for s in uni_syms],
        "board": [meta[s][2] for s in uni_syms],
        "is_st": [meta[s][3] for s in uni_syms],
        "list_date": [None] * len(uni_syms),
        "days_since_list": [None] * len(uni_syms),
        "is_halted": [False] * len(uni_syms),
        "limit_pct": [10.0] * len(uni_syms),
        "limit_up": [None] * len(uni_syms),
        "limit_down": [None] * len(uni_syms),
        "industry": [meta[s][1] for s in uni_syms],
        "open": [None] * len(uni_syms),
        "high": [None] * len(uni_syms),
        "low": [None] * len(uni_syms),
        "close": [_DAY_CLOSE.get(s) for s in uni_syms],
        "volume": [1000.0] * len(uni_syms),
    })
    uni_dir = root / "universe_daily" / "symbol=__all__"
    uni_dir.mkdir(parents=True, exist_ok=True)
    uni.write_parquet(uni_dir / f"year={DAY.year}.snappy.parquet")

    con = sqlite3.connect(str(db_path))
    try:
        con.executemany(
            "INSERT OR REPLACE INTO instrument "
            "(symbol, code, name, market, instrument_type, is_st, is_halted, "
            " industry) VALUES (?,?,?,?,?,?,?,?)",
            [(s, s.split(".")[0], n, s.split(".")[1], "stock", int(st), 0, ind)
             for s, n, ind, _, st in _SYMS])
        con.commit()
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前清空进程内 LRU。

    conftest 把 REDIS_ENABLED 置 0，SWR 会退化为「进程内 LRU 兜底」——也就是说
    缓存在测试间**是生效的**（TTL 60s）。不清会让前序用例构建好的全市场行被
    后续用例命中，打桩/降级断言全部失效。
    """
    lru_clear()
    yield
    lru_clear()


@pytest.fixture(autouse=True)
def _offline_quotes(monkeypatch):
    """默认禁止真实外网行情（测试必须离线可跑、结果可复现）。

    不打这个桩，``basis=auto`` 的用例会真的去腾讯/新浪拉 8 只票的快照，
    于是断言里的涨跌幅变成真值、本机无网时还会拖慢 12s×重试。需要验证
    「实时合并」的用例在自身内部用 monkeypatch 覆盖本桩（后设置者生效）。
    """
    async def _stub(_symbols: list[str]):
        return [], "degraded", None

    monkeypatch.setattr(screener_api, "_fetch_quotes_sharded", _stub)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """私有 DATA_ROOT + 已种数据的测试客户端（避免跨模块污染）。"""
    mp = pytest.MonkeyPatch()
    private_root = tmp_path_factory.mktemp("screener_stocks_data")
    mp.setattr(get_settings(), "DATA_ROOT", private_root)
    # 有效截面阈值：合成数据只有 8 只，用 5 区分「3 行的残缺今日」与「8 行的完整截面」
    mp.setattr(screening, "MIN_VALID_SECTION_ROWS", 5)
    _seed(private_root, get_settings().SQLITE_PATH)

    with TestClient(app) as c:
        yield c
    mp.undo()


def _data(c: TestClient, **params) -> dict:
    r = c.get(_URL, params=params, headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    assert {"code", "message", "data", "trace_id", "ts"} <= set(body)
    return body["data"]


# ---------------- 1) 契约字段齐全 ----------------
def test_contract_fields_complete(client: TestClient):
    """响应必须带齐契约字段；默认排序 = code 升序；分页元数据正确。"""
    d = _data(client, page_size=20)
    for key in ("total", "page", "page_size", "trade_date", "as_of", "basis",
                "basis_desc", "basis_fields", "source", "degraded",
                "quote_coverage", "sort_applied", "dir_applied", "items",
                "options", "stale", "from_cache"):
        assert key in d, f"缺少契约字段 {key}"
    assert d["total"] == 8 and d["page"] == 1 and d["page_size"] == 20
    assert d["trade_date"] == DAY.isoformat(), "必须回退到完整截面日 2026-09-04"
    assert set(d["basis_fields"]) == {"close", "pct", "amount", "total_cap_yi",
                                      "float_cap_yi"}
    assert {"boards", "industries"} <= set(d["options"])
    assert "bse" in d["options"]["boards"] and "银行" in d["options"]["industries"]

    assert len(d["items"]) == 8
    item = d["items"][0]
    assert set(item) == set(screening.STOCK_ROW_FIELDS), set(item)
    # 默认排序 = code 升序
    assert [i["symbol"] for i in d["items"]][:2] == ["000001.SZ", "300001.SZ"]
    # 名称/行业/板块：600003 不在 universe 里，走 SQLite 兜底
    by_sym = {i["symbol"]: i for i in d["items"]}
    assert by_sym["600003.SH"]["name"] == "停牌测试"
    assert by_sym["600003.SH"]["industry"] == "银行"
    assert by_sym["600003.SH"]["board"] == "main"     # board_of 兜底
    assert by_sym["600003.SH"]["is_halted"] is True   # 无收盘 -> 视为停牌
    assert by_sym["600004.SH"]["is_st"] is True


def test_local_basis_values_and_units(client: TestClient):
    """本地口径：pct = 当日 close / 前一有效截面 close - 1；turnover ×100。"""
    d = _data(client, basis="daily", sort="code", page_size=50)
    assert d["basis"] == "daily" and d["source"] == "local"
    assert d["degraded"] is False and d["quote_coverage"] is None
    by_sym = {i["symbol"]: i for i in d["items"]}
    assert by_sym["600001.SH"]["pct"] == pytest.approx(11.11)
    assert by_sym["600002.SH"]["pct"] == pytest.approx(-20.0)
    assert by_sym["300001.SZ"]["pct"] == pytest.approx(100.0)
    assert by_sym["600001.SH"]["turnover"] == pytest.approx(1.23)   # 0.0123 × 100
    assert by_sym["600001.SH"]["amount"] == pytest.approx(1.0e8)    # 元
    assert by_sym["600001.SH"]["amount_yi"] == pytest.approx(1.0)   # 亿元
    # 红线：本地无市值数据 -> null，绝不 0 / 推算值
    assert by_sym["600001.SH"]["total_cap_yi"] is None
    assert by_sym["600001.SH"]["float_cap_yi"] is None
    assert by_sym["600003.SH"]["pct"] is None and by_sym["600003.SH"]["amount"] is None


# ---------------- 2) 排序 null 恒末尾 ----------------
def test_sort_nulls_last_desc(client: TestClient):
    """pct 降序：null 必须排末尾（不能被 0 兜底顶到前面）。"""
    d = _data(client, sort="pct", dir="desc", page_size=50)
    pcts = [i["pct"] for i in d["items"]]
    assert pcts[-1] is None, pcts
    assert pcts[:-1] == sorted(pcts[:-1], reverse=True)
    assert d["items"][0]["symbol"] == "300001.SZ"      # +100%
    assert d["items"][-1]["symbol"] == "600003.SH"     # null


def test_sort_nulls_last_asc(client: TestClient):
    """pct 升序：null 同样排末尾（asc / desc 一致，这是本次修复的核心）。"""
    d = _data(client, sort="pct", dir="asc", page_size=50)
    pcts = [i["pct"] for i in d["items"]]
    assert pcts[-1] is None, pcts
    assert pcts[:-1] == sorted(pcts[:-1])
    assert d["items"][0]["symbol"] == "430047.BJ"      # -20%（同值 tie-break symbol）
    assert d["items"][-1]["symbol"] == "600003.SH"     # null 仍末尾


def test_sort_nulls_last_unit():
    """纯函数口径：两列（pct / total_cap_yi）在双向排序下 null 都恒末尾。"""
    rows = [
        {"symbol": "A", "pct": 1.0, "total_cap_yi": None},
        {"symbol": "B", "pct": None, "total_cap_yi": 5.0},
        {"symbol": "C", "pct": -3.0, "total_cap_yi": 1.0},
        {"symbol": "D", "pct": 0.0, "total_cap_yi": None},
    ]
    asc, s, dr = screening.sort_stock_rows(rows, "pct", "asc")
    assert (s, dr) == ("pct", "asc")
    assert [r["symbol"] for r in asc] == ["C", "D", "A", "B"]
    desc, _, _ = screening.sort_stock_rows(rows, "pct", "desc")
    assert [r["symbol"] for r in desc] == ["A", "D", "C", "B"]
    cap_asc, _, _ = screening.sort_stock_rows(rows, "total_cap_yi", "asc")
    assert [r["symbol"] for r in cap_asc] == ["C", "B", "A", "D"]
    cap_desc, _, _ = screening.sort_stock_rows(rows, "total_cap_yi", "desc")
    assert [r["symbol"] for r in cap_desc] == ["B", "C", "A", "D"]


# ---------------- 3) 非法 sort 回落并回显 ----------------
def test_invalid_sort_falls_back_and_echoes(client: TestClient):
    """非法 sort 不报错（code=0），回落到默认并回显实际生效值。"""
    d = _data(client, sort="__bogus__", dir="desc", page_size=20)
    assert d["sort_applied"] == ""
    assert d["dir_applied"] == "asc"        # 默认口径 = code 升序
    assert [i["symbol"] for i in d["items"]][0] == "000001.SZ"

    # 显式 sort="" 与非法 sort 走同一条默认路径
    d0 = _data(client, sort="", dir="desc", page_size=20)
    assert (d0["sort_applied"], d0["dir_applied"]) == ("", "asc")

    d2 = _data(client, sort="amount", dir=" sideways ", page_size=20)
    assert d2["sort_applied"] == "amount"
    assert d2["dir_applied"] == "desc"      # 非法 dir -> desc


# ---------------- 4) 非法参数 -> 40000 ----------------
def test_invalid_board_returns_40000(client: TestClient):
    r = client.get(_URL, params={"board": "gem"}, headers=_ADMIN)
    assert r.status_code == 200                     # HTTP 恒 200
    body = r.json()
    assert body["code"] == 40000, body
    assert "board 仅支持" in body["message"], body


@pytest.mark.parametrize("params,expect", [
    ({"page": 0}, "page"),
    ({"page_size": 0}, "page_size"),
    ({"page_size": 101}, "page_size"),
    ({"q": "x" * 33}, "q"),
])
def test_invalid_pagination_returns_40000(client: TestClient, params, expect):
    r = client.get(_URL, params=params, headers=_ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 40000, body
    assert expect in body["message"], body


# ---------------- 5) 降级：实时源不可达 ----------------
def test_realtime_unreachable_degrades_to_local(client: TestClient, monkeypatch):
    """外部源全挂：HTTP 200 + 列表非空 + degraded=true + 两列市值 null。"""
    async def _down(_symbols: list[str]):
        return [], "degraded", None

    monkeypatch.setattr(screener_api, "_fetch_quotes_sharded", _down)

    r = client.get(_URL, params={"basis": "auto", "page_size": 20}, headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    d = body["data"]
    assert d["items"], "降级后列表也必须非空"
    assert d["degraded"] is True
    assert d["source"] == "local"
    assert d["quote_coverage"] is None
    assert all(i["total_cap_yi"] is None and i["float_cap_yi"] is None
               for i in d["items"])
    # 本地口径的 pct 仍要算出来（不是全 null）
    assert any(i["pct"] is not None for i in d["items"])
    assert "降级" in d["basis_desc"]


def test_realtime_path_merges_caps(client: TestClient, monkeypatch):
    """实时口径可用（覆盖率 7/8 ≥ 0.5）：合并外部字段，degraded=false。

    打桩说明：这里 stub 掉的是**外部行情源**（腾讯/新浪），验证的是本端的
    合并与覆盖率判定逻辑，桩数据不写入任何持久化存储。
    """
    async def _up(symbols: list[str]):
        quotes = []
        for s in symbols:
            if s == "600003.SH":       # 故意漏 1 只，覆盖率 7/8
                continue
            quotes.append({"symbol": s, "price": 12.34, "pct": 1.23,
                           "amount": 3.0e8, "turnover": 4.56,
                           "float_cap_yi": 100.0, "total_cap_yi": 200.0,
                           "as_of": "2026-09-04 14:32:05"})
        return quotes, "tencent", "2026-09-04 14:32:05"

    monkeypatch.setattr(screener_api, "_fetch_quotes_sharded", _up)

    d = _data(client, basis="auto", sort="pct", dir="desc", page_size=50)
    assert d["degraded"] is False
    assert d["source"] == "tencent"
    assert d["quote_coverage"] == {"hit": 7, "total": 8}
    assert d["as_of"] == "14:32:05"
    by_sym = {i["symbol"]: i for i in d["items"]}
    assert by_sym["600001.SH"]["close"] == pytest.approx(12.34)
    assert by_sym["600001.SH"]["pct"] == pytest.approx(1.23)
    assert by_sym["600001.SH"]["total_cap_yi"] == pytest.approx(200.0)
    assert by_sym["600001.SH"]["float_cap_yi"] == pytest.approx(100.0)
    assert by_sym["600001.SH"]["amount_yi"] == pytest.approx(3.0)
    assert by_sym["600001.SH"]["quote_status"] == "ok"
    # 未命中的那只保持本地口径、两列市值 null，如实标注
    assert by_sym["600003.SH"]["quote_status"] == "halted"
    assert by_sym["600003.SH"]["total_cap_yi"] is None


def test_low_coverage_degrades_whole_table(client: TestClient, monkeypatch):
    """覆盖率 2/8 < 0.5：整表降级（绝不部分混用两种口径）。"""
    async def _partial(symbols: list[str]):
        quotes = [{"symbol": s, "price": 1.0, "pct": 0.0, "amount": 1.0,
                   "turnover": 1.0, "float_cap_yi": 1.0, "total_cap_yi": 2.0,
                   "as_of": "2026-09-04 14:32:05"} for s in symbols[:2]]
        return quotes, "tencent", "2026-09-04 14:32:05"

    monkeypatch.setattr(screener_api, "_fetch_quotes_sharded", _partial)

    d = _data(client, basis="auto", page_size=50)
    assert d["degraded"] is True and d["source"] == "local"
    assert all(i["total_cap_yi"] is None for i in d["items"])
    assert all(i["quote_status"] != "ok" for i in d["items"])


def test_basis_daily_never_touches_network(client: TestClient, monkeypatch):
    """basis=daily：不调用外部行情源（打桩为抛错，仍必须 200）。"""
    async def _boom(_symbols: list[str]):
        raise AssertionError("basis=daily 不应触网")

    monkeypatch.setattr(screener_api, "_fetch_quotes_sharded", _boom)
    d = _data(client, basis="daily", page_size=20)
    assert d["degraded"] is False and d["source"] == "local"


# ---------------- 6) 鉴权 ----------------
def test_requires_auth(client: TestClient):
    r = client.get(_URL)
    assert r.status_code == 200
    assert r.json()["code"] == 40100, r.json()


# ---------------- 附加：有效截面日回退 ----------------
def test_latest_valid_section_date_skips_sparse_today(client: TestClient):
    """最大日期（3 行）必须被跳过，回退到完整截面日（8 行）。"""
    assert TODAY in screening.section_dates()
    assert screening.latest_valid_section_date() == DAY
    # 前收基准同样取「有效」截面日
    assert screening._prev_valid_section_date(DAY) == PREV


def test_board_filter_and_exclude_st(client: TestClient):
    """板块过滤 / ST 剔除 / 关键词搜索（内存筛选，无匹配不报错）。"""
    d = _data(client, board="chinext_star", page_size=50)
    assert {i["symbol"] for i in d["items"]} == {"300001.SZ", "300002.SZ"}

    d = _data(client, exclude_st=1, page_size=50)
    assert "600004.SH" not in {i["symbol"] for i in d["items"]}

    d = _data(client, q="600001", page_size=50)
    assert [i["symbol"] for i in d["items"]] == ["600001.SH"]

    d = _data(client, industry="不存在的行业", page_size=50)
    assert d["total"] == 0 and d["items"] == []

    d = _data(client, board="bse", page_size=50)
    assert [i["symbol"] for i in d["items"]] == ["430047.BJ"]


# ---------------- 附加：ETF 列表排序（纯函数，离线） ----------------
def test_etf_sort_nulls_last_both_directions():
    """ETF 列表：null 在 asc / desc 下都排末尾；-1 不再污染升序。"""
    items = [
        {"code": "A", "size_yi": 10.0, "pct": -1.0, "amount": 5.0},
        {"code": "B", "size_yi": None, "pct": None, "amount": None},
        {"code": "C", "size_yi": 1.0, "pct": 2.0, "amount": 1.0},
        {"code": "D", "size_yi": 50.0, "pct": 0.0, "amount": 9.0},
    ]
    asc, s, dr = etf_api._sort_catalog_items(list(items), "pct", "asc")
    assert (s, dr) == ("pct", "asc")
    assert [x["code"] for x in asc] == ["A", "D", "C", "B"]     # null 末尾

    desc, _, _ = etf_api._sort_catalog_items(list(items), "pct", "desc")
    assert [x["code"] for x in desc] == ["C", "D", "A", "B"]    # null 仍末尾

    size_asc, _, _ = etf_api._sort_catalog_items(list(items), "size", "asc")
    assert [x["code"] for x in size_asc] == ["C", "A", "D", "B"]


def test_etf_sort_whitelist_falls_back():
    """白名单外 sort 回落 {size, desc}；非法 dir 回落 desc。"""
    items = [{"code": "A", "size_yi": 1.0}, {"code": "B", "size_yi": 9.0}]
    out, s, dr = etf_api._sort_catalog_items(list(items), "__evil__", "asc")
    assert (s, dr) == ("size", "desc")
    assert [x["code"] for x in out] == ["B", "A"]

    out2, s2, dr2 = etf_api._sort_catalog_items(list(items), "code", "sideways")
    assert (s2, dr2) == ("code", "desc")
    assert [x["code"] for x in out2] == ["B", "A"]
