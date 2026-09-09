#!/usr/bin/env python3
"""Render the report's charts as PNGs from the fetched market + sentiment JSON.

Free (no API calls). Writes three charts sized for the A4 content width:
fear & greed history, 24h gainers/losers, and the BTC 30-day price line.
A chart whose input data is missing is skipped, not fatal.

    python tools/make_charts.py
    python tools/make_charts.py --date 2026-09-09
"""
from __future__ import annotations

import argparse
import datetime
import sys

import _lib  # noqa: E402
import matplotlib

matplotlib.use("Agg")  # headless: must be set before pyplot is imported
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

# A4 with 1.6cm margins leaves ~17.8cm (~7in) of content width.
FIGSIZE = (6.4, 2.5)
DPI = 150

# Kept in sync with the PDF palette in _report.py.
INK = "#12385c"       # navy, matches the report headings
GRID = "#c8d4de"
UP = "#12805c"
DOWN = "#c0392b"
LINE = "#2b6cb0"

# Fear & Greed zone bands, from the index's own classification thresholds.
ZONES = [(0, 25, "#c0392b", "Extreme fear"), (25, 45, "#e67e22", "Fear"),
         (45, 55, "#95a5a6", "Neutral"), (55, 75, "#7cb342", "Greed"),
         (75, 100, "#12805c", "Extreme greed")]


def style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK, labelsize=7, length=3)
    ax.grid(True, axis="y", color=GRID, linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)


def save(fig, name: str, date: str) -> str:
    path = _lib.TMP / "charts" / date / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  chart -> {path}")
    return str(path)


def chart_fng(sent: dict, date: str):
    series = sent.get("series") or []
    if len(series) < 2:
        return None
    xs = [datetime.datetime.utcfromtimestamp(s["t"]) for s in series]
    ys = [s["value"] for s in series]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    for lo, hi, color, _ in ZONES:
        ax.axhspan(lo, hi, color=color, alpha=0.10, linewidth=0)
    ax.plot(xs, ys, color=INK, linewidth=1.6)
    ax.scatter([xs[-1]], [ys[-1]], color=INK, s=26, zorder=5)
    ax.annotate(f"{ys[-1]}", (xs[-1], ys[-1]), textcoords="offset points",
                xytext=(6, 0), fontsize=8, fontweight="bold", color=INK, va="center")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Index", fontsize=7, color=INK)
    style(ax)
    fig.autofmt_xdate(rotation=0, ha="center")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
    return save(fig, "fear_greed.png", date)


def chart_movers(market: dict, date: str):
    by = {c["id"]: c for c in market.get("coins", [])}
    gainers = [by[i] for i in market.get("gainers", []) if i in by]
    losers = [by[i] for i in market.get("losers", []) if i in by]
    # Strictly descending top-to-bottom: biggest gainer, ..., biggest loser.
    # `losers` arrives worst-first, so it is reversed to sit under the gainers.
    rows = gainers + losers[::-1]
    if not rows:
        return None

    labels = [c["symbol"].upper() for c in rows]
    vals = [c["chg_24h"] for c in rows]
    colors = [UP if v >= 0 else DOWN for v in vals]

    fig, ax = plt.subplots(figsize=(FIGSIZE[0], 0.26 * len(rows) + 0.7))
    ypos = range(len(rows))
    ax.barh(list(ypos), vals, color=colors, height=0.68)
    ax.set_yticks(list(ypos))
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.invert_yaxis()
    ax.axvline(0, color=INK, linewidth=0.9)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    pad = max(abs(min(vals)), abs(max(vals))) * 0.16 or 1
    for y, v in zip(ypos, vals):
        ax.text(v + (pad * 0.18 if v >= 0 else -pad * 0.18), y, f"{v:+.1f}%",
                va="center", ha="left" if v >= 0 else "right",
                fontsize=7, color=INK)
    ax.set_xlim(min(0, min(vals)) - pad, max(0, max(vals)) + pad)
    style(ax)
    ax.grid(False, axis="y")
    ax.grid(True, axis="x", color=GRID, linewidth=0.6)
    return save(fig, "movers.png", date)


def chart_btc(market: dict, date: str):
    hist = (market.get("history") or {}).get("bitcoin") or []
    if len(hist) < 2:
        return None
    xs = [datetime.datetime.utcfromtimestamp(p["t"]) for p in hist]
    ys = [p["price"] for p in hist]

    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.plot(xs, ys, color=LINE, linewidth=1.6)
    ax.fill_between(xs, min(ys) * 0.985, ys, color=LINE, alpha=0.10)
    ax.scatter([xs[-1]], [ys[-1]], color=LINE, s=26, zorder=5)
    ax.annotate(f"${ys[-1]:,.0f}", (xs[-1], ys[-1]), textcoords="offset points",
                xytext=(6, 0), fontsize=8, fontweight="bold", color=LINE, va="center")
    ax.set_ylim(min(ys) * 0.985, max(ys) * 1.02)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v/1000:,.0f}k"))
    style(ax)
    fig.autofmt_xdate(rotation=0, ha="center")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
    return save(fig, "btc_30d.png", date)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="run date to chart (YYYY-MM-DD)")
    args = ap.parse_args()
    date = args.date or _lib.today()

    try:
        market = _lib.read_tmp(_lib.stage_path("market", date))
        sent = _lib.read_tmp(_lib.stage_path("sentiment", date))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing input: {exc}. Run fetch_market.py and fetch_sentiment.py first.")

    made = {
        "fear_greed": chart_fng(sent, date),
        "movers": chart_movers(market, date),
        "btc_30d": chart_btc(market, date),
    }
    made = {k: v for k, v in made.items() if v}
    _lib.write_tmp(f"charts/{date}/index.json", made)
    print(f"charts -> {len(made)}/3 rendered for {date}")
    if len(made) < 3:
        print("  (missing charts are skipped, not fatal)", file=sys.stderr)


if __name__ == "__main__":
    main()
