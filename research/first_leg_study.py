"""
First-leg target study
=======================
Empirical question (from the user): is there a reliable way to estimate the size
of the FIRST LEG of a breakout move (entry -> first swing high, before the first
pullback) so we can set a target between 1:2 and 1:4 R, instead of a fixed 1:3?

Method
------
- Pull full daily OHLCV for the whole NSE universe from the bhavcopy GitHub mirror
  (production bhavcopy.py only keeps Close+Volume; we need High/Low here).
- Detect breakouts: first close above the prior LOOKBACK-day high, in an uptrend
  (close > SMA50 > SMA200), liquid (>= MIN_TURNOVER cr), within 25% of 52w high.
- For each breakout measure the first leg = max-favourable-excursion before price
  pulls back PB x ATR from its running peak. Express it in R (R = stop distance).
- Test predictors (ATR%, base depth, vol surge, distance above SMA50, 1M return,
  distance from 52w high) via correlation + bucketed first-leg medians + hit rates.
- Compare expectancy of fixed 2R / 3R / 4R targets vs an adaptive rule.

Run: py -3 scanner/research/first_leg_study.py
Writes: backtests/<date>_first_leg_target_study.md  (+ prints a summary)

Caveat: NSE bhavcopy is raw (not split-adjusted); we drop single-bar moves > 35%
as likely splits/bonuses so they don't fake breakouts or legs.
"""
from __future__ import annotations

import io
import sys
import time
import json
import concurrent.futures as cf
from datetime import date, timedelta, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]   # trading/
BACKTESTS = ROOT / "backtests"
BACKTESTS.mkdir(exist_ok=True)

MIRROR = "https://raw.githubusercontent.com/tilak999/NSE-Data-bank/main/data/sec_bhavdata_full_{}.csv"
EQUITY_SERIES = {"EQ", "BE"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; PhenomResearch/1.0)"}
_S = requests.Session(); _S.headers.update(UA)
_S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64, max_retries=2))

# ---------------- params ----------------
DAYS_BACK     = 540
LOOKBACK      = 50      # breakout = first close above prior 50-day high
FWD_WINDOW    = 40      # bars to track the first leg / target hits
ATR_N         = 14
MIN_TURNOVER  = 2.0     # ₹cr, 20-day avg, at breakout
MAX_OFF_HIGH  = 25.0    # within 25% of 52w high
SPLIT_GUARD   = 0.35    # drop bars with |1d move| > 35% (likely split/bonus)
PB_LEVELS     = [1.5, 2.0, 2.5]   # pullback = PB x ATR off the running peak
PB_MAIN       = 2.0
STOP_DEFS     = {"fixed4pct": "4% below entry", "atr1_5": "1.5x ATR below entry", "baselow": "10-day low"}
TARGETS       = [2.0, 3.0, 4.0]


# ---------------- data ----------------
def _trading_days(days_back: int) -> list[date]:
    out, d = [], date.today()
    while len(out) < days_back + 50:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def _fetch_one(d: date):
    url = MIRROR.format(d.strftime("%d%m%Y"))
    try:
        r = _S.get(url, timeout=25)
        if r.status_code != 200 or "SYMBOL" not in r.text[:200]:
            return None
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        df["SERIES"] = df["SERIES"].astype(str).str.strip()
        df = df[df["SERIES"].isin(EQUITY_SERIES)]
        if df.empty:
            return None
        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
        return pd.DataFrame({
            "symbol": df["SYMBOL"], "date": pd.Timestamp(d),
            "open":  pd.to_numeric(df["OPEN_PRICE"], errors="coerce"),
            "high":  pd.to_numeric(df["HIGH_PRICE"], errors="coerce"),
            "low":   pd.to_numeric(df["LOW_PRICE"], errors="coerce"),
            "close": pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
            "volume": pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
        }).dropna(subset=["close", "high", "low"])
    except Exception:
        return None


def fetch_ohlc(days_back: int, min_bars: int = 260):
    days = _trading_days(days_back)
    frames, got = [], 0
    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        for res in ex.map(_fetch_one, days):
            if res is not None:
                frames.append(res); got += 1
    print(f"[data] fetched {got} daily files", flush=True)
    big = pd.concat(frames, ignore_index=True)
    out = {}
    for sym, g in big.groupby("symbol"):
        g = g.sort_values("date").drop_duplicates("date").set_index("date")
        g = g[(g["close"] > 0) & (g["high"] >= g["low"])]
        if len(g) >= min_bars:
            out[sym] = g
    print(f"[data] {len(out)} symbols with >= {min_bars} bars", flush=True)
    return out


# ---------------- per-symbol breakout scan ----------------
def scan_symbol(sym: str, g: pd.DataFrame) -> list[dict]:
    o = g["open"].values; h = g["high"].values; l = g["low"].values
    c = g["close"].values; v = g["volume"].values
    n = len(c)
    if n < 220 + FWD_WINDOW:
        return []

    # split guard: mask bars with absurd 1-day moves
    ret1 = np.empty(n); ret1[0] = 0
    ret1[1:] = c[1:] / c[:-1] - 1
    if np.nanmax(np.abs(ret1)) > SPLIT_GUARD:
        # zero out those bars' influence by clipping highs/lows later isn't trivial;
        # simplest: reject symbols with any monster bar (likely unadjusted split)
        if np.sum(np.abs(ret1) > SPLIT_GUARD) > 0:
            return []

    sma50 = pd.Series(c).rolling(50).mean().values
    sma200 = pd.Series(c).rolling(200).mean().values
    prior_high = pd.Series(h).rolling(LOOKBACK).max().shift(1).values
    high252 = pd.Series(h).rolling(252, min_periods=200).max().values
    turnover_cr = (pd.Series(c * v).rolling(20).mean() / 1e7).values
    # ATR (Wilder-ish, simple mean of true range)
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr = np.full(n, np.nan)
    atr[1:] = pd.Series(tr).rolling(ATR_N).mean().values
    base_min20 = pd.Series(l).rolling(20).min().shift(1).values
    base_max20 = pd.Series(h).rolling(20).max().shift(1).values
    low10 = pd.Series(l).rolling(10).min().values
    vol50 = pd.Series(v).rolling(50).mean().values

    events = []
    last_event = -999
    for i in range(210, n - FWD_WINDOW):
        if np.isnan(prior_high[i]) or np.isnan(sma200[i]) or np.isnan(atr[i]):
            continue
        # breakout = first close above prior high (not every day above)
        if not (c[i] > prior_high[i] and c[i-1] <= prior_high[i-1]):
            continue
        # uptrend + liquidity + near 52w high
        if not (c[i] > sma50[i] > sma200[i]):
            continue
        if not (turnover_cr[i] >= MIN_TURNOVER):
            continue
        off_high = (1 - c[i] / high252[i]) * 100 if high252[i] > 0 else 100
        if off_high > MAX_OFF_HIGH:
            continue
        if i - last_event < 10:          # don't double-count overlapping breakouts
            continue
        last_event = i

        entry = c[i]
        atr_i = atr[i]
        if atr_i <= 0:
            continue
        # features (all known at/just-after the breakout close)
        base_depth = (base_max20[i] - base_min20[i]) / base_min20[i] * 100 if base_min20[i] > 0 else np.nan
        feat = {
            "atr_pct": atr_i / entry * 100,
            "base_depth_pct": base_depth,
            "vol_surge": v[i] / vol50[i] if vol50[i] > 0 else np.nan,
            "dist_sma50_pct": (entry / sma50[i] - 1) * 100,
            "dist_52wh_pct": off_high,
            "r1m_pct": (c[i] / c[i-21] - 1) * 100 if i >= 21 else np.nan,
            "turnover_cr": turnover_cr[i],
        }

        # stop definitions -> R (risk per share)
        stops = {
            "fixed4pct": entry * 0.04,
            "atr1_5": 1.5 * atr_i,
            "baselow": max(entry - low10[i], 1e-9),
        }

        # forward walk
        hi = h[i+1:i+1+FWD_WINDOW]; lo = l[i+1:i+1+FWD_WINDOW]; cl = c[i+1:i+1+FWD_WINDOW]
        # first leg for each pullback level (in price), measured as running-peak MFE
        legs = {}
        for pb in PB_LEVELS:
            run_peak = entry
            leg_top = entry
            for t in range(len(hi)):
                run_peak = max(run_peak, hi[t])
                leg_top = max(leg_top, run_peak)
                if lo[t] <= run_peak - pb * atr_i:   # pulled back pb*ATR from peak
                    break
            legs[f"leg_pb{pb}"] = (leg_top - entry)

        # target/stop path outcomes per stop-def + target (stop checked before target in-bar)
        outcomes = {}
        for sname, R in stops.items():
            stop_px = entry - R
            for T in TARGETS:
                tgt_px = entry + T * R
                res = None
                for t in range(len(hi)):
                    if lo[t] <= stop_px:
                        res = -1.0; break
                    if hi[t] >= tgt_px:
                        res = T; break
                if res is None:                      # neither hit in window -> mark to last close
                    res = (cl[-1] - entry) / R
                outcomes[f"{sname}_T{T}"] = res
            # first-leg in R for this stop
            outcomes[f"{sname}_legR_pb{PB_MAIN}"] = legs[f"leg_pb{PB_MAIN}"] / R
            # max excursion in R over the whole window (ceiling on achievable target)
            outcomes[f"{sname}_mfeR"] = (np.max(hi) - entry) / R

        events.append({"symbol": sym, "i": i, "date": g.index[i].strftime("%Y-%m-%d"),
                       "entry": entry, **feat, **legs, **outcomes})
    return events


# ---------------- analysis ----------------
def pct(a, q):
    a = np.asarray(a, float); a = a[~np.isnan(a)]
    return float(np.percentile(a, q)) if len(a) else float("nan")


def main():
    t0 = time.time()
    data = fetch_ohlc(DAYS_BACK)
    all_events = []
    for k, (sym, g) in enumerate(data.items()):
        all_events.extend(scan_symbol(sym, g))
        if k % 500 == 0:
            print(f"[scan] {k}/{len(data)} symbols, {len(all_events)} breakouts so far", flush=True)
    df = pd.DataFrame(all_events)
    print(f"[scan] DONE — {len(df)} breakout events from {len(data)} symbols in {time.time()-t0:.0f}s", flush=True)
    if df.empty:
        print("no events"); return

    rep = []
    def out(s=""): print(s, flush=True); rep.append(s)

    out(f"# First-leg target study — {datetime.now():%Y-%m-%d}")
    out("")
    out(f"- Universe: NSE bhavcopy OHLC, ~{DAYS_BACK} trading days")
    out(f"- Breakout = first close above prior {LOOKBACK}-day high, close>SMA50>SMA200, "
        f">= ₹{MIN_TURNOVER}cr/day, within {MAX_OFF_HIGH}% of 52w high")
    out(f"- **{len(df):,} breakout events** across {df['symbol'].nunique():,} symbols")
    out(f"- First leg = max-favourable-excursion before a {PB_MAIN}×ATR pullback; "
        f"R = stop distance; window = {FWD_WINDOW} bars")
    out("")

    # ---- 1. first-leg distribution in R, per stop definition ----
    out("## 1. How big is the first leg, in R?")
    out("")
    out("| Stop def | P10 | P25 | median | P75 | P90 | mean | % leg ≥2R | % ≥3R | % ≥4R |")
    out("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for sname, desc in STOP_DEFS.items():
        col = f"{sname}_legR_pb{PB_MAIN}"
        a = df[col].replace([np.inf, -np.inf], np.nan).dropna()
        a = a[(a > -2) & (a < 50)]
        ge = lambda k: f"{(a>=k).mean()*100:.0f}%"
        out(f"| {sname} ({desc}) | {pct(a,10):.1f} | {pct(a,25):.1f} | **{pct(a,50):.1f}** | "
            f"{pct(a,75):.1f} | {pct(a,90):.1f} | {a.mean():.1f} | {ge(2)} | {ge(3)} | {ge(4)} |")
    out("")
    # conditional: given the trade got to +1R first (it "started working")
    base = "fixed4pct"; legcol = f"{base}_legR_pb{PB_MAIN}"
    work = df[df[f"{base}_mfeR"] >= 1.0]
    a = work[legcol].replace([np.inf,-np.inf], np.nan).dropna(); a = a[(a>-2)&(a<50)]
    out(f"**Given the breakout reached at least +1R (n={len(work):,}, {len(work)/len(df)*100:.0f}% of breakouts), "
        f"first-leg (4% stop): median {pct(a,50):.1f}R, P25 {pct(a,25):.1f}R, P75 {pct(a,75):.1f}R.**")
    out("")

    # ---- 2. target hit-rates & expectancy (fixed) per stop def ----
    out("## 2. Fixed-target hit rate & expectancy (enter every breakout, 1R stop)")
    out("")
    for sname, desc in STOP_DEFS.items():
        out(f"**Stop = {desc}**")
        out("")
        out("| Target | hit-before-stop | expectancy (R) |")
        out("|---|--:|--:|")
        for T in TARGETS:
            o_ = df[f"{sname}_T{T}"].replace([np.inf,-np.inf], np.nan).dropna()
            hit = (o_ >= T).mean() * 100
            exp = o_.mean()
            out(f"| {int(T)}R | {hit:.0f}% | {exp:+.3f} |")
        out("")

    # ---- 3. which features predict the first leg? ----
    out("## 3. Does any pre-entry signal predict the first-leg size?")
    out("")
    out("Correlation of each feature with first-leg R (4% stop):")
    out("")
    out("| Feature | Pearson r | Spearman ρ |")
    out("|---|--:|--:|")
    y = df[legcol].replace([np.inf,-np.inf], np.nan)
    feats = ["atr_pct","base_depth_pct","vol_surge","dist_sma50_pct","dist_52wh_pct","r1m_pct","turnover_cr"]
    for f in feats:
        x = df[f].replace([np.inf,-np.inf], np.nan)
        m = x.notna() & y.notna() & (y>-2) & (y<50)
        if m.sum() < 50: continue
        pr = np.corrcoef(x[m], y[m])[0,1]
        sr = pd.Series(x[m]).corr(pd.Series(y[m]), method="spearman")
        out(f"| {f} | {pr:+.3f} | {sr:+.3f} |")
    out("")

    # ---- 4. bucketed: ATR% terciles -> first leg + best target ----
    out("## 4. First leg by volatility regime (ATR% terciles)")
    out("")
    out("The actionable cut: split breakouts by ATR% (volatility) at entry.")
    out("")
    q1, q2 = df["atr_pct"].quantile([0.33, 0.66])
    df["atr_bucket"] = np.where(df["atr_pct"]<=q1, "low", np.where(df["atr_pct"]<=q2, "mid", "high"))
    out(f"ATR% cutoffs: low ≤ {q1:.1f}% < mid ≤ {q2:.1f}% < high")
    out("")
    out("| ATR bucket | n | median legR | P25 | P75 | 2R hit | 3R hit | 4R hit | best fixed T (exp) |")
    out("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for b in ["low","mid","high"]:
        sub = df[df["atr_bucket"]==b]
        a = sub[legcol].replace([np.inf,-np.inf],np.nan).dropna(); a=a[(a>-2)&(a<50)]
        hits = {T: (sub[f'fixed4pct_T{T}']>=T).mean()*100 for T in TARGETS}
        exps = {T: sub[f'fixed4pct_T{T}'].replace([np.inf,-np.inf],np.nan).mean() for T in TARGETS}
        bestT = max(exps, key=exps.get)
        out(f"| {b} | {len(sub):,} | **{pct(a,50):.1f}** | {pct(a,25):.1f} | {pct(a,75):.1f} | "
            f"{hits[2.0]:.0f}% | {hits[3.0]:.0f}% | {hits[4.0]:.0f}% | {int(bestT)}R ({exps[bestT]:+.2f}) |")
    out("")

    # ---- 5. adaptive rule vs fixed ----
    out("## 5. Adaptive target vs fixed (expectancy, 4% stop)")
    out("")
    # adaptive: choose target per ATR bucket = the expectancy-maximising fixed T in that bucket (learned above)
    exp_by = {}
    for b in ["low","mid","high"]:
        sub = df[df["atr_bucket"]==b]
        exps = {T: sub[f'fixed4pct_T{T}'].replace([np.inf,-np.inf],np.nan).mean() for T in TARGETS}
        exp_by[b] = max(exps, key=exps.get)
    df["adaptive_T"] = df["atr_bucket"].map(exp_by)
    df["adaptive_out"] = df.apply(lambda r: r[f"fixed4pct_T{r['adaptive_T']}"], axis=1)
    fixed_exp = {T: df[f'fixed4pct_T{T}'].replace([np.inf,-np.inf],np.nan).mean() for T in TARGETS}
    adap_exp = df["adaptive_out"].replace([np.inf,-np.inf],np.nan).mean()
    out("Adaptive rule learned: " + ", ".join(f"{b} ATR → {int(exp_by[b])}R" for b in ['low','mid','high']))
    out("")
    out("| Strategy | expectancy (R/trade) |")
    out("|---|--:|")
    for T in TARGETS:
        out(f"| Fixed {int(T)}R | {fixed_exp[T]:+.3f} |")
    out(f"| **Adaptive (ATR bucket)** | **{adap_exp:+.3f}** |")
    out("")

    # ---- 6. OUT-OF-SAMPLE validation (learn on older half, test on newer half) ----
    out("## 6. Out-of-sample check (no peeking)")
    out("")
    out("Learn the ATR-bucket→target map on the older half of the events, then apply it "
        "unchanged to the newer half. This removes the in-sample bias in section 5.")
    out("")
    df["date"] = pd.to_datetime(df["date"])
    cut = df["date"].quantile(0.5)
    train, test = df[df["date"] <= cut].copy(), df[df["date"] > cut].copy()
    tq1, tq2 = train["atr_pct"].quantile([0.33, 0.66])
    def buck(x): return np.where(x <= tq1, "low", np.where(x <= tq2, "mid", "high"))
    train["b"] = buck(train["atr_pct"]); test["b"] = buck(test["atr_pct"])
    learned = {}
    for b in ["low", "mid", "high"]:
        sub = train[train["b"] == b]
        exps = {T: sub[f'fixed4pct_T{T}'].replace([np.inf,-np.inf],np.nan).mean() for T in TARGETS}
        learned[b] = max(exps, key=exps.get)
    test["adap"] = test.apply(lambda r: r[f"fixed4pct_T{learned[r['b']]}"], axis=1)
    out(f"- train n={len(train):,} (≤ {cut:%Y-%m-%d}), test n={len(test):,} (> {cut:%Y-%m-%d})")
    out(f"- learned on train: " + ", ".join(f"{b}→{int(learned[b])}R" for b in ['low','mid','high']))
    out("")
    out("| Strategy | test expectancy (R/trade) |")
    out("|---|--:|")
    for T in TARGETS:
        out(f"| Fixed {int(T)}R | {test[f'fixed4pct_T{T}'].replace([np.inf,-np.inf],np.nan).mean():+.3f} |")
    out(f"| **Adaptive (learned on train)** | **{test['adap'].replace([np.inf,-np.inf],np.nan).mean():+.3f}** |")
    out("")

    out(f"_Samples: {len(df):,} breakouts. Caveat: raw (unadjusted) bhavcopy; symbols with any "
        f">{int(SPLIT_GUARD*100)}% single-day bar were dropped as likely splits. Daily-bar resolution; "
        f"in-bar ties resolved stop-before-target (conservative)._")

    # write report + raw event sample
    fn = BACKTESTS / f"{datetime.now():%Y-%m-%d}_first_leg_target_study.md"
    fn.write_text("\n".join(rep), encoding="utf-8")
    df.to_csv(BACKTESTS / f"{datetime.now():%Y-%m-%d}_first_leg_events.csv", index=False)
    print(f"\n[report] {fn}", flush=True)


if __name__ == "__main__":
    main()
