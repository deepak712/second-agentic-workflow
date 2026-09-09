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

# Palette, mirroring the PDF. Styles are inlined on every element rather than
# put in a <style> block: Gmail and Outlook strip or ignore embedded CSS, and
# no external images are used because clients block them by default.
NAVY, BLUE, GREEN, RED, AMBER = "#12385c", "#2b6cb0", "#12805c", "#c0392b", "#b7791f"
SLATE, WASH, LINE = "#5a6b7b", "#f4f8fb", "#c8d4de"

STANCE_COLOR = {"accumulate": GREEN, "wait": AMBER, "avoid": RED}

_TD = f"padding:7px 9px;border-bottom:1px solid {LINE};font-size:13px;"


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _money(v) -> str:
    if not isinstance(v, (int, float)):
        return "n/a"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"${v / div:,.2f}{unit}"
    return f"${v:,.0f}"


def _price(v) -> str:
    if not isinstance(v, (int, float)):
        return "n/a"
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.4f}"
    return f"${v:.8f}".rstrip("0")


def _pct(v) -> str:
    if not isinstance(v, (int, float)):
        return f"<td style='{_TD}text-align:right;color:{SLATE}'>n/a</td>"
    colour = GREEN if v >= 0 else RED
    return (f"<td style='{_TD}text-align:right;color:{colour};"
            f"font-weight:bold'>{v:+.2f}%</td>")


def _card(label: str, value: str, colour: str, sub: str = "") -> str:
    """One stat tile in the header strip."""
    return (
        f"<td width='33%' style='padding:0 5px'>"
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='background:{WASH};border:1px solid {LINE};border-radius:4px'>"
        f"<tr><td style='padding:11px 12px;text-align:center'>"
        f"<div style='font-size:10px;color:{SLATE};text-transform:uppercase;"
        f"letter-spacing:0.7px;padding-bottom:4px'>{_esc(label)}</div>"
        f"<div style='font-size:19px;font-weight:bold;color:{colour}'>{value}</div>"
        + (f"<div style='font-size:11px;color:{SLATE};padding-top:2px'>{_esc(sub)}</div>"
           if sub else "")
        + "</td></tr></table></td>")


def _h2(text: str) -> str:
    return (f"<h2 style='font-size:15px;color:{NAVY};margin:26px 0 8px 0;"
            f"padding-bottom:5px;border-bottom:2px solid {BLUE}'>{_esc(text)}</h2>")


def build_html(date: str, market: dict, sent: dict, triage: dict,
               calls: list, bottom: str) -> str:
    """A self-contained HTML email body: header, stat tiles, top 10, stances."""
    g = market.get("global", {}) or {}
    cur = sent.get("current") or {}
    cap_chg = g.get("market_cap_change_24h_pct")

    fng = cur.get("value")
    fng_colour = NAVY
    if isinstance(fng, int):
        fng_colour = RED if fng >= 75 or fng <= 25 else AMBER if fng >= 55 else BLUE

    tone = triage.get("net_tone")
    tone_txt = f"{tone:+.2f}" if isinstance(tone, (int, float)) else "n/a"
    tone_colour = (GREEN if isinstance(tone, (int, float)) and tone > 0.05
                   else RED if isinstance(tone, (int, float)) and tone < -0.05 else SLATE)
    counts = triage.get("sentiment_counts", {}) or {}

    p = [
        "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
        "Helvetica,Arial,sans-serif;background:#eef2f6;padding:18px 0;margin:0\">",
        "<table width='640' cellpadding='0' cellspacing='0' border='0' align='center' "
        "style='max-width:640px;margin:0 auto;background:#ffffff;border-radius:6px'>",

        # Header
        f"<tr><td style='background:{NAVY};padding:20px 24px;border-radius:6px 6px 0 0'>"
        f"<div style='font-size:21px;font-weight:bold;color:#ffffff'>"
        f"Daily Crypto Market Report</div>"
        f"<div style='font-size:13px;color:#a8c4dd;padding-top:3px'>{_esc(date)} &middot; "
        f"{triage.get('count', 0)} headlines analysed</div></td></tr>",

        "<tr><td style='padding:18px 19px 0 19px'>",
        "<table width='100%' cellpadding='0' cellspacing='0' border='0'><tr>",
        _card("Market cap", _money(g.get("total_market_cap_usd")),
              GREEN if isinstance(cap_chg, (int, float)) and cap_chg >= 0 else RED,
              f"{cap_chg:+.2f}% 24h" if isinstance(cap_chg, (int, float)) else ""),
        _card("Fear & Greed", str(fng) if fng is not None else "n/a",
              fng_colour, cur.get("label", "")),
        _card("News tone", tone_txt, tone_colour,
              f"{counts.get('positive', 0)} pos / {counts.get('negative', 0)} neg"),
        "</tr></table></td></tr>",
    ]

    # Top 10 by market cap
    p.append("<tr><td style='padding:0 24px'>")
    p.append(_h2("Top 10 by Market Cap"))
    p.append("<table width='100%' cellpadding='0' cellspacing='0' border='0' "
             f"style='border:1px solid {LINE};border-radius:4px'>")
    p.append(f"<tr style='background:{NAVY}'>"
             + "".join(f"<th style='padding:7px 9px;font-size:11px;color:#fff;"
                       f"text-align:{a}'>{h}</th>"
                       for h, a in (("Coin", "left"), ("Price", "right"),
                                    ("24h", "right"), ("7d", "right")))
             + "</tr>")
    for i, c in enumerate(market.get("coins", [])[:10]):
        bg = f"background:{WASH};" if i % 2 else ""
        p.append(
            f"<tr style='{bg}'>"
            f"<td style='{_TD}color:{NAVY};font-weight:bold'>{_esc(c['symbol'].upper())}"
            f" <span style='color:{SLATE};font-weight:normal'>{_esc(c.get('name', ''))}</span></td>"
            f"<td style='{_TD}text-align:right'>{_price(c.get('price_usd'))}</td>"
            + _pct(c.get("chg_24h")) + _pct(c.get("chg_7d")) + "</tr>")
    p.append("</table></td></tr>")

    # Buy or wait
    if calls:
        tally: dict = {}
        for c in calls:
            tally[c["stance"]] = tally.get(c["stance"], 0) + 1
        chips = " ".join(
            f"<span style='display:inline-block;background:"
            f"{STANCE_COLOR.get(k.lower(), SLATE)};color:#fff;font-size:11px;"
            f"font-weight:bold;padding:3px 9px;border-radius:10px;margin-right:5px'>"
            f"{_esc(k)} {v}</span>"
            for k, v in sorted(tally.items(), key=lambda kv: -kv[1]))

        p.append("<tr><td style='padding:0 24px'>")
        p.append(_h2("Buy or Wait"))
        p.append(f"<div style='padding-bottom:9px'>{chips}</div>")
        p.append("<table width='100%' cellpadding='0' cellspacing='0' border='0' "
                 f"style='border:1px solid {LINE};border-radius:4px'>")
        for i, c in enumerate(calls):
            bg = f"background:{WASH};" if i % 2 else ""
            colour = STANCE_COLOR.get(c["stance"].lower(), SLATE)
            why = c.get("why", "")
            why = why[:110].rstrip() + ("..." if len(why) > 110 else "")
            p.append(
                f"<tr style='{bg}'>"
                f"<td style='{_TD}width:58px;color:{NAVY};font-weight:bold'>"
                f"{_esc(c['symbol'])}</td>"
                f"<td style='{_TD}width:92px;color:{colour};font-weight:bold'>"
                f"{_esc(c['stance'])}<div style='font-size:10px;color:{SLATE};"
                f"font-weight:normal'>{_esc(c.get('confidence', ''))}</div></td>"
                f"<td style='{_TD}color:#33414f'>{_esc(why)}</td></tr>")
        p.append("</table></td></tr>")

    # Bottom line
    if bottom:
        p.append("<tr><td style='padding:0 24px'>")
        p.append(_h2("Bottom Line"))
        p.append(f"<div style='background:{WASH};border-left:4px solid {BLUE};"
                 f"padding:12px 14px;font-size:13.5px;line-height:1.55;color:#26313d'>"
                 f"{_esc(bottom).replace(chr(10), '<br/>')}</div></td></tr>")

    p.append(
        f"<tr><td style='padding:20px 24px 8px 24px'>"
        f"<div style='font-size:12.5px;color:{SLATE}'>The full report &mdash; charts, "
        f"the news breakdown by importance, watch levels and every invalidation "
        f"condition &mdash; is attached as a PDF.</div></td></tr>"
        f"<tr><td style='padding:10px 24px 22px 24px'>"
        f"<div style='background:#fffbe6;border:1px solid #d4b106;color:#614700;"
        f"font-size:11px;line-height:1.45;padding:9px 11px;border-radius:4px'>"
        f"<b>Not financial advice.</b> Automatically generated research produced by a "
        f"language model from public market data and news headlines. It may contain "
        f"errors, omissions or stale information. Stances are theses with stated "
        f"invalidation conditions &mdash; not trade signals or recommendations. Crypto "
        f"assets are volatile and you can lose your entire capital.</div>"
        f"</td></tr></table></div>")
    return "".join(p)


def _recipient() -> str:
    return _lib.require_env("REPORT_TO_EMAIL")


def send_smtp(subject: str, body: str, attachment: pathlib.Path | None,
              html: str | None = None) -> None:
    host = _lib.optional_env("SMTP_HOST", "smtp.gmail.com")
    port = int(_lib.optional_env("SMTP_PORT", "587"))
    user = _lib.require_env("SMTP_USER")
    password = _lib.require_env("SMTP_APP_PASSWORD")
    to = _recipient()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    # multipart/alternative: clients that refuse HTML still get readable text.
    msg.set_content(body + FOOTER)
    if html:
        msg.add_alternative(html, subtype="html")

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


def send_resend(subject: str, body: str, attachment: pathlib.Path | None,
                html: str | None = None) -> None:
    import _lib  # noqa: F811  (kept before requests; see _lib docstring)
    import requests

    key = _lib.require_env("RESEND_API_KEY")
    sender = _lib.optional_env("RESEND_FROM", "onboarding@resend.dev")
    to = _recipient()

    payload = {"from": sender, "to": [to], "subject": subject, "text": body + FOOTER}
    if html:
        payload["html"] = html
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
         provider: str = "smtp", html: str | None = None) -> None:
    (send_resend if provider == "resend" else send_smtp)(subject, body, attachment, html)


def compose(date: str) -> tuple:
    """Build (subject, text_body, html_body, pdf) for a run date.

    Every data source is optional: a missing stage degrades the email rather
    than failing it, because a plain report beats no report.
    """
    import json

    import _report

    pdf = _lib.DELIVERABLES / f"crypto_report_{date}.pdf"
    if not pdf.exists():
        raise SystemExit(f"No report at {pdf} - run tools/build_report.py --confirm first.")

    def load(stage):
        try:
            return _lib.read_tmp(_lib.stage_path(stage, date))
        except (FileNotFoundError, ValueError):
            return {}

    market, sent, triage = load("market"), load("sentiment"), load("triage")

    calls = []
    calls_path = _lib.HISTORY / "calls" / f"{date}.json"
    if calls_path.exists():
        try:
            calls = json.loads(calls_path.read_text())
        except ValueError:
            calls = []

    g, cur = market.get("global", {}) or {}, (sent.get("current") or {})
    chg, cap = g.get("market_cap_change_24h_pct"), g.get("total_market_cap_usd")
    bits = []
    if isinstance(cap, (int, float)):
        bits.append(f"cap ${cap / 1e12:.2f}T"
                    + (f" {chg:+.1f}%" if isinstance(chg, (int, float)) else ""))
    if cur.get("value") is not None:
        bits.append(f"F&G {cur['value']} ({cur.get('label')})")
    headline = " | ".join(bits)

    bottom = ""
    cache = _report.cache_path(date)
    if cache.exists():
        bottom = _report.extract_section(cache.read_text(), "Bottom Line")

    subject = f"Crypto Report {date}" + (f" - {headline}" if headline else "")
    text = (f"Daily crypto market report for {date}.\n"
            + (f"{headline}\n" if headline else "")
            + ("\nBOTTOM LINE\n" + bottom + "\n" if bottom else "")
            + "\nFull report attached (charts, news breakdown, and the "
              "coin-by-coin buy/wait table with invalidation levels).")
    html = build_html(date, market, sent, triage, calls, bottom)
    return subject, text, html, pdf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["smtp", "resend"], default="smtp")
    ap.add_argument("--date", default=None, help="run date to send (YYYY-MM-DD)")
    ap.add_argument("--attach", default=None, help="override the PDF path to attach")
    ap.add_argument("--test", action="store_true",
                    help="send a short test email with no attachment, to verify credentials")
    ap.add_argument("--preview", action="store_true",
                    help="write the HTML body to .tmp/ and send nothing (free layout check)")
    ap.add_argument("--subject", default=None, help="override the subject line")
    args = ap.parse_args()
    date = args.date or _lib.today()

    if args.test:
        send(args.subject or f"Crypto report pipeline test ({date})",
             "This is a test from tools/send_email.py.\n"
             "If you are reading this, credentials and delivery are working.",
             None, args.provider)
        return

    subject, text, html, pdf = compose(date)
    if args.attach:
        pdf = pathlib.Path(args.attach)
        if not pdf.exists():
            raise SystemExit(f"Attachment not found: {pdf}")
    if args.preview:
        out = _lib.TMP / f"email_preview_{date}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
        print(f"preview -> {out}\n(nothing sent; open it in a browser to check the layout)")
        return
    send(args.subject or subject, text, pdf, args.provider, html)
    print(f"  attached {pdf.name} ({pdf.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
