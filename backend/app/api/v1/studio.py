"""因子自动化挖掘工作室 API（真实 GP：语法生成 + RankIC 适应度 + 进化）。"""
from __future__ import annotations

import asyncio
import threading
from datetime import timedelta

import polars as pl
from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field

from ...core.auth import require_role
from ...core.config import get_settings
from ...data.features import assert_unique_feature_rows, feature_files, resolve_feature_version
from ...data.parquet_store import as_py_date
from ...core.errors import (APIResponse, AQPException, ERR_DATA_EMPTY,
                            ERR_LLM_UNAVAILABLE, ok)
from ...ml import gp_miner

router = APIRouter()


def _load_snapshot(days: int = 520, version: str | None = None) -> pl.DataFrame:
    """features 近 N 交易日快照（挖掘适应度的真实数据源；带进程级缓存）。

    P2-12：features 只在每日因子构建后变化，以（文件名/mtime/size）作签名，
    未变化时直接复用上次 concat 结果，避免 mining/start 与 alpha-eval
    每次全量读取 48MB parquet + concat（大内存峰值 + 数秒耗时）。
    只保留当前窗口一份缓存（不同 days 重建即可，避免多份大表常驻内存）。
    """
    selected_version = resolve_feature_version(version)
    _version, files = feature_files(selected_version)
    sig = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    cache_key = (selected_version, days)
    with _SNAP_LOCK:
        hit = _SNAP_CACHE.get(cache_key)
        if hit and hit[0] == sig:
            return hit[1]
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    assert_unique_feature_rows(df, selected_version)
    dcol = df.schema["date"]
    if dcol != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))
    dmax = as_py_date(df["date"].max())
    df = df.filter(pl.col("date") >= dmax - timedelta(days=int(days * 1.6)))
    with _SNAP_LOCK:
        _SNAP_CACHE.clear()
        _SNAP_CACHE[cache_key] = (sig, df)
    return df


# 快照缓存： (version, days) -> (文件签名, DataFrame)。
_SNAP_CACHE: dict[tuple[str, int], tuple[tuple, pl.DataFrame]] = {}
_SNAP_LOCK = threading.Lock()


class MiningRequest(BaseModel):
    fields: list[str] = Field(..., min_length=2, max_length=6,
                              description="参与挖掘的 features 字段")
    horizon: int = Field(5, ge=1, le=20)
    population: int = Field(30, ge=10, le=80)
    generations: int = Field(8, ge=3, le=20)
    seed: int = Field(42, ge=0, le=2**31 - 1)


@router.post("/mining/start", response_model=APIResponse[dict])
async def start_mining(
    req: MiningRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """启动 GP 挖掘任务（后台线程，真实进化；适应度 = 真实 RankIC）。"""
    pdf = await asyncio.to_thread(_load_snapshot)
    missing = [f for f in req.fields if f not in pdf.columns]
    if missing:
        raise AQPException(ERR_DATA_EMPTY, f"features 缺少字段: {missing}")
    # 只保留挖掘需要的列（close 用于前向收益）
    keep = ["symbol", "date", "close", *req.fields]
    pdf = pdf.select(keep)
    params = {"fields": req.fields, "horizon": req.horizon,
              "population": req.population, "generations": req.generations,
              "seed": req.seed}
    task_id = await asyncio.to_thread(gp_miner.start_task, params,
                                      pdf.to_pandas())
    logger.info(f"[studio] GP task {task_id} started: {params}")
    return ok({"task_id": task_id, "params": params,
               "snapshot_rows": pdf.height,
               "snapshot_end": str(pdf["date"].max())[:10]})


@router.get("/mining/status/{task_id}", response_model=APIResponse[dict])
async def mining_status(
    task_id: str,
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    task = gp_miner.get_task(task_id)
    if task is None:
        raise AQPException(
            ERR_DATA_EMPTY, "任务不存在（运行中的任务会随服务重启丢失；"
            "已完成任务的最终结果已持久化，可直接查询）")
    return ok({
        "task_id": task.task_id, "status": task.status,
        "generation": task.generation, "total_generations": task.total_generations,
        "fitness_curve": task.fitness_curve,
        # 同期 best 的 MeanIC / ICIR 曲线（让用户看清真实 IC 水平）
        "mean_ic_curve": task.mean_ic_curve,
        "icir_curve": task.icir_curve,
        "best": task.best, "top_expressions": task.history,
        "error": task.error,
        # started_at=0 表示从 SQLite 恢复的历史任务（重启前已完成），无 elapsed
        "elapsed_sec": (round(__import__("time").monotonic() - task.started_at, 1)
                        if task.started_at else None),
    })


@router.post("/mining/cancel/{task_id}", response_model=APIResponse[dict])
async def cancel_mining(
    task_id: str,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """请求取消一个运行中的挖掘任务（优雅：当前代完成后退出）。

    Args:
        task_id: 要取消的任务 ID。

    Returns:
        cancelled=True 表示已设取消信号（任务存在）；False 表示任务不存在。
    """
    cancelled = await asyncio.to_thread(gp_miner.cancel_task, task_id)
    if not cancelled:
        raise AQPException(ERR_DATA_EMPTY, "任务不存在（可能已完成或服务重启）")
    logger.info(f"[studio] cancel requested for task {task_id}")
    return ok({"cancelled": True, "task_id": task_id})


class AlphaEvalRequest(BaseModel):
    expr: str = Field(..., min_length=1, max_length=500,
                      description="Alpha 表达式（算子白名单同表达式引擎）")
    horizon: int = Field(5, ge=1, le=20)
    # 可选：限定标的列表（默认全 universe）；代码自动归一化为 XXXXXX.SH/SZ
    symbols: list[str] | None = Field(None, max_length=50)


class NLFactorRequest(BaseModel):
    text: str = Field(..., min_length=4, max_length=500,
                      description="自然语言因子描述")
    horizon: int = Field(5, ge=1, le=20)


def _extract_expression(content: str) -> str:
    """从 LLM 回复中提取表达式：优先代码围栏，否则取首个非空行。"""
    import re

    fenced = re.search(r"```[a-zA-Z]*\s*\n(.+?)\n```", content, re.S)
    raw = fenced.group(1) if fenced else content.strip().splitlines()[0]
    raw = raw.strip().strip("`\"' ")
    # 常见前缀清理（"表达式：..." / "Expression: ..."）
    raw = re.sub(r"^(?:表达式|Expression|Alpha)\s*[:：]\s*", "", raw, flags=re.I)
    return raw.strip()


def _call_llm(text: str, fields: set[str]) -> tuple[str, str]:
    """调用 LLM 生成表达式，返回 (expression, model_used)。

    走共享 core.llm 客户端（ollama / openai 兼容）；失败抛 AQPException
    （统一信封脱敏，不泄漏 URL/密钥细节）。
    """
    from ...core.llm import chat

    s = get_settings()
    ops = "、".join(sorted(_alpha_ops()))
    field_list = "、".join(sorted(fields)[:80])
    system = (
        "你是 AQP 量化平台的因子工程师。把用户的自然语言因子逻辑编译为一个 Alpha 表达式。\n"
        f"可用算子（函数调用形式，窗口参数为整数）：{ops}。\n"
        f"可用字段（只能使用这些名字）：{field_list}。\n"
        "规则：只输出一个表达式，不要任何解释、不要 markdown 修饰；"
        "字段名必须逐字来自可用字段列表；算子参数数量必须正确"
        "（滚动类算子如 Ref/Mean/Std/Corr 的最后一个参数是窗口整数）。")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]
    content = chat(messages)
    return _extract_expression(content), s.LLM_MODEL


def _alpha_ops() -> set[str]:
    """表达式引擎算子白名单（供 Prompt 生成）。"""
    from ...ml.alpha_expr import OPS

    return set(OPS)


@router.post("/nl-to-factor", response_model=APIResponse[dict])
async def nl_to_factor(
    req: NLFactorRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """自然语言 → Alpha 表达式（LLM 只产 DSL 文本；AST 白名单 + 真实数据校验）。

    流程：LLM 生成 → alpha_expr.parse_expr 白名单静态校验（拒绝属性访问/
    下标/未知算子与字段，防注入非法代码）→ 通过后用 gp_miner.evaluate_expr_detail
    在真实 features 截面评估 RankIC（与 /alpha-eval 同一套口径）。
    LLM 未配置/不可用时返回统一错误信封（code 53000）。
    """
    s = get_settings()
    if s.LLM_PROVIDER == "none":
        raise AQPException(ERR_LLM_UNAVAILABLE,
                           "未配置 LLM（.env 设置 LLM_PROVIDER=ollama|openai 后启用）")
    pdf = await asyncio.to_thread(_load_snapshot)
    fields = set(pdf.columns) - {"symbol", "date"}
    expr, model_used = await asyncio.to_thread(_call_llm, req.text, fields)

    from ...ml.alpha_expr import parse_expr

    result: dict = {"provider": s.LLM_PROVIDER, "model": model_used,
                    "expression": expr, "horizon": req.horizon}
    try:
        parse_expr(expr, extra_fields=fields)
        result["valid"] = True
    except ValueError as e:
        result.update({"valid": False, "error": str(e)})
        logger.info(f"[studio] nl-to-factor 校验失败: {expr!r} ({e})")
        return ok(result)

    # 语法合法 → 真实截面评估（与 alpha-eval 同口径；样本不足返回 None）
    def _eval() -> dict | None:
        return gp_miner.evaluate_expr_detail(
            expr, pdf.to_pandas(), fields, req.horizon, with_groups=False,
            min_days=30)

    evaluation = await asyncio.to_thread(_eval)
    if evaluation is not None:
        evaluation.pop("long_nav", None)
        evaluation.pop("short_nav", None)
    result["evaluation"] = evaluation
    if evaluation is None:
        result["error"] = "表达式合法但有效 IC 样本不足（<30 日），无法评估"
    logger.info(f"[studio] nl-to-factor: {expr!r} valid={result['valid']} "
                f"ic={(evaluation or {}).get('mean_ic')}")
    return ok(result)


@router.post("/alpha-eval", response_model=APIResponse[dict])
async def alpha_eval(
    req: AlphaEvalRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """评估单个 Alpha 表达式：截面 Rank IC 时序 + 多空分组净值 + 关键指标。

    复用 gp_miner.evaluate_expr_detail（与 GP 适应度同一套真实截面计算），
    纯计算无副作用；表达式字段可用 features 快照的全部数值列 + OHLCV 别名。
    """
    def _run() -> dict:
        from ...trading import paper

        pdf = _load_snapshot(days=520)
        if req.symbols:
            wanted = {paper._norm_symbol(s) for s in req.symbols}  # noqa: SLF001
            pdf = pdf.filter(pl.col("symbol").is_in(sorted(wanted)))
            if pdf.is_empty():
                raise AQPException(ERR_DATA_EMPTY,
                                   "指定标的均不在 features 快照中")
        # 表达式可用字段 = 快照全部数值列（close 等 OHLCV 由引擎别名映射）
        fields = set(pdf.columns) - {"symbol", "date"}
        res = gp_miner.evaluate_expr_detail(
            req.expr, pdf.to_pandas(), fields, req.horizon,
            with_groups=True, min_days=30)
        if res is None:
            raise AQPException(
                ERR_DATA_EMPTY,
                "表达式无法求值或有效 IC 样本不足（<30 日）")
        res["expr"] = req.expr
        res["horizon"] = req.horizon
        res["n_symbols"] = pdf["symbol"].n_unique()
        return res
    return ok(await asyncio.to_thread(_run))


# ---------------- 因子库（§4.3 Sprint4：校验 → 试算 → 入库） ----------------
class FactorSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    expression: str = Field(..., min_length=1, max_length=500)
    horizon: int = Field(5, ge=1, le=20)


def _factor_out(r) -> dict:
    import json as _json

    return {
        "id": r.id, "name": r.name, "expression": r.expression,
        "horizon": r.horizon,
        "metrics": _json.loads(r.metrics_json or "null"),
        "enabled": bool(r.enabled), "created_by": r.created_by,
        "created_at": str(r.created_at or ""),
    }


@router.get("/factors", response_model=APIResponse[list])
async def list_factors(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    """自定义因子库列表（含下线因子；metrics 为入库时评估摘要）。"""

    async def _q() -> list[dict]:

        from sqlalchemy import select

        from ...db.models import CustomFactor
        from ...db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as sess:
            rows = (await sess.execute(
                select(CustomFactor).order_by(CustomFactor.id.desc()))).scalars().all()
        return [_factor_out(r) for r in rows]

    return ok(await _q())


@router.post("/factors", response_model=APIResponse[dict])
async def save_factor(
    req: FactorSaveRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """保存自定义因子：AST 白名单校验 → 真实截面评估 → 入库（重名拒绝）。

    评估摘要（mean_ic/icir/n_days）随因子存档，供因子库列表直接对比；
    评估失败（样本不足/表达式无法求值）不入库。
    """
    from sqlalchemy import select

    from ...core.errors import ERR_EXPR_INVALID, ERR_PARAMS, fail
    from ...db.models import CustomFactor
    from ...db.session import get_session_factory

    pdf = await asyncio.to_thread(_load_snapshot)
    fields = set(pdf.columns) - {"symbol", "date"}

    from ...ml.alpha_expr import parse_expr

    try:
        parse_expr(req.expression, extra_fields=fields)
    except ValueError as e:
        # 审计 B7a 线索 2 / B4b-14 / B9b D-3：`ERR_EXPR_INVALID=53001` 此前
        # **全仓无抛出点**（前端 `types/api.ts` 已登记该码），非法表达式被
        # 报成普通参数错 40000 ⇒ 前端无法把"表达式非法"与参数错分开。
        return fail(ERR_EXPR_INVALID, f"表达式校验失败: {e}")

    def _eval() -> dict | None:
        from ...ml.gp_miner import factor_report

        return factor_report(req.expression, pdf.to_pandas(), fields, req.horizon)

    report = await asyncio.to_thread(_eval)
    if report is None:
        return fail(ERR_PARAMS, "表达式合法但有效 IC 样本不足（<60 日），不予入库")
    metrics = {k: report[k] for k in ("mean_ic", "icir", "t_stat", "n_days")}

    async def _q() -> dict | None:  # None = 同名因子已存在（调用方据此返回提示）
        factory = get_session_factory()
        async with factory() as sess:
            dup = (await sess.execute(
                select(CustomFactor).where(CustomFactor.name == req.name.strip())
            )).scalar_one_or_none()
            if dup is not None:
                return None
            row = CustomFactor(
                name=req.name.strip(), expression=req.expression.strip(),
                horizon=req.horizon,
                metrics_json=__import__("json").dumps(metrics, ensure_ascii=False),
                created_by=str(_user.get("sub") or _user.get("username") or ""))
            sess.add(row)
            await sess.commit()
            await sess.refresh(row)
            return _factor_out(row)

    saved = await _q()
    if saved is None:
        return fail(ERR_PARAMS, f"因子名已存在: {req.name}")
    logger.info(f"[studio] factor saved: {req.name} ic={metrics['mean_ic']}")
    return ok(saved, message="因子已入库")


@router.delete("/factors/{factor_id}", response_model=APIResponse[dict])
async def delete_factor(
    factor_id: int,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """删除自定义因子（物理删除；表达式可随时重新入库）。"""

    async def _q() -> bool:
        from sqlalchemy import delete as _delete

        from ...db.models import CustomFactor
        from ...db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as sess:
            res = await sess.execute(
                _delete(CustomFactor).where(CustomFactor.id == factor_id))
            await sess.commit()
            return res.rowcount > 0

    if not await _q():
        raise AQPException(ERR_DATA_EMPTY, f"因子不存在: {factor_id}")
    return ok({"id": factor_id}, message="因子已删除")


class FactorReportRequest(BaseModel):
    expr: str = Field(..., min_length=1, max_length=500)
    horizon: int = Field(5, ge=1, le=20)


@router.post("/factor-report", response_model=APIResponse[dict])
async def factor_report_api(
    req: FactorReportRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """因子检测报告（§4.3）：5 分组分层净值/年化、1/5/10/20 日衰减、Top 组换手率。

    纯计算无副作用；与 /alpha-eval 同一评估底座（真实截面，不造数）。
    """
    def _run() -> dict:
        from ...ml.gp_miner import factor_report

        pdf = _load_snapshot(days=520)
        fields = set(pdf.columns) - {"symbol", "date"}
        rep = factor_report(req.expr, pdf.to_pandas(), fields, req.horizon)
        if rep is None:
            raise AQPException(ERR_DATA_EMPTY,
                               "表达式无法求值或有效 IC 样本不足（<60 日）")
        rep["expr"] = req.expr
        rep["horizon"] = req.horizon
        return rep

    return ok(await asyncio.to_thread(_run))
