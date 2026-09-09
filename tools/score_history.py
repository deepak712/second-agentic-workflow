#!/usr/bin/env python3
"""Score past buy/wait calls against what the market actually did. Free (no LLM).

Reads history/calls/<date>.json, fetches each coin's price 7 and 30 days after the
call, and judges whether the stance was useful. Results accumulate in
history/track_record.json, which build_report.py feeds back into the next report
so the model can see its own record.

Calls are only scored once their horizon has fully elapsed, and a scored call is
never re-scored, so this is cheap and idempotent to run daily.

    python tools/score_history.py              # score everything now due
    python tools/score_history.py --summary    # print the record, fetch nothing
    python tools/score_history.py --rescore    # recompute all verdicts from scratch
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time

import _lib  # noqa: E402
import requests

API = "https://api.coingecko.com/api/v3"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

HORIZONS = (7, 30)

# A "Wait" that misses a big rally was a bad call; a "Wait" that dodges a drop or
# sits through chop was a good one. These are the thresholds for that judgement,
# in percent, measured against BTC so a call is not credited for beta alone.
RALLY = 10.0    # a move this far up means Wait/Avoid cost you something
DROP = -5.0     # a move this far down vindicates Wait/Avoid
EDGE = 2.0      # Accumulate needs at least this much to count as right


def get(path: str, params: dict | None = None, attempts: int = 4):
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
            time.sleep(3 * (2 ** i))
    print(f"  lookup failed {path}: {last}", file=sys.stderr)
    return None


def price_on(coin_id: str, date: str, cache: dict):
    """Close price for a coin on a given date, memoised across the whole run."""
    key = f"{coin_id}@{date}"
    if key in cache:
        return cache[key]
    d = datetime.date.fromisoformat(date)
    data = get(f"/coins/{coin_id}/history",
               {"date": d.strftime("%d-%m-%Y"), "localization": "false"})
    price = None
    if data:
        try:
            price = data["market_data"]["current_price"]["usd"]
        except (KeyError, TypeError):
            price = None
    cache[key] = price
    return price


def verdict(stance: str, ret: float, rel: float) -> str:
    """Judge a stance on what the reader's money would actually have done.

    Absolute return is the primary test, because that is what a holder lives
    through: 'Wait' on something that then ran 29% was a miss regardless of how
    BTC did. Relative-to-BTC is recorded alongside as context, and is the only
    sensible read for calls on BTC itself, where relative return is zero by
    construction.
    """
    s = stance.lower()
    if s.startswith("accum"):
        return "correct" if ret >= EDGE else "incorrect" if ret <= DROP else "neutral"
    if s.startswith("avoid"):
        # Avoiding something that fell, or merely went nowhere, was useful.
        return "correct" if ret <= 0 else "incorrect" if ret >= RALLY else "neutral"
    if s.startswith("wait"):
        # Wait claims there was no urgency. A big run means there was.
        return "incorrect" if ret >= RALLY else "correct" if ret <= DROP else "neutral"
    return "unscored"


def load_record() -> dict:
    path = _lib.HISTORY / "track_record.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"scored": {}, "updated": None}


def summarise(record: dict) -> dict:
    """Aggregate hit rates by stance and by confidence, per horizon."""
    out: dict = {}
    for horizon in HORIZONS:
        rows = [s for s in record["scored"].values() if str(horizon) in s.get("h", {})]
        if not rows:
            continue
        by_stance: dict = {}
        by_conf: dict = {}
        for s in rows:
            h = s["h"][str(horizon)]
            v, ret = h["verdict"], h["rel_vs_btc"]
            for bucket, key in ((by_stance, s["stance"]), (by_conf, s["confidence"])):
                b = bucket.setdefault(key, {"correct": 0, "incorrect": 0,
                                            "neutral": 0, "n": 0, "avg_ret": 0.0, "avg_rel": 0.0})
                b[v] = b.get(v, 0) + 1
                b["n"] += 1
                b["avg_ret"] += h["return_pct"]
                b["avg_rel"] += ret
        for bucket in (by_stance, by_conf):
            for b in bucket.values():
                for k in ("avg_ret", "avg_rel"):
                    b[k] = round(b[k] / b["n"], 2) if b["n"] else 0.0
        out[str(horizon)] = {"n": len(rows), "by_stance": by_stance, "by_confidence": by_conf}
    return out


def render_summary(record: dict) -> str:
    """Compact plain-text track record, for humans and for the report prompt."""
    summary = summarise(record)
    if not summary:
        return "No calls have reached their scoring horizon yet."
    lines = []
    for horizon in HORIZONS:
        s = summary.get(str(horizon))
        if not s:
            continue
        lines.append(f"--- {horizon}-day horizon ({s['n']} scored calls) ---")
        for stance, b in sorted(s["by_stance"].items()):
            hit = b["correct"] / b["n"] * 100 if b["n"] else 0
            lines.append(f"  {stance:<12} n={b['n']:<3} correct {b['correct']}/{b['n']} "
                         f"({hit:.0f}%)  avg move {b['avg_ret']:+.1f}% "
                         f"(vs BTC {b['avg_rel']:+.1f}%)")
        for conf, b in sorted(s["by_confidence"].items()):
            hit = b["correct"] / b["n"] * 100 if b["n"] else 0
            lines.append(f"  confidence {conf:<8} n={b['n']:<3} correct {hit:.0f}%")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", action="store_true", help="print the record, fetch nothing")
    ap.add_argument("--rescore", action="store_true", help="recompute every verdict")
    ap.add_argument("--today", default=None, help="treat this as today's date (testing)")
    args = ap.parse_args()

    record = load_record()
    if args.summary:
        print(render_summary(record))
        return
    if args.rescore:
        record["scored"] = {}

    today = datetime.date.fromisoformat(args.today or _lib.today())
    calls_dir = _lib.HISTORY / "calls"
    if not calls_dir.exists():
        print("No calls recorded yet - nothing to score.")
        return

    cache: dict = {}
    scored_now = 0
    for path in sorted(calls_dir.glob("*.json")):
        call_date = path.stem
        age = (today - datetime.date.fromisoformat(call_date)).days
        if age < min(HORIZONS):
            continue
        for call in json.loads(path.read_text()):
            key = f"{call_date}:{call['symbol']}"
            entry = record["scored"].get(key) or {
                "date": call_date, "symbol": call["symbol"],
                "stance": call["stance"], "confidence": call["confidence"], "h": {},
            }
            for horizon in HORIZONS:
                if age < horizon or str(horizon) in entry["h"]:
                    continue
                then = (datetime.date.fromisoformat(call_date)
                        + datetime.timedelta(days=horizon)).isoformat()
                now_price = price_on(call["coin_id"], then, cache)
                btc_then = price_on("bitcoin", then, cache)
                if not now_price or not call.get("price_at_call"):
                    continue
                ret = (now_price / call["price_at_call"] - 1) * 100
                rel = ret
                if btc_then and call.get("btc_at_call"):
                    btc_ret = (btc_then / call["btc_at_call"] - 1) * 100
                    rel = ret - btc_ret
                entry["h"][str(horizon)] = {
                    "return_pct": round(ret, 2),
                    "rel_vs_btc": round(rel, 2),
                    "verdict": verdict(call["stance"], ret, rel),
                }
                scored_now += 1
                print(f"  {call_date} {call['symbol']:<6} {call['stance']:<11} "
                      f"{horizon}d: {ret:+.1f}% (vs BTC {rel:+.1f}%) -> "
                      f"{entry['h'][str(horizon)]['verdict']}")
            if entry["h"]:
                record["scored"][key] = entry

    record["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record["summary"] = summarise(record)
    out = _lib.HISTORY / "track_record.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2))
    print(f"\ntrack record -> {out}  ({scored_now} newly scored, "
          f"{len(record['scored'])} total)")
    print(render_summary(record))


if __name__ == "__main__":
    main()
