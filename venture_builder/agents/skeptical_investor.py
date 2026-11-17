"""Skeptical Investor (GPT-OSS-20B, high; critic): scores the canvas on a fixed rubric and ranks the
riskiest assumptions. The gate reads the scores, never an overall opinion."""

from ..schemas import InvestorReview

BOXES = ["problem", "customer_segments", "unique_value", "solution", "channels", "revenue", "costs",
         "key_metrics", "unfair_advantage"]


def run(ctx, canvas: dict, evidence: list[dict]) -> InvestorReview:
    def check(r: InvestorReview) -> None:
        missing = set(BOXES) - {s.box for s in r.scores}
        if missing:
            raise ValueError(f"score every box; missing {sorted(missing)}")

    return ctx.router.call("skeptical_investor", {"canvas": canvas, "evidence": evidence, "boxes": BOXES},
                           InvestorReview, action="review_canvas", validate=check)
