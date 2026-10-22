---
name: investor-setup
description: >-
  Investor Skills setup and first steps. Checks that Python and the free data
  sources (SEC EDGAR, Yahoo Finance) are ready, saves the SEC contact the user
  must give once (it then works in every folder), downloads the market data
  in advance so the first scan is fast, and shows what to try first. Use when
  the user has just installed Investor Skills, says "set up", "get started",
  "how do I use this", "first time", "it doesn't work", "python not found",
  or when another Investor Skill reports a missing SEC contact or Python.
---

# Investor Skills setup

`${CLAUDE_SKILL_DIR}` is this skill's folder. Go through the steps in order,
talking to the user in plain words. Most people using this are not
programmers: explain what a step does before running it, and never install
anything without their yes.

## 1. Python

Run `python --version`, and if that fails, `python3 --version`. Use whichever
works for every later step. Python 3.8 or newer is needed.

If neither works, or Windows opens the Microsoft Store instead (a message
mentioning "App execution aliases"), Python is missing. Tell the user, and
offer the right install for their system:

- **Windows:** `winget install --id Python.Python.3.12 -e` (ask before running it),
  or download from python.org and tick "Add python.exe to PATH" during install.
  Then they must restart Claude so it sees Python.
- **macOS:** `xcode-select --install` (Apple's developer tools, which include
  python3), or the installer from python.org.
- **Linux:** the package manager, e.g. `sudo apt install python3`.

## 2. The checklist

```bash
python "${CLAUDE_SKILL_DIR}/setup.py"
```

It prints OK or FIX for each item: Python, the SEC contact, whether SEC EDGAR
and Yahoo Finance can be reached, where results will be saved, and which
Investor Skills are installed.

## 3. The SEC contact (once)

If the contact line says FIX, explain: the SEC asks every program that reads
its filings to identify itself with a name and email. It is sent only to
sec.gov, with each request, and saved on this computer. Ask for their name
and email, then run:

```bash
python "${CLAUDE_SKILL_DIR}/investkit.py" save-contact "Their Name" their@email.com
```

Run the checklist again until everything says OK. If SEC or Yahoo can't be
reached, it is usually a firewall, a VPN or a work network.

## 4. Download the market data now (optional, 1-2 minutes)

The Idea Scanner reads about 100 SEC data files the first time it runs.
Offer to fetch them now so the first scan is instant later:

```bash
python "${CLAUDE_SKILL_DIR}/setup.py" warm
```

## 5. Show them how to use it

End setup with this guide, in your own friendly words, kept short:

**Where to work.** Open Claude Code in one folder for investing (for
example a new "Investing" folder) and keep using it: results are saved in an
`investor/` folder there.

**Just ask.** Plain English is enough; Claude picks the right skill:

| what you want | say something like |
|---|---|
| Ideas | "Scan the market for quality compounders", "What raised guidance this week?", "Any news on NVDA, KO and MSFT?" |
| Is it attractive? | "Score Costco", "Give me a scorecard on Nvidia" |
| Is it expensive? | "What's priced into NVIDIA?" |
| When to buy | "Where are the buy zones for Microsoft?" |
| What could happen | "Simulate 10 years of owning Apple, $500 a month" |

Slash commands work too: `/idea-scanner`, `/scorecard`,
`/priced-in`, `/buy-zones`, `/time-machine`. Installed as a plugin they carry
a prefix, e.g. `/investor-skills:priced-in`.

**What comes back.** A short answer in chat, plus a report page with the
chart, tables and sources (a link where Claude can publish pages, otherwise
an HTML file in `investor/reports/`), and a chart image in `investor/charts/`.

**For one stock, go in order:** scan → scorecard → priced-in → buy zones →
time machine. Priced-In feeds Buy Zones and the Time Machine.

**Good to know:** setup never needs repeating; the first market scan takes a
minute or two unless the data was downloaded in step 4; data refreshes on
its own. Everything is research, not financial advice.

Then offer to run their first question.

## Honesty rules

- Use only this skill's tools and your general knowledge. Do not read the
  user's own files.
- Never install software or change system settings without the user's yes.
- These are research tools, not financial advice.
