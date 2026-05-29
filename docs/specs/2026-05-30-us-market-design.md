# US Market + Market Toggle — Design (2026-05-30)

## Goal
Run the exact same Phenom product (full‑universe momentum scanner + dark dashboard:
Scanner / Breadth / Sectors / Trade / Journal / News) for the **US market**, on the **same
website**, with an **India ⇄ US toggle**. Same UI, two datasets. Capture the **complete US
common‑stock universe**. E2E‑tested.

## Data sourcing (the hard part = the US analog of bhavcopy)
- **Universe**: Nasdaq Trader official symbol directory (free, no key) —
  `nasdaqlisted.txt` (NASDAQ) + `otherlisted.txt` (NYSE/AMEX/ARCA). Filter to common stock:
  drop ETF flag, Test‑Issue flag, and names containing warrant/unit/right/preferred/
  depositary/notes/debenture/ETN. Class dots → dashes for yfinance (BRK.B→BRK-B). ≈ **5,581**
  common stocks (NASDAQ 3,225 / NYSE 2,087 / AMEX 267).
- **EOD OHLCV**: **yfinance** batch download (chunked 160, retried). Stooq — the first‑choice
  bhavcopy analog — went **API‑key‑gated in 2026** (per‑symbol returns "Get your apikey",
  bulk zip 401), and we don't create accounts/keys on the user's behalf. yfinance is the only
  complete **keyless** source and returns **~99%** of established US names in ~8–10 min (the
  India rate‑limit pain was ~8k symbols WITH repeated runs; ~5.5k US in one chunked pass is clean).
- **Fundamentals**: TradingView public scanner, **`america`** region (same mechanism as India's
  `india` region). **News**: Google News RSS, US locale (`hl=en-US&gl=US`).

## Liquidity floor — grounded in Minervini + Qullamaggie (per user direction)
- Minervini: ≥ ~400k–1M shares/day, price typically > $20–30, avoids sub‑$10 "deteriorating
  former leaders." Qullamaggie: liquid leaders; **episodic pivots dollar‑volume > $100M**.
- **US qualifier gate** = price ≥ **$10** AND 20‑day avg dollar‑volume ≥ **$20M**.
  **US episodic pivot** = gap ≥ **10%** + 3× vol + dollar‑volume ≥ **$100M**.
  (India unchanged: ₹10 cr/day, gap ≥ 5%.) Full universe still scanned for breadth/regime.

## Architecture (market‑agnostic core, thin per‑market edges)
- `usdata.py` (new): `build_us_universe()` + `fetch_us_history()` return the **same shape** as
  `bhavcopy.py` so `scan.py`'s `analyze()` is unchanged.
- `scan.py`: a `SCAN_MARKET` env (`in`|`us`) sets the universe + price source, the turnover
  divisor (1e7 ₹cr vs 1e6 $M), the liquidity/min‑price/EP thresholds, and the output filename.
  US → `scanner_output_us.json`; India output byte‑unchanged.
- `build_tickers.py` / `fundamentals_fetch.py` / `news_fetch.py`: same `SCAN_MARKET` switch →
  `tickers_us.json` / `fundamentals_us.json` / `news_us.json`.
- **Dashboard**: `MARKET` read from `localStorage("phenom_market")` at load (default last‑used,
  first visit = India). A navbar **IN/US** segmented toggle persists the choice and **reloads**
  — so every fetch + render re‑runs cleanly for the chosen market (no fragile live re‑render).
  All data paths, number locale (`en-IN`/`en-US`), currency (₹/$), liquidity unit (cr/$M), and
  TradingView symbol prefixes (NSE:/BSE: vs NASDAQ:/NYSE:/AMEX:) are market‑aware. Trade/News/
  Journal JS read the same localStorage key (robust to script load order).
- **CI**: `scan.yml` runs India then US (build_tickers + fundamentals for each); `news.yml` /
  `fundamentals.yml` run both markets; deploy copies all `*_us.json` and rewrites the `../data/`
  prefix (covers the market‑aware template paths) — India + US ship together.

## Non‑goals / deferred (v1)
- Trade‑tab currency cosmetics (₹ labels, NSE/BSE exchange select) stay India‑styled; the US
  Trade autocomplete IS market‑aware (uses `tickers_us.json`). Full Trade‑tab $ localization = polish.
- US VCP engine port (research/local; rides the same parametrization later).
- CI yfinance throttling risk on a datacenter IP (local is clean; non‑fatal + retried in CI).

## E2E test
Serve `web/` locally, load in Chrome, toggle to US: verify Scanner (all subtabs) / Breadth /
Sectors / News render with US data, symbol links resolve to US exchanges, no console errors;
toggle back to India and confirm it's intact.
