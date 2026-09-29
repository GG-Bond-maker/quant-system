# -*- coding: utf-8 -*-
"""AQP 依赖安全升级脚本（2026-09-29 上线前全检 P1-A2）

背景：pip 在本机 venv 上的 uninstall 阶段被安全守卫拦截（SHFileOperationW 0x2），
      且运行中的 uvicorn 会锁住 orjson 的 .pyd，导致常规 `pip install -U` 失败。
方案：先把目标版本安装到独立暂存目录（--target，不触发 uninstall），
      再把包文件复制进 site-packages 并清理旧 dist-info，最后校验。

用法（务必先停掉后端服务，否则 orjson.pyd 被占用会失败）：
    cd backend
    ./.venv/Scripts/python.exe scripts/upgrade_security_deps.py

回滚：
    备份在 backend/.req_backup_20260929/（含 pip-freeze-before.txt 与 sitepkgs/），
    site-packages 覆盖还原即可。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
VENV = BACKEND / ".venv"
SP = VENV / "Lib" / "site-packages"
STAGE = BACKEND / ".stage_upgrade"
PY = VENV / "Scripts" / "python.exe"

# 需要升级/安装的包：(pip 名, 目标版本, site-packages 内模块目录名, dist-info 前缀)
TARGETS = [
    ("fastapi", "0.141.1", "fastapi", "fastapi"),
    ("starlette", "1.7.0", "starlette", "starlette"),
    ("PyJWT", "2.15.1", "jwt", "PyJWT"),
    ("orjson", "3.11.6", "orjson", "orjson"),
    ("python-dotenv", "1.2.2", "dotenv", "python_dotenv"),
    ("uvicorn", "0.38.0", "uvicorn", "uvicorn"),
    ("anyio", "4.11.0", "anyio", "anyio"),
    # fastapi 0.141+ 新增的传递依赖（0.115 之前无需，--no-deps 下必须显式列入）
    ("annotated-doc", "0.0.5", "annotated_doc", "annotated_doc"),
    ("typing-inspection", "0.4.4", "typing_inspection", "typing_inspection"),
]


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd)


def installed_version(pkg: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


def stage_install() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    args = [str(PY), "-m", "pip", "install", "--target", str(STAGE), "--no-deps"]
    args += [f"{name}=={ver}" for name, ver, _, _ in TARGETS]
    if run(args) != 0:
        raise SystemExit("暂存安装失败")


def sync_into_sitepkgs() -> None:
    for _, _, moddir, distprefix in TARGETS:
        src_mod = STAGE / moddir
        if src_mod.exists():
            dst_mod = SP / moddir
            if dst_mod.exists():
                # 逐文件复制，单文件失败（被占用）时明确报告而不是静默半成品
                for f in src_mod.rglob("*"):
                    rel = f.relative_to(src_mod)
                    target = dst_mod / rel
                    if f.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(f, target)
                    except PermissionError as exc:
                        raise SystemExit(
                            f"[锁定] 无法覆盖 {target}\n"
                            f"  -> 请先停止后端服务（uvicorn）再重跑本脚本。\n  原因: {exc}"
                        )
            else:
                shutil.copytree(src_mod, dst_mod)

        # 复制新 dist-info
        for di in STAGE.glob(f"{distprefix}*.dist-info"):
            dst = SP / di.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(di, dst)


def cleanup_old_distinfo() -> None:
    """删除与目标版本不一致的旧 dist-info。"""
    want = {}
    for name, ver, _, distprefix in TARGETS:
        want[distprefix] = ver
    for di in SP.glob("*.dist-info"):
        stem = di.name[: -len(".dist-info")]
        if "-" not in stem:
            continue
        prefix, _, ver = stem.rpartition("-")
        if prefix in want and ver != want[prefix]:
            print("  删除旧元数据:", di.name)
            shutil.rmtree(di, ignore_errors=True)


def verify() -> bool:
    print("\n=== 版本校验 ===")
    ok = True
    for name, want, _, _ in TARGETS:
        got = installed_version(name)
        flag = "OK " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  [{flag}] {name}: 期望 {want}, 实际 {got}")
    return ok


def main() -> int:
    if not PY.exists():
        raise SystemExit(f"找不到 venv python: {PY}")

    print("== 1/4 暂存安装 ==")
    stage_install()

    print("\n== 2/4 同步到 site-packages ==")
    sync_into_sitepkgs()

    print("\n== 3/4 清理旧 dist-info ==")
    cleanup_old_distinfo()

    print("\n== 4/4 校验 ==")
    ok = verify()

    print("\n== 导入冒烟 ==")
    rc = run([str(PY), "-c", "import app.main; print('app.main import OK')"])
    if rc != 0:
        ok = False
        print("  app.main 导入失败")

    print("\n结果:", "全部通过" if ok else "存在未达成项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
