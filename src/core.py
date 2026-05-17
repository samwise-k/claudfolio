"""Core orchestration functions for SFE engines.

Each function takes typed arguments and returns data. No argparse, no print,
no exit codes. Both the CLI (pipeline.py) and TUI (tui/) import from here.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session


def run_sentiment(
    tickers: list[str],
    on_date: Date,
    session: Session,
) -> list[dict[str, Any]]:
    from src.engines.sentiment.aggregator import aggregate, apply_history
    from src.storage.sentiment_repo import get_score_near, upsert_sentiment_daily

    results: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            payload = aggregate(ticker, on_date)
        except Exception as exc:
            logger.exception(f"{ticker}: aggregate failed: {exc}")
            results.append({"ticker": ticker, "error": str(exc)})
            continue
        prior_score = get_score_near(
            session, ticker, on_date - timedelta(days=7), window_days=7
        )
        apply_history(payload, prior_score)
        upsert_sentiment_daily(session, payload)
        results.append(payload)
        logger.info(
            "{ticker} {date}: score={score} sources={sources}",
            ticker=ticker,
            date=on_date,
            score=payload["sentiment_score"],
            sources=list(payload["source_breakdown"].keys()),
        )
    return results


def run_quant(
    entries: list[dict[str, Any]],
    as_of: Date,
    session: Session,
) -> list[dict[str, Any]]:
    from src.engines.quantitative.aggregator import aggregate
    from src.storage.quant_repo import upsert_quant_daily

    results: list[dict[str, Any]] = []
    for entry in entries:
        ticker = entry["ticker"]
        try:
            payload = aggregate(ticker, as_of, sector=entry.get("sector"))
        except Exception as exc:
            logger.exception(f"{ticker}: quant aggregate failed: {exc}")
            results.append({"ticker": ticker, "error": str(exc)})
            continue
        upsert_quant_daily(session, payload)
        results.append(payload)
        logger.info(
            "{ticker} {date}: close={close} rsi={rsi} health={health}",
            ticker=ticker,
            date=as_of,
            close=payload["close"],
            rsi=payload["rsi_14"],
            health=payload["health_score"],
        )
    return results


def run_enrichment(
    tickers: list[str],
    on_date: Date,
    session: Session,
) -> list[dict[str, Any]]:
    from src.engines.enrichment.aggregator import aggregate
    from src.storage.enrichment_repo import upsert_enrichment_daily

    results: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            payload = aggregate(ticker, on_date)
        except Exception as exc:
            logger.exception(f"{ticker}: enrichment aggregate failed: {exc}")
            results.append({"ticker": ticker, "error": str(exc)})
            continue
        upsert_enrichment_daily(session, payload)
        results.append(payload)
        next_er = payload["next_earnings"]
        logger.info(
            "{ticker} {date}: insider={insider} next_earn={er} analyst={analyst}",
            ticker=ticker,
            date=on_date,
            insider=payload["insider_trades"]["net_insider_sentiment"],
            er=next_er["date"] if next_er else "—",
            analyst=payload["analyst_activity"]["trend"],
        )
    return results


def run_meta(
    tickers: list[str],
    on_date: Date,
    session: Session,
) -> str:
    from src.meta.formatter import format_briefing
    from src.meta.llm_client import generate_briefing
    from src.meta.payload_builder import build_payload

    payload = build_payload(session, on_date, tickers=tickers)
    logger.info("meta: calling Claude for {n} ticker(s) on {d}", n=len(tickers), d=on_date)
    raw = generate_briefing(payload)
    return format_briefing(raw, on_date=on_date)


def run_earnings_brief(
    ticker: str,
    on_date: Date,
    session: Session,
    earnings_date: Date | None = None,
) -> str:
    from src.engines.earnings.payload_builder import build_earnings_payload
    from src.meta.formatter import format_briefing
    from src.meta.llm_client import generate_briefing

    if earnings_date is None:
        from src.engines.enrichment.event_calendar import fetch_earnings, summarize

        try:
            events = fetch_earnings(ticker, on_date)
            summary = summarize(events, on_date)
            if summary["next_earnings"]:
                earnings_date = Date.fromisoformat(summary["next_earnings"]["date"])
        except Exception as exc:
            logger.warning(f"{ticker}: earnings date lookup failed: {exc}")

    if earnings_date is None:
        raise ValueError(
            f"{ticker}: no upcoming earnings found. Pass earnings_date explicitly."
        )

    payload = build_earnings_payload(session, ticker, on_date, earnings_date)
    logger.info(
        "earnings brief: {ticker} reporting {er_date} ({days}d away)",
        ticker=ticker,
        er_date=earnings_date,
        days=(earnings_date - on_date).days,
    )

    prompt_path = Path(__file__).resolve().parent / "meta" / "prompts" / "earnings_briefing.txt"
    system_prompt = prompt_path.read_text()

    raw = generate_briefing(payload, system_prompt=system_prompt)
    return format_briefing(raw, on_date=on_date, disclaimer=True)


def earnings_calendar(on_date: Date) -> list[dict[str, Any]]:
    from src.config import load_watchlist
    from src.engines.earnings.consensus import fetch_estimates
    from src.engines.enrichment.event_calendar import fetch_earnings, summarize

    watchlist = load_watchlist()
    tickers = [t["ticker"] for t in watchlist]

    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            events = fetch_earnings(ticker, on_date, lookahead_days=14)
            summary = summarize(events, on_date)
            if not summary["next_earnings"]:
                continue
            ne = summary["next_earnings"]

            est: dict[str, Any] = {}
            try:
                est = fetch_estimates(ticker)
            except Exception:
                pass

            rows.append({
                "ticker": ticker,
                "date": ne["date"],
                "days_until": ne["days_until"],
                "consensus_eps": est.get("eps_estimate"),
                "prior_surprise": ne.get("estimate_eps"),
            })
        except Exception as exc:
            logger.warning(f"{ticker}: calendar lookup failed: {exc}")

    rows.sort(key=lambda r: r["date"])
    return rows


def log_outcome(data: dict[str, Any], session: Session) -> None:
    from src.storage.earnings_repo import upsert_outcome

    upsert_outcome(session, data)
    logger.info(
        "logged outcome: {ticker} {er_date} → {outcome}",
        ticker=data["ticker"],
        er_date=data["earnings_date"],
        outcome=data["outcome"],
    )


def log_signal(data: dict[str, Any], session: Session) -> None:
    from src.storage.signal_repo import upsert_signal_daily

    upsert_signal_daily(session, data)
    logger.info(
        "logged signal: {ticker} {date} → {dir} ({conv:.0%})",
        ticker=data["ticker"],
        date=data["as_of"],
        dir=data["direction"],
        conv=data["conviction"],
    )


def run_signals(
    on_date: Date,
    session: Session,
) -> dict[str, list[dict[str, Any]]]:
    """Run all 3 engines for the full watchlist. No Claude call."""
    from src.config import load_watchlist

    watchlist = load_watchlist()
    tickers = [t["ticker"] for t in watchlist]
    if not tickers:
        logger.error("watchlist is empty — fill config/watchlist.yaml")
        return {"sentiment": [], "quant": [], "enrichment": []}

    logger.info("run-signals: {n} tickers on {d}", n=len(tickers), d=on_date)

    sentiment = run_sentiment(tickers, on_date, session)
    quant = run_quant(watchlist, on_date, session)
    enrichment = run_enrichment(tickers, on_date, session)

    logger.info(
        "run-signals complete: {s} sentiment, {q} quant, {e} enrichment rows",
        s=len([r for r in sentiment if "error" not in r]),
        q=len([r for r in quant if "error" not in r]),
        e=len([r for r in enrichment if "error" not in r]),
    )
    return {"sentiment": sentiment, "quant": quant, "enrichment": enrichment}


def generate_signals(
    on_date: Date,
    session: Session,
) -> list[dict[str, Any]]:
    """Read engine outputs, call Claude to reason about each ticker, log signals."""
    import json

    from src.meta.llm_client import generate_briefing
    from src.meta.payload_builder import build_payload

    payload = build_payload(session, on_date)
    tickers_with_data = [
        t for t in payload["tickers"]
        if t.get("sentiment") or t.get("quant") or t.get("enrichment")
    ]

    if not tickers_with_data:
        logger.warning("no engine data found for any ticker on {d}", d=on_date)
        return []

    prompt_path = Path(__file__).resolve().parent / "meta" / "prompts" / "signal_generation.txt"
    system_prompt = prompt_path.read_text()

    logger.info(
        "generate-signals: calling Claude for {n} tickers on {d}",
        n=len(tickers_with_data),
        d=on_date,
    )
    raw = generate_briefing(payload, system_prompt=system_prompt, model="claude-sonnet-4-6")

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

    signals = json.loads(cleaned)

    logged: list[dict[str, Any]] = []
    for sig in signals:
        direction = sig.get("direction", "neutral")
        if direction not in ("bullish", "bearish", "neutral"):
            direction = "neutral"
        dominant = sig.get("dominant_component", "convergence")
        if dominant not in ("sentiment", "quant", "enrichment", "convergence"):
            dominant = "convergence"

        data = {
            "ticker": sig["ticker"].upper(),
            "as_of": on_date,
            "direction": direction,
            "conviction": max(0.0, min(1.0, float(sig.get("conviction", 0.5)))),
            "dominant_component": dominant,
            "reasoning": sig.get("reasoning", ""),
            "entry_price": sig.get("entry_price"),
            "signal_components": {},
        }
        log_signal(data, session)
        logged.append(data)

    return logged


def score_signals(
    session: Session,
    on_date: Date | None = None,
) -> list[dict[str, Any]]:
    from src.tracking.scorer import compute_stats, score_all

    on_date = on_date or Date.today()
    scored = score_all(session, on_date)
    stats = compute_stats(scored)

    logger.info(
        "scoring complete: {n} signals scored, 5d EV={ev}",
        n=stats["total_signals"],
        ev=stats["by_horizon"].get("5d", {}).get("ev", "n/a"),
    )
    return scored


def render_dashboard(
    session: Session,
    on_date: Date | None = None,
    output_path: str | None = None,
) -> str:
    from pathlib import Path

    from src.tracking.dashboard import render

    path = Path(output_path) if output_path else None
    result = render(session, on_date, path)
    return str(result)


def prune_old_data(
    session: Session,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Prune engine rows past their retention window. Decision tables untouched."""
    from src.storage.retention import prune

    counts = prune(session, dry_run=dry_run)
    logger.info(
        "prune ({mode}): {counts}",
        mode="dry-run" if dry_run else "applied",
        counts=counts,
    )
    return counts


def get_ticker_summary(
    ticker: str,
    on_date: Date,
    session: Session,
) -> dict[str, Any]:
    """Quick-look: pull latest stored data for a ticker. No API calls."""
    from src.meta.payload_builder import _enrichment_view, _latest, _quant_view, _sentiment_view
    from src.storage.earnings_repo import get_latest_outcome
    from src.storage.models import EnrichmentDaily, QuantDaily, SentimentDaily

    sentiment = _sentiment_view(_latest(session, SentimentDaily, ticker, on_date))
    quant = _quant_view(_latest(session, QuantDaily, ticker, on_date))
    enrichment = _enrichment_view(_latest(session, EnrichmentDaily, ticker, on_date))
    outcome = get_latest_outcome(session, ticker)

    return {
        "ticker": ticker,
        "sentiment": sentiment,
        "quant": quant,
        "enrichment": enrichment,
        "latest_outcome": {
            "predicted_dir": outcome.predicted_dir,
            "conviction": outcome.conviction,
            "outcome": outcome.outcome,
            "earnings_date": outcome.earnings_date.isoformat(),
        } if outcome else None,
    }
