"""图神经网络训练脚手架（3-2 前置，PyTorch XPU/CPU 自适应 + 裸 GCN 实现，无 PyG 依赖）。

依赖 torch（可选，见 torch_models.torch_ready）。模型：两层图卷积
H = σ(A·X·W)（行归一化 A，见 graph.build_adjacency），逐节点回归
前向收益——即"产业链传导"的最低成本可训练版本。
设备选择与 torch_models 同源：resolve_torch_device()，训练完搬回 cpu。

注意：当前无关系数据 + 120 只池，训练出来的东西不会有研究价值；
本模块存在的意义是数据到位后 `python -m scripts.train_gnn` 即可训练。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from .torch_device import resolve_torch_device
from .torch_models import torch_ready


def build_gcn(n_features: int, hidden: int = 32):
    """两层 GCN：(B,N,F) + A(N,N) → (B,N) 逐节点预测。"""
    import torch
    import torch.nn as nn

    class GCN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.w0 = nn.Linear(n_features, hidden)
            self.w1 = nn.Linear(hidden, 1)

        def forward(self, x, a):  # x:(B,N,F) a:(N,N)
            h = torch.relu(a @ self.w0(x))          # (B,N,H)
            return self.w1(a @ h).squeeze(-1)       # (B,N)

    return GCN()


def train_gnn(
    X: np.ndarray, y: np.ndarray, A: np.ndarray, *,
    epochs: int = 40, lr: float = 1e-3, seed: int = 42,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[object, list[dict]]:
    """X:(D,N,F) 逐日截面特征；y:(D,N) 前向收益；A:(N,N) 行归一化邻接。

    特征 NaN 一律按 0 填充（缺失特征不参与，由该节点的有效标签掩码兜底）——
    必须显式 nan_to_num：任一 NaN 进入 A·X·W 都会让整张图的前向/反向全变
    NaN，最终得到一个权重全是 NaN 的"静默废模型"，且训练日志的 loss 也
    会是 NaN 而无人察觉。填充比例如实计入 history[0] 供复核。
    """
    if not torch_ready():
        raise RuntimeError(
            "未安装 PyTorch（可选依赖）。CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu；"
            "Intel Arc 核显/独显版："
            "pip install torch --index-url https://download.pytorch.org/whl/xpu")
    import torch

    from ..core.config import effective_cpu_threads

    torch.manual_seed(seed)
    torch.set_num_threads(effective_cpu_threads())
    # 设备自适应：与 SeqTransformer 同源（xpu → cpu）；history 首条披露
    device = resolve_torch_device()

    nan_x = float(np.isnan(X).mean())
    nan_y = float(np.isnan(y).mean())
    model = build_gcn(X.shape[2]).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    at = torch.tensor(A, dtype=torch.float32, device=device)
    xt = torch.tensor(np.nan_to_num(X, nan=0.0), dtype=torch.float32, device=device)
    yt = torch.tensor(np.nan_to_num(y), dtype=torch.float32, device=device)
    mask = torch.tensor(np.isfinite(y), dtype=torch.float32, device=device)
    denom = float(mask.sum())
    if denom <= 0:
        raise ValueError("训练段无任何有效标签（y 全 NaN）——检查 horizon 与样本区间")
    # epoch 记录里既有 int/float 也有 str(bool 由 cancelled 带入)，统一为 Any 值
    history: list[dict[str, Any]] = []
    for epoch in range(1, epochs + 1):
        if should_stop is not None and should_stop():
            history.append({"epoch": epoch, "cancelled": True})
            break
        opt.zero_grad()
        pred = model(xt, at)
        loss = ((pred - yt) ** 2 * mask).sum() / denom
        loss.backward()
        opt.step()
        rec = {"epoch": epoch, "mse": round(float(loss), 6), "device": device}
        if epoch == 1:
            rec["nan_feature_ratio"] = round(nan_x, 4)
            rec["nan_label_ratio"] = round(nan_y, 4)
        history.append(rec)
    # 统一搬回 CPU：save_gnn / predict_gnn / 回读路径均按 CPU 假设
    model = model.to("cpu")
    return model, history


def predict_gnn(model, X: np.ndarray, A: np.ndarray) -> np.ndarray:
    import torch

    model.eval()
    with torch.no_grad():
        return model(torch.tensor(np.nan_to_num(X, nan=0.0), dtype=torch.float32),
                     torch.tensor(A, dtype=torch.float32)).numpy()


def save_gnn(model, path) -> None:
    """连同重建所需的 n_features/hidden 一起落盘（GCN 是局部类，无配置无法重建）。"""
    import torch
    from pathlib import Path

    w0 = dict(model.named_parameters())["w0.weight"]
    torch.save({"n_features": int(w0.shape[1]), "hidden": int(w0.shape[0]),
                "state_dict": model.state_dict()}, Path(path))


def load_gnn(path):
    """从 save_gnn 产物重建 GCN 并载入权重。"""
    import torch

    ckpt = torch.load(str(path), map_location="cpu")
    model = build_gcn(ckpt["n_features"], hidden=ckpt["hidden"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model
