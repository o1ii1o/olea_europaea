#!/usr/bin/env python3
"""Fetch live market data via yfinance and update the Market Snapshot in index.html."""

import re
from pathlib import Path

import yfinance as yf

HTML_FILE = Path(__file__).parent / "index.html"

# Each section: (html_escaped_title, [(display_name, yfinance_ticker), ...])
SECTIONS = [
    ("Currencies &amp; Commodities", [
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
        ("XAU/USD", "GC=F"),
        ("XAG/USD", "SI=F"),
        ("BTC/USD", "BTC-USD"),
        ("ETH/USD", "ETH-USD"),
        ("Crude Oil WTI", "CL=F"),
        ("Brent Oil", "BZ=F"),
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
            lines.append(
                f'              <tr>'
                f'<td>{name}</td>'
                f'<td>{fmt_price(d["last"])}</td>'
                f'<td class="{cls}">{fmt_change(d["chg"], d["last"])}</td>'
                f'<td class="{cls}">{fmt_change(d["chg_pct"], 100)}%</td>'
                f'<td>{fmt_time(d["time"])}</td>'
                f'</tr>'
            )
    return "\n".join(lines)


def update_html(tbody_html):
    """Replace everything between <tbody> and </tbody> in index.html."""
    content = HTML_FILE.read_text()
    start_tag = "<tbody>"
    end_tag = "</tbody>"
    i = content.index(start_tag) + len(start_tag)
    j = content.index(end_tag)
    new_content = content[:i] + "\n" + tbody_html + "\n            " + content[j:]
    HTML_FILE.write_text(new_content)
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
