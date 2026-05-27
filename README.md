# NSE + BSE Momentum Scanner

A daily momentum scanner over the full NSE + BSE equity universe (~7,000 stocks), ranked Minervini-style.

**Live dashboard:** see GitHub Pages once enabled · dark-themed, sortable, filterable.

## What it does

1. Pulls the live NSE listed-equity CSV + BSE active-equity list each run.
2. Bulk-downloads 1 year of daily OHLCV via `yfinance`.
3. Applies mandatory gates:
   - **Above the 200-day SMA**
   - **20-day avg daily turnover ≥ ₹2 cr** (real liquidity, no penny stocks)
4. Computes per stock:
   - 1M / 3M / 6M / 12M returns
   - % below 52-week high
   - Volume surge vs 50-day average
   - **Minervini Trend Template** (all 8 criteria)
   - Weighted momentum composite (40/30/20/10 on 12M/6M/3M/1M)
5. Ranks survivors by RS percentile against the qualifying universe.

## Running locally

```powershell
py -3 -m pip install -r scanner/requirements.txt
py -3 scanner/scan.py
```

Outputs to `scanner/data/`:
- `scanner_output.json` — dashboard feed
- `scanner_output.csv` — spreadsheet-ready
- `universe.csv` — what was scanned

Open `scanner/web/index.html` in any browser.

## Auto-refresh

`.github/workflows/scan.yml` runs every weekday 30 minutes after NSE close (16:30 IST / 11:00 UTC), commits fresh results, and re-deploys the dashboard to GitHub Pages.

## Filter logic

| Filter | Default | Why |
|---|---|---|
| Close > SMA200 | mandatory | Long-term Stage-2 trend |
| Turnover ≥ ₹2 cr | mandatory | Tradeable size, no junk |
| Within 25% of 52w high | UI toggle | Minervini criterion #7 |
| Trend Template (all 8) | UI toggle | Strictest setup |
| Min RS rating | UI slider | Relative momentum gate |

## Disclaimer

Educational tool. Not investment advice. Yahoo Finance data can have errors and lag.
