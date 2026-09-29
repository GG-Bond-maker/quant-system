"""时序深度模型训练脚手架（3-1 TFT/iTransformer 前置，PyTorch XPU/CPU 自适应）。

依赖 torch（**可选**，未安装时 torch_ready()=False，全部入口给出明确提示；
安装：CPU 版 pip install torch --index-url https://download.pytorch.org/whl/cpu
Intel Arc 核显/独显版 pip install torch --index-url https://download.pytorch.org/whl/xpu）。
设备选择：resolve_torch_device() 单一真源——xpu 可用则用 xpu，否则 cpu，
训练结束模型统一搬回 cpu（存盘/推理路径无需感知设备）。
线程纪律：torch.set_num_threads(effective_cpu_threads())。该值与 LightGBM
num_threads 同源，且两者都受 AQP_CPU_THREADS 显式覆盖支配；但**不与 polars
共用默认值**——polars 默认（app/__init__::_bootstrap_threads）为
max(1, min(cpu // 3, 8))，torch/LGBM 默认为 max(1, min(cpu - 2, 12))，
两者刻意分叉（并发热路径压低、串行训练用足核心，见 effective_cpu_threads docstring）。

模型：轻量 Transformer 编码器（输入 (B,L,F) → 线性嵌入 d_model →
TransformerEncoder → 均值池化 → 标量回归）。这是"扩池后可训练"的基线
骨架，不是完整 TFT（概率预测/多目标留待扩池后引入 pytorch-forecasting）。
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .torch_device import resolve_torch_device


def torch_ready() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _require_torch():
    if not torch_ready():
        raise RuntimeError(
            "未安装 PyTorch（可选依赖）。CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu；"
            "Intel Arc 核显/独显版："
            "pip install torch --index-url https://download.pytorch.org/whl/xpu")


def build_model(n_features: int, lookback: int, d_model: int = 64,
                nhead: int = 4, num_layers: int = 2, dim_ff: int = 128):
    """构建 SeqTransformer（每层带残差/LN 由 nn.TransformerEncoder 自带）。

    注意 pos 编码长度 = lookback：推理输入序列长度必须 <= 训练时 lookback
    （位置表按前缀切片），更长输入会广播失败——load 后用 predict_sequence
    传同长度序列即可。
    """
    import torch
    import torch.nn as nn

    class SeqTransformer(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.embed = nn.Linear(n_features, d_model)
            self.pos = nn.Parameter(torch.zeros(1, lookback, d_model))
            layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead, dim_feedforward=dim_ff,
                dropout=0.1, batch_first=True)
            self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
            self.head = nn.Linear(d_model, 1)

        def forward(self, x):  # (B,L,F) -> (B,)
            h = self.encoder(self.embed(x) + self.pos[:, :x.size(1)])
            return self.head(h.mean(dim=1)).squeeze(-1)

    return SeqTransformer()


def save_sequence_model(model, path) -> None:
    """连同重建所需的 n_features/lookback/d_model 一起落盘。

    模型类是 build_model 的局部类，只存 state_dict 无法重建——不存结构参数
    的话，promote 之后的 model.pt 就只是一个打不开的权重包。
    """
    import torch
    from pathlib import Path

    embed_w = dict(model.named_parameters())["embed.weight"]
    pos = dict(model.named_parameters())["pos"]
    torch.save({"n_features": int(embed_w.shape[1]),
                "lookback": int(pos.shape[1]),
                "d_model": int(pos.shape[2]),
                "state_dict": model.state_dict()}, Path(path))


def load_sequence_model(path):
    """从 save_sequence_model 产物重建 SeqTransformer 并载入权重（eval 模式）。"""
    import torch

    ckpt = torch.load(str(path), map_location="cpu")
    model = build_model(ckpt["n_features"], ckpt["lookback"],
                        d_model=ckpt.get("d_model", 64))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model


def train_sequence_model(
    X_train: np.ndarray, y_train: np.ndarray,
    X_valid: np.ndarray | None = None, y_valid: np.ndarray | None = None,
    *, epochs: int = 50, batch_size: int = 256, lr: float = 1e-3,
    patience: int = 5, seed: int = 42,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[object, list[dict]]:
    """训练并返回 (model, history)；早停看 valid MSE（无 valid 用 train）。

    should_stop：逐 epoch 检查的取消回调（True=请求中止）；中止当轮记录
    cancelled=True 后返回已训练到的状态，是否采纳由调用方判断。
    """
    _require_torch()
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    from ..core.config import effective_cpu_threads

    torch.manual_seed(seed)
    torch.set_num_threads(effective_cpu_threads())
    # 设备自适应：xpu 可用则用 Intel Arc 核显/独显，否则 cpu；history 首条披露
    device = resolve_torch_device()
    model = build_model(X_train.shape[2], X_train.shape[1]).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = torch.nn.MSELoss()

    def loader(X, y, shuffle):
        ds = TensorDataset(torch.tensor(X), torch.tensor(y))
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle)

    va_loader = (loader(X_valid, y_valid, False)
                 if X_valid is not None and len(X_valid) else None)
    history: list[dict] = []
    best_val, best_state, bad = float("inf"), None, 0
    for epoch in range(1, epochs + 1):
        if should_stop is not None and should_stop():
            history.append({"epoch": epoch, "cancelled": True})
            break
        model.train()
        tr_losses = []
        for xb, yb in loader(X_train, y_train, True):
            opt.zero_grad()
            loss = loss_fn(model(xb.to(device)), yb.to(device))
            loss.backward()
            opt.step()
            tr_losses.append(float(loss))
        rec: dict = {"epoch": epoch, "train_mse": round(np.mean(tr_losses), 6),
                     "device": device}
        if va_loader is not None:
            model.eval()
            va = []
            with torch.no_grad():
                for xb, yb in va_loader:
                    va.append(float(loss_fn(model(xb.to(device)), yb.to(device))))
            rec["valid_mse"] = round(float(np.mean(va)), 6)
            if rec["valid_mse"] < best_val - 1e-6:
                best_val, bad = rec["valid_mse"], 0
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            else:
                bad += 1
            if bad >= patience:
                rec["early_stop"] = True
                history.append(rec)
                break
        history.append(rec)
    if best_state is not None:
        model.load_state_dict(best_state)
    # 统一搬回 CPU：存盘（state_dict 落盘）与后续 predict_sequence/回读路径
    # 均按 CPU 假设，训练完的模型不残留 XPU 张量
    model = model.to("cpu")
    return model, history


def predict_sequence(model, X: np.ndarray, batch_size: int = 1024) -> np.ndarray:
    """批量推理 → (N,) float。"""
    _require_torch()
    import torch

    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), batch_size):
            xb = torch.tensor(X[i:i + batch_size])
            out.append(model(xb).numpy())
    return np.concatenate(out) if out else np.empty(0, dtype=float)
