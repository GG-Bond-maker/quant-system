"""一次性证伪脚本：证明新增的并行/预算测试**真的能抓到回退**。

做法：把 `market.py` 临时改回旧实现（串行 + 5.0s 预算）→ 跑测试 → 必须 FAIL → 恢复原文。
不改任何其它文件；无论成败都会恢复（finally）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TARGET = BACKEND_ROOT / "app" / "api" / "v1" / "market.py"
TEST = "tests/test_build_daily_parallel_and_budget.py"

PARALLEL_BLOCK = """    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="daily") as pool:
        f_heat = pool.submit(_heat_from_local)
        f_sectors = pool.submit(_sectors_from_local)
        f_recommend = pool.submit(_build_recommend, recommend_k)
        f_ai_stats = pool.submit(_build_ai_stats)
        # heat 是关键路径起点：sentiment 需要它，拿到后马上算，别等其它块。
        heat = f_heat.result()
        sentiment = _build_sentiment(heat)
        blocks = {
            "heat": heat,
            "sectors": f_sectors.result(),
            "recommend": f_recommend.result(),
            "ai_stats": f_ai_stats.result(),
            "sentiment": sentiment,
            "pred_dates": _pred_dates(),
        }"""

SERIAL_BLOCK = """    heat = _heat_from_local()
    blocks = {
        "heat": heat,
        "sectors": _sectors_from_local(),
        "recommend": _build_recommend(recommend_k),
        "ai_stats": _build_ai_stats(),
        "sentiment": _build_sentiment(heat),
        "pred_dates": _pred_dates(),
    }"""


def run_test() -> tuple[int, str]:
    p = subprocess.run(
        [str(BACKEND_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest",
         TEST, "-q", "--tb=line", "-p", "no:randomly"],
        cwd=str(BACKEND_ROOT), capture_output=True, text=True, timeout=300,
    )
    tail = [ln for ln in p.stdout.splitlines() if "passed" in ln or "failed" in ln]
    return p.returncode, " | ".join(tail[-2:])


def main() -> int:
    orig = TARGET.read_text(encoding="utf-8")
    try:
        # --- 证伪 A：改回串行 ---
        assert PARALLEL_BLOCK in orig, "未找到并行块（文件已变），证伪 A 无法执行"
        TARGET.write_text(orig.replace(PARALLEL_BLOCK, SERIAL_BLOCK), encoding="utf-8")
        rc_a, msg_a = run_test()
        print(f"[证伪 A 串行] exit={rc_a}  {msg_a}")
        assert rc_a != 0, "证伪 A 失败：改回串行后测试仍通过 ⇒ 并行性断言无效"

        # --- 证伪 B：预算改回 5.0s ---
        TARGET.write_text(orig.replace(
            "DAILY_BUILD_TIMEOUT_SECONDS = 20.0",
            "DAILY_BUILD_TIMEOUT_SECONDS = 5.0"), encoding="utf-8")
        rc_b, msg_b = run_test()
        print(f"[证伪 B 预算5s] exit={rc_b}  {msg_b}")
        assert rc_b != 0, "证伪 B 失败：预算 5.0s 后测试仍通过 ⇒ 预算不变量断言无效"
    finally:
        TARGET.write_text(orig, encoding="utf-8")
        restored = TARGET.read_text(encoding="utf-8") == orig
        print(f"[恢复] market.py 与原文一致 = {restored}")

    rc_ok, msg_ok = run_test()
    print(f"[恢复后复跑] exit={rc_ok}  {msg_ok}")
    return 0 if rc_ok == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
