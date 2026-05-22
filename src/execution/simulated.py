"""SimulatedBroker: in-process backend backed by the SQLite portfolio tables.

Fills happen synchronously at the price provided to the constructor (the same
``current_prices`` snapshot the tool layer used before this refactor). Every
order writes a ``BrokerOrder`` row *before* mutating the position, so the
write-ahead invariant holds even though the simulated path can't actually
crash mid-flight.
"""

from __future__ import annotations

import uuid
from datetime import date as Date
from datetime import datetime
from typing import TYPE_CHECKING

from src.execution.broker import (
    AccountSnapshot,
    Backend,
    BrokerPosition,
    OrderTicket,
    OrderType,
    Quote,
    ReconciliationEvent,
    Side,
)
from src.storage.models import BrokerOrder, Portfolio
from src.storage.portfolio_repo import (
    close_position as repo_close_position,
)
from src.storage.portfolio_repo import (
    get_position,
    get_positions,
    portfolio_snapshot,
)
from src.storage.portfolio_repo import (
    open_position as repo_open_position,
)
from src.storage.portfolio_repo import (
    resize_position as repo_resize_position,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class SimulatedBroker:
    """In-memory broker that mutates the local portfolio tables directly.

    The broker accepts side+qty primitives. Translation from the agent's
    open/close/resize vocabulary happens one layer up (``src/agent/tools.py``).
    """

    backend: Backend = "simulated"

    def __init__(
        self,
        session: "Session",
        portfolio: Portfolio,
        current_prices: dict[str, float],
        trade_date: Date,
    ) -> None:
        self.session = session
        self.portfolio = portfolio
        self.current_prices = current_prices
        self.trade_date = trade_date

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get_quote(self, ticker: str) -> Quote:
        ticker = ticker.upper()
        last = self.current_prices.get(ticker)
        if last is None:
            raise ValueError(f"no simulated quote for {ticker}")
        now = datetime.now()
        return Quote(ticker=ticker, bid=last, ask=last, last=last, asof=now)

    def get_positions(self) -> list[BrokerPosition]:
        out: list[BrokerPosition] = []
        for pos in get_positions(self.session, self.portfolio.id):
            cur = self.current_prices.get(
                pos.ticker, pos.current_price or pos.entry_price
            )
            out.append(
                BrokerPosition(
                    ticker=pos.ticker,
                    direction=pos.direction,  # type: ignore[arg-type]
                    shares=pos.shares,
                    avg_entry_price=pos.entry_price,
                    current_price=cur,
                )
            )
        return out

    def get_account(self) -> AccountSnapshot:
        snap = portfolio_snapshot(self.session, self.portfolio, self.current_prices)
        return AccountSnapshot(
            cash=snap["cash"],
            equity=snap["equity"],
            buying_power=snap["cash"],  # no margin in the sim
        )

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def submit(
        self,
        ticker: str,
        side: Side,
        qty: float,
        order_type: OrderType = "market",
        limit_price: float | None = None,
        client_order_id: str | None = None,
        reasoning: str = "",
    ) -> OrderTicket:
        ticker = ticker.upper()
        client_order_id = client_order_id or str(uuid.uuid4())

        order = BrokerOrder(
            client_order_id=client_order_id,
            portfolio_id=self.portfolio.id,
            ticker=ticker,
            side=side,
            qty_requested=qty,
            order_type=order_type,
            limit_price=limit_price,
            status="submitted",
            reasoning=reasoning,
            backend=self.backend,
        )
        self.session.add(order)
        self.session.commit()

        price = self.current_prices.get(ticker)
        if price is None:
            return self._reject(order, f"no simulated price for {ticker}")

        if order_type == "limit" and limit_price is not None:
            # In the sim we still fill at the market quote; limit-price gating
            # would require a clearer simulation model than we need here.
            pass

        try:
            self._apply_fill(ticker, side, qty, price, reasoning)
        except ValueError as exc:
            return self._reject(order, str(exc))

        now = datetime.now()
        order.status = "filled"
        order.qty_filled = qty
        order.avg_fill_price = price
        order.filled_at = now
        self.session.commit()

        return OrderTicket(
            client_order_id=client_order_id,
            broker_order_id=None,
            ticker=ticker,
            side=side,
            qty_requested=qty,
            order_type=order_type,
            limit_price=limit_price,
            status="filled",
            qty_filled=qty,
            avg_fill_price=price,
            submitted_at=order.submitted_at,
            filled_at=now,
            error=None,
        )

    def poll(self, client_order_id: str) -> OrderTicket:
        order = self._find_order(client_order_id)
        return _ticket_from_row(order)

    def cancel(self, client_order_id: str) -> OrderTicket:
        order = self._find_order(client_order_id)
        if order.status in {"filled", "rejected", "expired", "canceled"}:
            return _ticket_from_row(order)
        order.status = "canceled"
        self.session.commit()
        return _ticket_from_row(order)

    def reconcile(self) -> list[ReconciliationEvent]:
        # Simulated orders are always terminal at the end of submit(); nothing
        # to reconcile. Returning [] keeps the interface uniform with Schwab.
        return []

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_fill(
        self, ticker: str, side: Side, qty: float, price: float, reasoning: str
    ) -> None:
        existing = get_position(self.session, self.portfolio.id, ticker)

        if side == "buy":
            if existing is None:
                repo_open_position(
                    self.session, self.portfolio, ticker, "long",
                    qty, price, self.trade_date, reasoning,
                )
            elif existing.direction == "long":
                repo_resize_position(
                    self.session, self.portfolio, existing,
                    existing.shares + qty, price, self.trade_date, reasoning,
                )
            else:  # covering a short
                new_shares = existing.shares - qty
                if new_shares < -1e-9:
                    raise ValueError("buy qty exceeds short position size")
                if abs(new_shares) < 1e-9:
                    repo_close_position(
                        self.session, self.portfolio, existing, price,
                        self.trade_date, reasoning,
                    )
                else:
                    repo_resize_position(
                        self.session, self.portfolio, existing, new_shares,
                        price, self.trade_date, reasoning,
                    )

        elif side == "sell":
            if existing is None or existing.direction != "long":
                raise ValueError(f"no long position in {ticker} to sell")
            new_shares = existing.shares - qty
            if new_shares < -1e-9:
                raise ValueError("sell qty exceeds long position size")
            if abs(new_shares) < 1e-9:
                repo_close_position(
                    self.session, self.portfolio, existing, price,
                    self.trade_date, reasoning,
                )
            else:
                repo_resize_position(
                    self.session, self.portfolio, existing, new_shares,
                    price, self.trade_date, reasoning,
                )

        elif side == "sell_short":
            if existing is None:
                repo_open_position(
                    self.session, self.portfolio, ticker, "short",
                    qty, price, self.trade_date, reasoning,
                )
            elif existing.direction == "short":
                repo_resize_position(
                    self.session, self.portfolio, existing,
                    existing.shares + qty, price, self.trade_date, reasoning,
                )
            else:
                raise ValueError(
                    f"cannot sell_short {ticker}: already long. close first."
                )

        elif side == "buy_to_cover":
            if existing is None or existing.direction != "short":
                raise ValueError(f"no short position in {ticker} to cover")
            new_shares = existing.shares - qty
            if new_shares < -1e-9:
                raise ValueError("buy_to_cover qty exceeds short position size")
            if abs(new_shares) < 1e-9:
                repo_close_position(
                    self.session, self.portfolio, existing, price,
                    self.trade_date, reasoning,
                )
            else:
                repo_resize_position(
                    self.session, self.portfolio, existing, new_shares,
                    price, self.trade_date, reasoning,
                )

        else:
            raise ValueError(f"unknown side: {side}")

    def _reject(self, order: BrokerOrder, reason: str) -> OrderTicket:
        order.status = "rejected"
        order.error = reason
        self.session.commit()
        return _ticket_from_row(order)

    def _find_order(self, client_order_id: str) -> BrokerOrder:
        from sqlalchemy import select

        row = self.session.execute(
            select(BrokerOrder).where(BrokerOrder.client_order_id == client_order_id)
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"no broker_order with client_order_id={client_order_id}")
        return row


def _ticket_from_row(row: BrokerOrder) -> OrderTicket:
    return OrderTicket(
        client_order_id=row.client_order_id,
        broker_order_id=row.broker_order_id,
        ticker=row.ticker,
        side=row.side,  # type: ignore[arg-type]
        qty_requested=row.qty_requested,
        order_type=row.order_type,  # type: ignore[arg-type]
        limit_price=row.limit_price,
        status=row.status,  # type: ignore[arg-type]
        qty_filled=row.qty_filled,
        avg_fill_price=row.avg_fill_price,
        submitted_at=row.submitted_at,
        filled_at=row.filled_at,
        error=row.error,
    )
