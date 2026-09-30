"""P3-5 备份与恢复：backup.py + restore.py。

⚠️ 2026-09-21 审计 P0-8 修复（本文件此前为**数据破坏型**运维脚本）：
  1. ``main()`` 原实现会 ``unlink()`` **生产库** ``data/sqlite/aqp.db`` 后再从 tar
     还原，作为"恢复演练"。只要 tar 不完整、SHA 校验失败或解压报错，生产库就
     **永久丢失**；而 README:213 把本脚本推荐为 nightly cron，等于每晚赌一次。
     现改为在**临时目录**里做演练，绝不触碰生产数据。
  2. ``create_backup`` 原是直接 ``tar.add`` 正在写入的 ``.db`` 文件，会得到**撕裂副本**
     （WAL 模式下主库文件可能不含已提交事务）。现用 SQLite 官方 ``VACUUM INTO``
     生成一致性快照后再打包。
  3. ``restore_backup`` 的 docstring 声称"先备份当前"，实际**没有**任何保护。现实现
     真正的回滚：先把现有 ``sqlite``/``models`` 改名为 ``.pre-restore-<stamp>``，
     解压失败则原样移回。
  4. 增加 tar 成员路径校验，拒绝绝对路径与 ``..``（防被篡改的归档写出到任意位置）。
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# tar 中承载的顶层目录名
_ARCHIVE_MEMBERS = ("sqlite", "models")
_SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _readonly_uri(path: Path) -> str:
    """SQLite read-only URI（Windows 盘符与空格都需正确转义）。"""
    return "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"


def _consistent_sqlite_copy(src: Path, dst: Path) -> bool:
    """用 ``VACUUM INTO`` 生成 src 的一致性快照到 dst；失败返回 False。

    这是 SQLite 官方推荐的热备方式：它在一个读事务里重建整库，
    因此 WAL / journal 中已提交但未回写主文件的内容也会被包含。
    """
    try:
        dst.unlink(missing_ok=True)
        con = sqlite3.connect(_readonly_uri(src), uri=True)
        try:
            con.execute("VACUUM INTO ?", (str(dst),))
        finally:
            con.close()
        return dst.exists()
    except (sqlite3.Error, OSError):
        dst.unlink(missing_ok=True)
        return False


def _stage_data_dir(data_dir: Path, stage: Path) -> None:
    """把待备份内容以一致性视图复制到 stage 目录。"""
    stage.mkdir(parents=True, exist_ok=True)
    for member in _ARCHIVE_MEMBERS:
        src_dir = data_dir / member
        if not src_dir.exists():
            continue
        dst_dir = stage / member
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(src_dir.rglob("*")):
            rel = f.relative_to(src_dir)
            target = dst_dir / rel
            if f.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member == "sqlite" and f.suffix.lower() in _SQLITE_SUFFIXES:
                if _consistent_sqlite_copy(f, target):
                    continue
                # 快照失败（例如文件不是合法 SQLite）→ 退化为普通复制，不中断备份
            elif member == "sqlite" and f.name.endswith(_SQLITE_SIDECARS):
                # 同目录下同名 .db 已由 VACUUM INTO 折叠，跳过 sidecar 避免归档里
                # 出现"主库 + 过期 WAL"这种反而会污染还原结果的组合
                base = f
                for sfx in _SQLITE_SIDECARS:
                    if base.name.endswith(sfx):
                        base = base.with_name(base.name[: -len(sfx)])
                        break
                if base.exists():
                    continue
            shutil.copy2(f, target)


def create_backup(data_dir: Path, output_dir: Path | None = None) -> tuple[Path, str]:
    """打包 data/sqlite + data/models 为 tar.gz 并生成 sha256。

    SQLite 部分以 ``VACUUM INTO`` 一致性快照打包（见模块 docstring）。
    返回 (backup_path, sha256_hex)。
    """
    if output_dir is None:
        output_dir = data_dir.parent / "backup"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup_path = output_dir / f"aqp-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="aqp_backup_stage_") as tmp:
        stage = Path(tmp)
        _stage_data_dir(data_dir, stage)
        with tarfile.open(backup_path, "w:gz") as tar:
            for member in _ARCHIVE_MEMBERS:
                member_dir = stage / member
                if member_dir.exists():
                    tar.add(str(member_dir), arcname=member)

    sha = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    sha_path = Path(str(backup_path) + ".sha256")
    sha_path.write_text(f"{sha}  {backup_path.name}\n", encoding="utf-8")
    return backup_path, sha


def verify_sha256(backup_path: Path, expected_sha: str) -> bool:
    actual = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    return actual == expected_sha


def _check_member_paths(tar: tarfile.TarFile) -> None:
    """拒绝绝对路径 / ``..`` 穿越 / 链接类成员（防被篡改的归档写出到任意位置）。

    ⚠️ 安全说明（2026-09-30 上线前全检 P0-a）：
    仅校验成员**名**不足以防住归档穿越 —— 攻击者可在归档中放入
    symlink / hardlink / 设备文件 / FIFO 成员，解压时先落一个指向 ``/etc`` 的符号链接，
    再让后续普通成员"穿过"该链接写出到目标目录之外（Archive Slip，CVE-2007-4559 类）。
    Windows 因默认无建链权限而掩盖该风险，**Linux 容器中可真实利用**。
    故此处显式拒绝一切非普通文件/目录成员。
    """
    for m in tar.getmembers():
        name = m.name.replace("\\", "/")
        if name.startswith("/") or name.startswith("../") or "/../" in name:
            raise ValueError(f"归档包含不安全路径: {m.name}")
        if Path(name).is_absolute():
            raise ValueError(f"归档包含绝对路径: {m.name}")
        # 拒绝符号链接 / 硬链接 —— 这是归档穿越的核心利用手法
        if m.issym() or m.islnk():
            raise ValueError(f"归档包含链接类成员（禁止）: {m.name}")
        # 拒绝设备文件 / FIFO / 其它特殊成员 —— 只允许普通文件与目录
        if not (m.isfile() or m.isdir()):
            raise ValueError(f"归档包含非常规成员类型（禁止）: {m.name}")


def restore_backup(backup_path: Path, data_dir: Path) -> bool:
    """从 tar.gz 恢复到 data_dir（SHA 校验 → 现有数据改名保护 → 解压 → 失败即回滚）。

    与原实现的区别：**不再无条件覆盖**。恢复前把现有 ``sqlite``/``models`` 改名为
    ``<member>.pre-restore-<stamp>``；解压抛错时把它们移回原处，保证失败不丢数据。
    成功后旧的 ``.pre-restore-*`` 目录会保留，供人工确认后再删。
    """
    sha_path = Path(str(backup_path) + ".sha256")
    if sha_path.exists():
        expected = sha_path.read_text(encoding="utf-8").split()[0]
        if not verify_sha256(backup_path, expected):
            raise ValueError(f"SHA256 不匹配: {backup_path}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    moved: list[tuple[Path, Path]] = []
    data_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(backup_path, "r:gz") as tar:
        _check_member_paths(tar)
        try:
            for member in _ARCHIVE_MEMBERS:
                live = data_dir / member
                if live.exists():
                    parked = data_dir / f"{member}.pre-restore-{stamp}"
                    live.rename(parked)
                    moved.append((parked, live))
            # ⚠️ filter="data" 为纵深防御第二道闸（Python 3.12+ 提供）。
            #    它由 CPython 官方实现，会再拦一次：绝对路径、.. 穿越、
            #    链接类成员、设备文件、权限位异常等。与上面的 _check_member_paths
            #    形成"显式白名单 + 官方过滤器"的双保险。理由见该函数 docstring。
            if sys.version_info >= (3, 12):
                tar.extractall(str(data_dir), filter="data")
            else:
                tar.extractall(str(data_dir))
        except Exception:
            # 回滚：把刚移走的原数据放回，并清掉解压到一半的内容
            for member in _ARCHIVE_MEMBERS:
                live = data_dir / member
                if live.exists() and not any(p == live for _, p in moved):
                    shutil.rmtree(live, ignore_errors=True)
            for parked, live in moved:
                if live.exists():
                    shutil.rmtree(live, ignore_errors=True)
                parked.rename(live)
            raise
    return True


def main() -> None:
    data_dir = BACKEND_ROOT.parent / "data"
    backup_path, sha = create_backup(data_dir)
    print(f"✅ 备份完成: {backup_path}")
    print(f"   SHA256: {sha[:16]}...")

    # 恢复演练：**在临时目录中**做，绝不触碰生产数据。
    if not (data_dir / "sqlite").exists():
        print("(跳过恢复演练：无 data/sqlite 目录)")
        return
    with tempfile.TemporaryDirectory(prefix="aqp_restore_drill_") as tmp:
        drill_dir = Path(tmp) / "data"
        drill_dir.mkdir(parents=True, exist_ok=True)
        print(f"恢复演练: 解压到临时目录 {drill_dir} → 验证 DB 可打开")
        restore_backup(backup_path, drill_dir)
        db = drill_dir / "sqlite" / "aqp.db"
        if not db.exists():
            print("❌ 恢复失败：临时目录中不存在 aqp.db")
            sys.exit(1)
        try:
            con = sqlite3.connect(_readonly_uri(db), uri=True)
            try:
                n = con.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error as e:
            print(f"❌ 恢复失败：还原出的 DB 无法打开（{e}）")
            sys.exit(1)
        print(f"✅ 恢复演练成功：DB 可打开，包含 {n} 张表（生产数据未被修改）")


if __name__ == "__main__":
    main()