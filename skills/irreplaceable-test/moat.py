#!/usr/bin/env python3
"""Irreplaceable Test: is it a moat or a story?

Six tests a business with a real competitive advantage tends to pass, each
graded PASS / WARN / FAIL from the company's own SEC filings, each with the
numbers and the filing behind it:

  1. Pricing power      Did gross margin hold through the 2022-23 inflation?
                        (Costs rose everywhere; only companies that could
                        raise prices kept their margins.)
  2. Margin resilience  How far did gross margin fall in its worst year of
                        the last ten, against its typical year?
  3. Return on capital  Median return on invested capital, last 5 years.
  4. Reinvestment       Return on the NEW capital put in over 5 years: is the
                        advantage scaling, or just being milked?
  5. Customer reliance  Largest customer's share of revenue, read from the
                        latest annual report. US rules require naming any
                        customer above 10%, so silence is informative.
  6. Dilution           Is the share count shrinking or growing?

It also pulls the "Competition" section of the latest annual report so
Claude can read who the company says it competes with and how it wins.

The thresholds are judgment calls, stated in the output and changeable with
flags. A pass is evidence, not proof: a moat can erode faster than ten years
of filings show.

  python moat.py NVDA
  python moat.py KO --roic-pass 0.12
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import investkit as ik  # noqa: E402

PASS, WARN, FAIL, NA = "PASS", "WARN", "FAIL", "n/a"
POINTS = {PASS: 2, WARN: 1, FAIL: 0}


# ---------------------------------------------------------------- data

def by_year(series):
    """{fiscal year label: value}; the label is the calendar year the fiscal year mostly falls in."""
    out = {}
    for r in series:
        end = r["end"]
        y = int(end[:4]) - (1 if int(end[5:7]) <= 3 else 0)  # FY ending Jan-Mar belongs to the prior year
        out[y] = r
    return out


def annual_data(facts, currency, splits=()):
    rev = by_year(ik.annual_series(facts, "revenue", currency))
    gp = by_year(ik.annual_series(facts, "gross_profit", currency))
    cogs = by_year(ik.annual_series(facts, "cogs", currency))
    opi = by_year(ik.annual_series(facts, "operating_income", currency))
    tax = by_year(ik.annual_series(facts, "income_tax", currency))
    pre = by_year(ik.annual_series(facts, "pretax_income", currency))
    sh = by_year(ik.annual_series(facts, "shares_diluted", currency))
    eq = by_year(ik.instant_series(facts, "equity", currency))
    cash = by_year(ik.instant_series(facts, "cash", currency))
    inv = by_year(ik.instant_series(facts, "short_investments", currency))
    dn = by_year(ik.instant_series(facts, "debt_noncurrent", currency))
    dc = by_year(ik.instant_series(facts, "debt_current", currency))
    dt = by_year(ik.instant_series(facts, "debt_total", currency))
    years = sorted(rev)
    rows = {}
    for y in years:
        r = rev[y]["val"]
        if not r:
            continue
        g = gp[y]["val"] if y in gp else (r - cogs[y]["val"] if y in cogs else None)
        shares = None
        if y in sh:
            shares = sh[y]["val"]
            # undo stock splits the filing predates (later filings restate, older ones do not)
            for when, ratio in splits:
                if when > sh[y]["end"] and when > sh[y].get("filed", ""):
                    shares *= ratio
        row = {"year": y, "end": rev[y]["end"], "accn": rev[y]["accn"], "revenue": r,
               "gross_margin": g / r if g is not None else None,
               "op_income": opi[y]["val"] if y in opi else None,
               "shares": shares}
        t = None
        if y in tax and y in pre and pre[y]["val"] and pre[y]["val"] > 0:
            t = min(max(tax[y]["val"] / pre[y]["val"], 0.0), 0.35)
        row["tax_rate"] = t if t is not None else 0.21
        if y in eq:
            debt = 0
            if y in dn or y in dc:
                debt = (dn[y]["val"] if y in dn else 0) + (dc[y]["val"] if y in dc else 0)
            elif y in dt:
                debt = dt[y]["val"]
            ic = eq[y]["val"] + debt - (cash[y]["val"] if y in cash else 0) - (inv[y]["val"] if y in inv else 0)
            row["invested_capital"] = ic
        if row["op_income"] is not None:
            row["nopat"] = row["op_income"] * (1 - row["tax_rate"])
        rows[y] = row
    # ROIC on average invested capital
    ys = sorted(rows)
    for i, y in enumerate(ys):
        r = rows[y]
        prev = rows.get(y - 1)
        if r.get("nopat") is None or r.get("invested_capital") is None:
            continue
        ic = r["invested_capital"]
        if prev and prev.get("invested_capital") is not None:
            ic = (ic + prev["invested_capital"]) / 2.0
        r["roic"] = r["nopat"] / ic if ic > 0 else None
        r["ic_avg"] = ic
    return [rows[y] for y in ys]


# ---------------------------------------------------------------- tests

def t_pricing_power(rows):
    gm = {r["year"]: r["gross_margin"] for r in rows if r["gross_margin"] is not None}
    rev = {r["year"]: r["revenue"] for r in rows}
    if 2021 not in gm or not (2022 in gm or 2023 in gm):
        return {"grade": NA, "value": None, "detail": "Needs gross margin for 2021 and 2022-23."}
    base = gm[2021]
    later = [(gm[y], y) for y in (2022, 2023) if y in gm]
    worst, wy = min(later)
    if base <= 0:
        return {"grade": NA, "value": None,
                "detail": "Gross margin was negative in 2021 (%s): it sold below cost, so pricing power cannot be "
                          "judged yet." % ik.pct(base)}
    change = worst - base
    grade = PASS if change >= -0.01 else (WARN if change >= -0.03 else FAIL)
    detail = "Gross margin %s in 2021, lowest of 2022-23 %s in %d (%s points)." % (
        ik.pct(base), ik.pct(worst), wy, "%+.1f" % (change * 100))
    rchange = rev[wy] / rev[2021] - 1 if rev.get(2021) and rev.get(wy) else None
    if grade == FAIL and rchange is not None and rchange < -0.10:
        # margin fell alongside a slump in sales: write-downs and lost scale, not clean evidence on pricing
        grade = WARN
        detail += " Revenue fell %s that year too, so the drop mixes a sales slump with pricing." % ik.pct(-rchange, 0)
    return {"grade": grade, "value": change, "detail": detail,
            "rule": "PASS if it fell under 1 point, WARN under 3 (or a bigger fall during a sales slump)"}


def t_resilience(rows):
    gms = [r["gross_margin"] for r in rows[-10:] if r["gross_margin"] is not None]
    if len(gms) < 5:
        return {"grade": NA, "value": None, "detail": "Needs 5+ years of gross margin."}
    med = ik.percentile(sorted(gms), 50)
    worst = min(gms)
    worst_year = [r["year"] for r in rows[-10:] if r["gross_margin"] == worst][0]
    gap = med - worst
    grade = PASS if gap <= 0.03 else (WARN if gap <= 0.07 else FAIL)
    return {"grade": grade, "value": -gap,
            "detail": "Typical gross margin %s over %d years; worst %s in %d (%s points below)."
                      % (ik.pct(med), len(gms), ik.pct(worst), worst_year, "%.1f" % (gap * 100)),
            "rule": "PASS within 3 points of typical, WARN within 7"}


def t_roic(rows, pass_at, warn_at):
    vals = [(r["year"], r["roic"]) for r in rows[-5:] if r.get("roic") is not None]
    neg_ic = [r["year"] for r in rows[-5:] if r.get("invested_capital") is not None and r["invested_capital"] <= 0]
    if len(vals) < 3:
        if neg_ic:
            return {"grade": PASS, "value": None,
                    "detail": "Invested capital is negative (cash and buybacks exceed the equity tied up): the business "
                              "earns its profits with almost no capital of its own.", "rule": "special case"}
        return {"grade": NA, "value": None, "detail": "Needs 3+ years of operating income and balance sheets."}
    med = ik.percentile(sorted(v for _, v in vals), 50)
    grade = PASS if med >= pass_at else (WARN if med >= warn_at else FAIL)
    return {"grade": grade, "value": med,
            "detail": "Median %s over %d years (%s)." % (ik.pct(med), len(vals),
                                                        ", ".join("%d %s" % (y, ik.pct(v, 0)) for y, v in vals)),
            "rule": "after-tax operating profit / (equity + debt - cash); PASS at %s, WARN at %s"
                    % (ik.pct(pass_at, 0), ik.pct(warn_at, 0))}


def t_incremental(rows, pass_at, warn_at):
    use = [r for r in rows if r.get("nopat") is not None and r.get("invested_capital") is not None]
    if len(use) < 6:
        return {"grade": NA, "value": None, "detail": "Needs 6+ years of profit and balance sheets."}
    a, b = use[-6], use[-1]
    dn = b["nopat"] - a["nopat"]
    dic = b["invested_capital"] - a["invested_capital"]
    span = "%d to %d" % (a["year"], b["year"])
    if dic <= 0:
        grade = PASS if dn > 0 else FAIL
        return {"grade": grade, "value": None,
                "detail": "%s: after-tax operating profit %s by %s while invested capital fell by %s."
                          % (span, "rose" if dn > 0 else "fell", ik.money(abs(dn)), ik.money(abs(dic))),
                "rule": "profit up with no new capital is a pass"}
    inc = dn / dic
    grade = PASS if inc >= pass_at else (WARN if inc >= warn_at else FAIL)
    return {"grade": grade, "value": inc,
            "detail": "%s: %s more after-tax operating profit on %s more invested capital = %s on the new money."
                      % (span, ik.money(dn), ik.money(dic), ik.pct(inc)),
            "rule": "PASS at %s, WARN at %s" % (ik.pct(pass_at, 0), ik.pct(warn_at, 0))}


def t_dilution(rows):
    sh = [(r["year"], r["shares"]) for r in rows if r.get("shares")][-6:]
    # start after any one-year jump over 50% (an IPO or an unadjusted split), and say so
    note = ""
    for i in range(len(sh) - 1, 0, -1):
        if sh[i][1] / sh[i - 1][1] > 1.5:
            note = " Starts after a one-off jump in %d (an IPO, merger or unadjusted split)." % sh[i][0]
            sh = sh[i:]
            break
    if len(sh) < 3:
        return {"grade": NA, "value": None, "detail": "Needs 3+ comparable years of share counts." + note}
    a, b = sh[0], sh[-1]
    years = b[0] - a[0]
    rate = (b[1] / a[1]) ** (1.0 / years) - 1 if years > 0 and a[1] > 0 else None
    if rate is None:
        return {"grade": NA, "value": None, "detail": "Share counts not comparable."}
    grade = PASS if rate <= 0.01 else (WARN if rate <= 0.03 else FAIL)
    return {"grade": grade, "value": rate,
            "detail": "Diluted shares %s a year, %d to %d (%s to %s).%s" % (
                ik.pct(rate, 1, True), a[0], b[0], "{:,.0f}".format(a[1]), "{:,.0f}".format(b[1]), note),
            "rule": "PASS if growing no more than 1% a year, WARN up to 3%"}


# ---------------------------------------------------------------- the annual report text

PCT = r"(\d{1,3}(?:\.\d+)?)\s?%"
REV = r"(?:net\s+|total\s+|consolidated\s+)*(?:revenue|revenues|sales|net sales)"
# "22% of total revenue", "25%, 22% and 19% of our net revenue"
PCT_OF_REV = (r"\d{1,3}(?:\.\d+)?\s?%(?:\s*,?\s*(?:and\s+)?\d{1,3}(?:\.\d+)?\s?%)*\s*,?\s*of\s+"
              r"(?:[a-z'’]+\s+){0,3}(?:revenue|revenues|sales|net sales|net operating revenues)\b")


def customer_concentration(text):
    """Sentences that put a single customer at X% of revenue. Returns (max_pct, quotes)."""
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
    # de-duplicate near-identical sentences
    quotes = []
    for p, s in found:
        norm = re.sub(r"\W+", " ", s.lower()).strip()
        if any(norm in q or q in norm for q in (re.sub(r"\W+", " ", x.lower()).strip() for _, x in quotes)):
            continue
        quotes.append((p, s if len(s) < 400 else s[:397] + "..."))
    return quotes[0][0], quotes[:3], negations[:1]


def t_customers(text, filing):
    if text is None:
        return {"grade": NA, "value": None, "detail": "Could not read the latest annual report."}
    top, quotes, negations = customer_concentration(text)
    src = "%s filed %s" % (filing["form"], filing["filed"])
    if top is None:
        detail = "No customer at 10%% or more of revenue disclosed in the %s (US rules require naming them)." % src
        if negations:
            detail = "The %s says no single customer reaches the disclosure threshold." % src
        return {"grade": PASS, "value": 0.0, "detail": detail,
                "rule": "PASS under 10%, WARN 10-25%, FAIL above 25%",
                "quotes": [(0, q if len(q) < 400 else q[:397] + "...") for q in negations]}
    v = top / 100.0
    grade = PASS if v < 0.10 else (WARN if v <= 0.25 else FAIL)
    detail = "Largest single-customer figure found: %s of revenue (%s)." % (ik.pct(v, 0), src)
    if re.search(r"distributor|wholesaler|bottler|reseller", quotes[0][1].lower()):
        detail += " It is a distributor or bottler, which resells to many end customers, so the real reliance is lower."
        if grade == FAIL:
            grade = WARN
    return {"grade": grade, "value": v, "detail": detail,
            "rule": "PASS under 10%, WARN 10-25%, FAIL above 25% (a distributor at most WARN)", "quotes": quotes}


def competition_excerpt(text, chars=2500):
    """The 'Competition' part of the business section, for Claude to read."""
    if not text:
        return None
    # the real business section is the "Item 1. Business" heading followed by the
    # most text before "Item 1A. Risk Factors" (the table of contents entry is short)
    start_biz, risk, longest = None, None, -1
    for m in re.finditer(r"(?i)\bitem\s*1\.?\s*:?\s*business\b", text):
        if re.match(r"\s*[.,\"”’']|\s*(of this|above|below|herein)", text[m.end():m.end() + 20]):
            continue  # a cross-reference ("see Item 1. Business" above), not the section
        m2 = re.search(r"(?i)\bitem\s*1a\.?\s*:?\s*risk factors\b", text[m.end():])
        if not m2:
            continue
        end = m.end() + m2.start()
        if end - m.start() > longest:
            start_biz, risk, longest = m.start(), end, end - m.start()
    best = None
    for m in re.finditer(r"(?m)^\s*(?:Competition|Competitive (?:Environment|Landscape|Position|Conditions)|Our Competition)\s*$", text):
        if start_biz is not None and m.start() < start_biz:
            continue
        if risk is not None and m.start() > risk:
            break
        best = m.end()
        break
    if best is None:
        # no heading: start at the business-section paragraph that talks most about competitors
        lo, hi = (start_biz, risk) if start_biz is not None else (0, len(text))
        pos, top = None, 0
        offset = lo
        for para in text[lo:hi].split("\n"):
            n = len(re.findall(r"(?i)compet", para))
            if n > top and len(para) > 80:
                pos, top = offset, n
            offset += len(para) + 1
        if pos is None:
            return None
        best = pos
    return re.sub(r"\s+", " ", text[best:best + chars]).strip()


# ---------------------------------------------------------------- output

def verdict(tests):
    graded = [t for t in tests.values() if t["grade"] in POINTS]
    if not graded:
        return None, "Not enough data to judge."
    score = sum(POINTS[t["grade"]] for t in graded) / float(2 * len(graded))
    if score >= 0.8:
        label = "Looks irreplaceable: the numbers behave like a moat"
    elif score >= 0.6:
        label = "Real advantages, with cracks to watch"
    elif score >= 0.4:
        label = "Mixed: some moat, some story"
    else:
        label = "Mostly story: the numbers do not show a moat yet"
    return score, label


NAMES = [("pricing_power", "Pricing power"), ("resilience", "Margin resilience"), ("roic", "Return on capital"),
         ("reinvestment", "Return on new capital"), ("customers", "Customer reliance"), ("dilution", "Dilution")]


def render(ticker, name, tests, score, label, comp, filing, cik):
    L = ["IRREPLACEABLE TEST: %s (%s)" % (ticker, name)]
    if score is not None:
        passed = sum(1 for t in tests.values() if t["grade"] == PASS)
        graded = sum(1 for t in tests.values() if t["grade"] in POINTS)
        L.append("%s. %d of %d tests passed, score %d/100." % (label, passed, graded, round(score * 100)))
    L.append("")
    for key, title in NAMES:
        t = tests[key]
        L.append("%-5s %s" % (t["grade"], title))
        L.append("      %s" % t["detail"])
        if t.get("rule"):
            L.append("      Rule: %s" % t["rule"])
        for p, q in t.get("quotes", [])[:2]:
            L.append('      "%s"' % q)
    L.append("")
    if comp:
        L.append("COMPETITION, from the %s filed %s (for Claude to read and summarise):" % (filing["form"], filing["filed"]))
        L.append(comp)
    else:
        L.append("No Competition section found in the latest annual report.")
    L.append("")
    L.append("Sources: SEC company facts %s; annual report %s" % (ik.cik_link(cik), filing["doc_url"] if filing else "n/a"))
    L.append("A pass is evidence, not proof. Thresholds are judgment calls; financial companies, young companies and")
    L.append("conglomerates fit these tests poorly. " + ik.DISCLAIMER)
    return "\n".join(L)


def card(ticker, name, tests, score, label):
    """The report card as a standalone image (for video)."""
    W, H = 960, 540
    cls = {PASS: "good", WARN: "warn", FAIL: "crit", NA: "na"}
    b = ['<text class="title" x="60" y="52">%s: moat or story?</text>' % ik.esc(ticker),
         '<text class="sub" x="60" y="78">%s</text>' % ik.esc(label)]
    y = 116
    for key, title in NAMES:
        t = tests[key]
        b.append('<rect class="%s" x="60" y="%d" width="86" height="34" rx="17"/>' % (cls[t["grade"]], y))
        b.append('<text class="badge" x="103" y="%d" text-anchor="middle">%s</text>' % (y + 22, t["grade"]))
        b.append('<text class="lbl" x="166" y="%d" style="font-size:17px">%s</text>' % (y + 15, ik.esc(title)))
        d = t["detail"]
        b.append('<text class="note" x="166" y="%d" style="font-size:12.5px">%s</text>'
                 % (y + 33, ik.esc(d if len(d) < 115 else d[:112] + "...")))
        y += 62
    if score is not None:
        b.append('<text class="title" x="%d" y="52" text-anchor="end">%d/100</text>' % (W - 60, round(score * 100)))
    b.append('<text class="note" x="60" y="%d">From %s\'s SEC filings. A pass is evidence, not proof. Not financial advice.</text>'
             % (H - 18, ik.esc(name)))
    return ik.svg_wrap(W, H, "\n".join(b))


def mini_line(points, title, ref=None, ref_label=""):
    """A small line chart of (year, fraction) points, for the report."""
    pts = [(y, v) for y, v in points if v is not None]
    if len(pts) < 3:
        return ""
    W, H, ml, mr, mt, mb = 440, 210, 46, 16, 16, 30
    vals = [v for _, v in pts] + ([ref] if ref is not None else [])
    lo, hi = min(0.0, min(vals)), max(vals) * 1.1 or 1.0
    pw, ph = W - ml - mr, H - mt - mb
    y0, y1 = pts[0][0], pts[-1][0]

    def x(yr):
        return ml + (yr - y0) / float(max(1, y1 - y0)) * pw

    def y(v):
        return mt + ph - (v - lo) / (hi - lo) * ph

    b = []
    for t in ik.nice_ticks(lo, hi, 3):
        b.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, W - mr, y(t), y(t)))
        b.append('<text class="axis" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ml - 6, y(t) + 4, ik.pct(t, 0)))
    if ref is not None and lo <= ref <= hi:
        b.append('<line class="refl" x1="%d" x2="%d" y1="%.1f" y2="%.1f"%s/>' % (ml, W - mr, y(ref), y(ref), ik.tip(ref_label)))
    b.append('<polyline class="s1l" points="%s"/>' % " ".join("%.1f,%.1f" % (x(yr), y(v)) for yr, v in pts))
    for yr, v in pts:
        b.append('<circle class="s1 ring" cx="%.1f" cy="%.1f" r="4"%s/>' % (x(yr), y(v), ik.tip("%s %d: %s" % (title, yr, ik.pct(v)))))
    for yr in (y0, y1):
        b.append('<text class="axis" x="%.1f" y="%d" text-anchor="%s">%d</text>' % (
            x(yr), H - 8, "start" if yr == y0 else "end", yr))
    return ik.svg_wrap(W, H, "\n".join(b), inline=True, label="%s by year" % title)


def report(ticker, name, tests, score, label, comp, filing, cik, rows, args):
    passed = sum(1 for t in tests.values() if t["grade"] == PASS)
    warns = sum(1 for t in tests.values() if t["grade"] == WARN)
    fails = sum(1 for t in tests.values() if t["grade"] == FAIL)
    graded = passed + warns + fails
    thesis = "%s. %d of %d tests passed%s." % (ik.esc(label), passed, graded,
                                              (", %d with a warning" % warns) if warns else "")
    kpis = [{"k": "Moat score", "v": "%d/100" % round(score * 100) if score is not None else "n/a",
             "s": "PASS 2, WARN 1, FAIL 0 points", "hero": True},
            {"k": "Passed", "v": str(passed), "s": "of %d graded tests" % graded, "tone": "up" if passed else None},
            {"k": "Warnings", "v": str(warns), "s": "worth a closer look"},
            {"k": "Failed", "v": str(fails), "s": "evidence against a moat", "tone": "down" if fails else None}]
    body = [ik.h_kpis(kpis)]
    cards = []
    for key, title in NAMES:
        t = tests[key]
        q = "".join("<blockquote>%s</blockquote>" % ik.esc(qq) for _, qq in t.get("quotes", [])[:2])
        cards.append('<div class="test">%s<div class="body"><h3>%s</h3><p style="margin:0">%s</p>%s%s</div></div>' % (
            ik.h_pill(t["grade"]), ik.esc(title), ik.esc(t["detail"]),
            ('<span class="rule">Rule: %s</span>' % ik.esc(t["rule"])) if t.get("rule") else "", q))
    body.append(ik.h_section("The six tests", '<div class="tests">%s</div>' % "".join(cards)))
    gm = mini_line([(r["year"], r.get("gross_margin")) for r in rows[-10:]], "Gross margin")
    rc = mini_line([(r["year"], r.get("roic")) for r in rows[-5:]], "Return on capital", args.roic_pass,
                   "Pass line: %s" % ik.pct(args.roic_pass, 0))
    figs = "".join(ik.h_figure(svg, cap) for svg, cap in (
        (gm, "Gross margin by fiscal year"), (rc, "Return on invested capital, the 5 years the test grades (dashed: the %s pass line)"
                                              % ik.pct(args.roic_pass, 0))) if svg)
    if figs:
        body.append(ik.h_section("Ten years of the numbers", '<div class="kv">%s</div>' % figs))
    comp_parts = []
    if args.competition_summary:
        comp_parts.append('<div class="callout"><h3>Claude\'s reading of the competition section</h3><p>%s</p></div>'
                          % ik.esc(args.competition_summary))
    if comp:
        comp_parts.append(ik.h_details("The competition section, as filed", "<p>%s</p>" % ik.esc(comp)))
    if comp_parts:
        body.append(ik.h_section("Who it competes with", *comp_parts))
    body.append(ik.h_details("How this was worked out", ik.h_paras(
        "Every number comes from the company's own SEC filings (XBRL company facts), by fiscal year. The customer test "
        "reads the latest annual report's own sentences and quotes them.",
        "Return on capital is after-tax operating profit divided by invested capital (equity plus debt minus cash and "
        "short-term investments), averaged over the year. Dilution uses diluted share counts, adjusted for stock splits.",
        "The thresholds are judgment calls and are printed with each test. A pass is evidence, not proof: moats can "
        "erode faster than ten years of filings show, and these tests cannot see technology shifts. Financial "
        "companies, conglomerates and young companies fit them poorly.")))
    links = [("SEC company filings for %s" % name, ik.cik_link(cik))]
    if filing:
        links.append(("Latest annual report (%s, filed %s)" % (filing["form"], filing["filed"]), filing["doc_url"]))
    body.append(ik.h_section("Sources", ik.h_sources(links)))
    return ik.report_page("%s Moat Test" % ticker, ["Irreplaceable Test", ticker, ik.today().isoformat()], name, thesis,
                          "\n".join(body), "Six tests from %s's SEC filings: is it a moat or a story?" % ticker)


def main(argv=None):
    ik.setup_console()
    ap = argparse.ArgumentParser(description="Irreplaceable Test: moat or story, from SEC filings.")
    ap.add_argument("ticker")
    ap.add_argument("--roic-pass", type=float, default=0.15)
    ap.add_argument("--roic-warn", type=float, default=0.10)
    ap.add_argument("--no-text", action="store_true", help="skip reading the annual report text")
    ap.add_argument("--competition-summary", help="Claude's short summary of the competition section, shown in the report")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    ticker = args.ticker.upper()
    try:
        cik, name = ik.sec_lookup(ticker)
        facts = ik.sec_facts(cik)
        currency = ik.reporting_currency(facts)
        try:
            splits = ik.yahoo_chart(ticker, "max", "3mo", ttl_hours=24).get("splits", [])
        except ik.InvestError:
            splits = []
        rows = annual_data(facts, currency, splits)
        if len(rows) < 3:
            raise ik.InvestError("%s has fewer than 3 years of annual data in SEC's feed." % ticker)
        text, filing = (None, None) if args.no_text else ik.annual_report_text(cik)
    except ik.InvestError as e:
        print("moat: %s" % e, file=sys.stderr)
        return 2
    tests = {
        "pricing_power": t_pricing_power(rows),
        "resilience": t_resilience(rows),
        "roic": t_roic(rows, args.roic_pass, args.roic_warn),
        "reinvestment": t_incremental(rows, args.roic_pass, args.roic_warn),
        "customers": t_customers(text, filing) if not args.no_text else {"grade": NA, "value": None, "detail": "skipped"},
        "dilution": t_dilution(rows),
    }
    score, label = verdict(tests)
    comp = competition_excerpt(text) if text else None
    print(render(ticker, name, tests, score, label, comp, filing, cik))
    if not args.no_write:
        payload = {"name": name, "score": score, "verdict": label, "tests": tests,
                   "competition_excerpt": comp, "annual_report": filing,
                   "history": [{k: r.get(k) for k in ("year", "revenue", "gross_margin", "roic", "shares")} for r in rows[-10:]]}
        path = ik.save_section(ticker, "irreplaceable", payload)
        svg = ik.write_text(os.path.join(ik.data_dir(), "charts", "%s-irreplaceable.svg" % ticker),
                            card(ticker, name, tests, score, label))
        rep = ik.write_report("%s-irreplaceable" % ticker, report(ticker, name, tests, score, label, comp, filing, cik,
                                                                  rows, args))
        print("Saved %s, %s.\nReport: %s" % (path, svg, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
