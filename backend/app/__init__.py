"""AQP backend package.

启动时统一 CPU 线程配置：在任何 polars 导入之前设置 POLARS_MAX_THREADS，
避免嵌套并行时的线程过载（审计报告 性能配置 项）。

默认公式为 ``max(1, min(cpu // 3, 8))``（本机 18 核 ⇒ 6）。原公式
``min(cpu - 2, 12)`` 在本机给到 12，而事件循环默认 ThreadPoolExecutor 宽至
``min(32, cpu+4)`` = 22；多个重扫任务并发时 22 × 12 ≈ 264 线程争抢磁盘与
CPU（嵌套并行过载）——实测 4 路并发冷扫把单次 29.3s 劣化为 60.9~63.0s。
下调为 cpu//3 且上限 8，令「并发任务数 × 单任务线程数」回到核数附近。

优先级：显式 ``POLARS_MAX_THREADS``（直接短路）> ``AQP_CPU_THREADS`` 覆盖 >
上述默认公式。注意 LightGBM/torch 的 num_threads 走
``core.config.effective_cpu_threads``：它与本模块**共用 AQP_CPU_THREADS 覆盖**，
但**默认公式各自独立**（未设置 AQP_CPU_THREADS 时不再与本模块同值），
故本次下调默认值**不影响 LGBM 训练默认线程数**，详见该函数 docstring。
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
        # 旧值 min(cpu - 2, 12)：本机 18 核 ⇒ 12，与事件循环 22 宽线程池相乘
        # 造成嵌套并行过载（见模块 docstring 实测数据）。改为 cpu//3 且上限 8。
        threads = max(1, min(cpu // 3, 8))
    os.environ["POLARS_MAX_THREADS"] = str(threads)


_bootstrap_torch_dll_order()
_bootstrap_threads()
