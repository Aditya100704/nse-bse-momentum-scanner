# Phenom Trade Worker

The always-on "brain" behind the dashboard's **Trade** tab. The website is a
static control panel; this little service is what actually **holds your trades
and watches the market** so you don't need the page open.

**Paper by default. It never places a real broker order on its own.**

---

## What it does

- Stores your trades (a JSON file under `DATA_DIR`).
- Sizes each trade by risk, exactly like the dashboard:
  `entry = level × (1 + buffer%)`, stop = your stop or **4%** if blank,
  `qty = floor(capital × risk% ÷ risk-per-share)`, target at your **R:R** (default 1:3).
- Every minute during market hours, pulls a quote per open trade and moves it
  `WATCHING → TRIGGERED → CLOSED (target/stop)`, pinging your phone (ntfy) on each step.

## Run it locally (to try before deploying)

```bash
cd worker
pip install -r requirements.txt
# off-hours testing: pretend the market is open
FORCE_MARKET_OPEN=1 python main.py
# -> http://localhost:8000/health
```

Then on the dashboard Trade tab → **Connect worker** → paste `http://localhost:8000` → Save.
(Only works on the same machine; for always-on, deploy to Railway below.)

## Deploy to Railway (≈3 minutes, one-time)

1. Push this repo to GitHub (the `worker/` folder is what Railway needs).
2. On [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo** → pick the repo.
3. Set the service **Root Directory** to `worker` (Settings → Source).
4. Railway auto-detects the `Dockerfile` and builds. When it's live, open
   **Settings → Networking → Generate Domain** to get a public URL like
   `https://phenom-worker.up.railway.app`.
5. (Recommended) Add a **Volume** mounted at `/app/data` so trades survive
   redeploys. Set env `DATA_DIR=/app/data`.
6. Paste that URL into the dashboard's Trade tab → **Connect worker** → Save → Test connection.

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `PORT` | `8000` | set by Railway automatically |
| `DATA_DIR` | `./data` | where trades persist (point at a Railway volume) |
| `NTFY_TOPIC` | `aditya-tradingsystem-fumoney` | phone alerts |
| `POLL_SECONDS` | `60` | how often to check quotes |
| `CORS_ORIGINS` | `*` | restrict to your Pages URL if you want |
| `FORCE_MARKET_OPEN` | `0` | `1` = check quotes even off-hours (testing) |
| `BROKER` | `paper` | `upstox` selects the (future) real adapter |
| `ALLOW_LIVE` | `0` | must be `1` **and** a valid token for any real order |

## Going live with Upstox (later)

The API is free and supports order placement, but note:

- **Daily login:** Upstox access tokens expire at **3:30 AM IST** and there's no
  refresh token — you approve a login once each morning.
- **Static IP:** SEBI's April-2026 rules require API orders from a **registered
  static IP**. Enable Railway **Static Outbound IPs** (Pro plan) and register
  that IP in Upstox *My Apps*.
- **No SEBI algo registration** needed for you — that only applies above 10
  orders/second; you place a handful by hand.

`broker.py` has the `UpstoxBroker` stub. Real order placement is intentionally
left unimplemented and gated behind `BROKER=upstox` + `ALLOW_LIVE=1` so live
trading is always a deliberate, reviewed step — never an accident.

## Endpoints

| Method | Path | Body |
|--------|------|------|
| GET | `/health` | — |
| GET | `/trades` | — |
| POST | `/trades` | `{ticker,exchange,direction,level,buffer,sl,capital,risk_pct,rr,note}` |
| POST | `/trades/{id}/trigger` | — |
| POST | `/trades/{id}/close` | `{exit}` |
| DELETE | `/trades/{id}` | — |
| DELETE | `/trades/closed` | — |
