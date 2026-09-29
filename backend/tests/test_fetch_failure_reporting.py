"""自定义抓取（`_run_fetch`）失败上报回归（2026-09-26）。

真 bug（`app/api/v1/datacenter.py::_run_fetch`）：原实现**只按 ``n`` 选文案**——

    _sync.log("INFO", f"抓取 {sym} ... {n} 行成功" if n else f"{sym} 无数据")

但本机东财**长期不可达** ⇒ 每只 ETF 的 ``("", "hfq")`` 里 ``hfq`` 必失败：
``n > 0`` 且 ``failed_adj == ["hfq"]``。此时该标的同时进了 ``_sync.failed``，
日志却一路打 **INFO「18 行成功」** —— 报喜不报忧。前端又完全不渲染
``failed_count`` ⇒ 界面上「逐只成功 + N/N 完成」，失败**完全不可见**。

修复语义（四种组合，日志等级与文案必须与事实一致）：

    ┌──────────────────────┬──────────┬─────────────────────────────────────────┐
    │ n > 0, failed_adj 空 │ INFO     │ 「… N 行成功」                           │
    │ n > 0, failed_adj 非空│ WARNING  │ 「部分口径失败：已写入 N 行（不复权）；   │
    │                      │          │   失败口径 后复权 不可用…」**不得含"成功"**│
    │ n == 0, failed_adj 非空│ WARNING │ 「全部口径失败：… 均不可用…」             │
    │ n == 0, failed_adj 空 │ INFO     │ 「无数据」                               │
    └──────────────────────┴──────────┴─────────────────────────────────────────┘

并保留既有 ``_sync.failed.add(sym)`` 语义（``_sync.failed`` 同时含全失败与部分失败）。

测试纪律：spy ``_sync.log`` + 替身 ``fetch_and_write_daily_bars``，**不打桩被测逻辑**、
不发真实网络；隔离 ``_sync`` 状态避免污染同进程其它用例。
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.data.ingest.tasks as tasks  # noqa: E402
from app.api.v1 import datacenter as dc  # noqa: E402

SYM = "159915.SZ"


@pytest.fixture()
def fetch_rec(monkeypatch):
    """spy ``_sync.log`` + 替身 ``fetch_and_write_daily_bars`` + 隔离 ``_sync`` 状态。

    返回 ``(calls, result)``：``calls`` 逐条记录 ``(level, message)``；
    ``result`` 由用例设置本次抓取结果 ``{"n": int, "failed_adj": list[str]}``。
    """
    calls: list[tuple[str, str]] = []
    result: dict = {"n": 0, "failed_adj": []}

    monkeypatch.setattr(dc._sync, "log",
                        lambda level, msg: calls.append((level, msg)))

    def _fake_fwdb(code, start, end, adjusts=("", "hfq"), fetcher=None):
        return result["n"], list(result["failed_adj"])

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fake_fwdb)

    saved = (set(dc._sync.completed), set(dc._sync.failed),
             dc._sync.done, dc._sync.total, dc._sync.current)
    dc._sync.completed, dc._sync.failed = set(), set()
    dc._sync.done, dc._sync.total, dc._sync.current = 0, 0, ""
    dc._sync.cancel_event.clear()
    try:
        yield calls, result
    finally:
        (dc._sync.completed, dc._sync.failed,
         dc._sync.done, dc._sync.total, dc._sync.current) = saved


def _run(calls, result, *, n: int, failed_adj: list[str]) -> list[tuple[str, str]]:
    """跑一遍 ``_run_fetch``（单只 ETF），返回该标的相关的日志条目。"""
    result["n"] = n
    result["failed_adj"] = list(failed_adj)
    dc._run_fetch([SYM], "etf", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())
    return [(lv, m) for lv, m in calls if SYM in m]


# ---------------------------------------------------------------------------
# 四种组合
# ---------------------------------------------------------------------------
def test_success_logs_info_success(fetch_rec):
    """① n>0 且无失败 ⇒ INFO「N 行成功」。"""
    calls, result = fetch_rec
    logs = _run(calls, result, n=18, failed_adj=[])
    assert any(lv == "INFO" and "成功" in m for lv, m in logs)
    assert not any(lv == "WARNING" for lv, m in logs)
    assert SYM in dc._sync.completed
    assert SYM not in dc._sync.failed


def test_partial_failure_warns_and_never_says_success(fetch_rec):
    """② n>0 且 failed_adj 非空 ⇒ WARNING，写出失败口径，**绝不出现"成功"**。

    这是核心回归：东财不可达时 ETF 的真实处境（raw 成功、hfq 失败）。
    """
    calls, result = fetch_rec
    logs = _run(calls, result, n=18, failed_adj=["hfq"])
    # 任何一条都不得含"成功"
    assert not any("成功" in m for _, m in logs), "部分口径失败不得出现'成功'字样"
    warns = [(lv, m) for lv, m in logs if lv == "WARNING"]
    assert warns, "部分口径失败必须打 WARNING"
    joined = " ".join(m for _, m in warns)
    assert "后复权" in joined          # 明确写出失败口径
    assert "不复权" in joined          # 明确写出已成功写入的口径
    assert "18" in joined              # 明确写出已写入行数
    # 保守口径：部分失败仍计入 failed（保留既有语义）
    assert SYM in dc._sync.failed
    assert SYM not in dc._sync.completed


def test_all_failed_warns(fetch_rec):
    """③ n==0 且 failed_adj 非空 ⇒ WARNING「全部口径失败」。"""
    calls, result = fetch_rec
    logs = _run(calls, result, n=0, failed_adj=["", "hfq"])
    assert not any("成功" in m for _, m in logs)
    assert any(lv == "WARNING" and "全部口径失败" in m for lv, m in logs)
    assert SYM in dc._sync.failed
    assert SYM not in dc._sync.completed


def test_no_data_logs_info(fetch_rec):
    """④ n==0 且无失败 ⇒ INFO「无数据」（停牌/退市，不算故障、不重试）。"""
    calls, result = fetch_rec
    logs = _run(calls, result, n=0, failed_adj=[])
    assert any(lv == "INFO" and "无数据" in m for lv, m in logs)
    assert not any(lv == "WARNING" for lv, m in logs)
    assert SYM in dc._sync.completed
    assert SYM not in dc._sync.failed


# ---------------------------------------------------------------------------
# 附加：真值表锁 —— 只有 (n>0, 无失败) 才是 INFO「成功」
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("n,failed_adj,expect_success_info", [
    (18, [], True),            # 成功
    (18, ["hfq"], False),      # 部分失败
    (0, ["", "hfq"], False),   # 全失败
    (0, [], False),            # 无数据（INFO，但非"成功"）
])
def test_only_full_success_is_info_success(fetch_rec, n, failed_adj, expect_success_info):
    calls, result = fetch_rec
    logs = _run(calls, result, n=n, failed_adj=failed_adj)
    got_success_info = any(lv == "INFO" and "成功" in m for lv, m in logs)
    assert got_success_info is expect_success_info
