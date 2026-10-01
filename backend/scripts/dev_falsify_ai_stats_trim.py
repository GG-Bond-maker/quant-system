"""一次性证伪：证明 `test_ai_stats_year_trim.py` 真的能抓到"退回全量 glob"。

把 market.py 的裁剪块换回旧的全量 glob → 跑测试 → 必须 FAIL → 无论成败都恢复原文。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TARGET = BACKEND_ROOT / "app" / "api" / "v1" / "market.py"
TEST = "tests/test_ai_stats_year_trim.py"
PY = str(BACKEND_ROOT / ".venv" / "Scripts" / "python.exe")

MARKER = "        # 按预测日跨度**裁剪** hfq 分区（2026-10-01）："
OLD_LINE = (
    '        hfq_files = sorted((s.DATA_ROOT / "daily_bar_hfq")'
    '.glob("symbol=*/year=*.snappy.parquet"))\n'
)


def run_test() -> tuple[int, str]:
    p = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--tb=line", "-p", "no:randomly"],
                       cwd=str(BACKEND_ROOT), capture_output=True, text=True, timeout=300)
    tail = [ln for ln in p.stdout.splitlines() if "passed" in ln or "failed" in ln]
    return p.returncode, " | ".join(tail[-2:])


def main() -> int:
    orig = TARGET.read_text(encoding="utf-8")
    try:
        i = orig.index(MARKER)
        j = orig.index("        if not hfq_files:", i)
        TARGET.write_text(orig[:i] + OLD_LINE + orig[j:], encoding="utf-8")
        rc, msg = run_test()
        print(f"[证伪 退回全量 glob] exit={rc}  {msg}")
        assert rc != 0, "证伪失败：退回全量 glob 后测试仍通过 ⇒ 裁剪断言无效"
    finally:
        TARGET.write_text(orig, encoding="utf-8")
        print("[恢复] market.py 与原文一致 =", TARGET.read_text(encoding="utf-8") == orig)

    rc_ok, msg_ok = run_test()
    print(f"[恢复后复跑] exit={rc_ok}  {msg_ok}")
    return 0 if rc_ok == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
