---
name: the-sniper
description: >-
  The Sniper, the Buy Zone Finder. Finds where a stock has support (price bands where buyers
  stepped in before) by rules, not by eye: weekly swing lows, old ceilings
  that became floors, volume shelves and the 200-day and 200-week averages,
  merged into zones scored 0-100. For each zone: its record (held or broke
  each time it was tested), the odds price even gets there in 6 and 12
  months, and, with /the-mind-reader, the growth the market would assume at that
  price. A zone that is strong on the chart and cheap on valuation is a
  DOUBLE FLOOR. Use when the user asks "where should I buy", "good entry",
  "entry level", "support and resistance", "buy the dip where", "what price
  to start buying", "buy zone" or "where's the floor".
argument-hint: "TICKER"
---

# The Sniper: Buy Zone Finder

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python` (Windows) or
`python3` (macOS/Linux); if one is not found, use the other. Run from the
user's project folder.

If neither `python` nor `python3` works (or Windows opens the Microsoft
Store), Python is missing: follow the /investor-setup skill, which explains
how to install it. If a script says the SEC needs a contact, ask the user for
their name and email and run
`python "${CLAUDE_SKILL_DIR}/investkit.py" save-contact "Their Name" their@email.com`:
it is saved once and works in every folder.

```bash
python "${CLAUDE_SKILL_DIR}/buyzones.py" NVDA
python "${CLAUDE_SKILL_DIR}/buyzones.py" KO --max-drop 0.30 --zones 4
python "${CLAUDE_SKILL_DIR}/buyzones.py" TSM --min-score 40
```

Run `/the-mind-reader TICKER` first for the valuation half (growth priced in at each
zone, the valuation floor, double floors). Without it the chart half still works.

## How it works (say this plainly when asked)

- **Levels**, from 5 years of weekly bars and 2 years of daily bars:
  swing lows (lowest of 4 weeks either side), swing highs the price later
  held above for 4 weeks ("old ceiling, now floor"), volume shelves (price
  bands where the most shares traded, each day's volume spread over its
  range), and the 200-day and 200-week averages. Recent turning points count
  more (half-life 1.5 years).
- **Zones**: levels within half the stock's typical weekly range are merged
  into one band.
- **Strength 0-100**: turning points up to 40, volume up to 25, each
  moving average 10, old-ceiling-now-floor 15, and up to +/-10 for its record.
- **Record**: every time price came down into the zone after it formed:
  held = closed 5% above it within 60 trading days, broke = closed 2% below.
- **Odds of getting there**: 10,000 paths built from the stock's own last 2
  years of daily moves (in blocks), centred on the same 9% a year as
  /the-mind-reader; plus how often a fall that deep within a year really happened
  in the last 5 years. The two should roughly agree; if not, say so.
- **Valuation**: the growth the price would assume at each zone; the
  *valuation floor* is the price where the market would only be assuming
  what a top-quarter company its size achieves over a decade.
  **Double floor** = zone strength 50+ and at or under that floor.

## What to tell the user

The ladder (zone, distance below, strength, evidence, record, odds, growth
priced in there), any double floor, the valuation floor, the ceilings above,
and the patience trade-off: a deeper zone is cheaper but less likely to be
reached. The chart image for video is `investor/charts/<TICKER>-buy-zones.svg`.

If no zone reaches the valuation floor, say it plainly: the chart shows
where buyers stepped in before, but on valuation none of these prices is
cheap.

## Show the report

Every run also writes a finished report page, `investor/reports/<TICKER>-buy-zones.html`: the
headline, the key figures, an interactive chart with hover details, the
tables, the method and the sources, in light and dark, readable on a phone.
It is the main thing to show the user.

- If you have a tool that publishes Artifacts (pages on claude.ai), publish
  that file as an Artifact: a one-sentence `description`, `icon` "chart",
  and give the user the link. Publishing it again later updates the same page.
- Otherwise tell the user the file path and that it opens in any web browser.

Keep the chat answer short (the headline, the two or three numbers that
matter, the warnings) and let the report carry the detail.

## Honesty rules

- Use only this skill's tools and your general knowledge. Do not search for
  or read the user's own files, notes or earlier research unless they hand
  you one by name.
- Support is where buyers stepped in before, not a promise they will again.
  Zones are fitted to the same history their record is measured on, so the
  record flatters them a little (the output says so).
- A zone is a band, not a line, and prices can gap straight through it.
- Splits are adjusted; dividends are not (levels are traded prices).
- Never say "buy at X". Say "the chart's strongest support is X to Y, and
  at that price the market would be assuming Z% growth". Not financial advice.
