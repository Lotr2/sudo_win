"""Product Reviewer (Gemma 4 31B; checks the Builder's work, so it runs on the other model when both exist):
scores the built product against a fixed rubric and lists specific, fixable issues."""

from ..schemas import ProductReview


def run(ctx, form: str, spec: dict, content: dict, promise: dict | None) -> ProductReview:
    return ctx.router.call("product_reviewer", {"product_form": form, "spec": spec, "content": content,
                                                "landing_promise": promise or {}}, ProductReview,
                           action="review_product")
