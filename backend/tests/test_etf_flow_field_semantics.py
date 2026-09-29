"""P1-14：ETF 资金流字段口径（`data/etf.py::fetch_etf_flow_history`）。

## 缺陷

东财 `fflow/kline` 的 `f52`~`f56` 是**净额**（主力/小单/中单/大单/超大单），
不是"流入/流出"配对。修复前代码按 in/out 解读：

```python
# f52=main_in, f53=main_out, ...          ← 错误
net = _num(parts[1]) or 0                  # 实际是主力净额
outflow = _num(parts[2]) or 0              # 实际是小单净额
"net_inflow": round(net - outflow, 2)      # 主力净额 − 小单净额
```

因四类净额（超大/大/中/小）之和为 0 ⇒ `小单净额 = −(主力净额 + 中单净额)`，
故错误值等价于 **`2×主力净额 + 中单净额`**：偏离随中单净额大小浮动（报告手算
既有样本 **+29.4%**），并在主力净额与中单净额异号时**符号可错**。

## 判据（红队用以确证字段语义的恒等式）

`f55 + f56 ≡ f52`（大单净额 + 超大单净额 = 主力净额）在真实样本上精确成立。
本文件据此构造自洽样本，先证明"修复前写法"与该恒等式**互相矛盾**，再断言修复后的
`net_inflow == f52` 且分项净额也能满足恒等式。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data import etf as etf_mod  # noqa: E402

# 一天的 15 列（f51..f65）；四类净额之和为 0，且 f55+f56 == f52（主力=大单+超大单）
_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"


def _kline(date: str, *, main: float, super_large: float, large: float,
           medium: float, small: float, close: float = 3.5) -> str:
    """按真实列序拼一行 kline（含净占比与收盘，占位 0/-）。"""
    main_ratio = main / 1e8
    return ",".join([date, f"{main}", f"{small}", f"{medium}", f"{large}",
                     f"{super_large}", f"{main_ratio}", "0", "0", "0", "0",
                     f"{close}", "0.5", "-", "-"])


def _patch_kline(monkeypatch, lines: list[str]) -> None:
    monkeypatch.setattr(etf_mod, "_request",
                        lambda *a, **k: {"data": {"klines": lines}})


# ---------------- 1. 正例：net_inflow 必须等于 f52（主力净额） ----------------

def test_net_inflow_is_main_net_f52(monkeypatch):
    """主力净流入 = f52 原值，**不做任何加减**。"""
    line = _kline("2026-09-18", main=2.4e8, super_large=1.9e8, large=0.5e8,
                  medium=-0.6e8, small=-1.8e8)
    # 自洽性：大单 + 超大单 = 主力；四类净额之和 = 0
    assert 1.9e8 + 0.5e8 == pytest.approx(2.4e8)
    assert 1.9e8 + 0.5e8 - 0.6e8 - 1.8e8 == pytest.approx(0.0)
    _patch_kline(monkeypatch, [line])

    out = etf_mod.fetch_etf_flow_history("510300")
    assert len(out) == 1
    item = out[0]
    assert item["date"] == "2026-09-18"
    assert item["net_inflow"] == pytest.approx(2.4e8), "主力净额被改动了"
    # 分项净额可独立核对"主力 = 大单 + 超大单"
    assert item["large_net"] + item["super_large_net"] == pytest.approx(
        item["net_inflow"])
    assert item["small_net"] == pytest.approx(-1.8e8)
    assert item["medium_net"] == pytest.approx(-0.6e8)


def test_old_reading_equals_2x_main_plus_medium(monkeypatch):
    """**缺陷本体反证**：修复前写法 ≡ `2×主力净额 + 中单净额`（偏差可推导）。

    推导（用得上"四类净额之和为 0"这一市场恒等式）：
    ``小单净额 = −(主力净额 + 中单净额)`` ⇒ ``旧值 = 主力 − 小单 = 2×主力 + 中单``。
    因此**偏差率 = (2×主力 + 中单)/主力 − 1 = 1 + 中单/主力**，随样本浮动 ——
    这就是为什么报告的 +29.4% 只能对应它观测的那份样本（隐含 中单/主力 = 29.4%），
    不能张冠李戴到别的样本上。
    """
    for main, medium in [(2.4e8, -0.6e8), (1.0e8, 3.0e7), (-1.0e8, 2.0e7)]:
        small = -(main + medium)                     # 恒等式：四类净额之和为 0
        large, super_large = main * 0.4, main * 0.6  # 恒等式：大单 + 超大单 = 主力
        line = _kline("2026-09-18", main=main, super_large=super_large,
                      large=large, medium=medium, small=small)
        _patch_kline(monkeypatch, [line])
        fixed = etf_mod.fetch_etf_flow_history("510300")[0]["net_inflow"]

        old = main - small
        assert old == pytest.approx(2 * main + medium)
        assert fixed == pytest.approx(main)
        assert old / fixed - 1 == pytest.approx(1 + medium / main)


def test_real_observed_sample_reproduces_reported_deviation(monkeypatch):
    """用 **B3b/红队观测到的真实样本**复算报告的 +29.4%（不是合成比值）。

    真实样本（`B3b-data.md:128-130`，东财 `fflow/kline` 某日）：

    ===================  ==================
    ``f52`` 主力净额     274,198,704
    ``f53`` 小单净额     −80,721,950
    ``f54`` 中单净额     −193,476,752
    ``f55`` 大单净额     65,674,576
    ``f56`` 超大单净额   208,524,128
    ===================  ==================

    恒等式核对：``f55+f56 = 274,198,704 = f52``（主力=大单+超大单）；
    ``f53+f54 = −274,198,702 ≈ −f52``（四类净额之和为 0，源数据四舍五入差 2 元）。
    旧写法 `f52 − f53 = 354,920,654` ⇒ 高估 **+29.4392%**（报告取证值）；
    新写法 = `f52 = 274,198,704`。
    """
    main, small = 274_198_704.0, -80_721_950.0
    medium, large, super_large = -193_476_752.0, 65_674_576.0, 208_524_128.0
    assert large + super_large == pytest.approx(main)           # f55 + f56 ≡ f52
    assert small + medium == pytest.approx(-main, abs=2.0)      # 四类净额之和为 0

    _patch_kline(monkeypatch, [_kline("2026-09-18", main=main, super_large=super_large,
                                      large=large, medium=medium, small=small)])
    fixed = etf_mod.fetch_etf_flow_history("510300")[0]["net_inflow"]

    old = main - small
    assert old == pytest.approx(354_920_654.0)                  # 报告中的"代码输出"
    assert fixed == pytest.approx(main)
    # 偏差率 = 1 + 中单/主力；源数据四舍五入（差 2 元）导致 1e-6 级差异，故容差 2e-3
    assert old / fixed - 1 == pytest.approx(29.4 / 100, abs=2e-3)


def test_old_reading_can_flip_sign(monkeypatch):
    """符号可错：主力净流出但中单大幅净流入（>2×|主力|）⇒ 旧写法给出**正**值。

    这正说明该缺陷不只是"数值偏大"——它能让"主力净流出"在页面上显示为"净流入"。
    """
    main2, medium2 = -1.0e8, 2.5e8              # 中单 > 2×|主力| ⇒ 旧值 = 2×主力+中单 > 0
    small2 = -(main2 + medium2)                 # = -1.5e8（小单净流出）
    assert small2 < 0
    _patch_kline(monkeypatch, [_kline("2026-09-17", main=main2, super_large=-0.6e8,
                                      large=-0.4e8, medium=medium2, small=small2)])
    item = etf_mod.fetch_etf_flow_history("510300")[0]
    assert item["net_inflow"] == pytest.approx(main2)
    assert item["net_inflow"] < 0, "主力净流出必须为负"
    assert 2 * main2 + medium2 > 0, "旧写法在同样样本上会给出**正**值（符号错）"


# ---------------- 2. 缺失值不再伪造成 0 ----------------

def test_missing_field_is_none_not_zero(monkeypatch):
    """源字段为 `-`（停牌/无数据）⇒ `None`；修复前 `or 0` 会伪造成 0。"""
    dash = "-"
    line = ",".join(["2026-09-18", dash, dash, dash, dash, dash, "0", "0", "0",
                     "0", "0", "3.5", "0", "-", "-"])
    _patch_kline(monkeypatch, [line])
    out = etf_mod.fetch_etf_flow_history("510300")
    assert out[0]["net_inflow"] is None
    assert out[0]["small_net"] is None


def test_short_or_malformed_rows_are_skipped(monkeypatch):
    """列数不足的行跳过（原判据 `len(parts) < 3` 会放行缺分项的行并算错）。"""
    good = _kline("2026-09-18", main=1e8, super_large=0.6e8, large=0.4e8,
                  medium=-0.3e8, small=-0.7e8)
    _patch_kline(monkeypatch, ["2026-09-17,1,2", good, ""])
    out = etf_mod.fetch_etf_flow_history("510300")
    assert len(out) == 1 and out[0]["date"] == "2026-09-18"


def test_empty_payload_returns_empty_list(monkeypatch):
    _patch_kline(monkeypatch, [])
    assert etf_mod.fetch_etf_flow_history("510300") == []


# ---------------- 3. 与 realtime 的正确解读保持同构（防再次分叉） ----------------

def test_semantics_match_realtime_reader(monkeypatch):
    """同一行 kline 分别喂给本函数与 `realtime._fetch_em_fflow`，主力净额必须一致。"""
    from app.data import realtime

    main, super_large, large, medium, small = -3.3e8, -2.1e8, -1.2e8, 1.0e8, 2.3e8
    line = _kline("2026-09-18", main=main, super_large=super_large, large=large,
                  medium=medium, small=small)
    _patch_kline(monkeypatch, [line])
    etf_item = etf_mod.fetch_etf_flow_history("510300")[0]

    monkeypatch.setattr(realtime, "_request",
                        lambda *a, **k: {"data": {"klines": [line]}})
    rt = realtime._fetch_em_fflow("https://push2his.eastmoney.com", "510300.SH")

    assert etf_item["net_inflow"] == pytest.approx(rt["main_net"]), \
        "ETF 与个股的主力净额解读再次分叉"
    assert etf_item["super_large_net"] == pytest.approx(rt["super_large_net"])
    assert etf_item["large_net"] == pytest.approx(rt["large_net"])
    assert etf_item["medium_net"] == pytest.approx(rt["medium_net"])
    assert etf_item["small_net"] == pytest.approx(rt["small_net"])


def test_source_comment_no_longer_claims_in_out_pairs():
    """静态断言：误导性注释（把 f52/f53 当 in/out）不得再出现。"""
    src = (BACKEND_ROOT / "app" / "data" / "etf.py").read_text("utf-8")
    assert "f52=main_in" not in src and "f53=main_out" not in src
    assert "f52``       **主力净额**" in src or "主力净额" in src