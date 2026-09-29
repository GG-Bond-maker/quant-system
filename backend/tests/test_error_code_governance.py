"""R9/S14 错误码治理守卫：**机制**上杜绝第 22 个裸码，并保证两端码表逐字对账。

## 缺陷

- `datacenter.py` 5 处、`backtest.py` 10 处 `fail(...)`/`AQPException(...)` 的第一实参是
  **裸整数字面量**（`4002/5000/4003/5001` 与 `40010~40017`），`core/errors.py` 的常量表
  与前端 `types/api.ts` 的 `ERR` 表**都不含**它们 ⇒ 前端只能拿到文案、**无法按码分类**，
  QA 也看不出"码表缺项"（P5 批次用 21 个读端点全量实测独立确认）。
- 同批还有三处语义错位：`sync/tasks/{id}` 用 `51001`（本地无数据）表"任务不存在"；
  `40017` 被三种不同语义共用（折数区间过短 / optuna 缺候选 / 寻优器 ValueError）。

## 修法与守卫

1. 15 个码全部登记进 `core/errors.py`（`40017` 按语义拆为 40017/40018/40019）；
   `alerts.py`/`screener.py` 里"值已登记但仍写字面量"的 5 处也换成常量；
2. `sync/tasks/{id}` 不存在改 `ERR_NOT_FOUND(40400)`；
3. **本文件的 AST 扫描**从机制上禁止任何裸整数字面量再出现；
4. **两端码表逐值对账**（后端常量集 == 前端 `ERR` 值集，双向），杜绝"单侧新增"；
5. 前端 `apiErrorMessage` 对未知码追加 `（未登记错误码 N）`（静态断言固化）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core import errors as err_mod  # noqa: E402

APP_DIR = BACKEND_ROOT / "app"
FRONTEND_TS = BACKEND_ROOT.parent / "frontend" / "src" / "types" / "api.ts"
FRONTEND_CLIENT = BACKEND_ROOT.parent / "frontend" / "src" / "api" / "client.ts"

# 允许不在 `errors.py` 里定义、但确实已登记的码名（当前为空，保留扩展位）
_EXTRA_REGISTERED_NAMES: set[str] = set()


def _backend_codes() -> dict[str, int]:
    """`core/errors.py` 的模块级 `ERR_* = <int>` 常量表（唯一事实源）。"""
    out: dict[str, int] = {}
    for node in ast.parse((BACKEND_ROOT / "app" / "core" / "errors.py").read_text("utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, int):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.startswith("ERR_"):
                    out[t.id] = node.value.value
    assert len(out) >= 25, f"常量表解析疑似失败，只解析到 {len(out)} 条"
    return out


def _frontend_codes() -> dict[str, int]:
    """前端 `ERR` 表（`NAME: 12345` 形式）。"""
    src = FRONTEND_TS.read_text("utf-8")
    body = src.split("export const ERR = {", 1)[1].split("} as const;", 1)[0]
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"(\w+):\s*(\d+)", body)}


def _code_call_sites(src_root: Path = APP_DIR) -> list[tuple[Path, int, ast.AST]]:
    """所有 `fail(...)` / `AQPException(...)` 的第一实参节点。"""
    sites: list[tuple[Path, int, ast.AST]] = []
    for py in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text("utf-8"))
        except SyntaxError as e:                      # 语法错误不属于本用例职责
            pytest.fail(f"{py} 无法解析：{e}")
        sites.extend((py, ln, a) for ln, a in _sites_in_tree(tree))
    assert sites, "没有扫到任何 fail/AQPException 调用点：扫描逻辑已失效（守卫会空转）"
    return sites


def _sites_in_tree(tree: ast.AST) -> list[tuple[int, ast.AST]]:
    """纯函数：从已解析的 AST 里取"码位实参"，便于用**合成源码**做变异反证。"""
    out: list[tuple[int, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if name in ("fail", "AQPException") and node.args:
            out.append((node.lineno, node.args[0]))
    return out


def _bare_int_values(src: str) -> list[int]:
    """合成源码 → 其中的裸码值（供变异反证）。"""
    return [a.value for _, a in _sites_in_tree(ast.parse(src))
            if isinstance(a, ast.Constant) and isinstance(a.value, int)]


# ---------------- 0. 先证明守卫**有判别力**（变异反证，不能空转） ----------------

@pytest.mark.parametrize("src, expected", [
    ("fail(4002, 'x')", [4002]),
    ("raise AQPException(40017, 'x')", [40017]),
    ("def f():\n    return fail( 5001 , 'x')", [5001]),
    ("fail(ERR_PARAMS, 'x')", []),
    ("raise AQPException(ERR_BT_MA_ORDER, 'x')", []),
    ("fail(code, 'x')", []),          # 变量形式不在本守卫职责内（由注册表对账覆盖）
    ("other_fail(4002, 'x')", []),    # 只认这两个调用名
])
def test_guard_detects_bare_codes_in_synthetic_source(src, expected):
    assert _bare_int_values(src) == expected


# ---------------- 1. 禁止裸码（机制守卫） ----------------

def test_no_bare_integer_code_as_first_argument():
    """任何 `fail(`/`AQPException(` 的第一实参都不得是整数字面量。

    这是"杜绝第 22 个裸码"的核心闸门：新增裸码会在这里直接变红。
    """
    bad = [(str(p.relative_to(BACKEND_ROOT)), ln, a.value)
           for p, ln, a in _code_call_sites()
           if isinstance(a, ast.Constant) and isinstance(a.value, int)]
    assert not bad, f"发现裸错误码（必须改用 core.errors 常量）：{bad}"


def test_every_named_code_exists_in_registry():
    """第一实参若是 `ERR_*` 名字，它必须在 `errors.py` 中真实存在（防拼写错误）。"""
    known = set(_backend_codes())
    unknown = [(str(p.relative_to(BACKEND_ROOT)), ln, a.id)
               for p, ln, a in _code_call_sites()
               if isinstance(a, ast.Name) and a.id.startswith("ERR_")
               and a.id not in known]
    assert not unknown, f"引用了未定义的错误码常量：{unknown}"


def test_the_fifteen_former_bare_codes_are_registered():
    """R9 点名的 15 个码（4002/5000/4003/5001 + 40010~40017）现状：
    前四个已收敛到已登记段，后八个（+拆分出的两个）必须已登记。

    这里固化"不再有码值落在两端码表之外"——按**码值**而不是按源码文本断言。
    """
    registered = set(_backend_codes().values())
    expected_registered = {40010, 40011, 40012, 40013, 40014, 40015,
                           40016, 40017, 40018, 40019}
    missing = sorted(expected_registered - registered)
    assert not missing, f"回测专用码未登记：{missing}"
    # 原先四个裸码必须**不再**作为码值存在（已归并到 40900/50000/51001）
    still_there = sorted({4002, 5000, 4003, 5001} & registered)
    assert not still_there, f"{still_there} 仍在码表中：应已归并到已登记段"


# ---------------- 2. 两端码表对账 ----------------

def test_backend_and_frontend_code_tables_match():
    """后端常量集与前端 `ERR` 表**逐值相等**（双向）。"""
    backend = _backend_codes()
    frontend = _frontend_codes()
    b_vals, f_vals = set(backend.values()), set(frontend.values())
    assert b_vals == f_vals, {
        "仅后端有": sorted(b_vals - f_vals),
        "仅前端有": sorted(f_vals - b_vals),
    }
    # 名字也要能对上（后端 `ERR_X` ↔ 前端 `X`），否则"逐字对账"名不副实
    b_names = {n[len("ERR_"):] for n in backend}
    f_names = set(frontend)
    assert b_names == f_names, {
        "仅后端有的名字": sorted(b_names - f_names),
        "仅前端有的名字": sorted(f_names - b_names),
    }


def test_frontend_unknown_code_is_marked():
    """前端对未知码必须显式标记（静态断言；前端无测试框架，此处读源码固化）。"""
    src = FRONTEND_CLIENT.read_text("utf-8")
    assert "未登记错误码" in src, "`apiErrorMessage` 缺少未知码标记"
    assert "REGISTERED_CODES" in src and "Object.values(ERR)" in src, (
        "未知码判据必须来自 ERR 码表，而不是另一个手写清单")


# ---------------- 3. 行为断言 ----------------

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}


def test_sync_task_not_found_uses_not_found_code():
    """`GET /sync/tasks/{id}` 不存在 ⇒ `ERR_NOT_FOUND(40400)`（原为 51001）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/datacenter/sync/tasks/definitely-missing-task",
                  headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == err_mod.ERR_NOT_FOUND, body
    assert body["data"] == {"task_id": "definitely-missing-task"}


def test_backtest_param_errors_use_registered_codes():
    """回测的参数错误回各自登记的码（短均线 ≥ 长均线 ⇒ `40014`）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/v1/backtest/strategy-run",
                   json={"symbols": ["600519.SH"], "start": "2026-01-01",
                         "end": "2026-03-01", "strategy_type": "ma_cross",
                         "short_ma": 30, "long_ma": 5},
                   headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == err_mod.ERR_BT_MA_ORDER, body