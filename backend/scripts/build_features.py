"""build_features：daily_bar_hfq -> alpha_basic_v1 因子宽表（P0-Major#5）。

⚠️ 特征基准纪律：因子计算基于【后复权 hfq】价格（asof 稳定，见
tests/test_feature_asof.py）。请先运行 update_daily.py 生成 daily_bar_hfq。

输出：DATA_ROOT/features/version={FEATURE_VERSION}/year=YYYY.parquet（按年分区，zstd）

用法：python scripts/build_features.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from loguru import logger  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.parquet_store import read_all_symbols, read_symbol_dataset  # noqa: E402
from app.data.parquet_store import atomic_write_parquet
from app.ml.features import (FEATURE_VERSION, apply_propagate,  # noqa: E402
                             build_factors, factor_columns)  # noqa: E402


def main() -> None:
    setup_logging()
    s = get_settings()
    out_root = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    out_root.mkdir(parents=True, exist_ok=True)

    symbols = read_all_symbols("daily_bar_hfq")
    if not symbols:
        raise SystemExit("dataset=daily_bar_hfq 为空，请先运行 scripts/update_daily.py")
    logger.info(f"build features for {len(symbols)} symbols (hfq basis)")

    frames = []
    for sym in symbols:
        df = read_symbol_dataset("daily_bar_hfq", sym)
        if not df.is_empty():
            frames.append(df.to_pandas())
    if not frames:
        raise SystemExit("无可用的 hfq 行情数据")
    import pandas as pd

    raw = pd.concat(frames, ignore_index=True)
    # 邻接 universe 冻结（P1-18）：显式重建工具按"允许重冻结"处理并留痕——这是
    # 操作员主动发起的全量重建，与夜间流水线（默认拒绝静默改 g1_ 口径）语义不同。
    from app.ml.graph import resolve_universe, write_graph_lineage
    resolved = resolve_universe(out_root, symbols, allow_drift=True)
    if resolved["drift"]["changed"]:
        logger.warning(f"[build_features] 边集指纹变化，已重冻结邻接 universe："
                       f"{resolved['drift']['reason']}")
    write_graph_lineage(out_root, resolved, len(symbols))
    feats = apply_propagate(build_factors(raw), universe=resolved["universe"])

    # 按年分区（避免按日分区产生海量微型文件）
    feats["year"] = pd.to_datetime(feats["date"]).dt.year
    for year, g in feats.groupby("year"):
        out = out_root / f"year={year}.parquet"
        atomic_write_parquet(out, g.drop(columns=["year"]))
    logger.info(f"features done: rows={len(feats)} cols={len(factor_columns(feats))} "
                f"-> {out_root}（{len(list(out_root.glob('year=*.parquet')))} 个年分区）")


if __name__ == "__main__":
    main()
