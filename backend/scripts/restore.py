"""scripts/restore.py：从 tar.gz 备份恢复数据（先备份当前，再解压）。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.backup import create_backup, restore_backup  # noqa: E402


def main() -> None:
    if "--file" not in sys.argv[1:]:
        print("用法: python scripts/restore.py --file backup/aqp-xxxx.tar.gz")
        sys.exit(1)
    backup_file = Path(sys.argv[sys.argv.index("--file") + 1])
    if not backup_file.exists():
        print(f"❌ 备份文件不存在: {backup_file}")
        sys.exit(1)

    data_dir = BACKEND_ROOT.parent / "data"

    # 恢复前先备份当前数据（防误恢复导致二次损坏）
    if (data_dir / "sqlite" / "aqp.db").exists():
        print("==> 恢复前自动备份当前数据...")
        pre_path, _ = create_backup(data_dir, output_dir=data_dir / "backup" / "pre_restore")
        print(f"    当前数据已备份至 {pre_path}")

    print(f"==> 恢复 {backup_file.name} → {data_dir}")
    restore_backup(backup_file, data_dir)
    print("✅ 恢复完成")


if __name__ == "__main__":
    main()
