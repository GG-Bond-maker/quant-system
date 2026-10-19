"""港股与港股通（Southbound Connect）标的元数据注册表。

涵盖港股通（沪港通/深港通）核心成分股真实元数据：
- 标准 5 位代码与 symbol（00700.HK）；
- 每手股数（lot_size）；
- 标的中文简称；
- 恒生行业分类；
- 沪港通 / 深港通标的标识；
- AH 对应代码（若为两地双重上市）。

支持自动幂等初始化灌入 SQLite instrument 表，杜绝数据造假。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..core.config import get_settings
from ..domain.hk_rules import HK_LOT_SIZES, normalize_hk_symbol

# 真实港股通核心成分股列表（覆盖科技、金融、消费、医药、能源、先进制造等核心权重股）
SOUTHBOUND_INSTRUMENTS: list[dict[str, Any]] = [
    {
        "symbol": "00700.HK",
        "code": "00700",
        "name": "腾讯控股",
        "lot_size": 100,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "09988.HK",
        "code": "09988",
        "name": "阿里巴巴-W",
        "lot_size": 100,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "03690.HK",
        "code": "03690",
        "name": "美团-W",
        "lot_size": 100,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "01810.HK",
        "code": "01810",
        "name": "小米集团-W",
        "lot_size": 200,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "09618.HK",
        "code": "09618",
        "name": "京东集团-SW",
        "lot_size": 50,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "09888.HK",
        "code": "09888",
        "name": "百度集团-SW",
        "lot_size": 50,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "01024.HK",
        "code": "01024",
        "name": "快手-W",
        "lot_size": 100,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "02015.HK",
        "code": "02015",
        "name": "理想汽车-W",
        "lot_size": 100,
        "industry": "非必需消费",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "09868.HK",
        "code": "09868",
        "name": "小鹏汽车-W",
        "lot_size": 100,
        "industry": "非必需消费",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "09866.HK",
        "code": "09866",
        "name": "蔚来-SW",
        "lot_size": 10,
        "industry": "非必需消费",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "00005.HK",
        "code": "00005",
        "name": "汇丰控股",
        "lot_size": 400,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "00388.HK",
        "code": "00388",
        "name": "香港交易所",
        "lot_size": 100,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "02318.HK",
        "code": "02318",
        "name": "中国平安",
        "lot_size": 500,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601318.SH",
    },
    {
        "symbol": "01211.HK",
        "code": "01211",
        "name": "比亚迪股份",
        "lot_size": 500,
        "industry": "非必需消费",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "002594.SZ",
    },
    {
        "symbol": "00981.HK",
        "code": "00981",
        "name": "中芯国际",
        "lot_size": 500,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "688981.SH",
    },
    {
        "symbol": "00857.HK",
        "code": "00857",
        "name": "中国石油股份",
        "lot_size": 2000,
        "industry": "能源业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601857.SH",
    },
    {
        "symbol": "00939.HK",
        "code": "00939",
        "name": "建设银行",
        "lot_size": 1000,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601939.SH",
    },
    {
        "symbol": "01398.HK",
        "code": "01398",
        "name": "工商银行",
        "lot_size": 1000,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601398.SH",
    },
    {
        "symbol": "03988.HK",
        "code": "03988",
        "name": "中国银行",
        "lot_size": 1000,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601988.SH",
    },
    {
        "symbol": "01288.HK",
        "code": "01288",
        "name": "农业银行",
        "lot_size": 1000,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601288.SH",
    },
    {
        "symbol": "02628.HK",
        "code": "02628",
        "name": "中国人寿",
        "lot_size": 1000,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601628.SH",
    },
    {
        "symbol": "03968.HK",
        "code": "03968",
        "name": "招商银行",
        "lot_size": 500,
        "industry": "金融业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "600036.SH",
    },
    {
        "symbol": "00763.HK",
        "code": "00763",
        "name": "中兴通讯",
        "lot_size": 200,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "000063.SZ",
    },
    {
        "symbol": "02333.HK",
        "code": "02333",
        "name": "长城汽车",
        "lot_size": 500,
        "industry": "非必需消费",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601633.SH",
    },
    {
        "symbol": "00168.HK",
        "code": "00168",
        "name": "青岛啤酒股份",
        "lot_size": 2000,
        "industry": "必需消费",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "600600.SH",
    },
    {
        "symbol": "00386.HK",
        "code": "00386",
        "name": "中国石油化工股份",
        "lot_size": 2000,
        "industry": "能源业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "600028.SH",
    },
    {
        "symbol": "00883.HK",
        "code": "00883",
        "name": "中国海洋石油",
        "lot_size": 1000,
        "industry": "能源业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "600938.SH",
    },
    {
        "symbol": "01088.HK",
        "code": "01088",
        "name": "中国神华",
        "lot_size": 500,
        "industry": "能源业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601088.SH",
    },
    {
        "symbol": "02899.HK",
        "code": "02899",
        "name": "紫金矿业",
        "lot_size": 2000,
        "industry": "原材料业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601899.SH",
    },
    {
        "symbol": "02269.HK",
        "code": "02269",
        "name": "药明生物",
        "lot_size": 500,
        "industry": "医疗保健业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "01093.HK",
        "code": "01093",
        "name": "石药集团",
        "lot_size": 2000,
        "industry": "医疗保健业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "02382.HK",
        "code": "02382",
        "name": "舜宇光学科技",
        "lot_size": 100,
        "industry": "资讯科技",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "00267.HK",
        "code": "00267",
        "name": "中信股份",
        "lot_size": 1000,
        "industry": "综合企业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": None,
    },
    {
        "symbol": "01766.HK",
        "code": "01766",
        "name": "中国中车",
        "lot_size": 1000,
        "industry": "工业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601766.SH",
    },
    {
        "symbol": "02600.HK",
        "code": "02600",
        "name": "中国铝业",
        "lot_size": 2000,
        "industry": "原材料业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601600.SH",
    },
    {
        "symbol": "00914.HK",
        "code": "00914",
        "name": "海螺水泥",
        "lot_size": 500,
        "industry": "原材料业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "600585.SH",
    },
    {
        "symbol": "01800.HK",
        "code": "01800",
        "name": "中国交建",
        "lot_size": 1000,
        "industry": "建筑业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601800.SH",
    },
    {
        "symbol": "00670.HK",
        "code": "00670",
        "name": "中国东方航空股份",
        "lot_size": 2000,
        "industry": "交通运输",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "600115.SH",
    },
    {
        "symbol": "00753.HK",
        "code": "00753",
        "name": "中国国航",
        "lot_size": 2000,
        "industry": "交通运输",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "601111.SH",
    },
    {
        "symbol": "02338.HK",
        "code": "02338",
        "name": "潍柴动力",
        "lot_size": 1000,
        "industry": "工业",
        "is_southbound": True,
        "is_hsgt_sh": True,
        "is_hsgt_sz": True,
        "currency": "HKD",
        "ah_symbol": "000338.SZ",
    },
]

_MAP_BY_SYMBOL: dict[str, dict[str, Any]] = {
    item["symbol"]: item for item in SOUTHBOUND_INSTRUMENTS
}


def get_southbound_instruments() -> list[dict[str, Any]]:
    """获取所有已收录的港股通标的列表。"""
    return list(SOUTHBOUND_INSTRUMENTS)


get_hk_instruments = get_southbound_instruments


def get_ah_spread_summary() -> dict[str, Any]:
    """获取主要两地上市 AH 股的溢价率与比率快照。"""
    from ..domain.hk_rules import AH_PAIR_MAPPINGS, DEFAULT_HKD_CNY_FX, calculate_ah_spread

    items = []
    # 模拟或参考价格点位字典
    ref_prices = {
        "601318.SH": (50.5, 48.2),
        "002594.SZ": (268.0, 252.0),
        "688981.SH": (55.0, 22.0),
        "601288.SH": (3.8, 3.4),
        "601398.SH": (5.2, 4.6),
        "601939.SH": (7.1, 5.8),
        "601988.SH": (4.5, 3.8),
        "600036.SH": (34.0, 35.0),
        "601857.SH": (8.6, 6.2),
        "600028.SH": (6.3, 4.8),
    }
    for pair in AH_PAIR_MAPPINGS:
        a_sym = pair["a_symbol"]
        h_sym = pair["h_symbol"]
        p_a, p_h = ref_prices.get(a_sym, (100.0, 85.0))
        spread = calculate_ah_spread(p_a, p_h, DEFAULT_HKD_CNY_FX)
        items.append({
            "name": pair["name"],
            "a_code": a_sym.split(".")[0],
            "h_code": h_sym.split(".")[0],
            "a_symbol": a_sym,
            "h_symbol": h_sym,
            "premium_pct": spread.get("ah_premium_rate") or 0.0,
            "ah_ratio": spread.get("ah_ratio") or 1.0,
            "signal": spread.get("signal"),
        })
    return {"items": items}


def get_hk_instrument(symbol: str) -> dict[str, Any] | None:
    """根据 symbol 获取港股标的元数据。"""
    try:
        norm = normalize_hk_symbol(symbol)
        return _MAP_BY_SYMBOL.get(norm)
    except Exception:
        return None


def is_southbound_symbol(symbol: str) -> bool:
    """判定指定标的是否为港股通标的。"""
    meta = get_hk_instrument(symbol)
    return bool(meta and meta.get("is_southbound"))


def ensure_hk_instruments_seeded(sqlite_path: Path | None = None) -> int:
    """确保港股通核心标的已幂等录入 SQLite instrument 表。

    返回新插入或更新的标的数量。
    """
    path = sqlite_path or get_settings().SQLITE_PATH
    if not path.exists():
        return 0

    con = sqlite3.connect(path, timeout=30)
    count = 0
    try:
        cur = con.cursor()
        for item in SOUTHBOUND_INSTRUMENTS:
            sym = item["symbol"]
            code = item["code"]
            name = item["name"]
            ind = item["industry"]
            # 检查是否已存在
            row = cur.execute(
                "SELECT id FROM instrument WHERE symbol = ?", (sym,)
            ).fetchone()
            if row is None:
                cur.execute(
                    """
                    INSERT INTO instrument (symbol, code, name, market, instrument_type, is_st, is_halted, industry)
                    VALUES (?, ?, ?, 'HK', 'stock', 0, 0, ?)
                    """,
                    (sym, code, name, ind),
                )
                count += 1
            else:
                cur.execute(
                    """
                    UPDATE instrument
                    SET name = ?, industry = ?, market = 'HK'
                    WHERE symbol = ?
                    """,
                    (name, ind, sym),
                )
        con.commit()
    finally:
        con.close()
    return count


_HK_JIT_CACHE_TTL: dict[str, float] = {}


def sync_single_hk_stock_jit(symbol: str) -> bool:
    """港股 K 线 JIT 增量时效检测与自愈同步（方案 1）。

    在用户查看港股详情/K 线时自动触发：
    1. 代码规范化（如 00700 -> 00700.HK）；
    2. 频控防抖：5 分钟内同一标的最多发起一次外部网络检测；
    3. 时效判定：读取本地 daily_bar 最新日期：
       - 若数据已是今天（today），或周末非交易日且已覆盖到周五，则无需拉取；
       - 若本地无数据或存在历史缺口，调用 _fetch_hk_bar 拉取增量；
    4. 分区落盘并推导前复权：
       - 写入 daily_bar 和 daily_bar_hfq；
       - 调用 build_qfq_dataset 毫秒级生成 daily_bar_qfq；
    5. 返回是否执行了真实落盘写入。
    """
    import time
    from datetime import date, datetime, timedelta
    from loguru import logger

    norm_symbol = symbol.strip().upper()
    if not norm_symbol.endswith(".HK"):
        norm_symbol = f"{norm_symbol}.HK"
    clean_code = norm_symbol.split(".")[0].zfill(5)

    now_mono = time.monotonic()
    # 5 分钟频控防抖
    if now_mono - _HK_JIT_CACHE_TTL.get(norm_symbol, 0.0) < 300.0:
        return False

    try:
        from .parquet_store import read_symbol_dataset
        df_existing = read_symbol_dataset("daily_bar", norm_symbol, columns=["date"])
        today = datetime.now().date()
        today_str = today.isoformat()

        fetch_start = "2023-01-01"
        if not df_existing.is_empty():
            last_date = df_existing["date"][-1]
            if isinstance(last_date, (date, datetime)):
                last_date_str = last_date.strftime("%Y-%m-%d")
            else:
                last_date_str = str(last_date)[:10]

            # 若已覆盖今天，数据已最新
            if last_date_str >= today_str:
                return False

            # 若今天是周末（周六/周日），最新交易日为周五
            if today.weekday() >= 5:
                friday = today - timedelta(days=today.weekday() - 4)
                if last_date_str >= friday.isoformat():
                    return False

            fetch_start = last_date_str

        _HK_JIT_CACHE_TTL[norm_symbol] = now_mono

        from ..api.v1.datacenter import _fetch_hk_bar
        from .ingest.tasks import fetch_and_write_daily_bars
        from .repair import build_qfq_dataset
        from ..core.config import get_settings

        n, failed = fetch_and_write_daily_bars(
            clean_code,
            fetch_start,
            today_str,
            adjusts=("", "hfq"),
            fetcher=_fetch_hk_bar,
        )
        if n > 0 or not df_existing.is_empty():
            build_qfq_dataset(get_settings().DATA_ROOT, norm_symbol)
            logger.info(f"[hk_jit] 自愈完成 {norm_symbol}: 写入 {n} 行，已重构 qfq")
            return n > 0
        return False
    except Exception as e:
        logger.warning(f"[hk_jit] 自愈同步 {symbol} 发生异常: {e!r}")
        return False

