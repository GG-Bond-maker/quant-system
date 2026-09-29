"""审计 P0-1 防回归：``PROJECT_ROOT`` 必须在**容器布局**下解析到 ``/app``。

缺陷（2026-09-21 全栈审计，P0）：
    ``core/config.py`` 用 ``Path(__file__).resolve().parents[3]`` 取数据根。
    容器内 ``COPY backend/app /app/app`` ⇒ 本文件位于 ``/app/app/core/config.py``，
    ``parents[3]`` 退化为 **``/``**，于是
      LOG_DIR   = /backend/logs      ← mkdir 失败（`read_only: true` + uid 10001）
      DATA_ROOT = /data/parquet      ← 应为 /app/data/parquet（compose 卷的位置）
      MODEL_ROOT= /data/models
      SQLITE    = /data/sqlite/aqp.db
    而 compose 已声明 ``./data:/app/data`` 与 ``./backend/logs:/app/logs``
    ⇒ 预期布局是 ``PROJECT_ROOT=/app``。原实现导致 **API 容器根本起不来**。

本文件把两条口径同时钉死：
    1. 容器布局 → ``BACKEND_ROOT = PROJECT_ROOT = <sandbox>/app``，
       ``LOG_DIR = <sandbox>/app/logs``、``DATA_ROOT = <sandbox>/app/data/parquet``；
    2. **本地仓库布局的取值一字不变**（``LOG_DIR`` 仍是 ``<repo>/backend/logs``）——
       否则"修容器"会顺手改掉开发机上的路径。
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

BACKEND_ROOT_REAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT_REAL))

from app.core import config as real_config  # noqa: E402

REAL_CONFIG_PY = BACKEND_ROOT_REAL / "app" / "core" / "config.py"


def _import_config_from(path: Path, name: str):
    """从任意路径加载一份 config.py。

    注意：该模块使用 ``from __future__ import annotations``，而 pydantic 需要能
    按模块名解析 ``Literal`` 等前向引用，因此必须**先注册到 sys.modules 再 exec**。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


def _field_default(mod, field: str) -> Path:
    """取字段的**默认值**（绕过 conftest 为隔离而注入的 DATA_ROOT/LOG_DIR 等 env）。"""
    return Path(mod.Settings.model_fields[field].default)


@pytest.fixture
def container_layout(tmp_path: Path) -> Path:
    """构造容器式布局： <tmp>/app/app/{main.py,core/config.py}（无 <repo>/backend）。"""
    app_root = tmp_path / "app"
    (app_root / "app" / "core").mkdir(parents=True)
    (app_root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (app_root / "app" / "main.py").write_text("", encoding="utf-8")
    shutil.copy2(REAL_CONFIG_PY, app_root / "app" / "core" / "config.py")
    return app_root


def test_container_layout_project_root_is_app(
    container_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """容器布局：PROJECT_ROOT/BACKEND_ROOT 都应是 <sandbox>/app，而不是 / 或 <sandbox>。"""
    for key in ("AQP_PROJECT_ROOT", "PROJECT_ROOT"):
        monkeypatch.delenv(key, raising=False)
    mod = _import_config_from(
        container_layout / "app" / "core" / "config.py", "cfg_container_probe")

    assert mod.BACKEND_ROOT == container_layout, (
        f"BACKEND_ROOT 解析错误：{mod.BACKEND_ROOT}（期望 {container_layout}）")
    assert mod.PROJECT_ROOT == container_layout, (
        f"PROJECT_ROOT 解析错误：{mod.PROJECT_ROOT}（期望 {container_layout}，"
        f"原实现会退化成 '/'，正是 P0-1）")

    # 比对**字段默认值**：conftest 会用 env 把 DATA_ROOT/LOG_DIR 重定向到临时目录，
    # 因此不能断言解析后的 Settings 值。
    assert _field_default(mod, "LOG_DIR") == container_layout / "logs", (
        f"LOG_DIR 默认值={_field_default(mod, 'LOG_DIR')}，应为 {container_layout / 'logs'}"
        "（对应 compose 的 ./backend/logs:/app/logs）")
    assert _field_default(mod, "DATA_ROOT") == container_layout / "data" / "parquet"
    assert _field_default(mod, "MODEL_ROOT") == container_layout / "data" / "models"


def test_container_layout_directories_are_creatable(
    container_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """容器布局下四个目录必须都能真的建出来，且都落在 <sandbox>/app 之下。"""
    for key in ("AQP_PROJECT_ROOT", "PROJECT_ROOT", "LOG_DIR", "DATA_ROOT",
                "MODEL_ROOT", "SQLITE_URL"):
        monkeypatch.delenv(key, raising=False)
    mod = _import_config_from(
        container_layout / "app" / "core" / "config.py", "cfg_container_dirs")
    s = mod.get_settings()
    for d in (s.LOG_DIR, s.DATA_ROOT, s.MODEL_ROOT, s.SQLITE_PATH.parent):
        assert d.is_dir(), f"{d} 未能创建"
        assert container_layout in d.parents, (
            f"{d} 落到容器布局之外（原实现的症状：/backend、/data）")


def test_repo_layout_values_unchanged() -> None:
    """本地仓库布局的取值必须与修复前完全一致（防止修容器时改坏开发机）。"""
    repo_root = BACKEND_ROOT_REAL.parent
    assert real_config.BACKEND_ROOT == BACKEND_ROOT_REAL
    assert real_config.PROJECT_ROOT == repo_root
    # 原实现：LOG_DIR = PROJECT_ROOT/"backend"/"logs" = <repo>/backend/logs
    assert _field_default(real_config, "LOG_DIR") == BACKEND_ROOT_REAL / "logs"
    assert _field_default(real_config, "DATA_ROOT") == repo_root / "data" / "parquet"
    assert _field_default(real_config, "MODEL_ROOT") == repo_root / "data" / "models"
    assert (real_config.Settings.model_fields["SQLITE_URL"].default).find(
        str(repo_root / "data" / "sqlite" / "aqp.db")) >= 0


def test_env_override_takes_priority(
    container_layout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AQP_PROJECT_ROOT 显式覆盖优先于所有推断。"""
    override = tmp_path / "explicit_root"
    override.mkdir()
    monkeypatch.setenv("AQP_PROJECT_ROOT", str(override))
    mod = _import_config_from(
        container_layout / "app" / "core" / "config.py", "cfg_env_override")
    assert mod.PROJECT_ROOT == override


def test_ensure_dir_error_names_the_setting(tmp_path: Path) -> None:
    """目录不可创建时，错误信息必须点名该改哪个配置（原先只抛裸 EROFS）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RuntimeError) as ei:
        real_config._ensure_dir(blocker / "sub", what="行情数据", setting="DATA_ROOT")
    msg = str(ei.value)
    assert "DATA_ROOT" in msg and "行情数据" in msg and str(blocker) in msg


def test_logging_degrades_instead_of_dying(
    container_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """日志文件 sink 不可用时只能降级（控制台仍在），不得让进程"零日志"崩溃。

    ``core/logging.py`` 使用相对导入，无法脱离包独立加载，因此直接用真实模块 +
    构造一个 LOG_DIR 指向"文件而非目录"的 Settings。
    原实现（``enqueue=True`` 且无 ``delay=True``）会在 ``logger.remove()`` 之后
    ``logger.add`` 抛错 ⇒ sink 数归零、异常冒泡；修复后应静默降级。
    """
    from loguru import logger as _logger

    from app.core import logging as log_mod
    from app.core.config import Settings

    bad = container_layout / "bad_logs"
    bad.write_text("not a directory", encoding="utf-8")
    s = Settings(LOG_DIR=str(bad))          # 显式 init 优先于 env

    log_mod.setup_logging(settings=s)       # 不得抛异常

    handlers = len(_logger._core.handlers)  # noqa: SLF001 探针：确认仍有 sink
    assert handlers >= 1, (
        "文件 sink 失败后连控制台 sink 都没了 —— 这正是 P0-1 的'零日志崩溃'")