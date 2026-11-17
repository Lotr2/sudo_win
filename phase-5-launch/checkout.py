"""Checkout: create an order and hand the buyer to the (MOCKED) provider's checkout page.

The store never asks for card details. It only collects the email address it needs to deliver the product.
"""

from __future__ import annotations

import re
import secrets

from store_db import now, tx

EMAIL_RE = re.compile(r"^[^@\s,;<>\"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")


class CheckoutError(Exception):
    pass


def start_checkout(db, provider, email: str, amount_cents: int, currency: str) -> tuple[str, str]:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise CheckoutError("Please enter a valid email address; it's where your access link is sent.")
    order_id = f"ord_{secrets.token_hex(10)}"
    with tx(db):
        db.execute("INSERT INTO orders (id, email, amount_cents, currency, status, created_at) VALUES (?,?,?,?,?,?)",
                   (order_id, email, amount_cents, currency, "pending", now()))
        checkout_id = provider.create_checkout(order_id, amount_cents, currency)
        db.execute("UPDATE orders SET checkout_id=? WHERE id=?", (checkout_id, order_id))
    return order_id, checkout_id
