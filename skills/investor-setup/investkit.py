"""investkit: the shared core of the Investor skills.

One file, standard library only, Python 3.8+. Every skill folder carries an
identical copy (tools/sync_core.py copies it from core/), so each skill folder
works on its own when copied into ~/.claude/skills/.

What lives here:
  * fetching with a polite cache: Yahoo Finance charts and quotes, SEC EDGAR
    (company facts, submissions, frames, full-text search)
  * reading XBRL company facts: annual series, trailing twelve months,
    latest balance-sheet values, with the filing each number came from
  * valuation math: two-stage DCF, implied growth, IRR, revenue-mode DCF
  * the stationary block bootstrap used by the Monte Carlo tools
  * small statistics, formatting and SVG helpers
  * the per-ticker results file investor/<TICKER>.json that chains the skills

Offline and recording, for tests and reproducible demos:
  INVESTOR_OFFLINE=<dir>  read every URL from <dir> instead of the network
  INVESTOR_RECORD=<dir>   also write every fetched response into <dir>
"""

import datetime
import gzip
import hashlib
import html
import http.cookiejar
import io
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"

DISCLAIMER = ("Education and research tooling, not financial advice. "
              "Numbers come from public filings and free market data, which can lag or be wrong.")

GZIP_MAGIC = bytes([0x1F, 0x8B])

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def setup_console():
    """Windows consoles default to a legacy code page; print UTF-8 instead."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


class InvestError(Exception):
    """An error with a message meant for the user."""


# ---------------------------------------------------------------- paths/config

def today():
    """Today's date, or INVESTOR_TODAY (YYYY-MM-DD) to replay a past day exactly."""
    pinned = os.environ.get("INVESTOR_TODAY")
    if pinned:
        return datetime.date(int(pinned[:4]), int(pinned[5:7]), int(pinned[8:10]))
    return datetime.date.today()


def data_dir():
    """Where results, charts and reports go: investor/ in the folder you work in."""
    return os.environ.get("INVESTOR_DIR") or "investor"


def home_dir():
    """Settings and downloaded data shared by every folder: ~/.investor."""
    return os.environ.get("INVESTOR_HOME") or os.path.join(os.path.expanduser("~"), ".investor")


def cache_dir():
    if os.environ.get("INVESTOR_CACHE"):
        return os.environ["INVESTOR_CACHE"]
    if os.environ.get("INVESTOR_DIR"):
        return os.path.join(data_dir(), "cache")
    return os.path.join(home_dir(), "cache")


def _read_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_config():
    """Settings from ~/.investor/config.json, overridden by investor/config.json in the current folder."""
    cfg = _read_json(os.path.join(home_dir(), "config.json"))
    cfg.update(_read_json(os.path.join(data_dir(), "config.json")))
    return cfg


def save_contact(name, email):
    """Save the SEC contact once, for every folder. Returns the file path."""
    name, email = name.strip(), email.strip()
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", email):
        raise InvestError("Give a name and a real email address, e.g. save-contact \"Jane Doe\" jane@mail.com")
    path = os.path.join(home_dir(), "config.json")
    cfg = _read_json(path)
    cfg["sec_user_agent"] = "%s %s" % (name, email)
    os.makedirs(home_dir(), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return path


def sec_user_agent():
    ua = os.environ.get("SEC_USER_AGENT") or load_config().get("sec_user_agent", "")
    if not ua or "@" not in ua or "example.com" in ua:
        raise InvestError(
            "SEC EDGAR needs a name and contact email on every request (its fair-access rule).\n"
            "Ask the user for theirs, then run:  python investkit.py save-contact \"Their Name\" their@email.com\n"
            "(investkit.py is in this skill's folder). It is saved once, in %s, for every folder."
            % os.path.join(home_dir(), "config.json"))
    return ua


# ---------------------------------------------------------------- fetching

_last_sec_call = [0.0]
_yahoo = {"opener": None, "crumb": None}


def url_key(url):
    """A stable, readable file name for a URL (used by the cache and fixtures)."""
    url = re.sub(r"[?&]crumb=[^&]*", "", url)
    parsed = urllib.parse.urlparse(url)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", parsed.netloc.split(".")[0] + parsed.path + "-" + parsed.query)
    slug = slug.strip("-")[:90]
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    return "%s-%s" % (slug, digest)


def _read_response(resp):
    body = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == GZIP_MAGIC:
        body = gzip.decompress(body)
    return body


def _http(url, headers, opener=None, tries=4):
    req = urllib.request.Request(url, headers=headers)
    delay = 1.0
    for attempt in range(tries):
        try:
            if opener:
                resp = opener.open(req, timeout=60)
            else:
                resp = urllib.request.urlopen(req, timeout=60)
            with resp:
                return _read_response(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            if e.code == 404:
                raise InvestError("Not found (404): %s" % url)
            if e.code == 403 and "sec.gov" in url:
                raise InvestError("SEC refused the request (403). Check sec_user_agent has a real name and email.")
            raise InvestError("HTTP %s from %s" % (e.code, url))
        except (urllib.error.URLError, OSError) as e:
            if attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise InvestError("Network error for %s: %s" % (url, e))


def fetch(url, kind="other", ttl_hours=12.0, key=None):
    """GET a URL with caching. kind is 'sec', 'yahoo' or 'other'. Returns bytes.
    `key` names the cache and fixture file when the URL itself changes daily."""
    key = key or url_key(url)
    offline = os.environ.get("INVESTOR_OFFLINE")
    if offline:
        path = os.path.join(offline, key)
        if not os.path.exists(path):
            raise InvestError("offline: no fixture for %s (%s)" % (url, key))
        with open(path, "rb") as f:
            body = f.read()
        return gzip.decompress(body) if body[:2] == GZIP_MAGIC else body

    cpath = os.path.join(cache_dir(), key)
    if ttl_hours and os.path.exists(cpath) and time.time() - os.path.getmtime(cpath) < ttl_hours * 3600:
        with open(cpath, "rb") as f:
            body = f.read()
    else:
        if kind == "sec":
            wait = 0.12 - (time.time() - _last_sec_call[0])  # SEC asks for <= 10 requests a second
            if wait > 0:
                time.sleep(wait)
            _last_sec_call[0] = time.time()
            body = _http(url, {"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip"})
        elif kind == "yahoo":
            body = _http(url, {"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"}, opener=_yahoo["opener"])
        else:
            body = _http(url, {"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"})
        if ttl_hours:
            os.makedirs(cache_dir(), exist_ok=True)
            with open(cpath, "wb") as f:
                f.write(body)

    record = os.environ.get("INVESTOR_RECORD")
    if record:
        os.makedirs(record, exist_ok=True)
        with open(os.path.join(record, key), "wb") as f:
            f.write(body)
    return body


def fetch_json(url, kind="other", ttl_hours=12.0, key=None):
    body = fetch(url, kind, ttl_hours, key)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise InvestError("Expected JSON from %s" % url)


# ---------------------------------------------------------------- Yahoo

def yahoo_symbol(ticker):
    """BRK.B -> BRK-B (US share classes), but 005930.KS, SU.PA, 7203.T stay as they are."""
    t = ticker.strip().upper()
    base, dot, suffix = t.rpartition(".")
    if dot and base.isalpha() and len(suffix) == 1 and suffix in "ABCDE":
        return base + "-" + suffix
    return t


def yahoo_chart(ticker, rng="max", interval="1mo", ttl_hours=6.0):
    """Price history. Returns dict: dates (YYYY-MM-DD), open, high, low, close,
    adjclose (dividends and splits folded in), volume, meta. OHLC are split
    adjusted, not dividend adjusted. Rows with a missing close are dropped."""
    sym = yahoo_symbol(ticker)
    if rng == "max" or rng.endswith("y"):
        # range=max (and long ranges) silently downgrade to coarser bars; explicit
        # dates keep the interval asked for. period2 = end of today (UTC) so the
        # URL, and the cache key, stays the same all day.
        end = (int((today() - datetime.date(1970, 1, 1)).days) + 1) * 86400
        start = 0 if rng == "max" else end - int(float(rng[:-1]) * 365.25 * 86400)
        span = "period1=%d&period2=%d" % (start, end)
    else:
        span = "range=%s" % rng
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/%s?%s&interval=%s&events=div%%2Csplit"
           % (urllib.parse.quote(sym), span, interval))
    key = url_key("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s"
                  % (urllib.parse.quote(sym), rng, interval))
    data = fetch_json(url, "yahoo", ttl_hours, key)
    chart = data.get("chart") or {}
    if chart.get("error") or not chart.get("result"):
        raise InvestError("Yahoo has no chart for %s (%s)" % (ticker, (chart.get("error") or {}).get("description", "no data")))
    res = chart["result"][0]
    ts = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
    meta = res.get("meta", {})
    offset = meta.get("gmtoffset", 0) or 0
    out = {k: [] for k in ("dates", "open", "high", "low", "close", "adjclose", "volume")}
    for i, t in enumerate(ts):
        c = (quote.get("close") or [None] * len(ts))[i]
        if c is None:
            continue
        d = _utc_date(t + offset)
        o = (quote.get("open") or [None] * len(ts))[i]
        h = (quote.get("high") or [None] * len(ts))[i]
        lo = (quote.get("low") or [None] * len(ts))[i]
        v = (quote.get("volume") or [None] * len(ts))[i]
        a = adj[i] if adj and i < len(adj) and adj[i] is not None else c
        out["dates"].append(d)
        out["open"].append(o if o is not None else c)
        out["high"].append(max(h, c) if h is not None else c)
        out["low"].append(min(lo, c) if lo is not None else c)
        out["close"].append(c)
        out["adjclose"].append(a)
        out["volume"].append(v or 0)
    # Yahoo sometimes repeats the current period as a partial row with the same date
    if len(out["dates"]) >= 2 and out["dates"][-1] == out["dates"][-2]:
        for k in out:
            del out[k][-2]
    # daily history often stops a session short of the live quote; add that session
    if interval == "1d" and out["dates"] and meta.get("regularMarketTime") and meta.get("regularMarketPrice"):
        last = _utc_date(meta["regularMarketTime"] + offset)
        if last > out["dates"][-1]:
            p = meta["regularMarketPrice"]
            out["dates"].append(last)
            out["open"].append(p)
            out["high"].append(max(p, meta.get("regularMarketDayHigh") or p))
            out["low"].append(min(p, meta.get("regularMarketDayLow") or p))
            out["close"].append(p)
            ratio = out["adjclose"][-1] / out["close"][-2] if out["close"][-2] else 1.0
            out["adjclose"].append(p * ratio)
            out["volume"].append(meta.get("regularMarketVolume") or 0)
    out["meta"] = meta
    splits = []
    for ev in ((res.get("events") or {}).get("splits") or {}).values():
        if ev.get("numerator") and ev.get("denominator"):
            splits.append((_utc_date(ev["date"] + offset), ev["numerator"] / float(ev["denominator"])))
    out["splits"] = sorted(splits)
    if not out["dates"]:
        raise InvestError("Yahoo returned no prices for %s" % ticker)
    return out


def _utc_date(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


def _yahoo_session():
    if _yahoo["crumb"]:
        return _yahoo["crumb"]
    jar = http.cookiejar.CookieJar()
    _yahoo["opener"] = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        _yahoo["opener"].open(urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": BROWSER_UA}), timeout=20)
    except Exception:
        pass  # fc.yahoo.com answers 404 but sets the cookie
    crumb = _http("https://query2.finance.yahoo.com/v1/test/getcrumb", {"User-Agent": BROWSER_UA}, opener=_yahoo["opener"])
    _yahoo["crumb"] = crumb.decode().strip()
    return _yahoo["crumb"]


def _yahoo_crumb_json(base, ttl_hours):
    """JSON from a Yahoo endpoint that needs the cookie + crumb, cached under the URL without the crumb."""
    if os.environ.get("INVESTOR_OFFLINE"):
        return fetch_json(base, "yahoo", ttl_hours)
    key_path = os.path.join(cache_dir(), url_key(base))
    if os.path.exists(key_path) and time.time() - os.path.getmtime(key_path) < ttl_hours * 3600:
        return fetch_json(base, "yahoo", ttl_hours)
    crumb = _yahoo_session()
    body = _http(base + "&crumb=" + urllib.parse.quote(crumb), {"User-Agent": BROWSER_UA}, opener=_yahoo["opener"])
    os.makedirs(cache_dir(), exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(body)
    record = os.environ.get("INVESTOR_RECORD")
    if record:
        os.makedirs(record, exist_ok=True)
        with open(os.path.join(record, url_key(base)), "wb") as f:
            f.write(body)
    return json.loads(body.decode("utf-8"))


def yahoo_quote(tickers, ttl_hours=1.0):
    """Quote snapshot (price, marketCap, sharesOutstanding, currency, financialCurrency, ...).
    Returns {TICKER: dict}. Needs Yahoo's cookie + crumb; returns {} if that fails."""
    syms = ",".join(yahoo_symbol(t) for t in tickers)
    base = "https://query2.finance.yahoo.com/v7/finance/quote?symbols=%s" % urllib.parse.quote(syms, safe=",")
    try:
        data = _yahoo_crumb_json(base, ttl_hours)
    except (InvestError, ValueError):
        return {}
    out = {}
    for q in (data.get("quoteResponse") or {}).get("result") or []:
        out[q.get("symbol", "")] = q
    for t in tickers:
        if yahoo_symbol(t) in out:
            out[t.strip().upper()] = out[yahoo_symbol(t)]
    return out


SUMMARY_MODULES = "summaryDetail,defaultKeyStatistics,financialData,earningsTrend,earningsHistory,calendarEvents"


def yahoo_summary(ticker, modules=SUMMARY_MODULES, ttl_hours=6.0):
    """Yahoo's per-company summary: consensus estimates (earningsTrend), beats (earningsHistory),
    the next earnings date (calendarEvents) and key statistics. Values are unwrapped from
    Yahoo's {"raw": x, "fmt": ...} form. Returns {module: dict}, or {} if Yahoo refuses."""
    base = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s" % (
        urllib.parse.quote(yahoo_symbol(ticker)), modules)
    try:
        data = _yahoo_crumb_json(base, ttl_hours)
    except (InvestError, ValueError):
        return {}
    res = ((data.get("quoteSummary") or {}).get("result") or [None])[0]
    return _unwrap(res) if res else {}


def _unwrap(x):
    if isinstance(x, dict):
        if "raw" in x and set(x) <= {"raw", "fmt", "longFmt"}:
            return x["raw"]
        if not x or set(x) <= {"fmt", "longFmt"}:
            return None
        return {k: _unwrap(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_unwrap(v) for v in x]
    return x


def yahoo_quotes(tickers, chunk=400):
    """yahoo_quote for any number of tickers, 400 to a request."""
    out = {}
    tickers = list(tickers)
    for i in range(0, len(tickers), chunk):
        out.update(yahoo_quote(tickers[i:i + chunk]))
    return out


def yahoo_headlines(ticker, days=14):
    """Recent Yahoo Finance headlines for a ticker: list of {date, title, link}, newest first.
    Yahoo's feed for a ticker also carries stories about other companies."""
    import email.utils
    import xml.etree.ElementTree as ET
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US" % urllib.parse.quote(yahoo_symbol(ticker))
    try:
        root = ET.fromstring(fetch(url, "other", ttl_hours=2))
    except (InvestError, ET.ParseError):
        return []
    since = today() - datetime.timedelta(days=days)
    out = []
    for item in root.iter("item"):
        try:
            when = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "").date()
        except (TypeError, ValueError):
            continue
        if when >= since:
            out.append({"date": when.isoformat(), "title": (item.findtext("title") or "").strip(),
                        "link": (item.findtext("link") or "").strip()})
    return sorted(out, key=lambda h: h["date"], reverse=True)


def quote_market_cap_usd(q):
    """A Yahoo quote's market cap in US dollars (Yahoo reports it in the listing's currency)."""
    mc = q.get("marketCap") if q else None
    if not mc:
        return None
    cur = q.get("currency") or "USD"
    minor = {"GBp": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}
    if cur in minor:
        cur, div = minor[cur]
        mc = mc / div
    try:
        return mc * fx_to_usd(cur)
    except InvestError:
        return None


def fx_to_usd(currency):
    """USD per one unit of `currency`, from Yahoo's FX chart. 1.0 for USD."""
    if not currency or currency.upper() == "USD":
        return 1.0
    ch = yahoo_chart("%sUSD=X" % currency.upper(), "5d", "1d", ttl_hours=12)
    return ch["close"][-1]


# ---------------------------------------------------------------- SEC EDGAR

def sec_tickers():
    return fetch_json("https://www.sec.gov/files/company_tickers.json", "sec", ttl_hours=24 * 7)


def sec_lookup(ticker):
    """(cik, title) for a ticker, via SEC's ticker file. BRK.B and BRK-B both work."""
    want = ticker.strip().upper().replace(".", "-")
    for row in sec_tickers().values():
        if row["ticker"].upper().replace(".", "-") == want:
            return int(row["cik_str"]), row["title"]
    raise InvestError("%s is not in SEC's ticker list (ETFs, funds and some foreign listings are not)." % ticker)


def sec_facts(cik):
    return fetch_json("https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json" % cik, "sec", ttl_hours=24)


def sec_submissions(cik):
    return fetch_json("https://data.sec.gov/submissions/CIK%010d.json" % cik, "sec", ttl_hours=24)


def sec_frame(tag, period, taxonomy="us-gaap", unit="USD", ttl_hours=24 * 30):
    url = "https://data.sec.gov/api/xbrl/frames/%s/%s/%s/%s.json" % (taxonomy, tag, unit, period)
    try:
        return fetch_json(url, "sec", ttl_hours=ttl_hours)
    except InvestError as e:
        if "404" in str(e):
            return {"data": []}
        raise


def sec_fulltext(query, forms="10-K", start=None, end=None):
    params = {"q": query, "forms": forms}
    if start and end:
        params.update({"dateRange": "custom", "startdt": start, "enddt": end})
    url = "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(params)
    return fetch_json(url, "sec", ttl_hours=24)


def sec_fulltext_hits(phrase, forms="8-K", start=None, end=None, max_pages=5, ttl_hours=12, cik=None):
    """Every filing (up to max_pages x 100) whose text contains the exact phrase, from one
    company when `cik` is given. Returns dicts: cik, name, form, filed, items, sic, url."""
    out, frm = [], 0
    for _ in range(max_pages):
        params = {"q": '"%s"' % phrase.strip('"'), "forms": forms}
        if cik:
            params["ciks"] = "%010d" % int(cik)
        if start and end:
            params.update({"dateRange": "custom", "startdt": start, "enddt": end})
        if frm:
            params["from"] = frm
        data = fetch_json("https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(params), "sec", ttl_hours)
        hits = (data.get("hits") or {}).get("hits") or []
        total = ((data.get("hits") or {}).get("total") or {}).get("value", 0)
        for h in hits:
            src = h.get("_source", {})
            adsh, _, doc = h.get("_id", ":").partition(":")
            for i, cik in enumerate(src.get("ciks") or []):
                out.append({"cik": int(cik), "name": re.sub(r"\s+\(.*$", "", (src.get("display_names") or [""])[i] if i < len(src.get("display_names") or []) else "").strip(),
                            "form": src.get("form"), "filed": src.get("file_date"), "items": src.get("items") or [],
                            "sic": (src.get("sics") or [""])[0], "url": filing_index_url(cik, adsh) + doc,
                            "doc_type": (src.get("file_type") or "").upper()})
        frm += len(hits)
        if not hits or frm >= total:
            break
    return out


def sec_frame_map(tags, period, unit="USD", taxonomy="us-gaap", ttl_hours=24 * 30, largest=False):
    """{cik: value} for one period across every filer, from the first tag in `tags`
    that each company reports (TAGS aliases accepted), or the largest when largest=True.
    Periods: CY2025, CY2026Q2, CY2025Q4I."""
    out = {}
    for tag in _tags(tags):
        tax = "dei" if tag.startswith("Entity") else taxonomy
        frame = sec_frame(tag, period, tax, unit, ttl_hours=ttl_hours)
        for row in frame.get("data", []):
            v = row.get("val")
            if v is None:
                continue
            if row["cik"] not in out or (largest and v > out[row["cik"]]):
                out[row["cik"]] = v
    return out


# Phrases companies use in 8-Ks. Catalysts: kind, label, strength, phrases.
CATALYSTS = [
    ("guidance_up", "Raised guidance", 3, ["raises full-year guidance", "raised full-year guidance", "raises its full-year",
                                           "raised its full-year", "raising full-year guidance", "raising its full-year",
                                           "increases full-year guidance", "increased its full-year", "raises full year",
                                           "raised full year", "raises its outlook", "raised its outlook", "raising its outlook",
                                           "raises annual guidance", "raised its annual"]),
    ("record", "Record results or backlog", 2, ["record backlog", "record revenue", "record quarterly revenue",
                                               "record orders", "record bookings"]),
    ("buyback", "New or larger buyback", 1, ["new share repurchase program", "new stock repurchase program",
                                            "increase to its share repurchase", "increase in its share repurchase",
                                            "additional share repurchase", "increased its share repurchase"]),
    ("dividend", "Dividend raised", 1, ["increased its quarterly dividend", "increases quarterly dividend",
                                       "raises quarterly dividend", "raised its quarterly dividend",
                                       "increase in the quarterly dividend", "increase in its quarterly dividend"]),
    ("contract", "Major contract", 1, ["awarded a contract", "contract award valued"]),
]
RED_FLAGS = [
    # kind, label, hard (removes the name by default), phrases, item code that must be on the filing (or None)
    ("restatement", "Restatement warning", True, ["Non-Reliance on Previously Issued"], "4.02"),
    ("going_concern", "Going-concern doubt", True, ["substantial doubt about our ability to continue as a going concern",
                                                    "substantial doubt about the Company's ability to continue as a going concern",
                                                    "substantial doubt about its ability to continue as a going concern"], None),
    ("bankruptcy", "Bankruptcy filing", True, ["Bankruptcy or Receivership"], "1.03"),
    ("listing", "Listing deficiency notice", False, ["not in compliance with", "no longer in compliance with"], "3.01"),
    ("guidance_down", "Lowered guidance", False, ["lowers full-year guidance", "lowered full-year guidance",
                                                  "lowered its full-year", "reduces full-year guidance", "cuts full-year guidance",
                                                  "lowers its outlook", "lowered its outlook", "lowered full year"], None),
    ("auditor", "Auditor change", False, ["Changes in Registrant's Certifying Accountant"], "4.01"),
    ("impairment", "Material impairment", False, ["Material Impairments"], "2.06"),
    # wording that reports one, not risk-factor wording such as "could cause us to have material weaknesses"
    ("weakness", "Material weakness reported", False, ["identified a material weakness", "identified material weaknesses",
                                                       "material weakness was identified", "existence of a material weakness"],
     None),
]


def own_words(hit):
    """The 8-K itself or a press release (Exhibit 99), not contracts or other attached documents,
    whose boilerplate mentions going concern, delisting and the like."""
    t = hit.get("doc_type") or ""
    return t in ("", "8-K", "8-K/A") or t.startswith("EX-99")


def recent_8k_items(cik, days):
    """8-K filings of the last `days`: list of {filed, items, url}, newest first."""
    rec = sec_submissions(cik).get("filings", {}).get("recent", {})
    since = (today() - datetime.timedelta(days=days)).isoformat()
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if form in ("8-K", "8-K/A") and rec["filingDate"][i] >= since:
            out.append({"filed": rec["filingDate"][i], "accn": rec["accessionNumber"][i], "items": [x for x in (rec.get("items") or [""] * (i + 1))[i].split(",") if x],
                        "url": filing_index_url(cik, rec["accessionNumber"][i]) + rec["primaryDocument"][i]})
    return out


def filing_index_url(cik, accn):
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (int(cik), accn.replace("-", ""))


def latest_filing(cik, forms=("10-K",)):
    """The newest filing of one of `forms`: dict with form, accn, filed, url, doc_url."""
    subs = sec_submissions(cik)
    rec = subs.get("filings", {}).get("recent", {})
    for i, form in enumerate(rec.get("form", [])):
        if form in forms:
            accn = rec["accessionNumber"][i]
            doc = rec["primaryDocument"][i]
            return {"form": form, "accn": accn, "filed": rec["filingDate"][i],
                    "url": filing_index_url(cik, accn),
                    "doc_url": filing_index_url(cik, accn) + doc}
    return None


def annual_report_text(cik):
    """Plain text of the latest annual report (10-K, 20-F or 40-F) and its filing record."""
    f = latest_filing(cik, ("10-K", "20-F", "40-F", "10-K/A"))
    if not f:
        return None, None
    return html_text(decode_filing(fetch(f["doc_url"], "sec", ttl_hours=24 * 30))), f


def filing_exhibit(cik, accn, prefix="EX-99"):
    """The first exhibit of a filing whose type starts with `prefix` (EX-99.1 is the press release
    of an earnings 8-K): (plain text, url), or (None, None)."""
    index = filing_index_url(cik, accn) + "%s-index.htm" % accn
    page = fetch(index, "sec", ttl_hours=24 * 30).decode("utf-8", "replace")
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)
        link = re.search(r'(?i)href="([^"]+)"', row)
        if link and any(re.sub(r"<[^>]+>|\s", "", c).upper().startswith(prefix) for c in cells):
            href = link.group(1).replace("/ix?doc=", "")
            url = href if href.startswith("http") else "https://www.sec.gov" + href
            return html_text(decode_filing(fetch(url, "sec", ttl_hours=24 * 30))), url
    return None, None


def decode_filing(body):
    """Filings are UTF-8, or Windows-1252 when older tools wrote them (curly quotes, bullets)."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", "replace")


def html_text(raw):
    """Readable plain text from a filing's HTML: block tags become line breaks."""
    raw = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"\s+", " ", raw)  # line breaks in the HTML source mean nothing; block tags do
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text


# ---------------------------------------------------------------- XBRL facts

ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F", "40-F/A")

TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet",
                "RevenuesNetOfInterestExpense", "Revenue"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
             "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization", "CostOfSales"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
                      "ProfitLossBeforeTax"],
    "income_tax": ["IncomeTaxExpenseBenefit", "IncomeTaxExpenseContinuingOperations"],
    "net_income": ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents"],
    "short_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "DebtSecuritiesCurrent",
                          "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "CurrentFinancialAssetsAtFairValueThroughProfitOrLoss"],
    "debt_total": ["LongTermDebt", "DebtAndCapitalLeaseObligations",
                   "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                   "DebtLongtermAndShorttermCombinedAmount", "DebtInstrumentCarryingAmount"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "short_borrowings": ["CommercialPaper", "ShortTermBorrowings"],
    "dna": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization", "Depreciation", "DepreciationAndAmortisationExpense",
            "DepreciationPropertyPlantAndEquipment"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "EquityAttributableToOwnersOfParent", "Equity"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic",
                    "DilutedEarningsLossPerShare"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "DividendsPaid"],
    "shares_out": ["EntityCommonStockSharesOutstanding"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic",
                       "AdjustedWeightedAverageShares", "WeightedAverageShares"],
}


def _tags(key):
    """A concept name from TAGS, a single tag, or a list of alternative tags."""
    if isinstance(key, (list, tuple)):
        return list(key)
    return TAGS[key] if key in TAGS else [key]


def _parse_date(s):
    return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _days(start, end):
    return (_parse_date(end) - _parse_date(start)).days


def facts_unit_entries(facts, tag, prefer_units=("USD",)):
    """Entries for one tag across taxonomies, in the first unit available.
    Returns (entries, unit, taxonomy) or ([], None, None)."""
    for taxonomy in ("us-gaap", "ifrs-full", "dei"):
        node = facts.get("facts", {}).get(taxonomy, {}).get(tag)
        if not node:
            continue
        units = node.get("units", {})
        for u in prefer_units:
            if u in units:
                return units[u], u, taxonomy
        if units:
            u = sorted(units, key=lambda k: -len(units[k]))[0]
            return units[u], u, taxonomy
    return [], None, None


def reporting_currency(facts):
    """The currency most revenue-like facts are in (USD for US filers; EUR, TWD... for 20-F filers)."""
    for tag in TAGS["revenue"] + TAGS["cfo"]:
        for taxonomy in ("us-gaap", "ifrs-full"):
            node = facts.get("facts", {}).get(taxonomy, {}).get(tag)
            if node and node.get("units"):
                units = node["units"]
                return sorted(units, key=lambda k: -len(units[k]))[0]
    return "USD"


def annual_series(facts, key, currency=None):
    """Fiscal-year values for a concept: list of dicts {end, start, val, accn, form, tag},
    oldest first. Duration 350-380 days from annual reports, latest filing wins,
    and for each fiscal year the highest-priority tag that reports it."""
    tags = _tags(key)
    currency = currency or reporting_currency(facts)
    # Revenue: lessors (equipment rental, real estate) tag contract revenue without their rental
    # income, so when a company files several revenue tags the largest is the total.
    largest = key == "revenue"
    by_end = {}
    for prio, tag in enumerate(tags):
        entries, unit, _ = facts_unit_entries(facts, tag, (currency, "USD"))
        for e in entries:
            if "start" not in e or e.get("form") not in ANNUAL_FORMS:
                continue
            d = _days(e["start"], e["end"])
            if d < 350 or d > 380:
                continue
            cur = by_end.get(e["end"])
            cand = dict(end=e["end"], start=e["start"], val=e["val"], accn=e.get("accn"),
                        form=e.get("form"), filed=e.get("filed", ""), tag=tag, prio=prio, unit=unit)
            if cur is None:
                by_end[e["end"]] = cand
            elif cand["tag"] == cur["tag"]:
                if cand["filed"] > cur["filed"]:
                    by_end[e["end"]] = cand
            elif (largest and cand["val"] > cur["val"]) or (not largest and prio < cur["prio"]):
                by_end[e["end"]] = cand
    rows = sorted(by_end.values(), key=lambda r: r["end"])
    # drop years that end within 300 days of the next (52/53-week overlaps from mixed tags)
    clean = []
    for r in rows:
        if clean and _days(clean[-1]["end"], r["end"]) < 300:
            if r["prio"] <= clean[-1]["prio"]:
                clean[-1] = r
            continue
        clean.append(r)
    return clean


def ttm_value(facts, key, currency=None):
    """Trailing-twelve-month value: last fiscal year + this year's YTD - last year's YTD.
    Works for income-statement and cash-flow tags (10-Q cash flows are YTD only).
    Returns dict {val, end, method, sources:[accn...], tag} or None."""
    tags = _tags(key)
    currency = currency or reporting_currency(facts)
    annual = annual_series(facts, key, currency)
    if not annual:
        return None
    last = annual[-1]
    best = {"val": last["val"], "end": last["end"], "method": "fiscal year ending %s" % last["end"],
            "sources": [last["accn"]], "tag": last["tag"]}
    entries, _, _ = facts_unit_entries(facts, last["tag"], (currency, "USD"))
    if not entries:
        for tag in tags:
            entries, _, _ = facts_unit_entries(facts, tag, (currency, "USD"))
            if entries:
                break
    fy_end = _parse_date(last["end"])
    ytd = [e for e in entries if "start" in e and e.get("form") in ("10-Q", "10-Q/A", "6-K")
           and abs((_parse_date(e["start"]) - fy_end).days - 1) <= 10 and e["end"] > last["end"]]
    if not ytd:
        return best
    cur = max(ytd, key=lambda e: (e["end"], e.get("filed", "")))
    length = _days(cur["start"], cur["end"])
    want_end = _parse_date(cur["end"]) - datetime.timedelta(days=364)
    prior = [e for e in entries if "start" in e and abs(_days(e["start"], e["end"]) - length) <= 10
             and abs((_parse_date(e["end"]) - want_end).days) <= 10]
    if not prior:
        return best
    p = max(prior, key=lambda e: e.get("filed", ""))
    return {"val": last["val"] + cur["val"] - p["val"], "end": cur["end"],
            "method": "TTM to %s (FY %s + %d-day YTD - prior YTD)" % (cur["end"], last["end"][:4], length),
            "sources": [last["accn"], cur.get("accn"), p.get("accn")], "tag": last["tag"]}


def quarterly_series(facts, key, currency=None, units=None, derive_q4=True):
    """Fiscal-quarter values for a concept: list of dicts {start, end, val, accn, filed, first_filed,
    form, fy, fp, tag}, oldest first. Three-month figures as last filed (restatements win), with
    `first_filed` the day the quarter was first reported. The fourth quarter, which annual reports
    do not tag, is the fiscal year minus the nine months to the third quarter, both as first
    reported so the two share a basis (a stock split in between would otherwise mix them)."""
    tags = _tags(key)
    currency = currency or reporting_currency(facts)
    units = units or (currency, "USD")
    largest = key == "revenue"
    by_end = {}
    for prio, tag in enumerate(tags):
        entries, unit, _ = facts_unit_entries(facts, tag, units)
        quarters, first, years, nines = {}, {}, {}, {}
        for e in entries:
            if "start" not in e:
                continue
            d = _days(e["start"], e["end"])
            filed = e.get("filed", "")
            if 80 <= d <= 100:
                if e["end"] not in quarters or filed > quarters[e["end"]].get("filed", ""):
                    quarters[e["end"]] = e
                if e["end"] not in first or filed < first[e["end"]]:
                    first[e["end"]] = filed
            elif 350 <= d <= 380 or 260 <= d <= 285:
                bucket, k = (years, e["end"]) if d >= 350 else (nines, (e["start"], e["end"]))
                if k not in bucket or filed < bucket[k].get("filed", ""):
                    bucket[k] = e
        rows = {end: dict(start=e["start"], end=end, val=e["val"], accn=e.get("accn"), filed=e.get("filed", ""),
                          first_filed=first[end], form=e.get("form"), fy=e.get("fy"), fp=e.get("fp"), tag=tag, prio=prio)
                for end, e in quarters.items()}
        if derive_q4:
            for end, fy in years.items():
                if end in rows:
                    continue
                nine = [n for (s, _), n in nines.items() if s == fy["start"]]
                if nine:
                    n = max(nine, key=lambda x: x["end"])
                    rows[end] = dict(start=n["end"], end=end, val=fy["val"] - n["val"], accn=fy.get("accn"),
                                     filed=fy.get("filed", ""), first_filed=fy.get("filed", ""), form=fy.get("form"),
                                     fy=fy.get("fy"), fp="Q4", tag=tag, prio=prio, derived=True)
        for end, r in rows.items():
            cur = by_end.get(end)
            if cur is None or (largest and r["val"] > cur["val"]) or (not largest and prio < cur["prio"]):
                by_end[end] = r
    out = sorted(by_end.values(), key=lambda r: r["end"])
    clean = []
    for r in out:  # drop quarters ending within 60 days of the next (mixed tags, restated periods)
        if clean and _days(clean[-1]["end"], r["end"]) < 60:
            clean[-1] = r if r["prio"] < clean[-1]["prio"] else clean[-1]
            continue
        clean.append(r)
    return clean


def latest_instant(facts, key, currency=None, on_or_before=None):
    """Most recent balance-sheet (point in time) value: dict {val, end, accn, tag} or None."""
    tags = _tags(key)
    currency = currency or reporting_currency(facts)
    best = None
    for prio, tag in enumerate(tags):
        units = ("shares",) if key in ("shares_out",) else (currency, "USD")
        entries, _, _ = facts_unit_entries(facts, tag, units)
        for e in entries:
            if "start" in e:
                continue
            if on_or_before and e["end"] > on_or_before:
                continue
            cand = (e["end"], -prio, e.get("filed", ""))
            if best is None or cand > best[0]:
                best = (cand, {"val": e["val"], "end": e["end"], "accn": e.get("accn"), "tag": tag})
    return best[1] if best else None


def instant_series(facts, key, currency=None):
    """Year-end balance-sheet values from annual reports: list {end, val, accn, tag}, oldest first."""
    tags = _tags(key)
    currency = currency or reporting_currency(facts)
    by_end = {}
    for prio, tag in enumerate(tags):
        entries, _, _ = facts_unit_entries(facts, tag, (currency, "USD"))
        for e in entries:
            if "start" in e or e.get("form") not in ANNUAL_FORMS:
                continue
            cur = by_end.get(e["end"])
            if cur is None or prio < cur["prio"] or (prio == cur["prio"] and e.get("filed", "") > cur["filed"]):
                by_end[e["end"]] = {"end": e["end"], "val": e["val"], "accn": e.get("accn"), "tag": tag,
                                    "prio": prio, "filed": e.get("filed", "")}
    return sorted(by_end.values(), key=lambda r: r["end"])


def value_at(series, end, tolerance_days=20):
    """The value in a series whose end date is within tolerance of `end`."""
    for r in series:
        if abs((_parse_date(r["end"]) - _parse_date(end)).days) <= tolerance_days:
            return r["val"]
    return None


IFRS_DEBT = [["Borrowings"],
              ["NoncurrentPortionOfNoncurrentBondsIssued"],
              ["CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued"],
              ["LongtermBorrowings", "NoncurrentPortionOfNoncurrentBorrowings"],
              ["CurrentPortionOfLongtermBorrowings", "CurrentPortionOfNoncurrentBorrowings"],
              ["ShorttermBorrowings"]]


def _ifrs_debt(facts, currency):
    """IFRS filers split debt across bonds and borrowings; add the parts on one date."""
    if not facts.get("facts", {}).get("ifrs-full"):
        return None
    total = latest_instant(facts, "Borrowings", currency)
    found = [(group, latest_instant(facts, group, currency)) for group in IFRS_DEBT[1:]]
    found = [(g, x) for g, x in found if x]
    if not total and not found:
        return None
    newest = max([x["end"] for _, x in found] + ([total["end"]] if total else []))
    if total and total["end"] == newest:
        return {"val": total["val"], "end": newest, "tags": [total["tag"]]}
    parts = [x for _, x in found if x["end"] == newest]
    return {"val": sum(x["val"] for x in parts), "end": newest, "tags": [x["tag"] for x in parts]}


def total_debt(facts, currency=None):
    """Total financial debt at the latest balance sheet, and how it was built."""
    ifrs = _ifrs_debt(facts, currency)
    if ifrs:
        return ifrs
    parts = []
    total = latest_instant(facts, "debt_total", currency)
    nonc = latest_instant(facts, "debt_noncurrent", currency)
    curr = latest_instant(facts, "debt_current", currency)
    short = latest_instant(facts, "short_borrowings", currency)
    newest = max([x["end"] for x in (total, nonc, curr, short) if x] or [""])

    def fresh(x):
        return x if x and x["end"] == newest else None

    total, nonc, curr, short = fresh(total), fresh(nonc), fresh(curr), fresh(short)
    if nonc or curr:
        val = (nonc["val"] if nonc else 0) + (curr["val"] if curr else 0)
        parts = [p["tag"] for p in (nonc, curr) if p]
    elif total:
        val = total["val"]
        parts = [total["tag"]]
    else:
        val = 0
    if short and short["tag"] not in parts:
        val += short["val"]
        parts.append(short["tag"])
    return {"val": val, "end": newest or None, "tags": parts}


def net_cash(facts, currency=None):
    """Cash + short-term investments - debt, all from the latest balance sheet.
    A component whose newest value is older than the cash figure is left out
    (and listed in `stale`), so a renamed tag never smuggles in an old number."""
    cash = latest_instant(facts, "cash", currency)
    if not cash:
        return {"val": 0, "cash": 0, "investments": 0, "debt": 0, "end": None, "stale": ["cash"], "sources": []}
    asof = cash["end"]
    inv = latest_instant(facts, "short_investments", currency)
    stale = []
    if inv and inv["end"] != asof:
        stale.append("short-term investments (%s, last reported %s)" % (inv["tag"], inv["end"]))
        inv = None
    debt = total_debt(facts, currency)
    if debt["end"] and debt["end"] != asof:
        # debt rarely vanishes: keep the old figure (the cautious direction) and say so
        stale.append("debt: used the last reported figure, from %s" % debt["end"])
    val = cash["val"] + (inv["val"] if inv else 0) - debt["val"]
    return {"val": val, "cash": cash["val"], "investments": inv["val"] if inv else 0, "debt": debt["val"],
            "end": asof, "stale": stale, "sources": [cash["accn"]],
            "tags": [cash["tag"]] + ([inv["tag"]] if inv else []) + debt["tags"]}


def shares_outstanding(facts):
    """Latest share count and where it came from.
    1. the cover-page count (dei), summing classes reported on the same date;
    2. else diluted weighted-average shares of the latest quarter (multi-class
       companies such as Alphabet only tag the cover count per class, which the
       company-facts API leaves out)."""
    entries, _, _ = facts_unit_entries(facts, "EntityCommonStockSharesOutstanding", ("shares",))
    if entries and months_old(max(e["end"] for e in entries)) > 18:
        entries = []  # a stale cover count (e.g. one class reported years ago) is worse than none
    if entries:
        latest_end = max(e["end"] for e in entries)
        same = [e for e in entries if e["end"] == latest_end]
        accn = max(e.get("accn", "") for e in same)
        vals = sorted(set(e["val"] for e in same if e.get("accn", "") == accn))
        return {"val": sum(vals), "end": latest_end, "accn": accn,
                "source": "SEC cover page, %s" % latest_end}
    for tag in TAGS["shares_diluted"]:
        entries, _, _ = facts_unit_entries(facts, tag, ("shares",))
        durs = [e for e in entries if "start" in e and _days(e["start"], e["end"]) <= 100]
        if durs:
            e = max(durs, key=lambda x: (x["end"], x.get("filed", "")))
            return {"val": e["val"], "end": e["end"], "accn": e.get("accn"),
                    "source": "diluted weighted-average shares, quarter to %s" % e["end"]}
    return None


def months_old(end):
    if not end:
        return None
    return (today() - _parse_date(end)).days / 30.44


def cik_link(cik):
    return "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%010d&type=10-K" % int(cik)


# ---------------------------------------------------------------- valuation math

def dcf_value(base_fcf, growth, years, discount, terminal_growth):
    """Present value of base_fcf growing at `growth` for `years`, then a Gordon
    terminal value growing at `terminal_growth`. End-of-year discounting."""
    if discount <= terminal_growth:
        raise InvestError("discount rate must exceed terminal growth")
    pv = 0.0
    f = base_fcf
    for t in range(1, years + 1):
        f *= (1 + growth)
        pv += f / (1 + discount) ** t
    tv = f * (1 + terminal_growth) / (discount - terminal_growth)
    return pv + tv / (1 + discount) ** years


def revenue_path_flows(rev0, margin0, margin_target, growth, years, terminal_growth):
    """Cash flows when free-cash-flow margin moves linearly from margin0 to
    margin_target over `years` while revenue grows at `growth`.
    Returns (flows[1..years], terminal_fcf_at_year_years_plus_1)."""
    flows = []
    rev = rev0
    for t in range(1, years + 1):
        rev *= (1 + growth)
        m = margin0 + (margin_target - margin0) * t / years
        flows.append(rev * m)
    terminal_next = rev * (1 + terminal_growth) * margin_target
    return flows, terminal_next


def pv_flows(flows, terminal_next, discount, terminal_growth):
    pv = sum(f / (1 + discount) ** (t + 1) for t, f in enumerate(flows))
    tv = terminal_next / (discount - terminal_growth)
    return pv + tv / (1 + discount) ** len(flows)


def bisect(fn, lo, hi, target, tol=1e-7, iters=200):
    """Solve fn(x) = target for increasing fn on [lo, hi]. None if out of range."""
    flo, fhi = fn(lo), fn(hi)
    if flo > target or fhi < target:
        return None
    for _ in range(iters):
        mid = (lo + hi) / 2
        if fn(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


GROWTH_LO, GROWTH_HI = -0.5, 1.5


def implied_growth_fcf(enterprise_value, base_fcf, years, discount, terminal_growth):
    """The constant growth rate of free cash flow over `years` that the price implies."""
    if base_fcf <= 0:
        return None
    return bisect(lambda g: dcf_value(base_fcf, g, years, discount, terminal_growth),
                  GROWTH_LO, GROWTH_HI, enterprise_value)


def implied_growth_revenue(enterprise_value, rev0, margin0, margin_target, years, discount, terminal_growth):
    """Revenue growth implied when FCF margin converges to margin_target by year `years`."""
    def value(g):
        flows, tn = revenue_path_flows(rev0, margin0, margin_target, g, years, terminal_growth)
        return pv_flows(flows, tn, discount, terminal_growth)
    return bisect(value, GROWTH_LO, GROWTH_HI, enterprise_value)


def irr(cashflows, lo=-0.95, hi=3.0):
    """Internal rate of return of cashflows[0..n] (cashflows[0] is the outlay, negative)."""
    def npv(r):
        return sum(c / (1 + r) ** t for t, c in enumerate(cashflows))
    # npv is decreasing in r for a conventional outlay-then-inflows stream
    a, b = lo, hi
    fa, fb = npv(a), npv(b)
    if fa * fb > 0:
        return None
    for _ in range(300):
        m = (a + b) / 2
        fm = npv(m)
        if fa * fm <= 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
        if b - a < 1e-9:
            break
    return (a + b) / 2


def scenario_return(enterprise_value, base, growth, years, discount, terminal_growth,
                    mode="fcf", margin0=None, margin_target=None):
    """Annual return from paying `enterprise_value` today if the business grows at
    `growth` for `years` and is then worth a terminal multiple consistent with
    `discount`. At the priced-in growth rate this returns exactly `discount`."""
    if mode == "fcf":
        flows = []
        f = base
        for _ in range(years):
            f *= (1 + growth)
            flows.append(f)
        terminal_next = f * (1 + terminal_growth)
    else:
        flows, terminal_next = revenue_path_flows(base, margin0, margin_target, growth, years, terminal_growth)
    tv = terminal_next / (discount - terminal_growth)
    cfs = [-enterprise_value] + flows[:-1] + [flows[-1] + tv]
    return irr(cfs)


# ---------------------------------------------------------------- statistics

def percentile(sorted_vals, p):
    """Linear-interpolated percentile (p in 0..100) of an already sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(math.floor(k))
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def cagr(first, last, years):
    if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1


def log_returns(prices):
    return [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))
            if prices[i] > 0 and prices[i - 1] > 0]


def stationary_bootstrap(n_source, length, mean_block, rng):
    """Politis-Romano stationary bootstrap: indices into a source series of
    n_source returns. Blocks have geometric lengths with mean `mean_block`, and
    wrap around the end, so crashes and rallies keep their clustering."""
    p = 1.0 / mean_block
    idx = []
    i = rng.randrange(n_source)
    for _ in range(length):
        idx.append(i)
        if rng.random() < p:
            i = rng.randrange(n_source)
        else:
            i = (i + 1) % n_source
    return idx


def max_drawdown(values):
    peak = values[0]
    worst = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = v / peak - 1
            if dd < worst:
                worst = dd
    return worst


# ---------------------------------------------------------------- formatting

def money(x, decimals=1):
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    a = abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return "%s$%.*f%s" % (sign, decimals, a / div, suf)
    if a >= 1000:
        return "%s$%s" % (sign, "{:,.0f}".format(a))
    if decimals == 0 and a == int(a):
        return "%s$%d" % (sign, a)
    return "%s$%.2f" % (sign, a)


def pct(x, decimals=1, signed=False):
    if x is None:
        return "n/a"
    return ("%+.*f%%" if signed else "%.*f%%") % (decimals, x * 100)


def table(rows, headers=None, align=None):
    """Plain-text table. rows: list of lists of strings."""
    allrows = ([headers] if headers else []) + rows
    widths = [max(len(str(r[i])) for r in allrows) for i in range(len(allrows[0]))]
    align = align or ["<"] + [">"] * (len(widths) - 1)
    out = []
    for j, r in enumerate(allrows):
        out.append("  ".join(("{:%s%d}" % (align[i], widths[i])).format(str(c)) for i, c in enumerate(r)).rstrip())
        if headers and j == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


# ---------------------------------------------------------------- results file

def ticker_path(ticker):
    return os.path.join(data_dir(), "%s.json" % ticker.upper().replace("/", "-"))


def load_ticker(ticker):
    path = ticker_path(ticker)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"ticker": ticker.upper()}


def save_section(ticker, section, payload):
    doc = load_ticker(ticker)
    payload = dict(payload)
    payload["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload["version"] = VERSION
    doc[section] = payload
    os.makedirs(data_dir(), exist_ok=True)
    with open(ticker_path(ticker), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return ticker_path(ticker)


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------- charts (SVG)
#
# Charts are drawn once, with class names instead of colours. A standalone .svg
# (for video) carries the dark style below; a chart inside an HTML report takes
# its colours from the page's light/dark tokens (REPORT_CSS). Text never wears a
# series colour: labels use ink tokens, a coloured key mark sits beside them.

SVG_STYLE = """<style>
  .bg{fill:#141413} .grid{stroke:#2c2c2a;stroke-width:1} .base{stroke:#4a4a46;stroke-width:1}
  .title{fill:#f0efec;font:600 22px system-ui,Segoe UI,Arial} .sub{fill:#c3c2b7;font:14px system-ui,Segoe UI,Arial}
  .axis{fill:#a3a29b;font:12px system-ui,Segoe UI,Arial} .lbl{fill:#f0efec;font:600 13px system-ui,Segoe UI,Arial}
  .note{fill:#a3a29b;font:11.5px system-ui,Segoe UI,Arial} .big{fill:#f0efec;font:700 26px system-ui,Segoe UI,Arial}
  .bar{fill:#4a4a46} .s1{fill:#3987e5} .s2{fill:#d95926} .s3{fill:#199e70} .s4{fill:#c98500} .mut{fill:#898781}
  .s1l{stroke:#3987e5;stroke-width:2.5;fill:none;stroke-linejoin:round;stroke-linecap:round}
  .s2l{stroke:#d95926;stroke-width:2.5;fill:none} .s4l{stroke:#c98500;stroke-width:2;fill:none;stroke-dasharray:8 6}
  .mutl{stroke:#898781;stroke-width:2;fill:none} .inkl{stroke:#f0efec;stroke-width:2;fill:none;stroke-linejoin:round}
  .refl{stroke:#898781;stroke-width:1.5;fill:none;stroke-dasharray:6 5}
  .wash1{fill:#3987e5;fill-opacity:.16} .wash2{fill:#3987e5;fill-opacity:.32}
  .zone{fill:#199e70} .ceil{fill:#e66767;fill-opacity:.24} .ring{stroke:#141413;stroke-width:2}
  .spag{stroke:#3987e5;stroke-opacity:.2;stroke-width:1;fill:none} .typl{stroke:#f0efec;stroke-width:3;fill:none}
  .todayl{stroke:#6a6a64;stroke-width:1;stroke-dasharray:3 4} .hit{fill:none}
  .box{fill:#3987e5} .boxl{stroke:#5a5a55;stroke-width:1;fill:none} .chokel{stroke:#c98500;stroke-width:4;fill:none}
  .good{fill:#0ca30c} .warn{fill:#fab219} .crit{fill:#d03b3b} .na{fill:#5a5a55} .badge{fill:#141413;font:700 14px system-ui,Segoe UI,Arial}
</style>"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def svg_wrap(width, height, body, inline=False, label=""):
    """A standalone dark SVG (inline=False) or a theme-aware chart for an HTML report."""
    if inline:
        return ('<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s" preserveAspectRatio="xMidYMid meet">\n'
                '%s\n</svg>' % (width, height, esc(label), body))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">\n%s\n'
            '<rect class="bg" width="100%%" height="100%%"/>\n%s\n</svg>\n' % (width, height, width, height, SVG_STYLE, body))


def tip(text):
    """Attributes that give an SVG mark a hover / focus tooltip in the HTML report."""
    return ' data-tip="%s" tabindex="0"' % esc(text).replace("\n", "&#10;")


# ---------------------------------------------------------------- HTML reports
#
# Every skill writes investor/reports/<name>.html: one self-contained page (no
# scripts or files from elsewhere except two Google fonts, with system fallbacks).
# It is written as a page fragment that the Artifact publisher wraps; it also
# opens directly in any browser.

REPORT_CSS = """
/* Layout: one reading column; summary first (thesis, figures), then the chart, then the detail. */
:root {
  --page: #f3f3f0; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #6e6c67;
  --grid: #e1e0d9; --base: #c3c2b7; --border: rgba(11,11,11,.10); --bar: #c9c8c1;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s8: #e34948; --ring: #fcfcfb;
  --good: #0ca30c; --warn: #fab219; --crit: #d03b3b; --na: #b9b8b1; --up: #006300; --down: #b02a2a;
  --badge-ink: #0b0b0b; --wash: rgba(42,120,214,.08);
  --f-display: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;
  --f-body: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --f-data: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #9a9993;
  --grid: #2c2c2a; --base: #4a4a46; --border: rgba(255,255,255,.10); --bar: #4a4a46;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s8: #e66767; --ring: #1a1a19;
  --na: #5a5a55; --up: #0ca30c; --down: #e66767; --badge-ink: #0b0b0b; --wash: rgba(57,135,229,.12);
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #9a9993;
  --grid: #2c2c2a; --base: #4a4a46; --border: rgba(255,255,255,.10); --bar: #4a4a46;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s8: #e66767; --ring: #1a1a19;
  --na: #5a5a55; --up: #0ca30c; --down: #e66767; --badge-ink: #0b0b0b; --wash: rgba(57,135,229,.12);
  color-scheme: dark; }
* { box-sizing: border-box; }
body { background: var(--page); color: var(--ink); font: 15px/1.55 var(--f-body); margin: 0; }
.wrap { max-width: 960px; margin: 0 auto; padding-block: 28px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }
header.top { display: grid; gap: 10px; }
.eyebrow { font: 500 12px/1.3 var(--f-data); letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
  display: flex; flex-wrap: wrap; gap: 6px 14px; }
.eyebrow b { color: var(--ink); font-weight: 500; }
h1 { font: 600 clamp(28px, 4.4vw, 40px)/1.08 var(--f-display); margin: 0; text-wrap: balance; letter-spacing: -.01em; }
.thesis { font-size: clamp(17px, 2.2vw, 20px); line-height: 1.45; margin: 0; max-width: 62ch; text-wrap: pretty; }
h2 { font: 600 20px/1.2 var(--f-display); margin: 0; letter-spacing: .005em; }
h3 { font: 600 15px/1.3 var(--f-body); margin: 0; }
section { display: grid; gap: 14px; min-width: 0; }
section > * { min-width: 0; }
section > * { min-width: 0; }
.lede { color: var(--ink-2); margin: 0; max-width: 68ch; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.kpi { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px;
  display: grid; gap: 4px; align-content: start; min-width: 0; }
.kpi .k { font-size: 13px; color: var(--ink-2); }
.kpi .v { font: 600 28px/1.1 var(--f-body); letter-spacing: -.01em; }
.kpi .s { font-size: 12.5px; color: var(--muted); }
.kpi.hero { border-color: var(--s1); box-shadow: inset 0 0 0 1px var(--s1); }
.tone-up { color: var(--up); } .tone-down { color: var(--down); }
figure { margin: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 16px 16px 12px; display: grid; gap: 10px; min-width: 0; }
figcaption { font-size: 13px; color: var(--muted); }
.chart { width: 100%; height: auto; display: block; overflow: visible; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 7px; }
.key { width: 18px; height: 10px; border-radius: 2px; display: inline-block; flex: none; }
.key.line { height: 3px; border-radius: 2px; }
.key.dash { height: 0; border-top: 2px dashed currentColor; background: none !important; }
.tbl { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { padding: 9px 14px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; white-space: normal; min-width: 180px; }
th { font: 500 12px var(--f-body); color: var(--muted); text-transform: uppercase; letter-spacing: .05em; white-space: normal; vertical-align: bottom; }
td { font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: 0; }
tr.mark td { background: var(--wash); font-weight: 600; }
.kv { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.kv dl { margin: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 6px 16px; }
.kv dl > div { display: flex; justify-content: space-between; gap: 16px; padding: 8px 0; border-bottom: 1px solid var(--grid); }
.kv dl > div:last-child { border-bottom: 0; }
.kv dt { color: var(--ink-2); } .kv dd { margin: 0; font-variant-numeric: tabular-nums; font-weight: 600; text-align: right; }
.kv dd small { display: block; font-weight: 400; color: var(--muted); font-size: 12px; }
.callout { border-left: 3px solid var(--s1); background: var(--surface); border-radius: 0 10px 10px 0; padding: 12px 16px;
  display: grid; gap: 6px; }
.callout.warn { border-left-color: var(--warn); }
.callout p { margin: 0; color: var(--ink-2); }
.callout ul { margin: 0; padding-left: 18px; color: var(--ink-2); display: grid; gap: 4px; }
.pill { display: inline-flex; align-items: center; gap: 6px; font: 700 12px/1 var(--f-body); letter-spacing: .04em;
  padding: 6px 9px; border-radius: 999px; color: var(--badge-ink); white-space: nowrap; min-width: 66px; justify-content: center; }
.pill.good { background: var(--good); } .pill.warn { background: var(--warn); } .pill.crit { background: var(--crit); color: #fff; }
.pill.na { background: var(--na); }
.tests { display: grid; gap: 10px; }
.test { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
  display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; align-items: start; }
.test .body { display: grid; gap: 6px; min-width: 0; }
.test .rule { font-size: 12.5px; color: var(--muted); }
blockquote { margin: 0; padding: 8px 12px; border-left: 2px solid var(--base); color: var(--ink-2); font-style: italic; }
details { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 16px; }
details > summary { cursor: pointer; font-weight: 600; }
details[open] > summary { margin-bottom: 10px; }
details p, details li { color: var(--ink-2); max-width: 72ch; }
.sources { display: grid; gap: 6px; font-size: 13px; color: var(--ink-2); margin: 0; padding-left: 18px; }
a { color: var(--s1); text-underline-offset: 2px; overflow-wrap: anywhere; }
a:focus-visible, [tabindex]:focus-visible, summary:focus-visible { outline: 2px solid var(--s1); outline-offset: 2px; }
.chain { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; counter-reset: step; }
.step { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
  display: grid; gap: 10px; align-content: start; min-width: 0; }
.step.choke { border-color: var(--s4); box-shadow: inset 0 0 0 2px var(--s4); }
.step .n { font: 500 12px var(--f-data); color: var(--muted); letter-spacing: .06em; text-transform: uppercase;
  display: flex; justify-content: space-between; gap: 8px; }
.meter { height: 8px; border-radius: 4px; background: var(--grid); overflow: hidden; }
.meter > i { display: block; height: 100%; border-radius: 4px; background: var(--s1); }
.step.choke .meter > i { background: var(--s4); }
.score { font: 600 26px/1 var(--f-body); }
.score small { font-size: 13px; font-weight: 400; color: var(--muted); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font: 500 12px var(--f-data); padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border); color: var(--ink-2); }
.chip.src { border-color: var(--s3); color: var(--ink); }
.chip.src::before { content: "\\2713 "; color: var(--s3); }
.facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 12px; font-size: 13px; color: var(--ink-2); }
.facts b { color: var(--ink); font-weight: 600; }
.tag { font-size: 11px; color: var(--muted); }
footer.fine { font-size: 12.5px; color: var(--muted); border-top: 1px solid var(--grid); padding-top: 14px; display: grid; gap: 6px; }
.tip { position: fixed; z-index: 20; pointer-events: none; background: var(--ink); color: var(--page);
  font: 13px/1.4 var(--f-body); padding: 7px 10px; border-radius: 8px; white-space: pre-line; max-width: 280px;
  box-shadow: 0 4px 16px rgba(0,0,0,.18); }
/* chart marks, from the same tokens */
.chart .grid { stroke: var(--grid); stroke-width: 1; } .chart .base { stroke: var(--base); stroke-width: 1; }
.chart .axis { fill: var(--muted); font: 12px var(--f-body); } .chart .lbl { fill: var(--ink); font: 600 13px var(--f-body); }
.chart .note { fill: var(--muted); font: 11.5px var(--f-body); } .chart .big { fill: var(--ink); font: 700 26px var(--f-body); }
.chart .bar { fill: var(--bar); } .chart .s1 { fill: var(--s1); } .chart .s2 { fill: var(--s2); } .chart .s3 { fill: var(--s3); }
.chart .s4 { fill: var(--s4); } .chart .mut { fill: var(--muted); }
.chart .s1l { stroke: var(--s1); stroke-width: 2.5; fill: none; stroke-linejoin: round; stroke-linecap: round; }
.chart .s2l { stroke: var(--s2); stroke-width: 2.5; fill: none; } .chart .s4l { stroke: var(--s4); stroke-width: 2; fill: none; stroke-dasharray: 8 6; }
.chart .mutl { stroke: var(--muted); stroke-width: 2; fill: none; }
.chart .inkl { stroke: var(--ink); stroke-width: 2; fill: none; stroke-linejoin: round; }
.chart .refl { stroke: var(--muted); stroke-width: 1.5; fill: none; stroke-dasharray: 6 5; }
.chart .wash1 { fill: var(--s1); fill-opacity: .14; } .chart .wash2 { fill: var(--s1); fill-opacity: .30; }
.chart .zone { fill: var(--s3); } .chart .ceil { fill: var(--s8); fill-opacity: .22; } .chart .ring { stroke: var(--ring); stroke-width: 2; }
.chart .hit { fill: transparent; } .chart [data-tip]:hover { opacity: .85; }
.chart .spag { stroke: var(--s1); stroke-opacity: .16; stroke-width: 1; fill: none; }
.chart .typl { stroke: var(--ink); stroke-width: 3; fill: none; stroke-linejoin: round; }
.chart .todayl { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 4; }
@media (max-width: 560px) { .facts { grid-template-columns: 1fr; } .kpi .v { font-size: 24px; } th, td { padding: 8px 10px; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""

REPORT_JS = """
(function () {
  var t = document.createElement('div'); t.className = 'tip'; t.hidden = true; document.body.appendChild(t);
  function show(el, x, y) { t.textContent = el.getAttribute('data-tip'); t.hidden = false; place(x, y); }
  function place(x, y) { var w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = Math.max(8, Math.min(x + 14, innerWidth - w - 8)) + 'px';
    t.style.top = Math.max(8, Math.min(y + 14, innerHeight - h - 8)) + 'px'; }
  document.addEventListener('pointerover', function (e) { var el = e.target.closest && e.target.closest('[data-tip]');
    if (el) show(el, e.clientX, e.clientY); else t.hidden = true; });
  document.addEventListener('pointermove', function (e) { if (!t.hidden) place(e.clientX, e.clientY); });
  document.addEventListener('focusin', function (e) { var el = e.target.closest && e.target.closest('[data-tip]');
    if (el) { var r = el.getBoundingClientRect(); show(el, r.right, r.top); } });
  document.addEventListener('focusout', function () { t.hidden = true; });
})();
"""


def report_page(title, eyebrow, heading, thesis, body_html, description=""):
    """A whole report page. eyebrow: list of strings (the first is bold)."""
    eb = "".join(("<b>%s</b>" if i == 0 else "<span>%s</span>") % esc(e) for i, e in enumerate(eyebrow))
    return ('<meta charset="utf-8">\n<title>%s</title>\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '<meta name="claude:viewport-fit-sides" content="cover">\n'
            '<meta name="description" content="%s">\n'
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
            '&family=IBM+Plex+Sans+Condensed:wght@500;600&display=swap">\n'
            "<style>%s</style>\n"
            '<div class="wrap">\n<header class="top"><div class="eyebrow">%s</div><h1>%s</h1>'
            '<p class="thesis">%s</p></header>\n%s\n'
            '<footer class="fine"><span>%s</span><span>Made with Investor Skills. Generated %s from SEC EDGAR filings '
            "and Yahoo Finance prices.</span></footer>\n</div>\n<script>%s</script>\n"
            % (esc(title), esc(description), REPORT_CSS, eb, esc(heading), thesis, body_html, esc(DISCLAIMER),
               today().strftime("%d %b %Y"), REPORT_JS))


def h_section(title, *parts, lede=None):
    return "<section><h2>%s</h2>%s%s</section>" % (esc(title), ('<p class="lede">%s</p>' % lede) if lede else "",
                                                   "".join(parts))


def h_kpis(items):
    """items: dicts with k (label), v (value), s (sub), tone ('up'/'down'/None), hero (bool)."""
    out = []
    for it in items:
        tone = (' tone-%s' % it["tone"]) if it.get("tone") else ""
        out.append('<div class="kpi%s"><span class="k">%s</span><span class="v%s">%s</span>%s</div>' % (
            " hero" if it.get("hero") else "", esc(it["k"]), tone, esc(it["v"]),
            ('<span class="s">%s</span>' % esc(it["s"])) if it.get("s") else ""))
    return '<div class="kpis">%s</div>' % "".join(out)


def h_figure(svg, caption="", legend=None):
    """legend: list of (css colour var, label, kind) with kind 'box', 'line' or 'dash'."""
    leg = ""
    if legend:
        leg = '<div class="legend">%s</div>' % "".join(
            '<span><i class="key %s" style="background:var(%s);color:var(%s)"></i>%s</span>'
            % (kind if kind != "box" else "", var, var, esc(label)) for var, label, kind in legend)
    return "<figure>%s%s%s</figure>" % (leg, svg, ("<figcaption>%s</figcaption>" % caption) if caption else "")


def h_table(headers, rows, mark=None):
    """rows: lists of cells (already-escaped HTML allowed via h_raw); mark: index of the row to highlight."""
    head = "".join("<th>%s</th>" % esc(h) for h in headers)
    body = "".join('<tr%s>%s</tr>' % (' class="mark"' if i == mark else "",
                                      "".join("<td>%s</td>" % (c if isinstance(c, Raw) else esc(c)) for c in r))
                   for i, r in enumerate(rows))
    return '<div class="tbl"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (head, body)


class Raw(str):
    """A string that is already HTML."""


def h_kv(groups):
    """groups: list of lists of (label, value, small)."""
    out = []
    for g in groups:
        rows = "".join("<div><dt>%s</dt><dd>%s%s</dd></div>" % (
            esc(k), esc(v), ("<small>%s</small>" % esc(s)) if s else "") for k, v, s in g)
        out.append("<dl>%s</dl>" % rows)
    return '<div class="kv">%s</div>' % "".join(out)


def h_callout(title, items, warn=False):
    lis = "".join("<li>%s</li>" % esc(i) for i in items)
    return '<div class="callout%s"><h3>%s</h3>%s</div>' % (" warn" if warn else "", esc(title),
                                                           ("<ul>%s</ul>" % lis) if len(items) > 1 else
                                                           ("<p>%s</p>" % esc(items[0]) if items else ""))


def h_details(summary, html_body):
    return "<details><summary>%s</summary>%s</details>" % (esc(summary), html_body)


def h_paras(*texts):
    return "".join("<p>%s</p>" % esc(t) for t in texts)


def h_sources(links):
    """links: (label, url) pairs; url may be None."""
    seen, items = set(), []
    for label, url in links:
        if (label, url) in seen:
            continue
        seen.add((label, url))
        items.append("<li>%s</li>" % (('<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(url), esc(label)))
                                      if url else esc(label)))
    return '<ul class="sources">%s</ul>' % "".join(items)


def h_pill(grade):
    cls = {"PASS": "good", "WARN": "warn", "FAIL": "crit"}.get(grade, "na")
    icon = {"PASS": "&#10003;", "WARN": "!", "FAIL": "&#10005;"}.get(grade, "&#8211;")
    return '<span class="pill %s" aria-label="%s">%s %s</span>' % (cls, esc(grade), icon, esc(grade))


def write_report(name, html_text):
    """Saves investor/reports/<name>.html and returns the path."""
    return write_text(os.path.join(data_dir(), "reports", "%s.html" % name), html_text)


def filing_links(cik, accns, label="SEC filing"):
    return [("%s %s" % (label, a), filing_index_url(cik, a)) for a in accns if a]


def nice_ticks(lo, hi, n=5):
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    while v <= hi + 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


# ---------------------------------------------------------------- setup commands
#
#   python investkit.py save-contact "Your Name" you@mail.com   save the SEC contact once
#   python investkit.py check                                   is everything ready?

def check():
    """A plain checklist of what the skills need. Returns (all_ok, lines)."""
    lines, ok = [], True

    def row(good, text, fix=None):
        lines.append("%s  %s" % ("OK " if good else "FIX", text))
        if not good and fix:
            lines.append("     " + fix)
        return good

    v = sys.version_info
    ok &= row(v >= (3, 8), "Python %d.%d.%d" % (v[0], v[1], v[2]),
              "Python 3.8 or newer is needed. Install the latest from python.org (Windows: tick 'Add python.exe to PATH').")
    cfg = load_config()
    ua = os.environ.get("SEC_USER_AGENT") or cfg.get("sec_user_agent", "")
    has_contact = bool(ua and "@" in ua and "example.com" not in ua)
    ok &= row(has_contact, "SEC contact %s" % ("saved (%s)" % ua.split()[-1] if has_contact else "not saved yet"),
              'Run: python investkit.py save-contact "Your Name" you@mail.com')
    if has_contact:
        try:
            fetch("https://data.sec.gov/submissions/CIK0000320193.json", "sec", ttl_hours=0)
            row(True, "SEC EDGAR reachable")
        except InvestError as e:
            ok &= row(False, "SEC EDGAR not reachable: %s" % e, "Check the internet connection, or a firewall or VPN blocking sec.gov.")
    try:
        yahoo_chart("SPY", "5d", "1d", ttl_hours=0)
        row(True, "Yahoo Finance reachable")
    except InvestError as e:
        ok &= row(False, "Yahoo Finance not reachable: %s" % e, "Check the internet connection, or a firewall or VPN blocking yahoo.com.")
    try:
        os.makedirs(data_dir(), exist_ok=True)
        row(True, "Results will be saved in %s" % os.path.abspath(data_dir()))
    except OSError as e:
        ok &= row(False, "Cannot create %s: %s" % (data_dir(), e), "Run Claude from a folder you can write to.")
    row(True, "Downloaded data is shared across folders in %s" % cache_dir())
    return ok, lines


def _main(argv):
    setup_console()
    if len(argv) == 3 and argv[0] == "save-contact":
        try:
            print("Saved the SEC contact in %s. It is used in every folder." % save_contact(argv[1], argv[2]))
            return 0
        except InvestError as e:
            print(str(e), file=sys.stderr)
            return 2
    if argv[:1] == ["check"]:
        good, lines = check()
        print("\n".join(lines))
        print("\nReady." if good else "\nFix the lines marked FIX, then run check again.")
        return 0 if good else 1
    print('usage: python investkit.py save-contact "Your Name" you@mail.com | check', file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
