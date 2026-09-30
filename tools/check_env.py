#!/usr/bin/env python3
"""Verify the credentials in .env actually work. Prints only masked values.

Free: the Anthropic check uses count_tokens (no generation, no charge) and the
SMTP check logs in and disconnects without sending mail.

    python tools/check_env.py
    python tools/check_env.py --fix-spaces   # strip spaces from a pasted App Password
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import smtplib
import stat
import sys

import _lib

OK, BAD, WARN = "  OK  ", " FAIL ", " WARN "


def mask(value: str) -> str:
    """Show only enough to identify a value, never enough to use it."""
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return value[0] + "*" * (len(value) - 1)
    return f"{value[:6]}...{value[-4:]} ({len(value)} chars)"


def check_permissions() -> bool:
    env = _lib.ROOT / ".env"
    if not env.exists():
        print(f"[{BAD}] .env does not exist. Run: cp .env.example .env")
        return False
    mode = stat.S_IMODE(env.stat().st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(f"[{WARN}] .env is readable by other users (mode {mode:o}). "
              f"Fix with: chmod 600 .env")
    else:
        print(f"[{OK}] .env permissions are {mode:o} (owner-only)")
    return True


def fix_spaces() -> None:
    """Rewrite SMTP_APP_PASSWORD without spaces, as Google displays it grouped."""
    env = _lib.ROOT / ".env"
    text = env.read_text()
    new, n = re.subn(
        r"^(SMTP_APP_PASSWORD=)(.*)$",
        lambda m: m.group(1) + m.group(2).replace(" ", ""),
        text, flags=re.M)
    if n and new != text:
        env.write_text(new)
        print(f"[{OK}] stripped spaces from SMTP_APP_PASSWORD")
    else:
        print(f"[{OK}] SMTP_APP_PASSWORD needed no change")


def check_anthropic() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print(f"[{BAD}] ANTHROPIC_API_KEY is empty - paste it into .env")
        return False
    print(f"[{OK}] ANTHROPIC_API_KEY present: {mask(key)}")
    if not key.startswith("sk-ant-"):
        print(f"[{WARN}] that does not look like an Anthropic key "
              f"(they normally start with 'sk-ant-')")
    try:
        import _llm
        counted = _llm.client().messages.count_tokens(
            model=_llm.HAIKU, messages=[{"role": "user", "content": "ping"}])
        print(f"[{OK}] Anthropic API reachable and key accepted "
              f"(count_tokens returned {counted.input_tokens} tokens, no charge)")
        return True
    except Exception as exc:
        print(f"[{BAD}] Anthropic rejected the key: {type(exc).__name__}: "
              f"{str(exc)[:200]}")
        return False


def check_smtp() -> bool:
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_APP_PASSWORD", "")
    to = os.environ.get("REPORT_TO_EMAIL", "")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT") or 587)

    if not to:
        print(f"[{BAD}] REPORT_TO_EMAIL is empty - the report has nowhere to go")
    else:
        print(f"[{OK}] REPORT_TO_EMAIL: {to}")
    if not user:
        print(f"[{BAD}] SMTP_USER is empty")
        return False
    if not password:
        print(f"[{BAD}] SMTP_APP_PASSWORD is empty - generate one at "
              f"https://myaccount.google.com/apppasswords")
        return False

    print(f"[{OK}] SMTP_USER: {user}")
    print(f"[{OK}] SMTP_APP_PASSWORD present: {mask(password)}")
    if " " in password:
        print(f"[{WARN}] it contains spaces. Google shows the password grouped, "
              f"but it must be stored without them. Run: "
              f"tools/check_env.py --fix-spaces")
    if len(password.replace(" ", "")) != 16:
        print(f"[{WARN}] Gmail App Passwords are 16 characters; this one is "
              f"{len(password.replace(' ', ''))}. If login fails, that is why.")

    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, password.replace(" ", ""))
        print(f"[{OK}] SMTP login succeeded at {host}:{port} (nothing sent)")
        return True
    except smtplib.SMTPAuthenticationError:
        print(f"[{BAD}] {host} rejected the login. Use a 16-char App Password, "
              f"not your account password, and make sure 2FA is on.")
    except OSError as exc:
        print(f"[{BAD}] could not reach {host}:{port} ({exc}). If this machine "
              f"blocks port 587, use --provider resend instead.")
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix-spaces", action="store_true",
                    help="strip spaces from SMTP_APP_PASSWORD in .env, then re-check")
    args = ap.parse_args()

    if not check_permissions():
        raise SystemExit(1)
    if args.fix_spaces:
        fix_spaces()

    _lib.load_env()
    print()
    anthropic_ok = check_anthropic()
    print()
    smtp_ok = check_smtp()

    print()
    if anthropic_ok and smtp_ok:
        print("All credentials verified. Next:")
        print("  .venv/bin/python tools/triage_news.py          # cost estimate")
        print("  .venv/bin/python tools/run_daily.py --confirm  # full run")
    else:
        print("Some checks failed - fix the items marked FAIL in .env and re-run.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
