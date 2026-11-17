"""Critic (GPT-OSS-20B, high): checks each memo claim against the metrics on a fixed rubric."""

from ..schemas import CriticReview


def run(ctx, memo: dict, metrics: dict, targets: dict | None = None, data_label: str = "") -> CriticReview:
    return ctx.router.call("critic", {"memo": memo, "metrics": metrics, "targets": targets or {},
                                      "data_label": data_label}, CriticReview, action="review_memo")
