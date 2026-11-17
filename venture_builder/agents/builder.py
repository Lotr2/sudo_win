"""Builder (GPT-OSS-20B, medium): fills the product template until the acceptance tests pass."""

from ..schemas import GuideContent, PackContent, WebToolContent

SCHEMAS = {"guide": GuideContent, "template_pack": PackContent, "web_tool": WebToolContent}


def run(ctx, form: str, spec: dict, failures: list[str], previous: dict | None) -> object:
    return ctx.router.call("builder", {"product_form": form, "spec": spec, "failing_tests": failures,
                                       "previous_attempt": previous}, SCHEMAS[form], action="build_product")
