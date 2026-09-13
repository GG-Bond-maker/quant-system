"""Qlib 式表达式因子引擎（参考 microsoft/qlib 的 Feature 表达式设计）。

核心思想（qlib 的招牌能力）：
    因子不再是一段段手写 pandas 代码，而是**表达式字符串**：
        "Mean($close, 20) / Std($close, 20)"        # 反转/波动比
        "Corr($close, Log($volume + 1), 10)"        # 量价相关性
    由统一的算子注册表 + 安全 AST 解析器求值，优点：
    - 批量挖掘/网格化枚举因子（配合符号回归或人工枚举）
    - 表达式即文档：因子定义可入库、可复现、可 diff

PIT 安全红线（与 qlib 不同，我们**禁止任何未来引用**）：
    - Ref(x, n) 仅允许 n >= 0（取 n 天前的值）；
      qlib 的 Ref(x, -1) 是未来值，在本引擎直接抛异常；
    - 全部算子为滚动/逐元素向后计算，窗口 min_periods=window；
    - build_alpha158_lite 可直接接入
      ml.labeling.future_contamination_check 做 asof 验收。

字段约定：$close/$open/$high/$low/$volume/$amount（别名 C/O/H/L/V/AMT）。
"""
from __future__ import annotations

import ast
from typing import Callable

import numpy as np
import pandas as pd
from loguru import logger

# ---------------- 字段映射 ----------------
FIELDS: dict[str, str] = {
    "close": "close", "open": "open", "high": "high", "low": "low",
    "volume": "volume", "amount": "amount",
    # qlib 风格缩写
    "C": "close", "O": "open", "H": "high", "L": "low", "V": "volume",
    "AMT": "amount",
}

EPS = 1e-12


# ---------------- 算子注册表（全部向后计算） ----------------
def _op_ref(x: pd.Series, n: int = 1) -> pd.Series:
    """Ref(x, n)：n 天前的值（n>=0）。qlib 的负 n = 未来引用，此处禁止。"""
    if n < 0:
        raise ValueError(f"Ref 禁止未来引用（n={n} < 0），PIT 红线")
    return x.shift(int(n))


def _op_slope(x: pd.Series, w: int) -> pd.Series:
    """滚动线性回归斜率：Cov(x, t) / Var(t)，t = 0..w-1（向量化）。"""
    w = int(w)
    t = pd.Series(np.arange(len(x), dtype=np.float64), index=x.index)
    var_t = (w * w - 1) / 12.0
    return x.rolling(w, min_periods=w).cov(t) / var_t


def _op_log(x: pd.Series) -> pd.Series:
    return np.log(x.clip(lower=EPS))


OPS: dict[str, tuple[Callable, int, int]] = {
    # name: (实现, 最少参数数, 最多参数数)；窗口参数统一 int() 化
    #（表达式常量在解析阶段统一转 float，滚动 API 要求整数窗口）
    "Ref": (_op_ref, 2, 2),
    "Mean": (lambda x, w: x.rolling(int(w), min_periods=int(w)).mean(), 2, 2),
    "Sum": (lambda x, w: x.rolling(int(w), min_periods=int(w)).sum(), 2, 2),
    "Std": (lambda x, w: x.rolling(int(w), min_periods=int(w)).std(), 2, 2),
    "Var": (lambda x, w: x.rolling(int(w), min_periods=int(w)).var(), 2, 2),
    "Max": (lambda x, w: x.rolling(int(w), min_periods=int(w)).max(), 2, 2),
    "Min": (lambda x, w: x.rolling(int(w), min_periods=int(w)).min(), 2, 2),
    "Med": (lambda x, w: x.rolling(int(w), min_periods=int(w)).median(), 2, 2),
    "Mad": (lambda x, w: x.rolling(int(w), min_periods=int(w)).apply(
        lambda a: float(np.median(np.abs(a - np.median(a)))), raw=True), 2, 2),
    "Rank": (lambda x, w: x.rolling(int(w), min_periods=int(w)).rank(pct=True), 2, 2),
    "Corr": (lambda x, y, w: x.rolling(int(w), min_periods=int(w)).corr(y), 3, 3),
    "Cov": (lambda x, y, w: x.rolling(int(w), min_periods=int(w)).cov(y), 3, 3),
    "Delta": (lambda x, w: x - x.shift(int(w)), 2, 2),
    "Slope": (_op_slope, 2, 2),
    "IdxMax": (lambda x, w: x.rolling(int(w), min_periods=int(w)).apply(
        np.argmax, raw=True), 2, 2),
    "IdxMin": (lambda x, w: x.rolling(int(w), min_periods=int(w)).apply(
        np.argmin, raw=True), 2, 2),
    "Abs": (np.abs, 1, 1),
    "Log": (_op_log, 1, 1),
    "Sign": (np.sign, 1, 1),
    "Sqrt": (lambda x: np.sqrt(x.clip(lower=0.0)), 1, 1),
    "Power": (lambda x, n: np.power(x, float(n)), 2, 2),
    "If": (lambda cond, a, b: pd.Series(np.where(cond > 0, a, b), index=cond.index), 3, 3),
    "Greater": (lambda a, b: pd.Series((a > b).astype(np.float64), index=a.index), 2, 2),
    "Less": (lambda a, b: pd.Series((a < b).astype(np.float64), index=a.index), 2, 2),
    "EMA": (lambda x, w: x.ewm(span=int(w), adjust=False).mean(), 2, 2),
}


# ---------------- 安全 AST 解析器 ----------------
_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name,
                  ast.Constant, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div,
                  ast.USub, ast.Pow, ast.Compare, ast.Gt, ast.Lt, ast.GtE,
                  ast.LtE)

_PARSE_CACHE: dict[tuple[str, frozenset[str]], ast.Expression] = {}
# 缓存上限：GP 挖掘每任务产生上千唯一表达式，无上限会随任务数无界增长；
# 达到上限后批量淘汰最旧的 1/4（dict 按插入序，FIFO 近似 LRU，CPython
# dict 操作在 GIL 下原子，与现有无锁读写一致）
_PARSE_CACHE_MAX = 5000


def parse_expr(expr: str, extra_fields: set[str] | None = None) -> ast.Expression:
    """解析并白名单校验表达式；拒绝属性访问/下标/lambda 等一切非计算节点。

    :param extra_fields: 额外允许的数值字段名（如 features 因子列），
                         缓存按 (expr, fields) 区分。
    """
    extra = frozenset(extra_fields or ())
    cache_key = (expr, extra)
    if cache_key in _PARSE_CACHE:
        return _PARSE_CACHE[cache_key]
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"表达式语法错误: {expr!r} ({e})") from e
    nodes = list(ast.walk(tree))
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"表达式含不允许的节点 {type(node).__name__}: {expr!r}")
    # 第一遍：校验算子调用（函数名不算字段）
    func_nodes: set[int] = set()
    for node in nodes:
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in OPS:
                raise ValueError(f"未知算子: {expr!r}")
            fn, lo, hi = OPS[node.func.id]
            if not (lo <= len(node.args) <= hi) or node.keywords:
                raise ValueError(f"算子 {node.func.id} 参数数不符: {expr!r}")
            func_nodes.add(id(node.func))
    # 第二遍：校验字段名与常量
    for node in nodes:
        if isinstance(node, ast.Name) and id(node) not in func_nodes:
            if node.id not in FIELDS and node.id not in extra:
                raise ValueError(f"未知字段 {node.id!r}（可用: "
                                 f"{sorted(set(FIELDS) | extra)}）: {expr!r}")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ValueError(f"仅允许数值常量: {expr!r}")
    if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
        for k in list(_PARSE_CACHE)[:_PARSE_CACHE_MAX // 4]:
            del _PARSE_CACHE[k]
    _PARSE_CACHE[cache_key] = tree
    return tree


def eval_expr(expr: str, df: pd.DataFrame,
              extra_fields: set[str] | None = None) -> pd.Series:
    """在单标的 DataFrame（按日期升序）上求值表达式。

    :param extra_fields: 额外允许的字段（df 中同名列），如 features 因子列；
                         优先级高于 FIELDS 别名映射。
    返回 pd.Series（与 df.index 对齐）；除零/溢出统一转 NaN。
    """
    tree = parse_expr(expr, extra_fields)
    extra = extra_fields or set()

    def _field_value(node: ast.Name) -> pd.Series | float:
        name = node.id
        col = FIELDS.get(name, name if name in extra else None)
        if col is None or col not in df.columns:
            raise ValueError(f"DataFrame 缺少字段列 {name!r}: {expr!r}")
        return df[col].astype(np.float64)

    def _ev(node: ast.AST) -> pd.Series | float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            v = node.value
            # ast 常量可以是任意字面量，非数值（字符串/None/Ellipsis）直接拒绝，
            # 否则 float() 会抛难定位的 TypeError
            if isinstance(v, bool):
                return float(int(v))
            if isinstance(v, (int, float)):
                return float(v)
            raise ValueError(f"表达式常量必须为数值，收到 {type(v).__name__}: {v!r}")
        if isinstance(node, ast.Name):
            return _field_value(node)
        if isinstance(node, ast.BinOp):
            a, b = _ev(node.left), _ev(node.right)
            with np.errstate(all="ignore"):
                if isinstance(node.op, ast.Add):
                    return a + b
                if isinstance(node.op, ast.Sub):
                    return a - b
                if isinstance(node.op, ast.Mult):
                    return a * b
                if isinstance(node.op, ast.Div):
                    q = a / b
                    return q.replace([np.inf, -np.inf], np.nan) \
                        if isinstance(q, pd.Series) else q
                if isinstance(node.op, ast.Pow):
                    return a ** b
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            v = _ev(node.operand)
            return -v
        if isinstance(node, ast.Compare):
            a = _ev(node.left)
            for op, b_node in zip(node.ops, node.comparators):
                b = _ev(b_node)
                if isinstance(op, ast.Gt):
                    a = pd.Series((a > b).astype(np.float64), index=a.index) \
                        if isinstance(a, pd.Series) else float(a > b)
                elif isinstance(op, ast.Lt):
                    a = pd.Series((a < b).astype(np.float64), index=a.index) \
                        if isinstance(a, pd.Series) else float(a < b)
                elif isinstance(op, ast.GtE):
                    a = pd.Series((a >= b).astype(np.float64), index=a.index) \
                        if isinstance(a, pd.Series) else float(a >= b)
                elif isinstance(op, ast.LtE):
                    a = pd.Series((a <= b).astype(np.float64), index=a.index) \
                        if isinstance(a, pd.Series) else float(a <= b)
                else:
                    raise ValueError(f"不支持的比较运算: {expr!r}")
            return a
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("仅支持直接函数名调用（不支持属性/下标调用）")
            fn, _, _ = OPS[node.func.id]
            args = [_ev(a) for a in node.args]
            return fn(*args)
        raise ValueError(f"无法求值的节点 {type(node).__name__}: {expr!r}")

    out = _ev(tree)
    if isinstance(out, pd.Series):
        out = out.replace([np.inf, -np.inf], np.nan)
    return out


# ---------------- Alpha158-lite 基线因子集（参考 qlib contrib Alpha158） ----------------
def _alpha158_lite_exprs() -> dict[str, str]:
    """qlib Alpha158 的 A 股日线精简版（~50 条，全部 PIT 安全）。

    命名沿用 qlib：KMID/KLEN/KUP/KLOW/KSFT（K线形态）、ROC（动量）、
    MA/STD（均线波动带）、BETA/CORR（量价）、RSV（随机指标）、
    IMAX（极值位置）、CNTP/SUMP（上涨占比/上涨量和）、VMA/VSTD（量能）。
    """
    e: dict[str, str] = {}
    # K 线形态（qlib KMID 族）
    e["KMID"] = "(close - open) / open"
    e["KLEN"] = "(close - open) / open"       # 同 KMID（qlib 中 KLEN 即实体长度）
    e["KUP"] = "(high - Greater(close, open)) / open"
    e["KLOW"] = "(Less(close, open) - low) / open"
    e["KSFT"] = "(2 * close - high - low) / open"
    # 动量 ROC
    for w in (5, 10, 20, 60):
        e[f"ROC{w}"] = f"close / Ref(close, {w}) - 1"
    # 均线乖离 / 波动带
    for w in (5, 10, 20, 30, 60):
        e[f"MA{w}"] = f"close / Mean(close, {w}) - 1"
        e[f"STD{w}"] = f"close / Std(close, {w}) - 1"
    # 量价相关（qlib BETA/CORR 族）
    e["BETA10"] = "Slope(close / Ref(close, 1), 10)"
    for w in (5, 10, 20, 30):
        e[f"CORR{w}"] = f"Corr(close, Log(volume + 1), {w})"
    # 随机指标 RSV（qlib 口径，除零转 NaN）
    for w in (5, 10, 20):
        e[f"RSV{w}"] = (f"(close - Min(low, {w})) / "
                        f"(Max(high, {w}) - Min(low, {w}))")
    # 极值位置
    for w in (5, 10, 20):
        e[f"IMAX{w}"] = f"IdxMax(high, {w}) / {w}"
        e[f"IMIN{w}"] = f"IdxMin(low, {w}) / {w}"
    # 上涨天数占比 / 上涨量和（qlib CNTP/SUMP 族）
    e["CNTP5"] = "Mean(Greater(close, Ref(close, 1)), 5)"
    e["CNTP10"] = "Mean(Greater(close, Ref(close, 1)), 10)"
    e["SUMP5"] = "Sum(If(close > Ref(close, 1), close - Ref(close, 1), 0), 5) / close"
    e["SUMN5"] = "Sum(If(close < Ref(close, 1), Ref(close, 1) - close, 0), 5) / close"
    # 量能
    for w in (5, 10, 20, 60):
        e[f"VMA{w}"] = f"volume / Mean(volume, {w}) - 1"
    e["VSTD5"] = "Std(volume, 5) / (Mean(volume, 60) + 1e-12)"
    e["VROC5"] = "volume / Ref(volume, 5) - 1"
    # 振幅与波动
    for w in (5, 20):
        e[f"RANGE{w}"] = f"Mean((high - low) / close, {w})"
        e[f"VOLOFVOL{w}"] = f"Std(close / Ref(close, 1), {w}) / (Std(close / Ref(close, 1), {w * 3}) + 1e-12)"
    return e


ALPHA158_LITE: dict[str, str] = _alpha158_lite_exprs()


def build_alpha158_lite(
    raw: pd.DataFrame,
    exprs: dict[str, str] | None = None,
    symbol_col: str = "symbol",
    date_col: str = "date",
) -> pd.DataFrame:
    """按标的分组求值全部表达式，返回 symbol/date + 因子列的宽表。

    :param raw: 多标的 OHLCV 长表（symbol, date, open, high, low, close, volume[, amount]）
    :param exprs: 自定义 {因子名: 表达式}；默认 ALPHA158_LITE
    """
    exprs = exprs or ALPHA158_LITE
    need_cols = {col for e in exprs.values()
                 for name in _used_fields(e) for col in [FIELDS[name]]}
    missing = need_cols - set(raw.columns)
    if missing:
        raise ValueError(f"数据缺少字段列: {sorted(missing)}")

    chunks: list[pd.DataFrame] = []
    total = raw[symbol_col].nunique()
    for i, (sym, g) in enumerate(raw.groupby(symbol_col, sort=False), 1):
        g = g.sort_values(date_col).reset_index(drop=True)
        out = pd.DataFrame({symbol_col: sym, date_col: g[date_col].values})
        for name, expr in exprs.items():
            try:
                out[name] = eval_expr(expr, g).to_numpy()
            except Exception as ex:  # 单因子失败不阻断（记录为 NaN 列）
                logger.warning(f"[alpha_expr] {name} 求值失败 {sym}: {ex!r}")
                out[name] = np.nan
        chunks.append(out)
        if i % 100 == 0:
            logger.info(f"[alpha_expr] progress {i}/{total}")
    if not chunks:
        raise ValueError("alpha158_lite 构建失败：无任何标的")
    return pd.concat(chunks, ignore_index=True).replace([np.inf, -np.inf], np.nan)


def _used_fields(expr: str) -> list[str]:
    """提取表达式引用的字段名（排除算子函数名；供缺列校验）。"""
    tree = parse_expr(expr)
    func_nodes = {id(n.func) for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return [n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and id(n) not in func_nodes]
