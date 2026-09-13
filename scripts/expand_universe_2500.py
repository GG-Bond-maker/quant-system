#!/usr/bin/env python
"""股票池扩容至 2500（流动性优先）+ 双 cutoff 时窗收敛 + 下游全链路重建。

**双 cutoff（2026-09 决策）**：为 lookback 保留 2023 全年，但 2023 只服务计算、
不进结果。
    CUTOFF_PRICE  = 2023-01-01  -> 价格集 daily_bar / daily_bar_hfq / daily_bar_qfq
    CUTOFF_RESULT = 2024-01-01  -> 结果集 universe_daily / universe_daily_bt /
                                   features / predictions / cs / announcements / screener

五步流水线（可用 ``--steps`` 任选）：
    select    流动性优先选股：akshare 新浪快照 ``stock_zh_a_spot`` 的成交额降序，
              归一代码 -> 排除北交所 -> 保留全部已有 daily_bar 标的 -> 补齐到 2500；
              清单落盘 ``data/universe_2500.json``。
    fetch     回补缺失标的 2023-01-01~今日 的 raw + hfq（新浪直连，3 线程，
              断点续跑：**分区存在且 min(date) <= 2023-01-05 才算完成**；仅有
              2024+ 的旧分区会被判定为「未覆盖新起始日」并重抓；qfq 留给 rebuild
              派生）。状态落盘 ``data/.expand_2500_state.json``。
    truncate  双 cutoff 时窗收敛：价格集保留 >= 2023-01-01、结果集保留 >= 2024-01-01。
              先整体备份到 ``data/_backup_pre2024_<YYYYMMDD_HHMMSS>/`` 并校验
              文件数/大小一致，再逐分区「读 -> 过滤 -> 原子替换」，分区产出为空时
              仅删除该单个文件。幂等：无 pre-cutoff 分区时整段跳过（不重复备份）。
    rebuild   按序重建下游：rebuild_qfq -> build_universe(universe_daily)
              -> build_universe_backtest(universe_daily_bt) -> build_features(v2g 全量)
              -> infer -> screener_dump。**重建按 daily_bar 实际日期范围重算，会把
              2023 行重新引入结果集，故末尾自动再跑一次结果集截断（复用
              ``_truncate_spec``，幂等），保证结果集不含 2023 行。**
    report    汇总写 ``data/expand_2500_report.json``。

用法（**必须用项目 venv**：backend/.venv/Scripts/python.exe）：
    cd backend
    ../.venv/Scripts/python.exe ../scripts/expand_universe_2500.py                 # 全流程
    ../.venv/Scripts/python.exe ../scripts/expand_universe_2500.py --steps select --limit 3
    ../.venv/Scripts/python.exe ../scripts/expand_universe_2500.py \
        --data-root /tmp/aqp_test/parquet --snapshot-json /tmp/snap.json --steps select,truncate

设计约束（与团队约定一致）：
- 全程复用平台公共写路径：``fetch_and_write_daily_bars``（含质量门禁）、
  ``parquet_store``（原子替换/分区读写）、``repair.build_qfq_dataset``、
  ``orchestrator.STEP_FUNCTIONS``。不另写行情落盘/门禁逻辑。
- qfq **不直抓**，由 ``rebuild_qfq`` 从 raw + hfq 派生（根治增量直抓基准漂移）。
- 截断绝不整目录递归删除；仅在分区产出为空时 unlink 单个文件，
  空目录用 ``os.rmdir``（仅空目录可删）。不触碰 ``data/_backup_pre2024_*``
  与 ``data/archive_legacy/``。
- 禁止 ``git gc`` / ``git prune`` / ``rm -rf`` / 任何批量删除 .git 的命令。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

try:  # 提前暴露环境问题：脚本依赖 venv 内的 polars
    import polars as pl
except Exception as exc:  # pragma: no cover - 环境自检
    print(f"[expand2500] 无法导入 polars（请用 backend/.venv/Scripts/python.exe 运行）：{exc}")
    raise

# 双 cutoff：价格集（lookback 源）保留 2023+；结果集统一截到 2024+。
CUTOFF_PRICE: date = date(2023, 1, 1)
CUTOFF_RESULT: date = date(2024, 1, 1)
# 价格集（保留到 CUTOFF_PRICE）与结果集（截到 CUTOFF_RESULT）的分流键
PRICE_DATASETS: frozenset[str] = frozenset(
    {"daily_bar", "daily_bar_hfq", "daily_bar_qfq"})
# 断点续跑覆盖判定：分区 min(date) <= 该日（容忍年初非交易日）即视为"已覆盖新起始日"
FETCH_COVER_MIN: date = date(2023, 1, 5)
TARGET_UNIVERSE: int = 2500
FETCH_ADJUSTS: tuple[str, ...] = ("", "hfq")   # qfq 由 rebuild 派生，不直抓
FETCH_WORKERS: int = 3
SOCKET_TIMEOUT: int = 60
# 北交所代码段：新浪日线不支持（且 _sina_symbol 会把 92 误映射为 sh）
BSE_PREFIXES: tuple[str, ...] = ("4", "8", "92")
# 截断时的容错：date 列之外，允许的日期列别名（announcements 用 pub_date）
DATE_COL_ALIASES: tuple[str, ...] = ("date", "pub_date")


def _cutoff_for(key: str) -> date:
    """数据集对应的截断日：价格集=2023-01-01，其余结果集=2024-01-01。"""
    return CUTOFF_PRICE if key in PRICE_DATASETS else CUTOFF_RESULT


# --------------------------------------------------------------------------- #
# 日志 / 通用小工具
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    """带时间戳并立即 flush（后台长任务可见进度）。"""
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _bootstrap(data_root: str | None) -> Path:
    """设置 DATA_ROOT（覆盖 env 并清缓存）并返回解析后的 DATA_ROOT。"""
    if data_root:
        os.environ["DATA_ROOT"] = str(Path(data_root).expanduser().resolve())
    from app.core.config import get_settings

    get_settings.cache_clear()
    root = get_settings().DATA_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# 数据集规格（截断目标）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DatasetSpec:
    """一个待截断数据集的布局描述。

    kind:
        symbol_year  ``<rel>/symbol=*/year=*.parquet``（daily_bar / universe_daily / ...）
        flat_year    ``<rel>/year=*.parquet``（features/version=*）
        date_file    ``<rel>/date=*.parquet``（predictions / screener）
        cs           ``cs/<sub>/year=*/date=*.parquet``（横截面镜像）
    """

    key: str                # 展示用 / 报告键
    kind: str
    rel: str                # 相对 DATA_ROOT 的路径
    date_col: str = "date"
    cutoff: date = CUTOFF_RESULT   # 该数据集保留 >= 此日期；价格集为 CUTOFF_PRICE


def build_specs(root: Path) -> list[DatasetSpec]:
    """枚举本次需要截断的全部数据集（features/cs 动态展开）。

    每个数据集带各自 ``cutoff``：价格集（daily_bar/_hfq/_qfq）= CUTOFF_PRICE(2023)，
    其余结果集 = CUTOFF_RESULT(2024)。
    """
    specs: list[DatasetSpec] = [
        DatasetSpec("daily_bar", "symbol_year", "daily_bar",
                    cutoff=_cutoff_for("daily_bar")),
        DatasetSpec("daily_bar_hfq", "symbol_year", "daily_bar_hfq",
                    cutoff=_cutoff_for("daily_bar_hfq")),
        DatasetSpec("daily_bar_qfq", "symbol_year", "daily_bar_qfq",
                    cutoff=_cutoff_for("daily_bar_qfq")),
        DatasetSpec("universe_daily", "symbol_year", "universe_daily"),
        DatasetSpec("universe_daily_bt", "symbol_year", "universe_daily_bt"),
        DatasetSpec("announcements", "symbol_year", "announcements",
                    date_col="pub_date"),
        DatasetSpec("predictions", "date_file", "predictions"),
        DatasetSpec("screener", "date_file", "screener"),
    ]
    feat_root = root / "features"
    if feat_root.exists():
        for vd in sorted(feat_root.glob("version=*")):
            if vd.is_dir():
                specs.append(DatasetSpec(f"features/{vd.name}", "flat_year",
                                         f"features/{vd.name}"))
    cs_root = root / "cs"
    if cs_root.exists():
        for sub in sorted(p.name for p in cs_root.iterdir() if p.is_dir()):
            specs.append(DatasetSpec(f"cs/{sub}", "cs", f"cs/{sub}"))
    return specs


def _spec_files(root: Path, spec: DatasetSpec) -> list[Path]:
    """列出规格下的所有 parquet 分区文件。"""
    if spec.kind == "symbol_year":
        return sorted((root / spec.rel).glob("symbol=*/year=*.parquet"))
    if spec.kind == "flat_year":
        return sorted((root / spec.rel).glob("year=*.parquet"))
    if spec.kind == "date_file":
        return sorted((root / spec.rel).glob("date=*.parquet"))
    if spec.kind == "cs":
        return sorted((root / spec.rel).glob("year=*/date=*.parquet"))
    raise ValueError(f"未知 kind: {spec.kind}")


def _spec_rows(files: list[Path]) -> int:
    """数据集行数（pyarrow footer 元数据，免全量读）。"""
    import pyarrow.parquet as pq

    total = 0
    for f in files:
        try:
            total += pq.ParquetFile(f).metadata.num_rows
        except Exception:  # pragma: no cover - 坏文件按 0 计，不阻塞统计
            continue
    return total


def _spec_symbols(root: Path, spec: DatasetSpec) -> int | None:
    """symbol_year / cs 类返回 symbol 维度只在 symbol_year 有意义。"""
    if spec.kind == "symbol_year":
        base = root / spec.rel
        return sum(1 for p in base.iterdir() if p.is_dir() and p.name.startswith("symbol=")) \
            if base.exists() else 0
    return None


def _spec_date_bounds(files: list[Path], spec: DatasetSpec) -> tuple[date | None, date | None]:
    """数据集实际日期范围 (min, max)（逐文件读日期列投影；无日期列返回 (None, None)）。"""
    if not files:
        return None, None
    dcol = _resolve_date_col(files[0], spec)
    if dcol is None:
        return None, None
    lo: date | None = None
    hi: date | None = None
    for f in files:
        try:
            s = pl.read_parquet(f, columns=[dcol]).get_column(dcol)
        except Exception:  # pragma: no cover - 坏文件跳过
            continue
        if not len(s):
            continue
        fmin, fmax = _as_date(s.min()), _as_date(s.max())
        if fmin is not None and (lo is None or fmin < lo):
            lo = fmin
        if fmax is not None and (hi is None or fmax > hi):
            hi = fmax
    return lo, hi


def _spec_summary(root: Path, spec: DatasetSpec) -> dict[str, Any]:
    """数据集摘要：文件数 / 行数 / 字节 / symbols / min_date / max_date。

    供报告核验「价格集含 2023、结果集不含 2023」。
    """
    files = _spec_files(root, spec)
    nbytes = 0
    for f in files:
        try:
            nbytes += f.stat().st_size
        except OSError:
            pass
    lo, hi = _spec_date_bounds(files, spec)
    return {
        "kind": spec.kind, "cutoff": spec.cutoff.isoformat(),
        "files": len(files), "rows": _spec_rows(files), "bytes": nbytes,
        "symbols": _spec_symbols(root, spec),
        "min_date": lo.isoformat() if lo else None,
        "max_date": hi.isoformat() if hi else None,
    }


_RE_YEAR = re.compile(r"year=(\d{4})")
_RE_DATE = re.compile(r"date=(\d{8})")


def _year_from_name(name: str) -> int | None:
    m = _RE_YEAR.search(name)
    return int(m.group(1)) if m else None


def _date_from_name(name: str) -> date | None:
    m = _RE_DATE.search(name)
    if not m:
        return None
    s = m.group(1)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _file_min_date(f: Path, spec: DatasetSpec) -> date | None:
    """读单分区日期列的最小值（单列投影；无候选日期列返回 None）。"""
    try:
        schema = pl.read_parquet_schema(f)
    except Exception:  # pragma: no cover
        return None
    dcol = spec.date_col if spec.date_col in schema else next(
        (c for c in DATE_COL_ALIASES if c in schema), None)
    if dcol is None:
        return None
    s = pl.read_parquet(f, columns=[dcol]).get_column(dcol)
    return _as_date(s.min()) if len(s) else None


def _spec_has_pre_cutoff(root: Path, spec: DatasetSpec) -> bool:
    """是否含 ``spec.cutoff`` 之前的数据（价格集用 2023，结果集用 2024）。

    分区按年/日切分：年份 < cutoff 年的分区整段为 pre-cutoff（文件名判定即可）；
    边界年（== cutoff 年）与无法解析命名的分区，进一步读日期列最小值确认，
    以防某分区混入早于 cutoff 的行（此时需走 rewrite 而非整段删除）。
    """
    cutoff = spec.cutoff
    for f in _spec_files(root, spec):
        if spec.kind in ("symbol_year", "flat_year"):
            y = _year_from_name(f.name)
            if y is None:
                mn = _file_min_date(f, spec)
                if mn is not None and mn < cutoff:
                    return True
                continue
            if y < cutoff.year:
                return True
            if y == cutoff.year:
                mn = _file_min_date(f, spec)
                if mn is not None and mn < cutoff:
                    return True
        else:  # date_file / cs：单文件单日
            d = _date_from_name(f.name)
            if d is not None and d < cutoff:
                return True
            if d is None:
                mn = _file_min_date(f, spec)
                if mn is not None and mn < cutoff:
                    return True
    return False


# --------------------------------------------------------------------------- #
# Step 1: 选股（流动性优先）
# --------------------------------------------------------------------------- #
def _fetch_spot_snapshot(cache_json: Path | None) -> list[dict[str, Any]]:
    """取 A 股实时快照（成交额）。返回 [{"code","name","amount"}, ...]。

    数据源：``akshare.stock_zh_a_spot()``（新浪，一次约 20s 返回 ~5600 行）。
    ``cache_json`` 已存在则直接读缓存（离线可复现）；否则抓取后写缓存。
    """
    if cache_json is not None and cache_json.exists():
        rows = json.loads(cache_json.read_text(encoding="utf-8"))
        log(f"选股快照：读本地缓存 {cache_json}（{len(rows)} 行）")
        return rows

    from app.data.ingest import akshare_adapter as ada

    log("选股快照：调用 akshare.stock_zh_a_spot()（约 20s）...")
    df = ada._safe_call(ada.get_akshare().stock_zh_a_spot)
    cols = set(df.columns)
    code_col = next((c for c in ("代码", "code") if c in cols), None)
    amt_col = next((c for c in ("成交额", "amount") if c in cols), None)
    name_col = next((c for c in ("名称", "name") if c in cols), None)
    if code_col is None or amt_col is None:
        raise ValueError(f"新浪快照缺少必要列（代码/成交额），实际列：{sorted(cols)}")
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        amt = rec.get(amt_col)
        try:
            amt_f = float(amt)
        except (TypeError, ValueError):
            amt_f = None
        rows.append({
            "code": str(rec.get(code_col, "")).strip().lower(),
            "name": str(rec.get(name_col, "")) if name_col else "",
            "amount": amt_f,
        })
    if cache_json is not None:
        cache_json.parent.mkdir(parents=True, exist_ok=True)
        cache_json.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        log(f"选股快照：已缓存 {cache_json}（{len(rows)} 行）")
    return rows


def _normalize_spot_code(raw: str) -> tuple[str | None, str | None]:
    """新浪快照代码归一：``sh600519`` -> (``600519.SH``, ``600519``)。

    排除北交所（返回 (None, 原因)）：
      - 前缀 ``bj``（北交所）新浪日线不支持；
      - 6 位代码以 4/8/92 开头（北交所段，且 92 会被 _sina_symbol 误映射为 sh）。
    返回 ``(symbol, None)`` 或 ``(None, 排除原因)``。
    """
    from app.domain.a_share_rules import code_to_symbol

    raw = (raw or "").strip().lower()
    if len(raw) < 3:
        return None, "格式异常"
    prefix, code = raw[:2], raw[2:]
    if prefix == "bj":
        return None, "北交所(前缀bj)"
    if code.startswith(BSE_PREFIXES):
        return None, f"北交所段({code[:2]})"
    try:
        sym = code_to_symbol(code)
    except ValueError as e:
        return None, f"代码无法归一({e})"
    if sym.endswith(".BJ"):
        return None, "北交所(.BJ)"
    return sym, None


def step_select(root: Path, state: dict, report: dict, *,
                limit: int, snapshot_json: Path | None) -> list[str]:
    """流动性优先选股，落盘 ``data/universe_2500.json``，返回选中 symbol 列表。"""
    from app.data.parquet_store import list_symbols_with_data

    target = max(1, limit)
    existing = sorted(list_symbols_with_data("daily_bar"))
    existing_set = set(existing)
    log(f"选股：已有 daily_bar 标的 {len(existing)} 只（全部保留，目标 {target}）")

    raw_rows = _fetch_spot_snapshot(snapshot_json)

    # 归一并排除北交所
    norm: dict[str, dict[str, Any]] = {}
    excluded_bse = 0
    excluded_other = 0
    for r in raw_rows:
        sym, reason = _normalize_spot_code(r.get("code", ""))
        if sym is None:
            if reason and "北交所" in reason:
                excluded_bse += 1
            else:
                excluded_other += 1
            continue
        amt = r.get("amount")
        # 同 symbol 多行（重复）取成交额较大者
        old = norm.get(sym)
        if old is None or (amt is not None and (old["amount"] is None or amt > old["amount"])):
            norm[sym] = {"amount": amt, "name": r.get("name", "")}
    log(f"选股：快照 {len(raw_rows)} 行 -> 归一 {len(norm)} 只"
        f"（排除北交所 {excluded_bse} / 其它 {excluded_other}）")

    # 成交额降序排名（None 视为最小，排最后；同额按 symbol 升序稳定）
    ranked = sorted(
        norm.items(),
        key=lambda kv: (kv[1]["amount"] is not None,
                        kv[1]["amount"] if kv[1]["amount"] is not None else 0.0,
                        kv[0]),
        reverse=True,
    )
    rank_of = {sym: i + 1 for i, (sym, _) in enumerate(ranked)}

    # 组装 2500：已有标的全部保留 -> 其余按成交额降序补齐
    rank_of_ranked: list[str] = [s for s, _ in ranked]
    selected: list[str] = list(existing)
    if len(selected) > target:
        # 仅在显式 --limit 小于已有数（测试用）时截断；生产 target=2500 > 1157 不触发
        selected = selected[:target]
    else:
        need = target - len(selected)
        for sym in rank_of_ranked:
            if need <= 0:
                break
            if sym not in existing_set:
                selected.append(sym)
                need -= 1

    selected_set = set(selected)
    missing_from_snap = sorted(s for s in selected if s not in norm)
    added = sorted(s for s in selected if s not in existing_set)

    # 落盘清单（symbol + 成交额 + 排名），供审计
    out_items: list[dict[str, Any]] = []
    for i, sym in enumerate(sorted(selected), 1):
        meta = norm.get(sym)
        out_items.append({
            "symbol": sym,
            "amount": (meta or {}).get("amount"),
            "rank": rank_of.get(sym),           # None = 快照缺失（兜底保留）
            "rank_selected": i,
            "in_snapshot": sym in norm,
            "source": "existing" if sym in existing_set else "snapshot",
        })
    uni_path = root.parent / "universe_2500.json"
    uni_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": target,
        "cutoff_price": CUTOFF_PRICE.isoformat(),
        "cutoff_result": CUTOFF_RESULT.isoformat(),
        "snapshot_source": str(snapshot_json) if snapshot_json else "akshare.stock_zh_a_spot",
        "snapshot_rows": len(raw_rows),
        "eligible_non_bj": len(norm),
        "excluded_bse": excluded_bse,
        "existing_kept": len(existing_set & selected_set),
        "added": len(added),
        "existing_missing_from_snapshot": missing_from_snap,
        "symbols": out_items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"选股：选中 {len(selected)} 只（新增 {len(added)}，快照缺失兜底 "
        f"{len(missing_from_snap)}）-> {uni_path}")

    state["select"] = {
        "target": target,
        "selected": len(selected),
        "existing_kept": len(existing_set & selected_set),
        "added": len(added),
        "missing_from_snapshot": len(missing_from_snap),
        "universe_json": str(uni_path),
    }
    report["selection"] = {
        "target": target,
        "snapshot_rows": len(raw_rows),
        "eligible_non_bj": len(norm),
        "excluded_bse": excluded_bse,
        "existing_kept": len(existing_set & selected_set),
        "added": len(added),
        "existing_missing_from_snapshot": missing_from_snap,
        "universe_json": str(uni_path),
    }
    return selected


# --------------------------------------------------------------------------- #
# Step 2: 回补行情（仅缺失标的，2024-01-01 ~ 今日）
# --------------------------------------------------------------------------- #
def _classify_fetch_coverage(root: Path, selected: list[str], have: set[str],
                             cover_min: date = FETCH_COVER_MIN
                             ) -> tuple[set[str], list[str], list[str]]:
    """将 ``selected`` 按断点续跑覆盖情况分类（纯函数，可离线单测）。

    **判定口径必须 raw + hfq 双满足**：仅有 ``daily_bar`` 分区不算完成——
    若该分区的 hfq 口径（``daily_bar_hfq``）缺失或最早日期晚于 ``cover_min``，
    则视为 hfq 残缺、必须重抓。否则 hfq 空洞会被 ``step_rebuild`` 静默传播到
    ``daily_bar_qfq``（qfq 由 raw + hfq 派生），造成下游 qfq 数据损坏。这正是
    run1 中断后残留 4 只 ``covered`` 假阳性标的永不重抓的根因。

    分类语义：
    - ``covered``：symbol 在 ``have`` 中，且 ``daily_bar`` 与 ``daily_bar_hfq``
      的最早日期均非 None 且均 ``<= cover_min``。
    - ``uncovered_raw``：``daily_bar`` 缺失分区或最早日期晚于 ``cover_min``
      （raw 口径本身不完整）。
    - ``uncovered_hfq``：raw 已覆盖，但 ``daily_bar_hfq`` 缺失分区或最早日期
      晚于 ``cover_min``（hfq 口径残缺）。

    不在 ``have`` 中的 symbol 不出现在任何返回列表中（与既有行为一致）：
    它们由调用方 ``todo = [s for s in selected if s not in covered]`` 兜底抓取。

    Args:
        root: 数据根目录（各数据集为其直接子目录）。
        selected: 目标 symbol 列表（形如 ``600000.SH``）。
        have: 已存在 ``daily_bar`` 分区的 symbol 集合。
        cover_min: 覆盖判定的最早日期阈值（含该日）。

    Returns:
        ``(covered, uncovered_raw, uncovered_hfq)``：已覆盖集合、raw 口径未覆盖
        列表、hfq 口径未覆盖列表（后两者保持 ``selected`` 顺序）。
    """
    covered: set[str] = set()
    uncovered_raw: list[str] = []
    uncovered_hfq: list[str] = []
    for s in selected:
        if s not in have:
            continue
        raw_min = _symbol_min_date(root, "daily_bar", s)
        if raw_min is None or raw_min > cover_min:
            uncovered_raw.append(s)
            continue
        hfq_min = _symbol_min_date(root, "daily_bar_hfq", s)
        if hfq_min is None or hfq_min > cover_min:
            uncovered_hfq.append(s)
            continue
        covered.add(s)
    return covered, uncovered_raw, uncovered_hfq


def step_fetch(root: Path, state: dict, report: dict, *,
               selected: list[str], start: date, end: date) -> dict:
    """对缺失 / 未覆盖新起始日的标的抓取 raw + hfq。

    复用 ``fetch_and_write_daily_bars``（含质量门禁）。**断点续跑判定升级**：
    仅有 daily_bar 分区不算完成——旧分区可能只覆盖 2024+（历史暂停点），
    必须满足 ``min(date) <= FETCH_COVER_MIN`` 才视为"已覆盖新起始日"，否则重抓。
    """
    from app.data.ingest import akshare_adapter as ada
    from app.data.ingest.tasks import fetch_and_write_daily_bars
    from app.data.parquet_store import manifest_invalidate

    socket.setdefaulttimeout(SOCKET_TIMEOUT)
    manifest_invalidate()  # 丢弃可能过期的 manifest，断点判定以磁盘为准
    have = set(ada_list_symbols())
    covered, uncovered_raw, uncovered_hfq = _classify_fetch_coverage(root, selected, have)
    # 合并口径：raw / hfq 任一残缺都需重抓；去重排序保证日志与状态稳定
    uncovered: list[str] = sorted(set(uncovered_raw) | set(uncovered_hfq))
    todo = [s for s in selected if s not in covered]
    log(f"回补：目标 {len(selected)} 只，已覆盖 {len(covered)} 只"
        f"（有分区但不覆盖新起始日、需重抓 {len(uncovered)} 只"
        f"（raw 缺 {len(uncovered_raw)} / hfq 缺 {len(uncovered_hfq)}）），"
        f"待抓 {len(todo)} 只（{start} ~ {end}，adjusts={FETCH_ADJUSTS}）")
    if uncovered:
        log(f"  需重抓样例（起始日不覆盖，前 10）：{uncovered[:10]}")
    if uncovered_hfq:
        log(f"  ⚠️ hfq 口径残缺样例（前 10）：{uncovered_hfq[:10]}")

    fetcher: Callable[[str, str, str, str], pl.DataFrame] = ada._fetch_daily_bar_sina
    state_path = root.parent / ".expand_2500_state.json"
    fs: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "range": [start.isoformat(), end.isoformat()],
        "source": "sina_direct",
        "covered": len(covered), "refetch_uncovered": uncovered,
        "refetch_uncovered_raw": len(uncovered_raw),
        "refetch_uncovered_hfq": len(uncovered_hfq),
        "todo": len(todo), "done": 0, "ok": [], "no_data": [], "failed": {},
    }

    def _flush() -> None:
        fs["elapsed_s"] = round(time.time() - t0, 1)
        merged = dict(state)
        merged["fetch"] = fs
        state_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")

    def _one(sym: str) -> tuple[int, list[str]]:
        return fetch_and_write_daily_bars(
            sym.split(".")[0], start.isoformat(), end.isoformat(),
            adjusts=FETCH_ADJUSTS, fetcher=fetcher)

    t0 = time.time()
    if todo:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="fetch2500") as ex:
            futs = {ex.submit(_one, s): s for s in todo}
            for fut in as_completed(futs):
                sym = futs[fut]
                fs["done"] += 1
                try:
                    total, failed_adj = fut.result()   # ⚠️ 返回 (行数, 失败口径)
                except Exception as e:  # noqa: BLE001
                    fs["failed"][sym] = f"{type(e).__name__}: {e}"[:160]
                    log(f"[{fs['done']}/{len(todo)}] {sym} FAILED {fs['failed'][sym]}")
                else:
                    if failed_adj:
                        fs["failed"][sym] = f"adjust_failed={failed_adj}"
                        log(f"[{fs['done']}/{len(todo)}] {sym} 口径失败 {failed_adj}")
                    elif total > 0:
                        fs["ok"].append(sym)
                        log(f"[{fs['done']}/{len(todo)}] {sym} {total} 行")
                    else:
                        fs["no_data"].append(sym)
                        log(f"[{fs['done']}/{len(todo)}] {sym} 无数据")
                _flush()

    manifest_invalidate()
    dur = time.time() - t0
    log(f"回补完成：成功 {len(fs['ok'])} / 无数据 {len(fs['no_data'])} / "
        f"失败 {len(fs['failed'])}，耗时 {dur/60:.1f} 分钟")
    fs["elapsed_s"] = round(dur, 1)
    state["fetch"] = fs
    report["fetch"] = {
        "covered": len(covered), "refetch_uncovered": len(uncovered),
        "refetch_uncovered_raw": len(uncovered_raw),
        "refetch_uncovered_hfq": len(uncovered_hfq),
        "todo": len(todo), "ok": len(fs["ok"]), "no_data": len(fs["no_data"]),
        "failed": dict(list(fs["failed"].items())[:50]),
        "elapsed_s": round(dur, 1),
    }
    return fs


def ada_list_symbols() -> set[str]:
    """已有 daily_bar 分区的 symbol 集合（断点续跑判定）。"""
    from app.data.parquet_store import list_symbols_with_data

    return set(list_symbols_with_data("daily_bar"))


def _file_min_date_simple(f: Path) -> date | None:
    """读单文件 ``date`` 列的 min（无 date 列 / 读失败返回 None）。"""
    try:
        schema = pl.read_parquet_schema(f)
    except Exception:  # pragma: no cover
        return None
    if "date" not in schema:
        return None
    try:
        s = pl.read_parquet(f, columns=["date"]).get_column("date")
    except Exception:  # pragma: no cover
        return None
    return _as_date(s.min()) if len(s) else None


def _symbol_min_date(root: Path, rel: str, sym: str) -> date | None:
    """某标的在 ``rel`` 数据集下的最早日期（跨年分区取 min；无分区返回 None）。

    用于断点续跑：判定已有分区是否覆盖新的抓取起始日（min <= FETCH_COVER_MIN）。
    """
    base = root / rel / f"symbol={sym}"
    if not base.exists():
        return None
    best: date | None = None
    for f in sorted(base.glob("year=*.parquet")):
        mn = _file_min_date_simple(f)
        if mn is not None and (best is None or mn < best):
            best = mn
    return best


# --------------------------------------------------------------------------- #
# Step 3: 时窗收敛（备份 + 截断）
# --------------------------------------------------------------------------- #
def _tree_stats(path: Path) -> tuple[int, int]:
    """(文件数, 总字节)。"""
    n = 0
    b = 0
    for p in path.rglob("*"):
        if p.is_file():
            n += 1
            try:
                b += p.stat().st_size
            except OSError:
                pass
    return n, b


def _backup_datasets(root: Path, state: dict) -> Path | None:
    """整体备份将被截断的数据集到 ``data/_backup_pre2024_<ts>/`` 并校验。

    目录名沿用 ``_backup_pre2024_``（历史命名，勿改以免破坏断点复用的 glob 匹配）；
    实际覆盖两个 cutoff 将删除的部分（价格集 < 2023、结果集 < 2024）。
    幂等：若 state 中已有已验证的备份目录且仍在，则直接复用（不重复拷贝）。
    返回备份根目录，无 pre-cutoff 数据时返回 None。
    """
    # 已存在可用备份 -> 复用（幂等，避免重复拷贝 ~1.5GB）
    prev = state.get("backup") or {}
    prev_dir = prev.get("dir")
    if prev.get("verified") and prev_dir and (Path(prev_dir) / "_BACKUP_DONE.json").exists():
        log(f"备份：复用 state 记录的备份 {prev_dir}（幂等，不重复拷贝）")
        return Path(prev_dir)
    # 兜底：state 未落盘（上次中断）时，扫描已完成的既有备份目录复用
    done_markers = sorted(
        (d for d in root.parent.glob("_backup_pre2024_*")
         if d.is_dir() and (d / "_BACKUP_DONE.json").exists()),
        key=lambda d: d.stat().st_mtime, reverse=True)
    if done_markers:
        log(f"备份：复用既有备份目录 {done_markers[0]}（幂等，不重复拷贝）")
        return done_markers[0]

    top_dirs = [
        "daily_bar", "daily_bar_hfq", "daily_bar_qfq",
        "universe_daily", "universe_daily_bt",
        "features", "predictions", "screener", "cs", "announcements",
    ]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = root.parent / f"_backup_pre2024_{ts}"
    backup_root.mkdir(parents=True, exist_ok=True)
    log(f"备份：开始整体复制到 {backup_root} ...")

    verified = True
    summary: dict[str, Any] = {}
    for name in top_dirs:
        src = root / name
        if not src.exists():
            continue
        dst = backup_root / name
        shutil.copytree(src, dst, dirs_exist_ok=True)
        sn, sb = _tree_stats(src)
        dn, db = _tree_stats(dst)
        ok = (sn == dn) and (sb == db)
        verified = verified and ok
        summary[name] = {"files": sn, "bytes": sb, "copied_files": dn,
                         "copied_bytes": db, "verified": ok}
        log(f"  备份 {name}: files {sn}->{dn}, bytes {sb}->{db} {'OK' if ok else 'MISMATCH'}")

    if not verified:
        raise RuntimeError(f"备份校验失败（文件数/大小不一致），中止截断：{backup_root}")
    (backup_root / "_BACKUP_DONE.json").write_text(json.dumps({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data_root": str(root),
        "cutoff_price": CUTOFF_PRICE.isoformat(),
        "cutoff_result": CUTOFF_RESULT.isoformat(),
        "datasets": summary,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"备份完成并校验通过：{backup_root}")
    state["backup"] = {"dir": str(backup_root), "verified": True, "datasets": summary}
    return backup_root


def _truncate_spec(root: Path, spec: DatasetSpec) -> dict[str, Any]:
    """截断单个数据集：逐分区读 date 列 -> 过滤（>= ``spec.cutoff``）-> 原子替换 /
    空分区删除。价格集 cutoff=2023、结果集 cutoff=2024。

    返回统计：before/after 行数与文件数、bounds、跳过原因等。
    """
    from app.data.parquet_store import atomic_write_parquet

    files = _spec_files(root, spec)
    before_rows = _spec_rows(files)
    before_files = len(files)
    symbols_before = _spec_symbols(root, spec)
    stat: dict[str, Any] = {
        "kind": spec.kind, "date_col": spec.date_col,
        "cutoff": spec.cutoff.isoformat(),
        "files_before": before_files, "rows_before": before_rows,
        "symbols_before": symbols_before,
        "files_deleted": 0, "files_rewritten": 0, "files_skipped": 0,
        "rows_deleted": 0, "skipped_reason": None,
    }
    if not files:
        stat["skipped_reason"] = "无分区文件"
        stat.update(files_after=0, rows_after=0, symbols_after=0)
        return stat

    # schema 确认：定位实际存在的日期列（无 date/pub_date 则整段跳过并说明）
    dcol = _resolve_date_col(files[0], spec)
    if dcol is None:
        stat["skipped_reason"] = f"无日期列（候选 {DATE_COL_ALIASES}），按约定跳过"
        stat.update(files_after=before_files, rows_after=before_rows,
                    symbols_after=symbols_before)
        return stat

    import pyarrow.parquet as pq

    for f in files:
        try:
            drop = _partition_action(f, dcol, spec)
        except Exception as e:  # noqa: BLE001 - 单文件异常不阻断整段
            log(f"  [warn] {f.relative_to(root)} 处理失败，跳过：{e!r}")
            stat["files_skipped"] += 1
            continue
        n = pq.ParquetFile(f).metadata.num_rows
        if drop is None:
            stat["files_skipped"] += 1
        elif drop == "delete":          # 整分区 pre-cutoff
            f.unlink(missing_ok=True)
            stat["files_deleted"] += 1
            stat["rows_deleted"] += n
        elif drop == "rewrite":         # 跨界的年分区：过滤后原子替换
            df = pl.read_parquet(f)
            kept = df.filter(pl.col(dcol) >= spec.cutoff)
            if kept.is_empty():
                f.unlink(missing_ok=True)
                stat["files_deleted"] += 1
                stat["rows_deleted"] += n
            else:
                atomic_write_parquet(f, kept)
                stat["files_rewritten"] += 1
                stat["rows_deleted"] += (df.height - kept.height)

    _cleanup_empty_dirs(root, spec)
    files_after = _spec_files(root, spec)
    stat.update(
        files_after=len(files_after),
        rows_after=_spec_rows(files_after),
        symbols_after=_spec_symbols(root, spec),
    )
    return stat


def _resolve_date_col(sample: Path, spec: DatasetSpec) -> str | None:
    """确认分区实际 schema 中的日期列；无候选列返回 None。"""
    try:
        schema = pl.read_parquet_schema(sample)
    except Exception:  # pragma: no cover
        return None
    if spec.date_col in schema:
        return spec.date_col
    for cand in DATE_COL_ALIASES:
        if cand in schema:
            return cand
    return None


def _partition_action(f: Path, dcol: str, spec: DatasetSpec) -> str | None:
    """判断单分区动作：None=保留, 'delete'=整分区 pre-cutoff, 'rewrite'=需过滤重写。

    阈值取 ``spec.cutoff``（价格集 2023 / 结果集 2024）。只读日期列（单列投影），
    对已 >= cutoff 的分区代价极低。
    """
    cutoff = spec.cutoff
    s = pl.read_parquet(f, columns=[dcol]).get_column(dcol)
    if len(s) == 0:
        return None
    mn, mx = s.min(), s.max()
    mn_d = _as_date(mn)
    mx_d = _as_date(mx)
    if mn_d is not None and mn_d >= cutoff:
        return None                       # 全部 >= cutoff，保留
    if mx_d is not None and mx_d < cutoff:
        return "delete"                   # 全部 < cutoff，整分区删除
    return "rewrite"                      # 跨界，过滤重写


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _cleanup_empty_dirs(root: Path, spec: DatasetSpec) -> None:
    """最小范围清理：仅删除因截断而变空的 symbol=*/year=* 目录（os.rmdir 仅空目录可删）。"""
    bases: list[Path] = []
    if spec.kind in ("symbol_year",):
        bases = [p for p in (root / spec.rel).glob("symbol=*") if p.is_dir()]
    elif spec.kind == "cs":
        bases = [p for p in (root / spec.rel).glob("year=*") if p.is_dir()]
    for p in bases:
        try:
            os.rmdir(p)   # 非空则抛 OSError，安全跳过
            log(f"  清理空目录 {p.relative_to(root)}")
        except OSError:
            pass


def step_truncate(root: Path, state: dict, report: dict) -> dict:
    """备份 + 按各自 cutoff 截断（价格集 2023 / 结果集 2024）。

    幂等：无 pre-cutoff 分区则整段跳过（不重复备份）。
    """
    from app.data.parquet_store import manifest_invalidate

    specs = build_specs(root)
    pre = [s for s in specs if _spec_has_pre_cutoff(root, s)]
    if not pre:
        log("截断：未发现 pre-cutoff 分区，整段跳过（幂等，不重复备份）")
        # 仍记录各数据集现状，便于报告核对
        stats = {}
        for s in specs:
            files = _spec_files(root, s)
            if not files:
                continue
            stats[s.key] = {
                "kind": s.kind, "cutoff": s.cutoff.isoformat(),
                "rows_before": _spec_rows(files), "rows_after": _spec_rows(files),
                "files_before": len(files), "files_after": len(files),
                "symbols_before": _spec_symbols(root, s), "symbols_after": _spec_symbols(root, s),
                "files_deleted": 0, "files_rewritten": 0, "files_skipped": 0,
                "rows_deleted": 0, "skipped_reason": "已无 pre-cutoff 分区（幂等跳过）",
            }
        report["truncation"] = {"already_clean": True, "datasets": stats}
        state["truncate"] = {"already_clean": True, "datasets": {
            k: {"skipped_reason": v["skipped_reason"]} for k, v in stats.items()}}
        return report["truncation"]

    log(f"截断：发现 {len(pre)} 个数据集含 pre-cutoff 分区："
        f"{[(s.key, s.cutoff.isoformat()) for s in pre]}")
    backup_dir = _backup_datasets(root, state)

    stats: dict[str, Any] = {}
    for s in pre:
        log(f"截断 {s.key} ...")
        st = _truncate_spec(root, s)
        stats[s.key] = st
        log(f"  {s.key}: rows {st['rows_before']} -> {st['rows_after']}"
            f"（删除 {st['rows_deleted']}），files {st['files_before']} -> {st['files_after']}"
            f"（删除 {st['files_deleted']}，重写 {st['files_rewritten']}）")
    manifest_invalidate()
    report["truncation"] = {
        "already_clean": False,
        "backup_dir": str(backup_dir) if backup_dir else None,
        "datasets": stats,
    }
    state["truncate"] = {"already_clean": False,
                         "backup_dir": str(backup_dir) if backup_dir else None,
                         "datasets": stats}
    return report["truncation"]


def step_truncate_results(root: Path, state: dict, report: dict) -> dict:
    """rebuild 后二次截断 **结果集**（cutoff=2024），确保重建未重新引入 2023 行。

    rebuild 的下游（build_universe / build_universe_backtest / build_features ...）
    按 daily_bar **实际日期范围**重算；加入 2023 价格后它们会把 2023 行重新写回
    结果集。故此处复用 ``_truncate_spec``（幂等）再截一次，只针对结果集 spec。
    不重复备份：回滚点已在 ``step_truncate`` 建立，此处删除的是可再生的重建产物。
    """
    from app.data.parquet_store import manifest_invalidate

    specs = [s for s in build_specs(root) if s.cutoff == CUTOFF_RESULT]
    stats: dict[str, Any] = {}
    touched: list[str] = []
    for s in specs:
        if not _spec_has_pre_cutoff(root, s):
            continue
        log(f"重建后二次截断 {s.key} ...")
        st = _truncate_spec(root, s)
        stats[s.key] = st
        touched.append(s.key)
        log(f"  {s.key}: rows {st['rows_before']} -> {st['rows_after']}"
            f"（删除 {st['rows_deleted']} 行）")
    manifest_invalidate()
    if not touched:
        log("重建后二次截断：结果集已无 pre-2024 行（幂等，无操作）")
    result = {"cutoff_result": CUTOFF_RESULT.isoformat(),
              "touched": touched, "datasets": stats}
    report["truncate_after_rebuild"] = result
    state["truncate_after_rebuild"] = result
    return result


# --------------------------------------------------------------------------- #
# Step 4: 下游全链路重建
# --------------------------------------------------------------------------- #
def _latest_trade_date(root: Path) -> date | None:
    """取当前有行情的最新交易日（抽样若干标的，交易日历一致）。"""
    from app.data.parquet_store import list_symbols_with_data, read_symbol_dataset

    syms = sorted(list_symbols_with_data("daily_bar_hfq"))
    best: date | None = None
    for s in syms[:8]:
        df = read_symbol_dataset("daily_bar_hfq", s, columns=["date"])
        if not df.is_empty():
            m = _as_date(df["date"].max())
            if m is not None and (best is None or m > best):
                best = m
    return best


def step_rebuild(root: Path, state: dict, report: dict, *, trade_date: date | None) -> dict:
    """按序重建下游（fail-fast）。features 强制全量（新标的需回填历史特征）。

    ⚠️ 末尾自动执行 ``step_truncate_results``：重建按 daily_bar **实际日期范围**
    重算，2023 价格会让 universe_daily / universe_daily_bt / features / predictions
    / cs / screener 重新出现 2023 行，必须再截一次（幂等）才能保证结果集无 2023。
    """
    from app.orchestrator import STEP_FUNCTIONS
    from app.data.universe import build_universe_backtest

    # 增量特征只补「最新特征日之后」的行，无法为新增标的回填历史特征 ->
    # 强制全量重建（FEATURE_INCREMENTAL=0），否则新标的在重叠段无特征。
    os.environ["FEATURE_INCREMENTAL"] = "0"

    td = trade_date or _latest_trade_date(root)
    if td is None:
        raise RuntimeError("无法确定最新交易日（daily_bar_hfq 为空）")
    log(f"重建：trade_date={td}，features 强制全量")

    def _bt() -> str:
        """回测宇宙（hfq 口径）：universe_daily_bt。"""
        df = build_universe_backtest(persist=True)
        return f"rows={df.height} symbols={df['symbol'].n_unique()}"

    seq: list[tuple[str, Callable[[], str]]] = [
        ("rebuild_qfq", lambda: STEP_FUNCTIONS["rebuild_qfq"](td, [])),
        ("build_universe", lambda: STEP_FUNCTIONS["build_universe"](td, [])),
        ("build_universe_bt", _bt),
        ("build_features", lambda: STEP_FUNCTIONS["build_features"](td, [])),
        ("infer", lambda: STEP_FUNCTIONS["infer"](td, [])),
        ("screener_dump", lambda: STEP_FUNCTIONS["screener_dump"](td, [])),
    ]
    results: dict[str, Any] = {}
    for name, fn in seq:
        t0 = time.time()
        log(f"重建 [{name}] start ...")
        try:
            detail = fn()
        except Exception as e:  # noqa: BLE001 - fail-fast：下游依赖上游
            results[name] = {"status": "FAILED", "error": f"{type(e).__name__}: {e}"[:300],
                             "elapsed_s": round(time.time() - t0, 1)}
            log(f"重建 [{name}] FAILED: {e!r}")
            break
        results[name] = {"status": "OK", "detail": str(detail),
                         "elapsed_s": round(time.time() - t0, 1)}
        log(f"重建 [{name}] ok: {detail}（{results[name]['elapsed_s']}s）")

    report["rebuild"] = {"trade_date": td.isoformat(), "steps": results}

    # ⚠️ 必做：二次截断结果集，抹掉重建重新引入的 2023 行（幂等）。
    post = step_truncate_results(root, state, report)
    report["rebuild"]["post_truncate_touched"] = post["touched"]
    log(f"重建后二次截断结果集：touched={post['touched'] or '（无，已干净）'}")

    state["rebuild"] = report["rebuild"]
    return report["rebuild"]


# --------------------------------------------------------------------------- #
# Step 5: 报告
# --------------------------------------------------------------------------- #
def step_report(root: Path, state: dict, report: dict) -> Path:
    """写 ``data/expand_2500_report.json``（含每数据集 min/max/rows 与磁盘占用）。"""
    from app.data.parquet_store import list_symbols_with_data

    after_syms = list_symbols_with_data("daily_bar")
    specs = build_specs(root)
    datasets = {s.key: _spec_summary(root, s) for s in specs}
    total_files, total_bytes = _tree_stats(root)
    baseline = state.get("disk_bytes_baseline")
    baseline_mb = round(baseline / 1024 / 1024, 1) if isinstance(baseline, int) else None
    report["final"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_root": str(root),
        "cutoff_price": CUTOFF_PRICE.isoformat(),
        "cutoff_result": CUTOFF_RESULT.isoformat(),
        "daily_bar_symbols_after": len(after_syms),
        "daily_bar_rows_after": datasets.get("daily_bar", {}).get("rows"),
        "datasets": datasets,
        "disk": {
            "files": total_files,
            "bytes": total_bytes,
            "mb": round(total_bytes / 1024 / 1024, 1),
            "baseline_bytes": baseline if isinstance(baseline, int) else None,
            "baseline_mb": baseline_mb,
            "delta_mb": (round((total_bytes - baseline) / 1024 / 1024, 1)
                         if isinstance(baseline, int) else None),
        },
    }
    path = root.parent / "expand_2500_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    fin = report["final"]
    log(f"报告：{path}（DATA_ROOT={fin['disk']['mb']} MB，"
        f"较基线 {fin['disk']['delta_mb']} MB）")
    return path


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
ALL_STEPS = ["select", "fetch", "truncate", "rebuild", "report"]


def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover
            return {}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="股票池扩容 2500（流动性优先）+ 双 cutoff 时窗收敛"
                    "（价格集 2023 / 结果集 2024）")
    ap.add_argument("--steps", default=",".join(ALL_STEPS),
                    help=f"逗号分隔，从 {ALL_STEPS} 选；默认全部")
    ap.add_argument("--data-root", default=None, help="覆盖 DATA_ROOT（测试用临时目录）")
    ap.add_argument("--limit", type=int, default=TARGET_UNIVERSE, help="目标池大小（默认 2500）")
    ap.add_argument("--start", default=CUTOFF_PRICE.isoformat(),
                    help="回补起始日 YYYY-MM-DD（默认 2023-01-01，价格集 lookback 起点）")
    ap.add_argument("--end", default=date.today().isoformat(), help="回补结束日 YYYY-MM-DD")
    ap.add_argument("--snapshot-json", default=None,
                    help="选股快照缓存 JSON（离线/可复现；缺省则实时抓 akshare）")
    ap.add_argument("--trade-date", default=None, help="重建记账交易日（默认取最新行情日）")
    args = ap.parse_args()

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    unknown = [s for s in steps if s not in ALL_STEPS]
    if unknown:
        ap.error(f"未知步骤 {unknown}，可选 {ALL_STEPS}")

    root = _bootstrap(args.data_root)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    snap = Path(args.snapshot_json).expanduser().resolve() if args.snapshot_json else None
    if snap is None:
        snap = root.parent / f".spot_snapshot_{date.today():%Y%m%d}.json"
    td = date.fromisoformat(args.trade_date) if args.trade_date else None

    state_path = root.parent / ".expand_2500_state.json"
    state = _load_state(state_path)
    state.setdefault("started_at", datetime.now().isoformat(timespec="seconds"))
    # 磁盘基线（本次运行前，用于报告"新增占用"）：仅首次运行时记录，续跑沿用
    if not isinstance(state.get("disk_bytes_baseline"), int):
        state["disk_bytes_baseline"] = _tree_stats(root)[1]
    report: dict[str, Any] = state.get("report", {}) if isinstance(state.get("report"), dict) else {}
    log(f"DATA_ROOT={root}")
    log(f"steps={steps} limit={args.limit} snapshot={snap}")

    selected: list[str] = []
    t_all = time.time()
    try:
        if "select" in steps:
            selected = step_select(root, state, report,
                                   limit=args.limit, snapshot_json=snap)
        else:
            uni = root.parent / "universe_2500.json"
            if uni.exists():
                selected = [x["symbol"] for x in json.loads(uni.read_text(encoding="utf-8"))["symbols"]]
                log(f"选股：跳过，复用 {uni}（{len(selected)} 只）")
            else:
                selected = sorted(ada_list_symbols())

        if "fetch" in steps:
            step_fetch(root, state, report, selected=selected, start=start, end=end)

        if "truncate" in steps:
            step_truncate(root, state, report)

        if "rebuild" in steps:
            step_rebuild(root, state, report, trade_date=td)

        if "report" in steps:
            p = step_report(root, state, report)
            state["report_path"] = str(p)
    finally:
        state["report"] = report
        state["elapsed_s"] = round(time.time() - t_all, 1)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"状态：{state_path}（总耗时 {state['elapsed_s']}s）")
    log("完成。")


if __name__ == "__main__":
    main()
