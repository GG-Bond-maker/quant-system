"""v1 路由统一注册（AQP）。"""
from fastapi import APIRouter

from .alerts import router as alerts_router
from .auth import router as auth_router
from .app_settings import router as app_settings_router
from .backtest import router as backtest_router
from .datacenter import router as datacenter_router
from .etf import router as etf_router
from .export import router as export_router
from .market import router as market_router
from .monitor import router as monitor_router
from .notify import router as notify_router
from .portfolio import router as portfolio_router
from .report import router as report_router
from .research import router as research_router
from .screener import router as screener_router
from .studio import router as studio_router
from .ops import router as ops_router
from .desk import router as desk_router
from .stock import router as stock_router
from .watchlist import router as watchlist_router

v1_router = APIRouter()

v1_router.include_router(auth_router, prefix="/auth", tags=["auth"])
v1_router.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
v1_router.include_router(market_router, prefix="/market", tags=["market"])
v1_router.include_router(notify_router, prefix="/notify", tags=["notify"])
v1_router.include_router(stock_router, prefix="/stock", tags=["stock"])
v1_router.include_router(etf_router, prefix="/etf", tags=["etf"])
v1_router.include_router(datacenter_router, prefix="/datacenter", tags=["datacenter"])
v1_router.include_router(watchlist_router, prefix="/watchlist", tags=["watchlist"])
v1_router.include_router(app_settings_router, prefix="/settings", tags=["settings"])
v1_router.include_router(screener_router, prefix="/screener", tags=["screener"])
v1_router.include_router(backtest_router, prefix="/backtest", tags=["backtest"])
v1_router.include_router(portfolio_router, prefix="/portfolio", tags=["portfolio"])
v1_router.include_router(research_router, prefix="/research", tags=["research"])
v1_router.include_router(studio_router, prefix="/studio", tags=["studio"])
v1_router.include_router(ops_router, prefix="/ops", tags=["ops"])
v1_router.include_router(desk_router, prefix="/desk", tags=["desk"])
v1_router.include_router(export_router, prefix="/export", tags=["export"])
v1_router.include_router(monitor_router, prefix="/monitor", tags=["monitor"])
v1_router.include_router(report_router, prefix="/report", tags=["report"])
