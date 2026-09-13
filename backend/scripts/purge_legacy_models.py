"""清理修复前的无效模型产物与注册表记录（第十二阶段：清理无效 candidate）。

背景：
    修复前 MODEL_ROOT 下堆积了 384+ 个实验目录（含 pytest/leakA/leakB/audit/pipe 后缀），
    model_registry 有 72 条记录，is_production 全为 0 —— 全都是无效 candidate，
    且旧 load_prod_model 会把"目录名排序最后一个"当成生产模型，风险极高。

策略（保守，可恢复）：
    - 旧模型目录 -> 移动到 data/models_legacy/（不删除）；
    - model_registry 记录 -> 删除（纯元数据，且全部 is_production=0 无效）；
    - 新目录结构 models/prod、models/exp 不受影响。

    python scripts/purge_legacy_models.py [--apply]
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="purge_legacy_models.py")
    ap.add_argument("--apply", action="store_true", help="实际执行（默认干跑）")
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    root = s.MODEL_ROOT
    legacy = PROJECT_ROOT / "data" / "models_legacy"

    # 顶层散落的旧目录（不属于 prod/ 或 exp/ 的一律视为 legacy）
    dirs = [d for d in root.iterdir()
            if d.is_dir() and d.name not in ("prod", "exp")]
    print(f"MODEL_ROOT = {root}")
    print(f"待归档的旧实验目录：{len(dirs)} 个")

    conn = sqlite3.connect(s.SQLITE_PATH)
    try:
        n_reg = conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        n_prod = conn.execute(
            "SELECT COUNT(*) FROM model_registry WHERE is_production=1").fetchone()[0]
    finally:
        conn.close()
    print(f"model_registry 记录：{n_reg} 条（其中 is_production=1 的 {n_prod} 条）")

    if not args.apply:
        print("\n（干跑模式，未修改任何数据。加 --apply 执行）")
        return

    moved = 0
    legacy.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for d in dirs:
        dst = legacy / f"{d.name}_{stamp}"
        shutil.move(str(d), str(dst))
        moved += 1
    print(f"已归档 {moved} 个目录 -> {legacy}")

    conn = sqlite3.connect(s.SQLITE_PATH)
    try:
        conn.execute("DELETE FROM model_registry")
        conn.commit()
        left = conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
    finally:
        conn.close()
    print(f"已清空 model_registry（剩余 {left} 条）")
    print("\n清理完成。下一步：重新 train -> promote。")


if __name__ == "__main__":
    main()
