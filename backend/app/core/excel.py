"""P2-8 Excel 导出（openpyxl）：Screener Top-N / 回测交易明细与每日持仓。"""
from __future__ import annotations

import io
from datetime import date as date_cls
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter


def _sheet(wb: Workbook, title: str, headers: list[str],
           rows: list[list[Any]]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r[h] if isinstance(r, dict) else r[i] for i, h in enumerate(headers)]
                  if isinstance(r, dict) else r)
    # 列宽：表头与内容粗略自适应（14~30）
    for i, h in enumerate(headers, 1):
        width = max(14, min(30, len(str(h)) * 2 + 6))
        ws.column_dimensions[get_column_letter(i)].width = width


def screener_workbook(items: list[dict[str, Any]], meta: dict[str, Any]) -> bytes:
    """Screener 导出：与页面榜单同口径
    rank/symbol/name/industry/close/pct/turnover/amount/limit_pct/score/signal_strength
    （P2-9：删除从未有数据的 pred_return/prob_up/confidence 三列）。
    [AQP 全链路改名] 末列字段名为 signal_strength（旧 risk），与 ScreenerItem 同口径。"""
    wb = Workbook()
    wb.remove(wb.active)
    headers = ["rank", "symbol", "name", "industry", "close", "pct",
               "turnover", "amount", "limit_pct", "score", "signal_strength"]
    rows = []
    for i, it in enumerate(items, 1):
        rows.append([i, it.get("symbol"), it.get("name"), it.get("industry"),
                     it.get("close"), it.get("pct"), it.get("turnover"),
                     it.get("amount"), it.get("limit_pct"), it.get("score"),
                     it.get("signal_strength")])
    _sheet(wb, "screener", headers, rows)
    _sheet(wb, "meta", ["key", "value"],
           [[k, str(v)] for k, v in meta.items()])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def backtest_workbook(trades: list[dict[str, Any]],
                      holdings: list[dict[str, Any]]) -> bytes:
    """回测导出：交易明细 + 每日持仓两个 sheet，日期统一 YYYY-MM-DD 字符串。"""
    wb = Workbook()
    wb.remove(wb.active)

    def fmt_date(v: Any) -> str:
        if isinstance(v, date_cls):
            return v.isoformat()
        return str(v)[:10]

    trade_headers = ["date", "symbol", "side", "price", "qty", "amount", "cost", "reason"]
    trade_rows = [[fmt_date(t.get("date")), t.get("symbol"), t.get("side"),
                   t.get("price"), t.get("qty"), t.get("amount"), t.get("cost"),
                   t.get("reason")] for t in trades]
    _sheet(wb, "trades", trade_headers, trade_rows)

    hold_rows = []
    for h in holdings:
        d = fmt_date(h.get("date"))
        for sym, qty in (h.get("holdings") or {}).items():
            hold_rows.append([d, sym, qty])
    _sheet(wb, "holdings", ["date", "symbol", "qty"], hold_rows)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def strategy_backtest_workbook(trades: list[dict[str, Any]],
                               nav_curve: list[dict[str, Any]],
                               meta: dict[str, Any]) -> bytes:
    """策略回测导出：交易明细 + 净值曲线 + 参数快照三个 sheet。

    与 :func:`backtest_workbook`（Top-K 调仓回测，trades+holdings）区分开：
    策略回测没有逐日 holdings，而有逐日净值曲线，二者不可混用，
    否则导出的内容与页面展示对不上。

    :param trades:     交易明细 [{date, symbol, side, price, qty, fee, pnl}]
    :param nav_curve:  净值曲线 [{date, strategy, benchmark}]
    :param meta:       参数快照（区间/资金/费率/均线/止损等）
    :return:           xlsx 字节流
    """
    wb = Workbook()
    wb.remove(wb.active)

    trade_headers = ["date", "symbol", "side", "price", "qty", "fee", "pnl"]
    trade_rows = [[str(t.get("date"))[:10], t.get("symbol"), t.get("side"),
                   t.get("price"), t.get("qty"), t.get("fee"), t.get("pnl")]
                  for t in trades]
    _sheet(wb, "trades", trade_headers, trade_rows)

    nav_rows = [[str(p.get("date"))[:10], p.get("strategy"), p.get("benchmark")]
                for p in nav_curve]
    _sheet(wb, "nav", ["date", "strategy", "benchmark"], nav_rows)

    _sheet(wb, "meta", ["key", "value"],
           [[k, str(v)] for k, v in meta.items()])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
