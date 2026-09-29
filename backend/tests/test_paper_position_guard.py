"""P1-3 防回归：模拟盘**持仓校验**——裸卖不得凭空造现金。

## 缺陷（2026-09-18 审核 P1-3，父审核员本轮独立复核为**属实**）

`app/trading/paper.py` 的卖出路径三层都没有持仓校验：

| 层 | 位置 | 缺什么 |
|---|---|---|
| 下单 | `place_order:128-134` | 只查 kill switch / 禁买池 / 参数，**不看持仓** |
| 撮合 | `run_fills:220-233` | 按参与率闸门算 `amt` 后直接成交，**不看持仓** |
| 记账 | `account_summary:341` | `cash += f.amount - fee`，**不看持仓** |

后果：卖 10 万元（`order_amount` 口径）在零持仓下照常撮合 ⇒ 权益
`1,000,000 → 1,098,871.19`，凭空造出现金。

## 修法（三层）

1. **下单期**：按 `decision_price` 折算股数，超过可用持仓 ⇒ 拒绝且**不落单**
   （与既有 `kill_switch` / `合规禁买` 两条拒绝路径同形，返回 `ok=False` + reason）。
2. **撮合期**（兜底，防"下单后持仓被别的单卖掉"）：把卖出金额**按当时实际持仓
   截断**；持仓为 0 ⇒ 母单置 `REJECTED` + 写 `reject_reason`（模型本就有这两个字段，
   但**全仓从无写入方**——这正是它存在的理由）。
3. **记账期**：`account_summary` 仍是"对已记录成交的纯推导"（单一事实来源，不改数
   字），但对**历史上的越卖成交**显式给出 `integrity_warnings`，使既成损害**可见**
   而不是被静默当成真实权益。

> 审核原文"`paper.py` **无任何测试文件**"不准确：`tests/test_production.py:143-198`
> 已有 `TestPaperDesk` 4 例（kill switch / 禁买 / 完整生命周期 / 撤单）。本文件是针对
> 持仓校验的**独立**防回归面。
"""
from __future__ import annotations

import sqlalchemy as sa

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

SYM = "000001.SZ"


@pytest.fixture()
def paper_env(tmp_path, monkeypatch):
    """独立 sqlite + 伪造日行情（与 `tests/test_production.py::paper_env` 同约定）。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("SQLITE_URL",
                       f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("MODEL_ROOT", str(tmp_path / "models"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.trading import paper as paper_mod

    monkeypatch.setattr(paper_mod, "_SYNC_ENGINE", None)
    import app.db.models  # noqa: F401  确保 ORM 注册进 metadata
    from app.db.session import Base

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    rng = np.random.default_rng(8)
    anchor = date.today() - timedelta(days=7)
    days = [anchor + timedelta(days=i) for i in range(20)
            if (anchor + timedelta(days=i)).weekday() < 5]
    px = 10 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    df = pl.DataFrame({"date": days, "open": px * 0.999, "high": px * 1.01,
                       "low": px * 0.99, "close": px,
                       "volume": [1e7] * len(days), "amount": px * 1e7})
    sym_dir = tmp_path / "data" / "daily_bar" / f"symbol={SYM}"
    sym_dir.mkdir(parents=True)
    df.write_parquet(sym_dir / "year=2026.snappy.parquet")
    get_settings.cache_clear()
    yield tmp_path


def _buy(s, amount: float = 500_000.0) -> dict:
    from app.trading import paper

    res = paper.place_order(s, symbol=SYM, side="buy", order_amount=amount,
                            algo="market", split_days=1, participation_cap=0.05)
    assert res["ok"], res
    paper.run_fills(s)
    s.commit()
    return res


def test_naked_sell_is_rejected_and_creates_no_order(paper_env):
    """零持仓卖出 ⇒ 下单期拒绝，且**不落单**（不是落单后静默作废）。"""
    from app.trading import paper

    Session = paper.sync_session_factory()
    with Session() as s:
        res = paper.place_order(s, symbol=SYM, side="sell", order_amount=100_000,
                                algo="market", split_days=1, participation_cap=0.05)
        s.commit()
        assert res["ok"] is False, "零持仓卖出必须被拒绝"
        assert "持仓" in res["reason"], f"拒绝原因须说明持仓不足：{res['reason']}"
        assert s.query(paper.PaperOrder).count() == 0, "被拒绝的下单不得落库"


def test_sell_within_position_is_allowed(paper_env):
    """反向断言：持仓充足时卖出必须放行（防"一律拒绝"式过度修复）。"""
    from app.trading import paper

    Session = paper.sync_session_factory()
    with Session() as s:
        _buy(s)
        acc = paper.account_summary(s)
        held = acc["positions"][SYM]["qty"]
        assert held > 0
        px = acc["positions"][SYM]["last_price"]
        cash_before_sell = acc["cash"]
        res = paper.place_order(s, symbol=SYM, side="sell",
                                order_amount=held * px * 0.5, algo="market",
                                split_days=1, participation_cap=0.05)
        assert res["ok"] is True, res
        fr = paper.run_fills(s)
        s.commit()
        assert fr["fills_created"] >= 1
        acc2 = paper.account_summary(s)
        assert acc2["positions"].get(SYM, {}).get("qty", 0) < held
        # ⚠️ 不可断言"权益 < 初始资金"：合成行情有漂移，浮盈会让权益合法高于初始
        # 资金（本用例第一版就这么写错，被自己的反向断言抓住）。只断言**不变量**：
        # 卖出后现金增加，且未成交出超过持仓的股数。
        assert acc2["cash"] > cash_before_sell, "合法卖出应使现金增加"
        sold = sum(f.qty for f in s.query(paper.PaperFill).all()
                   if f.side == "sell")
        assert sold <= held, f"卖出 {sold} 股超出持仓 {held} 股"


def test_sell_beyond_position_is_rejected_at_placement(paper_env):
    """持仓不足（不是零）也要拒绝，并给出**可卖股数**便于用户改单。"""
    from app.trading import paper

    Session = paper.sync_session_factory()
    with Session() as s:
        _buy(s)
        held = paper.account_summary(s)["positions"][SYM]["qty"]
        res = paper.place_order(s, symbol=SYM, side="sell",
                                order_amount=1e9, algo="market",
                                split_days=1, participation_cap=0.05)
        assert res["ok"] is False, "超持仓卖出必须被拒绝"
        assert "持仓" in res["reason"]
        assert str(held) in res["reason"], (
            f"拒绝原因应含可卖股数 {held}：{res['reason']}")


def test_fill_time_guard_blocks_phantom_cash(paper_env):
    """**核心红队路径**：绕过下单期校验（直接插入 PENDING 卖单）也不得造出现金。

    场景即"下单后、撮合前持仓被别的单卖掉"——此时下单期校验已通过，
    只能由撮合期兜底。
    """
    from app.trading import paper

    Session = paper.sync_session_factory()
    with Session() as s:
        s.add(paper.PaperOrder(symbol=SYM, side="sell", algo="market",
                               order_amount=100_000.0, split_days=1,
                               participation_cap=0.05, decision_price=10.0,
                               status="PENDING"))
        s.commit()
        fr = paper.run_fills(s)
        s.commit()
        fills = s.query(paper.PaperFill).all()
        assert fills == [], f"零持仓卖单不得产生成交：{[(f.side, f.qty) for f in fills]}"
        order = s.query(paper.PaperOrder).one()
        assert order.status == "REJECTED", f"应置终态 REJECTED，实为 {order.status}"
        assert order.reject_reason, "REJECTED 必须写明原因"
        assert fr.get("rejected_orders", 0) == 1, f"应回报拒绝计数：{fr}"
        acc = paper.account_summary(s)
        assert acc["equity"] == pytest.approx(paper.INIT_CASH, abs=1e-6), (
            f"权益被凭空推高：{acc['equity']} > {paper.INIT_CASH}")


def test_partial_position_sell_is_capped_not_phantom(paper_env):
    """持仓小于订单量时按实际持仓截断，剩余留存为 PART_FILLED 而非造假。"""
    from app.trading import paper

    Session = paper.sync_session_factory()
    with Session() as s:
        _buy(s, amount=100_000.0)
        held = paper.account_summary(s)["positions"][SYM]["qty"]
        assert held > 0
        # 直接插入"要卖 10 倍持仓"的卖单，绕过下单期校验
        s.add(paper.PaperOrder(symbol=SYM, side="sell", algo="market",
                               order_amount=held * 10 * 10.0, split_days=1,
                               participation_cap=1.0, decision_price=10.0,
                               status="PENDING"))
        s.commit()
        paper.run_fills(s)
        s.commit()
        sold = sum(f.qty for f in s.query(paper.PaperFill).all()
                   if f.side == "sell")
        assert sold <= held, f"卖出股数 {sold} 不得超过持仓 {held}"
        # 截断到整手 ⇒ 卖出量应恰好等于持仓（不足 1 手时允许更少）
        assert sold == held or held - sold < 100, (
            f"应截断为实际持仓 {held} 股，实卖 {sold} 股")
        acc = paper.account_summary(s)
        assert acc["positions"].get(SYM, {}).get("qty", 0) >= 0
        # 不变量：截断卖出后持仓归零 ⇒ 权益必须等于现金（无幽灵市值）
        assert acc["equity"] == pytest.approx(acc["cash"], abs=1e-6)


def test_legacy_oversell_fill_is_disclosed_not_silent(paper_env):
    """**既成损害必须可见**：历史库里若已存在越卖成交，记账层给出告警。

    数字仍是对"已记录成交"的纯推导（不篡改事实），但不得静默当成真实权益。
    """
    from app.trading import paper

    Session = paper.sync_session_factory()
    with Session() as s:
        s.add(paper.PaperFill(order_id=1, symbol=SYM, side="sell",
                              exec_date=date.today() - timedelta(days=5),
                              qty=10_000, price=10.0, amount=100_000.0,
                              fee=5.0, impact_bps=0.0, participation=0.0,
                              basis_bps=0.0))
        s.commit()
        acc = paper.account_summary(s)
        assert acc["integrity_warnings"], (
            "存在越卖成交却无任何告警 ⇒ 虚高权益被静默呈现")
        assert any(SYM in w for w in acc["integrity_warnings"]), acc["integrity_warnings"]