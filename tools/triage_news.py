#!/usr/bin/env python3
"""Classify the day's headlines by sentiment, impact, coins mentioned and theme.

PAID: ~1 Sonnet call per 40 headlines (typically 3 calls/day).
Dry-run by default - prints a token/USD estimate and exits; --confirm to spend.

    python tools/triage_news.py                 # estimate only
    python tools/triage_news.py --confirm       # spend
    python tools/triage_news.py --date 2026-09-09 --batch 40 --confirm
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

import _lib
import _llm

SYSTEM = """You are a crypto market news analyst. You classify headlines for a \
daily market report. You are neutral and skeptical: headlines are written to be \
clicked, so judge the underlying substance, not the wording.

For EACH item you are given, return an object with exactly these fields:
  "id"        - the integer id you were given, unchanged
  "sentiment" - "positive", "negative" or "neutral", for crypto asset prices
  "impact"    - integer 1-5. 5 = moves the whole market (a major regulatory
                ruling, an ETF decision, a large exchange failure, a macro shock).
                3 = matters for one significant asset or sector.
                1 = routine coverage, opinion, price commentary or minor news.
  "coins"     - array of UPPERCASE tickers the item is materially about, e.g.
                ["BTC","ETH"]. Empty array if it is about the market generally.
                Only tickers, never project names. At most 4.
  "theme"     - exactly one of: "regulation", "adoption", "security", "macro",
                "tech", "market"

Rules:
- Price-movement recaps ("Bitcoin dips below X") are "market" theme and impact 1-2;
  the price data is already in the report, so these are low value.
- A hack, exploit or insolvency is "security" and negative, impact >= 3.
- Classify sentiment from the market's perspective, not the subject's: an
  enforcement action against a competitor is still negative for sentiment.
- Return ONE object per input item, same count, ids preserved.

Return a JSON array of these objects and nothing else."""

VALID_SENTIMENT = {"positive", "negative", "neutral"}
VALID_THEME = {"regulation", "adoption", "security", "macro", "tech", "market"}


def build_user(items: list[dict]) -> str:
    lines = []
    for it in items:
        lines.append(json.dumps({
            "id": it["id"],
            "source": it["source"],
            "title": it["title"],
            "summary": (it.get("summary") or "")[:280],
        }))
    return "Classify these %d news items:\n\n%s" % (len(items), "\n".join(lines))


def normalize(raw, batch: list[dict]) -> list[dict]:
    """Coerce the model's output into clean records, keyed back to the batch."""
    if isinstance(raw, dict):
        # Tolerate {"items": [...]} shaped replies.
        raw = next((v for v in raw.values() if isinstance(v, list)), [])
    by_id = {it["id"]: it for it in batch}
    out = []
    for rec in raw if isinstance(raw, list) else []:
        if not isinstance(rec, dict):
            continue
        try:
            rid = int(rec.get("id"))
        except (TypeError, ValueError):
            continue
        src = by_id.get(rid)
        if src is None:
            continue
        sentiment = str(rec.get("sentiment", "")).lower().strip()
        theme = str(rec.get("theme", "")).lower().strip()
        try:
            impact = max(1, min(5, int(rec.get("impact", 1))))
        except (TypeError, ValueError):
            impact = 1
        coins = rec.get("coins") or []
        coins = [str(c).upper().strip().lstrip("$") for c in coins if str(c).strip()][:4]
        out.append({
            "id": rid,
            "source": src["source"],
            "title": src["title"],
            "url": src.get("url"),
            "published": src.get("published"),
            "sentiment": sentiment if sentiment in VALID_SENTIMENT else "neutral",
            "impact": impact,
            "coins": coins,
            "theme": theme if theme in VALID_THEME else "market",
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="run date to triage (YYYY-MM-DD)")
    ap.add_argument("--batch", type=int, default=40, help="headlines per call (default 40)")
    ap.add_argument("--confirm", action="store_true", help="actually spend credits")
    args = ap.parse_args()
    date = args.date or _lib.today()

    try:
        news = _lib.read_tmp(_lib.stage_path("news", date))
    except FileNotFoundError:
        raise SystemExit(f"No news for {date} - run tools/fetch_news.py first.")

    items = news.get("items") or []
    if not items:
        raise SystemExit(f"News file for {date} has no items; nothing to triage.")

    batches = [items[i:i + args.batch] for i in range(0, len(items), args.batch)]

    if not args.confirm:
        # Batches are near-identical in size, so estimate one and scale.
        est = _llm.estimate(_llm.SONNET, SYSTEM, build_user(batches[0]),
                            max_output_tokens=100 * len(batches[0]))
        est["input_tokens"] *= len(batches)
        est["assumed_output_tokens"] *= len(batches)
        est["est_usd"] = round(est["est_usd"] * len(batches), 4)
        print(f"{len(items)} items in {len(batches)} batches of <={args.batch}", file=sys.stderr)
        _llm.print_estimate(est)
        return

    classified: list[dict] = []
    for n, batch in enumerate(batches, 1):
        print(f"  triaging batch {n}/{len(batches)} ({len(batch)} items)...")
        raw = _llm.complete_json(_llm.SONNET, SYSTEM, build_user(batch),
                                 max_tokens=100 * len(batch), effort="low")
        got = normalize(raw, batch)
        if len(got) != len(batch):
            print(f"    note: {len(batch)} sent, {len(got)} classified", file=sys.stderr)
        classified.extend(got)

    sentiments = Counter(c["sentiment"] for c in classified)
    themes = Counter(c["theme"] for c in classified)
    coin_mentions: Counter = Counter()
    coin_score: dict = {}
    for c in classified:
        weight = c["impact"] * (1 if c["sentiment"] == "positive"
                                else -1 if c["sentiment"] == "negative" else 0)
        for sym in c["coins"]:
            coin_mentions[sym] += 1
            coin_score[sym] = coin_score.get(sym, 0) + weight

    # Impact-weighted net tone across the day, normalized to roughly -1..+1.
    total_impact = sum(c["impact"] for c in classified) or 1
    net = sum(c["impact"] * (1 if c["sentiment"] == "positive"
                             else -1 if c["sentiment"] == "negative" else 0)
              for c in classified)

    out = {
        "date": date,
        "model": _llm.SONNET,
        "count": len(classified),
        "sentiment_counts": dict(sentiments),
        "theme_counts": dict(themes),
        "net_tone": round(net / total_impact, 3),
        "coin_mentions": dict(coin_mentions.most_common(25)),
        "coin_tone": {k: coin_score[k] for k, _ in coin_mentions.most_common(25)},
        "top_stories": sorted(classified, key=lambda c: -c["impact"])[:25],
        "items": classified,
    }
    path = _lib.write_tmp(_lib.stage_path("triage", date), out)
    print(f"triage -> {path}")
    print(f"  {dict(sentiments)}  net tone {out['net_tone']:+.2f}  themes {dict(themes)}")


if __name__ == "__main__":
    main()
