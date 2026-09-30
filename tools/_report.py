"""Shared PDF-report plumbing: stylesheet, markdown->PDF, and the
dry-run / --confirm / --reuse-narrative gate used by the report tools."""
from __future__ import annotations

import pathlib

import markdown as md
from xhtml2pdf import pisa

import _lib
import _llm

# Palette. Kept in one place so the charts and the PDF stay in sync.
NAVY = "#12385c"      # headings
BLUE = "#2b6cb0"      # accents, links, BTC line
GREEN = "#12805c"     # gains, Accumulate
RED = "#c0392b"       # losses, Avoid
AMBER = "#b7791f"     # Wait
SLATE = "#5a6b7b"     # secondary text

CSS = """
@page { size: A4; margin: 1.5cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1f2933; line-height: 1.45; }

h1 { font-size: 20pt; margin: 0 0 3pt 0; color: #12385c; }
h2 { font-size: 13.5pt; margin: 15pt 0 5pt 0; color: #12385c;
     border-bottom: 2px solid #2b6cb0; padding-bottom: 3pt; }
h3 { font-size: 11pt; margin: 10pt 0 2pt 0; color: #2b6cb0; }
ul { margin: 4pt 0 8pt 0; }
li { margin-bottom: 3.5pt; }
strong { color: #12385c; }

.meta { color: #5a6b7b; font-size: 9pt; margin-bottom: 4pt;
        border-left: 3px solid #2b6cb0; padding: 3pt 0 3pt 7pt; background: #f4f8fb; }

table { border-collapse: collapse; width: 100%; margin: 8pt 0 4pt 0; font-size: 8pt;
        table-layout: fixed; }
th, td { border: 1px solid #c8d4de; padding: 3.5pt 4pt; text-align: left;
         vertical-align: top; word-wrap: break-word; }
th { background: #12385c; color: #ffffff; font-size: 8pt; font-weight: bold; }
tr.alt td { background: #f4f8fb; }
td.co { font-weight: bold; color: #12385c; }
td.num { text-align: right; }
td.up { text-align: right; color: #12805c; font-weight: bold; }
td.down { text-align: right; color: #c0392b; font-weight: bold; }

/* Buy-or-wait stances, colour-coded so the table is scannable at a glance. */
.s-accumulate { color: #12805c; font-weight: bold; }
.s-wait       { color: #b7791f; font-weight: bold; }
.s-avoid      { color: #c0392b; font-weight: bold; }

/* News importance badges, keyed to the triage impact score. */
.badge      { font-size: 8pt; font-weight: bold; padding: 2pt 5pt; }
.b-major    { background-color: #c0392b; color: #ffffff; }
.b-important{ background-color: #d35400; color: #ffffff; }
.b-notable  { background-color: #2b6cb0; color: #ffffff; }
.b-minor    { background-color: #dfe6ec; color: #44515e; }

.section-table { margin: 4pt 0 10pt 0; }
.tbl-title { font-size: 10.5pt; font-weight: bold; color: #12385c; margin: 12pt 0 3pt 0; }

.charts { margin: 6pt 0 10pt 0; }
.chart { margin-bottom: 8pt; }
.chart-cap { color: #5a6b7b; font-size: 8pt; margin: 0 0 6pt 0; font-style: italic; }

.disclaimer { margin-top: 14pt; padding: 7pt 9pt; border: 1px solid #d4b106; background: #fffbe6;
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
                      *, confirm: bool, reuse: bool, max_output_tokens: int = 4000,
                      effort: str | None = None):
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
    text = _llm.complete_text(model, system, user, max_tokens=max_output_tokens,
                              effort=effort)
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


def _pct_cell(v) -> str:
    """A percentage table cell, green when up and red when down."""
    if not isinstance(v, (int, float)):
        return "<td class='num'>n/a</td>"
    cls = "up" if v >= 0 else "down"
    return f"<td class='{cls}'>{v:+.2f}%</td>"


def market_table_html(coins: list, n: int = 10, title: str = "") -> str:
    """Top-N coins by market cap as a coloured HTML table.

    Rendered straight from the fetched data rather than asked of the model, so the
    prices in the report are always present and always exactly what was fetched.
    """
    rows = coins[:n]
    if not rows:
        return ""
    out = []
    if title:
        out.append(f"<div class='tbl-title'>{esc(title)}</div>")
    out.append("<table class='section-table'>"
               "<tr><th width='7%'>#</th><th width='23%'>Coin</th>"
               "<th width='16%'>Price</th><th width='13%'>24h</th>"
               "<th width='13%'>7d</th><th width='13%'>30d</th>"
               "<th width='15%'>Market cap</th></tr>")
    for i, c in enumerate(rows):
        alt = " class='alt'" if i % 2 else ""
        name = f"{c.get('symbol', '').upper()} <span style='color:#5a6b7b'>{esc(c.get('name', ''))}</span>"
        out.append(
            f"<tr{alt}><td class='num'>{c.get('rank') or i + 1}</td>"
            f"<td class='co'>{name}</td>"
            f"<td class='num'>{_price(c.get('price_usd'))}</td>"
            + _pct_cell(c.get("chg_24h"))
            + _pct_cell(c.get("chg_7d"))
            + _pct_cell(c.get("chg_30d"))
            + f"<td class='num'>{_money(c.get('market_cap_usd'))}</td></tr>")
    out.append("</table>")
    return "".join(out)


_STANCES = ("Accumulate", "Wait", "Avoid")


# Coin | Stance | Confidence | Why | Invalidation. Without these the markdown
# renderer gives all five columns equal width, which squeezes the two prose
# columns into tall, unreadable slivers.
STANCE_WIDTHS = ("12%", "11%", "11%", "36%", "30%")


def style_model_tables(html: str) -> str:
    """Colour the stance words and widen the prose columns of the stance table.

    Runs on the HTML the markdown renderer produced, so the model stays free to
    write an ordinary markdown table and the presentation is fixed up here.
    """
    import re

    def colour(m):
        word = m.group(2)
        return f"{m.group(1)}<span class='s-{word.lower()}'>{word}</span>{m.group(3)}"

    html = re.sub(r"(<td[^>]*>)\s*(%s)\s*(</td>)" % "|".join(_STANCES),
                  colour, html, flags=re.I)

    # News importance tags: the model writes a literal [MAJOR] / [IMPORTANT] /
    # [NOTABLE] / [MINOR] prefix on each news bullet, turned into a badge here.
    html = re.sub(
        r"\[(MAJOR|IMPORTANT|NOTABLE|MINOR)\]\s*",
        lambda m: f"<span class='badge b-{m.group(1).lower()}'>{m.group(1)}</span> ",
        html)

    def widen(m):
        head = m.group(0)
        if "stance" not in head.lower():
            return head
        i = [0]

        def add_width(hm):
            w = STANCE_WIDTHS[i[0]] if i[0] < len(STANCE_WIDTHS) else None
            i[0] += 1
            return f"<th width='{w}'>" if w else hm.group(0)

        return re.sub(r"<th>", add_width, head)

    # Only the header row of each table is rewritten; widths propagate down.
    return re.sub(r"<tr>\s*(?:<th>.*?</th>\s*)+</tr>", widen, html,
                  flags=re.S | re.I)


def render_pdf(out_base: pathlib.Path, title_html: str, meta_html: str,
               body_markdown: str, *, prepend_html: str = "",
               append_html: str = "") -> None:
    """Write `<out_base>.pdf` from a title, a meta line, optional raw HTML
    (e.g. charts) and a markdown body. Raises on render error."""
    out_base.parent.mkdir(parents=True, exist_ok=True)
    body_markdown = strip_leading_title(body_markdown)
    rendered = style_model_tables(md.markdown(body_markdown, extensions=["tables"]))
    body = (
        f"<h1>{title_html}</h1>"
        f"<div class='meta'>{meta_html}</div>"
        f"{prepend_html}"
        f"{rendered}"
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
