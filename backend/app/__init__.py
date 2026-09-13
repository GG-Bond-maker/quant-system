"""AQP backend package.

启动时统一 CPU 线程配置：在任何 polars 导入之前设置 POLARS_MAX_THREADS，
与 LightGBM num_threads 共用 AQP_CPU_THREADS（见 core.config.effective_cpu_threads），
避免嵌套并行时的线程过载（审计报告 性能配置 项）。
"""
import os

__version__ = "1.0.0"


def _bootstrap_torch_dll_order() -> None:
    """Windows DLL 加载顺序守卫：torch 必须先于 pandas/scikit-learn 加载。

    根因（实测复现）：pandas / scikit-learn 先导入后，torch XPU 版的
    c10.dll 初始化失败 → OSError [WinError 1114]（DLL 初始化例程失败），
    训练 readiness 与训练端点全部 50000。而 torch-first 时任意顺序均正常。
    torch 是可选依赖：未安装时静默跳过，不影响 CPU-only 环境。
    """
    try:
        import torch  # noqa: F401  纯预热加载，建立 DLL 依赖的正确顺序
    except Exception:  # ImportError 或任何 DLL 加载失败——按未安装降级
        pass


def _bootstrap_threads() -> None:
    if "POLARS_MAX_THREADS" in os.environ:
        return  # 用户显式配置优先
    raw = os.environ.get("AQP_CPU_THREADS")
    if raw:
        threads = max(1, int(raw))
    else:
        cpu = os.cpu_count() or 4
        threads = max(1, min(cpu - 2, 12))
    os.environ["POLARS_MAX_THREADS"] = str(threads)


_bootstrap_torch_dll_order()
_bootstrap_threads()
