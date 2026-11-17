"""Turn the run's documents (Markdown, JSON) into simple readable HTML pages for the founder.

The files on disk stay as they are (they are the committed outputs); this is only how the web app shows them.
Everything is escaped first, so no document can inject markup or scripts."""

from __future__ import annotations

import html
import json
import re

from .. import labels

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>
:root {{ --bg:#f6f5f1; --panel:#fff; --ink:#1f1f1c; --muted:#686862; --line:#e4e2db; --accent:#2f5d50; --soft:#f0eee8; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#151514; --panel:#1e1e1c; --ink:#ecebe7; --muted:#a3a29b;
  --line:#34332f; --accent:#86c2ae; --soft:#262623; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width:760px; margin:24px auto; padding:24px 28px; background:var(--panel); border:1px solid var(--line);
  border-radius:14px; }}
@media (max-width:640px) {{ main {{ margin:0; border-radius:0; border:0; padding:20px 16px; }} }}
h1 {{ font-size:26px; line-height:1.25; margin:0 0 12px; }} h2 {{ font-size:19px; margin:26px 0 8px; }}
h3 {{ font-size:16px; margin:18px 0 6px; }} a {{ color:var(--accent); }}
code {{ background:var(--soft); padding:1px 5px; border-radius:4px; font-size:14px; }}
dl {{ margin:0; }} dt {{ font-weight:700; margin-top:10px; }} dd {{ margin:2px 0 0 0; }}
.box {{ border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin:10px 0; }}
.muted {{ color:var(--muted); font-size:14px; }}
</style></head><body><main>{body}</main></body></html>"""


def _inline(text: str) -> str:
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
    t = re.sub(r"(?<![\"'>])(https?://[^\s<)]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', t)
    return t


def markdown_html(md: str) -> str:
    """A small Markdown subset: headings, lists, paragraphs, bold/italic/code/links. Code blocks become readable
    lists when they hold JSON, otherwise plain preformatted text."""
    out: list[str] = []
    para: list[str] = []
    list_open = False
    lines = md.splitlines()
    i = 0

    def flush():
        nonlocal para
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para = []

    def close_list():
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush(); close_list()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            raw = "\n".join(block)
            try:
                out.append(value_html(json.loads(raw)))
            except ValueError:
                out.append(f'<pre class="box">{html.escape(raw)}</pre>')
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush(); close_list()
            level = min(len(m[1]), 3)
            out.append(f"<h{level}>{_inline(m[2])}</h{level}>")
        elif re.match(r"^\s*[-*]\s+", line):
            flush()
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{_inline(re.sub(r'^\s*[-*]\s+', '', line))}</li>")
        elif not line.strip():
            flush(); close_list()
        else:
            close_list()
            para.append(line.strip())
        i += 1
    flush(); close_list()
    return "\n".join(out)


def value_html(v, depth: int = 0) -> str:
    if isinstance(v, dict):
        if depth > 3:
            return html.escape(json.dumps(v, ensure_ascii=False))
        shown = {k: x for k, x in v.items() if not str(k).startswith("raw_")}
        hidden = len(v) - len(shown)
        return ("<dl>" + "".join(f"<dt>{html.escape(labels.human(k))}</dt><dd>{value_html(x, depth + 1)}</dd>"
                                 for k, x in shown.items()) + "</dl>"
                + ('<p class="muted">The raw numbers are kept in the file for reference.</p>' if hidden else ""))
    if isinstance(v, list):
        if not v:
            return '<span class="muted">none</span>'
        if all(isinstance(x, dict) for x in v):
            return "".join(f'<div class="box">{value_html(x, depth + 1)}</div>' for x in v)
        return "<ul>" + "".join(f"<li>{value_html(x, depth + 1)}</li>" for x in v) + "</ul>"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if v is None or v == "":
        return '<span class="muted">not available</span>'
    return _inline(str(v))


def document_page(name: str, content: str) -> str:
    title = labels.doc_label(name)
    if name.endswith(".json"):
        try:
            body = f"<h1>{html.escape(title)}</h1>" + value_html(json.loads(content))
        except ValueError:
            body = f"<h1>{html.escape(title)}</h1><pre>{html.escape(content)}</pre>"
    else:
        body = markdown_html(content)
        if not body.lstrip().startswith("<h1>"):
            body = f"<h1>{html.escape(title)}</h1>" + body
    return PAGE.format(title=html.escape(title), body=body)
