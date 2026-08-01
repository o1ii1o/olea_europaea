#!/usr/bin/env python3
"""Fetch live market data and update the Market Snapshot in index.html.

Two data sources:
  * yfinance  – currencies, commodities, equity indices (daily prices)
  * FRED      – interest rates (SOFR, US Treasury curve, central-bank &
                foreign 10-year rates).  Needs a free FRED_API_KEY env var.
Each row shows Last, Chg, Chg% and YTD%.
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

HTML_FILE = Path(__file__).parent / "index.html"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# Each section: (title, source, [(display_name, key), ...])
#   source "yf"   -> key is a yfinance ticker
#   source "fred" -> key is a FRED series id
SECTIONS = [
    ("Currencies", "yf", [
        ("US Dollar Index", "DX-Y.NYB"),
        ("AUD/USD", "AUDUSD=X"),
        ("USD/JPY", "JPY=X"),
        ("USD/HKD", "HKD=X"),
        ("USD/CHF", "CHF=X"),
        ("USD/ILS", "ILS=X"),
        ("EUR/ILS", "EURILS=X"),
        ("EUR/CHF", "EURCHF=X"),
        ("EUR/USD", "EURUSD=X"),
        ("EUR/GBP", "EURGBP=X"),
        ("GBP/CHF", "GBPCHF=X"),
        ("GBP/USD", "GBPUSD=X"),
        ("BTC/USD", "BTC-USD"),
        ("ETH/USD", "ETH-USD"),
    ]),
    ("Commodities", "yf", [
        ("XAU/USD", "GC=F"),
        ("XAG/USD", "SI=F"),
        ("Platinum", "PL=F"),
        ("Crude Oil WTI", "CL=F"),
        ("Brent Oil", "BZ=F"),
        ("Natural Gas", "NG=F"),
        ("Copper", "HG=F"),
        ("Aluminum", "ALI=F"),
        ("Steel", "SRU=F"),
        ("Uranium", "UX=F"),
    ]),
    ("Rates", "fred", [
        ("USD SOFR", "SOFR"),
        ("U.S. 3M", "DGS3MO"),
        ("U.S. 1Y", "DGS1"),
        ("U.S. 2Y", "DGS2"),
        ("U.S. 5Y", "DGS5"),
        ("U.S. 10Y", "DGS10"),
        ("U.S. 30Y", "DGS30"),
        ("Australia CB", "IRSTCB01AUM156N"),
        ("Australia 10Y", "IRLTLT01AUM156N"),
        ("Japan CB", "IRSTCB01JPM156N"),
        ("Japan 10Y", "IRLTLT01JPM156N"),
        ("China CB", "IRSTCB01CNM156N"),
        ("China 10Y", "IRLTLT01CNM156N"),
        ("Israel CB", "IRSTCB01ILM156N"),
        ("Israel 10Y", "IRLTLT01ILM156N"),
        ("ECB Refi", "ECBMRRFR"),
        ("Euro 10Y", "IRLTLT01EZM156N"),
        ("UK CB", "IRSTCB01GBM156N"),
        ("UK 10Y", "IRLTLT01GBM156N"),
    ]),
    ("Global Market Indices", "yf", [
        ("MSCI World", "URTH"),
        ("Nikkei 225", "^N225"),
        ("Shanghai", "000001.SS"),
        ("Hang Seng", "^HSI"),
        ("Nifty 50", "^NSEI"),
        ("TA 35", "^TA35.TA"),
        ("Euro Stoxx 50", "^STOXX50E"),
        ("SMI", "^SSMI"),
        ("DAX", "^GDAXI"),
        ("CAC 40", "^FCHI"),
        ("S&amp;P 500", "^GSPC"),
        ("Dow Jones", "^DJI"),
        ("Nasdaq 100", "^NDX"),
        ("S&amp;P 500 VIX", "^VIX"),
    ]),
]

URLS = {
    "US Dollar Index": "https://www.investing.com/currencies/us-dollar-index",
    "AUD/USD": "https://www.investing.com/currencies/aud-usd",
    "USD/JPY": "https://www.investing.com/currencies/usd-jpy",
    "USD/HKD": "https://www.investing.com/currencies/usd-hkd",
    "USD/CHF": "https://www.investing.com/currencies/usd-chf",
    "USD/ILS": "https://www.investing.com/currencies/usd-ils",
    "EUR/ILS": "https://www.investing.com/currencies/eur-ils",
    "EUR/CHF": "https://www.investing.com/currencies/eur-chf",
    "EUR/USD": "https://www.investing.com/currencies/eur-usd",
    "EUR/GBP": "https://www.investing.com/currencies/eur-gbp",
    "GBP/CHF": "https://www.investing.com/currencies/gbp-chf",
    "GBP/USD": "https://www.investing.com/currencies/gbp-usd",
    "XAU/USD": "https://www.investing.com/commodities/gold",
    "XAG/USD": "https://www.investing.com/commodities/silver",
    "BTC/USD": "https://www.investing.com/crypto/bitcoin",
    "ETH/USD": "https://www.investing.com/crypto/ethereum",
    "Platinum": "https://www.investing.com/commodities/platinum",
    "Crude Oil WTI": "https://www.investing.com/commodities/crude-oil",
    "Brent Oil": "https://www.investing.com/commodities/brent-oil",
    "Natural Gas": "https://www.investing.com/commodities/natural-gas",
    "Copper": "https://www.investing.com/commodities/copper",
    "Aluminum": "https://www.investing.com/commodities/aluminum",
    "Steel": "https://www.investing.com/commodities/us-steel-coil",
    "Uranium": "https://www.investing.com/commodities/uranium",
    "MSCI World": "https://www.investing.com/etfs/ishares-msci-world",
    "Nikkei 225": "https://www.investing.com/indices/japan-ni225",
    "Shanghai": "https://www.investing.com/indices/shanghai-composite",
    "Hang Seng": "https://www.investing.com/indices/hang-sen-40",
    "Nifty 50": "https://www.investing.com/indices/s-p-cnx-nifty",
    "TA 35": "https://www.investing.com/indices/ta-35",
    "Euro Stoxx 50": "https://www.investing.com/indices/eu-stoxx50",
    "SMI": "https://www.investing.com/indices/switzerland-20",
    "DAX": "https://www.investing.com/indices/germany-30",
    "CAC 40": "https://www.investing.com/indices/france-40",
    "S&amp;P 500": "https://www.investing.com/indices/us-spx-500",
    "Dow Jones": "https://www.investing.com/indices/us-30",
    "Nasdaq 100": "https://www.investing.com/indices/nq-100",
    "S&amp;P 500 VIX": "https://www.investing.com/indices/volatility-s-p-500",
}

# FRED rate rows link to their FRED series page.
for _section_title, _src, _instruments in SECTIONS:
    if _src == "fred":
        for _name, _series in _instruments:
            URLS.setdefault(
                _name, f"https://fred.stlouisfed.org/series/{_series}"
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def ytd_from_series(dates, values):
    """YTD % using the prior year's final value as the baseline.

    dates/values are chronological (oldest first).  Returns None if no
    prior-year baseline is available.
    """
    if not values:
        return None
    last = values[-1]
    cur_year = dates[-1].year
    baseline = None
    for d, v in zip(dates, values):
        if d.year < cur_year:
            baseline = v  # keep the latest prior-year value
    if baseline is None:
        # No prior-year data: fall back to first value of the current year.
        for d, v in zip(dates, values):
            if d.year == cur_year:
                baseline = v
                break
    if not baseline:
        return None
    return (last / baseline - 1) * 100


def fetch_yf(instruments):
    """Fetch yfinance instruments; return {name: dict(last, chg, chg_pct, ytd)}."""
    tickers = [k for _, k in instruments]
    print(f"Downloading {len(tickers)} yfinance tickers ...")
    data = yf.download(tickers, period="2y", progress=False)
    out = {}
    for name, ticker in instruments:
        try:
            closes = data["Close"][ticker].dropna()
            if len(closes) < 2:
                print(f"  skip {ticker} ({name}): <2 points")
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            chg = last - prev
            chg_pct = (chg / prev) * 100 if prev else 0.0
            ytd = ytd_from_series(list(closes.index), [float(v) for v in closes])
            out[name] = dict(last=last, chg=chg, chg_pct=chg_pct, ytd=ytd)
        except (KeyError, IndexError, TypeError) as exc:
            print(f"  skip {ticker} ({name}): {exc}")
    return out


def fred_observations(series_id, limit=800):
    """Return (dates, values) chronological for a FRED series, or ([], []).

    Requests the most recent `limit` observations (sort_order=desc) so long
    daily histories don't return an old first page, then sorts ascending.
    `limit` (default 800) comfortably reaches back past the prior year-end
    needed for YTD on daily, weekly, or monthly series.
    """
    if not FRED_API_KEY:
        return [], []
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
        f"&sort_order=desc&limit={limit}"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001 - network/JSON errors all skip
        print(f"  FRED error {series_id}: {exc}")
        return [], []
    pairs = []
    for obs in payload.get("observations", []):
        raw = obs.get("value")
        if raw in (None, "", "."):
            continue
        try:
            pairs.append((datetime.strptime(obs["date"], "%Y-%m-%d"), float(raw)))
        except (ValueError, KeyError):
            continue
    pairs.sort(key=lambda p: p[0])  # chronological (oldest first)
    dates = [d for d, _ in pairs]
    values = [v for _, v in pairs]
    return dates, values


def fetch_fred(instruments):
    """Fetch FRED rate series; return {name: dict(last, chg, chg_pct, ytd)}."""
    if not FRED_API_KEY:
        print("  FRED_API_KEY not set – rates left unchanged.")
        return {}
    out = {}
    for name, series_id in instruments:
        dates, values = fred_observations(series_id)
        if len(values) < 2:
            print(f"  skip {series_id} ({name}): <2 observations")
            continue
        last = values[-1]
        prev = values[-2]
        chg = last - prev
        chg_pct = (chg / prev) * 100 if prev else 0.0
        ytd = ytd_from_series(dates, values)
        out[name] = dict(last=last, chg=chg, chg_pct=chg_pct, ytd=ytd)
    return out


# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_price(val):
    a = abs(val)
    if a >= 10:
        return f"{val:,.2f}"
    if a >= 1:
        return f"{val:.3f}"
    return f"{val:.4f}"


def fmt_change(val, ref):
    sign = "+" if val >= 0 else ""
    a = abs(ref)
    if a >= 10:
        return f"{sign}{val:,.2f}"
    if a >= 1:
        return f"{sign}{val:.3f}"
    return f"{sign}{val:.4f}"


def fmt_pct(val):
    if val is None:
        return "—", ""
    cls = "chg-pos" if val >= 0 else "chg-neg"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%", cls


# ── HTML generation ───────────────────────────────────────────────────────────

SECTION_TBODY = {
    "Rates": "snap-rates",
    "Commodities": "snap-commodities",
    "Global Market Indices": "snap-equities",
    "Currencies": "snap-currencies",
}


def build_rows(instruments, results):
    """Return the inner HTML rows (Name, Last, Chg, Chg%, YTD%)."""
    lines = []
    for name, _key in instruments:
        d = results.get(name)
        if not d:
            continue
        cls = "chg-pos" if d["chg"] >= 0 else "chg-neg"
        ytd_txt, ytd_cls = fmt_pct(d.get("ytd"))
        url = URLS.get(name)
        name_cell = f'<a href="{url}" target="_blank">{name}</a>' if url else name
        lines.append(
            f'                <tr>'
            f'<td>{name_cell}</td>'
            f'<td>{fmt_price(d["last"])}</td>'
            f'<td class="{cls}">{fmt_change(d["chg"], d["last"])}</td>'
            f'<td class="{cls}">{fmt_change(d["chg_pct"], 100)}%</td>'
            f'<td class="{ytd_cls}">{ytd_txt}</td>'
            f'</tr>'
        )
    return "\n".join(lines)


def replace_tbody(content, tbody_id, rows_html):
    open_tag = f'<tbody id="{tbody_id}">'
    i = content.index(open_tag) + len(open_tag)
    j = content.index("</tbody>", i)
    return content[:i] + "\n" + rows_html + "\n              " + content[j:]


def update_html(results):
    content = HTML_FILE.read_text()
    for section_title, _src, instruments in SECTIONS:
        tbody_id = SECTION_TBODY.get(section_title)
        if not tbody_id:
            continue
        rows = build_rows(instruments, results)
        if rows.strip():
            content = replace_tbody(content, tbody_id, rows)

    now_utc = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")
    content = re.sub(
        r'(<span[^>]*id="snapshot-live-status"[^>]*>)[^<]*(</span>)',
        rf"\1Updated {now_utc}\2",
        content,
    )
    HTML_FILE.write_text(content)
    print(f"Wrote {HTML_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    results = {}
    for _title, src, instruments in SECTIONS:
        if src == "yf":
            results.update(fetch_yf(instruments))
        elif src == "fred":
            results.update(fetch_fred(instruments))

    total = sum(len(instr) for _, _, instr in SECTIONS)
    print(f"Fetched data for {len(results)} / {total} instruments.")
    if not results:
        print("No data fetched – index.html left unchanged.")
        return
    update_html(results)
    print("Done – open index.html in a browser to see the update.")


if __name__ == "__main__":
    main()
