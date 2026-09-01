#!/usr/bin/env python3
"""Daily TD Sequential screen for Dow Jones, S&P 500 and Nasdaq 100 stocks.

For every constituent of the three indices we pull ~1y of daily bars from
yfinance and compute Tom DeMark's TD Sequential:

  * TD Setup   — 9 consecutive closes below (buy) / above (sell) the close 4
                 bars earlier.  Completion at count 9.
  * TD Countdown — after a completed setup, count bars whose close is <= the
                 low 2 bars earlier (buy) / >= the high 2 bars earlier (sell).
                 Completion at count 13.

The screen reports the signals that COMPLETED on the most recent daily bar,
split into Bullish (buy setup / buy countdown) and Bearish (sell setup /
sell countdown), and injects them into index.html between marker comments.

Countdown here uses the standard aggregation (close vs low[-2] / high[-2]);
the finer deferral/cancellation nuances are not modelled.
"""

import re
import sys
import urllib.request
from zoneinfo import ZoneInfo
from datetime import datetime

import pandas as pd
import yfinance as yf

from pathlib import Path

HTML_FILE = Path(__file__).parent / "index.html"

# Wikipedia constituent pages.  (url, minimum plausible row count for the
# components table — used to pick the right table on the page.)
INDEX_SOURCES = {
    "DJIA": ("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", 25),
    "SPX":  ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 400),
    "NDX":  ("https://en.wikipedia.org/wiki/Nasdaq-100", 90),
}

# Short badge letters shown next to each ticker.
INDEX_BADGE = {"DJIA": "D", "SPX": "S", "NDX": "N"}

# Stable fallback for the Dow 30 if Wikipedia scraping fails.
DJIA_FALLBACK = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
    "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
]


# ── Constituents ────────────────────────────────────────────────────────────

def _norm(sym):
    """Normalise a Wikipedia ticker to the yfinance form (BRK.B -> BRK-B)."""
    return str(sym).strip().upper().replace(".", "-")


def _members_from_html(html, min_rows):
    """Return the ticker column of the largest Symbol/Ticker table on a page."""
    best = []
    for table in pd.read_html(html):
        col = next((c for c in table.columns
                    if str(c).strip() in ("Symbol", "Ticker", "Ticker symbol")), None)
        if col is None:
            continue
        syms = [_norm(s) for s in table[col].tolist()
                if isinstance(s, str) or not pd.isna(s)]
        syms = [s for s in syms if re.fullmatch(r"[A-Z][A-Z0-9-]{0,6}", s)]
        if len(syms) >= min_rows and len(syms) > len(best):
            best = syms
    return best


def get_index_members():
    """Return {ticker: set(index_keys)} across DJIA / SPX / NDX."""
    members = {}
    for key, (url, min_rows) in INDEX_SOURCES.items():
        syms = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                html = resp.read().decode("utf-8", "replace")
            syms = _members_from_html(html, min_rows)
        except Exception as exc:  # noqa: BLE001
            print(f"  {key}: fetch/parse failed ({exc})")
        if not syms and key == "DJIA":
            syms = DJIA_FALLBACK
            print(f"  {key}: using hardcoded fallback ({len(syms)} names)")
        print(f"  {key}: {len(syms)} names")
        for s in syms:
            members.setdefault(s, set()).add(key)
    return members


# ── Prices ──────────────────────────────────────────────────────────────────

def fetch_history(tickers, period="1y", batch=80):
    """Return {ticker: (dates, highs, lows, closes)} chronological."""
    out = {}
    for k in range(0, len(tickers), batch):
        chunk = tickers[k:k + batch]
        try:
            data = yf.download(chunk, period=period, group_by="ticker",
                               auto_adjust=False, progress=False, threads=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  price batch {k // batch}: {exc}")
            continue
        for t in chunk:
            try:
                df = data[t] if len(chunk) > 1 else data
                df = df[["High", "Low", "Close"]].dropna()
                if len(df) < 20:
                    continue
                out[t] = (
                    list(df.index),
                    [float(v) for v in df["High"]],
                    [float(v) for v in df["Low"]],
                    [float(v) for v in df["Close"]],
                )
            except (KeyError, IndexError, TypeError, ValueError):
                continue
    return out


# ── TD Sequential ─────────────────────────────────────────────────────────────

def td_last_bar_signals(highs, lows, closes):
    """Return the set of TD signals COMPLETED on the last bar.

    Elements are among: 'buy_setup', 'sell_setup', 'buy_countdown',
    'sell_countdown'.
    """
    n = len(closes)
    if n < 13:          # need 4-bar lookback + a 9-count to complete a setup
        return set()
    buy = sell = 0
    cd_dir, cd_count = None, 0
    last = n - 1
    signals = set()
    for i in range(n):
        buy = buy + 1 if (i >= 4 and closes[i] < closes[i - 4]) else 0
        sell = sell + 1 if (i >= 4 and closes[i] > closes[i - 4]) else 0

        if buy == 9:
            if i == last:
                signals.add("buy_setup")
            cd_dir, cd_count = "buy", 0            # start / restart buy countdown
        if sell == 9:
            if i == last:
                signals.add("sell_setup")
            cd_dir, cd_count = "sell", 0           # start / restart sell countdown

        if cd_dir == "buy" and i >= 2 and closes[i] <= lows[i - 2]:
            cd_count += 1
            if cd_count == 13:
                if i == last:
                    signals.add("buy_countdown")
                cd_dir, cd_count = None, 0
        elif cd_dir == "sell" and i >= 2 and closes[i] >= highs[i - 2]:
            cd_count += 1
            if cd_count == 13:
                if i == last:
                    signals.add("sell_countdown")
                cd_dir, cd_count = None, 0
    return signals


# ── HTML rendering ────────────────────────────────────────────────────────────

def _badges(idx_set):
    return "".join(INDEX_BADGE[k] for k in ("DJIA", "SPX", "NDX") if k in idx_set)


def render_list(rows):
    """rows: list of dict(ticker, badges, kind, px). Countdown first, then A-Z."""
    if not rows:
        return '<p class="td-empty">No signals on the latest close.</p>'
    order = {"C13": 0, "S9": 1}
    rows = sorted(rows, key=lambda r: (order.get(r["kind"], 9), r["ticker"]))
    out = []
    for r in rows:
        sig_cls = "td-c13" if r["kind"] == "C13" else "td-s9"
        out.append(
            f'<a class="td-item" href="https://finviz.com/quote.ashx?t={r["ticker"]}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'<span class="td-tkr">{r["ticker"]}</span>'
            f'<span class="td-idx">{r["badges"]}</span>'
            f'<span class="td-sig {sig_cls}">{r["kind"]}</span>'
            f'<span class="td-px">{r["px"]}</span></a>'
        )
    return "\n".join(out)


def replace_marker(content, name, inner):
    start, end = f"<!--{name}_START-->", f"<!--{name}_END-->"
    i = content.index(start) + len(start)
    j = content.index(end, i)
    return content[:i] + inner + content[j:]


def fmt_px(v):
    return f"{v:,.2f}" if v >= 10 else f"{v:.3f}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Fetching index constituents ...")
    members = get_index_members()
    tickers = sorted(members)
    if not tickers:
        print("No constituents resolved — index.html left unchanged.")
        return
    print(f"Screening {len(tickers)} unique tickers ...")

    prices = fetch_history(tickers)
    print(f"Got price history for {len(prices)} / {len(tickers)} tickers.")

    bullish, bearish = [], []
    data_date = None
    for t in tickers:
        hist = prices.get(t)
        if not hist:
            continue
        dates, highs, lows, closes = hist
        sig = td_last_bar_signals(highs, lows, closes)
        if not sig:
            continue
        if data_date is None or dates[-1] > data_date:
            data_date = dates[-1]
        row = dict(ticker=t, badges=_badges(members[t]), px=fmt_px(closes[-1]))
        if "buy_countdown" in sig:
            bullish.append({**row, "kind": "C13"})
        elif "buy_setup" in sig:
            bullish.append({**row, "kind": "S9"})
        if "sell_countdown" in sig:
            bearish.append({**row, "kind": "C13"})
        elif "sell_setup" in sig:
            bearish.append({**row, "kind": "S9"})

    print(f"Bullish: {len(bullish)}  |  Bearish: {len(bearish)}")

    now_zurich = datetime.now(ZoneInfo("Europe/Zurich")).strftime("%d/%m %H:%M")
    dstr = data_date.strftime("%d/%m/%Y") if data_date is not None else "—"
    stamp = f"close {dstr} · updated {now_zurich} Zurich"

    content = HTML_FILE.read_text()
    content = replace_marker(content, "TD_BULLISH", "\n" + render_list(bullish) + "\n")
    content = replace_marker(content, "TD_BEARISH", "\n" + render_list(bearish) + "\n")
    content = replace_marker(content, "TD_UPDATED", stamp)
    HTML_FILE.write_text(content)
    print(f"Wrote {HTML_FILE}")


if __name__ == "__main__":
    sys.exit(main())
