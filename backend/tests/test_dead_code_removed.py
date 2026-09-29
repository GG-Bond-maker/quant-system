"""§8.2 第 17 项（死代码清理）的反向锁：删掉的东西不得复活，留下的东西不得误删。

背景：2026-09-22 清理了一批**零调用方**代码。清理的风险有两个方向——
① 死代码被后人"顺手加回来"（尤其是包一层 wrapper 的旧习惯）；
② 清理时**误删仍然活着**的函数（本轮独立复核就发现报告点名的
   `stamp_duty_rate` / `check_pct_limit` / `write_year_batch` 其实都在生产链路上）。
本文件同时锁这两侧：

- **负向锁**：被删符号在 `app/` 内**不得**再出现（源码扫描 + 属性断言）；
- **正向锁**：报告曾点名"疑似死代码"、但实测**仍活着**的符号必须继续存在且可调用。

每个断言都成对出现（删了 + 活着），避免"只锁一侧"造成的假安全感。
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _module(name: str):
    return importlib.import_module(name)


def _app_sources() -> list[Path]:
    return sorted(p for p in APP_ROOT.rglob("*.py"))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """模块/类/函数 docstring 的 Constant 节点 id（这些是说明文字，不算引用）。"""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _code_hits(symbol: str) -> list[str]:
    """在 `app/` 里按 **AST** 找对该符号的真实引用。

    不用子串匹配：`publish` 会被 `publish_threadsafe`/`_publish_now` 命中，
    `path_for` 会被 `path_for_year` 命中，docstring 里的说明文字也会被命中
    —— 这三种假阳性都会让"删干净了"这条锁失效。
    只认：Name/Attribute/import 名，以及 `getattr/setattr/hasattr` 的字符串参数。
    """
    hits: list[str] = []
    for p in _app_sources():
        text = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, str(p))
        except SyntaxError:  # pragma: no cover 语法错误由别的测试负责
            continue
        docs = _docstring_nodes(tree)
        parent: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[id(child)] = node
        for node in ast.walk(tree):
            matched = (
                (isinstance(node, ast.Name) and node.id == symbol)
                or (isinstance(node, ast.Attribute) and node.attr == symbol)
                or (isinstance(node, ast.alias) and node.name.split(".")[-1] == symbol)
                or (isinstance(node, ast.Constant) and node.value == symbol
                    and id(node) not in docs
                    and isinstance(parent.get(id(node)), ast.Call)
                    and isinstance(parent[parent[id(node)]].func, ast.Name)
                    and parent[parent[id(node)]].func.id in
                    ("getattr", "setattr", "hasattr", "delattr"))
            )
            if matched:
                rel = p.relative_to(APP_ROOT)
                line = text.splitlines()[node.lineno - 1].strip()
                hits.append(f"{rel}:{node.lineno}: {line[:90]}")
    return hits


# ---------------------------------------------------------------- 负向锁

@pytest.mark.parametrize("mod_name, gone, kept", [
    # 模块, 已删符号, 必须仍在的同模块符号
    ("app.data.ingest.dividends", "fetch_dividends", "ex_date_coverage"),
    ("app.data.ingest.dividends", "save_dividends", "ex_date_coverage"),
    ("app.data.ingest.dividends", "load_dividends_asof", "ex_date_coverage"),
    ("app.data.parquet_store", "path_for", "path_for_year"),
    ("app.data.parquet_store", "write_whole_symbol", "write_year_batch"),
    ("app.data.repair", "is_quarantined", "quarantined_symbols"),
    ("app.core.pipeline_lock", "current_owner", "current_pipeline_owner"),
    ("app.data.cross_section", "remove_mirror", "mirror_status"),
    ("app.data.panels", "recent_trade_date_str", "build_quote"),
    ("app.data.panels", "announcement_window", "build_quote"),
    ("app.data.panels", "code_of", "build_quote"),
    ("app.core.events", "publish", "publish_threadsafe"),
    ("app.ml.infer", "infer_day", "predict_with_contrib"),
    ("app.data.ingest.akshare_adapter", "fetch_daily_bar_batch", "fetch_daily_bar"),
    ("app.data.ingest.akshare_adapter", "fetch_daily_bar_batch_by_year",
     "fetch_daily_bar"),
])
def test_dead_symbol_removed_and_live_neighbour_kept(mod_name, gone, kept):
    mod = _module(mod_name)
    assert not hasattr(mod, gone), f"{mod_name}.{gone} 已删除，不得复活"
    assert hasattr(mod, kept), f"{mod_name}.{kept} 仍在生产链路，不得误删"


@pytest.mark.parametrize("mod_name, names", [
    ("app.data.ingest.dividends",
     ["fetch_dividends", "save_dividends", "load_dividends_asof"]),
    ("app.data.parquet_store", ["path_for", "write_whole_symbol"]),
    ("app.data.repair", ["is_quarantined"]),
    ("app.core.pipeline_lock", ["current_owner"]),
    ("app.data.cross_section", ["remove_mirror"]),
    ("app.data.panels",
     ["recent_trade_date_str", "announcement_window", "code_of"]),
    ("app.core.events", ["publish"]),
    ("app.ml.infer", ["infer_day"]),
    ("app.data.ingest.akshare_adapter",
     ["fetch_daily_bar_batch", "fetch_daily_bar_batch_by_year"]),
])
def test_no_reference_left_in_app_sources(mod_name, names):
    """源码级反向锁：`app/` 内不得再有任何**代码级**引用（AST 精确匹配）。"""
    for name in names:
        hits = _code_hits(name)
        assert not hits, f"{name} 仍有代码引用：\n" + "\n".join(hits)


def test_parquet_store_dunder_all_no_dead_entries():
    """`__all__` 不得再导出已删符号（否则 `from ... import *` 会拿到旧名）。"""
    ps = _module("app.data.parquet_store")
    for name in ("path_for", "write_whole_symbol"):
        assert name not in ps.__all__, f"__all__ 仍导出已删除的 {name}"
    for name in ("path_for_year", "write_year_batch", "write_partition",
                 "read_symbol_dataset"):
        assert name in ps.__all__


def test_metrics_dead_gauges_and_counters_removed():
    """7 个"定义了但从不写入"的指标必须消失（否则 /metrics 恒报 0，误导抓取方）。

    `REDIS_CIRCUIT_OPEN` / `REDIS_STATUS` 尤其危险：字符串 `redis_circuit_open`
    在 alerts/alerter 里另有含义（是 reason 文案，不是指标），容易被误判为"有写入"。
    """
    m = _module("app.core.metrics")
    for name in ("HTTP_ERRORS_TOTAL", "REDIS_STATUS", "REDIS_CIRCUIT_OPEN",
                 "PIPELINE_TOTAL", "PIPELINE_DURATION",
                 "MODEL_PREDICTION_TOTAL", "MODEL_PREDICTION_ERROR"):
        assert not hasattr(m, name), f"metrics.{name} 无写入点，不得复活"
    # 真有写入点的指标必须还在（否则 /metrics 会退化成空）
    for name in ("HTTP_REQUESTS_TOTAL", "HTTP_REQUEST_DURATION",
                 "PANIC_CONTAINED_TOTAL", "LOOP_PANIC_CONTAINED_TOTAL",
                 "OVERVIEW_CACHE_TOTAL"):
        assert hasattr(m, name), f"metrics.{name} 有写入点，不得误删"


def test_metrics_exposition_has_no_never_written_series():
    """端到端：/metrics 的 exposition 里不得出现已删指标名。"""
    from app.core.metrics import metrics_response

    body = metrics_response().body.decode("utf-8")
    for dead in ("aqp_http_errors_total", "aqp_redis_status",
                 "aqp_redis_circuit_open", "aqp_pipeline_total",
                 "aqp_pipeline_duration_seconds", "aqp_model_prediction_total",
                 "aqp_model_prediction_error_total"):
        assert dead not in body, f"{dead} 仍在 /metrics 输出中（恒 0 误导）"
    assert "aqp_http_requests_total" in body


# ---------------------------------------------------------------- 正向锁

def test_live_symbols_mistakenly_reported_as_dead_still_exist():
    """报告曾点名、**实测仍活着**的符号：锁住"不得误删"（本轮复核的结论）。"""
    rules = _module("app.domain.a_share_rules")
    assert callable(rules.stamp_duty_rate)
    assert callable(rules.effective_stamp_duty)
    assert callable(rules.effective_transfer_fee)
    assert rules.STAMP_DUTY_CUT_DATE is not None

    validate = _module("app.data.ingest.validate")
    assert callable(validate.check_pct_limit)

    ps = _module("app.data.parquet_store")
    assert callable(ps.write_year_batch)
    assert callable(ps.write_partition)


def test_infer_module_still_imports_without_logger_import():
    """`infer_day` 删除后，其专用 import（logger / FEATURE_VERSION）也应一并消失，
    但模块本身必须仍可导入、核心函数仍可用。"""
    src = (APP_ROOT / "ml" / "infer.py").read_text(encoding="utf-8")
    assert "from loguru import logger" not in src
    assert "FEATURE_VERSION" not in src
    m = _module("app.ml.infer")
    assert callable(m.load_prod_model)
    assert callable(m.top_factor_contributions)
    assert callable(m.global_feature_importance)