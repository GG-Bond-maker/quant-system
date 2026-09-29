"""ETF 快照存档「旧口径假零」净化的回归锁。

## 背景

2026-09-28 ``aqp:etf:snap:history`` 被「清除缓存」误删，事后只能从 Redis AOF
恢复出 8 条**旧口径**存档。旧口径的 ``net_inflow_yi=0.0`` 有致命歧义：
**无法区分「真实零净流入」与「取数失败被写成 0」**。按本项目诚实性红线，
不可分辨的零必须如实置 ``null``（并写明原因），绝不能冒充真实指标；
非零旧值同样**不得**断言 ``status="ok"`` —— 来源不可考。

规则实现在 ``app/data/etf_snapshot_sanitize.py``（非一次性脚本），并由
``kpi_series._read_etf_snapshots`` 在读路径统一调用。

## 本文件验证

- 正向：旧口径 ``0.0`` ⇒ ``None`` + ``flow.status == "unavailable"``；
- 正向：旧口径非零 ⇒ 数值保留 + 口径标注（但**不**出现 ``status="ok"``）；
- 反向：**任何**零形态（``0.0`` / ``0`` / ``-0.0``）都不允许原样写回，
  并用 AST 锁住「模块里不存在把 0 写回来的字面量」；
- 幂等（读路径每次都跑）、新口径行零改动、null 行不编造 reason；
- 合并语义：同日以现有行为准，恢复行打 ``recovered`` 标记；
- 读路径集成：``_read_etf_snapshots`` 必须把假零净化掉。
"""
from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data import etf_snapshot_sanitize as S  # noqa: E402
from app.data import kpi_series as K  # noqa: E402

FIELD = S.FLOW_FIELD  # "net_inflow_yi"


def _row(date: str, net_inflow: Any, **extra: Any) -> dict:
    """构造一条旧口径存档（无 ``flow`` 子对象）。"""
    return {"date": date, "etf_count": 1679, "total_size_yi": 50078.8,
            "avg_pct": -0.4776, FIELD: net_inflow, "amount_yi": 4749.45, **extra}


# ---------------- 正向 ----------------
def test_legacy_zero_becomes_unavailable() -> None:
    """旧口径 0.0 ⇒ 置 null + flow.status="unavailable"，且写明无法区分。"""
    src = [_row("2026-09-23", 0.0)]
    original = copy.deepcopy(src)
    out = S.sanitize_etf_snapshots(src)

    assert len(out) == 1
    row = out[0]
    assert row[FIELD] is None, f"假零必须置 null: {row}"
    flow = row.get("flow")
    assert isinstance(flow, dict), f"必须补 flow 子对象: {row}"
    assert flow.get("status") == "unavailable", flow
    assert flow.get("net_inflow_yi") is None, flow
    assert "无法区分" in str(flow.get("reason")), f"reason 必须写明歧义: {flow}"
    # 其余指标不得被净化逻辑改动
    assert row["etf_count"] == 1679 and row["amount_yi"] == 4749.45, row
    # 纯函数：入参不被修改
    assert src == original, "sanitize 不得原地修改入参"


def test_legacy_nonzero_keeps_value_but_never_claims_ok() -> None:
    """旧口径非零（9.0）⇒ 数值保留，但只能用口径标注，不得断言 ok。"""
    out = S.sanitize_etf_snapshots([_row("2026-09-11", 9.0)])

    row = out[0]
    assert row[FIELD] == 9.0, f"真实数值必须保留: {row}"
    assert row.get("net_inflow_caliber") == S.LEGACY_FLOW_CALIBER, row
    assert str(row.get("net_inflow_note")), "必须给出说明"
    # 反向：不得出现任何 ok 断言
    assert "flow" not in row, f"非零旧值不应补 flow 子对象: {row}"
    dumped = json.dumps(row, ensure_ascii=False)
    assert '"ok"' not in dumped, f"旧口径不可考，不得标注 status=ok: {dumped}"


def test_new_caliber_rows_are_untouched() -> None:
    """新口径行（已有 flow 子对象）必须一个字节都不动。"""
    row = {"date": "2026-09-28", FIELD: None, "archived_post_close": True,
           "flow": {"status": "unavailable", "net_inflow_yi": None,
                    "reason": "数据源不可达"}}
    out = S.sanitize_etf_snapshots([row])
    assert out == [row], f"新口径行被改动: {out}"


def test_null_without_flow_is_not_given_a_fabricated_reason() -> None:
    """null 且无 flow ⇒ 原样保留（补 reason 等于替历史数据编造原因）。"""
    row = _row("2026-09-25", None)
    out = S.sanitize_etf_snapshots([row])
    assert out[0][FIELD] is None
    assert "flow" not in out[0], f"不得为 null 编造 flow 原因: {out[0]}"


def test_sanitize_is_idempotent() -> None:
    """读路径每次都跑 ⇒ 必须幂等（二次净化结果与一次完全相同）。"""
    src = [_row("2026-09-23", 0.0), _row("2026-09-11", 9.0), _row("2026-09-25", None)]
    once = S.sanitize_etf_snapshots(src)
    twice = S.sanitize_etf_snapshots(once)
    assert once == twice, "净化不幂等：读路径重复调用会持续改写存档"


# ---------------- 反向 ----------------
@pytest.mark.parametrize("zero", [0.0, 0, -0.0])
def test_no_zero_form_survives_sanitize(zero: float) -> None:
    """**反向**：任何零形态都不得原样写回。"""
    out = S.sanitize_etf_snapshots([_row("2026-09-24", zero)])
    value = out[0][FIELD]
    assert value is None, f"零值 {zero!r} 被原样写回: {out[0]}"
    assert not (isinstance(value, (int, float)) and not isinstance(value, bool)
                and value == 0), out[0]


def test_module_contains_no_zero_literal() -> None:
    """**反向（AST）**：净化模块里不得存在任何 0 / 0.0 字面量常量。

    缺陷的根因就是「把 0.0 当成有效值写回」。规则模块里根本不该出现零字面量
    —— 一旦出现，说明有人又加了一条「保留/回填 0」的分支。
    """
    path = BACKEND_ROOT / "app" / "data" / "etf_snapshot_sanitize.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def _is_zero(node: ast.AST) -> bool:
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
                and node.value == 0)

    # 判定「这是零吗」的**比较**是净化本身所需，允许出现（value == 0）；
    # 除此之外的任何零字面量都只能是「把 0 写回去」的分支 ⇒ 判红。
    detection_zeros = {
        id(sub)
        for node in ast.walk(tree) if isinstance(node, ast.Compare)
        for sub in ast.walk(node)
    }
    offenders = [
        getattr(node, "lineno", "?")
        for node in ast.walk(tree)
        if _is_zero(node) and id(node) not in detection_zeros
    ]
    assert not offenders, f"净化模块出现零字面量（疑似回填 0 的分支）: {offenders}"


# ---------------- 合并语义 ----------------
def test_merge_prefers_existing_row_for_same_date() -> None:
    """同日以现有行为准（现有行更新、带新口径字段），并给恢复行打标。"""
    existing = [{"date": "2026-09-28", FIELD: None, "archived_post_close": True,
                 "flow": {"status": "unavailable", "net_inflow_yi": None,
                          "reason": "数据源不可达"}}]
    recovered = [
        _row("2026-09-23", 0.0),
        {"date": "2026-09-28", FIELD: 0.0, "etf_count": 1},  # 同日：必须被忽略
    ]
    merged = S.merge_etf_snapshots(existing, recovered, recovered_at="2026-09-29 01:00:00")

    assert [m["date"] for m in merged] == ["2026-09-23", "2026-09-28"], merged
    sep28 = next(m for m in merged if m["date"] == "2026-09-28")
    assert sep28["archived_post_close"] is True, "同日必须保留现有行"
    assert "recovered" not in sep28, "现有行不得被标为 recovered"
    sep23 = next(m for m in merged if m["date"] == "2026-09-23")
    assert sep23["recovered"] is True and sep23["recovered_at"] == "2026-09-29 01:00:00"


def test_build_restored_archive_has_no_fake_zero() -> None:
    """完整恢复产物（合并 + 净化）里不得残留任何假零。"""
    existing = [{"date": "2026-09-28", FIELD: None,
                 "flow": {"status": "unavailable", "net_inflow_yi": None, "reason": "x"}}]
    recovered = [_row("2026-09-11", 9.0), _row("2026-09-23", 0.0),
                 _row("2026-09-26", 0.0), _row("2026-09-27", 0.0)]
    out = S.build_restored_archive(existing, recovered)

    assert len(out) == 5, out
    zeros = [r["date"] for r in out
             if isinstance(r.get(FIELD), (int, float))
             and not isinstance(r.get(FIELD), bool) and r.get(FIELD) == 0]
    assert not zeros, f"恢复产物残留假零: {zeros}"
    # 周末行必须原样恢复（读取侧有交易日过滤，静默丢弃违反红线）
    assert {r["date"] for r in out} >= {"2026-09-26", "2026-09-27"}, out


# ---------------- 读路径集成 ----------------
def test_read_path_sanitizes_legacy_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_read_etf_snapshots`` 必须净化后再交给上层（假零绝不出库）。"""
    legacy = [_row("2026-09-23", 0.0), _row("2026-09-11", 9.0)]

    import orjson

    monkeypatch.setattr(K, "_redis_get_sync", lambda key: orjson.dumps(legacy))
    rows = K._read_etf_snapshots()

    assert len(rows) == 2, rows
    values = {r["date"]: r.get(FIELD) for r in rows}
    assert values["2026-09-23"] is None, f"读路径未净化假零: {values}"
    assert values["2026-09-11"] == 9.0, f"真实数值被误改: {values}"
    assert all(not (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0)
               for v in values.values()), values
