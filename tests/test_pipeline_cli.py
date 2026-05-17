"""Smoke tests for the ``sfe`` CLI in src/pipeline.py.

Each test invokes the subcommand handler directly with a parsed Namespace and
verifies (a) the exit code, (b) that DB rows land where expected, (c) that
errors are bucketed into a non-zero exit. Engines and the LLM client are
monkeypatched at the seam so no external IO happens.
"""

from __future__ import annotations

import argparse
from datetime import date as Date

import pytest
from sqlalchemy import select

from src import core, pipeline
from src.storage.models import (
    BriefingDaily,
    EarningsBriefOutcome,
    EnrichmentDaily,
    QuantDaily,
    SentimentDaily,
    SignalDaily,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the CLI at a throwaway SQLite file so it never touches data/sfe.db."""
    db_path = tmp_path / "sfe.db"
    monkeypatch.setenv("SFE_DB_URL", f"sqlite:///{db_path}")

    # Reset the lru_cache on get_engine/_session_factory so the env var is read.
    from src.storage import db

    db.get_engine.cache_clear()
    db._session_factory.cache_clear()
    yield
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


@pytest.fixture()
def open_session():
    """Return a callable that opens a fresh session against the env DB."""
    from src.storage.db import get_session

    return get_session


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


# ─────────────────────────── parser ─────────────────────────── #


class TestBuildParser:
    @pytest.mark.parametrize("subcommand", [
        "run-sentiment", "run-quant", "run-enrichment", "run-meta",
        "earnings-calendar", "log-signal", "run-signals",
        "generate-signals", "score-signals", "dashboard", "run-all",
    ])
    def test_subcommand_parses(self, subcommand):
        parser = pipeline.build_parser()
        # Minimal valid args per subcommand; required args provided where needed.
        extra = []
        if subcommand == "log-signal":
            extra = [
                "--ticker", "NVDA", "--direction", "bullish",
                "--conviction", "0.7", "--dominant-component", "convergence",
                "--reasoning", "test",
            ]
        args = parser.parse_args([subcommand, *extra])
        assert args.command == subcommand


# ─────────────────────────── run-sentiment ─────────────────────────── #


class TestRunSentimentCli:
    def test_empty_watchlist_returns_1(self, monkeypatch):
        from src import config as cfg

        monkeypatch.setattr(cfg, "load_watchlist", lambda: [])
        rc = pipeline.run_sentiment(_ns(date=None, ticker=None))
        assert rc == 1

    def test_writes_row_for_explicit_ticker(self, monkeypatch, open_session):
        from src.engines.sentiment import aggregator as agg

        monkeypatch.setattr(agg, "aggregate", lambda t, d: {
            "ticker": t, "date": d.isoformat(),
            "sentiment_score": 0.6, "sentiment_direction": "improving",
            "source_breakdown": {}, "key_topics": [], "notable_headlines": [],
        })

        rc = pipeline.run_sentiment(_ns(date="2026-04-17", ticker="NVDA"))
        assert rc == 0

        with open_session() as s:
            rows = s.execute(select(SentimentDaily)).scalars().all()
            assert len(rows) == 1
            assert rows[0].ticker == "NVDA"


# ─────────────────────────── run-quant ─────────────────────────── #


class TestRunQuantCli:
    def test_writes_row(self, monkeypatch, open_session):
        from src import config as cfg
        from src.engines.quantitative import aggregator as agg

        monkeypatch.setattr(cfg, "load_watchlist", lambda: [{"ticker": "NVDA", "sector": "Tech"}])
        monkeypatch.setattr(agg, "aggregate", lambda t, d, sector=None: {
            "ticker": t, "date": d.isoformat(),
            "close": 100.0, "change_1d": 0.5, "change_5d": 1.0, "change_20d": 2.0,
            "rsi_14": 55.0, "above_50sma": True, "above_200sma": True,
            "macd_signal": "neutral", "volume_vs_20d_avg": 1.1,
            "sector_etf": "XLK", "relative_return_5d": 0.1,
            "health_score": "strong",
        })

        rc = pipeline.run_quant(_ns(date="2026-04-17", ticker=None))
        assert rc == 0

        with open_session() as s:
            assert s.execute(select(QuantDaily)).scalar_one().ticker == "NVDA"


# ─────────────────────────── run-enrichment ─────────────────────────── #


class TestRunEnrichmentCli:
    def test_writes_row(self, monkeypatch, open_session):
        from src import config as cfg
        from src.engines.enrichment import aggregator as agg

        monkeypatch.setattr(cfg, "load_watchlist", lambda: [{"ticker": "NVDA"}])
        monkeypatch.setattr(agg, "aggregate", lambda t, d: {
            "ticker": t, "date": d.isoformat(),
            "insider_trades": {"net_insider_sentiment": "bullish"},
            "next_earnings": None, "upcoming_events": [],
            "analyst_activity": {"trend": "stable"},
        })

        rc = pipeline.run_enrichment(_ns(date="2026-04-17", ticker=None))
        assert rc == 0

        with open_session() as s:
            assert s.execute(select(EnrichmentDaily)).scalar_one().ticker == "NVDA"


# ─────────────────────────── run-meta ─────────────────────────── #


class TestRunMetaCli:
    def test_llm_failure_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "run_meta", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("oops")))

        rc = pipeline.run_meta(_ns(date="2026-04-17", ticker="NVDA"))
        assert rc == 1

    def test_success_prints_briefing(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "run_meta", lambda tickers, on_date, session: "## Briefing OK")

        rc = pipeline.run_meta(_ns(date="2026-04-17", ticker="NVDA"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Briefing OK" in out


# ─────────────────────────── log-outcome ─────────────────────────── #


class TestLogOutcomeCli:
    def test_persists(self, open_session):
        rc = pipeline.log_outcome(_ns(
            ticker="NVDA",
            earnings_date="2026-05-22",
            brief_date="2026-05-01",
            predicted_dir="bullish",
            conviction="0.8",
            actual_eps_surp=None,
            actual_rev_surp=None,
            stock_move_1d=None,
            outcome="pending",
            notes=None,
        ))
        assert rc == 0
        with open_session() as s:
            row = s.execute(select(EarningsBriefOutcome)).scalar_one()
            assert row.predicted_dir == "bullish"
            assert row.conviction == 0.8


# ─────────────────────────── log-signal ─────────────────────────── #


class TestLogSignalCli:
    def test_persists(self, open_session):
        rc = pipeline.cmd_log_signal(_ns(
            ticker="NVDA",
            direction="bullish",
            conviction="0.7",
            dominant_component="convergence",
            reasoning="three-engine convergence",
            entry_price="950.0",
            as_of="2026-05-01",
        ))
        assert rc == 0
        with open_session() as s:
            row = s.execute(select(SignalDaily)).scalar_one()
            assert row.entry_price == 950.0


# ─────────────────────────── earnings-calendar ─────────────────────────── #


class TestEarningsCalendarCli:
    def test_empty_prints_message(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "earnings_calendar", lambda d: [])
        rc = pipeline.earnings_calendar(_ns(date=None))
        assert rc == 0
        assert "No watchlist tickers" in capsys.readouterr().out

    def test_rows_print_table(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "earnings_calendar", lambda d: [
            {"ticker": "NVDA", "date": "2026-05-22", "days_until": 21,
             "consensus_eps": 0.99, "prior_surprise": None},
        ])
        rc = pipeline.earnings_calendar(_ns(date=None))
        out = capsys.readouterr().out
        assert rc == 0
        assert "NVDA" in out
        assert "$0.99" in out


# ─────────────────────────── run-signals ─────────────────────────── #


class TestRunSignalsCli:
    def test_all_errors_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "run_signals", lambda d, s: {
            "sentiment": [{"ticker": "X", "error": "boom"}],
            "quant": [{"ticker": "X", "error": "boom"}],
            "enrichment": [{"ticker": "X", "error": "boom"}],
        })
        rc = pipeline.cmd_run_signals(_ns(date=None))
        assert rc == 1
        assert "0 successful" in capsys.readouterr().out

    def test_partial_success_returns_0(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "run_signals", lambda d, s: {
            "sentiment": [{"ticker": "X"}],
            "quant": [{"ticker": "X", "error": "boom"}],
            "enrichment": [],
        })
        rc = pipeline.cmd_run_signals(_ns(date=None))
        assert rc == 0


# ─────────────────────────── generate-signals ─────────────────────────── #


class TestGenerateSignalsCli:
    def test_failure_returns_1(self, monkeypatch):
        monkeypatch.setattr(core, "generate_signals", lambda d, s: (_ for _ in ()).throw(RuntimeError("llm")))
        rc = pipeline.cmd_generate_signals(_ns(date=None))
        assert rc == 1

    def test_prints_one_line_per_signal(self, monkeypatch, capsys):
        monkeypatch.setattr(core, "generate_signals", lambda d, s: [
            {"ticker": "NVDA", "direction": "bullish", "conviction": 0.8, "reasoning": "good"},
        ])
        rc = pipeline.cmd_generate_signals(_ns(date=None))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Logged 1 signals" in out


# ─────────────────────────── run-all ─────────────────────────── #


class TestRunAll:
    def test_run_all_is_a_noop_zero(self):
        assert pipeline.run_all(_ns()) == 0
