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
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from loguru import logger

from ..domain.metrics import deflated_sharpe_ratio


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

    def top(self, k: int = 5) -> list[dict]:
        """按目标值降序的前 k 个试验（供 API 展示）。"""
        ranked = sorted(self.trials, key=lambda t: t.objective, reverse=True)
        return [{"params": t.params, "objective": round(t.objective, 6),
                 **t.metrics}
                for t in ranked[:k]]


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
) -> SearchResult:
    """网格搜索：参数笛卡尔积逐组合回测（vnpy optimize_parameter_grid 口径）。

    :param evaluate: params -> {"sharpe"/objective字段, "nav"(可选), 其余指标}
    :param max_trials: 组合数上限保护（超过直接抛错，防止误触发天文数字回测）
    """
    keys = list(param_grid.keys())
    combos = list(itertools.product(*(param_grid[k] for k in keys)))
    if not combos:
        raise ValueError("param_grid 不能为空")
    if len(combos) > max_trials:
        raise ValueError(f"网格组合数 {len(combos)} 超过上限 {max_trials}，请缩小参数空间")

    trials: list[Trial] = []
    best: Trial | None = None
    best_nav: np.ndarray | None = None
    best_metrics: dict[str, Any] = {}
    for combo in combos:
        params = dict(zip(keys, combo))
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
    if best is None:
        raise ValueError("所有参数组合均失败")
    return SearchResult(
        best_params=best.params, best_objective=best.objective,
        best_nav=best_nav, best_metrics=best_metrics, trials=trials,
        deflated_sharpe=_dsr_of_best(best_nav, len(trials)), method="grid")


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


@dataclass
class WalkForwardResult:
    """walk-forward 汇总：折外均值 + 过拟合比（IS/OOS 目标比，>1.5 需警惕）。"""

    folds: list[WalkForwardFold]
    mean_oos_objective: float
    mean_is_objective: float
    overfit_ratio: float


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
        （显著大于 1 说明寻优过拟合，参数不可信）。
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
        folds.append(WalkForwardFold(
            fold=i, is_window=(is_s, is_e), oos_window=(oos_s, oos_e),
            best_params=search.best_params, is_objective=search.best_objective,
            oos_objective=float(oos.get(objective, float("nan"))),
            n_trials=len(search.trials)))
    mean_is = float(np.nanmean([f.is_objective for f in folds]))
    mean_oos = float(np.nanmean([f.oos_objective for f in folds]))
    ratio = (mean_is / mean_oos
             if abs(mean_oos) > 1e-12 and mean_is == mean_is else float("nan"))
    return WalkForwardResult(folds=folds, mean_is_objective=mean_is,
                             mean_oos_objective=mean_oos,
                             overfit_ratio=ratio)


def run_search(
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
    optimize_params: dict[str, list[Any]],
    method: str = "grid",
    objective: str = "sharpe",
    ga_population: int = 20,
    ga_generations: int = 10,
    optuna_trials: int = 30,
) -> SearchResult:
    """统一入口：grid -> 笛卡尔积；ga -> 边界区间；optuna -> TPE 贝叶斯。"""
    if method == "grid":
        return grid_search(evaluate, optimize_params, objective=objective)
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
