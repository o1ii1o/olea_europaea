#!/usr/bin/env python3
"""Fetch live market data via yfinance and update the Market Snapshot in index.html."""

import re
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

HTML_FILE = Path(__file__).parent / "index.html"

# Each section: (html_escaped_title, [(display_name, yfinance_ticker), ...])
SECTIONS = [
    ("Currencies", [
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
    ("Commodities", [
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
    ("U.S. Treasury Yields &amp; ETFs", [
        ("U.S. 3M", "^IRX"),
        ("U.S. 5Y", "^FVX"),
        ("U.S. 10Y", "^TNX"),
        ("U.S. 30Y", "^TYX"),
        ("iShares US Treasury", "GOVT"),
        ("SPDR 1-3M T-Bill", "BIL"),
        ("iShares 1-3Y Treasury", "SHY"),
        ("iShares 7-10Y Treasury", "IEF"),
        ("iShares 20+Y Treasury", "TLT"),
        ("ProShares Ultra Short 20+Y", "TBT"),
        ("PIMCO 25+Y Zero Coupon", "ZROZ"),
    ]),
    ("Global Market Indices", [
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
    "U.S. 3M": "https://www.investing.com/rates-bonds/u.s.-3-month-bond-yield",
    "U.S. 5Y": "https://www.investing.com/rates-bonds/u.s.-5-year-bond-yield",
    "U.S. 10Y": "https://www.investing.com/rates-bonds/u.s.-10-year-bond-yield",
    "U.S. 30Y": "https://www.investing.com/rates-bonds/u.s.-30-year-bond-yield",
    "iShares US Treasury": "https://www.investing.com/etfs/ishares-us-treasury-bond-etf",
    "SPDR 1-3M T-Bill": "https://www.investing.com/etfs/spdr-bloomberg-1-3-month-t-bill",
    "iShares 1-3Y Treasury": "https://www.investing.com/etfs/ishares-1-3-year-treasury-bond",
    "iShares 7-10Y Treasury": "https://www.investing.com/etfs/ishares-7-10-year-treasury-bond",
    "iShares 20+Y Treasury": "https://www.investing.com/etfs/ishares-20-year-treasury-bond",
    "ProShares Ultra Short 20+Y": "https://www.investing.com/etfs/proshares-ultrashort-20-year-treasury",
    "PIMCO 25+Y Zero Coupon": "https://www.investing.com/etfs/pimco-25-year-zero-coupon-us-treas",
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


def fetch_data():
    """Fetch market data for all tickers via yf.download (single batch call)."""
    all_tickers = [t for _, instruments in SECTIONS for _, t in instruments]

    print(f"Downloading data for {len(all_tickers)} tickers ...")
    data = yf.download(all_tickers, period="5d", progress=False)

    results = {}
    for _, instruments in SECTIONS:
        for name, ticker in instruments:
            try:
                closes = data["Close"][ticker].dropna()
                if len(closes) < 2:
                    print(f"  skip {ticker} ({name}): fewer than 2 data points")
                    continue
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                chg = last - prev
                chg_pct = (chg / prev) * 100 if prev else 0.0
                last_dt = closes.index[-1]
                results[ticker] = dict(
                    last=last, chg=chg, chg_pct=chg_pct, time=last_dt
                )
            except (KeyError, IndexError, TypeError) as exc:
                print(f"  skip {ticker} ({name}): {exc}")
    return results


# ── Formatting helpers ────────────────────────────────────────────────────────

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


def fmt_time(dt):
    return dt.strftime("%d/%m")


# ── HTML generation ───────────────────────────────────────────────────────────

def build_tbody(results):
    """Return the inner HTML of <tbody> for the snapshot table."""
    lines = []
    for section_title, instruments in SECTIONS:
        lines.append(
            f'              <!-- ── {section_title} ── -->'
        )
        lines.append(
            f'              <tr class="section-header">'
            f'<td colspan="5">{section_title}</td></tr>'
        )
        for name, ticker in instruments:
            if ticker not in results:
                continue
            d = results[ticker]
            cls = "chg-pos" if d["chg"] >= 0 else "chg-neg"
            url = URLS.get(name)
            name_cell = (
                f'<a href="{url}" target="_blank">{name}</a>' if url else name
            )
            lines.append(
                f'              <tr>'
                f'<td>{name_cell}</td>'
                f'<td>{fmt_price(d["last"])}</td>'
                f'<td class="{cls}">{fmt_change(d["chg"], d["last"])}</td>'
                f'<td class="{cls}">{fmt_change(d["chg_pct"], 100)}%</td>'
                f'<td>{fmt_time(d["time"])}</td>'
                f'</tr>'
            )
    return "\n".join(lines)


def update_html(tbody_html):
    """Replace everything between <tbody> and </tbody> in index.html,
    and update the snapshot-live-status span with the current UTC time."""
    content = HTML_FILE.read_text()

    # Update tbody
    start_tag = "<tbody>"
    end_tag = "</tbody>"
    i = content.index(start_tag) + len(start_tag)
    j = content.index(end_tag)
    content = content[:i] + "\n" + tbody_html + "\n            " + content[j:]

    # Update the status span with the current UTC timestamp
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
    results = fetch_data()
    total = sum(len(instr) for _, instr in SECTIONS)
    print(f"Fetched data for {len(results)} / {total} instruments.")
    if not results:
        print("No data fetched – index.html left unchanged.")
        return
    tbody = build_tbody(results)
    update_html(tbody)
    print("Done – open index.html in a browser to see the update.")


if __name__ == "__main__":
    main()
