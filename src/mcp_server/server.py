"""MCP server (stdio) exposing SFE engines, portfolio actions, and tracking queries.

Primary interface for driving the Signal Fusion Engine from an external agent.
Procedural playbooks (weekly review, event-driven review) live as Claude Code
skills in `.claude/skills/`; this server provides the underlying capabilities.

Tool groups:
  - Engines:   sentiment_aggregate, quant_aggregate, enrichment_aggregate,
               earnings_calendar, run_signals
  - Portfolio: the 10 tools defined in src/agent/tools.py (schemas reused verbatim)
  - Tracking:  score_signals, get_ticker_summary, list_recent_signals,
               get_latest_outcome
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date as Date
from typing import Any, Iterator

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.agent.tools import TOOL_SCHEMAS as PORTFOLIO_TOOL_SCHEMAS
from src.agent.tools import ToolContext, execute_tool
from src.bootstrap import load_env
from src.storage.db import get_session
from src.storage.portfolio_repo import get_or_create_portfolio, portfolio_snapshot

load_env()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


@contextmanager
def _session_scope() -> Iterator[Session]:
    """Open a fresh DB session per tool call. Commit is the tool's responsibility."""
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def _parse_date(value: str | None, *, default_today: bool = False) -> Date:
    if value is None:
        if default_today:
            return Date.today()
        raise ValueError("date argument is required")
    return Date.fromisoformat(value)


def _json(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, default=str, indent=2))]


# ---------------------------------------------------------------------------
# Engine tools
# ---------------------------------------------------------------------------


def _engine_schemas() -> list[Tool]:
    return [
        Tool(
            name="sentiment_aggregate",
            description=(
                "Run the sentiment engine live for one ticker on a given date. "
                "Returns the aggregated sentiment payload (score, source breakdown, "
                "history-adjusted score)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "on_date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD)",
                    },
                },
                "required": ["ticker", "on_date"],
            },
        ),
        Tool(
            name="quant_aggregate",
            description=(
                "Run the quantitative engine live for one ticker as of a given date. "
                "Returns OHLCV-derived metrics (RSI, MAs, health score, sector-relative)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "as_of": {"type": "string", "description": "ISO date"},
                    "sector": {"type": "string"},
                },
                "required": ["ticker", "as_of"],
            },
        ),
        Tool(
            name="enrichment_aggregate",
            description=(
                "Run the enrichment engine live for one ticker (insider trades, "
                "analyst activity, upcoming earnings)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "on_date": {"type": "string"},
                    "earnings_date": {"type": "string"},
                },
                "required": ["ticker", "on_date"],
            },
        ),
        Tool(
            name="earnings_calendar",
            description="List upcoming earnings across the watchlist within the next 14 days.",
            inputSchema={
                "type": "object",
                "properties": {
                    "on_date": {
                        "type": "string",
                        "description": "ISO date; defaults to today",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="run_signals",
            description=(
                "Run all three engines (sentiment, quant, enrichment) for the full "
                "watchlist on the given date and persist results. No LLM call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "on_date": {"type": "string", "description": "ISO date; defaults to today"},
                },
                "required": [],
            },
        ),
    ]


def _engine_dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "sentiment_aggregate":
        from src.engines.sentiment.aggregator import aggregate
        return aggregate(args["ticker"], _parse_date(args["on_date"]))
    if name == "quant_aggregate":
        from src.engines.quantitative.aggregator import aggregate
        return aggregate(
            args["ticker"], _parse_date(args["as_of"]), sector=args.get("sector")
        )
    if name == "enrichment_aggregate":
        from src.engines.enrichment.aggregator import aggregate
        er = args.get("earnings_date")
        return aggregate(
            args["ticker"],
            _parse_date(args["on_date"]),
            earnings_date=_parse_date(er) if er else None,
        )
    if name == "earnings_calendar":
        from src.core import earnings_calendar
        return earnings_calendar(_parse_date(args.get("on_date"), default_today=True))
    if name == "run_signals":
        from src.core import run_signals
        with _session_scope() as session:
            return run_signals(_parse_date(args.get("on_date"), default_today=True), session)
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Tracking tools
# ---------------------------------------------------------------------------


def _tracking_schemas() -> list[Tool]:
    return [
        Tool(
            name="score_signals",
            description=(
                "Score open SFE signals against realized prices and return per-signal "
                "outcomes plus aggregate stats."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "on_date": {"type": "string", "description": "ISO date; defaults to today"},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_ticker_summary",
            description=(
                "Read-only quick-look for a single ticker: latest stored sentiment, "
                "quant, enrichment, and most recent earnings outcome. No live API calls."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "on_date": {"type": "string"},
                },
                "required": ["ticker", "on_date"],
            },
        ),
        Tool(
            name="list_recent_signals",
            description="Return the most recent SignalDaily rows across all tickers.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                    "ticker": {"type": "string", "description": "Optional ticker filter"},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_latest_outcome",
            description="Get the most recent earnings outcome row for one ticker.",
            inputSchema={
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        ),
    ]


def _tracking_dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "score_signals":
        from src.core import score_signals
        with _session_scope() as session:
            return score_signals(
                session, _parse_date(args.get("on_date"), default_today=True)
            )
    if name == "get_ticker_summary":
        from src.core import get_ticker_summary
        with _session_scope() as session:
            return get_ticker_summary(args["ticker"], _parse_date(args["on_date"]), session)
    if name == "list_recent_signals":
        from src.storage.models import SignalDaily
        limit = int(args.get("limit", 20))
        ticker = args.get("ticker")
        with _session_scope() as session:
            stmt = select(SignalDaily).order_by(desc(SignalDaily.as_of)).limit(limit)
            if ticker:
                stmt = (
                    select(SignalDaily)
                    .where(SignalDaily.ticker == ticker.upper())
                    .order_by(desc(SignalDaily.as_of))
                    .limit(limit)
                )
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "ticker": r.ticker,
                    "as_of": r.as_of.isoformat(),
                    "direction": r.direction,
                    "conviction": r.conviction,
                    "dominant_component": r.dominant_component,
                    "reasoning": r.reasoning,
                    "entry_price": r.entry_price,
                }
                for r in rows
            ]
    if name == "get_latest_outcome":
        from src.storage.earnings_repo import get_latest_outcome
        with _session_scope() as session:
            outcome = get_latest_outcome(session, args["ticker"])
            if outcome is None:
                return None
            return {
                "ticker": outcome.ticker,
                "earnings_date": outcome.earnings_date.isoformat(),
                "predicted_dir": outcome.predicted_dir,
                "conviction": outcome.conviction,
                "outcome": outcome.outcome,
            }
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Portfolio tools — reuse src/agent/tools.py verbatim
# ---------------------------------------------------------------------------


PORTFOLIO_TOOL_NAMES = {schema["name"] for schema in PORTFOLIO_TOOL_SCHEMAS}


def _portfolio_tools() -> list[Tool]:
    return [
        Tool(
            name=schema["name"],
            description=schema["description"],
            inputSchema=schema["input_schema"],
        )
        for schema in PORTFOLIO_TOOL_SCHEMAS
    ]


def _portfolio_dispatch(name: str, args: dict[str, Any]) -> Any:
    """Build a fresh ToolContext per call and delegate to execute_tool.

    A new session, signals payload, and price snapshot are built each call so
    the long-lived MCP process never holds stale state.
    """
    from src.meta.payload_builder import build_payload

    today = Date.today()
    with _session_scope() as session:
        portfolio = get_or_create_portfolio(
            session, name="default", starting_equity=100_000.0, inception_date=today
        )
        signals_payload = build_payload(session, today)
        tickers = [t["ticker"] for t in signals_payload.get("tickers", [])]
        current_prices = _fetch_prices(tickers)

        ctx = ToolContext(
            session=session,
            portfolio=portfolio,
            signals_payload=signals_payload,
            current_prices=current_prices,
            trade_date=today,
        )
        result_json = execute_tool(ctx, name, args)
        return json.loads(result_json)


def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    try:
        import yfinance as yf
    except ImportError:
        return {}
    prices: dict[str, float] = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
            if price is not None:
                prices[ticker] = float(price)
        except Exception:
            pass
    return prices


# ---------------------------------------------------------------------------
# Server wiring
# ---------------------------------------------------------------------------


server: Server = Server("sfe")


@server.list_tools()
async def _list_tools() -> list[Tool]:
    return _engine_schemas() + _portfolio_tools() + _tracking_schemas()


@server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    args = arguments or {}
    try:
        if name in PORTFOLIO_TOOL_NAMES:
            return _json(_portfolio_dispatch(name, args))
        if name in {t.name for t in _engine_schemas()}:
            return _json(_engine_dispatch(name, args))
        if name in {t.name for t in _tracking_schemas()}:
            return _json(_tracking_dispatch(name, args))
        return _json({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        return _json({"error": str(exc), "type": type(exc).__name__})


async def _serve() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    import asyncio

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
