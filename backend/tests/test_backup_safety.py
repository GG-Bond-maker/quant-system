"""审计 P0-8 防回归：备份脚本不得破坏生产数据。

缺陷（2026-09-21 全栈审计，P0）：
  ``scripts/backup.py::main()`` 的"恢复演练"会 ``unlink()`` **生产库**
  ``data/sqlite/aqp.db`` 再从 tar 还原；tar 不完整 / SHA 不匹配 / 解压报错都会导致
  生产数据**永久丢失**。而 README:213 把它推荐为 nightly cron。
  同时 ``create_backup`` 直接打包正在写入的 ``.db``，WAL 模式下会得到
  **缺少已提交事务的撕裂副本**（且归档里不含 ``-wal``，还原即丢数据）。

本文件把四条安全属性钉死：
  1. 备份中的 SQLite 必须包含 WAL 中已提交但未回写主库的数据（一致性快照）；
  2. 恢复失败必须**回滚**，且不留 ``.pre-restore-*`` 孤儿目录；
  3. tar 路径穿越必须被拒绝；
  4. ``main()`` 演练**绝不修改生产库**（字节级比对）。
"""
from __future__ import annotations

import hashlib
import io
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from scripts import backup  # noqa: E402


def _write_sha(archive: Path) -> None:
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(str(archive) + ".sha256").write_text(
        f"{sha}  {archive.name}\n", encoding="utf-8")


def _make_wal_db(path: Path) -> sqlite3.Connection:
    """建库并写入 50 行，数据留在 -wal（主库文件不含这些页）。返回**未关闭**连接。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE t(x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    con.commit()
    return con


def test_backup_snapshot_includes_wal_committed_rows(tmp_path: Path) -> None:
    """核心断言：备份里的 DB 必须能看到 WAL 中已提交的行。"""
    data = tmp_path / "data"
    db = data / "sqlite" / "aqp.db"
    con = _make_wal_db(db)
    try:
        wal = data / "sqlite" / "aqp.db-wal"
        assert wal.exists() and wal.stat().st_size > 0, "前置条件：WAL 非空"
        backup_path, _sha = backup.create_backup(data, output_dir=tmp_path / "backup")

        with tarfile.open(backup_path, "r:gz") as tar:
            names = tar.getnames()
            assert "sqlite/aqp.db" in names
            # sidecar 不应进归档（否则"主库+过期 WAL"反而污染还原）
            assert not any(n.endswith(("-wal", "-shm")) for n in names), names
            member = tar.extractfile("sqlite/aqp.db")
            assert member is not None
            restored = tmp_path / "restored.db"
            restored.write_bytes(member.read())

        c2 = sqlite3.connect(restored)
        try:
            n = c2.execute("SELECT count(*) FROM t").fetchone()[0]
        finally:
            c2.close()
        assert n == 50, (
            f"备份中的 DB 只有 {n} 行（应为 50）—— 快照未包含 WAL 中已提交的数据，"
            "还原即丢数据")
    finally:
        con.close()


def test_restore_extract_failure_rolls_back(tmp_path: Path) -> None:
    """解压失败必须回滚到原数据，且不留 .pre-restore-* 目录。"""
    src = tmp_path / "src"
    _make_wal_db(src / "sqlite" / "aqp.db").close()
    backup_path, _ = backup.create_backup(src, output_dir=tmp_path / "backup")

    target = tmp_path / "target"
    live_db = target / "sqlite" / "aqp.db"
    live_db.parent.mkdir(parents=True, exist_ok=True)
    live_db.write_bytes(b"KEEPME-SENTINEL")

    original = tarfile.TarFile.extractall

    def _boom(self: tarfile.TarFile, *a: object, **k: object) -> None:
        raise OSError("模拟解压失败")

    tarfile.TarFile.extractall = _boom  # type: ignore[method-assign]
    try:
        with pytest.raises(OSError):
            backup.restore_backup(backup_path, target)
    finally:
        tarfile.TarFile.extractall = original  # type: ignore[method-assign]

    assert live_db.read_bytes() == b"KEEPME-SENTINEL", "解压失败后原数据被破坏"
    assert not list(target.glob("sqlite.pre-restore-*")), "回滚后遗留孤儿目录"


def test_restore_rejects_path_traversal(tmp_path: Path) -> None:
    """被篡改的归档不得写出到 data_dir 之外。"""
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        payload = b"pwned"
        info = tarfile.TarInfo("../escaped.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    _write_sha(evil)

    data = tmp_path / "data"
    data.mkdir()
    with pytest.raises(ValueError, match="不安全路径|绝对路径"):
        backup.restore_backup(evil, data)
    assert not (tmp_path / "escaped.txt").exists(), "路径穿越未被拦截"


def test_main_drill_does_not_modify_production_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() 的恢复演练不得改动生产库（字节级比对）。"""
    project = tmp_path / "project"
    data = project / "data"
    db = data / "sqlite" / "aqp.db"
    _make_wal_db(db).close()
    before = db.read_bytes()

    monkeypatch.setattr(backup, "BACKEND_ROOT", project / "backend")
    backup.main()

    assert db.exists(), "main() 删除了生产库（P0-8 回归）"
    assert db.read_bytes() == before, "main() 修改了生产库内容"
    # 演练产物应落在临时目录，data 下不得出现 .pre-restore-*
    assert not list(data.glob("sqlite.pre-restore-*"))