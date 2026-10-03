"""一次性证伪（用完即删）：backfill_incremental.build_plan 的断言是否有效。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SRC = BACKEND / "scripts" / "backfill_incremental.py"
PYTEST = [str(BACKEND / ".venv" / "Scripts" / "python.exe"), "-m", "pytest",
          "tests/test_backfill_incremental.py", "-q", "--tb=no", "-p", "no:randomly"]


def run() -> int:
    p = subprocess.run(PYTEST, cwd=BACKEND, capture_output=True, text=True)
    tail = (p.stdout or "").strip().splitlines()
    print("   ->", tail[-1] if tail else "(no output)")
    return p.returncode


def check(name: str, old: str, new: str) -> None:
    orig = SRC.read_text(encoding="utf-8")
    assert orig.count(old) == 1, f"{name}: 锚点命中 {orig.count(old)} 次"
    SRC.write_text(orig.replace(old, new), encoding="utf-8")
    try:
        rc = run()
        print(f"[证伪] {name}: "
              f"{'FAIL(符合预期，断言有效)' if rc != 0 else 'PASS(!!! 断言无效)'}")
        results.append(rc != 0)
    finally:
        SRC.write_text(orig, encoding="utf-8")
        assert SRC.read_text(encoding="utf-8") == orig


results: list[bool] = []

# ① 起点只看 raw（丢掉「取两侧较早者」这条核心判据）
check(
    "① 起点误用 raw 单侧",
    "        ref = min(d for d in (last, last_hfq) if d is not None)\n",
    "        ref = last if last is not None else last_hfq\n",
)

# ② 删掉区间倒置保护
check(
    "② 缺区间倒置保护",
    "        if start > target:\n",
    "        if False:\n",
)

# ③ 删掉 hfq 缺失统计
check(
    "③ 缺 hfq_missing 统计",
    "        if last_hfq is None:\n            hfq_missing.append(sym)\n",
    "",
)

# ④ 把「两侧都最新才跳过」放宽成「raw 最新就跳过」
check(
    "④ 跳过判据放宽为 raw 单侧",
    "        if (last is not None and last >= target\n"
    "                and last_hfq is not None and last_hfq >= target):\n",
    "        if last is not None and last >= target:\n",
)

print()
print("基线测试应已还原为 PASS：", run() == 0)
print(f"证伪结论：{sum(results)}/{len(results)} 条注入缺陷被测试捕获")
sys.exit(0 if all(results) else 1)
