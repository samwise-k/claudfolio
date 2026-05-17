# Claudfolio

Agentic portfolio management experiment. Sentiment, quantitative, and enrichment engines feed a Claude-powered agent that autonomously manages a simulated portfolio — making allocation decisions, sizing positions, and reacting to new signals daily.

**Research question:** Can an LLM agent, given structured market signals and full discretion over a simulated book, manage a portfolio at a level of competence comparable to a human?

The agent operates under the strategy framework in [STRATEGY.md](./STRATEGY.md) — mandate, benchmark (QQQ), net-long band (70-85%), three-tier position sizing, kill rules (-15% hard / -10% trailing / thesis-break, -7% defensive / -10% kill at the portfolio level), and weekly review cadence. Update STRATEGY.md when the strategy evolves; do not let practice drift from documentation.

```
> /weekly-review

[skill] Loading STRATEGY.md…
[mcp]   run_signals(2026-05-04) — 21 tickers refreshed
[mcp]   score_signals — 8 open positions scored
[agent] NVDA long 5% — thesis intact, sentiment 0.52, hold
[agent] JPM long 8% → 4% — quant health deteriorating, trim per
        STRATEGY.md § Position sizing
[agent] Opening AVGO long 4% — medium-tier conviction setup, insider buys
[agent] Portfolio: 9 positions, $98,420 equity, +1.8% since inception
```

## Quick start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), a free [Finnhub API key](https://finnhub.io/register), and an [Anthropic API key](https://console.anthropic.com/).

```bash
# 1. Clone and install
git clone https://github.com/chonneyhft/claudfolio.git
cd claudfolio
uv sync                     # pulls dev + llm groups by default
uv sync --group quant       # yfinance + scipy + sklearn/xgboost
uv sync --group dashboard   # streamlit + plotly (agent dashboard)

# 2. Configure API keys
cp .env.example .env
# Edit .env and set at minimum:
#   FINNHUB_KEY=your_finnhub_key
#   ANTHROPIC_API_KEY=your_anthropic_key
#   SEC_EDGAR_USER_AGENT=sfe/0.1 (your-email@example.com)

# 3. Run all signal engines for the watchlist
uv run sfe run-signals

# 4. Drive the engines + portfolio from any MCP-capable client (recommended)
#    Claude Code auto-discovers .mcp.json at the repo root.
uv run sfe-mcp        # smoke test the server (Ctrl-C to exit)

# 5. View the dashboard
uv run sfe-dashboard
```

## MCP server (primary interface)

The Signal Fusion Engine exposes its capabilities as an MCP (Model Context
Protocol) server over stdio. Any MCP-capable agent — Claude Code, Claude
Desktop, or a custom client — can drive the engines, manage the simulated
portfolio, and query tracking history through tool calls.

Tools exposed:

- **Engines:** `sentiment_aggregate`, `quant_aggregate`, `enrichment_aggregate`, `earnings_calendar`, `run_signals`
- **Portfolio:** `get_portfolio_state`, `get_signals`, `get_ticker_detail`, `open_position`, `close_position`, `resize_position`, `get_trade_history`, `investigate_sentiment`, `get_quant_detail`, `get_enrichment_detail`
- **Tracking:** `score_signals`, `get_ticker_summary`, `list_recent_signals`, `get_latest_outcome`

The repo ships a `.mcp.json` file so Claude Code picks the server up
automatically when the project directory is opened. Approve it once via
`/mcp` and the tools become available in-session.

## Procedural skills

Two Claude Code skills live in `.claude/skills/` and drive the agent through
the MCP tools:

- **`/weekly-review`** — Monday cadence. Refresh signals, score open
  positions against kill rules, redeploy fresh conviction. Fully prescriptive
  recipe with portfolio-level guardrails before any trade fires.
- **`/event-review`** — Catalyst-driven, narrow. Use after earnings prints,
  Fed/CPI days, single-name news, or stop hits. Does not redeploy fresh
  conviction.

Both skills defer to [STRATEGY.md](./STRATEGY.md) for policy (mandate,
sizing tiers, kill rules, sector cap). Edit STRATEGY.md and both skills pick
up the change automatically.

## What it does

SFE pulls data from multiple free sources, scores it, and hands a structured payload to a Claude agent that manages a simulated portfolio:

```
Finnhub (news, insider trades, analyst revisions, earnings calendar, consensus)
EDGAR (SEC filings: 8-K, 10-K, 10-Q)                                          → Sentiment
finlight (financial news)                                                         + Quant
yfinance (OHLCV, technicals, options chains)                                      + Enrichment
                                                                                     ↓
                                                                            Claude Code + MCP
                                                                              (skill-driven loop)
                                                                                     ↓
                                                                          Simulated portfolio mgmt
                                                                         (positions, P&L, decisions)
```

**Agentic portfolio management** is the primary workflow. Each day (pre-market), the agent:
1. Reads the latest engine outputs for every watchlist ticker
2. Reviews current portfolio state (positions, cash, P&L)
3. Makes autonomous allocation decisions — open, close, resize positions
4. Logs its reasoning for every trade decision

The agent has near-full discretion. There are minimal guardrails — the experiment tests whether Claude can manage a book competently, not whether it can follow rules.

**Earnings briefs** remain available as a secondary workflow for per-ticker, pre-earnings context.

## CLI commands

### Agent (primary workflow)

The agent runs inside Claude Code against the MCP server. There is no
standalone `sfe run-agent` command — use the `/weekly-review` or
`/event-review` skills described above.

| Command | What it does |
|---------|-------------|
| `sfe run-signals` | Run all 3 engines for the full watchlist (no Claude call) |
| `sfe-mcp` | Smoke test the MCP server (Ctrl-C to exit) |
| `sfe-dashboard` | Launch the Streamlit portfolio dashboard |

### Engines

| Command | What it does |
|---------|-------------|
| `sfe run-sentiment [--ticker SYM]` | Run sentiment engine (Finnhub + EDGAR + finlight) |
| `sfe run-quant [--ticker SYM]` | Run quantitative engine (technicals, sector-relative) |
| `sfe run-enrichment [--ticker SYM]` | Run enrichment engine (insider, analyst, calendar) |

### Earnings briefs (secondary)

| Command | What it does |
|---------|-------------|
| `sfe earnings-calendar` | Show watchlist tickers reporting in the next 14 days |
| `sfe run-earnings-brief --ticker SYM` | Generate a per-ticker pre-earnings context brief |
| `sfe run-meta [--ticker SYM]` | Generate a daily watchlist briefing via Claude |

All commands accept `--date YYYY-MM-DD` (defaults to today). Commands without `--ticker` run against the full watchlist in `config/watchlist.yaml`.

## API keys

| Key | Required for | Free tier |
|-----|-------------|-----------|
| `FINNHUB_KEY` | Sentiment, enrichment, earnings | Yes (60 calls/min) |
| `ANTHROPIC_API_KEY` | MCP-driven agent (Claude Code), `run-meta`, `run-earnings-brief` | Pay-per-use |
| `SEC_EDGAR_USER_AGENT` | SEC filing fetches | Keyless (requires email in UA) |
| `FINLIGHT_KEY` | Additional news sentiment | Yes |

Set these in `.env` (gitignored). `FINNHUB_KEY` and `ANTHROPIC_API_KEY` are required.

## Setup (detailed)

```bash
# Core only (sentiment + storage + CLI)
uv sync

# Optional dependency groups, installed on demand:
uv sync --group sentiment-ml   # transformers + torch (FinBERT scorer)
uv sync --group quant          # scikit-learn, xgboost, yfinance
uv sync --group llm            # anthropic SDK (Claude agent + briefings)
uv sync --group dashboard      # streamlit + plotly (agent dashboard)
uv sync --group api            # fastapi + uvicorn (HTTP layer)
```

### Watchlist

Edit `config/watchlist.yaml` to set your tickers:

```yaml
tickers:
  - ticker: NVDA
    sector: technology
  - ticker: MSFT
    sector: technology
  - ticker: JPM
    sector: financials
```

The `sector` field maps to SPDR ETFs for sector-relative returns (XLK, XLF, etc.).

### Frontend (optional)

```bash
cd frontend
npm install
npm run dev       # Vite dev server on :5173, proxies /api to 127.0.0.1:8000
# Backend must be running in another shell: uv run sfe-api
```

### Tests

```bash
uv run pytest               # tests across engines, API, storage, and frontend smoke
```

## Layout

```
config/      # watchlist.yaml, sources.yaml
src/
  agent/           # MCP tool implementations, sub-agents, Streamlit dashboard
  mcp_server/      # MCP stdio server (`sfe-mcp` entry) — primary agent interface
  engines/
    sentiment/     # news, SEC → score → aggregate
    quantitative/  # OHLCV, technicals, sector-relative, health score
    enrichment/    # insider trades, analyst revisions, earnings calendar
    earnings/      # consensus, beat/miss, options-implied, earnings payload
  meta/            # payload builder, Claude client, formatter, prompt templates
  api/             # FastAPI app, Pydantic schemas, `sfe-api` entry
  storage/         # SQLAlchemy models, repos, session (portfolio, positions, trades)
  tracking/        # signal scorer + experiment dashboard
  tui/             # Textual TUI (optional)
  delivery/        # email + Slack delivery helpers
  pipeline.py      # CLI entry point (all `sfe` commands)
frontend/    # React + Vite + TS dashboard (Briefing, Watchlist, TickerDetail, Portfolio, Trades)
tests/       # ~300 tests
data/        # raw/ + processed/ (gitignored)
STRATEGY.md  # authoritative trading framework — read before trading
AGENTS.md    # per-repo agent-skill configuration
```

## Disclaimer

This is a personal research tool, not a financial product. It does not provide investment advice. The author may hold positions in securities discussed. Past framework outputs do not predict future results. See the auto-appended disclaimer on every earnings brief for the full text.

## Known limitations

- **EDGAR signal quality.** 10-K/10-Q filings are scored uniformly. Risk-factor boilerplate dilutes signal from MD&A sections. Targeting Item 7 specifically would improve this but requires section-anchor parsing that varies across filings.
- **Options data coverage.** yfinance options chain data is inconsistent across tickers. Some names return no expirations at all. The earnings brief gracefully omits the implied-move section when this happens.
- **Sentiment scorer.** FinBERT is the default for finance-domain accuracy. Requires `uv sync --group sentiment-ml` (torch + transformers). Falls back to TextBlob with a warning if those deps are missing. Force TextBlob with `SENTIMENT_SCORER=textblob` for lightweight dev/CI runs.
