---
name: idea-scanner
description: >-
  Idea Scanner. Scans every US-listed company that files with the SEC
  (about 1,800 worth $1B or more) through stated investor lenses: quality
  compounders, the expectations gap (the price assumes less growth than the
  company has been delivering), fallen quality, accelerating growth and fresh
  catalysts, plus one combined ranking. Adds newsflow: positive 8-K catalysts
  (raised guidance, record backlog, new buybacks), red flags that remove names
  (restatements, going-concern doubt) and recent headlines. Also scans a
  watchlist for news. Use when the user asks for "stock ideas", "what to look
  at", "screen", "scanner", "find me stocks", "what's cheap", "quality
  stocks", "beaten down", "what raised guidance", "newsflow", "catalysts", "any
  news on my stocks", or names a theme and asks which companies to look at.
argument-hint: "[--lens all|quality|gap|fallen|accelerating|catalysts] [--theme \"phrase\"] | news TICKERS"
---

# Idea Scanner

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
python "${CLAUDE_SKILL_DIR}/scanner.py"                                 # all lenses
python "${CLAUDE_SKILL_DIR}/scanner.py" --lens gap --min-cap 5e9        # one lens, bigger companies
python "${CLAUDE_SKILL_DIR}/scanner.py" --lens quality --theme "data center"
python "${CLAUDE_SKILL_DIR}/scanner.py" --lens catalysts --days 7
python "${CLAUDE_SKILL_DIR}/scanner.py" news NVDA,MSFT,KO               # newsflow for a watchlist
python "${CLAUDE_SKILL_DIR}/scanner.py" news --watchlist my-stocks.txt
```

The first run reads about 100 SEC data files and takes a minute or two;
later runs take seconds (cached). Tell the user that before the first run.

## The lenses (the rules print with every result)

| lens | the idea | the rule |
|---|---|---|
| `quality` | Quality compounders | Return on capital 15%+ in each of 3 years, gross margin within 3 points of its 3-year average, revenue +8% a year or more, shares +2% a year or less |
| `gap` | Expectations gap | The growth the price assumes (Priced-In's reverse DCF, 9% required return) is 5+ points below the company's own revenue growth (the lower of its 3-year and last-year rates, counted at 40% at most); positive free cash flow 2 years running; return on capital 10%+; no acquisitions |
| `fallen` | Fallen quality | Return on capital 12%+ in each of 3 years, steady gross margin, positive free cash flow, and 25%+ below its 52-week high |
| `accelerating` | Accelerating growth | Latest quarter's revenue +15% on a year ago and 5+ points faster than a year earlier (which was already positive), margin holding, positive free cash flow, no acquisition |
| `catalysts` | Fresh catalysts | A positive 8-K in the last 14 days (raised guidance, record results or backlog, new or larger buyback, dividend increase, major contract), positive operating cash flow, return on capital 10%+ |
| `all` | Combined | Average percentile rank on return on capital, own revenue growth and the expectations gap |

Kept out on purpose (each counted in the output):
- Companies without US-GAAP filings, or reporting in another currency.
- Banks, insurers, REITs, lenders, asset managers and brokers (by their SEC
  tags and industry code).
- Names with a hard red flag in the last 90 days: restatement (8-K item
  4.02), going-concern doubt or a bankruptcy filing. `--keep-flagged` keeps them.
- From the growth lenses: companies that completed an acquisition in the last
  13 months (8-K item 2.01), because their growth is partly bought.

## Newsflow

- **8-K catalysts and red flags** come from SEC full-text search, counting only
  the 8-K itself and its press release (Exhibit 99), not attached contracts.
- **Recent 8-Ks** are listed by item: results, leadership change, deal completed...
- **Headlines** are Yahoo's feed for each shortlisted ticker, kept only when they
  name the company. Tone words (beats, raises / misses, cuts, lawsuit) are a
  simple keyword tag. Read the headlines yourself before characterising them.

## Show the report

Every run writes a finished report page, `investor/reports/scan-<lens>.html`
(or `investor/reports/newsflow.html` for `news`). It has:
- tabs per lens, with sortable tables;
- a chart of each company's growth against the growth its price assumes;
- the newsflow per company;
- what was removed and why.

- If you have a tool that publishes Artifacts (pages on claude.ai), publish that
  file as an Artifact: a one-sentence `description`, `icon` "chart", and give
  the user the link. Publishing it again later updates the same page.
- Otherwise tell the user the file path and that it opens in any web browser.

## What to tell the user

1. The counts: how many were scanned and how many pass each lens.
2. The top three to five names for the lens they asked about. For each, give
   the "why it showed up" line in your own words, and the newsflow that
   matters (a raised guide, a red flag, a headline that explains a fall).
3. For **fallen quality** names, read their headlines and say whether the
   fall looks like a temporary scare or a broken thesis, labelled as your
   reading.
4. The next step for each name, as the report's "Next step" column suggests:
   `/irreplaceable-test` (is the quality real), `/priced-in` (what the price
   assumes on trailing numbers) or `/buy-zones`.

## Honesty rules

- Use only this skill's tools and your general knowledge. Do not search for
  or read the user's own files, notes or earlier research unless they hand
  you one by name.
- A screen is a starting point, not a pick. Never call a result a buy.
- The fundamentals are the last full calendar year in SEC's frames (a fiscal
  year ending early next year counts toward it). /priced-in redoes the
  valuation on trailing-twelve-month numbers, so the two can differ.
- Frames carry what companies tagged; a bad tag can push a company in or out.
  Companies that hold customer money (payment apps, marketplaces with float)
  can show inflated cash flow. Say so when a name looks too good.
- Survivors only: today's ticker list leaves out companies that failed.
- A phrase match shows what a company said, not whether it matters. A
  headline that names a company can still be mostly about another one.
- Not financial advice.
