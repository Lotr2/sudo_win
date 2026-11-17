"""Store pages. Model text is HTML-escaped into fixed templates; price, billing, refund terms, features and the
MOCKED-checkout notice come from approved data and code, never from the model.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

from store_db import now

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#5b5b63;--accent:#2f5d50;--card:#fff;--line:#e4e2dc;--warn:#8a5a00;--warnbg:#fff4dc}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#f2f2f2;--muted:#a3a3ad;--accent:#7fc4ae;--card:#1d1d21;--line:#2c2c31;--warn:#ffd27a;--warnbg:#2e2614}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.6 system-ui,sans-serif}
main{max-width:680px;margin:0 auto;padding:40px 16px}h1{font-size:2rem;line-height:1.2;margin:0 0 12px}h2{font-size:1.15rem;margin:32px 0 10px}
.sub{font-size:1.1rem;color:var(--muted);margin:0 0 24px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px}
.price{font-size:1.6rem;font-weight:700;margin:0}.small{font-size:.9rem;color:var(--muted)}
.mock{background:var(--warnbg);color:var(--warn);border:1px solid var(--warn);border-radius:8px;padding:10px 14px;font-size:.92rem;margin:0 0 20px}
form{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 0}input,textarea{flex:1 1 220px;padding:11px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
button{padding:11px 18px;border:0;border-radius:8px;background:var(--accent);color:var(--bg);font:inherit;font-weight:600;cursor:pointer}
button.alt{background:transparent;color:var(--fg);border:1px solid var(--line)}dt{font-weight:600;margin-top:12px}dd{margin:4px 0 0}
pre{white-space:pre-wrap;font:15px/1.55 ui-monospace,monospace}footer{margin-top:40px;font-size:.88rem;color:var(--muted)}a{color:var(--accent)}
"""
MOCK_NOTICE = ("MOCKED CHECKOUT: payments in this version are simulated. No card details are asked for and no money "
               "is taken.")


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{_e(title)}</title><style>{CSS}</style></head>'
            f'<body><main>{body}</main></body></html>')


def capability(story: str) -> str:
    m = re.search(r"\bI can (.+?)(?:,?\s+so that\b|\.?$)", story, re.I)
    text = (m.group(1) if m else story).strip().rstrip(".")
    return text[:1].upper() + text[1:]


def render_store(copy: dict, f: dict) -> str:
    """f: product, price_line, billing_note, refund_note, features, out_of_scope, business_name, contact_email."""
    features = "".join(f"<li>{_e(x)}</li>" for x in f["features"])
    missing = "".join(f"<li>{_e(x)}</li>" for x in f["out_of_scope"])
    faq = "".join(f"<dt>{_e(q['q'])}</dt><dd>{_e(q['a'])}</dd>" for q in copy.get("faq") or [])
    body = f"""<p class="mock">{MOCK_NOTICE}</p>
<h1>{_e(copy['headline'])}</h1><p class="sub">{_e(copy['subheadline'])}</p>
<div class="card"><p class="price">{_e(f['price_line'])}</p><p class="small">{_e(f['billing_note'])} {_e(f['refund_note'])}</p>
<form method="post" action="/checkout"><input type="email" name="email" required placeholder="you@example.com" aria-label="Email for your access link">
<button type="submit">Buy {_e(f['product'])}</button></form>
<p class="small">We only ask for your email, to send your access link. By buying you agree to the <a href="/policies/terms">terms</a>.</p></div>
<h2>What you get</h2><ul>{features}</ul>
{f"<h2>Not included yet</h2><ul>{missing}</ul>" if missing else ""}
{f"<h2>Who it is for</h2><p>{_e(copy['who_its_for'])}</p>" if copy.get('who_its_for') else ""}
{f"<h2>Questions</h2><dl>{faq}</dl>" if faq else ""}
<h2>Already bought?</h2><p class="small">Ask for a refund from your order page (link in your access email). The founder reviews every request.</p>
<footer>{_e(f['product'])} is an early version, sold by {_e(f['business_name'])} ({_e(f['contact_email'])}).
<a href="/policies/terms">Terms</a> · <a href="/policies/privacy">Privacy</a> · <a href="/policies/refunds">Refunds</a></footer>"""
    return page(f["product"], body)


def render_mock_checkout(checkout_id: str, product: str, price_line: str, email: str) -> str:
    body = f"""<p class="mock">{MOCK_NOTICE} This page stands in for a payment provider.</p>
<h1>Checkout: {_e(product)}</h1><div class="card"><p class="price">{_e(price_line)}</p><p class="small">For {_e(email)}</p>
<form method="post" action="/mock-pay/{_e(checkout_id)}"><input type="hidden" name="outcome" value="success">
<button type="submit">Simulate a successful payment</button></form>
<form method="post" action="/mock-pay/{_e(checkout_id)}"><input type="hidden" name="outcome" value="fail">
<button class="alt" type="submit">Simulate a failed payment</button></form></div>"""
    return page("Checkout (MOCKED)", body)


def render_order(order, grant_link: str | None, refund_status: str | None, outbox_body: str | None) -> str:
    status = {"paid": "Paid. Your access is ready.", "pending": "Waiting for payment.",
              "failed": "The payment failed. Nothing was charged.", "refunded": "Refunded. Access has been removed.",
              "amount_mismatch": "There was a problem with this payment. The founder has been told; nothing was delivered."
              }.get(order["status"], order["status"])
    body = f'<p class="mock">{MOCK_NOTICE}</p><h1>Your order</h1><div class="card"><p>{_e(status)}</p>'
    if grant_link:
        body += f'<p><a href="{_e(grant_link)}">Open the product</a></p>'
    body += "</div>"
    if outbox_body:
        body += f'<h2>Delivery email (MOCKED: shown here, not sent)</h2><div class="card"><pre>{_e(outbox_body)}</pre></div>'
    if refund_status:
        body += f"<h2>Refund</h2><p>Your refund request is {_e(refund_status)}.</p>"
    elif order["status"] == "paid":
        body += f"""<h2>Ask for a refund</h2><form method="post" action="/refund-request">
<input type="hidden" name="order_id" value="{_e(order['id'])}"><input type="email" name="email" required placeholder="The email you bought with">
<textarea name="reason" placeholder="Reason (optional)"></textarea><button type="submit">Request a refund</button></form>
<p class="small">A person reviews every refund. Nothing is refunded automatically.</p>"""
    return page("Your order", body)


def render_message(title: str, text: str, link: tuple[str, str] | None = None) -> str:
    extra = f'<p><a href="{_e(link[0])}">{_e(link[1])}</a></p>' if link else ""
    return page(title, f"<h1>{_e(title)}</h1><p>{_e(text)}</p>{extra}")


def render_policy(title: str, markdown_text: str) -> str:
    return page(title, f'<p><a href="/">Back to the store</a></p><pre>{_e(markdown_text)}</pre>')


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def publish(store_dir: Path, page_html: str) -> dict:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "index.html").write_text(page_html)
    record = {"sha256": sha256(page_html), "approved_by": "founder", "approved_at": now()}
    (store_dir / "approval.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def approved_store(store_dir: Path) -> str:
    page_path, approval = store_dir / "index.html", store_dir / "approval.json"
    if not page_path.exists() or not approval.exists():
        raise PermissionError("No founder-approved store page. Run `prepare` first.")
    text = page_path.read_text()
    if json.loads(approval.read_text()).get("sha256") != sha256(text):
        raise PermissionError("The store page changed after the founder approved it. Run `prepare` again.")
    return text
