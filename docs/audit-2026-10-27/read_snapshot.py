"""Read-only audit: aggregate local database and parquet provenance."""
from pathlib import Path
import json
import sqlite3

import polars as pl
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
env = dotenv_values(ROOT / ".env")
db_url = env.get("SQLITE_URL") or ""
db = Path(db_url.split("///", 1)[1]) if "///" in db_url else ROOT / "data/sqlite/aqp.db"
data = Path(env.get("DATA_ROOT") or ROOT / "data/parquet")
out = {"database": str(db), "data_root": str(data), "tables": {}, "parquet": {}}
with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
    conn.execute("PRAGMA query_only=ON")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in {
        "instrument": ["list_date", "delist_date"],
        "trade_calendar": ["trade_date"],
        "financial_report": ["period", "announce_date"],
        "news_announcement": ["publish_time"],
        "model_registry": ["created_at"],
        "feature_runs": ["created_at"],
        "data_jobs": ["trade_date"],
        "screener_snapshot": ["date"],
        "backtest": ["created_at"],
        "paper_orders": ["created_at"],
        "paper_fills": ["exec_date"],
        "conditional_orders": ["created_at"],
        "grid_bots": ["created_at"],
    }.items():
        if table not in names:
            continue
        actual = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        row = {"count": conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]}
        for column in columns:
            if column in actual:
                row[column] = conn.execute(f'SELECT MIN("{column}"),MAX("{column}") FROM "{table}"').fetchone()
        out["tables"][table] = row
    if "data_jobs" in names:
        out["latest_jobs"] = conn.execute("SELECT trade_date,status,current_step FROM data_jobs ORDER BY id DESC LIMIT 5").fetchall()
for name in ["features", "predictions", "daily_bar_hfq", "universe_daily", "universe_daily_bt", "announcements", "text_features"]:
    base = data / name
    files = sorted(base.rglob("*.parquet")) if base.exists() else []
    row = {"files": len(files)}
    if files:
        # Select one recent file only; do not scan full-market bar contents.
        chosen = max(files, key=lambda p: p.stat().st_mtime)
        frame = pl.read_parquet(chosen)
        row.update(file=str(chosen.relative_to(data)), rows=frame.height, columns=frame.columns)
        if "date" in frame.columns:
            row["dates"] = [str(frame["date"].min()), str(frame["date"].max())]
        for col in ["model_version", "feature_version", "label_horizon"]:
            if col in frame.columns:
                row[col] = frame[col].unique().to_list()[:8]
    out["parquet"][name] = row
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
