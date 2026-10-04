"""数据中心「任务结论」展示语义回归（2026-10-04）。

背景（实测 2026-09-30）：一次 ``--codes 000007`` 的单标的重跑被 ``step_validate``
的**纯比例**容差判为 100% 丢失（``n_expected=1`` ⇒ 容差 0.05 只，``1 > 0.05`` ⇒
致命）。失败行落进 ``data_jobs`` 后，``/overview`` 把它渲染成「最近同步异常：…」，
前端再对任何非 green 无条件追加「数据源可能不可用，请检查网络/代理或稍后重试同步」
⇒ **"一次单标的校验未通过"被读成"市场级数据源故障"**，且在国庆假期里挂到 10-04
仍无人能判断它到底是不是真故障。

本文件钉死三条修正（都只补展示层缺失的信息，**不改任何校验判据**）：

① **语义**：校验类失败不得写成"数据源不可用"（校验失败恰恰说明接口是通的）；
② **规模**：必须披露代码集规模与**来源**（``recorded`` 记录值 / ``message`` 由错误
   文本推断 / ``unknown`` 未知），否则个位数分母下的 100% 丢失与市场级故障在文案上
   完全同形、无法区分；
③ **时效**：久未复核的失败行必须标陈旧并披露当前覆盖，避免把 4 天前的结论当"当前
   状态"。

每个用例的 docstring 写明**反证方式**（注入什么缺陷会让它变红）。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1 import datacenter as dc
from app.cache.redis_client import RedisClient
from app.data.pipeline import classify_job_scope
from app.services import sync_service

# 与 models.DataJob 同形状（新增列必须一起补，否则 SELECT 报错被 except 吞掉 ⇒ 假绿）
_DATA_JOBS_DDL = """
CREATE TABLE data_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    traceback TEXT,
    duration_ms INTEGER NOT NULL,
    codes_count INTEGER,
    job_scope TEXT,
    task_id TEXT,
    created_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    updated_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    UNIQUE (job_type, trade_date)
)
"""

# 真实历史行（job id=20，2026-09-30 18:37）的原文，一字不改。
_LEGACY_VALIDATE_ERR = "ValueError: validate failed: 昨有今无 1/1 只（阈值 5%）: 000007"
# 2026-10-01 之后的代码会带上规模，这里用于"记录在案"的用例。
_NEW_VALIDATE_ERR = ("ValueError: validate failed: 昨有今无 1/1 只（阈值 5%；"
                     "当日已落库 0/1 = 0.0%）: 000007")


def _job_row(*, status: str = "FAILED", error: str | None = _LEGACY_VALIDATE_ERR,
             finished_at: str | None = None, trade_date: str = "2026-09-14",
             codes_count: int | None = None, job_scope: str | None = None,
             job_type: str = "daily_pipeline", current_step: str = "validate",
             duration_ms: int = 1000, job_id: int = 1,
             task_id: str | None = None) -> tuple:
    """与 ``/overview`` 那条 SELECT 的列序**逐列对齐**的 11 元组。

    ⚠️ 列序必须与 ``datacenter.overview`` 的 SELECT 完全一致（``id, job_type,
    status, trade_date, current_step, COALESCE(finished_at, started_at),
    error_message, duration_ms, codes_count, job_scope, task_id``）：``_build_last_job``
    按**位置**解包，错位会静默把 ``trade_date`` 当成 ``job_type``——本文件初版即因
    列序不一致让"失败日已进入覆盖范围"这条时效判据静默失效。
    写入时用 :func:`_insert_values` 重排成 INSERT 的列序。

    ``task_id``（B1+，2026-10-04）：该行对应的 ``task_store`` id。
    """
    return (job_id, job_type, status, trade_date, current_step,
            finished_at, error, duration_ms, codes_count, job_scope, task_id)


def _insert_values(row: tuple) -> tuple:
    """SELECT 列序 → INSERT 列序（``started_at`` 一律 NULL，只用 ``finished_at``）。"""
    (job_id, job_type, status, trade_date, current_step,
     finished_at, error, duration_ms, codes_count, job_scope, task_id) = row
    return (job_type, trade_date, status, current_step, None,
            finished_at, error, duration_ms, codes_count, job_scope, task_id)


def _overview_data(tmp_path: Path, monkeypatch, rows: list[tuple], *,
                   bar_last: str = "2026-09-14") -> dict:
    """构造 tmp DATA_ROOT + sqlite，调用真实 ``/overview``，返回响应 data。"""
    db_path = tmp_path / "aqp.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE instrument (symbol TEXT)")
        conn.execute("INSERT INTO instrument VALUES ('600519.SH')")
        conn.execute(_DATA_JOBS_DDL)
        conn.executemany(
            "INSERT INTO data_jobs (job_type, trade_date, status, current_step, "
            "started_at, finished_at, error_message, duration_ms, codes_count, "
            "job_scope, task_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_insert_values(r) for r in rows])
        conn.commit()
    manifest = {"daily_bar": {
        "600519.SH": {"rows": 2, "first": "2026-09-11", "last": bar_last}}}
    (tmp_path / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pq = tmp_path / "daily_bar" / "symbol=600519.SH"
    pq.mkdir(parents=True)
    (pq / "year=2026.parquet").write_bytes(b"test")

    monkeypatch.setattr(dc, "get_settings",
                        lambda: SimpleNamespace(DATA_ROOT=tmp_path, SQLITE_PATH=db_path))
    cache: dict[str, bytes] = {}

    async def fake_get_stale(cls, key: str) -> tuple[bytes | None, bool]:
        return cache.get(key), False

    async def fake_set(cls, key: str, value: bytes, **kwargs) -> None:
        cache[key] = value

    monkeypatch.setattr(RedisClient, "get_stale", classmethod(fake_get_stale))
    monkeypatch.setattr(RedisClient, "set", classmethod(fake_set))
    return asyncio.run(dc.overview(refresh=0, _user={"username": "alice"})).data


# ---------------------------------------------------------------------------
# 规模判据：与全市场硬门禁同源（阈值不得分叉）
# ---------------------------------------------------------------------------
def test_classify_job_scope_boundaries() -> None:
    """``classify_job_scope`` 在阈值边界上的取值。

    反证：把阈值改成 100（或把 ``>=`` 写成 ``>``）⇒ 499/500 两处断言变红。
    ``None`` 必须回 ``None``（未知），**不得**回 ``subset``——否则历史行会被
    当成"小规模"从而掩盖真市场级故障。
    """
    assert classify_job_scope(None) is None
    assert classify_job_scope(0) == "subset"
    assert classify_job_scope(1) == "subset"
    assert classify_job_scope(499) == "subset"
    assert classify_job_scope(500) == "market"
    assert classify_job_scope(5212) == "market"
    # 与全市场硬门禁**共用同一阈值**（两处判据分叉即缺陷）
    from app.data import pipeline as pl

    assert classify_job_scope(pl.VALIDATE_MIN_MARKET_UNIVERSE - 1) == "subset"
    assert classify_job_scope(pl.VALIDATE_MIN_MARKET_UNIVERSE) == "market"


# ---------------------------------------------------------------------------
# ① 语义 + ② 规模：历史行（无规模元数据）
# ---------------------------------------------------------------------------
def test_legacy_validate_failure_discloses_inferred_scale_and_stale(
        tmp_path: Path, monkeypatch) -> None:
    """历史行（``codes_count`` 为 NULL）：规模由错误文本推断并标注来源，且标陈旧。

    反证：① 去掉规模推断（scope_source 恒 unknown）⇒ scope_note 断言变红；
    ② 去掉时效判据 ⇒ stale/age_days 断言变红；
    ③ 把前缀改回「最近同步异常：」⇒ "最近同步异常" not in 断言变红。
    """
    finished = (date.today() - timedelta(days=4)).strftime("%Y-%m-%d 18:37:22")
    data = _overview_data(tmp_path, monkeypatch, [
        _job_row(error=_LEGACY_VALIDATE_ERR, finished_at=finished)])

    # ① 校验类失败不得写成"数据源不可用"
    msg = data["akshare_message"]
    assert msg.startswith("最近一次流水线校验未通过（非数据源/网络故障）："), msg
    assert "最近同步异常" not in msg, msg
    assert _LEGACY_VALIDATE_ERR in msg, "错误原文不得被改写"
    # 推断值**不得**据此把健康灯点绿（推断可能低估真实代码集）
    assert data["akshare_health"] == "yellow", data["akshare_health"]

    # ② 规模披露 + 来源标注
    lj = data["last_job"]
    assert lj["codes_count"] is None, "历史行不得用 0 冒充规模"
    assert lj["job_scope"] == "subset"
    assert lj["scope_source"] == "message"
    assert "下界" in lj["scope_note"], lj["scope_note"]
    assert "未记录规模元数据" in lj["scope_note"], lj["scope_note"]
    assert lj["verdict"] == "failed_subset"

    # ③ 时效
    assert lj["age_days"] == 4, lj["age_days"]
    assert lj["stale"] is True
    assert "已 4 天未复核" in lj["stale_note"], lj["stale_note"]

    # 建议方向必须正确：本例与网络无关
    assert "与网络/代理无关" in lj["advice"], lj["advice"]
    assert "不构成市场级同步异常的证据" in lj["advice"], lj["advice"]


# ---------------------------------------------------------------------------
# ② 规模（记录值）：小规模子集失败不得被当成市场级同步故障
# ---------------------------------------------------------------------------
def test_recorded_subset_failure_does_not_claim_datasource_fault(
        tmp_path: Path, monkeypatch) -> None:
    """记录在案的 subset 失败（codes_count=1）：接口健康灯不点黄，失败仍如实展示。

    反证：把 ``scope_source == "recorded"`` 这个前提去掉（推断值也降级）⇒ 本用例
    仍绿，但 ``test_legacy_validate_failure_...`` 会红——两条判据互为约束。
    """
    today = date.today()
    finished = (today - timedelta(days=1)).strftime("%Y-%m-%d 18:37:22")
    # bar_last 距今日 4 天：不触发既有的「行情已 >5 天未更新」降级规则；
    # fail_day 晚于 bar_last：不触发「失败日已进入覆盖范围」这条时效判据。
    # 两者都用相对日期，避免用例随 wall clock 漂移。
    data = _overview_data(tmp_path, monkeypatch, [
        _job_row(error=_NEW_VALIDATE_ERR, finished_at=finished,
                 trade_date=(today - timedelta(days=3)).isoformat(),
                 codes_count=1, job_scope="subset")],
        bar_last=(today - timedelta(days=4)).isoformat())

    assert data["akshare_health"] == "green", data["akshare_health"]
    assert "小规模子集" in data["akshare_message"], data["akshare_message"]
    # 接口状态文案**不得**出现"数据源"字样（那是市场级故障的措辞）
    assert "数据源" not in data["akshare_message"], data["akshare_message"]

    lj = data["last_job"]
    assert lj["verdict"] == "failed_subset"
    assert lj["scope_source"] == "recorded"
    assert lj["codes_count"] == 1
    assert lj["scope_note"] == "本次代码集 1 只", lj["scope_note"]
    assert lj["stale"] is False, lj["stale_note"]
    # 建议同样必须指向"校验未通过"，不得指向网络（证伪 R2 曾因缺此断言而漏检）
    assert "与网络/代理无关" in lj["advice"], lj["advice"]
    assert "小规模子集任务" in lj["advice"], lj["advice"]
    # 失败本身必须仍然可见（不是被隐藏）
    assert lj["status"] == "FAILED"
    assert _NEW_VALIDATE_ERR in lj["error_message"]


def test_recorded_market_failure_still_warns(tmp_path: Path, monkeypatch) -> None:
    """记录在案的**市场级**失败（codes_count=5212）：仍点黄灯 + 校验类前缀。

    反证：把降级分支的条件放宽（不判 scope）⇒ 本用例 health 变 green 而变红。
    """
    today = date.today()
    finished = (today - timedelta(days=1)).strftime("%Y-%m-%d 18:37:22")
    data = _overview_data(tmp_path, monkeypatch, [
        _job_row(error=_NEW_VALIDATE_ERR, finished_at=finished,
                 trade_date=(today - timedelta(days=3)).isoformat(),
                 codes_count=5212, job_scope="market")],
        bar_last=(today - timedelta(days=4)).isoformat())

    assert data["akshare_health"] == "yellow", data["akshare_health"]
    assert data["akshare_message"].startswith(
        "最近一次流水线校验未通过（非数据源/网络故障）："), data["akshare_message"]
    lj = data["last_job"]
    assert lj["verdict"] == "failed", lj["verdict"]
    assert lj["job_scope"] == "market"
    assert lj["scope_source"] == "recorded"


# ---------------------------------------------------------------------------
# 建议必须由真实错误文本派生，不得无条件指向网络
# ---------------------------------------------------------------------------
def test_connection_failure_advice_points_to_network(tmp_path: Path, monkeypatch) -> None:
    """连接类失败（ProxyError）才提示网络/代理，且前缀仍是"同步异常"。

    反证：把 ``_CONN_HINTS`` 清空 ⇒ advice 变成"原因未归类"而变红。
    """
    err = "ProxyError: HTTPSConnectionPool(host='push2.eastmoney.com', port=443)"
    data = _overview_data(tmp_path, monkeypatch, [
        _job_row(error=err, finished_at="2026-09-14 18:37:22")])

    assert data["akshare_message"].startswith("最近同步异常："), data["akshare_message"]
    assert "请检查网络/代理" in data["last_job"]["advice"], data["last_job"]["advice"]


def test_unclassified_failure_refuses_to_guess_network(
        tmp_path: Path, monkeypatch) -> None:
    """未归类的错误：如实说"未归类"，**不得**给出网络建议（判据：不把猜测写成结论）。

    反证：把兜底分支改成返回网络建议 ⇒ 本用例变红。
    """
    data = _overview_data(tmp_path, monkeypatch, [
        _job_row(error="RuntimeError: 某种未知故障", finished_at="2026-09-14 18:37:22")])

    advice = data["last_job"]["advice"]
    assert "原因未归类" in advice, advice
    assert "网络/代理" not in advice, advice


def test_success_job_green_and_no_advice(tmp_path: Path, monkeypatch) -> None:
    """SUCCESS：绿灯、无建议、verdict=success。

    反证：把 advice 改成非 FAILED 也返回文案 ⇒ 本用例变红。
    """
    today = date.today()
    data = _overview_data(tmp_path, monkeypatch, [
        _job_row(status="SUCCESS", error=None,
                 finished_at=today.strftime("%Y-%m-%d 15:45:26"),
                 trade_date=today.isoformat(),
                 codes_count=5212, job_scope="market")],
        bar_last=today.isoformat())

    assert data["akshare_health"] == "green"
    assert data["akshare_message"] == "AKShare 连接正常，最近同步成功"
    lj = data["last_job"]
    assert lj["verdict"] == "success"
    assert lj["advice"] == ""
    # 成功行没有可陈旧的告警（stale 只对 FAILED 计算）
    assert lj["stale"] is False, lj["stale_note"]


# ---------------------------------------------------------------------------
# ③ 时效判据的边界（纯函数，用固定 today 保证确定性）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("age,expect_stale", [(3, False), (4, True)])
def test_stale_threshold_boundary(age: int, expect_stale: bool) -> None:
    """时效阈值边界：3 天不算陈旧，4 天算（阈值 ``_JOB_ALERT_STALE_DAYS``=3）。

    反证：把阈值改成 4 ⇒ age=3 的期望反转、用例变红。
    """
    today = date(2026, 10, 4)
    fin = (today - timedelta(days=age)).strftime("%Y-%m-%d 18:37:22")
    # daily_bar_end 刻意早于失败日，隔离出"久未复核"这一条判据
    out = dc._build_last_job(
        _job_row(finished_at=fin, trade_date="2026-09-30"), "2026-09-01", today)
    assert out is not None
    assert out["age_days"] == age
    assert out["stale"] is expect_stale, out["stale_note"]


def test_covered_failure_day_is_disclosed_but_not_overclaimed() -> None:
    """失败日已进入 daily_bar 覆盖范围 ⇒ 必须披露，且**不得**声称已修复。

    反证：把该条时效判据删掉 ⇒ stale 变 False；把措辞改成"已修复"⇒ 第二句断言变红。
    """
    today = date(2026, 10, 4)
    out = dc._build_last_job(
        _job_row(finished_at="2026-10-03 18:37:22", trade_date="2026-09-30"),
        "2026-09-30", today)
    assert out is not None
    assert out["stale"] is True
    assert "已进入 daily_bar 覆盖范围" in out["stale_note"], out["stale_note"]
    assert "不证明涉及标的已修复" in out["stale_note"], out["stale_note"]


# ---------------------------------------------------------------------------
# 存储侧：sync 终态落库必须带规模（否则展示层永远只能靠推断）
# ---------------------------------------------------------------------------
@pytest.fixture
def sync_job_db(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(_DATA_JOBS_DDL)
        conn.commit()
    monkeypatch.setattr(sync_service, "get_settings",
                        lambda: SimpleNamespace(SQLITE_PATH=db_path))
    return db_path


def _scope_row(db_path: Path) -> tuple:
    with sqlite3.connect(str(db_path)) as conn:
        r = conn.execute("SELECT codes_count, job_scope FROM data_jobs").fetchone()
    assert r is not None, "data_jobs 未写入"
    return r


@pytest.mark.parametrize("codes,expect", [
    (5212, (5212, "market")),
    (1, (1, "subset")),
    (0, (None, None)),      # 0 = 本轮无待处理标的 ⇒ 规模未知，不得写成 subset
    (None, (None, None)),
])
def test_record_sync_job_persists_scope(sync_job_db, codes, expect) -> None:
    """``_record_sync_job`` 落库规模与范围；0/None 落 NULL（不得用 0 冒充）。

    反证：把 ``_codes = codes_count if codes_count else None`` 改成直接透传 ⇒
    codes=0 的用例落成 (0, 'subset') 而变红。
    """
    sync_service._record_sync_job("incremental", "SUCCESS", 1234, None, codes)
    assert _scope_row(sync_job_db) == expect


def test_record_sync_job_upsert_overwrites_scope(sync_job_db) -> None:
    """同日同模式 upsert 必须一并覆盖规模列（否则残留上一次的规模）。

    反证：把 SQL 的 ``codes_count=excluded.codes_count`` 删掉 ⇒ 本用例读到旧值而变红。
    """
    sync_service._record_sync_job("incremental", "FAILED", 10, "boom", 5212)
    assert _scope_row(sync_job_db) == (5212, "market")
    sync_service._record_sync_job("incremental", "SUCCESS", 20, None, 3)
    assert _scope_row(sync_job_db) == (3, "subset")
