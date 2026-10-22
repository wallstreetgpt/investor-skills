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

## 5. What to try first

Tell the user they can just ask in plain words. Five good first questions:

- "Scan the market for quality stocks" or "any fresh catalysts this week?" (Idea Scanner)
- "Does Costco have a moat?" (Irreplaceable Test)
- "What's priced into NVIDIA?" (Priced-In)
- "Where are the buy zones for Microsoft?" (Buy Zones)
- "Simulate 10 years of owning Apple" (Time Machine)

Each answer comes with a report page (published as an Artifact where
possible, otherwise an HTML file in `investor/reports/` that opens in any
browser). Results are saved in an `investor/` folder wherever they work.

## Honesty rules

- Use only this skill's tools and your general knowledge. Do not read the
  user's own files.
- Never install software or change system settings without the user's yes.
- These are research tools, not financial advice.
