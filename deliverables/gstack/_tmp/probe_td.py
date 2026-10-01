import sys; sys.path.insert(0,".")
from app.data.parquet_store import today_trade_date_or_last
import datetime as dt
print("date.today() =", dt.date.today())
print("today_trade_date_or_last() =", today_trade_date_or_last())
from app.core.config import get_settings
s=get_settings()
print("AQP_OFFLINE/exchange calendar?")
