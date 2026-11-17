"""Pricing Analyst (GPT-OSS-20B, medium): sets price and tiers from sourced price anchors."""

from ..schemas import Pricing


def run(ctx, payload: dict, allowed_ids: list[str]) -> Pricing:
    return ctx.router.call("pricing_analyst", payload, Pricing, action="set_price",
                           validate=lambda p: ctx.ledger.require_known(p.evidence_ids),
                           sources=ctx.ledger.urls(allowed_ids))
