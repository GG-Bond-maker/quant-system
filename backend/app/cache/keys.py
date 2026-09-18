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


def k_etf_overview(data_date: str) -> str:
    """ETF 概览快照：按自然日隔离，避免跨日复用实时规模与成交额。"""
    return f"{NS}:etf:overview:{data_date}"


def k_etf_performance(data_date: str, symbols_key: str, metric: str, period: str) -> str:
    """ETF 表现序列：数据日与全部请求参数共同决定缓存实体。"""
    return f"{NS}:etf:performance:{data_date}:{symbols_key}:{metric}:{period}"


def k_etf_scale(data_date: str, period: str, top_n: int) -> str:
    """ETF 规模估算序列：数据日、窗口和样本数量均参与隔离。"""
    return f"{NS}:etf:scale:{data_date}:{period}:n{top_n}"


def k_etf_detail(data_date: str, code: str, kline_period: str) -> str:
    """ETF 详情聚合：按交易数据日、标的和 K 线周期隔离。"""
    return f"{NS}:etf:detail:{data_date}:{code.lower()}:{kline_period}"


def k_stock_profile(symbol: str) -> str:
    """个股档案（DB 基础信息 + 最新行情）。"""
    return f"{NS}:stock:profile:{symbol}"


def k_stock_kline(symbol: str, adjust: str, start: str, end: str) -> str:
    """K 线区间（key 必须带 adjust + 区间，避免口径串数据）。"""
    return f"{NS}:stock:kline:{symbol}:{adjust}:{start}_{end}"


def k_stock_predict(symbol: str, trade_date: str) -> str:
    """个股机器学习预测（key 按数据日期隔离，跨天自动失效）。"""
    return f"{NS}:stock:predict:{symbol}:{trade_date}"


def k_stock_block(symbol: str, block: str, trade_date: str,
                  params_key: str = "default") -> str:
    """个股详情面板单块缓存键，包含数据日和影响结果的块参数。"""
    return f"{NS}:stock:block:{symbol}:{block}:{trade_date}:{params_key}"


def k_portfolio_search(username: str, data_date: str, query: str, limit: int) -> str:
    """组合资产搜索；按用户、数据日、查询词和条数隔离。"""
    return f"{NS}:portfolio:search:{username}:{data_date}:{query}:n{limit}"


def k_watchlist_dashboard(username: str, data_date: str, symbols_key: str) -> str:
    """自选看板；用户维度必须入键，禁止跨用户复用私有自选结果。"""
    return f"{NS}:watchlist:dashboard:{username}:{data_date}:{symbols_key}"


def k_datacenter_overview(data_date: str, root_key: str, revision: str) -> str:
    """数据中心概览；数据根目录和本地数据修订均入键，避免跨环境/旧版本复用。"""
    return f"{NS}:datacenter:overview:{data_date}:{root_key}:{revision}"


def k_screener(date_key: str, strategy: str, top_k: int, board: str) -> str:
    """选股缓存键：date+strategy+top_k+board 全参数入键（避免不同参数共享缓存）。"""
    return f"{NS}:screener:{date_key}:{strategy}:{top_k}:{board}"


def k_screener_stocks(basis: str) -> str:
    """选股中心「股票列表」全市场行缓存键（TTL 60s，见 api/v1/screener.py）。

    ⚠️ 只按 ``basis`` 分键 —— page / sort / dir / 筛选条件**全部不进键**：
    这些维度在内存里对已构建好的全市场行做切片（约 1157 行），若入键则
    「每页 × 每排序 × 每筛选组合」各缓存一份，全表副本会把缓存打爆，
    且 60s TTL 内翻页会看到不同时刻的行集合。
    basis 必须入键：日终口径（daily）与实时口径（realtime/auto）的数字
    不可混用。
    """
    return f"{NS}:screener:stocks:{basis}"


def k_backtest(params_key: str) -> str:
    """回测结果缓存键（参数快照）。

    ⚠️ 调用方必须把**全部**影响结果的请求参数拼进 params_key——漏参 =
    不同请求共享缓存 = 返回他人结果（2026-09-05 审核 P0-1：/run 曾漏
    init_cash、/strategy-run 曾漏 walk_forward；recommend_k 同类前科见
    k_market_overview 注释）。新增请求字段时键必须同步。
    """
    return f"{NS}:backtest:{params_key}"
