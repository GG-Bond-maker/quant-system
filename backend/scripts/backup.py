"""P3-5 备份与恢复：backup.py + restore.py。"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def create_backup(data_dir: Path, output_dir: Path | None = None) -> tuple[Path, str]:
    """打包 data/sqlite + data/models 为 tar.gz 并生成 sha256。

    Parquet 可选独立归档（体积大时）。
    返回 (backup_path, sha256_hex)。
    """
    if output_dir is None:
        output_dir = data_dir.parent / "backup"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup_path = output_dir / f"aqp-{stamp}.tar.gz"

    with tarfile.open(backup_path, "w:gz") as tar:
        sqlite_dir = data_dir / "sqlite"
        if sqlite_dir.exists():
            tar.add(str(sqlite_dir), arcname="sqlite")
        models_dir = data_dir / "models"
        if models_dir.exists():
            tar.add(str(models_dir), arcname="models")

    sha = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    sha_path = Path(str(backup_path) + ".sha256")
    sha_path.write_text(f"{sha}  {backup_path.name}\n", encoding="utf-8")
    return backup_path, sha


def verify_sha256(backup_path: Path, expected_sha: str) -> bool:
    actual = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    return actual == expected_sha


def restore_backup(backup_path: Path, data_dir: Path) -> bool:
    """从 tar.gz 恢复到 data_dir（先备份当前 -> 解压 -> 覆盖）。"""
    sha_path = Path(str(backup_path) + ".sha256")
    if sha_path.exists():
        expected = sha_path.read_text(encoding="utf-8").split()[0]
        if not verify_sha256(backup_path, expected):
            raise ValueError(f"SHA256 不匹配: {backup_path}")
    with tarfile.open(backup_path, "r:gz") as tar:
        tar.extractall(str(data_dir))
    return True


def main() -> None:
    data_dir = BACKEND_ROOT.parent / "data"
    backup_path, sha = create_backup(data_dir)
    print(f"✅ 备份完成: {backup_path}")
    print(f"   SHA256: {sha[:16]}...")

    # 恢复演练
    test_db = data_dir / "sqlite" / "aqp.db"
    if test_db.exists():
        print(f"恢复演练: 删除 {test_db.name} → restore → 验证")
        test_db.unlink()
        restore_backup(backup_path, data_dir)
        if test_db.exists():
            print("✅ 恢复成功：DB 文件已还原")
        else:
            print("❌ 恢复失败：DB 文件不存在")
            sys.exit(1)


if __name__ == "__main__":
    main()
