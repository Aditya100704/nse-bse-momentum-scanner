"""
Phenom fundamentals fetcher — the Minervini layer we were missing.

Pulls company fundamentals (EPS growth, sales growth, profit growth, margins,
ROE, P/E, market cap, and the NEXT EARNINGS DATE) for every qualifier + IPO +
episodic-pivot stock from TradingView's PUBLIC scanner endpoint
(scanner.tradingview.com/india/scan) — no login, no API key. Writes
data/fundamentals.json keyed by symbol, which the dashboard merges into the
tables.

Why TradingView: MarketSmith India is login-walled (can't automate without
handing over credentials — we don't); screener.in is scrape-gray and slower.
TV's scan endpoint is public and returns clean, structured fundamentals for the
whole NSE+BSE universe.

Run:  py -3 fundamentals_fetch.py            (all qualifiers + IPOs + EPs)
      py -3 fundamentals_fetch.py 50         (cap to 50, for testing)
Runs daily in fundamentals.yml (+ manual dispatch).
"""
from __future__ import annotations

import os, sys, json
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MARKET = os.getenv("SCAN_MARKET", "in").strip().lower()
_US = MARKET == "us"
OUT = DATA / ("fundamentals_us.json" if _US else "fundamentals.json")
IN_SCAN = DATA / ("scanner_output_us.json" if _US else "scanner_output.json")

# TradingView public scanner region: america for US, india for NSE/BSE.
URL = "https://scanner.tradingview.com/america/scan" if _US else "https://scanner.tradingview.com/india/scan"
MCAP_DIV = 1e6 if _US else 1e7      # market cap units: $ million (US) vs Rs crore (India)
H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
BATCH = 350

# TV scanner column -> our short field name
COLS = {
    "price_earnings_ttm": "pe",
    "earnings_per_share_basic_ttm": "eps_ttm",
    "earnings_per_share_diluted_yoy_growth_ttm": "eps_growth",      # YoY %  (Minervini)
    "total_revenue_yoy_growth_ttm": "sales_growth",                 # YoY %  (Minervini)
    "net_income_yoy_growth_ttm": "profit_growth",                   # YoY %
    "return_on_equity": "roe",
    "debt_to_equity": "de",
    "net_margin_ttm": "net_margin",
    "market_cap_basic": "mcap",
    "sector": "sector",                                             # real sector (TV)
    "industry": "industry",                                         # finer industry (TV)
    "earnings_release_next_date": "next_earnings",                  # unix ts
    "earnings_release_date": "last_earnings",                       # unix ts
}
COL_KEYS = list(COLS.keys())


def _iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
    except Exception:
        return None


def fetch_batch(tickers: list[str]) -> dict:
    body = {"symbols": {"tickers": tickers, "query": {"types": []}}, "columns": COL_KEYS}
    r = requests.post(URL, json=body, headers=H, timeout=30)
    r.raise_for_status()
    out = {}
    for row in r.json().get("data", []):
        tv = row["s"]                      # e.g. "NSE:RELIANCE"
        sym = tv.split(":", 1)[1].upper()
        d = dict(zip(COL_KEYS, row["d"]))
        rec = {}
        for col, short in COLS.items():
            v = d.get(col)
            if short in ("next_earnings", "last_earnings"):
                rec[short] = _iso(v)
            elif short == "mcap":
                rec[short] = round(v / MCAP_DIV) if isinstance(v, (int, float)) else None  # $M (US) / ₹cr (IN)
            elif isinstance(v, (int, float)):
                rec[short] = round(v, 2)
            else:
                rec[short] = v
        # days until next earnings (handy for "don't hold through earnings")
        if rec.get("next_earnings"):
            try:
                dd = (datetime.fromisoformat(rec["next_earnings"]).date() - datetime.now(timezone.utc).date()).days
                rec["days_to_earnings"] = dd
            except Exception:
                rec["days_to_earnings"] = None
        out[sym] = rec
    return out


def main():
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else None
    scan = json.loads(IN_SCAN.read_text(encoding="utf-8"))

    # Collect unique TV tickers from qualifiers + IPOs + EPs.
    seen, tickers = set(), []
    for block in ("results", "ipos", "episodic_pivots"):
        for r in scan.get(block, []):
            sym = (r.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            if _US:
                exch = (r.get("exchange") or "NASDAQ").upper()
                if exch not in ("NASDAQ", "NYSE", "AMEX"):
                    exch = "NASDAQ"
            else:
                exch = "NSE" if (r.get("exchange") or "NSE") == "NSE" else "BSE"
            tickers.append(f"{exch}:{sym}")
    if cap:
        tickers = tickers[:cap]
    print(f"[fund] fetching fundamentals for {len(tickers)} symbols from TradingView...", flush=True)

    data = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        try:
            data.update(fetch_batch(chunk))
            print(f"[fund] {min(i+BATCH, len(tickers))}/{len(tickers)}", flush=True)
        except Exception as e:
            print(f"[fund] batch {i} failed: {e}", flush=True)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "count": len(data), "source": "TradingView", "data": data}
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"[fund] wrote {OUT} — {len(data)} stocks with fundamentals", flush=True)


if __name__ == "__main__":
    main()
