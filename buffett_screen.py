#!/usr/bin/env python3
"""Buffett financial-statement screen (Brian Feroldi's "rules of thumb").

Universe
    Every equity on the primary exchanges of the US, UK, Europe and Hong Kong
    with a market cap above MIN_MCAP_USD (default USD 1bn), pulled from the
    Yahoo Finance screener.

Data
    Yahoo's fundamentals-timeseries endpoint, three requests per ticker:
      * annual statements (up to 4 fiscal years) — history for the EPS and
        retained-earnings rules, and fallback for everything else;
      * trailing twelve months (TTM) for income-statement and cash-flow lines;
      * the latest interim (quarterly / half-year) balance sheet.
    So the ratios reflect the most recent reported quarter (US) or half-year
    (most UK / European / HK issuers), not just the last annual report.
    Results are cached (data/buffett_cache.json, kept in the Actions cache
    rather than in git).  A ticker is re-fetched two days after its earnings
    date (from the Yahoo quote), and in any case once its entry is older than
    REFRESH_DAYS.

Rules (14) — TTM / latest balance sheet unless stated
    Income statement
      gm   Gross margin            Gross profit / Revenue            > 40 %
      sga  SG&A margin             SG&A / Gross profit               < 30 %
      rd   R&D margin              R&D / Gross profit                < 30 %
      dep  Depreciation margin     D&A / Gross profit                < 10 %
      int  Interest margin         Interest expense / Op. income     < 15 %
      tax  Tax margin              Tax provision / Pre-tax income    15–35 %
      nm   Net income margin       Net income / Revenue              > 20 %
      eps  EPS growth              Diluted EPS positive every year; TTM above
                                   last FY (else FY above prior FY)
    Balance sheet
      cd   Cash & debt             Cash + ST investments > Total debt
      de   Adj. debt to equity     Total liabilities /
                                   (Equity + Treasury stock)         < 0.80
      pref Preferred stock         none
      re   Retained earnings       up in every fiscal year, and latest interim
                                   above the same date a year earlier
      ts   Treasury stock          exists
    Cash-flow statement
      cap  Capex margin            Capex / Net income                < 25 %

Output
    data/buffett_screen.json — one row per company with the 14 metric values
    and pass/fail flags; index.html loads it and does the filtering client
    side.  The "updated" stamp in index.html is refreshed between markers.
"""

import json
import math
import re
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from yfinance import EquityQuery

ROOT = Path(__file__).parent
HTML_FILE = ROOT / "index.html"
DATA_DIR = ROOT / "data"
OUT_FILE = DATA_DIR / "buffett_screen.json"
CACHE_FILE = Path(os.environ.get("BUFFETT_CACHE", DATA_DIR / "buffett_cache.json"))

MIN_MCAP_USD = float(os.environ.get("BUFFETT_MIN_MCAP_USD", 1e9))
# Average daily traded value floor — also removes dormant copies of foreign
# shares on Vienna / Xetra / SIX that the other filters miss.
MIN_TRADED_USD = float(os.environ.get("BUFFETT_MIN_TRADED_USD", 1e5))
REFRESH_DAYS = int(os.environ.get("BUFFETT_REFRESH_DAYS", 7))
MAX_FETCH = int(os.environ.get("BUFFETT_MAX_FETCH", 6000))     # per run
PAUSE = float(os.environ.get("BUFFETT_PAUSE", 0.25))            # s between tickers
LIMIT = int(os.environ.get("BUFFETT_LIMIT", 0))                 # debug: cap universe

# Region label -> {yahoo region code: [primary exchange codes]}
UNIVERSE = {
    "US": {"us": ["NYQ", "NMS", "NGM", "NCM", "ASE"]},
    "UK": {"gb": ["LSE"]},
    "Europe": {
        "at": ["VIE"], "be": ["BRU"], "ch": ["EBS"], "de": ["GER"],
        "dk": ["CPH"], "es": ["MCE"], "fi": ["HEL"], "fr": ["PAR"],
        "gr": ["ATH"], "ie": ["ISE"], "it": ["MIL"], "nl": ["AMS"],
        "no": ["OSL"], "pl": ["WSE"], "pt": ["LIS"], "se": ["STO"],
    },
    "HK": {"hk": ["HKG"]},
}

# Line items requested from Yahoo (without the annual/quarterly/trailing prefix).
FLOW_KEYS = [
    # income statement
    "TotalRevenue", "OperatingRevenue", "CostOfRevenue", "GrossProfit",
    "SellingGeneralAndAdministration", "SellingAndMarketingExpense",
    "GeneralAndAdministrativeExpense", "ResearchAndDevelopment",
    "ReconciledDepreciation", "DepreciationAndAmortizationInIncomeStatement",
    "InterestExpense", "InterestExpenseNonOperating", "OperatingIncome",
    "TaxProvision", "PretaxIncome", "NetIncome", "NetIncomeCommonStockholders",
    "DilutedEPS", "BasicEPS",
    # cash flow
    "CapitalExpenditure", "DepreciationAndAmortization",
    "DepreciationAmortizationDepletion",
]
BAL_KEYS = [
    "CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalents",
    "TotalDebt", "TotalLiabilitiesNetMinorityInterest", "TotalAssets",
    "MinorityInterest", "StockholdersEquity",
    "TreasuryStock", "PreferredStock", "PreferredStockEquity", "RetainedEarnings",
]
KEYS = FLOW_KEYS + BAL_KEYS
CACHE_VERSION = 2          # 2 = annual + quarterly balance sheet + TTM flows

RULES = ["gm", "sga", "rd", "dep", "int", "tax", "nm", "eps",
         "cd", "de", "pref", "re", "ts", "cap"]

TAX_BAND = (0.15, 0.35)

# Listings whose USD market cap is logged each run as a sanity check on the
# currency handling (LSE quotes in pence, HK in HKD, ...).
MCAP_CHECK = {"AZN.L", "HSBA.L", "SHEL.L", "0700.HK", "0005.HK", "NESN.SW",
              "SAP.DE", "ASML.AS", "MC.PA", "NOVO-B.CO", "AAPL"}


# ── Universe ────────────────────────────────────────────────────────────────

def screen_region(region, exchanges, min_mcap, page=250, attempts=3):
    """All equities in `region` on `exchanges` with intraday mcap > min_mcap."""
    q = EquityQuery("and", [
        EquityQuery("eq", ["region", region]),
        EquityQuery("is-in", ["exchange", *exchanges]),
        EquityQuery("gt", ["intradaymarketcap", min_mcap]),
    ])
    quotes, offset, total = [], 0, None
    while total is None or offset < total:
        res = None
        for a in range(attempts):
            try:
                res = yf.screen(q, offset=offset, size=page,
                                sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"    {region} offset {offset} attempt {a + 1}: {exc}")
                time.sleep(5 * (a + 1))
        if not res:
            break
        batch = res.get("quotes") or []
        total = res.get("total", 0) if total is None else total
        quotes.extend(batch)
        if not batch:
            break
        offset += len(batch)
        time.sleep(0.5)
    return quotes, total or 0


def fx_to_usd(currencies):
    """{currency: USD per 1 unit}.  GBp/GBX/ZAc handled by the caller."""
    rates = {"USD": 1.0}
    need = sorted({c for c in currencies if c and c != "USD"})
    for cur in need:
        try:
            hist = yf.Ticker(f"{cur}USD=X").history(period="10d")
            closes = hist["Close"].dropna()
            if len(closes):
                rates[cur] = float(closes.iloc[-1])
        except Exception as exc:  # noqa: BLE001
            print(f"  FX {cur}: {exc}")
    return rates


def get_universe():
    rows, seen, stats = [], set(), {}
    for label, regions in UNIVERSE.items():
        n_label = 0
        for code, exch in regions.items():
            # Yahoo applies this filter in the listing's own currency, so it
            # is set loose here; the exact USD cut is applied after FX.
            quotes, total = screen_region(code, exch, MIN_MCAP_USD * 0.5)
            print(f"  {label}/{code}: {len(quotes)} of {total}")
            for q in quotes:
                sym = q.get("symbol")
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                rows.append(dict(
                    s=sym, n=q.get("longName") or q.get("shortName") or sym,
                    r=label, c=code.upper(), x=q.get("exchange"),
                    cur=q.get("currency"), fcur=q.get("financialCurrency"),
                    mcap_local=q.get("marketCap"), px=q.get("regularMarketPrice"),
                    vol=q.get("averageDailyVolume3Month") or q.get("averageDailyVolume10Day"),
                    earn=q.get("earningsTimestamp") or q.get("earningsTimestampStart"),
                ))
                n_label += 1
        stats[label] = n_label
    sample = quotes[0] if quotes else {}
    return rows, stats, sorted(sample.keys())


# Secondary-market codes for shares whose primary listing is elsewhere:
#   LSE "0xxx.L" international lines, Borsa Italiana "1xxx.MI" (GEM / EuroTLX
#   foreign shares), HKEX 8xxxx.HK RMB counters.
SECONDARY_PATTERNS = [re.compile(p) for p in
                      (r"^0[A-Z0-9]{3}\.L$", r"^1[A-Z][A-Z0-9-]*\.MI$", r"^8\d{4}\.HK$")]

# Reporting currencies plausible for a primary listing in each region.  A
# Xetra / Vienna / SIX line of a Japanese or Canadian company reports in JPY /
# CAD and is dropped; US-dollar reporters are kept (Shell, HSBC, Glencore ...).
HOME_FCUR = {
    "UK": {"GBP", "GBp", "USD", "EUR"},
    "Europe": {"EUR", "CHF", "GBP", "SEK", "NOK", "DKK", "PLN", "USD"},
    "HK": {"HKD", "CNY", "USD"},
}

_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
             "limited", "plc", "ag", "sa", "se", "nv", "spa", "ab", "publ", "asa",
             "as", "oyj", "the", "holding", "holdings", "group", "class", "a", "b"}


def name_key(name):
    words = re.sub(r"[^\w ]", " ", name.lower().replace(".", "")).split()
    core = [w for w in words if w not in _SUFFIXES]
    return " ".join(core or words)


def clean_universe(rows):
    """Keep one primary listing per company."""
    dropped = {"secondary_code": 0, "foreign_reporting_ccy": 0, "illiquid": 0,
               "duplicate": 0}
    keep = []
    for u in rows:
        if any(p.match(u["s"]) for p in SECONDARY_PATTERNS):
            dropped["secondary_code"] += 1
            continue
        allowed = HOME_FCUR.get(u["r"])
        if allowed and u["fcur"] and u["fcur"] not in allowed:
            dropped["foreign_reporting_ccy"] += 1
            continue
        if 0 < (u["vt"] or 0) < MIN_TRADED_USD:
            dropped["illiquid"] += 1
            continue
        keep.append(u)
    # Same company on several venues (ADR + home line, Xetra / Vienna / SIX
    # copies of foreign shares, A/B classes): keep the most-traded line.  A
    # US ADR is only preferred over a home listing if it trades >10x more, so
    # Shell stays a UK name and BABA an HK name, while Apple's Xetra copy
    # never displaces AAPL.
    def weight(u):
        return (u["vt"] or 0) / (10 if u["r"] == "US" else 1)
    best = {}
    for u in keep:
        k = name_key(u["n"])
        if k not in best or weight(u) > weight(best[k]):
            best[k] = u
    out = [u for u in keep if best[name_key(u["n"])] is u]
    dropped["duplicate"] = len(keep) - len(out)
    return out, dropped


# ── Fundamentals ────────────────────────────────────────────────────────────

def _table(t, timescale, keys):
    """One fundamentals-timeseries request -> {'dates': [desc], 'v': {key: [...]}}."""
    try:
        # Same code path yfinance uses for income_stmt / balance_sheet /
        # cashflow, but with our own key list so it is a single request.
        df = t._fundamentals._financials._get_financials_time_series(timescale, keys)
    except Exception as exc:  # noqa: BLE001
        if "Empty fundamentals" in str(exc):
            return None
        raise
    if df is None or df.empty:
        return None
    df = df.loc[:, sorted(df.columns, reverse=True)]
    v = {}
    for k in df.index:
        vals = [None if (x is None or (isinstance(x, float) and math.isnan(x)))
                else float(x) for x in df.loc[k].tolist()]
        if any(x is not None for x in vals):
            v[k] = vals
    return {"dates": [c.strftime("%Y-%m-%d") for c in df.columns], "v": v}


def fetch_fundamentals(sym, attempts=3):
    """Annual history (4y), latest interim balance sheets and TTM flows.

    Returns {'a': table, 'q': table|None, 't': table|None} or None if Yahoo
    has no annual statements for the ticker.
    """
    last_exc = None
    for a in range(attempts):
        try:
            t = yf.Ticker(sym)
            annual = _table(t, "yearly", KEYS)
            if annual is None:
                return None
            out = {"a": annual, "q": None, "t": None}
            for key, scale, keys in (("q", "quarterly", BAL_KEYS),
                                     ("t", "trailing", FLOW_KEYS)):
                try:
                    out[key] = _table(t, scale, keys)
                except Exception as exc:  # noqa: BLE001  annual still usable
                    print(f"    {sym} {scale}: {exc}")
            return out
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(4 * (a + 1))
    raise RuntimeError(str(last_exc))


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, separators=(",", ":")))


# ── Rules ───────────────────────────────────────────────────────────────────

def _series(f, *keys):
    """First key present -> list aligned to f['dates'] (may contain None)."""
    for k in keys:
        if k in f["v"]:
            return f["v"][k]
    return [None] * len(f["dates"])


def _ratio(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _days(d1, d2):
    return (datetime.fromisoformat(d1) - datetime.fromisoformat(d2)).days


def evaluate(f):
    """Return (info, metrics{rule: value}, passes{rule: True/False/None}).

    Flow items (income statement, cash flow) use the trailing twelve months
    when Yahoo has a TTM period ending after the last fiscal year; balance
    sheet items use the latest interim balance sheet when it is newer than
    the annual one.  Anything missing from those falls back to the annual
    report.  info = {'fe': flow period end, 'fb': 'TTM'|'FY', 'be': balance
    sheet date, 'fy': last fiscal year end}.
    """
    if "a" not in f:                       # cache entry from v1 (annual only)
        f = {"a": f, "q": None, "t": None}
    A = f["a"]
    n = len(A["dates"])
    rev = _series(A, "TotalRevenue", "OperatingRevenue")
    ni = _series(A, "NetIncomeCommonStockholders", "NetIncome")

    # Latest fiscal year with both revenue and net income reported.
    i0 = next((i for i in range(n) if rev[i] is not None and ni[i] is not None), None)
    if i0 is None:
        return None
    idx = list(range(i0, n))                      # latest -> oldest
    fy = A["dates"][i0]

    def annual(*keys):
        ser = _series(A, *keys)
        return ser[i0] if i0 < len(ser) else None

    # TTM flows -------------------------------------------------------------
    T = f.get("t")
    use_ttm = False
    if T and T["dates"] and T["dates"][0] > fy:
        t_rev = _series(T, "TotalRevenue", "OperatingRevenue")[0]
        t_ni = _series(T, "NetIncomeCommonStockholders", "NetIncome")[0]
        use_ttm = t_rev is not None and t_ni is not None

    def flow(*keys):
        if use_ttm:
            v = _series(T, *keys)[0]
            if v is not None:
                return v
        return annual(*keys)

    # Latest balance sheet ---------------------------------------------------
    Q = f.get("q")
    jq = None
    if Q and Q["dates"]:
        eqs = _series(Q, "StockholdersEquity")
        jq = next((j for j in range(len(Q["dates"]))
                   if eqs[j] is not None and Q["dates"][j] > fy), None)

    def bal(*keys):
        if jq is not None:
            v = _series(Q, *keys)[jq]
            if v is not None:
                return v
        return annual(*keys)

    info = {"fe": T["dates"][0] if use_ttm else fy, "fb": "TTM" if use_ttm else "FY",
            "be": Q["dates"][jq] if jq is not None else fy, "fy": fy}

    R = flow("TotalRevenue", "OperatingRevenue")
    NI = flow("NetIncomeCommonStockholders", "NetIncome")
    gp = flow("GrossProfit")
    if gp is None:
        cor = flow("CostOfRevenue")
        gp = R - cor if (R is not None and cor is not None) else None

    sga = flow("SellingGeneralAndAdministration")
    if sga is None:
        sm = flow("SellingAndMarketingExpense")
        ga = flow("GeneralAndAdministrativeExpense")
        if sm is not None or ga is not None:
            sga = (sm or 0) + (ga or 0)
    rd = flow("ResearchAndDevelopment")
    dep = flow("ReconciledDepreciation", "DepreciationAndAmortization",
               "DepreciationAmortizationDepletion",
               "DepreciationAndAmortizationInIncomeStatement")
    intx = flow("InterestExpense", "InterestExpenseNonOperating")
    opi = flow("OperatingIncome")
    tax = flow("TaxProvision")
    pti = flow("PretaxIncome")
    capex = flow("CapitalExpenditure")

    cash = bal("CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalents")
    debt = bal("TotalDebt")
    liab = bal("TotalLiabilitiesNetMinorityInterest")
    eq = bal("StockholdersEquity")
    if liab is None and eq is not None:
        assets = bal("TotalAssets")
        if assets is not None:
            liab = assets - eq - (bal("MinorityInterest") or 0.0)
    ts = bal("TreasuryStock")
    pref = bal("PreferredStock", "PreferredStockEquity")

    # EPS: positive in every fiscal year; growth measured on the most recent
    # comparison available — TTM vs last fiscal year (i.e. the latest
    # quarters vs the same quarters a year earlier), else FY vs prior FY.
    eps_s = _series(A, "DilutedEPS", "BasicEPS")
    eps = [eps_s[i] for i in idx if eps_s[i] is not None]
    eps_ttm = _series(T, "DilutedEPS", "BasicEPS")[0] if use_ttm else None
    if eps_ttm is not None and eps_s[i0] is not None:
        eps = [eps_ttm] + eps

    # Retained earnings: up in every fiscal year, and — when interim balance
    # sheets exist — latest interim above the same date a year earlier.
    re_s = _series(A, "RetainedEarnings")
    re = [re_s[i] for i in idx if re_s[i] is not None]
    re_yoy = None
    if jq is not None:
        rq = _series(Q, "RetainedEarnings")
        if rq[jq] is not None:
            for j in range(jq + 1, len(Q["dates"])):
                if rq[j] is not None and 320 <= _days(Q["dates"][jq], Q["dates"][j]) <= 410:
                    re_yoy = (rq[jq], rq[j])
                    break

    gp_ok = gp is not None and gp > 0
    m, p = {}, {}

    # Income statement ------------------------------------------------------
    m["gm"] = _ratio(gp, R) if (R and R > 0) else None
    p["gm"] = None if m["gm"] is None else m["gm"] > 0.40

    def over_gp(x, limit, missing_is_zero=False):
        if x is None and missing_is_zero:
            x = 0.0
        if x is None or gp is None:
            return None, None
        if not gp_ok:
            return None, False
        v = abs(x) / gp
        return v, v < limit

    m["sga"], p["sga"] = over_gp(sga, 0.30)
    m["rd"], p["rd"] = over_gp(rd, 0.30, missing_is_zero=True)
    m["dep"], p["dep"] = over_gp(dep, 0.10)

    if opi is None:
        m["int"], p["int"] = None, None
    elif opi <= 0:
        m["int"], p["int"] = None, False
    else:
        m["int"] = abs(intx or 0.0) / opi
        p["int"] = m["int"] < 0.15

    if tax is None or pti is None:
        m["tax"], p["tax"] = None, None
    elif pti <= 0:
        m["tax"], p["tax"] = None, False
    else:
        m["tax"] = tax / pti
        p["tax"] = TAX_BAND[0] <= m["tax"] <= TAX_BAND[1]

    m["nm"] = _ratio(NI, R) if (R and R > 0) else None
    p["nm"] = None if m["nm"] is None else m["nm"] > 0.20

    if len(eps) >= 2:
        m["eps"] = (eps[0] / eps[1] - 1) if eps[1] > 0 else None
        p["eps"] = all(e > 0 for e in eps) and eps[0] > eps[1]
    else:
        m["eps"], p["eps"] = None, None

    # Balance sheet ---------------------------------------------------------
    if cash is None:
        m["cd"], p["cd"] = None, None
    else:
        d = debt or 0.0
        m["cd"] = (cash / d) if d > 0 else None      # cash / debt; None = no debt
        p["cd"] = cash > d

    if liab is None or eq is None:
        m["de"], p["de"] = None, None
    else:
        denom = eq + abs(ts or 0.0)
        if denom <= 0:
            m["de"], p["de"] = None, False
        else:
            m["de"] = liab / denom
            p["de"] = m["de"] < 0.80

    m["pref"] = abs(pref) if pref else 0.0
    p["pref"] = not pref

    if len(re) >= 2:
        yearly_up = all(re[k] > re[k + 1] for k in range(len(re) - 1))
        now, base = re_yoy if re_yoy else (re[0], re[1])
        m["re"] = (now / base - 1) if base > 0 else None
        p["re"] = yearly_up and now > base and now > 0
    else:
        m["re"], p["re"] = None, None

    m["ts"] = abs(ts) if ts else 0.0
    p["ts"] = bool(ts)

    # Cash flow -------------------------------------------------------------
    if capex is None or NI is None:
        m["cap"], p["cap"] = None, None
    elif NI <= 0:
        m["cap"], p["cap"] = None, False
    else:
        m["cap"] = abs(capex) / NI
        p["cap"] = m["cap"] < 0.25

    return info, m, p


# ── Output ──────────────────────────────────────────────────────────────────

def _r(x, nd=4):
    return None if x is None else round(x, nd)


def replace_marker(content, name, inner):
    start, end = f"<!--{name}_START-->", f"<!--{name}_END-->"
    i = content.index(start) + len(start)
    j = content.index(end, i)
    return content[:i] + inner + content[j:]


def main():
    started = time.time()
    print(f"Universe: mcap > USD {MIN_MCAP_USD / 1e9:.1f}bn ...")
    universe, screener_counts, quote_fields = get_universe()
    print(f"Screener returned {len(universe)} listings: {screener_counts}")

    # Market cap in USD.  LSE quotes are in pence (GBp) but Yahoo reports the
    # market cap itself in pounds.
    norm = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
    rates = fx_to_usd({norm.get(u["cur"], u["cur"]) for u in universe})
    for u in universe:
        rate = rates.get(norm.get(u["cur"], u["cur"]))
        u["mc"] = (u["mcap_local"] * rate / 1e9
                   if (rate and u["mcap_local"]) else None)
        # Average daily traded value in USD (LSE prices are in pence).
        px = (u["px"] or 0) / (100 if u["cur"] in ("GBp", "GBX", "ZAc", "ILA") else 1)
        u["vt"] = (u["vol"] or 0) * px * (rate or 0)
    mcap_check = {u["s"]: [u["cur"], u["mcap_local"], u["mc"] and round(u["mc"], 1)]
                  for u in universe if u["s"] in MCAP_CHECK}
    print("Market-cap check:", mcap_check)
    universe = [u for u in universe if u["mc"] and u["mc"] * 1e9 >= MIN_MCAP_USD]
    universe, dropped = clean_universe(universe)
    print(f"Dropped: {dropped}")
    region_counts = {k: sum(1 for u in universe if u["r"] == k) for k in UNIVERSE}
    if LIMIT:            # debug: an evenly spread sample across regions
        universe = universe[::max(1, len(universe) // LIMIT)][:LIMIT]
    elif len(universe) < 500:
        print(f"Only {len(universe)} names after filters — aborting, "
              "previous output left unchanged.")
        return 1
    print(f"{len(universe)} companies above USD {MIN_MCAP_USD / 1e9:.1f}bn: {region_counts}")

    cache = load_cache()
    now = datetime.now(timezone.utc)
    today = now.date()
    cutoff = (today - timedelta(days=REFRESH_DAYS)).isoformat()

    def refresh_reason(u):
        c = cache.get(u["s"]) or {}
        fetched = c.get("fetched", "")
        if c.get("ver") != CACHE_VERSION:
            return "new"
        # Results published since our last fetch: give Yahoo two days to
        # load the new statements, then re-fetch.
        if u.get("earn"):
            due = datetime.fromtimestamp(u["earn"], timezone.utc) + timedelta(days=2)
            if due <= now and fetched < due.date().isoformat():
                return "earnings"
        if fetched < cutoff:
            return "age"
        return None

    reasons = {u["s"]: refresh_reason(u) for u in universe}
    order = {"new": 0, "earnings": 1, "age": 2}
    stale = sorted((s for s, r in reasons.items() if r),
                   key=lambda s: (order[reasons[s]], cache.get(s, {}).get("fetched", "")))
    stale = stale[:MAX_FETCH]
    why = {k: sum(1 for s in stale if reasons[s] == k) for k in order}
    print(f"Fetching statements for {len(stale)} tickers {why} "
          f"({len(universe) - len(stale)} served from cache) ...")

    errors = []
    for k, sym in enumerate(stale, 1):
        try:
            f = fetch_fundamentals(sym)
            cache[sym] = {"fetched": today.isoformat(), "ver": CACHE_VERSION, "f": f}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sym}: {exc}"[:160])
        if k % 250 == 0:
            print(f"  {k}/{len(stale)}  errors={len(errors)}  "
                  f"{(time.time() - started) / 60:.1f} min")
            save_cache(cache)
        time.sleep(PAUSE)
    save_cache(cache)

    rows, no_data = [], 0
    basis = {"TTM": 0, "FY": 0, "interim_bs": 0, "annual_bs": 0}
    for u in universe:
        f = (cache.get(u["s"]) or {}).get("f")
        res = evaluate(f) if f else None
        if not res:
            no_data += 1
            continue
        info, m, p = res
        basis[info["fb"]] += 1
        basis["interim_bs" if info["be"] > info["fy"] else "annual_bs"] += 1
        flags = [None if p[r] is None else int(p[r]) for r in RULES]
        rows.append({
            "s": u["s"], "n": u["n"], "r": u["r"], "c": u["c"],
            "mc": _r(u["mc"], 2), "fy": info["fy"], "fe": info["fe"],
            "fb": info["fb"], "be": info["be"],
            "m": [_r(m[r]) for r in RULES], "p": flags,
            "sc": sum(1 for x in flags if x == 1),
        })
    rows.sort(key=lambda r: (-r["sc"], -(r["mc"] or 0)))

    full = sum(1 for r in rows if r["sc"] == len(RULES))
    now_z = datetime.now(ZoneInfo("Europe/Zurich"))
    meta = {
        "generated": now_z.strftime("%Y-%m-%d %H:%M %Z"),
        "min_mcap_usd": MIN_MCAP_USD,
        "rules": RULES,
        "universe": len(universe),
        "by_region": region_counts,
        "screener_listings": screener_counts,
        "dropped": dropped,
        "quote_fields": quote_fields,
        "fx": {k: round(v, 5) for k, v in rates.items()},
        "mcap_check": mcap_check,
        "evaluated": len(rows),
        "no_data": no_data,
        "fetched_this_run": len(stale),
        "fetch_reasons": why,
        "data_basis": basis,
        "fetch_errors": len(errors),
        "error_sample": errors[:15],
        "pass_all": full,
        "runtime_min": round((time.time() - started) / 60, 1),
    }
    DATA_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps({"meta": meta, "rows": rows},
                                   separators=(",", ":"), ensure_ascii=False))
    print(json.dumps(meta, indent=1))

    stamp = (f"{len(rows):,} companies · {full} pass all 14 · "
             f"updated {now_z.strftime('%d/%m %H:%M')} Zurich")
    content = HTML_FILE.read_text()
    content = replace_marker(content, "BUFFETT_UPDATED", stamp)
    HTML_FILE.write_text(content)
    print(f"Wrote {OUT_FILE} and stamp in {HTML_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
