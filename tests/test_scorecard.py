import json
import os
import unittest

from _helpers import Offline, ik, load, run

sc = load("the-judge", "scorecard")


def facts_with(entries_by_tag, unit="USD"):
    return {"facts": {"us-gaap": {tag: {"units": {unit: entries}} for tag, entries in entries_by_tag.items()}}}


class Rules(unittest.TestCase):
    def test_straight_line_scores(self):
        self.assertEqual(sc.lin(0.01, 0.01, 0.07), 0)
        self.assertEqual(sc.lin(0.07, 0.01, 0.07), 100)
        self.assertAlmostEqual(sc.lin(0.04, 0.01, 0.07), 50)
        self.assertEqual(sc.lin(0.50, 0.01, 0.07), 100)          # clamped
        self.assertAlmostEqual(sc.lin(28.5, 45, 12), 50)          # lower is better
        self.assertIsNone(sc.lin(None, 0, 1))
        self.assertAlmostEqual(sc.inverse(75, 45, 12), 20.25)

    def test_bands(self):
        self.assertEqual(sc.band(81.4), "Highly Attractive")
        self.assertEqual(sc.band(80), "Highly Attractive")
        self.assertEqual(sc.band(79.9), "Attractive")
        self.assertEqual(sc.band(50), "Fair")
        self.assertEqual(sc.band(34.9), "Avoid")

    def test_composite_matches_the_stated_weights_and_skips_missing_pillars(self):
        p = {"quality": 90, "trend": 88, "forward": 86, "valuation": 75, "catalysts": 66}
        self.assertAlmostEqual(sc.composite(p), 81.45)   # the worked example on the August NVDA card
        del p["catalysts"]
        self.assertAlmostEqual(sc.composite(p), (0.2 * 90 + 0.2 * 88 + 0.2 * 86 + 0.25 * 75) / 0.85)

    def test_valuation_weights_its_three_views_10_8_7(self):
        M = [{"key": "a", "pillar": "valuation", "part": "absolute", "score": 62.0},
             {"key": "b", "pillar": "valuation", "part": "peers", "score": 82.0},
             {"key": "c", "pillar": "valuation", "part": "history", "score": 85.0}]
        pillars, parts = sc.pillar_scores(M)
        self.assertAlmostEqual(pillars["valuation"], (62 * 10 + 82 * 8 + 85 * 7) / 25.0)
        pillars, _ = sc.pillar_scores(M[:1] + M[2:])     # no peers: absolute and history only
        self.assertAlmostEqual(pillars["valuation"], (62 * 10 + 85 * 7) / 17.0)

    def test_sensitivity_names_the_weakest_and_strongest_input(self):
        M = [sc.metric("x", "quality", "Gross margin", 0.70, "pct", 0.2, 0.7, "t"),
             sc.metric("y", "quality", "Free cash flow yield", 0.025, "pct", 0.01, 0.07, "t")]
        s = sc.sensitivity(M, "quality")
        self.assertIn("Weakest input: free cash flow yield at 2.5%. At 5.5% this pillar would score 88.", s["text"])
        self.assertIn("Strongest: gross margin at 70.0%. At 40.0% it would fall to 32.", s["text"])
        M2 = [sc.metric("z", "valuation", "EV/Sales vs peer median 13.0x", -0.35, "spct", -0.4, 0.4, "t", part="peers")]
        self.assertIn("EV/Sales", sc.sensitivity(M2, "valuation")["text"])   # acronyms keep their capitals


class Quarters(unittest.TestCase):
    def test_fourth_quarter_uses_the_figures_as_first_reported(self):
        # FY ends Jan; the 10-K's EPS was restated after a 10:1 split, the original 9 months were not
        eps = [
            {"start": "2022-01-31", "end": "2022-10-30", "val": 0.90, "form": "10-Q", "filed": "2022-11-18"},
            {"start": "2022-01-31", "end": "2023-01-29", "val": 1.47, "form": "10-K", "filed": "2023-02-24"},
            {"start": "2022-01-31", "end": "2023-01-29", "val": 0.147, "form": "10-K", "filed": "2025-02-26"},
            {"start": "2022-10-31", "end": "2023-01-29", "val": 0.057, "form": "10-K", "filed": "2025-02-26"},
        ]
        f = facts_with({"EarningsPerShareDiluted": eps}, unit="USD/shares")
        q = ik.quarterly_series(f, "eps_diluted", "USD", units=("USD/shares",))
        self.assertEqual([r["end"] for r in q], ["2023-01-29"])
        self.assertAlmostEqual(q[0]["val"], 0.057)       # a three-month figure as filed wins
        f2 = facts_with({"EarningsPerShareDiluted": eps[:3]}, unit="USD/shares")
        q2 = ik.quarterly_series(f2, "eps_diluted", "USD", units=("USD/shares",))
        self.assertAlmostEqual(q2[0]["val"], 1.47 - 0.90)  # derived on one basis, never 0.147 - 0.90
        self.assertTrue(q2[0]["derived"])
        self.assertEqual(q2[0]["first_filed"], "2023-02-24")

    def test_split_adjust(self):
        splits = [("2021-07-20", 4.0), ("2024-06-10", 10.0)]
        self.assertAlmostEqual(sc.split_adjust(4.0, "2021-05-01", splits, True), 0.1)
        self.assertAlmostEqual(sc.split_adjust(4.0, "2022-05-01", splits, True), 0.4)
        self.assertAlmostEqual(sc.split_adjust(4.0, "2025-05-01", splits, True), 4.0)
        self.assertAlmostEqual(sc.split_adjust(10, "2023-01-01", splits, False), 100)

    def test_pe_history_uses_what_was_known_each_month(self):
        qs = [{"end": e, "eps": 1.0, "eps_filed": f, "filed": f} for e, f in (
            ("2025-03-31", "2025-05-01"), ("2025-06-30", "2025-08-01"), ("2025-09-30", "2025-11-01"),
            ("2025-12-31", "2026-02-01"), ("2026-03-31", "2026-05-01"))]
        qs[-1]["eps"] = 2.0
        ttm = sc.ttm_eps_series(qs)
        self.assertEqual(ttm, [("2026-02-01", 4.0), ("2026-05-01", 5.0)])
        monthly = {"dates": ["2026-01-01", "2026-03-01", "2026-06-01"], "close": [40.0, 40.0, 50.0]}
        self.assertEqual(sc.pe_history(monthly, ttm), [("2026-03-01", 10.0), ("2026-06-01", 10.0)])

    def test_fiscal_year_labels(self):
        self.assertEqual(sc.fy_label("2026-01-25"), 2026)   # NVIDIA calls this FY2026
        self.assertEqual(sc.fy_label("2025-09-27"), 2025)
        self.assertEqual(sc.fy_label("2026-01-03"), 2025)   # a 52/53-week year spilling into January


class FilingText(unittest.TestCase):
    def test_guidance_point_and_range(self):
        text = ("Outlook\nNVIDIA's outlook for the third quarter of fiscal 2027 is as follows:\n"
                "• Revenue is expected to be $108.0 billion, plus or minus 2%. NVIDIA is not assuming any Data "
                "Center compute revenue from China in its outlook.\n")
        mid, sentence = sc.parse_guidance(text)
        self.assertEqual(mid, 108.0e9)
        self.assertEqual(sentence, "Revenue is expected to be $108.0 billion, plus or minus 2%.")
        mid, _ = sc.parse_guidance("Revenue is expected to be in the range of $2.10 billion to $2.20 billion.")
        self.assertAlmostEqual(mid / 1e9, 2.15)
        mid, _ = sc.parse_guidance("Third quarter revenue is expected to be between $850 million and $870 million.")
        self.assertAlmostEqual(mid / 1e6, 860)
        self.assertEqual(sc.parse_guidance("We do not provide guidance."), (None, None))

    def test_customer_sentences(self):
        s = ("For fiscal year 2026, sales to one direct customer represented 22% of total revenue and sales to "
             "another direct customer represented 14% of total revenue, all of which were primarily attributable "
             "to the Compute & Networking segment.")
        self.assertEqual(sc.customer_concentration(s)[0], 22)
        s = ("Our largest customer in 2023 , 2024 and 2025 accounted for 25% , 22% and 19% of our net revenue "
             "in the respective year.")
        self.assertEqual(sc.customer_concentration(s)[0], 19)
        s = ("Our ten largest customers in 2023 , 2024 and 2025 accounted for approximately, 70% , 76% and 78% of "
             "our net revenue in the respective year.")
        self.assertIsNone(sc.customer_concentration(s)[0])
        top, _, neg = sc.customer_concentration("No single customer accounted for 10% or more of our net "
                                                "operating revenues in 2025.")
        self.assertIsNone(top)
        self.assertEqual(len(neg), 1)
        self.assertIsNone(sc.customer_concentration("One customer accounted for 15% of accounts receivable.")[0])

    def test_customer_sentence_in_a_real_annual_report(self):
        with Offline():
            text, filing = ik.annual_report_text(21344)   # Coca-Cola's 10-K: one bottler near 10%
            top, quotes, _ = sc.customer_concentration(text)
        self.assertEqual(filing["form"], "10-K")
        self.assertTrue(quotes)
        self.assertTrue(5 <= top <= 15, top)

    def test_risk_headings(self):
        text = ("Item 1A. Risk Factors\nRisks related to our industry\n"
                "Competition could adversely impact our market share and financial results.\n"
                + "Body text that explains the risk at length. " * 20 + "\n"
                "We receive a significant amount of our revenue from a limited number of customers.\n"
                "Item 1B. Unresolved Staff Comments\nNone.\n")
        self.assertEqual(sc.risk_headings(text), [
            "Competition could adversely impact our market share and financial results.",
            "We receive a significant amount of our revenue from a limited number of customers."])


class Recorded(unittest.TestCase):
    """NVDA as recorded on 22 Oct 2026 (filings text and 8-K search skipped)."""

    def test_nvda(self):
        with Offline() as o:
            code, out = run(sc.main, ["NVDA", "--no-text", "--no-news"])
            self.assertEqual(code, 0, out)
            doc = o.section("NVDA", "scorecard")
            notes_path = os.path.join(os.environ["INVESTOR_DIR"], "scorecards", "NVDA-notes.json")
            with open(notes_path, encoding="utf-8") as f:
                template = json.load(f)
        self.assertEqual(set(doc["pillars"]), {"quality", "trend", "forward", "valuation", "catalysts"})
        self.assertTrue(70 <= doc["score"] <= 95, doc["score"])
        inputs = {i["key"]: i for i in doc["inputs"]}
        self.assertGreater(inputs["t_growth"]["value"], 0.5)            # revenue roughly doubled
        self.assertTrue(0.2 < inputs["h_pe"]["value"] < 0.8)           # P/E well below its 5-year median
        self.assertGreater(inputs["q_roic"]["value"], 0.5)
        self.assertIn("thesis", template)
        self.assertIn("Score", out)

    def test_notes_are_escaped_and_marked_as_claude(self):
        with Offline():
            d = sc.gather("NVDA", (), with_text=False, with_news=False)
            ev = sc.evaluate(d)
            html = sc.report(d, ev, {"thesis": [{"lead": "<script>x</script>", "text": "t"}],
                                     "change_mind": "Two things would undo it."}, prior=("2026-08-06", 80.6))
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('<span class="tag tag-c">Claude</span>', html)
        self.assertIn("vs 6 Aug 2026", html)

    def test_change_since_an_earlier_day(self):
        with Offline():
            ev = {"score": 80.0, "band": "Highly Attractive", "pillars": {}}
            os.environ["INVESTOR_TODAY"] = "2026-08-06"
            sc.save_history("NVDA", ev)
            os.environ["INVESTOR_TODAY"] = "2026-10-22"
            sc.save_history("NVDA", dict(ev, score=83.0))
            self.assertEqual(sc.prior_score("NVDA"), ("2026-08-06", 80.0))


if __name__ == "__main__":
    unittest.main()
