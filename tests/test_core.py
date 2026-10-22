import os
import random
import subprocess
import sys
import unittest

from _helpers import ROOT, ik


def facts_with(entries_by_tag, taxonomy="us-gaap", unit="USD"):
    return {"facts": {taxonomy: {tag: {"units": {unit: entries}} for tag, entries in entries_by_tag.items()}}}


class ValuationMath(unittest.TestCase):
    def test_implied_growth_recovers_the_growth_that_made_the_value(self):
        for g in (-0.05, 0.0, 0.03, 0.15, 0.4):
            v = ik.dcf_value(100.0, g, 10, 0.09, 0.025)
            self.assertAlmostEqual(ik.implied_growth_fcf(v, 100.0, 10, 0.09, 0.025), g, places=6)

    def test_return_at_the_priced_in_growth_is_the_discount_rate(self):
        v = ik.dcf_value(100.0, 0.12, 10, 0.09, 0.025)
        self.assertAlmostEqual(ik.scenario_return(v, 100.0, 0.12, 10, 0.09, 0.025), 0.09, places=6)
        # more growth than priced in -> more than the discount rate, and less -> less
        self.assertGreater(ik.scenario_return(v, 100.0, 0.20, 10, 0.09, 0.025), 0.09)
        self.assertLess(ik.scenario_return(v, 100.0, 0.02, 10, 0.09, 0.025), 0.09)

    def test_revenue_mode_round_trip(self):
        flows, tn = ik.revenue_path_flows(1000.0, -0.2, 0.12, 0.25, 10, 0.025)
        v = ik.pv_flows(flows, tn, 0.09, 0.025)
        self.assertAlmostEqual(ik.implied_growth_revenue(v, 1000.0, -0.2, 0.12, 10, 0.09, 0.025), 0.25, places=6)
        r = ik.scenario_return(v, 1000.0, 0.25, 10, 0.09, 0.025, mode="revenue", margin0=-0.2, margin_target=0.12)
        self.assertAlmostEqual(r, 0.09, places=6)

    def test_irr(self):
        self.assertAlmostEqual(ik.irr([-100, 110]), 0.10, places=7)
        self.assertAlmostEqual(ik.irr([-100, 0, 121]), 0.10, places=7)

    def test_growth_out_of_range_is_none(self):
        self.assertIsNone(ik.implied_growth_fcf(1e15, 1.0, 10, 0.09, 0.025))
        self.assertIsNone(ik.implied_growth_fcf(1000, -5.0, 10, 0.09, 0.025))


class Statistics(unittest.TestCase):
    def test_percentile(self):
        xs = [1, 2, 3, 4, 5]
        self.assertEqual(ik.percentile(xs, 50), 3)
        self.assertEqual(ik.percentile(xs, 0), 1)
        self.assertAlmostEqual(ik.percentile(xs, 25), 2)
        self.assertAlmostEqual(ik.percentile([1, 2], 50), 1.5)

    def test_stationary_bootstrap_block_length(self):
        rng = random.Random(1)
        idx = ik.stationary_bootstrap(1000, 200000, 12, rng)
        runs, cur = [], 1
        for a, b in zip(idx, idx[1:]):
            if b == (a + 1) % 1000:
                cur += 1
            else:
                runs.append(cur)
                cur = 1
        self.assertAlmostEqual(sum(runs) / float(len(runs)), 12, delta=0.6)

    def test_max_drawdown(self):
        self.assertAlmostEqual(ik.max_drawdown([100, 120, 60, 90, 130]), -0.5)
        self.assertEqual(ik.max_drawdown([1, 2, 3]), 0.0)


class Symbols(unittest.TestCase):
    def test_yahoo_symbol(self):
        self.assertEqual(ik.yahoo_symbol("brk.b"), "BRK-B")
        self.assertEqual(ik.yahoo_symbol("005930.KS"), "005930.KS")
        self.assertEqual(ik.yahoo_symbol("SU.PA"), "SU.PA")
        self.assertEqual(ik.yahoo_symbol("7203.T"), "7203.T")
        self.assertEqual(ik.yahoo_symbol("NVDA"), "NVDA")

    def test_url_key_ignores_the_crumb(self):
        a = ik.url_key("https://query2.finance.yahoo.com/v7/finance/quote?symbols=NVDA")
        b = ik.url_key("https://query2.finance.yahoo.com/v7/finance/quote?symbols=NVDA&crumb=abc.def")
        self.assertEqual(a, b)


class Facts(unittest.TestCase):
    def test_ttm_is_fiscal_year_plus_ytd_minus_prior_ytd(self):
        f = facts_with({"NetCashProvidedByUsedInOperatingActivities": [
            {"start": "2025-01-01", "end": "2025-12-31", "val": 100, "form": "10-K", "fp": "FY", "filed": "2026-02-01", "accn": "a"},
            {"start": "2025-01-01", "end": "2025-06-30", "val": 50, "form": "10-Q", "fp": "Q2", "filed": "2025-08-01", "accn": "b"},
            {"start": "2026-01-01", "end": "2026-06-30", "val": 60, "form": "10-Q", "fp": "Q2", "filed": "2026-08-01", "accn": "c"},
        ]})
        t = ik.ttm_value(f, "cfo")
        self.assertEqual(t["val"], 110)
        self.assertEqual(t["end"], "2026-06-30")

    def test_annual_series_prefers_tag_priority_then_latest_filing(self):
        f = facts_with({
            "Revenues": [{"start": "2024-01-01", "end": "2024-12-31", "val": 90, "form": "10-K", "filed": "2025-02-01"}],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 100, "form": "10-K", "filed": "2025-02-01"},
                {"start": "2024-01-01", "end": "2024-12-31", "val": 101, "form": "10-K", "filed": "2026-02-01"}],
        })
        rows = ik.annual_series(f, "revenue")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["val"], 101)

    def test_net_cash_drops_stale_investments_but_keeps_stale_debt(self):
        f = facts_with({
            "CashAndCashEquivalentsAtCarryingValue": [{"end": "2026-06-30", "val": 50, "form": "10-Q", "accn": "x"}],
            "MarketableSecuritiesCurrent": [{"end": "2021-12-31", "val": 999, "form": "10-K", "accn": "y"}],
            "LongTermDebtNoncurrent": [{"end": "2025-12-31", "val": 30, "form": "10-K", "accn": "z"}],
        })
        nc = ik.net_cash(f)
        self.assertEqual(nc["val"], 50 - 30)
        self.assertEqual(len(nc["stale"]), 2)

    def test_shares_sum_classes_and_ignore_stale_cover_counts(self):
        recent = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2026-07-01", "val": 100, "accn": "q"}, {"end": "2026-07-01", "val": 40, "accn": "q"}]}}}}}
        os.environ["INVESTOR_TODAY"] = "2026-10-22"
        try:
            self.assertEqual(ik.shares_outstanding(recent)["val"], 140)
            stale = {"facts": {
                "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [{"end": "2011-04-29", "val": 7, "accn": "o"}]}}},
                "us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                    {"start": "2026-04-01", "end": "2026-06-30", "val": 500, "accn": "n", "filed": "2026-08-01"}]}}}}}
            s = ik.shares_outstanding(stale)
            self.assertEqual(s["val"], 500)
            self.assertIn("diluted", s["source"])
        finally:
            os.environ.pop("INVESTOR_TODAY", None)


class Setup(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.home, self.work = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.saved = {k: os.environ.get(k) for k in ("INVESTOR_HOME", "INVESTOR_DIR", "SEC_USER_AGENT", "INVESTOR_CACHE")}
        os.environ["INVESTOR_HOME"] = self.home
        os.environ["INVESTOR_DIR"] = os.path.join(self.work, "investor")
        for k in ("SEC_USER_AGENT", "INVESTOR_CACHE"):
            os.environ.pop(k, None)

    def tearDown(self):
        import shutil
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)

    def test_contact_saved_once_is_found_from_any_folder(self):
        with self.assertRaises(ik.InvestError):
            ik.sec_user_agent()
        ik.save_contact("Jane Doe", "jane@mail.com")
        self.assertEqual(ik.sec_user_agent(), "Jane Doe jane@mail.com")

    def test_a_projects_own_config_wins(self):
        ik.save_contact("Jane Doe", "jane@mail.com")
        os.makedirs(os.environ["INVESTOR_DIR"])
        with open(os.path.join(os.environ["INVESTOR_DIR"], "config.json"), "w") as f:
            f.write('{"sec_user_agent": "Team Account team@firm.com"}')
        self.assertEqual(ik.sec_user_agent(), "Team Account team@firm.com")

    def test_bad_contacts_are_refused(self):
        for name, email in (("", "a@b.com"), ("Jane", "not-an-email"), ("Jane", "jane@localhost")):
            with self.assertRaises(ik.InvestError):
                ik.save_contact(name, email)


class Packaging(unittest.TestCase):
    def test_every_skill_carries_the_same_core(self):
        code = subprocess.call([sys.executable, os.path.join(ROOT, "tools", "sync_core.py"), "--check"])
        self.assertEqual(code, 0, "run tools/sync_core.py")

    def test_every_skill_has_a_skill_md_with_a_name(self):
        for name in os.listdir(os.path.join(ROOT, "skills")):
            path = os.path.join(ROOT, "skills", name, "SKILL.md")
            self.assertTrue(os.path.exists(path), name)
            with open(path, encoding="utf-8") as f:
                head = f.read(400)
            self.assertIn("name: %s" % name, head)
            self.assertIn("description:", head)


if __name__ == "__main__":
    unittest.main()
