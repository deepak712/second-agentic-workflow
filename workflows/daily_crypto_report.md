# Workflow: Daily Crypto Market Report

## Objective
Every 24 hours, gather crypto market data, sentiment and news; classify what is
positive vs negative; and email a PDF research brief that ends in a per-coin
**buy or wait** thesis with reasoning, confidence and invalidation conditions.

## Required Inputs
- `date` — the run date, `YYYY-MM-DD`. Defaults to today; every tool takes `--date`
  so an old day can be rebuilt from `.tmp/` without re-fetching.
- `--top` — how many coins to scan by market cap (default 50)
- `.env` must contain `ANTHROPIC_API_KEY`, and for delivery either
  `SMTP_USER` + `SMTP_APP_PASSWORD` + `REPORT_TO_EMAIL` (smtp) or
  `RESEND_API_KEY` + `REPORT_TO_EMAIL` (resend)

## Tools Used
| Tool | Cost | Purpose |
|---|---|---|
| `tools/fetch_market.py` | free | CoinGecko: global totals, top-N coins, BTC/ETH 30d history, mover selection |
| `tools/fetch_sentiment.py` | free | alternative.me Fear & Greed + 30d history and deltas |
| `tools/fetch_news.py` | free | six RSS feeds → deduped headlines from the last 30h |
| `tools/triage_news.py` | **PAID** — ~1 Sonnet call per 40 headlines | sentiment / impact / coins / theme per headline |
| `tools/make_charts.py` | free | three PNGs: fear & greed, movers, BTC 30d |
| `tools/build_report.py` | **PAID** — 1 Opus call | synthesis → narrative → PDF + stance CSV |
| `tools/send_email.py` | free | emails the PDF, smtp or resend backend |
| `tools/run_daily.py` | wraps the paid ones | the whole chain; launchd's entry point |

Models: Sonnet 5 (`claude-sonnet-5`) for triage, Opus 5 (`claude-opus-5`) for
synthesis. Typical run: **~$0.10–0.20**.

**Paid tools are dry-run by default** — they print a token/cost estimate and exit.
Re-run with `--confirm` to spend. Show the user the estimate and get a go-ahead
before re-running with `--confirm` (per CLAUDE.md).

## Steps

Unattended, this is one command:
```
.venv/bin/python tools/run_daily.py --confirm
```

By hand, stage by stage:

1. **Fetch** (free, ~30s total). Order does not matter.
   ```
   .venv/bin/python tools/fetch_market.py --top 50
   .venv/bin/python tools/fetch_sentiment.py
   .venv/bin/python tools/fetch_news.py
   ```
   Check each output's `errors[]`. Sanity-check `.tmp/news/<date>.json` has 80–130
   items; far fewer means feeds are failing or the dedupe is too aggressive.

2. **Triage the headlines** — dry run first, then spend:
   ```
   .venv/bin/python tools/triage_news.py             # estimate
   .venv/bin/python tools/triage_news.py --confirm
   ```
   Spot-check ~10 classifications in `.tmp/triage/<date>.json` against the actual
   headlines before trusting the day's `net_tone`.

3. **Render charts** (free):
   ```
   .venv/bin/python tools/make_charts.py
   ```

4. **Build the report** — dry run, then spend:
   ```
   .venv/bin/python tools/build_report.py            # estimate
   .venv/bin/python tools/build_report.py --confirm
   ```
   Iterating on layout afterwards is free: `--reuse-narrative` rebuilds the PDF
   from `.tmp/report_narrative_<date>.md` with no Opus call.

5. **Send**:
   ```
   .venv/bin/python tools/send_email.py --test       # credentials check, no attachment
   .venv/bin/python tools/send_email.py
   ```

6. Hand the user the path to the PDF.

## Expected Output
- `deliverables/crypto_report_<YYYY-MM-DD>.pdf` — charts, market snapshot, sentiment
  read, positive/negative news drivers, the coin-by-coin buy/wait table, watch levels,
  bottom line, and the standing not-financial-advice disclaimer.
- `deliverables/crypto_report_<YYYY-MM-DD>.csv` — the stance table as raw data.
- `.tmp/<stage>/<date>.json` holds every intermediate, so any day is fully replayable.
- `.tmp/logs/<date>.log` — the unattended run's log.

## Edge Cases & Failure Handling
- **CoinGecko 429** — `fetch_market.py` backs off 3s/6s/12s/24s. The free tier is
  strict; if a run still fails, wait a minute rather than hammering it.
- **A feed goes dark** — recorded in `errors[]`, tool still exits 0. One or two dead
  feeds is survivable; the report just has less to work with.
- **Empty news day** — `triage_news.py` exits with a clear message if there are no
  items. Do not fabricate a report from market data alone without saying so.
- **Model returns the wrong item count in triage** — `normalize()` keys results back
  to the batch by id and drops anything unmatched, so a short reply loses items but
  never corrupts them. The count mismatch is printed to stderr.
- **Opus truncation** — Opus 5's adaptive thinking counts against `max_tokens`.
  `build_report.py` defaults to 9000 and `_llm.complete_text` warns when the cap is
  hit; raise `--max-tokens` if the narrative ends mid-section.
- **No Coin-by-Coin table in the narrative** — the PDF still renders; the CSV is
  skipped with a warning. Re-run with `--confirm` to get a fresh narrative.
- **Stablecoins / wrapped tokens polluting the movers** — excluded by the `PEGGED`
  set in `fetch_market.py`. New pegged assets appear regularly; add them there when
  something like USDe or cbBTC shows up in the mover list.
- **SMTP blocked (port 587)** — likely in any sandboxed or cloud environment.
  `send_email.py` says so explicitly and the fix is `--provider resend`.

## Lessons Learned
- 2026-09-09 — Initial build. CoinDesk's plain `/arc/outboundfeeds/rss/` URL
  redirects to HTML; the `?outputType=xml` variant is required. All six feeds
  verified returning ~100 deduped items/day.
- 2026-09-09 — RSS parses fine with stdlib `xml.etree.ElementTree`; `feedparser`
  is not needed. Undated items are kept rather than dropped, since some feeds omit
  `pubDate` intermittently.
- 2026-09-09 — System Python is 3.9, so nested same-type quotes inside f-strings
  are a syntax error (that only became legal in 3.12). Keep `%`-formatting for the
  aligned data tables in `build_report.py`.
- 2026-09-09 — **First live run.** 97 headlines → 3 Sonnet batches + 1 Opus call,
  2m54s wall clock, 5-page PDF. Actual spend came in well under the dry-run ceiling.
- 2026-09-09 — **Bug found in the first report:** the prompt printed price rows for
  only the top 30 coins, but the deep-dive set can include coins ranked 31–50. The
  model was asked for a stance on NEAR/AAVE/ASTER/DOT with no data for them and
  wrote "no price data supplied" in 4 of 13 rows. Fixed by giving every deep-dive
  coin its own row in a dedicated section. **If new coins ever show up with empty
  reasoning, check this first** — it means a coin reached the table without data.
- 2026-09-09 — Triage `max_tokens` was 180/item; actual replies are ~40. Lowered to
  100 (still 2.5x headroom), which halves the dry-run estimate ceiling. Do not go
  below ~60 or a full batch will truncate and fail JSON parsing.
- 2026-09-09 — `./scripts/install_schedule.sh --run-now` on a day whose PDF already
  exists is a **free** way to verify launchd works: the job runs, hits the
  idempotency guard, and exits 0 without making any API call. Use this to test the
  schedule rather than forcing a paid rebuild.
- 2026-09-09 — Gmail App Passwords are displayed grouped ("abcd efgh ijkl mnop") and
  must be stored unspaced. `tools/check_env.py --fix-spaces` handles it; the same
  tool verifies both credentials without echoing them.
