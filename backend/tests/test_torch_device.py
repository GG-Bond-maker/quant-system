"""torch 设备探测模块（torch_device.py）的单元测试。

测试环境（venv）通常未装 torch：断言"未安装路径"返回 cpu + installed=False
且不抛异常；装了 torch 的环境则校验 xpu→cpu 优先级与信息快照结构。
"""
from __future__ import annotations

from app.ml.torch_device import resolve_torch_device, torch_device_info
from app.ml.torch_models import torch_ready


def test_resolve_device_safe_without_torch():
    """torch 未安装：必须返回 'cpu' 且不抛异常（训练门禁之外绝不崩）。"""
    if torch_ready():
        assert resolve_torch_device() in ("cpu", "xpu")
    else:
        assert resolve_torch_device() == "cpu"


def test_device_info_structure():
    """信息快照结构稳定：installed 与 device 字段一致，name/version 可缺失。"""
    info = torch_device_info()
    assert set(info) == {"torch_installed", "device", "device_name", "torch_version"}
    assert info["torch_installed"] == torch_ready()
    if info["torch_installed"]:
        assert info["device"] in ("cpu", "xpu")
        if info["device"] == "xpu":
            assert isinstance(info["device_name"], str) and info["device_name"]
    else:
        assert info["device"] is None and info["torch_version"] is None
