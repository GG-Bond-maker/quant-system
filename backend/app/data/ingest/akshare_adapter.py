"""
AKShare 数据采集适配器（AQP）。

职责：
1. 把 AKShare 返回的中文列名 DataFrame 统一转换成 AQP 标准 Polars Schema
   （date: Date, open/high/low/close/volume/amount: Float64, symbol/code: String）；
2. 线程安全限速：任意两次 AKShare 请求间隔 >= AKSHARE_RATE_LIMIT 秒 + 随机抖动，
   防止东方财富封临时 IP（threading.Lock 保证多线程并发下的限速正确性）；
3. Tenacity 重试：指数退避（1s -> 2s -> 4s ...），默认 AKSHARE_RETRY=3 次；
4. 批量并发：ThreadPoolExecutor（max_workers 默认 4，不建议超过 4，
   总 QPS ~= max_workers / AKSHARE_RATE_LIMIT）。

⚠️ akshare 采用惰性导入：本模块被 API 进程 import 时不加载 akshare，
仅在实际调用拉取函数时才加载，保证服务启动速度与依赖解耦。
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from datetime import datetime
from types import ModuleType
from typing import Any

import pandas as pd
import polars as pl
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from ...core.config import get_settings
from ...core.errors import DataSourceUnavailable
from ...domain.a_share_rules import code_to_symbol

# ---------- AKShare 惰性加载 ----------
_ak_module: ModuleType | None = None
_ak_lock = threading.Lock()


def _ak() -> ModuleType:
    """惰性导入并缓存 akshare 模块（线程安全）。"""
    global _ak_module
    if _ak_module is None:
        with _ak_lock:
            if _ak_module is None:
                import akshare as ak  # 延迟到首次调用

                _ak_module = ak
    return _ak_module


def get_akshare() -> ModuleType:
    """``_ak`` 的公开别名（Task 16）：api/services 层经此取 akshare 模块，
    保留惰性导入特性；业务层不再直接引用私有名 _ak。"""
    return _ak()


# ---------- 线程安全限速 ----------
_last_call: float = 0.0
_throttle_lock = threading.Lock()


def _throttle() -> None:
    """限速：确保任意两次 AKShare 调用间隔不小于 AKSHARE_RATE_LIMIT 秒（含随机抖动）。

    使用 threading.Lock 保证多线程并发场景下的全局限速正确。
    """
    global _last_call
    s = get_settings()
    jitter = random.uniform(0, s.AKSHARE_RATE_LIMIT * 0.3)
    gap = s.AKSHARE_RATE_LIMIT + jitter
    with _throttle_lock:
        need = _last_call + gap
        now = time.time()
        if now < need:
            time.sleep(need - now)
        _last_call = time.time()


# ---------- 数据源熔断（快速失败保护，2026-09-23 线上缺陷修复） ----------
# 缺陷：`GET /market/overview/rt` 单次请求含 ~21 次外呼；东财端点对本机**快速失败**
# （实测 0.19~0.57s 内 Disconnect / ProxyError），但 AKSHARE_RETRY(3) × 指数退避
# (1s/2s) × 全局限速(1.2s) 把**每个失败调用**放大到 ~3.6s ⇒ 端到端 ~24s，
# 恒定突破 5s 预算 ⇒ 三块本可成功的载荷被统一置为"实时数据源响应超时"。
# 对"快速失败"的正确解法是**熔断**（fail-fast），而非加大预算。
#
# 语义：仅对**连接类**失败计数（远端断开 / 代理错误 / 超时）；任一成功即复位。
# 键 = 被调函数的 "模块.名"（粒度到单个接口，东财各接口各自熔断，新浪指数独立）。
# 冷却期内该接口的外呼直接抛 DataSourceUnavailable，**不再走限速与重试**，
# 从而既省掉重试放大的时间，也不再往已不可达的源站发无谓请求。
_CONN_EXC_NAMES = frozenset({
    "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "TimeoutError",
    "ProxyError", "ConnectError", "ReadError", "ProtocolError", "ProtocolError_",
    "RemoteDisconnected", "RemoteProtocolError", "ServerDisconnectedError",
    "MaxRetryError", "ChunkedEncodingError", "SSLError",
})


def _is_connection_error(exc: BaseException) -> bool:
    """异常因果链中是否出现连接类失败（远端断开 / 代理错误 / 超时）。"""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ in _CONN_EXC_NAMES:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _breaker_key(func: Callable[..., Any]) -> str:
    """熔断键：到"单个 akshare 接口"的粒度（稳定、可读、无需注册表）。"""
    return f"{getattr(func, '__module__', '?')}.{getattr(func, '__name__', repr(func))}"


class _SourceBreaker:
    """按接口粒度统计连续失败并短路的熔断器（线程安全）。

    仅连接类失败计数（``_is_connection_error``）——schema 类异常
    （KeyError/ValueError，"接口变更"而非"源不可达"）不触发熔断，避免误伤。
    """

    def __init__(self) -> None:
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, key: str) -> bool:
        """冷却期内返回 True；冷却到期按"半开"放行下一次探测。"""
        with self._lock:
            until = self._open_until.get(key, 0.0)
            if until <= 0.0:
                return False
            if time.monotonic() >= until:
                self._open_until.pop(key, None)
                self._fails.pop(key, None)
                return False
            return True

    def record_success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._open_until.pop(key, None)

    def record_failure(self, key: str, exc: BaseException, *,
                       threshold: int, cooldown: float) -> None:
        if not _is_connection_error(exc):
            return
        with self._lock:
            n = self._fails.get(key, 0) + 1
            self._fails[key] = n
            if n >= max(1, threshold):
                self._open_until[key] = time.monotonic() + max(0.0, cooldown)

    def reset(self) -> None:
        """清空全部状态（测试隔离用；生产无调用）。"""
        with self._lock:
            self._fails.clear()
            self._open_until.clear()


_breaker = _SourceBreaker()


def reset_source_breaker() -> None:
    """清除熔断状态（供测试夹具在用例间隔离）。"""
    _breaker.reset()


def _breaker_enabled() -> bool:
    """读开关（getattr 兜底：测试会注入只含旧字段的 Settings 替身）。"""
    return bool(getattr(get_settings(), "AKSHARE_BREAKER_ENABLED", True))


def _breaker_threshold() -> int:
    return int(getattr(get_settings(), "AKSHARE_BREAKER_THRESHOLD", 1))


def _breaker_cooldown() -> float:
    return float(getattr(get_settings(), "AKSHARE_BREAKER_COOLDOWN_SECONDS", 15.0))


def _should_retry(state: Any) -> bool:
    """tenacity 重试判据（返回 True = 重试）：源已判定不可达时**立即停止重试**。

    ⚠️ 关键：必须对"**成功**"显式返回 **False**。tenacity 的 ``iter`` 在
    outcome 成功时**同样会**调用本判据
    （``if not (is_explicit_retry or self.retry(retry_state)): return fut.result()``），
    若成功时返回 True，则一次**成功**的结果会被当成"需要重试"，空转到
    ``stop_after_attempt`` 后抛 ``RetryError``（实测新浪指数：成功的 DataFrame
    被包成 ``RetryError[<Future ... finished returned DataFrame>]``）。
    默认的 ``retry_if_exception_type`` 之所以没这问题，正因为它在成功时返回 False。

    失败时：
    - 已抛 ``DataSourceUnavailable``（熔断快速失败）⇒ 不重试；
    - 该接口的断路器已打开（本次失败刚触发，或此前已打开）⇒ 不重试；
    - 其余 Exception 与改动前一致（全部重试）；非 Exception 的 BaseException 不重试。
    """
    outcome = getattr(state, "outcome", None)
    if outcome is None:
        return False
    if not getattr(outcome, "failed", True):  # 成功：绝不重试
        return False
    try:
        exc = outcome.exception()
    except Exception:  # noqa: BLE001 取不到异常时按"不重试"兜底
        return False
    if isinstance(exc, DataSourceUnavailable):
        return False
    # 与原 ``retry_if_exception_type((Exception,))`` 语义一致：非 Exception 的
    # BaseException（如 KeyboardInterrupt / SystemExit）不重试。
    if not isinstance(exc, Exception):
        return False
    if not _breaker_enabled():
        return True
    try:
        func = state.args[0]
    except Exception:  # noqa: BLE001 取不到被调函数时退回"重试"
        return True
    return not _breaker.is_open(_breaker_key(func))


def _retry_decorator() -> Any:
    """构造重试装饰器（读取配置，避免模块导入期依赖 Settings 单例）。"""
    return retry(
        stop=stop_after_attempt(max(1, get_settings().AKSHARE_RETRY)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=_should_retry,
        reraise=True,
    )


def _safe_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """限速 + 重试地执行一次 AKShare 调用（源已熔断时快速失败）。

    熔断短路在 ``_throttled_call`` 入口（= 每次尝试做的第一件事），故已确认不可达
    的源既不会走到 1.2s 限速、也不会发出外呼；``_should_retry`` 再保证它不被退避重试放大。
    """
    return _retry_decorator()(_throttled_call)(func, *args, **kwargs)


def _throttled_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """限速 + 熔断记录地执行一次外呼（重试由 ``_retry_decorator`` 负责）。"""
    key = _breaker_key(func)
    enabled = _breaker_enabled()
    if enabled and _breaker.is_open(key):
        # 冷却期内：既不再走 1.2s 限速，也不向已不可达的源发请求
        raise DataSourceUnavailable(f"数据源连续失败已熔断，冷却中：{key}")
    _throttle()
    try:
        out = func(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 连接类失败计数后原样上抛（改变异常类型会破坏换源/降级判断）
        if enabled:
            _breaker.record_failure(key, e, threshold=_breaker_threshold(),
                                    cooldown=_breaker_cooldown())
        raise
    if enabled:
        _breaker.record_success(key)
    return out


# ---------- 列名映射（东方财富日线接口） ----------
_RENAME_DAILY: dict[str, str] = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct",
    "涨跌额": "change",
    "换手率": "turnover",
}


def _standardize_daily(df: pd.DataFrame, code: str) -> pl.DataFrame:
    """把 AKShare 日线原始 DataFrame 规范化为 AQP 标准 Polars Schema。

    标准列：date(Date) / open,high,low,close,volume,amount(Float64)
            / symbol,code(String) / pct,turnover,amplitude,change(Float64, 若源提供)
    """
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.rename(columns={k: v for k, v in _RENAME_DAILY.items() if k in df.columns})
    if "date" not in df.columns:
        # Task 13：主源（东财）返回了**非空**响应却没有 date 列 —— 说明接口列名
        # 已变更或解析失败。抛带上下文的可读异常，避免下游 ``.sort("date")`` 抛出
        # 难以定位的 polars 列缺失错误；空响应（停牌/退市）已在上面正常返回空表。
        raise ValueError(
            f"东方财富日线响应缺 date 列（code={code}, rows={len(df)}, "
            f"实际列={list(df.columns)}）——主源接口可能已变更")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["code"] = code
    df["symbol"] = code_to_symbol(code)
    num_cols = ["open", "high", "low", "close", "volume", "amount", "pct", "turnover"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["source"] = "akshare"
    keep = [c for c in df.columns if c in {
        "date", "symbol", "code", "open", "high", "low", "close",
        "volume", "amount", "pct", "turnover", "amplitude", "change", "source",
    }]
    return pl.from_pandas(df[keep]).sort("date")


# ---------- 单只拉取 ----------
def _sina_symbol(code: str) -> str:
    """把 6 位代码转成新浪接口格式：600519 -> sh600519。"""
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    raise ValueError(f"无法识别的代码前缀: {code}")


def _fetch_daily_bar_em(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    """主源：东方财富 stock_zh_a_hist（中文列名）。"""
    df = _safe_call(
        _ak().stock_zh_a_hist,
        symbol=code,
        period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
    )
    return _standardize_daily(df, code)


def _missing_date_diagnostic(
    source: str, code: str, columns: Any, rows: int
) -> str:
    """构造"日线响应缺 date 列"的可读诊断串（含数据源/代码/实际列/行数）。

    消息刻意写清三要素，便于一眼区分是"数据源不覆盖"还是"接口变更"。
    """
    return (f"{source}日线响应缺 date 列（code={code}, rows={rows}, "
            f"实际列={list(columns)}）——可能该源不覆盖此标的、被限流或接口已变更")


def _fetch_daily_bar_sina(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    """备用源：新浪 stock_zh_a_daily（已是英文列名；北交所可能不支持，best-effort）。

    ⚠️ 健壮性（Task 13）：新浪对**新上市/无历史/被限流**的标的可能返回畸形或空响应体，
    此时 akshare 内部（``stock_zh_a_sina.py`` 解密后执行 ``data_df["date"]``）会抛**裸
    ``KeyError('date')``**，早于本函数的空值判断，最终向上冒泡污染失败统计。
    这里将该 KeyError 等价识别为「该源暂无此标的数据」，记 WARNING 诊断后返回空 DataFrame
    （空数据在下游按"无数据"处理，不算抓取故障）；**网络类异常（ConnectionError /
    超时等）一律不被拦截，正常向上抛出**。

    另外，若新浪正常返回了**非空** DataFrame 却缺 ``date`` 列（接口列名变更），同样按
    无数据处理并记录诊断，不抛裸 ``KeyError``。
    """
    symbol = _sina_symbol(code)
    try:
        df = _safe_call(
            _ak().stock_zh_a_daily,
            symbol=symbol,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust,
        )
    except KeyError as e:
        # akshare 内部因响应体异常（空/畸形）对缺失 date 字段抛裸 KeyError —— 按无数据处理
        logger.warning(
            f"[sina] {_missing_date_diagnostic('新浪', code, (), 0)}: "
            f"symbol={symbol} adjust={adjust!r} err={e!r} -> 按无数据处理")
        return pl.DataFrame()
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.copy()
    if "date" not in df.columns:
        logger.warning(
            f"[sina] {_missing_date_diagnostic('新浪', code, df.columns, len(df))}: "
            f"symbol={symbol} adjust={adjust!r} -> 按无数据处理")
        return pl.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["code"] = code
    df["symbol"] = code_to_symbol(code)
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["source"] = "akshare"
    keep = [c for c in df.columns if c in {
        "date", "symbol", "code", "open", "high", "low", "close",
        "volume", "amount", "pct", "turnover", "amplitude", "change", "source",
    }]
    return pl.from_pandas(df[keep]).sort("date")


def _fetch_daily_bar_tx(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    """备用源：腾讯 ``stock_zh_a_hist_tx``（仅当优先级含 ``tencent`` 时使用）。

    ⚠️ 腾讯返回列里名为 ``amount`` 的列**实为成交量（手）**（akshare
    ``stock_hist_tx.py:72``）⇒ 必须**重命名为 ``volume``**；``amount``/``turnover``
    /``pct`` 腾讯一方不提供 ⇒ **显式置 null**（如实降级，绝不用成交量冒充成交额）。

    ⚠️ 腾讯复权算法与东财**不同** ⇒ 若与本地东财 hfq/qfq 序列混列会造成假的复权
    因子跳变（口径告警，见 ``docs/audit-datasource-2026-09-26`` 增量设计方案 §2.1）。
    """
    df = _safe_call(
        _ak().stock_zh_a_hist_tx,
        symbol=_sina_symbol(code),   # 腾讯与新浪同格式：sh600519
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
    )
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.copy()
    if "date" not in df.columns:
        logger.warning(
            f"[tencent] {_missing_date_diagnostic('腾讯', code, df.columns, len(df))}: "
            f"adjust={adjust!r} -> 按无数据处理")
        return pl.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["code"] = code
    df["symbol"] = code_to_symbol(code)
    # 腾讯列 ``amount`` 实为成交量（手）-> 重命名为 volume；真成交额缺失
    df = df.rename(columns={"amount": "volume"})
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["amount"] = None       # 腾讯不提供成交额 ⇒ 如实 null（不伪造）
    df["turnover"] = None     # 腾讯不提供换手率 ⇒ 如实 null
    df["pct"] = None          # 腾讯不提供涨跌幅 ⇒ 如实 null
    df["amplitude"] = None
    df["change"] = None
    df["source"] = "tencent"
    keep = [c for c in df.columns if c in {
        "date", "symbol", "code", "open", "high", "low", "close",
        "volume", "amount", "pct", "turnover", "amplitude", "change", "source",
    }]
    return pl.from_pandas(df[keep]).sort("date")


# ---------- 源优先级（D1-alt：用户可用 .env 切换「腾讯优先」） ----------
_DEFAULT_DAILY_PRIORITY = "eastmoney,sina,baostock"

# 已知源名 -> 本模块内的抓取函数**属性名**（不直接持有函数对象：
# 持有对象会让 monkeypatch.setattr(ada, "_fetch_daily_bar_em", ...) 失效，
# 测试会绕过打桩直连真实网络；故在调用时按名查 globals()）。
_DAILY_FETCHER_ATTRS: dict[str, str] = {
    "eastmoney": "_fetch_daily_bar_em",
    "sina": "_fetch_daily_bar_sina",
    "tencent": "_fetch_daily_bar_tx",
}


# 口径告警只打一次（进程级），避免每次抓取刷屏
_PRIORITY_WARNED: set[str] = set()


def _warn_priority_once(names: list[str]) -> None:
    """首源为腾讯时告警一次：腾讯损口径且与本地东财复权基准不兼容。"""
    if not names or names[0] != "tencent" or "tencent" in _PRIORITY_WARNED:
        return
    _PRIORITY_WARNED.add("tencent")
    logger.warning(
        "数据源配置 AQP_DAILY_SOURCE_PRIORITY 以 tencent 为首源：腾讯 stock_zh_a_hist_tx "
        "**不提供成交额与换手率**（其 amount 列实为成交量），且复权算法与东财/新浪不同 ⇒ "
        f"与本地存量东财口径不兼容，可能造成复权序列跳变。当前优先级={names}。")


def daily_source_priority() -> list[str]:
    """读取管线日线源优先级（逗号分隔、按序降级）。

    ``getattr`` 兜底：测试会注入只含旧字段的 Settings 替身，与 ``_breaker_enabled``
    同一模式；缺字段/空串时回退默认 ``"eastmoney,sina,baostock"``。
    """
    raw = getattr(get_settings(), "AQP_DAILY_SOURCE_PRIORITY", _DEFAULT_DAILY_PRIORITY)
    names = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
    names = names or [_DEFAULT_DAILY_PRIORITY]
    _warn_priority_once(names)
    return names


def _resolve_daily_source(
    name: str,
) -> Callable[[str, str, str, str], pl.DataFrame] | None:
    """按源名解析抓取函数；``baostock`` 惰性 import（未安装返回 ``None``）。

    解析**发生在调用时**（``globals()`` / 模块属性），因此对 ``ada._fetch_daily_bar_*``
    或 ``baostock_adapter.fetch_daily_bar_bs`` 的 monkeypatch 都能生效——这是测试
    「不发真实网络」的前提（持有函数对象的 dict 会让打桩静默失效）。
    """
    if name == "baostock":
        from . import baostock_adapter as bsa

        return bsa.fetch_daily_bar_bs if bsa.is_available() else None
    attr = _DAILY_FETCHER_ATTRS.get(name)
    if attr is None:
        return None
    return globals().get(attr)


def fetch_daily_bar(code: str, start: str, end: str, adjust: str = "") -> pl.DataFrame:
    """拉取单只股票日线，返回标准 Polars DataFrame。

    多源降级链（按 ``AQP_DAILY_SOURCE_PRIORITY`` 顺序，默认
    ``东财 → 新浪 → BaoStock``）：主源东方财富 ``stock_zh_a_hist`` 失败
    （东财对部分网络/IP 直接断连）自动降级新浪 ``stock_zh_a_daily``；两者都拿不到
    再降级 BaoStock（**仅当 ``is_available()``**，即已安装）。用户把该配置改为
    ``"tencent,eastmoney,sina,baostock"`` 即启用「腾讯优先」（口径风险自担）。

    ⚠️ 缺失语义（Task 13）：数据源不覆盖 / 无历史（如新上市标的）时返回**空 DataFrame**
    （下游按"无数据"处理，不算故障）；只有全部源都以异常失败才会**抛异常**
    （抛 :class:`DataSourceUnavailable`，→ 标准信封 ``code=51000``），不会静默吞掉。

    ⚠️ 复权口径：BaoStock 用「涨跌幅复权法」，与东财/新浪不同 ⇒ ``source==baostock``
    的行不得与其它源混列（守卫见 ``ingest/tasks.write_daily_bars``）。

    :param code:   6 位纯数字代码，如 "600519"
    :param start:  起始日期 "YYYYMMDD" 或 "YYYY-MM-DD"
    :param end:    结束日期
    :param adjust: "" 不复权 / "qfq" 前复权 / "hfq" 后复权
    """
    errs: list[str] = []
    saw_empty = False
    for name in daily_source_priority():
        fn = _resolve_daily_source(name)
        if fn is None:
            # 未知源名 / baostock 未安装 —— 直接跳过（不等价于失败）
            logger.debug(f"[daily] source '{name}' 不可用或未知，跳过")
            continue
        try:
            df = fn(code, start, end, adjust)
        except Exception as e:  # noqa: BLE001 换源：异常只进日志与聚合
            logger.warning(
                f"daily fetch via {name} fail {code} adjust={adjust!r}: "
                f"{e!r} -> next source")
            errs.append(f"{name}:{type(e).__name__}")
            continue
        if df is not None and not df.is_empty():
            return df
        # 空结果 = 该源不覆盖此标的（无历史/停牌）——记录后继续尝试后续源；
        # 若最终所有源都空 ⇒ 返回空（无数据，不算故障）。
        saw_empty = True
    if saw_empty:
        logger.debug(f"[daily] {code} adjust={adjust!r} 所有源均无数据，返回空")
        return pl.DataFrame()
    raise DataSourceUnavailable(
        f"日线全部数据源失败 {code} adjust={adjust!r}: {'; '.join(errs) or '无可用源'}"
    )


# ---------- 交易日历 ----------
def fetch_trade_calendar(start: str = "20000101", end: str | None = None) -> pd.DataFrame:
    """拉取 A 股交易日历，返回 DataFrame[trade_date, is_sh, is_sz, is_bj, week]。

    end=None 时保留数据源返回的全部日期（新浪源通常含当年剩余交易日）。
    ⚠️ 不能默认截断到"今天"：日历被截断后 prev_trade_day 会停在旧日期，
    增量同步目标日随之冻结，新交易日行情永远同步不进来。
    显式传 end（如 CLI 指定截止日）时才做上界过滤。
    """
    df = _safe_call(_ak().tool_trade_date_hist_sina)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    lo = datetime.strptime(start, "%Y%m%d").date()
    if end is not None:
        hi = datetime.strptime(end, "%Y%m%d").date()
        df = df[(df["trade_date"] >= lo) & (df["trade_date"] <= hi)].copy()
    else:
        df = df[df["trade_date"] >= lo].copy()
    df = df.assign(is_sh=True, is_sz=True, is_bj=True)
    df["week"] = pd.to_datetime(df["trade_date"]).dt.weekday + 1
    return df[["trade_date", "is_sh", "is_sz", "is_bj", "week"]].reset_index(drop=True)


# ---------- 证券列表 ----------
def fetch_stock_list() -> pl.DataFrame:
    """拉取 A 股 + 北交所证券列表，统一为标准 Schema。"""
    df = _safe_call(_ak().stock_info_a_code_name)
    df = df.rename(columns={"code": "code", "name": "name"})
    df["symbol"] = df["code"].apply(code_to_symbol)
    df["market"] = df["symbol"].str.split(".").str[1]
    df["instrument_type"] = "stock"
    # ST 判定：证券简称含 "ST"（覆盖 ST/*ST/S*ST），名称来源真实、口径可解释
    df["is_st"] = df["name"].str.upper().str.contains("ST", na=False)
    return pl.from_pandas(
        df[["code", "symbol", "name", "market", "instrument_type", "is_st"]])


# ---------- 指数日线（新浪源） ----------
# 核心指数：新浪代码 -> 展示名
CORE_INDICES: list[tuple[str, str]] = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
]


def fetch_index_daily(index_code: str) -> pl.DataFrame:
    """拉取指数日线（新浪源，返回全部历史，按 date 升序）。

    :param index_code: 新浪指数代码，如 "sh000001" / "sz399006"
    """
    df = _safe_call(_ak().stock_zh_index_daily, symbol=index_code)
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return pl.from_pandas(df).sort("date")


# ---------- 退市名单（Task 6） ----------
def fetch_delist_list() -> pl.DataFrame:
    """拉取沪深已退市证券名单，统一为 [code, name, list_date, delist_date, source]。

    ⚠️ 语义差异：深交所源提供"终止上市日期"（真实退市日）；上交所源只提供
    "暂停上市日期"（≤ 终止上市日），作为退市时点的近似——用于移出宇宙时
    会略早于真实退市，对强平减记是保守方向（更早按更接近真实的纸面价清算）。

    两个源都自带"上市日期"，故一并联回 ``list_date``（缺陷 B5-14：``instrument.
    list_date`` 实测 97.8% 为 NULL）。⚠️ 这只是**部分**回填：名单只覆盖**已退市**
    标的，在册标的那部分仍需别的来源（``scripts/enrich_instruments.py`` 从本地
    行情最早日近似）。源缺该列时如实为 null，绝不造数。
    """
    sh = _safe_call(_ak().stock_info_sh_delist)
    sz = _safe_call(_ak().stock_info_sz_delist, symbol="终止上市公司")
    frames: list[pl.DataFrame] = []
    for df, code_col, name_col, date_col, list_col, src in (
        (sh, "公司代码", "公司简称", "暂停上市日期", "上市日期", "akshare_sh_delist"),
        (sz, "证券代码", "证券简称", "终止上市日期", "上市日期", "akshare_sz_delist"),
    ):
        if df is None or df.empty:
            continue
        missing = {code_col, name_col, date_col} - set(df.columns)
        if missing:
            raise ValueError(f"退市名单源 {src} 缺少列 {missing}，接口可能已变更")
        pdf = df.rename(columns={
            code_col: "code", name_col: "name", date_col: "delist_date"}).copy()
        if list_col in pdf.columns:
            pdf = pdf.rename(columns={list_col: "list_date"})
        else:
            pdf["list_date"] = None  # 源未提供 ⇒ 如实空列
        # 源列可能是 str / datetime64 / datetime.date 混杂，统一在 pandas 侧归一
        pdf["delist_date"] = pd.to_datetime(pdf["delist_date"], errors="coerce").dt.date
        pdf["list_date"] = pd.to_datetime(pdf["list_date"], errors="coerce").dt.date
        pdf["code"] = pdf["code"].astype(str).str.strip()
        std = pl.from_pandas(pdf[["code", "name", "list_date", "delist_date"]])
        std = std.with_columns([pl.col("list_date").cast(pl.Date),
                                pl.col("delist_date").cast(pl.Date)])
        std = std.with_columns(pl.lit(src).alias("source")).drop_nulls("delist_date")
        frames.append(std)
    if not frames:
        return pl.DataFrame(schema={"code": pl.Utf8, "name": pl.Utf8,
                                    "list_date": pl.Date, "delist_date": pl.Date,
                                    "source": pl.Utf8})
    return pl.concat(frames, how="vertical_relaxed").unique(subset=["code"],
                                                            keep="first")
