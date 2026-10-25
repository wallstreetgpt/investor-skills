#!/usr/bin/env python3
"""Idea Scanner: scan the whole US market for names worth a closer look, with their newsflow.

Every US-listed company that files with the SEC, scored from the SEC's XBRL
"frames" (one request returns one number for every filer) and Yahoo's batch
quotes, then passed through a few stated investor lenses:

  quality       Quality compounders: high return on capital 3 years running,
                steady gross margin, growing revenue, little dilution.
  gap           Expectations gap: the price assumes less growth than the
                company has been delivering (Priced-In's reverse DCF, run on
                every company).
  fallen        Fallen quality: a good business trading 25%+ below its high.
  accelerating  Accelerating growth: revenue growth speeding up.
  catalysts     Fresh catalysts: a positive 8-K in the last two weeks (raised
                guidance, record backlog, a new buyback...) at a decent business.
  all           One combined ranking of quality, growth and the expectations gap.

Newsflow comes from SEC 8-K filings (market-wide phrase search, with red flags
such as restatements removing names) and Yahoo headlines for the shortlist.

  python scanner.py                          # all lenses, $1B+ companies
  python scanner.py --lens gap --min-cap 5e9
  python scanner.py --lens quality --theme "data center"
  python scanner.py news NVDA,MSFT,KO        # newsflow for your own list
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import investkit as ik  # noqa: E402

LENSES = [("all", "All lenses"), ("quality", "Quality compounders"), ("gap", "Expectations gap"),
          ("fallen", "Fallen quality"), ("accelerating", "Accelerating growth"), ("catalysts", "Fresh catalysts")]
LENS_NAME = dict(LENSES)
LENS_RULE = {
    "quality": "Return on capital of 15% or more in each of the last 3 years, gross margin within 3 points of its "
               "3-year average, revenue growing 8% a year or more over 3 years, share count growing 2% a year or less.",
    "gap": "Positive free cash flow in each of the last 2 years (at least 5% of revenue last year), return on capital "
           "of 10% or more, and the growth today's price assumes (reverse DCF, 9% required return) at least 5 points "
           "below its own revenue growth (the lower of its 3-year rate and last year's, counted at no more than 40%). "
           "Companies whose growth came from acquisitions (goodwill jump or a completed-deal 8-K) are left out.",
    "fallen": "Return on capital of 12% or more in each of the last 3 years, gross margin within 3 points of its "
              "3-year average, positive free cash flow in each of the last 2 years, and the price 25% or more below "
              "its 52-week high.",
    "accelerating": "Latest quarter's revenue up 15% or more on a year earlier and at least 5 points faster than the "
                    "same quarter's growth a year before, which was already positive; gross margin not down more than "
                    "1 point; positive free cash flow last year; no acquisition in the last 13 months (goodwill jump or "
                    "a completed-deal 8-K).",
    "catalysts": "A positive catalyst 8-K in the last {days} days (raised guidance, record results or backlog, a new or "
                 "larger buyback, a dividend increase, a major contract), positive operating cash flow and return on "
                 "capital of 10% or more.",
    "all": "Average percentile rank on three things: return on capital (3-year median), own revenue growth (the lower "
           "of the 3-year rate and last year's), and the expectations gap. Needs return on capital and positive free "
           "cash flow in each of the last 2 years.",
}
def lens_rule(lens, days):
    return LENS_RULE[lens].replace("{days}", str(days))


NEXT_STEP = {"quality": "the-judge", "gap": "the-mind-reader", "fallen": "the-sniper",
             "accelerating": "the-judge", "catalysts": "the-mind-reader", "all": "the-mind-reader"}

# ---- what to read from SEC frames
REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
           "RevenueFromContractWithCustomerIncludingAssessedTax"]
COGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
CAPEX = ik.TAGS["capex"]
EQUITY = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
          "PartnersCapital", "MembersEquity"]
STI = ik.TAGS["short_investments"]

# ---- newsflow: the 8-K phrase library lives in the core (the scorecard uses it too)
CATALYSTS = ik.CATALYSTS
RED_FLAGS = ik.RED_FLAGS
own_words = ik.own_words
ITEM_LABELS = {"1.01": "material agreement", "1.02": "agreement ended", "1.03": "bankruptcy", "2.01": "deal completed",
               "2.02": "results", "2.03": "new debt", "2.05": "restructuring", "2.06": "impairment",
               "3.01": "delisting notice", "3.02": "share sale", "4.01": "auditor change", "4.02": "restatement",
               "5.02": "officer or director change", "5.03": "bylaws change", "5.07": "shareholder vote",
               "7.01": "investor disclosure", "8.01": "other event", "9.01": "exhibits"}
POSITIVE_WORDS = ["beat", "beats", "raises", "raised", "upgrade", "upgraded", "record", "surge", "surges", "soars",
                  "jumps", "wins", "approval", "approved", "buyback", "outperform", "tops"]
NEGATIVE_WORDS = ["miss", "misses", "cuts", "cut", "downgrade", "downgraded", "lawsuit", "sued", "probe",
                  "investigation", "recall", "plunges", "slumps", "tumbles", "falls", "warning", "delay", "delays",
                  "lowers", "halts", "layoffs", "fraud"]


GROWTH_CAP = 0.40


# ---------------------------------------------------------------- periods

def periods():
    """The latest complete calendar year in SEC frames, and the latest quarter that
    ended at least 75 days ago (time for most 10-Qs to arrive)."""
    t = ik.today()
    year = t.year - 1 if t.month >= 4 else t.year - 2
    q_end = t - datetime.timedelta(days=75)
    qn = (q_end.month - 1) // 3  # quarters fully ended before q_end's quarter
    qy = q_end.year
    if qn == 0:
        qy, qn = qy - 1, 4
    return year, (qy, qn)


def q_label(qy, qn, back=0):
    n = qy * 4 + (qn - 1) - back
    return "CY%dQ%d" % (n // 4, n % 4 + 1)


# ---------------------------------------------------------------- universe

def listed_companies(include_otc=False):
    """One ticker per company: Nasdaq and NYSE (and OTC if asked), from SEC's ticker file."""
    data = ik.fetch_json("https://www.sec.gov/files/company_tickers_exchange.json", "sec", ttl_hours=24 * 7)
    allowed = {"Nasdaq", "NYSE", "CBOE"} | ({"OTC"} if include_otc else set())
    out, seen = {}, set()
    for cik, name, ticker, exch in data["data"]:
        if exch not in allowed or cik in seen or not ticker:
            continue
        if not re.match(r"^[A-Z]{1,5}(-[A-Z])?$", ticker):  # skip warrants, units, preferreds
            continue
        seen.add(cik)
        out[cik] = {"cik": cik, "name": name, "ticker": ticker, "exchange": exch}
    return out


def theme_ciks(phrase):
    """Companies whose annual reports of the last 15 months mention the phrase."""
    end = ik.today()
    start = end - datetime.timedelta(days=460)
    hits = ik.sec_fulltext_hits(phrase, forms="10-K", start=start.isoformat(), end=end.isoformat(), max_pages=30,
                                ttl_hours=24)
    return {h["cik"] for h in hits}


# ---------------------------------------------------------------- fundamentals from frames

def load_frames(year, quarter, progress=True):
    """Everything the lenses need, for every filer: {name: {cik: value}}."""
    y = year
    qy, qn = quarter
    jobs = []
    for k in range(4):
        jobs.append(("rev%d" % (y - k), REVENUE, "CY%d" % (y - k), "USD"))
    for k in range(3):
        yy = y - k
        jobs += [("gp%d" % yy, ["GrossProfit"], "CY%d" % yy, "USD"), ("cogs%d" % yy, COGS, "CY%d" % yy, "USD"),
                 ("oi%d" % yy, ["OperatingIncomeLoss"], "CY%d" % yy, "USD"), ("pre%d" % yy, PRETAX, "CY%d" % yy, "USD"),
                 ("tax%d" % yy, ["IncomeTaxExpenseBenefit"], "CY%d" % yy, "USD"),
                 ("eq%d" % yy, EQUITY, "CY%dQ4I" % yy, "USD"),
                 ("cash%d" % yy, ik.TAGS["cash"], "CY%dQ4I" % yy, "USD"),
                 ("sti%d" % yy, STI, "CY%dQ4I" % yy, "USD"),
                 ("dlt%d" % yy, ik.TAGS["debt_noncurrent"], "CY%dQ4I" % yy, "USD"),
                 ("dcur%d" % yy, ik.TAGS["debt_current"], "CY%dQ4I" % yy, "USD"),
                 ("dtot%d" % yy, ik.TAGS["debt_total"], "CY%dQ4I" % yy, "USD"),
                 ("dst%d" % yy, ik.TAGS["short_borrowings"], "CY%dQ4I" % yy, "USD")]
    for yy in (y, y - 1):
        sfx = "" if yy == y else "_prev"
        jobs += [("cfo" + sfx, ["NetCashProvidedByUsedInOperatingActivities"], "CY%d" % yy, "USD"),
                 ("capex" + sfx, CAPEX, "CY%d" % yy, "USD"), ("sbc" + sfx, ["ShareBasedCompensation"], "CY%d" % yy, "USD")]
    jobs += [
             ("sh%d" % y, ["WeightedAverageNumberOfDilutedSharesOutstanding"], "CY%d" % y, "shares"),
             ("sh%d" % (y - 3), ["WeightedAverageNumberOfDilutedSharesOutstanding"], "CY%d" % (y - 3), "shares"),
             ("deposits", ["Deposits"], "CY%dQ4I" % y, "USD"), ("premiums", ["PremiumsEarnedNet"], "CY%d" % y, "USD"),
             ("reit", ["RealEstateInvestmentPropertyNet"], "CY%dQ4I" % y, "USD")]
    jobs += [("gw%d" % y, ["Goodwill"], "CY%dQ4I" % y, "USD"), ("gw%d" % (y - 3), ["Goodwill"], "CY%dQ4I" % (y - 3), "USD"),
             ("qgw0", ["Goodwill"], q_label(qy, qn, 0) + "I", "USD"), ("qgw4", ["Goodwill"], q_label(qy, qn, 4) + "I", "USD")]
    for back in (0, 4, 8):
        jobs.append(("qrev%d" % back, REVENUE, q_label(qy, qn, back), "USD"))
    for back in (0, 4):
        jobs += [("qgp%d" % back, ["GrossProfit"], q_label(qy, qn, back), "USD"),
                 ("qcogs%d" % back, COGS, q_label(qy, qn, back), "USD")]
    out = {}
    for i, (name, tags, period, unit) in enumerate(jobs):
        if progress:
            sys.stderr.write("\rReading SEC data for every filer: %d of %d..." % (i + 1, len(jobs)))
            sys.stderr.flush()
        recent = period.startswith("CY%d" % qy) or period.startswith("CY%d" % (qy - 1))
        out[name] = ik.sec_frame_map(tags, period, unit=unit, ttl_hours=24 * 3 if recent else 24 * 30,
                                      largest=name.startswith(("rev", "qrev")))
    if progress:
        sys.stderr.write("\r" + " " * 60 + "\r")
    return out


def _gm(f, cik, tag_gp, tag_cogs, rev):
    if not rev or rev <= 0:
        return None
    gp = f[tag_gp].get(cik)
    if gp is None and f[tag_cogs].get(cik) is not None:
        gp = rev - f[tag_cogs][cik]
    if gp is None:
        return None
    m = gp / rev
    return m if -1 <= m <= 1 else None


def metrics(cik, f, year, q, discount=0.09, terminal=0.025):
    """Every number the lenses use, for one company; None where a number is missing."""
    y = year
    m = {"cik": cik}
    rev = {yy: f["rev%d" % yy].get(cik) for yy in range(y - 3, y + 1)}
    m["revenue"] = rev[y]
    if not rev[y] or rev[y] <= 0:
        return None
    m["growth3"] = ik.cagr(rev[y - 3], rev[y], 3) if rev[y - 3] and rev[y - 3] > 0 else None
    gms = [_gm(f, cik, "gp%d" % yy, "cogs%d" % yy, rev[yy]) for yy in (y - 2, y - 1, y)]
    m["gross_margin"] = gms[-1]
    m["gm_drift"] = (gms[-1] - sum(gms) / 3.0) if all(g is not None for g in gms) else None
    roics, ics = [], []
    for yy in (y - 2, y - 1, y):
        oi = f["oi%d" % yy].get(cik)
        eq = f["eq%d" % yy].get(cik)
        if oi is None or eq is None:
            roics.append(None)
            ics.append(None)
            continue
        pre, tax = f["pre%d" % yy].get(cik), f["tax%d" % yy].get(cik)
        rate = min(max(tax / pre, 0.0), 0.35) if pre and pre > 0 and tax is not None else 0.21
        lt, cur, tot = f["dlt%d" % yy].get(cik), f["dcur%d" % yy].get(cik), f["dtot%d" % yy].get(cik)
        debt_known = lt is not None or cur is not None or tot is not None
        debt = ((lt or 0) + (cur or 0)) if (lt is not None or cur is not None) else (tot or 0)
        debt += f["dst%d" % yy].get(cik) or 0
        ic = eq + debt - (f["cash%d" % yy].get(cik) or 0) - (f["sti%d" % yy].get(cik) or 0)
        ics.append(ic)
        if ic > 0:
            roics.append(oi * (1 - rate) / ic)
        elif eq < 0:
            roics.append(None)  # negative equity and no capital left: a debt figure is missing, so unknown
        else:
            # cash and buybacks exceed the capital tied up: profitable means very high returns
            roics.append(float("inf") if oi > 0 else None)
        del debt_known
        if yy == y:
            m["net_debt"] = debt - (f["cash%d" % yy].get(cik) or 0) - (f["sti%d" % yy].get(cik) or 0)
    m["roic"] = roics
    finite = [r for r in roics if r is not None]
    m["roic_min"] = min(finite) if len(finite) == 3 else None
    m["roic_last"] = roics[-1]
    cfo, capex, sbc = f["cfo"].get(cik), f["capex"].get(cik), f["sbc"].get(cik)
    m["cfo"] = cfo
    m["fcf"] = (cfo - capex - (sbc or 0)) if cfo is not None and capex is not None else None
    m["fcf_margin"] = m["fcf"] / rev[y] if m["fcf"] is not None else None
    cfo_p = f["cfo_prev"].get(cik)
    m["fcf_prev"] = (cfo_p - (f["capex_prev"].get(cik) or 0) - (f["sbc_prev"].get(cik) or 0)) if cfo_p is not None else None
    # cash flow far above revenue means client money or a tagging error, not free cash flow
    # needs capital spending as well: a company that tags its capex under its own custom name
    # (United Rentals' rental fleet) would otherwise look far more cash-generative than it is
    m["capex_found"] = capex is not None and f["capex_prev"].get(cik) is not None
    m["fcf_ok"] = bool(m["fcf"] is not None and m["fcf_prev"] is not None and m["fcf"] > 0 and m["fcf_prev"] > 0
                       and m["fcf_margin"] <= 0.6 and m["capex_found"])
    s1, s0 = f["sh%d" % y].get(cik), f["sh%d" % (y - 3)].get(cik)
    dil = (s1 / s0) ** (1 / 3.0) - 1 if s1 and s0 and 0.33 < s1 / s0 < 3 else None
    m["dilution"] = dil
    q0, q4, q8 = f["qrev0"].get(cik), f["qrev4"].get(cik), f["qrev8"].get(cik)
    m["q_growth"] = q0 / q4 - 1 if q0 and q4 and q4 > 0 else None
    m["q_growth_prev"] = q4 / q8 - 1 if q4 and q8 and q8 > 0 else None
    m["q_gm"] = _gm(f, cik, "qgp0", "qcogs0", q0)
    m["q_gm_prev"] = _gm(f, cik, "qgp4", "qcogs4", q4)
    m["q_revenue"] = q0
    # growth bought by acquisition: goodwill up by more than a quarter of revenue (3 years), or by
    # more than a quarter in the last year (latest quarter)
    g1, g0 = f["gw%d" % y].get(cik) or 0, f["gw%d" % (y - 3)].get(cik) or 0
    m["acquisitive"] = (g1 - g0) > 0.25 * rev[y]
    qg1, qg0 = f["qgw0"].get(cik) or 0, f["qgw4"].get(cik) or 0
    m["q_acquisitive"] = qg1 > 0 and (qg0 == 0 and qg1 > 0.1 * (q0 or 1) * 4 or qg0 > 0 and qg1 / qg0 > 1.25)
    # own growth: the lower of the 3-year rate and last year's, so a rebound from a bad year does not count
    last = rev[y] / rev[y - 1] - 1 if rev[y - 1] and rev[y - 1] > 0 else None
    m["growth1"] = last
    m["own_growth"] = min(m["growth3"], last) if m["growth3"] is not None and last is not None else m["growth3"]
    m["financial"] = cik in f["deposits"] or cik in f["premiums"] or cik in f["reit"]
    return m


def add_market(m, q, discount=0.09, terminal=0.025):
    """Price-based numbers from a Yahoo quote: market cap, distance from highs, priced-in growth."""
    m["market_cap"] = q.get("marketCap")
    p, hi, ma = q.get("regularMarketPrice"), q.get("fiftyTwoWeekHigh"), q.get("twoHundredDayAverage")
    m["price"] = p
    m["off_high"] = p / hi - 1 if p and hi else None
    m["vs_200d"] = p / ma - 1 if p and ma else None
    m["short_name"] = q.get("shortName") or q.get("longName")
    m["implied"] = None
    if m["market_cap"] and m.get("fcf") and m["fcf"] > 0 and m.get("capex_found", True):
        ev = m["market_cap"] + (m.get("net_debt") or 0)
        if ev > 0:
            m["implied"] = ik.implied_growth_fcf(ev, m["fcf"], 10, discount, terminal)
    # past growth above 40% a year almost never lasts (see Priced-In's base rates) and often comes from
    # acquisitions, so the gap is measured against growth capped at 40%
    m["gap"] = (min(m["own_growth"], GROWTH_CAP) - m["implied"]) if m.get("own_growth") is not None and m["implied"] is not None else None
    return m


# ---------------------------------------------------------------- lenses

def passes(m, lens, catalysts=None):
    """(passes, why) for one company and one lens."""
    if m.get("financial"):
        return False, None
    if lens == "quality":
        ok = (m["roic_min"] is not None and m["roic_min"] >= 0.15 and m["gm_drift"] is not None
              and abs(m["gm_drift"]) <= 0.03 and m["growth3"] is not None and m["growth3"] >= 0.08
              and m["dilution"] is not None and m["dilution"] <= 0.02)
        why = "Return on capital %s every year for 3 years, revenue %s a year, gross margin steady at %s, shares %s a year" % (
            at_least(m["roic_min"]), ik.pct(m["growth3"], 0, True), ik.pct(m["gross_margin"], 0),
            ik.pct(m["dilution"], 1, True)) if ok else None
        return ok, why
    if lens == "gap":
        ok = (m["fcf_ok"] and m["fcf_margin"] >= 0.05 and m["roic_last"] is not None and m["roic_last"] >= 0.10
              and m["gap"] is not None and m["gap"] >= 0.05 and not m["acquisitive"])
        why = "Revenue grew %s a year over 3 years and %s last year%s; the price assumes %s, a gap of %d points. " \
              "Free cash flow %s of revenue" % (
                  ik.pct(m["growth3"], 0), ik.pct(m["growth1"], 0),
                  " (counted as 40%)" if m["own_growth"] > GROWTH_CAP else "", ik.pct(m["implied"], 0),
                  round(m["gap"] * 100), ik.pct(m["fcf_margin"], 0)) if ok else None
        return ok, why
    if lens == "fallen":
        ok = (m["roic_min"] is not None and m["roic_min"] >= 0.12 and m["gm_drift"] is not None
              and abs(m["gm_drift"]) <= 0.03 and m["fcf_ok"] and m["off_high"] is not None and m["off_high"] <= -0.25)
        why = "Return on capital %s every year for 3 years and steady margins, but %s below its 52-week high" % (
            at_least(m["roic_min"]), ik.pct(-m["off_high"], 0)) if ok else None
        return ok, why
    if lens == "accelerating":
        ok = (m["q_growth"] is not None and m["q_growth_prev"] is not None and 0.15 <= m["q_growth"] <= 1.5
              and (m["q_revenue"] or 0) >= 50e6 and m["q_growth_prev"] >= 0 and (m["fcf"] or 0) > 0
              and not m["q_acquisitive"]
              and m["q_growth"] - m["q_growth_prev"] >= 0.05
              and (m["q_gm"] is None or m["q_gm_prev"] is None or m["q_gm"] >= m["q_gm_prev"] - 0.01))
        why = "Revenue %s on a year earlier in the latest quarter, up from %s a year before%s" % (
            ik.pct(m["q_growth"], 0, True), ik.pct(m["q_growth_prev"], 0, True),
            (", gross margin %s" % ik.pct(m["q_gm"], 0)) if m["q_gm"] is not None else "") if ok else None
        return ok, why
    if lens == "catalysts":
        cats = (catalysts or {}).get(m["cik"]) or []
        ok = bool(cats) and (m["cfo"] or 0) > 0 and m["roic_last"] is not None and m["roic_last"] >= 0.10
        if not ok:
            return False, None
        best = max(cats, key=lambda c: (c["strength"], c["filed"]))
        return True, "%s (8-K filed %s), return on capital %s" % (best["label"], best["filed"], pct_or_high(m["roic_last"]))
    return False, None


def at_least(x):
    return "above 100%" if x is not None and (x == float("inf") or x > 1) else "of at least %s" % ik.pct(x, 0)


def pct_or_high(x):
    if x is None:
        return "n/a"
    return "over 100%" if x == float("inf") or x > 1 else ik.pct(x, 0)


def percentile_ranks(rows, key):
    vals = sorted(r[key] for r in rows if r.get(key) is not None)
    import bisect
    n = float(len(vals)) or 1.0
    return {r["cik"]: bisect.bisect_right(vals, r[key]) / n for r in rows if r.get(key) is not None}


def combined(rows):
    for r in rows:
        r["roic_median"] = sorted(x if x is not None else -1 for x in r["roic"])[1] if r["roic_min"] is not None else None
        if r["roic_median"] == float("inf"):
            r["roic_median"] = 10.0
    ranks = [percentile_ranks(rows, k) for k in ("roic_median", "own_growth", "gap")]
    for r in rows:
        got = [rk[r["cik"]] for rk in ranks if r["cik"] in rk]
        ok = r["cik"] in ranks[0] and r.get("fcf_ok") and not r.get("financial")
        r["score"] = sum(got) / len(got) if ok and len(got) >= 2 else None


# ---------------------------------------------------------------- newsflow

def scan_catalysts(days, ciks):
    """Positive catalyst 8-Ks of the last `days`, for companies in `ciks`: {cik: [hits]}."""
    end = ik.today()
    start = (end - datetime.timedelta(days=days)).isoformat()
    out = {}
    for kind, label, strength, phrases in CATALYSTS:
        for ph in phrases:
            for h in ik.sec_fulltext_hits(ph, "8-K", start, end.isoformat(), max_pages=3):
                if h["cik"] in ciks and own_words(h):
                    lst = out.setdefault(h["cik"], [])
                    if not any(x["kind"] == kind and x["url"] == h["url"] for x in lst):
                        lst.append({"kind": kind, "label": label, "strength": strength, "filed": h["filed"],
                                    "url": h["url"], "phrase": ph, "items": h["items"]})
    return out


def scan_red_flags(days, ciks):
    """Red-flag 8-Ks of the last `days`: {cik: [hits]}."""
    end = ik.today()
    start = (end - datetime.timedelta(days=days)).isoformat()
    out = {}
    for kind, label, hard, phrases, item in RED_FLAGS:
        for ph in phrases:
            for h in ik.sec_fulltext_hits(ph, "8-K", start, end.isoformat(), max_pages=5):
                if h["cik"] not in ciks or not own_words(h) or (item and item not in h["items"]):
                    continue
                lst = out.setdefault(h["cik"], [])
                if not any(x["kind"] == kind for x in lst):
                    lst.append({"kind": kind, "label": label, "hard": hard, "filed": h["filed"], "url": h["url"]})
    return out


GENERIC = {"american", "general", "united", "first", "national", "international", "global", "new", "the", "us",
           "advanced", "applied", "western", "southern", "northern", "eastern", "energy", "health", "capital"}


def name_keys(ticker, short_name):
    """Words that mark a headline as being about this company."""
    keys = [ticker.upper()]
    core = re.sub(r"[,.]|\b(inc|corp|corporation|co|company|ltd|plc|holdings?|group|the|class [a-z])\b", " ",
                  (short_name or "").lower())
    words = [w for w in core.split() if w]
    if words:
        keys.append(" ".join(words[:2]))
        if len(words[0]) >= 4 and words[0] not in GENERIC:
            keys.append(words[0])
    return keys


def headline_info(ticker, short_name, days):
    heads = ik.yahoo_headlines(ticker, days)
    keys = name_keys(ticker, short_name)
    out = []
    for h in heads:
        t = h["title"]
        low = t.lower()
        about = bool(re.search(r"\b%s\b" % re.escape(keys[0]), t)) or any(k in low for k in keys[1:])
        pos = [w for w in POSITIVE_WORDS if re.search(r"\b%s\b" % w, low)]
        neg = [w for w in NEGATIVE_WORDS if re.search(r"\b%s\b" % w, low)]
        tone = "positive" if pos and not neg else "negative" if neg and not pos else "mixed" if pos and neg else "neutral"
        out.append(dict(h, about=about, tone=tone))
    return out


FINANCIAL_SIC = ("60", "61", "62", "63", "64", "67")


def company_sic(cik):
    return str(ik.sec_submissions(cik).get("sic") or "")


def completed_deal(cik):
    """The newest 8-K of the last 13 months reporting a completed acquisition (item 2.01), or None."""
    for f in ik.recent_8k_items(cik, 400):
        if "2.01" in f["items"]:
            return f
    return None


def news_for(row, days_headlines, days_8k, with_headlines=True):
    row["filings"] = [dict(f, labels=[ITEM_LABELS.get(i, i) for i in f["items"] if i != "9.01"])
                      for f in ik.recent_8k_items(row["cik"], days_8k)]
    if with_headlines:
        row["headlines"] = headline_info(row["ticker"], row.get("short_name") or row["name"], days_headlines)


# ---------------------------------------------------------------- the scan

def run_scan(args):
    year, quarter = periods()
    comp = listed_companies(args.include_otc)
    if args.theme:
        tc = theme_ciks(args.theme)
        comp = {c: v for c, v in comp.items() if c in tc}
        if not comp:
            raise ik.InvestError('No listed company\'s annual report mentions "%s".' % args.theme)
    f = load_frames(year, quarter, progress=sys.stderr.isatty())
    have = {c: v for c, v in comp.items() if f["rev%d" % year].get(c)}
    sys.stderr.write("Pricing %d companies on Yahoo...\n" % len(have))
    quotes = ik.yahoo_quotes([v["ticker"] for v in have.values()])
    rows, counts = [], {"listed": len(comp), "with_sec_financials": len(have), "below_min_cap": 0, "below_min_revenue": 0, "no_quote": 0, "foreign_currency": 0,
                        "financial": 0}
    for cik, c in have.items():
        q = quotes.get(c["ticker"])
        if not q or q.get("quoteType") not in (None, "EQUITY"):
            counts["no_quote"] += 1
            continue
        if q.get("financialCurrency") not in (None, "USD"):
            counts["foreign_currency"] += 1
            continue
        if (ik.quote_market_cap_usd(q) or 0) < args.min_cap:
            counts["below_min_cap"] += 1
            continue
        m = metrics(cik, f, year, quarter)
        if m is None:
            continue
        if m["revenue"] < args.min_revenue:
            counts["below_min_revenue"] += 1
            continue
        m.update(c)
        add_market(m, q, args.discount)
        if m["financial"]:
            counts["financial"] += 1
        rows.append(m)
    counts["scanned"] = len(rows)
    ciks = {r["cik"] for r in rows}
    cats, flags = {}, {}
    if not args.no_news:
        sys.stderr.write("Reading 8-K newsflow...\n")
        cats = scan_catalysts(args.days, ciks)
        flags = scan_red_flags(90, ciks)
    combined(rows)
    removed = []
    lenses = [k for k, _ in LENSES if k != "all"] if args.lens == "all" else [args.lens]
    results = {}
    for lens in lenses + (["all"] if args.lens == "all" else []):
        picked = []
        for r in rows:
            if lens == "all":
                ok, why = r["score"] is not None, None
            else:
                ok, why = passes(r, lens, cats)
            if not ok:
                continue
            hard = [x for x in flags.get(r["cik"], []) if x["hard"]]
            if hard and not args.keep_flagged:
                if not any(x["ticker"] == r["ticker"] for x in removed):
                    removed.append({"ticker": r["ticker"], "name": r["name"], "flags": hard})
                continue
            r.setdefault("lenses", set())
            if lens != "all":
                r["lenses"].add(lens)
                r.setdefault("why", {})[lens] = why
            picked.append(r)
        results[lens] = picked
    # rank each lens
    keyf = {"quality": lambda r: -(min(r["roic_median"] or 0, 1.0) + min(r["growth3"] or 0, GROWTH_CAP)),
            "gap": lambda r: -(r["gap"] or 0), "fallen": lambda r: (r["off_high"] or 0),
            "accelerating": lambda r: -((r["q_growth"] or 0) - (r["q_growth_prev"] or 0)),
            "catalysts": lambda r: (-max(c["strength"] for c in cats[r["cik"]]), -(min(r["roic_last"] or 0, 1.0))),
            "all": lambda r: -(r["score"] or 0)}
    bought, finance = [], []
    for lens in results:
        results[lens].sort(key=keyf[lens])
        # vet candidates in rank order from their SEC record until the list is full:
        # financial industries (SIC 60-67) are out everywhere; a completed acquisition
        # (8-K item 2.01) is out of the lenses that reward growth
        kept = []
        for r in results[lens]:
            if len(kept) >= args.top:
                break
            sic = company_sic(r["cik"])
            r["sic"] = sic
            if sic[:2] in FINANCIAL_SIC:
                if not any(b["ticker"] == r["ticker"] for b in finance):
                    finance.append({"ticker": r["ticker"], "name": r["name"], "sic": sic})
                r.get("lenses", set()).discard(lens)
                continue
            if lens in ("gap", "accelerating", "all"):
                deal = completed_deal(r["cik"])
                if deal and lens != "all":
                    r["deal"] = deal
                    if not any(b["ticker"] == r["ticker"] for b in bought):
                        bought.append({"ticker": r["ticker"], "name": r["name"], "filed": deal["filed"], "url": deal["url"]})
                    r["lenses"].discard(lens)
                    continue
                r["deal"] = deal
            kept.append(r)
        results[lens] = kept
    for r in rows:
        if "lenses" in r and r["score"] is not None:
            pass
    for r in rows:
        for c in cats.get(r["cik"], []):
            r.setdefault("catalysts", []).append(c)
        r["flags"] = flags.get(r["cik"], [])
    short = {}
    for lens, lst in results.items():
        for r in lst:
            short[r["cik"]] = r
    if not args.no_news:
        sys.stderr.write("Reading filings and headlines for %d shortlisted companies...\n" % len(short))
        for r in short.values():
            news_for(r, args.days, 90, with_headlines=not args.no_headlines)
    for r in results.get("all", []):
        r["why_all"] = "Passes %s. Return on capital %s (3-year median), revenue %s a year, the price assumes %s" % (
            ", ".join(LENS_NAME[k].lower() for k in sorted(r.get("lenses") or [])) or "no single lens",
            pct_or_high(r.get("roic_median")), ik.pct(r["own_growth"], 0, True) if r["own_growth"] is not None else "n/a",
            ik.pct(r["implied"], 0) if r["implied"] is not None else "n/a")
    lens_counts = {k: sum(1 for r in rows if k in (r.get("lenses") or set())) for k, _ in LENSES if k != "all"}
    return {"year": year, "quarter": q_label(*quarter), "counts": counts, "rows": rows, "results": results, "bought": bought, "finance": finance,
            "catalysts": cats, "flags": flags, "removed": removed, "lens_counts": lens_counts, "args": vars(args)}


# ---------------------------------------------------------------- output (terminal)

def row_line(r):
    return "%-6s %-26s %8s  rev %6s  roic %8s  fcf %5s  priced-in %6s  vs high %5s" % (
        r["ticker"], (r.get("short_name") or r["name"])[:26], ik.money(r["market_cap"], 0),
        ik.pct(r["growth3"], 0, True), pct_or_high(r.get("roic_last")), ik.pct(r["fcf_margin"], 0),
        ik.pct(r["implied"], 0), ik.pct(r["off_high"], 0))


def unique_kinds(cats):
    """Newest catalyst of each kind."""
    out, seen = [], set()
    for c in sorted(cats or [], key=lambda c: c["filed"], reverse=True):
        if c["kind"] not in seen:
            seen.add(c["kind"])
            out.append(c)
    return out


def news_line(r):
    bits = []
    for c in unique_kinds(r.get("catalysts"))[:2]:
        bits.append("+ %s %s" % (c["label"], c["filed"]))
    for x in r.get("flags") or []:
        bits.append("! %s %s" % (x["label"], x["filed"]))
    heads = [h for h in r.get("headlines") or [] if h["about"]]
    if heads:
        bits.append("%d headline%s, latest: %s" % (len(heads), "" if len(heads) == 1 else "s", heads[0]["title"][:70]))
    return "; ".join(bits)


def render(scan):
    a = scan["args"]
    c = scan["counts"]
    L = ["IDEA SCANNER: %s%s" % (LENS_NAME[a["lens"]], (' (theme: "%s")' % a["theme"]) if a.get("theme") else "")]
    L.append("Scanned %d US-listed companies worth %s or more (of %d listed; %d without SEC financials in US GAAP, "
             "%d too small, %d under the revenue floor, %d reporting in other currencies, %d banks/insurers/REITs "
             "left out of the lenses). Fundamentals: calendar %d (SEC frames), "
             "latest quarter %s; prices from Yahoo today." % (
                 c["scanned"], ik.money(a["min_cap"], 0), c["listed"], c["listed"] - c["with_sec_financials"],
                 c["below_min_cap"], c["below_min_revenue"], c["foreign_currency"], c["financial"], scan["year"],
                 scan["quarter"]))
    L.append("")
    for lens, lst in scan["results"].items():
        rule = lens_rule(lens, a["days"])
        n = scan["lens_counts"].get(lens)
        L.append("%s%s" % (LENS_NAME[lens].upper(), (": %d pass" % n) if n is not None else ""))
        L.append("  Rule: " + rule)
        for r in lst:
            L.append("  " + row_line(r))
            why = r.get("why_all") if lens == "all" else (r.get("why") or {}).get(lens)
            if why:
                L.append("         " + why)
            nl = news_line(r)
            if nl:
                L.append("         News: " + nl)
        if not lst:
            L.append("  Nothing passes today.")
        L.append("")
    if scan.get("finance"):
        L.append("Left out as financial companies by their SEC industry code (lenders, asset managers, brokers): "
                 + ", ".join(b["ticker"] for b in scan["finance"]))
        L.append("")
    if scan.get("bought"):
        L.append("Set aside from expectations gap and accelerating growth: completed an acquisition in the last 13 months "
                 "(8-K item 2.01), so the growth is partly bought:")
        L.append("  " + ", ".join("%s (%s)" % (b["ticker"], b["filed"]) for b in scan["bought"]))
        L.append("")
    if scan["removed"]:
        L.append("Removed for red flags (use --keep-flagged to keep them):")
        for x in scan["removed"]:
            L.append("  %-6s %s: %s" % (x["ticker"], x["name"][:30], "; ".join("%s %s" % (f["label"], f["filed"]) for f in x["flags"])))
        L.append("")
    L.append("A screen is a starting point, not a pick. Next: /%s on the names that interest you." % NEXT_STEP[a["lens"]])
    L.append(ik.DISCLAIMER)
    return "\n".join(L)


# ---------------------------------------------------------------- report page

def scatter(rows, highlight):
    """Own 3-year revenue growth (x) against the growth the price assumes (y)."""
    W, H, ml, mr, mt, mb = 960, 520, 64, 24, 20, 56
    lo, hi = -0.2, 0.6
    pw, ph = W - ml - mr, H - mt - mb

    def x(v):
        return ml + (min(max(v, lo), hi) - lo) / (hi - lo) * pw

    def y(v):
        return mt + ph - (min(max(v, lo), hi) - lo) / (hi - lo) * ph

    b = []
    for t in (-0.2, 0, 0.2, 0.4, 0.6):
        b.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, W - mr, y(t), y(t)))
        b.append('<text class="axis" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ml - 8, y(t) + 4, ik.pct(t, 0)))
        b.append('<text class="axis" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x(t), mt + ph + 20, ik.pct(t, 0)))
    b.append('<line class="refl" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>' % (x(lo), y(lo), x(hi), y(hi)))
    b.append('<text class="note" x="%.1f" y="%.1f" text-anchor="end">Above the line: the price expects faster growth '
             'than the past 3 years</text>' % (x(hi) - 6, y(hi) + 16))
    b.append('<text class="note" x="%.1f" y="%.1f" text-anchor="end">Below: it expects less</text>' % (x(hi) - 6, y(lo + 0.06)))
    pts = [r for r in rows if r.get("growth3") is not None and r.get("implied") is not None and not r.get("financial")
           and lo <= r["growth3"] <= hi and lo <= r["implied"] <= hi]
    outside = sum(1 for r in rows if r.get("growth3") is not None and r.get("implied") is not None
                  and not r.get("financial")) - len(pts)
    for r in pts:
        if r["cik"] in highlight:
            continue
        b.append('<circle class="bar" cx="%.1f" cy="%.1f" r="2.4"/>' % (x(r["growth3"]), y(r["implied"])))
    for r in pts:
        if r["cik"] not in highlight:
            continue
        b.append('<circle class="s1 ring" cx="%.1f" cy="%.1f" r="5.5"%s/>' % (
            x(r["growth3"]), y(r["implied"]),
            ik.tip("%s, %s\nRevenue grew %s a year\nThe price assumes %s" % (
                r["ticker"], (r.get("short_name") or r["name"])[:40], ik.pct(r["growth3"]), ik.pct(r["implied"])))))
    b.append('<text class="axis" x="%d" y="%d" text-anchor="middle">Revenue growth a year, last 3 years</text>'
             % (ml + pw / 2, H - 8))
    b.append('<text class="axis" x="14" y="%d" transform="rotate(-90 14 %d)" text-anchor="middle">Growth the price '
             'assumes</text>' % (mt + ph / 2, mt + ph / 2))
    return ik.svg_wrap(W, H, "\n".join(b), inline=True,
                       label="Scatter of each company's past revenue growth against the growth its price assumes"), outside


TABLE_JS = """
(function () {
  document.querySelectorAll('[data-tabs]').forEach(function (bar) {
    var btns = bar.querySelectorAll('button');
    function open(id, push) {
      btns.forEach(function (b) { var on = b.dataset.tab === id; b.setAttribute('aria-selected', on);
        document.getElementById('lens-' + b.dataset.tab).hidden = !on; });
    }
    btns.forEach(function (b) { b.addEventListener('click', function () { open(b.dataset.tab); }); });
    function fromHash() { var h = (location.hash || '').slice(1);
      open(document.getElementById('lens-' + h) ? h : btns[0].dataset.tab); }
    window.addEventListener('hashchange', fromHash);
    fromHash();
  });
  document.querySelectorAll('table.sortable').forEach(function (t) {
    t.querySelectorAll('th').forEach(function (th, i) {
      th.tabIndex = 0;
      function sort() { var body = t.tBodies[0], rows = Array.prototype.slice.call(body.rows);
        var dir = th.dataset.dir === 'desc' ? 1 : -1; th.dataset.dir = dir === 1 ? 'asc' : 'desc';
        rows.sort(function (a, b) { var x = a.cells[i].dataset.v, y = b.cells[i].dataset.v;
          var nx = parseFloat(x), ny = parseFloat(y);
          if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * dir; return String(x).localeCompare(String(y)) * dir; });
        rows.forEach(function (r) { body.appendChild(r); }); }
      th.addEventListener('click', sort); th.addEventListener('keydown', function (e) { if (e.key === 'Enter') sort(); });
    });
  });
})();
"""

EXTRA_CSS = """<style>
.tabs { display: flex; flex-wrap: wrap; gap: 6px; }
.tabs button { font: 600 13px var(--f-body); color: var(--ink-2); background: var(--surface); border: 1px solid var(--border);
  border-radius: 999px; padding: 7px 12px; cursor: pointer; }
.tabs button[aria-selected="true"] { background: var(--ink); color: var(--page); border-color: var(--ink); }
.tabs button span { font-weight: 400; opacity: .75; }
table.sortable th { cursor: pointer; } table.sortable th:hover { color: var(--ink); }
td .why { display: block; font-size: 12.5px; color: var(--ink-2); white-space: normal; min-width: 30ch; max-width: 60ch; }
td .tk { font: 600 14px var(--f-data); } td .nm { color: var(--ink-2); font-size: 13px; }
td code, .cmd { font: 12px var(--f-data); color: var(--ink-2); }
.badge { display: inline-block; font-size: 11.5px; padding: 2px 7px; border-radius: 999px; margin: 1px 2px 1px 0;
  border: 1px solid var(--border); white-space: nowrap; }
.badge.pos { border-color: var(--s3); } .badge.neg { border-color: var(--crit); }
.newsrow { display: grid; gap: 4px; font-size: 13px; color: var(--ink-2); }
.newsrow b { color: var(--ink); }
</style>"""


def _num(v):
    if v is None:
        return ""
    return "1e9" if v == float("inf") else "%.6g" % v


def lens_table(lens, lst, scan):
    head = ["Company", "Market cap", "Revenue growth (3 yr)", "Return on capital", "FCF margin", "Price assumes",
            "Vs 52-wk high", "News", "Next step"]
    rows = []
    for r in lst:
        why = r.get("why_all") if lens == "all" else (r.get("why") or {}).get(lens) or ""
        badges = []
        for c in unique_kinds(r.get("catalysts"))[:2]:
            badges.append('<a class="badge pos" href="%s" target="_blank" rel="noopener">+ %s</a>' % (ik.esc(c["url"]), ik.esc(c["label"])))
        for x in r.get("flags") or []:
            badges.append('<a class="badge neg" href="%s" target="_blank" rel="noopener">! %s</a>' % (ik.esc(x["url"]), ik.esc(x["label"])))
        heads = [h for h in r.get("headlines") or [] if h["about"]]
        if heads:
            badges.append('<span class="badge">%d headline%s</span>' % (len(heads), "" if len(heads) == 1 else "s"))
        cmd = "/%s %s" % (NEXT_STEP[lens], r["ticker"])
        cells = [
            ('<span class="tk">%s</span> <span class="nm">%s</span><span class="why">%s</span>'
             % (ik.esc(r["ticker"]), ik.esc((r.get("short_name") or r["name"])[:34]), ik.esc(why)), r["ticker"]),
            (ik.esc(ik.money(r["market_cap"], 0)), _num(r["market_cap"])),
            (ik.esc(ik.pct(r["growth3"], 0, True)), _num(r["growth3"])),
            (ik.esc(pct_or_high(r.get("roic_last"))), _num(r.get("roic_last"))),
            (ik.esc(ik.pct(r["fcf_margin"], 0)), _num(r["fcf_margin"])),
            (ik.esc(ik.pct(r["implied"], 0)), _num(r["implied"])),
            (ik.esc(ik.pct(r["off_high"], 0)), _num(r["off_high"])),
            (" ".join(badges), str(len(badges))),
            ('<code>%s</code>' % ik.esc(cmd), cmd)]
        rows.append("<tr>%s</tr>" % "".join('<td data-v="%s">%s</td>' % (ik.esc(v), h) for h, v in cells))
    if not rows:
        return '<p class="lede">Nothing passes this lens today.</p>'
    return ('<div class="tbl"><table class="sortable"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % ("".join("<th>%s</th>" % ik.esc(h) for h in head), "".join(rows)))


def news_panels(scan):
    seen, parts = set(), []
    for lens, lst in scan["results"].items():
        for r in lst:
            if r["cik"] in seen:
                continue
            seen.add(r["cik"])
            lines = []
            for c in unique_kinds(r.get("catalysts")):
                lines.append('<span><b>+ %s</b>, 8-K %s: <a href="%s" target="_blank" rel="noopener">filing</a></span>'
                             % (ik.esc(c["label"]), c["filed"], ik.esc(c["url"])))
            for x in r.get("flags") or []:
                lines.append('<span><b>! %s</b>, 8-K %s: <a href="%s" target="_blank" rel="noopener">filing</a></span>'
                             % (ik.esc(x["label"]), x["filed"], ik.esc(x["url"])))
            for fl in (r.get("filings") or [])[:4]:
                if fl["labels"]:
                    lines.append('<span>8-K %s: %s (<a href="%s" target="_blank" rel="noopener">filing</a>)</span>'
                                 % (fl["filed"], ik.esc(", ".join(fl["labels"])), ik.esc(fl["url"])))
            for h in [h for h in r.get("headlines") or [] if h["about"]][:5]:
                lines.append('<span>%s: <a href="%s" target="_blank" rel="noopener">%s</a>%s</span>' % (
                    h["date"], ik.esc(h["link"]), ik.esc(h["title"]),
                    "" if h["tone"] == "neutral" else ' <span class="tag">(%s words)</span>' % h["tone"]))
            if not lines:
                continue
            parts.append(ik.h_details("%s, %s" % (r["ticker"], (r.get("short_name") or r["name"])[:40]),
                                      '<div class="newsrow">%s</div>' % "".join(lines)))
    return "".join(parts)


def report(scan):
    a, c = scan["args"], scan["counts"]
    lens = a["lens"]
    main_list = scan["results"].get(lens) or []
    title_bits = LENS_NAME[lens] + ((' in "%s"' % a["theme"]) if a.get("theme") else "")
    if lens == "all":
        thesis = ("%d companies scanned. The combined ranking puts <b>%s</b> on top; %s pass at least one lens."
                  % (c["scanned"], ik.esc(", ".join(r["ticker"] for r in main_list[:3])) or "nothing",
                     sum(1 for r in scan["rows"] if r.get("lenses"))))
    else:
        thesis = "<b>%d</b> of %d companies pass the %s lens%s." % (
            scan["lens_counts"].get(lens, 0), c["scanned"], ik.esc(LENS_NAME[lens].lower()),
            (": " + ik.esc(", ".join(r["ticker"] for r in main_list[:3])) + " lead the list") if main_list else "")
    n_cats = len(scan["catalysts"])
    kpis = [{"k": "Companies scanned", "v": "{:,}".format(c["scanned"]), "s": "US-listed, %s or more" % ik.money(a["min_cap"], 0), "hero": True}]
    for k in ("quality", "gap", "fallen", "accelerating", "catalysts"):
        if k in scan["lens_counts"] and (lens == "all" or k == lens):
            kpis.append({"k": LENS_NAME[k], "v": str(scan["lens_counts"][k]), "s": "pass"})
    if not a.get("no_news"):
        kpis.append({"k": "Fresh catalysts", "v": str(n_cats), "s": "companies, last %d days" % a["days"]})
        kpis.append({"k": "Removed for red flags", "v": str(len(scan["removed"])), "s": "restatement, going concern...",
                     "tone": "down" if scan["removed"] else None})
    body = [ik.h_kpis(kpis[:6])]
    order = [lens] + [k for k in scan["results"] if k != lens]
    tabs = "".join('<button type="button" data-tab="%s" aria-selected="false">%s <span>%d</span></button>' % (
        k, ik.esc(LENS_NAME[k]), len(scan["results"][k])) for k in order)
    panes = "".join('<div id="lens-%s" role="tabpanel"><p class="lede">%s</p>%s</div>' % (
        k, ik.esc(lens_rule(k, a["days"])), lens_table(k, scan["results"][k], scan))
        for k in order)
    body.append(ik.h_section("The shortlist", '<div class="tabs" role="tablist" data-tabs>%s</div>%s' % (tabs, panes),
                             lede="Click a column to sort. Every lens is a stated rule; a name can pass several."))
    hl = {r["cik"] for lst in scan["results"].values() for r in lst}
    svg, outside = scatter(scan["rows"], hl)
    body.append(ik.h_section("What the market expects, against what each company delivered", ik.h_figure(
        svg, "Each dot is a company with positive free cash flow. Below the dashed line, the price assumes slower "
        "growth than the company's last 3 years: the expectations gap. Highlighted: the shortlist; hover for names. "
        "%d companies fall outside the -20%% to 60%% range and are not drawn." % outside, [("--s1", "On the shortlist", "box"), ("--bar", "Everything else scanned", "box")])))
    panels = news_panels(scan)
    if panels:
        body.append(ik.h_section("Newsflow on the shortlist", panels,
                                 lede="Positive catalysts (+) and red flags (!) from 8-K filings, recent 8-Ks by item, "
                                      "and headlines Yahoo links to the company that name it. Tone words are a simple "
                                      "keyword tag, not a judgment."))
    if scan.get("bought"):
        body.append(ik.h_callout("Set aside: growth partly bought", [
            "These passed the expectations gap or accelerating growth lens but completed an acquisition in the last "
            "13 months (8-K item 2.01), so their growth is not all their own: " + ", ".join(
                "%s (%s)" % (b["ticker"], b["filed"]) for b in scan["bought"])]))
    if scan["removed"]:
        body.append(ik.h_callout("Removed for red flags", ["%s (%s): %s" % (x["ticker"], x["name"][:30], "; ".join(
            "%s, %s" % (f["label"], f["filed"]) for f in x["flags"])) for x in scan["removed"]], warn=True))
    body.append(ik.h_details("How the scan works", ik.h_paras(
        "Universe: Nasdaq and NYSE companies that file US GAAP financials with the SEC, worth %s or more today. Foreign "
        "filers (20-F, IFRS) are not covered. Banks, insurers and REITs are left out of the lenses because "
        "free cash flow and return on capital do not describe them." % ik.money(a["min_cap"], 0),
        "Fundamentals come from SEC XBRL frames, which line each company's fiscal year up with a calendar year: "
        "calendar %d for the annual numbers, %s for the latest quarter. A company whose fiscal year ended in early %d "
        "counts toward %d." % (scan["year"], scan["quarter"], scan["year"] + 1, scan["year"]),
        "Return on capital is after-tax operating profit over equity plus debt minus cash, as in The Judge (the scorecard). "
        "Free cash flow is operating cash flow minus capital spending and stock compensation. The growth the price "
        "assumes uses The Mind Reader's reverse DCF with a 9% required return, on the last full year's free cash flow; "
        "/the-mind-reader redoes it on the trailing twelve months.",
        "Newsflow: SEC full-text search over 8-K filings for fixed phrases (raised guidance, record backlog, new buybacks, "
        "dividend increases, contract awards; and red flags such as restatements, going-concern doubt, lowered guidance), "
        "plus Yahoo headlines. A phrase match shows what a company said, not whether it matters.",
        "Survivors only: today's ticker list leaves out companies that failed. A screen is a starting point, not a pick.")))
    srcs = [("SEC XBRL frames API", "https://www.sec.gov/edgar/sec-api-documentation"),
            ("SEC full-text search", "https://efts.sec.gov/LATEST/search-index"),
            ("Prices and headlines: Yahoo Finance", None)]
    body.append(ik.h_section("Sources", ik.h_sources(srcs)))
    page = ik.report_page("Idea Scanner %s" % title_bits.replace('"', ""), ["The Scout · Idea Scanner", LENS_NAME[lens], ik.today().isoformat()],
                          "Ideas: " + title_bits, thesis, "\n".join(body),
                          "A scan of every US-listed company through stated investor lenses, with 8-K newsflow.")
    return page.replace("<style>", EXTRA_CSS + "\n<style>", 1).replace("</script>\n", "</script>\n<script>%s</script>\n" % TABLE_JS, 1)


def save_scan(scan):
    def clean(r):
        d = {k: v for k, v in r.items() if k not in ("lenses",)}
        d["lenses"] = sorted(r.get("lenses") or [])
        d["roic"] = [None if x is None else ("inf" if x == float("inf") else x) for x in r.get("roic") or []]
        for k in ("roic_min", "roic_last", "roic_median"):
            if d.get(k) == float("inf"):
                d[k] = "inf"
        return d
    a = scan["args"]
    name = "%s-%s%s" % (ik.today().isoformat(), a["lens"], ("-" + re.sub(r"[^a-z0-9]+", "-", a["theme"].lower())) if a.get("theme") else "")
    payload = {"year": scan["year"], "quarter": scan["quarter"], "counts": scan["counts"], "args": a,
               "shortlists": {k: [r["ticker"] for r in v] for k, v in scan["results"].items()},
               "removed": scan["removed"], "rows": [clean(r) for r in scan["rows"]]}
    path = ik.write_text(os.path.join(ik.data_dir(), "scans", name + ".json"), json.dumps(payload, default=str))
    rep_name = "scan-%s%s" % (a["lens"], ("-" + re.sub(r"[^a-z0-9]+", "-", a["theme"].lower())) if a.get("theme") else "")
    rep = ik.write_report(rep_name, report(scan))
    return path, rep


# ---------------------------------------------------------------- news for your own list

def run_news(tickers, days):
    comp = {v["ticker"]: v for v in listed_companies(True).values()}
    rows = []
    quotes = ik.yahoo_quotes(tickers)
    for t in tickers:
        c = comp.get(t.upper().replace(".", "-"))
        if not c:
            try:
                cik, name = ik.sec_lookup(t)
                c = {"cik": cik, "name": name, "ticker": t.upper()}
            except ik.InvestError:
                rows.append({"ticker": t.upper(), "name": "not found in SEC's ticker list", "cik": None})
                continue
        r = dict(c)
        r["short_name"] = (quotes.get(t.upper()) or {}).get("shortName")
        rows.append(r)
    ciks = {r["cik"] for r in rows if r["cik"]}
    cats = scan_catalysts(days, ciks)
    flags = scan_red_flags(90, ciks)
    for r in rows:
        if not r["cik"]:
            continue
        r["catalysts"] = cats.get(r["cik"], [])
        r["flags"] = flags.get(r["cik"], [])
        news_for(r, days, 90)
    return rows


def render_news(rows, days):
    L = ["NEWSFLOW: %s (8-K catalysts last %d days, red flags last 90, headlines last %d)" % (
        ", ".join(r["ticker"] for r in rows), days, days), ""]
    for r in rows:
        L.append("%s  %s" % (r["ticker"], r.get("short_name") or r["name"]))
        if not r.get("cik"):
            L.append("  Not found.")
            continue
        for c in r["catalysts"]:
            L.append("  + %s, 8-K %s  %s" % (c["label"], c["filed"], c["url"]))
        for x in r["flags"]:
            L.append("  ! %s, 8-K %s  %s" % (x["label"], x["filed"], x["url"]))
        for fl in r.get("filings", [])[:5]:
            if fl["labels"]:
                L.append("  8-K %s: %s" % (fl["filed"], ", ".join(fl["labels"])))
        about = [h for h in r.get("headlines", []) if h["about"]]
        other = len(r.get("headlines", [])) - len(about)
        for h in about[:6]:
            L.append("  %s  %s%s" % (h["date"], h["title"], "" if h["tone"] == "neutral" else "  [%s words]" % h["tone"]))
        if other:
            L.append("  (%d more headlines in its feed are about other companies)" % other)
        L.append("")
    L.append("Phrase matches show what companies said in filings, not whether it matters. " + ik.DISCLAIMER)
    return "\n".join(L)


def news_report(rows, days):
    body = []
    n_c = sum(len(r.get("catalysts") or []) for r in rows)
    n_f = sum(len(r.get("flags") or []) for r in rows)
    kpis = [{"k": "Companies", "v": str(len(rows)), "s": "on your list", "hero": True},
            {"k": "Positive catalysts", "v": str(n_c), "s": "8-Ks, last %d days" % days},
            {"k": "Red flags", "v": str(n_f), "s": "8-Ks, last 90 days", "tone": "down" if n_f else None},
            {"k": "Headlines", "v": str(sum(1 for r in rows for h in r.get("headlines") or [] if h["about"])),
             "s": "about these companies, last %d days" % days}]
    body.append(ik.h_kpis(kpis))
    scan = {"results": {"news": rows}}
    body.append(ik.h_section("Company by company", news_panels(scan) or '<p class="lede">No news found.</p>'))
    body.append(ik.h_details("How the newsflow is read", ik.h_paras(
        "8-K filings are the forms companies must file within four business days of material news. Catalysts and red "
        "flags are found by SEC full-text search for fixed phrases; the recent 8-K list uses each filing's item codes.",
        "Headlines are Yahoo Finance's feed for each ticker, kept only when they name the company or its ticker. Tone "
        "words are a simple keyword tag, not a judgment.")))
    thesis = "%d positive catalyst%s and %d red flag%s across %d companies." % (
        n_c, "" if n_c == 1 else "s", n_f, "" if n_f == 1 else "s", len(rows))
    return ik.report_page("Newsflow Watchlist", ["The Scout · Newsflow", ik.today().isoformat()], "Newsflow: " + ", ".join(
        r["ticker"] for r in rows[:8]) + ("..." if len(rows) > 8 else ""), thesis, "\n".join(body),
        "Catalysts, red flags and headlines for a watchlist.")


# ---------------------------------------------------------------- main

def main(argv=None):
    ik.setup_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "news":
        ap = argparse.ArgumentParser(prog="scanner.py news", description="Newsflow for your own list of tickers.")
        ap.add_argument("tickers", nargs="?", help="comma-separated, e.g. NVDA,MSFT,KO")
        ap.add_argument("--watchlist", help="a text file with one ticker per line")
        ap.add_argument("--days", type=int, default=7)
        ap.add_argument("--no-write", action="store_true")
        args = ap.parse_args(argv[1:])
        tickers = [t.strip().upper() for t in (args.tickers or "").split(",") if t.strip()]
        if args.watchlist:
            with open(args.watchlist, encoding="utf-8") as fh:
                tickers += [ln.strip().upper() for ln in fh if ln.strip() and not ln.startswith("#")]
        if not tickers:
            ap.error("give tickers or --watchlist")
        try:
            rows = run_news(tickers, args.days)
        except ik.InvestError as e:
            print("scanner: %s" % e, file=sys.stderr)
            return 2
        print(render_news(rows, args.days))
        if not args.no_write:
            print("Report: %s" % ik.write_report("newsflow", news_report(rows, args.days)))
        return 0
    if argv and argv[0] == "scan":
        argv = argv[1:]
    ap = argparse.ArgumentParser(description="Idea Scanner: the whole US market through investor lenses.")
    ap.add_argument("--lens", choices=[k for k, _ in LENSES], default="all")
    ap.add_argument("--min-cap", type=float, default=1e9, help="minimum market cap in USD (default 1e9)")
    ap.add_argument("--min-revenue", type=float, default=2e8, help="minimum yearly revenue in USD (default 2e8)")
    ap.add_argument("--theme", help='only companies whose annual report mentions this phrase, e.g. "data center"')
    ap.add_argument("--days", type=int, default=7, help="newsflow window for catalysts and headlines (default 7)")
    ap.add_argument("--top", type=int, default=20, help="names per lens (default 20)")
    ap.add_argument("--discount", type=float, default=0.09)
    ap.add_argument("--keep-flagged", action="store_true", help="keep names with restatement or going-concern flags")
    ap.add_argument("--no-news", action="store_true", help="skip 8-K and headline newsflow")
    ap.add_argument("--no-headlines", action="store_true", help="skip Yahoo headlines (8-Ks only)")
    ap.add_argument("--include-otc", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    if args.lens == "catalysts" and args.no_news:
        ap.error("the catalysts lens needs the newsflow; drop --no-news")
    try:
        scan = run_scan(args)
    except ik.InvestError as e:
        print("scanner: %s" % e, file=sys.stderr)
        return 2
    print(render(scan))
    if not args.no_write:
        path, rep = save_scan(scan)
        print("Saved %s\nReport: %s" % (path, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
