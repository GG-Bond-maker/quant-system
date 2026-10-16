"""策略参数寻优（参考 vnpy 的 OptimizeSetting：grid / genetic 两种模式）。

vnpy 的参数优化用遗传算法在参数空间搜索最优回测配置；本模块在同样
能力之上加了**防过拟合闭环**：
    - 每个参数组合 = 一次"试验"（trial）；
    - 搜索结束后对最优净值计算 **Deflated Sharpe Ratio**
      （n_trials = 实际试验次数），报告"考虑了试了这么多次之后，
      这个夏普还有多大概率是真的"—— 搜索空间越大，DSR 越低。

用法::

    def evaluate(params: dict) -> dict:      # 返回至少含 sharpe 与 nav
        res = run_strategy(bars, MaCrossStrategy(**params), ...)
        return {"sharpe": res.risk["sharpe"], "nav": nav, ...}

    best = grid_search(evaluate, {"short_ma": [5, 10], "long_ma": [20, 60]})
    best = genetic_search(evaluate, {"short_ma": (3, 30), "long_ma": (20, 120)})
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from loguru import logger

from ..domain.metrics import deflated_sharpe_ratio
from .opt_checkpoint import combo_key as _combo_key


@dataclass
class Trial:
    """单次参数试验记录。"""

    params: dict[str, Any]
    objective: float
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """寻优结果：最优参数 + 全部试验 + DSR 防过拟合结论。"""

    best_params: dict[str, Any]
    best_objective: float
    best_nav: np.ndarray | None
    best_metrics: dict[str, Any]
    trials: list[Trial]
    deflated_sharpe: float          # 对 best_nav 按 n_trials=len(trials) 计
    method: str
    n_skipped: int = 0              # 续跑跳过的组合数（P8-c；0=全新搜索）

    def top(self, k: int = 5) -> list[dict]:
        """按目标值降序的前 k 个试验（供 API 展示）。"""
        ranked = sorted(self.trials, key=lambda t: t.objective, reverse=True)
        return [{"params": t.params, "objective": round(t.objective, 6),
                 **t.metrics}
                for t in ranked[:k]]


@dataclass
class SearchHooks:
    """网格搜索的**可选**外部钩子（P8-c，2026-10-04 上线前全检）。

    两个钩子都刻意设为可选，默认 ``None`` ⇒ 不传时行为与改动前**逐字节一致**。

    * ``should_cancel``：**每个组合跑之前**调用一次；返回真则抛
      :class:`~app.backtest.opt_checkpoint.CancelledSearch` 停止搜索。
      粒度是"一个组合"，故最坏多跑一个组合 —— 这是刻意取舍：不动
      ``backtest/engine.py`` 的日循环，回归面最小。
    * ``on_trial``：每完成一个组合后调用一次，用于落**检查点**
      （参数 / 目标值 / 指标），使同请求重试可跳过已完成的组合。

    为什么用回调而不是让本模块直接依赖 ``opt_checkpoint``：
    保持本模块的**纯算法**定位（它现在只依赖 numpy/loguru），
    且让"检查点存哪里"成为调用方的决定 —— 测试里可以直接传内存假实现。
    """

    should_cancel: Callable[[], bool] | None = None
    on_trial: Callable[[dict[str, Any], float, dict[str, Any]], None] | None = None


def _dsr_of_best(nav: np.ndarray | None, n_trials: int) -> float:
    if nav is None or n_trials < 1:
        return float("nan")
    try:
        return float(deflated_sharpe_ratio(nav, n_trials=n_trials))
    except (ValueError, ZeroDivisionError):
        return float("nan")


def grid_search(
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
    param_grid: dict[str, list[Any]],
    objective: str = "sharpe",
    max_trials: int = 500,
    hooks: SearchHooks | None = None,
    skip_trials: dict[str, tuple[float, dict[str, Any]]] | None = None,
    n_jobs: int = 1,
) -> SearchResult:
    """网格搜索：参数笛卡尔积逐组合回测（vnpy optimize_parameter_grid 口径）。

    :param evaluate: params -> {"sharpe"/objective字段, "nav"(可选), 其余指标}
    :param max_trials: 组合数上限保护（超过直接抛错，防止误触发天文数字回测）
    :param hooks: 可选的取消/检查点钩子（见 :class:`SearchHooks`）；``None``
        时行为与改动前完全一致。
    :param skip_trials: **续跑**用：``{组合稳定键: (objective, metrics)}``。
        命中则**不重跑**该组合，直接复用既有结果回填 trials/best。
        只为"同一请求重试"设计，键由 ``opt_checkpoint.combo_key`` 生成。
    :param n_jobs: 并行工作线程数（默认 1 为确定性串行，>1 时启用多线程分批并行加速）。

    .. note::
       ``skip_trials`` 里的组合**不**再调用 ``evaluate``，因此也拿不到
       ``nav``。这会影响 Deflated Sharpe（它需要 best 的 nav）——
       故命中续跑时，若最优解来自跳过的组合，DSR 按历史 ``metrics`` 里的
       ``nav`` 缺失处理（返回 NaN，如实表示"本次未重算、无法给出 DSR"），
       **绝不编造**。
    """
    keys = list(param_grid.keys())
    # 先按长度求积判上限，**不得先物化**笛卡尔积：物化 3 键 × 1000 候选会产生
    # 1e9 个元组（≈72GB）把 API 进程打死，且守卫是在物化之后才执行的。
    total = math.prod(len(param_grid[k]) for k in keys) if keys else 1
    if total == 0:
        raise ValueError("param_grid 不能为空")
    if total > max_trials:
        raise ValueError(f"网格组合数 {total} 超过上限 {max_trials}，请缩小参数空间")
    combos = itertools.product(*(param_grid[k] for k in keys))

    hooks = hooks or SearchHooks()
    skip = skip_trials or {}
    trials: list[Trial] = []
    best: Trial | None = None
    best_nav: np.ndarray | None = None
    best_metrics: dict[str, Any] = {}
    n_skipped = 0

    if n_jobs > 1:
        from concurrent.futures import ThreadPoolExecutor

        pending_params: list[dict[str, Any]] = []
        for combo in combos:
            params = dict(zip(keys, combo))
            if skip:
                ck = _combo_key(params)
                hit = skip.get(ck)
                if hit is not None:
                    obj_prev, metrics_prev = hit
                    t_prev = Trial(params=params, objective=obj_prev,
                                   metrics=dict(metrics_prev))
                    trials.append(t_prev)
                    n_skipped += 1
                    if np.isfinite(obj_prev) and (best is None or obj_prev > best.objective):
                        best = t_prev
                        best_nav = None
                        best_metrics = {}
                    continue
            pending_params.append(params)

        def _eval_one(p: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
            try:
                return p, evaluate(p)
            except Exception as e:
                logger.warning(f"[grid] {p} evaluate 失败: {e!r}")
                return p, None

        with ThreadPoolExecutor(max_workers=n_jobs, thread_name_prefix="grid-opt") as pool:
            batch_size = max(1, n_jobs)
            for batch_start in range(0, len(pending_params), batch_size):
                if hooks.should_cancel is not None and hooks.should_cancel():
                    from .opt_checkpoint import CancelledSearch

                    logger.info(
                        f"[grid] 收到取消标志：已完成 {len(trials)} 个组合"
                        f"（跳过续跑 {n_skipped} 个），停止搜索并返回已完成部分")
                    raise CancelledSearch(
                        f"寻优已被取消（已完成 {len(trials)} 个组合）")
                batch = pending_params[batch_start:batch_start + batch_size]
                for p, out in pool.map(_eval_one, batch):
                    if out is None:
                        continue
                    obj = float(out.get(objective, float("nan")))
                    t = Trial(params=p, objective=obj,
                              metrics={k: v for k, v in out.items()
                                       if k not in (objective, "nav") and
                                       (isinstance(v, (int, float, str, bool)))})
                    trials.append(t)
                    if np.isfinite(obj) and (best is None or obj > best.objective):
                        best = t
                        best_nav = out.get("nav")
                        best_metrics = {k: v for k, v in out.items() if k != "nav"}
                    if hooks.on_trial is not None:
                        try:
                            hooks.on_trial(p, obj, t.metrics)
                        except Exception as e:
                            logger.warning(f"[grid] on_trial 落检查点失败: {e!r}")
    else:
        for combo in combos:
            params = dict(zip(keys, combo))

            # ---- 续跑：命中检查点则跳过重算（P8-c）----
            if skip:
                ck = _combo_key(params)
                hit = skip.get(ck)
                if hit is not None:
                    obj_prev, metrics_prev = hit
                    t_prev = Trial(params=params, objective=obj_prev,
                                   metrics=dict(metrics_prev))
                    trials.append(t_prev)
                    n_skipped += 1
                    # 最优解选取与下面同口径（仅用有限目标值）
                    if np.isfinite(obj_prev) and (best is None or obj_prev > best.objective):
                        best = t_prev
                        # 跳过的组合没有 nav ⇒ DSR 如实为 NaN，不编造
                        best_nav = None
                        best_metrics = {}
                    continue

            # ---- 取消检查：**每个组合跑之前**（粒度=一个组合）----
            if hooks.should_cancel is not None and hooks.should_cancel():
                from .opt_checkpoint import CancelledSearch

                logger.info(
                    f"[grid] 收到取消标志：已完成 {len(trials)} 个组合"
                    f"（跳过续跑 {n_skipped} 个），停止搜索并返回已完成部分")
                raise CancelledSearch(
                    f"寻优已被取消（已完成 {len(trials)} 个组合）")

            try:
                out = evaluate(params)
            except Exception as e:  # noqa: BLE001  单组合失败不阻断搜索
                logger.warning(f"[grid] {params} evaluate 失败: {e!r}")
                continue
            obj = float(out.get(objective, float("nan")))
            t = Trial(params=params, objective=obj,
                      metrics={k: v for k, v in out.items()
                               if k not in (objective, "nav") and
                               (isinstance(v, (int, float, str, bool)))})
            trials.append(t)
            if np.isfinite(obj) and (best is None or obj > best.objective):
                best = t
                best_nav = out.get("nav")
                best_metrics = {k: v for k, v in out.items() if k != "nav"}
            # ---- 检查点：完成一个就记一个（进程被杀/超时后可续跑）----
            if hooks.on_trial is not None:
                try:
                    hooks.on_trial(params, obj, t.metrics)
                except Exception as e:  # noqa: BLE001 落检查点失败绝不阻断搜索
                    logger.warning(f"[grid] on_trial 落检查点失败: {e!r}")
    if best is None:
        raise ValueError("所有参数组合均失败")
    if n_skipped:
        logger.info(f"[grid] 续跑：跳过 {n_skipped} 个已完成组合，本次实算 "
                    f"{len(trials) - n_skipped} 个")
    return SearchResult(
        best_params=best.params, best_objective=best.objective,
        best_nav=best_nav, best_metrics=best_metrics, trials=trials,
        deflated_sharpe=_dsr_of_best(best_nav, len(trials)), method="grid",
        n_skipped=n_skipped)


def genetic_search(
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
    param_bounds: dict[str, tuple[float, float]],
    population: int = 20,
    generations: int = 10,
    objective: str = "sharpe",
    seed: int = 42,
    crossover_rate: float = 0.7,
    mutation_rate: float = 0.2,
) -> SearchResult:
    """实数编码遗传算法（vnpy optimize_parameter_ga 口径，numpy 实现）。

    流程：随机初始种群 -> 锦标赛选择 -> 混合交叉（BLX-α）-> 高斯变异 ->
    逐代保留最优。整型参数四舍五入并夹到边界。

    :param param_bounds: {参数名: (下界, 上界)}
    """
    if not param_bounds:
        raise ValueError("param_bounds 不能为空")
    rng = np.random.default_rng(seed)
    keys = list(param_bounds.keys())
    lo = np.array([float(param_bounds[k][0]) for k in keys])
    hi = np.array([float(param_bounds[k][1]) for k in keys])
    if np.any(hi < lo):
        raise ValueError("param_bounds 中存在上界 < 下界")
    int_like = {k: float(param_bounds[k][0]).is_integer()
                and float(param_bounds[k][1]).is_integer() for k in keys}

    def _decode(genes: np.ndarray) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for i, k in enumerate(keys):
            v = float(np.clip(genes[i], lo[i], hi[i]))
            out[k] = int(round(v)) if int_like[k] else v
        return out

    def _fitness(genes: np.ndarray) -> tuple[float, dict[str, Any], np.ndarray | None]:
        params = _decode(genes)
        out = evaluate(params)
        return float(out.get(objective, float("nan"))), out, out.get("nav")

    # ---- 初始种群 ----
    pop = lo + rng.random((population, len(keys))) * (hi - lo)
    scores = np.full(population, -np.inf)
    cache: dict[tuple, tuple[float, dict, np.ndarray | None]] = {}

    def _score(genes: np.ndarray) -> tuple[float, dict, np.ndarray | None]:
        key = tuple(np.round(genes, 10))
        if key not in cache:
            cache[key] = _fitness(genes)
        return cache[key]

    best_genes, best_obj, best_out, best_nav = None, -np.inf, {}, None
    for gen in range(generations):
        for i, g in enumerate(pop):
            obj, out, nav = _score(g)
            scores[i] = obj
            if np.isfinite(obj) and obj > best_obj:
                best_genes, best_obj, best_out, best_nav = g.copy(), obj, out, nav
        # ---- 锦标赛选择 ----
        idx1 = rng.integers(0, population, population)
        idx2 = rng.integers(0, population, population)
        winners = np.where(scores[idx1] >= scores[idx2], idx1, idx2)
        parents = pop[winners]
        # ---- 混合交叉 BLX-α ----
        children = parents.copy()
        for c in range(0, population - 1, 2):
            if rng.random() < crossover_rate:
                alpha = rng.random(len(keys))
                children[c] = alpha * parents[c] + (1 - alpha) * parents[c + 1]
                children[c + 1] = alpha * parents[c + 1] + (1 - alpha) * parents[c]
        # ---- 高斯变异 ----
        span = hi - lo
        mask = rng.random(children.shape) < mutation_rate
        noise = rng.normal(0, 0.1, children.shape) * span
        children = np.where(mask, children + noise, children)
        children = np.clip(children, lo, hi)
        # 精英保留
        if best_genes is not None:
            children[0] = best_genes
        pop = children
        logger.debug(f"[ga] gen={gen} best={best_obj:.4f}")

    if best_genes is None:
        raise ValueError("遗传算法未找到任何有效解")
    return SearchResult(
        best_params=_decode(best_genes), best_objective=best_obj,
        best_nav=best_nav, best_metrics={k: v for k, v in best_out.items()
                                         if k != "nav"},
        # cache 的键是基因元组，需还原为参数字典（与 best_params 同口径）
        trials=[Trial(params=_decode(np.asarray(p, dtype=float)), objective=o,
                      metrics={k: v for k, v in m.items() if k != "nav"})
                for p, (o, m, _) in cache.items()],
        deflated_sharpe=_dsr_of_best(best_nav, len(cache)), method="ga")


def optuna_search(
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
    param_grid: dict[str, list[Any]],
    objective: str = "sharpe",
    n_trials: int = 30,
    seed: int = 42,
) -> SearchResult:
    """Optuna TPE 贝叶斯寻优（§4.4，Sprint4-遗留项闭环）。

    网格值视为离散候选（suggest_categorical，保持与 grid 相同的参数空间语义）；
    TPE 以历史试验自适应采样，通常以远小于网格积的试验数逼近最优。
    失败试验记为 Fail（不计入 DSR 的有效试验口径时按实际完成数）。
    """
    try:
        import optuna
    except ImportError as e:  # pragma: no cover
        raise ValueError("optuna 未安装：pip install optuna") from e

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    keys = list(param_grid.keys())
    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    trials: list[Trial] = []
    best: Trial | None = None
    best_nav: np.ndarray | None = None
    best_metrics: dict[str, Any] = {}

    def _objective(trial: "optuna.Trial") -> float:
        nonlocal best, best_nav, best_metrics
        params = {k: trial.suggest_categorical(k, param_grid[k]) for k in keys}
        out = evaluate(params)
        obj = float(out.get(objective, float("nan")))
        t = Trial(params=params, objective=obj,
                  metrics={k: v for k, v in out.items()
                           if k not in (objective, "nav") and
                           (isinstance(v, (int, float, str, bool)))})
        trials.append(t)
        if np.isfinite(obj) and (best is None or obj > best.objective):
            best = t
            best_nav = out.get("nav")
            best_metrics = {k: v for k, v in out.items() if k != "nav"}
        return obj if np.isfinite(obj) else float("nan")

    study.optimize(_objective, n_trials=max(1, n_trials), show_progress_bar=False)
    if best is None:
        raise ValueError("所有 optuna 试验均失败")
    return SearchResult(
        best_params=best.params, best_objective=best.objective,
        best_nav=best_nav, best_metrics=best_metrics, trials=trials,
        deflated_sharpe=_dsr_of_best(best_nav, len(trials)), method="optuna")


@dataclass
class WalkForwardFold:
    """单折 walk-forward 结果：IS 段寻优 → OOS 段折外验证。"""

    fold: int
    is_window: tuple[Any, Any]
    oos_window: tuple[Any, Any]
    best_params: dict[str, Any]
    is_objective: float
    oos_objective: float
    n_trials: int
    oos_nav: np.ndarray | None = None


@dataclass
class WalkForwardResult:
    """walk-forward 汇总：折外均值 + 过拟合比 + 步进效率 (WFE) + 链式 OOS 净值。"""

    folds: list[WalkForwardFold]
    mean_oos_objective: float
    mean_is_objective: float
    overfit_ratio: float
    wfe: float = 0.0          # Walk-Forward Efficiency (Pardo 2008): mean_oos / mean_is
    chained_oos_nav: np.ndarray | None = None
    chained_oos_metrics: dict[str, Any] = field(default_factory=dict)


def generate_walk_forward_windows(
    total_periods: int,
    is_size: int,
    oos_size: int,
    step: int | None = None,
    expanding: bool = False,
) -> list[tuple[int, int, int, int]]:
    """生成 Walk-Forward 滚动 / 扩张验证窗口（严格按时序防泄漏）。

    :param total_periods: 总时间步数（交易日数）
    :param is_size: 样本内（IS）回看窗口长度
    :param oos_size: 样本外（OOS）测试窗口长度
    :param step: 步进长度（默认等于 oos_size，无缝衔接）
    :param expanding: False 为滚动窗口 (Rolling)，True 为扩张窗口 (Expanding)
    :return: [(is_start, is_end, oos_start, oos_end), ...]
    """
    if is_size < 5 or oos_size < 1:
        raise ValueError(f"is_size={is_size} 必须 >= 5, oos_size={oos_size} 必须 >= 1")
    stride = step if step is not None and step > 0 else oos_size
    windows: list[tuple[int, int, int, int]] = []

    start = 0
    while True:
        is_start = 0 if expanding else start
        is_end = start + is_size
        oos_start = is_end
        oos_end = oos_start + oos_size
        if oos_end > total_periods:
            break
        windows.append((is_start, is_end, oos_start, oos_end))
        start += stride

    return windows


def walk_forward_search(
    evaluate_window: Callable[[Any, Any], Callable[[dict[str, Any]], dict[str, Any]]],
    windows: list[tuple[Any, Any, Any, Any]],
    param_grid: dict[str, list[Any]],
    objective: str = "sharpe",
    method: str = "optuna",
    n_trials_per_fold: int = 20,
) -> WalkForwardResult:
    """walk-forward 折外验证（§4.4 防过拟合闭环）。

    Args:
        evaluate_window: ``(is_start, is_end) -> evaluate(params)``——调用方按
            窗口切片数据后返回该窗口的评估闭包（数据切片由业务层完成）。
        windows: 每折 ``(is_start, is_end, oos_start, oos_end)``，语义由调用方
            定义（通常为交易日或日期）。
        param_grid: 离散参数空间（与 optuna_search/grid 相同语义）。
        method: 每折 IS 段的寻优方法（optuna/grid/ga）。
        n_trials_per_fold: optuna 每折试验数。

    Returns:
        折外汇总：mean_oos（真实样本外表现）与 overfit_ratio = mean_IS/mean_OOS
        （显著大于 1 说明寻优过拟合，参数不可信），以及 WFE 与拼接后的链式样本外净值。
    """
    folds: list[WalkForwardFold] = []
    for i, (is_s, is_e, oos_s, oos_e) in enumerate(windows, start=1):
        evaluate = evaluate_window(is_s, is_e)
        if method == "optuna":
            search = optuna_search(evaluate, param_grid, objective=objective,
                                   n_trials=n_trials_per_fold)
        else:
            search = run_search(evaluate, param_grid, method=method,
                                objective=objective)
        # 折外：最优参数在 OOS 窗口的真实表现（无搜索偏置）
        oos_evaluate = evaluate_window(oos_s, oos_e)
        oos = oos_evaluate(search.best_params)
        raw_nav = oos.get("nav")
        folds.append(WalkForwardFold(
            fold=i, is_window=(is_s, is_e), oos_window=(oos_s, oos_e),
            best_params=search.best_params, is_objective=search.best_objective,
            oos_objective=float(oos.get(objective, float("nan"))),
            n_trials=len(search.trials),
            oos_nav=raw_nav if isinstance(raw_nav, np.ndarray) else None))
    mean_is = float(np.nanmean([f.is_objective for f in folds]))
    mean_oos = float(np.nanmean([f.oos_objective for f in folds]))
    ratio = (mean_is / mean_oos
             if abs(mean_oos) > 1e-12 and mean_is == mean_is else float("nan"))
    wfe = (mean_oos / mean_is
           if abs(mean_is) > 1e-12 and np.isfinite(mean_is) and np.isfinite(mean_oos)
           else float("nan"))

    # 链式拼接样本外（OOS）真实连续净值
    from ..domain.metrics import annual_return, max_drawdown, sharpe_ratio

    chained_nav: np.ndarray | None = None
    chained_metrics: dict[str, Any] = {}
    valid_rets: list[np.ndarray] = []
    for f in folds:
        if f.oos_nav is not None and len(f.oos_nav) >= 2:
            arr = np.asarray(f.oos_nav, dtype=np.float64)
            with np.errstate(divide="ignore", invalid="ignore"):
                rets = np.diff(arr) / arr[:-1]
            rets = rets[np.isfinite(rets)]
            if len(rets) > 0:
                valid_rets.append(rets)

    if valid_rets:
        all_rets = np.concatenate(valid_rets)
        chained_nav = np.cumprod(np.insert(1.0 + all_rets, 0, 1.0))
        mdd, _, _ = max_drawdown(chained_nav)
        chained_metrics = {
            "sharpe": round(float(sharpe_ratio(chained_nav)), 4),
            "max_drawdown": round(float(mdd), 4),
            "annual_return": round(float(annual_return(chained_nav)), 4),
            "total_return": round(float(chained_nav[-1] - 1.0), 4),
            "n_days": int(len(all_rets)),
        }

    return WalkForwardResult(folds=folds, mean_is_objective=mean_is,
                             mean_oos_objective=mean_oos,
                             overfit_ratio=ratio,
                             wfe=wfe,
                             chained_oos_nav=chained_nav,
                             chained_oos_metrics=chained_metrics)


def run_search(
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
    optimize_params: dict[str, list[Any]],
    method: str = "grid",
    objective: str = "sharpe",
    ga_population: int = 20,
    ga_generations: int = 10,
    optuna_trials: int = 30,
    hooks: SearchHooks | None = None,
    skip_trials: dict[str, tuple[float, dict[str, Any]]] | None = None,
    n_jobs: int = 1,
) -> SearchResult:
    """统一入口：grid -> 笛卡尔积；ga -> 边界区间；optuna -> TPE 贝叶斯。

    ``hooks`` / ``skip_trials``（P8-c）与 ``n_jobs`` **仅对 grid 生效**并透传给
    :func:`grid_search`；ga / optuna 路径忽略它们（保持既有语义不变）。
    """
    if method == "grid":
        return grid_search(evaluate, optimize_params, objective=objective,
                           hooks=hooks, skip_trials=skip_trials, n_jobs=n_jobs)
    if method == "optuna":
        return optuna_search(evaluate, optimize_params, objective=objective,
                             n_trials=optuna_trials)
    if method == "ga":
        bounds: dict[str, tuple[float, float]] = {}
        for k, vals in optimize_params.items():
            if not vals:
                raise ValueError(f"参数 {k} 的候选列表为空")
            nums = [float(v) for v in vals]
            bounds[k] = (min(nums), max(nums))
        return genetic_search(evaluate, bounds, population=ga_population,
                              generations=ga_generations, objective=objective)
    raise ValueError(f"未知寻优方法: {method!r}（可选 grid/ga/optuna）")
