"""BaoStock 数据采集适配器（AQP，多源降级链的**最后兜底**）。

设计要点（2026-09-26 多源降级改造，见 ``docs/audit-datasource-2026-09-26``）：

1. **懒加载 import**：模块被 import 时**不加载** baostock，仅首次调用才导入；
   未安装时 :func:`is_available` 返回 ``False``，后端启动路径不受任何影响。
2. **进程级会话**：``login()/logout()`` 幂等封装（锁保护 + ``atexit`` 注册）；
   登录失败按「源不可用」处理（抛 :class:`DataSourceUnavailable`），换下一个源。
3. **北交所 guard**：BaoStock 不支持北交所（实测报 ``10004011`` 且失败路径耗时
   39 秒），故 :func:`bs_code` 对 4/8 开头代码**提前拒绝、绝不发请求**。
4. **复用限速域**：复用 ``akshare_adapter._throttle``（同一外部请求节流），
   但**不复用** ``_safe_call``/``_SourceBreaker``（其熔断键面向 ``ak.*`` 函数对象，
   对 baostock 无意义）。
5. **复权口径告警**：BaoStock 用「涨跌幅复权法」，与东财/新浪不同 ⇒
   ``source==baostock`` 的行**不得与**东财/新浪行混在同一条 hfq/qfq 序列内
   （守卫见 ``ingest/tasks.write_daily_bars``）。
6. **仅兜底、不进热路径**：实测平均 3.18 秒/次，只用于离线/夜间同步，
   不得用于实时快照，也不得并行预热。

⚠️ 所有返回值都是字符串（如 ``'0.240900'``），必须转数值；``volume`` 单位为
**股**（与东财 ``stock_zh_a_hist`` 口径一致，**直接使用、切勿换算**），
``amount`` 为元，``turn``/``pctChg`` 为 %。

🔴 **量纲契约（2026-10-01 实证修正，勿再改回）**：
全仓库 ``daily_bar.volume`` 的**唯一口径是「股」**。此前本模块曾对 baostock 的
``volume`` 做 ``÷100`` 以求"对齐东财=手"，该前提**是错的** ——
实测 600519.SH 全量 1142 个交易日，``amount / (volume × close)`` 中位数 = **1.0007**
（若 volume 为「手」该比值应为 ~100），且 1 手=100 股 ⇒ 东财单位即「股」。
据此 ``÷100`` 会让 baostock 行比其他源**小两个数量级**，属静默量纲污染。
判据必须用**跨字段恒等式**（成交额≈价格×成交量）而非注释或字段名。
"""
from __future__ import annotations

import atexit
import threading
from datetime import datetime
from types import ModuleType

import pandas as pd
import polars as pl
from loguru import logger

from ...core.errors import DataSourceUnavailable
from ...domain.a_share_rules import code_to_symbol
from .akshare_adapter import _throttle

# ---------- BaoStock 惰性加载 ----------
_bs_module: ModuleType | None = None
_bs_lock = threading.Lock()


def _bs() -> ModuleType:
    """惰性导入并缓存 baostock 模块（线程安全；未安装时抛 ImportError）。"""
    global _bs_module
    if _bs_module is None:
        with _bs_lock:
            if _bs_module is None:
                import baostock as bs  # 延迟到首次调用

                _bs_module = bs
    return _bs_module


def is_available() -> bool:
    """BaoStock 是否可用（已安装）。

    未安装时返回 ``False``（**不抛异常**）：调用方据此把该源标记为 ``absent``，
    直接跳过（不等价于失败，不计数、不报错）。
    """
    try:
        _bs()
    except ImportError:
        return False
    return True


# ---------- 进程级会话（login/logout 幂等） ----------
_session_lock = threading.Lock()
_logged_in = False
_atexit_registered = False


def _register_logout() -> None:
    """注册进程退出时的登出钩子（幂等，只注册一次）。"""
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(_logout)
    _atexit_registered = True


def _ensure_login() -> ModuleType:
    """确保已登录，返回 baostock 模块；登录失败抛 :class:`DataSourceUnavailable`。

    - 可重入（锁保护），已登录时直接返回；
    - **首批连接失败不缓存**（下次重试），成功后才置 ``_logged_in=True``。
    """
    global _logged_in
    bs = _bs()
    if _logged_in:
        return bs
    with _session_lock:
        if _logged_in:
            return bs
        lg = bs.login()
        if getattr(lg, "error_code", "1") != "0":
            raise DataSourceUnavailable(
                f"baostock 登录失败: code={getattr(lg, 'error_code', '?')} "
                f"msg={getattr(lg, 'error_msg', '')!r}")
        _logged_in = True
        _register_logout()
        logger.debug("[baostock] login ok")
    return bs


def _logout() -> None:
    """登出（幂等：未登录时直接返回；登出异常只记 debug，绝不上抛）。"""
    global _logged_in
    with _session_lock:
        if not _logged_in:
            return
        try:
            _bs().logout()
            logger.debug("[baostock] logout ok")
        except Exception as e:  # noqa: BLE001 进程退出/登出失败不影响主流程
            logger.debug(f"[baostock] logout 失败（忽略）: {type(e).__name__}: {e!r}")
        finally:
            _logged_in = False


def _reset_session_for_test() -> None:
    """清空「已登录」缓存（测试隔离用；生产无调用）。"""
    global _logged_in
    with _session_lock:
        _logged_in = False


# ---------- 代码 / 复权口径转换 ----------
def bs_code(code: str) -> str:
    """6 位纯数字代码 -> BaoStock 代码：``600519`` -> ``sh.600519``。

    - ``6``/``9``/``5`` 开头 -> ``sh.``（沪市股票 / ETF / B股）
    - ``0``/``2``/``3`` 开头 -> ``sz.``（深市）
    - ``4``/``8`` 开头（新三板 / 北交所）-> **抛 ValueError**：BaoStock 实测不支持
      （报 ``10004011``，且失败路径耗时 39 秒），调用方须提前 guard，**绝不发请求**。
    """
    if code.startswith(("6", "9", "5")):
        return f"sh.{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz.{code}"
    if code.startswith(("4", "8")):
        raise ValueError(f"BaoStock 不支持北交所/新三板代码: {code}")
    raise ValueError(f"无法识别的代码前缀: {code}")


# adjustflag 映射（实测 3/2/1 全部可用）：3=不复权 / 2=前复权 / 1=后复权
_BS_ADJUST: dict[str, str] = {"": "3", "none": "3", "qfq": "2", "hfq": "1"}


def bs_adjust(adjust: str) -> str:
    """把 AQP 复权口径映射到 BaoStock ``adjustflag``。"""
    try:
        return _BS_ADJUST[adjust]
    except KeyError as e:
        raise ValueError(f"BaoStock 未知复权口径: {adjust!r}") from e


# 请求字段（实测 turn/pctChg/amount/volume 全部有值）
_FIELDS: tuple[str, ...] = (
    "date", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg",
)

# 与 ``akshare_adapter._standardize_daily`` 对齐的标准列集合（baostock 自身提供
# pct/turnover；amplitude/change 无来源 ⇒ 显式补 null，不伪造）。
_STD_COLS: frozenset[str] = frozenset({
    "date", "symbol", "code", "open", "high", "low", "close",
    "volume", "amount", "pct", "turnover", "amplitude", "change", "source",
})


def _standardize_bs(df: pd.DataFrame, code: str) -> pl.DataFrame:
    """把 BaoStock 原始字符串 DataFrame 规范化到 ``_standardize_daily`` 的 schema。

    - ``turn -> turnover``、``pctChg -> pct``；
    - ``volume`` **直取（单位=股）**，与东财 ``volume``（同为股）口径一致，**不换算**；
      ``amount`` 为元、直取（见模块 docstring 的量纲契约：实证 1 手=100 股，
      东财 ``volume`` 单位即股，故任何 ``÷100``/``×100`` 都是错的）；
    - 全部字段 ``pd.to_numeric(errors="coerce")``（源返回字符串）；
    - 补 ``date/symbol/code/source`` 与 ``amplitude(null)/change(null)``。
    """
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.copy()
    df = df.rename(columns={"turn": "turnover", "pctChg": "pct"})
    if "date" not in df.columns:
        raise ValueError(
            f"BaoStock 日线响应缺 date 列（code={code}, rows={len(df)}, "
            f"实际列={list(df.columns)}）")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["code"] = code
    df["symbol"] = code_to_symbol(code)
    for c in ("open", "high", "low", "close", "volume", "amount", "pct", "turnover"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    # 🔴 volume 不换算：BaoStock 与东财同为「股」口径（实证见模块 docstring 量纲契约）。
    # 历史上此处曾 `/100.0`，前提"东财=手"是错的 ⇒ 会让本源行小两个数量级。
    df["amplitude"] = None
    df["change"] = None
    df["source"] = "baostock"
    keep = [c for c in df.columns if c in _STD_COLS]
    return pl.from_pandas(df[keep]).sort("date")


def fetch_daily_bar_bs(
    code: str, start: str, end: str, adjust: str = "",
) -> pl.DataFrame:
    """拉取单只股票日线（BaoStock），返回与 ``_standardize_daily`` 同构的 DataFrame。

    ⚠️ 语义：
    - 北交所/未知前缀代码 -> 记 WARNING 后返回**空 DataFrame**（= 该源不覆盖此标的，
      不算故障，**绝不发请求**）；
    - ``is_available()==False``（未安装）-> 抛 :class:`DataSourceUnavailable`，由上层
      跳过（调用方通常已先判 ``is_available``）；
    - 登录失败 / 请求错误 -> 记结构化 WARNING 日志后**原样上抛**（换下一个源）。

    :param code:   6 位纯数字代码，如 ``"600519"``
    :param start:  起始日期 ``"YYYYMMDD"`` 或 ``"YYYY-MM-DD"``
    :param end:    结束日期
    :param adjust: ``""`` 不复权 / ``"qfq"`` 前复权 / ``"hfq"`` 后复权
    """
    try:
        bs_symbol = bs_code(code)
    except ValueError as e:
        # 北交所/未知前缀：不支持的标的，快速返回空（**不发请求**，避免 39s 卡顿）
        logger.warning(
            f"[baostock] fetch_daily_bar 跳过（不支持的代码）| source=baostock "
            f"| api=query_history_k_data_plus | code={code} | reason={e}")
        return pl.DataFrame()

    # 未知复权口径属调用方错误（配置/代码 bug），直接抛，不吞
    adjustflag = bs_adjust(adjust)
    start_date = start.replace("-", "")
    end_date = end.replace("-", "")

    bs = _ensure_login()
    rows: list[list[str]] = []
    try:
        _throttle()
        rs = bs.query_history_k_data_plus(
            bs_symbol, ",".join(_FIELDS),
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag=adjustflag,
        )
        if getattr(rs, "error_code", "1") != "0":
            raise ConnectionError(
                f"baostock 返回错误 code={rs.error_code} msg={getattr(rs, 'error_msg', '')!r}")
        while rs.next():
            rows.append(rs.get_row_data())
    except Exception as e:  # noqa: BLE001 结构化记录后原样上抛（换源判断依赖异常类型）
        logger.warning(
            "[baostock] fetch_daily_bar 失败 | source=baostock "
            "| api=query_history_k_data_plus "
            f"| code={code} start={start} end={end} adjust={adjust} "
            f"| error={type(e).__name__}: {e!r} "
            f"| ts={datetime.now().isoformat(timespec='seconds')}")
        raise

    if not rows:
        return pl.DataFrame()
    return _standardize_bs(pd.DataFrame(rows, columns=list(_FIELDS)), code)
