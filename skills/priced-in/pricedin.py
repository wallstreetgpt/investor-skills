#!/usr/bin/env python3
"""Priced-In Calculator: what growth does today's price already assume?

A reverse DCF. Instead of guessing a fair value, it takes the market's price
as given and solves for the free-cash-flow growth rate that makes a standard
two-stage DCF equal today's enterprise value. Then it checks that number
against reality:

  * the company's own revenue track record (3, 5 and 10-year growth), and
  * the base rate: how fast every SEC-reporting company of a similar size
    actually grew revenue over 10 years (SEC XBRL frames, 2011 onward).

It also turns growth scenarios into the annual return an investor would earn
from today's price. At exactly the priced-in growth, that return equals the
discount rate; the other scenarios show what happens if the company grows
like a typical peer, like a top-quartile peer, or like its own past.

  python pricedin.py NVDA
  python pricedin.py KO --discount 0.08
  python pricedin.py RIVN --mode revenue --target-margin 0.10
  python pricedin.py NVDA --price 150          # what would $150 assume?
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import investkit as ik  # noqa: E402

DEFAULTS = {"discount": 0.09, "terminal": 0.025, "years": 10}

SIZE_BUCKETS = [(0, 325e6), (325e6, 1e9), (1e9, 2e9), (2e9, 5e9), (5e9, 10e9),
                (10e9, 25e9), (25e9, 50e9), (50e9, float("inf"))]

FIRST_FRAME_YEAR = 2011  # all filers were tagging XBRL by fiscal 2011


# ---------------------------------------------------------------- inputs

FINANCIAL_SIC = ("60", "61", "62", "63", "64", "67")


def size_bucket(revenue):
    for lo, hi in SIZE_BUCKETS:
        if lo <= revenue < hi:
            return lo, hi
    return SIZE_BUCKETS[-1]


def bucket_label(lo, hi):
    if hi == float("inf"):
        return "revenue over %s" % ik.money(lo, 0)
    return "revenue %s to %s" % (ik.money(lo, 0), ik.money(hi, 0))


def gather(ticker, args):
    """Everything the model needs, each with where it came from."""
    cik, title = ik.sec_lookup(ticker)
    facts = ik.sec_facts(cik)
    subs = ik.sec_submissions(cik)
    currency = ik.reporting_currency(facts)
    fx = ik.fx_to_usd(currency) if currency != "USD" else 1.0
    sic = str(subs.get("sic") or "")
    warnings = []

    rev = ik.ttm_value(facts, "revenue", currency)
    cfo = ik.ttm_value(facts, "cfo", currency)
    capex = ik.ttm_value(facts, "capex", currency)
    sbc = ik.ttm_value(facts, "sbc", currency)
    dna = ik.ttm_value(facts, "dna", currency)
    ni = ik.ttm_value(facts, "net_income", currency)
    financial = sic[:2] in FINANCIAL_SIC
    if not rev or not cfo:
        raise ik.InvestError("%s's filings do not carry the revenue and operating cash flow this needs." % ticker)
    if not capex and not financial:
        warnings.append("No capital-expenditure tag found; free cash flow = operating cash flow.")
    if rev["end"] != cfo["end"] and not financial:
        warnings.append("Revenue runs to %s, cash flow to %s." % (rev["end"], cfo["end"]))
    age = ik.months_old(cfo["end"])
    if age and age > 13:
        warnings.append("The newest financials in SEC's data end %s, %d months ago. Foreign filers report once a "
                        "year and SEC's feed can lag; the business may have changed since." % (cfo["end"], age))

    fcf = cfo["val"] - (capex["val"] if capex else 0)
    sbc_val = sbc["val"] if sbc else 0
    if args.basis == "fcf-sbc":
        fcf -= sbc_val
    nc = ik.net_cash(facts, currency)
    for s in nc["stale"]:
        warnings.append("Left out of net cash, not reported on the latest balance sheet: %s." % s)

    # price and market value
    chart = ik.yahoo_chart(ticker, "5d", "1d", ttl_hours=1)
    price = args.price or chart["meta"].get("regularMarketPrice") or chart["close"][-1]
    price_asof = chart["dates"][-1]
    quote = {} if args.price else ik.yahoo_quote([ticker]).get(ticker.upper(), {})
    sh = ik.shares_outstanding(facts)
    foreign = currency != "USD" or any(f.startswith(("20-F", "40-F")) for f in
                                       subs.get("filings", {}).get("recent", {}).get("form", []))
    if args.market_cap:
        mcap, mcap_src = args.market_cap, "given with --market-cap"
        shares_eff = mcap / price
    elif sh and not foreign:
        mcap, mcap_src = price * sh["val"], "price x %s shares (%s)" % ("{:,.0f}".format(sh["val"]), sh["source"])
        shares_eff = sh["val"]
        ymc = ik.quote_market_cap_usd(quote)
        if ymc and abs(ymc / mcap - 1) > 0.15:
            warnings.append("SEC shares x price (%s) is more than 15%% away from Yahoo's market cap (%s), usually a "
                            "share class or a split; used Yahoo's." % (ik.money(mcap), ik.money(ymc)))
            mcap, mcap_src = ymc, "Yahoo market cap (SEC share count did not match this share class)"
            shares_eff = mcap / price
    elif quote.get("marketCap"):
        mcap = ik.quote_market_cap_usd(quote) * (price / quote.get("regularMarketPrice", price))
        mcap_src = "Yahoo market cap" + (" (foreign filer / ADR)" if foreign else "")
        shares_eff = mcap / price
    else:
        raise ik.InvestError("Could not work out %s's market cap; pass --market-cap." % ticker)

    ev = mcap - nc["val"] * fx
    if financial:
        warnings.append("SIC %s is a financial company. Its cash and debt are raw material, not spare cash, so this "
                        "values its earnings against its market cap instead of free cash flow against enterprise value." % sic)

    return {
        "financial": financial, "net_income": (ni["val"] * fx) if ni else None,
        "dna": (dna["val"] * fx) if dna else None,
        "ticker": ticker.upper(), "name": title, "cik": cik, "sic": sic, "currency": currency, "fx": fx,
        "price": price, "price_asof": price_asof, "market_cap": mcap, "market_cap_source": mcap_src,
        "shares_effective": shares_eff, "ev": ev, "net_cash": nc["val"] * fx, "net_cash_detail": nc,
        "revenue": rev["val"] * fx, "revenue_period": rev["method"],
        "cfo": cfo["val"] * fx, "capex": (capex["val"] if capex else 0) * fx, "sbc": sbc_val * fx,
        "fcf": fcf * fx, "fcf_period": cfo["method"],
        "sources": {"revenue": rev["sources"], "cash_flow": cfo["sources"], "balance_sheet": nc["sources"]},
        "annual_revenue": [(r["end"], r["val"] * fx) for r in ik.annual_series(facts, "revenue", currency)],
        "annual_fcf": _annual_fcf(facts, currency, fx, args.basis),
        "warnings": warnings, "facts_link": ik.cik_link(cik),
    }


def _annual_fcf(facts, currency, fx, basis):
    cfo = {r["end"]: r["val"] for r in ik.annual_series(facts, "cfo", currency)}
    capex = {r["end"]: r["val"] for r in ik.annual_series(facts, "capex", currency)}
    sbc = {r["end"]: r["val"] for r in ik.annual_series(facts, "sbc", currency)}
    rev = {r["end"]: r["val"] for r in ik.annual_series(facts, "revenue", currency)}
    out = []
    for end in sorted(cfo):
        f = cfo[end] - capex.get(end, 0) - (sbc.get(end, 0) if basis == "fcf-sbc" else 0)
        r = rev.get(end)
        out.append({"end": end, "fcf": f * fx, "margin": (f / r) if r else None})
    return out


# ---------------------------------------------------------------- base rates

def revenue_by_company(year):
    """{cik: revenue} for calendar year `year`, the largest of the revenue tags each company used."""
    tags = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]
    out = {}
    for tag in tags:
        if tag == "SalesRevenueNet" and year > 2018:
            continue
        if tag.startswith("RevenueFromContract") and year < 2016:
            continue
        frame = ik.sec_frame(tag, "CY%d" % year)
        for row in frame.get("data", []):
            v = row.get("val")
            if v and v > 0 and v > out.get(row["cik"], 0):
                out[row["cik"]] = v
    return out


def base_rates(revenue_now, years, progress=None):
    """10-year (or `years`-year) revenue growth of every company that started in the
    same size bucket, pooled across every start year from 2011 on.
    Cached in investor/cache/base_rates-<years>y.json."""
    last_year = ik.today().year - 1
    starts = [y for y in range(FIRST_FRAME_YEAR, last_year - years + 1)]
    if not starts:
        return None
    cache = os.path.join(ik.cache_dir(), "base_rates-%dy.json" % years)
    windows = None
    if os.path.exists(cache) and not os.environ.get("INVESTOR_OFFLINE"):
        with open(cache, encoding="utf-8") as f:
            stored = json.load(f)
        if stored.get("starts") == starts:
            windows = stored["windows"]
    if windows is None:
        if progress is None:
            progress = sys.stderr.isatty()
        if not progress:
            sys.stderr.write("Base rates: reading SEC revenue for every filer (first run only, about a minute)...\n")
        need = sorted(set(starts + [s + years for s in starts]))
        revs = {}
        for i, y in enumerate(need):
            if progress:
                sys.stderr.write("\rBase rates: reading SEC revenue for every filer, %d of %d years..." % (i + 1, len(need)))
                sys.stderr.flush()
            revs[y] = revenue_by_company(y)
        if progress:
            sys.stderr.write("\r" + " " * 70 + "\r")
        windows = []
        for s in starts:
            a, b = revs[s], revs[s + years]
            for cik, v0 in a.items():
                v1 = b.get(cik)
                if v1 and v0 >= 1e6:  # ignore shells with under $1M revenue
                    windows.append([s, v0, (v1 / v0) ** (1.0 / years) - 1])
        if not os.environ.get("INVESTOR_OFFLINE"):
            os.makedirs(ik.cache_dir(), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"starts": starts, "windows": windows}, f)
    lo, hi = size_bucket(revenue_now)
    growth = sorted(w[2] for w in windows if lo <= w[1] < hi)
    if len(growth) < 30:
        return None
    return {"bucket": bucket_label(lo, hi), "n": len(growth), "starts": "%d-%d" % (starts[0], starts[-1]),
            "years": years, "p10": ik.percentile(growth, 10), "p25": ik.percentile(growth, 25),
            "p50": ik.percentile(growth, 50), "p75": ik.percentile(growth, 75),
            "p90": ik.percentile(growth, 90), "sorted": growth}


def share_at_or_above(sorted_growth, g):
    if not sorted_growth or g is None:
        return None
    import bisect as _b
    return 1 - _b.bisect_left(sorted_growth, g) / float(len(sorted_growth))


# ---------------------------------------------------------------- the model

def solve(inp, args):
    """Implied growth and the sensitivity grid."""
    years, r, gt = args.years, args.discount, args.terminal
    rev, fcf = inp["revenue"], inp["fcf"]
    margin0 = fcf / rev if rev else 0
    hist = [x["margin"] for x in inp["annual_fcf"][-10:] if x["margin"] is not None]
    mode = args.mode
    if mode == "auto":
        if inp["financial"]:
            mode = "earnings"
        else:
            mode = "fcf" if fcf > 0 and margin0 >= 0.05 else "revenue"
    ev_used = inp["ev"]
    if mode == "earnings":
        if not inp["net_income"] or inp["net_income"] <= 0:
            raise ik.InvestError("Earnings mode needs positive trailing net income.")
        fcf, ev_used = inp["net_income"], inp["market_cap"]
        margin0 = fcf / rev if rev else 0
    if args.target_margin is not None:
        target, target_src = args.target_margin, "given with --target-margin"
    else:
        best = max(hist) if hist else 0
        target = min(max(best, 0.08), 0.30)
        target_src = ("its best free-cash-flow margin of the last %d years (%s), kept between 8%% and 30%%"
                      % (len(hist), ik.pct(best)))

    def implied(ev, rr, gg):
        if mode in ("fcf", "earnings"):
            return ik.implied_growth_fcf(ev, fcf, years, rr, gg)
        return ik.implied_growth_revenue(ev, rev, margin0, target, years, rr, gg)

    g = implied(ev_used, r, gt)
    grid = []
    for rr in (r - 0.01, r, r + 0.01):
        grid.append([rr] + [implied(ev_used, rr, gg) for gg in (gt - 0.005, gt, gt + 0.005)])

    # Growth capex check: heavy investment depresses today's free cash flow and so
    # inflates the growth the price seems to need. Re-run with capex = depreciation.
    maint = None
    if mode == "fcf" and inp["dna"] and inp["dna"] > 0 and inp["capex"] > 1.3 * inp["dna"]:
        fcf_m = inp["cfo"] - inp["dna"] - (inp["sbc"] if args.basis == "fcf-sbc" else 0)
        if fcf_m > 0:
            maint = {"capex_to_dna": inp["capex"] / inp["dna"], "fcf": fcf_m,
                     "implied_growth": ik.implied_growth_fcf(ev_used, fcf_m, years, r, gt)}
    return {"mode": mode, "implied_growth": g, "margin0": margin0, "target_margin": target if mode == "revenue" else None,
            "target_margin_source": target_src if mode == "revenue" else None, "grid": grid,
            "grid_terminals": [gt - 0.005, gt, gt + 0.005], "base": fcf if mode != "revenue" else rev,
            "ev_used": ev_used, "maintenance": maint}


def track_record(annual_revenue, ttm_revenue):
    """Revenue CAGR over the last 3, 5 and 10 fiscal years (as reported, not inflation adjusted)."""
    out = {}
    vals = [v for _, v in annual_revenue]
    for n in (3, 5, 10):
        if len(vals) > n:
            out[n] = ik.cagr(vals[-1 - n], vals[-1], n)
    return out


def scenarios(inp, model, br, tr, args):
    """Growth scenarios turned into annual returns from today's price."""
    rows = []
    mode = model["mode"]

    def ret(g):
        if g is None:
            return None
        return ik.scenario_return(model["ev_used"], model["base"], g, args.years, args.discount, args.terminal,
                                  mode="revenue" if mode == "revenue" else "fcf",
                                  margin0=model["margin0"], margin_target=model["target_margin"])

    if br:
        rows.append({"key": "typical", "label": "Grows like a typical company its size (median)", "growth": br["p50"]})
        rows.append({"key": "top_quartile", "label": "Grows like a top-quarter company its size", "growth": br["p75"]})
    if model["implied_growth"] is not None:
        rows.append({"key": "priced_in", "label": "Grows exactly as priced in", "growth": model["implied_growth"]})
    own = tr.get(10) if tr.get(10) is not None else tr.get(5)
    if own is not None:
        rows.append({"key": "track_record", "label": "Keeps its own %d-year pace" % (10 if tr.get(10) is not None else 5),
                     "growth": own})
    for g in args.scenario or []:
        rows.append({"key": "custom_%g" % g, "label": "Your scenario", "growth": g})
    for row in rows:
        row["annual_return"] = ret(row["growth"])
        if br:
            row["share_of_peers"] = share_at_or_above(br["sorted"], row["growth"])
    rows.sort(key=lambda x: x["growth"])
    return rows


# ---------------------------------------------------------------- output

def headline(inp, model, br, tr, args):
    g = model["implied_growth"]
    what = {"fcf": "free cash flow", "revenue": "revenue", "earnings": "earnings"}[model["mode"]]
    if g is None:
        return ("At %s, %s's price implies more than %s a year of %s growth for %d years: beyond what this model solves."
                % (ik.money(inp["price"]), inp["ticker"], ik.pct(ik.GROWTH_HI, 0), what, args.years))
    s = ("At %s, the price assumes %s grows %s a year for the next %d years, if investors want %s a year back."
         % (ik.money(inp["price"]), what, ik.pct(g), args.years, ik.pct(args.discount, 0)))
    if br:
        share = share_at_or_above(br["sorted"], g)
        s += (" Of US companies its size, %s grew revenue that fast over a decade (%d company-decades, %s)."
              % (ik.pct(share, 0), br["n"], br["starts"] + " starts"))
    return s


def render(inp, model, br, tr, scen, args):
    L = []
    L.append("PRICED-IN: %s (%s)" % (inp["ticker"], inp["name"]))
    L.append(headline(inp, model, br, tr, args))
    L.append("")
    L.append("What you pay")
    L.append("  Price           %s (%s)" % (ik.money(inp["price"]), inp["price_asof"]))
    L.append("  Market cap      %s  %s" % (ik.money(inp["market_cap"]), inp["market_cap_source"]))
    nc = inp["net_cash_detail"]
    if model["mode"] != "earnings":
        L.append("  Net cash        %s  cash %s + short-term investments %s - debt %s (balance sheet %s)"
                 % (ik.money(inp["net_cash"]), ik.money(nc["cash"] * inp["fx"]), ik.money(nc["investments"] * inp["fx"]),
                    ik.money(nc["debt"] * inp["fx"]), nc["end"]))
    if model["mode"] == "earnings":
        L.append("  Valued on       %s  market cap (a financial company's cash and debt are its inventory)"
                 % ik.money(inp["market_cap"]))
    else:
        L.append("  Enterprise val  %s  (what the business itself is priced at)" % ik.money(inp["ev"]))
    if inp["currency"] != "USD":
        L.append("  Reported in %s, converted at %.4f USD each" % (inp["currency"], inp["fx"]))
    L.append("")
    L.append("What the business makes (%s)" % inp["fcf_period"])
    L.append("  Revenue         %s" % ik.money(inp["revenue"]))
    if model["mode"] == "earnings":
        L.append("  Net income      %s  (%s of revenue)" % (ik.money(inp["net_income"]), ik.pct(model["margin0"])))
    else:
        L.append("  Operating cash  %s" % ik.money(inp["cfo"]))
        L.append("  - Capex         %s" % ik.money(inp["capex"]))
        if args.basis == "fcf-sbc":
            L.append("  - Stock comp    %s  (treated as a real cost: it dilutes you)" % ik.money(inp["sbc"]))
        L.append("  = Free cash     %s  (%s of revenue)" % (ik.money(inp["fcf"]), ik.pct(inp["fcf"] / inp["revenue"])))
    m = model["maintenance"]
    if m:
        L.append("  Growth-capex check: capex is %.1fx depreciation, so much of it is building for growth."
                 % m["capex_to_dna"])
        L.append("  If capex were only depreciation, free cash would be %s and the price would assume %s growth."
                 % (ik.money(m["fcf"]), ik.pct(m["implied_growth"]) if m["implied_growth"] is not None else "n/a"))
        L.append("  The truth sits between the two: some of that capex is needed just to stand still.")
    L.append("")
    L.append("The model: %d years of growth, then %s a year forever, discounted at %s a year: the return investors"
             % (args.years, ik.pct(args.terminal), ik.pct(args.discount)))
    L.append("are assumed to demand, close to the stock market's long-run average. A steadier company may deserve less")
    L.append("(see the sensitivity table); a riskier one more. Change it with --discount.")
    if model["mode"] == "earnings":
        L.append("Mode: earnings. Net income grows; for a bank or insurer it is close to what owners can take out.")
    elif model["mode"] == "revenue":
        L.append("Mode: revenue. Free cash flow is too thin to grow from (%s of revenue), so revenue grows and the"
                 % ik.pct(model["margin0"]))
        L.append("free-cash-flow margin moves in a straight line to %s by year %d: %s."
                 % (ik.pct(model["target_margin"]), args.years, model["target_margin_source"]))
    else:
        L.append("Mode: free cash flow. At a steady margin, revenue has to grow at the same rate.")
    L.append("")
    L.append("Sensitivity: implied growth when the assumptions move")
    hdr = ["discount rate"] + ["terminal %s" % ik.pct(t) for t in model["grid_terminals"]]
    L.append(ik.table([[ik.pct(row[0])] + [ik.pct(x) if x is not None else "n/a" for x in row[1:]]
                       for row in model["grid"]], hdr))
    L.append("")
    L.append("Its own track record (revenue, as reported)")
    if tr:
        L.append("  " + "   ".join("%d-year %s" % (n, ik.pct(tr[n])) for n in sorted(tr) if tr[n] is not None))
    else:
        L.append("  Not enough annual history in its filings.")
    if br:
        L.append("")
        L.append("Base rate: %d-year revenue growth of US companies that started with %s"
                 % (br["years"], br["bucket"]))
        L.append("  %d company-decades, start years %s, from SEC XBRL frames" % (br["n"], br["starts"]))
        L.append("  10th %s   25th %s   median %s   75th %s   90th %s"
                 % tuple(ik.pct(br[k]) for k in ("p10", "p25", "p50", "p75", "p90")))
        L.append("  Survivors only: companies that were acquired or went under drop out, so real odds are a bit worse.")
    L.append("")
    L.append("If you buy at %s and the company grows..." % ik.money(inp["price"]))
    rows = []
    for s in scen:
        rows.append([s["label"], ik.pct(s["growth"]),
                     ik.pct(s["annual_return"]) if s["annual_return"] is not None else "n/a",
                     ik.pct(s.get("share_of_peers"), 0) if s.get("share_of_peers") is not None else ""])
    L.append(ik.table(rows, ["scenario", "growth/yr", "your return/yr", "peers this fast"]))
    L.append("  Return = the yearly return from today's price if that growth happens and the market then values")
    L.append("  the company like any business discounted at %s. At the priced-in growth it equals exactly %s."
             % (ik.pct(args.discount), ik.pct(args.discount)))
    if inp["warnings"]:
        L.append("")
        L.append("Check:")
        for w in inp["warnings"]:
            L.append("  - " + w)
    L.append("")
    L.append("Sources: SEC company facts %s; filings %s; price from Yahoo Finance."
             % (inp["facts_link"], ", ".join(sorted(set(a for a in inp["sources"]["cash_flow"] if a)))))
    L.append(ik.DISCLAIMER)
    return "\n".join(L)


def chart(inp, model, br, tr, args):
    """Histogram of peers' decade growth with the priced-in rate marked."""
    if not br:
        return None
    return draw_histogram(inp["ticker"], model, br, tr, inline=False)


def marker_set(model, br, tr):
    """(value, line class, swatch var, label) for the lines drawn over the histogram."""
    marks = []
    if model["implied_growth"] is not None:
        marks.append((model["implied_growth"], "s2l", "--s2", "Priced in: %s" % ik.pct(model["implied_growth"])))
    own = tr.get(10) if tr.get(10) is not None else tr.get(5)
    if own is not None:
        marks.append((own, "s1l", "--s1", "Its own %d-year pace: %s" % (10 if tr.get(10) is not None else 5, ik.pct(own))))
    marks.append((br["p50"], "mutl", "--muted", "Typical company its size: %s" % ik.pct(br["p50"])))
    return [(v, c, var, lbl + (" (beyond the chart)" if v > 0.40 else "")) for v, c, var, lbl in marks]


def draw_histogram(ticker, model, br, tr, inline):
    W, H = 960, (440 if inline else 540)
    ml, mr, mb = 56, 24, 64
    mt = 40 if inline else 96
    lo, hi = -0.15, 0.40
    bins = 22
    width = (hi - lo) / bins
    counts = [0] * bins
    for g in br["sorted"]:
        k = int((min(max(g, lo), hi - 1e-9) - lo) / width)
        counts[k] += 1
    top = max(counts) or 1
    pw, ph = W - ml - mr, H - mt - mb

    def x(v):
        return ml + (min(max(v, lo), hi) - lo) / (hi - lo) * pw

    def y(c):
        return mt + ph - c / float(top) * ph

    b = []
    if not inline:
        b.append('<text class="title" x="%d" y="40">What %s\'s price assumes</text>' % (ml, ik.esc(ticker)))
        b.append('<text class="sub" x="%d" y="66">%d-year revenue growth of %d US company-decades that started with %s</text>'
                 % (ml, br["years"], br["n"], ik.esc(br["bucket"])))
    for c in ik.nice_ticks(0, top, 4):
        b.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, W - mr, y(c), y(c)))
        b.append('<text class="axis" x="%d" y="%.1f" text-anchor="end">%d</text>' % (ml - 8, y(c) + 4, c))
    bw = pw / float(bins)
    for i, c in enumerate(counts):
        if not c:
            continue
        x0 = ml + i * bw + 1
        a, z = lo + i * width, lo + (i + 1) * width
        rng = ("%s or less" % ik.pct(z, 1)) if i == 0 else ("%s or more" % ik.pct(a, 1)) if i == bins - 1 else \
            "%s to %s" % (ik.pct(a, 1), ik.pct(z, 1))
        h = ph * c / float(top)
        r = min(4, (bw - 2) / 2, h)
        b.append('<path class="bar" d="M%.1f %.1f V%.1f Q%.1f %.1f %.1f %.1f H%.1f Q%.1f %.1f %.1f %.1f V%.1f Z"%s/>'
                 % (x0, mt + ph, mt + ph - h + r, x0, mt + ph - h, x0 + r, mt + ph - h, x0 + bw - 2 - r,
                    x0 + bw - 2, mt + ph - h, x0 + bw - 2, mt + ph - h + r, mt + ph,
                    ik.tip("%d company-decades grew %s a year" % (c, rng))))
    b.append('<line class="base" x1="%d" x2="%d" y1="%d" y2="%d"/>' % (ml, W - mr, mt + ph, mt + ph))
    for t in (-0.1, 0, 0.1, 0.2, 0.3, 0.4):
        b.append('<text class="axis" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x(t), mt + ph + 22, ik.pct(t, 0)))
    b.append('<text class="axis" x="%d" y="%d" text-anchor="middle">Revenue growth a year, over a decade '
             '(companies per bar on the left)</text>' % (ml + pw / 2, mt + ph + 46))
    for i, (v, cls, var, lbl) in enumerate(marker_set(model, br, tr)):
        xv = x(v)
        b.append('<line class="%s" x1="%.1f" x2="%.1f" y1="%d" y2="%d"%s/>' % (cls, xv, xv, mt - 6, mt + ph, ik.tip(lbl)))
        if not inline:
            anchor = "end" if xv > W - 260 else "start"
            dx = -8 if anchor == "end" else 8
            b.append('<text class="lbl" x="%.1f" y="%d" text-anchor="%s">%s%s</text>'
                     % (xv + dx, mt + 12 + 20 * i, anchor, ik.esc(lbl), ""))
    return ik.svg_wrap(W, H, "\n".join(b), inline=inline,
                       label="Histogram of decade revenue growth for companies of this size, with the priced-in rate marked")


def report(inp, model, br, tr, scen, args):
    """The HTML report page."""
    t, g = inp["ticker"], model["implied_growth"]
    what = {"fcf": "free cash flow", "revenue": "revenue", "earnings": "earnings"}[model["mode"]]
    own = tr.get(10) if tr.get(10) is not None else tr.get(5)
    share = share_at_or_above(br["sorted"], g) if br and g is not None else None
    kpis = [{"k": "Growth the price assumes", "v": ik.pct(g) + " a year" if g is not None else "over 150%",
             "s": "%s, for %d years, at a %s required return" % (what, args.years, ik.pct(args.discount, 0)), "hero": True}]
    if own is not None:
        kpis.append({"k": "Its own pace", "v": ik.pct(own), "s": "revenue growth, last %d years" % (10 if tr.get(10) is not None else 5)})
    if br:
        kpis.append({"k": "Typical company its size", "v": ik.pct(br["p50"]), "s": "median decade of revenue growth"})
        kpis.append({"k": "Peers who grew that fast", "v": ik.pct(share, 0) if share is not None else "n/a",
                     "s": "of %d company-decades" % br["n"]})
    body = [ik.h_kpis(kpis)]
    if br:
        legend = [(var, lbl, "line") for _, _, var, lbl in marker_set(model, br, tr)]
        body.append(ik.h_section(
            "How rare is that growth?",
            ik.h_figure(draw_histogram(t, model, br, tr, inline=True),
                        "Each bar counts US companies that started a decade with %s, by how fast their revenue grew "
                        "over the next %d years (SEC XBRL frames, start years %s). Survivors only: companies that were "
                        "acquired or failed drop out, so the real odds are a little worse." % (
                            ik.esc(br["bucket"]), br["years"], br["starts"]), legend)))
    rows, mark = [], None
    for i, s in enumerate(scen):
        rows.append([s["label"], ik.pct(s["growth"]), ik.pct(s["annual_return"]) if s["annual_return"] is not None else "n/a",
                     ik.pct(s.get("share_of_peers"), 0) if s.get("share_of_peers") is not None else "n/a"])
        if s["key"] == "priced_in":
            mark = i
    body.append(ik.h_section(
        "What you would earn from today's price", ik.h_table(["If it grows", "Growth a year", "Your return a year",
                                                             "Peers this fast"], rows, mark),
        lede="The yearly return from paying %s, if that growth happens and the market then values the company at the "
             "same %s required return. At exactly the priced-in growth it equals %s." % (
                 ik.esc(ik.money(inp["price"])), ik.pct(args.discount, 0), ik.pct(args.discount, 0))))
    nc = inp["net_cash_detail"]
    pay = [("Share price", ik.money(inp["price"]), inp["price_asof"]),
           ("Market cap", ik.money(inp["market_cap"]), inp["market_cap_source"])]
    if model["mode"] != "earnings":
        pay += [("Net cash", ik.money(inp["net_cash"]), "cash %s + investments %s - debt %s" % (
            ik.money(nc["cash"] * inp["fx"]), ik.money(nc["investments"] * inp["fx"]), ik.money(nc["debt"] * inp["fx"]))),
                ("Enterprise value", ik.money(inp["ev"]), "what the business itself is priced at")]
    make = [("Revenue", ik.money(inp["revenue"]), inp["revenue_period"])]
    if model["mode"] == "earnings":
        make.append(("Net income", ik.money(inp["net_income"]), "%s of revenue" % ik.pct(model["margin0"])))
    else:
        make += [("Operating cash flow", ik.money(inp["cfo"]), None), ("Capital spending", "-" + ik.money(inp["capex"]), None)]
        if args.basis == "fcf-sbc":
            make.append(("Stock compensation", "-" + ik.money(inp["sbc"]), "counted as a cost: it dilutes owners"))
        make.append(("Free cash flow", ik.money(inp["fcf"]), "%s of revenue" % ik.pct(inp["fcf"] / inp["revenue"])))
    body.append(ik.h_section("What you pay, and what it makes", ik.h_kv([pay, make])))
    m = model["maintenance"]
    if m:
        body.append(ik.h_callout("Growth-capex check", [
            "Capital spending is %.1fx depreciation, so much of it is building for growth." % m["capex_to_dna"],
            "If capex were only depreciation, free cash flow would be %s and the price would assume %s growth." % (
                ik.money(m["fcf"]), ik.pct(m["implied_growth"]) if m["implied_growth"] is not None else "n/a"),
            "The truth sits between the two: some of that spending is needed just to stand still."]))
    grid_rows = [[ik.pct(r[0])] + [ik.pct(x) if x is not None else "n/a" for x in r[1:]] for r in model["grid"]]
    body.append(ik.h_section(
        "If the assumptions move", ik.h_table(["Required return"] + ["Long-run growth %s" % ik.pct(x) for x in model["grid_terminals"]],
                                              grid_rows, 1),
        lede="Growth the price implies under other required returns and long-run growth rates. A steadier company may "
             "deserve a lower required return; a riskier one a higher."))
    if inp["warnings"]:
        body.append(ik.h_callout("Check before you use this", inp["warnings"], warn=True))
    mode_text = {
        "fcf": "Free cash flow grows at one rate for %d years, then %s a year forever, discounted at %s." % (
            args.years, ik.pct(args.terminal), ik.pct(args.discount)),
        "revenue": "Free cash flow is negative or thin, so revenue grows and the free-cash-flow margin moves in a straight "
                   "line to %s by year %d (%s)." % (ik.pct(model["target_margin"] or 0), args.years,
                                                     model["target_margin_source"] or ""),
        "earnings": "A financial company: its cash and debt are its inventory, so this grows net income and compares it "
                    "with the market cap."}[model["mode"]]
    body.append(ik.h_details("How this was worked out", ik.h_paras(
        "This is a reverse DCF. Instead of guessing a fair value, it takes today's price as given and solves for the "
        "growth rate that makes a standard discounted-cash-flow model equal it.", mode_text,
        "Free cash flow is operating cash flow minus capital spending minus stock compensation, over the trailing "
        "twelve months, from the company's 10-K and 10-Q filings. Market cap uses SEC share counts, or Yahoo's figure "
        "for ADRs and unusual share classes.",
        "Base rates pool every %d-year window starting 2011 or later for US filers that started in the same revenue "
        "bracket. Free-cash-flow growth and revenue growth match only when the margin holds steady." % args.years)))
    links = [("SEC company filings for %s" % inp["name"], inp["facts_link"])]
    links += ik.filing_links(inp["cik"], sorted(set(a for a in inp["sources"]["cash_flow"] + inp["sources"]["balance_sheet"] if a)))
    links.append(("Price: Yahoo Finance %s" % t, "https://finance.yahoo.com/quote/%s" % ik.yahoo_symbol(t)))
    body.append(ik.h_section("Sources", ik.h_sources(links)))
    thesis = ik.esc(headline(inp, model, br, tr, args))
    return ik.report_page("%s Priced-In" % t, ["Priced-In", t, inp["price_asof"]], inp["name"], thesis, "\n".join(body),
                          "The growth %s's price assumes, against its own record and every US company its size." % t)


def main(argv=None):
    ik.setup_console()
    ap = argparse.ArgumentParser(description="Priced-In Calculator: the growth today's price assumes.")
    ap.add_argument("ticker")
    ap.add_argument("--discount", type=float, default=None, help="annual discount rate (default 0.09)")
    ap.add_argument("--terminal", type=float, default=None, help="growth after the forecast years (default 0.025)")
    ap.add_argument("--years", type=int, default=None, help="forecast years (default 10)")
    ap.add_argument("--basis", choices=["fcf-sbc", "fcf"], default="fcf-sbc",
                    help="free cash flow after stock comp (default) or before it")
    ap.add_argument("--mode", choices=["auto", "fcf", "revenue"], default="auto")
    ap.add_argument("--target-margin", type=float, help="revenue mode: free-cash-flow margin reached by the last year")
    ap.add_argument("--price", type=float, help="use this share price instead of the market's")
    ap.add_argument("--market-cap", type=float, help="override the market cap (USD)")
    ap.add_argument("--scenario", type=float, action="append", help="extra growth scenario, e.g. 0.15 (repeatable)")
    ap.add_argument("--no-base-rates", action="store_true", help="skip the SEC-wide base rates")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cfg = ik.load_config().get("priced_in", {})
    for k in ("discount", "terminal", "years"):
        if getattr(args, k) is None:
            setattr(args, k, cfg.get(k, DEFAULTS[k]))
    if args.discount <= args.terminal:
        ap.error("--discount must be above --terminal")
    try:
        inp = gather(args.ticker, args)
        model = solve(inp, args)
        tr = track_record(inp["annual_revenue"], inp["revenue"])
        br = None if args.no_base_rates else base_rates(inp["revenue"], args.years)
        scen = scenarios(inp, model, br, tr, args)
    except ik.InvestError as e:
        print("pricedin: %s" % e, file=sys.stderr)
        return 2

    payload = {
        "price": inp["price"], "price_asof": inp["price_asof"], "market_cap": inp["market_cap"],
        "shares_effective": inp["shares_effective"], "net_cash": inp["net_cash"], "ev": inp["ev"],
        "revenue": inp["revenue"], "fcf": inp["fcf"], "basis": args.basis, "mode": model["mode"],
        "margin0": model["margin0"], "target_margin": model["target_margin"],
        "discount": args.discount, "terminal": args.terminal, "years": args.years,
        "implied_growth": model["implied_growth"], "grid": model["grid"],
        "model_base": model["base"], "ev_used": model["ev_used"], "maintenance": model["maintenance"],
        "financial": inp["financial"],
        "track_record": {str(k): v for k, v in tr.items()},
        "base_rates": {k: v for k, v in (br or {}).items() if k != "sorted"} or None,
        "base_rate_sorted": [round(x, 5) for x in br["sorted"]] if br else None,
        "scenarios": scen, "warnings": inp["warnings"], "name": inp["name"], "cik": inp["cik"],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(render(inp, model, br, tr, scen, args))
    if not args.no_write and not args.price:
        path = ik.save_section(inp["ticker"], "priced_in", payload)
        svg = chart(inp, model, br, tr, args)
        extra = ""
        if svg:
            extra = ", " + ik.write_text(os.path.join(ik.data_dir(), "charts", "%s-priced-in.svg" % inp["ticker"]), svg)
        rep = ik.write_report("%s-priced-in" % inp["ticker"], report(inp, model, br, tr, scen, args))
        if not args.json:
            print("Saved %s%s.\nReport: %s" % (path, extra, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
