"""Task 15（整改计划 A-P1-6b）：data 层不得 import ml 层。

数据采集/存储与模型层曾互相依赖（data ⇄ ml 成环，靠函数内延迟 import
掩盖）。编排职责已上移 app/orchestrator.py；本守卫用 AST 扫描 app/data
全部 import（含懒加载），任何指向 app.ml 的引用即违规。
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_ROOT / "app" / "data"


def _resolve(node: ast.ImportFrom) -> str | None:
    mod = node.module or ""
    if node.level == 0:
        return mod if mod.startswith("app") else None
    if node.level == 1:
        return f"app.data.{mod}" if mod else "app.data"
    if node.level == 2:
        return f"app.{mod}" if mod else "app"
    return None


def test_data_has_no_ml_imports():
    violations: list[str] = []
    for py in sorted(DATA_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                r = _resolve(node)
                if r:
                    mods = [r]
            for m in mods:
                if m == "app.ml" or m.startswith("app.ml."):
                    violations.append(f"{py.relative_to(BACKEND_ROOT)}:{node.lineno}: {m}")
    assert not violations, f"data 层存在对 ml 层的依赖: {violations}"
