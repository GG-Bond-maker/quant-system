"""市场概览的数据聚合服务（Task 16 下沉）。

_build_money_flow 自 api/v1/market.py 平移（纯移动，行为不变）：
北向资金 / 大盘主力净流入 / 行业板块主力-散户结构，各自独立容错，
任一块失败降级而不阻塞其他块。外呼统一走 akshare_adapter._safe_call
（全局限速 + 重试），不再自建重试包装绕过全局限速。
"""
from __future__ import annotations

import pandas as pd
from loguru import logger

from ..data.ingest.akshare_adapter import get_akshare, _safe_call


def build_money_flow() -> dict:
    """北向资金 + 大盘主力净流入 + 行业板块主力/散户净流入结构（各自独立容错）。"""
    ak = get_akshare
    north: float | None = None
    main: float | None = None
    errs: list[str] = []
    try:
        df = _safe_call(ak().stock_hsgt_fund_flow_summary_em, )
        north_cols = [c for c in df.columns if "净流入" in str(c)]
        if not north_cols:
            raise ValueError(f"未找到净流入列: {list(df.columns)[:8]}")
        north = round(float(pd.to_numeric(df[north_cols[0]], errors="coerce").sum()), 2)
    except Exception as e:
        logger.debug(f"[overview] north flow degraded: {e!r}")
        errs.append(f"north:{type(e).__name__}")
    try:
        mf = _safe_call(ak().stock_market_fund_flow, )
        main = round(
            float(pd.to_numeric(mf["主力净流入-净额"], errors="coerce").iloc[-1]) / 1e8, 2)
    except Exception as e:
        logger.debug(f"[overview] main flow degraded: {e!r}")
        errs.append(f"main:{type(e).__name__}")

    # 行业板块资金结构：主力（超大单+大单）/ 散户（中单+小单），供双向柱状图
    sector_flows: list[dict] = []
    try:
        sf = _safe_call(ak().stock_sector_fund_flow_rank, indicator="今日", sector_type="行业资金流")
        num = lambda c: pd.to_numeric(sf[c], errors="coerce")  # noqa: E731
        sf = sf.assign(
            _main=num("超大单净流入-净额").fillna(0) + num("大单净流入-净额").fillna(0),
            _retail=num("中单净流入-净额").fillna(0) + num("小单净流入-净额").fillna(0),
        )
        sf = sf.assign(_total=sf["_main"] + sf["_retail"]).sort_values("_total", ascending=False).head(8)
        sector_flows = [
            {"name": str(r["名称"]),
             "main_yi": round(float(r["_main"]) / 1e8, 1),
             "retail_yi": round(float(r["_retail"]) / 1e8, 1),
             "total_yi": round(float(r["_total"]) / 1e8, 1)}
            for _, r in sf.iterrows()
        ]
    except Exception as e:
        logger.debug(f"[overview] sector fund flow degraded: {e!r}")

    if north is None and main is None and not sector_flows:
        return {"status": "unavailable", "reason": "; ".join(errs) or "无资金数据"}
    return {"status": "ok", "north_net_today": north, "main_net_today": main,
            "sector_flows": sector_flows}


# 兼容别名：market.py 内部历史名
_build_money_flow = build_money_flow
