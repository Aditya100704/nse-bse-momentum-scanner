"""
Build data/tickers.json — every active NSE + BSE symbol with its preferred
exchange (NSE if it trades there, else BSE) AND its company name. Powers the
Trade tab's ticker autocomplete (type-to-narrow dropdown showing SYMBOL + name;
exchange auto-defaults to NSE).

Symbols + exchange come from the latest NSE + BSE bhavcopy day (what actually
trades). Names come from the cached Zerodha/Kite instruments dump
(data/kite_instruments.csv) with BSE bhavcopy names as a fallback.

Run standalone to seed; also a step in the daily scan workflow.
"""
from __future__ import annotations
import io, json, os, sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import pandas as pd, requests

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

MARKET = os.getenv("SCAN_MARKET", "in").strip().lower()
DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(exist_ok=True)
OUT = DATA / ("tickers_us.json" if MARKET == "us" else "tickers.json")

UA = {"User-Agent": "Mozilla/5.0 (compatible; PhenomScanner/1.0)"}
S = requests.Session(); S.headers.update(UA)
NSE = "https://raw.githubusercontent.com/tilak999/NSE-Data-bank/main/data/sec_bhavdata_full_{}.csv"
BSE = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{}_F_0000.CSV"
NSE_SERIES = {"EQ", "BE", "SM", "ST"}
BSE_SERIES = {"A", "B", "T", "X", "XT", "Z", "M", "MT", "MS"}


def _days(n=12):
    out, d = [], date.today()
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= timedelta(days=1)
    return out


def nse_symbols():
    for d in _days():
        try:
            r = S.get(NSE.format(d.strftime("%d%m%Y")), timeout=25)
            if r.status_code == 200 and "SYMBOL" in r.text[:200]:
                df = pd.read_csv(io.StringIO(r.text)); df.columns = [c.strip() for c in df.columns]
                df["SERIES"] = df["SERIES"].astype(str).str.strip()
                df = df[df["SERIES"].isin(NSE_SERIES)]
                return sorted({s.strip().upper() for s in df["SYMBOL"].astype(str)})
        except Exception:
            pass
    return []


def bse_rows():
    """Return {SYMBOL: name} for active BSE securities."""
    for d in _days():
        try:
            r = S.get(BSE.format(d.strftime("%Y%m%d")), timeout=25)
            if r.status_code == 200 and "TckrSymb" in r.text[:300]:
                df = pd.read_csv(io.StringIO(r.text)); df.columns = [c.strip() for c in df.columns]
                if "SctySrs" in df.columns:
                    df = df[df["SctySrs"].astype(str).str.strip().isin(BSE_SERIES)]
                sym_col = "TckrSymb" if "TckrSymb" in df.columns else "FinInstrmId"
                nm_col = "FinInstrmNm" if "FinInstrmNm" in df.columns else sym_col
                out = {}
                for s, n in zip(df[sym_col].astype(str), df[nm_col].astype(str)):
                    s = s.strip().upper()
                    if s and s[0].isalpha():
                        out[s] = n.strip()
                return out
        except Exception:
            pass
    return {}


def kite_names():
    """{SYMBOL: company name} from the cached Kite instruments dump (EQ only)."""
    p = DATA / "kite_instruments.csv"
    names = {}
    if not p.exists():
        return names
    try:
        df = pd.read_csv(p, usecols=["tradingsymbol", "name", "instrument_type", "exchange"])
    except Exception:
        return names
    df = df[df["instrument_type"].astype(str) == "EQ"]
    # BSE first then NSE so NSE (cleaner) names win on collision
    for ex in ["BSE", "NSE"]:
        sub = df[df["exchange"].astype(str) == ex]
        for ts, nm in zip(sub["tradingsymbol"].astype(str), sub["name"].astype(str)):
            ts = str(ts).strip().upper(); nm = str(nm).strip()
            if not nm or nm.lower() == "nan":
                continue
            names[ts] = nm
            names.setdefault(ts.split("-")[0], nm)  # strip SME/series suffix
    return names


def main():
    if MARKET == "us":
        import usdata
        uni = usdata.build_us_universe()
        tickers = [[r["symbol"], r["exchange"], r["name"]] for _, r in uni.iterrows()]
        named = sum(1 for t in tickers if t[2])
        OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(),
                                   "count": len(tickers), "named": named, "tickers": tickers},
                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"[tickers] US {len(tickers)} symbols ({named} named) -> {OUT}", flush=True)
        return

    nse = nse_symbols()
    bse = bse_rows()
    kite = kite_names()

    mp = {}
    for s in bse:
        mp[s] = "BSE"
    for s in nse:                 # NSE wins
        mp[s] = "NSE"

    tickers = []
    for s in sorted(mp):
        nm = kite.get(s) or kite.get(s.split("-")[0]) or bse.get(s) or ""
        tickers.append([s, mp[s], nm])

    named = sum(1 for t in tickers if t[2])
    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "count": len(tickers), "named": named,
           "tickers": tickers}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[tickers] {len(tickers)} symbols ({named} with names) -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
