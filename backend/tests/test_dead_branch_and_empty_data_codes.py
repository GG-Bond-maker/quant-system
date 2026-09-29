"""B7a-08 / B7a-09 防回归：空数据的错误码 + 死分支清理。

- **B7a-08** `research.py` 压力测试：`universe_daily` 无任何 parquet 时
  `pl.concat([])` 抛未捕获 `ValueError` → 全局兜底成**裸 50000**（"系统故障"），
  而它其实是"本地还没数据"这种可解释的降级。修法：与本函数 `:545` 同口径，
  显式 `ERR_DATA_EMPTY(51001)`。
- **B7a-09** `market.py::_build_ai_stats`：`"hfq" if hfq_files else "raw_fallback"`
  的 else **不可达**（`:456-457` 已提前 return available）⇒ 永不兑现的降级承诺；
  直接读码可证（无消费方）。修法：OK 路径恒为 `"hfq"`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import ERR_DATA_EMPTY, AQPException  # noqa: E402


# ---------------- B7a-08 ----------------

def test_concat_of_empty_parquet_list_would_be_a_bare_error():
    """先证明"原写法"确实是**未分类异常**（不是 AQPException）：这是本条的缺陷本体。"""
    with pytest.raises(Exception) as ei:
        pl.concat([], how="diagonal_relaxed")
    assert not isinstance(ei.value, AQPException), (
        "若 polars 未来把它改成可识别异常，本条修复的收益需要重新评估")


def test_stress_test_empty_universe_daily_raises_data_empty(tmp_path, monkeypatch):
    """端到端：**hfq 有数据、`universe_daily` 没有**时必须是 51001 且指明基准缺失。

    判别力说明：`stress_test` 里两处早退都抛 `ERR_DATA_EMPTY`（`:545` 无 hfq、
    本条新增的基准缺失），所以只断言 code 会**假通过**；必须同时断言消息里
    出现 `universe_daily`，才能证明真的走到了被修的那一步。
    """
    import datetime as _dt

    from app.core.config import get_settings
    from app.data.parquet_store import path_for_year

    from app.api.v1 import research as research_api

    root = tmp_path / "data_root_with_hfq"
    root.mkdir()
    symbol = "600519.SH"
    # ⚠️ 必须先打补丁再落盘：`path_for_year` 读的是 `get_settings().DATA_ROOT`，
    # 顺序颠倒会把 fixture 写进**真实数据根**（本轮自查即踩到，虽因 conftest 已把
    # DATA_ROOT 重定向到临时目录而未污染生产 `data/parquet/`，但这属于运气而非设计）。
    monkeypatch.setattr(get_settings(), "DATA_ROOT", root)
    hfq = path_for_year("daily_bar_hfq", symbol, 2026)
    hfq.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [_dt.date(2026, 1, 5) + _dt.timedelta(days=i) for i in range(30)],
        "close": [100.0 + i for i in range(30)],
    }).write_parquet(hfq)
    assert hfq.is_file() and str(hfq).startswith(str(root)), "fixture 必须落在临时根内"

    req = research_api.StressTestRequest(assets=[{"code": symbol, "weight": 1.0}])
    import asyncio
    with pytest.raises(AQPException) as ei:
        asyncio.run(research_api.stress_test(req, _user={"role": "admin"}))
    assert ei.value.code == ERR_DATA_EMPTY, ei.value.message
    assert "universe_daily" in ei.value.message, (
        f"未走到基准构建那一步（假通过）：{ei.value.message}")


def test_stress_test_reports_no_hfq_before_universe_check(tmp_path, monkeypatch):
    """顺序锚点：连 hfq 都没有时先报 `所选资产均无本地 hfq 行情`（：545），
    保证上面那条用例命中的确实是**基准构建**那一步（而非更早的早退）。"""
    from app.core.config import get_settings

    from app.api.v1 import research as research_api

    empty_root = tmp_path / "empty_data_root2"
    empty_root.mkdir()
    monkeypatch.setattr(get_settings(), "DATA_ROOT", empty_root)
    req = research_api.StressTestRequest(assets=[{"code": "600519.SH", "weight": 1.0}])
    import asyncio
    with pytest.raises(AQPException) as ei:
        asyncio.run(research_api.stress_test(req, _user={"role": "admin"}))
    assert "hfq" in ei.value.message, ei.value.message


# ---------------- B7a-09 ----------------

def test_ai_stats_ok_path_label_basis_is_always_hfq():
    """OK 路径的口径恒为 `hfq`；且**源码中不再存在** `raw_fallback` 这个永假值。"""
    src = (BACKEND_ROOT / "app" / "api" / "v1" / "market.py").read_text(encoding="utf-8")
    # 允许出现在注释里（解释为何删掉），但不得作为**取值**出现
    for line in src.splitlines():
        stripped = line.strip()
        if "raw_fallback" in stripped:
            assert stripped.startswith("#"), (
                f"`raw_fallback` 仍是可执行取值/字符串字面量：{stripped}")


def test_ai_stats_never_returns_ok_without_hfq(monkeypatch, tmp_path):
    """`hfq_files` 为空 ⇒ 绝不返回 `status="ok"`；且口径字段**不随可用性消失**。

    这正是 else 分支不可达的表现（`market.py` 无预测先早退、无 hfq 再早退；
    两条早退都先于 ok 路径的返回值）。更直接的结构性证据由上面那条
    **源码扫描**用例给出。

    [AQP R10 2026-09-22] 本用例原先断言 `"label_price_basis" not in out`，
    把"口径字段只在 ok 路径返回"这个**缺口**（P5 D12 C4）当契约固化了下来。
    按本仓既有原则（见 `test_panic_rootcause_guard.py:127-129`："口径字段必须
    不随数据可用性变化，`label_price_basis` 只在 ok 路径返回是反面教材"），
    改为断言降级路径同样携带该口径 —— 断言是**收紧**而非放宽（且新增了
    "不得声称 ok" 的正面断言）。
    """
    from app.core.config import get_settings

    from app.api.v1 import market as market_api

    root = tmp_path / "no_hfq"
    (root / "predictions").mkdir(parents=True)
    (root / "daily_bar_hfq").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "DATA_ROOT", root)
    out = market_api._build_ai_stats()
    assert out["status"] == "unavailable", out
    assert out["label_price_basis"] == market_api._LABEL_PRICE_BASIS, (
        "不可用路径同样必须披露标签价口径（口径不随数据可用性变化，R10）")


def test_ai_stats_every_return_discloses_label_basis() -> None:
    """**R10**：`_build_ai_stats` 的**每一条**返回路径都必须携带标签价口径。

    结构级断言（AST）：口径字段此前只在 ok 路径返回，一降级就整块消失——
    而"口径"是评估方法的属性，不应随数据可用性变化（本仓既有原则见
    `test_panic_rootcause_guard.py:127-129`：该写法是"反面教材"）。
    """
    import ast

    src = (BACKEND_ROOT / "app" / "api" / "v1" / "market.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_build_ai_stats")
    dict_returns = [n for n in ast.walk(fn)
                    if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    assert len(dict_returns) >= 5, f"返回点解析疑似失败：只找到 {len(dict_returns)} 个"
    for r in dict_returns:
        keys = [k.value for k in r.value.keys if isinstance(k, ast.Constant)]
        assert "label_price_basis" in keys, (
            f"market.py:{r.lineno} 的返回未披露 label_price_basis（R10）")


def test_ai_stats_exception_path_still_discloses_label_basis(monkeypatch) -> None:
    """异常兜底路径同样必须披露口径（不得因异常而丢失契约字段）。"""
    from app.api.v1 import market as market_api

    def _boom():
        raise RuntimeError("settings down")

    monkeypatch.setattr(market_api, "get_settings", _boom)
    out = market_api._build_ai_stats()
    assert out["status"] == "unavailable", out
    assert out["label_price_basis"] == market_api._LABEL_PRICE_BASIS, out