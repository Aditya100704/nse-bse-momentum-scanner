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

MIN_LIQUIDITY_CR = float(os.getenv("SCAN_MIN_LIQ_CR", "2.0"))
WITHIN_PCT_OF_52W_HIGH = 25.0
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


def _download_one_batch(batch: list[str], period: str) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Download a batch; return (ok dict, failed list to retry)."""
    out: dict[str, pd.DataFrame] = {}
    try:
        data = yf.download(
            batch,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
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
            if len(df) >= 200:
                out[t] = df
        except (KeyError, TypeError):
            failed.append(t)
    return out, failed


def download_prices(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
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
    # Single retry pass for failed tickers (often Yahoo rate-limit casualties)
    if retry_pool:
        # Dedupe + retry in larger chunks with extra cooldown
        retry_pool = list(set(retry_pool) - set(out))
        print(f"[prices] retry pass on {len(retry_pool)} failed tickers", flush=True)
        time.sleep(5)
        for i in range(0, len(retry_pool), BATCH_SIZE):
            batch = retry_pool[i : i + BATCH_SIZE]
            ok, _ = _download_one_batch(batch, period)
            out.update(ok)
            time.sleep(SLEEP_BETWEEN_BATCHES + 1)
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

    sma50 = close.rolling(50).mean()
    sma150 = close.rolling(150).mean()
    sma200 = close.rolling(200).mean()
    sma200_prev = sma200.shift(22)

    last = close.iloc[-1]
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

    turnover_cr = (close * vol).iloc[-20:].mean() / 1e7
    avg_vol50 = vol.iloc[-50:].mean()
    vol_surge = vol.iloc[-1] / avg_vol50 if avg_vol50 > 0 else np.nan

    momentum = np.nanmean([
        (r12m if not pd.isna(r12m) else 0) * 0.4,
        (r6m if not pd.isna(r6m) else 0) * 0.3,
        (r3m if not pd.isna(r3m) else 0) * 0.2,
        (r1m if not pd.isna(r1m) else 0) * 0.1,
    ]) * 4

    crit = {
        "price_gt_sma50": bool(last > last50),
        "price_gt_sma150": bool(last > last150),
        "price_gt_sma200": bool(last > last200),
        "sma50_gt_sma150": bool(last50 > last150),
        "sma150_gt_sma200": bool(last150 > last200),
        "sma200_rising": bool(not pd.isna(last200_prev) and last200 > last200_prev),
        "within_25_of_high": bool(pct_off_high <= 25),
        "above_30_of_low": bool(low_52w > 0 and (last / low_52w - 1) >= 0.30),
    }
    trend_template = all(crit.values())

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
        "momentum": _safe_float(momentum),
        "trend_template": trend_template,
        **crit,
    }


def main() -> int:
    started = datetime.now(timezone.utc)
    universe = build_universe()
    universe.to_csv(DATA / "universe.csv", index=False)

    prices = download_prices(universe["yf_ticker"].tolist())
    print(f"[prices] got {len(prices)} of {len(universe)} symbols")

    rows: list[dict] = []
    for _, row in universe.iterrows():
        t = row["yf_ticker"]
        if t not in prices:
            continue
        res = analyze(prices[t])
        if res is None:
            continue
        if res["turnover_cr"] is None or res["turnover_cr"] < MIN_LIQUIDITY_CR:
            continue
        if not res["price_gt_sma200"]:
            continue
        rows.append({
            "symbol": row["symbol"],
            "name": row["name"],
            "exchange": row["exchange"],
            "yf_ticker": t,
            **res,
        })

    df_out = pd.DataFrame(rows)
    if len(df_out):
        df_out["rs_rating"] = (
            df_out["momentum"].rank(pct=True).mul(100).round(0).astype(int)
        )
        df_out = df_out.sort_values("rs_rating", ascending=False).reset_index(drop=True)

    finished = datetime.now(timezone.utc)
    meta = {
        "generated_at": finished.isoformat(),
        "duration_s": round((finished - started).total_seconds(), 1),
        "universe_size": int(len(universe)),
        "with_data": int(len(prices)),
        "qualifiers": int(len(df_out)),
        "filters": {
            "min_liquidity_cr": MIN_LIQUIDITY_CR,
            "max_pct_below_52w_high": WITHIN_PCT_OF_52W_HIGH,
            "above_sma200": True,
        },
    }

    payload = {"meta": meta, "results": df_out.to_dict(orient="records") if len(df_out) else []}
    (DATA / "scanner_output.json").write_text(json.dumps(payload, indent=2))
    if len(df_out):
        df_out.to_csv(DATA / "scanner_output.csv", index=False)
    print(f"[done] {meta['qualifiers']} qualifiers in {meta['duration_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
