"""变异验证：把每项修复改回缺陷形态 ⇒ 新增护栏用例必须变红。

方法（byte 级，保真 CRLF/LF）：
  1. 读源码字节 → 记录 md5；
  2. 精确替换片段为"缺陷形态"（带 # MUTATION 标记）；
  3. 子进程跑 `tests/test_trade_caliber_guardrails.py`（解除沙箱 safe-delete 守卫）；
  4. 还原原始字节 → 复核 md5 与变异前**逐一相等**（杜绝残留）。

运行：cd backend && .venv/Scripts/python.exe ../docs/audit/2026-10-03/_fix/run_mutations.py
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]                      # 项目根
BACKEND = ROOT / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
GUARD_TEST = "tests/test_trade_caliber_guardrails.py"

# (id, 说明, 文件, 原片段, 缺陷片段)
MUTATIONS = [
    ("#4", "last_completed_trade_date_or_last 丢弃 now（15:30 截断失效）",
     BACKEND / "app/data/parquet_store.py",
     b"return last_completed_trade_day(cal, now)",
     b"return last_completed_trade_day(cal)  # MUTATION"),
    ("#2", "build_daily_report 退回自然日 date.today()",
     BACKEND / "app/api/v1/report.py",
     b"day = last_completed_trade_day(get_calendar())",
     b"day = date.today()  # MUTATION"),
    ("#1", "auto_sync_scheduler 交易日门禁恒真（=删除门禁）",
     BACKEND / "app/services/sync_service.py",
     b"if is_trade_day(today, get_calendar()):",
     b"if True:  # MUTATION"),
    ("#2b", "startup_catchup 交易日门禁恒真（=删除门禁）",
     BACKEND / "app/jobs/evening_routine.py",
     b"if is_trade_day(today, get_calendar()):",
     b"if True:  # MUTATION"),
]


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _env() -> dict:
    e = dict(os.environ)
    for k in ("CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR",
              "CODEBUDDY_SAFE_DELETE_BULK_GUARD", "CODEBUDDY_TOOL_CALL_ID"):
        e.pop(k, None)
    e["PATH"] = "/usr/bin:/bin:" + e.get("PATH", "")
    return e


def _run_guard(test_id: str) -> tuple[int, str]:
    log = HERE / f"_mut{test_id.replace('#', '')}.log"
    proc = subprocess.run(
        [str(PY), "-m", "pytest", GUARD_TEST, "-v", "-p", "no:randomly"],
        cwd=str(BACKEND), env=_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    log.write_text(out, encoding="utf-8")
    failed = [ln for ln in out.splitlines() if ln.startswith("FAILED")]
    verdict = "RED (护栏生效)" if proc.returncode != 0 else "GREEN (护栏失效!)"
    print(f"    {verdict}；失败用例 {len(failed)}:")
    for ln in failed:
        print(f"      - {ln}")
    return proc.returncode, out


def main() -> int:
    print(f"guard test = {GUARD_TEST}\n")
    all_ok = True
    for mid, desc, path, old, new in MUTATIONS:
        raw = path.read_bytes()
        before = _md5(raw)
        if old not in raw:
            print(f"[{mid}] ✗ 未找到变异锚点，跳过：{path}")
            all_ok = False
            continue
        assert raw.count(old) == 1, f"{mid} 锚点不唯一"
        print(f"[{mid}] {desc}")
        print(f"    文件={path.relative_to(ROOT)}  md5(pre)={before}")
        path.write_bytes(raw.replace(old, new))
        try:
            code, _ = _run_guard(mid)
        finally:
            path.write_bytes(raw)  # 还原
        after = _md5(path.read_bytes())
        restored = after == before
        print(f"    md5(post)={after}  还原={'OK' if restored else '失败!'}")
        if not restored:
            all_ok = False
        if code == 0:
            all_ok = False  # 变异未变红 ⇒ 护栏失效
        print()
    print("ALL MUTATIONS RESTORED & DETECTED" if all_ok
          else "!!! 有问题，需人工检查 !!!")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
