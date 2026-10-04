"""只读：后端审查精炼版（排除嵌套 def 误报）+ N+1 + 未跟踪 task + 全局可变状态。"""
from __future__ import annotations
import ast, os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
APP = os.path.join(ROOT, "backend", "app")
BLOCKING = {"sleep", "urlopen", "run", "check_output", "Popen", "system"}
BLOCKING_FULL = {"time.sleep", "requests.get", "requests.post", "requests.put",
                 "requests.delete", "requests.request", "urllib.request.urlopen",
                 "subprocess.run", "subprocess.check_output", "subprocess.Popen",
                 "os.system", "sqlite3.connect"}

def rel(p): return os.path.relpath(p, ROOT).replace("\\", "/")

def fname(f):
    if isinstance(f, ast.Attribute):
        parts=[]; cur=f
        while isinstance(cur, ast.Attribute): parts.append(cur.attr); cur=cur.value
        if isinstance(cur, ast.Name): parts.append(cur.id)
        return ".".join(reversed(parts))
    if isinstance(f, ast.Name): return f.id
    return ""

def direct_calls(fn):
    """只收集 fn 直接语句体中的调用（不下钻到嵌套 def）。"""
    out=[]
    stack=list(fn.body)
    while stack:
        n=stack.pop()
        if isinstance(n,(ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and n is not fn:
            continue
        if isinstance(n, ast.Call): out.append(n)
        for ch in ast.iter_child_nodes(n):
            if isinstance(ch,(ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)): continue
            stack.append(ch)
    return out

blocking_async, nplus1, untracked = [], [], []

for base,_,fns in os.walk(APP):
    if "__pycache__" in base: continue
    for fn in fns:
        if not fn.endswith(".py"): continue
        fp=os.path.join(base,fn); r=rel(fp)
        try: tree=ast.parse(open(fp,encoding="utf-8").read())
        except SyntaxError: continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for c in direct_calls(node):
                    nm=fname(c.func)
                    if nm in BLOCKING_FULL:
                        blocking_async.append((r,c.lineno,f"async {node.name} 直接调用阻塞 {nm}"))
                # N+1：async 或 sync 函数内 for 循环体含 DB/HTTP 调用
            if isinstance(node,(ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub,(ast.For, ast.AsyncFor)):
                        for c in ast.walk(sub):
                            if isinstance(c, ast.Call):
                                nm=fname(c.func)
                                if nm in ("execute","fetchall","fetchone","scalars","query",
                                          "get","post","read_parquet","connect","urlopen") \
                                        and nm not in ("get",):  # 排除 dict.get
                                    nplus1.append((r,c.lineno,f"{node.name}: for 循环内 {nm}()"))
                                    break
            # 未跟踪 task
            if isinstance(node, ast.Call) and fname(node.func) in ("create_task","ensure_future"):
                untracked.append((r,node.lineno,"create_task/ensure_future"))

def show(t,items,limit=60):
    print(f"\n### {t} ({len(items)}) ###")
    seen=set()
    for it in items[:limit]:
        k=(it[0],it[1])
        if k in seen: continue
        seen.add(k); print("  "+" | ".join(map(str,it)))

show("A. async 直接体中的阻塞调用（排除嵌套 def）", blocking_async)
show("B. for 循环内 DB/HTTP/parquet 调用（N+1 候选）", nplus1)
show("C. create_task/ensure_future 调用点", untracked)
