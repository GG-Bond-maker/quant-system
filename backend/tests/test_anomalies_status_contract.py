"""``_build_anomalies`` 的**状态口径**契约（2026-10-04 上线前全检残留项）。

缺陷：原实现用 ``"status": "ok" if items else "degraded"``，把两件性质不同的事
混成同一个 ``degraded``：

  1. **上游源不可用、根本没取到数** —— 这是"不可得"（应为 ``unavailable``）；
  2. **源正常、当日确实没有涨停股** —— 这是**正常结果**，却被标成 ``degraded``
     （**假降级**：把正常说成异常）。

前者会误导"今天没有异动"，后者会让监控把正常日当成故障日。两者都必须区分开，
并各自给出可读的 ``reason``。

本文件逐条钉住这三种状态。被测对象是 ``_build_anomalies`` 的**判定逻辑**，故对
``get_akshare`` / ``_safe_call`` 打桩属合法依赖注入（与 ``test_rt_payload_status_contract``
对 ``_build_rt`` 子块打桩同一口径）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.api.v1 import market as market_api


def _spot(pcts: list[float]) -> pd.DataFrame:
    """构造 ``stock_zh_a_spot_em`` 形状的最小快照（只含被测逻辑用到的三列）。"""
    return pd.DataFrame({
        "代码": [f"60000{i}.SH" for i in range(len(pcts))],
        "名称": [f"股票{i}" for i in range(len(pcts))],
        "涨跌幅": pcts,
    })


class _FakeAk:
    """``get_akshare()`` 的替身：只暴露 ``stock_zh_a_spot_em``。"""

    def __init__(self, spot: pd.DataFrame) -> None:
        self._spot = spot

    def stock_zh_a_spot_em(self) -> pd.DataFrame:
        return self._spot


def _patch_ak(monkeypatch, *, spot: pd.DataFrame | None, boom: Exception | None = None) -> None:
    """把 akshare 通道换成确定行为：正常返回 spot，或抛 boom。"""
    def _call(fn, *args, **kwargs):
        if boom is not None:
            raise boom
        return fn(*args, **kwargs)

    monkeypatch.setattr(market_api, "_safe_call", _call)
    monkeypatch.setattr(market_api, "get_akshare", lambda: _FakeAk(spot))


# ---------------- 情形 1：上游源不可用 ⇒ unavailable（不是"没有异动"） ----------------

@pytest.mark.parametrize("heat", [
    {"status": "ok", "source": "local"},      # heat 走了本地回退
    {"status": "degraded", "source": "em"},   # heat 本身降级
    {"status": "unavailable"},                # heat 不可得
    {},                                       # 空载荷
])
def test_upstream_unavailable_is_not_false_ok(monkeypatch, heat: dict) -> None:
    """heat 非 em 可用源 ⇒ ``unavailable`` + 明确 reason，且**绝不外呼** akshare。"""
    called: list[int] = []
    monkeypatch.setattr(market_api, "get_akshare",
                        lambda: called.append(1) or _FakeAk(_spot([11.0])))

    out = market_api._build_anomalies(heat)

    assert out["status"] == "unavailable"
    assert out["items"] == []
    assert "上游实时快照源" in out["reason"]
    # 关键：不可用时**不得**再去打一次东财（那正是把 rt 拖超时的原因）。
    assert called == [], "上游不可用时不应外呼 akshare"


# ---------------- 情形 2：源可用但扫描抛错 ⇒ degraded ----------------

def test_scan_failure_is_degraded_with_exception_type(monkeypatch) -> None:
    _patch_ak(monkeypatch, spot=None, boom=RuntimeError("upstream 500"))
    out = market_api._build_anomalies({"status": "ok", "source": "em"})
    assert out["status"] == "degraded"
    assert out["items"] == []
    assert "RuntimeError" in out["reason"], out["reason"]


# ---------------- 情形 3：源正常、无涨停 ⇒ **ok**（假降级回归守卫） ----------------

def test_no_limit_up_is_ok_not_degraded(monkeypatch) -> None:
    """当日没有涨停股是**正常结果**，必须 ``ok`` —— 这条就是假降级的回归守卫。"""
    _patch_ak(monkeypatch, spot=_spot([1.2, -0.8, 3.4]))
    out = market_api._build_anomalies({"status": "ok", "source": "em"})
    assert out["status"] == "ok", f"源正常且扫描成功时不应降级，实际 {out}"
    assert out["items"] == []
    assert out["reason"] and "无" in out["reason"]


# ---------------- 情形 4：源正常、有涨停 ⇒ ok + 名单 ----------------

def test_limit_up_items_returned(monkeypatch) -> None:
    _patch_ak(monkeypatch, spot=_spot([10.02, 1.0, 9.95]))
    out = market_api._build_anomalies({"status": "ok", "source": "em"})
    assert out["status"] == "ok"
    assert [it["code"] for it in out["items"]] == ["600000.SH", "600002.SH"]
    assert all(it["pct"] >= 9.8 for it in out["items"])
    assert out["reason"] is None
