"""Render the waitlist landing page from the Landing Copywriter's structured copy and the founder's brand.

The model writes text and picks from fixed design choices (layout, section order, type style, colour mood);
code owns the HTML and CSS. All text is escaped, the brand colour must be a hex colour, a logo must be a small
PNG/JPEG/WebP embedded in the page (no SVG: it can carry scripts), and the page loads nothing from outside.
There are no slots for testimonials, ratings, user counts, scarcity or countdowns. Publishing is MOCKED: the
founder hosts the page and connects the form themselves."""

from __future__ import annotations

import base64
import binascii
import html
import re

from .schemas import LandingCopy

e = html.escape

THEMES = {"trust": "#2f5d8a", "warm": "#b4562b", "fresh": "#237a57", "bold": "#6d3fc0", "calm": "#2f6f73"}
FONTS = {
    "modern": ('ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif',
               'ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif', "800"),
    "classic": ('"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif',
                'ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif', "700"),
    "friendly": ('ui-rounded,"SF Pro Rounded","Nunito","Varela Round","Segoe UI",system-ui,sans-serif',
                 'ui-rounded,"SF Pro Rounded","Nunito","Segoe UI",system-ui,sans-serif', "800"),
}
SECTIONS = ("problems", "features", "steps", "inside", "faq")
LAYOUT_DEFAULTS = {
    "product": ["inside", "features", "steps", "problems", "faq"],
    "story": ["features", "steps", "inside", "faq"],          # the problems open the page, in the hero
    "minimal": ["features", "inside", "faq"],
}
LAYOUT_REQUIRED = {"product": {"inside", "features", "faq"}, "story": {"features", "faq"}, "minimal": {"features", "faq"}}

_P = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
ICONS = {
    "clock": f'<svg viewBox="0 0 24 24" {_P}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    "shield": f'<svg viewBox="0 0 24 24" {_P}><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/></svg>',
    "check": f'<svg viewBox="0 0 24 24" {_P}><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/></svg>',
    "mail": f'<svg viewBox="0 0 24 24" {_P}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
    "document": f'<svg viewBox="0 0 24 24" {_P}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    "chart": f'<svg viewBox="0 0 24 24" {_P}><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
    "spark": f'<svg viewBox="0 0 24 24" {_P}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18"/></svg>',
    "calendar": f'<svg viewBox="0 0 24 24" {_P}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/></svg>',
    "chat": f'<svg viewBox="0 0 24 24" {_P}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
    "tools": f'<svg viewBox="0 0 24 24" {_P}><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/></svg>',
}

# ---------------- brand ----------------

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$")
LOGO_MAX_BYTES = 100_000
_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff", "webp": b"RIFF"}


def clean_brand(raw: dict | None) -> dict:
    """Validate the founder's brand: {name, color, logo}. Raises ValueError with a plain reason."""
    raw = raw or {}
    out: dict = {}
    name = str(raw.get("name") or "").strip()
    if name:
        if len(name) > 60:
            raise ValueError("The brand name can be at most 60 characters.")
        out["name"] = name
    color = str(raw.get("color") or "").strip()
    if color:
        if not HEX.match(color):
            raise ValueError("The brand colour must look like #1a2b3c.")
        out["color"] = color.lower()
    logo = str(raw.get("logo") or "").strip()
    if logo:
        m = LOGO.match(logo)
        if not m:
            raise ValueError("The logo must be a PNG, JPEG or WebP image (SVG isn't accepted).")
        try:
            data = base64.b64decode(m.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("The logo file couldn't be read.") from None
        if len(data) > LOGO_MAX_BYTES:
            raise ValueError("The logo must be under 100 KB.")
        if not data.startswith(_MAGIC[m.group(1)]):
            raise ValueError("The logo file doesn't match its image type.")
        out["logo"] = logo
    return out


def _rgb(hex_: str) -> tuple[float, float, float]:
    return tuple(int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0, min(1, c)) * 255):02x}" for c in rgb)


def _mix(a: str, b: str, t: float) -> str:
    return _hex(tuple(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))))


def _lum(hex_: str) -> float:
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(hex_)]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def palette(accent: str) -> dict:
    """Everything the page needs from one colour, with readable text on every coloured surface."""
    deep = _mix(accent, "#000000", 0.28)
    on = "#ffffff" if contrast("#ffffff", accent) >= 3 else "#111318"
    # Accent used as text on white must be readable: darken until it reaches 4.5:1.
    text_accent = accent
    for t in (0.15, 0.3, 0.45, 0.6):
        if contrast(text_accent, "#ffffff") >= 4.5:
            break
        text_accent = _mix(accent, "#000000", t)
    dark_accent = _mix(accent, "#ffffff", 0.35)
    return {"accent": accent, "deep": deep, "on": on, "text_accent": text_accent, "tint": _mix(accent, "#ffffff", 0.9),
            "dark_accent": dark_accent, "dark_tint": _mix(accent, "#0f1115", 0.86),
            "dark_on": "#111318" if contrast("#111318", dark_accent) >= contrast("#ffffff", dark_accent) else "#ffffff"}


# ---------------- pieces ----------------

def _mockup(form: str, name: str, items: list[str]) -> str:
    rows = items[:4] or ["Start here", "Step by step", "Ready to use"]
    if form == "template_pack":
        cards = "".join(f'<div class="sheet s{i}"><b>{e(t)}</b><i></i><i></i><i class="short"></i></div>'
                        for i, t in enumerate(reversed(rows[:3])))
        return f'<div class="mock pack" aria-hidden="true">{cards}</div>'
    if form == "web_tool":
        fields = "".join(f'<div class="field"><span>{e(t)}</span><i></i></div>' for t in rows[:3])
        return (f'<div class="mock tool" aria-hidden="true"><div class="bar"><span></span><span></span><span></span></div>'
                f'<b>{e(name)}</b>{fields}<div class="go">Calculate</div></div>')
    lines = "".join(f"<li>{e(t)}</li>" for t in rows)
    return (f'<div class="mock book" aria-hidden="true"><div class="spine"></div><div class="cover">'
            f'<small>Guide</small><b>{e(name)}</b><ol>{lines}</ol></div></div>')


def _form(copy: LandingCopy, n: int) -> str:
    return (f'<form class="signup" onsubmit="this.querySelector(\'output\').textContent=\'Thanks! This is a preview: '
            f'the form is not connected yet.\';return false"><label class="sr" for="email{n}">Email address</label>'
            f'<input id="email{n}" type="email" placeholder="you@example.com" required autocomplete="email">'
            f'<button type="submit">{e(copy.cta_text)}</button><output aria-live="polite"></output></form>'
            '<p class="fine">We only email you when it\'s ready. No spam, unsubscribe any time.</p>')


def section_order(copy: LandingCopy, layout: str) -> list[str]:
    """The model's order, cleaned: known sections only, no repeats, and the layout's essentials always present."""
    order = [s for s in dict.fromkeys(copy.section_order or []) if s in SECTIONS]
    if not order:
        order = list(LAYOUT_DEFAULTS[layout])
    if layout == "story":
        order = [s for s in order if s != "problems"]
    for s in LAYOUT_DEFAULTS[layout]:
        if s in LAYOUT_REQUIRED[layout] and s not in order:
            order.append(s)
    return order


def _section(key: str, copy: LandingCopy, layout: str, mock: str) -> str:
    if key == "problems" and copy.problems:
        items = "".join(f"<li>{e(p)}</li>" for p in copy.problems)
        return f'<div class="wrap narrow"><h2>{e(copy.problem_title or "Sound familiar?")}</h2><ul class="pains">{items}</ul></div>'
    if key == "features":
        feats = [f.model_dump() for f in copy.features] or [{"title": b, "text": "", "icon": "check"} for b in copy.benefits]
        cards = "".join(f'<div class="card"><span class="icon">{ICONS.get(f["icon"], ICONS["check"])}</span>'
                        f'<div><h3>{e(f["title"])}</h3>' + (f'<p>{e(f["text"])}</p>' if f["text"] else "") + "</div></div>"
                        for f in feats)
        return f'<div class="wrap"><h2 class="center">What you get</h2><div class="grid">{cards}</div></div>'
    if key == "steps" and copy.steps:
        steps = "".join(f'<li><span class="n">{i}</span><div><h3>{e(s.title)}</h3><p>{e(s.text)}</p></div></li>'
                        for i, s in enumerate(copy.steps, 1))
        return f'<div class="wrap"><h2 class="center">How it works</h2><ol class="steps">{steps}</ol></div>'
    if key == "inside" and (copy.inside or copy.body):
        items = "".join(f"<li>{ICONS['check']}<span>{e(x)}</span></li>" for x in copy.inside)
        side = mock if layout == "story" else (f'<ul class="inside">{items}</ul>' if items else "")
        text = f'<p class="lead">{e(copy.body)}</p>' + (f'<ul class="inside">{items}</ul>' if layout == "story" and items else "")
        return f'<div class="wrap two"><div><h2>What\'s inside</h2>{text}</div>{side}</div>'
    if key == "faq" and copy.faq:
        qa = "".join(f"<details><summary>{e(q.q)}</summary><p>{e(q.a)}</p></details>" for q in copy.faq)
        return f'<div class="wrap narrow"><h2 class="center">Questions</h2><div class="faq">{qa}</div></div>'
    return ""


def _hero(copy: LandingCopy, layout: str, name: str, mock: str) -> str:
    eyebrow = f'<span class="eyebrow">{e(copy.eyebrow)}</span>' if copy.eyebrow else ""
    lead = f"{eyebrow}<h1>{e(copy.headline)}</h1><p class=\"sub\">{e(copy.subheadline)}</p>"
    if layout == "product":
        return f'<header class="hero split"><div class="wrap"><div>{lead}{_form(copy, 1)}</div>{mock}</div></header>'
    if layout == "story":
        quotes = "".join(f"<li>{e(p)}</li>" for p in copy.problems[:3])
        title = f'<p class="quotes-title">{e(copy.problem_title)}</p>' if copy.problem_title and quotes else ""
        return (f'<header class="hero centered"><div class="wrap narrow">{lead}'
                + (f'{title}<ul class="quotes">{quotes}</ul>' if quotes else "") + f'{_form(copy, 1)}</div></header>')
    return f'<header class="hero minimal"><div class="wrap narrow">{lead}{_form(copy, 1)}</div></header>'


INTERNAL_LABELS = re.compile(r"\s*[\(\[]\s*(ASSUMPTIONS?|MOCKED|SOURCED|SIMULATED|STUBBED)\s*[\)\]]|\b(ASSUMPTIONS?|SOURCED)\b:?"
                             r"|\s*\[?\b(ev|log|as|doc)_\d+\b\]?")


def strip_internal(text: str) -> str:
    """Customer-facing copy never shows internal labels (ASSUMPTION, MOCKED...) or evidence ids."""
    return re.sub(r"\s{2,}", " ", INTERNAL_LABELS.sub("", text)).strip()


def clean_copy(copy: LandingCopy) -> LandingCopy:
    def walk(v):
        if isinstance(v, str):
            return strip_internal(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: (walk(x) if k not in {"icon", "theme", "layout", "font_style", "section_order"} else x)
                    for k, x in v.items()}
        return v
    return LandingCopy.model_validate(walk(copy.model_dump()))


def render(copy: LandingCopy, *, product_name: str = "", product_form: str = "guide", brand: dict | None = None) -> str:
    brand = clean_brand(brand)
    copy = clean_copy(copy)
    layout = copy.layout if copy.layout in LAYOUT_DEFAULTS else "product"
    p = palette(brand.get("color") or THEMES.get(copy.theme, THEMES["trust"]))
    head_font, body_font, weight = FONTS.get(copy.font_style, FONTS["modern"])
    name = product_name or copy.headline
    mock = _mockup(product_form, name, copy.inside or copy.benefits)
    blocks = []
    for i, key in enumerate(section_order(copy, layout)):
        inner = _section(key, copy, layout, mock)
        if inner:
            tinted = layout != "minimal" and len(blocks) % 2 == 1
            blocks.append(f'<section class="sec-{key}{" tinted" if tinted else ""}">{inner}</section>')
    logo = f'<img src="{brand["logo"]}" alt="" class="logo">' if brand.get("logo") else ""
    brand_name = brand.get("name") or ""
    topbar = (f'<nav class="top"><div class="wrap">{logo}<span>{e(brand_name or name)}</span></div></nav>'
              if (logo or brand_name) else "")
    footer = e(name) + (f" by {e(brand_name)}" if brand_name and brand_name != name else "") + ". Coming soon."
    return PAGE.format(
        title=e(copy.headline), head_font=head_font, body_font=body_font, weight=weight, layout=layout, topbar=topbar,
        hero=_hero(copy, layout, name, mock), sections="\n".join(blocks), name=e(name),
        closing=e(copy.closing or copy.subheadline), form_bottom=_form(copy, 2), footer=footer, **p)


PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<!-- Waitlist page: copy and design choices by the Landing Copywriter, rendered by code. Publishing is MOCKED: the
     founder hosts the page and connects the form. The page has no slots for social proof, counts or timers. -->
<style>
:root{{--accent:{accent};--deep:{deep};--on:{on};--ta:{text_accent};--tint:{tint};--bg:#fff;--ink:#16181d;--muted:#5b616e;
  --line:#e6e8ee;--card:#fff;--hf:{head_font};--bf:{body_font}}}
@media (prefers-color-scheme:dark){{:root{{--accent:{dark_accent};--deep:{dark_accent};--on:{dark_on};--ta:{dark_accent};
  --tint:{dark_tint};--bg:#0f1115;--ink:#eceef3;--muted:#a3a9b6;--line:#2a2e37;--card:#171a20}}}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
body{{margin:0;font:17px/1.6 var(--bf);color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px}}.narrow{{max-width:760px}}
h1,h2,h3{{font-family:var(--hf);line-height:1.15;letter-spacing:-.015em;margin:0}}
h1{{font-size:clamp(2.2rem,5.2vw,3.6rem);font-weight:{weight}}}h2{{font-size:clamp(1.6rem,3.2vw,2.2rem);margin-bottom:28px}}
h3{{font-size:1.1rem}}.center{{text-align:center}}p{{margin:0}}
nav.top{{padding:16px 0;border-bottom:1px solid var(--line);background:var(--bg)}}
nav.top .wrap{{display:flex;align-items:center;gap:12px;font-family:var(--hf);font-weight:700;font-size:1.05rem}}
.logo{{height:34px;width:auto;max-width:160px;object-fit:contain;border-radius:6px}}
.eyebrow{{display:inline-block;font-size:.8rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ta);
  background:color-mix(in srgb,var(--accent) 12%,transparent);padding:6px 12px;border-radius:99px;margin-bottom:18px}}
.sub{{font-size:1.2rem;color:var(--muted);margin:18px 0 28px;max-width:34rem}}
.hero{{padding:72px 0 80px;overflow:hidden}}
.hero.split{{background:radial-gradient(1200px 500px at 85% -10%,var(--tint),transparent 60%),linear-gradient(180deg,var(--tint),var(--bg))}}
.hero.split .wrap{{display:grid;grid-template-columns:1.15fr .85fr;gap:56px;align-items:center}}
@media (max-width:860px){{.hero.split .wrap{{grid-template-columns:1fr}}.mock{{max-width:300px;margin:0 auto}}}}
.hero.centered{{text-align:center;background:linear-gradient(180deg,var(--tint),var(--bg) 85%)}}
.hero.centered .sub,.hero.minimal .sub{{margin-left:auto;margin-right:auto}}.hero.centered .signup,.hero.minimal .signup{{margin:0 auto}}
.quotes-title{{font-weight:700;color:var(--ta);margin:6px 0 10px}}
.quotes{{list-style:none;padding:0;margin:0 auto 30px;display:flex;flex-wrap:wrap;justify-content:center;gap:10px;max-width:46rem}}
.quotes li{{background:var(--card);border:1px solid var(--line);border-radius:99px;padding:8px 16px;font-size:.95rem;
  box-shadow:0 4px 14px rgba(0,0,0,.05)}}.quotes li::before{{content:"\\201C";color:var(--ta);font-weight:800;margin-right:2px}}
.quotes li::after{{content:"\\201D";color:var(--ta);font-weight:800;margin-left:2px}}
.hero.minimal{{text-align:center;padding:96px 0 64px;border-bottom:1px solid var(--line)}}
.hero.minimal h1{{font-size:clamp(2rem,4.6vw,3.1rem)}}
.signup{{display:flex;gap:10px;flex-wrap:wrap;max-width:30rem}}
.signup input{{flex:1 1 220px;font:inherit;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)}}
.signup input:focus{{outline:3px solid color-mix(in srgb,var(--accent) 35%,transparent);border-color:var(--accent)}}
.signup button{{font:inherit;font-weight:700;padding:14px 22px;border:0;border-radius:12px;color:var(--on);cursor:pointer;
  background:var(--accent);box-shadow:0 8px 22px color-mix(in srgb,var(--accent) 30%,transparent)}}
.signup button:hover{{filter:brightness(1.07)}}.signup output{{flex-basis:100%;color:var(--ta);font-weight:600}}
.fine{{font-size:.85rem;color:var(--muted);margin-top:10px}}.sr{{position:absolute;left:-9999px}}
section{{padding:80px 0}}.tinted{{background:var(--tint)}}
.pains{{list-style:none;padding:0;margin:0;display:grid;gap:12px}}
.pains li{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:12px;padding:16px 18px;font-size:1.05rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;
  box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px rgba(0,0,0,.04);transition:transform .2s}}
.card:hover{{transform:translateY(-3px)}}.card p{{color:var(--muted);margin-top:8px;font-size:.98rem}}
.icon{{display:inline-grid;place-items:center;width:44px;height:44px;border-radius:12px;margin-bottom:14px;flex:none;
  color:var(--ta);background:color-mix(in srgb,var(--accent) 12%,transparent)}}.icon svg{{width:24px;height:24px}}
.minimal-page .grid{{grid-template-columns:1fr 1fr;gap:28px 40px}}@media (max-width:700px){{.minimal-page .grid{{grid-template-columns:1fr}}}}
.minimal-page .card{{display:flex;gap:16px;border:0;box-shadow:none;padding:0;background:none}}.minimal-page .card:hover{{transform:none}}
.minimal-page section{{padding:64px 0;border-bottom:1px solid var(--line)}}
.steps{{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:22px}}
.steps li{{display:flex;gap:16px;align-items:flex-start;background:var(--card);border-radius:16px;padding:22px;border:1px solid var(--line)}}
.steps .n{{flex:none;width:36px;height:36px;border-radius:50%;display:grid;place-items:center;font-weight:800;color:var(--on);
  background:var(--accent)}}.steps p{{color:var(--muted);margin-top:6px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:48px;align-items:center}}@media (max-width:760px){{.two{{grid-template-columns:1fr}}}}
.lead{{color:var(--muted);font-size:1.08rem;margin-top:-12px}}
.inside{{list-style:none;padding:0;margin:18px 0 0;display:grid;gap:10px}}
.inside li{{display:flex;gap:12px;align-items:flex-start;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--card)}}
.inside svg{{flex:none;width:22px;height:22px;color:var(--ta);margin-top:2px}}
.faq details{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;margin-bottom:10px}}
.faq summary{{cursor:pointer;font-weight:700;list-style:none;display:flex;justify-content:space-between;gap:12px}}
.faq summary::after{{content:"+";color:var(--ta);font-size:1.3rem;line-height:1}}.faq details[open] summary::after{{content:"\\2212"}}
.faq p{{color:var(--muted);margin-top:10px}}
.closing{{text-align:center;padding:88px 0;background:linear-gradient(135deg,var(--accent),var(--deep));color:var(--on)}}
.closing h2{{color:inherit;margin-bottom:12px}}.closing p.sub{{color:inherit;opacity:.88;margin:0 auto 28px}}
.closing .signup{{margin:0 auto}}.closing .signup input{{border-color:transparent}}
.closing .signup button{{background:var(--bg);color:var(--ink);box-shadow:none}}.closing .fine,.closing output{{color:inherit!important;opacity:.85}}
.minimal-page .closing{{background:none;color:var(--ink);border-top:0}}
.minimal-page .closing .signup button{{background:var(--accent);color:var(--on)}}
.minimal-page .closing .signup input{{border-color:var(--line)}}
footer{{padding:28px 0;color:var(--muted);font-size:.85rem;text-align:center}}
.mock{{position:relative;width:100%;max-width:360px;justify-self:center;aspect-ratio:4/5}}
.book .cover{{position:absolute;inset:0 0 0 14px;border-radius:6px 16px 16px 6px;padding:30px 26px;color:var(--on);
  display:flex;flex-direction:column;justify-content:center;
  background:linear-gradient(150deg,var(--accent),var(--deep));box-shadow:0 30px 60px -20px rgba(0,0,0,.45);transform:rotate(-3deg)}}
.book .spine{{position:absolute;left:0;top:10px;bottom:-6px;width:28px;border-radius:6px;background:var(--deep);transform:rotate(-3deg)}}
.book small{{text-transform:uppercase;letter-spacing:.14em;opacity:.8;font-weight:700}}
.book b{{display:block;font-family:var(--hf);font-size:1.6rem;line-height:1.15;margin:12px 0 22px}}
.book ol{{margin:0;padding-left:20px;opacity:.92;font-size:.92rem;display:grid;gap:8px}}
.pack .sheet{{position:absolute;inset:8% 6%;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;
  box-shadow:0 24px 50px -24px rgba(0,0,0,.35)}}.pack .s0{{transform:rotate(-7deg) translate(-6%,4%)}}
.pack .s1{{transform:rotate(4deg) translate(5%,-2%)}}.pack .s2{{transform:rotate(-1deg)}}
.pack b{{display:block;font-size:1.05rem;margin-bottom:16px;color:var(--ta)}}
.pack i{{display:block;height:10px;border-radius:6px;background:var(--line);margin:10px 0}}.pack i.short{{width:60%}}
.tool{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;aspect-ratio:auto;
  box-shadow:0 30px 60px -24px rgba(0,0,0,.35)}}.tool .bar{{display:flex;gap:6px;margin-bottom:16px}}
.tool .bar span{{width:10px;height:10px;border-radius:50%;background:var(--line)}}.tool b{{display:block;margin-bottom:14px}}
.tool .field{{display:flex;justify-content:space-between;gap:10px;align-items:center;margin:10px 0;font-size:.9rem;color:var(--muted)}}
.tool .field i{{width:42%;height:30px;border:1px solid var(--line);border-radius:8px}}
.tool .go{{margin-top:16px;text-align:center;font-weight:700;color:var(--on);padding:10px;border-radius:10px;background:var(--accent)}}
@media (prefers-reduced-motion:reduce){{.card{{transition:none}}html{{scroll-behavior:auto}}}}
</style>
</head>
<body class="{layout}-page">
{topbar}
{hero}
{sections}
<section class="closing"><div class="wrap narrow"><h2>{name}</h2><p class="sub">{closing}</p>{form_bottom}</div></section>
<footer><div class="wrap">{footer}</div></footer>
</body>
</html>
"""
