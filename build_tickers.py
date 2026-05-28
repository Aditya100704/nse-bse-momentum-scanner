"""
Build data/tickers.json — every active NSE + BSE symbol with its preferred
exchange (NSE if the symbol trades there, else BSE). Powers the Trade tab's
ticker autocomplete (type-to-narrow dropdown, exchange auto-defaults to NSE).

Pulls the latest NSE + BSE bhavcopy day (one file each, ~5s). Run standalone to
seed, and as a step in the daily scan workflow to keep it fresh.
"""
from __future__ import annotations
import io, json, sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import pandas as pd, requests

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(exist_ok=True)
OUT = DATA / "tickers.json"

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


def bse_symbols():
    for d in _days():
        try:
            r = S.get(BSE.format(d.strftime("%Y%m%d")), timeout=25)
            if r.status_code == 200 and "TckrSymb" in r.text[:300]:
                df = pd.read_csv(io.StringIO(r.text)); df.columns = [c.strip() for c in df.columns]
                if "SctySrs" in df.columns:
                    df = df[df["SctySrs"].astype(str).str.strip().isin(BSE_SERIES)]
                col = "TckrSymb" if "TckrSymb" in df.columns else "FinInstrmId"
                return sorted({s.strip().upper() for s in df[col].astype(str) if s.strip()})
        except Exception:
            pass
    return []


def main():
    nse = nse_symbols()
    bse = bse_symbols()
    mp = {}
    for s in bse:
        if s and s[0].isalpha():        # skip pure scrip-code rows
            mp[s] = "BSE"
    for s in nse:                        # NSE wins (overwrites BSE)
        mp[s] = "NSE"
    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "counts": {"nse": len(nse), "bse": len([s for s in mp if mp[s] == 'BSE']), "total": len(mp)},
           "map": dict(sorted(mp.items()))}
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"[tickers] NSE={len(nse)} BSE-only={out['counts']['bse']} total={len(mp)} -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
