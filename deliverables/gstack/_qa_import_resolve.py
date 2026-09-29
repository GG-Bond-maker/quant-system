"""QA 静态检查 2：跨模块 import 符号可解析性 + 统一响应包装合规性（只读）。

1) 扫描 backend/app 下所有 .py 的相对/绝对 from-import，
   解析目标模块文件，验证被 import 的符号确实在目标模块中定义（或是子模块/包）。
2) 检查所有 API handler 的最终 return 是否走 ok()/fail()/Response 统一包装。
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "backend", "app")


def mod_file(rel_parts: list) -> str | None:
    """把 app 相对模块路径映射到文件（包→__init__.py）。"""
    base = os.path.join(APP, *rel_parts)
    for cand in (base + ".py", os.path.join(base, "__init__.py")):
        if os.path.isfile(cand):
            return cand
    return None


def resolve(module_parts: list, level: int, pkg: list) -> list | None:
    """解析相对导入的目标模块路径（相对 app 包）。

    pkg = 当前文件所属**包**的路径（相对 app），如 ['api','v1']。
    level=1 → 当前包本体；level=2 → 上一层包；以此类推。
    """
    if level == 0:
        # 绝对导入：只处理 app.* 开头的
        if module_parts and module_parts[0] == "app":
            return module_parts[1:]
        return None
    anchor = pkg[: max(0, len(pkg) - (level - 1))]
    if not module_parts:
        return anchor
    return anchor + list(module_parts)


def top_names(path: str) -> set:
    """目标模块顶层定义的名字 + 其 re-export（from x import y）。"""
    try:
        src = open(path, encoding="utf-8").read()
        mod = ast.parse(src)
    except Exception:
        return set()
    names = set()
    for node in mod.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
    return names


def main():
    files = []
    for dirpath, dirnames, filenames in os.walk(APP):
        dirnames[:] = [d for d in dirnames if d not in
                       ("__pycache__", ".tmp_testrun", "reports")]
        for f in filenames:
            if f.endswith(".py"):
                files.append(os.path.join(dirpath, f))

    missing = []
    checked = 0
    for path in files:
        rel = os.path.relpath(path, APP)
        cur_parts = rel[:-3].split(os.sep)
        is_pkg = bool(cur_parts) and cur_parts[-1] == "__init__"
        if is_pkg:
            cur_parts = cur_parts[:-1]
        # 当前文件所属的包（模块文件的包 = 去掉自身文件名；包的包 = 自身）
        pkg = cur_parts if is_pkg else cur_parts[:-1]
        try:
            mod = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in ast.walk(mod):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0 and not (node.module or "").startswith("app"):
                continue
            target = resolve((node.module or "").split(".") if node.module else [],
                             node.level, pkg)
            if target is None:
                continue
            tf = mod_file(target)
            if tf is None:
                # 可能是第三方/不存在模块
                if node.level > 0:
                    missing.append((os.path.relpath(path, ROOT), node.lineno,
                                    "." * node.level + (node.module or ""),
                                    [a.name for a in node.names], "目标模块文件不存在"))
                continue
            names = top_names(tf)
            submods = set()
            tdir = os.path.join(APP, *target)
            if os.path.isdir(tdir):
                submods = {f[:-3] for f in os.listdir(tdir) if f.endswith(".py")}
                submods |= {d for d in os.listdir(tdir)
                            if os.path.isdir(os.path.join(tdir, d))}
            for a in node.names:
                if a.name == "*":
                    continue
                checked += 1
                if a.name not in names and a.name not in submods:
                    missing.append((os.path.relpath(path, ROOT), node.lineno,
                                    "." * node.level + (node.module or ""),
                                    [a.name], "符号未定义"))

    print(f"已校验 from-import 符号数: {checked}")
    print(f"无法解析: {len(missing)}")
    for m in missing:
        print(f"   {m[0]}:{m[1]}  from {m[2]} import {', '.join(m[3])}  <- {m[4]}")

    # ---- 统一响应包装 ----
    API = os.path.join(APP, "api", "v1")
    WRAP = {"ok", "fail"}
    RESP = {"JSONResponse", "StreamingResponse", "Response", "FileResponse",
            "HTMLResponse", "RedirectResponse", "ORJSONResponse"}
    bad = []
    total = 0
    for fn in sorted(os.listdir(API)):
        if not fn.endswith(".py") or fn in ("__init__.py", "router.py"):
            continue
        src = open(os.path.join(API, fn), encoding="utf-8").read()
        mod = ast.parse(src)
        for n in ast.walk(mod):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_route = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                           and d.func.attr in {"get", "post", "put", "patch", "delete",
                                               "api_route"}
                           for d in n.decorator_list)
            if not is_route:
                continue
            total += 1
            finals = []
            for st in ast.walk(n):
                if isinstance(st, ast.Return) and st.value is not None:
                    v = st.value
                    while isinstance(v, ast.Await):
                        v = v.value
                    nm = None
                    if isinstance(v, ast.Call):
                        nm = getattr(v.func, "id", None) or getattr(v.func, "attr", None)
                    finals.append(nm)
            if not finals:
                continue  # 无 return（异常路径/流式）
            unwrapped = sorted({x for x in finals
                                if x is not None and x not in WRAP and x not in RESP})
            # 允许：handler 内部嵌套函数里的 return 会混入，故仅在「全部 return 都未包装」时报警
            normalized = {x for x in finals if x is not None}
            if unwrapped and normalized and len(unwrapped) == len(normalized):
                bad.append((fn, n.lineno, n.name, unwrapped))

    print(f"\nAPI handler 总数: {total}")
    print(f"疑似未走 ok()/fail() 统一包装的 handler: {len(bad)}")
    for b in bad:
        print(f"   {b[0]}:{b[1]} {b[2]} -> {b[3]}")


if __name__ == "__main__":
    sys.exit(main())
