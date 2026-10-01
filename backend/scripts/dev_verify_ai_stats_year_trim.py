"""对照实验：证明"按预测日裁剪 hfq 分区"**不改变** `_build_ai_stats` 的任何输出。

做法（文件级 swap，避免模块重载歧义）：
  ① 用当前实现跑一次 `_build_ai_stats()` → 落 JSON；
  ② 临时把 market.py 的裁剪块换回**旧的全量 glob** → 再跑一次 → 落 JSON；
  ③ 逐字段比对（`rank_ic` / `top_k_precision` / `n_days` / `horizon` / ...）；
  ④ 无论成败都恢复原文。

若两次输出完全一致 ⇒ 裁剪是**纯优化**（省 ~32% 读取，不改语义）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TARGET = BACKEND_ROOT / "app" / "api" / "v1" / "market.py"
PY = str(BACKEND_ROOT / ".venv" / "Scripts" / "python.exe")

# 当前（裁剪）实现里的片段 —— 用于定位替换起点
TRIMMED_MARKER = "        # 按预测日跨度**裁剪** hfq 分区（2026-10-01）："
OLD_LINE = (
    '        hfq_files = sorted((s.DATA_ROOT / "daily_bar_hfq")'
    '.glob("symbol=*/year=*.snappy.parquet"))\n'
)

DUMP = r"""
import json, sys
sys.path.insert(0, r"%s")
from app.api.v1.market import _build_ai_stats
out = _build_ai_stats()
print("@@JSON@@" + json.dumps(out, ensure_ascii=False, default=str))
""" % str(BACKEND_ROOT)


def run_dump() -> dict:
    p = subprocess.run([PY, "-c", DUMP], cwd=str(BACKEND_ROOT),
                       capture_output=True, text=True, timeout=900)
    for ln in p.stdout.splitlines():
        if ln.startswith("@@JSON@@"):
            return json.loads(ln[len("@@JSON@@"):])
    raise RuntimeError(f"未拿到 JSON；stderr={p.stderr[-800:]}")


def strip_trim_block(src: str) -> str:
    """把裁剪块整体替换为旧的全量 glob 行。"""
    i = src.index(TRIMMED_MARKER)
    # 裁剪块以 "        if not hfq_files:" 结束（保留该守卫）
    j = src.index("        if not hfq_files:", i)
    return src[:i] + OLD_LINE + src[j:]


def main() -> int:
    orig = TARGET.read_text(encoding="utf-8")

    print("[1/3] 跑当前（裁剪）实现 ...")
    trimmed = run_dump()
    print("      ", json.dumps({k: trimmed.get(k) for k in
                               ("status", "rank_ic", "top_k_precision", "n_days",
                                "horizon", "top_k", "label_price_basis")},
                               ensure_ascii=False))

    try:
        print("[2/3] 临时改回全量 glob，再跑一次 ...")
        TARGET.write_text(strip_trim_block(orig), encoding="utf-8")
        full = run_dump()
        print("      ", json.dumps({k: full.get(k) for k in
                                   ("status", "rank_ic", "top_k_precision", "n_days",
                                    "horizon", "top_k", "label_price_basis")},
                                   ensure_ascii=False))
    finally:
        TARGET.write_text(orig, encoding="utf-8")
        print("[恢复] market.py 与原文一致 =", TARGET.read_text(encoding="utf-8") == orig)

    print("[3/3] 比对 ...")
    keys = sorted(set(trimmed) | set(full))
    diffs = [k for k in keys if trimmed.get(k) != full.get(k)]
    if diffs:
        print("❌ 输出不一致，差异字段：")
        for k in diffs:
            print(f"   {k}: 裁剪={trimmed.get(k)!r}  全量={full.get(k)!r}")
        return 1
    print(f"✅ 输出完全一致（{len(keys)} 个字段）：{keys}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
