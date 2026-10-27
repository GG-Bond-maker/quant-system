"""统一交易柜台网关抽象接口（Broker Gateway Protocol，对标 CTP/QMT/PTrade）。

统一实盘与模拟盘接口，涵盖：
1. 柜台连接与断开管理；
2. 资金账户与持仓穿透查询；
3. 报单生命周期（挂单、撤单、部分成交、全部成交、拒单）；
4. 真实交易滑点与摩擦税费模型对接。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from loguru import logger


@dataclass
class AccountInfo:
    """账户资金信息快照（对标 CTP CThostFtdcTradingAccountField / QMT Account）。"""

    account_id: str
    total_asset: float | None
    cash: float
    market_value: float | None
    frozen_cash: float = 0.0
    currency: str = "CNY"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PositionInfo:
    """标的持仓明细（对标 CTP CThostFtdcInvestorPositionField）。"""

    symbol: str
    volume: int
    available_volume: int           # A 股 T+1 交易制度下可用股数
    avg_price: float
    market_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    realized_pnl: float = 0.0


@dataclass
class OrderRequest:
    """委托报单请求结构体（对标 CTP CThostFtdcInputOrderField）。"""

    symbol: str
    side: Literal["buy", "sell", "open_long", "close_long", "open_short", "close_short"]
    volume: int                      # 委托股数/张数
    price: float | None = None       # 限价委托价格（市价/算法单为 None）
    order_type: Literal["limit", "market", "vwap", "twap"] = "limit"
    algo_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResponse:
    """报单状态回报（对标 CTP CThostFtdcOrderField）。"""

    order_id: str
    symbol: str
    status: Literal["PENDING", "PART_FILLED", "FILLED", "CANCELLED", "REJECTED"]
    submitted_volume: int
    filled_volume: int
    filled_amount: float
    avg_filled_price: float
    commission: float = 0.0
    stamp_duty: float = 0.0
    message: str = ""
    created_at: datetime = field(default_factory=datetime.now)


class BaseBrokerGateway(ABC):
    """统一交易柜台网关抽象基类（Abstract Gateway Interface）。"""

    @abstractmethod
    def connect(self) -> bool:
        """连接柜台交易接口。"""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """断开柜台连接。"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """柜台长连接状态检测。"""
        ...

    @abstractmethod
    def query_account(self) -> AccountInfo:
        """查询账户最新资金资产快照。"""
        ...

    @abstractmethod
    def query_positions(self) -> list[PositionInfo]:
        """查询当前全部持仓列表。"""
        ...

    @abstractmethod
    def submit_order(self, request: OrderRequest) -> OrderResponse:
        """向柜台提交委托报单。"""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """向柜台发送撤单指令。"""
        ...


class PaperBrokerGateway(BaseBrokerGateway):
    """基于本地模拟盘引擎 (trading.paper) 的标准柜台适配器。

    支持对标真实外部柜台生命周期，复用真实印花税、最低 5 元佣金与 T+1 持仓约束。
    """

    def __init__(self, account_id: str = "paper_default") -> None:
        self.account_id = account_id
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        logger.info(f"[PaperBrokerGateway] 模拟柜台已连接，账户: {self.account_id}")
        return True

    def disconnect(self) -> None:
        self._connected = False
        logger.info(f"[PaperBrokerGateway] 模拟柜台已断开，账户: {self.account_id}")

    def is_connected(self) -> bool:
        return self._connected

    def query_account(self) -> AccountInfo:
        if not self._connected:
            raise ConnectionError("柜台未连接，无法查询账户")
        from .paper import account_summary, sync_session_factory
        factory = sync_session_factory()
        with factory() as session:
            summary = account_summary(session, account_id=self.account_id)
        return AccountInfo(
            account_id=self.account_id,
            total_asset=summary.get("equity", summary.get("cash", 0.0)),
            cash=summary.get("cash", 0.0),
            market_value=summary.get("market_value", 0.0),
            frozen_cash=summary.get("frozen_cash", 0.0),
            currency="CNY",
        )

    def query_positions(self) -> list[PositionInfo]:
        if not self._connected:
            raise ConnectionError("柜台未连接，无法查询持仓")
        from .paper import account_summary, sync_session_factory
        factory = sync_session_factory()
        with factory() as session:
            summary = account_summary(session, account_id=self.account_id)
        positions = []
        marks = summary.get("positions", {})
        if isinstance(marks, dict):
            for sym, p in marks.items():
                vol = int(p.get("qty", 0))
                px = p.get("last_price")
                cost = float(p.get("cost_price", 0.0))
                val = p.get("market_value")
                pnl = p.get("pnl")
                positions.append(
                    PositionInfo(
                        symbol=sym,
                        volume=vol,
                        available_volume=int(p.get("available_qty", 0)),
                        avg_price=cost,
                        market_price=px,
                        market_value=val,
                        unrealized_pnl=pnl,
                    )
                )
        return positions

    def submit_order(self, request: OrderRequest) -> OrderResponse:
        if not self._connected:
            raise ConnectionError("柜台未连接，无法提交委托")
        from .paper import _bars, place_order, sync_session_factory
        factory = sync_session_factory()
        if request.side not in ("buy", "sell"):
            return OrderResponse("", request.symbol, "REJECTED", request.volume, 0, 0.0, 0.0,
                                 message="现货模拟柜台仅支持buy/sell，不支持期货/融券方向")
        bars = _bars(request.symbol)
        reference = request.price or (float(bars["close"][-1]) if not bars.is_empty() else 0.0)
        with factory() as session:
            result = place_order(
                session, account_id=self.account_id, symbol=request.symbol, side=request.side,
                order_amount=request.volume * reference,
                target_qty=request.volume,
                algo=request.order_type if request.order_type in ("vwap", "twap") else "market",
                split_days=int(request.algo_params.get("split_days", 1)),
                participation_cap=float(request.algo_params.get("participation_cap", .05)),
                price_mode="limit" if request.order_type == "limit" else "market",
                limit_price=request.price,
                client_order_id=request.algo_params.get("client_order_id"),
                strategy_version=request.algo_params.get("strategy_version"),
            )
            session.commit()
        return OrderResponse(
            str(result.get("order_id", "")), request.symbol,
            "PENDING" if result["ok"] else "REJECTED", request.volume, 0, 0.0, 0.0,
            message="已提交模拟委托" if result["ok"] else result["reason"],
        )

    def cancel_order(self, order_id: str) -> bool:
        if not self._connected:
            raise ConnectionError("柜台未连接，无法撤单")
        from .paper import cancel_order, sync_session_factory
        factory = sync_session_factory()
        with factory() as session:
            ok = cancel_order(session, int(order_id), account_id=self.account_id)
            session.commit()
            return ok
