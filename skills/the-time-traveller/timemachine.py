#!/usr/bin/env python3
"""Time Machine: 10,000 possible next decades for one stock, against the S&P 500.

A Monte Carlo simulation with two honest choices built in:

  1. The SHAPE comes from history. Each simulated month is the stock's real
     monthly return paired with the S&P 500's return in that same month, drawn
     in blocks that average 12 months (a stationary block bootstrap). Crashes
     stay clustered, recoveries take as long as they really took, and the
     stock keeps its real relationship with the market.

  2. The EXPECTED RETURN does not come from history. A plain bootstrap of a
     past winner promises its past forever. Instead each scenario sets the
     average yearly return: by default, the returns Priced-In worked out for
     "grows like a typical company its size", "top quarter", "as priced in"
     and "keeps its own pace". The market's own expected return is a stated
     assumption (default 9%, the same discount rate Priced-In uses).

Beside the simulation it prints what really happened: every rolling window
of the same length in the stock's own history.

  python timemachine.py NVDA                       # $10,000 today, 10 years
  python timemachine.py KO --monthly 500 --years 20
  python timemachine.py TSLA --return 0.12          # your own expected return
  python timemachine.py AAPL --scenario history     # raw history, hindsight and all
"""

import argparse
import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import investkit as ik  # noqa: E402

DEFAULTS = {"years": 10, "amount": 10000.0, "paths": 10000, "block": 12, "seed": 2026,
            "benchmark": "SPY", "market_return": 0.09}

MIN_MONTHS = 36
PATHS_DRAWN = 250       # lines on the chart, spread evenly over the range of outcomes
HISTORY_MONTHS = 36    # real months shown before today


# ---------------------------------------------------------------- data

def monthly_total_returns(ticker):
    """{YYYY-MM: month-end adjusted close} from Yahoo monthly bars. The
    unfinished current month is dropped."""
    ch = ik.yahoo_chart(ticker, "max", "1mo", ttl_hours=12)
    this_month = ik.today().strftime("%Y-%m")
    out = {}
    for d, a in zip(ch["dates"], ch["adjclose"]):
        if a and a > 0:
            out[d[:7]] = a  # dates are ascending, so the last day of each month wins
    out.pop(this_month, None)
    return out, ch["meta"]


def paired_returns(ticker, bench):
    s, meta = monthly_total_returns(ticker)
    b, _ = monthly_total_returns(bench)
    months = sorted(set(s) & set(b))
    rs, rb, used = [], [], []
    for i in range(1, len(months)):
        m0, m1 = months[i - 1], months[i]
        # only consecutive calendar months
        y0, mo0 = int(m0[:4]), int(m0[5:])
        y1, mo1 = int(m1[:4]), int(m1[5:])
        if (y1 * 12 + mo1) - (y0 * 12 + mo0) != 1:
            continue
        rs.append(math.log(s[m1] / s[m0]))
        rb.append(math.log(b[m1] / b[m0]))
        used.append(m1)
    return rs, rb, used, meta


# ---------------------------------------------------------------- scenarios

def load_scenarios(ticker, args, rs):
    """[(key, label, annual_return or None for raw history)] in the order to show."""
    hist_ret = math.exp(ik.mean(rs) * 12) - 1
    if args.ret is not None:
        return [("custom", "Your expected return", args.ret)], None
    if args.scenario == "history":
        return [("history", "Raw history (hindsight)", None)], None
    doc = ik.load_ticker(ticker).get("priced_in")
    rows = []
    if doc and doc.get("scenarios"):
        for s in doc["scenarios"]:
            if s.get("annual_return") is None:
                continue
            if args.scenario not in ("all", s["key"]):
                continue
            rows.append((s["key"], "%s (%s growth)" % (s["label"], ik.pct(s["growth"])), s["annual_return"]))
        rows.sort(key=lambda r: r[2])
        return rows, doc
    # no Priced-In run: compare the market's return with the stock's own past, clearly labelled
    rows.append(("market", "Earns the market's expected return", args.market_return))
    rows.append(("own_past", "Repeats its own past average (hindsight)", hist_ret))
    return rows, None


# ---------------------------------------------------------------- simulation

def vol_scale(xs, recent_months):
    """Filtered historical simulation: keep each month's real shape (its rank,
    its clustering, its pairing with the market) but resize the swings so the
    series has the volatility of its most recent `recent_months`."""
    if not recent_months or len(xs) < 2 * recent_months or not ik.stdev(xs):
        return list(xs), 1.0
    mu = ik.mean(xs)
    k = ik.stdev(xs[-recent_months:]) / ik.stdev(xs)
    return [mu + k * (x - mu) for x in xs], k


def simulate(rs, rb, scen, args):
    """Run every scenario on the same 10,000 bootstrap index paths (common random
    numbers), so differences between scenarios are not noise."""
    months = args.years * 12
    n = len(rs)
    rng = random.Random(args.seed)
    paths = [ik.stationary_bootstrap(n, months, args.block, rng) for _ in range(args.paths)]
    raw_s, raw_b = rs, rb
    if scen[0][2] is not None:  # raw-history mode stays raw
        rs, _ = vol_scale(rs, args.vol_months)
        rb, _ = vol_scale(rb, args.vol_months)
    mu_s, mu_b = ik.mean(rs), ik.mean(rb)
    bench_shift = (math.log(1 + args.market_return) / 12 - mu_b) if args.market_return is not None else 0.0
    contrib = [args.amount] + [args.monthly] * (months - 1) if args.monthly else [args.amount] + [0.0] * (months - 1)
    invested = sum(contrib)
    keys = [s[0] for s in scen]
    fan_key = "priced_in" if "priced_in" in keys else keys[-1]
    results = []
    for key, label, ret in scen:
        shift = (math.log(1 + ret) / 12 - mu_s) if ret is not None else 0.0
        bshift = bench_shift if ret is not None else 0.0
        ends, bends, dds, under3, fan, fanb = [], [], [], 0, [], []
        keep_fan = key == fan_key
        for idx in paths:
            v = vb = 0.0
            put = 0.0
            peak = 0.0
            worst = 0.0
            row = [] if keep_fan else None
            rowb = [] if keep_fan else None
            for t, i in enumerate(idx):
                c = contrib[t]
                put += c
                v = (v + c) * math.exp(rs[i] + shift)
                vb = (vb + c) * math.exp(rb[i] + bshift)
                # drawdown on the growth of a dollar, not on new deposits
                if v > peak:
                    peak = v
                elif peak > 0:
                    dd = v / peak - 1
                    if dd < worst:
                        worst = dd
                if t == 35 and v < put:
                    under3 += 1
                if keep_fan:
                    row.append(v)
                    rowb.append(vb)
            ends.append(v)
            bends.append(vb)
            dds.append(worst)
            if keep_fan:
                fan.append(row)
                fanb.append(rowb)
        if args.monthly:
            # deposits lift the peak too; measure drawdowns on a lump-sum path instead
            dds = lump_drawdowns(rs, paths, shift)
        s_ends = sorted(ends)
        s_bends = sorted(bends)
        beat = sum(1 for a, b in zip(ends, bends) if a > b) / float(len(ends))
        res = {
            "key": key, "label": label, "annual_return": ret, "invested": invested,
            "end": {p: ik.percentile(s_ends, p) for p in (5, 25, 50, 75, 95)},
            "bench_end": {p: ik.percentile(s_bends, p) for p in (5, 25, 50, 75, 95)},
            "p_loss": sum(1 for x in ends if x < invested) / float(len(ends)),
            "p_half": sum(1 for x in ends if x < invested * 0.5) / float(len(ends)),
            "p_bench_loss": sum(1 for x in bends if x < invested) / float(len(bends)),
            "p_beat": beat,
            "dd_median": ik.percentile(sorted(dds), 50), "dd_bad": ik.percentile(sorted(dds), 10),
            "p_under_3y": under3 / float(len(ends)) if months > 36 else None,
        }
        if not args.monthly:
            res["cagr"] = {p: (res["end"][p] / invested) ** (1.0 / args.years) - 1 for p in res["end"]}
            res["bench_cagr"] = {p: (res["bench_end"][p] / invested) ** (1.0 / args.years) - 1 for p in res["bench_end"]}
        if fan:
            res["fan"] = [[ik.percentile(sorted(r[t] for r in fan), p) for p in (5, 25, 50, 75, 95)]
                          for t in range(months)]
            res["fan_invested"] = [sum(contrib[:t + 1]) for t in range(months)]
            res["fan_bench"] = [ik.percentile(sorted(r[t] for r in fanb), 50) for t in range(months)]
            order = sorted(range(len(fan)), key=lambda i: ends[i])
            n = min(PATHS_DRAWN, len(order))
            res["paths_sample"] = [fan[order[int((j + 0.5) / n * len(order))]] for j in range(n)]
            # the real past, in the same money: what the amount was worth over the last 3 years, ending today
            past = [contrib[0]]
            for r in reversed(raw_s[-HISTORY_MONTHS:]):
                past.append(past[-1] / math.exp(r))
            res["history_path"] = list(reversed(past))
        results.append(res)
    del raw_s, raw_b
    return results


def lump_drawdowns(rs, paths, shift):
    out = []
    for idx in paths:
        v = peak = 1.0
        worst = 0.0
        for i in idx:
            v *= math.exp(rs[i] + shift)
            if v > peak:
                peak = v
            else:
                worst = min(worst, v / peak - 1)
        out.append(worst)
    return out


# ---------------------------------------------------------------- real history

def history_windows(rs, rb, used, years):
    """Every real rolling window of `years` in the stock's own record (lump sum)."""
    m = years * 12
    if len(rs) < m:
        return None
    rows = []
    for start in range(0, len(rs) - m + 1):
        s = sum(rs[start:start + m])
        b = sum(rb[start:start + m])
        rows.append((used[start], math.exp(s / years) - 1, math.exp(b / years) - 1))
    cagrs = sorted(r[1] for r in rows)
    worst = min(rows, key=lambda r: r[1])
    best = max(rows, key=lambda r: r[1])
    return {"n": len(rows), "first": rows[0][0], "last": rows[-1][0],
            "p_positive": sum(1 for r in rows if r[1] > 0) / float(len(rows)),
            "p_beat": sum(1 for r in rows if r[1] > r[2]) / float(len(rows)),
            "median": ik.percentile(cagrs, 50), "worst": worst, "best": best}


def history_stats(rs, rb):
    ann = lambda xs: math.exp(ik.mean(xs) * 12) - 1  # noqa: E731
    vol = lambda xs: ik.stdev(xs) * math.sqrt(12)  # noqa: E731
    cov = sum((a - ik.mean(rs)) * (b - ik.mean(rb)) for a, b in zip(rs, rb)) / (len(rs) - 1)
    beta = cov / (ik.stdev(rb) ** 2)
    corr = cov / (ik.stdev(rs) * ik.stdev(rb))
    # worst real drawdown of the stock
    v = peak = 1.0
    worst = 0.0
    for r in rs:
        v *= math.exp(r)
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return {"ret": ann(rs), "vol": vol(rs), "bench_ret": ann(rb), "bench_vol": vol(rb),
            "vol_recent": vol(rs[-60:]), "bench_vol_recent": vol(rb[-60:]),
            "beta": beta, "corr": corr, "worst_dd": worst}


# ---------------------------------------------------------------- output

def render(ticker, args, used, hs, hw, results, doc):
    L = []
    plan = ("%s today" % ik.money(args.amount, 0)) if not args.monthly else (
        "%s today, then %s a month" % (ik.money(args.amount, 0), ik.money(args.monthly, 0)))
    L.append("TIME MACHINE: %s, %s, %d years, %s simulated futures vs %s"
             % (ticker, plan, args.years, "{:,}".format(args.paths), args.benchmark))
    L.append("Shape from %d real months (%s to %s), drawn in blocks averaging %d months, each month paired with %s's."
             % (len(used), used[0], used[-1], args.block, args.benchmark))
    if len(used) < 120:
        L.append("Only %d months of history: the shape rests on a short record, so treat the spread as optimistic."
                 % len(used))
    L.append("Its real record: %s a year, volatility %s, beta %.2f to %s, worst fall %s. %s: %s a year, volatility %s."
             % (ik.pct(hs["ret"]), ik.pct(hs["vol"], 0), hs["beta"], args.benchmark, ik.pct(hs["worst_dd"], 0),
                args.benchmark, ik.pct(hs["bench_ret"]), ik.pct(hs["bench_vol"], 0)))
    if results[0]["annual_return"] is not None and args.vol_months and hs.get("vol_recent"):
        if len(used) >= 2 * args.vol_months:
            L.append("Swings resized to the last %d years' volatility: %s for %s (full record %s), %s for %s. "
                     "Use --vol full for the whole record."
                     % (args.vol_months // 12, ik.pct(hs["vol_recent"], 0), ticker, ik.pct(hs["vol"], 0),
                        ik.pct(hs["bench_vol_recent"], 0), args.benchmark))
        else:
            L.append("History is short, so the whole record's volatility is used as is.")
    if results[0]["annual_return"] is not None:
        src = "The Mind Reader's scenarios" if doc else "the returns below"
        L.append("Expected returns come from %s, not from the past. %s is set to %s a year."
                 % (src, args.benchmark, ik.pct(args.market_return)))
    L.append("")
    invested = results[0]["invested"]
    for r in results:
        L.append(r["label"] + (": %s a year expected" % ik.pct(r["annual_return"]) if r["annual_return"] is not None else ""))
        e = r["end"]
        L.append("  You put in %s. After %d years:  bad (5%%) %s   typical %s   great (95%%) %s"
                 % (ik.money(invested, 0), args.years, ik.money(e[5], 0), ik.money(e[50], 0), ik.money(e[95], 0)))
        if "cagr" in r:
            L.append("  A year:                         %s          %s           %s"
                     % (ik.pct(r["cagr"][5]), ik.pct(r["cagr"][50]), ik.pct(r["cagr"][95])))
        L.append("  Lose money %s   lose half %s   beat %s %s   (%s alone: typical %s, loses money %s)"
                 % (ik.pct(r["p_loss"], 0), ik.pct(r["p_half"], 0), args.benchmark, ik.pct(r["p_beat"], 0),
                    args.benchmark, ik.money(r["bench_end"][50], 0), ik.pct(r["p_bench_loss"], 0)))
        L.append("  The ride: typical worst fall along the way %s; 1 in 10 futures sees %s or worse%s"
                 % (ik.pct(r["dd_median"], 0), ik.pct(r["dd_bad"], 0),
                    ("; underwater after 3 years %s" % ik.pct(r["p_under_3y"], 0)) if r["p_under_3y"] is not None else ""))
        L.append("")
    L.append("What really happened: every %d-year window in its own history (lump sum, dividends in)" % args.years)
    if hw:
        L.append("  %d windows starting %s to %s (they overlap, so they are not %d independent tries)"
                 % (hw["n"], hw["first"], hw["last"], hw["n"]))
        L.append("  Made money %s   beat %s %s   median %s a year   worst %s (from %s)   best %s (from %s)"
                 % (ik.pct(hw["p_positive"], 0), args.benchmark, ik.pct(hw["p_beat"], 0), ik.pct(hw["median"]),
                    ik.pct(hw["worst"][1]), hw["worst"][0], ik.pct(hw["best"][1]), hw["best"][0]))
        L.append("  History only contains the path that happened. A stock that is still listed is a survivor.")
    else:
        L.append("  Not enough history for a single %d-year window." % args.years)
    L.append("")
    L.append("Read it as a range of futures, not a forecast. The spread comes from how this stock has moved;")
    L.append("the center comes from the scenario you believe. Seed %d, so the same command gives the same numbers." % args.seed)
    L.append(ik.DISCLAIMER)
    return "\n".join(L)


def fan_chart(ticker, args, res, inline=False):
    """One thin line per simulated future (a sample spread evenly over the outcomes), leading out of
    the stock's real last 3 years, with the typical future, the S&P's typical path and the money put in."""
    if "fan" not in res:
        return None
    W, H = 960, (480 if inline else 540)
    ml, mr, mb = 72, 150, 46
    mt = 24 if inline else 92
    fan, inv, bench = res["fan"], res["fan_invested"], res.get("fan_bench")
    paths, past = res.get("paths_sample") or [], res.get("history_path") or []
    months = len(fan)
    start = inv[0]
    back = len(past) - 1  # months of real history before today
    # value at step k: k = -back..-1 is the past, 0 is today, 1..months the future
    ends = sorted(p[-1] for p in paths) or [fan[-1][0], fan[-1][4]]
    lo_vals = [ends[int(len(ends) * 0.02)], min(r[0] for r in fan), start] + past
    hi_vals = [ends[min(len(ends) - 1, int(len(ends) * 0.98))], max(r[4] for r in fan)] + past
    lo_v, top = math.log(max(1.0, min(lo_vals) * 0.85)), math.log(max(hi_vals) * 1.15)
    pw, ph = W - ml - mr, H - mt - mb

    def x(k):
        return ml + (k + back) / float(months + back) * pw

    def y(v):
        return mt + ph - (math.log(max(v, 1e-9)) - lo_v) / float(top - lo_v) * ph

    def log_ticks():
        out = []
        e = int(math.floor(lo_v / math.log(10)))
        while 10 ** e <= math.exp(top):
            for m in (1, 2, 5):
                v = m * 10 ** e
                if math.exp(lo_v) <= v <= math.exp(top):
                    out.append(v)
            e += 1
        return out

    def future(vals, cls):
        pts = [(x(0), y(start))] + [(x(t + 1), y(v)) for t, v in enumerate(vals)]
        return '<polyline class="%s" points="%s"/>' % (cls, " ".join("%.1f,%.1f" % p for p in pts))

    clip = "tm-%s-%s" % (re.sub(r"[^A-Za-z0-9]", "", ticker), "in" if inline else "svg")
    b = []
    if not inline:
        b.append('<text class="title" x="%d" y="40">%s: %s possible next %d years</text>'
                 % (ml, ik.esc(ticker), "{:,}".format(args.paths), args.years))
        b.append('<text class="sub" x="%d" y="66">%s. %d of the futures drawn, spread evenly from worst to best.</text>'
                 % (ml, ik.esc(res["label"]), len(paths)))
    b.append('<defs><clipPath id="%s"><rect x="%d" y="%d" width="%d" height="%d"/></clipPath></defs>' % (clip, ml, mt, pw, ph))
    for tv in log_ticks():
        b.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, W - mr, y(tv), y(tv)))
        b.append('<text class="axis" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ml - 8, y(tv) + 4, ik.money(tv, 0)))
    if back:
        b.append('<text class="axis" x="%.1f" y="%d" text-anchor="middle">%d years ago</text>' % (x(-back), mt + ph + 22, back // 12))
    for yr in range(0, args.years + 1, max(1, args.years // 5)):
        b.append('<text class="axis" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                 % (x(min(yr * 12, months)), mt + ph + 22, "Today" if yr == 0 else "Year %d" % yr))
    b.append('<g clip-path="url(#%s)">' % clip)
    for p in paths:
        b.append(future(p, "spag"))
    b.append(future(inv[1:] + [inv[-1]], "refl"))
    if bench:
        b.append(future(bench, "mutl"))
    b.append(future([r[2] for r in fan], "typl"))
    b.append('</g>')
    b.append('<line class="todayl" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/>' % (x(0), x(0), mt, mt + ph))
    if back:
        b.append('<polyline class="inkl" points="%s"/>' % " ".join(
            "%.1f,%.1f" % (x(k - back), y(v)) for k, v in enumerate(past)))
        b.append('<text class="note" x="%.1f" y="%d">the last %d years</text>' % (x(-back) + 4, mt + 14, back // 12))
    b.append('<circle class="s1 ring" cx="%.1f" cy="%.1f" r="5"/>' % (x(0), y(start)))
    end = fan[-1]
    placed = []
    for p, lbl in ((4, "Great (95th)"), (2, "Typical"), (0, "Bad (5th)")):
        yy = y(end[p]) + 4
        for q in placed:
            if abs(yy - q) < 16:
                yy = q + 16
        placed.append(yy)
        b.append('<text class="lbl" x="%d" y="%.1f">%s %s</text>' % (W - mr + 10, yy, lbl, ik.money(end[p], 0)))
    if bench:
        yb = y(bench[-1]) + 4
        if any(abs(yb - q) < 16 for q in placed):
            yb = placed[1] + 18
        b.append('<text class="note" x="%d" y="%.1f">%s %s</text>' % (W - mr + 10, yb, ik.esc(args.benchmark),
                                                                      ik.money(bench[-1], 0)))
    # hover columns (report only): one per year, with that year's spread across all the futures
    for t in (range(0, months, 12) if inline else ()):
        t2 = min(t + 12, months) - 1
        r = fan[t2]
        text = "Year %d\nGreat (95th): %s\nTypical: %s\nBad (5th): %s\nPut in: %s%s" % (
            (t2 + 1) // 12, ik.money(r[4], 0), ik.money(r[2], 0), ik.money(r[0], 0), ik.money(inv[t2], 0),
            ("\n%s typical: %s" % (args.benchmark, ik.money(bench[t2], 0))) if bench else "")
        b.append('<rect class="hit" x="%.1f" y="%d" width="%.1f" height="%d"%s/>'
                 % (x(t), mt, x(t2 + 1) - x(t), ph, ik.tip(text)))
    if not inline:
        b.append('<text class="note" x="%d" y="%d">Each thin line is one simulated future (log scale). White: the typical one. '
                 'Grey: %s. Dashed: money put in. Not a forecast.</text>' % (ml, H - 10, ik.esc(args.benchmark)))
    return ik.svg_wrap(W, H, "\n".join(b), inline=inline,
                       label="%d simulated futures over %d years, one line each, after the last %d years of real prices"
                             % (len(paths), args.years, back // 12))


def report(ticker, args, used, hs, hw, results, doc):
    primary = next((r for r in results if "fan" in r), results[-1])
    plan = ("%s today" % ik.money(args.amount, 0)) if not args.monthly else (
        "%s today plus %s a month" % (ik.money(args.amount, 0), ik.money(args.monthly, 0)))
    e, inv = primary["end"], primary["invested"]
    label = primary["label"]
    thesis = ("Scenario: %s. %s typically becomes <b>%s</b> after %d years, against %s for the S&amp;P 500 alone. "
              "The typical worst fall along the way is %s." % (
                  ik.esc(label[0].lower() + label[1:]), ik.esc(plan[0].upper() + plan[1:]),
                  ik.esc(ik.money(e[50], 0)), args.years, ik.esc(ik.money(primary["bench_end"][50], 0)),
                  ik.pct(primary["dd_median"], 0)))
    kpis = [{"k": "Typical outcome", "v": ik.money(e[50], 0), "s": "median of %s futures; you put in %s" % (
                "{:,}".format(args.paths), ik.money(inv, 0)), "hero": True},
            {"k": "Range", "v": "%s to %s" % (ik.money(e[5], 0), ik.money(e[95], 0)), "s": "bad (5th) to great (95th)"},
            {"k": "Beats the S&P 500", "v": ik.pct(primary["p_beat"], 0), "s": "of futures"},
            {"k": "Loses money", "v": ik.pct(primary["p_loss"], 0), "s": "S&P alone: %s" % ik.pct(primary["p_bench_loss"], 0)},
            {"k": "Typical worst fall", "v": ik.pct(primary["dd_median"], 0),
             "s": "1 in 10 futures: %s or worse" % ik.pct(primary["dd_bad"], 0), "tone": "down"}]
    body = [ik.h_kpis(kpis)]
    legend = [("--s1", "One simulated future", "line"), ("--ink", "Typical future", "line"),
              ("--ink", "The last 3 years", "line"), ("--muted", "S&P 500 typical", "line"),
              ("--muted", "Money put in", "dash")]
    body.append(ik.h_section("%s possible next %d years" % ("{:,}".format(args.paths), args.years), ik.h_figure(
        fan_chart(ticker, args, primary, inline=True),
        "Scenario shown: %s. %d of the %s futures are drawn, one line each, picked evenly from worst to best so "
        "the bundle shows the true spread. Log scale, so equal steps are equal percentage moves. Hover a year for "
        "its spread across all futures." % (ik.esc(label), len(primary.get("paths_sample") or []),
                                           "{:,}".format(args.paths)), legend)))
    rows, mark = [], None
    for i, r in enumerate(results):
        rows.append([r["label"], ik.pct(r["annual_return"]) if r["annual_return"] is not None else "history",
                     ik.money(r["end"][5], 0), ik.money(r["end"][50], 0), ik.money(r["end"][95], 0),
                     ik.pct(r["p_loss"], 0), ik.pct(r["p_beat"], 0), ik.pct(r["dd_median"], 0)])
        if r is primary:
            mark = i
    body.append(ik.h_section("Every scenario, side by side", ik.h_table(
        ["Scenario", "Expected a year", "Bad (5th)", "Typical", "Great (95th)", "Loses money", "Beats S&P", "Worst fall"],
        rows, mark), lede="Each row runs on the same %s random paths, so the differences come from the scenario, not "
                         "luck. You put in %s." % ("{:,}".format(args.paths), ik.money(inv, 0))))
    if hw:
        hist = ik.h_kv([[("Windows", "{:,}".format(hw["n"]), "starting %s to %s, overlapping" % (hw["first"], hw["last"])),
                         ("Made money", ik.pct(hw["p_positive"], 0), None),
                         ("Beat %s" % args.benchmark, ik.pct(hw["p_beat"], 0), None)],
                        [("Median", ik.pct(hw["median"]) + " a year", None),
                         ("Worst", ik.pct(hw["worst"][1]) + " a year", "starting %s" % hw["worst"][0]),
                         ("Best", ik.pct(hw["best"][1]) + " a year", "starting %s" % hw["best"][0])]])
        body.append(ik.h_section("What really happened", hist,
                                 lede="Every real %d-year window in %s's own history, lump sum, dividends included. "
                                      "History holds only the one path that happened, from a company that survived."
                                      % (args.years, ik.esc(ticker))))
    paras = [
        "Each simulated month is a real month of %s's total return, paired with %s's return in the same month, drawn "
        "in blocks averaging %d months (a stationary block bootstrap), so crashes stay clustered and the stock keeps "
        "its real link to the market. History used: %d months, %s to %s." % (
            ticker, args.benchmark, args.block, len(used), used[0], used[-1])]
    if args.vol_months and primary["annual_return"] is not None:
        paras.append("Swings are resized to the last 5 years' volatility (%s for %s, against %s over the full record), "
                     "so a company's wild early years do not set today's risk." % (
                         ik.pct(hs["vol_recent"], 0), ticker, ik.pct(hs["vol"], 0)))
    if primary["annual_return"] is not None:
        paras.append("The center does not come from the past. Each scenario sets the average yearly growth; the S&P 500 "
                     "is set to %s a year. A plain replay of a past winner would promise its past forever."
                     % ik.pct(args.market_return))
    paras.append("Seed %d: the same settings give the same numbers." % args.seed)
    body.append(ik.h_details("How this was worked out", ik.h_paras(*paras)))
    links = [("Prices and dividends: Yahoo Finance %s" % ticker, "https://finance.yahoo.com/quote/%s" % ik.yahoo_symbol(ticker)),
             ("Benchmark: Yahoo Finance %s" % args.benchmark, "https://finance.yahoo.com/quote/%s" % args.benchmark)]
    if doc:
        links.append(("Scenarios from The Mind Reader's report of %s" % doc.get("updated", ""), None))
    body.append(ik.h_section("Sources", ik.h_sources(links)))
    name = (doc or {}).get("name") or ticker
    return ik.report_page("%s Time Machine" % ticker, ["The Time Traveller · Time Machine", ticker, ik.today().isoformat()], name, thesis,
                          "\n".join(body), "%s simulated decades for %s against the S&P 500."
                          % ("{:,}".format(args.paths), ticker))


def main(argv=None):
    ik.setup_console()
    ap = argparse.ArgumentParser(description="Time Machine: Monte Carlo of the next decade for one stock.")
    ap.add_argument("ticker")
    ap.add_argument("--amount", type=float, default=None, help="invested today (default 10000)")
    ap.add_argument("--monthly", type=float, default=0.0, help="also invest this much every month")
    ap.add_argument("--years", type=int, default=None)
    ap.add_argument("--scenario", default="all",
                    help="all (default), typical, top_quartile, priced_in, track_record, or history (raw, hindsight)")
    ap.add_argument("--return", dest="ret", type=float, help="your own expected annual return, e.g. 0.10")
    ap.add_argument("--market-return", type=float, default=None, help="the benchmark's expected return (default 0.09)")
    ap.add_argument("--benchmark", default=None, help="default SPY (S&P 500 with dividends)")
    ap.add_argument("--paths", type=int, default=None)
    ap.add_argument("--block", type=float, default=None, help="mean block length in months (default 12)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--vol", default="recent",
                    help="recent (default: resize swings to the last 5 years' volatility) or full (the whole record)")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    args.vol_months = 60 if args.vol == "recent" else 0
    cfg = ik.load_config().get("time_machine", {})
    for k, d in DEFAULTS.items():
        if getattr(args, k, None) is None:
            setattr(args, k, cfg.get(k, d))
    ticker = args.ticker.upper()
    try:
        doc = ik.load_ticker(ticker).get("priced_in")
        if doc and args.market_return == DEFAULTS["market_return"] and "market_return" not in cfg:
            args.market_return = doc.get("discount", args.market_return)
        rs, rb, used, meta = paired_returns(ticker, args.benchmark)
        if len(rs) < MIN_MONTHS:
            raise ik.InvestError("%s has only %d months of prices; The Time Traveller needs at least %d."
                                 % (ticker, len(rs), MIN_MONTHS))
        scen, doc = load_scenarios(ticker, args, rs)
        if not scen:
            raise ik.InvestError("No scenario matched --scenario %s." % args.scenario)
        if scen[0][2] is None:
            args.market_return = None
        results = simulate(rs, rb, scen, args)
        hs = history_stats(rs, rb)
        hw = history_windows(rs, rb, used, args.years)
    except ik.InvestError as e:
        print("timemachine: %s" % e, file=sys.stderr)
        return 2
    if args.market_return is None:
        args.market_return = 0.0
    print(render(ticker, args, used, hs, hw, results, doc))
    if not args.no_write:
        svg = fan_chart(ticker, args, next(r for r in results if "fan" in r))
        payload = {"years": args.years, "amount": args.amount, "monthly": args.monthly, "paths": args.paths,
                   "seed": args.seed, "block": args.block, "benchmark": args.benchmark,
                   "history": {"months": len(used), "from": used[0], "to": used[-1], **hs, "windows": hw},
                   "scenarios": [{k: v for k, v in r.items() if k not in ("fan", "fan_invested", "fan_bench", "paths_sample",
                                                                         "history_path")} for r in results]}
        path = ik.save_section(ticker, "time_machine", payload)
        extra = ""
        if svg:
            extra = ", " + ik.write_text(os.path.join(ik.data_dir(), "charts", "%s-time-machine.svg" % ticker), svg)
        rep = ik.write_report("%s-time-machine" % ticker, report(ticker, args, used, hs, hw, results, doc))
        print("Saved %s%s.\nReport: %s" % (path, extra, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
