"""Shared test helpers: load a skill's script as a module, run it offline on fixtures."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "live")
TODAY = "2026-10-22"  # the day the fixtures were recorded

sys.path.insert(0, os.path.join(ROOT, "core"))
import investkit as ik  # noqa: E402,F401


def load(skill, module):
    path = os.path.join(ROOT, "skills", skill, module + ".py")
    spec = importlib.util.spec_from_file_location(module, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Offline(object):
    """Point every fetch at a fixture folder, pin today, and use a throwaway investor/ folder."""

    def __init__(self, fixtures=FIXTURES, today=TODAY):
        self.fixtures, self.today = fixtures, today

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="investor-test-")
        self.saved = {k: os.environ.get(k) for k in ("INVESTOR_OFFLINE", "INVESTOR_TODAY", "INVESTOR_DIR", "INVESTOR_RECORD")}
        os.environ["INVESTOR_OFFLINE"] = self.fixtures
        os.environ["INVESTOR_TODAY"] = self.today
        os.environ["INVESTOR_DIR"] = os.path.join(self.tmp, "investor")
        os.environ.pop("INVESTOR_RECORD", None)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def section(self, ticker, name):
        with open(os.path.join(os.environ["INVESTOR_DIR"], "%s.json" % ticker), encoding="utf-8") as f:
            return json.load(f)[name]


def run(main, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(argv)
    return code, out.getvalue()
