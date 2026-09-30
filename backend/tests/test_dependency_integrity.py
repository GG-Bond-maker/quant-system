"""依赖完整性 pytest 门禁（B-1b，2026-09-30 上线前全检）。

与 ``scripts/check_deps.py`` 的关系
----------------------------------
``scripts/check_deps.py`` 是 **CI 层**门禁（独立进程、纯环境检查、非零退出）；
本文件是 **测试层** 门禁，把同一件事纳入 pytest 套件，好处有二：

1. 本地 ``pytest`` 就能暴露环境损坏 —— 不必等 CI；
2. 一旦有人删掉 CI 里的 ``check_deps`` 步骤，测试仍会红。

⚠️ 本文件**不重复实现**检查逻辑，而是 import ``scripts/check_deps.py`` 复用，
避免"两处规则不一致"这一经典陷阱。``scripts/`` 不是包，故用 ``importlib``
按路径加载。

设计取舍
--------
* 冒烟检查（35 个包 import）**较慢**（~1-2s）但值得：它是唯一能抓住
  "包在盘上、import 才炸"的检查。
* ``pip check`` 走子进程（~0.5s）。
* 两处都有 ``pytest.mark.slow`` 语义但**不标记 skip**：环境门禁一旦放过，
  B-1b 就白修了。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_deps.py"


def _load_checker():
    """按路径加载 check_deps.py（scripts/ 非包，不能直接 import）。

    ⚠️ 必须先 ``sys.modules[NAME] = mod`` 再 ``exec_module``：check_deps 里用了
    ``@dataclass``，而 dataclasses 在解析字段类型时会
    ``sys.modules.get(cls.__module__).__dict__`` —— 模块未注册则 ``get`` 返回
    ``None`` ⇒ ``AttributeError: 'NoneType' object has no attribute '__dict__'``。
    """
    import sys

    assert _SCRIPT.exists(), f"门禁脚本缺失: {_SCRIPT}"
    name = "_aqp_check_deps"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def checker():
    return _load_checker()


def test_pip_check_clean(checker):
    """A. 依赖声明层：``pip check`` 必须零不一致。

    B-1b 事故现场曾有 4 条：孤儿 openai 声明不存在的 httpx2/jiter、
    jsonpath 缺 dist-info、akshare 要求 aiohttp>=3.11.13 而实装 3.10.10。
    """
    issues = checker.check_pip_check()
    assert not issues, "pip check 不一致:\n" + "\n".join(i.render() for i in issues)


def test_import_smoke(checker):
    """B. 运行时层：每个生产依赖都必须能 import，且探针符号存在。

    这道检查专门防"空壳包 / 残缺包" —— 它们对所有测试都是隐形的。
    """
    issues = checker.check_smoke()
    assert not issues, "import 冒烟失败:\n" + "\n".join(i.render() for i in issues)


def test_distinfo_unique(checker):
    """C. 元数据层：同一发行名不得有多套 dist-info 并存。

    B-1b 事故现场有 9 个包各含两套（aiosqlite / alembic / fonttools /
    mako / numpy / pyparsing / pytz / threadpoolctl / watchfiles），
    是 aborted pip 事务的残留。
    """
    issues = checker.check_distinfo_unique()
    assert not issues, "dist-info 重复:\n" + "\n".join(i.render() for i in issues)


def test_pinned_versions_match_requirements(checker):
    """D. 显式 pin 的包，实装版本必须与 requirements.txt 逐字一致。

    只校验 ``==`` 形式的 pin；``>=`` 等开区间跳过（由 pip 解析决定）。
    """
    import importlib.metadata as md

    req = (pathlib.Path(__file__).resolve().parents[1] / "requirements.txt").read_text(
        encoding="utf-8"
    )
    mismatches: list[str] = []
    for raw in req.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        # 处理 `pkg==1.2.3` 与 `pkg[extra]==1.2.3`
        name, _, ver = line.partition("==")
        name = name.split("[", 1)[0].strip()
        ver = ver.strip()
        try:
            actual = md.version(name)
        except md.PackageNotFoundError:
            mismatches.append(f"{name}: requirements 要求 {ver}，但未安装")
            continue
        if actual != ver:
            mismatches.append(f"{name}: requirements={ver} 实装={actual}")
    assert not mismatches, "版本漂移:\n  " + "\n  ".join(mismatches)
