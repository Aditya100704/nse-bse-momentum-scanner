"""
Are we capturing all ACTIVE NSE+BSE stocks, or is there more?
Pulls the most recent NSE + BSE bhavcopy day and counts how many securities
actually traded, at several liquidity thresholds, vs what our scan ingests.
"""
from __future__ import annotations
import io, sys
from datetime import date, timedelta
import pandas as pd, requests

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "Mozilla/5.0 (compatible; PhenomResearch/1.0)"}
S = requests.Session(); S.headers.update(UA)
NSE = "https://raw.githubusercontent.com/tilak999/NSE-Data-bank/main/data/sec_bhavdata_full_{}.csv"
BSE = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{}_F_0000.CSV"

def recent_days(n=12):
    out, d = [], date.today()
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= timedelta(days=1)
    return out

def latest_nse():
    for d in recent_days():
        try:
            r = S.get(NSE.format(d.strftime("%d%m%Y")), timeout=25)
            if r.status_code == 200 and "SYMBOL" in r.text[:200]:
                df = pd.read_csv(io.StringIO(r.text)); df.columns=[c.strip() for c in df.columns]
                df["SERIES"]=df["SERIES"].astype(str).str.strip()
                return d, df
        except Exception: pass
    return None, None

def latest_bse():
    for d in recent_days():
        try:
            r = S.get(BSE.format(d.strftime("%Y%m%d")), timeout=25)
            if r.status_code == 200 and "TckrSymb" in r.text[:300]:
                df = pd.read_csv(io.StringIO(r.text)); df.columns=[c.strip() for c in df.columns]
                return d, df
        except Exception: pass
    return None, None

def main():
    nd, ndf = latest_nse()
    print(f"== NSE latest day: {nd} ==")
    if ndf is not None:
        eq = ndf[ndf["SERIES"].isin(["EQ","BE"])].copy()
        eq["CLOSE_PRICE"]=pd.to_numeric(eq["CLOSE_PRICE"],errors="coerce")
        eq["TTL_TRD_QNTY"]=pd.to_numeric(eq["TTL_TRD_QNTY"],errors="coerce")
        eq["turn_cr"]=eq["CLOSE_PRICE"]*eq["TTL_TRD_QNTY"]/1e7
        allser = ndf["SERIES"].value_counts().to_dict()
        print(f"  all series counts: {allser}")
        print(f"  EQ+BE securities traded: {eq['SYMBOL'].nunique()}")
        print(f"  ...with volume>0:        {(eq['TTL_TRD_QNTY']>0).sum()}")
        print(f"  ...turnover >= 1 cr:     {(eq['turn_cr']>=1).sum()}")
        print(f"  ...turnover >= 2 cr:     {(eq['turn_cr']>=2).sum()}")
        print(f"  ...turnover >= 10 cr:    {(eq['turn_cr']>=10).sum()}")

    bd, bdf = latest_bse()
    print(f"\n== BSE latest day: {bd} ==")
    if bdf is not None:
        if "SctySrs" in bdf.columns:
            grp = bdf["SctySrs"].astype(str).str.strip()
            tradeable = bdf[grp.isin(["A","B","T","X","XT","Z","M","MT","MS"])].copy()
        else:
            tradeable = bdf.copy()
        tradeable["ClsPric"]=pd.to_numeric(tradeable["ClsPric"],errors="coerce")
        tradeable["TtlTradgVol"]=pd.to_numeric(tradeable["TtlTradgVol"],errors="coerce")
        tradeable["turn_cr"]=tradeable["ClsPric"]*tradeable["TtlTradgVol"]/1e7
        print(f"  tradeable-group securities: {tradeable['FinInstrmId'].nunique()}")
        print(f"  ...with volume>0:           {(tradeable['TtlTradgVol']>0).sum()}")
        print(f"  ...turnover >= 1 cr:        {(tradeable['turn_cr']>=1).sum()}")
        print(f"  ...turnover >= 2 cr:        {(tradeable['turn_cr']>=2).sum()}")
        print(f"  ...turnover >= 10 cr:       {(tradeable['turn_cr']>=10).sum()}")

    # how many UNIQUE names trade on a given day, combined, with vol>0
    print("\n== takeaway ==")
    print("Our scan ingests ~8,178 stocks (any name that traded >=30 days in the last year).")
    print("Compare the 'volume>0' single-day counts above: that's how many are active on a typical day.")

if __name__ == "__main__":
    main()
