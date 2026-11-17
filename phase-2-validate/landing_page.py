"""Landing page: render model copy into a fixed template, lock it with founder approval, and serve it locally.

The model only ever supplies text. All of it is HTML-escaped, and the template adds a mandatory notice that
the product does not exist yet. No JavaScript, trackers or external resources.
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from waitlist import Waitlist

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#5b5b63;--accent:#2f5d50;--card:#fff;--line:#e4e2dc}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#f2f2f2;--muted:#a3a3ad;--accent:#7fc4ae;--card:#1d1d21;--line:#2c2c31}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.6 system-ui,sans-serif}
main{max-width:680px;margin:0 auto;padding:48px 20px}h1{font-size:2.1rem;line-height:1.2;margin:0 0 12px}
.sub{font-size:1.15rem;color:var(--muted);margin:0 0 28px}h2{font-size:1.15rem;margin:36px 0 10px}
.notice{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);padding:14px 16px;border-radius:8px;font-size:.95rem}
form{display:flex;gap:8px;flex-wrap:wrap;margin:24px 0}input[type=email]{flex:1 1 240px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
button{padding:12px 20px;border:0;border-radius:8px;background:var(--accent);color:var(--bg);font:inherit;font-weight:600;cursor:pointer}
.hp{position:absolute;left:-9999px}ul,ol{padding-left:22px}dt{font-weight:600;margin-top:12px}dd{margin:4px 0 0}
footer{margin-top:48px;font-size:.85rem;color:var(--muted)}
"""


def _e(s: str) -> str:
    return html.escape(str(s), quote=True)


def render(copy: dict) -> str:
    name = _e(copy["product_name"])
    problems = "".join(f"<li>{_e(p)}</li>" for p in copy["problem_points"])
    steps = "".join(f"<li>{_e(s)}</li>" for s in copy["how_it_works"])
    faq = "".join(f"<dt>{_e(f['q'])}</dt><dd>{_e(f['a'])}</dd>" for f in copy["faq"])
    form = (f'<form method="post" action="/join"><label class="hp">Leave empty <input name="website" tabindex="-1" '
            f'autocomplete="off"></label><input type="email" name="email" required placeholder="you@example.com" '
            f'aria-label="Email address"><button type="submit">{_e(copy["cta_label"])}</button></form>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title><style>{CSS}</style></head><body><main>
<h1>{_e(copy["headline"])}</h1>
<p class="sub">{_e(copy["subheadline"])}</p>
<p class="notice"><strong>{name} is not available yet.</strong> We are checking whether people want it before building it.
Joining the waitlist is free and only means we may email you about it. There is nothing to buy.</p>
{form}
{f"<h2>Sound familiar?</h2><ul>{problems}</ul>" if problems else ""}
{f"<h2>How it would work</h2><ol>{steps}</ol>" if steps else ""}
{f"<h2>Who it is for</h2><p>{_e(copy['who_its_for'])}</p>" if copy.get("who_its_for") else ""}
{f"<h2>Questions</h2><dl>{faq}</dl>" if faq else ""}
{form}
<footer>We store only your email address, and only to contact you about {name}. Reply to any email from us to be removed.</footer>
</main></body></html>
"""


THANKS = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thanks</title><style>{css}</style></head><body><main><h1>Thanks, you're on the list.</h1>
<p class="sub">We'll only email you about this product. There is nothing to buy.</p></main></body></html>"""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def publish(site_dir: Path, page_html: str) -> dict:
    """Write the founder-approved page and record its hash. Only call after explicit founder approval."""
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(page_html)
    record = {"sha256": sha256(page_html), "approved_by": "founder",
              "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (site_dir / "approval.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def approved_page(site_dir: Path) -> str:
    """Return the page only if it is exactly what the founder approved. Raises otherwise."""
    page, approval = site_dir / "index.html", site_dir / "approval.json"
    if not page.exists() or not approval.exists():
        raise PermissionError("No founder-approved landing page. Run the `plan` step and approve the page first.")
    text = page.read_text()
    if json.loads(approval.read_text()).get("sha256") != sha256(text):
        raise PermissionError("The landing page changed after the founder approved it. Re-run `plan` to approve it again.")
    return text


def make_handler(site_dir: Path, waitlist: Waitlist):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, headers: dict | None = None):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] == "/thanks":
                return self._send(200, THANKS.format(css=CSS))
            if self.path.split("?")[0] != "/":
                return self._send(404, "Not found")
            try:
                page = approved_page(site_dir)
            except PermissionError as e:
                return self._send(403, _e(str(e)))
            headers = {}
            if "seen=1" not in (self.headers.get("Cookie") or ""):
                waitlist.record_visit()  # one per browser, no IP or fingerprint stored
                headers["Set-Cookie"] = "seen=1; Max-Age=2592000; Path=/; SameSite=Lax; HttpOnly"
            self._send(200, page, headers)

        def do_POST(self):
            if self.path != "/join":
                return self._send(404, "Not found")
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = parse_qs(self.rfile.read(length).decode(errors="replace"))
            if not form.get("website", [""])[0]:  # honeypot left empty = probably a person
                waitlist.record_signup(form.get("email", [""])[0])
            self._send(303, "", {"Location": "/thanks"})

        def log_message(self, *args):
            pass

    return Handler


def serve(site_dir: Path, waitlist: Waitlist, host: str = "127.0.0.1", port: int = 8000):
    approved_page(site_dir)  # refuse to start if there is no approved page
    server = ThreadingHTTPServer((host, port), make_handler(site_dir, waitlist))
    return server
