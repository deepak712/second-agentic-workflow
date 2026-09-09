#!/usr/bin/env python3
"""Fetch crypto market state from CoinGecko: global totals, top-N coins, BTC/ETH history.

Free (no API key). Selects the "deep dive" set the report focuses on:
BTC + ETH always, plus the biggest 24h gainers/losers and 7d movers, with
stablecoins and wrapped/staked derivatives excluded so they can't crowd it out.

    python tools/fetch_market.py
    python tools/fetch_market.py --top 50 --movers 5
"""
from __future__ import annotations

import argparse
import sys
import time

import _lib  # noqa: E402  (import before requests: silences the LibreSSL warning)
import requests

API = "https://api.coingecko.com/api/v3"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Excluded from mover selection: these track another asset, so their moves are
# not independent signal. They still appear in the full top-N table.
PEGGED = {
    "usdt", "usdc", "dai", "usde", "fdusd", "busd", "tusd", "pyusd", "usds",
    "usd1", "usdd", "frax", "lusd", "gusd", "eurc", "usdtb", "rlusd", "buidl",
    "wbtc", "weth", "steth", "wsteth", "weeth", "cbbtc", "reth", "rseth",
    "solvbtc", "lbtc", "meth", "ezeth", "wbeth", "bsc-usd", "jitosol", "msol",
}


def get(path: str, params: dict | None = None, attempts: int = 4):
    """GET with backoff on 429/5xx. CoinGecko's free tier rate-limits hard."""
    last = None
    for i in range(attempts):
        try:
            r = requests.get(f"{API}{path}", params=params or {},
                             headers={"User-Agent": UA, "Accept": "application/json"},
                             timeout=25)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:
            last = repr(exc)
        if i < attempts - 1:
            delay = 3 * (2 ** i)
            print(f"  retry {i + 1}/{attempts - 1} in {delay}s ({last}) for {path}",
                  file=sys.stderr)
            time.sleep(delay)
    print(f"  giving up on {path}: {last}", file=sys.stderr)
    return None


def pct(coin: dict, key: str):
    v = coin.get(key)
    return round(v, 2) if isinstance(v, (int, float)) else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=50, help="how many coins by market cap (default 50)")
    ap.add_argument("--movers", type=int, default=5, help="gainers and losers to deep-dive (default 5)")
    ap.add_argument("--date", default=None, help="override the run date (YYYY-MM-DD)")
    args = ap.parse_args()

    errors: list[str] = []

    glob = get("/global")
    if glob is None:
        errors.append("global: request failed")
        glob = {}
    g = glob.get("data", {})
    dominance = g.get("market_cap_percentage", {}) or {}
    global_out = {
        "total_market_cap_usd": (g.get("total_market_cap") or {}).get("usd"),
        "total_volume_24h_usd": (g.get("total_volume") or {}).get("usd"),
        "market_cap_change_24h_pct": g.get("market_cap_change_percentage_24h_usd"),
        "btc_dominance_pct": dominance.get("btc"),
        "eth_dominance_pct": dominance.get("eth"),
        "active_cryptocurrencies": g.get("active_cryptocurrencies"),
    }

    markets = get("/coins/markets", {
        "vs_currency": "usd", "order": "market_cap_desc", "per_page": args.top, "page": 1,
        "sparkline": "false", "price_change_percentage": "1h,24h,7d,30d",
    })
    if not markets:
        errors.append("markets: request failed")
        markets = []

    coins = []
    for c in markets:
        coins.append({
            "id": c.get("id"), "symbol": (c.get("symbol") or "").lower(),
            "name": c.get("name"), "rank": c.get("market_cap_rank"),
            "price_usd": c.get("current_price"), "market_cap_usd": c.get("market_cap"),
            "volume_24h_usd": c.get("total_volume"),
            "chg_1h": pct(c, "price_change_percentage_1h_in_currency"),
            "chg_24h": pct(c, "price_change_percentage_24h_in_currency"),
            "chg_7d": pct(c, "price_change_percentage_7d_in_currency"),
            "chg_30d": pct(c, "price_change_percentage_30d_in_currency"),
            "ath_usd": c.get("ath"),
            "pct_below_ath": (round(c["ath_change_percentage"], 2)
                              if isinstance(c.get("ath_change_percentage"), (int, float)) else None),
        })

    # Mover selection: independent movers only, and only coins with real 24h data.
    pool = [c for c in coins if c["symbol"] not in PEGGED and c["chg_24h"] is not None]
    by_24h = sorted(pool, key=lambda c: c["chg_24h"], reverse=True)
    gainers = by_24h[:args.movers]
    losers = by_24h[-args.movers:][::-1]
    by_7d = sorted([c for c in pool if c["chg_7d"] is not None],
                   key=lambda c: c["chg_7d"], reverse=True)[:3]

    deep = []
    seen = set()
    for c in [c for c in coins if c["symbol"] in ("btc", "eth")] + gainers + losers + by_7d:
        if c["id"] not in seen:
            seen.add(c["id"])
            deep.append(c["id"])

    history = {}
    for cid in ("bitcoin", "ethereum"):
        chart = get(f"/coins/{cid}/market_chart",
                    {"vs_currency": "usd", "days": 30, "interval": "daily"})
        if not chart or not chart.get("prices"):
            errors.append(f"history/{cid}: request failed")
            continue
        history[cid] = [{"t": int(p[0] / 1000), "price": round(p[1], 2)}
                        for p in chart["prices"]]

    out = {
        "date": args.date or _lib.today(),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "coingecko",
        "global": global_out,
        "coins": coins,
        "gainers": [c["id"] for c in gainers],
        "losers": [c["id"] for c in losers],
        "deep_dive": deep,
        "history": history,
        "errors": errors,
    }
    path = _lib.write_tmp(_lib.stage_path("market", args.date), out)
    print(f"market -> {path}  ({len(coins)} coins, {len(deep)} deep-dive, {len(errors)} errors)")
    if errors:
        print(f"  errors: {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
