import math
import random
import unittest
from types import SimpleNamespace

from _helpers import Offline, ik, load, run

tm = load("time-machine", "timemachine")


def args(**kw):
    base = dict(years=10, amount=10000.0, monthly=0.0, paths=400, block=12, seed=7, market_return=0.09, vol_months=0)
    base.update(kw)
    return SimpleNamespace(**base)


class Simulation(unittest.TestCase):
    def test_no_volatility_compounds_exactly_at_the_scenario_return(self):
        rs = [0.02] * 240
        rb = [0.01] * 240
        res = tm.simulate(rs, rb, [("x", "x", 0.12)], args())[0]
        self.assertAlmostEqual(res["end"][50], 10000 * 1.12 ** 10, delta=0.01)
        self.assertAlmostEqual(res["bench_end"][50], 10000 * 1.09 ** 10, delta=0.01)
        self.assertEqual(res["p_loss"], 0.0)

    def test_monthly_contributions_add_up(self):
        res = tm.simulate([0.0] * 120, [0.0] * 120, [("x", "x", 0.0)], args(monthly=500.0, years=5))[0]
        self.assertAlmostEqual(res["invested"], 10000 + 500 * 59)
        self.assertAlmostEqual(res["end"][50], res["invested"], delta=0.01)

    def test_center_follows_the_scenario_not_the_history(self):
        rng = random.Random(3)
        rs = [rng.gauss(0.03, 0.08) for _ in range(300)]   # a past of ~40% a year
        rb = [rng.gauss(0.007, 0.04) for _ in range(300)]
        res = tm.simulate(rs, rb, [("x", "x", 0.06)], args(paths=2000))[0]
        self.assertAlmostEqual(res["cagr"][50], 0.06, delta=0.012)

    def test_vol_scale_keeps_the_mean_and_matches_recent_volatility(self):
        xs = [0.1, -0.1] * 100 + [0.02, -0.02] * 30
        scaled, k = tm.vol_scale(xs, 60)
        self.assertAlmostEqual(ik.mean(scaled), ik.mean(xs), places=12)
        self.assertAlmostEqual(ik.stdev(scaled), ik.stdev(xs[-60:]), places=9)
        flat, k1 = tm.vol_scale([0.01] * 200, 60)
        self.assertEqual(k1, 1.0)

    def test_history_windows(self):
        rs = [math.log(1.01)] * 150
        rb = [0.0] * 150
        used = ["m%d" % i for i in range(150)]
        hw = tm.history_windows(rs, rb, used, 10)
        self.assertEqual(hw["n"], 150 - 120 + 1)
        self.assertAlmostEqual(hw["median"], 1.01 ** 12 - 1, places=9)
        self.assertEqual(hw["p_beat"], 1.0)


class LiveFixtureRun(unittest.TestCase):
    def test_nvda_is_reproducible(self):
        with Offline():
            code1, out1 = run(tm.main, ["NVDA", "--paths", "300", "--no-write"])
            code2, out2 = run(tm.main, ["NVDA", "--paths", "300", "--no-write"])
        self.assertEqual(code1, 0, out1)
        self.assertEqual(out1, out2)
        self.assertIn("What really happened", out1)

    def test_raw_history_mode(self):
        with Offline():
            code, out = run(tm.main, ["NVDA", "--paths", "200", "--scenario", "history", "--no-write"])
        self.assertEqual(code, 0, out)
        self.assertIn("Raw history (hindsight)", out)


if __name__ == "__main__":
    unittest.main()
