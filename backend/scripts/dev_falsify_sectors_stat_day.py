"""一次性证伪：证明 `test_sectors_local_stat_day.py` 真的能抓到"退回 `df["date"].max()`"。

缺陷 D（2026-10-01）：`_sectors_from_local` 原用 `last_day = df["date"].max()` —— 与缺陷 A 同型。
把该处换回旧写法 → 跑测试 → 必须 FAIL → 无论成败都恢复原文。

⚠️ 锚点必须用**缺陷 D 的注释**（`_pick_stat_day(df)` 那一行在文件里出现两次，
   直接按行文本替换会误伤 `_heat_from_local`）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TARGET = BACKEND_ROOT / "app" / "api" / "v1" / "market.py"
TEST = "tests/test_sectors_local_stat_day.py"
PY = str(BACKEND_ROOT / ".venv" / "Scripts" / "python.exe")

# 缺陷 D 的注释锚点（唯一）
MARKER = "        # 缺陷 D（2026-10-01 修）："
# 旧写法：劫持统计日；其余三个变量补桩，保证 return 语句仍能通过语法检查
OLD_BLOCK = (
    "        last_day = df[\"date\"].max()\n"
    "        coverage, latest_day, skipped_days = 0, last_day, 0\n"
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
        j = orig.index("        last_day, coverage, latest_day, skipped_days = _pick_stat_day(df)", i)
        j = orig.index("\n", j) + 1
        TARGET.write_text(orig[:i] + OLD_BLOCK + orig[j:], encoding="utf-8")
        # 自检：确认替换只命中 `_sectors_from_local` 一处，`_heat_from_local` 未被动到
        patched = TARGET.read_text(encoding="utf-8")
        assert patched.count("_pick_stat_day(df)") == 1, "误伤：_pick_stat_day 调用点数量异常"
        rc, msg = run_test()
        print(f"[证伪 退回 df['date'].max()] exit={rc}  {msg}")
        assert rc != 0, "证伪失败：退回旧写法后测试仍通过 ⇒ 统计日断言无效"
    finally:
        TARGET.write_text(orig, encoding="utf-8")
        print("[恢复] market.py 与原文一致 =", TARGET.read_text(encoding="utf-8") == orig)

    rc_ok, msg_ok = run_test()
    print(f"[恢复后复跑] exit={rc_ok}  {msg_ok}")
    return 0 if rc_ok == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
