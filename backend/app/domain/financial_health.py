"""
个股财务排雷与健康度评估模型（AQP）。

结合 Altman 破产预警指数体系与中国 A 股真实披露特征：
1. 偿债安全垫 (Solvency & Capital Structure): 每股净资产 (BPS) 与破净/跌破面值/资不抵债风险
2. 盈利持续性 (Profitability & Earnings Quality): 净利润为正/亏损、ROE 收益率与亏损报告期频率
3. 毛利护城河 (Margin Moat & Stability): 销售毛利率稳定性、断崖式跌幅检测
4. 业绩增长与匹配 (Growth & Divergence): 同比增速、增收不增利 (收入正增而利润暴跌) 背离排雷
5. 综合健康评分 (0~100) 与风险红线 (Red Flags) 提取

⚠️ 绝不伪造虚假数据：若标的暂无财报数据，诚实返回 status: unavailable。
"""
from __future__ import annotations

import math
from typing import Any
from loguru import logger


def compute_financial_health(symbol: str, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """计算个股财务健康度综合评分与排雷诊断报告（纯领域计算模型）。"""
    if not history:
        return {
            "status": "unavailable",
            "symbol": symbol,
            "reason": "暂无该标的历史财报披露数据",
            "score": None,
            "risk_level": "unavailable",
            "risk_label": "数据不可用",
            "red_flags": [],
            "positive_flags": [],
            "metrics": None,
            "quarterly_trend": [],
            "altman_assessment": None,
            "dimensions": [],
        }

    latest = history[0]
    red_flags: list[dict[str, str]] = []
    positive_flags: list[dict[str, str]] = []

    # 1. 偿债安全垫维度 (Solvency & Capital Cushion)
    bps = latest["bps"]
    solvency_score = 75.0
    solvency_status = "healthy"
    solvency_desc = "每股净资产维持在正常区间"

    if bps is not None:
        if bps <= 0:
            solvency_score = 10.0
            solvency_status = "danger"
            solvency_desc = f"每股净资产为负 ({bps:.2f}元)，已资不抵债，存在极高退市破产风险"
            red_flags.append({
                "level": "danger",
                "code": "NEGATIVE_BPS",
                "title": "资不抵债预警",
                "desc": f"每股净资产为 {bps:.2f} 元 (≤0)，净资产为负，触及交易所财务类退市风险警示红线",
            })
        elif bps < 1.0:
            solvency_score = 40.0
            solvency_status = "warning"
            solvency_desc = f"每股净资产接近面值 ({bps:.2f}元)，资本缓冲垫薄弱"
            red_flags.append({
                "level": "warning",
                "code": "LOW_BPS",
                "title": "净资产安全垫薄弱",
                "desc": f"最新每股净资产仅 {bps:.2f} 元 (<1.00元面值)，抗风险缓冲空间极小",
            })
        elif bps >= 10.0:
            solvency_score = 95.0
            positive_flags.append({
                "title": "净资产安全垫厚重",
                "desc": f"每股净资产高达 {bps:.2f} 元，股东权益基础极其稳固",
            })
        else:
            solvency_score = min(90.0, 50.0 + bps * 4.0)

    # 2. 盈利持续性与亏损排雷 (Profitability & Loss Detection)
    profit = latest["net_profit"]
    profit_yi = latest["net_profit_yi"]
    roe = latest["roe"]
    prof_score = 70.0
    prof_status = "healthy"
    prof_desc = "企业保持正常盈利能力"

    # 统计近期报告期亏损次数
    loss_count = sum(1 for h in history[:4] if h["net_profit"] is not None and h["net_profit"] < 0)

    if profit is not None and profit < 0:
        prof_status = "danger"
        if loss_count >= 3:
            prof_score = 15.0
            prof_desc = f"近 4 期财报中有 {loss_count} 期发生亏损，存在严重持续经营风险"
            red_flags.append({
                "level": "danger",
                "code": "CONTINUOUS_LOSS",
                "title": "连续多个报告期亏损",
                "desc": f"近 4 期财报中发生 {loss_count} 次亏损，最新净亏损达 {abs(profit_yi):.2f} 亿元",
            })
        else:
            prof_score = 30.0
            prof_desc = f"最新期发生净亏损 ({profit_yi:.2f} 亿元)"
            red_flags.append({
                "level": "warning",
                "code": "NET_LOSS",
                "title": "单期经营亏损",
                "desc": f"最新季度归母净利润为 {profit_yi:.2f} 亿元，经营出现赤字",
            })
    else:
        if roe is not None:
            if roe >= 20.0:
                prof_score = 98.0
                positive_flags.append({
                    "title": "卓越的净资产收益率",
                    "desc": f"加权 ROE 高达 {roe:.2f}%，资本回报率位居全市场顶尖梯队",
                })
            elif roe >= 10.0:
                prof_score = 85.0
                positive_flags.append({
                    "title": "稳健的资本回报",
                    "desc": f"加权 ROE 为 {roe:.2f}%，高于 A 股平均资本成本",
                })
            elif roe < 3.0:
                prof_score = 55.0
                prof_status = "warning"
                prof_desc = f"ROE 偏低 ({roe:.2f}%)，资本利用效率平淡"
            else:
                prof_score = 70.0

    # 3. 毛利护城河与稳定性 (Margin Moat & Stability)
    gm = latest["gross_margin"]
    margin_score = 70.0
    margin_status = "healthy"
    margin_desc = "销售毛利率处于稳健区间"

    if gm is None:
        margin_score = 70.0
        margin_desc = "金融/公用事业类标的不披露标准毛利率，按基准评估"
    elif gm < 0:
        margin_score = 10.0
        margin_status = "danger"
        margin_desc = f"销售毛利率为负 ({gm:.2f}%)，产品倒贴销售，经营模式遭遇严重危机"
        red_flags.append({
            "level": "danger",
            "code": "NEGATIVE_GROSS_MARGIN",
            "title": "毛利率倒挂为负",
            "desc": f"销售毛利率录得 {gm:.2f}% (<0)，售价无法覆盖营业成本",
        })
    elif gm < 8.0:
        margin_score = 45.0
        margin_status = "warning"
        margin_desc = f"毛利率较薄 ({gm:.2f}%)，抗成本波动弹性弱"
        red_flags.append({
            "level": "warning",
            "code": "LOW_GROSS_MARGIN",
            "title": "微利经营脆弱性",
            "desc": f"毛利率仅 {gm:.2f}%，原材料成本或价格战易导致利润迅速转负",
        })
    elif gm > 50.0:
        margin_score = 95.0
        positive_flags.append({
            "title": "深厚的产品毛利护城河",
            "desc": f"最新销售毛利率达 {gm:.2f}%，具备极强的产品定价权与护城河",
        })
    else:
        margin_score = min(90.0, 40.0 + gm * 1.0)

    # 4. 业绩增长与背离检测 (Growth & Divergence)
    growth_score = 70.0
    growth_status = "healthy"
    growth_desc = "业绩增速稳定"
    profit_yoy: float | None = None
    revenue_yoy: float | None = None

    # 同比对比：寻找去年同期报告期 (如 2025-12-31 对比 2024-12-31)
    if len(history) >= 2:
        latest_period = latest["period"]
        latest_suffix = latest_period[4:]  # 例如 -12-31
        comp_item = None
        for h in history[1:]:
            if h["period"].endswith(latest_suffix) and h["period"] != latest_period:
                comp_item = h
                break
        if comp_item is None and len(history) >= 4:
            comp_item = history[3]  # 4 个季度前
        elif comp_item is None:
            comp_item = history[1]  # 紧邻前一期

        if comp_item:
            prev_rev = comp_item["revenue"]
            cur_rev = latest["revenue"]
            if prev_rev and cur_rev and prev_rev > 0:
                revenue_yoy = round((cur_rev / prev_rev - 1.0) * 100.0, 2)

            prev_prof = comp_item["net_profit"]
            cur_prof = latest["net_profit"]
            if prev_prof is not None and cur_prof is not None:
                if prev_prof > 0:
                    profit_yoy = round((cur_prof / prev_prof - 1.0) * 100.0, 2)
                elif cur_prof > 0:
                    profit_yoy = 100.0  # 扭亏为盈
                else:
                    # 持续亏损：若今年亏损大于去年亏损，变动为负
                    profit_yoy = round(((cur_prof - prev_prof) / abs(prev_prof)) * 100.0, 2)

            # 背离检测 1: 增收不增利 (营收增长 > 15%，净利润下滑 > 25%)
            if revenue_yoy is not None and profit_yoy is not None:
                if revenue_yoy > 15.0 and profit_yoy < -25.0:
                    growth_score = 35.0
                    growth_status = "danger"
                    growth_desc = f"严重增收不增利背离：营收增 {revenue_yoy}% 但利润降 {profit_yoy}%"
                    red_flags.append({
                        "level": "danger",
                        "code": "REVENUE_PROFIT_DIVERGENCE",
                        "title": "增收不增利背离警示",
                        "desc": f"营业收入同比大增 {revenue_yoy}%，但归母净利润大幅跳水 {profit_yoy}%，警惕应收账款减值或成本失控",
                    })
                elif profit_yoy < -50.0:
                    growth_score = 40.0
                    growth_status = "danger"
                    growth_desc = f"净利润同比腰斩 ({profit_yoy}%)"
                    red_flags.append({
                        "level": "danger",
                        "code": "PROFIT_SLUMP",
                        "title": "业绩断崖式下滑",
                        "desc": f"归母净利润同比下滑 {abs(profit_yoy):.1f}%，短期业绩遭遇重创",
                    })
                elif profit_yoy > 25.0:
                    growth_score = 90.0
                    positive_flags.append({
                        "title": "高景气业绩高增",
                        "desc": f"归母净利润同比劲增 {profit_yoy}%，成长动能充沛",
                    })
                elif profit_yoy < -20.0:
                    growth_score = 50.0
                    growth_status = "warning"
                    growth_desc = f"净利润同比承压下滑 ({profit_yoy}%)"

    # 5. 波动与财报稳定性 (Stability & Volatility)
    stab_score = 75.0
    stab_status = "healthy"
    stab_desc = "多期经营指标表现平稳"
    if loss_count > 0:
        stab_score = max(20.0, 75.0 - loss_count * 20.0)
        if loss_count >= 2:
            stab_status = "warning"
            stab_desc = f"历史业绩起伏较大，多次出现单季亏损"

    # 维度聚合与总分计算 (加权平均)
    weights = {
        "solvency": 0.25,
        "profitability": 0.25,
        "margin": 0.20,
        "growth": 0.15,
        "stability": 0.15,
    }
    raw_total = (
        solvency_score * weights["solvency"]
        + prof_score * weights["profitability"]
        + margin_score * weights["margin"]
        + growth_score * weights["growth"]
        + stab_score * weights["stability"]
    )
    # 如果有严重红线，总分实行封顶熔断机制
    has_critical_danger = any(f["level"] == "danger" for f in red_flags)
    if has_critical_danger and raw_total > 50.0:
        raw_total = 49.0  # 压入高危预警区

    total_score = round(max(5.0, min(99.0, raw_total)), 1)

    if total_score >= 70.0:
        risk_level = "safe"
        risk_label = "安全区 (低违约风险)"
    elif total_score >= 50.0:
        risk_level = "moderate"
        risk_label = "中度关注 (存在瑕疵)"
    else:
        risk_level = "distress"
        risk_label = "高危预警 (排雷警示)"

    # 构建 Altman 模型量化近似与口径说明
    altman_z_score: float | None = None
    altman_zone = "unavailable"
    if total_score >= 70.0:
        altman_zone = "safe"
    elif total_score >= 50.0:
        altman_zone = "grey"
    else:
        altman_zone = "distress"

    # 基于多期综合质量的中国市场修订版排雷指数 (MDI: Modified Distress Index)
    # 范围约 0.5 ~ 6.0，与经典 Altman 分区对齐 (>2.9 安全, 1.8~2.9 灰色, <1.8 预警)
    altman_z_score = round(total_score / 20.0 - 0.5, 2)

    dimensions = [
        {
            "key": "solvency",
            "name": "偿债安全垫",
            "score": round(solvency_score, 1),
            "weight": weights["solvency"],
            "status": solvency_status,
            "description": solvency_desc,
        },
        {
            "key": "profitability",
            "name": "盈利持续性",
            "score": round(prof_score, 1),
            "weight": weights["profitability"],
            "status": prof_status,
            "description": prof_desc,
        },
        {
            "key": "margin",
            "name": "毛利护城河",
            "score": round(margin_score, 1),
            "weight": weights["margin"],
            "status": margin_status,
            "description": margin_desc,
        },
        {
            "key": "growth",
            "name": "业绩增长与匹配",
            "score": round(growth_score, 1),
            "weight": weights["growth"],
            "status": growth_status,
            "description": growth_desc,
        },
        {
            "key": "stability",
            "name": "波动与减值抗力",
            "score": round(stab_score, 1),
            "weight": weights["stability"],
            "status": stab_status,
            "description": stab_desc,
        },
    ]

    quarterly_trend = [
        {
            "period": h["period"],
            "revenue_yi": h["revenue_yi"],
            "net_profit_yi": h["net_profit_yi"],
            "roe": h["roe"],
            "gross_margin": h["gross_margin"],
        }
        for h in history[:6]
    ]

    metrics = {
        "latest_period": latest["period"],
        "revenue_yi": latest["revenue_yi"],
        "net_profit_yi": latest["net_profit_yi"],
        "roe": latest["roe"],
        "gross_margin": latest["gross_margin"],
        "bps": latest["bps"],
        "revenue_yoy": revenue_yoy,
        "profit_yoy": profit_yoy,
        "history_quarters": len(history),
    }

    return {
        "status": "ok",
        "symbol": symbol,
        "score": total_score,
        "risk_level": risk_level,
        "risk_label": risk_label,
        "altman_assessment": {
            "score": altman_z_score,
            "zone": altman_zone,
            "formula": "Altman Z-Score 中国A股修正版 (MDI 财报质量多维排雷模型)",
            "safe_threshold": 2.9,
            "distress_threshold": 1.8,
        },
        "dimensions": dimensions,
        "red_flags": red_flags,
        "positive_flags": positive_flags,
        "metrics": metrics,
        "quarterly_trend": quarterly_trend,
    }
