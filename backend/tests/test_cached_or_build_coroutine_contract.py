"""静态契约：``cache.swr.cached_or_build`` 的 ``build`` 必须是**可等待的协程函数**。

## 为什么需要这条常驻检查

2026-09-28 的 P0：``/api/v1/screener/stats/series`` 与 ``/api/v1/etf/overview/series``
把**同步** ``def _build()`` 传给了 ``cached_or_build``，而后者内部是 ``await build()``
⇒ ``TypeError: object dict can't be used in 'await' expression``
⇒ 真机 HTTP 200 但业务 ``code=50000``（100% 必崩，缓存永远填不上）。

该缺陷逃过了当时 1832 个用例（那时 RBAC 用例只断言 ``code not in (40100, 40300)``）。
行为侧已由 ``test_read_endpoints_rbac.py::test_kpi_series_endpoint_actually_runs``
（严格断言 ``code == 0``）守住；本文件再从**静态**一侧堵住同类缺陷：
以后新增端点即便忘了登记、忘了写行为断言，只要把同步函数传给 ``cached_or_build``，
这里立刻变红 —— 不必等到真机出现 ``code=50000``。

## 判据（AST，不是正则）

遍历 ``app/**/*.py`` 每个 ``cached_or_build(...)`` 调用点，解析**需要可等待的实参**
（第 2 位置参数 / ``build=`` / ``background_build=``），把表达式归类：

* ``ASYNC``       —— 解析到 ``async def`` ⇒ 通过；
* ``NONE``        —— 字面量 ``None``（``background_build`` 缺省）⇒ 通过；
* ``PASSTHROUGH`` —— 指向被调函数的**形参** ⇒ 回溯该函数在 ``app/`` 内的全部调用点，
                     逐个解析实参（支持多级透传，深度上限 ``_MAX_DEPTH``）；
                     只要有一处不是 ASYNC/NONE 就失败；
* ``SYNC``        —— 解析到 ``def``（非 async）⇒ **失败**（即本次 P0 的形态）；
* ``UNKNOWN``     —— 动态取函数（``getattr`` / ``functools.partial`` / lambda / 任意表达式）
                     ⇒ **失败（保守判定）**。确需例外时登记进 ``_REVIEWED_UNKNOWN``
                     并写明理由 —— 不允许静默跳过（否则就是"永远绿"的假检查）。

## 能力边界（它**测不出来**什么）

1. ``getattr(mod, "build_x")``、``functools.partial(fn, ...)``、装饰器把协程包成同步
   （或反之）—— 一律判 ``UNKNOWN`` 并失败，需要人工复核后登记白名单；
2. 运行时才决定的分支（``build = f if flag else g``）—— 判 ``UNKNOWN`` 并失败；
3. ``build`` 内部的逻辑错误（例如它自己 ``await`` 了一个同步函数、或返回非 dict）
   —— 本文件只保证"传进去的是 ``async def``"；这类问题由
   :func:`test_await_target_is_async_local_def`（泛化扫描）与行为用例分担；
4. 通过字符串/反射间接调用 ``cached_or_build`` 的场景 —— 扫不到，天然不覆盖。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

APP_ROOT = BACKEND_ROOT / "app"

TARGET = "cached_or_build"
# `cached_or_build` 内部对这两个实参都会 `await`（见 app/cache/swr.py）
AWAITED_PARAMS = ("build", "background_build")
_MAX_DEPTH = 3
# 静态可发现调用点的下限。用途只有一个：**防止扫描器整体失效**（例如 APP_ROOT 写错、
# 源码改成别的写法而本文件还"全绿"）。它不是"冻结调用点数量"，故留足余量 —— 
# 现状 11 处，取 8。真正防退化的是 test_call_sites_are_discovered 里
# 「每个调用点都必须解析出 build 实参」那条断言。
_MIN_CALL_SITES = 8

# 经人工复核、确实无法静态判定的表达式 ⇒ 允许放行（正常应保持为空）。
# 键 = "<相对路径>:<行号>:<参数名>"，值 = 复核结论与理由。
_REVIEWED_UNKNOWN: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# AST 工具
# --------------------------------------------------------------------------- #
def _iter_py(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _is_target_call(node: ast.AST) -> bool:
    """``cached_or_build(...)`` 或 ``swr.cached_or_build(...)``。"""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id == TARGET
    return isinstance(fn, ast.Attribute) and fn.attr == TARGET


def _defs_in(node: ast.AST) -> dict[str, ast.AST]:
    """``node`` 子树内出现的全部函数定义（含自身）。"""
    out: dict[str, ast.AST] = {}
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(n.name, n)
    return out


def _module_defs(tree: ast.Module) -> dict[str, ast.AST]:
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _param_names(fn: ast.AST) -> tuple[list[str], list[str]]:
    args = fn.args  # type: ignore[attr-defined]
    return ([p.arg for p in (*args.posonlyargs, *args.args)],
            [p.arg for p in args.kwonlyargs])


def _owned_calls(fn: ast.AST) -> list[ast.Call]:
    """``fn`` 自身作用域内的 ``Call``（不下潜进嵌套 ``def``/``lambda``）。"""
    out: list[ast.Call] = []

    def rec(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                out.append(child)
            rec(child)

    rec(fn)
    return out


def _iter_functions(tree: ast.Module, mdefs: dict[str, ast.AST]):
    """产出 ``(fn, scope_defs)``；``scope_defs`` = 模块级 ∪ 全部祖先函数的定义。"""
    def rec(node: ast.AST, inherited: dict[str, ast.AST]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = {**inherited, **_defs_in(child)}
                yield child, inner
                yield from rec(child, inner)
            else:
                yield from rec(child, inherited)

    yield from rec(tree, dict(mdefs))


def _expr_for(call: ast.Call, pname: str) -> ast.expr | None:
    """调用点里绑定到待检查形参的实参；未提供返回 None（走默认值）。"""
    if pname == "build" and len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == pname:
            return kw.value
    return None


def _arg_for(fn: ast.AST, call: ast.Call, name: str) -> ast.expr | None:
    """调用点里绑定到被调函数形参 ``name`` 的实参；未提供返回 None。"""
    pos, _kwonly = _param_names(fn)
    if name in pos:
        idx = pos.index(name)
        return call.args[idx] if idx < len(call.args) else None
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


# --------------------------------------------------------------------------- #
# 扫描与判定
# --------------------------------------------------------------------------- #
class Site:
    """一个 ``cached_or_build`` 调用点上的一个待检查实参。

    ``owner_name`` = 该调用点所在的（被调）函数名 —— 透传回溯时要靠它找到调用方。
    """

    __slots__ = ("rel", "line", "arg", "expr", "defs", "params",
                 "owner_name", "verdict", "detail")

    def __init__(self, rel: str, line: int, arg: str, expr, defs, params,
                 owner_name: str) -> None:
        self.rel, self.line, self.arg = rel, line, arg
        self.expr, self.defs, self.params = expr, defs, params
        self.owner_name = owner_name
        self.verdict, self.detail = "UNKNOWN", "未判定"

    def key(self) -> str:
        return f"{self.rel}:{self.line}:{self.arg}"

    def __str__(self) -> str:
        return f"{self.rel}:{self.line} [{self.arg}] {self.verdict} — {self.detail}"


def _classify(expr: ast.expr | None, defs: dict[str, ast.AST],
              params: frozenset[str]) -> tuple[str, str]:
    """单层判定 ⇒ ``(verdict, detail)``；``PASSTHROUGH`` 由调用方继续回溯。"""
    if expr is None:
        return "NONE", "未提供（走默认值 None）"
    if isinstance(expr, ast.Constant) and expr.value is None:
        return "NONE", "字面量 None"
    if isinstance(expr, ast.Name):
        if expr.id in defs:
            d = defs[expr.id]
            if isinstance(d, ast.AsyncFunctionDef):
                return "ASYNC", f"async def {expr.id} @line {d.lineno}"
            return "SYNC", f"def {expr.id} @line {d.lineno}（**非 async**）"
        if expr.id in params:
            return "PASSTHROUGH", expr.id
        return "UNKNOWN", f"未解析的名字 {expr.id!r}"
    return "UNKNOWN", f"动态/表达式，无法静态判定：{ast.unparse(expr)[:80]}"


def _scan() -> tuple[list[Site], dict, dict, int]:
    sites: list[Site] = []
    # 被调函数名 -> [(call, 所在模块, 调用方作用域 defs, 调用方形参, 调用方函数名)]
    call_index: dict[str, list[tuple[ast.Call, str, dict, frozenset, str]]] = {}
    def_index: dict[str, list[tuple[str, ast.AST]]] = {}
    total = 0

    for path in _iter_py(APP_ROOT):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn, scope in _iter_functions(tree, _module_defs(tree)):
            pos, kwonly = _param_names(fn)
            params = frozenset(pos) | frozenset(kwonly)
            def_index.setdefault(fn.name, []).append((rel, fn))
            for node in _owned_calls(fn):
                if _is_target_call(node):
                    total += 1
                    for pname in AWAITED_PARAMS:
                        sites.append(Site(rel, node.lineno, pname,
                                          _expr_for(node, pname), scope, params,
                                          fn.name))
                callee_name = node.func.id if isinstance(node.func, ast.Name) else None
                if callee_name:
                    call_index.setdefault(callee_name, []).append(
                        (node, rel, scope, params, fn.name))
    return sites, call_index, def_index, total


def _resolve(expr, defs, params, owner_name: str, owner_rel: str,
             call_index, def_index, depth: int = 0) -> tuple[str, str]:
    """把待检查实参判定为 ASYNC / NONE / SYNC / UNKNOWN，必要时回溯透传。"""
    verdict, detail = _classify(expr, defs, params)
    if verdict != "PASSTHROUGH" or depth >= _MAX_DEPTH:
        return verdict, detail

    # detail 是「被透传的形参名」；调用方函数 = owner_name（位于 owner_rel）
    param = detail
    owners = [x for x in def_index.get(owner_name, []) if x[0] == owner_rel]
    if len(owners) != 1:
        return "UNKNOWN", (f"站点位于 {owner_rel}:{owner_name}，但该函数在 app/ 内有 "
                           f"{len(owners)} 处定义，无法唯一定位其调用点")
    owner_fn = owners[0][1]
    ctxs = [c for c in call_index.get(owner_name, []) if c[1] == owner_rel]
    if not ctxs:
        return "UNKNOWN", (f"透传形参 {param}，但找不到 {owner_name}({owner_rel}) "
                           f"的调用点，无法判定")

    worst, notes = "ASYNC", []
    for call, rel, cscope, cparams, cowner in ctxs:
        sub_expr = _arg_for(owner_fn, call, param)
        sv, _sd = _resolve(sub_expr, cscope, cparams, cowner, rel,
                           call_index, def_index, depth + 1)
        notes.append(f"{sv}@{rel}:{call.lineno}")
        if sv not in ("ASYNC", "NONE") and worst == "ASYNC":
            worst = sv
    return worst, (f"透传形参 {param} ⇒ {owner_name}({owner_rel}) 的 {len(ctxs)} 个调用点："
                   f"{', '.join(notes)}")


def _scan_and_resolve() -> tuple[list[Site], int]:
    sites, call_index, def_index, total = _scan()
    for s in sites:
        s.verdict, s.detail = _resolve(s.expr, s.defs, s.params, s.owner_name, s.rel,
                                       call_index, def_index)
    return sites, total


_SITES, _TOTAL_CALLS = _scan_and_resolve()


def _inventory() -> str:
    return "\n".join(f"  - {s}" for s in sorted(_SITES, key=lambda x: (x.rel, x.line, x.arg)))


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def test_call_sites_are_discovered() -> None:
    """防止检查本身空转：必须扫到足够多调用点，且每处都能取出 ``build`` 实参。"""
    assert _TOTAL_CALLS >= _MIN_CALL_SITES, (
        f"只发现 {_TOTAL_CALLS} 个 cached_or_build 调用点（下限 {_MIN_CALL_SITES}）。"
        f"若源码确实变少了请同步调整下限；若不该变少，说明本检查已失效。\n"
        f"清单：\n{_inventory()}")

    build_sites = [s for s in _SITES if s.arg == "build"]
    assert len(build_sites) == _TOTAL_CALLS, (
        f"有 {_TOTAL_CALLS - len(build_sites)} 个调用点解析不出 build 实参"
        f"（形状变化 ⇒ 检查会静默失效）\n清单：\n{_inventory()}")


def test_cached_or_build_build_arg_is_coroutine() -> None:
    """核心断言：每个 ``cached_or_build`` 的 build/background_build 都是协程（或 None）。

    同步 ``def _build()`` 传进来 ⇒ ``await build()`` 抛
    ``TypeError: object dict can't be used in 'await' expression``
    ⇒ 真机 HTTP 200 + 业务 ``code=50000``。本用例就是拦这个。
    """
    bad = [s for s in _SITES
           if s.verdict in ("SYNC", "UNKNOWN") and s.key() not in _REVIEWED_UNKNOWN]
    assert not bad, (
        "以下 cached_or_build 调用点传入了**不可等待**（或无法静态判定）的 build 实参。\n"
        "cached_or_build 内部是 `await build()`，传同步函数会 100% 抛 TypeError "
        "→ 真机 code=50000。请把对应 `def _build()` 改成 `async def _build()`"
        "（内部阻塞操作放 asyncio.to_thread）。\n"
        "若确为动态取值、经人工复核无误，请登记 _REVIEWED_UNKNOWN 并写明理由。\n"
        + "\n".join(f"  - {s}" for s in bad))


def test_await_target_is_async_local_def() -> None:
    """泛化扫描：``await <同一作用域内的同步 def>()`` 也是同一种 TypeError。"""
    bad: list[str] = []
    scanned_files = 0
    awaited_name_calls = 0
    for path in _iter_py(APP_ROOT):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scanned_files += 1
        for fn, scope in _iter_functions(tree, _module_defs(tree)):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Await):
                    continue
                call = node.value
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                    continue
                awaited_name_calls += 1
                name = call.func.id
                d = scope.get(name)
                if d is not None and not isinstance(d, ast.AsyncFunctionDef):
                    bad.append(f"{rel}:{call.lineno} await {name}() → def @line {d.lineno}")

    # 防"空转"：必须真的扫过足够多的文件与 await 调用，否则本用例会永远绿。
    assert scanned_files >= 20, f"只扫到 {scanned_files} 个 app/ 源文件，扫描器可能已失效"
    assert awaited_name_calls >= 5, (
        f"只发现 {awaited_name_calls} 处 `await <名字>()`，扫描器可能已失效")

    assert not bad, ("以下位置 `await` 了一个**同步 def**（同样的 TypeError 形态）：\n"
                     + "\n".join(f"  - {x}" for x in bad))
