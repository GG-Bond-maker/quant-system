"""
模型注册与生产治理（AQP ML，第三/四/五/六阶段）。

CRIT-003 背景：
    原 ``load_prod_model()`` 取 MODEL_ROOT 下"按目录名排序的最后一个"目录，
    而 ``_register_model()`` 恒写 ``is_production=0`` —— 注册表只写不读。
    结果是任何一次实验（MODEL_ROOT 下曾堆积 384+ 个目录，含 pytest/leakA/leakB 后缀）
    都会静默变成"生产模型"，线上曾服务一个 1 棵树、test IC = −0.365 的模型。

本模块确立的纪律：

1. **生产模型只能来自注册表**：``load_prod_model()`` 只认 ``is_production=1``，
   不存在就**明确报错**，绝不回退到"最新目录"或其它实验模型；
2. **目录隔离**：``models/prod/`` 与 ``models/exp/`` 分离，
   训练产物默认落 ``exp/``，只有 promote 才复制/登记到 ``prod/``；
3. **promote 必须显式执行**：训练完不会自动覆盖生产模型；
4. **同一时间最多一个 production**：promote 在单个事务里先把同 model_name 的
   所有行置 0 再置 1，中途失败整体回滚；
5. **决策只基于 validation**：test 指标仅用于审计，不参与 promote 决策
   （用 test 调参 = test contamination）。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
from loguru import logger

from ..core.config import get_settings

# ---------------- 目录布局 ----------------
PROD_SUBDIR = "prod"
EXP_SUBDIR = "exp"


def exp_root() -> Path:
    """实验模型根目录（训练产物默认落这里）。"""
    p = get_settings().MODEL_ROOT / EXP_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def prod_root() -> Path:
    """生产模型根目录（只有 promote 才会写入）。"""
    p = get_settings().MODEL_ROOT / PROD_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------- 异常 ----------------
class ModelRegistryError(RuntimeError):
    """模型注册表相关错误。"""


class NoProductionModelError(ModelRegistryError):
    """不存在生产模型。绝不以实验模型兜底。"""


# ---------------- promote 策略（集中阈值） ----------------
@dataclass(frozen=True)
class PromotePolicy:
    """生产模型质量门槛（第六阶段）。

    设计说明：
        不采用"RankIC >= 0.03 就生产"这种绝对阈值 —— 不同市场阶段、
        不同样本规模下，IC 的绝对水平不可比（121 只与 3000 只的横截面
        RankIC 分布差异很大）。因此采用**相对比较**：
        候选模型必须优于（或不劣于）当前生产模型，并满足绝对下限。
    """

    min_valid_rank_ic: float = 0.02      # 绝对下限：valid RankIC
    min_valid_icir: float = 0.10         # 绝对下限：valid ICIR
    max_rmse_worsen_ratio: float = 0.05  # RMSE 相对生产最多允许恶化 5%
    rank_ic_tolerance: float = 0.0       # RankIC 相对生产的容忍度（0 = 必须不劣）
    icir_tolerance: float = 0.0          # ICIR 相对生产的容忍度
    # test 仅审计：是否要求 test RankIC > 0（默认 False，避免用 test 调参）
    require_test_rank_ic_positive: bool = False
    # 允许 RankIC/ICIR 缺失（NaN）。
    # 仅用于"极小合成数据"的链路测试：daily_rank_ic 要求每日截面样本 > 10，
    # 单标的/小样本合成数据下该指标数学上无定义，必然为 NaN。
    # ⚠️ 生产与任何真实数据场景必须为 False —— 指标缺失时应拒绝 promote，
    #    而不是当作"通过"。
    allow_missing_metrics: bool = False


DEFAULT_PROMOTE_POLICY = PromotePolicy()


def auto_min_rank_ic(
    n_symbols: int,
    n_days: int = 250,
    sigma: float = 2.0,
    floor: float = 0.015,
) -> float:
    """**按样本规模推导** RankIC 绝对下限（第六阶段的核心设计）。

    为什么不写死 0.03：
        原假设 H0 为"因子无预测力"，此时日度 RankIC 的标准差约为
        1/sqrt(N-1)（Spearman 秩相关在随机情形下的离散度，N 为横截面股票数）。
        D 个交易日的**均值**标准误为 1/(sqrt(N-1) * sqrt(D))。
        因此同一个 RankIC 数值在不同宽度股票池下的显著性完全不同：
            N=120,  D=250 -> SE ≈ 0.0058，RankIC 0.019 约 3.3σ（显著）
            N=3000, D=250 -> SE ≈ 0.0012，同样 0.019 约 16σ（极显著）
        固定阈值会在小样本宇宙下误杀、在大样本宇宙下失守。

    判据取"统计显著性"与"经济意义"的较大值：
        max(floor, sigma * SE)
    floor 防止"统计显著但幅度过小、无法覆盖交易成本"的因子上线。

    :param n_symbols: 横截面股票数
    :param n_days:    valid 段交易日数
    :param sigma:     显著性倍数（2 ≈ 95% 置信）
    :param floor:     经济意义下限
    """
    import math

    if n_symbols < 3 or n_days < 5:
        return floor
    se = 1.0 / (math.sqrt(n_symbols - 1) * math.sqrt(n_days))
    return max(floor, sigma * se)


@dataclass
class PromoteDecision:
    promote: bool
    reason: str
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"promote": self.promote, "reason": self.reason, "checks": self.checks}


def _n(v: Any, digits: int = 4) -> str:
    """安全地格式化可能为 None / NaN 的指标值，用于日志与拒绝原因。

    注册表把 NaN 存为 SQL NULL，取出来的值是 None；
    直接 f"{None:.4f}" 会抛 TypeError，掩盖真正的拒绝原因。
    """
    if v is None:
        return "缺失"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return "NaN" if f != f else f"{f:.{digits}f}"  # noqa: PLR0124  # NaN 判定的标准写法


# ---------------- 注册表读写 ----------------
def _connect() -> sqlite3.Connection:
    s = get_settings()
    if not s.SQLITE_PATH.exists():
        raise ModelRegistryError(
            f"SQLite 不存在：{s.SQLITE_PATH}（请先运行 init/bootstrap）")
    conn = sqlite3.connect(s.SQLITE_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _r6(v: Any) -> float | None:
    """把可能为 None / NaN 的指标值转成可序列化 float（缺失返回 None）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 6)  # noqa: PLR0124  # NaN 判定的标准写法


def register_candidate(
    model_name: str,
    version: str,
    metrics: dict[str, Any],
    model_dir: Path,
    feature_version: str = "alpha_basic_v1",
    dataset_version: str | None = None,
    model_file: str = "model.lgbm",
) -> int:
    """登记一个候选模型（status=candidate，is_production=0）。

    返回 registry id。训练完成后调用，不会触碰生产模型。
    model_file：产物文件名（lgbm 默认 model.lgbm；TFT/GNN 记 model.pt）。
    """
    split = metrics.get("split", {}) or {}
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT OR REPLACE INTO model_registry
               (model_name, version, feature_version, dataset_version,
                train_start, train_end, valid_start, valid_end,
                test_start, test_end,
                valid_ic, valid_rank_ic, valid_icir, valid_rmse,
                test_ic, test_rank_ic, test_icir, test_rmse,
                model_path, params_json, label_quality_json,
                is_production, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'candidate')""",
            (
                model_name, version, feature_version, dataset_version,
                _d(split.get("train_start")), _d(split.get("train_end")),
                _d(split.get("valid_start")), _d(split.get("valid_end")),
                _d(split.get("test_start")), None,
                _f(metrics.get("valid_ic")), _f(metrics.get("valid_rank_ic")),
                _f(metrics.get("valid_icir")), _f(metrics.get("valid_rmse")),
                _f(metrics.get("test_ic")), _f(metrics.get("test_rank_ic")),
                _f(metrics.get("test_icir")), _f(metrics.get("test_rmse")),
                str(model_dir / model_file),
                json.dumps({"kept_features": metrics.get("kept_features", []),
                            "split": split}, ensure_ascii=False, default=str),
                json.dumps(metrics.get("label_quality", {}),
                           ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
        rid = int(cur.lastrowid or 0)
        logger.info(f"[registry] candidate 登记: {model_name}/{version} id={rid}")
        return rid
    finally:
        conn.close()


def _d(v: Any) -> str | None:
    """把 split 里的日期字符串截断为 YYYY-MM-DD（可能是 datetime 串）。"""
    if v is None:
        return None
    return str(v)[:10] or None


def _f(v: Any) -> float | None:
    """NaN/inf 一律存 NULL（SQLite 无 NaN 语义）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None  # noqa: PLR0124  # NaN 判定的标准写法


def get_production(model_name: str = "lgbm_v1") -> dict[str, Any] | None:
    """读取当前生产模型记录；不存在返回 None。"""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM model_registry WHERE model_name=? AND is_production=1",
            (model_name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_model(model_name: str, version: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM model_registry WHERE model_name=? AND version=?",
            (model_name, version)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_models(model_name: str = "lgbm_v1", limit: int = 50) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM model_registry WHERE model_name=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?", (model_name, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clear_registry(model_name: str | None = None) -> int:
    """清空注册表记录（**仅供测试与重建使用**，生产环境不应调用）。

    测试用它消除"用例必须按特定顺序执行"的隐式依赖
    （第九阶段要求：pytest 单独跑与全量跑结果必须一致）。
    """
    conn = _connect()
    try:
        if model_name is None:
            n = conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
            conn.execute("DELETE FROM model_registry")
        else:
            n = conn.execute(
                "SELECT COUNT(*) FROM model_registry WHERE model_name=?",
                (model_name,)).fetchone()[0]
            conn.execute("DELETE FROM model_registry WHERE model_name=?",
                         (model_name,))
        conn.commit()
        return int(n)
    finally:
        conn.close()


def count_production(model_name: str = "lgbm_v1") -> int:
    """生产模型数量（不变量：必须 <= 1）。"""
    conn = _connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM model_registry "
            "WHERE model_name=? AND is_production=1", (model_name,)).fetchone()[0])
    finally:
        conn.close()


# ---------------- promote ----------------
def promote_model(
    model_name: str,
    version: str,
    by: str = "cli",
    reason: str = "",
    policy: PromotePolicy | None = None,
) -> dict[str, Any]:
    """显式把候选模型提升为生产模型（第五阶段）。

    事务保证"同一时间最多一个 production"：
        BEGIN IMMEDIATE -> 全部置 0 -> 目标置 1 -> COMMIT（失败整体回滚）

    :return: {"promoted": bool, "version": str, "checks": ...}
    """
    policy = policy or DEFAULT_PROMOTE_POLICY
    cand = get_model(model_name, version)
    if cand is None:
        raise ModelRegistryError(f"找不到模型 {model_name}/{version}")

    # 模型文件必须存在，否则拒绝 promote（防止注册了但产物被删的幽灵模型）
    model_file = Path(cand["model_path"])
    if not model_file.exists():
        raise ModelRegistryError(f"模型文件不存在，拒绝 promote: {model_file}")

    current = get_production(model_name)
    decision = evaluate_candidate(cand, current, policy)
    if not decision.promote:
        logger.warning(f"[registry] promote 被拒绝 {model_name}/{version}: "
                       f"{decision.reason}")
        return {"promoted": False, "version": version, **decision.as_dict()}

    # 把产物复制到 prod/ 目录（生产模型与实验目录物理隔离）
    # 产物名取自候选登记时的 model_file（LGBM=model.lgbm；TFT/GNN=model.pt），
    # 不再硬编码 model.lgbm——否则非 LGBM 候选 promote 后 model_path 会指向
    # 一个从未被复制过来的文件名，注册表出现"记录存在、产物缺失"的幽灵生产模型。
    artifact = model_file.name
    dst_dir = prod_root() / f"{model_name}_{version}"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in (artifact, "features.json", "params.json", "metrics.json"):
        src = model_file.parent / name
        if src.exists():
            shutil.copy2(src, dst_dir / name)
    dst_artifact = dst_dir / artifact
    if not dst_artifact.exists():
        raise ModelRegistryError(
            f"产物复制失败，拒绝 promote: {dst_artifact}（源 {model_file}）")

    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 1) 该 model_name 下所有 production 降级为 archived
        conn.execute(
            "UPDATE model_registry SET is_production=0, status='archived' "
            "WHERE model_name=? AND is_production=1", (model_name,))
        # 2) 目标置为 production
        conn.execute(
            "UPDATE model_registry SET is_production=1, status='production', "
            "promoted_at=?, promoted_by=?, promote_reason=?, model_path=? "
            "WHERE model_name=? AND version=?",
            (datetime.now(), by, reason or decision.reason,
             str(dst_artifact), model_name, version))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    n = count_production(model_name)
    if n != 1:
        raise ModelRegistryError(
            f"promote 后生产模型数量为 {n}（应为 1），治理不变量被破坏")
    logger.info(f"[registry] promote 成功: {model_name}/{version} by={by} "
                f"reason={decision.reason}")
    return {"promoted": True, "version": version, "model_path": str(dst_dir),
            **decision.as_dict()}


def evaluate_candidate(
    candidate: dict[str, Any],
    current: dict[str, Any] | None,
    policy: PromotePolicy = DEFAULT_PROMOTE_POLICY,
) -> PromoteDecision:
    """第六阶段：候选 vs 当前生产 —— 决策只基于 validation 指标。

    比较维度（全部取 valid 段）：RankIC / IC / ICIR / RMSE。
    test 指标不参与决策，只写入注册表供审计。
    """
    checks: dict[str, dict[str, Any]] = {}

    vr = candidate.get("valid_rank_ic")
    icir = candidate.get("valid_icir")
    rmse = candidate.get("valid_rmse")
    vic = candidate.get("valid_ic")

    missing: list[str] = []
    if vr is None or vr != vr:  # noqa: PLR0124  # NaN 判定的标准写法
        missing.append("valid_rank_ic")
    if icir is None or icir != icir:  # noqa: PLR0124  # NaN 判定的标准写法
        missing.append("valid_icir")
    if missing:
        if not policy.allow_missing_metrics:
            return PromoteDecision(
                False, f"候选 {', '.join(missing)} 缺失或为 NaN，无法评估", checks)
        logger.warning(
            f"[registry] 候选 {', '.join(missing)} 为 NaN，因 "
            f"allow_missing_metrics=True 跳过对应检查（仅合成数据链路测试可用）")
        checks["missing_metrics"] = {"missing": missing, "skipped": True,
                                     "pass": None}
        # 缺失的指标不参与绝对门槛与相对比较

    # 1) 绝对下限
    def _pass(value: Any, threshold: float) -> tuple[bool, bool]:
        """返回 (是否通过, 是否因缺失而跳过)。"""
        if value is None or value != value:  # noqa: PLR0124  # NaN 判定的标准写法
            # 缺失：allow_missing_metrics 为真则跳过该项（视为通过），否则不通过
            return policy.allow_missing_metrics, policy.allow_missing_metrics
        return value >= threshold, False

    ok_rank, skip_rank = _pass(vr, policy.min_valid_rank_ic)
    checks["min_valid_rank_ic"] = {
        "value": None if vr is None or vr != vr else round(float(vr), 6),  # noqa: PLR0124  # NaN 判定的标准写法
        "threshold": policy.min_valid_rank_ic,
        "pass": bool(ok_rank), "skipped": bool(skip_rank)}
    ok_icir, skip_icir = _pass(icir, policy.min_valid_icir)
    checks["min_valid_icir"] = {
        "value": None if icir is None or icir != icir else round(float(icir), 6),  # noqa: PLR0124  # NaN 判定的标准写法
        "threshold": policy.min_valid_icir,
        "pass": bool(ok_icir), "skipped": bool(skip_icir)}
    if not (ok_rank and ok_icir):
        # 注意：注册表把 NaN 存为 SQL NULL，故这里 vr/icir 可能是 None，
        # 直接用 f"{vr:.4f}" 会抛 TypeError（NoneType.__format__）。
        return PromoteDecision(
            False,
            f"未达绝对门槛：valid_rank_ic={_n(vr)}(>={policy.min_valid_rank_ic}) "
            f"valid_icir={_n(icir)}(>={policy.min_valid_icir})", checks)

    if policy.require_test_rank_ic_positive:
        tr = candidate.get("test_rank_ic")
        checks["test_rank_ic_positive"] = {
            "value": None if tr is None else round(float(tr), 6),
            "pass": bool(tr is not None and tr == tr and tr > 0)}  # noqa: PLR0124  # NaN 判定的标准写法
        if not checks["test_rank_ic_positive"]["pass"]:
            return PromoteDecision(False, "test RankIC 非正（仅审计项，但策略要求为正）",
                                   checks)

    # 2) 相对比较（有生产模型时）
    if current is not None:
        p_rank = current.get("valid_rank_ic")
        p_rmse = current.get("valid_rmse")
        p_icir = current.get("valid_icir")
        p_ic = current.get("valid_ic")

        # 候选指标缺失时跳过对应比较（None 无法参与数值比较）
        has_vr = vr is not None and vr == vr  # noqa: PLR0124  # NaN 判定的标准写法
        has_icir = icir is not None and icir == icir  # noqa: PLR0124  # NaN 判定的标准写法

        if has_vr and p_rank is not None and p_rank == p_rank:  # noqa: PLR0124  # NaN 判定的标准写法
            ok = vr >= p_rank - policy.rank_ic_tolerance
            checks["rank_ic_vs_prod"] = {
                "candidate": _r6(vr), "production": _r6(p_rank),
                "pass": bool(ok)}
            if not ok:
                return PromoteDecision(
                    False, f"valid_rank_ic 劣于生产 {_n(vr)} < {_n(p_rank)}", checks)

        if has_icir and p_icir is not None and p_icir == p_icir:  # noqa: PLR0124  # NaN 判定的标准写法
            ok = icir >= p_icir - policy.icir_tolerance
            checks["icir_vs_prod"] = {
                "candidate": _r6(icir), "production": _r6(p_icir),
                "pass": bool(ok)}
            if not ok:
                return PromoteDecision(
                    False, f"valid_icir 劣于生产 {_n(icir)} < {_n(p_icir)}", checks)

        if p_rmse is not None and p_rmse == p_rmse and rmse is not None and rmse == rmse:  # noqa: PLR0124  # NaN 判定的标准写法
            limit = p_rmse * (1 + policy.max_rmse_worsen_ratio)
            ok = rmse <= limit
            checks["rmse_vs_prod"] = {
                "candidate": round(float(rmse), 6), "production": round(float(p_rmse), 6),
                "limit": round(float(limit), 6), "pass": bool(ok)}
            if not ok:
                return PromoteDecision(
                    False, f"valid_rmse 恶化超限 {rmse:.4f} > {limit:.4f}", checks)

        if p_ic is not None and p_ic == p_ic and vic is not None and vic == vic:  # noqa: PLR0124  # NaN 判定的标准写法
            checks["ic_vs_prod"] = {
                "candidate": round(float(vic), 6), "production": round(float(p_ic), 6),
                "pass": bool(vic >= p_ic)}
        reason = "通过全部门槛且优于/不劣于当前生产模型"
    else:
        reason = "通过全部绝对门槛（当前无生产模型）"

    return PromoteDecision(True, reason, checks)


# ---------------- 推理加载（第三阶段核心） ----------------
def load_prod_model(model_name: str = "lgbm_v1") -> tuple[lgb.Booster, list[str], Path]:
    """加载**注册表认定**的生产模型。

    ⚠️ CRIT-003 修复：不再按目录名排序取"最新"。
    - 读 ``model_registry.is_production = 1`` 的唯一记录；
    - 不存在则抛 :class:`NoProductionModelError`，**绝不回退到实验模型**；
    - 校验模型文件存在，并校验 features.json 与训练时记录一致。
    """
    rec = get_production(model_name)
    if rec is None:
        raise NoProductionModelError(
            f"没有生产模型（model_registry 中 {model_name} 无 is_production=1）。"
            f"请先训练并显式执行 promote：python scripts/promote_model.py --version <v>")
    model_path = Path(rec["model_path"])
    if not model_path.exists():
        raise NoProductionModelError(
            f"生产模型文件缺失：{model_path}（version={rec['version']}）。"
            f"注册记录与磁盘产物不一致，请重新训练并 promote。")

    # 推理链路目前只实现 LightGBM。TFT/GNN 等候选可正常登记/promote（治理通道
    # 复用），但 promote 后不能被本函数加载——此处给出明确诊断，而不是让
    # lgb.Booster 在解析 .pt 时抛出难以定位的底层异常。
    if model_path.suffix != ".lgbm":
        raise NoProductionModelError(
            f"生产模型 {model_name}/{rec['version']} 的产物为 {model_path.name}，"
            f"不属于 LightGBM 推理链路。TFT/GNN 等序列/图模型使用 "
            f"app.ml.torch_models / gnn_models 的专用加载器，尚未接入 infer 流水线，"
            f"请勿将其 promote 为生产模型（如需实验请保留 candidate 状态）。")

    booster = lgb.Booster(model_file=str(model_path))
    feat_file = model_path.parent / "features.json"
    if not feat_file.exists():
        raise NoProductionModelError(f"生产模型缺少 features.json: {feat_file}")
    features: list[str] = json.loads(feat_file.read_text(encoding="utf-8"))

    logger.info(
        f"load PROD model: {model_name}/{rec['version']} "
        f"n_features={len(features)} promoted_at={rec.get('promoted_at')} "
        f"valid_rank_ic={rec.get('valid_rank_ic')}")
    return booster, features, model_path.parent


def prod_model_info(model_name: str = "lgbm_v1") -> dict[str, Any]:
    """给 API / 前端用的生产模型摘要（不含 booster）。"""
    rec = get_production(model_name)
    if rec is None:
        return {"has_production": False}
    return {
        "has_production": True,
        "model_name": rec["model_name"],
        "version": rec["version"],
        "feature_version": rec.get("feature_version"),
        "dataset_version": rec.get("dataset_version"),
        "train_period": [str(rec.get("train_start")), str(rec.get("train_end"))],
        "valid_period": [str(rec.get("valid_start")), str(rec.get("valid_end"))],
        "valid_rank_ic": rec.get("valid_rank_ic"),
        "valid_icir": rec.get("valid_icir"),
        "test_rank_ic": rec.get("test_rank_ic"),
        "promoted_at": str(rec.get("promoted_at")),
        "model_path": rec.get("model_path"),
    }
