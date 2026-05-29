"""
Last VCP buy point per stock — the MOST RECENT real breakout, not the current high.

The earlier ranker reported base_high ~= current price for stocks already at new
highs (useless). This finds, for each name, the latest bar where price broke OUT
of a consolidation: it closed above a level that had capped it for >= LOOKBACK
bars (the pivot), out of a contained base. Reports the breakout date, the pivot
(the price you'd have bought a break above), price then, and how far it's run since.

Run: py -3 scanner/research/vcp_last_buy.py
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from bhavcopy import fetch_nse_history, fetch_bse_history

LOOKBACK = 20          # bars that define the resistance/base
MAX_BASE_DEPTH = 0.22  # base must be contained within ~22% (a real consolidation)
MIN_BASE_BARS = 10     # price must have been capped this long before the break


def last_breakout(df: pd.DataFrame):
    if df is None or len(df) < 80 or "High" not in df.columns:
        return None
    high = df["High"].astype(float).values
    low = df["Low"].astype(float).values
    close = df["Close"].astype(float).values
    vol = df["Volume"].astype(float).values
    idx = df.index
    n = len(close)
    avgvol = pd.Series(vol).rolling(50).mean().values

    # walk backward → first (most recent) qualifying breakout
    for i in range(n - 1, LOOKBACK + 5, -1):
        base_hi = high[i - LOOKBACK:i].max()         # resistance before this bar
        base_lo = low[i - LOOKBACK:i].min()
        if close[i] > base_hi and close[i - 1] <= base_hi:   # first close above the pivot
            depth = (base_hi - base_lo) / base_hi if base_hi else 1
            if depth > MAX_BASE_DEPTH:
                continue
            # the pivot should have been touched/approached more than once (a real ceiling)
            touches = int(np.sum(high[i - LOOKBACK:i] >= base_hi * 0.97))
            if touches < 2:
                continue
            volx = vol[i] / avgvol[i] if avgvol[i] and avgvol[i] > 0 else None
            return {
                "date": idx[i].strftime("%Y-%m-%d"),
                "pivot": round(float(base_hi), 2),
                "px_then": round(float(close[i]), 2),
                "days_ago": int(n - 1 - i),
                "base_depth": round(depth * 100, 1),
                "volx": round(volx, 1) if volx else None,
                "cur": round(float(close[-1]), 2),
                "since_pct": round((close[-1] / base_hi - 1) * 100, 1),
            }
    return None


def main():
    scan = json.loads((ROOT / "data" / "scanner_output.json").read_text(encoding="utf-8"))
    # map symbol -> (symbol, exchange, yf_ticker) from the scan (vcp_top.json lacks yf_ticker)
    sym2tk = {r["symbol"].upper(): (r["symbol"], r["exchange"], r.get("yf_ticker"))
              for r in scan["results"] if r.get("yf_ticker")}
    vcp = json.loads((ROOT / "research" / "vcp_top.json").read_text(encoding="utf-8"))
    order, seen = [], set()
    for v in vcp:
        s = v["symbol"].upper()
        if s in sym2tk and s not in seen:
            seen.add(s); order.append(sym2tk[s])
    want = {t for _, _, t in order}

    prices = {}
    nse, _ = fetch_nse_history(days_back=320)
    prices.update({t: d for t, d in nse.items() if t in want})
    if want - set(prices):
        bse, _ = fetch_bse_history(days_back=320)
        prices.update({t: d for t, d in bse.items() if t in want})

    print(f"\n{'SYMBOL':<12}{'last buy date':>14}{'pivot(buy>)':>12}{'days ago':>9}"
          f"{'base%':>7}{'volx':>6}{'now':>11}{'since':>8}")
    print("-" * 80)
    rows = []
    for sym, ex, t in order:
        b = last_breakout(prices.get(t))
        if not b:
            print(f"{sym:<12}{'(no clean base found)':>40}")
            continue
        rows.append((sym, ex, b))
        print(f"{sym:<12}{b['date']:>14}{b['pivot']:>12.2f}{b['days_ago']:>8}d"
              f"{b['base_depth']:>6.1f}%{(b['volx'] or 0):>6.1f}{b['cur']:>11.2f}{b['since_pct']:>7.1f}%")

    out = ROOT / "research" / "vcp_last_buy.json"
    out.write_text(json.dumps([{"symbol": s, "exchange": e, **b} for s, e, b in rows], indent=2), encoding="utf-8")
    print(f"\n[done] {len(rows)} breakouts -> {out}")


if __name__ == "__main__":
    main()
