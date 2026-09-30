---
name: crypto-report
description: Operate and improve the daily crypto market report. Use when running, debugging, reviewing, or changing the report pipeline in this project - fetching market/news/sentiment data, triaging headlines, building the PDF, emailing it, scoring past buy/wait calls, or acting on reader feedback. Also use when the user asks why a report said something, wants the report changed, or asks how the agent is performing.
---

# Daily Crypto Report

A WAT-framework pipeline that emails a daily crypto research brief ending in
per-coin buy/wait theses. See `workflows/daily_crypto_report.md` for the full SOP
and `CLAUDE.md` for the framework rules.

## Non-negotiables

1. **`.venv/bin/python`, never `python3`.** System Python is 3.9 with none of the
   deps. Because it is 3.9, every tool needs `from __future__ import annotations`
   and cannot use nested same-type quotes inside f-strings.
2. **Paid tools are dry-run by default.** `triage_news.py` and `build_report.py`
   print a cost estimate and exit until `--confirm`. **Always show the user the
   estimate and get an explicit go-ahead before spending.** This is a hard rule
   from CLAUDE.md, not a courtesy.
3. **`--reuse-narrative` costs nothing.** Any change to layout, CSS, charts,
   tables or badges must be verified this way first. Never spend a synthesis call to
   check a visual change.
4. **`.tmp/` is disposable; `history/` is not.** The track record and recorded
   calls are the only learning state. Never write learning state to `.tmp/`, and
   never delete `history/`.
5. **This is research, not advice.** Never remove the disclaimer, and never let
   the report emit a bare signal without reasoning and an invalidation condition.

## Running it

```bash
.venv/bin/python tools/run_daily.py --confirm          # whole chain
.venv/bin/python tools/check_env.py                    # verify credentials, masked
./scripts/install_schedule.sh --run-now                # force the scheduled job
```

Stage by stage, and what each costs:

| Tool | Cost | Notes |
|---|---|---|
| `fetch_market.py` `fetch_sentiment.py` `fetch_news.py` | free | keyless; failures land in `errors[]`, exit 0 |
| `triage_news.py` | **paid** (Haiku 4.5) | ~$0.03; classifies ~100 headlines |
| `make_charts.py` | free | 3 PNGs |
| `build_report.py` | **paid** (Sonnet 5.5) | ~$0.04; `--reuse-narrative` is free |
| `send_email.py` | free | `--test` verifies credentials without a report |
| `score_history.py` | free | scores past calls; idempotent, safe to re-run |

Typical full run: **~$0.15–0.20**.

## The improvement loop

This is the part that makes the agent better rather than merely consistent. Run
it after each report, and always when the user asks how it is performing.

1. **Score what has matured.**
   ```bash
   .venv/bin/python tools/score_history.py            # scores anything now due
   .venv/bin/python tools/score_history.py --summary  # read it, fetch nothing
   ```
   Calls are scored at 7 and 30 days on absolute return, with return-vs-BTC
   recorded as context. A failed price lookup simply leaves that horizon
   unscored and is retried on the next run.

2. **Read the record honestly.** Look for a *pattern*, not a bad day:
   - `Wait` scoring `incorrect` repeatedly → the report is too cautious and is
     missing real entries. Say so plainly to the user.
   - `High` confidence not outperforming `Low` → confidence is decorative and
     should be tightened or dropped.
   - `Avoid` mostly `neutral` → the bar for Avoid is too low; it is being used
     where Wait belongs.

3. **Change something specific.** A pattern is only worth finding if it changes
   the next report. The levers, in order of preference:
   - `feedback.md` — the reader's standing instructions, injected verbatim every
     run. Highest signal. Suggest edits here first.
   - The `SYSTEM` prompt in `build_report.py` — for systematic framing problems.
   - Scoring thresholds `RALLY` / `DROP` / `EDGE` in `score_history.py` — only if
     the *measurement* is wrong, never to make the record look better.

4. **Write the lesson down.** Append to `## Lessons Learned` in
   `workflows/daily_crypto_report.md`, dated. If it is not written down it will
   be relearned.

## Reader feedback

`feedback.md` is injected into every report prompt. When the user complains about
a report ("too cautious", "stop covering memecoins", "I hold SOL"), **offer to add
it to `feedback.md`** rather than only fixing today's output — that is the
difference between a one-off patch and a durable improvement.

## Known failure modes

- **Empty reasoning in a stance row** ("no price data supplied") → a deep-dive
  coin reached the table without a data row. Check the deep-dive section in
  `build_report.build_user`; every deep-dive coin needs its own row.
- **CoinGecko 429** → backoff is built in; if it still fails, wait, do not hammer.
- **Synthesis truncation mid-section** → adaptive thinking counts against `max_tokens`;
  raise `--max-tokens`.
- **No Coin-by-Coin table found** → PDF still renders, CSV and call recording are
  skipped. Needs a fresh `--confirm` run.
- **SMTP blocked on port 587** → expected in any sandbox; use `--provider resend`.
- **New pegged/wrapped asset polluting the movers** → add its ticker to `PEGGED`
  in `fetch_market.py`.

## Changing the report

Layout, colour, tables and badges are all in `tools/_report.py`
(`CSS`, `market_table_html`, `style_model_tables`). Content and section structure
are in the `SYSTEM` prompt in `build_report.py`. Deterministic data the reader
must always get — prices, the top-10 table — belongs in rendered HTML, **not** in
the model's output, so it can never be missing or hallucinated.

Always verify with `--reuse-narrative` before spending on a regeneration.
