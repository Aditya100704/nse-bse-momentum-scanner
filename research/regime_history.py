"""
Historical market-regime reconstruction.

The live dashboard computes the regime score (0-10) from TODAY's snapshot only.
This rebuilds the SAME score for every past trading day we have data for, using
the identical six ingredients + bands + weights as scan.py `_compute_breadth`,
then lists the dates the market was BULLISH (regime 7-10).

Ingredients per day, across all "analyzed" stocks (>=200 bars at that point):
  % above SMA10/20/50/200, median 1M & 3M return, % within 10% of 52w high.

Run: py -3 scanner/research/regime_history.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from bhavcopy import fetch_nse_history, fetch_bse_history

# --- EXACT copy of scan.py's band() + weights so this matches the live score ---
def band(v, bear_lo, bear_hi, bull_lo, bull_hi):
    if v <= bear_lo: return 0.0
    if v >= bull_hi: return 10.0
    if v <= bear_hi: return (v - bear_lo) / (bear_hi - bear_lo) * 4
    if v >= bull_lo: return 6 + (v - bull_lo) / (bull_hi - bull_lo) * 4
    return 4 + (v - bear_hi) / (bull_lo - bear_hi) * 2


def main():
    print("[regime] fetching as much daily history as the mirrors hold...", flush=True)
    nse, _ = fetch_nse_history(days_back=900, min_bars=210)
    bse, _ = fetch_bse_history(days_back=820, min_bars=210)
    series = {}
    for d in (nse, bse):
        for t, df in d.items():
            series[t] = df["Close"].astype(float)
    print(f"[regime] {len(series)} stocks; building price matrix...", flush=True)

    P = pd.DataFrame(series).sort_index()
    P = P[~P.index.duplicated()]
    # NSE & BSE files use slightly different trading-day sets and the NSE mirror has a
    # ~33-day gap in mid-2025. In the merged matrix that leaves scattered NaN holes, and
    # rolling(200) needs 200 *consecutive* non-NaN -> SMA200 collapsed to NaN almost
    # everywhere (only a lucky 2023-07->2024-03 window ever survived). Forward-fill
    # (past-only, no look-ahead) bridges the interleave holes + the mid-2025 gap; a
    # genuinely delisted stock still drops out after 40 sessions and IPOs stay NaN
    # until they actually list. raw_n marks days where stocks really printed.
    raw_n = P.notna().sum(axis=1)
    Pf = P.ffill(limit=40)
    sma10, sma20, sma50, sma200 = (Pf.rolling(w).mean() for w in (10, 20, 50, 200))
    hi52 = Pf.rolling(252, min_periods=200).max()
    r1m = Pf / Pf.shift(21) - 1
    r3m = Pf / Pf.shift(63) - 1
    pct_off = (1 - Pf / hi52) * 100

    analyzed = sma200.notna() & Pf.notna()         # ~"has >=200 bars" = in the breadth set
    n = analyzed.sum(axis=1)
    def share(mask):
        return (mask & analyzed).sum(axis=1) / n.replace(0, np.nan) * 100
    pct10 = share(Pf > sma10); pct20 = share(Pf > sma20)
    pct50 = share(Pf > sma50); pct200 = share(Pf > sma200)
    near = share(pct_off <= 10)
    med_r1m = r1m.where(analyzed).median(axis=1) * 100
    med_r3m = r3m.where(analyzed).median(axis=1) * 100

    print(f"[diag] P {P.shape}, idx {P.index.min().date()}->{P.index.max().date()}, "
          f"analyzed days(n>=50)={int((n>=50).sum())}, real days(raw>=50)={int((raw_n>=50).sum())}", flush=True)
    rows = []
    for dt in P.index:
        if raw_n.loc[dt] < 50:                     # phantom / no-print day (e.g. today) -> skip
            continue
        nn = n.loc[dt]
        if not (nn >= 50):                         # need a real sample
            continue
        p200, p50, p20 = pct200.loc[dt], pct50.loc[dt], pct20.loc[dt]
        if any(pd.isna(x) for x in (p200, p50, p20)):
            continue
        s = (band(p200, 20, 40, 55, 75) * 0.30 + band(p50, 25, 45, 55, 75) * 0.22
             + band(p20, 30, 45, 55, 70) * 0.13 + band((med_r1m[dt] or 0), -5, -1, 1, 5) * 0.08
             + band((med_r3m[dt] or 0), -10, -2, 2, 10) * 0.17 + band(near[dt], 5, 12, 18, 30) * 0.10)
        rows.append((dt, round(s, 2), round(p200, 0), round(p50, 0), round(near[dt], 0)))

    hist = pd.DataFrame(rows, columns=["date", "regime", "pct200", "pct50", "near_high"]).set_index("date")
    print(f"[regime] computed {len(hist)} days: {hist.index.min().date()} -> {hist.index.max().date()}")
    print(f"[regime] latest = {hist['regime'].iloc[-1]} (live dashboard ~5.27 sanity check)")
    print(f"[regime] range min {hist['regime'].min()} / max {hist['regime'].max()} / avg {hist['regime'].mean():.2f}")

    bull = hist[hist["regime"] >= 7]
    print(f"\n===== BULLISH (regime 7-10): {len(bull)} of {len(hist)} days ({len(bull)/len(hist)*100:.0f}%) =====\n")
    # collapse consecutive bullish days into stretches
    if len(bull):
        ds = list(bull.index)
        runs = []; start = prev = ds[0]
        for d in ds[1:]:
            gap = (d - prev).days
            if gap <= 5:  # same stretch (weekends/holidays)
                prev = d
            else:
                runs.append((start, prev)); start = prev = d
        runs.append((start, prev))
        print(f"{'from':>12}  {'to':>12}  {'days':>4}  {'peak':>5}")
        for a, b in runs:
            seg = bull.loc[a:b]
            print(f"{a.date()!s:>12}  {b.date()!s:>12}  {len(seg):>4}  {seg['regime'].max():>5}")
        print("\n--- every bullish day ---")
        for d, row in bull.iterrows():
            print(f"{d.date()}  regime {row['regime']:>5}  (>SMA200 {row['pct200']:.0f}%, near-high {row['near_high']:.0f}%)")
    (ROOT / "research" / "regime_history.json").write_text(
        hist.reset_index().assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d")).to_json(orient="records"),
        encoding="utf-8")


if __name__ == "__main__":
    main()
