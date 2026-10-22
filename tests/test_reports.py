import os
import re
import unittest

from _helpers import Offline, ik, load, run

pricedin = load("priced-in", "pricedin")
tm = load("time-machine", "timemachine")
bz = load("buy-zones", "buyzones")
sc = load("scorecard", "scorecard")


class Reports(unittest.TestCase):
    """Every skill writes a self-contained report page that follows the artifact page rules."""

    def check_page(self, path, title):
        with open(path, encoding="utf-8") as f:
            html = f.read()
        head = html[:8192]
        self.assertIn("<title>%s</title>" % title, head)
        self.assertIn('name="viewport"', head)
        for tag in ("<!doctype", "<html", "<head", "<body"):
            self.assertIsNone(re.search(re.escape(tag) + r"[\s>]", html.lower()), "the publisher adds %s" % tag)
        # only Google Fonts may be loaded from elsewhere; scripts are inline
        self.assertEqual(re.findall(r'<script[^>]+src=', html), [])
        for href in re.findall(r'<link[^>]+href="([^"]+)"', html):
            self.assertTrue(href.startswith("https://fonts.googleapis.com"), href)
        # light tokens on :root, redefined for both dark routes
        self.assertIn("@media (prefers-color-scheme: dark)", html)
        self.assertIn(':root[data-theme="dark"]', html)
        # charts take colours from tokens, never hard-coded fills
        for svg in re.findall(r'<svg class="chart".*?</svg>', html, re.S):
            self.assertIsNone(re.search(r'(fill|stroke)="#', svg), "hard-coded colour in a chart")
        self.assertIn(ik.esc(ik.REPORT_DISCLAIMER), html)  # the full disclaimer, word for word
        return html

    def test_all_five(self):
        with Offline():
            d = os.environ["INVESTOR_DIR"]
            self.assertEqual(run(pricedin.main, ["NVDA", "--no-base-rates"])[0], 0)
            self.check_page(os.path.join(d, "reports", "NVDA-priced-in.html"), "NVDA Priced-In")
            self.assertEqual(run(tm.main, ["NVDA", "--paths", "200"])[0], 0)
            html = self.check_page(os.path.join(d, "reports", "NVDA-time-machine.html"), "NVDA Time Machine")
            self.assertIn("data-tip=", html)
            self.assertEqual(run(bz.main, ["NVDA", "--paths", "200"])[0], 0)
            self.check_page(os.path.join(d, "reports", "NVDA-buy-zones.html"), "NVDA Buy Zones")
            self.assertEqual(run(sc.main, ["NVDA", "--no-text", "--no-news"])[0], 0)
            html = self.check_page(os.path.join(d, "reports", "NVDA-scorecard.html"), "NVDA Scorecard")
            self.assertIn("SCORE / 100", html)
            self.assertIn("data-tip=", html)  # the P/E history chart has hover details

    def test_standalone_svgs_keep_their_dark_style(self):
        with Offline():
            d = os.environ["INVESTOR_DIR"]
            run(tm.main, ["NVDA", "--paths", "200"])
            with open(os.path.join(d, "charts", "NVDA-time-machine.svg"), encoding="utf-8") as f:
                svg = f.read()
        self.assertTrue(svg.startswith("<svg xmlns"))
        self.assertIn('<rect class="bg"', svg)
        self.assertIn(".s1l{", svg)

    def test_report_text_is_escaped(self):
        page = ik.report_page("T", ["X"], "<b>bold</b>", "ok", "")
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", page)


if __name__ == "__main__":
    unittest.main()
