"""AQP 全仓死代码候选扫描（审核工具，不属于生产代码）。

用途：为 `docs/audit-2026-09-18/` 的死代码清单提供**机器可复核**的候选证据。
输出为候选清单，不是结论——FastAPI 路由/依赖、Pydantic 字段、pytest fixture、
动态 import 等都会造成假阳性，必须人工研判。

用法：
    backend/.venv/Scripts/python.exe docs/audit-2026-09-18/tools/deadcode_scan.py
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend" / "src"

PY_ROOTS = [BACKEND / "app", BACKEND / "scripts", BACKEND / "tests"]
TS_ROOT = FRONTEND


def iter_py() -> list[Path]:
    out: list[Path] = []
    for r in PY_ROOTS:
        if r.exists():
            out += [p for p in r.rglob("*.py") if "__pycache__" not in p.parts]
    return out


def iter_ts() -> list[Path]:
    return [
        p
        for p in TS_ROOT.rglob("*")
        if p.suffix in {".ts", ".tsx"} and p.is_file()
    ]


# --------------------------------------------------------------------- Python
class PyModule:
    def __init__(self, path: Path, src: str) -> None:
        self.path = path
        self.src = src
        self.defs: dict[str, int] = {}          # name -> lineno
        self.methods: dict[str, int] = {}       # Class.method -> lineno
        self.dynamic: set[str] = set()          # decorated/registered names
        self.imports: set[str] = set()

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(ROOT)).replace("\\", "/")


def _decorator_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for d in getattr(node, "decorator_list", []) or []:
        try:
            names.append(ast.unparse(d))
        except Exception:  # noqa: BLE001
            names.append("")
    return names


def parse_py(path: Path) -> PyModule | None:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError as e:  # pragma: no cover
        print(f"[warn] syntax error {path}: {e}", file=sys.stderr)
        return None
    mod = PyModule(path, src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.defs[node.name] = node.lineno
            decs = " ".join(_decorator_names(node))
            # 路由/任务注册/事件钩子：按装饰器动态引用，不算死代码
            if re.search(r"router|app\.|click|command|task|fixture|hook|validator|serializer", decs):
                mod.dynamic.add(node.name)
            if node.name.startswith("test_") or node.name.startswith("_"):
                mod.dynamic.add(node.name)
        elif isinstance(node, ast.ClassDef):
            mod.defs[node.name] = node.lineno
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if sub.name.startswith("__"):
                        continue
                    mod.methods[f"{node.name}.{sub.name}"] = sub.lineno
                    decs = " ".join(_decorator_names(sub))
                    if re.search(r"router|app\.|property|validator|serializer|command", decs):
                        mod.dynamic.add(f"{node.name}.{sub.name}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                mod.imports.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    mod.defs[t.id] = node.lineno
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            mod.defs[node.target.id] = node.lineno
    return mod


def main() -> int:
    py_files = iter_py()
    mods = [m for m in (parse_py(p) for p in py_files) if m]
    blob = {m.rel: m.src for m in mods}
    all_src = "\n".join(blob.values())

    # 所有名字的全局出现次数（含字符串内出现，故为「候选」）
    word = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    ref_count: dict[str, int] = defaultdict(int)
    for src in blob.values():
        for w in word.findall(src):
            ref_count[w] += 1

    unreferenced: list[dict] = []
    for m in mods:
        for name, lineno in sorted(m.defs.items(), key=lambda kv: kv[1]):
            if name in m.dynamic or name.startswith("__"):
                continue
            if ref_count[name] <= 1:
                unreferenced.append(
                    {"file": m.rel, "line": lineno, "name": name, "kind": "top-level"}
                )
        for name, lineno in sorted(m.methods.items(), key=lambda kv: kv[1]):
            short = name.split(".", 1)[1]
            if name in m.dynamic:
                continue
            if ref_count[short] <= 1:
                unreferenced.append(
                    {"file": m.rel, "line": lineno, "name": name, "kind": "method"}
                )

    # 未被任何模块 import 的模块（按模块名匹配 import 语句）
    imported: set[str] = set()
    imp_re = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.M)
    for src in blob.values():
        for a, b in imp_re.findall(src):
            for dotted in (a, b):
                if dotted:
                    imported.add(dotted.split(".")[-1])
                    imported.add(dotted)
    orphan_modules: list[str] = []
    for m in mods:
        if m.path.name in {"__init__.py", "__main__.py", "conftest.py"}:
            continue
        stem = m.path.stem
        if stem.startswith("test_"):
            continue
        if stem not in imported:
            orphan_modules.append(m.rel)

    # Settings 配置项是否被读取（只在 app/ 内统计，排除定义处）
    settings_unread: list[str] = []
    cfg = BACKEND / "app" / "core" / "config.py"
    if cfg.exists():
        src = cfg.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        fields: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                        fields.append((sub.target.id, sub.lineno))
                    elif isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            if isinstance(t, ast.Name):
                                fields.append((t.id, sub.lineno))
        app_src = "\n".join(
            s for rel, s in blob.items() if rel.startswith("backend/app/")
        )
        for name, lineno in fields:
            if name.startswith("_") or name.isupper() and ref_count[name] > 1:
                pass
            uses = len(re.findall(rf"\b{re.escape(name)}\b", app_src))
            # 定义处 1 次；被 get_settings().X 或 settings.X 读取会额外出现
            if uses <= 1:
                settings_unread.append(f"config.py:{lineno} {name}")

    # ------------------------------------------------------------------ TS/TSX
    ts_files = iter_ts()
    ts_src = {str(p.relative_to(ROOT)).replace("\\", "/"): p.read_text(encoding="utf-8", errors="replace") for p in ts_files}
    ts_blob = "\n".join(ts_src.values())
    ts_ref: dict[str, int] = defaultdict(int)
    for w in word.findall(ts_blob):
        ts_ref[w] += 1

    ts_unreferenced: list[dict] = []
    exp_re = re.compile(
        r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:const|let|var|function|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
        re.M,
    )
    seen: set[str] = set()
    for rel, src in ts_src.items():
        for name in set(exp_re.findall(src)):
            if name in seen:
                continue
            seen.add(name)
            if ts_ref[name] <= 1:
                line = src[: src.find(name)].count("\n") + 1
                ts_unreferenced.append({"file": rel, "line": line, "name": name})

    # 从未被其它文件 import 的前端文件
    ts_orphans: list[str] = []
    for rel, src in ts_src.items():
        if rel.endswith(("main.tsx", "vite-env.d.ts")):
            continue
        stem = Path(rel).stem
        if stem == "index":
            stem = Path(rel).parent.name
        pat = re.compile(rf"""from\s+['"][^'"]*{re.escape(stem)}['"]|import\(['"][^'"]*{re.escape(stem)}['"]\)""")
        hits = sum(len(pat.findall(s)) for r, s in ts_src.items() if r != rel)
        if hits == 0:
            ts_orphans.append(rel)

    report = {
        "python": {
            "files_scanned": len(mods),
            "unreferenced_names": unreferenced,
            "orphan_modules": sorted(orphan_modules),
            "settings_maybe_unread": settings_unread,
        },
        "frontend": {
            "files_scanned": len(ts_src),
            "unreferenced_exports": sorted(ts_unreferenced, key=lambda d: d["file"]),
            "orphan_files": sorted(ts_orphans),
        },
    }
    out = ROOT / "docs" / "audit-2026-09-18" / "deadcode-candidates.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"python files: {len(mods)}  unreferenced names: {len(unreferenced)}")
    print(f"orphan modules: {len(orphan_modules)}  maybe-unread settings: {len(settings_unread)}")
    print(f"ts files: {len(ts_src)}  unreferenced exports: {len(ts_unreferenced)}  orphan files: {len(ts_orphans)}")
    print(f"-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())