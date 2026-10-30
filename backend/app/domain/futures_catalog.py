"""Single source of truth for the domestic futures universe and specs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class FeeRule:
    """One exchange fee rule.

    ``rate`` is charged against notional value; ``fixed`` is charged per lot.
    """

    fee_type: Literal["rate", "fixed"]
    fee_unit: float
    source_note: str


@dataclass(frozen=True)
class FuturesInstrument:
    """Catalog metadata for one underlying futures product."""

    code: str
    name: str
    exchange: str
    category: str
    sina_symbol: str
    listing_date: date | None
    tier: Literal["core", "extended"]
    status: Literal["active", "retired", "pending"]
    default_enabled: bool


@dataclass(frozen=True)
class FuturesContractSpec:
    """Tradable contract terms captured at ``spec_as_of``."""

    symbol_prefix: str
    name: str
    exchange: str
    category: str
    multiplier: float
    tick_size: float
    margin_rate: float
    fee_open: FeeRule
    fee_close: FeeRule
    fee_close_today: FeeRule
    spec_as_of: date
    underlying_code: str | None = None

    @property
    def fee_type(self) -> Literal["rate", "fixed"]:
        """Backward-compatible open-fee type."""

        return self.fee_open.fee_type

    @property
    def fee_unit(self) -> float:
        """Backward-compatible open-fee unit."""

        return self.fee_open.fee_unit


FUTURES_CORE: tuple[str, ...] = (
    "IF", "IC", "IM", "IH", "T", "TF", "TS",
    "AU", "AG", "CU", "AL", "ZN", "NI", "SN",
    "RB", "I", "J", "JM", "HC",
    "SC", "TA", "MA", "FG", "SA", "PP", "L", "V", "RU", "BU",
    "CF", "SR", "M", "Y", "P", "A", "C", "OI", "RM", "AP", "JD",
)

FUTURES_EXTENDED: tuple[str, ...] = (
    "PB", "SS", "AO", "BC", "AD", "SF", "SM", "UR", "EG", "EB",
    "PG", "PF", "PX", "LU", "NR", "SP", "BR", "EC", "PR", "PL",
    "SH", "CY", "CJ", "LH", "CS", "PK", "B", "FU", "FB", "RR",
    "LG", "BZ", "RS", "WH", "JR", "RI", "LR", "LC", "SI", "PS",
    "PT", "PD", "OP",
)


def _instrument(
    code: str,
    name: str,
    exchange: str,
    category: str,
    *,
    tier: Literal["core", "extended"],
    listing_date: date | None = None,
    sina_symbol: str | None = None,
) -> FuturesInstrument:
    return FuturesInstrument(
        code=code,
        name=name,
        exchange=exchange,
        category=category,
        sina_symbol=sina_symbol or f"{code}0",
        listing_date=listing_date,
        tier=tier,
        status="active",
        default_enabled=tier == "core",
    )


FUTURES_CATALOG: dict[str, FuturesInstrument] = {
    # CFFEX
    "IF": _instrument("IF", "沪深300股指期货", "CFFEX", "index", tier="core", listing_date=date(2010, 4, 16)),
    "IC": _instrument("IC", "中证500股指期货", "CFFEX", "index", tier="core", listing_date=date(2015, 4, 16)),
    "IM": _instrument("IM", "中证1000股指期货", "CFFEX", "index", tier="core", listing_date=date(2022, 7, 22)),
    "IH": _instrument("IH", "上证50股指期货", "CFFEX", "index", tier="core", listing_date=date(2015, 4, 16)),
    "T": _instrument("T", "10年期国债期货", "CFFEX", "bond", tier="core", listing_date=date(2015, 3, 20), sina_symbol="T0"),
    "TF": _instrument("TF", "5年期国债期货", "CFFEX", "bond", tier="core", listing_date=date(2013, 9, 6)),
    "TS": _instrument("TS", "2年期国债期货", "CFFEX", "bond", tier="core", listing_date=date(2018, 8, 17)),
    # SHFE precious and base metals
    "AU": _instrument("AU", "沪金", "SHFE", "precious", tier="core", listing_date=date(2008, 1, 9)),
    "AG": _instrument("AG", "沪银", "SHFE", "precious", tier="core", listing_date=date(2012, 5, 10)),
    "CU": _instrument("CU", "沪铜", "SHFE", "metal", tier="core", listing_date=date(1993, 3, 1)),
    "AL": _instrument("AL", "沪铝", "SHFE", "metal", tier="core", listing_date=date(1993, 3, 1)),
    "ZN": _instrument("ZN", "沪锌", "SHFE", "metal", tier="core", listing_date=date(2007, 3, 26)),
    "NI": _instrument("NI", "沪镍", "SHFE", "metal", tier="core", listing_date=date(2015, 3, 27)),
    "SN": _instrument("SN", "沪锡", "SHFE", "metal", tier="core", listing_date=date(2015, 3, 27)),
    "PB": _instrument("PB", "沪铅", "SHFE", "metal", tier="extended"),
    "SS": _instrument("SS", "不锈钢", "SHFE", "metal", tier="extended"),
    "AO": _instrument("AO", "氧化铝", "SHFE", "metal", tier="extended"),
    "AD": _instrument("AD", "铸造铝合金", "SHFE", "metal", tier="extended"),
    "BC": _instrument("BC", "国际铜", "INE", "metal", tier="extended"),
    # SHFE / DCE ferrous
    "RB": _instrument("RB", "螺纹钢", "SHFE", "ferrous", tier="core", listing_date=date(2009, 3, 27)),
    "HC": _instrument("HC", "热轧卷板", "SHFE", "ferrous", tier="core", listing_date=date(2014, 3, 21)),
    "I": _instrument("I", "铁矿石", "DCE", "ferrous", tier="core", listing_date=date(2013, 10, 18)),
    "J": _instrument("J", "焦炭", "DCE", "ferrous", tier="core", listing_date=date(2011, 4, 15)),
    "JM": _instrument("JM", "焦煤", "DCE", "ferrous", tier="core", listing_date=date(2013, 3, 22)),
    "SF": _instrument("SF", "硅铁", "CZCE", "ferrous", tier="extended"),
    "SM": _instrument("SM", "锰硅", "CZCE", "ferrous", tier="extended"),
    # Energy and chemicals
    "SC": _instrument("SC", "原油", "INE", "energy", tier="core", listing_date=date(2018, 3, 26)),
    "TA": _instrument("TA", "PTA", "CZCE", "energy", tier="core", listing_date=date(2006, 12, 18)),
    "MA": _instrument("MA", "甲醇", "CZCE", "energy", tier="core", listing_date=date(2011, 10, 28)),
    "FG": _instrument("FG", "玻璃", "CZCE", "energy", tier="core", listing_date=date(2012, 12, 3)),
    "SA": _instrument("SA", "纯碱", "CZCE", "energy", tier="core", listing_date=date(2019, 12, 6)),
    "PP": _instrument("PP", "聚丙烯", "DCE", "energy", tier="core", listing_date=date(2014, 2, 28)),
    "L": _instrument("L", "塑料", "DCE", "energy", tier="core", listing_date=date(2007, 7, 31)),
    "V": _instrument("V", "PVC", "DCE", "energy", tier="core", listing_date=date(2009, 5, 25)),
    "RU": _instrument("RU", "天然橡胶", "SHFE", "energy", tier="core", listing_date=date(1993, 11, 1)),
    "BU": _instrument("BU", "石油沥青", "SHFE", "energy", tier="core", listing_date=date(2013, 10, 9)),
    "UR": _instrument("UR", "尿素", "CZCE", "energy", tier="extended"),
    "EG": _instrument("EG", "乙二醇", "DCE", "energy", tier="extended"),
    "EB": _instrument("EB", "苯乙烯", "DCE", "energy", tier="extended"),
    "PG": _instrument("PG", "液化石油气", "DCE", "energy", tier="extended"),
    "PF": _instrument("PF", "短纤", "CZCE", "energy", tier="extended"),
    "PX": _instrument("PX", "对二甲苯", "CZCE", "energy", tier="extended"),
    "LU": _instrument("LU", "低硫燃料油", "INE", "energy", tier="extended"),
    "NR": _instrument("NR", "20号胶", "INE", "energy", tier="extended"),
    "SP": _instrument("SP", "漂针浆", "SHFE", "energy", tier="extended"),
    "BR": _instrument("BR", "丁二烯橡胶", "SHFE", "energy", tier="extended"),
    "EC": _instrument("EC", "SCFIS欧线", "INE", "energy", tier="extended"),
    "PR": _instrument("PR", "瓶片", "CZCE", "energy", tier="extended"),
    "PL": _instrument("PL", "丙烯", "CZCE", "energy", tier="extended"),
    "SH": _instrument("SH", "烧碱", "CZCE", "energy", tier="extended"),
    "FU": _instrument("FU", "燃料油", "SHFE", "energy", tier="extended"),
    "BZ": _instrument("BZ", "纯苯", "DCE", "energy", tier="extended"),
    "OP": _instrument("OP", "胶版纸", "SHFE", "energy", tier="extended"),
    # Agricultural products
    "CF": _instrument("CF", "棉花", "CZCE", "agri", tier="core", listing_date=date(2004, 6, 1)),
    "SR": _instrument("SR", "白糖", "CZCE", "agri", tier="core", listing_date=date(2006, 1, 6)),
    "M": _instrument("M", "豆粕", "DCE", "agri", tier="core", listing_date=date(2000, 7, 17)),
    "Y": _instrument("Y", "豆油", "DCE", "agri", tier="core", listing_date=date(2006, 1, 9)),
    "P": _instrument("P", "棕榈油", "DCE", "agri", tier="core", listing_date=date(2007, 10, 29)),
    "A": _instrument("A", "豆一", "DCE", "agri", tier="core", listing_date=date(2002, 3, 15)),
    "C": _instrument("C", "玉米", "DCE", "agri", tier="core", listing_date=date(2004, 9, 22)),
    "OI": _instrument("OI", "菜籽油", "CZCE", "agri", tier="core", listing_date=date(2007, 6, 8)),
    "RM": _instrument("RM", "菜粕", "CZCE", "agri", tier="core", listing_date=date(2012, 12, 28)),
    "AP": _instrument("AP", "苹果", "CZCE", "agri", tier="core", listing_date=date(2017, 12, 22)),
    "JD": _instrument("JD", "鸡蛋", "DCE", "agri", tier="core", listing_date=date(2013, 11, 8)),
    "CY": _instrument("CY", "棉纱", "CZCE", "agri", tier="extended"),
    "CJ": _instrument("CJ", "红枣", "CZCE", "agri", tier="extended"),
    "LH": _instrument("LH", "生猪", "DCE", "agri", tier="extended"),
    "CS": _instrument("CS", "玉米淀粉", "DCE", "agri", tier="extended"),
    "PK": _instrument("PK", "花生仁", "CZCE", "agri", tier="extended"),
    "B": _instrument("B", "豆二", "DCE", "agri", tier="extended"),
    "FB": _instrument("FB", "纤维板", "DCE", "agri", tier="extended"),
    "RR": _instrument("RR", "粳米", "DCE", "agri", tier="extended"),
    "LG": _instrument("LG", "原木", "DCE", "agri", tier="extended"),
    "RS": _instrument("RS", "油菜籽", "CZCE", "agri", tier="extended"),
    "WH": _instrument("WH", "优质强筋小麦", "CZCE", "agri", tier="extended"),
    "JR": _instrument("JR", "粳稻", "CZCE", "agri", tier="extended"),
    "RI": _instrument("RI", "早籼稻", "CZCE", "agri", tier="extended"),
    "LR": _instrument("LR", "晚籼稻", "CZCE", "agri", tier="extended"),
    # GFEX and precious metals
    "LC": _instrument("LC", "碳酸锂", "GFEX", "metal", tier="extended"),
    "SI": _instrument("SI", "工业硅", "GFEX", "metal", tier="extended"),
    "PS": _instrument("PS", "多晶硅", "GFEX", "metal", tier="extended"),
    "PT": _instrument("PT", "铂", "GFEX", "precious", tier="extended"),
    "PD": _instrument("PD", "钯", "GFEX", "precious", tier="extended"),
}


_DEFAULT_FEE_SOURCE = (
    "OpenCTP futures fee table snapshot 2026-10-30; "
    "fixed fees normalized to conventional exchange increments"
)
_CFFEX_INDEX_FEE_SOURCE = (
    "CFFEX stock-index fee baseline retained by the project "
    "(open/prior-close 0.000023, close-today 0.000345)"
)


def _rate(unit: float, source_note: str = _DEFAULT_FEE_SOURCE) -> FeeRule:
    return FeeRule(fee_type="rate", fee_unit=unit, source_note=source_note)


def _fixed(unit: float, source_note: str = _DEFAULT_FEE_SOURCE) -> FeeRule:
    return FeeRule(fee_type="fixed", fee_unit=unit, source_note=source_note)


def _spec(
    code: str,
    multiplier: float,
    tick_size: float,
    margin_rate: float,
    fee_open: FeeRule,
    fee_close: FeeRule,
    fee_close_today: FeeRule,
    *,
    underlying_code: str | None = None,
) -> FuturesContractSpec:
    instrument = FUTURES_CATALOG[code]
    return FuturesContractSpec(
        symbol_prefix=code,
        name=instrument.name,
        exchange=instrument.exchange,
        category=instrument.category,
        multiplier=multiplier,
        tick_size=tick_size,
        margin_rate=margin_rate,
        fee_open=fee_open,
        fee_close=fee_close,
        fee_close_today=fee_close_today,
        spec_as_of=date(2026, 10, 30),
        underlying_code=underlying_code,
    )


_IF_OPEN = _rate(0.000023, _CFFEX_INDEX_FEE_SOURCE)
_IF_CLOSE = _rate(0.000023, _CFFEX_INDEX_FEE_SOURCE)
_IF_CLOSE_TODAY = _rate(0.000345, _CFFEX_INDEX_FEE_SOURCE)

FUTURES_SPECS: dict[str, FuturesContractSpec] = {
    "IF": _spec("IF", 300.0, 0.2, 0.12, _IF_OPEN, _IF_CLOSE, _IF_CLOSE_TODAY, underlying_code="000300.SH"),
    "IC": _spec("IC", 200.0, 0.2, 0.12, _IF_OPEN, _IF_CLOSE, _IF_CLOSE_TODAY, underlying_code="000905.SH"),
    "IM": _spec("IM", 200.0, 0.2, 0.12, _IF_OPEN, _IF_CLOSE, _IF_CLOSE_TODAY, underlying_code="000852.SH"),
    "IH": _spec("IH", 300.0, 0.2, 0.12, _IF_OPEN, _IF_CLOSE, _IF_CLOSE_TODAY, underlying_code="000016.SH"),
    "T": _spec("T", 10000.0, 0.005, 0.02, _fixed(3.0), _fixed(3.0), _fixed(0.0)),
    "TF": _spec("TF", 10000.0, 0.005, 0.012, _fixed(3.0), _fixed(3.0), _fixed(0.0)),
    "TS": _spec("TS", 20000.0, 0.002, 0.005, _fixed(3.0), _fixed(3.0), _fixed(0.0)),
    "AU": _spec("AU", 1000.0, 0.02, 0.16, _fixed(20.0), _fixed(20.0), _fixed(0.0)),
    "AG": _spec("AG", 15.0, 1.0, 0.22, _rate(0.000051), _rate(0.000051), _rate(0.000051)),
    "CU": _spec("CU", 5.0, 10.0, 0.15, _rate(0.000051), _rate(0.000051), _rate(0.000101)),
    "AL": _spec("AL", 5.0, 5.0, 0.15, _fixed(3.0), _fixed(3.0), _fixed(3.0)),
    "ZN": _spec("ZN", 5.0, 5.0, 0.15, _fixed(3.0), _fixed(3.0), _fixed(0.0)),
    "NI": _spec("NI", 1.0, 10.0, 0.15, _fixed(3.0), _fixed(3.0), _fixed(3.0)),
    "SN": _spec("SN", 1.0, 10.0, 0.15, _fixed(3.0), _fixed(3.0), _fixed(3.0)),
    "RB": _spec("RB", 10.0, 1.0, 0.15, _rate(0.000101), _rate(0.000101), _rate(0.000101)),
    "I": _spec("I", 100.0, 0.5, 0.20, _rate(0.000101), _rate(0.000101), _rate(0.000101)),
    "J": _spec("J", 100.0, 0.5, 0.20, _rate(0.000141), _rate(0.000101), _rate(0.000141)),
    "JM": _spec("JM", 60.0, 0.5, 0.20, _rate(0.000101), _rate(0.000101), _rate(0.000101)),
    "HC": _spec("HC", 10.0, 1.0, 0.15, _rate(0.000101), _rate(0.000101), _rate(0.000101)),
    "SC": _spec("SC", 1000.0, 0.1, 0.22, _fixed(20.0), _fixed(20.0), _fixed(0.0)),
    "TA": _spec("TA", 5.0, 2.0, 0.20, _fixed(3.0), _fixed(3.0), _fixed(0.0)),
    "MA": _spec("MA", 10.0, 1.0, 0.20, _rate(0.000101), _rate(0.000101), _rate(0.000101)),
    "FG": _spec("FG", 20.0, 1.0, 0.20, _fixed(2.0), _fixed(2.0), _fixed(2.0)),
    "SA": _spec("SA", 20.0, 1.0, 0.20, _rate(0.000101), _rate(0.000101), _rate(0.000101)),
    "PP": _spec("PP", 5.0, 1.0, 0.20, _fixed(1.0), _fixed(1.0), _fixed(1.0)),
    "L": _spec("L", 5.0, 1.0, 0.20, _fixed(1.0), _fixed(1.0), _fixed(1.0)),
    "V": _spec("V", 5.0, 1.0, 0.20, _fixed(1.0), _fixed(1.0), _fixed(1.0)),
    "RU": _spec("RU", 10.0, 5.0, 0.15, _fixed(3.0), _fixed(3.0), _fixed(0.0)),
    "BU": _spec("BU", 10.0, 1.0, 0.15, _rate(0.000051), _rate(0.000051), _rate(0.0)),
    "CF": _spec("CF", 5.0, 5.0, 0.20, _fixed(4.3), _fixed(4.3), _fixed(0.0)),
    "SR": _spec("SR", 10.0, 1.0, 0.20, _fixed(2.0), _fixed(2.0), _fixed(0.0)),
    "M": _spec("M", 10.0, 1.0, 0.20, _fixed(1.5), _fixed(1.5), _fixed(1.5)),
    "Y": _spec("Y", 10.0, 1.0, 0.20, _fixed(2.5), _fixed(2.5), _fixed(2.5)),
    "P": _spec("P", 10.0, 1.0, 0.20, _fixed(2.5), _fixed(2.5), _fixed(2.5)),
    "A": _spec("A", 10.0, 1.0, 0.20, _fixed(2.0), _fixed(2.0), _fixed(2.0)),
    "C": _spec("C", 10.0, 1.0, 0.20, _fixed(1.2), _fixed(1.2), _fixed(1.2)),
    "OI": _spec("OI", 10.0, 1.0, 0.20, _fixed(2.0), _fixed(2.0), _fixed(2.0)),
    "RM": _spec("RM", 10.0, 1.0, 0.20, _fixed(1.5), _fixed(1.5), _fixed(0.0)),
    "AP": _spec("AP", 10.0, 1.0, 0.20, _fixed(5.0), _fixed(5.0), _fixed(10.0)),
    "JD": _spec("JD", 10.0, 1.0, 0.20, _rate(0.000151), _rate(0.000151), _rate(0.000151)),
}


def get_futures_instrument(symbol: str) -> FuturesInstrument | None:
    """Resolve ``CF``, ``CF0`` or ``CF2412`` to one catalog instrument."""

    prefix = "".join(ch for ch in symbol.strip().upper() if ch.isalpha())
    return FUTURES_CATALOG.get(prefix)
