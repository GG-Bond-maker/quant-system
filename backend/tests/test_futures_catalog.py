from datetime import date

from app.domain.futures_catalog import (
    FUTURES_CATALOG,
    FUTURES_CORE,
    FUTURES_EXTENDED,
    FUTURES_SPECS,
    get_futures_instrument,
)


def test_catalog_counts_and_disjoint_tiers() -> None:
    assert len(FUTURES_CATALOG) == 83
    assert len(FUTURES_CORE) == 40
    assert len(FUTURES_EXTENDED) == 43
    assert set(FUTURES_CORE).isdisjoint(FUTURES_EXTENDED)
    assert set(FUTURES_CATALOG) == set(FUTURES_CORE) | set(FUTURES_EXTENDED)


def test_core_specs_exist_and_extended_specs_do_not() -> None:
    assert set(FUTURES_SPECS) == set(FUTURES_CORE)
    assert FUTURES_SPECS["CF"].multiplier == 5.0
    assert FUTURES_SPECS["CF"].category == "agri"
    assert FUTURES_SPECS["TS"].category == "bond"
    assert "PB" not in FUTURES_SPECS
    assert FUTURES_CATALOG["PB"].tier == "extended"


def test_t0_is_registered_even_though_sina_display_omits_it() -> None:
    instrument = get_futures_instrument("T0")
    assert instrument is not None
    assert instrument.code == "T"
    assert instrument.sina_symbol == "T0"


def test_core_listing_dates_are_explicit() -> None:
    assert all(FUTURES_CATALOG[code].listing_date is not None for code in FUTURES_CORE)
    assert FUTURES_CATALOG["CF"].listing_date == date(2004, 6, 1)
