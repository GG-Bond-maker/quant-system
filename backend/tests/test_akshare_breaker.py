"""回归守卫（2026-09-23 报障）：AKShare 数据源熔断器。

背景：用户报「市场概览：实时数据源响应超时」。根因是东财对本机**快速失败**
（实测 0.19~0.57s/次），但 ``AKSHARE_RETRY(3)`` × 指数退避 × 全局限速把单个子块
放大到 6~17s，远超响应预算。修法是「按接口粒度熔断 + 让 ``_should_retry`` 在源已
判定不可达时立即停止重试」。

测试纪律：
- 直接测 ``_safe_call`` 的真实行为；只有 ``_throttle`` 打桩（去掉 1.2s 限速等待），
  ``_SourceBreaker`` / ``_retry_decorator`` / ``_should_retry`` 一律用真实实现。
- 实测：撤掉 ``_throttled_call`` 的熔断短路（变异 M2）时本文件的前两个用例会变红。
  此前全套件**无任何用例**覆盖该短路（全仓 grep 只有 ``conftest`` 的 reset），
  故那处修复形同未被守卫。
"""
from __future__ import annotations

import pytest

from app.data.ingest import akshare_adapter as ada


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """去掉 1.2s 全局限速等待（与被测语义无关），并在前后清空熔断状态。"""
    monkeypatch.setattr(ada, "_throttle", lambda: None)
    ada.reset_source_breaker()
    yield
    ada.reset_source_breaker()


def test_connection_failure_opens_breaker_and_short_circuits_next_call():
    """连接类失败：1 次外呼即熔断，冷却期内的后续调用必须短路（不外呼、不重试）。"""
    calls: list[int] = []

    def boom():  # noqa: ANN202
        calls.append(1)
        raise ConnectionError("模拟远端断连")

    wrapped = ada._retry_decorator()(ada._throttled_call)

    with pytest.raises(ConnectionError):
        wrapped(boom)
    assert len(calls) == 1, (
        "熔断开启后不得再退避重试——否则又回到『6~17s 堆出来』的老问题")

    key = ada._breaker_key(boom)
    assert ada._breaker.is_open(key) is True, "连接类失败必须打开熔断"

    with pytest.raises(ada.DataSourceUnavailable):
        wrapped(boom)
    assert len(calls) == 1, "冷却期内的调用不得打到源站"


def test_schema_error_does_not_open_breaker():
    """schema 类异常（接口变更）不得触发熔断，仍按原语义重试。"""
    calls: list[int] = []

    def schema_boom():  # noqa: ANN202
        calls.append(1)
        raise KeyError("列名变更")

    wrapped = ada._retry_decorator()(ada._throttled_call)

    with pytest.raises(KeyError):
        wrapped(schema_boom)
    assert len(calls) > 1, "schema 类异常未被熔断误伤，仍应重试"
    assert ada._breaker.is_open(ada._breaker_key(schema_boom)) is False


def test_success_path_is_not_retried():
    """成功路径不得被 tenacity 误判为"需重试"。

    工程实现曾一度在 ``retry`` 判据里对**成功**结果也返回 True，导致一次成功被
    空转到 ``stop_after_attempt`` 后抛 ``RetryError[<Future ... returned DataFrame>]``。
    """
    calls: list[int] = []

    def ok():  # noqa: ANN202
        calls.append(1)
        return "REAL"

    wrapped = ada._retry_decorator()(ada._throttled_call)

    assert wrapped(ok) == "REAL"
    assert len(calls) == 1, "成功不得触发重试"


def test_breaker_reset_restores_traffic():
    """``reset_source_breaker``（conftest 用例隔离所依赖）必须真的解锁。"""
    def boom():  # noqa: ANN202
        raise ConnectionError("模拟远端断连")

    wrapped = ada._retry_decorator()(ada._throttled_call)
    with pytest.raises(ConnectionError):
        wrapped(boom)
    assert ada._breaker.is_open(ada._breaker_key(boom)) is True

    ada.reset_source_breaker()
    assert ada._breaker.is_open(ada._breaker_key(boom)) is False, (
        "未复位会让后续用例拿到 DataSourceUnavailable 而非预期异常")
