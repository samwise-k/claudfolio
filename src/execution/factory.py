"""Broker factory — selects backend from EXECUTION_BACKEND env var.

Today only ``simulated`` is wired up; ``schwab`` will be added in a
subsequent change. Centralizing the selection here keeps the choice out of
the tool layer.
"""

from __future__ import annotations

import os
from datetime import date as Date
from typing import TYPE_CHECKING

from src.execution.broker import Broker
from src.execution.simulated import SimulatedBroker

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.storage.models import Portfolio


def build_broker(
    session: "Session",
    portfolio: "Portfolio",
    current_prices: dict[str, float],
    trade_date: Date,
    *,
    backend: str | None = None,
) -> Broker:
    backend = (backend or os.getenv("EXECUTION_BACKEND") or "simulated").lower()
    if backend == "simulated":
        return SimulatedBroker(session, portfolio, current_prices, trade_date)
    if backend == "schwab":
        raise NotImplementedError(
            "Schwab backend is not yet implemented. "
            "Set EXECUTION_BACKEND=simulated for now."
        )
    raise ValueError(f"Unknown EXECUTION_BACKEND: {backend!r}")
