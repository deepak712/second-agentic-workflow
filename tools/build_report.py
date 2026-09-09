#!/usr/bin/env python3
"""Synthesize the daily crypto report: market + sentiment + triaged news -> PDF + CSV.

PAID: 1 Claude (Opus) call. Dry-run by default; --confirm to spend.
--reuse-narrative rebuilds the PDF from cache with no Opus call.

    python tools/build_report.py                    # estimate only
    python tools/build_report.py --confirm          # spend
    python tools/build_report.py --reuse-narrative  # free layout iteration
"""
from __future__ import annotations

import argparse
import csv
import json
import re

import _lib
import _llm
import _report

SYSTEM = """You are a senior crypto market analyst writing a daily research brief \
for one experienced retail investor. You are paid to be right, not to be exciting.

You will be given: market data (top coins, 24h/7d/30d moves, dominance), the Fear \
& Greed index with recent history, and the day's news headlines already classified \
by sentiment, impact and theme.

Use these exact ## headings, in this order:

## Market Snapshot
## Sentiment Read
## What Moved the Market - Positive
## What Moved the Market - Negative
## Coin-by-Coin: Buy or Wait
## Watch Levels & Invalidation
## Bottom Line

Section requirements:

- **Market Snapshot** - 3-5 bullets on total market cap, the 24h direction, BTC and
  ETH dominance and what the dominance trend implies about risk appetite. A table of
  the top 10 coins with their prices is already printed above this section, so do not
  reproduce it; reference those numbers in prose instead, and do call out by name any
  of the top 10 whose move is worth explaining.
- **Sentiment Read** - interpret the Fear & Greed number *against its own recent
  history*, which matters far more than the absolute level. Say plainly whether
  sentiment is stretched, recovering or deteriorating, and note when sentiment and
  price are diverging.
- **What Moved the Market - Positive / Negative** - 3-6 bullets each, drawn from the
  highest-impact classified headlines. Lead each bullet with the substance, and name
  the source in parentheses. Ignore routine price-recap headlines. If a side has
  genuinely little of substance, say so in one line rather than padding it.
- **Coin-by-Coin: Buy or Wait** - a markdown table, one row per coin in the deep-dive
  list you are given, with EXACTLY these columns:
  | Coin | Stance | Confidence | Why | Invalidation |
  Stance is exactly one of: Accumulate, Wait, Avoid. Confidence is High, Medium or Low.
  "Why" is one sentence tying the stance to specific evidence in the data. "Invalidation"
  names the concrete condition that would prove the stance wrong - a price level, a
  dominance shift, a pending event. Never write "N/A": if you cannot invalidate a view,
  the stance should be Wait. Be willing to say Wait for most rows; a day with no
  compelling entry is a normal and useful finding.
- **Watch Levels & Invalidation** - 3-5 bullets on the specific levels, dates or events
  worth watching over the next few days.
- **Bottom Line** - 3-4 sentences a reader could act on having read nothing else. State
  the single most important thing about today. This section is lifted verbatim into the
  email body, so it must stand alone without the rest of the report.

Hard rules:
- Ground every claim in the supplied data. Never invent a price, a level or a headline.
- A large move in a low-liquidity or newly listed token is noise until proven otherwise -
  say so rather than treating it as a signal.
- Distinguish what the data shows from what you infer. Hedge honestly; do not manufacture
  confidence you do not have.
- Never tell the reader to buy. You describe theses and their invalidation conditions.
- Aim for 900-1300 words. Real markdown bullet lists and one real markdown table.
- Markdown only, no code fences, and do not restate the report title."""


def fmt_usd(v) -> str:
    if not isinstance(v, (int, float)):
        return "n/a"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"${v / div:,.2f}{unit}"
    return f"${v:,.0f}"


def fmt_pct(v) -> str:
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "n/a"


def fmt_price(v) -> str:
    """Readable price across six orders of magnitude, without scientific notation."""
    if not isinstance(v, (int, float)):
        return "n/a"
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.4f}"
    return f"${v:.8f}".rstrip("0")


def build_user(market: dict, sent: dict, triage: dict) -> str:
    g = market.get("global", {})
    by = {c["id"]: c for c in market.get("coins", [])}
    deep = [by[i] for i in market.get("deep_dive", []) if i in by]

    parts = [f"DATE: {market.get('date')}", "", "=== GLOBAL MARKET ==="]
    parts.append(f"Total market cap: {fmt_usd(g.get('total_market_cap_usd'))} "
                 f"({fmt_pct(g.get('market_cap_change_24h_pct'))} 24h)")
    parts.append(f"24h volume: {fmt_usd(g.get('total_volume_24h_usd'))}")
    btc_d, eth_d = g.get("btc_dominance_pct"), g.get("eth_dominance_pct")
    if isinstance(btc_d, (int, float)) and isinstance(eth_d, (int, float)):
        parts.append(f"BTC dominance: {btc_d:.2f}%  ETH dominance: {eth_d:.2f}%")
    else:
        parts.append("Dominance: n/a")

    parts += ["", "=== FEAR & GREED ==="]
    cur = sent.get("current") or {}
    parts.append(f"Today: {cur.get('value')} ({cur.get('label')})")
    parts.append(f"1d change: {sent.get('delta_1d')}  7d change: {sent.get('delta_7d')}")
    parts.append(f"7d avg: {sent.get('avg_7d')}  30d avg: {sent.get('avg_30d')}  "
                 f"30d range: {sent.get('min_30d')}-{sent.get('max_30d')}")
    hist = [str(s["value"]) for s in (sent.get("series") or [])]
    parts.append(f"30d series (oldest->newest): {', '.join(hist)}")

    header = ("%-8s%14s%8s%9s%9s%9s%11s%9s"
              % ("sym", "price", "1h", "24h", "7d", "30d", "mcap", "vs ATH"))

    def row(c: dict) -> str:
        return "".join([
            "%-8s" % c["symbol"].upper(),
            "%14s" % fmt_price(c.get("price_usd")),
            "%8s" % fmt_pct(c["chg_1h"]),
            "%9s" % fmt_pct(c["chg_24h"]),
            "%9s" % fmt_pct(c["chg_7d"]),
            "%9s" % fmt_pct(c["chg_30d"]),
            "%11s" % fmt_usd(c["market_cap_usd"]),
            "%9s" % fmt_pct(c["pct_below_ath"]),
        ])

    parts += ["", "=== TOP COINS BY MARKET CAP ===", header]
    top = market.get("coins", [])[:30]
    parts += [row(c) for c in top]

    # Every deep-dive coin gets its own data row here, including ones ranked
    # below the top-30 cut above. Without this the model is asked for a stance
    # on a coin it has no price data for, and writes "no price supplied".
    parts += ["", "=== DEEP-DIVE SET (one table row required for each of these) ===",
              header]
    for c in deep:
        parts.append(row(c) + "   " + c["name"])

    btc_hist = (market.get("history") or {}).get("bitcoin") or []
    if btc_hist:
        parts += ["", "=== BTC 30d DAILY CLOSE ===",
                  ", ".join(f"{p['price']:,.0f}" for p in btc_hist)]
    eth_hist = (market.get("history") or {}).get("ethereum") or []
    if eth_hist:
        parts += ["=== ETH 30d DAILY CLOSE ===",
                  ", ".join(f"{p['price']:,.0f}" for p in eth_hist)]

    parts += ["", "=== NEWS TRIAGE SUMMARY ===",
              f"{triage.get('count')} headlines classified",
              f"Sentiment split: {json.dumps(triage.get('sentiment_counts', {}))}",
              f"Themes: {json.dumps(triage.get('theme_counts', {}))}",
              f"Impact-weighted net tone (-1 bearish .. +1 bullish): {triage.get('net_tone')}",
              f"Most-mentioned coins: {json.dumps(triage.get('coin_mentions', {}))}",
              f"Per-coin news tone: {json.dumps(triage.get('coin_tone', {}))}"]

    parts += ["", "=== HEADLINES (highest impact first) ==="]
    ordered = sorted(triage.get("items", []),
                     key=lambda c: (-c["impact"], c["sentiment"] == "neutral"))
    for c in ordered[:70]:
        coins = f" [{','.join(c['coins'])}]" if c["coins"] else ""
        parts.append(f"- ({c['sentiment']}/{c['impact']}/{c['theme']}){coins} "
                     f"{c['title']} ({c['source']})")

    return "\n".join(parts)


def parse_stance_table(narrative: str) -> list[dict]:
    """Pull the Coin-by-Coin markdown table out of the narrative for the CSV twin."""
    rows: list[dict] = []
    in_section = False
    for line in narrative.splitlines():
        if line.startswith("## "):
            in_section = "coin-by-coin" in line.lower()
            continue
        if not in_section or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        if re.match(r"^[-: ]+$", cells[0]) or cells[0].lower() == "coin":
            continue
        rows.append({"Coin": cells[0], "Stance": cells[1], "Confidence": cells[2],
                     "Why": cells[3], "Invalidation": cells[4]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="run date to report on (YYYY-MM-DD)")
    ap.add_argument("--confirm", action="store_true", help="actually spend credits")
    ap.add_argument("--reuse-narrative", action="store_true",
                    help="rebuild the PDF from the cached narrative, no API call")
    ap.add_argument("--max-tokens", type=int, default=9000,
                    help="output cap (Opus adaptive thinking counts against this)")
    args = ap.parse_args()
    date = args.date or _lib.today()

    try:
        market = _lib.read_tmp(_lib.stage_path("market", date))
        sent = _lib.read_tmp(_lib.stage_path("sentiment", date))
        triage = _lib.read_tmp(_lib.stage_path("triage", date))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing input: {exc}. Run the fetch + triage tools for {date} first.")

    user = build_user(market, sent, triage)
    narrative = _report.resolve_narrative(
        _report.cache_path(date), _llm.OPUS, SYSTEM, user,
        confirm=args.confirm, reuse=args.reuse_narrative,
        max_output_tokens=args.max_tokens,
    )
    if narrative is None:
        return

    charts = []
    try:
        index = _lib.read_tmp(f"charts/{date}/index.json")
        for key, caption in (("fear_greed", "Fear & Greed Index, last 30 days"),
                             ("movers", "Biggest 24h movers (stablecoins and wrapped assets excluded)"),
                             ("btc_30d", "Bitcoin, 30-day daily close")):
            if index.get(key):
                charts.append((index[key], caption))
    except FileNotFoundError:
        print("  (no charts found; rendering text-only)", flush=True)

    g = market.get("global", {})
    cur = (sent.get("current") or {})
    meta = (f"Generated {date} &middot; Total cap {_report.esc(fmt_usd(g.get('total_market_cap_usd')))} "
            f"({_report.esc(fmt_pct(g.get('market_cap_change_24h_pct')))} 24h) &middot; "
            f"Fear &amp; Greed {cur.get('value')} ({_report.esc(cur.get('label', 'n/a'))}) &middot; "
            f"{triage.get('count', 0)} headlines analysed")

    # The top-10 price table is rendered from the fetched data, not written by the
    # model, so it is always present and always matches exactly what was fetched.
    top10 = _report.market_table_html(market.get("coins", []), n=10,
                                      title="Top 10 by Market Cap")

    base = _lib.DELIVERABLES / f"crypto_report_{date}"
    _report.render_pdf(base, "Daily Crypto Market Report", meta, narrative,
                       prepend_html=top10 + _report.charts_html(charts),
                       append_html=_report.DISCLAIMER)
    print(f"report -> {base}.pdf")

    rows = parse_stance_table(narrative)
    if rows:
        with open(base.with_suffix(".csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["Coin", "Stance", "Confidence", "Why", "Invalidation"])
            w.writeheader()
            w.writerows(rows)
        print(f"stances -> {base}.csv  ({len(rows)} coins)")
    else:
        print("  WARNING: no Coin-by-Coin table found in the narrative; CSV skipped")


if __name__ == "__main__":
    main()
