"""QA 静态路由扫描器（只读，不修改任何生产代码）。

用途：从 backend/app/api/v1/*.py 提取全部路由，检测
  1) 占位/未实现 handler（pass / return {} / raise NotImplementedError / TODO）
  2) 重复路由（同 method+path 注册两次）
  3) 路由冲突（/x/{p} 与 /x/literal 的顺序遮蔽）
  4) Depends(...) 指向的名字是否在本模块可解析
输出 JSON 到 stdout 同目录的 route_scan.json
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_DIR = os.path.join(ROOT, "backend", "app", "api", "v1")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "route_scan.json")

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def literal_str(node):
    """尽力把 AST 节点还原成字符串常量。"""
    try:
        v = ast.literal_eval(node)
        if isinstance(v, (str, bytes)):
            return v.decode() if isinstance(v, bytes) else v
    except Exception:
        pass
    return None


def router_prefix(mod: ast.Module) -> str:
    """找模块级 router = APIRouter(prefix=...) 的 prefix。"""
    prefix = ""
    for node in mod.body:
        if isinstance(node, ast.Assign):
            val = node.value
            if isinstance(val, ast.Call):
                fname = ""
                f = val.func
                if isinstance(f, ast.Attribute):
                    fname = f.attr
                elif isinstance(f, ast.Name):
                    fname = f.id
                if fname == "APIRouter":
                    for kw in val.keywords:
                        if kw.arg == "prefix":
                            s = literal_str(kw.value)
                            if s:
                                prefix = s
    return prefix


def module_level_names(mod: ast.Module) -> set:
    """模块顶层可解析的名字（def/class/import/assign）。"""
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
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
    return names


def strip_docstring(body):
    b = list(body)
    if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
            and isinstance(b[0].value.value, str):
        b = b[1:]
    return b


def classify(fn: ast.AST, src_lines: list) -> tuple:
    """返回 (verdict, reason)。verdict ∈ STUB / THIN / TODO / OK"""
    body = strip_docstring(fn.body)
    n = len(body)

    # NotImplementedError
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise):
            exc = node.exc
            nm = None
            if isinstance(exc, ast.Call):
                nm = getattr(exc.func, "id", None) or getattr(exc.func, "attr", None)
            elif isinstance(exc, ast.Name):
                nm = exc.id
            if nm == "NotImplementedError":
                return ("STUB", "raise NotImplementedError")

    if n == 0:
        return ("STUB", "空函数体（仅 docstring）")
    if n == 1:
        st = body[0]
        if isinstance(st, ast.Pass):
            return ("STUB", "pass")
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) \
                and st.value.value is Ellipsis:
            return ("STUB", "... (Ellipsis)")
        if isinstance(st, ast.Return):
            v = st.value
            if v is None:
                return ("STUB", "return None")
            try:
                lit = ast.literal_eval(v)
            except Exception:
                lit = None
            if lit is not None and lit in ({}, [], "", 0, None):
                return ("STUB", f"常量空返回 {lit!r}")
            if isinstance(v, (ast.Dict, ast.List)) and len(getattr(v, "keys", []) or getattr(v, "elts", [])) == 0:
                return ("STUB", "空容器返回")
            return ("THIN", "单行 return（非字面量，需人工确认）")

    seg = "\n".join(src_lines[fn.lineno - 1: fn.end_lineno])
    if re.search(r"TODO|FIXME|XXX|占位|待实现|未实现|暂未", seg):
        return ("TODO", "含 TODO/FIXME/占位标记")

    if n <= 2:
        return ("THIN", f"仅 {n} 条语句（需人工确认）")
    return ("OK", "")


def depends_targets(fn: ast.AST) -> list:
    """收集默认参数里的 Depends(...) 目标名。"""
    out = []
    args = fn.args
    defaults = list(args.defaults)
    allargs = args.posonlyargs + args.args + args.kwonlyargs
    kwdefs = {k.arg: v for k, v in zip(args.kwonlyargs, args.kw_defaults) if v is not None}
    for a, d in zip(allargs[len(allargs) - len(defaults):], defaults):
        out.extend(_walk_depends(d))
    for d in kwdefs.values():
        out.extend(_walk_depends(d))
    return out


def _walk_depends(node):
    res = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fname = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if fname in ("Depends", "Security"):
                if n.args:
                    a = n.args[0]
                    if isinstance(a, ast.Name):
                        res.append(a.id)
                    elif isinstance(a, ast.Attribute):
                        res.append(a.attr)
                    elif isinstance(a, ast.Call):
                        f = a.func
                        res.append(getattr(f, "id", None) or getattr(f, "attr", "?"))
    return res


def main():
    results = []
    mod_prefix = {}
    for fname in sorted(os.listdir(API_DIR)):
        if not fname.endswith(".py") or fname in ("__init__.py", "router.py"):
            continue
        path = os.path.join(API_DIR, fname)
        src = open(path, encoding="utf-8").read()
        lines = src.splitlines()
        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            results.append({"file": fname, "error": f"SyntaxError {e}"})
            continue
        prefix = router_prefix(mod)
        mod_prefix[fname] = prefix
        names = module_level_names(mod)
        for node in ast.walk(mod):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                f = dec.func
                if not isinstance(f, ast.Attribute):
                    continue
                if f.attr not in HTTP_METHODS and f.attr != "api_route":
                    continue
                method = f.attr.upper()
                if f.attr == "api_route":
                    methods = []
                    for a in dec.args:
                        s = literal_str(a)
                        if s:
                            methods = [s.upper()]
                    for kw in dec.keywords:
                        if kw.arg == "methods":
                            try:
                                methods = [m.upper() for m in ast.literal_eval(kw.value)]
                            except Exception:
                                pass
                    if not methods:
                        methods = ["GET"]
                else:
                    methods = [method]
                rpath = None
                if dec.args:
                    rpath = literal_str(dec.args[0])
                if rpath is None:
                    for kw in dec.keywords:
                        if kw.arg == "path":
                            rpath = literal_str(kw.value)
                if rpath is None:
                    rpath = "/"
                verdict, reason = classify(node, lines)
                deps = depends_targets(node)
                missing = [d for d in deps if d not in names]
                calls = set()
                for n in ast.walk(node):
                    if isinstance(n, ast.Call):
                        f = n.func
                        if isinstance(f, ast.Name):
                            calls.add(f.id)
                        elif isinstance(f, ast.Attribute):
                            calls.add(f.attr)
                # 去掉 FastAPI 包装/装饰器噪声，只看是否真的调用了业务函数
                biz_calls = sorted(c for c in calls if c not in
                                   {"ok", "fail", "Depends", "Query", "Body", "Path",
                                    "Field", "dict", "list", "len", "str", "int", "float",
                                    "bool", "set", "getattr", "isinstance", "Query",
                                    "jsonable_encoder", "HTTPException", "logger"})
                for m in methods:
                    results.append({
                        "file": fname,
                        "lineno": node.lineno,
                        "end_lineno": node.end_lineno,
                        "func": node.name,
                        "method": m,
                        "raw_path": rpath,
                        "prefix": prefix,
                        "verdict": verdict,
                        "reason": reason,
                        "stmts": max(0, len(strip_docstring(node.body))),
                        "loc": node.end_lineno - node.lineno + 1,
                        "deps": deps,
                        "missing_deps": missing,
                        "biz_calls": biz_calls,
                        "no_biz_call": len(biz_calls) == 0,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                    })

    # router.py 中的 include 前缀
    rp = open(os.path.join(API_DIR, "router.py"), encoding="utf-8").read()
    includes = {}
    for m in re.finditer(r"include_router\((\w+)_router,\s*prefix=[\"']([^\"']*)[\"']", rp):
        includes[m.group(1)] = m.group(2)
    included_mods = set(includes.keys())

    routes = []
    for r in results:
        if "error" in r:
            continue
        modkey = r["file"][:-3]
        inc = includes.get(modkey)
        if inc is None:
            r["registered"] = False
            r["full_path"] = None
        else:
            r["registered"] = True
            r["full_path"] = "/api/v1" + inc + r["prefix"] + (r["raw_path"] or "")
        routes.append(r)

    # 重复路由
    dup = {}
    for r in routes:
        if not r["full_path"]:
            continue
        key = (r["method"], r["full_path"].rstrip("/") or "/")
        dup.setdefault(key, []).append(r)

    duplicates = {f"{k[0]} {k[1]}": [
        {"file": x["file"], "lineno": x["lineno"], "func": x["func"]} for x in v]
        for k, v in dup.items() if len(v) > 1}

    # 参数 vs 字面量 冲突（同 method，同段数）
    conflicts = []
    by_method = {}
    for r in routes:
        if not r["full_path"]:
            continue
        by_method.setdefault(r["method"], []).append(r)
    for method, rs in by_method.items():
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                pa = [s for s in a["full_path"].split("/") if s]
                pb = [s for s in b["full_path"].split("/") if s]
                if len(pa) != len(pb):
                    continue
                hit = False
                shadow = caster = None
                for sa, sb in zip(pa, pb):
                    if sa == sb:
                        continue  # 该段一致，继续比较
                    aparam = sa.startswith("{") and sa.endswith("}")
                    bparam = sb.startswith("{") and sb.endswith("}")
                    if aparam and bparam:
                        hit = False
                        break  # 同为参数段（名字不同但等价），不算冲突
                    if not aparam and not bparam:
                        hit = False
                        break  # 字面量不同 → 路径在此分叉，不可能互相遮蔽
                    if aparam and not bparam:
                        hit = True  # a 是参数段 ⇒ a 遮蔽 b 的字面路由
                        shadow, caster = b, a
                        break
                    hit = True  # b 是参数段 ⇒ b 遮蔽 a
                    shadow, caster = a, b
                    break
                if hit:
                    order = (a["file"], a["lineno"]) <= (b["file"], b["lineno"])
                    first, second = (a, b) if order else (b, a)
                    conflicts.append({
                        "method": method,
                        "path_a": a["full_path"], "file_a": a["file"], "line_a": a["lineno"],
                        "path_b": b["full_path"], "file_b": b["file"], "line_b": b["lineno"],
                        "shadowed": shadow["full_path"],
                        "shadowed_file": shadow["file"], "shadowed_line": shadow["lineno"],
                        "param_route": caster["full_path"],
                        "first_registered": f"{first['file']}:{first['lineno']}",
                        "second_registered": f"{second['file']}:{second['lineno']}",
                    })

    # 未注册的 router 模块
    all_mods = set()
    for fname in sorted(os.listdir(API_DIR)):
        if fname.endswith(".py") and fname not in ("__init__.py", "router.py"):
            src = open(os.path.join(API_DIR, fname), encoding="utf-8").read()
            if re.search(r"^\s*router\s*=\s*APIRouter", src, re.M):
                all_mods.add(fname[:-3])
    unregistered = sorted(all_mods - included_mods)

    summary = {
        "total_routes": len([r for r in routes]),
        "registered_routes": len([r for r in routes if r["registered"]]),
        "unregistered_routes": len([r for r in routes if not r["registered"]]),
        "modules_with_router": sorted(all_mods),
        "included_modules": sorted(included_mods),
        "unregistered_modules": unregistered,
        "stub": [r for r in routes if r["verdict"] == "STUB"],
        "todo": [r for r in routes if r["verdict"] == "TODO"],
        "thin": [r for r in routes if r["verdict"] == "THIN"],
        "missing_deps": [r for r in routes if r["missing_deps"]],
        "no_biz_call": [r for r in routes if r["no_biz_call"]],
        "duplicates": duplicates,
        "conflicts": conflicts,
        "routes": routes,
        "mod_prefix": mod_prefix,
        "includes": includes,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(f"routes={summary['total_routes']} registered={summary['registered_routes']}")
    print(f"STUB={len(summary['stub'])} TODO={len(summary['todo'])} THIN={len(summary['thin'])}")
    print(f"missing_deps={len(summary['missing_deps'])}")
    print(f"unregistered_modules={unregistered}")
    print("DUPLICATES:")
    for k, v in duplicates.items():
        print("  ", k, "->", v)
    print("CONFLICTS:")
    for c in conflicts:
        print(f"   {c['method']} {c['param_route']} shadows {c['shadowed']} "
              f"({c['shadowed_file']}:{c['shadowed_line']})")
    print("STUBS:")
    for s in summary["stub"]:
        print(f"   {s['file']}:{s['lineno']} {s['method']} {s['raw_path']} {s['func']} :: {s['reason']}")
    print("TODO:")
    for s in summary["todo"]:
        print(f"   {s['file']}:{s['lineno']} {s['method']} {s['raw_path']} {s['func']}")
    print("THIN:")
    for s in summary["thin"]:
        print(f"   {s['file']}:{s['lineno']} {s['method']} {s['raw_path']} {s['func']} :: {s['reason']}")
    print("MISSING_DEPS:")
    for s in summary["missing_deps"]:
        print(f"   {s['file']}:{s['lineno']} {s['func']} missing={s['missing_deps']}")
    print("NO_BIZ_CALL (handler 未调用任何业务函数 —— 疑似空壳):")
    for s in summary["no_biz_call"]:
        print(f"   {s['file']}:{s['lineno']} {s['method']} {s['raw_path']} {s['func']} "
              f"stmts={s['stmts']} verdict={s['verdict']}")


if __name__ == "__main__":
    sys.exit(main())
