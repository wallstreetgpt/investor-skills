import json
import os
import shutil
import tempfile
import unittest

from _helpers import Offline, ik, load, run

pricedin = load("priced-in", "pricedin")


class LiveFixtureRun(unittest.TestCase):
    """NVIDIA on 2026-10-22, from recorded SEC and Yahoo responses."""

    def test_nvda(self):
        with Offline() as o:
            code, out = run(pricedin.main, ["NVDA", "--no-base-rates"])
            self.assertEqual(code, 0, out)
            doc = o.section("NVDA", "priced_in")
        self.assertEqual(doc["mode"], "fcf")
        self.assertAlmostEqual(doc["implied_growth"], 0.162, delta=0.002)
        priced = [s for s in doc["scenarios"] if s["key"] == "priced_in"][0]
        self.assertAlmostEqual(priced["annual_return"], 0.09, places=5)
        # the reverse DCF must reproduce the enterprise value it was solved from
        v = ik.dcf_value(doc["model_base"], doc["implied_growth"], 10, 0.09, 0.025)
        self.assertAlmostEqual(v / doc["ev"], 1.0, places=5)
        self.assertIn("if investors want 9% a year back", out)
        self.assertIn("Sensitivity", out)

    def test_price_override_is_not_saved(self):
        with Offline() as o:
            code, out = run(pricedin.main, ["NVDA", "--no-base-rates", "--price", "150"])
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(os.path.join(os.environ["INVESTOR_DIR"], "NVDA.json")))
        self.assertIn("At $150.00", out)


class BaseRates(unittest.TestCase):
    """Synthetic SEC frames: 40 companies with known growth, every start year 2011-2015.
    They start at $80B so that even the shrinking ones stay in the over-$50B bracket in 2015."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.growth = [(-0.05 + 0.005 * i) for i in range(40)]  # -5% .. +14.5%
        for year in range(2011, 2026):
            for tag in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"):
                data = []
                if tag == "Revenues":
                    data = [{"cik": 1000 + i, "val": 80e9 * (1 + g) ** (year - 2011)} for i, g in enumerate(self.growth)]
                url = "https://data.sec.gov/api/xbrl/frames/us-gaap/%s/USD/CY%d.json" % (tag, year)
                with open(os.path.join(self.dir, ik.url_key(url)), "w") as f:
                    json.dump({"data": data}, f)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pooled_percentiles_match_the_true_growth(self):
        with Offline(fixtures=self.dir):
            br = pricedin.base_rates(300e9, 10, progress=False)
        self.assertEqual(br["n"], 40 * 5)
        self.assertAlmostEqual(br["p50"], sorted(self.growth)[19] + 0.0025, places=6)
        self.assertAlmostEqual(pricedin.share_at_or_above(br["sorted"], 0.10), 10 / 40.0, places=6)
        self.assertEqual(br["bucket"], "revenue over $50B")

    def test_companies_are_bucketed_by_their_starting_size(self):
        # the same 40 companies, but starting at $52B: the shrinkers fall below $50B in later start years
        for name in os.listdir(self.dir):
            path = os.path.join(self.dir, name)
            with open(path) as f:
                d = json.load(f)
            for row in d["data"]:
                row["val"] *= 52.0 / 80.0
            with open(path, "w") as f:
                json.dump(d, f)
        with Offline(fixtures=self.dir):
            br = pricedin.base_rates(300e9, 10, progress=False)
        self.assertLess(br["n"], 200)
        self.assertGreater(br["n"], 150)

    def test_small_buckets_are_refused(self):
        with Offline(fixtures=self.dir):
            self.assertIsNone(pricedin.base_rates(1e9, 10, progress=False))


if __name__ == "__main__":
    unittest.main()
