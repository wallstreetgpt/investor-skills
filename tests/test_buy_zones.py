import unittest

from _helpers import Offline, ik, load, run

bz = load("the-sniper", "buyzones")


def series(closes, spread=0.01):
    n = len(closes)
    return {"dates": ["2020-01-%02d" % (i % 28 + 1) if i < 28 else "2021-%05d" % i for i in range(n)],
            "close": closes, "high": [c * (1 + spread) for c in closes], "low": [c * (1 - spread) for c in closes],
            "open": closes, "volume": [1000] * n}


class Levels(unittest.TestCase):
    def test_pivots_find_the_swing_low(self):
        closes = [110, 108, 105, 102, 100, 103, 106, 109, 112, 115, 113, 111]
        wk = series(closes, 0.0)
        lows, highs = bz.find_pivots(wk, k=3, lookback=100)
        self.assertIn(4, lows)
        self.assertIn(9, highs)

    def test_cluster_merges_nearby_levels(self):
        c = [{"price": p, "kind": "swing low", "w": 1.0, "date": None} for p in (100, 101, 102.5, 130)]
        zones = bz.cluster(c, 0.03)
        self.assertEqual(len(zones), 2)
        self.assertEqual(len(zones[0]["members"]), 3)
        self.assertLessEqual(zones[0]["lo"], 100)
        self.assertGreaterEqual(zones[0]["hi"], 102.5)

    def test_record_counts_holds_and_breaks(self):
        # comes down to 100-102 twice and bounces, then breaks through
        closes = [120] * 30 + [101] * 3 + [110] * 10 + [125] * 10 + [101] * 2 + [112] * 10 + [125] * 5 + [95] * 5
        daily = series(closes, 0.0)
        daily["dates"] = ["d%04d" % i for i in range(len(closes))]
        z = {"lo": 100, "hi": 102, "members": [{"date": "d0000", "kind": "swing low", "w": 1}]}
        r = bz.zone_record(z, daily)
        self.assertEqual((r["held"], r["broke"]), (2, 1))

    def test_historical_drop_odds(self):
        closes = [100.0] * 300 + [70.0] * 10 + [100.0] * 300
        daily = series(closes, 0.0)
        odds = bz.historical_drop_odds(daily, [0.2, 0.5], horizon=252)
        self.assertGreater(odds[0], 0.3)
        self.assertEqual(odds[1], 0.0)

    def test_touch_odds_fall_with_depth(self):
        import random
        rng = random.Random(5)
        closes = [100.0]
        for _ in range(600):
            closes.append(closes[-1] * (1 + rng.gauss(0, 0.02)))
        o6, o12 = bz.touch_odds(series(closes), [0.95, 0.85, 0.7], 0.09, seed=1, paths=800)
        self.assertGreater(o12[0], o12[1])
        self.assertGreater(o12[1], o12[2])
        for a, b in zip(o6, o12):
            self.assertLessEqual(a, b)


class Valuation(unittest.TestCase):
    doc = {"mode": "fcf", "model_base": 100.0, "years": 10, "discount": 0.09, "terminal": 0.025,
           "shares_effective": 10.0, "net_cash": 50.0, "base_rate_sorted": [0.0, 0.02, 0.04, 0.06, 0.08]}

    def test_price_and_growth_round_trip(self):
        p = bz.price_for_growth(self.doc, 0.07)
        self.assertAlmostEqual(bz.implied_at_price(self.doc, p), 0.07, places=6)
        self.assertGreater(bz.price_for_growth(self.doc, 0.10), p)

    def test_peers_share(self):
        self.assertAlmostEqual(bz.peers_share(self.doc, 0.05), 0.4)


class LiveFixtureRun(unittest.TestCase):
    def test_nvda(self):
        with Offline() as o:
            code, out = run(bz.main, ["NVDA", "--paths", "300"])
            self.assertEqual(code, 0, out)
            doc = o.section("NVDA", "buy_zones")
        self.assertIn("Zone 1", out)
        self.assertTrue(doc["zones"])
        for z in doc["zones"]:
            self.assertLess(z["hi"], doc["price"])
            self.assertTrue(0 <= z["score"] <= 100)
            self.assertTrue(0 <= z["odds6"] <= z["odds12"] <= 1)


if __name__ == "__main__":
    unittest.main()
