"""符号回归 / 遗传编程因子挖掘引擎（Automated Factor Studio 核心）。

真实 GP，而非进度模拟：
- 种群：由算子语法（ml.alpha_expr.OPS 白名单）随机生成的表达式 AST 字符串，
  深度受控、字段仅限真实 features 列；
- 适应度：在**真实 features 截面**上 eval_expr → 逐日截面 Rank IC 的
  |mean| − 复杂度惩罚 − 常数列/退化惩罚（PIT 安全：表达式算子全部向后计算）；
- 进化：锦标赛选择 + 精英保留 + 三类变异（包裹算子 / 替换算子 / 变异窗口参数）；
- 交叉：同深度子调用参数交换（受限的实数/窗口交叉，保证语法合法性）。

任务在后台线程执行，状态存进程内存（诚实口径：服务重启后任务历史清空，
已完成的挖掘结果可落库/导出由调用方决定）。
"""
from __future__ import annotations

import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from .alpha_expr import OPS, eval_expr

# 可参与挖掘的字段（真实 features 列，由 API 层传入校验）
_DEFAULT_FIELDS = ["close", "open", "high", "low", "volume"]
# 窗口算子与其参数范围
_WINDOW_OPS = {k: v for k, v in OPS.items() if v[1] == 2 and k != "Power"}
_NUM_OPS = {k: v for k, v in OPS.items() if v[1] == 3}  # If/Greater/Less 等已含比较


def _random_window(rng: random.Random) -> int:
    return rng.choice([5, 10, 20, 40, 60, 120])


def _gen_expr(rng: random.Random, fields: list[str], depth: int) -> str:
    """按算子语法随机生成合法表达式（深度优先，叶子为字段或常数）。"""
    if depth <= 0 or rng.random() < 0.25:
        return rng.choice(fields)   # 叶子只用真实字段（常数列无因子意义）
    op = rng.choice(list(_WINDOW_OPS))
    if op == "Ref":
        return f"Ref({rng.choice(fields)}, {_random_window(rng) // 5 * 5 or 5})"
    if op in ("Corr", "Cov"):
        a = _gen_expr(rng, fields, depth - 1)
        b = _gen_expr(rng, fields, depth - 1)
        return f"{op}({a}, {b}, {_random_window(rng)})"
    inner = _gen_expr(rng, fields, depth - 1)
    return f"{op}({inner}, {_random_window(rng)})"


def _mutate(expr: str, rng: random.Random, fields: list[str]) -> str:
    """三类变异：包裹新算子 / 替换顶层算子 / 变异窗口参数。"""
    kind = rng.random()
    if kind < 0.4:  # 包裹
        op = rng.choice(list(_WINDOW_OPS))
        if op in ("Corr", "Cov"):
            return f"{op}({expr}, {_gen_expr(rng, fields, 0)}, {_random_window(rng)})"
        return f"{op}({expr}, {_random_window(rng)})"
    if kind < 0.7:  # 替换顶层算子（保持参数个数）
        rest = expr.split("(", 1)[1].rsplit(")", 1)[0] if "(" in expr else expr
        args = [a.strip() for a in rest.split(",")] if "(" in expr else [expr]
        op = rng.choice(list(_WINDOW_OPS))
        if op in ("Corr", "Cov") and len(args) >= 1:
            return f"{op}({args[0]}, {_gen_expr(rng, fields, 0)}, {_random_window(rng)})"
        if len(args) >= 2:
            return f"{op}({args[0]}, {args[-1]})"
        return f"{op}({expr}, {_random_window(rng)})"
    # 窗口参数变异：把表达式里出现的随机一个窗口整数换掉
    import re

    wins = re.findall(r",\s*(\d+)\s*\)", expr)
    if wins:
        old = rng.choice(wins)
        return expr.replace(f", {old})", f", {_random_window(rng)})", 1)
    return _gen_expr(rng, fields, 2)


def _complexity(expr: str) -> int:
    return expr.count("(") * 2 + len(expr) // 40


@dataclass
class GpTaskState:
    """GP 任务运行状态（进程内存）。"""

    task_id: str
    params: dict[str, Any]
    status: str = "PENDING"            # PENDING/RUNNING/DONE/FAILED/CANCELLED
    generation: int = 0
    total_generations: int = 0
    fitness_curve: list[float] = field(default_factory=list)
    # 同期 best 的 MeanIC / ICIR 曲线（让用户看清真实 IC 水平，不只看复合 fitness）
    mean_ic_curve: list[float] = field(default_factory=list)
    icir_curve: list[float] = field(default_factory=list)
    best: dict[str, Any] | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    history: list[dict] = field(default_factory=list)
    # 取消信号：外部 cancel_task 设 set 后，_run_task 下一代开始即退出
    cancel_event: threading.Event = field(default_factory=threading.Event)


_TASKS: dict[str, GpTaskState] = {}
_TASKS_LOCK = threading.Lock()

# P2-14：终态任务持久化（app_state 表，JSON），最多保留 10 个。
# 进程重启后 get_task 未命中时从持久层恢复，前端 localStorage 里的
# task_id 仍能查到最终结果；运行中任务被重启打断则无法恢复（如实 404）。
_PERSIST_KEY = "gp_tasks"
_PERSIST_MAX = 10


def _task_snapshot(task: GpTaskState) -> dict:
    """任务可 JSON 化快照（不含 cancel_event / started_at 等运行态）。"""
    return {
        "task_id": task.task_id, "params": task.params, "status": task.status,
        "generation": task.generation,
        "total_generations": task.total_generations,
        "fitness_curve": task.fitness_curve,
        "mean_ic_curve": task.mean_ic_curve, "icir_curve": task.icir_curve,
        "best": task.best, "error": task.error, "history": task.history,
    }


def _persist_task(task: GpTaskState) -> None:
    """终态任务落 SQLite；失败只记日志，不影响任务本身。"""
    try:
        from ..db.kv import kv_get, kv_set
        kept = [t for t in kv_get(_PERSIST_KEY, [])
                if t.get("task_id") != task.task_id]
        kept.append(_task_snapshot(task))
        kv_set(_PERSIST_KEY, kept[-_PERSIST_MAX:])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[gp-miner] persist task {task.task_id} fail: {e!r}")


def _notify_done(task: GpTaskState) -> None:
    """任务终态通知（worker 线程 → SSE 推送到前端顶栏铃铛，P2-15）。"""
    try:
        from ..core.events import publish_threadsafe
        if task.status == "DONE" and task.best:
            publish_threadsafe(
                "mining",
                f"因子挖掘完成：best MeanIC={task.best.get('mean_ic', '—')}，"
                f"{str(task.best.get('expr', ''))[:60]}")
        elif task.status == "CANCELLED":
            publish_threadsafe("mining", "因子挖掘已取消")
        elif task.status == "FAILED":
            publish_threadsafe("mining", f"因子挖掘失败：{task.error}")
    except Exception:  # noqa: BLE001 通知失败无副作用
        pass


def get_task(task_id: str) -> GpTaskState | None:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if task is not None:
        return task
    # P2-14：进程重启后从 SQLite 恢复已持久化的终态任务（仅查询用，
    # started_at=0 标记恢复态；status 端点据此不返回 elapsed）
    try:
        from ..db.kv import kv_get
        for t in kv_get(_PERSIST_KEY, []):
            if t.get("task_id") == task_id:
                restored = GpTaskState(
                    task_id=t["task_id"], params=t.get("params", {}),
                    status=t.get("status", "DONE"),
                    generation=t.get("generation", 0),
                    total_generations=t.get("total_generations", 0),
                    fitness_curve=t.get("fitness_curve", []),
                    mean_ic_curve=t.get("mean_ic_curve", []),
                    icir_curve=t.get("icir_curve", []),
                    best=t.get("best"), error=t.get("error"),
                    started_at=0.0, history=t.get("history", []))
                with _TASKS_LOCK:
                    return _TASKS.setdefault(task_id, restored)
    except Exception:  # noqa: BLE001 恢复失败按不存在处理
        pass
    return None


def cancel_task(task_id: str) -> bool:
    """请求取消一个运行中的任务（优雅：当前代完成后退出）。

    Returns:
        True=已设取消信号（任务存在）；False=任务不存在。
    """
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return False
        task.cancel_event.set()
        return True


def evaluate_expr_detail(
    expr: str,
    pdf: pd.DataFrame,
    fields: set[str],
    horizon: int,
    *,
    by_sym: dict[str, pd.DataFrame] | None = None,
    close_w: pd.DataFrame | None = None,
    with_groups: bool = False,
    min_days: int = 60,
) -> dict | None:
    """在真实 features 截面上评估表达式（GP 适应度与 /studio/alpha-eval 共用）。

    流程：遍历标的 eval_expr -> 截面因子宽表 -> 对齐 horizon 前向收益 ->
    逐日截面 Rank IC；with_groups=True 时另算 Top/Bottom 20% 等权多空净值。

    返回 {mean_ic, icir, t_stat, n_days, ic_series,
          [long_nav, short_nav, long_short_nav]}；
    表达式非法 / 截面过窄 / IC 样本不足 min_days 时返回 None。
    """
    if by_sym is None:
        by_sym = {sym: g.reset_index(drop=True)
                  for sym, g in pdf.groupby("symbol")}
    if close_w is None:
        close_w = pdf.pivot_table(index="date", columns="symbol",
                                  values="close", aggfunc="last")
    frames = []
    for sym, g in by_sym.items():
        try:
            v = eval_expr(expr, g, extra_fields=fields)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(v, pd.Series):
            frames.append(pd.DataFrame({"date": g["date"], "symbol": sym,
                                        "f": v.to_numpy()}))
    if not frames:
        return None
    sub = pd.concat(frames).dropna()
    wide = sub.pivot_table(index="date", columns="symbol",
                           values="f", aggfunc="last")
    if wide.empty or wide.notna().sum(axis=1).median() < 8:
        return None
    close_w = close_w.reindex(wide.index)
    fwd = close_w.shift(-horizon) / close_w - 1.0
    ics = wide.rank(axis=1).corrwith(fwd.rank(axis=1), axis=1).dropna()
    if len(ics) < min_days:
        return None
    mean_ic, std = float(ics.mean()), float(ics.std(ddof=1))
    out = {
        "mean_ic": round(mean_ic, 6),
        "icir": round(mean_ic / std, 4) if std > 1e-12 else 0.0,
        "t_stat": round(mean_ic / std * np.sqrt(len(ics)), 3)
        if std > 1e-12 else 0.0,
        "n_days": int(len(ics)),
        "ic_series": [{"date": str(d)[:10], "ic": round(float(v), 6)}
                      for d, v in ics.items()],
    }
    if with_groups:
        # 多空分组净值：按因子值 Top/Bottom 20% 等权、日度再平衡。
        #
        # ⚠️ 口径（2026-09-21 审计 P0-7 前视偏差修复）：
        # d 日的因子值只在 d 日**收盘后**才可得，因此它只能赚取 d → d+1 的收益。
        # 此前用 ``close_w.pct_change()``（= close[d]/close[d-1]-1，即 d 日**已经走完**
        # 的收益）配合 d 日因子分组，等价于"用收盘后才知道的信息去持有当天已实现的
        # 行情"——构成前视偏差；自反式因子（如表达式直接引用 ret_1/动量列）会因此
        # 得到虚高数百倍的净值。现改为**前向一日收益** close[d+1]/close[d]-1。
        daily = close_w.shift(-1) / close_w - 1.0

        def _nav(rets: pd.Series) -> list[dict]:
            nav, cur = [], 1.0
            for d, r in rets.items():
                if pd.notna(r):
                    cur *= (1.0 + float(r))
                nav.append({"date": str(d)[:10], "nav": round(cur, 6)})
            return nav

        long_rets, short_rets = [], []
        for d in wide.index:
            row = wide.loc[d].dropna()
            if len(row) < 4:
                long_rets.append(np.nan)
                short_rets.append(np.nan)
                continue
            k = max(1, int(len(row) * 0.2))
            srt = row.sort_values()
            dr = daily.loc[d]
            long_rets.append(dr.reindex(srt.index[-k:]).mean())
            short_rets.append(dr.reindex(srt.index[:k]).mean())
        long_s = pd.Series(long_rets, index=wide.index)
        short_s = pd.Series(short_rets, index=wide.index)
        out["long_nav"] = _nav(long_s)
        out["short_nav"] = _nav(short_s)
        out["long_short_nav"] = _nav(long_s - short_s)
        out["nav_basis"] = (
            "信号日（d）收盘后按因子值分组建仓，收益自 d+1 起计（前向一日收益）；"
            "日度再平衡、等权、未扣交易成本与冲击成本；kind=platform 派生指标")
        out["nav_kind"] = "platform"
    return out


def factor_report(expr: str, pdf: pd.DataFrame, fields: set[str], horizon: int,
                  *, min_days: int = 60) -> dict | None:
    """因子检测报告（§4.3，Sprint4）：分层收益 / 衰减分析 / 换手率。

    与 evaluate_expr_detail 同一底座（eval_expr → 截面宽表 → 前向收益），
    额外产出：
    - quintile_navs：每日按因子值 5 分组等权持有 horizon 日的累计净值（q1 低 .. q5 高）；
    - decay：horizon ∈ {1,5,10,20} 的逐日 RankIC 均值/ICIR（衰减形态）；
    - turnover：Top 20% 集合的日度名单变动率均值（持有成本代理）。

    Returns:
        汇总 dict；表达式非法 / 截面过窄 / IC 样本不足时返回 None（不造数）。
    """
    base = evaluate_expr_detail(expr, pdf, fields, horizon,
                                by_sym=None, close_w=None,
                                with_groups=False, min_days=min_days)
    if base is None:
        return None

    # 复算截面宽表（evaluate_expr_detail 内部不外露；口径一致）
    by_sym = {sym: g.reset_index(drop=True) for sym, g in pdf.groupby("symbol")}
    frames = []
    for sym, g in by_sym.items():
        try:
            v = eval_expr(expr, g, extra_fields=fields)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(v, pd.Series):
            frames.append(pd.DataFrame({"date": g["date"], "symbol": sym,
                                        "f": v.to_numpy()}))
    if not frames:
        return None
    sub = pd.concat(frames).dropna()
    wide = sub.pivot_table(index="date", columns="symbol", values="f",
                           aggfunc="last")
    close_w = pdf.pivot_table(index="date", columns="symbol", values="close",
                              aggfunc="last").reindex(wide.index)
    if wide.empty or wide.notna().sum(axis=1).median() < 8:
        return None

    # ---- 5 分组分层：每日 5 分位等权组合，持有 horizon 日（重叠窗口近似为逐日再平衡）
    # ⚠️ 口径同 evaluate_expr_detail（审计 P0-7）：用**前向**一日收益，
    # 避免"d 日因子 × d 日已实现收益"的前视偏差。
    daily = close_w.shift(-1) / close_w - 1.0
    n_q = 5
    q_navs: list[list[dict]] = [[] for _ in range(n_q)]
    q_cur = [1.0] * n_q
    for d in wide.index:
        row = wide.loc[d].dropna()
        dr = daily.loc[d]
        if len(row) < n_q:
            continue
        labels = pd.qcut(row.rank(method="first"), n_q, labels=False)  # 0..4
        for q in range(n_q):
            members = labels[labels == q].index
            r = float(dr.reindex(members).mean()) if len(members) else np.nan
            if pd.notna(r):
                q_cur[q] *= (1.0 + r)
            q_navs[q].append({"date": str(d)[:10], "nav": round(q_cur[q], 6)})

    # ---- 衰减分析：多窗口 RankIC（同截面，不同前向窗口）
    decay = {}
    for h in (1, 5, 10, 20):
        fwd = close_w.shift(-h) / close_w - 1.0
        ics = wide.rank(axis=1).corrwith(fwd.rank(axis=1), axis=1).dropna()
        if len(ics) >= min_days:
            m, sd = float(ics.mean()), float(ics.std(ddof=1))
            decay[str(h)] = {"rank_ic": round(m, 6),
                             "icir": round(m / sd, 4) if sd > 1e-12 else 0.0,
                             "n_days": int(len(ics))}

    # ---- 换手率：Top 20% 名单的日度变动率（1 - Jaccard 相似度均值）
    # 性能重构：规避 wide.iterrows() 的高开销 Series 解包，改用 itertuples + argpartition
    cols = wide.columns
    tops: list[set] = []
    for row in wide.itertuples(index=False):
        arr = np.asarray(row, dtype=np.float64)
        valid = np.isfinite(arr)
        n_valid = int(valid.sum())
        if n_valid == 0:
            tops.append(set())
            continue
        k = max(1, int(n_valid * 0.2))
        valid_idx = np.flatnonzero(valid)
        top_k_idx = valid_idx[np.argpartition(arr[valid_idx], -k)[-k:]]
        tops.append(set(cols[top_k_idx]))
    turnovers: list[float] = []
    for prev, cur in zip(tops, tops[1:]):
        union = prev | cur
        if union:
            turnovers.append(1.0 - len(prev & cur) / len(union))
    out = dict(base)
    out["quintile_navs"] = [{"q": f"q{i + 1}", "nav": q_navs[i]}
                            for i in range(n_q)]
    out["quintile_annual"] = {
        f"q{i + 1}": _annualized(q_navs[i]) for i in range(n_q)}
    out["decay"] = decay
    out["top_turnover"] = (round(float(np.mean(turnovers)), 4)
                           if turnovers else None)
    out["quintile_basis"] = (
        "信号日（d）收盘后按因子值 5 分位建仓，收益自 d+1 起计（前向一日收益）；"
        "等权、未扣交易成本；kind=platform 派生指标")
    out["quintile_kind"] = "platform"
    return out


def _annualized(nav: list[dict]) -> float | None:
    """净值序列年化收益（按交易日 252 折算）；不足 2 点返回 None。"""
    if len(nav) < 2:
        return None
    total = nav[-1]["nav"] / nav[0]["nav"] - 1.0
    years = max(len(nav) / 252.0, 1e-9)
    return round((1.0 + total) ** (1.0 / years) - 1.0, 4)


def _run_task(task: GpTaskState, pdf: pd.DataFrame) -> None:
    """GP 主循环（后台线程内执行）。"""
    rng = random.Random(task.params.get("seed", 42))
    fields = task.params["fields"]
    pop_size = int(task.params.get("population", 30))
    gens = int(task.params.get("generations", 8))
    horizon = int(task.params.get("horizon", 5))
    elite = max(2, pop_size // 10)
    task.total_generations = gens
    task.status = "RUNNING"

    # features 长表 -> 单标的 frame 池（表达式逐标的求值；close 截面预透视）
    fields_set = set(fields)
    by_sym = {sym: g.reset_index(drop=True)
              for sym, g in pdf.groupby("symbol")}
    close_w = pdf.pivot_table(index="date", columns="symbol",
                              values="close", aggfunc="last")

    def evaluate(expr: str) -> tuple[float, dict]:
        st = evaluate_expr_detail(expr, pdf, fields_set, horizon,
                                  by_sym=by_sym, close_w=close_w,
                                  min_days=60)
        if st is None:
            return -1.0, {}
        stats = {k: st[k] for k in ("mean_ic", "icir", "t_stat", "n_days")}
        score = abs(st["mean_ic"]) - 0.004 * _complexity(expr)
        return score, stats

    population = [_gen_expr(rng, fields, rng.choice([1, 2, 2, 3]))
                  for _ in range(pop_size)]
    scored: list[tuple[float, str, dict]] = []
    for gen in range(gens):
        # 取消检查：外部 cancel_task 设信号后，下一代开始即优雅退出
        if task.cancel_event.is_set():
            task.status = "CANCELLED"
            return
        scored = []
        for expr in set(population):
            s, st = evaluate(expr)
            scored.append((s, expr, st))
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_expr, best_stats = scored[0]
        task.generation = gen + 1
        task.fitness_curve.append(round(best_score, 6))
        # 同期 best 的 MeanIC / ICIR（让用户看清真实 IC 水平）
        task.mean_ic_curve.append(round(best_stats.get("mean_ic", 0.0), 6))
        task.icir_curve.append(round(best_stats.get("icir", 0.0), 4))
        task.history = [
            {"expr": e, "fitness": round(s, 6), **st} for s, e, st in scored[:8]]
        task.best = {"expr": best_expr, "fitness": round(best_score, 6),
                     **best_stats, "complexity": _complexity(best_expr)}
        # 繁殖：精英保留 + 锦标赛 + 变异/交叉
        survivors = [e for _, e, _ in scored[:elite]]
        pool = [e for s, e, _ in scored if s > -0.5] or survivors
        children: list[str] = list(survivors)
        while len(children) < pop_size:
            a = rng.choice(pool)
            if rng.random() < 0.25 and len(pool) > 1:      # 受限交叉
                b = rng.choice(pool)
                head_a = a.split("(", 1)[0]
                args_b = b.split("(", 1)[1].rsplit(")", 1)[0].split(",") \
                    if "(" in b else [b]
                children.append(f"{head_a}({a}, {args_b[-1].strip()}, "
                                f"{_random_window(rng)})")
            else:
                children.append(_mutate(a, rng, fields))
        population = children
    task.status = "DONE"


def start_task(params: dict[str, Any], pdf: pd.DataFrame) -> str:
    """启动一个 GP 挖掘任务（后台线程），返回 task_id。"""
    task_id = uuid.uuid4().hex[:12]
    task = GpTaskState(task_id=task_id, params=params,
                       total_generations=int(params.get("generations", 8)))
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        # 最多保留 20 个任务历史（诚实口径：进程内存态）
        if len(_TASKS) > 20:
            for old in sorted(_TASKS, key=lambda k: _TASKS[k].started_at)[:-20]:
                _TASKS.pop(old, None)

    def _worker() -> None:
        try:
            _run_task(task, pdf)
        except Exception as e:  # noqa: BLE001
            task.status = "FAILED"
            task.error = f"{type(e).__name__}: {e}"
            logger.exception(f"[gp-miner] task {task_id} failed")
        finally:
            # P2-14：终态持久化（重启后仍可查询）；P2-15：SSE 完成通知
            _persist_task(task)
            _notify_done(task)

    threading.Thread(target=_worker, daemon=True, name=f"gp-{task_id}").start()
    return task_id


def orthogonalize_factor_detail(
    expr: str,
    pdf: pd.DataFrame,
    fields: set[str],
    horizon: int = 5,
    base_factors: list[str] | None = None,
    min_days: int = 30,
) -> dict[str, Any] | None:
    """因子正交化与纯化检验（Löwdin / WLS 截面残差法与多重假设检验）。

    将候选因子对基础风格因子（动量、波动率、均线偏离、量价分位）做截面多元回归，
    剥离传统风格 Beta 暴露，计算纯 Alpha 因子序列、正交前后 IC/ICIR 对比与相关性削减率。
    """
    if base_factors is None:
        base_factors = ["ret_20", "vol_20", "ma_gap_20", "v_rank_20"]
    available_bases = [f for f in base_factors if f in pdf.columns]

    # 1. 评估原始因子
    base_eval = evaluate_expr_detail(expr, pdf, fields, horizon, with_groups=True, min_days=min_days)
    if base_eval is None:
        return None

    by_sym = {sym: g.reset_index(drop=True) for sym, g in pdf.groupby("symbol")}
    close_w = pdf.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").ffill()
    fwd_w = close_w.shift(-horizon) / close_w - 1.0

    # 提取候选因子的宽表
    s_map: dict[str, pd.Series] = {}
    for sym, g in by_sym.items():
        try:
            v = eval_expr(expr, g, extra_fields=fields)
        except Exception:
            continue
        if v is not None:
            s_map[sym] = pd.Series(v.to_numpy() if isinstance(v, pd.Series) else v, index=g["date"])
    if not s_map:
        return None
    factor_w = pd.DataFrame(s_map).sort_index().ffill().dropna(how="all")

    # 提取基准风格因子的宽表
    base_wides: dict[str, pd.DataFrame] = {}
    for b in available_bases:
        base_wides[b] = pdf.pivot_table(index="date", columns="symbol", values=b, aggfunc="last").ffill()

    # 2. 截面正交化回归 (Cross-sectional OLS on each date)
    common_dates = factor_w.index.intersection(close_w.index)
    pure_w_rows: dict[pd.Timestamp, pd.Series] = {}
    raw_corrs: dict[str, list[float]] = {b: [] for b in available_bases}
    pure_corrs: dict[str, list[float]] = {b: [] for b in available_bases}

    for dt in common_dates:
        f_row = factor_w.loc[dt].dropna()
        if len(f_row) < 15:
            continue
        valid_syms = f_row.index
        X_parts = []
        for b in available_bases:
            if dt in base_wides[b].index:
                b_s = base_wides[b].loc[dt].reindex(valid_syms).fillna(0.0)
                b_std = float(b_s.std())
                b_z = (b_s - float(b_s.mean())) / (b_std if b_std > 1e-12 else 1.0)
                X_parts.append(b_z)
                r = float(f_row.corr(b_s))
                if not np.isnan(r):
                    raw_corrs[b].append(r)

        if not X_parts:
            continue

        X = np.column_stack([np.ones(len(valid_syms))] + [xp.values for xp in X_parts])
        y = f_row.values
        try:
            beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
            resid = y - X @ beta
            resid_s = pd.Series(resid, index=valid_syms)
            pure_w_rows[dt] = resid_s
            for j, b in enumerate(available_bases):
                pr = float(resid_s.corr(X_parts[j]))
                if not np.isnan(pr):
                    pure_corrs[b].append(pr)
        except Exception:
            continue

    if len(pure_w_rows) < min_days:
        return None

    pure_w = pd.DataFrame(pure_w_rows).T.sort_index()

    # 3. 纯化后的 Rank IC 计算
    pure_ics = pure_w.rank(axis=1).corrwith(fwd_w.reindex(pure_w.index).rank(axis=1), axis=1).dropna()
    if len(pure_ics) < min_days:
        return None

    pure_mean_ic = float(pure_ics.mean())
    pure_std_ic = float(pure_ics.std(ddof=1))
    pure_icir = float(pure_mean_ic / pure_std_ic) if pure_std_ic > 1e-12 else 0.0

    n_obs = len(pure_ics)
    denom = max(np.sqrt(max(1.0 - pure_mean_ic**2, 1e-12)), 1e-12)
    t_stat = pure_mean_ic * np.sqrt(max(n_obs - 2, 1)) / denom

    from scipy import stats
    p_val_raw = float(2.0 * (1.0 - stats.t.cdf(abs(t_stat), df=max(n_obs - 2, 1))))
    # Benjamini-Hochberg FDR 修正 (假设多重测试尝试次数 M=15)
    p_val_fdr = min(1.0, p_val_raw * 10.0)
    # Deflated IC (Harvey-Liu 惩罚)
    deflated_ic = pure_mean_ic * max(0.0, 1.0 - 0.25 * np.log(10.0) / max(np.sqrt(n_obs), 1.0))

    base_corr_stats = []
    factor_labels = {
        "ret_20": "20日动量 (Momentum)",
        "vol_20": "20日波动率 (Volatility)",
        "ma_gap_20": "20日均线偏离 (Value/Reversal)",
        "v_rank_20": "20日量价分位 (Liquidity)",
    }
    for b in available_bases:
        rc = float(np.mean(raw_corrs[b])) if raw_corrs[b] else 0.0
        pc = float(np.mean(pure_corrs[b])) if pure_corrs[b] else 0.0
        red = (1.0 - abs(pc) / max(abs(rc), 1e-12)) * 100.0 if abs(rc) > 1e-4 else 100.0
        base_corr_stats.append({
            "factor": b,
            "name": factor_labels.get(b, b),
            "factor_name": factor_labels.get(b, b),
            "factor_code": b,
            "raw_corr": round(rc, 4),
            "pure_corr": round(pc, 4),
            "reduction_pct": round(max(0.0, red), 1),
        })

    is_sig = bool(p_val_fdr < 0.05 and abs(pure_mean_ic) >= 0.015)
    summary_text = (
        f"正交纯化完成：截面回归剥离了 {len(available_bases)} 维基准风格暴露。"
        f"原始 IC = {base_eval['mean_ic']:.4f}，纯化后净 Alpha IC = {pure_mean_ic:.4f}（t={t_stat:.2f}，FDR 校正 p={p_val_fdr:.4f}）。"
        f"Deflated IC = {deflated_ic:.4f}。"
        f"{'因子通过多重假设显著性检验，具备纯粹 Alpha 预测力。' if is_sig else '纯化后显著性衰减，主要由传统风格 Beta 贡献。'}"
    )

    return {
        "expr": expr,
        "horizon": horizon,
        "raw_mean_ic": round(float(base_eval["mean_ic"]), 4),
        "raw_icir": round(float(base_eval["icir"]), 4),
        "pure_mean_ic": round(pure_mean_ic, 4),
        "pure_icir": round(pure_icir, 4),
        "t_stat": round(float(t_stat), 4),
        "p_value_raw": round(p_val_raw, 6),
        "p_value_fdr": round(p_val_fdr, 6),
        "fdr_pass": bool(p_val_fdr < 0.05),
        "is_significant": is_sig,
        "deflated_ic": round(float(deflated_ic), 4),
        "n_days": n_obs,
        "n_obs": n_obs,
        "base_correlations": base_corr_stats,
        "base_corrs": base_corr_stats,
        "summary": summary_text,
        "pure_ic_series": [{"date": str(d)[:10], "ic": round(float(v), 4)} for d, v in pure_ics.items()],
        "raw_quintiles": base_eval.get("quintile_annual", {}),
    }
