"""Streamlit dashboard for the agentic portfolio manager."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st
import plotly.graph_objects as go
from sqlalchemy import select, func
from streamlit_autorefresh import st_autorefresh

from src.storage.db import get_engine, get_session
from src.storage.models import (
    AgentSession,
    Base,
    Portfolio,
    Position,
    Trade,
)
from src.storage.portfolio_repo import get_positions, get_trades, portfolio_snapshot


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@st.cache_resource
def _init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    return True


def _get_session():
    _init_db()
    return get_session()


def _fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    try:
        import yfinance as yf

        prices: dict[str, float] = {}
        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                price = getattr(info, "last_price", None) or getattr(
                    info, "previous_close", None
                )
                if price is not None:
                    prices[ticker] = float(price)
            except Exception:
                pass
        return prices
    except ImportError:
        return {}


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SFE Agent Dashboard",
    page_icon="📈",
    layout="wide",
)

st.title("SFE Agent Dashboard")

with st.sidebar:
    st.subheader("Auto-refresh")
    auto_refresh = st.toggle("Enable", value=False, key="auto_refresh_enabled")
    interval_sec = st.slider(
        "Interval (seconds)", min_value=10, max_value=300, value=30, step=5,
        disabled=not auto_refresh,
    )
if auto_refresh:
    st_autorefresh(interval=interval_sec * 1000, key="dashboard_autorefresh")

session = _get_session()

# ---------------------------------------------------------------------------
# Portfolio selector
# ---------------------------------------------------------------------------

portfolios = list(
    session.execute(
        select(Portfolio).where(Portfolio.active.is_(True))
    ).scalars().all()
)

if not portfolios:
    st.warning("No portfolios found. Run `/weekly-review` in Claude Code first.")
    st.stop()

portfolio_names = [p.name for p in portfolios]
selected_name = st.sidebar.selectbox("Portfolio", portfolio_names)
portfolio = next(p for p in portfolios if p.name == selected_name)

# ---------------------------------------------------------------------------
# Current prices & snapshot
# ---------------------------------------------------------------------------

positions = get_positions(session, portfolio.id)
tickers = [p.ticker for p in positions]

with st.sidebar:
    st.subheader("Settings")
    refresh_prices = st.button("Refresh Prices")

if refresh_prices or "prices" not in st.session_state:
    st.session_state.prices = _fetch_current_prices(tickers) if tickers else {}

snap = portfolio_snapshot(session, portfolio, st.session_state.prices)

# ---------------------------------------------------------------------------
# Top-level metrics
# ---------------------------------------------------------------------------

st.subheader("Portfolio Overview")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Equity", f"${snap['equity']:,.2f}")
col2.metric("Cash", f"${snap['cash']:,.2f}")
col3.metric("Return", f"{snap['total_return_pct']:+.2f}%")
col4.metric("Positions", snap["position_count"])
col5.metric("Starting Equity", f"${snap['starting_equity']:,.2f}")

# ---------------------------------------------------------------------------
# Positions table
# ---------------------------------------------------------------------------

st.subheader("Open Positions")

if snap["positions"]:
    pos_data = []
    for p in snap["positions"]:
        alloc = (p["shares"] * p["current_price"]) / snap["equity"] * 100 if snap["equity"] else 0
        pos_data.append({
            "Ticker": p["ticker"],
            "Direction": p["direction"].upper(),
            "Shares": f"{p['shares']:.2f}",
            "Entry": f"${p['entry_price']:,.2f}",
            "Current": f"${p['current_price']:,.2f}",
            "P&L": f"${p['unrealized_pnl']:,.2f}",
            "Allocation": f"{alloc:.1f}%",
            "Opened": p["entry_date"],
        })

    st.dataframe(
        pos_data,
        use_container_width=True,
        hide_index=True,
    )

    # Allocation pie chart
    fig_alloc = go.Figure(data=[
        go.Pie(
            labels=[p["ticker"] for p in snap["positions"]] + ["Cash"],
            values=[p["shares"] * p["current_price"] for p in snap["positions"]] + [snap["cash"]],
            hole=0.4,
            textinfo="label+percent",
        )
    ])
    fig_alloc.update_layout(
        title="Portfolio Allocation",
        height=400,
        margin=dict(t=40, b=20, l=20, r=20),
    )
    st.plotly_chart(fig_alloc, use_container_width=True)
else:
    st.info("No open positions.")

# ---------------------------------------------------------------------------
# Equity curve (from agent sessions)
# ---------------------------------------------------------------------------

st.subheader("Equity Over Time")

agent_sessions = list(
    session.execute(
        select(AgentSession)
        .where(AgentSession.portfolio_id == portfolio.id)
        .order_by(AgentSession.run_date)
    ).scalars().all()
)

if agent_sessions:
    dates = [s.run_date for s in agent_sessions]
    equities_after = [s.portfolio_snapshot_after.get("equity", 0) for s in agent_sessions]
    equities_before = [s.portfolio_snapshot_before.get("equity", 0) for s in agent_sessions]

    fig_equity = go.Figure()
    fig_equity.add_trace(go.Scatter(
        x=dates,
        y=equities_after,
        mode="lines+markers",
        name="Equity (end of session)",
        line=dict(color="#2196F3", width=2),
    ))
    fig_equity.add_hline(
        y=portfolio.starting_equity,
        line_dash="dash",
        line_color="gray",
        annotation_text="Starting Equity",
    )
    fig_equity.update_layout(
        height=350,
        margin=dict(t=20, b=20, l=20, r=20),
        yaxis_title="Equity ($)",
        xaxis_title="Date",
    )
    st.plotly_chart(fig_equity, use_container_width=True)
else:
    st.info("No agent sessions yet — run `/weekly-review` in Claude Code to start building history.")

# ---------------------------------------------------------------------------
# Trade history
# ---------------------------------------------------------------------------

st.subheader("Trade History")

trades = get_trades(session, portfolio.id, limit=100)

if trades:
    trade_data = []
    for t in trades:
        trade_data.append({
            "Date": t.trade_date.isoformat(),
            "Ticker": t.ticker,
            "Action": t.action.upper(),
            "Direction": t.direction.upper(),
            "Shares": f"{t.shares:.2f}",
            "Price": f"${t.price:,.2f}",
            "Reasoning": (t.reasoning or "")[:120],
        })
    st.dataframe(trade_data, use_container_width=True, hide_index=True)
else:
    st.info("No trades yet.")

# ---------------------------------------------------------------------------
# Agent session logs
# ---------------------------------------------------------------------------

st.subheader("Agent Decision Logs")

if agent_sessions:
    selected_session = st.selectbox(
        "Select session",
        agent_sessions,
        format_func=lambda s: f"{s.run_date} — {s.decisions_made} decisions ({s.model})",
        index=len(agent_sessions) - 1,
    )

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Decisions", selected_session.decisions_made)
    col_b.metric(
        "Equity Before",
        f"${selected_session.portfolio_snapshot_before.get('equity', 0):,.2f}",
    )
    col_c.metric(
        "Equity After",
        f"${selected_session.portfolio_snapshot_after.get('equity', 0):,.2f}",
    )

    trace = selected_session.reasoning_trace
    if isinstance(trace, list):
        for entry in trace:
            entry_type = entry.get("type", "")

            if entry_type == "tool_call":
                tool = entry.get("tool", "")
                inp = entry.get("input", {})
                result = entry.get("result", {})

                if tool in ("open_position", "close_position", "resize_position"):
                    status = result.get("status", result.get("error", "unknown"))
                    ticker = inp.get("ticker", "")
                    direction = inp.get("direction", "")
                    alloc = inp.get("allocation_pct", inp.get("new_allocation_pct", ""))

                    icon = "🟢" if tool == "open_position" else "🔴" if tool == "close_position" else "🔄"
                    header = f"{icon} **{tool}** — {ticker} {direction} {alloc}%"

                    with st.expander(header, expanded=False):
                        st.write(f"**Reasoning:** {inp.get('reasoning', '')}")
                        st.json(result)

                elif tool in ("get_portfolio_state", "get_signals", "get_ticker_detail", "get_trade_history"):
                    with st.expander(f"🔍 **{tool}** {inp.get('ticker', '')}", expanded=False):
                        st.json(result)

            elif entry_type == "final_message":
                with st.expander("💬 **Agent Summary**", expanded=True):
                    st.markdown(entry.get("content", ""))
else:
    st.info("No agent sessions yet.")

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

session.close()
