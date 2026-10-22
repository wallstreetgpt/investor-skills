---
name: priced-in
description: >-
  Priced-In Calculator. Works out what growth a stock's price already assumes
  (a reverse DCF from SEC filings), then checks it against reality: the
  company's own track record and the base rate of how fast every US company
  its size actually grew over a decade. Turns growth scenarios (typical peer,
  top-quarter peer, as priced in, its own pace) into the yearly return you
  would earn from today's price. Use when the user asks "what's priced in",
  "what does the market expect", "is X expensive", "what growth does the
  price assume", "reverse DCF", "expectations", "is it overvalued", or before
  /time-machine and /buy-zones (both read its results).
argument-hint: "TICKER [--discount 0.09] [--price P]"
---

# Priced-In Calculator

Instead of guessing a fair value, take the market's price as the answer and
solve for the question: how fast must this business grow to be worth it?

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run the script with `python` on
Windows or `python3` on macOS/Linux; if one is not found (or opens the
Microsoft Store), use the other. Run it from the user's project folder: it
reads and writes `investor/` there.

```bash
python "${CLAUDE_SKILL_DIR}/pricedin.py" NVDA
python "${CLAUDE_SKILL_DIR}/pricedin.py" KO --discount 0.08          # a steadier company, lower required return
python "${CLAUDE_SKILL_DIR}/pricedin.py" RIVN --target-margin 0.10   # loss-maker: margin it reaches by year 10
python "${CLAUDE_SKILL_DIR}/pricedin.py" NVDA --price 150            # what would $150 assume? (not saved)
python "${CLAUDE_SKILL_DIR}/pricedin.py" AAPL --scenario 0.12        # add your own growth scenario
```

If neither `python` nor `python3` works (or Windows opens the Microsoft
Store), Python is missing: follow the /investor-setup skill, which explains
how to install it. If a script says the SEC needs a contact, ask the user for
their name and email and run
`python "${CLAUDE_SKILL_DIR}/investkit.py" save-contact "Their Name" their@email.com`:
it is saved once and works in every folder.

The base rates take about a minute the first time (they read every filer's
revenue since 2011), then come from the cache.

## How it works (say this plainly when asked)

1. **What you pay**: market cap from the price and SEC share count (or Yahoo
   for ADRs and odd share classes), plus debt, minus cash and short-term
   investments, all from the latest balance sheet = enterprise value.
2. **What the business makes**: trailing-twelve-month free cash flow =
   operating cash flow - capex - stock compensation (stock comp is treated as
   a real cost because it dilutes owners; `--basis fcf` leaves it in).
3. **The model**: free cash flow grows at a constant rate for 10 years, then
   2.5% a year forever, discounted at 9% (roughly the stock market's long-run
   return). It solves for the growth rate that makes this equal today's
   enterprise value. A sensitivity table shows 8-10% discount and 2-3%
   terminal growth.
4. **Modes it picks by itself**:
   - free cash flow, when it is at least 5% of revenue;
   - revenue, when cash flow is negative or thin: revenue grows and the
     margin moves in a straight line to a target margin by year 10;
   - earnings, for banks, insurers and other financials (SIC 60-67), where
     cash and debt are inventory, not spare cash.
5. **Growth-capex check**: when capex is far above depreciation (heavy
   building for growth, e.g. AI data centers), it also shows the implied
   growth if capex were only depreciation. The truth is between the two.
6. **Base rates**: SEC XBRL "frames" give every filer's revenue by calendar
   year. It pools every 10-year window starting 2011 onward for companies that
   started in the same revenue bracket, and reports what share grew at least
   as fast as the price needs. Survivors only (acquired and failed companies
   drop out), so the true odds are a bit worse.
7. **Scenario returns**: for each growth rate, the yearly return from paying
   today's price, if the market then values the company at the same discount
   rate. At exactly the priced-in growth, the return equals the discount rate.

## What to tell the user

Lead with the headline sentence, then:
- the implied growth next to its own track record and the base rate;
- the scenario table (the "your return" column is the most useful number);
- the growth-capex check if it appears;
- every line under "Check:" (stale data, financial company, share class).

The chart image for video is `investor/charts/<TICKER>-priced-in.svg` (open it in
a browser), and suggest `/time-machine TICKER` (it uses these scenarios) and
`/buy-zones TICKER` (it shows the growth priced in at each support zone).

## Show the report

Every run also writes a finished report page, `investor/reports/<TICKER>-priced-in.html`: the
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
- "Priced in" depends on the required return. Always say "if investors want
  9% a year". A steadier company may deserve 7-8%; use the sensitivity table.
- Free-cash-flow growth and revenue growth are the same only at a steady
  margin. Say so when comparing to the revenue base rates.
- Foreign filers report once a year and SEC's feed can lag; the script warns
  when the numbers are over 13 months old. Repeat that warning.
- It is a lens, not a verdict. Never call a stock a buy or a sell.
- Not financial advice. Most of this is arithmetic on public filings, which
  can be restated or mis-tagged.
