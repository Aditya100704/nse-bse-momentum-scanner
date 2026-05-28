"""
Broker adapters for the Phenom Trade Worker.

Two layers:
  - get_quote(symbol, exchange) -> last price (used by the monitor loop)
  - place_entry / place_exit     -> where a REAL order would go

Default = PaperBroker: simulates everything, places no real orders, ever.

UpstoxBroker is a stub for the future. Even when wired up, real order placement
stays behind an explicit env switch (BROKER=upstox AND ALLOW_LIVE=1) plus a
valid daily access token. Per SEBI's April-2026 API rules you also need a
registered static IP and a single API app; see worker/README.md. Until all of
that is set up the worker silently stays in paper mode — it never guesses.
"""
from __future__ import annotations

import os
from typing import Optional

import yfinance as yf

_QUOTE_CACHE: dict[str, tuple[float, float]] = {}  # ticker -> (price, ts)
_QUOTE_TTL = 20.0


def _yf_ticker(symbol: str, exchange: str) -> str:
    return f"{symbol}.NS" if (exchange or "NSE").upper() == "NSE" else f"{symbol}.BO"


def _quote_yf(symbol: str, exchange: str) -> Optional[float]:
    import time
    key = _yf_ticker(symbol, exchange)
    now = time.time()
    cached = _QUOTE_CACHE.get(key)
    if cached and now - cached[1] < _QUOTE_TTL:
        return cached[0]
    tk = yf.Ticker(key)
    price = None
    # fast_info is cheap; fall back to a tiny intraday history pull
    try:
        fi = getattr(tk, "fast_info", None)
        if fi:
            price = fi.get("last_price") or fi.get("lastPrice")
    except Exception:
        price = None
    if price is None:
        try:
            df = tk.history(period="1d", interval="5m").dropna(subset=["Close"])
            if len(df):
                price = float(df["Close"].iloc[-1])
        except Exception:
            price = None
    if price is not None:
        _QUOTE_CACHE[key] = (float(price), now)
    return float(price) if price is not None else None


class PaperBroker:
    mode = "paper"

    def get_quote(self, symbol: str, exchange: str) -> Optional[float]:
        return _quote_yf(symbol, exchange)

    def place_entry(self, trade: dict) -> None:
        print(f"[paper] ENTER {trade['ticker']} {trade['direction']} "
              f"qty={trade['qty']} @ {trade['entry']}", flush=True)

    def place_exit(self, trade: dict, exit_price: float, result: str) -> None:
        print(f"[paper] EXIT {trade['ticker']} @ {exit_price} ({result})", flush=True)


class UpstoxBroker(PaperBroker):
    """Future real-money adapter. Inherits paper quote/exit until implemented.

    To go live you would:
      1. Create ONE Upstox API app (key + secret), set redirect URI.
      2. Do the daily OAuth login (token dies 3:30 AM IST — no refresh token).
      3. Register this worker's static IP in Upstox 'My Apps' (SEBI 2026).
      4. Set BROKER=upstox, ALLOW_LIVE=1, UPSTOX_ACCESS_TOKEN=...
    Then implement place_entry/place_exit to call POST /v2/order/place.
    """
    mode = "upstox-paper"  # stays paper until ALLOW_LIVE flips and methods are filled in

    def __init__(self) -> None:
        self.allow_live = os.getenv("ALLOW_LIVE", "0") == "1"
        self.token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if self.allow_live and self.token:
            self.mode = "upstox-live"

    def place_entry(self, trade: dict) -> None:
        if self.mode != "upstox-live":
            return super().place_entry(trade)
        # TODO: real POST https://api-hft.upstox.com/v2/order/place (Bearer token)
        # Intentionally not implemented — left as the explicit, reviewed step.
        raise NotImplementedError("Upstox live order placement not implemented yet.")

    def place_exit(self, trade: dict, exit_price: float, result: str) -> None:
        if self.mode != "upstox-live":
            return super().place_exit(trade, exit_price, result)
        raise NotImplementedError("Upstox live order placement not implemented yet.")


def get_broker():
    which = os.getenv("BROKER", "paper").lower()
    if which == "upstox":
        return UpstoxBroker()
    return PaperBroker()
