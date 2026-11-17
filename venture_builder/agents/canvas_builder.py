"""Canvas Builder (Gemma 4 31B, generator): drafts the Lean Canvas; every box cites evidence IDs or is
marked as an assumption."""

from ..schemas import LeanCanvas


def run(ctx, payload: dict) -> LeanCanvas:
    def check(c: LeanCanvas) -> None:
        ctx.ledger.require_known([i for box in c.model_dump().values() for i in box["evidence_ids"]])

    return ctx.router.call("canvas_builder", payload, LeanCanvas, action="design_business", validate=check)
