"""Product Manager (GPT-OSS-20B, medium): writes the spec as machine-checkable acceptance criteria."""

from ..schemas import Spec


def run(ctx, payload: dict) -> Spec:
    return ctx.router.call("product_manager", payload, Spec, action="write_spec")
