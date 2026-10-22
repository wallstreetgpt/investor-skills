---
name: scorecard
description: >-
  Attractiveness Scorecard. Scores a stock 0-100 on five weighted pillars,
  each from stated rules: operating quality (return on capital, margins, cash
  conversion, balance sheet), recent trend (the latest quarters against a
  year earlier), forward growth (consensus, the company's own guidance and
  how often it beat it, estimate revisions), valuation (absolute, against
  peers, against its own 10-year P/E) and catalysts and risk (moves on
  results, buybacks, customer concentration, red flags in 8-Ks). Claude adds
  the thesis, drivers, risks and what would change the view. Produces a
  two-page scorecard report with a band (Highly Attractive to Avoid) and a
  confidence level. Use when the user asks "score X", "scorecard", "is X
  attractive", "rate this stock", "is it a good business", "does X have a
  moat", "quality check", "should I look at X", "full picture on X", or on
  names /idea-scanner surfaces.
argument-hint: "TICKER [--peers A,B,C]"
---

# Attractiveness Scorecard

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python` (Windows) or
`python3` (macOS/Linux); if one is not found, use the other. Run from the
user's project folder: results go in `investor/` there.

If neither `python` nor `python3` works (or Windows opens the Microsoft
Store), Python is missing: follow the /investor-setup skill, which explains
how to install it. If a script says the SEC needs a contact, ask the user for
their name and email and run
`python "${CLAUDE_SKILL_DIR}/investkit.py" save-contact "Their Name" their@email.com`:
it is saved once and works in every folder.

## Steps

1. **Pick 3-5 peers** on business model and end market, not index or
   sector label: companies a customer could buy from instead, or that sell
   the same kind of product to the same buyers. Listed on a US exchange or
   with a Yahoo symbol. Say in one line why you chose them.
2. **Score it** (about 30 seconds the first time; later runs use the cache):

   ```bash
   python "${CLAUDE_SKILL_DIR}/scorecard.py" NVDA --peers AVGO,AMD,TSM,MU
   ```

   It prints every input with its value, its 0-100 score and its source,
   the pillar scores, the composite, the latest revenue guidance and the
   guidance record, the customer-concentration sentence, red flags and
   catalyst filings. It also writes a notes file to fill in,
   `investor/scorecards/<TICKER>-notes.json`.
3. **Read the filings** the notes need:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scorecard.py" read NVDA outlook       # latest results release: highlights and guidance
   python "${CLAUDE_SKILL_DIR}/scorecard.py" read NVDA risks         # risk factor headings from the annual report
   python "${CLAUDE_SKILL_DIR}/scorecard.py" read NVDA customers     # customer concentration sentences
   python "${CLAUDE_SKILL_DIR}/scorecard.py" read NVDA competition   # who it says it competes with
   ```

4. **Fill the notes file** (JSON; leave out anything you have nothing solid
   for, and the report skips it):
   - `peers`, `peer_reason`: the peers and the one-line reason.
   - `thesis`: three items `{"lead": "short claim.", "text": "the numbers
     behind it", "source": "10-K FY26"}`. Use the printed figures; every
     number must come from the scorecard output or a filing you read.
   - `drivers`: two to four named growth drivers from the results release
     or annual report, each with its figure and source tag.
   - `guidance`: `{"text": "..."}` in the company's words. If the script
     found no guidance in dollars but the release has it, also give
     `"revenue"` (the midpoint, in dollars) so it is scored.
   - `disagreement`: one or two sentences, only if guidance and consensus
     clearly point different ways.
   - `catalysts`: dated or undated events from the filings, each
     `{"date": "12 Nov 2026" or "Undated", "sort": "2026-11-12", "title",
     "direction": "positive | negative | two-sided | positive skew",
     "note", "source"}`. The next results date, ex-dividend date and 8-K
     catalysts are added on their own.
   - `risks`: the three company-specific risks that matter most, from the
     risk factor headings and the numbers (not boilerplate such as "the
     economy could weaken"), each with its figure.
   - `valuation`: one sentence each for `absolute`, `peers`, `history`.
   - `change_mind`: a short paragraph: the one or two things that would
     undo the score.
   - Optional `why` and `what_would_change`, keyed by pillar (`quality`,
     `trend`, `forward`, `valuation`, `catalysts`), replace the automatic
     lines. The automatic ones are usually better: they are computed.
5. **Render the report** (instant, the data is cached):

   ```bash
   python "${CLAUDE_SKILL_DIR}/scorecard.py" NVDA --notes investor/scorecards/NVDA-notes.json
   ```

## The scoring (state it when asked)

Each input scores 0-100 on a straight line between two stated values (for
example free cash flow yield: 0 at 1%, 100 at 7%). A pillar is the average
of its inputs; valuation weights absolute 10, peers 8, own history 7.
Composite = 20% quality + 20% trend + 20% forward + 25% valuation + 15%
catalysts and risk. Bands: 80+ Highly Attractive, 65-79 Attractive, 50-64
Fair, 35-49 Unattractive, under 35 Avoid. Confidence is High when nearly
every input was sourced, Medium or Low when inputs are missing or replaced
(no consensus, no peers). Every rule is printed in the report's "How the
score is built" table. Missing inputs are left out, never guessed.

## What to tell the user

The score, band and confidence; the strongest and weakest pillar with the
number behind each; the one or two things that would change the view. Keep
the chat short and let the report carry the detail. Point to the image for
video, `investor/charts/<TICKER>-scorecard.svg`. Then suggest /priced-in
for the full expectations picture, /buy-zones for entry levels and
/time-machine for the range of outcomes.

## Show the report

The run writes a finished two-page report, `investor/reports/<TICKER>-scorecard.html`:
masthead with price and filing dates, the score gauge and band, the five
pillars, thesis, key metrics, five-year trend, the three valuation views with
a 10-year P/E chart, forward drivers, catalyst timeline, risks, what would
change each score, every input and its rule, and the sources. Light and dark,
readable on a phone, prints on two pages. It is the main thing to show.

- If you have a tool that publishes Artifacts (pages on claude.ai), publish
  that file as an Artifact: a one-sentence `description`, `icon` "chart",
  and give the user the link. Publishing it again later updates the same page.
- Otherwise tell the user the file path and that it opens in any web browser.

Running it again on a later day shows the change in score ("+3 vs 6 Aug 2026").

## Honesty rules

- Use only this skill's tools and your general knowledge. Do not search for
  or read the user's own files, notes or earlier research unless they hand
  you one by name.
- The scores come from the script. Never change a score or invent a number;
  your part is the notes, and the report marks them "Claude".
- Consensus estimates and peer figures come from Yahoo Finance and are
  estimates. GAAP earnings can include one-off gains (check the results
  release for GAAP against non-GAAP) and say so when they distort P/E.
- Banks, insurers and REITs fit these rules poorly (no gross margin, debt
  is their raw material). Say so if asked to score one.
- The score organises evidence; it is not a recommendation. Not financial advice.
