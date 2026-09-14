"""
测试隔离（第九阶段：HIGH-002 修复）。

修复前的问题：
    仓库缺少 conftest.py，测试直接读写**生产**环境：
    - SQLite 用 ``get_settings().SQLITE_PATH`` -> data/sqlite/aqp.db
      实测 ``test_p2_ml`` 断言 ``n == 81`` 失败，实际 648 = 81 × 8（跨运行累积）；
      feature_runs 表累积 685+ 行、model_registry 72 行，全是测试垃圾；
    - ``MODEL_ROOT`` 指向 data/models，测试训练写入 384+ 个目录，
      而旧 ``load_prod_model`` 按目录名取"最新" -> 测试模型会变成线上模型；
    - 结果还依赖执行顺序：``test_pipeline.py`` 单独跑 6 passed，
      全量跑却间歇性 4 连败。

现在的策略：
    在 **pytest_configure**（早于任何测试模块导入）把
    SQLITE_URL / DATA_ROOT / MODEL_ROOT 全部重定向到本次会话的临时目录，
    并清掉 ``get_settings`` 的 lru_cache 与全局 DB 引擎缓存。
    会话结束后清理临时目录。

效果：
    - pytest 单独跑 与 全量跑 结果一致；
    - 测试绝不修改生产数据库 / 生产数据仓库 / 生产模型目录。

如需让某个测试显式使用真实环境，用 ``real_env`` marker 并在测试内自行处理。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_TMP_ROOT: Path | None = None
_ENV_KEYS = ("SQLITE_URL", "DATA_ROOT", "MODEL_ROOT", "LOG_DIR",
             "WARM_OVERVIEW_ON_STARTUP", "REDIS_ENABLED")


# --------------------------------------------------------------- anyio 线程收尾
# 背景（2026-09-12 定位：pytest 打印完 summary 后进程永不退出）
#   anyio 的 WorkerThread（.venv/.../anyio/_backends/_asyncio.py:1045-1126）
#   **不是 daemon 线程**：第 1057 行 `super().__init__(name="AnyIO worker thread")`
#   未传 daemon=True，于是继承创建者（MainThread，daemon=False）的 daemon 状态。
#   它的 run() 阻塞在第 1089 行 `item = self.queue.get()`，等待 None 停机哨兵；
#   哨兵由第 2678 行 `root_task.add_done_callback(worker.stop)` 在 root task 结束
#   时投递（stop() 见第 1119-1126 行，行为 `self.queue.put_nowait(None)`）。
#   一旦哨兵没能投递（root task 已 done 再注册回调、或拥有它的 portal/loop 先被
#   关闭），worker 就永久卡在 queue.get()。CPython 退出时 `threading._shutdown()`
#   会 join 所有非 daemon 线程——于是 summary 打印完毕后进程挂死。
#
#   实测复现（纯观测、未打桩）：`pytest tests/test_api.py -q` 单独运行即卡死；
#   退出阶段 MainThread 栈 = threading.py:1590 in _shutdown，
#   被 join 的线程栈 = anyio/_backends/_asyncio.py:1089 in run（daemon=False）。
#
#   处理方式：只在**测试边界**补齐缺失的停机哨兵——记录 worker 实例，会话收尾时
#   对仍存活的 worker 投递 None。不改变任何被测行为，也不放宽任何断言。
_ANYIO_WORKERS: list = []


def _track_anyio_workers() -> None:
    """记录 anyio WorkerThread 实例（仅追加引用，不改其行为）。"""
    try:
        from anyio._backends import _asyncio as _anyio_asyncio
    except Exception:  # noqa: BLE001 anyio 缺席 -> 放弃追踪（不影响测试）
        return
    worker_cls = getattr(_anyio_asyncio, "WorkerThread", None)
    if worker_cls is None or getattr(worker_cls, "_aqp_tracked", False):
        return

    original_init = worker_cls.__init__

    def _tracking_init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        original_init(self, *args, **kwargs)
        _ANYIO_WORKERS.append(self)

    worker_cls.__init__ = _tracking_init
    worker_cls._aqp_tracked = True  # type: ignore[attr-defined]


def _drain_anyio_workers() -> None:
    """向仍存活的 anyio worker 投递 None 停机哨兵，使解释器能正常退出。"""
    import queue as _queue

    drained = 0
    for worker in list(_ANYIO_WORKERS):
        try:
            if not worker.is_alive():
                continue
            q = getattr(worker, "queue", None)  # anyio WorkerThread.queue
            if q is None:
                continue
            try:
                q.put_nowait(None)
            except _queue.Full:  # 队列满：腾一格再投递
                try:
                    q.get_nowait()
                except _queue.Empty:
                    pass
                q.put_nowait(None)
            drained += 1
        except Exception as e:  # noqa: BLE001 收尾阶段不因个别 worker 抛错而失败
            print(f"[conftest] anyio worker 收尾失败: {e!r}")
    if drained:
        print(f"[conftest] 已向 {drained} 个残留 anyio worker 投递停机哨兵")


def pytest_configure(config) -> None:  # noqa: ANN001
    """在收集测试之前重定向所有持久化路径到临时目录。"""
    global _TMP_ROOT

    # ---- cwd 无关的 import 路径（2026-09-14 修复）----
    # 本文件里的 import 依赖 `app` 包可导入，而 `app` 位于 backend/。此前只有
    # 「cwd=backend 且根目录无 conftest」时才恰好成立（pyproject/rootdir 不参与
    # sys.path）。从**项目根**跑 `backend/.venv/Scripts/python.exe -m pytest
    # backend/tests/...` 时 `-m` 把**项目根**（而非 backend）塞进 sys.path[0]，
    # 于是下方 `from app.db.init_db import init_database` 抛
    # `ModuleNotFoundError: No module named 'app'`，被 except 吞成一行
    # 「测试库初始化失败（继续收集）」—— 建表从未发生，随后所有用到 SQLite 的
    # 用例集体 `sqlite3.OperationalError: no such table: instrument`（实测 17 errors）。
    # 这里无条件补上 backend/（幂等：cwd=backend 时该路径本就在 sys.path 里，
    # insert 到最前不改变解析结果），使两种跑法结果一致。
    _backend_root = str(Path(__file__).resolve().parents[1])
    if _backend_root not in sys.path:
        sys.path.insert(0, _backend_root)

    # anyio worker 收尾追踪（与隔离无关，必须无条件启用，见文件顶部说明）
    _track_anyio_workers()

    # 允许显式关闭隔离（仅供需要真实环境的本地排查，CI 不应使用）
    if os.environ.get("AQP_TEST_NO_ISOLATE") == "1":
        return

    _TMP_ROOT = Path(tempfile.mkdtemp(prefix="aqp_test_"))
    data_root = _TMP_ROOT / "parquet"
    model_root = _TMP_ROOT / "models"
    log_dir = _TMP_ROOT / "logs"
    db_path = _TMP_ROOT / "sqlite" / "aqp_test.db"
    for p in (data_root, model_root, log_dir, db_path.parent):
        p.mkdir(parents=True, exist_ok=True)

    os.environ["DATA_ROOT"] = str(data_root)
    os.environ["MODEL_ROOT"] = str(model_root)
    os.environ["LOG_DIR"] = str(log_dir)
    os.environ["SQLITE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    # ---- 关掉 autoSync 的真实联网（测试边界隔离）----
    # sync_service.auto_sync_scheduler 在 app lifespan 中被启动为后台任务；它每轮
    # 调用 _load_auto_sync()，而后者在配置文件缺失时**默认**
    # {"enabled": True, "time": "15:45"}（见 app/services/sync_service.py）。
    # 于是只要本机墙钟 >= 15:45 且当日未自动跑过，测试会话就会触发**真实增量同步
    # （akshare 联网）**——这正是会话收尾打印 loguru「I/O operation on closed file」
    # + akshare 协程告警的根因（测试打真实网络，而非仅噪音）。
    # 配置路径 = DATA_ROOT.parent/".auto_sync.json"（sync_service._auto_sync_path）；
    # auto_sync_scheduler 的 while 循环内每轮都 `cfg = _load_auto_sync()` **重读该文件**
    # （非启动时缓存一次），故在路径隔离生效后写入 enabled=False 即对整个会话有效。
    # 只改测试隔离：生产默认仍为 enabled=True（是否改默认属产品决策，不在此处）。
    # 该文件名已被 .gitignore 忽略（**/.auto_sync.json），且落在会话临时目录，
    # 不会污染仓库、也不会被误提交。
    (data_root.parent / ".auto_sync.json").write_text(
        json.dumps({"enabled": False, "time": "15:45"}), encoding="utf-8")

    # 认证隔离：测试断言使用默认 ADMIN_TOKEN（见各 test 的 _ADMIN 头），
    # 但 config.py 会读真实 .env（生产随机 token）→ 测试必须显式钉回默认值，
    # 环境变量优先级高于 .env 文件（pydantic-settings 语义）。同时固定 JWT_SECRET，
    # 避免从 token 派生的密钥随 .env 变化导致已签 token 失效。
    # 测试环境关闭 overview 启动预热：预热会发起真实外部聚合（东财/新浪），
    # 每次 TestClient 启动 48s+ 且线程不可取消，既慢又会拖死退出
    os.environ.setdefault("WARM_OVERVIEW_ON_STARTUP", "0")
    # 测试环境强制关闭 Redis（覆盖本机可能运行的 aqp-redis）：套件全部按
    # 「进程内 LRU 兜底」路径设计与断言（from_cache 语义 / SWR 影子键 / 锁）。
    # 若放行真实 Redis：缓存跨 pytest 运行存活（影子键 TTL 2100s 会跨 run 命中
    # stale）、redis-py 异步连接跨事件循环复用会永久 pending——均为非隔离产物。
    os.environ["REDIS_ENABLED"] = "0"
    os.environ.setdefault("ADMIN_TOKEN", "aqp-dev-token-change-me")
    os.environ.setdefault("JWT_SECRET", "aqp-test-jwt-secret-not-for-prod")

    # 清缓存，确保后续 get_settings() 读到临时路径
    try:
        from app.core.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass
    try:
        from app.db.session import reset_engine

        reset_engine()
    except Exception:
        pass

    # 建表必须在**收集阶段之前**完成：部分测试用 class 作用域 fixture 触发训练，
    # 而 class 作用域先于 function 作用域夹具执行，若等到夹具里再建表，
    # 训练期间的 model_registry 写入会静默失败（旧代码只 warn 不阻断）。
    try:
        import asyncio

        from app.db.init_db import init_database
        from app.db.session import reset_engine as _reset

        asyncio.run(init_database())
        _reset()  # 丢弃本次临时 loop 绑定的引擎，交给测试自己的 loop 重建
    except Exception as e:  # 建表失败不能阻止收集，交由具体测试报错
        print(f"[conftest] 测试库初始化失败（继续收集）: {e!r}")


def pytest_unconfigure(config) -> None:  # noqa: ANN001
    """会话结束：恢复环境变量并清理临时目录。"""
    global _TMP_ROOT

    # 必须先于解释器退出：给残留 anyio worker 投递停机哨兵，否则
    # threading._shutdown() 会永久 join 它们，pytest 进程卡死（见文件顶部说明）
    _drain_anyio_workers()

    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    try:
        from app.core.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass
    try:
        from app.db.session import reset_engine

        reset_engine()
    except Exception:
        pass
    if _TMP_ROOT is not None and _TMP_ROOT.exists():
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
        _TMP_ROOT = None


@pytest.fixture(autouse=True)
async def _ensure_test_db():
    """兜底：确保 SQLite 可用（主初始化已在 pytest_configure 中完成）。

    保留此夹具是为了兼容直接 ``pytest tests/xxx.py`` 单文件运行之外的场景，
    以及在测试中显式 reset_engine() 之后能重建连接。
    """
    from app.core.config import get_settings

    s = get_settings()
    if not s.SQLITE_PATH.exists():
        from app.db.init_db import init_database

        await init_database()


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001
    """打印一行隔离信息，便于人工确认测试没有落到生产库。"""
    try:
        from app.core.config import get_settings

        s = get_settings()
        print(f"\n[conftest] 测试隔离：DATA_ROOT={s.DATA_ROOT}")
        print(f"[conftest] 测试隔离：SQLITE={s.SQLITE_PATH}")
    except Exception:
        pass
