#!/usr/bin/env python3
"""Fetch the last day's crypto headlines from six RSS feeds. Free, no API key.

Parses with stdlib xml.etree - no feedparser dependency. A feed that fails is
recorded in errors[] and skipped; the tool always exits 0 so one dead source
cannot kill the daily run.

    python tools/fetch_news.py
    python tools/fetch_news.py --hours 30 --max-per-feed 40
"""
from __future__ import annotations

import argparse
import calendar
import email.utils
import html
import re
import sys
import time
import xml.etree.ElementTree as ET

import _lib  # noqa: E402
import requests

# Verified 2026-09-09: all six return 200 with parseable <item> elements.
# NOTE: coindesk's plain /rss/ URL redirects to an HTML page - the
# ?outputType=xml variant is required.
FEEDS = [
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
    ("The Block", "https://www.theblock.co/rss.xml"),
]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(raw: str, limit: int = 400) -> str:
    """Strip HTML tags and entities from a feed description, collapse whitespace."""
    if not raw:
        return ""
    text = TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text).strip()
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def parse_date(raw: str):
    """RFC-822 pubDate -> epoch seconds, or None."""
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt is None:
            return None
        if dt.tzinfo is None:
            return calendar.timegm(dt.timetuple())
        return int(dt.timestamp())
    except Exception:
        return None


def norm_title(t: str) -> str:
    """Normalized key for cross-feed dedupe of the same story."""
    return re.sub(r"[^a-z0-9]+", "", t.lower())[:70]


def text_of(item, *tags):
    for tag in tags:
        el = item.find(tag)
        if el is not None and (el.text or "").strip():
            return el.text.strip()
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=30,
                    help="keep items published within this many hours (default 30)")
    ap.add_argument("--max-per-feed", type=int, default=40,
                    help="cap items taken from each feed (default 40)")
    ap.add_argument("--date", default=None, help="override the run date (YYYY-MM-DD)")
    args = ap.parse_args()

    cutoff = time.time() - args.hours * 3600
    errors: list[str] = []
    items: list[dict] = []
    seen: set[str] = set()
    per_feed: dict[str, int] = {}

    for source, url in FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as exc:
            errors.append(f"{source}: {exc!r}")
            print(f"  {source} failed: {exc!r}", file=sys.stderr)
            per_feed[source] = 0
            continue

        kept = 0
        for it in root.findall(".//item")[: args.max_per_feed]:
            title = clean(text_of(it, "title"), 300)
            if not title:
                continue
            key = norm_title(title)
            if not key or key in seen:
                continue
            ts = parse_date(text_of(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date"))
            # Undated items are kept: better a stale headline than a silent gap.
            if ts is not None and ts < cutoff:
                continue
            seen.add(key)
            items.append({
                "source": source,
                "title": title,
                "url": text_of(it, "link", "guid"),
                "published": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
                              if ts else None),
                "ts": ts,
                "summary": clean(text_of(it, "description", "summary")),
            })
            kept += 1
        per_feed[source] = kept
        print(f"  {source}: {kept} items")

    items.sort(key=lambda i: i["ts"] or 0, reverse=True)
    for n, item in enumerate(items):
        item["id"] = n

    out = {
        "date": args.date or _lib.today(),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window_hours": args.hours,
        "per_feed": per_feed,
        "count": len(items),
        "items": items,
        "errors": errors,
    }
    path = _lib.write_tmp(_lib.stage_path("news", args.date), out)
    print(f"news -> {path}  ({len(items)} deduped items, {len(errors)} feed errors)")


if __name__ == "__main__":
    main()
