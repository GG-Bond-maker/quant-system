"""数据质量扫描 / 血缘图谱 / 调度 DAG 看板（生产端可观测性，全部真实状态）。"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field

from ...core.auth import require_role
from ...core.config import PROJECT_ROOT, get_settings
from ...core.errors import APIResponse, AQPException, ERR_DATA_EMPTY, ok

router = APIRouter()


# ==================== 数据质量扫描（复用 data.quality 引擎） ====================
class ScanRequest(BaseModel):
    year: int | None = Field(None, ge=2020, le=2100, description="扫描年份（默认最新一年）")
    dataset: str = Field("daily_bar", pattern=r"^(daily_bar|daily_bar_hfq|daily_bar_qfq)$")


@router.post("/quality-scan", response_model=APIResponse[dict])
async def quality_scan(
    req: ScanRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """对本地 parquet 跑真实 QC 检查项（缺失/零成交/复权突变/价格突变/日历）。"""
    def _run() -> dict:
        from ...data.quality import QCThresholds, validate_partition

        s = get_settings()
        base = s.DATA_ROOT / req.dataset
        # 目录不存在（全新安装 / 该数据集尚未同步）必须先兜底：直接 iterdir() 会抛
        # 未捕获的 FileNotFoundError，绕过信封契约变成 HTTP 500（2026-09-11 补测发现）。
        if not base.exists():
            raise AQPException(ERR_DATA_EMPTY, f"{req.dataset} 无数据（分区目录不存在）")
        sym_dirs = sorted(p for p in base.iterdir() if p.is_dir())
        if not sym_dirs:
            raise AQPException(ERR_DATA_EMPTY, f"{req.dataset} 无数据")
        year = req.year or 2026
        th = QCThresholds()
        issues: list[dict] = []
        rows_scanned = 0
        n_syms = 0
        for sym_dir in sym_dirs[:200]:
            sym = sym_dir.name.split("=", 1)[-1]
            files = sorted(sym_dir.glob(f"year={year}*.parquet"))
            if not files:
                continue
            df = pl.read_parquet(files[0])
            rows_scanned += df.height
            n_syms += 1
            for i in validate_partition(df, req.dataset, sym, year, th):
                issues.append({"dataset": i.dataset, "symbol": i.symbol,
                               "date": i.sample_dates[0] if i.sample_dates else None,
                               "kind": i.kind, "severity": i.severity,
                               "detail": i.detail})
        by_kind: dict[str, int] = {}
        by_symbol: dict[str, int] = {}
        # 循环变量避开上面的 QCIssue 名称 i，否则类型会在两个循环间被污染
        for iss in issues:
            by_kind[iss["kind"]] = by_kind.get(iss["kind"], 0) + 1
            by_symbol[iss["symbol"]] = by_symbol.get(iss["symbol"], 0) + 1
        return {"dataset": req.dataset, "year": year,
                "rows_scanned": rows_scanned, "symbols_scanned": n_syms,
                "n_issues": len(issues),
                "n_errors": sum(1 for i in issues if i["severity"] == "error"),
                "by_kind": by_kind,
                "worst_symbols": sorted(by_symbol.items(),
                                        key=lambda kv: -kv[1])[:10],
                "samples": issues[:50]}
    return ok(await asyncio.to_thread(_run))


# ==================== 数据血缘图谱 ====================
#
# 历史问题（审计 B1）：本端点此前返回 13 个节点 / 18 条边的写死常量数组，
# docstring 却声称"节点与边由平台真实数据流抽象"——无论磁盘上数据是否存在、
# 是否最新，前端拿到的图永远长一个样，属于展示层造假。
#
# 修复后分两层表述，各自如实标注，避免再次夸大：
#   1) **拓扑**（谁流向谁）是流水线代码的依赖关系，属于编译期事实，
#      无法从文件系统推断，因此仍以常量表达，并在响应中用
#      ``topology_source: "static"`` 显式声明；
#   2) **节点状态**（是否存在 / 最新数据日期 / 行数 / 体积 / 生产模型
#      验证集 RankIC / QC 隔离分区数 / 业务表记录数）全部来自真实扫描，
#      用 ``node_states_scanned: true`` 声明。
# 两端节点都不存在的边会被剔除：features 还没跑时，图上不会出现
# features→model 这条边，而不是像以前那样假装一切就绪。

def _scan_parquet_dataset(rel: str) -> dict:
    """扫描 DATA_ROOT/rel 下全部 parquet 的真实产物统计。

    只读 parquet footer 元数据（行数）与文件体积，不加载任何数据列，
    因此对 48MB 的 features 数据集也足够快，可安全放在 API 同步路径上。

    :param rel: 数据集相对 DATA_ROOT 的目录名，如 "daily_bar"
    :return:    {exists, files, rows, size_mb, latest_date, partitions}
    """
    root = get_settings().DATA_ROOT / rel
    files = sorted(root.rglob("*.parquet")) if root.exists() else []
    if not files:
        return {"exists": False, "files": 0, "rows": 0, "size_mb": 0.0,
                "latest_date": None, "partitions": 0}
    rows = 0
    for f in files:
        try:
            rows += pq.ParquetFile(f).metadata.num_rows
        except Exception as e:  # noqa: BLE001 单文件损坏不应让整个血缘端点失败
            logger.debug(f"[lineage] 元数据读取失败 {f}: {e!r}")
    return {
        "exists": True,
        "files": len(files),
        "rows": rows,
        "size_mb": round(sum(f.stat().st_size for f in files) / 1024 / 1024, 2),
        "latest_date": _latest_partition_date(files),
        "partitions": len({f.parent for f in files}),
    }


def _latest_partition_date(files: list[Path]) -> str | None:
    """数据集最新数据日期：优先解析 ``date=YYYYMMDD`` 分区名（零 IO），
    否则惰性只读 date 列求 max（不加载其它列）。

    :param files: 该数据集全部 parquet 路径（升序）
    :return:      YYYY-MM-DD；无 date 列或读取失败时返回 None
    """
    stems = [m.group(1) for f in files if (m := re.search(r"date=(\d{8})", str(f)))]
    if stems:
        d = max(stems)
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    try:
        lf = pl.scan_parquet(files)
        if "date" in lf.collect_schema().names():
            v = lf.select(pl.col("date").max()).collect().item()
            return str(v)[:10] if v is not None else None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[lineage] 最新日期扫描失败: {e!r}")
    return None


def _sqlite_count(table: str) -> int | None:
    """SQLite 业务表真实行数。

    :param table: 表名——仅接受本模块内硬编码常量，不接受外部输入，
                  故直接拼接 SQL 不存在注入风险。
    :return:      行数；表不存在或库不可读时返回 None
    """
    path = get_settings().SQLITE_PATH
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[lineage] sqlite 计数失败 table={table}: {e!r}")
        return None


def _model_stat() -> dict:
    """生产模型真实状态：读 model_registry（权威来源，非目录名排序）。

    只认 ``status='production'`` 的注册记录——与 ml.registry 的加载口径一致，
    避免把实验模型当成线上模型展示。
    """
    out: dict = {"exists": False, "version": None, "valid_rank_ic": None,
                 "test_rank_ic": None, "promoted_at": None, "registered": 0}
    path = get_settings().SQLITE_PATH
    if not path.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            out["registered"] = int(
                con.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0])
            row = con.execute(
                "SELECT version, valid_rank_ic, test_rank_ic, promoted_at "
                "FROM model_registry WHERE status='production' "
                "ORDER BY promoted_at DESC LIMIT 1").fetchone()
            if row is not None:
                out.update(exists=True, version=row["version"],
                           valid_rank_ic=row["valid_rank_ic"],
                           test_rank_ic=row["test_rank_ic"],
                           promoted_at=row["promoted_at"])
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[lineage] 模型注册状态读取失败: {e!r}")
    return out


def _qc_stat() -> dict:
    """QC 真实状态：data/quarantine 下被隔离的分区数（错误数据的实际落点）。"""
    qroot = PROJECT_ROOT / "data" / "quarantine"
    if not qroot.exists():
        return {"quarantined_partitions": 0, "quarantined_datasets": 0}
    files = list(qroot.rglob("*.parquet"))
    datasets = {f.relative_to(qroot).parts[0] for f in files if f.suffix == ".parquet"}
    return {"quarantined_partitions": len(files), "quarantined_datasets": len(datasets)}


# 节点声明：group 用于前端配色；kind 决定状态从哪里扫
#   dataset  -> 扫 DATA_ROOT/<dataset> 的 parquet 产物
#   table    -> 数 SQLite 业务表行数
#   model    -> 读 model_registry 生产模型
#   qc       -> 扫 quarantine 隔离区
#   external -> 外部数据源，无本地落地产物
#   derived  -> 训练期内存计算产物，不落盘
_LINEAGE_NODE_SPEC: list[dict] = [
    {"id": "akshare", "name": "AKShare 上游", "group": "source", "kind": "external",
     "desc": "行情/日历/基本面 外呼（限速+重试）"},
    {"id": "daily_bar", "name": "daily_bar 原始日线", "group": "raw",
     "kind": "dataset", "dataset": "daily_bar"},
    {"id": "daily_bar_hfq", "name": "hfq 后复权", "group": "raw",
     "kind": "dataset", "dataset": "daily_bar_hfq"},
    {"id": "daily_bar_qfq", "name": "qfq 前复权", "group": "raw",
     "kind": "dataset", "dataset": "daily_bar_qfq"},
    {"id": "qc", "name": "质量校验 quality", "group": "process", "kind": "qc",
     "desc": "schema/null/ohlc/复权突变/日历/密度，error 隔离 quarantine"},
    {"id": "universe", "name": "universe_daily 截面", "group": "raw",
     "kind": "dataset", "dataset": "universe_daily",
     "desc": "含 industry / is_st（合规与归因数据源）"},
    {"id": "features", "name": "features 因子库", "group": "feature",
     "kind": "dataset", "dataset": "features",
     "desc": "alpha_basic_v1 · 45 列（含 ret/overnight_gap/ma_gap 等因子，PIT）"},
    {"id": "labels", "name": "label_ret 标签", "group": "feature", "kind": "derived",
     "desc": "winsorize 质量策略（训练期由 ml.labeling 现算，不落盘）"},
    {"id": "model", "name": "LightGBM 生产模型", "group": "model", "kind": "model",
     "desc": "train|gap|valid|gap|test 三段切分 + registry 准入"},
    {"id": "predictions", "name": "predictions 信号", "group": "model",
     "kind": "dataset", "dataset": "predictions"},
    {"id": "screener", "name": "screener 选股快照", "group": "model",
     "kind": "dataset", "dataset": "screener"},
    {"id": "desk", "name": "模拟盘执行 desk", "group": "prod", "kind": "table",
     "table": "paper_orders",
     "desc": "算法分批 + 合规禁买池 + kill switch"},
    {"id": "attribution", "name": "归因/容量", "group": "prod", "kind": "table",
     "table": "backtest",
     "desc": "Brinson 行业 + 风格回归 + ADV 容量"},
]

# 拓扑：编译期事实（代码里的流水线依赖），无法从文件系统推断
_LINEAGE_EDGE_SPEC: list[tuple[str, str]] = [
    ("akshare", "daily_bar"), ("akshare", "universe"),
    ("daily_bar", "daily_bar_hfq"), ("daily_bar", "daily_bar_qfq"),
    ("daily_bar", "qc"), ("daily_bar_hfq", "qc"),
    ("qc", "universe"), ("universe", "features"),
    ("daily_bar_hfq", "features"), ("features", "labels"),
    ("features", "model"), ("labels", "model"),
    ("model", "predictions"), ("predictions", "screener"),
    ("screener", "desk"), ("predictions", "desk"),
    ("universe", "attribution"), ("desk", "attribution"),
]


@router.get("/lineage", response_model=APIResponse[dict])
async def data_lineage(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """数据血缘：拓扑为静态声明，节点状态由本地产物 / SQLite 真实扫描得到。

    ingest→qc→features→model→pred→desk 的依赖关系来自流水线代码（静态），
    每个节点的存在性、最新数据日期、行数、体积、生产模型 RankIC、
    QC 隔离数、业务表记录数则实时扫描，**不随数据变化而变化的只有拓扑**。
    """
    def _run() -> dict:
        scan_cache: dict[str, dict] = {}
        nodes: list[dict] = []
        for spec in _LINEAGE_NODE_SPEC:
            node = {k: spec[k] for k in ("id", "name", "group") if k in spec}
            desc = spec.get("desc")
            if desc:
                node["desc"] = desc
            kind = spec["kind"]

            if kind == "dataset":
                rel = spec["dataset"]
                # 同数据集被多个节点引用（如 features/labels）时只扫一次
                if rel not in scan_cache:
                    scan_cache[rel] = _scan_parquet_dataset(rel)
                st = scan_cache[rel]
                node.update(st)
                node["status"] = "ok" if st["exists"] else "missing"
            elif kind == "table":
                n = _sqlite_count(spec["table"])
                node["records"] = n
                # ⚠️ 0 条记录视为"无产物"（exists=False）以便隐藏其连线；
                #    注意不能写 `n is not None`——0 is not None 为 True 会误判为存在。
                node["exists"] = n is not None and n > 0
                if n:
                    node["status"] = "ok"
                elif n == 0:
                    node["status"] = "missing"
                else:
                    node["status"] = "unknown"
            elif kind == "model":
                st = _model_stat()
                node.update(st)
                node["status"] = "ok" if st["exists"] else "missing"
            elif kind == "qc":
                st = _qc_stat()
                node.update(st)
                # QC 是常驻进程节点，隔离区为空恰恰说明健康
                node["status"] = "ok"
                node["exists"] = True
            elif kind == "external":
                node["status"] = "external"
                node["exists"] = True
            else:  # derived：训练期现算，无独立产物
                node["status"] = "derived"
                node["exists"] = True
            nodes.append(node)

        by_id = {n["id"]: n for n in nodes}

        def _active(nid: str) -> bool:
            """该节点是否"真的存在"——决定其入边出边要不要画。"""
            n = by_id.get(nid)
            if n is None:
                return False
            # external / derived 无落盘产物，但有真实代码依赖，边始终保留
            return bool(n.get("exists")) or n.get("status") in ("external", "derived")

        edges = [{"source": a, "target": b} for a, b in _LINEAGE_EDGE_SPEC
                 if _active(a) and _active(b)]

        bench = max((n.get("latest_date") or "" for n in nodes
                     if n.get("status") == "ok"), default="")
        missing = [n["name"] for n in nodes if n.get("status") == "missing"]

        return {
            "nodes": nodes,
            "edges": edges,
            # 前端据此区分"静态拓扑"与"实扫状态"，避免再次被当成全动态血缘
            "topology_source": "static",
            "node_states_scanned": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "benchmark_date": bench or None,
            "missing_nodes": missing,
            "note": ("拓扑（谁流向谁）为流水线代码的静态依赖；节点状态为实时扫描："
                     "行数/体积取 parquet footer，最新日期取分区或 date 列，"
                     "生产模型取 model_registry，QC 取 quarantine 隔离数。"),
            "degraded_note": (f"以下节点尚无本地产物，相关连线已隐藏：{'、'.join(missing)}"
                              if missing else None),
        }
    return ok(await asyncio.to_thread(_run))


# ==================== 调度 DAG 看板（真实产物新鲜度） ====================
@router.get("/dag", response_model=APIResponse[dict])
async def pipeline_dag(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """每日收盘后 DAG：收割→清洗→特征→推理→选股→调仓单。

    节点状态 = **真实产物新鲜度**（数据集最新日期 vs 最新交易日），
    非装饰性状态灯；附 data_jobs 最近执行记录与单步耗时。
    """
    def _run() -> dict:
        s = get_settings()

        def latest_date(rel: str) -> str | None:
            files = sorted((s.DATA_ROOT / rel).rglob("*.parquet"))
            if not files:
                return None
            df = pl.read_parquet(files[-1])
            if "date" not in df.columns:
                return None
            return str(df["date"].max())[:10]

        def latest_mtime(rel: str) -> float:
            files = list((s.DATA_ROOT / rel).rglob("*.parquet"))
            return max((f.stat().st_mtime for f in files), default=0.0)

        trading_max = latest_date("universe_daily")
        stages = [
            {"id": "harvest", "name": "数据收割", "dataset": "daily_bar",
             "done_date": latest_date("daily_bar")},
            {"id": "qc", "name": "清洗校验", "dataset": "daily_bar_hfq",
             "done_date": latest_date("daily_bar_hfq")},
            {"id": "features", "name": "特征构建", "dataset": "features",
             "done_date": latest_date("features")},
            {"id": "infer", "name": "模型推理", "dataset": "predictions",
             "done_date": latest_date("predictions")},
            {"id": "screener", "name": "选股快照", "dataset": "screener",
             "done_date": None},
        ]
        out = []
        for st in stages:
            if st["id"] == "screener":
                files = sorted((s.DATA_ROOT / "screener").rglob("*.parquet"))
                st["done_date"] = None
                if files:
                    df = pl.read_parquet(files[-1])
                    if "date" in df.columns:
                        st["done_date"] = str(df["date"].max())[:10]
            fresh = (st["done_date"] is not None and trading_max is not None
                     and st["done_date"] >= trading_max)
            out.append({**st, "status": "ok" if fresh else "stale"})
        jobs: list[dict] = []
        try:
            with sqlite3.connect(s.SQLITE_PATH) as conn:
                conn.row_factory = sqlite3.Row
                for r in conn.execute(
                        "SELECT job_type, trade_date, status, current_step, "
                        "duration_ms, error_message, finished_at "
                        "FROM data_jobs ORDER BY id DESC LIMIT 10"):
                    jobs.append(dict(r))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dag] data_jobs 不可读: {e!r}")
        return {"benchmark_date": trading_max, "stages": out,
                "recent_jobs": jobs,
                "note": "节点状态由产物新鲜度真实推导；重跑调用真实流水线 "
                        "run_pipeline（幂等，SUCCESS 不重复执行）"}
    return ok(await asyncio.to_thread(_run))


class RerunRequest(BaseModel):
    trade_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    codes: list[str] | None = Field(None, max_length=20)


@router.post("/dag/rerun", response_model=APIResponse[dict])
async def dag_rerun(
    req: RerunRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """真实重跑每日流水线（同步等待，幂等控制由 data_jobs 保证）。

    执行 orchestrator.FULL_STEPS 全量步骤（含 rebuild_qfq / build_universe /
    build_cs_mirror），与晚间例行 jobs/evening_routine 同源，杜绝步骤集漂移
    （缺陷 2，FIX-SPEC §2）——此前不传 steps 会落到缺三步的默认子集，
    qfq / universe / 截面镜像永不重建且无提示。
    """
    from ...orchestrator import FULL_STEPS, run_pipeline

    def _run() -> dict:
        try:
            summary = run_pipeline(date.fromisoformat(req.trade_date),
                                   codes=req.codes, steps=list(FULL_STEPS))
            return {"ok": True, "summary": str(summary)[:500]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return ok(await asyncio.to_thread(_run))
