"""
NSE bhavcopy history reconstructor.

Yahoo Finance silently omits thousands of NSE/BSE listings. The NSE daily
bhavcopy (one CSV per trading day, hosted on a GitHub mirror so there's no
NSE bot-detection in the way) contains EVERY NSE-listed security for that day.
We fetch ~2 years of daily files concurrently and pivot them into per-stock
Close + Volume series — giving near-100% NSE coverage.

Used as a *fill* for tickers Yahoo missed (Yahoo prices are split-adjusted;
bhavcopy is raw, so we prefer Yahoo where available and only backfill gaps).
"""

from __future__ import annotations

import concurrent.futures as cf
import io
from datetime import date, timedelta

import pandas as pd
import requests

MIRROR = "https://raw.githubusercontent.com/tilak999/NSE-Data-bank/main/data/sec_bhavdata_full_{}.csv"
EQUITY_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; PhenomScanner/1.0)"}

# Shared session with a wide connection pool — big speedup vs a fresh
# connection per request when fetching hundreds of files concurrently.
_SESSION = requests.Session()
_SESSION.headers.update(UA)
_adapter = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64, max_retries=2)
_SESSION.mount("https://", _adapter)


def _trading_day_candidates(days_back: int) -> list[date]:
    """Weekdays going back ~days_back trading days. Weekdays already exclude
    weekends; we add a small buffer for the ~15 holidays/year (the 404s on
    holiday dates self-prune during fetch)."""
    today = date.today()
    out: list[date] = []
    d = today
    span = days_back + 45  # buffer for ~2 years of NSE holidays
    while len(out) < span:
        if d.weekday() < 5:  # Mon–Fri
            out.append(d)
        d -= timedelta(days=1)
    return out


def _fetch_one(d: date) -> pd.DataFrame | None:
    url = MIRROR.format(d.strftime("%d%m%Y"))
    try:
        r = _SESSION.get(url, timeout=25)
        if r.status_code != 200 or "SYMBOL" not in r.text[:200]:
            return None
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        df["SERIES"] = df["SERIES"].astype(str).str.strip()
        df = df[df["SERIES"].isin(EQUITY_SERIES)]
        if df.empty:
            return None
        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
        out = pd.DataFrame({
            "symbol": df["SYMBOL"],
            "date": pd.Timestamp(d),
            "open":  pd.to_numeric(df.get("OPEN_PRICE"), errors="coerce"),
            "high":  pd.to_numeric(df.get("HIGH_PRICE"), errors="coerce"),
            "low":   pd.to_numeric(df.get("LOW_PRICE"), errors="coerce"),
            "close": pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
            "volume": pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
        })
        return out.dropna(subset=["close"])
    except Exception:
        return None


def fetch_nse_history(days_back: int = 504, max_workers: int = 32,
                      min_bars: int = 30) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    """Return ({SYMBOL.NS: DataFrame[Close, Volume]}, {SYMBOL.NS: meta}).
    NSE bhavcopy has no company name, so meta name = the symbol."""
    candidates = _trading_day_candidates(days_back)
    frames: list[pd.DataFrame] = []
    fetched = 0
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for res in ex.map(_fetch_one, candidates):
            if res is not None:
                frames.append(res)
                fetched += 1
    print(f"[bhavcopy] fetched {fetched} daily files", flush=True)
    if not frames:
        return {}, {}

    big = pd.concat(frames, ignore_index=True)
    out: dict[str, pd.DataFrame] = {}
    meta: dict[str, dict] = {}
    for sym, g in big.groupby("symbol"):
        g = g.sort_values("date").drop_duplicates("date").set_index("date")
        ser = pd.DataFrame({
            "Open": g.get("open"), "High": g.get("high"), "Low": g.get("low"),
            "Close": g["close"], "Volume": g["volume"],
        }).dropna(subset=["Close"])
        if len(ser) >= min_bars:
            t = f"{sym}.NS"
            out[t] = ser
            meta[t] = {"symbol": sym, "name": sym, "exchange": "NSE"}
    print(f"[bhavcopy] reconstructed {len(out)} NSE symbols", flush=True)
    return out, meta


# ============================================================ BSE
# BSE's own bhavcopy (UDiFF CSV) is directly downloadable — no bot detection.
# FinInstrmId is the BSE scrip code, which is exactly our `<code>.BO` ticker.
BSE_MIRROR = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{}_F_0000.CSV"
BSE_SERIES = {"A", "B", "T", "X", "XT", "Z", "M", "MT", "MS"}  # tradeable equity groups


def _fetch_one_bse(d: date) -> pd.DataFrame | None:
    url = BSE_MIRROR.format(d.strftime("%Y%m%d"))
    try:
        r = _SESSION.get(url, timeout=25)
        if r.status_code != 200 or "TckrSymb" not in r.text[:300]:
            return None
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        if "SctySrs" in df.columns:
            df = df[df["SctySrs"].astype(str).str.strip().isin(BSE_SERIES)]
        if df.empty:
            return None
        return pd.DataFrame({
            "code": df["FinInstrmId"].astype(str).str.strip(),
            "tckr": df["TckrSymb"].astype(str).str.strip() if "TckrSymb" in df.columns else df["FinInstrmId"].astype(str),
            "name": df["FinInstrmNm"].astype(str).str.strip() if "FinInstrmNm" in df.columns else "",
            "date": pd.Timestamp(d),
            "open":  pd.to_numeric(df.get("OpnPric"), errors="coerce"),
            "high":  pd.to_numeric(df.get("HghPric"), errors="coerce"),
            "low":   pd.to_numeric(df.get("LwPric"),  errors="coerce"),
            "close": pd.to_numeric(df["ClsPric"], errors="coerce"),
            "volume": pd.to_numeric(df["TtlTradgVol"], errors="coerce"),
        }).dropna(subset=["close"])
    except Exception:
        return None


def fetch_bse_history(days_back: int = 460, max_workers: int = 16,
                      min_bars: int = 30) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    """Return ({SCRIPCODE.BO: DataFrame[Close, Volume]}, {SCRIPCODE.BO: meta}).
    meta carries the alpha ticker + company name from the BSE UDiFF file."""
    candidates = _trading_day_candidates(days_back)
    frames: list[pd.DataFrame] = []
    fetched = 0
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for res in ex.map(_fetch_one_bse, candidates):
            if res is not None:
                frames.append(res)
                fetched += 1
    print(f"[bhavcopy] BSE fetched {fetched} daily files", flush=True)
    if not frames:
        return {}, {}
    big = pd.concat(frames, ignore_index=True)
    out: dict[str, pd.DataFrame] = {}
    meta: dict[str, dict] = {}
    for code, g in big.groupby("code"):
        g = g.sort_values("date").drop_duplicates("date").set_index("date")
        ser = pd.DataFrame({"Close": g["close"], "Volume": g["volume"]}).dropna(subset=["Close"])
        if len(ser) >= min_bars:
            t = f"{code}.BO"
            out[t] = ser
            last = g.iloc[-1]
            meta[t] = {"symbol": str(last.get("tckr", code)) or code,
                       "name": str(last.get("name", "")) or str(code),
                       "exchange": "BSE"}
    print(f"[bhavcopy] BSE reconstructed {len(out)} symbols", flush=True)
    return out, meta


if __name__ == "__main__":
    import sys
    import time
    which = sys.argv[1] if len(sys.argv) > 1 else "nse"
    t0 = time.time()
    if which == "bse":
        hist, _meta = fetch_bse_history(days_back=460)
        checks = ["500325.BO", "500002.BO", "543320.BO"]
    else:
        hist, _meta = fetch_nse_history(days_back=504)
        checks = ["RELIANCE.NS", "HFCL.NS", "BLISSGVS.NS", "MTARTECH.NS", "20MICRONS.NS"]
    dt = time.time() - t0
    print(f"\n[bhavcopy] {len(hist)} symbols in {dt:.1f}s")
    for s in checks:
        if s in hist:
            df = hist[s]
            print(f"  {s:16s} bars={len(df):4d}  last_close={df['Close'].iloc[-1]:.2f}  last_date={df.index[-1].date()}")
        else:
            print(f"  {s:16s} MISSING")
