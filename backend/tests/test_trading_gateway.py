"""交易网关抽象接口 (Broker Gateway Protocol) 单元测试。"""
from __future__ import annotations

import pytest
from app.trading.gateway import (
    AccountInfo,
    BaseBrokerGateway,
    OrderRequest,
    OrderResponse,
    PaperBrokerGateway,
    PositionInfo,
)


def test_gateway_interface_inheritance():
    """验证 BaseBrokerGateway 抽象契约。"""
    assert issubclass(PaperBrokerGateway, BaseBrokerGateway)
    gw = PaperBrokerGateway(account_id="test_acc")
    assert not gw.is_connected()
    assert gw.connect()
    assert gw.is_connected()
    gw.disconnect()
    assert not gw.is_connected()


def test_gateway_disconnected_query_raises():
    """未连接时查询应抛出 ConnectionError。"""
    gw = PaperBrokerGateway()
    with pytest.raises(ConnectionError, match="柜台未连接"):
        gw.query_account()

    with pytest.raises(ConnectionError, match="柜台未连接"):
        gw.query_positions()

    with pytest.raises(ConnectionError, match="柜台未连接"):
        gw.submit_order(OrderRequest(symbol="600519.SH", side="buy", volume=100))


def test_gateway_query_account_and_positions():
    """连接后查询真实账户与持仓结构体。"""
    gw = PaperBrokerGateway()
    gw.connect()
    acc = gw.query_account()
    assert isinstance(acc, AccountInfo)
    assert acc.total_asset > 0
    assert acc.currency == "CNY"

    pos_list = gw.query_positions()
    assert isinstance(pos_list, list)
    for pos in pos_list:
        assert isinstance(pos, PositionInfo)
        assert pos.symbol != ""
        assert pos.volume >= 0
