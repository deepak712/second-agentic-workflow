# Agent Instructions

You're working inside the **WAT framework** (Workflows, Agents, Tools). This architecture separates concerns so that probabilistic AI handles reasoning while deterministic code handles execution. That separation is what makes this system reliable.

## The WAT Architecture

**Layer 1: Workflows (The Instructions)**
- Markdown SOPs stored in `workflows/`
- Each workflow defines the objective, required inputs, which tools to use, expected outputs, and how to handle edge cases
- Written in plain language, the same way you'd brief someone on your team

**Layer 2: Agents (The Decision-Maker)**
- This is your role. You're responsible for intelligent coordination.
- Read the relevant workflow, run tools in the correct sequence, handle failures gracefully, and ask clarifying questions when needed
- You connect intent to execution without trying to do everything yourself
- Example: If you need the day's crypto headlines, don't fetch and parse them yourself. Read `workflows/daily_crypto_report.md`, figure out the required inputs, then execute `tools/fetch_news.py`

**Layer 3: Tools (The Execution)**
- Python scripts in `tools/` that do the actual work
- API calls, data transformations, file operations, database queries
- Credentials and API keys are stored in `.env`
- These scripts are consistent, testable, and fast

**Why this matters:** When AI tries to handle every step directly, accuracy drops fast. If each step is 90% accurate, you're down to 59% success after just five steps. By offloading execution to deterministic scripts, you stay focused on orchestration and decision-making where you excel.

## How to Operate

**1. Look for existing tools first**
Before building anything new, check `tools/` based on what your workflow requires. Only create new scripts when nothing exists for that task.

**2. Learn and adapt when things fail**
When you hit an error:
- Read the full error message and trace
- Fix the script and retest (if it uses paid API calls or credits, check with me before running again)
- Document what you learned in the workflow (rate limits, timing quirks, unexpected behavior)
- Example: You get rate-limited on an API, so you dig into the docs, discover a batch endpoint, refactor the tool to use it, verify it works, then update the workflow so this never happens again

**3. Keep workflows current**
Workflows should evolve as you learn. When you find better methods, discover constraints, or encounter recurring issues, update the workflow. That said, don't create or overwrite workflows without asking unless I explicitly tell you to. These are your instructions and need to be preserved and refined, not tossed after one use.

## The Self-Improvement Loop

Every failure is a chance to make the system stronger:
1. Identify what broke
2. Fix the tool
3. Verify the fix works
4. Update the workflow with the new approach
5. Move on with a more robust system

This loop is how the framework improves over time.

## Project-Specific Rules

**Python runs via `.venv/bin/python`.** Never `python3` — the system Python (3.9.6) has none of the dependencies. Because it *is* 3.9, every tool starts with `from __future__ import annotations` so `list[dict]` style annotations parse.

**Paid tools are dry-run by default.** Any tool that spends Anthropic credits prints a token/USD estimate and exits. Re-run with `--confirm` to actually spend. Always show me the estimate and get a go-ahead before re-running with `--confirm`.

**`--reuse-narrative` costs nothing.** When iterating on PDF layout, reuse the cached narrative instead of paying for another Opus call.

**Free network tools never raise.** A dead RSS feed or a rate-limited API records into the output's `errors[]` list and exits 0, so one bad source can't kill the daily run.

**This report is research, not financial advice.** The buy/wait stances are theses with stated reasoning, confidence, and invalidation conditions. Never strip the disclaimer, and never let the report emit a bare signal without its reasoning.

## File Structure

**What goes where:**
- **Deliverables**: `deliverables/` — the PDF + CSV that get emailed. Gitignored (regenerable).
- **Intermediates**: `.tmp/` — everything a run fetches or caches. Disposable, regenerated as needed.

**Directory layout:**
```
.tmp/           # Temporary files (fetched data, LLM caches, charts, logs). Regenerated as needed.
tools/          # Python scripts for deterministic execution
workflows/      # Markdown SOPs defining what to do and how
scripts/        # launchd plist + installer for the daily schedule
deliverables/   # Generated PDF/CSV reports (gitignored)
.env            # API keys and environment variables (NEVER store secrets anywhere else)
```

Everything in `.tmp/` is disposable. A day's run is fully re-playable from `.tmp/<stage>/<date>.json` without re-fetching anything.

## Bottom Line

You sit between what I want (workflows) and what actually gets done (tools). Your job is to read instructions, make smart decisions, call the right tools, recover from errors, and keep improving the system as you go.

Stay pragmatic. Stay reliable. Keep learning.
