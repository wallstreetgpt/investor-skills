---
name: time-machine
description: >-
  Time Machine. A Monte Carlo of the next 10 years for one stock against the
  S&P 500: 10,000 simulated futures built from the stock's real monthly
  returns (paired with the market's, drawn in blocks so crashes stay
  clustered), centred on growth scenarios from /priced-in instead of on the
  past. Shows the range of outcomes, odds of losing money, odds of beating
  the S&P, the worst fall you would likely sit through, and every real
  10-year window from its own history. Lump sum or monthly investing. Use
  when the user asks "what could happen if I buy X", "simulate", "Monte
  Carlo", "10,000 futures", "what are the odds", "lump sum vs monthly",
  "worst case", or "time machine".
argument-hint: "TICKER [--monthly 500] [--years 10]"
---

# Time Machine

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
python "${CLAUDE_SKILL_DIR}/timemachine.py" NVDA                         # $10,000 today, 10 years
python "${CLAUDE_SKILL_DIR}/timemachine.py" KO --monthly 500 --years 20  # plus $500 a month
python "${CLAUDE_SKILL_DIR}/timemachine.py" TSLA --return 0.12           # the user's own expected return
python "${CLAUDE_SKILL_DIR}/timemachine.py" AAPL --scenario priced_in    # one scenario only
python "${CLAUDE_SKILL_DIR}/timemachine.py" AAPL --scenario history      # raw history (hindsight), for contrast
python "${CLAUDE_SKILL_DIR}/timemachine.py" NVDA --vol full              # use the whole record's volatility
```

Run `/priced-in TICKER` first if it has not been run today: the Time Machine
reads its scenarios from `investor/<TICKER>.json`. Without them it compares
"earns the market's 9%" with "repeats its own past (hindsight)".

## How it works (say this plainly when asked)

- **The shape comes from history.** Each simulated month is a real month of
  the stock's total return (dividends in), paired with the S&P 500's (SPY)
  return in the same month. Months are drawn in blocks averaging 12 months (a
  stationary block bootstrap), so crashes and rallies keep their clustering
  and the stock keeps its real link to the market.
- **The swings are resized to today.** By default each month's deviation from
  the average is scaled so volatility matches the last 5 years, not a
  company's wild early years (filtered historical simulation). `--vol full`
  turns this off.
- **The center does not come from history.** A plain bootstrap of a past
  winner just promises its past forever. Each scenario shifts the average
  (log) yearly growth to the scenario's return from /priced-in; the typical
  (median) future lands slightly above it because crashes skew the downside.
  The S&P is set to the same 9% the discount rate assumes.
- All scenarios run on the same 10,000 random paths, so differences between
  them are real, not noise. Seed 2026: the same command gives the same
  numbers.
- **What really happened** prints every rolling window of the same length in
  the stock's own history, beside the simulation.

## What to tell the user

For each scenario: what $X becomes in a bad (5th percentile), typical
(median) and great (95th percentile) future; the odds of losing money and of
beating the S&P; the typical worst fall along the way ("could you sit
through a 60% drop?"). Then the real-history windows, and the chart
`investor/charts/<TICKER>-time-machine.svg` (log scale fan chart, as an image for video).

The most useful comparison is usually "priced in" against "the S&P alone":
if the stock only delivers what is priced in, the user gets roughly the
market's return with far bigger swings.

## Show the report

Every run also writes a finished report page, `investor/reports/<TICKER>-time-machine.html`: the
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
- It is a range of futures, not a forecast. The center is an assumption the
  user chooses; say which one each number belongs to.
- History windows overlap and only contain the path that happened, from a
  company that survived. Say so.
- Under 10 years of prices, the shape rests on a short record (it warns).
- Never present a percentile as a prediction or a promise. Not financial advice.
