#!/usr/bin/env python3
"""Investor Skills setup: check everything is ready, and download the market data in advance.

  python setup.py            # the checklist
  python setup.py warm       # download the whole-market data now (1-2 minutes), so the first scan is instant
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import investkit as ik  # noqa: E402

SKILLS = ["the-scout", "the-judge", "the-mind-reader", "the-sniper", "the-time-traveller"]


def installed():
    """The other skills that sit next to this one (they do in both a plugin and a copied install)."""
    parent = os.path.dirname(HERE)
    return [s for s in SKILLS if os.path.isfile(os.path.join(parent, s, "SKILL.md"))]


def warm():
    parent = os.path.dirname(HERE)
    jobs = []
    if os.path.isfile(os.path.join(parent, "the-scout", "scanner.py")):
        jobs.append(("whole-market data for The Scout",
                     [sys.executable, os.path.join(parent, "the-scout", "scanner.py"), "--no-write", "--no-headlines"]))
    if os.path.isfile(os.path.join(parent, "the-mind-reader", "pricedin.py")):
        jobs.append(("growth base rates for The Mind Reader",
                     [sys.executable, os.path.join(parent, "the-mind-reader", "pricedin.py"), "AAPL", "--no-write"]))
    if not jobs:
        print("No skills found next to this one to warm up.")
        return 1
    for label, cmd in jobs:
        t0 = time.time()
        print("Downloading %s..." % label, flush=True)
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print("  failed: %s" % res.stderr.decode("utf-8", "replace").strip().splitlines()[-1:])
            return 1
        print("  done in %d seconds" % (time.time() - t0))
    print("Ready: later scans read this data from %s and take seconds." % ik.cache_dir())
    return 0


def main(argv=None):
    ik.setup_console()
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["warm"]:
        return warm()
    good, lines = ik.check()
    print("\n".join(lines))
    found = installed()
    print("OK   Skills installed: %s" % ", ".join(found) if found else "FIX  No other Investor Skills found next to this one")
    print("\nReady." if good and found else "\nFix the lines marked FIX, then run this again.")
    return 0 if good and found else 1


if __name__ == "__main__":
    sys.exit(main())
