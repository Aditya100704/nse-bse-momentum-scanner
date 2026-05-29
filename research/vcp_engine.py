"""
VCP engine v3 — recent launch/continuation setups with a buy-pivot and a STATUS.

Calibrated against the user's own chart reads (1h/1D views, daily-bar detection):
  YES  HFCL 74.6, BLISSGVS 278, BAJAJCON 370 (triangle apex), NAVINFLUOR ~7200 ("good"),
       IFCI ~65, CPPLUS ~2567 ("maybe")
  NO   DIACABS (deep V-recovery), USHAMART (loose, rising-resistance range = "no setup")
  WATCH RBLBANK ~347 ("no trade yet" — coiling under resistance, not broken)

A good VCP (user's words, now encoded):
  1. TIGHTNESS ON THE RIGHT  -> the final contraction (right half of the base) is tight and
     no wider than the left half (right_range <= MAX_RIGHT_RANGE and <= left_range).
  2. SURFING AN MA           -> a 10/20/50-day SMA threads the base as support and the 50-day
     is rising (the stock is riding a moving average up).
  3. SYMMETRY / FLAT CEILING -> the resistance is flat or descending, NOT a rising wedge
     (ceiling_rise <= MAX_CEILING_RISE). This is what kills USHAMART.
  4. EXPANSION               -> breakout bar prints volume/range expansion (recorded as volx).

STATUS vs the current price:
  COILING   = price is pressing the pivot but has NOT closed above it -> buy > pivot (watch).
  TRIGGERED = broke the pivot recently (<=~7%, <=12 bars) and held -> still in the buy zone.
  EXTENDED  = broke and already ran far -> the pivot is just the historical launch level.

Run:  py -3 research/vcp_engine.py                 # all 40, with status
      py -3 research/vcp_engine.py HFCL RBLBANK    # only these (verbose calibration)
      py -3 research/vcp_engine.py --refresh       # refetch the price panel first
"""
from __future__ import annotations
import sys, json, pickle
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from bhavcopy import fetch_nse_history

PRICE_CACHE = ROOT / "research" / "_vcp_prices.pkl"

# ---- tunables (calibrated to the 9 hand-checked charts) ----
TS          = (10, 12, 15, 18, 22, 26)   # base lengths to try (daily bars)
DET_PCT     = 80      # percentile of base highs used to DETECT the breakout
REPORT_PCT  = 90      # percentile REPORTED as the pivot (the line a trader draws)
MAX_DEPTH   = 0.16    # whole-base hi-lo range / pivot
MAX_DRIFT   = 0.09    # |net close move across the base| / pivot  (sideways, balanced)
MIN_TOUCHES = 6       # highs tagging the resistance (a real, repeatedly-tested shelf)
MAX_RIGHT_RNG = 0.085 # TIGHTNESS ON THE RIGHT: range of the base's right half / pivot
MAX_CLOSE_RNG = 0.075 # CLOSE tightness: range of the base's CLOSES / pivot. A clean base has
                      # closes packed in a band; a deep V-dip-and-recover (LLOYDSME, 1800->1650
                      # ->1870) or a choppy range (LLOYDSENT) has loose closes -> rejected. This
                      # catches what hi-lo range + NET drift miss (same start/end hides a deep dip).
MAX_CEIL_RISE = 0.03  # resistance must be flat/descending, not a rising wedge (kills USHAMART)
NEAR_FRAC   = 0.78    # pivot >= this * 180-bar high  (near the highs, not a bottom)
BUF         = 0.005   # breakout = close > pivot*(1+BUF)
HOLD_GRACE  = 0.93    # after breakout, no close back below pivot*HOLD_GRACE (it held)
LEG_LOOKBACK= 190
MIN_RISE    = 0.12    # stock up >= this off the leg low (a real advance exists)
COIL_LO     = 0.92    # COILING if cur in [pivot_det*COIL_LO, pivot_det*(1+BUF)]
TRIG_SINCE  = 7.0     # TRIGGERED if <= this % above pivot ...
TRIG_DAYS   = 12      # ... and broke out within this many bars
TH_1H       = 7.0     # 1h CONFIRM: base's close-range on hourly bars must be <= this %
                      # (POLYCAB 4.1 / NAVINFLUOR 5.9 / KEI 6.3 confirm; LLOYDSME 11 / USHAMART
                      # 10.4 / LLOYDSENT 9.2 = user "no setup" -> WEAK-1h. Clean gap at ~7%.)


def _smas(C):
    s = pd.Series(C)
    return (s.rolling(10, min_periods=8).mean().values,
            s.rolling(20, min_periods=15).mean().values,
            s.rolling(50, min_periods=40).mean().values,
            s.rolling(200, min_periods=150).mean().values)


def _base_quality(H, L, C, sma10, sma20, sma50, s, e, det_pct, report_pct):
    """Validate the base window [s, e] against the VCP quality rules; return metrics or None."""
    Hw, Lw, Cw = H[s:e + 1], L[s:e + 1], C[s:e + 1]
    T = e - s + 1
    R = float(np.percentile(Hw, det_pct))
    if R <= 0:
        return None
    depth = (Hw.max() - Lw.min()) / R
    drift = abs(Cw[-1] - Cw[0]) / R
    close_rng = (Cw.max() - Cw.min()) / R          # how tightly the CLOSES cluster
    touches = int((Hw >= R * 0.985).sum())
    if depth > MAX_DEPTH or drift > MAX_DRIFT or touches < MIN_TOUCHES:
        return None
    if close_rng > MAX_CLOSE_RNG:                   # loose/choppy or deep-V base -> not clean
        return None
    h = T // 2
    HL, LL = H[s:s + h], L[s:s + h]                  # left half
    HR, LR = H[e - h + 1:e + 1], L[e - h + 1:e + 1]  # right half
    left_rng = (HL.max() - LL.min()) / R
    right_rng = (HR.max() - LR.min()) / R
    ceil_rise = HR.max() / HL.max() - 1
    if right_rng > MAX_RIGHT_RNG:                    # (1) tightness on the right
        return None
    if right_rng > left_rng * 1.05:                 # right no wider than left (contraction)
        return None
    if ceil_rise > MAX_CEIL_RISE:                   # (3) flat/descending ceiling, not a rising wedge
        return None
    base_lo, base_hi = Lw.min(), Hw.max()           # (2) surfing an MA
    mas = [sma10[e], sma20[e], sma50[e]]
    ma_in_base = any(base_lo * 0.97 <= m <= base_hi for m in mas if not np.isnan(m))
    rising50 = (not np.isnan(sma50[e]) and not np.isnan(sma50[max(0, e - 15)])
                and sma50[e] >= sma50[max(0, e - 15)] * 0.99)
    above_ma = not np.isnan(sma50[e]) and C[e] > sma50[e] * 0.99
    if not (ma_in_base and rising50 and above_ma):
        return None
    Rrep = float(np.percentile(Hw, report_pct))
    return dict(R=round(Rrep, 2), Rdet=round(R, 2), depth=round(depth * 100, 1),
                drift=round(drift * 100, 1), touches=touches, T=T,
                right_rng=round(right_rng * 100, 1), left_rng=round(left_rng * 100, 1),
                close_rng=round(close_rng * 100, 1), ceil_rise=round(ceil_rise * 100, 1))


def scan(df, *, verbose=False):
    if df is None or len(df) < 120 or "High" not in df.columns:
        return None
    H = df["High"].astype(float).values
    L = df["Low"].astype(float).values
    C = df["Close"].astype(float).values
    V = df["Volume"].astype(float).values
    idx = df.index
    n = len(C)
    avgvol = pd.Series(V).rolling(50, min_periods=20).mean().values
    sma10, sma20, sma50, sma200 = _smas(C)
    cur = C[-1]
    lo_region = max(0, n - LEG_LOOKBACK)
    leg_low_i = lo_region + int(np.argmin(C[lo_region:]))
    if cur / C[leg_low_i] - 1 < MIN_RISE:
        return None

    cands = []
    for e in range(n - 1, max(leg_low_i + 9, n - 55) - 1, -1):   # base right edge
        for T in TS:
            s = e - T + 1
            if s < 55 or s <= leg_low_i - 2:
                continue
            q = _base_quality(H, L, C, sma10, sma20, sma50, s, e, DET_PCT, REPORT_PCT)
            if not q:
                continue
            R, Rrep = q["Rdet"], q["R"]
            hi180 = H[max(0, e - 180):e + 1].max()
            if Rrep < NEAR_FRAC * hi180:
                continue
            after = C[e + 1:]
            bk = np.where(after > R * (1 + BUF))[0]
            if len(bk) == 0:                                     # not broken since base end
                if not (R * COIL_LO <= cur <= R * (1 + BUF)):
                    continue
                if np.isnan(sma200[e]) or C[e] <= sma200[e]:
                    continue
                status, k, days = "COILING", None, int(n - 1 - e)
                volx = None
            else:
                k = e + 1 + int(bk[0])
                if C[k:].min() < R * HOLD_GRACE:                 # held
                    continue
                if np.isnan(sma200[k]) or C[k] <= sma200[k]:
                    continue
                days = int(n - 1 - k)
                since = (cur / Rrep - 1) * 100
                status = "TRIGGERED" if (since <= TRIG_SINCE and days <= TRIG_DAYS) else "EXTENDED"
                volx = round(float(V[k] / avgvol[k]), 1) if avgvol[k] and avgvol[k] > 0 else None
            rank = {"COILING": 0, "TRIGGERED": 1, "EXTENDED": 2}[status]
            qscore = q["touches"] - q["right_rng"] - q["ceil_rise"]   # tighter+flatter+more-tested = better
            cands.append(dict(status=status, rank=rank, R=Rrep, Rdet=R, date=idx[k].strftime("%Y-%m-%d") if k else idx[e].strftime("%Y-%m-%d"),
                              days=days, since_pct=round(float((cur / Rrep - 1) * 100), 1),
                              touches=q["touches"], depth=q["depth"], right_rng=q["right_rng"],
                              close_rng=q["close_rng"], ceil_rise=q["ceil_rise"], T=q["T"], volx=volx,
                              cur=round(float(cur), 2), qscore=round(qscore, 1), e=e))
    if verbose:
        for c in sorted(cands, key=lambda x: (x["rank"], -x["qscore"]))[:14]:
            print(f"      {c['status']:<10} pivot={c['R']:.2f} T={c['T']} touch={c['touches']} "
                  f"closeRng={c['close_rng']}% rightRng={c['right_rng']}% ceilRise={c['ceil_rise']}% "
                  f"since={c['since_pct']}% days={c['days']}")
    if not cands:
        return None
    # most actionable first (COILING>TRIGGERED>EXTENDED), then best quality, then most recent
    cands.sort(key=lambda c: (c["rank"], -c["qscore"], -c["e"]))
    return cands[0]


def load_prices(want, refresh=False):
    if not refresh and PRICE_CACHE.exists():
        data = pickle.loads(PRICE_CACHE.read_bytes())
        if not (want - set(data)):
            latest = max(d.index.max() for d in data.values())
            print(f"[cache] {len(data)} symbols, latest bar {latest.date()} (use --refresh to refetch)")
            return data
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=6)
    data = {}
    for attempt in range(3):
        nse, _ = fetch_nse_history(days_back=400, min_bars=120)
        cand = {t: d for t, d in nse.items() if t in want}
        if not cand:
            continue
        latest = max(d.index.max() for d in cand.values())
        missing = len(want - set(cand))
        print(f"[fetch {attempt + 1}] {len(cand)}/{len(want)} symbols, latest {latest.date()}, missing {missing}")
        if latest >= cutoff and missing == 0:
            data = cand
            break
        data = cand if len(cand) > len(data) else data
    if data:
        PRICE_CACHE.write_bytes(pickle.dumps(data))
    return data


def confirm_1h(rows, th=TH_1H, win=36, bpd=6):
    """Hybrid 1h confirmation on the shortlist (coiling + triggered): re-measure the base's
    CLOSE-tightness on hourly bars (the user judges on 1h). For COILING use the recent hourly
    window (the live coil); for TRIGGERED look back to the pre-breakout window (~`days` sessions).
    Adds tf1h = CONFIRMED / WEAK-1h / NA and the 1h close-range cr1h. yfinance 60m source."""
    short = [r for r in rows if r["status"] in ("COILING", "TRIGGERED")]
    if not short:
        return
    try:
        import yfinance as yf
        dl = yf.download([r["symbol"] + ".NS" for r in short], period="2mo", interval="60m",
                         progress=False, auto_adjust=True, group_by="ticker", threads=True)
    except Exception as e:
        print(f"[1h] fetch failed ({e}); skipping 1h confirm", flush=True)
        return
    for r in short:
        try:
            c = dl[r["symbol"] + ".NS"]["Close"].dropna().values.astype(float)
        except Exception:
            c = None
        if c is None or len(c) < 50:
            r["tf1h"], r["cr1h"] = "NA", None
            continue
        off = 0 if r["status"] == "COILING" else int(r["days"])     # coiling = live window
        be = max(win, min(len(c) - 1 - round(off * bpd), len(c) - 1))
        base = c[be - win:be + 1]
        cr = float((base.max() - base.min()) / np.percentile(base, 90) * 100)
        r["cr1h"] = round(cr, 1)
        r["tf1h"] = "CONFIRMED" if cr <= th else "WEAK-1h"


def main():
    args = sys.argv[1:]
    refresh = "--refresh" in args
    no1h = "--no1h" in args
    only = [a.upper() for a in args if not a.startswith("--")]
    vcp = json.loads((ROOT / "research" / "vcp_top.json").read_text(encoding="utf-8"))
    all_syms = [v["symbol"].upper() for v in vcp]
    order = (([s for s in all_syms if s in only] or only) if only else all_syms)
    prices = load_prices({f"{s}.NS" for s in all_syms}, refresh=refresh)

    rows, rejected = [], []
    for sym in order:
        df = prices.get(f"{sym}.NS")
        if only:
            print(f"  [{sym}]")
        b = scan(df, verbose=bool(only))
        if not b:
            rejected.append(sym)
            continue
        rows.append({"symbol": sym, "exchange": "NSE", **{k: v for k, v in b.items() if k not in ("rank", "e", "qscore")}})

    if not no1h:
        confirm_1h(rows)

    print(f"\n{'SYMBOL':<12}{'STATUS':<11}{'BUY >':>11}{'now':>10}{'vs piv':>8}"
          f"{'touch':>6}{'1h':>11}{'days':>6}")
    print("-" * 76)
    for b in rows:
        print(f"{b['symbol']:<12}{b['status']:<11}{b['R']:>11.2f}{b['cur']:>10.2f}{b['since_pct']:>7.1f}%"
              f"{b['touches']:>6}{b.get('tf1h', '-'):>11}{b['days']:>6}")
    for sym in rejected:
        print(f"{sym:<12}{'(no clean VCP setup)':>44}")

    out = ROOT / "research" / "vcp_engine.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    coil = sum(1 for r in rows if r["status"] == "COILING")
    trig = sum(1 for r in rows if r["status"] == "TRIGGERED")
    ext = sum(1 for r in rows if r["status"] == "EXTENDED")
    conf = sum(1 for r in rows if r.get("tf1h") == "CONFIRMED")
    print(f"\n[done] {len(rows)} setups ({coil} coiling, {trig} triggered, {ext} extended); "
          f"{conf} 1h-CONFIRMED; {len(rejected)} rejected -> {out}")
    if rejected:
        print("rejected:", ", ".join(rejected))


if __name__ == "__main__":
    main()
