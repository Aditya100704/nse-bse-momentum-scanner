"""
TradingView ticker validator for the copy-to-TradingView list.

Our symbols come from Zerodha/bhavcopy (India) and Nasdaq Trader (US); a handful
don't exist on TradingView (coverage gaps, renamed scrips, ETFs TV lists under a
different name). Pasting those into TV shows "This symbol doesn't exist". This
module asks TradingView's PUBLIC scanner which tickers actually resolve (and on
which exchange), then tags every stock with:
    tv_ok        -> bool (TV has this ticker)
    tv_exchange  -> the exchange that resolves (NSE/BSE or NASDAQ/NYSE/AMEX)
so the dashboard's "Copy for TradingView" can skip the dead ones.

Usage:
    annotate(payload, is_us)            # in-process (called from scan.py)
    py -3 tv_validate.py                # standalone: patch data/scanner_output.json
    SCAN_MARKET=us py -3 tv_validate.py # standalone: patch data/scanner_output_us.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parent / "data"
_H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
_BLOCKS = ("results", "ipos", "episodic_pivots")


def _order(r: dict, is_us: bool) -> list[str]:
    """Exchanges to try, in preference order, for a row's symbol."""
    if is_us:
        ex = (r.get("exchange") or "NASDAQ").upper()
        return [ex if ex in ("NASDAQ", "NYSE", "AMEX") else "NASDAQ"]
    # India: try the row's own exchange first, then the other.
    return ["NSE", "BSE"] if (r.get("exchange") == "NSE") else ["BSE", "NSE"]


def _resolved_set(rows: list[dict], is_us: bool) -> set[str]:
    """Ask the TV scanner which EX:SYM tickers it knows. Returns the resolved set."""
    url = "https://scanner.tradingview.com/america/scan" if is_us else "https://scanner.tradingview.com/india/scan"
    cands: set[str] = set()
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if not s:
            continue
        for ex in _order(r, is_us):
            cands.add(f"{ex}:{s}")
    tickers = sorted(cands)
    resolved: set[str] = set()
    for i in range(0, len(tickers), 350):
        body = {"symbols": {"tickers": tickers[i:i + 350], "query": {"types": []}}, "columns": ["close"]}
        try:
            resp = requests.post(url, json=body, headers=_H, timeout=30)
            resp.raise_for_status()
            for row in resp.json().get("data", []):
                resolved.add(row["s"])
        except Exception as exc:
            print(f"[tv_validate] batch {i} failed (non-fatal): {exc}", flush=True)
    return resolved


def annotate(payload: dict, is_us: bool) -> int:
    """Tag every results/ipos/episodic_pivots row with tv_ok + tv_exchange.
    Returns the count of TV-unresolvable rows. Non-fatal: if the TV probe returns
    nothing (outage), leaves rows untagged (tv_ok stays absent -> copy keeps them)."""
    rows = [r for b in _BLOCKS for r in payload.get(b, [])]
    if not rows:
        return 0
    resolved = _resolved_set(rows, is_us)
    if not resolved:
        print("[tv_validate] TV probe empty — skipping (rows left untagged)", flush=True)
        return 0
    bad = 0
    for b in _BLOCKS:
        for r in payload.get(b, []):
            s = (r.get("symbol") or "").upper()
            tv = next((f"{ex}:{s}" for ex in _order(r, is_us) if f"{ex}:{s}" in resolved), None)
            r["tv_ok"] = tv is not None
            if tv:
                r["tv_exchange"] = tv.split(":", 1)[0]
            else:
                bad += 1
    print(f"[tv_validate] tagged {len(rows)} rows · {bad} not on TradingView", flush=True)
    return bad


def main():
    is_us = os.getenv("SCAN_MARKET", "in").strip().lower() == "us"
    path = DATA / ("scanner_output_us.json" if is_us else "scanner_output.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    annotate(payload, is_us)
    path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    print(f"[tv_validate] patched {path}", flush=True)


if __name__ == "__main__":
    main()
