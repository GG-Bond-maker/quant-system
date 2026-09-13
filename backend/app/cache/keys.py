"""
缓存 Key 生成器（集中管理，禁止业务代码手拼 key）。

规则：namespace:resource:id[:extra]，全小写。
"""
from __future__ import annotations

NS = "aqp"


def k_market_overview(date_yyyymmdd: str, recommend_k: int = 50) -> str:
    """市场概览聚合结果（含指数/热度/资金/推荐榜）。

    ⚠️ key 必须包含 recommend_k：不同榜单条数的请求不可共享同一缓存
    （P0-Minor 修复项：否则推荐条数不同的请求会返回彼此的缓存）。"""
    return f"{NS}:market:overview:{date_yyyymmdd}:k{recommend_k}"


def k_market_overview_rt(date_yyyymmdd: str) -> str:
    """市场概览实时块（L2-2）：指数/资金/异动，TTL 45s。"""
    return f"{NS}:market:overview_rt:{date_yyyymmdd}"


def k_market_overview_daily(date_yyyymmdd: str, recommend_k: int = 50) -> str:
    """市场概览日频块（L2-2）：本地口径分布/推荐榜/情绪，TTL 至次日盘后。"""
    return f"{NS}:market:overview_daily:{date_yyyymmdd}:k{recommend_k}"


def k_stock_profile(symbol: str) -> str:
    """个股档案（DB 基础信息 + 最新行情）。"""
    return f"{NS}:stock:profile:{symbol}"


def k_stock_kline(symbol: str, adjust: str, start: str, end: str) -> str:
    """K 线区间（key 必须带 adjust + 区间，避免口径串数据）。"""
    return f"{NS}:stock:kline:{symbol}:{adjust}:{start}_{end}"


def k_stock_predict(symbol: str, trade_date: str) -> str:
    """个股机器学习预测（key 按数据日期隔离，跨天自动失效）。"""
    return f"{NS}:stock:predict:{symbol}:{trade_date}"


def k_stock_block(symbol: str, block: str, trade_date: str) -> str:
    """个股详情面板的单个数据块（quote/money_flow/north/events/holders/chip/risk）。

    按 block 分键：各块更新频率不同（盘中快照 5min / 股东信息 6h），
    且网络块与本地计算块互不影响，分键才能实现独立 TTL 与独立降级。
    """
    return f"{NS}:stock:block:{symbol}:{block}:{trade_date}"


def k_screener(date_key: str, strategy: str, top_k: int, board: str) -> str:
    """选股缓存键：date+strategy+top_k+board 全参数入键（避免不同参数共享缓存）。"""
    return f"{NS}:screener:{date_key}:{strategy}:{top_k}:{board}"


def k_backtest(params_key: str) -> str:
    """回测结果缓存键（参数快照）。

    ⚠️ 调用方必须把**全部**影响结果的请求参数拼进 params_key——漏参 =
    不同请求共享缓存 = 返回他人结果（2026-09-05 审核 P0-1：/run 曾漏
    init_cash、/strategy-run 曾漏 walk_forward；recommend_k 同类前科见
    k_market_overview 注释）。新增请求字段时键必须同步。
    """
    return f"{NS}:backtest:{params_key}"
