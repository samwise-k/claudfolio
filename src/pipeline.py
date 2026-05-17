"""SFE CLI entry point. Thin wrappers dispatching to core.py."""

from __future__ import annotations

import argparse
import sys
from datetime import date as Date
from pathlib import Path
from typing import Any

from loguru import logger

from src.bootstrap import load_env


# ---------------------------------------------------------------------------
# Shared bootstrap helpers (CLI-only: argparse + DB init)
# ---------------------------------------------------------------------------


def _bootstrap_db():
    """Load env, create DB dir if needed, run CREATE TABLE, return session."""
    load_env()

    from src.storage.db import get_engine, get_session
    from src.storage.models import Base

    engine = get_engine()
    db_path = engine.url.database
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    return get_session()


def _parse_date(args: argparse.Namespace) -> Date:
    return Date.fromisoformat(args.date) if args.date else Date.today()


def _resolve_tickers(args: argparse.Namespace) -> list[str] | None:
    """Return ticker list from --ticker flag or watchlist. None = empty watchlist."""
    from src.config import load_watchlist

    if args.ticker:
        return [args.ticker.upper()]
    tickers = [t["ticker"] for t in load_watchlist()]
    if not tickers:
        logger.error(
            "watchlist is empty — pass --ticker SYM or fill config/watchlist.yaml"
        )
        return None
    return tickers


def _resolve_watchlist_entries(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    """Like _resolve_tickers but returns full watchlist dicts (with sector)."""
    from src.config import load_watchlist

    watchlist = load_watchlist()
    if args.ticker:
        sym = args.ticker.upper()
        sector = next(
            (t.get("sector") for t in watchlist if t["ticker"].upper() == sym), None
        )
        return [{"ticker": sym, "sector": sector}]
    if not watchlist:
        logger.error(
            "watchlist is empty — pass --ticker SYM or fill config/watchlist.yaml"
        )
        return None
    return watchlist


# ---------------------------------------------------------------------------
# Engine commands (thin wrappers around src.core)
# ---------------------------------------------------------------------------


def run_sentiment(args: argparse.Namespace) -> int:
    from src.core import run_sentiment as _run

    session = _bootstrap_db()
    on_date = _parse_date(args)
    tickers = _resolve_tickers(args)
    if tickers is None:
        return 1
    try:
        _run(tickers, on_date, session)
    finally:
        session.close()
    return 0


def run_quant(args: argparse.Namespace) -> int:
    from src.core import run_quant as _run

    session = _bootstrap_db()
    as_of = _parse_date(args)
    entries = _resolve_watchlist_entries(args)
    if entries is None:
        return 1
    try:
        _run(entries, as_of, session)
    finally:
        session.close()
    return 0


def run_enrichment(args: argparse.Namespace) -> int:
    from src.core import run_enrichment as _run

    session = _bootstrap_db()
    on_date = _parse_date(args)
    tickers = _resolve_tickers(args)
    if tickers is None:
        return 1
    try:
        _run(tickers, on_date, session)
    finally:
        session.close()
    return 0


def run_meta(args: argparse.Namespace) -> int:
    from src.core import run_meta as _run

    session = _bootstrap_db()
    on_date = _parse_date(args)
    tickers = _resolve_tickers(args)
    if tickers is None:
        return 1
    try:
        result = _run(tickers, on_date, session)
    except Exception as exc:
        logger.error("Claude API call failed: {exc}", exc=exc)
        return 1
    finally:
        session.close()
    print(result)
    return 0


def run_earnings_brief(args: argparse.Namespace) -> int:
    from src.core import run_earnings_brief as _run

    session = _bootstrap_db()
    on_date = _parse_date(args)
    ticker = args.ticker.upper()

    earnings_date: Date | None = None
    if args.earnings_date:
        earnings_date = Date.fromisoformat(args.earnings_date)

    try:
        result = _run(ticker, on_date, session, earnings_date=earnings_date)
    except ValueError as exc:
        logger.error(str(exc))
        return 1
    except Exception as exc:
        logger.error("Claude API call failed: {exc}", exc=exc)
        return 1
    finally:
        session.close()
    print(result)
    return 0


def log_outcome(args: argparse.Namespace) -> int:
    from src.core import log_outcome as _log

    session = _bootstrap_db()

    data = {
        "ticker": args.ticker.upper(),
        "earnings_date": Date.fromisoformat(args.earnings_date),
        "brief_date": Date.fromisoformat(args.brief_date) if args.brief_date else Date.today(),
        "predicted_dir": args.predicted_dir,
        "conviction": float(args.conviction),
        "actual_eps_surp": float(args.actual_eps_surp) if args.actual_eps_surp else None,
        "actual_rev_surp": float(args.actual_rev_surp) if args.actual_rev_surp else None,
        "stock_move_1d": float(args.stock_move_1d) if args.stock_move_1d else None,
        "outcome": args.outcome or "pending",
        "notes": args.notes,
    }

    try:
        _log(data, session)
    finally:
        session.close()
    return 0


def earnings_calendar(args: argparse.Namespace) -> int:
    from src.core import earnings_calendar as _cal

    _bootstrap_db()
    on_date = _parse_date(args)
    rows = _cal(on_date)

    if not rows:
        print("No watchlist tickers reporting in the next 14 days.")
        return 0

    header = f"{'TICKER':<10} {'REPORT DATE':<14} {'DAYS UNTIL':<12} {'CONSENSUS EPS':<16}"
    print(header)
    print("-" * len(header))
    for r in rows:
        eps_str = f"${r['consensus_eps']:.2f}" if r["consensus_eps"] is not None else "—"
        print(
            f"{r['ticker']:<10} {r['date']:<14} {r['days_until']:<12} {eps_str:<16}"
        )
    return 0


def cmd_log_signal(args: argparse.Namespace) -> int:
    from src.core import log_signal as _log

    session = _bootstrap_db()
    data = {
        "ticker": args.ticker.upper(),
        "as_of": Date.fromisoformat(args.as_of) if args.as_of else Date.today(),
        "direction": args.direction,
        "conviction": float(args.conviction),
        "dominant_component": args.dominant_component,
        "reasoning": args.reasoning,
        "entry_price": float(args.entry_price) if args.entry_price else None,
        "signal_components": {},
    }
    try:
        _log(data, session)
    finally:
        session.close()
    return 0


def cmd_run_signals(args: argparse.Namespace) -> int:
    from src.core import run_signals as _run

    session = _bootstrap_db()
    on_date = _parse_date(args)
    try:
        results = _run(on_date, session)
    finally:
        session.close()

    ok = sum(
        1 for engine in results.values()
        for r in engine
        if "error" not in r
    )
    errs = sum(
        1 for engine in results.values()
        for r in engine
        if "error" in r
    )
    print(f"Done: {ok} successful, {errs} errors")
    return 1 if ok == 0 else 0


def cmd_generate_signals(args: argparse.Namespace) -> int:
    from src.core import generate_signals

    session = _bootstrap_db()
    on_date = _parse_date(args)
    try:
        signals = generate_signals(on_date, session)
    except Exception as exc:
        logger.error("Signal generation failed: {exc}", exc=exc)
        return 1
    finally:
        session.close()

    for s in signals:
        print(f"  {s['ticker']}: {s['direction']} ({s['conviction']:.0%}) — {s['reasoning'][:80]}")
    print(f"\nLogged {len(signals)} signals")
    return 0


def cmd_score_signals(args: argparse.Namespace) -> int:
    from src.core import score_signals

    session = _bootstrap_db()
    on_date = _parse_date(args)
    try:
        scored = score_signals(session, on_date)
    finally:
        session.close()
    print(f"Scored {len(scored)} signals")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from src.core import render_dashboard

    session = _bootstrap_db()
    on_date = _parse_date(args)
    try:
        path = render_dashboard(session, on_date, args.output)
    finally:
        session.close()
    print(f"Dashboard written to {path}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    from src.core import prune_old_data

    session = _bootstrap_db()
    try:
        counts = prune_old_data(session, dry_run=args.dry_run)
    finally:
        session.close()

    verb = "would delete" if args.dry_run else "deleted"
    total = sum(counts.values())
    if not total:
        print(f"Nothing to prune ({verb} 0 rows).")
        return 0
    for table, n in counts.items():
        print(f"  {table}: {verb} {n} rows")
    print(f"Total: {verb} {total} rows")
    return 0


def run_all(args: argparse.Namespace) -> int:
    logger.info("full pipeline: not yet implemented")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sfe", description="Signal Fusion Engine CLI")
    parser.add_argument("--no-tui", action="store_true", help="Force CLI mode (skip TUI)")
    subs = parser.add_subparsers(dest="command")

    p_sent = subs.add_parser("run-sentiment", help="Run the sentiment engine")
    p_sent.add_argument("--ticker", help="Run for one ticker, bypassing the watchlist")
    p_sent.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_sent.set_defaults(func=run_sentiment)

    p_quant = subs.add_parser("run-quant", help="Run the quantitative engine")
    p_quant.add_argument("--ticker", help="Run for one ticker, bypassing the watchlist")
    p_quant.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_quant.set_defaults(func=run_quant)

    p_enrich = subs.add_parser("run-enrichment", help="Run enrichment signals")
    p_enrich.add_argument("--ticker", help="Run for one ticker, bypassing the watchlist")
    p_enrich.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_enrich.set_defaults(func=run_enrichment)

    p_meta = subs.add_parser("run-meta", help="Run the meta-synthesis layer")
    p_meta.add_argument("--ticker", help="Run for one ticker, bypassing the watchlist")
    p_meta.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_meta.set_defaults(func=run_meta)

    p_eb = subs.add_parser("run-earnings-brief", help="Generate an earnings context brief")
    p_eb.add_argument("--ticker", required=True, help="Ticker symbol (required)")
    p_eb.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_eb.add_argument(
        "--earnings-date",
        help="Override earnings date (YYYY-MM-DD); auto-detected from Finnhub if omitted",
    )
    p_eb.set_defaults(func=run_earnings_brief)

    p_lo = subs.add_parser("log-outcome", help="Log an earnings brief outcome")
    p_lo.add_argument("--ticker", required=True, help="Ticker symbol")
    p_lo.add_argument("--earnings-date", required=True, help="Earnings date (YYYY-MM-DD)")
    p_lo.add_argument(
        "--predicted-dir", required=True, choices=["bullish", "bearish", "neutral"],
        help="Predicted direction from the brief",
    )
    p_lo.add_argument("--conviction", required=True, help="Conviction score 0.0-1.0")
    p_lo.add_argument("--actual-eps-surp", help="Actual EPS surprise %%")
    p_lo.add_argument("--actual-rev-surp", help="Actual revenue surprise %%")
    p_lo.add_argument("--stock-move-1d", help="1-day stock move post-print %%")
    p_lo.add_argument(
        "--outcome", choices=["correct", "incorrect", "mixed", "pending"],
        help="Outcome assessment (default: pending)",
    )
    p_lo.add_argument("--brief-date", help="Date brief was published (default: today)")
    p_lo.add_argument("--notes", help="Free-form notes")
    p_lo.set_defaults(func=log_outcome)

    p_cal = subs.add_parser("earnings-calendar", help="Show upcoming earnings for watchlist")
    p_cal.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_cal.set_defaults(func=earnings_calendar)

    p_ls = subs.add_parser("log-signal", help="Log a directional signal for the experiment")
    p_ls.add_argument("--ticker", required=True, help="Ticker symbol")
    p_ls.add_argument(
        "--direction", required=True, choices=["bullish", "bearish", "neutral"],
        help="Directional call",
    )
    p_ls.add_argument("--conviction", required=True, help="Conviction score 0.0-1.0")
    p_ls.add_argument(
        "--dominant-component", required=True,
        choices=["sentiment", "quant", "enrichment", "convergence"],
        help="Which engine drove this call",
    )
    p_ls.add_argument("--reasoning", required=True, help="One-line reasoning for the call")
    p_ls.add_argument("--entry-price", help="Entry price at signal time")
    p_ls.add_argument("--as-of", help="Signal date (YYYY-MM-DD); defaults to today")
    p_ls.set_defaults(func=cmd_log_signal)

    p_rs = subs.add_parser("run-signals", help="Run all engines for the full watchlist")
    p_rs.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_rs.set_defaults(func=cmd_run_signals)

    p_gs = subs.add_parser("generate-signals", help="Run Claude meta-layer to generate directional signals")
    p_gs.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_gs.set_defaults(func=cmd_generate_signals)

    p_sc = subs.add_parser("score-signals", help="Score matured signals against outcomes")
    p_sc.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_sc.set_defaults(func=cmd_score_signals)

    p_dash = subs.add_parser("dashboard", help="Render the experiment dashboard HTML")
    p_dash.add_argument("--date", help="ISO date (YYYY-MM-DD); defaults to today")
    p_dash.add_argument("--output", help="Output file path (default: output/dashboard.html)")
    p_dash.set_defaults(func=cmd_dashboard)

    p_prune = subs.add_parser("prune", help="Delete engine rows past retention window")
    p_prune.add_argument(
        "--dry-run", action="store_true", help="Report counts without deleting"
    )
    p_prune.set_defaults(func=cmd_prune)

    subs.add_parser("run-all", help="Run the full pipeline end to end").set_defaults(func=run_all)
    return parser


def main() -> None:
    if len(sys.argv) == 1:
        try:
            from src.tui.app import SFEApp
            SFEApp().run()
            return
        except ImportError:
            pass

    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        if args.no_tui:
            parser.print_help()
        else:
            try:
                from src.tui.app import SFEApp
                SFEApp().run()
                return
            except ImportError:
                parser.print_help()
        return

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
