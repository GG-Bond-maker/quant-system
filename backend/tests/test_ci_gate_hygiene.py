"""§8.2 第 20 项（CI/交付门禁）的回归锁：.dockerignore / .gitignore / 健康检查一致性。

本轮实测到的三件事，各锁一条：

1. **构建上下文 ≈ 7.12 GB 且含 `.env`**：仓库此前没有 `.dockerignore`，而
   `docker-compose.yml` 里所有服务 `context: .`（仓库根）⇒ 每次 build 都要把
   `backend/.venv` 4.70 GB、`data/` 2.26 GB、`frontend/node_modules` 149 MB
   以及**含真实凭据的 `.env`** 传给 docker daemon。此锁保证关键排除项存在，
   并且**绝不可能**因为后续编辑把 `.env` 重新纳入上下文。
2. **`.gitignore` 缺运行时/审计产物**：`backend/.tmp_*`、`qa_browser/`、
   `data/_purged_*/`、`.workbuddy-ai/` 会一直以未跟踪文件形式污染 `git status`，
   淹没真正的交付物。
3. **健康检查必须指向真实存在的路由且两端同 URL**：compose 曾用恒 200 的
   `/health` 覆盖镜像里的 `/health/ready`，使镜像那条声明成死配置、容器即使
   未就绪也永远 healthy（P1-43/DEP-10）。此锁同时校验
   "URL 存在"与"两处一致"，防止再次分叉。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

# Dockerfile 里会被 COPY 的路径 —— 任何一项被 .dockerignore 排除，镜像就缺件。
_MUST_KEEP = [
    "backend/requirements.txt",
    "backend/pytest.ini",
    "backend/app/main.py",
    "backend/app/api/v1/screener.py",
    "backend/scripts/build_features.py",
    "backend/tests/conftest.py",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/tailwind.config.js",
    "frontend/postcss.config.js",
    "frontend/nginx.conf",
    "frontend/src/main.tsx",
]

# 必须排除（体积/凭据/无关物）
_MUST_IGNORE = [
    ".env",
    "backend/.venv/pyvenv.cfg",
    "backend/.venv/Lib/site-packages/polars/__init__.py",
    "data/parquet/daily_bar/symbol=000001.SZ/year=2026.parquet",
    "data/sqlite/aqp.db",
    "frontend/node_modules/react/index.js",
    "node_modules/x/index.js",
    "backend/logs/app.log",
    "backend/.tmp_testrun/sitecustomize.py",
    ".mypy_cache/3.11/x.json",
    "backend/.mypy_cache/3.11/x.json",
    "docs/audit-2026-09-18/AQP-全栈审核报告.md",
    "qa_browser/shot.png",
    ".git/config",
    "backend/app/__pycache__/main.cpython-311.pyc",
]


def _dockerignore_spec():
    """按 docker 的排除语义构造匹配器。

    docker 用 Go 的 `filepath.Match` + `**`；`pathspec` 的 gitwildmatch 对
    **本文件用到的模式**（无转义、仅 `*`/`**`/目录后缀、一条 `!` 例外）语义一致，
    足以作为回归锁。若环境里没有 pathspec，则退化为"模式存在性 + 手写断言"。
    """
    text = (REPO / ".dockerignore").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    try:
        import pathspec
    except ImportError:  # pragma: no cover 依赖缺失时只做模式存在性检查
        return None, lines
    return pathspec.PathSpec.from_lines("gitwildmatch", lines), lines


def test_dockerignore_exists_at_repo_root():
    """构建上下文是仓库根（compose 的 `context: .`）⇒ .dockerignore 必须在根。"""
    assert (REPO / ".dockerignore").is_file(), \
        "缺少根 .dockerignore：构建上下文会是 7 GB 级且包含 .env"
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    # 根编排里 aqp-api / aqp-web 两个服务都是 `context: .`（仓库根）
    assert compose.count("context: .") >= 2, \
        "compose 的 context 变了，请同步复核 .dockerignore 位置"


def test_dockerignore_excludes_secrets_and_bulk():
    spec, lines = _dockerignore_spec()
    if spec is None:  # pragma: no cover
        for p in (".env", "data/", "**/.venv", "node_modules"):
            assert any(p in ln for ln in lines), f".dockerignore 缺少 {p}"
        return
    for path in _MUST_IGNORE:
        assert spec.match_file(path), f".dockerignore 未排除 {path}（体积/凭据风险）"


def test_dockerignore_keeps_everything_dockerfiles_copy():
    spec, _ = _dockerignore_spec()
    if spec is None:  # pragma: no cover
        pytest.skip("pathspec 不可用")
    for path in _MUST_KEEP:
        assert not spec.match_file(path), \
            f".dockerignore 误排除 {path}：Dockerfile 的 COPY 会失败或镜像缺件"


def test_dockerignore_negation_ordering_keeps_env_example():
    """`.env.*` 之后必须紧跟 `!.env.example`：例外要写在排除**之后**才生效。"""
    spec, _ = _dockerignore_spec()
    if spec is None:  # pragma: no cover
        pytest.skip("pathspec 不可用")
    assert spec.match_file(".env")
    assert not spec.match_file(".env.example")


def test_gitignore_covers_runtime_and_audit_artifacts():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pat in ("backend/.tmp_*/", "qa_browser/", "data/_purged_*/",
                ".workbuddy-ai/"):
        assert pat in gi, f".gitignore 缺少 {pat}（会持续污染 git status）"


def test_healthcheck_urls_are_consistent_and_route_exists():
    """镜像 HEALTHCHECK 与 compose healthcheck 必须同 URL，且该 URL 真是路由。"""
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")

    m_img = re.search(r"HEALTHCHECK[^\n]*\n\s*CMD\s+curl\s+-fsS\s+(\S+)", dockerfile)
    assert m_img, "backend/Dockerfile 缺少 HEALTHCHECK curl 行"
    img_url = m_img.group(1)

    m_cmp = re.search(r'test:\s*\[\s*"CMD"\s*,\s*"curl"\s*,\s*"-fsS"\s*,\s*"(\S+)"',
                      compose)
    assert m_cmp, "docker-compose.yml 缺少 healthcheck test 行"
    cmp_url = m_cmp.group(1)

    assert img_url == cmp_url, (
        f"健康检查 URL 分叉：镜像={img_url} compose={cmp_url}"
        "（历史上这里是 `/health` 覆盖 `/health/ready`，导致容器永远 healthy）"
    )

    # 该 URL 必须在 FastAPI 路由表里真实存在（否则健康检查恒失败 ⇒ 容器永不健康）
    from app.main import app

    path = "/" + img_url.split("127.0.0.1:8000/", 1)[1] if "8000/" in img_url \
        else img_url
    assert any(getattr(r, "path", None) == path for r in app.routes), \
        f"健康检查指向的路由 {path} 不存在"


def test_frontend_healthcheck_target_exists_in_nginx():
    """前端健康检查打 `/healthz` ⇒ nginx.conf 必须有对应的 location。"""
    dockerfile = (REPO / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    nginx = (REPO / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    m = re.search(r"HEALTHCHECK[^\n]*\n\s*CMD\s+curl\s+-fsS\s+(\S+)", dockerfile)
    assert m, "frontend/Dockerfile 缺少 HEALTHCHECK curl 行"
    url = m.group(1)
    path = "/" + url.split("127.0.0.1/", 1)[1] if "127.0.0.1/" in url else url
    assert f"location = {path}" in nginx or f"location {path}" in nginx, \
        f"nginx.conf 未定义 {path} ⇒ 前端容器健康检查恒失败"


# ---------------------------------------------------------------- 可执行脚本门禁

def test_scripts_bootstrap_has_no_undefined_name():
    """`scripts/` 的路径引导代码必须能真正执行（不得 NameError）。

    缺陷本体（实测复现）：`scripts/alerter.py` 用了 `sys.path` 却从未 `import sys`
    ⇒ 按 README 直接 `python scripts/alerter.py` 时**第 20 行就 NameError**，
    运维告警脚本 100% 不可用（历史审计记为 LOW-001，长期未修）。
    `compile()` 只查语法抓不到这类错，所以这里**真正执行**引导段。
    """
    import sys as _sys

    script = BACKEND / "scripts" / "alerter.py"
    src = script.read_text(encoding="utf-8")
    # 只执行到第三方 import 之前（引导段），避免触发 loguru enqueue 等运行时依赖
    head = src.split("import sqlite3")[0]
    ns: dict = {"__file__": str(script), "__name__": "aqp_alerter_bootstrap_probe"}
    exec(compile(head, str(script), "exec"), ns)  # noqa: S102 执行的是本仓脚本的引导段
    assert "sys" in ns, "引导段必须 import sys"
    assert ns.get("sys_path") in _sys.path, "引导段必须把 backend 根加入 sys.path"


def test_all_scripts_compile():
    """所有 `scripts/*.py` 必须可编译（防止提交语法坏脚本）。"""
    bad = []
    for p in sorted((BACKEND / "scripts").glob("*.py")):
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")
        except SyntaxError as e:  # pragma: no cover
            bad.append(f"{p.name}: {e}")
    assert not bad, "存在无法编译的脚本：\n" + "\n".join(bad)


# ---------------------------------------------------------------- 工作流门禁

def _workflows() -> dict[str, str]:
    wf = REPO / ".github" / "workflows"
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(wf.glob("*.yml"))}


def _default_branch() -> str:
    head = (REPO / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        return head.rsplit("/", 1)[-1]
    return "master"


def test_ci_push_trigger_covers_actual_default_branch():
    """CI 的 push 分支过滤必须包含**本仓真实默认分支**。

    缺陷本体：`ci.yml` 原写 `push: branches: [main]`，而本仓
    `git branch -a` 只有 `master`/`origin/master` ⇒ **推 master 时 CI 从不触发**，
    整套门禁（pytest/mypy/ruff/前端 build）在主干上形同不存在。
    """
    ci = _workflows()["ci.yml"]
    branch = _default_branch()
    assert re.search(rf"branches:\s*\[[^\]]*\b{re.escape(branch)}\b", ci), (
        f"ci.yml 的 push 分支过滤不含真实默认分支 {branch!r} ⇒ 主干推送不触发 CI")


def test_ci_ruff_step_scope_is_not_narrower_than_verified_clean_baseline():
    """ruff 步骤必须覆盖 `app tests scripts`（且规则集含 `F`）。

    缺陷本体：原 `ruff check app/ --select F401,F811,E9` 只查 `app/`、只 3 条规则
    ⇒ `tests/` 与 `scripts/` 的 27 处问题（含 `scripts/alerter.py` 的 F821，
    该脚本因此必然 NameError）在 CI 里永远看不见。范围一旦被改窄即报红。
    """
    ci = _workflows()["ci.yml"]
    m = re.search(r"run:\s*ruff check ([^\n]+)", ci)
    assert m, "ci.yml 里找不到 ruff check 步骤"
    cmd = m.group(1)
    for scope in ("app", "tests", "scripts"):
        assert re.search(rf"\b{scope}\b", cmd), f"ruff 范围缺 {scope}：{cmd!r}"
    assert "--select" in cmd and re.search(r"--select\s+\S*\bF\b", cmd), \
        f"ruff 规则集必须包含 F（Pyflakes）：{cmd!r}"


def test_no_workflow_step_swallows_failures():
    """CI 步骤不得用 `|| true` / `|| echo` 吞掉失败（会让红灯变绿灯）。

    缺陷本体：`nightly.yml` 的 Playwright E2E 原为
    `pytest tests/test_e2e.py -v || echo "E2E non-blocking"` ⇒ 整步 exit 0，
    失败也显示绿色，夜间 E2E 从不上报问题。非阻塞应写 `continue-on-error: true`。
    """
    offenders = []
    for name, text in _workflows().items():
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\|\|\s*(true|echo)\b", stripped):
                offenders.append(f"{name}:{i}: {stripped}")
    assert not offenders, ("工作流用 `|| true`/`|| echo` 吞掉失败 ⇒ 门禁信号丢失，"
                           "非阻塞请用 `continue-on-error: true`：\n  " + "\n  ".join(offenders))