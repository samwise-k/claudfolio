"""Tests for the broker execution port + SimulatedBroker."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.execution.factory import build_broker
from src.execution.simulated import SimulatedBroker
from src.storage.models import Base, BrokerOrder
from src.storage.portfolio_repo import get_or_create_portfolio, get_position


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


@pytest.fixture
def broker(session, portfolio):
    return SimulatedBroker(
        session=session,
        portfolio=portfolio,
        current_prices={"NVDA": 950.0, "AAPL": 200.0, "TSLA": 300.0},
        trade_date=date(2026, 5, 1),
    )


class TestSimulatedBrokerOpen:
    def test_buy_opens_long(self, session, portfolio, broker):
        ticket = broker.submit("NVDA", "buy", qty=10, reasoning="test")
        assert ticket.status == "filled"
        assert ticket.qty_filled == 10
        assert ticket.avg_fill_price == 950.0

        pos = get_position(session, portfolio.id, "NVDA")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.shares == 10
        assert portfolio.cash == 100_000.0 - 10 * 950.0

    def test_sell_short_opens_short(self, session, portfolio, broker):
        ticket = broker.submit("TSLA", "sell_short", qty=5, reasoning="bearish")
        assert ticket.status == "filled"

        pos = get_position(session, portfolio.id, "TSLA")
        assert pos.direction == "short"
        assert pos.shares == 5
        assert portfolio.cash == 100_000.0 + 5 * 300.0

    def test_buy_adds_to_existing_long(self, session, portfolio, broker):
        broker.submit("NVDA", "buy", qty=10)
        broker.submit("NVDA", "buy", qty=5)

        pos = get_position(session, portfolio.id, "NVDA")
        assert pos.shares == 15

    def test_sell_short_while_long_rejects(self, broker):
        broker.submit("NVDA", "buy", qty=10)
        ticket = broker.submit("NVDA", "sell_short", qty=5)
        assert ticket.status == "rejected"
        assert "already long" in (ticket.error or "")


class TestSimulatedBrokerClose:
    def test_sell_closes_long(self, session, portfolio, broker):
        broker.submit("AAPL", "buy", qty=20)
        cash_after_open = portfolio.cash

        ticket = broker.submit("AAPL", "sell", qty=20)
        assert ticket.status == "filled"

        assert get_position(session, portfolio.id, "AAPL") is None
        assert portfolio.cash == cash_after_open + 20 * 200.0

    def test_sell_partial_resizes_long(self, session, portfolio, broker):
        broker.submit("AAPL", "buy", qty=20)
        broker.submit("AAPL", "sell", qty=5)

        pos = get_position(session, portfolio.id, "AAPL")
        assert pos.shares == 15

    def test_sell_more_than_held_rejects(self, broker):
        broker.submit("AAPL", "buy", qty=10)
        ticket = broker.submit("AAPL", "sell", qty=15)
        assert ticket.status == "rejected"

    def test_buy_to_cover_closes_short(self, session, portfolio, broker):
        broker.submit("TSLA", "sell_short", qty=5)
        ticket = broker.submit("TSLA", "buy_to_cover", qty=5)
        assert ticket.status == "filled"
        assert get_position(session, portfolio.id, "TSLA") is None


class TestSimulatedBrokerReads:
    def test_get_quote(self, broker):
        q = broker.get_quote("NVDA")
        assert q.last == 950.0
        assert q.ticker == "NVDA"

    def test_get_quote_unknown_raises(self, broker):
        with pytest.raises(ValueError):
            broker.get_quote("UNKNOWN")

    def test_get_positions_reflects_holdings(self, broker):
        broker.submit("NVDA", "buy", qty=10)
        broker.submit("AAPL", "buy", qty=20)

        positions = broker.get_positions()
        tickers = {p.ticker for p in positions}
        assert tickers == {"NVDA", "AAPL"}

    def test_get_account_returns_snapshot(self, broker):
        snap = broker.get_account()
        assert snap.cash == 100_000.0
        assert snap.equity == 100_000.0


class TestWriteAheadLog:
    def test_filled_order_persists_with_status_filled(self, session, broker):
        ticket = broker.submit("NVDA", "buy", qty=10, reasoning="test")
        row = session.execute(
            select(BrokerOrder).where(BrokerOrder.client_order_id == ticket.client_order_id)
        ).scalar_one()
        assert row.status == "filled"
        assert row.qty_filled == 10
        assert row.avg_fill_price == 950.0
        assert row.backend == "simulated"
        assert row.reasoning == "test"

    def test_rejected_order_persists_with_error(self, session, broker):
        broker.submit("NVDA", "buy", qty=10)
        ticket = broker.submit("NVDA", "sell_short", qty=5)
        row = session.execute(
            select(BrokerOrder).where(BrokerOrder.client_order_id == ticket.client_order_id)
        ).scalar_one()
        assert row.status == "rejected"
        assert row.error is not None

    def test_client_order_id_is_unique(self, session, broker):
        t1 = broker.submit("NVDA", "buy", qty=1)
        t2 = broker.submit("AAPL", "buy", qty=1)
        assert t1.client_order_id != t2.client_order_id

    def test_poll_returns_terminal_ticket(self, broker):
        ticket = broker.submit("NVDA", "buy", qty=10)
        polled = broker.poll(ticket.client_order_id)
        assert polled.status == "filled"
        assert polled.client_order_id == ticket.client_order_id


class TestFactory:
    def test_simulated_is_default(self, session, portfolio, monkeypatch):
        monkeypatch.delenv("EXECUTION_BACKEND", raising=False)
        b = build_broker(
            session=session, portfolio=portfolio,
            current_prices={}, trade_date=date(2026, 5, 1),
        )
        assert b.backend == "simulated"

    def test_explicit_simulated(self, session, portfolio):
        b = build_broker(
            session=session, portfolio=portfolio,
            current_prices={}, trade_date=date(2026, 5, 1),
            backend="simulated",
        )
        assert isinstance(b, SimulatedBroker)

    def test_schwab_not_implemented(self, session, portfolio):
        with pytest.raises(NotImplementedError):
            build_broker(
                session=session, portfolio=portfolio,
                current_prices={}, trade_date=date(2026, 5, 1),
                backend="schwab",
            )

    def test_unknown_backend_raises(self, session, portfolio):
        with pytest.raises(ValueError):
            build_broker(
                session=session, portfolio=portfolio,
                current_prices={}, trade_date=date(2026, 5, 1),
                backend="bogus",
            )
