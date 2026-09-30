#!/usr/bin/env python
"""依赖完整性门禁（CI）。

背景（2026-09-30 上线前全检 B-1b）
----------------------------------
本仓曾出现一类**监控完全看不见**的环境事故：

  1. 一次未完成的 ``pip`` 事务把 ``mypy`` 目录留在「半装」状态（原先用
     ``ls site-packages/mypy | wc -l`` 判定为"空壳"，**该判据本身是错的**
     —— mypy 是 mypyc 编译版，``dir(mypy)`` 天然为 0，见下方 ``probe`` 设计）；
  2. ``pip check`` 长期有 4 条不一致（孤儿包 ``openai`` 声明了不存在的
     ``httpx2``/``jiter``；``jsonpath`` 只有裸模块无 dist-info；
     ``aiohttp`` 同时存在 3.10.10 与 3.14.3 两套 dist-info）。

这些状态**不会让任何测试失败**（未 import 的包坏掉就是隐形的），
但只要某条代码路径真的 import 到它，就是运行时 ``ModuleNotFoundError``。

本脚本把三类检查合并成一道可入 CI 的门禁：

  A. ``pip check``              —— 依赖声明层：有无未满足的 requirement
  B. 逐包 import 冒烟 + 探针     —— 运行时时层：包是否真的能 import、是否"空壳"
  C. dist-info 唯一性            —— 元数据层：同一包不得有多套 dist-info 并存

设计要点
--------
* **探针不能靠 ``len(dir(mod))``**：mypyc 编译包（mypy / black / ...）的
  ``dir()`` 天然接近 0，用它判"空壳"会**误报**。故探针分两档：
  - ``probe='import'``：能 import 即可（大多数包）；
  - ``probe='call'``：额外要求一个已知符号存在且可调用（防"导入了但里面是空的"）。
  * 只有**明确知道**某个包"必须暴露某个符号"时才用 ``call``，避免耦合过深。
* **显式排除**可选依赖（如未安装的 torch），排除项是白名单，写死在
  ``OPTIONAL_PACKAGES`` 里，且必须注明原因。

用法::

    python scripts/check_deps.py            # 全部门禁，失败 exit=1
    python scripts/check_deps.py --quick    # 跳过 import 冒烟，只跑 pip check
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import pathlib
import subprocess
import sys
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

#: 冒烟清单： ``(import 名, dist 名, 探针)``
#:
#: * ``dist 名`` 用于核对 dist-info 是否存在（import 名常与发行名不同，
#:   如 ``PIL`` / ``Pillow``、``sklearn`` / ``scikit-learn``）。
#: * ``probe``：
#:   - ``"import"`` —— 能导入即可；
#:   - ``"call"``   —— 额外要求 ``(属性名, 期望类型)``，见 ``SYMBOL_PROBES``。
#:
#: 只列**生产运行真正需要**的包 + CI 门禁自身需要的包。
SMOKE_PACKAGES: tuple[tuple[str, str, str], ...] = (
    # Web 栈
    ("fastapi", "fastapi", "import"),
    ("starlette", "starlette", "import"),
    ("uvicorn", "uvicorn", "call"),
    ("pydantic", "pydantic", "call"),
    ("pydantic_settings", "pydantic-settings", "import"),
    ("anyio", "anyio", "import"),
    ("orjson", "orjson", "call"),
    # DB / Cache
    ("sqlalchemy", "sqlalchemy", "import"),
    ("aiosqlite", "aiosqlite", "import"),
    ("redis", "redis", "import"),
    ("hiredis", "hiredis", "import"),
    ("cachetools", "cachetools", "import"),
    # Log / Retry / HTTP
    ("loguru", "loguru", "import"),
    ("tenacity", "tenacity", "import"),
    ("httpx", "httpx", "call"),
    # DataFrame —— 注意 polars 是 rust 扩展，dir() 很少，用 call 探针
    ("polars", "polars", "call"),
    ("pandas", "pandas", "call"),
    ("pyarrow", "pyarrow", "call"),
    ("numpy", "numpy", "call"),
    # Finance / DataSource
    ("akshare", "akshare", "call"),
    ("baostock", "baostock", "import"),
    ("jsonpath", "jsonpath", "call"),
    # ML
    ("lightgbm", "lightgbm", "call"),
    ("narwhals", "narwhals", "import"),
    ("sklearn", "scikit-learn", "import"),
    ("joblib", "joblib", "import"),
    # P2
    ("openpyxl", "openpyxl", "import"),
    ("matplotlib", "matplotlib", "import"),
    # P3
    ("jwt", "PyJWT", "call"),
    ("prometheus_client", "prometheus-client", "import"),
    # Test / CI 门禁自身
    ("pytest", "pytest", "call"),
    ("pytest_asyncio", "pytest-asyncio", "import"),
    ("mypy", "mypy", "call"),
    ("ruff", "ruff", "import"),
    ("optuna", "optuna", "import"),
)

#: ``"call"`` 探针的具体断言： import 名 -> (属性名, 期望类型/说明)
#:
#: 选符号的原则：**包的主要入口**，且**版本间稳定**。避免选内部私有符号。
SYMBOL_PROBES: dict[str, tuple[str, str]] = {
    "uvicorn": ("run", "可调用（ASGI 服务入口）"),
    "pydantic": ("BaseModel", "类型（pydantic v2 基类）"),
    "orjson": ("dumps", "可调用（序列化入口）"),
    "httpx": ("AsyncClient", "类型（异步 HTTP 客户端）"),
    "polars": ("DataFrame", "类型（polars 核心类型）"),
    "pandas": ("DataFrame", "类型（pandas 核心类型）"),
    "pyarrow": ("__version__", "字符串（版本号；防残缺安装）"),
    "numpy": ("ndarray", "类型（numpy 核心类型）"),
    "akshare": ("__version__", "字符串（版本号）"),
    "jsonpath": ("jsonpath", "可调用（jsonpath 求值入口）"),
    "lightgbm": ("Booster", "类型（模型加载入口）"),
    "jwt": ("encode", "可调用（JWT 签发入口）"),
    "pytest": ("main", "可调用（pytest 入口）"),
    # ⚠️ mypy 是 mypyc 编译版：``dir(mypy)`` 为 0，且 ``mypy.api`` **不是**
    # 一个已在顶层绑定的属性 —— 必须子模块导入才会挂上。故不能用 hasattr。
    # 见 ``check_smoke`` 中对 ``"mypy"`` 的特判分支。
    "mypy": ("api", "模块（mypy API；编译版需子模块导入）"),
}

#: 需要"子模块导入后校验"的包： import 名 -> (子模块名, 必需属性)
#: 用于 mypyc 编译包这类「顶层 dir() 为空、属性靠子模块导入挂载」的情况。
SUBMODULE_PROBES: dict[str, tuple[str, str]] = {
    "mypy": ("mypy.api", "run"),
}

#: 允许缺失的包（可选依赖）。任何一项都必须写明"为什么允许缺失"。
OPTIONAL_PACKAGES: dict[str, str] = {
    "torch": "可选：TFT/GNN 训练脚手架；未装时相关 CLI 明确提示，其余功能不受影响",
}

#: 已知的"同名双 dist-info"白名单（规范化名 -> 允许的最大份数）。
#: 空 ⇒ 任何重复 dist-info 都判失败。这是防"pip 事务半途而废"的关键断言。
DISTINFO_DUPLICATE_ALLOWLIST: dict[str, int] = {}


# ---------------------------------------------------------------------------
# 检查实现
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    kind: str  # A / B / C
    target: str
    detail: str

    def render(self) -> str:
        return f"  [{self.kind}] {self.target}: {self.detail}"


def _canon(name: str) -> str:
    """PEP 503 规范化（小写 + 连续 -/_/. → -）。"""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def check_pip_check() -> list[Issue]:
    """A. ``pip check``：依赖声明层。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
    )
    out = (proc.stdout + proc.stderr).strip()
    issues: list[Issue] = []
    if proc.returncode != 0:
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("[notice]"):
                continue
            issues.append(Issue("A", "pip check", line))
        if not issues:  # 有非零退出但没解析到行 —— 也要报
            issues.append(Issue("A", "pip check", f"exit={proc.returncode} 且输出为空"))
    return issues


def check_smoke() -> list[Issue]:
    """B. 逐包 import 冒烟 + 符号探针。"""
    issues: list[Issue] = []
    installed = {_canon(d.metadata["Name"]) for d in md.distributions() if d.metadata["Name"]}

    for mod_name, dist_name, probe in SMOKE_PACKAGES:
        # B0: dist-info 是否存在（防"只有裸模块、无元数据"）
        if _canon(dist_name) not in installed:
            issues.append(
                Issue("B", mod_name, f"dist-info 缺失（发行名 {dist_name} 未注册元数据）")
            )
            continue

        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 —— 冒烟就要兜住一切
            issues.append(Issue("B", mod_name, f"import 失败: {type(exc).__name__}: {exc}"))
            continue

        if probe != "call":
            continue

        # 特判：mypyc 编译包（顶层 dir() 为空，属性靠子模块导入挂载）
        sub = SUBMODULE_PROBES.get(mod_name)
        if sub is not None:
            submod_name, attr = sub
            try:
                submod = importlib.import_module(submod_name)
            except Exception as exc:  # noqa: BLE001
                issues.append(
                    Issue("B", mod_name, f"子模块 {submod_name} 导入失败: {type(exc).__name__}: {exc}")
                )
                continue
            if not hasattr(submod, attr):
                issues.append(
                    Issue("B", mod_name, f"子模块 {submod_name} 缺少 {attr} —— 疑似残缺安装")
                )
            continue

        spec = SYMBOL_PROBES.get(mod_name)
        if spec is None:
            # 配了 call 却没登记符号 —— 是脚本自身的配置错误，必须报
            issues.append(Issue("B", mod_name, "探针配置错误：probe='call' 但未登记 SYMBOL_PROBES"))
            continue

        attr, desc = spec
        if attr == "__version__":
            val = getattr(mod, "__version__", None)
            if not isinstance(val, str) or not val.strip():
                issues.append(Issue("B", mod_name, f"__version__ 缺失或非法（值={val!r}）"))
        elif not hasattr(mod, attr):
            issues.append(Issue("B", mod_name, f"找不到符号 {attr}（{desc}）—— 疑似残缺/空壳安装"))
        elif not callable(getattr(mod, attr)) and "类型" not in desc and "模块" not in desc:
            issues.append(Issue("B", mod_name, f"符号 {attr} 不可调用（{desc}）"))

    # 可选依赖缺失不算失败，但要打印一行提示，便于排查
    for mod_name, reason in OPTIONAL_PACKAGES.items():
        try:
            importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001
            print(f"  [i] 可选依赖 {mod_name} 未安装（{reason}）")
    return issues


def check_distinfo_unique() -> list[Issue]:
    """C. 同一发行名的 dist-info 不得并存多份。

    实现说明（踩过的坑）
    --------------------
    最初用 ``importlib.metadata.distributions()`` 聚合，但它**会受
    ``.venv`` 中残留的 ``*.egg-info`` / 半装目录影响，返回值可能在同一进程内
    不一致**（实测同一次运行里 ``packaging`` 时有时无）。故这里改为**直接扫盘**
    —— ``site-packages/*.dist-info/`` 是唯一权威事实，且**必须在跑 CI 前就已稳定**。

    版本从目录名解析（``<name>-<version>.dist-info``）而非读 METADATA，
    以便捕捉"目录名与 METADATA 不一致"这类更隐蔽的损坏。
    """
    issues: list[Issue] = []

    # 定位 site-packages：取本项目解释器的 sysconfig 纯 lib 目录
    import sysconfig

    sp = pathlib.Path(sysconfig.get_paths()["purelib"])
    if not sp.is_dir():
        return [Issue("C", "site-packages", f"目录不存在: {sp}")]

    seen: dict[str, dict[str, str]] = {}  # canon -> {version: dir_name}
    for d in sorted(sp.glob("*.dist-info")):
        stem = d.name[: -len(".dist-info")]
        # 目录名形如 <name>-<version>；版本段可能含 '-' 之后的 distro 标记，
        # 故以**最后一个 '-' 之前的全部**为发行名（PEP 427 实际是先 name 后 version，
        # 且 name 里不会出现 '-'（被规范化成 '_'））。
        if "-" not in stem:
            continue
        name, _, version = stem.rpartition("-")
        canon = _canon(name)
        seen.setdefault(canon, {})[version] = d.name

    for canon, versions in sorted(seen.items()):
        if len(versions) <= DISTINFO_DUPLICATE_ALLOWLIST.get(canon, 1):
            continue
        detail = ", ".join(f"{v}({n})" for v, n in sorted(versions.items()))
        issues.append(Issue("C", canon, f"存在 {len(versions)} 套 dist-info 并存: {detail}"))
    return issues


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AQP 依赖完整性门禁")
    ap.add_argument("--quick", action="store_true", help="只跑 pip check，跳过 import 冒烟")
    args = ap.parse_args(argv)

    print("=" * 68)
    print("AQP 依赖完整性门禁")
    print("=" * 68)

    all_issues: list[Issue] = []

    print("\n[A] pip check（依赖声明层）")
    a = check_pip_check()
    print("  ✓ 无不一致" if not a else "\n".join(i.render() for i in a))
    all_issues += a

    if not args.quick:
        print(f"\n[B] import 冒烟 + 符号探针（{len(SMOKE_PACKAGES)} 个包）")
        b = check_smoke()
        print("  ✓ 全部可导入且探针通过" if not b else "\n".join(i.render() for i in b))
        all_issues += b

    print("\n[C] dist-info 唯一性")
    c = check_distinfo_unique()
    print("  ✓ 无重复" if not c else "\n".join(i.render() for i in c))
    all_issues += c

    print("\n" + "=" * 68)
    if all_issues:
        print(f"❌ 门禁未通过：{len(all_issues)} 项问题")
        print("=" * 68)
        return 1
    print("✅ 门禁通过：依赖完整性无异常")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
