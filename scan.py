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

MIN_LIQUIDITY_CR = 2.0
WITHIN_PCT_OF_52W_HIGH = 25.0
BATCH_SIZE = 60
SLEEP_BETWEEN_BATCHES = 0.5
INCLUDE_BSE = True

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


NSE_SOURCES = [
    # Official archive (cookie-gated; preferred — has the full ~2000 EQ universe)
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    # Mirrors (smaller / older but unauthenticated)
    "https://raw.githubusercontent.com/kprohith/nse-stock-analysis/master/ind_nifty500list.csv",
]


def _fetch_nse_csv() -> str:
    """Hit NSE with a properly primed browser session; fall back to mirrors."""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": UA["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    })
    # NSE issues anti-bot cookies on home page + one inner page
    for warmup in ("https://www.nseindia.com/",
                   "https://www.nseindia.com/market-data/securities-available-for-trading"):
        try:
            sess.get(warmup, timeout=15)
        except Exception as e:
            print(f"[universe] NSE warmup soft-fail {warmup}: {e}")
    sess.headers.update({
        "Accept": "text/csv,application/csv,*/*",
        "Referer": "https://www.nseindia.com/market-data/securities-available-for-trading",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    })
    last_exc: Exception | None = None
    for url in NSE_SOURCES:
        try:
            r = sess.get(url, timeout=30)
            if r.status_code == 200 and "," in r.text and "ymbol" in r.text:
                print(f"[universe] NSE source OK: {url} ({len(r.text)} bytes)")
                return r.text
            last_exc = RuntimeError(f"{url} -> HTTP {r.status_code}")
            print(f"[universe] NSE source returned {r.status_code}: {url}")
        except Exception as e:
            last_exc = e
            print(f"[universe] NSE source failed ({url}): {e}")
    raise RuntimeError(f"all NSE sources failed; last: {last_exc}")


def fetch_nse_universe() -> pd.DataFrame:
    text = _fetch_nse_csv()
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    # Tolerate both schemas: official ('SYMBOL', 'NAME OF COMPANY', 'SERIES')
    # and the kprohith mirror ('Symbol', 'Company Name', 'Series')
    sym_col = next((c for c in df.columns if c.upper() == "SYMBOL"), None)
    name_col = next((c for c in df.columns
                     if "NAME" in c.upper() or "COMPANY" in c.upper()), None)
    series_col = next((c for c in df.columns if c.upper() == "SERIES"), None)
    if not (sym_col and name_col):
        raise RuntimeError(f"unexpected NSE columns: {df.columns.tolist()}")
    if series_col:
        df = df[df[series_col].astype(str).str.strip().isin(["EQ", "BE", "BZ"])].copy()
    df["symbol"] = df[sym_col].astype(str).str.strip()
    df["yf_ticker"] = df["symbol"] + ".NS"
    df["name"] = df[name_col].astype(str).str.strip()
    df["exchange"] = "NSE"
    return df[["symbol", "yf_ticker", "name", "exchange"]].drop_duplicates("symbol").reset_index(drop=True)


def fetch_bse_universe() -> pd.DataFrame:
    url = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
    print(f"[universe] BSE -> {url}")
    try:
        r = requests.get(
            url,
            headers={**UA, "Referer": "https://www.bseindia.com/"},
            params={"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"},
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
        df = pd.DataFrame(payload if isinstance(payload, list) else payload.get("Table", []))
        if df.empty:
            raise RuntimeError("empty response")
        df.columns = [c.strip() for c in df.columns]
        sc_id = next((c for c in df.columns if c.lower() in ("scrip_id", "scripid", "scrip id")), None)
        sc_cd = next((c for c in df.columns if c.lower() in ("scrip_cd", "scripcd", "scripcode", "scrip code")), None)
        sc_nm = next((c for c in df.columns if c.lower() in ("scrip_name", "scripname", "scrip name", "issuer_name")), None)
        if not (sc_id and sc_cd and sc_nm):
            raise RuntimeError(f"unexpected columns: {df.columns.tolist()}")
        df["symbol"] = df[sc_id].astype(str).str.strip()
        df["yf_ticker"] = df[sc_cd].astype(str).str.strip() + ".BO"
        df["name"] = df[sc_nm].astype(str).str.strip()
        df["exchange"] = "BSE"
        return df[["symbol", "yf_ticker", "name", "exchange"]].reset_index(drop=True)
    except Exception as exc:
        print(f"[universe] BSE fetch failed ({exc}); continuing NSE-only")
        return pd.DataFrame(columns=["symbol", "yf_ticker", "name", "exchange"])


def build_universe() -> pd.DataFrame:
    nse = fetch_nse_universe()
    bse = fetch_bse_universe() if INCLUDE_BSE else pd.DataFrame(columns=nse.columns)
    only_bse = bse[~bse["symbol"].isin(nse["symbol"])] if not bse.empty else bse
    universe = pd.concat([nse, only_bse], ignore_index=True)
    print(f"[universe] NSE={len(nse)} BSE_only_added={len(only_bse)} total={len(universe)}")
    return universe


def download_prices(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    total = len(tickers)
    batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, total, BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        print(f"[prices] batch {i // BATCH_SIZE + 1}/{batches} "
              f"({i + 1}-{min(i + BATCH_SIZE, total)}/{total})")
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
            print(f"[prices] batch failed: {exc}")
            time.sleep(2)
            continue
        for t in batch:
            try:
                df = data if len(batch) == 1 else data[t]
                df = df.dropna(subset=["Close"])
                if len(df) >= 200:
                    out[t] = df
            except (KeyError, TypeError):
                continue
        time.sleep(SLEEP_BETWEEN_BATCHES)
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
