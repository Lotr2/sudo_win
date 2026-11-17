"""Store server: the approved store page, MOCKED checkout, webhook endpoint, order pages, refund requests, and the
product itself behind per-buyer access links that stop working when a refund revokes them.

Single-threaded on purpose (HTTPServer, not ThreadingHTTPServer): one SQLite connection, no races.
"""

from __future__ import annotations

import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

import delivery
import refunds
import store_page
import webhooks
from checkout import CheckoutError, start_checkout
from store_db import now, one

POLICY_TITLES = {"terms": "Terms of service", "privacy": "Privacy policy", "refunds": "Refund policy"}


class Store:
    """Everything the handler needs, bundled so tests can build one directly."""

    def __init__(self, db, provider, secret, rule, facts, store_dir: Path, product_dir: Path, policy_files: dict,
                 base_url: str):
        self.db, self.provider, self.secret, self.rule = db, provider, secret, rule
        self.facts, self.store_dir, self.product_dir = facts, Path(store_dir), Path(product_dir).resolve()
        self.policy_files, self.base_url = policy_files, base_url
        self.lock = threading.Lock()


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location):
            self._send(303, "", headers={"Location": location})

        def _form(self) -> dict:
            length = min(int(self.headers.get("Content-Length") or 0), 8192)
            return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode(errors="replace")).items()}

        def do_GET(self):
            path = unquote(self.path.split("?")[0])
            with store.lock:
                try:
                    if path == "/":
                        page = store_page.approved_store(store.store_dir)
                        headers = {}
                        if "sv=1" not in (self.headers.get("Cookie") or ""):
                            # one per browser; no IP or fingerprint stored
                            store.db.execute("INSERT INTO visits (at) VALUES (?)", (now(),))
                            headers["Set-Cookie"] = "sv=1; Max-Age=2592000; Path=/; SameSite=Lax; HttpOnly"
                        return self._send(200, page, headers=headers)
                    if path.startswith("/policies/"):
                        name = path.split("/")[2]
                        f = store.policy_files.get(name)
                        if not f or not Path(f).exists():
                            return self._send(404, store_page.render_message("Not found", "No such policy."))
                        return self._send(200, store_page.render_policy(POLICY_TITLES[name], Path(f).read_text()))
                    if path.startswith("/mock-checkout/"):
                        row = one(store.db, "SELECT c.id, o.email FROM mock_checkouts c JOIN orders o ON o.id=c.order_id "
                                            "WHERE c.id=? AND c.status='open'", path.split("/")[2])
                        if row is None:
                            return self._send(404, store_page.render_message("Not found", "This checkout has ended."))
                        return self._send(200, store_page.render_mock_checkout(
                            row["id"], store.facts["product"], store.facts["price_line"], row["email"]))
                    if path.startswith("/order/"):
                        return self._order(path.split("/")[2])
                    if path.startswith("/app/"):
                        return self._app(path)
                    return self._send(404, store_page.render_message("Not found", "That page doesn't exist."))
                except PermissionError as e:
                    return self._send(403, store_page.render_message("Not available", str(e)))

        def _order(self, order_id):
            order = one(store.db, "SELECT * FROM orders WHERE id=?", order_id)
            if order is None:
                return self._send(404, store_page.render_message("Not found", "No such order."))
            grant = one(store.db, "SELECT token FROM grants WHERE order_id=? AND revoked_at IS NULL", order_id)
            link = f"/app/{grant['token']}/" if grant and order["status"] == "paid" else None
            refund = one(store.db, "SELECT status FROM refunds WHERE order_id=? ORDER BY requested_at DESC", order_id)
            mail = one(store.db, "SELECT body FROM outbox WHERE order_id=? AND kind='delivery'", order_id)
            return self._send(200, store_page.render_order(order, link, refund["status"] if refund else None,
                                                           mail["body"] if mail else None))

        def _app(self, path):
            parts = path.split("/", 3)  # ['', 'app', token, rest]
            token = parts[2] if len(parts) > 2 else ""
            if not delivery.access_ok(store.db, token):
                return self._send(403, store_page.render_message(
                    "No access", "This access link is not valid, or the purchase was refunded."))
            if len(parts) < 4:
                return self._redirect(f"/app/{token}/")
            rel = parts[3] or "index.html"
            target = (store.product_dir / rel).resolve()
            if not target.is_relative_to(store.product_dir) or not target.is_file() or target.name == "logic.test.js":
                return self._send(404, store_page.render_message("Not found", "No such file."))
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, target.read_bytes(), ctype)

        def do_POST(self):
            path = unquote(self.path.split("?")[0])
            with store.lock:
                if path == "/webhooks/payments":
                    length = min(int(self.headers.get("Content-Length") or 0), 65536)
                    payload = self.rfile.read(length)
                    try:
                        result = webhooks.handle(store.db, payload, self.headers.get("X-Signature", ""),
                                                 store.secret, store.rule, store.base_url)
                    except webhooks.InvalidSignature:
                        return self._send(400, "invalid signature", "text/plain")
                    return self._send(200, result, "text/plain")
                form = self._form()
                if path == "/checkout":
                    try:
                        _, checkout_id = start_checkout(store.db, store.provider, form.get("email", ""),
                                                        store.facts["amount_cents"], store.facts["currency"])
                    except CheckoutError as e:
                        return self._send(400, store_page.render_message("Check your email address", str(e), ("/", "Back")))
                    return self._redirect(f"/mock-checkout/{checkout_id}")
                if path.startswith("/mock-pay/"):
                    checkout_id = path.split("/")[2]
                    row = one(store.db, "SELECT order_id FROM mock_checkouts WHERE id=?", checkout_id)
                    try:
                        payload, sig = store.provider.complete_checkout(checkout_id, form.get("outcome") == "success")
                    except ValueError as e:
                        return self._send(400, store_page.render_message("Checkout ended", str(e)))
                    webhooks.handle(store.db, payload, sig, store.secret, store.rule, store.base_url)
                    return self._redirect(f"/order/{row['order_id']}")
                if path == "/refund-request":
                    try:
                        refunds.request(store.db, form.get("order_id", ""), form.get("email", ""), form.get("reason", ""))
                    except refunds.RefundError as e:
                        return self._send(400, store_page.render_message("Refund request", str(e)))
                    return self._send(200, store_page.render_message(
                        "Refund requested", "Thanks. The founder reviews every refund request personally and will "
                                            "reply by email.", (f"/order/{form.get('order_id', '')}", "Back to your order")))
                return self._send(404, store_page.render_message("Not found", "That page doesn't exist."))

        def log_message(self, *args):
            pass

    return Handler


def make_server(store: Store, host: str, port: int) -> HTTPServer:
    store_page.approved_store(store.store_dir)  # refuse to start without an approved page
    return HTTPServer((host, port), make_handler(store))
