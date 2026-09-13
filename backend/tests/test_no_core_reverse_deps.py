"""Task 14（整改计划 A-P1-6a）core 层反向依赖守卫。

core 层自声明为"最底层基础设施"（config/errors/logging/metrics/auth/events），
历史上却 import 过 api.v1.report / ml.monitor / data.realtime，靠调度/行情
两个文件把 core→api→core 的环焊死。本守卫用 AST 扫描 app/core 全部 import
（含函数内懒加载），任何指向 app.api / app.ml / app.data / app.jobs /
app.trading 的引用即违规——调度与行情已迁 jobs/ 与 data/，core 不再需要它们。
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = BACKEND_ROOT / "app" / "core"

FORBIDDEN_APP_MODULES = ("app.api", "app.ml", "app.data", "app.jobs", "app.trading")


def _resolve(node: ast.ImportFrom) -> str | None:
    """把相对导入解析为绝对模块名。core 文件位于 app/core/：
    level=1 -> app.core.<mod>；level=2 -> app.<mod>。"""
    mod = node.module or ""
    if node.level == 0:
        return mod if mod.startswith("app") else None
    if node.level == 1:
        return f"app.core.{mod}" if mod else "app.core"
    if node.level == 2:
        return f"app.{mod}" if mod else "app"
    return None


def _app_imports(tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names if a.name.startswith("app"))
        elif isinstance(node, ast.ImportFrom):
            r = _resolve(node)
            if r:
                out.append(r)
    return out


def test_core_has_no_reverse_deps():
    """core 层不得反向依赖 api/ml/data/jobs/trading（A-P1-6a）。"""
    violations: list[str] = []
    for py in sorted(CORE_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for mod in _app_imports(tree):
            if any(mod == f or mod.startswith(f + ".")
                   for f in FORBIDDEN_APP_MODULES):
                violations.append(f"{py.relative_to(BACKEND_ROOT)}:{mod}")
    assert not violations, f"core 层存在反向依赖: {violations}"
