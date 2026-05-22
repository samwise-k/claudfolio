"""Broker port: backend-agnostic dataclasses + Protocol.

Order placement is modeled as *submit-then-poll* even when the underlying
backend is synchronous. Real broker APIs (Schwab) confirm asynchronously, so
the port returns an ``OrderTicket`` whose ``status`` may be ``submitted`` or
``working`` and that callers reconcile via :meth:`Broker.poll` (or at boot
via :meth:`Broker.reconcile`). The simulated backend short-circuits to
``filled`` immediately, but uses the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

Side = Literal["buy", "sell", "sell_short", "buy_to_cover"]
OrderType = Literal["market", "limit"]
OrderStatus = Literal[
    "submitted", "working", "filled", "partial", "canceled", "rejected", "expired"
]
PositionDirection = Literal["long", "short"]
Backend = Literal["simulated", "schwab"]


TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {"filled", "canceled", "rejected", "expired"}
)


@dataclass(frozen=True)
class Quote:
    ticker: str
    bid: float
    ask: float
    last: float
    asof: datetime


@dataclass(frozen=True)
class BrokerPosition:
    ticker: str
    direction: PositionDirection
    shares: float
    avg_entry_price: float
    current_price: float


@dataclass(frozen=True)
class AccountSnapshot:
    cash: float
    equity: float
    buying_power: float


@dataclass(frozen=True)
class OrderTicket:
    client_order_id: str
    broker_order_id: str | None
    ticker: str
    side: Side
    qty_requested: float
    order_type: OrderType
    limit_price: float | None
    status: OrderStatus
    qty_filled: float
    avg_fill_price: float | None
    submitted_at: datetime
    filled_at: datetime | None
    error: str | None = None


@dataclass(frozen=True)
class ReconciliationEvent:
    kind: Literal["order_resolved", "position_drift", "no_change"]
    detail: dict[str, Any] = field(default_factory=dict)


class Broker(Protocol):
    """Backend-agnostic broker interface.

    Implementations may be sync (SimulatedBroker) or async-with-polling
    (SchwabBroker). Either way, ``submit`` returns an ``OrderTicket``;
    callers inspect ``ticket.status`` to decide whether to use the fill
    immediately or wait for ``poll``/``reconcile``.
    """

    backend: Backend

    def get_quote(self, ticker: str) -> Quote: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_account(self) -> AccountSnapshot: ...

    def submit(
        self,
        ticker: str,
        side: Side,
        qty: float,
        order_type: OrderType = "market",
        limit_price: float | None = None,
        client_order_id: str | None = None,
        reasoning: str = "",
    ) -> OrderTicket: ...

    def poll(self, client_order_id: str) -> OrderTicket: ...

    def cancel(self, client_order_id: str) -> OrderTicket: ...

    def reconcile(self) -> list[ReconciliationEvent]: ...
