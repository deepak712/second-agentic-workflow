#!/usr/bin/env python3
"""Run the whole daily pipeline end to end: fetch -> triage -> charts -> report -> email.

PAID: the triage and report stages spend credits (~$0.10-0.30/run total), so this
tool requires its own --confirm. That keeps the per-tool cost gates meaningful
when you run stages by hand.

This is the entry point launchd calls. It logs to .tmp/logs/<date>.log.

    python tools/run_daily.py --confirm             # the daily run
    python tools/run_daily.py --confirm --force     # regenerate even if today's PDF exists
    python tools/run_daily.py --confirm --skip-email
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import subprocess
import sys
import time

import _lib

PY = str(_lib.ROOT / ".venv" / "bin" / "python")
TOOLS = _lib.ROOT / "tools"


class Tee:
    """Write to the console and the day's log file at once."""

    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(path, "a", buffering=1)

    def write(self, text: str) -> None:
        sys.__stdout__.write(text)
        self.fh.write(text)

    def flush(self) -> None:
        sys.__stdout__.flush()
        self.fh.flush()


def run(name: str, argv: list[str]) -> None:
    """Run one tool. Raises RuntimeError on failure so the caller can report it."""
    print(f"\n--- {name} ---", flush=True)
    proc = subprocess.run([PY, str(TOOLS / argv[0])] + argv[1:],
                          cwd=str(_lib.ROOT), capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip(), flush=True)
    if proc.stderr:
        print(proc.stderr.rstrip(), flush=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {proc.returncode}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="run date (YYYY-MM-DD), defaults to today")
    ap.add_argument("--confirm", action="store_true", help="required: the paid stages will spend")
    ap.add_argument("--force", action="store_true", help="rebuild even if today's PDF exists")
    ap.add_argument("--skip-email", action="store_true", help="build the report but do not send")
    ap.add_argument("--provider", choices=["smtp", "resend"], default="smtp")
    ap.add_argument("--top", type=int, default=50, help="coins to scan (default 50)")
    args = ap.parse_args()
    date = args.date or _lib.today()

    sys.stdout = Tee(_lib.TMP / "logs" / f"{date}.log")
    started = time.time()
    print("=" * 68)
    print(f"daily crypto report | {date} | started {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 68)

    if not args.confirm:
        raise SystemExit(
            "run_daily.py spends credits on the triage and report stages.\n"
            "Re-run with --confirm. To see the cost first:\n"
            "  .venv/bin/python tools/triage_news.py\n"
            "  .venv/bin/python tools/build_report.py"
        )

    pdf = _lib.DELIVERABLES / f"crypto_report_{date}.pdf"
    if pdf.exists() and not args.force:
        print(f"\n{pdf} already exists; nothing to do (use --force to rebuild).")
        return

    try:
        run("fetch market", ["fetch_market.py", "--top", str(args.top), "--date", date])
        run("fetch sentiment", ["fetch_sentiment.py", "--date", date])
        run("fetch news", ["fetch_news.py", "--date", date])
        run("triage news", ["triage_news.py", "--date", date, "--confirm"])
        run("make charts", ["make_charts.py", "--date", date])
        run("build report", ["build_report.py", "--date", date, "--confirm"])
        # Score whatever has matured. Free, idempotent, and it must run after the
        # report so today's calls are already recorded. A failure here is not
        # worth losing the report over.
        try:
            run("score history", ["score_history.py"])
        except Exception as exc:
            print(f"  (scoring failed, continuing: {exc})", flush=True)
        if not args.skip_email:
            run("send email", ["send_email.py", "--date", date, "--provider", args.provider])
    except Exception as exc:
        print(f"\nRUN FAILED: {exc}", flush=True)
        if not args.skip_email:
            # Best effort: tell the user the run broke rather than failing silently
            # at 8am. A failure to send the failure notice must not mask the cause.
            try:
                import send_email
                send_email.send(
                    f"Crypto report FAILED for {date}",
                    f"The daily crypto report run failed.\n\n{exc}\n\n"
                    f"Log: {_lib.TMP / 'logs' / (date + '.log')}",
                    None, args.provider)
            except Exception as notify_exc:
                print(f"  (could not send failure notice: {notify_exc})", flush=True)
        raise SystemExit(1)

    mins, secs = divmod(int(time.time() - started), 60)
    print(f"\nDONE in {mins}m{secs:02d}s -> {pdf}")


if __name__ == "__main__":
    main()
