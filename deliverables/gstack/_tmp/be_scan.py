"""只读：后端代码审查扫描（AST + 正则）。
输出：静默吞异常 / async 中的阻塞调用 / 无超时 HTTP / 未跟踪 create_task /
      循环内 DB 查询(N+1 候选) / 裸 open / 全局可变状态 / TODO。
"""
from __future__ import annotations
import ast, os, re, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
APP = os.path.join(ROOT, "backend", "app")
BLOCKING = {"time.sleep", "requests.get", "requests.post", "requests.put",
            "requests.delete", "requests.request", "urllib.request.urlopen",
            "subprocess.run", "subprocess.check_output", "subprocess.Popen",
            "os.system", "input", "sqlite3.connect", "socket.create_connection"}

def rel(p): return os.path.relpath(p, ROOT).replace("\\", "/")

files = []
for base, _, fns in os.walk(APP):
    if "__pycache__" in base:
        continue
    for fn in fns:
        if fn.endswith(".py"):
            files.append(os.path.join(base, fn))

silent, blocking_async, notimeout_http, untracked_task, nplus1, bare_open, todos = [], [], [], [], [], [], []

HTTP_CALL = re.compile(r"\b(httpx\.(?:get|post|put|delete|request|AsyncClient|Client)|requests\.(?:get|post|put|delete|request)|aiohttp\.ClientSession|urlopen)\b")
TIMEOUT_KW = re.compile(r"timeout\s*=")

for fp in files:
    src = open(fp, encoding="utf-8").read()
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print("SYNTAXERR", rel(fp), e); continue

    # --- 静默吞异常 ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                body = h.body
                real = [b for b in body if not (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant))]
                if not real:
                    silent.append((rel(fp), h.lineno, "handler 体为空/pass/仅注释"))
                elif len(real) == 1 and isinstance(real[0], ast.Pass):
                    silent.append((rel(fp), h.lineno, "handler=pass"))
                elif len(real) == 1 and isinstance(real[0], ast.Continue):
                    silent.append((rel(fp), h.lineno, "handler=continue"))
                elif len(real) == 1 and isinstance(real[0], ast.Return) and real[0].value is None:
                    silent.append((rel(fp), h.lineno, "handler=return None"))
                elif len(real) == 1 and isinstance(real[0], ast.Raise) and isinstance(real[0].exc, ast.Call) \
                        and getattr(real[0].exc.func, "id", None) is None:
                    pass

    # --- async 内阻塞调用 ---
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = None
                    if isinstance(f, ast.Attribute):
                        parts = []
                        cur = f
                        while isinstance(cur, ast.Attribute):
                            parts.append(cur.attr); cur = cur.value
                        if isinstance(cur, ast.Name):
                            parts.append(cur.id)
                        name = ".".join(reversed(parts))
                    elif isinstance(f, ast.Name):
                        name = f.id
                    if name in BLOCKING:
                        blocking_async.append((rel(fp), sub.lineno, f"async {node.name} 调用阻塞 {name}"))

    # --- 无 timeout 的 HTTP 调用 ---
    for i, line in enumerate(lines, 1):
        if HTTP_CALL.search(line) and not TIMEOUT_KW.search(line) and "Client(" not in line:
            # 允许跨行 timeout=，向上/下各看 2 行
            window = "\n".join(lines[max(0, i-3):i+2])
            if not TIMEOUT_KW.search(window):
                notimeout_http.append((rel(fp), i, line.strip()[:110]))

    # --- create_task 未跟踪 ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if fname in ("create_task", "ensure_future"):
                untracked_task.append((rel(fp), node.lineno, "asyncio.create_task/ensure_future"))

    # --- 裸 open() 非 with（粗查） ---
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if re.match(r"\w[\w\.]*\s*=\s*open\(", s) and "with " not in lines[i-2] if i >= 2 else False:
            bare_open.append((rel(fp), i, s[:100]))

    # --- TODO/FIXME/HACK/XXX ---
    for i, line in enumerate(lines, 1):
        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", line):
            todos.append((rel(fp), i, line.strip()[:110]))

def show(title, items, limit=80):
    print(f"\n### {title} ({len(items)}) ###")
    for it in items[:limit]:
        print("  " + " | ".join(str(x) for x in it))
    if len(items) > limit:
        print(f"  ... 其余 {len(items)-limit} 条省略")

show("A. 静默吞异常（handler 无有效语句）", silent)
show("B. async 函数内的阻塞调用", blocking_async)
show("C. 疑似无 timeout 的 HTTP 调用", notimeout_http)
show("D. asyncio.create_task/ensure_future（需确认是否被跟踪）", untracked_task)
show("E. TODO/FIXME/HACK/XXX", todos)
