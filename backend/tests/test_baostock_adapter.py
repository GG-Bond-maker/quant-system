"""BaoStock 适配器单测（全部 monkeypatch，**不发真实网络**）。

覆盖：未安装/登录失败/北交所 guard/字段标准化/复权映射/会话幂等。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.ingest import baostock_adapter as bsa  # noqa: E402

_FIELDS = "date,open,high,low,close,volume,amount,turn,pctChg"
# 源返回**全部字符串**（实测），volume 单位=股 ⇒ 期望 /100 得手
_ROWS = [
    ["2024-01-02", "10.00", "10.50", "9.50", "10.20", "123456", "1234567", "0.5", "1.23"],
    ["2024-01-03", "10.20", "10.80", "10.10", "10.70", "234500", "2543210", "0.6", "-0.55"],
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    """隔离会话/限速/登出注册，并清空惰性模块缓存。"""
    monkeypatch.setattr(bsa, "_throttle", lambda: None)
    monkeypatch.setattr(bsa, "_register_logout", lambda: None)
    monkeypatch.setattr(bsa, "_logged_in", False)
    monkeypatch.setattr(bsa, "_bs_module", None)
    yield


class _FakeRs:
    """仿 baostock query 结果集。"""

    def __init__(self, fields, rows, error_code="0", error_msg=""):
        self.fields = fields
        self._rows = list(rows)
        self._i = -1
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return self._rows[self._i]


class _FakeBs:
    """仿 baostock 模块（记录调用次数与参数）。"""

    def __init__(self, rows=None, login_code="0", query_code="0"):
        self.rows = rows if rows is not None else []
        self.login_code = login_code
        self.query_code = query_code
        self.login_calls = 0
        self.logout_calls = 0
        self.query_calls = 0
        self.last_kwargs = None

    def login(self):
        self.login_calls += 1
        return types.SimpleNamespace(error_code=self.login_code, error_msg="boom")

    def logout(self):
        self.logout_calls += 1
        return types.SimpleNamespace(error_code="0", error_msg="")

    def query_history_k_data_plus(self, code, fields, **kwargs):
        self.query_calls += 1
        self.last_kwargs = {"code": code, **kwargs}
        return _FakeRs(fields.split(","), self.rows, error_code=self.query_code)


# ---------------- 懒惰加载 / 可用性 ----------------
def test_is_available_false_when_not_installed(monkeypatch: pytest.MonkeyPatch):
    """未安装 baostock ⇒ is_available() 返回 False（不抛异常，绝不影响启动）。"""
    monkeypatch.setitem(sys.modules, "baostock", None)  # import baostock -> ImportError
    assert bsa.is_available() is False


def test_is_available_true_when_module_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bsa, "_bs_module", types.SimpleNamespace())
    assert bsa.is_available() is True


# ---------------- 代码转换（含北交所 guard） ----------------
@pytest.mark.parametrize("code,expected", [
    ("600519", "sh.600519"),
    ("688981", "sh.688981"),
    ("900901", "sh.900901"),   # B 股 -> 沪
    ("510300", "sh.510300"),   # ETF -> 沪
    ("000001", "sz.000001"),
    ("300750", "sz.300750"),
    ("200011", "sz.200011"),   # B 股 -> 深
])
def test_bs_code(code: str, expected: str):
    assert bsa.bs_code(code) == expected


@pytest.mark.parametrize("code", ["430047", "830799", "400001", "870204"])
def test_bs_code_rejects_beijing(code: str):
    """北交所（4/8 开头）必须**快速拒绝**，绝不发请求（实测失败路径 39s）。"""
    with pytest.raises(ValueError, match="北交所"):
        bsa.bs_code(code)


# ---------------- 复权口径映射 ----------------
@pytest.mark.parametrize("adjust,flag", [
    ("", "3"), ("none", "3"), ("qfq", "2"), ("hfq", "1"),
])
def test_bs_adjust(adjust: str, flag: str):
    assert bsa.bs_adjust(adjust) == flag


def test_bs_adjust_unknown_raises():
    with pytest.raises(ValueError):
        bsa.bs_adjust("xx")


# ---------------- 字段标准化 / 单位换算 ----------------
def test_fetch_standardizes_and_converts_volume(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=_ROWS)
    monkeypatch.setattr(bsa, "_bs_module", fake)

    out = bsa.fetch_daily_bar_bs("600519", "2024-01-01", "2024-01-05", "")

    assert out.height == 2
    assert out["source"].unique().to_list() == ["baostock"]
    assert out["code"].unique().to_list() == ["600519"]
    # volume 股 -> 手（÷100）
    assert out["volume"].to_list() == [1234.56, 2345.0]
    assert out["amount"].to_list() == [1234567.0, 2543210.0]  # 元
    assert out["turnover"].to_list() == [0.5, 0.6]            # %
    assert out["pct"].to_list() == [1.23, -0.55]              # %
    assert out["amplitude"].to_list() == [None, None]         # 无来源 ⇒ null（不伪造）
    assert out["change"].to_list() == [None, None]
    assert str(out.schema["close"]).startswith("Float")


def test_fetch_passes_code_and_adjustflag(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=_ROWS)
    monkeypatch.setattr(bsa, "_bs_module", fake)

    bsa.fetch_daily_bar_bs("600519", "2024-01-01", "2024-01-05", "hfq")

    assert fake.last_kwargs["code"] == "sh.600519"
    assert fake.last_kwargs["adjustflag"] == "1"
    assert fake.last_kwargs["frequency"] == "d"
    assert fake.last_kwargs["start_date"] == "20240101"


def test_fetch_empty_rows_returns_empty(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=[])
    monkeypatch.setattr(bsa, "_bs_module", fake)
    out = bsa.fetch_daily_bar_bs("600519", "2024-01-01", "2024-01-05", "")
    assert out.is_empty()


# ---------------- 北交所 guard：不发请求 ----------------
def test_bj_guard_does_not_request(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=_ROWS)
    monkeypatch.setattr(bsa, "_bs_module", fake)

    out = bsa.fetch_daily_bar_bs("430047", "2024-01-01", "2024-01-05", "")

    assert out.is_empty()
    assert fake.query_calls == 0
    assert fake.login_calls == 0  # guard 早于登录 ⇒ 连登录都不做


# ---------------- 会话：登录失败 / 幂等 / 缓存 ----------------
def test_login_failure_raises_datasource_unavailable(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=_ROWS, login_code="10001")
    monkeypatch.setattr(bsa, "_bs_module", fake)

    with pytest.raises(bsa.DataSourceUnavailable):
        bsa.fetch_daily_bar_bs("600519", "2024-01-01", "2024-01-05", "")

    assert fake.login_calls == 1
    assert fake.query_calls == 0


def test_login_cached_across_calls(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=_ROWS)
    monkeypatch.setattr(bsa, "_bs_module", fake)

    bsa.fetch_daily_bar_bs("600519", "2024-01-01", "2024-01-05", "")
    bsa.fetch_daily_bar_bs("000001", "2024-01-01", "2024-01-05", "")

    assert fake.login_calls == 1   # 第二次复用会话
    assert fake.query_calls == 2


def test_logout_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=_ROWS)
    monkeypatch.setattr(bsa, "_bs_module", fake)

    bsa.fetch_daily_bar_bs("600519", "2024-01-01", "2024-01-05", "")
    bsa._logout()
    bsa._logout()  # 第二次：未登录 => no-op

    assert fake.logout_calls == 1


# ---------------- 请求错误：结构化记录后上抛 ----------------
def test_query_error_raises_and_propagates(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeBs(rows=_ROWS, query_code="10004011")
    monkeypatch.setattr(bsa, "_bs_module", fake)

    with pytest.raises(ConnectionError):
        bsa.fetch_daily_bar_bs("600519", "2024-01-01", "2024-01-05", "")
