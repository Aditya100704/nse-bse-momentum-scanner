"""Smoke test: run the analyzer + filter pipeline on a tiny known-good universe."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from scan import analyze, download_prices, _safe_float, MIN_LIQUIDITY_CR  # noqa: E402

TEST_TICKERS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
    "ASIANPAINT.NS", "BAJFINANCE.NS", "MARUTI.NS", "AXISBANK.NS", "LT.NS",
    "WIPRO.NS", "TITAN.NS", "ULTRACEMCO.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "DIVISLAB.NS", "TATAMOTORS.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "BAJAJ-AUTO.NS",
    "POWERGRID.NS", "NTPC.NS", "COALINDIA.NS", "SUNPHARMA.NS", "ONGC.NS",
]

def main() -> int:
    prices = download_prices(TEST_TICKERS)
    print(f"\n[smoke] got data for {len(prices)} / {len(TEST_TICKERS)} tickers")
    assert len(prices) >= 25, f"Too few responses: {len(prices)}"

    rows = []
    for t, df in prices.items():
        res = analyze(df)
        assert res is not None, f"analyze() returned None for {t}"
        # Sanity ranges
        assert res["close"] > 0, f"{t}: bad close {res['close']}"
        if res["pct_off_high"] is not None:
            assert -1 <= res["pct_off_high"] <= 100, f"{t}: pct_off_high out of range {res['pct_off_high']}"
        assert isinstance(res["trend_template"], bool)
        rows.append({"ticker": t, **res})

    df = pd.DataFrame(rows)
    above_200 = df[df["price_gt_sma200"]]
    liquid    = df[df["turnover_cr"] >= MIN_LIQUIDITY_CR]
    qualifies = df[df["price_gt_sma200"] & (df["turnover_cr"] >= MIN_LIQUIDITY_CR)]
    tt_pass   = df[df["trend_template"]]

    print(f"[smoke] above 200MA: {len(above_200)}/{len(df)}")
    print(f"[smoke] liquid (>= Rs.{MIN_LIQUIDITY_CR}cr): {len(liquid)}/{len(df)}")
    print(f"[smoke] qualifies (both): {len(qualifies)}/{len(df)}")
    print(f"[smoke] trend template pass: {len(tt_pass)}/{len(df)}")
    print(f"\n[smoke] sample qualifier:")
    if len(qualifies):
        print(json.dumps(qualifies.iloc[0].to_dict(), indent=2, default=str))
    print("\n[smoke] OK - pipeline passes")
    return 0

if __name__ == "__main__":
    sys.exit(main())
