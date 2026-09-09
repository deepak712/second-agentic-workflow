# Second Agentic Workflow

A [WAT framework](CLAUDE.md) project: **W**orkflows (markdown SOPs) → **A**gent (Claude, orchestration) → **T**ools (deterministic Python).

Builds a **daily crypto market report** — market data, Fear & Greed, and the day's
news classified by sentiment — and emails it as a PDF ending in a per-coin
buy-or-wait thesis with invalidation levels.

> Research output, not financial advice. See the disclaimer on every report.

## Layout
```
workflows/   Markdown SOPs — what to do and how
tools/       Python scripts — deterministic execution
scripts/     launchd plist + installer for the daily schedule
deliverables/  Generated PDF + CSV (gitignored)
.tmp/        Disposable intermediates, one file per stage per day (gitignored)
.env         Secrets only (gitignored) — copy from .env.example
.venv/       Python environment (gitignored)
```

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in keys
```

`.env` needs `ANTHROPIC_API_KEY`, plus `SMTP_USER` / `SMTP_APP_PASSWORD` /
`REPORT_TO_EMAIL` for delivery. Gmail requires an
[App Password](https://myaccount.google.com/apppasswords) (2FA must be on) — not
your account password.

## Running
```bash
.venv/bin/python tools/run_daily.py --confirm      # the whole chain
.venv/bin/python tools/<script>.py --help          # any single stage
```

Paid stages are dry-run by default: they print a token/USD estimate and exit until
you pass `--confirm`. A full run costs roughly **$0.10–0.20**.

## Scheduling
```bash
./scripts/install_schedule.sh              # daily at 08:07 local
./scripts/install_schedule.sh --run-now    # force a run
./scripts/install_schedule.sh --uninstall
```

See [`workflows/daily_crypto_report.md`](workflows/daily_crypto_report.md) for the
full SOP, edge cases and lessons learned.
