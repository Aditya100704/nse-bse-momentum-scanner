"""Targeted test: verify the missing 9 chartink names now survive
download_prices() with the silently-dropped-ticker fix."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scan import download_prices, analyze, MIN_LIQUIDITY_CR  # noqa: E402

CHARTINK_MISSING = [
    "HFCL.NS", "NETWEB.NS", "SIGMAADV.NS", "SILVERTUC.NS",
    "KIRLOSENG.NS", "POWERINDIA.NS", "MAYURUNIQ.NS", "DSSL.NS", "BLISSGVS.NS",
]
# Mix in some Nifty heavies so the batch resembles a real run
PADDING = [f"{s}.NS" for s in [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN","ITC","HINDUNILVR",
    "BHARTIARTL","LT","KOTAKBANK","AXISBANK","MARUTI","ASIANPAINT","TITAN",
    "WIPRO","BAJFINANCE","ADANIENT","SUNPHARMA","ULTRACEMCO",
]]

batch = CHARTINK_MISSING + PADDING
print(f"[test] downloading {len(batch)} tickers (9 missing + 20 padding)...")
prices = download_prices(batch)
print(f"[test] got {len(prices)} of {len(batch)}")

print("\nResults for the 9 previously missing names:")
for t in CHARTINK_MISSING:
    if t in prices:
        bars = len(prices[t])
        a = analyze(prices[t])
        if a:
            liq = a["turnover_cr"]
            qualifies = liq >= MIN_LIQUIDITY_CR and a["price_gt_sma200"]
            print(f"  OK  {t:16s} bars={bars}  liq={liq}cr  >SMA200={a['price_gt_sma200']}  TT={a['trend_template']}  qualifies={qualifies}")
        else:
            print(f"  PARTIAL  {t:16s} bars={bars}  (<200, expected for new IPOs)")
    else:
        print(f"  MISSING  {t}  - still being dropped by bulk fetch")
