#!/usr/bin/env python3
"""
Fetch previous trading day's performance for major market indices.
Uses Yahoo Finance public API — no API key required.

Output JSON:
{
  "date": "2026-04-05",
  "indices": [
    {"symbol": "^GSPC", "name": "S&P 500", "price": 5204.34,
     "change": 58.21, "change_pct": 1.13, "prev_close": 5146.13}
  ]
}

Usage:
    python3 fetch-market.py --output /tmp/td-market.json
    python3 fetch-market.py --symbols "^GSPC,^IXIC,^DJI" --output /tmp/td-market.json
"""

import argparse
import json
import logging
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

DEFAULT_SYMBOLS = [
    ("^GSPC",   "S&P 500"),
    ("^IXIC",   "NASDAQ"),
    ("^DJI",    "Dow Jones"),
    ("^FTSE",   "FTSE 100"),
    ("^N225",   "Nikkei 225"),
    ("BTC-USD", "Bitcoin"),
]

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def fetch_quote(symbol: str) -> dict | None:
    url = YAHOO_URL.format(symbol=urllib.request.quote(symbol))
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = data["chart"]["result"]
        if not result:
            return None
        r = result[0]
        meta = r["meta"]
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        # Filter out None values from closes
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None

        prev_close = closes[-2]
        last_close = closes[-1]
        change = last_close - prev_close
        change_pct = (change / prev_close) * 100

        return {
            "symbol": symbol,
            "price": round(last_close, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_close, 2),
            "currency": meta.get("currency", "USD"),
        }
    except Exception as e:
        logging.warning(f"  {symbol}: {e}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch market index data from Yahoo Finance")
    parser.add_argument("--output", "-o", type=Path, default=Path("/tmp/td-market.json"))
    parser.add_argument(
        "--symbols", type=str, default="",
        help="Comma-separated 'SYMBOL:Name' pairs to override defaults"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.symbols:
        pairs = []
        for part in args.symbols.split(","):
            part = part.strip()
            if ":" in part:
                sym, name = part.split(":", 1)
                pairs.append((sym.strip(), name.strip()))
            else:
                pairs.append((part, part))
    else:
        pairs = DEFAULT_SYMBOLS

    results = []
    for symbol, name in pairs:
        logging.debug(f"  Fetching {symbol} ({name})")
        quote = fetch_quote(symbol)
        if quote:
            quote["name"] = name
            results.append(quote)
            logging.debug(
                f"  {name}: {quote['price']} "
                f"{'▲' if quote['change'] >= 0 else '▼'} "
                f"{quote['change_pct']:+.2f}%"
            )
        else:
            logging.warning(f"  {name} ({symbol}): no data")
        time.sleep(0.2)

    output = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "fetched_at": datetime.now().isoformat(),
        "indices": results,
    }

    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logging.info(f"✅ Market data → {args.output} ({len(results)}/{len(pairs)} indices)")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
