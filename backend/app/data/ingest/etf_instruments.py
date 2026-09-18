"""ETF 目录 → ``instrument`` 表（幂等 upsert）。

根因（审计：ETF 搜索永久搜不到）
--------------------------------
``instrument`` 表实测只有股票行（``('stock', 5552)``，``etf`` = 0 行），
因为入库链路只写股票目录：
    - ``data/ingest/akshare_adapter.py::fetch_stock_list`` 把 ``instrument_type``
      **硬编码为 "stock"``；
    - ``data/ingest/__main__.py::_stage_instruments`` 只 upsert 股票目录。
于是 ``api/v1/portfolio.py::_search_assets`` 查不到任何 ETF —— 实测搜 ``510300``
返回 0 条。**根因在入库，不在查询。**

本模块复用 ``data/etf.py::build_catalog``（中国全量实时目录 + 美/日/韩静态目录，
不引入新的外部依赖），把 ETF 落进 ``instrument`` 表（``instrument_type='etf'``），
并复用与股票目录相同的 ``INSERT ... ON CONFLICT(symbol) DO UPDATE`` 幂等 upsert。

⚠️ 一次性真实回填需联网跑：见 ``ingest/__main__.py`` 的 ``--stage etf-instruments``。
"""
from __future__ import annotations

import polars as pl
from loguru import logger

#: ``instrument`` 表需要的列（与 ``akshare_adapter.fetch_stock_list`` 输出一致）。
_COLUMNS: tuple[str, ...] = ("code", "symbol", "name", "market", "instrument_type", "is_st")

_EMPTY_SCHEMA: dict[str, pl.DataType] = {
    "code": pl.String,
    "symbol": pl.String,
    "name": pl.String,
    "market": pl.String,
    "instrument_type": pl.String,
    "is_st": pl.Boolean,
}


def cn_etf_symbol(code: str) -> tuple[str, str]:
    """6 位 ETF 代码 → ``(标准 symbol, market)``。

    交易所前缀规则（按 ETF 实际代码段，**不可**直接用 ``domain.a_share_rules.
    code_to_symbol``：后者不认 ``1xxxxx``（深市 ETF），会误判为 ``.SH``）：
        5/6/9 开头 → ``.SH``（51/56/58xxxx 沪市 ETF）
        0/1/2/3 开头 → ``.SZ``（15/16xxxx 深市 ETF）
        4/8 开头 → ``.BJ``

    Args:
        code: 6 位数字代码（如 ``510300`` / ``159915``）。

    Returns:
        ``(symbol, market)``；非 6 位数字代码退化为 ``(code, "CN")``（不造后缀）。
    """
    c = str(code).strip()
    if len(c) == 6 and c.isdigit():
        if c.startswith(("5", "6", "9")):
            return f"{c}.SH", "SH"
        if c.startswith(("0", "1", "2", "3")):
            return f"{c}.SZ", "SZ"
        if c.startswith(("4", "8")):
            return f"{c}.BJ", "BJ"
        return f"{c}.SH", "SH"
    return c, "CN"


def build_etf_instrument_frame(catalog: list[dict] | None = None) -> pl.DataFrame:
    """把 ETF 目录映射为 ``instrument`` 表行。

    Args:
        catalog: 可选，注入目录（测试用，避免触网）。缺省调用
            ``app.data.etf.build_catalog()``（中国全量实时目录 + 境外静态目录）。

    Returns:
        ``pl.DataFrame``，列 = :data:`_COLUMNS`；目录为空时返回**同 schema 的空表**
        （便于上层判断并跳过写入，绝不写入伪造行）。
    """
    if catalog is None:
        from ..etf import build_catalog  # 惰性导入：避免 import 期触网

        catalog = build_catalog()

    codes: list[str] = []
    symbols: list[str] = []
    names: list[str] = []
    markets: list[str] = []
    seen: set[str] = set()

    for entry in catalog or []:
        code = str(entry.get("code") or "").strip()
        if not code:
            continue
        country = str(entry.get("country") or "cn").lower()
        if country == "cn":
            symbol, market = cn_etf_symbol(code)
        else:
            symbol, market = code, country.upper()
        if symbol in seen:  # 目录内去重（同一 symbol 只保留首见）
            continue
        seen.add(symbol)
        codes.append(code)
        symbols.append(symbol)
        names.append(str(entry.get("name") or code))
        markets.append(market)

    if not symbols:
        return pl.DataFrame(schema=_EMPTY_SCHEMA)

    return pl.DataFrame({
        "code": codes,
        "symbol": symbols,
        "name": names,
        "market": markets,
        "instrument_type": ["etf"] * len(symbols),
        "is_st": [False] * len(symbols),
    })


async def upsert_etf_instruments(catalog: list[dict] | None = None) -> int:
    """把 ETF 目录**幂等**写入 ``instrument`` 表，返回写入行数。

    依赖 :func:`data.ingest.tasks.upsert_instruments`（``ON CONFLICT(symbol)
    DO UPDATE``），重复执行不产生重复行。

    Args:
        catalog: 可选，注入目录（测试用）。

    Returns:
        写入（含更新）行数；目录为空时返回 0 且不写库。
    """
    from .tasks import upsert_instruments

    frame = build_etf_instrument_frame(catalog)
    if frame.is_empty():
        logger.warning("[etf-instruments] ETF 目录为空，跳过 upsert（不写入任何伪造行）")
        return 0
    n = await upsert_instruments(frame)
    logger.info(f"[etf-instruments] upsert {n} 行（instrument_type='etf'）")
    return n
