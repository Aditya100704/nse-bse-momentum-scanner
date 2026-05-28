"""
Phenom Trade Worker — the "brain" behind the dashboard's Trade tab.

The website (GitHub Pages) is a static control panel. This service is what
actually *holds* your trades and watches the market for you, always-on, so you
don't need the page open. Deploy it on Railway, paste its URL into the Trade
tab's "Connect worker" box, and the page starts talking to it.

What it does
------------
- Stores trades (JSON file under DATA_DIR; point a Railway volume there to keep
  them across redeploys).
- Sizes every trade by risk exactly like the dashboard does:
      entry  = level * (1 + dir * buffer%)
      stop   = your stop, or 4% from entry if you left it blank
      qty    = floor( (capital * risk%) / risk-per-share )
      target = entry + dir * risk-per-share * RR   (default 1:3)
- Runs a background loop that, during market hours, pulls a quote per open
  trade and advances the state machine:
      WATCHING  -> price breaks the level  -> TRIGGERED  (alerts your phone)
      TRIGGERED -> hits target  -> CLOSED (TARGET)        (alerts)
                -> hits stop    -> CLOSED (STOP)           (alerts)
- Phone alerts via ntfy (same topic as the rest of Phenom).

SAFETY
------
This worker is PAPER by default — it simulates fills, it does NOT place real
broker orders. A real broker (Upstox) can be wired in later via broker.py, and
even then live order placement stays behind an explicit env flag + confirmation.
Nothing here ever moves real money on its own.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from broker import get_broker

# ----------------------------------------------------------------- config
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TRADES_FILE = DATA_DIR / "trades.json"

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "aditya-tradingsystem-fumoney")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

DEFAULT_SL_PCT = 4.0
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
FORCE_MARKET_OPEN = os.getenv("FORCE_MARKET_OPEN", "0") == "1"  # for testing off-hours
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

IST = timezone(timedelta(hours=5, minutes=30))
MKT_OPEN = dtime(9, 15)
MKT_CLOSE = dtime(15, 30)

broker = get_broker()  # PaperBroker unless explicitly configured otherwise

_lock = threading.Lock()


# ----------------------------------------------------------------- helpers
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def market_open_now() -> bool:
    if FORCE_MARKET_OPEN:
        return True
    n = datetime.now(IST)
    if n.weekday() >= 5:
        return False
    return MKT_OPEN <= n.time() <= MKT_CLOSE


def ntfy(title: str, message: str, priority: str = "high", tags: str = "") -> None:
    try:
        headers = {"Title": title, "Priority": priority}
        if tags:
            headers["Tags"] = tags
        requests.post(NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception as e:
        print(f"[ntfy] failed: {e}", flush=True)


def round2(v) -> float:
    return round(float(v) + 0.0, 2)


def load_trades() -> dict:
    try:
        d = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
        return {"open": d.get("open", []), "closed": d.get("closed", [])}
    except Exception:
        return {"open": [], "closed": []}


def save_trades(d: dict) -> None:
    TRADES_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def uid() -> str:
    return f"{int(time.time()*1000):x}{os.urandom(2).hex()}"


# ----------------------------------------------------------------- trade math
def compute_trade(p: "NewTrade") -> dict:
    """Mirror of the dashboard's computeTrade so LOCAL and WORKER agree."""
    direction = (p.direction or "long").lower()
    d = -1 if direction == "short" else 1
    level = float(p.level)
    buffer = 1.0 if p.buffer is None else float(p.buffer)
    entry = level * (1 + d * buffer / 100.0)

    if p.sl is not None and float(p.sl) > 0:
        stop = float(p.sl)
    else:
        stop = entry * (1 - d * DEFAULT_SL_PCT / 100.0)

    risk_per_share = abs(entry - stop)
    capital = float(p.capital) if p.capital and p.capital > 0 else 0.0
    risk_pct = 1.0 if p.risk_pct is None else float(p.risk_pct)
    risk_rs = capital * risk_pct / 100.0
    qty = int(risk_rs // risk_per_share) if risk_per_share > 0 else 0
    rr = 3.0 if p.rr is None else float(p.rr)
    target = entry + d * risk_per_share * rr

    return {
        "direction": direction,
        "entry": round2(entry),
        "stop": round2(stop),
        "target": round2(target),
        "riskPerShare": round2(risk_per_share),
        "riskRs": round(risk_rs),
        "qty": qty,
        "rr": rr,
    }


def finalize_close(t: dict, exit_price: float, result: Optional[str] = None) -> dict:
    sign = -1 if t["direction"] == "short" else 1
    pnl_rs = (exit_price - t["entry"]) * sign * t["qty"]
    pnl_pct = ((exit_price - t["entry"]) * sign / t["entry"] * 100.0) if t["entry"] else 0.0
    r_mult = (((exit_price - t["entry"]) * sign) / t["riskPerShare"]) if t.get("riskPerShare") else 0.0
    if result is None:
        if sign == 1:
            result = "TARGET" if exit_price >= t["target"] else ("STOP" if exit_price <= t["stop"] else "MANUAL")
        else:
            result = "TARGET" if exit_price <= t["target"] else ("STOP" if exit_price >= t["stop"] else "MANUAL")
    closed = dict(t)
    closed.update({
        "exit": round2(exit_price),
        "pnlRs": round(pnl_rs),
        "pnlPct": round2(pnl_pct),
        "rMultiple": round2(r_mult),
        "result": result,
        "closedAt": now_iso(),
    })
    return closed


# ----------------------------------------------------------------- monitor loop
def monitor_tick() -> None:
    with _lock:
        d = load_trades()
        open_trades = d["open"]
    if not open_trades:
        return
    if not market_open_now():
        return

    changed = False
    for t in list(open_trades):
        try:
            mark = broker.get_quote(t["ticker"], t["exchange"])
        except Exception as e:
            print(f"[quote] {t['ticker']}: {e}", flush=True)
            continue
        if mark is None:
            continue
        t["mark"] = round2(mark)
        sign = -1 if t["direction"] == "short" else 1

        if t["state"] == "WATCHING":
            buf = t["level"] * 0.0015  # small "strong break" buffer (0.15%)
            broke = (sign == 1 and mark >= t["level"] + buf) or (sign == -1 and mark <= t["level"] - buf)
            if broke:
                t["state"] = "TRIGGERED"
                t["triggeredAt"] = now_iso()
                broker.place_entry(t)  # paper: no-op log; live: real order (gated)
                ntfy(
                    f"ENTER {'LONG' if sign == 1 else 'SHORT'} {t['ticker']} @ {t['entry']:.2f}",
                    f"{t['ticker']} broke {t['level']:.2f}. Entry {t['entry']:.2f} | "
                    f"SL {t['stop']:.2f} | Target {t['target']:.2f} ({t['rr']:.0f}R) | "
                    f"Qty {t['qty']} | Risk ₹{t['riskRs']}.",
                    tags="rocket",
                )
                changed = True

        elif t["state"] == "TRIGGERED":
            hit_target = (sign == 1 and mark >= t["target"]) or (sign == -1 and mark <= t["target"])
            hit_stop = (sign == 1 and mark <= t["stop"]) or (sign == -1 and mark >= t["stop"])
            if hit_target or hit_stop:
                exit_price = t["target"] if hit_target else t["stop"]
                result = "TARGET" if hit_target else "STOP"
                broker.place_exit(t, exit_price, result)
                closed = finalize_close(t, exit_price, result)
                with _lock:
                    cur = load_trades()
                    cur["open"] = [x for x in cur["open"] if x["id"] != t["id"]]
                    cur["closed"].insert(0, closed)
                    save_trades(cur)
                emoji = "white_check_mark" if hit_target else "x"
                ntfy(
                    f"{'TARGET HIT' if hit_target else 'STOPPED OUT'} {t['ticker']} @ {exit_price:.2f}",
                    f"{t['ticker']} closed {result}. P&L ₹{closed['pnlRs']} ({closed['rMultiple']}R).",
                    priority="high" if hit_target else "default", tags=emoji,
                )
                changed = True
                continue

    if changed or open_trades:
        with _lock:
            cur = load_trades()
            # write back marks/states for trades still open
            by_id = {t["id"]: t for t in open_trades}
            cur["open"] = [by_id.get(x["id"], x) for x in cur["open"]]
            save_trades(cur)


def monitor_loop() -> None:
    print(f"[worker] monitor loop up. poll={POLL_SECONDS}s broker={broker.mode}", flush=True)
    while True:
        try:
            monitor_tick()
        except Exception as e:
            print(f"[monitor] error: {e}", flush=True)
        time.sleep(POLL_SECONDS)


# ----------------------------------------------------------------- API
class NewTrade(BaseModel):
    ticker: str
    exchange: str = "NSE"
    direction: str = "long"
    level: float
    buffer: Optional[float] = 1.0
    sl: Optional[float] = None
    capital: Optional[float] = 100000
    risk_pct: Optional[float] = 1.0
    rr: Optional[float] = 3.0
    note: Optional[str] = ""


class CloseBody(BaseModel):
    exit: float


app = FastAPI(title="Phenom Trade Worker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    threading.Thread(target=monitor_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok", "broker_mode": broker.mode, "market_open": market_open_now()}


@app.get("/trades")
def get_trades():
    d = load_trades()
    return {"open": d["open"], "closed": d["closed"], "broker_mode": broker.mode}


@app.post("/trades")
def add_trade(p: NewTrade):
    c = compute_trade(p)
    t = {
        "id": uid(),
        "ticker": p.ticker.upper(),
        "exchange": p.exchange,
        "level": float(p.level),
        "buffer": float(p.buffer or 1.0),
        "note": p.note or "",
        "state": "WATCHING",
        "mark": None,
        "added": now_iso(),
        "triggeredAt": None,
        **c,
    }
    with _lock:
        d = load_trades()
        d["open"].insert(0, t)
        save_trades(d)
    return t


@app.post("/trades/{tid}/trigger")
def trigger(tid: str):
    with _lock:
        d = load_trades()
        t = next((x for x in d["open"] if x["id"] == tid), None)
        if not t:
            raise HTTPException(404, "not found")
        t["state"] = "TRIGGERED"
        t["triggeredAt"] = now_iso()
        if t.get("mark") is None:
            t["mark"] = t["entry"]
        save_trades(d)
    return t


@app.post("/trades/{tid}/close")
def close(tid: str, body: CloseBody):
    with _lock:
        d = load_trades()
        t = next((x for x in d["open"] if x["id"] == tid), None)
        if not t:
            raise HTTPException(404, "not found")
        if t["state"] == "WATCHING":
            t["state"] = "TRIGGERED"
            t["triggeredAt"] = now_iso()
        closed = finalize_close(t, float(body.exit))
        d["open"] = [x for x in d["open"] if x["id"] != tid]
        d["closed"].insert(0, closed)
        save_trades(d)
    return closed


# NOTE: this MUST be declared before /trades/{tid} or FastAPI matches "closed"
# as a {tid} and this route never fires.
@app.delete("/trades/closed")
def clear_closed():
    with _lock:
        d = load_trades()
        d["closed"] = []
        save_trades(d)
    return {"ok": True}


@app.delete("/trades/{tid}")
def delete(tid: str):
    with _lock:
        d = load_trades()
        d["open"] = [x for x in d["open"] if x["id"] != tid]
        save_trades(d)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
