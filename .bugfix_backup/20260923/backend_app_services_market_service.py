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
    """北向资金 + 大盘主力净流入 + 行业板块主力/散户净流入结构（各自独立容错）。

    **状态三态（审计 P1-30，2026-09-21 修正）**：
        - `ok`         ：三个子源**全部**成功；
        - `degraded`   ：**部分**成功（1~2 个）——必须带 `reason` 与 `n_ok`；
        - `unavailable`：三个**全部**失败。

    ⚠️ 原实现只有两态：只要**任一个**子源成功就返回 `status="ok"` 且**不带
    `reason`**。而消费方 `api/v1/market.py` 的 `degraded` 判据是
    `block.status in ("degraded", "unavailable")` ⇒ 三个子块只成功一个时
    `data_freshness` 仍报 `fresh`，**外部源降级被当健康**，契约三态退化为两态
    （本项是审核中该缺陷的唯一实例）。
    """
    ak = get_akshare
    north: float | None = None
    main: float | None = None
    sector_ok = False
    errs: list[str] = []
    try:
        df = _safe_call(ak().stock_hsgt_fund_flow_summary_em, )
        north_cols = [c for c in df.columns if "净流入" in str(c)]
        if not north_cols:
            raise ValueError(f"未找到净流入列: {list(df.columns)[:8]}")
        # 审计 P1-33：`stock_hsgt_fund_flow_summary_em` 是**双向汇总表**
        # （列 `资金方向` = 北向/南向），原实现对全表 sum ⇒ 港股通(南向)被计入
        # "北向净流入"（B7a 实测 840.0 亿 vs 真实北向 0.0，直接显示在前端卡片）。
        # 必须按方向过滤后再求和；无法确认方向列时不静默全表求和（口径不可验证
        # ⇒ 记入 errs 走 degraded，而不是给出一个无法自证口径的数字）。
        if "资金方向" not in df.columns:
            raise ValueError(f"未找到资金方向列: {list(df.columns)[:8]}")
        direction = df["资金方向"].astype(str).str.strip()
        north_df = df[direction == "北向"]
        if north_df.empty:
            raise ValueError("资金方向列无'北向'行，无法确认北向口径")
        north = round(float(pd.to_numeric(north_df[north_cols[0]], errors="coerce").sum()), 2)
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
        # 调用**成功**即算该子源可达（今日恰好 0 行 ≠ 数据源故障）
        sector_ok = True
    except Exception as e:
        logger.debug(f"[overview] sector fund flow degraded: {e!r}")
        errs.append(f"sector:{type(e).__name__}")

    # 审计 P1-30：按**子源个数**判定三态（此前只要有一个成功就报 ok）
    n_ok = (1 if north is not None else 0) + (1 if main is not None else 0) \
        + (1 if sector_ok else 0)
    if n_ok == 0:
        return {"status": "unavailable", "reason": "; ".join(errs) or "无资金数据"}
    payload = {"north_net_today": north, "main_net_today": main,
               "sector_flows": sector_flows}
    if n_ok < 3:
        # 部分可用：交付已拿到的数据，但**必须**让消费方看到 degraded 与原因，
        # 否则 `data_freshness` 会把它当 fresh（P1-30 的本体）
        return {"status": "degraded", "n_ok": n_ok,
                "reason": f"资金源部分可用({n_ok}/3)："
                          f"{'; '.join(errs) or '返回空数据'}",
                **payload}
    return {"status": "ok", "n_ok": n_ok, **payload}


# 兼容别名：market.py 内部历史名
_build_money_flow = build_money_flow
