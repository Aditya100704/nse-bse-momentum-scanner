"""Render daily + hourly candlestick charts (with SMAs + volume) for a list of
symbols, so VCP/setup verification happens on the timeframe the setup lives on.

Lesson (2026-06-03): a DAILY chart compresses a multi-week intraday base into what
looks like a vertical spike (DOCN). Judge the *entry/base* on the HOURLY. This helper
makes that a repeatable step. US tickers (yfinance bare symbol).

Usage:
    python research/render_charts.py DOCN AAON CX            # both 1d + 1h
    python research/render_charts.py SAIA --tf 1h            # hourly only
Outputs PNGs to data/_charts/<SYM>_<tf>.png
"""
import sys, os, warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import mplfinance as mpf

args = sys.argv[1:]
tf_only = None
if "--tf" in args:
    i = args.index("--tf"); tf_only = args[i + 1]; del args[i:i + 2]
syms = [a.upper() for a in args if not a.startswith("--")]
os.makedirs("data/_charts", exist_ok=True)

PLAN = {
    "1d": dict(period="2y", interval="1d", mav=(10, 20, 50, 200), n=290),
    "1h": dict(period="3mo", interval="60m", mav=(10, 20, 50), n=420),
}

def render(sym, tf):
    cfg = PLAN[tf]
    o = yf.download(sym, period=cfg["period"], interval=cfg["interval"],
                    progress=False, auto_adjust=False, threads=False)
    if o is None or len(o) < 30:
        print(f"{sym} {tf}: too few bars"); return
    if getattr(o.columns, "nlevels", 1) > 1:
        o.columns = o.columns.get_level_values(0)
    o = o.dropna().tail(cfg["n"])
    mpf.plot(o[["Open", "High", "Low", "Close", "Volume"]], type="candle",
             mav=cfg["mav"], volume=True, style="yahoo", title=f"{sym}  {tf}",
             figratio=(16, 9), figscale=1.3, tight_layout=True,
             savefig=dict(fname=f"data/_charts/{sym}_{tf}.png", dpi=100))
    print(f"{sym} {tf} ok")

if __name__ == "__main__":
    tfs = [tf_only] if tf_only else ["1d", "1h"]
    for s in syms:
        for tf in tfs:
            try:
                render(s, tf)
            except Exception as e:
                print(f"{s} {tf}: {e}")
