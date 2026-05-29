"""
NSE + BSE Momentum Scanner.

Mandatory filters:
  - Close above 200-day SMA
  - 20-day avg daily turnover above MIN_LIQUIDITY_CR (₹ crore)

Ranks survivors by a Minervini/Carhart-style momentum composite
(weighted blend of 1M / 3M / 6M / 12M returns) and outputs a
percentile RS rating against the qualifying universe.

Outputs:
  data/scanner_output.json   - dashboard feed
  data/scanner_output.csv    - spreadsheet feed
  data/universe.csv          - the universe scanned
"""

from __future__ import annotations

import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

import os
import re

# ----- Market selection (in = NSE+BSE India, us = NASDAQ+NYSE+AMEX) -----
# The whole pipeline is market-agnostic except: which universe + price source,
# the turnover divisor + liquidity gate (Rs crore vs $ million), a min-price floor,
# the episodic-pivot thresholds, and the output filename. All set here.
MARKET = os.getenv("SCAN_MARKET", "in").strip().lower()
_IS_US = MARKET == "us"

if _IS_US:
    # Liquidity floor grounded in Minervini (>= ~1M shares, price > $10-30) + Qullamaggie
    # (liquid leaders; EP dollar-volume > $100M). Turnover is held in $ MILLIONS.
    _TURNOVER_DIV = 1e6                                              # $ -> $M
    MIN_LIQUIDITY_CR = float(os.getenv("SCAN_MIN_LIQ_CR", "20.0"))   # $20M/day
    MIN_PRICE = float(os.getenv("SCAN_MIN_PRICE", "10.0"))          # avoid sub-$10 (Minervini)
    EP_GAP = float(os.getenv("SCAN_EP_GAP", "10.0"))                # Qullamaggie EP gap >= 10%
    EP_TURNOVER_MIN = float(os.getenv("SCAN_EP_LIQ", "100.0"))      # EP dollar-volume >= $100M
    IPO_LIQ_DEFAULT = "5.0"                                          # $5M/day for fresh listings
    OUT_SUFFIX = "_us"
    CURRENCY = "USD"
else:
    _TURNOVER_DIV = 1e7                                              # Rs -> Rs crore
    MIN_LIQUIDITY_CR = float(os.getenv("SCAN_MIN_LIQ_CR", "10.0"))   # Rs 10 cr/day
    MIN_PRICE = float(os.getenv("SCAN_MIN_PRICE", "0.0"))
    EP_GAP = float(os.getenv("SCAN_EP_GAP", "5.0"))
    EP_TURNOVER_MIN = float(os.getenv("SCAN_EP_LIQ", "2.0"))
    IPO_LIQ_DEFAULT = "1.0"
    OUT_SUFFIX = ""
    CURRENCY = "INR"

WITHIN_PCT_OF_52W_HIGH = float(os.getenv("SCAN_MAX_OFF_HIGH", "25.0"))
_LIMIT = int(os.getenv("SCAN_LIMIT", "0"))   # cap universe (smoke testing only; 0 = full)
BATCH_SIZE = int(os.getenv("SCAN_BATCH_SIZE", "80"))
SLEEP_BETWEEN_BATCHES = float(os.getenv("SCAN_SLEEP_S", "2.5"))
INCLUDE_BSE = os.getenv("SCAN_INCLUDE_BSE", "1") == "1"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


# Zerodha publishes a public, no-auth, daily-refreshed instruments CSV covering
# every NSE + BSE listing. ~10 MB, 128k rows. We cache it locally for the day.
KITE_URL = "https://api.kite.trade/instruments"
KITE_CACHE = "data/kite_instruments.csv"

# NSE/BSE label "EQ" includes bonds, SGBs, T-bills and SDLs. Drop those by suffix.
# Real equity series (EQ, BE, BZ, SM, ST, MM) either have no suffix or one of these.
_BAD_SUFFIX = re.compile(
    r"-(SG|GS|TB|GB|SF|GL|GF|IV|ND|NC|NA|NE|FB|UT|SS|RE|RR|RT|IL|NCD|NV|D|N\d*|G\d+|PD|PP)$"
)


def _fetch_kite_instruments() -> pd.DataFrame:
    """Get Zerodha's public instruments dump (cached for the day)."""
    cache = Path(KITE_CACHE)
    fresh = False
    if cache.exists():
        # Refresh once per UTC day
        age_h = (datetime.now(timezone.utc).timestamp() - cache.stat().st_mtime) / 3600
        fresh = age_h < 18
    if not fresh:
        print(f"[universe] fetching Zerodha instruments: {KITE_URL}")
        r = requests.get(KITE_URL, headers=UA, timeout=60)
        r.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(r.content)
        print(f"[universe] cached -> {cache} ({len(r.content)/1e6:.1f} MB)")
    else:
        print(f"[universe] using cached {cache} ({cache.stat().st_size/1e6:.1f} MB)")
    return pd.read_csv(cache)


def _is_equity(sym) -> bool:
    """Heuristic: drop bonds / SGBs / SDLs / T-bills that share the 'EQ' label."""
    if not isinstance(sym, str) or not sym:
        return False
    if sym[0].isdigit():
        return False
    if _BAD_SUFFIX.search(sym):
        return False
    return True


def fetch_nse_universe() -> pd.DataFrame:
    df = _fetch_kite_instruments()
    nse = df[
        (df["exchange"] == "NSE")
        & (df["segment"] == "NSE")
        & (df["instrument_type"] == "EQ")
    ].copy()
    nse = nse[nse["tradingsymbol"].apply(_is_equity)]
    nse["symbol"] = nse["tradingsymbol"].astype(str).str.strip()
    nse["yf_ticker"] = nse["symbol"] + ".NS"
    nse["name"] = nse["name"].astype(str).str.strip()
    nse["exchange"] = "NSE"
    return (
        nse[["symbol", "yf_ticker", "name", "exchange"]]
        .drop_duplicates("symbol")
        .reset_index(drop=True)
    )


def fetch_bse_universe() -> pd.DataFrame:
    """Pull BSE-listed equities from the Zerodha dump too. exchange_token is
    the BSE scrip code, which is what yfinance expects as `<code>.BO`."""
    df = _fetch_kite_instruments()
    bse = df[
        (df["exchange"] == "BSE")
        & (df["segment"] == "BSE")
        & (df["instrument_type"] == "EQ")
    ].copy()
    bse = bse[bse["tradingsymbol"].apply(_is_equity)]
    bse["symbol"] = bse["tradingsymbol"].astype(str).str.strip()
    bse["yf_ticker"] = bse["exchange_token"].astype(str).str.strip() + ".BO"
    bse["name"] = bse["name"].astype(str).str.strip()
    bse["exchange"] = "BSE"
    return (
        bse[["symbol", "yf_ticker", "name", "exchange"]]
        .drop_duplicates("symbol")
        .reset_index(drop=True)
    )


def build_universe() -> pd.DataFrame:
    nse = fetch_nse_universe()
    bse = fetch_bse_universe() if INCLUDE_BSE else pd.DataFrame(columns=nse.columns)
    only_bse = bse[~bse["symbol"].isin(nse["symbol"])] if not bse.empty else bse
    universe = pd.concat([nse, only_bse], ignore_index=True)
    print(f"[universe] NSE={len(nse)} BSE_only_added={len(only_bse)} total={len(universe)}")
    return universe


def _download_one_batch(
    batch: list[str], period: str, threads: bool = True
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Download a batch; return (ok dict, retry list).

    A ticker goes into `retry` whenever bulk fetch returned nothing usable —
    either because it raised KeyError/TypeError in the result frame, or because
    Yahoo silently came back with NaN columns (typical rate-limit symptom).
    The retry pass downloads these individually with throttling and that
    almost always succeeds.
    """
    out: dict[str, pd.DataFrame] = {}
    try:
        data = yf.download(
            batch,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=threads,
            progress=False,
        )
    except Exception as exc:
        print(f"[prices] batch error: {exc}; will retry")
        return {}, batch
    failed: list[str] = []
    for t in batch:
        try:
            df = data if len(batch) == 1 else data[t]
            df = df.dropna(subset=["Close"])
            if len(df) >= 30:
                # Keep anything with at least ~6 weeks of data.
                # Full analyze() needs 200 bars; analyze_ipo() handles 30-199.
                out[t] = df
            elif len(df) == 0:
                # Bulk fetch silently dropped this ticker; worth a single retry.
                failed.append(t)
            # Else: ticker has < 30 bars — too new to do anything meaningful.
        except (KeyError, TypeError):
            failed.append(t)
    return out, failed


def download_prices(tickers: list[str], period: str = "2y",
                    retry_failed: bool = True) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    total = len(tickers)
    batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    retry_pool: list[str] = []
    t_start = time.time()
    for i in range(0, total, BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        elapsed = time.time() - t_start
        rate = (i + 1) / max(elapsed, 1)
        eta = max(0, (total - i) / max(rate, 0.1))
        print(f"[prices] batch {i // BATCH_SIZE + 1}/{batches} "
              f"({i + 1}-{min(i + BATCH_SIZE, total)}/{total}) "
              f"got={len(out)} eta={int(eta)}s",
              flush=True)
        ok, failed = _download_one_batch(batch, period)
        out.update(ok)
        retry_pool.extend(failed)
        time.sleep(SLEEP_BETWEEN_BATCHES)
    # Retry pass for failed tickers. Sequential (no threads) + small chunks +
    # longer cooldown so we don't trigger another rate-limit wave.
    # Skipped when bhavcopy backfill is on — bhavcopy fills the gaps far faster
    # and more completely than retrying thousands of mostly-dead tickers.
    if retry_pool and retry_failed:
        retry_pool = sorted(set(retry_pool) - set(out))
        print(f"[prices] retry pass on {len(retry_pool)} failed tickers (sequential)",
              flush=True)
        time.sleep(8)
        RETRY_BATCH = 15
        recovered = 0
        for i in range(0, len(retry_pool), RETRY_BATCH):
            batch = retry_pool[i : i + RETRY_BATCH]
            ok, still_bad = _download_one_batch(batch, period, threads=False)
            out.update(ok)
            recovered += len(ok)
            if i % (RETRY_BATCH * 4) == 0:
                print(f"[prices] retry {i + len(batch)}/{len(retry_pool)} recovered={recovered}",
                      flush=True)
            time.sleep(3)
        print(f"[prices] retry recovered {recovered} of {len(retry_pool)}", flush=True)
    return out


def _safe_float(x) -> float | None:
    try:
        f = float(x)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 2)
    except (TypeError, ValueError):
        return None


def analyze(df: pd.DataFrame) -> dict | None:
    if len(df) < 200:
        return None
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)

    sma10 = close.rolling(10).mean()
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma150 = close.rolling(150).mean()
    sma200 = close.rolling(200).mean()
    sma200_prev = sma200.shift(22)

    last = close.iloc[-1]
    last10 = sma10.iloc[-1]
    last20 = sma20.iloc[-1]
    last50 = sma50.iloc[-1]
    last150 = sma150.iloc[-1]
    last200 = sma200.iloc[-1]
    last200_prev = sma200_prev.iloc[-1]

    if any(pd.isna(x) for x in [last50, last150, last200]):
        return None

    window = close.iloc[-252:] if len(close) >= 252 else close
    high_52w = window.max()
    low_52w = window.min()
    pct_off_high = (1 - last / high_52w) * 100

    def ret(days: int) -> float | float:
        if len(close) < days + 1:
            return np.nan
        return (close.iloc[-1] / close.iloc[-days - 1] - 1) * 100

    r1m, r3m, r6m, r12m = ret(21), ret(63), ret(126), ret(252)

    turnover_cr = (close * vol).iloc[-20:].mean() / _TURNOVER_DIV
    avg_vol50 = vol.iloc[-50:].mean()
    avg_vol5 = vol.iloc[-5:].mean()
    vol_surge = vol.iloc[-1] / avg_vol50 if avg_vol50 > 0 else np.nan
    vol_dryup = bool(avg_vol50 > 0 and avg_vol5 < avg_vol50)  # selling drying up

    # ----- Qullamaggie / Minervini structure metrics -----
    # ADR% (Average Daily Range) — Qullamaggie's volatility cornerstone:
    # mean over the last 20 bars of (High/Low - 1)*100. Falls back to a
    # close-to-close proxy when High/Low aren't available.
    has_hl = ("High" in df.columns) and ("Low" in df.columns)
    if has_hl:
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
        dr = (high / low.replace(0, np.nan) - 1) * 100
        adr_pct = dr.iloc[-20:].mean()
        rng_recent = high.iloc[-10:].max() - low.iloc[-10:].min()
        rng_prior = (high.iloc[-20:-10].max() - low.iloc[-20:-10].min()) if len(close) >= 20 else np.nan
        gap_pct = (df["Open"].astype(float).iloc[-1] / close.iloc[-2] - 1) * 100 \
            if ("Open" in df.columns and len(close) >= 2) else np.nan
    else:
        dcc = close.pct_change().abs() * 100
        adr_pct = dcc.iloc[-20:].mean() * 1.4   # intraday range ~1.4x close-to-close
        rng_recent = close.iloc[-10:].max() - close.iloc[-10:].min()
        rng_prior = (close.iloc[-20:-10].max() - close.iloc[-20:-10].min()) if len(close) >= 20 else np.nan
        gap_pct = (close.pct_change().iloc[-1] * 100) if len(close) >= 2 else np.nan
    # recent 10-bar range as % of price (lower = tighter consolidation)
    tightness_pct = (rng_recent / last * 100) if last else np.nan
    # contraction = recent 10-bar range meaningfully tighter than the prior 10
    contracting = bool(not pd.isna(rng_prior) and rng_prior > 0 and rng_recent < rng_prior * 0.75)
    prior_move = max(r1m if not pd.isna(r1m) else 0, r3m if not pd.isna(r3m) else 0)

    momentum = np.nanmean([
        (r12m if not pd.isna(r12m) else 0) * 0.4,
        (r6m if not pd.isna(r6m) else 0) * 0.3,
        (r3m if not pd.isna(r3m) else 0) * 0.2,
        (r1m if not pd.isna(r1m) else 0) * 0.1,
    ]) * 4

    crit = {
        "price_gt_sma10": bool(not pd.isna(last10) and last > last10),
        "price_gt_sma20": bool(not pd.isna(last20) and last > last20),
        "price_gt_sma50": bool(last > last50),
        "price_gt_sma150": bool(last > last150),
        "price_gt_sma200": bool(last > last200),
        "sma50_gt_sma150": bool(last50 > last150),
        "sma150_gt_sma200": bool(last150 > last200),
        "sma200_rising": bool(not pd.isna(last200_prev) and last200 > last200_prev),
        "within_25_of_high": bool(pct_off_high <= 25),
        "above_30_of_low": bool(low_52w > 0 and (last / low_52w - 1) >= 0.30),
    }
    # Minervini's Trend Template is the strict 8-criteria subset
    trend_template = all(
        crit[k] for k in (
            "price_gt_sma50", "price_gt_sma150", "price_gt_sma200",
            "sma50_gt_sma150", "sma150_gt_sma200", "sma200_rising",
            "within_25_of_high", "above_30_of_low",
        )
    )

    # Stage-2 uptrend (Weinstein/Minervini): above the key SMAs, 50>150, 200 rising
    stage2 = bool(crit["price_gt_sma50"] and crit["price_gt_sma150"]
                  and crit["price_gt_sma200"] and crit["sma50_gt_sma150"]
                  and crit["sma200_rising"])

    # Minervini VCP / tight setup: a Stage-2 leader within 15% of its high whose
    # range is contracting while volume dries up — the classic pre-breakout coil.
    vcp_setup = bool(stage2 and pct_off_high <= 15 and contracting and vol_dryup)

    # Qullamaggie breakout: a high-ADR mover above its 10/20/50 SMAs that already
    # made a big move (>=25% over 1-3M) and is now coiling tight near its highs.
    qm_breakout = bool(
        (not pd.isna(adr_pct) and adr_pct >= 4)
        and crit["price_gt_sma10"] and crit["price_gt_sma20"] and crit["price_gt_sma50"]
        and prior_move >= 25 and pct_off_high <= 15 and contracting
    )

    return {
        "close": _safe_float(last),
        "sma50": _safe_float(last50),
        "sma200": _safe_float(last200),
        "high_52w": _safe_float(high_52w),
        "pct_off_high": _safe_float(pct_off_high),
        "r1m": _safe_float(r1m),
        "r3m": _safe_float(r3m),
        "r6m": _safe_float(r6m),
        "r12m": _safe_float(r12m),
        "turnover_cr": _safe_float(turnover_cr),
        "vol_surge": _safe_float(vol_surge),
        "adr_pct": _safe_float(adr_pct),
        "gap_pct": _safe_float(gap_pct),
        "tightness_pct": _safe_float(tightness_pct),
        "vol_dryup": vol_dryup,
        "contracting": contracting,
        "momentum": _safe_float(momentum),
        "trend_template": trend_template,
        "stage2": stage2,
        "vcp_setup": vcp_setup,
        "qm_breakout": qm_breakout,
        **crit,
    }


def analyze_ipo(df: pd.DataFrame) -> dict | None:
    """For recent listings — between 30 and 199 trading bars (~6 weeks to ~10
    months). Returns IPO-friendly metrics: all-time-high proximity, returns
    over whatever windows fit, liquidity, and a 'momentum since listing' read.
    Returns None when the stock has full history (>= 200 bars) or too little
    history (< 30 bars)."""
    n = len(df)
    if n < 30 or n >= 200:
        return None
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)

    last = close.iloc[-1]
    high_all = float(close.max())
    low_all = float(close.min())
    pct_off_high = (1 - last / high_all) * 100 if high_all > 0 else None
    pct_from_low = (last / low_all - 1) * 100 if low_all > 0 else None

    # SMAs only where there's enough data
    smas = {}
    for w in (10, 20, 50, 100):
        if n >= w:
            smas[f"sma{w}"] = _safe_float(close.rolling(w).mean().iloc[-1])
        else:
            smas[f"sma{w}"] = None

    def ret(days: int) -> float | None:
        if n < days + 1:
            return None
        return round((close.iloc[-1] / close.iloc[-days - 1] - 1) * 100, 2)

    r1w  = ret(5)
    r1m  = ret(21)
    r3m  = ret(63) if n >= 64 else None
    # "Since listing" return — first close vs current
    since_listing = round((close.iloc[-1] / close.iloc[0] - 1) * 100, 2) if close.iloc[0] > 0 else None

    # Liquidity over the last 20 bars (or whatever's available)
    w = min(20, n)
    turnover_cr = float((close * vol).iloc[-w:].mean() / _TURNOVER_DIV)
    avg_vol = float(vol.iloc[-w:].mean()) if w > 0 else 0.0
    vol_surge = (float(vol.iloc[-1]) / avg_vol) if avg_vol > 0 else None

    # Trend conditions (relaxed for IPOs — no SMA200 yet)
    price_gt_sma20 = (smas["sma20"] is not None and last > smas["sma20"])
    price_gt_sma50 = (smas["sma50"] is not None and last > smas["sma50"])

    return {
        "is_ipo": True,
        "bars": int(n),
        "close": _safe_float(last),
        "high_since_listing": _safe_float(high_all),
        "low_since_listing": _safe_float(low_all),
        "pct_off_high": _safe_float(pct_off_high),
        "pct_from_low": _safe_float(pct_from_low),
        "r1w": r1w,
        "r1m": r1m,
        "r3m": r3m,
        "since_listing_pct": since_listing,
        "turnover_cr": _safe_float(turnover_cr),
        "vol_surge": _safe_float(vol_surge),
        "price_gt_sma20": bool(price_gt_sma20),
        "price_gt_sma50": bool(price_gt_sma50),
        **smas,
    }


def _load_sector_map() -> dict[str, str]:
    """Flatten the static sector_map.json (sector -> [symbols]) into a
    symbol -> sector lookup."""
    p = Path(__file__).resolve().parent / "sector_map.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for sector, syms in raw.items():
        if sector.startswith("_"):
            continue
        for s in syms:
            out[s.upper()] = sector
    return out


def _compute_breadth(all_results: list[dict]) -> dict:
    """Breadth across every stock the analyzer produced output for (NOT just
    qualifiers). This is the real 'market participation' signal."""
    n = len(all_results)
    if n == 0:
        return {}
    def pct(flag: str) -> float:
        return round(sum(1 for r in all_results if r.get(flag)) / n * 100, 1)
    pct10 = pct("price_gt_sma10")
    pct20 = pct("price_gt_sma20")
    pct50 = pct("price_gt_sma50")
    pct200 = pct("price_gt_sma200")
    near_high = round(
        sum(1 for r in all_results if (r.get("pct_off_high") or 100) <= 10) / n * 100, 1
    )
    tt_pass = sum(1 for r in all_results if r.get("trend_template"))

    # Median returns across the broad market
    def med(key: str) -> float | None:
        xs = sorted([r[key] for r in all_results if r.get(key) is not None])
        if not xs: return None
        m = len(xs) // 2
        return round((xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2), 2)

    med_r1m = med("r1m")
    med_r3m = med("r3m")
    med_r6m = med("r6m")

    # 1-10 regime score: blend breadth + median returns. Each signal -> 0..10
    def band(v: float, bear_lo: float, bear_hi: float, bull_lo: float, bull_hi: float) -> float:
        # Linear scale: <=bear_lo -> 0, >=bull_hi -> 10, bear_hi=4, bull_lo=6
        if v <= bear_lo: return 0.0
        if v >= bull_hi: return 10.0
        if v <= bear_hi: return (v - bear_lo) / (bear_hi - bear_lo) * 4
        if v >= bull_lo: return 6 + (v - bull_lo) / (bull_hi - bull_lo) * 4
        return 4 + (v - bear_hi) / (bull_lo - bear_hi) * 2  # the 4-6 sideways band

    score_200 = band(pct200, 20, 40, 55, 75)   # long-term trend participation
    score_50  = band(pct50, 25, 45, 55, 75)
    score_20  = band(pct20, 30, 45, 55, 70)
    score_r1m = band((med_r1m or 0), -5, -1, 1, 5)
    score_r3m = band((med_r3m or 0), -10, -2, 2, 10)
    score_high = band(near_high, 5, 12, 18, 30)

    regime = round(
        score_200 * 0.30
        + score_50 * 0.22
        + score_20 * 0.13
        + score_r1m * 0.08
        + score_r3m * 0.17
        + score_high * 0.10,
        2,
    )
    if regime >= 7:
        regime_label = "Bullish"
    elif regime >= 4:
        regime_label = "Sideways"
    else:
        regime_label = "Bearish"

    # Minervini "progressive exposure": scale how much capital you risk to how
    # healthy the market is. Map regime 0-10 to a suggested max exposure %, and
    # give a plain-English stance.
    suggested_exposure = int(round(min(100, max(0, (regime - 2) / 6 * 100))))
    if regime >= 7:
        exposure_note = "Strong market — press your best setups."
    elif regime >= 5:
        exposure_note = "Constructive — normal size on A-setups, stay selective."
    elif regime >= 4:
        exposure_note = "Mixed — half size, only the cleanest setups."
    else:
        exposure_note = "Weak market — mostly cash, tiny size or sit out."

    return {
        "universe_with_data": n,
        "suggested_exposure_pct": suggested_exposure,
        "exposure_note": exposure_note,
        "pct_above_sma10": pct10,
        "pct_above_sma20": pct20,
        "pct_above_sma50": pct50,
        "pct_above_sma200": pct200,
        "pct_within_10_of_high": near_high,
        "trend_template_pass": tt_pass,
        "median_r1m": med_r1m,
        "median_r3m": med_r3m,
        "median_r6m": med_r6m,
        "regime_score": regime,
        "regime_label": regime_label,
        "component_scores": {
            "pct200": round(score_200, 2),
            "pct50":  round(score_50, 2),
            "pct20":  round(score_20, 2),
            "median_r1m": round(score_r1m, 2),
            "median_r3m": round(score_r3m, 2),
            "near_high": round(score_high, 2),
        },
    }


def _compute_history(prices: dict[str, pd.DataFrame], lookback: int = 252) -> dict:
    """Daily count of stocks matching each scanner over the last ~2 years, plus
    daily % of stocks above SMA10/20/50/200. Used by the dashboard to show
    breadth and per-scanner participation trends. ~500 trading days.

    Returns:
      {
        "dates": ["YYYY-MM-DD", ...],
        "scanners": { name: [count_per_day, ...] },
        "breadth_pct": { "above_sma10": [pct, ...], ..., "above_sma200": [pct, ...] }
      }
    """
    import collections
    if not prices:
        return {}

    # Take the union of all ticker date indexes
    all_dates_set = set()
    for df in prices.values():
        all_dates_set.update(df.index.tolist())
    all_dates = sorted(all_dates_set)
    if not all_dates:
        return {}
    start_idx = max(0, len(all_dates) - lookback)
    keep_dates = all_dates[start_idx:]
    keep_set = set(keep_dates)

    # Per-scanner daily count (sparse — we'll sum into a series)
    scanner_keys = ["momentum", "trend_template", "breakout52w", "vol_shocker", "ipo"]
    scanner_sums = {k: pd.Series(0, index=keep_dates, dtype="int64") for k in scanner_keys}

    # Per-MA daily numerator (count above) and denominator (count having that MA)
    ma_windows = [10, 20, 50, 200]
    above_sums = {w: pd.Series(0, index=keep_dates, dtype="int64") for w in ma_windows}
    have_sums  = {w: pd.Series(0, index=keep_dates, dtype="int64") for w in ma_windows}

    # Net new highs: daily (#stocks at a new 52w high) - (#at a new 52w low)
    nh_sum = pd.Series(0, index=keep_dates, dtype="int64")
    nl_sum = pd.Series(0, index=keep_dates, dtype="int64")

    processed = 0
    for t, df in prices.items():
        n = len(df)
        if n < 30:
            continue
        try:
            close = df["Close"].astype(float)
            vol = df["Volume"].astype(float)

            # Pre-compute everything we need
            smas = {w: close.rolling(w).mean() for w in ma_windows}
            sma150 = close.rolling(150).mean()
            sma200_22d = smas[200].shift(22)
            high_252 = close.rolling(252).max()
            low_252  = close.rolling(252).min()
            pct_off  = (1 - close / high_252) * 100
            r1m = close.pct_change(21) * 100
            r3m = close.pct_change(63) * 100
            r6m = close.pct_change(126) * 100
            turnover_cr = (close * vol).rolling(20).mean() / _TURNOVER_DIV
            avg_vol50 = vol.rolling(50).mean()
            vol_surge = vol / avg_vol50

            cond_liquid = (turnover_cr >= MIN_LIQUIDITY_CR).fillna(False)

            # ----- Scanner conditions per day -----
            # Momentum = the tightened base gate (uptrend + near-high + 3M&6M positive + liquid)
            mom = ((close > smas[50]) & (close > smas[200]) &
                   (pct_off <= WITHIN_PCT_OF_52W_HIGH) & (r6m > 0) & (r3m > 0) & cond_liquid)
            # Trend Template (strict 8/8)
            tt = (
                (close > smas[50]) & (close > sma150) & (close > smas[200]) &
                (smas[50] > sma150) & (sma150 > smas[200]) &
                (smas[200] > sma200_22d) &
                (pct_off <= 25) &
                (close > low_252 * 1.30)
            ) & cond_liquid
            brk = (pct_off <= 2) & (r1m > 0) & cond_liquid
            vsh = (vol_surge >= 2.5) & (r1m > 0) & cond_liquid

            for key, s in (("momentum", mom), ("trend_template", tt),
                           ("breakout52w", brk), ("vol_shocker", vsh)):
                s = s.fillna(False).astype(int)
                # Restrict to lookback window
                s = s.loc[s.index.isin(keep_set)]
                scanner_sums[key] = scanner_sums[key].add(s, fill_value=0)

            # ----- Breadth per MA: numerator + denominator -----
            for w in ma_windows:
                sma_w = smas[w]
                have = sma_w.notna()
                above = (close > sma_w) & have
                have = have.astype(int).loc[have.index.isin(keep_set)]
                above = above.fillna(False).astype(int).loc[above.index.isin(keep_set)]
                have_sums[w]  = have_sums[w].add(have,  fill_value=0)
                above_sums[w] = above_sums[w].add(above, fill_value=0)

            # ----- Net new highs: new 52w high vs new 52w low (need >=252 bars) -----
            if n >= 252:
                roll_max = close.rolling(252).max()
                roll_min = close.rolling(252).min()
                is_nh = (close >= roll_max).fillna(False).astype(int)
                is_nl = (close <= roll_min).fillna(False).astype(int)
                is_nh = is_nh.loc[is_nh.index.isin(keep_set)]
                is_nl = is_nl.loc[is_nl.index.isin(keep_set)]
                nh_sum = nh_sum.add(is_nh, fill_value=0)
                nl_sum = nl_sum.add(is_nl, fill_value=0)

            # ----- IPO scanner per day -----
            # A ticker is "IPO" between bar index 30 and 199 (0-indexed: 29..198)
            ipo_start = 29
            ipo_end = min(198, n - 1)
            if ipo_start <= ipo_end:
                ipo_dates = close.index[ipo_start:ipo_end + 1]
                ath = close.expanding().max()
                pct_off_ath = (1 - close / ath) * 100
                # Use 20-bar SMA where it exists
                sma20_local = smas[20]
                # Liquidity for IPOs uses a lower threshold
                turnover_ipo = (close * vol).rolling(min(20, n)).mean() / _TURNOVER_DIV
                ipo_cond = (
                    (close > sma20_local) &
                    (pct_off_ath <= 25) &
                    (turnover_ipo >= 1.0)
                ).fillna(False).astype(int)
                ipo_cond = ipo_cond.reindex(ipo_dates, fill_value=0)
                ipo_cond = ipo_cond.loc[ipo_cond.index.isin(keep_set)]
                scanner_sums["ipo"] = scanner_sums["ipo"].add(ipo_cond, fill_value=0)

            processed += 1
        except Exception as exc:
            print(f"[history] skip {t}: {exc}", flush=True)
            continue

    # Compute breadth %
    breadth_pct = {}
    for w in ma_windows:
        denom = have_sums[w].astype(float).replace(0, np.nan)  # np.nan supports .round(); pd.NA does not
        pct = (above_sums[w] / denom * 100).round(1)
        breadth_pct[f"above_sma{w}"] = [None if pd.isna(v) else float(v) for v in pct.reindex(keep_dates).tolist()]

    scanners_out = {
        k: [int(scanner_sums[k].reindex(keep_dates).fillna(0).iloc[i]) for i in range(len(keep_dates))]
        for k in scanner_keys
    }

    net_new_highs = [
        int(nh_sum.reindex(keep_dates).fillna(0).iloc[i] - nl_sum.reindex(keep_dates).fillna(0).iloc[i])
        for i in range(len(keep_dates))
    ]

    print(f"[history] processed {processed} tickers; {len(keep_dates)} dates", flush=True)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in keep_dates],
        "scanners": scanners_out,
        "breadth_pct": breadth_pct,
        "net_new_highs": net_new_highs,
        "tickers_processed": processed,
    }


def _compute_sectors(qualifiers: list[dict]) -> list[dict]:
    """Group qualifiers by sector, return aggregate momentum/return stats."""
    if not qualifiers:
        return []
    df = pd.DataFrame(qualifiers)
    by = df.groupby("sector", dropna=False)
    rows: list[dict] = []
    for sector, sub in by:
        rows.append({
            "sector": sector,
            "count": int(len(sub)),
            "avg_r1m":  round(float(sub["r1m"].mean()), 2)  if sub["r1m"].notna().any() else None,
            "avg_r3m":  round(float(sub["r3m"].mean()), 2)  if sub["r3m"].notna().any() else None,
            "avg_r6m":  round(float(sub["r6m"].mean()), 2)  if sub["r6m"].notna().any() else None,
            "avg_r12m": round(float(sub["r12m"].mean()), 2) if sub["r12m"].notna().any() else None,
            "avg_momentum": round(float(sub["momentum"].mean()), 2) if sub["momentum"].notna().any() else None,
            "tt_pass": int(sub["trend_template"].sum()),
            "top_symbols": sub.nlargest(5, "rs_rating")[["symbol", "rs_rating"]].to_dict(orient="records"),
        })
    rows.sort(key=lambda r: r["avg_momentum"] or -1e9, reverse=True)
    return rows


def main() -> int:
    started = datetime.now(timezone.utc)
    if _IS_US:
        # ---- US: Nasdaq Trader universe + yfinance EOD (the keyless US stack) ----
        import usdata
        universe = usdata.build_us_universe()
        if _LIMIT:
            universe = universe.head(_LIMIT).reset_index(drop=True)
            print(f"[scan] SCAN_LIMIT={_LIMIT} — smoke test on {len(universe)} symbols", flush=True)
        universe.to_csv(DATA / f"universe{OUT_SUFFIX}.csv", index=False)
        prices, _usmeta = usdata.fetch_us_history(universe["yf_ticker"].tolist(), period="2y")
        print(f"[prices] usdata (yfinance) got {len(prices)} of {len(universe)} US symbols", flush=True)
    else:
        universe = build_universe()
        universe.to_csv(DATA / "universe.csv", index=False)

        # When bhavcopy backfill is on, skip the slow yfinance retry pass —
        # bhavcopy recovers the gaps faster and more completely.
        use_bhavcopy = os.getenv("SCAN_USE_BHAVCOPY", "1") == "1"
        # Bhavcopy-primary mode: skip Yahoo entirely (it rate-limits hard under
        # repeated runs). Bhavcopy alone is the complete, reliable NSE+BSE universe.
        if os.getenv("SCAN_SKIP_YFINANCE", "0") == "1":
            prices = {}
            print("[prices] SKIP_YFINANCE=1 — bhavcopy-primary mode (no Yahoo)")
        else:
            prices = download_prices(universe["yf_ticker"].tolist(), retry_failed=not use_bhavcopy)
            print(f"[prices] yfinance got {len(prices)} of {len(universe)} symbols")

        # Bhavcopy: (1) FILL universe tickers Yahoo missed, and (2) ADD tradeable
        # stocks the Zerodha master list doesn't even include (maximizes coverage —
        # ~3k extra NSE+BSE names + recent IPOs). Yahoo prices stay primary (split-
        # adjusted); bhavcopy is raw, used only where Yahoo had nothing.
        extra_rows: list[dict] = []
        if os.getenv("SCAN_USE_BHAVCOPY", "1") == "1":
            existing = set(universe["yf_ticker"])
            # NSE (GitHub mirror — works from any IP)
            try:
                from bhavcopy import fetch_nse_history
                bhav, meta = fetch_nse_history(days_back=504)
                filled = added = 0
                for t, df in bhav.items():
                    if t in prices:
                        continue
                    prices[t] = df
                    if t in existing:
                        filled += 1
                    else:
                        m = meta.get(t, {})
                        extra_rows.append({"symbol": m.get("symbol", t[:-3]),
                                           "yf_ticker": t, "name": m.get("name", t[:-3]),
                                           "exchange": "NSE"})
                        added += 1
                print(f"[bhavcopy] NSE filled {filled}, added {added} new (total {len(prices)})", flush=True)
            except Exception as exc:
                print(f"[bhavcopy] NSE step skipped: {exc}", flush=True)

            # BSE (bseindia.com direct — may be geo-restricted on some CI IPs)
            try:
                from bhavcopy import fetch_bse_history
                bbse, bmeta = fetch_bse_history(days_back=460)
                filled = added = 0
                for t, df in bbse.items():
                    if t in prices:
                        continue
                    prices[t] = df
                    if t in existing:
                        filled += 1
                    else:
                        m = bmeta.get(t, {})
                        extra_rows.append({"symbol": m.get("symbol", t[:-3]),
                                           "yf_ticker": t, "name": m.get("name", t[:-3]),
                                           "exchange": "BSE"})
                        added += 1
                print(f"[bhavcopy] BSE filled {filled}, added {added} new (total {len(prices)})", flush=True)
            except Exception as exc:
                print(f"[bhavcopy] BSE step skipped: {exc}", flush=True)

        # Fold the bhavcopy-only stocks into the universe so they flow through analyze()
        if extra_rows:
            universe = pd.concat([universe, pd.DataFrame(extra_rows)], ignore_index=True)
            universe = universe.drop_duplicates("yf_ticker").reset_index(drop=True)
            universe.to_csv(DATA / "universe.csv", index=False)
            print(f"[universe] expanded to {len(universe)} with bhavcopy-only stocks", flush=True)

    sector_map = _load_sector_map()
    all_results: list[dict] = []        # everything analyze() produced (breadth)
    rows: list[dict] = []               # passed both gates (qualifiers)
    ipo_rows: list[dict] = []           # recent listings, 30-199 bars
    ep_rows: list[dict] = []            # episodic pivots (gap + volume off a base)

    # IPO gates (separate from main-scanner gates):
    #   bars 30-199, turnover >= 1 cr, within 25% of all-time high since listing
    MIN_IPO_LIQ_CR = float(os.getenv("SCAN_IPO_MIN_LIQ_CR", IPO_LIQ_DEFAULT))
    MAX_IPO_OFF_HIGH = float(os.getenv("SCAN_IPO_MAX_OFF_HIGH", "25.0"))

    for _, row in universe.iterrows():
        t = row["yf_ticker"]
        if t not in prices:
            continue
        sym = row["symbol"]
        df = prices[t]

        # Mature stock?
        res = analyze(df)
        if res is not None:
            all_results.append(res)  # for breadth (full universe, pre-gate)

            # Episodic Pivot (EOD proxy): a big up-gap on heavy volume out of a
            # non-extended base — the market repricing a stock on a surprise.
            # Daily data, so this is the end-of-day footprint of Qullamaggie's EP.
            gp = res.get("gap_pct") or 0
            if (gp >= EP_GAP and (res.get("vol_surge") or 0) >= 3
                    and (res.get("turnover_cr") or 0) >= EP_TURNOVER_MIN
                    and (res.get("r3m") if res.get("r3m") is not None else 99) < 40
                    and res.get("price_gt_sma50")):
                ep_rows.append({
                    "symbol": sym, "name": row["name"], "exchange": row["exchange"],
                    "yf_ticker": t, "sector": sector_map.get(sym.upper(), "Other"), **res,
                })

            # Qualifier gates — tightened to a focused, tradeable momentum set (~<500):
            #   in a real uptrend (above SMA50 AND SMA200), genuinely liquid,
            #   within X% of the 52w high, and positive 3M AND 6M momentum.
            if res["turnover_cr"] is None or res["turnover_cr"] < MIN_LIQUIDITY_CR:
                continue
            if MIN_PRICE and (res["close"] is None or res["close"] < MIN_PRICE):
                continue
            if not (res["price_gt_sma50"] and res["price_gt_sma200"]):
                continue
            if res["pct_off_high"] is None or res["pct_off_high"] > WITHIN_PCT_OF_52W_HIGH:
                continue
            if (res["r6m"] if res["r6m"] is not None else -1) <= 0:
                continue
            if (res["r3m"] if res["r3m"] is not None else -1) <= 0:
                continue
            rows.append({
                "symbol": sym,
                "name": row["name"],
                "exchange": row["exchange"],
                "yf_ticker": t,
                "sector": sector_map.get(sym.upper(), "Other"),
                **res,
            })
            continue

        # Recent IPO?
        ipo = analyze_ipo(df)
        if ipo is None:
            continue
        if ipo["turnover_cr"] is None or ipo["turnover_cr"] < MIN_IPO_LIQ_CR:
            continue
        if ipo["pct_off_high"] is None or ipo["pct_off_high"] > MAX_IPO_OFF_HIGH:
            continue
        # Must be in a real uptrend — above 20-bar SMA at minimum
        if not ipo["price_gt_sma20"]:
            continue
        ipo_rows.append({
            "symbol": sym,
            "name": row["name"],
            "exchange": row["exchange"],
            "yf_ticker": t,
            "sector": sector_map.get(sym.upper(), "Other"),
            **ipo,
        })

    df_out = pd.DataFrame(rows)
    if len(df_out):
        df_out["rs_rating"] = (
            df_out["momentum"].rank(pct=True).mul(100).round(0).astype(int)
        )
        # Setup-quality score (0-100): blend RS strength, ADR (tradeability),
        # strict trend, and whether it's coiling (contraction). Ranks the cleanest
        # Minervini/Qullamaggie-style setups to the top.
        adr_score = (df_out["adr_pct"].clip(upper=10).fillna(0) / 10 * 100)
        df_out["setup_quality"] = (
            0.45 * df_out["rs_rating"]
            + 0.20 * adr_score
            + 0.20 * df_out["trend_template"].astype(int) * 100
            + 0.15 * df_out["contracting"].astype(int) * 100
        ).round(0).astype(int)
        df_out = df_out.sort_values("rs_rating", ascending=False).reset_index(drop=True)

    # IPO list: rank by since-listing return (i.e. how much they've run since IPO)
    df_ipos = pd.DataFrame(ipo_rows)
    if len(df_ipos):
        df_ipos = df_ipos.sort_values(
            ["since_listing_pct", "r1m"], ascending=[False, False], na_position="last"
        ).reset_index(drop=True)

    # Episodic Pivots: rank by gap × volume surge (strongest repricing first)
    df_eps = pd.DataFrame(ep_rows)
    if len(df_eps):
        df_eps = df_eps.drop_duplicates("yf_ticker")
        df_eps["ep_score"] = (df_eps["gap_pct"].fillna(0) * df_eps["vol_surge"].fillna(0))
        # give EPs an RS-like 1-99 rank by strength so the table column + sort work
        df_eps["rs_rating"] = df_eps["ep_score"].rank(pct=True).mul(100).round(0).astype(int)
        df_eps = df_eps.sort_values("ep_score", ascending=False).reset_index(drop=True)

    breadth = _compute_breadth(all_results)
    sectors = _compute_sectors(df_out.to_dict(orient="records") if len(df_out) else [])
    print("[history] computing 2-year scanner + breadth history...", flush=True)
    try:
        history = _compute_history(prices)
    except Exception as exc:
        import traceback
        print(f"[history] FAILED (non-fatal): {exc}", flush=True)
        traceback.print_exc()
        history = {}

    finished = datetime.now(timezone.utc)
    meta = {
        "generated_at": finished.isoformat(),
        "duration_s": round((finished - started).total_seconds(), 1),
        "market": MARKET,
        "currency": CURRENCY,
        "universe_size": int(len(universe)),
        "with_data": int(len(prices)),
        "analyzed": int(len(all_results)),
        "qualifiers": int(len(df_out)),
        "ipos": int(len(df_ipos)),
        "episodic_pivots": int(len(df_eps)),
        "filters": {
            "min_liquidity_cr": MIN_LIQUIDITY_CR,
            "max_pct_below_52w_high": WITHIN_PCT_OF_52W_HIGH,
            "above_sma200": True,
            "ipo_min_liquidity_cr": MIN_IPO_LIQ_CR,
            "ipo_max_off_high": MAX_IPO_OFF_HIGH,
        },
    }

    payload = {
        "meta": meta,
        "breadth": breadth,
        "sectors": sectors,
        "results": df_out.to_dict(orient="records") if len(df_out) else [],
        "ipos": df_ipos.to_dict(orient="records") if len(df_ipos) else [],
        "episodic_pivots": df_eps.to_dict(orient="records") if len(df_eps) else [],
        "history": history,
    }
    # Pandas DataFrame coerces None -> NaN on numeric columns. json.dumps
    # writes NaN as the literal "NaN", which JavaScript JSON.parse rejects.
    # Walk the payload and replace NaN/Inf with None before serializing.
    import math
    def _clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_clean(x) for x in v]
        return v
    (DATA / f"scanner_output{OUT_SUFFIX}.json").write_text(
        json.dumps(_clean(payload), indent=2, allow_nan=False)
    )
    if len(df_out):
        df_out.to_csv(DATA / f"scanner_output{OUT_SUFFIX}.csv", index=False)
    print(
        f"[done] {meta['qualifiers']} qualifiers · {meta['ipos']} IPOs · regime "
        f"{breadth.get('regime_score', '?')} ({breadth.get('regime_label', '?')}) "
        f"in {meta['duration_s']}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
