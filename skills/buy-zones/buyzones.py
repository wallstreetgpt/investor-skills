#!/usr/bin/env python3
"""Buy Zone Finder: where is a good price to start buying, and how likely is it to get there?

Support and resistance for investors, found by rules rather than by eye:

  * swing lows and highs on WEEKLY bars over 5 years (a low that is the
    lowest of the 4 weeks either side), weighted toward recent ones
  * old ceilings that became floors: a swing high the price later held above
    for at least 4 weeks
  * volume shelves: price bands where the most shares changed hands in the
    last 2 years (each day's volume spread across its high-low range)
  * the 200-day and 200-week moving averages

Nearby levels are merged into zones (bands, not lines) and scored 0-100 on
that evidence. Then three honesty checks for every zone:

  * its record: each time price came down into the zone after it formed,
    did it hold (bounced 5% above it) or break (closed 2% below it)?
  * the odds price even gets there within 6 and 12 months, from 10,000
    bootstrapped paths of the stock's own last 2 years of daily moves,
    plus how often a fall that deep happened in its real last 5 years
  * the valuation at that price, if /priced-in has run: the growth the price
    would assume there, and the price where it only assumes what a
    top-quarter company its size has achieved (the valuation floor). A zone
    that is strong on the chart AND at or under that floor is a DOUBLE FLOOR.

  python buyzones.py NVDA
  python buyzones.py KO --max-drop 0.30
"""

import argparse
import bisect
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import investkit as ik  # noqa: E402

PIVOT_K = 4            # weeks on each side for a swing high or low
LOOKBACK_WEEKS = 260   # 5 years of pivots
HALF_LIFE_WEEKS = 78   # a pivot from 1.5 years ago counts half
PROFILE_DAYS = 504     # 2 years of volume profile
PROFILE_BIN = 0.01     # 1% price bins (log spaced)
TEST_DAYS = 60         # trading days to decide a test
HELD_UP = 0.05         # held: closes 5% above the zone
BROKE_DOWN = 0.02      # broke: closes 2% below the zone
SIM_PATHS = 10000
SIM_BLOCK = 20         # mean block length in trading days
DOUBLE_FLOOR_SCORE = 50


# ---------------------------------------------------------------- levels

def sma(xs, n):
    return sum(xs[-n:]) / float(n) if len(xs) >= n else None


def weekly_atr_pct(wk, n=26):
    rows = list(zip(wk["high"], wk["low"], wk["close"]))[-n:]
    trs = []
    prev = None
    for h, lo, c in rows:
        tr = h - lo if prev is None else max(h, prev) - min(lo, prev)
        trs.append(tr / c)
        prev = c
    return ik.mean(trs)


def find_pivots(wk, k=PIVOT_K, lookback=LOOKBACK_WEEKS):
    n = len(wk["close"])
    start = max(k, n - lookback)
    lows, highs = [], []
    for i in range(start, n - 1):
        lo_win = wk["low"][max(0, i - k):i + k + 1]
        hi_win = wk["high"][max(0, i - k):i + k + 1]
        if wk["low"][i] == min(lo_win):
            lows.append(i)
        if wk["high"][i] == max(hi_win):
            highs.append(i)
    return lows, highs


def flipped(wk, i, hold_weeks=4):
    """A swing high at week i that price later closed above for hold_weeks in a row."""
    level = wk["high"][i]
    run = 0
    for c in wk["close"][i + 1:]:
        run = run + 1 if c > level else 0
        if run >= hold_weeks:
            return True
    return False


def volume_profile(daily, days=PROFILE_DAYS, step=PROFILE_BIN):
    """Volume by 1% price bin over the last `days`, smoothed over 3 bins.
    Returns (bin_low_prices, smoothed_volumes)."""
    hi_all = max(daily["high"][-days:])
    lo_all = min(daily["low"][-days:])
    base = math.log(lo_all)
    nb = int((math.log(hi_all) - base) / step) + 1
    vol = [0.0] * nb
    for h, lo, v in zip(daily["high"][-days:], daily["low"][-days:], daily["volume"][-days:]):
        if not v or h <= 0 or lo <= 0:
            continue
        a = int((math.log(lo) - base) / step)
        b = int((math.log(h) - base) / step)
        share = v / float(b - a + 1)
        for j in range(a, b + 1):
            vol[j] += share
    sm = [sum(vol[max(0, j - 1):j + 2]) / len(vol[max(0, j - 1):j + 2]) for j in range(nb)]
    prices = [math.exp(base + (j + 0.5) * step) for j in range(nb)]
    return prices, sm


def volume_nodes(prices, sm):
    """Local peaks of the profile at least 1.5x the median bin. Weight = peak / tallest peak."""
    nz = sorted(x for x in sm if x > 0)
    if not nz:
        return []
    med = ik.percentile(nz, 50)
    top = max(sm)
    out = []
    for j in range(1, len(sm) - 1):
        if sm[j] >= sm[j - 1] and sm[j] >= sm[j + 1] and sm[j] >= 1.5 * med:
            out.append((prices[j], sm[j] / top))
    return out


def candidates(wk, daily):
    n = len(wk["close"])
    lows, highs = find_pivots(wk)
    out = []
    for i in lows:
        out.append({"price": wk["low"][i], "kind": "swing low", "w": 0.5 ** ((n - 1 - i) / float(HALF_LIFE_WEEKS)),
                    "date": wk["dates"][i]})
    for i in highs:
        f = flipped(wk, i)
        out.append({"price": wk["high"][i], "kind": "old ceiling, now floor" if f else "swing high",
                    "w": 0.5 ** ((n - 1 - i) / float(HALF_LIFE_WEEKS)), "date": wk["dates"][i], "flip": f})
    prices, sm = volume_profile(daily)
    for p, w in volume_nodes(prices, sm):
        out.append({"price": p, "kind": "volume shelf", "w": w, "date": None})
    ma200d = sma(daily["close"], 200)
    ma200w = sma(wk["close"], 200)
    if ma200d:
        out.append({"price": ma200d, "kind": "200-day average", "w": 1.0, "date": None})
    if ma200w:
        out.append({"price": ma200w, "kind": "200-week average", "w": 1.0, "date": None})
    return out, {"ma200d": ma200d, "ma200w": ma200w, "profile": (prices, sm)}


def cluster(cands, tol):
    """Merge levels within `tol` (a fraction) of each other into zones."""
    cands = sorted(cands, key=lambda c: c["price"])
    zones = []
    for c in cands:
        z = zones[-1] if zones else None
        if z and c["price"] <= z["center"] * (1 + tol) and c["price"] <= z["lo"] * (1 + 2.5 * tol):
            z["members"].append(c)
            ws = [max(m["w"], 0.05) for m in z["members"]]
            z["center"] = sum(m["price"] * w for m, w in zip(z["members"], ws)) / sum(ws)
            z["lo"] = min(z["lo"], c["price"])
            z["hi"] = max(z["hi"], c["price"])
        else:
            zones.append({"members": [c], "center": c["price"], "lo": c["price"], "hi": c["price"]})
    for z in zones:
        half = z["center"] * tol / 2
        if z["hi"] - z["lo"] < 2 * half:
            z["lo"] = min(z["lo"], z["center"] - half)
            z["hi"] = max(z["hi"], z["center"] + half)
    return zones


def score(z, support=True):
    """0-100 from the evidence. Returns (score, parts)."""
    kinds = [m["kind"] for m in z["members"]]
    touch_w = sum(m["w"] for m in z["members"]
                  if m["kind"] in ("swing low", "old ceiling, now floor") or (not support and m["kind"] == "swing high"))
    vol = max([m["w"] for m in z["members"] if m["kind"] == "volume shelf"] or [0])
    parts = {
        "turning points": 40 * min(1.0, touch_w / 2.5),
        "volume": 25 * vol,
        "200-day": 10 if "200-day average" in kinds else 0,
        "200-week": 10 if "200-week average" in kinds else 0,
        "ceiling turned floor": 15 if (support and "old ceiling, now floor" in kinds) else 0,
    }
    rec = z.get("record")
    if rec and rec["held"] + rec["broke"] >= 2:
        parts["record"] = 10 * (rec["held"] - rec["broke"]) / float(rec["held"] + rec["broke"])
    return max(0, min(100, round(sum(parts.values())))), parts


# ---------------------------------------------------------------- record

def zone_record(z, daily):
    """Tests of a support zone after it formed: held, broke, or still open."""
    dated = [m["date"] for m in z["members"] if m.get("date")]
    if dated:
        first = min(dated)
        start = bisect.bisect_left(daily["dates"], first) + 20  # let the zone form first
    else:
        start = max(0, len(daily["dates"]) - PROFILE_DAYS)
    lo, hi = z["lo"], z["hi"]
    c, low = daily["close"], daily["low"]
    held = broke = opened = 0
    tests = []
    above = start < len(c) and c[start] > hi
    d = start
    n = len(c)
    while d < n:
        if above and low[d] <= hi:
            outcome = "open"
            e = d
            for e in range(d, min(n, d + TEST_DAYS)):
                if c[e] < lo * (1 - BROKE_DOWN):
                    outcome = "broke"
                    break
                if c[e] >= hi * (1 + HELD_UP):
                    outcome = "held"
                    break
            tests.append((daily["dates"][d], outcome))
            held += outcome == "held"
            broke += outcome == "broke"
            opened += outcome == "open"
            above = False
            d = e + 1
            continue
        if not above and c[d] > hi * (1 + BROKE_DOWN):
            above = True
        d += 1
    return {"held": held, "broke": broke, "open": opened, "tests": tests}


# ---------------------------------------------------------------- odds

def touch_odds(daily, levels, drift_annual, seed, paths=SIM_PATHS):
    """P(price trades at or below each level within 126 and 252 trading days).
    Bootstraps the last 2 years of daily log moves in blocks (mean 20 days),
    re-centred on drift_annual. Levels are fractions of today's price."""
    closes = daily["close"][-505:]
    rs = ik.log_returns(closes)
    mu = ik.mean(rs)
    shift = math.log(1 + drift_annual) / 252 - mu
    rs = [r + shift for r in rs]
    rng = random.Random(seed)
    targets = [math.log(x) for x in levels]
    hit6 = [0] * len(levels)
    hit12 = [0] * len(levels)
    n = len(rs)
    p = 1.0 / SIM_BLOCK
    for _ in range(paths):
        s = 0.0
        m = 0.0
        i = rng.randrange(n)
        m6 = None
        for t in range(252):
            s += rs[i]
            if s < m:
                m = s
            if t == 125:
                m6 = m
            i = rng.randrange(n) if rng.random() < p else (i + 1) % n
        for k, tg in enumerate(targets):
            if m6 <= tg:
                hit6[k] += 1
            if m <= tg:
                hit12[k] += 1
    return [h / float(paths) for h in hit6], [h / float(paths) for h in hit12]


def historical_drop_odds(daily, drops, horizon=252):
    """Share of days in the last 5 years from which the price fell at least `drop`
    (by its intraday low) within the next `horizon` trading days."""
    c, low = daily["close"], daily["low"]
    n = len(c)
    starts = range(0, n - horizon)
    if not starts:
        return [None] * len(drops)
    # running minimum of the next `horizon` lows, computed backwards with a deque
    from collections import deque
    mins = [None] * n
    dq = deque()
    for j in range(n - 1, -1, -1):
        # window is j+1 .. j+horizon
        k = j + 1
        if k < n:
            while dq and low[dq[-1]] >= low[k]:
                dq.pop()
            dq.append(k)
        while dq and dq[0] > j + horizon:
            dq.popleft()
        mins[j] = low[dq[0]] if dq else None
    out = []
    for drop in drops:
        hits = sum(1 for s in starts if mins[s] is not None and mins[s] <= c[s] * (1 - drop))
        out.append(hits / float(len(starts)))
    return out


# ---------------------------------------------------------------- valuation

def implied_at_price(doc, price):
    mcap = price * doc["shares_effective"]
    ev = mcap if doc["mode"] == "earnings" else mcap - doc["net_cash"]
    if doc["mode"] in ("fcf", "earnings"):
        return ik.implied_growth_fcf(ev, doc["model_base"], doc["years"], doc["discount"], doc["terminal"])
    return ik.implied_growth_revenue(ev, doc["revenue"], doc["margin0"], doc["target_margin"],
                                     doc["years"], doc["discount"], doc["terminal"])


def price_for_growth(doc, g):
    """The share price at which the market would be assuming growth g."""
    if doc["mode"] in ("fcf", "earnings"):
        ev = ik.dcf_value(doc["model_base"], g, doc["years"], doc["discount"], doc["terminal"])
    else:
        flows, tn = ik.revenue_path_flows(doc["revenue"], doc["margin0"], doc["target_margin"], g,
                                          doc["years"], doc["terminal"])
        ev = ik.pv_flows(flows, tn, doc["discount"], doc["terminal"])
    mcap = ev if doc["mode"] == "earnings" else ev + doc["net_cash"]
    return mcap / doc["shares_effective"] if mcap > 0 else None


def peers_share(doc, g):
    s = doc.get("base_rate_sorted")
    if not s or g is None:
        return None
    return 1 - bisect.bisect_left(s, g) / float(len(s))


# ---------------------------------------------------------------- main analysis

def analyse(ticker, args):
    daily = ik.yahoo_chart(ticker, "5y", "1d", ttl_hours=6)
    wk = ik.yahoo_chart(ticker, "10y", "1wk", ttl_hours=6)
    if len(daily["close"]) < 300 or len(wk["close"]) < 60:
        raise ik.InvestError("%s needs at least about 1.5 years of trading history for buy zones." % ticker)
    price = daily["close"][-1]
    tol = min(0.06, max(0.015, 0.5 * weekly_atr_pct(wk)))
    cands, extra = candidates(wk, daily)
    zones = cluster(cands, tol)

    support = [z for z in zones if z["hi"] < price * 0.995 and z["center"] >= price * (1 - args.max_drop)]
    here = [z for z in zones if z["lo"] <= price <= z["hi"]]
    resist = [z for z in zones if z["lo"] > price * 1.005]
    for z in support + here:
        z["record"] = zone_record(z, daily)
        z["score"], z["parts"] = score(z, True)
    for z in resist:
        z["score"], z["parts"] = score(z, False)
    here = [z for z in here if z["score"] >= args.min_score]
    ladder = sorted([z for z in support if z["score"] >= args.min_score],
                    key=lambda z: -z["score"])[:args.zones]
    ladder.sort(key=lambda z: -z["center"])
    ceilings = sorted([z for z in resist if z["score"] >= args.min_score], key=lambda z: z["center"])[:2]

    doc = ik.load_ticker(ticker).get("priced_in")
    drift = (doc or {}).get("discount", 0.09)
    levels = [z["hi"] / price for z in ladder]
    o6, o12 = touch_odds(daily, levels, drift, args.seed, args.paths) if ladder else ([], [])
    hist = historical_drop_odds(daily, [1 - x for x in levels]) if ladder else []
    for z, a, b, h in zip(ladder, o6, o12, hist):
        z["odds6"], z["odds12"], z["hist12"] = a, b, h
        z["drop"] = 1 - z["hi"] / price

    val = None
    if doc and doc.get("model_base"):
        br = doc.get("base_rates") or {}
        val = {"asof": doc.get("updated"), "now_growth": implied_at_price(doc, price)}
        if br:
            val["floor_price"] = price_for_growth(doc, br["p75"])
            val["typical_price"] = price_for_growth(doc, br["p50"])
            val["p75"], val["p50"] = br["p75"], br["p50"]
        for z in ladder + here:
            z["growth"] = implied_at_price(doc, (z["lo"] + z["hi"]) / 2)
            z["peers"] = peers_share(doc, z["growth"])
            z["double"] = bool(z["score"] >= DOUBLE_FLOOR_SCORE and z["growth"] is not None and br
                               and z["growth"] <= br["p75"])
    return {"price": price, "asof": daily["dates"][-1], "tol": tol, "ladder": ladder, "here": here,
            "ceilings": ceilings, "ma200d": extra["ma200d"], "ma200w": extra["ma200w"], "val": val,
            "drift": drift, "wk": wk, "daily": daily, "doc": doc}


def evidence(z):
    kinds = {}
    for m in z["members"]:
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    order = ["swing low", "old ceiling, now floor", "swing high", "volume shelf", "200-day average", "200-week average"]
    bits = []
    for k in order:
        if k in kinds:
            bits.append(k if kinds[k] == 1 or "average" in k else "%d %ss" % (kinds[k], k))
    return ", ".join(bits)


def render(ticker, a, args):
    L = []
    price = a["price"]
    L.append("BUY ZONES: %s at %s (%s)" % (ticker, ik.money(price), a["asof"]))
    L.append("Zones are bands %s wide or more (half its typical weekly range). Strength 0-100 from turning points,"
             % ik.pct(a["tol"]))
    L.append("volume, the 200-day and 200-week averages and old ceilings that became floors.")
    ma = []
    if a["ma200d"]:
        ma.append("200-day average %s (%s)" % (ik.money(a["ma200d"]), ik.pct(a["ma200d"] / price - 1, 0, True)))
    if a["ma200w"]:
        ma.append("200-week average %s (%s)" % (ik.money(a["ma200w"]), ik.pct(a["ma200w"] / price - 1, 0, True)))
    if ma:
        L.append("  " + "   ".join(ma))
    if a["here"]:
        z = a["here"][0]
        L.append("  Price is sitting inside a zone right now: %s to %s (strength %d)." % (
            ik.money(z["lo"]), ik.money(z["hi"]), z["score"]))
    L.append("")
    if not a["ladder"]:
        L.append("No support zone scored %d or more within %s below the price." % (args.min_score, ik.pct(args.max_drop, 0)))
    for n, z in enumerate(a["ladder"], 1):
        flag = "   DOUBLE FLOOR" if z.get("double") else ""
        L.append("Zone %d: %s to %s   %s below   strength %d%s" % (
            n, ik.money(z["lo"]), ik.money(z["hi"]), ik.pct(z["drop"], 0), z["score"], flag))
        L.append("  Evidence: %s" % evidence(z))
        r = z["record"]
        if r["held"] or r["broke"] or r["open"]:
            L.append("  Record since it formed: held %d, broke %d%s" % (
                r["held"], r["broke"], (", still deciding %d" % r["open"]) if r["open"] else ""))
        else:
            L.append("  Record since it formed: not tested yet")
        L.append("  Odds price gets there: %s within 6 months, %s within 12 (simulated);"
                 " a fall this deep within a year happened from %s of days in the last 5 years"
                 % (ik.pct(z["odds6"], 0), ik.pct(z["odds12"], 0), ik.pct(z["hist12"], 0)))
        if z.get("growth") is not None:
            L.append("  At that price the market would assume %s growth a year%s" % (
                ik.pct(z["growth"]), (" (%s of companies its size grew that fast)" % ik.pct(z["peers"], 0))
                if z.get("peers") is not None else ""))
        L.append("")
    v = a["val"]
    if v:
        L.append("Valuation (from /priced-in, %s)" % v["asof"])
        if v.get("now_growth") is not None:
            L.append("  Today's price assumes %s growth a year." % ik.pct(v["now_growth"]))
        if v.get("floor_price"):
            L.append("  Valuation floor %s: there the price only assumes %s growth, what a top-quarter company"
                     % (ik.money(v["floor_price"]), ik.pct(v["p75"])))
            L.append("  its size achieves (%s below today)." % ik.pct(1 - v["floor_price"] / price, 0))
        if v.get("typical_price"):
            L.append("  At %s it would only assume a typical company's %s." % (
                ik.money(v["typical_price"]), ik.pct(v["p50"])))
        doubles = [z for z in a["ladder"] if z.get("double")]
        if doubles:
            L.append("  Double floor: the chart's support and the valuation agree at %s."
                     % " and ".join("%s-%s" % (ik.money(z["lo"]), ik.money(z["hi"])) for z in doubles))
        else:
            L.append("  No chart zone reaches the valuation floor: on valuation alone, none of these is cheap.")
    else:
        L.append("Run /priced-in %s first to see the growth priced in at each zone and the valuation floor." % ticker)
    if a["ceilings"]:
        L.append("")
        L.append("Ceilings above: " + "   ".join("%s-%s (+%s, strength %d)" % (
            ik.money(z["lo"]), ik.money(z["hi"]), ik.pct(z["lo"] / price - 1, 0), z["score"]) for z in a["ceilings"]))
    if a["ladder"]:
        z = a["ladder"][-1]
        L.append("")
        L.append("The patience trade-off: the deepest zone is %s cheaper, but price reaches it within a year in only %s"
                 % (ik.pct(z["drop"], 0), ik.pct(z["odds12"], 0)))
        L.append("of simulated futures. Waiting for a better price has a cost too: the chance you never get to buy.")
    L.append("")
    L.append("Odds: 10,000 paths of its own last 2 years of daily moves, in blocks, centred on %s a year. Records count"
             % ik.pct(a["drift"]))
    L.append("a test as held at 5%% above the zone and broken at a close 2%% below, within %d trading days. Zones are fitted"
             % TEST_DAYS)
    L.append("to this same history, so their records flatter them a little. Prices from Yahoo, split-adjusted.")
    L.append(ik.DISCLAIMER)
    return "\n".join(L)


def chart(ticker, a, inline=False):
    """Three years of weekly closes with the support zones, ceilings and the valuation floor."""
    wk = a["wk"]
    n = min(156, len(wk["close"]))
    closes = wk["close"][-n:]
    dates = wk["dates"][-n:]
    zones = a["ladder"] + a["ceilings"]
    v = a["val"] or {}
    floor = v.get("floor_price") if v.get("floor_price") and v["floor_price"] > 0 else None
    lows = [min(closes)] + [z["lo"] for z in zones] + ([floor] if floor else [])
    highs = [max(closes)] + [z["hi"] for z in zones]
    lo, hi = min(lows) * 0.95, max(highs) * 1.03
    W, H = 960, (460 if inline else 540)
    ml, mr, mb = 64, 190, 40
    mt = 20 if inline else 86
    pw, ph = W - ml - mr, H - mt - mb

    def x(i):
        return ml + i / float(n - 1) * pw

    def y(p):
        return mt + ph - (p - lo) / (hi - lo) * ph

    b = []
    if not inline:
        b.append('<text class="title" x="%d" y="40">%s: where buyers have stepped in</text>' % (ml, ik.esc(ticker)))
        b.append('<text class="sub" x="%d" y="64">Weekly closes, 3 years. Green: support zones (stronger is more solid). '
                 'Red: ceilings. Dashed: valuation floor.</text>' % ml)
    for t in ik.nice_ticks(lo, hi, 6):
        b.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, W - mr, y(t), y(t)))
        b.append('<text class="axis" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ml - 8, y(t) + 4, ik.money(t, 0)))
    labels = []
    for k, z in enumerate(a["ladder"], 1):
        op = 0.22 + 0.5 * z["score"] / 100.0
        r = z["record"]
        text = "Zone %d: %s to %s (%s below)\nStrength %d/100\nEvidence: %s\nHeld %d, broke %d\nReached within 12 months: %s" % (
            k, ik.money(z["lo"]), ik.money(z["hi"]), ik.pct(z["drop"], 0), z["score"], evidence(z),
            r["held"], r["broke"], ik.pct(z.get("odds12"), 0))
        if z.get("growth") is not None:
            text += "\nPrice there assumes %s growth" % ik.pct(z["growth"])
        b.append('<rect class="zone" x="%d" y="%.1f" width="%d" height="%.1f" fill-opacity="%.2f"%s/>'
                 % (ml, y(z["hi"]), pw, max(3, y(z["lo"]) - y(z["hi"])), op, ik.tip(text)))
        labels.append(((y(z["hi"]) + y(z["lo"])) / 2 + 4, "Zone %d  %s-%s%s" % (
            k, ik.money(z["lo"], 0), ik.money(z["hi"], 0), "  double floor" if z.get("double") else ""), "lbl"))
    for z in a["ceilings"]:
        b.append('<rect class="ceil" x="%d" y="%.1f" width="%d" height="%.1f"%s/>' % (
            ml, y(z["hi"]), pw, max(3, y(z["lo"]) - y(z["hi"])),
            ik.tip("Ceiling: %s to %s (+%s)\nStrength %d/100" % (ik.money(z["lo"]), ik.money(z["hi"]),
                                                              ik.pct(z["lo"] / a["price"] - 1, 0), z["score"]))))
        labels.append(((y(z["hi"]) + y(z["lo"])) / 2 + 4, "Ceiling %s" % ik.money(z["lo"], 0), "note"))
    if floor and lo < floor < hi:
        b.append('<line class="s4l" x1="%d" x2="%d" y1="%.1f" y2="%.1f"%s/>' % (
            ml, W - mr, y(floor), y(floor),
            ik.tip("Valuation floor %s\nThe price would only assume %s growth there" % (ik.money(floor), ik.pct(v["p75"])))))
        labels.append((y(floor) + 4, "Valuation floor %s" % ik.money(floor, 0), "lbl"))
    pts = " ".join("%.1f,%.1f" % (x(i), y(c)) for i, c in enumerate(closes))
    b.append('<polyline class="inkl" points="%s"/>' % pts)
    b.append('<circle class="s1 ring" cx="%.1f" cy="%.1f" r="5"%s/>' % (x(n - 1), y(a["price"]),
                                                                     ik.tip("Now %s (%s)" % (ik.money(a["price"]), a["asof"]))))
    labels.append((y(a["price"]) + 4, "Now %s" % ik.money(a["price"], 0), "lbl"))
    placed = []
    for yy, text, cls in sorted(labels):
        for q in placed:
            if abs(yy - q) < 15:
                yy = q + 15
        placed.append(yy)
        b.append('<text class="%s" x="%d" y="%.1f">%s</text>' % (cls, W - mr + 10, yy, ik.esc(text)))
    b.append('<text class="axis" x="%d" y="%d">%s</text>' % (ml, mt + ph + 24, dates[0]))
    b.append('<text class="axis" x="%d" y="%d" text-anchor="end">%s</text>' % (W - mr, mt + ph + 24, dates[-1]))
    return ik.svg_wrap(W, H, "\n".join(b), inline=inline, label="Weekly price with support zones and the valuation floor")


def report(ticker, a, args):
    price, v, ladder = a["price"], a["val"] or {}, a["ladder"]
    doc = a.get("doc") or {}
    name = doc.get("name") or ticker
    floor = v.get("floor_price")
    doubles = [z for z in ladder if z.get("double")]
    if ladder:
        strongest = max(ladder, key=lambda z: z["score"])
        thesis = "The chart's strongest support is <b>%s to %s</b>, %s below today (strength %d/100)." % (
            ik.esc(ik.money(strongest["lo"])), ik.esc(ik.money(strongest["hi"])), ik.pct(strongest["drop"], 0),
            strongest["score"])
    else:
        thesis = "No support zone scored %d or more within %s below today." % (args.min_score, ik.pct(args.max_drop, 0))
    if floor:
        if doubles:
            thesis += " At %s the chart and the valuation agree: a double floor." % " and ".join(
                ik.esc("%s-%s" % (ik.money(z["lo"], 0), ik.money(z["hi"], 0))) for z in doubles)
        else:
            thesis += (" On valuation, though, none of these prices is cheap: the price would have to fall to %s before "
                       "it only assumes what a top-quarter company its size achieves." % ik.esc(ik.money(floor)))
    kpis = [{"k": "Price", "v": ik.money(price), "s": a["asof"]}]
    if ladder:
        z1 = ladder[0]
        kpis.append({"k": "Nearest buy zone", "v": "%s-%s" % (ik.money(z1["lo"], 0), ik.money(z1["hi"], 0)),
                     "s": "%s below, strength %d" % (ik.pct(z1["drop"], 0), z1["score"]), "hero": True})
        zl = ladder[-1]
        kpis.append({"k": "Odds of reaching the deepest zone", "v": ik.pct(zl.get("odds12"), 0),
                     "s": "within 12 months (%s below)" % ik.pct(zl["drop"], 0)})
    if floor:
        kpis.append({"k": "Valuation floor", "v": ik.money(floor, 0), "s": "%s below today" % ik.pct(1 - floor / price, 0),
                     "tone": "down" if not doubles else None})
    body = [ik.h_kpis(kpis)]
    legend = [("--ink", "Weekly close", "line"), ("--s3", "Support zone", "box"), ("--s8", "Ceiling", "box")]
    if floor:
        legend.append(("--s4", "Valuation floor", "dash"))
    body.append(ik.h_section("Where buyers have stepped in", ik.h_figure(
        chart(ticker, a, inline=True), "Three years of weekly closes. Stronger zones are more solid. Hover a zone for its "
                                        "evidence and record.", legend)))
    rows = []
    for k, z in enumerate(ladder, 1):
        r = z["record"]
        rows.append([ik.Raw("<b>Zone %d</b><br><span class=\"tag\">%s</span>" % (k, ik.esc(evidence(z)))),
                     "%s-%s" % (ik.money(z["lo"]), ik.money(z["hi"])), ik.pct(z["drop"], 0), str(z["score"]),
                     "%d / %d" % (r["held"], r["broke"]) + (" (+%d open)" % r["open"] if r["open"] else ""),
                     "%s / %s" % (ik.pct(z.get("odds6"), 0), ik.pct(z.get("odds12"), 0)), ik.pct(z.get("hist12"), 0),
                     (ik.pct(z["growth"]) + (" (double floor)" if z.get("double") else "")) if z.get("growth") is not None else "run Priced-In"])
    if rows:
        body.append(ik.h_section("The buy-zone ladder", ik.h_table(
            ["Zone", "Range", "Below", "Strength", "Held / broke", "Reached in 6 / 12 months", "Happened before",
             "Growth priced in there"], rows),
            lede="Odds come from 10,000 paths built from its own last 2 years of daily moves. 'Happened before' is how "
                 "often a fall that deep within a year really happened in the last 5 years. The two should roughly agree."))
    if v:
        val = [("Today's price assumes", ik.pct(v.get("now_growth")) + " growth" if v.get("now_growth") is not None else "n/a",
                "a year, for 10 years")]
        if floor:
            val.append(("Valuation floor", ik.money(floor), "assumes %s, a top-quarter company's growth" % ik.pct(v["p75"])))
        if v.get("typical_price"):
            val.append(("Priced like a typical company", ik.money(v["typical_price"]), "assumes %s growth" % ik.pct(v["p50"])))
        ma = [("200-day average", ik.money(a["ma200d"]) if a["ma200d"] else "n/a",
               ik.pct(a["ma200d"] / price - 1, 0, True) if a["ma200d"] else None),
              ("200-week average", ik.money(a["ma200w"]) if a["ma200w"] else "n/a",
               ik.pct(a["ma200w"] / price - 1, 0, True) if a["ma200w"] else None)]
        ma += [("Ceiling %d" % i, "%s-%s" % (ik.money(z["lo"], 0), ik.money(z["hi"], 0)), "strength %d" % z["score"])
               for i, z in enumerate(a["ceilings"], 1)]
        body.append(ik.h_section("Valuation and the averages", ik.h_kv([val, ma])))
    else:
        body.append(ik.h_callout("Add the valuation half", ["Run /priced-in %s first to see the growth priced in at each "
                                                            "zone and the valuation floor." % ticker]))
    if ladder:
        zl = ladder[-1]
        body.append(ik.h_callout("The patience trade-off", [
            "The deepest zone is %s cheaper, but price reaches it within a year in only %s of simulated futures. "
            "Waiting for a better price has a cost too: the chance you never get to buy." % (
                ik.pct(zl["drop"], 0), ik.pct(zl.get("odds12"), 0))]))
    body.append(ik.h_details("How this was worked out", ik.h_paras(
        "Levels come from 5 years of weekly bars and 2 years of daily bars: swing lows (the lowest of 4 weeks either "
        "side), swing highs the price later held above for 4 weeks, price bands where the most shares traded, and the "
        "200-day and 200-week averages. Recent turning points count more.",
        "Levels within half the stock's typical weekly range (%s here) merge into one zone. Strength: turning points "
        "up to 40, volume up to 25, each average 10, an old ceiling turned floor 15, and up to 10 either way for its "
        "record." % ik.pct(a["tol"]),
        "A test counts as held when price closes 5% above the zone within 60 trading days, and as broken at a close "
        "2% below it. Zones are fitted to the same history their record is measured on, which flatters them a little.",
        "Prices are split-adjusted, not dividend-adjusted, because levels are traded prices.")))
    links = [("Prices: Yahoo Finance %s" % ticker, "https://finance.yahoo.com/quote/%s" % ik.yahoo_symbol(ticker))]
    if doc:
        links.append(("Valuation from the Priced-In report of %s" % doc.get("updated", ""), None))
    body.append(ik.h_section("Sources", ik.h_sources(links)))
    return ik.report_page("%s Buy Zones" % ticker, ["Buy Zones", ticker, a["asof"]], name, thesis, "\n".join(body),
                          "Support zones for %s with their records, the odds of reaching them and the valuation there."
                          % ticker)


def main(argv=None):
    ik.setup_console()
    ap = argparse.ArgumentParser(description="Buy Zone Finder: support zones, their records, odds and valuation.")
    ap.add_argument("ticker")
    ap.add_argument("--zones", type=int, default=3, help="how many buy zones to show (default 3)")
    ap.add_argument("--max-drop", type=float, default=0.45, help="ignore zones more than this far below (default 0.45)")
    ap.add_argument("--min-score", type=int, default=25, help="ignore zones weaker than this (default 25)")
    ap.add_argument("--paths", type=int, default=SIM_PATHS)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    ticker = args.ticker.upper()
    try:
        a = analyse(ticker, args)
    except ik.InvestError as e:
        print("buyzones: %s" % e, file=sys.stderr)
        return 2
    print(render(ticker, a, args))
    if not args.no_write:
        def zrow(z):
            row = {k: z.get(k) for k in ("lo", "hi", "center", "score", "parts", "drop", "odds6", "odds12",
                                         "hist12", "growth", "peers", "double")}
            row["evidence"] = evidence(z)
            row["record"] = {k: z["record"][k] for k in ("held", "broke", "open")} if z.get("record") else None
            return row
        payload = {"price": a["price"], "asof": a["asof"], "tolerance": a["tol"], "ma200d": a["ma200d"],
                   "ma200w": a["ma200w"], "zones": [zrow(z) for z in a["ladder"]],
                   "ceilings": [{"lo": z["lo"], "hi": z["hi"], "score": z["score"]} for z in a["ceilings"]],
                   "valuation": a["val"]}
        path = ik.save_section(ticker, "buy_zones", payload)
        svg = ik.write_text(os.path.join(ik.data_dir(), "charts", "%s-buy-zones.svg" % ticker), chart(ticker, a))
        rep = ik.write_report("%s-buy-zones" % ticker, report(ticker, a, args))
        print("Saved %s, %s.\nReport: %s" % (path, svg, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
