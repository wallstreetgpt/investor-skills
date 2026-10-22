import collections
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from _helpers import Offline, ik, load

sc = load("idea-scanner", "scanner")

YEAR, Q = 2025, (2026, 2)
REV = 1e9


def company(f, cik, g=0.10, gm=0.6, oi_m=0.25, fcf_m=0.2, fcf_prev_m=0.2, sh_growth=0.0,
            q_now=0.12, q_prev=0.10, goodwill_jump=0.0):
    """Write one synthetic company into the frames dict: steady growth g, 25% operating margin,
    equity at 80% of revenue and no debt or cash, so return on capital is 0.25 x 0.79 / 0.8 = 24.7%."""
    for k in range(4):
        f["rev%d" % (YEAR - k)][cik] = REV / (1 + g) ** k
    for yy in (YEAR - 2, YEAR - 1, YEAR):
        r = f["rev%d" % yy][cik]
        f["gp%d" % yy][cik] = r * gm
        f["oi%d" % yy][cik] = r * oi_m
        f["pre%d" % yy][cik] = r * oi_m
        f["tax%d" % yy][cik] = r * oi_m * 0.21
        f["eq%d" % yy][cik] = r * 0.8
    f["cfo"][cik] = REV * fcf_m
    f["cfo_prev"][cik] = f["rev%d" % (YEAR - 1)][cik] * fcf_prev_m
    f["capex"][cik] = f["capex_prev"][cik] = 0.0
    f["sh%d" % YEAR][cik] = 100.0
    f["sh%d" % (YEAR - 3)][cik] = 100.0 / (1 + sh_growth) ** 3
    q4 = REV / 4
    f["qrev4"][cik], f["qrev0"][cik], f["qrev8"][cik] = q4, q4 * (1 + q_now), q4 / (1 + q_prev)
    f["qgp0"][cik], f["qgp4"][cik] = q4 * (1 + q_now) * gm, q4 * gm
    f["gw%d" % (YEAR - 3)][cik], f["gw%d" % YEAR][cik] = 0.0, REV * goodwill_jump


def cap_for(implied, fcf_m=0.2):
    """The market cap at which the price assumes `implied` growth (no debt, no cash)."""
    return ik.dcf_value(REV * fcf_m, implied, 10, 0.09, 0.025)


class Universe(object):
    def __init__(self):
        self.f = collections.defaultdict(dict)
        self.listed, self.quotes = {}, {}

    def add(self, cik, ticker, cap, price=100.0, high=105.0, currency="USD", **kw):
        company(self.f, cik, **kw)
        self.listed[cik] = {"cik": cik, "name": ticker + " Corp", "ticker": ticker, "exchange": "NYSE"}
        self.quotes[ticker] = {"marketCap": cap, "regularMarketPrice": price, "fiftyTwoWeekHigh": high,
                               "twoHundredDayAverage": price, "quoteType": "EQUITY", "financialCurrency": currency,
                               "shortName": ticker + " Corp", "currency": "USD"}


def build():
    u = Universe()
    u.add(1, "QUAL", cap_for(0.12))                               # quality only
    u.add(2, "DILU", cap_for(0.12), sh_growth=0.05)               # issues 5% more shares a year
    u.add(3, "GAP", cap_for(0.05, 0.15), g=0.20, fcf_m=0.15, fcf_prev_m=0.15)
    u.add(4, "GAPACQ", cap_for(0.05, 0.15), g=0.20, fcf_m=0.15, fcf_prev_m=0.15, goodwill_jump=0.5)
    u.add(5, "FALL", cap_for(0.06), g=0.05, price=60.0, high=100.0)
    u.add(6, "ACCEL", cap_for(0.12), q_now=0.30, q_prev=0.10)
    u.add(7, "ACCNEG", cap_for(0.12), q_now=0.40, q_prev=-0.10)  # a rebound, not acceleration
    u.add(8, "CAT", cap_for(0.12), g=0.05)
    u.add(9, "BANK", cap_for(0.12))
    u.f["deposits"][9] = 5e9
    u.add(10, "FLAG", cap_for(0.12))
    u.add(11, "DEAL", cap_for(0.05, 0.15), g=0.20, fcf_m=0.15, fcf_prev_m=0.15)
    u.add(12, "FINSIC", cap_for(0.12))
    u.add(13, "SMALL", 5e8)
    u.add(14, "FX", cap_for(0.12), currency="EUR")
    return u


def args(**kw):
    a = dict(lens="all", min_cap=1e9, min_revenue=2e8, theme=None, days=14, top=20, discount=0.09,
             keep_flagged=False, no_news=False, no_headlines=False, include_otc=False, no_write=True)
    a.update(kw)
    return SimpleNamespace(**a)


class Lenses(unittest.TestCase):
    def run_scan(self, **kw):
        u = build()
        cat = {8: [{"kind": "guidance_up", "label": "Raised guidance", "strength": 3, "filed": "2026-10-20",
                    "url": "https://www.sec.gov/x", "phrase": "raises full-year guidance", "items": ["2.02"]}]}
        flag = {10: [{"kind": "restatement", "label": "Restatement warning", "hard": True, "filed": "2026-10-01",
                      "url": "https://www.sec.gov/y"}]}
        with Offline(), \
                mock.patch.object(sc, "listed_companies", lambda otc=False: u.listed), \
                mock.patch.object(sc, "load_frames", lambda y, q, progress=True: u.f), \
                mock.patch.object(sc.ik, "yahoo_quotes", lambda tickers: u.quotes), \
                mock.patch.object(sc, "scan_catalysts", lambda days, ciks: cat), \
                mock.patch.object(sc, "scan_red_flags", lambda days, ciks: flag), \
                mock.patch.object(sc, "company_sic", lambda cik: "6199" if cik == 12 else "3571"), \
                mock.patch.object(sc, "completed_deal", lambda cik: {"filed": "2026-05-01", "url": "u", "items": ["2.01"]}
                                  if cik == 11 else None), \
                mock.patch.object(sc.ik, "recent_8k_items", lambda cik, days: []), \
                mock.patch.object(sc.ik, "yahoo_headlines", lambda t, days=14: []):
            scan = sc.run_scan(args(**kw))
            page = sc.report(scan)
        return scan, page

    def test_every_lens(self):
        scan, page = self.run_scan()
        names = {k: [r["ticker"] for r in v] for k, v in scan["results"].items()}
        self.assertIn("QUAL", names["quality"])
        for bad in ("DILU", "FINSIC", "FLAG", "BANK", "SMALL", "FX"):
            self.assertNotIn(bad, names["quality"], bad)
        self.assertIn("GAP", names["gap"])
        for bad in ("GAPACQ", "DEAL", "QUAL"):
            self.assertNotIn(bad, names["gap"], bad)
        self.assertEqual(names["fallen"], ["FALL"])
        self.assertIn("ACCEL", names["accelerating"])
        self.assertNotIn("ACCNEG", names["accelerating"])
        self.assertEqual(names["catalysts"], ["CAT"])
        self.assertEqual([x["ticker"] for x in scan["removed"]], ["FLAG"])
        self.assertEqual([x["ticker"] for x in scan["bought"]], ["DEAL"])
        self.assertIn("FINSIC", [x["ticker"] for x in scan["finance"]])
        c = scan["counts"]
        self.assertEqual((c["below_min_cap"], c["foreign_currency"], c["financial"]), (1, 1, 1))
        self.assertIn("<title>Idea Scanner All lenses</title>", page)

    def test_the_gap_uses_priced_ins_math(self):
        scan, _ = self.run_scan()
        gap = next(r for r in scan["rows"] if r["ticker"] == "GAP")
        self.assertAlmostEqual(gap["implied"], 0.05, places=5)
        self.assertAlmostEqual(gap["gap"], 0.20 - 0.05, places=5)
        self.assertAlmostEqual(gap["roic_last"], 0.25 * 0.79 / 0.8, places=6)

    def test_flagged_names_can_be_kept(self):
        scan, _ = self.run_scan(keep_flagged=True)
        self.assertIn("FLAG", [r["ticker"] for r in scan["results"]["quality"]])

    def test_growth_above_the_cap_counts_as_40_percent(self):
        f = collections.defaultdict(dict)
        company(f, 1, g=0.80)
        m = sc.metrics(1, f, YEAR, Q)
        sc.add_market(m, {"marketCap": cap_for(0.10), "regularMarketPrice": 1, "fiftyTwoWeekHigh": 1})
        self.assertAlmostEqual(m["gap"], 0.40 - 0.10, places=5)


class Periods(unittest.TestCase):
    def test_latest_year_and_quarter(self):
        for today, want in (("2026-10-22", (2025, (2026, 2))), ("2026-02-10", (2024, (2025, 3))),
                            ("2026-05-20", (2025, (2025, 4))), ("2026-06-20", (2025, (2026, 1)))):
            os.environ["INVESTOR_TODAY"] = today
            try:
                self.assertEqual(sc.periods(), want, today)
            finally:
                os.environ.pop("INVESTOR_TODAY", None)
        self.assertEqual(sc.q_label(2026, 2, 4), "CY2025Q2")
        self.assertEqual(sc.q_label(2026, 1, 1), "CY2025Q4")


class Newsflow(unittest.TestCase):
    def test_only_the_8k_and_press_releases_count(self):
        self.assertTrue(sc.own_words({"doc_type": "8-K"}))
        self.assertTrue(sc.own_words({"doc_type": "EX-99.1"}))
        self.assertFalse(sc.own_words({"doc_type": "EX-10.1"}))  # a credit agreement's boilerplate
        self.assertFalse(sc.own_words({"doc_type": "EX-2.1"}))

    def test_headlines_about_the_company(self):
        heads = [{"date": "2026-10-21", "title": "United Rentals Announces Record Third Quarter Results and Raises "
                                                 "Full-Year 2026 Guidance", "link": "a"},
                 {"date": "2026-10-21", "title": "Jensen Huang on a new AI chip", "link": "b"},
                 {"date": "2026-10-20", "title": "URI misses on margins, shares fall", "link": "c"}]
        with mock.patch.object(sc.ik, "yahoo_headlines", lambda t, days=14: heads):
            out = sc.headline_info("URI", "United Rentals, Inc.", 14)
        self.assertEqual([h["about"] for h in out], [True, False, True])
        self.assertEqual(out[0]["tone"], "positive")
        self.assertEqual(out[2]["tone"], "negative")

    def test_generic_first_words_need_the_full_name(self):
        keys = sc.name_keys("AEP", "American Electric Power Company, Inc.")
        self.assertIn("american electric", keys)
        self.assertNotIn("american", keys)


if __name__ == "__main__":
    unittest.main()
