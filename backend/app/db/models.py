"""
所有核心表的 ORM 定义（AQP，共 8 张）。

- 用 datetime.datetime 存 DATETIME（SQLite 兼容）；
- 索引根据查询路径建立，保持够用、不夸张。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    PrimaryKeyConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..domain.trading_rules import COMMISSION_RATE_DEFAULT, STAMP_DUTY_STOCK_RATE
from .session import Base


# ---------- 基础表 ----------
class Instrument(Base):
    """证券基础信息表（A 股股票 / ETF / 指数）。"""

    __tablename__ = "instrument"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, comment="交易所代码，如 600519.SH")
    code: Mapped[str] = mapped_column(String(16), nullable=False, comment="纯数字代码 600519")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="贵州茅台")
    market: Mapped[str] = mapped_column(String(16), nullable=False, comment="SH/SZ/BJ 等")
    instrument_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="stock", comment="stock/etf/index"
    )
    list_date: Mapped[date | None] = mapped_column(Date, default=None, comment="上市日期")
    delist_date: Mapped[date | None] = mapped_column(Date, default=None)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    is_halted: Mapped[bool] = mapped_column(Boolean, default=False, comment="今日是否停牌")
    industry: Mapped[str | None] = mapped_column(String(64), default=None)
    area: Mapped[str | None] = mapped_column(String(32), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("symbol", name="uq_instrument_symbol"),
        Index("ix_instrument_code", "code"),
        Index("ix_instrument_industry", "industry"),
    )


class TradeCalendar(Base):
    """A 股交易日历（包含各交易所单独判断字段）。"""

    __tablename__ = "trade_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    is_sh: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sz: Mapped[bool] = mapped_column(Boolean, default=True)
    is_bj: Mapped[bool] = mapped_column(Boolean, default=True)
    week: Mapped[int] = mapped_column(Integer, comment="星期 1-7")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Watchlist(Base):
    """自选股。"""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("instrument.symbol"), nullable=False
    )
    tags: Mapped[str | None] = mapped_column(String(256), default=None, comment="逗号分隔 tag")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    )


class PriceAlert(Base):
    """价位提醒。"""

    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False, comment="up/down")
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(256), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class BacktestRun(Base):
    """回测运行记录。"""

    __tablename__ = "backtest"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, default="lgbm_topk")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    init_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=1_000_000)
    top_k: Mapped[int] = mapped_column(Integer, default=10)
    rebalance_freq: Mapped[str] = mapped_column(String(16), default="daily")
    commission_rate: Mapped[float] = mapped_column(Float, default=COMMISSION_RATE_DEFAULT)
    stamp_duty: Mapped[float] = mapped_column(Float, default=STAMP_DUTY_STOCK_RATE)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", comment="pending/running/success/failed"
    )
    summary_json: Mapped[str | None] = mapped_column(Text, default=None, comment="绩效指标 JSON")
    error_msg: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class ModelRegistry(Base):
    """模型注册表，每次训练产生一个版本（第三阶段：生产模型治理）。

    生命周期： candidate --promote--> production --demote--> archived
    约束：同一 model_name 下**最多一个** production（由 ml.registry.promote_model 保证）。
    """

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, default="lgbm_v1")
    version: Mapped[str] = mapped_column(String(64), nullable=False, comment="如 20260828_01")
    feature_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="alpha_basic_v1"
    )
    train_start: Mapped[date] = mapped_column(Date)
    train_end: Mapped[date] = mapped_column(Date)
    valid_ic: Mapped[float | None] = mapped_column(Float, default=None)
    valid_rank_ic: Mapped[float | None] = mapped_column(Float, default=None)
    model_path: Mapped[str] = mapped_column(String(512), nullable=False)
    params_json: Mapped[str | None] = mapped_column(Text, default=None)
    is_production: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ---- 第五阶段：完整血缘与评估记录（由 migrations 增量补列）----
    status: Mapped[str | None] = mapped_column(
        String(16), default="candidate", comment="candidate/production/archived")
    dataset_version: Mapped[str | None] = mapped_column(String(64), default=None)
    valid_start: Mapped[date | None] = mapped_column(Date, default=None)
    valid_end: Mapped[date | None] = mapped_column(Date, default=None)
    test_start: Mapped[date | None] = mapped_column(Date, default=None)
    test_end: Mapped[date | None] = mapped_column(Date, default=None)
    valid_icir: Mapped[float | None] = mapped_column(Float, default=None)
    valid_rmse: Mapped[float | None] = mapped_column(Float, default=None)
    test_ic: Mapped[float | None] = mapped_column(Float, default=None)
    test_rank_ic: Mapped[float | None] = mapped_column(Float, default=None)
    test_icir: Mapped[float | None] = mapped_column(Float, default=None)
    test_rmse: Mapped[float | None] = mapped_column(Float, default=None)
    label_quality_json: Mapped[str | None] = mapped_column(Text, default=None)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    promoted_by: Mapped[str | None] = mapped_column(String(64), default=None)
    promote_reason: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_model_name_version"),
        Index("ix_model_registry_prod_status", "is_production", "status"),
    )


class DataUpdateLog(Base):
    """Parquet 数据更新日志，可审计 / 断点续跑。"""

    __tablename__ = "data_update_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="daily_bar/adj_factor/..."
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(16), default="success", comment="success/failed/partial"
    )
    source: Mapped[str] = mapped_column(String(16), default="akshare")
    message: Mapped[str | None] = mapped_column(Text, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_update_log_dataset_date", "dataset", "trade_date"),
    )


class NewsAnnouncement(Base):
    """公告 / 新闻摘要（AKShare 抓取后入库，供前端展示）。"""

    __tablename__ = "news_announcement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(String(16), default=None, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(
        String(32), default="announcement", comment="announcement/news"
    )
    pub_date: Mapped[date] = mapped_column(Date, index=True)
    url: Mapped[str | None] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- P1 新增表 ----------
class DataJob(Base):
    """每日流水线作业记录（P1-1）：job_type + trade_date 幂等。"""

    __tablename__ = "data_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, default="daily_pipeline")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING",
        comment="PENDING/RUNNING/SUCCESS/FAILED",
    )
    current_step: Mapped[str | None] = mapped_column(String(32), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    traceback: Mapped[str | None] = mapped_column(Text, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("job_type", "trade_date", name="uq_datajob_type_date"),
        Index("ix_datajob_status_finished", "status", "finished_at"),
    )


class FeatureRun(Base):
    """选股/特征运行记录（P1-5）：每次正式选股一条。"""

    __tablename__ = "feature_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="alpha_basic_v1")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64), default=None)
    feature_version: Mapped[str | None] = mapped_column(String(64), default=None)
    top_k: Mapped[int] = mapped_column(Integer, default=50)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DividendSplit(Base):
    """分红送配元数据（P1-4）：与 dividend_split Parquet 对应。"""

    __tablename__ = "dividend_split"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    announce_date: Mapped[date | None] = mapped_column(Date, default=None)
    record_date: Mapped[date | None] = mapped_column(Date, default=None)
    ex_date: Mapped[date | None] = mapped_column(Date, index=True)
    dividend: Mapped[float | None] = mapped_column(Float, default=None, comment="每股派息(税前,元)")
    bonus_share: Mapped[float | None] = mapped_column(Float, default=None, comment="每股送股")
    split_ratio: Mapped[float | None] = mapped_column(Float, default=None, comment="转增比例")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="akshare")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FinancialReport(Base):
    """财务报表元数据（P1-4 最小集）：announce_date 与 period 严格区分（PIT 红线）。"""

    __tablename__ = "financial_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    period: Mapped[date] = mapped_column(Date, nullable=False, comment="报告期（季度末）")
    announce_date: Mapped[date] = mapped_column(Date, nullable=False, comment="披露可见日期")
    is_proxy_announce: Mapped[bool] = mapped_column(Boolean, default=False, comment="报告期+45日代理披露")
    announce_basis: Mapped[str | None] = mapped_column(
        String(16), default=None,
        comment="announce_date 来源：actual=巨潮实际披露 / scheduled=首次预约")
    revenue: Mapped[float | None] = mapped_column(Float, default=None)
    net_profit: Mapped[float | None] = mapped_column(Float, default=None)
    total_assets: Mapped[float | None] = mapped_column(Float, default=None)
    total_liability: Mapped[float | None] = mapped_column(Float, default=None)
    roe: Mapped[float | None] = mapped_column(Float, default=None)
    roa: Mapped[float | None] = mapped_column(Float, default=None)
    eps: Mapped[float | None] = mapped_column(Float, default=None)
    gross_margin: Mapped[float | None] = mapped_column(Float, default=None, comment="销售毛利率 %")
    bps: Mapped[float | None] = mapped_column(Float, default=None, comment="每股净资产(元)")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="akshare")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "period", "announce_date", name="uq_fin_symbol_period_ann"),
    )


class UserSetting(Base):
    """用户系统设置（单行 JSON 存储：偏好 / 引擎参数 / API Keys / 连接测试结果）。"""

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ---------- 生产端：纸面交易 / 风控 / 合规（P4 执行中心） ----------
class PaperOrder(Base):
    """模拟盘母单（算法订单：market / vwap / twap / pov）。

    真实撮合口径：
    - decision_price = 下单时点最新收盘价（回测理想价），实际成交 = 执行日
      开盘价 ± sqrt 冲击滑点，二者之差即实盘-回测基差；
    - 子单按算法分批（vwap/twap/pov 各 N 日），逐日撮合，参与率受限。
    """

    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)          # buy/sell
    algo: Mapped[str] = mapped_column(String(8), nullable=False, default="market")
    order_amount: Mapped[float] = mapped_column(Float, nullable=False)
    filled_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    split_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    participation_cap: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    decision_price: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING",
                                        comment="PENDING/PART_FILLED/FILLED/CANCELLED/REJECTED")
    reject_reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_paper_order_status_created", "status", "created_at"),
    )


class PaperFill(Base):
    """模拟盘子单成交（真实日行情撮合，含佣金/印花税/sqrt 冲击）。"""

    __tablename__ = "paper_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    exec_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    impact_bps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    participation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    basis_bps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0,
                                             comment="成交价 vs 决策价 基差（bps）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ExclusionItem(Base):
    """合规禁买池（ST / 退市风险 / 流动性差 / 机构合规限制）。"""

    __tablename__ = "exclusion_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="st | delist_risk | illiquid | manual_blacklist | manual_whitelist")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "category", name="uq_exclusion_symbol_cat"),
    )


class AppState(Base):
    """全局键值状态（kill_switch 等风控闸门）。"""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AlertRule(Base):
    """预警规则（§4.1 预警中心，Sprint2）。

    rule_type 六类：price_pct / price_cross / volume_spike / score_topk /
    factor_quantile / data_health；触发参数存 params_json（按类型白名单校验）。
    """

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="symbol",
        comment="symbol | watchlist | global")
    symbol: Mapped[str | None] = mapped_column(String(16), default=None)
    params_json: Mapped[str] = mapped_column(
        Text, nullable=False, comment='如 {"threshold":0.03,"window":20,"k":3.0}')
    channels_json: Mapped[str] = mapped_column(Text, nullable=False, default='["sse"]')
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AlertEvent(Base):
    """预警触发历史（站内信落库；is_read 供未读角标与批量已读）。"""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(16), default=None)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_alert_event_is_read", "is_read"),
    )


class ScreenerSnapshot(Base):
    """选股快照行表（L2-1，Sprint3）：盘后流水线按 board 物化 top-200。

    与路线图 DDL 对齐（date/strategy/board/rank 复合主键），另扩展富化列
    （close/pct/turnover/amount/limit_pct/risk）：写入时一次算好，读取路径
    单次 SELECT 即可组装响应（<200ms 验收的关键，避免逐只回查日线）。
    """

    __tablename__ = "screener_snapshot"

    date: Mapped[str] = mapped_column(String(10), nullable=False, comment="YYYY-MM-DD")
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    board: Mapped[str] = mapped_column(String(16), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), default=None)
    industry: Mapped[str | None] = mapped_column(String(64), default=None)
    pred_score: Mapped[float | None] = mapped_column(Float, default=None)
    model_version: Mapped[str | None] = mapped_column(String(64), default=None)
    close: Mapped[float | None] = mapped_column(Float, default=None)
    pct: Mapped[float | None] = mapped_column(Float, default=None)
    turnover: Mapped[float | None] = mapped_column(Float, default=None)
    amount: Mapped[float | None] = mapped_column(Float, default=None)
    limit_pct: Mapped[float | None] = mapped_column(Float, default=None)
    # [AQP 字段改名历史债务] 物理列名 risk 保留历史命名（存量 SQLite 快照表列），
    # 实际存储的是 strong/neutral/weak 三档信号强度（非投资风险）。
    # 对外契约名已统一为 signal_strength；后端在读路径（load_screener_snapshot）将 risk 列
    # 重新映射为 signal_strength 返回。重命名物理列需走 SQLite migration，成本较高，暂缓。
    risk: Mapped[str | None] = mapped_column(String(8), default=None)

    __table_args__ = (
        PrimaryKeyConstraint("date", "strategy", "board", "rank",
                             name="pk_screener_snapshot"),
    )


class ScreenerSnapshotStats(Base):
    """选股快照聚合表（L2-1）：pool_size/total 按路线图 DDL；stats_json 存
    完整 _stats 输出（胜率/行业分布等），供前端概览卡直接消费。"""

    __tablename__ = "screener_snapshot_stats"

    date: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    board: Mapped[str] = mapped_column(String(16), nullable=False)
    pool_size: Mapped[int | None] = mapped_column(Integer, default=None)
    total: Mapped[int | None] = mapped_column(Integer, default=None)
    trade_date: Mapped[str | None] = mapped_column(String(10), default=None)
    stats_json: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        PrimaryKeyConstraint("date", "strategy", "board",
                             name="pk_screener_snapshot_stats"),
    )


class CustomFactor(Base):
    """自定义因子库（§4.3，Sprint4）：表达式经白名单校验 + 真实截面评估后入库。

    metrics_json 存入库时的评估摘要（mean_ic/icir/n_days），供因子库列表
    直接展示；enabled=False 表示下线（保留表达式供复用，评估/挖掘不再纳入）。
    """

    __tablename__ = "custom_factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    expression: Mapped[str] = mapped_column(String(500), nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    metrics_json: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("name", name="uq_custom_factor_name"),
    )
