"""torch 计算设备探测（单一真源，供 torch_models / gnn_models / train_service 共用）。

优先级：xpu（Intel Arc iGPU/dGPU，PyTorch 2.6+ 原生支持，Ultra 125H 核显即此路径）
→ cpu 兜底。torch 未安装时一律返回 "cpu"（调用方应先过 torch_ready() 门禁），
驱动异常等任何探测失败都安全回退 cpu，绝不因设备探测把训练入口弄崩。
"""
from __future__ import annotations


def resolve_torch_device() -> str:
    """返回最佳可用计算设备：'xpu' 或 'cpu'（torch 未安装返回 'cpu'）。"""
    try:
        import torch
    except ImportError:
        return "cpu"
    try:
        if getattr(torch, "xpu", None) is not None and torch.xpu.is_available():
            return "xpu"
    except Exception:  # noqa: BLE001 驱动/运行时异常一律回退 CPU
        pass
    return "cpu"


def torch_device_info() -> dict:
    """设备信息快照（readiness 端点与前端 TrainPanel 展示用）。"""
    try:
        import torch
    except ImportError:
        return {"torch_installed": False, "device": None,
                "device_name": None, "torch_version": None}
    device = resolve_torch_device()
    name = None
    if device == "xpu":
        try:
            name = torch.xpu.get_device_name(0)
        except Exception:  # noqa: BLE001 拿不到名字不阻塞
            name = "Intel XPU"
    return {"torch_installed": True, "device": device,
            "device_name": name, "torch_version": torch.__version__}
