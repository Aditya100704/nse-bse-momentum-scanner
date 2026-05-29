"""
US market data layer — the keyless analog of bhavcopy.py for the US universe.

Two pieces, mirroring the India stack:
  1. build_us_universe()  -> the COMPLETE US common-stock universe (symbol, name,
     exchange) from the official Nasdaq Trader symbol directory files (free, no key):
         https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt   (NASDAQ)
         https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt    (NYSE/AMEX/ARCA)
     filtered to common stock (drop ETFs, test issues, warrants/units/rights/preferreds/notes).
  2. fetch_us_history()   -> {SYMBOL: OHLCV DataFrame}, {SYMBOL: meta} via yfinance batch
     download, chunked + retried. Same shape as bhavcopy.fetch_nse_history so scan.py's
     analyze() is market-agnostic. (Stooq went API-key-gated in 2026; yfinance is the only
     complete keyless source — and it returns ~all established US names in ~10 min.)

yfinance is keyless and works for the US at universe scale (the India rate-limit pain was
~8k symbols WITH repeated runs; ~5k US in one chunked pass with retries comes back clean).
"""
from __future__ import annotations
import io
import time
import urllib.request as _U
import warnings
import pandas as pd

warnings.filterwarnings("ignore")

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# exchange-code -> label (otherlisted "Exchange" column)
_EXCH = {"N": "NYSE", "A": "AMEX", "P": "ARCA", "Z": "BATS", "V": "IEX"}


def _is_equity(name: str) -> bool:
    """Inclusion test for the tradeable equity universe. KEEPS common stock, ordinary
    shares, class A/B/C shares, REITs, AND American Depositary Receipts (ADRs like TSM,
    ASML, BABA — major momentum names). DROPS only non-equity instruments: warrants,
    units (SPAC), rights, preferred shares (incl preferred depositary shares), notes/
    debentures, and ETNs. Word-boundary checks so 'United'/'Communities' aren't dropped.
    This matches the official ~5,600 exchange-listed company count."""
    n = name.lower()
    words = set(n.replace(",", " ").replace(".", " ").split())
    if "warrant" in n or "warrants" in n:
        return False
    if words & {"right", "rights"}:
        return False
    if words & {"unit", "units"}:                 # SPAC units (keeps 'United', 'Opportunities')
        return False
    if "preferred" in n:                          # ordinary AND preferred depositary shares
        return False
    if "notes" in n or "debenture" in n or "subordinated" in n:
        return False
    if " etn" in n or n.endswith("etn"):
        return False
    return True                                   # common / ordinary / class / ADR / REIT / other


def _get(url: str, timeout: int = 60) -> str:
    req = _U.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return _U.urlopen(req, timeout=timeout).read().decode("latin-1")


def _clean_symbol(sym: str) -> str | None:
    """Keep ordinary tickers; convert class dots to dashes for yfinance (BRK.B -> BRK-B).
    Drop symbols carrying warrant/unit/right/preferred suffixes ($, =, ^ or W/U/R/P 5th char)."""
    sym = sym.strip().upper()
    if not sym or any(c in sym for c in ("$", "=", "^", " ")):
        return None
    yf = sym.replace(".", "-")
    if len(yf.replace("-", "")) > 5:
        return None
    return yf


def build_us_universe() -> pd.DataFrame:
    """Return DataFrame[symbol, yf_ticker, name, exchange] of US common stocks.
    symbol == yf_ticker for the US (no suffix), so scan.py keys work unchanged."""
    rows: list[dict] = []
    seen: set[str] = set()

    # --- NASDAQ-listed: Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot|ETF|NextShares
    for ln in _get(NASDAQ_LISTED).splitlines()[1:]:
        if ln.startswith("File Creation"):
            continue
        f = ln.split("|")
        if len(f) < 8 or f[3].strip() == "Y" or f[6].strip() == "Y":  # test issue / ETF
            continue
        name = f[1]
        if not _is_equity(name):
            continue
        yf = _clean_symbol(f[0])
        if not yf or yf in seen:
            continue
        seen.add(yf)
        rows.append({"symbol": yf, "yf_ticker": yf, "name": name.strip(), "exchange": "NASDAQ"})

    # --- otherlisted: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot|Test Issue|NASDAQ Symbol
    for ln in _get(OTHER_LISTED).splitlines()[1:]:
        if ln.startswith("File Creation"):
            continue
        f = ln.split("|")
        if len(f) < 8 or f[6].strip() == "Y" or f[4].strip() == "Y":  # test issue / ETF
            continue
        name = f[1]
        if not _is_equity(name):
            continue
        yf = _clean_symbol(f[0])
        if not yf or yf in seen:
            continue
        seen.add(yf)
        rows.append({"symbol": yf, "yf_ticker": yf, "name": name.strip(),
                     "exchange": _EXCH.get(f[2].strip(), "US")})

    df = pd.DataFrame(rows).drop_duplicates("yf_ticker").reset_index(drop=True)
    print(f"[usdata] universe: {len(df)} US common stocks "
          f"(NASDAQ {int((df.exchange=='NASDAQ').sum())}, "
          f"NYSE {int((df.exchange=='NYSE').sum())}, "
          f"AMEX {int((df.exchange=='AMEX').sum())}, "
          f"other {int((~df.exchange.isin(['NASDAQ','NYSE','AMEX'])).sum())})", flush=True)
    return df


def fetch_us_history(symbols: list[str], period: str = "2y", min_bars: int = 30,
                     chunk: int = 160, pause: float = 0.4, retries: int = 2
                     ) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    """Batch-download daily OHLCV via yfinance, chunked + retried. Returns
    ({SYMBOL: DataFrame[Open,High,Low,Close,Volume]}, {SYMBOL: meta})."""
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    meta: dict[str, dict] = {}
    total = len(symbols)
    done = 0
    for i in range(0, total, chunk):
        batch = symbols[i:i + chunk]
        dl = None
        for attempt in range(retries + 1):
            try:
                dl = yf.download(batch, period=period, interval="1d", auto_adjust=True,
                                 group_by="ticker", threads=True, progress=False)
                if dl is not None and len(dl) > 0:
                    break
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        if dl is None or len(dl) == 0:
            continue
        for s in batch:
            try:
                sub = dl[s] if len(batch) > 1 else dl
                df = pd.DataFrame({
                    "Open": sub["Open"], "High": sub["High"], "Low": sub["Low"],
                    "Close": sub["Close"], "Volume": sub["Volume"],
                }).dropna(subset=["Close"])
                if len(df) >= min_bars:
                    out[s] = df
                    meta[s] = {"symbol": s, "name": s, "exchange": "US"}
            except Exception:
                continue
        done += len(batch)
        print(f"[usdata] fetched {done}/{total} (kept {len(out)})", flush=True)
        time.sleep(pause)
    print(f"[usdata] reconstructed {len(out)} US symbols with >= {min_bars} bars", flush=True)
    return out, meta


if __name__ == "__main__":
    uni = build_us_universe()
    print(uni.head(10).to_string())
    print("…", len(uni), "symbols")
