"""Tests for the portfolio tool implementations exposed via MCP."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.storage.models import Base, Portfolio, Position, Trade
from src.storage.portfolio_repo import (
    close_position,
    get_or_create_portfolio,
    get_position,
    get_positions,
    get_trades,
    open_position,
    portfolio_snapshot,
    resize_position,
)
from src.agent.tools import TOOL_SCHEMAS, ToolContext, execute_tool


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess = factory()
    yield sess
    sess.close()


@pytest.fixture
def portfolio(session):
    return get_or_create_portfolio(
        session, name="test", starting_equity=100_000.0,
        inception_date=date(2026, 5, 1),
    )


class TestPortfolioRepo:
    def test_create_portfolio(self, session):
        p = get_or_create_portfolio(session, name="test", starting_equity=50_000.0)
        assert p.cash == 50_000.0
        assert p.active is True

    def test_get_existing_portfolio(self, session, portfolio):
        p2 = get_or_create_portfolio(session, name="test")
        assert p2.id == portfolio.id

    def test_open_position(self, session, portfolio):
        pos = open_position(
            session, portfolio, "NVDA", "long", 10, 950.0,
            date(2026, 5, 1), "test reasoning",
        )
        assert pos.ticker == "NVDA"
        assert pos.shares == 10
        assert portfolio.cash == 100_000 - (10 * 950)

    def test_close_position(self, session, portfolio):
        pos = open_position(
            session, portfolio, "AAPL", "long", 20, 200.0,
            date(2026, 5, 1), "opening",
        )
        cash_after_open = portfolio.cash

        trade = close_position(
            session, portfolio, pos, 210.0, date(2026, 5, 2), "closing",
        )
        assert trade.action == "close"
        assert portfolio.cash == cash_after_open + (20 * 210)

        assert get_position(session, portfolio.id, "AAPL") is None

    def test_resize_position(self, session, portfolio):
        pos = open_position(
            session, portfolio, "MSFT", "long", 10, 400.0,
            date(2026, 5, 1), "opening",
        )
        cash_after_open = portfolio.cash

        trade = resize_position(
            session, portfolio, pos, 15, 410.0, date(2026, 5, 2), "adding",
        )
        assert trade.action == "resize"
        assert trade.shares == 5
        assert pos.shares == 15
        assert portfolio.cash == cash_after_open - (5 * 410)

    def test_portfolio_snapshot(self, session, portfolio):
        open_position(
            session, portfolio, "GOOG", "long", 5, 170.0,
            date(2026, 5, 1), "test",
        )
        snap = portfolio_snapshot(session, portfolio, {"GOOG": 180.0})
        assert snap["position_count"] == 1
        assert snap["positions"][0]["unrealized_pnl"] == 50.0
        assert snap["equity"] == portfolio.cash + (5 * 180)

    def test_trade_history(self, session, portfolio):
        pos = open_position(
            session, portfolio, "META", "long", 8, 500.0,
            date(2026, 5, 1), "open",
        )
        close_position(
            session, portfolio, pos, 520.0, date(2026, 5, 2), "close",
        )
        trades = get_trades(session, portfolio.id)
        assert len(trades) == 2
        actions = {t.action for t in trades}
        assert actions == {"open", "close"}


class TestToolSchemas:
    def test_all_tools_have_schemas(self):
        names = {t["name"] for t in TOOL_SCHEMAS}
        expected = {
            "get_portfolio_state", "get_signals", "get_ticker_detail",
            "open_position", "close_position", "resize_position",
            "get_trade_history",
            "investigate_sentiment", "get_quant_detail", "get_enrichment_detail",
        }
        assert names == expected

    def test_schemas_have_required_fields(self):
        for schema in TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"


class TestToolExecution:
    @pytest.fixture
    def ctx(self, session, portfolio):
        signals = {
            "as_of": "2026-05-01",
            "tickers": [
                {
                    "ticker": "NVDA",
                    "sentiment": {"score": 0.72, "direction": "improving"},
                    "quant": {"close": 950.0, "health_score": "strong"},
                    "enrichment": {"insider_trades": {"net_insider_sentiment": "bullish"}},
                },
            ],
        }
        return ToolContext(
            session=session,
            portfolio=portfolio,
            signals_payload=signals,
            current_prices={"NVDA": 950.0},
            trade_date=date(2026, 5, 1),
        )

    def test_get_portfolio_state(self, ctx):
        import json
        result = json.loads(execute_tool(ctx, "get_portfolio_state", {}))
        assert result["cash"] == 100_000.0
        assert result["position_count"] == 0

    def test_get_signals(self, ctx):
        import json
        result = json.loads(execute_tool(ctx, "get_signals", {}))
        assert len(result["tickers"]) == 1
        assert result["tickers"][0]["ticker"] == "NVDA"

    def test_open_and_close_position(self, ctx):
        import json

        result = json.loads(execute_tool(ctx, "open_position", {
            "ticker": "NVDA",
            "direction": "long",
            "allocation_pct": 5.0,
            "reasoning": "test",
        }))
        assert result["status"] == "opened"

        result = json.loads(execute_tool(ctx, "close_position", {
            "ticker": "NVDA",
            "reasoning": "taking profits",
        }))
        assert result["status"] == "closed"

    def test_cannot_open_duplicate(self, ctx):
        import json

        execute_tool(ctx, "open_position", {
            "ticker": "NVDA", "direction": "long",
            "allocation_pct": 5.0, "reasoning": "test",
        })
        result = json.loads(execute_tool(ctx, "open_position", {
            "ticker": "NVDA", "direction": "long",
            "allocation_pct": 5.0, "reasoning": "again",
        }))
        assert "error" in result

    def test_unknown_tool(self, ctx):
        import json
        result = json.loads(execute_tool(ctx, "nonexistent", {}))
        assert "error" in result

    def test_get_ticker_detail(self, ctx):
        import json
        result = json.loads(execute_tool(ctx, "get_ticker_detail", {"ticker": "NVDA"}))
        assert result["ticker"] == "NVDA"
        assert result["signals"] is not None

    def test_get_trade_history_empty(self, ctx):
        import json
        result = json.loads(execute_tool(ctx, "get_trade_history", {}))
        assert result["trades"] == []
