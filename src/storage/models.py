"""SQLAlchemy declarative models. Per-phase schemas are filled in as engines land."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all SFE tables."""


class SentimentDaily(Base):
    """Per-ticker daily sentiment rollup (Phase 1).

    Mirrors the JSON schema in the planning doc. ``source_breakdown``,
    ``key_topics``, and ``notable_headlines`` are JSON columns so a row
    round-trips into the meta-layer payload without a join.
    """

    __tablename__ = "sentiment_daily"
    __table_args__ = (UniqueConstraint("ticker", "as_of", name="uq_sentiment_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    sentiment_score: Mapped[float] = mapped_column(Float)
    sentiment_direction: Mapped[str] = mapped_column(String(16))
    sentiment_delta_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    key_topics: Mapped[list] = mapped_column(JSON, default=list)
    notable_headlines: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QuantDaily(Base):
    """Per-ticker daily quantitative scorecard (Phase 2)."""

    __tablename__ = "quant_daily"
    __table_args__ = (UniqueConstraint("ticker", "as_of", name="uq_quant_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_14: Mapped[float | None] = mapped_column(Float, nullable=True)
    above_50sma: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    above_200sma: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    macd_signal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    volume_vs_20d_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector_etf: Mapped[str | None] = mapped_column(String(10), nullable=True)
    relative_return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_score: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EnrichmentDaily(Base):
    """Per-ticker daily enrichment signals (Phase 3)."""

    __tablename__ = "enrichment_daily"
    __table_args__ = (UniqueConstraint("ticker", "as_of", name="uq_enrichment_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    insider_trades: Mapped[dict] = mapped_column(JSON, default=dict)
    next_earnings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    upcoming_events: Mapped[list] = mapped_column(JSON, default=list)
    analyst_activity: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EarningsBriefOutcome(Base):
    """Tracks earnings brief predictions vs actual outcomes."""

    __tablename__ = "earnings_brief_outcome"
    __table_args__ = (
        UniqueConstraint("ticker", "earnings_date", name="uq_outcome_ticker_erdate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    brief_date: Mapped[date] = mapped_column(Date)
    earnings_date: Mapped[date] = mapped_column(Date, index=True)
    predicted_dir: Mapped[str] = mapped_column(String(16))
    conviction: Mapped[float] = mapped_column(Float)
    actual_eps_surp: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_rev_surp: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_move_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default="pending")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SignalDaily(Base):
    """Per-ticker daily directional signal for the experiment."""

    __tablename__ = "signal_daily"
    __table_args__ = (UniqueConstraint("ticker", "as_of", name="uq_signal_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    direction: Mapped[str] = mapped_column(String(16))
    conviction: Mapped[float] = mapped_column(Float)
    dominant_component: Mapped[str] = mapped_column(String(32))
    reasoning: Mapped[str] = mapped_column(Text)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_components: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Portfolio(Base):
    """Simulated portfolio state. One active portfolio at a time."""

    __tablename__ = "portfolio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, default="default")
    starting_equity: Mapped[float] = mapped_column(Float, default=100_000.0)
    cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    inception_date: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Position(Base):
    """Open position in the simulated portfolio."""

    __tablename__ = "position"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "ticker", name="uq_position_portfolio_ticker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(Integer, index=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # long / short
    shares: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Trade(Base):
    """Completed trade log for the simulated portfolio."""

    __tablename__ = "trade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(Integer, index=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    action: Mapped[str] = mapped_column(String(16))  # open / close / resize
    direction: Mapped[str] = mapped_column(String(8))
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentSession(Base):
    """Log of a single agent run — reasoning trace and decisions."""

    __tablename__ = "agent_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(Integer, index=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    decisions_made: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_trace: Mapped[dict] = mapped_column(JSON, default=dict)
    portfolio_snapshot_before: Mapped[dict] = mapped_column(JSON, default=dict)
    portfolio_snapshot_after: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BrokerOrder(Base):
    """Write-ahead log of broker orders for crash-safety and reconciliation.

    A row is inserted in ``submitted`` state *before* the broker call. The
    ``client_order_id`` is the idempotency key — on restart, any row not in
    a terminal status is resolved against the broker before the agent runs.
    """

    __tablename__ = "broker_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    portfolio_id: Mapped[int] = mapped_column(Integer, index=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    side: Mapped[str] = mapped_column(String(16))  # buy/sell/sell_short/buy_to_cover
    qty_requested: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(16))  # market/limit
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    qty_filled: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    backend: Mapped[str] = mapped_column(String(16))  # simulated/schwab
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BriefingDaily(Base):
    """Cached meta-layer briefing output (Phase 5).

    One row per briefing date. ``payload`` stores the exact JSON sent to
    Claude so a briefing can be regenerated or inspected later without
    re-querying the engine tables.
    """

    __tablename__ = "briefing_daily"
    __table_args__ = (UniqueConstraint("as_of", name="uq_briefing_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    tickers: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    briefing_markdown: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
