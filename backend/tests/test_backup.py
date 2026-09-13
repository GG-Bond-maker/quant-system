"""P3-5 备份与恢复测试：BACKUP-CREATE / SHA256 / RESTORE / DATA-INTEGRITY。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.backup import create_backup, restore_backup, verify_sha256  # noqa: E402


@pytest.fixture()
def temp_data_dir(tmp_path: Path):
    """临时数据目录：含 SQLite DB + models 目录 + 测试数据。"""
    sqlite_dir = tmp_path / "sqlite"
    sqlite_dir.mkdir()
    db_path = sqlite_dir / "aqp.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS data_jobs (id INTEGER PRIMARY KEY, status TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS feature_runs (id INTEGER PRIMARY KEY, strategy TEXT)")
    conn.execute("INSERT INTO data_jobs (status) VALUES ('SUCCESS')")
    conn.execute("INSERT INTO feature_runs (strategy) VALUES ('alpha_basic_v1')")
    conn.commit()
    conn.close()
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "models" / "test.lgbm").write_text("fake_model_data")
    return tmp_path


def test_backup_create(temp_data_dir: Path, tmp_path: Path):
    """BACKUP-CREATE：备份文件存在且非空。"""
    backup_path, sha = create_backup(temp_data_dir, output_dir=tmp_path / "backup")
    assert backup_path.exists() and backup_path.stat().st_size > 0
    assert len(sha) == 64


def test_backup_sha256(temp_data_dir: Path, tmp_path: Path):
    """BACKUP-SHA256：校验和验证通过，篡改后失败。"""
    backup_path, sha = create_backup(temp_data_dir, output_dir=tmp_path / "backup")
    assert verify_sha256(backup_path, sha) is True
    # 篡改
    backup_path.write_bytes(b"tampered")
    assert verify_sha256(backup_path, sha) is False


def test_backup_restore(temp_data_dir: Path, tmp_path: Path):
    """BACKUP-RESTORE：删除 DB → restore → 文件还原。"""
    db_path = temp_data_dir / "sqlite" / "aqp.db"
    backup_path, sha = create_backup(temp_data_dir, output_dir=tmp_path / "backup")
    # 模拟数据丢失
    db_path.unlink()
    assert not db_path.exists()
    restore_backup(backup_path, temp_data_dir)
    assert db_path.exists()


def test_backup_data_integrity(temp_data_dir: Path, tmp_path: Path):
    """BACKUP-DATA-INTEGRITY：恢复后 SQLite 数据可查、models 文件可读。"""
    backup_path, sha = create_backup(temp_data_dir, output_dir=tmp_path / "backup")
    # 删除
    (temp_data_dir / "sqlite" / "aqp.db").unlink()
    (temp_data_dir / "models" / "test.lgbm").unlink()
    # 恢复
    restore_backup(backup_path, temp_data_dir)
    # 验证 DB 数据完整
    conn = sqlite3.connect(temp_data_dir / "sqlite" / "aqp.db")
    jobs = conn.execute("SELECT status FROM data_jobs").fetchall()
    runs = conn.execute("SELECT strategy FROM feature_runs").fetchall()
    conn.close()
    assert ("SUCCESS",) in jobs
    assert ("alpha_basic_v1",) in runs
    # 验证 models 文件
    assert (temp_data_dir / "models" / "test.lgbm").read_text() == "fake_model_data"
