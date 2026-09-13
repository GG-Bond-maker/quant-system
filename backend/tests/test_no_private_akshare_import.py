"""Task 16（整改计划 A-P1-7）：api 层不得私有导入 _ak / 自建重试包装。

背景：market.py 曾私有导入 akshare_adapter._ak 并自建 _ak_call 重试
包装——绕过 adapter 的全局限速（_throttle）与统一重试策略，东财夜间
限流时多个数据块并发打满源站。外呼必须经 _safe_call（限速+重试）。

守卫口径（计划原文：api/ 无 `_ak` 直接导入）：
- 禁止 from akshare_adapter import _ak（私有名外泄）；
- 禁止 api 层再定义 _ak 前缀的本地包装函数。
已知遗留（计划范围外，不红灯）：datacenter._fetch_etf_bar 与
portfolio._search_assets 仍直接 import akshare——属后续计划收口项。
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
API_DIR = BACKEND_ROOT / "app" / "api"


def _resolve(node: ast.ImportFrom) -> str | None:
    mod = node.module or ""
    if node.level == 0:
        return mod if mod.startswith("app") else None
    # api/v1/*.py 位于 app/api/v1/：level=1 -> app.api.v1.<mod>，
    # level=2 -> app.api.<mod>，level=3 -> app.<mod>
    bases = {1: "app.api.v1", 2: "app.api", 3: "app"}
    base = bases.get(node.level)
    if base is None:
        return None
    return f"{base}.{mod}" if mod else base


def test_api_has_no_private_akshare_import():
    """api 层不得导入 adapter 私有名 _ak（外呼一律经 _safe_call）。"""
    violations: list[str] = []
    for py in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            r = _resolve(node)
            if not r or not r.endswith("akshare_adapter"):
                continue
            for a in node.names:
                if a.name == "_ak" or a.name.startswith("_ak"):
                    violations.append(
                        f"{py.relative_to(BACKEND_ROOT)}:{node.lineno}: "
                        f"import {a.name}")
    assert not violations, f"api 层存在 akshare_adapter 私有名导入: {violations}"


def test_api_has_no_local_akshare_retry_wrapper():
    """api 层不得再定义 _ak_call 一类私有重试包装（统一走 _safe_call）。"""
    violations: list[str] = []
    for py in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("_ak"):
                violations.append(
                    f"{py.relative_to(BACKEND_ROOT)}:{node.lineno}: {node.name}()")
    assert not violations, f"api 层存在私有 akshare 包装函数: {violations}"
