"""审计 R2 / §6.4 / §8.2 第 18 项：费率**单一来源**定向守卫 + 数值回归锁。

背景：同一批费率此前硬编码在 8 处（`backtest/broker.py`、`backtest/ma_cross.py`、
`backtest/engine.py`、`backtest/strategy_base.py`、`trading/paper.py`、
`domain/portfolio.py`、`api/v1/backtest.py`、`db/models.py`），改一处漏一处
（B5-10：ETF 豁免 4 处免 / 1 处收）。本次把**数字**收敛到
:mod:`app.domain.trading_rules`，把**品种判定与历史分段**留给
:mod:`app.domain.a_share_rules` 的既有函数（复用而非重写）。

四层断言：
1. `TestSingleSourceDefaults`：所有计费入口的默认值 == 单一来源常量
   （构造器默认值 / 函数签名默认值 / pydantic 字段默认值 / ORM 列默认值），
   且模块级别名为**同一对象**（`is`）而非等值副本；
2. `TestNoFeeLiteralsInApp`：AST 扫描 `app/`，费率字面量只允许出现在
   `domain/trading_rules.py`（唯一豁免：被 §8.2 第 17 项标记删除的
   `a_share_rules.commission_rate_default`，且其字面量必须与被豁免的常量
   **逐位相等**，防止豁免变成分叉）；
3. `TestTierSemantics`：印花税 2023-08-28 前后 1‰/0.5‰、ETF 卖出免印花税、
   过户费 0.01‰ 双边（场内基金 0）—— 与单一来源常量一致，且**经计费路径**
   验证同一口径；
4. `TestGoldenRegressionLock`：固定输入的回测 digest 与"口径归一之前"逐位相同
   （golden 由 `.tmp_testrun/r2_fee_golden.py` 在改动前采样；纯 Python 浮点
   运算用 `==`，含 numpy/pandas 归约的量用 `rel=1e-12` 容差 —— 费率若被改动，
   偏差量级在 1e-4 以上，必被此锁拦下）。
"""
from __future__ import annotations

import ast
import inspect
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1.backtest import StrategyBacktestRequest  # noqa: E402
from app.backtest.broker import (  # noqa: E402
    Broker,
    BrokerConfig,
)
from app.backtest.engine import run_backtest, run_group_backtest  # noqa: E402
from app.backtest.ma_cross import MaCrossParams, run_ma_cross  # noqa: E402
from app.backtest.strategy_base import run_strategy  # noqa: E402
from app.db.models import BacktestRun  # noqa: E402
from app.domain import a_share_rules, portfolio, trading_rules  # noqa: E402
from app.domain.trading_rules import (  # noqa: E402
    COMMISSION_MIN,
    COMMISSION_RATE_DEFAULT,
    STAMP_DUTY_CUT_DATE,
    STAMP_DUTY_STOCK_RATE,
    STAMP_DUTY_STOCK_RATE_LEGACY,
    TRANSFER_FEE_RATE,
)
from app.trading import paper  # noqa: E402

ROW = {"open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
       "volume": 1_000_000.0, "limit_up": 11.0, "limit_down": 9.0,
       "is_halted": False}

D2020 = date(2020, 6, 1)      # 印花税 1‰ 区间
D2024 = date(2024, 6, 3)      # 印花税 0.5‰ 区间


def _sell_cost(symbol: str, buy_day: date, sell_day: date, **kw) -> tuple[float, float]:
    """返回 (卖出成交额, 卖出费用) —— 走真实 Broker 计费路径。"""
    b = Broker(init_cash=100_000.0, **kw)
    t_buy = b.buy(buy_day, symbol, cash_amount=100_000.0, row=pd.Series(ROW))
    b.mark_to_market(buy_day, pd.DataFrame({"close": [10.0]}, index=[symbol]))
    t_sell = b.sell(sell_day, symbol, qty=t_buy.qty, row=pd.Series(ROW))
    assert t_sell.reason == "filled"
    return t_sell.amount, t_sell.cost


# ============================================================================
# ① 默认值同源
# ============================================================================
class TestSingleSourceDefaults:
    def test_canonical_values(self):
        """单一来源的数值本身（审计口径：万 3 佣金 / 最低 5 元 / 1‰→0.5‰ / 0.01‰）。"""
        assert COMMISSION_RATE_DEFAULT == 0.0003
        assert COMMISSION_MIN == 5.0
        assert STAMP_DUTY_STOCK_RATE == 0.0005
        assert STAMP_DUTY_STOCK_RATE_LEGACY == 0.001
        assert STAMP_DUTY_CUT_DATE == date(2023, 8, 28)
        assert TRANSFER_FEE_RATE == 0.00001

    def test_broker_defaults(self):
        """`Broker(init_cash=…)` 的默认费率 == 单一来源常量。

        注：`BrokerConfig` 只承载摩擦模型参数（滑点/换手衰减/冲击），费率字段在
        `Broker` 上；摩擦模型默认值见 `test_friction_model_defaults_still_consistent`。
        """
        b = Broker(init_cash=1.0)
        assert b.commission_rate == COMMISSION_RATE_DEFAULT
        assert b.stamp_duty is None, "None = 按法定分段（effective_stamp_duty），不是硬编码 0.0005"

    def test_function_signature_defaults(self):
        """引擎/框架/策略三条回测入口的签名默认值同源。"""
        for fn in (run_backtest, run_group_backtest, run_strategy):
            p = inspect.signature(fn).parameters["commission_rate"]
            assert p.default == COMMISSION_RATE_DEFAULT, f"{fn.__name__} 默认佣金率分叉"
            assert inspect.signature(fn).parameters["stamp_duty"].default is None

    def test_dataclass_and_model_defaults(self):
        """数据类 / pydantic 请求模型 / ORM 列默认值同源（含 DB 侧快照税率）。"""
        assert MaCrossParams().commission_rate == COMMISSION_RATE_DEFAULT
        assert MaCrossParams().stamp_duty is None
        assert StrategyBacktestRequest.model_fields["commission_rate"].default \
            == COMMISSION_RATE_DEFAULT
        assert float(BacktestRun.__table__.c.commission_rate.default.arg) \
            == COMMISSION_RATE_DEFAULT
        # DB 存的是"当前法定税率"快照（分段在计费时由 effective_stamp_duty 完成）
        assert float(BacktestRun.__table__.c.stamp_duty.default.arg) \
            == STAMP_DUTY_STOCK_RATE

    def test_module_level_names_are_same_object(self):
        """调用模块里的模块级名字必须是**同一个对象**（import），不是等值副本。"""
        from app.backtest import broker, engine, ma_cross, strategy_base  # noqa: F401

        assert broker.COMMISSION_MIN is trading_rules.COMMISSION_MIN
        assert ma_cross.COMMISSION_MIN is trading_rules.COMMISSION_MIN
        assert paper.COMMISSION_MIN is trading_rules.COMMISSION_MIN
        assert portfolio.COMMISSION_MIN is trading_rules.COMMISSION_MIN
        # a_share_rules 的费率常量是对 trading_rules 的再导出（identity，等值副本不算）
        assert a_share_rules.STAMP_DUTY_STOCK_RATE is trading_rules.STAMP_DUTY_STOCK_RATE
        assert a_share_rules.STAMP_DUTY_STOCK_RATE_LEGACY is trading_rules.STAMP_DUTY_STOCK_RATE_LEGACY
        assert a_share_rules.TRANSFER_FEE_RATE is trading_rules.TRANSFER_FEE_RATE
        assert a_share_rules.STAMP_DUTY_CUT_DATE is trading_rules.STAMP_DUTY_CUT_DATE

    def test_duplicate_module_constants_not_revived(self):
        """调用模块内不得再出现本地费率副本（R2 的分叉源头）。"""
        for mod in (paper, portfolio):
            for name in ("COMMISSION_RATE", "STAMP_DUTY"):
                assert not hasattr(mod, name), \
                    f"{mod.__name__}.{name} 是费率副本；应改为引用 domain.trading_rules"

    def test_default_equals_explicit_constant_behaviourally(self):
        """默认值与显式传入单一来源常量产生**逐位相同**的费用（行为级同源）。"""
        t_default = Broker(init_cash=100_000.0).buy(D2024, "600519.SH",
                                                    cash_amount=10_000.0, row=pd.Series(ROW))
        t_explicit = Broker(init_cash=100_000.0, commission_rate=COMMISSION_RATE_DEFAULT) \
            .buy(D2024, "600519.SH", cash_amount=10_000.0, row=pd.Series(ROW))
        assert t_default.qty == t_explicit.qty
        assert t_default.cost == t_explicit.cost

    def test_friction_model_defaults_still_consistent(self):
        """摩擦模型默认值（滑点/衰减）本次**未**收敛，只锁"当前处处一致"的现状。

        报告 §8.2 第 18 项只针对费率；默认摩擦姿态属产品决策（§8.2 第 5 项），
        故此处只做一致性回归，不引入新的单一来源。
        """
        assert BrokerConfig().slippage_bps == 5.0
        assert BrokerConfig().decay_bps == 10.0
        assert MaCrossParams().slippage_bps == BrokerConfig().slippage_bps
        assert portfolio.SLIPPAGE_BPS == BrokerConfig().slippage_bps
        assert inspect.signature(run_strategy).parameters["slippage_bps"].default \
            == BrokerConfig().slippage_bps


# ============================================================================
# ② app/ 下不得再有费率字面量（AST 扫描）
# ============================================================================
FEE_LITERAL_NAMES = {
    0.0003: "COMMISSION_RATE_DEFAULT",
    0.0005: "STAMP_DUTY_STOCK_RATE",
    0.00001: "TRANSFER_FEE_RATE",
}
CANONICAL_MODULE = Path("domain") / "trading_rules.py"
# §8.2 第 17 项已判定为死代码、待删除；此处仅豁免"字面量等于常量"的实现，
# 待该函数删除后本豁免自动失效（无匹配即无豁免）。
PENDING_DELETE_EXEMPT = {((Path("domain") / "a_share_rules.py").as_posix(),
                          "commission_rate_default")}
FEE_CONST_ASSIGN_RE = r"^(COMMISSION_MIN|COMMISSION_RATE(_DEFAULT)?|STAMP_DUTY.*|TRANSFER_FEE.*)$"
# 语义不同、**不得合并**的同值字面量：`np.allclose(..., rtol=1e-5)` 之类是数值容差
# （`orchestrator.py:239` 的因子重叠段浮点尾差判据），与过户费 0.00001 只是数值巧合。
# 判据按"实参/形参名"识别，不用行号白名单（行号会随无关改动漂移）。
TOLERANCE_KWARGS = {"rtol", "atol", "tol", "abs_tol", "rel_tol", "eps"}


def _tolerance_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in TOLERANCE_KWARGS \
                        and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, float):
                    lines.add(kw.value.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a, d in zip([*args.args, *args.kwonlyargs],
                            [*args.defaults, *(args.kw_defaults or [])]):
                if a.arg in TOLERANCE_KWARGS and isinstance(d, ast.Constant) \
                        and isinstance(d.value, float):
                    lines.add(d.lineno)
    return lines


def _scan_file(py: Path, root: Path = APP_ROOT) -> list[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    rel = py.relative_to(root).as_posix()
    violations: list[str] = []
    tolerance_lines = _tolerance_lines(tree)

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.func: str | None = None

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            prev = self.func
            self.func = node.name
            self.generic_visit(node)
            self.func = prev

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Constant(self, node: ast.Constant) -> None:
            val = node.value
            if isinstance(val, float) and val in FEE_LITERAL_NAMES:
                if rel == CANONICAL_MODULE.as_posix():
                    return
                if node.lineno in tolerance_lines:
                    return          # 数值容差，非费率（语义不同，不合并）
                if (rel, self.func) in PENDING_DELETE_EXEMPT \
                        and val == COMMISSION_RATE_DEFAULT:
                    return
                violations.append(
                    f"{rel}:{node.lineno} 费率字面量 {val!r}"
                    f"（应为 trading_rules.{FEE_LITERAL_NAMES[val]}）"
                    + (f"，enclosing={self.func}" if self.func else ""))

    _Visitor().visit(tree)

    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for tgt in targets:
            if not isinstance(tgt, ast.Name) or not re.match(FEE_CONST_ASSIGN_RE, tgt.id):
                continue
            if not isinstance(node.value, ast.Constant) \
                    or not isinstance(node.value.value, (int, float)):
                continue
            if rel != CANONICAL_MODULE.as_posix():
                violations.append(
                    f"{rel}:{node.lineno} 本地定义费率常量 {tgt.id} = {node.value.value!r}"
                    "（应 import domain.trading_rules）")
    return violations


def test_no_fee_literals_outside_single_source():
    """`app/**/*.py` 中费率字面量只能出现在 domain/trading_rules.py。"""
    bad: list[str] = []
    for py in sorted(APP_ROOT.rglob("*.py")):
        bad.extend(_scan_file(py))
    assert not bad, "费率字面量仍散落在多个文件（R2 未收敛）:\n" + "\n".join(bad)


def test_scanner_detects_violation(tmp_path):
    """变异反证：把"一份新费率副本"写进文件，扫描器必须报错（守卫不能恒真）。"""
    fake_root = tmp_path / "app"
    fake = fake_root / "domain" / "fake_copy.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("COMMISSION_MIN = 5.0\nRATE = 0.0003\n", encoding="utf-8")
    bad = _scan_file(fake, root=fake_root)
    assert len(bad) == 2, bad
    assert any("0.0003" in b for b in bad)           # 字面量
    assert any("COMMISSION_MIN" in b for b in bad)   # 本地常量定义
    # 对照组：同一内容放在单一来源模块里则合规
    ok_dir = fake_root / "domain"
    trading_rules_like = ok_dir / "trading_rules.py"
    trading_rules_like.write_text("COMMISSION_MIN = 5.0\nRATE = 0.0003\n", encoding="utf-8")
    assert _scan_file(trading_rules_like, root=fake_root) == []


# ============================================================================
# ③ 分档口径与该模块一致
# ============================================================================
class TestTierSemantics:
    def test_stamp_duty_before_and_after_cut(self):
        """2023-08-28 前 1‰、当日起 0.5‰（分段边界逐日）。"""
        assert a_share_rules.stamp_duty_rate(
            "stock", "sell", STAMP_DUTY_CUT_DATE - timedelta(days=1)) \
            == STAMP_DUTY_STOCK_RATE_LEGACY == 0.001
        assert a_share_rules.stamp_duty_rate(
            "stock", "sell", STAMP_DUTY_CUT_DATE) \
            == STAMP_DUTY_STOCK_RATE == 0.0005
        # 不给日期 = 当前税率（保持既有调用方行为）
        assert a_share_rules.stamp_duty_rate("stock", "sell") == STAMP_DUTY_STOCK_RATE

    def test_stamp_duty_only_stock_sell(self):
        """股票买入免、场内基金卖出免（方向/品种大小写不敏感）。"""
        assert a_share_rules.stamp_duty_rate("stock", "buy", D2020) == 0.0
        assert a_share_rules.stamp_duty_rate("etf", "sell", D2020) == 0.0
        assert a_share_rules.stamp_duty_rate("stock", "SELL", D2024) == STAMP_DUTY_STOCK_RATE

    def test_effective_stamp_duty_etf_exempt_wins_over_override(self):
        """ETF 免征是法定豁免，连显式 override 都不覆盖；股票 override 有效。"""
        assert a_share_rules.effective_stamp_duty("510300.SH", D2020) == 0.0
        assert a_share_rules.effective_stamp_duty("510300.SH", D2024, 0.001) == 0.0
        assert a_share_rules.effective_stamp_duty("159915.SZ", D2020) == 0.0
        assert a_share_rules.effective_stamp_duty("600519.SH", D2020) == STAMP_DUTY_STOCK_RATE_LEGACY
        assert a_share_rules.effective_stamp_duty("600519.SH", D2024) == STAMP_DUTY_STOCK_RATE
        assert a_share_rules.effective_stamp_duty("600519.SH", D2024, 0.0003) == 0.0003

    def test_transfer_fee_stock_both_sides_etf_zero(self):
        """过户费：股票双边 0.01‰，场内基金 0。"""
        assert a_share_rules.transfer_fee_rate("stock") == TRANSFER_FEE_RATE == 0.00001
        assert a_share_rules.transfer_fee_rate("etf") == 0.0
        assert a_share_rules.effective_transfer_fee("600519.SH") == TRANSFER_FEE_RATE
        assert a_share_rules.effective_transfer_fee("510300.SH") == 0.0

    def test_broker_consumes_same_constants(self):
        """经真实计费路径验证：费用公式只用单一来源常量（纯 Python 浮点 ⇒ 精确相等）。"""
        # 买入：佣金（含最低 5 元）+ 双边过户费
        b = Broker(init_cash=100_000.0)
        t = b.buy(D2024, "600519.SH", cash_amount=100_000.0, row=pd.Series(ROW))
        assert t.cost == max(COMMISSION_MIN, t.amount * COMMISSION_RATE_DEFAULT) \
            + t.amount * TRANSFER_FEE_RATE

        # 股票卖出：佣金 + 印花税（分段）+ 过户费
        amt, cost = _sell_cost("600519.SH", D2020, D2020 + timedelta(days=1))
        assert cost == max(COMMISSION_MIN, amt * COMMISSION_RATE_DEFAULT) \
            + amt * STAMP_DUTY_STOCK_RATE_LEGACY + amt * TRANSFER_FEE_RATE
        amt, cost = _sell_cost("600519.SH", D2024, D2024 + timedelta(days=1))
        assert cost == max(COMMISSION_MIN, amt * COMMISSION_RATE_DEFAULT) \
            + amt * STAMP_DUTY_STOCK_RATE + amt * TRANSFER_FEE_RATE

        # ETF 卖出：免印花税、免过户费（B5-10 的原始缺陷）
        amt, cost = _sell_cost("510300.SH", D2024, D2024 + timedelta(days=1))
        assert cost == max(COMMISSION_MIN, amt * COMMISSION_RATE_DEFAULT)

        # 显式 override（DB/API 配置口径）仍被尊重
        amt, cost = _sell_cost("600519.SH", D2024, D2024 + timedelta(days=1), stamp_duty=0.0003)
        assert cost == max(COMMISSION_MIN, amt * COMMISSION_RATE_DEFAULT) \
            + amt * 0.0003 + amt * TRANSFER_FEE_RATE


# ============================================================================
# ④ 数值回归锁：固定输入 → 与"口径归一之前"逐位相同
# ============================================================================
ENGINE_GOLDEN: dict[str, dict[str, float]] = {
    "2020_no_friction": {
        "n_trades": 22, "sum_cost": 4694.810000000001, "sum_amount": 7579000.0,
        "nav_sum": 11.97149257, "turnover_sum": 3.797203739537164,
        "nav_last": 0.99530519, "decay": 0.0, "slippage": 0.0,
    },
    "2024_no_friction": {
        "n_trades": 19, "sum_cost": 2982.28, "sum_amount": 6637000.0,
        "nav_sum": 11.980623599999998, "turnover_sum": 3.322528617199284,
        "nav_last": 0.9970177199999999, "decay": 0.0, "slippage": 0.0,
    },
    "2020_friction": {
        "n_trades": 20, "sum_cost": 4675.5835099999995, "sum_amount": 7549470.5,
        "nav_sum": 11.925276233715007, "turnover_sum": 3.794976350361999,
        "nav_last": 0.9877751812400009,
        "decay": 3774.7352499999997, "slippage": 3774.4999999992488,
    },
    "2024_friction": {
        "n_trades": 23, "sum_cost": 2972.720505, "sum_amount": 6615470.5,
        "nav_sum": 11.936207759175007, "turnover_sum": 3.3211870059194526,
        "nav_last": 0.9904120442450008,
        "decay": 3307.7352499999997, "slippage": 3307.499999999342,
    },
}
BROKER_GOLDEN = {
    "stock_2020_sell_cost": 129.69,
    "stock_2024_sell_cost": 80.18999999999998,
    "stock_2024_sell_cost_override_0.0003": 60.38999999999999,
    "etf_2024_sell_cost": 29.699999999999996,
    "stock_2024_buy_cost": 30.689999999999994,
}
GROUP_GOLDEN_LAST_ROW = [0.9906666100000003, 0.99221462, 0.99250193, 0.0018353199999996406]
GROUP_GOLDEN_TURNOVER = {"Q1": 0.7516266274143005, "Q2": 0.6722487330108828,
                         "Q3": 0.6327320901657927}
MA_CROSS_GOLDEN = {"nav_sum": 75392707.60051614, "n_signals": 9,
                   "annual_strategy": -0.3929308928396683,
                   "sharpe": -3.2409596415823,
                   "max_drawdown": 0.19032663050620224,
                   "last_buy_qty": 76600, "last_buy_price": 10.732}


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _make_uni(dates: list[date], symbols: list[str], price: float = 10.0) -> pd.DataFrame:
    rows = [{"date": d, "symbol": s, "open": price, "high": price, "low": price,
             "close": price, "volume": 1e6, "limit_up": price * 1.1,
             "limit_down": price * 0.9, "is_halted": False}
            for d in dates for s in symbols]
    return pd.DataFrame(rows)


def _make_sig(uni: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    s = uni[["date", "symbol"]].copy()
    s["pred_score"] = rng.random(len(s))
    return s


class TestGoldenRegressionLock:
    """固定输入的回测 digest 必须与口径归一之前逐位一致（"不改变任何回测数字"）。"""

    @pytest.mark.parametrize("day,seed,friction,key", [
        (D2020, 3, False, "2020_no_friction"),
        (D2024, 5, False, "2024_no_friction"),
        (D2020, 3, True, "2020_friction"),
        (D2024, 5, True, "2024_friction"),
    ])
    def test_engine_digest_unchanged(self, day: date, seed: int, friction: bool, key: str):
        dates = _weekdays(day, 12)
        uni = _make_uni(dates, ["600000.SH", "000001.SZ", "510300.SH"])
        sig = _make_sig(uni, seed)
        cfg = BrokerConfig(slippage_bps=5.0, decay_bps=10.0, enabled=friction)
        r = run_backtest(uni, sig, init_cash=1_000_000.0, top_k=2, friction=cfg)
        g = ENGINE_GOLDEN[key]
        costs = [t["cost"] for t in r.trades]
        amounts = [t["amount"] for t in r.trades]
        assert len(r.trades) == g["n_trades"]
        assert sum(costs) == pytest.approx(g["sum_cost"], rel=1e-12)
        assert sum(amounts) == pytest.approx(g["sum_amount"], rel=1e-12)
        assert float(r.nav_df["nav"].sum()) == pytest.approx(g["nav_sum"], rel=1e-12)
        assert float(r.nav_df["turnover"].sum()) == pytest.approx(g["turnover_sum"], rel=1e-12)
        assert float(r.nav_df["nav"].iloc[-1]) == pytest.approx(g["nav_last"], rel=1e-12)
        assert r.friction_costs["decay"] == pytest.approx(g["decay"], rel=1e-12)
        assert r.friction_costs["slippage"] == pytest.approx(g["slippage"], rel=1e-12)

    def test_group_backtest_digest_unchanged(self):
        dates = _weekdays(D2024, 12)
        uni = _make_uni(dates, ["600000.SH", "000001.SZ", "300750.SZ",
                                "510300.SH", "002594.SZ"])
        g = run_group_backtest(uni, _make_sig(uni, 7), groups=3, init_cash=1_000_000.0)
        last = g["group_nav"].iloc[-1].tolist()
        assert [float(x) for x in last[1:]] == pytest.approx(GROUP_GOLDEN_LAST_ROW, rel=1e-12)
        assert g["group_turnover"] == pytest.approx(GROUP_GOLDEN_TURNOVER, rel=1e-12)
        assert g["monthly_monotonic_ratio"] == 1.0

    def test_ma_cross_digest_unchanged(self):
        dates = _weekdays(D2020, 80)
        px = [10.0 * (1 + 0.12 * math.sin(i / 4.0)) for i in range(len(dates))]
        bars = {"600519.SH": pd.DataFrame({"date": dates, "open": px, "close": px})}
        bench = pd.DataFrame({"date": dates, "close": px})
        r = run_ma_cross(bars, bench, MaCrossParams())
        assert float(r.nav_df.select_dtypes("number").sum().sum()) \
            == pytest.approx(MA_CROSS_GOLDEN["nav_sum"], rel=1e-12)
        assert len(r.signals) == MA_CROSS_GOLDEN["n_signals"]
        assert r.risk["annual_strategy"] == pytest.approx(MA_CROSS_GOLDEN["annual_strategy"],
                                                          rel=1e-12)
        assert r.risk["sharpe"] == pytest.approx(MA_CROSS_GOLDEN["sharpe"], rel=1e-12)
        assert r.risk["max_drawdown"] == pytest.approx(MA_CROSS_GOLDEN["max_drawdown"],
                                                       rel=1e-12)
        last_buy = [t for t in r.trades if t["side"] == "buy"][-1]
        assert last_buy["qty"] == MA_CROSS_GOLDEN["last_buy_qty"]
        assert last_buy["price"] == pytest.approx(MA_CROSS_GOLDEN["last_buy_price"], rel=0)

    def test_broker_fee_golden_exact(self):
        """券商费率逐位锁（纯 Python 浮点 ⇒ 用 ==，不是 approx）。"""
        _, cost_2020 = _sell_cost("600519.SH", D2020, D2020 + timedelta(days=1))
        assert cost_2020 == BROKER_GOLDEN["stock_2020_sell_cost"]
        _, cost_2024 = _sell_cost("600519.SH", D2024, D2024 + timedelta(days=1))
        assert cost_2024 == BROKER_GOLDEN["stock_2024_sell_cost"]
        _, cost_ovr = _sell_cost("600519.SH", D2024, D2024 + timedelta(days=1),
                                 stamp_duty=0.0003)
        assert cost_ovr == BROKER_GOLDEN["stock_2024_sell_cost_override_0.0003"]
        _, cost_etf = _sell_cost("510300.SH", D2024, D2024 + timedelta(days=1))
        assert cost_etf == BROKER_GOLDEN["etf_2024_sell_cost"]
        b = Broker(init_cash=100_000.0)
        t = b.buy(D2024, "600519.SH", cash_amount=100_000.0, row=pd.Series(ROW))
        assert t.cost == BROKER_GOLDEN["stock_2024_buy_cost"]