"""
Phenom news fetcher — runs in GitHub Actions (daily cron + manual dispatch).

For every current qualifier + IPO stock, pull the latest headlines from Google
News RSS (free, no API key, server-side so there's no browser CORS problem) and
write them to data/news.json. The dashboard's News search bar reads that file —
instant, static, refreshed daily (or on demand when you run the workflow).

"Catalysts" here = the latest news headlines (results, orders, deals, upgrades,
block deals) — that's what moves a stock. No paid data needed.

Run locally:  py -3 news_fetch.py            (all qualifiers + IPOs)
              py -3 news_fetch.py 40          (cap to 40 stocks, for testing)
"""
from __future__ import annotations

import sys, json, html, re
import concurrent.futures as cf
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "news.json"
MAX_HEADLINES = 6
WORKERS = 8

UA = {"User-Agent": "Mozilla/5.0 (compatible; PhenomNews/1.0)"}
S = requests.Session(); S.headers.update(UA)
RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def clean_name(name: str) -> str:
    n = re.sub(r"\b(Limited|Ltd|Ltd\.|Industries|Corporation|Company|Co\.?)\b", "", name or "", flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def fetch_one(sym: str, name: str) -> tuple[str, list]:
    query = f'"{clean_name(name) or sym}" (stock OR share OR results OR order OR NSE) when:14d'
    url = RSS.format(q=quote_plus(query))
    try:
        r = S.get(url, timeout=15)
        if r.status_code != 200:
            return sym, []
        root = ET.fromstring(r.content)
        items = []
        for it in root.iter("item"):
            title = it.findtext("title") or ""
            link = it.findtext("link") or ""
            pub = it.findtext("pubDate") or ""
            src_el = it.find("source")
            source = (src_el.text if src_el is not None else "") or ""
            # title often "Headline - Source"; split the trailing source
            t = html.unescape(title).strip()
            if not source and " - " in t:
                t, source = t.rsplit(" - ", 1)
            items.append({"title": t.strip(), "link": link.strip(),
                          "source": html.unescape(source).strip(), "date": pub.strip()})
            if len(items) >= MAX_HEADLINES:
                break
        return sym, items
    except Exception:
        return sym, []


def main():
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        scan = json.loads((DATA / "scanner_output.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[news] cannot read scanner_output.json: {e}"); sys.exit(1)

    seen, targets = set(), []
    for r in (scan.get("results", []) + scan.get("ipos", [])):
        sym = (r.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        targets.append((sym, r.get("name") or sym, r.get("exchange") or "NSE"))
    if cap:
        targets = targets[:cap]
    print(f"[news] fetching headlines for {len(targets)} stocks…", flush=True)

    stocks = {}
    names = {}
    done = 0
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_one, s, n): (s, n, e) for s, n, e in targets}
        for fut in cf.as_completed(futs):
            s, n, e = futs[fut]
            _, items = fut.result()
            if items:
                stocks[s] = items
            names[s] = {"name": n, "exchange": e}
            done += 1
            if done % 100 == 0:
                print(f"[news] {done}/{len(targets)}", flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(stocks),
        "names": names,
        "stocks": stocks,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[news] wrote {OUT} — {len(stocks)} stocks with headlines / {len(targets)} attempted", flush=True)


if __name__ == "__main__":
    main()
