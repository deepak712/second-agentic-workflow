#!/usr/bin/env python3
"""Email the daily report PDF. Free (no LLM calls).

Two backends. `smtp` (default) uses stdlib smtplib - for Gmail this needs an App
Password, not your account password (https://myaccount.google.com/apppasswords,
requires 2FA). `resend` posts over HTTPS instead, for environments that block
outbound port 587 such as a cloud sandbox.

    python tools/send_email.py --test                    # verify credentials only
    python tools/send_email.py                           # send today's report
    python tools/send_email.py --provider resend --date 2026-09-09

Importable:
    from send_email import send
    send(subject="...", body="...", attachment=pathlib.Path("x.pdf"))
"""
from __future__ import annotations

import argparse
import base64
import mimetypes
import pathlib
import smtplib
import sys
from email.message import EmailMessage

import _lib

FOOTER = ("\n\n---\nAutomatically generated research. Not financial advice; "
          "stances are theses with stated invalidation conditions, not trade signals. "
          "Crypto assets are volatile and you can lose your entire capital.\n")


def _recipient() -> str:
    return _lib.require_env("REPORT_TO_EMAIL")


def send_smtp(subject: str, body: str, attachment: pathlib.Path | None) -> None:
    host = _lib.optional_env("SMTP_HOST", "smtp.gmail.com")
    port = int(_lib.optional_env("SMTP_PORT", "587"))
    user = _lib.require_env("SMTP_USER")
    password = _lib.require_env("SMTP_APP_PASSWORD")
    to = _recipient()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body + FOOTER)

    if attachment:
        ctype, _ = mimetypes.guess_type(attachment.name)
        maintype, _, subtype = (ctype or "application/pdf").partition("/")
        msg.add_attachment(attachment.read_bytes(), maintype=maintype,
                           subtype=subtype, filename=attachment.name)

    try:
        with smtplib.SMTP(host, port, timeout=45) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise SystemExit(
            f"SMTP auth rejected: {exc}\n"
            "Gmail requires an App Password (not your account password), and the "
            "account must have 2FA enabled. Generate one at "
            "https://myaccount.google.com/apppasswords and set SMTP_APP_PASSWORD in .env."
        )
    except OSError as exc:
        raise SystemExit(
            f"Could not reach {host}:{port} ({exc}).\n"
            "If this is a sandboxed or cloud environment, outbound port 587 is "
            "probably blocked - re-run with --provider resend."
        )
    print(f"email -> {to} via smtp ({host}:{port})")


def send_resend(subject: str, body: str, attachment: pathlib.Path | None) -> None:
    import _lib  # noqa: F811  (kept before requests; see _lib docstring)
    import requests

    key = _lib.require_env("RESEND_API_KEY")
    sender = _lib.optional_env("RESEND_FROM", "onboarding@resend.dev")
    to = _recipient()

    payload = {"from": sender, "to": [to], "subject": subject, "text": body + FOOTER}
    if attachment:
        payload["attachments"] = [{
            "filename": attachment.name,
            "content": base64.b64encode(attachment.read_bytes()).decode(),
        }]
    r = requests.post("https://api.resend.com/emails", json=payload,
                      headers={"Authorization": f"Bearer {key}"}, timeout=45)
    if r.status_code >= 300:
        raise SystemExit(f"Resend rejected the send (HTTP {r.status_code}): {r.text}")
    print(f"email -> {to} via resend (from {sender})")


def send(subject: str, body: str, attachment: pathlib.Path | None = None,
         provider: str = "smtp") -> None:
    (send_resend if provider == "resend" else send_smtp)(subject, body, attachment)


def compose(date: str) -> tuple:
    """Build (subject, body, pdf) for a run date from its cached narrative + data."""
    import _report

    pdf = _lib.DELIVERABLES / f"crypto_report_{date}.pdf"
    if not pdf.exists():
        raise SystemExit(f"No report at {pdf} - run tools/build_report.py --confirm first.")

    headline = ""
    try:
        market = _lib.read_tmp(_lib.stage_path("market", date))
        sent = _lib.read_tmp(_lib.stage_path("sentiment", date))
        g, cur = market.get("global", {}), (sent.get("current") or {})
        chg = g.get("market_cap_change_24h_pct")
        cap = g.get("total_market_cap_usd")
        bits = []
        if isinstance(cap, (int, float)):
            bits.append(f"cap ${cap / 1e12:.2f}T" + (f" {chg:+.1f}%" if isinstance(chg, (int, float)) else ""))
        if cur.get("value") is not None:
            bits.append(f"F&G {cur['value']} ({cur.get('label')})")
        headline = " | ".join(bits)
    except FileNotFoundError:
        pass

    bottom = ""
    cache = _report.cache_path(date)
    if cache.exists():
        bottom = _report.extract_section(cache.read_text(), "Bottom Line")

    subject = f"Crypto Report {date}" + (f" - {headline}" if headline else "")
    body = (f"Daily crypto market report for {date}.\n"
            + (f"{headline}\n" if headline else "")
            + ("\nBOTTOM LINE\n" + bottom + "\n" if bottom else "")
            + "\nFull report attached (charts, news breakdown, and the "
              "coin-by-coin buy/wait table with invalidation levels).")
    return subject, body, pdf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["smtp", "resend"], default="smtp")
    ap.add_argument("--date", default=None, help="run date to send (YYYY-MM-DD)")
    ap.add_argument("--attach", default=None, help="override the PDF path to attach")
    ap.add_argument("--test", action="store_true",
                    help="send a short test email with no attachment, to verify credentials")
    ap.add_argument("--subject", default=None, help="override the subject line")
    args = ap.parse_args()
    date = args.date or _lib.today()

    if args.test:
        send(args.subject or f"Crypto report pipeline test ({date})",
             "This is a test from tools/send_email.py.\n"
             "If you are reading this, credentials and delivery are working.",
             None, args.provider)
        return

    subject, body, pdf = compose(date)
    if args.attach:
        pdf = pathlib.Path(args.attach)
        if not pdf.exists():
            raise SystemExit(f"Attachment not found: {pdf}")
    send(args.subject or subject, body, pdf, args.provider)
    print(f"  attached {pdf.name} ({pdf.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
