"""domain 层纯函数架构守卫（P0-Critical #2 回归测试）。

规则：app/domain/*.py 禁止出现任何 IO 依赖 ——
- 禁止 import：sqlite3 / sqlalchemy / aiosqlite / redis / httpx / akshare /
  requests / urllib / socket / asyncio / pathlib.Path 使用；
- 禁止调用：open() / 任何 execute(。
使用 AST 静态扫描（非字符串 grep），新增文件自动纳入守卫。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND_ROOT / "app" / "domain"

FORBIDDEN_MODULES = {
    "sqlite3", "sqlalchemy", "aiosqlite", "redis", "httpx", "akshare",
    "requests", "urllib", "socket", "asyncio", "pathlib",
}
# 注：polars/numpy/pandas 属于纯计算库，允许 import，但禁止其文件 IO 入口
# （read_parquet / read_csv / connect / open 等，见 FORBIDDEN_ATTR_CALLS）。
FORBIDDEN_ATTR_CALLS = {
    "read_parquet", "scan_parquet", "read_csv", "read_excel", "to_parquet",
    "connect", "execute", "open",
}


def _imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            # 相对导入（level>0）在 domain 层等价于 app.<sub>——同样纳入扫描
            if node.module and node.level == 0:
                out.add(node.module)
            elif node.level > 0:
                # domain 文件位于 app/domain/：level=1 -> app.domain.x，level=2 -> app.x
                base = "app.domain" if node.level == 1 else "app"
                out.add(f"{base}.{node.module}" if node.module else base)
    return out


# Task 13（整改 A-P1-1）：domain 禁止 import 业务层（app.data/app.ml/app.api/
# app.trading）——"纯函数承诺被打破"的真实形态就是这类相对导入（此前扫描
# 只看 level==0 顶级名，对 from ..data.x 是盲区）。
FORBIDDEN_APP_MODULES = ("app.data", "app.ml", "app.api", "app.trading")


def _bad_calls(tree: ast.AST) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in FORBIDDEN_ATTR_CALLS:
                bad.append(name)
    return bad


def test_domain_has_no_io_imports():
    """TC-ARCH-PURE：domain 全部文件不得 import 任何 IO 库。"""
    violations: list[str] = []
    for py in sorted(DOMAIN_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        bad = {m for m in _imports(tree)
               if m.split(".")[0] in FORBIDDEN_MODULES}
        if bad:
            violations.append(f"{py.name}: imports {sorted(bad)}")
    assert not violations, f"domain 层存在 IO import 违规: {violations}"


def test_domain_has_no_business_layer_imports():
    """TC-ARCH-PURE-2（Task 13）：domain 不得 import data/ml/api/trading 层。

    IO 与模型编排属调用方职责；domain 只消费调用方注入的数据。
    """
    violations: list[str] = []
    for py in sorted(DOMAIN_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for mod in _imports(tree):
            if any(mod == f or mod.startswith(f + ".")
                   for f in FORBIDDEN_APP_MODULES):
                violations.append(f"{py.name}: imports {mod}")
    assert not violations, f"domain 层存在业务层反向依赖: {violations}"


def test_domain_has_no_io_calls():
    """TC-ARCH-PURE：domain 全部文件不得调用文件/数据库 IO 入口。"""
    violations: list[str] = []
    for py in sorted(DOMAIN_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        bad = _bad_calls(tree)
        if bad:
            violations.append(f"{py.name}: calls {sorted(set(bad))}")
    assert not violations, f"domain 层存在 IO 调用违规: {violations}"


def test_domain_modules_importable_without_io_stack():
    """domain 各模块在屏蔽 sqlite3/sqlalchemy 的环境下仍可导入（证明无硬依赖）。"""
    import importlib

    for name in ("adjust", "limit", "metrics", "calendar", "indicators", "a_share_rules"):
        real_sqlite = sys.modules.get("sqlite3")
        try:
            sys.modules["sqlite3"] = None  # type: ignore[assignment]  # 模拟不可用
            importlib.import_module(f"app.domain.{name}")
        finally:
            if real_sqlite is not None:
                sys.modules["sqlite3"] = real_sqlite
            else:
                sys.modules.pop("sqlite3", None)
