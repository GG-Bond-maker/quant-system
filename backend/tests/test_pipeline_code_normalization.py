"""晚间例行「symbol 后缀打挂流水线」回归（2026-09-18）。

故障现象：前端「API 接口状态」显示 ``最近同步异常：ValueError: code 必须 6 位数字: 000001.SZ``；
``data_jobs`` 里昨晚 19:32 的 ``daily_pipeline`` 为 FAILED，predictions 停在 09-17。

根因链（本文件逐环钉死，任何一环被改坏都会变红）：
    ``read_all_symbols("daily_bar")`` 返回**带交易所后缀**的 symbol（实测 2499 只
    全部形如 ``000001.SZ``）
        -> ``jobs/evening_routine`` 把它当作 ``codes`` 传给 ``run_pipeline``
            -> ``step_validate`` 对每个 code 调 ``code_to_symbol``（只认纯 6 位）
                -> ``ValueError``
                    -> ``_run_pipeline_impl`` 是 fail-fast ⇒ 后续 6 步全部不执行。

修法（在流水线**入口**统一规范化，覆盖 evening_routine / ops.dag_rerun / CLI 三条路径）：
    ``domain.a_share_rules.normalize_code`` + ``orchestrator._run_pipeline_impl`` 的
    逐项规范化 + 保序去重。

测试纪律（与 C 段 dead-branch 教训一致）：
- **禁止**用 monkeypatch 把 ``code_to_symbol`` / ``read_all_symbols`` / ``normalize_code``
  换成 stub —— 那等于自证 nothing；
- 集成用例写**真实 parquet 文件**到隔离 DATA_ROOT，再用真实 ``read_all_symbols`` 取回；
- 唯一的 monkeypatch 是「记录型 spy」（透传给真实函数，仅记录入参）与测试隔离
  （DATA_ROOT / 日历 / DB），不替换任何被测逻辑。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app import orchestrator as orchestrator_mod  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.data import calendar_store as calendar_store_mod  # noqa: E402
from app.data.parquet_store import (  # noqa: E402
    manifest_invalidate,
    read_all_symbols,
    write_year_batch,
)
from app.db.init_db import init_database  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.domain.a_share_rules import code_to_symbol, normalize_code  # noqa: E402
from app.domain.calendar import build_calendar  # noqa: E402

# 本模块专用交易日（避开 test_pipeline.py 的 2024-06-05，防止 data_jobs 幂等串扰）
TRADE_DAY = date(2024, 7, 10)      # Wednesday
PREV_TRADE_DAY = date(2024, 7, 9)  # Tuesday

# 本模块专用标的：三段前缀覆盖沪/深/创，且刻意与 test_pipeline.py 的 605888.SH 错开，
# 避免共享 DATA_ROOT 年分区互相覆盖导致套件结果依赖执行顺序。
SYM_SH = "600000.SH"
SYM_SZ = "000001.SZ"
SYM_CY = "300750.SZ"
CODE_SH, CODE_SZ, CODE_CY = "600000", "000001", "300750"


# ---------------- Part A：normalize_code 纯函数单测 ----------------
def test_normalize_code_accepts_plain_six_digits() -> None:
    """裸 6 位代码原样返回（既有口径不变）。"""
    assert normalize_code("600519") == "600519"
    assert normalize_code("000001") == "000001"
    assert normalize_code("300750") == "300750"


def test_normalize_code_strips_exchange_suffix() -> None:
    """带交易所后缀的 symbol 一律脱后缀（这是本 BUG 的直接触发形态）。"""
    assert normalize_code("600519.SH") == "600519"
    assert normalize_code("000001.SZ") == "000001"
    assert normalize_code("831010.BJ") == "831010"


def test_normalize_code_suffix_is_case_insensitive() -> None:
    """后缀大小写都认（上游数据源存在小写后缀，不得因大小写被判非法）。"""
    assert normalize_code("600519.sh") == "600519"
    assert normalize_code("000001.sz") == "000001"


def test_normalize_code_strips_surrounding_whitespace() -> None:
    """首尾空白先 strip 再判定（CLI ``--codes`` 逗号分割后常见残留空格）。"""
    assert normalize_code(" 600519 ") == "600519"
    assert normalize_code("\t600519\n") == "600519"
    assert normalize_code("  600519.SH  ") == "600519"


@pytest.mark.parametrize("bad", ["", "   ", "abc", "60051", "6005190", "60051x", "60-519"])
def test_normalize_code_rejects_malformed_value(bad: str) -> None:
    """非 6 位数字一律拒绝（空串 / 位数不足 / 位数过多 / 含非数字）。"""
    with pytest.raises(ValueError, match="非法标的代码"):
        normalize_code(bad)


@pytest.mark.parametrize("bad", ["600519.SH.XX", "600519.", ".600519", "600519..SH"])
def test_normalize_code_rejects_broken_suffix(bad: str) -> None:
    """多段 / 空后缀属格式损坏：不静默截断，显式拒绝（掩盖上游脏数据更危险）。"""
    with pytest.raises(ValueError, match="非法标的代码"):
        normalize_code(bad)


def test_normalize_code_rejects_non_string() -> None:
    """非字符串入参显式拒绝（不做隐式 str() 转换，避免 None 变成 'None' 蒙混）。"""
    for bad in (None, 600519, 600519.0, ["600519"]):
        with pytest.raises(ValueError, match="非法标的代码"):
            normalize_code(bad)  # type: ignore[arg-type]


def test_normalize_code_error_message_carries_raw_input() -> None:
    """错误信息必须携带原始入参——值班否则无法从日志定位是哪一条脏数据。"""
    with pytest.raises(ValueError) as ei:
        normalize_code("000001.SZ.EXTRA")
    msg = str(ei.value)
    assert "非法标的代码" in msg
    assert "000001.SZ.EXTRA" in msg


def test_code_to_symbol_contract_unchanged() -> None:
    """钉住既有契约：normalize_code 的引入不得放宽/改变 code_to_symbol。

    放宽它会让「入口格式不齐」继续无声蔓延（本仓教训：只改一侧最危险）。
    """
    assert code_to_symbol("600519") == "600519.SH"
    with pytest.raises(ValueError, match="code 必须 6 位数字"):
        code_to_symbol("600519.SH")


# ---------------- Part B：集成——真实 parquet + 真实入口 ----------------
def _seed_bar(symbol: str, d: date) -> None:
    """写一张**真实**日线 parquet（走生产写入路径 write_year_batch）。"""
    pdf = pl.DataFrame({
        "symbol": [symbol], "code": [symbol.split(".")[0]], "date": [d],
        "open": [10.0], "high": [10.2], "low": [9.8], "close": [10.1],
        "volume": [50_000.0], "amount": [505_000.0],
        "pct": [0.005], "turnover": [1.0],
    })
    write_year_batch("daily_bar", symbol, d.year, pdf)


async def _clear_job(trade_date: date) -> None:
    """清理本模块的 daily_pipeline 作业记录（避免 SUCCESS 幂等跳过）。"""
    from sqlalchemy import delete as sa_delete

    from app.db.models import DataJob

    factory = get_session_factory()
    async with factory() as sess:
        await sess.execute(sa_delete(DataJob).where(
            DataJob.job_type == "daily_pipeline",
            DataJob.trade_date == trade_date))
        await sess.commit()


@pytest.fixture()
def pipeline_code_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """隔离 DATA_ROOT 到 tmp_path + 注入日历 + 播 3 只真实日线。

    DATA_ROOT 隔离后必须 ``manifest_invalidate()``：``parquet_store._manifest`` 是
    **进程级**缓存，若此前已有测试在同一进程里为 daily_bar 建过 manifest，切换
    DATA_ROOT 后读到的仍是旧目录的符号集合（顺序敏感 flake 的经典形态）。
    """
    asyncio.run(init_database())
    asyncio.run(_clear_job(TRADE_DAY))

    # 日历：注入含 TRADE_DAY / PREV_TRADE_DAY 的连续工作日
    prev_cache = calendar_store_mod._CACHE
    days = [date(2024, 7, 8) + timedelta(days=i) for i in range(10)]
    calendar_store_mod.set_calendar(build_calendar(d for d in days if d.weekday() < 5))

    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    manifest_invalidate()
    for sym in (SYM_SH, SYM_SZ, SYM_CY):
        _seed_bar(sym, TRADE_DAY)

    yield tmp_path

    manifest_invalidate()          # 还原：清掉本例在 tmp_path 下建的 manifest
    calendar_store_mod._CACHE = prev_cache
    asyncio.run(_clear_job(TRADE_DAY))


def test_read_all_symbols_returns_suffixed_symbols(pipeline_code_env) -> None:
    """钉住根因的第一环：read_all_symbols 返回的是**带后缀** symbol。

    这条断言就是「调用方传错格式」的实锤；若哪天它变绿成裸码，说明上游口径已变，
    入口规范化仍需保留（防御性），但根因需重新评估。
    """
    symbols = read_all_symbols("daily_bar")
    assert sorted(symbols) == [SYM_SZ, SYM_CY, SYM_SH]
    assert all("." in s for s in symbols), f"预期全部带交易所后缀，实际: {symbols}"
    assert sum(1 for s in symbols if "." not in s) == 0


def test_step_validate_rejects_suffixed_codes_without_normalization(pipeline_code_env) -> None:
    """钉住根因的第二环：带后缀 code 直接喂 step_validate 必然抛 ValueError。

    这条用例是「为什么必须在入口规范化」的活证据——它证明仅靠下游无法自愈。
    """
    from app.data.pipeline import step_validate

    with pytest.raises(ValueError, match="code 必须 6 位数字"):
        step_validate(TRADE_DAY, [SYM_SZ])


def test_pipeline_entry_normalizes_suffixed_codes(pipeline_code_env) -> None:
    """核心回归：真实 read_all_symbols 输出喂流水线入口，validate 步骤不再抛错。

    断言链：
    1. 作业 SUCCESS（fail-fast 下任何 ValueError 都会记 FAILED + error_message）；
    2. validate 步骤**收到的入参**已是纯 6 位且已去重（spy 仅记录，透传真实函数）。
    """
    seen: dict[str, list[str]] = {}
    real_validate = orchestrator_mod.STEP_FUNCTIONS["validate"]

    def spy(d: date, codes: list[str]) -> str:
        seen["codes"] = list(codes)
        return real_validate(d, codes)

    orchestrator_mod.STEP_FUNCTIONS["validate"] = spy
    try:
        dirty_codes = read_all_symbols("daily_bar")   # 真实脏输入（带后缀）
        job, executed = orchestrator_mod.run_pipeline(TRADE_DAY, dirty_codes,
                                                      steps=["validate"])
    finally:
        orchestrator_mod.STEP_FUNCTIONS["validate"] = real_validate

    assert executed is True
    assert job is not None and job.status == "SUCCESS", \
        f"流水线仍失败: {job.error_message if job else None}"
    assert job.error_message is None
    assert seen["codes"] == [CODE_SZ, CODE_CY, CODE_SH], \
        f"入口未把带后缀 symbol 规范化为纯 6 位代码: {seen.get('codes')}"


def test_pipeline_entry_dedupes_preserving_order(pipeline_code_env) -> None:
    """去重：同一标的的三种写法（带后缀 / 裸码 / 带空白）必须收敛为 1 项。

    为什么必须去重：step_validate 用 ``len(codes)`` 作分母算容差与覆盖率，
    重复项虚增分母 ⇒ 真实整日丢失会被静默放行（C 段同类教训：静默即最危险）。
    """
    seen: dict[str, list[str]] = {}
    real_validate = orchestrator_mod.STEP_FUNCTIONS["validate"]

    def spy(d: date, codes: list[str]) -> str:
        seen["codes"] = list(codes)
        return real_validate(d, codes)
    orchestrator_mod.STEP_FUNCTIONS["validate"] = spy
    try:
        job, executed = orchestrator_mod.run_pipeline(
            TRADE_DAY, [SYM_SH, CODE_SH, f" {SYM_SH} "], steps=["validate"])
    finally:
        orchestrator_mod.STEP_FUNCTIONS["validate"] = real_validate

    assert executed is True
    assert job is not None and job.status == "SUCCESS", \
        f"流水线失败: {job.error_message if job else None}"
    assert seen["codes"] == [CODE_SH], \
        f"重复/异形写法未被收敛为单项: {seen.get('codes')}"


def test_normalized_codes_read_correct_partition(pipeline_code_env) -> None:
    """规范化后确实读到「该标的」的分区：不能全部被误判为停牌跳过。

    直接调 ``step_validate``（真实函数、真实 parquet）：
    - 3 只当日均有数据 ⇒ 全部计入 n_ok（validated=3/3、suspended=0、missing=0）；
    - 混入一只**从未落库**的标的 ⇒ 它计入 suspended，而有数据的那只仍为 n_ok。
    """
    from app.data.pipeline import step_validate

    detail = step_validate(TRADE_DAY, [CODE_SH, CODE_SZ, CODE_CY])
    assert "validated=3/3" in detail, f"规范化后未读到正确分区: {detail}"
    assert "suspended=0" in detail and "missing_vs_prev=0" in detail, detail

    # 混入一只从未落库的标的：应记为停牌/未上市，且不得影响有数据的那一只
    detail2 = step_validate(TRADE_DAY, [CODE_SH, "999999"])
    assert "validated=1/2" in detail2, f"有数据的标的未被计入 n_ok: {detail2}"
    assert "suspended=1" in detail2, detail2


def test_pipeline_entry_rejects_malformed_code_with_context(pipeline_code_env) -> None:
    """入口对**真正非法**的入参不得放行：报错必须带上原始脏值（便于排障）。

    与「带后缀」区分：后缀是格式不齐（可规范化），非数字/位数错是脏数据（须拒绝）。
    """
    with pytest.raises(ValueError) as ei:
        orchestrator_mod._run_pipeline_impl(TRADE_DAY, ["600519", "60X.SZ"],
                                            steps=["validate"])
    msg = str(ei.value)
    assert "非法标的" in msg
    assert "60X.SZ" in msg, f"错误信息未携带原始入参: {msg}"


def test_pipeline_none_codes_falls_back_to_default(pipeline_code_env) -> None:
    """``codes=None`` 仍落到默认裸码集（规范化不得吞掉兜底语义）。"""
    seen: dict[str, list[str]] = {}
    real_validate = orchestrator_mod.STEP_FUNCTIONS["validate"]

    def spy(d: date, codes: list[str]) -> str:
        seen["codes"] = list(codes)
        return "ok"
    orchestrator_mod.STEP_FUNCTIONS["validate"] = spy
    try:
        orchestrator_mod._run_pipeline_impl(TRADE_DAY, None, steps=["validate"])
    finally:
        orchestrator_mod.STEP_FUNCTIONS["validate"] = real_validate
    assert seen["codes"] == ["600519", "000001", "300750"]
