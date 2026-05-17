---
name: weekly-review
description: Weekly portfolio review for the SFE Signal Fusion Engine. Re-runs signals, scores open positions against kill rules, then redeploys fresh conviction. Use only when invoked explicitly with /weekly-review.
---

# Weekly review

The Monday cadence for the SFE portfolio. Broad scope: every open position is re-evaluated and fresh conviction may be deployed. For narrow catalyst-driven reviews, use `/event-review` instead.

## Before you start

Read `STRATEGY.md` in the repo root. It is authoritative for the mandate, position-sizing tiers, kill rules, sector cap, deployment band, and watchlist. Do not duplicate or restate its rules — apply them.

## The procedure

Follow these phases in order. Do not skip ahead — later phases depend on the state established by earlier ones.

### Phase 1 — Refresh data

1. Call `run_signals` with today's date. This re-runs sentiment, quant, and enrichment for the full watchlist and persists the results.

### Phase 2 — Assess current book

2. Call `get_portfolio_state` to load cash, equity, and open positions with unrealized P&L.
3. Call `score_signals` to score open SFE signals against realized prices.

### Phase 3 — Position-by-position review

For each open position, in order:

4. Check kill rules per STRATEGY.md § Risk framework:
   - Hard stop: -15% from entry → close mechanically.
   - Trailing stop: -10% from peak (binding once position is up ~5%+) → close.
   - Thesis-break: if the original reasoning is now false, close regardless of P&L.
5. If no stop fired, re-evaluate the thesis against the latest signals (`get_ticker_detail`). Decide: hold, resize, or close.

### Phase 4 — Look for new conviction

6. Call `get_signals` to scan the full watchlist for high-conviction setups not yet held.
7. For candidates, optionally go deep:
   - `investigate_sentiment(ticker, mode="deep", question=...)` when sentiment is the swing factor.
   - `get_quant_detail(ticker, depth="full")` for live OHLCV and fresh indicators.
   - `get_enrichment_detail(ticker, depth="full")` for full insider/analyst/earnings detail.
   Use deep mode selectively — these cost time and API calls.

### Phase 5 — Portfolio-level checks (do this BEFORE placing any trade)

Compute the *post-trade* state and verify against STRATEGY.md before executing:

8. Sector exposure ≤ 40% per sector.
9. Net long in the 70-85% band.
   - Below 70%: write down the reason (active de-gross, drawdown, breadth deterioration).
   - Above 85%: only acceptable with broad multi-name conviction.
10. Max single position ≤ 10%.
11. Min position ≥ 1% (anything smaller doesn't move the needle).
12. No leverage: gross long ≤ 100%.

If any check fails, revise the plan before executing.

### Phase 6 — Execute

13. Place trades in this order: closes first (frees cash), then resizes, then opens.
14. Sizing tiers per STRATEGY.md § Position sizing: High 7%, Medium 4%, Starter 2%.
15. Each trade's `reasoning` field must cite the specific signal (sentiment / quant / enrichment data point) that drove the decision. This is the requirement that makes the experiment evaluable.

### Phase 7 — Summary

16. Produce a brief markdown summary covering: trades made (with rationale), post-trade sector exposure, post-trade net long, any policy violations and the written justification.

## What this skill is not

- Not a day trader. Daily intraday moves are out of scope.
- Not a research report writer. Reasoning lives in the `reasoning` field on each trade.
- Not a guarantee of action. If signals are ambiguous, no-action is a valid outcome.

## Constraints

- Trade only tickers in `get_signals` (the watchlist).
- Cannot open a position you already hold — use `resize_position`, or close-then-open to reverse direction.
- All trades execute at current market price. No limit orders.
