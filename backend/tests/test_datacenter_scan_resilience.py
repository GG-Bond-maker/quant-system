"""datacenter 数据集扫描韧性回归（2026-09-19）。

真 bug：``announcements`` 整行从 ``/datasets`` 响应里消失
--------------------------------------------------------
``_scan_dataset`` 在 symbol 分区分支**无条件**读 ``columns=["date"]``，而 announcements
的 parquet schema 是 ``['symbol','pub_date','title','type','sentiment','source','url']``
——**没有 date 列** ⇒ ``ColumnNotFoundError('date')`` 被数据集级 ``except`` 吞掉 ⇒ 返回
None ⇒ ``/datasets`` 里 ``if not st: continue`` 把「公告摘要」整行删掉。

两层修法（缺一不可）：
1. **日期列可配置**（``DATASET_DATE_COLUMN``：announcements -> ``pub_date``）；列不存在时
   不读该列，行数走 pyarrow footer 元数据，日期区间留 ``None``；
2. **按文件隔离**：单文件损坏不得让整个数据集消失（此前形态是「一个坏文件 → 整行消失」，
   属本项目零容忍的「静默给出错误信息」）。

测试纪律：**写真实 parquet 文件**，禁止 monkeypatch 替换被测函数。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from loguru import logger

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import datacenter as dc  # noqa: E402
from app.core.config import get_settings  # noqa: E402

ANN_SYMBOL = "__all__"


def _write(df: pl.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return path


@pytest.fixture()
def data_root(monkeypatch, tmp_path: Path) -> Path:
    """把 DATA_ROOT 隔离到 tmp_path（不触碰真实 data/）。"""
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    return tmp_path


def _seed_announcements(root: Path, days: list[date]) -> None:
    """announcements 的真实 schema：**没有 date 列**，日期在 ``pub_date``。"""
    df = pl.DataFrame({
        "symbol": [f"60000{i}.SH" for i in range(len(days))],
        "pub_date": days,
        "title": [f"公告{i}" for i in range(len(days))],
        "type": ["定期报告"] * len(days),
        "sentiment": [0.1] * len(days),
        "source": ["cninfo"] * len(days),
        "url": [f"http://example.com/{i}" for i in range(len(days))],
    })
    _write(df, root / "announcements" / f"symbol={ANN_SYMBOL}" / "year=2024.snappy.parquet")


def _seed_daily_bar(root: Path, symbol: str, days: list[date]) -> None:
    df = pl.DataFrame({"symbol": [symbol] * len(days), "date": days,
                       "open": [1.0] * len(days), "close": [1.0] * len(days)})
    _write(df, root / "daily_bar" / f"symbol={symbol}" / "year=2024.snappy.parquet")


def _capture_warnings() -> tuple[list[str], int]:
    msgs: list[str] = []
    sink_id = logger.add(lambda m: msgs.append(str(m.record["message"])), level="WARNING")
    return msgs, sink_id


# ---------------------------------------------------------------------------
# 缺陷 4 主用例：announcements（无 date 列）不再整行消失
# ---------------------------------------------------------------------------
def test_scan_announcements_uses_pub_date_and_is_not_none(data_root: Path) -> None:
    """announcements 用 pub_date 取区间 ⇒ _scan_dataset 返回非 None 且区间正确。

    修法前：读 columns=["date"] 抛 ColumnNotFoundError ⇒ 返回 None ⇒ 整行被删。
    """
    days = [date(2024, 3, 1), date(2024, 5, 20), date(2024, 12, 31)]
    _seed_announcements(data_root, days)

    meta = dc._scan_dataset(data_root / "announcements", "announcements")
    assert meta is not None, "announcements 仍返回 None（缺陷 4 未修复）"
    assert meta["rows"] == len(days), f"行数应走 footer 元数据: {meta}"
    assert meta["symbols"] == 1
    assert meta["bytes"] > 0
    # 显式期望：按 DATASET_DATE_COLUMN 映射到 pub_date，区间**应当**取到
    assert meta["start"] == "2024-03-01", meta
    assert meta["end"] == "2024-12-31", meta


def test_scan_all_datasets_includes_announcements(data_root: Path) -> None:
    """装配层：/datasets 的清单里 announcements 与 daily_bar 都在（不再缺席）。"""
    _seed_announcements(data_root, [date(2024, 3, 1), date(2024, 3, 2)])
    _seed_daily_bar(data_root, "600000.SH", [date(2024, 3, 1), date(2024, 3, 4)])

    items = dc._scan_all_datasets()

    assert "announcements" in items, f"announcements 从清单里消失: {sorted(items)}"
    assert "daily_bar" in items
    assert items["announcements"]["rows"] == 2
    assert items["daily_bar"]["rows"] == 2
    assert items["daily_bar"]["start"] == "2024-03-01"

    # 端到端装配：/datasets 的 items 列表里必须出现这两个 key
    rendered = {row["dataset"] for row in _render_datasets(items)}
    assert "announcements" in rendered
    assert "daily_bar" in rendered


def _render_datasets(items: dict[str, dict]) -> list[dict]:
    """复刻 /datasets 端点的装配逻辑（`if not st: continue` 是整行消失的那一步）。"""
    out: list[dict] = []
    for key, meta in dc.DATASET_META.items():
        st = items.get(key)
        if not st:
            continue
        out.append({"dataset": key, "start": st["start"], "end": st["end"],
                    "rows": st["rows"], "symbols": st["symbols"], "bytes": st["bytes"]})
    return out


# ---------------------------------------------------------------------------
# 日期列缺失的通用容忍（非 announcements 的数据集）
# ---------------------------------------------------------------------------
def test_scan_tolerates_dataset_without_any_date_column(data_root: Path) -> None:
    """没有任何日期列的数据集：返回非 None、行数正确、区间为 None（不抛、不消失）。"""
    _write(pl.DataFrame({"symbol": ["A", "B"], "rank": [1, 2]}),
           data_root / "screener" / f"symbol={ANN_SYMBOL}" / "year=2024.snappy.parquet")

    meta = dc._scan_dataset(data_root / "screener", "screener")
    assert meta is not None
    assert meta["rows"] == 2
    assert meta["start"] is None and meta["end"] is None


def test_scan_generic_branch_tolerates_missing_date_column(data_root: Path) -> None:
    """非 symbol 分区（通用 rglob 分支）同样容忍缺日期列。"""
    _write(pl.DataFrame({"title": ["x"], "pub_date": [date(2024, 1, 2)]}),
           data_root / "features" / "all.snappy.parquet")

    meta = dc._scan_dataset(data_root / "features", "features")
    assert meta is not None
    assert meta["rows"] == 1
    assert meta["symbols"] == 0
    assert meta["start"] is None and meta["end"] is None  # features 映射仍是 date，非 pub_date


# ---------------------------------------------------------------------------
# 按文件隔离：一个坏文件不得让整个数据集消失
# ---------------------------------------------------------------------------
def test_scan_isolates_corrupt_file_and_keeps_others(data_root: Path) -> None:
    """一个文件损坏 ⇒ 仍返回其余文件的统计（不是 None），并留下 warning。"""
    _seed_daily_bar(data_root, "600000.SH", [date(2024, 3, 1), date(2024, 3, 4),
                                             date(2024, 3, 5)])
    corrupt = data_root / "daily_bar" / "symbol=000001.SZ" / "year=2024.snappy.parquet"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"this is definitely not a parquet file")

    msgs, sink_id = _capture_warnings()
    try:
        meta = dc._scan_dataset(data_root / "daily_bar", "daily_bar")
    finally:
        logger.remove(sink_id)

    assert meta is not None, "坏文件让整个数据集消失（缺陷 4 的「静默」形态未修复）"
    assert meta["rows"] == 3, f"应保留好文件的行数: {meta}"
    assert meta["symbols"] == 2, "符号数按目录计，坏文件所在 symbol 目录仍在"
    assert meta["start"] == "2024-03-01" and meta["end"] == "2024-03-05"
    hits = [m for m in msgs if "不可读/损坏" in m]
    assert hits, f"坏文件必须留 warning（不得静默）: {msgs}"
    assert "已跳过" in hits[0]


def test_scan_corrupt_only_dataset_returns_none(data_root: Path) -> None:
    """全坏 ⇒ 行数 0 ⇒ 仍返回 None（真空/不可用数据集不该显示在清单里）。"""
    corrupt = data_root / "daily_bar" / "symbol=600000.SH" / "year=2024.snappy.parquet"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"\x00\x01\x02 not parquet")

    msgs, sink_id = _capture_warnings()
    try:
        meta = dc._scan_dataset(data_root / "daily_bar", "daily_bar")
    finally:
        logger.remove(sink_id)

    assert meta is None
    assert any("不可读/损坏" in m for m in msgs), "坏文件必须留 warning"


def test_scan_zero_row_dataset_returns_none(data_root: Path) -> None:
    """既有行为保留：rows == 0 ⇒ None。"""
    _write(pl.DataFrame({"symbol": [], "pub_date": []}, schema_overrides={
        "symbol": pl.Utf8, "pub_date": pl.Date}),
        data_root / "announcements" / f"symbol={ANN_SYMBOL}" / "year=2024.snappy.parquet")

    assert dc._scan_dataset(data_root / "announcements", "announcements") is None


# ---------------------------------------------------------------------------
# 兼容性：dataset 参数缺省取 root.name（既有直接调用/测试不受影响）
# ---------------------------------------------------------------------------
def test_scan_dataset_defaults_dataset_to_root_name(data_root: Path) -> None:
    """不传 dataset ⇒ 取 root.name，因此 announcements 仍能拿到 pub_date 映射。"""
    _seed_announcements(data_root, [date(2024, 6, 6)])

    meta = dc._scan_dataset(data_root / "announcements")  # 不传第二参
    assert meta is not None
    assert meta["rows"] == 1
    assert meta["start"] == "2024-06-06"


def test_dataset_date_column_map_is_explicit() -> None:
    """映射表本身：announcements -> pub_date，默认 date。"""
    assert dc.DATASET_DATE_COLUMN.get("announcements") == "pub_date"
    assert dc.DEFAULT_DATE_COLUMN == "date"
    # 所有登记的数据集 key 都必须在 DATASET_META 里（防拼写漂移）
    assert set(dc.DATASET_DATE_COLUMN) <= set(dc.DATASET_META)
