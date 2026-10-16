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
    total_asset: float
    cash: float
    market_value: float
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
    market_price: float
    market_value: float
    unrealized_pnl: float
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
        from .paper import sync_session_factory, account_summary
        factory = sync_session_factory()
        with factory() as session:
            summary = account_summary(session)
        return AccountInfo(
            account_id=self.account_id,
            total_asset=summary.get("equity", summary.get("cash", 0.0)),
            cash=summary.get("cash", 0.0),
            market_value=summary.get("market_value", 0.0),
            frozen_cash=0.0,
            currency="CNY",
        )

    def query_positions(self) -> list[PositionInfo]:
        if not self._connected:
            raise ConnectionError("柜台未连接，无法查询持仓")
        from .paper import sync_session_factory, account_summary
        factory = sync_session_factory()
        with factory() as session:
            summary = account_summary(session)
        positions = []
        marks = summary.get("positions", {})
        if isinstance(marks, dict):
            for sym, p in marks.items():
                vol = int(p.get("qty", 0))
                px = float(p.get("last_price", 0.0))
                cost = float(p.get("cost_price", 0.0))
                val = float(p.get("market_value", 0.0))
                pnl = float(p.get("pnl", 0.0))
                positions.append(
                    PositionInfo(
                        symbol=sym,
                        volume=vol,
                        available_volume=vol,
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
        from .paper import sync_session_factory, submit_parent_order
        factory = sync_session_factory()
        # 委托金额估算
        price_ref = request.price or 10.0
        amount = float(request.volume * price_ref)
        # 映射期货与双向单方向至 underlying paper 引擎（buy/sell）
        engine_side = "buy" if request.side in ("buy", "open_long", "close_short") else "sell"
        with factory() as session:
            try:
                order_id, n_slices = submit_parent_order(
                    session=session,
                    symbol=request.symbol,
                    side=engine_side,
                    order_amount=amount,
                    algo=request.order_type if request.order_type in ("vwap", "twap") else "market",
                    limit_price=request.price,
                    split_days=request.algo_params.get("split_days", 1),
                )
                session.commit()
                return OrderResponse(
                    order_id=order_id,
                    symbol=request.symbol,
                    status="PENDING",
                    submitted_volume=request.volume,
                    filled_volume=0,
                    filled_amount=0.0,
                    avg_filled_price=0.0,
                    message=f"已成功提交模拟柜台，生成 {n_slices} 笔子单",
                )
            except Exception as e:
                session.rollback()
                return OrderResponse(
                    order_id="",
                    symbol=request.symbol,
                    status="REJECTED",
                    submitted_volume=request.volume,
                    filled_volume=0,
                    filled_amount=0.0,
                    avg_filled_price=0.0,
                    message=f"报单被风控拦截或拒绝: {e}",
                )

    def cancel_order(self, order_id: str) -> bool:
        if not self._connected:
            raise ConnectionError("柜台未连接，无法撤单")
        from .paper import sync_session_factory, cancel_parent_order
        factory = sync_session_factory()
        with factory() as session:
            ok, _ = cancel_parent_order(session, order_id)
            session.commit()
            return ok
