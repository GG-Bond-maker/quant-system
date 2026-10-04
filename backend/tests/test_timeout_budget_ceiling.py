"""超时预算**上限**（收紧方向）回归：``_PATH_MAX_BUDGETS`` + 与前端 ``CAPPED_PATHS`` 对齐。

## 背景（2026-10-04 上线前全检 T-1）

``core/timeout_guard.py`` 原先只有"**延长**"语义（``_PATH_BUDGETS``），
不变量只守了一个方向：**服务端预算必须 > 前端超时**（防"误杀"：
前端还在等、服务端已经回 504）。

但反方向从未设防：若服务端预算**远大于**前端超时，用户早已断开，
服务端还在烧 worker —— 而且 ``asyncio.to_thread`` 里的重算**不可取消**
（见 ``core/compute_pool.py`` 文件头），504 之后 CPU/IO 继续烧。

真实代价（日志实测，共 6 次）::

    backend-run-1003.log:180   GET /api/v1/settings             超时（>240s）→ 504
    backend-run-1003.log:1024  GET /api/v1/ops/lineage          超时（>240s）→ 504
    backend-run-1003.log:1043  GET /api/v1/ops/lineage          超时（>240s）→ 504
    backend-run-1003-r6.log:910 GET /api/v1/datacenter/mirror/status 超时（>240s）→ 504
    backend-run.log:235        GET /api/v1/datacenter/datasets 超时（>240s）→ 504
    backend-run.log:606        GET /api/v1/datacenter/datasets 超时（>240s）→ 504

这 4 条路径的前端超时只有 60s/60s/90s/120s ⇒ **用户 60s 就断了，服务端烧满 240s**，
且用户只拿到网络错误（不是带 ``code=50400`` 的结构化错误）。

## 本文件守什么

1. ``_PATH_MAX_BUDGETS`` 的每一条都**严格小于**前端 ``CAPPED_PATHS`` 的同路径超时；
2. 两张表**双向**一一对应（无孤儿）；
3. 两张表取值符合 ``前端超时 × CAPPED_BUDGET_RATIO``（当前 0.8）口径，
   留出余量避免"服务端刚回 504、前端也刚好超时"的竞态；
4. 上限表与延长表**键不相交**（一条路径要么被延长、要么被收紧）；
5. ``resolve_budget_seconds`` 的优先级：豁免 > 上限 > 延长 > 默认；
6. 上限命中时**真的**走中间件回 504（端到端，不只是查表）。

前端 ``timeouts.ts`` 是唯一真值来源 —— 这里**动态解析**它，不抄数字，
否则又会退化成"手工快照"（本文件要防的正是这个）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import ERR_REQUEST_TIMEOUT  # noqa: E402
from app.core.timeout_guard import (  # noqa: E402
    TimeoutGuardMiddleware,
    _EXEMPT_PATHS,
    _PATH_BUDGETS,
    _PATH_MAX_BUDGETS,
    resolve_budget_seconds,
)

PROJECT_ROOT = BACKEND_ROOT.parent
TIMEOUTS_TS = PROJECT_ROOT / "frontend" / "src" / "api" / "timeouts.ts"
GATE_MJS = PROJECT_ROOT / "frontend" / "scripts" / "check-timeout-consistency.mjs"


# ---------------- 解析前端真值（不抄数字） ----------------

def _load_frontend_registries() -> tuple[dict[str, int], dict[str, int], float]:
    """从 ``timeouts.ts`` 解析 ``(TIMEOUT 档位, CAPPED_PATHS, 收紧系数)``。

    与闸门 ``check-timeout-consistency.mjs::collectSources`` 用**同一套语法假设**
    （``'<路径>': TIMEOUT.<档位>`` / ``档位: 15_000``），但**独立实现** ——
    这样"闸门自己解析错了"不会同时骗过两边。
    """
    src = TIMEOUTS_TS.read_text(encoding="utf-8")

    tiers: dict[str, int] = {}
    for m in re.finditer(r"^\s{2}(\w+):\s*([\d_]+),", src, re.M):
        tiers[m.group(1)] = int(m.group(2).replace("_", ""))
    assert tiers, "未从 timeouts.ts 解析出任何 TIMEOUT 档位（语法变了？）"

    def _parse_table(name: str) -> dict[str, int]:
        block = re.search(
            rf"{name}[^{{]*\{{([\s\S]*?)\n\}};", src)
        assert block, f"未在 timeouts.ts 中找到 {name} 定义块"
        out: dict[str, int] = {}
        for m in re.finditer(r"'([^']+)':\s*TIMEOUT\.(\w+)", block.group(1)):
            tier = m.group(2)
            assert tier in tiers, f"{name}['{m.group(1)}'] 引用了不存在的档位 {tier}"
            out[m.group(1)] = tiers[tier]
        return out

    capped = _parse_table("CAPPED_PATHS")

    ratio_m = re.search(r"CAPPED_BUDGET_RATIO\s*=\s*([\d.]+)", src)
    assert ratio_m, "未在 timeouts.ts 中找到 CAPPED_BUDGET_RATIO"
    return tiers, capped, float(ratio_m.group(1))


_TIERS, _CAPPED, _RATIO = _load_frontend_registries()


# ---------------- 表结构与不变量 ----------------

def test_capped_paths_registered_on_both_sides():
    """两张表必须双向一一对应（无孤儿）。"""
    py = set(_PATH_MAX_BUDGETS)
    fe = set(_CAPPED)
    assert py == fe, (
        f"后端 _PATH_MAX_BUDGETS={sorted(py)} 与前端 CAPPED_PATHS={sorted(fe)} 不一致；"
        "只在一侧登记等于该侧静默失效"
    )
    assert py, "收紧表为空 —— 若确实不再需要收紧，请连同前端 CAPPED_PATHS 一起删除"


def test_capped_budget_is_strictly_below_frontend_timeout():
    """核心不变量：后端上限预算必须**严格小于**前端超时（否则用户仍先断）。"""
    for path, ms in _CAPPED.items():
        budget = _PATH_MAX_BUDGETS[path]
        assert budget < ms / 1000, (
            f"'{path}' 后端上限 {budget}s ≥ 前端超时 {ms}ms（{ms / 1000}s）—— "
            "没有真正收紧，用户仍会在服务端回 504 之前断开"
        )


def test_capped_budget_follows_ratio_convention():
    """取值口径：后端上限 = 前端超时 × 系数（留余量，避免两侧同时到点）。"""
    for path, ms in _CAPPED.items():
        expected = ms / 1000 * _RATIO
        budget = _PATH_MAX_BUDGETS[path]
        assert budget == pytest.approx(expected, abs=0.05), (
            f"'{path}' 后端上限 {budget}s 偏离口径 {_RATIO} × {ms / 1000}s "
            f"= {expected:.2f}s；改口径请同步 CAPPED_BUDGET_RATIO 与两侧表"
        )


def test_capped_and_extended_tables_are_disjoint():
    """一条路径不能同时被延长又被收紧（语义互斥，优先级会掩盖另一侧）。"""
    overlap = set(_PATH_MAX_BUDGETS) & set(_PATH_BUDGETS)
    assert not overlap, f"以下路径同时在延长表与上限表：{sorted(overlap)}"


def test_capped_budgets_are_below_default_budget():
    """收紧的意义就在于压到默认预算之下；否则这 4 条等于没登记。"""
    from app.core.timeout_guard import default_timeout_seconds

    default = default_timeout_seconds()
    for path, budget in _PATH_MAX_BUDGETS.items():
        assert budget < default, (
            f"'{path}' 上限 {budget}s ≥ 默认预算 {default}s ⇒ 收紧未生效"
        )


def test_capped_paths_are_not_exempt():
    """豁免路径不加任何超时，与"收紧"语义冲突。"""
    overlap = set(_PATH_MAX_BUDGETS) & set(_EXEMPT_PATHS)
    assert not overlap, f"以下路径既豁免又被收紧：{sorted(overlap)}"


# ---------------- 解析优先级 ----------------

def test_resolve_prefers_cap_over_extend_and_default():
    for path, budget in _PATH_MAX_BUDGETS.items():
        assert resolve_budget_seconds(path) == budget


def test_resolve_falls_back_to_default_for_unregistered_path():
    from app.core.timeout_guard import default_timeout_seconds

    assert resolve_budget_seconds("/api/v1/definitely/not/registered") == \
        default_timeout_seconds()


def test_resolve_returns_none_for_exempt_path():
    assert resolve_budget_seconds("/api/v1/notify/stream") is None


# ---------------- 端到端：上限命中真的回 504 ----------------

def test_capped_path_actually_returns_504_via_middleware():
    """端到端验证：一条被收紧的路径超时后回 **504 + code=50400**。

    这条用例的价值是防"表改了但 ``resolve_budget_seconds`` 没接上"——
    只查表会漏掉接线错误。直接驱动 ASGI（不经 TestClient，避免其自有超时干扰）。
    """
    import asyncio

    path = "/api/v1/settings"
    budget = _PATH_MAX_BUDGETS[path]

    async def _slow_app(scope, receive, send):  # noqa: ANN001
        await asyncio.sleep(budget + 30)          # 远超上限；被取消后不会真等
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    sent: list[dict] = []

    async def _send(message):  # noqa: ANN001
        sent.append(message)

    async def _receive():  # noqa: ANN001
        await asyncio.sleep(3600)                 # 没有请求体；不返回任何消息
        return {"type": "http.disconnect"}

    scope = {
        "type": "http", "method": "GET", "path": path,
        "headers": [], "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "client": ("testclient", 1234),
    }

    wrapped = TimeoutGuardMiddleware(_slow_app)

    async def _drive() -> None:
        await wrapped(scope, _receive, _send)

    asyncio.run(asyncio.wait_for(_drive(), timeout=budget + 20))

    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1, f"期望恰好 1 条 response.start，实际 {len(starts)}"
    assert starts[0]["status"] == 504, f"期望 504，实际 {starts[0]['status']}"
    body = b"".join(m.get("body", b"") for m in sent
                    if m["type"] == "http.response.body")
    assert f'"code":{ERR_REQUEST_TIMEOUT}'.encode() in body, (
        f"504 body 必须是统一信封且 code={ERR_REQUEST_TIMEOUT}，实际：{body[:200]!r}"
    )


# ---------------- 与 Node 闸门互证 ----------------

@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node（CI 前端 job 覆盖）")
def test_node_gate_passes_and_self_tests_are_sound():
    """跑真实闸门 + 证伪自测。

    只断言"通过"是不够的 —— 一个**永远通过**的闸门也能通过。故同时跑
    ``--self-test`` 并断言其证伪用例全部可被触发（否则闸门已空转）。
    """
    assert GATE_MJS.exists(), f"闸门脚本不存在：{GATE_MJS}"

    # ⚠️ 必须显式声明 utf-8：闸门输出含中文，Windows 默认用 GBK 解码会抛
    # UnicodeDecodeError（实测 0x85），并被吞成 PytestUnhandledThreadExceptionWarning。
    _kw = {"capture_output": True, "text": True, "encoding": "utf-8",
           "errors": "replace", "timeout": 120, "check": False,
           "cwd": str(GATE_MJS.parent.parent)}
    real = subprocess.run(["node", str(GATE_MJS)], **_kw)
    assert real.returncode == 0, (
        f"超时一致性闸门未通过：\nstdout={real.stdout}\nstderr={real.stderr}")

    trap = subprocess.run(["node", str(GATE_MJS), "--self-test"], **_kw)
    assert trap.returncode == 0, (
        f"闸门证伪自测未全通过（闸门可能已空转）：\n"
        f"stdout={trap.stdout}\nstderr={trap.stderr}")
    assert "❌" not in trap.stdout, f"存在未被触发的规则：\n{trap.stdout}"
