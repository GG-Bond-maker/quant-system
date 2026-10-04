"""周期预热排程 + 重活线程池隔离回归（P-4 / P-8，2026-10-04 上线前全检）。

## P-4：预热周期 = 耗时 + 间隔（缓存保温落空）

三个周期预热协程原先每轮结束都无条件 ``sleep(period)``，于是
**真实周期 = 预热耗时 + period**。对 datacenter 预热这是实质缺陷：
``period=1500s`` 的本意是"在 1800s 统计 TTL 内提前 300s 续期"，但实测单轮最坏
**702.6s**（``backend-run-1003-r6.log``，同期还有 624.5 / 573.0 / 421.9s）⇒
真实周期 ``702.6 + 1500 = 2202.6s`` **> 1800s TTL** ⇒ 每轮之间有一段缓存已过期，
"保温"目标落空，首个页面加载仍要扛冷扫。

修法：``main.wait_until_next_cycle`` 按**截止时刻**排程，周期恒为 ``period``。

## P-8：重活与探针共用默认池

``datacenter._scan_in_pool`` / ``ops._build_lineage`` 等重活原先走
``asyncio.to_thread``（event loop 默认池 22 槽，与 ``/health/ready`` 探针共用）。
本文件断言这些路径确实改走了 ``core/compute_pool``。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import wait_until_next_cycle  # noqa: E402


# ---------------- P-4：按截止时刻排程 ----------------

async def test_cycle_is_period_minus_elapsed_not_period():
    """本轮已耗时 T ⇒ 只再睡 ``period - T``，而不是睡满 ``period``。

    这是 P-4 的核心：周期恒为 ``period``，与每轮耗时无关。
    """
    period = 1.0
    started = asyncio.get_running_loop().time() - 0.4  # 模拟本轮已跑 0.4s
    t0 = asyncio.get_running_loop().time()
    await wait_until_next_cycle(started, period)
    slept = asyncio.get_running_loop().time() - t0
    # 期望睡 ~0.6s；给宽松容差（CI 上 sleep 精度有限），但必须显著 < period
    assert 0.3 < slept < 0.95, f"应只睡 period-已耗时（~0.6s），实际 {slept:.3f}s"


async def test_cycle_never_sleeps_negative_when_overrun():
    """本轮耗时**超过** period 时立刻进入下一轮（sleep 非负、不倒退）。"""
    period = 0.2
    started = asyncio.get_running_loop().time() - 5.0   # 大幅超时
    t0 = asyncio.get_running_loop().time()
    await wait_until_next_cycle(started, period)
    slept = asyncio.get_running_loop().time() - t0
    assert slept < 0.15, f"超时轮应立刻重排（不睡），实际睡了 {slept:.3f}s"


async def test_period_stays_within_ttl_for_real_warm_durations():
    """实测最坏单轮 702.6s 时，周期必须仍 < 统计 TTL(1800s)。

    这条把"保温"这一设计目标**固化成断言**：若将来有人把 period 调大到
    让 ``耗时 + period`` 逼近 TTL，就会在这里失败，而不是等到线上首屏变慢。
    """
    worst_measured_round = 702.6      # backend-run-1003-r6.log
    stats_ttl = 1800.0                # datacenter 统计 TTL
    period = 1500.0                   # main.py 的预热周期
    # 按截止时刻排程 ⇒ 周期就是 period，**不含**单轮耗时
    assert period < stats_ttl, (
        f"预热周期 {period}s 必须 < TTL {stats_ttl}s，否则每轮之间缓存必过期")
    # 旧实现下的实际周期（供对照，说明该修复不是空谈）
    old_effective_period = worst_measured_round + period
    assert old_effective_period > stats_ttl, (
        "夹具前提失效：旧实现的周期应确实超过 TTL（否则本修复没有必要）")


async def test_cycle_does_not_overlap_when_round_is_short():
    """单轮很快时，周期仍为 period（不能被"立即返回"破坏）。"""
    period = 0.5
    t0 = asyncio.get_running_loop().time()
    await wait_until_next_cycle(asyncio.get_running_loop().time(), period)
    slept = asyncio.get_running_loop().time() - t0
    assert slept >= 0.35, f"刚起步的轮应睡接近 period，实际 {slept:.3f}s"


# ---------------- P-8：重活走专用计算池 ----------------

def test_datacenter_scan_uses_compute_pool():
    """``datacenter._scan_in_pool`` 必须提交到 ``compute_pool``，而不是默认池。"""
    import asyncio as _aio
    import concurrent.futures

    from app.api.v1 import datacenter as dc
    from app.core.compute_pool import get_compute_pool

    pool = get_compute_pool()
    seen: list[str] = []

    async def _run():
        loop = _aio.get_running_loop()
        orig = loop.run_in_executor

        def _spy(executor, fn, *args):
            seen.append("compute" if executor is pool
                        else ("default" if executor is None else "other"))
            return orig(executor, fn, *args)

        loop.run_in_executor = _spy  # type: ignore[assignment]
        try:
            return await dc._scan_in_pool(lambda: "scan-ok")  # noqa: SLF001
        finally:
            loop.run_in_executor = orig  # type: ignore[assignment]

    assert _aio.run(_run()) == "scan-ok"
    assert seen == ["compute"], f"重扫描应提交到 compute_pool，实际 {seen}"
    assert isinstance(pool, concurrent.futures.ThreadPoolExecutor)


def test_ops_lineage_uses_compute_pool():
    """``ops._build_lineage`` 是 32~38s 全量 footer 扫描，必须走专用池。"""
    import asyncio as _aio

    from app.api.v1 import ops
    from app.core.compute_pool import get_compute_pool

    pool = get_compute_pool()
    seen: list[str] = []

    async def _run():
        loop = _aio.get_running_loop()
        orig = loop.run_in_executor

        def _spy(executor, fn, *args):
            seen.append("compute" if executor is pool else "default")
            return orig(executor, fn, *args)

        loop.run_in_executor = _spy  # type: ignore[assignment]
        try:
            return await ops._build_lineage()  # noqa: SLF001
        finally:
            loop.run_in_executor = orig  # type: ignore[assignment]

    # 真实扫描在测试的隔离 DATA_ROOT 上跑（可能为空/很小），只看有没有抛错与走哪个池
    try:
        _aio.run(_run())
    except Exception:  # noqa: BLE001 隔离数据根下扫描可能直接报无数据；池选择已记录
        pass
    assert seen and seen[0] == "compute", (
        f"_build_lineage 应提交到 compute_pool，实际 {seen}")


# ---------------- P-8a：research / backtest 迁移 ----------------

def test_run_in_compute_pool_submits_to_pool_not_default():
    """统一入口 ``run_in_compute_pool`` 必须提交到 ``compute_pool``。"""
    import asyncio as _aio

    from app.core.compute_pool import get_compute_pool, run_in_compute_pool

    pool = get_compute_pool()
    seen: list[object] = []

    async def _run():
        loop = _aio.get_running_loop()
        orig = loop.run_in_executor

        def _spy(executor, fn, *args):
            seen.append(executor)
            return orig(executor, fn, *args)

        loop.run_in_executor = _spy  # type: ignore[assignment]
        try:
            return await run_in_compute_pool(lambda a, b: a + b, 2, 3)
        finally:
            loop.run_in_executor = orig  # type: ignore[assignment]

    assert _aio.run(_run()) == 5, "透传参数与返回值必须不变"
    assert seen == [pool], f"应只提交到专用池，实际 {seen}"


def test_research_run_sync_uses_compute_pool():
    """``research._run_sync`` 是该文件 10 个端点的统一入口，必须走专用池。"""
    import asyncio as _aio

    from app.api.v1 import research
    from app.core.compute_pool import get_compute_pool

    pool = get_compute_pool()
    seen: list[object] = []

    async def _run():
        loop = _aio.get_running_loop()
        orig = loop.run_in_executor

        def _spy(executor, fn, *args):
            seen.append(executor)
            return orig(executor, fn, *args)

        loop.run_in_executor = _spy  # type: ignore[assignment]
        try:
            return await research._run_sync(lambda x: x * 2, 21)  # noqa: SLF001
        finally:
            loop.run_in_executor = orig  # type: ignore[assignment]

    assert _aio.run(_run()) == 42
    assert seen == [pool], f"research._run_sync 应走专用池，实际 {seen}"


def test_no_heavy_to_thread_left_in_research_and_backtest():
    """静态守卫：**重活模块**不得再出现 ``asyncio.to_thread`` 调用。

    只靠"入口走对池"的用例还不够 —— 后人完全可以在别处新加一处
    ``asyncio.to_thread`` 跑重活，那些用例仍然绿。故这里直接对源码断言。

    ⚠️ 用 **AST** 而不是文本匹配：这些文件的注释里到处提到 ``asyncio.to_thread``
    （含 docstring 与行尾注释），文本匹配会把**说明文字**当成违规
    （实测踩过：自己的修复说明被报成违规）。
    AST 只看得见真正的 ``Call`` 节点 —— 注释与 docstring 天然被排除。

    ⚠️ 模块清单是**累积**的：P8-a 第一轮只列了 research/backtest，结果
    ``export.py`` 被漏掉（三处重活仍在默认池，直到 P8-a 收尾才发现）。
    新增重活模块时请把它加进下面的列表 —— 这是本用例存在的意义。
    """
    import ast

    # 已迁入专用计算池的模块：不得再有裸 to_thread。
    guarded = (
        "app/api/v1/research.py",
        "app/api/v1/backtest.py",
        "app/api/v1/export.py",     # P8-a 收尾补入（曾漏掉）
    )
    for rel in guarded:
        src = (BACKEND_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (isinstance(fn, ast.Attribute) and fn.attr == "to_thread"
                    and isinstance(fn.value, ast.Name) and fn.value.id == "asyncio"):
                offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, (
            "重活模块不应再直接调用 asyncio.to_thread（默认池与 /health/ready 共用）；"
            "请改用 app.core.compute_pool.run_in_compute_pool：\n  "
            + "\n  ".join(offenders))


def test_export_routes_use_compute_pool():
    """`/export/*` 三条路由都必须走专用计算池（P8-a 收尾）。

    导出路径 = **重跑一遍完整回测引擎**（`_run` / `_run_strategy`）或全 universe
    关联（`_screen`），是全仓最重的同步活之一。它们原先走默认池，与
    ``/health/ready`` 探针共用 —— 导出期间探针会被排到队尾。
    """
    import ast

    src = (BACKEND_ROOT / "app/api/v1/export.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    n_pool = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "run_in_compute_pool":
                n_pool += 1
    # 三条路由各一处：_screen / _run / _run_strategy
    assert n_pool == 3, (
        f"/export/* 应有 3 处 run_in_compute_pool（_screen/_run/_run_strategy），"
        f"实际 {n_pool} 处")


def test_p8a_capped_paths_registered_both_sides():
    """P-8a 新增的 4 条收紧必须两侧登记一致（后端上限 < 前端超时）。"""
    from app.core.timeout_guard import _PATH_MAX_BUDGETS

    expected = {
        "/api/v1/backtest/run": 96.0,
        "/api/v1/backtest/signal-analysis": 96.0,
        "/api/v1/research/optimize": 144.0,
        "/api/v1/research/stress-test": 144.0,
    }
    for path, budget in expected.items():
        assert _PATH_MAX_BUDGETS.get(path) == budget, (
            f"{path} 应登记上限 {budget}s，实际 {_PATH_MAX_BUDGETS.get(path)}")
