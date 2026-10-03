"""静态解析 backend/app/api 下的 FastAPI 路由，导出为 CSV/MD。

用法: python extract_routes.py <out_csv>
"""
import ast
import os
import re
import sys

ROOT = r"D:\Python_Project\Alpha Quant Platform"
API_DIR = os.path.join(ROOT, "backend", "app", "api")

METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "api_route"}

# router 定义处: router = APIRouter(prefix=..., tags=...)
# include_router(xxx, prefix=..., tags=...)


def const_str(node):
    try:
        v = ast.literal_eval(node)
    except Exception:
        return None
    return v


def module_prefixes():
    """返回 {模块文件名(无.py): (own_prefix, tags)}"""
    res = {}
    v1 = os.path.join(API_DIR, "v1")
    for fn in os.listdir(v1):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(v1, fn)
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        own_prefix = ""
        tags = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                tgt = node.targets[0]
                name = getattr(tgt, "id", None)
                if name in ("router",) or (isinstance(name, str) and name.endswith("_router")):
                    if isinstance(node.value, ast.Call):
                        f = node.value.func
                        fname = getattr(f, "id", getattr(f, "attr", ""))
                        if fname == "APIRouter":
                            for kw in node.value.keywords:
                                if kw.arg == "prefix":
                                    own_prefix = const_str(kw.value) or ""
                                if kw.arg == "tags":
                                    tags = const_str(kw.value) or []
        res[fn[:-3]] = (own_prefix, tags)
    return res


def router_registry():
    """返回 {router变量名: (模块名)} 来自 router.py 的 import"""
    src = open(os.path.join(API_DIR, "v1", "router.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    reg = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                reg[a.asname or a.name] = node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                reg[a.asname or a.name] = a.name
    # 收集 include_router 的 prefix
    inc = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            fname = getattr(f, "attr", getattr(f, "id", ""))
            if fname == "include_router" and node.args:
                arg0 = node.args[0]
                nm = getattr(arg0, "id", None)
                prefix = ""
                for kw in node.keywords:
                    if kw.arg == "prefix":
                        prefix = const_str(kw.value) or ""
                if nm:
                    inc[nm] = prefix
    return reg, inc


def main():
    out = sys.argv[1]
    modpref = module_prefixes()
    reg, inc = router_registry()
    rows = []
    v1 = os.path.join(API_DIR, "v1")
    for fn in sorted(os.listdir(v1)):
        if not fn.endswith(".py") or fn == "router.py" or fn == "__init__.py":
            continue
        mod = fn[:-3]
        path = os.path.join(v1, fn)
        src = open(path, encoding="utf-8").read()
        lines = src.splitlines()
        tree = ast.parse(src)
        own_prefix, tags = modpref.get(mod, ("", []))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                f = dec.func
                if not isinstance(f, ast.Attribute):
                    continue
                meth = f.attr.lower()
                if meth not in METHODS:
                    continue
                base = getattr(f.value, "id", None)
                if base is None:
                    base = "?"
                p = ""
                if dec.args:
                    p = const_str(dec.args[0]) or ""
                elif dec.keywords:
                    for kw in dec.keywords:
                        if kw.arg == "path":
                            p = const_str(kw.value) or ""
                name = ""
                summary = ""
                # docstring 首行
                doc = ast.get_docstring(node) or ""
                summary = doc.strip().splitlines()[0] if doc else ""
                rows.append({
                    "method": meth.upper(),
                    "module": mod,
                    "file": f"backend/app/api/v1/{fn}",
                    "line": node.lineno,
                    "func": node.name,
                    "raw_path": p,
                    "own_prefix": own_prefix,
                    "router_prefix": inc.get(mod + "_router", ""),
                    "doc": summary[:160],
                })
    # 计算最终路径
    import csv
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "prefix", "path", "module", "file", "line", "func", "doc"])
        for r in rows:
            prefix = r["router_prefix"] + r["own_prefix"]
            full = ("/api/v1" + prefix + r["raw_path"]) or "/api/v1"
            w.writerow([r["method"], prefix, full, r["module"], r["file"], r["line"], r["func"], r["doc"]])
    print("total routes:", len(rows))


if __name__ == "__main__":
    main()
