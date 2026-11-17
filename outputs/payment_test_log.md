# Payment and delivery test log

All payment events are **MOCKED**: no provider, no keys, no card data, no real money.

Run: `20261117-200601-ad63`

1. Checkout ord_001 ($27) → payment.succeeded evt_0002 → webhook: **delivered**
2. Same event replayed → webhook: **duplicate**; deliveries for ord_001: 1
3. Refund for ord_002 attempted without founder approval → refused; access still **active**
4. Founder approved the refund → refund.succeeded (MOCKED) → access **revoked**
5. Reconciliation: 2 orders, gross $54.00, fees $2.16, refunds $27.00, payout $24.84 → 0 mismatch(es)
6. Scratch copy with a deliberately broken record (delivery with no payment) → reconciliation flags it: **True** (ord_999: delivery with no payment)

**Result: PASS**

Checks: one payment delivers once; a duplicate event does not deliver twice; a refund without founder approval does nothing; an approved refund revokes access; reconciliation matches orders, payments, fees, refunds and payouts and flags a deliberately broken record.
