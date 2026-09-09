"""Shared PDF-report plumbing: stylesheet, markdown->PDF, and the
dry-run / --confirm / --reuse-narrative gate used by the report tools."""
from __future__ import annotations

import pathlib

import markdown as md
from xhtml2pdf import pisa

import _lib
import _llm

CSS = """
@page { size: A4; margin: 1.6cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.45; }
h1 { font-size: 19pt; margin: 0 0 2pt 0; }
h2 { font-size: 13pt; margin: 16pt 0 4pt 0; border-bottom: 1px solid #ccc; padding-bottom: 2pt; }
h3 { font-size: 11pt; margin: 10pt 0 2pt 0; }
ul { margin: 4pt 0 8pt 0; }
li { margin-bottom: 3pt; }
.meta { color: #666; font-size: 9pt; margin-bottom: 10pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 4pt 0; font-size: 8pt; table-layout: fixed; }
th, td { border: 1px solid #bbb; padding: 3pt 4pt; text-align: left; vertical-align: top; word-wrap: break-word; }
th { background: #f0f0f0; font-size: 8pt; }
td.co { font-weight: bold; }
.charts { margin: 6pt 0 10pt 0; }
.chart { margin-bottom: 8pt; }
.chart-cap { color: #666; font-size: 8pt; margin: 0 0 6pt 0; }
.disclaimer { margin-top: 14pt; padding: 6pt 8pt; border: 1px solid #d4b106; background: #fffbe6;
              color: #614700; font-size: 8pt; line-height: 1.35; }
"""

DISCLAIMER = (
    "<div class='disclaimer'><b>Not financial advice.</b> This report is automatically "
    "generated research produced by a language model from public market data and news "
    "headlines. It may contain errors, omissions or stale information. Stances are "
    "theses with stated reasoning and invalidation conditions &mdash; not trade signals, "
    "recommendations, or solicitations. Crypto assets are volatile and you can lose your "
    "entire capital. Do your own research and consider consulting a licensed financial "
    "adviser before making any investment decision.</div>"
)


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cache_path(slug: str, kind: str = "narrative") -> pathlib.Path:
    return _lib.TMP / f"report_{kind}_{slug}.md"


def resolve_narrative(cache: pathlib.Path, model: str, system: str, user: str,
                      *, confirm: bool, reuse: bool, max_output_tokens: int = 4000):
    """Return the narrative markdown, or None if this was a dry run (estimate printed).

    reuse  -> load the cached narrative, no API call
    confirm-> call the model and cache the result
    neither-> print a cost estimate and return None
    """
    if reuse:
        if not cache.exists():
            raise SystemExit(f"No cached narrative at {cache} - run once with --confirm first.")
        print(f"(reusing cached narrative from {cache})")
        return cache.read_text()
    if not confirm:
        _llm.print_estimate(_llm.estimate(model, system, user, max_output_tokens=max_output_tokens))
        return None
    text = _llm.complete_text(model, system, user, max_tokens=max_output_tokens)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    return text


def strip_leading_title(markdown_text: str) -> str:
    """Drop a leading '# ...' heading so it doesn't duplicate the page <h1>."""
    lines = markdown_text.lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines)


def charts_html(charts: list) -> str:
    """Build an <img> block for chart PNGs.

    xhtml2pdf resolves plain local filesystem paths, so absolute paths are passed
    straight through as src. Missing files are skipped rather than breaking the
    render - a chart failure must not cost us the whole report.
    """
    if not charts:
        return ""
    parts = ["<div class='charts'>"]
    for item in charts:
        path, caption = (item if isinstance(item, (tuple, list)) else (item, ""))
        p = pathlib.Path(path)
        if not p.exists():
            continue
        parts.append(f"<div class='chart'><img src='{p.resolve()}' width='460' />")
        if caption:
            parts.append(f"<p class='chart-cap'>{esc(caption)}</p>")
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def render_pdf(out_base: pathlib.Path, title_html: str, meta_html: str,
               body_markdown: str, *, prepend_html: str = "",
               append_html: str = "") -> None:
    """Write `<out_base>.pdf` from a title, a meta line, optional raw HTML
    (e.g. charts) and a markdown body. Raises on render error."""
    out_base.parent.mkdir(parents=True, exist_ok=True)
    body_markdown = strip_leading_title(body_markdown)
    body = (
        f"<h1>{title_html}</h1>"
        f"<div class='meta'>{meta_html}</div>"
        f"{prepend_html}"
        f"{md.markdown(body_markdown, extensions=['tables'])}"
        f"{append_html}"
    )
    html = f"<html><head><style>{CSS}</style></head><body>{body}</body></html>"
    pdf_path = out_base.with_suffix(".pdf")
    with open(pdf_path, "wb") as fh:
        result = pisa.CreatePDF(html, dest=fh)
    if result.err:
        out_base.with_suffix(".debug.html").write_text(html)
        raise SystemExit(f"PDF generation failed ({result.err} errors); HTML dumped next to it.")


def extract_section(markdown_text: str, heading: str) -> str:
    """Return the plain-text body of one '## <heading>' section, markdown stripped.

    Used to lift 'Bottom Line' into the email body so the mail is useful without
    opening the attachment.
    """
    import re

    lines = markdown_text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("## "):
            if capturing:
                break
            capturing = heading.lower() in line[3:].strip().lower()
            continue
        if capturing:
            out.append(line)
    text = "\n".join(out).strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()
