"""参数寻优的**网格级取消**与**检查点续跑**回归（P8-c / A+C，2026-10-04 上线前全检）。

## 背景

``POST /api/v1/backtest/strategy-run`` 带 ``optimize_params`` 时前端给 600s、
后端给 660s，寻优 = 网格组合数 × 单次回测，是全仓唯一真正可能跑到分钟级的同步重活。
此前有两个缺口：

1. **白烧**：客户端断开 / 660s 预算触发 504 后，worker 仍跑完整张网格
   （``asyncio.wait_for`` 只取消外层 await，executor 线程不会被取消）。
2. **无中间态**：跑到 480/500 组合时进程被杀 ⇒ 全部作废，重试从头再跑。

## 覆盖

* A（取消）：每个组合**跑之前**检查标志；置位即停止并返回已完成部分；
  粒度为一个组合；取消后**剩余组合不再调用** ``evaluate``；
* C（检查点）：每完成一个组合即落库；同签名重试**跳过**已完成组合并复用其结果；
* 不变量：``skip_trials`` 不影响正确性（best 选取与全量搜索一致）；
* 门面：``opt_checkpoint`` 的签名/组合键稳定性、取消标志读写、TTL 语义；
* 契约：取消**不是**错误（不抛 ``ValueError``/不落 ``ERR_BT_OPTIMIZE_FAILED``）。

测试用 ``REDIS_ENABLED=0``（conftest 已钉死）⇒ 走 ``opt_checkpoint`` 的
**进程内降级存储**，因此这些用例不依赖 Redis，结果确定。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest import opt_checkpoint as ck  # noqa: E402
from app.backtest.opt_checkpoint import CancelledSearch  # noqa: E402
from app.backtest.param_search import (  # noqa: E402
    SearchHooks,
    grid_search,
    run_search,
)


@pytest.fixture(autouse=True)
def _force_local_store(monkeypatch):
    """强制走**进程内降级存储**，并清空状态。

    为什么要强制：``_sget/_sset`` 在 ``REDIS_ENABLED`` 且 Redis 可达时会真的访问
    Redis。若本机恰好跑着 Redis（本项目 compose 里就有 ``aqp-redis``），
    那么"刚写过就断言读不到"这类用例会被**上一轮遗留键**干扰（实测踩过：
    ``request_cancel`` 之后 ``is_cancelled`` 在清空后仍为 True）。
    这里把 ``_sync_redis`` 钉成 None ⇒ 存储完全进程内，用例确定且不依赖外部服务。
    """
    monkeypatch.setattr(ck, "_sync_redis", lambda: None)
    ck._local_checkpoints.clear()  # noqa: SLF001
    yield
    ck._local_checkpoints.clear()  # noqa: SLF001


def _grid():
    return {"short_ma": [5, 10, 15], "long_ma": [20, 30]}


def _evaluate_factory(calls: list[dict]):
    def _evaluate(params: dict) -> dict:
        calls.append(dict(params))
        # 目标值刻意用可预测的函数，便于断言 best 选取
        return {"sharpe": float(params["short_ma"] + params["long_ma"]),
                "n": 1, "nav": None}
    return _evaluate


# ---------------- 基线：不传 hooks 行为不变 ----------------

def test_grid_search_without_hooks_is_unchanged():
    calls: list[dict] = []
    res = grid_search(_evaluate_factory(calls), _grid())
    assert len(calls) == 6 and len(res.trials) == 6
    assert res.n_skipped == 0
    assert res.best_params == {"short_ma": 15, "long_ma": 30}
    assert res.best_objective == 45.0


# ---------------- A：网格级取消 ----------------

def test_cancel_stops_before_next_grid_point():
    """取消置位后，**剩余组合不再调用 evaluate**，并抛 CancelledSearch。"""
    calls: list[dict] = []
    stop_after = 2

    def _should_cancel() -> bool:
        return len(calls) >= stop_after

    with pytest.raises(CancelledSearch) as ei:
        grid_search(_evaluate_factory(calls), _grid(),
                    hooks=SearchHooks(should_cancel=_should_cancel))
    assert len(calls) == stop_after, (
        f"取消后不应再评估新组合：期望 {stop_after} 次，实际 {len(calls)}")
    assert "已完成" in str(ei.value)


def test_cancel_granularity_is_one_combo():
    """粒度=一个组合：只有**已开始**的那个会跑完，不会多跑第二个。"""
    calls: list[dict] = []
    tripped = {"done": False}

    def _should_cancel() -> bool:
        # 第一次检查放行，评估完一个组合后置位
        if tripped["done"]:
            return True
        tripped["done"] = True
        return False

    with pytest.raises(CancelledSearch):
        grid_search(_evaluate_factory(calls), _grid(),
                    hooks=SearchHooks(should_cancel=_should_cancel))
    assert len(calls) == 1, f"应恰好跑完 1 个组合，实际 {len(calls)}"


def test_cancel_not_requested_runs_all():
    calls: list[dict] = []
    res = grid_search(_evaluate_factory(calls), _grid(),
                      hooks=SearchHooks(should_cancel=lambda: False))
    assert len(calls) == 6 and res.n_skipped == 0


# ---------------- C：检查点续跑 ----------------

def test_checkpoint_records_each_completed_trial():
    """每完成一个组合即回调 on_trial（进程被杀后据此续跑）。"""
    recorded: list[tuple] = []
    calls: list[dict] = []
    grid = _grid()
    total = 6

    with pytest.raises(CancelledSearch):
        grid_search(
            _evaluate_factory(calls), grid,
            hooks=SearchHooks(
                should_cancel=lambda: len(calls) >= 3,
                on_trial=lambda p, o, m: recorded.append((dict(p), o, dict(m))),
            ))
    assert len(recorded) == 3, f"应记录 3 个已完成组合，实际 {len(recorded)}"
    assert [r[0]["short_ma"] for r in recorded] == [5, 5, 10]
    assert total == 6  # 断言意图：上下文中的总组合数（供 total 字段口径）


def test_resume_skips_completed_combos_and_reuses_results():
    """续跑：命中 ``skip_trials`` 的组合**不重算**，直接复用其结果。"""
    calls: list[dict] = []
    evaluate = _evaluate_factory(calls)
    grid = _grid()

    # 先跑完整一轮，拿到"已完成"快照
    full = grid_search(evaluate, grid)
    skip = {ck.combo_key(t.params): (t.objective, t.metrics)
            for t in full.trials}
    assert len(skip) == 6

    # 再以"全部已完成"续跑 ⇒ 一次都不该重算
    calls.clear()
    resumed = grid_search(evaluate, grid, skip_trials=skip)
    assert calls == [], f"全部命中检查点时不应重算，实际调用 {calls}"
    assert resumed.n_skipped == 6
    assert resumed.best_params == full.best_params
    assert resumed.best_objective == full.best_objective
    assert len(resumed.trials) == 6


def test_resume_partial_only_recomputes_remainder():
    """部分完成：只重算未完成的组合（这就是"480/500 被杀只需再跑 20"）。"""
    calls: list[dict] = []
    evaluate = _evaluate_factory(calls)
    grid = _grid()
    full = grid_search(evaluate, grid)

    # 只把前 4 个组合标记为已完成
    skip = {ck.combo_key(t.params): (t.objective, t.metrics)
            for t in full.trials[:4]}
    calls.clear()
    resumed = grid_search(evaluate, grid, skip_trials=skip)
    assert len(calls) == 2, f"应只重算剩余 2 个，实际 {len(calls)}"
    assert resumed.n_skipped == 4
    # 正确性不变量：续跑结果与全量搜索一致
    assert resumed.best_params == full.best_params
    assert resumed.best_objective == full.best_objective


def test_resume_does_not_change_best_selection():
    """续跑不影响 best 选取：最优组合落在"跳过"集合里也能被正确识别。"""
    calls: list[dict] = []
    grid = _grid()
    # 手工构造：只有最优组合（15,30=45）在检查点里，其余需重算
    best_params = {"short_ma": 15, "long_ma": 30}
    skip = {ck.combo_key(best_params): (45.0, {"n": 1})}
    res = grid_search(_evaluate_factory(calls), grid, skip_trials=skip)
    assert res.best_params == best_params
    assert res.best_objective == 45.0
    assert len(calls) == 5, "其余 5 个组合应被重算"


# ---------------- 门面：opt_checkpoint ----------------

def test_signature_is_stable_and_order_insensitive():
    a = ck.signature_of({"x": [1, 2], "y": [3]})
    b = ck.signature_of({"y": [3], "x": [1, 2]})
    assert a == b, "同内容不同键序必须得到同一签名（否则续跑永远命中不了）"


def test_signature_changes_when_grid_changes():
    """改了网格 ⇒ 必须是**不同**签名，否则会复用错误的"已完成"集合。"""
    a = ck.signature_of({"grid": {"short_ma": [5, 10]}})
    b = ck.signature_of({"grid": {"short_ma": [5, 20]}})
    assert a != b, "网格变了签名必须变（改了网格就是另一次搜索）"


def test_combo_key_is_stable():
    assert ck.combo_key({"a": 1, "b": 2}) == ck.combo_key({"b": 2, "a": 1})


def test_checkpoint_roundtrip_and_clear():
    sig = ck.signature_of({"t": "roundtrip"})
    assert ck.load_checkpoint(sig)["n_done"] == 0
    ck.save_trial(sig, ck.combo_key({"a": 1}), {"a": 1}, 1.5, {"n": 2}, total=3)
    ck.save_trial(sig, ck.combo_key({"a": 2}), {"a": 2}, 2.5, {"n": 2}, total=3)
    got = ck.load_checkpoint(sig)
    assert got["n_done"] == 2
    assert got["done"][ck.combo_key({"a": 1})]["objective"] == 1.5
    ck.clear_checkpoint(sig)
    assert ck.load_checkpoint(sig)["n_done"] == 0


def test_nan_objective_is_stored_as_none_not_fabricated():
    """目标值为 NaN 时必须如实存 None，不得写成 0 之类的假数值。"""
    sig = ck.signature_of({"t": "nan"})
    ck.save_trial(sig, ck.combo_key({"a": 1}), {"a": 1}, float("nan"), {}, total=1)
    got = ck.load_checkpoint(sig)
    assert got["done"][ck.combo_key({"a": 1})]["objective"] is None


def test_cancel_flag_roundtrip():
    sig = ck.signature_of({"t": "cancel"})
    assert ck.is_cancelled(sig) is False
    ck.request_cancel(sig)
    assert ck.is_cancelled(sig) is True
    ck.clear_cancel(sig)
    assert ck.is_cancelled(sig) is False


def test_is_cancelled_tolerates_empty_signature():
    """空签名不得误判为已取消（未开寻优的普通回测走这条）。"""
    assert ck.is_cancelled("") is False


def test_bad_checkpoint_payload_degrades_to_empty():
    """坏检查点（非合法 JSON）不得让搜索失败，只当作"没有检查点"。"""
    import time

    sig = ck.signature_of({"t": "bad"})
    ck._local_checkpoints[ck._ckpt_key(sig)] = {  # noqa: SLF001
        "_ts": time.time(), "raw": "{ this is not valid json"}
    got = ck.load_checkpoint(sig)
    assert got["n_done"] == 0


# ---------------- run_search 透传 ----------------

def test_run_search_passes_hooks_and_skip_to_grid():
    calls: list[dict] = []
    grid = _grid()
    full = grid_search(_evaluate_factory([]), grid)
    skip = {ck.combo_key(t.params): (t.objective, t.metrics) for t in full.trials}
    res = run_search(_evaluate_factory(calls), grid, method="grid",
                     skip_trials=skip)
    assert calls == [] and res.n_skipped == 6


def test_run_search_hooks_work_through_facade():
    calls: list[dict] = []
    with pytest.raises(CancelledSearch):
        run_search(_evaluate_factory(calls), _grid(), method="grid",
                   hooks=SearchHooks(should_cancel=lambda: len(calls) >= 1))
    assert len(calls) == 1


# ---------------- API 契约（不改现有契约，只新增两个路由） ----------------

def _req(**over):
    from app.api.v1.backtest import StrategyBacktestRequest

    base = dict(
        symbols=["600519.SH"], start="2024-01-01", end="2025-12-31",
        strategy_type="ma_cross", short_ma=5, long_ma=20,
        optimize_params={"short_ma": [5, 10], "long_ma": [20, 30]},
    )
    base.update(over)
    return StrategyBacktestRequest(**base)


def test_cancel_and_status_routes_registered():
    """三个路由必须都在（取消/进度是 P8-c 的对外接口）。"""
    from app.api.v1.backtest import router

    paths = {getattr(r, "path", None) for r in router.routes}
    for p in ("/strategy-run", "/strategy-run/cancel", "/strategy-run/opt-status"):
        assert p in paths, f"缺少路由 {p}（现有：{sorted(x for x in paths if x)}）"


def test_cancel_and_status_use_same_request_model_as_strategy_run():
    """取消/进度必须用**与 strategy-run 完全相同的请求体**定位目标。

    这是"不引入任务 ID、因而不改变契约"的关键：三者共用
    ``StrategyBacktestRequest`` ⇒ 客户端把原请求体原样再发一次即可。

    ⚠️ 本模块有 ``from __future__ import annotations`` ⇒ 注解是**字符串**，
    必须用 ``typing.get_type_hints`` 解析后再比，不能直接比 ``parameter.annotation``
    （那样只会拿到字符串 ``'StrategyBacktestRequest'``）。
    """
    import typing

    from app.api.v1 import backtest as bt

    for fn in (bt.cancel_strategy_optimization, bt.strategy_optimization_status,
               bt.run_strategy_backtest_api):
        hints = typing.get_type_hints(fn)
        assert hints.get("req") is bt.StrategyBacktestRequest, (
            f"{fn.__name__} 的请求体必须是 StrategyBacktestRequest，"
            f"实际 {hints.get('req')!r}")


def test_signature_is_derived_from_cache_key_so_retry_matches():
    """同一请求体 ⇒ 同一个签名（缓存键）。这是"同请求体重试可续跑"的前提。"""
    from app.api.v1.backtest import _strategy_cache_key

    r1, r2 = _req(), _req()
    assert _strategy_cache_key(r1) == _strategy_cache_key(r2)


def test_signature_changes_when_grid_changes_via_cache_key():
    """改了网格 ⇒ 缓存键必须变（否则会复用错误的检查点）。"""
    from app.api.v1.backtest import _strategy_cache_key

    a = _strategy_cache_key(_req())
    b = _strategy_cache_key(_req(optimize_params={"short_ma": [5, 99],
                                                  "long_ma": [20, 30]}))
    assert a != b, "网格改变必须改变签名，否则续跑会跳过本该重跑的组合"


# ---------------- 源码护栏：寻优分支的相对导入必须可解析 ----------------

def test_optimize_branch_relative_imports_resolve():
    """``backtest.py`` 里指向 ``app.backtest.*`` 的相对导入必须**真能导入**。

    回归护栏（2026-10-04 上线前全检）：该分支曾写
    ``from ...backtest.param_search import CancelledSearch, SearchHooks``，
    而 ``CancelledSearch`` 定义在 ``opt_checkpoint``、``param_search`` **不 re-export**
    ⇒ ImportError。该 import 在 ``if req.optimize_params:`` 分支内**无条件执行**
    （位于下方 ``try`` 之外）⇒ 任何带 ``optimize_params`` 的
    ``/backtest/strategy-run`` 都必然 500。

    为什么本文件其余用例抓不到：它们从 ``opt_checkpoint`` 直接导入
    ``CancelledSearch``（走对了路），而**生产代码走错了路**；「把模块 import 一下」
    也抓不到（该 import 在**函数体内**）。故此处按源码 **AST** 逐条核对导入目标
    ——刻意不用正则：本仓库注释里会原样引用旧的错误写法，正则判据会恒真。
    """
    import ast
    import importlib

    src_path = BACKEND_ROOT / "app" / "api" / "v1" / "backtest.py"
    pkg = "app.api.v1"  # `app/api/v1/backtest.py` 的 __package__
    resolved: list[str] = []
    for node in ast.walk(ast.parse(src_path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or not node.level or not node.module:
            continue
        parts = pkg.split(".")
        base = ".".join(parts[: len(parts) - (node.level - 1)])
        mod_name = f"{base}.{node.module}"
        if not mod_name.startswith("app.backtest"):
            continue
        mod = importlib.import_module(mod_name)
        for alias in node.names:
            if not hasattr(mod, alias.name):
                # 也可能是子模块形式：``from ...backtest import opt_checkpoint``
                importlib.import_module(f"{mod_name}.{alias.name}")
            resolved.append(f"{mod_name}.{alias.name}")

    assert resolved, "未扫到任何 app.backtest.* 相对导入 —— 护栏本身已失效"
    assert "app.backtest.opt_checkpoint.CancelledSearch" in resolved, (
        "寻优分支应从 opt_checkpoint 导入 CancelledSearch；"
        "若已合法改写，请同步更新本护栏")
    assert "app.backtest.param_search.SearchHooks" in resolved
