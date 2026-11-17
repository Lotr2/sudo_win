"""Turn an agent's JSON into the standard output text (key content first). HTML pages are template-rendered."""
from __future__ import annotations

import html

from core.conventions import ASSUMPTION, MOCKED, PHASES, Step

_SKIP = {"title", "summary", "sections", "claims", "copy"}


def _header(step: Step, idea: str, meta: dict) -> str:
    phase_name = PHASES[step.phase][0]
    return (f"# Phase {step.phase} ({phase_name}), step {step.num}: {step.title} [{idea}]\n\n"
            f"_Agent: {meta.get('agent')} · Model: {meta.get('model')} · Run: {meta.get('run_id')} · "
            f"Variant: {meta.get('variant')}_\n\n")


def claims_md(claims: list[dict]) -> str:
    if not claims:
        return ""
    lines = ["## Claims and sources", ""]
    for c in claims:
        if c.get("source_url"):
            lines.append(f"- {c['text']} — source: {c['source_url']}")
        else:
            lines.append(f"- {c['text']} — **{ASSUMPTION}**")
    return "\n".join(lines) + "\n"


def _extra_md(data: dict) -> str:
    out = []
    for k, v in data.items():
        if k in _SKIP or v in (None, "", [], {}):
            continue
        out.append(f"### {k.replace('_', ' ').capitalize()}\n")
        if isinstance(v, list):
            for item in v:
                out.append(f"- {', '.join(f'{a}: {b}' for a, b in item.items()) if isinstance(item, dict) else item}")
        elif isinstance(v, dict):
            out += [f"- {a}: {b}" for a, b in v.items()]
        else:
            out.append(str(v))
        out.append("")
    return ("## Details\n\n" + "\n".join(out)) if out else ""


def render_md(step: Step, idea: str, data: dict, claims: list[dict], meta: dict, extra: str = "") -> str:
    parts = [_header(step, idea, meta), f"**{data.get('title', step.title)}**\n\n{data.get('summary', '')}\n"]
    for s in data.get("sections") or []:
        parts.append(f"## {s.get('heading', '')}\n\n{s.get('body', '')}\n")
    if data.get("copy"):
        parts.append(_extra_md({"copy": data["copy"]}))
    parts.append(_extra_md(data))
    parts.append(claims_md(claims))
    if extra:
        parts.append(extra)
    return "\n".join(p for p in parts if p).strip() + "\n"


def render_page(step: Step, idea: str, data: dict, meta: dict, price: float | None = None) -> str:
    c = data.get("copy") or {}
    e = html.escape
    bullets = "".join(f"<li>{e(str(b))}</li>" for b in (c.get("bullets") or []))
    price_html = f"<p class='price'>{price:.2f} USD</p>" if price else ""
    cta_note = ("Join the waitlist (MOCKED: no sign-ups are collected)" if step.outward == "publish_landing_page"
                else "Buy (MOCKED checkout: no payment is taken)")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{e(str(c.get('headline', step.title)))}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem}}
.mocked{{background:#fde68a;color:#78350f;padding:.5rem;border-radius:4px;font-weight:600}}
.price{{font-size:1.4rem;font-weight:700}}</style></head>
<body>
<p class="mocked">{MOCKED}: phase {step.phase} step {step.num} ({step.title}) for {e(idea)}. Not published; written to the local outbox only after founder approval.</p>
<h1>{e(str(c.get('headline', '')))}</h1>
<p>{e(str(c.get('subheadline', '')))}</p>
<ul>{bullets}</ul>
{price_html}
<p><button disabled>{e(str(c.get('cta', 'Continue')))}</button> <small>{cta_note}</small></p>
<p><small>Agent: {e(str(meta.get('agent')))} · Model: {e(str(meta.get('model')))} · Run: {e(str(meta.get('run_id')))}</small></p>
</body></html>
"""
