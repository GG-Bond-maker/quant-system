"""市场概览的数据聚合服务（Task 16 下沉）。

_build_money_flow 自 api/v1/market.py 平移（纯移动，行为不变）：
北向资金 / 大盘主力净流入 / 行业板块主力-散户结构，各自独立容错，
任一块失败降级而不阻塞其他块。外呼统一走 akshare_adapter._safe_call
（全局限速 + 重试），不再自建重试包装绕过全局限速。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

from ..data.ingest.akshare_adapter import get_akshare, _safe_call
from ..data import realtime as _realtime

# 东财沪深港通「交易状态」取值语义（`stock_hsgt_fund_flow_summary_em` 的
# quoteColumns `status~07~BOARD_CODE` 直出，akshare 1.16.72 原样透传）。
#
# 实测证据（2026-10-01 国庆休市，本机 live 调用该接口）：
#   交易日=2026-10-01 时**四个板块**（沪股通/深股通/港股通(沪)/港股通(深)）
#   `交易状态` 全部 = 4；且北向 `资金净流入 = 0.0`（**假 0**），而南向 = 420.0
#   （同表南向为真实更新值）。⇒ 当天 A 股休市，北向 0.0 是"接口占位默认值"
#   而非真实资金零流入。若不读该列，`north=0.0` 会被当成真实值上抛，
#   前端 `MoneyFlowPanel` 渲染成「+0亿」并染红（t-up）——把"数据不存在"
#   说成了"资金恰好持平"。
#
# ⚠️ 保守策略：东财未公开该字段的完整码表，故**只**把已确认的不可计量态
#   列入下表；**未知取值一律放行**（保持原行为）——宁可漏判（把一次休市当
#   交易日），也绝不把真实交易日的数字误判成不可得（后者是更严重的错误）。
_NORTH_NON_TRADING_STATUS: dict[int, str] = {
    4: "休市/未开盘",
}


def _non_trading_status_reason(raw_status: Any) -> str | None:
    """判定该方向当前是否处于**非交易态**（不可计量）。

    返回 `None` 表示"可正常计量或取值未知"（放行）；否则返回人读原因串
    （如 ``"休市/未开盘"``），供调用方写入 `errs` 披露降级理由。

    ``raw_status`` 取值形态不定（源可能给 int/float/numpy 标量/字符串），
    故先 ``float()`` 再 ``int()`` 收敛；非数值（含 ``NaN``）⇒ 未知 ⇒ 保守放行。
    """
    try:
        code = int(float(raw_status))
    except (TypeError, ValueError):
        return None  # 非数值（含 NaN）⇒ 未知取值 ⇒ 保守放行
    return _NORTH_NON_TRADING_STATUS.get(code)


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
        # 缺陷 A（2026-10-01 实测）：休市日该接口 `资金净流入` 返回 0.0（占位），
        # 必须读 `交易状态` 把"非交易态"识别出来。此时**不得** sum 出 0 冒充真实值
        # ——否则 `north=0.0` 会被上层当成成功值（n_ok 不降、block 仍 ok），
        # 前端渲染成红色「+0亿」。改为 north 保持 None 并记 errs，让 block 走
        # degraded、前端渲染中性的「—」且 reason 如实披露。
        if "交易状态" in north_df.columns:
            status_raw = north_df["交易状态"].iloc[0]
            nt_reason = _non_trading_status_reason(status_raw)
            if nt_reason is not None:
                raise ValueError(f"非交易态(交易状态={status_raw}:{nt_reason})")
        north = round(float(pd.to_numeric(north_df[north_cols[0]], errors="coerce").sum()), 2)
    except Exception as e:  # noqa: BLE001 单子源失败不得拖垮其余子源
        logger.debug(f"[overview] north flow degraded: {e!r}")
        # ⚠️ 必须带上 `e` 的**消息**而不只是异常类名：本块抛的是带口径信息的
        # `ValueError("非交易态(交易状态=4:休市/未开盘)")`，只写 `type(e).__name__`
        # 会把原因压成 `north:ValueError`，而这条 errs 会经 `reason` 原样透传到前端
        # ——把它降级成"ValueError"等于**把可解释的降级变回不可解释**。
        # （其余 raise 点同理：都在消息里带了可自证口径的上下文。）
        errs.append(f"north:{e}")
    try:
        # 2026-10-01（审计 F1）：改用 push2test 首选的多源适配器。
        # 原先 `ak().stock_market_fund_flow()` 内部硬编码 `push2his`（本机被阻断），
        # 数据其实完全可得 ⇒ "取得到却取不到"的真数据丢失。
        # ⚠️ 必须走**模块属性**（`_realtime.fetch_market_fund_flow`）而非模块级
        # `from ... import`：与同文件 `get_akshare` 的约定一致，且保证单测可
        # monkeypatch（直接 import 的符号会绕过 patch，导致测试打到真实网络 ⇒
        # 子源"失败"注入失效，测试假绿/假红）。同型坑见 CLAUDE.md「可达≠有入口」旁。
        mf_rows = _realtime.fetch_market_fund_flow()
        main_yuan = pd.to_numeric(
            pd.Series([r.get("主力净流入-净额") for r in mf_rows]), errors="coerce")
        if main_yuan.notna().sum() == 0:
            raise ValueError("大盘资金流无有效净额观测")
        main = round(float(main_yuan.dropna().iloc[-1]) / 1e8, 2)
    except Exception as e:
        logger.debug(f"[overview] main flow degraded: {e!r}")
        errs.append(f"main:{e}")

    # 行业板块资金结构：主力（超大单+大单）/ 散户（中单+小单），供双向柱状图
    sector_flows: list[dict] = []
    try:
        # 2026-10-01（审计 F1）：同上，改用 push2test 首选的板块资金流适配器
        # （原 `ak().stock_sector_fund_flow_rank` 内部硬编码 `push2`，本机被阻断）。
        # 同样走模块属性以保留 monkeypatch 能力。
        sf_rows = _realtime.fetch_sector_fund_flow("行业资金流")
        sdf = pd.DataFrame(sf_rows)
        num = lambda c: pd.to_numeric(sdf[c], errors="coerce")  # noqa: E731
        # f62=主力净额, f66=超大单, f72=大单, f78=中单, f84=小单（元）
        sdf = sdf.assign(
            _main=num("f66").fillna(0) + num("f72").fillna(0),
            _retail=num("f78").fillna(0) + num("f84").fillna(0),
        )
        sdf = sdf.assign(_total=sdf["_main"] + sdf["_retail"]).sort_values(
            "_total", ascending=False).head(8)
        sector_flows = [
            {"name": str(r["f14"]),
             "main_yi": round(float(r["_main"]) / 1e8, 1),
             "retail_yi": round(float(r["_retail"]) / 1e8, 1),
             "total_yi": round(float(r["_total"]) / 1e8, 1)}
            for _, r in sdf.iterrows()
        ]
        # 调用**成功**即算该子源可达（今日恰好 0 行 ≠ 数据源故障）
        sector_ok = True
    except Exception as e:
        logger.debug(f"[overview] sector fund flow degraded: {e!r}")
        errs.append(f"sector:{e}")

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
