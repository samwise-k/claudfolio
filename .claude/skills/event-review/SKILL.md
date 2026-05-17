---
name: event-review
description: Catalyst-driven portfolio review for the SFE Signal Fusion Engine. Narrow scope — evaluates only positions touched by a specific event. Does not redeploy fresh conviction. Use only when invoked explicitly with /event-review.
---

# Event review

A narrow review fired by a specific catalyst since the last weekly review: an earnings print, Fed day, CPI release, individual stop hit, or single-name news. For the broad Monday cadence, use `/weekly-review`.

## Before you start

Read `STRATEGY.md` in the repo root, with attention to § Risk framework — kill rules and the operating notes on cited reasoning.

## The procedure

1. **Identify the catalyst.** What happened, and which positions does it touch? If the catalyst is portfolio-wide (Fed, CPI), the affected set may be all positions; if it is single-name (earnings, news, stop hit), it is just that ticker.

2. **For each affected position, refresh the picture.** Use the most relevant deep tool for the catalyst type:
   - Narrative-driven catalysts (news, regulatory action, sentiment shift): `investigate_sentiment(ticker, mode="deep", question=...)`.
   - Price-action catalysts (stop hit, gap, breakdown): `get_quant_detail(ticker, depth="full")`.
   - Earnings or insider-trade catalysts: `get_enrichment_detail(ticker, depth="full")`.
   `get_ticker_detail(ticker)` first if you just need the current cached state.

3. **Re-evaluate the thesis.** Decide one of:
   - **Intact** → hold.
   - **Wounded** → resize down.
   - **Broken** → close regardless of P&L (thesis-break stop per STRATEGY.md).
   Also check the mechanical kill rules: -15% from entry, -10% from peak.

4. **Do not redeploy fresh conviction.** Redeployment is a weekly-review responsibility. Event reviews are deliberately narrow — they exist to act fast on new information, not to rebalance the book. If you find yourself wanting to open new positions, stop and schedule a weekly review instead.

5. **Execute trades.** Closes first, then resizes. Each trade's `reasoning` field must cite both the catalyst and the specific signal that drove the decision.

6. **Summary.** Brief markdown: catalyst, positions touched, trades made, current state of those positions.

## Constraints

- Same trading constraints as the weekly review: watchlist-only, no double-open, market-price execution.
- This skill must not trigger broad rebalancing. If a position-level review reveals a portfolio-level problem (e.g., sector concentration after a close), note it in the summary and address it at the next weekly review — do not redeploy here.
