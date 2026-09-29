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
import math
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

    # ---- R14：门禁比较对象的可比性（审计 2026-09-18 §4.4 R14）----
    # 相对比较（RankIC/ICIR/RMSE 对生产）只在双方**同口径**时才有意义。
    # feature_version / dataset_version 被证实不同 ⇒ 默认拒绝直接比较（不得拿
    # 不同数据集/特征版本的 IC 比大小）；显式置 True 才降级为"跳过相对比较"。
    allow_incomparable_lineage: bool = False
    # 验证区间（valid_start/valid_end）被证实不同时，是否也按"不可比"处理。
    # 默认 False：正常滚动重训必然换验证段，默认硬拒会让门禁形同虚设；
    # 该维度默认**披露**（checks.lineage_comparability.valid_window_differs），
    # 需要严格时置 True。
    require_identical_valid_window: bool = False

    # ---- T8：验证窗口 regime 化（审计 2026-09-18 §7.9 T8）----
    # 判据：k 个连续窗口的 RankIC 均值 >= max(min_regime_rank_ic, sigma·SE)
    # 且 正向窗口占比 >= min_regime_positive_ratio。
    # ⚠️ 默认只**披露**（enforce_regime_gate=False）：合成/小样本数据下 4 段窗口
    #    的符号一致性本身噪声极大，作为默认硬门槛会误杀。置 True 后即成为硬判据
    #    （影子通道 / 跨 regime 候选应使用）。
    enforce_regime_gate: bool = False
    require_regime_windows: bool = False   # 候选必须披露逐窗口 RankIC，否则拒绝
    n_regime_windows: int = 4
    min_regime_positive_ratio: float = 0.75
    min_regime_rank_ic: float = 0.002
    sigma_regime: float = 2.0
    # test 仅审计：是否要求 test RankIC > 0（默认 False，避免用 test 调参）
    require_test_rank_ic_positive: bool = False

    # ---- P1-48：门禁的"水平偏置"维度（审计 2026-09-18 §7.9 T3）----
    # RankIC 门禁与预测的**绝对水平**完全正交：生产模型实测逐日截面均值
    # ≈ −0.0016（≈ −0.16%/5d），RankIC 再达标也拦不住这种整体下移。
    # ⚠️ 默认只**披露**（enforce_pred_level_gate=False）：报告自陈"新增硬门槛
    #    可能挡住合法数据更新 ⇒ 用 shadow 通道而非硬拒"，故置 True 才硬判。
    enforce_pred_level_gate: bool = False
    sigma_pred_level: float = 2.0

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
                            "split": split,
                            # 审计 P1-39：训练口径随登记行一起落盘（存 params_json，
                            # 无需改表结构），门禁据此判断 RMSE 是否可比。
                            "basis": metrics.get("train_basis") or {},
                            # 审计 P1-48：预测水平/校准统计随登记行落盘，门禁据此
                            # 判断"水平偏置"（RankIC 与它正交，结构上拦不住）。
                            "pred_level": metrics.get("pred_level") or {},
                            # 审计 T8：逐窗口 RankIC 随登记行落盘，门禁据此做
                            # 跨 regime 符号一致性判据 / 披露（无需改表结构）。
                            "regime_windows": metrics.get("valid_regime_windows") or {}},
                           ensure_ascii=False, default=str),
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
def _same_file(a: Path, b: Path) -> bool:
    """a / b 是否指向**同一个文件**（审计 P1-7 的核心判据）。

    已 promote 过的版本其 ``model_path`` 已在 ``prod/`` 下，重复 promote / 回滚时
    ``prod/<name>_<ver>/<artifact>`` 与源路径是同一个文件 ⇒ ``shutil.copy2(src, src)``
    会抛 ``SameFileError``（未捕获 ⇒ CLI 直接崩）。
    """
    try:
        if a.exists() and b.exists() and a.samefile(b):
            return True
    except OSError:
        pass
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _copy_artifacts(model_file: Path, dst_dir: Path) -> Path:
    """把候选产物复制进 ``prod/``，**同路径幂等跳过**（审计 P1-7）。

    修复前：``shutil.copy2(src, dst)`` 在 ``src == dst`` 时抛 ``SameFileError``，
    且该异常未被捕获 ⇒ ``python scripts/promote_model.py --version <当前生产版本>``
    直接崩溃（连"已是最新"都做不到）。修复后 equal-path 视为已完成，其余步骤
    （注册表指针切换 / 不变量校验）照常执行 ⇒ 幂等成功。
    """
    artifact = model_file.name
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in (artifact, "features.json", "params.json", "metrics.json"):
        src = model_file.parent / name
        if not src.exists():
            continue
        dst = dst_dir / name
        if _same_file(src, dst):
            continue  # 已在 prod/（重复 promote / 回滚）：幂等跳过，不再 copy2
        shutil.copy2(src, dst)
    dst_artifact = dst_dir / artifact
    if not dst_artifact.exists():
        raise ModelRegistryError(
            f"产物复制失败，拒绝 promote: {dst_artifact}（源 {model_file}）")
    return dst_artifact


def _install_as_production(cand: dict[str, Any], model_name: str, version: str,
                           by: str, reason: str) -> Path:
    """复制产物 + 单事务切换生产指针（promote / rollback 共用）。"""
    model_file = Path(cand["model_path"])
    dst_dir = prod_root() / f"{model_name}_{version}"
    dst_artifact = _copy_artifacts(model_file, dst_dir)

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
            (datetime.now(), by, reason, str(dst_artifact), model_name, version))
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
    return dst_dir


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

    dst_dir = _install_as_production(cand, model_name, version, by,
                                     reason or decision.reason)
    logger.info(f"[registry] promote 成功: {model_name}/{version} by={by} "
                f"reason={decision.reason}")
    return {"promoted": True, "version": version, "model_path": str(dst_dir),
            **decision.as_dict()}


def rollback_model(
    model_name: str,
    version: str,
    by: str = "cli",
    reason: str = "",
) -> dict[str, Any]:
    """**回滚通道**（审计 P1-6）：把生产指针切回一个**曾被提升过**的版本。

    为什么必须有这条通道：
        ``PromotePolicy.rank_ic_tolerance`` 默认 0.0（"必须不劣于当前生产"），
        于是生产模型一旦变坏，任何"IC 更差但曾验证过的旧版本"都恒被拒
        （实测 ``0.0920 < 0.1020``）——**运维上没有任何回滚手段**。
        回滚的语义不是"新候选优于生产"，而是"恢复一个已知可用的历史版本"，
        因此**不适用**相对门禁；但必须可审计：

    - 目标版本必须**曾被提升过**（``promoted_at`` 非空）——从未过门禁的候选不得
      借回滚绕过门禁（那才叫放松阈值）；
    - 必须有当前生产模型（无生产模型时请走 ``promote_model``）；
    - ``reason`` 必填（写入 ``promote_reason``，形如 ``rollback: <reason>``）；
    - 两侧指标与差值全部进入 ``checks["rollback"]``（**披露**回滚是"变差"还是"变好"）。

    重复回滚 / 回滚到当前生产版本是**幂等**的（P1-7：不再踩 ``SameFileError``）。
    """
    if not str(reason or "").strip():
        raise ModelRegistryError(
            "回滚必须提供非空 reason（审计要求：promote_reason 记录回滚依据）")
    cand = get_model(model_name, version)
    if cand is None:
        raise ModelRegistryError(f"找不到模型 {model_name}/{version}")
    model_file = Path(cand["model_path"])
    if not model_file.exists():
        raise ModelRegistryError(f"模型文件不存在，拒绝回滚: {model_file}")

    current = get_production(model_name)
    if current is None:
        raise ModelRegistryError(
            f"当前没有生产模型（{model_name}），无需回滚；请用 promote_model")
    if not cand.get("promoted_at"):
        raise ModelRegistryError(
            f"{model_name}/{version} 从未被提升过（promoted_at 为空），不能作为回滚目标；"
            f"新候选必须走 promote_model 的质量门禁")

    checks: dict[str, dict[str, Any]] = {
        "rollback": {
            "target": version,
            "previous_production": current.get("version"),
            "by": by,
            "gate": "skipped_by_rollback_channel",
            "reason": reason,
            "delta_valid_rank_ic": _delta(cand.get("valid_rank_ic"),
                                          current.get("valid_rank_ic")),
            "delta_valid_icir": _delta(cand.get("valid_icir"),
                                       current.get("valid_icir")),
            "target_valid_rank_ic": _r6(cand.get("valid_rank_ic")),
            "previous_valid_rank_ic": _r6(current.get("valid_rank_ic")),
        },
        "lineage_comparability": lineage_comparability(cand, current),
    }
    # 供人工复核：记录"若走普通门禁会被拒"的事实（不改变回滚结果）
    checks["rollback"]["gate_decision_if_promoted"] = evaluate_candidate(
        cand, current, DEFAULT_PROMOTE_POLICY).as_dict()

    dst_dir = _install_as_production(
        cand, model_name, version, by, f"rollback: {reason}")
    logger.info(f"[registry] rollback 成功: {model_name}/{version}（from "
                f"{current.get('version')}）by={by} reason={reason}")
    return {"promoted": True, "rollback": True, "version": version,
            "previous_production": current.get("version"),
            "model_path": str(dst_dir), "checks": checks,
            "reason": f"回滚到 {version}（原因：{reason}）；相对门禁按回滚通道跳过"}


def _delta(a: Any, b: Any) -> float | None:
    """a - b（任一缺失/NaN 返回 None），用于回滚披露。"""
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return None
    return round(x - y, 6)


_RMSE_BASIS_KEYS = (
    "target", "xsec_demean", "horizon", "label_mode",
    "max_abs_label_return", "dataset_version", "feature_version",
)


def training_basis(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """取出训练口径（审计 P1-39）。候选来自 metrics.json，生产来自注册表行。

    - 候选：``metrics["train_basis"]``（dict）
    - 生产：``params_json`` 里的 ``basis``（JSON 串，见 register_candidate）
    取不到返回 None ⇒ 调用方按「不可比」处理（不能证明可比就不比）。
    """
    if not record:
        return None
    b = record.get("train_basis")
    if isinstance(b, dict) and b:
        return b
    return _from_params_json(record, "basis")


def _from_params_json(record: dict[str, Any], key: str) -> dict[str, Any] | None:
    """从注册表行的 ``params_json`` 串里取一个 dict 字段（取不到返回 None）。"""
    pj = record.get("params_json")
    if not isinstance(pj, str) or not pj:
        return None
    try:
        obj = json.loads(pj)
    except (TypeError, ValueError):
        return None
    v = obj.get(key) if isinstance(obj, dict) else None
    return v if isinstance(v, dict) and v else None


def prediction_level(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """取出**预测水平/校准**统计（审计 P1-48）。

    与 :func:`training_basis` 同源同形：候选取 ``metrics["pred_level"]``（落盘时
    同时写进注册表行），生产取 ``params_json["pred_level"]``。旧产物（未落盘该
    统计）返回 None ⇒ 门禁按"无法判定"披露，**不**据此拒绝。
    """
    if not record:
        return None
    v = record.get("pred_level")
    if isinstance(v, dict) and v:
        return v
    return _from_params_json(record, "pred_level")


def basis_mismatch_reason(
    candidate_basis: dict[str, Any] | None,
    prod_basis: dict[str, Any] | None,
) -> str | None:
    """返回不可比的原因；可比则返回 None。

    **为什么必须拦**：RMSE 是「目标函数尺度」相关的指标。``xsec_demean`` 把标签换成
    截面相对收益后，同数据同切分的 RMSE 会系统性变小（实测 0.07411 → 0.05998），
    此时拿两边 RMSE 比大小等于把「换了目标函数」误判成「模型变好/变差」。
    实测该比值 1.25× 远超 ``max_rmse_worsen_ratio=0.05`` ⇒ 更优候选恒被拒。
    """
    if candidate_basis is None and prod_basis is None:
        return None  # 两边都没记口径（旧产物）：保持历史行为，仅提示
    if candidate_basis is None:
        return "候选未记录训练口径（train_basis 缺失），无法与生产模型比较 RMSE"
    if prod_basis is None:
        return "生产模型未记录训练口径（旧产物，params_json 无 basis），无法比较 RMSE"
    diffs = {
        k: {"candidate": candidate_basis.get(k), "production": prod_basis.get(k)}
        for k in _RMSE_BASIS_KEYS
        if candidate_basis.get(k) != prod_basis.get(k)
    }
    if diffs:
        return "训练口径不一致，RMSE 不可比：" + json.dumps(diffs, ensure_ascii=False)
    return None


# ---------------- T8：验证窗口 regime 化 ----------------
def regime_windows(
    series: Any,
    n_windows: int = 4,
    *,
    min_rank_ic: float = 0.002,
    sigma: float = 2.0,
) -> dict[str, Any]:
    """把**有序**的逐日 RankIC 序列切成 n 个连续窗口，给出跨 regime 判据与披露。

    审计 T8 的背景：生产模型的 ``valid_rank_ic=0.1020`` 取自**单一段 regime**
    （2024-07~2025-08），而 train/valid/test = 0.156/0.102/0.0705 的单调衰减说明
    关系非平稳 ⇒ 任何"单一验证段绝对值"门槛都是在拿**一个 regime 的运气**当门槛。
    本函数给出该段的 regime 拆分：每段均值、符号、正负窗口数，以及按样本/离散度
    归一的门槛 ``max(min_rank_ic, sigma·SE)``（SE 取窗口均值的跨窗口标准误）。

    :param series: 逐日 IC 列表（float），或 ``[(date, ic), ...]``（推荐，可披露窗口
        边界=哪段 regime）。序列必须按日期升序。
    :param n_windows: 窗口数（T8 建议 4 段）。
    :return: 披露字典；不可用（空序列/窗口数非法）时 ``{"available": False, ...}``。
        ``available=True`` 时含 ``windows``（每段 index/bounds/n/mean_rank_ic/sign）、
        ``mean_rank_ic`` / ``se_across_windows`` / ``threshold`` /
        ``positive_windows`` / ``positive_ratio`` / ``pass_level``。
    """
    import math

    pairs: list[tuple[str | None, float]] = []
    for item in series or []:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            label, value = item
        else:
            label, value = None, item
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if v != v or math.isinf(v):  # noqa: PLR0124  # NaN 判定的标准写法
            continue
        pairs.append((None if label is None else str(label), v))

    if n_windows < 1 or len(pairs) < n_windows:
        return {"available": False, "n_windows": int(n_windows),
                "n_observations": len(pairs),
                "reason": f"逐窗口 RankIC 样本不足（{len(pairs)} < {n_windows} 段），无法 regime 化"}

    # 连续切分（不重不漏）；余数分给前面的窗口，保证顺序即 regime 顺序
    base, extra = divmod(len(pairs), n_windows)
    windows: list[dict[str, Any]] = []
    totals: list[float] = []
    cursor = 0
    for i in range(n_windows):
        size = base + (1 if i < extra else 0)
        chunk = pairs[cursor:cursor + size]
        cursor += size
        vals = [v for _, v in chunk]
        mean = sum(vals) / len(vals)
        totals.append(mean)
        windows.append({
            "index": i + 1,
            "start": chunk[0][0], "end": chunk[-1][0],
            "n": len(vals),
            "mean_rank_ic": round(mean, 6),
            "sign": 1 if mean > 0 else (-1 if mean < 0 else 0),
        })

    mean_all = sum(totals) / n_windows
    se: float | None = None
    if n_windows >= 2:
        var = sum((m - mean_all) ** 2 for m in totals) / (n_windows - 1)
        se = math.sqrt(var) / math.sqrt(n_windows)
    positive = sum(1 for m in totals if m > 0)
    threshold = max(float(min_rank_ic), sigma * se) if se else float(min_rank_ic)
    return {
        "available": True,
        "n_windows": int(n_windows),
        "n_observations": len(pairs),
        "windows": windows,
        "mean_rank_ic": round(mean_all, 6),
        "se_across_windows": None if se is None else round(se, 6),
        "sigma": float(sigma),
        "min_rank_ic": float(min_rank_ic),
        "threshold": round(threshold, 6),
        "mean_minus_sigma_se": round(mean_all - sigma * (se or 0.0), 6),
        "positive_windows": positive,
        "positive_ratio": round(positive / n_windows, 4),
        "pass_level": bool(mean_all >= threshold),
    }


def candidate_regime_windows(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """取出候选/生产模型的逐窗口 RankIC（审计 T8）。

    - 候选 dict：``record["valid_regime_windows"]``
    - 注册表行：``params_json.regime_windows``（见 register_candidate）
    取不到返回 None。
    """
    if not record:
        return None
    rw = record.get("valid_regime_windows")
    if isinstance(rw, dict) and rw.get("available"):
        return rw
    pj = record.get("params_json")
    if isinstance(pj, str) and pj:
        try:
            obj = json.loads(pj)
        except (TypeError, ValueError):
            return None
        rw = obj.get("regime_windows") if isinstance(obj, dict) else None
        if isinstance(rw, dict) and rw.get("available"):
            return rw
    return None


def regime_check(record: dict[str, Any] | None,
                 policy: PromotePolicy = DEFAULT_PROMOTE_POLICY,
                 enforce: bool | None = None) -> dict[str, Any]:
    """T8 门禁判据 + **默认披露**：跨窗口符号一致性 + ``max(0.002, 2·SE)`` 门槛。

    ``enforce_regime_gate=False``（默认）时只披露（``pass=None``，不下判决），
    置 True 后 ``pass`` 才是硬判决；``require_regime_windows=True`` 时缺披露即拒绝。
    ``enforce`` 显式传入时覆盖策略（R14 不可比 ⇒ 该判据是**替代**判据，必须硬判）。
    """
    rw = candidate_regime_windows(record)
    enforced = bool(policy.enforce_regime_gate if enforce is None else enforce)
    if rw is None:
        return {
            "available": False, "pass": None,
            "enforced": enforced,
            "required": bool(policy.require_regime_windows),
            "reason": "候选未披露逐窗口 RankIC（train_lgbm 落盘的 valid_regime_windows），"
                      "无法判断跨 regime 符号一致性",
        }
    positive = int(rw.get("positive_windows", 0))
    k = int(rw.get("n_windows", 0)) or 1
    ratio = positive / k
    out = dict(rw)
    out["min_positive_ratio"] = policy.min_regime_positive_ratio
    out["pass_sign"] = bool(ratio >= policy.min_regime_positive_ratio)
    out["fail_reasons"] = [n for n, ok in (
        ("符号一致性不足", out["pass_sign"]),
        (f"窗口均值未达门槛 max({policy.min_regime_rank_ic}, "
         f"{policy.sigma_regime}·SE)", bool(rw.get("pass_level"))),
    ) if not ok]
    out["enforced"] = enforced
    out["required"] = bool(policy.require_regime_windows)
    out["pass"] = (bool(out["pass_sign"] and rw.get("pass_level"))
                   if enforced else None)
    return out


# ---------------- P1-48：水平偏置门禁维度 ----------------
def pred_level_check(candidate: dict[str, Any] | None,
                     prod: dict[str, Any] | None,
                     policy: PromotePolicy = DEFAULT_PROMOTE_POLICY) -> dict[str, Any]:
    """审计 P1-48：把"预测水平"变成门禁的**第二个维度**（RankIC 与它正交）。

    两个判据，都以**统计显著性**表述（不用凭空的绝对值阈值）：

    ① ``calibration``：候选自身的校准偏差 ``level_bias = mean(pred) − mean(label)``
       是否超过 ``sigma · level_bias_se``。截面去均值目标下预测的截面均值应≈0；
       绝对收益目标下应≈同段标签均值 ⇒ 超出即"整体水平下移/上移"。
    ② ``shift_vs_prod``：候选与生产的 ``pred_level_mean`` 之差是否超过
       ``sigma · sqrt(se_c² + se_p²)``。下游任何把 ``pred_score`` 当"预期收益"
       消费的地方（如 MVO 的 μ）看到的是绝对水平，换水平本身就是一次语义变更。

    **默认只披露**（``enforce_pred_level_gate=False``，与 T8/R14 同范式）：
    报告自陈"新增硬门槛可能挡住合法数据更新 ⇒ 用 shadow 通道而非硬拒"，
    故置 True 才成为硬判决。旧产物未落盘该统计 ⇒ ``available=False``、
    ``pass=None``（"无法判定"），既不放行也不误杀。
    """
    cand_lv = prediction_level(candidate)
    prod_lv = prediction_level(prod)
    enforced = bool(policy.enforce_pred_level_gate)
    sigma = float(policy.sigma_pred_level)
    if cand_lv is None:
        return {
            "available": False, "pass": None, "enforced": enforced,
            "reason": "候选未披露预测水平（train_lgbm 落盘的 pred_level），无法判断水平偏置",
        }
    out: dict[str, Any] = {"available": True, "enforced": enforced,
                           "sigma": sigma, "candidate": cand_lv,
                           "production": prod_lv}
    fail_reasons: list[str] = []

    bias = cand_lv.get("level_bias")
    se = cand_lv.get("level_bias_se")
    if bias is not None and se is not None and float(se) > 0:
        thr = sigma * float(se)
        ok = abs(float(bias)) <= thr
        out["calibration"] = {
            "level_bias": round(float(bias), 8), "se": round(float(se), 8),
            "threshold": round(thr, 8), "pass": bool(ok)}
        if not ok:
            fail_reasons.append("校准偏差显著")
    else:
        out["calibration"] = {"level_bias": bias, "se": se, "pass": None,
                              "reason": "候选未披露 level_bias/level_bias_se（旧产物）"}

    if prod_lv is None:
        out["shift_vs_prod"] = {"pass": None, "reason": "生产模型未落盘 pred_level（旧产物）"}
    else:
        c_mean, p_mean = cand_lv.get("pred_level_mean"), prod_lv.get("pred_level_mean")
        # 变量名不与上方 calibration 分支的 `thr`（float）复用，否则 mypy 会把
        # 函数级的 `thr` 定成 float，这里再赋 None 即报 assignment 错（CI 的
        # `mypy app/` 是硬门禁）。数值语义不变：shift/threshold 仍为 float|None。
        c_mean_f: float | None = None
        p_mean_f: float | None = None
        shift: float | None = None
        shift_thr: float | None = None
        if c_mean is not None and p_mean is not None:
            c_mean_f, p_mean_f = float(c_mean), float(p_mean)
            shift = c_mean_f - p_mean_f
            se_c = _level_se(cand_lv)
            se_p = _level_se(prod_lv)
            shift_thr = sigma * float(math.sqrt(se_c ** 2 + se_p ** 2)) if (se_c is not None
                                                                           and se_p is not None) else None
        if shift is None or shift_thr is None:
            out["shift_vs_prod"] = {
                "shift": shift, "threshold": shift_thr, "pass": None,
                "reason": "水平差或标准误缺失，无法判定"}
        else:
            assert c_mean_f is not None and p_mean_f is not None  # shift 非空即两者非空
            ok = abs(shift) <= shift_thr
            out["shift_vs_prod"] = {
                "candidate_mean": round(c_mean_f, 8),
                "production_mean": round(p_mean_f, 8),
                "shift": round(shift, 8), "threshold": round(shift_thr, 8),
                "pass": bool(ok)}
            if not ok:
                fail_reasons.append("与生产模型水平漂移显著")

    judged = [v["pass"] for v in (out["calibration"], out["shift_vs_prod"])
              if v.get("pass") is not None]
    out["fail_reasons"] = fail_reasons
    out["pass"] = (all(judged) if (enforced and judged) else None)
    if not judged:
        out["reason"] = "无任何可判定的水平统计（两侧均未落盘）"
    return out


def _level_se(level: dict[str, Any]) -> float | None:
    """预测均值的标准误 = pred_level_std / sqrt(n)（n 缺失时返回 None）。"""
    std, n = level.get("pred_level_std"), level.get("n")
    if std is None or not n:
        return None
    return float(std) / float(math.sqrt(float(n)))


# ---------------- R14：门禁比较对象的可比性 ----------------
_LINEAGE_KEYS = ("feature_version", "dataset_version", "valid_start", "valid_end")
_LINEAGE_VERSION_KEYS = ("feature_version", "dataset_version")
_LINEAGE_WINDOW_KEYS = ("valid_start", "valid_end")


def _lineage_value(record: dict[str, Any] | None, key: str) -> str | None:
    if not record:
        return None
    v = record.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def lineage_comparability(candidate: dict[str, Any] | None,
                          current: dict[str, Any] | None) -> dict[str, Any]:
    """R14：判定「候选 vs 生产」的**直接比较**是否可比。

    审计 R14：门禁此前不校验 ``feature_version/dataset_version/验证区间``，
    生产 ``ds_1133s…r2022`` 与候选 ``ds_1729s…r2018`` 直接被拿来比 IC —— 两个
    不同数据集上算出的 IC 大小没有可比性（比较对象错，不是阈值松紧问题）。

    返回字典（**默认披露**）：
        - ``pass``：True 可比 / False 有**证实**的差异 / None 不可证明（旧产物缺记）；
        - ``version_mismatch``：feature_version / dataset_version 的证实差异；
        - ``valid_window_differs``：验证区间证实不同；
        - ``unproven``：只有一边记录了值 ⇒ 无法证明可比（不额外拦，但要披露）；
        - ``reason``：人类可读摘要。
    规则与 ``basis_mismatch_reason`` 一致：**两边都缺记 ⇒ 保持历史行为**（不拦）。
    """
    version_diffs: dict[str, dict[str, Any]] = {}
    window_diffs: dict[str, dict[str, Any]] = {}
    unproven: list[str] = []
    for key in _LINEAGE_KEYS:
        cv, pv = _lineage_value(candidate, key), _lineage_value(current, key)
        if cv is None and pv is None:
            continue
        if cv is None or pv is None:
            unproven.append(key)
            continue
        if cv != pv:
            (version_diffs if key in _LINEAGE_VERSION_KEYS else window_diffs)[key] = {
                "candidate": cv, "production": pv}
    diffs = {**version_diffs, **window_diffs}
    if diffs:
        pass_: bool | None = False
    elif unproven:
        pass_ = None
    else:
        pass_ = True
    return {
        "pass": pass_,
        "version_mismatch": version_diffs,
        "valid_window_differs": bool(window_diffs),
        "window_diff": window_diffs,
        "unproven": unproven,
        "candidate": {k: _lineage_value(candidate, k) for k in _LINEAGE_KEYS},
        "production": {k: _lineage_value(current, k) for k in _LINEAGE_KEYS},
        "reason": (None if not diffs else
                   "与当前生产模型不可比：" + json.dumps(diffs, ensure_ascii=False)),
    }


def evaluate_candidate(
    candidate: dict[str, Any],
    current: dict[str, Any] | None,
    policy: PromotePolicy = DEFAULT_PROMOTE_POLICY,
) -> PromoteDecision:
    """第六阶段：候选 vs 当前生产 —— 决策只基于 validation 指标。

    比较维度（全部取 valid 段）：RankIC / IC / ICIR / RMSE。
    test 指标不参与决策，只写入注册表供审计。

    ⚠️ 审计 P1-39：**RMSE 仅在双方训练口径一致时才参与比较**。口径不同（例如
    ``xsec_demean`` 一边开一边关）时目标函数不同，RMSE 尺度系统性变化，比较结果
    无意义；此时跳过 RMSE 并记 ``basis_mismatch``，RankIC/ICIR 维度照常把关。
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
    #    审计 R14：先判**比较对象是否可比**（feature_version/dataset_version/验证区间），
    #    证实不可比时不得直接拿 IC/RMSE 比大小（比较对象错 ≠ 阈值松紧问题）。
    cmp_info = lineage_comparability(candidate, current)
    checks["lineage_comparability"] = cmp_info

    incomparable_keys = sorted(cmp_info["version_mismatch"])
    if policy.require_identical_valid_window and cmp_info["valid_window_differs"]:
        incomparable_keys = sorted(set(incomparable_keys) | set(_LINEAGE_WINDOW_KEYS))
    # 比较对象不可比时：**不得**拿 IC/RMSE 比大小，但也**不得**因此硬性冻结运维闭环。
    # T8 的做法是换判据（多窗口 regime），本函数按同一逻辑分三种去向：
    #   ① 显式 allow_incomparable_lineage ⇒ 跳过相对比较（调用方自担，已披露）；
    #   ② 候选披露了逐窗口 RankIC ⇒ **替代判据**：跨 regime 符号一致性 + max(0.002,2·SE)
    #      （此时该判据强制生效，避免"跳过=放行"）；
    #   ③ 两者都没有 ⇒ 拒绝（无法证明可比、也没有替代证据）。
    substitution = False
    if current is not None and incomparable_keys:
        if policy.allow_incomparable_lineage:
            skip_relative = True
        elif candidate_regime_windows(candidate) is not None:
            substitution = True
            skip_relative = True
        else:
            return PromoteDecision(
                False,
                f"与当前生产模型不可比（{'/'.join(incomparable_keys)} 不同），拒绝直接比较 "
                f"IC/RMSE：{cmp_info['reason']}；且候选未披露逐窗口 RankIC（无替代判据）。"
                f"请用新版 train_lgbm 重训（落盘 valid_regime_windows）或走影子通道"
                f"（allow_incomparable_lineage=True）",
                checks)
    else:
        skip_relative = bool(current is not None and incomparable_keys)

    # 审计 T8：跨 regime 判据（默认**披露**；替代判据时强制生效）
    checks["regime_windows"] = regime_check(
        candidate, policy, enforce=(policy.enforce_regime_gate or substitution))
    if substitution:
        checks["relative_gate"] = {
            "pass": None, "skipped": True, "substituted_by": "regime_windows",
            "keys": incomparable_keys,
            "reason": "训练口径/验证区间不可比（R14）：按 T8 以跨 regime 多窗口判据"
                      "替代直接相对比较（该判据已强制生效）",
        }
    elif skip_relative:
        checks["relative_gate"] = {
            "pass": None, "skipped": True,
            "keys": incomparable_keys,
            "reason": "训练口径/验证区间不可比（R14）：按 allow_incomparable_lineage 跳过"
                      "直接相对比较；绝对门槛与 regime 判据仍生效",
        }

    if checks["regime_windows"].get("available") is False and (
            policy.enforce_regime_gate or policy.require_regime_windows):
        return PromoteDecision(
            False,
            "策略要求跨窗口 regime 判据，但" + str(checks["regime_windows"]["reason"]), checks)
    if checks["regime_windows"].get("enforced") and \
            checks["regime_windows"].get("pass") is False:
        rw = checks["regime_windows"]
        return PromoteDecision(
            False,
            f"未通过跨 regime 判据（{'；'.join(rw.get('fail_reasons') or [])}）："
            f"窗口均值 {rw.get('mean_rank_ic')} / 门槛 {rw.get('threshold')} / "
            f"正向窗口 {rw.get('positive_windows')}/{rw.get('n_windows')}", checks)

    # 审计 P1-48：**水平偏置**维度（RankIC 与它正交，结构上拦不住）。
    # 默认只披露（enforce_pred_level_gate=False）；置 True 时成为硬判据。
    checks["pred_level_bias"] = pred_level_check(candidate, current, policy)
    if checks["pred_level_bias"].get("enforced") and \
            checks["pred_level_bias"].get("pass") is False:
        lv = checks["pred_level_bias"]
        shift = (lv.get("shift_vs_prod") or {}).get("shift")
        return PromoteDecision(
            False,
            f"未通过预测水平门禁（{'；'.join(lv.get('fail_reasons') or [])}）："
            f"校准偏差 {lv['calibration'].get('level_bias')}（阈值 "
            f"{lv['calibration'].get('threshold')}）/ 与生产水平差 {shift}"
            f"（阈值 {(lv.get('shift_vs_prod') or {}).get('threshold')}）——"
            f"RankIC 达标的候选仍可能带系统性水平漂移（P1-48/T3）", checks)

    if current is not None and not skip_relative:
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
            # P1-39：先判口径可比性，再决定 RMSE 是否参与决策
            cb = training_basis(candidate)
            pb = training_basis(current)
            mismatch = basis_mismatch_reason(cb, pb)
            if mismatch is not None:
                checks["rmse_vs_prod"] = {
                    "candidate": round(float(rmse), 6),
                    "production": round(float(p_rmse), 6),
                    "pass": None, "skipped": True, "reason": "basis_mismatch"}
                checks["basis_mismatch"] = {
                    "candidate": cb, "production": pb,
                    "pass": None, "reason": mismatch}
                logger.warning(
                    f"[registry] 跳过 RMSE 比较（{mismatch}）；"
                    f"RankIC/ICIR 仍按同口径把关")
            else:
                limit = p_rmse * (1 + policy.max_rmse_worsen_ratio)
                ok = rmse <= limit
                checks["rmse_vs_prod"] = {
                    "candidate": round(float(rmse), 6), "production": round(float(p_rmse), 6),
                    "limit": round(float(limit), 6), "pass": bool(ok)}
                if cb is not None and pb is not None:
                    checks["basis_mismatch"] = {
                        "candidate": cb, "production": pb, "pass": True}
                if not ok:
                    return PromoteDecision(
                        False, f"valid_rmse 恶化超限 {rmse:.4f} > {limit:.4f}", checks)

        if p_ic is not None and p_ic == p_ic and vic is not None and vic == vic:  # noqa: PLR0124  # NaN 判定的标准写法
            checks["ic_vs_prod"] = {
                "candidate": round(float(vic), 6), "production": round(float(p_ic), 6),
                "pass": bool(vic >= p_ic)}
        reason = "通过全部门槛且优于/不劣于当前生产模型"
    elif current is not None:
        # 相对比较按 R14 跳过（不可比，已披露）
        how = ("已以跨 regime 多窗口判据替代" if substitution else "已跳过并披露")
        reason = (f"通过绝对门槛；与当前生产模型不可比（{'/'.join(incomparable_keys)} 不同），"
                  f"直接相对比较{how}（R14/T8）")
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
