"""Agent tool definitions and execution for the portfolio MCP server.

Each tool has a JSON schema (for the Anthropic tool_use API) and an execute
function that takes the parsed input and returns a result dict.
"""

from __future__ import annotations

import json
from datetime import date as Date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from src.execution.broker import Broker
from src.storage.models import EnrichmentDaily, Portfolio, QuantDaily, SentimentDaily
from src.storage.portfolio_repo import (
    get_position,
    get_trades,
    portfolio_snapshot,
)

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic tool_use format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_portfolio_state",
        "description": (
            "Get the current portfolio state: cash, equity, total return, "
            "and all open positions with unrealized P&L."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_signals",
        "description": (
            "Get the latest engine outputs (sentiment, quant, enrichment) "
            "for all watchlist tickers. This is your primary source of market data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_ticker_detail",
        "description": (
            "Get a detailed view of a single ticker: all engine data, "
            "current position (if any), and recent trades."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol (e.g. NVDA)",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "open_position",
        "description": (
            "Open a new position. Specify the ticker, direction (long/short), "
            "and size as a percentage of total portfolio equity. "
            "Cannot open a position in a ticker you already hold — use resize_position instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol",
                },
                "direction": {
                    "type": "string",
                    "enum": ["long", "short"],
                    "description": "Position direction",
                },
                "allocation_pct": {
                    "type": "number",
                    "description": "Position size as percentage of total portfolio equity (e.g. 5.0 for 5%)",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are opening this position — cite the specific signals driving this decision",
                },
            },
            "required": ["ticker", "direction", "allocation_pct", "reasoning"],
        },
    },
    {
        "name": "close_position",
        "description": (
            "Close an existing position entirely. All shares are sold at the current market price."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol of the position to close",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are closing this position",
                },
            },
            "required": ["ticker", "reasoning"],
        },
    },
    {
        "name": "resize_position",
        "description": (
            "Resize an existing position to a new allocation percentage of total equity. "
            "Use this to increase or decrease position size."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol of the position to resize",
                },
                "new_allocation_pct": {
                    "type": "number",
                    "description": "New target size as percentage of total equity (e.g. 3.0 for 3%)",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are resizing this position",
                },
            },
            "required": ["ticker", "new_allocation_pct", "reasoning"],
        },
    },
    {
        "name": "get_trade_history",
        "description": (
            "Get the history of your recent trades — what you bought, sold, "
            "resized, and why. Use this to review your past decisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent trades to return (default 20)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "investigate_sentiment",
        "description": (
            "Investigate sentiment for a ticker. In summary mode, returns the cached daily "
            "sentiment data. In deep mode, launches a sentiment analyst sub-agent that can "
            "fetch live news, SEC filings, and score text to answer your specific question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol (e.g. NVDA)",
                },
                "question": {
                    "type": "string",
                    "description": "What you want to understand — only used in deep mode",
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "deep"],
                    "description": "summary = cached data only (fast, no API calls); deep = live investigation via sub-agent",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_quant_detail",
        "description": (
            "Get quantitative/technical analysis for a ticker. Default returns cached daily data. "
            "With depth=full, fetches live price data and computes fresh technicals, "
            "sector-relative performance, and a 20-day price table."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol (e.g. NVDA)",
                },
                "depth": {
                    "type": "string",
                    "enum": ["standard", "full"],
                    "description": "standard = cached DB row; full = live OHLCV + fresh technicals",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_enrichment_detail",
        "description": (
            "Get enrichment data (insider trades, analyst activity, earnings) for a ticker. "
            "Default returns cached daily data. With depth=full, fetches live data with "
            "full insider trade details and analyst revision timeline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol (e.g. NVDA)",
                },
                "depth": {
                    "type": "string",
                    "enum": ["standard", "full"],
                    "description": "standard = cached DB row; full = live insider/analyst/earnings with full detail",
                },
            },
            "required": ["ticker"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution context
# ---------------------------------------------------------------------------


class ToolContext:
    """Holds the state needed to execute agent tools."""

    def __init__(
        self,
        session: Session,
        portfolio: Portfolio,
        signals_payload: dict[str, Any],
        current_prices: dict[str, float],
        trade_date: Date,
        broker: Broker | None = None,
    ):
        self.session = session
        self.portfolio = portfolio
        self.signals_payload = signals_payload
        self.current_prices = current_prices
        self.trade_date = trade_date
        self._broker = broker

    @property
    def broker(self) -> Broker:
        if self._broker is None:
            from src.execution.simulated import SimulatedBroker

            self._broker = SimulatedBroker(
                self.session, self.portfolio, self.current_prices, self.trade_date
            )
        return self._broker

    def _snapshot(self) -> dict[str, Any]:
        return portfolio_snapshot(
            self.session, self.portfolio, self.current_prices
        )

    def _equity(self) -> float:
        snap = self._snapshot()
        return snap["equity"]


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


def execute_tool(ctx: ToolContext, tool_name: str, tool_input: dict[str, Any]) -> str:
    handlers = {
        "get_portfolio_state": _exec_get_portfolio_state,
        "get_signals": _exec_get_signals,
        "get_ticker_detail": _exec_get_ticker_detail,
        "open_position": _exec_open_position,
        "close_position": _exec_close_position,
        "resize_position": _exec_resize_position,
        "get_trade_history": _exec_get_trade_history,
        "investigate_sentiment": _exec_investigate_sentiment,
        "get_quant_detail": _exec_get_quant_detail,
        "get_enrichment_detail": _exec_get_enrichment_detail,
    }
    handler = handlers.get(tool_name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        result = handler(ctx, tool_input)
        return json.dumps(result, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _exec_get_portfolio_state(ctx: ToolContext, _input: dict) -> dict:
    return ctx._snapshot()


def _exec_get_signals(ctx: ToolContext, _input: dict) -> dict:
    return ctx.signals_payload


def _exec_get_ticker_detail(ctx: ToolContext, inp: dict) -> dict:
    ticker = inp["ticker"].upper()
    ticker_data = None
    for t in ctx.signals_payload.get("tickers", []):
        if t["ticker"] == ticker:
            ticker_data = t
            break

    pos = get_position(ctx.session, ctx.portfolio.id, ticker)
    pos_data = None
    if pos is not None:
        cur_price = ctx.current_prices.get(ticker, pos.current_price or pos.entry_price)
        if pos.direction == "long":
            pnl = (cur_price - pos.entry_price) * pos.shares
        else:
            pnl = (pos.entry_price - cur_price) * pos.shares
        pos_data = {
            "direction": pos.direction,
            "shares": pos.shares,
            "entry_price": pos.entry_price,
            "current_price": cur_price,
            "unrealized_pnl": round(pnl, 2),
            "entry_date": pos.entry_date.isoformat(),
        }

    trades = get_trades(ctx.session, ctx.portfolio.id, limit=50)
    ticker_trades = [
        {
            "action": t.action,
            "direction": t.direction,
            "shares": t.shares,
            "price": t.price,
            "trade_date": t.trade_date.isoformat(),
            "reasoning": t.reasoning or "",
        }
        for t in trades
        if t.ticker == ticker
    ][:10]

    return {
        "ticker": ticker,
        "signals": ticker_data,
        "current_position": pos_data,
        "recent_trades": ticker_trades,
    }


def _exec_open_position(ctx: ToolContext, inp: dict) -> dict:
    ticker = inp["ticker"].upper()
    direction = inp["direction"]
    alloc_pct = inp["allocation_pct"]
    reasoning = inp["reasoning"]

    existing = get_position(ctx.session, ctx.portfolio.id, ticker)
    if existing is not None:
        return {
            "error": f"Already holding {ticker}. Use resize_position to adjust, or close_position first."
        }

    price = ctx.current_prices.get(ticker)
    if price is None:
        return {"error": f"No current price available for {ticker}"}

    equity = ctx._equity()
    target_value = equity * (alloc_pct / 100.0)
    shares = round(target_value / price, 4)

    if direction == "long" and shares * price > ctx.portfolio.cash:
        return {
            "error": f"Insufficient cash. Need ${shares * price:,.2f} but only ${ctx.portfolio.cash:,.2f} available."
        }

    side = "buy" if direction == "long" else "sell_short"
    ticket = ctx.broker.submit(
        ticker=ticker, side=side, qty=shares,
        order_type="market", reasoning=reasoning,
    )
    if ticket.status == "rejected":
        return {"error": ticket.error or "order rejected"}
    if ticket.status != "filled":
        return {
            "status": ticket.status,
            "ticker": ticker,
            "client_order_id": ticket.client_order_id,
            "message": "order submitted, fill pending",
        }

    fill_price = ticket.avg_fill_price or price
    return {
        "status": "opened",
        "ticker": ticker,
        "direction": direction,
        "shares": ticket.qty_filled,
        "price": fill_price,
        "cost": round(ticket.qty_filled * fill_price, 2),
        "allocation_pct": alloc_pct,
    }


def _exec_close_position(ctx: ToolContext, inp: dict) -> dict:
    ticker = inp["ticker"].upper()
    reasoning = inp["reasoning"]

    pos = get_position(ctx.session, ctx.portfolio.id, ticker)
    if pos is None:
        return {"error": f"No open position in {ticker}"}

    shares = pos.shares
    entry_price = pos.entry_price
    direction = pos.direction
    side = "sell" if direction == "long" else "buy_to_cover"

    ticket = ctx.broker.submit(
        ticker=ticker, side=side, qty=shares,
        order_type="market", reasoning=reasoning,
    )
    if ticket.status == "rejected":
        return {"error": ticket.error or "order rejected"}
    if ticket.status != "filled":
        return {
            "status": ticket.status,
            "ticker": ticker,
            "client_order_id": ticket.client_order_id,
            "message": "close order submitted, fill pending",
        }

    fill_price = ticket.avg_fill_price or ctx.current_prices.get(ticker, entry_price)
    if direction == "long":
        pnl = (fill_price - entry_price) * shares
    else:
        pnl = (entry_price - fill_price) * shares

    return {
        "status": "closed",
        "ticker": ticker,
        "shares": shares,
        "entry_price": entry_price,
        "exit_price": fill_price,
        "realized_pnl": round(pnl, 2),
    }


def _exec_resize_position(ctx: ToolContext, inp: dict) -> dict:
    ticker = inp["ticker"].upper()
    new_alloc_pct = inp["new_allocation_pct"]
    reasoning = inp["reasoning"]

    pos = get_position(ctx.session, ctx.portfolio.id, ticker)
    if pos is None:
        return {"error": f"No open position in {ticker}. Use open_position to start one."}

    price = ctx.current_prices.get(ticker)
    if price is None:
        price = pos.current_price or pos.entry_price

    equity = ctx._equity()
    target_value = equity * (new_alloc_pct / 100.0)
    new_shares = round(target_value / price, 4)
    old_shares = pos.shares
    delta = new_shares - old_shares

    if abs(delta) < 1e-4:
        return {
            "status": "noop",
            "ticker": ticker,
            "shares": old_shares,
            "message": "already at target allocation",
        }

    if pos.direction == "long":
        side = "buy" if delta > 0 else "sell"
    else:
        side = "sell_short" if delta > 0 else "buy_to_cover"

    qty = abs(delta)
    cash_needed = qty * price
    if pos.direction == "long" and delta > 0 and cash_needed > ctx.portfolio.cash:
        return {
            "error": f"Insufficient cash to increase position. Need ${cash_needed:,.2f} more."
        }
    if pos.direction == "short" and delta < 0 and cash_needed > ctx.portfolio.cash:
        return {
            "error": f"Insufficient cash to cover. Need ${cash_needed:,.2f}."
        }

    ticket = ctx.broker.submit(
        ticker=ticker, side=side, qty=qty,
        order_type="market", reasoning=reasoning,
    )
    if ticket.status == "rejected":
        return {"error": ticket.error or "order rejected"}
    if ticket.status != "filled":
        return {
            "status": ticket.status,
            "ticker": ticker,
            "client_order_id": ticket.client_order_id,
            "message": "resize order submitted, fill pending",
        }

    return {
        "status": "resized",
        "ticker": ticker,
        "old_shares": old_shares,
        "new_shares": new_shares,
        "price": ticket.avg_fill_price or price,
        "new_allocation_pct": new_alloc_pct,
    }


def _exec_get_trade_history(ctx: ToolContext, inp: dict) -> dict:
    limit = inp.get("limit", 20)
    trades = get_trades(ctx.session, ctx.portfolio.id, limit=limit)
    return {
        "trades": [
            {
                "ticker": t.ticker,
                "action": t.action,
                "direction": t.direction,
                "shares": t.shares,
                "price": t.price,
                "trade_date": t.trade_date.isoformat(),
                "reasoning": t.reasoning or "",
            }
            for t in trades
        ]
    }


# ---------------------------------------------------------------------------
# New tools: investigate_sentiment, get_quant_detail, get_enrichment_detail
# ---------------------------------------------------------------------------


def _exec_investigate_sentiment(ctx: ToolContext, inp: dict) -> dict:
    from src.meta.payload_builder import _latest, _sentiment_view

    ticker = inp["ticker"].upper()
    mode = inp.get("mode", "summary")

    if mode == "summary":
        row = _latest(ctx.session, SentimentDaily, ticker, ctx.trade_date)
        view = _sentiment_view(row)
        if view is None:
            return {"error": f"No cached sentiment data for {ticker} as of {ctx.trade_date}"}
        return {"ticker": ticker, "mode": "summary", **view}

    from src.agent.sub_agents.sentiment import SentimentSubAgent

    question = inp.get("question", f"Analyze the current sentiment landscape for {ticker}.")
    agent = SentimentSubAgent(ctx.session, ctx.trade_date)
    user_msg = f"Deep investigation for {ticker}: {question}"

    logger.info("sub-agent: launching sentiment investigation for {t}", t=ticker)
    result = agent.run(user_msg)
    logger.info(
        "sub-agent: sentiment done for {t} in {n} turns ({it} in / {ot} out tokens)",
        t=ticker,
        n=len(result.trace),
        it=result.token_usage.get("input_tokens", 0),
        ot=result.token_usage.get("output_tokens", 0),
    )

    return {
        "ticker": ticker,
        "mode": "deep",
        "analysis": result.answer,
        "sub_agent_trace": result.trace,
        "tokens_used": result.token_usage,
    }


def _exec_get_quant_detail(ctx: ToolContext, inp: dict) -> dict:
    from src.meta.payload_builder import _latest, _quant_view

    ticker = inp["ticker"].upper()
    depth = inp.get("depth", "standard")

    if depth == "standard":
        row = _latest(ctx.session, QuantDaily, ticker, ctx.trade_date)
        view = _quant_view(row)
        if view is None:
            return {"error": f"No cached quant data for {ticker} as of {ctx.trade_date}"}
        return {"ticker": ticker, "depth": "standard", **view}

    from src.config import load_watchlist
    from src.engines.quantitative import model, price_fetcher, technicals
    from src.engines.quantitative.aggregator import _sector_relative

    sector = None
    for entry in load_watchlist():
        if entry["ticker"].upper() == ticker:
            sector = entry.get("sector")
            break

    ohlcv = price_fetcher.fetch_ohlcv(ticker, ctx.trade_date)
    if not ohlcv:
        return {"error": f"No OHLCV data returned for {ticker}"}

    indicators = technicals.compute_indicators(ohlcv)
    health = model.predict_health(indicators)
    sector_rel = _sector_relative(ohlcv, sector, ctx.trade_date)

    price_table = [
        {"date": bar["date"].isoformat() if hasattr(bar["date"], "isoformat") else str(bar["date"]),
         "close": round(bar["close"], 2),
         "volume": bar["volume"]}
        for bar in ohlcv[-20:]
    ]

    return {
        "ticker": ticker,
        "depth": "full",
        **indicators,
        "health_score": health,
        **sector_rel,
        "price_table_20d": price_table,
    }


def _exec_get_enrichment_detail(ctx: ToolContext, inp: dict) -> dict:
    from src.meta.payload_builder import _latest, _enrichment_view

    ticker = inp["ticker"].upper()
    depth = inp.get("depth", "standard")

    if depth == "standard":
        row = _latest(ctx.session, EnrichmentDaily, ticker, ctx.trade_date)
        view = _enrichment_view(row)
        if view is None:
            return {"error": f"No cached enrichment data for {ticker} as of {ctx.trade_date}"}
        return {"ticker": ticker, "depth": "standard", **view}

    from src.engines.enrichment import analyst_revisions, event_calendar, insider_trades

    events_block: dict[str, Any]
    try:
        events = event_calendar.fetch_earnings(ticker, ctx.trade_date)
        events_block = event_calendar.summarize(events, ctx.trade_date)
    except Exception as exc:
        logger.warning(f"{ticker}: earnings calendar fetch failed: {exc}")
        events_block = event_calendar.summarize([], ctx.trade_date)

    earnings_date = None
    if events_block["next_earnings"]:
        try:
            from datetime import date
            earnings_date = date.fromisoformat(events_block["next_earnings"]["date"])
        except (ValueError, KeyError):
            pass

    insider_block: dict[str, Any]
    try:
        insider_end = earnings_date if earnings_date else ctx.trade_date
        txns = insider_trades.fetch_transactions(ticker, insider_end)
        insider_block = insider_trades.summarize(txns)
    except Exception as exc:
        logger.warning(f"{ticker}: insider fetch failed: {exc}")
        insider_block = insider_trades.summarize([])

    analyst_block: dict[str, Any]
    try:
        recs = analyst_revisions.fetch_recommendations(ticker)
        analyst_block = analyst_revisions.summarize(recs, before_date=earnings_date)
    except Exception as exc:
        logger.warning(f"{ticker}: analyst fetch failed: {exc}")
        analyst_block = analyst_revisions.summarize([])

    return {
        "ticker": ticker,
        "depth": "full",
        "insider_trades": insider_block,
        "next_earnings": events_block["next_earnings"],
        "upcoming_events": events_block["upcoming_events"],
        "analyst_activity": analyst_block,
    }
