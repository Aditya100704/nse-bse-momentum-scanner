"""
VCP ranker — "read the chart" VCP detection over all current qualifiers (daily).

This is a proper Volatility Contraction Pattern scorer, not just the boolean flag.
For each qualifier it finds the base, walks the swing pivots, and scores:
  - 2-4 successive contractions, each TIGHTER than the last (the coil)
  - volume DRYING UP into the most recent contraction
  - price sitting NEAR the pivot (top of the base), ready to break
  - a real prior uptrend feeding the base (left side of the VCP)
  - final contraction genuinely tight
Outputs the top 20 with the numbers behind each call.

Daily bars (bhavcopy). VCP is fundamentally a daily/weekly pattern; hourly would
need intraday data and is a later add.

Run:  py -3 scanner/research/vcp_rank.py
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]   # scanner/
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from bhavcopy import fetch_nse_history, fetch_bse_history


def swing_points(high, low, order=3):
    """Return ordered list of (idx, price, 'H'|'L') fractal swing highs/lows."""
    n = len(high); pts = []
    for i in range(order, n - order):
        if high[i] == max(high[i - order:i + order + 1]):
            pts.append((i, high[i], "H"))
        elif low[i] == min(low[i - order:i + order + 1]):
            pts.append((i, low[i], "L"))
    # collapse consecutive same-type (keep the more extreme)
    out = []
    for p in pts:
        if out and out[-1][2] == p[2]:
            if (p[2] == "H" and p[1] >= out[-1][1]) or (p[2] == "L" and p[1] <= out[-1][1]):
                out[-1] = p
        else:
            out.append(p)
    return out


def vcp_score(df: pd.DataFrame) -> dict | None:
    if df is None or len(df) < 120 or "High" not in df.columns:
        return None
    high = df["High"].astype(float).values
    low = df["Low"].astype(float).values
    close = df["Close"].astype(float).values
    vol = df["Volume"].astype(float).values
    n = len(close)
    last = close[-1]

    # base window: last ~13 weeks (65 bars), but at least 25
    win = min(65, n - 1)
    h, l, c, v = high[-win:], low[-win:], close[-win:], vol[-win:]
    base_high = h.max()
    pct_to_pivot = (base_high - last) / base_high * 100      # how far below the pivot

    # prior uptrend feeding the base: 6M return into the base start
    prior = (close[-win] / close[-min(n - 1, win + 120)] - 1) * 100 if n > win + 5 else 0

    pts = swing_points(h, l, order=3)
    # contractions = each swing-High followed by a swing-Low: depth %
    contractions = []
    for i in range(len(pts) - 1):
        if pts[i][2] == "H" and pts[i + 1][2] == "L":
            peak, trough = pts[i][1], pts[i + 1][1]
            if peak > 0:
                contractions.append((peak - trough) / peak * 100)
    if len(contractions) < 2:
        return {"contractions": len(contractions), "score": 0, "pct_to_pivot": pct_to_pivot,
                "depths": [round(x, 1) for x in contractions], "vol_dryup": None,
                "base_len": win, "prior_6m": round(prior, 0)}

    contractions = [d for d in contractions if d > 0]      # ignore "negative" (price made higher high)
    if len(contractions) < 2:
        return None
    last_depth = contractions[-1]
    prev_depth = contractions[-2]
    # proper VCP: the final contraction is the tightest (the coil). Reward that.
    final_tightest = last_depth <= min(contractions[:-1]) + 0.5
    tightening = last_depth <= prev_depth * 1.05
    shrink_steps = sum(1 for a, b in zip(contractions, contractions[1:]) if b < a * 1.05)
    shrink_frac = shrink_steps / max(1, len(contractions) - 1)

    # volume dry-up: avg vol of last 10 bars vs first half of the base
    recent_v = v[-10:].mean()
    base_v = v[: max(10, win // 2)].mean()
    vol_ratio = recent_v / base_v if base_v > 0 else 1.0
    vol_dryup = vol_ratio < 0.95

    # ---- score 0-100 ----
    s = 0.0
    s += {2: 16, 3: 22, 4: 22}.get(len(contractions), 14 if len(contractions) > 4 else 0)
    s += 18 * shrink_frac
    s += 14 if final_tightest else (6 if tightening else 0)   # the coil tightens into the pivot
    s += 18 if last_depth <= 8 else 10 if last_depth <= 12 else 3 if last_depth <= 18 else 0
    s += 14 * max(0, min(1, (1.05 - vol_ratio) / 0.35))
    s += 14 if pct_to_pivot <= 4 else 9 if pct_to_pivot <= 8 else 3 if pct_to_pivot <= 12 else 0
    s += 6 if prior >= 25 else 3 if prior >= 10 else 0
    return {
        "final_tightest": bool(final_tightest),
        "score": round(min(100, s)),
        "contractions": len(contractions),
        "depths": [round(x, 1) for x in contractions[-4:]],
        "last_depth": round(last_depth, 1),
        "tighten_frac": round(shrink_frac, 2),
        "vol_ratio": round(vol_ratio, 2),
        "vol_dryup": bool(vol_dryup),
        "pct_to_pivot": round(pct_to_pivot, 1),
        "base_len": int(win),
        "prior_6m": round(float(prior), 0),
    }


def main():
    scan = json.loads((ROOT / "data" / "scanner_output.json").read_text(encoding="utf-8"))
    quals = scan["results"]
    by_ticker = {r["yf_ticker"]: r for r in quals if r.get("yf_ticker")}
    want = set(by_ticker)
    print(f"[vcp] {len(want)} qualifiers; fetching daily OHLC...", flush=True)

    prices = {}
    nse, _ = fetch_nse_history(days_back=300)
    prices.update({t: d for t, d in nse.items() if t in want})
    missing = want - set(prices)
    if missing:
        bse, _ = fetch_bse_history(days_back=300)
        prices.update({t: d for t, d in bse.items() if t in want})
    print(f"[vcp] got OHLC for {len(prices)}/{len(want)}", flush=True)

    # Exclude ETFs / index / liquid funds — they coil "tightly" by construction but
    # aren't tradeable VCPs. Filter by name keywords + near-zero ADR.
    BAD = ("ETF", "BEES", "IETF", "FUND", "LIQUID", "GILT", "GOLD", "SILVER", "NIFTY",
           "SENSEX", "NASDAQ", "S&P", "INDEX", "MAFANG", " 100", "MOMENTUM 30", "BANKBEES",
           "TARGET MATURITY", "1D RATE", "OVERNIGHT")
    def is_fund(name, adr):
        nm = (name or "").upper()
        return any(b in nm for b in BAD) or (adr is not None and adr < 1.0)

    seen, scored = set(), []
    for t, r in by_ticker.items():
        sym = r["symbol"].upper()
        if sym in seen:
            continue
        if is_fund(r.get("name"), r.get("adr_pct") if r.get("adr_pct") is not None else r.get("adr")):
            continue
        # VCP is a LEADER pattern — require real relative strength + a real mover
        if (r.get("rs_rating") or 0) < 60:
            continue
        if (r.get("adr_pct") or 0) < 2.0:
            continue
        df = prices.get(t)
        sc = vcp_score(df)
        if sc and sc["score"] >= 55 and sc.get("pct_to_pivot", 99) <= 12 and sc.get("last_depth", 99) <= 15:
            seen.add(sym)
            scored.append({"symbol": r["symbol"], "name": r.get("name", ""),
                           "exchange": r["exchange"], "rs": r.get("rs_rating"),
                           "adr": r.get("adr_pct"), **sc})
    scored.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n===== TOP 20 VCP SETUPS (of {len(scored)} scored) =====\n")
    print(f"{'#':>2} {'SYMBOL':<12}{'RS':>3}{'ADR':>6}  {'score':>5}  {'contr':>5} "
          f"{'depths (tightening %)':<24}{'last%':>6}{'volX':>6}{'toPivot':>8}  name")
    for i, x in enumerate(scored[:20], 1):
        depths = " > ".join(f"{d}" for d in x["depths"])
        print(f"{i:>2} {x['symbol']:<12}{x['rs']:>3}{(x['adr'] or 0):>6.1f}  {x['score']:>5}  "
              f"{x['contractions']:>5} {depths:<24}{x['last_depth']:>6}{x['vol_ratio']:>6}"
              f"{x['pct_to_pivot']:>7}%  {x['name'][:26]}")

    out = ROOT / "research" / "vcp_top.json"
    out.write_text(json.dumps(scored[:40], indent=2), encoding="utf-8")
    print(f"\n[vcp] full top-40 -> {out}")


if __name__ == "__main__":
    main()
