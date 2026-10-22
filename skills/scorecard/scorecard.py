#!/usr/bin/env python3
"""Scorecard: how attractive is this stock, all things weighed?

Five pillars, each scored 0-100 by stated rules from free data, then weighted
into one score and a band:

  1. Operating quality   20%  return on capital, margins, cash conversion, balance sheet
  2. Recent trend        20%  the last quarters against a year earlier: growth, its
                              direction, margins, share count
  3. Forward growth      20%  consensus growth, the company's own guidance and how often
                              it has beaten it, estimate revisions, earnings surprises
  4. Valuation           25%  absolute (10), against peers Claude picks (8), against
                              its own 10-year P/E history (7)
  5. Catalysts and risk  15%  how the stock has moved on results, buybacks and dividends,
                              reliance on one customer, red flags in its 8-Ks

Every input is a number with a source tag (10-K, 10-Q, 8-K, market data,
consensus, calculated) and a printed rule. Claude adds what a script cannot:
the thesis, the growth drivers, the risks that matter, what would change the
view. That goes in a notes file, shown in the report as Claude's reading.

  python scorecard.py NVDA --peers AVGO,AMD,TSM,MU
  python scorecard.py read NVDA outlook        # also: risks, customers, competition
  python scorecard.py NVDA --peers AVGO,AMD,TSM,MU --notes investor/scorecards/NVDA-notes.json
"""

import argparse
import datetime
import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import investkit as ik  # noqa: E402

PILLARS = [("quality", "Operating quality & key metrics", 0.20), ("trend", "Recent financial trend", 0.20),
           ("forward", "Forward growth & key drivers", 0.20), ("valuation", "Valuation", 0.25),
           ("catalysts", "Catalysts & risk skew", 0.15)]
PILLAR_NAME = {k: n for k, n, _ in PILLARS}
VALUATION_PARTS = [("absolute", "Absolute", 10), ("peers", "Versus peers", 8), ("history", "Versus own history", 7)]
BANDS = [(80, "Highly Attractive"), (65, "Attractive"), (50, "Fair"), (35, "Unattractive"), (0, "Avoid")]
DISCOUNT, TERMINAL, YEARS, GROWTH_CAP = 0.09, 0.025, 10, 0.40
HARD_ITEMS = {"4.02": "Restatement warning (item 4.02)", "1.03": "Bankruptcy filing (item 1.03)"}
SOFT_ITEMS = {"4.01": "Auditor change (item 4.01)", "2.06": "Material impairment (item 2.06)",
              "3.01": "Listing deficiency notice (item 3.01)"}


def band(score):
    return next(name for floor, name in BANDS if score >= floor)


def lin(x, lo, hi):
    """0 at lo, 100 at hi, straight line between, clamped. lo > hi scores falling values higher."""
    if x is None:
        return None
    return max(0.0, min(100.0, 100.0 * (x - lo) / (hi - lo)))


def inverse(score, lo, hi):
    """The input value that scores `score` under lin(x, lo, hi)."""
    return lo + score / 100.0 * (hi - lo)


def fmt(v, kind):
    if v is None:
        return "n/a"
    if kind == "pct":
        return ik.pct(v)
    if kind == "pp":
        return "%+.1f pts" % (v * 100)
    if kind == "spct":
        return ik.pct(v, 1, True)
    if kind == "x":
        return "%.1fx" % v
    if kind == "ratio":
        return "%.2f" % v
    if kind == "frac":
        return "%d of %d" % v if isinstance(v, (list, tuple)) else "%.0f%%" % (v * 100)
    if kind == "money":
        return ik.money(v)
    return str(v)


# ---------------------------------------------------------------- data: annual (return on capital)

def by_year(series):
    """{fiscal year label: value}; the label is the calendar year the fiscal year mostly falls in."""
    out = {}
    for r in series:
        end = r["end"]
        y = int(end[:4]) - (1 if int(end[5:7]) <= 3 else 0)  # FY ending Jan-Mar belongs to the prior year
        out[y] = r
    return out


def fy_label(end):
    """Companies name a fiscal year by the year it ends in (NVIDIA's year to January 2026 is FY2026);
    a 52/53-week year that spills into the first days of January keeps the year before."""
    y = int(end[:4])
    return y - 1 if end[5:7] == "01" and int(end[8:10]) <= 7 else y


def annual_data(facts, currency):
    """Fiscal years with revenue, margins, profit, cash flow and return on invested capital."""
    get = {k: by_year(ik.annual_series(facts, k, currency)) for k in
           ("revenue", "gross_profit", "cogs", "operating_income", "income_tax", "pretax_income", "net_income",
            "cfo", "capex", "dna")}
    bs = {k: by_year(ik.instant_series(facts, k, currency)) for k in
          ("equity", "cash", "short_investments", "debt_noncurrent", "debt_current", "debt_total")}
    rows = {}
    for y, rr in get["revenue"].items():
        r = rr["val"]
        if not r:
            continue

        def v(k, src=get):
            return src[k][y]["val"] if y in src[k] else None
        gp = v("gross_profit") if v("gross_profit") is not None else (r - v("cogs") if v("cogs") is not None else None)
        row = {"year": y, "end": rr["end"], "accn": rr["accn"], "fy": fy_label(rr["end"]), "revenue": r,
               "gross_margin": gp / r if gp is not None else None, "op_income": v("operating_income"),
               "net_income": v("net_income"), "cfo": v("cfo"), "capex": v("capex"), "dna": v("dna")}
        row["fcf"] = row["cfo"] - (row["capex"] or 0) if row["cfo"] is not None else None
        row["ebitda"] = row["op_income"] + (row["dna"] or 0) if row["op_income"] is not None else None
        t = None
        if v("income_tax") is not None and v("pretax_income"):
            if v("pretax_income") > 0:
                t = min(max(v("income_tax") / v("pretax_income"), 0.0), 0.35)
        row["tax_rate"] = t if t is not None else 0.21
        if y in bs["equity"]:
            dn, dc, dt = v("debt_noncurrent", bs), v("debt_current", bs), v("debt_total", bs)
            debt = (dn or 0) + (dc or 0) if (dn is not None or dc is not None) else (dt or 0)
            row["invested_capital"] = (v("equity", bs) + debt - (v("cash", bs) or 0) - (v("short_investments", bs) or 0))
        if row["op_income"] is not None:
            row["nopat"] = row["op_income"] * (1 - row["tax_rate"])
        rows[y] = row
    ys = sorted(rows)
    for y in ys:
        r, prev = rows[y], rows.get(y - 1)
        if r.get("nopat") is None or r.get("invested_capital") is None:
            continue
        ic = r["invested_capital"]
        if prev and prev.get("invested_capital") is not None:
            ic = (ic + prev["invested_capital"]) / 2.0
        r["roic"] = r["nopat"] / ic if ic > 0 else None
        r["ic_negative"] = ic <= 0
    return [rows[y] for y in ys]


# ---------------------------------------------------------------- data: quarters, prices, filings

def quarters(facts, currency, splits):
    rev = ik.quarterly_series(facts, "revenue", currency)
    gp = {r["end"]: r["val"] for r in ik.quarterly_series(facts, "gross_profit", currency)}
    cogs = {r["end"]: r["val"] for r in ik.quarterly_series(facts, "cogs", currency)}
    opi = {r["end"]: r["val"] for r in ik.quarterly_series(facts, "operating_income", currency)}
    eps = {r["end"]: r for r in ik.quarterly_series(facts, "eps_diluted", currency, units=("%s/shares" % currency, "USD/shares"))}
    shares = {r["end"]: r for r in ik.quarterly_series(facts, "shares_diluted", currency, units=("shares",), derive_q4=False)}
    out = []
    for r in rev:
        e = r["end"]
        g = gp.get(e, r["val"] - cogs[e] if e in cogs else None)
        q = {"end": e, "fy": r["fy"], "fp": r["fp"], "form": r["form"], "filed": r["filed"], "accn": r["accn"],
             "revenue": r["val"], "gross_margin": g / r["val"] if g is not None and r["val"] else None,
             "op_margin": opi[e] / r["val"] if e in opi and r["val"] else None}
        if e in eps:
            q["eps"] = split_adjust(eps[e]["val"], eps[e]["filed"], splits, per_share=True)
            q["eps_filed"] = eps[e]["first_filed"]
        if e in shares:
            q["shares"] = split_adjust(shares[e]["val"], shares[e]["filed"], splits, per_share=False)
        out.append(q)
    return out


def split_adjust(val, filed, splits, per_share):
    """Restate a figure filed before a stock split onto today's share basis."""
    for when, ratio in splits:
        if when > filed:
            val = val / ratio if per_share else val * ratio
    return val


def year_ago(qs, i):
    """Index of the quarter about a year before qs[i], or None."""
    want = ik._parse_date(qs[i]["end"]) - datetime.timedelta(days=364)
    for j in range(i - 1, -1, -1):
        if abs((ik._parse_date(qs[j]["end"]) - want).days) <= 20:
            return j
    return None


def ttm_eps_series(qs):
    """[(known_from_date, ttm_eps)] for every run of four consecutive quarters."""
    out = []
    for i in range(3, len(qs)):
        four = qs[i - 3:i + 1]
        if any(q.get("eps") is None for q in four) or ik._days(four[0]["end"], four[-1]["end"]) > 300:
            continue
        out.append((max(q.get("eps_filed") or q["filed"] for q in four), sum(q["eps"] for q in four)))
    return out


def pe_history(monthly, ttm_eps):
    """Month-end trailing P/E using the four quarters known by then (only months with profits)."""
    out = []
    j = -1
    for d, c in zip(monthly["dates"], monthly["close"]):
        while j + 1 < len(ttm_eps) and ttm_eps[j + 1][0] <= d:
            j += 1
        if j >= 0 and ttm_eps[j][1] > 0:
            out.append((d, c / ttm_eps[j][1]))
    return out


def earnings_reactions(cik, daily, spy, n=4):
    """Stock minus S&P 500 move from the close before each results 8-K to the close after it."""
    items = [x for x in ik.recent_8k_items(cik, 500) if "2.02" in x["items"]]
    seen, out = set(), []
    for x in items:
        if x["filed"] in seen:
            continue
        seen.add(x["filed"])

        def move(px):
            before = [c for d, c in zip(px["dates"], px["close"]) if d < x["filed"]]
            after = [c for d, c in zip(px["dates"], px["close"]) if d > x["filed"]]
            return after[0] / before[-1] - 1 if before and after else None
        s, m = move(daily), move(spy)
        if s is not None and m is not None:
            out.append({"filed": x["filed"], "stock": s, "market": m, "excess": s - m, "url": x["url"]})
        if len(out) >= n:
            break
    return out, items


GUIDE_PATTERNS = [
    r"revenue (?:is|are) expected to be (?:approximately |about )?\$(?P<a>\d[\d,.]*) ?(?P<u>billion|million)",
    r"revenue (?:is|are) expected to (?:be )?(?:in the range of |between )\$(?P<a>\d[\d,.]*) ?(?P<u1>billion|million)? "
    r"(?:and|to) \$(?P<b>\d[\d,.]*) ?(?P<u>billion|million)",
    r"revenue (?:guidance |outlook )?of (?:approximately )?\$(?P<a>\d[\d,.]*) ?(?P<u1>billion|million)? "
    r"(?:to|-|–) \$(?P<b>\d[\d,.]*) ?(?P<u>billion|million)",
]


def parse_guidance(text):
    """Next-quarter revenue guidance from an earnings release: (midpoint, sentence) or (None, None).
    The sentence is the line that carries it (releases set guidance out as bullets)."""
    if not text:
        return None, None
    for line in text.split("\n"):
        flat = re.sub(r"\s+", " ", line)
        for pat in GUIDE_PATTERNS:
            m = re.search(pat, flat, re.I)
            if m:
                mult = 1e9 if m.group("u").lower() == "billion" else 1e6
                a = float(m.group("a").replace(",", ""))
                b = float(m.group("b").replace(",", "")) if "b" in m.groupdict() and m.group("b") else a
                start = max(flat.rfind(". ", 0, m.start()) + 2, 0)
                end = flat.find(". ", m.end())
                return (a + b) / 2.0 * mult, flat[start:end + 1 if end > 0 else len(flat)].strip(" •·●-")
    return None, None


def guidance_record(cik, qs, earnings_items, n=5):
    """Revenue guidance from the last results releases, and the quarter each one was about."""
    out = []
    for x in earnings_items[:n]:
        try:
            text, url = ik.filing_exhibit(cik, x["accn"])
        except ik.InvestError:
            continue
        mid, sentence = parse_guidance(text)
        reported = [q for q in qs if q["end"] < x["filed"]]
        if not reported:
            continue
        last_end = ik._parse_date(reported[-1]["end"])
        guided_end = last_end + datetime.timedelta(days=91)
        actual = next((q for q in qs if abs((ik._parse_date(q["end"]) - guided_end).days) <= 20), None)
        yago = next((q for q in qs if abs((ik._parse_date(q["end"]) - guided_end + datetime.timedelta(days=364)).days)
                     <= 20), None)
        out.append({"filed": x["filed"], "url": url, "guided": mid, "sentence": sentence,
                    "guided_end": guided_end.isoformat(), "actual": actual["revenue"] if actual else None,
                    "year_ago": yago["revenue"] if yago else None, "text": text})
    return out


def phrase_hits(cik, days, library, with_item=False):
    """8-K catalyst or red-flag phrases filed by this company in the last `days`."""
    end = ik.today()
    start = (end - datetime.timedelta(days=days)).isoformat()
    out = []
    for row in library:
        kind, label, phrases = row[0], row[1], row[3]
        item = row[4] if with_item else None
        if with_item and item:
            continue  # item-coded flags come from the filing list itself
        for ph in phrases:
            try:
                hits = ik.sec_fulltext_hits(ph, "8-K", start, end.isoformat(), max_pages=1, cik=cik)
            except ik.InvestError:
                continue
            for h in hits:
                if h["cik"] == cik and ik.own_words(h) and not any(o["kind"] == kind and o["filed"] == h["filed"] for o in out):
                    out.append({"kind": kind, "label": label, "filed": h["filed"], "url": h["url"], "phrase": ph})
    return sorted(out, key=lambda o: o["filed"], reverse=True)


# ---------------------------------------------------------------- the annual report text

PCT = r"(\d{1,3}(?:\.\d+)?)\s?%"
# "22% of total revenue", "25%, 22% and 19% of our net revenue"
PCT_OF_REV = (r"\d{1,3}(?:\.\d+)?\s?%(?:\s*,?\s*(?:and\s+)?\d{1,3}(?:\.\d+)?\s?%)*\s*,?\s*of\s+"
              r"(?:[a-z'’]+\s+){0,3}(?:revenue|revenues|sales|net sales|net operating revenues)\b")


def customer_concentration(text):
    """Sentences that put a single customer at X% of revenue. Returns (max_pct, quotes, negations)."""
    if not text:
        return None, [], []
    flat = re.sub(r"\s+", " ", text)
    sentences = re.split(r"(?<=[.;])\s+(?=[A-Z(])", flat)
    found, negations = [], []
    for s in sentences:
        low = s.lower()
        if "customer" not in low or "%" not in s or len(s) > 600:
            continue
        if re.search(r"\b(no (single |one |individual |other )?customers?|none of (our|the) customers|"
                     r"not (individually )?(account|represent)|did not (account|represent))", low) \
                and re.search(r"10\s?%|ten percent", low):
            negations.append(s.strip())
            continue
        spans = re.findall(PCT_OF_REV, low)
        if not spans:
            continue
        # groups ("ten largest customers", "top five") are not single-customer reliance
        if re.search(r"\b(top|largest)\s+(two|three|four|five|six|seven|eight|nine|ten|\d+)\b|"
                     r"\b(two|three|four|five|ten|\d+)\s+largest\b|largest customers", low):
            continue
        single = re.search(r"\b(one|a single|single|largest customer|customer [a-z]\b|customers? [a-z],|"
                           r"direct customer|indirect customer|another customer)", low)
        pcts = [float(p) for span in spans for p in re.findall(PCT, span) if 0 < float(p) <= 100]
        if not pcts or not single:
            continue
        val = max(pcts)
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", s)]
        if "respective" in low and len(years) >= 2 and len(pcts) >= 2:
            # "... in 2023, 2024 and 2025 accounted for 25%, 22% and 19%, respectively": take the latest year
            val = pcts[-1] if years == sorted(years) else pcts[0]
        # tables glue onto the sentence that follows them: keep the part near the percentage
        first = re.search(PCT_OF_REV, low).start()
        cut = max(0, first - 220)
        if cut:
            m = re.search(r"(?i)\b(for|in|during|during the|sales|one|our|approximately|direct)\b", s[cut:first])
            cut = cut + m.start() if m else cut
        found.append((val, s[cut:].strip()))
    if not found:
        return None, [], negations[:1]
    found.sort(key=lambda x: -x[0])
    quotes = []
    for p, s in found:  # de-duplicate near-identical sentences
        norm = re.sub(r"\W+", " ", s.lower()).strip()
        if any(norm in q or q in norm for q in (re.sub(r"\W+", " ", x.lower()).strip() for _, x in quotes)):
            continue
        quotes.append((p, s if len(s) < 400 else s[:397] + "..."))
    return quotes[0][0], quotes[:3], negations[:1]


def section_span(text, start_pat, end_pat):
    """The longest stretch from a heading to the next one (the table of contents entry is short)."""
    best, longest = None, -1
    for m in re.finditer(start_pat, text):
        if re.match(r"\s*[.,\"”’']|\s*(of this|above|below|herein)", text[m.end():m.end() + 20]):
            continue  # a cross-reference ("see Item 1A. Risk Factors" above), not the section
        m2 = re.search(end_pat, text[m.end():])
        if not m2:
            continue
        end = m.end() + m2.start()
        if end - m.start() > longest:
            best, longest = (m.start(), end), end - m.start()
    return best


def risk_headings(text, limit=60):
    """The one-sentence headings of the annual report's risk factors (Item 1A)."""
    span = section_span(text or "", r"(?i)\bitem\s*1a\.?\s*:?\s*risk factors\b",
                        r"(?i)\bitem\s*(1b|1c|2)\.?\s*:?\s*(unresolved|cybersecurity|properties)\b")
    if not span:
        return []
    out = []
    for line in text[span[0]:span[1]].split("\n"):
        line = line.strip()
        if 50 <= len(line) <= 350 and line[0].isupper() and line.endswith(".") and line.count(". ") <= 1 \
                and not re.match(r"(?i)item\s", line):
            out.append(line)
    return out[:limit]


def competition_excerpt(text, chars=2500):
    """The 'Competition' part of the business section."""
    if not text:
        return None
    span = section_span(text, r"(?i)\bitem\s*1\.?\s*:?\s*business\b", r"(?i)\bitem\s*1a\.?\s*:?\s*risk factors\b")
    lo, hi = span if span else (0, len(text))
    m = re.search(r"(?m)^\s*(?:Competition|Competitive (?:Environment|Landscape|Position|Conditions)|Our Competition)\s*$",
                  text[lo:hi])
    if m:
        return re.sub(r"\s+", " ", text[lo + m.end():lo + m.end() + chars]).strip()
    pos, top, offset = None, 0, lo
    for para in text[lo:hi].split("\n"):
        n = len(re.findall(r"(?i)compet", para))
        if n > top and len(para) > 80:
            pos, top = offset, n
        offset += len(para) + 1
    return re.sub(r"\s+", " ", text[pos:pos + chars]).strip() if pos is not None else None


# ---------------------------------------------------------------- gather

def yahoo_view(t):
    """The market and consensus figures used for a company, all from Yahoo."""
    q = ik.yahoo_quote([t]).get(t.upper(), {})
    s = ik.yahoo_summary(t)
    sd, ks, fd = s.get("summaryDetail") or {}, s.get("defaultKeyStatistics") or {}, s.get("financialData") or {}
    trend = {r.get("period"): r for r in ((s.get("earningsTrend") or {}).get("trend") or [])}

    def tr(period, part, key):
        return ((trend.get(period) or {}).get(part) or {}).get(key)
    return {
        "price": q.get("regularMarketPrice") or fd.get("currentPrice"), "currency": q.get("currency"),
        "market_cap_usd": ik.quote_market_cap_usd(q) if q else None, "exchange": q.get("fullExchangeName"),
        "short_name": q.get("longName") or q.get("shortName"), "quote_time": q.get("regularMarketTime"),
        "trailing_pe": sd.get("trailingPE") or q.get("trailingPE"), "forward_pe": sd.get("forwardPE") or q.get("forwardPE"),
        "ev_ebitda": ks.get("enterpriseToEbitda"), "ev_sales": ks.get("enterpriseToRevenue"),
        "roe": fd.get("returnOnEquity"), "target_median": fd.get("targetMedianPrice"),
        "analysts": fd.get("numberOfAnalystOpinions"),
        "rev_growth_0y": tr("0y", "revenueEstimate", "growth"), "rev_growth_1y": tr("+1y", "revenueEstimate", "growth"),
        "eps_growth_0y": tr("0y", "earningsEstimate", "growth"), "eps_growth_1y": tr("+1y", "earningsEstimate", "growth"),
        "eps_0y_now": tr("0y", "epsTrend", "current"), "eps_0y_90d": tr("0y", "epsTrend", "90daysAgo"),
        "fy_0y_end": (trend.get("0y") or {}).get("endDate"), "fy_1y_end": (trend.get("+1y") or {}).get("endDate"),
        "history": [{"quarter": h.get("quarter"), "actual": h.get("epsActual"), "estimate": h.get("epsEstimate"),
                     "surprise": h.get("surprisePercent")} for h in ((s.get("earningsHistory") or {}).get("history") or [])],
        "next_earnings": ((s.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate"),
        "next_earnings_estimate": ((s.get("calendarEvents") or {}).get("earnings") or {}).get("isEarningsDateEstimate"),
        "ex_dividend": (s.get("calendarEvents") or {}).get("exDividendDate"),
        "have_summary": bool(s),
    }


def gather(ticker, peers=(), with_text=True, with_news=True):
    cik, name = ik.sec_lookup(ticker)
    facts = ik.sec_facts(cik)
    cur = ik.reporting_currency(facts)
    fx = ik.fx_to_usd(cur)
    y = yahoo_view(ticker)
    if not y.get("price"):
        raise ik.InvestError("Yahoo has no quote for %s." % ticker)
    monthly = ik.yahoo_chart(ticker, "max", "1mo", ttl_hours=24)
    splits = monthly.get("splits", [])
    rows = annual_data(facts, cur)
    if len(rows) < 3:
        raise ik.InvestError("%s has fewer than 3 years of annual data in SEC's feed." % ticker)
    qs = quarters(facts, cur, splits)
    ttm = {k: ik.ttm_value(facts, k, cur) for k in
           ("revenue", "gross_profit", "cogs", "operating_income", "net_income", "cfo", "capex", "dna",
            "buybacks", "dividends_paid")}
    nc = ik.net_cash(facts, cur)
    d = {"ticker": ticker, "cik": cik, "name": name, "currency": cur, "fx": fx, "yahoo": y, "rows": rows,
         "quarters": qs, "ttm": ttm, "net_cash": nc, "splits": splits, "asof": ik.today().isoformat(),
         "quote_date": ik._utc_date(y["quote_time"]) if y.get("quote_time") else ik.today().isoformat()}
    # trailing P/E history (US-dollar filers quoted in dollars only: ADR ratios break per-share maths)
    d["pe_today"] = d["pe_hist"] = None
    eps = ttm_eps_series(qs)
    if eps and cur == "USD" and (y.get("currency") or "USD") == "USD":
        d["ttm_eps"] = eps[-1][1]
        d["pe_hist"] = [(dt, pe) for dt, pe in pe_history(monthly, eps) if dt >= "%d" % (ik.today().year - 10)]
        d["pe_today"] = y["price"] / eps[-1][1] if eps[-1][1] > 0 else None
    # results days and the moves around them
    try:
        daily = ik.yahoo_chart(ticker, "5y", "1d", ttl_hours=12)
        spy = ik.yahoo_chart("SPY", "5y", "1d", ttl_hours=12)
        d["reactions"], earnings_items = earnings_reactions(cik, daily, spy)
    except ik.InvestError:
        d["reactions"], earnings_items = [], []
    d["earnings_8k"] = [{"filed": x["filed"], "url": x["url"]} for x in earnings_items[:8]]
    d["guidance"] = []
    if with_text and earnings_items:
        d["guidance"] = guidance_record(cik, qs, earnings_items)
    # 8-K item codes over the last year, and phrase hits
    items = ik.recent_8k_items(cik, 365)
    d["flags"] = [{"label": lab, "hard": code in HARD_ITEMS, "filed": x["filed"], "url": x["url"]}
                  for x in items for code, lab in list(HARD_ITEMS.items()) + list(SOFT_ITEMS.items()) if code in x["items"]]
    d["catalyst_hits"] = []
    if with_news:
        d["flags"] += [dict(h, hard=h["kind"] == "going_concern") for h in phrase_hits(cik, 365, ik.RED_FLAGS, True)]
        d["catalyst_hits"] = phrase_hits(cik, 180, ik.CATALYSTS)
    # the annual report: customers
    d["customers"] = {"top": None, "quotes": [], "negations": [], "filing": None}
    if with_text:
        text, filing = ik.annual_report_text(cik)
        top, quotes, neg = customer_concentration(text)
        d["customers"] = {"top": top / 100.0 if top is not None else None, "quotes": quotes, "negations": neg,
                          "filing": filing}
    d["peers"] = []
    for p in peers:
        if p.upper() == ticker.upper():
            continue
        pv = yahoo_view(p)
        if pv.get("price"):
            d["peers"].append(dict(pv, ticker=p.upper()))
    return d


# ---------------------------------------------------------------- the metrics and their rules

def metric(key, pillar, label, value, kind, lo, hi, source, rule=None, part=None, note=None):
    return {"key": key, "pillar": pillar, "part": part, "label": label, "value": value, "kind": kind, "lo": lo, "hi": hi,
            "score": lin(value, lo, hi), "source": source, "note": note,
            "rule": rule or "0 at %s, 100 at %s" % (fmt(lo, kind), fmt(hi, kind))}


def source_tags(d):
    last_fy = d["rows"][-1]
    q = d["quarters"][-1] if d["quarters"] else None
    tag_k = "10-K FY%s" % (str(last_fy.get("fy") or last_fy["year"])[-2:])
    tag_q = ("%s %sFY%s" % (q["form"], q["fp"] if q["fp"] != "FY" else "Q4", str(q["fy"])[-2:])) if q and q.get("fy") else tag_k
    if q and q.get("derived"):
        tag_q = "10-K FY%s" % str(fy_label(q["end"]))[-2:]
    when = datetime.date.fromisoformat(d["quote_date"])
    return {"k": tag_k, "q": tag_q, "mkt": "Mkt %s" % when.strftime("%d-%b-%y"), "cons": "Cons", "calc": "Calc",
            "8k": "8-K", "peers": "Peers", "claude": "Claude"}


def own_growth(rows):
    """min(3-year revenue growth a year, last year's growth), capped: the same 'own pace' as the scanner."""
    if len(rows) < 4:
        return None
    three = ik.cagr(rows[-4]["revenue"], rows[-1]["revenue"], 3)
    one = rows[-1]["revenue"] / rows[-2]["revenue"] - 1 if rows[-2]["revenue"] else None
    vals = [v for v in (three, one) if v is not None]
    return min(min(vals), GROWTH_CAP) if vals else None


def build_metrics(d, notes=None):
    notes = notes or {}
    T = source_tags(d)
    y, ttm, rows, qs = d["yahoo"], d["ttm"], d["rows"], d["quarters"]
    fx = d["fx"]
    M = []
    ok = {"fallbacks": []}

    def tv(k):
        return ttm[k]["val"] if ttm.get(k) else None
    rev, opi, ni, cfo, capex = tv("revenue"), tv("operating_income"), tv("net_income"), tv("cfo"), tv("capex")
    gp = tv("gross_profit") if tv("gross_profit") is not None else (rev - tv("cogs") if rev and tv("cogs") else None)
    fcf = cfo - (capex or 0) if cfo is not None else None
    ebitda = opi + (tv("dna") or 0) if opi is not None else None
    mcap = y.get("market_cap_usd")
    netcash = d["net_cash"]["val"] * fx
    ev = mcap - netcash if mcap else None
    d["derived"] = {"rev": rev, "gp": gp, "opi": opi, "ni": ni, "fcf": fcf, "ebitda": ebitda, "ev": ev, "mcap": mcap,
                    "net_cash": netcash}

    # 1. operating quality
    last = rows[-1]
    roic = last.get("roic")
    note = None
    if roic is None and last.get("ic_negative"):
        roic, note = 1.0, "invested capital is negative: profits come with almost no capital tied up"
    M.append(metric("q_roic", "quality", "Return on invested capital, FY%s" % str(last.get("fy") or last["year"])[-2:],
                    roic, "pct", 0.05, 0.30, T["k"], note=note))
    M.append(metric("q_gm", "quality", "Gross margin, last 12 months", gp / rev if gp is not None and rev else None,
                    "pct", 0.20, 0.70, T["q"]))
    M.append(metric("q_om", "quality", "Operating margin, last 12 months", opi / rev if opi is not None and rev else None,
                    "pct", 0.0, 0.35, T["q"]))
    M.append(metric("q_conv", "quality", "Free cash flow / net income, last 12 months",
                    fcf / ni if fcf is not None and ni and ni > 0 else None, "pct", 0.5, 1.0, T["q"]))
    lev = None
    if ebitda and ebitda > 0:
        lev = -d["net_cash"]["val"] / ebitda
    elif d["net_cash"]["val"] >= 0:
        lev = 0.0
    M.append(metric("q_lev", "quality", "Net debt / EBITDA", lev, "x", 4.0, 0.0, T["q"],
                    rule="0 at 4.0x, 100 at no net debt (net cash scores 100)"))
    if lev is not None and lev <= 0:
        M[-1]["display"] = "net cash"

    # 2. recent trend: the latest quarter against a year earlier
    i = len(qs) - 1
    j = year_ago(qs, i) if qs else None
    g_now = g_then = gm_d = om_d = sh_d = None
    if j is not None:
        g_now = qs[i]["revenue"] / qs[j]["revenue"] - 1 if qs[j]["revenue"] else None
        if qs[i]["gross_margin"] is not None and qs[j]["gross_margin"] is not None:
            gm_d = qs[i]["gross_margin"] - qs[j]["gross_margin"]
        if qs[i]["op_margin"] is not None and qs[j]["op_margin"] is not None:
            om_d = qs[i]["op_margin"] - qs[j]["op_margin"]
        if i >= 3:
            j3 = year_ago(qs, i - 3)
            if j3 is not None and qs[j3]["revenue"]:
                g_then = qs[i - 3]["revenue"] / qs[j3]["revenue"] - 1
    sh = [q for q in qs if q.get("shares")]
    if sh:
        k = year_ago(sh, len(sh) - 1)
        if k is not None:
            sh_d = sh[-1]["shares"] / sh[k]["shares"] - 1
    M.append(metric("t_growth", "trend", "Revenue growth, latest quarter vs a year earlier", g_now, "spct", -0.10, 0.30, T["q"]))
    M.append(metric("t_accel", "trend", "Change in that growth rate over three quarters",
                    g_now - g_then if g_now is not None and g_then is not None else None, "pp", -0.15, 0.15, T["calc"]))
    M.append(metric("t_gm", "trend", "Gross margin vs a year earlier", gm_d, "pp", -0.05, 0.05, T["q"]))
    M.append(metric("t_om", "trend", "Operating margin vs a year earlier", om_d, "pp", -0.05, 0.05, T["q"]))
    M.append(metric("t_shares", "trend", "Diluted share count vs a year earlier", sh_d, "spct", 0.04, -0.02, T["q"]))

    # 3. forward growth
    revg = [v for v in (y.get("rev_growth_0y"), y.get("rev_growth_1y")) if v is not None]
    if revg:
        M.append(metric("f_rev", "forward", "Expected revenue growth, this and next fiscal year (average)",
                        sum(revg) / len(revg), "spct", 0.0, 0.30, T["cons"]))
    else:
        og = own_growth(rows)
        ok["fallbacks"].append("no consensus revenue estimates: used the company's own recent growth instead")
        M.append(metric("f_rev", "forward", "Own revenue growth, used in place of consensus", og, "spct", 0.0, 0.30,
                        T["calc"]))
    epsg = y.get("eps_growth_1y") if y.get("eps_growth_1y") is not None else y.get("eps_growth_0y")
    M.append(metric("f_eps", "forward", "Expected EPS growth, next fiscal year", epsg, "spct", 0.0, 0.35, T["cons"]))
    guide = notes.get("guidance") or {}
    g_latest = d["guidance"][0] if d["guidance"] else None
    gval, gyago = guide.get("revenue"), None
    if gval is None and g_latest and g_latest.get("guided"):
        gval, gyago = g_latest["guided"], g_latest.get("year_ago")
    elif gval is not None and g_latest:
        gyago = g_latest.get("year_ago")
    M.append(metric("f_guide", "forward", "Next-quarter revenue guidance vs a year earlier",
                    gval / gyago - 1 if gval and gyago else None, "spct", 0.0, 0.30, T["8k"]))
    graded = [g for g in d["guidance"][1:] if g.get("guided") and g.get("actual")]
    beats = sum(1 for g in graded if g["actual"] >= g["guided"])
    M.append(metric("f_cred", "forward", "Quarters that beat the company's own revenue guidance",
                    beats / float(len(graded)) if graded else None, "frac", 0.0, 1.0, T["8k"],
                    rule="share of guided quarters beaten; 0 at none, 100 at all"))
    if graded:
        M[-1]["display"] = "%d of %d" % (beats, len(graded))
    rev90 = (y["eps_0y_now"] / y["eps_0y_90d"] - 1) if y.get("eps_0y_now") and y.get("eps_0y_90d") and y["eps_0y_90d"] > 0 else None
    M.append(metric("f_rev90", "forward", "Change in this year's consensus EPS, last 90 days", rev90, "spct", -0.10, 0.10,
                    T["cons"]))
    hist = [h for h in y.get("history") or [] if h.get("surprise") is not None][-4:]
    M.append(metric("f_beats", "forward", "Quarters beating consensus EPS, last four",
                    sum(1 for h in hist if h["surprise"] > 0) / float(len(hist)) if hist else None, "frac", 0.0, 1.0,
                    T["cons"], rule="0 at none, 100 at all four"))
    if hist:
        M[-1]["display"] = "%d of %d" % (sum(1 for h in hist if h["surprise"] > 0), len(hist))

    # 4a. valuation, absolute
    fcfy = fcf * fx / mcap if fcf is not None and mcap else None
    fpe = y.get("forward_pe") if y.get("forward_pe") and y["forward_pe"] > 0 else None
    evx = ev / (ebitda * fx) if ev and ebitda and ebitda > 0 else None
    peg = fpe / (epsg * 100) if fpe and epsg and epsg > 0 else None
    implied = ik.implied_growth_fcf(ev, fcf * fx, YEARS, DISCOUNT, TERMINAL) if ev and fcf and fcf > 0 else None
    og = own_growth(rows)
    gap = og - implied if og is not None and implied is not None else None
    d["derived"].update({"fcf_yield": fcfy, "implied_growth": implied, "own_growth": og,
                         "trailing_pe": d.get("pe_today"), "ev_fcf": ev / (fcf * fx) if ev and fcf and fcf > 0 else None,
                         "ev_sales": ev / (rev * fx) if ev and rev else None, "forward_pe": fpe, "ev_ebitda": evx, "peg": peg})
    M.append(metric("v_fcfy", "valuation", "Free cash flow yield", fcfy, "pct", 0.01, 0.07, T["mkt"], part="absolute"))
    M.append(metric("v_fpe", "valuation", "Forward P/E (next fiscal year EPS)", fpe, "x", 45.0, 12.0, T["cons"], part="absolute"))
    M.append(metric("v_evebitda", "valuation", "EV / EBITDA, last 12 months", evx, "x", 35.0, 8.0, T["calc"], part="absolute"))
    M.append(metric("v_peg", "valuation", "PEG: forward P/E / expected EPS growth", peg, "ratio", 2.5, 0.75, T["calc"],
                    part="absolute"))
    M.append(metric("v_gap", "valuation", "Own growth minus the growth the price assumes", gap, "pp", -0.10, 0.10,
                    T["calc"], part="absolute",
                    rule="reverse DCF on free cash flow (9%% required return, %d years, then 2.5%%), as /priced-in; "
                         "0 at -10 pts, 100 at +10 pts" % YEARS))
    # 4b. against peers (Yahoo's figures for every company, so like is compared with like)
    peer_rows = d.get("peers") or []
    d["peer_medians"] = {}
    for key, label in (("trailing_pe", "P/E"), ("forward_pe", "Forward P/E"), ("ev_ebitda", "EV/EBITDA"), ("ev_sales", "EV/Sales")):
        vals = sorted(p[key] for p in peer_rows if p.get(key) and p[key] > 0)
        own = y.get(key)
        if len(vals) >= 2 and own and own > 0:
            med = ik.percentile(vals, 50)
            d["peer_medians"][key] = med
            M.append(metric("p_" + key, "valuation", "%s vs peer median %s" % (label, fmt(med, "x")), 1 - own / med, "spct",
                            -0.40, 0.40, T["peers"], part="peers",
                            rule="discount to the peer median; 0 at a 40% premium, 100 at a 40% discount"))
    if not any(m["part"] == "peers" for m in M):
        ok["fallbacks"].append("no peers given (or too few with data): valuation skips the peer comparison")
    # 4c. against its own history
    ph = d.get("pe_hist") or []
    if d.get("pe_today") and len(ph) >= 36:
        five = sorted(pe for dt, pe in ph if dt >= "%d" % (ik.today().year - 5))
        ten = sorted(pe for _, pe in ph)
        med5 = ik.percentile(five, 50)
        d["pe_stats"] = {"today": d["pe_today"], "med3": ik.percentile(sorted(pe for dt, pe in ph if dt >= "%d" % (ik.today().year - 3)), 50),
                         "med5": med5, "med10": ik.percentile(ten, 50), "hi10": ten[-1], "lo10": ten[0],
                         "pctile": sum(1 for v in ten if v <= d["pe_today"]) / float(len(ten))}
        M.append(metric("h_pe", "valuation", "Trailing P/E / its 5-year median (%s)" % fmt(med5, "x"), d["pe_today"] / med5,
                        "ratio", 1.5, 0.5, T["calc"], part="history", rule="0 at 1.5x its median, 100 at half of it"))
    else:
        d["pe_stats"] = None

    # 5. catalysts and risk skew
    rx = d.get("reactions") or []
    M.append(metric("c_react", "catalysts", "Move vs S&P 500 around the last %d results (average, 2 days)" % len(rx),
                    sum(r["excess"] for r in rx) / len(rx) if rx else None, "spct", -0.08, 0.08, T["mkt"]))
    sy = ((tv("buybacks") or 0) + (tv("dividends_paid") or 0)) * fx / mcap if mcap else None
    M.append(metric("c_yield", "catalysts", "Buybacks + dividends / market cap, last 12 months", sy, "pct", 0.0, 0.05, T["q"]))
    cu = d["customers"]
    if cu.get("filing"):
        top = cu["top"] if cu["top"] is not None else 0.0
        M.append(metric("c_cust", "catalysts", "Largest single customer, share of revenue", top, "pct", 0.40, 0.10, T["k"],
                        rule="from the annual report's own sentences; 100 at 10% or less, 0 at 40%"))
        if cu["top"] is None:
            M[-1]["display"] = "under 10%"
    flags = d.get("flags") or []
    hard = [f for f in flags if f.get("hard")]
    M.append(metric("c_flags", "catalysts", "Red flags in 8-Ks, last 12 months",
                    0 if hard else (0.6 if flags else 1.0), "frac", 0.0, 1.0, T["8k"],
                    rule="100 with none, 60 with a warning sign (auditor change, impairment, lowered guidance...), "
                         "0 with a restatement, going-concern doubt or bankruptcy"))
    M[-1]["display"] = "none" if not flags else ", ".join(sorted(set(f["label"] for f in flags)))
    return M, ok


def pillar_scores(M, overrides=None):
    """{pillar: score} and {valuation part: score} from metric scores (overrides: {key: score})."""
    overrides = overrides or {}

    def sc(m):
        return overrides.get(m["key"], m["score"])
    out, parts = {}, {}
    for key, _, _ in PILLARS:
        if key == "valuation":
            for part, _, w in VALUATION_PARTS:
                vals = [sc(m) for m in M if m["part"] == part and sc(m) is not None]
                if vals:
                    parts[part] = sum(vals) / len(vals)
            if parts:
                tw = sum(w for p, _, w in VALUATION_PARTS if p in parts)
                out[key] = sum(parts[p] * w for p, _, w in VALUATION_PARTS if p in parts) / tw
        else:
            vals = [sc(m) for m in M if m["pillar"] == key and sc(m) is not None]
            if vals:
                out[key] = sum(vals) / len(vals)
    return out, parts


def composite(pillars):
    tw = sum(w for k, _, w in PILLARS if k in pillars)
    return sum(pillars[k] * w for k, _, w in PILLARS if k in pillars) / tw if tw else None


def confidence(M, ok, pillars):
    have = sum(1 for m in M if m["score"] is not None)
    share = have / float(len(M)) if M else 0
    reasons = []
    if share < 1:
        reasons.append("%d of %d inputs could not be sourced" % (len(M) - have, len(M)))
    reasons += ok["fallbacks"]
    if len(pillars) < len(PILLARS):
        reasons.append("a pillar had no data and was left out")
    level = "High" if share >= 0.9 and not ok["fallbacks"] and len(pillars) == len(PILLARS) else \
        "Medium" if share >= 0.7 and len(pillars) == len(PILLARS) else "Low"
    return level, reasons


def sensitivity(M, pillar):
    """What would move a pillar: its weakest input brought up to 75, its strongest dropped to 40."""
    ms = [m for m in M if m["pillar"] == pillar and m["score"] is not None and m["kind"] != "frac"]
    if not ms:
        return None
    base = pillar_scores(M)[0].get(pillar)
    weak = min(ms, key=lambda m: m["score"])
    strong = max(ms, key=lambda m: m["score"])
    lines = []
    if weak["score"] < 75:
        v = inverse(75, weak["lo"], weak["hi"])
        after = pillar_scores(M, {weak["key"]: 75})[0][pillar]
        lines.append("Weakest input: %s at %s. At %s this pillar would score %d." % (
            low_first(weak["label"].split(",")[0]), fmt(weak["value"], weak["kind"]), fmt(v, weak["kind"]), round(after)))
    if strong["score"] > 40 and strong is not weak:
        v = inverse(40, strong["lo"], strong["hi"])
        after = pillar_scores(M, {strong["key"]: 40})[0][pillar]
        lines.append("Strongest: %s at %s. At %s it would fall to %d." % (
            low_first(strong["label"].split(",")[0]), fmt(strong["value"], strong["kind"]), fmt(v, strong["kind"]), round(after)))
    return {"base": base, "text": " ".join(lines)}


def low_first(text):
    """Lower-case the first word unless it is an acronym (EV, PEG, P/E)."""
    return text[0].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


def why_line(M, pillar):
    """A plain summary of a pillar's inputs, best first."""
    ms = sorted([m for m in M if m["pillar"] == pillar and m["score"] is not None], key=lambda m: -m["score"])
    return "; ".join("%s %s" % (m["label"].split(",")[0].split(" (")[0], m.get("display") or fmt(m["value"], m["kind"]))
                     for m in ms) + "."


# ---------------------------------------------------------------- scoring run

def evaluate(d, notes=None):
    M, ok = build_metrics(d, notes)
    pillars, parts = pillar_scores(M)
    score = composite(pillars)
    level, reasons = confidence(M, ok, pillars)
    return {"metrics": M, "pillars": pillars, "parts": parts, "score": score, "band": band(score) if score is not None else "n/a",
            "confidence": level, "confidence_reasons": reasons,
            "sensitivity": {k: sensitivity(M, k) for k, _, _ in PILLARS}}


def history_dir():
    return os.path.join(ik.data_dir(), "scorecards", "history")


def prior_score(ticker):
    """The most recent saved scorecard from an earlier day: (date, score) or None."""
    files = sorted(glob.glob(os.path.join(history_dir(), "%s-*.json" % ticker.upper())))
    today = ik.today().isoformat()
    for path in reversed(files):
        day = os.path.basename(path)[len(ticker) + 1:-5]
        if day < today:
            try:
                with open(path, encoding="utf-8") as f:
                    return day, json.load(f)["score"]
            except (OSError, ValueError, KeyError):
                continue
    return None


def save_history(ticker, ev):
    path = os.path.join(history_dir(), "%s-%s.json" % (ticker.upper(), ik.today().isoformat()))
    ik.write_text(path, json.dumps({"score": ev["score"], "band": ev["band"], "pillars": ev["pillars"]}, indent=2))
    return path


# ---------------------------------------------------------------- text output

def render(d, ev, notes_path=None):
    y = d["yahoo"]
    L = ["SCORECARD: %s (%s)" % (d["ticker"], y.get("short_name") or d["name"])]
    if ev["score"] is None:
        L.append("Not enough data to score.")
        return "\n".join(L)
    L.append("Score %d/100, %s. Confidence %s%s." % (round(ev["score"]), ev["band"], ev["confidence"],
                                                     (": " + "; ".join(ev["confidence_reasons"])) if ev["confidence_reasons"] else ""))
    L.append("Price %s (%s), market cap %s, enterprise value %s." % (
        ik.money(y["price"]), d["quote_date"], ik.money(d["derived"]["mcap"]), ik.money(d["derived"]["ev"])))
    L.append("")
    for key, name, w in PILLARS:
        s = ev["pillars"].get(key)
        L.append("%-34s %3s   (weight %d%%)" % (name, "n/a" if s is None else round(s), w * 100))
        for m in ev["metrics"]:
            if m["pillar"] != key:
                continue
            L.append("    %-62s %12s  %4s  [%s]" % (m["label"][:62], m.get("display") or fmt(m["value"], m["kind"]),
                                                   "-" if m["score"] is None else round(m["score"]), m["source"]))
        if key == "valuation" and ev["parts"]:
            L.append("    parts: " + ", ".join("%s %d" % (n, round(ev["parts"][p])) for p, n, _ in VALUATION_PARTS if p in ev["parts"]))
    L.append("")
    g = d["guidance"]
    if g and g[0].get("sentence"):
        L.append("LATEST GUIDANCE (8-K filed %s): %s" % (g[0]["filed"], g[0]["sentence"]))
    elif g:
        L.append("LATEST RESULTS RELEASE (8-K filed %s): no revenue guidance in dollars found. If the company guides, "
                 "read it with `read %s outlook` and put it in the notes." % (g[0]["filed"], d["ticker"]))
    graded = [x for x in g[1:] if x.get("guided") and x.get("actual")]
    if graded:
        L.append("Guidance record: " + "; ".join("%s guided %s, actual %s" % (x["guided_end"], ik.money(x["guided"]),
                                                                              ik.money(x["actual"])) for x in graded))
    cu = d["customers"]
    if cu.get("quotes"):
        L.append('CUSTOMERS (%s filed %s): "%s"' % (cu["filing"]["form"], cu["filing"]["filed"], cu["quotes"][0][1]))
    for f in d.get("flags") or []:
        L.append("RED FLAG %s: %s %s" % (f["filed"], f["label"], f["url"]))
    for c in d.get("catalyst_hits") or []:
        L.append("CATALYST 8-K %s: %s (\"%s\") %s" % (c["filed"], c["label"], c["phrase"], c["url"]))
    if y.get("next_earnings"):
        L.append("Next results: %s%s." % (ik._utc_date(y["next_earnings"][0]),
                                         " (estimated)" if y.get("next_earnings_estimate") else ""))
    if not d.get("peers"):
        L.append("No peers given: add --peers with 3-5 companies of the same business model and end market.")
    L.append("")
    if not notes_path:
        L.append("NEXT (for Claude): read the filings with `read %s outlook` and `read %s risks`, fill the notes file, "
                 "then run again with --notes." % (d["ticker"], d["ticker"]))
    L.append(ik.DISCLAIMER)
    return "\n".join(L)


# ---------------------------------------------------------------- the report page

PAGE_CSS = """
:root{--bg:#ffffff;--panel:#f6f8f8;--ink:#13181a;--muted:#697276;--line:#e1e6e7;--line-strong:#c8d0d2;
  --accent:#0e6b66;--accent-soft:#d9ecea;--accent-ink:#0a4f4b;--neg:#a3412f;--s1:11.5px;--s2:14px;--s3:30px;--gap:16px}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0f1315;--panel:#161b1e;--ink:#e7edee;
  --muted:#8d979b;--line:#242b2f;--line-strong:#394347;--accent:#4ec9c0;--accent-soft:#15322f;--accent-ink:#7fdcd5;--neg:#e58a76}}
:root[data-theme="dark"]{--bg:#0f1315;--panel:#161b1e;--ink:#e7edee;--muted:#8d979b;--line:#242b2f;--line-strong:#394347;
  --accent:#4ec9c0;--accent-soft:#15322f;--accent-ink:#7fdcd5;--neg:#e58a76}
*{box-sizing:border-box}html,body{overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  font-size:var(--s2);line-height:1.5;font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:28px 22px 40px}
.eyebrow{font-size:var(--s1);font-weight:650;letter-spacing:.11em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
.display{font-size:var(--s3);font-weight:640;letter-spacing:-.02em;line-height:1.1;margin:0}
.strong{font-weight:640}.muted{color:var(--muted)}.small{font-size:var(--s1)}
.topbar{display:flex;justify-content:flex-end;margin-bottom:6px}
.toggle{font:inherit;font-size:var(--s1);font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  background:transparent;border:1px solid var(--line);border-radius:2px;padding:5px 10px;cursor:pointer}
.toggle:hover{color:var(--ink);border-color:var(--line-strong)}.toggle:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
header.masthead{border-top:2px solid var(--ink);padding-top:14px}
.mast-row{display:flex;flex-wrap:wrap;gap:12px 26px;align-items:baseline;justify-content:space-between}
.mast-id{min-width:220px}
.mast-facts{display:grid;grid-template-columns:repeat(4,auto);gap:4px 24px;font-size:var(--s1);text-align:right;margin:0}
.mast-facts dt{color:var(--muted);letter-spacing:.06em;text-transform:uppercase;margin:0}.mast-facts dd{margin:0;font-weight:640}
.filing-strip{margin-top:12px;padding:8px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);display:flex;
  flex-wrap:wrap;gap:6px 22px;font-size:var(--s1);color:var(--muted)}
.filing-strip b{color:var(--ink);font-weight:640}
.tag{display:inline-block;font-size:9.5px;font-weight:640;letter-spacing:.04em;color:var(--muted);background:var(--panel);
  border:1px solid var(--line);border-radius:2px;padding:0 4px;margin-left:5px;vertical-align:1px;white-space:nowrap}
.tag-c{color:var(--accent-ink);background:var(--accent-soft);border-color:transparent}
.verdict{display:grid;grid-template-columns:300px 1fr;gap:28px;align-items:center;margin:22px 0 6px}
.gauge svg{display:block;width:100%;height:auto}
.verdict-meta{display:flex;flex-direction:column;gap:10px}
.band-line{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.band-name{font-size:var(--s3);font-weight:640;letter-spacing:-.02em;color:var(--accent)}
.conf,.delta{font-size:var(--s1);font-weight:640;letter-spacing:.08em;text-transform:uppercase;border:1px solid var(--accent);
  color:var(--accent);border-radius:2px;padding:3px 8px}
.delta{background:var(--accent-soft);border-color:transparent;color:var(--accent-ink)}
.verdict-note{font-size:var(--s1);color:var(--muted);max-width:52ch;margin:0}
.pillars{margin-top:18px;border-top:1px solid var(--line)}
.pillar{display:grid;grid-template-columns:1fr 148px 46px;gap:8px 14px;align-items:center;padding:11px 0;border-bottom:1px solid var(--line)}
.pillar-name{font-weight:640}.pillar-w{font-size:var(--s1);font-weight:640;color:var(--muted);letter-spacing:.05em;margin-left:7px}
.pillar-bar svg{display:block;width:100%;height:auto}
.pillar-score{font-size:20px;font-weight:640;text-align:right;letter-spacing:-.01em}
.pillar-why{grid-column:1 / -1;font-size:var(--s1);color:var(--muted);margin:-2px 0 0}
section{margin-top:26px}
.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;border-bottom:1px solid var(--ink);
  padding-bottom:5px;margin-bottom:12px}
.thesis{margin:0;padding:0;list-style:none}
.thesis li{padding:9px 0 9px 26px;border-bottom:1px solid var(--line);position:relative}
.thesis li:last-child{border-bottom:0}
.thesis li::before{content:attr(data-n);position:absolute;left:0;top:9px;font-size:var(--s1);font-weight:650;color:var(--accent);letter-spacing:.04em}
.scroller{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:520px;font-size:var(--s2)}
th,td{padding:6px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal;padding-left:0}
th:last-child,td:last-child{padding-right:0}
thead th{font-size:var(--s1);font-weight:650;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--line-strong)}
tbody tr:last-child td{border-bottom:0}
td.dir{color:var(--accent);font-weight:640}td.dir-down{color:var(--muted);font-weight:640}td.wrap{white-space:normal;text-align:left}
.val-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--gap)}
.val-col{border:1px solid var(--line);border-top:2px solid var(--accent);padding:12px 13px;background:var(--panel);min-width:0}
.val-col h3{margin:0 0 2px;font-size:var(--s1);font-weight:650;letter-spacing:.08em;text-transform:uppercase}
.val-sub{font-size:var(--s1);color:var(--muted);margin:0 0 9px}
.val-score{font-size:var(--s3);font-weight:640;letter-spacing:-.02em;line-height:1;margin:0 0 10px}
.val-list{margin:0;padding:0;list-style:none;font-size:var(--s1)}
.val-list li{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px solid var(--line)}
.val-list li:last-child{border-bottom:0}.val-list b{font-weight:640}
.val-judge{margin:9px 0 0;font-size:var(--s1);color:var(--muted);border-top:1px solid var(--line-strong);padding-top:8px}
.val-col svg{display:block;width:100%;height:auto;margin-top:8px}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:var(--gap) 30px}
.stack{margin:0;padding:0;list-style:none}
.stack li{padding:8px 0;border-bottom:1px solid var(--line)}.stack li:last-child{border-bottom:0}
.stack .lead{font-weight:640}.stack .note{font-size:var(--s1);color:var(--muted);display:block;margin-top:1px}
.cat{display:grid;grid-template-columns:96px 1fr 84px;gap:4px 12px;align-items:baseline;padding:8px 0;border-bottom:1px solid var(--line)}
.cat:last-child{border-bottom:0}
.cat-date{font-size:var(--s1);font-weight:640;letter-spacing:.03em}
.cat-dir{font-size:var(--s1);font-weight:650;letter-spacing:.05em;text-transform:uppercase;text-align:right}
.dir-pos{color:var(--accent)}.dir-neg{color:var(--neg)}.dir-two{color:var(--muted)}
.cat-note{grid-column:2 / -1;font-size:var(--s1);color:var(--muted)}
.changes{margin:0;padding:0;list-style:none;font-size:var(--s1)}
.changes li{display:grid;grid-template-columns:190px 1fr;gap:12px;padding:6px 0;border-bottom:1px solid var(--line)}
.changes li:last-child{border-bottom:0}.changes b{font-weight:640;font-size:var(--s2)}
details{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;font-size:var(--s1);font-weight:650;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:5px 16px;font-size:var(--s1);color:var(--muted)}
.legend span b{color:var(--ink)}
.links{font-size:var(--s1);margin:8px 0 0;padding-left:18px}.links a{color:var(--accent)}
.disclaimer{margin-top:20px;padding-top:12px;border-top:2px solid var(--ink);font-size:var(--s1);color:var(--muted)}
.disclaimer .hard{color:var(--ink);font-weight:640}
.tip{position:fixed;z-index:9;max-width:260px;padding:6px 8px;border-radius:3px;background:var(--ink);color:var(--bg);
  font-size:var(--s1);pointer-events:none;white-space:pre-line}
.peline{fill:none;stroke:var(--accent);stroke-width:2}.pemed{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 3}
.pegrid{stroke:var(--line);stroke-width:1}.peaxis{fill:var(--muted);font-size:10px}.pedot{fill:var(--accent);stroke:var(--panel);stroke-width:2}
.pehit{fill:transparent}
@media (max-width:720px){.verdict{grid-template-columns:1fr;gap:16px}.gauge{max-width:300px}
  .val-grid,.cols2{grid-template-columns:1fr}.mast-facts{grid-template-columns:repeat(2,auto);text-align:left}
  .mast-row{justify-content:flex-start}.pillar{grid-template-columns:1fr 46px}.pillar-bar{grid-column:1 / -1;order:3}
  .changes li{grid-template-columns:1fr}.cat{grid-template-columns:84px 1fr}.cat-dir{text-align:left;grid-column:2}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@page{size:auto;margin:13mm 12mm}
@media print{html,body{background:#fff;color:#000}.wrap{max-width:none;padding:0}.topbar{display:none}
  section{margin-top:14px;break-inside:avoid}.val-col,.pillar,.cat,.thesis li{break-inside:avoid}table{min-width:0}
  .scroller{overflow:visible}details{display:none}a{text-decoration:none;color:inherit}}
"""

THEME_JS = """
(function(){var root=document.documentElement,btn=document.getElementById('themeBtn');
  var mql=window.matchMedia('(prefers-color-scheme: dark)');
  function apply(t){root.setAttribute('data-theme',t);btn.textContent=t==='dark'?'Light':'Dark';}
  apply(mql.matches?'dark':'light');
  mql.addEventListener('change',function(e){apply(e.matches?'dark':'light');});
  btn.addEventListener('click',function(){apply(root.getAttribute('data-theme')==='dark'?'light':'dark');});})();
"""

E = ik.esc


def tag(t, claude=False):
    return '<span class="tag%s">%s</span>' % (" tag-c" if claude else "", E(t))


def gauge(score):
    R, cx, cy = 120, 160, 140
    arc = math.pi * R
    ticks, labels = [], []
    for s in (35, 50, 65, 80):
        a = math.pi * s / 100.0
        ticks.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>' % (
            cx - 108 * math.cos(a), cy - 108 * math.sin(a), cx - 132 * math.cos(a), cy - 132 * math.sin(a)))
        labels.append('<text x="%.0f" y="%.0f" text-anchor="middle">%d</text>' % (
            cx - 143 * math.cos(a), cy - 143 * math.sin(a) + 4, s))
    return ('<svg viewBox="0 -8 320 176" role="img" aria-label="Attractiveness score %d out of 100, band %s">'
            '<path d="M 40 140 A 120 120 0 0 1 280 140" fill="none" stroke="var(--line)" stroke-width="15"/>'
            '<path d="M 40 140 A 120 120 0 0 1 280 140" fill="none" stroke="var(--accent)" stroke-width="15" '
            'stroke-dasharray="%.1f %.1f"/>'
            '<g stroke="var(--line-strong)" stroke-width="1.5">%s</g>'
            '<g fill="var(--muted)" font-size="10" font-weight="650" font-family="inherit">%s'
            '<text x="34" y="156" text-anchor="middle">0</text><text x="286" y="156" text-anchor="middle">100</text></g>'
            '<text x="160" y="124" text-anchor="middle" fill="var(--ink)" font-size="62" font-weight="650" letter-spacing="-3" '
            'font-family="inherit">%d</text>'
            '<text x="160" y="146" text-anchor="middle" fill="var(--muted)" font-size="11" font-weight="650" letter-spacing="1.6" '
            'font-family="inherit">SCORE / 100</text></svg>'
            % (round(score), band(score), arc * score / 100.0, arc + 1, "".join(ticks), "".join(labels), round(score)))


def bar(score):
    w = 300 * (score or 0) / 100.0
    return ('<svg viewBox="0 0 300 8" aria-hidden="true"><rect width="300" height="8" fill="var(--line)"/>'
            '<rect width="%.0f" height="8" fill="var(--accent)"/></svg>' % w)


def pe_chart(ph, med5):
    """Ten years of month-end trailing P/E with the 5-year median, for the history column."""
    if len(ph) < 12:
        return ""
    W, H, ml, mr, mt, mb = 260, 120, 30, 6, 8, 18
    vals = [v for _, v in ph]
    lo, hi = 0.0, min(max(vals), med5 * 4) * 1.05
    n = len(ph)

    def x(i):
        return ml + i / float(n - 1) * (W - ml - mr)

    def y(v):
        return mt + (H - mt - mb) * (1 - (min(v, hi) - lo) / (hi - lo))
    b = []
    for t in ik.nice_ticks(lo, hi, 3):
        b.append('<line class="pegrid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/><text class="peaxis" x="%d" y="%.1f" '
                 'text-anchor="end">%dx</text>' % (ml, W - mr, y(t), y(t), ml - 4, y(t) + 3, t))
    b.append('<line class="pemed" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, W - mr, y(med5), y(med5)))
    b.append('<polyline class="peline" points="%s"/>' % " ".join("%.1f,%.1f" % (x(i), y(v)) for i, (_, v) in enumerate(ph)))
    b.append('<circle class="pedot" cx="%.1f" cy="%.1f" r="4"/>' % (x(n - 1), y(vals[-1])))
    step = max(1, n // 40)
    for i in range(0, n, step):
        b.append('<rect class="pehit" x="%.1f" y="%d" width="%.1f" height="%d"%s/>' % (
            x(i) - 3, mt, (W - ml - mr) / float(n) * step + 1, H - mt - mb, ik.tip("%s: %.1fx" % (ph[i][0][:7], ph[i][1]))))
    b.append('<text class="peaxis" x="%d" y="%d">%s</text><text class="peaxis" x="%d" y="%d" text-anchor="end">%s</text>' % (
        ml, H - 4, ph[0][0][:4], W - mr, H - 4, ph[-1][0][:4]))
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="Trailing P/E by month, %s to %s, with its 5-year median">%s</svg>'
            % (W, H, ph[0][0][:7], ph[-1][0][:7], "".join(b)))


def nice_date(s):
    try:
        return datetime.date.fromisoformat(s[:10]).strftime("%-d %b %Y") if os.name != "nt" else \
            datetime.date.fromisoformat(s[:10]).strftime("%#d %b %Y")
    except (ValueError, TypeError):
        return s or ""


def lead_text(item):
    if isinstance(item, str):
        parts = item.split(". ", 1)
        return (parts[0] + ".", parts[1] if len(parts) > 1 else ""), None
    return (item.get("lead", ""), item.get("text") or item.get("note", "")), item.get("source")


def catalysts_list(d, notes):
    """Dated catalysts: Claude's from the notes, then what the data shows (next results, ex-dividend, 8-Ks)."""
    y, T = d["yahoo"], source_tags(d)
    out = []
    for c in notes.get("catalysts") or []:
        out.append({"date": c.get("date", ""), "sort": c.get("sort") or c.get("date", ""), "title": c.get("title", ""),
                    "dir": (c.get("direction") or "two-sided").lower(), "note": c.get("note", ""),
                    "tag": c.get("source") or T["claude"], "claude": not c.get("source")})
    if y.get("next_earnings"):
        day = ik._utc_date(y["next_earnings"][0])
        if not any(day in (c["sort"] or "") for c in out):
            rx = d.get("reactions") or []
            note = ""
            if rx:
                note = "Last %d results, most recent first: %s against the S&P 500 over two days." % (
                    len(rx), ", ".join(ik.pct(r["excess"], 1, True) for r in rx))
            out.append({"date": nice_date(day), "sort": day, "dir": "two-sided", "note": note, "tag": T["mkt"], "claude": False,
                        "title": "Results%s" % (" (date estimated)" if y.get("next_earnings_estimate") else ", confirmed")})
    if y.get("ex_dividend") and ik._utc_date(y["ex_dividend"]) >= ik.today().isoformat():
        day = ik._utc_date(y["ex_dividend"])
        out.append({"date": nice_date(day), "sort": day, "dir": "positive", "title": "Ex-dividend date", "note": "",
                    "tag": T["mkt"], "claude": False})
    for c in (d.get("catalyst_hits") or [])[:4]:
        out.append({"date": nice_date(c["filed"]), "sort": c["filed"], "dir": "positive", "title": c["label"] + " (8-K)",
                    "note": 'The filing says "%s".' % c["phrase"], "tag": T["8k"], "claude": False, "url": c["url"]})
    for f in d.get("flags") or []:
        out.append({"date": nice_date(f["filed"]), "sort": f["filed"], "dir": "negative", "title": f["label"] + " (8-K)",
                    "note": "", "tag": T["8k"], "claude": False, "url": f["url"]})
    return sorted(out, key=lambda c: c["sort"] or "9999")


def key_metrics_rows(d, T):
    rows, qs, y = d["rows"], d["quarters"], d["yahoo"]
    out = []
    last, prev = rows[-1], rows[-2]
    three = rows[-4] if len(rows) >= 4 else rows[0]
    fy = "FY%s" % str(last.get("fy") or last["year"])[-2:]

    def ch(a, b):
        return a / b - 1 if a is not None and b else None

    def dirn(a, b, good_up=True, word=None):
        if a is None or b is None:
            return "", "n/a"
        up = a > b
        cls = "dir" if up == good_up else "dir-down"
        return cls, ("▲" if up else "▼") + ((" " + word[0 if up else 1]) if word else "")
    if len(qs) >= 8:
        t_now = sum(q["revenue"] for q in qs[-4:])
        t_then = sum(q["revenue"] for q in qs[-8:-4])
        out.append(("Revenue, last 12 months", T["q"], ik.money(t_now), ik.pct(ch(t_now, t_then), 1, True)) + dirn(t_now, three["revenue"]))
    for label, key, kind in (("Revenue", "revenue", "money"), ("Net income", "net_income", "money"),
                             ("EBITDA", "ebitda", "money"), ("Free cash flow", "fcf", "money")):
        a, b, c = last.get(key), prev.get(key), three.get(key)
        out.append(("%s, %s" % (label, fy), T["k"], ik.money(a), ik.pct(ch(a, b), 1, True) if b and b > 0 else "n/a") + dirn(a, c))
    if qs and qs[-1].get("gross_margin") is not None:
        j = year_ago(qs, len(qs) - 1)
        gm_then = qs[j]["gross_margin"] if j is not None else None
        out.append(("Gross margin, latest quarter", T["q"], ik.pct(qs[-1]["gross_margin"]),
                    fmt(qs[-1]["gross_margin"] - gm_then, "pp") if gm_then is not None else "n/a")
                   + dirn(qs[-1]["gross_margin"], gm_then))
    sh = [q for q in qs if q.get("shares")]
    if len(sh) >= 2:
        k = year_ago(sh, len(sh) - 1)
        old = [q for q in sh if q["end"] <= three["end"]]
        out.append(("Diluted shares, latest quarter", T["q"], "%.2fB" % (sh[-1]["shares"] / 1e9),
                    ik.pct(ch(sh[-1]["shares"], sh[k]["shares"]), 1, True) if k is not None else "n/a")
                   + dirn(sh[-1]["shares"], old[-1]["shares"] if old else None, good_up=False, word=("growing", "shrinking")))
    cu = d["customers"]
    if cu.get("filing"):
        out.append(("Largest single customer, share of revenue", T["k"],
                    ik.pct(cu["top"], 0) if cu["top"] is not None else "under 10%", "", "", ""))
    return out


def report(d, ev, notes, prior=None):
    T = source_tags(d)
    y, der = d["yahoo"], d["derived"]
    M = ev["metrics"]
    score = ev["score"]
    q = d["quarters"][-1] if d["quarters"] else None
    claude_tag = tag("Claude", True)
    # masthead
    facts = [("Price", ik.money(y["price"]) if y["price"] < 1000 else "$" + "{:,.0f}".format(y["price"]), T["mkt"]),
             ("Market cap", ik.money(der["mcap"], 2), T["mkt"]), ("Enterprise value", ik.money(der["ev"], 2), T["calc"]),
             ("Net cash" if der["net_cash"] >= 0 else "Net debt", ik.money(abs(der["net_cash"])), T["q"])]
    mast = "".join('<div><dt>%s %s</dt><dd>%s</dd></div>' % (E(k), tag(t), E(v)) for k, v, t in facts)
    strip = ['<span>Price as of <b>%s close</b>%s</span>' % (nice_date(d["quote_date"]), tag(T["mkt"]))]
    if q:
        strip.append('<span>Latest filing <b>%s, quarter ended %s</b>%s</span>' % (E(T["q"].replace("FY", " FY")), nice_date(q["end"]), tag(T["q"])))
    strip.append('<span>Fiscal year ended <b>%s</b>%s</span>' % (nice_date(d["rows"][-1]["end"]), tag(T["k"])))
    exch = y.get("exchange") or ""
    head = ('<header class="masthead"><div class="mast-row"><div class="mast-id"><p class="eyebrow">Attractiveness Scorecard</p>'
            '<h1 class="display">%s</h1><p class="small muted" style="margin:4px 0 0">%s%s · Reporting currency %s</p></div>'
            '<dl class="mast-facts">%s</dl></div><div class="filing-strip">%s</div></header>'
            % (E(notes.get("company_name") or y.get("short_name") or d["name"]), E(d["ticker"]), (" · " + E(exch)) if exch else "", E(d["currency"]),
               mast, "".join(strip)))
    # verdict
    delta = ""
    if prior:
        diff = round(score) - round(prior[1])
        delta = '<span class="delta">%s vs %s</span>' % ("%+d" % diff if diff else "unchanged", nice_date(prior[0]))
    reasons = ev["confidence_reasons"]
    conf_note = ("Confidence is %s%s." % (ev["confidence"], (": " + "; ".join(reasons)) if reasons else
                                          ": every input was sourced"))
    verdict = ('<div class="verdict"><div class="gauge">%s</div><div class="verdict-meta"><div class="band-line">'
               '<span class="band-name">%s</span><span class="conf">Confidence: %s</span>%s</div>'
               '<p class="verdict-note">Bands: 80+ Highly Attractive · 65-79 Attractive · 50-64 Fair · 35-49 Unattractive · '
               'under 35 Avoid. %s</p></div></div>' % (gauge(score), E(ev["band"]), E(ev["confidence"]), delta, E(conf_note)))
    # pillars
    whys = notes.get("why") or {}
    prow = []
    for key, name, w in PILLARS:
        s = ev["pillars"].get(key)
        why = whys.get(key)
        why_html = (E(why) + " " + claude_tag) if why else E(why_line(M, key))
        prow.append('<div class="pillar"><div><span class="pillar-name">%s</span><span class="pillar-w">%d%%</span></div>'
                    '<div class="pillar-bar">%s</div><div class="pillar-score">%s</div><p class="pillar-why">%s</p></div>'
                    % (E(name), w * 100, bar(s), "n/a" if s is None else round(s), why_html))
    body = [head, verdict, '<div class="pillars">%s</div>' % "".join(prow)]
    # thesis
    if notes.get("thesis"):
        lis = []
        for i, t in enumerate(notes["thesis"][:4]):
            (lead, rest), src = lead_text(t)
            lis.append('<li data-n="%02d"><span class="strong">%s</span> %s%s</li>' % (
                i + 1, E(lead), E(rest), tag(src) if src else claude_tag))
        body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Thesis</p>'
                    '<span class="small muted">Claude\'s reading of the numbers</span></div><ul class="thesis">%s</ul></section>'
                    % "".join(lis))
    # key metrics
    km = "".join('<tr><td>%s%s</td><td>%s</td><td>%s</td><td class="%s">%s</td></tr>' % (
        E(a), tag(t), E(v), E(c), cls, E(dr)) for a, t, v, c, cls, dr in key_metrics_rows(d, T))
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Key metrics</p>'
                '<span class="small muted">▲ up · ▼ down, against three fiscal years ago</span></div><div class="scroller"><table>'
                '<thead><tr><th>Metric</th><th>Latest</th><th>Change in a year</th><th>3-yr direction</th></tr></thead>'
                '<tbody>%s</tbody></table></div></section>' % km)
    # five-year trend
    tr = []
    rows = d["rows"][-5:]
    for i, r in enumerate(rows):
        prev = d["rows"][d["rows"].index(r) - 1] if d["rows"].index(r) > 0 else None
        g = r["revenue"] / prev["revenue"] - 1 if prev and prev["revenue"] else None
        tr.append('<tr><td>FY%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            str(r.get("fy") or r["year"])[-2:], ik.money(r["revenue"], 2), ik.pct(g, 1, True) if g is not None else "n/a",
            ik.money(r.get("fcf"), 2), ik.pct(r["fcf"] / r["revenue"]) if r.get("fcf") is not None else "n/a"))
    if der["rev"] and der["fcf"] is not None and q and q["end"] > d["rows"][-1]["end"]:
        tr.append('<tr><td><span class="strong">Last 12 months to %s</span></td><td><span class="strong">%s</span></td><td></td>'
                  '<td><span class="strong">%s</span></td><td><span class="strong">%s</span></td></tr>' % (
                      datetime.date.fromisoformat(q["end"]).strftime("%b %Y"), ik.money(der["rev"], 2), ik.money(der["fcf"], 2),
                      ik.pct(der["fcf"] / der["rev"])))
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Five-year trend</p><span class="small muted">'
                'Revenue and free cash flow %s · margins %s</span></div><div class="scroller"><table><thead><tr><th>Fiscal year</th>'
                '<th>Revenue</th><th>Growth</th><th>Free cash flow</th><th>FCF margin</th></tr></thead><tbody>%s</tbody></table>'
                '</div></section>' % (tag(T["k"]), tag(T["calc"]), "".join(tr)))
    # valuation
    cols = []
    judge = notes.get("valuation") or {}
    for part, name, w in VALUATION_PARTS:
        ms = [m for m in M if m["part"] == part]
        s = ev["parts"].get(part)
        items = ["<li><span>%s</span><b>%s</b></li>" % (E(m["label"].replace(" (next fiscal year EPS)", "").split(", last")[0]),
                                                         E(m.get("display") or fmt(m["value"], m["kind"]))) for m in ms]
        sub = "%d points" % w
        extra = ""
        if part == "absolute":
            for label, v, k in (("Trailing P/E", der.get("trailing_pe"), "x"), ("EV / free cash flow", der.get("ev_fcf"), "x"),
                                ("EV / sales", der.get("ev_sales"), "x"), ("Growth the price assumes", der.get("implied_growth"), "pct"),
                                ("Own growth (3-yr or last year, lower)", der.get("own_growth"), "pct")):
                if v is not None:
                    items.append('<li><span class="muted">%s</span><b>%s</b></li>' % (E(label), E(fmt(v, k))))
            sub += " " + tag(T["mkt"]) + tag(T["cons"])
        elif part == "peers":
            peers = d.get("peers") or []
            sub += " · %s %s" % (E(", ".join(p["ticker"] for p in peers)) or "none given", tag(T["peers"]))
        else:
            st = d.get("pe_stats")
            if st:
                for label, v in (("3-yr median", st["med3"]), ("10-yr median", st["med10"]), ("10-yr high", st["hi10"]),
                                 ("10-yr low", st["lo10"])):
                    items.append('<li><span class="muted">%s</span><b>%s</b></li>' % (label, fmt(v, "x")))
                items.append('<li><span class="muted">Percentile in its 10-year range</span><b>%d%%</b></li>' % round(st["pctile"] * 100))
                extra = pe_chart(d["pe_hist"], st["med5"])
            sub += " · trailing P/E " + tag(T["calc"])
        j = judge.get(part)
        cols.append('<div class="val-col"><h3>%s · %s</h3><p class="val-sub">%s</p><p class="val-score">%s</p>'
                    '<ul class="val-list">%s</ul>%s%s</div>' % (
                        "4" + "abc"[[p for p, _, _ in VALUATION_PARTS].index(part)], E(name), sub,
                        "n/a" if s is None else round(s), "".join(items) or "<li><span>No data</span></li>", extra,
                        ('<p class="val-judge">%s %s</p>' % (E(j), claude_tag)) if j else ""))
    vs = ev["pillars"].get("valuation")
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Valuation · 25%% weight · cheap scores high'
                '</p><span class="small muted">Composite %s</span></div><div class="val-grid">%s</div></section>'
                % ("n/a" if vs is None else round(vs), "".join(cols)))
    if d.get("peers"):
        pr = [('<tr><td><span class="strong">%s</span></td>' % E(d["ticker"])) + "".join(
            "<td>%s</td>" % fmt(y.get(k), kk) for k, kk in (("trailing_pe", "x"), ("forward_pe", "x"), ("ev_ebitda", "x"),
                                                            ("ev_sales", "x"), ("rev_growth_1y", "spct"), ("roe", "pct"))) + "</tr>"]
        for p in d["peers"]:
            pr.append("<tr><td>%s</td>" % E(p["ticker"]) + "".join(
                "<td>%s</td>" % fmt(p.get(k), kk) for k, kk in (("trailing_pe", "x"), ("forward_pe", "x"), ("ev_ebitda", "x"),
                                                                ("ev_sales", "x"), ("rev_growth_1y", "spct"), ("roe", "pct"))) + "</tr>")
        reason = notes.get("peer_reason")
        body.append('<details><summary>Peer table</summary>%s<div class="scroller"><table><thead><tr><th>Company</th><th>P/E</th>'
                    '<th>Fwd P/E</th><th>EV/EBITDA</th><th>EV/Sales</th><th>Rev growth next FY</th><th>ROE</th></tr></thead>'
                    '<tbody>%s</tbody></table></div></details>' % (
                        ('<p class="small muted">%s %s</p>' % (E(reason), claude_tag)) if reason else "", "".join(pr)))
    # forward growth
    left, right = [], []
    for dr in notes.get("drivers") or []:
        (lead, rest), src = lead_text(dr)
        left.append('<li><span class="lead">%s</span><span class="note">%s%s</span></li>' % (E(lead), E(rest), tag(src) if src else claude_tag))
    g = d["guidance"]
    guide = notes.get("guidance") or {}
    if guide.get("text") or (g and g[0].get("sentence")):
        right.append('<li><span class="lead">Company guidance</span><span class="note">%s%s</span></li>' % (
            E(guide.get("text") or g[0]["sentence"]), tag(T["8k"])))
    graded = [x for x in g[1:] if x.get("guided") and x.get("actual")]
    if graded:
        right.append('<li><span class="lead">Guidance credibility</span><span class="note">%s%s</span></li>' % (E("; ".join(
            "quarter to %s: %s guided, %s reported" % (nice_date(x["guided_end"]), ik.money(x["guided"]), ik.money(x["actual"]))
            for x in graded) + "."), tag(T["8k"])))
    if y.get("rev_growth_0y") is not None or y.get("eps_growth_1y") is not None:
        right.append('<li><span class="lead">Consensus</span><span class="note">Revenue growth %s this fiscal year and %s next; EPS '
                     'growth %s next fiscal year%s.%s</span></li>' % (
                         fmt(y.get("rev_growth_0y"), "spct"), fmt(y.get("rev_growth_1y"), "spct"), fmt(y.get("eps_growth_1y"), "spct"),
                         (", from %d analysts" % y["analysts"]) if y.get("analysts") else "", tag(T["cons"])))
    if notes.get("disagreement"):
        right.append('<li><span class="lead">Where guidance and consensus disagree</span><span class="note">%s%s</span></li>'
                     % (E(notes["disagreement"]), claude_tag))
    fs = ev["pillars"].get("forward")
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Forward growth · named drivers</p>'
                '<span class="small muted">Pillar 3 · score %s</span></div><div class="cols2"><ul class="stack">%s</ul>'
                '<ul class="stack">%s</ul></div></section>' % ("n/a" if fs is None else round(fs),
                                                               "".join(left) or '<li><span class="note">No drivers written.</span></li>',
                                                               "".join(right)))
    # catalysts
    cats = catalysts_list(d, notes)
    cs = ev["pillars"].get("catalysts")
    dir_cls = {"positive": "dir-pos", "positive skew": "dir-pos", "negative": "dir-neg", "two-sided": "dir-two"}
    ch = "".join('<div class="cat"><span class="cat-date">%s</span><span class="strong">%s%s%s</span><span class="cat-dir %s">%s</span>'
                 '<span class="cat-note">%s</span></div>' % (
                     E(c["date"]), E(c["title"]), tag(c["tag"], c["claude"]),
                     (' <a class="small" href="%s">filing</a>' % E(c["url"])) if c.get("url") else "",
                     dir_cls.get(c["dir"], "dir-two"), E(c["dir"]), E(c["note"])) for c in cats)
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Catalyst timeline</p><span class="small muted">'
                'Pillar 5 · score %s</span></div>%s</section>' % ("n/a" if cs is None else round(cs), ch or
                                                                  '<p class="small muted">No dated catalysts found.</p>'))
    # risks
    if notes.get("risks"):
        rl = []
        for i, r in enumerate(notes["risks"][:3]):
            (lead, rest), src = lead_text(r)
            rl.append('<li><span class="lead">%02d · %s</span><span class="note">%s%s</span></li>' % (
                i + 1, E(lead), E(rest), tag(src) if src else claude_tag))
        body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Top three company-specific risks</p>'
                    '<span class="small muted">From the risk factors and filings, boilerplate left out</span></div>'
                    '<ul class="stack">%s</ul></section>' % "".join(rl))
    # what would change each score
    wc = notes.get("what_would_change") or {}
    li = []
    for key, name, _ in PILLARS:
        s = ev["pillars"].get(key)
        if s is None:
            continue
        txt = wc.get(key)
        auto = (ev["sensitivity"].get(key) or {}).get("text")
        li.append("<li><b>%s · %d</b><span>%s</span></li>" % (
            E(name.split(" &")[0]), round(s), (E(txt) + " " + claude_tag) if txt else E(auto or "")))
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">What would change each score</p></div>'
                '<ul class="changes">%s</ul></section>' % "".join(li))
    if notes.get("change_mind"):
        body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">What would change our mind</p>%s</div>'
                    '<p style="margin:0">%s</p></section>' % (claude_tag, E(notes["change_mind"])))
    # every input and its rule
    ir = "".join('<tr><td class="wrap">%s%s</td><td>%s</td><td>%s</td><td class="wrap small muted">%s</td></tr>' % (
        E(m["label"]), tag(m["source"]), E(m.get("display") or fmt(m["value"], m["kind"])),
        "n/a" if m["score"] is None else round(m["score"]), E(m["rule"] + ("; " + m["note"] if m.get("note") else "")))
        for m in M)
    comp = " + ".join("%.2f(%d)" % (w, round(ev["pillars"][k])) for k, _, w in PILLARS if k in ev["pillars"])
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">How the score is built</p></div>'
                '<p class="small muted" style="margin:0">Each input scores 0-100 on a straight line between the two values in its '
                'rule. A pillar is the average of its inputs (valuation: absolute 10, peers 8, history 7). Composite = %s = %.1f.'
                '</p><details><summary>Every input, its value and its rule</summary><div class="scroller"><table><thead><tr>'
                '<th>Input</th><th>Value</th><th>Score</th><th>Rule</th></tr></thead><tbody>%s</tbody></table></div></details></section>'
                % (E(comp), score, ir))
    # sources
    links = [("SEC filings for %s" % d["name"], ik.cik_link(d["cik"]))]
    cu = d["customers"]
    if cu.get("filing"):
        links.append(("Annual report (%s, filed %s)" % (cu["filing"]["form"], cu["filing"]["filed"]), cu["filing"]["doc_url"]))
    if g:
        links.append(("Latest results release (8-K, filed %s)" % g[0]["filed"], g[0]["url"]))
    links.append(("Market data and consensus: Yahoo Finance", "https://finance.yahoo.com/quote/%s" % ik.yahoo_symbol(d["ticker"])))
    legend = [(T["k"], "Annual report, fiscal year ended %s" % nice_date(d["rows"][-1]["end"]))]
    if q and T["q"] != T["k"]:
        legend.append((T["q"], "Quarterly report, quarter ended %s" % nice_date(q["end"])))
    legend += [(T["8k"], "Results releases and event filings"), (T["mkt"], "Market data, %s close" % nice_date(d["quote_date"])),
               (T["cons"], "Sell-side consensus via Yahoo Finance, an estimate"), (T["peers"], "Peer figures, Yahoo Finance"),
               (T["calc"], "Worked out from the tagged figures"), ("Claude", "Claude's reading of the filings, not a computed figure")]
    body.append('<section><div class="section-head"><p class="eyebrow" style="margin:0">Source legend</p></div><div class="legend">%s'
                '</div><ul class="links">%s</ul></section>' % (
                    "".join("<span><b>%s</b> - %s</span>" % (E(a), E(b)) for a, b in legend),
                    "".join('<li><a href="%s">%s</a></li>' % (E(u), E(t)) for t, u in links)))
    body.append('<div class="disclaimer"><p style="margin:0 0 8px"><span class="hard">Educational content only. Not financial '
                'advice and not a recommendation to buy or sell any security.</span></p><p style="margin:0 0 8px">The scores are a '
                'framework for organising evidence, not a recommendation. They come from public filings and free market data, '
                'which can lag or be wrong, and from thresholds that are judgment calls (each one is printed above). Verify '
                'independently and consult a licensed professional before investing.</p><p style="margin:0">Prepared %s · '
                'Investor Skills by WallStreetGPT</p></div>' % nice_date(ik.today().isoformat()))
    title = "%s Scorecard" % d["ticker"]
    desc = "%s scores %d/100 (%s) on five weighted pillars: quality, trend, forward growth, valuation, catalysts and risk." % (
        d["ticker"], round(score), ev["band"])
    # a page fragment, like every Investor Skills report: the Artifact publisher adds the document
    # skeleton, and browsers open it as it is
    return ('<meta charset="utf-8">\n<title>%s</title>\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta name="description" content="%s">\n<style>%s</style>\n<div class="wrap">\n'
            '<div class="topbar"><button class="toggle" id="themeBtn" type="button">Dark</button></div>\n%s\n</div>\n'
            '<script>%s%s</script>\n' % (E(title), E(desc), PAGE_CSS, "\n".join(body), THEME_JS, ik.REPORT_JS))


def card(d, ev):
    """The verdict as a standalone image (for video): score, band and the five pillars."""
    W, H = 960, 540
    b = ['<text class="title" x="60" y="62">%s</text>' % E(d["ticker"]),
         '<text class="sub" x="60" y="90">Attractiveness scorecard · %s</text>' % E(nice_date(ik.today().isoformat())),
         '<text class="title" x="60" y="200" style="font-size:96px">%d</text>' % round(ev["score"]),
         '<text class="sub" x="62" y="236">%s · confidence %s</text>' % (E(ev["band"]), E(ev["confidence"].lower()))]
    yy = 140
    for key, name, w in PILLARS:
        s = ev["pillars"].get(key) or 0
        b.append('<text class="lbl" x="420" y="%d">%s · %d%%</text>' % (yy, E(name), w * 100))
        b.append('<rect class="grid" x="420" y="%d" width="420" height="10" rx="2" style="fill:#2c2c2a"/>' % (yy + 10))
        b.append('<rect class="s1" x="420" y="%d" width="%.0f" height="10" rx="2"/>' % (yy + 10, 420 * s / 100.0))
        b.append('<text class="lbl" x="900" y="%d" text-anchor="end">%d</text>' % (yy + 20, round(s)))
        yy += 64
    b.append('<text class="note" x="60" y="%d">From SEC filings and free market data. A framework for evidence, not advice.</text>' % (H - 22))
    return ik.svg_wrap(W, H, "\n".join(b))


# ---------------------------------------------------------------- read: filings for Claude

def read(ticker, what):
    cik, name = ik.sec_lookup(ticker)
    if what == "outlook":
        items = [x for x in ik.recent_8k_items(cik, 400) if "2.02" in x["items"]]
        if not items:
            return "No results 8-K (item 2.02) in the last 400 days."
        text, url = ik.filing_exhibit(cik, items[0]["accn"])
        if not text:
            return "The results 8-K filed %s has no press-release exhibit: %s" % (items[0]["filed"], items[0]["url"])
        flat = re.sub(r"[ \t]+", " ", text)
        out = ["RESULTS RELEASE, 8-K filed %s: %s" % (items[0]["filed"], url), "", flat[:3500].strip()]
        m = re.search(r"(?im)^\s*(outlook|guidance|business outlook|financial outlook)\b.*$", flat)
        if m and m.start() > 3500:
            out += ["", "...", flat[m.start():m.start() + 2500].strip()]
        return "\n".join(out)
    text, filing = ik.annual_report_text(cik)
    if not text:
        return "Could not read the latest annual report."
    src = "%s filed %s: %s" % (filing["form"], filing["filed"], filing["doc_url"])
    if what == "risks":
        hs = risk_headings(text)
        return "RISK FACTOR HEADINGS, %s\n\n%s" % (src, "\n".join("- " + h for h in hs) if hs else
                                                  "No headings found; read Item 1A in the filing.")
    if what == "customers":
        top, quotes, neg = customer_concentration(text)
        lines = ['"%s"' % q for _, q in quotes] + ['"%s"' % q for q in neg]
        return "CUSTOMERS, %s\n\n%s" % (src, "\n\n".join(lines) or "No customer at 10% or more of revenue disclosed.")
    if what == "competition":
        return "COMPETITION, %s\n\n%s" % (src, competition_excerpt(text) or "No competition section found.")
    return "read what? outlook, risks, customers or competition"


NOTES_TEMPLATE = {
    "peers": [], "peer_reason": "",
    "thesis": [{"lead": "", "text": ""}, {"lead": "", "text": ""}, {"lead": "", "text": ""}],
    "drivers": [{"lead": "", "text": "", "source": ""}],
    "guidance": {"text": "", "revenue": None},
    "disagreement": "",
    "catalysts": [{"date": "", "sort": "YYYY-MM-DD", "title": "", "direction": "two-sided", "note": "", "source": ""}],
    "risks": [{"lead": "", "text": "", "source": ""}],
    "valuation": {"absolute": "", "peers": "", "history": ""},
    "why": {}, "what_would_change": {}, "change_mind": "",
}


def load_notes(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        notes = json.load(f)

    def filled(x):
        if isinstance(x, dict):
            return {k: filled(v) for k, v in x.items() if v not in ("", None, [], {}) and filled(v) not in ("", None, [], {})}
        if isinstance(x, list):
            return [filled(v) for v in x if filled(v) not in ("", None, [], {})]
        return x
    return filled(notes)


def main(argv=None):
    ik.setup_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["read"]:
        if len(argv) < 3:
            print("usage: scorecard.py read TICKER outlook|risks|customers|competition", file=sys.stderr)
            return 2
        try:
            print(read(argv[1].upper(), argv[2].lower()))
        except ik.InvestError as e:
            print("scorecard: %s" % e, file=sys.stderr)
            return 2
        return 0
    ap = argparse.ArgumentParser(description="Attractiveness scorecard: five weighted pillars from free data.")
    ap.add_argument("ticker")
    ap.add_argument("--peers", help="comma-separated peer tickers (same business model and end market)")
    ap.add_argument("--notes", help="Claude's notes JSON: thesis, drivers, risks, catalysts (see SKILL.md)")
    ap.add_argument("--no-text", action="store_true", help="skip reading filings' text (customers, guidance)")
    ap.add_argument("--no-news", action="store_true", help="skip the 8-K phrase search")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    ticker = args.ticker.upper()
    try:
        notes = load_notes(args.notes)
    except (OSError, ValueError) as e:
        print("scorecard: cannot read the notes file: %s" % e, file=sys.stderr)
        return 2
    peers = [p.strip().upper() for p in (args.peers or "").split(",") if p.strip()] or notes.get("peers") or []
    try:
        d = gather(ticker, peers, with_text=not args.no_text, with_news=not args.no_news)
    except ik.InvestError as e:
        print("scorecard: %s" % e, file=sys.stderr)
        return 2
    ev = evaluate(d, notes)
    print(render(d, ev, args.notes))
    if args.no_write or ev["score"] is None:
        return 0
    prior = prior_score(ticker)
    folder = os.path.join(ik.data_dir(), "scorecards")
    notes_file = os.path.join(folder, "%s-notes.json" % ticker)
    if not args.notes and not os.path.exists(notes_file):
        tpl = dict(NOTES_TEMPLATE, peers=peers)
        if d["guidance"] and d["guidance"][0].get("sentence"):
            tpl["guidance"] = {"text": d["guidance"][0]["sentence"], "revenue": d["guidance"][0]["guided"]}
        ik.write_text(notes_file, json.dumps(tpl, indent=2))
    payload = {"score": ev["score"], "band": ev["band"], "confidence": ev["confidence"], "pillars": ev["pillars"],
               "valuation_parts": ev["parts"], "inputs": [{k: m[k] for k in ("key", "label", "value", "score", "source", "rule")}
                                                          for m in ev["metrics"]],
               "peers": peers, "prior": prior}
    path = ik.save_section(ticker, "scorecard", payload)
    save_history(ticker, ev)
    svg = ik.write_text(os.path.join(ik.data_dir(), "charts", "%s-scorecard.svg" % ticker), card(d, ev))
    rep = ik.write_report("%s-scorecard" % ticker, report(d, ev, notes, prior))
    print("Saved %s, %s.\nNotes file: %s\nReport: %s" % (path, svg, args.notes or notes_file, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
