"""一次性证伪：证明 `test_ai_stats_independent_cache.py` 真的能抓到"缓存被禁用"。

把 `_build_ai_stats` 的独立缓存查表整段去掉（`sig = None` ⇒ 既不查也不写）
→ 跑测试 → 必须 FAIL → 无论成败都恢复原文。

⚠️ 锚点必须用**独立缓存那段专属注释**（`_build_ai_stats` 里还有多处
   `_ai_stats_*` 标识符，按通用文本替换容易误伤）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TARGET = BACKEND_ROOT / "app" / "api" / "v1" / "market.py"
TEST = "tests/test_ai_stats_independent_cache.py"
PY = str(BACKEND_ROOT / ".venv" / "Scripts" / "python.exe")

MARKER = "        # 独立缓存查表（见 _AI_STATS_TTL_SECONDS 的说明）。"
ANCHOR_END = '        pred_files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))\n'
# 旧行为：无缓存 —— 每次调用都重算
OLD_BLOCK = "        s = get_settings()\n        sig = None\n"


def run_test() -> tuple[int, str]:
    p = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--tb=line", "-p", "no:randomly"],
                       cwd=str(BACKEND_ROOT), capture_output=True, text=True, timeout=300)
    tail = [ln for ln in p.stdout.splitlines() if "passed" in ln or "failed" in ln]
    return p.returncode, " | ".join(tail[-2:])


def main() -> int:
    orig = TARGET.read_text(encoding="utf-8")
    try:
        i = orig.index(MARKER)
        # 回退到 `s = get_settings()` 那一行的开头，保证 OLD_BLOCK 能接上
        i = orig.rindex("        s = get_settings()\n", 0, i)
        j = orig.index(ANCHOR_END, i)
        TARGET.write_text(orig[:i] + OLD_BLOCK + orig[j:], encoding="utf-8")
        # 自检：查表调用点应已消失，且文件里只剩函数定义那一处
        # ⚠️ 必须匹配**带赋值的调用**：`count("_ai_stats_signature(s)")` 会同时命中
        #    定义行 `def _ai_stats_signature(s)`，恒为 1，起不到自检作用。
        patched = TARGET.read_text(encoding="utf-8")
        assert patched.count("sig = _ai_stats_signature(s)") == 0, "误伤：查表调用点未被移除"
        assert patched.count("def _ai_stats_signature(") == 1, "误伤：签名函数定义被破坏"
        rc, msg = run_test()
        print(f"[证伪 禁用独立缓存] exit={rc}  {msg}")
        assert rc != 0, "证伪失败：禁用缓存后测试仍通过 ⇒ 缓存断言无效"
    finally:
        TARGET.write_text(orig, encoding="utf-8")
        print("[恢复] market.py 与原文一致 =", TARGET.read_text(encoding="utf-8") == orig)

    rc_ok, msg_ok = run_test()
    print(f"[恢复后复跑] exit={rc_ok}  {msg_ok}")
    return 0 if rc_ok == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
