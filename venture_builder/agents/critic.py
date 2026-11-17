"""Critic (GPT-OSS-20B, high): checks each memo claim against the metrics on a fixed rubric."""

from ..schemas import CriticReview


def run(ctx, memo: dict, metrics: dict) -> CriticReview:
    return ctx.router.call("critic", {"memo": memo, "metrics": metrics}, CriticReview, action="review_memo")
