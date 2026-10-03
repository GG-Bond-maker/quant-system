"""对照实验：量化宿主 safe-delete 守卫对「单文件删除」的开销与**语义**。

背景（2026-10-02）：全市场抓取长跑在 09:41 静默失活 —— 主线程被
``sitecustomize.py:_check_bulk_delete_guard`` 抛出的 ``SystemExit(1)`` 打死，
``ThreadPoolExecutor.__exit__`` 的 ``shutdown(wait=True)`` 又让进程硬撑 1h54m。
抓取速率 0.36 只/s，而每标的要经历 10 个分区锁的 create+unlink。

三组对照（逐操作兜底，任何 BaseException 都记账而非崩掉）：

    # A 守卫全开（宿主默认）
    ./.venv/Scripts/python.exe scripts/dev_bench_safe_delete.py

    # C 只关「批量守卫 helper」——保留回收站语义，去掉每次删除的 node 子进程
    CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR= ./.venv/Scripts/python.exe scripts/dev_bench_safe_delete.py

    # B 关闭整个 shim
    CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.venv/Scripts/python.exe scripts/dev_bench_safe_delete.py
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import time

N = 20


def _safe_unlink(p: pathlib.Path) -> str:
    """返回空串表示正常；否则返回异常名（含 BaseException）。"""
    try:
        p.unlink()
    except BaseException as e:  # noqa: BLE001 - 必须观察 SystemExit
        return type(e).__name__
    return ""


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[2]
    d = root / f".tmp_bench_{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)

    enabled = os.environ.get("CODEBUDDY_SAFE_DELETE_ENABLED", "<unset>")
    state_dir = os.environ.get("CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR")
    label = {
        "<unset>": "A 守卫全开",
        "0": "B shim 关闭",
    }.get(enabled, "?")
    if enabled != "0":
        label = "C 仅关批量守卫" if not state_dir else "A 守卫全开"

    # ---- 语义：unlink 后文件是否真的消失 ----
    probe = d / "probe.rwlock"
    probe.write_text("x", encoding="utf-8")
    err = _safe_unlink(probe)
    survived = probe.exists()

    # ---- 开销 ----
    errs: dict[str, int] = {}
    t = time.perf_counter()
    for i in range(N):
        p = d / f"x{i}.rwlock"
        p.write_text("x", encoding="utf-8")
        e = _safe_unlink(p)
        if e:
            errs[e] = errs.get(e, 0) + 1
    el = time.perf_counter() - t

    left = len([x for x in d.iterdir() if x.name != "probe.rwlock"])
    print(
        f"[{label}] ENABLED={enabled} STATE_DIR={'set' if state_dir else 'empty'}\n"
        f"   语义: unlink 后文件仍存在 = {survived}"
        + (f"（异常 {err}）" if err else "（无异常）")
        + f"\n   开销: {N} × (create+unlink) = {el:.2f}s → {el / N * 1000:.0f} ms/op"
        f"；目录残留 {left} 个"
        + (f"；异常计数 {errs}" if errs else "")
    )
    shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
