"""scripts/backup.py 的恢复演练（真实执行：创建 → 删除 → 恢复 → 验证）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.backup import create_backup, restore_backup  # noqa: E402


def run_drill(tmp_base: Path) -> bool:
    """真实备份恢复演练：创建 → 写入测试数据 → 备份 → 删除 → 恢复 → 验证。"""
    data_dir = tmp_base / "data"
    sqlite_dir = data_dir / "sqlite"
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    db_path = sqlite_dir / "aqp.db"

    # 1) 写入测试数据
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS data_jobs (id INTEGER PRIMARY KEY, status TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS feature_runs (id INTEGER PRIMARY KEY, strategy TEXT)")
    conn.execute("INSERT INTO data_jobs (status) VALUES ('SUCCESS')")
    conn.execute("INSERT INTO feature_runs (strategy) VALUES ('alpha_basic_v1')")
    conn.commit()
    conn.close()

    # 2) 备份
    backup_path, sha = create_backup(data_dir, output_dir=tmp_base / "backup")
    assert backup_path.exists() and backup_path.stat().st_size > 0

    # 3) 删除 DB（模拟数据丢失）
    db_path.unlink()
    assert not db_path.exists()

    # 4) 恢复
    restore_backup(backup_path, data_dir)
    assert db_path.exists()

    # 5) 验证数据完整性
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "data_jobs" in tables and "feature_runs" in tables
    jobs = conn.execute("SELECT status FROM data_jobs").fetchall()
    runs = conn.execute("SELECT strategy FROM feature_runs").fetchall()
    conn.close()
    assert ("SUCCESS",) in jobs
    assert ("alpha_basic_v1",) in runs
    return True


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ok = run_drill(Path(tmp))
        print(f"恢复演练: {'PASS' if ok else 'FAIL'}")
