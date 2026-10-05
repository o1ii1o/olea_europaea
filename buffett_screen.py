#!/usr/bin/env python3
"""Buffett financial-statement screen (Brian Feroldi's "rules of thumb").

Universe
    Every equity on the primary exchanges of the US, UK, Europe and Hong Kong
    with a market cap above MIN_MCAP_USD (default USD 1bn), pulled from the
    Yahoo Finance screener.

Data
    Latest *annual* statements from Yahoo's fundamentals-timeseries endpoint
    (up to 4 fiscal years), fetched in ONE request per ticker.  Results are
    cached (data/buffett_cache.json, kept in the Actions cache rather than in
    git) and a ticker is only re-fetched once its cache entry is older than
    REFRESH_DAYS — statements only change once a quarter.

Rules (14) — latest fiscal year unless stated
    Income statement
      gm   Gross margin            Gross profit / Revenue            > 40 %
      sga  SG&A margin             SG&A / Gross profit               < 30 %
      rd   R&D margin              R&D / Gross profit                < 30 %
      dep  Depreciation margin     D&A / Gross profit                < 10 %
      int  Interest margin         Interest expense / Op. income     < 15 %
      tax  Tax margin              Tax provision / Pre-tax income    15–35 %
      nm   Net income margin       Net income / Revenue              > 20 %
      eps  EPS growth              Diluted EPS positive every year and up y/y
    Balance sheet
      cd   Cash & debt             Cash + ST investments > Total debt
      de   Adj. debt to equity     Total liabilities /
                                   (Equity + Treasury stock)         < 0.80
      pref Preferred stock         none
      re   Retained earnings       up in every year available
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

# Annual line items requested from Yahoo (without the "annual" prefix).
KEYS = [
    # income statement
    "TotalRevenue", "OperatingRevenue", "CostOfRevenue", "GrossProfit",
    "SellingGeneralAndAdministration", "SellingAndMarketingExpense",
    "GeneralAndAdministrativeExpense", "ResearchAndDevelopment",
    "ReconciledDepreciation", "DepreciationAndAmortizationInIncomeStatement",
    "InterestExpense", "InterestExpenseNonOperating", "OperatingIncome",
    "TaxProvision", "PretaxIncome", "NetIncome", "NetIncomeCommonStockholders",
    "DilutedEPS", "BasicEPS",
    # balance sheet
    "CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalents",
    "TotalDebt", "TotalLiabilitiesNetMinorityInterest", "StockholdersEquity",
    "TreasuryStock", "PreferredStock", "PreferredStockEquity", "RetainedEarnings",
    # cash flow
    "CapitalExpenditure", "DepreciationAndAmortization",
    "DepreciationAmortizationDepletion",
]

RULES = ["gm", "sga", "rd", "dep", "int", "tax", "nm", "eps",
         "cd", "de", "pref", "re", "ts", "cap"]

TAX_BAND = (0.15, 0.35)


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
            quotes, total = screen_region(code, exch, MIN_MCAP_USD)
            print(f"  {label}/{code}: {len(quotes)} of {total}")
            names_seen = set()
            for q in quotes:
                sym = q.get("symbol")
                if not sym or sym in seen:
                    continue
                name = q.get("longName") or q.get("shortName") or sym
                # One line per company (GOOG/GOOGL, BRK-A/BRK-B, ...): quotes
                # are sorted by mcap, so the first listing seen is kept.
                key = name.lower().replace(",", "").replace(".", "")
                if key in names_seen:
                    continue
                names_seen.add(key)
                seen.add(sym)
                rows.append(dict(
                    s=sym, n=name, r=label, c=code.upper(),
                    x=q.get("fullExchangeName") or q.get("exchange"),
                    cur=q.get("currency"), mcap_local=q.get("marketCap"),
                    px=q.get("regularMarketPrice"),
                ))
                n_label += 1
        stats[label] = n_label
    return rows, stats


# ── Fundamentals ────────────────────────────────────────────────────────────

def fetch_fundamentals(sym, attempts=3):
    """Return {'dates': [...desc], 'v': {key: [values aligned to dates]}}."""
    last_exc = None
    for a in range(attempts):
        try:
            t = yf.Ticker(sym)
            # One request for all three statements (same code path yfinance
            # uses for income_stmt / balance_sheet / cashflow).
            df = t._fundamentals._financials._get_financials_time_series("yearly", KEYS)
            if df is None or df.empty:
                return None
            df = df.loc[:, sorted(df.columns, reverse=True)]
            dates = [c.strftime("%Y-%m-%d") for c in df.columns]
            v = {}
            for k in df.index:
                vals = [None if (x is None or (isinstance(x, float) and math.isnan(x)))
                        else float(x) for x in df.loc[k].tolist()]
                if any(x is not None for x in vals):
                    v[k] = vals
            return {"dates": dates, "v": v}
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            if "Empty fundamentals" in msg or "No data" in msg:
                return None
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


def evaluate(f):
    """Return (fy, metrics{rule: value}, passes{rule: True/False/None})."""
    n = len(f["dates"])
    rev = _series(f, "TotalRevenue", "OperatingRevenue")
    ni = _series(f, "NetIncomeCommonStockholders", "NetIncome")

    # Latest fiscal year with both revenue and net income reported.
    i0 = next((i for i in range(n) if rev[i] is not None and ni[i] is not None), None)
    if i0 is None:
        return None
    idx = list(range(i0, n))                      # latest -> oldest

    def at(series, i=i0):
        return series[i] if i < len(series) else None

    R, NI = at(rev), at(ni)
    gp = at(_series(f, "GrossProfit"))
    if gp is None:
        cor = at(_series(f, "CostOfRevenue"))
        gp = R - cor if (R is not None and cor is not None) else None

    sga = at(_series(f, "SellingGeneralAndAdministration"))
    if sga is None:
        sm = at(_series(f, "SellingAndMarketingExpense"))
        ga = at(_series(f, "GeneralAndAdministrativeExpense"))
        if sm is not None or ga is not None:
            sga = (sm or 0) + (ga or 0)
    rd = at(_series(f, "ResearchAndDevelopment"))
    dep = at(_series(f, "ReconciledDepreciation", "DepreciationAndAmortization",
                     "DepreciationAmortizationDepletion",
                     "DepreciationAndAmortizationInIncomeStatement"))
    intx = at(_series(f, "InterestExpense", "InterestExpenseNonOperating"))
    opi = at(_series(f, "OperatingIncome"))
    tax = at(_series(f, "TaxProvision"))
    pti = at(_series(f, "PretaxIncome"))
    cash = at(_series(f, "CashCashEquivalentsAndShortTermInvestments",
                      "CashAndCashEquivalents"))
    debt = at(_series(f, "TotalDebt"))
    liab = at(_series(f, "TotalLiabilitiesNetMinorityInterest"))
    eq = at(_series(f, "StockholdersEquity"))
    ts = at(_series(f, "TreasuryStock"))
    pref = at(_series(f, "PreferredStock", "PreferredStockEquity"))
    capex = at(_series(f, "CapitalExpenditure"))

    eps_s = _series(f, "DilutedEPS", "BasicEPS")
    eps = [eps_s[i] for i in idx if eps_s[i] is not None]
    re_s = _series(f, "RetainedEarnings")
    re = [re_s[i] for i in idx if re_s[i] is not None]

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
        m["re"] = (re[0] / re[1] - 1) if re[1] > 0 else None
        p["re"] = all(re[k] > re[k + 1] for k in range(len(re) - 1)) and re[0] > 0
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

    return f["dates"][i0], m, p


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
    universe, region_counts = get_universe()
    if LIMIT:
        universe = universe[:LIMIT]
    if len(universe) < 100 and not LIMIT:
        print(f"Only {len(universe)} names from the screener — aborting, "
              "previous output left unchanged.")
        return 1
    print(f"{len(universe)} companies: {region_counts}")

    # Market cap in USD.  LSE quotes are in pence (GBp) but Yahoo reports the
    # market cap itself in pounds.
    norm = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
    rates = fx_to_usd({norm.get(u["cur"], u["cur"]) for u in universe})
    for u in universe:
        rate = rates.get(norm.get(u["cur"], u["cur"]))
        u["mc"] = (u["mcap_local"] * rate / 1e9
                   if (rate and u["mcap_local"]) else None)

    cache = load_cache()
    today = datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=REFRESH_DAYS)).isoformat()
    stale = [u["s"] for u in universe
             if cache.get(u["s"], {}).get("fetched", "") < cutoff]
    # Never-fetched first, then oldest.
    stale.sort(key=lambda s: cache.get(s, {}).get("fetched", ""))
    stale = stale[:MAX_FETCH]
    print(f"Fetching statements for {len(stale)} tickers "
          f"({len(universe) - len(stale)} served from cache) ...")

    errors = []
    for k, sym in enumerate(stale, 1):
        try:
            f = fetch_fundamentals(sym)
            cache[sym] = {"fetched": today.isoformat(), "f": f}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sym}: {exc}"[:160])
        if k % 250 == 0:
            print(f"  {k}/{len(stale)}  errors={len(errors)}  "
                  f"{(time.time() - started) / 60:.1f} min")
            save_cache(cache)
        time.sleep(PAUSE)
    save_cache(cache)

    rows, no_data = [], 0
    fy_dates = []
    for u in universe:
        f = (cache.get(u["s"]) or {}).get("f")
        res = evaluate(f) if f else None
        if not res:
            no_data += 1
            continue
        fy, m, p = res
        fy_dates.append(fy)
        flags = [None if p[r] is None else int(p[r]) for r in RULES]
        rows.append({
            "s": u["s"], "n": u["n"], "r": u["r"], "c": u["c"],
            "mc": _r(u["mc"], 2), "fy": fy,
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
        "evaluated": len(rows),
        "no_data": no_data,
        "fetched_this_run": len(stale),
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
