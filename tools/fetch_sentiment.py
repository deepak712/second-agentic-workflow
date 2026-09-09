#!/usr/bin/env python3
"""Fetch the Crypto Fear & Greed Index (alternative.me), with 30 days of history.

Free (no API key). Records today's reading plus the deltas and averages the
report needs to say whether sentiment is improving or deteriorating.

    python tools/fetch_sentiment.py
    python tools/fetch_sentiment.py --days 30
"""
from __future__ import annotations

import argparse
import sys
import time

import _lib  # noqa: E402
import requests

API = "https://api.alternative.me/fng/"


def mean(xs: list[int]):
    return round(sum(xs) / len(xs), 1) if xs else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="history length (default 30)")
    ap.add_argument("--date", default=None, help="override the run date (YYYY-MM-DD)")
    args = ap.parse_args()

    errors: list[str] = []
    series: list[dict] = []
    try:
        r = requests.get(API, params={"limit": args.days, "format": "json"}, timeout=20)
        r.raise_for_status()
        rows = r.json().get("data", []) or []
        for row in rows:
            series.append({
                "t": int(row["timestamp"]),
                "value": int(row["value"]),
                "label": row.get("value_classification", ""),
            })
    except Exception as exc:
        errors.append(f"fng: {exc!r}")
        print(f"  fear & greed fetch failed: {exc!r}", file=sys.stderr)

    # API returns newest first.
    values = [s["value"] for s in series]
    current = series[0] if series else None
    out = {
        "date": args.date or _lib.today(),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "alternative.me/fng",
        "current": current,
        "yesterday": series[1] if len(series) > 1 else None,
        "week_ago": series[7] if len(series) > 7 else None,
        "delta_1d": (values[0] - values[1]) if len(values) > 1 else None,
        "delta_7d": (values[0] - values[7]) if len(values) > 7 else None,
        "avg_7d": mean(values[:7]),
        "avg_30d": mean(values[:30]),
        "min_30d": min(values) if values else None,
        "max_30d": max(values) if values else None,
        # Oldest-first, which is the order the chart wants.
        "series": series[::-1],
        "errors": errors,
    }
    path = _lib.write_tmp(_lib.stage_path("sentiment", args.date), out)
    label = f"{current['value']} ({current['label']})" if current else "unavailable"
    print(f"sentiment -> {path}  (now {label}, {len(series)}d history)")


if __name__ == "__main__":
    main()
