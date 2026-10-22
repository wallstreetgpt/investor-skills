import unittest

from _helpers import Offline, load, run

moat = load("irreplaceable-test", "moat")


class CustomerText(unittest.TestCase):
    """Sentences as they appear in real annual reports."""

    def top(self, text):
        return moat.customer_concentration(text)[0]

    def test_single_direct_customer(self):
        s = ("For fiscal year 2026, sales to one direct customer represented 22% of total revenue and sales to "
             "another direct customer represented 14% of total revenue, all of which were primarily attributable "
             "to the Compute & Networking segment.")
        self.assertEqual(self.top(s), 22)

    def test_respective_years_take_the_latest(self):
        s = ("Our largest customer in 2023 , 2024 and 2025 accounted for 25% , 22% and 19% of our net revenue "
             "in the respective year.")
        self.assertEqual(self.top(s), 19)
        s2 = ("Direct sales to one semiconductor solutions customer, which is a distributor, accounted for 32% and "
              "28% of our net revenue for fiscal years 2025 and 2024, respectively.")
        self.assertEqual(self.top(s2), 32)

    def test_groups_are_not_single_customers(self):
        s = ("Our ten largest customers in 2023 , 2024 and 2025 accounted for approximately, 70% , 76% and 78% of "
             "our net revenue in the respective year.")
        self.assertIsNone(self.top(s))

    def test_negation_is_a_pass(self):
        s = "No single customer accounted for 10% or more of our net operating revenues in 2025."
        top, quotes, neg = moat.customer_concentration(s)
        self.assertIsNone(top)
        self.assertEqual(len(neg), 1)

    def test_receivables_only_is_ignored(self):
        s = "One customer accounted for 15% of accounts receivable as of December 31, 2025."
        self.assertIsNone(self.top(s))

    def test_distributor_is_capped_at_warn(self):
        text = ("Direct sales to one semiconductor solutions customer, which is a distributor, accounted for 32% "
                "of our net revenue for fiscal year 2025.")
        t = moat.t_customers(text, {"form": "10-K", "filed": "2025-12-18"})
        self.assertEqual(t["grade"], moat.WARN)


class Tests(unittest.TestCase):
    def rows(self, gms, revs=None, shares=None):
        out = []
        for i, y in enumerate(range(2016, 2016 + len(gms))):
            out.append({"year": y, "gross_margin": gms[i], "revenue": (revs or [100] * len(gms))[i],
                        "shares": (shares or [None] * len(gms))[i]})
        return out

    def test_pricing_power_grades(self):
        gms = [0.5] * 5 + [0.60, 0.595, 0.60] + [0.6] * 2   # 2021 = 0.60, 2022 = 0.595
        self.assertEqual(moat.t_pricing_power(self.rows(gms))["grade"], moat.PASS)
        gms[6] = 0.55
        self.assertEqual(moat.t_pricing_power(self.rows(gms))["grade"], moat.FAIL)
        revs = [100] * 5 + [100, 80, 100] + [100] * 2       # but revenue slumped 20% that year
        self.assertEqual(moat.t_pricing_power(self.rows(gms, revs))["grade"], moat.WARN)

    def test_negative_margins_cannot_show_pricing_power(self):
        gms = [0.1] * 5 + [-8.0, -1.9, -0.4] + [0.1] * 2
        self.assertEqual(moat.t_pricing_power(self.rows(gms))["grade"], moat.NA)

    def test_dilution_starts_after_an_ipo_jump(self):
        rows = self.rows([0.4] * 6, shares=[100, 105, 900, 920, 940, 960])
        t = moat.t_dilution(rows)
        self.assertEqual(t["grade"], moat.WARN)
        self.assertIn("one-off jump", t["detail"])

    def test_competition_section_skips_cross_references(self):
        text = ("Item 1. Business 2\nItem 1A. Risk Factors 9\nITEM 1. BUSINESS\nWe make things.\nCompetition\n"
                "We compete with Acme and Globex on price.\nItem 1A. Risk Factors\nRisks.\n"
                "See \"Item 1. Business\" of this report for more.")
        self.assertTrue(moat.competition_excerpt(text).startswith("We compete with Acme"))


class LiveFixtureRun(unittest.TestCase):
    def test_coca_cola(self):
        with Offline() as o:
            code, out = run(moat.main, ["KO"])
            self.assertEqual(code, 0, out)
            doc = o.section("KO", "irreplaceable")
        self.assertEqual(doc["tests"]["customers"]["grade"], moat.WARN)   # one bottler at 10%
        self.assertIn("bottler", doc["tests"]["customers"]["detail"])
        self.assertEqual(doc["tests"]["roic"]["grade"], moat.PASS)
        self.assertTrue(doc["competition_excerpt"].startswith("The commercial beverage industry"))


if __name__ == "__main__":
    unittest.main()
