---
name: irreplaceable-test
description: >-
  Irreplaceable Test. Moat or story? Grades a company PASS / WARN / FAIL on
  six tests from its own SEC filings: pricing power (did gross margin hold
  through the 2022-23 inflation), margin resilience, return on invested
  capital, return on new capital, reliance on its largest customer (read
  from the annual report) and dilution, each with the numbers and the filing
  behind it. Also pulls the annual report's Competition section for Claude to
  summarise. Draws a report card. Use when the user asks "does X have a
  moat", "is X irreplaceable", "competitive advantage", "moat or story",
  "quality check", "is this a good business", or on names /idea-scanner
  surfaces.
argument-hint: "TICKER"
---

# Irreplaceable Test

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
python "${CLAUDE_SKILL_DIR}/moat.py" AAPL
python "${CLAUDE_SKILL_DIR}/moat.py" KO --roic-pass 0.12 --roic-warn 0.08
python "${CLAUDE_SKILL_DIR}/moat.py" NVDA --no-text      # numbers only, skip the annual report text
```

## The six tests (the thresholds are judgment calls, printed with each result)

1. **Pricing power**: gross margin in 2021 against the lowest of 2022-23,
   when costs rose everywhere. PASS if it fell under 1 point, WARN under 3. A
   bigger fall during a revenue slump of 10%+ is only a WARN, because a sales
   collapse muddies the evidence.
2. **Margin resilience**: worst gross margin of the last 10 years against
   the typical (median) year. PASS within 3 points, WARN within 7.
3. **Return on capital**: after-tax operating profit / (equity + debt - cash
   - short-term investments), averaged over the year, median of the last 5
   years. PASS 15%+, WARN 10%+. Negative invested capital (cash and buybacks
   exceed equity) counts as a pass, with an explanation.
4. **Return on new capital**: extra after-tax operating profit over 5 years
   divided by the extra capital put in. Shows whether the advantage scales.
5. **Customer reliance**: the largest single-customer share of revenue in
   the latest annual report. US rules require disclosing any customer at 10%+
   of revenue, so finding none is evidence. Groups ("ten largest customers")
   are skipped; a distributor or bottler is at most a WARN.
6. **Dilution**: diluted share count growth over 5 years, split-adjusted.
   PASS at or under 1% a year, WARN up to 3%. It starts after any one-off jump
   (an IPO or merger) and says so.

Score: PASS 2 points, WARN 1, FAIL 0, out of the tests that could be graded.
80%+ "looks irreplaceable", 60%+ "real advantages, with cracks", 40%+ "mixed",
below that "mostly story".

## What to tell the user

The verdict line, then each test in one line with its number. Quote the
customer sentence when there is one. Then read the **COMPETITION** excerpt
and summarise it in two or three sentences: who the company names as
competitors and what it says it competes on. Label that part as your
reading of the filing. If the excerpt does not look like a competition
section, say so and offer to read the annual report link instead. Point to
the report card image for video, `investor/charts/<TICKER>-irreplaceable.svg`.

## Show the report

Every run also writes a finished report page, `investor/reports/<TICKER>-irreplaceable.html`: the
headline, the key figures, an interactive chart with hover details, the
tables, the method and the sources, in light and dark, readable on a phone.
It is the main thing to show the user.

- If you have a tool that publishes Artifacts (pages on claude.ai), publish
  that file as an Artifact: a one-sentence `description`, `icon` "chart",
  and give the user the link. Publishing it again later updates the same page.
- Otherwise tell the user the file path and that it opens in any web browser.

Keep the chat answer short (the headline, the two or three numbers that
matter, the warnings) and let the report carry the detail.

To put your summary of the competition section into the report, run the script
once more with `--competition-summary "<two or three sentences>"` (instant: the
data is cached) before publishing.

## Honesty rules

- Use only this skill's tools and your general knowledge. Do not search for
  or read the user's own files, notes or earlier research unless they hand
  you one by name.
- A pass is evidence, not proof. Moats erode faster than ten years of
  filings show, and the tests cannot see technology shifts.
- Financial companies, conglomerates, young companies and companies with big
  acquisitions fit these tests poorly. Say so when relevant.
- The customer test reads text with patterns. Show the quote so the user
  can check it. Not financial advice.
