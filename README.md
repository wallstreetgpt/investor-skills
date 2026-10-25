# Investor Skills

Five research bots for long-term investors, as Claude skills. The Scout scans
the whole market for ideas and their news, The Judge scores how attractive a
stock is, The Mind Reader shows what its price already assumes, The Sniper
finds where buyers have stepped in before, and The Time Traveller simulates
10,000 possible next decades.

Plain Python with nothing to install and no API keys. They read SEC filings
and free market data, and show the source of every number.

> Educational use only, not financial advice. Read the [disclaimer](#disclaimer).

| bot | the question | how it answers |
| --- | --- | --- |
| **The Scout** `/the-scout` | What is worth a closer look? | Scans every US-listed SEC filer (about 1,800 worth $1B+) through stated lenses (quality compounders, expectations gap, fallen quality, accelerating growth, fresh catalysts) with 8-K newsflow, red flags and headlines. Also scans a watchlist for news. |
| **The Judge** `/the-judge` | How attractive is it, all things weighed? | A 0-100 score on five weighted pillars, every input with its rule and source: operating quality, recent trend, forward growth (consensus, the company's own guidance and how often it beat it), valuation (absolute, against peers, against its own 10-year P/E) and catalysts and risk. Claude adds the thesis, drivers and risks from the filings. |
| **The Mind Reader** `/the-mind-reader` | What does the price already expect? | A reverse DCF: the growth rate today's price implies, checked against the company's own record and the base rate for every US company its size since 2011. |
| **The Sniper** `/the-sniper` | Where is a good price to start buying? | Support zones found by rules (swing lows, old ceilings turned floors, volume shelves, 200-day and 200-week averages), each with its track record, the odds of price getting there, and the growth priced in at that level. |
| **The Time Traveller** `/the-time-traveller` | What could the next 10 years look like? | A Monte Carlo of 10,000 futures. The shape comes from the stock's real monthly returns, paired with the S&P 500. The center comes from The Mind Reader's growth scenarios, not from the past. |

**The Scout finds it → The Judge scores it → The Mind Reader prices it → The
Sniper times it → The Time Traveller lives it.** Each bot saves its results to
`investor/<TICKER>.json`, and the next one picks them up.

## Quick start

You need **Claude Code**: the Code tab in the Claude desktop app, or Claude
Code in a terminal. The skills run small Python scripts on your computer that
read free public data.

1. **Install.** In Claude Code, type these two lines:
   ```
   /plugin marketplace add wallstreetgpt/investor-skills
   /plugin install investor-skills@investor-skills
   ```
   Or paste this page's link into Claude Code and say *"install these skills"*.
2. **Set up.** Say *"set up investor skills"*. Claude:
   - checks Python and the data sources;
   - asks once for your name and email, because the SEC requires every
     program that reads its filings to identify itself (it is sent only to
     sec.gov);
   - offers to download the market data in advance.
3. **Ask.** For example:
   - *"Scan the market for quality stocks"*
   - *"Score Costco"*
   - *"What's priced into NVIDIA?"*
   - *"Where are the buy zones for Microsoft?"*
   - *"Simulate 10 years of owning Apple"*

Every answer comes with a report page, and results are saved in an
`investor/` folder wherever you work.

Installed as a plugin, the commands carry a prefix (`/investor-skills:the-mind-reader`).
Plain-English requests work either way. After a plugin install, Claude Code
greets you at the start of each session until setup is done.

## How to use it

- **Pick one folder** for investing and open Claude Code there. Results are
  saved in an `investor/` folder inside it.
- **Ask in plain English:**

  | what you want | say something like | who answers |
  |---|---|---|
  | Ideas | "Scan the market for quality compounders", "What raised guidance this week?", "Any news on NVDA, KO and MSFT?" | The Scout |
  | Is it attractive? | "Score Costco", "Give me a scorecard on Nvidia" | The Judge |
  | Is it expensive? | "What's priced into NVIDIA?" | The Mind Reader |
  | When to buy | "Where are the buy zones for Microsoft?" | The Sniper |
  | What could happen | "Simulate 10 years of owning Apple, $500 a month" | The Time Traveller |

- **What comes back:** a short answer in chat, a report page with the chart,
  tables and sources, and a chart image in `investor/charts/`.
- **For one stock, go in order:** The Scout, The Judge, The Mind Reader, The
  Sniper, The Time Traveller. The Mind Reader feeds the last two.

## For Claude: installing from this page

If a user asks you to install the skills from this repository:

1. Prefer the plugin: run `/plugin marketplace add wallstreetgpt/investor-skills`
   and then `/plugin install investor-skills@investor-skills`. If you can't run
   those, ask the user to paste them.
2. Otherwise copy each folder in `skills/` into `~/.claude/skills/`.
3. Then start the `investor-setup` skill. It checks Python and the data
   sources, saves the SEC contact the user gives, and ends by showing them
   how to use the skills.

## Example: NVIDIA, October 22 2026

These are real outputs from live runs that day. The offline tests replay The
Mind Reader's figure from recorded data.

- **The Mind Reader (Priced-In):** at $221.97, the price assumes free cash flow grows **16.2% a
  year for 10 years**, if investors want 9% a year back. Of US companies
  that started a decade with over $50B in revenue, **3%** grew that fast.
  If NVIDIA grows like a typical company its size (2.8%), the return from
  today's price is **-3.6% a year**. If it keeps its own 10-year pace, it is
  36.6%.
- **The Time Traveller (Time Machine):** $10,000 for 10 years, if it delivers exactly what is
  priced in:
  - Futures: bad $1,527, typical $24,380, great $326,969. S&P alone, typical: $24,829.
  - It beats the S&P in 50% of futures, and loses money in 29%.
  - The typical worst fall along the way is -77%.
  - In other words, the market's return with far bigger swings.
- **The Sniper (Buy Zones):**
  - $208-216 (strength 56), $179-185 (51) and $164-171 (50).
  - Odds of price reaching the deepest zone within a year: 37%.
  - Valuation floor: $110. Below that, the price would only assume what a
    top-quarter company its size achieves. No chart zone reaches it.
- **The Judge (Scorecard):** 81/100, Highly Attractive, confidence High (peers AVGO, AMD,
  TSM, MU).
  - Pillars: quality 86, trend 92, forward growth 95, valuation 70, catalysts
    and risk 59.
  - Revenue grew 106% in the latest quarter; guidance for the next is $108.0B,
    and it beat its own revenue guidance in each of the last four quarters.
  - Trailing P/E 28x against a 5-year median of 65x, but a 2.4% free cash flow
    yield and EV/EBITDA above the peer median.
  - Weakest pillar: one direct customer is 22% of revenue, and buybacks plus
    dividends are only 1.2% of the market value.
- **The Scout (Idea Scanner)**, whole market, same day:
  - It scanned 1,755 companies; 81 passed quality compounders, 108 the
    expectations gap and 92 fallen quality.
  - NVIDIA passes quality compounders and the expectations gap.
  - The newsflow found United Rentals, 3M and Waste Connections raising
    guidance, and Lam Research, CSX and Northrop reporting records.
  - It removed names with restatement or going-concern 8-Ks, and set aside
    Devon and Sanmina from the growth lenses because their growth was partly
    bought (completed-deal 8-Ks).

Each skill also writes a report page (`investor/reports/*.html`, published as
an Artifact where Claude can) and draws a chart (`investor/charts/*.svg`),
sized for video.

## Other ways to install

- **Without the plugin system:** download this repository (the green **Code**
  button, then **Download ZIP**), unzip it, and tell Claude Code *"copy the
  folders in investor-skills/skills into my ~/.claude/skills folder"*. The
  commands are then plain `/the-mind-reader` and so on.
- **By hand:** copy each folder in `skills/` into `~/.claude/skills/`. Each
  folder is self-contained.

**Needs:** Python 3.8 or newer (nothing to pip install) and an internet
connection to sec.gov and yahoo.com.

## If something goes wrong

- **"Python was not found", or Windows opens the Microsoft Store:** Python
  isn't installed. Say *"set up investor skills"* and Claude explains how to
  install it. On Windows, tick "Add python.exe to PATH", then restart Claude.
- **"SEC EDGAR needs a name and contact email":** run the setup, or tell
  Claude your name and email and it saves them.
- **"Not reachable" or network errors:** a firewall, VPN or work network may
  block sec.gov or yahoo.com.
- **The first scan takes a minute or two:** it is downloading the whole
  market's data once. Later scans take seconds.
- **Where is the report?** In `investor/reports/`. Open the `.html` file in
  any browser.
- **No welcome after installing the plugin:** start a new session. The welcome
  hook runs in Git Bash, which Claude Code on Windows normally has. If it
  doesn't show, just say *"set up investor skills"*.

## The method, and why it is built this way

**The Mind Reader (Priced-In).**
- Market cap + debt - cash - short-term investments = enterprise value.
- Free cash flow = operating cash flow - capex - stock compensation, over
  the trailing twelve months, built from 10-K and 10-Q data.
- A two-stage DCF (10 years of growth, then 2.5% forever, discounted at 9%)
  is solved for the growth that matches today's price.
- Loss-makers switch to a revenue model whose margin converges to a target.
  Banks and insurers switch to earnings, because their cash and debt are
  inventory, not spare cash.
- When capex runs far above depreciation, it also shows the growth implied
  with maintenance-level capex.
- **Base rates** come from SEC XBRL frames. That is every filer's revenue for
  every year since 2011, pooled into 10-year windows by starting size. This
  follows the "outside view": a company's own story is weighed against
  what companies like it actually achieved.
- **Scenario returns** are internal rates of return from today's price. They
  equal the discount rate exactly at the priced-in growth, which ties every
  scenario to the same model.

**The Time Traveller (Time Machine).**
- **The draws:** a stationary block bootstrap (Politis-Romano, mean block 12
  months) draws real months of the stock's total return, paired with SPY's
  in the same month. Crashes stay clustered and the stock keeps its link to
  the market.
- **The swings:** resized to the last 5 years' volatility (filtered
  historical simulation), so an old company's wild early years do not set
  today's swings.
- **The center:** shifted so the average yearly growth (in log terms)
  equals each scenario's return. A bootstrap of a past winner would
  otherwise promise its past forever. The typical (median) future lands
  slightly above that average, because crashes skew the downside.
- **Fair comparisons:** all scenarios share the same 10,000 random paths, so
  their differences are not noise.
- **History beside it:** it prints every real rolling window from the
  stock's own history next to the simulation.

**The Sniper (Buy Zones).**
- **The levels:** weekly pivots (4 weeks each side) with a 1.5-year
  recency half-life, role reversal (a swing high held above for 4 weeks),
  a 2-year volume-by-price profile, and the 200-day and 200-week averages.
  Levels are clustered at half the stock's typical weekly range.
- **The track record:** a test counts as held at +5% within 60 trading days,
  and as broken at a close 2% below the zone.
- **The odds:** a daily block bootstrap estimates the chance of reaching each
  zone. They are checked against how often a fall that deep happened in the
  last 5 years. The two agree within a few points on the stocks tested.

**The Judge (Scorecard).**
- **Every input is a rule:** it scores 0-100 on a straight line between two
  printed values (free cash flow yield: 0 at 1%, 100 at 7%). A pillar is the
  average of its inputs; the composite weights quality, trend and forward
  growth 20% each, valuation 25% and catalysts and risk 15%. Missing inputs
  are left out and lower the confidence level, never guessed.
- **Guidance is read from the filings:** the revenue outlook in each results
  press release (8-K, exhibit 99), checked against what the company then
  reported, gives the "beat its own guidance" record.
- **Its own history, done properly:** trailing P/E for every month of 10
  years uses only the earnings known that month, split-adjusted, with the
  fourth quarter derived from figures on the same share basis.
- **Peers are Claude's choice** (business model and end market, stated in the
  report) and compared on Yahoo's figures for every company, like with like.
- **Claude's part is marked:** the thesis, drivers, risks and what would change
  the view carry a "Claude" tag; every computed number carries its source.
- The customer test reads the annual report's own sentences and shows them.
  It handles negations, groups ("ten largest customers"), distributors,
  receivables-only mentions and "respectively" lists.

**The Scout (Idea Scanner).**
- **One request per number for the whole market:** SEC XBRL frames return
  one figure for every filer (about 110 requests, cached), and Yahoo prices
  400 tickers per request.
- **Same definitions as the single-company skills:** return on capital as in
  The Judge, and the expectations gap from The Mind Reader's reverse DCF.
- **Rules against the usual screening traps:**
  - own growth is the lower of the 3-year and last-year rates, so post-COVID
    rebounds do not count, and it is capped at 40% a year;
  - free cash flow must be positive two years running;
  - growth bought by acquisition is set aside (a goodwill jump or a
    completed-deal 8-K, item 2.01);
  - revenue uses the largest revenue tag, so lessors' rental income counts;
  - companies reporting in other currencies and financial companies are left
    out (by their XBRL tags and SEC industry code).
- **Newsflow:** SEC full-text search over 8-Ks for fixed phrases, counting
  only the 8-K and its press release, not attached contracts whose
  boilerplate mentions going concern or delisting. Plus Yahoo headlines that
  name the company.

## Known limits

- Free data can lag or be wrong. SEC's feed carries what companies tagged.
  Foreign filers report once a year, and the newest annual report can be
  missing for months. The tools warn when financials are over 13 months old.
- The base rates include survivors only: acquired and failed companies drop
  out, so the real odds are somewhat worse than shown.
- Support zones are fitted to the same history their records are measured
  on, which flatters them a little. The output says so.
- The Judge's thresholds are judgment calls (each is printed), and
  consensus and peer figures are Yahoo's estimates. Banks, insurers and very
  young companies fit its rules poorly.
- The Scout uses the last full calendar year from SEC frames, so it
  can differ from The Mind Reader's trailing-twelve-month figures. Companies that
  hold customer money can show inflated cash flow, and a headline that names
  a company can be mostly about another one.

## Disclaimer

This content is for educational purposes only and should not be considered
financial advice. Nothing here is a recommendation to buy or sell any
security. I am not a licensed financial advisor. All investing carries risk
including total loss of capital, and past performance does not predict future
results. AI tools can make mistakes. To the fullest extent permitted by law I
accept no liability for any loss arising from your use of this content. Your
decisions are your own. Speak to a licensed professional before investing.

## Terms of use

© 2026 WallStreetGPT. All rights reserved. You may install and use these
skills for your own research. Please don't redistribute, resell or publish
modified versions.

## Tests

```bash
python -m unittest discover -s tests
```

The 67 tests run offline in about 3 seconds:

- **Math checks:** a DCF round trip, IRR equal to the discount rate at the
  priced-in growth, the bootstrap's average block length, volatility scaling.
- **Filing parsers:** cases built from real filings (multi-class shares,
  stale tags, IFRS debt, customer sentences from NVIDIA, TSMC, Broadcom and
  Coca-Cola).
- **Base rates and the scanner's lenses:** checked on synthetic companies with
  known answers. Each lens has companies built to pass or fail by a known
  margin (a dilutive compounder, an acquisitive grower, a rebound from
  negative growth, a bank, a restatement).
- **End-to-end runs:** on responses recorded on 2026-10-22, in
  `tests/fixtures/live/`.

To replay any day exactly, set `INVESTOR_TODAY=YYYY-MM-DD`. To run from a
fixture folder instead of the network, set `INVESTOR_OFFLINE=<dir>`. To
record new fixtures, set `INVESTOR_RECORD=<dir>`.

## Layout

```
core/investkit.py        shared core (data, filings, valuation math, bootstrap, charts)
tools/sync_core.py       copies the core into every skill folder (run after editing it)
skills/<name>/SKILL.md   what Claude reads
skills/<name>/*.py       the tool, plus its copy of investkit.py
hooks/                   the welcome shown after a plugin install, until setup is done
tests/                   unittest suite and recorded fixtures
```
